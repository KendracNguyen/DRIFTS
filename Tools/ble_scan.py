"""List nearby BLE devices and flag any ESP32 running the DRIFTS sketch.

Usage:  python tools/ble_scan.py
Works on the Pi and on Windows. Copy the address into config.toml [ble] address.
"""

import asyncio

try:
    from bleak import BleakScanner
except ImportError:
    print("bleak is required: pip install bleak")
    exit(1)

PI_SERVICE_UUID = "dft10001-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = "DRIFTS-ESP32"


async def main():
    print("Scanning for BLE devices (8 seconds)...")
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    rows = sorted(found.values(), key=lambda da: -da[1].rssi)

    for device, adv in rows:
        name = adv.local_name or device.name or "(no name)"
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        is_drifts = (name == DEVICE_NAME) or (PI_SERVICE_UUID in uuids)
        tag = "  <-- DRIFTS ESP32" if is_drifts else ""
        print(f"{device.address}  {adv.rssi:>4} dBm  {name}{tag}")

    if not rows:
        print("No devices found. Ensure Bluetooth is enabled.")


if __name__ == "__main__":
    asyncio.run(main())
