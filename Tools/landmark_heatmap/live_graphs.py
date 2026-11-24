"""DEV ONLY - live line graphs of each facial indicator (laptop demo).

Not part of the Pi app (lives outside Raspberry/, refuses to run on a Pi).
MediaPipe's Face Landmarker gives the 478 landmarks; the indicators use the
same points and maths as the Pi (facesignals.py / mouth.py):

  EAR        eye aspect ratio, mean of both eyes     Closed -> Open
  NAR        nose aspect ratio |168-1| / |98-327|    Down -> Up (NAR rises as the head drops,
                                                     so that axis is drawn inverted)
  MAR        mouth aspect ratio                      Closed -> Open
  Head pose  yaw / pitch / roll in degrees (DRIFTS sign convention)
  Gaze       from the driver's seat, in degrees: head (vs. neutral) + eyes, with the
             windshield (+/-30 deg across, 10 up, 15 down) as the road range and a red target

Each indicator records on its own: press its key to start, press again to
stop and save that graph (PNG) and its data (CSV) to captures/<session>/.

    keys   1 EAR   2 NAR   3 MAR   4 head pose   5 gaze   a all   p picture   c re-centre   s snapshot   q quit

    python Tools/landmark_heatmap/live_graphs.py --camera 0
    python Tools/landmark_heatmap/live_graphs.py --video clip.mp4 --record all --no-window
"""

from __future__ import annotations

import argparse
import csv
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                   # noqa: E402
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.facecolor": "white", "figure.facecolor": "white",
                     "mathtext.fontset": "stix"})
import matplotlib.patheffects as pe                               # noqa: E402
from matplotlib.patches import Circle, FancyBboxPatch, Polygon         # noqa: E402
from matplotlib.ticker import AutoMinorLocator                    # noqa: E402

import landmark_heatmap as lh                                     # noqa: E402  (same folder)

WINDOW = 100                     # frames shown live, as in the reference screenshot
# Gaze in degrees from the driver's seat: head (vs. neutral) + eyes.
# Windshield = the road range: +/-30 deg across, 10 deg up, 15 deg down (to the hood / dash).
IRIS_DEG = 90.0                  # iris offset -> degrees (0.5 at the eye corner ~ 45 deg)
WS_SIDE, WS_UP, WS_DOWN = 30.0, 10.0, 15.0
SPAN_H, SPAN_V = 60.0, 30.0      # plotted range: +/-60 deg across, +/-30 deg up/down
NAR_BRIDGE, NAR_TIP, NAR_ALAE = 168, 1, (98, 327)
TARGET_RED, TARGET_WHITE = "#F2492B", "white"

# indicator -> (key, title, y label, colour, (low label, high label), y-limits)
LINES = {
    "ear": ("1", "Eye Opening (EAR)", "Eye Aspect Ratio", "blue", ("Closed", "Open"), (0.0, 0.45)),
    "nar": ("2", "Nose Angle (NAR)", "Nose Aspect Ratio", "red", ("Down", "Up"), None),
    "mar": ("3", "Mouth Opening (MAR)", "Mouth Aspect Ratio", "green", ("Closed", "Open"), (0.0, 1.0)),
}
KEYS = {"1": "ear", "2": "nar", "3": "mar", "4": "pose", "5": "gaze"}


# ---------------------------------------------------------------- measurements

def nar(P):
    w = np.linalg.norm(P[NAR_ALAE[0], :2] - P[NAR_ALAE[1], :2])
    return float(np.linalg.norm(P[NAR_BRIDGE, :2] - P[NAR_TIP, :2]) / w) if w > 1e-6 else None


def iris_offset(P):
    vals = []
    for (a, b), iris in (((33, 133), 468), ((362, 263), 473)):
        e = P[b, :2] - P[a, :2]
        n2 = float(e @ e)
        if n2 > 1e-6:
            t = float((P[iris, :2] - P[a, :2]) @ e) / n2
            vals.append((t - 0.5) * (1 if e[0] > 0 else -1))
    return float(np.mean(vals)) if vals else None


