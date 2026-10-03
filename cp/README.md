# VisageCP - Face Unlock credential provider for Windows

A native C++ COM credential provider (`VisageCP.dll`) that adds a single
"Face Unlock" tile to the Windows sign-in and lock screens. It is the
Windows rebuild of the `jonnyoo/glance` Mac unlock pipeline: tile, scan
states, challenge prompt, password submission - expressed through the
Windows Credential Provider API (SampleV2CredentialProvider architecture).

It ADDS one tile. It never replaces, filters, hides, or reorders the
built-in password/PIN providers. See `TRUST_MODEL.md`.

## How it works

```
User selects "Face Unlock" tile
  -> SetSelected starts a worker thread (UI thread never blocks)
  -> worker connects to \\.\pipe\VisageAuth (spawns the engine if absent)
  -> sends {"cmd":"authenticate","timeout_ms":15000}
  -> engine streams status lines; the tile shows them live:
       scanning -> "Looking for your face..."
       face_found -> "Hold still..."
       challenge  -> the engine's prompt, e.g. "Turn your head left"
       success    -> "Recognized. Press Enter to sign in."
       failure    -> "<message> Try again." (max 3 attempts, then the
                     "Face unlock unavailable - use your password or PIN."
                     fallback)
  -> user presses Enter -> GetSerialization reads C:\ProgramData\Visage\vault.dat,
     DPAPI-decrypts it (LocalMachine scope), builds a KERB_INTERACTIVE_UNLOCK_LOGON
     blob, and hands it to LSA. The password buffer is SecureZeroMemory'd
     immediately after packing.
```

Cancel: selecting another tile, or the tile's Cancel button while scanning,
sends `{"cmd":"cancel"}` and returns the tile to idle.

## Layout

```
cp/
  VisageCP.sln / VisageCP.vcxproj   VS2022, x64 only, /W4 + SDLCheck
  register.ps1 / unregister.ps1 admin registration (fully reversible)
  BUILD.md                      exact build steps
  TEST_PLAN.md                   VM-first test matrix + recovery
  TRUST_MODEL.md                 pipe/vault/process trust + failure analysis
  src/
    Dll.cpp                     DllMain, class factory, DllRegister/UnregisterServer
    Guids.h                     CLSID {794AAC04-6CB6-4C77-A828-27C20D0F820D}
    VisageProvider.*              ICredentialProvider (one tile, logon+unlock only)
    VisageCredential.*            ICredentialProviderCredential + scan state machine
    PipeClient.*                JSONL pipe client, timeouts, engine spawning
    Vault.*                     DPAPI vault read (decrypt-at-submit only)
    KerbPack.*                  KERB_INTERACTIVE_UNLOCK_LOGON builder
    JsonMini.h                  strict flat-JSON reader (whitelists, 8 KB cap)
    Common.h                    zeroing buffers, debug tracing (Release: compiled out)
    resources.rc / resource.h / VisageCP.def / res/tile.bmp
  test/
    fake_engine.py              scripted pipe server (success/no_face/spoof/
                                timeout/slow_cancel/challenge)
    make_test_vault.ps1         creates a DPAPI test vault.dat on a test VM
```

## Configuration (all machine-wide, no user paths)

| Setting | Location |
|---|---|
| Engine script | `C:\ProgramData\Visage\engine\visage_engine.py` (fixed path) |
| Vault | `C:\ProgramData\Visage\vault.dat` (DPAPI LocalMachine; ACL: SYSTEM + Administrators) |
| Python for engine spawn | `HKLM\SOFTWARE\Visage\PythonPath` (REG_SZ, written by installer); fallback: `C:\Windows\py.exe`; last resort: PATH `pythonw.exe` |
| Pipe | `\\.\pipe\VisageAuth` (server side enforces SYSTEM/Administrators DACL) |

## Glance ditto mapping (behavior)

Studied: `FaceUnlockCoordinator.swift`, `NotchOverlayController.swift`,
`ScanAnimationView.swift` in `glance-reference/`.

| Glance (Mac) | VisageCP (this) |
|---|---|
| Overlay phases: closed -> scanning -> success / failure | Tile states: Idle -> Scanning -> FaceFound -> Challenge -> Success / Failed |
| `statusMessage = "Looking for your face..."` | Same tile copy |
| Outcomes: matched / consistentlyWrongFace ("Face not recognized.") / spoofSuspected ("Couldn't confirm a live face.") / noResolution ("No face detected.") | Engine `failure` reasons map to the same three strings (+ timeout/no_camera); see `ReasonToCopy` |
| Retry hint: "hover the notch to try again" | "Try again." + focused Try Again button (Enter retries) |
| One auto-retry per lock session (`hasAutoRetriedForCurrentLock`) | DEVIATION: no automatic retry. Retry is user-initiated (Enter). Rationale: on the shared sign-in screen the camera must not loop without an explicit user action; the engine owns its own timing. Max 3 attempts per tile selection, then the password/PIN fallback. |
| Precondition: session unlocked + password stored, else plain message, no scan | Precondition: vault readable, checked BEFORE the camera runs (fail fast); else the unavailable fallback |
| `wrongFaceGraceDuration`: sustained non-match before failing | Owned by the engine (it sends one verdict per attempt) |
| Challenge prompts | Protocol extension: the engine streams `{"status":"challenge","prompt":"..."}`; the tile shows the prompt verbatim. Glance's pipeline is internal and has no equivalent message. |
| Unlock by typing the stored password (macOS has no auth API) | Unlock by submitting KERB_INTERACTIVE_UNLOCK_LOGON to LSA (the Windows-native path) |

## Verification checklist (needs Windows - cannot be done on Linux)

The code is written carefully but UNCOMPILED. Before shipping, the items
in BUILD.md section 8 plus these must be verified on a real machine:

1. It compiles clean at /W4 with the v143 toolset (names like
   `KerbUnlockLogon`, `MICROSOFT_KERBEROS_NAME_A`, `PIPE_READMODE_BYTE`
   exist in the installed SDK exactly as used).
2. `ICredentialProviderCredentialEvents` methods accept `hwndParent=NULL`
   for field pushes from the message window.
3. `SetFieldState` on the submit button from `RefreshTile` behaves (no
   re-entrancy surprises in LogonUI).
4. `LsaLookupAuthenticationPackage` with "Kerberos" returns a package id
   that accepts our `KERB_INTERACTIVE_UNLOCK_LOGON` for both
   `CPUS_LOGON` and `CPUS_UNLOCK_WORKSTATION` (with the stashed LUID).
5. `WaitNamedPipeW`/`CancelIoEx`/`GetOverlappedResult` interplay in
   `PipeClient::ReadLine` under real cancellation.
6. The full TEST_PLAN.md matrix, especially T5 (lock-screen LUID path)
   and T17 (stale-vault ReportResult path).
