#!/usr/bin/env python3
"""Verify hardware/pinout.csv agrees with esp32/drifts_esp32/config.h.

Two documents describing the same pins will drift. This makes the drift an
error instead of a discovery during bring-up.

It also refuses any pin that is reserved or absent on the ESP32-C6-WROOM-1,
which catches the specific mistake that motivated this file: GPIO 25 and 26
are SPI flash lines on the C6, and assigning them produces no compiler error
and no runtime error -- just flash corruption.

    python3 hardware/check_pins.py

Exit code 0 if everything agrees, 1 otherwise.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_H = REPO / "esp32" / "drifts_esp32" / "config.h"
PINOUT = Path(__file__).resolve().parent / "pinout.csv"
RESERVED = Path(__file__).resolve().parent / "reserved_pins.csv"


def parse_config_h(path: Path) -> dict[str, int]:
    """Pull `constexpr int NAME = N;` assignments out of config.h."""
    pattern = re.compile(
        r"^\s*constexpr\s+(?:int|uint8_t)\s+(\w+)\s*=\s*(\d+)\s*;", re.MULTILINE
    )
    text = path.read_text(encoding="utf-8")
    return {m.group(1): int(m.group(2)) for m in pattern.finditer(text)}


def main() -> int:
    problems: list[str] = []

    for f in (CONFIG_H, PINOUT, RESERVED):
        if not f.exists():
            print(f"MISSING: {f}", file=sys.stderr)
            return 1

    constants = parse_config_h(CONFIG_H)

    with open(RESERVED, newline="", encoding="utf-8") as fh:
        blocked = {int(r["gpio"]): (r["status"], r["reason"]) for r in csv.DictReader(fh)}

    with open(PINOUT, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    seen: dict[int, str] = {}

    for row in rows:
        gpio = int(row["gpio"])
        name = row["firmware_constant"].strip()
        component = row["component"]

        # 1. Does the firmware constant exist and match?
        if name not in constants:
            problems.append(f"GPIO {gpio} ({component}): config.h has no constant {name}")
        elif constants[name] != gpio:
            problems.append(
                f"GPIO {gpio} ({component}): pinout.csv says {gpio}, "
                f"config.h says {name} = {constants[name]}"
            )

        # 2. Is the pin usable on this board at all?
        if gpio in blocked:
            status, reason = blocked[gpio]
            problems.append(
                f"GPIO {gpio} ({component}) is {status} on ESP32-C6-WROOM-1: {reason}"
            )

        # 3. Assigned twice?
        if gpio in seen:
            problems.append(f"GPIO {gpio} assigned to both {seen[gpio]} and {component}")
        seen[gpio] = component

    # 4. Any pin constant in config.h that the docs never mention?
    documented = {int(r["gpio"]) for r in rows}
    for name, value in constants.items():
        if name.endswith("_PIN") and value not in documented:
            problems.append(
                f"config.h defines {name} = {value}, which is not in pinout.csv"
            )

    if problems:
        print(f"{len(problems)} problem(s):\n", file=sys.stderr)
        for p in problems:
            print(f"  FAIL  {p}", file=sys.stderr)
        return 1

    print(f"OK: {len(rows)} pins agree between pinout.csv and config.h")
    for row in rows:
        print(f"  GPIO {row['gpio']:>2}  {row['firmware_constant']:<14} {row['component']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
