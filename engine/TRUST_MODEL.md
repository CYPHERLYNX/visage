# Visage Engine — Trust Model

This document states, in one place, what the face-unlock engine is trusted
with, what it is not, and how the code enforces the boundary. It is written
for the adversarial reviewer: every claim names the file that implements it.

## Roles

| Component | Process | Trusted with |
|---|---|---|
| **Engine** (`visage_engine.py`) | Windows service, runs as SYSTEM | Camera frames (in memory only), face embeddings, liveness verdicts |
| **Credential provider (CP)** | Winlogon / LogonUI | The password vault (`vault.dat`), submitting credentials to Windows |
| **Manager app** | User session | Enrollment UI, `enroll.py` / `identities.py` / `vault.py` |

The engine and the CP communicate over the named pipe
`\\.\pipe\VisageAuth` using the JSONL protocol documented in
`pipe_protocol.py`.

## 1. The pipe carries scan evidence only — there is no "unlock" command

The engine accepts exactly three commands: `authenticate`, `cancel`, `ping`
(`pipe_protocol.VALID_COMMANDS`). The engine emits only statuses, challenge
prompts, and failure reasons.

Deliberately absent from the protocol:

* No message requests the stored password or any credential.
* No message triggers credential submission — submission code lives only in
  the CP, which acts solely on `{"status": "success"}`.
* No message bypasses matching or liveness. There is no flag, override, or
  "test mode" reachable from the pipe.

The unlock decision (identity match AND liveness verdict) is computed inside
the engine; the CP cannot influence it beyond starting/cancelling a scan.
Even a fully compromised engine process cannot exfiltrate the Windows
password, because the password never enters the engine's address space —
`visage_engine.py` never imports the vault's credential functions.

`validate_cp_message()` enforces this with a whitelist: exact command set,
exact field set per command, exact types, and value ranges
(`timeout_ms` 1000–120000, `strictness` in {light, heavy, off}). Unknown
commands, unknown fields, wrong types, and out-of-range values are rejected.

## 2. Named-pipe access control

`PIPE_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)"` (`visage_engine.py`):

* `D:P` — DACL, protected from inheriting parent ACEs.
* `(A;;GA;;;SY)` — allow full access to SYSTEM.
* `(A;;GA;;;BA)` — allow full access to built-in Administrators.
* Everyone else is denied by omission.

The descriptor is built with
`ConvertStringSecurityDescriptorToSecurityDescriptorW` and passed as
`lpSecurityAttributes` to `CreateNamedPipeW`, so the DACL exists from the
moment the pipe exists — no window where the pipe is world-open. If the
descriptor cannot be built, the service refuses to start (fail closed).

The TCP localhost fallback (`serve_tcp`, non-Windows dev/testing only)
binds 127.0.0.1 exclusively; it never listens on an external interface.

## 3. Input validation: strict, capped, fail-closed

* Every inbound message is capped at 64KB (`MAX_MESSAGE_BYTES`). `LineBuffer`
  resets instead of growing past the cap; `decode_line` rejects oversized
  lines before JSON parsing.
* No pickle, no eval, no dynamic dispatch on message contents anywhere in
  the package (grep-verifiable: the words appear only in this document and
  in comments stating their absence).
* The first malformed message on a connection produces a human-readable
  error and the connection is dropped (`_handle_client`). Unexpected
  exceptions in the client loop are logged; the service keeps running.

## 4. Vault and memory hygiene

* Passwords are never written to disk in plaintext. `vault.dat` holds only
  the envelope described below.
* Envelope encryption (`vault.seal`/`vault.unseal`): a fresh random
  AES-256-GCM data key per file encrypts the payload; the data key is
  wrapped by DPAPI LocalMachine scope (`CryptProtectData`). On non-Windows,
  a dev-only file key wraps it, with a loud warning on every use — dev only,
  never ships.
* Secret-holding mutable buffers (`bytearray`) are zeroed via `vault.zero()`
  immediately after use: the JSON payload before file write, the data key
  after wrapping, the decrypted plaintext after parsing.
* What cannot be zeroed: CPython `str`/`bytes` are immutable, so parsed JSON
  strings and the returned password string live until garbage collection.
  Callers keep the credential dict's lifetime short and never copy the
  password. Documented in `vault.py`.
* No secret ever appears in logs, error messages, reprs, or tracebacks: every
  exception from `vault.py` carries a generic message (e.g. "vault is
  corrupt or undecryptable"), never payload content.

## 5. Camera frames and biometrics

* Camera frames are processed in memory and never written to disk — no frame
  dumps, no debug images, no temp files. `grep` the package: the only disk
  writes are the encrypted vault, the encrypted identity store, and logs.
* Images are never stored, not even during enrollment: `enroll.py` converts
  each accepted frame to a 512-d embedding and discards the frame.
* Embeddings at rest are envelope-encrypted exactly like the vault
  (`identities.py` → `vault.seal`), with DPAPI-bound keys on Windows.
* No network calls happen at runtime. The only module that touches the
  network is `fetch_models.py` (one-time model download over pinned HTTPS
  with SHA-256 verification; fails closed and deletes the partial file on
  mismatch).

## 6. Copy standard

Every user-facing string — challenge prompts, failure messages, setup
output — is held to the bar: *would Apple ship this?* Calm, confident,
plain English. No jargon, no raw errors, no tracebacks reaching a user.
Diagnostic detail goes to the service log, never to the user.
