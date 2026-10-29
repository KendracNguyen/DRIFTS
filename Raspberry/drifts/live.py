"""DRIFTS live dashboard: camera feed with eye tracking, ESP32 telemetry, and
training-frame capture, served as a web page.

    # Laptop + webcam + ESP32 on USB (no Pi needed):
    python -m drifts.live --esp serial --port COM5
    # Raspberry Pi in the car (Pi camera, ESP32 over BLE):
    sudo systemctl stop drifts && python -m drifts.live --esp ble
    # No hardware at all:
    python -m drifts.live --esp sim

Then open http://localhost:8080 (or http://<pi-address>:8080 from a phone on
the same network).

What it does, once per camera frame:
  1. eyes.EyeTracker finds the face and eyes -> open / closed / no face
  2. PerclosWindow -> PERCLOS over the last N seconds -> eye score 0-100
  3. every --eye-every seconds the eye score is sent to the ESP32 as EYE:<n>,
     so the ESP32's alert score, LEDs and LCD react to the camera live
The ESP32 link supplies OBD (rpm / speed / throttle), the alert score and
level, and on USB the LCD text, all shown next to the video.

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
from drifts.eyes import CLOSED, NO_FACE, OPEN, EyeTracker, PerclosWindow, eye_score

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
        self.tracker = EyeTracker(work_width=args.work_width)
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
        # 1 Hz session history for the History tab: (t, alert score, eye score, rpm)
        self.history: list[tuple] = []

    def start(self) -> None:
        threading.Thread(target=self._run, name="camera", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

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
        import cv2
        self.camera.start()
        last_send = 0.0
        t_fps, n_fps = time.time(), 0
        try:
            while not self._stop.is_set():
                frame = self.camera.grab()
                if frame is None:
                    time.sleep(0.05)
                    continue
                if self.args.mirror:
                    frame = cv2.flip(frame, 1)
                now = time.time()
                obs = self.tracker.process(frame)
                self.window.add(now, obs.state)
                perclos = self.window.perclos()
                score = eye_score(perclos)
                closed_for = self.window.closed_for(now)
                warming = self.window.coverage(now) < min(3.0, self.args.window)

                if self.send_eye and score is not None and not warming and now - last_send >= self.args.eye_every:
                    last_send = now
                    self.link.send_eye(score)
                    self.eye["last_sent"] = score

                with self._lock:
                    burst = self._burst.pop(0) if self._burst else None
                if burst:
                    self.capture.save(frame, burst, perclos)

                jpeg = cv2.imencode(".jpg", self._annotate(frame, obs, perclos, score, closed_for),
                                    [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
                if not self.history or now - self.history[-1][0] >= 1.0:
                    esp = self.link.snapshot()
                    self.history.append((round(now, 1), esp["score"], score, esp["rpm"]))
                    del self.history[:-3600]          # keep the last hour
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

    def status(self) -> dict:
        with self._lock:
            eye = dict(self.eye)
        return {
            "eye": eye, "eye_sending": self.send_eye, "eyes_sent": self.link.eyes_sent,
            "esp": self.link.snapshot(), "log": list(self.link.log_lines)[-12:],
            "capture": {"session": self.capture.session, "counts": self.capture.counts,
                        "dir": str(self.capture.root.resolve()), "pending": len(self._burst)},
            "window_s": self.args.window, "time": time.time(), "started": self.started,
        }


def make_handler(pipe: Pipeline):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):   # keep the console for the app's own log
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
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
                                            "eye": [r[2] for r in h], "rpm": [r[3] for r in h]}).encode(),
                           "application/json")
            elif path == "/status.json":
                self._send(200, json.dumps(pipe.status()).encode(), "application/json")
            elif path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
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
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/capture" and q.get("label", [""])[0] in LABELS:
                pipe.request_capture(q["label"][0], int(q.get("n", ["1"])[0]))
                self._send(200, b"ok", "text/plain")
            elif u.path == "/beep":
                pipe.link.send_command("BEEP")      # ESP32 firmware v15+: one 1 s beep
                self._send(200, b"ok", "text/plain")
            elif u.path == "/eye_send":
                pipe.send_eye = q.get("on", ["1"])[0] == "1"
                self._send(200, b"ok", "text/plain")
            else:
                self._send(400, b"bad request", "text/plain")

    return Handler


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m drifts.live", description="DRIFTS live dashboard")
    p.add_argument("--esp", choices=["serial", "ble", "sim"], default="sim",
                   help="ESP32 link: serial (USB), ble (Pi in the car), or sim (no hardware)")
    p.add_argument("--port", default="COM5", help="serial port for --esp serial (COM5, /dev/ttyACM0)")
    p.add_argument("--camera", type=int, default=0, help="webcam index (laptop)")
    p.add_argument("--video", help="play a video file or a folder of images instead of a camera (loops)")
    p.add_argument("--video-fps", type=float, default=8.0, help="playback rate for --video")
    p.add_argument("--no-picamera", action="store_true", help="skip the Pi camera even if present")
    p.add_argument("--http-port", type=int, default=8080)
    p.add_argument("--window", type=float, default=10.0, help="PERCLOS window, seconds")
    p.add_argument("--eye-every", type=float, default=2.0, help="seconds between EYE:<n> sends")
    p.add_argument("--no-eye", action="store_true", help="show eye tracking but do not send EYE scores")
    p.add_argument("--work-width", type=int, default=320, help="detection resolution (lower = faster)")
    p.add_argument("--mirror", action="store_true", help="mirror the image (selfie view)")
    p.add_argument("--capture-dir", default="captures", help="where training frames are saved")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

    cfg = load_config()
    link = make_link(args.esp, port=args.port, ble_name=cfg.ble.device_name, ble_address=cfg.ble.address)
    link.start()

    if args.video:
        camera = VideoSource(args.video, fps=args.video_fps)
    else:
        camera = Camera(cfg.camera, device=args.camera, use_picamera=not args.no_picamera)
    pipe = Pipeline(camera, link, args)
    pipe.start()

    server = ThreadingHTTPServer(("0.0.0.0", args.http_port), make_handler(pipe))
    log.info("Dashboard: http://localhost:%d   (from another device: http://<this-ip>:%d)",
             args.http_port, args.http_port)
    log.info("ESP32 link: %s   PERCLOS window %.0f s   EYE every %.0f s%s",
             args.esp, args.window, args.eye_every, "  (sending OFF)" if args.no_eye else "")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        pipe.stop()
        link.stop()
        server.server_close()
    return 0


PAGE_FILE = Path(__file__).with_name("dashboard.html")


if __name__ == "__main__":
    raise SystemExit(main())
