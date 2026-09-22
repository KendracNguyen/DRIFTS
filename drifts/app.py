"""Main control loop: read sources -> score -> level -> tell the ESP32."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

from .api import SharedState, start_api_thread
from .config import Config
from .fatigue import Level, fatigue_score, level_for
from .sources import build_sources

log = logging.getLogger(__name__)


def _alert_flags(level: Level) -> dict:
    return {
        "seat": level >= Level.WARN,        # vibration motor
        "audio": level >= Level.CRITICAL,   # buzzer
        "voice": False,                     # not implemented
    }


class DriftsApp:
    def __init__(self, cfg: Config, *, force_sim=False, use_ble=True, use_api=True):
        self.cfg = cfg
        self.state = SharedState()
        self.eyes, self.steering = build_sources(cfg, force_sim=force_sim)
        self.use_api = use_api and cfg.api.enabled

        self.link = None
        if use_ble and cfg.ble.enabled:
            from .ble_link import EspLink  # needs bleak; skipped with --no-ble
            self.link = EspLink(cfg.ble)

        self.level = Level.OK
        self._sent_level: Level | None = None
        self._sent_generation = -1

    def _commands_for(self, level: Level) -> list[str]:
        a = self.cfg.alerts
        return {Level.OK: a.ok, Level.WARN: a.warn, Level.CRITICAL: a.critical}[level]

    async def _control_loop(self):
        period = 1.0 / self.cfg.sources.loop_hz
        f = self.cfg.fatigue
        last_log = 0.0
        while True:
            start = time.monotonic()

            perclos = self.eyes.read()
            steering = self.steering.read()
            score = fatigue_score(perclos, steering)
            new_level = level_for(score, f.warn_below, f.critical_below, self.level, f.hysteresis)

            if new_level != self.level:
                log.info("Level %s -> %s (score %.1f, PERCLOS %.1f, steering %.1f)",
                         self.level.name, new_level.name, score, perclos, steering)
                self.level = new_level

            # Send when the level changes, or when the ESP32 has just (re)connected.
            if self.link and self.link.connected:
                if self._sent_level != self.level or self._sent_generation != self.link.generation:
                    if await self.link.send_all(self._commands_for(self.level)):
                        self._sent_level = self.level
                        self._sent_generation = self.link.generation

            self.state.update(
                perclos=round(perclos, 1),
                steering=round(steering, 1),
                score=round(score, 1),
                level=self.level.name,
                alerts=_alert_flags(self.level),
                esp32_connected=bool(self.link and self.link.connected),
            )

            if start - last_log >= 5.0:
                log.debug("score %.1f  PERCLOS %.1f  steering %.1f  level %s  esp32 %s",
                          score, perclos, steering, self.level.name,
                          "up" if self.link and self.link.connected else "down")
                last_log = start

            await asyncio.sleep(max(0.0, period - (time.monotonic() - start)))

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(self.cfg.ble.heartbeat_s)
            if self.link and self.link.connected:
                await self.link.send("PING")

    async def run(self):
        if self.use_api:
            start_api_thread(self.state, self.cfg.api.host, self.cfg.api.port)

        link_task = asyncio.create_task(self.link.run()) if self.link else None
        main = asyncio.current_task()
        if sys.platform != "win32":
            # `systemctl stop` sends SIGTERM: turn it into a clean shutdown.
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, main.cancel)

        log.info("DRIFTS running")
        try:
            workers = [self._control_loop()]
            if self.link:
                workers.append(self._heartbeat())
            await asyncio.gather(*workers)
        finally:
            # Silence the motor and buzzer before we go, then drop the link.
            if self.link and self.link.connected:
                log.info("Shutting down: sending STOP to ESP32")
                await self.link.send("STOP")
            if link_task:
                link_task.cancel()
                await asyncio.gather(link_task, return_exceptions=True)
            log.info("DRIFTS stopped")
