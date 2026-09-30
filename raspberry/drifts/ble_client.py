"""BLE client to connect the Raspberry Pi to the DRIFTS-ESP32 hub using bleak."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from drifts.config import BleConfig

log = logging.getLogger(__name__)


class BleClient:
    """Manages BLE connection to the ESP32 hub, listens for WAKE, and reports results."""

    def __init__(self, cfg: BleConfig):
        self.cfg = cfg
        self.wake_event = asyncio.Event()
        self.connected = False
        self._client = None
        self._stop = False

    async def run(self) -> None:
        """Main connection and reconnection supervision loop."""
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            log.warning("bleak is not installed. Running BLE client in simulated mode.")
            return

        while not self._stop:
            try:
                log.info("Scanning for ESP32 hub (%s)...", self.cfg.device_name)
                device = None

                if self.cfg.address:
                    device = await BleakScanner.find_device_by_address(self.cfg.address, timeout=5.0)
                if not device:
                    device = await BleakScanner.find_device_by_name(self.cfg.device_name, timeout=5.0)

                if not device:
                    log.warning("ESP32 not found. Retrying in %.1f s...", self.cfg.reconnect_s)
                    await asyncio.sleep(self.cfg.reconnect_s)
                    continue

                log.info("Connecting to %s (%s)...", device.name, device.address)
                async with BleakClient(device, disconnected_callback=self._on_disconnect) as client:
                    self._client = client
                    self.connected = True
                    log.info("Connected to ESP32 hub!")

                    # Subscribe to WAKE characteristic notifications
                    await client.start_notify(self.cfg.wake_char_uuid, self._on_wake_notify)
                    log.info("Subscribed to WAKE characteristic (%s)", self.cfg.wake_char_uuid)

                    # Keep alive while connected
                    while client.is_connected and not self._stop:
                        await asyncio.sleep(1.0)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("BLE connection error: %s", exc)
                self.connected = False
                self._client = None
                await asyncio.sleep(self.cfg.reconnect_s)

    def _on_disconnect(self, client) -> None:
        log.warning("Disconnected from ESP32 hub.")
        self.connected = False
        self._client = None

    def _on_wake_notify(self, sender, data: bytearray) -> None:
        msg = data.decode("utf-8", errors="replace").strip()
        log.info("[BLE] Received notification on WAKE char: '%s'", msg)
        if msg.upper() == "WAKE":
            self.wake_event.set()

    async def send_result(self, result: str) -> bool:
        """Write DROWSY or NOT_DROWSY to RESULT_CHAR on the ESP32."""
        if not self._client or not self.connected:
            log.warning("Cannot send result '%s': BLE not connected (running in standalone/sim mode).", result)
            return False

        try:
            payload = result.strip().encode("utf-8")
            await self._client.write_gatt_char(self.cfg.result_char_uuid, payload, response=False)
            log.info("[BLE] Successfully wrote result '%s' to ESP32.", result)
            return True
        except Exception as exc:
            log.error("Failed to write result to ESP32: %s", exc)
            return False

    async def send_ping(self) -> bool:
        """Write PING to the result characteristic.

        This is the ESP32's fail-safe input, not a keepalive for the BLE
        link itself. The ESP32 kills the motor and buzzer if it has not
        heard from the Pi within FAILSAFE_MS while alerting -- so without
        this, every alert is cut off a few seconds after it starts, and it
        looks like the actuator driver is broken.
        """
        if not self._client or not self.connected:
            return False
        try:
            await self._client.write_gatt_char(
                self.cfg.result_char_uuid, b"PING", response=False
            )
            return True
        except Exception as exc:
            log.debug("PING failed: %s", exc)
            return False

    async def heartbeat(self) -> None:
        """Send PING at the configured interval for as long as we are up."""
        log.info("Heartbeat started (%.1f s interval)", self.cfg.heartbeat_s)
        while not self._stop:
            await asyncio.sleep(self.cfg.heartbeat_s)
            if self.connected:
                await self.send_ping()

    def trigger_simulated_wake(self) -> None:
        """Manually trigger wake for local simulation."""
        log.info("[SIM] Triggering simulated WAKE event.")
        self.wake_event.set()

    def stop(self) -> None:
        self._stop = True
