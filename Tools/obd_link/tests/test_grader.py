"""Tests for the OBD link grader and runner, driven by recorded-style logs."""

from obd_link.__main__ import Runner
from obd_link.grader import EXPECTED, FAIL, PASS, PENDING, SKIP, LinkGrader

BRINGUP = """\
[OBD] Scanning for a BLE OBD dongle...
[OBD] Candidate: OBDII (aa:bb:cc:dd:ee:ff)
[OBD] Connected, MTU 247
[OBD] Matched layout: FFF0 (generic ELM327 BLE)
[OBD] Initialising ELM327 (protocol '6')...
[OBD] ELM327 ready
[OBD] supported: RPM=1 SPEED=1 THROTTLE=1 LOAD=1
"""


def watch(obd="polling", hz=8.0, ovf=0, layout="FFF0 (generic ELM327 BLE)"):
    return (f"[WATCH] obd={obd} layout={layout} hz={hz:.1f} rpm=780 spd=0 thr=14.5 load=22.0 "
            f"nodata=0 err=0 ovf={ovf}")


def feed(g, text):
    for line in text.splitlines():
        g.feed(line)


def test_car_full_pass():
    g = LinkGrader("car", poll_seconds=3)
    feed(g, BRINGUP)
    assert not g.done()
    for _ in range(3):
        g.feed(watch())
    assert g.done() and g.verdict() == PASS
    assert all(g.status[k] == PASS for k in g.status)
    assert g.facts.mac == "aa:bb:cc:dd:ee:ff" and g.facts.mtu == 247 and g.facts.protocol == "6"
    assert "RESULT: PASS" in g.report()


def test_car_polling_with_zero_hz_does_not_pass():
    g = LinkGrader("car", poll_seconds=3)
    feed(g, BRINGUP)
    for _ in range(10):
        g.feed(watch(hz=0.0))
    assert not g.done() and g.verdict() == FAIL
    assert g.first_problem() == "polling"
    assert "Look first at: Live data flowing" in g.report()


def test_desk_mode_elm_failure_is_expected_and_passes():
    g = LinkGrader("desk")
    feed(g, BRINGUP.split("[OBD] ELM327 ready")[0])
    g.feed("[OBD] ELM327 init failed")
    assert g.status["elm"] == EXPECTED
    assert g.status["polling"] == SKIP
    assert g.done() and g.verdict() == PASS


def test_car_elm_failure_fails_with_hint():
    g = LinkGrader("car")
    feed(g, BRINGUP.split("[OBD] ELM327 ready")[0])
    g.feed("[OBD] ELM327 init failed")
    assert g.status["elm"] == FAIL
    assert g.verdict() == FAIL and g.first_problem() == "elm"
    assert "ELM_PROTOCOL" in g.report()


def test_dongle_never_found_points_at_found_stage():
    g = LinkGrader("car")
    for _ in range(5):
        g.feed("[OBD] Scanning for a BLE OBD dongle...")
        g.feed("[OBD] No dongle found; rescanning")
    assert g.status["scan"] == PASS and g.status["found"] == PENDING
    assert g.first_problem() == "found"
    assert "one central at a time" in g.report()


def test_unknown_layout_lists_probed_services():
    g = LinkGrader("desk")
    feed(g, "[OBD] Scanning for a BLE OBD dongle...\n"
            "[OBD] Candidate: MYSTERY (11:22:33:44:55:66)\n"
            "[OBD] Connected, MTU 185\n"
            "[OBD] No known layout matched; probing services...\n"
            "[OBD]   0xabcd: 1 notify, 1 write\n"
            "[OBD] Could not identify the dongle's data channel.\n")
    assert g.status["layout"] == FAIL and g.done() and g.verdict() == FAIL
    assert "0xabcd: 1 notify, 1 write" in g.report()


def test_joined_mid_session_marks_earlier_stages_skip():
    g = LinkGrader("car", poll_seconds=2)
    g.feed(watch()); g.feed(watch())
    assert g.verdict() == PASS
    assert g.status["found"] == SKIP and g.facts.layout.startswith("FFF0")


def test_warnings_for_overflow_low_rate_and_missing_pid():
    g = LinkGrader("car", poll_seconds=2)
    feed(g, BRINGUP.replace("THROTTLE=1", "THROTTLE=0"))
    g.feed(watch(hz=1.2)); g.feed(watch(hz=8.0, ovf=4))
    w = " ".join(g.warnings())
    assert "ovf=4" in w and "1.2 Hz" in w and "THR" in w


def test_link_drop_is_recorded():
    g = LinkGrader("car")
    feed(g, BRINGUP)
    g.feed("[OBD] Link down: link lost")
    assert "link lost" in " ".join(g.warnings())


class FakePort:
    def __init__(self, lines):
        self.lines = [l.encode() + b"\n" for l in lines]
        self.written = []

    def write(self, data):
        self.written.append(data.decode().strip())

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


class StepClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.1
        return self.t


def test_runner_sends_commands_and_rearms_watch_if_toggled_off():
    lines = ["[CMD] OBD watch OFF", "[CMD] OBD watch ON (1 Hz)", "[CMD] Restarting the dongle link"]
    lines += BRINGUP.splitlines() + [watch()] * 3
    port = FakePort(lines)
    r = Runner(port, LinkGrader("car", poll_seconds=3), out=lambda *_: None, clock=StepClock())
    assert r.run(timeout_s=50) == PASS
    assert port.written[:2] == ["WATCH", "OBD RESTART"]
    assert port.written.count("WATCH") == 3          # initial, re-arm after OFF, final turn-off
    assert "STATUS" in port.written


def test_runner_times_out_cleanly():
    port = FakePort(["[OBD] Scanning for a BLE OBD dongle..."])
    r = Runner(port, LinkGrader("car"), out=lambda *_: None, clock=StepClock())
    assert r.run(timeout_s=2) == FAIL
