"""Tokyo app hook (phases 2 + 4) with a fake drifts.live pipeline: camera eye
score + ESP32 driving score -> level -> outputs, status for the dashboard."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokyo.app import TokyoOutputs
from tokyo.config import Config


class FakeCam:
    def __init__(self):
        self.perclos, self.state = 0.05, "alert"
        self.face = {"found": True, "profile_loaded": True}

    def eye_score(self):
        return None if self.perclos is None else round(100 * (1 - self.perclos))


class FakeLink:
    def __init__(self):
        self.drv = 90.0

    def snapshot(self):
        return {"drv_score": self.drv}


def make():
    cfg = Config()
    cfg.interventions.min_hold_s = 0
    cam, link = FakeCam(), FakeLink()
    pipe = SimpleNamespace(camera=cam, link=link, eye={}, esp_override={}, extra_status={})
    return TokyoOutputs(cfg, sim=True), pipe, cam, link


def tick(out, pipe, cam, t):
    cam.face["state"] = cam.state
    out(pipe, t)
    return pipe.extra_status["outputs"], pipe.esp_override


def test_ok_then_levels_then_recovery():
    out, pipe, cam, link = make()
    o, e = tick(out, pipe, cam, 100.0)
    assert o["level"] == 0 and e["lvl"] == "OK" and abs(e["score"] - (0.7 * 95 + 0.3 * 90)) < 1e-9
    cam.perclos = 0.45                                   # eye 55 -> 0.7*55 + 27 = 65.5
    o, e = tick(out, pipe, cam, 110.0)
    assert o["level"] == 1 and not o["motors"] and e["lvl"] == "CAUTION"
    assert out.voice.last == "LEVEL1" and not o["buzzer"]           # buzzer waits for the words
    o, e = tick(out, pipe, cam, 114.0)                                # clip over: 1 s / 1 s pattern
    assert o["buzzer"]
    assert e["lcd1"] == " INTERVENTION 1 "
    cam.perclos = 0.80                                   # eye 20 -> 41
    o, e = tick(out, pipe, cam, 120.0)
    assert o["level"] == 2 and o["motors"] and e["alert"] is True and out.voice.last == "LEVEL2"
    o, e = tick(out, pipe, cam, 124.2)                   # after the 3.4 s warning: buzzer back
    assert o["buzzer"] and o["motors"]
    cam.perclos = 0.05
    o, e = tick(out, pipe, cam, 130.0)
    assert o["level"] == 0 and not o["buzzer"] and not o["motors"]
    out.stop()


def test_dms_drowsy_forces_level_2():
    out, pipe, cam, link = make()
    cam.state = "drowsy"                                 # microsleep while PERCLOS still low
    o, e = tick(out, pipe, cam, 1.0)
    assert o["level"] == 2 and o["drowsy"]


def test_no_esp32_uses_eye_only():
    out, pipe, cam, link = make()
    link.drv = None
    o, e = tick(out, pipe, cam, 1.0)
    assert o["no_driving"] and e["score"] == 95


def test_no_face_data_yet():
    out, pipe, cam, link = make()
    cam.perclos = None
    o, e = tick(out, pipe, cam, 1.0)
    assert e["part"] is True and e["score"] == 90        # driving only, as alertness.h


def test_voice_ir_and_strip_through_the_app():
    out, pipe, cam, link = make()
    pipe.frame_id = 1
    cam.face["night"] = True
    o, e = tick(out, pipe, cam, 0.0)
    assert o["ir"] is True                                     # IR auto: on at night
    pipe.frame_id = 2
    tick(out, pipe, cam, 3.5)
    assert out.voice.last == "READY"
    cam.perclos = 0.80                                         # -> Level 2
    pipe.frame_id = 3
    o, e = tick(out, pipe, cam, 10.0)
    assert o["level"] == 2 and out.voice.last == "LEVEL2"
    assert out.iv.mute_until > 10.0 and not o["buzzer"] and o["motors"]   # buzzer holds for the words
    assert out.strip.dev.last[1:] == [(63, 0, 0)] * 13         # alarm bar, 25 % brightness
    tick(out, pipe, cam, 20.0)                                 # frames stopped at 10 s
    tick(out, pipe, cam, 26.0)
    assert out.voice.last == "CAMERA_LOST"
    assert out.strip.dev.last[0] in ((63, 0, 0), (0, 0, 0))   # status pixel: red blink
    out.stop()
