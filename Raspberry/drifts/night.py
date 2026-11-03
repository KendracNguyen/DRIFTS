"""Day / night (IR) detection from the camera image, and IR image enhancement.

The Arducam day-night OV5647 switches its IR-cut filter and IR LEDs by
itself, from its own light sensor (software control was removed in current
Raspberry Pi OS). The app cannot ask which mode it is in, so it infers it:
under IR light the three colour channels respond almost equally, so the
picture loses nearly all colour saturation. Low saturation for a few seconds
= night (IR) mode.

Night frames are low-contrast; CLAHE on the luminance (the same treatment
dms-face applies in its --ir mode) helps the face landmarker.
"""

from __future__ import annotations

from typing import Optional

NIGHT_SAT = 0.10      # mean HSV saturation below this looks like IR
DAY_SAT = 0.16        # and above this looks like daylight (hysteresis)
HOLD_S = 3.0          # must persist this long to switch


def mean_saturation(frame) -> Optional[float]:
    """Mean HSV saturation 0-1 of a BGR frame (subsampled for speed)."""
    import cv2
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
        return None
    small = frame[::8, ::8]
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) / 255.0


class NightDetector:
    def __init__(self, night_sat=NIGHT_SAT, day_sat=DAY_SAT, hold_s=HOLD_S):
        self.night_sat, self.day_sat, self.hold_s = night_sat, day_sat, hold_s
        self.night = False
        self._pending_since: Optional[float] = None
        self.saturation: Optional[float] = None

    def update(self, now: float, saturation: Optional[float]) -> bool:
        self.saturation = saturation
        if saturation is None:
            return self.night
        wants = (saturation < self.night_sat) if not self.night else not (saturation > self.day_sat)
        if wants != self.night:
            if self._pending_since is None:
                self._pending_since = now
            if now - self._pending_since >= self.hold_s:
                self.night, self._pending_since = wants, None
        else:
            self._pending_since = None
        return self.night


def enhance_ir(frame):
    """CLAHE on luminance; returns a BGR frame."""
    import cv2
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab[..., 0] = clahe.apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
