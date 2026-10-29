"""Live link to the DRIFTS ESP32 for the dashboard: telemetry in, EYE scores out.

Three transports, one interface (start / stop / snapshot / send_eye):

  SerialLink  USB cable, laptop or Pi. Sends STATUS once a second and parses
              the [STATUS] / [SCORE] / [LCD] lines the firmware prints.
              Works with any firmware from v10 on; no reflash needed.
  BleLink     The phone telemetry characteristic (DF720002, 1 Hz JSON) plus
              EYE: and PING writes on the Pi result characteristic. Used on
              the Pi in the car. Stop the drifts service first: the ESP32
              takes one Pi link at a time.
  SimLink     Made-up but plausible numbers, to work on the dashboard with no
              hardware at all.

Parsing is pure and unit-tested (tests/test_esp_link.py).
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import re
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

PHONE_STATUS_CHAR = "DF720002-B5A3-F393-E0A9-E50E24DCCA9E"
PI_RESULT_CHAR = "DF710003-B5A3-F393-E0A9-E50E24DCCA9E"

# Telemetry fields shown on the dashboard. None = not reported yet.
EMPTY = {
    "link": "disconnected", "fw": None,
    "obd": None, "hz": None, "rpm": None, "spd": None, "thr": None,
    "score": None, "lvl": None, "part": None, "eye": None,
    "alert": None, "lcd1": None, "lcd2": None, "updated": None,
}

_STATUS_RE = re.compile(
    r"\[STATUS\] obd=(?P<obd>\S+) layout=\S+ (?P<hz>[\d.]+) Hz \| rpm (?P<rpm>[\d.]+) .*?"
    r"spd (?P<spd>[\d.]+) .*?thr (?P<thr>[\d.]+)")
_SCORE_RE = re.compile(
    r"\[SCORE\] alert (?P<score>[\d.]+) \((?P<lvl>\w+)\) = eye (?P<eye>[\d.]+)")
_LCD_RE = re.compile(r"\[LCD\] \|(?P<l1>.{0,16})\| \|(?P<l2>.{0,16})\|")
_FW_RE = re.compile(r"\[STATUS\] firmware (?P<fw>\S+)")


def parse_serial_line(line: str) -> dict:
    """Fields found in one firmware log line ({} if none)."""
    line = line.strip()
    if m := _FW_RE.search(line):
        return {"fw": m["fw"]}
    if m := _STATUS_RE.search(line):
        return {"obd": m["obd"], "hz": float(m["hz"]), "rpm": float(m["rpm"]),
                "spd": float(m["spd"]), "thr": float(m["thr"])}
    if m := _SCORE_RE.search(line):
        out = {"score": float(m["score"]), "lvl": m["lvl"], "eye": float(m["eye"]),
               "part": "PARTIAL" in line}
        if " LCD absent" in line:
            out.update(lcd1=None, lcd2=None)
        return out
    if m := _LCD_RE.search(line):
        return {"lcd1": m["l1"], "lcd2": m["l2"]}
    if "-> ALERTING" in line:
        return {"alert": True}
    if "-> IDLE" in line:
        return {"alert": False}
    return {}


def parse_phone_json(text: str) -> dict:
    """Fields from the ESP32's 1 Hz phone telemetry JSON ({} if unreadable)."""
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        return {}
    out = {}
    for k in ("rpm", "spd", "thr", "hz", "score"):
        if isinstance(d.get(k), (int, float)):
            out[k] = float(d[k])
    for k in ("obd", "lvl"):
        if isinstance(d.get(k), str):
            out[k] = d[k]
    for k in ("part", "alert"):
        if isinstance(d.get(k), bool):
            out[k] = d[k]
    return out


class _Base:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = dict(EMPTY)
        self._stop = threading.Event()
        self.log_lines: collections.deque[str] = collections.deque(maxlen=40)
        self.eyes_sent = 0

    def _merge(self, fields: dict) -> None:
        if not fields:
            return
        with self._lock:
            self._state.update(fields)
            self._state["updated"] = time.time()

    def _set_link(self, text: str) -> None:
        with self._lock:
            self._state["link"] = text

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def stop(self) -> None:
        self._stop.set()

    def send_eye(self, score: int) -> None:
        raise NotImplementedError

    def send_command(self, text: str) -> None:
        """Any firmware command, e.g. BEEP (firmware v15+)."""
        raise NotImplementedError


