"""Unit tests for MobileNetV2 driver drowsiness detector."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from drifts.config import DetectorConfig
from drifts.detector import DrowsinessDetector, ClassificationResult


def test_detector_init():
    cfg = DetectorConfig()
    detector = DrowsinessDetector(cfg)
    assert detector.classes == ["Open", "Closed", "Drowsy/Microsleep"]
    assert detector.backend in ("ncnn", "onnx", "fallback")


def test_detector_inference_on_sample_image():
    cfg = DetectorConfig()
    detector = DrowsinessDetector(cfg)

    # Use a real image from the dataset if available
    sample_img = (REPO_ROOT.parent / "Simuletic_DMS_Dataset" / "images" / "driver_no_sleep_f10.jpg")
    if not sample_img.exists():
        sample_img = REPO_ROOT / "Simuletic_DMS_Dataset" / "images" / "driver_no_sleep_f10.jpg"
    assert sample_img.exists(), f"Sample test image missing at {sample_img}"

    res = detector.predict(sample_img)
    assert isinstance(res, ClassificationResult)
    assert res.label in detector.classes
    assert 0.0 <= res.confidence <= 1.0
    assert "Open" in res.probabilities
    assert "Closed" in res.probabilities
    assert "Drowsy/Microsleep" in res.probabilities


def test_detector_is_drowsy_evaluation():
    cfg = DetectorConfig()
    detector = DrowsinessDetector(cfg)

    sleep_img = (REPO_ROOT.parent / "Simuletic_DMS_Dataset" / "images" / "driver_full_sleep_f50.jpg")
    if not sleep_img.exists():
        sleep_img = REPO_ROOT / "Simuletic_DMS_Dataset" / "images" / "driver_full_sleep_f50.jpg"
    assert sleep_img.exists()

    res = detector.predict(sleep_img)
    is_drowsy = detector.is_drowsy(res)
    assert isinstance(is_drowsy, bool)
