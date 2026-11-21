"""Dev view: zoomed close-ups of what the face signals measure, in the poster colours.

Draws, from one frame and its 478 face-mesh landmarks:

  eye    the driver's right eye, zoomed: the six EAR points P1..P6, the two
         eyelid openings |P2-P6|, |P3-P5| and the corner-to-corner width |P1-P4|
  mouth  the mouth, zoomed: the eight MAR points, the three inner-lip openings
         and the inner width
  gaze   both eyes side by side: the corners, the iris centre and where the
         iris sits between them (t), with the off-road band
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
    """Warm the photo towards the poster palette and darken it so the marks read."""
    import cv2
    import numpy as np
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    lo, hi = np.array(INK, np.float32), np.array(PEACH, np.float32)
    out = lo + (hi - lo) * (g[..., None] ** 1.1) * 0.85
    return out.clip(0, 255).astype(np.uint8)


def _I(p):
    return (int(round(p[0] * 4)), int(round(p[1] * 4)))


def _line(img, a, b, col, t):
    import cv2
    cv2.line(img, _I(a), _I(b), col, t, cv2.LINE_AA, shift=2)


def _dot(img, p, r):
    import cv2
    cv2.circle(img, _I(p), int(r * 4) + 4 * 3, CREAM, -1, cv2.LINE_AA, shift=2)
    cv2.circle(img, _I(p), int(r * 4), RED, -1, cv2.LINE_AA, shift=2)


def _label(img, text, p, dx, dy, scale, col=CREAM, bg=INK):
    import cv2
    font = cv2.FONT_HERSHEY_DUPLEX
    (tw, th), base = cv2.getTextSize(text, font, scale, max(1, int(scale * 1.6)))
    x, y = int(p[0] + dx - tw / 2), int(p[1] + dy + th / 2)
    pad = int(5 * scale)
    cv2.rectangle(img, (x - pad, y - th - pad), (x + tw + pad, y + base + pad // 2), bg, -1, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), font, scale, col, max(1, int(scale * 1.6)), cv2.LINE_AA)


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
    t = max(2, int(3 * s))
    _line(crop, p[0], p[3], SAND, t)                      # |P1-P4|
    _line(crop, p[1], p[5], SALMON, t + 1)                # |P2-P6|
    _line(crop, p[2], p[4], SALMON, t + 1)                # |P3-P5|
    for k, q in enumerate(p):
        _dot(crop, q, 7 * s)
        above = k in (1, 2)
        below = k in (4, 5)
        dy = -30 * s if above else 30 * s if below else 0
        dx = (-38 * s if k == 0 else 38 * s if k == 3 else 0)
        _label(crop, f"P{k + 1}", q, dx, dy, 0.75 * s)
    ear = f.get("ear_r") if f.get("ear_r") is not None else f.get("ear")
    _banner(crop, "EAR", "--" if ear is None else f"{ear:.2f}", SALMON)
    return crop


def mouth_view(img, P, f: dict, out=(620, 380)):
    idx = MOUTH_ORDER
    crop, to = _crop(img, P, idx, 0.45, out)
    crop = _tone(crop)
    s = out[0] / 620
    t = max(2, int(3 * s))
    _line(crop, to(CORNERS[0]), to(CORNERS[1]), SAND, t)  # inner width
    for a, b in UPPER_LOWER:
        _line(crop, to(a), to(b), PEACH, t + 1)           # three openings
    for k, i in enumerate(idx):
        q = to(i)
        _dot(crop, q, 7 * s)
        upper = k in (1, 2, 3)
        lower = k in (5, 6, 7)
        dy = -30 * s if upper else 30 * s if lower else 0
        dx = (-40 * s if k == 0 else 40 * s if k == 4 else 0)
        _label(crop, f"P{k + 1}", q, dx, dy, 0.75 * s)
    m = f.get("mar")
    _banner(crop, "MAR", "--" if m is None else f"{m:.2f}", PEACH)
    return crop


def gaze_view(img, P, f: dict, out=(620, 380)):
    """Both eyes: corners, iris centre and the iris position t between them."""
    import cv2
    import numpy as np
    s = out[0] / 620
    half = (out[0] // 2, out[1])
    panels = []
    limit = float(f.get("gaze_limit") or 0.16)
    off = bool(f.get("gaze_off"))
    for (a, b), iris in (((33, 133), fs.IRIS_RIGHT), ((362, 263), fs.IRIS_LEFT)):
        crop, to = _crop(img, P, (a, b, iris), 0.7, half)
        crop = _tone(crop)
        pa, pb, pi = to(a), to(b), to(iris)
        lo, hi = (pa, pb) if pa[0] < pb[0] else (pb, pa)
        y = (lo[1] + hi[1]) / 2 + 62 * s
        tt = max(2, int(3 * s))
        # the road band: the middle of the eye, +/- gaze_limit
        mid = ((lo[0] + hi[0]) / 2, y)
        span = hi[0] - lo[0]
        band = (mid[0] - span * limit, mid[0] + span * limit)
        cv2.rectangle(crop, (int(band[0]), int(y - 9 * s)), (int(band[1]), int(y + 9 * s)), SAND, -1, cv2.LINE_AA)
        _line(crop, (lo[0], y), (hi[0], y), CREAM, tt)
        _line(crop, (pi[0], pi[1]), (pi[0], y), RED if off else CREAM, tt)
        cv2.circle(crop, (int(pi[0]), int(y)), int(9 * s), RED if off else MAROON, -1, cv2.LINE_AA)
        for q, lab in ((lo, "P1"), (hi, "P4")):
            _dot(crop, q, 6 * s)
            _label(crop, lab, q, 0, -30 * s, 0.7 * s)
        cv2.circle(crop, (int(pi[0]), int(pi[1])), int(12 * s), CREAM, max(2, int(3 * s)), cv2.LINE_AA)
        _dot(crop, pi, 5 * s)
        panels.append(crop)
    view = np.concatenate(panels[::-1], axis=1)           # image-left eye first
    cv2.line(view, (half[0], 0), (half[0], out[1]), INK, max(2, int(4 * s)))
    g = f.get("gaze_x")
    _banner(view, "GAZE t", "--" if g is None else f"{g:+.2f}  {'OFF ROAD' if off else 'ON ROAD'}", SAND)
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
