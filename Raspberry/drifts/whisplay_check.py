"""Whisplay HAT bring-up check: run it the first time the HAT is on the Pi.

    python -m drifts.whisplay_check            # software checks only (safe anywhere)
    python -m drifts.whisplay_check --hw       # + screen, backlight, LED, speaker, mic, button
    python -m drifts.whisplay_check --hw --sim # the hardware steps against a simulated HAT (PNGs)

Part 1 (software) needs no HAT: SPI and I2C enabled, PiSugar's driver
installed, the Python modules, the sound card, user groups, nothing else
holding the HAT, the font, the voice clips and the [whisplay] settings.

Part 2 (--hw) drives each part of the HAT and asks you what you saw. The
button test needs a short press, a long press (hold) and a double press.
A report is written to whisplay_check_report.txt.

Each check is PASS, WARN (works, but look at it) or FAIL (fix before use).
"""

from __future__ import annotations

import argparse
import grp
import importlib
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from drifts.config import WhisplayConfig, load_config
from drifts.ui_model import RGB
from drifts.whisplay_ui import (CLIPS, ButtonDecoder, Canvas, SimBoard, check_action, font_name,
                                hat_conflicts, icon)

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
CARD_NAMES = ("whisplaysound", "wm8960", "es8389")
INK, MUTED, BG = RGB["lcd_text"], RGB["lcd_muted"], RGB["lcd_bg"]
GREEN, YELLOW, RED = RGB["green"], RGB["yellow"], RGB["red"]


