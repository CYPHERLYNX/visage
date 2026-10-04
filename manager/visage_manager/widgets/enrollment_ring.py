"""Enrollment tick ring — PyQt6 replication of Glance's EnrollmentRingView.

Spec: ``docs/glance-specs/enrollment-ui.md``, section 1.

80 ticks tile the circle (10 per 45-degree sector). Every motion is driven by
a 60 Hz QTimer that interpolates per-tick animation tracks (length, color
intensity, opacity, width) plus the completion ring's opacity/scale tracks.
No text, no other UI — just the ring, on a transparent background.
"""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

_ACCENT = QColor(0x34, 0x99, 0xFF)
_WHITE = QColor(255, 255, 255)

_BOX = 240                     # cluster reserve diameter (layout box)
_RING_RADIUS = 100.0           # tick inner-tip anchor radius
_TICK_COUNT = 80
_TICK_WIDTH = 2.4
_TICK_WIDTH_MERGED = 2.0 * math.pi * _RING_RADIUS / _TICK_COUNT * 1.2  # ~9.42
_LEN_UNLIT = 12.0
_LEN_LIT = 20.0
_HEAD_TURN_GROWTH = 5.0        # head-turn indicator never reaches lit length
_HEAD_TURN_DEADZONE = 0.15
_PULSE_MS = 220


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def _ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def _sector_half_away(angle_deg: float) -> int:
    """Sector index (0..7) for a compass angle (0 = up, clockwise).

    Boundary ticks round to the higher sector — Swift ``rounded()`` is half
    away from zero, unlike Python's banker's ``round()``.
    """
    return int(math.floor(angle_deg / 45.0 + 0.5)) % 8


