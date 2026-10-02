"""Credential Provider (lock-screen tile) registration controls.

Calls the PowerShell scripts owned by the CP worker:

    ../cp/register.ps1      — register the Mien credential provider tile
    ../cp/unregister.ps1    — remove it

Status is read from ``../cp/status.ps1`` when present; otherwise the view
reports honestly that status cannot be determined from here.

Everything here is Windows-only by nature. On other platforms every
operation raises a human-readable error instead of failing obscurely.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .errors import CredentialProviderError

log = logging.getLogger("mien")


def _cp_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "cp"


def scripts_present() -> bool:
    d = _cp_dir()
    return (d / "register.ps1").exists() or (d / "unregister.ps1").exists()


def _run_ps1(script: Path, timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CredentialProviderError(
            "PowerShell is not available.",
            "Credential Provider registration needs Windows with PowerShell.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CredentialProviderError(
            "The registration script timed out.",
            "Try again; if it keeps timing out, run the script by hand in an elevated PowerShell.",
        ) from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise CredentialProviderError(
            "The Credential Provider script reported a failure.",
            f"Details:\n{output.strip() or 'no output'}\n\n"
            "Try running it in an elevated PowerShell window to see the full error.",
        )
    return output.strip()


def _require_windows(action: str) -> Path:
    import os
    if os.name != "nt":
        raise CredentialProviderError(
            f"Cannot {action} the lock-screen tile here.",
            "The Windows lock-screen tile only exists on Windows 10/11.",
        )
    script = _cp_dir() / f"{action}.ps1"
    if not script.exists():
        raise CredentialProviderError(
            f"The {action} script is missing.",
            f"Expected it at {script}. The Credential Provider component may not be built yet.",
        )
    return script


def register() -> str:
    """Register the Mien tile on the Windows lock screen."""
    return _run_ps1(_require_windows("register"))


def unregister() -> str:
    """Remove the Mien tile from the Windows lock screen."""
    return _run_ps1(_require_windows("unregister"))


def status() -> str:
    """Best-effort registration status, in plain words."""
    import os
    if os.name != "nt":
        return "Not available — the lock-screen tile only exists on Windows."
    status_script = _cp_dir() / "status.ps1"
    if status_script.exists():
        try:
            out = _run_ps1(status_script, timeout=15)
            return out or "The status script ran but reported nothing."
        except CredentialProviderError as exc:
            return exc.message
    if not scripts_present():
        return "The Credential Provider scripts are not installed yet, so the tile cannot be registered."
    return "Status unknown — no status script is installed. Use Register / Unregister below."
