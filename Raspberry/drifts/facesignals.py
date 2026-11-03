"""The eight driver signals from MediaPipe face-mesh landmarks, plus the
glasses-aware fusion that turns them into one face score.

Pure Python (math only), so all of it is unit-tested without a camera:
tests/test_facesignals.py. drifts/facemesh.py feeds it from MediaPipe.

Signals (the dashboard's dev view shows each one):
  1-3  yaw / pitch / roll   head pose from MediaPipe's facial transformation
                            matrix: the "nose axis". Sign convention as
                            headpose.py: yaw + = driver turned to THEIR right,
                            pitch + = looking down, roll + = toward their
                            right shoulder (checked on real photos)
  4    off-road gaze        iris position between the eye corners + head pose
  5    yawn                 MAR held high (mouth.py)
  6    mouth aperture       MAR = inner-lip openings / mouth width (mouth.py)
  7    eye openness (EAR)   EAR = (|P2-P6| + |P3-P5|) / (2 |P1-P4|), per eye
  8    blinking             closed episodes of 50-500 ms -> rate, duration
  + PERCLOS (P80, 60 s): share of time the eyes are >= 80 % closed

Glasses. EAR depends on six eyelid points; lenses (reflections, frames over
the lid, refraction) make them less reliable. When glasses are detected (or
switched on in the dev view), the face score leans on the nose axis instead:
head nods and head drooping (the head dropping forward when drowsy).

  face score = w_eye x eye score + w_head x head score
     no glasses: w_eye 0.80, w_head 0.20      glasses: w_eye 0.40, w_head 0.60
  eye score  = 100 x (1 - PERCLOS)
  head score = 100 - 20 per nod in the last minute - 20 per second of droop
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional, Sequence

# MediaPipe face-mesh indices, in EAR order P1..P6 (P1/P4 = corners,
# P2-P6 and P3-P5 = upper/lower lid pairs). "Right" = the driver's right.
EAR_RIGHT = (33, 160, 158, 133, 153, 144)
EAR_LEFT = (362, 385, 387, 263, 373, 380)
IRIS_RIGHT, IRIS_LEFT = 468, 473
NOSE_TIP = 1
BRIDGE = 168             # between the eyes, where a glasses bridge sits
MOUTH_POINTS = (78, 82, 13, 312, 308, 317, 14, 87)

W_EYE_NORMAL, W_EYE_GLASSES = 0.80, 0.40


def _d(px, a: int, b: int) -> float:
    return math.hypot(float(px[a][0]) - float(px[b][0]), float(px[a][1]) - float(px[b][1]))


@dataclass
class EarTerms:
    v1: float     # |P2 - P6|
    v2: float     # |P3 - P5|
    h: float      # |P1 - P4|
    ear: float


def ear_terms(px: Sequence, idx: Sequence[int]) -> Optional[EarTerms]:
    """EAR with its three distances, for the dev view's worked formula."""
    if px is None or len(px) <= max(idx):
        return None
    p1, p2, p3, p4, p5, p6 = idx
    v1, v2, h = _d(px, p2, p6), _d(px, p3, p5), _d(px, p1, p4)
    if h < 1e-6:
        return None
    return EarTerms(v1, v2, h, (v1 + v2) / (2.0 * h))


def angles_from_matrix(m) -> tuple:
    """(yaw, pitch, roll) in degrees from MediaPipe's 4x4 facial transformation
    matrix, in the DRIFTS sign convention (see module docstring)."""
    r = [[float(m[i][j]) for j in range(3)] for i in range(3)]
    mp_yaw = math.degrees(math.atan2(-r[2][0], math.hypot(r[2][1], r[2][2])))
    mp_pitch = math.degrees(math.atan2(r[2][1], r[2][2]))
    mp_roll = math.degrees(math.atan2(r[1][0], r[0][0]))
    return -mp_yaw, mp_pitch, mp_roll


def nose_axes(m, nose_xy, length: float) -> dict:
    """End points of the head's three axes drawn from the nose tip (orthographic).
    x = across the face (to the driver's left), y = up the face, z = out of the
    face: the direction the head points."""
    r = [[float(m[i][j]) for j in range(3)] for i in range(3)]
    nx, ny = float(nose_xy[0]), float(nose_xy[1])
    out = {}
    for name, col, scale in (("x", 0, 1.0), ("y", 1, 1.0), ("z", 2, 4.0)):
        vx, vy = r[0][col], r[1][col]          # camera frame: y up, image y down
        out[name] = (nx + vx * length * scale, ny - vy * length * scale)
    return out


