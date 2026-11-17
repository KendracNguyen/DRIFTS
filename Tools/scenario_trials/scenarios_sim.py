"""Simulated scenario trials through the real DRIFTS code.

Each trial: 40 s of normal driving (fills the windows), then 30 s of the
scenario, with timing and intensity randomised per trial. Face signals run
through the Pi's FaceMeshSource and DrowsyTrigger; driving and the alert
score run through the ESP32's own weave.h and alertness.h (esp_drive.cpp,
compiled here with g++). Writes sim_trials.csv, the same columns
trial_logger.py writes for real trials.

    python Tools/scenario_trials/scenarios_sim.py [trials_per_scenario]
"""
import sys, csv, random, subprocess, statistics as st
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "Raspberry")); sys.path.insert(0, str(REPO / "Raspberry" / "tests"))
from drifts.facemesh import FaceMeshSource
from drifts.drowsy_trigger import DrowsyTrigger
from test_facesignals import face_points, rot_matrix

FPS, WARM, DUR, N = 8.0, 40.0, 30.0, int(sys.argv[1]) if len(sys.argv) > 1 else 10
SCEN = ["normal", "yawning", "eyes_closing", "head_turn", "head_nods", "steering_weave", "harsh_braking", "obd_variability", "combined"]
COLS = ["panel", "scenario", "trial", "perclos", "blink", "gaze", "yaw", "pitch", "mar", "yawn",
        "steer", "brake", "obd", "eye_fatigue", "driving_fatigue", "overall_fatigue", "alert"]

def esp_binary() -> str:
    exe = HERE / ("esp_drive.exe" if sys.platform == "win32" else "esp_drive")
    if not exe.exists():
        subprocess.run(["g++", "-O2", "-std=c++17", f"-I{REPO / 'Esp32' / 'drifts_esp32'}", str(HERE / "esp_drive.cpp"), "-o", str(exe)], check=True)
    return str(exe)


def plan(sc, rng):
    """Events inside the 30 s scenario window, randomised per trial."""
    p = {"blink_per": rng.uniform(3, 5), "blink_len": rng.uniform(0.15, 0.3), "closures": [], "yawns": [], "turns": [], "nods": [],
         "weave": 0, "brakes": [], "obd": (rng.uniform(50, 80), rng.uniform(0.8, 1.5), rng.uniform(1.5, 3))}
    def spots(n, length):
        return sorted(rng.uniform(1, DUR - length - 1) for _ in range(n))
    if sc in ("eyes_closing", "combined"):
        for _ in range(rng.randint(2, 4)):
            L = rng.uniform(1.0, 3.6); p["closures"].append((rng.uniform(1, DUR - L - 1), L))
    if sc in ("yawning", "combined"):
        for _ in range(rng.randint(1, 2)):
            L = rng.uniform(4, 7); p["yawns"].append((rng.uniform(1, DUR - L - 1), L, rng.uniform(0.8, 1.0)))
    if sc == "head_turn":
        for _ in range(2):
            L = rng.uniform(3, 8); p["turns"].append((rng.uniform(1, DUR - L - 1), L, rng.choice([-1, 1]) * rng.uniform(25, 45)))
    if sc in ("head_nods", "combined"):
        for _ in range(rng.randint(2, 4) if sc == "head_nods" else 2):
            L = rng.uniform(1.0, 2.0); p["nods"].append((rng.uniform(1, DUR - L - 1), L, rng.uniform(14, 22)))
    if sc in ("steering_weave", "combined"):
        p["weave"] = rng.randint(5, 9) if sc == "steering_weave" else 6
    if sc in ("harsh_braking", "combined"):
        p["brakes"] = spots(rng.randint(2, 4) if sc == "harsh_braking" else 1, 0.5)
    if sc in ("obd_variability", "combined"):
        p["obd"] = (rng.uniform(250, 450), rng.uniform(6, 10), rng.uniform(10, 18))
    return p

def inside(t, spans):
    for s in spans:
        if s[0] <= t < s[0] + s[1]: return s
    return None

