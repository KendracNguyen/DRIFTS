"""Face-mesh source for the first DRIFTS model: all eight driver signals from
MediaPipe's Face Landmarker (478 points incl. irises + head pose matrix).

Self-contained: MediaPipe + OpenCV only, no calibration profile needed. It
runs on a laptop webcam (test here first), on the Pi Zero 2 W (64-bit OS,
fewer frames per second) and on the Pi 5.

    python -m drifts.live --face mesh --camera 0 --esp sim       # laptop
    python -m drifts.live --face mesh --esp ble                  # Pi in the car

Camera-like interface for drifts.live, like face_mediapipe.Pi5FaceSource:
grab() returns the BGR frame and sets
  .face       dict of signals (the dashboard's cards and dev view read it)
  .keypoints  5 COCO-style points for the shared pose overlay
  .landmarks  (478, 2) pixel landmarks
render_dev() / render_zoom() draw the dev view's overlay and eye/mouth close-ups.
The signal maths is in facesignals.py (unit-tested).
"""

from __future__ import annotations

import logging
import math
import time
import urllib.request
from pathlib import Path
from typing import Optional

from drifts import facesignals as fs
from drifts.mouth import YawnDetector, mar
from drifts.night import NightDetector, enhance_ir, mean_saturation

log = logging.getLogger(__name__)

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
             "float16/1/face_landmarker.task")
DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "face_landmarker.task"

LAYERS = ("mesh", "ear", "mouth", "axis", "iris", "glasses")

# BGR colours, matching the dev view's legend
C_MESH = (170, 200, 170)
C_CORNER = (255, 220, 60)      # P1 / P4
C_LID = (60, 220, 255)         # P2 P3 / P5 P6
C_MOUTH = (255, 140, 220)
C_X, C_Y, C_Z = (80, 80, 255), (90, 220, 90), (0, 230, 255)
C_TEXT = (240, 240, 240)