def iris_offset(px: Sequence) -> Optional[float]:
    """Mean horizontal iris position across both eyes: 0 = centred, about
    +/-0.5 at the corners. + = toward image right."""
    if px is None or len(px) <= IRIS_LEFT:
        return None
    vals = []
    for (a, b), iris in (((33, 133), IRIS_RIGHT), ((362, 263), IRIS_LEFT)):
        ax, ay = float(px[a][0]), float(px[a][1])
        bx, by = float(px[b][0]), float(px[b][1])
        ex, ey = bx - ax, by - ay
        n2 = ex * ex + ey * ey
        if n2 < 1e-6:
            continue
        t = ((float(px[iris][0]) - ax) * ex + (float(px[iris][1]) - ay) * ey) / n2
        vals.append((t - 0.5) * (1 if ex > 0 else -1))
    return sum(vals) / len(vals) if vals else None


# ---------------------------------------------------------------- eyes
class EyeTracker:
    """EAR -> openness (0 closed .. 1 open) -> blinks and PERCLOS.

    Openness is EAR relative to this driver's own open-eye EAR (the 85th
    percentile of the last minute), so it adapts to eye shape and camera
    angle without a calibration step. Durations are in seconds, so the
    meaning doesn't change with frame rate.
    """

    def __init__(self, window_s: float = 60.0, closed_below: float = 0.30, open_above: float = 0.45,
                 perclos_closed: float = 0.20, min_coverage_s: float = 10.0):
        self.window_s, self.closed_below, self.open_above = window_s, closed_below, open_above
        self.perclos_closed, self.min_coverage_s = perclos_closed, min_coverage_s
        self._ears: deque = deque()          # (t, ear) for the open-eye baseline
        self._samples: deque = deque()       # (t, openness) for PERCLOS
        self.blinks: deque = deque()         # (t_end, duration_s)
        self.closed = False
        self.closed_since: Optional[float] = None
        self.openness: Optional[float] = None
        self.ear_open: Optional[float] = None
        self.last_blink_ms: Optional[float] = None
        self.long_closures = 0

    def _baseline(self) -> float:
        vals = sorted(e for _, e in self._ears)
        if len(vals) < 5:
            return max(0.25, vals[-1]) if vals else 0.28
        return max(0.15, vals[int(0.85 * (len(vals) - 1))])

    def update(self, now: float, ear: Optional[float]) -> Optional[float]:
        while self._ears and now - self._ears[0][0] > self.window_s:
            self._ears.popleft()
        while self._samples and now - self._samples[0][0] > self.window_s:
            self._samples.popleft()
        while self.blinks and now - self.blinks[0][0] > self.window_s:
            self.blinks.popleft()
        if ear is None:
            self.openness = None
            self.closed, self.closed_since = False, None
            return None
        self._ears.append((now, ear))
        self.ear_open = self._baseline()
        lo = 0.35 * self.ear_open
        self.openness = max(0.0, min(1.0, (ear - lo) / (self.ear_open - lo)))
        self._samples.append((now, self.openness))
        if not self.closed and self.openness < self.closed_below:
            self.closed, self.closed_since = True, now
        elif self.closed and self.openness > self.open_above:
            dur = now - self.closed_since
            if 0.05 <= dur <= 0.5:
                self.blinks.append((now, dur))
                self.last_blink_ms = dur * 1000
            elif dur > 0.5:
                self.long_closures += 1
            self.closed, self.closed_since = False, None
        return self.openness

    def closed_for(self, now: float) -> float:
        return now - self.closed_since if self.closed and self.closed_since is not None else 0.0

    def coverage(self, now: float) -> float:
        return now - self._samples[0][0] if self._samples else 0.0

    def perclos(self, now: float) -> Optional[float]:
        """Time-weighted share of the window with openness < 0.2 (P80)."""
        if self.coverage(now) < self.min_coverage_s or len(self._samples) < 2:
            return None
        closed = total = 0.0
        s = list(self._samples)
        for (t0, o0), (t1, _) in zip(s, s[1:]):
            dt = min(t1 - t0, 0.5)            # a gap (no face) doesn't count as either
            total += dt
            if o0 < self.perclos_closed:
                closed += dt
        return closed / total if total > 0 else None

    def blink_rate(self, now: float) -> Optional[float]:
        cov = self.coverage(now)
        if cov < self.min_coverage_s:
            return None
        return len(self.blinks) * 60.0 / min(cov, self.window_s)

    def mean_blink_ms(self) -> Optional[float]:
        if not self.blinks:
            return None
        recent = list(self.blinks)[-10:]
        return 1000 * sum(d for _, d in recent) / len(recent)

    def state(self) -> str:
        if self.openness is None:
            return "no_face"
        return "closed" if self.closed else "open"


