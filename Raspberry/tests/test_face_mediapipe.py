"""Pi 5 face backend adapter, against a fake dms-face with the same API shape
(FrameResult / StateReport / Config / DriverProfile, read from dms-face 7b32c63).
No MediaPipe, no camera."""

import sys
import textwrap
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from drifts.face_mediapipe import Pi5FaceSource, coco_from_mesh


class State(Enum):
    ALERT = "alert"
    DROWSY = "drowsy"


class EyeState(Enum):
    OPEN = 1
    CLOSED = 2


def mesh(mouth_open=0.0):
    px = np.zeros((478, 2))
    px[1] = (320, 260)                                   # nose tip
    px[362], px[263] = (350, 220), (380, 220)            # driver's left eye corners
    px[33], px[133] = (260, 220), (290, 220)             # driver's right eye corners
    px[454], px[234] = (420, 240), (220, 240)
    px[78], px[308] = (300, 300), (340, 300)             # mouth corners: width 40
    for up, lo, x in ((13, 14, 320), (82, 87, 310), (312, 317, 330)):
        px[up], px[lo] = (x, 300 - mouth_open / 2), (x, 300 + mouth_open / 2)
    return px


class FakePipeline:
    def __init__(self):
        self.mouth_open, self.face = 0.0, True
        self.perclos, self.state = 0.05, State.ALERT

    def process(self, rgb, t):
        assert rgb.shape[2] == 3
        rep = SimpleNamespace(state=self.state, reasons=["test"], perclos=self.perclos, perclos_valid=True,
                              perclos_warming=False, closure_ms=0.0, blink_rate=14.0, mean_blink_ms=220.0,
                              gaze_off_road_s=0.0, should_alert=self.state == State.DROWSY)
        if not self.face:
            return SimpleNamespace(face_present=False, landmarks=None, report=rep, angles=None, ear_filtered=None,
                                   openness_smooth=None, eye_state=EyeState.OPEN, eye_channel_valid=False,
                                   gaze_dev=None, inference_ms=9.0)
        return SimpleNamespace(face_present=True, landmarks=SimpleNamespace(pixels=mesh(self.mouth_open)),
                               report=rep, angles=(12.0, -3.0, 2.0), ear_filtered=0.28, openness_smooth=0.9,
                               eye_state=EyeState.OPEN, eye_channel_valid=True, gaze_dev=0.05, inference_ms=9.0)


def frame(colour=True):
    f = np.full((480, 640, 3), 110, np.uint8)
    if colour:
        f[..., 2] = 200
    return f


def test_coco_points_from_mesh():
    k = coco_from_mesh(mesh())
    assert k[0][:2] == (320.0, 260.0)
    assert k[1][:2] == (365.0, 220.0) and k[2][:2] == (275.0, 220.0)   # left eye on image right
    assert len(k) == 17 and k[5][2] == 0.0


def test_signals_mapping_and_profile_offsets():
    src = Pi5FaceSource(pipeline=FakePipeline())
    src.profile = SimpleNamespace(yaw0=2.0, pitch0=-1.0, roll0=0.0)
    s = src.process(frame(), 1.0)
    assert s["found"] and s["yaw"] == 10.0 and s["pitch"] == -2.0 and s["roll"] == 2.0
    assert s["ear"] == 0.28 and s["eye_state"] == "open" and s["blink_rate"] == 14.0
    assert s["perclos"] == 0.05 and src.eye_score() == 95
    assert s["state"] == "alert" and s["mar"] == 0.0 and s["yawns"] == 0
    assert src.keypoints is not None and src.inference_ms == 9.0


def test_yaw_sign_flip():
    src = Pi5FaceSource(pipeline=FakePipeline(), yaw_sign=-1)
    assert src.process(frame(), 1.0)["yaw"] == -12.0


def test_yawn_from_mesh():
    fp = FakePipeline()
    src = Pi5FaceSource(pipeline=fp)
    fp.mouth_open = 30.0                                  # MAR 0.75
    t = 0.0
    while t < 2.0:
        s = src.process(frame(), t)
        t += 0.1
    assert s["mar"] == 0.75 and s["yawning"] and s["yawns"] == 1


def test_night_mode_switches_and_enhances():
    src = Pi5FaceSource(pipeline=FakePipeline(), ir="auto")
    for t in range(6):
        s = src.process(frame(colour=False), float(t))     # grey IR-looking image
    assert s["night"] and s["ir_enhanced"]
    off = Pi5FaceSource(pipeline=FakePipeline(), ir="off")
    for t in range(6):
        s = off.process(frame(colour=False), float(t))
    assert s["night"] and not s["ir_enhanced"]


def test_no_face():
    fp = FakePipeline()
    fp.face = False
    src = Pi5FaceSource(pipeline=fp)
    s = src.process(frame(), 1.0)
    assert not s["found"] and s["yaw"] is None and src.keypoints is None and src.landmarks is None


def test_make_pipeline_wiring_with_fake_package(tmp_path):
    """The import path, Config fields and DriverProfile call match dms-face's API."""
    pkg = tmp_path / "dms_face"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "config.py").write_text(textwrap.dedent("""
        from dataclasses import dataclass, field
        @dataclass
        class CameraConfig:
            width: int = 640
            height: int = 480
        @dataclass
        class LandmarkerConfig:
            model_path: str = "models/face_landmarker.task"
        @dataclass
        class Config:
            camera: CameraConfig = field(default_factory=CameraConfig)
            landmarker: LandmarkerConfig = field(default_factory=LandmarkerConfig)
    """))
    (pkg / "calibration.py").write_text(textwrap.dedent("""
        class DriverProfile:
            def __init__(self, name="default"):
                self.name, self.yaw0, self.pitch0, self.roll0 = name, 0.0, 0.0, 0.0
            @classmethod
            def load_or_default(cls, name, directory="profiles"):
                return cls(name), False
    """))
    (pkg / "pipeline.py").write_text(textwrap.dedent("""
        class FacePipeline:
            def __init__(self, cfg, profile=None, width=None, height=None, camera_calib=None):
                self.cfg, self.profile, self.size = cfg, profile, (width, height)
    """))
    src = Pi5FaceSource(dms_dir=str(tmp_path), profile="kendra", size=(640, 480))
    src.start()
    assert src._pipe.size == (640, 480)
    assert src._pipe.cfg.landmarker.model_path == str(tmp_path / "models" / "face_landmarker.task")
    assert src.profile.name == "kendra" and src.profile_loaded is False
    for mod in [m for m in sys.modules if m.startswith("dms_face")]:
        del sys.modules[mod]
