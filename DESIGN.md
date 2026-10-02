# Mien Design Law

> **What if Apple itself made Face ID for Windows?**

Every decision in Mien — copy, interface, behavior, even the security
documentation — is held against that one question. It is not a slogan on a
slide. It is the test a string, a screen, or a fallback path has to pass
before it ships.

## What the law demands

**Calm.** The lock screen is a moment of mild stress: you want to get into
your machine. Nothing in Mien shouts, warns theatrically, or dumps technical
detail on you. The tile says "Look at the camera to sign in." When something
fails it says what happened in plain words and points at the way out —
"Face unlock unavailable — use your password or PIN." No error codes, no
tracebacks, no jargon. Ever.

**Confident.** Mien does not hedge, apologize, or narrate its own
uncertainty. It does not say "Attempting facial recognition, please wait…"
It says "Looking for your face…" and gets on with it. Confidence here is a
UX property, not a claim about accuracy — the honesty about accuracy lives
in the security docs, stated just as plainly.

**Plain English.** Every user-facing string must pass the read-aloud test:
if you would not say it to a non-technical friend, rewrite it. "Cosine
similarity 0.73" never reaches a user. Neither does "HRESULT 0x80070005".
Diagnostics go to logs; users get sentences.

**Invisible when it should be.** The best face unlock is one you never
think about. The tile is quiet, the scan is quick, success is a glance and
an Enter key. There is no dashboard to admire, no ceremony. Mien is a
feature of the sign-in screen, not an app that demands attention.

**Fail-safe, never fail-annoying.** This is where the law bites hardest.
If the engine is down, the vault is corrupt, the camera is busy, or the
face does not match — the tile degrades to one calm sentence and your
password/PIN tiles are right there, untouched. Mien can break in a dozen
ways and none of them may ever cost you a sign-in. A feature that can lock
you out of your own PC is not a feature; it is a liability. The entire
credential-provider implementation is written against the question "if
this crashes, what does the user see?" and the answer is always the same:
they sign in another way.

**Honest about what it is not.** Apple would not ship a 2D-webcam face
unlock and call it as secure as Face ID — and neither do we. The README
opens with "Mien is not as secure as Windows Hello" before any download
link, and the threat model names exactly what defeats it. Understating
security is part of the design law: calm confidence about the product,
blunt honesty about its limits. Those are not in tension; they are the
same virtue.

## How the law is enforced

- **Copy review.** Every user-facing string — tile states, wizard steps,
  error messages, installer output, challenge prompts — is reviewed against
  the read-aloud test. `manager/mien_manager/errors.py` is the choke point:
  no traceback ever reaches the UI; every failure becomes a plain sentence
  plus a next step.
- **Two vocabularies.** Users get sentences. The service log gets hex codes,
  HRESULTs, and stack traces. The two never mix: debug tracing in the
  credential provider compiles out of Release builds entirely.
- **Dark by default.** The standing visual rule: black and dark-grey
  surfaces, quiet accent color, no generic template look. The lock-screen
  tile is a dark tile with a face glyph and one line of text. Nothing more.
- **No half-states.** Setup is one script that verifies itself and says
  exactly what is pending. Uninstall removes everything it created.
  Registration touches exactly two registry keys and no others. There is
  no state where Mien is "sort of installed."
- **The security docs are UX.** The threat model in the README is written
  for the same non-technical reader as the installer. "A printed photo" /
  "a video of you" / "a stolen laptop" — concrete attackers, honest
  answers, no cryptography lectures.

## Non-goals

- Feature sprawl. Mien unlocks your PC with your face. It is not a
  surveillance tool, not a time tracker, not a smart-home hub.
- Cleverness in copy. No puns on the lock screen. No personality in error
  messages. The product's character is competence, and competence is quiet.
- Parity with Windows Hello's security claims. We say what we defeat and
  what we do not, in the same paragraph. Anyone who wants Hello-grade
  assurance should buy Hello-grade hardware.

## The one-sentence version

If Jony Ive's team had to ship face unlock for Windows using an ordinary
webcam, they would make it quiet, certain, plain-spoken — and they would
tell you exactly where it stops. That is Mien.
