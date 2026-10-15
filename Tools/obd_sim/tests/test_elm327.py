"""ELM327 protocol tests. No hardware, no serial port, no clock."""

from __future__ import annotations

import pytest

from obd_sim.elm327 import PROMPT, Elm327, supported_pid_bitmask
from obd_sim.vehicle import VehicleState

SAMPLE = VehicleState(rpm=1850.0, speed_kph=105.0, throttle_pct=24.0, engine_load_pct=38.0)


@pytest.fixture
def elm() -> Elm327:
    e = Elm327(lambda: SAMPLE)
    e.feed(b"ATE0\r")  # echo off, as ELMduino does first
    return e


def text(raw: bytes) -> str:
    return raw.decode("ascii")


# -- framing ---------------------------------------------------------------
def test_no_reply_until_carriage_return():
    e = Elm327(lambda: SAMPLE)
    assert e.feed(b"ATZ") == b""
    assert b"ELM327" in e.feed(b"\r")


def test_reply_ends_with_prompt(elm):
    assert text(elm.feed(b"ATI\r")).endswith(PROMPT)


def test_byte_at_a_time_is_identical_to_one_chunk(elm):
    whole = elm.feed(b"010C\r")
    piecewise = b"".join(elm.feed(bytes([b])) for b in b"010C\r")
    assert whole == piecewise


def test_two_commands_in_one_chunk_get_two_replies(elm):
    out = text(elm.feed(b"010C\r010D\r"))
    assert out.count(PROMPT) == 2
    assert "410C" in out.replace(" ", "")
    assert "410D" in out.replace(" ", "")


def test_echo_on_repeats_the_command():
    e = Elm327(lambda: SAMPLE)  # defaults to echo on, like a real reset
    assert "ATI" in text(e.feed(b"ATI\r"))


def test_echo_off_suppresses_it(elm):
    assert "ATI" not in text(elm.feed(b"ATI\r"))


def test_empty_line_repeats_last_command(elm):
    elm.feed(b"010C\r")
    before = elm.request_count
    elm.feed(b"\r")
    assert elm.request_count == before + 1


# -- AT commands -----------------------------------------------------------
def test_reset_restores_echo(elm):
    assert elm.flags.echo is False
    elm.feed(b"ATZ\r")
    assert elm.flags.echo is True


def test_elmduino_init_sequence_all_answer_ok(elm):
    # What ELMduino's begin() actually sends, in order.
    for command in (b"ATD\r", b"ATE0\r", b"ATS0\r", b"ATAL\r", b"ATST32\r", b"ATSP6\r"):
        reply = text(elm.feed(command))
        assert "?" not in reply, f"{command!r} was rejected"
        assert "OK" in reply or "ELM327" in reply


def test_set_protocol_is_remembered(elm):
    elm.feed(b"ATSP6\r")
    assert elm.flags.protocol == "6"
    assert "CAN" in text(elm.feed(b"ATDP\r"))


def test_unknown_at_command_returns_question_mark(elm):
    assert "?" in text(elm.feed(b"ATQQQ\r"))


def test_spaces_off_removes_them(elm):
    elm.feed(b"ATS1\r")
    assert " " in text(elm.feed(b"010C\r"))
    elm.feed(b"ATS0\r")
    body = text(elm.feed(b"010C\r")).strip().strip(PROMPT).strip()
    assert " " not in body


def test_headers_on_adds_the_ecu_header(elm):
    elm.feed(b"ATH1\r")
    assert "7E8" in text(elm.feed(b"010C\r"))


# -- OBD mode 01 -----------------------------------------------------------
def test_supported_pid_bitmask_sets_the_right_bits():
    # PIDs 0x04, 0x0C, 0x0D, 0x11 -> 10 18 80 00
    assert supported_pid_bitmask().to_bytes(4, "big").hex().upper() == "10188000"


def test_pid_00_advertises_the_four_we_serve(elm):
    body = text(elm.feed(b"0100\r")).replace(" ", "")
    assert "410010188000" in body


def test_rpm_encoding_round_trips(elm):
    body = text(elm.feed(b"010C\r")).replace(" ", "")
    payload = body[body.index("410C") + 4 :][:4]
    assert int(payload, 16) / 4 == pytest.approx(SAMPLE.rpm, abs=0.25)


def test_speed_encoding_round_trips(elm):
    body = text(elm.feed(b"010D\r")).replace(" ", "")
    payload = body[body.index("410D") + 4 :][:2]
    assert int(payload, 16) == round(SAMPLE.speed_kph)


def test_throttle_encoding_round_trips(elm):
    body = text(elm.feed(b"0111\r")).replace(" ", "")
    payload = body[body.index("4111") + 4 :][:2]
    assert int(payload, 16) * 100 / 255 == pytest.approx(SAMPLE.throttle_pct, abs=0.4)


def test_engine_load_encoding_round_trips(elm):
    body = text(elm.feed(b"0104\r")).replace(" ", "")
    payload = body[body.index("4104") + 4 :][:2]
    assert int(payload, 16) * 100 / 255 == pytest.approx(SAMPLE.engine_load_pct, abs=0.4)


def test_unsupported_pid_returns_no_data(elm):
    assert "NO DATA" in text(elm.feed(b"010F\r"))
    assert elm.no_data_count == 1


def test_non_mode_01_returns_no_data(elm):
    assert "NO DATA" in text(elm.feed(b"0902\r"))


def test_malformed_request_returns_question_mark(elm):
    assert "?" in text(elm.feed(b"01ZZ\r"))


def test_rpm_is_clamped_at_the_two_byte_ceiling():
    e = Elm327(lambda: VehicleState(rpm=99999.0))
    e.feed(b"ATE0\r")
    body = text(e.feed(b"010C\r")).replace(" ", "")
    payload = body[body.index("410C") + 4 :][:4]
    assert payload == "FFFF"
