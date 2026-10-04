All files read. Compiling the complete spec now.

---

# Glance Unlock-Flow & Settings UI — Behavior Spec for Windows (PyQt6 + Credential Provider)

## 0. Big picture

Glance's unlock UX is a small black panel anchored to the top-center of the screen. It has two visual styles:

- **Original**: the panel expands into a rounded rectangle showing a looping scan video.
- **Minimal** (default): the panel widens only slightly into a capsule revealing a lock glyph on the left and a small square scan video on the right.

Both ride on one phase state machine (`closed → scanning → success | failure → collapsing → closed`), driven by `FaceUnlockCoordinator` (recognition/liveness/timeouts) and `NotchOverlayController` (visibility/animation). On macOS the panel is drawn over the lock screen via SkyLight delegation; on Windows this maps to the Credential Provider tile + an optional top-center toast window.

---

## 1. MinimalUnlockView — exact layout

Layout is an `HStack`: **[ lock glyph ][ gap ][ video ]**, inside a black capsule.

| Token | Pill style (use this on Windows) | Notch style (macOS-only) |
|---|---|---|
| Panel (open) size | 150 × 40 pt (`minimalPillOpenWidth/Height`) | closed notch width + 84 (`minimalNotchFlankWidth` × 2), height = notch height + 12 (`minimalNotchHeightBump`) |
| Corner radius | height/2 at all times (true capsule while stretching) | top 12 / bottom 22 (`minimalNotchTopRadius/BottomRadius`) |
| Lock glyph | SF `lock.fill` → `lock.open.fill`, 14 pt semibold, white | 16 pt semibold |
| Glyph cell width | 34 pt (`minimalMediaWidth`) | 40 pt (`minimalNotchMediaWidth`) |
| Video | square, aspect-fit, 34 pt wide, 8 pt vertical inset | 40 pt wide, 11 pt vertical inset |
| Content edge inset | 4 pt horizontal padding | 4 pt + topRadius (flare eats space) |
| Video right padding | 4 pt trailing | 4 pt trailing |

**Lock-glyph flip animation:** on success the glyph swaps `lock.fill` → `lock.open.fill` with a magic-replace symbol effect (morph, fallback crossfade), duration **0.4 s** (`minimalLockAnimationDuration`), unlock delay **0 s** (`minimalLockUnlockDelay`). The glyph change is state-driven (`isMinimalLockOpen`), held "open" across the collapse (never re-locks visibly — re-locking would read as undoing the unlock).

**Scanning "breathing" pulse** (applies to the video only, never the glyph or panel):
- Ping-pongs between full and dimmed: scale **1.0 → 0.97**, opacity **1.0 → 0.65**
- Half-cycle **0.4 s** ease-in-out, **0.05 s** hold at each end
- First pulse starts **0.6 s** after expansion begins (lets the open spring settle)
- On resolve, eases back to full over **0.2 s** (`scanPulseSettleDuration`) so the success/failure video plays against steady content

**Interactivity:** hovering the panel gives a **+6 pt** size bump (`hoverBump`, ease-out 0.18 s), a generic haptic tap, and — if `retryOnHover` is on — starts/retries the scan. Click capture is restricted to the visible panel shape plus 6 pt slack; everything else is click-through. Shadow: black, radius 9, opacity 0.3 (0.55 while hovering), only while expanded.

## 2. Original (full) unlock expansion

- Open size: **220 × 200** content box; content padding **top 26 / leading 40 / trailing 40 / bottom 30** (pill style uses 32 on all four sides, open size **180 × 180**, corner radius **48**)
- Open spring: response **0.45**, damping **0.7** (slight overshoot); close spring: response **0.45**, damping **1.0** (critically damped)
- Same breathing pulse as minimal, applied to the whole video

## 3. Scan media states (`ScanMedia`)

