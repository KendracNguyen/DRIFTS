"""ELM327 command interpreter.

Pure logic: bytes in, bytes out. No serial, no BLE, no clock. Everything here
is testable on a laptop, which is the point -- the parts that need hardware are
in `transports/`.

Framing follows the ELM327 datasheet as the real dongles implement it:
commands are terminated with CR (0x0D); replies are CR-separated lines
followed by CR and the '>' prompt. ELMduino waits for that prompt, so dropping
it hangs the firmware in `Initialising` with no error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .vehicle import VehicleState

ELM_VERSION = "ELM327 v1.5"
DEVICE_DESCRIPTION = "DRIFTS OBD SIMULATOR"
PROMPT = ">"

# Mode 01 PIDs this virtual ECU answers. Anything else returns NO DATA,
# which is the ordinary reply a real car gives and is counted, not an error.
PID_ENGINE_LOAD = 0x04
PID_RPM = 0x0C
PID_SPEED = 0x0D
PID_THROTTLE = 0x11

SUPPORTED_PIDS = (PID_ENGINE_LOAD, PID_RPM, PID_SPEED, PID_THROTTLE)

PROTOCOL_NAMES = {
    "0": "AUTO",
    "1": "SAE J1850 PWM",
    "2": "SAE J1850 VPW",
    "3": "ISO 9141-2",
    "4": "ISO 14230-4 KWP",
    "5": "ISO 14230-4 KWP",
    "6": "ISO 15765-4 CAN (11 bit ID, 500 kbaud)",
    "7": "ISO 15765-4 CAN (29 bit ID, 500 kbaud)",
    "8": "ISO 15765-4 CAN (11 bit ID, 250 kbaud)",
    "9": "ISO 15765-4 CAN (29 bit ID, 250 kbaud)",
    "A": "SAE J1939 CAN",
}


def supported_pid_bitmask(pids: tuple[int, ...] = SUPPORTED_PIDS) -> int:
    """Build the 32-bit response to PID 0x00 (`which PIDs 0x01-0x20 exist`).

    The MSB of the first byte is PID 0x01, so PID n sets bit (32 - n).
    """
    mask = 0
    for pid in pids:
        if 1 <= pid <= 0x20:
            mask |= 1 << (32 - pid)
    return mask


@dataclass
class ElmFlags:
    """The AT-settable state a real ELM327 keeps across commands."""

    echo: bool = True
    linefeeds: bool = False
    spaces: bool = True
    headers: bool = False
    protocol: str = "0"
    adaptive_timing: int = 1
    timeout_units: int = 0x32


class Elm327:
    """A virtual ELM327 sitting in front of a virtual ECU.

    `sample` is called once per PID request and must return the current
    `VehicleState`. It is a callable rather than a value so the transport can
    drive the vehicle model at its own rate.
    """

    def __init__(self, sample: Callable[[], VehicleState]) -> None:
        self._sample = sample
        self.flags = ElmFlags()
        self._rx = bytearray()
        self._last_command = ""
        self.request_count = 0
        self.no_data_count = 0

    # -- framing ---------------------------------------------------------
    def feed(self, data: bytes) -> bytes:
        """Accumulate incoming bytes; return the reply for each complete command.

        Returns b"" until a CR arrives. Several commands in one chunk are each
        answered, in order, in the returned buffer.
        """
        self._rx.extend(data)
        out = bytearray()
        while b"\r" in self._rx:
            raw, _, rest = bytes(self._rx).partition(b"\r")
            self._rx = bytearray(rest)
            out.extend(self._reply(raw.decode("ascii", "ignore")))
        return bytes(out)

    def _reply(self, raw: str) -> bytes:
        command = raw.strip()
        # An empty line repeats the previous command, as the datasheet says.
        if not command:
            command = self._last_command
        else:
            self._last_command = command

        body = self.handle(command)
        eol = "\r\n" if self.flags.linefeeds else "\r"

        parts = []
        if self.flags.echo and raw.strip():
            parts.append(raw.strip())
        if body:
            parts.extend(body.split("\n"))

        text = "".join(p + eol for p in parts) + eol + PROMPT
        return text.encode("ascii")

    # -- command dispatch -------------------------------------------------
    def handle(self, command: str) -> str:
        """Answer one command. Returns the body only, without echo or prompt."""
        tidy = command.replace(" ", "").upper()
        if not tidy:
            return ""
        if tidy.startswith("AT"):
            return self._handle_at(tidy[2:])
        return self._handle_obd(tidy)

    def _handle_at(self, arg: str) -> str:
        f = self.flags
        if arg in ("Z", "WS", "D"):
            if arg != "D":
                self.flags = ElmFlags()  # reset restores defaults, including echo
            return ELM_VERSION if arg in ("Z", "WS") else "OK"
        if arg == "I":
            return ELM_VERSION
        if arg == "@1":
            return DEVICE_DESCRIPTION
        if arg == "RV":
            return "13.8V"
        if arg in ("E0", "E1"):
            f.echo = arg.endswith("1")
            return "OK"
        if arg in ("L0", "L1"):
            f.linefeeds = arg.endswith("1")
            return "OK"
        if arg in ("S0", "S1"):
            f.spaces = arg.endswith("1")
            return "OK"
        if arg in ("H0", "H1"):
            f.headers = arg.endswith("1")
            return "OK"
        if arg in ("AL", "NL", "M0", "M1", "CAF0", "CAF1", "R0", "R1"):
            return "OK"
        if arg.startswith("AT") and len(arg) == 3 and arg[2].isdigit():
            f.adaptive_timing = int(arg[2])
            return "OK"
        if arg.startswith("ST") and len(arg) > 2:
            try:
                f.timeout_units = int(arg[2:], 16)
            except ValueError:
                return "?"
            return "OK"
        if arg.startswith("SP") and len(arg) >= 3:
            wanted = arg[-1]
            if wanted in PROTOCOL_NAMES:
                f.protocol = wanted
                return "OK"
            return "?"
        if arg == "DP":
            return PROTOCOL_NAMES.get(f.protocol, "AUTO")
        if arg == "DPN":
            return f.protocol
        return "?"

    def _handle_obd(self, arg: str) -> str:
        if len(arg) < 4 or not all(c in "0123456789ABCDEF" for c in arg):
            return "?"
        try:
            mode = int(arg[0:2], 16)
            pid = int(arg[2:4], 16)
        except ValueError:
            return "?"

        if mode != 0x01:
            # Modes 03, 09 and the rest are out of scope. A real dongle on a
            # car with no stored faults answers 03 with NO DATA too.
            self.no_data_count += 1
            return "NO DATA"

        self.request_count += 1

        if pid == 0x00:
            mask = supported_pid_bitmask()
            return self._frame(0x00, mask.to_bytes(4, "big"))

        if pid not in SUPPORTED_PIDS:
            self.no_data_count += 1
            return "NO DATA"

        state = self._sample()
        return self._frame(pid, self._encode(pid, state))

    # -- encoding ---------------------------------------------------------
    @staticmethod
    def _encode(pid: int, state: VehicleState) -> bytes:
        if pid == PID_RPM:
            # A*256 + B = RPM * 4
            raw = int(round(max(0.0, state.rpm) * 4))
            return min(0xFFFF, raw).to_bytes(2, "big")
        if pid == PID_SPEED:
            return bytes([int(round(max(0.0, min(255.0, state.speed_kph))))])
        if pid == PID_THROTTLE:
            return bytes([int(round(max(0.0, min(100.0, state.throttle_pct)) * 255 / 100))])
        if pid == PID_ENGINE_LOAD:
            return bytes([int(round(max(0.0, min(100.0, state.engine_load_pct)) * 255 / 100))])
        raise ValueError(f"no encoder for PID 0x{pid:02X}")

    def _frame(self, pid: int, payload: bytes) -> str:
        data = bytes([0x41, pid]) + payload
        sep = " " if self.flags.spaces else ""
        body = sep.join(f"{b:02X}" for b in data)
        if self.flags.headers:
            # 7E8 is the usual ECU response header for an 11-bit CAN reply.
            header = f"7E8{sep}{len(data):02X}{sep}"
            return header + body
        return body
