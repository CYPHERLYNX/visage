#!/usr/bin/env python3
"""Visage engine self-test.

Run by installer/setup.ps1 step 7 as ``python engine\\selftest.py``.
Exit 0 = every check passed; exit 1 = something failed (the failure is
named in the output). No camera, no models, no Windows-only APIs needed —
this exercises the pipe protocol, the machine-config loader, and the vault
envelope, which are the parts setup must be able to trust.

Usage: python engine/selftest.py   (from the project root, or anywhere)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # project root, so `import engine` works

from engine import pipe_protocol as proto  # noqa: E402
from engine import vault  # noqa: E402

try:
    from engine.visage_engine import load_machine_config  # noqa: E402
except Exception as exc:  # missing cv2/numpy: setup step 3 did not finish
    print("FAIL: could not import the engine (%s)." % exc)
    print("Run setup.ps1 again so the Python dependencies install, then re-run.")
    sys.exit(1)


class Failed(Exception):
    pass


def check(name, cond):
    print(("PASS" if cond else "FAIL") + ": " + name)
    if not cond:
        raise Failed(name)


def expect_protocol_error(name, fn, *args):
    try:
        fn(*args)
    except proto.ProtocolError:
        print("PASS: " + name)
        return
    except Exception as exc:  # noqa: BLE001 - any other error is a failure too
        print("FAIL: %s (wrong exception: %r)" % (name, exc))
        raise Failed(name) from exc
    print("FAIL: " + name + " (no error raised)")
    raise Failed(name)


def test_protocol():
    # Round-trip of every engine->CP message shape.
    for msg in (
        proto.msg_scanning(),
        proto.msg_face_found(),
        proto.msg_challenge("Turn your head left"),
        proto.msg_success("Fahad", 0.73),
        proto.msg_failure("no_match", "Face not recognized."),
        proto.msg_cancelled(),
        proto.msg_pong(),
    ):
        decoded = proto.decode_line(proto.encode_message(msg))
        check("protocol round-trip: %s" % msg["status"], decoded == msg)
        proto.validate_engine_message(decoded)

    # CP->engine authenticate: defaults and strictness pass through.
    params = proto.validate_cp_message({"cmd": "authenticate", "timeout_ms": 15000})
    check("authenticate defaults", params == {"cmd": "authenticate",
                                              "timeout_ms": 15000,
                                              "strictness": "heavy"})
    params = proto.validate_cp_message(
        {"cmd": "authenticate", "timeout_ms": 5000, "strictness": "light"})
    check("authenticate explicit strictness", params["strictness"] == "light")

    # Whitelist rejections.
    expect_protocol_error("reject unknown command",
                          proto.validate_cp_message, {"cmd": "unlock"})
    expect_protocol_error("reject extra field",
                          proto.validate_cp_message,
                          {"cmd": "ping", "extra": 1})
    expect_protocol_error("reject bool timeout",
                          proto.validate_cp_message,
                          {"cmd": "authenticate", "timeout_ms": True})
    expect_protocol_error("reject float timeout",
                          proto.validate_cp_message,
                          {"cmd": "authenticate", "timeout_ms": 1.5})
    expect_protocol_error("reject timeout too small",
                          proto.validate_cp_message,
                          {"cmd": "authenticate", "timeout_ms": 10})
    expect_protocol_error("reject timeout too large",
                          proto.validate_cp_message,
                          {"cmd": "authenticate", "timeout_ms": 99999999})
    expect_protocol_error("reject bad strictness",
                          proto.validate_cp_message,
                          {"cmd": "authenticate", "strictness": "paranoid"})
    expect_protocol_error("reject unknown status",
                          proto.validate_engine_message, {"status": "unlocked"})
    expect_protocol_error("reject bad failure reason",
                          proto.validate_engine_message,
                          {"status": "failure", "reason": "nope", "message": "x"})

    # Size caps.
    expect_protocol_error("reject oversized line",
                          proto.decode_line, b"x" * (proto.MAX_MESSAGE_BYTES + 1))
    buf = proto.LineBuffer()
    try:
        buf.feed(b"y" * (proto.MAX_MESSAGE_BYTES + 1))
        print("FAIL: LineBuffer cap (no error raised)")
        raise Failed("LineBuffer cap")
    except proto.ProtocolError:
        print("PASS: LineBuffer cap")


def _write_config(obj=None, raw=None):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(raw if raw is not None else json.dumps(obj))
    return path


def test_machine_config():
    defaults = dict(load_machine_config(path=os.path.join(HERE, "definitely-not-here.json")))
    check("missing config -> defaults",
          defaults["match_threshold"] == 0.66 and defaults["strictness"] == "heavy")

    p = _write_config({"match_threshold": 0.5, "strictness": "LIGHT",
                       "future_key": [1, 2, 3]})
    try:
        cfg = load_machine_config(path=p)
        check("valid config applied (case-insensitive strictness)",
              cfg["match_threshold"] == 0.5 and cfg["strictness"] == "light")
    finally:
        os.unlink(p)

    for bad in ({"match_threshold": "high"}, {"match_threshold": 5.0},
                {"match_threshold": True}, {"strictness": "paranoid"},
                {"strictness": 3}):
        p = _write_config(bad)
        try:
            cfg = load_machine_config(path=p)
            key = "match_threshold" if "match_threshold" in bad else "strictness"
            check("bad %s ignored -> default" % key,
                  cfg[key] == defaults[key])
        finally:
            os.unlink(p)

    p = _write_config(raw="{not json")
    try:
        cfg = load_machine_config(path=p)
        check("corrupt config -> defaults", cfg == defaults)
    finally:
        os.unlink(p)

    p = _write_config(raw="[1,2]")
    try:
        cfg = load_machine_config(path=p)
        check("non-object config -> defaults", cfg == defaults)
    finally:
        os.unlink(p)


def test_vault_envelope():
    blob = vault.seal(b'{"version":1}', "selftest-hint")
    check("vault seal/unseal round-trip",
          vault.unseal(blob, "selftest-hint") == b'{"version":1}')
    tampered = bytearray(blob)
    tampered[-10] ^= 0xFF
    try:
        vault.unseal(bytes(tampered), "selftest-hint")
        print("FAIL: tampered vault envelope accepted")
        raise Failed("tamper rejected")
    except ValueError:
        print("PASS: tampered vault envelope rejected")


def main():
    print("Visage engine self-test")
    print("---------------------")
    try:
        test_protocol()
        test_machine_config()
        test_vault_envelope()
    except Failed as exc:
        print("")
        print("Self-test FAILED at: %s" % exc)
        print("Fix what it names above, then re-run setup.ps1.")
        return 1
    print("")
    print("All self-test checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
