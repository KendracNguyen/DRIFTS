"""MediaPipe's face models run by OpenCV, for CPUs that MediaPipe 1.x can't run on.

MediaPipe 1.x's ARM build needs the CPU's AES (crypto) instructions. The
Raspberry Pi Zero 2 W (Cortex-A53, BCM2710) and Pi 4 (BCM2711) don't have
them, so creating the landmarker kills the process ("This binary was compiled
with aes enabled ... Illegal instruction"). The 0.10 releases that ran there
have no Python 3.13 build for current Raspberry Pi OS.

This module runs the same two models from face_landmarker.task with OpenCV's
DNN module (apt's python3-opencv 4.10, no AES needed):

    face_detector.tflite            BlazeFace short range, 128 x 128: finds the face
    face_landmarks_detector.tflite  478 points (x, y, z) in a 256 x 256 face crop

and does what MediaPipe's graph does around them: letterbox, anchor decoding,
a rotated square crop from the eye line, mapping the points back to the
frame, and tracking (the next crop comes from the last points, so the detector
only runs when the face is lost). The head rotation matrix is built from the
3D points (eye line, chin-to-forehead line); drifts.facesignals.angles_from_matrix
reads it like MediaPipe's facial transformation matrix. No blendshapes.

    lm = CvFaceLandmarker(task_path)
    px, matrix = lm.detect(rgb)     # 478 (x, y) pixels and a 4x4 matrix, or (None, None)
"""

from __future__ import annotations

import math
import zipfile
from pathlib import Path
from typing import Optional

DET_SIZE, LM_SIZE = 128, 256
SCORE_MIN = 0.5            # detector
PRESENCE_MIN = 0.5         # landmark model: is there still a face in the crop
CROP_SCALE = 1.5           # the crop is this much bigger than the face (MediaPipe's value)
R_EYE, L_EYE = 33, 263     # outer eye corners (the driver's right / left)
FOREHEAD, CHIN = 10, 152
# MediaPipe's canonical face is tilted: its pitch reads ~5 degrees lower than
# the chin-to-forehead line (measured on the sample images). Matched here.
PITCH_OFFSET_DEG = -5.0


def has_aes(text: Optional[str] = None) -> Optional[bool]:
    """Does this CPU have the AES instructions MediaPipe 1.x's ARM build needs?
    None when it can't tell (not Linux). `text`: /proc/cpuinfo's contents (tests)."""
    if text is None:
        try:
            text = Path("/proc/cpuinfo").read_text(errors="ignore")
        except OSError:
            return None
    for line in text.splitlines():
        low = line.lower()
        if low.startswith(("features", "flags")):
            return " aes" in f" {low.split(':', 1)[-1]} "
    return None


def needs_opencv_engine() -> bool:
    """True on ARM CPUs without AES (Pi Zero 2 W, Pi 3, Pi 4): MediaPipe 1.x would crash."""
    import platform
    return platform.machine().lower() in ("aarch64", "arm64") and has_aes() is False


def _anchors() -> list:
    """BlazeFace short-range SSD anchors: 16 x 16 x 2 + 8 x 8 x 6 = 896 (x, y) centres, 0-1."""
    out = []
    for stride, per in ((8, 2), (16, 6)):
        n = DET_SIZE // stride
        for y in range(n):
            for x in range(n):
                for _ in range(per):
                    out.append(((x + 0.5) / n, (y + 0.5) / n))
    return out


def _sigmoid(v):
    import numpy as np
    return 1.0 / (1.0 + np.exp(-np.clip(v, -80, 80)))


