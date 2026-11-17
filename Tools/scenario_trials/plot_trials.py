"""Scenario trials heat map, simulated (left) and real (right).
Rows = scenarios (grouped Face / Vehicle), columns = trials 1..N.
Each box is that trial's 0-100 score for the signal the scenario tests;
▲ = an alert fired in that trial.

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
NAVY, BROWN, INK, GREY, RED = "#1F3E6E", "#6B4A12", "#1A1A1A", "#555555", "#7A1015"
# (scenario, label, scored signal column, signal name, group)
ROWS = [("normal", "Normal (alert)", "overall_fatigue", "overall fatigue", "Face"),
        ("yawning", "Yawning", "yawn", "yawn (MAR)", "Face"),
        ("eyes_closing", "Eyes closing", "perclos", "PERCLOS (EAR)", "Face"),
        ("head_turn", "Head turn (off-road)", "yaw", "head pose, yaw", "Face"),
        ("head_nods", "Head nods", "pitch", "head pose, pitch", "Face"),
        ("steering_weave", "Steering weave", "steer", "gyro reversals", "Vehicle"),
        ("harsh_braking", "Harsh braking", "brake", "accelerometer", "Vehicle"),
        ("obd_variability", "OBD variability", "obd", "RPM / speed / throttle", "Vehicle"),
        ("combined", "COMBINED FATIGUE", "overall_fatigue", "overall fatigue", "Fused")]
GCOL = {"Face": NAVY, "Vehicle": BROWN, "Fused": INK}
cmap = LinearSegmentedColormap.from_list("w", ["#FBF0DC", "#F6D58A", "#EB9A45", "#D2602B", "#A82822", "#7A1015"])


def load(name):
    p = HERE / name
    return list(csv.DictReader(open(p))) if p.exists() else []


def main(out="scenario_trials.png"):
    sim, real = load("sim_trials.csv"), load("real_trials.csv")
    n = max(int(r["trial"]) for r in sim)
    ys, y, prev = [], 0.0, None
    for r in ROWS:
        if prev and r[4] != prev: y += 0.45
        ys.append(y); y += 1; prev = r[4]
    fig = plt.figure(figsize=(17, 8.6), dpi=220)
    fig.text(0.025, 0.935, "DRIFTS: Scenario Trials, Simulated vs Real", fontsize=26, fontweight="bold", color=NAVY)
    fig.text(0.025, 0.893, f"Score for the signal each scenario tests (0–100), one box per trial, {n} trials per scenario: "
             "40 s normal, then 30 s of the scenario.", fontsize=14, color="#444444")
    axs = [fig.add_axes([0.30, 0.15, 0.31, 0.68]), fig.add_axes([0.655, 0.15, 0.31, 0.68])]
    for ax, data, title in ((axs[0], sim, "SIMULATED (DRIFTS code)"),
                            (axs[1], real, f"REAL (measured: {len(real)} of {len(ROWS) * n})")):
        for (key, _, col, _, _), yy in zip(ROWS, ys):
            for t in range(n):
                rec = next((r for r in data if r["scenario"] == key and int(r["trial"]) == t + 1), None)
                v = None if rec is None or rec.get(col, "") == "" else float(rec[col])
                ax.add_patch(plt.Rectangle((t + 0.04, yy + 0.04), 0.92, 0.92, color="#EEEAEA" if v is None else cmap(v / 100), lw=0))
                if rec is not None and rec.get("alert") == "1":
                    ax.plot(t + 0.5, yy + 0.5, marker="^", ms=9, color="white", mec=RED, mew=1.2)
        ax.set_xlim(0, n); ax.set_ylim(y, -0.05); ax.set_aspect("equal")
        ax.set_xticks([t + 0.5 for t in range(n)]); ax.set_xticklabels([str(t + 1) for t in range(n)], fontsize=13, color="#333333")
        ax.set_xlabel("trial", fontsize=14, color="#333333", labelpad=6)
        ax.tick_params(length=0)
        for s in ax.spines.values(): s.set_visible(False)
        ax.set_title(title, fontsize=16, fontweight="bold", color=NAVY, pad=10)
        if not data:
            ax.text(n / 2, y / 2, "To be recorded\nwith trial_logger.py", ha="center", va="center", fontsize=16, color=GREY)
    ax = axs[0]
    ax.set_yticks([yy + 0.5 for yy in ys]); ax.set_yticklabels([""] * len(ys))
    for (key, lab, _, sig, grp), yy in zip(ROWS, ys):
        bold = grp == "Fused"
        ax.text(-0.35, yy + 0.42, lab, ha="right", va="center", fontsize=15, color=GCOL[grp], fontweight="bold" if bold else "normal")
        ax.text(-0.35, yy + 0.80, sig, ha="right", va="center", fontsize=10.5, color=GREY)
    axs[1].set_yticks([])
    groups = {}
    for (_, _, _, _, g), yy in zip(ROWS, ys):
        groups.setdefault(g, []).append(yy)
    for g, yl in groups.items():
        top, bot = min(yl) + 0.05, max(yl) + 0.95
        ax.plot([-6.6, -6.6], [top, bot], color=GCOL[g], lw=2.5, clip_on=False)
        ax.text(-6.9, (top + bot) / 2, g if g != "Fused" else "Both", rotation=90, ha="right", va="center",
                fontsize=16, fontweight="bold", color=GCOL[g], clip_on=False)
    cax = fig.add_axes([0.30, 0.055, 0.15, 0.026])
    cb = fig.colorbar(plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 100)), cax=cax, orientation="horizontal")
    cb.outline.set_visible(False); cb.set_ticks([])
    fig.text(0.457, 0.058, "0 → 100 fatigue score", fontsize=14, color="#333333")
    fig.lines.append(plt.Line2D([0.60], [0.069], marker="^", ms=10, color="white", mec=RED, mew=1.2, transform=fig.transFigure))
    fig.text(0.611, 0.058, "alert fired in that trial", fontsize=14, color=RED)
    fig.patches.append(plt.Rectangle((0.80, 0.055), 0.012, 0.026, color="#EEEAEA", transform=fig.transFigure))
    fig.text(0.817, 0.058, "not recorded yet", fontsize=14, color="#333333")
    fig.text(0.025, 0.012, "Simulated trials: a scripted driver run through the DRIFTS code (Pi face pipeline, ESP32 weave detector and alert score), "
             "timing and intensity varied per trial.", fontsize=12, color=GREY, style="italic")
    fig.savefig(HERE / out, facecolor="white")
    print("wrote", HERE / out)


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
