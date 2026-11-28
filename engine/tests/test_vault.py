"""Tests for the credential vault: envelope encryption roundtrip, hygiene
(no plaintext, no secrets in errors), and memory-hygiene helpers."""

import base64
import json
import os

import pytest

from engine import vault


def test_seal_unseal_roundtrip(tmp_path):
    hint = str(tmp_path / "v.dat")
    blob = vault.seal(b'{"hello": "world"}', hint)
    assert vault.unseal(blob, hint) == b'{"hello": "world"}'


def test_envelope_structure():
    import tempfile
    hint = tempfile.mktemp()
    blob = vault.seal(b"secret", hint)
    env = json.loads(blob.decode("utf-8"))
    assert env["version"] == 2
    assert env["data_alg"] == "AES-256-GCM"
    assert env["key_wrap"] in ("dpapi-localmachine", "dev-file-aesgcm")
    for field in ("wrapped_key", "nonce", "ciphertext"):
        base64.b64decode(env[field])  # must be valid base64


def test_data_key_is_fresh_per_file(tmp_path):
    hint = str(tmp_path / "v.dat")
    e1 = json.loads(vault.seal(b"same", hint).decode("utf-8"))
    e2 = json.loads(vault.seal(b"same", hint).decode("utf-8"))
    assert e1["wrapped_key"] != e2["wrapped_key"]
    assert e1["nonce"] != e2["nonce"]
    assert e1["ciphertext"] != e2["ciphertext"]


def test_unseal_rejects_tampered_ciphertext(tmp_path):
    hint = str(tmp_path / "v.dat")
    env = json.loads(vault.seal(b"secret", hint).decode("utf-8"))
    ct = bytearray(base64.b64decode(env["ciphertext"]))
    ct[0] ^= 0xFF
    env["ciphertext"] = base64.b64encode(bytes(ct)).decode("ascii")
    with pytest.raises(ValueError) as exc:
        vault.unseal(json.dumps(env).encode("utf-8"), hint)
    assert "secret" not in str(exc.value)


def test_unseal_rejects_garbage():
    with pytest.raises(ValueError):
        vault.unseal(b"not json at all", "/tmp/x.dat")


def test_unseal_rejects_unknown_version(tmp_path):
    hint = str(tmp_path / "v.dat")
    env = json.loads(vault.seal(b"secret", hint).decode("utf-8"))
    env["version"] = 99
    with pytest.raises(ValueError):
        vault.unseal(json.dumps(env).encode("utf-8"), hint)


def test_save_load_roundtrip(tmp_vault):
    vault.save_credentials(tmp_vault, "fahad", "FALAPTOP", "s3cret-pw")
    creds = vault.load_credentials(tmp_vault)
    assert creds == {"username": "fahad", "domain": "FALAPTOP", "password": "s3cret-pw"}


def test_no_plaintext_password_on_disk(tmp_vault):
    vault.save_credentials(tmp_vault, "fahad", "FALAPTOP", "s3cret-pw-xyz")
    raw = open(tmp_vault, "rb").read()
    assert b"s3cret-pw-xyz" not in raw
    assert b"fahad" not in raw


def test_load_missing_file_raises(tmp_vault):
    with pytest.raises(FileNotFoundError):
        vault.load_credentials(tmp_vault)


def test_load_corrupt_file_raises_without_secrets(tmp_vault):
    with open(tmp_vault, "wb") as fh:
        fh.write(b"\x00\x01\x02garbage")
    with pytest.raises(ValueError) as exc:
        vault.load_credentials(tmp_vault)
    assert "garbage" not in str(exc.value)


def test_delete_vault(tmp_vault):
    vault.save_credentials(tmp_vault, "u", "d", "p")
    assert vault.vault_exists(tmp_vault)
    assert vault.delete_vault(tmp_vault)
    assert not vault.vault_exists(tmp_vault)
    assert not vault.delete_vault(tmp_vault)


def test_zero_wipes_buffer():
    buf = bytearray(b"topsecret")
    vault.zero(buf)
    assert bytes(buf) == b"\x00" * 9


def test_dev_fallback_warns_loudly(tmp_vault):
    # On non-Windows the dev fallback must warn on every use.
    if os.name == "nt":
        pytest.skip("dev fallback is non-Windows only")
    with pytest.warns(RuntimeWarning):
        vault.save_credentials(tmp_vault, "u", "d", "p")
