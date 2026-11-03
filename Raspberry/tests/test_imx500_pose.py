"""Pose sources without hardware: driver selection and the simulated head."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.headpose import estimate
from drifts.imx500_pose import SimPoseSource, pick_driver, synthetic_keypoints


def test_pick_driver_takes_the_largest_face():
    far = synthetic_keypoints(scale=0.5, cx=100, cy=100)       # passenger in the back
    near = synthetic_keypoints(scale=2.0, cx=320, cy=200)      # driver
    assert pick_driver([far, near]) is near
    assert pick_driver([[(0, 0, 0.0)] * 17]) is None


def test_synthetic_keypoints_round_trip():
    e = estimate(synthetic_keypoints(yaw_right=20, pitch_down=10, roll_right=-5))
    assert abs(e.yaw - 20) < 1 and abs(e.pitch - 10) < 1 and abs(e.roll + 5) < 1


def test_sim_source_produces_moving_keypoints():
    src = SimPoseSource(fps=50)
    src.start()
    yaws = []
    for _ in range(5):
        frame = src.grab()
        assert frame.shape == (480, 640, 3)
        yaws.append(estimate(src.keypoints).yaw)
    src.stop()
    assert all(y is not None for y in yaws)
