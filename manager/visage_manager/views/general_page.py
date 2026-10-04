"""General settings page — Glance-ditto layout for Windows.

Sections: Card 1 (launch/tray/face-unlock/triggers/display), Behaviour,
Animation, Lock screen. Copy follows docs/glance-specs/unlock-settings.md.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .. import theme
from ..engine_bridge import EngineBridge
from ..settings_store import SettingsStore
from ..widgets import Toast, Toggle

ACCENT = QColor(theme.ACCENT)
_ACCENT_WASH = "rgba(52,153,255,0.12)"
_ROW_FILL = "rgba(255,255,255,0.08)"
_ROW_BORDER = "rgba(204,204,204,0.12)"
_TILE_FILL = "rgba(255,255,255,0.05)"
_TILE_BORDER = "rgba(255,255,255,0.10)"

TRIGGERS = [
    ("on_wake", "On wake"),
    ("on_lock", "On lock"),
    ("on_space", "On space"),
]


class LockIcon(QWidget):
    """Simple painted padlock — no emoji, no font glyph dependency."""

    def __init__(self, size: int = 34, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor("#999999")

    def paintEvent(self, event) -> None:  # noqa: N802
        s = self.width()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color, max(2, s // 14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        # Shackle: arc over the top.
        cx, body_top = s / 2, s * 0.44
        r = s * 0.20
        p.drawArc(int(cx - r), int(body_top - r), int(2 * r), int(2 * r), 0, 180 * 16)
        # Body: rounded rect.
        p.setBrush(QColor("#1E1E1E"))
        bw, bh = s * 0.52, s * 0.40
        p.drawRoundedRect(int(cx - bw / 2), int(body_top), int(bw), int(bh), 4, 4)
        p.end()


class SectionTitle(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #999999;"
            "margin-left: 10px; margin-top: 6px; margin-bottom: 2px;"
        )


class _SettingsCard(QFrame):
    """Card holding 44px rows with hairline dividers (Glance metrics)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background: {_ROW_FILL}; border: 1px solid {_ROW_BORDER};"
            " border-radius: 16px; }"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def add_row(self, row: QWidget) -> None:
        if self._layout.count() > 0:
            div = QFrame()
            div.setFixedHeight(1)
            div.setStyleSheet(f"background: {_ROW_BORDER}; margin-left: 16px;")
            self._layout.addWidget(div)
        self._layout.addWidget(row)


class _Row(QWidget):
    """44px settings row: title (+subtitle) left, control right."""

    def __init__(self, title: str, subtitle: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)
        name = QLabel(title)
        name.setStyleSheet("font-size: 13px; color: #EEEEEE; border: none;")
        text_col.addWidget(name)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet("font-size: 12px; color: #999999; border: none;")
            text_col.addWidget(sub)
            text_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(text_col, 1)
        self._right = QHBoxLayout()
        self._right.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._right)

    def add_control(self, w: QWidget) -> None:
        self._right.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)


def _make_toggle(settings: SettingsStore, key: str,
                 on_change=None) -> Toggle:
    t = Toggle()
    t.setChecked(bool(settings.get(key)))

    def _on(state: bool) -> None:
        settings.set(key, bool(state))
        if on_change:
            on_change(bool(state))

    t.toggled.connect(_on)
    return t


