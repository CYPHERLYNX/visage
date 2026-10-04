All six files read. Here is the complete spec.

---

# Glance Enrollment — Behavior & Animation Spec for PyQt6 Replication

All point values below are macOS points; on Windows treat 1pt = 1px at 100% scale. Colors given as hex.

## 0. Design tokens

- **accent** `#3499FF` (system blue); **accentBright** `#7FC2FF`; **accentPale** `#CFE7FF`; **accentDeep** `#1F6BD1`
- **panel** black; **textPrimary** white; **textSecondary** `#949494`; **textDetail** `#BDBDBD`
- `whiteToAccent(t)`: linear lerp white→accent per channel, t clamped 0..1
- **Instruction font**: system 15pt, weight medium (Qt: `QFont` pixel size 15, weight DemiBold), white, centered, max 2 lines, minimum scale factor 0.8 (shrink-to-fit)

---

## 1. EnrollmentRingView — the tick ring

Custom-drawn ring around the camera preview. **80 ticks** tile the circle (10 per 45° sector × 8 sectors).

### Geometry
- Ring outer diameter: **200** → radius 100
- Camera preview circle inside: **185** diameter (ticks stay visible around it)
- Tick: capsule, width **2.4**
  - Unlit length **12**, lit length **20**
  - Each tick's **inner tip is anchored at the ring radius** (offset `y = -(radius + length/2)` before rotation), so growth extends **outward**, not inward
  - Tick *i* sits at angle `i × 4.5°` (0° = top, clockwise)
- Cluster reserve diameter (layout box): `200 + 20×2 = 240`

### Sector bucketing
- Tick → sector: `sector = round(angle/45) mod 8`; sector angle = `sector × 45°`
- Compass mapping (0° = up, clockwise): top=0, topRight=45, right=90, bottomRight=135, bottom=180, bottomLeft=225, left=270, topLeft=315
- Boundary ticks round to the higher sector (Swift `rounded()` = half away from zero)

### Sector lighting (poses completing)
- A tick is **lit** when its sector's pose is in `capturedPoses`, or when enrollment is complete
- Lit tick: length animates 12→20, color snaps to accent
- **Staggered fill**: easeOut **0.3s** + per-tick delay `(index % 10) × 0.008s` — a captured sector fills as a sweep, not all at once
- Color when unlit: `whiteToAccent(intensity)` (see head-turn indicator below)

### Center pulse (no sector for center)
- When the **center** pose captures, `centerPulseTick` bumps → `pulseActive = true` for **220 ms**
- While pulsing, **all** ticks jump to lit length (20) — whole ring breathes once — animated easeOut **0.22s**

### Live head-turn indicator
- `headTurn = {angle (compass, 0=up clockwise), progress 0..1}`; computed from yaw/pitch normalized against the current pose's match thresholds; `nil` when: not on enroll step, complete, no face, too far, excessive turn, or pose is center
- Deadzone: progress below **0.15** → no indicator
- Per-tick intensity (only for **unlit** ticks): `falloff = max(0, 1 − (Δangle / 4.5°) / 3)`; `intensity = progress × falloff`
  - i.e. a ±3-tick pointer (±13.5°) tracking the **true head angle** (not snapped to sectors), animated easeOut **0.15s** (undelayed — must track a live head)
- Visual: color shifts white→accent by intensity; length grows by up to **+5** (`tickLengthUnlit + 5 × intensity`) — deliberately short of lit length 20 so it never reads as captured

### Completion merge
- On `enrollmentComplete`: every tick opacity → 0 (easeInOut **0.45s**, per-tick delay `index × 0.004s`), tick width → **9.42** (`2π × 100 / 80 × 1.2`)
- Simultaneously a solid ring fades/scales in: circle diameter `200 + 2×(20−8) − 14 = 210`, stroke width **14**, accent color, opacity 0→1, scale 0.92→1.0, easeInOut 0.45s
- Ticks sit just outside it (ring's outer edge is 8pt inside the lit ticks' outer tips so it reads slightly smaller once alone)

---

## 2. Sweep window (direction cue)

A **full-screen, transparent, click-through** overlay *behind* the panel (macOS: borderless non-activating NSPanel, clear background, `ignoresMouseEvents`, window level one below the panel; Qt equivalent: frameless fullscreen widget, `WA_TranslucentBackground`, `WA_TransparentForMouseEvents`, stays-on-top below the panel).