| State | Asset | Behavior |
|---|---|---|
| `idle` | `unlockstatic.png` (still = first frame of success video) | Shown during `.scanning`; seamless cut into a playing video |
| `success` | `unlockanimation.mp4` (~1.22 s) | Muted, plays **once**, holds final frame (`actionAtItemEnd = .none`), never loops |
| `failure` | `unsuccessfulunlockanimation.mp4` | Muted, plays **once**, holds final frame |

Player swaps still→video only when `isReadyForDisplay` fires (0.6 s fallback timer as safety net) to avoid a black-frame flash. When animation style is **None**, no video ever plays — phases and retry logic are unchanged, resolve holds shrink to **400 ms**.

## 4. Overlay phase state machine (`NotchOverlayController`)

Phases: `.closed`, `.scanning`, `.success`, `.failure`, `.collapsing`, `.onboarding`.

- **`arm(onActivate)`** (lock-screen flow): shows window, phase `.closed`, content idle still. Pill style: pill starts off-screen (parked at `-(height + 20)`), renders one frame, then slides down to a **3 pt** top gap (ease-out **0.25 s**).
- **`beginScanning()`**: content idle still, phase `.scanning`. Panel expands (slide leads, expansion trails by **0.16 s** on enter). A scan-timeout task fires at `faceDetectionSeconds` (default **5 s**, range 3–10) → silent `collapse()` (no failure animation).
- **`finish(success:)`**: swaps in success/failure video (or idle still if style None), phase set. **Success hold 1.7 s** (video 1.22 s + beat to read final frame); **failure hold 5 s**; style-None hold 400 ms. Then `collapse()`.
- **`collapse()`**: phase `.collapsing`, 700 ms for the close spring to settle, then: if still armed → back to `.closed` (resting silhouette, hover-reactive); if not armed → window hidden.
- **`activate()`** (hover): from `.closed` or `.failure` only, and only if `retryOnHover` is on → re-enters `.scanning` and runs `onActivate` (restarts the scan cycle). From `.failure` with no retry handler → just collapses. No-op during scanning/success/collapsing/onboarding.
- **`disarm()`**: cancels everything, phase `.closed`, window hidden (pill slides up after shrink: shrink leads, slide trails by **0.18 s**). Never interrupts an in-flight success/collapse.
- Content dissolves on collapse: blur radius 0→40, opacity 1→0, scale 1→0.3, riding the same close animation.
- Success also fires a `.levelChange` haptic (gated by the haptic setting).

## 5. FaceUnlockCoordinator — the unlock state machine

**Triggers** (multi-select, at least one always kept): **On wake** (`moon.fill`), **On lock** (`lock.laptopcomputer`), **On space** (`space` icon; macOS-only — needs Input Monitoring, off by default). Defaults: On wake + On lock.

**Arming sequence** (per lock session, one-shot):
1. Lock/wake event → 300 ms settle delay → `evaluateTrigger()`
2. Guards: screen actually locked, not sleeping, Face Unlock enabled, session unlocked, password stored, preferred display connected
3. If the trigger isn't selected → still arms (shows closed silhouette) but doesn't auto-scan; hover remains available
4. `arm()` shows the closed silhouette; if auto-scan selected → `startScanCycle()` after a 250 ms buffer
5. Wake bursts: multiple wake signals within **2 s** (`rearmDebounce`) collapse into one arm

