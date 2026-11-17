"""Heat map of scenario trials, simulated (left) and real (right).
Rows = signals, columns = scenarios, one box per trial; each box is the
signal's 0-100 fatigue contribution, ▲ = an alert fired in that trial.

    python Tools/scenario_trials/plot_trials.py            # -> scenario_trials.png
Reads sim_trials.csv (scenarios_sim.py) and real_trials.csv (trial_logger.py).
"""
import csv, sys
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib import font_manager as fm

for f in fm.findSystemFonts():
    if "LiberationSans" in f:
        fm.fontManager.addfont(f)
plt.rcParams["font.family"] = ["Liberation Sans", "DejaVu Sans"]
HERE = Path(__file__).resolve().parent
SCEN = [("normal", "Normal"), ("yawning", "Yawning"), ("eyes_closing", "Eyes\nclosing"), ("head_turn", "Head turn\n(off-road)"),
        ("head_nods", "Head\nnods"), ("steering_weave", "Steering\nweave"), ("harsh_braking", "Harsh\nbraking"),
        ("obd_variability", "OBD\nvariability"), ("combined", "Combined\nfatigue")]
NAVY, BROWN, INK, GREY, RED = "#1F3E6E", "#6B4A12", "#1A1A1A", "#555555", "#7A1015"
ROWS = [("perclos", "PERCLOS (EAR)", NAVY), ("blink", "Blink duration", NAVY), ("gaze", "Gaze off-road", NAVY),
        ("yaw", "Head turn (yaw)", NAVY), ("pitch", "Head nod (pitch)", NAVY), ("yawn", "Yawn (MAR)", NAVY),
        ("steer", "Steering weave", BROWN), ("brake", "Harsh braking", BROWN), ("obd", "OBD variability", BROWN),
        ("eye_fatigue", "Eye fatigue", INK), ("driving_fatigue", "Driving fatigue", INK), ("overall_fatigue", "OVERALL FATIGUE", INK)]
cmap = LinearSegmentedColormap.from_list("w", ["#FBF0DC", "#F6D58A", "#EB9A45", "#D2602B", "#A82822", "#7A1015"])


def load(name):
    p = HERE / name
    return list(csv.DictReader(open(p))) if p.exists() else []


def main(out="scenario_trials.png"):
    sim, real = load("sim_trials.csv"), load("real_trials.csv")
    n = max(int(r["trial"]) for r in sim)
    G = 1.0
    width = len(SCEN) * n + (len(SCEN) - 1) * G
    ys, hs, y = [], [], 0.0
    for i, (k, _, _) in enumerate(ROWS):
        if i == 9: y += 0.45
        if k == "overall_fatigue": y += 0.3
        h = 1.25 if k == "overall_fatigue" else 1.0
        ys.append(y); hs.append(h); y += h
    fig = plt.figure(figsize=(22, 8.2), dpi=200)
    fig.text(0.025, 0.93, "DRIFTS: Scenario Trials, Simulated vs Real", fontsize=26, fontweight="bold", color=NAVY)
    fig.text(0.025, 0.885, f"Normalized fatigue contribution per signal (0–100). Each box is one trial ({n} per scenario): "
             "40 s normal, then 30 s of the scenario. Alert score = 0.70 × Eye score + 0.30 × Driving score.", fontsize=14, color="#444444")
    axs = [fig.add_axes([0.13, 0.20, 0.395, 0.60]), fig.add_axes([0.545, 0.20, 0.395, 0.60])]
    for ax, data, title in ((axs[0], sim, "SIMULATED (DRIFTS code)"), (axs[1], real, f"REAL (measured on the Pi: {len(real)} of {len(SCEN) * n})")):
        for si, (key, _) in enumerate(SCEN):
            x0 = si * (n + G)
            for t in range(n):
                rec = next((r for r in data if r["scenario"] == key and int(r["trial"]) == t + 1), None)
                for (col, _, _), yy, h in zip(ROWS, ys, hs):
                    v = None if rec is None or rec.get(col, "") == "" else float(rec[col])
                    ax.add_patch(plt.Rectangle((x0 + t + 0.03, yy + 0.03), 0.94, h - 0.06,
                                               color="#EEEAEA" if v is None else cmap(v / 100), lw=0))
                if rec is not None and rec.get("alert") == "1":
                    ax.plot(x0 + t + 0.5, y + 0.55, marker="^", ms=8, color=RED, clip_on=False)
        ax.set_xlim(0, width); ax.set_ylim(y + 0.95, -0.1)
        ax.set_xticks([si * (n + G) + n / 2 for si in range(len(SCEN))])
        ax.set_xticklabels([s[1] for s in SCEN], fontsize=13.5, color="#333333")
        ax.tick_params(axis="x", length=0, pad=4); ax.tick_params(axis="y", length=0)
        for s in ax.spines.values(): s.set_visible(False)
        ax.plot([0, width], [ys[9] - 0.22, ys[9] - 0.22], color="#444444", lw=1.3)
        ax.set_title(title, fontsize=16, fontweight="bold", color=NAVY, loc="center", pad=8)
        if not data:
            ax.text(width / 2, y / 2, "Real trials to be recorded\nwith trial_logger.py", ha="center", va="center", fontsize=17, color=GREY)
    axs[0].set_yticks([yy + h / 2 for yy, h in zip(ys, hs)]); axs[0].set_yticklabels([r[1] for r in ROWS], fontsize=15)
    for lab, r in zip(axs[0].get_yticklabels(), ROWS):
        lab.set_color(r[2]); lab.set_fontweight("bold" if r[2] == INK else "normal")
    axs[1].set_yticks([])
    for txt, a, b, col in (("Face", 0, 5, NAVY), ("Vehicle", 6, 8, BROWN), ("Fused", 9, 11, INK)):
        axs[1].text(width + 0.8, (ys[a] + ys[b] + hs[b]) / 2, txt, va="center", fontsize=15, fontweight="bold", color=col)
    cax = fig.add_axes([0.13, 0.065, 0.13, 0.026])
    cb = fig.colorbar(plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 100)), cax=cax, orientation="horizontal")
    cb.outline.set_visible(False); cb.set_ticks([])
    fig.text(0.267, 0.068, "0 → 100 normalized fatigue contribution", fontsize=14, color="#333333")
    fig.text(0.525, 0.068, "▲ alert issued (score < 50, or camera DROWSY)", fontsize=14, color=RED)
    fig.patches.append(plt.Rectangle((0.775, 0.066), 0.012, 0.026, color="#EEEAEA", transform=fig.transFigure))
    fig.text(0.792, 0.068, "not recorded yet", fontsize=14, color="#333333")
    fig.text(0.025, 0.015, "Simulated trials: a scripted driver run through the DRIFTS code (Pi face pipeline, ESP32 weave detector and alert score), "
             "timing and intensity varied per trial. Fused rows show 100 − score.", fontsize=12.5, color=GREY, style="italic")
    fig.savefig(HERE / out, facecolor="white")
    print("wrote", HERE / out)


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
