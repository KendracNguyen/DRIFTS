"""Driver display on the PiSugar Whisplay HAT (Pi Zero 2 W, first model).

The Whisplay HAT (Hardware/whisplay-hat.md) is the driver-facing part of
DRIFTS: a 1.69" 240 x 280 colour LCD, an RGB LED, a speaker and a button.
What it shows comes from the ESP32's 1 Hz BLE status (alert score, level,
driving score, OBD state). The ESP32 keeps the buzzer and motors, so the
alert still works if the Pi is down.

    python -m drifts.whisplay_ui --demo          # on the Pi: cycle OK -> Level 1 -> Level 2 on the HAT
    python -m drifts.whisplay_ui --demo --sim    # anywhere: writes the screens as PNGs instead
    python -m drifts.whisplay_ui --gallery       # every screen as a PNG + one contact sheet

In the car it runs inside `python -m drifts` (drifts/app.py) when
[whisplay] enabled = true in config.toml: ESP32 data only. With the camera
pipeline, `python -m drifts.live --face mesh --esp uart --whisplay` adds the
eye, blink and head-pose readings.

The screen is a status display: the driver does nothing on it. Everything
the driver chooses happens in the phone app; the LCD follows.

What it does:
  screen  welcome while starting, then the alertness score on its own (a ring
          gauge, green / yellow / red by the ESP32's level, drifts/ui_model.py).
          It changes by itself:
            calibration  while a calibration started in the phone app runs
                         (quick road pose, the 9-pose profile or the accuracy
                         test), then "Calibration complete" for 3 s
            I am awake   when an alert has lasted awake_after_s: asks the driver
                         to say "I am awake" (Level 3 check-in), awake_window_s
  LED     green = linked, yellow = Intervention 1, red 1 s/1 s = Intervention 2,
          red slow blink = connection lost, blue = starting
  voice   the clips in Voice/clips (Voice/phrases.json) on level changes,
          lost links, a finished calibration and the awake check, played on
          the HAT's speaker
  button  for developers on the bench (config.toml [whisplay]): repeat_voice,
          calibrate, brightness, awake_check (shows the awake screen) or none.
          During an alert or the awake check any press confirms "I'm awake"
          (the backup until the voice reply is built). A press never silences
          the ESP32's buzzer or motors.

Pure parts (render, led_color, VoiceDirector, ButtonDecoder) are tested in
tests/test_whisplay_ui.py without the HAT.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from drifts.config import WhisplayConfig
from drifts.ui_model import (BANDS, CAUTION, HIGH, RGB, UNAVAILABLE, UiState, band_color,  # noqa: F401
                             state_from_live, state_from_status)  # noqa: F401  (re-exported)

log = logging.getLogger(__name__)

W, H = 240, 280
REPO = Path(__file__).resolve().parents[2]
PHRASES = REPO / "Voice" / "phrases.json"
CLIPS = REPO / "Voice" / "clips"

# Colours: the shared design tokens (drifts/ui_model.py).
BG = RGB["lcd_bg"]
SURFACE = RGB["lcd_surface"]
LINE = RGB["lcd_line"]
TRACK = RGB["track"]
MAROON = RGB["burgundy"]
MAROON_DEEP = RGB["burgundy_deep"]
ACCENT = RGB["burgundy_accent"]
BUTTON = RGB["button"]
INK = RGB["lcd_text"]
MUTED = RGB["lcd_muted"]
GREEN, YELLOW, RED, GREY = RGB["green"], RGB["yellow"], RGB["red"], RGB["grey"]
DARK_INK = (11, 11, 13)

BOOT_S = 4.0
S = 2                       # drawn at 2x, then scaled down: smooth arcs and text

# Every screen. The UI picks one by itself (WhisplayUI.view); there are no pages to flip.
VIEWS = ("welcome", "calibration", "score", "awake")
DONE_S = 3.0                # "Calibration complete" stays this long


# ---------------------------------------------------------------- LED
def led_color(st: UiState, now: float, booted: bool = True) -> tuple:
    if not booted:
        return (0, 0, 120)                                   # blue while starting
    if st.tier >= 2:
        return (255, 0, 0) if (now % 2.0) < 1.0 else (0, 0, 0)   # with the buzzer's 1 s / 1 s
    if not st.esp_linked or not st.obd_ok:
        return (255, 0, 0) if (now % 1.0) < 0.5 else (0, 0, 0)   # connection lost: slow red blink
    if st.tier == 1:
        return (255, 170, 0)
    return (0, 200, 40)


# ---------------------------------------------------------------- fonts
FONT_DIRS = ("/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/inter",
             str(Path.home() / ".local/share/fonts"), "/usr/share/fonts/truetype/dejavu")
WEIGHTS = {700: ("Inter-Bold.otf", "Inter-Bold.ttf", "DejaVuSans-Bold.ttf"),
           600: ("Inter-SemiBold.otf", "Inter-SemiBold.ttf", "DejaVuSans-Bold.ttf"),
           500: ("Inter-Medium.otf", "Inter-Medium.ttf", "DejaVuSans.ttf"),
           400: ("Inter-Regular.otf", "Inter-Regular.ttf", "DejaVuSans.ttf")}
_font_cache: dict = {}


def font(size: int, weight: int = 400):
    """Inter (fonts-inter) at size px, falling back to DejaVu Sans."""
    from PIL import ImageFont
    key = (size, weight)
    if key not in _font_cache:
        f = None
        for name in WEIGHTS[weight]:
            for d in FONT_DIRS:
                try:
                    f = ImageFont.truetype(str(Path(d) / name), size * S)
                    break
                except OSError:
                    continue
            if f:
                break
        _font_cache[key] = f or ImageFont.load_default()
    return _font_cache[key]


def font_name() -> str:
    f = font(14)
    return getattr(f, "path", "built-in")


# ---------------------------------------------------------------- drawing helpers (logical 240 x 280 px)
class Canvas:
    def __init__(self):
        from PIL import Image, ImageDraw
        self.img = Image.new("RGB", (W * S, H * S), BG)
        self.d = ImageDraw.Draw(self.img)

    def _p(self, *v):
        return [round(x * S) for x in v]

    def text(self, x, y, s, size=14, weight=400, fill=INK, anchor="la"):
        self.d.text(self._p(x, y), s, font=font(size, weight), fill=fill, anchor=anchor)

    def width(self, s, size=14, weight=400) -> float:
        return self.d.textlength(s, font=font(size, weight)) / S

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width=1):
        self.d.rectangle(self._p(x0, y0, x1, y1), fill=fill, outline=outline, width=round(width * S))

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._p(x0, y0, x1, y1), radius=r * S, fill=fill, outline=outline,
                                 width=round(width * S))

    def line(self, pts, fill=INK, width=1.6, joint="curve"):
        self.d.line([round(v * S) for p in pts for v in p], fill=fill, width=max(1, round(width * S)), joint=joint)

    def circle(self, x, y, r, fill=None, outline=None, width=1.6):
        self.d.ellipse(self._p(x - r, y - r, x + r, y + r), fill=fill, outline=outline,
                       width=max(1, round(width * S)))

    def arc(self, x, y, r, start, end, fill, width=1.6):
        self.d.arc(self._p(x - r, y - r, x + r, y + r), start, end, fill=fill, width=max(1, round(width * S)))

    def poly(self, pts, fill=None, outline=None, width=1.6):
        self.d.polygon([round(v * S) for p in pts for v in p], fill=fill, outline=outline,
                       width=max(1, round(width * S)) if outline else 0)

    def done(self):
        from PIL import Image
        return self.img.resize((W, H), Image.LANCZOS)


def mix(a, b, t):
    """Colour a blended toward b by t (0-1)."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


