"""Load config.toml into simple dataclasses for DRIFTS v2."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.toml"


@dataclass
class BleConfig:
    device_name: str = "DRIFTS-ESP32"
    address: str = ""
    reconnect_s: float = 3.0
    service_uuid: str = "DF710001-B5A3-F393-E0A9-E50E24DCCA9E"
    wake_char_uuid: str = "DF710002-B5A3-F393-E0A9-E50E24DCCA9E"
    result_char_uuid: str = "DF710003-B5A3-F393-E0A9-E50E24DCCA9E"
    # ESP32 status JSON (1 Hz): score, level, OBD state. The Whisplay screen shows it.
    status_char_uuid: str = "DF720002-B5A3-F393-E0A9-E50E24DCCA9E"
    # The ESP32 kills its actuators if the Pi goes quiet for longer than
    # its FAILSAFE_MS (3 s). This must stay comfortably below that.
    heartbeat_s: float = 1.0


@dataclass
class LinkConfig:
    """How the Pi reaches the ESP32: "uart" (wired, firmware v21+) or "ble"."""
    kind: str = "uart"
    uart_port: str = "/dev/serial0"            # GPIO 14/15 (pins 8/10)
    uart_baud: int = 115200
    link_s: float = 5.0                        # ESP32 silent this long = link lost


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
class WhisplayConfig:
    """PiSugar Whisplay HAT: screen, RGB LED, speaker, button (drifts/whisplay_ui.py)."""
    enabled: bool = False
    driver_dir: str = "~/Whisplay/runtime"     # PiSugar's driver (install_driver.sh)
    audio_device: str = "plughw:CARD=whisplaysound"
    brightness: int = 70                       # 0-100
    voice: bool = True
    repeat_s: float = 60.0                     # Level 2 warning repeats this often
    stale_s: float = 5.0                       # ESP32 status older than this = link lost
    # The side button. Actions: next_page, prev_page, repeat_voice, calibrate, ai,
    # brightness, page:<name> (score, driving, vision, devices, setup, ai), none.
    # During an alert any short or double press acknowledges it (fixed).
    button_short: str = "next_page"
    button_long: str = "repeat_voice"
    button_double: str = "calibrate"
    long_s: float = 1.5                        # held this long = long press
    double_s: float = 0.4                      # second press within this = double press
    joystick: bool = False                     # "Joy" version: 5-way joystick pages through screens
    rotate: int = 0                            # 0 or 180 if the screen is upside down in the mount


@dataclass
class Config:
    link: LinkConfig = field(default_factory=LinkConfig)
    ble: BleConfig = field(default_factory=BleConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
    whisplay: WhisplayConfig = field(default_factory=WhisplayConfig)


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
    try:
        import tomllib                    # Python 3.11+
    except ModuleNotFoundError:           # older laptops: pip install tomli
        import tomli as tomllib
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cfg = Config()
    for section in fields(Config):
        if section.name in raw:
            section_cls = type(getattr(cfg, section.name))
            setattr(cfg, section.name, _fill(section_cls, raw[section.name]))
    return cfg
