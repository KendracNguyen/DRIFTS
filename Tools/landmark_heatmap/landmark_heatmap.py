"""DEV ONLY - in-depth MediaPipe face-landmark heat maps and close-ups (laptop).

Not part of the Pi app: deploy.ps1 copies only Raspberry/ and Voice/, and this
script refuses to run on a Raspberry Pi (override with --allow-pi).

From one still (or a short clip / webcam recording) it writes, to
Tools/landmark_heatmap/captures/<timestamp>/ (git-ignored: these are faces):

  depth_heatmap.png    smooth depth map of the face from the 478 landmarks' z
                       (near = hot), with depth contours
  signal_heatmap.png   where DRIFTS looks: each landmark the eight driver
                       signals use, weighted by facesignals.WEIGHTS
  motion_heatmap.png   (clip / webcam only) how much each landmark moved,
                       head motion removed - blinks light the lids, yawns the mouth
  regions/*.png        zoomed close-up of every facial region (eyes, irises,
                       brows, lips, nose, face oval) with every landmark numbered,
                       plus the EAR / MAR measurements DRIFTS takes
  regions_sheet.png    all region close-ups on one page
  points_sheet.png     a zoomed tile for EACH of the 478 landmarks, grouped by region
  points/NNN.png       (--tiles) the same tiles as separate files
  blendshapes.png      MediaPipe's 52 expression scores (eyeBlink, jawOpen, ...)
  landmarks.csv        index, region, x, y (px), z, plus motion if recorded
  summary.json         EAR (both eyes), MAR, head pose, face size, settings

    pip install -r Tools/landmark_heatmap/requirements-dev.txt
    python Tools/landmark_heatmap/landmark_heatmap.py --image face.jpg
    python Tools/landmark_heatmap/landmark_heatmap.py --camera 0 --seconds 5 --preview
    python Tools/landmark_heatmap/landmark_heatmap.py --video clip.mp4 --tiles

For sharp close-ups use a high-resolution, evenly lit, front-on photo: the
close-ups are crops of the original pixels, so a 640x480 webcam frame gives
soft tiles. --camera tries to open the camera at --width x --height (1920x1080).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "Raspberry"))

try:  # reuse the Pi app's landmark indices and weights so this stays in step
    from drifts import facesignals as fs
    from drifts.mouth import CORNERS, UPPER_LOWER
    EAR_R, EAR_L, WEIGHTS = fs.EAR_RIGHT, fs.EAR_LEFT, fs.WEIGHTS
    MOUTH_POINTS, NOSE_TIP, BRIDGE = fs.MOUTH_POINTS, fs.NOSE_TIP, fs.BRIDGE
except Exception:  # pragma: no cover - fallback if run outside the repo
    EAR_R, EAR_L = (33, 160, 158, 133, 153, 144), (362, 385, 387, 263, 373, 380)
    WEIGHTS = {"ear": 0.30, "blink": 0.15, "gaze": 0.10, "yaw": 0.05,
               "pitch": 0.15, "roll": 0.05, "mar": 0.05, "yawn": 0.15}
    MOUTH_POINTS, NOSE_TIP, BRIDGE = (78, 82, 13, 312, 308, 317, 14, 87), 1, 168
    UPPER_LOWER, CORNERS = ((13, 14), (82, 87), (312, 317)), (78, 308)

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
             "float16/1/face_landmarker.task")
MODEL_PATHS = (REPO / "Raspberry" / "models" / "face_landmarker.task", HERE / "models" / "face_landmarker.task")


def bgr(h: str) -> tuple:
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


MAROON, RED, INK, WHITE = bgr("6B0F1A"), bgr("B5162B"), bgr("1A0E0B"), (255, 255, 255)
GREY, LIGHT = bgr("8A8A8A"), bgr("EEF3F9")
# region name -> (colour, MediaPipe connection set). Order = drawing / sheet order.
REGION_STYLE = {
    "right_eye": bgr("D9826A"), "left_eye": bgr("D9826A"),
    "right_iris": bgr("2E86C1"), "left_iris": bgr("2E86C1"),
    "right_brow": bgr("7E2A2A"), "left_brow": bgr("7E2A2A"),
    "lips": bgr("B5162B"), "nose": bgr("E0A100"), "face_oval": bgr("5D6D7E"), "mesh": bgr("A0A0A0"),
}


# ---------------------------------------------------------------- setup

def refuse_on_pi(allow: bool) -> None:
    try:
        model = Path("/proc/device-tree/model").read_text(errors="ignore")
    except OSError:
        return
    if "Raspberry Pi" in model and not allow:
        sys.exit("landmark_heatmap is a laptop-only dev tool (it is slow and keeps face images). "
                 "Run it on your laptop, or pass --allow-pi if you really mean to.")


def model_path() -> Path:
    for p in MODEL_PATHS:
        if p.exists():
            return p
    p = MODEL_PATHS[1]
    p.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading the face landmarker model to {p} ...")
    tmp = p.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.rename(p)
    return p


def regions() -> dict:
    """landmark index -> region name, from MediaPipe's own connection sets."""
    from mediapipe.tasks.python.vision import FaceLandmarksConnections as C
    sets = {
        "right_iris": C.FACE_LANDMARKS_RIGHT_IRIS, "left_iris": C.FACE_LANDMARKS_LEFT_IRIS,
        "right_eye": C.FACE_LANDMARKS_RIGHT_EYE, "left_eye": C.FACE_LANDMARKS_LEFT_EYE,
        "right_brow": C.FACE_LANDMARKS_RIGHT_EYEBROW, "left_brow": C.FACE_LANDMARKS_LEFT_EYEBROW,
        "lips": C.FACE_LANDMARKS_LIPS, "nose": C.FACE_LANDMARKS_NOSE, "face_oval": C.FACE_LANDMARKS_FACE_OVAL,
    }
    reg = {468: "right_iris", 473: "left_iris"}   # iris centres are not in the connection sets
    for name, conns in sets.items():          # first set wins (irises before eyes)
        for c in conns:
            for i in (c.start, c.end):
                reg.setdefault(i, name)
    return {i: reg.get(i, "mesh") for i in range(478)}