# ---------------------------------------------------------------- head (nose axis)
class NodTracker:
    """Head nods and drooping from pitch, relative to the driver's own neutral
    (the median pitch of the last 30 s), so it needs no calibration.

    nod   = the head drops >= drop_deg and comes back within 0.25-3 s
    droop = the head stays dropped for longer than 2 s (it is not counted
            again as a nod when it comes back)
    """

    def __init__(self, drop_deg: float = 12.0, back_deg: float = 6.0, neutral_s: float = 30.0,
                 window_s: float = 60.0, min_neutral_samples: int = 10):
        self.drop_deg, self.back_deg, self.neutral_s, self.window_s = drop_deg, back_deg, neutral_s, window_s
        self.min_neutral_samples = min_neutral_samples
        self._pitch: deque = deque()
        self.nods: deque = deque()
        self.down_since: Optional[float] = None
        self.drop: Optional[float] = None
        self.neutral: Optional[float] = None
        self.total_nods = 0

    def update(self, now: float, pitch: Optional[float]) -> None:
        while self._pitch and now - self._pitch[0][0] > self.neutral_s:
            self._pitch.popleft()
        while self.nods and now - self.nods[0] > self.window_s:
            self.nods.popleft()
        if pitch is None:
            self.drop, self.down_since = None, None
            return
        # The neutral only learns from un-dropped frames, so a long droop
        # doesn't become the new normal.
        if self.down_since is None:
            self._pitch.append((now, pitch))
        if len(self._pitch) < self.min_neutral_samples:
            self.neutral, self.drop = None, None
            return
        vals = sorted(p for _, p in self._pitch)
        self.neutral = vals[len(vals) // 2]
        self.drop = pitch - self.neutral
        if self.down_since is None and self.drop >= self.drop_deg:
            self.down_since = now
        elif self.down_since is not None and self.drop <= self.back_deg:
            dur = now - self.down_since
            if 0.25 <= dur <= 3.0:
                self.nods.append(now)
                self.total_nods += 1
            self.down_since = None

    def droop_s(self, now: float) -> float:
        if self.down_since is None:
            return 0.0
        d = now - self.down_since
        return d if d > 2.0 else 0.0

    def score(self, now: float) -> Optional[float]:
        if self.neutral is None:
            return None
        return max(0.0, min(100.0, 100.0 - 20.0 * len(self.nods) - 20.0 * self.droop_s(now)))


# ---------------------------------------------------------------- glasses
class GlassesDetector:
    """Glasses from edge strength across the nose bridge (facemesh.py measures
    it: a frame's bridge is a strong horizontal edge where skin is smooth).

    The measurement is smoothed over ~1.5 s with hysteresis, so a single
    frame can't flip the decision. mode "on" / "off" overrides it; the dev
    view shows the raw value so the threshold can be tuned on the team's own
    faces (Docs/dev-view.md).
    """

    def __init__(self, on_above: float = 0.22, off_below: float = 0.15, tau_s: float = 1.5, mode: str = "auto"):
        self.on_above, self.off_below, self.tau_s = on_above, off_below, tau_s
        self.mode = mode
        self.level: Optional[float] = None
        self.detected = False
        self._t: Optional[float] = None

    def update(self, now: float, edge: Optional[float]) -> bool:
        if edge is not None:
            if self.level is None or self._t is None:
                self.level = edge
            else:
                a = min(1.0, max(0.0, (now - self._t) / self.tau_s))
                self.level += a * (edge - self.level)
            self._t = now
            if not self.detected and self.level > self.on_above:
                self.detected = True
            elif self.detected and self.level < self.off_below:
                self.detected = False
        return self.wearing()

    def wearing(self) -> bool:
        if self.mode == "on":
            return True
        if self.mode == "off":
            return False
        return self.detected


def fuse(eye_score: Optional[float], head_score: Optional[float], glasses: bool) -> dict:
    """Face score from the eye and head parts. With glasses the nose axis
    (head) carries more weight because EAR is less trustworthy."""
    w_eye = W_EYE_GLASSES if glasses else W_EYE_NORMAL
    if eye_score is None and head_score is None:
        return {"score": None, "w_eye": w_eye, "w_head": 1 - w_eye, "basis": "none"}
    if eye_score is None:
        return {"score": head_score, "w_eye": 0.0, "w_head": 1.0, "basis": "head only"}
    if head_score is None:
        return {"score": eye_score, "w_eye": 1.0, "w_head": 0.0, "basis": "eyes only"}
    return {"score": w_eye * eye_score + (1 - w_eye) * head_score, "w_eye": w_eye, "w_head": 1 - w_eye,
            "basis": "glasses: leaning on head pose" if glasses else "eyes + head"}
