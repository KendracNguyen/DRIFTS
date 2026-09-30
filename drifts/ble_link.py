"""Bluetooth Low Energy link to the ESP32.

The ESP32 exposes the Nordic UART Service (NUS): we write text commands to the
RX characteristic and receive replies as notifications on TX. Commands are
short ASCII lines ending in newline, e.g. "VIB 160 400 600".

The link reconnects on its own forever, so the ESP32 can be powered on in any
order relative to the Pi.
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakScanner

from .config import BleConfig

log = logging.getLogger(__name__)

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Pi -> ESP32 (write)
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # ESP32 -> Pi (notify)


class EspLink:
    def __init__(self, cfg: BleConfig):
        self.cfg = cfg
        self._client: BleakClient | None = None
        self._write_lock = asyncio.Lock()
        self.connected = False
        self.generation = 0        # bumps on every new connection
        self.last_reply = ""

    async def _find(self):
        if self.cfg.address:
            return await BleakScanner.find_device_by_address(self.cfg.address, timeout=10.0)
        return await BleakScanner.find_device_by_name(self.cfg.device_name, timeout=10.0)

    def _on_notify(self, _sender, data: bytearray):
        text = data.decode(errors="replace").strip()
        self.last_reply = text
        if text == "FAILSAFE":
            # The ESP32 switched everything off because it stopped hearing from us.
            # Bumping the generation makes the app resend the current alert level.
            log.warning("ESP32 reported fail-safe; resending current level")
            self.generation += 1
        elif text and text != "PONG":
            log.debug("ESP32: %s", text)

    async def run(self):
        """Find, connect, and stay connected. Runs until cancelled."""
        target = self.cfg.address or self.cfg.device_name
        while True:
            try:
                log.info("Looking for ESP32 '%s'...", target)
                device = await self._find()
                if device is None:
                    log.warning("ESP32 '%s' not found, retrying in %.0fs", target, self.cfg.reconnect_s)
                else:
                    gone = asyncio.Event()
                    async with BleakClient(device, disconnected_callback=lambda _c: gone.set()) as client:
                        await client.start_notify(NUS_TX, self._on_notify)
                        self._client = client
                        self.connected = True
                        self.generation += 1
                        log.info("Connected to ESP32 %s", device.address)
                        await gone.wait()
                    log.warning("ESP32 disconnected")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # BLE errors are common; keep trying
                log.warning("BLE error: %s", exc)
            finally:
                self._client = None
                self.connected = False
            await asyncio.sleep(self.cfg.reconnect_s)

    async def send(self, line: str) -> bool:
        """Send one command line. Returns False if not connected or the write failed."""
        client = self._client
        if not self.connected or client is None:
            return False
        data = (line.strip() + "\n").encode()
        try:
            async with self._write_lock:
                await client.write_gatt_char(NUS_RX, data, response=True)
            if line != "PING":
                log.info("-> ESP32: %s", line)
            return True
        except Exception as exc:
            log.warning("Write to ESP32 failed (%s): %s", line, exc)
            return False

    async def send_all(self, lines: list[str]) -> bool:
        ok = True
        for line in lines:
            ok = await self.send(line) and ok
        return ok