def _angle_diff(a: float, b: float) -> float:
    """Smallest absolute angular difference in degrees."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _mix_white_accent(intensity: float) -> QColor:
    """Linear white -> accent lerp per channel (``whiteToAccent``)."""
    t = max(0.0, min(1.0, intensity))
    return QColor(
        int(round(255 + (0x34 - 255) * t)),
        int(round(255 + (0x99 - 255) * t)),
        255,
    )


class EnrollmentRing(QWidget):
    """80-tick enrollment ring. See module docstring for the animation model."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_BOX, _BOX)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Per-tick state: length, color intensity, opacity, width.
        self._tick = [
            {"length": _LEN_UNLIT, "intensity": 0.0,
             "opacity": 1.0, "width": _TICK_WIDTH}
            for _ in range(_TICK_COUNT)
        ]
        # prop -> (from, to, t0, duration, delay, easing)
        self._tracks: list[dict[str, tuple]] = [dict() for _ in range(_TICK_COUNT)]
        self._tick_sector = [_sector_half_away(i * 4.5) for i in range(_TICK_COUNT)]
        self._captured: set[int] = set()

        # Completion solid ring.
        self._ring = {"opacity": 0.0, "scale": 0.92}
        self._ring_tracks: dict[str, tuple] = {}

        # Pulse generation counter — stale singleShot callbacks no-op.
        self._pulse_id = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(16)  # ~60 Hz

    # ------------------------------------------------------------------ API

    def set_captured_sectors(self, sectors: set[float]) -> None:
        """Mark compass angles (e.g. {0.0, 45.0}) as captured; their ticks
        fill with a center-out stagger. Center has no sector."""
        new = {_sector_half_away(a) for a in sectors} - self._captured
        if not new:
            return
        self._captured |= new
        for i in range(_TICK_COUNT):
            if self._tick_sector[i] in new:
                self._animate_tick(i, "intensity", 1.0, 0.0)  # snap to accent
                self._animate_tick(i, "length", _LEN_LIT, 0.3,
                                   delay=(i % 10) * 0.008,
                                   ease=_ease_out_cubic)
        self.update()

    def pulse_center(self) -> None:
        """Whole-ring 220 ms pulse: every tick eases to lit length, then back."""
        self._pulse_id += 1
        pid = self._pulse_id
        for i in range(_TICK_COUNT):
            self._animate_tick(i, "length", _LEN_LIT, 0.22, ease=_ease_out_cubic)
        self.update()
        QTimer.singleShot(_PULSE_MS, lambda: self._end_pulse(pid))

    def set_head_turn(self, angle: float | None, progress: float) -> None:
        """Live head-turn indicator. ``angle`` is compass degrees (0 = up,
        clockwise); hidden when None or progress < 0.15. Unlit ticks only."""
        show = angle is not None and progress >= _HEAD_TURN_DEADZONE
        for i in range(_TICK_COUNT):
            if self._tick_sector[i] in self._captured:
                continue
            if show:
                delta = _angle_diff(i * 4.5, angle)
                falloff = max(0.0, 1.0 - (delta / 4.5) / 3.0)
                intensity = progress * falloff
            else:
                intensity = 0.0
            self._animate_tick(i, "intensity", intensity, 0.15,
                               ease=_ease_out_cubic)
            self._animate_tick(i, "length", _LEN_UNLIT + _HEAD_TURN_GROWTH * intensity,
                               0.15, ease=_ease_out_cubic)
        self.update()

    def set_complete(self) -> None:
        """Completion merge: ticks fade out (staggered) while a solid accent
        ring fades/scales in."""
        self._pulse_id += 1  # cancel any in-flight pulse
        for i in range(_TICK_COUNT):
            self._animate_tick(i, "opacity", 0.0, 0.45,
                               delay=i * 0.004, ease=_ease_in_out_cubic)
            self._animate_tick(i, "width", _TICK_WIDTH_MERGED, 0.45,
                               delay=i * 0.004, ease=_ease_in_out_cubic)
        self._animate_ring("opacity", 1.0, 0.45, ease=_ease_in_out_cubic)
        self._animate_ring("scale", 1.0, 0.45, ease=_ease_in_out_cubic)
        self.update()

    def reset(self) -> None:
        """Back to the initial empty state."""
        self._pulse_id += 1
        self._captured.clear()
        for tracks in self._tracks:
            tracks.clear()
        self._ring_tracks.clear()
        for t in self._tick:
            t["length"] = _LEN_UNLIT
            t["intensity"] = 0.0
            t["opacity"] = 1.0
            t["width"] = _TICK_WIDTH
        self._ring["opacity"] = 0.0
        self._ring["scale"] = 0.92
        self.update()

    # ---------------------------------------------------------- animation

    def _end_pulse(self, pid: int) -> None:
        if pid != self._pulse_id:
            return
        for i in range(_TICK_COUNT):
            if self._tick_sector[i] not in self._captured:
                self._animate_tick(i, "length", _LEN_UNLIT, 0.22,
                                   ease=_ease_out_cubic)
        self.update()

    def _animate_tick(self, i: int, prop: str, target: float, duration: float,
                      delay: float = 0.0, ease=_ease_out_cubic) -> None:
        if duration <= 0.0:
            self._tick[i][prop] = target
            self._tracks[i].pop(prop, None)
            return
        self._tracks[i][prop] = (
            self._tick[i][prop], target, time.monotonic(), duration, delay, ease,
        )

    def _animate_ring(self, prop: str, target: float, duration: float,
                      delay: float = 0.0, ease=_ease_in_out_cubic) -> None:
        if duration <= 0.0:
            self._ring[prop] = target
            self._ring_tracks.pop(prop, None)
            return
        self._ring_tracks[prop] = (
            self._ring[prop], target, time.monotonic(), duration, delay, ease,
        )

    def _advance(self) -> None:
        now = time.monotonic()
        active = False
        for i in range(_TICK_COUNT):
            if self._tracks[i] and self._step(self._tracks[i], self._tick[i], now):
                active = True
        if self._ring_tracks and self._step(self._ring_tracks, self._ring, now):
            active = True
        if active:
            self.update()

    @staticmethod
    def _step(tracks: dict, state: dict, now: float) -> bool:
        active = False
        for prop in list(tracks):
            frm, to, t0, dur, delay, ease = tracks[prop]
            el = now - t0 - delay
            if el < 0.0:
                active = True
                continue
            p = 1.0 if dur <= 0.0 else min(el / dur, 1.0)
            state[prop] = frm + (to - frm) * ease(p)
            active = True
            if p >= 1.0:
                del tracks[prop]
        return active

    # ------------------------------------------------------------- painting

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = _BOX / 2.0

        # Completion solid ring (diameter 210, stroke 14, accent).
        if self._ring["opacity"] > 0.001:
            p.save()
            p.translate(c, c)
            s = self._ring["scale"]
            p.scale(s, s)
            p.setOpacity(self._ring["opacity"])
            pen = QPen(_ACCENT)
            pen.setWidthF(14.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawEllipse(QRectF(-105.0, -105.0, 210.0, 210.0))
            p.restore()

        # Ticks: inner tip anchored at the ring radius, growth outward.
        for i in range(_TICK_COUNT):
            t = self._tick[i]
            if t["opacity"] <= 0.001:
                continue
            p.save()
            p.translate(c, c)
            p.rotate(i * 4.5)  # 0 = top, clockwise
            p.setOpacity(t["opacity"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_mix_white_accent(t["intensity"]))
            w = t["width"]
            length = t["length"]
            p.drawRoundedRect(
                QRectF(-w / 2.0, -(_RING_RADIUS + length), w, length),
                w / 2.0, w / 2.0,
            )
            p.restore()


if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    win = QWidget()
    win.setWindowTitle("EnrollmentRing self-test")
    win.setStyleSheet("background-color: black;")
    win.resize(400, 400)
    ring = EnrollmentRing(win)
    ring.move((400 - _BOX) // 2, (400 - _BOX) // 2)
    win.show()

    # empty -> capture 3 sectors (staggered fill)
    QTimer.singleShot(600, lambda: ring.set_captured_sectors({0.0, 45.0, 90.0}))
    # center pulse
    QTimer.singleShot(2400, ring.pulse_center)

    # head-turn indicator sweeping around the dial
    sweep = QTimer()
    sweep.setInterval(50)
    angle = [0.0]

    def _on_sweep_tick() -> None:
        ring.set_head_turn(angle[0], 0.9)
        angle[0] += 12.0
        if angle[0] >= 360.0:
            sweep.stop()
            ring.set_head_turn(None, 0.0)
            QTimer.singleShot(500, ring.set_complete)

    sweep.timeout.connect(_on_sweep_tick)
    QTimer.singleShot(3400, sweep.start)

    # back to empty at the end
    QTimer.singleShot(9500, ring.reset)

    sys.exit(app.exec())
