"""Buzzer and vibration motors on Raspberry Pi 5 GPIO.

The Pi's pins give 3.3 V and only a few milliamps, so neither load connects
straight to a pin (see Docs/wiring.md):
  motors -> logic-level MOSFET + flyback diode (or driver modules: pin -> IN)
  buzzer -> NPN transistor or MOSFET + diode (the TMB12A03 is a magnetic coil)

Motors use the Pi 5's hardware PWM through the kernel's sysfs interface
(enable with `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4` in
/boot/firmware/config.txt). If it is not available, they fall back to
gpiozero software PWM. RPi.GPIO does not work on the Pi 5 and is not used.

Every driver has a `sim` mode that only records what it would do, so the
logic runs and is tested on a laptop.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from tokyo.config import BuzzerConfig, MotorConfig

PWM_SYSFS = Path("/sys/class/pwm")
# GPIO -> hardware PWM channel on the Pi 5 (RP1 PWM0), with the overlay above.
PI5_PWM_CHANNEL = {12: 0, 13: 1, 18: 2, 19: 3}


class SysfsPwm:
    """One kernel PWM channel: /sys/class/pwm/pwmchipN/pwmM."""

    def __init__(self, gpio: int, root: Path = PWM_SYSFS, chip: Optional[str] = None):
        if gpio not in PI5_PWM_CHANNEL:
            raise ValueError(f"GPIO {gpio} has no hardware PWM on the Pi 5 (use 12, 13, 18 or 19)")
        self.channel = PI5_PWM_CHANNEL[gpio]
        self.root = Path(root)
        self.chip = self.root / chip if chip else self._find_chip()
        self.dir = self.chip / f"pwm{self.channel}"
        if not self.dir.exists():
            (self.chip / "export").write_text(str(self.channel))
            for _ in range(50):                  # udev needs a moment to create it
                if self.dir.exists():
                    break
                time.sleep(0.02)
        self.period_ns = 0

    def _find_chip(self) -> Path:
        # The RP1 PWM0 block: the chip with at least 4 channels (number varies by kernel).
        for chip in sorted(self.root.glob("pwmchip*")):
            try:
                if int((chip / "npwm").read_text()) >= 4:
                    return chip
            except (OSError, ValueError):
                continue
        raise FileNotFoundError("no hardware PWM chip: add dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4 "
                                "to /boot/firmware/config.txt and reboot")

    def _w(self, name: str, value) -> None:
        (self.dir / name).write_text(str(value))

    def set(self, hz: float, duty: float) -> None:
        duty = max(0.0, min(1.0, duty))
        period = int(1e9 / hz)
        if period != self.period_ns:
            self._w("duty_cycle", 0)             # duty must never exceed the period
            self._w("period", period)
            self.period_ns = period
        self._w("duty_cycle", int(period * duty))
        self._w("enable", 1 if duty > 0 else 0)

    def off(self) -> None:
        if self.period_ns:
            self._w("duty_cycle", 0)
        self._w("enable", 0)


class _Recorder:
    """Sim backend: remembers the last command."""

    def __init__(self):
        self.hz, self.duty, self.history = 0.0, 0.0, []

    def set(self, hz, duty):
        self.hz, self.duty = hz, duty
        self.history.append(("set", hz, round(duty, 3)))

    def off(self):
        self.duty = 0.0
        self.history.append(("off",))


def _gpiozero_pwm(pin: int, hz: int):
    from gpiozero import PWMOutputDevice
    dev = PWMOutputDevice(pin, frequency=hz, initial_value=0)

    class _Soft:
        def set(self, hz_, duty):
            dev.frequency = hz_
            dev.value = duty

        def off(self):
            dev.value = 0
    return _Soft()


def make_pwm(pin: int, hz: int, sim: bool):
    if sim:
        return _Recorder()
    try:
        return SysfsPwm(pin)
    except (FileNotFoundError, ValueError, PermissionError, OSError):
        return _gpiozero_pwm(pin, hz)


class Motors:
    """All four motors on one driver: on/off at a duty, or a pulse pattern."""

    def __init__(self, cfg: MotorConfig, sim: bool = False):
        self.cfg = cfg
        self.pwm = make_pwm(cfg.pin, cfg.pwm_hz, sim)
        self.on = False

    def set(self, on: bool, duty: Optional[float] = None) -> None:
        if on:
            self.pwm.set(self.cfg.pwm_hz, self.cfg.duty if duty is None else duty)
        else:
            self.pwm.off()
        self.on = on


class Buzzer:
    """Active buzzer: switched on/off. Passive piezo: hardware-PWM tone."""

    def __init__(self, cfg: BuzzerConfig, sim: bool = False):
        self.cfg, self.sim, self.on = cfg, sim, False
        if cfg.kind == "passive":
            self.pwm = make_pwm(cfg.pin, cfg.tone_hz, sim)
            self.dev = None
        elif cfg.kind == "active":
            self.pwm = None
            if sim:
                self.dev = _Recorder()
            else:
                from gpiozero import OutputDevice
                self.dev = OutputDevice(cfg.pin, initial_value=False)
        else:
            raise ValueError("buzzer kind must be 'active' or 'passive'")

    def set(self, on: bool) -> None:
        if self.pwm is not None:
            self.pwm.set(self.cfg.tone_hz, 0.5) if on else self.pwm.off()
        elif isinstance(self.dev, _Recorder):
            self.dev.set(0, 1.0 if on else 0.0)
        else:
            self.dev.on() if on else self.dev.off()
        self.on = on
