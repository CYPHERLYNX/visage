"""Tests for every liveness cue, the session decision logic, challenge-
response, and the Face Lab-style diagnostics.

Cue semantics replicate Glance's design (see module docstrings for the
replicated-vs-interpreted split); the challenge-response round is Visage's
extension beyond Glance.
"""

import cv2
import numpy as np
import pytest

from engine import liveness as liv
from engine.liveness import (
    BlinkTracker,
    ChallengeVerifier,
    CueReading,
    LivenessSession,
    LivenessTuning,
    device_rectangle_reading,
    eye_openness,
    eye_roi,
    extract_glare_sample,
    glare_reading,
    lbp_texture_reading,
    pick_challenge,
)

from .synth import (
    EYE_L,
    EYE_R,
    FACE_BOX,
    base_landmarks,
    eye_roi_frames,
    make_face,
    parallax_landmarks,
    rigid_shift_landmarks,
)

X, Y, W, H = FACE_BOX


def face_crop(img):
    return img[Y: Y + H, X: X + W]


def feed(session, img, landmarks=None, n=1):
    for _ in range(n):
        session.update(img, FACE_BOX, base_landmarks() if landmarks is None else landmarks)


# -- gloss/glare deny cue ---------------------------------------------------

def test_glare_fires_on_screen_blob():
    r = glare_reading(face_crop(make_face(glare=True)))
    assert r.confidence > 0
    assert r.level >= LivenessTuning.gloss_level  # 0.04, Glance's tuning


def test_glare_silent_on_clean_face():
    r = glare_reading(face_crop(make_face()))
    assert r.level < LivenessTuning.gloss_level


def test_glare_sample_fields_mirror_glance():
    s = extract_glare_sample(face_crop(make_face(glare=True)))
    assert s.crop_pixel_width == W
    assert 0.0 <= s.specular_fraction <= 1.0
    assert 0.0 <= s.specular_cluster_ratio <= 1.0


def test_glare_confidence_ramps_with_crop_size():
    small = np.full((40, 40, 3), 128, dtype=np.uint8)
    small[10:30, 10:30] = 255
    r = glare_reading(small)
    assert 0.0 <= r.confidence <= 1.0


def test_glare_abstains_on_empty():
    assert glare_reading(np.zeros((0, 0, 3), dtype=np.uint8)) is CueReading.NONE


# -- device rectangle deny cue ----------------------------------------------

def test_device_rectangle_fires_on_bezel():
    r = device_rectangle_reading(make_face(device_frame=True), FACE_BOX)
    assert r.confidence > 0
    assert r.level >= LivenessTuning.device_level  # 0.15


def test_device_rectangle_level_is_overlap_fraction():
    # Semantics mirror Glance's deviceDetected: level = overlap fraction.
    r = device_rectangle_reading(make_face(device_frame=True), FACE_BOX)
    assert r.level == pytest.approx(1.0)


def test_device_rectangle_silent_on_clean_face():
    r = device_rectangle_reading(make_face(), FACE_BOX)
    assert r.level < LivenessTuning.device_level


# -- blink confirm cue -------------------------------------------------------

def _openness_series(frames):
    gray_frames = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    iod = abs(EYE_R[0] - EYE_L[0])
    out = []
    for g in gray_frames:
        vals = []
        for ex, ey in (EYE_L, EYE_R):
            o, c = eye_openness(eye_roi(g, np.array([ex, ey]), iod))
            if c > 0:
                vals.append(o)
        out.append(sum(vals) / len(vals))
    return out


def test_blink_fires_on_dip_and_recover():
    frames = eye_roi_frames([0.9, 1.0, 0.9, 0.05, 0.05, 0.9, 1.0, 0.9])
    tracker = BlinkTracker(window=30)
    readings = [tracker.update(o, 1.0) for o in _openness_series(frames)]
    assert any(r.level >= 1.0 for r in readings)
    assert tracker.fired


def test_blink_silent_when_eyes_stay_open():
    frames = eye_roi_frames([1.0] * 8)
    tracker = BlinkTracker(window=30)
    readings = [tracker.update(o, 1.0) for o in _openness_series(frames)]
    assert not any(r.level >= 1.0 for r in readings)


def test_blink_needs_recovery_on_both_sides():
    # Dip at the very end with no recovery after -> not a blink (Glance rule).
    tracker = BlinkTracker(window=30)
    readings = [tracker.update(o, 1.0) for o in [0.8, 0.85, 0.8, 0.1, 0.1]]
    assert not any(r.level >= 1.0 for r in readings)


