"""Cross-process cache publication: locking, staging, atomic swap, recovery.

One OS file lock per visible cache generation serialises prepare, validate,
commit, and read. Acquisition heals crash-left publication siblings, and the
swap primitive keeps a complete old or new generation visible at all times."""

from __future__ import annotations

import os
import shutil
import stat as stat_module
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, cast
from weakref import WeakValueDictionary

from haute._logging import get_logger

logger = get_logger(component="json_shred")


_META_FILENAME = "meta.json"


# One re-entrant lock per canonical cache directory. The thread lock protects
# same-process callers; the stable sibling file lock protects independent CLI,
# server, and test processes across a complete generation-selection or publish
# transaction. Different cache identities retain independent locks.
_BUILD_LOCKS: WeakValueDictionary[str, _CacheBuildLock]


_BUILD_LOCKS_GUARD = threading.Lock()


_BUILD_LOCKS_PROCESS_ID = os.getpid()


_RENAME_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05, 0.1)  # pragma: no mutate


class JsonCacheRecoveryError(RuntimeError):
    """A crash-left cache publication cannot be recovered unambiguously."""


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


def _publication_siblings(cache_dir: Path, kind: str) -> list[tuple[Path, os.stat_result]]:
    prefix = f"{cache_dir.name}.build-{kind}-"
    try:
        children = tuple(cache_dir.parent.iterdir())
    except FileNotFoundError:
        return []
    matches: list[tuple[Path, os.stat_result]] = []
    for child in children:
        if not child.name.startswith(prefix):
            continue
        matches.append((child, _plain_directory_stat(child)))
    return matches


def _recover_cache_publication(cache_dir: Path) -> None:
    """Restore or remove only verified crash-left publication siblings."""
    old_generations = _publication_siblings(cache_dir, "old")
    staged_generations = _publication_siblings(cache_dir, "tmp")
    if cache_dir.exists() or cache_dir.is_symlink():
        _plain_directory_stat(cache_dir)
        for path, _path_stat in (*old_generations, *staged_generations):
            _remove_plain_cache_directory(path)
        if old_generations or staged_generations:
            logger.info(
                "json_cache_publication_recovered",
                cache_dir=str(cache_dir),
                action="removed_superseded_siblings",
                old_count=len(old_generations),
                staged_count=len(staged_generations),
            )
        return

    for path, _path_stat in staged_generations:
        _remove_plain_cache_directory(path)
    if not old_generations:
        return
    newest_mtime = max(path_stat.st_mtime_ns for _path, path_stat in old_generations)
    newest = [path for path, path_stat in old_generations if path_stat.st_mtime_ns == newest_mtime]
    if len(newest) != 1:
        raise JsonCacheRecoveryError(
            f"Cache recovery found ambiguous newest backup generations for {cache_dir}"
        )
    restored = newest[0]
    _rename_dir_with_retry(restored, cache_dir)
    for path, _path_stat in old_generations:
        if path != restored:
            _remove_plain_cache_directory(path)
    logger.info(
        "json_cache_publication_recovered",
        cache_dir=str(cache_dir),
        action="restored_backup_generation",
        restored=str(restored),
        discarded_old_count=len(old_generations) - 1,
        discarded_staged_count=len(staged_generations),
    )


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
        """Return whether this process/thread owns the publication lock."""
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
            _recover_cache_publication(self._cache_dir)
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


@contextmanager
def per_port_cache_publication_lock(cache_dir: str | Path) -> Iterator[None]:  # pragma: no mutate
    """Serialize prepare/validate/commit for one visible cache generation."""
    with _build_lock_for(Path(cache_dir)):
        yield


def _unique_build_tmp_dir(cache_dir: Path) -> Path:
    return cache_dir.with_name(f"{cache_dir.name}.build-tmp-{uuid.uuid4().hex}")


def new_per_port_cache_staging_dir(cache_dir: str | Path) -> Path:  # pragma: no mutate
    """Return one parent-owned, validated private generation path."""
    cd = _normalised_build_path(cache_dir)
    return _validated_build_staging_dir(cd, _unique_build_tmp_dir(cd))


def _unique_build_old_dir(cache_dir: Path) -> Path:
    return cache_dir.with_name(f"{cache_dir.name}.build-old-{uuid.uuid4().hex}")


def _rename_dir_with_retry(source: Path, target: Path) -> None:
    """Rename a fully-built cache dir, retrying transient Windows handle locks."""
    delays = iter((*_RENAME_RETRY_DELAYS_SECONDS, None))
    while True:
        delay = next(delays)
        try:
            source.rename(target)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def _normalised_build_path(path: str | Path) -> Path:  # pragma: no mutate
    return Path(os.path.abspath(path))


def _validated_build_staging_dir(
    cache_dir: Path,
    staging_dir: str | Path,  # pragma: no mutate
) -> Path:
    staging = _normalised_build_path(staging_dir)
    expected_prefix = f"{cache_dir.name}.build-tmp-"
    suffix = staging.name.removeprefix(expected_prefix)
    if (
        staging.parent != cache_dir.parent
        or not staging.name.startswith(expected_prefix)
        or len(suffix) != 32
        or any(character not in "0123456789abcdef" for character in suffix)
    ):
        raise ValueError("cache staging directory must be an exact private sibling generation")
    _assert_cache_path_ancestors_plain(staging)
    return staging


def _swap_dir_into_place(tmp_dir: Path, live_dir: Path) -> None:
    """Atomically replace *live_dir* with the fully-built *tmp_dir*.

    Same rename dance as :func:`haute._json_flatten.mirror_cache_to_committed`:
    rename the live dir aside, rename the temp dir in, then best-effort
    remove the old copy. If the second rename fails the old dir is restored
    before re-raising, so the cache is never left missing.
    """
    if live_dir.exists():
        backup = _unique_build_old_dir(live_dir)
        try:
            _rename_dir_with_retry(live_dir, backup)
        except BaseException:  # pragma: no mutate
            shutil.rmtree(tmp_dir, ignore_errors=True)  # pragma: no mutate
            raise
        try:
            _rename_dir_with_retry(tmp_dir, live_dir)
        except BaseException:  # pragma: no mutate
            try:
                _rename_dir_with_retry(backup, live_dir)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)  # pragma: no mutate
            raise
        shutil.rmtree(backup, ignore_errors=True)  # pragma: no mutate
    else:
        try:
            _rename_dir_with_retry(tmp_dir, live_dir)
        except BaseException:  # pragma: no mutate
            shutil.rmtree(tmp_dir, ignore_errors=True)  # pragma: no mutate
            raise
