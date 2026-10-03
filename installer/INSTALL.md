# Visage - Installation

One command installs Visage (face unlock for Windows 10/11, 64-bit). No manual
steps: the script checks prerequisites, installs dependencies, downloads the
face models, writes the default config, and registers the Visage sign-in tile.

## Prerequisites

- Windows 10 or 11, 64-bit
- A webcam (built-in or USB)
- Python 3.10 or later, with `python` on your PATH
  (download from https://www.python.org/downloads/ - during install tick
  **"Add python.exe to PATH"**)
- An internet connection (for the Python packages and the face models)
- Administrator rights (setup asks to elevate itself if you forget)

## One command

Download the latest release zip, unzip it, then in PowerShell:

```powershell
cd <where-you-unzipped-it>\visage\installer
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

If you are not already elevated, setup prompts for admin rights (UAC) and
relaunches itself. From there it is hands-off until the summary at the end.

> **SmartScreen / "running scripts is disabled":** if Windows blocks the
> script, right-click `setup.ps1` is not needed - just use the
> `-ExecutionPolicy Bypass` form above, or once per machine run
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`.

## What setup does (7 steps)

| # | Step | What happens |
|---|------|--------------|
| 1 | Checks | Verifies Windows 10/11 x64 and Python 3.10+ on PATH. If Python is missing, setup tells you exactly where to get it and stops cleanly - nothing is installed. |
| 2 | Data folders | Creates `C:\ProgramData\Visage\` with `models\` and `identities\`, then locks the ACLs to **SYSTEM + Administrators only** (no inheritance). Re-running repairs the ACLs. |
| 3 | Python packages | Runs `pip install -r ..\engine\requirements.txt` (progress shown). On failure it prints the exact remedy and stops. |
| 4 | Face models | Runs `python ..\engine\fetch_models.py`, which downloads the YuNet + ArcFace ONNX models with hash verification. Already-verified files are kept, so a re-run resumes instead of restarting. |
| 5 | Default config | Writes `C:\ProgramData\Visage\config.json` (match threshold 0.66, strictness heavy) - the machine-wide defaults the engine reads at startup. An existing config is **never** overwritten - if it is corrupt, it is backed up to `config.json.bak.<timestamp>` first. |
| 6 | Sign-in tile | Runs `..\cp\register.ps1` to register the Visage Credential Provider. If `register.ps1` is missing (CP not built yet), setup says so clearly, installs everything else, and asks you to re-run it later - it never fails silently. |
| 7 | Verify | Runs the engine's pipe-protocol self-test (`..\engine\selftest.py`) when present. Otherwise it prints a manual checklist (Win+L, look for the tile, reboot if missing). |

Final summary shows per-step results, what was installed and where, and the
exact 3 steps to enroll:

1. Open the Visage Manager app (Start menu -> Visage Manager).
2. Click "Enroll face" and follow the on-screen guide.
3. Done. Press Win+L to lock Windows, then look at the camera.

**Exit codes:** `0` = installed; `1` = failed (read the messages - each names
the remedy); `2` = installed with pending steps (a component was not built
yet - re-run `setup.ps1` after it lands; it repairs and continues).

**Idempotent:** re-running `setup.ps1` at any time repairs a partial or stale
install instead of breaking a working one.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `File ...\setup.ps1 cannot be loaded` / execution-policy error | PowerShell blocks unsigned scripts | Launch with `powershell -ExecutionPolicy Bypass -File .\setup.ps1`, or run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once |
| `Python 3.10 or newer was not found` | Python missing, or installed without PATH | Install from https://www.python.org/downloads/ with **"Add python.exe to PATH"** ticked, close and reopen PowerShell, re-run setup |
| `pip install` fails with red errors | No internet, or pip too old | Check the connection, run `python -m pip install --upgrade pip`, then re-run `.\setup.ps1` - the failing package is named in the red lines |
| Model download fails / is very slow | No internet, low disk space, or a hash mismatch | Free 1+ GB on C:, check the connection, re-run setup (verified files are kept); on a hash mismatch delete `C:\ProgramData\Visage\models` and re-run |
| No Visage tile on the lock screen | Not registered, or Windows has not loaded it yet | Re-run setup as Administrator; then **reboot once** - Windows only loads credential providers at boot; then press Win+L |

Also: if the camera light never comes on during a test, close any other app
using the camera (Zoom, Teams, browser tabs) and try again. Enroll your face
in Visage Manager *before* testing unlock - the tile does nothing until an
identity exists.

## Uninstall

```powershell
cd <where-you-unzipped-it>\visage\installer
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

It unregisters the sign-in tile (via `..\cp\unregister.ps1`), stops running
Visage processes, asks for confirmation (`YES`), then deletes
`C:\ProgramData\Visage` and verifies it is gone. Removing the Python packages
is optional (asked at the end). The Visage program folder itself is left in
place - delete it yourself if you want it gone too.

## Files

- `setup.ps1` - the installer (this document describes it)
- `uninstall.ps1` - clean removal
- `INSTALL.md` - this file

Sibling components the installer calls (owned by other workers):

- `..\engine\requirements.txt` - Python dependencies
- `..\engine\fetch_models.py` - downloads YuNet + ArcFace ONNX, hash-verified
- `..\engine\selftest.py` - pipe-protocol self-test used in the verify step
- `..\cp\register.ps1` / `..\cp\unregister.ps1` - Credential Provider
  (un)registration

If any of these is missing, the installer says so in plain language and marks
the step PENDING (exit code 2) instead of failing or pretending it worked.
