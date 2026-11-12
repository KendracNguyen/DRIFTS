"""The OpenCV face engine (drifts/face_cv.py): MediaPipe's face models without MediaPipe,
for CPUs without AES (Pi Zero 2 W) where MediaPipe 1.x crashes."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from drifts import face_cv
from drifts.face_cv import _anchors, has_aes

MODEL = Path(__file__).resolve().parents[1] / "models" / "face_landmarker.task"
IMAGES = Path(__file__).resolve().parents[2] / "Simuletic_DMS_Dataset" / "images"
PI_ZERO_2W = "processor\t: 0\nBogoMIPS\t: 38.40\nFeatures\t: fp asimd evtstrm crc32 cpuid\nCPU part\t: 0xd03\n"
PI_5 = "processor\t: 0\nFeatures\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid\n"


def test_aes_detection():
    assert has_aes(PI_ZERO_2W) is False and has_aes(PI_5) is True
    assert has_aes("flags : fpu vme sse aes avx\n") is True              # x86 says "flags"
    assert has_aes("model name : something\n") is None


def test_engine_choice(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(face_cv, "has_aes", lambda text=None: False)
    assert face_cv.needs_opencv_engine()                                  # Pi Zero 2 W: OpenCV
    monkeypatch.setattr(face_cv, "has_aes", lambda text=None: True)
    assert not face_cv.needs_opencv_engine()                              # Pi 5: MediaPipe
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(face_cv, "has_aes", lambda text=None: False)
    assert not face_cv.needs_opencv_engine()                              # laptops: MediaPipe


def test_blazeface_anchors():
    a = _anchors()
    assert len(a) == 896 and a[0] == (0.5 / 16, 0.5 / 16) and a[-1] == (7.5 / 8, 7.5 / 8)


def _engine():
    cv2 = pytest.importorskip("cv2")
    if not MODEL.exists():
        pytest.skip("face model not downloaded")
    try:
        return face_cv.CvFaceLandmarker(MODEL)
    except RuntimeError as exc:                                           # OpenCV 5 drops the outputs
        pytest.skip(f"OpenCV {cv2.__version__}: {exc}")


@pytest.mark.skipif(not IMAGES.exists(), reason="sample images not in this checkout")
def test_finds_and_tracks_a_face_on_sample_images():
    import cv2
    from drifts import facesignals as fs
    lm = _engine()
    files = sorted(IMAGES.glob("driver_no_sleep_f*.jpg"))[:8]
    found = 0
    for f in files:
        rgb = cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2RGB)
        px, m = lm.detect(rgb)
        if px is None:
            continue
        found += 1
        assert len(px) == 478
        h, w = rgb.shape[:2]
        assert all(0 <= x <= w and 0 <= y <= h for x, y in (px[33], px[263], px[1]))
        ear = (fs.ear_terms(px, fs.EAR_LEFT).ear + fs.ear_terms(px, fs.EAR_RIGHT).ear) / 2
        assert 0.0 < ear < 0.5
        yaw, pitch, roll = fs.angles_from_matrix(m)
        assert all(abs(a) < 60 for a in (yaw, pitch, roll))
    assert found >= 6
    assert lm.roi is not None                                             # tracking: next crop from the points
    lm.detect(cv2.cvtColor(cv2.imread(str(files[-1])), cv2.COLOR_BGR2RGB))
    assert not lm.ran_detector                                            # tracked frame skips the detector


def test_no_face_in_a_blank_frame():
    import numpy as np
    lm = _engine()
    assert lm.detect(np.zeros((480, 640, 3), np.uint8)) == (None, None)


def test_matrix_reads_like_mediapipe():
    """A face turned to the driver's left reads as positive yaw, like MediaPipe's matrix."""
    import numpy as np
    from drifts import facesignals as fs
    lm = face_cv.CvFaceLandmarker.__new__(face_cv.CvFaceLandmarker)
    lm.np = np
    xy = np.zeros((478, 2))
    z = np.zeros(478)
    xy[face_cv.R_EYE], xy[face_cv.L_EYE] = (100, 100), (200, 100)
    xy[face_cv.FOREHEAD], xy[face_cv.CHIN] = (150, 40), (150, 220)
    flat = fs.angles_from_matrix(lm._matrix(xy, z))
    assert abs(flat[0]) < 1 and abs(flat[2]) < 1 and abs(flat[1] - face_cv.PITCH_OFFSET_DEG) < 1.5
    z[face_cv.L_EYE], z[face_cv.R_EYE] = 30, -30                          # left eye further away
    turned = fs.angles_from_matrix(lm._matrix(xy, z))
    assert abs(turned[0] - flat[0]) > 10
