"""Fatigue inputs: eyes (PERCLOS) and steering, each 0-100.

Every source has a `read() -> float` method. "sim" sources let the whole app
(and the ESP32 link) run on a laptop with no camera or IMU attached.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque

from .config import Config

log = logging.getLogger(__name__)

SIM_PERIOD_S = 120.0  # one full OK -> WARN -> CRITICAL -> OK cycle


def _sim_phase(t0: float) -> float:
    """0..1..0 over SIM_PERIOD_S, starting at 0 (fully alert)."""
    t = (time.monotonic() - t0) % SIM_PERIOD_S
    return 0.5 - 0.5 * math.cos(2 * math.pi * t / SIM_PERIOD_S)


class SimEyes:
    """Fake PERCLOS that drifts from 5% to 75% and back every 2 minutes."""

    def __init__(self):
        self.t0 = time.monotonic()

    def read(self) -> float:
        return 5.0 + 70.0 * _sim_phase(self.t0)


class SimSteering:
    """Fake steering value that drifts from 10 to 60 in step with SimEyes."""

    def __init__(self):
        self.t0 = time.monotonic()

    def read(self) -> float:
        return 10.0 + 50.0 * _sim_phase(self.t0)


class CameraEyes:
    """PERCLOS from the OV5647 via picamera2. Not built yet."""

    def __init__(self, cfg: Config):
        raise NotImplementedError(
            "Camera PERCLOS pipeline isn't built yet. "
            "Set [sources] eyes = \"sim\" in config.toml for now."
        )

    def read(self) -> float:  # pragma: no cover
        raise NotImplementedError


class ImuSteering:
    """Steering activity from the MPU6050 yaw rate (gyro Z).

    Takes the RMS of the yaw rate over a rolling window and scales it to 0-100,
    where `full_scale_dps` maps to 100. Keep the IMU still for ~1 s at start-up
    while it measures the gyro bias.
    """

    PWR_MGMT_1 = 0x6B
    GYRO_CONFIG = 0x1B
    GYRO_ZOUT_H = 0x47
    LSB_PER_DPS = 131.0  # +/-250 deg/s range

    def __init__(self, cfg: Config):
        from smbus2 import SMBus  # imported here so laptops don't need I2C

        c = cfg.imu
        self.addr = c.address
        self.full_scale = c.full_scale_dps
        self.bus = SMBus(c.i2c_bus)
        self.bus.write_byte_data(self.addr, self.PWR_MGMT_1, 0x00)   # wake up
        self.bus.write_byte_data(self.addr, self.GYRO_CONFIG, 0x00)  # +/-250 dps
        time.sleep(0.1)

        n = max(1, int(c.window_s * cfg.sources.loop_hz))
        self.samples: deque[float] = deque(maxlen=n)
        self.bias = self._calibrate()
        log.info("MPU6050 ready at 0x%02X, gyro Z bias %.2f dps", self.addr, self.bias)

    def _raw_dps(self) -> float:
        hi, lo = self.bus.read_i2c_block_data(self.addr, self.GYRO_ZOUT_H, 2)
        value = (hi << 8) | lo
        if value & 0x8000:
            value -= 0x10000
        return value / self.LSB_PER_DPS

    def _calibrate(self, n: int = 100) -> float:
        total = 0.0
        for _ in range(n):
            total += self._raw_dps()
            time.sleep(0.01)
        return total / n

    def read(self) -> float:
        self.samples.append(self._raw_dps() - self.bias)
        rms = math.sqrt(sum(s * s for s in self.samples) / len(self.samples))
        return min(100.0, 100.0 * rms / self.full_scale)


def build_sources(cfg: Config, force_sim: bool = False):
    eyes_kind = "sim" if force_sim else cfg.sources.eyes
    steer_kind = "sim" if force_sim else cfg.sources.steering

    eyes = {"sim": lambda: SimEyes(), "camera": lambda: CameraEyes(cfg)}.get(eyes_kind)
    steer = {"sim": lambda: SimSteering(), "imu": lambda: ImuSteering(cfg)}.get(steer_kind)
    if eyes is None:
        raise ValueError(f"Unknown eyes source {eyes_kind!r} (use 'sim' or 'camera')")
    if steer is None:
        raise ValueError(f"Unknown steering source {steer_kind!r} (use 'sim' or 'imu')")

    log.info("Sources: eyes=%s steering=%s", eyes_kind, steer_kind)
    return eyes(), steer()
