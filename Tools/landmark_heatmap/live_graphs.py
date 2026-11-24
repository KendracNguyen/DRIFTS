"""DEV ONLY - live line graphs of each facial indicator (laptop demo).

Not part of the Pi app (lives outside Raspberry/, refuses to run on a Pi).
MediaPipe's Face Landmarker gives the 478 landmarks; the indicators use the
same points and maths as the Pi (facesignals.py / mouth.py):

  EAR        eye aspect ratio, mean of both eyes     Closed -> Open
  NAR        nose aspect ratio |168-1| / |98-327|    Down -> Up (NAR rises as the head drops,
                                                     so that axis is drawn inverted)
  MAR        mouth aspect ratio                      Closed -> Open
  Head pose  yaw / pitch / roll in degrees (DRIFTS sign convention)
  Gaze       road box (iris offset +/-0.16 across, head pitch +/-20 deg) with a target

Each indicator records on its own: press its key to start, press again to
stop and save that graph (PNG) and its data (CSV) to captures/<session>/.

    keys   1 EAR   2 NAR   3 MAR   4 head pose   5 gaze   a all   c re-centre   s snapshot   q quit

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
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.facecolor": "white", "figure.facecolor": "white"})
from matplotlib.patches import Circle, Polygon, Rectangle         # noqa: E402

import landmark_heatmap as lh                                     # noqa: E402  (same folder)

WINDOW = 100                     # frames shown live, as in the reference screenshot
GAZE_LIMIT, PITCH_LIMIT = 0.16, 20.0
GAZE_X_SPAN, PITCH_SPAN = 0.5, 45.0
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
    ax.tick_params(labelsize=8)
    ax.set_facecolor("white")


def end_labels(ax, low, high, inverted=False):
    """'Closed' at the bottom and 'Open' at the top of the value axis."""
    top, bottom = (low, high) if inverted else (high, low)
    kw = dict(transform=ax.transAxes, fontsize=7, color="0.35", ha="left")
    ax.text(1.01, 1.0, top, va="top", **kw)
    ax.text(1.01, 0.0, bottom, va="bottom", **kw)


def draw_gaze_box(ax, gx, pitch, trail=None, title="Gaze", off=False):
    """White graph-paper grid, the road range as a box, a red sight target where the eyes point."""
    ax.clear()
    ax.set_xlim(-1, 1), ax.set_ylim(-1, 1), ax.set_aspect("equal")
    ticks = np.linspace(-1, 1, 11)
    ax.set_xticks(ticks), ax.set_yticks(ticks)
    ax.set_xticklabels([]), ax.set_yticklabels([])
    ax.tick_params(length=0)
    ax.grid(True, color="black", linewidth=0.5, alpha=0.55)
    ax.set_facecolor("white")
    rx, ry = GAZE_LIMIT / GAZE_X_SPAN, PITCH_LIMIT / PITCH_SPAN
    ax.add_patch(Rectangle((-rx, -ry), 2 * rx, 2 * ry, fill=False, linewidth=2.0, linestyle="--",
                           edgecolor="black", zorder=3))
    ax.text(-rx + 0.02, -ry + 0.02, "road", ha="left", va="bottom", fontsize=8, zorder=3)
    if trail is not None and len(trail) > 1:
        ax.plot(trail[:, 0], trail[:, 1], color="0.45", linewidth=1.0, alpha=0.7, zorder=2)
    if gx is not None and pitch is not None:
        x = float(np.clip(gx / GAZE_X_SPAN, -0.95, 0.95))
        y = float(np.clip(-pitch / PITCH_SPAN, -0.95, 0.95))      # pitch + = looking down
        s = 0.30
        ax.add_patch(Polygon([(x, y + s), (x + s, y), (x, y - s), (x - s, y)], closed=True,
                             facecolor=TARGET_RED, edgecolor="black", linewidth=0.8, zorder=4))
        for r in (0.22, 0.15, 0.08):
            ax.add_patch(Circle((x, y), r, fill=False, edgecolor="black", linewidth=1.0, zorder=5))
        ax.add_patch(Circle((x, y), 0.045, facecolor=TARGET_WHITE, edgecolor="black", linewidth=0.8, zorder=6))
        ax.plot([x - 0.03, x + 0.03], [y, y], color="black", linewidth=0.8, zorder=7)
        ax.plot([x, x], [y - 0.03, y + 0.03], color="black", linewidth=0.8, zorder=7)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("Left  <-  eyes  ->  Right", fontsize=8)
    ax.set_ylabel("Down  <-  head (vs. neutral)  ->  Up", fontsize=8)
    if off:
        ax.text(0, -0.93, "OFF ROAD", ha="center", va="bottom", fontsize=9, color="red", fontweight="bold", zorder=8)


def gaze_xy(gx, pitch):
    return (np.clip(gx / GAZE_X_SPAN, -0.95, 0.95), np.clip(-pitch / PITCH_SPAN, -0.95, 0.95))


def off_road(gx, pitch):
    return (gx is not None and abs(gx) > GAZE_LIMIT) or (pitch is not None and abs(pitch) > PITCH_LIMIT)


# ---------------------------------------------------------------- live dashboard

class Dashboard:
    """Line graphs on the left (EAR, NAR, MAR, head pose), the gaze box in its own column on the right."""

    def __init__(self, height_px=720, width_px=1100):
        dpi = 100
        self.fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, facecolor="white")
        gs = self.fig.add_gridspec(4, 4, height_ratios=[1, 1, 1, 1.25], width_ratios=[1, 1, 1, 1.55],
                                   hspace=0.85, wspace=0.55, left=0.07, right=0.98, top=0.94, bottom=0.08)
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
        gx, p = v.get("gaze_x"), v.get("pitch_rel")
        if gx is not None and p is not None:
            self.trail.append(gaze_xy(gx, p))
        draw_gaze_box(self.ax["gaze"], gx, p, np.array(self.trail) if self.trail else None,
                      title="Gaze" + ("  ● REC" if recording.get("gaze") else ""), off=off_road(gx, p))
        if recording.get("gaze"):
            self.ax["gaze"].title.set_color("red")
        self.fig.canvas.draw()
        img = np.asarray(self.fig.canvas.buffer_rgba())[..., :3]
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------- recording

FIELDS = {"ear": ["ear"], "nar": ["nar"], "mar": ["mar"], "pose": ["yaw", "pitch", "roll"],
          "gaze": ["gaze_x", "pitch_rel", "off_road"]}


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
        fig, ax = plt.subplots(figsize=(4.2, 4.6), dpi=200, facecolor="white")
        gx, p = col("gaze_x"), col("pitch_rel")
        ok = ~(np.isnan(gx) | np.isnan(p))
        trail = np.column_stack(gaze_xy(gx[ok], p[ok])) if ok.any() else None
        last = (gx[ok][-1], p[ok][-1]) if ok.any() else (None, None)
        share = np.mean([off_road(a, b) for a, b in zip(gx[ok], p[ok])]) if ok.any() else 0
        draw_gaze_box(ax, *last, trail=trail, title=f"Gaze  ({share:.0%} of frames off road)")
    fig.tight_layout()
    png = folder / f"{name}_{stamp}.png"
    fig.savefig(png, facecolor="white")
    plt.close(fig)
    return png


# ---------------------------------------------------------------- main loop

def draw_mesh(frame, P):
    """MediaPipe's own face mesh (tessellation + contours), drawn lightly."""
    from mediapipe.tasks.python.vision import FaceLandmarksConnections as C
    for conns, col, t in ((C.FACE_LANDMARKS_TESSELATION, (192, 192, 192), 1),
                          (C.FACE_LANDMARKS_CONTOURS, (240, 240, 240), 1),
                          (C.FACE_LANDMARKS_RIGHT_IRIS, lh.RED, 1), (C.FACE_LANDMARKS_LEFT_IRIS, lh.RED, 1)):
        for c in conns:
            a, b = P[c.start, :2], P[c.end, :2]
            cv2.line(frame, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), col, t, cv2.LINE_AA)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--camera", type=int)
    src.add_argument("--video")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--record", default="", help="start recording at launch: ear,nar,mar,pose,gaze or all")
    ap.add_argument("--no-window", action="store_true", help="no display (with --video); saves at the end")
    ap.add_argument("--no-mesh", action="store_true", help="hide the MediaPipe mesh on the camera view")
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
    print("keys: 1 EAR  2 NAR  3 MAR  4 head pose  5 gaze  a all  c re-centre  s snapshot  q quit")

    def toggle(name):
        if recording[name]:
            png = save_recording(name, rows[name], out)
            if png:
                saved.append(png)
                print(f"saved {png}")
            rows[name] = []
        recording[name] = not recording[name]

    view = None
    neutral, calib = None, []          # neutral head pitch: median of the first ~1 s with a face ('c' re-centres)
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
            if not args.no_mesh:
                draw_mesh(frame, P)
        if v.get("pitch") is not None and neutral is None:
            calib.append(v["pitch"])
            if len(calib) >= 30:
                neutral = float(np.median(calib))
                print(f"neutral pitch {neutral:+.1f} deg")
        ref = neutral if neutral is not None else (float(np.median(calib)) if calib else 0.0)
        v["pitch_rel"] = None if v.get("pitch") is None else v["pitch"] - ref
        v["off_road"] = off_road(v.get("gaze_x"), v.get("pitch_rel")) if v else None
        for name in names:
            if recording[name]:
                rows[name].append(dict(v, t=now))
        h = 720
        fh, fw = frame.shape[:2]
        side = min(fh, fw)                                      # square centre crop keeps the window < 1920 px
        sq = frame[(fh - side) // 2:(fh + side) // 2, (fw - side) // 2:(fw + side) // 2]
        cam = cv2.resize(sq, (h, h))
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
            elif k == ord("c"):
                neutral, calib = None, []
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
    if not args.no_window:
        cv2.destroyAllWindows()
    print(f"Wrote {out}")
    return out, saved


if __name__ == "__main__":
    main()
