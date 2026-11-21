"""Dev view: zoomed close-ups of what the face signals measure, in the poster colours.

Draws, from one frame and its 478 face-mesh landmarks:

  eye    the driver's right eye, zoomed: the six EAR points P1..P6, the two
         eyelid openings |P2-P6|, |P3-P5| and the corner-to-corner width |P1-P4|
  mouth  the mouth, zoomed: the eight MAR points, the three inner-lip openings
         and the inner width
  gaze   the road zone as a box with a target: iris position t across,
         head pitch up/down; the target turns red off the road
  face   the whole frame with every point used, small

The /dev page (dev.html) shows them. They are drawn only while that page is
open, so they cost nothing otherwise. Pure OpenCV/numpy: tests/test_devview.py
draws them from synthetic landmarks.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from drifts import facesignals as fs
from drifts.mouth import CORNERS, UPPER_LOWER

# Poster / app-icon palette (BGR)
def _bgr(h: str) -> tuple:
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


INK = _bgr("1A0E0B")        # app-icon background
CREAM = _bgr("F3E7D3")      # app-icon lettering
MAROON = _bgr("7E2A2A")     # head pose
RUST = _bgr("B24A3A")       # driving
SALMON = _bgr("D9826A")     # eyes · EAR
PEACH = _bgr("EDB9A6")      # mouth · MAR
SAND = _bgr("E3D3C9")       # gaze
RED = _bgr("D0201A")        # the poster's landmark dots
LABEL = _bgr("E3120B")      # point labels and measurement arrows (as in the EAR paper figure)

MOUTH_ORDER = (CORNERS[0], UPPER_LOWER[1][0], UPPER_LOWER[0][0], UPPER_LOWER[2][0],
               CORNERS[1], UPPER_LOWER[2][1], UPPER_LOWER[0][1], UPPER_LOWER[1][1])   # 78 82 13 312 308 317 14 87


def _pts(px, mirrored: bool, w: int):
    import numpy as np
    return np.array([[w - x if mirrored else x, y] for x, y in px], np.float32)


def _crop(img, P, idx, pad: float, out: tuple):
    """Square-ish crop around landmarks idx, scaled to out (w, h). Returns (crop, map(x, y))."""
    import cv2
    import numpy as np
    sel = P[list(idx)]
    (x0, y0), (x1, y1) = sel.min(0), sel.max(0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ow, oh = out
    half_w = max(x1 - x0, 8.0) * (0.5 + pad)
    half_h = half_w * oh / ow
    if (y1 - y0) * (0.5 + pad) > half_h:
        half_h = (y1 - y0) * (0.5 + pad)
        half_w = half_h * ow / oh
    src = np.float32([[cx - half_w, cy - half_h], [cx + half_w, cy - half_h], [cx - half_w, cy + half_h]])
    dst = np.float32([[0, 0], [ow, 0], [0, oh]])
    M = cv2.getAffineTransform(src, dst)
    crop = cv2.warpAffine(img, M, (ow, oh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=INK)

    def to(i):
        x, y = P[i]
        return (float(M[0, 0] * x + M[0, 1] * y + M[0, 2]), float(M[1, 0] * x + M[1, 1] * y + M[1, 2]))
    return crop, to


def _tone(img):
    """Warm, light rendering of the photo (towards the poster peach) so the red marks read."""
    import cv2
    import numpy as np
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)[..., None] / 255.0
    warm = np.array(INK, np.float32) + (np.array(CREAM, np.float32) - np.array(INK, np.float32)) * (0.25 + 0.75 * g)
    out = 0.55 * img.astype(np.float32) * 1.15 + 0.45 * warm
    return out.clip(0, 255).astype(np.uint8)


def _I(p):
    return (int(round(p[0] * 4)), int(round(p[1] * 4)))


def _line(img, a, b, col, t):
    import cv2
    cv2.line(img, _I(a), _I(b), col, t, cv2.LINE_AA, shift=2)


def _dot(img, p, r):
    import cv2
    cv2.circle(img, _I(p), int(r * 4), LABEL, -1, cv2.LINE_AA, shift=2)


def _arrow(img, a, b, t, s):
    """Thin double-headed measurement arrow, inset from the two points."""
    import cv2
    L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    inset = min(10 * s, L * 0.2)
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    p, q = (a[0] + ux * inset, a[1] + uy * inset), (b[0] - ux * inset, b[1] - uy * inset)
    tip = min(0.45, 16 * s / max(L - 2 * inset, 1))
    cv2.arrowedLine(img, _I(p), _I(q), LABEL, t, cv2.LINE_AA, shift=2, tipLength=tip)
    cv2.arrowedLine(img, _I(q), _I(p), LABEL, t, cv2.LINE_AA, shift=2, tipLength=tip)


_FONT_CACHE: dict = {}


def _serif(px: int):
    from pathlib import Path
    from PIL import ImageFont
    if px not in _FONT_CACHE:
        _FONT_CACHE[px] = ImageFont.truetype(str(Path(__file__).with_name("static") / "fonts" / "LiberationSerif-Bold.ttf"), px)
    return _FONT_CACHE[px]


def _labels(img, items, s):
    """Red serif labels with subscript numbers ('P' + small '1'), no box, as in the EAR figure.
    items: (point, number, dx, dy). Falls back to OpenCV's serif face without Pillow."""
    import cv2
    import numpy as np
    try:
        from PIL import Image, ImageDraw
        big, small = _serif(int(40 * s)), _serif(int(26 * s))
    except Exception:
        for p, n, dx, dy in items:
            cv2.putText(img, f"P{n}", (int(p[0] + dx - 16 * s), int(p[1] + dy + 10 * s)), cv2.FONT_HERSHEY_TRIPLEX,
                        0.9 * s, LABEL, max(1, int(2 * s)), cv2.LINE_AA)
        return img
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    rgb = (LABEL[2], LABEL[1], LABEL[0])
    for p, n, dx, dy in items:
        wP = d.textlength("P", font=big)
        wn = d.textlength(str(n), font=small)
        x = p[0] + dx - (wP + wn) / 2
        y = p[1] + dy
        d.text((x, y), "P", font=big, fill=rgb, anchor="lm")
        d.text((x + wP, y + 10 * s), str(n), font=small, fill=rgb, anchor="lm")
    img[:] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    return img


