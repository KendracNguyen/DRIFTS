"""Tokyo hardware tool: test each output, or run a scripted demo.

    python -m tokyo test buzzer      # 1 s on/off pattern for 6 s
    python -m tokyo test motors      # 0.5 s on / 0.3 s off for 5 s
    python -m tokyo test strip       # every score band and status pattern (same steps as LEDSTRIP on the ESP32)
    python -m tokyo test lcd         # boot screens, then each level
    python -m tokyo test ir          # IR illuminator on 3 s
    python -m tokyo test voice       # every phrase in Voice/phrases.json
    python -m tokyo say 4            # one phrase by id or name (like SAY:4 on the ESP32)
    python -m tokyo test all
    python -m tokyo demo             # score 90 -> 60 -> 35 -> 85: all outputs together
    add --sim to run anywhere without hardware (prints what would happen)

    python -m tokyo run [options]    # the full app: camera, face, score, outputs, dashboard
                                     # (see tokyo/app.py; --help for options)
"""

from __future__ import annotations

import argparse
import sys
import time

from tokyo.config import load_config
from tokyo.interventions import Interventions
from tokyo.lcd import Lcd, compose
from tokyo.ledstrip import LedStrip, StripState
from tokyo.outputs import Buzzer, IrLight, Motors
from tokyo.voice import Voice, VoiceInputs


def _bar(frame) -> str:
    """One character per LED, for --sim output: G / Y / R, . = off."""
    def ch(px):
        r, g, b = px
        if r == g == b == 0:
            return "."
        return "R" if g < 0.25 * r else "Y" if r > g else "G"
    return "".join(ch(px) for px in frame)


STRIP_STEPS = [   # (label, StripState kwargs) -- same order as the ESP32's LEDSTRIP command
    ("score 100 green, full bar", dict(score=100)),
    ("score 85 green", dict(score=85)),
    ("score 70 green, 3/4 bar", dict(score=70)),
    ("score 69 yellow", dict(score=69)),
    ("score 40 yellow, 1/2 bar", dict(score=40)),
    ("score 39 red", dict(score=39)),
    ("score 10 red, 1/4 bar", dict(score=10)),
    ("score 5 critical: whole bar fast red", dict(score=5)),
    ("score 85 partial: half brightness", dict(score=85, partial=True)),
    ("alarm: whole bar red 1 s / 1 s", dict(score=30, alarm=True)),
    ("status: connection failure (red slow)", dict(score=85, links_ok=False)),
    ("status: system issue (yellow slow)", dict(score=85, system_issue=True)),
]


def _run(seconds: float, step) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        step(time.monotonic() - t0)
        time.sleep(0.05)


def test_buzzer(cfg, sim):
    bz = Buzzer(cfg.buzzer, sim)
    print(f"Buzzer GPIO {cfg.buzzer.pin} ({cfg.buzzer.kind}): {cfg.buzzer.on_s:g} s on / {cfg.buzzer.off_s:g} s off, 6 s")
    last = None
    def step(t):
        nonlocal last
        on = (t % (cfg.buzzer.on_s + cfg.buzzer.off_s)) < cfg.buzzer.on_s
        if on != last:
            bz.set(on)
            print(f"  {t:4.1f} s  buzzer {'ON' if on else 'off'}")
            last = on
    _run(6.0, step)
    bz.set(False)


def test_motors(cfg, sim):
    m = Motors(cfg.motors, sim)
    c = cfg.motors
    print(f"Motors GPIO {c.pin}: duty {c.duty:.0%} at {c.pwm_hz} Hz, {c.on_s:g} s on / {c.off_s:g} s off, 5 s")
    last = None
    def step(t):
        nonlocal last
        on = (t % (c.on_s + c.off_s)) < c.on_s
        if on != last:
            m.set(on)
            print(f"  {t:4.1f} s  motors {'ON' if on else 'off'}")
            last = on
    _run(5.0, step)
    m.set(False)


def test_strip(cfg, sim):
    s = LedStrip(cfg.strip, sim)
    c = cfg.strip
    where = f"SPI {c.spi_device} (GPIO 10)" if c.backend == "spi" else f"GPIO {c.pin} (PIO)"
    print(f"LED strip on {where}: {c.count} LEDs, brightness {c.brightness:.0%}")
    for label, kw in STRIP_STEPS:
        print(f"  {label}")
        t0 = time.monotonic()
        while time.monotonic() - t0 < (0.2 if sim else 1.5):
            s.update(StripState(alarm_since=t0, **kw), time.monotonic(), force=True)
            time.sleep(0.05)
        if sim:
            print(f"    [{_bar(s.dev.last)}]  ~{s.amps:.2f} A")
    s.off()


def test_ir(cfg, sim):
    ir = IrLight(cfg.ir, sim)
    print(f"IR illuminator GPIO {cfg.ir.pin}: duty {cfg.ir.duty:.0%} at {cfg.ir.pwm_hz} Hz, on 3 s "
          "(look through a phone camera: the LEDs glow faintly purple)")
    ir.set(True)
    time.sleep(0 if sim else 3.0)
    ir.set(False)


