"""Guided calibration and accuracy test (drifts/guided.py), with synthetic frames."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.guided import CAL_STEPS, Guided, build_profile, classify, load_profile, save_profile


def frame(ear=0.30, mar=0.05, yaw=-4.0, pitch=8.0, roll=1.0, blink=0.05, jaw=0.02, gaze=0.03, found=True, **kw):
    return {"found": found, "ear": ear, "ear_open": 0.30, "mar": mar, "yaw": yaw, "pitch": pitch, "roll": roll,
            "gaze_raw": gaze, "blend": {"eyeBlinkLeft": blink, "eyeBlinkRight": blink, "jawOpen": jaw}, **kw}


POSES = {
    "eyes_closed": frame(ear=0.11, blink=0.85),
    "eyes_open": frame(ear=0.29),
    "mouth_closed": frame(mar=0.01),
    "mouth_open": frame(mar=0.62, jaw=0.7),
    "left": frame(yaw=-32.0),
    "right": frame(yaw=22.0),
    "up": frame(pitch=-6.0),
    "down": frame(pitch=24.0),
    "forward": frame(),
}


def run(g, poses, t0=0.0, dt=0.1, per_step=3.0):
    t = t0
    for key in [s.key for s in CAL_STEPS]:
        end = t + per_step
        while t < end and g.active:
            g.update(t, poses[key])
            t += dt
    return t


def test_full_calibration_builds_a_profile():
    g = Guided()
    g.start(0.0, "calibrate", name="kendra")
    run(g, POSES)
    assert not g.active and not g.skipped
    p = g.profile
    assert p["name"] == "kendra" and abs(p["ear_closed"] - 0.11) < 1e-6 and abs(p["ear_open"] - 0.29) < 1e-6
    assert abs(p["mar_open"] - 0.62) < 1e-6 and p["yawn_mar"] > p["mar_closed"]
    assert p["road"]["yaw"] == -4.0 and p["road"]["pitch"] == 8.0 and p["road"]["gaze"] == 0.03
    # off road at 60 % of the smaller deliberate turn (26 deg right of the road pose), within 15-35
    assert p["yaw_limit"] == round(0.6 * 26.0, 1)
    assert 10.0 <= p["pitch_limit"] <= 30.0


def test_a_pose_must_be_held_without_a_break():
    g = Guided(hold_s=2.0)
    g.start(0.0, "calibrate")
    for i in range(15):                                      # 1.5 s closed, then a peek
        g.update(i * 0.1, POSES["eyes_closed"])
    g.update(1.6, POSES["eyes_open"])
    assert g.index == 0 and g.hold_since is None
    for i in range(22):
        g.update(1.7 + i * 0.1, POSES["eyes_closed"])
    assert g.index == 1                                      # accepted after a full 2 s


def test_wrong_pose_times_out_and_is_skipped():
    g = Guided(step_timeout_s=5.0)
    g.start(0.0, "calibrate")
    for i in range(60):
        g.update(i * 0.1, POSES["eyes_open"])                # never closes the eyes
    assert "eyes_closed" in g.skipped and g.index >= 1


def test_no_face_never_counts():
    g = Guided(step_timeout_s=100)
    g.start(0.0, "calibrate")
    for i in range(50):
        g.update(i * 0.1, frame(found=False, ear=0.1, blink=0.9))
    assert g.index == 0


def test_profile_round_trip(tmp_path):
    prof = build_profile({"eyes_open": {"ear": 0.3}, "eyes_closed": {"ear": 0.1}}, "test_driver")
    path = save_profile(prof, tmp_path)
    assert path.name == "test_driver.json" and load_profile("test_driver", tmp_path)["ear_open"] == 0.3
    assert load_profile("nobody", tmp_path) is None


def test_accuracy_test_scores_each_prompt():
    g = Guided(test_s=3.0, settle_s=1.0)
    g.start(0.0, "test", name="kendra", yawn_mar=0.4)
    t = 0.0
    good = {"open": ({"eye_state": "open", "mar": 0.05}, "on_road"), "closed": ({"eye_state": "closed", "mar": 0.05}, "off_road"),
            "left": ({"eye_state": "open", "mar": 0.05}, "off_road"), "right": ({"eye_state": "open", "mar": 0.05}, "on_road"),
            "down": ({"eye_state": "open", "mar": 0.05}, "off_road"), "yawn": ({"eye_state": "open", "mar": 0.6}, "on_road"),
            "forward": ({"eye_state": "open", "mar": 0.05}, "on_road")}
    while g.active:
        key = g.steps[g.index].key
        f, att = good[key]
        g.update(t, {"found": True, **f}, {"attention": att})
        t += 0.1
    r = g.report
    by = {p["key"]: p for p in r["prompts"]}
    assert by["open"]["accuracy"] == 1.0 and by["closed"]["accuracy"] == 1.0
    assert by["right"]["accuracy"] == 0.0                    # said on road while turned right: a miss
    assert by["yawn"]["checks"]["mouth"] == 1.0
    assert 0.8 < r["overall"] < 1.0


def test_classify_without_a_face_is_unknown():
    assert classify({"found": False}, {}, 0.5) == {"eyes": None, "road": None, "mouth": None}
    assert classify({"found": True, "eye_state": "closed", "mar": 0.7}, {"attention": "off_road"}, 0.5) == \
        {"eyes": "closed", "road": False, "mouth": True}


def test_eye_tracker_uses_the_calibrated_scale():
    from drifts.facesignals import EyeTracker
    e = EyeTracker()
    assert e.set_calibration(0.30, 0.10)
    assert abs(e.update(0.0, 0.20) - 0.5) < 1e-6           # halfway between closed and open
    assert not e.set_calibration(0.11, 0.10)                # too close together: ignored
    assert e.fixed is None
