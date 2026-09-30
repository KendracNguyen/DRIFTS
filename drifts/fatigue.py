"""Fatigue score and alert level logic."""

from __future__ import annotations

from enum import IntEnum


class Level(IntEnum):
    OK = 0
    WARN = 1
    CRITICAL = 2


def fatigue_score(perclos: float, steering: float) -> float:
    """Score = 100 - (0.7 * PERCLOS + 0.3 * steering), clamped to 0-100.

    Both inputs are expected on a 0-100 scale. Higher score = more alert.
    """
    score = 100.0 - (0.7 * perclos + 0.3 * steering)
    return max(0.0, min(100.0, score))


def level_for(score: float, warn_below: float, critical_below: float,
              current: Level = Level.OK, hysteresis: float = 5.0) -> Level:
    """Map a score to a level.

    Getting worse happens immediately. Getting better requires the score to
    clear the threshold by `hysteresis` points, so the alert doesn't flicker
    on and off when the score hovers near a boundary.
    """
    if score < critical_below:
        raw = Level.CRITICAL
    elif score < warn_below:
        raw = Level.WARN
    else:
        raw = Level.OK

    if raw >= current:
        return raw

    # Improving: only step down once we're safely past the boundary we're leaving.
    if current == Level.CRITICAL and score < critical_below + hysteresis:
        return Level.CRITICAL
    if current >= Level.WARN and raw == Level.OK and score < warn_below + hysteresis:
        return Level.WARN
    return raw
