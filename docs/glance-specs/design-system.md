All four files read, plus cross-checks on the panel background, pill button, permission row, and checkmark. Here is the complete spec.

---

# Glance Design System — Implementable Spec for PyQt6/Windows

## 1. Color tokens

### Onboarding (`GlanceTheme.swift`)
| Token | Hex | RGB |
|---|---|---|
| `accent` | `#3499FF` | 52, 153, 255 |
| `accentBright` | `#7FC2FF` | 127, 194, 255 |
| `accentPale` | `#CFE7FF` | 207, 231, 255 |
| `accentDeep` | `#1F6BD1` | 31, 107, 209 |
| `surface` | `#1E1E1E` | 30, 30, 30 |
| `surfaceRaised` | `#323232` | 50, 50, 50 |
| `panel` | `#000000` | 0, 0, 0 |
| `textPrimary` | `#FFFFFF` | 255, 255, 255 |
| `textSecondary` | `#949494` | 148, 148, 148 |
| `textDetail` | `#BDBDBD` | 189, 189, 189 |
| `placeholder` | `#2F2F2F` | 47, 47, 47 |
| `statusGranted` | `#30D158` | 48, 209, 88 |
| `statusDenied` | `#FF453A` | 255, 69, 58 |

- `whiteToAccent(t)`: linear lerp from white (t=0) to `accent` (t=1), clamped. Used for animated progress fills.
- The sweep uses `accent` → `accentBright` → `accentPale` layered gradients (never flat single-color shapes).

### Settings — dark mode values (`SettingsMetrics.swift`; Glance is dark-first, always use the `dark:` branch on Windows)
| Token | Value |
|---|---|
| `textPrimary` | `#EEEEEE` |
| `textSecondary` | `#BFBFBF` |
| `textTertiary` | `#999999` |
| `rowColor` | white @ 8% alpha (`rgba(255,255,255,0.08)`) |
| `rowBorder` | white @ 12% alpha over 80%-white base (`rgba(204,204,204,0.12)`), 1px |
| `neutralButtonFill` | white @ 14% (`rgba(255,255,255,0.14)`) |
| `destructiveFill` | `#E03B2F` |
| `tabBarTint` | white @ 15% |
| `tabBarBorder` | white @ 16% |
| `selectedPillColor` | white @ 10% |
| `selectedPillBorder` | white @ 12% |
| `optionPreviewFill` / `pickerPillFill` | white @ 5% |
| `optionPreviewSelectedTint` | accent wash @ 12% opacity over fill |
| `buttonBackgroundColor` | `#3F3F3F` |
| `qualityPoor` | `#FF5454` |
| `qualityFair` | `#FFBE54` |
| `qualityGood` | `#85FF77` |
| `qualityUnrated` | white @ 22% (deliberately neutral, not red) |
| `optionPreviewOuterStroke` | dark-only: white @ 35% over 10%-white base, 1px; light mode: transparent |

## 2. Font tokens

Glance uses the macOS system font (SF Pro). **Windows equivalent: Segoe UI Variable / Segoe UI.** Sizes are in points; map 1:1 to px at 96 DPI in Qt.

### Onboarding (`GlanceTheme.Font`)
| Token | Size | Weight | Used for |
|---|---|---|---|
| `title` | 26 | Bold | Step titles |
| `instruction` | 15 | Medium | Enrollment pose instructions |
| `button` | 13 | Medium | Pill button labels |
| `rowTitle` | 13 | Medium | Permission row titles |
| `grantLabel` | 12 | Semibold | Grant/Granted pill |
| `passwordCaption` | 12 | Medium | Password field caption |
| `passwordPlaceholder` | 12 | Regular | Password placeholder |
| `rowDetail` | 11 | Regular | Permission row detail line |

### Settings (`SettingsMetrics`)
| Token | Size | Weight |
|---|---|---|
| `headerButtonFont` | 13 | Medium |
| `tabTitleFont` | 13 | Medium |
| `rowFont` | 13 | Regular |
| `optionLabelFont` | 12 | Medium |
| `sectionTitleFont` | 13 | Medium |

Note: there is intentionally **no large display font in Settings** — everything is 12–13pt. Hierarchy comes from color (primary/secondary/tertiary), not size.

