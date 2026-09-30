"""Load config.toml into simple dataclasses for DRIFTS v2."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.toml"


@dataclass
class BleConfig:
    device_name: str = "DRIFTS-ESP32"
    address: str = ""
    reconnect_s: float = 3.0
    service_uuid: str = "DFT10001-B5A3-F393-E0A9-E50E24DCCA9E"
    wake_char_uuid: str = "DFT10002-B5A3-F393-E0A9-E50E24DCCA9E"
    result_char_uuid: str = "DFT10003-B5A3-F393-E0A9-E50E24DCCA9E"
    # The ESP32 kills its actuators if the Pi goes quiet for longer than
    # its FAILSAFE_MS (3 s). This must stay comfortably below that.
    heartbeat_s: float = 1.0


@dataclass
class CameraConfig:
    width: int = 640
    height: int = 480
    fps: int = 10


@dataclass
class DetectorConfig:
    model_param: str = "model/mobilenetv2_dms.param"
    model_bin: str = "model/mobilenetv2_dms.bin"
    model_onnx: str = "model/mobilenetv2_dms.onnx"
    input_size: int = 224
    conf_threshold: float = 0.5
    drowsy_frame_threshold: int = 3
    max_frames_per_cycle: int = 30


@dataclass
class PowerConfig:
    idle_camera_off: bool = True
    cooldown_s: float = 10.0


@dataclass
class Config:
    ble: BleConfig = field(default_factory=BleConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    power: PowerConfig = field(default_factory=PowerConfig)


def _fill(cls, data: dict):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown key(s) in [{cls.__name__}]: {', '.join(sorted(unknown))}")
    return cls(**data)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        return Config()
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cfg = Config()
    for section in fields(Config):
        if section.name in raw:
            section_cls = type(getattr(cfg, section.name))
            setattr(cfg, section.name, _fill(section_cls, raw[section.name]))
    return cfg