def test_voice(cfg, sim, only=None):
    v = Voice(cfg.voice, sim)
    if v.missing:
        print(f"  missing clips: {', '.join(v.missing)} -- run python Voice/make_clips.py")
    if not v.player_ok:
        print(f"  '{cfg.voice.player}' not found: sudo apt install alsa-utils")
    names = [only] if only else list(v.phrases)
    for name in names:
        if not v.say(name):
            print(f"  unknown phrase {name!r}: use an id or name from Voice/phrases.json")
            return
        queued, v.dir.pending = v.dir.pending, None
        v._play(queued, time.monotonic())
        print(f"  {v.last:<12} {v.phrases[v.last]['text']}")
        while v.playing(time.monotonic()) and not sim:
            time.sleep(0.05)


def test_lcd(cfg, sim):
    lcd = Lcd(cfg.lcd.bus, cfg.lcd.addresses, sim=sim)
    print(f"LCD on I2C bus {cfg.lcd.bus}: {'found at 0x%02X' % lcd.addr if lcd.present else 'not found'}")
    for t, score, lvl, alarm in ((0, 90, 0, False), (3, 90, 0, False), (6, 87, 0, False), (6, 62, 1, False),
                                 (8, 62, 1, False), (6, 38, 2, True), (8, 38, 2, True), (10, 38, 2, True)):
        l1, l2 = compose(t, score, lvl, alarm)
        lcd.show(l1, l2)
        print(f"  |{l1}|\n  |{l2}|\n")
        time.sleep(0.8 if not sim else 0)


def demo(cfg, sim):
    bz, m = Buzzer(cfg.buzzer, sim), Motors(cfg.motors, sim)
    iv = Interventions(bz, m, cfg.buzzer, cfg.motors, cfg.interventions)
    strip = LedStrip(cfg.strip, sim)
    lcd = Lcd(cfg.lcd.bus, cfg.lcd.addresses, sim=sim)
    voice = Voice(cfg.voice, sim)
    script = [(0, 90), (6, 60), (16, 35), (28, 85), (40, None)]
    print("Demo: score 90 -> 60 (Intervention 1) -> 35 (Intervention 2) -> 85 (recovers)")
    t0, last_print = time.monotonic(), -1
    try:
        while True:
            t = time.monotonic() - t0
            score = next(sc for until, sc in reversed(script) if t >= until)
            if score is None:
                break
            lvl = iv.tick(t, score)
            spoke = voice.update(VoiceInputs(level=lvl), t)
            if spoke:
                print(f"  {t:4.0f} s  voice: \"{voice.phrases[spoke[0]]['text']}\"")
                if lvl >= 1:
                    iv.mute_buzzer(t + spoke[1] + 0.25)
            strip.update(StripState(score=score, level=lvl, alarm=lvl >= 2, alarm_since=t0 + iv.levels.since), time.monotonic())
            l1, l2 = compose(t + 5.0, score, lvl, alarm=lvl >= 2)
            lcd.show(l1, l2)
            if int(t) != last_print:
                last_print = int(t)
                print(f"  {t:4.0f} s  score {score:3}  level {lvl}  buzzer {'ON ' if bz.on else 'off'}  "
                      f"motors {'ON ' if m.on else 'off'}  |{l1}|{l2}|" + (f"  [{_bar(strip.dev.last)}]" if sim else ""))
            time.sleep(0.05)
    finally:
        iv.stop()
        strip.off()
        voice.stop()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["run"]:
        from tokyo.app import run
        return run(argv[1:])
    if argv[:1] == ["say"]:
        sim = "--sim" in argv
        rest = [a for a in argv[1:] if a != "--sim"]
        if len(rest) != 1:
            print("usage: python -m tokyo say <id|NAME> [--sim]")
            return 2
        test_voice(load_config(), sim, only=rest[0].upper() if not rest[0].isdigit() else int(rest[0]))
        return 0
    ap = argparse.ArgumentParser(prog="python -m tokyo", description="Tokyo (Pi 5) hardware tool")
    ap.add_argument("cmd", choices=["test", "demo"])
    ap.add_argument("what", nargs="?", default="all", choices=["buzzer", "motors", "strip", "lcd", "ir", "voice", "all"])
    ap.add_argument("--sim", action="store_true", help="no hardware: print what would happen")
    ap.add_argument("--config", help="path to tokyo.toml")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.cmd == "demo":
        demo(cfg, args.sim)
        return 0
    tests = {"buzzer": test_buzzer, "motors": test_motors, "strip": test_strip, "lcd": test_lcd,
             "ir": test_ir, "voice": test_voice}
    for name in (tests if args.what == "all" else [args.what]):
        tests[name](cfg, args.sim)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
