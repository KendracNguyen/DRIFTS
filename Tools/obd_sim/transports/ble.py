"""Advertise the virtual dongle as a BLE peripheral the ESP32 will connect to.

This is the transport that makes the simulator worth building: the firmware's
own scan, GATT layout probe, ELM327 init and PID poller run unmodified. No
test hook, no `#ifdef SIMULATION` -- the code that ships is the code tested.

Layout: FFE0 / FFE1, the HM-10 style with ONE characteristic carrying both
notify and write. That is entry 2 in `LAYOUTS[]` in `Esp32/drifts_esp32/obd_ble.cpp`
and the simplest of the four to serve.

The advertised name contains "obd" so it matches `OBD_NAME_HINTS` in config.h.

=============================================================================
NOT TESTED ON HARDWARE. Written against the bless API but never run against a
real BlueZ stack or a real ESP32. Treat the first bring-up as debugging, and
expect to need:
  - BlueZ >= 5.55 and an adapter that supports the peripheral role
  - `bless` installed:  pip install bless
  - the adapter not already busy as a central (stop bluetoothctl scans)
If it fights back, fall back to the PTY transport plus a real dongle for the
BLE half. The PTY path is tested and proves the protocol; this one proves the
radio.
=============================================================================
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

LOG = logging.getLogger(__name__)

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
ADVERTISED_NAME = "OBDSIM-DRIFTS"  # contains "obd" -> matches OBD_NAME_HINTS

# A BLE notification carries at most MTU-3 bytes. The firmware negotiates a
# larger MTU but must not rely on it, so chunk at the conservative default.
NOTIFY_CHUNK = 20


class BleTransport:
    """GATT peripheral presenting an ELM327 over FFE0/FFE1."""

    def __init__(self, on_bytes: Callable[[bytes], bytes], name: str = ADVERTISED_NAME) -> None:
        self._on_bytes = on_bytes
        self._name = name
        self._server = None
        self.connected = False
        self.rx_bytes = 0
        self.tx_bytes = 0

    async def start(self) -> None:
        try:
            from bless import (  # type: ignore[import-not-found]
                BlessServer,
                BlessGATTCharacteristic,
                GATTAttributePermissions,
                GATTCharacteristicProperties,
            )
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "the BLE transport needs 'bless': pip install bless"
            ) from exc

        server = BlessServer(name=self._name)
        server.read_request_func = self._on_read
        server.write_request_func = self._on_write

        await server.add_new_service(SERVICE_UUID)
        await server.add_new_characteristic(
            SERVICE_UUID,
            CHAR_UUID,
            GATTCharacteristicProperties.notify
            | GATTCharacteristicProperties.write
            | GATTCharacteristicProperties.write_without_response,
            None,
            GATTAttributePermissions.readable | GATTAttributePermissions.writeable,
        )

        self._server = server
        await server.start()
        LOG.info("advertising as %s (service %s)", self._name, SERVICE_UUID)

    def _on_read(self, characteristic, **_: object) -> bytearray:
        return characteristic.value or bytearray()

    def _on_write(self, characteristic, value: bytearray, **_: object) -> None:
        """A central wrote a command. Answer it as notifications on the same char."""
        self.connected = True
        self.rx_bytes += len(value)
        reply = self._on_bytes(bytes(value))
        if not reply:
            return
        for start in range(0, len(reply), NOTIFY_CHUNK):
            chunk = reply[start : start + NOTIFY_CHUNK]
            characteristic.value = bytearray(chunk)
            if self._server is not None:
                self._server.update_value(SERVICE_UUID, CHAR_UUID)
            self.tx_bytes += len(chunk)

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.stop()
            self._server = None
        self.connected = False


async def run_forever(on_bytes: Callable[[bytes], bytes], tick: Callable[[], None]) -> None:
    """Start the peripheral and keep the vehicle model advancing."""
    transport = BleTransport(on_bytes)
    await transport.start()
    try:
        while True:
            tick()
            await asyncio.sleep(0.05)
    finally:
        await transport.stop()
