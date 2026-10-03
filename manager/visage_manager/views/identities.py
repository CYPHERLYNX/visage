"""Identities page: list, enable/disable, recapture, delete.

Mirrors Glance's YourFaceSettingsPage: several people, or several versions
of yourself (glasses, beard, lighting). Toggling off keeps the enrollment
intact but excludes it from unlock scoring; delete removes it for good.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .. import theme
from ..engine_bridge import EngineBridge, Identity
from ..errors import humanize, log_exception
from ..widgets import AvatarLabel, Card, RowCard, Toggle, Toast


def _quality_text(ident: Identity) -> str:
    q = ident.average_quality
    if q is None:
        return "quality not rated"
    if q < 0.4:
        return f"quality poor ({q:.0%})"
    if q < 0.5:
        return f"quality fair ({q:.0%})"
    return f"quality good ({q:.0%})"


class IdentityRow(RowCard):
    toggled = pyqtSignal(str, bool)
    delete_requested = pyqtSignal(str)
    recapture_requested = pyqtSignal(str, str)

    def __init__(self, ident: Identity, parent=None):
        super().__init__(parent)
        self._ident = ident
        lay = self.layout()

        lay.addWidget(AvatarLabel(ident.name))

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(ident.name)
        name.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {theme.TEXT};")
        info.addWidget(name)
        meta = QLabel(
            f"{ident.sample_count} samples · {len(ident.poses_covered)} of 9 directions · "
            f"{_quality_text(ident)}"
        )
        meta.setObjectName("caption")
        info.addWidget(meta)
        created = ident.created_at
        if isinstance(created, datetime):
            when = QLabel("Enrolled " + created.strftime("%d %b %Y"))
        else:
            when = QLabel("")
        when.setObjectName("hint")
        info.addWidget(when)
        lay.addLayout(info, stretch=1)

        if not ident.is_enabled:
            off = QLabel("Disabled")
            off.setStyleSheet(f"color: {theme.TEXT_TERTIARY}; font-size: {theme.CAPTION_SIZE}px;")
            lay.addWidget(off)

        recapture = QPushButton("Recapture")
        recapture.setObjectName("ghost")
        recapture.setCursor(Qt.CursorShape.PointingHandCursor)
        recapture.clicked.connect(lambda: self.recapture_requested.emit(ident.id, ident.name))
        lay.addWidget(recapture)

        toggle = Toggle()
        toggle.setChecked(ident.is_enabled)
        toggle.setToolTip("Turn off to exclude from unlock without deleting")
        toggle.toggled.connect(lambda on: self.toggled.emit(ident.id, on))
        lay.addWidget(toggle)

        delete = QPushButton("Delete")
        delete.setObjectName("danger")
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.clicked.connect(lambda: self.delete_requested.emit(ident.id))
        lay.addWidget(delete)


class IdentitiesPage(QWidget):
    enroll_requested = pyqtSignal()
    recapture_requested = pyqtSignal(str, str)  # id, name

    def __init__(self, bridge: EngineBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_PAGE, 32, theme.PAD_PAGE, theme.PAD_PAGE)
        layout.setSpacing(theme.GAP)

        head = QHBoxLayout()
        title = QLabel("Identities")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addStretch(1)
        add_btn = QPushButton("Enroll new face")
        add_btn.setObjectName("primary")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self.enroll_requested.emit)
        head.addWidget(add_btn)
        layout.addLayout(head)

        sub = QLabel(
            "Enroll several people, or several versions of yourself — with glasses, "
            "a beard, or in different lighting. Turning one off keeps it saved but "
            "excludes it from unlock."
        )
        sub.setObjectName("pageSubtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(4)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setSpacing(theme.GAP_SMALL)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        layout.addWidget(self._scroll, stretch=1)

        self._toast = Toast(self)
        self.refresh()

    def refresh(self) -> None:
        while self._list_layout.count() > 1:  # keep the stretch
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            identities = self.bridge.list_identities()
        except Exception as exc:
            log_exception(exc, "list identities")
            err = QLabel(humanize(exc))
            err.setWordWrap(True)
            err.setStyleSheet(f"color: {theme.DANGER};")
            self._list_layout.insertWidget(0, err)
            return

        if not identities:
            empty = Card()
            el = empty.layout()
            msg = QLabel("No faces enrolled yet.")
            msg.setStyleSheet(f"font-size: 15px; font-weight: 600;")
            el.addWidget(msg)
            hint = QLabel("Enroll your face to let Visage recognize you at the lock screen.")
            hint.setObjectName("caption")
            hint.setWordWrap(True)
            el.addWidget(hint)
            btn = QPushButton("Enroll your face")
            btn.setObjectName("primary")
            btn.clicked.connect(self.enroll_requested.emit)
            el.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
            self._list_layout.insertWidget(0, empty)
            return

        for ident in identities:
            row = IdentityRow(ident)
            row.toggled.connect(self._on_toggled)
            row.delete_requested.connect(self._on_delete)
            row.recapture_requested.connect(self.recapture_requested.emit)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    # -- actions -------------------------------------------------------------
    def _on_toggled(self, identity_id: str, enabled: bool) -> None:
        try:
            self.bridge.set_identity_enabled(identity_id, enabled)
        except Exception as exc:
            log_exception(exc, "toggle identity")
            self._toast.show_message(humanize(exc).split("\n\n")[0])
            self.refresh()

    def _on_delete(self, identity_id: str) -> None:
        names = {i.id: i.name for i in self.bridge.list_identities()}
        name = names.get(identity_id, "this face")
        box = QMessageBox(self)
        box.setWindowTitle("Delete identity")
        box.setText(f"Delete \"{name}\"?")
        box.setInformativeText("Its enrolled samples are removed immediately. This cannot be undone.")
        box.setStandardButtons(QMessageBox.StandardButton.Delete | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Delete:
            return
        try:
            self.bridge.delete_identity(identity_id)
            self._toast.show_message(f"Deleted \"{name}\".")
        except Exception as exc:
            log_exception(exc, "delete identity")
            self._toast.show_message(humanize(exc).split("\n\n")[0])
        self.refresh()
