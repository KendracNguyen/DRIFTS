"""Scenario tests.

These are the ones that matter. They simulate a full statistics window at the
PID rate the firmware actually achieves and assert that each anomaly crosses
the threshold it claims to target, and that normal driving does not.

If someone changes a threshold in config.h without changing the matching
scenario here, these fail -- which is the whole point. They are a tripwire
between the firmware constants and the bench tool, not a unit test of maths.
"""

from __future__ import annotations

import statistics

import pytest

from obd_sim.vehicle import (
    RPM_STDDEV_TRIGGER,
    SCENARIOS,
    SPEED_STDDEV_TRIGGER,
    STAT_WINDOW_S,
    THROTTLE_STDDEV_TRIGGER,
    Vehicle,
)

# ~2 Hz per PID is what a BLE ELM327 achieves once four PIDs share the link.
PID_HZ = 2.0
DT = 1.0 / PID_HZ
SAMPLES = int(STAT_WINDOW_S * PID_HZ)


def window(key: str, seed: int = 1) -> dict[str, list[float]]:
    v = Vehicle(key, seed=seed)
    out: dict[str, list[float]] = {"rpm": [], "speed": [], "throttle": []}
    for _ in range(SAMPLES):
        s = v.step(DT)
        out["rpm"].append(s.rpm)
        out["speed"].append(s.speed_kph)
        out["throttle"].append(s.throttle_pct)
    return out


def test_window_is_long_enough_to_be_meaningful():
    # MIN_SAMPLES_FOR_STAT is 12 in config.h.
    assert SAMPLES >= 12


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_normal_cruise_crosses_nothing(seed):
    w = window("normal", seed=seed)
    assert statistics.stdev(w["rpm"]) < RPM_STDDEV_TRIGGER
    assert statistics.stdev(w["speed"]) < SPEED_STDDEV_TRIGGER
    assert statistics.stdev(w["throttle"]) < THROTTLE_STDDEV_TRIGGER


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_rpm_scenario_crosses_the_rpm_threshold(seed):
    assert statistics.stdev(window("rpm", seed=seed)["rpm"]) > RPM_STDDEV_TRIGGER


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_throttle_scenario_crosses_the_throttle_threshold(seed):
    w = window("throttle", seed=seed)
    assert statistics.stdev(w["throttle"]) > THROTTLE_STDDEV_TRIGGER


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_speed_scenario_crosses_the_speed_threshold(seed):
    assert statistics.stdev(window("speed", seed=seed)["speed"]) > SPEED_STDDEV_TRIGGER


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_microsleep_crosses_all_three(seed):
    w = window("microsleep", seed=seed)
    assert statistics.stdev(w["rpm"]) > RPM_STDDEV_TRIGGER
    assert statistics.stdev(w["speed"]) > SPEED_STDDEV_TRIGGER
    assert statistics.stdev(w["throttle"]) > THROTTLE_STDDEV_TRIGGER


def test_idle_stays_parked():
    # The production speed gate exists to stop this scenario triggering.
    w = window("idle")
    assert max(w["speed"]) < 1.0


def test_braking_decelerates_then_recovers():
    v = Vehicle("braking", seed=1)
    speeds = [v.step(DT).speed_kph for _ in range(SAMPLES * 2)]
    assert min(speeds) < 20.0, "never actually slowed down"
    assert max(speeds) > 90.0, "never returned to cruise"


def test_predicted_sigma_matches_simulated_sigma():
    # The closed-form estimate in Scenario is what the thresholds were chosen
    # against; if the generator drifts from it, the estimate is misleading.
    for key in ("rpm", "speed", "throttle", "microsleep"):
        sc = SCENARIOS[key]
        w = window(key, seed=7)
        if sc.rpm_amp:
            assert statistics.stdev(w["rpm"]) == pytest.approx(
                sc.expected_rpm_sigma(), rel=0.3
            )
        if sc.speed_amp:
            assert statistics.stdev(w["speed"]) == pytest.approx(
                sc.expected_speed_sigma(), rel=0.3
            )


def test_every_scenario_documents_what_it_targets():
    for key, sc in SCENARIOS.items():
        assert sc.targets, f"{key} does not say which threshold it is built against"
        assert sc.description, f"{key} has no description"


def test_switching_scenario_does_not_crash_mid_run():
    v = Vehicle("normal", seed=1)
    for _ in range(10):
        v.step(DT)
    for key in SCENARIOS:
        v.select(key)
        for _ in range(5):
            v.step(DT)
