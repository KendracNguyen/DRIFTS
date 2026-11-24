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
    mouse  click Download PNG under the Eye / Nose / Mouth zoom tiles

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
        self.ax["gaze"] = self.fig.add_subplot(gs[0:2, 3])
        self.ax_zoom = self.fig.add_subplot(gs[2:, 3])           # area for the live zoom tiles (drawn with OpenCV)
        self.ax_zoom.axis("off")
        self.zoom_box = None
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
        if self.zoom_box is None:                                  # (x, y, w, h) in panel pixels, top-left origin
            bb = self.ax_zoom.get_window_extent()
            H = self.fig.canvas.get_width_height()[1]
            self.zoom_box = (int(bb.x0), int(H - bb.y1), int(bb.width), int(bb.height))
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


def take_picture(raw, P, v, nar_neutral, out, mat=None):
    """'p': the plain frame plus eye, mouth and nose close-ups (value in the corner) and the head pose."""
    files = [out / time.strftime("picture_%H%M%S_frame.png")]
    cv2.imwrite(str(files[0]), raw)
    files += [picture_eye(raw, P, v.get("ear"), out), picture_mouth(raw, P, v.get("mar"), out),
              picture_nose(raw, P, v.get("nar"), nar_neutral, out)]
    if mat is not None:
        files.append(picture_mannequin(P, mat, v, out))
    for f in files:
        print(f"saved {f}")
    return files


# ---------------------------------------------------------------- live zoom tiles with Download PNG buttons

ZOOM_TILES = ("eye", "nose", "mouth")
ZOOM_TITLES = {"eye": "Eye  ·  EAR  (p1–p6)", "nose": "Nose  ·  NAR", "mouth": "Mouth  ·  MAR  (p1–p6)"}


