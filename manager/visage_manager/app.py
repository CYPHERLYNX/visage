"""Visage Manager main window: sidebar navigation + stacked pages."""

from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import theme
from .engine_bridge import EngineBridge
from .errors import humanize, log_exception
from .settings_store import SettingsStore
from .views.about import AboutPage
from .views.credential_provider_view import CredentialProviderPage
from .views.enroll_wizard import EnrollmentWizard
from .views.face_lab import FaceLabPage
from .views.first_run import FirstRunWizard
from .views.home import HomePage
from .views.identities import IdentitiesPage
from .views.password_view import PasswordPage
from .views.settings_view import SettingsPage
from .widgets import LogoMark, Toast

log = logging.getLogger("visage")


class SidebarButton(QPushButton):
    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; text-align: left;"
            f" padding: 10px 14px; border-radius: 8px; font-size: {theme.BODY_SIZE + 1}px;"
            f" color: {theme.TEXT_SECONDARY}; }}"
            f"QPushButton:checked {{ background: #232329; color: {theme.TEXT}; font-weight: 600; }}"
            "QPushButton:hover:!checked {{ background: #1B1B1F; }}"
        )


class MainWindow(QMainWindow):
    def __init__(self, bridge: EngineBridge, settings: SettingsStore):
        super().__init__()
        self.bridge = bridge
        self.settings = settings
        self._face_lab_page: FaceLabPage | None = None
        self._face_lab_button: SidebarButton | None = None

        self.setWindowTitle("Visage")
        self.resize(1080, 720)
        self.setMinimumSize(900, 600)
        self.setStyleSheet(theme.STYLESHEET)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar(), stretch=0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, stretch=1)

        self._pages: dict[str, QWidget] = {}
        self._buttons: dict[str, SidebarButton] = {}
        self._add_page("home", "Home", HomePage(bridge, settings))
        self._add_page("identities", "Identities", IdentitiesPage(bridge))
        self._add_page("settings", "Settings", SettingsPage(bridge, settings))
        self._add_page("password", "Password", PasswordPage(bridge))
        self._add_page("cp", "Lock-screen tile", CredentialProviderPage())
        self._add_page("about", "About", AboutPage())

        home = self._pages["home"]
        home.enroll_requested.connect(self.open_enrollment)
        home.open_identities.connect(lambda: self.navigate("identities"))
        home.open_settings.connect(lambda: self.navigate("settings"))
        home.open_password.connect(lambda: self.navigate("password"))
        home.open_cp.connect(lambda: self.navigate("cp"))

        identities = self._pages["identities"]
        identities.enroll_requested.connect(self.open_enrollment)
        identities.recapture_requested.connect(self.open_recapture)

        about = self._pages["about"]
        about.face_lab_unlocked.connect(self._unlock_face_lab)

        if self.settings.get("face_lab_unlocked"):
            self._unlock_face_lab(announce=False)

        self.setCentralWidget(root)
        self._toast = Toast(root)
        self.navigate("home")

        if not self.settings.get("onboarding_done"):
            self._run_first_run()

    # -- sidebar -----------------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(224)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(16, 20, 16, 16)
        lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.addWidget(LogoMark(34))
        name = QLabel("Visage")
        name.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {theme.TEXT};")
        brand.addWidget(name)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(16)

        self._nav_host = QVBoxLayout()
        self._nav_host.setSpacing(2)
        lay.addLayout(self._nav_host)
        lay.addStretch(1)

        foot = QLabel("On-device. Private by design.")
        foot.setObjectName("hint")
        foot.setWordWrap(True)
        lay.addWidget(foot)
        return bar

    def _add_page(self, key: str, label: str, page: QWidget) -> None:
        self._pages[key] = page
        self._stack.addWidget(page)
        btn = SidebarButton(label)
        btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
        self._buttons[key] = btn
        self._nav_host.addWidget(btn)

    def navigate(self, key: str) -> None:
        if key not in self._pages:
            return
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)
        self._stack.setCurrentWidget(self._pages[key])
        page = self._pages[key]
        if hasattr(page, "refresh"):
            try:
                page.refresh()
            except Exception as exc:
                log_exception(exc, f"refresh {key}")

    # -- face lab ------------------------------------------------------------------
    def _unlock_face_lab(self, announce: bool = True) -> None:
        if self._face_lab_page is None:
            self._face_lab_page = FaceLabPage(self.bridge)
            self._add_page("facelab", "Face Lab", self._face_lab_page)
            self.settings.set("face_lab_unlocked", True)
        if announce:
            self._toast.show_message("Face Lab unlocked.")
            self.navigate("facelab")

    # -- enrollment -------------------------------------------------------------------
    def open_enrollment(self) -> None:
        wiz = EnrollmentWizard(self.bridge, parent=self)
        wiz.enrolled.connect(lambda ident: self._on_enrolled(ident))
        wiz.start()

    def open_recapture(self, identity_id: str, name: str) -> None:
        wiz = EnrollmentWizard(self.bridge, existing_id=identity_id,
                               existing_name=name, parent=self)
        wiz.enrolled.connect(lambda ident: self._on_enrolled(ident))
        wiz.start()

    def _on_enrolled(self, identity) -> None:
        self._toast.show_message(f"Enrolled \"{identity.name}\".")
        self.navigate("identities")

    # -- first run ----------------------------------------------------------------------
    def _run_first_run(self) -> None:
        wiz = FirstRunWizard(self.bridge, self.settings, parent=self)
        wiz.finished_setup.connect(lambda: (self.navigate("home"), self._toast.show_message("Welcome to Visage.")))
        wiz.exec()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Visage")
    app.setOrganizationName("Visage")

    bridge = EngineBridge()
    settings = SettingsStore()
    window = MainWindow(bridge, settings)
    window.show()

    if not bridge.using_real_engine:
        log.info("Running with the simulated engine — see ENGINE_INTERFACE.md.")
    return app.exec()
