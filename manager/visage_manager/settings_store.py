"""Persisted Manager settings (JSON, per-user).

Windows: %APPDATA%\\Visage\\manager-settings.json
Other:   ~/.config/visage/manager-settings.json

Mirrors the spirit of GlanceSettings: writes go through immediately, and
the file is plain JSON so it can be inspected or reset by hand.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULTS = {
    "camera_id": None,
    "liveness_enabled": True,
    "liveness_mode": "light",          # off | light | heavy
    "match_threshold": 0.66,
    "detection_distance": "default",   # close | default | far
    "auto_lock_days": 30,             # 7 | 14 | 30 | 0 (never)
    "face_unlock_enabled": True,
    "onboarding_done": False,
    "face_lab_unlocked": False,
}


def _settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return base / "Visage" / "manager-settings.json"
    return Path.home() / ".config" / "visage" / "manager-settings.json"


class SettingsStore:
    def __init__(self) -> None:
        self._path = _settings_path()
        self._data = dict(_DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self._path.exists():
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for key in _DEFAULTS:
                        if key in loaded:
                            self._data[key] = loaded[key]
        except (OSError, ValueError):
            # A corrupt settings file must never break startup; defaults win.
            pass

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            pass  # Settings are best-effort; the app keeps running.

    def get(self, key: str):
        return self._data.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if key not in _DEFAULTS:
            raise KeyError(f"Unknown setting: {key}")
        self._data[key] = value
        self.save()

    def reset(self) -> None:
        self._data = dict(_DEFAULTS)
        self.save()

    # Typed conveniences -------------------------------------------------
    @property
    def liveness_mode(self) -> str:
        mode = str(self.get("liveness_mode"))
        return mode if mode in ("off", "light", "heavy") else "light"

    @property
    def auto_lock_days(self) -> int:
        days = self.get("auto_lock_days")
        return days if days in (7, 14, 30, 0) else 30
