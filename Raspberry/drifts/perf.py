"""Board and performance figures for comparing the two demo rigs.

Reads Linux files only (no psutil): board model, CPU use since the last call,
SoC temperature, and memory. Paths can be overridden for tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class PerfMonitor:
    def __init__(self, proc_stat="/proc/stat", thermal="/sys/class/thermal/thermal_zone0/temp",
                 model="/proc/device-tree/model", meminfo="/proc/meminfo"):
        self.proc_stat, self.thermal, self.model_path, self.meminfo = map(Path, (proc_stat, thermal, model, meminfo))
        self._last: Optional[tuple] = None
        self.model = self._read_model()

    def _read_model(self) -> str:
        try:
            return self.model_path.read_bytes().rstrip(b"\x00").decode("utf-8", "replace").strip()
        except OSError:
            import platform
            return f"{platform.system()} {platform.machine()}"

    def cpu_percent(self) -> Optional[float]:
        """Whole-board CPU use (all cores, 0-100) since the previous call."""
        try:
            fields = self.proc_stat.read_text().splitlines()[0].split()[1:]
        except (OSError, IndexError):
            return None
        vals = [int(v) for v in fields]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)       # idle + iowait
        total = sum(vals)
        prev, self._last = self._last, (idle, total)
        if prev is None or total == prev[1]:
            return None
        return max(0.0, min(100.0, 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))))

    def temperature_c(self) -> Optional[float]:
        try:
            return int(self.thermal.read_text().strip()) / 1000.0
        except (OSError, ValueError):
            return None

    def mem_used_mb(self) -> Optional[float]:
        try:
            info = {ln.split(":")[0]: int(ln.split()[1]) for ln in self.meminfo.read_text().splitlines() if ":" in ln}
            return (info["MemTotal"] - info["MemAvailable"]) / 1024.0
        except (OSError, KeyError, ValueError, IndexError):
            return None

    def snapshot(self) -> dict:
        return {"model": self.model, "cpu": self.cpu_percent(), "temp_c": self.temperature_c(),
                "mem_mb": self.mem_used_mb()}


# The eight driver signals, and what each rig can deliver.
# live = measured; proxy = a stand-in (e.g. head direction for gaze);
# rough = measured but unreliable; none = not possible on this hardware.
SIGNALS = [
    ("off_road_gaze", "Off-road gaze"), ("yawn", "Yawn"),
    ("yaw", "Head yaw"), ("pitch", "Head pitch"), ("roll", "Head roll"),
    ("mouth", "Mouth aperture"), ("ear", "Eye openness (EAR)"), ("blink", "Blinking"),
]

CAPABILITY = {
    # Pi Zero 2 W + AI Camera: 5 face keypoints from the on-sensor pose network.
    "imx500": {"off_road_gaze": "proxy", "yaw": "live", "roll": "live", "pitch": "rough",
               "yawn": "none", "mouth": "none", "ear": "none", "blink": "none"},
    # Pi 5 + OV5647 day/night: 478 MediaPipe landmarks incl. iris (dms-face + mouth.py).
    "mediapipe": {k: "live" for k, _ in SIGNALS},
    # Development: scripted head, Haar eye estimate.
    "sim": {"off_road_gaze": "proxy", "yaw": "live", "roll": "live", "pitch": "rough",
            "yawn": "none", "mouth": "none", "ear": "none", "blink": "none"},
    "off": {k: "none" for k, _ in SIGNALS},
}


def capability(backend: str) -> dict:
    cap = CAPABILITY.get(backend, CAPABILITY["off"])
    return {"signals": [{"key": k, "label": label, "level": cap[k]} for k, label in SIGNALS],
            "live": sum(1 for k, _ in SIGNALS if cap[k] == "live"),
            "usable": sum(1 for k, _ in SIGNALS if cap[k] in ("live", "proxy", "rough")),
            "total": len(SIGNALS)}
