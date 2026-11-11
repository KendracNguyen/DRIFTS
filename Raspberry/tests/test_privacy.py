"""The phone app's privacy panel (/privacy.json) and the preview that is only
encoded while watched (drifts/live.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.esp_link import SimLink
from drifts.live import Pipeline, build_parser


class FakeFaceCamera:
    """Enough of FaceMeshSource for Pipeline.privacy()."""
    def __init__(self):
        self.face = {"found": True, "ear": 0.271234, "mar": 0.05, "yaw": 3.2, "pitch": -1.0, "roll": 0.5,
                     "blink_rate": 14.0, "perclos": 0.06, "eye_state": "open", "state": "alert",
                     "drowsiness": 12.0, "secret_image": b"\xff\xd8"}
        self.landmarks = [(320.0 + i % 10, 240.0 + i // 10) for i in range(478)]
        self.frame_size = (640, 480)

    def apply_profile(self, prof):
        pass


def make(tmp_path):
    args = build_parser().parse_args(["--esp", "sim", "--capture-dir", str(tmp_path / "cap"),
                                      "--trips", str(tmp_path / "trips.json"), "--profile", "nobody_here"])
    return Pipeline(FakeFaceCamera(), SimLink(), args)


def test_privacy_reports_numbers_points_and_nothing_saved(tmp_path):
    p = make(tmp_path)
    p.frames_analysed = 1234
    p.sent_log.append((100.0, "EYE:88"))
    p.sent_log.append((101.0, "DROWSY"))
    pv = p.privacy()
    m = pv["measures"]
    assert m["ear"] == 0.271 and m["state"] == "alert" and "secret_image" not in m      # numbers only
    assert len(pv["points"]) == 478 * 2 and all(0 <= v <= 1000 for v in pv["points"])  # positions, no pixels
    assert pv["frames_saved"] == 0 and pv["saved_dir"] is None and pv["uploads"] == 0
    assert [x["msg"] for x in pv["sent"]] == ["EYE:88", "DROWSY"]
    assert pv["frames_analysed"] == 1234 and pv["preview_open"] is False
    assert "jpg" not in str(pv).lower() and "image" not in pv


def test_training_capture_is_counted_as_saved(tmp_path):
    p = make(tmp_path)
    p.capture.counts["open"] = 3
    pv = p.privacy()
    assert pv["frames_saved"] == 3 and pv["saved_dir"]


def test_dev_view_is_gone():
    import drifts.facemesh as fm
    src = (Path(__file__).resolve().parents[1] / "drifts" / "live.py").read_text()
    for gone in ('"/dev"', "/face.sse", "/cam.mjpg", "/dev.mjpg", "/mesh.json", "/drowsy.json"):
        assert gone not in src, gone
    assert not hasattr(fm.FaceMeshSource, "render_dev") and not hasattr(fm.FaceMeshSource, "packet")
    assert not (Path(__file__).resolve().parents[1] / "drifts" / "devview.html").exists()
