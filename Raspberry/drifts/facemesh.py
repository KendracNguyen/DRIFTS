"""Face-mesh source for the first DRIFTS model: all eight driver signals from
MediaPipe's Face Landmarker (478 points incl. irises + head pose matrix).

Self-contained: MediaPipe + OpenCV only, no calibration profile needed. It
runs on a laptop webcam (test here first), on the Pi Zero 2 W (64-bit OS,
fewer frames per second) and on the Pi 5.

    python -m drifts.live --face mesh --camera 0 --esp sim       # laptop
    python -m drifts.live --face mesh --esp uart                 # Pi in the car (wired)

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

LAYERS = ("mesh", "eyes", "head", "mouth", "nose", "glasses")
DEFAULT_LAYERS = ("mesh", "eyes", "head", "mouth", "nose")

# BGR colours, one per face cluster. The dev view uses the same colours
# (dashboard.html --c-eyes / --c-head / --c-mouth / --c-nose).
C_EYES = (247, 195, 79)        # #4FC3F7 sky blue
C_EYES_HI = (255, 236, 190)    # P1 / P4 corners, lighter blue
C_HEAD = (77, 183, 255)        # #FFB74D amber
C_MOUTH = (255, 136, 179)      # #B388FF violet
C_NOSE = (122, 92, 255)        # #FF5C7A rose
C_WIRE = (92, 72, 78)          # dim wireframe
C_TEXT = (240, 240, 240)
C_GLASSES = (0, 200, 255)

NOSE_BRIDGE = (168, 6, 197, 195, 5, 4, 1)


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
        self.yaw_n, self.roll_n = fs.Neutral(), fs.Neutral()
        self.gaze_off = fs.OffTimer()
        self.gaze_limit = 0.16                     # iris offset counted as off road
        # Iris "looking at the road" position. The camera is usually off to one
        # side, so straight-ahead eyes don't read 0: calibrate it like the head
        # pose (auto at start, or the Calibrate button), from open-eye frames only.
        self.gaze0 = 0.0
        self._gaze_samples: list = []
        self.gaze_calibrated = False
        self.calib_frames = 30
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
        self.conn = {"mesh": [], "eyes": [], "lips": [], "oval": []}   # filled from MediaPipe in start()

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
            c = vision.FaceLandmarksConnections
            pairs = lambda conns: [(k.start, k.end) for k in conns]
            self.conn = {"mesh": pairs(c.FACE_LANDMARKS_TESSELATION),
                         "eyes": pairs(c.FACE_LANDMARKS_LEFT_EYE) + pairs(c.FACE_LANDMARKS_RIGHT_EYE),
                         "lips": pairs(c.FACE_LANDMARKS_LIPS),
                         "oval": pairs(c.FACE_LANDMARKS_FACE_OVAL)}
        if self.camera is not None:
            self.camera.start()

    def calibrate(self, frames: int = 30) -> None:
        """Restart the iris calibration: the next `frames` open-eye frames
        become the 'looking at the road' iris position."""
        self.calib_frames = frames
        self._gaze_samples, self.gaze_calibrated, self.gaze0 = [], False, 0.0

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
        gaze_raw = fs.iris_offset(px) if found else None
        eyes_open = found and self.eyes.state() == "open"
        if not self.gaze_calibrated and eyes_open and gaze_raw is not None:
            self._gaze_samples.append(gaze_raw)
            if len(self._gaze_samples) >= self.calib_frames:
                vals = sorted(self._gaze_samples)
                self.gaze0, self.gaze_calibrated = vals[len(vals) // 2], True
        gaze = None if gaze_raw is None else gaze_raw - self.gaze0
        eyes_closed = found and self.eyes.state() == "closed"
        # Eyes closed = not watching the road, whatever the head does.
        gaze_off_now = None if not found else (eyes_closed or (gaze is not None and abs(gaze) > self.gaze_limit))
        gaze_off_s = self.gaze_off.update(now, gaze_off_now)
        gaze_reason = (None if not gaze_off_now else "eyes closed" if eyes_closed else "eyes looking away")
        yaw_rel, roll_rel = self.yaw_n.update(now, yaw), self.roll_n.update(now, roll)
        closed_for = self.eyes.closed_for(now)
        raw = {"found": found, "perclos": perclos, "mean_blink_ms": self.eyes.mean_blink_ms(),
               "closed_for": closed_for, "gaze_off_s": gaze_off_s, "yaw_rel": yaw_rel, "roll_rel": roll_rel,
               "nods": len(self.nods.nods), "droop_s": self.nods.droop_s(now),
               "neutral_ready": self.nods.neutral is not None, "mar": mr,
               "yawns": self.yawns.recent(now), "yawning": self.yawns.yawning()}
        comp = fs.composite(raw, glasses)
        drowsy = comp["score"]
        face_score = None if drowsy is None else 100.0 - drowsy
        state = "no_face" if not found else (
            "drowsy" if closed_for >= 1.0 or (drowsy is not None and drowsy > 50) else "alert")
        nose = (px[fs.NOSE_TIP][0], px[fs.NOSE_TIP][1]) if found else None
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
            "gaze_dev": abs(gaze) if gaze is not None else None, "gaze_x": gaze, "gaze_raw": gaze_raw,
            "gaze_calibrated": self.gaze_calibrated, "gaze_reason": gaze_reason, "eyes_closed": eyes_closed,
            "gaze_calib_progress": 1.0 if self.gaze_calibrated else len(self._gaze_samples) / self.calib_frames,
            "mar": mr, "yawning": self.yawns.yawning(), "yawns": self.yawns.recent(now),
            "nods": len(self.nods.nods), "nods_total": self.nods.total_nods,
            "pitch_drop": self.nods.drop, "droop_s": self.nods.droop_s(now),
            "glasses": glasses, "glasses_mode": self.glasses.mode, "glasses_level": self.glasses.level,
            "glasses_edge": edge,
            "gaze_off_s": gaze_off_s, "gaze_off": bool(gaze_off_now), "gaze_limit": self.gaze_limit,
            "yaw_rel": yaw_rel, "roll_rel": roll_rel,
            "nose": nose, "nose_neutral": {"yaw": self.yaw_n.value, "pitch": self.nods.neutral,
                                           "roll": self.roll_n.value},
            "eye_score": eye_score, "head_score": head_score,
            "drowsiness": drowsy, "parts": comp["parts"], "face_score": face_score,
            "basis": "glasses: weight moved from the eyes to the nose axis" if glasses else "normal weights",
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
        """Score sent to the ESP32 as EYE:<n>: 100 - the composite drowsiness."""
        s = self.face.get("face_score")
        return None if s is None else max(0, min(100, round(s)))

    def stop(self) -> None:
        if self.camera is not None:
            self.camera.stop()
        if self._lm is not None and hasattr(self._lm, "close"):
            self._lm.close()

    # ------------------------------------------------------------------ dev view drawing
    def render_dev(self, frame, layers=DEFAULT_LAYERS, mirror: bool = False):
        """Analysis overlay: dim face-mesh wireframe, then each cluster's
        landmarks in its own colour (eyes, head pose, mouth, nose anchor)."""
        import cv2
        import numpy as np
        img = cv2.flip(frame, 1) if mirror else frame.copy()
        px, m = self.landmarks, self.matrix
        if not px:
            cv2.putText(img, "no face", (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, C_NOSE, 2, cv2.LINE_AA)
            return img
        w = img.shape[1]
        def P(i):
            x, y = px[i]
            return (int(w - x), int(y)) if mirror else (int(x), int(y))
        iod = math.hypot(px[133][0] - px[362][0], px[133][1] - px[362][1])
        r = max(1, int(iod / 70))
        def lines(pairs, col, th=1):
            for a, b in pairs:
                cv2.line(img, P(a), P(b), col, th, cv2.LINE_AA)
        if "mesh" in layers:
            if self.conn["mesh"]:
                overlay = img.copy()
                for a, b in self.conn["mesh"]:
                    cv2.line(overlay, P(a), P(b), C_WIRE, 1, cv2.LINE_AA)
                cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
            else:
                for i in range(min(468, len(px))):
                    cv2.circle(img, P(i), r, C_WIRE, -1, cv2.LINE_AA)
        if "head" in layers:
            lines(self.conn["oval"], C_HEAD, 1)
            if not self.conn["oval"]:
                for i in (10, 152, 234, 454):
                    cv2.circle(img, P(i), r + 1, C_HEAD, -1, cv2.LINE_AA)
            if m is not None:
                ends = fs.nose_axes(m, px[fs.NOSE_TIP], 0.9 * iod)
                o = P(fs.NOSE_TIP)
                for name, th in (("x", 1), ("y", 1), ("z", 3)):
                    ex, ey = ends[name]
                    e = (int(w - ex), int(ey)) if mirror else (int(ex), int(ey))
                    cv2.arrowedLine(img, o, e, C_HEAD, th, cv2.LINE_AA, tipLength=0.12)
        if "mouth" in layers:
            lines(self.conn["lips"], C_MOUTH, 1)
            for a, b in ((82, 87), (13, 14), (312, 317), (78, 308)):
                cv2.line(img, P(a), P(b), C_MOUTH, 1, cv2.LINE_AA)
            for i in fs.MOUTH_POINTS:
                cv2.circle(img, P(i), r + 1, C_MOUTH, -1, cv2.LINE_AA)
        if "eyes" in layers:
            lines(self.conn["eyes"], C_EYES, 1)
            for idx in (fs.EAR_RIGHT, fs.EAR_LEFT):
                p = [P(i) for i in idx]
                cv2.line(img, p[1], p[5], C_EYES, 1, cv2.LINE_AA)
                cv2.line(img, p[2], p[4], C_EYES, 1, cv2.LINE_AA)
                for k, q in enumerate(p):
                    cv2.circle(img, q, r + 2, C_EYES_HI if k in (0, 3) else C_EYES, -1, cv2.LINE_AA)
            if len(px) > fs.IRIS_LEFT:
                for c in (fs.IRIS_RIGHT, fs.IRIS_LEFT):
                    rad = max(2, int(math.hypot(px[c][0] - px[c + 1][0], px[c][1] - px[c + 1][1])))
                    cv2.circle(img, P(c), rad, C_EYES, 1, cv2.LINE_AA)
        if "nose" in layers:
            pts = np.array([P(i) for i in NOSE_BRIDGE], np.int32)
            cv2.polylines(img, [pts], False, C_NOSE, 1, cv2.LINE_AA)
            o = P(fs.NOSE_TIP)
            cv2.circle(img, o, r + 5, C_NOSE, 2, cv2.LINE_AA)
            cv2.circle(img, o, r + 2, C_NOSE, -1, cv2.LINE_AA)
        if "glasses" in layers:
            bx, by = P(fs.BRIDGE)
            col = C_GLASSES if self.face.get("glasses") else (120, 120, 120)
            cv2.rectangle(img, (int(bx - 0.18 * iod), int(by - 0.1 * iod)),
                          (int(bx + 0.18 * iod), int(by + 0.34 * iod)), col, 1, cv2.LINE_AA)
        return img

    def render_zoom(self, frame, part: str = "eyes", cell=(320, 180)):
        """Close-ups with P1-P6 labelled: part "eyes" (right + left eye) or "mouth"."""
        import cv2
        import numpy as np
        cw, ch = cell
        groups = (((fs.EAR_RIGHT, "RIGHT EYE"), (fs.EAR_LEFT, "LEFT EYE")) if part == "eyes"
                  else ((fs.MOUTH_POINTS, "MOUTH"),))
        out = np.full((ch, cw * len(groups) + 4 * (len(groups) - 1), 3), 20, np.uint8)
        px = self.landmarks
        if not px:
            return out
        h, w = frame.shape[:2]
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
            if part == "mouth":
                cv2.polylines(crop, [np.array(q, "int32")], True, C_MOUTH, 1, cv2.LINE_AA)
                for a, b in ((1, 7), (2, 6), (3, 5)):
                    cv2.line(crop, q[a], q[b], C_MOUTH, 2, cv2.LINE_AA)
                cv2.line(crop, q[0], q[4], C_MOUTH, 1, cv2.LINE_AA)
                for pt in q:
                    cv2.circle(crop, pt, 4, C_MOUTH, -1, cv2.LINE_AA)
            else:
                cv2.line(crop, q[0], q[3], C_EYES_HI, 1, cv2.LINE_AA)
                cv2.line(crop, q[1], q[5], C_EYES, 2, cv2.LINE_AA)
                cv2.line(crop, q[2], q[4], C_EYES, 2, cv2.LINE_AA)
                for j, pt in enumerate(q):
                    col = C_EYES_HI if j in (0, 3) else C_EYES
                    cv2.circle(crop, pt, 5, col, -1, cv2.LINE_AA)
                    lbl = f"P{j + 1}"
                    off = (-30, -8) if j in (1, 2) else (-30, 24) if j in (4, 5) else ((-32, 6) if j == 0 else (9, 6))
                    org = (pt[0] + off[0], pt[1] + off[1])
                    cv2.putText(crop, lbl, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
                    cv2.putText(crop, lbl, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 1, cv2.LINE_AA)
            cv2.putText(crop, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(crop, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, C_TEXT, 1, cv2.LINE_AA)
            out[:, k * (cw + 4):k * (cw + 4) + cw] = crop
        return out