def _tile_crop(raw, win, tw, th):
    x0, x1, y0, y1 = win
    src = np.float32([[x0, y0], [x1, y0], [x0, y1]])
    dst = np.float32([[0, 0], [tw, 0], [0, th]])
    M = cv2.getAffineTransform(src, dst)
    tile = cv2.warpAffine(raw, M, (tw, th), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return tile, (lambda p: (M[0, 0] * p[0] + M[0, 1] * p[1] + M[0, 2], M[1, 0] * p[0] + M[1, 1] * p[1] + M[1, 2]))


def zoom_tile(kind, raw, P, v, nar_neutral, tw, th):
    """One live close-up (BGR tile) with the figure's points and lines, value in the corner."""
    aspect = tw / th
    if kind == "eye":
        w = {s_: float(np.linalg.norm(P[i[0], :2] - P[i[3], :2])) for s_, i in (("R", lh.EAR_R), ("L", lh.EAR_L))}
        idx = lh.EAR_R if w["R"] >= w["L"] else lh.EAR_L
        tile, to = _tile_crop(raw, _window(P, idx, aspect, 0.40), tw, th)
        q = [to(P[i]) for i in idx]
        lines, lc = [(0, 3), (1, 5), (2, 4)], (25, 229, 242)            # yellow, as figure (a)
        labels = [(f"p{k + 1}", q[k], off) for k, off in
                  enumerate([(-14, 0), (0, -13), (0, -13), (14, 0), (0, 13), (0, 13)])]
        val = ("EAR", v.get("ear"))
    elif kind == "mouth":
        tile, to = _tile_crop(raw, _window(P, MOUTH6, aspect, 0.30), tw, th)
        q = [to(P[i]) for i in MOUTH6]
        lines, lc = [(0, 4), (1, 3), (5, 2)], (20, 20, 20)              # black, as figure (b)
        labels = [(f"p{k + 1}", q[k], off) for k, off in
                  enumerate([(-10, -10), (10, -10), (14, 0), (10, 11), (-10, 11), (-14, 0)])]
        val = ("MAR", v.get("mar"))
    else:
        ids = [NOSE4[k] for k in ("bridge", "tip", "ala L", "ala R")]
        tile, to = _tile_crop(raw, _window(P, ids, aspect, 0.12), tw, th)
        q = [to(P[i]) for i in ids]
        lines, lc = [(0, 1), (2, 3)], (20, 20, 20)
        labels = []
        val = ("NAR", v.get("nar"))
        if nar_neutral and v.get("nar"):
            b, t = np.array(q[0]), np.array(q[1])
            u = (t - b) / (np.linalg.norm(t - b) or 1.0)
            width = float(np.linalg.norm(np.array(q[2]) - np.array(q[3])))
            nrm = np.array([u[1], -u[0]]) * width * 0.18
            nrm = -nrm if nrm[0] > 0 else nrm
            e = b + u * nar_neutral * width
            for k in range(0, 10, 2):                                   # dashed neutral length
                a_, b_ = b + (e - b) * k / 10 + nrm, b + (e - b) * (k + 1) / 10 + nrm
                cv2.line(tile, _I(a_), _I(b_), (90, 90, 90), 2, cv2.LINE_AA, shift=4)
    for a, b in lines:
        cv2.line(tile, _I(q[a]), _I(q[b]), (255, 255, 255), 4, cv2.LINE_AA, shift=4)
        cv2.line(tile, _I(q[a]), _I(q[b]), lc, 2, cv2.LINE_AA, shift=4)
    for p in q:
        cv2.circle(tile, _I(p), 5 * 16, (255, 255, 255), -1, cv2.LINE_AA, shift=4)
        cv2.circle(tile, _I(p), 4 * 16, RED_BGR, -1, cv2.LINE_AA, shift=4)
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(tile, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for text, p, (dx, dy) in labels:
        f = _font(12)
        tw_ = d.textlength(text, font=f)
        d.text((p[0] + dx - tw_ / 2, p[1] + dy - 7), text, font=f, fill=(0, 0, 0), stroke_width=2,
               stroke_fill=(255, 255, 255))
    txt = f"{val[0]} {val[1]:.3f}" if val[1] is not None else f"{val[0]} –"
    f = _font(11)
    d.rounded_rectangle([4, 4, 12 + d.textlength(txt, font=f), 21], radius=4, fill=(255, 255, 255))
    d.text((8, 6), txt, font=f, fill=(20, 20, 20))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def draw_zoom_panel(panel, box, raw, P, v, nar_neutral, flash):
    """Live close-ups: the eye wide across the top, nose and mouth side by side below, each with a
    '↓ PNG' button in its corner. Returns {kind: (x0, y0, x1, y1)} button rectangles in panel pixels."""
    from PIL import Image, ImageDraw
    x, y, w, h = box
    gap, title_h = 10, 17
    eye_h = int((h - 2 * title_h - gap) * 0.5)
    low_h = h - 2 * title_h - gap - eye_h
    half = (w - gap) // 2
    rects = {"eye": (x, y + title_h, w, eye_h),
             "nose": (x, y + 2 * title_h + gap + eye_h, half, low_h),
             "mouth": (x + half + gap, y + 2 * title_h + gap + eye_h, w - half - gap, low_h)}
    buttons = {}
    for kind, (tx, ty, tw, th) in rects.items():
        panel[ty:ty + th, tx:tx + tw] = zoom_tile(kind, raw, P, v, nar_neutral, tw, th) if P is not None else 235
        cv2.rectangle(panel, (tx, ty), (tx + tw - 1, ty + th - 1), (190, 190, 190), 1)
        buttons[kind] = (tx + tw - 74, ty + 5, tx + tw - 6, ty + 25)       # top-right, opposite the value
    pil = Image.fromarray(cv2.cvtColor(panel, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for kind, (tx, ty, tw, th) in rects.items():
        d.text((tx, ty - title_h + 1), ZOOM_TITLES[kind], font=_font(12), fill=(40, 40, 40))
        bx0, by0, bx1, by1 = buttons[kind]
        saved = flash.get(kind, 0) > time.time()
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=5, fill=(46, 125, 50) if saved else (107, 15, 26),
                            outline=(255, 255, 255))
        label = "Saved ✓" if saved else "↓ PNG"
        f = _font(12)
        d.text(((bx0 + bx1 - d.textlength(label, font=f)) / 2, by0 + 3), label, font=f, fill=(255, 255, 255))
    panel[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    return buttons


# ---------------------------------------------------------------- face overlay (poster style)
# One restrained palette: thin white contours and points; DRIFTS red only on the points the
# indicators measure (EAR 12, MAR 8, NAR 4); a slim pitch / yaw / roll axis at the nose; a small
# translucent read-out. Drawn on the display-size image with sub-pixel anti-aliasing.
MEASURED = tuple(lh.EAR_R) + tuple(lh.EAR_L) + tuple(MOUTH6) + (13, 14, 78, 308) + (168, 1, 98, 327)
KEY_POINTS = (70, 63, 105, 66, 107, 300, 293, 334, 296, 336,                 # brows
              33, 160, 159, 158, 133, 153, 145, 144, 362, 385, 386, 387, 263, 373, 374, 380,   # eyes
              168, 6, 195, 1, 98, 97, 2, 326, 327,                          # nose
              61, 40, 37, 0, 267, 270, 291, 321, 314, 17, 84, 91,           # outer lips
              78, 82, 13, 312, 308, 317, 14, 87,                            # inner lips
              10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378,
              400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109)
WHITE_BGR, RED_BGR = (255, 255, 255), (60, 50, 232)                         # DRIFTS red #E8323C
AXES = {"x": ((76, 76, 232), "pitch"), "y": ((113, 179, 60), "yaw"), "z": ((242, 140, 61), "roll")}
_FONT = {}


def _font(px):
    from PIL import ImageFont
    from matplotlib import font_manager
    if px not in _FONT:
        _FONT[px] = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), px)
    return _FONT[px]


def _I(p):                                       # sub-pixel point for cv2 (shift=4)
    return (int(round(p[0] * 16)), int(round(p[1] * 16)))


def draw_overlay(img, P, mat, v):
    """Poster-style face overlay on the display-size image `img` (P in its pixel coords)."""
    from mediapipe.tasks.python.vision import FaceLandmarksConnections as C
    jaw_w = float(np.linalg.norm(P[234, :2] - P[454, :2]))
    # contours: face oval faint, features a little stronger
    layer = img.copy()
    for conns, alpha in ((C.FACE_LANDMARKS_FACE_OVAL, 0.35),):
        for c in conns:
            cv2.line(layer, _I(P[c.start]), _I(P[c.end]), WHITE_BGR, 1, cv2.LINE_AA, shift=4)
        img[:] = cv2.addWeighted(layer, alpha, img, 1 - alpha, 0)
    layer = img.copy()
    for conns in (C.FACE_LANDMARKS_LEFT_EYE, C.FACE_LANDMARKS_RIGHT_EYE, C.FACE_LANDMARKS_LEFT_EYEBROW,
                  C.FACE_LANDMARKS_RIGHT_EYEBROW, C.FACE_LANDMARKS_LIPS):
        for c in conns:
            cv2.line(layer, _I(P[c.start]), _I(P[c.end]), WHITE_BGR, 1, cv2.LINE_AA, shift=4)
    for chain in ((168, 6, 195, 1), (98, 97, 2, 326, 327)):           # nose: bridge line and nostril base
        for a, b in zip(chain, chain[1:]):
            cv2.line(layer, _I(P[a]), _I(P[b]), WHITE_BGR, 1, cv2.LINE_AA, shift=4)
    for iris in (range(469, 473), range(474, 478)):                  # iris as a thin ring
        ring = P[list(iris), :2]
        cen = ring.mean(0)
        rad = float(np.linalg.norm(ring - cen, axis=1).mean())
        cv2.circle(layer, _I(cen), int(rad * 16), WHITE_BGR, 1, cv2.LINE_AA, shift=4)
    img[:] = cv2.addWeighted(layer, 0.6, img, 0.4, 0)
    # points: small white; measured ones red with a white ring
    r = max(1.6, jaw_w * 0.0075)
    for i in KEY_POINTS:
        if i not in MEASURED:
            cv2.circle(img, _I(P[i]), int(r * 16), WHITE_BGR, -1, cv2.LINE_AA, shift=4)
    for i in set(MEASURED):
        cv2.circle(img, _I(P[i]), int((r * 1.45 + 1.2) * 16), WHITE_BGR, -1, cv2.LINE_AA, shift=4)
        cv2.circle(img, _I(P[i]), int(r * 1.45 * 16), RED_BGR, -1, cv2.LINE_AA, shift=4)
    # head axes: slim, from the nose tip
    labels = []
    if mat is not None:
        tip = (float(P[1, 0]), float(P[1, 1]))
        ends = lh.fs.nose_axes(mat, tip, 0.30 * jaw_w)
        for k in ("z", "x", "y"):
            col, name = AXES[k]
            e = np.array(ends[k])
            d = e - np.array(tip)
            L = float(np.linalg.norm(d)) or 1.0
            if L > 0.9 * jaw_w:                                         # keep roll (out of the face) on screen
                e = np.array(tip) + d / L * 0.9 * jaw_w
                L = 0.9 * jaw_w
            u = d / (np.linalg.norm(d) or 1.0)
            hl = min(0.07 * jaw_w + 8, L * 0.45)                         # solid triangular head, as the mannequin
            base = e - u * hl
            nrm = np.array([-u[1], u[0]]) * hl * 0.42
            cv2.line(img, _I(tip), _I(base), (20, 20, 20), 6, cv2.LINE_AA, shift=4)
            cv2.line(img, _I(tip), _I(base), col, 4, cv2.LINE_AA, shift=4)
            tri = np.array([_I(e), _I(base + nrm), _I(base - nrm)], np.int32)
            cv2.fillPoly(img, [tri], col, cv2.LINE_AA, shift=4)
            cv2.polylines(img, [tri], True, (20, 20, 20), 1, cv2.LINE_AA, shift=4)
            labels.append((name, e + u * 16, col))
        cv2.circle(img, _I(tip), int(3.2 * 16), WHITE_BGR, -1, cv2.LINE_AA, shift=4)
    # text with Pillow: axis labels and the read-out
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
    over = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    f_lab = _font(18)
    for name, pos, col in labels:
        tw = d.textlength(name, font=f_lab)
        x, y = pos[0] - tw / 2, pos[1] - 11
        d.text((x, y), name, font=f_lab, fill=(col[2], col[1], col[0], 255),
               stroke_width=3, stroke_fill=(0, 0, 0, 200))
    fmt = lambda x, f: "–" if x is None else format(x, f)                      # noqa: E731
    rows = [("EAR", fmt(v.get("ear"), ".3f")), ("MAR", fmt(v.get("mar"), ".3f")), ("NAR", fmt(v.get("nar"), ".3f")),
            ("yaw", fmt(v.get("yaw"), "+.1f") + "°"), ("pitch", fmt(v.get("pitch"), "+.1f") + "°"),
            ("roll", fmt(v.get("roll"), "+.1f") + "°")]
    f_k, f_v = _font(12), _font(15)
    W, H = pil.size
    pad, colw, rowh = 12, 74, 34
    bw, bh = pad * 2 + colw * 3, pad * 2 + rowh * 2 + 18
    x0, y0 = 14, H - bh - 14
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=10, fill=(12, 12, 16, 150))
    for k, (key, val) in enumerate(rows):
        cx, cy = x0 + pad + (k % 3) * colw, y0 + pad + (k // 3) * rowh
        d.text((cx, cy), key, font=f_k, fill=(190, 190, 196, 255))
        d.text((cx, cy + 13), val, font=f_v, fill=(255, 255, 255, 255))
    ly = y0 + bh - 18
    d.ellipse([x0 + pad, ly + 3, x0 + pad + 9, ly + 12], fill=(232, 50, 60, 255), outline=(255, 255, 255, 255))
    d.text((x0 + pad + 14, ly), "measured points  ·  MediaPipe Face Landmarker", font=_font(11),
           fill=(200, 200, 205, 255))
    pil = Image.alpha_composite(pil, over).convert("RGB")
    img[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    return img


# ---------------------------------------------------------------- head-pose mannequin
# The live MediaPipe mesh as a black wireframe on grey, seen from a fixed three-quarter view so all
# three head axes stay visible, with long yaw / pitch / roll arrows (the mannequin figure's style).
# Axes follow DRIFTS: yaw turns about the head's up axis, pitch about the ear-to-ear axis,
# roll about the nose (forward) axis.

def _rot(axis, deg):
    a = np.radians(deg)
    c, s_ = np.cos(a), np.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s_], [0, s_, c]])
    return np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])


