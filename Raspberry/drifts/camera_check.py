"""Camera bring-up check: the Raspberry Pi Camera Module 3 NoIR on the Pi Zero 2 W.

    python -m drifts.camera_check                 # software checks + a 20 s live test in the terminal
    python -m drifts.camera_check --lcd           # ... with the live picture on the Whisplay LCD, to aim it
    python -m drifts.camera_check --seconds 60    # longer, e.g. while you try the IR boards
    python -m drifts.camera_check --video ../Simuletic_DMS_Dataset/images --lcd-sim --out shots
                                                  # anywhere: sample images, the LCD as PNGs

Part 1 (software): the camera stack, the camera itself (model, NoIR), the
face mesh and its model, and that nothing else holds the camera.

Part 2 (live): frames for --seconds, through the face mesh, printing once a
second: frame rate, face found, mesh time, light level, day / IR. With
--lcd the Whisplay screen shows the picture (mirrored, like a mirror) with
the face outline, for aiming the camera in the car. Run it with the drifts
service stopped (it holds the camera and the screen).

Nothing is saved except the text report, unless --snapshot is given: then
one frame is written to --out (the only picture this tool ever saves).

Each check is PASS, WARN (works, but look at it) or FAIL (fix before use).
"""

from __future__ import annotations

import argparse
import grp
import importlib
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from drifts.config import load_config
from drifts.whisplay_check import FAIL, PASS, SKIP, WARN, Report, _read

MESH_OK_FPS, MESH_MIN_FPS = 5.0, 2.0      # loop rate with the face mesh: good / usable
DARK, BLACK = 25, 8                       # mean grey level: dim (turn the IR on) / black


# ---------------------------------------------------------------- part 1: software
def camera_list() -> Optional[list]:
    """libcamera's cameras as [{'Model': ...}], or None if picamera2 is missing."""
    try:
        from picamera2 import Picamera2
    except ImportError:
        return None
    try:
        return list(Picamera2.global_camera_info())
    except Exception:
        return []


def describe(model: str) -> str:
    m = (model or "").lower()
    name = {"imx708": "Camera Module 3", "imx219": "Camera Module 2", "ov5647": "Camera Module 1 / OV5647",
            "imx500": "AI Camera", "imx477": "HQ Camera"}.get(m.split("_")[0], model or "unknown")
    if "noir" in m:
        name += " NoIR"
    if "wide" in m:
        name += " Wide"
    return name


def software_checks(rep: Report, use_mesh: bool = True, cams: Optional[list] = "probe") -> None:
    model = _read("/proc/device-tree/model")
    rep.add("Board", PASS if "Raspberry Pi" in model else WARN, model or "not a Raspberry Pi")
    arch = platform.machine()
    rep.add("64-bit OS", PASS if arch in ("aarch64", "arm64", "x86_64", "AMD64") else FAIL,
            arch + ("" if arch in ("aarch64", "arm64", "x86_64", "AMD64") else
                    ": MediaPipe needs 64-bit Raspberry Pi OS; reflash with the 64-bit Lite image"))
    if cams == "probe":
        cams = camera_list()
    if cams is None:
        rep.add("picamera2", FAIL, "sudo apt install python3-picamera2 (the venv sees it: --system-site-packages)")
    else:
        rep.add("picamera2", PASS)
        if cams == [{"Model": "video"}]:
            rep.add("Camera detected", SKIP, "--video: sample images instead of the camera")
        elif not cams:
            rep.add("Camera detected", FAIL, "none: Pi off, re-seat the ribbon (the Zero needs the narrow 22-pin "
                    "Zero cable, contacts towards the board at both ends), then rpicam-hello --list-cameras")
        else:
            m = cams[0].get("Model", "")
            ok = m.lower().startswith("imx708")
            rep.add("Camera detected", PASS if ok else WARN,
                    f"{describe(m)} ({m})" + ("" if ok else ": DRIFTS expects the Camera Module 3 NoIR"))
            if ok:
                rep.add("NoIR (no IR filter)", PASS if "noir" in m.lower() else WARN,
                        "sees the 850 nm IR boards" if "noir" in m.lower() else
                        "this one has an IR filter: the IR boards won't light the face at night")
    boot = _read("/boot/firmware/config.txt") or _read("/boot/config.txt")
    if boot:
        auto = "camera_auto_detect=1" in boot or "camera_auto_detect" not in boot
        rep.add("camera_auto_detect", PASS if auto else WARN,
                "on" if auto else "set camera_auto_detect=1 in /boot/firmware/config.txt (or dtoverlay=imx708), reboot")
    try:
        names = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except (KeyError, OSError):
        names = set()
    if names:
        rep.add("User in group video", PASS if "video" in names else WARN,
                "" if "video" in names else "sudo usermod -aG video $USER, then log out and in")
    if shutil.which("systemctl"):
        try:
            busy = subprocess.run(["systemctl", "is-active", "--quiet", "drifts"], timeout=3,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.SubprocessError):
            busy = False
        rep.add("Camera free", WARN if busy else PASS,
                "the drifts service is running and may hold the camera: sudo systemctl stop drifts" if busy else
                "the drifts service is stopped")
    if use_mesh:
        try:
            importlib.import_module("mediapipe")
            rep.add("MediaPipe", PASS)
        except ImportError:
            rep.add("MediaPipe", FAIL, "MESH=1 bash deploy/install.sh (or run with --no-mesh)")
        from drifts.facemesh import DEFAULT_MODEL
        rep.add("Face model", PASS if DEFAULT_MODEL.exists() else WARN,
                str(DEFAULT_MODEL.name) if DEFAULT_MODEL.exists() else "downloads on first use (needs internet once)")


