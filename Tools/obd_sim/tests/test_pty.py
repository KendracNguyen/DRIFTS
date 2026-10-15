"""End-to-end test over a real pseudo-terminal.

Opens the slave path by name, exactly as `screen`, `minicom` or pyserial
would, and runs the ELMduino init dialogue plus a PID read through it. This is
what proves the transport, not just the protocol.

Skipped on platforms without openpty.
"""

from __future__ import annotations

import os
import time

import pytest

from obd_sim.elm327 import Elm327
from obd_sim.vehicle import Vehicle

pytest.importorskip("pty", reason="needs a POSIX pseudo-terminal")

from obd_sim.transports.pty import PtyTransport  # noqa: E402


def exchange(transport: PtyTransport, client_fd: int, elm: Elm327, command: bytes) -> str:
    """Write a command from the client side and pump until the prompt comes back."""
    os.write(client_fd, command)
    reply = bytearray()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        transport.poll(elm.feed, timeout=0.02)
        try:
            chunk = os.read(client_fd, 4096)
        except BlockingIOError:
            chunk = b""
        reply.extend(chunk)
        if b">" in reply:
            break
    return reply.decode("ascii", "ignore")


@pytest.fixture
def link():
    vehicle = Vehicle("normal", seed=3)
    vehicle.step(0.1)
    elm = Elm327(lambda: vehicle.state)
    transport = PtyTransport()
    client = os.open(transport.slave_path, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        yield transport, client, elm, vehicle
    finally:
        os.close(client)
        transport.close()


def test_slave_path_looks_like_a_serial_device(link):
    transport, _, _, _ = link
    assert transport.slave_path.startswith("/dev/")


def test_reset_answers_over_the_pty(link):
    transport, client, elm, _ = link
    assert "ELM327" in exchange(transport, client, elm, b"ATZ\r")


def test_full_elmduino_init_then_a_pid_read(link):
    transport, client, elm, vehicle = link
    for command in (b"ATD\r", b"ATZ\r", b"ATE0\r", b"ATS0\r", b"ATAL\r", b"ATST32\r", b"ATSP6\r"):
        reply = exchange(transport, client, elm, command)
        assert "?" not in reply, f"{command!r} rejected: {reply!r}"

    reply = exchange(transport, client, elm, b"010C\r").replace(" ", "")
    assert "410C" in reply
    payload = reply[reply.index("410C") + 4 :][:4]
    assert int(payload, 16) / 4 == pytest.approx(vehicle.state.rpm, abs=1.0)


def test_no_kernel_echo_on_the_master_side(link):
    """Raw mode matters: without it the client reads its own bytes back.

    That failure looks exactly like a dongle stuck in echo mode, and costs an
    hour to diagnose from the firmware side.
    """
    transport, client, elm, _ = link
    exchange(transport, client, elm, b"ATE0\r")
    reply = exchange(transport, client, elm, b"010D\r")
    assert reply.count("010D") == 0, f"command echoed back by the tty layer: {reply!r}"


def test_symlink_is_created_and_removed(tmp_path):
    target = tmp_path / "obd0"
    transport = PtyTransport(link=str(target))
    try:
        assert target.is_symlink()
        assert transport.path == str(target)
    finally:
        transport.close()
    assert not target.is_symlink()
