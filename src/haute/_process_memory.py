"""Cross-platform RSS sampling for supervised child processes."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Any, cast


def process_rss_bytes(pid: int) -> int | None:
    """Return the resident bytes of *pid*, or ``None`` when unobservable."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if sys.platform == "win32":
        return _windows_process_rss_bytes(pid)
    if sys.platform == "darwin":
        return _darwin_process_rss_bytes(pid)
    return _proc_process_rss_bytes(pid)


def process_rss_sampling_supported() -> bool:
    """Return whether the current host can observe another process's RSS."""
    return process_rss_bytes(os.getpid()) is not None


def process_is_alive(pid: int) -> bool:
    """Return whether *pid* may still be alive, preserving on uncertainty."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if sys.platform == "win32":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _proc_process_rss_bytes(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if not line.startswith("VmRSS:"):
                continue
            fields = line.split()
            if len(fields) < 3 or fields[2].casefold() != "kb":
                return None
            value = int(fields[1])
            return value * 1024 if value >= 0 else None
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError):
        return None
    return None


class _WindowsProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _windows_process_rss_bytes(pid: int) -> int | None:
    try:
        from ctypes import wintypes

        windll_factory = getattr(ctypes, "WinDLL", None)
        if windll_factory is None:
            return None
        kernel32 = windll_factory("kernel32", use_last_error=True)
        psapi = windll_factory("psapi", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_WindowsProcessMemoryCountersEx),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL

        process_query_limited_information = 0x1000
        process_query_information = 0x0400
        process_vm_read = 0x0010
        handle = open_process(
            process_query_limited_information | process_vm_read,
            False,
            pid,
        )
        if not handle:
            handle = open_process(process_query_information | process_vm_read, False, pid)
        if not handle:
            return None
        try:
            counters = _WindowsProcessMemoryCountersEx()
            counters.cb = ctypes.sizeof(counters)
            if not get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
                return None
            value = int(counters.WorkingSetSize)
            return value if value >= 0 else None
        finally:
            close_handle(handle)
    except (AttributeError, ImportError, OSError, ValueError, ctypes.ArgumentError):
        return None


def _windows_process_is_alive(pid: int) -> bool:
    try:
        from ctypes import wintypes

        windll_factory = getattr(ctypes, "WinDLL", None)
        if windll_factory is None:
            return True
        kernel32 = windll_factory("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code.restype = wintypes.BOOL
        handle = open_process(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            return False if error == 87 else True
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return int(exit_code.value) == 259
        finally:
            close_handle(handle)
    except (AttributeError, ImportError, OSError, ValueError, ctypes.ArgumentError):
        return True


def _darwin_process_rss_bytes(pid: int) -> int | None:
    """Read ``proc_taskinfo.pti_resident_size`` through macOS libproc."""
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = library.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        # PROC_PIDTASKINFO. The first two uint64 fields are virtual_size and
        # resident_size; a generously sized zeroed buffer keeps this binding
        # independent of later trailing additions to struct proc_taskinfo.
        buffer = (ctypes.c_ubyte * 256)()
        copied = int(proc_pidinfo(pid, 4, 0, ctypes.byref(buffer), ctypes.sizeof(buffer)))
        if copied < 2 * ctypes.sizeof(ctypes.c_uint64):
            return None
        resident = cast(
            Any,
            ctypes.c_uint64.from_buffer(buffer, ctypes.sizeof(ctypes.c_uint64)),
        ).value
        return int(resident) if resident >= 0 else None
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        return None
