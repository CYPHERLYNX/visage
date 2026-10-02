"""Credential Provider page: the Windows lock-screen tile.

Registers/unregisters Mien's tile via the PowerShell scripts owned by the
CP worker (../cp/register.ps1 and ../cp/unregister.ps1). Registering needs
an elevated process — the page says so up front instead of failing
cryptically.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme
from .. import cp as cp_ops
from ..errors import CredentialProviderError, humanize, log_exception
from ..widgets import Card, Toast


class CredentialProviderPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_PAGE, 32, theme.PAD_PAGE, theme.PAD_PAGE)
        layout.setSpacing(theme.GAP)

        title = QLabel("Lock-screen tile")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        sub = QLabel(
            "The Credential Provider is the tile on the Windows lock screen that starts "
            "a Mien face scan. Register it once; Windows shows it on every lock screen afterwards."
        )
        sub.setObjectName("pageSubtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(8)

        card = Card()
        lay = card.layout()
        head = QLabel("Registration")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)

        self._status = QLabel("Checking…")
        self._status.setObjectName("caption")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        row = QHBoxLayout()
        self._register_btn = QPushButton("Register tile")
        self._register_btn.setObjectName("primary")
        self._register_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._register_btn.clicked.connect(self._on_register)
        row.addWidget(self._register_btn)
        self._unregister_btn = QPushButton("Unregister tile")
        self._unregister_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unregister_btn.clicked.connect(self._on_unregister)
        row.addWidget(self._unregister_btn)
        refresh = QPushButton("Refresh status")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(self.refresh_status)
        row.addWidget(refresh)
        row.addStretch(1)
        lay.addLayout(row)

        note = QLabel(
            "Registration writes to HKLM, so it needs an elevated Mien Manager "
            "(right-click > Run as administrator). Unregistering removes the tile completely."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        layout.addWidget(card)

        self._output = QLabel("")
        self._output.setObjectName("hint")
        self._output.setWordWrap(True)
        self._output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._output)
        layout.addStretch(1)

        self._toast = Toast(self)
        self.refresh_status()

    def refresh_status(self) -> None:
        try:
            text = cp_ops.status()
        except Exception as exc:
            log_exception(exc, "cp status")
            text = humanize(exc).split("\n\n")[0]
        self._status.setText(text)

    def _run(self, action: str, fn) -> None:
        self._output.setText("")
        try:
            out = fn()
        except CredentialProviderError as exc:
            log_exception(exc, f"cp {action}")
            self._output.setText(exc.full_text())
            self._toast.show_message(f"{action.capitalize()} failed.")
            return
        except Exception as exc:
            log_exception(exc, f"cp {action}")
            self._output.setText(humanize(exc))
            self._toast.show_message(f"{action.capitalize()} failed.")
            return
        if out:
            self._output.setText(out)
        self._toast.show_message(f"Tile {action}ed.")
        self.refresh_status()

    def _on_register(self) -> None:
        self._run("register", cp_ops.register)

    def _on_unregister(self) -> None:
        self._run("unregister", cp_ops.unregister)
