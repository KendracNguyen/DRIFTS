"""Who's driving, the face check and the calibration on the LCD (drifts/startup.py,
drifts/drivers.py, drifts/name_listener.py, and their place in WhisplayUI / drifts.live)."""

import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from drifts import drivers as dv
from drifts.config import WhisplayConfig
from drifts.name_listener import grammar, match
from drifts.startup import FACE_OK_S, HELLO_S, StartupFlow, face_hint
from drifts.ui_model import state_from_status
from drifts.whisplay_ui import H, W, SimBoard, Speaker, WhisplayUI, render


def st(**kw):
    return state_from_status({"score": 90, "lvl": "OK", "obd": "polling"}, linked=True, **kw)


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    d = tmp_path / "profiles"
    d.mkdir()
    for n in ("kendra", "trevor"):
        (d / f"{n}.json").write_text('{"name": "%s"}' % n)
    monkeypatch.setattr(dv, "PROFILE_DIR", d)
    return d


def face_points(cx, cy, size, w=640, h=480):
    """A fake 478-point face: a box centred at (cx, cy) (0-1, as on the mirrored LCD), size = height 0-1."""
    x = w - cx * w                                        # stored unmirrored
    half = size * h / 2
    return [(x - half * 0.75, cy * h - half), (x + half * 0.75, cy * h + half)] * 239


# ---------------------------------------------------------------- face position hints
def test_face_hints():
    assert face_hint(None, 640, 480) == (False, "Look at the camera")
    assert face_hint(face_points(0.5, 0.5, 0.45), 640, 480) == (True, "Face in frame")
    assert face_hint(face_points(0.5, 0.5, 0.12), 640, 480)[1] == "Come closer"
    assert face_hint(face_points(0.5, 0.5, 0.9), 640, 480)[1] == "Move back a little"
    assert face_hint(face_points(0.25, 0.5, 0.45), 640, 480)[1] == "Move right"     # drawn on the left
    assert face_hint(face_points(0.75, 0.5, 0.45), 640, 480)[1] == "Move left"
    assert face_hint(face_points(0.5, 0.25, 0.45), 640, 480)[1] == "Move down a little"


# ---------------------------------------------------------------- the speech result
def test_grammar_and_match():
    assert grammar(["Kendra", "trevor"]) == ["guest", "kendra", "trevor", "[unk]"]
    names = ["kendra", "trevor"]
    r = {"result": [{"word": "[unk]", "conf": 1.0}, {"word": "kendra", "conf": 0.91}], "text": "[unk] kendra"}
    assert match(r, names) == ("kendra", 0.91)                                     # extra unknown word ignored
    assert match({"result": [{"word": "guest", "conf": 0.8}]}, names) == ("Guest", 0.8)
    assert match({"result": [{"word": "kendra", "conf": 0.3}]}, names)[0] is None  # not sure enough
    two = {"result": [{"word": "kendra", "conf": 0.9}, {"word": "trevor", "conf": 0.9}]}
    assert match(two, names) == (None, 0.0)                                        # two names: no answer


# ---------------------------------------------------------------- drivers and name recordings
def test_driver_roster_and_last_driver(profiles):
    assert dv.list_drivers() == ["kendra", "trevor"]
    assert dv.default_driver(["kendra", "trevor"]) == dv.GUEST
    dv.save_last_driver("trevor")
    assert dv.default_driver(["kendra", "trevor"]) == "trevor"
    assert dv.default_driver(["kendra"]) == dv.GUEST                               # no longer on the list
    assert dv.clean_name("Ken/../dra!") == "Kendra"