class CvFaceLandmarker:
    def __init__(self, task_path, threads: int = 0):
        import cv2
        import numpy as np
        self.cv2, self.np = cv2, np
        if threads:
            cv2.setNumThreads(threads)
        with zipfile.ZipFile(str(task_path)) as z:
            det = np.frombuffer(z.read("face_detector.tflite"), np.uint8)
            lm = np.frombuffer(z.read("face_landmarks_detector.tflite"), np.uint8)
        try:
            self.det = cv2.dnn.readNetFromTFLite(det)
            self.lm = cv2.dnn.readNetFromTFLite(lm)
        except cv2.error as exc:
            if "FlatBuffers" in str(exc):          # Debian / Raspberry Pi OS's python3-opencv
                raise RuntimeError(f"this OpenCV ({cv2.__version__}, {cv2.__file__}) can't read TFLite models: "
                                   "install the official build: .venv/bin/pip install "
                                   "opencv-python-headless==4.10.0.84") from None
            raise
        self.det_out = self.det.getUnconnectedOutLayersNames()
        self.lm_out = self.lm.getUnconnectedOutLayersNames()
        # OpenCV 4.8-4.12 read both outputs of each model; OpenCV 5's importer drops all but one.
        if len(self.det_out) < 2 or len(self.lm_out) < 2:
            raise RuntimeError(f"OpenCV {cv2.__version__} reads only {len(self.det_out)} / {len(self.lm_out)} outputs "
                               "of the face models: use OpenCV 4.x (Raspberry Pi OS: sudo apt install python3-opencv)")
        self.anchors = np.array(_anchors(), np.float32)
        self.roi = None                   # (cx, cy, size, angle) in frame pixels, from the last frame
        self.ran_detector = False

    # ------------------------------------------------------------ detector
    def _detect_face(self, rgb):
        """The best face as (cx, cy, size, angle) in frame pixels, or None."""
        cv2, np = self.cv2, self.np
        h, w = rgb.shape[:2]
        s = max(h, w)
        pad = np.zeros((s, s, 3), np.uint8)                      # letterbox to a square, as MediaPipe does
        oy, ox = (s - h) // 2, (s - w) // 2
        pad[oy:oy + h, ox:ox + w] = rgb
        img = cv2.resize(pad, (DET_SIZE, DET_SIZE), interpolation=cv2.INTER_AREA)
        blob = (img.astype(np.float32) / 127.5 - 1.0).transpose(2, 0, 1)[None]
        self.det.setInput(blob)
        outs = dict(zip(self.det_out, self.det.forward(self.det_out)))
        scores = next(v for v in outs.values() if v.shape[-1] == 1)
        boxes = next(v for v in outs.values() if v.shape[-1] == 16)
        scores = _sigmoid(scores.reshape(-1))
        i = int(np.argmax(scores))
        if scores[i] < SCORE_MIN:
            return None
        r = boxes.reshape(-1, 16)[i] / DET_SIZE
        ax, ay = self.anchors[i]
        bw, bh = r[2], r[3]
        kp = [(ax + r[4 + 2 * k], ay + r[5 + 2 * k]) for k in range(6)]  # 0 right eye, 1 left eye (image order)
        to_px = lambda x, y: (x * s - ox, y * s - oy)              # noqa: E731
        cx, cy = to_px(ax + r[0], ay + r[1])
        (rx, ry), (lx, ly) = to_px(*kp[0]), to_px(*kp[1])
        angle = math.atan2(ly - ry, lx - rx)                        # the eye line's tilt
        return cx, cy, max(bw, bh) * s * CROP_SCALE, angle

    @staticmethod
    def _roi_from_points(px):
        xs = [p[0] for p in px]
        ys = [p[1] for p in px]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        angle = math.atan2(px[L_EYE][1] - px[R_EYE][1], px[L_EYE][0] - px[R_EYE][0])
        # the size along the face's own axes (a turned head stays the same crop size)
        ca, sa = math.cos(-angle), math.sin(-angle)
        us = [(x - cx) * ca - (y - cy) * sa for x, y in px]
        vs = [(x - cx) * sa + (y - cy) * ca for x, y in px]
        size = max(max(us) - min(us), max(vs) - min(vs)) * CROP_SCALE
        return cx, cy, size, angle

    # ------------------------------------------------------------ landmarks
    def _landmarks(self, rgb, roi):
        cv2, np = self.cv2, self.np
        cx, cy, size, angle = roi
        # affine: crop pixel (u, v) in 0..LM_SIZE -> frame pixel
        k = size / LM_SIZE
        ca, sa = math.cos(angle) * k, math.sin(angle) * k
        to_frame = np.array([[ca, -sa, cx - ca * LM_SIZE / 2 + sa * LM_SIZE / 2],
                             [sa, ca, cy - sa * LM_SIZE / 2 - ca * LM_SIZE / 2]], np.float64)
        crop = cv2.warpAffine(rgb, to_frame, (LM_SIZE, LM_SIZE),
                              flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT)
        self.lm.setInput((crop.astype(np.float32) / 255.0).transpose(2, 0, 1)[None])
        outs = dict(zip(self.lm_out, self.lm.forward(self.lm_out)))
        pts = next(v for v in outs.values() if v.size == 478 * 3).reshape(478, 3)
        flag = next((v for n, v in outs.items() if v.size == 1 and n.endswith("_1")), None)
        presence = float(_sigmoid(flag.reshape(-1)[0])) if flag is not None else 1.0
        uv1 = np.concatenate([pts[:, :2], np.ones((478, 1))], axis=1)
        xy = uv1 @ to_frame.T
        z = pts[:, 2] * k                                        # depth, in frame pixels
        return xy, z, presence

    def _matrix(self, xy, z):
        """A 4x4 head rotation like MediaPipe's facial transformation matrix (rotation part)."""
        np = self.np
        p = np.concatenate([xy, z[:, None]], axis=1)
        p[:, 1] *= -1                                            # y up, as in MediaPipe's canonical space
        p[:, 2] *= -1                                            # z towards the camera
        xa = p[L_EYE] - p[R_EYE]                                 # face x: right eye -> left eye
        ya = p[FOREHEAD] - p[CHIN]                               # face y: chin -> forehead
        xa /= np.linalg.norm(xa)
        ya = ya - xa * float(ya @ xa)
        ya /= np.linalg.norm(ya)
        za = np.cross(xa, ya)
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2] = xa, ya, za
        t = math.radians(PITCH_OFFSET_DEG)
        tilt = np.eye(4)
        tilt[1, 1] = tilt[2, 2] = math.cos(t)
        tilt[1, 2], tilt[2, 1] = -math.sin(t), math.sin(t)
        return m @ tilt

    def detect(self, rgb):
        """(478 (x, y) frame pixels, 4x4 matrix) or (None, None)."""
        self.ran_detector = False
        for attempt in (0, 1):
            roi = self.roi
            if roi is None:
                roi = self._detect_face(rgb)
                self.ran_detector = True
                if roi is None:
                    return None, None
            xy, z, presence = self._landmarks(rgb, roi)
            if presence >= PRESENCE_MIN:
                px = [(float(x), float(y)) for x, y in xy]
                self.roi = self._roi_from_points(px)             # track: next crop from these points
                return px, self._matrix(xy, z)
            self.roi = None                                      # lost it: run the detector once more
            if self.ran_detector:
                break
        return None, None
