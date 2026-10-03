# Visage Manager

The desktop companion app for **Visage** — face unlock for Windows 10/11.
Enroll faces, manage identities, tune recognition and liveness, store the
Windows password securely, and control the lock-screen tile — all in one
calm, dark, Apple-grade interface.

> Design law: *"What if Apple itself made Face ID for Windows?"* Every
> screen and string is held to that bar — calm, confident, plain English,
> zero jargon, zero raw errors.

## Running

**On Windows** (the real target):

```bat
pip install -r requirements.txt
python run.py
```

**On Linux / macOS** (development only): the app runs with a simulated
engine and an optional synthetic camera, so every screen and flow can be
exercised with no hardware:

```bash
pip install PyQt6 numpy          # opencv not needed for the simulated path
QT_QPA_PLATFORM=offscreen python run.py        # headless smoke test
VISAGE_DEMO_CAMERA=1 python run.py                # synthetic camera preview
```

No network calls are made at runtime, ever.

## What it does

- **First-run setup** — welcome, an honest security notice ("Visage is not
  as secure as Windows Hello..."), camera selection, guided enrollment,
  password storage. Mirrors Glance's onboarding arc.
- **Guided enrollment** — nine head directions (front, up, down, left,
  right + diagonals) with Glance's exact prompts, per-pose progress,
  per-frame quality feedback ("Bring your face closer", "Too dark",
  "Hold still"), a completion sequence (instructions fade, preview
  fades, animated checkmark), then naming.
- **Identities** — multiple people, or several versions of yourself
  (glasses, beard, lighting). Enable/disable without deleting; recapture;
  delete with confirmation.
- **Settings** — camera selection, match confidence, detection distance,
  liveness (Off / Light / Heavy with plain-English explanations),
  session auto-lock timeout.
- **Password** — one-time Windows username/password (+ optional domain)
  collection, stored via the engine's `vault.py` (DPAPI on Windows).
  The password is never displayed, never logged, never kept in memory
  longer than the save call.
- **Lock-screen tile** — register/unregister the Credential Provider
  tile via `../cp/register.ps1` / `unregister.ps1`, with status readout.
- **Face Lab** — hidden diagnostics console. On the About page, click
  the Visage logo **5 times in a row** (a pause resets the count). Shows
  embedder status, vault/store state, live camera preview, detection
  stats, manual sample capture, per-sample identity disclosure,
  Identify with threshold slider, threshold calibration with strip
  chart, the five liveness cues with real engine thresholds, and a
  timestamped log.

## Architecture

```
manager/
  run.py                  entry point
  requirements.txt
  README.md               this file
  ENGINE_INTERFACE.md     the engine contract (what the engine worker owns)
  visage_manager/
    app.py                main window + sidebar
    theme.py              the dark design language
    widgets.py            cards, toggles, segmented control, checkmark…
    engine_bridge.py      the ONLY module that imports ../engine
    camera.py             camera access, degrades gracefully
    cp.py                 register/unregister/status for the CP tile
    settings_store.py     JSON settings (immediate write-through)
    errors.py             every failure -> a human sentence + remedy
    views/                home, identities, settings, password,
                          credential_provider_view, about, face_lab,
                          enroll_wizard, first_run
```

`engine_bridge.py` adapts the real engine when it's importable and falls
back to a clearly-labelled simulated engine otherwise. See
`ENGINE_INTERFACE.md` for the exact engine API surface and the known gaps.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "Camera unavailable" in enrollment | Check the camera is plugged in, not used by another app, and allowed in Windows Settings → Privacy & security → Camera. |
| "No camera support is installed" | `pip install -r requirements.txt`, then restart. |
| Enrollment stalls on a direction | Turn a little further, or ease back — extreme angles are rejected. If a pose never completes, relight the room and try again. |
| Face Lab is missing from the sidebar | Click the logo on the About page 5 times quickly. |
| Lock-screen tile won't register | Registration writes to HKLM — run Visage Manager as administrator. |

## Security notes

- Face data: embeddings only, encrypted at rest by the engine's store.
  Frames are processed in memory and never written to disk.
- Password: DPAPI-encrypted via `engine/vault.py`; the Manager never
  displays or logs it.
- The Manager makes no network calls. The only engine module that
  touches the network is `fetch_models.py` (one-time model download).