def indicators(P, mat):
    e = [v for v in (lh.ear(P, lh.EAR_R), lh.ear(P, lh.EAR_L)) if v is not None]
    pose = lh.head_pose(mat) or {}
    return {"ear": float(np.mean(e)) if e else None, "nar": nar(P), "mar": lh.mar(P),
            "yaw": pose.get("yaw"), "pitch": pose.get("pitch"), "roll": pose.get("roll"),
            "gaze_x": iris_offset(P)}


# ---------------------------------------------------------------- styling

def style(ax, title, ylabel, xlabel=None, bold=False):
    w = "bold" if bold else "normal"
    ax.set_title(title, fontsize=10, fontweight=w)
    ax.set_ylabel(ylabel, fontsize=8, fontweight=w)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9, fontweight=w)
    ax.grid(True, alpha=0.3)
    # x grid at half the tick spacing: an extra unlabelled line halfway between numbered ticks
    ax.xaxis.set_minor_locator(AutoMinorLocator(2))
    ax.grid(True, which="minor", axis="x", alpha=0.3)
    ax.tick_params(which="minor", axis="x", length=0)
    ax.tick_params(labelsize=8)
    ax.set_facecolor("white")


def end_labels(ax, low, high, inverted=False):
    """'Closed' at the bottom and 'Open' at the top of the value axis."""
    top, bottom = (low, high) if inverted else (high, low)
    kw = dict(transform=ax.transAxes, fontsize=7, color="0.35", ha="left")
    ax.text(1.01, 1.0, top, va="top", **kw)
    ax.text(1.01, 0.0, bottom, va="bottom", **kw)


def draw_gaze_box(ax, h, v, trail=None, title="Gaze", off=False):
    """Graph-paper grid (10 deg squares) seen from the driver's seat, the windshield as the
    road range, and a red sight target where the driver is looking."""
    ax.clear()
    k = 1.0 / SPAN_H                                         # degrees -> plot units (same scale both ways)
    ax.set_xlim(-1, 1), ax.set_ylim(-SPAN_V * k, SPAN_V * k), ax.set_aspect("equal")
    ax.set_xticks(np.arange(-SPAN_H, SPAN_H + 1, 10) * k)
    ax.set_yticks(np.arange(-SPAN_V, SPAN_V + 1, 10) * k)
    ax.set_xticklabels([f"{d:.0f}" if d % 30 == 0 else "" for d in np.arange(-SPAN_H, SPAN_H + 1, 10)], fontsize=7)
    ax.set_yticklabels([f"{d:.0f}" for d in np.arange(-SPAN_V, SPAN_V + 1, 10)], fontsize=7)
    ax.tick_params(length=0)
    ax.grid(True, color="black", linewidth=0.5, alpha=0.55)
    ax.set_facecolor("white")
    ax.add_patch(FancyBboxPatch((-WS_SIDE * k, -WS_DOWN * k), 2 * WS_SIDE * k, (WS_UP + WS_DOWN) * k,
                                boxstyle="round,pad=0,rounding_size=0.04", fill=False, linewidth=2.0,
                                linestyle="--", edgecolor="black", zorder=3))
    ax.text(-WS_SIDE * k + 0.02, -WS_DOWN * k + 0.015, "windshield (road)", ha="left", va="bottom",
            fontsize=8, zorder=3)
    if trail is not None and len(trail) > 1:
        ax.plot(trail[:, 0], trail[:, 1], color="0.45", linewidth=1.0, alpha=0.7, zorder=2)
    if h is not None and v is not None:
        x, y = gaze_xy(h, v)
        s = 0.11
        ax.add_patch(Polygon([(x, y + s), (x + s, y), (x, y - s), (x - s, y)], closed=True,
                             facecolor=TARGET_RED, edgecolor="black", linewidth=0.8, zorder=4))
        for r in (0.08, 0.055, 0.03):
            ax.add_patch(Circle((x, y), r, fill=False, edgecolor="black", linewidth=1.0, zorder=5))
        ax.add_patch(Circle((x, y), 0.016, facecolor=TARGET_WHITE, edgecolor="black", linewidth=0.8, zorder=6))
        ax.plot([x - 0.011, x + 0.011], [y, y], color="black", linewidth=0.8, zorder=7)
        ax.plot([x, x], [y - 0.011, y + 0.011], color="black", linewidth=0.8, zorder=7)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("driver's left  <-  gaze (deg)  ->  driver's right", fontsize=8)
    ax.set_ylabel("down  <-  (deg)  ->  up", fontsize=8)
    if off:
        ax.text(0, -SPAN_V * k + 0.02, "OFF ROAD", ha="center", va="bottom", fontsize=9, color="red",
                fontweight="bold", zorder=8)


