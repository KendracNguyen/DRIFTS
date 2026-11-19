"""Simulated night vision (drifts/night.py simulate_night / SimNightCamera, drifts.live --sim-night)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from drifts.night import NIGHT_SAT, NightDetector, SimNightCamera, mean_saturation, simulate_night


def colour_frame(h=240, w=320):
    rng = np.random.default_rng(0)
    f = np.zeros((h, w, 3), np.uint8)
    f[..., 0], f[..., 1], f[..., 2] = 60, 140, 220                    # warm daylight colours
    return np.clip(f + rng.integers(-30, 30, f.shape), 0, 255).astype(np.uint8)


def test_ir_frame_reads_as_night():
    day = colour_frame()
    assert mean_saturation(day) > 0.3
    ir = simulate_night(day, "ir", rng=np.random.default_rng(1))
    assert ir.shape == day.shape and ir.dtype == np.uint8
    assert mean_saturation(ir) < NIGHT_SAT                              # the app's own night test
    det = NightDetector()
    for t in range(0, 50):
        det.update(t * 0.1, mean_saturation(ir))
    assert det.night                                                    # after the 3 s hold


def test_light_comes_from_the_board_side():
    flat = np.full((240, 320, 3), 200, np.uint8)
    right = cv2.cvtColor(simulate_night(flat, "ir", "right", np.random.default_rng(2)), cv2.COLOR_BGR2GRAY)
    left = cv2.cvtColor(simulate_night(flat, "ir", "left", np.random.default_rng(2)), cv2.COLOR_BGR2GRAY)
    assert right[:, 200:].mean() > right[:, :120].mean()
    assert left[:, :120].mean() > left[:, 200:].mean()


def test_dark_mode_is_dark():
    dark = simulate_night(colour_frame(), "dark", rng=np.random.default_rng(3))
    assert dark.mean() < 30


def test_wrapper_passes_everything_else_through():
    class Cam:
        fps = 8
        def __init__(self): self.n = 0
        def grab(self):
            self.n += 1
            return colour_frame()
        def stop(self): self.stopped = True
    inner = Cam()
    cam = SimNightCamera(inner, "ir")
    f = cam.grab()
    assert mean_saturation(f) < NIGHT_SAT and inner.n == 1
    assert cam.fps == 8
    cam.stop()
    assert inner.stopped
    inner.grab = lambda: None
    assert cam.grab() is None                                           # no frame: passed through


def test_live_options():
    from drifts import live
    import argparse
    src = Path(live.__file__).read_text()
    assert '"--sim-night"' in src and '"--sim-night-side"' in src and "SimNightCamera" in src
