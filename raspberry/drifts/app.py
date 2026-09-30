"""Main application state machine for DRIFTS on Raspberry Pi Zero 2 W."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Optional

from drifts.ble_client import BleClient
from drifts.camera import Camera
from drifts.config import Config
from drifts.detector import DrowsinessDetector

log = logging.getLogger(__name__)


class Application:
    """Core state machine:
    
    [IDLE] --- (WAKE from ESP32) ---> [ANALYZING]
      ^                                     |
      |                                     |
      +---- (Send Result + Cooldown) <------+
    """

    def __init__(self, cfg: Config, sim: bool = False, no_ble: bool = False):
        self.cfg = cfg
        self.sim = sim
        self.no_ble = no_ble

        self.ble = BleClient(self.cfg.ble)
        self.camera = Camera(self.cfg.camera)
        self.detector = DrowsinessDetector(self.cfg.detector)
        self._running = True

    async def run(self) -> None:
        """Start background tasks and run the main supervision loop."""
        tasks = []

        if not self.no_ble:
            tasks.append(asyncio.create_task(self.ble.run()))
            # Required: the ESP32 fail-safe cuts alerts without it.
            tasks.append(asyncio.create_task(self.ble.heartbeat()))

        if self.sim:
            tasks.append(asyncio.create_task(self._sim_wake_loop()))

        log.info("DRIFTS v2 Pi application started. System is IDLE.")

        try:
            while self._running:
                # ── 1. IDLE: Wait for WAKE notification ──
                log.info("Idle: waiting for ESP32 trigger...")
                await self.ble.wake_event.wait()
                self.ble.wake_event.clear()

                if not self._running:
                    break

                # ── 2. ANALYZING: Wake camera and run inference cycle ──
                log.info("WAKE event received! Starting camera and running inference...")
                drowsy_count = 0
                total_frames = 0

                self.camera.start()
                try:
                    while total_frames < self.cfg.detector.max_frames_per_cycle and self._running:
                        frame = self.camera.grab()
                        if frame is None:
                            await asyncio.sleep(0.05)
                            continue

                        result = self.detector.predict(frame)
                        total_frames += 1

                        if result.is_drowsy:
                            drowsy_count += 1
                            log.info(
                                "Frame %d/%d: DROWSY detected (%s, conf=%.2f) [count: %d/%d]",
                                total_frames,
                                self.cfg.detector.max_frames_per_cycle,
                                result.label,
                                result.confidence,
                                drowsy_count,
                                self.cfg.detector.drowsy_frame_threshold,
                            )
                        else:
                            log.debug(
                                "Frame %d/%d: Alert (%s, conf=%.2f)",
                                total_frames,
                                self.cfg.detector.max_frames_per_cycle,
                                result.label,
                                result.confidence,
                            )

                        if drowsy_count >= self.cfg.detector.drowsy_frame_threshold:
                            log.info("Drowsy threshold reached early (%d frames).", drowsy_count)
                            break

                        await asyncio.sleep(0.01)  # Yield to event loop
                finally:
                    self.camera.stop()

                # ── 3. SEND RESULT: Report back to ESP32 over BLE ──
                is_drowsy = (drowsy_count >= self.cfg.detector.drowsy_frame_threshold)
                result_str = "DROWSY" if is_drowsy else "NOT_DROWSY"
                log.info(
                    "Cycle complete. Result: %s (%d/%d drowsy frames). Sending to ESP32...",
                    result_str,
                    drowsy_count,
                    total_frames,
                )
                await self.ble.send_result(result_str)

                # ── 4. COOLDOWN: Enforce cooldown before next cycle ──
                log.info("Entering cooldown for %.1f seconds...", self.cfg.power.cooldown_s)
                await asyncio.sleep(self.cfg.power.cooldown_s)

        except asyncio.CancelledError:
            log.info("Application loop cancelled.")
        finally:
            self.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _sim_wake_loop(self) -> None:
        """Periodically emit simulated WAKE events for testing without ESP32."""
        interval = 25.0
        log.info("[SIM] Simulator active: will trigger WAKE every %.1f seconds.", interval)
        while self._running:
            await asyncio.sleep(interval)
            log.info("[SIM] Emitting periodic WAKE event.")
            self.ble.trigger_simulated_wake()

    def stop(self) -> None:
        """Gracefully shut down all components."""
        self._running = False
        self.ble.wake_event.set()
        self.ble.stop()
        self.camera.stop()