def gaze_angles(gx, yaw_rel, pitch_rel):
    """(across, up) in degrees from the driver's seat. Head yaw + = driver's right; iris offset
    + = image right = driver's left; pitch + = looking down."""
    if gx is None or yaw_rel is None or pitch_rel is None:
        return None, None
    return yaw_rel - IRIS_DEG * gx, -pitch_rel


def gaze_xy(h, v):
    k = 1.0 / SPAN_H
    return (float(np.clip(h * k, -0.97, 0.97)), float(np.clip(v * k, -SPAN_V * k * 0.95, SPAN_V * k * 0.95)))


def off_road(h, v):
    return h is not None and v is not None and (abs(h) > WS_SIDE or v > WS_UP or v < -WS_DOWN)


# ---------------------------------------------------------------- live dashboard

class Dashboard:
    """Line graphs on the left (EAR, NAR, MAR, head pose), the gaze box in its own column on the right."""

    def __init__(self, height_px=720, width_px=1260):
        dpi = 100
        self.fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, facecolor="white")
        gs = self.fig.add_gridspec(4, 4, height_ratios=[1, 1, 1, 1.25], width_ratios=[1, 1, 1, 3.8],
                                   hspace=0.85, wspace=0.55, left=0.06, right=0.985, top=0.94, bottom=0.08)
        self.ax, self.ln = {}, {}
        for r, (k, (_, title, ylab, col, ends, ylim)) in enumerate(LINES.items()):
            a = self.fig.add_subplot(gs[r, :3])
            style(a, title, ylab)
            self.ln[k], = a.plot([], [], color=col, linewidth=2)
            a.set_xlim(0, WINDOW)
            if ylim:
                a.set_ylim(*ylim)
            end_labels(a, *ends)
            self.ax[k] = a
        for c, k in enumerate(("yaw", "pitch", "roll")):
            a = self.fig.add_subplot(gs[3, c])
            style(a, k.capitalize(), "Degrees" if c == 0 else "", "Frame", bold=True)
            self.ln[k], = a.plot([], [], "b--", linewidth=1.4)
            a.set_xlim(0, WINDOW)
            self.ax[k] = a
        self.ax["gaze"] = self.fig.add_subplot(gs[:, 3])
        self.hist = {k: deque(maxlen=WINDOW) for k in ("ear", "nar", "mar", "yaw", "pitch", "roll")}
        self.trail = deque(maxlen=30)

    def update(self, v, recording):
        for k in self.hist:
            self.hist[k].append(np.nan if v.get(k) is None else v[k])
        for k, h in self.hist.items():
            y = np.array(h, float)
            self.ln[k].set_data(np.arange(len(y)), y)
            a = self.ax[k]
            ok = y[~np.isnan(y)]
            if k in ("yaw", "pitch", "roll") and ok.size:
                lo, hi = ok.min(), ok.max()
                pad = max(3.0, (hi - lo) * 0.15)
                a.set_ylim(lo - pad, hi + pad)
            elif k == "nar" and ok.size:
                lo, hi = ok.min(), ok.max()
                pad = max(0.03, (hi - lo) * 0.2)
                a.set_ylim(hi + pad, lo - pad)                      # inverted: Down at the bottom
            elif k in ("ear", "mar") and ok.size:
                y0, y1 = LINES[k][5]
                a.set_ylim(y0, max(y1, float(ok.max()) * 1.1))
        for name, keys in (("ear", ["ear"]), ("nar", ["nar"]), ("mar", ["mar"]), ("pose", ["yaw", "pitch", "roll"])):
            for k in keys:
                base = LINES[k][1] if k in LINES else k.capitalize()
                rec = recording.get(name)
                self.ax[k].set_title(base + ("  ● REC" if rec else ""), fontsize=10,
                                     fontweight="bold" if k in ("yaw", "pitch", "roll") else "normal",
                                     color="red" if rec else "black")
        h, gv = v.get("gaze_h"), v.get("gaze_v")
        if h is not None and gv is not None:
            self.trail.append(gaze_xy(h, gv))
        draw_gaze_box(self.ax["gaze"], h, gv, np.array(self.trail) if self.trail else None,
                      title="Gaze from the driver's seat" + ("  ● REC" if recording.get("gaze") else ""),
                      off=off_road(h, gv))
        if recording.get("gaze"):
            self.ax["gaze"].title.set_color("red")
        self.fig.canvas.draw()
        img = np.asarray(self.fig.canvas.buffer_rgba())[..., :3]
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------- recording