VIEW = _rot("x", 14) @ _rot("y", 28)            # three-quarter view from slightly above, as the mannequin
_TESS = None


def _tess():
    global _TESS
    if _TESS is None:
        from mediapipe.tasks.python.vision import FaceLandmarksConnections as C
        _TESS = np.array([(c.start, c.end) for c in C.FACE_LANDMARKS_TESSELATION])
    return _TESS


def mannequin_geometry(P, mat):
    """2-D drawing coordinates (y down, unit = face width) for the mesh points and the three axes."""
    X = np.column_stack([P[:, 0], -P[:, 1], -P[:, 2]]).astype(float)       # camera frame: x right, y up, z to camera
    fw = float(np.linalg.norm(X[234] - X[454])) or 1.0
    X = (X - X.mean(0)) / fw
    R = np.array(mat)[:3, :3] if mat is not None else np.eye(3)
    ex, ey, ez = R[:, 0], R[:, 1], R[:, 2]                                 # across, up, out of the face
    origin = -ez * 0.45                                                    # roughly the head's centre, behind the face
    axes = {"yaw": (origin - ey * 0.85, origin + ey * 1.25),
            "pitch": (origin, origin + ex * 1.05 * (-1 if (VIEW @ ex)[0] > 0 else 1)),   # to the viewer's left
            "roll": (origin, origin + ez * 1.35)}
    proj = lambda Y: (Y @ VIEW.T)[..., :2] * np.array([1, -1])           # noqa: E731
    return proj(X), {k: (proj(a), proj(b)) for k, (a, b) in axes.items()}, (X @ VIEW.T)[:, 2]


