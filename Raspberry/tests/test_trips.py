"""Trip summaries for the phone app's History tab (drifts/trips.py) and the
shared alertness band in drifts.live's status."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.trips import TripLog, summarize


def rows(scores, t0=100.0):
    return [(t0 + i, s, None, None, None) for i, s in enumerate(scores)]


def test_short_sessions_are_not_listed():
    assert summarize(rows([90] * 30), 100.0) is None
    assert summarize(rows([None] * 120), 100.0) is None


def test_summary_counts_alerts_with_hysteresis():
    scores = [90] * 30 + [65, 71, 68, 72] * 5 + [80] * 30 + [40] * 10 + [90] * 10
    t = summarize(rows(scores), 100.0, demo=True)
    assert t["alerts"] == 2                                   # wobbling around 70 is one alert
    assert t["duration_s"] == len(scores) - 1 and t["min_score"] == 40 and t["demo"]
    assert abs(t["avg_score"] - sum(scores) / len(scores)) < 0.1


def test_trip_log_upserts_newest_first(tmp_path):
    log = TripLog(tmp_path / "logs" / "trips.json")
    a = summarize(rows([80] * 70, 100.0), 100.0)
    b = summarize(rows([60] * 70, 500.0), 500.0)
    log.save(a)
    log.save(b)
    log.save({**b, "duration_s": 999})                        # same session saved again: replaced
    trips = log.load()
    assert [x["start"] for x in trips] == [500.0, 100.0] and trips[0]["duration_s"] == 999
    log.save(None)
    assert len(log.load()) == 2


def test_live_status_carries_the_shared_band():
    from drifts.ui_model import BANDS, COLORS, state_from_live
    st = {"esp": {"link": "simulated", "updated": 1000.0, "score": 66.0, "lvl": "CAUTION"}}
    ui = state_from_live(st, now=1001.0)
    assert ui.band == "caution" and BANDS[ui.band]["title"] == "CAUTION"
    assert COLORS[BANDS[ui.band]["color"]] == "#FFC83D" and ui.demo
