"""Head pose from COCO face keypoints: project a synthetic head at known angles
and check the estimate recovers them."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.headpose import AttentionTracker, Calibration, HeadPose, estimate

# Head model in mm, camera's view: x right in the image, y down, z toward the
# camera. The driver's right eye/ear appear on the image LEFT.
MODEL = {
    "nose": (0.0, 40.0, 35.0),
    "l_eye": (31.5, 0.0, 0.0), "r_eye": (-31.5, 0.0, 0.0),
    "l_ear": (75.0, 10.0, -90.0), "r_ear": (-75.0, 10.0, -90.0),
}


def keypoints(yaw_right=0.0, pitch_down=0.0, roll_right=0.0, scale=1.6, cx=320, cy=200, hide=()):
    """17 COCO keypoints (x, y, score) for a head at these angles (degrees)."""
    a = math.radians(-yaw_right)        # model rotation: + a turns the face to image right
    p = math.radians(pitch_down)
    q = math.radians(-roll_right)
    out = []
    for name in ("nose", "l_eye", "r_eye", "l_ear", "r_ear"):
        x, y, z = MODEL[name]
        x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)   # yaw
        y, z = y * math.cos(p) + z * math.sin(p), -y * math.sin(p) + z * math.cos(p)   # pitch
        x, y = x * math.cos(q) - y * math.sin(q), x * math.sin(q) + y * math.cos(q)    # roll
        out.append((cx + scale * x, cy + scale * y, 0.0 if name in hide else 0.9))
    return out + [(0.0, 0.0, 0.0)] * 12      # body keypoints unused


def near(a, b, tol):
    return abs(a - b) <= tol, f"{a:.1f} vs {b:.1f}"


def test_straight_ahead_is_zero():
    pose = estimate(keypoints())
    assert pose.found and not pose.profile
    for v in (pose.yaw, pose.pitch, pose.roll):
        assert abs(v) < 0.5


def test_yaw_recovered():
    for yaw in (-40, -20, -5, 10, 25, 45):
        ok, msg = near(estimate(keypoints(yaw_right=yaw)).yaw, yaw, 0.5)
        assert ok, f"yaw {yaw}: {msg}"


def test_roll_recovered():
    for roll in (-25, -10, 15, 30):
        ok, msg = near(estimate(keypoints(roll_right=roll)).roll, roll, 0.5)
        assert ok, f"roll {roll}: {msg}"


def test_pitch_recovered():
    for pitch in (-20, -10, 10, 20, 30):
        ok, msg = near(estimate(keypoints(pitch_down=pitch)).pitch, pitch, 1.0)
        assert ok, f"pitch {pitch}: {msg}"


def test_combined_angles_within_rough_tolerance():
    pose = estimate(keypoints(yaw_right=20, pitch_down=15, roll_right=10))
    assert abs(pose.yaw - 20) < 4 and abs(pose.roll - 10) < 4 and abs(pose.pitch - 15) < 6


def test_roll_does_not_leak_into_yaw_or_pitch():
    pose = estimate(keypoints(roll_right=25))
    assert abs(pose.yaw) < 0.5 and abs(pose.pitch) < 1.0


def test_calibration_removes_camera_offset():
    # Camera mounted off to the side: "looking at the road" reads as 25 deg yaw, 8 deg down.
    cal = Calibration(auto_samples=10)
    for _ in range(10):
        cal.add(estimate(keypoints(yaw_right=25, pitch_down=8)))     # camera-relative
    assert cal.done
    pose = estimate(keypoints(yaw_right=25, pitch_down=8), cal)
    assert abs(pose.yaw) < 0.5 and abs(pose.pitch) < 0.5 and abs(pose.roll) < 0.5
    turned = estimate(keypoints(yaw_right=55, pitch_down=8), cal)
    assert abs(turned.yaw - 30) < 1.0          # 30 deg further right than the road pose
    assert abs(turned.pitch) < 1.0


def test_profile_when_an_eye_is_hidden():
    right = estimate(keypoints(yaw_right=70, hide=("r_eye", "r_ear")))
    left = estimate(keypoints(yaw_right=-70, hide=("l_eye", "l_ear")))
    assert right.profile and right.yaw > 45
    assert left.profile and left.yaw < -45


def test_no_face():
    assert estimate([(0, 0, 0.0)] * 17).found is False
    assert estimate(None).found is False


def test_attention_glance_counting():
    t = AttentionTracker(yaw_limit=30, glance_s=2.0)
    on = HeadPose(found=True, yaw=5, pitch=0, roll=0)
    off = HeadPose(found=True, yaw=45, pitch=0, roll=0)
    assert t.update(0.0, on) == "on_road"
    t.update(1.0, off)
    assert t.state == "off_road" and t.events == 0
    t.update(2.5, off)
    assert t.off_for(2.5) == 1.5 and not t.distracted(2.5)
    t.update(3.1, off)
    assert t.distracted(3.1) and t.events == 1
    t.update(4.0, off)
    assert t.events == 1                     # one event per glance
    t.update(4.5, on)
    assert t.state == "on_road" and t.off_for(4.5) == 0.0


def test_attention_hysteresis_and_pitch():
    t = AttentionTracker(yaw_limit=30, pitch_down=20, hysteresis=5)
    t.update(0, HeadPose(found=True, yaw=0, pitch=25, roll=0))        # looking down
    assert t.state == "off_road"
    t.update(1, HeadPose(found=True, yaw=0, pitch=17, roll=0))        # inside, but not by 5 deg
    assert t.state == "off_road"
    t.update(2, HeadPose(found=True, yaw=0, pitch=14, roll=0))
    assert t.state == "on_road"


def test_attention_unknown_without_face_holds_timer():
    t = AttentionTracker()
    t.update(0, HeadPose(found=True, yaw=50, pitch=0, roll=0))
    assert t.update(1, HeadPose(found=False)) == "unknown"
    assert t.off_for(1) == 0.0


def test_full_grid_with_ears_visible():
    # Turned + nodded at once is ambiguous from 3 points; the ears resolve it.
    worst = 0.0
    for y in range(-50, 51, 10):
        for p in range(-20, 31, 10):
            for r in range(-20, 21, 10):
                e = estimate(keypoints(y, p, r))
                worst = max(worst, abs(e.yaw - y), abs(e.pitch - p), abs(e.roll - r))
    assert worst < 1.5


def test_tracking_resolves_ambiguity_without_ears():
    prev, worst = None, 0.0
    for k in range(51):                       # glance down-left toward the console
        y, p = -k, 0.6 * k
        e = estimate(keypoints(y, p, 0, hide=("l_ear", "r_ear")), prev=prev)
        prev = e
        worst = max(worst, abs(e.yaw - y), abs(e.pitch - p))
    assert worst < 1.5