# ---------------------------------------------------------------- icons: simple line icons, size ~ 16 px
def icon(c: Canvas, name: str, x: float, y: float, col=INK, s: float = 16, bg=DARK_INK) -> None:
    """Draws icon `name` centred on (x, y), about s px across (bg: the colour behind it)."""
    h = s / 2
    w = max(1.4, s / 10)
    if name == "check":
        c.circle(x, y, h, fill=col)
        c.line([(x - h * .45, y), (x - h * .1, y + h * .38), (x + h * .5, y - h * .35)], fill=bg, width=w * 1.2)
    elif name == "warning":
        c.poly([(x, y - h), (x + h * 1.1, y + h * .85), (x - h * 1.1, y + h * .85)], outline=col, width=w)
        c.line([(x, y - h * .3), (x, y + h * .25)], fill=col, width=w)
        c.circle(x, y + h * .55, w * .6, fill=col)
    elif name == "alert":
        c.circle(x, y, h, outline=col, width=w)
        c.line([(x, y - h * .5), (x, y + h * .1)], fill=col, width=w)
        c.circle(x, y + h * .45, w * .6, fill=col)
    elif name == "dash":
        c.circle(x, y, h, outline=col, width=w)
        c.line([(x - h * .45, y), (x + h * .45, y)], fill=col, width=w)
    elif name == "eye":
        c.arc(x, y + h * .9, h * 1.35, 222, 318, col, w)
        c.arc(x, y - h * .9, h * 1.35, 42, 138, col, w)
        c.circle(x, y, h * .38, fill=col)
    elif name in ("person", "user"):
        c.circle(x, y - h * .45, h * .42, outline=col, width=w)
        c.arc(x, y + h * 1.0, h * .85, 200, 340, col, w)
    elif name == "gauge":
        c.arc(x, y + h * .3, h, 180, 360, col, w)
        c.line([(x, y + h * .3), (x + h * .5, y - h * .3)], fill=col, width=w)
    elif name == "car":
        c.rrect(x - h, y - h * .1, x + h, y + h * .55, h * .25, outline=col, width=w)
        c.line([(x - h * .7, y - h * .1), (x - h * .45, y - h * .65), (x + h * .45, y - h * .65), (x + h * .7, y - h * .1)],
               fill=col, width=w)
        c.circle(x - h * .55, y + h * .7, h * .2, fill=col)
        c.circle(x + h * .55, y + h * .7, h * .2, fill=col)
    elif name == "speaker":
        c.poly([(x - h, y - h * .3), (x - h * .5, y - h * .3), (x, y - h * .8), (x, y + h * .8),
                (x - h * .5, y + h * .3), (x - h, y + h * .3)], outline=col, width=w)
        c.arc(x + h * .1, y, h * .45, -50, 50, col, w)
        c.arc(x + h * .1, y, h * .85, -50, 50, col, w)
    elif name == "vibe":
        c.rrect(x - h * .4, y - h * .8, x + h * .4, y + h * .8, h * .2, outline=col, width=w)
        for sx in (-1, 1):
            c.line([(x + sx * h * .7, y - h * .45), (x + sx * h * .9, y - h * .15), (x + sx * h * .7, y + h * .15),
                    (x + sx * h * .9, y + h * .45)], fill=col, width=w * .8)
    elif name == "link":                     # radio waves: the ESP32 Bluetooth link
        c.circle(x, y + h * .55, w * .8, fill=col)
        c.arc(x, y + h * .55, h * .55, 225, 315, col, w)
        c.arc(x, y + h * .55, h * 1.05, 225, 315, col, w)
    elif name == "chip":
        c.rrect(x - h * .6, y - h * .6, x + h * .6, y + h * .6, h * .15, outline=col, width=w)
        for k in (-.3, .3):
            for a, b in (((x + k * h, y - h * .6), (x + k * h, y - h)), ((x + k * h, y + h * .6), (x + k * h, y + h)),
                         ((x - h * .6, y + k * h), (x - h, y + k * h)), ((x + h * .6, y + k * h), (x + h, y + k * h))):
                c.line([a, b], fill=col, width=w * .8)
    elif name == "camera":
        c.rrect(x - h, y - h * .45, x + h, y + h * .7, h * .2, outline=col, width=w)
        c.line([(x - h * .35, y - h * .45), (x - h * .2, y - h * .75), (x + h * .2, y - h * .75), (x + h * .35, y - h * .45)],
               fill=col, width=w)
        c.circle(x, y + h * .1, h * .35, outline=col, width=w)
    elif name == "pi":
        for dx, dy in ((-.35, -.1), (.35, -.1), (0, .25), (-.35, .55), (.35, .55), (0, -.45)):
            c.circle(x + dx * h, y + dy * h, h * .27, outline=col, width=w * .8)
        c.line([(x - h * .3, y - h * .9), (x, y - h * .7), (x + h * .3, y - h * .9)], fill=col, width=w * .8)
    elif name == "wifi":
        c.circle(x, y + h * .6, w * .8, fill=col)
        for r in (.5, .9, 1.3):
            c.arc(x, y + h * .6, h * r, 225, 315, col, w)
    elif name == "sliders":
        for k, p in ((-.5, -.3), (0, .4), (.5, -.1)):
            c.line([(x - h, y + k * h), (x + h, y + k * h)], fill=col, width=w * .8)
            c.circle(x + p * h, y + k * h, h * .22, fill=BG, outline=col, width=w * .8)
    elif name == "target":
        c.circle(x, y, h * .65, outline=col, width=w)
        for a, b in (((x, y - h), (x, y - h * .35)), ((x, y + h), (x, y + h * .35)),
                     ((x - h, y), (x - h * .35, y)), ((x + h, y), (x + h * .35, y))):
            c.line([a, b], fill=col, width=w)
    elif name == "mic":
        c.rrect(x - h * .3, y - h, x + h * .3, y + h * .25, h * .3, outline=col, width=w)
        c.arc(x, y - h * .05, h * .6, 20, 160, col, w)
        c.line([(x, y + h * .55), (x, y + h * .95)], fill=col, width=w)
    elif name == "sparkle":
        pts = []
        for i in range(8):
            a = math.pi / 4 * i - math.pi / 2
            r = h if i % 2 == 0 else h * .28
            pts.append((x + r * math.cos(a), y + r * math.sin(a)))
        c.poly(pts, outline=col, width=w)
        c.line([(x + h * .85, y - h * .95), (x + h * .85, y - h * .55)], fill=col, width=w * .8)
        c.line([(x + h * .65, y - h * .75), (x + h * 1.05, y - h * .75)], fill=col, width=w * .8)
    elif name == "pin":
        c.circle(x, y - h * .3, h * .62, outline=col, width=w)
        c.line([(x - h * .55, y), (x, y + h), (x + h * .55, y)], fill=col, width=w)
        c.circle(x, y - h * .3, h * .22, fill=col)
    elif name == "phone":
        c.line([(x - h * .7, y - h * .8), (x - h * .35, y - h * .85), (x - h * .15, y - h * .4), (x - h * .4, y - h * .15),
                (x + h * .15, y + h * .4), (x + h * .4, y + h * .15), (x + h * .85, y + h * .35), (x + h * .8, y + h * .7),
                (x + h * .4, y + h * .9), (x - h * .5, y + h * .1), (x - h * .85, y - h * .4), (x - h * .7, y - h * .8)],
               fill=col, width=w)
    elif name == "chat":
        c.rrect(x - h, y - h * .75, x + h, y + h * .45, h * .3, outline=col, width=w)
        c.line([(x - h * .4, y + h * .45), (x - h * .55, y + h * .95), (x, y + h * .45)], fill=col, width=w)
        for dx in (-.45, 0, .45):
            c.circle(x + dx * h, y - h * .15, w * .7, fill=col)
    elif name == "chevron":
        c.line([(x - h * .25, y - h * .5), (x + h * .25, y), (x - h * .25, y + h * .5)], fill=col, width=w)
    elif name == "clock":
        c.circle(x, y, h * .85, outline=col, width=w)
        c.line([(x, y - h * .5), (x, y), (x + h * .4, y + h * .25)], fill=col, width=w)


