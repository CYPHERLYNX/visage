"""
Anti-spoofing liveness analysis. Mirrors Glance's cue design and exceeds it
with a challenge-response round that defeats replayed video.

Cue vocabulary (mirrors Glance's LivenessCues):
  DENY cues (evidence of a spoof; any one firing fails the scan outright):
    - gloss_glare:      large flat specular highlight = glass/screen glare,
                        not skin's small scattered shine.
    - device_rectangle: a device-shaped rectangle (phone/tablet bezel) around
                        the face, via contour/quad analysis.
  CONFIRM cues (evidence of a real face; any one is enough; absence is never
  a failure in Light mode):
    - blink:            eye-openness dip-and-recover across frames.
    - head_parallax:    nose landmark displaces *relative to the eye centers*
                        across frames. A printed photo or held screen moved as
                        a rigid body cannot do this — the whole face moves
                        together, so relative displacement stays ~0.
    - lbp_texture:      uniform-LBP micro-texture liveness score.

CHALLENGE-RESPONSE (Heavy mode, borderline scans): the engine emits a random
prompt ("turn your head left", ...); the landmark yaw trajectory must match a
real 3D head turn within 3 seconds. A replayed video cannot know which
challenge is coming, which is what defeats it — the attack Glance's README
admits it cannot reliably stop.

Strictness:
  light: deny cues only ("confirmed unless proven wrong"). Never blocks a
         user who sits still.
  heavy: deny cues + at least one confirm cue, else challenge-response.
  off:   no liveness at all.

A cue reading with confidence 0 is always an abstention, never a zero — a cue
that cannot see anything must not be able to convict or acquit.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Cue vocabulary
# ---------------------------------------------------------------------------

DENY = "deny"
CONFIRM = "confirm"

CUE_ROLES = {
    "gloss_glare": DENY,
    "device_rectangle": DENY,
    "blink": CONFIRM,
    "head_parallax": CONFIRM,
    "lbp_texture": CONFIRM,
}

CUE_EXPLANATIONS = {
    "gloss_glare": "Large flat specular highlight - glass/screen glare rather than skin's small scattered shine.",
    "device_rectangle": "A device-shaped rectangle overlaps the face - a phone or tablet held up.",
    "blink": "Eye openness dipped and recovered - a photo cannot blink.",
    "head_parallax": "Nose displaced relative to the eye centers - rigid photo motion cannot parallax.",
    "lbp_texture": "Skin micro-texture present - prints and screens recapture differently.",
}


@dataclass
class CueReading:
    """One cue's latest reading. confidence 0 = abstention."""

    level: float       # 0..1 strength in the direction the cue argues
    confidence: float  # 0..1; 0 means "I saw nothing"
    detail: str = ""

    NONE = None  # set below


CueReading.NONE = CueReading(level=0.0, confidence=0.0)


@dataclass
class LivenessTuning:
    """Fire thresholds per cue. Defaults seeded from Glance's LivenessTuning
    (real-device observations); retune from measured data."""

    gloss_level: float = 0.04
    gloss_frames: int = 3
    device_level: float = 0.15
    device_frames: int = 3
    blink_frames: int = 1          # a blink is a discrete dip-and-recover event
    parallax_level: float = 0.6    # level = min(1, nose-offset-range / 0.20)
    parallax_frames: int = 2
    lbp_level: float = 0.5
    lbp_frames: int = 2
    light_mode_minimum_frames: int = 3
    window_seconds: float = 2.0

    def level_for(self, cue: str) -> float:
        return {
            "gloss_glare": self.gloss_level,
            "device_rectangle": self.device_level,
            "blink": 0.5,
            "head_parallax": self.parallax_level,
            "lbp_texture": self.lbp_level,
        }[cue]

    def frames_for(self, cue: str) -> int:
        return {
            "gloss_glare": self.gloss_frames,
            "device_rectangle": self.device_frames,
            "blink": self.blink_frames,
            "head_parallax": self.parallax_frames,
            "lbp_texture": self.lbp_frames,
        }[cue]