class SerialLink(_Base):
    """USB serial to the ESP32 (COM5 on Windows, /dev/ttyACM0 or /dev/ttyUSB0 on Linux)."""

    def __init__(self, port: str, baud: int = 115200, poll_s: float = 1.0):
        super().__init__()
        self.port_name, self.baud, self.poll_s = port, baud, poll_s
        self._ser = None
        self._wlock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._run, name="esp-serial", daemon=True).start()

    def _open(self):
        import serial
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout = self.port_name, self.baud, 0.2
        # DTR/RTS low before opening: on this board they reset the ESP32.
        ser.dtr = False
        ser.rts = False
        ser.open()
        return ser

    def _write(self, text: str) -> None:
        with self._wlock:
            if self._ser is not None:
                try:
                    self._ser.write((text + "\n").encode())
                except Exception as exc:
                    log.debug("serial write failed: %s", exc)

    def send_eye(self, score: int) -> None:
        self._write(f"EYE:{int(score)}")
        self.eyes_sent += 1

    def send_command(self, text: str) -> None:
        self._write(text)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._ser = self._open()
                self._set_link(f"serial {self.port_name}")
                log.info("ESP32 on %s", self.port_name)
                buf = b""
                last_poll = 0.0
                while not self._stop.is_set():
                    now = time.time()
                    if now - last_poll >= self.poll_s:
                        last_poll = now
                        self._write("STATUS")
                    chunk = self._ser.read(512)
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("utf-8", "replace").rstrip("\r")
                        if line and not line.startswith(("[STATUS]", "[SCORE]", "[LCD]")):
                            self.log_lines.append(line)
                        self._merge(parse_serial_line(line))
            except Exception as exc:
                self._set_link(f"serial {self.port_name}: {exc}")
                log.warning("Serial %s: %s (retrying)", self.port_name, exc)
            finally:
                if self._ser is not None:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
                self._ser = None
            self._stop.wait(2.0)


class BleLink(_Base):
    """BLE to DRIFTS-ESP32: phone telemetry notifications + EYE / PING writes."""

    def __init__(self, device_name: str = "DRIFTS-ESP32", address: str = "", heartbeat_s: float = 1.0):
        super().__init__()
        self.device_name, self.address, self.heartbeat_s = device_name, address, heartbeat_s
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending: collections.deque[str] = collections.deque(maxlen=8)

    def start(self) -> None:
        threading.Thread(target=lambda: asyncio.run(self._main()), name="esp-ble", daemon=True).start()

    def send_eye(self, score: int) -> None:
        self._pending.append(f"EYE:{int(score)}")
        self.eyes_sent += 1

    def send_command(self, text: str) -> None:
        self._pending.append(text)

    def _on_notify(self, _sender, data: bytearray) -> None:
        text = data.decode("utf-8", "replace")
        self._merge(parse_phone_json(text))

    async def _main(self) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self._set_link("bleak not installed (pip install bleak)")
            return
        while not self._stop.is_set():
            try:
                self._set_link("scanning")
                dev = None
                if self.address:
                    dev = await BleakScanner.find_device_by_address(self.address, timeout=6.0)
                if dev is None:
                    dev = await BleakScanner.find_device_by_name(self.device_name, timeout=6.0)
                if dev is None:
                    self._set_link(f"{self.device_name} not found")
                    await asyncio.sleep(2.0)
                    continue
                async with BleakClient(dev) as client:
                    self._set_link(f"BLE {dev.address}")
                    await client.start_notify(PHONE_STATUS_CHAR, self._on_notify)
                    last_ping = 0.0
                    while client.is_connected and not self._stop.is_set():
                        while self._pending:
                            await client.write_gatt_char(PI_RESULT_CHAR, self._pending.popleft().encode(),
                                                         response=False)
                        # The ESP32 treats this writer as the Pi: its alert
                        # fail-safe needs a PING at least every 3 s.
                        if time.time() - last_ping >= self.heartbeat_s:
                            last_ping = time.time()
                            await client.write_gatt_char(PI_RESULT_CHAR, b"PING", response=False)
                        await asyncio.sleep(0.1)
            except Exception as exc:
                self._set_link(f"BLE error: {exc}")
                log.warning("BLE: %s (retrying)", exc)
                await asyncio.sleep(2.0)


class SimLink(_Base):
    """No hardware: an idling car whose alert score follows the eye score."""

    def start(self) -> None:
        self._set_link("simulated")
        self._eye = 90.0
        threading.Thread(target=self._run, name="esp-sim", daemon=True).start()

    def send_eye(self, score: int) -> None:
        self._eye = float(score)
        self.eyes_sent += 1

    def send_command(self, text: str) -> None:
        self.log_lines.append(f"[SIM] {text} received (a real ESP32 would act on it)")

    def _run(self) -> None:
        t0 = time.time()
        while not self._stop.wait(1.0):
            t = time.time() - t0
            score = 0.7 * self._eye + 0.3 * 100
            lvl = "OK" if score >= 70 else "CAUTION" if score >= 50 else "LOW"
            right = f"{score:.0f}/100"
            lcd1 = {"OK": "ALERT".ljust(16 - len(right)) + right, "CAUTION": " INTERVENTION 1 ",
                    "LOW": " INTERVENTION 2 "}[lvl]
            self._merge({"fw": "sim", "obd": "polling", "hz": 4.6,
                         "rpm": 780 + 40 * math.sin(t / 3), "spd": 0.0, "thr": 14.9,
                         "score": score, "lvl": lvl, "eye": self._eye, "part": False,
                         "alert": lvl == "LOW", "lcd1": lcd1[:16], "lcd2": f"  Score {score:.0f}/100"[:16]})


def make_link(kind: str, port: str = "", ble_name: str = "DRIFTS-ESP32", ble_address: str = ""):
    if kind == "serial":
        return SerialLink(port)
    if kind == "ble":
        return BleLink(ble_name, ble_address)
    if kind == "sim":
        return SimLink()
    raise ValueError(f"unknown ESP32 link '{kind}' (serial, ble, sim)")