class Landmarker:
    def __init__(self, video: bool):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        self.mp, self.video = mp, video
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path())),
            running_mode=vision.RunningMode.VIDEO if video else vision.RunningMode.IMAGE,
            num_faces=1, output_face_blendshapes=True, output_facial_transformation_matrixes=True,
            min_face_detection_confidence=0.4, min_face_presence_confidence=0.4)
        self.lm = vision.FaceLandmarker.create_from_options(opts)

    def close(self):
        self.lm.close()

    def __call__(self, frame, t_ms: int = 0):
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        r = self.lm.detect_for_video(img, t_ms) if self.video else self.lm.detect(img)
        if not r.face_landmarks:
            return None
        h, w = frame.shape[:2]
        pts = np.array([[p.x * w, p.y * h, p.z * w] for p in r.face_landmarks[0]], np.float32)
        shapes = {b.category_name: float(b.score) for b in r.face_blendshapes[0]} if r.face_blendshapes else {}
        mat = np.array(r.facial_transformation_matrixes[0]) if r.facial_transformation_matrixes else None
        return pts, shapes, mat


# ---------------------------------------------------------------- measurements

def ear(P, idx):
    p1, p2, p3, p4, p5, p6 = (P[i, :2] for i in idx)
    h = np.linalg.norm(p1 - p4)
    return float((np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)) / (2 * h)) if h > 1e-6 else None


def mar(P):
    w = np.linalg.norm(P[CORNERS[0], :2] - P[CORNERS[1], :2])
    if w < 1e-6:
        return None
    return float(np.mean([np.linalg.norm(P[a, :2] - P[b, :2]) for a, b in UPPER_LOWER]) / w)


def head_pose(mat):
    """yaw / pitch / roll in degrees, DRIFTS sign convention (facesignals.angles_from_matrix):
    yaw + = driver turned to their right, pitch + = looking down, roll + = toward right shoulder."""
    if mat is None:
        return None
    try:
        yaw, pitch, roll = fs.angles_from_matrix(mat)
    except NameError:  # outside the repo: same maths inline
        R = mat[:3, :3]
        yaw = -math.degrees(math.atan2(-R[2, 0], math.hypot(R[2, 1], R[2, 2])))
        pitch = math.degrees(math.atan2(R[2, 1], R[2, 2]))
        roll = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    return {"yaw": round(yaw, 1), "pitch": round(pitch, 1), "roll": round(roll, 1)}


