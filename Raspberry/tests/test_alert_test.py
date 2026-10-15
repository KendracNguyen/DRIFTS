"""Tests for the alert-chain bench tool. No BLE, no hardware."""

import asyncio

import pytest

from drifts.alert_test import (
    MAX_TEST_S,
    Session,
    SimHub,
    make_test_command,
    parse_seconds,
)


def test_make_test_command_matches_firmware_format():
    assert make_test_command(3, 2) == "TEST:3000:2000"
    assert make_test_command(0.5, 0) == "TEST:500:0"
    assert make_test_command(1, 1, buzzer_dc=True) == "TEST:1000:1000:DC"


def test_make_test_command_fits_esp32_result_buffer():
    # ble_hub.cpp RESULT_MAX is 24 bytes including the terminator.
    assert len(make_test_command(MAX_TEST_S, MAX_TEST_S, True)) < 24


@pytest.mark.parametrize("bad", [-1, MAX_TEST_S + 0.1])
def test_make_test_command_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        make_test_command(bad, 1)
    with pytest.raises(ValueError):
        make_test_command(1, bad)


def test_parse_seconds():
    assert parse_seconds("", 3.0) == 3.0
    assert parse_seconds(" 2.5 ", 3.0) == 2.5
    assert parse_seconds("0", 3.0) == 0
    for bad in ("-1", "21", "abc"):
        with pytest.raises(ValueError):
            parse_seconds(bad, 3.0)


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_sim_hub_test_timing_and_stop():
    clock, lines = FakeClock(), []
    hub = SimHub(clock=clock, out=lines.append)
    asyncio.run(hub.send("TEST:2000:500"))
    assert hub.status()["test"] is True
    clock.t += 1.0
    assert hub.status()["test"] is True          # motor still on
    clock.t += 1.5
    assert hub.status()["test"] is False         # both expired
    assert any("GPIO 10 HIGH for 2000 ms" in l for l in lines)


def test_sim_hub_drowsy_then_not_drowsy():
    clock = FakeClock()
    hub = SimHub(clock=clock, out=lambda *_: None)
    asyncio.run(hub.send("DROWSY"))
    assert hub.status()["alert"] is True
    clock.t += 21
    assert hub.status()["alert"] is False        # 20 s cap
    asyncio.run(hub.send("DROWSY"))
    asyncio.run(hub.send("NOT_DROWSY"))
    assert hub.status()["alert"] is False


def test_session_records_results_and_saves(tmp_path):
    answers = iter(["T", "2", "1", "d", "y", "n", "Q"])

    async def ask(_prompt):
        return next(answers)

    async def no_sleep(_s):
        return None

    hub = SimHub(out=lambda *_: None)
    sent = []
    orig_send = hub.send

    async def spy(msg):
        sent.append(msg)
        await orig_send(msg)

    hub.send = spy
    session = Session(hub, ask, tmp_path)
    real_sleep = asyncio.sleep
    asyncio.sleep = no_sleep
    try:
        asyncio.run(session.run())
    finally:
        asyncio.sleep = real_sleep

    assert "TEST:2000:1000:DC" in sent
    assert [a for _, _, a in session.results] == ["PASS", "FAIL"]
    logs = list(tmp_path.glob("alert_test_*.log"))
    assert len(logs) == 1
    assert "1 pass, 1 fail" in logs[0].read_text()