# ---------------------------------------------------------------- the LCD aim view
def lcd_frame(frame, face, stats: dict):
    """240 x 280: the picture (mirrored), the face outline, and the numbers."""
    from PIL import Image
    from drifts.face_mediapipe import EYE_OUTLINES, FACE_OVAL
    from drifts.ui_model import RGB
    from drifts.whisplay_ui import Canvas, S, W, header_bar
    c = Canvas()
    header_bar(c, "Camera check")
    top, ph = 30, 196                                     # preview area: 240 x 196
    if frame is not None:
        h, w = frame.shape[:2]
        px = getattr(face, "landmarks", None) if face is not None else None
        cx = sum(p[0] for p in px) / len(px) if px else w / 2
        cy = sum(p[1] for p in px) / len(px) if px else h / 2
        cw = min(w, int(h * W / ph))
        ch = int(cw * ph / W)
        x0 = int(min(max(cx - cw / 2, 0), w - cw))
        y0 = int(min(max(cy - ch / 2, 0), h - ch))
        crop = frame[y0:y0 + ch, x0:x0 + cw, ::-1]          # BGR -> RGB
        img = Image.fromarray(crop.copy()).resize((W * S, ph * S)).transpose(Image.FLIP_LEFT_RIGHT)
        c.img.paste(img, (0, top * S))
        if px:
            sx, sy = W / cw, ph / ch

            def pt(i):
                return (W - (px[i][0] - x0) * sx, top + (px[i][1] - y0) * sy)
            closed = (face.face or {}).get("eye_state") in ("closed", "closing")
            c.line([pt(i) for i in FACE_OVAL + FACE_OVAL[:1]], fill=(235, 235, 240), width=1.2)
            for e in EYE_OUTLINES:
                c.line([pt(i) for i in e + e[:1]], fill=RGB["red"] if closed else RGB["green"], width=1.4)
    else:
        c.text(120, top + ph / 2, "No picture", 18, 700, RGB["lcd_muted"], "mm")
    fps, found = stats.get("fps"), stats.get("face")
    ok = RGB["green"] if found else RGB["yellow"]
    c.text(12, 242, f"{fps:.1f} fps" if fps is not None else "-- fps", 15, 700, RGB["lcd_text"], "lm")
    c.text(228, 242, "Face found" if found else "No face", 15, 700, ok, "rm")
    ms = stats.get("mesh_ms")
    light = stats.get("light")
    c.text(12, 264, f"Mesh {ms:.0f} ms" if ms else "Mesh --", 12, 500, RGB["lcd_muted"], "lm")
    c.text(228, 264, (f"Light {light:.0f}" if light is not None else "Light --") +
           (" · IR" if stats.get("night") else " · day"), 12, 500, RGB["lcd_muted"], "rm")
    return c.done()


