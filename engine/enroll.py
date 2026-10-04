"""
Guided enrollment: capture embeddings across head directions with per-frame
quality gates.

Faithful to jonnyoo/glance's OnboardingController enrollment state machine:

- Nine poses in Glance's presentation order, two samples per pose.
- Each frame is captured only when the head matches the *target* pose —
  samples are never bucketed into whatever direction a classifier guesses.
- 500 ms settle hold plus a 3-frame match streak before a capture fires,
  so samples are taken once the user has settled into the turn.
- A 12-second stall timeout widens the match bands 1.25x, so an unusual
  camera angle can never strand the user mid-flow.
- Yaw/pitch are measured *relative to the user's own center pose*
  (captured first), so there are no magic frontal constants to miscalibrate
  per face.

Sign conventions (Glance's):
    +yaw   = turned to the user's own left   ("+yaw turns left")
    +pitch = nose lower in the image = head tilted down ("+pitch looks down")

The manager app owns the camera and the UI; this module owns the reusable
logic: pose estimation, the target state machine, and quality gates. No
camera code lives here, so it is fully testable with synthetic frames.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .recognition import (
    ArcFaceEmbedder,
    FaceDetection,
    align_face,
    largest_face,
)

# The nine guided head directions, in Glance's presentation order
# (center, left, topLeft, top, topRight, right, bottomRight, bottom,
# bottomLeft). Keys stay engine-style ("up_left") so the manager's pose
# map is untouched.
DIRECTIONS = (
    "center",
    "left",
    "up_left",
    "up",
    "up_right",
    "right",
    "down_right",
    "down",
    "down_left",
)

# Glance's instruction copy, verbatim in spirit.
DIRECTION_PROMPTS = {
    "center": "Look straight at the camera",
    "left": "Turn your head slightly left",
    "up_left": "Turn your head to the top left",
    "up": "Turn your head slightly up",
    "up_right": "Turn your head to the top right",
    "right": "Turn your head slightly right",
    "down_right": "Turn your head to the bottom right",
    "down": "Turn your head slightly down",
    "down_left": "Turn your head to the bottom left",
}

# (yaw_band, pitch_band) per direction — Glance's EnrollmentPose bands.
_POSE_BANDS: Dict[str, Tuple[str, str]] = {
    "center": ("none", "none"),
    "left": ("left", "none"),
    "up_left": ("left", "up"),
    "up": ("none", "up"),
    "up_right": ("right", "up"),
    "right": ("right", "none"),
    "down_right": ("right", "down"),
    "down": ("none", "down"),
    "down_left": ("left", "down"),
}

_EXCESSIVE_COPY = {
    "left": "Too far left",
    "right": "Too far right",
    "up": "Too far up",
    "down": "Too far down",
}

# Pose-match bands, in nose-offset/iod ratio units. A ~20-degree head turn
# moves the nose tip roughly 0.13-0.20 of the inter-ocular distance, so the
# inner thresholds sit at a comfortable "slight" turn and the outer caps
# reject turns too extreme to be useful enrollment data.
_YAW_INNER = 0.14
_YAW_CENTER_TOL = 0.10
_YAW_OUTER_CAP = 0.40
_PITCH_INNER = 0.10
_PITCH_CENTER_TOL = 0.08
_PITCH_OUTER_CAP = 0.30

# Glance's timing knobs.
_SAMPLES_PER_DIRECTION = 2
_MATCH_STREAK = 3          # consecutive matching frames before a capture
_POSE_HOLD_S = 0.5         # settle time once the pose first matches
_STALL_TIMEOUT_S = 12.0    # then bands widen instead of stranding the user
_STALL_WIDEN_FACTOR = 1.25
_INITIAL_CAPTURE_DELAY_S = 1.5  # don't capture mid-blink as the camera comes up


@dataclass
class QualityConfig:
    """Per-frame quality gates. A frame must pass ALL of them to be captured.

    Deliberately permissive (Glance's philosophy): better to accept a
    mediocre sample than to stall the whole flow.
    """

    min_face_fraction: float = 0.08   # face box area / frame area
    min_face_pixels: int = 90         # face box width/height in px
    min_sharpness: float = 30.0       # variance of Laplacian on the face crop
    min_eye_openness: float = 0.25    # eyes must be open (blink metric)
    max_faces: int = 1                # exactly one face in frame


@dataclass
class FrameVerdict:
    accepted: bool
    reason: str = ""          # machine key; the bridge maps it to UI copy
    direction: str = ""       # target direction of an accepted capture
    embedding: Optional[np.ndarray] = None


def head_pose_ratios(landmarks: np.ndarray) -> Optional[Tuple[float, float]]:
    """Raw yaw/pitch ratios from 5 YuNet landmarks (viewer-left eye first).

    yaw   = (nose_x - eye_mid_x) / iod      (+ = nose toward viewer's right
                                             = user turned to their own left)
    pitch = (nose_y - eye_mid_y) / iod      (+ = nose lower in the image
                                             = head tilted down)

    Returns None when the landmarks are degenerate. These are *relative*
    measurements — the session calibrates them against the user's own
    center pose, so no absolute frontal reference is needed.
    """
    if landmarks is None or len(landmarks) < 3:
        return None
    eye_l, eye_r, nose = landmarks[0], landmarks[1], landmarks[2]
    iod = float(abs(eye_r[0] - eye_l[0]))
    if iod < 1.0:
        return None
    yaw = (float(nose[0]) - float((eye_l[0] + eye_r[0]) / 2.0)) / iod
    pitch = (float(nose[1]) - float((eye_l[1] + eye_r[1]) / 2.0)) / iod
    if not (np.isfinite(yaw) and np.isfinite(pitch)):
        return None
    return yaw, pitch


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


def _yaw_matches(yaw_rel: float, band: str, factor: float) -> bool:
    if band == "none":
        return abs(yaw_rel) < _YAW_CENTER_TOL * factor
    if band == "left":
        return yaw_rel > _YAW_INNER / factor and yaw_rel < _YAW_OUTER_CAP
    if band == "right":
        return yaw_rel < -_YAW_INNER / factor and yaw_rel > -_YAW_OUTER_CAP
    return False


def _pitch_matches(pitch_rel: float, band: str, factor: float) -> bool:
    if band == "none":
        return abs(pitch_rel) < _PITCH_CENTER_TOL * factor
    if band == "up":
        return pitch_rel < -_PITCH_INNER / factor and pitch_rel > -_PITCH_OUTER_CAP
    if band == "down":
        return pitch_rel > _PITCH_INNER / factor and pitch_rel < _PITCH_OUTER_CAP
    return False


def _excessive_turn(yaw_rel: float, pitch_rel: float) -> Optional[str]:
    """Dominant over-turned axis, or None when inside the safe envelope."""
    yaw_over = abs(yaw_rel) / _YAW_OUTER_CAP
    pitch_over = abs(pitch_rel) / _PITCH_OUTER_CAP
    if max(yaw_over, pitch_over) < 1.0:
        return None
    if yaw_over >= pitch_over:
        return "left" if yaw_rel > 0 else "right"
    return "up" if pitch_rel < 0 else "down"


class EnrollmentSession:
    """Glance-style target-driven enrollment state machine.

    The caller feeds frames (already face-detected); this module applies the
    quality gates, checks the head against the *current target pose*, and
    captures an embedding only on a settled match. `now` accepts an explicit
    monotonic timestamp for deterministic tests; otherwise time.monotonic()
    is used.
    """

    def __init__(self, embedder: ArcFaceEmbedder,
                 quality: Optional[QualityConfig] = None,
                 directions: Tuple[str, ...] = DIRECTIONS,
                 samples_per_direction: int = _SAMPLES_PER_DIRECTION) -> None:
        self.embedder = embedder
        self.quality = quality or QualityConfig()
        self.directions = directions
        self.samples_per_direction = samples_per_direction
        self.captured: Dict[str, List[np.ndarray]] = {d: [] for d in directions}
        # (yaw, pitch) ratios per captured sample, for baseline calibration.
        self._captured_ratios: Dict[str, List[Tuple[float, float]]] = {
            d: [] for d in directions
        }
        self._pose_index = 0
        self._match_streak = 0
        self._pose_started_at: Optional[float] = None
        self._hold_started_at: Optional[float] = None
        self._capture_ready_at: Optional[float] = None

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

    # -- internals --------------------------------------------------------

    def _baseline(self) -> Optional[Tuple[float, float]]:
        ratios = self._captured_ratios.get("center", [])
        if not ratios:
            return None
        yaw = sum(r[0] for r in ratios) / len(ratios)
        pitch = sum(r[1] for r in ratios) / len(ratios)
        return yaw, pitch

    def _pose_matches(self, target: str, yaw: float, pitch: float,
                      widened: bool) -> bool:
        factor = (_STALL_WIDEN_FACTOR if widened else 1.0)
        if target == "center":
            # No baseline yet on the very first pose — the user starts out
            # looking at the camera by definition.
            base = self._baseline()
            if base is None:
                return True
            yaw_rel, pitch_rel = yaw - base[0], pitch - base[1]
        else:
            base = self._baseline()
            if base is None:
                return False
            yaw_rel, pitch_rel = yaw - base[0], pitch - base[1]
        yaw_band, pitch_band = _POSE_BANDS[target]
        return _yaw_matches(yaw_rel, yaw_band, factor) and _pitch_matches(
            pitch_rel, pitch_band, factor)

    def _reset_match(self) -> None:
        self._match_streak = 0
        self._hold_started_at = None

    # -- frame processing --------------------------------------------------

    def process_frame(self, frame_bgr: np.ndarray,
                      detections: List[FaceDetection],
                      now: Optional[float] = None) -> FrameVerdict:
        """Apply quality gates and target-pose matching to one frame."""
        t = time.monotonic() if now is None else now
        q = self.quality
        target = self.next_direction()
        if target is None:
            return FrameVerdict(False, reason="complete")

        if self._pose_started_at is None:
            self._pose_started_at = t
        if self._capture_ready_at is None:
            # Hold back only the very first pose, not every pose change.
            self._capture_ready_at = t + _INITIAL_CAPTURE_DELAY_S

        if not detections:
            self._reset_match()
            return FrameVerdict(False, reason="no_face")
        det = largest_face(detections)
        if len(detections) > q.max_faces:
            self._reset_match()
            return FrameVerdict(False, reason="multiple_faces")

        fh, fw = frame_bgr.shape[:2]
        x, y, w, h = det.box
        if w < q.min_face_pixels or h < q.min_face_pixels:
            self._reset_match()
            return FrameVerdict(False, reason="too_far")
        if (w * h) / float(fw * fh) < q.min_face_fraction:
            self._reset_match()
            return FrameVerdict(False, reason="too_far")

        x0, y0 = max(0, x), max(0, y)
        crop = frame_bgr[y0: y0 + h, x0: x0 + w]
        if crop.size == 0:
            self._reset_match()
            return FrameVerdict(False, reason="too_far")
        if face_sharpness(crop) < q.min_sharpness:
            self._reset_match()
            return FrameVerdict(False, reason="hold_still")

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        openness, measurable = _eye_openness_for_enroll(gray, det.landmarks)
        if measurable and openness < q.min_eye_openness:
            self._reset_match()
            return FrameVerdict(False, reason="eyes_closed")

        ratios = head_pose_ratios(det.landmarks)
        if ratios is None:
            self._reset_match()
            return FrameVerdict(False, reason="hold_still")
        yaw, pitch = ratios

        # Detection ran; only capture is held back while settling in.
        if t < self._capture_ready_at:
            self._reset_match()
            return FrameVerdict(False, reason="settling")

        # An extreme turn is never useful enrollment data — say so plainly.
        base = self._baseline()
        if base is not None and target != "center":
            over = _excessive_turn(yaw - base[0], pitch - base[1])
            if over is not None:
                self._reset_match()
                return FrameVerdict(False, reason="excessive_" + over)

        widened = (t - self._pose_started_at) > _STALL_TIMEOUT_S
        if not self._pose_matches(target, yaw, pitch, widened):
            self._reset_match()
            return FrameVerdict(False, reason="awaiting_pose")

        if self._hold_started_at is None:
            self._hold_started_at = t
        if (t - self._hold_started_at) < _POSE_HOLD_S:
            return FrameVerdict(False, reason="awaiting_pose")
        self._match_streak += 1
        if self._match_streak < _MATCH_STREAK:
            return FrameVerdict(False, reason="awaiting_pose")
        self._match_streak = 0

        try:
            aligned = align_face(frame_bgr, det.landmarks)
            embedding = self.embedder.embedding(aligned)
        except Exception:
            self._reset_match()
            return FrameVerdict(False, reason="frame_error")

        self.captured[target].append(embedding)
        self._captured_ratios[target].append((yaw, pitch))

        if len(self.captured[target]) >= self.samples_per_direction:
            self._pose_index += 1
            self._pose_started_at = t
            self._hold_started_at = None
            self._match_streak = 0

        return FrameVerdict(True, reason="captured", direction=target,
                            embedding=embedding)

    def embeddings(self) -> List[np.ndarray]:
        out = []
        for d in self.directions:
            out.extend(self.captured[d][: self.samples_per_direction])
        return out
