"""Tests for detection/alignment/embedding/matching.

Model-dependent tests use the real downloaded ONNX models and are skipped
with a clear message when the models are absent (run fetch_models.py).
"""

import os

import numpy as np
import pytest

from engine import recognition as rec
from engine.recognition import (
    DEFAULT_MATCH_THRESHOLD,
    MODEL_IDENTIFIER,
    ArcFaceEmbedder,
    FaceDetection,
    FaceDetector,
    align_face,
    best_match,
    cosine_similarity,
    largest_face,
    quality_tier,
    recognize_frame,
    score_identities,
    select_dominant_face,
    IdentityTemplate,
)

from .synth import base_landmarks, make_face

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
YUNET = os.path.join(MODEL_DIR, "face_detection_yunet_2023mar.onnx")
R50 = os.path.join(MODEL_DIR, "w600k_r50.onnx")
MODELS_PRESENT = os.path.isfile(YUNET) and os.path.isfile(R50)
needs_models = pytest.mark.skipif(not MODELS_PRESENT, reason="run fetch_models.py first")


def fake_detection():
    return FaceDetection(box=(70, 50, 180, 225), landmarks=base_landmarks(), score=0.99)


# -- cosine / matching (no models needed) -----------------------------------

def test_cosine_self_is_one():
    v = np.random.RandomState(0).rand(512).astype(np.float32)
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    a = np.zeros(512); a[0] = 1.0
    b = np.zeros(512); b[1] = 1.0
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_opposite_is_minus_one():
    a = np.random.RandomState(1).rand(512).astype(np.float32)
    assert cosine_similarity(a, -a) == pytest.approx(-1.0)


def test_score_identities_sorts_descending():
    probe = np.zeros(512); probe[0] = 1.0
    near = np.zeros(512); near[0] = 1.0; near[1] = 0.01
    far = np.zeros(512); far[5] = 1.0
    idents = [IdentityTemplate("far", [far]), IdentityTemplate("near", [near])]
    scored = score_identities(probe, idents)
    assert [s.name for s in scored] == ["near", "far"]


def test_best_match_needs_both_centroid_and_sample():
    # centroid passes but no single sample does -> no match (Glance's rule)
    probe = np.zeros(512); probe[0] = 1.0
    s1 = np.zeros(512); s1[0] = 0.5; s1[1] = 0.5
    s2 = np.zeros(512); s2[0] = 0.5; s2[1] = -0.5
    ident = IdentityTemplate("x", [s1, s2])
    scored = score_identities(probe, [ident])
    assert scored[0].centroid_similarity > 0.9   # centroid aligns...
    assert scored[0].max_sample_similarity < 0.9  # ...but no sample does
    assert best_match(scored, 0.9) is None


def test_best_match_accepts_clear_match():
    probe = np.zeros(512); probe[0] = 1.0
    ident = IdentityTemplate("x", [probe.copy()])
    assert best_match(score_identities(probe, [ident]), 0.4).name == "x"


def test_best_match_rejects_below_threshold():
    probe = np.zeros(512); probe[0] = 1.0
    other = np.zeros(512); other[3] = 1.0
    ident = IdentityTemplate("x", [other])
    assert best_match(score_identities(probe, [ident]), 0.4) is None


def test_disabled_identity_never_matches():
    probe = np.zeros(512); probe[0] = 1.0
    ident = IdentityTemplate("x", [probe.copy()], enabled=False)
    assert score_identities(probe, [ident]) == []


def test_best_match_empty():
    assert best_match([], 0.4) is None


# -- alignment (no models needed) -------------------------------------------

def test_align_face_output_shape_and_determinism():
    img = make_face()
    a = align_face(img, base_landmarks())
    b = align_face(img, base_landmarks())
    assert a.shape == (112, 112, 3)
    assert np.array_equal(a, b)


def test_align_face_rejects_bad_landmarks():
    img = make_face()
    with pytest.raises(ValueError):
        align_face(img, np.zeros((4, 2), dtype=np.float32))


def test_preprocess_rejects_wrong_size():
    emb = ArcFaceEmbedder.__new__(ArcFaceEmbedder)
    with pytest.raises(ValueError):
        emb.preprocess(np.zeros((100, 100, 3), dtype=np.uint8))


# -- detector ---------------------------------------------------------------

def test_detector_missing_model_raises_helpful_error():
    det = FaceDetector(model_path="/nonexistent/yunet.onnx")
    with pytest.raises(rec.ModelNotFoundError) as exc:
        det.detect(np.zeros((100, 100, 3), dtype=np.uint8))
    assert "fetch_models.py" in str(exc.value)


@needs_models
def test_yunet_runs_on_image():
    det = FaceDetector(model_path=YUNET)
    dets = det.detect(make_face())
    assert isinstance(dets, list)  # cartoon may not detect; must not crash


# -- embedder (real model) ----------------------------------------------------

