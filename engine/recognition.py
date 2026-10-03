"""
Face detection, alignment, embedding and matching.

Pipeline (mirrors Glance's recognition half):
  frame -> YuNet (OpenCV zoo) face detection with 5 landmarks
        -> similarity-transform alignment to the canonical 112x112 crop
           (InsightFace/ArcFace 5-point template)
        -> ArcFace 512-d embedding via ONNX Runtime
           (InsightFace buffalo_l ``w600k_r50``)
        -> cosine similarity against enrolled identities

Matching rule (mirrors Glance's FaceRecognitionPipeline.bestMatch): the top
identity must clear the threshold on BOTH its centroid similarity and its
best single-sample similarity.

Deterministic: same aligned input -> same embedding, always.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

# Canonical InsightFace 5-point alignment template for a 112x112 crop.
# Order: viewer-left eye, viewer-right eye, nose, viewer-left mouth,
# viewer-right mouth.
ARCFACE_TEMPLATE_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

ALIGNED_SIZE = 112
EMBEDDING_DIM = 512
MODEL_IDENTIFIER = "arcface-w600k_r50-v1"
YUNET_MODEL_NAME = "face_detection_yunet_2023mar.onnx"
ARCFACE_MODEL_NAME = "w600k_r50.onnx"

# Mirrors Glance's shipped default (GlanceSettings.matchThreshold ?? 0.66).
# Glance's Face Lab slider defaults to 0.6; raw ArcFace literature quotes
# ~0.28-0.40. This default is tunable — calibrate from genuine/impostor data.
DEFAULT_MATCH_THRESHOLD = 0.66

# Below this fraction of frame width, a face is a bystander, not a candidate
# (mirrors Glance's minimumProminentFaceWidth = 0.18, shared with the
# enrollment "move closer" prompt).
MINIMUM_PROMINENT_FACE_WIDTH = 0.18
# Max normalized-coordinate drift between frames still counted as the same
# person (mirrors Glance's continuityDistanceTolerance = 0.3).
CONTINUITY_DISTANCE_TOLERANCE = 0.3


def default_model_dir() -> str:
    """Models live under ProgramData on Windows, ./models for dev."""
    if os.name == "nt":
        return r"C:\ProgramData\Visage\models"
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "models")


class ModelNotFoundError(FileNotFoundError):
    pass


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass
class FaceDetection:
    """One detected face. Landmarks are (x, y) in frame pixels, ordered:
    viewer-left eye, viewer-right eye, nose tip, viewer-left mouth corner,
    viewer-right mouth corner."""

    box: Tuple[int, int, int, int]  # x, y, w, h
    landmarks: np.ndarray  # shape (5, 2)
    score: float


class FaceDetector:
    """YuNet face detector (OpenCV zoo). Lazy-loads the ONNX model."""

    def __init__(self, model_path: Optional[str] = None, input_size: Tuple[int, int] = (640, 640),
                 score_threshold: float = 0.6) -> None:
        self.model_path = model_path or os.path.join(default_model_dir(), YUNET_MODEL_NAME)
        self.input_size = input_size
        self.score_threshold = score_threshold
        self._net = None

    def _ensure_loaded(self) -> None:
        if self._net is None:
            if not os.path.isfile(self.model_path):
                raise ModelNotFoundError(
                    "YuNet model not found at %s. Run fetch_models.py to download it." % self.model_path
                )
            self._net = cv2.FaceDetectorYN.create(
                self.model_path, "", self.input_size, self.score_threshold
            )

    def detect(self, frame_bgr: np.ndarray) -> List[FaceDetection]:
        """Detect faces in a BGR frame. Returns detections sorted by score desc."""
        self._ensure_loaded()
        h, w = frame_bgr.shape[:2]
        self._net.setInputSize((w, h))
        _, faces = self._net.detect(frame_bgr)
        out: List[FaceDetection] = []
        if faces is None:
            return out
        for f in faces:
            x, y, bw, bh = (int(f[0]), int(f[1]), int(f[2]), int(f[3]))
            score = float(f[14])
            # YuNet landmark order: right eye, left eye, nose, right mouth, left mouth
            # ("right" = the person's right = viewer's left).
            pts = f[4:14].reshape(5, 2).astype(np.float32)
            out.append(FaceDetection(box=(x, y, bw, bh), landmarks=pts, score=score))
        out.sort(key=lambda d: d.score, reverse=True)
        return out


def largest_face(detections: Sequence[FaceDetection]) -> Optional[FaceDetection]:
    """Largest face by area, no prominence cutoff.

    Mirrors Glance's FaceRecognitionPipeline.largestFace: enrollment uses this
    to bypass the prominence filter, so a too-small face reads as
    "move closer" rather than "nobody there".
    """
    if not detections:
        return None
    return max(detections, key=lambda d: d.box[2] * d.box[3])


def select_dominant_face(
    detections: Sequence[FaceDetection],
    frame_shape: Tuple[int, int],
    previous_box: Optional[Tuple[int, int, int, int]] = None,
) -> Optional[FaceDetection]:
    """Pick the person actually at the camera, not a bystander.

    Mirrors Glance's FaceRecognitionPipeline.selectDominantFace: faces narrower
    than MINIMUM_PROMINENT_FACE_WIDTH of the frame are filtered out, then
    continuity with the previous frame's box (within
    CONTINUITY_DISTANCE_TOLERANCE, normalized coordinates) is preferred over
    raw largest-by-area, so two similarly-sized faces can't flip-flop the
    selection frame to frame.
    """
    fh, fw = frame_shape[:2]
    candidates = [d for d in detections if d.box[2] / float(fw) >= MINIMUM_PROMINENT_FACE_WIDTH]
    if not candidates:
        return None
    if previous_box is not None:
        px, py, pw, ph = previous_box
        pcx, pcy = (px + pw / 2) / fw, (py + ph / 2) / fh
        nearest = min(
            candidates,
            key=lambda d: math.hypot(
                (d.box[0] + d.box[2] / 2) / fw - pcx,
                (d.box[1] + d.box[3] / 2) / fh - pcy,
            ),
        )
        nx, ny, nw, nh = nearest.box
        if math.hypot((nx + nw / 2) / fw - pcx, (ny + nh / 2) / fh - pcy) < CONTINUITY_DISTANCE_TOLERANCE:
            return nearest
    return largest_face(candidates)


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def align_face(frame_bgr: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    """Warp the face to the canonical 112x112 ArcFace crop.

    ``landmarks`` must be in YuNet order (person-right eye first, i.e. the
    same order as the template: viewer-left, viewer-right, nose, ...).
    """
    if landmarks.shape != (5, 2):
        raise ValueError("expected 5 landmarks of shape (5, 2)")
    src = landmarks.astype(np.float32)
    mat, _ = cv2.estimateAffinePartial2D(src, ARCFACE_TEMPLATE_112, method=cv2.LMEDS)
    if mat is None:
        raise ValueError("could not estimate alignment transform")
    return cv2.warpAffine(frame_bgr, mat, (ALIGNED_SIZE, ALIGNED_SIZE), flags=cv2.INTER_LINEAR)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


class ArcFaceEmbedder:
    """ArcFace 512-d embedder via ONNX Runtime. Lazy-loads the model."""

    def __init__(self, model_path: Optional[str] = None) -> None:
        self.model_path = model_path or os.path.join(default_model_dir(), ARCFACE_MODEL_NAME)
        self._session = None
        self._input_name = None

    def _ensure_loaded(self) -> None:
        if self._session is None:
            if not os.path.isfile(self.model_path):
                raise ModelNotFoundError(
                    "ArcFace model not found at %s. Run fetch_models.py to download it." % self.model_path
                )
            import onnxruntime as ort

            sess = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
            self._input_name = sess.get_inputs()[0].name
            out_shape = sess.get_outputs()[0].shape
            if out_shape[-1] != EMBEDDING_DIM:
                raise ValueError(
                    "unexpected ArcFace output dim %s (expected %d)" % (out_shape, EMBEDDING_DIM)
                )
            self._session = sess

    @staticmethod
    def preprocess(aligned_bgr: np.ndarray) -> np.ndarray:
        """112x112 BGR -> (1, 3, 112, 112) float32, RGB, (x-127.5)/127.5."""
        if aligned_bgr.shape[:2] != (ALIGNED_SIZE, ALIGNED_SIZE):
            raise ValueError(
                "ArcFaceEmbedder expects a %dx%d aligned face, got %s. "
                "Run the face through align_face first." % (ALIGNED_SIZE, ALIGNED_SIZE, aligned_bgr.shape[:2])
            )
        rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        blob = (rgb - 127.5) / 127.5
        return np.transpose(blob, (2, 0, 1))[np.newaxis, ...]

    def embedding(self, aligned_bgr: np.ndarray) -> np.ndarray:
        """Return the L2-normalized 512-d embedding. Deterministic."""
        self._ensure_loaded()
        blob = self.preprocess(aligned_bgr)
        raw = self._session.run(None, {self._input_name: blob})[0].astype(np.float64).ravel()
        norm = np.linalg.norm(raw)
        if norm <= 0:
            raise ValueError("ArcFace model produced a zero embedding")
        return (raw / norm).astype(np.float32)

    def embedding_for_frame(self, frame_bgr: np.ndarray, detection: FaceDetection,
                            detector: Optional[FaceDetector] = None) -> np.ndarray:
        aligned = align_face(frame_bgr, detection.landmarks)
        return self.embedding(aligned)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom <= 0:
        return 0.0
    return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))


@dataclass
class IdentityTemplate:
    """One enrolled identity: a name and its sample embeddings."""

    name: str
    embeddings: List[np.ndarray]
    enabled: bool = True
    # Model that produced the embeddings. Mirrors Glance's FaceIdentity
    # modelIdentifier / isStale(comparedTo:): embeddings from a different
    # model must never match.
    model_id: str = MODEL_IDENTIFIER

    def is_stale(self, current_model_id: str = MODEL_IDENTIFIER) -> bool:
        return self.model_id != current_model_id

    def centroid(self) -> Optional[np.ndarray]:
        if not self.embeddings:
            return None
        c = np.mean(np.stack(self.embeddings), axis=0)
        n = np.linalg.norm(c)
        return (c / n).astype(np.float32) if n > 0 else None


@dataclass
class ScoredIdentity:
    name: str
    centroid_similarity: float
    max_sample_similarity: float
    is_stale: bool = False


@dataclass
class RecognitionResult:
    """One frame's recognition outcome — the values a Face Lab-style
    diagnostics console shows. Mirrors Glance's FaceRecognitionResult."""

    embedding: np.ndarray
    aligned_image: np.ndarray  # the 112x112 crop actually fed to the embedder
    alignment_tier: str        # always "5-point" (YuNet landmarks -> similarity warp)
    detection: FaceDetection
    scored: List[ScoredIdentity]


