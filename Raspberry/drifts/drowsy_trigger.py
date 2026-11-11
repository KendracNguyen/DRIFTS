"""Camera-confirmed drowsiness -> DROWSY / NOT_DROWSY to the ESP32.

The ESP32's alert score is 0.7 x eye + 0.3 x driving. Parked, or with no
OBD dongle, driving sits near 100, so the eye part alone can only pull the
score to about 60 (CAUTION), never into Intervention 2, even with the eyes
closed. The design has the camera confirm drowsiness itself instead (the
"Pi reports DROWSY" path that starts the buzzer and motors). drifts.live
uses this to do that:

    DROWSY      once the face mesh's state has been "drowsy" (eyes closed
                >= 1 s, or drowsiness > 50) for `confirm_s`, then again
                every `repeat_s` while it lasts (an ESP32 alert is capped at
                20 s, so it would otherwise lapse)
    NOT_DROWSY  once the driver has been alert for `clear_s` after an alert

Pure logic: tests/test_drowsy_trigger.py.
"""

from __future__ import annotations

from typing import Optional


class DrowsyTrigger:
    def __init__(self, confirm_s: float = 1.5, repeat_s: float = 10.0, clear_s: float = 3.0):
        self.confirm_s, self.repeat_s, self.clear_s = confirm_s, repeat_s, clear_s
        self.drowsy_since: Optional[float] = None
        self.alert_since: Optional[float] = None
        self.last_sent: Optional[float] = None
        self.active = False          # DROWSY sent and not yet cleared
        self.sent = 0

    def update(self, now: float, state: Optional[str]) -> Optional[str]:
        """state: the face's "drowsy" / "alert" / "no_face" (None = unknown).
        Returns the command to send now, or None."""
        if state == "drowsy":
            self.alert_since = None
            if self.drowsy_since is None:
                self.drowsy_since = now
            if now - self.drowsy_since < self.confirm_s:
                return None
            if not self.active or now - (self.last_sent or 0) >= self.repeat_s:
                self.active, self.last_sent = True, now
                self.sent += 1
                return "DROWSY"
            return None
        self.drowsy_since = None
        if state == "alert" and self.active:
            if self.alert_since is None:
                self.alert_since = now
            if now - self.alert_since >= self.clear_s:
                self.active, self.alert_since = False, None
                return "NOT_DROWSY"
        return None
