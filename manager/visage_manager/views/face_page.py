"""Your Face settings page: enrollment management.

Mirrors Glance's YourFaceSettingsPage: four mutually exclusive states
(locked / unreadable / not enrolled / enrolled), a "Face encrypted" explainer
card, per-identity cards with name pill, enable toggle, capture-quality
caption, quality tick strip, Recapture and delete actions.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..engine_bridge import EngineBridge, Identity
from ..errors import StoreError, humanize, log_exception
from ..widgets import Card, Toggle, Toast

# --- Glance design tokens (unlock-settings.md section 10) ----------------------
_ACCENT = "#3499FF"
_SURFACE = "#1E1E1E"
_SURFACE_RAISED = "#323232"
_TEXT = "#FFFFFF"
_TEXT_SECONDARY = "#949494"
_TEXT_DETAIL = "#BDBDBD"
_TEXT_TERTIARY = "#999999"
_ROW_FILL = "rgba(255,255,255,0.08)"
_ROW_BORDER = "rgba(204,204,204,0.12)"
_NEUTRAL_FILL = "rgba(255,255,255,0.14)"
_DESTRUCTIVE = "#E03B2F"
_Q_POOR = "#FF5454"
_Q_FAIR = "#FFBE54"
_Q_GOOD = "#85FF77"
_Q_UNRATED = "rgba(255,255,255,0.22)"
_AMBER = "#FFBE36"

# Engine's current recognition model; identities captured under anything else
# get the stale-model warning.
_CURRENT_MODEL_ID = "arcface-w600k_r50-v1"

_TICK_W = 3.5
_TICK_GAP = 5.5
_TICK_H = 26.0
_TICK_STRIP_MAX_W = 250.0


def _quality_tier(quality) -> str:
    """Mirror Glance's FaceSample.qualityTier thresholds (engine/recognition.py)."""
    if quality is None:
        return "unrated"
    if quality < 0.4:
        return "poor"
    if quality < 0.5:
        return "fair"
    return "good"


_TIER_COLORS = {
    "poor": _Q_POOR,
    "fair": _Q_FAIR,
    "good": _Q_GOOD,
    "unrated": _Q_UNRATED,
}


def _quality_caption(ident: Identity) -> str:
    samples = ident.samples or []
    if not samples:
        return "No samples captured"
    tiers = [_quality_tier(s.quality) for s in samples]
    if all(t == "unrated" for t in tiers):
        return "Capture quality \u2022 not recorded"
    good_share = sum(1 for t in tiers if t != "poor") / len(tiers)
    return f"Capture quality \u2022 {round(good_share * 100)}%"


class QualityTickStrip(QWidget):
    """One capsule tick per sample, in capture order (Glance spec section 9)."""

    def __init__(self, samples, parent: QWidget | None = None):
        super().__init__(parent)
        self._tiers = [_quality_tier(s.quality) for s in samples]
        n = len(self._tiers)
        tick_w = _TICK_W
        if n:
            total = n * _TICK_W + (n - 1) * _TICK_GAP
            if total > _TICK_STRIP_MAX_W:
                tick_w = max(1.5, (_TICK_STRIP_MAX_W - (n - 1) * _TICK_GAP) / n)
        self._tick_w = tick_w
        width = int(min(_TICK_STRIP_MAX_W, n * tick_w + max(0, n - 1) * _TICK_GAP)) if n else 0
        self.setFixedSize(max(width, 1), int(_TICK_H))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        x = 0.0
        for tier in self._tiers:
            p.setBrush(QColor(_TIER_COLORS[tier]))
            p.drawRoundedRect(
                int(round(x)), 0, int(round(self._tick_w)), int(_TICK_H),
                self._tick_w / 2.0, self._tick_w / 2.0,
            )
            x += self._tick_w + _TICK_GAP
        p.end()


class NamePill(QLabel):
    """Capsule name pill: 14/7px padding, neutral fill + border."""

    def __init__(self, name: str, parent: QWidget | None = None):
        super().__init__(name, parent)
        self.setStyleSheet(
            f"background: {_NEUTRAL_FILL}; color: {_TEXT};"
            " border: 1px solid rgba(204,204,204,0.12); border-radius: 12px;"
            " padding: 7px 14px; font-size: 13px; font-weight: 500;"
        )
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


