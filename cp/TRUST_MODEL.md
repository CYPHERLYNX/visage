TRUST MODEL - Mien Credential Provider
========================================
ASCII-only document.

This file states exactly what the credential provider (MienCP.dll) trusts,
what it verifies, and what happens when anything fails. The provider runs
inside LogonUI.exe as SYSTEM on the secure desktop: every code path below
was written against the question "if this throws, crashes, or hangs, what
does the user see?" The answer is always the same: they can still sign in
with their password or PIN.

1. THE NAMED PIPE (\\.\pipe\MienAuth)
-------------------------------------
Role: the ONLY channel between the tile and the face engine.

- The provider is a pipe CLIENT only. It never creates, listens on, or
  takes over the pipe. (The engine worker owns the server side.)
- Trust rests on three legs:
  a) Pipe DACL: the engine's pipe server restricts access to SYSTEM and
     Administrators. A standard-user process cannot connect to it, and
     cannot squat on the name to impersonate the engine. (Enforced by the
     engine worker's service side - verify this in its review.)
  b) Spawn-what-you-talk-to: if the pipe is absent, the provider launches
     the engine itself (a single fixed script path,
     C:\ProgramData\Mien\engine\mien_engine.py, via a configured Python -
     never a user path) and then connects. The common case is therefore
     talking to a process it just started, not to a stranger holding the
     pipe name.
  c) No "unlock" command exists in the protocol. The provider sends
     {"cmd":"authenticate"} / {"cmd":"cancel"} and nothing else. There is
     no message any party can send that bypasses face matching or liveness:
     the provider submits Windows credentials only after receiving a
     well-formed {"status":"success"} line, and the matching/liveness
     verdict is produced entirely inside the engine.
- Residual risk (documented, accepted): a process already running as
  SYSTEM or Administrator could impersonate the engine on the pipe. That
  attacker already owns the machine (they can read the DPAPI LocalMachine
  vault directly, install drivers, etc.); the pipe adds no new privilege.

2. PIPE MESSAGE PARSING (strict)
--------------------------------
- Transport cap: a single line longer than 8 KB is rejected outright
  (protocol says 64 KB max; we are stricter). No unbounded allocation.
- Every line must parse as a flat JSON object with a "status" string, or
  it is ignored. More than 5 malformed lines in one scan aborts the scan
  to the "unavailable" tile state - garbage never crashes us and never
  spins us in a retry loop.
- "status" is matched against an exact whitelist:
  scanning | face_found | challenge | success | failure | cancelled.
  Anything else is ignored.
- "reason" is matched against an exact whitelist before it may influence
  tile copy: no_match | spoof | liveness | timeout | no_face | no_camera | error.
  Unknown reasons fall back to a generic message and are never echoed.
- "prompt" and "message" are human-readable text shown on the tile,
  truncated to 200 characters. They are display-only: they cannot change
  the state machine, trigger submission, or reach any log, file, or UI
  other than the tile's own status line.

3. THE VAULT (C:\ProgramData\Mien\vault.dat)
--------------------------------------------
- Format: DPAPI CryptUnprotectData with CRYPTPROTECT_LOCAL_MACHINE over
  UTF-8 JSON {"version":1,"username":"...","domain":"...","password":"..."}.
- LocalMachine scope means any local process CAN unprotect the blob, so
  confidentiality rests on the FILE ACL: the installer must ACL vault.dat
  to SYSTEM + Administrators only. Verify this on install; the provider
  cannot fix a loose ACL at runtime.
- Decryption happens ONLY at submission moment (inside GetSerialization,
  after a verified face), never earlier, never speculatively.
- The password lives in zeroing buffers (SecureWBuffer/SecureByteBuffer).
  It is copied into the KERB serialization and then SecureZeroMemory'd
  immediately, in ALL paths including failures. It is never written to
  disk, never logged (debug tracing is compiled out in Release and never
  carries secrets anyway), and never shown in any UI.
- A missing/corrupt/undecryptable vault degrades to the tile state
  "Face unlock unavailable - use your password or PIN." It never blocks
  sign-in and never crashes LogonUI.

4. PROCESS / THREADING
----------------------
- The DLL is Apartment-threaded. All COM calls happen on LogonUI's STA
  thread. The scan worker thread does pipe I/O only and marshals UI
  updates back via PostMessage to a hidden message-only window; it never
  touches a COM interface.
- Every wait is bounded: pipe reads (2 s slices, cancel-aware), engine
  connect (12 s, cancel-aware), overall scan (30 s hard cap), worker stop
  (5 s). There is no code path that can hang LogonUI indefinitely.
- Selecting another tile (SetDeselected) cancels the scan, tells the
  engine {"cmd":"cancel"}, and returns the tile to idle.

5. SUBMISSION
-------------
- On verified success the provider builds a KERB_INTERACTIVE_UNLOCK_LOGON
  blob (Kerberos auth package, looked up from LSA at load) and returns it
  via GetSerialization. LogonUI/LSA perform the actual authentication;
  the provider never logs on by itself.
- Unlock scenario (lock screen): the blob carries the locked session's
  LUID, stashed from SetSerialization. If no LUID was stashed, the
  provider REFUSES to submit rather than hand LSA a blob it cannot match.
- After submission, the "verified" flag is one-shot: ReportResult clears
  it on both success and failure, so a stale verification can never
  submit twice.

6. REGISTRATION REVERSIBILITY
-----------------------------
- register.ps1 / unregister.ps1 create and remove exactly two keys:
    HKCR\CLSID\{794AAC04-6CB6-4C77-A828-27C20D0F820D}[...]
    HKLM\SOFTWARE\...\Authentication\Credential Providers\{794AAC04-...}
  No other registry state is touched. Unregistering cannot disturb the
  built-in password/PIN providers.
- Recovery if the tile ever misbehaves: sign in with password/PIN (always
  available - we never hide those tiles) and run unregister.ps1 elevated;
  or Safe Mode -> regsvr32 /u -> delete the CLSID key -> reboot; or WinRE
  offline registry edit. See TEST_PLAN.md.

7. WHAT WE DELIBERATELY DO NOT DO
----------------------------------
- No network calls, ever.
- No camera access from the provider: all computer vision lives in the
  engine process.
- No password/PIN tiles are enumerated, filtered, hidden, or reordered by
  any code path (see the INVARIANT comment on CMienProvider).
- No auto-logon: SetSelected always returns *pbAutoLogon = FALSE and
  GetCredentialCount returns pbAutoLogonWithDefault = FALSE. A face scan
  must succeed AND the user must press Enter before anything submits.