def quality_tier(quality: Optional[float]) -> str:
    """Mirrors Glance's FaceSample.qualityTier thresholds.

    "unrated" when no score exists (never counted as poor), "poor" below 0.4,
    "fair" below 0.5, "good" otherwise. Note: YuNet reports a detection
    confidence, not Vision's capture-quality metric — the manager should only
    feed genuine capture-quality scores here.
    """
    if quality is None:
        return "unrated"
    if quality < 0.4:
        return "poor"
    if quality < 0.5:
        return "fair"
    return "good"


def score_identities(embedding: np.ndarray, identities: Sequence[IdentityTemplate],
                     model_id: str = MODEL_IDENTIFIER) -> List[ScoredIdentity]:
    """Score one embedding against identities, centroid-similarity descending.

    Mirrors Glance's FaceRecognitionPipeline.score: stale identities are kept
    in the list (the diagnostics console shows them flagged); best_match is
    what excludes them from actually matching.
    """
    scored: List[ScoredIdentity] = []
    for ident in identities:
        if not ident.enabled or not ident.embeddings:
            continue
        centroid = ident.centroid()
        if centroid is None:
            continue
        centroid_sim = cosine_similarity(embedding, centroid)
        max_sim = max(cosine_similarity(embedding, e) for e in ident.embeddings)
        scored.append(ScoredIdentity(ident.name, centroid_sim, max_sim,
                                     is_stale=ident.is_stale(model_id)))
    scored.sort(key=lambda s: s.centroid_similarity, reverse=True)
    return scored