**Scan cycle** (`runScanCycle`, generation-guarded so overlapping cycles can't kill each other's camera):
1. Camera starts; overlay → `.scanning`; status "Looking for your face…"
2. `observeScanWindow` until deadline (`faceDetectionSeconds`):
   - Recognition + liveness run concurrently, each latches; unlock fires the moment both land
   - Liveness `denied` (glare/device-rectangle cue) → **immediate `.spoofSuspected`**, overriding even a latched match
   - No match: starts a wrong-face timer; only after **2 s of sustained non-match** (`wrongFaceGraceDuration` — a wake often catches the owner looking away) → `.consistentlyWrongFace`. Any detection gap or a match clears the timer
   - Liveness `pending` never fails the scan — it just keeps scanning until the deadline
   - Only **enabled** identities are matched against (disabled ones stay enrolled but can't unlock)
3. Outcomes:
   - **`.matched`**: stored password injected (typed), overlay `finish(success: true)`, status "Recognized — unlocking…"
   - **`.consistentlyWrongFace`**: overlay `finish(success: false)`, status "Face not recognized — hover the notch to try again."
   - **`.spoofSuspected`**: same failure path, status "Couldn't confirm a live face — hover the notch to try again."
   - **`.noResolution`** (timeout): **silent collapse, no failure animation**, status "No face detected — hover the notch to try again."
4. **Auto-retry-once** (setting, default OFF): one extra `startScanCycle()` per lock session, after the failure hold (5 s) / collapse duration elapses, and only if still locked and the overlay is back at `.closed`. Manual hover-retry is unlimited.

**Failure fallback:** Glance never types anything on failure and never locks the user out — the macOS password field stays available throughout. The user just types their password (or hovers to retry face).

**Gating copy** (shown in the app's status line when scan can't start):
- "Face unlock is on, but the session is locked — authenticate once from Password settings first."
- "Face unlock is on, but no password is stored yet."

## 6. NotchGeometry → Windows positioning (abstract)

- The window is created **once at max size** and never resized; only content moves. It's centered horizontally at the **top edge** of the preferred screen.
- Preferred screen = user-pinned display if still connected, else the first display with a notch, else the primary display.
- **Windows mapping: use the pill model everywhere** (no notch exists): a detached black capsule floating at top-center of the primary screen, **3 px below the top edge**, sliding in from off-screen (ease-out 0.25 s) then expanding (0.16 s stagger); on exit, shrink first, slide away after 0.18 s. Resting/closed state stays parked on screen while armed (hover-reactive); hidden entirely when disarmed.
- Hover bump +6 px; click-through everywhere except the panel (+6 px slack).

## 7. Settings window chrome

- **Size: 500 × 620.** Background: native sidebar blur material, no custom tint. Native window frame/corners/shadow (don't draw your own).
- **Header (52 pt):** transparent, floats over scrolling content with a progressive blur (80 pt tall, fading to nothing). Left side: native traffic lights (Windows: your own min/close buttons). Right side: **session lock button** — capsule, 30 pt tall, 12 pt horizontal padding, icon 12 pt + label 13 pt medium: "Session locked" / "Authenticating…" / "Session unlocked"; lock icon morphs open/closed. Also an optional circular refresh button (30 pt) on camera-related pages.
- **Content:** vertical scroll, 16 pt horizontal padding, 12 pt row spacing, rows 44 pt tall with 16 pt corner radius, subtle fill + 1 pt border. Page bottom inset clears the tab bar (54 + 14 + 16).
- **Tab bar:** floating capsule pinned to bottom (54 pt tall, 14 pt above window bottom), same blur material + white 15% tint (dark) + 1 pt border. Tabs show **icon only** (15 pt glyph in a 19 pt box, 11 pt horizontal padding); the **selected tab** gets a sliding highlight pill behind it (spring 0.35/0.82) and its title unfurls beside the icon (13 pt medium, slides 10 pt + 4 pt blur + fade as it appears/disappears). Unselected tabs show icon only.
- **Tabs in order:** General (`gearshape.fill`), **Face** (custom face glyph), Password (`lock.fill`), Camera (`video.fill`), Recognition (`sparkle`), About (`info.circle.fill`). (+ Face Lab `flask`, debug-only — skip on Windows.)
- Window always opens on **General** (selection resets on close). Two-finger horizontal swipe (36 pt threshold, must exceed vertical × 1.5) switches tabs; not applicable on Windows — use Ctrl+Tab or nothing.
- Accent color: **#3499FF**, applied as the tint for all controls/sliders.

## 8. General page

Gated: locked session shows a centered empty state (34 pt `lock.fill` icon, "Session locked", primary button "Unlock session"/"Authenticating…", error caption). Unlocked shows:

**Card 1 (rows, dividers between):**
- "Launch at login" — toggle
- "Show in menu bar" — toggle (Windows: "Show in system tray")
- "Enable Face Unlock" — toggle (turning off cancels any in-flight scan + disarms immediately)
- **Triggers** — label row: title "Triggers", subtitle "Select multiple"; three selectable tiles (**70 × 44**, 13 pt corner radius, icon 16 pt + label 12 pt medium beneath): "On wake" (`moon.fill`), "On lock" (`lock.laptopcomputer`), "On space" (Windows: drop, or map to a hotkey). Selected tiles get an accent wash (12% opacity) + accent ring (3.5 pt stroke, 2.5 pt outset). Deselecting the last one is a no-op. Disabled (dimmed 0.4) when Face Unlock is off.
- "Display on" — menu picker pill; options "Main display" (default) + connected displays; a picked-but-disconnected display reads "{name} (disconnected)" / "Selected display (disconnected)" rather than silently falling back.

**"Behaviour" section:**
- "Retry on notch hover" — toggle, default **ON** (Windows copy: "Retry on hover")
- "Auto retry once after failure" — toggle, default **OFF**
- "Haptic feedback" — toggle, default **ON** (no Windows equivalent — drop it)
- "Face detection duration" — stepped slider row: title left, value label right ("**5s**"), slider with integer stops **3–10** (default 5)

**"Animation" section:**
- "Show animation" — toggle, default **ON** (off = headless scan, no panel at all)
- **Style** — label row: title "Style", subtitle "The animation that appears when unlocking your Mac" (Windows: "…when unlocking your PC"); two tiles (**112 × 76**): "Minimal" (black capsule preview: 11 pt lock glyph + 18 pt video still) and "Original" (rounded-rect preview with video still). Selecting a tile **live-previews** it: the real panel presents that style, holds the success frame 1.5 s, then resolves. "None" is not a tile — it's produced by the "Show animation" toggle.

## 9. Your Face page ("Face" tab)

Four mutually exclusive states, crossfading (0.28 s ease-in-out):

1. **Locked:** empty state — "Session locked" + "Unlock session" (auth reason: "Authenticate to view your enrolled face").
2. **Unreadable** (decrypt failed): amber `exclamationmark.triangle.fill`, "Enrolled faces couldn't be read", caption "The stored data couldn't be decrypted with this session key.", plus long caption: "Nothing has been deleted, and Glance will not overwrite it — enrolling is blocked until this resolves. Quit and reopen Glance to retry. If it keeps failing, the session key no longer matches this data: remove the stored password on the Password tab to clear both, then set up again." **No enroll/delete actions offered.**
3. **Not enrolled:** `faceid` icon, "Face enrollment", primary button "**Set up Face Unlock**" → starts the 9-pose guided enrollment flow (disabled while a flow is already running).
4. **Enrolled:**
   - **"Face encrypted" card**: row with "Face encrypted" + 12 pt `lock.fill`; body copy (12 pt, tertiary): "Enroll separate identities to use Glance with multiple people, accessories (ex. glasses), facial expressions, or new lighting environments. This improves recognition quality."
   - **"Identities"** section title + **+** button (22 pt hit area, "Enroll another face") → enrolls an additional identity.
   - **Per-identity card**: name pill (capsule, 14/7 pt padding, neutral fill + border) + enable toggle on line 1; line 2: caption "Capture quality • **NN%**" (share of samples not in the red band; "Capture quality • not recorded" for legacy enrollments; "No samples captured" if empty), then a **quality tick strip** — one capsule tick per sample in capture order: **3.5 pt wide, 5.5 pt spacing, 26 pt tall**, max width 250 pt (compresses beyond that); colors red **#FF5454** / amber **#FFBE36** / green **#85FF77** / unrated neutral; then **Recapture** pill button (12 pt medium, 14/6 pt padding) and circular **trash** button (28 pt). Disabled identities dim the whole card to **0.45** (still enrolled, just not matched). Stale-model warning in amber (11 pt): "Captured with a different recognition model — recapture before this face can unlock your Mac." (Windows: "…unlock your PC.")
   - If identities exist but **none enabled**: caption "No identities are enabled — face unlock won't recognize anyone until you switch one back on."
   - **Recapture** replaces that identity's samples in place (keeps name/id); **Delete** asks confirmation dialog: title "Delete this enrolled face?", message ""{name}" will stop being recognized until you enroll them again.", buttons **Delete** (destructive) / **Cancel**.
   - Enrollment entry points: "Set up Face Unlock" (first), "+" / "Enroll another face" (add), "Recapture" (per identity). Only one enrollment flow at a time — starting a second is blocked while one runs.

## 10. Theme tokens

- Accent **#3499FF**; accentBright **#7FC2FF**; accentPale **#CFE7FF**; accentDeep **#1F6BD1**
- Surface **#1E1E1E**, surfaceRaised **#323232**, panel **pure black**
- Text: primary white, secondary **#949494**, detail **#BDBDBD**
- Status green **#30D158**, status red **#FF453A**
- Destructive fill **#E03B2F**; neutral button fill white @ 14% (dark)
- Row: 44 pt, radius 16, fill white @ 8%, border white @ 12% (dark)
- Fonts: titles 26 bold (onboarding), instructions 15 medium, rows 13 regular, captions 12, section titles 13 medium in tertiary

## 11. Consolidated user-visible copy

- Scan: "Looking for your face…", "Recognized — unlocking…"
- Failure: "Face not recognized." → "Face not recognized — hover the notch to try again." (Windows: "…move your mouse over the panel to try again.")
- Spoof: "Couldn't confirm a live face." → "Couldn't confirm a live face — hover the notch to try again."
- Timeout: "No face detected." → "No face detected — hover the notch to try again."
- Gate: "Face unlock is on, but the session is locked — authenticate once from Password settings first." / "Face unlock is on, but no password is stored yet."
- Session: "Session locked" / "Authenticating…" / "Session unlocked" / "Unlock session"
- Tabs: "General", "Face", "Password", "Camera", "Recognition", "About"
- Triggers: "Triggers" / "Select multiple" / "On wake" / "On lock" / "On space"
- Animation: "Show animation" / "Style" / "The animation that appears when unlocking your Mac" / "Minimal" / "Original" / "None"
- Behaviour: "Behaviour" / "Retry on notch hover" / "Auto retry once after failure" / "Haptic feedback" / "Face detection duration"
- Face page: all strings in §9 above.

## 12. Windows mapping notes (for the implementer)

1. **No notch → always use the pill model**: detached top-center capsule, 3 px top gap, slide+expand choreography as specified.
2. **Lock screen**: Glance draws over the macOS lock screen via SkyLight. On Windows the sanctioned path is the **Credential Provider tile**; mirror the phase UX as: tile shows scanning state → success/failure. The top-center panel can appear on the secure desktop only via the CP's own UI surface — keep the CP tile as the lock-screen presence and use the floating panel for in-session previews/retries.
3. **"On space" trigger**: macOS-only (needs Input Monitoring). Drop on Windows, or offer a global hotkey as the manual trigger.
4. **Haptics**: no Windows equivalent — omit.
5. **Touch ID session unlock** → Windows Hello `Windows.Security.Credentials.UI` prompt, or fall back to a password dialog.
6. **Password injection** → CP submits the credential through `GetSerialization`/`ReportResult`, not keystroke injection.
7. **"On wake"/"On lock"** → WTS session lock/unlock notifications (`WTSRegisterSessionNotification`).
8. **Face detection duration** slider (3–10 s, default 5) maps directly to the scan deadline; the 2 s wrong-face grace and one-shot auto-retry carry over unchanged.
9. **"Show in menu bar"** → "Show in system tray".