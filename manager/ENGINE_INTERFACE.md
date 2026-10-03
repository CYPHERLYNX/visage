# Engine interface contract

The Manager never imports engine internals directly — everything goes
through `visage_manager/engine_bridge.py`. This document records the
**actual** engine API the bridge adapts to (read from the engine
sources, not assumed), plus the gaps the Manager works around.

Engine sources live in `../engine/` (owned by the engine worker).

## What the bridge uses

**Package import** — `../engine/__init__.py` is loaded as a real package
(`visage_engine_pkg`) via `importlib` with `submodule_search_locations`,
so the engine's relative imports (`from .recognition import …`) keep
working. If anything in the chain fails (no cv2/onnxruntime, no models),
the bridge falls back to the simulated engine.

**Enrollment** (`engine/enroll.py`)
- `EnrollmentSession(embedder, samples_per_direction=2)` — the Manager
  passes `2` to match Glance's `samplesPerPose`.
- `process_frame(frame_bgr, detections) -> FrameVerdict(accepted, reason, direction, embedding)`
- `next_direction()`, `is_complete()`, `progress() -> (got, want)`,
  `embeddings()` (DIRECTIONS order, `samples_per_direction` each).
- `DIRECTIONS = ("center","up","down","left","right","up_left","up_right","down_left","down_right")`
- Verdict `reason` strings consumed: `"No face visible"`,
  `"Just one face at a time, please"`, `"Move a little closer"`,
  `"Move into view"`, `"Hold still for a moment"`,
  `"Please open your eyes"`, `"Having trouble with this frame. Try again."`.

**Detection / embedding** (`engine/recognition.py`)
- `FaceDetector().detect(frame_bgr) -> [FaceDetection(box, landmarks, score)]`
- `align_face(frame_bgr, landmarks)`, `ArcFaceEmbedder().embedding(aligned)`
- `score_identities(embedding, templates) -> [ScoredIdentity(name, centroid_similarity, max_sample_similarity)]`
- `best_match(scored, threshold)` — top identity must clear the threshold
  on **both** centroid and max-sample similarity (no runner-up margin).
- `DEFAULT_MATCH_THRESHOLD = 0.66`, `EMBEDDING_DIM = 512`.
  (Mirrors Glance's shipped `matchThreshold ?? 0.66`; the manager reads
  the engine's value live via `engine_bridge.match_threshold_default()`.)

**Identities** (`engine/identities.py`) — name-keyed, no UUIDs
- `IdentityStore().names() / .get(name) / .add_identity(name, embeddings) /`
  `.add_embedding(name, embedding) / .remove_identity(name) /`
  `.set_enabled(name, enabled) / .templates()`
- Stored per identity: `name`, `embeddings`, `enabled`, `created`.
  No per-sample pose, quality, or timestamp.

**Vault** (`engine/vault.py`)
- `save_credentials(path, username, domain, password)`,
  `load_credentials(path) -> {username, domain, password}`,
  `delete_vault(path)`, `vault_exists(path)`, `default_vault_path()`.
- DPAPI LocalMachine on Windows. The Manager **refuses** the dev-only
  AES-GCM file fallback on non-Windows — from this user-facing app the
  vault is simply unavailable there.

**Liveness** (`engine/liveness.py`)
- `LivenessTuning().level_for(cue)` / `.frames_for(cue)` — real
  thresholds shown in Face Lab. (Cue keys: the Manager maps its five
  display cues onto these; live per-frame readings need an active
  unlock scan, which the Manager doesn't run.)

## Direction mapping

| Manager (Glance order) | Engine `DIRECTIONS` key |
|---|---|
| center | center |
| left | left |
| topLeft | up_left |
| top | up |
| topRight | up_right |
| right | right |
| bottomRight | down_right |
| bottom | down |
| bottomLeft | down_left |

## Known gaps (Manager workarounds)

1. **No camera catalog** — the engine owns no device enumeration; the
   Manager probes via OpenCV itself (`camera.probe_cameras`).
2. **No per-sample metadata** — pose/quality/capturedAt aren't stored;
   the Manager recovers pose tags from embedding order
   (`DIRECTIONS × samples_per_direction`) and reports quality as unrated.
3. **No rename** — recapture-with-rename is implemented as
   remove + add.
4. **No live liveness snapshot** — `LivenessSession` lives inside
   `visage_engine.authenticate`; Face Lab shows cue definitions and real
   thresholds with "no signal" readings rather than inventing values.
5. **Capture state machine differs from Glance** — the engine captures
   on gate-pass with no 500ms hold, no 3-frame streak, no 12s
   stall-widening, and no excessive-turn reporting. The Manager renders
   what the session reports; replicating Glance's exact capture beats
   needs engine-side support.
6. **Responsiveness contract** — `process_frame` should return promptly;
   the Manager additionally runs camera+session on a worker thread with
   a watchdog, so a stuck engine degrades to a message, never a hang.
