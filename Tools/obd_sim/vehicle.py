"""Vehicle state model and the anomaly scenarios the trigger should catch.

Every scenario's amplitude is chosen against a threshold in
`Esp32/drifts_esp32/config.h`. If a threshold moves there, the matching
number here is wrong and `tests/test_vehicle.py` will say so.

The standard deviation of a sine of amplitude A is A/sqrt(2), so an amplitude
of 700 RPM gives sigma ~= 495, comfortably over RPM_STDDEV_TRIGGER = 400
without being so far over that a threshold change goes unnoticed.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# --- mirrored from Esp32/drifts_esp32/config.h ----------------------------
# Kept here so the scenarios can be checked against them in tests. These are
# COPIES, not the source of truth. config.h is the source of truth.
RPM_STDDEV_TRIGGER = 400.0
THROTTLE_STDDEV_TRIGGER = 15.0
SPEED_STDDEV_TRIGGER = 8.0
MIN_SPEED_KPH_PRODUCTION = 65.0
STAT_WINDOW_S = 30


@dataclass
class VehicleState:
    """One instantaneous sample. Units match the OBD-II PIDs that carry them."""

    rpm: float = 0.0
    speed_kph: float = 0.0
    throttle_pct: float = 0.0
    engine_load_pct: float = 0.0

    def clamped(self) -> "VehicleState":
        return VehicleState(
            rpm=max(0.0, min(8000.0, self.rpm)),
            speed_kph=max(0.0, min(255.0, self.speed_kph)),
            throttle_pct=max(0.0, min(100.0, self.throttle_pct)),
            engine_load_pct=max(0.0, min(100.0, self.engine_load_pct)),
        )


@dataclass
class Scenario:
    """A named driving condition.

    `base` is the steady-state operating point. Each `*_amp` is the amplitude
    of a slow sine added to it; each `*_noise` is the standard deviation of
    white noise on top. `period_s` is the sine period -- it must be a good deal
    shorter than STAT_WINDOW_S or the window sees a trend rather than a spread.
    """

    key: str
    label: str
    description: str
    targets: str  # which config.h threshold this is built to cross
    base: VehicleState
    rpm_amp: float = 0.0
    speed_amp: float = 0.0
    throttle_amp: float = 0.0
    period_s: float = 8.0
    rpm_noise: float = 25.0
    speed_noise: float = 0.4
    throttle_noise: float = 1.0
    decel_kph_s: float = 0.0  # sustained deceleration, for the braking case

    def expected_rpm_sigma(self) -> float:
        return math.sqrt((self.rpm_amp / math.sqrt(2)) ** 2 + self.rpm_noise**2)

    def expected_speed_sigma(self) -> float:
        return math.sqrt((self.speed_amp / math.sqrt(2)) ** 2 + self.speed_noise**2)

    def expected_throttle_sigma(self) -> float:
        return math.sqrt(
            (self.throttle_amp / math.sqrt(2)) ** 2 + self.throttle_noise**2
        )


_CRUISE = VehicleState(rpm=1850.0, speed_kph=105.0, throttle_pct=24.0, engine_load_pct=38.0)
_IDLE = VehicleState(rpm=780.0, speed_kph=0.0, throttle_pct=0.0, engine_load_pct=22.0)


SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario(
        key="normal",
        label="Normal cruise",
        description="Steady highway driving. Nothing should fire.",
        targets="none -- all statistics stay under threshold",
        base=_CRUISE,
        rpm_noise=55.0,
        speed_noise=1.2,
        throttle_noise=2.0,
    ),
    "idle": Scenario(
        key="idle",
        label="Parked, engine idling",
        description=(
            "Stationary with the engine running. In production the speed gate "
            "blocks every trigger here; with GATE_BENCH_MODE = true it does not."
        ),
        targets="MIN_SPEED_KPH gate (open in bench mode, shut in production)",
        base=_IDLE,
        rpm_noise=45.0,
        speed_noise=0.0,
        throttle_noise=0.5,
    ),
    "rpm": Scenario(
        key="rpm",
        label="RPM fluctuation",
        description="Engine speed hunting up and down -- the classic fatigue signature.",
        targets=f"RPM_STDDEV_TRIGGER = {RPM_STDDEV_TRIGGER:.0f}",
        base=_CRUISE,
        rpm_amp=700.0,
        period_s=7.0,
        rpm_noise=40.0,
    ),
    "throttle": Scenario(
        key="throttle",
        label="Throttle fluctuation",
        description="Pedal modulation with no steady foot. Speed stays roughly constant.",
        targets=f"THROTTLE_STDDEV_TRIGGER = {THROTTLE_STDDEV_TRIGGER:.0f}",
        base=_CRUISE,
        throttle_amp=26.0,
        rpm_amp=260.0,
        period_s=6.0,
        throttle_noise=1.5,
    ),
    "speed": Scenario(
        key="speed",
        label="Speed fluctuation",
        description="Drifting speed, the lane-keeping analogue the OBD port can see.",
        targets=f"SPEED_STDDEV_TRIGGER = {SPEED_STDDEV_TRIGGER:.0f}",
        base=_CRUISE,
        speed_amp=13.0,
        rpm_amp=230.0,
        period_s=9.0,
        speed_noise=0.6,
    ),
    "braking": Scenario(
        key="braking",
        label="Harsh braking",
        description=(
            "Sustained deceleration. NOTE: the firmware takes harsh braking from "
            "the MPU-6050, not from the speed PID -- PID 0x0D has 1 km/h "
            "resolution at ~2 Hz, which is ~0.06 g per step. This scenario "
            "exercises the speed path only; use the serial BRAKE command to "
            "inject a real brake event."
        ),
        targets="SPEED_STDDEV_TRIGGER, indirectly. Not the brake counter",
        base=_CRUISE,
        decel_kph_s=18.0,
        rpm_noise=70.0,
        speed_noise=0.5,
    ),
    "microsleep": Scenario(
        key="microsleep",
        label="Microsleep precursor",
        description=(
            "Everything degrading at once: hunting RPM, wandering throttle, "
            "drifting speed. The condition the whole system exists to catch."
        ),
        targets="all three standard-deviation thresholds simultaneously",
        base=_CRUISE,
        rpm_amp=760.0,
        throttle_amp=28.0,
        speed_amp=14.0,
        period_s=7.5,
        rpm_noise=60.0,
        speed_noise=0.9,
        throttle_noise=2.0,
    ),
}

DEFAULT_SCENARIO = "normal"


class Vehicle:
    """Advances a scenario in simulated time and reports the current sample.

    Time is passed in rather than read from the clock so tests are
    deterministic and so a scenario can be stepped faster than real time.
    """

    def __init__(self, scenario: str = DEFAULT_SCENARIO, seed: int | None = None) -> None:
        if scenario not in SCENARIOS:
            raise KeyError(f"unknown scenario {scenario!r}")
        self._scenario = SCENARIOS[scenario]
        self._rng = random.Random(seed)
        self._t = 0.0
        self._phase = 0.0
        self._decel_speed = self._scenario.base.speed_kph
        self._state = self._scenario.base.clamped()

    # -- control ---------------------------------------------------------
    @property
    def scenario(self) -> Scenario:
        return self._scenario

    def select(self, key: str) -> None:
        """Switch scenario, keeping the sine phase so the change is not a step."""
        if key not in SCENARIOS:
            raise KeyError(f"unknown scenario {key!r}")
        self._scenario = SCENARIOS[key]
        self._decel_speed = self._scenario.base.speed_kph
        self._t = 0.0

    # -- simulation ------------------------------------------------------
    def step(self, dt: float) -> VehicleState:
        s = self._scenario
        self._t += dt
        self._phase += 2.0 * math.pi * dt / max(0.1, s.period_s)
        w = self._phase

        if s.decel_kph_s > 0.0:
            self._decel_speed -= s.decel_kph_s * dt
            if self._decel_speed <= 5.0:  # stopped; roll back up to cruise
                self._decel_speed = s.base.speed_kph
            speed = self._decel_speed
            # Engine braking drags RPM down with road speed.
            rpm = s.base.rpm * (speed / max(1.0, s.base.speed_kph))
            throttle = 0.0
        else:
            speed = s.base.speed_kph + s.speed_amp * math.sin(w)
            rpm = s.base.rpm + s.rpm_amp * math.sin(w)
            throttle = s.base.throttle_pct + s.throttle_amp * math.sin(w + 0.4)

        self._state = VehicleState(
            rpm=rpm + self._rng.gauss(0.0, s.rpm_noise),
            speed_kph=speed + self._rng.gauss(0.0, s.speed_noise),
            throttle_pct=throttle + self._rng.gauss(0.0, s.throttle_noise),
            engine_load_pct=s.base.engine_load_pct + self._rng.gauss(0.0, 3.0),
        ).clamped()
        return self._state

    @property
    def state(self) -> VehicleState:
        return self._state
