"""Tests for the engine entry point: authenticator flow, IPC handling,
pipe security posture, and --test mode."""

import json
import os
import socket
import threading
import time

import cv2
import numpy as np
import pytest

from engine import visage_engine, pipe_protocol as proto
from engine.identities import IdentityStore
from engine.visage_engine import Authenticator, ImageListSource, _handle_client
from engine.recognition import ArcFaceEmbedder, FaceDetection

from .synth import FACE_BOX, base_landmarks, make_face


class StubDetector:
    def __init__(self, detections):
        self._detections = detections

    def detect(self, frame):
        return self._detections


class FakeEmbedder(ArcFaceEmbedder):
    FIXED = None

    def _ensure_loaded(self):
        pass

    def embedding_for_frame(self, frame, detection):
        return self.FIXED


def fixed_embedding(seed=7):
    # randn (zero-mean): two different seeds must be near-orthogonal.
    v = np.random.RandomState(seed).randn(512).astype(np.float32)
    return v / np.linalg.norm(v)


def prominent_face():
    return FaceDetection(box=FACE_BOX, landmarks=base_landmarks(), score=0.99)


def make_auth(store, detections):
    FakeEmbedder.FIXED = fixed_embedding()
    return Authenticator(StubDetector(detections), FakeEmbedder(), store, fps=30)


def write_images(tmp_path, *imgs):
    paths = []
    for i, img in enumerate(imgs):
        p = str(tmp_path / ("f%d.png" % i))
        cv2.imwrite(p, img)
        paths.append(p)
    return paths


# -- authenticator ------------------------------------------------------------

def test_authenticate_success_light_mode(tmp_path):
    store = IdentityStore(str(tmp_path / "ids.dat"))
    store.add_identity("Fahad", [fixed_embedding()])
    auth = make_auth(store, [prominent_face()])
    paths = write_images(tmp_path, *([make_face()] * 6))
    seen = []
    result = auth.authenticate(timeout_ms=8000, strictness="light", emit=seen.append,
                               frame_source=ImageListSource(paths))
    assert result["status"] == "success"
    assert result["identity"] == "Fahad"
    assert seen[0]["status"] == "scanning"
    assert any(s["status"] == "face_found" for s in seen)


def test_authenticate_no_face(tmp_path):
    store = IdentityStore(str(tmp_path / "ids.dat"))
    auth = make_auth(store, [])
    paths = write_images(tmp_path, np.zeros((320, 320, 3), dtype=np.uint8))
    result = auth.authenticate(timeout_ms=1500, strictness="light",
                               frame_source=ImageListSource(paths))
    assert result["status"] == "failure"
    assert result["reason"] == "no_face"
    assert result["message"]  # human-readable, Apple-motto copy


def test_authenticate_no_match(tmp_path):
    store = IdentityStore(str(tmp_path / "ids.dat"))
    store.add_identity("Fahad", [fixed_embedding(seed=123)])  # different person
    auth = make_auth(store, [prominent_face()])
    paths = write_images(tmp_path, *([make_face()] * 6))
    result = auth.authenticate(timeout_ms=8000, strictness="light",
                               frame_source=ImageListSource(paths))
    assert result["status"] == "failure"
    assert result["reason"] == "no_match"


def test_authenticate_spoof_uses_denial_reason(tmp_path):
    store = IdentityStore(str(tmp_path / "ids.dat"))
    auth = make_auth(store, [prominent_face()])
    paths = write_images(tmp_path, *([make_face(glare=True)] * 8))
    result = auth.authenticate(timeout_ms=8000, strictness="light",
                               frame_source=ImageListSource(paths))
    assert result["status"] == "failure"
    assert result["reason"] == "spoof"
    assert "Screen glare detected" in result["message"]


def test_authenticate_cancelled(tmp_path):
    import threading
    store = IdentityStore(str(tmp_path / "ids.dat"))
    auth = make_auth(store, [prominent_face()])
    paths = write_images(tmp_path, *([make_face()] * 6))
    cancel = threading.Event()
    cancel.set()
    result = auth.authenticate(timeout_ms=8000, strictness="light",
                               cancel_event=cancel,
                               frame_source=ImageListSource(paths))
    assert result["status"] == "cancelled"


