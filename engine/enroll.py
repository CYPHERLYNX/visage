"""
Guided enrollment: capture embeddings across head directions with per-frame
quality gates.

Mirrors Glance's onboarding: the user is guided through nine head directions
(center, up, down, left, right, up-left, up-right, down-left, down-right).
Each accepted frame becomes one 512-d embedding; the image is thrown away.

The manager app owns the camera and the UI; this module owns the reusable
logic: direction classification, quality gates, and the capture state
machine. No camera code lives here, so it is fully testable with synthetic
frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .recognition import (
    ArcFaceEmbedder,
    FaceDetection,
    align_face,
    largest_face,
)

# The nine guided head directions (Glance's enrollment set).
DIRECTIONS = (
    "center",
    "up",
    "down",
    "left",
    "right",
    "up_left",
    "up_right",
    "down_left",
    "down_right",
)

DIRECTION_PROMPTS = {
    "center": "Look straight at the camera",
    "up": "Tilt your head up slightly",
    "down": "Tilt your head down slightly",
    "left": "Turn your head to the left",
    "right": "Turn your head to the right",
    "up_left": "Look up and to the left",
    "up_right": "Look up and to the right",
    "down_left": "Look down and to the left",
    "down_right": "Look down and to the right",
}


@dataclass
class QualityConfig:
    """Per-frame quality gates. A frame must pass ALL of them to be captured."""

    min_face_fraction: float = 0.08   # face box area / frame area
    min_face_pixels: int = 90         # face box width/height in px
    min_sharpness: float = 60.0       # variance of Laplacian on the face crop
    min_eye_openness: float = 0.25    # eyes must be open (blink metric)
    max_faces: int = 1                # exactly one face in frame


@dataclass
class FrameVerdict:
    accepted: bool
    reason: str = ""          # human-readable; empty when accepted
    direction: str = ""       # classified head direction
    embedding: Optional[np.ndarray] = None


def classify_direction(landmarks: np.ndarray,
                       yaw_threshold: float = 0.12,
                       pitch_threshold: float = 0.12) -> str:
    """Classify head direction from 5 landmarks (template order).

    yaw   = (nose_x - eye_mid_x) / iod   (+ = turned to viewer's right)
    pitch = (nose_y - eye_mid_y) / iod, measured relative to FRONTAL_PITCH_REF
    (+ = tilted down).

    Note: unlike Glance, which reads head pose from Vision's yaw estimate,
    YuNet exposes no pose — so pitch is measured against the typical frontal
    nose-below-eyes offset. This is an approximation for guided enrollment,
    not a precise pose estimator.
    """
    FRONTAL_PITCH_REF = 0.45  # typical (nose_y - eye_mid_y) / iod facing forward
    eye_l, eye_r, nose = landmarks[0], landmarks[1], landmarks[2]
    iod = float(abs(eye_r[0] - eye_l[0]))
    if iod < 1.0:
        return "center"
    yaw = (float(nose[0]) - float((eye_l[0] + eye_r[0]) / 2.0)) / iod
    pitch = (float(nose[1]) - float((eye_l[1] + eye_r[1]) / 2.0)) / iod - FRONTAL_PITCH_REF
    horiz = "left" if yaw < -yaw_threshold else ("right" if yaw > yaw_threshold else "")
    vert = "up" if pitch < -pitch_threshold else ("down" if pitch > pitch_threshold else "")
    if horiz and vert:
        return "%s_%s" % (vert, horiz)
    return horiz or vert or "center"


def face_sharpness(face_crop_bgr: np.ndarray) -> float:
    gray = cv2.cvtColor(face_crop_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _eye_openness_for_enroll(frame_gray: np.ndarray, landmarks: np.ndarray) -> Tuple[float, bool]:
    """Mean eye openness (0..1) and whether both eyes were measurable."""
    from .liveness import eye_openness, eye_roi

    iod = float(abs(landmarks[1][0] - landmarks[0][0]))
    opens = []
    for ei in (0, 1):
        roi = eye_roi(frame_gray, landmarks[ei], iod)
        if roi is not None:
            o, c = eye_openness(roi)
            if c > 0:
                opens.append(o)
    if len(opens) < 2:
        return 0.0, False
    return float(sum(opens) / len(opens)), True


class EnrollmentSession:
    """State machine for one guided enrollment.

    The caller feeds frames (already face-detected); this module applies the
    quality gates, classifies the head direction, and collects embeddings.
    """

    def __init__(self, embedder: ArcFaceEmbedder,
                 quality: Optional[QualityConfig] = None,
                 directions: Tuple[str, ...] = DIRECTIONS,
                 samples_per_direction: int = 1) -> None:
        self.embedder = embedder
        self.quality = quality or QualityConfig()
        self.directions = directions
        self.samples_per_direction = samples_per_direction
        self.captured: Dict[str, List[np.ndarray]] = {d: [] for d in directions}

    # -- progress ---------------------------------------------------------

    def next_direction(self) -> Optional[str]:
        for d in self.directions:
            if len(self.captured[d]) < self.samples_per_direction:
                return d
        return None

    def is_complete(self) -> bool:
        return self.next_direction() is None

    def progress(self) -> Tuple[int, int]:
        want = len(self.directions) * self.samples_per_direction
        got = sum(min(len(v), self.samples_per_direction) for v in self.captured.values())
        return got, want

    def next_prompt(self) -> str:
        d = self.next_direction()
        return DIRECTION_PROMPTS[d] if d else "You're all set"

    # -- frame processing --------------------------------------------------

    def process_frame(self, frame_bgr: np.ndarray,
                      detections: List[FaceDetection]) -> FrameVerdict:
        """Apply quality gates to one frame; capture an embedding if it passes."""
        q = self.quality
        if not detections:
            return FrameVerdict(False, "No face visible")
        # Largest face, bypassing the prominence filter — mirrors Glance's
        # enrollment path, so a too-small face reads as "move closer" rather
        # than "nobody there".
        det = largest_face(detections)
        if len(detections) > q.max_faces:
            return FrameVerdict(False, "Just one face at a time, please")
        fh, fw = frame_bgr.shape[:2]
        x, y, w, h = det.box
        if w < q.min_face_pixels or h < q.min_face_pixels:
            return FrameVerdict(False, "Move a little closer")
        if (w * h) / float(fw * fh) < q.min_face_fraction:
            return FrameVerdict(False, "Move a little closer")

        x0, y0 = max(0, x), max(0, y)
        crop = frame_bgr[y0: y0 + h, x0: x0 + w]
        if crop.size == 0:
            return FrameVerdict(False, "Move into view")
        if face_sharpness(crop) < q.min_sharpness:
            return FrameVerdict(False, "Hold still for a moment")

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        openness, measurable = _eye_openness_for_enroll(gray, det.landmarks)
        if measurable and openness < q.min_eye_openness:
            return FrameVerdict(False, "Please open your eyes")

        direction = classify_direction(det.landmarks)
        try:
            aligned = align_face(frame_bgr, det.landmarks)
            embedding = self.embedder.embedding(aligned)
        except Exception:
            # Raw error text never reaches the user; it is diagnostic only.
            return FrameVerdict(False, "Having trouble with this frame. Try again.")

        self.captured.setdefault(direction, []).append(embedding)
        return FrameVerdict(True, direction=direction, embedding=embedding)

    def embeddings(self) -> List[np.ndarray]:
        out = []
        for d in self.directions:
            out.extend(self.captured[d][: self.samples_per_direction])
        return out