class TriggerTile(QPushButton):
    """70x44 selectable trigger tile with painted glyph + label."""

    def __init__(self, key: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._key = key
        self._label = label
        self.setFixedSize(70, 44)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def trigger_key(self) -> str:
        return self._key

    def _glyph(self, p: QPainter, cx: float, cy: float) -> None:
        p.setPen(QPen(QColor("white"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._key == "on_wake":
            # Crescent moon: circle minus offset circle.
            p.setBrush(QColor("white"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(cx - 7), int(cy - 7), 14, 14)
            p.setBrush(QColor("#232326"))
            p.drawEllipse(int(cx - 3), int(cy - 9), 12, 12)
        elif self._key == "on_lock":
            # Laptop: screen + base.
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("white"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawRoundedRect(int(cx - 9), int(cy - 7), 18, 11, 2, 2)
            p.drawLine(int(cx - 12), int(cy + 7), int(cx + 12), int(cy + 7))
        else:
            # Space bar: wide rounded bar.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("white"))
            p.drawRoundedRect(int(cx - 12), int(cy - 4), 24, 8, 4, 4)

    def paintEvent(self, event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.isChecked()
        # Fill.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(52, 153, 255, int(255 * 0.12)) if selected
                   else QColor(255, 255, 255, int(255 * 0.05)))
        p.drawRoundedRect(0, 0, w, h, 13, 13)
        # Selection ring: 3.5px stroke, 2.5px outset.
        if selected:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ACCENT, 3.5, Qt.PenStyle.SolidLine))
            m = 2.5 + 3.5 / 2
            p.drawRoundedRect(int(m), int(m), int(w - 2 * m), int(h - 2 * m), 11, 11)
        # Glyph (16px cell) + label (12px medium) beneath.
        self._glyph(p, w / 2, 13)
        p.setPen(QColor("white"))
        font = p.font()
        font.setPixelSize(12)
        font.setWeight(500)
        p.setFont(font)
        p.drawText(0, 24, w, h - 24, Qt.AlignmentFlag.AlignHCenter,
                   self._label)
        p.end()


class StyleTile(QPushButton):
    """112x76 unlock-animation style tile with a drawn preview."""

    def __init__(self, key: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._key = key
        self._label = label
        self.setFixedSize(112, 76)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def style_key(self) -> str:
        return self._key

    def _draw_lock_glyph(self, p: QPainter, x: int, y: int, s: int) -> None:
        p.setPen(QPen(QColor("white"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(x + 2, y, s - 4, s - 2, 0, 180 * 16)
        p.setBrush(QColor("#2A2A2E"))
        p.drawRoundedRect(x, y + s // 2, s, s // 2 + 2, 2, 2)

    def paintEvent(self, event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(52, 153, 255, int(255 * 0.12)) if selected
                   else QColor(255, 255, 255, int(255 * 0.05)))
        p.drawRoundedRect(0, 0, w, h, 13, 13)
        if selected:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ACCENT, 3.5, Qt.PenStyle.SolidLine))
            m = 2.5 + 3.5 / 2
            p.drawRoundedRect(int(m), int(m), int(w - 2 * m), int(h - 2 * m), 11, 11)
        # Preview area.
        if self._key == "minimal":
            # Black capsule: lock glyph + small square video box.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("black"))
            p.drawRoundedRect(14, 8, w - 28, 34, 17, 17)
            self._draw_lock_glyph(p, 24, 16, 12)
            p.setBrush(QColor("#3A3A40"))
            p.drawRoundedRect(w - 44, 15, 20, 20, 3, 3)
        else:
            # Rounded rect with video still.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("black"))
            p.drawRoundedRect(30, 6, w - 60, 38, 12, 12)
            p.setBrush(QColor("#3A3A40"))
            p.drawRoundedRect(36, 12, w - 72, 26, 6, 6)
        # Label.
        p.setPen(QColor("white"))
        font = p.font()
        font.setPixelSize(12)
        font.setWeight(500)
        p.setFont(font)
        p.drawText(0, 48, w, 24, Qt.AlignmentFlag.AlignHCenter, self._label)
        p.end()


class GeneralPage(QWidget):
    """Glance-ditto General settings page."""

    def __init__(self, bridge: EngineBridge, settings: SettingsStore,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.bridge = bridge
        self.settings = settings
        self._toast = Toast(self)

        self._stack = QStackedWidget(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)

        self._stack.addWidget(self._build_locked_state())
        self._stack.addWidget(self._build_content())
        self.refresh()

    # -- locked empty state -------------------------------------------------
    def _build_locked_state(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(12)
        icon = LockIcon(34)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        title = QLabel("Session locked")
        title.setStyleSheet("font-size: 15px; font-weight: 600; color: #EEEEEE;")
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        btn = QPushButton("Unlock session")
        btn.setObjectName("primary")
        btn.setFixedWidth(180)
        btn.clicked.connect(self._unlock_session)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        return page

    def _unlock_session(self) -> None:
        win = self.window()
        unlock = getattr(win, "unlock_session", None)
        if callable(unlock):
            unlock()
            QTimer.singleShot(400, self.refresh)
        else:
            self._toast.show_message("Session unlock is not available here.")

    # -- content ------------------------------------------------------------
    def _build_content(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(16, 12, 16, 84)
        lay.setSpacing(12)

        lay.addWidget(self._build_card1())
        lay.addWidget(SectionTitle("Behaviour"))
        lay.addWidget(self._build_behaviour_card())
        lay.addWidget(SectionTitle("Animation"))
        lay.addWidget(self._build_animation_card())
        lay.addWidget(SectionTitle("Lock screen"))
        lay.addWidget(self._build_lockscreen_card())
        lay.addStretch(1)

        scroll.setWidget(inner)
        return scroll

    def _build_card1(self) -> _SettingsCard:
        card = _SettingsCard()

        r = _Row("Launch at login")
        self._launch_toggle = _make_toggle(self.settings, "launch_at_login")
        r.add_control(self._launch_toggle)
        card.add_row(r)

        r = _Row("Show in system tray")
        self._tray_toggle = _make_toggle(self.settings, "show_in_tray")
        r.add_control(self._tray_toggle)
        card.add_row(r)

        r = _Row("Enable Face Unlock")
        self._face_toggle = _make_toggle(
            self.settings, "face_unlock_enabled", self._on_face_unlock_changed)
        r.add_control(self._face_toggle)
        card.add_row(r)

        # Triggers label row + tiles.
        r = _Row("Triggers", "Select multiple")
        card.add_row(r)
        tiles_wrap = QWidget()
        tiles_wrap.setFixedHeight(44)
        tiles_lay = QHBoxLayout(tiles_wrap)
        tiles_lay.setContentsMargins(16, 0, 16, 0)
        tiles_lay.setSpacing(8)
        tiles_lay.addStretch(1)
        self._trigger_tiles: dict[str, TriggerTile] = {}
        for key, label in TRIGGERS:
            tile = TriggerTile(key, label)
            tile.clicked.connect(lambda _c, k=key: self._on_trigger_tile(k))
            self._trigger_tiles[key] = tile
            tiles_lay.addWidget(tile)
        tiles_lay.addStretch(1)
        card.add_row(tiles_wrap)
        self._triggers_dim = QGraphicsOpacityEffect(tiles_wrap)
        tiles_wrap.setGraphicsEffect(self._triggers_dim)

        # Display picker.
        r = _Row("Display on")
        self._display_combo = QComboBox()
        self._display_combo.addItem("Main display", "main")
        self._display_combo.setStyleSheet(
            "QComboBox { background: rgba(255,255,255,0.14); color: #EEEEEE;"
            " border: 1px solid rgba(255,255,255,0.12); border-radius: 10px;"
            " padding: 6px 12px; font-size: 13px; }"
            "QComboBox QAbstractItemView { background: #1E1E1E; color: #EEEEEE;"
            " selection-background-color: #3499FF; }"
        )
        self._display_combo.currentIndexChanged.connect(self._on_display_changed)
        r.add_control(self._display_combo)
        card.add_row(r)

        self._sync_triggers_ui()
        return card

    def _build_behaviour_card(self) -> _SettingsCard:
        card = _SettingsCard()

        r = _Row("Retry on hover")
        self._retry_toggle = _make_toggle(self.settings, "retry_on_hover")
        r.add_control(self._retry_toggle)
        card.add_row(r)

        r = _Row("Auto retry once after failure")
        self._autoretry_toggle = _make_toggle(self.settings, "auto_retry_once")
        r.add_control(self._autoretry_toggle)
        card.add_row(r)

        # Stepped slider row.
        r = _Row("Face detection duration")
        self._duration_value = QLabel()
        self._duration_value.setStyleSheet(
            "font-size: 13px; font-weight: 700; color: #EEEEEE;")
        r.add_control(self._duration_value)
        card.add_row(r)
        slider_wrap = QWidget()
        slider_wrap.setFixedHeight(44)
        sl = QHBoxLayout(slider_wrap)
        sl.setContentsMargins(16, 0, 16, 0)
        self._duration_slider = QSlider(Qt.Orientation.Horizontal)
        self._duration_slider.setRange(3, 10)
        self._duration_slider.setSingleStep(1)
        self._duration_slider.setPageStep(1)
        self._duration_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._duration_slider.setTickInterval(1)
        self._duration_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #3A3A40;"
            " border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 18px; height: 18px;"
            " margin: -7px 0; background: white; border-radius: 9px; }"
            "QSlider::sub-page:horizontal { background: #3499FF;"
            " border-radius: 2px; }"
        )
        self._duration_slider.valueChanged.connect(self._on_duration_changed)
        sl.addWidget(self._duration_slider)
        card.add_row(slider_wrap)
        return card

    def _build_animation_card(self) -> _SettingsCard:
        card = _SettingsCard()

        r = _Row("Show animation")
        self._anim_toggle = _make_toggle(self.settings, "show_unlock_animation")
        r.add_control(self._anim_toggle)
        card.add_row(r)

        r = _Row("Style", "The animation that appears when unlocking your PC")
        card.add_row(r)
        tiles_wrap = QWidget()
        tiles_wrap.setFixedHeight(76)
        tiles_lay = QHBoxLayout(tiles_wrap)
        tiles_lay.setContentsMargins(16, 0, 16, 0)
        tiles_lay.setSpacing(12)
        tiles_lay.addStretch(1)
        self._style_tiles: dict[str, StyleTile] = {}
        for key, label in (("minimal", "Minimal"), ("original", "Original")):
            tile = StyleTile(key, label)
            tile.clicked.connect(lambda _c, k=key: self._on_style_tile(k))
            self._style_tiles[key] = tile
            tiles_lay.addWidget(tile)
        tiles_lay.addStretch(1)
        card.add_row(tiles_wrap)
        return card

    def _build_lockscreen_card(self) -> _SettingsCard:
        card = _SettingsCard()
        r = _Row("Lock-screen sign-in tile",
                 "Add Visage to the Windows sign-in screen")
        btn = QPushButton("Set up")
        btn.setObjectName("ghost")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._setup_cp_tile)
        r.add_control(btn)
        card.add_row(r)
        return card

    # -- behaviour ----------------------------------------------------------
    def _sanitized_triggers(self) -> list[str]:
        raw = self.settings.get("unlock_triggers")
        valid = [k for k, _ in TRIGGERS]
        if isinstance(raw, (list, tuple)):
            out = [t for t in raw if t in valid]
        else:
            out = []
        return out or ["on_wake"]

    def _sync_triggers_ui(self) -> None:
        selected = set(self._sanitized_triggers())
        for key, tile in self._trigger_tiles.items():
            tile.blockSignals(True)
            tile.setChecked(key in selected)
            tile.blockSignals(False)
        enabled = bool(self.settings.get("face_unlock_enabled"))
        self._triggers_dim.setOpacity(0.4 if not enabled else 1.0)
        for tile in self._trigger_tiles.values():
            tile.setEnabled(enabled)

    def _on_trigger_tile(self, key: str) -> None:
        current = self._sanitized_triggers()
        tile = self._trigger_tiles[key]
        if key in current:
            if len(current) == 1:
                # Deselecting the last trigger is a no-op.
                tile.blockSignals(True)
                tile.setChecked(True)
                tile.blockSignals(False)
                return
            current.remove(key)
        else:
            current.append(key)
        self.settings.set("unlock_triggers", current)
        self._sync_triggers_ui()

    def _on_face_unlock_changed(self, on: bool) -> None:
        # Turning face unlock off dims the trigger tiles immediately.
        self._sync_triggers_ui()

    def _on_display_changed(self, index: int) -> None:
        self.settings.set("selected_display",
                          self._display_combo.itemData(index) or "main")

    def _on_duration_changed(self, value: int) -> None:
        self._duration_value.setText(f"{value}s")
        self.settings.set("face_detection_seconds", int(value))

    def _on_style_tile(self, key: str) -> None:
        self.settings.set("unlock_animation_style", key)
        for k, tile in self._style_tiles.items():
            tile.blockSignals(True)
            tile.setChecked(k == key)
            tile.blockSignals(False)
        win = self.window()
        preview = getattr(win, "preview_unlock_style", None)
        if callable(preview):
            try:
                preview(key)
            except Exception:
                pass

    def _setup_cp_tile(self) -> None:
        try:
            from .. import cp
        except Exception:
            self._toast.show_message("See the installer to register the tile.")
            return
        try:
            if not cp.scripts_present():
                self._toast.show_message(
                    "See the installer to register the tile.")
                return
            result = cp.register()
            self._toast.show_message(str(result)[:160] if result
                                    else "Tile registration requested.")
        except Exception:
            self._toast.show_message("See the installer to register the tile.")

    # -- public -------------------------------------------------------------
    def _is_unlocked(self) -> bool:
        win = self.window()
        check = getattr(win, "is_session_unlocked", None)
        if callable(check):
            try:
                return bool(check())
            except Exception:
                return True
        return True

    def refresh(self) -> None:
        """Re-read settings into every control; show lock gate if needed."""
        self._stack.setCurrentIndex(0 if not self._is_unlocked() else 1)
        if not self._is_unlocked():
            return
        for toggle, key in (
            (self._launch_toggle, "launch_at_login"),
            (self._tray_toggle, "show_in_tray"),
            (self._face_toggle, "face_unlock_enabled"),
            (self._retry_toggle, "retry_on_hover"),
            (self._autoretry_toggle, "auto_retry_once"),
            (self._anim_toggle, "show_unlock_animation"),
        ):
            toggle.blockSignals(True)
            toggle.setChecked(bool(self.settings.get(key)))
            toggle.blockSignals(False)
        self._sync_triggers_ui()
        idx = self._display_combo.findData(self.settings.get("selected_display"))
        self._display_combo.blockSignals(True)
        self._display_combo.setCurrentIndex(max(0, idx))
        self._display_combo.blockSignals(False)
        secs = self.settings.get("face_detection_seconds")
        secs = secs if isinstance(secs, int) and 3 <= secs <= 10 else 5
        self._duration_slider.blockSignals(True)
        self._duration_slider.setValue(secs)
        self._duration_slider.blockSignals(False)
        self._duration_value.setText(f"{secs}s")
        style = self.settings.get("unlock_animation_style")
        if style not in ("minimal", "original"):
            style = "minimal"
        for k, tile in self._style_tiles.items():
            tile.blockSignals(True)
            tile.setChecked(k == style)
            tile.blockSignals(False)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()
