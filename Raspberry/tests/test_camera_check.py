"""Camera bring-up check (drifts/camera_check.py) and the Camera Module 3 focus (drifts/camera.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from drifts.camera import Camera
from drifts.camera_check import describe, main, software_checks
from drifts.config import CameraConfig, load_config
from drifts.whisplay_check import FAIL, PASS, WARN, Report

IMAGES = Path(__file__).resolve().parents[2] / "Simuletic_DMS_Dataset" / "images"


def test_names_the_camera():
    assert describe("imx708_noir") == "Camera Module 3 NoIR"
    assert describe("imx708_wide_noir") == "Camera Module 3 NoIR Wide"
    assert describe("ov5647") == "Camera Module 1 / OV5647"


def test_camera_detection_results():
    rep = Report()
    software_checks(rep, use_mesh=False, cams=[{"Model": "imx708_noir"}])
    got = {n: r for n, r, _ in rep.rows}
    assert got["Camera detected"] == PASS and got["NoIR (no IR filter)"] == PASS
    rep = Report()
    software_checks(rep, use_mesh=False, cams=[{"Model": "imx708"}])
    assert {n: r for n, r, _ in rep.rows}["NoIR (no IR filter)"] == WARN       # IR filter: no night vision
    rep = Report()
    software_checks(rep, use_mesh=False, cams=[])
    assert {n: r for n, r, _ in rep.rows}["Camera detected"] == FAIL
    rep = Report()
    software_checks(rep, use_mesh=False, cams=None)
    assert {n: r for n, r, _ in rep.rows}["picamera2"] == FAIL


class FakePicam:
    def __init__(self, af=True):
        self.camera_controls = {"AfMode": (0, 2, 0), "LensPosition": (0.0, 15.0, 1.0)} if af else {}
        self.set = []

    def set_controls(self, c):
        self.set.append(c)


def test_camera_module_3_focus_held_at_the_driver():
    cam = Camera(CameraConfig())
    cam._picam2 = FakePicam()
    assert cam._apply_focus().startswith("manual 1.7") and cam._picam2.set == [{"AfMode": 0, "LensPosition": 1.7}]
    cam = Camera(CameraConfig(focus="continuous"))
    cam._picam2 = FakePicam()
    assert cam._apply_focus() == "continuous" and cam._picam2.set == [{"AfMode": 2}]
    cam = Camera(CameraConfig())
    cam._picam2 = FakePicam(af=False)                                       # fixed-focus camera: untouched
    assert cam._apply_focus() is None and cam._picam2.set == []
    c = load_config().camera
    assert c.focus == "manual" and 1.0 <= c.lens_position <= 3.0             # config.toml: ~35-100 cm


@pytest.mark.skipif(not IMAGES.exists(), reason="sample images not in this checkout")
def test_live_check_on_sample_images_with_lcd_view(tmp_path):
    rc = main(["--video", str(IMAGES), "--no-mesh", "--lcd-sim", "--seconds", "2", "--out", str(tmp_path)])
    report = (tmp_path / "camera_check_report.txt").read_text()
    assert "Frame rate" in report and "Picture" in report and rc == 0
    assert (tmp_path / "camera_check_lcd.png").exists()
    assert not list(tmp_path.glob("*.jpg"))                                 # nothing saved without --snapshot


@pytest.mark.skipif(not IMAGES.exists(), reason="sample images not in this checkout")
def test_live_check_with_face_mesh(tmp_path):
    pytest.importorskip("mediapipe")
    main(["--video", str(IMAGES), "--lcd-sim", "--seconds", "3", "--out", str(tmp_path), "--snapshot"])
    report = (tmp_path / "camera_check_report.txt").read_text()
    line = next(l for l in report.splitlines() if "Face found" in l)
    assert line.startswith(("PASS", "WARN")), line
    assert (tmp_path / "camera_check_snapshot.jpg").exists()                # only with --snapshot
