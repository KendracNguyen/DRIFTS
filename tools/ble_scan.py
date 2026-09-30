"""List nearby BLE devices and flag any ESP32 running the DRIFTS sketch.

Usage:  python tools/ble_scan.py
Works on the Pi and on Windows. Copy the address into config.toml [ble] address.
"""

import asyncio

from bleak import BleakScanner

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"


async def main():
    print("Scanning for 8 seconds...")
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    rows = sorted(found.values(), key=lambda da: -da[1].rssi)
    for device, adv in rows:
        name = adv.local_name or device.name or "(no name)"
        tag = "  <-- DRIFTS ESP32" if NUS_SERVICE in [u.lower() for u in adv.service_uuids] else ""
        print(f"{device.address}  {adv.rssi:>4} dBm  {name}{tag}")
    if not rows:
        print("Nothing found. Is Bluetooth on?")


if __name__ == "__main__":
    asyncio.run(main())
