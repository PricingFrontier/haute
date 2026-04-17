"""Content-based hashing helper.

Produces deterministic 64-bit content fingerprints (xxh64, seed=0) for files
and in-memory bytes. Used as a TOCTOU-safe replacement for mtime-based cache
keys.

Public API::

    HASH_ALGO                                   # "xxh64"
    content_hash_bytes(data: bytes) -> str      # 16-char lowercase hex
    content_hash(path: Path) -> str             # streamed file digest

OS-level errors (``FileNotFoundError``, ``IsADirectoryError``,
``PermissionError``, ``OSError``) propagate unchanged.
"""

from __future__ import annotations

from pathlib import Path

import xxhash

HASH_ALGO = "xxh64"

_CHUNK_SIZE = 64 * 1024


def content_hash_bytes(data: bytes) -> str:
    return xxhash.xxh64(data, seed=0).hexdigest()


def content_hash(path: Path) -> str:
    h = xxhash.xxh64(seed=0)
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()
