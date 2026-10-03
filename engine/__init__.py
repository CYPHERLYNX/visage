"""
Visage face-unlock engine.

A Python face-recognition + anti-spoofing engine that serves authentication
requests from a Windows credential-provider companion process. On Windows it
listens on the named pipe ``\\\\.\\pipe\\VisageAuth``; on other platforms (dev /
testing) it falls back to a TCP localhost socket, and ``--test`` mode runs the
pipeline directly on image files without any IPC.

Package layout
--------------
pipe_protocol  JSONL wire protocol between the credential provider (CP) and
               this engine (this file).
recognition    YuNet face detection, 5-landmark alignment, ArcFace 512-d
               embeddings via ONNX Runtime, cosine matching.
liveness       Anti-spoofing: deny cues (screen glare, device rectangle),
               confirm cues (blink, head-turn parallax, LBP texture) and a
               challenge-response round that defeats replayed video.
vault          Windows DPAPI (LocalMachine scope) encrypted credential vault.
               AES-GCM file fallback on non-Windows, dev-only, LOUD warning.
identities     Encrypted enrollment store: multiple 512-d embeddings per
               identity. Images are NEVER written to disk.
enroll         Guided multi-direction enrollment with per-frame quality gates.
fetch_models   Downloads the ONNX models (never bundled in the repo).
visage_engine    Entry point: pipe/TCP listener, auth state machine, --test mode.

No network calls happen at runtime. The only module that touches the network
is ``fetch_models``.
"""

__version__ = "0.1.0"
__all__ = [
    "pipe_protocol",
    "recognition",
    "liveness",
    "vault",
    "identities",
    "enroll",
    "fetch_models",
    "visage_engine",
]
