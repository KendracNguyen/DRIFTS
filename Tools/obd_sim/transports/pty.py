"""Expose the virtual dongle on a pseudo-terminal.

`os.openpty()` gives a master file descriptor and a slave path such as
/dev/pts/7. Point any serial client at the slave path and it behaves exactly
like an /dev/rfcomm0 bound to a real dongle.

For a stable path across restarts, symlink it:

    python -m obd_sim --pty --link /tmp/obd0

or bridge it with socat if a client insists on its own device node:

    socat -d -d pty,raw,echo=0,link=/tmp/obd0 pty,raw,echo=0,link=/tmp/obd1
"""

from __future__ import annotations

import os
import pty
import select
import termios
import tty
from pathlib import Path
from typing import Callable


class PtyTransport:
    """A pseudo-terminal endpoint driving an Elm327 instance."""

    def __init__(self, link: str | None = None) -> None:
        self._master, self._slave = pty.openpty()
        self.slave_path = os.ttyname(self._slave)
        self._link: Path | None = None

        # Raw mode: no echo, no line discipline. Without this the kernel
        # echoes every byte back and the client sees its own commands as
        # replies, which looks exactly like a dongle stuck in echo mode.
        tty.setraw(self._master, termios.TCSANOW)
        tty.setraw(self._slave, termios.TCSANOW)

        if link:
            self._link = Path(link)
            if self._link.is_symlink() or self._link.exists():
                self._link.unlink()
            self._link.symlink_to(self.slave_path)

    @property
    def path(self) -> str:
        return str(self._link) if self._link else self.slave_path

    def poll(self, on_bytes: Callable[[bytes], bytes], timeout: float = 0.05) -> int:
        """Service the link once. Returns the number of bytes received.

        `on_bytes` takes received bytes and returns bytes to send back --
        normally `Elm327.feed`.
        """
        ready, _, _ = select.select([self._master], [], [], timeout)
        if not ready:
            return 0
        try:
            data = os.read(self._master, 4096)
        except OSError:
            return 0
        if not data:
            return 0
        reply = on_bytes(data)
        if reply:
            os.write(self._master, reply)
        return len(data)

    def close(self) -> None:
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass
        if self._link is not None and self._link.is_symlink():
            try:
                self._link.unlink()
            except OSError:
                pass
