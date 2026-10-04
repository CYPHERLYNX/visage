"""Boundary between Visage Manager (UI) and the Visage engine.

The engine worker owns ``../engine/`` (a real Python package: enroll,
vault, identities, recognition, liveness, pipe_protocol, visage_engine).
This module is the *only* place that imports it, so the UI never depends
on engine internals directly.

Two modes:
- Real engine present (Windows, models + deps installed): the UI drives
  the actual YuNet/ArcFace pipeline through a strict, validated adapter.
- Anything missing: every capability degrades to a clearly-labelled
  stand-in (simulated session, in-memory store) instead of crashing.

Security rules enforced here (see also cp/TRUST_MODEL.md):
- Every dict coming back from engine code is strictly validated —
  whitelisted fields, type checks, range checks, size caps. Malformed
  data becomes a plain error outcome, never a crash, never raw text
  in the UI.
- The password is never logged, never shown, and on non-Windows the
  vault refuses to use its dev-only fallback from this user-facing app.

Nothing here ever touches the network.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import math
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .errors import (
    CameraUnavailableError,
    EngineUnavailableError,
    VisageError,
    StoreError,
    VaultUnavailableError,
)

log = logging.getLogger("visage")

# ---------------------------------------------------------------------------
# Shared data shapes (stable regardless of which implementation is active)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnrollmentPose:
    """One guided head direction, in Glance's display order."""
    key: str                 # UI key, e.g. "topLeft"
    instruction: str         # what the wizard tells the user
    engine_key: str          # engine enroll.DIRECTIONS key, e.g. "up_left"
    compass_angle: Optional[float]  # 0 = up, clockwise; None for center


ENROLLMENT_POSES: tuple[EnrollmentPose, ...] = (
    EnrollmentPose("center", "Look straight at the camera", "center", None),
    EnrollmentPose("left", "Turn your head slightly left", "left", 270),
    EnrollmentPose("topLeft", "Turn your head to the top left", "up_left", 315),
    EnrollmentPose("top", "Turn your head slightly up", "up", 0),
    EnrollmentPose("topRight", "Turn your head to the top right", "up_right", 45),
    EnrollmentPose("right", "Turn your head slightly right", "right", 90),
    EnrollmentPose("bottomRight", "Turn your head to the bottom right", "down_right", 135),
    EnrollmentPose("bottom", "Turn your head slightly down", "down", 180),
    EnrollmentPose("bottomLeft", "Turn your head to the bottom left", "down_left", 225),
)

POSE_BY_ENGINE_KEY = {p.engine_key: p for p in ENROLLMENT_POSES}

SAMPLES_PER_POSE = 2  # matches Glance's samplesPerPose

# Hard caps for anything the engine hands back (strict-parsing rule).
_MAX_EMBEDDING_DIM = 4096
_MAX_EMBEDDINGS_PER_IDENTITY = 512
_MAX_NAME_LEN = 120


@dataclass
class FrameOutcome:
    """Result of feeding one camera frame to an enrollment session."""
    kind: str  # no_face | too_dark | too_far | hold_still | excessive_turn |
               # pose_progress | pose_done | complete | error
    message: str = ""          # user-facing feedback for this frame
    pose_index: int = 0
    captured_for_pose: int = 0
    overall_progress: float = 0.0  # 0..1 across all poses
    detail: str = ""           # extra context (never shown raw)


@dataclass
class EnrollmentSample:
    embedding: list[float]
    pose: str                  # UI pose key, e.g. "topLeft"
    quality: Optional[float]
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Identity:
    id: str                    # engine identities are name-keyed; id == name there
    name: str
    samples: list[EnrollmentSample] = field(default_factory=list)
    model_identifier: str = "arcface"
    embedding_dimension: int = 512
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_enabled: bool = True

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def poses_covered(self) -> list[str]:
        seen: list[str] = []
        for s in self.samples:
            if s.pose not in seen:
                seen.append(s.pose)
        return seen

    @property
    def average_quality(self) -> Optional[float]:
        qs = [s.quality for s in self.samples if s.quality is not None]
        return sum(qs) / len(qs) if qs else None


@dataclass(frozen=True)
class CameraInfo:
    id: str
    label: str


@dataclass(frozen=True)
class LivenessCueState:
    key: str
    title: str
    role: str           # "deny" | "confirm"
    explanation: str
    level: float        # 0..1 current reading
    confidence: float   # 0..1; 0 means the cue abstains
    threshold: float
    frames_needed: int
    fired: bool


