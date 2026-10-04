"""Glance-ditto settings window chrome for the Visage Manager.

A fixed 500x620 dark window with:
  - 52px header bar: "Visage" title left, session-lock capsule right
  - scrolling content area (16px horizontal padding, 84px bottom inset)
  - floating capsule tab bar (54px, 14px above the bottom edge):
    General / Face / Password / Camera / Recognition / About
  - QStackedWidget pages; window always opens on General

Pages are supplied by the caller via add_page(). The session lock state is
local (the engine bridge exposes no session API); pages query it with
is_session_unlocked(). Wire session_button_clicked to the real auth flow
(Windows Hello / password dialog); the demo toggles locally.
"""

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
    QKeySequence,
)
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from ..theme import FONT_FAMILY
except ImportError:  # running as a standalone script: fall back to absolute import
    import os
    import sys

    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    from visage_manager.theme import FONT_FAMILY

# --- Glance tokens (docs/glance-specs/unlock-settings.md section 10) ---
ACCENT = "#3499FF"
SURFACE = "#1E1E1E"
SURFACE_RAISED = "#323232"
TEXT_PRIMARY = "#EEEEEE"
TEXT_SECONDARY = "#BFBFBF"
TEXT_TERTIARY = "#999999"
ROW_FILL = "rgba(255, 255, 255, 0.08)"
ROW_BORDER = "rgba(255, 255, 255, 0.12)"
TAB_BAR_BG = "#2A2A2A"
TAB_BAR_BORDER = "rgba(255, 255, 255, 0.16)"
SELECTED_PILL = "rgba(255, 255, 255, 0.10)"

WINDOW_W = 500
WINDOW_H = 620
HEADER_H = 52
TAB_BAR_H = 54
TAB_BAR_MARGIN_BOTTOM = 14
TAB_BAR_MARGIN_SIDE = 6
CONTENT_H_PAD = 16
CONTENT_BOTTOM_INSET = 84  # clears the floating tab bar

TAB_ORDER = [
    ("general", "General", "gear"),
    ("face", "Face", "face"),
    ("password", "Password", "lock"),
    ("camera", "Camera", "camera"),
    ("recognition", "Recognition", "sparkle"),
    ("about", "About", "info"),
]


