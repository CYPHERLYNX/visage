"""
Download the ONNX models used by the Visage engine.

Models are NEVER bundled in the repo. Run this once per machine::

    python -m engine.fetch_models        # or: python engine/fetch_models.py

Downloads to ``C:\\ProgramData\\Visage\\models\\`` on Windows, ``./models``
(dev, next to this file) elsewhere. Each file is verified against a pinned
SHA-256 hash, retried up to 3 times, and any failure is reported in plain
language.

Models
------
YuNet face detector (OpenCV zoo, 2023mar):
    https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
ArcFace recognizer (InsightFace buffalo_l, w600k_r50, 512-d):
    https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
    (the w600k_r50.onnx member is extracted; the zip itself is deleted)
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from typing import List, Optional

MODELS = {
    # NOTE: "sha256" is always the hash of the FINAL artifact written to the
    # model dir (for "zip_member", the extracted member - not the zip).
    # It is verified after download+extraction; on mismatch the file is
    # deleted and setup fails closed.
    "face_detection_yunet_2023mar.onnx": {
        "url": "https://github.com/opencv/opencv_zoo/raw/main/models/"
               "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "kind": "file",
        "size_mb": 0.4,
    },
    "w600k_r50.onnx": {
        "url": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        "sha256": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
        "kind": "zip_member",
        "zip_member": "w600k_r50.onnx",
        "size_mb": 174.0,
    },
}

MAX_RETRIES = 3
CHUNK = 1024 * 256


def default_model_dir() -> str:
    if os.name == "nt":
        return r"C:\ProgramData\Visage\models"
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "models")


class FetchError(RuntimeError):
    pass


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: str, label: str) -> None:
    # Pinned HTTPS only: models must come from the exact trusted URLs above.
    if not url.startswith("https://"):
        raise FetchError("Refusing to download %s: URL is not HTTPS." % label)
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print("Downloading %s (attempt %d/%d) ..." % (label, attempt, MAX_RETRIES))
            req = urllib.request.Request(url, headers={"User-Agent": "VisageEngine/0.1"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
                total = 0
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
            print("  received %.1f MB" % (total / 1024 / 1024))
            return
        except Exception as exc:  # noqa: BLE001 - retried, reported at the end
            last_exc = exc
            print("  attempt %d didn't work: %s" % (attempt, exc))
    raise FetchError(
        "Couldn't download %s. Check your internet connection and try again. "
        "(Last error: %s)" % (label, last_exc)
    )


def _extract_member(zip_path: str, member: str, dest: str,
                    max_mb: float) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            match = next((n for n in names if n.rsplit("/", 1)[-1] == member), None)
            if match is None:
                raise FetchError(
                    "The downloaded archive does not contain %s (contents: %s). "
                    "The upstream release may have changed; update fetch_models.py." % (member, names[:8])
                )
            # Bounded copy: the header's file_size is untrusted, so enforce
            # the cap while copying. A decompression bomb fails here instead
            # of filling the disk (the hash check afterwards would catch any
            # other tampering).
            limit = int(max_mb * 1024 * 1024)
            total = 0
            with zf.open(match) as src, open(dest, "wb") as fh:
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise FetchError(
                            "The archive member is far larger than expected "
                            "(%.0f MB cap); refusing to extract." % max_mb)
                    fh.write(chunk)
    except zipfile.BadZipFile as exc:
        raise FetchError("The downloaded file is not a valid zip archive: %s" % exc) from exc


def fetch_model(name: str, model_dir: str) -> str:
    """Download and verify one model. Returns its path. Skips if already valid."""
    spec = MODELS[name]
    dest = os.path.join(model_dir, name)
    if os.path.isfile(dest):
        if sha256_of(dest) == spec["sha256"]:
            print("%s is already present and verified." % name)
            return dest
        print("%s exists but the hash does not match - re-downloading." % name)
        os.remove(dest)

    # If the model dir path exists as a *file* (not a directory), remove it;
    # makedirs(exist_ok=True) would otherwise fail with FileExistsError.
    if os.path.isfile(model_dir):
        os.remove(model_dir)
    os.makedirs(model_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visage_models_") as tmp:
        tmp_file = os.path.join(tmp, name + ".download")
        _download(spec["url"], tmp_file, name)
        if spec["kind"] == "zip_member":
            _extract_member(tmp_file, spec["zip_member"], dest,
                            spec["size_mb"] * 2)
        else:
            shutil.move(tmp_file, dest)

    actual = sha256_of(dest)
    if actual != spec["sha256"]:
        # Fail closed: the unverified file is deleted, never used.
        os.remove(dest)
        raise FetchError(
            "The download didn't match its expected signature, so it was deleted. "
            "It may be corrupt — please try again. "
            "(expected %.16s..., got %.16s...)" % (spec["sha256"], actual)
        )
    print("%s verified." % name)
    return dest


def fetch_all(model_dir: Optional[str] = None) -> List[str]:
    model_dir = model_dir or default_model_dir()
    print("Model directory: %s" % model_dir)
    paths = []
    for name in MODELS:
        try:
            paths.append(fetch_model(name, model_dir))
        except FetchError:
            raise
        except OSError as exc:
            raise FetchError(
                "Couldn't write to %s (%s). Try running as administrator, then try again."
                % (model_dir, exc)
            ) from exc
    print("All models ready.")
    return paths


def main(argv: List[str]) -> int:
    model_dir = argv[1] if len(argv) > 1 else None
    try:
        fetch_all(model_dir)
    except FetchError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
