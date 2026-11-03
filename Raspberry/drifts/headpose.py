"""Head pose and road attention from the five COCO face keypoints.

Stage 2 of the AI Camera plan (Docs/ai-camera-stage2.md): the IMX500's stock
pose network gives 17 body keypoints, of which five are on the face --
nose, left eye, right eye, left ear, right ear ("left" = the driver's left).
That is enough for:

  roll   good    angle of the line between the eyes
  yaw    good    nose offset from the eye midpoint, relative to eye spacing
  pitch  rough   nose drop below the eye line; needs a calibrated neutral
  road attention  head-direction proxy for gaze (no iris points)

Geometry (weak perspective, head as a rigid body). Eyes sit on a line
`IOD` apart; the nose tip is `NOSE_DROP x IOD` below that line and
`NOSE_DEPTH x IOD` in front of it. Turning the head by yaw y moves the nose
sideways by NOSE_DEPTH*sin(y) while the eye spacing shrinks to cos(y), so
nose_offset / observed_spacing = NOSE_DEPTH * tan(y). Pitching by p makes the
nose drop v0*cos(p) + NOSE_DEPTH*cos(y)*sin(p), solved exactly for p. Roll is removed
first by rotating into the eye line's frame.

The camera is rarely straight in front of the driver, so the "looking at the
road" pose is calibrated (auto: the median of the first seconds with a face;
or the dashboard's Calibrate button) and angles are reported relative to it.

Sign convention: yaw + = driver turned to THEIR right, pitch + = looking
down, roll + = head tilted toward their right shoulder. Coordinates are the
unmirrored camera image (x right, y down).

Pure Python + math only: unit-tested in tests/test_headpose.py.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4

# Anthropometric ratios, relative to the distance between the eyes (~63 mm).
NOSE_DEPTH = 0.56    # nose tip ~35 mm in front of the eye line
# Ears (tragus), only used to pick between two fits that explain the eyes and
# nose equally well: ~75 mm out, ~10 mm below and ~90 mm behind the eye line.
EAR_X, EAR_DROP, EAR_BACK = 1.19, 0.16, 1.43
NOSE_DROP = 0.635    # nose tip ~40 mm below the eye line (person-to-person
                     # differences are absorbed by calibration)
TURNED_YAW = 60.0    # reported when one eye is hidden (profile view)


@dataclass
class HeadPose:
    found: bool = False
    yaw: Optional[float] = None       # degrees, relative to calibration
    pitch: Optional[float] = None
    roll: Optional[float] = None
    quality: float = 0.0              # lowest keypoint score used, 0-1
    profile: bool = False             # one eye hidden: yaw is coarse


def _pt(kps, i, min_conf):
    if kps is None or len(kps) <= i:
        return None
    x, y, c = float(kps[i][0]), float(kps[i][1]), float(kps[i][2])
    return (x, y, c) if c >= min_conf else None


def raw_measures(kps: Sequence, min_conf: float = 0.3) -> Optional[dict]:
    """Roll-free measurements from keypoints (x, y, score), or None."""
    nose, le, re = (_pt(kps, i, min_conf) for i in (NOSE, L_EYE, R_EYE))
    if nose is None:
        return None
    if le is None or re is None:
        # Profile: the hidden eye is on the side turned away from the camera.
        seen = le or re
        if seen is None:
            return None
        return {"profile": True, "yaw_sign": 1 if re is None else -1,
                "quality": min(nose[2], seen[2])}
    # The driver's right eye appears on the image left (unmirrored).
    dx, dy = le[0] - re[0], le[1] - re[1]
    iod = math.hypot(dx, dy)
    if iod < 1e-6:
        return None
    roll = -math.degrees(math.atan2(dy, dx))     # + = tilt to driver's right
    # Rotate the nose into the eye-line frame (u along the eyes, v down).
    mx, my = (le[0] + re[0]) / 2, (le[1] + re[1]) / 2
    ux, uy = dx / iod, dy / iod
    nx, ny = nose[0] - mx, nose[1] - my
    u = nx * ux + ny * uy
    v = -nx * uy + ny * ux
    return {"profile": False, "roll": roll, "r": u / iod, "v_obs": v / iod, "iod": iod,
            "obs": (le[0] - mx, le[1] - my, nose[0] - mx, nose[1] - my),
            "ears": tuple(None if e is None else (e[0] - mx, e[1] - my)
                          for e in (_pt(kps, L_EAR, min_conf), _pt(kps, R_EAR, min_conf))),
            "quality": min(nose[2], le[2], re[2])}


def _project(a: float, p: float, q: float, sc: float) -> tuple:
    """Image offsets (from the eye midpoint) of the left eye and the nose tip for
    model rotation a (about the vertical axis, + turns the face to image right),
    p (+ = chin down) and q (in-plane), at scale sc pixels per eye spacing."""
    def rot(x, y, z):
        x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
        y, z = y * math.cos(p) + z * math.sin(p), -y * math.sin(p) + z * math.cos(p)
        return x * math.cos(q) - y * math.sin(q), x * math.sin(q) + y * math.cos(q)
    ex, ey = rot(0.5, 0.0, 0.0)
    nx, ny = rot(0.0, NOSE_DROP, NOSE_DEPTH)
    return (sc * ex, sc * ey, sc * nx, sc * ny)


def _ears(a: float, p: float, q: float, sc: float) -> tuple:
    """Image offsets of the left and right ear for a fitted pose."""
    out = []
    for sx in (1.0, -1.0):
        x, y, z = sx * EAR_X, EAR_DROP, -EAR_BACK
        x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
        y, z = y * math.cos(p) + z * math.sin(p), -y * math.sin(p) + z * math.cos(p)
        out.append((sc * (x * math.cos(q) - y * math.sin(q)), sc * (x * math.sin(q) + y * math.cos(q))))
    return tuple(out)


def _cost(obs: tuple, x: list) -> float:
    pr = _project(*x)
    return sum((pr[i] - obs[i]) ** 2 for i in range(4))


def _solve(obs: tuple, init: list, iters: int = 25) -> list:
    """Damped Gauss-Newton fit of (a, p, q, sc) to 4 observed numbers."""
    x = list(init)
    lam = 1e-3
    def resid(v):
        pr = _project(*v)
        return [pr[i] - obs[i] for i in range(4)]
    r = resid(x)
    cost = sum(e * e for e in r)
    for _ in range(iters):
        jac = []
        for j in range(4):
            h = 1e-5 if j < 3 else 1e-4 * max(1.0, abs(x[3]))
            xp = list(x); xp[j] += h
            rp = resid(xp)
            jac.append([(rp[i] - r[i]) / h for i in range(4)])    # jac[j][i] = d r_i / d x_j
        # Normal equations (J^T J + lam I) dx = -J^T r
        jtj = [[sum(jac[j][i] * jac[k][i] for i in range(4)) + (lam if j == k else 0.0)
                for k in range(4)] for j in range(4)]
        jtr = [sum(jac[j][i] * r[i] for i in range(4)) for j in range(4)]
        dx = _gauss(jtj, [-v for v in jtr])
        if dx is None:
            break
        cand = [x[i] + dx[i] for i in range(4)]
        rc = resid(cand)
        cc = sum(e * e for e in rc)
        if cc < cost:
            x, r, cost, lam = cand, rc, cc, lam * 0.3
            if max(abs(d) for d in dx[:3]) < 1e-7:
                break
        else:
            lam *= 10.0
    return x


def _gauss(m: list, v: list) -> Optional[list]:
    n = len(v)
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-12:
            return None
        a[c], a[piv] = a[piv], a[c]
        for r in range(n):
            if r != c:
                f = a[r][c] / a[c][c]
                for k in range(c, n + 1):
                    a[r][k] -= f * a[c][k]
    return [a[i][n] / a[i][i] for i in range(n)]


def estimate(kps: Sequence, cal: Optional["Calibration"] = None, min_conf: float = 0.3,
             prev: Optional[HeadPose] = None) -> HeadPose:
    """Head pose from keypoints. Without `cal`, angles are camera-relative.
    `prev` is the previous frame's camera-relative pose (helps continuity)."""
    m = raw_measures(kps, min_conf)
    if m is None:
        return HeadPose(found=False)
    if m["profile"]:
        return HeadPose(found=True, yaw=TURNED_YAW * m["yaw_sign"], profile=True, quality=m["quality"])
    # Closed-form start: roll from the eye line, yaw from the nose offset,
    # pitch from the nose drop; then fit all three jointly, because turning
    # and nodding together tilt the eye line (which is not roll).
    a0 = math.atan(m["r"] / NOSE_DEPTH)
    sc0 = m["iod"] / max(0.2, math.cos(a0))
    v = m["v_obs"] * math.cos(a0)
    rr, phi = math.hypot(NOSE_DROP, NOSE_DEPTH * math.cos(a0)), math.atan2(NOSE_DROP, NOSE_DEPTH * math.cos(a0))
    p0 = math.asin(max(-1.0, min(1.0, v / rr))) - phi
    q0 = -math.radians(m["roll"])
    # Three points allow two poses that fit equally well (strongest when the
    # head is both turned and nodded). Fit from several starts, keep the ones
    # that explain eyes + nose, then choose by ear positions and by closeness
    # to the previous frame.
    starts = [[a0, p0, q0, sc0]]
    if prev is not None and prev.found and not prev.profile and prev.yaw is not None:
        starts.append([-math.radians(prev.yaw), math.radians(prev.pitch), -math.radians(prev.roll), sc0])
    for da in (-0.8, 0.8):
        for dp in (-0.5, 0.5):
            starts.append([a0 + da, p0 + dp, q0, sc0])
    fits = []
    tol = (0.02 * m["iod"]) ** 2 * 4
    for st in starts:
        x = _solve(m["obs"], st)
        if abs(x[0]) < math.radians(85) and abs(x[1]) < math.radians(70) and _cost(m["obs"], x) <= tol:
            fits.append(x)
    if not fits:
        fits = [_solve(m["obs"], starts[0])]
    ears = m.get("ears", (None, None))
    def score(x):
        sc = 0.0
        if any(e is not None for e in ears):
            for model, seen in zip(_ears(*x), ears):
                if seen is not None:
                    sc += ((model[0] - seen[0]) ** 2 + (model[1] - seen[1]) ** 2) / m["iod"] ** 2
        if len(starts) > 5:      # previous frame available: prefer continuity
            pa, pp, pq = starts[1][:3]
            sc += 0.5 * ((x[0] - pa) ** 2 + (x[1] - pp) ** 2 + (x[2] - pq) ** 2)
        return sc
    a, p, q, _ = min(fits, key=score)
    pose = HeadPose(found=True, yaw=-math.degrees(a), pitch=math.degrees(p), roll=-math.degrees(q),
                    quality=m["quality"])
    return cal.apply(pose) if cal else pose


