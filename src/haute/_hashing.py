"""Content-based hashing helper.

Produces deterministic 64-bit content fingerprints (xxh64, seed=0) for files
and in-memory bytes. Used as a TOCTOU-safe replacement for mtime-based cache
keys.

Public API::

    HASH_ALGO                                   # "xxh64"
    content_hasher() -> xxhash.xxh64            # fresh seeded hasher
    content_hash_bytes(data: bytes) -> str      # 16-char lowercase hex
    content_hash(path: Path) -> str             # streamed file digest
    HashingWriter(target)                       # IO[bytes] sink wrapper

OS-level errors (``FileNotFoundError``, ``IsADirectoryError``,
``PermissionError``, ``OSError``) propagate unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Any

import xxhash

HASH_ALGO = "xxh64"

_CHUNK_SIZE = 64 * 1024


def content_hasher() -> xxhash.xxh64:
    return xxhash.xxh64(seed=0)


def content_hash_bytes(data: bytes) -> str:
    return xxhash.xxh64(data, seed=0).hexdigest()


def content_hash(path: Path) -> str:
    h = content_hasher()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


class HashingWriter:
    """IO[bytes]-compatible wrapper around an open binary file that hashes written bytes."""

    def __init__(self, target: IO[bytes]) -> None:
        self._target = target
        self._hasher = content_hasher()

    def write(self, b: bytes | bytearray | memoryview) -> int:
        self._hasher.update(b)
        return self._target.write(b)

    def flush(self) -> None:
        self._target.flush()

    def close(self) -> None:
        self._target.close()

    def tell(self) -> int:
        return self._target.tell()

    @property
    def closed(self) -> bool:
        return self._target.closed

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()

    def __enter__(self) -> HashingWriter:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