# ---------------------------------------------------------------------------
# Vector icons (no emojis, no font glyphs — drawn with QPainter so they render
# identically on every Windows machine).
# ---------------------------------------------------------------------------
def paint_icon(painter: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    """Paint a simple line icon centered in ``rect``."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy = rect.center().x(), rect.center().y()
    s = min(rect.width(), rect.height())
    pen = QPen(
        color,
        max(1.2, s * 0.11),
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if name == "gear":
        painter.translate(cx, cy)
        for i in range(8):
            painter.save()
            painter.rotate(i * 45.0)
            painter.drawLine(QPointF(0, -s * 0.44), QPointF(0, -s * 0.30))
            painter.restore()
        painter.drawEllipse(QPointF(0, 0), s * 0.30, s * 0.30)
        painter.drawEllipse(QPointF(0, 0), s * 0.12, s * 0.12)
    elif name == "face":
        painter.drawEllipse(QPointF(cx, cy), s * 0.36, s * 0.36)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx - s * 0.13, cy - s * 0.08), s * 0.045, s * 0.045)
        painter.drawEllipse(QPointF(cx + s * 0.13, cy - s * 0.08), s * 0.045, s * 0.045)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawArc(
            QRectF(cx - s * 0.18, cy - s * 0.06, s * 0.36, s * 0.30), 200 * 16, 140 * 16
        )
    elif name in ("lock", "lock_open"):
        # shackle
        if name == "lock":
            painter.drawArc(QRectF(cx - s * 0.16, cy - s * 0.36, s * 0.32, s * 0.34), 0, 180 * 16)
        else:
            painter.drawArc(QRectF(cx - s * 0.16, cy - s * 0.40, s * 0.32, s * 0.34), 25 * 16, 155 * 16)
        # body
        path = QPainterPath()
        body = QRectF(cx - s * 0.24, cy - s * 0.04, s * 0.48, s * 0.38)
        path.addRoundedRect(body, s * 0.07, s * 0.07)
        painter.drawPath(path)
        # keyhole
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy + s * 0.10), s * 0.035, s * 0.035)
    elif name == "camera":
        path = QPainterPath()
        body = QRectF(cx - s * 0.32, cy - s * 0.20, s * 0.44, s * 0.40)
        path.addRoundedRect(body, s * 0.08, s * 0.08)
        painter.drawPath(path)
        trap = QPainterPath()
        trap.moveTo(cx + s * 0.12, cy - s * 0.10)
        trap.lineTo(cx + s * 0.36, cy - s * 0.20)
        trap.lineTo(cx + s * 0.36, cy + s * 0.20)
        trap.lineTo(cx + s * 0.12, cy + s * 0.10)
        painter.drawPath(trap)
    elif name == "sparkle":
        pts = []
        for i, (dx, dy) in enumerate(
            [(0, -0.42), (0.09, -0.09), (0.42, 0), (0.09, 0.09),
             (0, 0.42), (-0.09, 0.09), (-0.42, 0), (-0.09, -0.09)]
        ):
            pts.append(QPointF(cx + dx * s, cy + dy * s))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(*pts)
    elif name == "info":
        painter.drawEllipse(QPointF(cx, cy), s * 0.36, s * 0.36)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy - s * 0.17), s * 0.045, s * 0.045)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawLine(QPointF(cx, cy - s * 0.04), QPointF(cx, cy + s * 0.22))
    painter.restore()


def icon_pixmap(name: str, size: int, color: QColor) -> QPixmap:
    """Rasterize an icon for use in QIcon/QPushButton."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    pad = size * 0.12
    paint_icon(p, name, QRectF(pad, pad, size - 2 * pad, size - 2 * pad), color)
    p.end()
    return pm


class _IconWidget(QWidget):
    """Fixed 19px box painting a vector icon (Glance tab glyph cell)."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self.setFixedSize(19, 19)

    def set_icon(self, name: str) -> None:
        self._name = name
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        color = QColor(TEXT_PRIMARY) if self.isEnabled() else QColor(TEXT_TERTIARY)
        paint_icon(p, self._name, QRectF(0, 0, 19, 19), color)
        p.end()


# ---------------------------------------------------------------------------
# Tab bar
# ---------------------------------------------------------------------------
class _TabButton(QWidget):
    """One tab: icon always visible, title unfurls beside it when selected."""

    clicked = pyqtSignal()

    def __init__(self, icon_name: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title_text = title
        self._selected = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 0, 11, 0)
        lay.setSpacing(8)

        self._icon = _IconWidget(icon_name, self)
        lay.addWidget(self._icon)

        self._label = QLabel(title, self)
        font = QFont(FONT_FAMILY.split(",")[0].strip())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        self._label.setFont(font)
        self._label.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent;")
        self._label.setMinimumWidth(0)
        self._label.setMaximumWidth(0)
        self._full_width = self._label.fontMetrics().horizontalAdvance(title) + 4
        lay.addWidget(self._label)

        self.setFixedHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._fade = QGraphicsOpacityEffect(self._label)
        self._fade.setOpacity(0.0)
        self._label.setGraphicsEffect(self._fade)

        self._group = QParallelAnimationGroup(self)
        self._width_anim = QPropertyAnimation(self._label, b"maximumWidth", self)
        self._fade_anim = QPropertyAnimation(self._fade, b"opacity", self)
        self._group.addAnimation(self._width_anim)
        self._group.addAnimation(self._fade_anim)

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self._group.stop()
        dur = 350 if selected else 220
        self._width_anim.setDuration(dur)
        self._width_anim.setEasingCurve(
            QEasingCurve.Type.OutBack if selected else QEasingCurve.Type.OutCubic
        )
        self._width_anim.setStartValue(self._label.maximumWidth())
        self._width_anim.setEndValue(self._full_width if selected else 0)
        self._fade_anim.setDuration(dur)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.setStartValue(self._fade.opacity())
        self._fade_anim.setEndValue(1.0 if selected else 0.0)
        self._group.start()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)


class _TabBar(QWidget):
    """Floating capsule tab bar with a sliding highlight pill."""

    tab_clicked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[_TabButton] = []
        self._keys: list[str] = []
        self._current = -1

        self.setStyleSheet(
            f"""
            _TabBar {{
                background: {TAB_BAR_BG};
                border: 1px solid {TAB_BAR_BORDER};
                border-radius: {TAB_BAR_H // 2}px;
            }}
            """
        )

        # Highlight pill sits behind the buttons and tracks the selected one.
        self._pill = QWidget(self)
        self._pill.setStyleSheet(
            f"""
            background: {SELECTED_PILL};
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 18px;
            """
        )
        self._pill.setFixedHeight(36)
        self._pill.lower()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        lay.addStretch(1)
        for key, title, icon in TAB_ORDER:
            btn = _TabButton(icon, title, self)
            btn.clicked.connect(lambda _=False, k=key: self.tab_clicked.emit(k))
            btn.setEnabled(False)  # enabled when its page is added
            lay.addWidget(btn)
            self._buttons.append(btn)
            self._keys.append(key)
            # The pill tracks the selected button live while its title unfurls,
            # inheriting the spring from the title-width animation.
            btn._width_anim.valueChanged.connect(self._track_pill)
        lay.addStretch(1)

    def set_page_enabled(self, key: str, enabled: bool = True) -> None:
        if key in self._keys:
            self._buttons[self._keys.index(key)].setEnabled(enabled)

    def select(self, key: str) -> None:
        if key not in self._keys:
            return
        idx = self._keys.index(key)
        if idx == self._current:
            return
        if self._current >= 0:
            self._buttons[self._current].set_selected(False)
        self._current = idx
        self._buttons[idx].set_selected(True)
        self._track_pill()

    def _track_pill(self) -> None:
        if self._current < 0:
            return
        btn = self._buttons[self._current]
        r = btn.geometry()
        self._pill.setGeometry(r.adjusted(-2, 3, 2, -3))


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------
class SettingsWindow(QMainWindow):
    """Glance-ditto settings window chrome (500x620, dark)."""

    page_changed = pyqtSignal(str)
    session_button_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Visage Settings")
        self.setFixedSize(WINDOW_W, WINDOW_H)

        self._pages: dict[str, QWidget] = {}
        self._session_state = "locked"  # locked | authenticating | unlocked

        # Styling comes from the application stylesheet (theme.STYLESHEET),
        # set in app.main(). Do not override it here.

        central = QWidget(self)
        self.setCentralWidget(central)

        # -- header (52px) ---------------------------------------------------
        header = QWidget(central)
        header.setGeometry(0, 0, WINDOW_W, HEADER_H)
        header.setStyleSheet(
            f"background: {SURFACE}; border-bottom: 1px solid rgba(255, 255, 255, 0.08);"
        )
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(CONTENT_H_PAD, 0, CONTENT_H_PAD, 0)

        title = QLabel("Visage", header)
        tfont = QFont(FONT_FAMILY.split(",")[0].strip())
        tfont.setPixelSize(15)
        tfont.setWeight(QFont.Weight.DemiBold)
        title.setFont(tfont)
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent; border: none;")
        hlay.addWidget(title)
        hlay.addStretch(1)

        self._session_btn = QPushButton(header)
        self._session_btn.setObjectName("sessionCapsule")
        self._session_btn.setFixedHeight(30)
        self._session_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._session_btn.setStyleSheet(
            f"""
            QPushButton#sessionCapsule {{
                background: {ROW_FILL};
                border: 1px solid {ROW_BORDER};
                border-radius: 15px;
                padding-left: 12px;
                padding-right: 12px;
                color: {TEXT_PRIMARY};
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#sessionCapsule:hover {{
                background: rgba(255, 255, 255, 0.14);
            }}
            QPushButton#sessionCapsule:disabled {{
                color: {TEXT_SECONDARY};
            }}
            """
        )
        self._session_btn.clicked.connect(self.session_button_clicked.emit)
        hlay.addWidget(self._session_btn)
        self._refresh_session_button()

        # -- content ---------------------------------------------------------
        self._scroll = QScrollArea(central)
        self._scroll.setGeometry(0, HEADER_H, WINDOW_W, WINDOW_H - HEADER_H)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self._scroll.viewport().setStyleSheet("background: transparent;")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        clay = QVBoxLayout(container)
        clay.setContentsMargins(CONTENT_H_PAD, CONTENT_H_PAD, CONTENT_H_PAD, CONTENT_BOTTOM_INSET)
        clay.setSpacing(0)

        self._stack = QStackedWidget(container)
        self._stack.setStyleSheet("background: transparent;")
        clay.addWidget(self._stack)
        self._scroll.setWidget(container)

        # -- floating tab bar -------------------------------------------------
        self._tabbar = _TabBar(central)
        self._tabbar.setGeometry(
            TAB_BAR_MARGIN_SIDE,
            WINDOW_H - TAB_BAR_MARGIN_BOTTOM - TAB_BAR_H,
            WINDOW_W - 2 * TAB_BAR_MARGIN_SIDE,
            TAB_BAR_H,
        )
        self._tabbar.tab_clicked.connect(self.set_page)
        self._tabbar.raise_()

        # Ctrl+Tab / Ctrl+Shift+Tab to cycle tabs (Glance: swipe; n/a on Windows)
        next_sc = QShortcut(QKeySequence("Ctrl+Tab"), self)
        next_sc.setContext(Qt.ShortcutContext.WindowShortcut)
        next_sc.activated.connect(lambda: self._cycle_tab(1))
        prev_sc = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        prev_sc.setContext(Qt.ShortcutContext.WindowShortcut)
        prev_sc.activated.connect(lambda: self._cycle_tab(-1))

    # -- pages ---------------------------------------------------------------
    def add_page(self, key: str, title: str, widget: QWidget) -> None:
        """Register a page. ``title`` is kept for compatibility/debugging."""
        if key in self._pages:
            self._stack.removeWidget(self._pages[key])
        widget.setObjectName(f"page_{key}")
        self._pages[key] = widget
        self._stack.addWidget(widget)
        self._tabbar.set_page_enabled(key, True)
        if len(self._pages) == 1:
            self.set_page(key)

    def set_page(self, key: str) -> None:
        """Switch to a registered page and update the tab bar."""
        if key not in self._pages:
            return
        self._stack.setCurrentWidget(self._pages[key])
        self._tabbar.select(key)
        self.page_changed.emit(key)
        self.refresh_current()

    def current_page_key(self) -> str | None:
        for key, widget in self._pages.items():
            if widget is self._stack.currentWidget():
                return key
        return None

    def refresh_current(self) -> None:
        """Call refresh() on the current page if it defines one."""
        page = self._stack.currentWidget()
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    def _cycle_tab(self, direction: int) -> None:
        keys = [k for k, _t, _i in TAB_ORDER if k in self._pages]
        if not keys:
            return
        cur = self.current_page_key()
        idx = keys.index(cur) if cur in keys else 0
        self.set_page(keys[(idx + direction) % len(keys)])

    # -- session lock ---------------------------------------------------------
    def is_session_unlocked(self) -> bool:
        """Pages query this to decide whether to show locked empty states."""
        return self._session_state == "unlocked"

    def set_session_unlocked(self, unlocked: bool) -> None:
        self._session_state = "unlocked" if unlocked else "locked"
        self._refresh_session_button()

    def set_session_authenticating(self, active: bool = True) -> None:
        self._session_state = "authenticating" if active else "locked"
        self._refresh_session_button()

    def _refresh_session_button(self) -> None:
        state = self._session_state
        if state == "unlocked":
            text, icon, enabled = "Session unlocked", "lock_open", True
        elif state == "authenticating":
            text, icon, enabled = "Authenticating\u2026", "lock", False
        else:
            text, icon, enabled = "Session locked", "lock", True
        self._session_btn.setText(text)
        self._session_btn.setIcon(QIcon(icon_pixmap(icon, 24, QColor(TEXT_PRIMARY))))
        self._session_btn.setEnabled(enabled)

    # -- window behavior -------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Glance: the window always opens on General.
        if "general" in self._pages:
            self.set_page("general")
        self._tabbar._track_pill()


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    win = SettingsWindow()

    for key, title, _icon in TAB_ORDER:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)
        head = QLabel(title, page)
        hfont = QFont(FONT_FAMILY.split(",")[0].strip())
        hfont.setPixelSize(17)
        hfont.setWeight(QFont.Weight.DemiBold)
        head.setFont(hfont)
        head.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent;")
        lay.addWidget(head)
        for i in range(4):
            row = QLabel(f"{title} placeholder row {i + 1}", page)
            row.setFixedHeight(44)
            row.setStyleSheet(
                f"background: {ROW_FILL}; border: 1px solid {ROW_BORDER};"
                "border-radius: 16px; color: #BFBFBF; padding-left: 14px;"
            )
            lay.addWidget(row)
        lay.addStretch(1)

        def make_refresh(t=title):
            def _refresh():
                print(f"[demo] refresh() called on {t} page")
            return _refresh

        page.refresh = make_refresh()
        win.add_page(key, title, page)

    # Demo: session button runs a fake auth sequence.
    def _on_session_clicked():
        if win.is_session_unlocked():
            win.set_session_unlocked(False)
            win.refresh_current()
        else:
            win.set_session_authenticating(True)
            QTimer.singleShot(1200, lambda: (win.set_session_unlocked(True), win.refresh_current()))

    win.session_button_clicked.connect(_on_session_clicked)
    win.page_changed.connect(lambda k: print(f"[demo] page_changed: {k}"))

    win.show()
    print("[demo] current page:", win.current_page_key())
    print("[demo] session unlocked:", win.is_session_unlocked())
    sys.exit(app.exec())
