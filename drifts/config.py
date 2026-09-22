"""Load config.toml into simple dataclasses."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.toml"


@dataclass
class SourcesConfig:
    eyes: str = "sim"
    steering: str = "sim"
    loop_hz: float = 10.0


@dataclass
class FatigueConfig:
    warn_below: float = 70.0
    critical_below: float = 40.0
    hysteresis: float = 5.0


@dataclass
class ImuConfig:
    i2c_bus: int = 1
    address: int = 0x68
    window_s: float = 10.0
    full_scale_dps: float = 30.0


@dataclass
class BleConfig:
    enabled: bool = True
    device_name: str = "DRIFTS-ESP32"
    address: str = ""
    heartbeat_s: float = 1.0
    reconnect_s: float = 3.0


@dataclass
class AlertsConfig:
    ok: list[str] = field(default_factory=lambda: ["STOP"])
    warn: list[str] = field(default_factory=lambda: ["BUZZ 0 0 0", "VIB 160 400 600"])
    critical: list[str] = field(default_factory=lambda: ["VIB 255 500 300", "BUZZ 2700 300 300"])


@dataclass
class ApiConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 5000


@dataclass
class Config:
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    fatigue: FatigueConfig = field(default_factory=FatigueConfig)
    imu: ImuConfig = field(default_factory=ImuConfig)
    ble: BleConfig = field(default_factory=BleConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    api: ApiConfig = field(default_factory=ApiConfig)


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