def iod(P) -> float:
    return float(np.linalg.norm(P[EAR_R[3], :2] - P[EAR_L[0], :2]))   # inner eye corners 133 - 362


# ---------------------------------------------------------------- drawing helpers

def muted(img):
    """Photo as soft grey so heat colours read on top."""
    g = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return cv2.addWeighted(g, 0.75, np.full_like(g, 255), 0.25, 0)


def face_mask(shape, P):
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(m, cv2.convexHull(P[:, :2].astype(np.int32)), 255)
    return m


def overlay(base, heat01, mask, cmap=cv2.COLORMAP_TURBO, alpha=0.6):
    col = cv2.applyColorMap((heat01.clip(0, 1) * 255).astype(np.uint8), cmap)
    a = (mask.astype(np.float32) / 255.0 * alpha)[..., None]
    return (base * (1 - a) + col * a).astype(np.uint8)


def colorbar(img, label_lo, label_hi, title, cmap=cv2.COLORMAP_TURBO):
    """Legend in its own white side panel (never drawn over the face)."""
    h, w = img.shape[:2]
    pw = max(170, h // 4)
    panel = np.full((h, pw, 3), WHITE, np.uint8)
    s = max(0.5, h / 1100)
    bw, bh = int(28 * s), int(h * 0.5)
    x, y = int(24 * s), (h - bh) // 2
    panel[y:y + bh, x:x + bw] = cv2.applyColorMap(np.linspace(255, 0, bh).astype(np.uint8)[:, None].repeat(bw, 1), cmap)
    cv2.rectangle(panel, (x, y), (x + bw, y + bh), INK, 1)
    put(panel, title, (x, y - int(18 * s)), 0.75 * s, INK, bold=True)
    put(panel, label_hi, (x + bw + int(10 * s), y + int(16 * s)), 0.6 * s, INK)
    put(panel, label_lo, (x + bw + int(10 * s), y + bh), 0.6 * s, INK)
    return np.hstack([img, panel])


def tag(img, text, col, u):
    """Solid colour label in the top-left corner, white text (readable on any photo)."""
    sc, th = 0.95 * u, max(2, int(2 * u))
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, sc, th)
    pad = int(12 * u)
    cv2.rectangle(img, (0, 0), (tw + 2 * pad, tht + 2 * pad), col, -1)
    cv2.putText(img, text, (pad, tht + pad), cv2.FONT_HERSHEY_SIMPLEX, sc, WHITE, th, cv2.LINE_AA)


def put(img, text, org, scale, col, bold=False):
    t = 2 if bold else 1
    t = max(t, int(round(scale * (2.2 if bold else 1.4))))
    cv2.putText(img, text, (int(org[0]), int(org[1])), cv2.FONT_HERSHEY_SIMPLEX, scale, WHITE, t + 3, cv2.LINE_AA)
    cv2.putText(img, text, (int(org[0]), int(org[1])), cv2.FONT_HERSHEY_SIMPLEX, scale, col, t, cv2.LINE_AA)


