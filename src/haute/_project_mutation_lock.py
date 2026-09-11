"""One async-compatible writer lock shared across server processes for a project."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Literal

from haute._artifact_paths import safe_path
from haute._file_lock import _acquire_file_lock, _release_file_lock


class ProjectMutationLock(asyncio.Lock):
    """Keep the existing asyncio.Lock interface, adding a cancellable OS lock."""

    def __init__(self) -> None:
        super().__init__()
        self._handle: BinaryIO | None = None

    async def acquire(self) -> Literal[True]:
        await super().acquire()
        handle: BinaryIO | None = None
        try:
            # Even a rejected mutation or read-only preview takes this lock.
            # Keep its stable rendezvous file outside authored project storage.
            identity = hashlib.sha256(
                os.path.normcase(str(Path.cwd().resolve())).encode()
            ).hexdigest()
            path = safe_path(Path(tempfile.gettempdir()), f"haute-project-locks/{identity}.lock")
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+b")
            # Nonblocking polls remain on the event loop, so cancellation cannot
            # strand an acquired lock in a background executor thread.
            while not _acquire_file_lock(handle, blocking=False):
                await asyncio.sleep(0.025)
            self._handle = handle
            return True
        except BaseException:
            if handle is not None:
                handle.close()
            super().release()
            raise

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            raise RuntimeError("Project mutation lock is not acquired.")
        self._handle = None
        try:
            _release_file_lock(handle)
        finally:
            handle.close()
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
