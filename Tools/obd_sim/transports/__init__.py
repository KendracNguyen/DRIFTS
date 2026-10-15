"""Ways to expose the virtual dongle to a client.

`pty`  -- a POSIX pseudo-terminal. Any serial client (screen, minicom,
          python-OBD, pyserial) opens it like a real /dev/rfcomm0.
          Works on Linux and macOS. Tested.

`ble`  -- a BLE GATT peripheral advertising the HM-10 style FFE0/FFE1 layout,
          so the ESP32 firmware's own discovery and ELM dialogue run against
          it unchanged. Needs BlueZ and the optional `bless` package.
          NOT tested -- see the note in ble.py.
"""

from .pty import PtyTransport

__all__ = ["PtyTransport"]