# The five cues, mirroring Glance's LivenessCue set (titles and
# explanations quoted from the reference).
CUE_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    ("glossGlare", "Gloss/glare", "deny",
     "Large flat specular highlight — glass/screen glare rather than skin's small scattered shine."),
    ("deviceDetected", "Device detected", "deny",
     "A device-shaped rectangle overlaps the face — a phone or tablet held up."),
    ("flatVs3D", "Flat vs 3D", "confirm",
     "Held-out nose points miss the plane fit — the face has real depth."),
    ("depthPose", "Depth/pose", "confirm",
     "Nose offset tracks head yaw — the nose sits off the eye plane, so this isn't flat."),
    ("blink", "Blink", "confirm",
     "Eye aspect ratio dipped and recovered — a photo cannot blink."),
)

# Engine verdict reasons -> Apple-grade wizard copy.
# (would Apple ship this? calm, plain, no jargon, no raw errors)
# Keys must match engine/enroll.py's FrameVerdict reason strings exactly;
# the bridge looks them up verbatim and falls back to generic copy.
VERDICT_COPY = {
    # Legacy human-readable reasons (older engine builds).
    "No face visible": ("no_face", "Position your face in the frame"),
    "Just one face at a time, please": ("no_face", "Just one face at a time, please"),
    "Move a little closer": ("too_far", "Move a little closer"),
    "Move into view": ("too_far", "Move into the frame"),
    "Hold still for a moment": ("hold_still", "Hold still"),
    "Please open your eyes": ("hold_still", "Open your eyes"),
    "Having trouble with this frame. Try again.": ("error", "That frame didn't work — hold still."),
    # Machine-readable reasons from the Glance-faithful session.
    "no_face": ("no_face", "Position your face in the frame"),
    "multiple_faces": ("no_face", "Just one face at a time, please"),
    "too_far": ("too_far", "Move a little closer"),
    "hold_still": ("hold_still", "Hold still"),
    "eyes_closed": ("hold_still", "Open your eyes"),
    "settling": ("pose_progress", ""),
    "awaiting_pose": ("pose_progress", ""),
    "excessive_left": ("excessive_turn", "Too far left"),
    "excessive_right": ("excessive_turn", "Too far right"),
    "excessive_up": ("excessive_turn", "Too far up"),
    "excessive_down": ("excessive_turn", "Too far down"),
    "frame_error": ("error", "That frame didn't work — hold still."),
    "complete": ("complete", "Face captured"),
}


# ---------------------------------------------------------------------------
# Engine package loading
# ---------------------------------------------------------------------------

_ENGINE_PKG_NAME = "visage_engine_pkg"


def _engine_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "engine"


def _load_engine_package() -> Optional[Any]:
    """Import ../engine as a real package (relative imports keep working).

    Returns None when anything is missing — expected on dev machines
    without the Windows-only dependencies.
    """
    if _ENGINE_PKG_NAME in sys.modules:
        return sys.modules[_ENGINE_PKG_NAME]
    init = _engine_dir() / "__init__.py"
    if not init.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            _ENGINE_PKG_NAME, init,
            submodule_search_locations=[str(_engine_dir())])
        if spec is None or spec.loader is None:
            return None
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[_ENGINE_PKG_NAME] = pkg
        spec.loader.exec_module(pkg)
        return pkg
    except Exception as exc:
        sys.modules.pop(_ENGINE_PKG_NAME, None)
        log.info("Engine package not usable here: %s", exc)
        return None


