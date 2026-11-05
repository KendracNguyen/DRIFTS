"""Guided calibration and accuracy test for the face mesh (dev view).

Calibration: nine poses, each held for 2 s:
    eyes closed, eyes open, mouth closed, mouth open, left, right, up, down, forward

A step is accepted once its condition holds for `hold_s` without a break.
The medians of that hold are recorded. The result is a driver profile:
    ear_closed / ear_open   -> the eye tracker's openness scale
    mar_closed / mar_open   -> the yawn threshold
    yaw / pitch at left, right, up, down -> the off-road limits
    forward yaw / pitch / roll / iris    -> the road pose
saved as Raspberry/profiles/<name>.json and loaded at start.

Accuracy test: the same kind of prompts, each held for `test_s`. Every
frame after a 1 s settle is scored: does DRIFTS classify what the driver
was asked to do (eyes closed, looking left, mouth open...)? The report is
the share of frames classified correctly, per prompt and overall.

Pure logic: frames come in as dicts (FaceMeshSource.face plus the pipeline's
pose), so tests/test_guided.py runs it without a camera.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PROFILE_DIR = Path(__file__).resolve().parents[1] / "profiles"


def _blink(f: dict) -> Optional[float]:
    bs = f.get("blend") or {}
    a, b = bs.get("eyeBlinkLeft"), bs.get("eyeBlinkRight")
    return None if a is None or b is None else (a + b) / 2


def _jaw(f: dict) -> Optional[float]:
    return (f.get("blend") or {}).get("jawOpen")


@dataclass
class Step:
    key: str
    label: str
    prompt: str
    metric: str                       # what the step card shows: ear, mar, yaw, pitch
    ok: Callable[[dict, dict], bool]  # (frame, values recorded so far) -> condition holds


def _found(f):
    return bool(f.get("found"))


def _eyes_closed(f, rec):
    b, ear = _blink(f), f.get("ear")
    if b is not None and b > 0.5:
        return True
    return ear is not None and f.get("ear_open") and ear < 0.6 * f["ear_open"]


def _eyes_open(f, rec):
    b, ear = _blink(f), f.get("ear")
    closed = rec.get("eyes_closed", {}).get("ear")
    if ear is None:
        return False
    if closed is not None and ear < closed * 1.3:
        return False
    return b is None or b < 0.35


def _mouth_closed(f, rec):
    j, m = _jaw(f), f.get("mar")
    return m is not None and m < 0.12 and (j is None or j < 0.2)


def _mouth_open(f, rec):
    j, m = _jaw(f), f.get("mar")
    return m is not None and (m > 0.35 or (j is not None and j > 0.45))


def _turned(axis: str, sign: int, deg: float):
    def ok(f, rec):
        ref = rec.get("eyes_open", {}).get(axis, 0.0) or 0.0     # head pose while looking ahead
        v = f.get(axis)
        return v is not None and sign * (v - ref) > deg
    return ok


def _forward(f, rec):
    ref = rec.get("eyes_open", {})
    y, p = f.get("yaw"), f.get("pitch")
    if y is None or p is None:
        return False
    return abs(y - ref.get("yaw", y)) < 10 and abs(p - ref.get("pitch", p)) < 8 and (_blink(f) or 0) < 0.4


CAL_STEPS = [
    Step("eyes_closed", "Eyes closed", "Close your eyes and keep them closed", "ear", lambda f, r: _found(f) and _eyes_closed(f, r)),
    Step("eyes_open", "Eyes open", "Open your eyes and look at the road", "ear", lambda f, r: _found(f) and _eyes_open(f, r)),
    Step("mouth_closed", "Mouth closed", "Close your mouth", "mar", lambda f, r: _found(f) and _mouth_closed(f, r)),
    Step("mouth_open", "Mouth open", "Open your mouth wide, as in a yawn", "mar", lambda f, r: _found(f) and _mouth_open(f, r)),
    Step("left", "Left", "Turn your head to the left (towards the driver's window)", "yaw", lambda f, r: _found(f) and _turned("yaw", -1, 15)(f, r)),
    Step("right", "Right", "Turn your head to the right (towards the passenger)", "yaw", lambda f, r: _found(f) and _turned("yaw", 1, 15)(f, r)),
    Step("up", "Up", "Look up (at the mirror)", "pitch", lambda f, r: _found(f) and _turned("pitch", -1, 8)(f, r)),
    Step("down", "Down", "Look down (at your lap)", "pitch", lambda f, r: _found(f) and _turned("pitch", 1, 10)(f, r)),
    Step("forward", "Forward", "Look straight at the road, eyes open", "yaw", lambda f, r: _found(f) and _forward(f, r)),
]


def _values(f: dict) -> dict:
    return {"ear": f.get("ear"), "mar": f.get("mar"), "yaw": f.get("yaw"), "pitch": f.get("pitch"),
            "roll": f.get("roll"), "gaze": f.get("gaze_raw"), "blink": _blink(f), "jaw": _jaw(f)}


def _median(rows: list, key: str) -> Optional[float]:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(vals) if vals else None


def build_profile(rec: dict, name: str) -> dict:
    """The driver profile from the recorded medians of each step."""
    g = lambda step, k: rec.get(step, {}).get(k)
    fwd = rec.get("forward", {})
    prof = {"name": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": rec,
            "ear_closed": g("eyes_closed", "ear"), "ear_open": g("eyes_open", "ear"),
            "mar_closed": g("mouth_closed", "mar"), "mar_open": g("mouth_open", "mar"),
            "road": {"yaw": fwd.get("yaw"), "pitch": fwd.get("pitch"), "roll": fwd.get("roll"),
                     "gaze": fwd.get("gaze")}}
    yl, yr, pu, pd = g("left", "yaw"), g("right", "yaw"), g("up", "pitch"), g("down", "pitch")
    y0, p0 = fwd.get("yaw"), fwd.get("pitch")
    if None not in (yl, yr, y0):
        # Off road at 60 % of the smaller deliberate turn, kept within 15-35 degrees.
        prof["yaw_limit"] = round(max(15.0, min(35.0, 0.6 * min(abs(yl - y0), abs(yr - y0)))), 1)
    if None not in (pu, pd, p0):
        prof["pitch_limit"] = round(max(10.0, min(30.0, 0.6 * min(abs(pu - p0), abs(pd - p0)))), 1)
    mc, mo = prof["mar_closed"], prof["mar_open"]
    if mc is not None and mo is not None and mo > mc:
        prof["yawn_mar"] = round(max(0.3, mc + 0.6 * (mo - mc)), 3)
    return prof


@dataclass
class TestPrompt:
    key: str
    label: str
    prompt: str
    expect: dict                      # what DRIFTS should classify: eyes, attention, mouth


TEST_PROMPTS = [
    TestPrompt("open", "Eyes open, road", "Look at the road with your eyes open", {"eyes": "open", "road": True, "mouth": False}),
    TestPrompt("closed", "Eyes closed", "Close your eyes", {"eyes": "closed", "road": False}),
    TestPrompt("left", "Look left", "Turn your head left", {"road": False}),
    TestPrompt("right", "Look right", "Turn your head right", {"road": False}),
    TestPrompt("down", "Look down", "Look down at your lap", {"road": False}),
    TestPrompt("yawn", "Mouth open", "Open your mouth wide", {"mouth": True}),
    TestPrompt("forward", "Back to the road", "Look at the road again", {"eyes": "open", "road": True, "mouth": False}),
]


def classify(f: dict, pose: dict, yawn_mar: float) -> dict:
    """What DRIFTS currently says about the driver, in the test's terms."""
    if not f.get("found"):
        return {"eyes": None, "road": None, "mouth": None}
    eyes = "closed" if f.get("eye_state") == "closed" else "open"
    att = pose.get("attention")
    road = None if att in (None, "unknown") else att == "on_road"
    m = f.get("mar")
    return {"eyes": eyes, "road": road, "mouth": None if m is None else m > yawn_mar}