# ---------------------------------------------------------------- shared pieces
def _fmt(v, fmt="{:.0f}", unit=""):
    return "--" if v is None else fmt.format(v) + unit


def header(c: Canvas, st: UiState) -> None:
    from PIL import Image
    grad = Image.new("RGB", (2, 1))
    grad.putpixel((0, 0), MAROON_DEEP)
    grad.putpixel((1, 0), MAROON)
    c.img.paste(grad.resize((W * S, 30 * S), Image.BILINEAR), (0, 0))
    c.text(12, 15, "DRIFTS", 18, 700, INK, "lm")
    if st.demo:
        x = 14 + c.width("DRIFTS", 18, 700) + 6
        c.rrect(x, 8, x + 36, 22, 4, fill=(255, 255, 255))
        c.text(x + 18, 15, "DEMO", 9, 700, MAROON, "mm")
    for name, ok, x in (("link", st.esp_linked, 196), ("car", st.obd_ok, 222)):
        icon(c, name, x, 15, INK if ok else (120, 90, 96), 15)
        if not ok:
            c.line([(x - 8, 22), (x + 8, 8)], fill=RED, width=1.8)


def title(c: Canvas, text: str, y: float = 47, ico: Optional[str] = None) -> None:
    if ico:
        tw = c.width(text, 17, 600)
        x0 = 120 - (tw + 24) / 2
        icon(c, ico, x0 + 8, y, INK, 16)
        c.text(x0 + 24, y, text, 17, 600, INK, "lm")
    else:
        c.text(120, y, text, 17, 600, INK, "mm")


def rows(c: Canvas, items, y0: float = 66, h: float = 38, value_size: int = 15) -> None:
    """Icon, label, right-aligned value, thin dividers (the metric lists)."""
    for i, (ico, label, value, vcol) in enumerate(items):
        y = y0 + i * h + h / 2
        if i:
            c.line([(14, y - h / 2), (226, y - h / 2)], fill=LINE, width=1)
        icon(c, ico, 26, y, INK, 16)
        c.text(44, y, label, 14, 500, INK, "lm")
        c.text(226, y, value, value_size, 700, vcol or INK, "rm")


def gauge(c: Canvas, x: float, y: float, r: float, thick: float, score: Optional[float], col) -> None:
    """Grey track, coloured arc from 12 o'clock clockwise, rounded ends."""
    c.arc(x, y, r, 0, 360, TRACK, thick)
    if score is None or score <= 0:
        return
    frac = max(0.0, min(1.0, score / 100.0))
    start, end = -90.0, -90.0 + 360.0 * frac
    if frac < 1.0:
        c.arc(x, y, r, start, end, col, thick)
        rc = r - thick / 2
        for a in (start, end):
            c.circle(x + rc * math.cos(math.radians(a)), y + rc * math.sin(math.radians(a)), thick / 2, fill=col)
    else:
        c.arc(x, y, r, 0, 360, col, thick)


def pill(c: Canvas, x0, y0, x1, y1, text, fill, ink=INK, ico: Optional[str] = None, size=15, weight=700,
         r: Optional[float] = None):
    c.rrect(x0, y0, x1, y1, r if r is not None else ((y1 - y0) / 2 if y1 - y0 < 30 else 12), fill=fill)
    cy = (y0 + y1) / 2
    tw = c.width(text, size, weight)
    if ico:
        x = (x0 + x1) / 2 - (tw + 22) / 2
        icon(c, ico, x + 8, cy, ink, 16, bg=fill)
        c.text(x + 22, cy, text, size, weight, ink, "lm")
    else:
        c.text((x0 + x1) / 2, cy, text, size, weight, ink, "mm")


def badge(c: Canvas, x1: float, y: float, state: Optional[bool]) -> None:
    text, col = {True: ("Online", GREEN), False: ("Offline", RED), None: ("Unknown", GREY)}[state]
    w = c.width(text, 11, 600) + 24
    c.rrect(x1 - w, y - 10, x1, y + 10, 10, fill=mix(BG, col, 0.22))
    c.circle(x1 - w + 9, y, 3, fill=col)
    c.text(x1 - 8, y, text, 11, 600, col, "rm")


def footer(c: Canvas, text: str, col=MUTED, y: float = 262) -> None:
    c.text(120, y, text, 11, 400, col, "mm")


def wrap(c: Canvas, text: str, width: float, size: int, weight: int = 400) -> list:
    out, line = [], ""
    for word in text.split():
        test = (line + " " + word).strip()
        if c.width(test, size, weight) <= width or not line:
            line = test
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    return out