### Lifecycle
- **Presented** when enrollment begins (`beginEnrollment`); **dismissed** immediately when enrollment completes (before the checkmark sequence), and on cancel/back
- Dismiss = fade out **0.35s**, then tear down
- Per pose: after a **0.36s** stillness delay (`sweepPoseDelay`), the sweep for that direction plays **once** (~1.4s), then fades to invisible until the next pose
- Direction changes crossfade: easeInOut **0.25s**; overlay fade-in **0.5s** / fade-out **0.35s**
- **Suppressed** (hidden) when: too-far prompt active, or checkmark showing, or guide hidden
- **Center pose → no sweep** (direction is nil)
- Intro screen also plays a one-shot downward sweep, auto-dismissed after **1.6s**

### The sweep itself: 42 blurred light ribbons
- Not arrows — layered capsules with heavy Gaussian blur, blue tints (accent / accentBright / accentPale → white highlight), master opacity **0.6**
- Motion: each streak travels a quadratic Bézier from off-screen on the **opposite** side toward the target direction (e.g. "turn left" = ribbons sweep right→left), along the screen, ending slightly past center — the *motion* is the cue
- Per-streak: duration **0.94–1.28s** (7 lanes), start delay **0–0.096s** staggered, timing curve cubic (0.68, 0, 0.32, 1.0)
- Opacity envelope: smoothstep fade-in over first 7% of travel, hold peak, smoothstep fade-out over last 30%
- Geometry: streak length `max(shortEdge × lengthFactor × 0.55, 180)`; lateral fan ±0.86 of the perpendicular extent × 0.46; slight Bézier bow ±(0.08–0.26) × shortEdge × 0.20 with ±0.05 rad rotation drift
- Ribbon rendering (4 stacked layers per streak): thicknesses per lane [310, 190, 105, 155, 72, 230, 88]px (scaled 2.35× / 1× / 0.42× / 0.12×), blurs 78× / 34× / 14× / 6 × blurScale (blurScale 0.82–1.35, ×2.6 for 3 of 4 streaks), linear gradient transparent→tinted→white-hot→transparent; every ~3rd streak "vivid" (1.4× opacity)
- Direction travel vectors (screen space, x right / y down): left(−1,0), right(1,0), up(0,−1), down(0,1), diagonals ±(0.707,0.707)

---

## 3. Enroll step layout & copy

### Panel
- **320 × 344** (pill-style height 350 — use 320×350 on Windows, no physical notch), bottom corner radius **51.5**, black background
- Panel resize between steps: spring **response 0.42, damping 0.8**; step content transition: slide vertically by panel height (forward = new content rises from below; back = mirrored) + blur 12 + fade, same spring

### Layout (top → bottom)
1. **Camera cluster** (240×240 box), top padding **30**
   - Ring (200⌀) with camera preview circle (185⌀) centered inside it
   - Camera preview fades out (easeInOut **0.4s**) at the completion sequence
   - **Too-far chevron**: overlaid on the preview when `isTooFar` — double-chevron-up glyph, 32pt semibold, white, black shadow (45%, radius 6, y+1), **repeating bounce-up** animation; fades in/out 0.2s
   - **Checkmark** (70×59, see §4) replaces the preview area at completion, nudged 4px down
2. **Spacer** (min 8)
3. **Instruction label**: horizontal padding 20, bottom padding **30**; text change crossfades (0.2s); whole label fades in **0.3s** / out **0.35s** with `guideVisible`

### Close button
- Top-trailing, 28×28, inset 18 from both edges; "×" 11pt semibold, white at 80% → 100% on hover; hidden once complete/checkmark shows; acts as Back/cancel

### Copy (exact strings)
- Per-pose instruction: "Look straight at the camera" · "Turn your head slightly left" · "Turn your head to the top left" · "Turn your head slightly up" · "Turn your head to the top right" · "Turn your head slightly right" · "Turn your head to the bottom right" · "Turn your head slightly down" · "Turn your head to the bottom left"
- **Too far**: "Bring your face closer" (chevron shows simultaneously)
- **Excessive turn** (turned past the useful envelope): "Too far left" · "Too far toward the top left" · "Too far up" · "Too far toward the top right" · "Too far right" · "Too far toward the bottom right" · "Too far down" · "Too far toward the bottom left"
- **Complete**: "Face captured"

