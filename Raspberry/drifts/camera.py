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

    def __init__(self, cfg: CameraConfig, device: int = 0, use_picamera: bool = True):
        self.cfg = cfg
        self.device = device              # OpenCV webcam index (laptop testing)
        self.use_picamera = use_picamera
        self._picam2 = None
        self._cv_cap = None
        self._is_running = False
        self.model: Optional[str] = None      # the sensor libcamera reports, e.g. "imx708_noir"

    def _apply_focus(self) -> Optional[str]:
        """Camera Module 3: hold the focus at the driver (or auto / continuous). None if no autofocus."""
        ctrls = getattr(self._picam2, "camera_controls", {}) or {}
        if "AfMode" not in ctrls:
            return None
        mode = (getattr(self.cfg, "focus", "manual") or "manual").lower()
        if mode == "continuous":
            self._picam2.set_controls({"AfMode": 2})                          # libcamera AfModeContinuous
            return "continuous"
        if mode == "auto":
            self._picam2.set_controls({"AfMode": 1, "AfTrigger": 0})          # one autofocus scan
            return "auto"
        pos = float(getattr(self.cfg, "lens_position", 1.7))
        self._picam2.set_controls({"AfMode": 0, "LensPosition": pos})         # manual, dioptres
        return f"manual {pos:.1f} dioptres (~{100 / pos:.0f} cm)" if pos > 0 else "manual infinity"

    def start(self) -> None:
        """Initialize camera sensor and start capture stream."""
        if self._is_running:
            return

        log.info("Starting camera (%dx%d @ %d fps)...", self.cfg.width, self.cfg.height, self.cfg.fps)

        # 1. Try picamera2 (standard Raspberry Pi camera stack)
        try:
            if not self.use_picamera:
                raise RuntimeError("picamera2 disabled")
            from picamera2 import Picamera2
            self._picam2 = Picamera2()
            video_config = self._picam2.create_video_configuration(
                main={"size": (self.cfg.width, self.cfg.height), "format": "RGB888"},
                controls={"FrameRate": self.cfg.fps},
            )
            self._picam2.configure(video_config)
            self._picam2.start()
            self.model = (getattr(self._picam2, "camera_properties", {}) or {}).get("Model")
            try:
                focus = self._apply_focus()
            except Exception as exc:               # never stop the camera over focus
                focus = f"not set ({exc})"
            self._is_running = True
            log.info("picamera2 started: %s%s", self.model or "camera",
                     f", focus {focus}" if focus else " (fixed focus)")
            return
        except Exception as exc:
            log.debug("picamera2 not available: %s. Trying OpenCV fallback...", exc)
            self._picam2 = None

        # 2. Try OpenCV VideoCapture fallback (USB camera / dev testing)
        try:
            import cv2
            import os
            # Windows: try DirectShow, then Media Foundation, then the default,
            # and keep the first that actually delivers frames.
            def delivers(c) -> bool:
                # Opening can succeed while no frames ever arrive (camera held
                # by another app, or a backend that doesn't suit this webcam).
                for _ in range(10):
                    ok, _f = c.read()
                    if ok:
                        return True
                    time.sleep(0.1)
                return False

            cap = None
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, None] if os.name == "nt" else [None]
            for be in backends:
                c = cv2.VideoCapture(self.device) if be is None else cv2.VideoCapture(self.device, be)
                if c.isOpened() and delivers(c):
                    cap = c
                    break
                c.release()
            if cap is not None and cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
                cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
                self._cv_cap = cap
                self._is_running = True
                log.info("OpenCV VideoCapture(%d) started successfully.", self.device)
                return
        except Exception as exc:
            log.debug("OpenCV fallback failed: %s", exc)
            self._cv_cap = None

        # 3. Mock fallback for simulation / test environments
        log.warning("No camera opened (index %s). Showing a blank test image. Is another app using the "
                    "webcam? Try --camera 1.", self.device)
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
