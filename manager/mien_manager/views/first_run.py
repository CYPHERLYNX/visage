"""First-run setup wizard.

Mirrors Glance's onboarding arc: welcome → honest security notice →
camera pick → guided enrollment → password → done. Each page is calm,
one idea at a time, in the same Apple-grade tone as the reference.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .. import theme
from ..camera import probe_cameras
from ..engine_bridge import EngineBridge
from ..settings_store import SettingsStore
from ..widgets import LogoMark
from .enroll_wizard import EnrollmentWizard

PAGES = ("welcome", "security", "presetup", "camera", "enroll", "password", "done")


class FirstRunWizard(QDialog):
    finished_setup = pyqtSignal()

    def __init__(self, bridge: EngineBridge, settings: SettingsStore, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.settings = settings
        self._enrolled_name = ""

        self.setWindowTitle("Welcome to Mien")
        self.setModal(True)
        self.resize(620, 520)
        self.setStyleSheet(theme.STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 40, 48, 40)
        outer.setSpacing(theme.GAP)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, stretch=1)

        self._stack.addWidget(self._welcome_page())
        self._stack.addWidget(self._security_page())
        self._stack.addWidget(self._presetup_page())
        self._stack.addWidget(self._camera_page())
        self._stack.addWidget(self._enroll_prompt_page())
        self._stack.addWidget(self._password_prompt_page())
        self._stack.addWidget(self._done_page())

        nav = QHBoxLayout()
        nav.addStretch(1)
        self._back_btn = QPushButton("Back")
        self._back_btn.setObjectName("ghost")
        self._back_btn.clicked.connect(self._back)
        nav.addWidget(self._back_btn)
        self._next_btn = QPushButton("Continue")
        self._next_btn.setObjectName("primary")
        self._next_btn.clicked.connect(self._next)
        nav.addWidget(self._next_btn)
        outer.addLayout(nav)
        self._sync_nav()

    # -- pages -----------------------------------------------------------------
    def _page_shell(self, title: str, body: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(theme.GAP_SMALL)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 24px; font-weight: 600; color: {theme.TEXT};")
        t.setWordWrap(True)
        lay.addWidget(t)
        b = QLabel(body)
        b.setStyleSheet(f"font-size: {theme.BODY_SIZE + 1}px; color: {theme.TEXT_SECONDARY};")
        b.setWordWrap(True)
        lay.addWidget(b)
        lay.addStretch(1)
        return page, lay

    def _welcome_page(self) -> QWidget:
        page, lay = self._page_shell(
            "Unlock your PC with a glance.",
            "Mien learns your face, then signs you in at the Windows lock screen — "
            "no typing, no reaching for a key. Everything runs on this device: "
            "your face data and your password never touch the internet.",
        )
        logo = LogoMark(72)
        lay.insertWidget(0, logo, alignment=Qt.AlignmentFlag.AlignLeft)
        return page

    def _security_page(self) -> QWidget:
        page, lay = self._page_shell(
            "A quick, honest note on security.",
            "Mien is not as secure as Windows Hello or a fingerprint reader. It uses "
            "your PC's standard webcam — a flat 2D image, not a depth map.\n\n"
            "With liveness detection on, Mien defeats a printed photo and a photo on a "
            "phone screen with reasonable confidence. It does not reliably defeat a "
            "video of you.\n\n"
            "Mien is a convenience feature, not a security upgrade. "
            "Only continue if you accept the tradeoff.",
        )
        return page

    def _presetup_page(self) -> QWidget:
        # Glance's pre-setup copy, verbatim in spirit.
        page, lay = self._page_shell(
            "Set up Face Recognition",
            "Follow the directions shown on the screen",
        )
        return page

    def _camera_page(self) -> QWidget:
        # Mirrors Glance's SelectCameraStepView.
        page, lay = self._page_shell(
            "Select camera",
            "Used for Face enrollment and for unlocking your PC",
        )
        self._cam_combo = QComboBox()
        self._cam_combo.setMinimumWidth(280)
        cams = [(c.id, c.label) for c in self.bridge.list_cameras()]
        if not cams:
            for idx, label in probe_cameras():
                cams.append((str(idx), label))
        if not cams:
            self._cam_combo.addItem("No camera found", None)
            self._cam_combo.setEnabled(False)
        else:
            for cid, label in cams:
                self._cam_combo.addItem(label, cid)
            saved = self.settings.get("camera_id")
            for i, (cid, _) in enumerate(cams):
                if cid == saved:
                    self._cam_combo.setCurrentIndex(i)
                    break
            self._cam_combo.currentIndexChanged.connect(
                lambda i: self.settings.set("camera_id", self._cam_combo.itemData(i)))
        lay.addWidget(self._cam_combo, alignment=Qt.AlignmentFlag.AlignLeft)
        note = QLabel(
            "If no camera appears, check that it is connected, not in use by another "
            "app, and allowed in Windows privacy settings (Privacy & security > Camera).")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        return page

    def _enroll_prompt_page(self) -> QWidget:
        page, lay = self._page_shell(
            "First, teach Mien your face.",
            "You'll turn your head in nine directions while Mien captures what it needs. "
            "It takes about a minute. Sit in even light and face the screen.",
        )
        btn = QPushButton("Start enrollment")
        btn.setObjectName("primary")
        btn.clicked.connect(self._run_enrollment)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._enroll_status = QLabel("")
        self._enroll_status.setObjectName("caption")
        lay.addWidget(self._enroll_status)
        return page

    def _password_prompt_page(self) -> QWidget:
        page, lay = self._page_shell(
            "Enter your password",
            "Your password is required to unlock your PC. It is encrypted and securely "
            "stored on your device. Mien works entirely offline, so your password never "
            "leaves your PC.",
        )
        btn = QPushButton("Set password")
        btn.setObjectName("primary")
        btn.clicked.connect(self._open_password_page)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        note = QLabel("You can also do this later from the Password page.")
        note.setObjectName("hint")
        lay.addWidget(note)
        return page

    def _done_page(self) -> QWidget:
        page, lay = self._page_shell(
            "You're all set.",
            "Mien will offer a face scan on the Windows lock screen. "
            "You can enroll more faces, tune liveness, or open Face Lab from the sidebar any time.",
        )
        return page

    # -- navigation --------------------------------------------------------------
    def _sync_nav(self) -> None:
        idx = self._stack.currentIndex()
        self._back_btn.setVisible(idx > 0 and idx < len(PAGES) - 1)
        if idx == 4 and not self._enrolled_name:
            self._next_btn.setEnabled(False)
            self._next_btn.setText("Enroll first")
        elif idx == len(PAGES) - 1:
            self._next_btn.setText("Finish")
            self._next_btn.setEnabled(True)
        else:
            self._next_btn.setText("Continue")
            self._next_btn.setEnabled(True)

    def _next(self) -> None:
        idx = self._stack.currentIndex()
        if idx >= len(PAGES) - 1:
            self.settings.set("onboarding_done", True)
            self.finished_setup.emit()
            self.accept()
            return
        self._stack.setCurrentIndex(idx + 1)
        self._sync_nav()

    def _back(self) -> None:
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._sync_nav()

    # -- actions -------------------------------------------------------------------
    def _run_enrollment(self) -> None:
        wiz = EnrollmentWizard(self.bridge, parent=self)
        wiz.enrolled.connect(self._on_enrolled)
        wiz.start()

    def _on_enrolled(self, identity) -> None:
        self._enrolled_name = identity.name
        self._enroll_status.setText(f"Enrolled as \"{identity.name}\". You can continue.")
        self._sync_nav()

    def _open_password_page(self) -> None:
        # The main window's password page is the canonical place; signal the
        # parent to navigate there after setup. For the modal flow we simply
        # advance — the Home page will prompt for the missing password.
        self._next()