def title_bar(img, text, sub=""):
    h, w = img.shape[:2]
    bh = int(min(max(48, h // 14), max(48, w // 22)))
    bar = np.full((bh, w, 3), MAROON, np.uint8)
    s = bh / 60
    cv2.putText(bar, text, (int(16 * s), int(bh * 0.62)), cv2.FONT_HERSHEY_SIMPLEX, 0.9 * s, WHITE, max(2, int(2 * s)), cv2.LINE_AA)
    if sub:
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.9 * s, max(2, int(2 * s)))
        cv2.putText(bar, sub, (int(16 * s) + tw + int(20 * s), int(bh * 0.62)), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * s,
                    bgr("F3DDE0"), 1, cv2.LINE_AA)
    return np.vstack([bar, img])


def splat(shape, xy, weights, sigma):
    """Sum of Gaussians at xy with the given weights, normalised to 0..1."""
    acc = np.zeros(shape[:2], np.float32)
    h, w = acc.shape
    for (x, y), wt in zip(xy, weights):
        if wt <= 0:
            continue
        xi, yi = int(round(x)), int(round(y))
        if 0 <= xi < w and 0 <= yi < h:
            acc[yi, xi] += wt
    k = int(sigma * 6) | 1
    acc = cv2.GaussianBlur(acc, (k, k), sigma)
    return acc / acc.max() if acc.max() > 0 else acc


# ---------------------------------------------------------------- outputs

def depth_heatmap(img, P):
    from scipy.interpolate import griddata
    h, w = img.shape[:2]
    x0, y0 = np.floor(P[:, :2].min(0)).astype(int).clip(0)
    x1, y1 = np.ceil(P[:, :2].max(0)).astype(int)
    x1, y1 = min(x1, w - 1), min(y1, h - 1)
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
    near = -P[:, 2]                                       # MediaPipe: smaller z = closer to the camera
    z = griddata(P[:, :2], near, (gx, gy), method="cubic")
    z = np.where(np.isnan(z), griddata(P[:, :2], near, (gx, gy), method="nearest"), z)
    lo, hi = np.nanpercentile(near, 1), np.nanpercentile(near, 99)
    full = np.zeros((h, w), np.float32)
    full[y0:y1 + 1, x0:x1 + 1] = (z - lo) / max(hi - lo, 1e-6)
    mask = face_mask(img.shape, P)
    out = overlay(muted(img), full, mask, alpha=0.7)
    # depth contours
    q = (full.clip(0, 1) * 10).astype(np.uint8)
    for lvl in range(1, 10):
        cs, _ = cv2.findContours(((q >= lvl) & (mask > 0)).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cs, -1, WHITE, 1, cv2.LINE_AA)
    out = colorbar(out, "far", "near", "depth")
    depth_span = float((hi - lo) / max(iod(P), 1e-6))
    return title_bar(out, "DEPTH HEAT MAP", f"478 landmarks, z interpolated  |  depth span = {depth_span:.2f} x eye spacing")


def signal_heatmap(img, P, reg):
    wts = np.zeros(478, np.float32)
    for i in EAR_R + EAR_L:
        wts[i] += (WEIGHTS["ear"] + WEIGHTS["blink"]) / 12
    for i in range(468, 478):
        wts[i] += WEIGHTS["gaze"] / 10
    for i in MOUTH_POINTS:
        wts[i] += (WEIGHTS["mar"] + WEIGHTS["yawn"]) / len(MOUTH_POINTS)
    head = WEIGHTS["yaw"] + WEIGHTS["pitch"] + WEIGHTS["roll"]
    wts[NOSE_TIP] += head * 0.5
    wts[BRIDGE] += head * 0.25
    oval = [i for i, r in reg.items() if r == "face_oval"]
    wts[oval] += head * 0.25 / len(oval)
    sigma = max(3.0, iod(P) * 0.12)
    heat = splat(img.shape, P[:, :2], wts, sigma) ** 0.6
    mask = (heat > 0.02).astype(np.uint8) * 255
    out = overlay(muted(img), heat, mask, cmap=cv2.COLORMAP_INFERNO, alpha=0.75)
    for i in np.nonzero(wts)[0]:
        cv2.circle(out, tuple(np.round(P[i, :2]).astype(int)), max(2, int(sigma / 4)), WHITE, -1, cv2.LINE_AA)
    out = colorbar(out, "unused", "high weight", "DRIFTS", cv2.COLORMAP_INFERNO)
    return title_bar(out, "WHERE DRIFTS LOOKS", "landmarks used by the 8 signals, weighted by facesignals.WEIGHTS")


def motion_heatmap(img, P_ref, motion):
    sigma = max(3.0, iod(P_ref) * 0.10)
    heat = splat(img.shape, P_ref[:, :2], motion / max(motion.max(), 1e-6), sigma) ** 0.7
    out = overlay(muted(img), heat, face_mask(img.shape, P_ref), cmap=cv2.COLORMAP_TURBO, alpha=0.65)
    out = colorbar(out, "still", "moved most", "motion")
    return title_bar(out, "MOTION HEAT MAP", "per-landmark movement over the recording, head motion removed")


def align(P, ref):
    """Similarity-align P onto ref (removes head translation, rotation, scale)."""
    a, b = P[:, :2] - P[:, :2].mean(0), ref[:, :2] - ref[:, :2].mean(0)
    u, s, vt = np.linalg.svd(a.T @ b)
    R = u @ vt
    sc = s.sum() / (a ** 2).sum()
    return a @ R * sc + ref[:, :2].mean(0)


def crop(img, center, half, out_px):
    cx, cy = center
    src = np.float32([[cx - half, cy - half], [cx + half, cy - half], [cx - half, cy + half]])
    dst = np.float32([[0, 0], [out_px, 0], [0, out_px]])
    M = cv2.getAffineTransform(src, dst)
    tile = cv2.warpAffine(img, M, (out_px, out_px), flags=cv2.INTER_LANCZOS4,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=LIGHT)
    return tile, (lambda p: (M[0, 0] * p[0] + M[0, 1] * p[1] + M[0, 2], M[1, 0] * p[0] + M[1, 1] * p[1] + M[1, 2]))


def region_closeups(img, P, reg, out_px=900):
    tiles = {}
    names = [n for n in REGION_STYLE if n != "mesh"]
    for name in names:
        idx = [i for i, r in reg.items() if r == name]
        sel = P[idx, :2]
        (x0, y0), (x1, y1) = sel.min(0), sel.max(0)
        half = max(x1 - x0, y1 - y0) * 0.5 * (1.35 if "iris" not in name else 2.4) + 4
        tile, to = crop(img, ((x0 + x1) / 2, (y0 + y1) / 2), half, out_px)
        col = REGION_STYLE[name]
        # every landmark in view, faint
        for i in range(478):
            x, y = to(P[i])
            if 0 <= x < out_px and 0 <= y < out_px and i not in idx:
                cv2.circle(tile, (int(x), int(y)), max(2, int(out_px / 300)), GREY, -1, cv2.LINE_AA)
        # DRIFTS measurements
        if name in ("right_eye", "left_eye"):
            e = EAR_R if name == "right_eye" else EAR_L
            q = [to(P[i]) for i in e]
            for a, b in ((1, 5), (2, 4), (0, 3)):
                cv2.line(tile, tuple(map(int, q[a])), tuple(map(int, q[b])), RED, 4, cv2.LINE_AA)
            v = ear(P, e)
            put(tile, f"EAR = {v:.3f}" if v else "EAR n/a", (14, out_px - 22), 1.3 * out_px / 900, RED, bold=True)
        if name == "lips":
            for a, b in UPPER_LOWER + (CORNERS,):
                pa, pb = to(P[a]), to(P[b])
                cv2.line(tile, tuple(map(int, pa)), tuple(map(int, pb)), RED, 4, cv2.LINE_AA)
            v = mar(P)
            put(tile, f"MAR = {v:.3f}" if v else "MAR n/a", (14, out_px - 22), 1.3 * out_px / 900, RED, bold=True)
        # region landmarks, numbered
        u = out_px / 900
        r = max(6, int(9 * u))
        for i in idx:
            x, y = to(P[i])
            cv2.circle(tile, (int(x), int(y)), r + 2, WHITE, -1, cv2.LINE_AA)
            cv2.circle(tile, (int(x), int(y)), r, col, -1, cv2.LINE_AA)
            put(tile, str(i), (x + r + 3, y - r - 2), 0.62 * u, INK, bold=True)
        tag(tile, name.replace("_", " ").upper() + f"  {len(idx)} pts", col, u)
        tiles[name] = tile
    return tiles


def point_tiles(img, P, reg, tile_px=120):
    span = max(iod(P) * 0.18, 10.0)
    order = sorted(range(478), key=lambda i: (list(REGION_STYLE).index(reg[i]), i))
    tiles = []
    for i in order:
        t, to = crop(img, P[i, :2], span, tile_px)
        c = tile_px // 2
        col = REGION_STYLE[reg[i]]
        cv2.line(t, (c - 10, c), (c - 4, c), WHITE, 1, cv2.LINE_AA)
        cv2.line(t, (c + 4, c), (c + 10, c), WHITE, 1, cv2.LINE_AA)
        cv2.line(t, (c, c - 10), (c, c - 4), WHITE, 1, cv2.LINE_AA)
        cv2.line(t, (c, c + 4), (c, c + 10), WHITE, 1, cv2.LINE_AA)
        cv2.circle(t, (c, c), 3, col, -1, cv2.LINE_AA)
        cv2.rectangle(t, (0, 0), (tile_px - 1, tile_px - 1), col, 3)
        cv2.rectangle(t, (0, 0), (46, 18), col, -1)
        cv2.putText(t, str(i), (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1, cv2.LINE_AA)
        tiles.append((i, t))
    return tiles


def sheet(tiles, cols, gap=6, bg=WHITE):
    th, tw = tiles[0].shape[:2]
    rows = math.ceil(len(tiles) / cols)
    out = np.full((rows * (th + gap) + gap, cols * (tw + gap) + gap, 3), bg, np.uint8)
    for k, t in enumerate(tiles):
        r, c = divmod(k, cols)
        out[gap + r * (th + gap):gap + r * (th + gap) + th, gap + c * (tw + gap):gap + c * (tw + gap) + tw] = t
    return out


def legend(width):
    bar = np.full((40, width, 3), WHITE, np.uint8)
    x = 12
    for name, col in REGION_STYLE.items():
        cv2.rectangle(bar, (x, 12), (x + 18, 28), col, -1)
        cv2.putText(bar, name.replace("_", " "), (x + 24, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.5, INK, 1, cv2.LINE_AA)
        x += 34 + cv2.getTextSize(name, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0][0]
    return bar


def blendshape_chart(shapes):
    items = sorted(((k, v) for k, v in shapes.items() if k != "_neutral"), key=lambda kv: -kv[1])
    rowh, w, lw = 26, 1000, 260
    img = np.full((rowh * len(items) + 20, w, 3), WHITE, np.uint8)
    key = ("eyeBlink", "jawOpen", "eyeSquint", "eyeLook", "mouthClose")
    for r, (k, v) in enumerate(items):
        y = 10 + r * rowh
        col = RED if k.startswith(key) else MAROON
        cv2.putText(img, k, (10, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.52, INK, 1, cv2.LINE_AA)
        cv2.rectangle(img, (lw, y + 5), (w - 80, y + 21), bgr("EDEDED"), -1)
        cv2.rectangle(img, (lw, y + 5), (lw + int((w - 80 - lw) * v), y + 21), col, -1)
        cv2.putText(img, f"{v:.2f}", (w - 70, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, INK, 1, cv2.LINE_AA)
    return title_bar(img, "BLENDSHAPES", "red = signals DRIFTS also measures (blink, jaw, gaze)")


# ---------------------------------------------------------------- capture

def frames_from(args):
    """Yield (frame, t_ms). One frame for --image, many for --video / --camera."""
    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            sys.exit(f"Could not read {args.image}")
        yield img, 0
        return
    cap = cv2.VideoCapture(args.video if args.video else args.camera)
    if not args.video:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        sys.exit("Could not open the video / camera")
    t0, n = time.time(), 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t_ms = int(n * 1000 / fps) if args.video else int((time.time() - t0) * 1000)
        if args.preview:
            show = frame.copy()
            put(show, f"recording {t_ms / 1000:.1f}/{args.seconds:.0f}s - blink, yawn, nod (q = stop)", (12, 30), 0.7, RED, True)
            cv2.imshow("landmark_heatmap (dev)", show)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        yield frame, t_ms
        n += 1
        if (not args.video and time.time() - t0 >= args.seconds) or (args.video and n >= args.max_frames):
            break
    cap.release()
    if args.preview:
        cv2.destroyAllWindows()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="one still photo")
    src.add_argument("--video", help="a video clip (adds the motion heat map)")
    src.add_argument("--camera", type=int, help="webcam index (adds the motion heat map)")
    ap.add_argument("--seconds", type=float, default=5, help="webcam recording length")
    ap.add_argument("--max-frames", type=int, default=300, help="video frames to read")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--preview", action="store_true", help="show the webcam while recording")
    ap.add_argument("--tiles", action="store_true", help="also save each landmark close-up as points/NNN.png")
    ap.add_argument("--out", help="output folder (default captures/<timestamp>)")
    ap.add_argument("--allow-pi", action="store_true")
    args = ap.parse_args(argv)
    refuse_on_pi(args.allow_pi)

    reg = regions()
    lmk = Landmarker(video=not args.image)
    best, track = None, []
    for frame, t in frames_from(args):
        r = lmk(frame, t)
        if r is None:
            continue
        pts, shapes, mat = r
        track.append(pts)
        size = iod(pts)
        if best is None or size >= best[0] * 0.98:          # keep the largest (closest) face as the still
            best = (size, frame.copy(), pts, shapes, mat)
    lmk.close()
    if best is None:
        sys.exit("No face found. Try a brighter, front-on photo.")
    _, img, P, shapes, mat = best
    raw_iod = iod(P)
    scale = max(1.0, 1100 / img.shape[0])       # draw the maps at >= 1100 px tall so labels fit
    if scale > 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        P = P * np.float32([scale, scale, scale])
        track = [t * np.float32([scale, scale, scale]) for t in track]

    out = Path(args.out) if args.out else HERE / "captures" / time.strftime("%Y%m%d-%H%M%S")
    (out / "regions").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "depth_heatmap.png"), depth_heatmap(img, P))
    cv2.imwrite(str(out / "signal_heatmap.png"), signal_heatmap(img, P, reg))

    motion = None
    if len(track) >= 10:
        ref = track[0]
        A = np.stack([align(p, ref) for p in track])
        motion = (A.std(0) ** 2).sum(1) ** 0.5 / max(iod(ref), 1e-6)      # in eye-spacings
        cv2.imwrite(str(out / "motion_heatmap.png"), motion_heatmap(img, P, motion))

    tiles = region_closeups(img, P, reg)
    for name, t in tiles.items():
        cv2.imwrite(str(out / "regions" / f"{name}.png"), t)
    rs = sheet([cv2.resize(t, (450, 450), interpolation=cv2.INTER_AREA) for t in tiles.values()], cols=3, gap=10)
    cv2.imwrite(str(out / "regions_sheet.png"), title_bar(rs, "REGION CLOSE-UPS", "every landmark numbered; red = EAR / MAR"))

    pt = point_tiles(img, P, reg)
    ps = sheet([t for _, t in pt], cols=24, gap=4)
    ps = np.vstack([legend(ps.shape[1]), ps])
    cv2.imwrite(str(out / "points_sheet.png"), title_bar(ps, "ALL 478 LANDMARKS", "one zoomed tile per landmark, grouped by region"))
    if args.tiles:
        (out / "points").mkdir(exist_ok=True)
        for i, t in pt:
            cv2.imwrite(str(out / "points" / f"{i:03d}.png"), t)
    if shapes:
        cv2.imwrite(str(out / "blendshapes.png"), blendshape_chart(shapes))

    with open(out / "landmarks.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["index", "region", "x_px", "y_px", "z"] + (["motion_iod"] if motion is not None else []))
        for i in range(478):
            x, y, z = P[i] / scale
            row = [i, reg[i], f"{x:.2f}", f"{y:.2f}", f"{z:.3f}"]
            wr.writerow(row + ([f"{motion[i]:.4f}"] if motion is not None else []))
    summary = {
        "source": args.image or args.video or f"camera {args.camera}",
        "frames_with_face": len(track), "image_size": [img.shape[1], img.shape[0]],
        "eye_spacing_px": round(raw_iod, 1), "render_scale": round(scale, 2),
        "ear_right": ear(P, EAR_R), "ear_left": ear(P, EAR_L), "mar": mar(P),
        "head_pose_deg": head_pose(mat),
        "top_blendshapes": dict(sorted(shapes.items(), key=lambda kv: -kv[1])[:8]),
        "most_moving_landmarks": ([int(i) for i in np.argsort(-motion)[:15]] if motion is not None else None),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"Wrote {out}")
    if raw_iod < 60:
        print(f"Note: the face is small in this image (eye spacing {raw_iod:.0f} px); "
              "close-ups will be soft. Use a closer or higher-resolution photo.")
    return out


if __name__ == "__main__":
    main()