def test_record_name_trims_and_saves(profiles):
    rate = dv.NAME_RATE
    voice = (np.sin(np.linspace(0, 600, int(0.5 * rate))) * 8000).astype(np.int16)
    raw = np.concatenate([np.zeros(rate // 2, np.int16), voice, np.zeros(rate, np.int16)])
    out = dv.record_name("kendra", recorder=lambda s: raw)
    assert out["ok"] and 0.5 <= out["seconds"] <= 0.9                             # silence trimmed
    assert dv.has_recording("kendra") and dv.recordings() == ["kendra"]
    quiet = dv.record_name("trevor", recorder=lambda s: np.zeros(2 * rate, np.int16))
    assert not quiet["ok"] and "didn't hear" in quiet["why"] and not dv.has_recording("trevor")
    assert dv.delete_name("kendra") and not dv.has_recording("kendra") and not dv.delete_name("kendra")


# ---------------------------------------------------------------- the flow
class FakeListener:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def listen(self, names, timeout, stop=None):
        self.calls += 1
        return self.answer


def make_flow(profiles, answer=("kendra", 0.9), frame=None, **kw):
    chosen, calibrated = [], []
    sp = Speaker(sim=True)
    flow = StartupFlow(dv.list_drivers, chosen.append, sp, listener=FakeListener(answer) if answer else None,
                       frame=frame, calibrate=lambda: calibrated.append(1), **kw)
    return flow, sp, chosen, calibrated


def run(flow, s, t0, t1, step=0.1, until=None):
    t = t0
    while t <= t1:
        flow.tick(s, t)
        if until and flow.phase == until:
            return t
        if flow._listen_thread is not None:
            flow._listen_thread.join(1)
        t = round(t + step, 3)
    return t


def test_say_your_name_then_face_check_then_calibration(profiles):
    dv.record_name("kendra", recorder=lambda s: (np.sin(np.linspace(0, 900, 12000)) * 9000).astype(np.int16))
    pts = face_points(0.5, 0.5, 0.45)
    frame = (np.zeros((480, 640, 3), np.uint8), pts)
    flow, sp, chosen, calibrated = make_flow(profiles, frame=lambda: frame)
    flow.begin(0.0)
    assert flow.names == ["kendra", "trevor", "Guest"] and sp.played[-1][1] == "WHO"
    run(flow, st(), 2.0, 2.3)                                  # after the question: listens, hears "kendra"
    assert chosen == ["kendra"] and flow.phase == "hello" and flow.context()["select"]["chosen"] == "Kendra"
    assert sp.played[-1][1] == "HI+kendra_name.wav"            # "Hi," + her own recording
    assert dv.last_driver() == "kendra"
    run(flow, st(), 2.4, 2.4 + HELLO_S + 0.2)                  # -> face check
    assert flow.phase == "face" and sp.played[-1][1] == "FACE"
    t = run(flow, st(), 5.4, 5.4 + FACE_OK_S + 0.4, until="calib")   # face in the oval long enough -> calibrate
    assert calibrated == [1] and flow.phase == "calib" and flow.view() == "face"
    run(flow, st(calibrating="quick", calib_progress=0.5), t + 0.1, t + 0.5)
    assert flow.phase == "calib" and flow.face["progress"] == 0.5
    run(flow, st(calibrated=True), t + 0.6, t + 1.6)           # calibration finished -> normal screens
    assert flow.phase == "done" and not flow.active


def test_no_answer_picks_the_last_driver(profiles):
    dv.save_last_driver("trevor")
    flow, sp, chosen, _ = make_flow(profiles, answer=(None, 0.0), timeout_s=5)
    flow.begin(0.0)
    assert flow.names[flow.highlight] == "trevor"
    run(flow, st(), 2.0, 5.2)
    assert chosen == ["trevor"] and flow.how == "timeout"


def test_button_picks_and_guest_greeting(profiles):
    flow, sp, chosen, _ = make_flow(profiles, answer=None)    # no speech model: the button
    flow.begin(0.0)
    assert flow.status == "Press to choose"
    assert flow.button("short", 1.0) and flow.button("short", 1.2)    # kendra (default Guest) -> kendra -> trevor
    assert flow.names[flow.highlight] == "trevor"
    flow.button("short", 1.4)
    assert flow.button("long", 1.6) and chosen == ["Guest"]
    assert sp.played[-1][1] == "GUEST" and flow.status == "Driving as a guest"
    assert not flow.button("short", 2.0)                       # the list is over: presses go back to normal


def test_no_camera_skips_the_face_check(profiles):
    flow, sp, chosen, calibrated = make_flow(profiles, frame=None)
    flow.begin(0.0)
    run(flow, st(), 2.0, 2.0 + HELLO_S + 0.5)
    assert calibrated == [1] and flow.phase == "calib"


# ---------------------------------------------------------------- on the LCD
def test_lcd_runs_the_flow_after_the_welcome(profiles):
    chosen = []
    frame = (np.zeros((480, 640, 3), np.uint8), face_points(0.5, 0.5, 0.45))
    hooks = {"drivers": dv.list_drivers, "choose_driver": chosen.append, "frame": lambda: frame,
             "calibrate": lambda: None, "listener": FakeListener(("trevor", 0.88))}
    ui = WhisplayUI(WhisplayConfig(enabled=True), board=SimBoard(), sim=True, hooks=hooks)
    ui.tick(st(), 0.0)
    assert ui.view(st(), False, 0.1) == "welcome"
    ui.tick(st(), 4.1)                                          # welcome over: Who's driving?
    assert ui.startup.phase == "select" and ui.view(st(), True, 4.1) == "select"
    for t in (6.0, 6.2, 6.4):
        ui.tick(st(), t)
        if ui.startup._listen_thread is not None:
            ui.startup._listen_thread.join(1)
    assert chosen == ["trevor"]
    ui.tick(st(), 6.6 + HELLO_S)
    assert ui.view(st(), True, 6.7 + HELLO_S) == "face"
    assert ui.board.frame.size == (W, H)
    off = WhisplayUI(WhisplayConfig(enabled=True, select_driver=False), board=SimBoard(), sim=True, hooks=hooks)
    assert off.startup is None                                  # turned off in config.toml


def test_select_and_face_screens_render():
    ctx = {"select": {"names": ["Kendra", "Trevor", "Alex", "Sam", "Guest"], "highlight": 4, "chosen": None,
                      "status": "Listening..."}}
    assert render(st(), 0, "select", ctx=ctx).size == (W, H)
    ctx["select"]["chosen"] = "Guest"
    assert render(st(), 0, "select", ctx=ctx).size == (W, H)
    img = np.full((480, 640, 3), 90, np.uint8)
    for f in ({"img": img, "ok": True, "hint": "Face in frame"}, {"img": None, "hint": "Starting the camera"},
              {"img": img, "calibrating": True, "progress": 0.4}):
        assert render(st(), 0, "face", ctx={"face": f}).size == (W, H)


# ---------------------------------------------------------------- the phone app's side
def test_driver_endpoints_and_privacy(profiles, tmp_path):
    from drifts.live import make_handler
    from test_privacy import make
    p = make(tmp_path)
    (profiles / "kendra_name.wav").write_bytes(b"RIFF")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(p))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        import json
        d = json.loads(urllib.request.urlopen(f"{base}/drivers.json").read())
        assert [(x["name"], x["recording"]) for x in d["drivers"]] == [("kendra", True), ("trevor", False)]
        r = urllib.request.Request(f"{base}/name/delete?name=kendra", method="POST")
        assert json.loads(urllib.request.urlopen(r).read())["ok"] and not dv.has_recording("kendra")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(f"{base}/name/record?name=nobody", method="POST"))
    finally:
        srv.shutdown()
    (profiles / "trevor_name.wav").write_bytes(b"RIFF")
    assert p.privacy()["name_recordings"] == ["trevor"]
    p.set_driver(dv.GUEST)
    assert p.driver == dv.GUEST and p.profile is None and p.status()["driver"] == dv.GUEST
