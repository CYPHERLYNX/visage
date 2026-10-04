"""Recognition settings page (Glance-ditto).

Match threshold slider + liveness mode, in Glance's settings row style.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget,
)

from .. import theme
from ..engine_bridge import EngineBridge
from ..errors import log_exception
from ..widgets import Card, Toggle


class RecognitionPage(QWidget):
    def __init__(self, bridge: EngineBridge, settings, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(12)

        # -- matching -----------------------------------------------------------
        card = Card()
        layout.addWidget(card)
        lay = card.layout()

        head = QLabel("Matching")
        head.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {theme.TEXT};")
        lay.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("Match threshold"))
        self._thr_value = QLabel("")
        self._thr_value.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        row.addWidget(self._thr_value)
        lay.addLayout(row)

        self._thr = QSlider(Qt.Orientation.Horizontal)
        self._thr.setRange(50, 85)  # 0.50 - 0.85
        self._thr.valueChanged.connect(self._on_threshold)
        lay.addWidget(self._thr)

        hint = QLabel("Higher is stricter: fewer false accepts, more retries in bad light.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # -- liveness ------------------------------------------------------------
        card2 = Card()
        layout.addWidget(card2)
        lay2 = card2.layout()

        head2 = QLabel("Liveness")
        head2.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {theme.TEXT};")
        lay2.addWidget(head2)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Liveness checks"))
        self._live_toggle = Toggle()
        self._live_toggle.toggled.connect(self._on_liveness_toggle)
        row2.addStretch(1)
        row2.addWidget(self._live_toggle)
        lay2.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Liveness mode"))
        self._mode = QComboBox()
        self._mode.addItem("Light", "light")
        self._mode.addItem("Heavy", "heavy")
        self._mode.currentIndexChanged.connect(self._on_mode)
        row3.addStretch(1)
        row3.addWidget(self._mode)
        lay2.addLayout(row3)

        hint2 = QLabel("Liveness defeats printed photos. Heavy mode is stricter but slower.")
        hint2.setObjectName("hint")
        hint2.setWordWrap(True)
        lay2.addWidget(hint2)

        layout.addStretch(1)
        self.refresh()

    def _on_threshold(self, v: int) -> None:
        try:
            self.settings.set("match_threshold", v / 100.0)
        except Exception as exc:
            log_exception(exc, "save threshold")
        self._thr_value.setText(f"{v / 100.0:.2f}")

    def _on_liveness_toggle(self, on: bool) -> None:
        try:
            self.settings.set("liveness_enabled", on)
        except Exception as exc:
            log_exception(exc, "save liveness")
        self._mode.setEnabled(on)

    def _on_mode(self, idx: int) -> None:
        try:
            self.settings.set("liveness_mode", self._mode.itemData(idx))
        except Exception as exc:
            log_exception(exc, "save liveness mode")

    def refresh(self) -> None:
        thr = float(self.settings.get("match_threshold") or 0.66)
        self._thr.blockSignals(True)
        self._thr.setValue(int(round(thr * 100)))
        self._thr.blockSignals(False)
        self._thr_value.setText(f"{thr:.2f}")

        live = bool(self.settings.get("liveness_enabled"))
        self._live_toggle.blockSignals(True)
        self._live_toggle.setChecked(live)
        self._live_toggle.blockSignals(False)
        self._mode.setEnabled(live)

        mode = self.settings.get("liveness_mode") or "light"
        idx = self._mode.findData(mode)
        self._mode.blockSignals(True)
        self._mode.setCurrentIndex(max(idx, 0))
        self._mode.blockSignals(False)