def trial(sc, k):
    rng = random.Random(1000 * SCEN.index(sc) + k)
    p = plan(sc, rng)
    src, trig = FaceMeshSource(landmarker=object()), DrowsyTrigger()
    ev, rows, last = [], [], -1e9
    for i in range(int((WARM + DUR) * FPS)):
        ts = i / FPS; t = ts - WARM
        o = 0.0 if ((ts + 0.7) % p["blink_per"]) < p["blink_len"] else 1.0
        mouth, yaw, pitch = 0.1, rng.gauss(0, 1.5), rng.gauss(0, 1.0)
        if t >= 0:
            if inside(t, p["closures"]): o = 0.0
            y = inside(t, p["yawns"]); mouth = y[2] if y else mouth
            h = inside(t, p["turns"]); yaw = h[2] if h else yaw
            n = inside(t, p["nods"]); pitch = n[2] if n else pitch
        f = src.compute(ts, face_points(o, mouth), rot_matrix(yaw, pitch, 0), edge=0.0)
        s = src.eye_score()
        if s is not None and f.get("perclos") is not None and ts - last >= 2.0:
            last = ts; ev.append((ts + 1 / FPS, f"EYE {s}"))
        cmd = trig.update(ts, f.get("state") if f.get("found") else "no_face")
        if cmd: ev.append((ts + 1 / FPS, cmd))
        if t >= 0:
            rows.append({k2: (v["d"] or 0.0) for k2, v in f["parts"].items()} | {"face_score": f.get("face_score")})
    # vehicle
    swings = sorted(rng.uniform(0.5, DUR - 1.5) for _ in range(p["weave"]))
    for i in range(int((WARM + DUR) * 50)):
        ts = i / 50; t = ts - WARM; y = rng.gauss(0, 0.5)
        for s0 in swings:
            if s0 <= t < s0 + 0.6: y = 3.2
            elif s0 + 0.6 <= t < s0 + 1.2: y = -3.2
        ev.append((ts, f"YAW {y:.2f}"))
    for s in range(int(WARM + DUR)):
        a, b, c = p["obd"] if s >= WARM else (60, 1.0, 2)
        ev.append((s + 0.5, f"OBD {2200 + rng.gauss(0, a):.0f} {100 + rng.gauss(0, b):.1f} {22 + rng.gauss(0, c):.1f}"))
    for bt in p["brakes"]: ev.append((WARM + bt, "BRAKE"))
    ev.sort(key=lambda e: e[0])
    out = subprocess.run([esp_binary()], input="\n".join(f"{int(round(a * 1000))} {b}" for a, b in ev) + f"\n{int((WARM + DUR) * 1000)} END\n",
                         capture_output=True, text=True).stdout.splitlines()
    ticks, alert = [], 0
    for l in out:
        q = l.split()
        if q[1] == "TICK" and int(q[0]) / 1000 > WARM:
            ticks.append(q)
        elif q[1] == "ALERT" and int(q[0]) / 1000 > WARM:
            alert = 1
    m = lambda key: 100 * st.mean(r[key] for r in rows)
    ratio = lambda x: max(0.0, min(1.0, x / 1.5)) * 100
    tv = lambda j: st.mean(float(q[j]) for q in ticks)
    return {"panel": "sim", "scenario": sc, "trial": k + 1, "perclos": m("ear"), "blink": m("blink"), "gaze": m("gaze"), "yaw": m("yaw"),
            "pitch": m("pitch"), "mar": m("mar"), "yawn": m("yawn"),
            "steer": ratio(float(ticks[-1][2])), "brake": ratio(float(ticks[-1][3])),
            "obd": ratio((float(ticks[-1][4]) + float(ticks[-1][5]) + float(ticks[-1][6])) / 3), "eye_fatigue": 100 - tv(7), "driving_fatigue": 100 - tv(8),
            "overall_fatigue": 100 - tv(9), "alert": alert}

if __name__ == "__main__":
    with open(HERE / "sim_trials.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS); w.writeheader()
        for sc in SCEN:
            res = [trial(sc, k) for k in range(N)]
            for r in res: w.writerow({k2: (round(v, 1) if isinstance(v, float) else v) for k2, v in r.items()})
            print(f"{sc:16s} overall {st.mean(r['overall_fatigue'] for r in res):5.1f}  alerts {sum(r['alert'] for r in res)}/{N}  "
                  f"eye {st.mean(r['eye_fatigue'] for r in res):5.1f}  drv {st.mean(r['driving_fatigue'] for r in res):5.1f}")
