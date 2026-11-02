"""Cross-platform OS file locks: the one helper for every cross-process lock.

:class:`FileLock` is the exclusive lock the project mutation lock, the
input-snapshot store's lease and publication locks, and the runtime storage
budget all use. The OS releases a lock when its holder exits, so no owner can
go stale. Lock files and the cache directories above them are refused when
they are reached through a link or reparse point.
"""

from __future__ import annotations

import os
import shutil
import stat as stat_module
import threading
import time
from pathlib import Path
from typing import Any, Literal, cast
from weakref import WeakValueDictionary


def _acquire_file_lock(
    handle: Any,
    *,  # pragma: no mutate
    blocking: bool = True,
    timeout_seconds: float | None = None,  # pragma: no mutate
) -> bool:
    """Acquire one OS file lock, optionally without waiting.

    ``timeout_seconds`` applies only when ``blocking`` is true.  The polling
    implementation is shared by POSIX and Windows so the public lock wrapper
    retains the timeout/non-blocking contract of the ``RLock`` it replaced.
    """
    deadline = (
        None if not blocking or timeout_seconds is None else time.monotonic() + timeout_seconds
    )
    if os.name != "nt":
        import fcntl

        fcntl_module = cast(Any, fcntl)
        if blocking and deadline is None:
            fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_EX)
            return True
        while True:
            try:
                fcntl_module.flock(
                    handle.fileno(),
                    fcntl_module.LOCK_EX | fcntl_module.LOCK_NB,
                )
                return True
            except OSError as exc:
                if exc.errno not in {11, 13}:
                    raise
                if not blocking or (deadline is not None and time.monotonic() >= deadline):
                    return False
                time.sleep(0.01)

    import msvcrt

    msvcrt_module = cast(Any, msvcrt)
    while True:
        handle.seek(0)
        try:
            msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {33, 36} and exc.errno not in {
                11,
                13,
                36,
            }:
                raise
            if not blocking or (deadline is not None and time.monotonic() >= deadline):
                return False
            time.sleep(0.01)


def _release_file_lock(handle: Any) -> None:
    if os.name != "nt":
        import fcntl

        fcntl_module = cast(Any, fcntl)
        fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_UN)
        return

    import msvcrt

    msvcrt_module = cast(Any, msvcrt)
    handle.seek(0)
    msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_UNLCK, 1)


class UnsafeCachePathError(RuntimeError):
    """A cache path or lock file is not a plain entry and cannot be trusted."""


class _LockFileReplacedError(UnsafeCachePathError):
    """The lock file was removed, or removed and recreated, while it was being opened.

    A sweep may remove a lock file at any time (:func:`remove_unheld_lock_file`),
    so :meth:`FileLock.acquire` opens the path again rather than failing.
    """


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    return bool(getattr(path_stat, "st_file_attributes", 0) & 0x400)


def _plain_directory_stat(path: Path) -> os.stat_result:
    path_stat = path.lstat()
    if (
        not stat_module.S_ISDIR(path_stat.st_mode)
        or stat_module.S_ISLNK(path_stat.st_mode)
        or _is_reparse_point(path_stat)
    ):
        raise UnsafeCachePathError(f"Cache recovery refused non-plain directory entry: {path}")
    return path_stat


def _remove_plain_cache_directory(path: Path) -> None:
    _plain_directory_stat(path)
    shutil.rmtree(path)


def _assert_path_ancestors_plain(path: Path) -> None:
    """Reject a cache root or existing descendant reached through a link/reparse point.

    The walk stops at the nearest ``.haute_cache`` ancestor, or at *path*'s
    parent when it has none.
    """
    absolute = Path(os.path.abspath(path))
    cache_root = next(
        (
            candidate
            for candidate in (absolute, *absolute.parents)
            if candidate.name == ".haute_cache"
        ),
        absolute.parent,
    )
    chain: list[Path] = []
    current = absolute.parent
    while True:
        chain.append(current)
        if current == cache_root or current.parent == current:
            break
        current = current.parent
    for directory in reversed(chain):
        if not directory.exists() and not directory.is_symlink():
            continue
        _plain_directory_stat(directory)