class Report:
    def __init__(self):
        self.rows: list = []

    def add(self, name: str, result: str, detail: str = "") -> str:
        self.rows.append((name, result, detail))
        mark = {PASS: "\033[32mPASS\033[0m", WARN: "\033[33mWARN\033[0m", FAIL: "\033[31mFAIL\033[0m",
                SKIP: "SKIP"}[result]
        print(f"  {mark}  {name:<26} {detail}")
        return result

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r[1] == FAIL)

    def text(self) -> str:
        lines = [f"DRIFTS Whisplay check  {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
        lines += [f"{r:<5} {n:<26} {d}" for n, r, d in self.rows]
        counts = {k: sum(1 for x in self.rows if x[1] == k) for k in (PASS, WARN, FAIL, SKIP)}
        lines += ["", "  ".join(f"{k} {v}" for k, v in counts.items())]
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- part 1: software
def _read(path: str) -> str:
    try:
        return Path(path).read_text(errors="ignore").replace("\0", "").strip()
    except OSError:
        return ""


def sound_card() -> Optional[str]:
    """The Whisplay card's line in /proc/asound/cards, or None."""
    for line in _read("/proc/asound/cards").splitlines():
        if any(n in line.lower() for n in CARD_NAMES):
            return line.strip()
    return None


DRIVER_KO = "snd-soc-whisplay-soundcard.ko"


def sound_card_hint(modules: Path = Path("/lib/modules"), running: Optional[str] = None) -> str:
    """Why there's no sound card. PiSugar's installer builds the driver only for the
    kernel running at the time, so a kernel update (apt full-upgrade) loses it."""
    running = running or os.uname().release
    built = sorted(p.parts[-6] for p in modules.glob(f"*/kernel/sound/soc/codecs/{DRIVER_KO}")) \
        if modules.exists() else []
    fix = "cd ~/Whisplay && sudo bash install_driver.sh (answer y), then sudo reboot"
    if built and running not in built:
        return (f"the driver was built for kernel {', '.join(built)} but {running} is running "
                f"(a kernel update): {fix}")
    return f"not found: {fix}"


def shutdown_allowed() -> Optional[bool]:
    """May this user run 'systemctl poweroff' through sudo without a password? None: no sudo."""
    if not shutil.which("sudo"):
        return None
    cmd = ["sudo", "-n", "-l", shutil.which("systemctl") or "/usr/bin/systemctl", "poweroff"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def software_checks(rep: Report, cfg: WhisplayConfig) -> None:
    model = _read("/proc/device-tree/model")
    rep.add("Board", PASS if "Raspberry Pi" in model else WARN, model or "not a Raspberry Pi")
    rep.add("SPI enabled", PASS if Path("/dev/spidev0.0").exists() else FAIL,
            "/dev/spidev0.0" if Path("/dev/spidev0.0").exists() else "sudo raspi-config nonint do_spi 0, then reboot")
    rep.add("I2C enabled (audio codec)", PASS if Path("/dev/i2c-1").exists() else WARN,
            "/dev/i2c-1" if Path("/dev/i2c-1").exists() else "PiSugar's installer enables it; reboot after installing")
    drv = Path(cfg.driver_dir).expanduser() / "whisplay.py"
    rep.add("PiSugar driver", PASS if drv.exists() else FAIL,
            str(drv) if drv.exists() else "WHISPLAY=1 bash deploy/install.sh (clones ~/Whisplay)")
    for mod, fix in (("spidev", "sudo apt install python3-spidev"), ("gpiod", "sudo apt install python3-libgpiod"),
                     ("PIL", "sudo apt install python3-pil"), ("numpy", "sudo apt install python3-numpy")):
        try:
            importlib.import_module(mod)
            rep.add(f"Python module {mod}", PASS)
        except ImportError:
            rep.add(f"Python module {mod}", FAIL if mod in ("spidev", "gpiod", "PIL") else WARN, fix)
    card = sound_card()
    rep.add("Sound card", PASS if card else FAIL, card or sound_card_hint())
    try:
        names = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except (KeyError, OSError):
        names = set()
    missing = [g for g in ("gpio", "spi", "audio") if g not in names]
    rep.add("User groups", PASS if not missing else WARN,
            "gpio, spi, audio" if not missing else f"missing {', '.join(missing)}: sudo usermod -aG "
            f"{','.join(missing)} $USER, then log out and in")
    others = hat_conflicts()
    rep.add("HAT not in use", PASS if not others else FAIL,
            "nothing else holds it" if not others else ", ".join(others) +
            ": sudo systemctl disable --now whisplay-daemon chatbot")
    f = font_name()
    rep.add("Font", PASS if "Inter" in f else WARN, f if "Inter" in f else f"{f} (sudo apt install fonts-inter)")
    wavs = sorted(CLIPS.glob("*.wav"))
    rep.add("Voice clips", PASS if len(wavs) >= 10 else FAIL, f"{len(wavs)} in Voice/clips")
    rep.add("aplay / arecord", PASS if shutil.which("aplay") and shutil.which("arecord") else FAIL,
            "" if shutil.which("aplay") else "sudo apt install alsa-utils")
    try:
        acts = [check_action(a) for a in (cfg.button_short, cfg.button_long, cfg.button_double)]
        rep.add("Button settings", PASS, f"short={acts[0]}  long={acts[1]}  double={acts[2]}")
    except ValueError as exc:
        rep.add("Button settings", FAIL, str(exc))
    if check_action(cfg.button_hold) == "shutdown":
        allowed = shutdown_allowed()
        rep.add("Shutdown by button hold", PASS if allowed else WARN if allowed is None else FAIL,
                f"hold {cfg.hold_s:.0f} s: powers off safely" if allowed else
                "can't tell (no sudo here)" if allowed is None else
                "not allowed yet: WHISPLAY=1 bash deploy/install.sh (adds /etc/sudoers.d/drifts-poweroff)")
    rep.add("[whisplay] enabled", PASS if cfg.enabled else WARN,
            "the car program drives the HAT" if cfg.enabled else "set enabled = true in config.toml after this check")


# ---------------------------------------------------------------- part 2: test screens
def screen_bars():
    """Colour bars, a corner marker in each corner and 'TOP' (orientation)."""
    c = Canvas()
    cols = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
    bw = 240 / len(cols)
    for i, col in enumerate(cols):
        c.rect(i * bw, 60, (i + 1) * bw, 170, fill=col)
    for i in range(8):
        g = round(255 * i / 7)
        c.rect(i * 30, 170, (i + 1) * 30, 200, fill=(g, g, g))
    c.text(120, 30, "TOP", 22, 700, INK, "mm")
    c.text(120, 222, "Screen test", 16, 600, INK, "mm")
    c.text(120, 244, "Bars, grey steps, 4 corner dots", 11, 400, MUTED, "mm")
    for x, y in ((22, 22), (218, 22), (22, 258), (218, 258)):
        c.circle(x, y, 5, fill=RED)
    return c.done()


def screen_message(title: str, lines=(), ico: Optional[str] = None, col=INK):
    c = Canvas()
    if ico:
        icon(c, ico, 120, 78, col, 40)
    c.text(120, 132, title, 20, 700, INK, "mm")
    for i, ln in enumerate(lines):
        c.text(120, 164 + i * 22, ln, 14, 400, MUTED, "mm")
    return c.done()


def screen_button(done: dict, remaining: float):
    c = Canvas()
    c.text(120, 34, "Button test", 20, 700, INK, "mm")
    rows = (("short", "Short press"), ("long", "Hold 1.5 s"), ("double", "Double press"))
    for i, (key, label) in enumerate(rows):
        y = 84 + i * 46
        ok = done.get(key)
        c.rrect(20, y - 18, 220, y + 18, 12, fill=(30, 50, 34) if ok else RGB["lcd_surface"])
        if ok:
            icon(c, "check", 44, y, GREEN, 18, bg=(30, 50, 34))
        else:
            c.circle(44, y, 8, outline=MUTED, width=1.6)
        c.text(64, y, label, 15, 600, INK if ok else MUTED, "lm")
    last = done.get("_last")
    c.text(120, 228, f"Last: {last}" if last else "Press the side button", 13, 500, INK, "mm")
    c.text(120, 252, f"{remaining:.0f} s left", 11, 400, MUTED, "mm")
    return c.done()


# ---------------------------------------------------------------- part 2: hardware steps
class Asker:
    """y/n answers from the terminal; --yes answers yes, no terminal answers skip."""

    def __init__(self, auto: Optional[bool]):
        self.auto = auto

    def __call__(self, question: str) -> Optional[bool]:
        if self.auto is not None:
            print(f"        {question} [auto: {'y' if self.auto else 'n'}]")
            return self.auto
        if not sys.stdin.isatty():
            return None
        while True:
            a = input(f"        {question} [y/n] ").strip().lower()
            if a in ("y", "yes", "n", "no"):
                return a.startswith("y")


def _verdict(ans: Optional[bool], name: str, rep: Report, fail_hint: str, ok_detail: str = "") -> None:
    if ans is None:
        rep.add(name, SKIP, "no answer (run in a terminal)")
    else:
        rep.add(name, PASS if ans else FAIL, ok_detail if ans else fail_hint)


def record_level(device: str, seconds: float, path: Path) -> Optional[float]:
    """Records from the HAT's microphones; returns the peak level (0-1), or None if recording failed."""
    try:
        subprocess.run(["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "1",
                        "-d", str(int(math.ceil(seconds))), str(path)], check=True, timeout=seconds + 10)
        with wave.open(str(path), "rb") as w:
            raw = w.readframes(w.getnframes())
    except (OSError, subprocess.SubprocessError, wave.Error):
        return None
    import array
    a = array.array("h", raw)
    return max((abs(x) for x in a), default=0) / 32768.0


def hardware_checks(rep: Report, cfg: WhisplayConfig, board, ask: Callable, sim: bool, out: Path,
                    button_s: float = 25.0, press_script: Optional[list] = None) -> None:
    shots = {}
    audio = {"dev": cfg.audio_device}          # the sound device that works (the speaker test may switch it)

    def show(name, img):
        board.show(img)
        shots[name] = img

    board.set_backlight(cfg.brightness)
    show("1_bars", screen_bars())
    print("\n  Screen: colour bars, grey steps, a red dot in each corner, TOP at the top.")
    time.sleep(0 if sim else 1)
    _verdict(ask("Do you see the bars, the four dots and TOP the right way up?"), "Screen", rep,
             "no picture: check the HAT is fully seated; turned: set rotate = 90 / 180 / 270 (clockwise) "
             "under [whisplay] in config.toml")

    print("\n  Backlight: 100 % -> 10 % -> 100 %.")
    for pct in (100, 60, 30, 10, 30, 60, 100):
        board.set_backlight(pct)
        time.sleep(0 if sim else 0.4)
    board.set_backlight(cfg.brightness)
    _verdict(ask("Did the screen dim and brighten again?"), "Backlight", rep,
             "on/off only: older boards have no dimming (fine)", "dims smoothly")

    print("\n  RGB LED: red, green, blue, white, off.")
    for name, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255)), ("white", (255, 255, 255))):
        show(f"2_led_{name}", screen_message(f"LED {name}", ("Look at the LED",), "sparkle", rgb))
        board.set_rgb(*rgb)
        time.sleep(0 if sim else 1.0)
    board.set_rgb(0, 0, 0)
    _verdict(ask("Did the LED show red, green, blue, then white, in that order?"), "RGB LED", rep,
             "wrong order: note which colour showed for which; the driver's pins are R25 G24 B23")

    clip = CLIPS / "10_TEST.wav"
    show("3_speaker", screen_message("Speaker", ("Playing the test clip",), "speaker"))
    print("\n  Speaker: \"This is a DRIFTS voice test\".")
    if sim:
        rep.add("Speaker", SKIP, "simulated")
    elif not clip.exists() or not shutil.which("aplay"):
        rep.add("Speaker", FAIL, "clip or aplay missing")
    else:
        dev = cfg.audio_device
        r = subprocess.run(["aplay", "-q", "-D", dev, str(clip)], timeout=20)
        if r.returncode and dev != "default":
            print(f"        aplay -D {dev} failed: trying the default device (PiSugar's asound.conf, 48 kHz)")
            dev = "default"
            r = subprocess.run(["aplay", "-q", "-D", dev, str(clip)], timeout=20)
            if not r.returncode:
                rep.add("Speaker device", WARN, f'{cfg.audio_device} fails, "default" works: set audio_device = '
                        '"default" under [whisplay] in config.toml (DRIFTS also switches by itself)')
        audio["dev"] = dev
        if r.returncode:
            rep.add("Speaker", FAIL, f"aplay -D {dev} failed: check `aplay -l` and /etc/asound.conf "
                    "(re-run PiSugar's install_driver.sh)")
        else:
            _verdict(ask("Did you hear the test sentence clearly?"), "Speaker", rep,
                     "silent: speaker switch on the HAT set to onboard? amixer -c whisplaysound sset speaker 90%")

    show("4_mic", screen_message("Microphone", ("Say something now", "3 seconds"), "mic"))
    print("\n  Microphones: say something for 3 seconds.")
    if sim:
        rep.add("Microphone", SKIP, "simulated")
    else:
        dev = audio["dev"]
        level = record_level(dev, 3, out / "whisplay_check_mic.wav")
        if level is None:
            rep.add("Microphone", FAIL, f"arecord -D {dev} failed")
        else:
            rep.add("Microphone", PASS if level > 0.05 else WARN,
                    f"peak {level:.2f}" + ("" if level > 0.05 else ": very quiet; amixer -c whisplaysound sset mic 80%"))
            subprocess.run(["aplay", "-q", "-D", dev, str(out / "whisplay_check_mic.wav")], timeout=10)

    # button: short, long, double, shown live on the screen
    print(f"\n  Button: do a short press, a long press (hold 1.5 s) and a double press ({button_s:.0f} s).")
    dec = ButtonDecoder(cfg.long_s, cfg.double_s or 0.4)
    board.on_button(lambda: dec.press(time.monotonic()), lambda: dec.release(time.monotonic()))
    done: dict = {}
    t0 = time.monotonic()
    script = list(press_script or [])
    while time.monotonic() - t0 < button_s:
        now = time.monotonic()
        while script and now - t0 >= script[0][0]:         # simulated presses (tests, --sim)
            _, kind = script.pop(0)
            getattr(dec, kind)(now)
        for ev in dec.poll(now):
            done[ev] = True
            done["_last"] = ev
            print(f"        button: {ev}")
        show("5_button", screen_button(done, button_s - (now - t0)))
        if all(done.get(k) for k in ("short", "long", "double")):
            break
        time.sleep(0.05)
    for key in ("short", "long", "double"):
        rep.add(f"Button {key} press", PASS if done.get(key) else FAIL,
                "" if done.get(key) else "not seen: check the HAT is seated; nothing else may hold the HAT")
    show("6_done", screen_message("Check done", (f"{rep.failed} failed", "See the terminal"), "check" if not rep.failed
                                  else "warning", GREEN if not rep.failed else YELLOW))
    if sim:
        out.mkdir(parents=True, exist_ok=True)
        for name, img in shots.items():
            img.save(out / f"whisplay_check_{name}.png")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m drifts.whisplay_check", description=__doc__.splitlines()[0])
    ap.add_argument("--hw", action="store_true", help="also test the screen, LED, speaker, mic and button")
    ap.add_argument("--sim", action="store_true", help="hardware steps against a simulated HAT (writes PNGs)")
    ap.add_argument("--yes", action="store_true", help="answer yes to every question (unattended)")
    ap.add_argument("--out", default=".", help="folder for the report (and --sim PNGs)")
    ap.add_argument("--button-s", type=float, default=25.0, help="time allowed for the button test")
    args = ap.parse_args(argv)
    cfg = load_config().whisplay
    out = Path(args.out)
    rep = Report()
    print("DRIFTS Whisplay check\n\n Part 1: software")
    software_checks(rep, cfg)
    if args.hw:
        print("\n Part 2: hardware")
        board = None
        if args.sim:
            board = SimBoard()
        else:
            try:
                from drifts.whisplay_ui import HatBoard
                board = HatBoard(cfg.driver_dir, cfg.rotate, cfg.joystick)
            except Exception as exc:
                rep.add("Open the HAT", FAIL, str(exc))
        if board is not None:
            script = None
            if args.sim:   # a short, a long and a double press
                script = [(0.2, "press"), (0.3, "release"), (1.0, "press"), (2.7, "release"),
                          (3.2, "press"), (3.3, "release"), (3.45, "press"), (3.55, "release")]
            try:
                hardware_checks(rep, cfg, board, Asker(True if args.yes or args.sim else None), args.sim, out,
                                args.button_s, script)
            finally:
                if hasattr(board, "close") and not args.sim:
                    time.sleep(2)
                    board.close()
    path = out / "whisplay_check_report.txt"
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(rep.text())
    print(f"\n {rep.failed} failed. Report: {path}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