# ---------------------------------------------------------------- views
def view_welcome(c: Canvas, st: UiState, ctx: dict) -> None:
    icon(c, "car", 120, 90, INK, 54)
    c.text(120, 140, "DRIFTS", 34, 700, INK, "mm")
    c.text(120, 172, "Driver Fatigue", 14, 400, MUTED, "mm")
    c.text(120, 190, "Detection System", 14, 400, MUTED, "mm")
    pill(c, 40, 222, 200, 254, "Starting...", BUTTON, INK, size=15, weight=600)


def _bar(c: Canvas, p: Optional[float], y: float = 232, label: Optional[str] = None) -> None:
    p = max(0.0, min(1.0, p or 0.0))
    c.rrect(18, y, 182, y + 14, 7, fill=(232, 232, 236))
    if p > 0:
        c.rrect(18, y, 18 + max(14, 164 * p), y + 14, 7, fill=ACCENT)
    c.text(224, y + 7, label if label is not None else f"{p * 100:.0f}%", 14, 700, INK, "rm")


def _check(c: Canvas, y: float, col=GREEN, r: float = 34) -> None:
    c.circle(120, y, r, fill=col)
    c.line([(120 - r * 0.42, y + r * 0.02), (120 - r * 0.1, y + r * 0.32), (120 + r * 0.44, y - r * 0.3)],
           fill=DARK_INK, width=r * 0.17, joint=None)


def view_calibration(c: Canvas, st: UiState, ctx: dict) -> None:
    """A calibration the driver started in the phone app."""
    mode = st.calibrating
    if ctx.get("calib_done"):
        _check(c, 108)
        c.text(120, 168, "Calibration complete", 18, 700, INK, "mm")
        c.text(120, 192, {"profile": "Your profile is saved", "test": "Results are on the phone"}
               .get(ctx["calib_done"], "Road pose saved"), 13, 400, MUTED, "mm")
        return
    if mode in ("profile", "test"):
        title(c, "Create Profile" if mode == "profile" else "Accuracy Test")
        if st.calib_step and st.calib_total:
            c.text(120, 70, f"Step {st.calib_step} of {st.calib_total}", 13, 500, MUTED, "mm")
        lines = wrap(c, st.calib_prompt or "Follow the phone", 204, 22, 700)[:3]
        y0 = 136 - (len(lines) - 1) * 14
        for i, ln in enumerate(lines):
            c.text(120, y0 + i * 28, ln, 22, 700, INK, "mm")
        if st.face_found is False:
            c.text(120, 206, "No face: look at the camera", 12, 500, YELLOW, "mm")
        else:
            c.text(120, 206, "Hold it" if mode == "profile" else "Keep doing this", 12, 500, MUTED, "mm")
        _bar(c, st.calib_progress)
        return
    # quick: the road pose (also the start-up pass in the gallery)
    title(c, "Calibration")
    icol = (150, 150, 158)
    c.circle(120, 96, 21, fill=icol)
    c.d.pieslice(c._p(78, 120, 162, 196), 180, 360, fill=icol)
    c.rect(70, 152, 170, 160, fill=BG)
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        cx, cy = 120 + sx * 44, 108 + sy * 40
        c.line([(cx, cy - sy * 12), (cx, cy), (cx - sx * 12, cy)], fill=INK, width=2.4, joint=None)
    c.text(120, 172, "Look at the road", 13, 500, INK, "mm")
    c.text(120, 189, "and hold still.", 13, 500, INK, "mm")
    if st.face_found is False:
        status, col = "No face: look at the camera", YELLOW
    else:
        status, col = "Face detected: hold position", MUTED
    c.text(120, 210, status, 11, 500, col, "mm")
    _bar(c, st.calib_progress)


def _score_rows(st: UiState) -> list:
    band = st.band
    if band == UNAVAILABLE:
        return [("link", "ESP32 linked: no score yet" if st.esp_linked else "Waiting for the ESP32"),
                ("car", "OBD: linked" if st.obd_ok else "OBD: not linked")]
    if band == HIGH:
        return [("vibe", "Intervention active" if st.outputs_on else "Intervention pending"),
                ("speaker", "Audio + vibration")]
    if st.partial:
        first = ("gauge", f"Driving score: {_fmt(st.driving)}")
        return [first, ("eye", "No eye data: driving only")]
    if band == CAUTION:
        if st.vision and st.face_found:
            return [("eye", f"Blinks: {_fmt(st.blink_rate)}/min"), ("person", f"Yaw: {_fmt(st.yaw, '{:+.0f}')}°")]
        return [("gauge", f"Driving score: {_fmt(st.driving)}"), ("car", f"Speed: {_fmt(st.speed, unit=' km/h')}")]
    if st.vision and st.face_found:
        return [("eye", f"Eyes: {(st.eyes or '--').capitalize()}"), ("person", f"Pose: {st.pose_label or '--'}")]
    return [("gauge", f"Driving score: {_fmt(st.driving)}"), ("car", "OBD: linked" if st.obd_ok else "OBD: not linked")]


def view_score(c: Canvas, st: UiState, ctx: dict) -> None:
    band = st.band
    col = band_color(band)
    if st.partial and band != UNAVAILABLE:
        col = mix(BG, col, 0.6)                       # dimmer: driving data only
    gauge(c, 120, 106, 70, 14, st.score, col)
    c.text(120, 98, _fmt(st.score), 52, 700, INK if band != UNAVAILABLE else MUTED, "mm")
    c.text(120, 135, "/ 100", 17, 500, INK if band != UNAVAILABLE else MUTED, "mm")
    b = BANDS[band]
    ink = INK if band in (HIGH, UNAVAILABLE) else DARK_INK
    fill = col if band != UNAVAILABLE else SURFACE
    pw = min(216, c.width(b["title"], 15, 700) + 46)          # the pill fits its words, as in the mockup
    pill(c, 120 - pw / 2, 184, 120 + pw / 2, 212, b["title"], fill, ink if band != UNAVAILABLE else MUTED,
         b["icon"], 15, 700, r=8)
    if ctx.get("banner"):
        return
    for i, (ico, text) in enumerate(_score_rows(st)):
        y = 231 + i * 25
        icon(c, ico, 46, y, INK, 17)
        c.text(64, y, text, 14, 500, INK, "lm")