FIELDS = {"ear": ["ear"], "nar": ["nar"], "mar": ["mar"], "pose": ["yaw", "pitch", "roll"],
          "gaze": ["gaze_h", "gaze_v", "gaze_x", "yaw_rel", "pitch_rel", "off_road"]}


def save_recording(name, rows, folder: Path):
    """One graph (PNG, 200 dpi) and its data (CSV) for one indicator's recording."""
    if not rows:
        return None
    stamp = time.strftime("%H%M%S")
    t = np.array([r["t"] for r in rows]) - rows[0]["t"]
    frames = np.arange(len(rows))
    with open(folder / f"{name}_{stamp}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "t_s"] + FIELDS[name])
        for i, r in enumerate(rows):
            w.writerow([i, f"{t[i]:.3f}"] + [("" if r.get(k) is None else r[k]) for k in FIELDS[name]])
    col = lambda k: np.array([np.nan if r.get(k) is None else r[k] for r in rows], float)   # noqa: E731
    if name in LINES:
        _, title, ylab, c, ends, ylim = LINES[name]
        fig, ax = plt.subplots(figsize=(7, 2.6), dpi=200, facecolor="white")
        ax.plot(frames, col(name), color=c, linewidth=2)
        style(ax, title, ylab, "Frame")
        ax.set_xlim(0, max(len(rows) - 1, 1))
        if ylim:
            ax.set_ylim(ylim[0], max(ylim[1], np.nanmax(col(name)) * 1.1))
        if name == "nar":
            ax.invert_yaxis()
        end_labels(ax, *ends)
    elif name == "pose":
        fig, axs = plt.subplots(1, 3, figsize=(7, 3.2), dpi=200, facecolor="white")
        for a, k in zip(axs, ("yaw", "pitch", "roll")):
            a.plot(frames, col(k), "b--", linewidth=1.4)
            style(a, k.capitalize(), "Degrees" if k == "yaw" else "", "Frame", bold=True)
    else:
        fig, ax = plt.subplots(figsize=(7, 4.2), dpi=200, facecolor="white")
        gh, gv = col("gaze_h"), col("gaze_v")
        ok = ~(np.isnan(gh) | np.isnan(gv))
        trail = np.array([gaze_xy(a, b) for a, b in zip(gh[ok], gv[ok])]) if ok.any() else None
        last = (gh[ok][-1], gv[ok][-1]) if ok.any() else (None, None)
        share = np.mean([off_road(a, b) for a, b in zip(gh[ok], gv[ok])]) if ok.any() else 0
        draw_gaze_box(ax, *last, trail=trail, title=f"Gaze  ({share:.0%} of frames off road)")
    fig.tight_layout()
    png = folder / f"{name}_{stamp}.png"
    fig.savefig(png, facecolor="white")
    plt.close(fig)
    return png


# ---------------------------------------------------------------- picture close-ups ('p')

DOT_RED, ARROW_YELLOW = "#E8323C", "#F2E519"
MOUTH6 = (82, 312, 308, 317, 87, 78)          # figure (b) p1..p6: upper L/R, right corner, lower R/L, left corner
NOSE4 = {"bridge": 168, "tip": 1, "ala L": 98, "ala R": 327}


def _window(P, idx, aspect, pad):
    """(x0, x1, y0, y1) around landmarks idx with width/height = aspect."""
    sel = P[list(idx), :2]
    (x0, y0), (x1, y1) = sel.min(0), sel.max(0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = max(x1 - x0, (y1 - y0) * aspect) * (1 + 2 * pad)
    h = w / aspect
    return cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2


def _closeup_axes(raw, win, width_in=6.0):
    x0, x1, y0, y1 = win
    fig = plt.figure(figsize=(width_in, width_in * (y1 - y0) / (x1 - x0)), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(cv2.cvtColor(raw, cv2.COLOR_BGR2RGB), interpolation="lanczos")
    ax.set_xlim(x0, x1), ax.set_ylim(y1, y0), ax.axis("off")
    return fig, ax


def _corner(ax, label, value, sub=None):
    """Small value label in the top-left corner."""
    ax.text(0.012, 0.975, f"{label} {value:.3f}" if value is not None else f"{label} n/a", transform=ax.transAxes,
            fontsize=8, fontweight="bold", va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="none", alpha=0.85))
    if sub:
        ax.text(0.012, 0.86, sub, transform=ax.transAxes, fontsize=6, va="top", ha="left", color="0.25",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="none", alpha=0.75))


def _arrow(ax, a, b, color, lw):
    ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="<->", color=color, lw=lw, shrinkA=0, shrinkB=0,
                                                    mutation_scale=14))


