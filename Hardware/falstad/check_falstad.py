#!/usr/bin/env python3
"""Sanity-check the Falstad netlists in this folder.

Falstad's text importer is unforgiving in a quiet way: a wire endpoint that
misses an element's post by one grid step leaves an open node, and the circuit
still loads and still animates -- it just reads zero current. This script
catches that before anyone concludes the hardware design is wrong.

Checks per file:
  1. every element line has the right number of fields
  2. every post coordinate is shared by at least two element posts
     (a node with one post is a dangling wire or a missed connection)
  3. no element has zero length
  4. the header line is present and well formed

Run:  python3 Hardware/falstad/check_falstad.py
"""

from __future__ import annotations

import pathlib
import sys

# Posts for a two-terminal element are its two endpoints. Posts for a one-post
# element (ground, rail) are point1 only. The MOSFET is special -- see below.
TWO_POST = {"w", "r", "c", "l", "d", "162", "z", "v", "s"}
ONE_POST = {"g", "R"}
NO_POST = {"x", "o", "38", "37", "207"}

# Minimum field count after "type x1 y1 x2 y2 flags".
MIN_EXTRA = {
    "w": 0,
    "g": 0,
    "r": 1,    # resistance
    "c": 1,    # capacitance (+ optional initial voltage)
    "l": 1,    # inductance (+ optional initial current)
    "d": 1,    # fwdrop or model name
    "162": 1,  # LED: fwdrop/model (+ colour, brightness)
    "R": 6,    # waveform freq maxV bias phase duty
    "v": 6,
    "f": 0,    # vt and beta are both optional in Falstad
}

# MosfetElm offsets the source and drain 16 px perpendicular to the body at the
# point2 end. Gate is point1. Derived from MosfetElm.setPoints(): the
# perpendicular unit vector is ((y2-y1)/L, (x1-x2)/L), source takes -hs*dsign
# and drain +hs*dsign, where dsign = sign(dx) when dy == 0 else sign(dy).
MOSFET_HS = 16


def sign(v: int) -> int:
    return (v > 0) - (v < 0)


def mosfet_posts(x1: int, y1: int, x2: int, y2: int) -> list[tuple[int, int]]:
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        raise ValueError("zero-length mosfet")
    px, py = dy / length, -dx / length        # perpendicular unit vector
    dsign = sign(dx) if dy == 0 else sign(dy)
    hs2 = MOSFET_HS * dsign
    src = (round(x2 + -hs2 * px), round(y2 + -hs2 * py))
    drn = (round(x2 - -hs2 * px), round(y2 - -hs2 * py))
    return [(x1, y1), src, drn]


def check(path: pathlib.Path) -> list[str]:
    problems: list[str] = []
    nodes: dict[tuple[int, int], list[str]] = {}
    lines = path.read_text().splitlines()

    if not lines or not lines[0].startswith("$ "):
        problems.append("line 1: missing or malformed '$' header")
    else:
        header = lines[0].split()
        if len(header) < 7:
            problems.append(f"line 1: header has {len(header) - 1} fields, expected 6 or more")

    for n, raw in enumerate(lines[1:], start=2):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        kind = parts[0]

        if kind in NO_POST:
            continue

        if len(parts) < 6:
            problems.append(f"line {n}: only {len(parts)} fields: {line!r}")
            continue

        try:
            x1, y1, x2, y2 = (int(parts[i]) for i in (1, 2, 3, 4))
        except ValueError:
            problems.append(f"line {n}: non-integer coordinates: {line!r}")
            continue

        extra = len(parts) - 6
        want = MIN_EXTRA.get(kind)
        if want is not None and extra < want:
            problems.append(
                f"line {n}: '{kind}' has {extra} value field(s), expected at least {want}: {line!r}"
            )

        if (x1, y1) == (x2, y2):
            problems.append(f"line {n}: zero-length element: {line!r}")
            continue

        if kind == "f":
            posts = mosfet_posts(x1, y1, x2, y2)
        elif kind in ONE_POST:
            posts = [(x1, y1)]
        elif kind in TWO_POST:
            posts = [(x1, y1), (x2, y2)]
        else:
            problems.append(f"line {n}: unrecognised element type {kind!r}")
            continue

        for p in posts:
            nodes.setdefault(p, []).append(f"line {n} {kind}")

    for coord, owners in sorted(nodes.items()):
        if len(owners) < 2:
            problems.append(
                f"open node at {coord}: only {owners[0]} connects there"
            )

    return problems


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent
    files = sorted(here.glob("*.txt"))
    if not files:
        print("no .txt netlists found", file=sys.stderr)
        return 1

    failed = 0
    for path in files:
        problems = check(path)
        if problems:
            failed += 1
            print(f"FAIL {path.name}")
            for p in problems:
                print(f"       {p}")
        else:
            print(f"OK   {path.name}")

    if failed:
        print(f"\n{failed} of {len(files)} netlists have problems")
        return 1
    print(f"\nAll {len(files)} netlists pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