### Capture state machine (drives the above)
- 9 poses × **2 samples** = 18 total; order: center, left, topLeft, top, topRight, right, bottomRight, bottom, bottomLeft
- Per frame: require **3 consecutive matching frames** + **500 ms settled hold** before a capture fires (debounces mid-motion grabs); only 5-point face alignment accepted toward enrollment
- **Stall timeout 12 s** per pose → match bands widen ×1.25 (with extra per-pose leniency: bottom poses ×1.5/×1.2) so no camera angle can strand the user
- Quality floor deliberately permissive (better a mediocre sample than a stalled flow); capture blocked for the first **1.5 s** after camera start (no mid-blink first samples)

### Camera-complete sequence (timings)
1. `enrollmentComplete` → sweep dismissed instantly, instruction → "Face captured", ring ticks merge into solid ring
2. +0.35s → camera preview fades (0.4s)
3. +0.4s more → +0.2s → **checkmark draws** (0.28s)
4. Total **3.0s** after step 1 → camera stops, panel advances to naming

---

## 4. AnimatedCheckmark

- Path in unit rect: moveTo **(0.16w, 0.52h)** → lineTo **(0.40w, 0.78h)** → lineTo **(0.86w, 0.22h)**
- Drawn via stroke **trim 0→1** over **0.28s**, timing curve (0.65, 0, 0.35, 1.0), round caps and joins
- Enrollment usage: **70×59** box, line width **8**, accent color
- Scales to whatever frame it's given (pure vector — no assets)

---

## 5. ScanAnimationView (unlock, not enrollment)

- Plays **once** at the lock screen per attempt; holds its final frame (does not loop, does not rewind)
- States: `idle` = still PNG (first frame of the success video, shown so video start is seamless); `success` = `unlockanimation.mp4`; `failure` = `unsuccessfulunlockanimation.mp4`
- Always **muted**; video reveals only after first frame is decode-ready (else a 0.6s fallback timer) to avoid a black flash
- While scanning (before verdict), the panel "breathes": scale ping-pong 1.0↔**0.97**, opacity 1.0↔**0.65**, half-cycle **0.4s**, 0.05s hold at each end; settles back in **0.2s** once the animation starts; breathing starts **0.6s** after panel expansion
- Windows note: replicate with a short frame-sequence/video widget inside the tile; the still-first-frame trick matters for perceived latency

---

## 6. Panel hosting (OnboardingNotchView)

- Onboarding lives in a top-screen panel; on notch-less displays (i.e. **every Windows PC**) Glance uses **pill style**: a floating top-center capsule that expands into a rounded rect (expanded corner radius **48**), parked **3px** below the screen top edge, content padding 32/32/32/32
- Show: spring **response 0.45, damping 0.7** (slight overshoot); hide: same response, **critically damped (1.0)**; panel content blurs while off-screen, resolving as it slides in
- Pill collapsed: **80×24** capsule; expanded: sized to the current step (enroll: 320×350)
- Click-through sweep overlay sits one window level below the panel
- For Windows: implement as a frameless, top-center, always-on-top Qt dialog with a 48px-radius rounded-rect mask, drop shadow, and the spring approximated with `QPropertyAnimation` (out-back easing on open ≈ overshoot; linear-out on close)

---

### PyQt6 implementation notes (highest risk items)
1. The **ring** is a custom `QWidget.paintEvent` job: 80 capsules is trivial; drive per-tick length/color/opacity with a 60 Hz `QTimer` interpolating the four animation tracks (lit-stagger 0.3s+8ms, pulse 0.22s, turn-track 0.15s, complete-merge 0.45s+4ms).
2. The **sweep** is the hardest: 42 ribbons × 4 blurred layers at 60 fps. Prerender each streak as a blurred `QPixmap` once (per direction, per screen size), then animate pixmap position/rotation/opacity along the Bézier — never re-blur per frame.
3. **Click-through fullscreen overlay** in Qt: `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`, `setAttribute(Qt.WA_TranslucentBackground)` + `WA_TransparentForMouseEvents`.