GLOW = [pe.withStroke(linewidth=3, foreground="white")]


def picture_eye(raw, P, ear_total, out):
    """Figure (a): the larger (closer) eye, zoomed, p1..p6 with the EAR openings and width."""
    widths = {k: float(np.linalg.norm(P[idx[0], :2] - P[idx[3], :2])) for k, idx in (("right", lh.EAR_R), ("left", lh.EAR_L))}
    side = max(widths, key=widths.get)
    idx = lh.EAR_R if side == "right" else lh.EAR_L
    pts = [P[i, :2] for i in idx]
    fig, ax = _closeup_axes(raw, _window(P, idx, 2.2, 0.45))
    _arrow(ax, pts[0], pts[3], ARROW_YELLOW, 2.4)                      # |p1-p4|
    _arrow(ax, pts[1], pts[5], ARROW_YELLOW, 2.4)                      # |p2-p6|
    _arrow(ax, pts[2], pts[4], ARROW_YELLOW, 2.4)                      # |p3-p5|
    ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=150, color=DOT_RED, zorder=5, edgecolors="none")
    offs = [(-22, 0), (0, 18), (0, 18), (22, 0), (0, -20), (0, -20)]   # p1 left, p2/p3 above, p4 right, p5/p6 below
    for k, (pt, (dx, dy)) in enumerate(zip(pts, offs), 1):
        ax.annotate(rf"$\mathbfit{{p}}_{{\mathbf{{{k}}}}}$", pt, xytext=(dx, dy), textcoords="offset points",
                    ha="center", va="center", fontsize=20, color="black", path_effects=GLOW, zorder=6)
    _corner(ax, "EAR", ear_total, f"this eye ({side}) {lh.ear(P, idx):.3f}  ·  (|p2-p6|+|p3-p5|) / 2|p1-p4|")
    f = out / time.strftime("picture_%H%M%S_eye.png")
    fig.savefig(f), plt.close(fig)
    return f


def picture_mouth(raw, P, mar_value, out):
    """Figure (b): the mouth, zoomed, p1..p6 with two openings and the width."""
    pts = [P[i, :2] for i in MOUTH6]
    fig, ax = _closeup_axes(raw, _window(P, MOUTH6, 1.6, 0.35))
    _arrow(ax, pts[0], pts[4], "black", 1.8)                           # p1-p5
    _arrow(ax, pts[1], pts[3], "black", 1.8)                           # p2-p4
    _arrow(ax, pts[5], pts[2], "black", 1.8)                           # p6-p3
    ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=90, color=DOT_RED, zorder=5, edgecolors="none")
    offs = [(-14, 10), (14, 10), (16, -6), (12, -14), (-12, -14), (-16, -6)]
    for k, (pt, (dx, dy)) in enumerate(zip(pts, offs), 1):
        ax.annotate(f"p{k}", pt, xytext=(dx, dy), textcoords="offset points", ha="center", va="center",
                    fontsize=12, color="black", path_effects=GLOW, zorder=6)
    _corner(ax, "MAR", mar_value, "openings / width (DRIFTS also averages the centre 13-14)")
    f = out / time.strftime("picture_%H%M%S_mouth.png")
    fig.savefig(f), plt.close(fig)
    return f


