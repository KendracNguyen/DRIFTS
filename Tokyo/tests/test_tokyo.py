"""Tokyo phase 1 (Pi 5 outputs) without hardware: config, sysfs PWM, buzzer and
motors, LED strip rendering and current cap, intervention levels, LCD screens."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tokyo.config import Config, InterventionConfig, StripConfig, load_config
from tokyo.interventions import Interventions, LevelTracker, pattern_on
from tokyo.lcd import COLS, Lcd, compose
from tokyo.ledstrip import (GREEN, OFF, RED, YELLOW, StripState, apply_limits, bar_lit, estimate_current_a,
                            render)
from tokyo.outputs import Buzzer, IrLight, Motors, SysfsPwm
from tokyo.voice import VoiceDirector, VoiceInputs, load_phrases


# ------------------------------------------------------------------ config
def test_config_defaults_and_override(tmp_path):
    cfg = Config()
    # Rev 9 wiring: motors 13, buzzer 18, strip SPI MOSI 10, IR 12
    assert (cfg.motors.pin, cfg.buzzer.pin, cfg.strip.pin, cfg.ir.pin) == (13, 18, 10, 12)
    assert cfg.strip.count == 14 and cfg.strip.backend == "spi" and cfg.buzzer.kind == "active"
    assert cfg.buzzer.on_s == 1.0 and cfg.buzzer.off_s == 1.0 and cfg.motors.duty == 1.0
    f = tmp_path / "tokyo.toml"
    f.write_text("[motors]\nduty = 1.0\n[strip]\nbrightness = 0.1\n[lcd]\naddresses = [63]\n")
    c = load_config(f)
    assert c.motors.duty == 1.0 and c.strip.brightness == 0.1 and c.lcd.addresses == (63,)
    f.write_text("[motors]\nspeed = 3\n")
    with pytest.raises(ValueError):
        load_config(f)


# ------------------------------------------------------------------ PWM
def fake_pwm_root(tmp_path, npwm=4):
    chip = tmp_path / "pwmchip2"
    chip.mkdir()
    (chip / "npwm").write_text(str(npwm))
    (chip / "export").write_text("")
    for ch in range(npwm):                     # as if udev had created them on export
        d = chip / f"pwm{ch}"
        d.mkdir()
        for n in ("period", "duty_cycle", "enable"):
            (d / n).write_text("0")
    return chip


def test_sysfs_pwm_finds_chip_and_writes(tmp_path):
    chip = fake_pwm_root(tmp_path)
    pwm = SysfsPwm(12, root=tmp_path)
    assert pwm.chip == chip and pwm.channel == 0
    pwm.set(20000, 0.6)
    d = chip / "pwm0"
    assert (d / "period").read_text() == "50000"          # 20 kHz = 50 us
    assert (d / "duty_cycle").read_text() == "30000"
    assert (d / "enable").read_text() == "1"
    pwm.off()
    assert (d / "duty_cycle").read_text() == "0" and (d / "enable").read_text() == "0"
    assert SysfsPwm(13, root=tmp_path).channel == 1


def test_sysfs_pwm_rejects_non_pwm_pin_and_missing_chip(tmp_path):
    with pytest.raises(ValueError):
        SysfsPwm(17, root=tmp_path)
    with pytest.raises(FileNotFoundError):
        SysfsPwm(12, root=tmp_path)                       # no chip present


def test_motors_and_buzzer_sim():
    cfg = Config()
    m = Motors(cfg.motors, sim=True)
    m.set(True)
    assert m.on and m.pwm.duty == 1.0 and m.pwm.hz == 20000
    m.set(False)
    assert not m.on and m.pwm.duty == 0.0
    b = Buzzer(cfg.buzzer, sim=True)                      # active: on/off
    b.set(True)
    assert b.on and b.dev.duty == 1.0
    cfg.buzzer.kind = "passive"
    p = Buzzer(cfg.buzzer, sim=True)
    p.set(True)
    assert p.pwm.hz == 2700 and p.pwm.duty == 0.5
    cfg.buzzer.kind = "loud"
    with pytest.raises(ValueError):
        Buzzer(cfg.buzzer, sim=True)


# ------------------------------------------------------------------ LED strip
# Score -> LEDs lit and colour, for the 13-LED bar.
STRIP_TABLE = [(100, 13, GREEN), (85, 11, GREEN), (70, 10, GREEN),
               (69, 10, YELLOW), (55, 8, YELLOW), (40, 7, YELLOW),
               (39, 6, RED), (25, 5, RED), (10, 3, RED)]


@pytest.mark.parametrize("score,lit,colour", STRIP_TABLE)
def test_render_bands_match_esp32(score, lit, colour):
    f = render(StripState(score=score), StripConfig(), now=0.1)
    assert len(f) == 14 and f[0] == GREEN
    bar = f[1:]
    assert bar[:lit] == [colour] * lit and bar[lit:] == [OFF] * (13 - lit)


def test_render_bar_never_shrinks_as_score_rises():
    assert all(bar_lit(s + 1, 13) >= bar_lit(s, 13) for s in range(10, 100))


def test_render_critical_partial_and_no_score():
    cfg = StripConfig()
    on, off = render(StripState(score=5), cfg, 0.1), render(StripState(score=5), cfg, 0.2)
    assert on[1:] == [RED] * 13 and off[1:] == [OFF] * 13          # 4 Hz
    half = render(StripState(score=85, partial=True), cfg, 0.1)
    assert half[1] == (0, 127, 20) and half[1:].count(OFF) == 2
    assert render(StripState(score=None), cfg, 0.1)[1:] == [OFF] * 13


def test_render_alarm_flash_in_phase_with_buzzer():
    cfg = StripConfig()
    on = render(StripState(score=30, level=2, alarm=True, alarm_since=10.0), cfg, now=10.5)
    off = render(StripState(score=30, level=2, alarm=True, alarm_since=10.0), cfg, now=11.5)
    assert on[1:] == [RED] * 13 and off[1:] == [OFF] * 13


def test_render_status_pixel():
    cfg = StripConfig()
    lost = [render(StripState(links_ok=False, system_issue=True), cfg, t)[0] for t in (0.1, 0.6)]
    assert lost == [RED, OFF]                       # connection failure wins
    assert render(StripState(system_issue=True), cfg, 0.1)[0] == YELLOW
    assert render(StripState(), cfg, 0.6)[0] == GREEN


def test_ir_modes():
    cfg = Config()
    ir = IrLight(cfg.ir, sim=True)
    assert ir.want(night=True) and not ir.want(night=False)       # auto
    ir.set(True)
    assert ir.on and ir.pwm.hz == 25000 and ir.pwm.duty == 1.0
    cfg.ir.mode = "off"
    assert not ir.want(night=True)


# ------------------------------------------------------------------ voice
def test_phrases_and_clips_exist():
    ph = load_phrases()
    assert ph["LEVEL2"]["id"] == 4 and ph["READY"]["id"] == 1
    clips = Path(__file__).resolve().parents[2] / "Voice" / "clips"
    for name, p in ph.items():
        assert (clips / f"{p['id']:02d}_{name}.wav").exists(), name


def test_voice_director_levels():
    d = VoiceDirector(repeat_s=60, link_lost_s=5)
    assert d.update(VoiceInputs(), 0.0, False) is None
    assert d.update(VoiceInputs(), 3.0, False) == "READY"
    assert d.update(VoiceInputs(level=1), 10.0, False) == "LEVEL1"
    assert d.update(VoiceInputs(level=2), 10.1, True) is None          # waits for the clip
    assert d.update(VoiceInputs(level=2), 13.0, False) == "LEVEL2"
    assert d.update(VoiceInputs(level=2), 43.0, False) is None
    assert d.update(VoiceInputs(level=2), 73.5, False) == "LEVEL2"     # repeats each minute
    assert d.update(VoiceInputs(level=1), 74.0, False) is None
    assert d.update(VoiceInputs(level=0), 75.0, False) == "RECOVERED"


def test_voice_director_links_and_calibration():
    d = VoiceDirector(link_lost_s=5)
    d.update(VoiceInputs(), 0.0, False)
    d.update(VoiceInputs(), 3.0, False)                                 # READY
    assert d.update(VoiceInputs(driving_ok=False, camera_ok=False), 20.0, False) is None   # never seen
    d.update(VoiceInputs(driving_ok=True, camera_ok=True, calibrated=False), 21.0, False)
    assert d.update(VoiceInputs(driving_ok=True, camera_ok=True, calibrated=True), 22.0, False) == "CALIBRATED"
    d.update(VoiceInputs(driving_ok=False, camera_ok=True), 23.0, False)
    assert d.update(VoiceInputs(driving_ok=False, camera_ok=True), 28.0, False) == "OBD_LOST"
    d.update(VoiceInputs(driving_ok=False, camera_ok=False), 29.0, False)
    assert d.update(VoiceInputs(driving_ok=False, camera_ok=False), 34.0, False) == "CAMERA_LOST"
    assert d.update(VoiceInputs(driving_ok=False, camera_ok=False), 90.0, False) is None    # once


def test_buzzer_holds_off_while_speaking():
    cfg = Config()
    b, m = Buzzer(cfg.buzzer, sim=True), Motors(cfg.motors, sim=True)
    iv = Interventions(b, m, cfg.buzzer, cfg.motors, InterventionConfig(min_hold_s=0))
    iv.tick(0.0, 40)
    assert b.on and m.on
    iv.mute_buzzer(3.5)
    iv.tick(0.2, 40)
    assert not b.on and m.on                         # words over the alarm; motors continue
    iv.tick(4.1, 40)
    assert b.on                                      # rejoins the 1 s / 1 s pattern
    iv.stop()


def test_current_cap():
    white = [(255, 255, 255)] * 160
    assert abs(estimate_current_a(white) - 9.6) < 0.01         # why the cap exists
    capped = apply_limits(white, brightness=1.0, max_current_a=1.5)
    assert estimate_current_a(capped) <= 1.5
    dim = apply_limits([(255, 0, 0)] * 10, brightness=0.25, max_current_a=1.5)
    assert dim[0] == (63, 0, 0)


# ------------------------------------------------------------------ levels
def test_level_tracker_bands_hysteresis_hold():
    t = LevelTracker(InterventionConfig(min_hold_s=3, hysteresis=5))
    assert t.update(0, 90) == 0
    assert t.update(1, 65) == 1                     # down into Level 1 immediately
    assert t.update(2, 45) == 2
    assert t.update(3, 52) == 2                     # needs 55 to leave Level 2
    assert t.update(6, 56) == 1                     # recovered, and held >= 3 s
    assert t.update(7, 80) == 1                     # held: < 3 s since Level 1 started
    assert t.update(9.1, 80) == 0
    assert t.update(10, 90, alarm=True) == 2        # confirmed drowsy forces Level 2


def test_pattern_timing():
    assert pattern_on(0.5, 0.0, 1.0, 1.0) and not pattern_on(1.5, 0.0, 1.0, 1.0)
    assert pattern_on(2.2, 0.0, 1.0, 1.0)


def test_interventions_stack():
    cfg = Config()
    b, m = Buzzer(cfg.buzzer, sim=True), Motors(cfg.motors, sim=True)
    iv = Interventions(b, m, cfg.buzzer, cfg.motors, InterventionConfig(min_hold_s=0))
    iv.tick(0.0, 90)
    assert not b.on and not m.on
    iv.tick(10.0, 60)                               # Level 1: buzzer only
    assert b.on and not m.on
    iv.tick(11.5, 60)
    assert not b.on                                 # 1 s on, 1 s off
    iv.tick(20.0, 40)                               # Level 2: buzzer + motors
    assert b.on and m.on
    iv.tick(20.6, 40)
    assert b.on and not m.on                        # motor 0.5 s on / 0.3 s off
    iv.tick(30.0, 90)
    assert not b.on and not m.on
    iv.stop()


# ------------------------------------------------------------------ LCD
def test_lcd_screens():
    assert compose(0, 90, 0) == ("   Welcome to   ", "     DRIFTS     ")
    assert compose(3, 90, 0) == ("   Drive Safe   ", " " * 16)
    assert compose(10, 87, 0) == ("ALERT     87/100", "   Drive Safe   ")
    assert compose(10, 42, 0, partial=True)[0] == "ALERT    42/100*"
    # Line 2 rotates every 2 s: frame = int(t / 2) % frames
    assert compose(12, 62, 1) == (" INTERVENTION 1 ", "Signs of Fatigue")      # frame 0
    assert compose(10, 62, 1)[1] == "  Score 62/100  "                          # frame 1
    assert compose(12, 38, 2, alarm=True)[1] == "Worsening Signs "              # 6 % 3 = 0
    assert compose(14, 38, 2, alarm=True)[1] == "  Score 38/100  "              # 7 % 3 = 1
    assert compose(10, 38, 2, alarm=True)[1] == "PULL OVER SAFELY"              # 5 % 3 = 2
    assert compose(10, 90, 0, alarm=True)[0] == " INTERVENTION 2 "         # alarm while score OK


def test_lcd_all_screens_16_chars():
    for t in range(0, 20):
        for score in (None, -5, 0, 49, 50, 100, 130):
            for lvl in (0, 1, 2):
                for alarm in (False, True):
                    l1, l2 = compose(t, score, lvl, alarm, partial=True)
                    assert len(l1) == COLS and len(l2) == COLS


def test_lcd_writes_only_changes():
    lcd = Lcd(sim=True)
    assert lcd.show("ALERT     87/100", "   Drive Safe   ") == 20     # non-blank characters
    assert lcd.show("ALERT     86/100", "   Drive Safe   ") == 1
    assert lcd.show("ALERT     86/100", "   Drive Safe   ") == 0


def test_voice_finds_the_usb_speaker():
    from tokyo.voice import find_usb_card
    out = ("**** List of PLAYBACK Hardware Devices ****\n"
           "card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]\n"
           "card 2: Device [USB2.0 Device], device 0: USB Audio [USB Audio]\n")
    assert find_usb_card(out) == "plughw:CARD=Device,DEV=0"
    assert find_usb_card("card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM\n") is None
