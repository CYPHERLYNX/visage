"""About page. Click the logo 5 times in a row to reveal Face Lab.

Mirrors Glance's AboutSettingsPage: a pause over one second resets the
count, so it takes five *consecutive* clicks.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import theme
from ..widgets import Card, LogoMark

APP_VERSION = "0.1.0"

REQUIRED_CLICKS = 5
CLICK_RESET_SECONDS = 1.0


class AboutPage(QWidget):
    face_lab_unlocked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clicks = 0
        self._last_click = 0.0
        self._unlocked_announced = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(12)

        card = Card()
        lay = card.layout()
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._logo = LogoMark(88)
        self._logo.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._logo, alignment=Qt.AlignmentFlag.AlignCenter)

        name = QLabel("Visage")
        name.setStyleSheet(f"font-size: 24px; font-weight: 600; color: {theme.TEXT};")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(name)

        ver = QLabel(f"Version {APP_VERSION}")
        ver.setObjectName("caption")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ver)

        tag = QLabel("Face unlock for Windows — on-device, private by design.")
        tag.setObjectName("caption")
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setWordWrap(True)
        lay.addWidget(tag)

        self._secret_note = QLabel("")
        self._secret_note.setObjectName("hint")
        self._secret_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._secret_note)

        layout.addWidget(card)
        layout.addStretch(1)

        self._logo.mousePressEvent = self._on_logo_click  # noqa: SLF001

    def _on_logo_click(self, event) -> None:
        now = time.monotonic()
        if now - self._last_click > CLICK_RESET_SECONDS:
            self._clicks = 0
        self._last_click = now
        self._clicks += 1
        if self._clicks >= REQUIRED_CLICKS:
            self._clicks = 0
            if not self._unlocked_announced:
                self._unlocked_announced = True
                self._secret_note.setText("Face Lab unlocked — see the sidebar.")
            self.face_lab_unlocked.emit()
        event.accept()