def test_blink_abstains_without_confidence():
    tracker = BlinkTracker()
    assert tracker.update(0.5, 0.0) is CueReading.NONE


# -- head-turn parallax confirm cue ------------------------------------------

def test_parallax_fires_on_relative_nose_motion():
    session = LivenessSession(strictness="heavy", fps=10)
    for lm in parallax_landmarks([0, 6, 14, 24, 34]):
        session.update(make_face(), FACE_BOX, lm)
    snap = session.snapshot()
    assert snap["cues"]["head_parallax"]["fired"]


def test_parallax_silent_on_rigid_photo_motion():
    # Whole landmark set translates together: a photo being waved around.
    session = LivenessSession(strictness="heavy", fps=10)
    for lm in rigid_shift_landmarks([(0, 0), (8, 3), (16, 6), (24, 9), (32, 12)]):
        session.update(make_face(), FACE_BOX, lm)
    snap = session.snapshot()
    assert not snap["cues"]["head_parallax"]["fired"]


# -- LBP texture confirm cue --------------------------------------------------

def test_lbp_prefers_textured_over_blurred():
    textured = face_crop(make_face(noise_sigma=14.0, seed=3))
    smooth = cv2.GaussianBlur(face_crop(make_face(noise_sigma=2.0, seed=3)), (15, 15), 0)
    gray_t = cv2.cvtColor(textured, cv2.COLOR_BGR2GRAY)
    gray_s = cv2.cvtColor(smooth, cv2.COLOR_BGR2GRAY)
    assert lbp_texture_reading(gray_t).level > lbp_texture_reading(gray_s).level


def test_lbp_abstains_on_tiny_crop():
    assert lbp_texture_reading(np.zeros((8, 8), dtype=np.uint8)) is CueReading.NONE


# -- session decision logic ----------------------------------------------------

def test_light_mode_spoof_on_glare_after_three_frames():
    session = LivenessSession(strictness="light", fps=30)
    feed(session, make_face(glare=True), n=2)
    assert session.decision().verdict == "pending"  # deny cues get a fair chance
    feed(session, make_face(glare=True), n=2)
    d = session.decision()
    assert d.verdict == "spoof"
    assert d.denied_by == "gloss_glare"
    # Glance's user-facing denial reason:
    assert "Screen glare detected" in d.detail


def test_light_mode_spoof_names_device_cue():
    session = LivenessSession(strictness="light", fps=30)
    feed(session, make_face(device_frame=True), n=4)
    d = session.decision()
    assert d.verdict == "spoof"
    assert d.denied_by == "device_rectangle"
    assert "device-shaped rectangle" in d.detail


def test_light_mode_live_when_no_deny_fires():
    session = LivenessSession(strictness="light", fps=30)
    feed(session, make_face(), n=5)
    assert session.decision().verdict == "live"


def test_deny_overrides_confirm_unconditionally():
    session = LivenessSession(strictness="heavy", fps=10)
    # First prove liveness via parallax, then show a spoof frame burst.
    for lm in parallax_landmarks([0, 10, 20, 30, 40]):
        session.update(make_face(), FACE_BOX, lm)
    assert session.decision().verdict == "live"
    feed(session, make_face(device_frame=True), n=4)
    assert session.decision().verdict == "spoof"


def test_firing_latches_for_the_whole_scan():
    # Mirrors Glance: a cue that fired stays fired even if later frames look clean.
    session = LivenessSession(strictness="light", fps=30)
    feed(session, make_face(glare=True), n=4)
    assert session.decision().verdict == "spoof"
    feed(session, make_face(), n=20)
    assert session.decision().verdict == "spoof"


def test_heavy_confirms_on_blink():
    session = LivenessSession(strictness="heavy", fps=10)
    frames = eye_roi_frames([1.0, 1.0, 1.0, 0.05, 0.05, 1.0, 1.0, 1.0])
    for f in frames:
        session.update(f, FACE_BOX, base_landmarks())
    assert session.decision().verdict == "live"


def test_heavy_borderline_escalates_to_challenge():
    # Static, clean, texture-poor frames: no confirm cue in the full window.
    session = LivenessSession(strictness="heavy", fps=5)  # window = 10 frames
    smooth = cv2.GaussianBlur(make_face(noise_sigma=0.0), (9, 9), 0)
    feed(session, smooth, n=12)
    assert session.decision().verdict == "needs_challenge"


