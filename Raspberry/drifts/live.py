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
            "window_s": self.args.window, "time": time.time(),
        }


def make_handler(pipe: Pipeline):
    page = PAGE.encode()

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
                self._send(200, page, "text/html; charset=utf-8")
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


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DRIFTS Live</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--line:#2a2f3a;--text:#e7e9ee;--dim:#9aa3b2;
--ok:#3ccf5a;--warn:#f2c94c;--bad:#ff5a5a;--accent:#7aa2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--line)}
header h1{font-size:18px;margin:0;letter-spacing:.5px}header .link{margin-left:auto;color:var(--dim);font-size:13px}
main{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,1fr);gap:14px;padding:14px 16px}
@media(max-width:860px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin:0 0 8px}
img#feed{width:100%;border-radius:8px;background:#000;display:block;aspect-ratio:4/3;object-fit:contain}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.m{background:#11141a;border-radius:8px;padding:8px}.m .v{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums}
.m .k{font-size:12px;color:var(--dim)}
.level{font-size:20px;font-weight:700;padding:10px;border-radius:8px;text-align:center;margin-top:8px}
.lcd{font:16px/1.3 ui-monospace,Consolas,monospace;background:#1d3a8a;color:#dfe8ff;border-radius:6px;
padding:8px 10px;white-space:pre;margin-top:8px;display:inline-block}
.side{display:flex;flex-direction:column;gap:14px}
button{background:#232838;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 12px;
font-size:14px;cursor:pointer}button:hover{border-color:var(--accent)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.log{font:12px/1.35 ui-monospace,Consolas,monospace;color:var(--dim);max-height:150px;overflow:auto;white-space:pre-wrap}
.dim{color:var(--dim);font-size:13px}.pill{padding:2px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
</style></head><body>
<header><h1>DRIFTS · Live</h1><span class="pill" id="fw">fw --</span><span class="link" id="link">connecting…</span></header>
<main>
 <section class="card"><h2>Driver camera</h2><img id="feed" src="/stream.mjpg" alt="live camera feed">
  <div class="grid" style="margin-top:10px">
   <div class="m"><div class="k">Eyes</div><div class="v" id="eyes">--</div></div>
   <div class="m"><div class="k">PERCLOS (<span id="win">10</span> s)</div><div class="v" id="perclos">--</div></div>
   <div class="m"><div class="k">Eye score → ESP32</div><div class="v" id="eyescore">--</div></div>
  </div>
  <div class="row dim" style="margin-top:8px"><span id="fps">-- fps</span>·<span id="sent">0 sent</span>
   <label style="margin-left:auto"><input type="checkbox" id="send" checked> send EYE score</label></div>
 </section>
 <div class="side">
  <section class="card"><h2>Vehicle (OBD)</h2>
   <div class="grid">
    <div class="m"><div class="k">RPM</div><div class="v" id="rpm">--</div></div>
    <div class="m"><div class="k">Speed km/h</div><div class="v" id="spd">--</div></div>
    <div class="m"><div class="k">Throttle %</div><div class="v" id="thr">--</div></div>
   </div><div class="dim" style="margin-top:6px">dongle: <span id="obd">--</span> · <span id="hz">--</span> Hz</div>
  </section>
  <section class="card"><h2>Alertness</h2>
   <div class="grid" style="grid-template-columns:1fr 1fr">
    <div class="m"><div class="k">Alert score</div><div class="v" id="score">--</div></div>
    <div class="m"><div class="k">ESP32 eye input</div><div class="v" id="espeye">--</div></div>
   </div>
   <div class="level" id="level">--</div>
   <div id="lcdwrap" style="display:none"><div class="dim" style="margin-top:8px">LCD on the ESP32</div><div class="lcd" id="lcd"></div></div>
  </section>
  <section class="card"><h2>Training capture</h2>
   <div class="row"><button onclick="cap('open')">Save OPEN (O)</button><button onclick="cap('closed')">Save CLOSED (C)</button>
    <button onclick="cap('drowsy')">Save DROWSY (D)</button></div>
   <div class="row" style="margin-top:8px"><span class="dim">Burst 10:</span><button onclick="cap('open',10)">open ×10</button>
    <button onclick="cap('closed',10)">closed ×10</button></div>
   <div class="dim" style="margin-top:8px" id="capinfo">--</div>
  </section>
  <section class="card"><h2>ESP32 log</h2><div class="log" id="log"></div></section>
 </div>
</main>
<script>
const $=id=>document.getElementById(id);
const LV={OK:["Drive Safe","var(--ok)","#0f2a16"],CAUTION:["INTERVENTION 1 · Signs of Fatigue","var(--warn)","#2e2710"],
LOW:["INTERVENTION 2 · Worsening Signs","var(--bad)","#341414"]};
const f=(v,d=0)=>v==null?"--":Number(v).toFixed(d);
async function cap(label,n=1){await fetch(`/capture?label=${label}&n=${n}`,{method:"POST"})}
$("send").onchange=e=>fetch(`/eye_send?on=${e.target.checked?1:0}`,{method:"POST"});
document.addEventListener("keydown",e=>{const k=e.key.toLowerCase();
 if(k==="o")cap("open");if(k==="c")cap("closed");if(k==="d")cap("drowsy")});
async function tick(){try{const s=await (await fetch("/status.json")).json();const e=s.eye,p=s.esp;
 $("eyes").textContent={open:"OPEN",closed:"CLOSED",no_face:"NO FACE"}[e.state];
 $("eyes").style.color={open:"var(--ok)",closed:"var(--bad)",no_face:"var(--warn)"}[e.state];
 $("perclos").textContent=e.perclos==null?"--":Math.round(e.perclos*100)+"%";
 $("eyescore").textContent=e.warming?"…":f(e.score);$("win").textContent=s.window_s;
 $("fps").textContent=f(e.fps,1)+" fps";$("sent").textContent=s.eyes_sent+" sent";$("send").checked=s.eye_sending;
 $("link").textContent="ESP32: "+p.link;$("fw").textContent="fw "+(p.fw||"--");
 $("rpm").textContent=f(p.rpm);$("spd").textContent=f(p.spd);$("thr").textContent=f(p.thr);
 $("obd").textContent=p.obd||"--";$("hz").textContent=f(p.hz,1);
 $("score").textContent=f(p.score)+(p.part?"*":"");$("espeye").textContent=f(p.eye);
 const lv=LV[p.lvl];if(lv){$("level").textContent=lv[0];$("level").style.color=lv[1];$("level").style.background=lv[2]}
 if(p.alert){$("level").textContent+=" · ALERT ACTIVE"}
 if(p.lcd1!=null){$("lcdwrap").style.display="";$("lcd").textContent=p.lcd1+"\n"+p.lcd2}else{$("lcdwrap").style.display="none"}
 const c=s.capture.counts;$("capinfo").textContent=`open ${c.open} · closed ${c.closed} · drowsy ${c.drowsy}`+
  (s.capture.pending?` · saving ${s.capture.pending}…`:"")+` → ${s.capture.dir}`;
 $("log").textContent=s.log.join("\n");}catch(err){$("link").textContent="dashboard offline"}}
setInterval(tick,500);tick();
</script></body></html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