def picture_nose(raw, P, nar_value, nar_neutral, out):
    """NAR: nose length (bridge -> tip) over nostril width, with the neutral length dashed.
    Looking down the tip comes toward the camera and the length line grows; looking up it shrinks."""
    b, t, l, r = (P[NOSE4[k], :2] for k in ("bridge", "tip", "ala L", "ala R"))
    fig, ax = _closeup_axes(raw, _window(P, NOSE4.values(), 1.15, 0.40), width_in=6.0)
    width = float(np.linalg.norm(l - r))
    if nar_neutral:
        u = (t - b) / (np.linalg.norm(t - b) or 1.0)
        n_end = b + u * nar_neutral * width
        nrm = np.array([u[1], -u[0]]) * width * 0.16                  # beside the live line, on the image-left side
        if nrm[0] > 0:
            nrm = -nrm
        ax.plot([b[0] + nrm[0], n_end[0] + nrm[0]], [b[1] + nrm[1], n_end[1] + nrm[1]], "--", color="0.35", lw=2.0)
        ax.plot([n_end[0] + nrm[0] - width * 0.05, n_end[0] + nrm[0] + width * 0.05], [n_end[1] + nrm[1]] * 2,
                color="0.35", lw=2.0)
        ax.annotate("neutral\nlength", n_end + nrm, xytext=(-10, 0), textcoords="offset points", fontsize=10,
                    color="0.25", ha="right", va="center", path_effects=GLOW)
    _arrow(ax, b, t, "black", 1.8)                                    # length
    _arrow(ax, l, r, "black", 1.8)                                    # width
    pts = [b, t, l, r]
    ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=110, color=DOT_RED, zorder=5, edgecolors="none")
    for name, pt, (dx, dy) in (("bridge", b, (0, 14)), ("tip", t, (22, 0)), ("ala", l, (-16, -12)), ("ala", r, (16, -12))):
        ax.annotate(name, pt, xytext=(dx, dy), textcoords="offset points", ha="center", va="center", fontsize=12,
                    color="black", path_effects=GLOW, zorder=6)
    sub = "length / width"
    if nar_value and nar_neutral:
        d = (nar_value / nar_neutral - 1) * 100
        sub = f"neutral {nar_neutral:.3f}  ·  {d:+.0f}% " + ("longer: looking down" if d > 2 else
                                                            "shorter: looking up" if d < -2 else "level")
    _corner(ax, "NAR", nar_value, sub)
    f = out / time.strftime("picture_%H%M%S_nose.png")
    fig.savefig(f), plt.close(fig)
    return f


def take_picture(raw, P, v, nar_neutral, out):
    """'p': the plain frame plus eye, mouth and nose close-ups, each with its value in the corner."""
    files = [out / time.strftime("picture_%H%M%S_frame.png")]
    cv2.imwrite(str(files[0]), raw)
    files += [picture_eye(raw, P, v.get("ear"), out), picture_mouth(raw, P, v.get("mar"), out),
              picture_nose(raw, P, v.get("nar"), nar_neutral, out)]
    for f in files:
        print(f"saved {f}")
    return files


# ---------------------------------------------------------------- main loop

