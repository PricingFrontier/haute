"""Atomic file write primitives and Writer context manager.

Implements Foundation tasks F2 (``atomic_write_bytes`` /
``atomic_write_text``) and F6 (``Writer`` with self-write callback).

All writes follow the temp-then-rename pattern proven in
``haute._polars_utils.atomic_write``: payload is staged to a sibling
``.tmp`` file in the same directory as the target (so ``Path.replace``
is a same-filesystem atomic operation), then renamed onto the target.
On any failure the temp file is unlinked and the original is left
intact.

Temp filenames embed the process pid and a uuid4 fragment so that
concurrent writers to the same target never collide on the staging
file. The committed target is still last-rename-wins (one full payload).

The parent directory is never silently created — callers must ensure
the target directory exists. Failing loudly is preferable to a silent
mkdir that masks configuration bugs.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from pathlib import Path
from types import TracebackType


def _temp_path_for(target: Path) -> Path:
    """Return a unique sibling temp path for *target*.

    Uniqueness is provided by the process pid and a uuid4 hex fragment,
    so that concurrent writers from different threads or processes do
    not clobber each other's staging files.
    """
    return target.with_name(f"{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write *data* to *path*.

    Stages bytes in a sibling temp file then renames onto *path*.  On
    any failure the temp file is unlinked and the original *path* is
    untouched.  The parent directory of *path* must already exist.
    """
    tmp = _temp_path_for(path)
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    """Atomically write *data* (text) to *path* using *encoding*."""
    atomic_write_bytes(path, data.encode(encoding))


class Writer:
    """Context manager for a single atomic file write.

    Within the ``with`` block the caller invokes ``write_text`` or
    ``write_bytes`` zero or more times.  Only the LAST call's payload
    is committed (last-wins buffering).  On clean exit the optional
    ``mark_self_write`` callback fires BEFORE the rename — this lets
    file-watcher coordination register the incoming write before the
    fs event is emitted.

    On exit with an exception, no file is written, ``mark_self_write``
    is not called, and any staged temp file is removed.
    """

    def __init__(
        self,
        path: Path,
        mark_self_write: Callable[[Path], None] | None = None,
    ) -> None:
        self._path = path
        self._mark_self_write = mark_self_write
        self._payload: bytes | None = None

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        """Buffer *data* as the pending payload (last call wins)."""
        self._payload = data.encode(encoding)

    def write_bytes(self, data: bytes) -> None:
        """Buffer *data* as the pending payload (last call wins)."""
        self._payload = data

    def __enter__(self) -> Writer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None or self._payload is None:
            return
        if self._mark_self_write is not None:
            self._mark_self_write(self._path)
        atomic_write_bytes(self._path, self._payload)