def view_awake(c: Canvas, st: UiState, ctx: dict, now: float = 0.0) -> None:
    """The Level 3 check-in: the driver answers by voice ("I am awake")."""
    aw = ctx.get("awake") or {}
    if aw.get("confirmed"):
        _check(c, 100)
        c.text(120, 160, "Thank you", 20, 700, INK, "mm")
        c.text(120, 186, "Please take a break soon.", 13, 400, MUTED, "mm")
        return
    pulse = 0.5 + 0.5 * math.sin(now * 4.0)
    c.circle(120, 92, 40 + 6 * pulse, fill=mix(BG, RED, 0.18 + 0.1 * pulse))
    c.circle(120, 92, 32, fill=RED)
    icon(c, "mic", 120, 92, INK, 30, bg=RED)
    c.text(120, 154, "Are you awake?", 15, 500, MUTED, "mm")
    c.text(120, 184, "Say", 15, 500, INK, "mm")
    c.text(120, 210, "“I am awake”", 24, 700, INK, "mm")
    left = aw.get("left")
    if left is not None and aw.get("window"):
        frac = max(0.0, min(1.0, left / aw["window"]))
        c.rrect(30, 236, 210, 242, 3, fill=TRACK)
        if frac > 0:
            c.rrect(30, 236, 30 + max(6, 180 * frac), 242, 3, fill=RED)
    footer(c, "Listening..." if aw.get("listening") else "Or press the side button", y=260)


DRAW = {"welcome": view_welcome, "calibration": view_calibration, "score": view_score}


def render(st: UiState, now: float, page=0, booted: bool = True, banner: Optional[str] = None,
           ctx: Optional[dict] = None):
    """The 240 x 280 frame (a PIL image). page: a view name (VIEWS); a number means "score"."""
    ctx = dict(ctx or {})
    ctx["banner"] = banner
    view = page if isinstance(page, str) else "score"
    if not booted:
        view = "welcome"
    c = Canvas()
    header(c, st)
    if view == "awake":
        view_awake(c, st, ctx, now)
    else:
        DRAW[view](c, st, ctx)
    if banner:
        c.rrect(14, 236, 226, 270, 12, fill=MAROON)
        c.text(120, 253, banner, 14, 600, INK, "mm")
    return c.done()


def rgb565(img) -> bytes:
    """Big-endian RGB565 bytes for WhisplayBoard.draw_image."""
    try:
        import numpy as np
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        v = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
        return v.astype(">u2").tobytes()
    except ImportError:
        out = bytearray()
        for r, g, b in img.convert("RGB").getdata():
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out += bytes(((v >> 8) & 0xFF, v & 0xFF))
        return bytes(out)


# ---------------------------------------------------------------- voice
PRIORITY = {"AWAKE": 5, "LEVEL2": 4, "LEVEL1": 3}


class VoiceDirector:
    """What to say, and when (same rules as Docs/voice.md):
    READY at start; LEVEL1 / LEVEL2 on entering a level (LEVEL2 again every
    repeat_s while it lasts, unless acknowledged); RECOVERED back to OK;
    OBD_LOST / ESP_LOST when a link that was up drops for lost_s."""

    def __init__(self, repeat_s: float = 60.0, lost_s: float = 5.0, ready_after_s: float = 3.0):
        self.repeat_s, self.lost_s, self.ready_after_s = repeat_s, lost_s, ready_after_s
        self.t0: Optional[float] = None
        self.ready = False
        self.tier = 0
        self.last_l2 = 0.0
        self.ack_until = 0.0
        self.pending: Optional[str] = None
        self._links = {"OBD_LOST": [False, None, False], "ESP_LOST": [False, None, False]}  # seen, down_since, said

    def queue(self, name: str) -> None:
        if PRIORITY.get(name, 2) >= PRIORITY.get(self.pending, 0 if self.pending is None else 2):
            self.pending = name

    def acknowledge(self, now: float) -> None:
        """Button press during an alert: no LEVEL2 repeats for repeat_s."""
        self.ack_until = now + self.repeat_s

    def _link(self, key: str, ok: bool, now: float) -> None:
        w = self._links[key]
        if ok:
            w[0], w[1], w[2] = True, None, False
            return
        if not w[0] or w[2]:
            return
        if w[1] is None:
            w[1] = now
        elif now - w[1] >= self.lost_s:
            w[2] = True
            self.queue(key)

    def update(self, st: UiState, now: float, playing: bool) -> Optional[str]:
        if self.t0 is None:
            self.t0 = now
        if not self.ready and now - self.t0 >= self.ready_after_s:
            self.ready = True
            self.queue("READY")
        tier = st.tier if st.score is not None else self.tier
        if tier != self.tier:
            if tier == 2:
                self.queue("LEVEL2")
                self.last_l2 = now
            elif tier == 1 and self.tier == 0:
                self.queue("LEVEL1")
            elif tier == 0:
                if self.pending in ("LEVEL1", "LEVEL2"):
                    self.pending = None
                self.queue("RECOVERED")
                self.ack_until = 0.0
            self.tier = tier
        elif tier == 2 and now - self.last_l2 >= self.repeat_s and now >= self.ack_until:
            self.queue("LEVEL2")
            self.last_l2 = now
        self._link("ESP_LOST", st.esp_linked, now)
        if st.esp_linked:
            self._link("OBD_LOST", st.obd_ok, now)
        if playing or self.pending is None:
            return None
        name, self.pending = self.pending, None
        return name