# ---------------------------------------------------------------- part 2: live
def open_board(sim: bool):
    from drifts.whisplay_ui import HatBoard, SimBoard
    if sim:
        return SimBoard()
    cfg = load_config().whisplay
    b = HatBoard(cfg.driver_dir, cfg.rotate, cfg.joystick)
    b.set_backlight(max(30, cfg.brightness))
    return b


def live_checks(rep: Report, source, face, seconds: float, board=None, out: Optional[Path] = None,
                snapshot: bool = False, quiet: bool = False) -> dict:
    """Frames for `seconds` through the face mesh. Returns the totals."""
    import cv2
    import numpy as np
    from drifts.night import mean_saturation
    try:
        source.start()
    except Exception as exc:
        rep.add("Camera opens", FAIL, str(exc))
        return {}
    model = getattr(source, "model", None)
    rep.add("Camera opens", PASS, model or type(source).__name__)
    n = found = 0
    lights: list = []
    mesh_ms: list = []
    night_frames = 0
    last = None
    t0 = time.monotonic()
    t_sec, n_sec = t0, 0
    stats: dict = {}
    frames_seen = 0
    try:
        while time.monotonic() - t0 < seconds:
            frame = source.grab()
            if frame is None:
                time.sleep(0.02)
                continue
            frames_seen += 1
            now = time.monotonic()
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
            light = float(np.mean(grey[::4, ::4]))
            lights.append(light)
            f = {}
            if face is not None:
                f = face.process(frame, now)
                if face.inference_ms:
                    mesh_ms.append(face.inference_ms)
            if f.get("found"):
                found += 1
            sat = mean_saturation(frame)
            is_night = bool(f.get("night")) if face is not None else (sat is not None and sat < 0.08)
            night_frames += is_night
            n += 1
            n_sec += 1
            last = frame
            if now - t_sec >= 1.0:
                stats = {"fps": n_sec / (now - t_sec), "face": bool(f.get("found")),
                         "mesh_ms": face.inference_ms if face is not None else None,
                         "light": light, "night": is_night}
                if not quiet:
                    print(f"        {now - t0:4.0f} s  {stats['fps']:4.1f} fps  face {'yes' if stats['face'] else 'no ':<3}"
                          f"  mesh {stats['mesh_ms'] or 0:4.0f} ms  light {light:5.1f}"
                          f"  {'IR / night' if is_night else 'day'}", flush=True)
                t_sec, n_sec = now, 0
            if board is not None:
                board.show(lcd_frame(frame, face, stats or {"light": light}))
    finally:
        meta = {}
        cam = getattr(source, "_picam2", None)
        if cam is not None:
            try:
                meta = cam.capture_metadata() or {}
            except Exception:
                meta = {}
        try:
            source.stop()
        except Exception:
            pass
    elapsed = max(1e-6, time.monotonic() - t0)
    fps = n / elapsed
    res = {"frames": n, "fps": fps, "found": found / n if n else 0.0,
           "light": sum(lights) / len(lights) if lights else None,
           "mesh_ms": sorted(mesh_ms)[len(mesh_ms) // 2] if mesh_ms else None,
           "night": night_frames / n if n else 0.0, "meta": meta}
    if not n:
        rep.add("Frames", FAIL, "no frames: another program holds the camera, or the ribbon is loose")
        return res
    if last is not None:
        res["size"] = f"{last.shape[1]} x {last.shape[0]}"
    rate = (PASS if fps >= MESH_OK_FPS else WARN if fps >= MESH_MIN_FPS else FAIL)
    rep.add("Frame rate" + (" (with face mesh)" if face is not None else ""), rate,
            f"{fps:.1f} fps, {res.get('size', '')}" + ("" if rate == PASS else
            ": slow; try --work-width 240 in drifts.live, or a lower [camera] fps"))
    lt = res["light"]
    rep.add("Picture", PASS if lt >= DARK else WARN if lt >= BLACK else FAIL,
            f"mean level {lt:.0f} of 255" + ("" if lt >= DARK else
            ": dim: at night turn the IR boards on" if lt >= BLACK else
            ": black: lens covered, or night without the IR boards on"))
    rep.add("Day / IR", PASS, f"{'IR / night (grey picture)' if res['night'] > 0.5 else 'day (colour)'}"
            f" in {res['night'] * 100:.0f} % of frames")
    if face is not None:
        fr = res["found"]
        rep.add("Face found", PASS if fr >= 0.8 else WARN if fr >= 0.3 else FAIL,
                f"{fr * 100:.0f} % of frames" + ("" if fr >= 0.8 else
                ": aim at the face: 50-70 cm away, about eye level, no more than ~30 degrees off to the side"))
        if res["mesh_ms"]:
            rep.add("Face mesh time", PASS if res["mesh_ms"] < 150 else WARN, f"{res['mesh_ms']:.0f} ms a frame")
    if "LensPosition" in meta:
        pos = meta["LensPosition"]
        rep.add("Focus", PASS, f"lens {pos:.2f} dioptres (~{100 / pos:.0f} cm)" if pos > 0 else "infinity")
    elif cam is not None:
        rep.add("Focus", SKIP, "no autofocus on this camera (fixed focus)")
    if "ExposureTime" in meta:
        rep.add("Exposure", PASS, f"{meta['ExposureTime'] / 1000:.1f} ms, gain {meta.get('AnalogueGain', 0):.1f}"
                + (": long exposure means dim light: motion blur on blinks" if meta["ExposureTime"] > 40000 else ""))
    if snapshot and last is not None and out is not None:
        out.mkdir(parents=True, exist_ok=True)
        path = out / "camera_check_snapshot.jpg"
        cv2.imwrite(str(path), last)
        rep.add("Snapshot", PASS, f"{path} (delete it when done)")
    if board is not None and last is not None:
        board.show(lcd_frame(last, face, stats or {}))
        res["lcd"] = getattr(board, "frame", None)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m drifts.camera_check", description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=20.0, help="length of the live test")
    ap.add_argument("--lcd", action="store_true", help="show the picture on the Whisplay LCD (to aim the camera)")
    ap.add_argument("--lcd-sim", action="store_true", help="the LCD view without the HAT (saved as a PNG in --out)")
    ap.add_argument("--no-mesh", action="store_true", help="skip the face mesh (camera only)")
    ap.add_argument("--no-live", action="store_true", help="software checks only")
    ap.add_argument("--video", help="a video file or image folder instead of the camera (testing)")
    ap.add_argument("--snapshot", action="store_true", help="save one frame to --out (otherwise nothing is saved)")
    ap.add_argument("--out", default=".", help="folder for the report")
    args = ap.parse_args(argv)
    out = Path(args.out)
    rep = Report()
    print("DRIFTS camera check\n\n Part 1: software")
    software_checks(rep, use_mesh=not args.no_mesh, cams=[{"Model": "video"}] if args.video else "probe")
    res: dict = {}
    if not args.no_live:
        print(f"\n Part 2: live ({args.seconds:.0f} s)" + ("  -- look at the LCD" if args.lcd else ""))
        cfg = load_config()
        if args.video:
            from drifts.live import VideoSource
            source = VideoSource(args.video, fps=cfg.camera.fps)
        else:
            from drifts.camera import Camera
            source = Camera(cfg.camera)
        face = None
        if not args.no_mesh:
            try:
                from drifts.facemesh import FaceMeshSource
                face = FaceMeshSource(camera=None)
                face.start()
            except Exception as exc:
                rep.add("Face mesh starts", FAIL, str(exc))
                face = None
        board = None
        if args.lcd or args.lcd_sim:
            try:
                board = open_board(args.lcd_sim)
            except Exception as exc:
                rep.add("Whisplay LCD", WARN, f"not available ({exc}): carrying on without it")
        try:
            res = live_checks(rep, source, face, args.seconds, board, out, args.snapshot)
        finally:
            if board is not None and not args.lcd_sim:
                time.sleep(2)
                board.close()
            if face is not None and getattr(face, "_lm", None) is not None:
                try:
                    face._lm.close()               # release MediaPipe before the interpreter exits
                except Exception:
                    pass
                face._lm = None
        if args.lcd_sim and res.get("lcd") is not None:
            out.mkdir(parents=True, exist_ok=True)
            res["lcd"].save(out / "camera_check_lcd.png")
    path = out / "camera_check_report.txt"
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(rep.text().replace("Whisplay check", "camera check"))
    print(f"\n {rep.failed} failed. Report: {path}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
