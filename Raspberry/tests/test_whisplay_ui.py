"""Whisplay HAT display logic (drifts/whisplay_ui.py) without the HAT:
screen frames, LED colours, voice rules, button, and the app wiring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from drifts.config import WhisplayConfig, load_config
from drifts.whisplay_ui import (H, W, ButtonDecoder, SimBoard, UiState, VoiceDirector, WhisplayUI, bar_fraction,
                                led_color, render, rgb565, state_from_status)


def st(score=90.0, lvl="OK", alert=False, obd="polling", linked=True, **kw):
    return state_from_status({"score": score, "lvl": lvl, "alert": alert, "obd": obd, "drv_score": 80, **kw},
                             linked=linked)


def test_state_from_esp32_status():
    s = st(62, "CAUTION")
    assert s.score == 62 and s.level == 1 and s.tier == 1 and s.obd_ok and s.esp_linked
    assert st(80, "OK", alert=True).tier == 2                       # the drowsy alert counts as Level 2
    stale = state_from_status({"score": 40, "lvl": "LOW", "alert": True}, linked=True, fresh=False)
    assert stale.score is None and not stale.alert and not stale.esp_linked


def test_bar_bands():
    assert bar_fraction(100) == 1.0 and bar_fraction(70) == 0.75
    assert bar_fraction(40) == 0.5 and bar_fraction(10) == 0.25 and bar_fraction(5) == 1.0
    assert all(bar_fraction(s + 1) >= bar_fraction(s) for s in range(10, 100))


def test_led_colours():
    assert led_color(st(), 0.1, booted=False) == (0, 0, 120)        # blue while starting
    assert led_color(st(), 0.1) == (0, 200, 40)                     # green: linked, OK
    assert led_color(st(62, "CAUTION"), 0.1) == (255, 170, 0)       # Level 1
    l2 = [led_color(st(30, "LOW", alert=True), t) for t in (0.5, 1.5)]
    assert l2 == [(255, 0, 0), (0, 0, 0)]                           # 1 s on / 1 s off with the buzzer
    lost = [led_color(st(obd="scanning"), t) for t in (0.2, 0.7)]
    assert lost == [(255, 0, 0), (0, 0, 0)]                         # connection lost: slow red blink


def test_render_every_page_and_state():
    for state in (st(), st(62, "CAUTION"), st(30, "LOW", alert=True), st(5, "LOW"), st(None, linked=False),
                  st(85, part=True)):
        for page in (0, 1, 2):
            img = render(state, 0.2, page)
            assert img.size == (W, H)
    assert render(st(), 0.2, booted=False).size == (W, H)
    assert len(rgb565(render(st(), 0.2))) == W * H * 2


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


def test_button_never_silences_and_pages():
    ui = WhisplayUI(WhisplayConfig(enabled=True), board=SimBoard(), sim=True)
    ui.tick(st(), 0.0)
    ui.tick(st(), 5.0)                                                       # past the boot screen
    ui.handle("short", st(), 5.1)
    assert ui.page == 1
    ui.handle("short", st(30, "LOW", alert=True), 6.0)                      # during an alert: acknowledge
    assert ui.voice.ack_until > 6.0 and ui.banner == "Acknowledged"
    ui.tick(st(30, "LOW", alert=True), 6.1)
    assert ui.page == 0                                                      # an alert shows the score page
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
