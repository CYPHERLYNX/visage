"""Fullscreen click-through sweep overlay for guided face enrollment.

Replicates Glance's ``EnrollmentDirectionSweep``: 42 blurred light ribbons
sweep across the screen toward the enrollment direction. The motion itself is
the cue — no arrows, no text.

Lifecycle (mirrors Glance's sweep window):
  * presented when enrollment begins, dismissed on complete/cancel
  * ``play(direction)`` plays the sweep once (~1.4 s) then fades to invisible
  * a mid-play direction change crossfades (easeInOut 0.25 s)
  * ``showEvent`` fades in 0.5 s; ``stop()`` fades out 0.35 s then hides
  * the overlay sits below the enrollment panel and never intercepts clicks

Performance: each streak is prerendered ONCE per screen size as a QPixmap.
The heavy soft-glow layers are baked analytically (capsule + length gradient
+ vertical soft-falloff mask — no convolution), so the 60 Hz timer only ever
moves, rotates and fades pixmaps along quadratic Beziers. Nothing is
re-blurred per frame.

Dependency-free: only PyQt6 and the standard library.
"""

from __future__ import annotations

import math
import sys
import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QImage,
    QLinearGradient,
    QPainter,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QWidget

# ---------------------------------------------------------------------------
# Spec constants (Glance enrollment-ui.md section 2)
# ---------------------------------------------------------------------------

_STREAK_COUNT = 42
_MASTER_OPACITY = 0.6  # master cap applied at draw time

_ACCENT = QColor("#3499FF")
_ACCENT_BRIGHT = QColor("#7FC2FF")
_ACCENT_PALE = QColor("#CFE7FF")
_WHITE = QColor("#FFFFFF")

_DIAG = 1.0 / math.sqrt(2.0)

#: Travel vectors in screen space (x right, y down). Ribbons start off-screen
#: on the OPPOSITE side and travel toward the named direction.
_TRAVEL: dict[str, tuple[float, float]] = {
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "up": (0.0, -1.0),
    "down": (0.0, 1.0),
    "topLeft": (-_DIAG, -_DIAG),
    "topRight": (_DIAG, -_DIAG),
    "bottomLeft": (-_DIAG, _DIAG),
    "bottomRight": (_DIAG, _DIAG),
}

#: Per-lane tables, indexed by streak_index % 7 (Glance's makeSpecs()).
_LANE_THICKNESS = (310.0, 190.0, 105.0, 155.0, 72.0, 230.0, 88.0)
_LANE_LENGTH_FACTOR = (1.16, 0.94, 0.72, 0.86, 0.54, 1.04, 0.62)
_LANE_BLUR_BASE = (1.35, 1.16, 0.96, 1.22, 0.82, 1.32, 1.02)
_LANE_PEAK_BASE = (0.58, 0.72, 0.80, 0.52, 0.66, 0.42, 0.48)
_LANE_DURATION = (1.22, 1.08, 0.98, 1.16, 0.94, 1.28, 1.04)
_LANE_DELAY = (0.00, 0.03, 0.06, 0.015, 0.08, 0.04, 0.065)
_LANE_BOW = (0.18, -0.14, 0.10, -0.22, 0.26, 0.08, -0.12)

_TICK_MS = 16  # 60 Hz animation driver


# ---------------------------------------------------------------------------
# Easing helpers
# ---------------------------------------------------------------------------

def _bez(t: float, a: float, b: float) -> float:
    """1-D cubic Bezier with P0=0, P3=1."""
    u = 1.0 - t
    return 3.0 * u * u * t * a + 3.0 * u * t * t * b + t * t * t


def _bez_d(t: float, a: float, b: float) -> float:
    """Derivative of _bez."""
    return (
        3.0 * (1.0 - t) * (1.0 - 3.0 * t) * a
        + 3.0 * t * (2.0 - 3.0 * t) * b
        + 3.0 * t * t
    )


