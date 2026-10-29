"""Ping the DRIFTS ESP32 so its buzzer beeps: proves the Bluetooth path end to end.

    python -m drifts.beep                    # over BLE (Pi, or a laptop with Bluetooth)
    python -m drifts.beep -n 3               # three beeps, 2 s apart
    python -m drifts.beep --serial COM5      # over the USB cable instead

Each ping writes the text BEEP to the ESP32's Pi result characteristic
(DF710003, the same one the Pi uses for DROWSY / NOT_DROWSY). Firmware v15+
answers with one 1 s beep; older firmware ignores it ("Ignored unknown Pi
message: BEEP" on its serial log).

On the Pi, stop the service first: the ESP32 takes one Pi link at a time.
    sudo systemctl stop drifts

Any phone can do the same with a generic BLE app (nRF Connect, LightBlue):
connect to DRIFTS-ESP32, find characteristic DF710003-..., write the UTF-8
text BEEP.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from drifts.config import load_config


async def _ble(name: str, address: str, result_uuid: str, count: int, gap: float) -> int:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        print("bleak is not installed: pip install bleak")
        return 1
    print(f"Scanning for {address or name} ...")
    dev = None
    if address:
        dev = await BleakScanner.find_device_by_address(address, timeout=8.0)
    if dev is None:
        dev = await BleakScanner.find_device_by_name(name, timeout=8.0)
    if dev is None:
        print(f"{name} not found. Is the ESP32 powered and advertising? On the Pi, is the "
              "drifts service stopped (sudo systemctl stop drifts)?")
        return 1
    async with BleakClient(dev) as client:
        print(f"Connected to {dev.address}")
        for i in range(count):
            await client.write_gatt_char(result_uuid, b"BEEP", response=True)
            print(f"  BEEP {i + 1}/{count} sent")
            if i + 1 < count:
                await asyncio.sleep(gap)
        await asyncio.sleep(0.5)
    return 0


def _serial(port: str, count: int, gap: float) -> int:
    try:
        import serial
    except ImportError:
        print("pyserial is not installed: pip install pyserial")
        return 1
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 115200, 0.3
    ser.dtr = ser.rts = False          # DTR/RTS reset this board
    ser.open()
    try:
        for i in range(count):
            ser.write(b"BEEP\n")
            time.sleep(0.4)
            reply = [ln for ln in ser.read(2048).decode("utf-8", "replace").splitlines() if "BEEP" in ln]
            print(f"  BEEP {i + 1}/{count} sent" + (f" -> {reply[-1].strip()}" if reply else
                                                 "  (no reply: firmware older than v15?)"))
            if i + 1 < count:
                time.sleep(max(0.0, gap - 0.4))
    finally:
        ser.close()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m drifts.beep", description="Make the ESP32 buzzer beep")
    p.add_argument("-n", "--count", type=int, default=1, help="number of beeps (default 1)")
    p.add_argument("--gap", type=float, default=2.0, help="seconds between beeps (default 2)")
    p.add_argument("--serial", metavar="PORT", help="use USB serial (e.g. COM5) instead of BLE")
    args = p.parse_args(argv)
    count = max(1, min(args.count, 20))
    if args.serial:
        return _serial(args.serial, count, args.gap)
    cfg = load_config().ble
    return asyncio.run(_ble(cfg.device_name, cfg.address, cfg.result_char_uuid, count, args.gap))


if __name__ == "__main__":
    sys.exit(main())
