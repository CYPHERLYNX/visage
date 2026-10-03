"""Reusable premium widgets: cards, toggles, segmented controls, toasts."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from . import theme


class Card(QFrame):
    """Rounded dark card with hairline border."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD)
        layout.setSpacing(theme.GAP_SMALL)

    def layout(self) -> QVBoxLayout:  # type: ignore[override]
        return super().layout()  # type: ignore[return-value]


class RowCard(QFrame):
    """Card with a horizontal layout — for settings rows."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD)
        layout.setSpacing(theme.GAP)

    def layout(self) -> QHBoxLayout:  # type: ignore[override]
        return super().layout()  # type: ignore[return-value]


class Toggle(QPushButton):
    """iOS-style switch. checkable; checked == on."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(46, 27)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._knob = 4.0 if not self.isChecked() else 23.0
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        anim = QPropertyAnimation(self, b"knob", self)
        anim.setDuration(160)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(self._knob)
        anim.setEndValue(23.0 if on else 4.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    @pyqtProperty(float)
    def knob(self) -> float:
        return self._knob

    @knob.setter
    def knob(self, value: float) -> None:
        self._knob = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(theme.ACCENT if self.isChecked() else "#3A3A40")
        p.setBrush(track)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 46, 27, 13.5, 13.5)
        p.setBrush(QColor("white"))
        p.drawEllipse(int(self._knob), 4, 19, 19)
        p.end()

    # silence the default QPushButton text rendering
    def setText(self, text: str) -> None:  # noqa: N802
        pass


class SegmentedControl(QWidget):
    """Apple-style segmented picker. Emits indexChanged(int)."""

    indexChanged = pyqtSignal(int)

    def __init__(self, options: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self._options = options
        self._buttons: list[QPushButton] = []
        self._current = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.setStyleSheet(
            f"background: #101013; border: 1px solid {theme.BORDER}; border-radius: 10px;"
        )
        for i, opt in enumerate(options):
            btn = QPushButton(opt)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 7px;"
                f" padding: 7px 14px; color: {theme.TEXT_SECONDARY}; }}"
                "QPushButton:checked { background: #2E2E34;"
                f" color: {theme.TEXT}; font-weight: 600; }}"
            )
            btn.clicked.connect(lambda _=False, idx=i: self.setCurrent(idx))
            layout.addWidget(btn)
            self._buttons.append(btn)
        self.setCurrent(0)

    def setCurrent(self, index: int) -> None:  # noqa: N802
        index = max(0, min(index, len(self._buttons) - 1))
        if index == self._current and self._buttons[index].isChecked():
            return
        self._current = index
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)
        self.indexChanged.emit(index)

    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def currentText(self) -> str:
        return self._options[self._current]