# Major landmarks as dots, colours and size as the 98-point reference figure
# (jaw blue, brows green, eyes + pupils magenta, nose bridge black, nose base yellow,
# outer lips cyan, inner lips red). MediaPipe indices; EAR, NAR and MAR points included.
DOT_GROUPS = (
    ((238, 4, 8), (127, 234, 93, 132, 58, 172, 136, 150, 149, 176, 148, 152,
                   377, 400, 378, 379, 365, 397, 288, 361, 323, 454, 356)),               # jaw
    ((12, 240, 12), (70, 63, 105, 66, 107, 46, 53, 52, 65, 55,
                     300, 293, 334, 296, 336, 276, 283, 282, 295, 285)),                  # brows
    ((232, 12, 240), (33, 160, 159, 158, 133, 153, 145, 144,
                      362, 385, 386, 387, 263, 373, 374, 380, 468, 473)),                 # eyes + pupils
    ((1, 1, 5), (168, 6, 195, 1)),                                                        # nose bridge -> tip
    ((15, 251, 251), (98, 97, 2, 326, 327)),                                              # nose base
    ((243, 246, 18), (61, 40, 37, 0, 267, 270, 291, 321, 314, 17, 84, 91)),              # outer lips
    ((12, 10, 235), (78, 82, 13, 312, 308, 317, 14, 87)),                                 # inner lips (MAR)
)
AXIS_COLOURS = {"x": ((0, 0, 255), "pitch"), "y": ((0, 200, 0), "yaw"), "z": ((255, 0, 0), "roll")}


def draw_dots(frame, P):
    jaw_w = float(np.linalg.norm(P[234, :2] - P[454, :2]))
    r = max(2, int(round(0.0097 * jaw_w)))            # ~9 px dots on a 440 px-wide face, as the figure
    for col, idx in DOT_GROUPS:
        for i in idx:
            cv2.circle(frame, (int(round(P[i, 0])), int(round(P[i, 1]))), r, col, -1, cv2.LINE_AA)
    return jaw_w


