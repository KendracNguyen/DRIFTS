"""Heat map of scenario trials: simulated (left) and real (right) side by side.
Rows = signals, columns = scenarios, one box per trial (repeatability).

    python Tools/scenario_trials/plot_trials.py            # -> scenario_trials.png
Reads sim_trials.csv (scenarios_sim.py) and real_trials.csv (trial_logger.py)
if it exists; missing real trials are drawn as 'to be measured'.
"""
import csv, sys
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

HERE = Path(__file__).resolve().parent
SCEN = [("normal", "Normal\n(alert)"), ("yawning", "Yawning"), ("eyes_closing", "Eyes\nclosing"), ("head_turn", "Head turn\n(off-road)"),
        ("head_nods", "Head\nnods"), ("steering_weave", "Steering\nweave"), ("harsh_braking", "Harsh\nbraking"),
        ("obd_variability", "OBD\nvariability"), ("combined", "Combined\nfatigue")]
ROWS = [("perclos", "PERCLOS (EAR)"), ("blink", "Blink duration"), ("gaze", "Gaze off-road (eyes)"), ("yaw", "Head turn (yaw)"),
        ("pitch", "Head nod (pitch)"), ("mar", "Mouth open (MAR)"), ("yawn", "Yawn"),
        ("steer", "Steering weave"), ("brake", "Harsh braking"), ("obd", "OBD variability"),
        ("eye_fatigue", "Eye fatigue"), ("driving_fatigue", "Driving fatigue"), ("overall_fatigue", "OVERALL FATIGUE"), ("alert", "Alert fired")]
NAVY, BROWN, INK, MUTED, RED = "#1F3E6E", "#6B4A12", "#2B1A20", "#6E5A61", "#7A1015"
plt.rcParams["font.family"] = "DejaVu Sans"
cmap = LinearSegmentedColormap.from_list("w", ["#FBF0DC", "#F6D58A", "#EB9A45", "#D2602B", "#A82822", "#7A1015"])


def load(name):
    p = HERE / name
    return list(csv.DictReader(open(p))) if p.exists() else []


def main(out="scenario_trials.png", n=None):
    sim, real = load("sim_trials.csv"), load("real_trials.csv")
    n = n or max(int(r["trial"]) for r in sim)
    G = 1.2                                            # gap between scenario groups (in boxes)
    width = len(SCEN) * n + (len(SCEN) - 1) * G
    ys, y = [], 0.0
    for i in range(len(ROWS)):
        if i in (7, 10): y += 0.5
        if i == 13: y += 0.35
        ys.append(y); y += 1
    fig = plt.figure(figsize=(22, 9.0), dpi=200)
    axs = [fig.add_axes([0.12, 0.215, 0.40, 0.67]), fig.add_axes([0.555, 0.215, 0.40, 0.67])]
    for ax, data, title in ((axs[0], sim, f"SIMULATED: DRIFTS code, {n} trials per scenario"),
                            (axs[1], real, f"REAL: measured on the Pi ({len(real)} of {len(SCEN) * n} trials recorded)")):
        for si, (key, _) in enumerate(SCEN):
            x0 = si * (n + G)
            for t in range(n):
                rec = next((r for r in data if r["scenario"] == key and int(r["trial"]) == t + 1), None)
                for (col, _), yy in zip(ROWS, ys):
                    if rec is None or rec.get(col, "") == "":
                        ax.add_patch(plt.Rectangle((x0 + t + 0.06, yy + 0.06), 0.88, 0.88, facecolor="#F1EEEE", edgecolor="#D9D2D4", hatch="///", lw=0.4))
                        continue
                    v = float(rec[col])
                    face = (RED if v >= 0.5 else "#EFE7E2") if col == "alert" else cmap(v / 100)
                    ax.add_patch(plt.Rectangle((x0 + t + 0.06, yy + 0.06), 0.88, 0.88, color=face, lw=0))
        ax.set_xlim(-0.3, width + 0.3); ax.set_ylim(y + 0.1, -0.2)
        ax.set_xticks([si * (n + G) + n / 2 for si in range(len(SCEN))]); ax.set_xticklabels([s[1] for s in SCEN], fontsize=12.5, color=INK)
        ax.tick_params(length=0)
        for s in ax.spines.values(): s.set_visible(False)
        ax.set_title(title, fontsize=15, fontweight="bold", color=NAVY, loc="left", pad=10)
        ax.plot([-0.3, width + 0.3], [ys[10] - 0.27, ys[10] - 0.27], color=INK, lw=1.1)
        if not data:
            ax.text(width / 2, y / 2, "To be measured\n(Tools/scenario_trials/trial_logger.py)", ha="center", va="center",
                    fontsize=17, color=MUTED, fontweight="bold", bbox=dict(boxstyle="round,pad=0.6", fc="white", ec="#D9D2D4"))
    axs[0].set_yticks([yy + 0.5 for yy in ys]); axs[0].set_yticklabels([r[1] for r in ROWS], fontsize=13)
    for i, lab in enumerate(axs[0].get_yticklabels()):
        lab.set_color(NAVY if i < 7 else BROWN if i < 10 else INK)
        if i >= 10: lab.set_fontweight("bold")
    axs[1].set_yticks([])
    for txt, a, b, col in (("Face", 0, 6, NAVY), ("Vehicle", 7, 9, BROWN), ("Fused", 10, 13, INK)):
        axs[1].text(width + 1.2, (ys[a] + ys[b] + 1) / 2, txt, va="center", fontsize=14, fontweight="bold", color=col)
    fig.text(0.12, 0.955, "DRIFTS scenario trials: which signals respond, and how repeatably", fontsize=20, fontweight="bold", color=NAVY)
    cax = fig.add_axes([0.12, 0.075, 0.2, 0.022])
    cb = fig.colorbar(plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 100)), cax=cax, orientation="horizontal")
    cb.outline.set_visible(False); cb.set_ticks([0, 50, 100]); cb.ax.tick_params(labelsize=11, colors=MUTED)
    fig.text(0.12, 0.108, "Fatigue contribution 0–100 (fused rows: 100 − score), mean over the 30 s scenario", fontsize=12, color=INK)
    fig.patches.append(plt.Rectangle((0.555, 0.078), 0.012, 0.022, color=RED, transform=fig.transFigure))
    fig.text(0.571, 0.080, "alert fired (buzzer + seat)", fontsize=12, color=INK)
    fig.patches.append(plt.Rectangle((0.70, 0.078), 0.012, 0.022, facecolor="#F1EEEE", edgecolor="#D9D2D4", hatch="///", transform=fig.transFigure))
    fig.text(0.716, 0.080, "not measured yet", fontsize=12, color=INK)
    fig.text(0.12, 0.012, "Each box = one trial: 40 s normal, then 30 s of the scenario (timing and intensity varied per trial). "
             "Simulated trials run the Pi face pipeline and the ESP32 alert score code with a scripted driver.", fontsize=11, color=MUTED, style="italic")
    fig.savefig(HERE / out, facecolor="white")
    print("wrote", HERE / out)


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
