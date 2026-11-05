"""Face-mesh signal maths (facesignals.py) and the mesh source's per-frame
signals (facemesh.FaceMeshSource.compute), with synthetic landmarks: no
camera, no MediaPipe."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from drifts import facesignals as fs
from drifts.facemesh import FaceMeshSource


def face_points(open_=1.0, mouth=0.0):
    """478 landmarks: eyes 60 px wide, lid gap 18 px x open_, mouth gap mouth x 40 px."""
    px = [(320.0, 240.0)] * 478
    px = list(px)

    def eye(idx, cx):
        gap = 9.0 * open_
        p1, p2, p3, p4, p5, p6 = idx
        px[p1], px[p4] = (cx - 30, 200.0), (cx + 30, 200.0)
        px[p2], px[p6] = (cx - 10, 200 - gap), (cx - 10, 200 + gap)
        px[p3], px[p5] = (cx + 10, 200 - gap), (cx + 10, 200 + gap)
    eye(fs.EAR_RIGHT, 260.0)
    eye(fs.EAR_LEFT, 380.0)
    # make the corner pairs used elsewhere consistent (33-133, 362-263)
    px[fs.IRIS_RIGHT], px[fs.IRIS_LEFT] = (260.0, 200.0), (380.0, 200.0)
    px[fs.IRIS_RIGHT + 1], px[fs.IRIS_LEFT + 1] = (266.0, 200.0), (386.0, 200.0)
    px[78], px[308] = (290.0, 320.0), (350.0, 320.0)
    for a, b, x in ((82, 87, 305.0), (13, 14, 320.0), (312, 317, 335.0)):
        px[a], px[b] = (x, 320 - 20 * mouth), (x, 320 + 20 * mouth)
    return px


def rot_matrix(yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0):
    """MediaPipe-style 4x4 from DRIFTS angles (inverse of angles_from_matrix)."""
    y, p, r = math.radians(-yaw_deg), math.radians(pitch_deg), math.radians(roll_deg)
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    rz = [[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rx = [[1, 0, 0], [0, cp, -sp], [0, sp, cp]]
    mul = lambda a, b: [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    m = mul(mul(rz, ry), rx)
    return [m[0] + [0], m[1] + [0], m[2] + [0], [0, 0, 0, 1]]


# ---------------------------------------------------------------- geometry
def test_ear_matches_the_formula():
    t = fs.ear_terms(face_points(1.0), fs.EAR_RIGHT)
    assert (t.v1, t.v2, t.h) == (18.0, 18.0, 60.0)
    assert t.ear == pytest.approx((18 + 18) / (2 * 60))
    assert fs.ear_terms(face_points(0.0), fs.EAR_LEFT).ear == 0.0
    assert fs.ear_terms(None, fs.EAR_LEFT) is None


@pytest.mark.parametrize("yaw,pitch,roll", [(0, 0, 0), (20, 0, 0), (-15, 10, 5), (5, -12, -20)])
def test_angles_round_trip(yaw, pitch, roll):
    got = fs.angles_from_matrix(rot_matrix(yaw, pitch, roll))
    assert got == pytest.approx((yaw, pitch, roll), abs=1e-6)


def test_nose_axis_points_where_the_head_turns():
    straight = fs.nose_axes(rot_matrix(), (100, 100), 50)
    assert straight["z"] == pytest.approx((100, 100))           # facing the camera: a dot
    assert straight["y"] == pytest.approx((100, 50))            # up the face = up the image
    right = fs.nose_axes(rot_matrix(30), (100, 100), 50)
    assert right["z"][0] < 100                                  # driver's right = image left (unmirrored)


def test_iris_offset():
    px = face_points()
    assert fs.iris_offset(px) == pytest.approx(0.0)
    px[fs.IRIS_RIGHT] = (275.0, 200.0)
    px[fs.IRIS_LEFT] = (395.0, 200.0)
    assert fs.iris_offset(px) == pytest.approx(0.25)


# ---------------------------------------------------------------- eyes
def run_eyes(tr, t0, t1, ear, dt=0.05):
    t = t0
    while t < t1:
        tr.update(t, ear)
        t += dt
    return t


def test_blinks_perclos_and_adaptive_baseline():
    tr = fs.EyeTracker()
    t = run_eyes(tr, 0.0, 12.0, 0.30)                  # open eyes, 12 s
    assert tr.ear_open == pytest.approx(0.30) and tr.openness == pytest.approx(1.0)
    assert tr.perclos(t) == pytest.approx(0.0)
    t = run_eyes(tr, t, t + 0.2, 0.08)                 # 200 ms closure
    t = run_eyes(tr, t, t + 1.0, 0.30)
    assert len(tr.blinks) == 1 and 150 <= tr.last_blink_ms <= 260
    t = run_eyes(tr, t, t + 3.0, 0.08)                 # 3 s closure: not a blink
    assert tr.closed_for(t) > 2.5 and tr.state() == "closed"
    t = run_eyes(tr, t, t + 0.5, 0.30)
    assert tr.long_closures == 1 and len(tr.blinks) == 1
    assert 0.15 < tr.perclos(t) < 0.35                 # ~3.2 s closed of ~16.7 s
    assert tr.blink_rate(t) == pytest.approx(60 / t, rel=0.05)


def test_perclos_waits_for_coverage_and_no_face_resets():
    tr = fs.EyeTracker()
    t = run_eyes(tr, 0.0, 5.0, 0.3)
    assert tr.perclos(t) is None                       # < 10 s of data
    assert tr.update(t, None) is None and tr.state() == "no_face"


def test_baseline_adapts_to_narrow_eyes():
    tr = fs.EyeTracker()
    run_eyes(tr, 0.0, 12.0, 0.20)                      # narrower eyes: still "open"
    assert tr.openness == pytest.approx(1.0) and tr.state() == "open"


# ---------------------------------------------------------------- head
def run_pitch(n, t0, t1, pitch, dt=0.1):
    t = t0
    while t < t1:
        n.update(t, pitch)
        t += dt
    return t


def test_nod_and_droop():
    n = fs.NodTracker()
    t = run_pitch(n, 0.0, 10.0, 5.0)                   # neutral 5 deg
    assert n.neutral == pytest.approx(5.0) and n.score(t) == 100
    t = run_pitch(n, t, t + 0.8, 22.0)                 # head drops 17 deg ...
    t = run_pitch(n, t, t + 1.0, 5.0)                  # ... and comes back: a nod
    assert len(n.nods) == 1 and n.score(t) == 80
    t = run_pitch(n, t, t + 4.0, 25.0)                 # stays down 4 s: droop
    assert n.droop_s(t) > 2.0 and n.score(t) < 80
    assert n.neutral == pytest.approx(5.0)             # the droop didn't move the neutral
    t = run_pitch(n, t, t + 1.0, 5.0)
    assert len(n.nods) == 1                            # a droop is not also a nod
    t = run_pitch(n, t, t + 61.0, 5.0)
    assert n.score(t) == 100                           # nods age out after a minute


# ---------------------------------------------------------------- glasses + fusion
def test_glasses_smoothing_hysteresis_and_override():
    g = fs.GlassesDetector()
    assert not g.update(0.0, 0.02)
    assert not g.update(0.1, 0.9)                      # one bright frame isn't enough
    t = 0.1
    while t < 3.0:
        t += 0.1
        g.update(t, 0.7)
    assert g.wearing()
    g.update(t + 0.1, 0.18)                            # between thresholds: stays on
    assert g.wearing()
    g.mode = "off"
    assert not g.wearing()
    g.mode = "on"
    assert g.wearing()


def test_fusion_leans_on_head_with_glasses():
    normal = fs.fuse(40.0, 100.0, glasses=False)
    glasses = fs.fuse(40.0, 100.0, glasses=True)
    assert normal["score"] == pytest.approx(0.8 * 40 + 0.2 * 100)
    assert glasses["score"] == pytest.approx(0.4 * 40 + 0.6 * 100)
    assert glasses["w_head"] > normal["w_head"]
    assert fs.fuse(None, 70.0, False)["score"] == 70.0
    assert fs.fuse(55.0, None, True)["score"] == 55.0
    assert fs.fuse(None, None, False)["score"] is None


# ---------------------------------------------------------------- the source, per frame
def test_mesh_source_signals_and_score():
    src = FaceMeshSource(landmarker=object())
    t = 0.0
    while t < 12.0:
        f = src.compute(t, face_points(1.0), rot_matrix(10, 3, -4), edge=0.01)
        t += 0.1
    assert f["found"] and f["yaw"] == pytest.approx(10) and f["roll"] == pytest.approx(-4)
    assert f["ear"] == pytest.approx(0.3) and f["eye_state"] == "open"
    assert f["perclos"] == pytest.approx(0.0) and f["face_score"] == pytest.approx(100)
    assert f["mar"] == pytest.approx(0.0) and not f["glasses"]
    assert src.eye_score() == 100
    while t < 15.0:                                    # eyes shut 3 s
        f = src.compute(t, face_points(0.0), rot_matrix(10, 3, -4), edge=0.01)
        t += 0.1
    assert f["eye_state"] == "closed" and f["closed_for"] > 2.5 and f["state"] == "drowsy"
    assert f["face_score"] < 100
    f = src.compute(t, None, None, edge=None)
    assert not f["found"] and f["state"] == "no_face"


def test_mesh_source_glasses_shift_weight():
    src = FaceMeshSource(landmarker=object(), glasses="on")
    f = src.compute(0.0, face_points(), rot_matrix(), edge=0.0)
    assert f["glasses"] and f["glasses_mode"] == "on"
    src.set_glasses_mode("bogus")
    assert src.glasses.mode == "on"
    src.set_glasses_mode("off")
    assert not src.compute(0.1, face_points(), rot_matrix(), edge=0.0)["glasses"]


# ---------------------------------------------------------------- composite of the eight
def calm(**kw):
    sig = {"found": True, "perclos": 0.0, "mean_blink_ms": 150, "closed_for": 0.0, "gaze_off_s": 0.0,
           "yaw_rel": 2.0, "roll_rel": 1.0, "nods": 0, "droop_s": 0.0, "neutral_ready": True,
           "mar": 0.05, "yawns": 0, "yawning": False}
    sig.update(kw)
    return sig


def test_weights_sum_to_one_and_cover_the_eight():
    for w in (fs.WEIGHTS, fs.WEIGHTS_GLASSES):
        assert set(w) == set(fs.SIGNALS) and sum(w.values()) == pytest.approx(1.0)
    assert sum(len(v) for v in fs.CLUSTERS.values()) == 8
    eyes = lambda w: sum(w[k] for k in fs.CLUSTERS["eyes"])
    head = lambda w: sum(w[k] for k in fs.CLUSTERS["head"])
    assert eyes(fs.WEIGHTS_GLASSES) < eyes(fs.WEIGHTS) and head(fs.WEIGHTS_GLASSES) > head(fs.WEIGHTS)


def test_composite_calm_is_zero_and_parts_add_up():
    c = fs.composite(calm(), glasses=False)
    assert c["score"] == pytest.approx(0.0)
    c = fs.composite(calm(perclos=0.15, yawns=3, nods=3), glasses=False)
    total = sum(p["points"] for p in c["parts"].values())
    assert c["score"] == pytest.approx(total)
    assert c["parts"]["ear"]["d"] == pytest.approx(0.5) and c["parts"]["yawn"]["d"] == 1.0
    assert c["score"] == pytest.approx(100 * (0.30 * 0.5 + 0.15 * 1.0 + 0.15 * 1.0))


def test_glasses_shift_the_score_toward_the_head():
    eyes_bad = calm(perclos=0.3)                       # only the eyes say drowsy
    head_bad = calm(nods=3)                            # only the head says drowsy
    assert fs.composite(eyes_bad, True)["score"] < fs.composite(eyes_bad, False)["score"]
    assert fs.composite(head_bad, True)["score"] > fs.composite(head_bad, False)["score"]


def test_composite_renormalises_missing_signals():
    c = fs.composite(calm(perclos=None, neutral_ready=False, yaw_rel=None, roll_rel=None), glasses=False)
    assert c["parts"]["ear"]["d"] is None and c["parts"]["pitch"]["d"] is None
    assert c["score"] == pytest.approx(0.0)
    c = fs.composite(calm(closed_for=2.0, perclos=None), glasses=False)
    assert c["score"] > 0                              # an eye closure counts even before PERCLOS is ready
    assert fs.composite({"found": False}, False)["score"] is None


def test_neutral_and_off_timer():
    n = fs.Neutral(min_samples=3)
    for t in range(5):
        n.update(t, 10.0)
    assert n.value == pytest.approx(10.0) and n.update(5, 40.0) == pytest.approx(30.0)
    for t in range(6, 20):
        n.update(t, 40.0)                              # a long turn doesn't become the neutral
    assert n.value == pytest.approx(10.0)
    o = fs.OffTimer()
    assert o.update(0, True) == 0 and o.update(2.5, True) == 2.5 and o.update(3, False) == 0


# ---------------------------------------------------------------- gaze: closed eyes + calibration
def run_src(src, t, t1, px, m, dt=0.1):
    f = None
    while t < t1:
        f = src.compute(t, px, m, edge=0.0)
        t += dt
    return t, f


def test_eyes_closed_counts_as_off_road():
    src = FaceMeshSource(landmarker=object())
    t, f = run_src(src, 0.0, 5.0, face_points(1.0), rot_matrix())
    assert not f["gaze_off"] and f["gaze_reason"] is None
    t, f = run_src(src, t, t + 0.5, face_points(0.0), rot_matrix())          # eyes shut, head still on road
    assert f["eyes_closed"] and f["gaze_off"] and f["gaze_reason"] == "eyes closed"
    t, f = run_src(src, t, t + 2.6, face_points(0.0), rot_matrix())
    assert f["gaze_off_s"] > 2.5
    t, f = run_src(src, t, t + 0.5, face_points(1.0), rot_matrix())          # open again: back on road
    assert not f["gaze_off"]


def test_iris_calibration_learns_the_road_position_from_open_eyes():
    src = FaceMeshSource(landmarker=object())
    off = face_points(1.0)
    off[fs.IRIS_RIGHT] = (266.0, 200.0)       # camera off to one side: road gaze reads +0.1
    off[fs.IRIS_LEFT] = (386.0, 200.0)
    t, f = run_src(src, 0.0, 2.0, face_points(0.0), rot_matrix())            # closed-eye frames: not learned
    assert not f["gaze_calibrated"] and f["gaze_calib_progress"] == 0
    t, f = run_src(src, t, t + 4.0, off, rot_matrix())
    assert f["gaze_calibrated"] and f["gaze_x"] == pytest.approx(0.0) and not f["gaze_off"]
    away = face_points(1.0)
    away[fs.IRIS_RIGHT] = (281.0, 200.0)      # 0.35 raw = 0.25 past the road position
    away[fs.IRIS_LEFT] = (401.0, 200.0)
    t, f = run_src(src, t, t + 0.3, away, rot_matrix())
    assert f["gaze_x"] == pytest.approx(0.25) and f["gaze_off"] and f["gaze_reason"] == "eyes looking away"
    src.calibrate(10)                          # Calibrate button
    t, f = run_src(src, t, t + 0.3, away, rot_matrix())
    assert not f["gaze_calibrated"]


def test_pipeline_road_pose_ignores_closed_eye_frames():
    from types import SimpleNamespace
    from drifts.headpose import AttentionTracker, Calibration
    from drifts.live import Pipeline
    fake = SimpleNamespace(cal=Calibration(auto_samples=5), attn=AttentionTracker(), pose={},
                           args=SimpleNamespace(gaze_dev=0.16, calib_frames=5),
                           camera=SimpleNamespace(profile_loaded=False, face={}))
    def frame(yaw, state, closed=False):
        fake.camera.face = {"found": True, "yaw": yaw, "pitch": 0.0, "roll": 0.0, "eye_state": state,
                            "eyes_closed": closed, "gaze_dev": 0.0}
        Pipeline._update_face_pose(fake, 0.0)
    for _ in range(5):
        frame(25.0, "closed", True)           # head drooped/turned with eyes shut: ignored
    assert not fake.cal.done
    for _ in range(5):
        frame(10.0, "open")
    assert fake.cal.done and fake.cal.yaw0 == pytest.approx(10.0)
    frame(10.0, "closed", True)
    assert fake.attn.state == "off_road"      # eyes closed -> off road even with the head on road
