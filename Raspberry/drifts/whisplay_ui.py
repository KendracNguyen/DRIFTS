"""Driver display on the PiSugar Whisplay HAT (Pi Zero 2 W, first model).

The Whisplay HAT (Hardware/whisplay-hat.md) is the driver-facing part of
DRIFTS: a 1.69" 240 x 280 colour LCD, an RGB LED, a speaker and a button.
What it shows comes from the ESP32's 1 Hz BLE status (alert score, level,
driving score, OBD state). The ESP32 keeps the buzzer and motors, so the
alert still works if the Pi is down.

    python -m drifts.whisplay_ui --demo          # on the Pi: cycle OK -> Level 1 -> Level 2 on the HAT
    python -m drifts.whisplay_ui --demo --sim    # anywhere: writes the screens as PNGs instead

In the car it runs inside `python -m drifts` (drifts/app.py) when
[whisplay] enabled = true in config.toml.

What it does:
  screen  boot screen, then the score page (big score, level, coloured
          score bar, links); the button cycles to details and links pages
  LED     green = linked, yellow = Intervention 1, red 1 s/1 s = Intervention 2,
          red slow blink = connection lost, blue = starting
  voice   the clips in Voice/clips (Voice/phrases.json) on level changes and
          lost links, played on the HAT's speaker
  button  short press: next page, or acknowledge an alert ("I'm awake");
          long press (> 1.5 s): repeat the last message.
          A press never silences the ESP32's buzzer or motors.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from drifts.config import WhisplayConfig

log = logging.getLogger(__name__)

W, H = 240, 280
REPO = Path(__file__).resolve().parents[2]
PHRASES = REPO / "Voice" / "phrases.json"
CLIPS = REPO / "Voice" / "clips"

# Colours (RGB). Dark background, maroon accents, as the dashboard's dev view.
BG = (20, 12, 14)
CARD = (34, 22, 26)
MAROON = (125, 29, 44)
INK = (243, 231, 233)
MUTED = (168, 150, 155)
GREEN = (46, 204, 113)
YELLOW = (255, 196, 0)
RED = (255, 64, 80)
BLUE = (79, 195, 247)

LEVEL_OF = {"OK": 0, "CAUTION": 1, "LOW": 2}
BOOT_S = 4.0


# ---------------------------------------------------------------- state
@dataclass
class UiState:
    """Everything the screen and LED need, from the ESP32's status JSON."""
    score: Optional[float] = None       # alert score 0-100 (100 = alert)
    level: int = 0                      # 0 OK, 1 Intervention 1, 2 Intervention 2
    alert: bool = False                 # drowsy alert running on the ESP32
    partial: bool = False               # eye data missing: driving only
    driving: Optional[float] = None
    obd: Optional[str] = None           # "polling" when the dongle is live
    rpm: Optional[float] = None
    speed: Optional[float] = None
    esp_linked: bool = False            # BLE link to the ESP32 up and status fresh
    fw: Optional[str] = None

    @property
    def obd_ok(self) -> bool:
        return self.obd == "polling"

    @property
    def tier(self) -> int:
        return 2 if self.alert else self.level


def state_from_status(status: dict, linked: bool, fresh: bool = True) -> UiState:
    """UiState from esp_link.parse_phone_json() output."""
    lvl = status.get("lvl")
    return UiState(score=status.get("score") if fresh else None, level=LEVEL_OF.get(lvl, 0),
                   alert=bool(status.get("alert")) and fresh, partial=bool(status.get("part")),
                   driving=status.get("drv_score"), obd=status.get("obd") if fresh else None,
                   rpm=status.get("rpm"), speed=status.get("spd"), esp_linked=linked and fresh,
                   fw=status.get("fw"))


# ---------------------------------------------------------------- score bar (same bands as before)
def bar_color(score: float):
    return GREEN if score >= 70 else YELLOW if score >= 40 else RED


