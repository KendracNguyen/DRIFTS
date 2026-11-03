"""Pose sources for head-pose tracking: the Raspberry Pi AI Camera (IMX500), and
a simulated source for laptop development.

Imx500PoseSource runs the stock HigherHRNet COCO pose network ON THE CAMERA
(the Pi only decodes 17 keypoints per person), exactly like Raspberry Pi's
picamera2-examples/examples/imx500/imx500_pose_estimation_higherhrnet_demo.py.
Each grab() returns the camera frame and sets `.keypoints` to the driver's
17 (x, y, score) points in frame pixels, or None.

    # On the Pi with the AI Camera (sudo apt install imx500-all; pip install munkres):
    python -m drifts.imx500_pose --soak 3600     # 1-hour stability test (picamera2 #1404)
    python -m drifts.imx500_pose --print-pose    # yaw / pitch / roll once a second

See Docs/ai-camera-stage2.md.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time
from typing import Optional

DEFAULT_MODEL = "/usr/share/imx500-models/imx500_network_higherhrnet_coco.rpk"
FACE = (0, 1, 2, 3, 4)          # COCO: nose, left eye, right eye, left ear, right ear


def pick_driver(people, min_conf: float = 0.3):
    """The person whose face is largest in the frame (closest to the camera)."""
    best, best_size = None, 0.0
    for kp in people:
        pts = [(float(kp[i][0]), float(kp[i][1])) for i in FACE if float(kp[i][2]) >= min_conf]
        if len(pts) < 2:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        size = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if size > best_size:
            best, best_size = kp, size
    return best


class Imx500PoseSource:
    """Raspberry Pi AI Camera running HigherHRNet; camera-like grab()/stop()."""

    def __init__(self, model: str = DEFAULT_MODEL, fps: int = 15, size=(640, 480), threshold: float = 0.3):
        self.model, self.fps, self.size, self.threshold = model, fps, size, threshold
        self.keypoints = None           # driver's 17 x (x, y, score), frame pixels
        self.people = 0
        self.inference_ms: Optional[float] = None
        self.frames = 0
        self._last_out = 0.0

    def start(self) -> None:
        # Imports here: these exist only on a Pi with the AI Camera packages.
        import numpy as np
        from picamera2 import Picamera2
        from picamera2.devices.imx500 import IMX500
        from picamera2.devices.imx500.postprocess_highernet import postprocess_higherhrnet

        self._np, self._post, self._IMX500 = np, postprocess_higherhrnet, IMX500
        if not os.path.exists(self.model):
            raise FileNotFoundError(f"{self.model} not found: sudo apt install imx500-all")
        self.imx500 = IMX500(self.model)            # must come before Picamera2()
        self.picam2 = Picamera2(self.imx500.camera_num)
        # RGB888 arrives in B,G,R byte order: what OpenCV expects.
        cfg = self.picam2.create_preview_configuration(
            main={"size": self.size, "format": "RGB888"},
            controls={"FrameRate": self.fps}, buffer_count=12)
        print("[AI Camera] Loading the network onto the sensor (minutes the first time)...", flush=True)
        self.imx500.show_network_fw_progress_bar()
        self.picam2.start(cfg)
        self.imx500.set_auto_aspect_ratio()

    def grab(self):
        req = self.picam2.capture_request()
        try:
            frame = req.make_array("main")
            md = req.get_metadata()
        finally:
            req.release()
        self.frames += 1
        outputs = self.imx500.get_outputs(md, add_batch=True)
        kpi = self._IMX500.get_kpi_info(md)
        if kpi:
            self.inference_ms = kpi[0]
        if outputs is not None:
            w, h = self.size
            kps, scores, _boxes = self._post(outputs=outputs, img_size=(h, w), img_w_pad=(0, 0),
                                             img_h_pad=(0, 0), detection_threshold=self.threshold,
                                             network_postprocess=True)
            self._last_out = time.time()
            if scores is not None and len(scores) > 0:
                people = self._np.reshape(self._np.stack(kps, axis=0), (len(scores), 17, 3))
                self.people = len(people)
                self.keypoints = pick_driver(people, self.threshold)
            else:
                self.people, self.keypoints = 0, None
        elif time.time() - self._last_out > 0.5:
            self.keypoints = None        # no fresh inference: do not show stale points
        return frame

    def stop(self) -> None:
        try:
            self.picam2.stop()
            self.picam2.close()
        except Exception:
            pass


# ---------------------------------------------------------------- simulated
def synthetic_keypoints(yaw_right=0.0, pitch_down=0.0, roll_right=0.0, scale=1.6, cx=320, cy=200):
    """17 COCO keypoints for a model head at these angles (same model as the tests)."""
    model = {"nose": (0.0, 40.0, 35.0), "l_eye": (31.5, 0.0, 0.0), "r_eye": (-31.5, 0.0, 0.0),
             "l_ear": (75.0, 10.0, -90.0), "r_ear": (-75.0, 10.0, -90.0)}
    a, p, q = math.radians(-yaw_right), math.radians(pitch_down), math.radians(-roll_right)
    out = []
    for name in ("nose", "l_eye", "r_eye", "l_ear", "r_ear"):
        x, y, z = model[name]
        x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
        y, z = y * math.cos(p) + z * math.sin(p), -y * math.sin(p) + z * math.cos(p)
        x, y = x * math.cos(q) - y * math.sin(q), x * math.sin(q) + y * math.cos(q)
        # An ear turned well away from the camera is hidden.
        hidden = (name == "r_ear" and yaw_right > 35) or (name == "l_ear" and yaw_right < -35)
        out.append((cx + scale * x, cy + scale * y, 0.0 if hidden else 0.9))
    return out + [(0.0, 0.0, 0.0)] * 12


# A 30 s script: road, mirror check, phone glance down, head tilt, long look away.
SIM_SCRIPT = [  # (until_s, yaw_right, pitch_down, roll_right)
    (6, 0, 0, 0), (8, 35, 0, 0), (11, 0, 0, 0), (14, -15, 28, 0),
    (17, 0, 0, 0), (20, 0, 0, 18), (23, 0, 0, 0), (27, 45, 5, 0), (30, 0, 0, 0)]


class SimPoseSource:
    """Wraps another frame source (or a blank frame) and adds scripted keypoints.

    The head eases between script poses so the dashboard shows realistic motion.
    """

    def __init__(self, inner=None, fps: float = 10.0):
        self.inner, self.period = inner, 1.0 / fps
        self.keypoints = None
        self.people = 1
        self.inference_ms = None
        self._t0 = None
        self._pose = [0.0, 0.0, 0.0]

    def start(self) -> None:
        if self.inner is not None:
            self.inner.start()
        self._t0 = time.time()

    def _target(self, t: float):
        t = t % SIM_SCRIPT[-1][0]
        for until, y, p, r in SIM_SCRIPT:
            if t < until:
                return [y, p, r]
        return [0, 0, 0]

    def grab(self):
        import numpy as np
        if self.inner is not None:
            frame = self.inner.grab()
        else:
            time.sleep(self.period)
            frame = np.full((480, 640, 3), 28, dtype=np.uint8)
        tgt = self._target(time.time() - self._t0)
        self._pose = [c + 0.35 * (g - c) for c, g in zip(self._pose, tgt)]     # ease
        h, w = (frame.shape[0], frame.shape[1]) if frame is not None else (480, 640)
        self.keypoints = synthetic_keypoints(*self._pose, scale=w / 400, cx=w / 2, cy=h * 0.42)
        return frame

    def stop(self) -> None:
        if self.inner is not None:
            self.inner.stop()


# ---------------------------------------------------------------- CLI
def _soak(src: Imx500PoseSource, seconds: float, print_pose: bool) -> int:
    """Stream for `seconds`; exit 2 if frames stop (picamera2 issue #1404)."""
    from drifts.headpose import Calibration, estimate

    last = {"t": time.time(), "n": 0}
    stop = threading.Event()

    def watchdog():
        while not stop.wait(1.0):
            if time.time() - last["t"] > 10.0:
                print(f"\n[SOAK] HANG: no frame for {time.time() - last['t']:.0f} s after "
                      f"{src.frames} frames ({time.time() - t0:.0f} s). This matches picamera2 "
                      "issue #1404: note the power supply and `uname -r`.", flush=True)
                os._exit(2)

    t0 = time.time()
    threading.Thread(target=watchdog, daemon=True).start()
    cal, prev, seen, last_print = Calibration(), None, 0, t0
    while time.time() - t0 < seconds:
        src.grab()
        last["t"] = time.time()
        if src.keypoints is not None:
            seen += 1
            raw = estimate(src.keypoints, prev=prev)
            prev = raw
            cal.add(raw)
            pose = cal.apply(raw)
        else:
            pose = None
        if time.time() - last_print >= (1.0 if print_pose else 30.0):
            el = time.time() - t0
            msg = (f"[SOAK] {el:6.0f} s  frames {src.frames}  {src.frames / el:5.1f} fps  "
                   f"driver seen {100 * seen / max(1, src.frames):3.0f} %  "
                   f"inference {src.inference_ms if src.inference_ms is not None else '--'} ms")
            if print_pose and pose is not None and pose.found:
                msg += (f"  | yaw {pose.yaw:+5.1f}  pitch {pose.pitch:+5.1f}  roll {pose.roll:+5.1f}"
                         + ("" if cal.done else "  (calibrating: look at the road)"))
            print(msg, flush=True)
            last_print = time.time()
    stop.set()
    print(f"[SOAK] PASS: {src.frames} frames in {time.time() - t0:.0f} s, no hang.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m drifts.imx500_pose", description=__doc__.split("\n")[0])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--soak", type=float, default=0, help="stream for N seconds, watching for hangs")
    ap.add_argument("--print-pose", action="store_true", help="print yaw/pitch/roll every second")
    args = ap.parse_args(argv)
    src = Imx500PoseSource(args.model, fps=args.fps)
    src.start()
    try:
        return _soak(src, args.soak or (10 ** 9 if args.print_pose else 60), args.print_pose)
    except KeyboardInterrupt:
        return 0
    finally:
        src.stop()


if __name__ == "__main__":
    sys.exit(main())
