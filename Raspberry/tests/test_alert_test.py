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


# --------------------------------------------------------------------------- #
# SerialHub: the ESP32 over USB, no Pi. Lines below are copied from the
# firmware's Serial.printf formats in actuators.cpp and drifts_esp32.ino.
# --------------------------------------------------------------------------- #
from drifts.alert_test import SerialHub


class FakeSerial:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data.decode())

    def readline(self):
        return b""

    def close(self):
        self.closed = True


def test_serial_hub_tracks_pin_test_from_firmware_log():
    hub = SerialHub("COM5", out=lambda *_: None, port=FakeSerial())
    assert hub.status() is None                      # nothing heard yet
    hub.on_line("[TEST] Pin test from serial")
    hub.on_line("[TEST] Motor GPIO 10: 2000 ms at duty 255/255 | Buzzer GPIO 11: 0 ms tone")
    assert hub.motor_on and not hub.buzzer_on and hub.status()["test"] is True
    hub.on_line("[TEST] Motor off")
    assert hub.status()["test"] is False
    hub.on_line("[TEST] Motor GPIO 10: 0 ms at duty 255/255 | Buzzer GPIO 11: 5000 ms steady HIGH (silent)")
    assert hub.buzzer_on and not hub.motor_on
    hub.on_line("[TEST] Buzzer off")
    assert hub.status()["test"] is False


def test_serial_hub_tracks_alert_state():
    hub = SerialHub("COM5", out=lambda *_: None, port=FakeSerial())
    hub.on_line("[MAIN] -> ALERTING")
    assert hub.status()["alert"] is True
    hub.on_line("[MAIN] -> IDLE (manual STOP)")
    assert hub.status()["alert"] is False


def test_serial_hub_sends_lines_and_skips_ping():
    port = FakeSerial()
    hub = SerialHub("COM5", out=lambda *_: None, port=port)
    asyncio.run(hub.send("PING"))
    asyncio.run(hub.send("TEST:3000:0"))
    asyncio.run(hub.close())
    assert port.written == ["TEST:3000:0\n", "STOP\n"]
    assert port.closed


def test_serial_session_end_to_end(tmp_path):
    """A full menu session over USB: the fake port answers like the firmware."""
    class EchoingSerial(FakeSerial):
        def __init__(self):
            super().__init__()
            self.pending = []

        def write(self, data):
            super().write(data)
            cmd = data.decode().strip()
            if cmd.startswith("TEST:"):
                m, b = cmd.split(":")[1:3]
                self.pending.append(f"[TEST] Motor GPIO 10: {m} ms at duty 255/255 | Buzzer GPIO 11: {b} ms tone")
            elif cmd == "DROWSY":
                self.pending.append("[MAIN] -> ALERTING")
            elif cmd in ("NOT_DROWSY", "STOP"):
                self.pending.append("[MAIN] -> IDLE (manual STOP)")

        def readline(self):
            return (self.pending.pop(0) + "\n").encode() if self.pending else b""

    answers = iter(["T", "1", "0", "", "y", "D", "y", "y", "N", "y", "Q"])

    async def ask(_prompt):
        return next(answers)

    port = EchoingSerial()
    hub = SerialHub("COM5", out=lambda *_: None, port=port)
    real_sleep = asyncio.sleep

    async def short_sleep(_s):
        await real_sleep(0.01)                      # let the reader task run

    asyncio.sleep = short_sleep
    try:
        asyncio.run(Session(hub, ask, tmp_path).run())
    finally:
        asyncio.sleep = real_sleep

    sent = [w.strip() for w in port.written]
    assert sent[0] == "STATUS"
    assert "TEST:1000:0" in sent and "DROWSY" in sent and "NOT_DROWSY" in sent
    assert sent[-1] == "STOP"                        # outputs stopped on quit
    log = next(tmp_path.glob("alert_test_*.log")).read_text()
    assert "hub: SerialHub" in log and "4 pass, 0 fail" in log


# --------------------------------------------------------------------------- #
# Eye score -> expected LEDs (mirrors alertness.h / status_leds.h)
# --------------------------------------------------------------------------- #
from drifts.alert_test import expected_alert, expected_leds


def test_expected_led_bands_match_firmware():
    assert expected_alert(100) == 100
    assert round(expected_alert(57)) == 70              # 0.7*57 + 30 = 69.9 -> boundary
    assert expected_leds(70) == "red and yellow off (OK)"
    assert expected_leds(69.9).startswith("YELLOW solid")
    assert expected_leds(50).startswith("YELLOW solid")
    assert expected_leds(49.9).startswith("RED fast blink")


def test_eye_option_sends_eye_message(tmp_path):
    answers = iter(["E", "30", "y", "Q"])

    async def ask(_prompt):
        return next(answers)

    async def no_sleep(_s):
        return None

    lines = []
    hub = SimHub(out=lines.append)
    sent = []
    orig = hub.send

    async def spy(msg):
        sent.append(msg)
        await orig(msg)

    hub.send = spy
    real_sleep = asyncio.sleep
    asyncio.sleep = no_sleep
    try:
        asyncio.run(Session(hub, ask, tmp_path).run())
    finally:
        asyncio.sleep = real_sleep
    assert "EYE:30" in sent
    assert any("alert score 51" in l and "YELLOW solid" in l for l in lines)


def test_menu_box_lines_are_aligned():
    from drifts.alert_test import MENU
    box = [l for l in MENU.splitlines() if l.strip().startswith(("┌", "│", "└"))]
    assert len({len(l) for l in box}) == 1
