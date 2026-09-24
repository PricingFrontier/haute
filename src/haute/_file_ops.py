"""Atomic file write primitives and Writer context manager.

Implements Foundation tasks F2 (``atomic_write_bytes`` /
``atomic_write_text``) and F6 (``Writer`` with self-write callback).

Every atomic write goes through :func:`atomic_path`: the payload is staged
to a unique sibling ``.tmp`` file in the same directory as the target (so
``Path.replace`` is a same-filesystem rename), then renamed onto the target.
On any failure the temp file is unlinked and the original is left intact.
``atomic_write_bytes`` and ``haute._polars_utils.atomic_write`` are built on
it.

Cross-OS guarantee (be precise — these differ):

* A reader NEVER observes torn/partial bytes on any OS. It always sees
  either the complete previous payload or the complete new one, because
  the new bytes only become visible at the instant of the rename.
* On POSIX, ``rename(2)`` is atomic even under concurrent readers, so
  the replace also always *succeeds*.
* On Windows, ``Path.replace`` → ``MoveFileExW(MOVEFILE_REPLACE_EXISTING)``
  is NOT robust under reader contention. If another process/thread holds
  the target open without ``FILE_SHARE_DELETE`` — which is the default
  for Python ``open()`` / ``read_bytes`` / ``read_text`` — the replace
  fails with ``PermissionError`` (ERROR_ACCESS_DENIED) or an
  ERROR_SHARING_VIOLATION. Those two errors receive a short bounded retry
  of the same atomic replace, accommodating transient antivirus/indexer
  handles without introducing a non-atomic fallback. An exhausted retry
  still fails loudly and leaves the old complete payload intact.

Temp filenames embed a random uuid4 fragment so that concurrent writers
to the same target never collide on the staging file. The committed
target is still last-rename-wins (one full payload).

The parent directory is never silently created — callers must ensure
the target directory exists. Failing loudly is preferable to a silent
mkdir that masks configuration bugs.
"""

from __future__ import annotations

import errno
import os
import shutil
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05, 0.1)
_IS_WINDOWS = os.name == "nt"
_DISK_HEADROOM_BYTES = 64 * 1024


def ensure_disk_headroom(directory: Path, additional_bytes: int = 0) -> None:
    """Fail before a write when its destination filesystem lacks headroom."""
    if (
        isinstance(additional_bytes, bool)
        or not isinstance(additional_bytes, int)
        or additional_bytes < 0
    ):
        raise ValueError("additional_bytes must be a non-negative integer")
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    required = additional_bytes + _DISK_HEADROOM_BYTES
    available = shutil.disk_usage(directory).free
    if available < required:
        raise OSError(
            errno.ENOSPC,
            f"insufficient disk space: required {required} bytes, available {available} bytes",
            str(directory),
        )


def _temp_path_for(target: Path) -> Path:
    """Return a unique sibling temp path for *target*.

    Uniqueness is a random uuid4 fragment, so concurrent writers from
    different threads or processes do not clobber each other's staging files.
    The name replaces the target's last suffix rather than extending it: a
    staging file sits beside deep store paths, and Windows' traditional
    260-character path limit applies to it too.
    """
    return target.with_name(f"{target.stem}.{uuid.uuid4().hex[:8]}.tmp")


def _replace_with_windows_contention_retry(source: Path, target: Path) -> None:
    """Retry only Win32 access/sharing failures from one atomic replace."""

    for delay in (*_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS, None):
        try:
            source.replace(target)
            return
        except OSError as exc:
            if not _IS_WINDOWS or getattr(exc, "winerror", None) not in {5, 32} or delay is None:
                raise
            time.sleep(delay)


