"""Whisplay HAT display logic (drifts/whisplay_ui.py) without the HAT:
screen frames, LED colours, voice rules, button, and the app wiring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from drifts.config import WhisplayConfig, load_config
from drifts.ui_model import (BANDS, CAUTION, HIGH, NORMAL, UNAVAILABLE, band_color, band_for, state_from_live)
from drifts.whisplay_ui import (VIEWS, H, W, ButtonDecoder, SimBoard, VoiceDirector,
                                WhisplayUI, demo_state, font_name, gallery, led_color, render, rgb565,
                                state_from_status)


def st(score=90.0, lvl="OK", alert=False, obd="polling", linked=True, **kw):
    return state_from_status({"score": score, "lvl": lvl, "alert": alert, "obd": obd, "drv_score": 80, **kw},
                             linked=linked)


def test_state_from_esp32_status():
    s = st(62, "CAUTION")
    assert s.score == 62 and s.level == 1 and s.tier == 1 and s.obd_ok and s.esp_linked
    assert st(80, "OK", alert=True).tier == 2                       # the drowsy alert counts as Level 2
    stale = state_from_status({"score": 40, "lvl": "LOW", "alert": True}, linked=True, fresh=False)
    assert stale.score is None and not stale.alert and not stale.esp_linked


def test_alertness_bands_match_the_esp32():
    # the three design states, from the score alone (thresholds of alertness.h: 70 / 50)
    assert [band_for(s) for s in (92, 66, 34)] == [NORMAL, CAUTION, HIGH]
    assert band_for(70) == NORMAL and band_for(69.9) == CAUTION and band_for(50) == CAUTION and band_for(49.9) == HIGH
    assert band_for(None) == UNAVAILABLE and band_for(95, alert=True) == HIGH
    assert band_for(72, level="CAUTION") == CAUTION           # the ESP32's hysteresis wins
    assert [st(s, l).band for s, l in ((92, "OK"), (66, "CAUTION"), (34, "LOW"))] == [NORMAL, CAUTION, HIGH]
    assert st(None).band == UNAVAILABLE                        # missing score: never green
    assert band_color(NORMAL) == (0x39, 0xD3, 0x53) and band_color(CAUTION) == (0xFF, 0xC8, 0x3D)
    assert band_color(HIGH) == (0xFF, 0x59, 0x64)
    assert BANDS[NORMAL]["title"] == "ALERTNESS NORMAL" and BANDS[HIGH]["subtitle"] == "Pull over safely"


def test_firmware_thresholds_unchanged():
    from drifts import ui_model
    src = (Path(__file__).resolve().parents[2] / "Esp32/drifts_esp32/alertness.h").read_text()
    assert f"okAbove = {ui_model.OK_ABOVE:.1f}f" in src and f"lowBelow = {ui_model.LOW_BELOW:.1f}f" in src


def test_gauge_colour_follows_the_band():
    # the ring's right-hand side (3 o'clock) is lit for any score above 25
    for score, lvl, col in ((92, "OK", (0x39, 0xD3, 0x53)), (66, "CAUTION", (0xFF, 0xC8, 0x3D)),
                            (34, "LOW", (0xFF, 0x59, 0x64))):
        img = render(st(score, lvl, alert=lvl == "LOW"), 0.2, "score")
        px = img.getpixel((120 + 60, 106))
        assert max(abs(a - b) for a, b in zip(px, col)) < 40, (score, px)


def test_led_colours():
    assert led_color(st(), 0.1, booted=False) == (0, 0, 120)        # blue while starting
    assert led_color(st(), 0.1) == (0, 200, 40)                     # green: linked, OK
    assert led_color(st(62, "CAUTION"), 0.1) == (255, 170, 0)       # Level 1
    l2 = [led_color(st(30, "LOW", alert=True), t) for t in (0.5, 1.5)]
    assert l2 == [(255, 0, 0), (0, 0, 0)]                           # 1 s on / 1 s off with the buzzer
    lost = [led_color(st(obd="scanning"), t) for t in (0.2, 0.7)]
    assert lost == [(255, 0, 0), (0, 0, 0)]                         # connection lost: slow red blink


def test_render_every_view_and_state():
    states = (st(), st(62, "CAUTION"), st(30, "LOW", alert=True), st(5, "LOW"), st(None, linked=False),
              st(85, part=True), demo_state(92), demo_state(66), demo_state(None, calibrated=False, face_found=False),
              demo_state(92, calibrating="profile", calib_prompt="Turn your head to the left", calib_step=4,
                         calib_total=9, calib_progress=0.3),
              demo_state(92, calibrating="test", calib_prompt="Yawn", calib_step=1, calib_total=6))
    ctxs = ({}, {"calib_done": "profile"}, {"awake": {"left": 4.0, "window": 10.0}},
            {"awake": {"left": 4.0, "window": 10.0, "listening": True}}, {"awake": {"confirmed": True}})
    for state in states:
        for view in VIEWS:
            for ctx in ctxs:
                assert render(state, 0.2, view, ctx=ctx).size == (W, H)
    assert set(VIEWS) == {"welcome", "calibration", "score", "awake"}      # status only: no menus, no assistant
    assert render(st(), 0.2, 3).size == (W, H)                            # an old page number shows the score
    assert render(st(), 0.2, booted=False).size == (W, H)
    assert len(rgb565(render(st(), 0.2))) == W * H * 2


def test_inter_font_when_installed():
    name = font_name()
    assert name.endswith((".otf", ".ttf")) or name == "built-in"


def test_gallery_writes_every_screen(tmp_path):
    sheet = gallery(tmp_path)
    assert sheet.exists() and len(list(tmp_path.glob("whisplay_*.png"))) == 11


def test_demo_data_is_labelled():
    assert demo_state(92).demo and not st().demo


def test_state_from_live_status():
    now = 1000.0
    status = {"esp": {"link": "BLE DRIFTS", "updated": now - 1, "score": 66.0, "lvl": "CAUTION", "alert": False,
                      "obd": "polling", "drv_score": 80.0, "rpm": 742.0, "spd": 0.0, "thr": 15.0, "fw": "v20"},
              "eye": {"state": "open", "perclos": 0.047}, "face": {"blink_rate": 16.0},
              "pose": {"enabled": True, "found": True, "yaw": 6.0, "pitch": 1.0, "calibrated": True,
                       "calib_progress": 1.0}, "error": None, "camera_note": None}
    s = state_from_live(status, now=now)
    assert s.band == CAUTION and s.vision and s.camera_ok and s.eyes == "open" and s.blink_rate == 16
    assert s.throttle == 15 and s.pose_label == "Neutral" and not s.demo
    d = s.to_dict()
    assert d["alertness"] == {"score": 66.0, "state": "caution", "valid": True, "partial": False, "level": 1}
    assert d["vision"]["perclos"] == 0.047 and d["vehicle"]["rpm"] == 742 and d["devices"]["camera"] == "online"
    stale = state_from_live({**status, "esp": {**status["esp"], "updated": now - 30}}, now=now)
    assert stale.score is None and stale.band == UNAVAILABLE and not stale.esp_linked
    sim = state_from_live({**status, "esp": {**status["esp"], "link": "simulated"}}, now=now)
    assert sim.demo                                                         # simulated ESP32 = DEMO on screen


def test_welcome_then_score_by_itself():
    ui = WhisplayUI(WhisplayConfig(enabled=True, voice=False), board=SimBoard(), sim=True)
    ui.tick(st(), 0.0)
    assert ui.view(st(), booted=False, now=0.0) == "welcome"
    ui.tick(st(), 5.0)
    assert ui.view(st(), booted=True, now=5.0) == "score"                   # no button press needed
    # the automatic road-pose pass at start-up does not take over the screen
    assert ui.view(demo_state(92, calibrated=False, calib_progress=0.4), booted=True, now=5.0) == "score"


def test_calibration_from_the_app_and_complete():
    ui = WhisplayUI(WhisplayConfig(enabled=True), board=SimBoard(), sim=True)
    ui.tick(st(), 0.0)
    quick = demo_state(92, calibrating="quick", calibrated=False, calib_progress=0.4)
    ui.tick(quick, 5.0)
    assert ui.view(quick, booted=True, now=5.0) == "calibration"
    assert ui.view(demo_state(34, calibrating="quick"), booted=True, now=5.0) == "score"  # an alert wins
    done = demo_state(92, calibrated=True)
    ui.tick(done, 6.0)                                                      # finished: "complete" for 3 s
    assert ui.view(done, booted=True, now=6.5) == "calibration" and ui.context(6.5)["calib_done"] == "quick"
    assert ui.voice.pending == "CALIBRATED" or ui.speaker.last == "CALIBRATED"
    assert ui.view(done, booted=True, now=9.5) == "score"
    prof = demo_state(92, calibrating="profile", calib_prompt="Close your eyes", calib_step=2, calib_total=9)
    ui.tick(prof, 10.0)
    assert ui.view(prof, booted=True, now=10.0) == "calibration"


def test_calibration_status_from_the_pipeline(tmp_path):
    from drifts.esp_link import SimLink
    from drifts.live import Pipeline, build_parser
    from test_privacy import FakeFaceCamera
    args = build_parser().parse_args(["--esp", "sim", "--trips", str(tmp_path / "t.json"), "--profile", "nobody"])
    p = Pipeline(FakeFaceCamera(), SimLink(), args)
    p.cal.done = True
    assert p.calib_ui({}, 100.0) is None                                    # nothing asked: nothing shown
    p.recalibrate()
    c = p.calib_ui({}, p.calib_asked + 1)
    assert c["mode"] == "quick" and c["progress"] == 0.0
    p.cal.done = True
    assert p.calib_ui({}, p.calib_asked + 2) is None and p.calib_asked is None
    p.start_guided("calibrate", "kendra")
    c = p.calib_ui({}, 200.0)
    assert c["mode"] == "profile" and c["step"] == 1 and c["total"] >= 9 and c["prompt"]
    s = state_from_live({"esp": {}, "calib_ui": c, "pose": {"enabled": True, "calib_progress": 1.0}}, now=1.0)
    assert s.calibrating == "profile" and s.calib_step == 1 and s.calib_prompt == c["prompt"]


def test_awake_check_after_a_long_alert():
    cfg = WhisplayConfig(enabled=True, awake_after_s=20.0, awake_window_s=10.0)
    ui = WhisplayUI(cfg, board=SimBoard(), sim=True)
    alert = st(30, "LOW", alert=True)
    ui.tick(st(), 0.0)
    ui.tick(alert, 5.0)                                                     # alert starts
    ui.tick(alert, 20.0)
    assert ui.view(alert, True, 20.0) == "score"                            # not yet
    ui.tick(alert, 25.5)
    assert ui.view(alert, True, 25.5) == "awake" and ui.voice.pending in (None, "AWAKE")
    assert "AWAKE" in [n for _, n in ui.speaker.played] or ui.voice.pending == "AWAKE"
    ui.handle("short", alert, 27.0)                                         # backup: the button confirms
    assert ui.presses[-1][2] == "awake confirmed" and ui.context(27.5)["awake"] == {"confirmed": True}
    assert ui.view(alert, True, 30.0) == "score"
    ui.tick(st(), 31.0)                                                     # alert over: check resets
    assert ui._alert_since is None


def test_awake_check_from_a_voice_reply_and_dev_button():
    heard = []
    cfg = WhisplayConfig(enabled=True, voice=False, button_short="awake_check")
    ui = WhisplayUI(cfg, board=SimBoard(), sim=True, hooks={"listen": lambda: heard.append(1)})
    ui.tick(st(), 0.0)
    ui.handle("short", st(), 5.0)                                           # dev button shows it on demand
    assert ui.awake_active(5.1) and heard == [1] and ui.context(5.1)["awake"]["listening"]
    ui.confirm_awake(6.0)                                                   # what a speech recogniser calls
    assert not ui.awake_active(6.1) and ui.view(st(), True, 6.1) == "awake"  # "Thank you"
    assert ui.view(st(), True, 9.0) == "score"
    assert not ui.awake_active(5.0 + 10.1)


def test_frame_redrawn_only_on_change():
    board = SimBoard()
    shown = []
    board.show = shown.append
    ui = WhisplayUI(WhisplayConfig(enabled=True, voice=False), board=board, sim=True)
    for t in (5.0, 5.25, 5.5):
        ui.tick(st(), t)
    assert len(shown) == 1
    ui.tick(st(80), 5.75)
    assert len(shown) == 2


def test_rgb565_byte_order():
    from PIL import Image
    red = Image.new("RGB", (1, 1), (255, 0, 0))
    blue = Image.new("RGB", (1, 1), (0, 0, 255))
    assert rgb565(red) == b"\xf8\x00" and rgb565(blue) == b"\x00\x1f"


def test_voice_rules():
    v = VoiceDirector(repeat_s=60, lost_s=5, ready_after_s=3)
    assert v.update(st(), 0.0, False) is None
    assert v.update(st(), 3.0, False) == "READY"
    assert v.update(st(62, "CAUTION"), 10.0, False) == "LEVEL1"
    assert v.update(st(30, "LOW", alert=True), 11.0, True) is None          # waits while a clip plays
    assert v.update(st(30, "LOW", alert=True), 14.0, False) == "LEVEL2"
    assert v.update(st(30, "LOW", alert=True), 75.0, False) == "LEVEL2"     # repeats each minute
    v.acknowledge(76.0)
    assert v.update(st(30, "LOW", alert=True), 130.0, False) is None        # acknowledged: no repeat for 60 s
    assert v.update(st(30, "LOW", alert=True), 140.0, False) == "LEVEL2"    # ack expired: repeats again
    assert v.update(st(), 201.0, False) == "RECOVERED"


def test_voice_lost_links_once():
    v = VoiceDirector(lost_s=5, ready_after_s=0)
    v.update(st(), 0.0, False)                                              # READY
    v.update(st(obd="scanning"), 1.0, False)
    assert v.update(st(obd="scanning"), 7.0, False) == "OBD_LOST"
    assert v.update(st(obd="scanning"), 60.0, False) is None                # said once
    v.update(st(linked=False), 61.0, False)
    assert v.update(st(linked=False), 67.0, False) == "ESP_LOST"


def test_button_decoder():
    b = ButtonDecoder(long_s=1.5)
    b.press(0.0)
    assert b.release(0.3) == "short"
    b.press(1.0)
    assert b.release(3.0) == "long"
    assert b.release(4.0) is None


def test_button_never_silences_the_alert():
    ui = WhisplayUI(WhisplayConfig(enabled=True), board=SimBoard(), sim=True)
    ui.tick(st(), 0.0)
    ui.tick(st(), 5.0)                                                       # past the boot screen
    ui.handle("short", st(), 5.1)
    assert ui.presses[-1][2] == "none"                                       # driver presses do nothing
    ui.handle("short", st(30, "LOW", alert=True), 6.0)                      # during an alert: acknowledge
    assert ui.voice.ack_until > 6.0 and ui.banner == "Acknowledged"
    ui.tick(st(30, "LOW", alert=True), 6.1)
    assert ui.view(st(30, "LOW", alert=True), True, 6.1) == "score"
    assert ui.board.rgb in ((255, 0, 0), (0, 0, 0))                          # LED still flashing red


def test_app_config_section():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.toml")
    assert cfg.whisplay.enabled is False and cfg.whisplay.audio_device == "plughw:CARD=whisplaysound"
    assert cfg.ble.status_char_uuid.startswith("DF720002")


def test_ble_status_notifications_update_state():
    from drifts.ble_client import BleClient
    c = BleClient(load_config().ble)
    c._on_status_notify(None, bytearray(b'{"score":63,"lvl":"CAUTION","alert":false,"obd":"polling","dscore":71}'))
    assert c.status["score"] == 63 and c.status["drv_score"] == 71 and c.status_t > 0


# ---------------------------------------------------------------- the side button
def test_button_short_long_double():
    b = ButtonDecoder(long_s=1.5, double_s=0.4)
    b.press(0.0); b.release(0.1)
    assert b.poll(0.3) == []                                      # might still become a double
    assert b.poll(0.6) == ["short"]
    b.press(1.0); b.release(1.1); b.press(1.3); b.release(1.4)
    assert b.poll(1.5) == ["double"]
    b.press(2.0)
    assert b.poll(3.0) == [] and b.poll(3.6) == ["long"]          # fires while still held
    assert b.release(4.0) is None and b.poll(5.0) == []
    b.press(6.0); b.release(6.1); b.press(6.8); b.release(6.9)     # second press too late: two shorts
    assert b.poll(7.5) == ["short", "short"]
    b.press(8.0); b.release(8.1); b.press(8.3)                     # a short press, then a hold
    assert b.poll(8.5) == []                                       # still held: nothing decided yet
    assert b.poll(9.9) == ["short", "long"]
    nb = ButtonDecoder(long_s=1.5, double_s=0.0)                   # no double press: no wait
    nb.press(0.0)
    assert nb.release(0.2) == "short"


def test_button_actions_are_configurable():
    from drifts.whisplay_ui import check_action
    cfg = WhisplayConfig(enabled=True, voice=False, button_short="repeat_voice", button_long="brightness",
                         button_double="calibrate")
    calls = []
    ui = WhisplayUI(cfg, board=SimBoard(), sim=True, hooks={"calibrate": lambda: calls.append("cal")})
    ok = st()
    ui.handle("short", ok, 5.0)
    assert ui.presses[-1][2] == "nothing to repeat"
    ui.handle("long", ok, 5.2)
    assert ui.brightness == 100 and ui.board.backlight == 100
    ui.handle("double", ok, 5.3)
    assert calls == ["cal"]
    with pytest.raises(ValueError):
        check_action("explode")
    # older config.toml files: page / assistant actions are retired, not errors
    old = WhisplayUI(WhisplayConfig(button_short="next_page", button_long="page:devices", button_double="ai"),
                     board=SimBoard(), sim=True)
    assert set(old.actions.values()) == {"none"}


def test_alert_press_always_acknowledges():
    cfg = WhisplayConfig(enabled=True, voice=False, button_short="brightness", button_double="calibrate")
    ui = WhisplayUI(cfg, board=SimBoard(), sim=True)
    alert = st(30, "LOW", alert=True)
    ui.handle("double", alert, 10.0)
    assert ui.presses[-1][2] == "acknowledge" and ui.brightness == cfg.brightness
    ui.handle("short", alert, 11.0)
    assert ui.presses[-1][2] == "acknowledge"


def test_button_events_flow_through_tick():
    board = SimBoard()
    ui = WhisplayUI(WhisplayConfig(enabled=True, voice=False, double_s=0.4, button_short="brightness"),
                    board=board, sim=True)
    ui.tick(st(), 0.0)
    board._press(); board._release()                               # real callbacks, monotonic time
    import time as _t
    _t.sleep(0.5)
    ui.tick(st(), _t.monotonic())
    assert ui.presses[-1][1] == "short" and ui.brightness == 100


def test_joystick_centre_is_the_button():
    ui = WhisplayUI(WhisplayConfig(enabled=True, voice=False, button_short="brightness"), board=SimBoard(), sim=True)
    ui.board._joy("down"); ui.board._joy("center")
    ui.tick(st(), 5.0)
    assert [p[2] for p in ui.presses] == ["brightness 100"]


def test_close_turns_the_hat_off():
    ui = WhisplayUI(WhisplayConfig(enabled=True), board=SimBoard(), sim=True)
    ui.close()
    assert ui.board.closed


def test_bringup_check_runs_simulated(tmp_path):
    from drifts.whisplay_check import main
    main(["--hw", "--sim", "--out", str(tmp_path), "--button-s", "6"])
    report = (tmp_path / "whisplay_check_report.txt").read_text()
    for name in ("Screen", "RGB LED", "Button short press", "Button long press", "Button double press"):
        line = next(l for l in report.splitlines() if name in l)
        assert line.startswith("PASS"), line
    assert len(list(tmp_path.glob("whisplay_check_*.png"))) >= 6
