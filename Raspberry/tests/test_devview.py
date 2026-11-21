"""Dev-view close-ups draw from landmarks alone (no camera, no model)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts import devview, facesignals as fs
from drifts.mouth import CORNERS, UPPER_LOWER


def synthetic_face(w=640, h=480):
    """478 points around a plausible face: eyes, mouth and irises where they belong."""
    rng = np.random.default_rng(0)
    px = [(w / 2 + rng.uniform(-80, 80), h / 2 + rng.uniform(-100, 100)) for _ in range(478)]
    def eye(idx, cx, cy, iris):
        p1, p2, p3, p4, p5, p6 = idx
        px[p1], px[p4] = (cx - 30, cy), (cx + 30, cy)
        px[p2], px[p3] = (cx - 10, cy - 9), (cx + 10, cy - 9)
        px[p6], px[p5] = (cx - 10, cy + 9), (cx + 10, cy + 9)
        px[iris] = (cx, cy)
    eye(fs.EAR_RIGHT, 270, 200, fs.IRIS_RIGHT)
    eye(fs.EAR_LEFT, 370, 200, fs.IRIS_LEFT)
    px[CORNERS[0]], px[CORNERS[1]] = (290, 320), (350, 320)
    for k, (a, b) in enumerate(UPPER_LOWER):
        x = (320, 305, 335)[k]
        px[a], px[b] = (x, 312), (x, 330)
    px[fs.NOSE_TIP] = (320, 260)
    return px


def test_render_all_views():
    frame = np.full((480, 640, 3), 128, np.uint8)
    face = {"ear_r": 0.3, "mar": 0.3, "gaze_x": 0.02, "gaze_limit": 0.16, "gaze_off": False}
    v = devview.render(frame, synthetic_face(), face, mirrored=False)
    assert set(v) == {"eye", "mouth", "gaze", "face"}
    assert v["eye"].shape == (380, 620, 3) and v["mouth"].shape == (380, 620, 3)
    assert v["gaze"].shape[0] == 380
    # the landmark dots are drawn in the poster red (BGR 1A 20 D0)
    assert ((v["eye"] == devview.RED).all(axis=2)).sum() > 50


def test_mirrored_and_no_face():
    frame = np.zeros((480, 640, 3), np.uint8)
    assert devview.render(frame, None, {}, False) is None
    assert devview.render(frame, synthetic_face(), {}, mirrored=True) is not None


def test_mouth_labels_match_formula():
    # P1..P8 = 78 82 13 312 308 317 14 87: openings P2-P8, P3-P7, P4-P6 and width P1-P5
    m = devview.MOUTH_ORDER
    assert (m[0], m[4]) == CORNERS
    assert {(m[1], m[7]), (m[2], m[6]), (m[3], m[5])} == set(UPPER_LOWER)