def test_authenticate_challenge_path(tmp_path, monkeypatch):
    # Heavy mode, borderline scan -> challenge emitted -> static landmarks
    # cannot satisfy it -> liveness failure.
    monkeypatch.setattr(visage_engine, "CHALLENGE_TIMEOUT_S", 0.3)
    store = IdentityStore(str(tmp_path / "ids.dat"))
    store.add_identity("Fahad", [fixed_embedding()])
    FakeEmbedder.FIXED = fixed_embedding()
    auth = Authenticator(StubDetector([prominent_face()]), FakeEmbedder(), store, fps=5)
    smooth = cv2.GaussianBlur(make_face(noise_sigma=0.0), (9, 9), 0)
    paths = write_images(tmp_path, *([smooth] * 30))
    seen = []
    result = auth.authenticate(timeout_ms=12000, strictness="heavy", emit=seen.append,
                               frame_source=ImageListSource(paths))
    challenges = [s for s in seen if s["status"] == "challenge"]
    assert challenges, "expected a challenge-response round"
    assert challenges[0]["prompt"][0].isupper()
    assert result["status"] == "failure"
    assert result["reason"] == "liveness"


def test_bystander_face_ignored(tmp_path):
    # A narrow (distant) face is not prominent -> treated as no face.
    store = IdentityStore(str(tmp_path / "ids.dat"))
    auth = make_auth(store, [FaceDetection(box=(10, 10, 30, 40),
                                           landmarks=base_landmarks(), score=0.9)])
    paths = write_images(tmp_path, np.zeros((320, 320, 3), dtype=np.uint8))
    result = auth.authenticate(timeout_ms=1500, strictness="light",
                               frame_source=ImageListSource(paths))
    assert result["reason"] == "no_face"


# -- IPC: strict validation, drop on malformed ----------------------------------

def _dummy_auth(tmp_path):
    store = IdentityStore(str(tmp_path / "ids.dat"))
    return make_auth(store, [])


def test_client_ping_pong(tmp_path):
    sent = []
    _handle_client(lambda: iter([b'{"cmd": "ping"}\n']), sent.append, _dummy_auth(tmp_path))
    assert proto.decode_line(sent[0]) == {"status": "pong"}


def test_client_malformed_json_drops_connection(tmp_path):
    sent = []
    # Must not raise: human-readable error, then the connection is dropped.
    _handle_client(lambda: iter([b'{nope}\n', b'{"cmd": "ping"}\n']),
                   sent.append, _dummy_auth(tmp_path))
    assert len(sent) == 1
    msg = proto.decode_line(sent[0])
    assert msg["status"] == "failure" and msg["reason"] == "error"


def test_client_unknown_command_drops_connection(tmp_path):
    sent = []
    _handle_client(lambda: iter([b'{"cmd": "unlock"}\n']), sent.append, _dummy_auth(tmp_path))
    msg = proto.decode_line(sent[0])
    assert msg["status"] == "failure" and msg["reason"] == "error"


def test_client_extra_field_drops_connection(tmp_path):
    sent = []
    _handle_client(lambda: iter([b'{"cmd": "ping", "x": 1}\n']), sent.append,
                   _dummy_auth(tmp_path))
    msg = proto.decode_line(sent[0])
    assert msg["status"] == "failure"


def test_tcp_ping_pong_roundtrip(tmp_path):
    auth = _dummy_auth(tmp_path)
    port = 47639
    t = threading.Thread(target=visage_engine.serve_tcp, args=(auth, port), daemon=True)
    t.start()
    time.sleep(0.5)
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        s.sendall(b'{"cmd": "ping"}\n')
        line = b""
        while not line.endswith(b"\n"):
            line += s.recv(1024)
        assert proto.decode_line(line) == {"status": "pong"}
    finally:
        s.close()


# -- pipe security posture -------------------------------------------------------

def test_pipe_sddl_is_strict():
    # SYSTEM + Administrators only, protected from inheritance, no one else.
    assert visage_engine.PIPE_SDDL == "D:P(A;;GA;;;SY)(A;;GA;;;BA)"


def test_pipe_security_attributes_absent_off_windows():
    if os.name == "nt":
        pytest.skip("Windows-only check")
    sa, sd = visage_engine._pipe_security_attributes()
    assert sa is None and sd is None