def draw_nose_axes(frame, P, mat, jaw_w):
    """Head axes from the nose tip (facesignals.nose_axes): pitch = across the face,
    yaw = up the face, roll = out of the face (where the head points)."""
    if mat is None:
        return
    tip = (float(P[1, 0]), float(P[1, 1]))
    ends = lh.fs.nose_axes(mat, tip, 0.18 * jaw_w)
    t = max(2, int(round(jaw_w / 150)))
    for k in ("z", "x", "y"):
        col, name = AXIS_COLOURS[k]
        e = ends[k]
        cv2.arrowedLine(frame, (int(tip[0]), int(tip[1])), (int(e[0]), int(e[1])), col, t, cv2.LINE_AA, tipLength=0.12)
        # label just beyond the arrow tip, along the arrow, so the three never sit on each other
        d = np.array([e[0] - tip[0], e[1] - tip[1]])
        u = d / (np.linalg.norm(d) or 1.0)
        fs_ = max(0.4, jaw_w / 600)
        (tw, th), _ = cv2.getTextSize(name, cv2.FONT_HERSHEY_SIMPLEX, fs_, t)
        lx = e[0] + u[0] * (8 + tw * 0.6) - tw / 2
        ly = e[1] + u[1] * (8 + th * 1.2) + th / 2
        for c, w in (((255, 255, 255), t + 2), (col, t)):
            cv2.putText(frame, name, (int(lx), int(ly)), cv2.FONT_HERSHEY_SIMPLEX, fs_, c, w, cv2.LINE_AA)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--camera", type=int)
    src.add_argument("--video")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--record", default="", help="start recording at launch: ear,nar,mar,pose,gaze or all")
    ap.add_argument("--no-window", action="store_true", help="no display (with --video); saves at the end")
    ap.add_argument("--no-overlay", action="store_true", help="hide the landmark dots and head axes")
    ap.add_argument("--out", help="output folder (default captures/graphs-<timestamp>)")
    ap.add_argument("--allow-pi", action="store_true")
    args = ap.parse_args(argv)
    lh.refuse_on_pi(args.allow_pi)

    out = Path(args.out) if args.out else lh.HERE / "captures" / time.strftime("graphs-%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    names = list(FIELDS)
    want = names if args.record == "all" else [n for n in args.record.split(",") if n in FIELDS]
    recording = {n: (n in want) for n in names}
    rows = {n: [] for n in names}

    cap = cv2.VideoCapture(args.video if args.video else args.camera)
    if args.camera is not None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit("Could not open the camera / video")
    lmk = lh.Landmarker(video=True)
    dash, t0, n, saved = None, time.time(), 0, []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    print("keys: 1 EAR  2 NAR  3 MAR  4 head pose  5 gaze  a all  p picture  c re-centre  s snapshot  q quit")

    def toggle(name):
        if recording[name]:
            png = save_recording(name, rows[name], out)
            if png:
                saved.append(png)
                print(f"saved {png}")
            rows[name] = []
        recording[name] = not recording[name]

    view = None
    neutral, calib = None, []          # neutral head (yaw, pitch): median of the first ~1 s with a face ('c' re-centres)
    nar_neutral, nar_calib, last = None, [], None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        now = (n / fps) if args.video else (time.time() - t0)
        r = lmk(frame, int(now * 1000))
        v = {}
        if r is not None:
            P, _, mat = r
            v = indicators(P, mat)
            last = (frame.copy(), P, v)                          # plain frame for 'p' pictures
            if v.get("nar") is not None and nar_neutral is None:
                nar_calib.append(v["nar"])
                if len(nar_calib) >= 30:
                    nar_neutral = float(np.median(nar_calib))
            if not args.no_overlay:
                draw_nose_axes(frame, P, mat, draw_dots(frame, P))
        if v.get("pitch") is not None and v.get("yaw") is not None and neutral is None:
            calib.append((v["yaw"], v["pitch"]))
            if len(calib) >= 30:
                neutral = tuple(np.median(np.array(calib), 0))
                print(f"neutral yaw {neutral[0]:+.1f}, pitch {neutral[1]:+.1f} deg")
        ref = neutral if neutral is not None else (tuple(np.median(np.array(calib), 0)) if calib else (0.0, 0.0))
        v["yaw_rel"] = None if v.get("yaw") is None else v["yaw"] - ref[0]
        v["pitch_rel"] = None if v.get("pitch") is None else v["pitch"] - ref[1]
        v["gaze_h"], v["gaze_v"] = gaze_angles(v.get("gaze_x"), v.get("yaw_rel"), v.get("pitch_rel"))
        v["off_road"] = off_road(v["gaze_h"], v["gaze_v"]) if v.get("gaze_h") is not None else None
        for name in names:
            if recording[name]:
                rows[name].append(dict(v, t=now))
        h = 720
        fh, fw = frame.shape[:2]
        cw = min(fw, int(fh * 3 / 4))                            # 3:4 portrait centre crop keeps the window < 1920 px
        sq = frame[:, (fw - cw) // 2:(fw - cw) // 2 + cw]
        cam = cv2.resize(sq, (int(h * 3 / 4), h))
        if dash is None:
            dash = Dashboard(h)
        view = np.hstack([cam, dash.update(v, recording)])
        n += 1
        if not args.no_window:
            cv2.imshow("DRIFTS live graphs (dev)", view)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            if chr(k) in KEYS:
                toggle(KEYS[chr(k)])
            elif k == ord("a"):
                for name in names:
                    if recording[name] == recording[names[0]]:
                        toggle(name)
            elif k == ord("p") and last is not None:
                take_picture(*last, nar_neutral or (float(np.median(nar_calib)) if nar_calib else None), out)
            elif k == ord("c"):
                neutral, calib, nar_neutral, nar_calib = None, [], None, []
                print("re-centring: look at the road for 1 s")
            elif k == ord("s"):
                p = out / time.strftime("dashboard_%H%M%S.png")
                cv2.imwrite(str(p), view)
                print(f"saved {p}")
    cap.release()
    lmk.close()
    for name in names:                       # stop anything still recording -> save it
        if recording[name]:
            toggle(name)
    if view is not None:
        cv2.imwrite(str(out / "dashboard_last.png"), view)
    if args.no_window and last is not None:      # headless runs: close-ups of the last frame
        take_picture(*last, nar_neutral or (float(np.median(nar_calib)) if nar_calib else None), out)
    if not args.no_window:
        cv2.destroyAllWindows()
    print(f"Wrote {out}")
    return out, saved


if __name__ == "__main__":
    main()
