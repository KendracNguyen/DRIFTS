"""Pi 5 face backend: all eight driver signals from one MediaPipe inference.

Built on dms-face (github.com/youssefmedhat4/dms-face, MIT, pinned at
7b32c63 by deploy/install.sh), which provides the eye and head pipeline:
EAR -> calibrated openness -> blink state machine -> PERCLOS, iris gaze, and
solvePnP head pose, with per-driver calibration profiles. On top of the same
landmarks this adds mouth aperture and yawns (mouth.py) and day/night (IR)
detection with IR contrast enhancement (night.py).

Camera-like interface for drifts.live: grab() returns the BGR frame and sets
  .face       dict of signals for the dashboard (see _signals)
  .keypoints  17 COCO-style points (nose, eyes, ears) for the shared overlay
  .landmarks  (478, 2) pixels, for drawing eye and mouth contours

    python -m drifts.live --face mediapipe --profile kendra --esp ble
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

from drifts.mouth import YawnDetector, mar
from drifts.night import NightDetector, enhance_ir, mean_saturation

DEFAULT_DMS_DIR = os.environ.get("DMS_FACE_DIR", str(Path.home() / "dms-face"))

# MediaPipe face-mesh indices ("left" = the driver's left).
NOSE_TIP = 1
L_EYE_CORNERS, R_EYE_CORNERS = (362, 263), (33, 133)
L_FACE_EDGE, R_FACE_EDGE = 454, 234       # cheek edge, stand-in for the ear
EYE_CONTOURS = ((362, 385, 387, 263, 373, 380), (33, 160, 158, 133, 153, 144))
MOUTH_CONTOUR = (78, 82, 13, 312, 308, 317, 14, 87)


def coco_from_mesh(px) -> list:
    """17 COCO keypoints (x, y, score) from (478, 2) mesh pixels."""
    def mid(a, b):
        return ((px[a][0] + px[b][0]) / 2.0, (px[a][1] + px[b][1]) / 2.0)
    pts = [tuple(px[NOSE_TIP]), mid(*L_EYE_CORNERS), mid(*R_EYE_CORNERS), tuple(px[L_FACE_EDGE]), tuple(px[R_FACE_EDGE])]
    return [(float(x), float(y), 1.0) for x, y in pts] + [(0.0, 0.0, 0.0)] * 12


class Pi5FaceSource:
    def __init__(self, dms_dir: str = DEFAULT_DMS_DIR, profile: str = "default", camera=None,
                 ir: str = "auto", yaw_sign: int = 1, size=(640, 480), pipeline=None):
        self.dms_dir, self.profile_name, self.camera = Path(dms_dir), profile, camera
        self.ir, self.yaw_sign, self.size = ir, yaw_sign, size
        self._pipe = pipeline                 # injectable for tests
        self.night = NightDetector()
        self.yawns = YawnDetector()
        self.face: dict = {"found": False}
        self.keypoints = None
        self.landmarks = None
        self.people = 0
        self.inference_ms: Optional[float] = None
        self.profile = None
        self.profile_loaded = False
        self._t0 = time.monotonic()

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        if self._pipe is None:
            self._pipe = self._make_pipeline()
        if self.camera is not None:
            self.camera.start()

    def _make_pipeline(self):
        if not (self.dms_dir / "dms_face").is_dir():
            raise FileNotFoundError(f"dms-face not found at {self.dms_dir}: run PI5=1 bash deploy/install.sh "
                                    "or set DMS_FACE_DIR")
        sys.path.insert(0, str(self.dms_dir))
        from dms_face.calibration import DriverProfile
        from dms_face.config import Config
        from dms_face.pipeline import FacePipeline

        cfg = Config()
        cfg.camera.width, cfg.camera.height = self.size
        model = Path(cfg.landmarker.model_path)
        cfg.landmarker.model_path = str(model if model.is_absolute() else self.dms_dir / model)
        self.profile, loaded = DriverProfile.load_or_default(self.profile_name,
                                                             directory=str(self.dms_dir / "profiles"))
        self.profile_loaded = loaded
        if not loaded:
            print(f"[face] No calibration profile '{self.profile_name}'. Eye signals need one "
                  "(stop drifts.live first, the camera is in use):\n"
                  f"       cd {self.dms_dir} && ~/drifts/.venv/bin/python run.py --calibrate --profile "
                  f"{self.profile_name} --no-display", flush=True)
        return FacePipeline(cfg, self.profile, width=self.size[0], height=self.size[1])

    # ------------------------------------------------------------------ frames
    def grab(self):
        frame = self.camera.grab() if self.camera is not None else None
        if frame is None:
            return None
        self.process(frame, time.monotonic())
        return frame

    def process(self, frame, now: float) -> dict:
        import cv2
        night = self.night.update(now, mean_saturation(frame))
        use_ir = self.ir == "on" or (self.ir == "auto" and night)
        img = enhance_ir(frame) if use_ir else frame
        res = self._pipe.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), now)
        self.inference_ms = getattr(res, "inference_ms", None)
        px = getattr(res.landmarks, "pixels", None) if res.face_present and res.landmarks is not None else None
        self.landmarks = px
        self.keypoints = coco_from_mesh(px) if px is not None and len(px) > R_EYE_CORNERS[1] else None
        self.people = 1 if res.face_present else 0
        m = mar(px) if px is not None else None
        self.yawns.update(now, m)
        self.face = self._signals(res, now, m, night, use_ir)
        return self.face

    def _signals(self, res, now: float, m: Optional[float], night: bool, ir_enhanced: bool) -> dict:
        rep = res.report
        prof = self.profile
        ang = res.angles
        rel = None
        if ang is not None:
            y0, p0, r0 = (prof.yaw0, prof.pitch0, prof.roll0) if prof is not None else (0.0, 0.0, 0.0)
            rel = (self.yaw_sign * (ang[0] - y0), ang[1] - p0, ang[2] - r0)
        state = getattr(getattr(rep, "state", None), "value", None)
        perclos_ok = bool(rep and rep.perclos_valid)
        return {
            "found": bool(res.face_present),
            "yaw": rel[0] if rel else None, "pitch": rel[1] if rel else None, "roll": rel[2] if rel else None,
            "ear": res.ear_filtered, "openness": res.openness_smooth,
            "eye_state": getattr(res.eye_state, "name", str(res.eye_state)).lower(),
            "eye_valid": bool(res.eye_channel_valid),
            "blink_rate": rep.blink_rate if rep else None,          # per minute
            "mean_blink_ms": rep.mean_blink_ms if rep else None,
            "closure_ms": rep.closure_ms if rep else 0.0,
            "perclos": rep.perclos if perclos_ok else None,
            "perclos_warming": bool(rep and rep.perclos_warming),
            "gaze_dev": res.gaze_dev, "gaze_off_road_s": rep.gaze_off_road_s if rep else 0.0,
            "mar": m, "yawning": self.yawns.yawning(), "yawns": self.yawns.recent(now),
            "state": state, "reasons": list(rep.reasons) if rep else [],
            "should_alert": bool(rep and rep.should_alert),
            "night": night, "ir_enhanced": ir_enhanced, "saturation": self.night.saturation,
            "inference_ms": self.inference_ms,
            "profile": self.profile_name,
            "profile_loaded": self.profile_loaded,
        }

    def eye_score(self) -> Optional[int]:
        """Eye score for the ESP32: 100 x (1 - PERCLOS), once PERCLOS is valid."""
        p = self.face.get("perclos")
        return None if p is None else max(0, min(100, round(100 * (1 - p))))

    def stop(self) -> None:
        if self.camera is not None:
            self.camera.stop()
        if self._pipe is not None and hasattr(self._pipe, "close"):
            self._pipe.close()
