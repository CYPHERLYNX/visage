"""Tests for fetch_models: URL pinning, hashing, fail-closed behavior."""

import hashlib
import os

import pytest

from engine import fetch_models


def test_models_table_has_pinned_https_urls_and_hashes():
    for name, spec in fetch_models.MODELS.items():
        assert spec["url"].startswith("https://"), name
        assert len(spec["sha256"]) == 64, name
        int(spec["sha256"], 16)  # valid hex


def test_expected_hashes_match_downloaded_models():
    # The real files were downloaded during the build; verify the pins.
    model_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
    for name, spec in fetch_models.MODELS.items():
        path = os.path.join(model_dir, name)
        if not os.path.isfile(path):
            pytest.skip("model not downloaded: %s" % name)
        assert fetch_models.sha256_of(path) == spec["sha256"]


def test_sha256_of():
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"abc")
        path = fh.name
    assert fetch_models.sha256_of(path) == hashlib.sha256(b"abc").hexdigest()
    os.remove(path)


def test_fetch_model_skips_valid_existing_file(tmp_path, monkeypatch):
    # No network: stub out the downloader; a valid existing file is untouched.
    content = b"model-bytes"
    monkeypatch.setattr(fetch_models, "_download",
                        lambda url, dest, label: (_ for _ in ()).throw(
                            AssertionError("should not download")))
    name = "test.onnx"
    dest = tmp_path / name
    dest.write_bytes(content)
    fetch_models.MODELS[name] = {
        "url": "https://example.com/test.onnx",
        "sha256": hashlib.sha256(content).hexdigest(),
        "kind": "file",
    }
    try:
        got = fetch_models.fetch_model(name, str(tmp_path))
        assert open(got, "rb").read() == content
    finally:
        del fetch_models.MODELS[name]


def test_fetch_model_fails_closed_on_hash_mismatch(tmp_path, monkeypatch):
    def fake_download(url, dest, label):
        with open(dest, "wb") as fh:
            fh.write(b"tampered-bytes")
    monkeypatch.setattr(fetch_models, "_download", fake_download)
    name = "test_bad.onnx"
    dest_path = os.path.join(str(tmp_path), name)
    fetch_models.MODELS[name] = {
        "url": "https://example.com/test_bad.onnx",
        "sha256": "0" * 64,
        "kind": "file",
    }
    try:
        with pytest.raises(fetch_models.FetchError):
            fetch_models.fetch_model(name, str(tmp_path))
        assert not os.path.exists(dest_path)  # partial file deleted
    finally:
        del fetch_models.MODELS[name]


def test_download_refuses_non_https(tmp_path):
    with pytest.raises(fetch_models.FetchError):
        fetch_models._download("http://example.com/x.onnx", str(tmp_path / "x"), "x")
