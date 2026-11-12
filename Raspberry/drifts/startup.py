"""The LCD's start-up after the welcome screen: who's driving, then a face check
and a calibration with the camera on the screen.

    select   "Who's driving? Say your name."  The drivers from the phone app, and
             Guest. Saying a name picks it (drifts/name_listener.py). Backup: a
             short press moves down the list, a long press picks. No answer in
             select_timeout_s: whoever drove last (or Guest).
    hello    "Hi," and the driver's own recording of their name (drifts/drivers.py),
             or "Hi. Driving as a guest." Their profile is loaded.
    face     The camera on the LCD, mirrored, with an oval: "Move left", "Come
             closer"... until the face sits in the oval for a moment.
    calib    "Look at the road": the road pose is taken (Quick calibration).
    done     Normal screens (the score).

The camera picture is only shown on the LCD here, never saved. Everything the
flow needs comes in as functions (hooks), so it is tested without hardware.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from drifts.drivers import GUEST, default_driver, display, has_recording, name_clip, save_last_driver

log = logging.getLogger(__name__)

HELLO_S = 2.6            # the "Hi, Kendra" screen
FACE_OK_S = 1.2          # face in the oval this long -> calibrate
FACE_GIVE_UP_S = 30.0    # no usable face: carry on (the road pose is taken automatically later)
CALIB_MAX_S = 15.0
PREVIEW_FPS = 6.0


def face_hint(landmarks, frame_w: int, frame_h: int) -> tuple:
    """Where the face is in the (mirrored) LCD preview: (ok, hint).
    The preview shows the frame mirrored, so a face drawn on the left needs
    the driver to move to their right, like a mirror."""
    if not landmarks:
        return False, "Look at the camera"
    xs = [frame_w - p[0] for p in landmarks]               # mirrored, as on the LCD
    ys = [p[1] for p in landmarks]
    cx = (min(xs) + max(xs)) / 2 / frame_w
    cy = (min(ys) + max(ys)) / 2 / frame_h
    size = (max(ys) - min(ys)) / frame_h
    if size < 0.22:
        return False, "Come closer"
    if size > 0.80:
        return False, "Move back a little"
    if cx < 0.36:
        return False, "Move right"
    if cx > 0.64:
        return False, "Move left"
    if cy < 0.34:
        return False, "Move down a little"
    if cy > 0.66:
        return False, "Move up a little"
    return True, "Face in frame"


class StartupFlow:
    def __init__(self, names: Callable[[], list], choose: Callable[[str], None], speaker=None,
                 listener=None, frame: Optional[Callable[[], tuple]] = None,
                 calibrate: Optional[Callable[[], None]] = None, timeout_s: float = 15.0,
                 voice: bool = True, face_check: bool = True, profile_dir: Optional[Path] = None):
        self.names_fn, self.choose_fn, self.speaker, self.listener = names, choose, speaker, listener
        self.frame_fn, self.calibrate_fn = frame, calibrate
        self.timeout_s, self.voice, self.face_check = timeout_s, voice, face_check
        self.dir_kw = {"directory": profile_dir} if profile_dir else {}
        self.phase = "off"
        self.names: list = []
        self.highlight = 0
        self.chosen: Optional[str] = None
        self.how: Optional[str] = None
        self.deadline = 0.0
        self.until = 0.0
        self.status = ""
        self._listen_thread: Optional[threading.Thread] = None
        self._heard: Optional[tuple] = None
        self._stop = threading.Event()
        self._face_ok_since: Optional[float] = None
        self._face_since = 0.0
        self.face: dict = {}
        self._last_preview = 0.0
        self._manual = False                     # the driver has used the button on the list

    @property
    def active(self) -> bool:
        return self.phase not in ("off", "done")

    # ------------------------------------------------------------ select
    def begin(self, now: float) -> None:
        drivers = [n for n in self.names_fn() if n.lower() != GUEST.lower()]
        self.names = drivers + [GUEST]
        default = default_driver(drivers, **self.dir_kw)
        self.highlight = self.names.index(default) if default in self.names else len(self.names) - 1
        self.phase, self.deadline = "select", now + self.timeout_s
        self.status = "Say your name" if self.listener is not None else "Press to choose"
        if self.voice and self.speaker is not None:
            self.speaker.play("WHO", now)
        log.info("[UI] Select driver: %s (default %s)", ", ".join(self.names), self.names[self.highlight])

    def _start_listening(self) -> None:
        if self.listener is None or self._listen_thread is not None:
            return
        names = list(self.names)

        def run():
            try:
                self._heard = self.listener.listen(names, self.timeout_s, self._stop)
            except Exception as exc:                 # the listener must never stop the start-up
                log.warning("[UI] name listener failed: %s", exc)
                self._heard = (None, 0.0)
        self._listen_thread = threading.Thread(target=run, name="name-listener", daemon=True)
        self._listen_thread.start()
        self.status = "Listening..."

    def choose(self, name: str, how: str, now: float) -> None:
        self._stop.set()
        self.chosen, self.how = name, how
        self.highlight = self.names.index(name) if name in self.names else self.highlight
        self.status = f"Hi, {display(name)}!" if name != GUEST else "Driving as a guest"
        log.info("[UI] Driver: %s (%s)", name, how)
        if name != GUEST:
            save_last_driver(name, **self.dir_kw)
        try:
            self.choose_fn(name)
        except Exception as exc:
            log.warning("[UI] could not load %s's profile: %s", name, exc)
        if self.voice and self.speaker is not None:
            self._greet(name, now)
        self.phase, self.until = "hello", now + HELLO_S

    def _greet(self, name: str, now: float) -> None:
        if name == GUEST:
            self.speaker.play("GUEST", now)
            return
        clip = name_clip(name, **self.dir_kw)
        if has_recording(name, **self.dir_kw):
            self.speaker.play_files(["HI", clip], now)             # "Hi," + their own voice
            return
        spoken = _espeak(display(name))                             # no recording: a synthetic voice
        self.speaker.play_files(["HI"] + ([spoken] if spoken else []), now)

    # ------------------------------------------------------------ the button
    def button(self, event: str, now: float) -> bool:
        """True if the start-up used the press."""
        if self.phase != "select":
            return False
        if event in ("short", "double"):
            self.highlight = (self.highlight + 1) % len(self.names)
            self.deadline = now + self.timeout_s                       # choosing by hand: give time
            self._manual = True
            self.status = "Hold to choose"
            return True
        if event == "long":
            self.choose(self.names[self.highlight], "button", now)
            return True
        return False

    # ------------------------------------------------------------ each tick
    def tick(self, st, now: float) -> None:
        if self.phase == "select":
            speaking = self.speaker is not None and self.speaker.playing(now)
            if not speaking:
                self._start_listening()
            if self._heard is not None:
                name, conf = self._heard
                self._heard = None
                if name:
                    self.choose(name, f"voice {conf:.2f}", now)
                    return
                self._listen_thread = None                         # nothing usable: listen again
            if now >= self.deadline:
                pick = self.names[self.highlight] if self._manual else default_driver(self.names[:-1], **self.dir_kw)
                self.choose(pick, "timeout", now)
        elif self.phase == "hello" and now >= self.until:
            if self.face_check and self.frame_fn is not None:
                self.phase, self._face_since, self._face_ok_since = "face", now, None
                if self.voice and self.speaker is not None:
                    self.speaker.play("FACE", now)
            else:
                self._calibrate(now)
        elif self.phase == "face":
            self._update_preview(now, check=True)
            if self.face.get("ok"):
                self._face_ok_since = self._face_ok_since or now
                if now - self._face_ok_since >= FACE_OK_S:
                    self._calibrate(now)
            else:
                self._face_ok_since = None
            if now - self._face_since >= FACE_GIVE_UP_S:
                log.info("[UI] No usable face after %.0f s: carrying on", FACE_GIVE_UP_S)
                self.phase = "done"
        elif self.phase == "calib":
            self._update_preview(now, check=False)
            self.face["progress"] = st.calib_progress or 0.0
            started = now - self.until >= 0.8                       # the status needs a moment to show it
            if (started and not st.calibrating) or now - self.until >= CALIB_MAX_S:
                self.phase = "done"

    def _calibrate(self, now: float) -> None:
        if self.calibrate_fn is None:
            self.phase = "done"
            return
        self.calibrate_fn()
        self.phase, self.until = "calib", now
        self.face["hint"] = "Look at the road"

    def _update_preview(self, now: float, check: bool) -> None:
        if now - self._last_preview < 1.0 / PREVIEW_FPS:
            return
        self._last_preview = now
        got = self.frame_fn() if self.frame_fn else None
        frame, landmarks = got if got else (None, None)
        if frame is None:
            self.face.update(img=None, ok=False, hint="Starting the camera")
            return
        h, w = frame.shape[:2]
        if check:
            ok, hint = face_hint(landmarks, w, h)
            self.face.update(ok=ok, hint=hint)
        self.face["img"] = frame

    # ------------------------------------------------------------ for the screen
    def view(self) -> Optional[str]:
        return {"select": "select", "hello": "select", "face": "face", "calib": "face"}.get(self.phase)

    def context(self) -> dict:
        return {"select": {"names": [display(n) for n in self.names], "highlight": self.highlight,
                           "chosen": display(self.chosen) if self.chosen else None, "status": self.status},
                "face": {k: v for k, v in self.face.items()} | {"calibrating": self.phase == "calib"}}

    def stop(self) -> None:
        self._stop.set()


def _espeak(text: str) -> Optional[Path]:
    """A name spoken by espeak-ng (drivers who haven't recorded theirs), or None."""
    if not shutil.which("espeak-ng"):
        return None
    out = Path(tempfile.gettempdir()) / f"drifts_name_{abs(hash(text))}.wav"
    try:
        subprocess.run(["espeak-ng", "-s", "150", "-w", str(out), text], check=True, timeout=10,
                       capture_output=True)
        return out
    except (OSError, subprocess.SubprocessError):
        return None
