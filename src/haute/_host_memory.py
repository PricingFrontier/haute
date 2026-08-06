"""Host memory observation — available system RAM and GPU VRAM.

This module answers one question per resource: what does the machine have?
It never fabricates capacity — each RAM probe returns a real measurement or
``None`` with a recorded failure reason, so callers that require a
physical-memory limit must fail admission or use an explicit configured
budget.  ``available_vram_bytes`` reports *total* installed GPU VRAM (the
CatBoost sizing basis) or ``None`` when no GPU is detected.  Workload-side
estimation (how much a job *needs*) lives in :mod:`haute._ram_estimate`.

Available RAM is resolved by trying each platform source in order; the first
observation wins and is clamped to any finite Linux cgroup memory headroom.
When every source fails, one ``available_ram_unavailable`` warning reports
each attempted source's failure reason.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple, cast

from haute._logging import get_logger

logger = get_logger(component="host_memory")

__all__ = [
    "available_ram_bytes",
    "available_vram_bytes",
]


# ---------------------------------------------------------------------------
# Linux cgroup memory headroom
# ---------------------------------------------------------------------------


_CGROUP_V2_MEMORY_MAX = "/sys/fs/cgroup/memory.max"
_CGROUP_V2_MEMORY_CURRENT = "/sys/fs/cgroup/memory.current"
_CGROUP_V1_MEMORY_LIMIT = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
_CGROUP_V1_MEMORY_USAGE = "/sys/fs/cgroup/memory/memory.usage_in_bytes"
_CGROUP_V1_UNLIMITED_SENTINEL = 1 << 60


def _read_cgroup_memory_file(path: str) -> str | None:
    """Read one cgroup control file, returning ``None`` when absent."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _cgroup_memory_headroom_bytes() -> int | None:
    """Return observable Linux cgroup memory headroom, if it is finite."""

    def controller_headroom(
        version: str,
        limit_path: str,
        current_path: str,
        *,
        supports_max: bool,
    ) -> tuple[bool, int | None]:
        raw_limit = _read_cgroup_memory_file(limit_path)
        raw_current = _read_cgroup_memory_file(current_path)
        if raw_limit is None and raw_current is None:
            return False, None
        if raw_limit is None or raw_current is None:
            logger.warning(
                "cgroup_memory_state_incomplete",
                version=version,
                limit_path=limit_path,
                current_path=current_path,
            )
            return True, None
        if supports_max and raw_limit == "max":
            return True, None
        try:
            limit = int(raw_limit)
            current = int(raw_current)
        except ValueError:
            logger.warning(
                "cgroup_memory_state_malformed",
                version=version,
                limit=raw_limit,
                current=raw_current,
            )
            return True, None
        if limit < 0 or current < 0:
            logger.warning(
                "cgroup_memory_state_malformed",
                version=version,
                limit=raw_limit,
                current=raw_current,
            )
            return True, None
        if not supports_max and limit >= _CGROUP_V1_UNLIMITED_SENTINEL:
            return True, None
        return True, max(limit - current, 0)

    v2_present, v2_headroom = controller_headroom(
        "v2",
        _CGROUP_V2_MEMORY_MAX,
        _CGROUP_V2_MEMORY_CURRENT,
        supports_max=True,
    )
    if v2_present:
        return v2_headroom
    _v1_present, v1_headroom = controller_headroom(
        "v1",
        _CGROUP_V1_MEMORY_LIMIT,
        _CGROUP_V1_MEMORY_USAGE,
        supports_max=False,
    )
    return v1_headroom


def _clamp_available_ram_to_cgroup(host_available: int) -> int:
    if sys.platform != "linux":
        return host_available
    headroom = _cgroup_memory_headroom_bytes()
    return host_available if headroom is None else min(host_available, headroom)


# ---------------------------------------------------------------------------
# Available RAM probes — one per source, one shared result contract
# ---------------------------------------------------------------------------


class _RamProbe(NamedTuple):
    """One source's attempt to observe available RAM.

    ``(value, None)`` is a real observation.  ``(None, reason)`` is an
    attempted-but-failed observation.  ``(None, None)`` means the source does
    not apply on this platform and contributes nothing to diagnostics.
    """

    value: int | None
    reason: str | None = None
    details: Mapping[str, int] | None = None