@dataclass
class Calibration:
    """Neutral 'looking at the road' pose, as angle offsets.

    The camera is usually off to one side and below the driver's eye line, so
    the road pose reads as some fixed yaw/pitch. Feed it camera-relative
    poses (estimate without cal); after `auto_samples` it takes the medians.
    """
    yaw0: float = 0.0
    pitch0: float = 0.0
    roll0: float = 0.0
    done: bool = False
    auto_samples: int = 30            # ~3 s at 10 fps
    _y: list = field(default_factory=list)
    _p: list = field(default_factory=list)
    _r: list = field(default_factory=list)

    def add(self, pose: HeadPose) -> None:
        if self.done or not pose.found or pose.profile:
            return
        self._y.append(pose.yaw)
        self._p.append(pose.pitch)
        self._r.append(pose.roll)
        if len(self._y) >= self.auto_samples:
            self.finish()

    def finish(self) -> bool:
        if len(self._y) < 3:
            return False
        self.yaw0, self.pitch0, self.roll0 = (statistics.median(v) for v in (self._y, self._p, self._r))
        self.done = True
        return True

    def restart(self, samples: int = 30) -> None:
        self.done, self.auto_samples = False, samples
        self._y, self._p, self._r = [], [], []

    def apply(self, pose: HeadPose) -> HeadPose:
        if not pose.found or pose.profile:
            return pose
        return HeadPose(found=True, yaw=pose.yaw - self.yaw0, pitch=pose.pitch - self.pitch0,
                        roll=pose.roll - self.roll0, quality=pose.quality)


