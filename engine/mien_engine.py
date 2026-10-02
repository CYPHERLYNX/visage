"""
Mien face-unlock engine — entry point.

On Windows it serves the credential provider over the named pipe
``\\\\.\\pipe\\MienAuth`` (JSONL protocol, see pipe_protocol). On other
platforms it falls back to a TCP localhost socket (dev/testing). ``--test``
mode skips IPC entirely and runs the auth pipeline on image files::

    python mien_engine.py                      # Windows: named pipe server
    python mien_engine.py --socket 47631       # TCP localhost server (any OS)
    python mien_engine.py --test face1.jpg face2.jpg [--strictness heavy]

No network calls happen at runtime. Exit codes: 0 = authenticated (test mode),
1 = not authenticated, 2 = usage/startup error.

Trust model (also see TRUST_MODEL.md)
-------------------------------------
* The pipe carries scan *evidence* only — statuses, challenge prompts, failure
  reasons. There is NO command that triggers credential submission, returns
  the stored password, or bypasses matching/liveness. The unlock decision
  (match + liveness) is made inside this service; credential submission stays
  inside the credential provider, which is the only component that can read
  the password vault.
* The engine never imports the vault's credential functions and never handles
  the Windows password.
* The named pipe's DACL grants access to SYSTEM and Administrators only
  (see PIPE_SDDL).
* Every inbound message is strictly schema-validated (whitelist of commands,
  fields, types, value ranges) and size-capped at 64KB. A malformed message
  produces a human-readable error and the connection is dropped — never a
  crash.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

from . import pipe_protocol as proto
from .identities import IdentityStore
from .liveness import CHALLENGE_TIMEOUT_S, LivenessSession
from .recognition import (
    DEFAULT_MATCH_THRESHOLD,
    MODEL_IDENTIFIER,
    ArcFaceEmbedder,
    FaceDetector,
    best_match,
    score_identities,
    select_dominant_face,
)

PIPE_NAME = r"\\.\pipe\MienAuth"
DEFAULT_TCP_PORT = 47631

# Strict named-pipe DACL, applied at pipe creation (Windows only).
#
# SDDL "D:P(A;;GA;;;SY)(A;;GA;;;BA)" means:
#   D:     this is a DACL
#   P      protected — do not inherit ACEs from the parent object
#   (A;;GA;;;SY)  allow (A) generic-all (GA) to SYSTEM (SY)
#   (A;;GA;;;BA)  allow (A) generic-all (GA) to built-in Administrators (BA)
#
# Everyone else is denied by omission: there is no allow ACE for any other
# SID, so no other user or service can connect to the pipe. Set via
# ConvertStringSecurityDescriptorToSecurityDescriptorW and passed as the
# lpSecurityAttributes of CreateNamedPipeW — the DACL is on the pipe from
# the moment it exists, so there is no window where the pipe is world-open.
PIPE_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)"


def _log(msg: str) -> None:
    """Human-readable service log line (stderr). Never includes secrets."""
    print("mien-engine: %s" % msg, file=sys.stderr)


def _pipe_security_attributes():
    """Build SECURITY_ATTRIBUTES carrying the strict pipe DACL.

    Returns (SECURITY_ATTRIBUTES, sd_handle) on Windows; (None, None)
    elsewhere. The security descriptor must stay alive as long as pipes are
    created from it — the caller keeps the returned handle for the process
    lifetime. Fails closed: if the descriptor cannot be built, the service
    refuses to start rather than creating an unprotected pipe.
    """
    if os.name != "nt":
        return None, None
    import ctypes
    from ctypes import wintypes

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    advapi32 = ctypes.windll.advapi32
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    sd = wintypes.LPVOID()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        PIPE_SDDL, 1, ctypes.byref(sd), None
    ):
        raise SystemExit(
            "mien-engine: could not apply the named pipe security descriptor. "
            "Refusing to start without it."
        )
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False)
    return sa, sd


# ---------------------------------------------------------------------------
# Machine-wide configuration (C:\ProgramData\Mien\config.json on Windows)
# ---------------------------------------------------------------------------
#
# Written by installer/setup.ps1; read here once at startup. Only two keys
# are honored today — everything else is ignored so the file stays forward
# compatible. Every value is validated; anything missing or invalid falls
# back to the built-in default and the service logs one line. A corrupt or
# hostile config can never break the engine or weaken it silently.

MACHINE_CONFIG_DEFAULTS = {
    "match_threshold": DEFAULT_MATCH_THRESHOLD,  # 0.66, mirrors Glance
    "strictness": "heavy",                       # light | heavy | off
}


def _machine_config_path() -> str:
    if os.name == "nt":
        return r"C:\ProgramData\Mien\config.json"
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "config.json")


def load_machine_config(path: Optional[str] = None) -> Dict[str, object]:
    """Read and validate the machine config. Never raises: on any problem
    the built-in defaults are returned and one line is logged."""
    cfg: Dict[str, object] = dict(MACHINE_CONFIG_DEFAULTS)
    p = path or _machine_config_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        _log("machine config unreadable (%s); using defaults" % exc)
        return cfg
    if not isinstance(raw, dict):
        _log("machine config is not a JSON object; using defaults")
        return cfg
    if "match_threshold" in raw:
        thr = raw["match_threshold"]
        # bool is an int subclass; reject it explicitly.
        if isinstance(thr, bool) or not isinstance(thr, (int, float)):
            _log("machine config: match_threshold is not a number; using default")
        elif 0.1 <= float(thr) <= 0.99:
            cfg["match_threshold"] = float(thr)
        else:
            _log("machine config: match_threshold out of range; using default")
    if "strictness" in raw:
        strict = raw["strictness"]
        if isinstance(strict, str) and strict.lower() in ("light", "heavy", "off"):
            cfg["strictness"] = strict.lower()
        else:
            _log("machine config: bad strictness; using default")
    return cfg


# ---------------------------------------------------------------------------
# Frame sources (camera is injectable so tests never need one)
# ---------------------------------------------------------------------------


class FrameSource:
    def read(self) -> Optional[np.ndarray]:
        raise NotImplementedError

    def release(self) -> None:
        pass


class CameraSource(FrameSource):
    def __init__(self, index: int = 0) -> None:
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            raise RuntimeError(
                "Camera unavailable. Make sure it's connected and not being used "
                "by another app, then try again."
            )

    def read(self) -> Optional[np.ndarray]:
        ok, frame = self._cap.read()
        return frame if ok else None

    def release(self) -> None:
        self._cap.release()


class ImageListSource(FrameSource):
    """Cycles a list of image files as frames (for --test mode)."""

    def __init__(self, paths: List[str], loop: bool = True) -> None:
        self.paths = paths
        self.loop = loop
        self._idx = 0

    def read(self) -> Optional[np.ndarray]:
        if self._idx >= len(self.paths):
            if not self.loop or not self.paths:
                return None
            self._idx = 0
        img = cv2.imread(self.paths[self._idx])
        self._idx += 1
        return img


# ---------------------------------------------------------------------------
# Authenticator: one scan = detect -> liveness -> match
# ---------------------------------------------------------------------------


class Authenticator:
    def __init__(
        self,
        detector: FaceDetector,
        embedder: ArcFaceEmbedder,
        store: IdentityStore,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
        fps: float = 30.0,
    ) -> None:
        self.detector = detector
        self.embedder = embedder
        self.store = store
        self.match_threshold = match_threshold
        self.fps = fps

    def _match(self, frame: np.ndarray, detection) -> Optional[Dict]:
        try:
            embedding = self.embedder.embedding_for_frame(frame, detection)
        except Exception:
            return None
        scored = score_identities(embedding, self.store.templates(),
                                  model_id=MODEL_IDENTIFIER)
        top = best_match(scored, self.match_threshold, model_id=MODEL_IDENTIFIER)
        if top is None:
            return None
        return {"identity": top.name, "similarity": top.centroid_similarity}

    def authenticate(
        self,
        timeout_ms: int = 15000,
        strictness: str = "heavy",
        emit: Optional[Callable[[Dict], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        frame_source: Optional[FrameSource] = None,
    ) -> Dict:
        """Run one authentication scan. Returns the terminal protocol message."""
        emit = emit or (lambda m: None)
        cancel_event = cancel_event or threading.Event()
        source = frame_source
        own_source = False
        if source is None:
            try:
                source = CameraSource(0)
            except RuntimeError as exc:
                return proto.msg_failure("no_camera", str(exc))
            own_source = True

        session = LivenessSession(strictness=strictness, fps=self.fps)
        emit(proto.msg_scanning())
        deadline = time.monotonic() + timeout_ms / 1000.0
        face_seen = False
        best_hit: Optional[Dict] = None
        challenged = False
        previous_box: Optional[tuple] = None

        try:
            while time.monotonic() < deadline:
                if cancel_event.is_set():
                    return proto.msg_cancelled()
                frame = source.read()
                if frame is None:
                    time.sleep(0.05)
                    continue
                try:
                    detections = self.detector.detect(frame)
                except Exception as exc:
                    _log("face detection failed: %s" % exc)
                    return proto.msg_failure("error", "Something went wrong. Please try again.")
                # Prominence filter + continuity, mirroring Glance's
                # selectDominantFace: bystanders are ignored, and the
                # selection stays stuck to the same person frame to frame.
                det = select_dominant_face(detections, frame.shape, previous_box)
                if det is None:
                    continue
                previous_box = det.box
                if not face_seen:
                    face_seen = True
                    emit(proto.msg_face_found())

                x, y, w, h = det.box
                crop = frame[max(0, y): y + h, max(0, x): x + w]
                session.update(frame, det.box, det.landmarks, crop if crop.size else None)

                hit = self._match(frame, det)
                if hit and (best_hit is None or hit["similarity"] > best_hit["similarity"]):
                    best_hit = hit

                decision = session.decision()
                if decision.verdict == "spoof":
                    _log("spoof cue fired: %s" % decision.detail)
                    # decision.detail is the cue's user-facing denial reason.
                    return proto.msg_failure("spoof", decision.detail)
                if decision.verdict == "live":
                    if best_hit:
                        return proto.msg_success(best_hit["identity"], best_hit["similarity"])
                    continue  # live face, but not an enrolled identity yet
                if decision.verdict == "needs_challenge" and not challenged:
                    challenged = True
                    result = self._run_challenge(session, source, emit, cancel_event, deadline)
                    if result is not None:
                        return result
                    # Challenge inconclusive but not failed: keep scanning.
        finally:
            if own_source:
                source.release()

        if not face_seen:
            return proto.msg_failure(
                "no_face",
                "No face detected. Make sure you're in front of the camera, then try again.",
            )
        if best_hit is None:
            return proto.msg_failure(
                "no_match",
                "Face not recognized. Try again, or add this look in Mien settings.",
            )
        return proto.msg_failure(
            "liveness",
            "We couldn't confirm it's really you. Try moving your head a little and try again.",
        )

    def _run_challenge(self, session: LivenessSession, source: FrameSource,
                       emit: Callable[[Dict], None], cancel_event: threading.Event,
                       deadline: float) -> Optional[Dict]:
        """Challenge-response round. Returns a terminal message, or None to keep scanning."""
        verifier = session.new_challenge()
        emit(proto.msg_challenge(verifier.prompt))
        challenge_end = min(time.monotonic() + CHALLENGE_TIMEOUT_S, deadline)
        previous_box = None
        while time.monotonic() < challenge_end:
            if cancel_event.is_set():
                return proto.msg_cancelled()
            frame = source.read()
            if frame is None:
                time.sleep(0.05)
                continue
            try:
                detections = self.detector.detect(frame)
            except Exception:
                continue
            det = select_dominant_face(detections, frame.shape, previous_box)
            if det is not None:
                previous_box = det.box
                verifier.observe(det.landmarks)
        passed, detail = verifier.verify()
        if not passed:
            _log("challenge %r not passed: %s" % (verifier.kind, detail))
            return proto.msg_failure(
                "liveness",
                "We couldn't confirm that movement. Try again.",
            )
        return None  # passed: resume scanning; success still needs a match + live cues


# ---------------------------------------------------------------------------
# IPC: Windows named pipe (ctypes) or TCP localhost fallback
# ---------------------------------------------------------------------------


def _handle_client(make_reader, send, authenticator: Authenticator,
                   default_strictness: str = "heavy") -> None:
    """Serve one client connection speaking the JSONL protocol.

    default_strictness applies when the CP omits "strictness" from
    {"cmd":"authenticate"} (it comes from the machine config / CLI flag).

    Every inbound line is strictly schema-validated. The first malformed
    message ends the connection: a human-readable error is sent, the event is
    logged, and the connection is dropped. Nothing here can crash the
    service — unexpected exceptions are logged and the connection is closed.
    """
    buf = proto.LineBuffer()
    cancel_event = threading.Event()
    scan_lock = threading.Lock()
    state = {"scan_thread": None}

    def emit(msg: Dict) -> None:
        try:
            send(proto.encode_message(msg))
        except OSError:
            pass

    def run_scan(params: Dict) -> None:
        try:
            result = authenticator.authenticate(
                timeout_ms=params["timeout_ms"],
                strictness=params["strictness"],
                emit=emit,
                cancel_event=cancel_event,
            )
            emit(result)
        except Exception as exc:  # never let a scan take down the client loop
            _log("scan failed unexpectedly: %s" % exc)
            emit(proto.msg_failure("error", "Something went wrong. Please try again."))
        finally:
            with scan_lock:
                state["scan_thread"] = None
                cancel_event.clear()

    def drop(reason: str) -> None:
        _log("dropping connection: %s" % reason)
        emit(proto.msg_failure("error", "Couldn't understand that request. Closing the connection."))

    try:
        for chunk in make_reader():
            try:
                lines = buf.feed(chunk)
            except proto.ProtocolError as exc:
                drop(str(exc))
                return
            for line in lines:
                try:
                    raw = proto.decode_line(line)
                    msg = proto.validate_cp_message(raw)
                    if "strictness" not in raw:
                        msg["strictness"] = default_strictness
                except proto.ProtocolError as exc:
                    drop(str(exc))
                    return
                cmd = msg["cmd"]
                if cmd == "ping":
                    emit(proto.msg_pong())
                elif cmd == "cancel":
                    cancel_event.set()
                elif cmd == "authenticate":
                    with scan_lock:
                        thread = state["scan_thread"]
                        if thread is not None and thread.is_alive():
                            emit(proto.msg_failure(
                                "error", "A scan is already running. Please wait."))
                            continue
                        cancel_event.clear()
                        thread = threading.Thread(
                            target=run_scan, args=(msg,), daemon=True)
                        state["scan_thread"] = thread
                        thread.start()
                # No other commands exist: validate_cp_message already rejected
                # them. There is deliberately no generic dispatch here.
    except Exception as exc:  # defensive: a client must never crash the service
        _log("client handler error: %s" % exc)
    finally:
        cancel_event.set()  # client went away: stop any running scan


def serve_tcp(authenticator: Authenticator, port: int = DEFAULT_TCP_PORT,
              default_strictness: str = "heavy") -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SystemExit(
            "mien-engine: couldn't start on 127.0.0.1:%d (%s). "
            "Another copy may already be running." % (port, exc)
        )
    srv.listen(4)
    _log("listening on 127.0.0.1:%d (TCP fallback for non-Windows; localhost only)" % port)
    while True:
        conn, _ = srv.accept()
        try:
            def make_reader(c=conn):
                while True:
                    chunk = c.recv(65536)
                    if not chunk:
                        return
                    yield chunk

            _handle_client(make_reader, conn.sendall, authenticator,
                           default_strictness=default_strictness)
        finally:
            conn.close()


def serve_named_pipe(authenticator: Authenticator, pipe_name: str = PIPE_NAME,
                     default_strictness: str = "heavy") -> None:
    """Windows named-pipe server via ctypes (no pywin32 dependency).

    The pipe is created with a strict DACL (see PIPE_SDDL): only SYSTEM and
    Administrators can connect. The security descriptor is built before the
    first pipe instance exists and fails closed — if it cannot be applied,
    the service refuses to start.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    PIPE_ACCESS_DUPLEX = 0x00000003
    PIPE_TYPE_BYTE = 0x00000000
    PIPE_READMODE_BYTE = 0x00000000
    PIPE_WAIT = 0x00000000
    PIPE_UNLIMITED_INSTANCES = 255
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    ERROR_PIPE_CONNECTED = 535

    sa, _sd = _pipe_security_attributes()  # _sd kept alive for process lifetime
    sa_ptr = ctypes.byref(sa) if sa is not None else None

    kernel32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    ]
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE

    _log("listening on %s (SYSTEM + Administrators only)" % pipe_name)
    while True:
        handle = kernel32.CreateNamedPipeW(
            pipe_name, PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, sa_ptr,
        )
        if handle == INVALID_HANDLE_VALUE:
            raise SystemExit(
                "mien-engine: could not create the named pipe. "
                "Another copy of the engine may already be running."
            )
        connected = kernel32.ConnectNamedPipe(handle, None)
        if not connected and kernel32.GetLastError() != ERROR_PIPE_CONNECTED:
            kernel32.CloseHandle(handle)
            continue
        try:
            def make_reader(h=handle):
                buf = ctypes.create_string_buffer(65536)
                nread = wintypes.DWORD(0)
                while True:
                    ok = kernel32.ReadFile(h, buf, 65536, ctypes.byref(nread), None)
                    if not ok or nread.value == 0:
                        return
                    yield bytes(buf.raw[: nread.value])

            def send(data: bytes, h=handle) -> None:
                nwritten = wintypes.DWORD(0)
                if not kernel32.WriteFile(h, data, len(data), ctypes.byref(nwritten), None):
                    raise OSError("named pipe write failed")

            _handle_client(make_reader, send, authenticator,
                           default_strictness=default_strictness)
        finally:
            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_authenticator(model_dir: Optional[str] = None,
                        identities_path: Optional[str] = None,
                        match_threshold: Optional[float] = None) -> Authenticator:
    from .recognition import default_model_dir

    if match_threshold is None:
        match_threshold = float(load_machine_config()["match_threshold"])
    model_dir = model_dir or default_model_dir()
    detector = FaceDetector(os.path.join(model_dir, "face_detection_yunet_2023mar.onnx"))
    embedder = ArcFaceEmbedder(os.path.join(model_dir, "w600k_r50.onnx"))
    store = IdentityStore(identities_path)
    return Authenticator(detector, embedder, store,
                         match_threshold=match_threshold)