## 3. Corner radii, blur radii, opacities, materials

### Corner radii
- Onboarding panel bottom corners: **55px** (enroll step: **51.5px**). Top corners: inverted/notch silhouette on Mac — on Windows use a plain rounded rect with the same 55px bottom radius and ~24px top radius.
- Pill buttons, permission rows, camera-picker rows: **capsule** (fully rounded; button height 38 → effective radius 19). (`pillButtonRadius: 17` exists as a token but the actual views use `Capsule()`.)
- Settings rows: **16px**.
- Option preview tiles: **13px**.
- Blue selection ring on selected tile: outset **2.5px** outside the tile edge, stroke **3.5px**.

### Blur radii
- Panel shadow: radius **9px**, black @ 30% (55% on hover).
- Option preview shadow: radius **4px**, black @ 15%, zero offset (lifts evenly).
- Collapsed-panel blur: **40px** (content blurs out when panel collapses).
- Tab label reveal: max **4px** blur at fully-collapsed end.
- Enrollment sweep streak layers: base blurs **78 / 34 / 14px** (outer/mid/core) × per-streak `blurScale` (0.82–1.35 × 2.6 for heavy-blur streaks, × 1.1 otherwise); highlight streak blur **6px**.

### Opacities
- Sweep master cap: **0.6** (streaks can never overpower the camera preview).
- Streak peak opacities: 0.16–0.62 depending on layer (outer 0.16–0.28, mid 0.30–0.48, core 0.38–0.62, highlight 0.20–0.36).
- Disabled pill button: **0.4**. Disabled grant pill: **0.6**.

### Materials
- **Onboarding panel: solid black (`#000000`)** — NOT a blur material. This is deliberate; the panel is opaque.
- **Settings window: macOS `.sidebar` material** (vibrant blur) spanning the whole window, with a **fully transparent tint** (alpha 0) — i.e., the material alone is the look. Windows equivalent: `DwmExtendFrameIntoClientArea` / WinUI `Mica` or `Acrylic` with very low tint. Tab bar reuses the same material **blended within-window** (blurs rows scrolling beneath it) + white 15% tint.
- Settings header: blur strip **80px tall** (52px header + 28px fade room) so the blur fades to nothing before the first row.

## 4. Layout numbers & animation timings

### Onboarding panel sizes (`OnboardingMetrics`)
- Width: **380px** every step; enroll step: **320px**.
- Heights per step (notch/pill styles are identical except enroll):

| Step | Height |
|---|---|
| intro | 175 |
| permissions | 245 |
| securityNotice | 260 |
| preSetup | 220 |
| selectCamera | 228 |
| **enroll** | **344** (pill: 350) |
| name | 230 |
| password | 260 |
| complete | 95 |

### Onboarding content insets
- Top: **50px** (pill style: 32px — generous top clearance is for the physical notch housing)
- Leading/trailing: **28px** (pill: 30px)
- Bottom: **40px** (pill: 36px)
- `controlHorizontalPadding`: **24px**

### Onboarding controls
- Pill button: **160px** wide × **38px** tall, capsule.
- Permission row: **44px** tall, horizontal padding 12px inside; row layout: status dot (16px) + 10px gap + title/detail stack (1px internal spacing) + 4px min spacer + grant pill (**60×26px**, capsule, `surfaceRaised` fill).
- Status dot: **16px** circle.

### Enrollment camera cluster
- Camera preview circle: **185px** diameter (deliberately smaller than the ring so ticks stay visible).
- Tick ring outer diameter: **200px**; **80 ticks** (10 per 45° sector).
- Tick geometry: unlit length **12px**, lit length **20px**, width **2.4px**.
- Live turn indicator: **6 ticks** wide, length boost **+5px** (short of lit length so it never reads as captured).
- Completion ring: stroke width **14px**, outer edge inset **8px** inside the lit ticks' tips.
- Per-tick capture stagger: **0.008s** (sector fills as a sweep, not a snap).
- Total cluster footprint: **240px** diameter (200 + 2×20).
- Enroll chrome: instruction bottom padding **30px**, horizontal **20px**; camera top padding **40px** (notch) / **30px** (pill); close button **28px**, inset **18px** from top/trailing; "too far" chevron **32px**.

