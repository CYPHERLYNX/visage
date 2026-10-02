"""Synthetic test imagery: cartoon faces drawn with OpenCV.

No camera and no real face photos are needed: every liveness cue and quality
gate is exercised against drawings whose spoof/live properties we control.
Landmarks returned here are in template order (viewer-left eye first) and
match the drawn geometry.
"""

import cv2
import numpy as np

SIZE = 320
# Drawn geometry (viewer-left eye first).
EYE_L = (125, 140)
EYE_R = (195, 140)
NOSE = (160, 172)
MOUTH_L = (135, 208)
MOUTH_R = (185, 208)
FACE_BOX = (70, 50, 180, 225)  # x, y, w, h


def base_landmarks(nose_dx=0.0, nose_dy=0.0):
    return np.array(
        [
            [EYE_L[0], EYE_L[1]],
            [EYE_R[0], EYE_R[1]],
            [NOSE[0] + nose_dx, NOSE[1] + nose_dy],
            MOUTH_L,
            MOUTH_R,
        ],
        dtype=np.float32,
    )


def make_face(size=SIZE, eye_open=1.0, glare=False, device_frame=False,
              noise_sigma=6.0, seed=0):
    """Draw a cartoon face. eye_open 1.0 = open, 0.0 = shut."""
    rng = np.random.RandomState(seed)
    img = np.full((size, size, 3), 60, dtype=np.uint8)  # dark background
    cx, cy = size // 2, size // 2
    # skin ellipse
    cv2.ellipse(img, (cx, cy), (90, 112), 0, 0, 360, (190, 170, 150), -1)
    # eyes (sclera kept below the specular luma floor of 235, like real cameras)
    for ex, ey in (EYE_L, EYE_R):
        if eye_open > 0.35:
            cv2.ellipse(img, (ex, ey), (20, int(13 * eye_open) + 2), 0, 0, 360,
                        (225, 225, 225), -1)
            cv2.circle(img, (ex, ey), 8, (25, 25, 25), -1)
            cv2.circle(img, (ex - 2, ey - 2), 2, (215, 215, 215), -1)
        else:
            cv2.line(img, (ex - 18, ey), (ex + 18, ey), (30, 30, 30), 4)
    # nose + mouth
    cv2.line(img, (cx, 150), (cx - 6, 186), (120, 100, 90), 4)
    cv2.ellipse(img, (cx, 210), (26, 12), 0, 0, 180, (110, 70, 70), 4)
    if glare:
        # one big flat specular blob: the screen-glare signature
        cv2.ellipse(img, (cx + 45, cy - 55), (46, 32), 20, 0, 360,
                    (255, 255, 255), -1)
    if device_frame:
        # bright rectangular bezel around the face
        cv2.rectangle(img, (28, 18), (size - 28, size - 18), (235, 235, 235), 10)
    if noise_sigma > 0:
        noise = rng.normal(0, noise_sigma, img.shape).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return img


def eye_roi_frames(openness_sequence, size=SIZE, seed=1):
    """Frames whose drawn eyes follow an openness sequence (for blink tests)."""
    return [make_face(size=size, eye_open=o, seed=seed + i, noise_sigma=3.0)
            for i, o in enumerate(openness_sequence)]


def parallax_landmarks(deltas):
    """Landmark sets where the nose moves relative to fixed eyes (real turn)."""
    return [base_landmarks(nose_dx=d) for d in deltas]


def rigid_shift_landmarks(shifts):
    """Landmark sets translated rigidly (photo wobble: no relative motion)."""
    return [base_landmarks() + np.array([dx, dy], dtype=np.float32)
            for dx, dy in shifts]
