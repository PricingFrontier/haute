"""One async-compatible writer lock shared across server processes for a project."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Literal

from haute._artifact_paths import safe_path
from haute._file_lock import FileLock


class ProjectMutationLock(asyncio.Lock):
    """Keep the existing asyncio.Lock interface, adding a cancellable OS lock."""

    def __init__(self) -> None:
        super().__init__()
        self._file_lock: FileLock | None = None

    async def acquire(self) -> Literal[True]:
        await super().acquire()
        try:
            # Even a rejected mutation or read-only preview takes this lock.
            # Keep its stable rendezvous file outside authored project storage.
            identity = hashlib.sha256(
                os.path.normcase(str(Path.cwd().resolve())).encode()
            ).hexdigest()
            path = safe_path(Path(tempfile.gettempdir()), f"haute-project-locks/{identity}.lock")
            # A lock of its own, not the process's shared one for this path:
            # two project locks on the event loop's one thread must exclude
            # each other rather than re-enter.
            file_lock = FileLock(path)
            # Nonblocking polls remain on the event loop, so cancellation cannot
            # strand an acquired lock in a background executor thread.
            while not file_lock.acquire(blocking=False):
                await asyncio.sleep(0.025)
            self._file_lock = file_lock
            return True
        except BaseException:
            super().release()
            raise

    def release(self) -> None:
        file_lock = self._file_lock
        if file_lock is None:
            raise RuntimeError("Project mutation lock is not acquired.")
        self._file_lock = None
        try:
            file_lock.release()
        finally:
            super().release()

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
