"""Record REAL repeatability heat-map data from a running drifts.live (laptop camera or the Pi).

Each test: 40 s of normal behaviour (fills the 60 s windows), then 60 s in ten 6 s boxes
that go from baseline to fatigue. The screen tells you what to do in each box.
One row of 10 values per test goes into real_heat.json; plot with plot_heat.py.

Laptop (from the repo's Raspberry folder):
    python -m drifts.live --face mesh --camera 0 --esp sim --mirror --http-port 8090
    (glasses row: add --glasses on)
Then, in a second terminal (from the repo folder):
    python Tools/scenario_trials/heat_recorder.py --row eyes --test 1 --url http://localhost:8090
"""
import argparse, json, statistics as st, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOX, BOXES, WARMUP = 6.0, 10, 40.0
# row -> (title, face part(s) scored, what to do in each 6 s box: boxes 1-2 are baseline)
N = ("Normal", None)
ROWS = {
    "head":  ("Head Nods & Droop (pitch)", ["pitch"], [N, N, ("Nod once", 1.0), N, ("Nod once", 1.0), ("Nod once", 1.0),
              ("Nod twice", 3.0), ("Nod twice", 3.0), ("Head down", 3.0), ("Head down", 4.0)]),
    "eyes":  ("Eye Closure (EAR / PERCLOS)", ["ear", "blink"], [N, N, ("Slow blinks", 4.0), ("Slow blinks", 4.0),
              ("Close eyes", 1.0), ("Close eyes", 1.0), ("Close eyes", 2.0), ("Close eyes", 2.0), ("Close eyes", 3.0), ("Close eyes", 4.0)]),
    "yawns": ("Yawning (MAR)", ["yawn", "mar"], [N, N, ("Mouth slightly open", 4.0), N, ("Yawn", 2.0), N,
              ("Yawn", 3.0), N, ("Yawn", 3.0), ("Yawn", 4.0)]),
    "glasses": ("Head Turn Off-Road with Glasses (yaw)", ["yaw"], [N, N, ("Glance left", 1.0), N, ("Look left", 2.0),
              ("Look right", 2.0), ("Look left", 3.0), ("Look right", 3.0), ("Look away", 4.0), ("Look away", 5.0)]),
}


def say(text):
    """Speak without blocking (Windows built-in voice; macOS 'say'; else just print)."""
    import subprocess, sys
    try:
        if sys.platform == "win32":
            cmd = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                   f"$s.Rate = 1; $s.Speak('{text}')")
            subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "darwin":
            subprocess.Popen(["say", text])
    except OSError:
        pass


def beep(freq, ms, times=1):
    try:
        import winsound
        for _ in range(times):
            winsound.Beep(freq, ms)
    except (ImportError, RuntimeError):
        print("\a" * times, end="", flush=True)


def spoken(cue):
    text, hold = cue
    if hold is None: return "Normal"
    if text == "Slow blinks": return "Slow blinks"
    return f"{text}, {hold:g} second" + ("" if hold == 1 else "s")


def get(url):
    with urllib.request.urlopen(url + "/status.json", timeout=2) as r:
        return json.loads(r.read())


def part(face, keys):
    parts = (face or {}).get("parts") or {}
    vals = [((parts.get(k) or {}).get("d")) for k in keys]
    vals = [v for v in vals if v is not None]
    return 100.0 * max(vals) if vals else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--row", required=True, choices=list(ROWS))
    ap.add_argument("--test", type=int, required=True, help="1-4")
    ap.add_argument("--url", default="http://localhost:8090")
    ap.add_argument("--out", default=str(HERE / "real_heat.json"))
    a = ap.parse_args()
    title, keys, cues = ROWS[a.row]
    s = get(a.url)
    if not s.get("face"):
        raise SystemExit("drifts.live is running without --face mesh: no face data.")
    print(f"\n{title}: test {a.test}\nSit 50-70 cm from the camera, face it, good light.")
    input("Press Enter, then act NORMALLY for the 40 s warm-up...")
    end = time.time() + WARMUP
    said = False
    while time.time() < end:
        if not said and end - time.time() <= 3.0:
            say("Starting. " + spoken(cues[0])); said = True
        f = (get(a.url).get("face") or {})
        print(f"\r  warm-up {end - time.time():3.0f} s   face {'OK  ' if f.get('found') else 'LOST'}", end="", flush=True)
        time.sleep(0.5)
    vals, alerts, missing = [], [], 0
    print("\nListen for the voice and beeps: HIGH beep = start, two LOW beeps = stop / open your eyes.")
    for b in range(BOXES):
        cue = cues[b]
        nxt = cues[b + 1] if b + 1 < BOXES else None
        print(f"\n  box {b + 1:2d}/10  >>> {spoken(cue).upper()} <<<")
        if cue[1] is not None: beep(1200, 180)                       # start
        samples, fired, t0, stopped, announced = [], False, time.time(), cue[1] is None, nxt is None
        while time.time() - t0 < BOX:
            el = time.time() - t0
            if not stopped and el >= cue[1]:
                beep(500, 150, 2); stopped = True                    # stop / open eyes
            if not announced and el >= BOX - 3.0:
                say("Next: " + spoken(nxt)); announced = True
            try:
                s = get(a.url)
            except OSError:
                missing += 1; time.sleep(0.25); continue
            f = s.get("face") or {}
            v = part(f, keys)
            if v is not None: samples.append(v)
            fired = fired or bool(s.get("drowsy_sent")) or bool((s.get("esp") or {}).get("alert"))
            live = (f"eyes {str(f.get('eye_state')):8s} closed {f.get('closed_for') or 0:3.1f}s" if a.row == "eyes" else
                    f"yawning {bool(f.get('yawning'))!s:5s} MAR {f.get('mar') or 0:4.2f}" if a.row == "yawns" else
                    f"pitch drop {f.get('pitch_drop') or 0:+5.1f} deg nods {f.get('nods') or 0}" if a.row == "head" else
                    f"yaw from neutral {f.get('yaw_rel') or 0:+5.1f} deg")
            print(f"\r      {BOX - el:3.1f}s  {live}  score {v if v is not None else 0:5.1f}   ", end="", flush=True)
            time.sleep(0.25)
        vals.append(round(st.mean(samples), 1) if samples else 0.0)
        if fired: alerts.append(b)
        print(f"\r      box score {vals[-1]:5.1f}{'   ALERT' if fired else ''}" + " " * 50)
    data = json.loads(Path(a.out).read_text()) if Path(a.out).exists() else {}
    tests = data.setdefault(a.row, [None] * 4)
    while len(tests) < a.test: tests.append(None)
    tests[a.test - 1] = [vals, alerts]
    Path(a.out).write_text(json.dumps(data, indent=1))
    print(f"\nSaved {a.row} test {a.test} -> {a.out}" + (f"  ({missing} missed polls)" if missing else ""))


if __name__ == "__main__":
    main()
