"""Visage Manager: Glance-ditto settings window (500x620, floating tab bar)."""

from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication

from . import theme
from .engine_bridge import EngineBridge
from .settings_store import SettingsStore
from .views.about import AboutPage
from .views.camera_page import CameraPage
from .views.enroll_wizard import EnrollmentWizard
from .views.face_lab import FaceLabPage
from .views.face_page import FacePage
from .views.first_run import FirstRunWizard
from .views.general_page import GeneralPage
from .views.password_view import PasswordPage
from .views.recognition_page import RecognitionPage
from .views.settings_window import SettingsWindow
from .widgets import Toast

log = logging.getLogger("visage")


class VisageApp:
    def __init__(self, bridge: EngineBridge, settings: SettingsStore):
        self.bridge = bridge
        self.settings = settings
        self._face_lab_page: FaceLabPage | None = None

        self.window = SettingsWindow()
        self.window.set_session_unlocked(True)  # authenticated by Windows logon
        self.window.session_button_clicked.connect(self._on_session_button)

        self._toast = Toast(self.window)

        general = GeneralPage(bridge, settings)
        face = FacePage(bridge, settings)
        password = PasswordPage(bridge)
        camera = CameraPage(bridge, settings)
        recognition = RecognitionPage(bridge, settings)
        about = AboutPage()

        self.window.add_page("general", "General", general)
        self.window.add_page("face", "Face", face)
        self.window.add_page("password", "Password", password)
        self.window.add_page("camera", "Camera", camera)
        self.window.add_page("recognition", "Recognition", recognition)
        self.window.add_page("about", "About", about)

        face.enroll_requested.connect(self.open_enrollment)
        face.recapture_requested.connect(self.open_recapture)
        about.face_lab_unlocked.connect(self._unlock_face_lab)

        if self.settings.get("face_lab_unlocked"):
            self._unlock_face_lab(announce=False)

        self.window.set_page("general")

        if not self.settings.get("onboarding_done"):
            self._run_first_run()

    def _on_session_button(self) -> None:
        # Manual lock/unlock toggle. The session starts unlocked because the
        # user authenticated via the Windows logon; locking hides sensitive
        # pages until they unlock again (no extra credential — Windows logon
        # is the authentication).
        if self.window.is_session_unlocked():
            self.window.set_session_unlocked(False)
            self._toast.show_message("Session locked.")
        else:
            self.window.set_session_unlocked(True)
            self._toast.show_message("Session unlocked.")
        self.window.refresh_current()

    def _unlock_face_lab(self, announce: bool = True) -> None:
        if self._face_lab_page is None:
            self._face_lab_page = FaceLabPage(self.bridge)
            self.window.add_page("facelab", "Face Lab", self._face_lab_page)
            self.settings.set("face_lab_unlocked", True)
        if announce:
            self._toast.show_message("Face Lab unlocked.")
            self.window.set_page("facelab")

    def open_enrollment(self) -> None:
        wiz = EnrollmentWizard(self.bridge, parent=self.window)
        wiz.enrolled.connect(lambda ident: self._on_enrolled(ident))
        wiz.start()

    def open_recapture(self, identity_id: str, name: str) -> None:
        wiz = EnrollmentWizard(self.bridge, existing_id=identity_id,
                               existing_name=name, parent=self.window)
        wiz.enrolled.connect(lambda ident: self._on_enrolled(ident))
        wiz.start()

    def _on_enrolled(self, identity) -> None:
        self._toast.show_message(f"Enrolled \"{identity.name}\".")
        self.window.set_page("face")

    def _run_first_run(self) -> None:
        wiz = FirstRunWizard(self.bridge, self.settings, parent=self.window)
        wiz.finished_setup.connect(
            lambda: self._toast.show_message("Welcome to Visage."))
        wiz.exec()

    def show(self) -> None:
        self.window.show()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Visage")
    app.setOrganizationName("Visage")
    app.setStyleSheet(theme.STYLESHEET)

    bridge = EngineBridge()
    settings = SettingsStore()
    visage = VisageApp(bridge, settings)
    visage.show()

    if not bridge.using_real_engine:
        log.info("Running with the simulated engine — see ENGINE_INTERFACE.md.")
    return app.exec()
