"""Cross-process file locks and plain-path checks for cache directories.

A thread-reentrant OS file lock per canonical path serialises independent
processes (the runtime storage budget uses one), and the path checks refuse a
cache root or lock file reached through a link or reparse point before either
is trusted. The shared input-snapshot store opens its lock files here too."""

from __future__ import annotations

import os
import shutil
import stat as stat_module
import threading
import time
from pathlib import Path
from typing import Any, Literal
from weakref import WeakValueDictionary

from haute._file_lock import _acquire_file_lock, _release_file_lock
from haute._logging import get_logger

logger = get_logger(component="json_shred")


# One re-entrant lock per canonical path. The thread lock protects same-process
# callers; the stable sibling file lock protects independent CLI, server, and
# test processes. Different paths retain independent locks.
_BUILD_LOCKS: WeakValueDictionary[str, _CacheBuildLock]


_BUILD_LOCKS_GUARD = threading.Lock()


_BUILD_LOCKS_PROCESS_ID = os.getpid()


class JsonCacheRecoveryError(RuntimeError):
    """A cache path or lock file is not a plain entry and cannot be trusted."""


def _cache_lock_path(cache_dir: Path) -> Path:
    return cache_dir.with_name(f".{cache_dir.name}.build.lock")


def _cache_lock_file_stat(path: Path, path_stat: os.stat_result) -> None:
    if (
        not stat_module.S_ISREG(path_stat.st_mode)
        or stat_module.S_ISLNK(path_stat.st_mode)
        or _is_reparse_point(path_stat)
    ):
        raise JsonCacheRecoveryError(f"Cache lock path is not a plain regular file: {path}")


def _open_cache_lock_file(lock_path: Path) -> Any:
    """Open one stable lock inode without trusting a link or replacement path."""
    try:
        existing_stat = lock_path.lstat()
    except FileNotFoundError:
        existing_stat = None
    if existing_stat is not None:
        _cache_lock_file_stat(lock_path, existing_stat)

    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise JsonCacheRecoveryError(
            f"Cache lock path could not be opened safely: {lock_path}"
        ) from exc
    try:
        try:
            path_stat = lock_path.lstat()
        except OSError as exc:
            raise JsonCacheRecoveryError(
                f"Cache lock path changed while it was being opened: {lock_path}"
            ) from exc
        descriptor_stat = os.fstat(descriptor)
        _cache_lock_file_stat(lock_path, path_stat)
        _cache_lock_file_stat(lock_path, descriptor_stat)
        if (
            path_stat.st_ino
            and descriptor_stat.st_ino
            and (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise JsonCacheRecoveryError(
                f"Cache lock path changed file identity while it was being opened: {lock_path}"
            )
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _assert_cache_path_ancestors_plain(path: Path) -> None:
    """Reject a cache root or existing descendant reached through a link/reparse point."""
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
        if current == cache_root:
            break
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(chain):
        if not directory.exists() and not directory.is_symlink():
            continue
        _plain_directory_stat(directory)


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    return bool(getattr(path_stat, "st_file_attributes", 0) & 0x400)


def _plain_directory_stat(path: Path) -> os.stat_result:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        raise
    if (
        not stat_module.S_ISDIR(path_stat.st_mode)
        or stat_module.S_ISLNK(path_stat.st_mode)
        or _is_reparse_point(path_stat)
    ):
        raise JsonCacheRecoveryError(f"Cache recovery refused non-plain directory entry: {path}")
    return path_stat


def _remove_plain_cache_directory(path: Path) -> None:
    _plain_directory_stat(path)
    shutil.rmtree(path)


class _CacheBuildLock:
    """Thread-reentrant wrapper around one cross-process cache file lock."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle: Any | None = None  # pragma: no mutate
        self._owner_thread_id: int | None = None  # pragma: no mutate

    def __enter__(self) -> _CacheBuildLock:
        acquired = self.acquire()
        if not acquired:  # pragma: no cover - an unbounded acquire cannot time out
            raise RuntimeError("cache build lock could not be acquired")
        return self

    def owned_by_current_thread(self) -> bool:
        """Return whether this process/thread owns the lock."""
        return self._depth > 0 and self._owner_thread_id == threading.get_ident()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the thread and process lock with ``RLock``-compatible controls."""
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
            lock_path = _cache_lock_path(self._cache_dir)
            _assert_cache_path_ancestors_plain(lock_path)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = _open_cache_lock_file(lock_path)
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
            raise RuntimeError("cannot release un-acquired cache build lock")
        try:
            self._depth -= 1
            if self._depth:
                return
            handle, self._handle = self._handle, None
            self._owner_thread_id = None
            if handle is None:
                raise RuntimeError("cache build lock lost its file handle")
            try:
                _release_file_lock(handle)
            except BaseException as release_exc:
                if primary_exception is None:
                    raise
                primary_exception.add_note(f"cache file lock release failed: {release_exc}")
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


_BUILD_LOCKS = WeakValueDictionary()


def _build_lock_for(cache_dir: Path) -> _CacheBuildLock:
    global _BUILD_LOCKS_PROCESS_ID, _BUILD_LOCKS_GUARD, _BUILD_LOCKS
    if os.getpid() != _BUILD_LOCKS_PROCESS_ID:
        # A forked child must not acquire locks inherited from vanished parent
        # threads or reuse inherited file-lock ownership.
        _BUILD_LOCKS_PROCESS_ID = os.getpid()
        _BUILD_LOCKS_GUARD = threading.Lock()
        _BUILD_LOCKS = WeakValueDictionary()
    absolute = Path(os.path.abspath(cache_dir))
    key = os.path.normcase(str(absolute))
    with _BUILD_LOCKS_GUARD:
        return _BUILD_LOCKS.setdefault(key, _CacheBuildLock(absolute))