def _proc_meminfo_available_ram() -> _RamProbe:
    """Linux ``/proc/meminfo`` ``MemAvailable`` (most accurate where present)."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return _RamProbe(int(line.split()[1]) * 1024)
        return _RamProbe(None, "MemAvailable not found")
    except (OSError, ValueError, IndexError) as exc:
        return _RamProbe(None, str(exc))


def _sysconf_available_ram() -> _RamProbe:
    """POSIX ``sysconf`` page-based query.

    Does not cover macOS: ``SC_AVPHYS_PAGES`` is absent from
    ``os.sysconf_names`` on darwin, so this probe raises there and the Mach
    probe below is the darwin source.
    """
    try:
        import os

        sysconf = cast(Any, os).sysconf
        pages = int(sysconf("SC_AVPHYS_PAGES"))
        page_size = int(sysconf("SC_PAGE_SIZE"))
        if pages > 0 and page_size > 0:
            return _RamProbe(pages * page_size)
        return _RamProbe(
            None,
            "non-positive sysconf memory values",
            {"pages": pages, "page_size": page_size},
        )
    except (AttributeError, OSError, ValueError) as exc:
        return _RamProbe(None, str(exc))


# Mach kernel ABI for the darwin probe, from ``<mach/host_info.h>``,
# ``<mach/kern_return.h>``, and ``<mach/vm_statistics.h>``.
_MACH_HOST_VM_INFO64 = 4
_MACH_KERN_SUCCESS = 0
_mach_natural_t = ctypes.c_uint32


class _VMStatistics64(ctypes.Structure):
    """``struct vm_statistics64`` — per-queue VM page counts."""

    _fields_ = [
        ("free_count", _mach_natural_t),
        ("active_count", _mach_natural_t),
        ("inactive_count", _mach_natural_t),
        ("wire_count", _mach_natural_t),
        ("zero_fill_count", ctypes.c_uint64),
        ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64),
        ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64),
        ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64),
        ("purgeable_count", _mach_natural_t),
        ("speculative_count", _mach_natural_t),
        ("decompressions", ctypes.c_uint64),
        ("compressions", ctypes.c_uint64),
        ("swapins", ctypes.c_uint64),
        ("swapouts", ctypes.c_uint64),
        ("compressor_page_count", _mach_natural_t),
        ("throttled_count", _mach_natural_t),
        ("external_page_count", _mach_natural_t),
        ("internal_page_count", _mach_natural_t),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
    ]


# HOST_VM_INFO64_COUNT for the REV1 layout this struct mirrors, in integer_t
# words.  The kernel refuses counts below the lowest legal revision boundary,
# but silently CLAMPS an oversized or off-boundary count down to the nearest
# boundary it knows and still returns KERN_SUCCESS (trailing fields left
# zero), so a drifted struct cannot be detected at call time.  Pin the REV1
# count and require the struct to match it exactly.
_HOST_VM_INFO64_REV1_COUNT = 38
if ctypes.sizeof(_VMStatistics64) != _HOST_VM_INFO64_REV1_COUNT * ctypes.sizeof(ctypes.c_int):
    raise RuntimeError(  # pragma: no cover - static layout invariant
        "_VMStatistics64 layout drifted from the 38-word HOST_VM_INFO64 REV1 contract"
    )


def _macos_available_ram() -> _RamProbe:
    """macOS Mach ``host_statistics64`` VM page counters via libSystem.

    Available is ``free + inactive`` pages times the host VM page size.
    Speculative read-ahead pages are already counted inside ``free_count``
    (``vm_stat`` subtracts them only for display), so they need no separate
    term.  ``purgeable_count`` is deliberately excluded: purgeable is an
    attribute of pages that stay on the active/inactive queues rather than a
    disjoint pool, so adding it would double-count the purgeable pages already
    inside ``inactive_count`` and over-admit work.  Total installed RAM is
    never substituted for availability.

    The page counts are in *host* VM pages, so the multiplier comes from Mach
    ``host_page_size`` rather than POSIX ``sysconf(SC_PAGE_SIZE)`` — the two
    diverge for translated processes (Rosetta reports a 4 KiB POSIX page while
    the host counts 16 KiB pages).

    The result is an optimistic bound: ``inactive_count`` includes dirty pages
    reclaimable only through compression or swap, and compressor-held memory is
    not subtracted.  Admission's OS reserve and safety factor absorb that gap.
    """
    if sys.platform != "darwin":
        return _RamProbe(None)
    try:
        # libSystem is already mapped into every process, so the global symbol
        # namespace resolves these without touching the filesystem.
        libsystem = ctypes.CDLL(None)
        libsystem.mach_host_self.restype = _mach_natural_t
        libsystem.mach_task_self.restype = _mach_natural_t
        libsystem.mach_port_deallocate.argtypes = [_mach_natural_t, _mach_natural_t]

        stats = _VMStatistics64()
        info_count = ctypes.c_uint32(_HOST_VM_INFO64_REV1_COUNT)
        page_size = ctypes.c_size_t()  # vm_size_t is pointer-width
        host_port = libsystem.mach_host_self()
        try:
            page_size_result = int(libsystem.host_page_size(host_port, ctypes.byref(page_size)))
            if page_size_result != _MACH_KERN_SUCCESS:
                return _RamProbe(None, f"host_page_size returned {page_size_result}")
            stats_result = int(
                libsystem.host_statistics64(
                    host_port,
                    _MACH_HOST_VM_INFO64,
                    ctypes.byref(stats),
                    ctypes.byref(info_count),
                )
            )
        finally:
            # ``mach_host_self`` hands back a send right on every call; without
            # this the per-admission probe would leak port rights.  Guarded
            # because a leaked right is preferable to discarding a good reading
            # or masking the body's real error with the deallocate's own.
            try:
                libsystem.mach_port_deallocate(libsystem.mach_task_self(), host_port)
            except (AttributeError, OSError, ctypes.ArgumentError):
                pass

        if stats_result != _MACH_KERN_SUCCESS:
            return _RamProbe(None, f"host_statistics64 returned {stats_result}")
        pages = int(stats.free_count) + int(stats.inactive_count)
        if pages <= 0 or page_size.value <= 0:
            return _RamProbe(None, "non-positive mach memory values")
        return _RamProbe(pages * int(page_size.value))
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError) as exc:
        return _RamProbe(None, str(exc))


def _windows_available_ram() -> _RamProbe:
    """Windows ``GlobalMemoryStatusEx`` via ctypes."""
    if sys.platform != "win32":
        return _RamProbe(None)
    try:
        # Imported per call (unlike the darwin probe) so a non-Windows test
        # harness can stand in a fake ctypes via sys.modules.
        import ctypes as windows_ctypes

        class MemoryStatusEx(windows_ctypes.Structure):
            _fields_ = [
                ("dwLength", windows_ctypes.c_ulong),
                ("dwMemoryLoad", windows_ctypes.c_ulong),
                ("ullTotalPhys", windows_ctypes.c_ulonglong),
                ("ullAvailPhys", windows_ctypes.c_ulonglong),
                ("ullTotalPageFile", windows_ctypes.c_ulonglong),
                ("ullAvailPageFile", windows_ctypes.c_ulonglong),
                ("ullTotalVirtual", windows_ctypes.c_ulonglong),
                ("ullAvailVirtual", windows_ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", windows_ctypes.c_ulonglong),
            ]

        mem = MemoryStatusEx()
        mem.dwLength = windows_ctypes.sizeof(MemoryStatusEx)
        kernel32 = cast(Any, windows_ctypes).windll.kernel32
        if kernel32.GlobalMemoryStatusEx(windows_ctypes.byref(mem)):
            return _RamProbe(int(mem.ullAvailPhys))
        return _RamProbe(None, "GlobalMemoryStatusEx returned false")
    except (OSError, AttributeError, ImportError) as exc:
        return _RamProbe(None, str(exc))


_RAM_SOURCES: tuple[tuple[str, Callable[[], _RamProbe]], ...] = (
    ("proc_meminfo", _proc_meminfo_available_ram),
    ("sysconf", _sysconf_available_ram),
    ("macos", _macos_available_ram),
    ("windows", _windows_available_ram),
)


def available_ram_bytes() -> int | None:
    """Return available system RAM in bytes, or ``None`` when unobservable.

    Tries each source in ``_RAM_SOURCES`` order; the first observation wins
    and is clamped to any finite Linux cgroup memory headroom.  No fallback
    capacity is fabricated: callers that require a physical-memory limit must
    fail admission or require an explicit configured budget.
    """
    diagnostics: dict[str, object] = {}
    for name, probe in _RAM_SOURCES:
        probe_result = probe()
        if probe_result.value is not None:
            return _clamp_available_ram_to_cgroup(probe_result.value)
        if probe_result.reason is None:
            continue  # source does not apply on this platform
        diagnostics[f"{name}_attempted"] = True
        diagnostics[f"{name}_error"] = probe_result.reason
        for key, detail in (probe_result.details or {}).items():
            diagnostics[f"{name}_{key}"] = detail
    logger.warning("available_ram_unavailable", platform=sys.platform, **diagnostics)
    return None


# ---------------------------------------------------------------------------
# GPU VRAM
# ---------------------------------------------------------------------------


def available_vram_bytes() -> int | None:
    """Return total GPU VRAM in bytes, or ``None`` if no GPU is detected."""
    try:
        import subprocess

        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        if result.returncode == 0:
            line = result.stdout.strip().split("\n")[0].strip()
            return int(line) * 1024 * 1024
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None
