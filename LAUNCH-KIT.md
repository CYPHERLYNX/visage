# Visage — Launch Kit (LOCAL ONLY — never commit to the repo)

> The repo is currently PRIVATE. Use this copy only if/when you make it public.
> Never post from bot accounts, never fake engagement. All claims below are
> honest and match the README. Repo: https://github.com/X0D4N/visage

---

## Show HN

**Title:** Show HN: Visage – Face unlock for Windows with any webcam, plus anti-spoofing that beats video replay

**Comment draft:**

I rebuilt Glance (the Mac face-unlock app by jonnyoo) for Windows, and went
further on the part Glance itself admits it can't do: anti-spoofing.

Visage is a Credential Provider tile for the Windows lock screen plus a Python
face service (YuNet detection + ArcFace 512-d embeddings, everything on-device).
Enroll once with a guided 9-pose flow, type your Windows password once (stored
in a DPAPI-encrypted vault), and a glance signs you in.

The liveness engine is the part I'm proud of: five explainable cues over a
rolling 2-second window. Two *deny* cues fail a scan outright (screen
glare/reflection analysis, device-rectangle detection for a held-up phone),
three *confirm* cues (blink, head-turn parallax via nose-vs-eyes displacement,
LBP micro-texture). Borderline scans escalate to a challenge-response round —
"turn your head left" — and the landmark *trajectory* has to match a real 3D
head turn within 3 seconds. A photo can't parallax; a replayed video can't
answer a random prompt.

Honest limits, stated up front in the README: an RGB webcam is not a depth
sensor, so this is not as secure as Windows Hello — it's a convenience feature.
Without challenge-response it doesn't reliably beat video. The provider is
additive-only (password/PIN tiles stay untouched) and every failure degrades to
"just use your password."

MIT licensed. The C++ provider is code-complete but I haven't been able to
compile/test it on Windows yet — the Python engine has 154 passing tests.
Would love feedback on the liveness approach.

---

## Product Hunt

**Tagline:** Face unlock for Windows, with anti-spoofing that defeats video replay

**Description:**

Visage brings a Face ID-style experience to Windows PCs with any ordinary
webcam. Enroll once with a guided setup, then unlock with a glance from the
lock screen — no typing, no reaching for the keyboard.

What sets it apart is the anti-spoofing: five explainable liveness cues
(screen-glare and device-rectangle deny cues; blink, head-parallax, and
micro-texture confirm cues), plus a challenge-response round whose head-turn
trajectory must match a real 3D face — defeating printed photos *and* replayed
video, the attack most webcam unlock tools can't stop.

Everything runs on-device: face embeddings never leave your machine as images,
and your Windows password sits in a DPAPI-encrypted vault. Open source (MIT),
with an honest "not as secure as Windows Hello" security section — it's a
convenience feature, and it says so.

---

## Reddit

### r/SideProject

**Title:** I rebuilt Glance (Mac face unlock) for Windows, with anti-spoofing that beats replayed video

**Body:**

Most Windows face-unlock tools are either rough or trivially fooled by a photo.
I wanted the Glance experience (that polished Mac face-unlock app) on Windows —
plus the one thing Glance admits it can't do: real anti-spoofing.

Visage: lock-screen Credential Provider tile + Python service (YuNet + ArcFace,
all on-device). The liveness engine runs 5 cues — screen glare and
phone-rectangle detection fail you outright; blink, head-turn parallax, and
skin-texture confirm you're real. Borderline scans get a random head-turn
challenge, and the landmark trajectory has to match a real 3D turn within 3s.
A video replay can't know the challenge in advance.

Honest caveat, right in the README: RGB webcam ≠ depth sensor, so it's not
Windows Hello — it's a convenience feature. Provider is additive-only, never
replaces password/PIN.

MIT, open source. The engine has 154 passing tests; the C++ side still needs a
Windows machine to compile (I built it on Linux). Roast my liveness approach —
what would you try to fool it with?

### r/opensource (or r/windows)

**Title:** Visage — open-source face unlock for Windows 10/11 with any webcam (MIT)

**Body:**

Open-source face unlock for Windows that works with any webcam — no IR camera
needed. Inspired by jonnyoo/glance on Mac.

- Lock-screen sign-in tile (C++ Credential Provider, additive — your
  password/PIN tiles stay)
- YuNet + ArcFace 512-d embeddings, 100% on-device, DPAPI-encrypted vault
- 5-cue liveness engine + challenge-response that defeats photos and replayed video
- Guided 9-pose enrollment, hidden diagnostics console, strictness levels
- One-click installer (setup.ps1), full uninstaller

Security honesty is a design principle here: the README opens with "not as
secure as Windows Hello" and names exactly what defeats it. Convenience
feature, not a security upgrade.

---

## X / LinkedIn (short posts)

**Post 1 (X):**
Built Visage: face unlock for Windows with any webcam — a Glance-style
experience plus anti-spoofing that defeats replayed video, not just photos.
5 explainable liveness cues + a challenge-response round whose head-turn
trajectory must match a real 3D face. MIT, on-device, honest about limits.

**Post 2 (X):**
The part of Visage I'm proudest of: deny-first liveness. Screen glare or a
phone-shaped rectangle in frame = instant fail. Blink, head parallax, and skin
micro-texture confirm you're alive. And a random "turn your head left" that a
recording can't answer. No black-box CNN — every cue is inspectable.

**Post 3 (LinkedIn):**
Most webcam face-unlock tools stop at "is there a face." Visage asks "is it
alive" — five explainable liveness cues plus trajectory-verified
challenge-response, so it holds up against printed photos and replayed video.
Built as a proper Windows Credential Provider (additive-only, password/PIN
untouched), with the honest caveat up front: an RGB webcam isn't a depth
sensor, so this is convenience, not a Hello replacement. Open source (MIT).
