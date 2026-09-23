"""Process memory and liveness, read through psutil on every platform.

A reading the operating system will not give — the process is gone, access is
denied, or the platform does not expose that figure — is ``None``: unobservable,
never zero.
"""

from __future__ import annotations

import sys

import psutil


def _validated_pid(pid: int) -> int:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    return pid


def _memory_field_bytes(pid: int | None, field: str) -> int | None:
    """One ``memory_info()`` field of *pid* (this process when ``None``)."""
    try:
        memory = (psutil.Process() if pid is None else psutil.Process(pid)).memory_info()
    except (psutil.Error, OSError):
        return None
    value = getattr(memory, field, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def current_process_rss_bytes() -> int | None:
    """Return this process's resident bytes, or ``None`` when unobservable."""
    return _memory_field_bytes(None, "rss")


def current_process_private_bytes() -> int | None:
    """Return this process's committed private bytes (Windows), else ``None``."""
    if sys.platform != "win32":
        return None
    return _memory_field_bytes(None, "private")


def current_process_virtual_bytes() -> int | None:
    """Return this process's virtual address-space size, or ``None``."""
    return _memory_field_bytes(None, "vms")


def current_process_thread_count() -> int | None:
    """Return this process's OS thread count, or ``None``."""
    try:
        return int(psutil.Process().num_threads())
    except (psutil.Error, OSError):
        return None


def process_rss_bytes(pid: int) -> int | None:
    """Return the resident bytes of *pid*, or ``None`` when unobservable."""
    return _memory_field_bytes(_validated_pid(pid), "rss")


def process_rss_sampling_supported() -> bool:
    """Return whether the current host can observe a process's RSS."""
    return current_process_rss_bytes() is not None


def process_is_alive(pid: int) -> bool:
    """Return whether *pid* may still be alive, preserving on uncertainty."""
    pid = _validated_pid(pid)
    try:
        return bool(psutil.pid_exists(pid))
    except (psutil.Error, OSError):
        return True