def _banner(img, title, value, col):
    """Value chip in the corner: 'EAR 0.28'."""
    import cv2
    font = cv2.FONT_HERSHEY_DUPLEX
    s = img.shape[1] / 620
    text = f"{title}  {value}"
    (tw, th), _ = cv2.getTextSize(text, font, 0.9 * s, max(1, int(2 * s)))
    cv2.rectangle(img, (0, 0), (tw + int(36 * s), th + int(26 * s)), INK, -1)
    cv2.rectangle(img, (0, 0), (int(8 * s), th + int(26 * s)), col, -1)
    cv2.putText(img, text, (int(22 * s), th + int(13 * s)), font, 0.9 * s, CREAM, max(1, int(2 * s)), cv2.LINE_AA)


def eye_view(img, P, f: dict, out=(620, 380)):
    import cv2
    idx = fs.EAR_RIGHT                                    # driver's right eye, as on the poster
    crop, to = _crop(img, P, idx + (fs.IRIS_RIGHT,), 0.55, out)
    crop = _tone(crop)
    p = [to(i) for i in idx]
    s = out[0] / 620
    t = max(1, int(2 * s))
    _arrow(crop, p[0], p[3], t, s)                        # |P1-P4|
    _arrow(crop, p[1], p[5], t, s)                        # |P2-P6|
    _arrow(crop, p[2], p[4], t, s)                        # |P3-P5|
    items = []
    for k, q in enumerate(p):
        _dot(crop, q, 7 * s)
        dy = -34 * s if k in (1, 2) else 36 * s if k in (4, 5) else 0
        dx = -44 * s if k == 0 else 44 * s if k == 3 else 0
        items.append((q, k + 1, dx, dy))
    _labels(crop, items, s)
    ear = f.get("ear_r") if f.get("ear_r") is not None else f.get("ear")
    _banner(crop, "EAR", "--" if ear is None else f"{ear:.2f}", SALMON)
    return crop


def mouth_view(img, P, f: dict, out=(620, 380)):
    idx = MOUTH_ORDER
    crop, to = _crop(img, P, idx, 0.45, out)
    crop = _tone(crop)
    s = out[0] / 620
    t = max(1, int(2 * s))
    _arrow(crop, to(CORNERS[0]), to(CORNERS[1]), t, s)    # inner width
    for a_, b_ in UPPER_LOWER:
        _arrow(crop, to(a_), to(b_), t, s)                # three openings
    items = []
    for k, i in enumerate(idx):
        q = to(i)
        _dot(crop, q, 7 * s)
        dy = -34 * s if k in (1, 2, 3) else 36 * s if k in (5, 6, 7) else 0
        dx = -46 * s if k == 0 else 46 * s if k == 4 else 0
        items.append((q, k + 1, dx, dy))
    _labels(crop, items, s)
    m = f.get("mar")
    _banner(crop, "MAR", "--" if m is None else f"{m:.2f}", PEACH)
    return crop