def cmd_test(args: argparse.Namespace) -> int:
    import json as _json

    auth = build_authenticator(args.model_dir, args.identities)
    source = ImageListSource(args.test)
    statuses: List[Dict] = []
    result = auth.authenticate(
        timeout_ms=args.timeout_ms,
        strictness=args.strictness,
        emit=statuses.append,
        frame_source=source,
    )
    for s in statuses:
        print(_json.dumps(s))
    print(_json.dumps(result))
    return 0 if result.get("status") == "success" else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Mien face-unlock engine")
    parser.add_argument("--model-dir", default=None, help="directory with the ONNX models")
    parser.add_argument("--identities", default=None, help="path to the identities store")
    parser.add_argument("--socket", type=int, default=None,
                        help="serve on TCP localhost PORT instead of the named pipe")
    parser.add_argument("--test", nargs="+", metavar="IMAGE",
                        help="run the auth pipeline on image files, no IPC")
    parser.add_argument("--strictness", default=None, choices=["light", "heavy", "off"],
                        help="liveness strictness; default comes from the machine config")
    parser.add_argument("--match-threshold", type=float, default=None,
                        help="cosine-similarity match threshold 0.1-0.99; "
                             "default comes from the machine config (0.66)")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    args = parser.parse_args(argv)

    # Machine config supplies defaults; explicit CLI flags win.
    cfg = load_machine_config()
    strictness = args.strictness or str(cfg["strictness"])
    if args.match_threshold is not None:
        if not 0.1 <= args.match_threshold <= 0.99:
            print("ERROR: --match-threshold must be between 0.1 and 0.99",
                  file=sys.stderr)
            return 2
        match_threshold = args.match_threshold
    else:
        match_threshold = float(cfg["match_threshold"])
    args.strictness = strictness

    if args.test:
        for p in args.test:
            if not os.path.isfile(p):
                print("ERROR: image not found: %s" % p, file=sys.stderr)
                return 2
        try:
            return cmd_test(args)
        except Exception as exc:
            print("ERROR: %s" % exc, file=sys.stderr)
            return 2

    auth = build_authenticator(args.model_dir, args.identities,
                               match_threshold=match_threshold)
    if args.socket is not None or os.name != "nt":
        serve_tcp(auth, args.socket or DEFAULT_TCP_PORT,
                  default_strictness=strictness)
    else:
        serve_named_pipe(auth, default_strictness=strictness)
    return 0


if __name__ == "__main__":
    sys.exit(main())