def _engine_submodule(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(f"{_ENGINE_PKG_NAME}.{name}")
    except Exception as exc:
        log.info("Engine submodule %s not usable here: %s", name, exc)
        return None


class EngineBridge:
    """Single entry point the UI uses for everything engine-side."""

    def __init__(self) -> None:
        self._pkg = _load_engine_package()
        self._enroll_mod = _engine_submodule("enroll") if self._pkg else None
        self._vault_mod = _engine_submodule("vault") if self._pkg else None
        self._ident_mod = _engine_submodule("identities") if self._pkg else None
        self._rec_mod = _engine_submodule("recognition") if self._pkg else None
        self._live_mod = _engine_submodule("liveness") if self._pkg else None
        self._using_mock = self._enroll_mod is None or self._rec_mod is None
        if self._using_mock:
            log.info("Real engine unavailable — using the simulated engine.")
        self._mock_store = _MockIdentityStore()

    # -- capability flags -------------------------------------------------
    @property
    def using_real_engine(self) -> bool:
        return not self._using_mock

    @property
    def vault_available(self) -> bool:
        # The vault's dev-only fallback must never be reachable from this
        # user-facing app: DPAPI on Windows, otherwise unavailable.
        return sys.platform == "win32" and self._vault_mod is not None

    # -- enrollment --------------------------------------------------------
    def create_enrollment_session(self) -> "BaseEnrollmentSession":
        if self._using_mock:
            return _MockEnrollmentSession()
        try:
            return _RealEnrollmentSession(self._enroll_mod, self._rec_mod)
        except Exception as exc:
            raise EngineUnavailableError(
                "The face engine couldn't start an enrollment session.",
                str(exc),
            ) from exc

    # -- identities ----------------------------------------------------------
    def list_identities(self) -> list[Identity]:
        if self._using_mock or self._ident_mod is None:
            return self._mock_store.list_identities()
        try:
            store = self._ident_mod.IdentityStore()
        except Exception as exc:
            raise StoreError(
                "Your enrolled faces couldn't be read.",
                "The secure store may be locked or damaged. Nothing was changed.",
            ) from exc
        out = []
        for name in store.names():
            raw = store.get(name)
            if raw is not None:
                out.append(_identity_from_engine(raw, self._enroll_mod))
        return out

    def commit_enrollment(self, name: str, samples: list[EnrollmentSample],
                          existing_id: Optional[str] = None) -> Identity:
        name = name.strip()
        if not name:
            raise StoreError("Please give this face a name before saving.")
        if len(name) > _MAX_NAME_LEN:
            raise StoreError("That name is too long — please keep it under 120 characters.")
        if not samples:
            raise StoreError("No face samples were captured — nothing to save.")
        embeddings = _embeddings_to_numpy(samples, self._rec_mod)
        if self._using_mock or self._ident_mod is None:
            return self._mock_store.commit(name, samples, existing_id)
        try:
            store = self._ident_mod.IdentityStore()
        except Exception as exc:
            raise StoreError(
                "Your enrolled faces couldn't be saved.",
                "The secure store may be locked. Nothing was changed.",
            ) from exc
        # A recapture is a redo, not an append: replace wholesale.
        if existing_id and existing_id != name and store.get(existing_id) is not None:
            store.remove_identity(existing_id)
        if store.get(name) is not None:
            store.remove_identity(name)
        try:
            raw = store.add_identity(name, embeddings)
        except Exception as exc:
            raise StoreError(
                "Your enrolled faces couldn't be saved.",
                "Please try again. Nothing was changed.",
            ) from exc
        return _identity_from_engine(raw, self._enroll_mod)

    def set_identity_enabled(self, identity_id: str, enabled: bool) -> None:
        if self._using_mock or self._ident_mod is None:
            self._mock_store.set_enabled(identity_id, enabled)
            return
        try:
            self._ident_mod.IdentityStore().set_enabled(identity_id, bool(enabled))
        except Exception as exc:
            raise StoreError("That change couldn't be saved.", "Please try again.") from exc

    def delete_identity(self, identity_id: str) -> None:
        if self._using_mock or self._ident_mod is None:
            self._mock_store.delete(identity_id)
            return
        try:
            self._ident_mod.IdentityStore().remove_identity(identity_id)
        except Exception as exc:
            raise StoreError("That identity couldn't be deleted.", "Please try again.") from exc

    def name_is_taken(self, name: str, excluding: Optional[str] = None) -> bool:
        lowered = name.strip().casefold()
        if not lowered:
            return False
        for ident in self.list_identities():
            if excluding and ident.id == excluding:
                continue
            if ident.name.strip().casefold() == lowered:
                return True
        return False

    # -- vault (Windows credentials) -----------------------------------------
    def _vault_path(self) -> str:
        assert self._vault_mod is not None
        return self._vault_mod.default_vault_path()

    def store_credentials(self, username: str, password: str, domain: str = "") -> None:
        if not self.vault_available:
            raise VaultUnavailableError(
                "The secure vault isn't available on this machine.",
                "Storing your password needs Windows and the Visage engine.",
            )
        try:
            self._vault_mod.save_credentials(self._vault_path(), username, domain or "", password)
        except Exception as exc:
            # Fixed strings only — the password and any crypto detail
            # must never reach a message, a log, or a traceback shown to UI.
            log.warning("vault save failed: %s", type(exc).__name__)
            raise VisageError(
                "Your password couldn't be stored securely.",
                "Make sure you're on Windows with a normal user profile, then try again.",
            ) from exc

    def has_credentials(self) -> bool:
        if not self.vault_available:
            return False
        try:
            return bool(self._vault_mod.vault_exists(self._vault_path()))
        except Exception:
            return False

    def stored_username(self) -> str:
        """The stored username (for display). The password is never returned."""
        if not self.vault_available:
            return ""
        try:
            creds = self._vault_mod.load_credentials(self._vault_path())
            username = str(creds.get("username", ""))
        except Exception:
            return ""
        return username

    def clear_credentials(self) -> None:
        if not self.vault_available:
            raise VaultUnavailableError(
                "The secure vault isn't available on this machine.",
                "There's nothing stored here to remove.",
            )
        try:
            self._vault_mod.delete_vault(self._vault_path())
        except Exception as exc:
            log.warning("vault delete failed: %s", type(exc).__name__)
            raise VisageError("The stored password couldn't be removed.", "Please try again.") from exc

    # -- cameras ---------------------------------------------------------------
    def list_cameras(self) -> list[CameraInfo]:
        # The engine owns no camera catalog; the Manager probes locally.
        return []

    # -- diagnostics -------------------------------------------------------------
    def pipe_status(self) -> list[tuple[str, str]]:
        if self._using_mock:
            return [
                ("Camera", "idle"),
                ("Face detection", "idle"),
                ("Embedding", "idle"),
                ("Matching", "idle"),
                ("Liveness", "idle"),
            ]
        return [
            ("Engine package", "ok"),
            ("Face detection", "ok"),
            ("Embedding", "ok"),
            ("Identity store", "ok"),
            ("Named pipe", "idle"),
        ]

    def liveness_snapshot(self) -> list[LivenessCueState]:
        """Cue definitions + thresholds. Live readings need an active scan,
        which the Manager doesn't run — readings report 'no signal'."""
        defs = CUE_DEFINITIONS
        tuning = None
        if self._live_mod is not None:
            try:
                tuning = self._live_mod.LivenessTuning()
            except Exception:
                tuning = None
        out = []
        for key, title, role, expl in defs:
            threshold, frames = 0.5, 2
            if tuning is not None:
                try:
                    threshold = float(tuning.level_for(key))
                    frames = int(tuning.frames_for(key))
                except Exception:
                    pass
            out.append(LivenessCueState(key, title, role, expl, 0.0, 0.0, threshold, frames, False))
        return out

    # -- live analysis (Face Lab) ------------------------------------------------
    def _live_parts(self):
        """(recognition module, detector, embedder) or raise EngineUnavailableError."""
        if self._using_mock or self._rec_mod is None:
            raise EngineUnavailableError(
                "Live face analysis needs the real engine.",
                "It's not installed on this machine, so this Face Lab section is unavailable.",
            )
        try:
            detector = self._rec_mod.FaceDetector()
            embedder = self._rec_mod.ArcFaceEmbedder()
        except Exception as exc:
            raise EngineUnavailableError(
                "The face engine couldn't start.", _short_engine_error(exc)) from exc
        return self._rec_mod, detector, embedder

    @staticmethod
    def _as_bgr(frame_rgb):
        import cv2
        import numpy as np
        arr = np.ascontiguousarray(frame_rgb)
        if arr.ndim != 3 or arr.shape[2] != 3:
            raise VisageError("That frame didn't work — hold still.")
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    def detect_live(self, frame_rgb) -> list[dict]:
        """Largest-first face detections: [{'box': (x,y,w,h), 'score': float}]."""
        rec_mod, detector, _ = self._live_parts()
        frame_bgr = self._as_bgr(frame_rgb)
        try:
            detections = detector.detect(frame_bgr)
        except Exception:
            return []
        if not isinstance(detections, list):
            return []
        out = []
        for d in sorted(detections, key=lambda d: d.box[2] * d.box[3], reverse=True):
            try:
                x, y, w, h = (int(v) for v in d.box)
                out.append({"box": (x, y, w, h), "score": float(d.score)})
            except (TypeError, ValueError):
                continue
        return out

    def _embed_largest(self, frame_rgb):
        """Detect -> largest face -> align -> embed. Returns (embedding, box)."""
        rec_mod, detector, embedder = self._live_parts()
        frame_bgr = self._as_bgr(frame_rgb)
        try:
            detections = detector.detect(frame_bgr)
        except Exception as exc:
            raise VisageError("Having trouble seeing faces right now.") from exc
        faces = [d for d in detections or []
                 if getattr(d, "box", None) and len(d.box) == 4]
        if not faces:
            raise VisageError("No face detected in frame.")
        face = max(faces, key=lambda d: d.box[2] * d.box[3])
        try:
            aligned = rec_mod.align_face(frame_bgr, face.landmarks)
            embedding = embedder.embedding(aligned)
        except Exception as exc:
            raise VisageError("Could not read that face clearly.") from exc
        if not _valid_embedding(embedding):
            raise VisageError("Could not read that face clearly.")
        import numpy as np
        return np.asarray(embedding, dtype=np.float32), tuple(int(v) for v in face.box)

    def capture_live_sample(self, frame_rgb):
        """One embedding from the current frame (Face Lab manual capture)."""
        embedding, _ = self._embed_largest(frame_rgb)
        return embedding

    def identify_live(self, frame_rgb, threshold: float) -> dict:
        """Replicates the pipeline's scoring: centroid + max-sample similarity,
        both must clear the threshold (no runner-up margin check)."""
        rec_mod, _, _ = self._live_parts()
        embedding, _ = self._embed_largest(frame_rgb)
        if self._ident_mod is None:
            raise EngineUnavailableError("The identity store isn't available.")
        try:
            templates = self._ident_mod.IdentityStore().templates()
        except Exception as exc:
            raise StoreError("Your enrolled faces couldn't be read.") from exc
        scored = rec_mod.score_identities(embedding, templates)
        rows = [
            {"name": str(s.name),
             "centroid": float(s.centroid_similarity),
             "max": float(s.max_sample_similarity)}
            for s in scored
        ]
        best = rec_mod.best_match(scored, float(threshold))
        return {"scored": rows, "best": str(best.name) if best else None}

    def add_live_sample(self, name: str, frame_rgb) -> None:
        """Face Lab manual capture: one embedding appended to a named identity."""
        name = name.strip()
        if not name:
            raise StoreError("Enter a name before capturing a sample.")
        if self._using_mock or self._ident_mod is None:
            raise EngineUnavailableError("Manual capture needs the real engine.")
        embedding = self.capture_live_sample(frame_rgb)
        try:
            store = self._ident_mod.IdentityStore()
            if store.get(name) is None:
                store.add_identity(name, [embedding])
            else:
                store.add_embedding(name, embedding)
        except VisageError:
            raise
        except Exception as exc:
            raise StoreError("That sample couldn't be saved.", "Please try again.") from exc

    def match_threshold_default(self) -> float:
        try:
            return float(self._rec_mod.DEFAULT_MATCH_THRESHOLD)
        except (AttributeError, TypeError, ValueError):
            return 0.40

    def embedder_name(self) -> str:
        if self._using_mock:
            return "Simulated embedder"
        return "ArcFace (ONNX)"

    def embedding_distances(self) -> list[tuple[str, str, float]]:
        """(identity_a, identity_b, cosine_distance) for enrolled identities."""
        identities = [i for i in self.list_identities() if i.samples]
        if len(identities) < 2:
            return []
        templates = {i.id: _average_embedding(i.samples) for i in identities}
        names = {i.id: i.name for i in identities}
        ids = list(templates)
        rows = []
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                rows.append((names[ids[a]], names[ids[b]],
                             _cosine_distance(templates[ids[a]], templates[ids[b]])))
        return rows


# ---------------------------------------------------------------------------
# Session protocol
# ---------------------------------------------------------------------------

class BaseEnrollmentSession:
    def process_frame(self, frame_rgb) -> FrameOutcome:  # pragma: no cover
        raise NotImplementedError

    def samples(self) -> list[EnrollmentSample]:  # pragma: no cover
        raise NotImplementedError

    def cancel(self) -> None:  # pragma: no cover
        raise NotImplementedError


class _RealEnrollmentSession(BaseEnrollmentSession):
    """Drives the real YuNet + ArcFace guided enrollment.

    The Manager owns the camera (RGB frames); the engine wants BGR, so we
    convert at the boundary. Every verdict is strictly validated before it
    becomes UI state: whitelisted direction, finite 512-d embedding, sane
    progress numbers. Anything off becomes a plain error outcome.
    """

    def __init__(self, enroll_mod: Any, rec_mod: Any) -> None:
        self._enroll_mod = enroll_mod
        self._rec_mod = rec_mod
        try:
            self._detector = rec_mod.FaceDetector()
            self._embedder = rec_mod.ArcFaceEmbedder()
            self._session = enroll_mod.EnrollmentSession(
                self._embedder, samples_per_direction=SAMPLES_PER_POSE)
            self._directions: tuple[str, ...] = tuple(enroll_mod.DIRECTIONS)
        except Exception as exc:
            raise EngineUnavailableError(
                "The face engine couldn't start.", _short_engine_error(exc)) from exc
        for d in self._directions:
            if d not in POSE_BY_ENGINE_KEY:
                raise EngineUnavailableError(
                    "The face engine uses a head direction this app doesn't know.",
                    "Update Visage Manager to match the installed engine.",
                )

    def process_frame(self, frame_rgb) -> FrameOutcome:
        import cv2
        import numpy as np

        frame_rgb = np.ascontiguousarray(frame_rgb)
        if frame_rgb.ndim != 3 or frame_rgb.shape[2] != 3:
            return FrameOutcome(kind="error", message="That frame didn't work — hold still.")
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        try:
            detections = self._detector.detect(frame_bgr)
        except Exception as exc:
            log.warning("detection failed: %s", type(exc).__name__)
            return FrameOutcome(kind="error",
                                message="Having trouble seeing faces right now.")
        if not isinstance(detections, list):
            return FrameOutcome(kind="error", message="Having trouble seeing faces right now.")
        try:
            verdict = self._session.process_frame(frame_bgr, detections)
        except Exception as exc:
            log.warning("enrollment frame failed: %s", type(exc).__name__)
            return FrameOutcome(kind="error", message="That frame didn't work — hold still.")

        total = len(ENROLLMENT_POSES) * SAMPLES_PER_POSE
        got, want = self._safe_progress()
        target_engine = self._session.next_direction()
        target_pose = POSE_BY_ENGINE_KEY.get(target_engine or "", ENROLLMENT_POSES[0])
        pose_index = ENROLLMENT_POSES.index(target_pose)
        captured_here = self._captured_for(target_engine) if target_engine else 0

        if not getattr(verdict, "accepted", False):
            reason = str(getattr(verdict, "reason", "") or "")
            kind, message = VERDICT_COPY.get(reason, ("error", "That frame didn't work — hold still."))
            if kind == "error":
                log.info("unmapped verdict reason: %r", reason[:80])
            return FrameOutcome(kind=kind, message=message, pose_index=pose_index,
                                captured_for_pose=captured_here,
                                overall_progress=min(got / total, 1.0))

        # Accepted: validate the embedding before it goes anywhere.
        direction = str(getattr(verdict, "direction", "") or "")
        if direction not in POSE_BY_ENGINE_KEY:
            log.warning("engine returned unknown direction %r", direction[:40])
            return FrameOutcome(kind="error", message="That frame didn't work — hold still.")
        embedding = getattr(verdict, "embedding", None)
        if not _valid_embedding(embedding):
            log.warning("engine returned a malformed embedding")
            return FrameOutcome(kind="error", message="That frame didn't work — hold still.")

        if self._session.is_complete():
            return FrameOutcome(kind="complete", message="Face captured",
                                pose_index=len(ENROLLMENT_POSES) - 1,
                                captured_for_pose=SAMPLES_PER_POSE,
                                overall_progress=1.0)
        if direction != target_engine:
            # Sample landed in a different bucket than the one we're guiding
            # toward — acknowledge it, steer back.
            back = POSE_BY_ENGINE_KEY[target_engine].instruction if target_engine else ""
            return FrameOutcome(kind="pose_progress",
                                message=f"Got it — now {back[0].lower() + back[1:]}" if back else "",
                                pose_index=pose_index, captured_for_pose=captured_here,
                                overall_progress=min(got / total, 1.0))
        return FrameOutcome(kind="pose_progress", message="",
                            pose_index=pose_index, captured_for_pose=captured_here,
                            overall_progress=min(got / total, 1.0))

    def samples(self) -> list[EnrollmentSample]:
        out: list[EnrollmentSample] = []
        try:
            embeddings = self._session.embeddings()
        except Exception:
            return []
        # embeddings() returns DIRECTIONS order, samples_per_direction each.
        idx = 0
        for direction in self._directions:
            pose_key = POSE_BY_ENGINE_KEY[direction].key
            for _ in range(SAMPLES_PER_POSE):
                if idx >= len(embeddings):
                    break
                emb = embeddings[idx]
                idx += 1
                if _valid_embedding(emb):
                    out.append(EnrollmentSample(
                        embedding=[float(v) for v in emb.ravel().tolist()],
                        pose=pose_key, quality=None))
        return out

    def cancel(self) -> None:
        pass

    # -- internals ------------------------------------------------------------
    def _safe_progress(self) -> tuple[int, int]:
        try:
            got, want = self._session.progress()
            got, want = int(got), int(want)
            if want <= 0:
                return 0, 1
            return max(0, min(got, want)), want
        except Exception:
            return 0, 1

    def _captured_for(self, engine_direction: Optional[str]) -> int:
        try:
            captured = self._session.captured
            vals = captured.get(engine_direction, [])
            return max(0, min(len(vals), SAMPLES_PER_POSE))
        except Exception:
            return 0


def _valid_embedding(emb: Any) -> bool:
    """Strict embedding check: 1-D, 1..4096 finite floats."""
    try:
        import numpy as np
        arr = np.asarray(emb, dtype=np.float64)
    except Exception:
        return False
    if arr.ndim != 1 or not (1 <= arr.size <= _MAX_EMBEDDING_DIM):
        return False
    try:
        return bool(np.all(np.isfinite(arr)))
    except Exception:
        return False


def _short_engine_error(exc: BaseException) -> str:
    """One-line, jargon-free summary of an engine failure for the UI."""
    text = str(exc).strip()
    low = text.lower()
    if "not found" in low and "model" in low:
        return "The face models aren't downloaded yet. Run the engine's fetch_models.py, then try again."
    if len(text) > 160:
        text = text[:160] + "…"
    return text or "An unexpected engine error."


# ---------------------------------------------------------------------------
# Mock implementations (engine absent — Linux dev, UI testing)
# ---------------------------------------------------------------------------

class _MockEnrollmentSession(BaseEnrollmentSession):
    """Simulates a cooperative enrollment so the wizard UI can be exercised
    with no camera and no engine. Deterministic and clearly labelled."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._pose = 0
        self._captured = 0
        self._hold_until: Optional[float] = None
        self._samples: list[EnrollmentSample] = []
        self._done = False
        self._rng_state = 1234

    def _rand(self) -> float:
        self._rng_state = (1103515245 * self._rng_state + 12345) & 0x7FFFFFFF
        return self._rng_state / 0x7FFFFFFF

    def process_frame(self, frame_rgb) -> FrameOutcome:
        if self._done:
            return FrameOutcome(kind="complete", overall_progress=1.0)
        elapsed = time.monotonic() - self._start
        total = len(ENROLLMENT_POSES) * SAMPLES_PER_POSE
        done = self._pose * SAMPLES_PER_POSE + self._captured

        if elapsed < 1.5:
            return FrameOutcome(kind="no_face", message="Position your face in the frame",
                                pose_index=self._pose, overall_progress=done / total)
        if 1.5 <= elapsed < 2.5 and self._pose == 0 and self._captured == 0:
            return FrameOutcome(kind="too_dark", message="Too dark — find better light",
                                pose_index=self._pose, overall_progress=done / total)

        now = time.monotonic()
        if self._hold_until is None:
            self._hold_until = now + 0.5
            return FrameOutcome(kind="hold_still", message="Hold still",
                                pose_index=self._pose, captured_for_pose=self._captured,
                                overall_progress=done / total)
        if now < self._hold_until:
            return FrameOutcome(kind="hold_still", message="Hold still",
                                pose_index=self._pose, captured_for_pose=self._captured,
                                overall_progress=done / total)

        self._captured += 1
        pose_key = ENROLLMENT_POSES[self._pose].key
        emb = [self._rand() * 2 - 1 for _ in range(512)]
        norm = math.sqrt(sum(x * x for x in emb)) or 1.0
        emb = [x / norm for x in emb]
        self._samples.append(EnrollmentSample(
            embedding=emb, pose=pose_key, quality=0.72 + self._rand() * 0.2))
        self._hold_until = None

        if self._captured >= SAMPLES_PER_POSE:
            self._captured = 0
            self._pose += 1
            if self._pose >= len(ENROLLMENT_POSES):
                self._done = True
                return FrameOutcome(kind="complete", message="Face captured",
                                    pose_index=len(ENROLLMENT_POSES) - 1,
                                    captured_for_pose=SAMPLES_PER_POSE,
                                    overall_progress=1.0)
            return FrameOutcome(kind="pose_done", message="Nice — next",
                                pose_index=self._pose, overall_progress=done / total)
        return FrameOutcome(kind="pose_progress", message="",
                            pose_index=self._pose, captured_for_pose=self._captured,
                            overall_progress=(done + 1) / total)

    def samples(self) -> list[EnrollmentSample]:
        return list(self._samples)

    def cancel(self) -> None:
        self._done = True


class _MockIdentityStore:
    def __init__(self) -> None:
        self._identities: list[Identity] = []

    def list_identities(self) -> list[Identity]:
        return list(self._identities)

    def commit(self, name: str, samples: list[EnrollmentSample],
               existing_id: Optional[str] = None) -> Identity:
        if existing_id:
            for ident in self._identities:
                if ident.id == existing_id:
                    ident.name = name
                    ident.samples = list(samples)
                    return ident
        ident = Identity(id=str(uuid.uuid4()), name=name, samples=list(samples))
        self._identities.append(ident)
        return ident

    def set_enabled(self, identity_id: str, enabled: bool) -> None:
        for ident in self._identities:
            if ident.id == identity_id:
                ident.is_enabled = enabled
                return

    def delete(self, identity_id: str) -> None:
        self._identities = [i for i in self._identities if i.id != identity_id]


# ---------------------------------------------------------------------------
# Strict conversion helpers (whitelist fields/types, cap sizes)
# ---------------------------------------------------------------------------

def _identity_from_engine(raw: Any, enroll_mod: Any = None) -> Identity:
    """Validate an engine identity dict; raise StoreError on anything off."""
    if not isinstance(raw, dict):
        raise StoreError("The identity store returned an unexpected format.")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > _MAX_NAME_LEN:
        raise StoreError("The identity store returned an invalid name.")
    embeddings = raw.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) > _MAX_EMBEDDINGS_PER_IDENTITY:
        raise StoreError("The identity store returned invalid face data.")
    samples: list[EnrollmentSample] = []
    # embeddings() order: DIRECTIONS x samples_per_direction — recover pose tags.
    directions: tuple[str, ...] = ()
    if enroll_mod is not None:
        try:
            directions = tuple(enroll_mod.DIRECTIONS)
        except Exception:
            directions = ()
    idx = 0
    for e in embeddings:
        if not _valid_embedding(e):
            raise StoreError("The identity store returned invalid face data.")
        pose_key = ""
        if directions:
            d = directions[(idx // SAMPLES_PER_POSE) % len(directions)]
            pose_key = POSE_BY_ENGINE_KEY.get(d, ENROLLMENT_POSES[0]).key
        import numpy as np
        samples.append(EnrollmentSample(
            embedding=[float(v) for v in np.asarray(e).ravel().tolist()],
            pose=pose_key, quality=None))
        idx += 1
    enabled = raw.get("enabled", True)
    created_raw = raw.get("created", "")
    try:
        created = datetime.fromisoformat(str(created_raw))
    except (ValueError, TypeError):
        created = datetime.now(timezone.utc)
    return Identity(
        id=name,  # engine identities are name-keyed
        name=name.strip(),
        samples=samples,
        model_identifier="arcface",
        embedding_dimension=512,
        created_at=created,
        is_enabled=bool(enabled),
    )


def _embeddings_to_numpy(samples: list[EnrollmentSample], rec_mod: Any) -> list:
    """Validate UI samples and convert to engine ndarrays (or plain lists)."""
    import numpy as np
    out = []
    for s in samples:
        arr = np.asarray(s.embedding, dtype=np.float32)
        if not _valid_embedding(arr):
            raise StoreError("A captured face sample was invalid. Please enroll again.")
        out.append(arr)
    if len(out) > _MAX_EMBEDDINGS_PER_IDENTITY:
        raise StoreError("Too many samples to save at once.")
    return out


def _average_embedding(samples: list[EnrollmentSample]) -> list[float]:
    dim = len(samples[0].embedding)
    avg = [0.0] * dim
    for s in samples:
        for i, v in enumerate(s.embedding):
            avg[i] += v
    n = len(samples)
    avg = [v / n for v in avg]
    norm = math.sqrt(sum(v * v for v in avg)) or 1.0
    return [v / norm for v in avg]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(2.0, 1.0 - dot))


def iter_poses() -> Iterator[EnrollmentPose]:
    return iter(ENROLLMENT_POSES)
