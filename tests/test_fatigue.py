"""Run with:  python -m pytest"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.config import load_config  # noqa: E402
from drifts.fatigue import Level, fatigue_score, level_for  # noqa: E402


def test_score_formula():
    assert fatigue_score(0, 0) == 100
    assert fatigue_score(50, 50) == 50
    assert abs(fatigue_score(20, 40) - (100 - (14 + 12))) < 1e-9


def test_score_is_clamped():
    assert fatigue_score(200, 200) == 0
    assert fatigue_score(-10, -10) == 100


def test_levels_get_worse_immediately():
    assert level_for(80, 70, 40) == Level.OK
    assert level_for(60, 70, 40) == Level.WARN
    assert level_for(30, 70, 40) == Level.CRITICAL


def test_hysteresis_on_recovery():
    # Just above the critical line: stay CRITICAL until score >= 45.
    assert level_for(42, 70, 40, Level.CRITICAL, 5) == Level.CRITICAL
    assert level_for(46, 70, 40, Level.CRITICAL, 5) == Level.WARN
    # Just above the warn line: stay WARN until score >= 75.
    assert level_for(72, 70, 40, Level.WARN, 5) == Level.WARN
    assert level_for(76, 70, 40, Level.WARN, 5) == Level.OK


def test_repo_config_loads():
    cfg = load_config()
    assert cfg.fatigue.critical_below < cfg.fatigue.warn_below
    assert cfg.alerts.ok and cfg.alerts.warn and cfg.alerts.critical
