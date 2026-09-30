"""CLI entry point for DRIFTS v2 on the Raspberry Pi Zero 2 W."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from drifts import __version__
from drifts.app import Application
from drifts.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="drifts",
        description="DRIFTS v2 - Driver Drowsiness Inference and Alert Pipeline",
    )
    parser.add_argument(
        "--config",
        "-c",
        metavar="PATH",
        help="Path to custom config.toml",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Simulate periodic WAKE events (for testing without ESP32)",
    )
    parser.add_argument(
        "--no-ble",
        action="store_true",
        help="Disable BLE connection (run standalone)",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable verbose DEBUG logging",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args()

    # Logging setup
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log = logging.getLogger("drifts")
    log.info("Starting DRIFTS v%s...", __version__)

    # Load configuration
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        log.error("Failed to load configuration: %s", exc)
        return 1

    app = Application(cfg, sim=args.sim, no_ble=args.no_ble)

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")
    finally:
        app.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
