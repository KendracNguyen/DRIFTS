"""Camera-confirmed DROWSY / NOT_DROWSY (drifts/drowsy_trigger.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.drowsy_trigger import DrowsyTrigger


def feed(t, states, start=0.0, dt=0.1):
    out, now = [], start
    for st in states:
        c = t.update(now, st)
        if c:
            out.append((round(now, 1), c))
        now += dt
    return out, now


def test_confirms_after_a_short_hold_and_clears_after_alert():
    t = DrowsyTrigger(confirm_s=1.5, repeat_s=10, clear_s=3)
    out, now = feed(t, ["drowsy"] * 20)
    assert out == [(1.5, "DROWSY")]
    out, now = feed(t, ["alert"] * 40, start=now)
    assert [c for _, c in out] == ["NOT_DROWSY"] and not t.active


def test_a_blink_does_not_trigger():
    t = DrowsyTrigger(confirm_s=1.5)
    out, _ = feed(t, (["drowsy"] * 5 + ["alert"] * 5) * 6)
    assert out == []


def test_repeats_while_drowsy_so_the_20_s_alert_cap_cannot_lapse():
    t = DrowsyTrigger(confirm_s=1.5, repeat_s=10)
    out, _ = feed(t, ["drowsy"] * 260)                      # 26 s
    assert [c for _, c in out] == ["DROWSY"] * 3            # at 1.5, 11.5, 21.5 s


def test_no_face_neither_triggers_nor_clears():
    t = DrowsyTrigger(confirm_s=1.0, clear_s=2)
    feed(t, ["drowsy"] * 15)
    out, _ = feed(t, ["no_face"] * 50, start=2.0)
    assert out == [] and t.active                          # stays "sent" until the driver is seen alert