class Speaker:
    """Plays Voice/clips/NN_NAME.wav with aplay on the HAT's sound card."""

    def __init__(self, device: str = "plughw:CARD=whisplaysound", clips: Path = CLIPS, sim: bool = False):
        self.device, self.sim = device, sim
        names = {p["name"]: p["id"] for p in json.loads(PHRASES.read_text())["phrases"]}
        self.files = {n: clips / f"{i:02d}_{n}.wav" for n, i in names.items()}
        self.ok = sim or bool(shutil.which("aplay"))
        self.last: Optional[str] = None
        self.played: list = []
        self._proc = None
        self._until = 0.0

    def playing(self, now: float) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        return now < self._until

    def play(self, name: str, now: float) -> None:
        f = self.files.get(name)
        self.last = name
        self.played.append((now, name))
        try:
            with wave.open(str(f), "rb") as w:
                length = w.getnframes() / w.getframerate()
        except (OSError, wave.Error, TypeError):
            length = 2.0
        self._until = now + length + 0.2
        if self.sim or not self.ok or f is None or not f.exists():
            log.info("[VOICE] %s%s", name, "" if self.sim else " (not played: aplay or clip missing)")
            return
        try:
            self._proc = subprocess.Popen(["aplay", "-q", "-D", self.device, str(f)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            log.warning("aplay failed: %s", exc)


# ---------------------------------------------------------------- button
class ButtonDecoder:
    """Press / release times -> "short", "long" or "double".

    long:   fires once the button has been held long_s (while still held, so
            the driver feels it trigger); the release then does nothing.
    double: a second press within double_s of a short press. With
            double_s = 0 there is no double press and a short press fires on
            release at once; otherwise it waits double_s first.
    Callbacks come from the driver's thread, so this is locked.
    """

    def __init__(self, long_s: float = 1.5, double_s: float = 0.0):
        import threading
        self.long_s, self.double_s = long_s, double_s
        self._down: Optional[float] = None
        self._long_fired = False
        self._short_at: Optional[float] = None       # a short press waiting for a possible second one
        self._out: list = []
        self._lock = threading.Lock()

    def _flush_short(self) -> None:
        if self._short_at is not None:
            self._short_at = None
            self._out.append("short")

    def press(self, now: float) -> None:
        with self._lock:
            if self._short_at is not None and now - self._short_at > self.double_s:
                self._flush_short()                  # too late to be a double press
            self._down, self._long_fired = now, False

    def release(self, now: float) -> Optional[str]:
        """Returns the event this release completes (also queued for poll)."""
        with self._lock:
            if self._down is None:
                return None
            held, self._down = now - self._down, None
            if self._long_fired:
                return None
            if held >= self.long_s:
                self._flush_short()
                ev = "long"
            elif self._short_at is not None:         # second press inside the window
                self._short_at, ev = None, "double"
            elif self.double_s <= 0:
                ev = "short"
            else:
                self._short_at = now                 # wait: a second press makes it a double
                return None
            self._out.append(ev)
            return ev

    def poll(self, now: float) -> list:
        """Events ready now (a held long press, a short press whose double window ran out)."""
        with self._lock:
            if self._down is not None and not self._long_fired and now - self._down >= self.long_s:
                self._flush_short()
                self._long_fired = True
                self._out.append("long")
            if self._short_at is not None and self._down is None and now - self._short_at > self.double_s:
                self._flush_short()
            out, self._out = self._out, []
            return out


ACTIONS = ("repeat_voice", "calibrate", "brightness", "awake_check", "none")
# Older config.toml files: the page and assistant actions are gone with those screens.
RETIRED = ("next_page", "prev_page", "ai")


def check_action(name: str) -> str:
    if name in ACTIONS:
        return name
    if name in RETIRED or name.startswith("page:"):
        log.warning("[whisplay] button action %r is retired (the LCD has no pages now): using none", name)
        return "none"
    raise ValueError(f"[whisplay] button action {name!r}: use one of {', '.join(ACTIONS)}")


# ---------------------------------------------------------------- hardware
class SimBoard:
    """No HAT: keeps the last frame and LED colour (and can save PNGs)."""

    def __init__(self, out_dir: Optional[Path] = None):
        self.out_dir, self.frame, self.rgb, self.backlight = out_dir, None, (0, 0, 0), 0
        self._press = self._release = self._joy = None
        self.closed = False

    def show(self, img) -> None:
        self.frame = img

    def set_rgb(self, r, g, b) -> None:
        self.rgb = (r, g, b)

    def set_backlight(self, pct: int) -> None:
        self.backlight = pct

    def on_button(self, press: Callable, release: Callable) -> None:
        self._press, self._release = press, release

    def on_joystick(self, press: Callable) -> None:
        self._joy = press

    def close(self) -> None:
        self.closed = True


def hat_conflicts() -> list:
    """Other programs that take the HAT's screen and button for themselves."""
    found = []
    if Path("/tmp/whisplay-daemon.sock").exists():
        found.append("whisplay-daemon (PiSugar's app launcher)")
    if shutil.which("systemctl"):
        for svc, what in (("whisplay-daemon.service", "whisplay-daemon (PiSugar's app launcher)"),
                          ("chatbot.service", "whisplay-ai-chatbot")):
            try:
                r = subprocess.run(["systemctl", "is-active", "--quiet", svc], timeout=3,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and what not in found:
                found.append(what)
    return found


class HatBoard:
    """PiSugar's WhisplayBoard (github.com/PiSugar/Whisplay, runtime/whisplay.py)."""

    def __init__(self, driver_dir: str, rotate: int = 0, joystick: bool = False):
        others = hat_conflicts()
        if others:
            raise RuntimeError("the Whisplay HAT is in use by " + " and ".join(others) +
                               ". Stop it first: sudo systemctl disable --now whisplay-daemon chatbot")
        sys.path.insert(0, str(Path(driver_dir).expanduser()))
        from whisplay import WhisplayBoard            # noqa: E402  (PiSugar's driver)
        self.b = WhisplayBoard(enable_joystick=joystick) if joystick else WhisplayBoard()
        self.rotate = rotate % 360

    def show(self, img) -> None:
        if self.rotate:
            img = img.rotate(self.rotate)
        self.b.draw_image(0, 0, W, H, rgb565(img))

    def set_rgb(self, r, g, b) -> None:
        self.b.set_rgb(r, g, b)

    def set_backlight(self, pct: int) -> None:
        self.b.set_backlight(pct)

    def on_button(self, press: Callable, release: Callable) -> None:
        self.b.on_button_press(press)
        self.b.on_button_release(release)

    def on_joystick(self, press: Callable) -> None:
        if hasattr(self.b, "on_joystick_press"):
            self.b.on_joystick_press(press)

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self.b.fill_screen(0)
            self.b.set_rgb(0, 0, 0)
            self.b.set_backlight(0)
        finally:
            self.b.cleanup()


# ---------------------------------------------------------------- the UI
def wifi_ssid() -> Optional[str]:
    """The Wi-Fi network the Pi is on (None off the Pi or when not connected)."""
    if not shutil.which("iwgetid"):
        return None
    try:
        out = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


BRIGHTNESS_STEPS = (30, 70, 100)


class WhisplayUI:
    """The LCD, LED, speaker and button. The screen picks itself (view()): the
    driver sets things up in the phone app, not here."""

    def __init__(self, cfg: WhisplayConfig, board=None, sim: bool = False, hooks: Optional[dict] = None):
        self.cfg = cfg
        self.sim = sim
        self.actions = {"short": check_action(cfg.button_short), "long": check_action(cfg.button_long),
                        "double": check_action(cfg.button_double)}
        self.board = board or (SimBoard() if sim else HatBoard(cfg.driver_dir, cfg.rotate, cfg.joystick))
        self.speaker = Speaker(cfg.audio_device, sim=sim)
        self.voice = VoiceDirector(repeat_s=cfg.repeat_s)
        self.button = ButtonDecoder(cfg.long_s, 0.0 if self.actions["double"] == "none" else cfg.double_s)
        # e.g. {"calibrate": pipeline.recalibrate}; later {"listen": <speech recogniser start>}
        self.hooks = dict(hooks or {})
        self.brightness = cfg.brightness
        self.t0: Optional[float] = None
        self.banner: Optional[str] = None
        self.banner_until = 0.0
        self.presses: list = []                 # (time, event, what it did): for the self-test and logs
        self._joy: list = []
        self._key = None
        self._frame = None
        # calibration: what ran last tick, and the "complete" screen
        self._calib: Optional[str] = None
        self._calib_done: Optional[str] = None
        self._calib_done_until = 0.0
        # the awake check (Level 3)
        self._alert_since: Optional[float] = None
        self._next_awake = 0.0                  # earliest time for the next automatic check
        self.awake_until = 0.0                  # asking until
        self.awake_ok_until = 0.0               # "Thank you" until
        self.board.set_backlight(self.brightness)
        self.board.on_button(lambda: self.button.press(time.monotonic()),
                             lambda: self.button.release(time.monotonic()))
        if hasattr(self.board, "on_joystick"):
            self.board.on_joystick(lambda d: self._joy.append(d))

    def _flash(self, text: str, now: float, s: float = 2.0) -> None:
        self.banner, self.banner_until = text, now + s

    # ------------------------------------------------------------ awake check
    def ask_awake(self, now: float) -> None:
        """Show "Say 'I am awake'" and say it. Called when an alert has lasted
        awake_after_s, or by the dev button (awake_check)."""
        self.awake_until = now + self.cfg.awake_window_s
        self.awake_ok_until = 0.0
        self.voice.queue("AWAKE")
        fn = self.hooks.get("listen")
        if fn is not None:
            fn()                                # a speech recogniser, when there is one
        log.info("[UI] Awake check: asking the driver")

    def confirm_awake(self, now: float, how: str = "voice") -> None:
        """The driver answered (by voice, or the button as a backup)."""
        self.awake_until = 0.0
        self.awake_ok_until = now + 2.5
        self.voice.acknowledge(now)
        log.info("[UI] Awake check: confirmed (%s)", how)

    def awake_active(self, now: float) -> bool:
        return now < self.awake_until

    def _awake_rules(self, st: UiState, now: float) -> None:
        if st.tier < 2:
            self._alert_since, self.awake_until = None, 0.0
            return
        if self._alert_since is None:
            self._alert_since = now
            self._next_awake = now + self.cfg.awake_after_s
        if (self.cfg.awake_after_s > 0 and not self.awake_active(now) and now >= self._next_awake
                and now >= self.awake_ok_until):
            self.ask_awake(now)
            self._next_awake = now + self.cfg.awake_window_s + self.cfg.awake_after_s

    # ------------------------------------------------------------ button
    def do(self, action: str, st: UiState, now: float) -> str:
        """Runs one (developer) button action. Returns what happened (for the log)."""
        if action == "repeat_voice":
            if self.speaker.last:
                self.speaker.play(self.speaker.last, now)
                self._flash("Repeating message", now)
                return f"repeat {self.speaker.last}"
            self._flash("Nothing to repeat", now)
            return "nothing to repeat"
        if action == "calibrate":
            fn = self.hooks.get("calibrate")
            if fn is None:
                self._flash("Calibrate: needs the camera", now)
                return "calibrate: no camera"
            fn()
            return "calibrate"
        if action == "awake_check":
            self.ask_awake(now)
            return "awake check"
        if action == "brightness":
            i = BRIGHTNESS_STEPS.index(self.brightness) if self.brightness in BRIGHTNESS_STEPS else -1
            self.brightness = BRIGHTNESS_STEPS[(i + 1) % len(BRIGHTNESS_STEPS)]
            self.board.set_backlight(self.brightness)
            self._flash(f"Brightness {self.brightness}%", now)
            return f"brightness {self.brightness}"
        return "none"

    def handle(self, event: Optional[str], st: UiState, now: float) -> None:
        if event is None:
            return
        if self.awake_active(now) and event in ("short", "double", "long"):
            self.confirm_awake(now, "button")   # backup for the voice reply
            did = "awake confirmed"
        elif st.tier >= 1 and event in ("short", "double"):
            # Safety: during an alert a press acknowledges ("I'm awake"). It pauses
            # the voice repeat only; the ESP32's buzzer and motors keep going.
            self.voice.acknowledge(now)
            log.info("[UI] Driver acknowledged the alert")
            self._flash("Acknowledged", now)
            did = "acknowledge"
        else:
            did = self.do(self.actions.get(event, "none"), st, now)
        self.presses.append((now, event, did))
        del self.presses[:-50]
        log.info("[UI] button %s -> %s", event, did)

    def _joystick(self, direction: str, st: UiState, now: float) -> None:
        if direction == "center":
            self.handle("short", st, now)

    # ------------------------------------------------------------ screen
    def _calib_rules(self, st: UiState, now: float) -> None:
        if self._calib and not st.calibrating:           # just finished (or was cancelled)
            if self._calib != "test" and st.calibrated is not False:
                self.voice.queue("CALIBRATED")
            self._calib_done, self._calib_done_until = self._calib, now + DONE_S
        self._calib = st.calibrating

    def context(self, now: float) -> dict:
        aw = None
        if self.awake_active(now):
            aw = {"left": self.awake_until - now, "window": self.cfg.awake_window_s,
                  "listening": "listen" in self.hooks}
        elif now < self.awake_ok_until:
            aw = {"confirmed": True}
        return {"awake": aw, "calib_done": self._calib_done if now < self._calib_done_until else None}

    def view(self, st: UiState, booted: bool, now: Optional[float] = None):
        """welcome while starting; the awake check; a calibration from the app; else the score."""
        now = time.monotonic() if now is None else now
        if not booted:
            return "welcome"
        if self.awake_active(now) or now < self.awake_ok_until:
            return "awake"
        if st.tier >= 1:
            return "score"                          # an alert always shows the score
        if st.calibrating or now < self._calib_done_until:
            return "calibration"
        return "score"

    def tick(self, st: UiState, now: float):
        """One update: button events, voice, LED, screen. Returns the frame."""
        if self.t0 is None:
            self.t0 = now
        booted = now - self.t0 >= BOOT_S
        for ev in self.button.poll(now):
            self.handle(ev, st, now)
        while self._joy:
            self._joystick(self._joy.pop(0), st, now)
        self._calib_rules(st, now)
        if booted:
            self._awake_rules(st, now)
        if self.cfg.voice and booted:
            name = self.voice.update(st, now, self.speaker.playing(now))
            if name:
                self.speaker.play(name, now)
        self.board.set_rgb(*led_color(st, now, booted))
        banner = self.banner if now < self.banner_until else None
        view = self.view(st, booted, now)
        ctx = self.context(now)
        anim = int(now * 8) if view == "awake" else 0
        key = (view, banner, anim, repr({k: v for k, v in st.__dict__.items() if k != "t"}), repr(ctx))
        if view == "awake":
            key = (view, banner, anim, round(ctx["awake"].get("left", 0) if ctx["awake"] else 0, 1))
        if key != self._key:                        # draw only when something on screen changed
            self._key, self._frame = key, render(st, now, view, True, banner, ctx)
            self.board.show(self._frame)
        return self._frame

    def close(self) -> None:
        """Screen and LED off, driver released (on shutdown)."""
        try:
            if hasattr(self.board, "close"):
                self.board.close()
        except Exception as exc:
            log.warning("[UI] close failed: %s", exc)

    async def run(self, get_state: Callable[[], UiState], period_s: float = 0.1) -> None:
        import asyncio
        log.info("[UI] Whisplay display running (font: %s; button %s)", font_name(), self.actions)
        try:
            while True:
                try:
                    self.tick(get_state(), time.monotonic())
                except Exception as exc:        # the display must never stop the app
                    log.warning("[UI] update failed: %s", exc)
                await asyncio.sleep(period_s)
        finally:
            self.close()

    def run_thread(self, get_state: Callable[[], UiState], stop, period_s: float = 0.1) -> None:
        """The same loop for a threaded program (drifts.live --whisplay)."""
        log.info("[UI] Whisplay display running (font: %s; button %s)", font_name(), self.actions)
        while not stop.is_set():
            try:
                self.tick(get_state(), time.monotonic())
            except Exception as exc:
                log.warning("[UI] update failed: %s", exc)
            stop.wait(period_s)
        self.close()


# ---------------------------------------------------------------- demo and gallery
def demo_state(score: Optional[float], **kw) -> UiState:
    """Example data for the demo and gallery: always labelled DEMO on screen."""
    lvl = "OK" if score is None or score >= 70 else "CAUTION" if score >= 50 else "LOW"
    base = dict(score=score, lvl=lvl, alert=score is not None and score < 50, obd="polling", drv_score=85,
                rpm=2340, spd=65, thr=18, fw="v21")
    vis = dict(vision=True, camera_ok=True, face_found=True, eyes="open", perclos=0.047, blink_rate=16,
               yaw=2, pitch=1, calibrated=True, calib_progress=1.0, demo=True)
    if score is not None and score < 70:
        vis.update(blink_rate=18, yaw=6, perclos=0.12)
    vis.update(kw)
    return state_from_status(base, linked=True, fresh=score is not None, **vis)


def gallery(out_dir: Path) -> Path:
    """Every screen as a PNG, plus one contact sheet (the design review image)."""
    from PIL import Image, ImageDraw
    out_dir.mkdir(parents=True, exist_ok=True)
    calib = dict(calibrated=False, calib_progress=0.68)
    shots = [
        ("01_welcome", demo_state(92), "welcome", {}),
        ("02_score_normal", demo_state(92), "score", {}),
        ("03_score_caution", demo_state(66), "score", {}),
        ("04_score_high", demo_state(34), "score", {}),
        ("05_score_unavailable", state_from_status({}, linked=False, fresh=False), "score", {}),
        ("06_calibration", demo_state(92, calibrating="quick", **calib), "calibration", {}),
        ("07_profile_step", demo_state(92, calibrating="profile", calib_prompt="Close your eyes", calib_step=2,
                                       calib_total=9, calib_progress=0.55), "calibration", {}),
        ("08_calibration_done", demo_state(92), "calibration", {"calib_done": "quick"}),
        ("09_awake_check", demo_state(30), "awake", {"awake": {"left": 7.0, "window": 10.0}}),
        ("10_awake_confirmed", demo_state(30), "awake", {"awake": {"confirmed": True}}),
    ]
    imgs = []
    for name, st, view, extra in shots:
        img = render(st, 0.3, view, True, None, extra)
        img.save(out_dir / f"whisplay_{name}.png")
        imgs.append((name[3:].replace("_", " "), img))
    cols, pad, cap = 4, 16, 22
    rows_n = math.ceil(len(imgs) / cols)
    sheet = Image.new("RGB", (cols * (W + pad) + pad, rows_n * (H + pad + cap) + pad), (246, 238, 240))
    d = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate(imgs):
        x, y = pad + (i % cols) * (W + pad), pad + (i // cols) * (H + pad + cap)
        sheet.paste(img, (x, y))
        d.text((x + W / 2, y + H + 11), label, fill=(60, 20, 30), anchor="mm", font=_label_font())
    path = out_dir / "whisplay_screens.png"
    sheet.save(path)
    return path


def _label_font():
    from PIL import ImageFont
    f = font(13, 600)
    try:
        return ImageFont.truetype(f.path, 13)
    except (AttributeError, OSError):
        return ImageFont.load_default()


def demo(sim: bool, out_dir: Path, driver_dir: str) -> int:
    """OK -> Level 1 -> Level 2 -> OK on the HAT (or as PNGs with --sim), example data."""
    cfg = WhisplayConfig(enabled=True, driver_dir=driver_dir)
    ui = WhisplayUI(cfg, sim=sim)
    script = [(0, None), (5, 92), (12, 66), (20, 34), (30, 88), (36, None)]
    t0 = time.monotonic()
    shots = {6: "1_ok", 14: "2_level1", 22: "3_level2", 33: "4_recovered"}
    if sim:
        out_dir.mkdir(parents=True, exist_ok=True)
    while True:
        t = time.monotonic() - t0
        score = next(sc for start, sc in reversed(script) if t >= start)
        if t >= script[-1][0]:
            break
        img = ui.tick(demo_state(score), time.monotonic())
        for at, name in list(shots.items()):
            if t >= at and sim:
                img.save(out_dir / f"whisplay_demo_{name}.png")
                shots.pop(at)
        time.sleep(0.1)
    print("demo done" + (f": screens in {out_dir}" if sim else ""))
    return 0


def browse(sim: bool, driver_dir: str, seconds: Optional[float] = None) -> int:
    """Example OK state (DEMO) on the HAT; every button press is printed."""
    from drifts.config import load_config
    cfg = load_config().whisplay
    cfg.driver_dir = driver_dir
    ui = WhisplayUI(cfg, sim=sim)
    print(f"Button: short={ui.actions['short']}  long={ui.actions['long']}  double={ui.actions['double']}"
          "  (change them under [whisplay] in config.toml).  Ctrl-C to stop.", flush=True)
    t0 = time.monotonic()
    try:
        while seconds is None or time.monotonic() - t0 < seconds:
            ui.tick(demo_state(92), time.monotonic())
            for at, ev, did in ui.presses:
                print(f"  {at - t0:6.1f} s  {ev:<6} -> {did}", flush=True)
            ui.presses.clear()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        ui.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m drifts.whisplay_ui", description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="store_true", help="cycle OK -> Level 1 -> Level 2 -> OK (36 s)")
    ap.add_argument("--sim", action="store_true", help="no HAT: save the screens as PNGs")
    ap.add_argument("--gallery", action="store_true", help="write every screen as a PNG and a contact sheet")
    ap.add_argument("--browse", action="store_true",
                    help="hold an example OK state so you can try the button on every screen (Ctrl-C to stop)")
    ap.add_argument("--out", default="whisplay_screens", help="PNG folder for --sim and --gallery")
    ap.add_argument("--driver-dir", default=WhisplayConfig.driver_dir)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if args.gallery:
        print(f"screens in {gallery(Path(args.out))}  (font: {font_name()})")
        return 0
    if args.browse:
        return browse(args.sim, args.driver_dir)
    if not args.demo:
        ap.print_help()
        return 0
    return demo(args.sim, Path(args.out), args.driver_dir)


if __name__ == "__main__":
    raise SystemExit(main())
