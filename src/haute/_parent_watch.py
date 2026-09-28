"""Worker processes that end when the server process that spawned them does.

A spawned worker can outlive a server that crashed or was killed: a worker idle
on its queue, or busy in native code, never notices. Every haute worker is
spawned through ``start_process_with_environment``, which records the server's
pid in ``HAUTE_WORKER_PARENT_PID``; each worker's entrypoint then calls
:func:`exit_with_parent`, whose daemon thread waits on the parent *process* and
ends the worker when it is gone.

The wait follows the process, never a thread: ``PR_SET_PDEATHSIG`` fires when
the *thread* that spawned the child exits, and haute spawns workers from
short-lived job threads.
"""

from __future__ import annotations

import os
import sys
import threading
import time

from haute._env import optional_int_env

PARENT_PID_ENV = "HAUTE_WORKER_PARENT_PID"
_POLL_SECONDS = 1.0
# ``SYNCHRONIZE``: the one right waiting on a process handle needs.
_SYNCHRONIZE = 0x00100000


def wait_for_process_exit(pid: int) -> None:
    """Block until process *pid* has exited (or return at once if it already has)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        return
    if os.getppid() != pid:
        return
    pidfd_open = getattr(os, "pidfd_open", None)
    if pidfd_open is not None:
        import select

        try:
            fd = pidfd_open(pid)
        except OSError:
            fd = None
        if fd is not None:
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            poller.poll()
            return
    while os.getppid() == pid:
        time.sleep(_POLL_SECONDS)


def exit_with_parent(parent_pid: int | None = None) -> None:
    """End this worker process when its parent process ends (worker side).

    *parent_pid* defaults to the pid its spawner recorded in the environment;
    a process started without one (not a haute worker) is left alone.
    """
    if parent_pid is None:
        parent_pid = optional_int_env(PARENT_PID_ENV)
        if parent_pid is None:
            return
    watched = parent_pid

    def _watch() -> None:
        wait_for_process_exit(watched)
        # The server is gone: nothing can use this worker or its results.
        os._exit(0)

    threading.Thread(target=_watch, name="haute-parent-watcher", daemon=True).start()
