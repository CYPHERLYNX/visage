"""
Encrypted enrollment (identity) store.

Per identity::

    {"version": 1, "name": "...", "embeddings": [[512 floats], ...],
     "enabled": true, "created": "ISO-8601"}

Multiple 512-d embeddings per identity (Glance captures nine head directions;
several angles make matching robust to pose). Encrypted at rest with envelope
encryption (random AES-256-GCM data key per file, key wrapped by DPAPI
LocalMachine scope — see vault.seal); dev-only file-key fallback elsewhere.

Images are NEVER written to disk — embeddings only. Camera frames never leave
the machine: no network calls happen anywhere in this package at runtime.

Trust model: the engine only ever reads embeddings to *match* against. It has
no access to the password vault and the pipe protocol has no message that can
request credentials or trigger credential submission.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Dict, List, Optional

import numpy as np

from . import vault as _vault
from .recognition import EMBEDDING_DIM, MODEL_IDENTIFIER, IdentityTemplate

IDENTITY_VERSION = 1


def default_identities_path() -> str:
    if os.name == "nt":
        return r"C:\ProgramData\Mien\identities.dat"
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "identities.dev.dat")


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class IdentityStore:
    """Encrypted store of enrolled identities."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or default_identities_path()
        self._identities: List[Dict] = []
        self._dirty = False
        self.load()

    # -- persistence ------------------------------------------------------

    def load(self) -> None:
        if not os.path.isfile(self.path):
            self._identities = []
            return
        with open(self.path, "rb") as fh:
            blob = fh.read()
        try:
            payload = json.loads(_vault.unseal(blob, self.path).decode("utf-8"))
        except Exception as exc:
            raise ValueError("identity store at %s is corrupt or undecryptable" % self.path) from exc
        if not isinstance(payload, dict) or payload.get("version") != IDENTITY_VERSION:
            raise ValueError("identity store at %s has an unsupported format" % self.path)
        identities = payload.get("identities", [])
        for ident in identities:
            self._validate_identity(ident)
        self._identities = identities
        self._dirty = False

    def save(self) -> None:
        payload = json.dumps(
            {"version": IDENTITY_VERSION, "identities": self._identities}, ensure_ascii=False
        ).encode("utf-8")
        blob = _vault.seal(payload, self.path)
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
            fh.write(blob)
        os.replace(tmp, self.path)
        self._dirty = False

    # -- validation --------------------------------------------------------

    @staticmethod
    def _validate_identity(ident: Dict) -> None:
        if ident.get("version") != IDENTITY_VERSION:
            raise ValueError("identity has unsupported version")
        if not ident.get("name"):
            raise ValueError("identity is missing a name")
        embs = ident.get("embeddings", [])
        if not isinstance(embs, list):
            raise ValueError("identity embeddings must be a list")
        for e in embs:
            if len(e) != EMBEDDING_DIM:
                raise ValueError(
                    "identity embedding has %d dims, expected %d" % (len(e), EMBEDDING_DIM)
                )

    # -- CRUD --------------------------------------------------------------

    def names(self) -> List[str]:
        return [i["name"] for i in self._identities]

    def get(self, name: str) -> Optional[Dict]:
        for ident in self._identities:
            if ident["name"] == name:
                return ident
        return None

    def add_identity(self, name: str, embeddings: List[np.ndarray],
                     model_id: str = MODEL_IDENTIFIER) -> Dict:
        if self.get(name) is not None:
            raise ValueError("identity %r already exists" % name)
        ident = {
            "version": IDENTITY_VERSION,
            "name": name,
            # Model that produced these embeddings. Mirrors Glance's
            # FaceIdentity.modelIdentifier: embeddings from a different model
            # are stale and must never match (see recognition.best_match).
            "model": model_id,
            "embeddings": [np.asarray(e, dtype=np.float32).tolist() for e in embeddings],
            "enabled": True,
            "created": _utcnow_iso(),
        }
        self._validate_identity(ident)
        self._identities.append(ident)
        self.save()
        return ident

    def add_embedding(self, name: str, embedding: np.ndarray) -> None:
        ident = self.get(name)
        if ident is None:
            raise KeyError("no identity named %r" % name)
        emb = np.asarray(embedding, dtype=np.float32).tolist()
        if len(emb) != EMBEDDING_DIM:
            raise ValueError("embedding must have %d dims" % EMBEDDING_DIM)
        ident["embeddings"].append(emb)
        self.save()

    def remove_identity(self, name: str) -> bool:
        before = len(self._identities)
        self._identities = [i for i in self._identities if i["name"] != name]
        if len(self._identities) != before:
            self.save()
            return True
        return False

    def set_enabled(self, name: str, enabled: bool) -> None:
        ident = self.get(name)
        if ident is None:
            raise KeyError("no identity named %r" % name)
        ident["enabled"] = bool(enabled)
        self.save()

    def templates(self, include_disabled: bool = False) -> List[IdentityTemplate]:
        """Enabled identities as recognition templates (embeddings only).

        Disabled identities are excluded by default (toggling off must stop
        matching); pass include_disabled=True for diagnostics views.
        """
        out = []
        for ident in self._identities:
            enabled = bool(ident.get("enabled", True))
            if not enabled and not include_disabled:
                continue
            out.append(
                IdentityTemplate(
                    name=ident["name"],
                    embeddings=[np.array(e, dtype=np.float32) for e in ident["embeddings"]],
                    enabled=enabled,
                    # Lenient on old files: assume the current model.
                    model_id=ident.get("model", MODEL_IDENTIFIER),
                )
            )
        return out

    def embedding_count(self, name: str) -> int:
        ident = self.get(name)
        return len(ident["embeddings"]) if ident else 0
