"""Camera settings page (Glance-ditto).

Camera picker + permission status, in Glance's settings row style.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .. import theme
from ..engine_bridge import EngineBridge
from ..errors import log_exception
from ..widgets import Card, Toast


class CameraPage(QWidget):
    def __init__(self, bridge: EngineBridge, settings, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(12)

        card = Card()
        layout.addWidget(card)
        lay = card.layout()

        head = QLabel("Camera")
        head.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {theme.TEXT};")
        lay.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("Selected camera"))
        self._picker = QComboBox()
        self._picker.setMinimumWidth(200)
        self._picker.currentIndexChanged.connect(self._on_pick)
        row.addWidget(self._picker, stretch=1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh)
        row.addWidget(self._refresh_btn)
        lay.addLayout(row)

        self._status = QLabel("")
        self._status.setObjectName("hint")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        layout.addStretch(1)
        self._toast = Toast(self)
        self.refresh()

    def _on_pick(self, idx: int) -> None:
        cam_id = self._picker.itemData(idx)
        if cam_id is None:
            return
        try:
            self.settings.set("camera_id", cam_id)
        except Exception as exc:
            log_exception(exc, "save camera")

    def refresh(self) -> None:
        self._picker.blockSignals(True)
        self._picker.clear()
        try:
            cameras = self.bridge.list_cameras()
        except Exception as exc:
            log_exception(exc, "list cameras")
            cameras = []
        current = self.settings.get("camera_id")
        sel = 0
        for i, cam in enumerate(cameras):
            label = getattr(cam, "label", None) or getattr(cam, "name", None) or f"Camera {i}"
            cid = getattr(cam, "id", i)
            self._picker.addItem(label, cid)
            if cid == current:
                sel = i
        if cameras:
            self._picker.setCurrentIndex(sel)
            self._status.setText(f"{len(cameras)} camera(s) found.")
        else:
            self._picker.addItem("No cameras found", None)
            self._status.setText("No cameras were detected. Connect a camera and press Refresh.")
        self._picker.blockSignals(False)
