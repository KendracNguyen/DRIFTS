"""Trip history for the phone app's History tab: one summary per drifts.live
session, saved as it goes, so earlier drives stay listed after a restart.

Only measured values are stored: the start time, how long the session ran,
the average ESP32 alert score, and how many times the score fell into an
intervention band (an "alert", using the shared bands of drifts/ui_model.py).
Sessions with a simulated ESP32 are marked demo.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from drifts.ui_model import OK_ABOVE

MIN_S = 60          # shorter sessions are not listed
HYSTERESIS = 5.0    # alertness.h: back to OK only at 75
KEEP = 50


def summarize(history: list, started: float, demo: bool = False) -> Optional[dict]:
    """history rows: (t, alert score, eye score, rpm, yaw), 1 Hz."""
    scores = [(t, s) for t, s, *_ in history if s is not None]
    if not scores or scores[-1][0] - scores[0][0] < MIN_S:
        return None
    alerts, below = 0, False
    for _, s in scores:                 # same 5-point hysteresis as the ESP32
        if not below and s < OK_ABOVE:
            alerts, below = alerts + 1, True
        elif below and s >= OK_ABOVE + HYSTERESIS:
            below = False
    return {"id": f"{started:.0f}", "start": started, "duration_s": round(scores[-1][0] - started),
            "avg_score": round(sum(s for _, s in scores) / len(scores), 1), "alerts": alerts,
            "min_score": round(min(s for _, s in scores), 1), "demo": demo}


class TripLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> list:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def save(self, trip: Optional[dict]) -> None:
        if trip is None:
            return
        with self._lock:
            trips = [t for t in self.load() if t.get("id") != trip["id"]] + [trip]
            trips.sort(key=lambda t: t.get("start", 0), reverse=True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(trips[:KEEP], indent=1))
            tmp.replace(self.path)
