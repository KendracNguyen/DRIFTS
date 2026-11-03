"""The dashboard speaks the same words as the ESP32 and Tokyo (Voice/phrases.json)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_phrases_match_voice_clips():
    phrases = {p["name"]: p["text"] for p in json.loads((ROOT / "Voice" / "phrases.json").read_text())["phrases"]}
    html = (ROOT / "Raspberry" / "drifts" / "dashboard.html").read_text()
    for name in ("LEVEL1", "LEVEL2", "RECOVERED"):
        assert f'{name}:"{phrases[name]}"' in html, name
