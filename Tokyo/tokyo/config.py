"""Tokyo configuration: Raspberry Pi 5 pin map and output settings.

Tokyo is the single-board DRIFTS: a Raspberry Pi 5 does the camera, the
alert outputs, the LED strip, the LCD and the OBD dongle itself (the first
model splits this between an ESP32-C6 and a Pi Zero 2 W).

Pins are BCM GPIO numbers on the Pi 5's 40-pin header, as wired in the
DRIFTS Rev 9 wiring sheet (with the fixes in Docs/wiring.md). Every value can
be overridden in tokyo.toml (see load_config).

    GPIO 13  motors     hardware PWM ch1   -> 100 R -> IN of all 4 motor modules (rev 4 style)
    GPIO 23  buzzer     on/off             -> Q5 low side, 3 V active buzzer from 3V3
    GPIO 10  LED strip  SPI0 MOSI          -> 74AHCT125 -> 330 R -> WS2812B DIN (14 LEDs)
    GPIO 12  IR LEDs    hardware PWM ch0   -> Q6 low side, 2 x 2 940 nm LEDs
    GPIO 2/3 I2C1       SDA / SCL          -> BSS138 level shifter -> LCD backpack
    GPIO 18/19/21 voice I2S (BCLK/LRC/DIN) -> MAX98357A amplifier -> 3 W speaker
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class MotorConfig:
    pin: int = 13
    # Four vibration motor modules (GND, VCC, IN; onboard driver), all IN pins
    # on this one pin through 100 R, 10 k to GND: the same as the first
    # model's rev 4 schematic. 1.0 = full on, as the ESP32 drives them.
    duty: float = 1.0
    pwm_hz: int = 20000          # above hearing, so the motors do not whine
    on_s: float = 0.5            # pulse pattern while Level 2 is active
    off_s: float = 0.3


@dataclass
class BuzzerConfig:
    pin: int = 23               # moved from Rev 9's GPIO 18: I2S audio needs 18
    # "active": has its own oscillator (3 V magnetic buzzer): just switch it on and off.
    # "passive": a plain piezo: the Pi plays a tone with hardware PWM.
    kind: str = "active"
    tone_hz: int = 2700          # passive only
    on_s: float = 1.0            # the DRIFTS alarm pattern: 1 s beep, 1 s pause
    off_s: float = 1.0


@dataclass
class StripConfig:
    # "spi": pi5neo on /dev/spidev0.0 (MOSI = GPIO 10), as in Rev 9.
    # "pio": Adafruit's RP1 PIO driver, any GPIO (set pin).
    backend: str = "spi"
    pin: int = 10
    spi_device: str = "/dev/spidev0.0"
    count: int = 14              # cut from the 160-LED 2.7 mm WS2812B strip, fits above the LCD
    order: str = "GRB"
    brightness: float = 0.25     # global scale 0-1 (the ESP32 uses 64/255)
    max_current_a: float = 1.5   # frames are dimmed further to stay under this


@dataclass
class IrConfig:
    pin: int = 12
    # "auto": on when the camera sees night (face pipeline), "on", or "off".
    mode: str = "auto"
    # 1.0 = steady on. Dimming uses 25 kHz PWM: slower PWM bands the rolling-shutter image.
    duty: float = 1.0
    pwm_hz: int = 25000


@dataclass
class VoiceConfig:
    enabled: bool = True
    # Clips generated from Voice/phrases.json (same ids as the ESP32's DFPlayer).
    clips_dir: str = ""          # default: <repo>/Voice/clips
    player: str = "aplay"        # aplay -q <clip.wav>; set device below if needed
    device: str = "plughw:CARD=MAX98357A,DEV=0"   # I2S amplifier (dtoverlay=max98357a); "" = system default
    repeat_s: float = 60.0       # Level 2 warning repeats this often
    link_lost_s: float = 5.0


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
    ir: IrConfig = field(default_factory=IrConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
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
