"""Tests for guided enrollment: direction classification and quality gates."""

import cv2
import numpy as np
import pytest

from engine.enroll import (
    DIRECTION_PROMPTS,
    DIRECTIONS,
    EnrollmentSession,
    QualityConfig,
    classify_direction,
    face_sharpness,
)
from engine.recognition import ArcFaceEmbedder, FaceDetection

from .synth import FACE_BOX, base_landmarks, make_face

X, Y, W, H = FACE_BOX


class FakeEmbedder(ArcFaceEmbedder):
    def _ensure_loaded(self):
        pass

    def embedding(self, aligned_bgr):
        v = np.ones(512, dtype=np.float32)
        return v / np.linalg.norm(v)


def det(box=FACE_BOX, landmarks=None):
    return FaceDetection(box=box, landmarks=base_landmarks() if landmarks is None else landmarks,
                         score=0.99)


def session(**kw):
    return EnrollmentSession(FakeEmbedder(), **kw)


# -- direction classification -------------------------------------------------

def test_classify_all_nine_directions():
    assert classify_direction(base_landmarks()) == "center"
    assert classify_direction(base_landmarks(nose_dx=-20)) == "left"
    assert classify_direction(base_landmarks(nose_dx=+20)) == "right"
    assert classify_direction(base_landmarks(nose_dy=-14)) == "up"
    assert classify_direction(base_landmarks(nose_dy=+14)) == "down"
    assert classify_direction(base_landmarks(nose_dx=-20, nose_dy=-14)) == "up_left"
    assert classify_direction(base_landmarks(nose_dx=+20, nose_dy=-14)) == "up_right"
    assert classify_direction(base_landmarks(nose_dx=-20, nose_dy=+14)) == "down_left"
    assert classify_direction(base_landmarks(nose_dx=+20, nose_dy=+14)) == "down_right"


def test_nine_directions_and_prompts():
    assert len(DIRECTIONS) == 9
    assert set(DIRECTION_PROMPTS) == set(DIRECTIONS)
    assert all(p[0].isupper() for p in DIRECTION_PROMPTS.values())  # Apple-motto copy


# -- quality gates --------------------------------------------------------------

def test_rejects_no_face():
    s = session()
    v = s.process_frame(make_face(), [])
    assert not v.accepted and v.reason == "No face visible"


def test_rejects_tiny_face():
    s = session()
    v = s.process_frame(make_face(), [det(box=(10, 10, 40, 40))])
    assert not v.accepted and v.reason == "Move a little closer"


def test_rejects_blurry_frame():
    s = session()
    blurry = cv2.GaussianBlur(make_face(noise_sigma=0.0), (31, 31), 0)
    v = s.process_frame(blurry, [det()])
    assert not v.accepted and v.reason == "Hold still for a moment"


def test_rejects_closed_eyes():
    s = session()
    v = s.process_frame(make_face(eye_open=0.0), [det()])
    assert not v.accepted and v.reason == "Please open your eyes"


def test_rejects_multiple_faces():
    s = session()
    v = s.process_frame(make_face(), [det(), det()])
    assert not v.accepted and "one face" in v.reason


def test_accepts_good_frame_and_captures_embedding():
    s = session()
    v = s.process_frame(make_face(), [det()])
    assert v.accepted, v.reason
    assert v.embedding is not None and v.embedding.shape == (512,)
    assert v.direction == "center"
    got, want = s.progress()
    assert (got, want) == (1, 9)


def test_completes_all_directions():
    s = session()
    deltas = {"center": (0, 0), "left": (-20, 0), "right": (20, 0),
              "up": (0, -14), "down": (0, 14), "up_left": (-20, -14),
              "up_right": (20, -14), "down_left": (-20, 14), "down_right": (20, 14)}
    for d in DIRECTIONS:
        dx, dy = deltas[d]
        v = s.process_frame(make_face(), [det(landmarks=base_landmarks(nose_dx=dx, nose_dy=dy))])
        assert v.accepted, (d, v.reason)
    assert s.is_complete()
    assert s.next_prompt() == "You're all set"
    assert len(s.embeddings()) == 9


def test_face_sharpness_orders_correctly():
    sharp = face_sharpness(make_face()[Y:Y + H, X:X + W])
    blurry = face_sharpness(cv2.GaussianBlur(make_face(noise_sigma=0.0), (31, 31), 0)[Y:Y + H, X:X + W])
    assert sharp > blurry
    assert sharp >= QualityConfig().min_sharpness
    assert blurry < QualityConfig().min_sharpness
