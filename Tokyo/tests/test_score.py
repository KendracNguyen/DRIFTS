"""Tokyo alert score: same weighting as alertness.h, with explicit fallbacks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokyo.score import LocalScore


def test_weighting_matches_first_model():
    s = LocalScore()
    r = s.update(0, eye=40, driving=100)
    assert abs(r.score - 58.0) < 1e-9 and not r.partial and not r.no_driving     # 0.7*40 + 0.3*100
    r = s.update(1, eye=10)                                                      # driving held
    assert abs(r.score - 37.0) < 1e-9


def test_fallbacks():
    s = LocalScore(eye_stale_s=120, driving_stale_s=10)
    assert s.update(0).score is None
    r = s.update(1, eye=80)
    assert r.score == 80 and r.no_driving and not r.partial                      # no ESP32 yet
    r = s.update(2, driving=60)
    assert abs(r.score - (0.7 * 80 + 0.3 * 60)) < 1e-9
    r = s.update(20)                                                             # driving went stale
    assert r.score == 80 and r.no_driving
    r = s.update(200, driving=90)                                                # eye stale: driving only
    assert r.score == 90 and r.partial


def test_clamped():
    r = LocalScore().update(0, eye=130, driving=-5)
    assert r.eye == 100 and r.driving == 0