def test_engine_never_imports_vault_credentials():
    # Trust model regression guard: the engine must not touch the password vault.
    src = open(visage_engine.__file__).read()
    assert "from .vault" not in src
    assert "from engine.vault" not in src
    assert "import vault" not in src


def test_no_pickle_or_eval_in_package():
    import pathlib
    pkg = pathlib.Path(visage_engine.__file__).parent
    for path in pkg.glob("*.py"):
        text = path.read_text()
        assert "pickle.loads" not in text, path
        assert "eval(" not in text, path


# -- --test mode -------------------------------------------------------------------

def test_cli_test_mode_no_face(tmp_path, capsys):
    paths = write_images(tmp_path, np.zeros((320, 320, 3), dtype=np.uint8))
    rc = visage_engine.main(["--test"] + paths + ["--strictness", "light",
                                                "--timeout-ms", "1500",
                                                "--identities", str(tmp_path / "ids.dat")])
    assert rc == 1
    out = capsys.readouterr().out
    last = json.loads(out.strip().splitlines()[-1])
    assert last["status"] == "failure" and last["reason"] == "no_face"


def test_image_list_source_cycles(tmp_path):
    paths = write_images(tmp_path, np.zeros((10, 10, 3), dtype=np.uint8))
    src = ImageListSource(paths, loop=True)
    assert src.read() is not None
    assert src.read() is not None  # cycled back to the first image
    src2 = ImageListSource(paths, loop=False)
    assert src2.read() is not None
    assert src2.read() is None


# -- machine config --------------------------------------------------------------

def _write_cfg(tmp_path, obj=None, raw=None):
    p = tmp_path / "config.json"
    p.write_text(raw if raw is not None else json.dumps(obj), encoding="utf-8")
    return str(p)


def test_machine_config_missing_is_defaults(tmp_path):
    cfg = visage_engine.load_machine_config(path=str(tmp_path / "nope.json"))
    assert cfg["match_threshold"] == 0.66
    assert cfg["strictness"] == "heavy"


def test_machine_config_valid_values(tmp_path):
    cfg = visage_engine.load_machine_config(
        path=_write_cfg(tmp_path, {"match_threshold": 0.5, "strictness": "LIGHT"}))
    assert cfg["match_threshold"] == 0.5
    assert cfg["strictness"] == "light"


def test_machine_config_bad_values_fall_back(tmp_path):
    for bad in ({"match_threshold": "high"}, {"match_threshold": 5.0},
                {"match_threshold": True}, {"strictness": "paranoid"},
                {"strictness": 3}):
        cfg = visage_engine.load_machine_config(path=_write_cfg(tmp_path, bad))
        key = "match_threshold" if "match_threshold" in bad else "strictness"
        assert cfg[key] == visage_engine.MACHINE_CONFIG_DEFAULTS[key], bad


def test_machine_config_corrupt_is_defaults(tmp_path):
    cfg = visage_engine.load_machine_config(path=_write_cfg(tmp_path, raw="{nope"))
    assert cfg == visage_engine.MACHINE_CONFIG_DEFAULTS
    cfg = visage_engine.load_machine_config(path=_write_cfg(tmp_path, raw="[1,2]"))
    assert cfg == visage_engine.MACHINE_CONFIG_DEFAULTS


def test_machine_config_unknown_keys_ignored(tmp_path):
    cfg = visage_engine.load_machine_config(
        path=_write_cfg(tmp_path, {"match_threshold": 0.6, "future": {"x": 1}}))
    assert cfg["match_threshold"] == 0.6
    assert "future" not in cfg


def test_handle_client_uses_configured_strictness_default(tmp_path):
    # CP omits strictness -> the machine-config default applies, not a
    # hard-coded "heavy" inside the protocol validator.
    received = {}

    class A:
        def authenticate(self, timeout_ms, strictness, emit, cancel_event):
            received["strictness"] = strictness
            return proto.msg_cancelled()

    script = [proto.encode_message({"cmd": "authenticate", "timeout_ms": 1200})]

    def make_reader():
        yield b"".join(script)

    sent = []
    visage_engine._handle_client(make_reader, sent.append, A(),
                               default_strictness="light")
    assert received.get("strictness") == "light"
