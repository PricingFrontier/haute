"""Process liveness tokens: one lifetime-locked file per process in a directory.

A process holds an exclusive lock on its own token file for its whole lifetime. A token
whose file no process holds therefore names a process that has exited. Stores name the
owner of a lease marker or a staging file by its token, and remove markers whose owners
have gone.

There is one token per directory per process. A spawned child gets a fresh one, because
the registry is keyed by process id.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from haute._file_lock import (
    UnsafeCachePathError,
    _acquire_file_lock,
    _assert_path_ancestors_plain,
    _LockFileReplacedError,
    _open_lock_file,
    _path_names_handle,
    _release_file_lock,
    remove_unheld_lock_file,
)

TOKEN_LENGTH = 12
# A token file younger than this may still be in creation by its owner.
SWEEP_GRACE_SECONDS = 60.0


def is_token(value: str) -> bool:
    return len(value) == TOKEN_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


class ProcessTokens:
    """This process's token in one directory, and the liveness of other processes' tokens."""

    _registry: ClassVar[dict[tuple[Path, int], ProcessTokens]] = {}
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._guard = threading.Lock()
        self._token: str | None = None
        self._handle: Any | None = None

    @classmethod
    def for_directory(cls, directory: Path) -> ProcessTokens:
        key = (directory.resolve(), os.getpid())
        with cls._registry_lock:
            tokens = cls._registry.get(key)
            if tokens is None:
                tokens = cls._registry[key] = cls(directory)
            return tokens

    @property
    def current(self) -> str | None:
        """This process's token, or ``None`` before it has needed one."""
        return self._token

    @property
    def handle(self) -> Any | None:
        """The open, locked token file, while this process holds a token."""
        return self._handle

    def close(self) -> None:
        """Release this process's token; a later `own` takes a new one."""
        with self._guard:
            if self._handle is not None:
                self._handle.close()
            self._token, self._handle = None, None

    def own(self) -> str:
        """This process's token, created and locked on first use."""
        with self._guard:
            while self._token is None:
                token = uuid.uuid4().hex[:TOKEN_LENGTH]
                path = self.directory / f"{token}.lock"
                _assert_path_ancestors_plain(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = _open_lock_file(path)
                try:
                    handle.write(b"\0")
                    handle.flush()
                    if not _acquire_file_lock(handle, blocking=False):
                        raise RuntimeError("a fresh lease-owner token file is already locked")
                    named = _path_names_handle(path, handle)
                except BaseException:
                    handle.close()
                    raise
                if not named:
                    # A sweep removed the file before this process locked it, so other
                    # processes would read the token as dead: take another.
                    handle.close()
                    continue
                self._token, self._handle = token, handle
            return self._token

    def alive(self, token: str) -> bool:
        """Whether *token* names a live process. An unlocked token file is removed."""
        if token == self._token:
            return True
        if not is_token(token):
            return False
        path = self.directory / f"{token}.lock"
        if not path.exists():
            return False
        try:
            handle = _open_lock_file(path)
        except _LockFileReplacedError:
            # A sweep removed it meanwhile, which it does only to a file no process holds.
            return False
        try:
            if not _acquire_file_lock(handle, blocking=False):
                return True
            _release_file_lock(handle)
        finally:
            handle.close()
        try:
            path.unlink()
        except OSError:
            pass
        return False

    def sweep_dead(self, on_removed: Callable[[str], None] | None = None) -> None:
        """Remove the token files of processes that have exited.

        A file is removed only while this process holds its lock
        (`remove_unheld_lock_file`). An owner that locked it meanwhile finds it gone and
        takes another token. Files younger than the grace period, and this process's own,
        are left.
        """
        cutoff = time.time() - SWEEP_GRACE_SECONDS
        for path in tuple(self.directory.glob("*.lock")):
            token = path.stem
            if not is_token(token) or token == self._token:
                continue
            try:
                if path.lstat().st_mtime > cutoff or not remove_unheld_lock_file(path):
                    continue
            except (OSError, UnsafeCachePathError):
                # A racing or unreadable entry is not proven dead.
                continue
            if on_removed is not None:
                on_removed(token)
