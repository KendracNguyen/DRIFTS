"""Mouth aperture (MAR) and yawn detection from MediaPipe face-mesh landmarks.

dms-face (the Pi 5 eye/head pipeline we build on) does not cover the mouth, so
this adds it from the same landmarks, at no extra inference cost.

MAR = mean of three inner-lip openings / inner mouth width
      openings: 13-14 (centre), 82-87 and 312-317 (either side)
      width:    78-308 (inner lip corners)

Typical values: closed ~0.0-0.1, talking ~0.2-0.4, yawn > 0.5.

A yawn is the mouth held wide open (MAR above YAWN_OPEN) for at least
MIN_YAWN_S. Talking and laughing open the mouth for well under a second at
a time; a yawn lasts several seconds. Hysteresis (close below YAWN_CLOSE)
stops one yawn from counting twice. Durations are in seconds, not frames,
so the meaning does not change with frame rate.

Pure Python: tested in tests/test_mouth.py.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Optional, Sequence

UPPER_LOWER = ((13, 14), (82, 87), (312, 317))
CORNERS = (78, 308)

YAWN_OPEN = 0.50
YAWN_CLOSE = 0.35
MIN_YAWN_S = 1.5


def mar(pts: Sequence) -> Optional[float]:
    """Mouth aspect ratio from (N, 2) pixel landmarks, or None if unavailable."""
    if pts is None or len(pts) <= max(max(p) for p in UPPER_LOWER + (CORNERS,)):
        return None
    def d(a, b):
        return math.hypot(float(pts[a][0]) - float(pts[b][0]), float(pts[a][1]) - float(pts[b][1]))
    width = d(*CORNERS)
    if width < 1e-6:
        return None
    return sum(d(a, b) for a, b in UPPER_LOWER) / (len(UPPER_LOWER) * width)


class YawnDetector:
    """Counts yawns: MAR > open threshold for >= min_s, with hysteresis."""

    def __init__(self, open_thr=YAWN_OPEN, close_thr=YAWN_CLOSE, min_s=MIN_YAWN_S, window_s=600.0):
        self.open_thr, self.close_thr, self.min_s, self.window_s = open_thr, close_thr, min_s, window_s
        self.is_open = False
        self.open_since: Optional[float] = None
        self.counted = False
        self.events: deque = deque()        # (start time, duration) of completed yawns
        self.total = 0

    def update(self, now: float, value: Optional[float]) -> bool:
        """Feed one MAR sample. Returns True on the frame a yawn is confirmed."""
        confirmed = False
        if value is None:                   # no face: end any open episode
            self._close(now)
            return False
        if not self.is_open and value > self.open_thr:
            self.is_open, self.open_since, self.counted = True, now, False
        elif self.is_open and value < self.close_thr:
            self._close(now)
        if self.is_open and not self.counted and now - self.open_since >= self.min_s:
            self.counted, confirmed = True, True
            self.total += 1
        while self.events and now - self.events[0][0] > self.window_s:
            self.events.popleft()
        return confirmed

    def _close(self, now: float) -> None:
        if self.is_open and self.counted:
            self.events.append((self.open_since, now - self.open_since))
        self.is_open, self.open_since = False, None

    def yawning(self) -> bool:
        return self.is_open and self.counted

    def recent(self, now: float) -> int:
        """Yawns in the last window (default 10 min), including one in progress."""
        n = sum(1 for t, _ in self.events if now - t <= self.window_s)
        return n + (1 if self.yawning() else 0)
