"""
Password vault.

On-disk format: envelope encryption. A random 256-bit AES-GCM *data key* is
generated per file; the payload is encrypted with it, and the data key itself
is wrapped by Windows DPAPI (LocalMachine scope). The file holds only the
envelope JSON::

    {"version": 2, "data_alg": "AES-256-GCM",
     "key_wrap": "dpapi-localmachine",
     "wrapped_key": "<base64>", "nonce": "<base64>", "ciphertext": "<base64>"}

The inner plaintext is UTF-8 JSON ``{"version": 1, "username": "...",
"domain": "...", "password": "..."}``.

On non-Windows a dev-only fallback wraps the data key with an AES-GCM file
key, with a LOUD warning on every use. It is NOT a substitute for DPAPI and
must never ship.

Trust model: the *engine* (mien_engine.py) never touches this module's
credential functions — only the credential-provider companion does. The pipe
protocol has no message that can request credentials.

Memory hygiene
--------------
* Mutable buffers holding secrets (bytearray) are zeroed as soon as they are
  no longer needed — see ``zero()``.
* What we CANNOT zero: CPython ``str`` and ``bytes`` are immutable; copies
  (e.g. JSON parse output, the returned password string) live until garbage
  collection and may survive in freed memory. Callers should keep the
  credential dict's lifetime short and never copy the password elsewhere.
* Secrets never appear in log output, error messages, reprs, or tracebacks:
  every exception raised here carries only a generic, human-readable message.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import warnings
from typing import Dict, Tuple

VAULT_VERSION = 1      # inner credential JSON schema version
ENVELOPE_VERSION = 2   # outer envelope format version

_DEV_WARNING = (
    "MIEN DEV WARNING: vault key wrap uses the AES-GCM *file* fallback instead of "
    "Windows DPAPI. This is for development on non-Windows machines ONLY and must "
    "never ship. On Windows the data key is wrapped by DPAPI LocalMachine scope."
)


def _warn_dev() -> None:
    warnings.warn(_DEV_WARNING, RuntimeWarning, stacklevel=3)
    print(_DEV_WARNING, file=sys.stderr)


def zero(buf: bytearray) -> None:
    """Best-effort wipe of a mutable secret buffer."""
    for i in range(len(buf)):
        buf[i] = 0


# ---------------------------------------------------------------------------
# Windows DPAPI key-wrap backend (ctypes, no pywin32 needed)
# ---------------------------------------------------------------------------


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi_wrap(data_key: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_LOCAL_MACHINE = 0x4
    in_blob = _DataBlob(len(data_key), (ctypes.c_ubyte * len(data_key)).from_buffer_copy(data_key))
    out_blob = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, CRYPTPROTECT_LOCAL_MACHINE, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError("DPAPI key wrap failed (winerror %d)" % kernel32.GetLastError())
    try:
        return bytes(bytearray(out_blob.pbData[: out_blob.cbData]))
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unwrap(wrapped: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob = _DataBlob(len(wrapped), (ctypes.c_ubyte * len(wrapped)).from_buffer_copy(wrapped))
    out_blob = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError("DPAPI key unwrap failed (winerror %d)" % kernel32.GetLastError())
    try:
        return bytes(bytearray(out_blob.pbData[: out_blob.cbData]))
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ---------------------------------------------------------------------------
# Dev-only file key-wrap backend (non-Windows)
# ---------------------------------------------------------------------------


def _dev_key_path(hint_path: str) -> str:
    return hint_path + ".devkey"


def _dev_file_key(hint_path: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key_path = _dev_key_path(hint_path)
    if os.path.isfile(key_path):
        with open(key_path, "rb") as fh:
            file_key = fh.read()
        if len(file_key) != 32:
            raise OSError("dev key file is corrupt")
        return file_key
    file_key = AESGCM.generate_key(bit_length=256)
    with open(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
        fh.write(file_key)
    return file_key


def _dev_wrap_key(data_key: bytes, hint_path: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    _warn_dev()
    nonce = os.urandom(12)
    return nonce + AESGCM(_dev_file_key(hint_path)).encrypt(nonce, bytes(data_key), None)


def _dev_unwrap_key(wrapped: bytes, hint_path: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    _warn_dev()
    if len(wrapped) < 12 + 16:
        raise ValueError("key wrap is corrupt or truncated")
    try:
        return AESGCM(_dev_file_key(hint_path)).decrypt(wrapped[:12], wrapped[12:], None)
    except Exception as exc:
        raise ValueError("key unwrap failed: wrong key or corrupt file") from exc


def _wrap_data_key(data_key: bytes, hint_path: str) -> Tuple[bytes, str]:
    if os.name == "nt":
        return _dpapi_wrap(data_key), "dpapi-localmachine"
    return _dev_wrap_key(data_key, hint_path), "dev-file-aesgcm"


def _unwrap_data_key(wrapped: bytes, method: str, hint_path: str) -> bytes:
    if method == "dpapi-localmachine":
        if os.name != "nt":
            raise ValueError("vault was sealed with Windows DPAPI and cannot be opened here")
        return _dpapi_unwrap(wrapped)
    if method == "dev-file-aesgcm":
        return _dev_unwrap_key(wrapped, hint_path)
    raise ValueError("vault uses an unknown key-wrap method")


# ---------------------------------------------------------------------------
# Envelope seal / unseal
# ---------------------------------------------------------------------------


def seal(plaintext: bytes, hint_path: str) -> bytes:
    """Envelope-encrypt ``plaintext``; returns the envelope JSON bytes.

    A fresh random AES-256-GCM data key is generated per call and wiped from
    memory after the data key is wrapped.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = bytearray(AESGCM.generate_key(bit_length=256))
    try:
        aesgcm = AESGCM(bytes(key))
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, bytes(plaintext), None)
        wrapped, method = _wrap_data_key(bytes(key), hint_path)
    finally:
        zero(key)
    envelope = {
        "version": ENVELOPE_VERSION,
        "data_alg": "AES-256-GCM",
        "key_wrap": method,
        "wrapped_key": base64.b64encode(wrapped).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


def unseal(blob: bytes, hint_path: str) -> bytes:
    """Decrypt an envelope produced by :func:`seal`.

    Raises ValueError with a generic message on any corrupt/foreign input —
    never includes secret material.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        envelope = json.loads(bytes(blob).decode("utf-8"))
    except Exception as exc:
        raise ValueError("vault file is not valid envelope JSON") from exc
    try:
        if not isinstance(envelope, dict) or envelope.get("version") != ENVELOPE_VERSION:
            raise ValueError("unsupported envelope version")
        if envelope.get("data_alg") != "AES-256-GCM":
            raise ValueError("unsupported data algorithm")
        method = envelope["key_wrap"]
        wrapped = base64.b64decode(envelope["wrapped_key"])
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
    except (KeyError, TypeError, ValueError, base64.binascii.Error) as exc:
        raise ValueError("vault envelope is malformed") from exc

    key = bytearray(_unwrap_data_key(wrapped, method, hint_path))
    try:
        return AESGCM(bytes(key)).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError("vault decryption failed: wrong key or corrupt file") from exc
    finally:
        zero(key)


# ---------------------------------------------------------------------------
# Credential vault API (used by the credential provider, never by the engine)
# ---------------------------------------------------------------------------


def default_vault_path() -> str:
    if os.name == "nt":
        return r"C:\ProgramData\Mien\vault.dat"
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "vault.dev.dat")


def save_credentials(vault_path: str, username: str, domain: str, password: str) -> None:
    """Encrypt and store credentials. Never logs or prints the secret values."""
    plain = bytearray(
        json.dumps(
            {"version": VAULT_VERSION, "username": username, "domain": domain, "password": password},
            ensure_ascii=False,
        ).encode("utf-8")
    )
    try:
        blob = seal(bytes(plain), vault_path)
    finally:
        zero(plain)
    os.makedirs(os.path.dirname(os.path.abspath(vault_path)), exist_ok=True)
    tmp = vault_path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
        fh.write(blob)
    os.replace(tmp, vault_path)


def load_credentials(vault_path: str) -> Dict[str, str]:
    """Decrypt and return {"username","domain","password"}.

    The returned dict holds immutable strings — keep its lifetime short and
    never copy the password elsewhere (see module docstring).
    """
    if not os.path.isfile(vault_path):
        raise FileNotFoundError("no vault found at %s" % vault_path)
    with open(vault_path, "rb") as fh:
        blob = fh.read()
    try:
        plain = bytearray(unseal(blob, vault_path))
    except (ValueError, OSError) as exc:
        raise ValueError("vault at %s is corrupt or undecryptable" % vault_path) from exc
    try:
        payload = json.loads(bytes(plain).decode("utf-8"))
    except Exception as exc:
        raise ValueError("vault at %s holds invalid credential JSON" % vault_path) from exc
    finally:
        zero(plain)
    if not isinstance(payload, dict) or payload.get("version") != VAULT_VERSION:
        raise ValueError("vault at %s has an unsupported format" % vault_path)
    for key in ("username", "domain", "password"):
        if key not in payload:
            raise ValueError("vault at %s is missing a required field" % vault_path)
    return {"username": payload["username"], "domain": payload["domain"], "password": payload["password"]}


def delete_vault(vault_path: str) -> bool:
    """Remove the vault file (and dev key, if any). Returns True if removed."""
    removed = False
    for path in (vault_path, _dev_key_path(vault_path)):
        if os.path.isfile(path):
            os.remove(path)
            removed = True
    return removed


def vault_exists(vault_path: str) -> bool:
    return os.path.isfile(vault_path)
