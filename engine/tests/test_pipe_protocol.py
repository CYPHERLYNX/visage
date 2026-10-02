"""Tests for the JSONL pipe protocol: framing, strict schema validation,
and the trust-model guarantees (no unlock command, size caps)."""

import pytest

from engine import pipe_protocol as proto


# -- framing ---------------------------------------------------------------

def test_encode_decode_roundtrip():
    msg = proto.msg_success("Fahad", 0.71234)
    assert proto.decode_line(proto.encode_message(msg)) == {
        "status": "success", "identity": "Fahad", "similarity": 0.7123}


def test_decode_rejects_empty():
    with pytest.raises(proto.ProtocolError):
        proto.decode_line(b"")
    with pytest.raises(proto.ProtocolError):
        proto.decode_line(b"   \n")


def test_decode_rejects_bad_json():
    with pytest.raises(proto.ProtocolError):
        proto.decode_line(b"{not json}\n")


def test_decode_rejects_non_object():
    with pytest.raises(proto.ProtocolError):
        proto.decode_line(b"[1,2,3]\n")


def test_decode_rejects_bad_utf8():
    with pytest.raises(proto.ProtocolError):
        proto.decode_line(b"\xff\xfe\n")


def test_decode_rejects_oversized_line():
    with pytest.raises(proto.ProtocolError):
        proto.decode_line(b"x" * (proto.MAX_MESSAGE_BYTES + 1))


def test_line_buffer_splits_chunks():
    buf = proto.LineBuffer()
    assert buf.feed(b'{"cmd": "pi') == []
    assert buf.feed(b'ng"}\n{"cmd": "ca') == [b'{"cmd": "ping"}']
    assert buf.feed(b'ncel"}\n') == [b'{"cmd": "cancel"}']


def test_line_buffer_caps_growth():
    buf = proto.LineBuffer(max_bytes=16)
    with pytest.raises(proto.ProtocolError):
        buf.feed(b"x" * 32)  # no newline, over cap -> reset + error


def test_max_message_bytes_is_64k():
    assert proto.MAX_MESSAGE_BYTES == 64 * 1024


# -- strict CP->engine validation ------------------------------------------

def test_validate_ping_minimal():
    assert proto.validate_cp_message({"cmd": "ping"}) == {"cmd": "ping"}


def test_validate_authenticate_defaults():
    assert proto.validate_cp_message({"cmd": "authenticate"}) == {
        "cmd": "authenticate", "timeout_ms": 15000, "strictness": "heavy"}


def test_validate_authenticate_full():
    out = proto.validate_cp_message(
        {"cmd": "authenticate", "timeout_ms": 5000, "strictness": "light"})
    assert out == {"cmd": "authenticate", "timeout_ms": 5000, "strictness": "light"}


def test_validate_rejects_unknown_command():
    # There is deliberately no "unlock" command and no generic dispatch.
    for cmd in ("unlock", "UNLOCK", "submit_password", "authenticate2", "", None):
        with pytest.raises(proto.ProtocolError):
            proto.validate_cp_message({"cmd": cmd})


def test_validate_rejects_extra_fields():
    with pytest.raises(proto.ProtocolError):
        proto.validate_cp_message({"cmd": "ping", "extra": 1})
    with pytest.raises(proto.ProtocolError):
        proto.validate_cp_message(
            {"cmd": "authenticate", "timeout_ms": 5000, "bypass": True})


def test_validate_rejects_bad_timeout_type():
    for bad in ("5000", 5.5, True, None, [5000]):
        with pytest.raises(proto.ProtocolError):
            proto.validate_cp_message({"cmd": "authenticate", "timeout_ms": bad})


def test_validate_rejects_timeout_out_of_range():
    for bad in (0, 999, 120001, -5000):
        with pytest.raises(proto.ProtocolError):
            proto.validate_cp_message({"cmd": "authenticate", "timeout_ms": bad})


def test_validate_rejects_bad_strictness():
    with pytest.raises(proto.ProtocolError):
        proto.validate_cp_message({"cmd": "authenticate", "strictness": "ultra"})


def test_validate_rejects_non_dict():
    with pytest.raises(proto.ProtocolError):
        proto.validate_cp_message(["cmd", "ping"])


# -- strict engine->CP validation -----------------------------------------

def test_validate_engine_messages_ok():
    assert proto.validate_engine_message(proto.msg_scanning())
    assert proto.validate_engine_message(proto.msg_face_found())
    assert proto.validate_engine_message(proto.msg_challenge("Turn your head left"))
    assert proto.validate_engine_message(proto.msg_success("Fahad", 0.7))
    assert proto.validate_engine_message(proto.msg_failure("spoof", "Nope"))
    assert proto.validate_engine_message(proto.msg_cancelled())
    assert proto.validate_engine_message(proto.msg_pong())


def test_validate_engine_rejects_unknown_status():
    with pytest.raises(proto.ProtocolError):
        proto.validate_engine_message({"status": "unlocking"})


def test_validate_engine_rejects_bad_failure_reason():
    with pytest.raises(proto.ProtocolError):
        proto.validate_engine_message(
            {"status": "failure", "reason": "nope", "message": "x"})


def test_validate_engine_rejects_bad_similarity():
    with pytest.raises(proto.ProtocolError):
        proto.validate_engine_message(
            {"status": "success", "identity": "Fahad", "similarity": 99.0})


def test_msg_failure_rejects_unknown_reason():
    with pytest.raises(ValueError):
        proto.msg_failure("hacked", "x")


# -- terminal / builders ---------------------------------------------------

def test_is_terminal():
    assert proto.is_terminal({"status": "success"})
    assert proto.is_terminal({"status": "failure"})
    assert proto.is_terminal({"status": "cancelled"})
    assert not proto.is_terminal({"status": "scanning"})
    assert not proto.is_terminal({"status": "challenge", "prompt": "x"})


def test_challenge_carries_prompt():
    assert proto.msg_challenge("Turn your head left")["prompt"] == "Turn your head left"


def test_no_unlock_builder_exists():
    # The protocol must not grow an unlock/credential message by accident.
    builders = [n for n in dir(proto) if n.startswith("msg_")]
    assert not any("unlock" in n or "credential" in n or "password" in n
                   for n in builders), builders