class ProgressRing(QWidget):
    """Circular progress indicator, 0..1."""

    def __init__(self, diameter: int = 120, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._value = 0.0
        self._diameter = diameter

    def setValue(self, value: float) -> None:  # noqa: N802
        self._value = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pad = 8
        rect = QRect(pad, pad, self._diameter - 2 * pad, self._diameter - 2 * pad)
        pen = QPen(QColor(theme.BORDER), 7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(rect)
        pen.setColor(QColor(theme.ACCENT))
        p.setPen(pen)
        start_angle = 90 * 16
        span_angle = -int(self._value * 360 * 16)
        p.drawArc(rect, start_angle, span_angle)
        p.end()


class PoseDots(QWidget):
    """Per-pose sample dots (Glance captures 2 samples per pose)."""

    def __init__(self, total: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._total = total
        self._filled = 0
        self.setFixedHeight(14)

    def setFilled(self, n: int) -> None:  # noqa: N802
        self._filled = max(0, min(self._total, n))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(self._total):
            x = 4 + i * 20
            color = QColor(theme.ACCENT if i < self._filled else theme.BORDER)
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(x, 3, 8, 8)
        p.end()


class AvatarLabel(QLabel):
    """Circular identity avatar showing the person's initial."""

    def __init__(self, name: str, diameter: int = 44, parent: QWidget | None = None):
        super().__init__(parent)
        self._initial = (name.strip()[:1] or "?").upper()
        self.setFixedSize(diameter, diameter)
        self._diameter = diameter

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#2A2A32"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, self._diameter, self._diameter)
        p.setPen(QColor(theme.TEXT))
        font = p.font()
        font.setPixelSize(int(self._diameter * 0.42))
        font.setBold(True)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._initial)
        p.end()


class LogoMark(QLabel):
    """Visage logo: a rounded square with an eye-like arc, drawn in code."""

    def __init__(self, size: int = 34, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size

    def paintEvent(self, event) -> None:  # noqa: N802
        s = self._size
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # rounded square
        path = QPainterPath()
        path.addRoundedRect(0, 0, s, s, s * 0.28, s * 0.28)
        p.fillPath(path, QColor(theme.ACCENT))
        # eye arc
        p.setPen(QPen(QColor("white"), s * 0.07, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx, cy = s / 2, s / 2
        p.drawArc(int(cx - s * 0.22), int(cy - s * 0.13), int(s * 0.44), int(s * 0.26), 20 * 16, 140 * 16)
        # pupil
        p.setBrush(QColor("white"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(cx - s * 0.055), int(cy - s * 0.055), int(s * 0.11), int(s * 0.11))
        p.end()


class Toast(QWidget):
    """Small transient message that fades at the bottom of a window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._label = QLabel(self)
        self._label.setStyleSheet(
            f"background: #2A2A30; color: {theme.TEXT}; border: 1px solid {theme.BORDER};"
            " border-radius: 8px; padding: 10px 16px;"
        )
        layout = QVBoxLayout(self)
        layout.addWidget(self._label, alignment=Qt.AlignmentFlag.AlignCenter)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade)

    def show_message(self, text: str, ms: int = 2600) -> None:
        self._label.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(0, parent.height() - 90, parent.width(), 60)
        self._effect.setOpacity(1.0)
        self.show()
        self.raise_()
        self._timer.start(ms)

    def _fade(self) -> None:
        anim = QPropertyAnimation(self._effect, b"opacity", self)
        anim.setDuration(400)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self.hide)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class CueBar(QWidget):
    """Horizontal level bar with a threshold marker (Face Lab cue rows)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.level = 0.0
        self.threshold = 0.5

    def set_levels(self, level: float, threshold: float) -> None:
        self.level = max(0.0, min(1.0, level))
        self.threshold = max(0.0, min(1.0, threshold))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setBrush(QColor(theme.BORDER))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 2, w, h - 4, 3, 3)
        fill_w = int(w * self.level)
        if fill_w > 0:
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(0, 2, fill_w, h - 4, 3, 3)
        tx = int(w * self.threshold)
        p.setPen(QPen(QColor(theme.WARNING), 2))
        p.drawLine(tx, 0, tx, h)
        p.end()


class AnimatedCheckmark(QWidget):
    """Draws itself with a trim animation, like Glance's AnimatedCheckmark:
    ease-out cubic over ~0.6s, round caps."""

    def __init__(self, diameter: int = 84, color: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._diameter = diameter
        self._color = QColor(color or theme.ACCENT)
        self._progress = 0.0

    def play(self) -> None:
        self._progress = 0.0
        anim = QPropertyAnimation(self, b"drawProgress", self)
        anim.setDuration(600)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    @pyqtProperty(float)
    def drawProgress(self) -> float:  # noqa: N802
        return self._progress

    @drawProgress.setter
    def drawProgress(self, value: float) -> None:  # noqa: N802
        self._progress = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self._diameter
        # checkmark path in unit space: (0.16,0.52) -> (0.40,0.78) -> (0.86,0.22)
        pts = [(0.16 * d, 0.52 * d), (0.40 * d, 0.78 * d), (0.86 * d, 0.22 * d)]
        seg1 = math.dist(pts[0], pts[1])
        seg2 = math.dist(pts[1], pts[2])
        total = seg1 + seg2
        target = self._progress * total
        pen = QPen(self._color, max(4, d * 0.09))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        drawn = 0.0
        prev = pts[0]
        for nxt, seg in ((pts[1], seg1), (pts[2], seg2)):
            if target <= drawn:
                break
            take = min(seg, target - drawn)
            t = take / seg if seg > 0 else 0
            end = (prev[0] + (nxt[0] - prev[0]) * t, prev[1] + (nxt[1] - prev[1]) * t)
            p.drawLine(int(prev[0]), int(prev[1]), int(end[0]), int(end[1]))
            drawn += take
            prev = nxt
        p.end()


class PoseStrip(QWidget):
    """Nine segments — one per guided direction. Each fills with its two
    samples, translating Glance's tick ring into a linear strip."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self._fills: list[float] = [0.0] * 9
        self._current = 0

    def set_state(self, fills: list[float], current: int) -> None:
        self._fills = [(max(0.0, min(1.0, f))) for f in fills[:9]]
        while len(self._fills) < 9:
            self._fills.append(0.0)
        self._current = current
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n, gap = 9, 6
        w = (self.width() - gap * (n - 1)) / n
        h = self.height()
        for i in range(n):
            x = i * (w + gap)
            p.setBrush(QColor(theme.BORDER))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(int(x), 0, int(w), h, 3, 3)
            fill = self._fills[i]
            if fill > 0:
                color = theme.ACCENT if i != self._current else "#5AA9FF"
                p.setBrush(QColor(color))
                p.drawRoundedRect(int(x), 0, max(int(w * fill), 6), h, 3, 3)
        p.end()
