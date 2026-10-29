"""PERCLOS window and eye-score mapping (no camera, no OpenCV needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.eyes import CLOSED, NO_FACE, OPEN, PerclosWindow, eye_score


def test_empty_window_has_no_perclos():
    w = PerclosWindow(10)
    assert w.perclos() is None
    assert eye_score(w.perclos()) is None


def test_perclos_fraction_and_score():
    w = PerclosWindow(10)
    for i in range(10):
        w.add(i * 0.5, CLOSED if i < 3 else OPEN)
    assert abs(w.perclos() - 0.3) < 1e-9
    assert eye_score(w.perclos()) == 70


def test_window_slides():
    w = PerclosWindow(5)
    for i in range(10):
        w.add(float(i), CLOSED)          # closed 0..9 s
    for i in range(10, 16):
        w.add(float(i), OPEN)            # open 10..15 s
    assert w.perclos() == 0.0            # closed samples aged out
    assert eye_score(w.perclos()) == 100


def test_no_face_is_ignored_and_resets_closed_timer():
    w = PerclosWindow(10)
    w.add(0.0, CLOSED)
    w.add(1.0, CLOSED)
    assert w.closed_for(1.5) == 1.5
    w.add(2.0, NO_FACE)
    assert w.closed_for(2.5) == 0.0
    assert w.perclos() == 1.0            # only face samples count


def test_closed_for_resets_on_open():
    w = PerclosWindow(10)
    w.add(0.0, CLOSED)
    w.add(2.0, CLOSED)
    assert w.closed_for(2.0) == 2.0
    w.add(2.5, OPEN)
    assert w.closed_for(3.0) == 0.0


def test_score_clamped():
    assert eye_score(0.0) == 100
    assert eye_score(1.0) == 0
    assert eye_score(1.5) == 0
