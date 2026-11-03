"""Stacking interventions on the Pi 5: score -> level -> buzzer / motors / strip.

  Level 0  OK                     nothing
  Level 1  score < 70 (fatigue)   buzzer: 1 s beep, 1 s pause, repeating
  Level 2  score < 50 (worsening) buzzer pattern + seat vibration (0.5 s on / 0.3 s off)
  Voice: each level change is also spoken (tokyo/voice.py); the buzzer holds
  off while a clip plays. Levels 3-4 (check-in, rest stop) are planned.

Interventions stack: each level keeps everything below it running.
A confirmed drowsy alarm (e.g. the face pipeline's DROWSY state) forces Level 2.

Level changes use hysteresis (recover `hysteresis` points above a band to
drop) and a minimum hold, so a score sitting on a boundary does not make the
outputs stutter. Patterns are timed from when the level started.
"""

from __future__ import annotations

from typing import Optional

from tokyo.config import BuzzerConfig, InterventionConfig, MotorConfig


class LevelTracker:
    def __init__(self, cfg: InterventionConfig):
        self.cfg = cfg
        self.level = 0
        self.since = 0.0

    def _target(self, score: Optional[float]) -> int:
        if score is None:
            return self.level
        c = self.cfg
        # Entering a worse level happens at the threshold; leaving it needs margin.
        up1, up2 = c.level1_below, c.level2_below
        if self.level >= 2:
            up2 += c.hysteresis
        if self.level >= 1:
            up1 += c.hysteresis
        return 2 if score < up2 else 1 if score < up1 else 0

    def update(self, now: float, score: Optional[float], alarm: bool = False) -> int:
        target = 2 if alarm else self._target(score)
        if target > self.level or (target < self.level and now - self.since >= self.cfg.min_hold_s):
            self.level, self.since = target, now
        return self.level


def pattern_on(now: float, since: float, on_s: float, off_s: float) -> bool:
    period = on_s + off_s
    return period <= 0 or ((now - since) % period) < on_s


class Interventions:
    """Drives buzzer and motors for the current level. Call tick() often (>= 20 Hz)."""

    def __init__(self, buzzer, motors, bcfg: BuzzerConfig, mcfg: MotorConfig, icfg: InterventionConfig):
        self.buzzer, self.motors, self.bcfg, self.mcfg = buzzer, motors, bcfg, mcfg
        self.levels = LevelTracker(icfg)
        self.mute_until = 0.0

    def mute_buzzer(self, until: float) -> None:
        """Hold the buzzer off until `until` (a voice clip is playing). Motors continue."""
        self.mute_until = max(self.mute_until, until)
        if self.buzzer.on:
            self.buzzer.set(False)        # now, not on the next tick

    def tick(self, now: float, score: Optional[float], alarm: bool = False) -> int:
        lvl = self.levels.update(now, score, alarm)
        since = self.levels.since
        want_buzz = (lvl >= 1 and pattern_on(now, since, self.bcfg.on_s, self.bcfg.off_s)
                     and now >= self.mute_until)
        want_motor = lvl >= 2 and pattern_on(now, since, self.mcfg.on_s, self.mcfg.off_s)
        if want_buzz != self.buzzer.on:
            self.buzzer.set(want_buzz)
        if want_motor != self.motors.on:
            self.motors.set(want_motor)
        return lvl

    def stop(self) -> None:
        self.buzzer.set(False)
        self.motors.set(False)