def _cubic_bezier_ease(x1: float, y1: float, x2: float, y2: float, x: float) -> float:
    """CSS cubic-bezier easing: y as a function of x in [0, 1]."""
    x = min(max(x, 0.0), 1.0)
    t = x
    for _ in range(8):  # Newton-Raphson on Bx(t) - x
        err = _bez(t, x1, x2) - x
        if abs(err) < 1e-6:
            break
        slope = _bez_d(t, x1, x2)
        if abs(slope) < 1e-6:
            break
        t = min(max(t - err / slope, 0.0), 1.0)
    else:
        lo, hi = 0.0, 1.0  # bisection fallback (practically unreachable)
        for _ in range(24):
            mid = 0.5 * (lo + hi)
            if _bez(mid, x1, x2) < x:
                lo = mid
            else:
                hi = mid
        t = 0.5 * (lo + hi)
    return _bez(t, y1, y2)


def _sweep_ease(t: float) -> float:
    """Per-streak travel timing curve: cubic (0.68, 0, 0.32, 1.0)."""
    return _cubic_bezier_ease(0.68, 0.0, 0.32, 1.0, t)


def _smoothstep(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


def _envelope(t: float) -> float:
    """Opacity envelope over eased travel: smoothstep in over the first 7%,
    hold peak, smoothstep out over the last 30%."""
    if t <= 0.0 or t >= 1.0:
        return 0.0
    a = 1.0
    if t < 0.07:
        a = _smoothstep(t / 0.07)
    b = 1.0
    if t > 0.7:
        b = 1.0 - _smoothstep((t - 0.7) / 0.3)
    return a * b


# ---------------------------------------------------------------------------
# Streak spec / geometry
# ---------------------------------------------------------------------------

class _StreakGeom:
    """Motion path for one streak (direction-dependent)."""

    __slots__ = ("delay", "dur", "sx", "sy", "qx", "qy", "ex", "ey", "drift")

    def __init__(self) -> None:
        self.delay = 0.0
        self.dur = 1.0
        self.sx = self.sy = 0.0  # Bezier start (off-screen, opposite side)
        self.qx = self.qy = 0.0  # Bezier control (bow)
        self.ex = self.ey = 0.0  # Bezier end (past center)
        self.drift = 0.05  # rotation drift, radians


def _build_geoms(direction: str, w: float, h: float) -> list[_StreakGeom]:
    """Quadratic-Bezier travel paths for all 42 streaks (Glance geometry)."""
    tx, ty = _TRAVEL[direction]
    px, py = -ty, tx  # perpendicular
    cx, cy = w * 0.5, h * 0.5
    short = min(w, h)
    diag = math.hypot(w, h)
    start_span = diag * 0.58
    end_span = diag * 0.50
    perp_extent = abs(px) * w + abs(py) * h

    geoms: list[_StreakGeom] = []
    for i in range(_STREAK_COUNT):
        lane = i % 7
        lateral = -0.86 + (i / (_STREAK_COUNT - 1)) * 1.72
        lat = lateral * perp_extent * 0.46
        g = _StreakGeom()
        g.delay = _LANE_DELAY[lane] + (i % 4) * 0.008
        g.dur = _LANE_DURATION[lane]
        g.sx = cx - tx * start_span + px * lat
        g.sy = cy - ty * start_span + py * lat
        g.ex = cx + tx * end_span + px * lat
        g.ey = cy + ty * end_span + py * lat
        bow = _LANE_BOW[lane] * short * 0.20
        g.qx = (g.sx + g.ex) * 0.5 + px * bow
        g.qy = (g.sy + g.ey) * 0.5 + py * bow
        g.drift = -0.05 if _LANE_BOW[lane] < 0 else 0.05
        geoms.append(g)
    return geoms


# ---------------------------------------------------------------------------
# Streak prerendering (analytic soft glow — no per-frame blur)
# ---------------------------------------------------------------------------

def _alpha(color: QColor, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(min(max(a, 0.0), 1.0))
    return c


def _soft_capsule(length: float, thick: float, blur_r: float,
                  c0: QColor, c1: QColor, opacity: float) -> QImage:
    """One blurred ribbon layer.

    Draws a horizontal capsule with the spec's length gradient
    (transparent -> tinted -> white-hot -> transparent), then applies a
    vertical Gaussian-soft mask (rect profile convolved with sigma = r/2,
    sampled via erf) — the analytic equivalent of a Gaussian blur of radius
    ``blur_r``. Everything (including peak opacity) is baked in; the result
    is drawn with only position/rotation/opacity animation.
    """
    r = max(float(blur_r), 0.0)
    iw = max(int(math.ceil(length + 2.0 * r)), 1)
    ih = max(int(math.ceil(thick + 2.0 * r)), 1)

    img = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)

    # Length gradient along the capsule (Glance's streakLayer stops).
    grad = QLinearGradient(r, 0.0, r + length, 0.0)
    for pos, col, base_a in (
        (0.00, c0, 0.00),
        (0.18, c0, 0.60),
        (0.55, c1, 1.00),
        (0.82, c1, 0.85),
        (1.00, c1, 0.00),
    ):
        grad.setColorAt(pos, _alpha(col, base_a * opacity))
    p = QPainter(img)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(r, r, length, thick), thick * 0.5, thick * 0.5)
    p.end()

    # Vertical profile: cross-section of a rect of width `thick` convolved
    # with a Gaussian of sigma = r/2 — the analytic result of blurring the
    # capsule by radius r. Sampled via math.erf into gradient stops, so the
    # glow has soft Gaussian shoulders instead of a hard plateau.
    mask = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(Qt.GlobalColor.transparent)
    sigma = max(r / 2.0, 1e-6)
    sq2 = sigma * math.sqrt(2.0)
    vgrad = QLinearGradient(0.0, 0.0, 0.0, float(ih))
    _stops = 16
    for _k in range(_stops + 1):
        _y = ih * _k / _stops
        _prof = 0.5 * (math.erf((_y - r) / sq2) - math.erf((_y - r - thick) / sq2))
        _a = int(round(255.0 * min(max(_prof, 0.0), 1.0)))
        vgrad.setColorAt(_k / _stops, QColor(0, 0, 0, _a))
    mp = QPainter(mask)
    mp.fillRect(0, 0, iw, ih, QBrush(vgrad))
    mp.end()

    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawImage(0, 0, mask)
    p.end()
    return img


