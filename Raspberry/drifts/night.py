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


# ---------------------------------------------------------------- simulated night (no IR board needed)
def simulate_night(frame, mode: str = "ir", side: str = "right", rng=None):
    """Turn a daylight colour frame into what the Camera Module 3 NoIR sees in a dark car.

    mode "ir":   lit by the 850 nm board 60 mm to one side of the lens: near-monochrome with a
                 faint purple cast, the light falling off away from the board and with depth,
                 smooth skin, sensor noise.
    mode "dark": no IR light: a very dark, noisy frame (the face is usually lost).
    side:        which side of the picture the board is on ("right" / "left").
    An approximation for demos and testing, not a model of the sensor."""
    import cv2
    import numpy as np
    rng = rng if rng is not None else np.random.default_rng()
    h, w = frame.shape[:2]
    lum = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    if mode == "dark":
        out = lum * 0.07 + rng.normal(0.0, 0.03, lum.shape).astype(np.float32)
        out = np.clip(out, 0, 1)
        return cv2.cvtColor((out * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    key = (h, w, side)
    if _FALLOFF.get("key") != key:                       # the light pattern only depends on the frame size
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx = w * (0.62 if side == "right" else 0.38)
        board = np.exp(-((xx - cx) ** 2 + (yy - h * 0.45) ** 2) / (2 * (0.40 * w) ** 2))
        centre = np.exp(-((xx - w / 2) ** 2 / (2 * (0.30 * w) ** 2) + (yy - h * 0.45) ** 2 / (2 * (0.38 * h) ** 2)))
        _FALLOFF.update(key=key, map=(0.20 + 0.95 * board) * (0.35 + 0.65 * centre))
    ir = cv2.GaussianBlur(lum, (0, 0), 1.3) ** 0.8 * _FALLOFF["map"]
    ir += rng.normal(0.0, 0.02, ir.shape).astype(np.float32)
    ir = np.clip(ir, 0, 1)
    out = np.dstack([ir * 1.04, ir * 0.96, ir * 1.03])   # BGR: a faint purple, as a NoIR sensor shows
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


_FALLOFF: dict = {}


class SimNightCamera:
    """Wraps any camera (grab() -> BGR frame) and returns simulate_night() frames, so the whole
    app (night detector, IR enhancement, face mesh, scores, phone view) sees a night picture.
    drifts.live --sim-night ir|dark"""

    def __init__(self, inner, mode: str = "ir", side: str = "right"):
        self.inner, self.mode, self.side = inner, mode, side

    def grab(self):
        frame = self.inner.grab()
        if frame is None or getattr(frame, "ndim", 0) != 3:
            return frame
        return simulate_night(frame, self.mode, self.side)

    def __getattr__(self, name):                         # everything else: the real camera
        return getattr(self.inner, name)
