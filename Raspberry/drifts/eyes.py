"""Live eye-state estimate for the dashboard: OpenCV Haar cascades + PERCLOS.

This is a DEMO tracker, not the trained model. It finds the largest face,
looks for eyes in the upper half of it, and calls the eyes "closed" when
none are found (the eye cascade rarely fires on a closed eye). Glasses,
side lighting and head turns all fool it; it is good enough to show the
live loop and to collect labelled frames for training the real model
(Raspberry/training).

PERCLOS is the fraction of time the eyes were closed over a sliding window.
The eye score sent to the ESP32 is 100 x (1 - PERCLOS): 100 = eyes open the
whole window, 0 = closed the whole window. The ESP32 maps that into its
alert score (alertness.h: 0.7 x eye + 0.3 x driving).

Pure parts (PerclosWindow, eye_score) need no OpenCV and are unit-tested.
"""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

OPEN, CLOSED, NO_FACE = "open", "closed", "no_face"


# --------------------------------------------------------------------- pure
class PerclosWindow:
    """Time-weighted fraction of a sliding window spent with eyes closed.

    Samples without a face are ignored rather than counted as open or closed:
    looking away from the camera is not evidence either way.
    """

    def __init__(self, window_s: float = 10.0):
        self.window_s = window_s
        self._samples: collections.deque[tuple[float, bool]] = collections.deque()
        self._closed_since: Optional[float] = None

    def add(self, t: float, state: str) -> None:
        if state == NO_FACE:
            self._closed_since = None
        else:
            closed = state == CLOSED
            self._samples.append((t, closed))
            if closed and self._closed_since is None:
                self._closed_since = t
            elif not closed:
                self._closed_since = None
        while self._samples and t - self._samples[0][0] > self.window_s:
            self._samples.popleft()

    def perclos(self) -> Optional[float]:
        """0.0-1.0, or None when the window holds no face samples."""
        if not self._samples:
            return None
        return sum(c for _, c in self._samples) / len(self._samples)

    def closed_for(self, now: float) -> float:
        """Seconds the eyes have been continuously closed (0 if open)."""
        return 0.0 if self._closed_since is None else now - self._closed_since

    def coverage(self, now: float) -> float:
        """Seconds of the window that hold samples (for 'warming up')."""
        if not self._samples:
            return 0.0
        return min(self.window_s, now - self._samples[0][0])


def eye_score(perclos: Optional[float]) -> Optional[int]:
    """Eye score for the ESP32 (EYE:<n>): 100 = fully alert, 0 = eyes shut."""
    if perclos is None:
        return None
    return max(0, min(100, round(100 * (1.0 - perclos))))


# --------------------------------------------------------------- detection
Box = tuple[int, int, int, int]   # x, y, w, h in full-frame pixels


@dataclass
class EyeObservation:
    state: str
    face: Optional[Box] = None
    eyes: list[Box] = field(default_factory=list)


def _cascade_dirs() -> list[Path]:
    dirs = []
    try:
        import cv2
        if hasattr(cv2, "data"):
            dirs.append(Path(cv2.data.haarcascades))
    except ImportError:
        pass
    # Raspberry Pi OS python3-opencv (apt) has no cv2.data
    dirs += [Path("/usr/share/opencv4/haarcascades"), Path("/usr/share/opencv/haarcascades")]
    return dirs


def _load_cascade(name: str):
    import cv2
    for d in _cascade_dirs():
        p = d / name
        if p.exists():
            c = cv2.CascadeClassifier(str(p))
            if not c.empty():
                return c
    raise FileNotFoundError(f"OpenCV cascade {name} not found in {[str(d) for d in _cascade_dirs()]}")


class EyeTracker:
    """Largest face, then eyes in its upper half. Works on BGR or RGB frames."""

    def __init__(self, work_width: int = 320, closed_frames: int = 2):
        import cv2
        self._cv2 = cv2
        self.work_width = work_width
        # Debounce: one missed detection is noise; N in a row is a closed eye.
        self.closed_frames = closed_frames
        self._misses = 0
        self._face = _load_cascade("haarcascade_frontalface_default.xml")
        # The eyeglasses variant copes better with glasses and is no slower.
        self._eye = _load_cascade("haarcascade_eye_tree_eyeglasses.xml")

    def process(self, frame) -> EyeObservation:
        cv2 = self._cv2
        h, w = frame.shape[:2]
        scale = self.work_width / float(w) if w > self.work_width else 1.0
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        gray = cv2.equalizeHist(gray)

        faces = self._face.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5,
                                            minSize=(int(gray.shape[1] * 0.18),) * 2)
        if len(faces) == 0:
            self._misses = 0
            return EyeObservation(NO_FACE)

        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        # Eyes sit in the upper ~55 % of the face box.
        roi = gray[fy + int(fh * 0.15): fy + int(fh * 0.58), fx: fx + fw]
        eyes = self._eye.detectMultiScale(roi, scaleFactor=1.1, minNeighbors=6,
                                          minSize=(max(8, fw // 8),) * 2)

        inv = 1.0 / scale
        face_box = (int(fx * inv), int(fy * inv), int(fw * inv), int(fh * inv))
        eye_boxes = [(int((fx + ex) * inv), int((fy + int(fh * 0.15) + ey) * inv), int(ew * inv), int(eh * inv))
                     for ex, ey, ew, eh in eyes[:2]]

        if len(eyes) > 0:
            self._misses = 0
            state = OPEN
        else:
            self._misses += 1
            state = CLOSED if self._misses >= self.closed_frames else OPEN
        return EyeObservation(state, face_box, eye_boxes)