# ---------------------------------------------------------------------------
# DENY cue 1: screen glare / specular highlights
# ---------------------------------------------------------------------------

SPECULAR_LUMA_FLOOR = 235.0
SPECULAR_CHROMA_TOLERANCE = 10.0
CLUSTER_GRID = 8


def _ramp(value: float, floor: float, ceiling: float) -> float:
    """Mirrors Glance's LivenessCues.ramp: linear 0..1 ramp between floor and ceiling."""
    return float(min(max((value - floor) / max(ceiling - floor, 0.0001), 0.0), 1.0))


@dataclass
class GlareSample:
    """Mirrors Glance's GlareSample: the raw measurements behind the cue."""
    crop_pixel_width: float
    specular_fraction: float
    specular_cluster_ratio: float


def extract_glare_sample(face_crop_bgr: np.ndarray) -> Optional[GlareSample]:
    """Pixel-domain specular measurement. Mirrors Glance's GlareCueExtractor."""
    if face_crop_bgr is None or face_crop_bgr.size == 0:
        return None
    h, w = face_crop_bgr.shape[:2]
    if h == 0 or w == 0:
        return None
    b, g, r = (face_crop_bgr[:, :, i].astype(np.float32) for i in range(3))
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0
    specular = (
        (luma >= SPECULAR_LUMA_FLOOR)
        & (np.abs(cb - 128.0) <= SPECULAR_CHROMA_TOLERANCE)
        & (np.abs(cr - 128.0) <= SPECULAR_CHROMA_TOLERANCE)
    )
    total = int(specular.sum())
    pixel_count = h * w
    if total == 0:
        return GlareSample(crop_pixel_width=float(w), specular_fraction=0.0,
                           specular_cluster_ratio=0.0)
    ys, xs = np.nonzero(specular)
    grid = np.zeros((CLUSTER_GRID, CLUSTER_GRID), dtype=np.int64)
    gy = np.minimum(CLUSTER_GRID - 1, (ys * CLUSTER_GRID // h)).astype(int)
    gx = np.minimum(CLUSTER_GRID - 1, (xs * CLUSTER_GRID // w)).astype(int)
    np.add.at(grid, (gy, gx), 1)
    return GlareSample(
        crop_pixel_width=float(w),
        specular_fraction=float(total) / float(pixel_count),
        specular_cluster_ratio=float(grid.max()) / float(total),
    )


def glare_reading(face_crop_bgr: np.ndarray) -> CueReading:
    """Gloss/glare deny cue. Level formula mirrors Glance's LivenessCues.glossGlare exactly:

    fractionScore = ramp(specularFraction, 0.01, 0.08)
    clusterFactor = ramp(specularClusterRatio, 0.3, 1.0)
    level = fractionScore * (0.3 + 0.7 * clusterFactor)
    confidence = ramp(cropPixelWidth, 50, 130)

    Skin gives many small scattered specular points; glass gives one big flat
    blob — fraction alone would fire on a bright forehead, so it is gated by
    how concentrated the glare is.
    """
    sample = extract_glare_sample(face_crop_bgr)
    if sample is None:
        return CueReading.NONE
    fraction_score = _ramp(sample.specular_fraction, 0.01, 0.08)
    cluster_factor = _ramp(sample.specular_cluster_ratio, 0.3, 1.0)
    level = fraction_score * (0.3 + 0.7 * cluster_factor)
    confidence = _ramp(sample.crop_pixel_width, 50, 130)
    return CueReading(
        level=level, confidence=confidence,
        detail="specular_fraction=%.4f cluster_ratio=%.3f crop_px=%.0f"
               % (sample.specular_fraction, sample.specular_cluster_ratio,
                  sample.crop_pixel_width),
    )


# ---------------------------------------------------------------------------
# DENY cue 2: device rectangle (phone/tablet bezel around the face)
# ---------------------------------------------------------------------------


def _quad_angles_ok(quad: np.ndarray, tolerance_deg: float = 30.0) -> bool:
    pts = quad.reshape(4, 2).astype(np.float64)
    for i in range(4):
        a = pts[i] - pts[(i - 1) % 4]
        b = pts[(i + 1) % 4] - pts[i]
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return False
        ang = math.degrees(math.acos(float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))))
        if abs(ang - 90.0) > tolerance_deg:
            return False
    return True


def device_rectangle_reading(frame_bgr: np.ndarray, face_box: Tuple[int, int, int, int]) -> CueReading:
    """Find a device-shaped quadrilateral overlapping the face.

    Uses Canny edges + contour approximation (Glance uses Vision rectangle
    detection; same intent). Only ever produces evidence of spoofing.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return CueReading.NONE
    fh, fw = frame_bgr.shape[:2]
    fx, fy, fbw, fbh = face_box
    face_area = float(fbw * fbh)
    if face_area <= 0:
        return CueReading.NONE

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 180)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    frame_area = float(fh * fw)
    best = None  # (overlap, quad_area, quad)
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < 0.15 * frame_area:      # Glance: minimumSize 0.15 of image area
            continue
        peri = cv2.arcLength(cnt, True)
        quad = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        x, y, w, h = cv2.boundingRect(quad)
        if h == 0:
            continue
        aspect = float(w) / float(h)
        if not (0.35 <= aspect <= 1.0):   # phone-portrait .. near-square tablet
            continue
        if not _quad_angles_ok(quad):
            continue
        # Overlap with the face box.
        ix0, iy0 = max(x, fx), max(y, fy)
        ix1, iy1 = min(x + w, fx + fbw), min(y + h, fy + fbh)
        inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        overlap = inter / face_area
        if overlap <= 0.05:
            continue
        if best is None or area > best[1]:
            best = (overlap, area, (x, y, w, h))

    if best is None:
        return CueReading(level=0.0, confidence=1.0, detail="no device quad")
    overlap, area, _ = best
    # Level semantics mirror Glance's deviceDetected: the raw overlap fraction
    # of the face box covered by the device rectangle, used directly.
    # (Detection itself uses contour/quad analysis instead of Vision.)
    level = float(min(max(overlap, 0.0), 1.0))
    return CueReading(
        level=level, confidence=0.9,
        detail="quad overlap=%.3f area_ratio=%.2f" % (overlap, area / face_area),
    )


# ---------------------------------------------------------------------------
# CONFIRM cue 1: blink (eye-region intensity analysis across frames)
# ---------------------------------------------------------------------------


def eye_openness(eye_roi_gray: np.ndarray) -> Tuple[float, float]:
    """Estimate how open an eye is from a grayscale eye ROI.

    Returns (openness 0..1, confidence). Measures the vertical spread of the
    dark (iris/pupil/lash) mass: an open eye shows a tall dark blob, a closed
    eye a thin dark line. Confidence 0 when the ROI is unusable.
    """
    if eye_roi_gray is None or eye_roi_gray.size == 0:
        return 0.0, 0.0
    h, w = eye_roi_gray.shape[:2]
    if h < 8 or w < 8:
        return 0.0, 0.0
    _, dark = cv2.threshold(eye_roi_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = dark == 0
    if dark.sum() < 4:
        return 0.0, 0.0
    rows = np.nonzero(dark.any(axis=1))[0]
    if len(rows) < 2:
        return 0.0, 0.3
    lo, hi = np.percentile(rows, [5, 95])
    openness = float(np.clip((hi - lo) / float(h), 0.0, 1.0))
    return openness, 1.0


class BlinkTracker:
    """Blink as a windowed dip-and-recover event, mirroring Glance's
    LivenessScoring.blinkDynamics:

    baseline = max openness in the window; a blink is min/baseline < 0.65 with
    a recovery above 0.7 * baseline within 3 frames on BOTH sides of the dip;
    at least 4 samples required.

    One difference: Glance feeds Vision's eye aspect ratio; YuNet exposes only
    eye centers, so this tracker feeds the intensity-based openness proxy
    from eye_openness() instead. The dip/recover shape is what matters, and
    that is preserved.
    """

    def __init__(self, window: int = 60, dip_ratio: float = 0.65,
                 recovery_ratio: float = 0.7, recovery_radius: int = 3) -> None:
        self._window: Deque[float] = deque(maxlen=window)
        self.dip_ratio = dip_ratio
        self.recovery_ratio = recovery_ratio
        self.recovery_radius = recovery_radius
        self.fired = False

    def reset(self) -> None:
        self._window.clear()
        self.fired = False

    def update(self, openness: float, confidence: float) -> CueReading:
        if confidence <= 0:
            return CueReading.NONE
        self._window.append(float(openness))
        ears = list(self._window)
        if len(ears) < 4:
            return CueReading(level=0.0, confidence=0.4, detail="warming up")
        baseline = max(ears)
        if baseline <= 0:
            return CueReading.NONE
        min_ear = min(ears)
        dip_index = ears.index(min_ear)
        dip_ratio = min_ear / baseline
        recovered = (
            dip_index > 0
            and dip_index < len(ears) - 1
            and any(e / baseline > self.recovery_ratio
                    for e in ears[max(0, dip_index - self.recovery_radius): dip_index])
            and any(e / baseline > self.recovery_ratio
                    for e in ears[dip_index + 1: dip_index + 1 + self.recovery_radius])
        )
        if dip_ratio < self.dip_ratio and recovered:
            self.fired = True
            return CueReading(level=1.0, confidence=0.9,
                              detail="blink event (dip ratio %.2f)" % dip_ratio)
        return CueReading(level=0.0, confidence=0.8,
                          detail="no blink (dip ratio %.2f)" % dip_ratio)


def eye_roi(frame_gray: np.ndarray, eye_xy: np.ndarray, iod: float) -> Optional[np.ndarray]:
    """Crop a square ROI around an eye landmark, sized from the interocular distance."""
    if iod <= 0:
        return None
    side = int(max(16, iod * 0.55))
    x, y = int(eye_xy[0]), int(eye_xy[1])
    x0, y0 = max(0, x - side // 2), max(0, y - side // 2)
    roi = frame_gray[y0: y0 + side, x0: x0 + side]
    return roi if roi.size else None


# ---------------------------------------------------------------------------
# CONFIRM cue 2: head-turn parallax (nose vs eye centers across frames)
# ---------------------------------------------------------------------------


class ParallaxTracker:
    """Nose-offset parallax: o_t = (nose_x - eye_mid_x) / iod.

    Under rigid motion of a photo/screen the whole face translates together,
    so o_t stays ~constant. A real head turn moves the nose relative to the
    eyes. Fires when the range of o_t across the window proves 3D motion.
    """

    def __init__(self, window: int = 60, fire_range: float = 0.12) -> None:
        self._offsets: Deque[float] = deque(maxlen=window)
        self.fire_range = fire_range
        self.fired = False

    def reset(self) -> None:
        self._offsets.clear()
        self.fired = False

    def update(self, landmarks: np.ndarray) -> CueReading:
        """Landmarks: (5,2) in template order (viewer-left eye first)."""
        if landmarks is None or landmarks.shape != (5, 2):
            return CueReading.NONE
        eye_l, eye_r, nose = landmarks[0], landmarks[1], landmarks[2]
        iod = float(abs(eye_r[0] - eye_l[0]))
        if iod < 1.0:
            return CueReading.NONE
        mid_x = float((eye_l[0] + eye_r[0]) / 2.0)
        self._offsets.append((float(nose[0]) - mid_x) / iod)
        if len(self._offsets) < 4:
            return CueReading(level=0.0, confidence=0.4, detail="warming up")
        arr = np.array(self._offsets)
        rng = float(arr.max() - arr.min())
        level = float(np.clip(rng / 0.20, 0.0, 1.0))
        if rng >= self.fire_range:
            self.fired = True
            return CueReading(level=level, confidence=0.9,
                              detail="nose offset range=%.3f iod" % rng)
        return CueReading(level=level, confidence=0.9,
                          detail="nose offset range=%.3f iod" % rng)


# ---------------------------------------------------------------------------
# CONFIRM cue 3: LBP texture liveness
# ---------------------------------------------------------------------------

_LBP_UNIFORM_TABLE = None


def _lbp_uniform_table() -> np.ndarray:
    global _LBP_UNIFORM_TABLE
    if _LBP_UNIFORM_TABLE is None:
        table = np.zeros(256, dtype=np.int64)
        for code in range(256):
            bits = [(code >> i) & 1 for i in range(8)]
            transitions = sum(bits[i] != bits[(i + 1) % 8] for i in range(8))
            if transitions <= 2:
                table[code] = sum(bits)  # 0..8
            else:
                table[code] = 58  # non-uniform bin
        _LBP_UNIFORM_TABLE = table
    return _LBP_UNIFORM_TABLE


def uniform_lbp_histogram(gray: np.ndarray) -> np.ndarray:
    """59-bin uniform LBP histogram (P=8, R=1), normalized."""
    h, w = gray.shape[:2]
    img = gray.astype(np.int32)
    c = img[1:-1, 1:-1]
    code = (
        ((img[0:-2, 0:-2] >= c) << 7) | ((img[0:-2, 1:-1] >= c) << 6)
        | ((img[0:-2, 2:] >= c) << 5) | ((img[1:-1, 2:] >= c) << 4)
        | ((img[2:, 2:] >= c) << 3) | ((img[2:, 1:-1] >= c) << 2)
        | ((img[2:, 0:-2] >= c) << 1) | (img[1:-1, 0:-2] >= c)
    ).astype(np.uint8)
    table = _lbp_uniform_table()
    hist = np.bincount(table[code.ravel()], minlength=59).astype(np.float64)
    s = hist.sum()
    return hist / s if s > 0 else hist


def lbp_texture_reading(face_gray: np.ndarray) -> CueReading:
    """Micro-texture liveness: live skin shows rich high-frequency texture;
    recaptured prints/screens are blurred or dither-regular.

    Score combines uniform-LBP histogram entropy with Laplacian sharpness.
    """
    if face_gray is None or face_gray.size == 0:
        return CueReading.NONE
    h, w = face_gray.shape[:2]
    if h < 32 or w < 32:
        return CueReading.NONE
    hist = uniform_lbp_histogram(face_gray)
    nz = hist[hist > 0]
    entropy = float(-(nz * np.log2(nz)).sum()) / math.log2(59)  # 0..1
    sharp = float(cv2.Laplacian(face_gray, cv2.CV_64F).var())
    sharp_n = float(np.clip(sharp / 400.0, 0.0, 1.0))
    level = float(np.clip(0.6 * entropy + 0.4 * sharp_n, 0.0, 1.0))
    return CueReading(level=level, confidence=0.85,
                      detail="lbp_entropy=%.3f sharp=%.1f" % (entropy, sharp))


# ---------------------------------------------------------------------------
# Challenge-response (defeats replayed video)
# ---------------------------------------------------------------------------

CHALLENGES = {
    "turn_left": "Turn your head left",
    "turn_right": "Turn your head right",
    "look_up": "Look up",
    "look_down": "Look down",
    "blink": "Blink twice",
}
CHALLENGE_TIMEOUT_S = 3.0


def pick_challenge(rng: Optional[random.Random] = None) -> Tuple[str, str]:
    """Pick a random challenge. Returns (kind, human-readable prompt)."""
    rng = rng or random.Random()
    kind = rng.choice(sorted(CHALLENGES.keys()))
    return kind, CHALLENGES[kind]


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    dx, dy = rx - rx.mean(), ry - ry.mean()
    denom = math.sqrt((dx ** 2).sum() * (dy ** 2).sum())
    return float((dx * dy).sum() / denom) if denom > 0 else 0.0


class ChallengeVerifier:
    """Verifies a 3-second landmark trajectory against the issued challenge.

    Yaw signal o_t = (nose_x - eye_mid_x) / iod; pitch signal
    p_t = (nose_y - eye_mid_y) / iod. A real head turn moves the nose
    *relative to the eyes*; a replayed video is either static or translates
    rigidly, both of which keep these signals ~constant.
    """

    def __init__(self, kind: str, min_displacement: float = 0.10) -> None:
        if kind not in CHALLENGES:
            raise ValueError("unknown challenge kind: %r" % kind)
        self.kind = kind
        self.prompt = CHALLENGES[kind]
        self.min_displacement = min_displacement
        self._yaws: List[float] = []
        self._pitches: List[float] = []
        self._blinks = 0
        self._blink_tracker = BlinkTracker()

    def observe(self, landmarks: np.ndarray, eye_openness: Optional[float] = None,
                eye_confidence: float = 0.0) -> None:
        if landmarks is not None and landmarks.shape == (5, 2):
            eye_l, eye_r, nose = landmarks[0], landmarks[1], landmarks[2]
            iod = float(abs(eye_r[0] - eye_l[0]))
            if iod >= 1.0:
                mid_x = float((eye_l[0] + eye_r[0]) / 2.0)
                mid_y = float((eye_l[1] + eye_r[1]) / 2.0)
                self._yaws.append((float(nose[0]) - mid_x) / iod)
                self._pitches.append((float(nose[1]) - mid_y) / iod)
        if eye_openness is not None:
            reading = self._blink_tracker.update(eye_openness, eye_confidence)
            if reading.level >= 1.0:
                self._blinks += 1

    def _directional(self, signal: List[float], want_sign: int) -> Tuple[bool, str]:
        if len(signal) < 4:
            return False, "too few landmark samples"
        s = np.array(signal)
        t = np.arange(len(s))
        displacement = float(s[-1] - s[0])
        total_range = float(s.max() - s.min())
        rho = _spearman(t, s)
        ok = (
            (displacement * want_sign) >= self.min_displacement
            and total_range >= self.min_displacement
            and (rho * want_sign) >= 0.5
        )
        return ok, "displacement=%.3f range=%.3f rho=%.2f" % (displacement, total_range, rho)

    def verify(self) -> Tuple[bool, str]:
        """Returns (passed, human-readable detail)."""
        if self.kind == "turn_left":
            return self._directional(self._yaws, want_sign=-1)
        if self.kind == "turn_right":
            return self._directional(self._yaws, want_sign=+1)
        if self.kind == "look_up":
            return self._directional(self._pitches, want_sign=-1)
        if self.kind == "look_down":
            return self._directional(self._pitches, want_sign=+1)
        if self.kind == "blink":
            ok = self._blinks >= 2
            return ok, "blinks=%d (need 2)" % self._blinks
        return False, "unknown challenge"


# ---------------------------------------------------------------------------
# Session: rolling-window decision over all cues
# ---------------------------------------------------------------------------

# User-facing denial reasons, mirroring Glance's LivenessDecision.denialReason.
DENIAL_REASONS = {
    "gloss_glare": "Screen glare detected \u2014 this looks like a photo on a display.",
    "device_rectangle": "A device-shaped rectangle was detected around the face \u2014 "
                        "this looks like a photo or screen.",
}


@dataclass
class LivenessDecision:
    verdict: str  # "live" | "spoof" | "pending" | "needs_challenge"
    detail: str = ""
    fired_cues: List[str] = field(default_factory=list)
    denied_by: Optional[str] = None  # the deny cue, if verdict == "spoof"


@dataclass
class FrameDiagnostics:
    """One frame's raw liveness measurements — the values a Face Lab-style
    diagnostics console shows. Field names mirror Glance's LivenessFrame."""
    interocular_distance: Optional[float] = None
    nose_offset_ratio: Optional[float] = None  # (nose.x - eye_mid.x) / iod
    eye_openness_left: Optional[float] = None
    eye_openness_right: Optional[float] = None
    device_overlap_fraction: Optional[float] = None
    glare_specular_fraction: Optional[float] = None
    glare_specular_cluster_ratio: Optional[float] = None
    glare_crop_pixel_width: Optional[float] = None


class LivenessSession:
    """Accumulates per-frame cue readings and decides: live / spoof /
    pending / needs_challenge.

    Mirrors Glance's LivenessEvaluator: firing is LATCHED — a cue that has
    fired stays fired for the rest of the scan — and frame counting is
    cumulative, not consecutive (forgiving of one-frame dropouts). Deny cues
    override confirm cues unconditionally. A confirm cue's absence is never a
    failure in Light mode.

    Extension beyond Glance: "needs_challenge". Glance's heavy mode stays
    pending until its scan timeout; Visage instead escalates a borderline scan
    to a challenge-response round, which is what defeats replayed video.
    """

    def __init__(self, strictness: str = "heavy", fps: float = 30.0,
                 tuning: Optional[LivenessTuning] = None) -> None:
        if strictness not in ("light", "heavy", "off"):
            raise ValueError("strictness must be light|heavy|off")
        self.strictness = strictness
        self.fps = fps
        self.tuning = tuning or LivenessTuning()
        window = max(4, int(self.tuning.window_seconds * fps))
        self._window = window
        self._latest: Dict[str, CueReading] = {cue: CueReading.NONE for cue in CUE_ROLES}
        self._frames_counted: Dict[str, int] = {cue: 0 for cue in CUE_ROLES}
        self._latched: Dict[str, bool] = {cue: False for cue in CUE_ROLES}
        self._blink = BlinkTracker(window=window)
        self._parallax = ParallaxTracker(window=window)
        self.frames_seen = 0
        self.last_frame = FrameDiagnostics()

    # -- per-frame update -------------------------------------------------

    def _observe(self, cue: str, reading: CueReading) -> None:
        self._latest[cue] = reading
        if reading.confidence > 0 and reading.level >= self.tuning.level_for(cue):
            self._frames_counted[cue] += 1
            if self._frames_counted[cue] >= self.tuning.frames_for(cue):
                self._latched[cue] = True

    def update(self, frame_bgr: np.ndarray, face_box: Tuple[int, int, int, int],
               landmarks: np.ndarray, face_crop_bgr: Optional[np.ndarray] = None) -> None:
        """Run every cue extractor on one frame's observations."""
        self.frames_seen += 1
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        if face_crop_bgr is None:
            x, y, w, h = face_box
            face_crop_bgr = frame_bgr[max(0, y): y + h, max(0, x): x + w]

        diag = FrameDiagnostics()

        glare_sample = extract_glare_sample(face_crop_bgr)
        self._observe("gloss_glare", glare_reading(face_crop_bgr))
        if glare_sample is not None:
            diag.glare_specular_fraction = glare_sample.specular_fraction
            diag.glare_specular_cluster_ratio = glare_sample.specular_cluster_ratio
            diag.glare_crop_pixel_width = glare_sample.crop_pixel_width

        dev_reading = device_rectangle_reading(frame_bgr, face_box)
        self._observe("device_rectangle", dev_reading)
        # The device cue's level IS the overlap fraction (Glance semantics).
        diag.device_overlap_fraction = dev_reading.level if dev_reading.confidence > 0 else None

        # Blink from both eye ROIs (average openness).
        opens = []
        iod = 0.0
        if landmarks is not None and landmarks.shape == (5, 2):
            iod = float(abs(landmarks[1][0] - landmarks[0][0]))
            diag.interocular_distance = iod if iod >= 1.0 else None
            mid_x = float((landmarks[0][0] + landmarks[1][0]) / 2.0)
            if iod >= 1.0:
                diag.nose_offset_ratio = (float(landmarks[2][0]) - mid_x) / iod
            for ei in (0, 1):
                roi = eye_roi(gray, landmarks[ei], iod)
                if roi is not None:
                    o, c = eye_openness(roi)
                    if c > 0:
                        opens.append(o)
                        if ei == 0:
                            diag.eye_openness_left = o
                        else:
                            diag.eye_openness_right = o
        if opens:
            self._observe("blink", self._blink.update(float(np.mean(opens)), 1.0))
        else:
            self._observe("blink", CueReading.NONE)

        self._observe("head_parallax", self._parallax.update(landmarks))

        face_gray = cv2.cvtColor(face_crop_bgr, cv2.COLOR_BGR2GRAY) if face_crop_bgr.size else None
        self._observe("lbp_texture",
                      lbp_texture_reading(face_gray) if face_gray is not None else CueReading.NONE)
        self.last_frame = diag

    # -- decision ----------------------------------------------------------

    def _cue_fired(self, cue: str) -> bool:
        return self._latched[cue]

    def fired_cues(self) -> List[str]:
        return [c for c in CUE_ROLES if self._latched[c]]

    def decision(self) -> LivenessDecision:
        if self.strictness == "off":
            return LivenessDecision("live", "liveness off")

        fired = self.fired_cues()
        for cue in CUE_ROLES:
            if CUE_ROLES[cue] == DENY and self._latched[cue]:
                return LivenessDecision(
                    "spoof",
                    DENIAL_REASONS.get(cue, "Liveness check failed."),
                    fired_cues=fired,
                    denied_by=cue,
                )

        if self.strictness == "light":
            if self.frames_seen < self.tuning.light_mode_minimum_frames:
                return LivenessDecision("pending", "waiting for deny cues to get a fair chance")
            return LivenessDecision("live", "no deny cue fired", fired_cues=fired)

        # heavy
        confirm_fired = [c for c in fired if CUE_ROLES[c] == CONFIRM]
        if confirm_fired:
            return LivenessDecision(
                "live",
                "confirm cue fired: %s" % ", ".join(confirm_fired),
                fired_cues=fired,
            )
        if self.frames_seen >= self._window:
            return LivenessDecision("needs_challenge",
                                    "borderline: no confirm cue in the full window",
                                    fired_cues=fired)
        return LivenessDecision("pending", "waiting for confirm evidence", fired_cues=fired)

    def snapshot(self) -> Dict:
        """Face Lab-style snapshot: decision, mode, frame count, and per-cue
        state (latest reading, cumulative frames counted, latched fired flag,
        and 0..1 progress toward firing). Mirrors Glance's LivenessSnapshot."""
        cues = {}
        for cue in CUE_ROLES:
            reading = self._latest[cue]
            need = self.tuning.frames_for(cue)
            counted = self._frames_counted[cue]
            cues[cue] = {
                "level": reading.level,
                "confidence": reading.confidence,
                "detail": reading.detail,
                "frames_counted": counted,
                "fired": self._latched[cue],
                "progress": min(1.0, counted / need) if need > 0 else (1.0 if self._latched[cue] else 0.0),
                "role": CUE_ROLES[cue],
                "explanation": CUE_EXPLANATIONS[cue],
            }
        d = self.decision()
        return {
            "decision": d.verdict,
            "decision_detail": d.detail,
            "denied_by": d.denied_by,
            "mode": self.strictness,
            "frame_count": self.frames_seen,
            "cues": cues,
        }

    def new_challenge(self, rng: Optional[random.Random] = None) -> ChallengeVerifier:
        kind, _ = pick_challenge(rng)
        return ChallengeVerifier(kind)
