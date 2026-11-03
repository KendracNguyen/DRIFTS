"""Tokyo's alert score, computed on the Pi 5. Same formula as the first model
(Esp32/drifts_esp32/alertness.h):

    score = 0.70 x eye score + 0.30 x driving score          (0-100, 100 = alert)

  eye score      from the Pi 5 camera: 100 x (1 - PERCLOS), dms-face's P80
                 over 60 s with invalid frames excluded
  driving score  from the first model's ESP32 (steering reversals, harsh
                 brakes, RPM / speed / throttle variation), read over BLE
                 telemetry ("dscore", firmware v16+) or the USB [SCORE] line

When one part is missing the score uses the other and says so:
  no eye data for eye_stale_s  -> driving only, `partial` (as alertness.h)
  no driving data              -> eye only, `no_driving` (ESP32 not connected)
  neither                      -> None

Pure Python, unit-tested in tests/test_score.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

LEVEL_NAMES = {0: "OK", 1: "CAUTION", 2: "LOW"}


@dataclass
class ScoreResult:
    score: Optional[float]
    eye: Optional[float]
    driving: Optional[float]
    partial: bool          # eye data missing or stale: driving only
    no_driving: bool       # driving data missing: eye only


class LocalScore:
    def __init__(self, eye_weight: float = 0.70, eye_stale_s: float = 120.0, driving_stale_s: float = 10.0):
        self.w, self.eye_stale_s, self.driving_stale_s = eye_weight, eye_stale_s, driving_stale_s
        self._eye: Optional[float] = None
        self._eye_t: Optional[float] = None
        self._drv: Optional[float] = None
        self._drv_t: Optional[float] = None

    def update(self, now: float, eye: Optional[float] = None, driving: Optional[float] = None) -> ScoreResult:
        if eye is not None:
            self._eye, self._eye_t = max(0.0, min(100.0, float(eye))), now
        if driving is not None:
            self._drv, self._drv_t = max(0.0, min(100.0, float(driving))), now
        eye_ok = self._eye is not None and now - self._eye_t <= self.eye_stale_s
        drv_ok = self._drv is not None and now - self._drv_t <= self.driving_stale_s
        e = self._eye if eye_ok else None
        d = self._drv if drv_ok else None
        if e is not None and d is not None:
            s = self.w * e + (1 - self.w) * d
        elif d is not None:
            s = d
        elif e is not None:
            s = e
        else:
            s = None
        return ScoreResult(score=s, eye=e, driving=d, partial=e is None, no_driving=d is None)
