"""Home page: status at a glance + quick actions."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme
from ..engine_bridge import EngineBridge
from ..settings_store import SettingsStore
from ..widgets import Card, RowCard, Toggle


class HomePage(QWidget):
    enroll_requested = pyqtSignal()
    open_identities = pyqtSignal()
    open_settings = pyqtSignal()
    open_password = pyqtSignal()
    open_cp = pyqtSignal()

    def __init__(self, bridge: EngineBridge, settings: SettingsStore, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_PAGE, 32, theme.PAD_PAGE, theme.PAD_PAGE)
        layout.setSpacing(theme.GAP)

        title = QLabel("Home")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        sub = QLabel("Everything Visage needs to unlock your PC with just your face.")
        sub.setObjectName("pageSubtitle")
        layout.addWidget(sub)
        layout.addSpacing(8)

        self._status_card = Card()
        layout.addWidget(self._status_card)

        actions = Card()
        al = actions.layout()
        head = QLabel("Get started")
        head.setObjectName("sectionTitle")
        al.addWidget(head)
        for label, signal in [
            ("Enroll a face", self.enroll_requested),
            ("Manage identities", self.open_identities),
            ("Set your Windows password", self.open_password),
            ("Lock-screen tile", self.open_cp),
        ]:
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(signal.emit)
            al.addWidget(btn)
        layout.addWidget(actions)
        layout.addStretch(1)

        self.refresh()

    def refresh(self) -> None:
        # rebuild status card
        while self._status_card.layout().count():
            item = self._status_card.layout().takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        lay = self._status_card.layout()
        head = QLabel("Status")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        try:
            identities = self.bridge.list_identities()
            n_enabled = sum(1 for i in identities if i.is_enabled)
        except Exception:
            identities, n_enabled = [], 0
        has_pw = self.bridge.has_credentials()

        rows = [
            ("Face unlock", "On" if self.settings.get("face_unlock_enabled") else "Off",
             bool(self.settings.get("face_unlock_enabled"))),
            ("Enrolled faces", f"{n_enabled} active" + (f" ({len(identities)} total)" if len(identities) != n_enabled else ""),
             n_enabled > 0),
            ("Windows password", "Stored securely" if has_pw else "Not set",
             has_pw),
            ("Liveness", {"off": "Off", "light": "Light", "heavy": "Heavy"}[self.settings.liveness_mode],
             self.settings.liveness_mode != "off"),
            ("Engine", "Connected" if self.bridge.using_real_engine else "Simulated (engine not installed)",
             self.bridge.using_real_engine),
        ]
        for name, value, ok in rows:
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {theme.SUCCESS if ok else theme.WARNING}; font-size: 10px;")
            row.addWidget(dot)
            name_l = QLabel(name)
            name_l.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: {theme.BODY_SIZE}px;")
            row.addWidget(name_l)
            row.addStretch(1)
            val_l = QLabel(value)
            val_l.setStyleSheet(f"font-size: {theme.BODY_SIZE}px;")
            row.addWidget(val_l)
            lay.addLayout(row)

        if not identities or not has_pw:
            hint = QLabel()
            missing = []
            if not identities:
                missing.append("enroll your face")
            if not has_pw:
                missing.append("store your Windows password")
            hint.setText("Next step: " + " and ".join(missing) + ".")
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            lay.addWidget(hint)