def _render_streak(index: int, short_edge: float) -> QPixmap:
    """Prerender streak ``index`` (0..41): 4 stacked soft-capsule layers."""
    lane = index % 7
    thick = _LANE_THICKNESS[lane]
    length = max(short_edge * _LANE_LENGTH_FACTOR[lane] * 0.55, 180.0)
    blur_scale = _LANE_BLUR_BASE[lane] * (2.6 if (index % 4) != 0 else 1.1)
    vivid = (index % 6 == 1) or (index % 6 == 4)
    lateral = -0.86 + (index / (_STREAK_COUNT - 1)) * 1.72
    edge_fade = 1.0 - abs(lateral) * 0.22
    peak = _LANE_PEAK_BASE[lane] * edge_fade * (1.4 if vivid else 1.0)
    has_highlight = vivid or lane == 2 or lane == 4

    # (thickness_scale, blur_px, color0, color1, layer_opacity, layer_length)
    layers: list[tuple[float, float, QColor, QColor, float, float]] = [
        (2.35, 78.0 * blur_scale, _ACCENT, _ACCENT_BRIGHT,
         0.28 if vivid else 0.16, length),
        (1.00, 34.0 * blur_scale,
         _ACCENT_BRIGHT if vivid else _ACCENT,
         _ACCENT_PALE if vivid else _ACCENT_BRIGHT,
         0.48 if vivid else 0.30, length),
        (0.42, 14.0 * blur_scale, _ACCENT_BRIGHT, _ACCENT_PALE,
         0.62 if vivid else 0.38, length),
    ]
    if has_highlight:
        layers.append(
            (0.12, 6.0 * blur_scale, _ACCENT_PALE, _WHITE,
             0.36 if vivid else 0.20, length * 0.72)
        )

    max_r = max(r for _, r, _, _, _, _ in layers)
    w = max(int(math.ceil(length + 2.0 * max_r)), 1)
    h = max(int(math.ceil(thick * 2.35 + 2.0 * max_r)), 1)

    base = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    base.fill(Qt.GlobalColor.transparent)
    p = QPainter(base)
    for t_scale, r, c0, c1, layer_op, layer_len in layers:
        layer_img = _soft_capsule(layer_len, thick * t_scale, r,
                                  c0, c1, layer_op * peak)
        p.drawImage((w - layer_img.width()) // 2,
                    (h - layer_img.height()) // 2, layer_img)
    p.end()
    return QPixmap.fromImage(base)


# ---------------------------------------------------------------------------
# Active play state
# ---------------------------------------------------------------------------

class _ActivePlay:
    """One in-flight sweep (a play() call). Supports crossfade in/out."""

    __slots__ = ("pixmaps", "geoms", "t0", "end_t",
                 "fi_t0", "fi_dur", "fo_t0", "fo_dur")

    def __init__(self) -> None:
        self.pixmaps: list[QPixmap] = []
        self.geoms: list[_StreakGeom] = []
        self.t0 = 0.0          # sweep start (monotonic seconds)
        self.end_t = 0.0       # t0 + max(delay + duration)
        self.fi_t0: float | None = None  # crossfade-in start (None = none)
        self.fi_dur = 0.0
        self.fo_t0: float | None = None  # fade-out start (None = none)
        self.fo_dur = 0.0


# ---------------------------------------------------------------------------
# The overlay widget
# ---------------------------------------------------------------------------

class SweepOverlay(QWidget):
    """Fullscreen, frameless, click-through sweep overlay.

    Sits below the enrollment panel (both are stay-on-top; the panel is
    shown after the overlay, so it stays above). Never intercepts clicks.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._place_on_screen()

        # (direction, width, height) -> (pixmaps, geoms). Pixmap appearance
        # is direction-independent, so same-size entries share the list.
        self._cache: dict[tuple[str, int, int],
                          tuple[list[QPixmap], list[_StreakGeom]]] = {}
        self._plays: list[_ActivePlay] = []

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

        # Overlay-level fade (showEvent 0.5s in, stop() 0.35s out).
        self._ov_op = 0.0
        self._ov_from = 0.0
        self._ov_to = 0.0
        self._ov_t0 = 0.0
        self._ov_dur = 0.001
        self._hiding = False

    # -- setup ----------------------------------------------------------

    def _place_on_screen(self) -> None:
        app = QApplication.instance()
        screen = None
        if app is not None:
            try:
                screen = app.screenAt(QCursor.pos())
            except Exception:
                screen = None
            if screen is None:
                screen = app.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

    # -- public API -----------------------------------------------------

    def play(self, direction: str) -> None:
        """Play the sweep once (~1.4 s) toward ``direction``, then fade out.

        ``direction`` is one of left/right/up/down/topLeft/topRight/
        bottomLeft/bottomRight. If a sweep is mid-play, the old one
        crossfades out (easeInOut 0.25 s) while the new one fades in.
        """
        if direction not in _TRAVEL:
            raise ValueError(f"unknown sweep direction: {direction!r}")
        if not self.isVisible():
            self.show()
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        key = (direction, w, h)
        assets = self._cache.get(key)
        if assets is None:
            pixmaps: list[QPixmap] | None = None
            for (_d2, w2, h2), (px2, _g2) in self._cache.items():
                if w2 == w and h2 == h:
                    pixmaps = px2  # appearance is direction-independent
                    break
            if pixmaps is None:
                pixmaps = [self._render_streak_prerender(i, min(w, h))
                           for i in range(_STREAK_COUNT)]
            geoms = _build_geoms(direction, float(w), float(h))
            assets = (pixmaps, geoms)
            self._cache[key] = assets
            while len(self._cache) > 12:  # bound memory across resizes
                self._cache.pop(next(iter(self._cache)))

        pixmaps, geoms = assets
        now = time.monotonic()
        had_live = any(pl.fo_t0 is None for pl in self._plays)
        for pl in self._plays:
            if pl.fo_t0 is None:  # crossfade the outgoing sweep out
                pl.fo_t0 = now
                pl.fo_dur = 0.25

        play = _ActivePlay()
        play.pixmaps = pixmaps
        play.geoms = geoms
        play.t0 = now
        play.end_t = now + max(g.delay + g.dur for g in geoms)
        if had_live:  # crossfade the incoming sweep in
            play.fi_t0 = now
            play.fi_dur = 0.25
        self._plays.append(play)
        self._ensure_timer()

    def stop(self) -> None:
        """Fade out over 0.35 s, then hide."""
        if not self.isVisible() and self._ov_op <= 0.0:
            return
        now = time.monotonic()
        self._ov_from = self._ov_op
        self._ov_to = 0.0
        self._ov_t0 = now
        self._ov_dur = 0.35
        self._hiding = True
        self._plays.clear()
        self._ensure_timer()

    # -- Qt events ------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        now = time.monotonic()
        self._ov_from = self._ov_op
        self._ov_to = 1.0
        self._ov_t0 = now
        self._ov_dur = 0.5
        self._hiding = False
        self._ensure_timer()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._ov_op <= 0.001 or not self._plays:
            return
        now = time.monotonic()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for pl in self._plays:
            play_op = self._play_opacity(pl, now) * self._ov_op
            if play_op <= 0.001:
                continue
            elapsed = now - pl.t0
            for pix, g in zip(pl.pixmaps, pl.geoms, strict=True):
                local = elapsed - g.delay
                if local < 0.0:
                    continue
                t = min(local / g.dur, 1.0)
                e = _sweep_ease(t)
                u = 1.0 - e
                x = u * u * g.sx + 2.0 * u * e * g.qx + e * e * g.ex
                y = u * u * g.sy + 2.0 * u * e * g.qy + e * e * g.ey
                dx = 2.0 * u * (g.qx - g.sx) + 2.0 * e * (g.ex - g.qx)
                dy = 2.0 * u * (g.qy - g.sy) + 2.0 * e * (g.ey - g.qy)
                angle = math.degrees(math.atan2(dy, dx) + g.drift * e)
                op = _envelope(e) * play_op * _MASTER_OPACITY
                if op <= 0.003:
                    continue
                # Rotate first, then translate (matches Glance's ordering).
                p.save()
                p.translate(x, y)
                p.rotate(angle)
                p.setOpacity(op)
                p.drawPixmap(-pix.width() // 2, -pix.height() // 2, pix)
                p.restore()
        p.end()

    # -- animation driver -------------------------------------------------

    def _render_streak_prerender(self, index: int, short_edge: float) -> QPixmap:
        return _render_streak(index, short_edge)

    def _ensure_timer(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    @staticmethod
    def _play_opacity(pl: _ActivePlay, now: float) -> float:
        o = 1.0
        if pl.fi_t0 is not None:
            o *= _smoothstep((now - pl.fi_t0) / max(pl.fi_dur, 1e-9))
        if pl.fo_t0 is not None:
            o *= 1.0 - _smoothstep((now - pl.fo_t0) / max(pl.fo_dur, 1e-9))
        return o

    def _tick(self) -> None:
        now = time.monotonic()
        # Overlay-level fade.
        u = (now - self._ov_t0) / max(self._ov_dur, 1e-9)
        if u >= 1.0:
            self._ov_op = self._ov_to
        else:
            self._ov_op = self._ov_from + (self._ov_to - self._ov_from) * _smoothstep(u)
        if self._hiding:
            if self._ov_op <= 0.0:
                self._hiding = False
                self._timer.stop()
                self.hide()
            else:
                self.update()
            return
        # Retire finished plays; auto-fade a play once its sweep completes.
        live: list[_ActivePlay] = []
        for pl in self._plays:
            if pl.fo_t0 is None and now >= pl.end_t:
                pl.fo_t0 = now
                pl.fo_dur = 0.35
            if self._play_opacity(pl, now) > 0.0:
                live.append(pl)
        self._plays = live
        if not self._plays and self._ov_op == self._ov_to:
            self._timer.stop()  # idle: no work until the next play()/stop()
            return
        self.update()


# ---------------------------------------------------------------------------
# Manual visual test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = SweepOverlay()
    overlay.show()
    overlay.play("left")
    # Direction changes ride the 0.25 s crossfade path; stop() fades 0.35 s.
    QTimer.singleShot(2500, lambda: overlay.play("up"))
    QTimer.singleShot(5000, lambda: overlay.play("bottomRight"))
    QTimer.singleShot(7500, overlay.stop)
    QTimer.singleShot(9000, app.quit)
    sys.exit(app.exec())
