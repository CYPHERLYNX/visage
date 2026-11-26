"""
JSONL wire protocol between the credential provider (CP) and the Visage engine.

Transport
---------
Windows: named pipe ``\\\\.\\pipe\\VisageAuth`` (message mode, one JSON object
per line, UTF-8). Dev/testing: TCP localhost socket, same framing.

Every message is a single JSON object terminated by ``\\n``. Either side may
send messages in any order that the state machine below allows; unknown
fields are ignored, unknown ``cmd`` / ``status`` values are protocol errors.

CP -> engine commands
---------------------
{"cmd": "authenticate", "timeout_ms": 15000, "strictness": "heavy"}
    Start one authentication scan. ``timeout_ms`` is the whole-scan budget
    (default 15000). ``strictness`` is one of "light" | "heavy" | "off"
    (default "heavy"). While a scan is running the engine emits progress
    statuses; the scan ends with exactly one of success / failure / cancelled.

{"cmd": "cancel"}
    Abort the in-flight scan. The engine replies {"status": "cancelled"}.

{"cmd": "ping"}
    Health check. The engine replies {"status": "pong"}.

engine -> CP statuses
---------------------
{"status": "scanning"}
    Scan started, looking for a face.

{"status": "face_found"}
    A face is in frame; liveness analysis is running.

{"status": "challenge", "prompt": "Turn your head left"}
    Challenge-response round (heavy mode, borderline case). The user has 3
    seconds to perform the prompted motion. ``prompt`` is human-readable text
    for the CP to display. After the round the engine continues to a final
    success / failure; the CP must not treat this as terminal.

{"status": "success", "identity": "Fahad", "similarity": 0.73}
    Authenticated. ``identity`` is the matched enrollment name, ``similarity``
    the cosine similarity that cleared the threshold.

{"status": "failure", "reason": "<code>", "message": "<human-readable>"}
    Terminal failure. ``reason`` is one of:
      no_match   face seen, liveness ok, but no enrolled identity matched
      spoof      a deny liveness cue fired (glare / device rectangle)
      liveness   heavy mode: no confirm cue and challenge round not passed
      timeout    timeout_ms elapsed without a decision
      no_face    no face visible for the whole scan
      no_camera  camera could not be opened
      error      unexpected internal error; ``message`` carries the detail

{"status": "cancelled"}
    The scan was cancelled via {"cmd": "cancel"}.

{"status": "pong"}
    Reply to {"cmd": "ping"}.

Trust model (read before extending this protocol)
------------------------------------------------
The pipe carries scan *evidence* only. There is deliberately NO message that
can trigger credential submission, request the stored password, or bypass
matching/liveness:

* The engine never sees the Windows password. It only reads face embeddings
  (for matching) and reports success/identity, challenge prompts, and failure
  reasons.
* The credential provider (CP) owns the password vault and is the ONLY
  component that can submit credentials to Windows. It submits them only
  after receiving {"status": "success"} from the engine.
* The engine accepts exactly three commands — authenticate, cancel, ping —
  validated against a strict whitelist schema (see validate_cp_message).
  Unknown commands, unknown fields, wrong types, and oversized messages are
  rejected; the offending connection is dropped. No pickle, no eval, no
  dynamic dispatch on message contents — ever.
* Even a fully compromised engine process cannot exfiltrate the password,
  because the password never enters the engine's address space.

Example session
---------------
CP:      {"cmd": "authenticate", "timeout_ms": 15000, "strictness": "heavy"}\n
engine:  {"status": "scanning"}\n
engine:  {"status": "face_found"}\n
engine:  {"status": "challenge", "prompt": "Turn your head left"}\n
engine:  {"status": "success", "identity": "Fahad", "similarity": 0.71}\n
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Message builders (engine -> CP)
# ---------------------------------------------------------------------------

VALID_FAILURE_REASONS = frozenset(
    {"no_match", "spoof", "liveness", "timeout", "no_face", "no_camera", "error"}
)
VALID_STRICTNESS = frozenset({"light", "heavy", "off"})
VALID_COMMANDS = frozenset({"authenticate", "cancel", "ping"})
VALID_STATUSES = frozenset(
    {"scanning", "face_found", "challenge", "success", "failure", "cancelled", "pong"}
)

# Hard cap on a single inbound protocol message. Anything larger is rejected
# before parsing; the connection is dropped.
MAX_MESSAGE_BYTES = 64 * 1024

# authenticate bounds: at least 1s (a scan needs a few frames), at most 2 min.
MIN_TIMEOUT_MS = 1000
MAX_TIMEOUT_MS = 120000


def encode_message(obj: Dict[str, Any]) -> bytes:
    """Serialize one protocol message to a ``\\n``-terminated UTF-8 line."""
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def msg_scanning() -> Dict[str, Any]:
    return {"status": "scanning"}


def msg_face_found() -> Dict[str, Any]:
    return {"status": "face_found"}


def msg_challenge(prompt: str) -> Dict[str, Any]:
    return {"status": "challenge", "prompt": prompt}


def msg_success(identity: str, similarity: float) -> Dict[str, Any]:
    return {"status": "success", "identity": identity, "similarity": round(float(similarity), 4)}


def msg_failure(reason: str, message: str) -> Dict[str, Any]:
    if reason not in VALID_FAILURE_REASONS:
        raise ValueError("unknown failure reason: %r" % (reason,))
    return {"status": "failure", "reason": reason, "message": message}


def msg_cancelled() -> Dict[str, Any]:
    return {"status": "cancelled"}


def msg_pong() -> Dict[str, Any]:
    return {"status": "pong"}


# ---------------------------------------------------------------------------
# Parsing (either direction)
# ---------------------------------------------------------------------------


class ProtocolError(ValueError):
    """Raised when a received line is not a valid protocol message."""


def decode_line(line: bytes) -> Dict[str, Any]:
    """Parse one ``\\n``-terminated line into a message dict.

    Raises ProtocolError on empty lines, oversized lines (> 64KB), invalid
    UTF-8, invalid JSON, or a JSON value that is not an object.
    """
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            "message exceeds the %d-byte limit" % MAX_MESSAGE_BYTES
        )
    if not line or not line.strip():
        raise ProtocolError("empty protocol line")
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("protocol line is not valid UTF-8: %s" % exc) from exc
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("protocol line is not valid JSON: %s" % exc) from exc
    if not isinstance(obj, dict):
        raise ProtocolError("protocol message must be a JSON object")
    return obj


def parse_authenticate(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a CP->engine authenticate command; returns normalized params.

    Deprecated: prefer validate_cp_message, which whitelists every field.
    Raises ProtocolError on unknown strictness or a non-positive timeout.
    """
    timeout_ms = msg.get("timeout_ms", 15000)
    strictness = msg.get("strictness", "heavy")
    if not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0:
        raise ProtocolError("timeout_ms must be a positive number")
    if strictness not in VALID_STRICTNESS:
        raise ProtocolError("strictness must be one of %s" % sorted(VALID_STRICTNESS))
    return {"timeout_ms": int(timeout_ms), "strictness": strictness}


