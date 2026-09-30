"""Unit tests for DRIFTS configuration loader."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from drifts.config import Config, load_config


def test_default_config():
    cfg = Config()
    assert cfg.ble.device_name == "DRIFTS-ESP32"
    assert cfg.camera.width == 640
    assert cfg.camera.height == 480
    assert cfg.detector.input_size == 224
    assert cfg.power.idle_camera_off is True


def test_load_repo_config():
    cfg = load_config()
    assert cfg.ble.device_name == "DRIFTS-ESP32"
    assert cfg.camera.fps == 10
    assert cfg.detector.drowsy_frame_threshold == 3
    assert cfg.detector.max_frames_per_cycle == 30
    assert cfg.power.cooldown_s == 10