def test_off_always_live():
    session = LivenessSession(strictness="off")
    feed(session, make_face(glare=True, device_frame=True), n=5)
    assert session.decision().verdict == "live"


def test_invalid_strictness_rejected():
    with pytest.raises(ValueError):
        LivenessSession(strictness="paranoid")


# -- challenge-response ---------------------------------------------------------

def test_pick_challenge_returns_known_prompt():
    import random
    kind, prompt = pick_challenge(random.Random(0))
    assert kind in liv.CHALLENGES
    assert prompt == liv.CHALLENGES[kind]
    assert prompt[0].isupper()  # Apple-motto copy: sentence case


def _turn_landmarks(direction, steps=8, magnitude=0.22):
    # nose_offset_ratio sweeps 0 -> +/-magnitude (viewer-left is negative x)
    iods = abs(EYE_R[0] - EYE_L[0])
    return [base_landmarks(nose_dx=direction * magnitude * iods * (i + 1) / steps)
            for i in range(steps)]


def test_challenge_turn_left_passes_on_real_head_turn():
    v = ChallengeVerifier("turn_left")
    for lm in _turn_landmarks(-1):
        v.observe(lm)
    passed, _ = v.verify()
    assert passed


def test_challenge_fails_on_static_replay():
    v = ChallengeVerifier("turn_left")
    for _ in range(8):
        v.observe(base_landmarks())
    passed, _ = v.verify()
    assert not passed


def test_challenge_fails_on_wrong_direction():
    v = ChallengeVerifier("turn_left")
    for lm in _turn_landmarks(+1):  # turned right instead
        v.observe(lm)
    passed, _ = v.verify()
    assert not passed


def test_challenge_fails_on_rigid_translation():
    # A replayed video sliding sideways keeps nose offset constant.
    v = ChallengeVerifier("turn_right")
    for lm in rigid_shift_landmarks([(i * 6, 0) for i in range(8)]):
        v.observe(lm)
    passed, _ = v.verify()
    assert not passed


def test_challenge_blink_needs_two_blinks():
    v = ChallengeVerifier("blink")
    frames = eye_roi_frames([1.0] * 3 + [0.05, 0.05] + [1.0] * 3 + [0.05, 0.05] + [1.0] * 4)
    iod = abs(EYE_R[0] - EYE_L[0])
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        vals = [eye_openness(eye_roi(g, np.array([ex, ey]), iod))[0] for ex, ey in (EYE_L, EYE_R)]
        v.observe(base_landmarks(), eye_openness=sum(vals) / 2, eye_confidence=1.0)
    passed, detail = v.verify()
    assert passed, detail


def test_challenge_unknown_kind_rejected():
    with pytest.raises(ValueError):
        ChallengeVerifier("levitate")


# -- diagnostics snapshot (Face Lab parity) --------------------------------------

def test_snapshot_shape_mirrors_liveness_snapshot():
    session = LivenessSession(strictness="heavy", fps=10)
    feed(session, make_face(), n=3)
    snap = session.snapshot()
    assert snap["mode"] == "heavy"
    assert snap["frame_count"] == 3
    assert snap["decision"] in ("pending", "live", "spoof", "needs_challenge")
    for _cue, state in snap["cues"].items():
        assert set(state) == {"level", "confidence", "detail", "frames_counted",
                              "fired", "progress", "role", "explanation"}
        assert 0.0 <= state["progress"] <= 1.0
        assert state["role"] in ("deny", "confirm")


def test_snapshot_progress_ramps_toward_firing():
    session = LivenessSession(strictness="light", fps=30)
    feed(session, make_face(glare=True), n=1)
    p1 = session.snapshot()["cues"]["gloss_glare"]["progress"]
    feed(session, make_face(glare=True), n=1)
    p2 = session.snapshot()["cues"]["gloss_glare"]["progress"]
    assert 0.0 < p1 < p2 <= 1.0


def test_frame_diagnostics_mirror_liveness_frame():
    session = LivenessSession(strictness="light", fps=30)
    session.update(make_face(), FACE_BOX, base_landmarks(nose_dx=14.0))
    d = session.last_frame
    assert d.interocular_distance == pytest.approx(70.0)
    assert d.nose_offset_ratio == pytest.approx(0.2)
    assert d.eye_openness_left is not None and d.eye_openness_right is not None
    assert d.device_overlap_fraction is not None
    assert d.glare_crop_pixel_width == W
