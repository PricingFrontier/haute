"""Process-local transaction serialization for Git repositories.

Every caller keeps a stable lock keyed by its absolute project path, including
across ``git init``. Once Git metadata exists, callers also take a lock keyed by
the common Git directory so linked worktrees serialize access to shared refs
and objects.
"""

from __future__ import annotations

import os
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

_IDENTITY_CACHE_SIZE = 256

_registry_guard = threading.Lock()
_repository_locks: weakref.WeakValueDictionary[str, threading.RLock] = weakref.WeakValueDictionary()

_MarkerFingerprint = tuple[int, int, int, int, int, int] | None


def _normalized_absolute(path: Path) -> str:
    """Return a stable local key without touching the filesystem."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _marker_fingerprint(project_path: str) -> _MarkerFingerprint:
    """Fingerprint a direct ``.git`` marker with one steady-state stat."""
    try:
        stat = (Path(project_path) / ".git").stat(follow_symlinks=False)
    except OSError:
        return None
    return (
        stat.st_mode,
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _git_dir_from_marker(marker: Path) -> Path | None:
    if marker.is_dir():
        return marker.resolve()
    if not marker.is_file():
        return None
    try:
        first_line = marker.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    prefix = "gitdir:"
    if not first_line.lower().startswith(prefix):
        return None
    git_dir = Path(first_line[len(prefix) :].strip())
    if not git_dir.is_absolute():
        git_dir = marker.parent / git_dir
    return git_dir.resolve()


def _find_git_dir(path: Path) -> Path | None:
    current = path
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        git_dir = _git_dir_from_marker(candidate / ".git")
        if git_dir is not None:
            return git_dir
    return None


def _common_git_dir(git_dir: Path) -> Path:
    marker = git_dir / "commondir"
    try:
        raw = marker.read_text(encoding="utf-8", errors="strict").strip()
    except FileNotFoundError:
        return git_dir
    except OSError:
        return git_dir
    if not raw:
        return git_dir
    common = Path(raw)
    if not common.is_absolute():
        common = git_dir / common
    return common.resolve()


@lru_cache(maxsize=_IDENTITY_CACHE_SIZE)
def _resolved_location(project_path: str) -> Path:
    return Path(project_path).resolve()


@lru_cache(maxsize=_IDENTITY_CACHE_SIZE)
def _common_repository_identity(
    project_path: str,
    marker_fingerprint: _MarkerFingerprint,
) -> str | None:
    del marker_fingerprint  # It invalidates this cache entry when ``.git`` changes.
    git_dir = _find_git_dir(_resolved_location(project_path))
    if git_dir is None:
        return None
    return os.path.normcase(str(_common_git_dir(git_dir)))


def _repository_keys(path: Path) -> tuple[str, ...]:
    project_path = _normalized_absolute(path)
    common_identity = _common_repository_identity(
        project_path,
        _marker_fingerprint(project_path),
    )
    local_key = f"local:{project_path}"
    if common_identity is None:
        return (local_key,)
    return local_key, f"common:{common_identity}"


def repository_identity(path: Path) -> str:
    """Return a normalized key shared by every worktree of one repository."""
    project_path = _normalized_absolute(path)
    common_identity = _common_repository_identity(
        project_path,
        _marker_fingerprint(project_path),
    )
    return common_identity if common_identity is not None else project_path


def _locks_for(path: Path) -> tuple[threading.RLock, ...]:
    keys = _repository_keys(path)
    locks: list[threading.RLock] = []
    with _registry_guard:
        for key in keys:
            lock = _repository_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                _repository_locks[key] = lock
            locks.append(lock)
    return tuple(locks)


@contextmanager
def repository_mutation(path: Path) -> Iterator[None]:
    """Serialize a transaction with every other transaction of the repository."""
    acquired: list[threading.RLock] = []
    try:
        for lock in _locks_for(path):
            lock.acquire()
            acquired.append(lock)
        yield
    finally:
        for lock in reversed(acquired):
            lock.release()