class AttentionTracker:
    """Head-direction road attention with glance timing.

    Off road = head turned beyond YAW_LIMIT, or pitched down/up beyond the
    pitch limits. A glance counts as a distraction event once it lasts
    GLANCE_S (2 s: the single-glance limit in NHTSA's visual-manual
    distraction guidelines). With no face, the state is 'unknown' and the
    timer holds -- not seeing the driver is not evidence they look away.
    """

    def __init__(self, yaw_limit=30.0, pitch_down=20.0, pitch_up=20.0, glance_s=2.0, hysteresis=5.0):
        self.yaw_limit, self.pitch_down, self.pitch_up = yaw_limit, pitch_down, pitch_up
        self.glance_s, self.hyst = glance_s, hysteresis
        self.state = "unknown"            # on_road | off_road | unknown
        self.off_since: Optional[float] = None
        self.events = 0                   # glances that reached glance_s
        self._counted = False

    def _outside(self, pose: HeadPose, margin: float) -> bool:
        if pose.profile:
            return True
        return (abs(pose.yaw) > self.yaw_limit - margin or pose.pitch > self.pitch_down - margin
                or pose.pitch < -(self.pitch_up - margin))

    def update(self, now: float, pose: HeadPose, gaze_off: bool = False) -> str:
        """`gaze_off`: the eyes (iris) point away even if the head does not
        (Pi 5 rig only; the AI Camera has no iris points)."""
        if not pose.found or pose.yaw is None:
            self.state = "unknown"
            return self.state
        if self.state == "off_road":
            # Must come back inside the limits by `hyst` degrees to count as on road.
            off = self._outside(pose, self.hyst)
        else:
            off = self._outside(pose, 0.0)
        off = off or gaze_off
        if off:
            if self.state != "off_road":
                self.off_since, self._counted = now, False
            self.state = "off_road"
            if not self._counted and now - self.off_since >= self.glance_s:
                self.events += 1
                self._counted = True
        else:
            self.state, self.off_since = "on_road", None
        return self.state

    def off_for(self, now: float) -> float:
        return 0.0 if self.state != "off_road" or self.off_since is None else now - self.off_since

    def distracted(self, now: float) -> bool:
        return self.off_for(now) >= self.glance_s
