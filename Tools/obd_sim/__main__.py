"""Run the virtual OBD-II dongle.

    python -m obd_sim --pty                      # serial endpoint, prints the path
    python -m obd_sim --pty --link /tmp/obd0     # plus a stable symlink
    python -m obd_sim --ble                      # advertise to the ESP32 (untested)
    python -m obd_sim --pty --scenario rpm       # start in an anomaly
    python -m obd_sim --headless --seconds 30    # no UI, for CI

Run from the repo root or from tools/:

    cd Tools && python -m obd_sim --pty
"""

from __future__ import annotations

import argparse
import select
import sys
import termios
import time
import tty

from .elm327 import Elm327
from .ui import KEYMAP, Status, make_ui
from .vehicle import DEFAULT_SCENARIO, SCENARIOS, Vehicle

TICK_S = 0.05


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="obd_sim", description=__doc__)
    transport = p.add_mutually_exclusive_group()
    transport.add_argument("--pty", action="store_true", help="serial pseudo-terminal (default)")
    transport.add_argument("--ble", action="store_true", help="BLE peripheral (needs bless)")
    p.add_argument("--link", metavar="PATH", help="symlink the pty to a stable path")
    p.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO,
        choices=sorted(SCENARIOS),
        help="scenario to start in",
    )
    p.add_argument("--seed", type=int, help="fix the noise seed for reproducible runs")
    p.add_argument("--headless", action="store_true", help="no UI and no keyboard")
    p.add_argument("--plain", action="store_true", help="plain text UI instead of rich")
    p.add_argument("--seconds", type=float, help="exit after this long (for CI)")
    return p


class _RawStdin:
    """Put the terminal in cbreak so single keystrokes arrive without Enter."""

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled and sys.stdin.isatty()
        self._saved = None

    def __enter__(self) -> "_RawStdin":
        if self._enabled:
            self._saved = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def key(self) -> str | None:
        if not self._enabled:
            return None
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return None
        return sys.stdin.read(1)

    def __exit__(self, *exc: object) -> None:
        if self._saved is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._saved)


def run_pty(args: argparse.Namespace) -> int:
    from .transports.pty import PtyTransport

    vehicle = Vehicle(args.scenario, seed=args.seed)
    elm = Elm327(lambda: vehicle.state)
    transport = PtyTransport(link=args.link)
    ui = make_ui(force_plain=args.plain or args.headless)

    started = time.monotonic()
    last_tick = started
    saw_traffic = False

    def status() -> Status:
        return Status(
            scenario=vehicle.scenario,
            state=vehicle.state,
            transport="pty",
            endpoint=transport.path,
            elapsed_s=time.monotonic() - started,
            requests=elm.request_count,
            no_data=elm.no_data_count,
            connected=saw_traffic,
        )

    print(f"Virtual ELM327 on {transport.path}")
    if args.link:
        print(f"  (symlinked from {transport.slave_path})")
    print("  screen", transport.path, "38400")
    print()

    try:
        with _RawStdin(enabled=not args.headless) as stdin:
            ui.banner(status())
            while True:
                now = time.monotonic()
                if args.seconds and now - started >= args.seconds:
                    return 0

                dt = now - last_tick
                if dt >= TICK_S:
                    vehicle.step(dt)
                    last_tick = now
                    ui.refresh(status())

                if transport.poll(elm.feed, timeout=TICK_S / 2) > 0:
                    saw_traffic = True

                key = stdin.key()
                if key is None:
                    continue
                if key in ("q", "Q", "\x03"):
                    return 0
                if key in KEYMAP:
                    vehicle.select(KEYMAP[key])
                    ui.refresh(status())
    except KeyboardInterrupt:
        return 0
    finally:
        ui.close()
        transport.close()


def run_ble(args: argparse.Namespace) -> int:
    import asyncio

    from .transports.ble import run_forever

    vehicle = Vehicle(args.scenario, seed=args.seed)
    elm = Elm327(lambda: vehicle.state)
    last = time.monotonic()

    def tick() -> None:
        nonlocal last
        now = time.monotonic()
        vehicle.step(now - last)
        last = now

    print("Starting BLE peripheral. This transport has never been run on hardware --")
    print("if it fails, use --pty and see Tools/obd_sim/README.md.")
    try:
        asyncio.run(run_forever(elm.feed, tick))
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.ble:
        return run_ble(args)
    return run_pty(args)


if __name__ == "__main__":
    raise SystemExit(main())
