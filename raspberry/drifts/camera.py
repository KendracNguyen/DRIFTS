"""On-demand camera capture using picamera2 with graceful USB/mock fallback."""

from __future__ import annotations

import logging
import time
from typing import Optional

from drifts.config import CameraConfig

log = logging.getLogger(__name__)


class Camera:
    """Controls driver-facing camera capture with start/stop on demand.
    
    When idle, resources are released and sensor is powered down.
    """

    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self._picam2 = None
        self._cv_cap = None
        self._is_running = False

    def start(self) -> None:
        """Initialize camera sensor and start capture stream."""
        if self._is_running:
            return

        log.info("Starting camera (%dx%d @ %d fps)...", self.cfg.width, self.cfg.height, self.cfg.fps)

        # 1. Try picamera2 (standard Raspberry Pi camera stack)
        try:
            from picamera2 import Picamera2
            self._picam2 = Picamera2()
            video_config = self._picam2.create_video_configuration(
                main={"size": (self.cfg.width, self.cfg.height), "format": "RGB888"},
                controls={"FrameRate": self.cfg.fps},
            )
            self._picam2.configure(video_config)
            self._picam2.start()
            self._is_running = True
            log.info("picamera2 started successfully.")
            return
        except Exception as exc:
            log.debug("picamera2 not available: %s. Trying OpenCV fallback...", exc)
            self._picam2 = None

        # 2. Try OpenCV VideoCapture fallback (USB camera / dev testing)
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
                cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
                self._cv_cap = cap
                self._is_running = True
                log.info("OpenCV VideoCapture(0) started successfully.")
                return
            else:
                cap.release()
        except Exception as exc:
            log.debug("OpenCV fallback failed: %s", exc)
            self._cv_cap = None

        # 3. Mock fallback for simulation / test environments
        log.warning("No physical camera detected. Operating in simulated camera mode.")
        self._is_running = True

    def grab(self):
        """Capture and return the latest frame as a numpy array, or None if unavailable."""
        if not self._is_running:
            return None

        if self._picam2 is not None:
            try:
                return self._picam2.capture_array()
            except Exception as exc:
                log.error("Failed to capture frame from picamera2: %s", exc)
                return None

        if self._cv_cap is not None:
            ret, frame = self._cv_cap.read()
            if ret:
                return frame
            return None

        # Mock frame: synthetic image array (RGB)
        try:
            import numpy as np
            # Create a simple 3-channel test image
            frame = np.zeros((self.cfg.height, self.cfg.width, 3), dtype=np.uint8)
            # Add timestamp marker
            return frame
        except ImportError:
            return None

    def stop(self) -> None:
        """Stop capture stream and release all camera hardware resources."""
        if not self._is_running:
            return

        log.info("Stopping camera to save power...")
        if self._picam2 is not None:
            try:
                self._picam2.stop()
                self._picam2.close()
            except Exception as exc:
                log.warning("Error stopping picamera2: %s", exc)
            self._picam2 = None

        if self._cv_cap is not None:
            try:
                self._cv_cap.release()
            except Exception as exc:
                log.warning("Error releasing OpenCV capture: %s", exc)
            self._cv_cap = None

        self._is_running = False
        log.info("Camera stopped.")

    @property
    def is_running(self) -> bool:
        return self._is_running
