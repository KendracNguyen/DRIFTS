"""WS2812B LED strip (160 LEDs) above the LCD: status + alert-score bar.

Layout, left to right:
  [ status x8 ][ gap ][ alert-score bar ................................ ]

  status  green solid = camera + OBD linked   green slow blink = a link missing
          amber slow blink = system issue (no driver profile, camera fault)
  bar     length = alert score 0-100; colour = level
          green >= 70 (OK) | amber 50-69 (Intervention 1) | red < 50 (Intervention 2)
  alarm   the whole bar flashes red with the buzzer: 1 s on, 1 s off

Power: one WS2812B pixel draws up to ~60 mA at full white, so 160 pixels
could pull ~9.6 A. Every frame is scaled by `brightness`, then dimmed further
if its estimated current would exceed `max_current_a` (see Docs/wiring.md).

Backends: PiStrip (Raspberry Pi 5, RP1 PIO driver via Adafruit's
Adafruit-Blinka-Raspberry-Pi5-Neopixel) and SimStrip (records frames).
`render` is pure and unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tokyo.config import StripConfig

Color = tuple  # (r, g, b) 0-255

GREEN, AMBER, RED, OFF = (0, 255, 40), (255, 120, 0), (255, 0, 0), (0, 0, 0)
MA_PER_CHANNEL = 20.0          # one colour channel at 255 ~ 20 mA


@dataclass
class StripState:
    score: Optional[float] = None    # alert score 0-100 (100 = fully alert)
    level: int = 0                   # 0 OK, 1 = Intervention 1, 2 = Intervention 2
    alarm: bool = False              # alarm pattern running
    alarm_since: float = 0.0         # phase reference, shared with the buzzer
    links_ok: bool = True            # camera and OBD dongle both working
    system_issue: bool = False


def _blink(now: float, period: float = 1.0) -> bool:
    return (now % period) < period / 2


def render(state: StripState, cfg: StripConfig, now: float) -> list:
    n = cfg.count
    frame = [OFF] * n
    st = min(cfg.status_leds, n)
    # Status segment
    if state.system_issue:
        col = AMBER if _blink(now) else OFF
    elif state.links_ok:
        col = GREEN
    else:
        col = GREEN if _blink(now) else OFF
    for i in range(st):
        frame[i] = col
    # Alert-score bar (one LED gap after the status segment)
    start = min(n, st + 1)
    bar = n - start
    if bar <= 0:
        return frame
    if state.alarm:
        on = ((now - state.alarm_since) % 2.0) < 1.0          # same 1 s / 1 s as the buzzer
        for i in range(start, n):
            frame[i] = RED if on else OFF
        return frame
    if state.score is None:
        return frame
    colour = {0: GREEN, 1: AMBER}.get(state.level, RED)
    lit = round(bar * max(0.0, min(100.0, state.score)) / 100.0)
    for i in range(start, start + lit):
        frame[i] = colour
    return frame


def estimate_current_a(frame: list) -> float:
    return sum(c for px in frame for c in px) / 255.0 * MA_PER_CHANNEL / 1000.0


def apply_limits(frame: list, brightness: float, max_current_a: float) -> list:
    """Scale by brightness, then further if the strip would exceed the current budget."""
    b = max(0.0, min(1.0, brightness))
    scaled = [tuple(int(c * b) for c in px) for px in frame]
    amps = estimate_current_a(scaled)
    if amps > max_current_a > 0:
        k = max_current_a / amps
        scaled = [tuple(int(c * k) for c in px) for px in scaled]
    return scaled


class SimStrip:
    def __init__(self, cfg: StripConfig):
        self.cfg, self.last = cfg, [OFF] * cfg.count

    def show(self, frame: list) -> None:
        self.last = list(frame)

    def off(self) -> None:
        self.show([OFF] * self.cfg.count)


class PiStrip:
    """Raspberry Pi 5: adafruit_neopixel on top of the RP1 PIO driver.

    Install: pip install adafruit-blinka adafruit-circuitpython-neopixel \\
                         Adafruit-Blinka-Raspberry-Pi5-Neopixel
    Needs /dev/pio0 (current Pi 5 firmware); see Docs/wiring.md.
    """

    def __init__(self, cfg: StripConfig):
        import board
        import neopixel
        self.cfg = cfg
        pin = getattr(board, f"D{cfg.pin}")
        order = getattr(neopixel, cfg.order, neopixel.GRB)
        # Brightness is applied in apply_limits, so the library runs at 1.0.
        self.px = neopixel.NeoPixel(pin, cfg.count, brightness=1.0, auto_write=False, pixel_order=order)

    def show(self, frame: list) -> None:
        for i, c in enumerate(frame):
            self.px[i] = c
        self.px.show()

    def off(self) -> None:
        self.px.fill(OFF)
        self.px.show()


class LedStrip:
    """render -> limits -> backend, at most ~30 updates per second."""

    def __init__(self, cfg: StripConfig, sim: bool = False):
        self.cfg = cfg
        self.dev = SimStrip(cfg) if sim else PiStrip(cfg)
        self.amps = 0.0
        self._last_t = 0.0

    def update(self, state: StripState, now: float, force: bool = False) -> None:
        if not force and now - self._last_t < 1 / 30:
            return
        self._last_t = now
        frame = apply_limits(render(state, self.cfg, now), self.cfg.brightness, self.cfg.max_current_a)
        self.amps = estimate_current_a(frame)
        self.dev.show(frame)

    def off(self) -> None:
        self.dev.off()
