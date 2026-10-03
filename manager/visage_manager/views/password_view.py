"""One-time Windows password setup.

Collects the Windows username (+ optional domain) and password, then hands
them to the engine vault (DPAPI on Windows). The password is never shown
on screen, never logged, and never kept in memory longer than the call.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from .. import theme
from ..engine_bridge import EngineBridge
from ..errors import VisageError, humanize, log_exception
from ..widgets import Card, Toast


class PasswordPage(QWidget):
    def __init__(self, bridge: EngineBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_PAGE, 32, theme.PAD_PAGE, theme.PAD_PAGE)
        layout.setSpacing(theme.GAP)

        title = QLabel("Windows password")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        sub = QLabel(
            "Your password is required to unlock your PC. It is encrypted and securely "
            "stored on your device. Visage works entirely offline, so your password never "
            "leaves your PC."
        )
        sub.setObjectName("pageSubtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(8)

        self._card = Card()
        layout.addWidget(self._card)
        layout.addStretch(1)
        self._toast = Toast(self)
        self._build_form()

    def _build_form(self) -> None:
        lay = self._card.layout()
        head = QLabel("Store your sign-in password")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        lay.addWidget(self._field_label("Windows username"))
        self._username = QLineEdit()
        self._username.setPlaceholderText("e.g. fahad")
        lay.addWidget(self._username)

        lay.addWidget(self._field_label("Domain (only if your PC is on a work domain)"))
        self._domain = QLineEdit()
        self._domain.setPlaceholderText("Optional")
        lay.addWidget(self._domain)

        lay.addWidget(self._field_label("Password"))
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("Your Windows password")
        lay.addWidget(self._password)

        lay.addWidget(self._field_label("Confirm password"))
        self._confirm = QLineEdit()
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addWidget(self._confirm)

        self._error = QLabel("")
        self._error.setStyleSheet(f"color: {theme.DANGER}; font-size: {theme.BODY_SIZE}px;")
        self._error.setWordWrap(True)
        lay.addWidget(self._error)

        row = QHBoxLayout()
        row.addStretch(1)
        self._clear_btn = QPushButton("Remove stored password")
        self._clear_btn.setObjectName("danger")
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self._on_clear)
        row.addWidget(self._clear_btn)
        save = QPushButton("Save securely")
        save.setObjectName("primary")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._on_save)
        row.addWidget(save)
        lay.addLayout(row)

        note = QLabel(
            "The password field is never displayed and never written to any log. "
            "Only the encrypted form is stored, and only Windows itself can decrypt it."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.refresh_state()

    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: {theme.BODY_SIZE}px; color: {theme.TEXT_SECONDARY};")
        return lbl

    def refresh_state(self) -> None:
        has = self.bridge.has_credentials()
        user = self.bridge.stored_username()
        self._clear_btn.setVisible(has)
        if has:
            self._error.setStyleSheet(f"color: {theme.SUCCESS}; font-size: {theme.BODY_SIZE}px;")
            shown = f" A password is stored for \"{user}\"." if user else " A password is stored."
            self._error.setText("You're set." + shown + " Saving again replaces it.")

    def _on_save(self) -> None:
        username = self._username.text().strip()
        domain = self._domain.text().strip()
        password = self._password.text()
        confirm = self._confirm.text()
        self._error.setStyleSheet(f"color: {theme.DANGER}; font-size: {theme.BODY_SIZE}px;")

        if not username:
            self._error.setText("Enter your Windows username.")
            return
        if not password:
            self._error.setText("Enter your Windows password.")
            return
        if password != confirm:
            self._error.setText("The two passwords don't match. Please retype them.")
            self._password.clear()
            self._confirm.clear()
            return
        saved = False
        try:
            # The password lives only in these locals; it is passed straight
            # into the vault and never assigned to an attribute or logged.
            self.bridge.store_credentials(username, password, domain)
            saved = True
        except VisageError as exc:
            self._error.setText(exc.full_text())
        except Exception as exc:
            log_exception(exc, "store credentials")
            self._error.setText(humanize(exc))
        finally:
            self._password.clear()
            self._confirm.clear()
        if saved:
            self._error.setText("")
            self._toast.show_message("Password stored securely.")
            self._username.clear()
            self._domain.clear()
        self.refresh_state()

    def _on_clear(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Remove stored password")
        box.setText("Remove the stored Windows password?")
        box.setInformativeText("Face unlock will stop working until you store it again.")
        box.setStandardButtons(QMessageBox.StandardButton.Remove | QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Remove:
            return
        try:
            self.bridge.clear_credentials()
            self._toast.show_message("Stored password removed.")
        except Exception as exc:
            log_exception(exc, "clear credentials")
            self._toast.show_message(humanize(exc).split("\n\n")[0])
        self.refresh_state()