def remove_tree(path: Path) -> bool:
    """Remove a directory tree best-effort, retrying transient Windows failures.

    Returns whether the tree is gone. Windows fails a delete with
    ERROR_ACCESS_DENIED or ERROR_SHARING_VIOLATION while an antivirus scanner or
    indexer briefly holds a handle on a file that was just written, so those two
    codes receive the same short bounded retry as an atomic replace. Callers use
    this for cleanup they must not abort on, and report a tree that survives.
    """
    for delay in (*_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS, None):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            # A missing *root* is the tree already being gone. A missing
            # descendant is not: Windows reports a path it cannot open — one
            # past its 260-character limit, say — the same way, and the tree
            # survives. Answering on the root keeps that visible to the caller.
            return not path.exists()
        except OSError as exc:
            if not _IS_WINDOWS or getattr(exc, "winerror", None) not in {5, 32} or delay is None:
                return not path.exists()
            time.sleep(delay)
    return not path.exists()


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """Yield a unique staging path for *path*; publish it when the block exits.

    The caller writes the complete payload to the yielded path by any means (a
    Parquet or CSV writer, ``write_bytes``). A clean exit renames it onto
    *path*; an exception unlinks it and leaves the original *path* untouched
    (a block that wrote nothing raises ``FileNotFoundError`` the same way). The
    staging name embeds a random fragment, so concurrent writers to one target
    never share a stage and the target ends as one complete payload (last
    rename wins). The parent directory of *path* must already exist.

    A reader never observes torn/partial bytes on any OS. On POSIX the
    rename also always succeeds under concurrent readers. On Windows a
    concurrent open reader (default ``open()`` omits ``FILE_SHARE_DELETE``)
    can make the rename raise ``PermissionError`` (ERROR_ACCESS_DENIED) —
    those transient codes receive a bounded retry before the write fails
    loudly. See the module docstring for the full cross-OS contract.
    """
    tmp = _temp_path_for(path)
    try:
        yield tmp
        _replace_with_windows_contention_retry(tmp, path)
    except BaseException as exc:
        try:
            tmp.unlink(missing_ok=True)
        except BaseException as cleanup_exc:
            exc.add_note(f"atomic-write staging cleanup failed: {cleanup_exc}")
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write *data* to *path* (see :func:`atomic_path`)."""
    with atomic_path(path) as tmp:
        tmp.write_bytes(data)


def atomic_copy_files(pairs: Sequence[tuple[Path, Path]]) -> None:
    """Copy each (source, target) pair transactionally: all targets change or none do.

    Every source is first staged with ``shutil.copyfile`` to a unique sibling
    temp file of its target, so a copy failure changes nothing. Each existing
    target is then moved to a unique sibling backup before its staged copy
    replaces it (both renames use the Windows sharing-violation retry of
    ``atomic_write_bytes``). If any step fails, every newly published target is
    removed, every backup is restored, and every remaining staged copy is
    deleted before the error is re-raised. Backups are deleted only after every
    replacement succeeded; a backup that cannot be deleted then is logged and
    left as a hidden sibling, because the copy has already committed. As with
    ``atomic_write_bytes``, the parent directory of each target must already exist.
    """
    staged: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for source, target in pairs:
            tmp = _temp_path_for(target)
            # Registered before copying: a copy that writes part of the file
            # and then fails still leaves a staged file to clean up.
            staged.append((tmp, target))
            shutil.copyfile(source, tmp)
        for tmp, target in staged:
            if target.exists() or target.is_symlink():
                backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.backup")
                _replace_with_windows_contention_retry(target, backup)
                backups.append((backup, target))
            _replace_with_windows_contention_retry(tmp, target)
            published.append(target)
    except BaseException as exc:
        for target in reversed(published):
            try:
                target.unlink(missing_ok=True)
            except BaseException as cleanup_exc:
                exc.add_note(f"atomic-copy rollback could not remove {target}: {cleanup_exc}")
        for backup, target in reversed(backups):
            try:
                _replace_with_windows_contention_retry(backup, target)
            except BaseException as cleanup_exc:
                exc.add_note(f"atomic-copy rollback could not restore {target}: {cleanup_exc}")
        for tmp, _target in staged:
            try:
                tmp.unlink(missing_ok=True)
            except BaseException as cleanup_exc:
                exc.add_note(f"atomic-copy staging cleanup failed: {cleanup_exc}")
        raise
    for backup, _target in backups:
        try:
            backup.unlink()
        except OSError as exc:
            from haute._logging import get_logger

            get_logger(component="file_ops").warning(
                "atomic_copy_backup_cleanup_failed",
                path=str(backup),
                error_type=type(exc).__name__,
            )


def atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    """Atomically write *data* (text) to *path* using *encoding*."""
    atomic_write_bytes(path, data.encode(encoding))


class Writer:
    """Context manager for a single atomic file write.

    Within the ``with`` block the caller invokes ``write_text`` or
    ``write_bytes`` zero or more times.  Only the LAST call's payload
    is committed (last-wins buffering).  On clean exit the optional
    ``mark_self_write`` callback fires BEFORE the rename with the target
    path and the exact committed payload — this lets file-watcher
    coordination register the incoming write, by content identity, before
    the fs event is emitted.

    On exit with an exception, no file is written, ``mark_self_write``
    is not called, and any staged temp file is removed.
    """

    def __init__(
        self,
        path: Path,
        mark_self_write: Callable[[Path, bytes], None] | None = None,
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
            self._mark_self_write(self._path, self._payload)
        atomic_write_bytes(self._path, self._payload)
