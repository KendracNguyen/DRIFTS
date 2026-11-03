"""Tokyo configuration: Raspberry Pi 5 pin map and output settings.

Tokyo is the single-board DRIFTS: a Raspberry Pi 5 does the camera, the
alert outputs, the LED strip, the LCD and the OBD dongle itself (the first
model splits this between an ESP32-C6 and a Pi Zero 2 W).

Pins are BCM GPIO numbers on the Pi 5's 40-pin header. Every value can be
overridden in tokyo.toml (see load_config).

    GPIO 12  motors     hardware PWM0 ch0  -> MOSFET gate (or module IN)
    GPIO 13  buzzer     hardware PWM0 ch1  -> NPN/MOSFET (active buzzer: on/off)
    GPIO 18  LED strip  RP1 PIO            -> 74AHCT125 -> WS2812B DIN
    GPIO 2/3 I2C1       SDA / SCL          -> level shifter -> LCD backpack
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class MotorConfig:
    pin: int = 12
    # 1.0 = full 5 V. The coin motors are 3 V parts: 0.6 of 5 V is ~3 V average.
    # Modules with their own driver: set to what the module expects (often 1.0).
    duty: float = 0.6
    pwm_hz: int = 20000          # above hearing, so the motors do not whine
    on_s: float = 0.5            # pulse pattern while Level 2 is active
    off_s: float = 0.3


@dataclass
class BuzzerConfig:
    pin: int = 13
    # "active": has its own oscillator (TMB12A03): just switch it on and off.
    # "passive": a plain piezo: the Pi plays a tone with hardware PWM.
    kind: str = "active"
    tone_hz: int = 2700          # passive only
    on_s: float = 1.0            # the DRIFTS alarm pattern: 1 s beep, 1 s pause
    off_s: float = 1.0


@dataclass
class StripConfig:
    pin: int = 18
    count: int = 160             # 3.2 ft, 160 LEDs (2.7 mm ultra narrow WS2812B)
    order: str = "GRB"
    # Full white on all 160 LEDs would draw ~9.6 A. Two limits keep it sane:
    brightness: float = 0.25     # global scale 0-1
    max_current_a: float = 1.5   # frames are dimmed further to stay under this
    status_leds: int = 8         # left end: system status; the rest: alert-score bar


@dataclass
class LcdConfig:
    enabled: bool = True
    bus: int = 1                 # /dev/i2c-1 = GPIO 2 (SDA) / GPIO 3 (SCL)
    addresses: tuple = (0x27, 0x3F)
    refresh_s: float = 0.25


@dataclass
class InterventionConfig:
    # Same bands as the first model (alertness.h): score 50-69 = Level 1, < 50 = Level 2.
    level1_below: float = 70.0
    level2_below: float = 50.0
    hysteresis: float = 5.0      # must recover this far above a band to drop a level
    min_hold_s: float = 3.0      # a level, once entered, stays at least this long


@dataclass
class Config:
    motors: MotorConfig = field(default_factory=MotorConfig)
    buzzer: BuzzerConfig = field(default_factory=BuzzerConfig)
    strip: StripConfig = field(default_factory=StripConfig)
    lcd: LcdConfig = field(default_factory=LcdConfig)
    interventions: InterventionConfig = field(default_factory=InterventionConfig)


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "tokyo.toml"


def load_config(path=None) -> Config:
    """Defaults, overridden by any [section] keys present in tokyo.toml."""
    cfg = Config()
    path = Path(path) if path else DEFAULT_PATH
    if not path.exists():
        return cfg
    try:
        import tomllib
    except ModuleNotFoundError:          # Python < 3.11
        import tomli as tomllib
    raw = tomllib.loads(path.read_text())
    for sec in fields(Config):
        if sec.name not in raw:
            continue
        obj = getattr(cfg, sec.name)
        known = {f.name for f in fields(obj)}
        unknown = set(raw[sec.name]) - known
        if unknown:
            raise ValueError(f"unknown key(s) in [{sec.name}]: {', '.join(sorted(unknown))}")
        for k, v in raw[sec.name].items():
            setattr(obj, k, tuple(v) if isinstance(v, list) else v)
    return cfg
