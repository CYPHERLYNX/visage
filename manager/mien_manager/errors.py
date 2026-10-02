"""Human-readable error mapping.

Every failure that reaches the UI goes through here. The rule is simple:
no tracebacks on screen — a plain sentence saying what happened and what
to try next. Full tracebacks still go to the log file for diagnosis.
"""

from __future__ import annotations

import logging
import traceback

log = logging.getLogger("mien")


class MienError(Exception):
    """Base for expected, user-facing failures."""

    def __init__(self, message: str, remedy: str = ""):
        super().__init__(message)
        self.message = message
        self.remedy = remedy

    def full_text(self) -> str:
        if self.remedy:
            return f"{self.message}\n\n{self.remedy}"
        return self.message


class EngineUnavailableError(MienError):
    pass


class CameraUnavailableError(MienError):
    pass


class VaultUnavailableError(MienError):
    pass


class CredentialProviderError(MienError):
    pass


class StoreError(MienError):
    pass


def humanize(exc: BaseException) -> str:
    """Turn any exception into a calm, actionable message for the UI."""
    if isinstance(exc, MienError):
        return exc.full_text()

    name = type(exc).__name__
    detail = str(exc).strip()

    mapping = {
        "FileNotFoundError": (
            "A file Mien needs is missing.",
            "Reinstall Mien Manager, then try again. If it keeps happening, check the log for the missing path.",
        ),
        "PermissionError": (
            "Mien was blocked by a file or device permission.",
            "Try running Mien Manager as administrator, then try again.",
        ),
        "ConnectionError": (
            "Mien could not reach a local service it depends on.",
            "Make sure the Mien engine service is installed and running, then try again.",
        ),
        "TimeoutError": (
            "The operation took too long and was stopped.",
            "Check that your camera is connected and not in use by another app, then try again.",
        ),
        "ImportError": (
            "A required component could not be loaded.",
            "On Windows, install the requirements (pip install -r requirements.txt) and restart Mien Manager.",
        ),
    }
    if name in mapping:
        title, remedy = mapping[name]
        text = title
        if detail:
            text += f" Details: {detail}"
        return f"{text}\n\n{remedy}"

    text = "Something went wrong."
    if detail and len(detail) < 200:
        text += f" {detail}"
    text += "\n\nPlease try again. If it keeps happening, the log file has the technical details."
    return text


def log_exception(exc: BaseException, context: str = "") -> None:
    """Record the full traceback in the log, never in the UI."""
    where = f" [{context}]" if context else ""
    log.error("Exception%s: %s\n%s", where, exc, traceback.format_exc())
