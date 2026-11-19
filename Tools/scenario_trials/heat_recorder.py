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
ROWS = {
    "head":  ("Head Nods & Droop (pitch)", ["pitch"], ["normal"] * 2 + ["nod once", "normal", "nod once", "nod once",
              "nod twice", "nod twice", "head down 3 s", "head down 4 s"]),
    "eyes":  ("Eye Closure (EAR / PERCLOS)", ["ear", "blink"], ["normal"] * 2 + ["slow blinks", "slow blinks",
              "close eyes 1 s", "close eyes 1 s", "close eyes 2 s", "close eyes 2 s", "close eyes 3 s", "close eyes 4 s"]),
    "yawns": ("Yawning (MAR)", ["yawn", "mar"], ["normal"] * 2 + ["mouth slightly open", "normal", "YAWN (hold 2 s)",
              "normal", "YAWN (hold 3 s)", "normal", "YAWN (hold 3 s)", "YAWN (hold 4 s)"]),
    "glasses": ("Head Turn Off-Road with Glasses (yaw)", ["yaw"], ["normal"] * 2 + ["glance left 1 s", "normal",
              "look left 2 s", "look right 2 s", "look left 3 s", "look right 3 s", "look away 4 s", "look away 5 s"]),
}


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
    while time.time() < end:
        f = (get(a.url).get("face") or {})
        print(f"\r  warm-up {end - time.time():3.0f} s   face {'OK  ' if f.get('found') else 'LOST'}", end="", flush=True)
        time.sleep(0.5)
    vals, alerts, missing = [], [], 0
    for b in range(BOXES):
        print(f"\n  box {b + 1:2d}/10  >>> {cues[b].upper()} <<<")
        samples, fired, t_end = [], False, time.time() + BOX
        while time.time() < t_end:
            try:
                s = get(a.url)
            except OSError:
                missing += 1; time.sleep(0.25); continue
            v = part(s.get("face"), keys)
            if v is not None: samples.append(v)
            fired = fired or bool(s.get("drowsy_sent")) or bool((s.get("esp") or {}).get("alert"))
            time.sleep(0.25)
        vals.append(round(st.mean(samples), 1) if samples else 0.0)
        if fired: alerts.append(b)
        print(f"      score {vals[-1]:5.1f}{'   ALERT' if fired else ''}")
    data = json.loads(Path(a.out).read_text()) if Path(a.out).exists() else {}
    tests = data.setdefault(a.row, [None] * 4)
    while len(tests) < a.test: tests.append(None)
    tests[a.test - 1] = [vals, alerts]
    Path(a.out).write_text(json.dumps(data, indent=1))
    print(f"\nSaved {a.row} test {a.test} -> {a.out}" + (f"  ({missing} missed polls)" if missing else ""))


if __name__ == "__main__":
    main()
