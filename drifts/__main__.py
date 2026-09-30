"""Entry point: python -m drifts [options]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .app import DriftsApp
from .config import load_config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="drifts", description="DRIFTS fatigue detection")
    p.add_argument("--config", help="path to config.toml (default: repo's config.toml)")
    p.add_argument("--sim", action="store_true", help="use simulated eyes + steering data")
    p.add_argument("--no-ble", action="store_true", help="don't connect to the ESP32")
    p.add_argument("--no-api", action="store_true", help="don't start the HTTP status API")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    p.add_argument("--version", action="version", version=f"drifts {__version__}")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    if not args.debug:
        logging.getLogger("bleak").setLevel(logging.WARNING)

    cfg = load_config(args.config)
    app = DriftsApp(cfg, force_sim=args.sim, use_ble=not args.no_ble, use_api=not args.no_api)
    try:
        asyncio.run(app.run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # Ctrl+C or systemctl stop - already shut down cleanly
    return 0


if __name__ == "__main__":
    sys.exit(main())
