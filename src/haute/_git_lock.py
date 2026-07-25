"""Process-local mutation serialization for Git repositories.

The lock identity is the repository's common Git directory, so linked
worktrees that share refs and objects also share one reentrant lock. Paths that
are not repositories yet fall back to their resolved directory; this keeps
state-file updates serialized during project setup as well.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_registry_guard = threading.Lock()
_repository_locks: dict[str, threading.RLock] = {}


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
    current = path.resolve()
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


def repository_identity(path: Path) -> str:
    """Return a normalized key shared by every worktree of one repository."""
    resolved = path.resolve()
    git_dir = _find_git_dir(resolved)
    identity = _common_git_dir(git_dir) if git_dir is not None else resolved
    return os.path.normcase(str(identity))


def _lock_for(path: Path) -> threading.RLock:
    key = repository_identity(path)
    with _registry_guard:
        lock = _repository_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _repository_locks[key] = lock
        return lock


@contextmanager
def repository_mutation(path: Path) -> Iterator[None]:
    """Serialize a mutation with every other mutation of the same repository."""
    with _lock_for(path):
        yield
