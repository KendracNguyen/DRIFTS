"""Tokyo hardware tool: test each output, or run a scripted demo.

    python -m tokyo test buzzer      # 1 s on/off pattern for 6 s
    python -m tokyo test motors      # 0.5 s on / 0.3 s off for 5 s
    python -m tokyo test strip       # status, score bar sweep, alarm flash
    python -m tokyo test lcd         # boot screens, then each level
    python -m tokyo test all
    python -m tokyo demo             # score 90 -> 60 -> 35 -> 85: all outputs together
    add --sim to run anywhere without hardware (prints what would happen)
"""

from __future__ import annotations

import argparse
import sys
import time

from tokyo.config import load_config
from tokyo.interventions import Interventions
from tokyo.lcd import Lcd, compose
from tokyo.ledstrip import LedStrip, StripState
from tokyo.outputs import Buzzer, Motors


def _bar(frame) -> str:
    """One character per 4 LEDs, for --sim output."""
    def ch(px):
        r, g, b = px
        if r == g == b == 0:
            return "."
        return "R" if g < 0.25 * r else "A" if r > g else "G"     # amber = (255, 120, 0)
    return "".join(ch(frame[i]) for i in range(0, len(frame), 4))


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
    print(f"LED strip GPIO {cfg.strip.pin}: {cfg.strip.count} LEDs, brightness {cfg.strip.brightness:.0%}, "
          f"cap {cfg.strip.max_current_a} A")
    phases = [("status OK + bar sweep 0->100", None), ("Level 1 amber bar", None), ("Level 2 red bar", None),
              ("alarm flash", None), ("link missing (green blinks)", None)]
    for name, _ in phases:
        print(f"  {name}")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.0:
            t = time.monotonic() - t0
            st = StripState(score=t / 2 * 100 if name.startswith("status") else 60 if "1" in name else 35,
                            level=0 if name.startswith("status") else 1 if "1" in name else 2,
                            alarm=name == "alarm flash", alarm_since=t0, links_ok="missing" not in name)
            s.update(st, time.monotonic(), force=True)
            time.sleep(0.05)
        if sim:
            print(f"    [{_bar(s.dev.last)}]  ~{s.amps:.2f} A")
    s.off()


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tokyo", description="Tokyo (Pi 5) hardware tool")
    ap.add_argument("cmd", choices=["test", "demo"])
    ap.add_argument("what", nargs="?", default="all", choices=["buzzer", "motors", "strip", "lcd", "all"])
    ap.add_argument("--sim", action="store_true", help="no hardware: print what would happen")
    ap.add_argument("--config", help="path to tokyo.toml")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.cmd == "demo":
        demo(cfg, args.sim)
        return 0
    tests = {"buzzer": test_buzzer, "motors": test_motors, "strip": test_strip, "lcd": test_lcd}
    for name in (tests if args.what == "all" else [args.what]):
        tests[name](cfg, args.sim)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
