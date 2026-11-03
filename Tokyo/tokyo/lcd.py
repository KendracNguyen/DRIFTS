"""16x2 LCD (HD44780 + PCF8574 backpack) on the Pi 5's I2C bus.

Screens are a line-for-line port of the first model's
Esp32/drifts_esp32/lcd_screen.h, so both projects show the same thing:
  boot   "Welcome to / DRIFTS" -> "Drive Safe" -> live screen
  OK     "ALERT     87/100" / "Drive Safe"
  Lv 1/2 "INTERVENTION n" / rotating: meaning -> "Score NN/100" (-> "PULL OVER SAFELY")

Wiring: GPIO 2 (SDA) / GPIO 3 (SCL) through a level shifter: the backpack is
5 V and pulls SDA/SCL toward 5 V, the Pi's pins are 3.3 V only (Docs/wiring.md).
Only changed characters are written.
"""

from __future__ import annotations

import time
from typing import Optional

COLS, ROWS = 16, 2
WELCOME_S, DRIVE_SAFE_S, SWAP_S = 2.5, 2.5, 2.0
MEANING = {0: "Drive Safe", 1: "Signs of Fatigue", 2: "Worsening Signs"}


def center(text: str) -> str:
    text = text[:COLS]
    left = (COLS - len(text)) // 2
    return (" " * left + text).ljust(COLS)


def compose(since_boot_s: float, score: Optional[float], level: int, alarm: bool = False,
            partial: bool = False) -> tuple:
    """(line1, line2), each exactly 16 characters."""
    if since_boot_s < WELCOME_S:
        return center("Welcome to"), center("DRIFTS")
    if since_boot_s < WELCOME_S + DRIVE_SAFE_S:
        return center("Drive Safe"), center("")
    s = 100 if score is None else max(0, min(100, int(score + 0.5)))
    lvl = 2 if alarm and level == 0 else level
    star = "*" if partial else ""
    if lvl == 0:
        right = f"{s}/100{star}"
        return "ALERT".ljust(COLS - len(right)) + right, center(MEANING[0])
    frames = 3 if alarm else 2
    k = int(since_boot_s / SWAP_S) % frames
    line2 = MEANING.get(lvl, "") if k == 0 else f"Score {s}/100{star}" if k == 1 else "PULL OVER SAFELY"
    return center(f"INTERVENTION {lvl}"), center(line2)


# PCF8574 -> HD44780 wiring on the common blue backpacks
RS, EN, BL = 0x01, 0x04, 0x08


class Lcd:
    def __init__(self, bus: int = 1, addresses=(0x27, 0x3F), backlight: bool = True, sim: bool = False):
        self.sim, self.backlight = sim, backlight
        self.addr = None
        self.shown = [[" "] * COLS for _ in range(ROWS)]
        self.lines = (" " * COLS, " " * COLS)
        self._t0 = time.monotonic()
        if sim:
            self.addr = 0x27
            return
        from smbus2 import SMBus
        self.bus = SMBus(bus)
        for a in addresses:
            try:
                self.bus.write_byte(a, 0)
                self.addr = a
                break
            except OSError:
                continue
        if self.addr is None:
            print("[LCD] No backpack at 0x27/0x3F; continuing without the LCD")
            return
        self._init()

    @property
    def present(self) -> bool:
        return self.addr is not None

    def _write4(self, nibble: int, rs: bool) -> None:
        b = (nibble << 4) | (RS if rs else 0) | (BL if self.backlight else 0)
        self.bus.write_byte(self.addr, b | EN)
        self.bus.write_byte(self.addr, b)

    def _byte(self, v: int, rs: bool) -> None:
        self._write4(v >> 4, rs)
        self._write4(v & 0x0F, rs)

    def _init(self) -> None:
        time.sleep(0.05)
        for n, wait in ((0x03, 0.005), (0x03, 0.001), (0x03, 0.001), (0x02, 0.001)):
            self._write4(n, False)
            time.sleep(wait)
        for cmd in (0x28, 0x0C, 0x06, 0x01):     # 4-bit 2-line, display on, entry, clear
            self._byte(cmd, False)
        time.sleep(0.002)
        self.shown = [[" "] * COLS for _ in range(ROWS)]

    def show(self, line1: str, line2: str) -> int:
        """Write only the changed characters; returns how many were written."""
        self.lines = (line1, line2)
        if not self.present:
            return 0
        n = 0
        for r, line in enumerate((line1, line2)):
            cursor = None
            for c, ch in enumerate(line[:COLS].ljust(COLS)):
                if self.shown[r][c] == ch:
                    continue
                if not self.sim:
                    if cursor != c:
                        self._byte(0x80 | ((0x40 if r else 0) + c), False)
                    self._byte(ord(ch), True)
                cursor = c + 1
                self.shown[r][c] = ch
                n += 1
        return n

    def update(self, score, level, alarm=False, partial=False) -> int:
        return self.show(*compose(time.monotonic() - self._t0, score, level, alarm, partial))
