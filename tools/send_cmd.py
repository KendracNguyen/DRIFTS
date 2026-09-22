"""Send commands to the ESP32 by hand and print its replies.

Usage:
  python tools/send_cmd.py                      # interactive prompt
  python tools/send_cmd.py "VIB 200 0 0" STOP   # send these, then exit

On the Pi, stop the service first (the ESP32 accepts one connection at a time):
  sudo systemctl stop drifts
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bleak import BleakClient, BleakScanner  # noqa: E402

from drifts.ble_link import NUS_RX, NUS_TX  # noqa: E402
from drifts.config import load_config  # noqa: E402


async def main(lines):
    cfg = load_config().ble
    target = cfg.address or cfg.device_name
    print(f"Looking for {target}...")
    if cfg.address:
        device = await BleakScanner.find_device_by_address(cfg.address, timeout=10)
    else:
        device = await BleakScanner.find_device_by_name(cfg.device_name, timeout=10)
    if device is None:
        print("ESP32 not found. Is it powered, and is the Pi service stopped?")
        return

    async with BleakClient(device) as client:
        await client.start_notify(NUS_TX, lambda _s, d: print("  <-", d.decode().strip()))
        print(f"Connected to {device.address}. Commands: PING, STOP, VIB d on off, BUZZ hz on off")

        async def send(line):
            await client.write_gatt_char(NUS_RX, (line.strip() + "\n").encode(), response=True)
            await asyncio.sleep(0.3)  # give the reply time to arrive

        if lines:
            for line in lines:
                print("->", line)
                await send(line)
            return

        loop = asyncio.get_running_loop()
        while True:
            line = (await loop.run_in_executor(None, input, "> ")).strip()
            if line.lower() in ("q", "quit", "exit"):
                await send("STOP")
                break
            if line:
                await send(line)


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
