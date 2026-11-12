"""The drivers on this car: profiles from the phone app, and each driver's own
recording of their name (played back when the LCD's Select Driver screen
recognises them).

    Raspberry/profiles/<name>.json        guided-calibration profile (drifts/guided.py)
    Raspberry/profiles/<name>_name.wav    the driver saying their name (optional, ~1 s)
    Raspberry/profiles/.last_driver       who drove last (the default after a timeout)

The recording is made on the Pi's microphone from the phone app (Settings ->
Driver Profile -> Record my name), never leaves the Pi, and is listed on the
privacy page with a Delete button.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional

from drifts.guided import PROFILE_DIR

log = logging.getLogger(__name__)

GUEST = "Guest"
NAME_RATE = 16000          # Hz, mono 16-bit: what the recogniser and the recording use
RECORD_S = 2.0


def _profiles() -> Path:
    import drifts.drivers as me                     # looked up each call, so tests can move it
    return me.PROFILE_DIR


def clean_name(name: str) -> str:
    """Profile names are file names: letters, digits, - and _ only."""
    return re.sub(r"[^A-Za-z0-9_-]", "", name or "")[:24]


def display(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name


def list_drivers(directory: Optional[Path] = None) -> list:
    """Driver names with a saved profile, A-Z (Guest not included)."""
    directory = Path(directory) if directory is not None else _profiles()
    if not directory.exists():
        return []
    return sorted((p.stem for p in directory.glob("*.json") if not p.name.startswith(("_", "."))), key=str.lower)


def name_clip(name: str, directory: Optional[Path] = None) -> Path:
    directory = Path(directory) if directory is not None else _profiles()
    return directory / f"{clean_name(name)}_name.wav"


def has_recording(name: str, directory: Optional[Path] = None) -> bool:
    directory = Path(directory) if directory is not None else _profiles()
    return name_clip(name, directory).exists()


def recordings(directory: Optional[Path] = None) -> list:
    directory = Path(directory) if directory is not None else _profiles()
    return sorted(p.name[:-len("_name.wav")] for p in directory.glob("*_name.wav")) if directory.exists() else []


def last_driver(directory: Optional[Path] = None) -> Optional[str]:
    directory = Path(directory) if directory is not None else _profiles()
    try:
        name = (directory / ".last_driver").read_text().strip()
        return name or None
    except OSError:
        return None


def save_last_driver(name: str, directory: Optional[Path] = None) -> None:
    directory = Path(directory) if directory is not None else _profiles()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".last_driver").write_text(name)
    except OSError as exc:
        log.warning("could not save the last driver: %s", exc)


def default_driver(names: list, directory: Optional[Path] = None) -> str:
    """After a timeout: whoever drove last, if they're still on the list, else Guest."""
    directory = Path(directory) if directory is not None else _profiles()
    last = last_driver(directory)
    return last if last in names else GUEST


# ---------------------------------------------------------------- the name recording
def trim_and_level(samples, rate: int = NAME_RATE, floor: float = 0.04, pad_s: float = 0.12):
    """Cut the silence before and after the spoken name and bring the peak to ~90 %.
    `samples`: numpy int16. Returns int16 (empty if nothing was said)."""
    import numpy as np
    a = samples.astype(np.float32)
    peak = float(np.abs(a).max()) if a.size else 0.0
    if peak < 300:                                      # ~1 % of full scale: nothing said
        return np.zeros(0, np.int16)
    win = max(1, rate // 50)                            # 20 ms loudness windows
    n = a.size // win
    env = np.abs(a[:n * win]).reshape(n, win).max(axis=1)
    loud = np.where(env > floor * peak)[0]
    pad = int(pad_s * rate)
    start, end = max(0, loud[0] * win - pad), min(a.size, (loud[-1] + 1) * win + pad)
    out = a[start:end] * (0.9 * 32767 / peak)
    return out.clip(-32767, 32767).astype(np.int16)


def write_wav(path: Path, samples, rate: int = NAME_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def record_name(name: str, device: str = "default", seconds: float = RECORD_S,
                directory: Optional[Path] = None, recorder=None) -> dict:
    """Records the driver saying their name on the Pi's microphone and saves it.
    `recorder(seconds) -> int16 samples` replaces arecord in tests."""
    directory = Path(directory) if directory is not None else _profiles()
    import numpy as np
    name = clean_name(name)
    if not name:
        return {"ok": False, "why": "no name"}
    if recorder is None:
        if not shutil.which("arecord"):
            return {"ok": False, "why": "arecord is not installed (sudo apt install alsa-utils)"}

        def recorder(s):
            with tempfile.NamedTemporaryFile(suffix=".raw") as tmp:
                r = subprocess.run(["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(NAME_RATE), "-c", "1",
                                    "-t", "raw", "-d", str(max(1, round(s))), tmp.name],
                                   capture_output=True, timeout=s + 10)
                if r.returncode:
                    raise OSError(r.stderr.decode(errors="ignore").strip() or "arecord failed")
                return np.frombuffer(Path(tmp.name).read_bytes(), np.int16)
    try:
        raw = recorder(seconds)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "why": str(exc)}
    peak = float(np.abs(raw).max()) / 32768 if len(raw) else 0.0
    clip = trim_and_level(np.asarray(raw, np.int16))
    if clip.size < NAME_RATE * 0.15:
        return {"ok": False, "why": "didn't hear anything: say your name right after the beep, a little louder",
                "peak": round(peak, 3)}
    write_wav(name_clip(name, directory), clip)
    log.info("Recorded %s's name (%.1f s, peak %.2f)", name, clip.size / NAME_RATE, peak)
    return {"ok": True, "seconds": round(clip.size / NAME_RATE, 2), "peak": round(peak, 3)}


def delete_name(name: str, directory: Optional[Path] = None) -> bool:
    directory = Path(directory) if directory is not None else _profiles()
    p = name_clip(name, directory)
    if p.exists():
        p.unlink()
        return True
    return False
