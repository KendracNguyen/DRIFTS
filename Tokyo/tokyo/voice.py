"""Spoken alerts on Tokyo (Pi 5), with the same phrases and rules as the
first model's ESP32 + DFPlayer (Esp32/drifts_esp32/voice.h).

Clips come from Voice/phrases.json, generated once with Piper by
Voice/make_clips.py (Voice/clips/NN_NAME.wav), and play through `aplay` on
a USB audio adapter -> PAM8403 amplifier -> 3 W speaker. Pre-generated clips
start instantly and cost no CPU while driving.

What is said, and when:
  READY        once, a few seconds after start
  LEVEL1       entering Intervention 1;  LEVEL2  entering Intervention 2 (or the
               DROWSY alarm), repeated every repeat_s while it lasts
  RECOVERED    back to OK
  CALIBRATED   the road-pose calibration finishes
  OBD_LOST     the ESP32's driving data stops (it was there before)
  CAMERA_LOST  no new camera frames for link_lost_s (there were before)
One clip at a time; a waiting clip only gives way to a more urgent one.
While a clip plays during an alert, the buzzer holds off (Interventions.mute_buzzer).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tokyo.config import VoiceConfig

REPO = Path(__file__).resolve().parents[2]
PHRASES = REPO / "Voice" / "phrases.json"
READY_DELAY_S = 3.0


def load_phrases(path: Path = PHRASES) -> dict:
    """name -> {"id", "text"}"""
    data = json.loads(Path(path).read_text())
    return {p["name"]: {"id": p["id"], "text": p["text"]} for p in data["phrases"]}


PRIORITY = {"LEVEL2": 4, "LEVEL1": 3}


def priority(name: Optional[str]) -> int:
    return 0 if name is None else PRIORITY.get(name, 2)


@dataclass
class VoiceInputs:
    level: int = 0                     # 0 OK, 1, 2 (alarm already folded in)
    driving_ok: Optional[bool] = None  # None = unknown
    camera_ok: Optional[bool] = None
    calibrated: Optional[bool] = None


class _Watch:
    def __init__(self):
        self.seen = self.announced = False
        self.down_since: Optional[float] = None

    def update(self, ok: Optional[bool], now: float, after_s: float) -> bool:
        if ok is None:
            return False
        if ok:
            self.seen, self.announced, self.down_since = True, False, None
            return False
        if not self.seen or self.announced:
            return False
        if self.down_since is None:
            self.down_since = now
        elif now - self.down_since >= after_s:
            self.announced = True
            return True
        return False


class VoiceDirector:
    """Pure decision logic (unit-tested): inputs + time -> clip name to play now."""

    def __init__(self, repeat_s: float = 60.0, link_lost_s: float = 5.0):
        self.repeat_s, self.link_lost_s = repeat_s, link_lost_s
        self.t0: Optional[float] = None
        self.ready_done = False
        self.level = 0
        self.last_level2 = 0.0
        self.pending: Optional[str] = None
        self._drv, self._cam = _Watch(), _Watch()
        self._calibrated: Optional[bool] = None

    def queue(self, name: str) -> None:
        if priority(name) >= priority(self.pending):
            self.pending = name

    def update(self, inp: VoiceInputs, now: float, playing: bool) -> Optional[str]:
        if self.t0 is None:
            self.t0 = now
        if not self.ready_done and now - self.t0 >= READY_DELAY_S:
            self.ready_done = True
            self.queue("READY")
        lvl = inp.level
        if lvl != self.level:
            if lvl >= 2:
                self.queue("LEVEL2")
                self.last_level2 = now
            elif lvl == 1 and self.level == 0:
                self.queue("LEVEL1")
            elif lvl == 0:
                if self.pending in ("LEVEL1", "LEVEL2"):
                    self.pending = None
                self.queue("RECOVERED")
            self.level = lvl
        elif lvl >= 2 and now - self.last_level2 >= self.repeat_s:
            self.queue("LEVEL2")
            self.last_level2 = now
        if inp.calibrated is not None:
            if self._calibrated is False and inp.calibrated:
                self.queue("CALIBRATED")
            self._calibrated = inp.calibrated
        if self._drv.update(inp.driving_ok, now, self.link_lost_s):
            self.queue("OBD_LOST")
        if self._cam.update(inp.camera_ok, now, self.link_lost_s):
            self.queue("CAMERA_LOST")
        if playing or self.pending is None:
            return None
        name, self.pending = self.pending, None
        return name


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


class Voice:
    """Plays the clips. sim=True records instead of playing."""

    def __init__(self, cfg: VoiceConfig, sim: bool = False):
        self.cfg, self.sim = cfg, sim
        self.phrases = load_phrases()
        self.dir = VoiceDirector(cfg.repeat_s, cfg.link_lost_s)
        clips = Path(cfg.clips_dir) if cfg.clips_dir else REPO / "Voice" / "clips"
        self.clips = {n: clips / f"{p['id']:02d}_{n}.wav" for n, p in self.phrases.items()}
        self.lengths = {n: wav_seconds(f) for n, f in self.clips.items() if f.exists()}
        self.player_ok = sim or bool(shutil.which(cfg.player))
        self.played: list = []           # (time, name)
        self._proc = None
        self._until = 0.0
        self.last: Optional[str] = None

    @property
    def missing(self) -> list:
        return [n for n in self.clips if n not in self.lengths]

    def playing(self, now: float) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        return now < self._until

    def say(self, name_or_id) -> bool:
        """Queue a clip by name ("LEVEL2") or id (4), like SAY:<n> on the ESP32."""
        name = name_or_id
        if isinstance(name_or_id, int) or str(name_or_id).isdigit():
            ids = {p["id"]: n for n, p in self.phrases.items()}
            name = ids.get(int(name_or_id))
        if name not in self.phrases:
            return False
        self.dir.queue(name)
        return True

    def _play(self, name: str, now: float) -> float:
        length = self.lengths.get(name, 2.0)
        self.played.append((now, name))
        self.last = name
        self._until = now + length + 0.25
        if self.sim or not self.cfg.enabled or not self.player_ok or name not in self.lengths:
            return length
        cmd = [self.cfg.player, "-q"] + (["-D", self.cfg.device] if self.cfg.device else []) + [str(self.clips[name])]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self._proc = None
        return length

    def update(self, inp: VoiceInputs, now: float) -> Optional[tuple]:
        """Returns (name, seconds) when a clip starts, else None."""
        if not self.cfg.enabled:
            return None
        name = self.dir.update(inp, now, self.playing(now))
        if name is None:
            return None
        return name, self._play(name, now)

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