def _lock_file_stat(path: Path, path_stat: os.stat_result) -> None:
    if (
        not stat_module.S_ISREG(path_stat.st_mode)
        or stat_module.S_ISLNK(path_stat.st_mode)
        or _is_reparse_point(path_stat)
    ):
        raise UnsafeCachePathError(f"Lock path is not a plain regular file: {path}")


def _open_lock_file(lock_path: Path) -> Any:
    """Open one stable lock inode without trusting a link or replacement path."""
    try:
        existing_stat = lock_path.lstat()
    except FileNotFoundError:
        existing_stat = None
    if existing_stat is not None:
        _lock_file_stat(lock_path, existing_stat)

    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise UnsafeCachePathError(f"Lock path could not be opened safely: {lock_path}") from exc
    try:
        try:
            path_stat = lock_path.lstat()
        except FileNotFoundError as exc:
            raise _LockFileReplacedError(
                f"Lock path changed while it was being opened: {lock_path}"
            ) from exc
        except OSError as exc:
            raise UnsafeCachePathError(
                f"Lock path changed while it was being opened: {lock_path}"
            ) from exc
        descriptor_stat = os.fstat(descriptor)
        _lock_file_stat(lock_path, path_stat)
        _lock_file_stat(lock_path, descriptor_stat)
        if (
            path_stat.st_ino
            and descriptor_stat.st_ino
            and (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise _LockFileReplacedError(
                f"Lock path changed file identity while it was being opened: {lock_path}"
            )
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


# A sweep removes a lock file at most once, so a path that names another file
# after this many locks is not being swept: it is failing.
_RELOCK_ATTEMPTS = 5


def _path_names_handle(path: Path, handle: Any) -> bool:
    """Whether *path* still names the file *handle* has open."""
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False
    handle_stat = os.fstat(handle.fileno())
    if not (path_stat.st_ino and handle_stat.st_ino):
        return True
    return (path_stat.st_dev, path_stat.st_ino) == (handle_stat.st_dev, handle_stat.st_ino)


def remove_unheld_lock_file(path: Path) -> bool:
    """Remove the lock file *path* unless a process holds it; whether it was removed.

    The removal can never leave two processes holding different files for one
    path. On POSIX it happens while this process holds the file's lock: a
    process that opened the file before then gets the lock only after the
    removal, finds that the path no longer names its file, and locks the path
    again (:meth:`FileLock.acquire`). Windows refuses to delete a file any
    process has open, and a process opens a lock file before it locks it, so a
    deletion that succeeds there removes a file no process holds or waits on.
    """
    # Only a plain file reached through plain directories is removed, never a
    # link or anything a link leads to.
    _assert_path_ancestors_plain(path)
    try:
        _lock_file_stat(path, path.lstat())
    except FileNotFoundError:
        return False
    if os.name == "nt":
        try:
            path.unlink()
        except (FileNotFoundError, PermissionError):
            return False
        return True
    lock = FileLock(path)
    if not lock.acquire(blocking=False):
        return False
    try:
        path.unlink(missing_ok=True)
    finally:
        lock.release()
    return True


class FileLock:
    """Thread-reentrant, cross-process exclusive lock on one plain lock file.

    The one exclusive-lock helper: the project mutation lock, the input-snapshot
    store's lease and publication locks, and the runtime storage budget all use
    it. A thread lock serialises callers in this process and the OS lock
    (``flock``/``msvcrt.locking``) serialises processes. The OS releases the
    lock when its holder exits, so no owner can go stale. ``acquire`` accepts
    ``RLock``-compatible ``blocking``/``timeout`` controls.
    """

    def __init__(self, lock_path: Path) -> None:
        self._path = Path(os.path.abspath(lock_path))
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle: Any | None = None  # pragma: no mutate
        self._owner_thread_id: int | None = None  # pragma: no mutate

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def owned_by_current_thread(self) -> bool:
        """Return whether this thread of this process holds the lock."""
        return self._depth > 0 and self._owner_thread_id == threading.get_ident()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the thread and process lock; ``False`` when it was not acquired.

        A lock file may be removed (:func:`remove_unheld_lock_file`) while this
        process waits on it. Holding the OS lock on a file the path no longer
        names would let another process lock the file that replaced it, so after
        locking, a file the path no longer names is let go and the path locked again.
        """
        if not blocking and timeout != -1:
            raise ValueError("can't specify a timeout for a non-blocking call")
        started_at = time.monotonic()
        if not blocking:
            thread_acquired = self._thread_lock.acquire(blocking=False)
        elif timeout == -1:
            thread_acquired = self._thread_lock.acquire()
        else:
            thread_acquired = self._thread_lock.acquire(timeout=timeout)
        if not thread_acquired:
            return False
        if self._depth:
            self._depth += 1
            return True
        handle: Any | None = None  # pragma: no mutate
        try:
            for _attempt in range(_RELOCK_ATTEMPTS):
                _assert_path_ancestors_plain(self._path)
                self._path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    handle = _open_lock_file(self._path)
                except _LockFileReplacedError:
                    continue
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                remaining_timeout = None
                if timeout != -1:
                    remaining_timeout = max(0.0, timeout - (time.monotonic() - started_at))
                if not _acquire_file_lock(
                    handle,
                    blocking=blocking,
                    timeout_seconds=remaining_timeout,
                ):
                    handle.close()
                    self._thread_lock.release()
                    return False
                if _path_names_handle(self._path, handle):
                    break
                _release_file_lock(handle)
                handle.close()
                handle = None
            else:
                raise UnsafeCachePathError(
                    f"Lock path kept naming another file after it was locked: {self._path}"
                )
            self._handle = handle
            self._depth = 1
            self._owner_thread_id = threading.get_ident()
            return True
        except BaseException:
            if handle is not None:
                try:
                    _release_file_lock(handle)
                except BaseException:
                    pass
                handle.close()
            self._thread_lock.release()
            raise

    def release(self) -> None:
        """Release one re-entrant acquisition."""
        self._release(primary_exception=None)

    def _release(self, *, primary_exception: BaseException | None) -> None:  # pragma: no mutate
        if self._owner_thread_id != threading.get_ident() or self._depth <= 0:
            raise RuntimeError("cannot release an unacquired file lock")
        try:
            self._depth -= 1
            if self._depth:
                return
            handle, self._handle = self._handle, None
            self._owner_thread_id = None
            if handle is None:
                raise RuntimeError("file lock lost its file handle")
            try:
                _release_file_lock(handle)
            except BaseException as release_exc:
                if primary_exception is None:
                    raise
                primary_exception.add_note(f"file lock release failed: {release_exc}")
            finally:
                handle.close()
        finally:
            self._thread_lock.release()

    def __exit__(
        self,
        exc_type: Any,
        exc: BaseException | None,  # pragma: no mutate
        traceback: Any,
    ) -> Literal[False]:
        del exc_type, traceback
        self._release(primary_exception=exc)
        return False


# One lock per canonical lock path, so every caller in a process shares it.
_LOCKS: WeakValueDictionary[str, FileLock] = WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()
_LOCKS_PROCESS_ID = os.getpid()


def file_lock_for(lock_path: Path) -> FileLock:
    """Return this process's :class:`FileLock` for *lock_path*."""
    global _LOCKS_PROCESS_ID, _LOCKS_GUARD, _LOCKS
    if os.getpid() != _LOCKS_PROCESS_ID:
        # A forked child must not acquire locks inherited from vanished parent
        # threads or reuse inherited file-lock ownership.
        _LOCKS_PROCESS_ID = os.getpid()
        _LOCKS_GUARD = threading.Lock()
        _LOCKS = WeakValueDictionary()
    absolute = Path(os.path.abspath(lock_path))
    key = os.path.normcase(str(absolute))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, FileLock(absolute))
