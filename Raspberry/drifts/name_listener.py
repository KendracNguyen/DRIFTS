"""Hears which driver is speaking their name (the LCD's Select Driver screen).

Offline, with Vosk's small English model (Apache 2.0, ~40 MB, installed by
deploy/install.sh into Raspberry/models/vosk-small-en). The recogniser is
limited to a grammar of the driver names plus "guest", so it only has to tell
a handful of words apart: far more reliable than open speech-to-text, and it
runs on the Pi Zero 2 W. Nothing heard is saved.

    nl = NameListener()
    nl.available()                        # vosk + model present?
    nl.listen(["kendra", "trevor"], 15)   # -> ("kendra", 0.93) or (None, 0.0)
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from drifts.drivers import GUEST, NAME_RATE

log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "vosk-small-en"
MIN_CONF = 0.5


def grammar(names: list) -> list:
    words = sorted({n.lower() for n in names} | {GUEST.lower()})
    return words + ["[unk]"]


def match(result: dict, names: list) -> tuple:
    """The one driver name in a Vosk result (with SetWords), or (None, 0.0).
    Extra unknown words are ignored; two different names count as no answer."""
    wanted = {n.lower(): n for n in list(names) + [GUEST]}
    hits = [(w.get("word", ""), float(w.get("conf", 0.0))) for w in result.get("result") or []]
    if not hits and result.get("text"):
        hits = [(t, 1.0) for t in result["text"].split()]
    found = {wanted[w]: c for w, c in hits if w in wanted}
    if len(found) != 1:
        return None, 0.0
    name, conf = next(iter(found.items()))
    return (name, conf) if conf >= MIN_CONF else (None, conf)


class NameListener:
    def __init__(self, model_dir: Path = MODEL_DIR, device: str = "default"):
        self.model_dir, self.device = Path(model_dir), device
        self._model = None
        self.why: Optional[str] = None            # why it isn't available

    def available(self) -> bool:
        if self._model is not None:
            return True
        try:
            from vosk import Model, SetLogLevel
        except ImportError:
            self.why = "vosk is not installed (WHISPLAY=1 bash deploy/install.sh)"
            return False
        if not (self.model_dir / "am").exists():
            self.why = f"no speech model in {self.model_dir} (WHISPLAY=1 bash deploy/install.sh)"
            return False
        if not shutil.which("arecord"):
            self.why = "arecord is not installed"
            return False
        SetLogLevel(-1)
        self._model = Model(str(self.model_dir))
        return True

    def unknown_words(self, names: list) -> list:
        """Names the model can't recognise (not in its vocabulary)."""
        if not self.available():
            return []
        return [n for n in names if not self._knows(n)]

    def _knows(self, word: str) -> bool:
        """Is the word in the model's vocabulary (else the grammar would drop it)?"""
        find = getattr(self._model, "vosk_model_find_word", None) or getattr(self._model, "find_word", None)
        return True if find is None else find(word.lower()) >= 0

    def listen(self, names: list, timeout: float, stop: Optional[threading.Event] = None) -> tuple:
        """Listens until a driver name is heard (or timeout / stop). -> (name, confidence)."""
        if not self.available():
            return None, 0.0
        from vosk import KaldiRecognizer
        known = [n for n in names if self._knows(n)]
        rec = KaldiRecognizer(self._model, NAME_RATE, json.dumps(grammar(known)))
        rec.SetWords(True)
        proc = subprocess.Popen(["arecord", "-q", "-D", self.device, "-f", "S16_LE", "-r", str(NAME_RATE),
                                 "-c", "1", "-t", "raw"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < timeout and not (stop and stop.is_set()):
                data = proc.stdout.read(NAME_RATE // 5 * 2)        # 0.2 s of audio
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    name, conf = match(json.loads(rec.Result()), names)
                    if name:
                        return name, conf
            name, conf = match(json.loads(rec.FinalResult()), names)
            return name, conf
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