def best_match(scored: Sequence[ScoredIdentity], threshold: float,
               model_id: str = MODEL_IDENTIFIER) -> Optional[ScoredIdentity]:
    """Top identity clears the threshold on BOTH centroid and best-sample similarity.

    Mirrors Glance's FaceRecognitionPipeline.bestMatch exactly: the top
    identity must not be stale, and must clear the threshold on both measures.
    No runner-up margin check — the same person can be enrolled multiple times
    under different appearances, so two of their own profiles legitimately
    score close together.
    """
    if not scored:
        return None
    top = scored[0]
    if top.is_stale:
        return None
    if top.centroid_similarity >= threshold and top.max_sample_similarity >= threshold:
        return top
    return None


def recognize_frame(
    frame_bgr: np.ndarray,
    detector: FaceDetector,
    embedder: ArcFaceEmbedder,
    identities: Sequence[IdentityTemplate],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    previous_box: Optional[Tuple[int, int, int, int]] = None,
) -> Optional[RecognitionResult]:
    """Full recognition pipeline for one frame.

    Stage order mirrors Glance's FaceRecognitionPipeline.recognize:
    detect -> select dominant face (prominence + continuity) -> 5-point
    align -> embed -> score. Returns None when no prominent face is found.
    """
    detections = detector.detect(frame_bgr)
    face = select_dominant_face(detections, frame_bgr.shape, previous_box)
    if face is None:
        return None
    aligned = align_face(frame_bgr, face.landmarks)
    embedding = embedder.embedding(aligned)
    scored = score_identities(embedding, identities)
    return RecognitionResult(
        embedding=embedding,
        aligned_image=aligned,
        alignment_tier="5-point",
        detection=face,
        scored=scored,
    )
