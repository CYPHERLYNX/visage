"""Camera access with graceful degradation.

On Windows with OpenCV installed this opens the real webcam. Everywhere
else — no OpenCV, no camera, headless Linux — it raises a
CameraUnavailableError carrying a plain-English explanation, and the UI
shows that instead of a black rectangle.

Set VISAGE_DEMO_CAMERA=1 to use a synthetic camera (animated gradient with
a face-like blob) so the enrollment wizard can be exercised and
screenshot-tested with no hardware at all. The preview is always labelled
when it is simulated.
"""

from __future__ import annotations

import logging
import math
import os
import time

import numpy as np

from .errors import CameraUnavailableError

log = logging.getLogger("visage")

def demo_camera_enabled() -> bool:
    """Read live so the in-app fallback button can enable it at runtime."""
    return os.environ.get("VISAGE_DEMO_CAMERA") == "1"


# Kept for compatibility; prefer demo_camera_enabled().
DEMO_CAMERA = demo_camera_enabled()


class Camera:
    """Pull-based camera. `read()` returns an RGB numpy frame or raises."""

    def __init__(self, device_id: int = 0, width: int = 640, height: int = 480):
        self.device_id = device_id
        self.width = width
        self.height = height
        self._cap = None
        self._demo = False
        self._t0 = time.monotonic()

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:
        if demo_camera_enabled():
            self._demo = True
            log.info("Using synthetic demo camera (VISAGE_DEMO_CAMERA=1).")
            return
        try:
            import cv2  # lazy: OpenCV is a Windows runtime dependency
        except ImportError as exc:
            raise CameraUnavailableError(
                "No camera support is installed.",
                "On Windows, run: pip install -r requirements.txt, then restart Visage Manager.",
            ) from exc
        cap = cv2.VideoCapture(self.device_id, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if not cap.isOpened():
            cap.release()
            raise CameraUnavailableError(
                "Visage could not open the camera.",
                "Check that it is plugged in, not in use by another app, and that camera "
                "access is allowed in Windows privacy settings (Settings > Privacy & security > Camera).",
            )
        self._cap = cap

    def read(self) -> np.ndarray:
        if self._demo:
            return self._demo_frame()
        if self._cap is None:
            raise CameraUnavailableError(
                "The camera is not open.",
                "Close and reopen the enrollment wizard to try again.",
            )
        import cv2
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraUnavailableError(
                "The camera stopped delivering frames.",
                "Unplug and reconnect the camera (or close apps using it), then try again.",
            )
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._demo = False

    @property
    def is_demo(self) -> bool:
        return self._demo

    # -- synthetic frames ---------------------------------------------------
    def _demo_frame(self) -> np.ndarray:
        """Dark gradient with a softly pulsing face-like ellipse — clearly
        synthetic, good enough to drive the wizard UI."""
        t = time.monotonic() - self._t0
        h, w = self.height, self.width
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        base = 24 + 18 * (yy / h) + 10 * np.sin(t * 0.7)
        frame = np.stack([base * 0.9, base * 0.95, base * 1.05], axis=-1)

        cx = w / 2 + 24 * math.sin(t * 0.5)
        cy = h / 2 + 14 * math.cos(t * 0.35)
        rx, ry = w * 0.16, h * 0.22
        dist = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        skin = np.clip(1 - dist, 0, 1) ** 0.7
        glow = 46 + 8 * math.sin(t * 2.0)
        for c in range(3):
            frame[:, :, c] += skin * glow
        return np.clip(frame, 0, 255).astype(np.uint8)


def probe_cameras(max_devices: int = 4) -> list[tuple[int, str]]:
    """Return [(index, label)] for cameras that actually open. Empty list
    means none found — never raises, and never prints: OpenCV logs scary
    errors for missing devices, so its stderr is silenced during probing."""
    try:
        import cv2
    except ImportError:
        return []
    found = []
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
    devnull = open(os.devnull, "w")
    old_stderr = os.dup(2)
    try:
        os.dup2(devnull.fileno(), 2)
        for i in range(max_devices):
            cap = cv2.VideoCapture(i, backend)
            if cap.isOpened():
                found.append((i, f"Camera {i}"))
                cap.release()
    finally:
        os.dup2(old_stderr, 2)
        os.close(old_stderr)
        devnull.close()
    return found