# ---------------------------------------------------------------------------
# Strict schema validation (every inbound message, no exceptions)
# ---------------------------------------------------------------------------
#
# Whitelist-based: the exact set of allowed fields, their types, and their
# value ranges. Anything else — unknown command, unknown field, wrong type,
# out-of-range value — is a ProtocolError and the connection is dropped.
# There is deliberately no generic dispatch: adding a new command means
# adding an explicit branch here AND in the engine's handler.


def _reject_extra_fields(msg: Dict[str, Any], allowed: frozenset, cmd: str) -> None:
    extra = set(msg.keys()) - set(allowed)
    if extra:
        raise ProtocolError(
            "command %r has unknown field(s): %s" % (cmd, ", ".join(sorted(extra)))
        )


def validate_cp_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Strictly validate one CP->engine message. Returns normalized params.

    Raises ProtocolError on anything that is not exactly one of the three
    known commands with whitelisted fields, types, and value ranges.
    """
    if not isinstance(msg, dict):
        raise ProtocolError("protocol message must be a JSON object")
    cmd = msg.get("cmd")
    if cmd not in VALID_COMMANDS:
        raise ProtocolError("unknown command: %r" % (cmd,))

    if cmd == "ping":
        _reject_extra_fields(msg, frozenset({"cmd"}), cmd)
        return {"cmd": "ping"}

    if cmd == "cancel":
        _reject_extra_fields(msg, frozenset({"cmd"}), cmd)
        return {"cmd": "cancel"}

    # authenticate
    _reject_extra_fields(msg, frozenset({"cmd", "timeout_ms", "strictness"}), cmd)
    timeout_ms = msg.get("timeout_ms", 15000)
    # bool is an int subclass; reject it explicitly.
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise ProtocolError("timeout_ms must be an integer number of milliseconds")
    if not (MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS):
        raise ProtocolError(
            "timeout_ms must be between %d and %d" % (MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
        )
    strictness = msg.get("strictness", "heavy")
    if strictness not in VALID_STRICTNESS:
        raise ProtocolError("strictness must be one of %s" % sorted(VALID_STRICTNESS))
    return {"cmd": "authenticate", "timeout_ms": timeout_ms, "strictness": strictness}


def validate_engine_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Strictly validate one engine->CP message (used by tests and the CP).

    Raises ProtocolError on unknown statuses, bad failure reasons, or wrong
    field types. The engine only ever emits these shapes.
    """
    if not isinstance(msg, dict):
        raise ProtocolError("protocol message must be a JSON object")
    status = msg.get("status")
    if status not in VALID_STATUSES:
        raise ProtocolError("unknown status: %r" % (status,))

    if status == "scanning":
        _reject_extra_fields(msg, frozenset({"status"}), status)
    elif status == "face_found":
        _reject_extra_fields(msg, frozenset({"status"}), status)
    elif status == "cancelled":
        _reject_extra_fields(msg, frozenset({"status"}), status)
    elif status == "pong":
        _reject_extra_fields(msg, frozenset({"status"}), status)
    elif status == "challenge":
        _reject_extra_fields(msg, frozenset({"status", "prompt"}), status)
        if not isinstance(msg.get("prompt"), str) or not msg["prompt"]:
            raise ProtocolError("challenge prompt must be a non-empty string")
    elif status == "success":
        _reject_extra_fields(msg, frozenset({"status", "identity", "similarity"}), status)
        if not isinstance(msg.get("identity"), str) or not msg["identity"]:
            raise ProtocolError("success identity must be a non-empty string")
        sim = msg.get("similarity")
        if isinstance(sim, bool) or not isinstance(sim, (int, float)):
            raise ProtocolError("success similarity must be a number")
        if not (-1.0 <= float(sim) <= 1.0):
            raise ProtocolError("success similarity out of range")
    elif status == "failure":
        _reject_extra_fields(msg, frozenset({"status", "reason", "message"}), status)
        if msg.get("reason") not in VALID_FAILURE_REASONS:
            raise ProtocolError("unknown failure reason: %r" % (msg.get("reason"),))
        if not isinstance(msg.get("message"), str) or not msg["message"]:
            raise ProtocolError("failure message must be a non-empty string")
    return msg


def is_terminal(msg: Dict[str, Any]) -> bool:
    """True if the engine message ends a scan (success/failure/cancelled)."""
    return msg.get("status") in ("success", "failure", "cancelled")


class LineBuffer:
    """Accumulates raw bytes from a stream into complete ``\\n``-terminated lines.

    The buffer is capped at MAX_MESSAGE_BYTES: a peer that sends more without
    a newline is protocol-violating, and the buffer is reset with a
    ProtocolError instead of growing without bound.
    """

    def __init__(self, max_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self._buf = bytearray()
        self._max_bytes = max_bytes

    def feed(self, chunk: bytes) -> list:
        """Feed bytes; returns the list of complete lines (without newlines)."""
        self._buf.extend(chunk)
        if len(self._buf) > self._max_bytes:
            self._buf.clear()
            raise ProtocolError(
                "message exceeds the %d-byte limit" % self._max_bytes
            )
        lines = []
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            lines.append(bytes(self._buf[:idx]))
            del self._buf[: idx + 1]
        return lines
