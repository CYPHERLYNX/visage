"""Settings page: camera, recognition, liveness, session auto-lock.

Copy for the liveness modes follows Glance's plain-English framing:
deny cues (evidence of a spoof) vs. confirm cues (evidence of a real
face), and what Light / Heavy / Off each mean for the user.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider,
    QVBoxLayout, QWidget,
)

from .. import theme
from ..camera import probe_cameras
from ..engine_bridge import CameraInfo, EngineBridge
from ..settings_store import SettingsStore
from ..widgets import Card, RowCard, SegmentedControl, Toast, Toggle

LIVENESS_COPY = {
    "off": (
        "Off",
        "Face recognition alone decides. Fastest unlock — but a photo of you would pass too. "
        "Only choose this if you understand the tradeoff.",
    ),
    "light": (
        "Light",
        "Recommended. Rejects obvious spoofs — a photo on a phone screen, screen glare — "
        "without asking anything of you. You can sit perfectly still and it still works.",
    ),
    "heavy": (
        "Heavy",
        "Strongest. Also requires proof of life: a blink or a small head movement during the scan. "
        "A perfectly still scan may not pass.",
    ),
}

MATCH_LEVELS = [("Less strict", 0.58), ("Default", 0.66), ("More strict", 0.74)]
DISTANCE_LEVELS = [("Close", "close"), ("Default", "default"), ("Far", "far")]
AUTOLOCK_LEVELS = [("7 days", 7), ("30 days", 30), ("Never", 0)]


class SettingsPage(QWidget):
    def __init__(self, bridge: EngineBridge, settings: SettingsStore, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_PAGE, 32, theme.PAD_PAGE, theme.PAD_PAGE)
        layout.setSpacing(theme.GAP)

        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        layout.addSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setSpacing(theme.GAP)
        host_layout.addWidget(self._camera_card())
        host_layout.addWidget(self._recognition_card())
        host_layout.addWidget(self._liveness_card())
        host_layout.addWidget(self._session_card())
        host_layout.addStretch(1)
        scroll.setWidget(host)
        layout.addWidget(scroll, stretch=1)

        self._toast = Toast(self)

    # -- camera --------------------------------------------------------------
    def _camera_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Camera")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        row = QHBoxLayout()
        label = QLabel("Camera used for enrollment and unlock")
        label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: {theme.BODY_SIZE}px;")
        row.addWidget(label, stretch=1)

        self._camera_combo = QComboBox()
        self._camera_combo.setMinimumWidth(240)
        self._cameras: list[CameraInfo] = []
        # engine catalog first, then a local probe as fallback
        try:
            self._cameras = self.bridge.list_cameras()
        except Exception:
            self._cameras = []
        if not self._cameras:
            for idx, name in probe_cameras():
                self._cameras.append(CameraInfo(id=str(idx), label=name))
        if not self._cameras:
            self._camera_combo.addItem("No camera found", None)
            self._camera_combo.setEnabled(False)
        else:
            for cam in self._cameras:
                self._camera_combo.addItem(cam.label, cam.id)
            saved = self.settings.get("camera_id")
            for i, cam in enumerate(self._cameras):
                if cam.id == saved:
                    self._camera_combo.setCurrentIndex(i)
                    break
        self._camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        row.addWidget(self._camera_combo)
        lay.addLayout(row)

        note = QLabel(
            "If no camera appears, check that it is connected, not in use by another app, "
            "and allowed in Windows privacy settings (Privacy & security > Camera)."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        return card

    def _on_camera_changed(self, index: int) -> None:
        cam_id = self._camera_combo.itemData(index)
        self.settings.set("camera_id", cam_id)
        self._toast.show_message("Camera preference saved.")

    # -- recognition -----------------------------------------------------------
    def _recognition_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Recognition")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        lay.addWidget(self._slider_row(
            "Match confidence",
            "How closely a face must match before Visage accepts it.",
            [t for t, _ in MATCH_LEVELS],
            self._nearest_level(self.settings.get("match_threshold"), [v for _, v in MATCH_LEVELS]),
            lambda i: self.settings.set("match_threshold", MATCH_LEVELS[i][1]),
        ))
        lay.addWidget(self._slider_row(
            "Detection distance",
            "How far you can sit from the camera and still be recognized.",
            [t for t, _ in DISTANCE_LEVELS],
            self._nearest_level(self.settings.get("detection_distance"),
                                [v for _, v in DISTANCE_LEVELS]),
            lambda i: self.settings.set("detection_distance", DISTANCE_LEVELS[i][1]),
        ))
        return card

    @staticmethod
    def _nearest_level(value, options) -> int:
        """Index of the option closest to `value`; exact match for strings."""
        if options and isinstance(options[0], str):
            try:
                return options.index(value)
            except ValueError:
                return 0
        best, best_d = 0, None
        for i, opt in enumerate(options):
            try:
                d = abs(float(value or 0) - float(opt))
            except (TypeError, ValueError):
                continue
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best

    def _slider_row(self, title, caption, labels, index, on_change) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 6, 0, 6)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: {theme.BODY_SIZE}px; font-weight: 600;")
        lay.addWidget(t)
        c = QLabel(caption)
        c.setObjectName("caption")
        c.setWordWrap(True)
        lay.addWidget(c)
        row = QHBoxLayout()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(len(labels) - 1)
        slider.setSingleStep(1)
        slider.setPageStep(1)
        slider.setValue(index)
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.valueChanged.connect(lambda i: (on_change(i), val.setText(labels[i])))
        row.addWidget(slider, stretch=1)
        val = QLabel(labels[index])
        val.setMinimumWidth(80)
        val.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(val)
        lay.addLayout(row)
        return wrap

    # -- liveness ----------------------------------------------------------------
    def _liveness_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Liveness")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        row = RowCard()
        rl = row.layout()
        text_col = QVBoxLayout()
        t = QLabel("Liveness detection")
        t.setStyleSheet(f"font-size: {theme.BODY_SIZE}px; font-weight: 600;")
        text_col.addWidget(t)
        c = QLabel("Checks that you're a live person, not a photo. May increase unlock time.")
        c.setObjectName("caption")
        c.setWordWrap(True)
        text_col.addWidget(c)
        rl.addLayout(text_col, stretch=1)
        self._liveness_toggle = Toggle()
        self._liveness_toggle.setChecked(self.settings.get("liveness_enabled"))
        self._liveness_toggle.toggled.connect(self._on_liveness_toggled)
        rl.addWidget(self._liveness_toggle)
        lay.addWidget(row)

        self._mode_wrap = QWidget()
        self._mode_wrap.setStyleSheet("background: transparent;")
        ml = QVBoxLayout(self._mode_wrap)
        ml.setContentsMargins(0, 4, 0, 0)
        self._seg = SegmentedControl(["Off", "Light", "Heavy"])
        current = {"off": 0, "light": 1, "heavy": 2}[self.settings.liveness_mode]
        # keep toggle + segmented consistent: toggle off forces "Off"
        if not self.settings.get("liveness_enabled"):
            current = 0
        self._seg.setCurrent(current)
        self._seg.indexChanged.connect(self._on_mode_changed)
        ml.addWidget(self._seg)
        self._mode_desc = QLabel("")
        self._mode_desc.setObjectName("caption")
        self._mode_desc.setWordWrap(True)
        ml.addWidget(self._mode_desc)
        lay.addWidget(self._mode_wrap)
        self._update_mode_desc()
        self._mode_wrap.setEnabled(self.settings.get("liveness_enabled"))
        return card

    def _on_liveness_toggled(self, on: bool) -> None:
        self.settings.set("liveness_enabled", on)
        self.settings.set("liveness_mode", "light" if on else "off")
        self._mode_wrap.setEnabled(on)
        self._seg.setCurrent(1 if on else 0)
        self._update_mode_desc()
        self._toast.show_message("Liveness " + ("enabled." if on else "disabled."))

    def _on_mode_changed(self, index: int) -> None:
        mode = ["off", "light", "heavy"][index]
        self.settings.set("liveness_mode", mode)
        self.settings.set("liveness_enabled", mode != "off")
        # keep the toggle in sync without recursion
        self._liveness_toggle.blockSignals(True)
        self._liveness_toggle.setChecked(mode != "off")
        self._liveness_toggle.blockSignals(False)
        self._mode_wrap.setEnabled(mode != "off")
        self._update_mode_desc()

    def _update_mode_desc(self) -> None:
        mode = self.settings.liveness_mode
        title, desc = LIVENESS_COPY[mode]
        self._mode_desc.setText(f"{title}: {desc}")

    # -- session -------------------------------------------------------------------
    def _session_card(self) -> Card:
        card = Card()
        lay = card.layout()
        head = QLabel("Session")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        lay.addWidget(self._slider_row(
            "Auto-lock session",
            "How long Visage stays authorized after you unlock it. Afterwards it asks "
            "for your face again instead of trusting the last unlock.",
            [t for t, _ in AUTOLOCK_LEVELS],
            self._nearest_level(self.settings.auto_lock_days, [v for _, v in AUTOLOCK_LEVELS]),
            lambda i: (self.settings.set("auto_lock_days", AUTOLOCK_LEVELS[i][1]),
                       self._toast.show_message("Auto-lock preference saved.")),
        ))

        reset = QPushButton("Reset all settings")
        reset.setObjectName("danger")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._on_reset)
        lay.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)
        return card

    def _on_reset(self) -> None:
        self.settings.reset()
        self._toast.show_message("Settings reset. Reopen this page to see the defaults.")