class FaceGlyph(QWidget):
    """Simple vector face icon for the not-enrolled empty state."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(64, 64)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(_TEXT_SECONDARY), 2.5)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(6, 6, 52, 52)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_TEXT_SECONDARY))
        p.drawEllipse(24, 24, 5, 5)
        p.drawEllipse(36, 24, 5, 5)
        # subtle smile arc
        arc_pen = QPen(QColor(_TEXT_SECONDARY), 2.0)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc_pen)
        path = QPainterPath()
        path.moveTo(24, 40)
        path.quadTo(32, 47, 40, 40)
        p.drawPath(path)
        p.end()


class WarnGlyph(QWidget):
    """Amber warning triangle with an exclamation mark (unreadable state)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(40, 36)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.moveTo(20, 2)
        path.lineTo(38, 34)
        path.lineTo(2, 34)
        path.closeSubpath()
        p.setPen(QPen(QColor(_AMBER), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_AMBER))
        p.drawRoundedRect(18, 12, 4, 10, 2, 2)
        p.drawEllipse(18, 25, 4, 4)
        p.end()


class TrashButton(QPushButton):
    """Circular 28px trash button with a painted (non-emoji) trash glyph."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Delete this enrolled face")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # circular neutral button
        bg = QColor(_NEUTRAL_FILL)
        if self.underMouse():
            bg = QColor(255, 255, 255, 51)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawEllipse(0, 0, 28, 28)
        # trash can glyph
        pen = QPen(QColor(_TEXT_SECONDARY), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(9, 8, 19, 8)          # lid
        p.drawLine(12, 8, 12, 6)
        p.drawLine(16, 8, 16, 6)
        p.drawLine(12, 6, 16, 6)        # handle
        p.drawRoundedRect(10, 10, 8, 11, 1.5, 1.5)  # body
        p.drawLine(13, 13, 13, 18)
        p.drawLine(15, 13, 15, 18)
        p.end()


class IdentityCard(Card):
    """Per-identity card: name pill + toggle, quality, tick strip, recapture, delete."""

    delete_requested = pyqtSignal(str, str)      # identity_id, name
    recapture_requested = pyqtSignal(str, str)   # identity_id, name
    toggled = pyqtSignal(str, bool)              # identity_id, enabled

    def __init__(self, ident: Identity, parent: QWidget | None = None):
        super().__init__(parent)
        self._ident = ident
        lay = self.layout()
        lay.setSpacing(10)

        # Line 1: name pill + enable toggle.
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(NamePill(ident.name))
        top.addStretch(1)
        self._toggle = Toggle()
        self._toggle.setChecked(bool(ident.is_enabled))
        self._toggle.toggled.connect(
            lambda on: self.toggled.emit(ident.id, bool(on))
        )
        top.addWidget(self._toggle)
        lay.addLayout(top)

        # Line 2: capture-quality caption.
        caption = QLabel(_quality_caption(ident))
        caption.setStyleSheet(f"color: {_TEXT_SECONDARY}; font-size: 12px;")
        lay.addWidget(caption)

        # Quality tick strip.
        if ident.samples:
            lay.addWidget(QualityTickStrip(ident.samples))

        # Stale-model warning (amber, 11pt).
        if ident.model_identifier and ident.model_identifier != _CURRENT_MODEL_ID:
            warn = QLabel(
                "Captured with a different recognition model \u2014 "
                "recapture before this face can unlock your PC."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {_AMBER}; font-size: 11px;")
            lay.addWidget(warn)

        # Actions: Recapture pill + trash.
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self._recapture_btn = QPushButton("Recapture")
        self._recapture_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recapture_btn.setStyleSheet(
            f"background: {_NEUTRAL_FILL}; color: {_TEXT}; border: none;"
            " border-radius: 13px; padding: 6px 14px;"
            " font-size: 12px; font-weight: 500;"
        )
        self._recapture_btn.clicked.connect(
            lambda: self.recapture_requested.emit(ident.id, ident.name)
        )
        actions.addWidget(self._recapture_btn)
        actions.addStretch(1)
        self._trash_btn = TrashButton()
        self._trash_btn.clicked.connect(
            lambda: self.delete_requested.emit(ident.id, ident.name)
        )
        actions.addWidget(self._trash_btn)
        lay.addLayout(actions)

        # Disabled identities dim to 0.45 (still enrolled, just not matched).
        if not ident.is_enabled:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.45)
            self.setGraphicsEffect(effect)

    def set_actions_enabled(self, enabled: bool) -> None:
        self._recapture_btn.setEnabled(enabled)
        self._trash_btn.setEnabled(enabled)


class FacePage(QWidget):
    """Your Face settings page (Glance-ditto)."""

    enroll_requested = pyqtSignal()
    recapture_requested = pyqtSignal(str, str)  # identity_id, name

    def __init__(self, bridge: EngineBridge, settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.bridge = bridge
        self.settings = settings
        self._enrolling = False
        self._toast: Toast | None = None
        self._enroll_buttons: list[QPushButton] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._locked_page = self._build_locked_page()
        self._unreadable_page = self._build_unreadable_page()
        self._not_enrolled_page = self._build_not_enrolled_page()
        self._enrolled_page = self._build_enrolled_page()

        for page in (
            self._locked_page,
            self._unreadable_page,
            self._not_enrolled_page,
            self._enrolled_page,
        ):
            self._stack.addWidget(page)

    # -- empty-state builders --------------------------------------------------
    def _empty_wrap(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(24, 48, 24, 48)
        outer.addStretch(1)
        box = QVBoxLayout()
        box.setSpacing(12)
        box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(box)
        outer.addStretch(1)
        return page, box

    def _build_locked_page(self) -> QWidget:
        page, box = self._empty_wrap()
        glyph = QLabel("\u26bf")  # squared-key lock glyph (text, not emoji)
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setStyleSheet(f"color: {_TEXT_SECONDARY}; font-size: 34px;")
        box.addWidget(glyph)
        title = QLabel("Session locked")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {_TEXT}; font-size: 15px; font-weight: 600;")
        box.addWidget(title)
        btn = QPushButton("Unlock session")
        btn.setObjectName("primary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._on_unlock_session)
        box.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        return page

    def _build_unreadable_page(self) -> QWidget:
        page, box = self._empty_wrap()
        box.addWidget(WarnGlyph(), alignment=Qt.AlignmentFlag.AlignHCenter)
        title = QLabel("Enrolled faces couldn't be read")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {_TEXT}; font-size: 15px; font-weight: 600;")
        box.addWidget(title)
        cap = QLabel("The stored data couldn't be decrypted with this session key.")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setWordWrap(True)
        cap.setStyleSheet(f"color: {_TEXT_SECONDARY}; font-size: 12px;")
        box.addWidget(cap)
        long_cap = QLabel(
            "Nothing has been deleted, and Visage will not overwrite it \u2014 "
            "enrolling is blocked until this resolves. Quit and reopen Visage "
            "to retry. If it keeps failing, the session key no longer matches "
            "this data: remove the stored password on the Password tab to clear "
            "both, then set up again."
        )
        long_cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        long_cap.setWordWrap(True)
        long_cap.setStyleSheet(f"color: {_TEXT_TERTIARY}; font-size: 12px;")
        box.addWidget(long_cap)
        return page

    def _build_not_enrolled_page(self) -> QWidget:
        page, box = self._empty_wrap()
        box.addWidget(FaceGlyph(), alignment=Qt.AlignmentFlag.AlignHCenter)
        title = QLabel("Face enrollment")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {_TEXT}; font-size: 15px; font-weight: 600;")
        box.addWidget(title)
        self._setup_btn = QPushButton("Set up Face Unlock")
        self._setup_btn.setObjectName("primary")
        self._setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_btn.clicked.connect(self.enroll_requested.emit)
        self._enroll_buttons.append(self._setup_btn)
        box.addWidget(self._setup_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        return page

    def _build_enrolled_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self._enrolled_layout = QVBoxLayout(content)
        self._enrolled_layout.setContentsMargins(16, 12, 16, 24)
        self._enrolled_layout.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    # -- enrolled content ------------------------------------------------------
    def _rebuild_enrolled(self, idents: list[Identity]) -> None:
        lay = self._enrolled_layout
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
        self._enroll_buttons = [
            b for b in self._enroll_buttons if b is self._setup_btn
        ]

        # "Face encrypted" card.
        card = Card()
        card_lay = card.layout()
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Face encrypted")
        title.setStyleSheet(f"color: {_TEXT}; font-size: 13px; font-weight: 600;")
        header.addWidget(title)
        lock = QLabel("\u26bf")
        lock.setStyleSheet(f"color: {_TEXT_SECONDARY}; font-size: 12px;")
        header.addWidget(lock)
        header.addStretch(1)
        card_lay.addLayout(header)
        body = QLabel(
            "Enroll separate identities to use Visage with multiple people, "
            "accessories (ex. glasses), facial expressions, or new lighting "
            "environments. This improves recognition quality."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {_TEXT_DETAIL}; font-size: 12px;")
        card_lay.addWidget(body)
        lay.addWidget(card)

        # "Identities" section header + add button.
        sec = QHBoxLayout()
        sec.setContentsMargins(2, 4, 2, 0)
        sec_title = QLabel("Identities")
        sec_title.setStyleSheet(
            f"color: {_TEXT_TERTIARY}; font-size: 13px; font-weight: 500;"
        )
        sec.addWidget(sec_title)
        sec.addStretch(1)
        add_btn = QPushButton("+")
        add_btn.setToolTip("Enroll another face")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setFixedSize(28, 28)
        add_btn.setStyleSheet(
            f"background: {_NEUTRAL_FILL}; color: {_TEXT}; border: none;"
            " border-radius: 14px; font-size: 16px; font-weight: 600; padding: 0px;"
        )
        add_btn.clicked.connect(self.enroll_requested.emit)
        self._enroll_buttons.append(add_btn)
        sec.addWidget(add_btn)
        lay.addLayout(sec)

        # Per-identity cards.
        self._identity_cards: list[IdentityCard] = []
        for ident in idents:
            icard = IdentityCard(ident)
            icard.toggled.connect(self._on_toggle)
            icard.recapture_requested.connect(self.recapture_requested.emit)
            icard.delete_requested.connect(self._on_delete_requested)
            self._identity_cards.append(icard)
            lay.addWidget(icard)

        # None-enabled notice.
        if idents and not any(i.is_enabled for i in idents):
            note = QLabel(
                "No identities are enabled \u2014 face unlock won't recognize "
                "anyone until you switch one back on."
            )
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {_TEXT_SECONDARY}; font-size: 12px;")
            lay.addWidget(note)

        lay.addStretch(1)
        self._sync_enroll_buttons()

    @staticmethod
    def _clear_layout(lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                FacePage._clear_layout(item.layout())

    # -- state -----------------------------------------------------------------
    def _session_locked(self) -> bool:
        win = self.window()
        check = getattr(win, "is_session_unlocked", None)
        if callable(check):
            try:
                return not bool(check())
            except Exception:
                return False
        return False

    def _switch(self, page: QWidget) -> None:
        if self._stack.currentWidget() is page:
            return
        self._stack.setCurrentWidget(page)
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", page)
        anim.setDuration(280)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def refresh(self) -> None:
        """Re-read identities from the bridge and show the right state."""
        if self._session_locked():
            self._switch(self._locked_page)
            return
        try:
            idents = self.bridge.list_identities()
        except StoreError as exc:
            log_exception(exc, "face page list identities")
            self._switch(self._unreadable_page)
            return
        except Exception as exc:  # noqa: BLE001 - never break the settings page
            log_exception(exc, "face page list identities")
            self._toast_message(humanize(exc))
            return
        if not idents:
            self._switch(self._not_enrolled_page)
        else:
            self._rebuild_enrolled(idents)
            self._switch(self._enrolled_page)
        self._sync_enroll_buttons()

    def set_enrolling(self, enrolling: bool) -> None:
        """Block new enrollment entry points while a flow is running."""
        self._enrolling = bool(enrolling)
        self._sync_enroll_buttons()
        for card in getattr(self, "_identity_cards", []):
            card.set_actions_enabled(not self._enrolling)

    def _sync_enroll_buttons(self) -> None:
        for btn in self._enroll_buttons:
            try:
                btn.setEnabled(not self._enrolling)
            except RuntimeError:
                pass  # button was deleted on rebuild

    # -- actions ---------------------------------------------------------------
    def _on_unlock_session(self) -> None:
        win = self.window()
        unlock = getattr(win, "unlock_session", None)
        if callable(unlock):
            try:
                unlock()
            except Exception as exc:  # noqa: BLE001
                log_exception(exc, "face page unlock session")
                self._toast_message(humanize(exc))
                return
            QTimer.singleShot(600, self.refresh)
        else:
            self.refresh()

    def _on_toggle(self, identity_id: str, enabled: bool) -> None:
        try:
            self.bridge.set_identity_enabled(identity_id, enabled)
        except Exception as exc:  # noqa: BLE001
            log_exception(exc, "face page toggle identity")
            self._toast_message(humanize(exc))
        self.refresh()

    def _on_delete_requested(self, identity_id: str, name: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Delete this enrolled face?")
        box.setText(f'"{name}" will stop being recognized until you enroll them again.')
        delete_btn = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(delete_btn)
        box.exec()
        if box.clickedButton() is not delete_btn:
            return
        try:
            self.bridge.delete_identity(identity_id)
        except Exception as exc:  # noqa: BLE001
            log_exception(exc, "face page delete identity")
            self._toast_message(humanize(exc))
            return
        self.refresh()

    def _toast_message(self, text: str) -> None:
        win = self.window()
        if win is None:
            return
        if self._toast is None:
            self._toast = Toast(win)
        self._toast.show_message(text)
