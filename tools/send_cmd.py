"""Send results or test commands to the DRIFTS-ESP32 BLE hub.

Usage:
  python tools/send_cmd.py                      # interactive prompt
  python tools/send_cmd.py DROWSY               # send DROWSY result
  python tools/send_cmd.py NOT_DROWSY           # send NOT_DROWSY result
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "raspberry"))
sys.path.insert(0, str(REPO_ROOT))

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("bleak is required: pip install bleak")
    sys.exit(1)

from drifts.config import load_config


async def main(commands: list[str]):
    cfg = load_config().ble
    target = cfg.address or cfg.device_name
    print(f"Connecting to ESP32 ({target})...")

    if cfg.address:
        device = await BleakScanner.find_device_by_address(cfg.address, timeout=8.0)
    else:
        device = await BleakScanner.find_device_by_name(cfg.device_name, timeout=8.0)

    if not device:
        print(f"ESP32 '{target}' not found. Make sure it is powered on and advertising.")
        return

    async with BleakClient(device) as client:
        print(f"Connected to {device.address}.")

        # Subscribe to WAKE notifications
        def on_wake(_sender, data: bytearray):
            print("  [NOTIFY from ESP32]:", data.decode("utf-8", errors="replace").strip())

        await client.start_notify(cfg.wake_char_uuid, on_wake)
        print(f"Subscribed to WAKE characteristic ({cfg.wake_char_uuid}).")
        print("Available results: DROWSY, NOT_DROWSY (or 'q' to quit)\n")

        async def send_result(res: str):
            payload = res.strip().encode("utf-8")
            await client.write_gatt_char(cfg.result_char_uuid, payload, response=False)
            print(f"  -> Sent: {res}")
            await asyncio.sleep(0.3)

        if commands:
            for cmd in commands:
                await send_result(cmd)
            return

        loop = asyncio.get_running_loop()
        while True:
            cmd = (await loop.run_in_executor(None, input, "> ")).strip()
            if cmd.lower() in ("q", "quit", "exit"):
                break
            if cmd:
                await send_result(cmd)


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
