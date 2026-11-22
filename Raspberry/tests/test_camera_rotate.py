"""[camera] rotate = 180: the picamera2 transform, and the OpenCV fallback flips the frame."""

import sys
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.camera import Camera
from drifts.config import CameraConfig, load_config


class FakePicam:
    def __init__(self): self.kw = None
    def create_video_configuration(self, **kw): self.kw = kw; return kw
    def configure(self, c): pass
    def start(self): pass
    camera_properties = {"Model": "imx708_noir"}
    camera_controls = {}


def run(rotate, monkeypatch):
    fake = FakePicam()
    monkeypatch.setitem(sys.modules, "picamera2", types.SimpleNamespace(Picamera2=lambda: fake))
    class T:
        def __init__(self, hflip=0, vflip=0): self.hflip, self.vflip = hflip, vflip
    monkeypatch.setitem(sys.modules, "libcamera", types.SimpleNamespace(Transform=T))
    Camera(CameraConfig(rotate=rotate)).start()
    return fake.kw


def test_picamera_transform(monkeypatch):
    t = run(180, monkeypatch).get("transform")
    assert t is not None and t.hflip == 1 and t.vflip == 1
    assert "transform" not in run(0, monkeypatch)
    assert "transform" not in run(90, monkeypatch)          # unsupported: left upright, with a warning


def test_opencv_fallback_flips():
    frame = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    cam = Camera(CameraConfig(rotate=180), use_picamera=False)
    cam._is_running = True
    cam._cv_cap = types.SimpleNamespace(read=lambda: (True, frame.copy()))
    assert (cam.grab() == frame[::-1, ::-1]).all()


def test_config_has_rotate():
    assert load_config().camera.rotate in (0, 180)
