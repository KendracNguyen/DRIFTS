"""Tokyo app: camera + face (phase 2) -> alert score + outputs + dashboard (phase 4).

    python -m tokyo run                       # Pi 5: NoIR camera, ESP32 over BLE (listen only)
    python -m tokyo run --esp sim             # no ESP32
    python -m tokyo run --camera 0 --mirror   # laptop webcam (outputs simulated off the Pi 5)
    python -m tokyo run --video clip.mp4      # recorded driver video

Data flow (one Pi 5; OBD stays with the first model):

  camera -> dms-face (MediaPipe) -> eye score = 100 x (1 - PERCLOS) ┐
                                                                    ├-> 0.7 eye + 0.3 driving
  first model's ESP32 --BLE telemetry (listen only)--> driving score┘        │
                                                                             v
          level (hysteresis, hold) -> buzzer / motors / LED strip / LCD + web dashboard

Tokyo never writes to the ESP32 (no EYE, no PING), so the first model's own
Pi Zero stays the one that steers it. The dashboard is the shared one from
Raspberry/drifts, showing Tokyo's score and outputs.

Every drifts.live option works here too (python -m tokyo run --help).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "Raspberry"))     # shared code: drifts.live, face pipeline, dashboard

from tokyo.config import load_config            # noqa: E402
from tokyo.interventions import Interventions   # noqa: E402
from tokyo.lcd import Lcd, compose              # noqa: E402
from tokyo.ledstrip import LedStrip, StripState  # noqa: E402
from tokyo.outputs import Buzzer, Motors        # noqa: E402
from tokyo.score import LEVEL_NAMES, LocalScore  # noqa: E402


def on_pi5() -> bool:
    try:
        return "Raspberry Pi 5" in Path("/proc/device-tree/model").read_bytes().decode("utf-8", "replace")
    except OSError:
        return False


class TokyoOutputs:
    """Per-frame hook for drifts.live: score -> level -> Tokyo's own outputs."""

    def __init__(self, cfg, sim: bool):
        self.cfg, self.sim = cfg, sim
        self.buzzer, self.motors = Buzzer(cfg.buzzer, sim), Motors(cfg.motors, sim)
        self.iv = Interventions(self.buzzer, self.motors, cfg.buzzer, cfg.motors, cfg.interventions)
        self.strip = LedStrip(cfg.strip, sim)
        self.lcd = Lcd(cfg.lcd.bus, cfg.lcd.addresses, sim=sim) if cfg.lcd.enabled else None
        self.score = LocalScore()
        self._t0 = None
        self._lcd_t = 0.0

    def __call__(self, pipe, now: float) -> None:
        if self._t0 is None:
            self._t0 = now
        cam = pipe.camera
        face = getattr(cam, "face", None) or {}
        eye = cam.eye_score() if hasattr(cam, "eye_score") else pipe.eye.get("score")
        drv = pipe.link.snapshot().get("drv_score")
        r = self.score.update(now, eye=eye, driving=drv)
        drowsy = face.get("state") == "drowsy"        # dms-face fused DROWSY (incl. microsleep)
        lvl = self.iv.tick(now, r.score, alarm=drowsy)

        self.strip.update(StripState(score=r.score, level=lvl, alarm=lvl >= 2, alarm_since=self.iv.levels.since,
                                     links_ok=bool(face.get("found")) and not r.no_driving,
                                     system_issue=bool(face) and not face.get("profile_loaded", True)), now)
        lines = compose(now - self._t0, r.score, lvl, alarm=lvl >= 2, partial=r.partial)
        if self.lcd is not None and now - self._lcd_t >= self.cfg.lcd.refresh_s:
            self._lcd_t = now
            self.lcd.show(*lines)

        pipe.esp_override = {"score": r.score, "lvl": LEVEL_NAMES[lvl] if r.score is not None else None,
                             "eye": r.eye, "part": r.partial, "alert": lvl >= 2,
                             "lcd1": lines[0], "lcd2": lines[1]}
        pipe.extra_status["outputs"] = {
            "level": lvl, "buzzer": self.buzzer.on, "motors": self.motors.on, "drowsy": drowsy,
            "strip_amps": round(self.strip.amps, 2), "lcd": bool(self.lcd and self.lcd.present),
            "driving": r.driving, "no_driving": r.no_driving, "sim": self.sim,
            "source": "Tokyo Pi 5: 0.7 x camera eye + 0.3 x ESP32 driving"}

    def stop(self) -> None:
        self.iv.stop()
        self.strip.off()


def run(argv=None) -> int:
    from drifts import live

    p = live.build_parser(prog="python -m tokyo run")
    p.add_argument("--outputs", choices=["auto", "real", "sim"], default="auto",
                   help="auto = real GPIO on a Raspberry Pi 5, simulated elsewhere")
    p.add_argument("--config", help="tokyo.toml")
    # Tokyo defaults: all-signal face pipeline, ESP32 read-only over BLE, no EYE sending.
    # 30 fps: blink durations (100-400 ms) need it; the first model's Zero runs at 10.
    p.set_defaults(face="mediapipe", esp="ble", listen_only=True, no_eye=True, camera_fps=30,
                   rig_name="Tokyo · Raspberry Pi 5")
    args = p.parse_args(argv)
    if args.esp != "ble":
        args.listen_only = False
    sim = args.outputs == "sim" or (args.outputs == "auto" and not on_pi5())

    outputs = TokyoOutputs(load_config(args.config), sim)
    pipe = live.build(args)
    pipe.hooks.append(outputs)
    print(f"[tokyo] outputs: {'SIMULATED' if sim else 'GPIO'}  ·  score: 0.7 eye + 0.3 ESP32 driving  ·  "
          f"ESP32: {args.esp}{' (listen only)' if args.listen_only else ''}", flush=True)
    try:
        return live.serve(pipe, args)
    finally:
        outputs.stop()