@needs_models
def test_embedding_is_deterministic():
    emb = ArcFaceEmbedder(model_path=R50)
    aligned = align_face(make_face(), base_landmarks())
    e1 = emb.embedding(aligned)
    e2 = emb.embedding(aligned)
    assert np.array_equal(e1, e2)


@needs_models
def test_embedding_is_unit_norm_512d():
    emb = ArcFaceEmbedder(model_path=R50)
    e = emb.embedding(align_face(make_face(), base_landmarks()))
    assert e.shape == (512,)
    assert float(np.linalg.norm(e)) == pytest.approx(1.0, abs=1e-5)


@needs_models
def test_same_face_scores_one_different_faces_score_lower():
    emb = ArcFaceEmbedder(model_path=R50)
    e1 = emb.embedding(align_face(make_face(seed=0), base_landmarks()))
    e2 = emb.embedding(align_face(make_face(seed=0), base_landmarks()))
    e3 = emb.embedding(align_face(make_face(seed=99), base_landmarks()))
    assert cosine_similarity(e1, e2) == pytest.approx(1.0)
    assert cosine_similarity(e1, e3) < 0.999


@needs_models
def test_embedder_missing_model_raises_helpful_error():
    emb = ArcFaceEmbedder(model_path="/nonexistent/r50.onnx")
    with pytest.raises(rec.ModelNotFoundError):
        emb.embedding(np.zeros((112, 112, 3), dtype=np.uint8))


# -- Glance-parity: threshold, staleness, face selection ------------------------

def test_default_threshold_is_glances_shipped_default():
    assert DEFAULT_MATCH_THRESHOLD == 0.66


def test_stale_identity_kept_in_scored_but_never_matches():
    probe = np.zeros(512); probe[0] = 1.0
    ident = IdentityTemplate("x", [probe.copy()], model_id="arcface-older-model")
    scored = score_identities(probe, [ident])
    assert scored[0].is_stale  # diagnostics console flags it...
    assert best_match(scored, 0.4) is None  # ...but it can never match


def test_fresh_identity_matches():
    probe = np.zeros(512); probe[0] = 1.0
    ident = IdentityTemplate("x", [probe.copy()], model_id=MODEL_IDENTIFIER)
    scored = score_identities(probe, [ident])
    assert not scored[0].is_stale
    assert best_match(scored, 0.4).name == "x"


def _det(box, score=0.9):
    return FaceDetection(box=box, landmarks=base_landmarks(), score=score)


def test_select_dominant_face_filters_bystanders():
    # 30px wide on a 320px frame = 0.094 < 0.18 -> not a candidate.
    assert select_dominant_face([_det((10, 10, 30, 40))], (320, 320, 3)) is None


def test_select_dominant_face_picks_largest():
    big = _det((50, 50, 180, 200))
    small = _det((10, 10, 70, 80))
    assert select_dominant_face([small, big], (320, 320, 3)).box == big.box


def test_select_dominant_face_prefers_continuity():
    big = _det((50, 50, 180, 200))
    small = _det((10, 10, 70, 80))
    # Previous box was the small face: stick to it instead of flip-flopping.
    prev = small.box
    assert select_dominant_face([big, small], (320, 320, 3), prev).box == small.box


def test_select_dominant_face_continuity_has_distance_limit():
    big = _det((50, 50, 180, 200))
    small = _det((200, 200, 70, 80))
    far_prev = (0, 0, 70, 80)  # nowhere near either face
    assert select_dominant_face([big, small], (320, 320, 3), far_prev).box == big.box


def test_largest_face_bypasses_prominence_filter():
    tiny = _det((10, 10, 30, 40))
    assert largest_face([tiny]).box == tiny.box
    assert largest_face([]) is None


def test_quality_tier_thresholds():
    assert quality_tier(None) == "unrated"
    assert quality_tier(0.2) == "poor"
    assert quality_tier(0.45) == "fair"
    assert quality_tier(0.9) == "good"


def test_recognize_frame_pipeline_order():
    class StubDetector:
        def detect(self, frame):
            return [_det((70, 50, 180, 225), score=0.99)]

    class FakeEmbedder(ArcFaceEmbedder):
        def _ensure_loaded(self):
            pass

        def embedding(self, aligned):
            v = np.random.RandomState(3).randn(512).astype(np.float32)
            return v / np.linalg.norm(v)

    probe = np.random.RandomState(3).randn(512).astype(np.float32)
    probe = probe / np.linalg.norm(probe)
    ident = IdentityTemplate("Fahad", [probe])
    result = recognize_frame(make_face(), StubDetector(), FakeEmbedder(), [ident])
    assert result is not None
    assert result.aligned_image.shape == (112, 112, 3)
    assert result.alignment_tier == "5-point"
    assert result.detection.score == pytest.approx(0.99)
    assert result.scored[0].name == "Fahad"
    assert result.scored[0].centroid_similarity == pytest.approx(1.0)


def test_recognize_frame_none_without_prominent_face():
    class StubDetector:
        def detect(self, frame):
            return [_det((10, 10, 30, 40))]

    assert recognize_frame(make_face(), StubDetector(), None, []) is None
