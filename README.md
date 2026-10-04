<h1 align="center">
  <br>
  <img src="assets/icon.png" alt="Visage" width="150">
  <br>
  Visage
  <br>
</h1>

<h3 align="center">Face unlock for your Windows PC</h3>

<p align="center"><em>What if Apple itself made Face ID for Windows?</em></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-black.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Windows-10%2F11-black.svg" alt="Windows 10/11">
  <img src="https://img.shields.io/badge/Python-3.10%2B-black.svg" alt="Python 3.10+">
</p>

Visage brings the FaceID-like experience of your iPhone to a Windows PC near you. Unlock your PC with a glance — no typing, no reaching for the keyboard. Everything runs on-device, so your face data and your Windows password never touch the internet. The UI lives on the lock screen as a sign-in tile with scanning, success, and failure states, and an optional challenge-response liveness check makes Visage hold up against replayed video, not just photos.


<!-- Demo video placeholder: upload the demo to the releases page and link it here -->


---

> [!WARNING]
> ## Read before downloading
> ## Visage is not as secure as Windows Hello
>
> Ordinary webcams don't come equipped with the depth sensors that make Windows Hello trustworthy and secure.
> Windows Hello builds a 3D map of your face with infrared; a regular webcam sees a flat 2D image. That means:
>
> - Visage defeats, with reasonable confidence, a printed photo and a photo on a phone screen (heavy liveness
>   detection — checks that verify you're a live person, not a picture — must be turned on)
> - Visage defeats a replayed video of you ONLY when challenge-response liveness is turned on. Challenge-response
>   prompts you to turn your head in a random direction; a recording can't answer an unpredictable prompt in time
> - Without challenge-response, Visage does not reliably defeat a video of you
> - Windows has no API that lets a third-party app authorize a login, so Visage signs you in by submitting
>   your stored password through a Credential Provider tile — a Credential Provider is Windows's plug-in system
>   for third-party sign-in options on the lock screen
>
> Visage is a convenience feature, not a security upgrade. Only continue if you accept the tradeoff.

## Installation

**Requirements:**
- Windows 10 or 11, 64-bit
- A webcam (built-in or USB)
- Python 3.10 or later ([python.org](https://www.python.org/downloads/) — tick "Add python.exe to PATH")
- Visual Studio 2022 with "Desktop development with C++" ([free Community edition](https://visualstudio.microsoft.com/downloads/)) — the installer builds the sign-in component with it

<a href="https://github.com/CYPHERLYNX/visage/releases/tag/v0.1" target="_self"><img width="200" src="https://img.shields.io/badge/Download_for_Windows-black.svg" alt="Download for Windows" /></a>

Download **Visage-Setup-v0.1.exe**, double-click it, approve the Administrator prompt, and follow the wizard. It installs Visage, downloads the face models (hash-verified), builds + registers the sign-in tile, and adds Visage Manager to your Start menu — then open Visage Manager to enroll your face.

Prefer the manual route? The same steps live in `installer\setup.ps1`:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\setup.ps1
```


## Permissions

| Permission | Why |
|---|---|
| **Camera** | To see your face. Frames are processed in memory and never written to disk. |
| **Administrator** | To register the Visage sign-in tile (Credential Provider) with Windows at install time. Needed once during setup. |
| **DPAPI vault** | To keep your stored password encrypted at rest. DPAPI (Windows's Data Protection API) is Windows's built-in encryption service; it locks data so only this machine can decrypt it. |

## How it works

1. Launch the app and follow the onboarding to enroll your face. Visage guides you through capturing your face, turning your head in nine
   directions. Each frame becomes a 512-number *embedding* — a mathematical fingerprint — and the
   image is thrown away.
2. Enter your Windows password once. It's encrypted with DPAPI and only this machine can unlock it.
3. When your PC locks or wakes from sleep, the Visage tile appears on the lock screen and starts searching for a face.
4. If it's you — and the liveness checks agree you're a real person — Visage submits the
   password and you're in.

## Features

| Feature | Description |
|---|---|
| **Face unlock** | Triggers on lock, on wake from sleep, or on selecting the Visage tile at the lock screen. Pick any combination. |
| **Multiple identities** | Enroll several people, or several versions of yourself — with glasses, a beard, different lighting. Toggle any of them off without deleting. |
| **Liveness checks** | Watches for the motion and reflections that separate a real face from a photo. *Light* or *Heavy* strictness, or off. *Challenge-response* adds a head-turn prompt that a recording can't answer — the only mode that defeats video replay. |
| **Lock-screen tile** | A sign-in tile that shows scanning, success, and failure states. Select it to retry — or turn the tile off entirely and Visage stays invisible. |
| **Camera selection** | Choose which camera to use — the built-in webcam, a USB camera, or any capture device Windows sees. |
| **Auto-locking sessions** | The unlock authorization re-locks itself after an idle period you choose, so an unattended PC doesn't stay authorized forever. |
| **Face Lab** | A hidden debug console to test face recognition and liveness detection with real values. See below. |
| **Your data, your call** | Edit or delete your enrolment or stored password at any time. The encrypted files are removed immediately. |

---

# Privacy and Security

Visage is designed to keep biometric data and credentials on-device.

### Face data

Visage never stores camera images. During enrollment, each captured face is converted into a **512-dimensional embedding** using an ArcFace-based model. The original frame is then discarded.

Embeddings are stored locally and encrypted with **AES-GCM** — an encryption mode that scrambles the data and detects any tampering with it.

### Credentials

Your Windows password is stored as encrypted data and is never written to disk in plaintext. The encryption key is protected by **DPAPI in LocalMachine scope** — bound to this PC, not your roaming profile — and the vault file carries strict ACLs (Windows file permissions) limiting access to SYSTEM and Administrators.

The key and the password are only held in memory while an authorized unlock session is active, and are wiped from memory (memory hygiene) as soon as the session ends.

### Unlock pipeline

Visage won't submit your password simply because a face matches. An unlock requires all of the following:

1. A valid Visage session is authorized.
2. The PC is actually at the lock screen.
3. An enabled identity matches above the configured similarity threshold — how closely the camera's view must match your enrollment to count as you.
4. Liveness checks accept the detected face.
5. The Credential Provider tile is available to submit the password.

Face recognition and liveness detection run independently and must both succeed before the password is submitted.

### Local by design

Face recognition, face enrollment, and liveness detection run entirely on-device. Visage does not send face data, camera frames, or credentials to a server.


### How it tells a face from a photo — and a video

Five independent cues over a rolling ~2s window, in two roles:

- **Deny cues** are evidence of a spoof — screen glare, or a device-shaped rectangle framing the
face. Either one fails the scan outright and overrides anything else.
- **Confirm cues** are evidence of a real face — blink timing, nose parallax
across head turns, skin texture. Any one is enough, and their absence is never a failure, since a
live person can sit still and not blink.

Light detection only includes deny cues. Heavy detection includes both deny and confirm cues.

Challenge-response is the differentiator: it prompts you to turn your head in a random direction on the
spot. A photo can't move, and a prerecorded video can't turn the right way on demand — which is why
challenge-response is the only mode that defeats video replay.

### Threat model: what an attacker actually gets

Honest answers, no hedging:

- **A printed photo of you.** Defeated with reasonable confidence in Heavy mode. A flat print
  cannot blink, cannot show nose parallax when the head turns, and has none of the skin texture
  the confirm cues look for. In Light mode (deny cues only) a high-quality matte print in soft
  lighting is less certain to trip glare detection — use Heavy if this worries you.
- **A photo of you on a phone screen.** Defeated with reasonable confidence. Screens betray
  themselves: the glare cue catches reflections, and the device-rectangle cue catches the sharp
  edges of the phone framing your face. Either one fails the scan outright.
- **A replayed video of you.** This is the attack that matters, and the answer depends on one
  setting. **Without challenge-response:** not reliably defeated — a good video blinks, turns,
  and has real texture, so the passive cues can be fooled. **With challenge-response turned on:**
  defeated — the prompt is a random direction with a few seconds to comply, and a recording
  cannot turn the right way on demand.
- **A stolen laptop (powered off).** Visage adds no protection here beyond what Windows already
  gives you, and you should assume a capable attacker can eventually extract the DPAPI machine
  key from the disk offline (this is a documented property of LocalMachine-scope DPAPI, not a
  Visage bug). The vault file's ACLs stop other *users on the running machine*, not someone holding
  the drive. If laptop theft is in your threat model, enable BitLocker — that is the defense that
  actually covers this case.

What Visage never does: your face data and password never leave the machine, there is no account,
no cloud, no telemetry. An attacker who never touches your laptop gets nothing, because there is
nothing to get.

### Face Lab

Face Lab is a hidden debug console to test face recognition and liveness detection with real values.

**To open it:** Settings → About, then click the app icon 5 times. A
debug section should appear in the sidebar.

---


## Building from source

### Prerequisites

- Windows 10/11, 64-bit
- Python 3.10+
- Visual Studio Build Tools (for compiling the native components)



### Installation

1. Clone repository:
  ```bash
   git clone https://github.com/CYPHERLYNX/visage.git
   cd visage
  ```
2. Run the installer:
  ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
  ```
3. Launch Visage from the Start menu and follow the onboarding to enroll your face.



## Acknowledgements

- **[jonnyoo/glance](https://github.com/jonnyoo/glance)** — the original idea. Visage is a Windows rebuild
  of Glance, and wouldn't exist without it.
- **[InsightFace](https://github.com/deepinsight/insightface)** — the ArcFace model doing the
recognition.
- **[OpenCV](https://opencv.org)** — YuNet face detection.


## License

[MIT](LICENSE) © 2026 CYPHERLYNX
