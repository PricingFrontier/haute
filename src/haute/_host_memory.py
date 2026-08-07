"""Host memory observation — available system RAM and GPU VRAM.

This module answers one question per resource: what does the machine have?
It never fabricates capacity — each RAM probe returns a real measurement or
``None`` with a recorded failure reason, so callers that require a
physical-memory limit must fail admission or use an explicit configured
budget.  ``available_vram_bytes`` reports the first GPU's total VRAM (the
CatBoost single-device sizing basis) or ``None`` when no GPU is detected;
detection failures are logged with a reason.  Workload-side estimation (how
much a job *needs*) lives in :mod:`haute._ram_estimate`.

Available RAM is resolved by trying each platform source in order; the first
observation wins and is clamped to any finite Linux cgroup memory headroom,
resolved at the process's own cgroup (nested limits included) with
ancestor-min semantics.
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
    "require_positive_available_ram",
]


def require_positive_available_ram(available: object) -> int:
    """Validate an observed available-RAM value for capacity-deriving callers.

    ``None`` (or a non-integer) means availability is unobservable, and a
    negative value is a probe defect (the cgroup clamp floors real headroom at
    zero, so no honest observation is negative) — both refuse with the
    configure-an-explicit-limit remedy.  Zero is an honest observation that
    the host or cgroup memory limit is fully consumed *right now*; because
    cgroup v2 ``memory.current`` includes reclaimable page cache, that state
    can be transient, so its remedy is free-memory-and-retry.  Configuring an
    explicit limit is deliberately not offered for exhaustion: it would
    bypass the zero observation rather than create capacity.
    """
    if not isinstance(available, int) or isinstance(available, bool):
        raise RuntimeError(
            "physical RAM is unavailable; configure an explicit execution memory limit"
        )
    if available < 0:
        raise RuntimeError(
            "available memory observation is negative (memory probe defect); "
            "configure an explicit execution memory limit"
        )
    if available == 0:
        raise RuntimeError(
            "available memory is exhausted (the host or cgroup memory limit is "
            "currently fully used); free memory and retry"
        )
    return available


# ---------------------------------------------------------------------------
# Linux cgroup memory headroom
# ---------------------------------------------------------------------------
#
# The memory limit that binds this process is not necessarily at the cgroup
# mount root: under a systemd service slice, or in a container sharing the
# host cgroup namespace, the process lives in a nested cgroup whose controller
# files sit below the mount point.  The probe resolves the process's own
# cgroup directory from ``/proc/self/cgroup`` + ``/proc/self/mountinfo``
# (v2 unified and v1 hybrid) and takes the minimum headroom across that
# cgroup and its ancestors up to the mount point — a parent's limit binds its
# children, and the memory controller may only be enabled partway up.  When
# resolution fails (unreadable or malformed proc files, no matching mount, a
# path outside the mount root), the probe degrades to reading the mount-root
# controller files, preserving the historical fail-open behaviour.


_PROC_SELF_CGROUP = "/proc/self/cgroup"
_PROC_SELF_MOUNTINFO = "/proc/self/mountinfo"
_CGROUP_V2_DEFAULT_MOUNT = "/sys/fs/cgroup"
_CGROUP_V1_MEMORY_DEFAULT_MOUNT = "/sys/fs/cgroup/memory"
_CGROUP_V1_UNLIMITED_SENTINEL = 1 << 60
_CGROUP_WALK_DEPTH_LIMIT = 64


def _read_cgroup_memory_file(path: str) -> str | None:
    """Read one cgroup control file, returning ``None`` when absent."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


class _CgroupLocation(NamedTuple):
    """Where one hierarchy's memory controller files live for this process.

    ``directory`` is the process's own cgroup directory (the walk start) and
    ``mount_point`` is the hierarchy's mount point (the walk end, inclusive).
    The mount-root fallback is the degenerate case where both are the mount
    point itself, which reads exactly the historical single-level paths.
    """

    directory: str
    mount_point: str


def _parse_proc_self_cgroup(text: str) -> tuple[str | None, str | None]:
    """Return (v2 path, v1 memory-controller path) from ``/proc/self/cgroup``.

    Lines look like ``0::/system.slice/haute.service`` (v2) or
    ``4:memory:/docker/abc`` (v1).  Malformed lines are skipped; a hierarchy
    with no well-formed line simply stays unresolved and falls back.
    """
    v2_path: str | None = None
    v1_path: str | None = None
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy_id, controllers, cgroup_path = parts
        # A deleted cgroup is reported with a trailing marker; its controller
        # files are gone, so resolution would only produce absent-file reads.
        if not cgroup_path.startswith("/") or cgroup_path.endswith(" (deleted)"):
            continue
        if hierarchy_id == "0" and controllers == "":
            v2_path = v2_path if v2_path is not None else cgroup_path
        elif "memory" in controllers.split(","):
            v1_path = v1_path if v1_path is not None else cgroup_path
    return v2_path, v1_path


