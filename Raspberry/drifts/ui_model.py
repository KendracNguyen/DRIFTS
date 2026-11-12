"""The DRIFTS state every driver-facing screen shows, and the shared rules for
showing it: alertness bands, labels and colours.

One model, several renderers: the Whisplay LCD (drifts/whisplay_ui.py) now,
the phone app next. Neither screen scores anything itself: the alert score
and its level come from the ESP32 (Esp32/drifts_esp32/alertness.h), and the
face measurements from the camera pipeline (drifts/live.py).

Bands follow the ESP32's level, so the screen always agrees with the buzzer
and motors:

    score >= 70          normal    green   ALERTNESS NORMAL
    50 <= score < 70     caution   yellow  CAUTION            (Intervention 1)
    score < 50 or alert  high      red     HIGH FATIGUE       (Intervention 2)
    no score / stale     unavailable grey  NO SCORE

The ESP32 adds 5 points of hysteresis, so a score just above 70 can still be
"caution" while it recovers. Without a level (old firmware), the score alone
decides.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Optional

# Same numbers as AlertConfig in Esp32/drifts_esp32/alertness.h.
OK_ABOVE = 70.0
LOW_BELOW = 50.0

NORMAL, CAUTION, HIGH, UNAVAILABLE = "normal", "caution", "high", "unavailable"
LEVEL_OF = {"OK": 0, "CAUTION": 1, "LOW": 2}
BAND_OF_TIER = {0: NORMAL, 1: CAUTION, 2: HIGH}

# ------------------------------------------------------------------ design tokens
def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


COLORS = {
    "burgundy": "#86172C", "burgundy_deep": "#650E20", "burgundy_accent": "#A51E39",
    "button": "#B51F3B", "blush": "#FFF4F5", "blush_surface": "#FBE8EC", "card": "#FFFFFF",
    "lcd_bg": "#0B0B0D", "lcd_surface": "#18181B", "lcd_line": "#2A2A2F",
    "text": "#191919", "text_muted": "#66666B", "divider": "#E9DDE0",
    "lcd_text": "#FFFFFF", "lcd_muted": "#A1A1AA", "track": "#3A3A40",
    "green": "#39D353", "yellow": "#FFC83D", "red": "#FF5964", "grey": "#8E8E96",
}
RGB = {k: _hex(v) for k, v in COLORS.items()}

BANDS = {
    NORMAL: {"color": "green", "title": "ALERTNESS NORMAL", "subtitle": "High alertness", "icon": "check"},
    CAUTION: {"color": "yellow", "title": "CAUTION", "subtitle": "Elevated fatigue detected", "icon": "warning"},
    HIGH: {"color": "red", "title": "HIGH FATIGUE", "subtitle": "Pull over safely", "icon": "alert"},
    UNAVAILABLE: {"color": "grey", "title": "NO SCORE", "subtitle": "Waiting for the ESP32", "icon": "dash"},
}


def band_for(score: Optional[float], level: Optional[str] = None, alert: bool = False) -> str:
    """The alertness band for a score (and the ESP32's level, when known)."""
    if score is None:
        return UNAVAILABLE
    if alert:
        return HIGH
    if level in LEVEL_OF:
        return BAND_OF_TIER[LEVEL_OF[level]]
    return NORMAL if score >= OK_ABOVE else CAUTION if score >= LOW_BELOW else HIGH


def band_color(band: str) -> tuple:
    return RGB[BANDS[band]["color"]]


# ------------------------------------------------------------------ state
@dataclass
class UiState:
    """Everything a driver-facing screen needs. None = not measured (shown as --)."""
    # From the ESP32 (1 Hz BLE status JSON)
    score: Optional[float] = None       # alert score 0-100 (100 = alert)
    level: int = 0                      # 0 OK, 1 Intervention 1, 2 Intervention 2
    alert: bool = False                 # drowsy alert running on the ESP32
    partial: bool = False               # eye data missing: driving only
    driving: Optional[float] = None     # driving score 0-100
    obd: Optional[str] = None           # "polling" when the dongle is live
    rpm: Optional[float] = None
    speed: Optional[float] = None       # km/h
    throttle: Optional[float] = None    # %
    esp_linked: bool = False            # link to the ESP32 up and status fresh
    fw: Optional[str] = None
    # From the camera pipeline (drifts.live); None in the car app, which has no live face data
    vision: bool = False                # a camera pipeline is feeding this state
    camera_ok: Optional[bool] = None
    face_found: Optional[bool] = None
    eyes: Optional[str] = None          # "open" / "closed"
    perclos: Optional[float] = None     # 0-1
    blink_rate: Optional[float] = None  # per minute
    yaw: Optional[float] = None         # degrees from the road pose, + = driver's right
    pitch: Optional[float] = None       # degrees, + = looking down
    calibrated: Optional[bool] = None
    calib_progress: Optional[float] = None   # 0-1
    # Calibration the driver started in the phone app (the LCD shows it; the
    # automatic road-pose pass at start-up does not take over the screen).
    calibrating: Optional[str] = None   # "quick", "profile" (9 poses) or "test" (accuracy test)
    calib_prompt: Optional[str] = None  # the current pose, e.g. "Close your eyes"
    calib_step: Optional[int] = None    # 1-based
    calib_total: Optional[int] = None
    # Labelling
    demo: bool = False                  # example or simulated data: the screen says DEMO
    t: float = 0.0                      # wall-clock time of this state

    @property
    def obd_ok(self) -> bool:
        return self.obd == "polling"

    @property
    def tier(self) -> int:
        return 2 if self.alert else self.level

    @property
    def band(self) -> str:
        if self.score is None:
            return UNAVAILABLE
        return BAND_OF_TIER[self.tier]

    @property
    def outputs_on(self) -> bool:
        """The ESP32's buzzer and motors run at Intervention 2 and during the drowsy alert."""
        return self.score is not None and self.tier >= 2

    @property
    def pose_label(self) -> Optional[str]:
        if self.yaw is None or self.pitch is None:
            return None
        if abs(self.yaw) <= 15 and abs(self.pitch) <= 12:
            return "Neutral"
        if abs(self.yaw) > abs(self.pitch):
            return "Turned right" if self.yaw > 0 else "Turned left"
        return "Looking down" if self.pitch > 0 else "Looking up"

    def to_dict(self) -> dict:
        """The normalized state, as the phone app will read it."""
        d = asdict(self)
        return {
            "timestamp": self.t, "demo": self.demo,
            "alertness": {"score": self.score, "state": self.band, "valid": self.score is not None,
                          "partial": self.partial, "level": self.tier},
            "vision": {"eyes": self.eyes, "headPose": self.pose_label, "blinkRatePerMinute": self.blink_rate,
                       "perclos": self.perclos, "yaw": self.yaw, "pitch": self.pitch,
                       "calibrated": self.calibrated, "calibProgress": self.calib_progress},
            "vehicle": {"speedKmh": self.speed, "rpm": self.rpm, "throttlePercent": self.throttle,
                        "drivingScore": self.driving, "obd": self.obd},
            "interventions": {"audioActive": self.outputs_on, "vibrationActive": self.outputs_on},
            "devices": {"esp32": "online" if self.esp_linked else "offline",
                        "obd": "online" if self.obd_ok else "offline",
                        "camera": None if self.camera_ok is None else ("online" if self.camera_ok else "offline")},
            "raw": d,
        }


def state_from_status(status: dict, linked: bool, fresh: bool = True, **extra) -> UiState:
    """UiState from esp_link.parse_phone_json() output (the car app's BLE status)."""
    lvl = status.get("lvl")
    return UiState(score=status.get("score") if fresh else None, level=LEVEL_OF.get(lvl, 0),
                   alert=bool(status.get("alert")) and fresh, partial=bool(status.get("part")),
                   driving=status.get("drv_score"), obd=status.get("obd") if fresh else None,
                   rpm=status.get("rpm"), speed=status.get("spd"), throttle=status.get("thr"),
                   esp_linked=linked and fresh, fw=status.get("fw"), t=time.time(), **extra)


def calib_fields(c: Optional[dict]) -> dict:
    """UiState calibration fields from drifts.live's status["calib_ui"] (None = not calibrating)."""
    if not c:
        return {}
    return {"calibrating": c.get("mode"), "calib_prompt": c.get("prompt"), "calib_step": c.get("step"),
            "calib_total": c.get("total"), "calib_progress": c.get("progress")}


def state_from_live(status: dict, stale_s: float = 5.0, now: Optional[float] = None) -> UiState:
    """UiState from drifts.live's /status.json (ESP32 link + camera pipeline)."""
    now = time.time() if now is None else now
    esp = status.get("esp") or {}
    updated = esp.get("updated")
    fresh = updated is not None and now - updated <= stale_s
    linked = esp.get("link") not in (None, "disconnected") and fresh
    eye = status.get("eye") or {}
    pose = status.get("pose") or {}
    face = status.get("face") or {}
    eyes = {"open": "open", "closed": "closed"}.get(eye.get("state"))
    pose_on = bool(pose.get("enabled")) or bool(status.get("face"))
    st = state_from_status(esp, linked=linked, fresh=fresh,
                           vision=True, camera_ok=status.get("error") is None and not status.get("camera_note"),
                           face_found=eyes is not None or bool(pose.get("found")),
                           eyes=eyes, perclos=eye.get("perclos"), blink_rate=face.get("blink_rate"),
                           yaw=pose.get("yaw"), pitch=pose.get("pitch"),
                           calibrated=bool(pose.get("calibrated")) if pose_on else None,
                           **{"calib_progress": pose.get("calib_progress") if pose_on else None,
                              **calib_fields(status.get("calib_ui"))},
                           demo=esp.get("link") == "simulated")
    st.t = now
    return st
