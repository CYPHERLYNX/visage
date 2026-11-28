"""Tests for guided enrollment: pose estimation, target-driven capture,
quality gates, and Glance's stall recovery."""

import cv2
import numpy as np
import pytest

from engine.enroll import (
    DIRECTION_PROMPTS,
    DIRECTIONS,
    EnrollmentSession,
    QualityConfig,
    face_sharpness,
    head_pose_ratios,
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


def feed(s, t, landmarks, n=4, dt=0.2):
    """Feed n frames dt apart; return (last verdict, new t)."""
    v = None
    for _ in range(n):
        v = s.process_frame(make_face(), [det(landmarks=landmarks)], now=t)
        t += dt
    return v, t


def capture(s, t, landmarks, dt=0.2):
    """Feed frames until one capture fires; return (verdict, new t)."""
    v = None
    for _ in range(30):
        v = s.process_frame(make_face(), [det(landmarks=landmarks)], now=t)
        t += dt
        if v.accepted:
            return v, t
    raise AssertionError("never captured, last reason: " + v.reason)


# Landmark deltas per direction. +x in the image is the viewer's right, so a
# user turning to their OWN left moves the nose toward +x (nose_dx positive).
# This is the regression anchor for the old mirrored-yaw bug.
DELTAS = {
    "center": (0, 0),
    "left": (20, 0),
    "up_left": (20, -14),
    "up": (0, -14),
    "up_right": (-20, -14),
    "right": (-20, 0),
    "down_right": (-20, 14),
    "down": (0, 14),
    "down_left": (20, 14),
}


# -- pose estimation --------------------------------------------------------

def test_head_pose_ratios_sign_conventions():
    yaw, pitch = head_pose_ratios(base_landmarks())
    assert yaw == pytest.approx(0.0)
    # Nose below the eye line in the image -> positive pitch.
    assert pitch > 0.3
    yaw_l, _ = head_pose_ratios(base_landmarks(nose_dx=20))
    assert yaw_l > 0  # user's own left reads positive (Glance's "+yaw turns left")
    yaw_r, _ = head_pose_ratios(base_landmarks(nose_dx=-20))
    assert yaw_r < 0
    _, pitch_up = head_pose_ratios(base_landmarks(nose_dy=-14))
    _, pitch_dn = head_pose_ratios(base_landmarks(nose_dy=14))
    assert pitch_up < pitch < pitch_dn


def test_head_pose_ratios_degenerate():
    assert head_pose_ratios(np.zeros((3, 2), dtype=np.float32)) is None
    assert head_pose_ratios(None) is None


# -- Glance order and copy ----------------------------------------------------

def test_nine_directions_glance_order_and_prompts():
    assert DIRECTIONS == (
        "center", "left", "up_left", "up", "up_right",
        "right", "down_right", "down", "down_left",
    )
    assert set(DIRECTION_PROMPTS) == set(DIRECTIONS)
    assert DIRECTION_PROMPTS["center"] == "Look straight at the camera"
    assert DIRECTION_PROMPTS["left"] == "Turn your head slightly left"
    assert all(p[0].isupper() for p in DIRECTION_PROMPTS.values())


# -- quality gates --------------------------------------------------------------

def test_rejects_no_face():
    s = session()
    v = s.process_frame(make_face(), [], now=10.0)
    assert not v.accepted and v.reason == "no_face"


def test_rejects_tiny_face():
    s = session()
    v = s.process_frame(make_face(), [det(box=(10, 10, 40, 40))], now=10.0)
    assert not v.accepted and v.reason == "too_far"


def test_rejects_blurry_frame():
    s = session()
    blurry = cv2.GaussianBlur(make_face(noise_sigma=0.0), (31, 31), 0)
    v = s.process_frame(blurry, [det()], now=10.0)
    assert not v.accepted and v.reason == "hold_still"


def test_rejects_closed_eyes():
    s = session()
    v = s.process_frame(make_face(eye_open=0.0), [det()], now=10.0)
    assert not v.accepted and v.reason == "eyes_closed"


def test_rejects_multiple_faces():
    s = session()
    v = s.process_frame(make_face(), [det(), det()], now=10.0)
    assert not v.accepted and v.reason == "multiple_faces"


def test_face_sharpness_orders_correctly():
    sharp = face_sharpness(make_face()[Y:Y + H, X:X + W])
    blurry = face_sharpness(cv2.GaussianBlur(make_face(noise_sigma=0.0), (31, 31), 0)[Y:Y + H, X:X + W])
    assert sharp > blurry
    assert sharp >= QualityConfig().min_sharpness
    assert blurry < QualityConfig().min_sharpness


# -- target-driven capture ----------------------------------------------------

def test_capture_requires_hold_and_streak():
    s = session()
    lm = base_landmarks()
    # First frame only arms the session (initial capture delay).
    v = s.process_frame(make_face(), [det(landmarks=lm)], now=0.0)
    assert not v.accepted and v.reason == "settling"
    # A single matching frame is not enough — hold + streak required.
    v = s.process_frame(make_face(), [det(landmarks=lm)], now=2.0)
    assert not v.accepted
    v, t = capture(s, 2.2, lm)
    assert v.accepted and v.direction == "center", v.reason
    got, want = s.progress()
    assert (got, want) == (1, 18)


def test_advances_through_targets_in_order():
    s = session()
    t = 0.0
    for i, d in enumerate(DIRECTIONS):
        dx, dy = DELTAS[d]
        lm = base_landmarks(nose_dx=dx, nose_dy=dy)
        # Two samples per pose.
        for _ in range(2):
            v, t = capture(s, t, lm)
            assert v.direction == d, (d, v.reason)
        nxt = DIRECTIONS[i + 1] if i + 1 < len(DIRECTIONS) else None
        assert s.next_direction() == nxt
    assert s.is_complete()
    assert s.next_prompt() == "You're all set"
    assert len(s.embeddings()) == 18


def test_wrong_direction_does_not_capture():
    """The mirrored-yaw regression: a right turn must not satisfy the left pose."""
    s = session()
    t = 0.0
    # Complete center first (baseline).
    for _ in range(2):
        _v, t = capture(s, t, base_landmarks())
    assert s.next_direction() == "left"
    # Feed a RIGHT turn (nose toward viewer's left) for a while: no capture.
    for _ in range(6):
        v = s.process_frame(make_face(), [det(landmarks=base_landmarks(nose_dx=-20))], now=t)
        t += 0.5
        assert not v.accepted, v.reason
        assert s.next_direction() == "left"
    # A genuine LEFT turn captures.
    v, t = capture(s, t, base_landmarks(nose_dx=20))
    assert v.accepted and v.direction == "left"


def test_excessive_turn_rejected_with_direction():
    s = session()
    t = 0.0
    for _ in range(2):
        _v, t = capture(s, t, base_landmarks())
    assert s.next_direction() == "left"
    v = s.process_frame(make_face(), [det(landmarks=base_landmarks(nose_dx=45))], now=t)
    assert not v.accepted and v.reason == "excessive_left"


def test_stall_widens_bands():
    """A near-miss pose becomes acceptable after the 12 s stall timeout."""
    s = session()
    t = 0.0
    for _ in range(2):
        _v, t = capture(s, t, base_landmarks())
    assert s.next_direction() == "left"
    # nose_dx=8 -> yaw_rel ~= 0.114: below the 0.14 inner threshold...
    lm = base_landmarks(nose_dx=8)
    v = s.process_frame(make_face(), [det(landmarks=lm)], now=t)
    assert not v.accepted and v.reason == "awaiting_pose"
    # ...but past the 12 s stall timeout the widened band (0.112) accepts it.
    v, t = capture(s, t + 13.0, lm)
    assert v.accepted and v.direction == "left", v.reason


def test_baseline_self_calibrates():
    """Pitch is measured against the user's own center, not a magic constant."""
    s = session()
    # A user whose frontal nose sits higher than the old 0.45 guess.
    shifted = base_landmarks().copy()
    shifted[2, 1] -= 10  # nose 10 px higher for this face
    t = 0.0
    for _ in range(2):
        v, t = capture(s, t, shifted)
    assert s.next_direction() == "left"
    # Burn through left/up_left to reach "up".
    for d in ("left", "up_left"):
        dx, dy = DELTAS[d]
        lm = shifted.copy()
        lm[2, 0] += dx
        lm[2, 1] += dy
        for _ in range(2):
            v, t = capture(s, t, lm)
            assert v.direction == d, (d, v.reason)
    assert s.next_direction() == "up"
    # "up" for THIS user is 14 px above their own baseline.
    up_lm = shifted.copy()
    up_lm[2, 1] -= 14
    v, t = capture(s, t, up_lm)
    assert v.accepted and v.direction == "up", v.reason


# -- live head-turn indicator ---------------------------------------------------

def test_head_turn_none_for_center_and_no_face():
    s = session()
    t = 0.0
    for _ in range(2):
        _v, t = capture(s, t, base_landmarks())
    # Target is now "left"; feed a matching turn and check the indicator.
    s.process_frame(make_face(), [det(landmarks=base_landmarks(nose_dx=10))], now=t)
    ht = s.head_turn()
    assert ht is not None
    angle, progress = ht
    # User's left -> compass ~270.
    assert 240 < angle < 300, angle
    assert 0.3 < progress <= 1.0
    # No face -> None.
    s.process_frame(make_face(), [], now=t + 1.0)
    assert s.head_turn() is None


def test_head_turn_deadzone():
    s = session()
    t = 0.0
    for _ in range(2):
        _v, t = capture(s, t, base_landmarks())
    # Tiny turn -> below the 0.15 deadzone -> None.
    s.process_frame(make_face(), [det(landmarks=base_landmarks(nose_dx=1))], now=t)
    assert s.head_turn() is None
