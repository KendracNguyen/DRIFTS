"""Draw the repeatability heat maps (Test 1-4 x 6 s boxes) from real_heat.json in the poster's
green -> red style, tagged MEASURED. Tests not recorded yet are grey.

    python Tools/scenario_trials/plot_heat.py      # -> heat_<row>.png for each recorded row
"""
import json
from pathlib import Path
import logging
import matplotlib; matplotlib.use("Agg")
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from heat_recorder import ROWS

HERE = Path(__file__).resolve().parent
plt.rcParams["font.family"] = ["Arial", "Liberation Sans", "DejaVu Sans"]
cmap = LinearSegmentedColormap.from_list("gr", ["#00A651", "#5CC33A", "#B5DD1E", "#FFE600", "#FFB000", "#FF7A00", "#E8200E", "#A30000"])
INK, SUB = "#222222", 4


def draw(row, tests, out):
    n, m = 10, 4
    fig = plt.figure(figsize=(5.6, 3.0), dpi=400)
    ax = fig.add_axes([0.15, 0.02, 0.83, 0.80])
    fig.text(0.15, 0.97, ROWS[row][0], ha="left", va="top", fontsize=13.5, fontweight="bold", color=INK)
    fig.text(0.98, 0.97, "MEASURED", ha="right", va="top", fontsize=10, fontweight="bold", color="#1E6B2E",
             bbox=dict(boxstyle="round,pad=0.3", fc="#DDEFE0", ec="none"))
    for r in range(m):
        rec = tests[r] if r < len(tests) else None
        for c in range(n):
            col = "#E6E6E6" if not rec else cmap(rec[0][c] / 100)
            ax.add_patch(plt.Rectangle((c, r), 1, 1, color=col, lw=0))
            for k in range(1, SUB):
                ax.plot([c + k / SUB] * 2, [r, r + 1], color="white", lw=0.35, alpha=0.75)
                ax.plot([c, c + 1], [r + k / SUB] * 2, color="white", lw=0.35, alpha=0.75)
        for c in (rec[1] if rec else []):
            ax.plot(c + 0.5, r + 0.5, marker="^", ms=7.5, color="white", mec="black", mew=1.0, zorder=5)
    for c in range(n + 1): ax.plot([c, c], [0, m], color="white", lw=1.4)
    for r in range(m + 1): ax.plot([0, n], [r, r], color="white", lw=1.4)
    ax.set_xlim(0, n); ax.set_ylim(m, 0); ax.set_aspect("equal")
    ax.set_yticks([r + 0.5 for r in range(m)]); ax.set_yticklabels([f"Test {r + 1}" for r in range(m)], fontsize=12, color=INK)
    ax.set_xticks([c + 0.5 for c in range(n)]); ax.set_xticklabels([f"{6 * (c + 1)}s" for c in range(n)], fontsize=10, color=INK)
    ax.xaxis.set_ticks_position("top"); ax.tick_params(length=0, pad=3)
    for s in ax.spines.values(): s.set_visible(False)
    fig.savefig(out, facecolor="white", bbox_inches="tight", pad_inches=0.04); plt.close(fig)


if __name__ == "__main__":
    data = json.loads((HERE / "real_heat.json").read_text())
    for row, tests in data.items():
        draw(row, tests, HERE / f"heat_{row}.png"); print("wrote", HERE / f"heat_{row}.png")