def gaze_view(img, P, f: dict, out=(620, 380)):
    """The road zone as a box with a target: where the eyes point (iris t across, head pitch up/down)."""
    import cv2
    import numpy as np
    W, H = out
    s = W / 620
    view = np.zeros((H, W, 3), np.uint8)
    view[:] = INK
    limit = float(f.get("gaze_limit") or 0.16)
    off = bool(f.get("gaze_off"))
    cx, cy = W / 2, H / 2 + 14 * s
    bw, bh = W * 0.26, H * 0.27                           # half-size of the road box = +/- limit
    # crosshair axes
    for a_, b_ in (((cx, 18 * s), (cx, H - 12 * s)), ((14 * s, cy), (W - 14 * s, cy))):
        _line(view, a_, b_, MAROON, max(1, int(2 * s)))
    # the road box: tinted, rounded, dashed outline
    x0, y0, x1, y1 = int(cx - bw), int(cy - bh), int(cx + bw), int(cy + bh)
    tint = view.copy()
    cv2.rectangle(tint, (x0, y0), (x1, y1), RUST if off else MAROON, -1, cv2.LINE_AA)
    cv2.addWeighted(tint, 0.35, view, 0.65, 0, view)
    col = RED if off else SAND
    per = int(18 * s)
    for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        L = int(math.hypot(bx - ax, by - ay))
        for d in range(0, L, per):
            e = min(L, d + per * 0.6)
            cv2.line(view, (int(ax + (bx - ax) * d / L), int(ay + (by - ay) * d / L)),
                     (int(ax + (bx - ax) * e / L), int(ay + (by - ay) * e / L)), col, max(2, int(3 * s)), cv2.LINE_AA)
    cv2.putText(view, "ROAD", (int(cx - 34 * s), int(y0 - 12 * s)), cv2.FONT_HERSHEY_DUPLEX, 0.75 * s, CREAM,
                max(1, int(1.6 * s)), cv2.LINE_AA)
    # target: iris position across, head pitch (relative to neutral) up/down
    g = f.get("gaze_x")
    pitch = f.get("pitch_drop")
    tx = cx + (0.0 if g is None else max(-2.2, min(2.2, g / limit)) * bw)
    ty = cy + (0.0 if pitch is None else max(-2.2, min(2.2, pitch / 20.0)) * bh)
    r = 26 * s
    tx = min(max(tx, r * 1.6), W - r * 1.6)
    ty = min(max(ty, 60 * s + r * 1.5), H - r * 1.6)
    if f.get("found", True):
        tc = RED if off else CREAM
        cv2.circle(view, (int(tx), int(ty)), int(r), tc, max(3, int(4 * s)), cv2.LINE_AA)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            _line(view, (tx + dx * r * 0.55, ty + dy * r * 0.55), (tx + dx * r * 1.45, ty + dy * r * 1.45), tc,
                  max(2, int(3 * s)))
        cv2.circle(view, (int(tx), int(ty)), int(10 * s), RED if off else SALMON, -1, cv2.LINE_AA)
    _banner(view, "GAZE t", "--" if g is None else f"{(g if abs(g) >= 0.005 else 0.0):+.2f}  {'OFF ROAD' if off else 'ON ROAD'}", SAND)
    return view


def face_view(img, P, f: dict, max_w: int = 900):
    """The whole frame, toned, with every point the signals use."""
    import cv2
    h, w = img.shape[:2]
    k = min(1.0, max_w / w)
    small = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else img.copy()
    small = _tone(small)
    Q = P * k
    s = small.shape[1] / 900
    for idx, col in ((fs.EAR_RIGHT, SALMON), (fs.EAR_LEFT, SALMON), (MOUTH_ORDER, PEACH)):
        pts = [tuple(Q[i]) for i in idx]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            _line(small, a, b, col, max(1, int(2 * s)))
    for i in fs.EAR_RIGHT + fs.EAR_LEFT + MOUTH_ORDER + (fs.IRIS_LEFT, fs.IRIS_RIGHT, fs.NOSE_TIP):
        _dot(small, tuple(Q[i]), 3.5 * s)
    return small


def render(frame, px: Optional[Sequence], face: dict, mirrored: bool) -> Optional[dict]:
    """All dev views from one frame, or None when no face is found."""
    if px is None or len(px) <= fs.IRIS_LEFT:
        return None
    P = _pts(px, mirrored, frame.shape[1])
    return {"eye": eye_view(frame, P, face), "mouth": mouth_view(frame, P, face),
            "gaze": gaze_view(frame, P, face), "face": face_view(frame, P, face)}