@dataclass
class Guided:
    """Runs either sequence. mode: "calibrate" or "test"."""
    mode: str = "calibrate"
    name: str = "default"
    hold_s: float = 2.0
    step_timeout_s: float = 20.0
    test_s: float = 4.0
    settle_s: float = 1.0
    active: bool = False
    index: int = 0
    started: float = 0.0
    hold_since: Optional[float] = None
    rows: list = field(default_factory=list)
    recorded: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    profile: Optional[dict] = None
    report: Optional[dict] = None
    yawn_mar: float = 0.5

    @property
    def steps(self) -> list:
        return CAL_STEPS if self.mode == "calibrate" else TEST_PROMPTS

    def start(self, now: float, mode: str = "calibrate", name: Optional[str] = None, yawn_mar: float = 0.5) -> None:
        self.mode, self.active, self.index, self.started = mode, True, 0, now
        self.name = name or self.name
        self.hold_since, self.rows, self.recorded, self.skipped = None, [], {}, []
        self.results, self.profile, self.report, self.yawn_mar = {}, None, None, yawn_mar

    def cancel(self) -> None:
        self.active = False

    def _next(self, now: float) -> None:
        self.index += 1
        self.started, self.hold_since, self.rows = now, None, []
        if self.index >= len(self.steps):
            self.active = False
            if self.mode == "calibrate":
                self.profile = build_profile(self.recorded, self.name)
            else:
                self.report = self._report()

    def update(self, now: float, face: dict, pose: Optional[dict] = None) -> None:
        if not self.active:
            return
        if self.mode == "calibrate":
            self._update_cal(now, face)
        else:
            self._update_test(now, face, pose or {})

    def _update_cal(self, now: float, f: dict) -> None:
        step = self.steps[self.index]
        if step.ok(f, self.recorded):
            if self.hold_since is None:
                self.hold_since, self.rows = now, []
            self.rows.append(_values(f))
            if now - self.hold_since >= self.hold_s:
                self.recorded[step.key] = {k: _median(self.rows, k) for k in self.rows[0]}
                self._next(now)
        else:
            self.hold_since, self.rows = None, []
            if now - self.started > self.step_timeout_s:
                self.skipped.append(step.key)
                self._next(now)

    def _update_test(self, now: float, f: dict, pose: dict) -> None:
        p = self.steps[self.index]
        t = now - self.started
        if t >= self.settle_s:
            got = classify(f, pose, self.yawn_mar)
            r = self.results.setdefault(p.key, {"frames": 0, "no_face": 0, **{k: [0, 0] for k in p.expect}})
            r["frames"] += 1
            if got["eyes"] is None:
                r["no_face"] += 1
            for k, want in p.expect.items():
                if got[k] is not None:
                    r[k][1] += 1
                    r[k][0] += int(got[k] == want)
        if t >= self.test_s:
            self._next(now)

    def _report(self) -> dict:
        rows, hit, tot = [], 0, 0
        for p in TEST_PROMPTS:
            r = self.results.get(p.key, {"frames": 0, "no_face": 0})
            checks = {k: r.get(k, [0, 0]) for k in p.expect}
            ok = sum(v[0] for v in checks.values())
            n = sum(v[1] for v in checks.values())
            hit, tot = hit + ok, tot + n
            rows.append({"key": p.key, "label": p.label, "frames": r["frames"], "no_face": r["no_face"],
                         "accuracy": None if n == 0 else round(ok / n, 3),
                         "checks": {k: (None if v[1] == 0 else round(v[0] / v[1], 3)) for k, v in checks.items()}})
        return {"name": self.name, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "prompts": rows,
                "overall": None if tot == 0 else round(hit / tot, 3)}

    def snapshot(self, now: float, face: Optional[dict] = None) -> dict:
        """What the dev view's calibration strip shows."""
        steps = self.steps
        cur = steps[self.index] if self.active else None
        hold = 0.0 if self.hold_since is None else min(1.0, (now - self.hold_since) / self.hold_s)
        if self.active and self.mode == "test":
            hold = min(1.0, (now - self.started) / self.test_s)
        return {"mode": self.mode, "active": self.active, "index": self.index, "total": len(steps),
                "name": self.name, "prompt": cur.prompt if cur else None, "hold": round(hold, 2),
                "steps": [{"key": s.key, "label": s.label,
                           "metric": getattr(s, "metric", None),
                           "value": (self.recorded.get(s.key, {}).get(getattr(s, "metric", ""), None)
                                     if self.mode == "calibrate" else
                                     (next((r["accuracy"] for r in (self.report or {}).get("prompts", [])
                                            if r["key"] == s.key), None))),
                           "done": s.key in self.recorded if self.mode == "calibrate" else
                           (self.report is not None or i < self.index),
                           "skipped": s.key in self.skipped} for i, s in enumerate(steps)],
                "profile_saved": bool(self.profile), "report": self.report}


# ---------------------------------------------------------------- profiles on disk
def save_profile(prof: dict, directory: Path = PROFILE_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{prof['name']}.json"
    path.write_text(json.dumps(prof, indent=1))
    return path


def load_profile(name: str, directory: Path = PROFILE_DIR) -> Optional[dict]:
    try:
        return json.loads((directory / f"{name}.json").read_text())
    except (OSError, ValueError):
        return None


def save_report(report: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"accuracy_{report['name']}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1))
    return path
