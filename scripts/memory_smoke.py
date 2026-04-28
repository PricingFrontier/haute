"""Run a command and emit a lightweight JSON memory smoke summary.

The child command's stdout and stderr are mirrored to this wrapper's stderr so
stdout remains machine-readable JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
import tracemalloc
from collections.abc import Sequence
from typing import Protocol, TextIO

try:
    import resource
except ImportError:  # pragma: no cover - exercised on platforms without resource.
    resource = None  # type: ignore[assignment]


class MemorySampler(Protocol):
    def process_rss_bytes(self, pid: int) -> int | None: ...

    def process_peak_rss_bytes(self) -> int | None: ...

    def child_peak_rss_bytes(self) -> int | None: ...


class ByteWriter(Protocol):
    def write(self, data: bytes) -> object: ...

    def flush(self) -> object: ...


class _TextByteWriter:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, data: bytes) -> int:
        return self._stream.write(data.decode(errors="replace"))

    def flush(self) -> None:
        self._stream.flush()


class StdlibMemorySampler:
    """Best-effort process memory sampler using only the Python standard library."""

    def process_rss_bytes(self, pid: int) -> int | None:
        if sys.platform == "win32":
            return _windows_working_set_bytes(pid=pid, peak=False)
        return _proc_status_rss_bytes(pid)

    def process_peak_rss_bytes(self) -> int | None:
        if sys.platform == "win32":
            return _windows_working_set_bytes(pid=os.getpid(), peak=True)
        return _resource_peak_rss_bytes("self")

    def child_peak_rss_bytes(self) -> int | None:
        return _resource_peak_rss_bytes("children")


def _windows_working_set_bytes(*, pid: int, peak: bool) -> int | None:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover - ctypes is stdlib on supported Python builds.
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    process_query_limited_information = 0x1000
    process_query_information = 0x0400
    process_vm_read = 0x0010

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    get_process_memory_info = psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    get_process_memory_info.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information | process_vm_read, False, pid)
    if not handle:
        handle = open_process(process_query_information | process_vm_read, False, pid)
    if not handle:
        return None

    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        if not get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
            return None
        value = counters.PeakWorkingSetSize if peak else counters.WorkingSetSize
        return int(value)
    finally:
        close_handle(handle)


def _proc_status_rss_bytes(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError):
        return None
    return None


def _resource_peak_rss_bytes(who: str) -> int | None:
    if resource is None:
        return None
    usage_target = resource.RUSAGE_SELF if who == "self" else resource.RUSAGE_CHILDREN
    peak = int(resource.getrusage(usage_target).ru_maxrss)
    if peak <= 0:
        return None
    if sys.platform == "darwin":
        return peak
    return peak * 1024


def _stderr_binary_writer() -> ByteWriter:
    buffer = getattr(sys.stderr, "buffer", None)
    if buffer is not None:
        return buffer
    return _TextByteWriter(sys.stderr)


def _start_output_mirror(
    stream: object,
    destination: ByteWriter,
    write_lock: threading.Lock,
) -> threading.Thread:
    def pump() -> None:
        try:
            while True:
                chunk = stream.read(8192)  # type: ignore[attr-defined]
                if not chunk:
                    return
                with write_lock:
                    destination.write(chunk)
                    destination.flush()
        finally:
            stream.close()  # type: ignore[attr-defined]

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return thread


def _sample_child_rss(sampler: MemorySampler, pid: int, samples: list[int]) -> None:
    child_rss = sampler.process_rss_bytes(pid)
    if child_rss is not None:
        samples.append(child_rss)


def _new_child_resource_peak(before: int | None, after: int | None) -> int | None:
    if after is None:
        return None
    if before is None or after > before:
        return after
    return None


def run_smoke(
    *,
    command: Sequence[str],
    enable_tracemalloc: bool = True,
    poll_interval_seconds: float = 0.05,
    sampler: MemorySampler | None = None,
    child_output: ByteWriter | None = None,
) -> dict[str, object]:
    if not command:
        raise ValueError("command must not be empty")
    if not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be greater than zero")

    sampler = sampler or StdlibMemorySampler()
    command_list = list(command)
    process_rss_before = sampler.process_rss_bytes(os.getpid())
    child_resource_peak_before = sampler.child_peak_rss_bytes()

    tracing_was_active = tracemalloc.is_tracing()
    if enable_tracemalloc:
        if not tracing_was_active:
            tracemalloc.start()
        tracemalloc.reset_peak()

    stdout_target = subprocess.PIPE if child_output is not None else subprocess.DEVNULL
    stderr_target = subprocess.PIPE if child_output is not None else subprocess.DEVNULL
    start = time.perf_counter()
    child_rss_samples: list[int] = []
    output_threads: list[threading.Thread] = []

    try:
        process = subprocess.Popen(command_list, stdout=stdout_target, stderr=stderr_target)
        if child_output is not None:
            write_lock = threading.Lock()
            if process.stdout is not None:
                output_threads.append(
                    _start_output_mirror(process.stdout, child_output, write_lock)
                )
            if process.stderr is not None:
                output_threads.append(
                    _start_output_mirror(process.stderr, child_output, write_lock)
                )

        while True:
            _sample_child_rss(sampler, process.pid, child_rss_samples)
            try:
                exit_code = process.wait(timeout=poll_interval_seconds)
                break
            except subprocess.TimeoutExpired:
                continue

        elapsed_seconds = time.perf_counter() - start
        for thread in output_threads:
            thread.join()

        process_rss_after = sampler.process_rss_bytes(os.getpid())
        process_peak_rss = sampler.process_peak_rss_bytes()
        child_resource_peak = _new_child_resource_peak(
            child_resource_peak_before,
            sampler.child_peak_rss_bytes(),
        )
        child_peak_rss = max(child_rss_samples, default=child_resource_peak)
        if child_resource_peak is not None and child_peak_rss is not None:
            child_peak_rss = max(child_peak_rss, child_resource_peak)

        if enable_tracemalloc:
            python_current, python_peak = tracemalloc.get_traced_memory()
        else:
            python_current = None
            python_peak = None

        return {
            "schema_version": 1,
            "command": command_list,
            "exit_code": exit_code,
            "elapsed_seconds": elapsed_seconds,
            "python_current_tracemalloc_bytes": python_current,
            "python_peak_tracemalloc_bytes": python_peak,
            "process_rss_before_bytes": process_rss_before,
            "process_rss_after_bytes": process_rss_after,
            "process_peak_rss_bytes": process_peak_rss,
            "child_peak_rss_bytes": child_peak_rss,
            "child_rss_sample_count": len(child_rss_samples),
            "poll_interval_seconds": poll_interval_seconds,
            "platform": platform.platform(),
            "python": platform.python_version(),
        }
    finally:
        if enable_tracemalloc and not tracing_was_active:
            tracemalloc.stop()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.05,
        help="Seconds between child RSS samples while the command is running.",
    )
    parser.add_argument(
        "--no-tracemalloc",
        action="store_true",
        help="Disable wrapper-process tracemalloc metrics.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON summary.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON summary. This is the default and is kept as an explicit flag for CI.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run. Use -- before the command to separate wrapper options.",
    )
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("command is required; pass it after --")
    if not math.isfinite(args.poll_interval) or args.poll_interval <= 0:
        parser.error("--poll-interval must be greater than zero")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    summary = run_smoke(
        command=args.command,
        enable_tracemalloc=not args.no_tracemalloc,
        poll_interval_seconds=args.poll_interval,
        child_output=_stderr_binary_writer(),
    )
    indent = 2 if args.pretty else None
    print(json.dumps(summary, indent=indent, sort_keys=True))
    return int(summary["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