def bar_fraction(score: float) -> float:
    """Each colour band owns a quarter of the bar: green 75-100 %, yellow 50-74 %,
    red 25-49 %; below 10 the whole bar (flashing)."""
    s = max(0.0, min(100.0, score))
    if s >= 70:
        return 0.75 + 0.25 * (s - 70) / 30
    if s >= 40:
        return 0.50 + 0.25 * (s - 40) / 30
    if s >= 10:
        return 0.25 + 0.25 * (s - 10) / 30
    return 1.0


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


# ---------------------------------------------------------------- screen
def _fonts():
    from PIL import ImageFont
    def f(size, bold=False):
        for name in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
                     "/usr/share/fonts/truetype/dejavu/" + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()
    return {"huge": f(76, True), "big": f(24, True), "mid": f(17, True), "small": f(14), "tiny": f(12)}


_FONTS = None


def render(st: UiState, now: float, page: int = 0, booted: bool = True, banner: Optional[str] = None):
    """The 240 x 280 frame for this state (a PIL image)."""
    from PIL import Image, ImageDraw
    global _FONTS
    if _FONTS is None:
        _FONTS = _fonts()
    F = _FONTS
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    def center(y, text, font, fill=INK):
        w = d.textlength(text, font=font)
        d.text(((W - w) / 2, y), text, font=font, fill=fill)

    if not booted:
        center(92, "Welcome to", F["mid"], MUTED)
        center(118, "DRIFTS", F["big"], INK)
        center(160, "Drive safe", F["mid"], MUTED)
        return img

    tier = st.tier
    head = {0: (MAROON, "DRIFTS"), 1: ((150, 100, 0), "INTERVENTION 1"), 2: ((170, 20, 40), "INTERVENTION 2")}[tier]
    d.rectangle([0, 0, W, 34], fill=head[0])
    d.text((12, 8), head[1], font=F["mid"], fill=INK)
    dot = GREEN if st.esp_linked else RED
    d.ellipse([W - 22, 12, W - 12, 22], fill=dot)

    if page == 0:
        if st.score is None:
            center(100, "--", F["huge"], MUTED)
            center(186, "waiting for ESP32", F["small"], MUTED)
        else:
            center(52, f"{st.score:.0f}", F["huge"], INK if tier == 0 else (YELLOW if tier == 1 else RED))
            sub = {0: "ALERT  ·  / 100", 1: "Signs of fatigue", 2: "PULL OVER SAFELY"}[tier]
            center(138, sub, F["mid"], MUTED if tier == 0 else INK)
            # score bar
            x0, y0, bw, bh = 18, 168, W - 36, 18
            d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=9, fill=CARD)
            frac = bar_fraction(st.score)
            flash_off = (st.score < 10 or st.alert) and (now % 1.0) >= 0.5
            if not flash_off:
                col = bar_color(st.score) if not st.alert else RED
                if st.partial:
                    col = tuple(c // 2 for c in col)
                d.rounded_rectangle([x0, y0, x0 + max(bh, int(bw * frac)), y0 + bh], radius=9, fill=col)
            if st.partial:
                center(192, "partial: driving data only", F["tiny"], MUTED)
        drv = "--" if st.driving is None else f"{st.driving:.0f}"
        center(212, f"Driving score {drv}", F["small"], MUTED)
        links = f"OBD {'●' if st.obd_ok else '○'}   ESP32 {'●' if st.esp_linked else '○'}"
        center(238, links, F["small"], GREEN if (st.obd_ok and st.esp_linked) else RED)
        if not (st.obd_ok and st.esp_linked):
            center(256, "connection lost", F["tiny"], RED)
    elif page == 1:
        rows = [("Alert score", "--" if st.score is None else f"{st.score:.0f}"),
                ("Driving score", "--" if st.driving is None else f"{st.driving:.0f}"),
                ("RPM", "--" if st.rpm is None else f"{st.rpm:.0f}"),
                ("Speed", "--" if st.speed is None else f"{st.speed:.0f} km/h"),
                ("Eye data", "missing" if st.partial else "live")]
        d.text((14, 46), "DETAILS", font=F["mid"], fill=MUTED)
        for i, (k, v) in enumerate(rows):
            y = 78 + i * 36
            d.text((14, y), k, font=F["small"], fill=MUTED)
            w = d.textlength(v, font=F["mid"])
            d.text((W - 14 - w, y - 2), v, font=F["mid"], fill=INK)
    else:
        d.text((14, 46), "LINKS", font=F["mid"], fill=MUTED)
        rows = [("ESP32 (Bluetooth)", st.esp_linked), ("OBD dongle", st.obd_ok)]
        for i, (k, ok) in enumerate(rows):
            y = 84 + i * 40
            d.ellipse([14, y + 3, 26, y + 15], fill=GREEN if ok else RED)
            d.text((36, y), k, font=F["small"], fill=INK)
            d.text((36, y + 18), "connected" if ok else "not connected", font=F["tiny"], fill=MUTED)
        d.text((14, 190), f"ESP32 firmware {st.fw or '--'}", font=F["tiny"], fill=MUTED)
        d.text((14, 210), "Short press: next page", font=F["tiny"], fill=MUTED)
        d.text((14, 228), "Long press: repeat message", font=F["tiny"], fill=MUTED)
    if banner:
        d.rounded_rectangle([14, H - 44, W - 14, H - 10], radius=10, fill=MAROON)
        center(H - 37, banner, F["small"], INK)
    return img


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
PRIORITY = {"LEVEL2": 4, "LEVEL1": 3}


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
    """press/release times -> "short" or "long" (long = held >= long_s)."""

    def __init__(self, long_s: float = 1.5):
        self.long_s = long_s
        self._down: Optional[float] = None

    def press(self, now: float) -> None:
        self._down = now

    def release(self, now: float) -> Optional[str]:
        if self._down is None:
            return None
        held, self._down = now - self._down, None
        return "long" if held >= self.long_s else "short"


# ---------------------------------------------------------------- hardware
class SimBoard:
    """No HAT: keeps the last frame and LED colour (and can save PNGs)."""

    def __init__(self, out_dir: Optional[Path] = None):
        self.out_dir, self.frame, self.rgb, self.backlight = out_dir, None, (0, 0, 0), 0
        self._press = self._release = None

    def show(self, img) -> None:
        self.frame = img

    def set_rgb(self, r, g, b) -> None:
        self.rgb = (r, g, b)

    def set_backlight(self, pct: int) -> None:
        self.backlight = pct

    def on_button(self, press: Callable, release: Callable) -> None:
        self._press, self._release = press, release


class HatBoard:
    """PiSugar's WhisplayBoard (github.com/PiSugar/Whisplay, runtime/whisplay.py)."""

    def __init__(self, driver_dir: str):
        sys.path.insert(0, str(Path(driver_dir).expanduser()))
        from whisplay import WhisplayBoard            # noqa: E402  (PiSugar's driver)
        self.b = WhisplayBoard()

    def show(self, img) -> None:
        self.b.draw_image(0, 0, W, H, rgb565(img))

    def set_rgb(self, r, g, b) -> None:
        self.b.set_rgb(r, g, b)

    def set_backlight(self, pct: int) -> None:
        self.b.set_backlight(pct)

    def on_button(self, press: Callable, release: Callable) -> None:
        self.b.on_button_press(press)
        self.b.on_button_release(release)


# ---------------------------------------------------------------- the UI
class WhisplayUI:
    PAGES = 3

    def __init__(self, cfg: WhisplayConfig, board=None, sim: bool = False):
        self.cfg = cfg
        self.board = board or (SimBoard() if sim else HatBoard(cfg.driver_dir))
        self.speaker = Speaker(cfg.audio_device, sim=sim)
        self.voice = VoiceDirector(repeat_s=cfg.repeat_s)
        self.button = ButtonDecoder()
        self.page = 0
        self.t0: Optional[float] = None
        self.banner: Optional[str] = None
        self.banner_until = 0.0
        self._events: list = []
        self._last_png = None
        self.board.set_backlight(cfg.brightness)
        self.board.on_button(lambda: self.button.press(time.monotonic()),
                             lambda: self._events.append(self.button.release(time.monotonic())))

    def _flash(self, text: str, now: float, s: float = 2.0) -> None:
        self.banner, self.banner_until = text, now + s

    def handle(self, event: Optional[str], st: UiState, now: float) -> None:
        if event == "short":
            if st.tier >= 1:
                self.voice.acknowledge(now)
                log.info("[UI] Driver acknowledged the alert")
                self._flash("Acknowledged", now)
            else:
                self.page = (self.page + 1) % self.PAGES
        elif event == "long" and self.speaker.last:
            self.speaker.play(self.speaker.last, now)
            self._flash("Repeating message", now)

    def tick(self, st: UiState, now: float):
        """One update: button events, voice, LED, screen. Returns the frame."""
        if self.t0 is None:
            self.t0 = now
        booted = now - self.t0 >= BOOT_S
        while self._events:
            self.handle(self._events.pop(0), st, now)
        if st.tier >= 1 and self.page != 0:
            self.page = 0                          # an alert always shows the score page
        if self.cfg.voice and booted:
            name = self.voice.update(st, now, self.speaker.playing(now))
            if name:
                self.speaker.play(name, now)
        self.board.set_rgb(*led_color(st, now, booted))
        banner = self.banner if now < self.banner_until else None
        img = render(st, now, self.page, booted, banner)
        self.board.show(img)
        return img

    async def run(self, get_state: Callable[[], UiState], period_s: float = 0.25) -> None:
        import asyncio
        log.info("[UI] Whisplay display running")
        while True:
            try:
                self.tick(get_state(), time.monotonic())
            except Exception as exc:        # the display must never stop the app
                log.warning("[UI] update failed: %s", exc)
            await asyncio.sleep(period_s)


# ---------------------------------------------------------------- demo
def demo(sim: bool, out_dir: Path, driver_dir: str) -> int:
    cfg = WhisplayConfig(enabled=True, driver_dir=driver_dir)
    ui = WhisplayUI(cfg, sim=sim)
    script = [(0, None), (5, 92), (12, 62), (20, 35), (30, 88), (36, None)]
    t0 = time.monotonic()
    shots = {6: "1_ok", 14: "2_level1", 22: "3_level2", 33: "4_recovered"}
    if sim:
        out_dir.mkdir(parents=True, exist_ok=True)
    while True:
        t = time.monotonic() - t0
        score = next(sc for start, sc in reversed(script) if t >= start)
        if t >= script[-1][0]:
            break
        lvl = "OK" if score is None or score >= 70 else "CAUTION" if score >= 50 else "LOW"
        st = state_from_status({"score": score, "lvl": lvl, "alert": score is not None and score < 50,
                                "obd": "polling", "drv_score": 85, "rpm": 1800, "spd": 72, "fw": "v20"},
                               linked=True, fresh=score is not None)
        img = ui.tick(st, time.monotonic())
        for at, name in list(shots.items()):
            if t >= at and sim:
                img.save(out_dir / f"whisplay_{name}.png")
                shots.pop(at)
        time.sleep(0.1)
    print("demo done" + (f": screens in {out_dir}" if sim else ""))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m drifts.whisplay_ui", description=__doc__.splitlines()[0])
    ap.add_argument("--demo", action="store_true", help="cycle OK -> Level 1 -> Level 2 -> OK (36 s)")
    ap.add_argument("--sim", action="store_true", help="no HAT: save the screens as PNGs")
    ap.add_argument("--out", default="whisplay_screens", help="PNG folder for --sim")
    ap.add_argument("--driver-dir", default=WhisplayConfig.driver_dir)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if not args.demo:
        ap.print_help()
        return 0
    return demo(args.sim, Path(args.out), args.driver_dir)


if __name__ == "__main__":
    raise SystemExit(main())