def mannequin_tile(P, mat, size):
    """Live inset (BGR, size x size): grey ground, black wireframe, long black arrows."""
    pts, axes, depth = mannequin_geometry(P, mat)
    tile = np.full((size, size, 3), 201, np.uint8)
    allp = np.vstack([pts] + [np.vstack(ab) for ab in axes.values()])
    c, span = (allp.min(0) + allp.max(0)) / 2, float((allp.max(0) - allp.min(0)).max()) * 1.45
    to = lambda q: (np.asarray(q) - c) / span * size + size / 2            # noqa: E731
    sp = to(pts)
    for a, b in _tess():
        g = 70 if min(depth[a], depth[b]) > np.median(depth) else 115       # nearer edges darker
        cv2.line(tile, _I(sp[a]), _I(sp[b]), (g, g, g), 1, cv2.LINE_AA, shift=4)
    labels = []
    for name, (a, b) in axes.items():
        a, b = to(a), to(b)
        d = b - a
        L = float(np.linalg.norm(d)) or 1.0
        u = d / L
        hl = size * 0.06
        base, nrm = b - u * hl, np.array([-u[1], u[0]]) * hl * 0.45
        cv2.line(tile, _I(a), _I(base), (10, 10, 10), 2, cv2.LINE_AA, shift=4)
        cv2.fillPoly(tile, [np.array([_I(b), _I(base + nrm), _I(base - nrm)], np.int32)], (10, 10, 10), cv2.LINE_AA, shift=4)
        labels.append((name, b + u * size * 0.09))
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(tile, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    f = _font(max(11, size // 13))
    for name, q in labels:
        tw = d.textlength(name, font=f)
        d.text((q[0] - tw / 2, q[1] - f.size * 0.6), name, font=f, fill=(15, 15, 15))
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def picture_mannequin(P, mat, v, out, px=1200):
    """4 x 4 in at 300 dpi: the mannequin figure with live head pose, readable at poster size."""
    from matplotlib.collections import LineCollection
    pts, axes, depth = mannequin_geometry(P, mat)
    fig = plt.figure(figsize=(4, 4), dpi=px / 4, facecolor="#C9C9C9")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#C9C9C9")
    T = _tess()
    near = np.minimum(depth[T[:, 0]], depth[T[:, 1]]) > np.median(depth)
    for sel, lw, col in ((~near, 0.35, "0.35"), (near, 0.45, "black")):
        ax.add_collection(LineCollection(pts[T[sel]], colors=col, linewidths=lw))
    for name, (a, b) in axes.items():
        ax.annotate("", xy=b, xytext=a, arrowprops=dict(arrowstyle="-|>", color="black", lw=3.2,
                                                        mutation_scale=34, shrinkA=0, shrinkB=0))
        u = (b - a) / (np.linalg.norm(b - a) or 1.0)
        ax.text(*(b + u * 0.26), name, fontsize=26, ha="center", va="center", color="#151515")
    allp = np.vstack([pts] + [np.vstack(ab) for ab in axes.values()])
    c, span = (allp.min(0) + allp.max(0)) / 2, float((allp.max(0) - allp.min(0)).max()) * 1.75
    ax.set_xlim(c[0] - span / 2, c[0] + span / 2), ax.set_ylim(c[1] + span / 2, c[1] - span / 2)
    ax.set_aspect("equal"), ax.axis("off")
    fmt = lambda x: "–" if x is None else f"{x:+.1f}°"                    # noqa: E731
    ax.text(0.5, 0.03, f"yaw {fmt(v.get('yaw'))}    pitch {fmt(v.get('pitch'))}    roll {fmt(v.get('roll'))}",
            transform=ax.transAxes, ha="center", fontsize=11, color="#222222")
    f = out / time.strftime("picture_%H%M%S_headpose.png")
    fig.savefig(f, facecolor="#C9C9C9"), plt.close(fig)
    return f


# ---------------------------------------------------------------- main loop

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
    nar_neutral, nar_calib, last, last_mat = None, [], None, None
    clicks, flash = [], {}
    win_name = "DRIFTS live graphs (dev)"
    if not args.no_window:
        cv2.namedWindow(win_name, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(win_name, lambda ev, x, y, *_: clicks.append((x, y)) if ev == cv2.EVENT_LBUTTONDOWN else None)
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
            last_mat = mat
            if v.get("nar") is not None and nar_neutral is None:
                nar_calib.append(v["nar"])
                if len(nar_calib) >= 30:
                    nar_neutral = float(np.median(nar_calib))
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
        cam = cv2.resize(sq, (int(h * 3 / 4), h), interpolation=cv2.INTER_CUBIC)
        if r is not None and not args.no_overlay:               # draw at display size: crisp lines and text
            sc = h / fh
            Pd = P.copy()
            Pd[:, 0] = (P[:, 0] - (fw - cw) // 2) * sc
            Pd[:, 1] = P[:, 1] * sc
            draw_overlay(cam, Pd, mat, v)
        cam_buttons = {}
        if r is not None and not args.no_overlay:               # head-pose mannequin inset, bottom right
            m = 210
            mx, my = cam.shape[1] - m - 12, cam.shape[0] - m - 12
            cam[my:my + m, mx:mx + m] = mannequin_tile(P, mat, m)
            cv2.rectangle(cam, (mx, my), (mx + m - 1, my + m - 1), (150, 150, 150), 1)
            cam_buttons["headpose"] = (mx + m - 74, my + m - 26, mx + m - 6, my + m - 6)
            from PIL import Image, ImageDraw
            pil = Image.fromarray(cv2.cvtColor(cam, cv2.COLOR_BGR2RGB))
            dd = ImageDraw.Draw(pil)
            bx0, by0, bx1, by1 = cam_buttons["headpose"]
            ok_ = flash.get("headpose", 0) > time.time()
            dd.rounded_rectangle([bx0, by0, bx1, by1], radius=5, fill=(46, 125, 50) if ok_ else (107, 15, 26),
                                 outline=(255, 255, 255))
            lab = "Saved ✓" if ok_ else "↓ PNG"
            dd.text(((bx0 + bx1 - dd.textlength(lab, font=_font(12))) / 2, by0 + 3), lab, font=_font(12),
                    fill=(255, 255, 255))
            dd.text((mx + 8, my + m - 20), "Head pose", font=_font(12), fill=(30, 30, 30))
            cam[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        if dash is None:
            dash = Dashboard(h)
        panel = dash.update(v, recording)
        raw_now, P_now = (last[0], last[1]) if (r is not None and last is not None) else (None, None)
        nn = nar_neutral or (float(np.median(nar_calib)) if nar_calib else None)
        buttons = draw_zoom_panel(panel, dash.zoom_box, raw_now, P_now, v, nn, flash)
        view = np.hstack([cam, panel])
        if clicks:                                               # Download PNG buttons
            cx, cy = clicks.pop()
            clicks.clear()
            hb = cam_buttons.get("headpose")
            if hb and last is not None and hb[0] <= cx <= hb[2] and hb[1] <= cy <= hb[3]:
                f = picture_mannequin(last[1], last_mat, last[2], out)
                print(f"saved {f}")
                flash["headpose"] = time.time() + 1.5
            for kind, (bx0, by0, bx1, by1) in buttons.items():
                if last is not None and bx0 <= cx - cam.shape[1] <= bx1 and by0 <= cy <= by1:
                    raw_l, P_l, v_l = last
                    f = (picture_eye(raw_l, P_l, v_l.get("ear"), out) if kind == "eye" else
                         picture_mouth(raw_l, P_l, v_l.get("mar"), out) if kind == "mouth" else
                         picture_nose(raw_l, P_l, v_l.get("nar"), nn, out))
                    print(f"saved {f}")
                    flash[kind] = time.time() + 1.5
        n += 1
        if not args.no_window:
            cv2.imshow(win_name, view)
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
                take_picture(*last, nar_neutral or (float(np.median(nar_calib)) if nar_calib else None), out, last_mat)
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
        take_picture(*last, nar_neutral or (float(np.median(nar_calib)) if nar_calib else None), out, last_mat)
    if not args.no_window:
        cv2.destroyAllWindows()
    print(f"Wrote {out}")
    return out, saved


if __name__ == "__main__":
    main()
