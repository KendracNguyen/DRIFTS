"""Wired link from the Raspberry Pi Zero to the DRIFTS ESP32 (firmware v21+).

Three wires, no Bluetooth (Hardware/pinout.md):
    Pi GPIO 14 TX (pin 8)  -> 1k -> ESP32 GPIO 20 (RX)
    Pi GPIO 15 RX (pin 10) <- 1k <- ESP32 GPIO 19 (TX)
    Pi GND (pin 6)         -- ESP32 GND

115200 baud, one message per line, the same messages as the BLE Pi link
(Esp32/drifts_esp32/pi_uart.h):
    ESP32 -> Pi:  WAKE | IDLE | STATUS {json} (1 Hz) | HELLO <fw>
    Pi -> ESP32:  PING (every heartbeat_s: the ESP32's fail-safe input) |
                  DROWSY | NOT_DROWSY | EYE:<n> | BEEP

UartClient has the same interface as ble_client.BleClient, so the app
picks one with [link] kind = "uart" | "ble" in config.toml.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

from drifts.config import BleConfig, LinkConfig
from drifts.esp_link import parse_phone_json

log = logging.getLogger(__name__)


class LineParser:
    """Bytes from the wire -> complete text lines (partial lines kept)."""

    def __init__(self, max_len: int = 1024):
        self.buf = b""
        self.max_len = max_len

    def feed(self, data: bytes) -> list:
        self.buf += data
        lines = []
        while True:
            i = min((p for p in (self.buf.find(b"\n"), self.buf.find(b"\r")) if p >= 0), default=-1)
            if i < 0:
                break
            raw, self.buf = self.buf[:i], self.buf[i + 1:]
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                lines.append(text)
        if len(self.buf) > self.max_len:          # garbage with no line end: drop it
            self.buf = b""
        return lines


class UartClient:
    """The ESP32 over a UART: WAKE / status in, results and PING out."""

    def __init__(self, cfg: LinkConfig, ble_cfg: Optional[BleConfig] = None, serial_factory=None):
        self.cfg = cfg
        self.heartbeat_s = (ble_cfg or BleConfig()).heartbeat_s
        self.wake_event = asyncio.Event()
        self.connected = False             # heard from the ESP32 within link_s
        self.status: dict = {}
        self.status_t: float = 0.0
        self.fw: Optional[str] = None
        self.last_heard = 0.0
        self._ser = None
        self._lock = threading.Lock()
        self._stop = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._parser = LineParser()
        self._serial_factory = serial_factory     # tests pass a fake port

    # ---------------------------------------------------------------- incoming
    def handle_line(self, line: str, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        self.last_heard = now
        if not self.connected:
            log.info("[UART] ESP32 heard on %s", self.cfg.uart_port)
        self.connected = True
        word = line.split(" ", 1)[0].upper()
        if word == "WAKE":
            log.info("[UART] WAKE from the ESP32")
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self.wake_event.set)
            else:
                self.wake_event.set()
        elif word == "STATUS":
            fields = parse_phone_json(line[7:])
            if fields:
                self.status.update(fields)
                self.status_t = now
        elif word == "HELLO":
            self.fw = line[6:].strip() or None
            log.info("[UART] ESP32 firmware %s", self.fw)
        # IDLE and console noise need nothing

    def _open(self):
        if self._serial_factory is not None:
            return self._serial_factory()
        import serial                               # pyserial
        return serial.Serial(self.cfg.uart_port, self.cfg.uart_baud, timeout=0.2)

    def _reader(self) -> None:
        while not self._stop:
            if self._ser is None:
                try:
                    ser = self._open()
                    with self._lock:
                        self._ser = ser
                    log.info("[UART] Opened %s at %d baud", self.cfg.uart_port, self.cfg.uart_baud)
                except Exception as exc:
                    log.warning("[UART] Can't open %s (%s). Retrying in 3 s. Is the UART enabled "
                                "(Hardware/pinout.md)?", self.cfg.uart_port, exc)
                    time.sleep(3.0)
                    continue
            try:
                ser = self._ser
                data = ser.read(256) if ser is not None else b""
            except Exception as exc:
                if self._stop:
                    break                         # closed by stop()
                log.warning("[UART] Read failed (%s): reopening", exc)
                with self._lock:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
                    self._ser = None
                continue
            for line in self._parser.feed(data or b""):
                self.handle_line(line)
            if self.connected and time.monotonic() - self.last_heard > self.cfg.link_s:
                log.warning("[UART] ESP32 silent for %.0f s: check the wires and the ESP32's power",
                            self.cfg.link_s)
                self.connected = False

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        t = threading.Thread(target=self._reader, name="uart", daemon=True)
        t.start()
        while not self._stop:
            await asyncio.sleep(0.5)

    # ---------------------------------------------------------------- outgoing
    def _write(self, text: str) -> bool:
        with self._lock:
            if self._ser is None:
                return False
            try:
                self._ser.write((text.strip() + "\n").encode())
                return True
            except Exception as exc:
                log.debug("[UART] write failed: %s", exc)
                return False

    async def send_result(self, result: str) -> bool:
        """DROWSY or NOT_DROWSY (or EYE:<n>, BEEP) to the ESP32."""
        ok = self._write(result)
        if ok:
            log.info("[UART] Sent '%s' to the ESP32.", result)
        else:
            log.warning("Cannot send result '%s': the UART is not open.", result)
        return ok

    async def send_ping(self) -> bool:
        """The ESP32's fail-safe input: it cuts the alert if the Pi goes quiet."""
        return self._write("PING")

    async def heartbeat(self) -> None:
        log.info("Heartbeat started (%.1f s interval, wired)", self.heartbeat_s)
        while not self._stop:
            await asyncio.sleep(self.heartbeat_s)
            await self.send_ping()            # sent even before the ESP32 is heard: it may be booting

    def trigger_simulated_wake(self) -> None:
        log.info("[SIM] Triggering simulated WAKE event.")
        self.wake_event.set()

    def stop(self) -> None:
        self._stop = True
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None


def make_client(cfg):
    """The ESP32 link the config asks for: [link] kind = "uart" (default) or "ble"."""
    if cfg.link.kind == "ble":
        from drifts.ble_client import BleClient
        return BleClient(cfg.ble)
    if cfg.link.kind != "uart":
        raise ValueError(f'[link] kind must be "uart" or "ble", not {cfg.link.kind!r}')
    return UartClient(cfg.link, cfg.ble)
