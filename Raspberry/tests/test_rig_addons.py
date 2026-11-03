"""Mouth/yawn, night detection, perf figures and the rig capability table."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from drifts.mouth import YawnDetector, mar
from drifts.night import NightDetector, enhance_ir, mean_saturation
from drifts.perf import PerfMonitor, capability


def mouth_points(open_px, width_px=60.0):
    """478 landmarks with only the mouth points placed; opening in pixels."""
    pts = np.zeros((478, 2))
    cx, cy = 320.0, 300.0
    pts[78] = (cx - width_px / 2, cy)
    pts[308] = (cx + width_px / 2, cy)
    for up, lo, dx in ((13, 14, 0), (82, 87, -12), (312, 317, 12)):
        pts[up] = (cx + dx, cy - open_px / 2)
        pts[lo] = (cx + dx, cy + open_px / 2)
    return pts


def test_mar_values():
    assert mar(mouth_points(0)) == 0.0
    assert abs(mar(mouth_points(30)) - 0.5) < 1e-9
    assert mar(None) is None
    assert mar(np.zeros((100, 2))) is None          # too few landmarks


def test_yawn_needs_duration_not_just_opening():
    y = YawnDetector(min_s=1.5)
    for t in np.arange(0, 0.8, 0.1):                 # talking: wide but short
        y.update(t, 0.6)
    for t in np.arange(0.8, 2.0, 0.1):
        y.update(t, 0.1)
    assert y.total == 0
    hits = [y.update(t, 0.7) for t in np.arange(2.0, 6.0, 0.1)]   # 4 s yawn
    assert sum(hits) == 1 and y.total == 1 and y.yawning()
    y.update(6.1, 0.1)
    assert not y.yawning() and y.recent(6.1) == 1


def test_yawn_hysteresis_counts_once():
    y = YawnDetector(min_s=1.0)
    t = 0.0
    for v in [0.6] * 12 + [0.45, 0.55] * 5 + [0.6] * 5 + [0.1]:   # dips to 0.45 never close it
        y.update(t, v)
        t += 0.1
    assert y.total == 1


def test_yawn_window_and_no_face():
    y = YawnDetector(min_s=1.0, window_s=60)
    for t in np.arange(0, 2, 0.1):
        y.update(t, 0.7)
    y.update(2.0, None)                              # face lost ends the episode
    assert not y.yawning() and y.recent(10) == 1
    y.update(100.0, 0.1)
    assert y.recent(100.0) == 0                      # aged out of the window


def test_night_detection_with_hold():
    n = NightDetector(hold_s=3)
    assert n.update(0, 0.40) is False
    assert n.update(1, 0.05) is False                # not yet: must hold 3 s
    assert n.update(3.9, 0.05) is False
    assert n.update(4.1, 0.05) is True
    assert n.update(5, 0.13) is True                 # between thresholds: stays night
    n.update(6, 0.30)
    assert n.update(9.1, 0.30) is False              # back to day after 3 s


def test_mean_saturation_colour_vs_grey():
    grey = np.full((48, 64, 3), 120, np.uint8)
    colour = grey.copy()
    colour[..., 2] = 220
    assert mean_saturation(grey) < 0.02
    assert mean_saturation(colour) > 0.3
    assert enhance_ir(grey).shape == grey.shape


def test_perf_monitor_from_files(tmp_path):
    (tmp_path / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    (tmp_path / "temp").write_text("54321\n")
    (tmp_path / "model").write_bytes(b"Raspberry Pi 5 Model B Rev 1.0\x00")
    (tmp_path / "mem").write_text("MemTotal:  8000000 kB\nMemAvailable: 6000000 kB\n")
    pm = PerfMonitor(tmp_path / "stat", tmp_path / "temp", tmp_path / "model", tmp_path / "mem")
    assert pm.model == "Raspberry Pi 5 Model B Rev 1.0"
    assert pm.cpu_percent() is None                   # needs two samples
    (tmp_path / "stat").write_text("cpu  150 0 150 900 0 0 0 0 0 0\n")
    assert abs(pm.cpu_percent() - 50.0) < 1e-9        # 100 busy of 200 ticks
    assert pm.temperature_c() == 54.321
    assert abs(pm.mem_used_mb() - 1953.125) < 1e-6


def test_capability_counts():
    zero = capability("imx500")
    pi5 = capability("mediapipe")
    assert zero["live"] == 2 and zero["usable"] == 4 and zero["total"] == 8
    assert pi5["live"] == 8
    assert capability("unknown")["usable"] == 0
