"""DRIFTS live dashboard: camera feed with eye tracking, ESP32 telemetry, and
training-frame capture, served as a web page.

    # Laptop + webcam + ESP32 on USB (no Pi needed):
    python -m drifts.live --esp serial --port COM5
    # Raspberry Pi in the car (Pi camera, ESP32 over BLE):
    sudo systemctl stop drifts && python -m drifts.live --esp ble
    # No hardware at all:
    python -m drifts.live --esp sim
    # All eight face signals + the dev view (laptop webcam first, then the Pi):
    python -m drifts.live --face mesh --camera 0 --esp sim      # see Docs/dev-view.md

Then open http://localhost:8080 (or http://<pi-address>:8080 from a phone on
the same network).

What it does, once per camera frame:
  1. eyes.EyeTracker finds the face and eyes -> open / closed / no face
  2. PerclosWindow -> PERCLOS over the last N seconds -> eye score 0-100
  3. every --eye-every seconds the eye score is sent to the ESP32 as EYE:<n>,
     so the ESP32's alert score and alert outputs react to the camera live
The ESP32 link supplies OBD (rpm / speed / throttle), the alert score and
level, all shown next to the video.

Capture buttons save the raw frame plus a label in the Simuletic layout
(images/ + labels/<session>.json), so `training/train.py --data-dir captures`
can train on it directly.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from drifts.camera import Camera
from drifts.config import load_config
from drifts.esp_link import make_link
from drifts.facemesh import DEFAULT_LAYERS, LAYERS as DEV_LAYERS
from drifts.eyes import CLOSED, NO_FACE, OPEN, EyeObservation, EyeTracker, PerclosWindow, eye_score
from drifts.headpose import AttentionTracker, Calibration, HeadPose, estimate
from drifts.perf import PerfMonitor, capability

log = logging.getLogger("drifts.live")

LABELS = {"open": "Open", "closed": "Closed", "drowsy": "Drowsy/Microsleep"}


class VideoSource:
    """Camera stand-in: loops a video file or a folder of .jpg/.png images."""

    def __init__(self, path: str, fps: float = 8.0):
        self.path, self.period = Path(path), 1.0 / max(0.5, fps)
        self._cap = None
        self._files: list[Path] = []
        self._i = 0
        self._next = 0.0

    def start(self) -> None:
        import cv2
        if self.path.is_dir():
            self._files = sorted(f for f in self.path.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png"))
            if not self._files:
                raise FileNotFoundError(f"no images in {self.path}")
        else:
            self._cap = cv2.VideoCapture(str(self.path))
            if not self._cap.isOpened():
                raise FileNotFoundError(f"cannot open video {self.path}")
        log.info("Playing %s instead of a camera", self.path)

    def grab(self):
        import cv2
        wait = self._next - time.time()
        if wait > 0:
            time.sleep(wait)
        self._next = time.time() + self.period
        if self._files:
            img = cv2.imread(str(self._files[self._i % len(self._files)]))
            self._i += 1
            return img
        ok, frame = self._cap.read()
        if not ok:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
        return frame if ok else None

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()


class Capture:
    """Saves labelled frames as images/<name>.jpg + labels/<session>.json."""

    def __init__(self, root: Path):
        self.root = root
        self.session = "drifts_live_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.samples: list[dict] = []
        self.counts = {k: 0 for k in LABELS}
        self._lock = threading.Lock()
        self._t0 = time.time()

    def save(self, frame, label: str, perclos: Optional[float]) -> str:
        import cv2
        with self._lock:
            (self.root / "images").mkdir(parents=True, exist_ok=True)
            (self.root / "labels").mkdir(parents=True, exist_ok=True)
            name = f"{self.session}_f{len(self.samples):05d}.jpg"
            cv2.imwrite(str(self.root / "images" / name), frame)
            self.samples.append({
                "image": name,
                "timestamp": round(time.time() - self._t0, 3),
                "attributes": {"eye_state": LABELS[label],
                               "perclos": None if perclos is None else round(perclos, 3)},
            })
            self.counts[label] += 1
            doc = {"info": {"source": "drifts.live webcam", "metadata": "DRIFTS capture"},
                   "samples": self.samples}
            (self.root / "labels" / f"{self.session}.json").write_text(json.dumps(doc, indent=1))
            return name


class Pipeline:
    """Camera -> eye tracker -> PERCLOS -> annotated JPEG, plus EYE sending."""

    def __init__(self, camera: Camera, link, args):
        self.camera, self.link, self.args = camera, link, args
        # Pi 5 rig: the camera source itself delivers every face signal (dms-face).
        self.face_on = hasattr(camera, "face")
        # Haar eye tracker: not needed with the Pi 5 face backend; optional on the
        # Pi Zero with the AI Camera (CPU-bound).
        self.tracker = None if (args.no_eyes or self.face_on) else EyeTracker(work_width=args.work_width)
        # Head pose + road attention from camera keypoints (AI Camera or simulated),
        # or from the Pi 5 face backend.
        self.pose_on = args.pose != "off" or self.face_on
        self.backend = getattr(camera, "backend", "mediapipe") if self.face_on else args.pose
        # Dev view: the full-analysis overlay and close-ups are drawn only while
        # someone is watching them (they cost CPU on a Pi Zero).
        self.dev_capable = hasattr(camera, "render_dev")
        self.dev_until = 0.0
        self.dev_layers = set(DEFAULT_LAYERS)
        self.dev_jpeg: Optional[bytes] = None
        self.zoom_jpeg: Optional[bytes] = None        # eyes close-up
        self.mouth_jpeg: Optional[bytes] = None
        self.dev_id = 0
        self.perf = PerfMonitor()
        # Extension points (Tokyo uses them): per-frame callbacks f(pipe, now),
        # extra status fields, and fields overlaid on the ESP32 snapshot (e.g.
        # an alert score computed on this computer instead of the ESP32's).
        self.hooks: list = []
        self.extra_status: dict = {}
        self.esp_override: dict = {}
        self._perf, self._perf_t = self.perf.snapshot(), 0.0
        self.cal = Calibration(auto_samples=args.calib_frames)
        self.attn = AttentionTracker(yaw_limit=args.yaw_limit, pitch_down=args.pitch_limit,
                                     pitch_up=args.pitch_limit)
        self._prev_raw = None
        self.pose = {"enabled": self.pose_on, "source": args.pose, "found": False, "yaw": None,
                     "pitch": None, "roll": None, "profile": False, "quality": 0.0,
                     "attention": "unknown", "off_for": 0.0, "distracted": False, "glances": 0,
                     "calibrated": False, "calib_progress": 0.0, "people": 0, "inference_ms": None}
        self.window = PerclosWindow(args.window)
        self.capture = Capture(Path(args.capture_dir))
        self.send_eye = not args.no_eye
        self.jpeg: Optional[bytes] = None
        self.frame = None
        self.frame_id = 0
        self.eye = {"state": NO_FACE, "perclos": None, "score": None, "closed_for": 0.0,
                    "fps": 0.0, "last_sent": None, "warming": True}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._burst: list[str] = []
        self.started = time.time()
        self.error: Optional[str] = None          # why the camera thread stopped, for the page
        self.camera_note: Optional[str] = None    # e.g. "no webcam found: showing a blank image"
        # 1 Hz session history for the History tab: (t, alert score, eye score, rpm)
        self.history: list[tuple] = []
        from drifts.trips import TripLog
        self.trips = TripLog(Path(args.trips))     # the History tab's earlier drives
        self._trip_saved = 0.0
        self.whisplay = False                      # set when --whisplay drives the HAT
        self._wifi = (-1e9, None)

    def start(self) -> None:
        threading.Thread(target=self._run, name="camera", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def recalibrate(self) -> None:
        """Driver looks at the road; the next ~3 s become the neutral pose."""
        self.cal.restart(self.args.calib_frames)
        if hasattr(self.camera, "calibrate"):
            self.camera.calibrate(self.args.calib_frames)

    def _update_pose(self, now: float) -> None:
        if self.face_on:
            return self._update_face_pose(now)
        kps = getattr(self.camera, "keypoints", None)
        raw = estimate(kps, prev=self._prev_raw) if kps is not None else None
        if raw is not None and raw.found:
            self._prev_raw = raw if not raw.profile else self._prev_raw
            self.cal.add(raw)
            pose = self.cal.apply(raw)
        else:
            self._prev_raw, pose = None, None
        from drifts.headpose import HeadPose
        state = self.attn.update(now, pose or HeadPose(found=False))
        prog = 1.0 if self.cal.done else len(self.cal._y) / max(1, self.cal.auto_samples)
        self.pose.update(found=bool(pose), yaw=pose.yaw if pose else None,
                         pitch=pose.pitch if pose else None, roll=pose.roll if pose else None,
                         profile=bool(pose and pose.profile), quality=pose.quality if pose else 0.0,
                         attention=state, off_for=self.attn.off_for(now),
                         distracted=self.attn.distracted(now), glances=self.attn.events,
                         calibrated=self.cal.done, calib_progress=prog,
                         people=getattr(self.camera, "people", 0),
                         inference_ms=getattr(self.camera, "inference_ms", None))

    def _update_face_pose(self, now: float) -> None:
        """Pi 5: head angles and iris gaze come from dms-face. With a driver
        profile they are already relative to the calibrated road pose; without
        one, the same auto-calibration as the AI Camera rig is applied."""
        f = self.camera.face
        raw = (HeadPose(found=True, yaw=f["yaw"], pitch=f["pitch"], roll=f["roll"], quality=1.0)
               if f.get("found") and f.get("yaw") is not None else None)
        if raw is not None and not getattr(self.camera, "profile_loaded", False):
            # Only learn the road pose from frames with the eyes open: a head
            # that droops while the eyes close must not become "normal".
            if f.get("eye_state", "open") == "open":
                self.cal.add(raw)
            raw = self.cal.apply(raw)
        calibrated = getattr(self.camera, "profile_loaded", False) or self.cal.done
        gd = f.get("gaze_dev")
        gaze_off = (gd is not None and gd > self.args.gaze_dev) or bool(f.get("eyes_closed"))
        state = self.attn.update(now, raw or HeadPose(found=False), gaze_off=gaze_off)
        prog = 1.0 if calibrated else len(self.cal._y) / max(1, self.cal.auto_samples)
        self.pose.update(found=bool(raw), yaw=raw.yaw if raw else None, pitch=raw.pitch if raw else None,
                         roll=raw.roll if raw else None, profile=False, quality=1.0 if raw else 0.0,
                         attention=state, off_for=self.attn.off_for(now), distracted=self.attn.distracted(now),
                         glances=self.attn.events, calibrated=calibrated, calib_progress=prog,
                         gaze_off=gaze_off, people=getattr(self.camera, "people", 0),
                         inference_ms=getattr(self.camera, "inference_ms", None))

    def _draw_face(self, img, px, mirrored: bool) -> None:
        """Eye and mouth outlines from the 478-point mesh (Pi 5 rig)."""
        import cv2
        import numpy as np
        from drifts.face_mediapipe import EYE_CONTOURS, MOUTH_CONTOUR
        if px is None:
            return
        w = img.shape[1]
        def poly(idx, colour):
            pts = np.array([[w - px[i][0] if mirrored else px[i][0], px[i][1]] for i in idx], np.int32)
            cv2.polylines(img, [pts], True, colour, 1, cv2.LINE_AA)
        f = self.camera.face
        eye_col = (60, 60, 230) if f.get("eye_state") in ("closed", "closing") else (255, 200, 0)
        for c in EYE_CONTOURS:
            poly(c, eye_col)
        poly(MOUTH_CONTOUR, (60, 60, 230) if f.get("yawning") else (200, 120, 255))
        if f.get("night"):
            cv2.putText(img, "NIGHT (IR)" + (" +CLAHE" if f.get("ir_enhanced") else ""), (10, 56),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 200, 255), 1)

    def _draw_pose(self, img, kps, mirrored: bool) -> None:
        import cv2
        if kps is None:
            return
        w = img.shape[1]
        pts = {}
        for i, colour in ((0, (60, 200, 255)), (1, (255, 200, 0)), (2, (255, 200, 0)),
                          (3, (200, 120, 255)), (4, (200, 120, 255))):
            x, y, c = float(kps[i][0]), float(kps[i][1]), float(kps[i][2])
            if c < 0.3:
                continue
            x = w - x if mirrored else x
            pts[i] = (int(x), int(y))
            cv2.circle(img, pts[i], 4, colour, -1)
        if 1 in pts and 2 in pts:
            cv2.line(img, pts[1], pts[2], (255, 200, 0), 1)
        if 0 in pts and self.pose["yaw"] is not None and not self.pose["profile"]:
            import math
            L = 70
            dx = math.sin(math.radians(self.pose["yaw"])) * L
            dx = dx if mirrored else -dx       # driver's right appears on the image left
            dy = math.sin(math.radians(self.pose["pitch"])) * L
            end = (int(pts[0][0] + dx), int(pts[0][1] + dy))
            colour = (80, 200, 80) if self.pose["attention"] == "on_road" else (60, 60, 230)
            cv2.arrowedLine(img, pts[0], end, colour, 3, tipLength=0.25)
        y0 = img.shape[0] - 12
        label = {"on_road": "ON ROAD", "off_road": f"OFF ROAD {self.pose['off_for']:.1f}s",
                 "unknown": "NO HEAD POSE"}[self.pose["attention"]]
        if not self.pose["calibrated"]:
            label += "  (calibrating: look at the road)"
        if self.args.pose == "sim":
            cv2.putText(img, "SIMULATED HEAD (scripted keypoints)", (10, 56), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (60, 200, 255), 1)
        cv2.rectangle(img, (0, y0 - 22), (w, img.shape[0]), (0, 0, 0), -1)
        cv2.putText(img, label, (10, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (80, 200, 80) if self.pose["attention"] == "on_road" else (60, 60, 230), 2)
        if self.pose["yaw"] is not None and not self.pose["profile"]:
            cv2.putText(img, f"Y {self.pose['yaw']:+.0f}  P {self.pose['pitch']:+.0f}  R {self.pose['roll']:+.0f}",
                        (w - 210, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)

    def request_capture(self, label: str, n: int) -> None:
        with self._lock:
            self._burst += [label] * max(1, min(n, 50))

    def _annotate(self, frame, obs, perclos, score, closed_for):
        import cv2
        img = frame.copy()
        colour = {OPEN: (80, 200, 80), CLOSED: (60, 60, 230), NO_FACE: (0, 200, 230)}[obs.state]
        if obs.face:
            x, y, w, h = obs.face
            cv2.rectangle(img, (x, y), (x + w, y + h), colour, 2)
        for x, y, w, h in obs.eyes:
            cv2.rectangle(img, (x, y), (x + w, y + h), (255, 200, 0), 1)
        banner = {OPEN: "EYES OPEN", CLOSED: "EYES CLOSED", NO_FACE: "NO FACE"}[obs.state]
        if obs.state == CLOSED and closed_for >= 1.0:
            banner += f"  {closed_for:.1f}s"
        cv2.rectangle(img, (0, 0), (img.shape[1], 34), (0, 0, 0), -1)
        cv2.putText(img, banner, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
        info = f"PERCLOS {'--' if perclos is None else f'{perclos*100:.0f}%'}  eye {'--' if score is None else score}"
        cv2.putText(img, info, (img.shape[1] - 260, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)
        return img

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as exc:
            # Without this the camera thread dies silently and the page just
            # shows a black feed at 0 fps. Say why, in the log and on the page.
            log.exception("Camera pipeline stopped")
            self.error = f"{type(exc).__name__}: {exc}"

    def _loop(self) -> None:
        import cv2
        self.camera.start()
        self.camera_note = describe_camera(self.camera)
        self._last_frame_t = time.time()
        last_send = 0.0
        t_fps, n_fps = time.time(), 0
        try:
            while not self._stop.is_set():
                frame = self.camera.grab()
                if frame is None:
                    if time.time() - self._last_frame_t > 5.0:
                        self.camera_note = ("The camera sends no frames. Another program probably has it: close other "
                                            "DRIFTS windows (check Task Manager for python), Zoom, Teams or the Camera "
                                            "app, then restart. Or try --camera 1.")
                    time.sleep(0.05)
                    continue
                self._last_frame_t = time.time()
                if self.camera_note and self.camera_note.startswith("The camera sends no frames"):
                    self.camera_note = None
                if self.args.mirror:
                    frame = cv2.flip(frame, 1)
                now = time.time()
                if self.face_on:
                    es = self.camera.face.get("eye_state") if self.camera.face.get("found") else None
                    obs = EyeObservation(CLOSED if es in ("closed", "closing") else
                                         OPEN if es in ("open", "opening") else NO_FACE)
                else:
                    obs = self.tracker.process(frame) if self.tracker else EyeObservation(NO_FACE)
                if self.pose_on:
                    self._update_pose(now)
                self.window.add(now, obs.state)
                perclos = self.window.perclos()
                score = eye_score(perclos)
                if self.face_on:      # dms-face PERCLOS: P80 over 60 s, invalid frames excluded
                    perclos = self.camera.face.get("perclos")
                    score = self.camera.eye_score()
                closed_for = self.window.closed_for(now)
                warming = self.window.coverage(now) < min(3.0, self.args.window)
                if self.face_on:
                    warming = self.camera.face.get("perclos") is None

                if self.send_eye and score is not None and not warming and now - last_send >= self.args.eye_every:
                    last_send = now
                    self.link.send_eye(score)
                    self.eye["last_sent"] = score

                with self._lock:
                    burst = self._burst.pop(0) if self._burst else None
                if burst:
                    self.capture.save(frame, burst, perclos)

                img = self._annotate(frame, obs, perclos, score, closed_for)
                if self.face_on:
                    self._draw_face(img, getattr(self.camera, "landmarks", None), self.args.mirror)
                if self.pose_on:
                    self._draw_pose(img, getattr(self.camera, "keypoints", None), self.args.mirror)
                for hook in self.hooks:
                    try:
                        hook(self, now)
                    except Exception as exc:          # a bad hook must not stop the camera
                        log.warning("hook %s failed: %s", getattr(hook, "__name__", hook), exc)
                if now - self._perf_t >= 2.0:
                    self._perf, self._perf_t = self.perf.snapshot(), now
                jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
                if self.dev_capable and now < self.dev_until:
                    raw = getattr(self.camera, "last_frame", None)
                    if raw is not None:
                        dev = self.camera.render_dev(raw, self.dev_layers, mirror=self.args.mirror)
                        enc = lambda im, q: cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()
                        dj = enc(dev, 80)
                        zj = enc(self.camera.render_zoom(raw, "eyes"), 85)
                        mj = enc(self.camera.render_zoom(raw, "mouth"), 85)
                        with self._lock:
                            self.dev_jpeg, self.zoom_jpeg, self.mouth_jpeg = dj, zj, mj
                            self.dev_id += 1
                if not self.history or now - self.history[-1][0] >= 1.0:
                    esp = {**self.link.snapshot(), **self.esp_override}
                    self.history.append((round(now, 1), esp["score"], score, esp["rpm"], self.pose["yaw"]))
                    del self.history[:-3600]          # keep the last hour
                    if now - self._trip_saved >= 30:
                        self._trip_saved = now
                        self.save_trip()
                n_fps += 1
                if now - t_fps >= 2.0:
                    self.eye["fps"] = n_fps / (now - t_fps)
                    t_fps, n_fps = now, 0
                with self._lock:
                    self.jpeg, self.frame, self.frame_id = jpeg, frame, self.frame_id + 1
                    self.eye.update(state=obs.state, perclos=perclos, score=score,
                                    closed_for=closed_for, warming=warming)
        finally:
            self.camera.stop()

    def save_trip(self) -> None:
        from drifts.trips import summarize
        esp = self.link.snapshot()
        self.trips.save(summarize(list(self.history), self.started, demo=esp.get("link") == "simulated"))

    def wifi(self) -> Optional[str]:
        if time.time() - self._wifi[0] > 30:
            from drifts.whisplay_ui import wifi_ssid
            self._wifi = (time.time(), wifi_ssid())
        return self._wifi[1]

    def status(self) -> dict:
        st = self._status()
        # The alertness band, label and colour, from the same rules as the LCD (drifts/ui_model.py)
        from drifts.ui_model import BANDS, COLORS, state_from_live
        ui = state_from_live(st)
        b = BANDS[ui.band]
        st["ui"] = {"band": ui.band, "title": b["title"], "subtitle": b["subtitle"], "icon": b["icon"],
                    "color": COLORS[b["color"]], "pose": ui.pose_label, "demo": ui.demo,
                    "outputs_on": ui.outputs_on}
        st["wifi"] = self.wifi()
        st["whisplay"] = self.whisplay
        return st

    def _status(self) -> dict:
        with self._lock:
            eye = dict(self.eye)
        return {
            "eye": eye, "eye_sending": self.send_eye, "eyes_sent": self.link.eyes_sent,
            "eye_tracker": self.tracker is not None or self.face_on, "pose": dict(self.pose),
            "face": dict(self.camera.face) if self.face_on else None,
            "dev": {"capable": self.dev_capable, "layers": sorted(self.dev_layers)},
            "rig": {"name": self.args.rig_name or self._perf["model"], "backend": self.backend,
                    "fps": eye["fps"], "inference_ms": getattr(self.camera, "inference_ms", None),
                    "perf": self._perf, **capability(self.backend)},
            "esp": {**self.link.snapshot(), **self.esp_override}, "log": list(self.link.log_lines)[-12:],
            **self.extra_status,
            "capture": {"session": self.capture.session, "counts": self.capture.counts,
                        "dir": str(self.capture.root.resolve()), "pending": len(self._burst)},
            "window_s": self.args.window, "time": time.time(), "started": self.started,
            "error": self.error, "camera_note": self.camera_note,
        }


def describe_camera(cam) -> Optional[str]:
    """A warning for the page when there is no real camera behind the source."""
    inner = getattr(cam, "camera", cam)
    if getattr(inner, "_picam2", None) is None and getattr(inner, "_cv_cap", "n/a") is None:
        return ("No webcam opened, so this is a blank test image. Close other apps using the camera "
                "(Zoom, Teams, the Windows Camera app, another browser tab), or try --camera 1.")
    return None


def make_handler(pipe: Pipeline):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):   # keep the console for the app's own log
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            try:
                self._send_now(code, body, ctype)
            except ConnectionError:      # the browser closed or refreshed the page mid-reply: harmless
                pass

        def _send_now(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # Lets the comparison page on the other rig read this one.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                # Read per request: edits to dashboard.html show on refresh.
                self._send(200, PAGE_FILE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/history.json":
                h = list(pipe.history)
                self._send(200, json.dumps({"t": [r[0] for r in h], "score": [r[1] for r in h],
                                            "eye": [r[2] for r in h], "rpm": [r[3] for r in h],
                                            "yaw": [r[4] for r in h]}).encode(),
                           "application/json")
            elif path == "/trips.json":
                self._send(200, json.dumps(pipe.trips.load()).encode(), "application/json")
            elif path.startswith("/static/fonts/") and path.endswith(".woff2"):
                f = FONT_DIR / Path(path).name
                if f.exists():
                    self.send_response(200)
                    body = f.read_bytes()
                    self.send_header("Content-Type", "font/woff2")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._send(404, b"no such font", "text/plain")
            elif path == "/compare":
                self._send(200, PAGE_FILE.with_name("compare.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/status.json":
                self._send(200, json.dumps(pipe.status()).encode(), "application/json")
            elif path in ("/dev.mjpg", "/zoom.mjpg", "/mouth.mjpg"):
                if not pipe.dev_capable:
                    self._send(404, b"dev view needs --face mesh", "text/plain")
                    return
                attr = {"/dev.mjpg": "dev_jpeg", "/zoom.mjpg": "zoom_jpeg", "/mouth.mjpg": "mouth_jpeg"}[path]
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                last = -1
                try:
                    while True:
                        pipe.dev_until = time.time() + 3.0       # keep rendering while watched
                        with pipe._lock:
                            jpeg, fid = getattr(pipe, attr), pipe.dev_id
                        if jpeg is not None and fid != last:
                            last = fid
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                        time.sleep(0.03)
                except ConnectionError:      # viewer left (broken pipe / reset / aborted on Windows)
                    pass
            elif path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                last = -1
                try:
                    while True:
                        with pipe._lock:
                            jpeg, fid = pipe.jpeg, pipe.frame_id
                        if jpeg is not None and fid != last:
                            last = fid
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                        time.sleep(0.03)
                except ConnectionError:      # viewer left (broken pipe / reset / aborted on Windows)
                    pass
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/capture" and q.get("label", [""])[0] in LABELS:
                pipe.request_capture(q["label"][0], int(q.get("n", ["1"])[0]))
                self._send(200, b"ok", "text/plain")
            elif u.path == "/calibrate":
                pipe.recalibrate()
                self._send(200, b"ok", "text/plain")
            elif u.path == "/beep":
                pipe.link.send_command("BEEP")      # ESP32 firmware v15+: one 1 s beep
                self._send(200, b"ok", "text/plain")
            elif u.path == "/glasses" and hasattr(pipe.camera, "set_glasses_mode"):
                pipe.camera.set_glasses_mode(q.get("mode", ["auto"])[0])
                self._send(200, b"ok", "text/plain")
            elif u.path == "/dev_layers":
                pipe.dev_layers = {k for k in DEV_LAYERS if q.get(k, ["1"])[0] == "1"}
                self._send(200, b"ok", "text/plain")
            elif u.path == "/eye_send":
                pipe.send_eye = q.get("on", ["1"])[0] == "1"
                self._send(200, b"ok", "text/plain")
            else:
                self._send(400, b"bad request", "text/plain")

    return Handler


def build_parser(prog: str = "python -m drifts.live") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="DRIFTS live dashboard")
    p.add_argument("--esp", choices=["serial", "ble", "sim"], default="sim",
                   help="ESP32 link: serial (USB), ble (Pi in the car), or sim (no hardware)")
    p.add_argument("--port", default="COM5", help="serial port for --esp serial (COM5, /dev/ttyACM0)")
    p.add_argument("--camera", type=int, default=0, help="webcam index (laptop)")
    p.add_argument("--camera-fps", type=int, default=None, help="camera frame rate (default: config.toml)")
    p.add_argument("--video", help="play a video file or a folder of images instead of a camera (loops)")
    p.add_argument("--pose", choices=["off", "imx500", "sim"], default="off",
                   help="head pose: imx500 = Raspberry Pi AI Camera, sim = scripted head on any camera")
    p.add_argument("--pose-model", default=None, help="AI Camera .rpk (default: HigherHRNet COCO)")
    p.add_argument("--pose-fps", type=int, default=15, help="AI Camera frame rate")
    p.add_argument("--yaw-limit", type=float, default=30.0, help="off road beyond this yaw (deg)")
    p.add_argument("--pitch-limit", type=float, default=20.0, help="off road beyond this pitch (deg)")
    p.add_argument("--calib-frames", type=int, default=30, help="frames averaged for the road pose")
    p.add_argument("--no-eyes", action="store_true", help="skip the Haar eye tracker (saves CPU on a Pi Zero)")
    p.add_argument("--face", choices=["off", "mesh", "mediapipe"], default="off",
                   help="mesh = all eight signals from MediaPipe's face landmarker (laptop, Pi Zero 2 W, Pi 5), "
                        "with the dev view; mediapipe = Pi 5 rig via dms-face")
    p.add_argument("--face-model", default=None, help="face_landmarker.task (default: downloaded to Raspberry/models)")
    p.add_argument("--whisplay", action="store_true",
                   help="also drive the Whisplay HAT screen with the live face data (Pi Zero; [whisplay] in config.toml)")
    p.add_argument("--whisplay-sim", action="store_true", help="--whisplay without the HAT (for testing)")
    p.add_argument("--glasses", choices=["auto", "on", "off"], default="auto",
                   help="--face mesh: glasses detection (on = always lean on head pose)")
    p.add_argument("--dms-dir", default=None, help="dms-face checkout (default ~/dms-face or $DMS_FACE_DIR)")
    p.add_argument("--profile", default="default", help="dms-face driver profile (run.py --calibrate)")
    p.add_argument("--ir", choices=["auto", "on", "off"], default="auto",
                   help="IR contrast enhancement: auto = when the image looks like night/IR")
    p.add_argument("--yaw-sign", type=int, choices=[-1, 1], default=1, help="flip if yaw reads backwards")
    p.add_argument("--gaze-dev", type=float, default=0.16, help="iris deviation counted as off road")
    p.add_argument("--rig-name", default=None, help="label for the comparison page")
    p.add_argument("--video-fps", type=float, default=8.0, help="playback rate for --video")
    p.add_argument("--no-picamera", action="store_true", help="skip the Pi camera even if present")
    p.add_argument("--http-port", type=int, default=8080)
    p.add_argument("--window", type=float, default=10.0, help="PERCLOS window, seconds")
    p.add_argument("--eye-every", type=float, default=2.0, help="seconds between EYE:<n> sends")
    p.add_argument("--no-eye", action="store_true", help="show eye tracking but do not send EYE scores")
    p.add_argument("--work-width", type=int, default=320, help="detection resolution (lower = faster)")
    p.add_argument("--mirror", action="store_true", help="mirror the image (selfie view)")
    p.add_argument("--capture-dir", default="captures", help="where training frames are saved")
    p.add_argument("--trips", default="logs/trips.json", help="trip summaries for the History tab")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--listen-only", action="store_true",
                   help="BLE: read ESP32 telemetry only, never write (leaves the ESP32 to its own Pi)")
    return p


def build(args) -> Pipeline:
    """Link + camera source + Pipeline, started. Used by main() and by Tokyo."""
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

    cfg = load_config()
    if args.camera_fps:
        cfg.camera.fps = args.camera_fps
    link = make_link(args.esp, port=args.port, ble_name=cfg.ble.device_name, ble_address=cfg.ble.address,
                     listen_only=args.listen_only)
    link.start()

    if args.pose == "imx500":
        from drifts.imx500_pose import DEFAULT_MODEL, Imx500PoseSource
        camera = Imx500PoseSource(args.pose_model or DEFAULT_MODEL, fps=args.pose_fps)
    else:
        if args.video:
            camera = VideoSource(args.video, fps=args.video_fps)
        else:
            camera = Camera(cfg.camera, device=args.camera, use_picamera=not args.no_picamera)
        if args.face == "mesh":
            from drifts.facemesh import FaceMeshSource
            camera = FaceMeshSource(camera=camera, model=args.face_model, ir=args.ir, yaw_sign=args.yaw_sign,
                                    glasses=args.glasses)
        elif args.face == "mediapipe":
            from drifts.face_mediapipe import DEFAULT_DMS_DIR, Pi5FaceSource
            camera = Pi5FaceSource(dms_dir=args.dms_dir or DEFAULT_DMS_DIR, profile=args.profile,
                                   camera=camera, ir=args.ir, yaw_sign=args.yaw_sign)
        elif args.pose == "sim":
            from drifts.imx500_pose import SimPoseSource
            camera = SimPoseSource(inner=camera)
    pipe = Pipeline(camera, link, args)
    pipe.start()
    return pipe


def start_whisplay(pipe: Pipeline, args):
    """The Whisplay screen, fed from /status.json's data (ESP32 link + face). Returns its stop event."""
    if not (getattr(args, "whisplay", False) or getattr(args, "whisplay_sim", False)):
        return None
    try:
        from drifts.ui_model import state_from_live
        from drifts.whisplay_ui import WhisplayUI
        ui = WhisplayUI(load_config().whisplay, sim=args.whisplay_sim,
                        hooks={"calibrate": pipe.recalibrate})  # double press: re-take the road pose
    except Exception as exc:              # no HAT or driver: the dashboard still runs
        log.warning("Whisplay HAT not available (%s): running without the screen", exc)
        return None
    stop = threading.Event()
    pipe.whisplay = True
    threading.Thread(target=ui.run_thread, args=(lambda: state_from_live(pipe.status()), stop),
                     name="whisplay", daemon=True).start()
    return stop


def serve(pipe: Pipeline, args) -> int:
    link = pipe.link
    server = ThreadingHTTPServer(("0.0.0.0", args.http_port), make_handler(pipe))
    log.info("Dashboard: http://localhost:%d   (from another device: http://<this-ip>:%d)",
             args.http_port, args.http_port)
    log.info("ESP32 link: %s   PERCLOS window %.0f s   EYE every %.0f s%s",
             args.esp, args.window, args.eye_every, "  (sending OFF)" if args.no_eye else "")
    ui_stop = start_whisplay(pipe, args)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        if ui_stop is not None:
            ui_stop.set()
        pipe.save_trip()
        pipe.stop()
        link.stop()
        server.server_close()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return serve(build(args), args)


PAGE_FILE = Path(__file__).with_name("dashboard.html")
FONT_DIR = Path(__file__).with_name("static") / "fonts"


if __name__ == "__main__":
    raise SystemExit(main())
