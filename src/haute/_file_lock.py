"""Cross-platform OS file locks shared by project and artifact transactions."""

from __future__ import annotations

import os
import time
from typing import Any, cast


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
