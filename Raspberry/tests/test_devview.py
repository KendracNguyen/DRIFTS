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
    assert v["gaze"].shape == (380, 620, 3)
    # the landmark dots are drawn in the poster red (BGR 0B 12 E3)
    assert ((v["eye"] == devview.LABEL).all(axis=2)).sum() > 50


def test_mirrored_and_no_face():
    frame = np.zeros((480, 640, 3), np.uint8)
    assert devview.render(frame, None, {}, False) is None
    assert devview.render(frame, synthetic_face(), {}, mirrored=True) is not None


def test_mouth_labels_match_formula():
    # P1..P8 = 78 82 13 312 308 317 14 87: openings P2-P8, P3-P7, P4-P6 and width P1-P5
    m = devview.MOUTH_ORDER
    assert (m[0], m[4]) == CORNERS
    assert {(m[1], m[7]), (m[2], m[6]), (m[3], m[5])} == set(UPPER_LOWER)


def test_gaze_target_moves_and_turns_red_off_road():
    frame = np.full((480, 640, 3), 128, np.uint8)
    px = synthetic_face()
    on = devview.render(frame, px, {"gaze_x": 0.0, "gaze_limit": 0.16, "gaze_off": False}, False)["gaze"]
    off = devview.render(frame, px, {"gaze_x": 0.3, "gaze_limit": 0.16, "gaze_off": True}, False)["gaze"]
    red = lambda im: ((np.abs(im.astype(int) - devview.RED) < 20).all(axis=2))
    assert red(off).sum() > red(on).sum() + 500
    cols = np.where(red(off).any(axis=0))[0]
    assert cols.max() > 620 * 0.85                      # the target sits past the right of the road box


def test_dev_page_is_laptop_only(tmp_path):
    from types import SimpleNamespace
    from drifts.live import dev_allowed, is_raspberry_pi
    pi = tmp_path / "model"
    pi.write_bytes(b"Raspberry Pi Zero 2 W Rev 1.0\x00")
    laptop = tmp_path / "none"                           # laptops have no device-tree model file
    assert is_raspberry_pi(str(pi)) and not is_raspberry_pi(str(laptop))
    assert dev_allowed(SimpleNamespace(dev=True), str(pi)) is False
    assert dev_allowed(SimpleNamespace(dev=True), str(laptop)) is True
    assert dev_allowed(SimpleNamespace(dev=False), str(laptop)) is False