def _unescape_mountinfo_field(field: str) -> str:
    """Decode the octal escapes (``\\040`` etc.) mountinfo uses in paths."""
    if "\\" not in field:
        return field
    out: list[str] = []
    index = 0
    while index < len(field):
        char = field[index]
        octal = field[index + 1 : index + 4]
        if char == "\\" and len(octal) == 3 and all(digit in "01234567" for digit in octal):
            out.append(chr(int(octal, 8)))
            index += 4
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _parse_proc_self_mountinfo(
    text: str,
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """Return (root, mount point) for the cgroup2 and v1-memory mounts.

    mountinfo fields: ``ID parent major:minor root mount-point options
    [optional...] - fstype source super-options``.  Malformed lines are
    skipped; the first matching mount per hierarchy wins.
    """
    v2_mount: tuple[str, str] | None = None
    v1_mount: tuple[str, str] | None = None
    for line in text.splitlines():
        fields = line.split(" ")
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 5 or separator + 1 >= len(fields):
            continue
        root = _unescape_mountinfo_field(fields[3])
        mount_point = _unescape_mountinfo_field(fields[4])
        fs_type = fields[separator + 1]
        if not root.startswith("/") or not mount_point.startswith("/"):
            continue
        if fs_type == "cgroup2":
            v2_mount = v2_mount if v2_mount is not None else (root, mount_point)
        elif fs_type == "cgroup" and separator + 3 < len(fields):
            super_options = fields[separator + 3]
            if "memory" in super_options.split(","):
                v1_mount = v1_mount if v1_mount is not None else (root, mount_point)
    return v2_mount, v1_mount


def _resolve_cgroup_directory(cgroup_path: str, mount_root: str, mount_point: str) -> str | None:
    """Map a ``/proc/self/cgroup`` path onto the filesystem via its mount.

    The mount exposes the hierarchy subtree at ``mount_root``; the process's
    directory is the mount point plus the cgroup path's remainder below that
    root.  Returns ``None`` when the path is not under the mount root (a
    cross-namespace view this process cannot read through this mount).
    """
    if ".." in cgroup_path.split("/"):
        return None
    root = mount_root.rstrip("/") or "/"
    path = cgroup_path.rstrip("/") or "/"
    mount = mount_point.rstrip("/") or "/"
    if path == root:
        return mount
    prefix = "/" if root == "/" else root + "/"
    if not path.startswith(prefix):
        return None
    suffix = path[len(prefix) :]
    return f"{mount}/{suffix}" if mount != "/" else f"/{suffix}"


def _resolve_cgroup_locations() -> tuple[_CgroupLocation, _CgroupLocation]:
    """Resolve the v2 and v1 walk locations, defaulting to the mount roots."""
    v2_location = _CgroupLocation(_CGROUP_V2_DEFAULT_MOUNT, _CGROUP_V2_DEFAULT_MOUNT)
    v1_location = _CgroupLocation(_CGROUP_V1_MEMORY_DEFAULT_MOUNT, _CGROUP_V1_MEMORY_DEFAULT_MOUNT)
    cgroup_text = _read_cgroup_memory_file(_PROC_SELF_CGROUP)
    mountinfo_text = _read_cgroup_memory_file(_PROC_SELF_MOUNTINFO)
    if cgroup_text is None or mountinfo_text is None:
        return v2_location, v1_location
    v2_path, v1_path = _parse_proc_self_cgroup(cgroup_text)
    v2_mount, v1_mount = _parse_proc_self_mountinfo(mountinfo_text)
    if v2_path is not None and v2_mount is not None:
        directory = _resolve_cgroup_directory(v2_path, *v2_mount)
        if directory is not None:
            v2_location = _CgroupLocation(directory, v2_mount[1])
        else:
            logger.warning(
                "cgroup_self_path_unresolved",
                version="v2",
                cgroup_path=v2_path,
                mount_root=v2_mount[0],
                mount_point=v2_mount[1],
            )
    if v1_path is not None and v1_mount is not None:
        directory = _resolve_cgroup_directory(v1_path, *v1_mount)
        if directory is not None:
            v1_location = _CgroupLocation(directory, v1_mount[1])
        else:
            logger.warning(
                "cgroup_self_path_unresolved",
                version="v1",
                cgroup_path=v1_path,
                mount_root=v1_mount[0],
                mount_point=v1_mount[1],
            )
    return v2_location, v1_location


def _cgroup_ancestor_chain(location: _CgroupLocation) -> list[str]:
    """Directories from the process's cgroup up to the mount point, inclusive."""
    top = location.mount_point.rstrip("/") or "/"
    current = location.directory.rstrip("/") or "/"
    chain: list[str] = []
    while len(chain) < _CGROUP_WALK_DEPTH_LIMIT:
        chain.append(current)
        if current == top or "/" not in current or current == "/":
            break
        current = current.rsplit("/", 1)[0] or "/"
    return chain


def _level_headroom(
    version: str,
    limit_path: str,
    current_path: str,
    *,
    supports_max: bool,
) -> tuple[bool, int | None, bool]:
    """Read one cgroup level: (present, finite headroom, fail-open abort)."""
    raw_limit = _read_cgroup_memory_file(limit_path)
    raw_current = _read_cgroup_memory_file(current_path)
    if raw_limit is None and raw_current is None:
        return False, None, False
    if raw_limit is None or raw_current is None:
        logger.warning(
            "cgroup_memory_state_incomplete",
            version=version,
            limit_path=limit_path,
            current_path=current_path,
        )
        return True, None, True
    if supports_max and raw_limit == "max":
        return True, None, False
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
        return True, None, True
    if limit < 0 or current < 0:
        logger.warning(
            "cgroup_memory_state_malformed",
            version=version,
            limit=raw_limit,
            current=raw_current,
        )
        return True, None, True
    if not supports_max and limit >= _CGROUP_V1_UNLIMITED_SENTINEL:
        return True, None, False
    return True, max(limit - current, 0), False


def _hierarchy_headroom(
    version: str,
    location: _CgroupLocation,
    limit_name: str,
    current_name: str,
    *,
    supports_max: bool,
) -> tuple[bool, int | None]:
    """Minimum finite headroom across the ancestor chain of *location*.

    A level where the controller files are absent (controller not enabled
    there, or the hierarchy's true root) contributes nothing; a level with
    incomplete or malformed state fails the whole probe open, matching the
    single-level behaviour this generalises.
    """
    present = False
    minimum: int | None = None
    for level in _cgroup_ancestor_chain(location):
        prefix = level.rstrip("/")
        level_present, headroom, abort = _level_headroom(
            version,
            f"{prefix}/{limit_name}",
            f"{prefix}/{current_name}",
            supports_max=supports_max,
        )
        present = present or level_present
        if abort:
            return present, None
        if headroom is not None:
            minimum = headroom if minimum is None else min(minimum, headroom)
    return present, minimum


def _cgroup_memory_headroom_bytes() -> int | None:
    """Return observable Linux cgroup memory headroom, if it is finite."""
    v2_location, v1_location = _resolve_cgroup_locations()
    v2_present, v2_headroom = _hierarchy_headroom(
        "v2",
        v2_location,
        "memory.max",
        "memory.current",
        supports_max=True,
    )
    if v2_present:
        return v2_headroom
    _v1_present, v1_headroom = _hierarchy_headroom(
        "v1",
        v1_location,
        "memory.limit_in_bytes",
        "memory.usage_in_bytes",
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
        # vm_size_t is pointer-width: verified empirically — host_page_size
        # overwrites all 8 bytes of a sentinel-filled c_size_t.  kern_return_t
        # is a plain int.
        libsystem.host_page_size.restype = ctypes.c_int
        libsystem.host_page_size.argtypes = [_mach_natural_t, ctypes.POINTER(ctypes.c_size_t)]
        libsystem.host_statistics64.restype = ctypes.c_int
        libsystem.host_statistics64.argtypes = [
            _mach_natural_t,
            ctypes.c_int,
            ctypes.POINTER(_VMStatistics64),
            ctypes.POINTER(ctypes.c_uint32),
        ]

        stats = _VMStatistics64()
        info_count = ctypes.c_uint32(_HOST_VM_INFO64_REV1_COUNT)
        page_size = ctypes.c_size_t()
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
            except (AttributeError, OSError, ctypes.ArgumentError) as release_exc:
                logger.debug("mach_port_release_failed", error=str(release_exc))

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
    except (OSError, AttributeError, ImportError, ctypes.ArgumentError) as exc:
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
    """Return the first GPU's total VRAM in bytes, or ``None`` without one.

    An absent ``nvidia-smi`` binary is the expected no-GPU state and is not
    logged.  Any other failure (broken driver, timeout, unparseable output)
    is logged with its reason so a detection outage is distinguishable from
    genuine GPU absence — the return value stays ``None`` either way, so the
    VRAM pre-check degrades to a user-visible advisory warning rather than
    refusing work.
    """
    import subprocess

    try:
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
        logger.warning(
            "vram_detection_failed",
            reason=f"nvidia-smi exited {result.returncode}",
        )
    except FileNotFoundError:
        pass  # no nvidia-smi — the ordinary no-GPU machine
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        logger.warning("vram_detection_failed", reason=str(exc))
    return None
