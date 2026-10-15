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


KENDRA_VEEPEAK_2026_10_15 = """\
[OBD] Scanning for a BLE OBD dongle...
[OBD] Candidate: VEEPEAK (66:1e:87:c6:2c:63)
[OBD] Connected, MTU 247
[OBD] Dongle disconnected (reason 520)
[OBD] No known layout matched; probing services...
[OBD] Could not identify the dongle's data channel.
[OBD] Add its UUIDs to LAYOUTS[] in obd_ble.cpp.
[OBD] Link down: discovery failed
"""


def test_drop_after_connect_is_blamed_on_connection_not_layout():
    """The first real Veepeak run: v4 firmware blamed the layout for a dropped link."""
    g = LinkGrader("desk")
    feed(g, KENDRA_VEEPEAK_2026_10_15)
    assert g.status["found"] == PASS
    assert g.status["connect"] == FAIL
    assert g.status["layout"] == PENDING
    assert g.first_problem() == "connect"
    report = g.report()
    assert "HCI 0x08" in report and "520" in report
    assert "LAYOUTS" not in report.split("Look first at")[1]


def test_v5_lost_after_connect_line_and_later_recovery():
    g = LinkGrader("desk")
    feed(g, "[OBD] Candidate: VEEPEAK (66:1e:87:c6:2c:63)\n"
            "[OBD] Connected, MTU 247\n"
            "[OBD] Dongle disconnected (reason 520 = HCI 0x08, connection timeout: radio contact lost)\n"
            "[OBD] Lost the dongle right after connecting; not a layout problem.\n")
    assert g.status["connect"] == FAIL
    feed(g, "[OBD] Connected, MTU 247\n[OBD] Matched layout: FFF0 (generic ELM327 BLE)\n")
    assert g.status["connect"] == PASS and g.status["layout"] == PASS
    assert g.verdict() == PASS


def test_a5_run_restart_resets_and_drop_blames_connection():
    """Oct 15, Audi A5: polling worked, OBD RESTART reconnected too fast, 520 during discovery."""
    g = LinkGrader("car", poll_seconds=10)
    g.feed(watch(hz=6.0))                                  # live before the tool's restart
    g.feed(watch(hz=6.0))
    g.feed("[CMD] Restarting the dongle link")
    assert all(v == PENDING for v in g.status.values())
    feed(g, "[OBD] Link down: manual restart\n"
            "[OBD] Scanning for a BLE OBD dongle...\n"
            "[OBD] Candidate: VEEPEAK (66:1e:87:c6:2c:63)\n"
            "[OBD] Connected, MTU 247\n"
            "[OBD] Dongle disconnected (reason 520 = HCI 0x08, connection timeout: radio contact lost)\n"
            "[OBD] No known layout matched; probing services...\n"
            "[OBD] Could not identify the dongle's data channel.\n"
            "[OBD] Link down: discovery failed\n")
    g.feed("[WATCH] obd=backoff layout=none hz=0.0 rpm=903 spd=0 thr=12.9 load=13.7 nodata=0 err=0 ovf=0 age=4210 STALE")
    assert g.status["found"] == PASS and g.status["connect"] == FAIL
    assert g.status["layout"] == PENDING
    report = g.report()
    assert "was polling before the restart" in report
    assert "STALE" in report
    assert "Look first at: BLE connected" in report


def test_watch_line_without_age_still_parses():
    g = LinkGrader("car", poll_seconds=1)
    g.feed(watch())
    assert g.verdict() == PASS


def test_reports_link_options_and_drop_rate():
    g = LinkGrader("car", poll_seconds=1)
    for _ in range(2):
        feed(g, "[OBD] Link options: MTU exchange OFF (23), PHY 1M only, settle 400 ms\n"
                "[OBD] Candidate: VEEPEAK (66:1e:87:c6:2c:63)\n"
                "[OBD] Connected, MTU 23\n"
                "[OBD] Dongle disconnected (reason 520 = HCI 0x08, connection timeout: radio contact lost)\n"
                "[OBD] Lost the dongle during connect/discovery; not a layout problem.\n")
    feed(g, "[OBD] Connected, MTU 23\n" + BRINGUP.split("[OBD] Connected, MTU 247\n")[1])
    g.feed(watch())
    r = g.report()
    assert g.verdict() == PASS
    assert "MTU exchange OFF (23), PHY 1M only" in r
    assert "3 connection(s), 2 dropped" in r


def test_v8_pid_line_after_polling_upgrades_skip():
    """v8 reports PIDs from live replies, after [WATCH] already shows polling."""
    g = LinkGrader("car", poll_seconds=3)
    g.feed("[CMD] Restarting the dongle link")
    feed(g, BRINGUP.split("[OBD] supported")[0])
    g.feed(watch(hz=4.0))
    g.feed("[OBD] supported: RPM=1 SPEED=1 THROTTLE=1 LOAD=1 (from live replies)")
    g.feed(watch(hz=4.0)); g.feed(watch(hz=5.0))
    assert g.status["pids"] == PASS and g.verdict() == PASS
    assert not any("does not support" in w for w in g.warnings())