def ensure_model(path: Path = DEFAULT_MODEL) -> Path:
    """Download the Face Landmarker model (3.7 MB, Apache 2.0) on first use."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading the face landmarker model to %s", path)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.rename(path)
    return path


def bridge_edge(gray, px) -> Optional[float]:
    """Horizontal-edge strength across the nose bridge, relative to the cheek
    below the eye (same lighting, no frame). A glasses bridge or rim adds a
    strong edge there; bare skin is smooth. Roughly 0.0-0.1 without glasses,
    0.2+ with them (tune in the dev view)."""
    import cv2
    import numpy as np
    h, w = gray.shape[:2]
    iod = math.hypot(px[133][0] - px[362][0], px[133][1] - px[362][1])
    if iod < 12:
        return None

    def roi(cx, cy, half_w, half_h):
        x0, x1 = int(max(0, cx - half_w)), int(min(w, cx + half_w))
        y0, y1 = int(max(0, cy - half_h)), int(min(h, cy + half_h))
        return gray[y0:y1, x0:x1] if x1 - x0 >= 4 and y1 - y0 >= 4 else None

    bx = (px[fs.BRIDGE][0] + px[6][0]) / 2
    by = (px[fs.BRIDGE][1] + px[6][1]) / 2
    bridge = roi(bx, by, 0.18 * iod, 0.22 * iod)
    cheek = roi(px[205][0], px[205][1], 0.18 * iod, 0.22 * iod)
    if bridge is None or cheek is None:
        return None

    def edge(img):
        g = cv2.GaussianBlur(img, (3, 3), 0).astype(np.float32)
        sy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        return float(np.percentile(np.abs(sy), 90))

    e_b, e_c = edge(bridge), edge(cheek)
    contrast = float(np.std(bridge.astype(np.float32))) + 8.0
    return max(0.0, (e_b - e_c) / (4.0 * contrast))


class FaceMeshSource:
    backend = "mesh"

    def __init__(self, camera=None, model: Optional[str] = None, ir: str = "auto", yaw_sign: int = 1,
                 glasses: str = "auto", landmarker=None):
        self.camera, self.model_path, self.ir, self.yaw_sign = camera, model, ir, yaw_sign
        self._lm = landmarker                      # injectable for tests
        self.eyes = fs.EyeTracker()
        self.nods = fs.NodTracker()
        self.glasses = fs.GlassesDetector(mode=glasses)
        self.yawns = YawnDetector()
        self.night = NightDetector()
        self.face: dict = {"found": False}
        self.keypoints = None
        self.landmarks = None
        self.matrix = None
        self.people = 0
        self.inference_ms: Optional[float] = None
        self.profile_loaded = False                # road pose: drifts.live auto-calibrates
        self.last_frame = None
        self._ts_ms = 0

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        if self._lm is None:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            model = ensure_model(Path(self.model_path) if self.model_path else DEFAULT_MODEL)
            opts = vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model)),
                running_mode=vision.RunningMode.VIDEO, num_faces=1,
                output_facial_transformation_matrixes=True)
            self._lm = vision.FaceLandmarker.create_from_options(opts)
            self._mp = mp
        if self.camera is not None:
            self.camera.start()

    def set_glasses_mode(self, mode: str) -> None:
        if mode in ("auto", "on", "off"):
            self.glasses.mode = mode

    # ------------------------------------------------------------------ frames
    def grab(self):
        frame = self.camera.grab() if self.camera is not None else None
        if frame is None:
            return None
        self.process(frame, time.monotonic())
        return frame

    def _detect(self, rgb):
        """(landmarks as (478, 2) pixels or None, 4x4 matrix or None)."""
        self._ts_ms = max(self._ts_ms + 1, int(time.monotonic() * 1000))
        res = self._lm.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb),
                                        self._ts_ms)
        if not res.face_landmarks:
            return None, None
        h, w = rgb.shape[:2]
        px = [(p.x * w, p.y * h) for p in res.face_landmarks[0]]
        m = res.facial_transformation_matrixes[0] if res.facial_transformation_matrixes else None
        return px, m

    def process(self, frame, now: float) -> dict:
        import cv2
        self.last_frame = frame
        night = self.night.update(now, mean_saturation(frame))
        use_ir = self.ir == "on" or (self.ir == "auto" and night)
        img = enhance_ir(frame) if use_ir else frame
        t0 = time.perf_counter()
        px, m = self._detect(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        self.inference_ms = (time.perf_counter() - t0) * 1000
        self.landmarks, self.matrix = px, m
        self.people = 1 if px else 0
        edge = bridge_edge(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), px) if px else None
        return self.compute(now, px, m, edge, night, use_ir)

    def compute(self, now: float, px, m, edge: Optional[float], night: bool = False,
                ir_enhanced: bool = False) -> dict:
        """All signals from one frame's landmarks (no image needed: tested directly)."""
        found = px is not None and len(px) > fs.IRIS_LEFT
        el = fs.ear_terms(px, fs.EAR_LEFT) if found else None
        er = fs.ear_terms(px, fs.EAR_RIGHT) if found else None
        ear = (el.ear + er.ear) / 2 if el and er else None
        openness = self.eyes.update(now, ear)
        yaw = pitch = roll = None
        if found and m is not None:
            yaw, pitch, roll = fs.angles_from_matrix(m)
            yaw *= self.yaw_sign
        self.nods.update(now, pitch)
        glasses = self.glasses.update(now, edge)
        mr = mar(px) if found else None
        self.yawns.update(now, mr)
        self.keypoints = self._coco(px) if found else None

        perclos = self.eyes.perclos(now)
        eye_score = None if perclos is None else 100.0 * (1.0 - perclos)
        head_score = self.nods.score(now)
        fused = fs.fuse(eye_score, head_score, glasses)
        gaze = fs.iris_offset(px) if found else None
        closed_for = self.eyes.closed_for(now)
        state = "no_face" if not found else (
            "drowsy" if closed_for >= 1.0 or (fused["score"] is not None and fused["score"] < 50) else
            "alert")
        self.face = {
            "found": found, "yaw": yaw, "pitch": pitch, "roll": roll,
            "ear": ear, "ear_l": el.ear if el else None, "ear_r": er.ear if er else None,
            "ear_terms": {"l": [el.v1, el.v2, el.h] if el else None, "r": [er.v1, er.v2, er.h] if er else None},
            "ear_open": self.eyes.ear_open, "openness": openness, "eye_state": self.eyes.state(),
            "eye_valid": ear is not None, "closed_for": closed_for,
            "blink_rate": self.eyes.blink_rate(now), "mean_blink_ms": self.eyes.mean_blink_ms(),
            "last_blink_ms": self.eyes.last_blink_ms, "blinks": len(self.eyes.blinks),
            "long_closures": self.eyes.long_closures,
            "perclos": perclos, "perclos_warming": perclos is None and found,
            "gaze_dev": abs(gaze) if gaze is not None else None, "gaze_x": gaze,
            "mar": mr, "yawning": self.yawns.yawning(), "yawns": self.yawns.recent(now),
            "nods": len(self.nods.nods), "nods_total": self.nods.total_nods,
            "pitch_drop": self.nods.drop, "droop_s": self.nods.droop_s(now),
            "glasses": glasses, "glasses_mode": self.glasses.mode, "glasses_level": self.glasses.level,
            "glasses_edge": edge,
            "eye_score": eye_score, "head_score": head_score, "face_score": fused["score"],
            "w_eye": fused["w_eye"], "w_head": fused["w_head"], "basis": fused["basis"],
            "state": state, "night": night, "ir_enhanced": ir_enhanced,
            "saturation": self.night.saturation, "inference_ms": self.inference_ms,
            "profile": "auto", "profile_loaded": True,
        }
        return self.face

    @staticmethod
    def _coco(px) -> list:
        def mid(a, b):
            return ((px[a][0] + px[b][0]) / 2.0, (px[a][1] + px[b][1]) / 2.0)
        pts = [px[fs.NOSE_TIP], mid(362, 263), mid(33, 133), px[454], px[234]]
        return [(float(x), float(y), 1.0) for x, y in pts] + [(0.0, 0.0, 0.0)] * 12

    def eye_score(self) -> Optional[int]:
        """Score sent to the ESP32 as EYE:<n>: the fused face score."""
        s = self.face.get("face_score")
        return None if s is None else max(0, min(100, round(s)))

    def stop(self) -> None:
        if self.camera is not None:
            self.camera.stop()
        if self._lm is not None and hasattr(self._lm, "close"):
            self._lm.close()

    # ------------------------------------------------------------------ dev view drawing
    def render_dev(self, frame, layers=LAYERS, mirror: bool = False):
        """Full-analysis overlay: mesh, EAR points P1-P6, mouth, nose axis, irises."""
        import cv2
        img = cv2.flip(frame, 1) if mirror else frame.copy()
        px, m = self.landmarks, self.matrix
        if not px:
            cv2.putText(img, "no face", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 80, 255), 2, cv2.LINE_AA)
            return img
        w = img.shape[1]
        P = (lambda i: (int(w - px[i][0]), int(px[i][1]))) if mirror else (lambda i: (int(px[i][0]), int(px[i][1])))
        iod = math.hypot(px[133][0] - px[362][0], px[133][1] - px[362][1])
        r = max(1, int(iod / 60))
        if "mesh" in layers:
            for i in range(min(468, len(px))):
                cv2.circle(img, P(i), r, C_MESH, -1, cv2.LINE_AA)
        if "ear" in layers:
            for idx in (fs.EAR_RIGHT, fs.EAR_LEFT):
                p = [P(i) for i in idx]
                cv2.line(img, p[0], p[3], C_CORNER, 1, cv2.LINE_AA)
                cv2.line(img, p[1], p[5], C_LID, 1, cv2.LINE_AA)
                cv2.line(img, p[2], p[4], C_LID, 1, cv2.LINE_AA)
                for k, q in enumerate(p):
                    cv2.circle(img, q, r + 2, C_CORNER if k in (0, 3) else C_LID, -1, cv2.LINE_AA)
        if "mouth" in layers:
            p = [P(i) for i in fs.MOUTH_POINTS]
            cv2.polylines(img, [__import__("numpy").array(p, "int32")], True, C_MOUTH, 1, cv2.LINE_AA)
            for a, b in ((82, 87), (13, 14), (312, 317)):
                cv2.line(img, P(a), P(b), C_MOUTH, 1, cv2.LINE_AA)
            cv2.line(img, P(78), P(308), C_MOUTH, 1, cv2.LINE_AA)
        if "iris" in layers and len(px) > fs.IRIS_LEFT:
            for c in (fs.IRIS_RIGHT, fs.IRIS_LEFT):
                rad = max(2, int(math.hypot(px[c][0] - px[c + 1][0], px[c][1] - px[c + 1][1])))
                cv2.circle(img, P(c), rad, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.circle(img, P(c), r + 1, (255, 255, 255), -1, cv2.LINE_AA)
        if "glasses" in layers:
            bx, by = P(fs.BRIDGE)
            col = (0, 200, 255) if self.face.get("glasses") else (120, 120, 120)
            cv2.rectangle(img, (int(bx - 0.18 * iod), int(by - 0.1 * iod)),
                          (int(bx + 0.18 * iod), int(by + 0.34 * iod)), col, 1, cv2.LINE_AA)
        if "axis" in layers and m is not None:
            nose = (px[fs.NOSE_TIP][0], px[fs.NOSE_TIP][1])
            ends = fs.nose_axes(m, nose, 0.9 * iod)
            o = P(fs.NOSE_TIP)
            for name, col in (("x", C_X), ("y", C_Y), ("z", C_Z)):
                ex, ey = ends[name]
                e = (int(w - ex), int(ey)) if mirror else (int(ex), int(ey))
                cv2.arrowedLine(img, o, e, col, 2, cv2.LINE_AA, tipLength=0.12)
        f = self.face
        def fmt(v, d=0):
            return "--" if v is None else f"{v:.{d}f}"
        hud = (f"yaw {fmt(f.get('yaw'))}  pitch {fmt(f.get('pitch'))}  roll {fmt(f.get('roll'))}   "
               f"EAR {fmt(f.get('ear'), 2)}  MAR {fmt(f.get('mar'), 2)}"
               + ("   GLASSES" if f.get("glasses") else ""))
        cv2.rectangle(img, (0, img.shape[0] - 26), (img.shape[1], img.shape[0]), (0, 0, 0), -1)
        cv2.putText(img, hud, (8, img.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, C_TEXT, 1, cv2.LINE_AA)
        return img

    def render_zoom(self, frame, cell=(320, 180)):
        """Close-ups: driver's right eye, left eye, mouth, with P1-P6 labels."""
        import cv2
        import numpy as np
        cw, ch = cell
        out = np.zeros((ch, cw * 3 + 8, 3), np.uint8)
        px = self.landmarks
        if not px:
            return out
        h, w = frame.shape[:2]
        groups = ((fs.EAR_RIGHT, "RIGHT EYE"), (fs.EAR_LEFT, "LEFT EYE"), (fs.MOUTH_POINTS, "MOUTH"))
        for k, (idx, title) in enumerate(groups):
            xs = [px[i][0] for i in idx]
            ys = [px[i][1] for i in idx]
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            half_w = max(10.0, (max(xs) - min(xs)) * 0.8)
            half_h = half_w * ch / cw
            x0, y0 = int(max(0, cx - half_w)), int(max(0, cy - half_h))
            x1, y1 = int(min(w, cx + half_w)), int(min(h, cy + half_h))
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            crop = cv2.resize(frame[y0:y1, x0:x1], (cw, ch), interpolation=cv2.INTER_CUBIC)
            sx, sy = cw / (x1 - x0), ch / (y1 - y0)
            q = [(int((px[i][0] - x0) * sx), int((px[i][1] - y0) * sy)) for i in idx]
            if idx is fs.MOUTH_POINTS:
                cv2.polylines(crop, [np.array(q, "int32")], True, C_MOUTH, 1, cv2.LINE_AA)
                for a, b in ((1, 7), (2, 6), (3, 5)):
                    cv2.line(crop, q[a], q[b], C_MOUTH, 1, cv2.LINE_AA)
                cv2.line(crop, q[0], q[4], C_MOUTH, 1, cv2.LINE_AA)
            else:
                cv2.line(crop, q[0], q[3], C_CORNER, 1, cv2.LINE_AA)
                cv2.line(crop, q[1], q[5], C_LID, 2, cv2.LINE_AA)
                cv2.line(crop, q[2], q[4], C_LID, 2, cv2.LINE_AA)
                for j, pt in enumerate(q):
                    col = C_CORNER if j in (0, 3) else C_LID
                    cv2.circle(crop, pt, 5, col, -1, cv2.LINE_AA)
                    lbl = f"P{j + 1}"
                    off = (-30, -8) if j in (1, 2) else (-30, 24) if j in (4, 5) else ((-32, 6) if j == 0 else (9, 6))
                    cv2.putText(crop, lbl, (pt[0] + off[0], pt[1] + off[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                (0, 0, 0), 4, cv2.LINE_AA)
                    cv2.putText(crop, lbl, (pt[0] + off[0], pt[1] + off[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                col, 1, cv2.LINE_AA)
            cv2.putText(crop, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(crop, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, C_TEXT, 1, cv2.LINE_AA)
            out[:, k * (cw + 4):k * (cw + 4) + cw] = crop
        return out
