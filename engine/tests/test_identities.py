"""Tests for the encrypted identity store."""

import json

import numpy as np
import pytest

from engine.identities import IdentityStore
from engine.recognition import MODEL_IDENTIFIER


def _emb(seed=0):
    v = np.random.RandomState(seed).randn(512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_add_get_names(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0), _emb(1)])
    assert store.names() == ["Fahad"]
    assert store.embedding_count("Fahad") == 2


def test_duplicate_name_rejected(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0)])
    with pytest.raises(ValueError):
        store.add_identity("Fahad", [_emb(1)])


def test_bad_embedding_dim_rejected(tmp_store):
    store = IdentityStore(tmp_store)
    with pytest.raises(ValueError):
        store.add_identity("X", [np.zeros(100, dtype=np.float32)])


def test_model_id_recorded_and_surfaced(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0)], model_id="test-model-v1")
    (t,) = store.templates()
    assert t.model_id == "test-model-v1"
    assert t.is_stale(MODEL_IDENTIFIER)


def test_templates_carry_current_model_id(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0)])
    (t,) = store.templates()
    assert t.model_id == MODEL_IDENTIFIER
    assert not t.is_stale(MODEL_IDENTIFIER)


def test_enable_disable(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0)])
    store.set_enabled("Fahad", False)
    assert store.templates() == []
    store.set_enabled("Fahad", True)
    assert len(store.templates()) == 1


def test_add_embedding(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0)])
    store.add_embedding("Fahad", _emb(1))
    assert store.embedding_count("Fahad") == 2


def test_persist_roundtrip_encrypted(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0), _emb(1)])
    raw = open(tmp_store, "rb").read()
    assert b"Fahad" not in raw  # encrypted at rest
    store2 = IdentityStore(tmp_store)
    assert store2.names() == ["Fahad"]
    assert store2.embedding_count("Fahad") == 2


def test_remove_identity(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0)])
    assert store.remove_identity("Fahad")
    assert store.names() == []
    assert not store.remove_identity("Fahad")


def test_corrupt_store_raises(tmp_store):
    with open(tmp_store, "wb") as fh:
        fh.write(b"corrupt")
    with pytest.raises(ValueError):
        IdentityStore(tmp_store)


def test_no_images_stored(tmp_store):
    store = IdentityStore(tmp_store)
    store.add_identity("Fahad", [_emb(0)])
    ident = store.get("Fahad")
    assert set(ident.keys()) == {"version", "name", "model", "embeddings", "enabled", "created"}
    assert "image" not in json.dumps(ident).lower()