### Animation timings
- Step transition (panel resize + content scroll/blur, always together): **spring, response 0.42s, damping 0.80**.
- Text-field autofocus delay after step change: **0.48s**.
- Camera-complete sequence: instructions fade **0.35s** → preview fades **0.40s** → checkmark draws (**0.20s** delay, **0.28s** draw, 6px round-cap stroke) → hold **3.0s** total before naming step.
- Complete screen auto-dismiss: **3.0s**.
- Instruction text crossfade: in **0.30s** / out **0.35s**.
- Sweep: loop gap **1.0s** between cycles; master fade in **0.5s** / out **0.35s**; direction crossfade **0.25s**; **0.36s** stillness after pose change before sweep plays; intro one-shot auto-dismiss **1.6s**.
- Sweep streak motion: cubic-bezier **(0.68, 0.0, 0.32, 1.0)**, per-streak durations **0.94–1.28s**, delays **0–0.11s**.
- Settings tab selection: **spring, response 0.35s, damping 0.82**; label slides **10px** toward icon while collapsing.
- Settings locked/unlocked crossfade: **easeInOut 0.28s**.

### Settings window (`SettingsMetrics`)
- Window: **500×620px**. Header: **52px** tall, header button **30px** tall.
- Floating tab bar: **54px** tall, **14px** above bottom edge, **6px** horizontal padding; tab items **42px** tall, **11px** horizontal padding (selected: **14px**); glyph **15px**.
- Page bottom inset: **84px** (= 54 + 14 + 16) so content clears the tab bar.
- Rows: **44px** tall, **16px** radius, **12px** vertical spacing, **14px** horizontal inset; subtitle max width **260px**; slider rows use **12px** vertical padding instead of fixed height.
- Section titles: **13px** medium, **10px** horizontal inset, **8px** vertical padding.
- Page content side padding: **16px**.
- Option cards: vertical padding **14px**; preview **58px** tall (trigger tiles **70×44**, unlock-animation tiles **112×76**); item spacing **15px**.
- Quality tick strip: ticks **3.5px** wide × **26px** tall, **5.5px** spacing, strip max **250px** (compresses beyond that).
- Empty state: icon **34px**, spacing **12px**, min height **340px**.

## 5. Visual language (the look to replicate)

- **Near-black everything.** Panels are pure black; surfaces step up in tiny increments (`#1E1E1E` → `#323232` → `#3F3F3F`). Depth comes from these subtle solid steps plus hairline white-alpha borders — never from gradients on chrome.
- **One blue accent, used sparingly.** `#3499FF` fills only the primary action; everywhere else it appears as light: the enrollment sweep, the selection ring, the progress wash. Supporting blues (`#7FC2FF`, `#CFE7FF`) exist only to make layered light read as one body.
- **Capsules, not rectangles.** Buttons, rows, pills, grant controls are all fully rounded. The only hard rectangles are the window itself and the tick marks.
- **Typography is quiet.** 11–13pt for almost everything; a single 26pt bold title per onboarding step and 15pt medium for live instructions. Emphasis via white → `#949494` → `#BDBDBD` text tiers, not size jumps.
- **Motion is springy and slow enough to read.** 0.42s spring on step changes, staggered 8ms tick fills, ~1s light sweeps with a full 1s pause between cycles. Nothing snaps; nothing spins continuously.
- **The camera is the hero of enrollment.** Preview (185px) sits inside the tick ring (200px); sweep light is hard-capped at 60% opacity so video always dominates; instructions are a single 15pt medium line under the cluster.
- **Feedback is a ring of 80 ticks**, not a progress bar: sectors light per captured pose (staggered sweep fill), the live head-turn shows as a 6-tick pointer, and completion melts the ticks into a 14px solid ring before the checkmark draws.
- **Settings reads as floating frosted cards over a blurred window** (sidebar material + 8%-white rows + 12%-white hairlines), with a bottom floating tab bar — calm, dense, no accent color except selection states.
- **Status is color-coded dots + verbs, never banners**: green `#30D158` / red `#FF453A` 16px dots, "Grant"→"Granted" pill state change, per-sample quality ticks in red/amber/green.