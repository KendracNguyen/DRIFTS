"""Record one REAL scenario trial from a running drifts.live, in the same
format as scenarios_sim.py, so the two can be plotted side by side.

Start the app first (on the Pi):
    .venv/bin/python -m drifts.live --face mesh --esp uart --whisplay

Then, from the Pi or a laptop on the same Wi-Fi:
    python Tools/scenario_trials/trial_logger.py --scenario eyes_closing --trial 1
    python Tools/scenario_trials/trial_logger.py --scenario yawning --trial 1 --url http://192.168.1.175:8080

Each trial: WARMUP s of normal behaviour, then SECONDS s of the scenario.
Only the scenario part is recorded; one row is appended to real_trials.csv.
"""
import argparse, csv, json, statistics as st, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCEN = {
    "normal": "Look at the road, blink normally, mouth relaxed. Drive (or sit) normally.",
    "yawning": "Yawn 1-2 times, mouth wide, 4-7 s each.",
    "eyes_closing": "Close the eyes 2-4 times, 1-3.5 s each (vary it). Parked or passenger only.",
    "head_turn": "Turn the head 25-45 deg off the road (left or right), 2 times, 3-8 s each.",
    "head_nods": "Let the head drop forward 15-20 deg, 2-4 times, 1-2 s each.",
    "steering_weave": "Drift and correct the steering 5-9 times (test rig, or twist the enclosure on the bench).",
    "harsh_braking": "2-4 firm brakes (test rig / safe empty road only).",
    "obd_variability": "Vary speed and throttle a lot (safe road only), or use the OBD simulator.",
    "combined": "Eyes closing + a yawn + 2 nods, with weave / braking / variable throttle.",
}
COLS = ["panel", "scenario", "trial", "perclos", "blink", "gaze", "yaw", "pitch", "mar", "yawn",
        "steer", "brake", "obd", "eye_fatigue", "driving_fatigue", "overall_fatigue", "alert"]
# Firmware trigger levels (Esp32/drifts_esp32/config.h): ratio 1.0 at these, capped at 1.5
STEER_TRIGGER, BRAKE_TRIGGER, BRAKE_G = 6, 3, 0.35
RPM_SD, SPD_SD, THR_SD = 400.0, 8.0, 15.0


def get(url):
    with urllib.request.urlopen(url + "/status.json", timeout=2) as r:
        return json.loads(r.read())


def ratio(x):
    return max(0.0, min(1.0, x / 1.5)) * 100


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=list(SCEN))
    ap.add_argument("--trial", type=int, required=True)
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--warmup", type=float, default=40.0)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--out", default=str(HERE / "real_trials.csv"))
    a = ap.parse_args()
    get(a.url)                                   # fails early if the app isn't running
    print(f"\nScenario: {a.scenario}  (trial {a.trial})\n  {SCEN[a.scenario]}\n")
    input("Press Enter, then behave NORMALLY for the warm-up...")
    t_end = time.time() + a.warmup
    while time.time() < t_end:
        print(f"\r  warm-up {t_end - time.time():4.0f} s ", end="", flush=True); time.sleep(0.5)
    print("\n\n  >>> START the scenario now <<<\n")
    S, t0 = [], time.time()
    while time.time() - t0 < a.seconds:
        try:
            S.append(get(a.url))
        except OSError as e:
            print("  (missed a sample:", e, ")")
        print(f"\r  recording {a.seconds - (time.time() - t0):4.0f} s ", end="", flush=True)
        time.sleep(0.25)
    print("\n  done.\n")
    face = [s.get("face") or {} for s in S]
    esp = [s.get("esp") or {} for s in S]
    part = lambda k: 100 * st.mean([((f.get("parts") or {}).get(k) or {}).get("d") or 0.0 for f in face] or [0])
    mean = lambda xs: st.mean(xs) if xs else None
    fs = [f.get("face_score") for f in face if f.get("face_score") is not None]
    ds = [e.get("drv_score") for e in esp if e.get("drv_score") is not None]
    sc = [e.get("score") for e in esp if e.get("score") is not None]
    steer = [e["steer"] for e in esp if e.get("steer") is not None]
    g = [e["g"] for e in esp if e.get("g") is not None]
    brakes = sum(1 for i, x in enumerate(g) if x <= -BRAKE_G and (i == 0 or g[i - 1] > -BRAKE_G))
    sd = lambda k: st.pstdev([e[k] for e in esp if e.get(k) is not None]) if sum(e.get(k) is not None for e in esp) > 2 else 0.0
    row = {"panel": "real", "scenario": a.scenario, "trial": a.trial,
           "perclos": part("ear"), "blink": part("blink"), "gaze": part("gaze"), "yaw": part("yaw"), "pitch": part("pitch"),
           "mar": part("mar"), "yawn": part("yawn"),
           "steer": ratio(steer[-1] / STEER_TRIGGER) if steer else "",
           "brake": ratio(brakes / BRAKE_TRIGGER) if g else "",
           "obd": ratio((sd("rpm") / RPM_SD + sd("spd") / SPD_SD + sd("thr") / THR_SD) / 3),
           "eye_fatigue": 100 - mean(fs) if fs else "", "driving_fatigue": 100 - mean(ds) if ds else "",
           "overall_fatigue": 100 - mean(sc) if sc else "",
           "alert": int(any(e.get("alert") for e in esp) or any(s.get("drowsy_sent") for s in S))}
    new = not Path(a.out).exists()
    with open(a.out, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        if new: w.writeheader()
        w.writerow({k: (round(v, 1) if isinstance(v, float) else v) for k, v in row.items()})
    print("  saved:", {k: row[k] for k in ("perclos", "yaw", "pitch", "yawn", "overall_fatigue", "alert")}, "->", a.out)


if __name__ == "__main__":
    main()
