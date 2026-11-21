"""Nose aspect ratio (NAR): the sunglasses-mode check on head nods."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts import facesignals as fs


def face(length: float, width: float = 40.0):
    px = [(0.0, 0.0)] * 478
    px[fs.NAR_BRIDGE] = (100.0, 100.0)
    px[fs.NAR_TIP] = (100.0, 100.0 + length)
    px[fs.NAR_ALAE[0]] = (100.0 - width / 2, 150.0)
    px[fs.NAR_ALAE[1]] = (100.0 + width / 2, 150.0)
    return px


def test_nar_ratio():
    assert abs(fs.nar(face(44.0)) - 1.1) < 1e-9
    assert fs.nar(None) is None and fs.nar([(0, 0)] * 10) is None


def test_nar_tracker_flags_head_down_against_own_neutral():
    t = fs.NarTracker(rise=0.05, min_samples=5)
    for i in range(10):
        assert t.update(i * 0.1, 1.10, head_down=False) in (None, False)
    assert abs(t.neutral - 1.10) < 1e-9
    assert t.update(1.1, 1.13, head_down=False) is False          # +2.7 %: not down
    assert t.update(1.2, 1.17, head_down=True) is True            # +6.4 %: down
    assert abs(t.neutral - 1.10) < 0.02                           # head-down frames don't move the neutral


def nod(tracker, start, confirm, down_deg=15.0):
    """Neutral 0 deg, then a 1 s nod to down_deg and back."""
    t = start
    for _ in range(12):
        tracker.update(t, 0.0, confirm=None if confirm is None else False)
        t += 0.1
    for _ in range(10):
        tracker.update(t, down_deg, confirm=confirm)
        t += 0.1
    tracker.update(t, 0.0, confirm=None if confirm is None else False)
    return t + 0.1


def test_normal_mode_counts_nods_without_the_nose():
    n = fs.NodTracker(min_neutral_samples=5)
    nod(n, 0.0, confirm=None)
    assert len(n.nods) == 1 and n.rejected == 0


def test_sunglasses_mode_needs_the_nose_to_agree():
    n = fs.NodTracker(min_neutral_samples=5)
    t = nod(n, 0.0, confirm=False)                    # pitch dropped, nose did not: rejected
    assert len(n.nods) == 0 and n.rejected == 1
    nod(n, t, confirm=True)                           # both agree: a nod
    assert len(n.nods) == 1 and n.rejected == 1


def test_unconfirmed_droop_is_not_scored():
    n = fs.NodTracker(min_neutral_samples=5)
    t = 0.0
    for _ in range(12):
        n.update(t, 0.0, confirm=False)
        t += 0.1
    for _ in range(40):                               # 4 s down, nose never agrees
        n.update(t, 20.0, confirm=False)
        t += 0.1
    assert n.droop_s(t) == 0.0
    for _ in range(5):
        n.update(t, 20.0, confirm=True)               # now the nose agrees: droop counts
        t += 0.1
    assert n.droop_s(t) > 2.0
