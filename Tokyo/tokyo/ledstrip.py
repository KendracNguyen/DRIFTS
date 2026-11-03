"""WS2812B LED strip (14 LEDs) above the LCD: status pixel + alert-score bar.

Identical to the first model's ESP32 strip (Esp32/drifts_esp32/led_strip.h):
both are tested against the same table, so a score lights the same LEDs on
both systems.

  pixel 0      status   green solid  = camera + ESP32 driving data linked
                        red slow     = connection failure
                        yellow slow  = system issue (no driver profile, camera fault)
  pixels 1-13  bar      length and colour follow the alert score:
                          70-100  green   75 % -> 100 % of the bar
                          40-69   yellow  50 % ->  74 %
                          10-39   red     25 % ->  49 %
                           0-9    red     whole bar, fast blink (4 Hz)
               alarm    whole bar red, 1 s on / 1 s off, in step with the buzzer
               partial  (one input missing) half brightness

The colour bands are the strip's own; the buzzer, motors and LCD still use
the alert levels (70 / 50). YELLOW_MIN = 50 makes the strip match them.

Backends: SpiStrip (pi5neo on SPI0 MOSI = GPIO 10, as wired in Rev 9),
PioStrip (Adafruit's RP1 PIO driver, any GPIO) and SimStrip (records frames).
`render` is pure and unit-tested.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from tokyo.config import StripConfig

Color = tuple  # (r, g, b) 0-255

OFF = (0, 0, 0)
GREEN = (0, 255, 40)
YELLOW = (255, 150, 0)
RED = (255, 0, 0)
WHITE = (255, 255, 255)
AMBER = YELLOW                 # older name
MA_PER_CHANNEL = 20.0          # one colour channel at 255 ~ 20 mA

STATUS_LED = 0
BAR_START = 1
GREEN_MIN, YELLOW_MIN, RED_MIN = 70.0, 40.0, 10.0


@dataclass
class StripState:
    score: Optional[float] = None    # alert score 0-100 (100 = fully alert); None = no data yet
    level: int = 0                   # 0 OK, 1 = Intervention 1, 2 = Intervention 2 (for the record)
    alarm: bool = False              # alarm pattern running
    alarm_since: float = 0.0         # phase reference, shared with the buzzer
    links_ok: bool = True            # camera and ESP32 driving data both working
    system_issue: bool = False
    partial: bool = False            # one input missing: drawn at half brightness


def band_color(score: float) -> Color:
    return GREEN if score >= GREEN_MIN else YELLOW if score >= YELLOW_MIN else RED


def bar_fraction(score: float) -> float:
    s = max(0.0, min(100.0, score))
    if s >= GREEN_MIN:
        return 0.75 + 0.25 * (s - GREEN_MIN) / (100.0 - GREEN_MIN)
    if s >= YELLOW_MIN:
        return 0.50 + 0.25 * (s - YELLOW_MIN) / (GREEN_MIN - YELLOW_MIN)
    if s >= RED_MIN:
        return 0.25 + 0.25 * (s - RED_MIN) / (YELLOW_MIN - RED_MIN)
    return 1.0


def bar_lit(score: float, bar_len: int) -> int:
    return max(1, min(bar_len, math.floor(bar_fraction(score) * bar_len + 0.5)))


def _blink(now: float, period: float) -> bool:
    return (now % period) < period / 2


def render(state: StripState, cfg: StripConfig, now: float) -> list:
    n = cfg.count
    frame = [OFF] * n
    if n == 0:
        return frame
    if not state.links_ok:
        frame[STATUS_LED] = RED if _blink(now, 1.0) else OFF
    elif state.system_issue:
        frame[STATUS_LED] = YELLOW if _blink(now, 1.0) else OFF
    else:
        frame[STATUS_LED] = GREEN
    bar = n - BAR_START
    if bar <= 0:
        return frame
    if state.alarm:
        on = ((now - state.alarm_since) % 2.0) < 1.0          # same 1 s / 1 s as the buzzer
        for i in range(BAR_START, n):
            frame[i] = RED if on else OFF
        return frame
    if state.score is None:
        return frame
    if state.score < RED_MIN:
        on = _blink(now, 0.25)
        for i in range(BAR_START, n):
            frame[i] = RED if on else OFF
        return frame
    c = band_color(state.score)
    if state.partial:
        c = tuple(v // 2 for v in c)
    for i in range(BAR_START, BAR_START + bar_lit(state.score, bar)):
        frame[i] = c
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


class SpiStrip:
    """Raspberry Pi 5, Rev 9 wiring: pi5neo on SPI0 (MOSI = GPIO 10).

    Install: pip install pi5neo; enable SPI with dtparam=spi=on in
    /boot/firmware/config.txt (see Docs/wiring.md).
    """

    def __init__(self, cfg: StripConfig):
        from pi5neo import Pi5Neo
        self.cfg = cfg
        self.neo = Pi5Neo(cfg.spi_device, num_leds=cfg.count, spi_speed_khz=800, quiet_mode=True)

    def show(self, frame: list) -> None:
        for i, (r, g, b) in enumerate(frame):
            self.neo.set_led_color(i, r, g, b)
        self.neo.update_strip(sleep_duration=None)

    def off(self) -> None:
        self.show([OFF] * self.cfg.count)


class PioStrip:
    """Raspberry Pi 5: adafruit_neopixel on top of the RP1 PIO driver (any GPIO).

    Install: pip install adafruit-blinka adafruit-circuitpython-neopixel \\
                         Adafruit-Blinka-Raspberry-Pi5-Neopixel
    Needs /dev/pio0 (current Pi 5 firmware).
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


PiStrip = PioStrip      # older name


class LedStrip:
    """render -> limits -> backend, at most ~30 updates per second."""

    def __init__(self, cfg: StripConfig, sim: bool = False):
        self.cfg = cfg
        if sim:
            self.dev = SimStrip(cfg)
        elif cfg.backend == "spi":
            self.dev = SpiStrip(cfg)
        elif cfg.backend == "pio":
            self.dev = PioStrip(cfg)
        else:
            raise ValueError("strip backend must be 'spi' or 'pio'")
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
