"""ESP32 <-> BLE OBD dongle link test, run from a laptop over USB serial.

    python -m obd_link --port COM5                 # dongle in a car, ignition on
    python -m obd_link --port COM5 --mode desk     # dongle powered, not in a car

Needs the firmware with WATCH and OBD RESTART (commit after 1deb26c).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

from obd_link.grader import STAGES, LinkGrader


class Runner:
    """Drives the ESP32 console and feeds the grader. `port` needs write() and
    readline(); a pyserial Serial or a test double."""

    def __init__(self, port, grader: LinkGrader, out=print, clock=time.monotonic):
        self.port = port
        self.g = grader
        self.out = out
        self.clock = clock
        self.log: list[str] = []
        self.watch_on = False

    def send(self, cmd: str) -> None:
        self.port.write((cmd + "\n").encode())
        self.log.append(f">>> {cmd}")

    def handle(self, line: str) -> None:
        self.log.append(line)
        if "[CMD] OBD watch OFF" in line and self.watch_on:
            self.send("WATCH")                       # it was already on; we toggled it off
            return
        if "[CMD] OBD watch ON" in line:
            self.watch_on = True
        stage = self.g.feed(line)
        if stage:
            label = dict(STAGES)[stage]
            self.out(f"  [{self.g.status[stage]:8}] {label}    <- {line.strip()}")
        elif line.startswith("[WATCH]"):
            self.out(f"  {line.strip()}")
        elif line.startswith("[OBD]"):
            self.out(f"  {line.strip()}")

    def run(self, timeout_s: float, restart: bool = True) -> str:
        self.send("WATCH")
        self.watch_on = True
        if restart:
            self.send("OBD RESTART")
        start = self.clock()
        while self.clock() - start < timeout_s and not self.g.done():
            raw = self.port.readline()
            if not raw:
                continue
            self.handle(raw.decode("utf-8", "replace"))
        self.send("STATUS")
        # Drain the STATUS reply briefly, then turn WATCH back off.
        end = self.clock() + 1.5
        while self.clock() < end:
            raw = self.port.readline()
            if raw:
                self.log.append(raw.decode("utf-8", "replace").rstrip())
        if self.watch_on:
            self.send("WATCH")
        return self.g.verdict()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m obd_link", description=__doc__.split("\n")[0])
    ap.add_argument("--port", required=True, help="ESP32 serial port, e.g. COM5 or /dev/ttyUSB0 (the CH343 one)")
    ap.add_argument("--mode", choices=["car", "desk"], default="car",
                    help="car: must reach live data. desk: dongle not in a vehicle, ELM init expected to fail")
    ap.add_argument("--timeout", type=float, default=120, help="seconds before giving up (default 120)")
    ap.add_argument("--poll-seconds", type=int, default=10, help="seconds of live data required to pass (car)")
    ap.add_argument("--no-restart", action="store_true", help="do not send OBD RESTART; grade the link as it is")
    ap.add_argument("--log-dir", default="obd_test_logs", help="where to save the log and report")
    args = ap.parse_args(argv)

    try:
        import serial
    except ImportError:
        print("pyserial is missing:  pip install pyserial   (it comes with esptool)")
        return 2

    # DTR/RTS low: on the CH343 board they drive reset/BOOT.
    port = serial.Serial()
    port.port, port.baudrate, port.timeout = args.port, 115200, 0.5
    port.dtr = False
    port.rts = False
    try:
        port.open()
    except serial.SerialException as exc:
        print(f"Cannot open {args.port}: {exc}\nClose miniterm / Arduino IDE first; only one program can hold the port.")
        return 2

    grader = LinkGrader(args.mode, args.poll_seconds)
    runner = Runner(port, grader)
    print(f"OBD BLE link test on {args.port}, mode {args.mode}, up to {args.timeout:g} s")
    if args.mode == "car":
        print("Car: dongle plugged in, ignition ON (engine running is better). Phone OBD apps closed.")
    else:
        print("Desk: dongle powered but not in a vehicle. 'ELM327 init failed' is EXPECTED at the end.")
    print()
    try:
        verdict = runner.run(args.timeout, restart=not args.no_restart)
    except serial.SerialException as exc:
        print(f"\nSerial port lost: {exc}\nOn this board pressing RESET drops the port. Re-run without touching it.")
        verdict = "FAIL"
    except KeyboardInterrupt:
        print("\nStopped.")
        verdict = grader.verdict()
    finally:
        try:
            port.close()
        except Exception:
            pass

    report = grader.report()
    print("\n" + report)
    out = Path(args.log_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"obd_link_{args.mode}_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    path.write_text(report + "\n\n--- serial log ---\n" + "\n".join(runner.log) + "\n")
    print(f"\nSaved: {path}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
