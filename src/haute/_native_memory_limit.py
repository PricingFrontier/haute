"""Native, per-process memory growth limits for execution workers.

This module intentionally does not use RSS sampling as enforcement: sampling is
useful supervision, but is not a kernel hard limit.
"""

from __future__ import annotations

import atexit
import ctypes
import importlib
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

NativeMemoryBackend = Literal["cgroup", "rlimit", "windows_job"]
_CURRENT_NATIVE_MEMORY_BACKEND: ContextVar[NativeMemoryBackend | None] = ContextVar(
    "haute_current_native_memory_backend",
    default=None,
)


def current_native_memory_backend() -> NativeMemoryBackend | None:
    """Return descendant-accounting evidence for the current worker call."""
    return _CURRENT_NATIVE_MEMORY_BACKEND.get()


@contextmanager
def native_memory_backend_scope(
    backend: NativeMemoryBackend | None,
) -> Iterator[None]:
    """Expose one lease backend only while its worker call remains active."""
    if backend not in {None, "cgroup", "rlimit", "windows_job"}:
        raise ValueError(f"unknown native memory backend: {backend!r}")
    token = _CURRENT_NATIVE_MEMORY_BACKEND.set(backend)
    try:
        yield
    finally:
        _CURRENT_NATIVE_MEMORY_BACKEND.reset(token)


class NativeMemoryLimitUnsupportedError(RuntimeError):
    """Raised before user work when a requested native cap cannot be installed."""


class NativeMemoryLimitCleanupError(NativeMemoryLimitUnsupportedError):
    """A private native-limit resource could not be safely removed."""


class _ResourceApi(Protocol):
    """The POSIX ``resource`` surface used by the native-limit lease."""

    RLIMIT_AS: int
    RLIM_INFINITY: int

    def getrlimit(self, resource: int, /) -> tuple[int, int]: ...

    def setrlimit(self, resource: int, limits: tuple[int, int], /) -> None: ...


def _resource_api() -> _ResourceApi | None:
    """Load the optional POSIX API without relying on Windows typeshed stubs."""
    try:
        module = importlib.import_module("resource")
    except ImportError:
        return None
    required = ("RLIMIT_AS", "RLIM_INFINITY", "getrlimit", "setrlimit")
    if not all(hasattr(module, name) for name in required):
        return None
    return cast(_ResourceApi, module)


def native_memory_caps_supported() -> bool:
    """Whether this host has a native kernel process-memory cap mechanism."""
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        return False
    if sys.platform.startswith("linux"):
        return _linux_cgroup_v2_available() or _rlimit_as_supported()
    return _rlimit_as_supported()


def _rlimit_as_supported() -> bool:
    return _resource_api() is not None


def _linux_cgroup_v2_available() -> bool:
    # A controller must be delegated and writable; merely seeing cgroup2 is
    # insufficient in containers.
    try:
        return os.access(_current_cgroup_path(), os.W_OK | os.X_OK)
    except NativeMemoryLimitUnsupportedError:
        return False


def _cgroup_parent() -> Path:
    return Path("/sys/fs/cgroup")


def _current_cgroup_path() -> Path:
    """Resolve our unified v2 cgroup, rejecting paths outside the cgroup root."""
    try:
        unified = next(
            line.split("::", 1)[1]
            for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
            if line.startswith("0::")
        )
    except (OSError, StopIteration, IndexError) as exc:
        raise NativeMemoryLimitUnsupportedError(
            "cannot locate unified cgroup v2 membership"
        ) from exc
    relative = Path(unified.lstrip("/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise NativeMemoryLimitUnsupportedError("unsafe unified cgroup path")
    try:
        root = _cgroup_parent().resolve(strict=True)
        current = (root / relative).resolve(strict=True)
        current.relative_to(root)
    except (OSError, ValueError) as exc:
        raise NativeMemoryLimitUnsupportedError(
            "unified cgroup is outside the cgroup root"
        ) from exc
    return current


def _native_baseline_bytes() -> int:
    if sys.platform == "win32":
        return _windows_private_usage()
    if sys.platform.startswith("linux"):
        return _linux_virtual_bytes()
    return 0


def _linux_virtual_bytes() -> int:
    try:
        pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[0])
        sysconf = getattr(os, "sysconf", None)
        if not callable(sysconf):
            raise OSError("os.sysconf is unavailable")
        return pages * int(sysconf("SC_PAGE_SIZE"))
    except (OSError, TypeError, ValueError, IndexError) as exc:
        raise NativeMemoryLimitUnsupportedError(
            "cannot measure Linux virtual address space"
        ) from exc


def _linux_cgroup_current(path: Path) -> int:
    return int((path / "memory.current").read_text(encoding="ascii").strip())


_PRIVATE_CGROUP_NAME = re.compile(r"haute-(?P<pid>[1-9][0-9]*)-[0-9a-f]{16}")


def _validated_private_cgroup(parent: Path, child: Path, *, pid: int) -> bool:
    """Whether ``child`` is exactly the direct private group for ``pid``."""
    if _PRIVATE_CGROUP_NAME.fullmatch(child.name) is None:
        return False
    if child.name.split("-", 2)[1] != str(pid):
        return False
    try:
        return child.resolve(strict=True).parent == parent.resolve(strict=True)
    except OSError:
        return False


def cleanup_private_cgroups_for_pid(pid: int) -> None:
    """Remove empty, exact private cgroups left by a dead joined child.

    This deliberately has no recursive operation and ignores anything that
    does not prove to be a direct group with the supplied child pid.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        parent = _current_cgroup_path()
    except NativeMemoryLimitUnsupportedError:
        return
    for candidate in parent.iterdir():
        if not _validated_private_cgroup(parent, candidate, pid=pid):
            continue
        try:
            if (candidate / "cgroup.procs").read_text(encoding="ascii").strip():
                continue
            candidate.rmdir()
        except OSError as exc:
            raise NativeMemoryLimitCleanupError(
                f"could not remove private cgroup {candidate.name!r} for worker {pid}"
            ) from exc


@dataclass(slots=True)
class NativeMemoryLease:
    """A child-owned cap which is applied for each user request and reset after it."""

    _backend: NativeMemoryBackend | None = None
    _rlimit_original: tuple[int, int] | None = None
    _cgroup: Path | None = None
    _cgroup_parent: Path | None = None
    _job: Any = None

    @property
    def backend(self) -> NativeMemoryBackend | None:
        """The installed hard-cap mechanism, if this lease has one."""
        return self._backend

    def apply(self, growth_bytes: int, *, required: bool) -> bool:
        if growth_bytes <= 0:
            raise ValueError("memory growth limit must be positive")
        # Evidence from an earlier request must never survive into this one:
        # a failed best-effort attempt after a successful request would
        # otherwise advertise a cap that is not installed.
        self._backend = None
        try:
            if sys.platform == "win32":
                self._apply_windows(growth_bytes)
            elif sys.platform.startswith("linux"):
                self._apply_linux(growth_bytes)
            else:
                self._apply_rlimit(growth_bytes)
            return True
        except NativeMemoryLimitCleanupError:
            raise
        except NativeMemoryLimitUnsupportedError:
            if required:
                raise
            return False
        except (OSError, ValueError) as exc:
            if required:
                raise NativeMemoryLimitUnsupportedError(
                    f"native memory limit setup failed: {exc}"
                ) from exc
            return False

    def restore(self) -> None:
        """Clear a request limit while retaining process-lifetime resources."""
        if self._backend == "rlimit" and self._rlimit_original is not None:
            resource_api = _resource_api()
            if resource_api is None:
                raise NativeMemoryLimitUnsupportedError(
                    "native address-space limits became unavailable"
                )
            resource_api.setrlimit(resource_api.RLIMIT_AS, self._rlimit_original)
            self._rlimit_original = None
        elif self._backend == "cgroup" and self._cgroup is not None:
            (self._cgroup / "memory.max").write_text("max\n", encoding="ascii")
        elif self._backend == "windows_job":
            self._set_windows_limit(None)
        # The cap is gone, so the lease no longer holds hard-cap evidence.
        self._backend = None

    def close(self) -> None:
        if self._cgroup is not None:
            path, parent = self._cgroup, self._cgroup_parent
            if parent is None:  # pragma: no cover - object invariant
                raise RuntimeError("private cgroup has no recorded parent")
            # Moving ourselves out is required before rmdir.  Do not clear the
            # references until both exact operations succeed, so a failure is
            # loud and diagnosable rather than leaking an invisible cgroup.
            _unwind_private_cgroup(path, parent, move_self=True)
            self._cgroup = None
            self._cgroup_parent = None
        if self._job is not None:
            handle, self._job = self._job, None
            kernel32, _psapi = _windows_apis()
            if not kernel32.CloseHandle(handle):
                raise _windows_error()

    def _apply_linux(self, growth_bytes: int) -> None:
        if self._cgroup is None:
            try:
                self._cgroup, self._cgroup_parent = _create_private_cgroup()
            except NativeMemoryLimitCleanupError:
                raise
            except NativeMemoryLimitUnsupportedError:
                self._apply_rlimit(growth_bytes)
                return
        try:
            self._backend = "cgroup"
            assert self._cgroup is not None
            current = _linux_cgroup_current(self._cgroup)
            (self._cgroup / "memory.max").write_text(
                f"{current + growth_bytes}\n", encoding="ascii"
            )
        except (OSError, ValueError):
            path, parent = self._cgroup, self._cgroup_parent
            assert path is not None and parent is not None
            _unwind_private_cgroup(path, parent, move_self=True)
            self._cgroup = None
            self._cgroup_parent = None
            self._backend = None
            try:
                self._apply_rlimit(growth_bytes)
            except NativeMemoryLimitUnsupportedError as fallback_error:
                raise NativeMemoryLimitUnsupportedError(
                    "cgroup memory limit programming failed and RLIMIT_AS is unavailable"
                ) from fallback_error

    def _apply_rlimit(self, growth_bytes: int) -> None:
        resource_api = _resource_api()
        if resource_api is None or sys.platform == "darwin":
            raise NativeMemoryLimitUnsupportedError("native address-space limits are unavailable")
        soft, hard = resource_api.getrlimit(resource_api.RLIMIT_AS)
        if self._rlimit_original is None:
            self._rlimit_original = (soft, hard)
        infinity = resource_api.RLIM_INFINITY
        ceiling = _native_baseline_bytes() + growth_bytes
        if hard != infinity:
            ceiling = min(ceiling, int(hard))
        if soft != infinity:
            ceiling = min(ceiling, int(soft))
        if ceiling <= 0:
            raise NativeMemoryLimitUnsupportedError("native address-space hard limit is exhausted")
        # Only change soft; warm workers may restore and later raise it, but
        # never increase the inherited hard ceiling.
        resource_api.setrlimit(resource_api.RLIMIT_AS, (ceiling, hard))
        self._backend = "rlimit"
        # The hard limit is deliberately retained verbatim.  A finite
        # inherited soft limit is never widened by a request lease.

    def _apply_windows(self, growth_bytes: int) -> None:
        # A warm lease retains its Job Object after ``restore``. Clear the
        # evidence before reprogramming it so a failed best-effort attempt
        # cannot claim (or later restore) a cap that was not installed.
        self._backend = None
        if self._job is None:
            self._job = _create_windows_job()
            atexit.register(self.close)
        self._set_windows_limit(_windows_private_usage() + growth_bytes)
        self._backend = "windows_job"

    def _set_windows_limit(self, limit: int | None) -> None:
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_JOB_MEMORY if limit else 0
        info.JobMemoryLimit = 0 if limit is None else limit
        kernel32, _psapi = _windows_apis()
        if not kernel32.SetInformationJobObject(
            self._job, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise _windows_error()


def _unwind_private_cgroup(path: Path, parent: Path, *, move_self: bool) -> None:
    """Move this process back and exactly remove a direct private cgroup."""
    if not _validated_private_cgroup(parent, path, pid=os.getpid()):
        raise NativeMemoryLimitCleanupError("refusing to remove an unvalidated private cgroup")
    try:
        if move_self:
            (parent / "cgroup.procs").write_text(f"{os.getpid()}\n", encoding="ascii")
        path.rmdir()
    except OSError as exc:
        raise NativeMemoryLimitCleanupError(
            f"could not safely remove private cgroup {path.name!r}"
        ) from exc


def _create_private_cgroup() -> tuple[Path, Path]:
    parent = _current_cgroup_path()
    name = f"haute-{os.getpid()}-{os.urandom(8).hex()}"
    if not name.startswith("haute-") or "/" in name:
        raise NativeMemoryLimitUnsupportedError("unsafe cgroup name")
    child = parent / name
    moved = False
    try:
        child.mkdir(mode=0o700)
        # Resolve after creation and require the exact direct child, preventing
        # path tricks and symlink traversal.
        if child.resolve(strict=True).parent != parent:
            raise NativeMemoryLimitUnsupportedError("unsafe cgroup path")
        (child / "cgroup.procs").write_text(f"{os.getpid()}\n", encoding="ascii")
        moved = True
        return child, parent
    except (OSError, RuntimeError) as exc:
        if child.exists():
            _unwind_private_cgroup(child, parent, move_self=moved)
        raise NativeMemoryLimitUnsupportedError(
            "no writable delegated cgroup v2 controller"
        ) from exc


_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IO_COUNTERS(ctypes.Structure):  # noqa: N801
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
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


def _windows_apis() -> tuple[Any, Any]:
    """Return Win32 APIs with pointer-width-safe ctypes declarations."""
    windll_factory = getattr(ctypes, "WinDLL", None)
    if windll_factory is None:
        raise NativeMemoryLimitUnsupportedError("Win32 APIs are unavailable")
    kernel32 = windll_factory("kernel32", use_last_error=True)
    psapi = windll_factory("psapi", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = ()
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD,
    )
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return kernel32, psapi


def _windows_private_usage() -> int:
    counters = _PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(counters)
    kernel32, psapi = _windows_apis()
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise _windows_error()
    return int(counters.PrivateUsage)


def _windows_error() -> OSError:
    error = _windows_error_code()
    win_error = getattr(ctypes, "WinError", None)
    return win_error(error) if win_error is not None else OSError(error, "Win32 API call failed")


def _windows_error_code() -> int:
    return int(getattr(ctypes, "get_last_error", lambda: 0)())


def _create_windows_job() -> Any:
    kernel32, _psapi = _windows_apis()
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise NativeMemoryLimitUnsupportedError(f"CreateJobObject failed: {_windows_error_code()}")
    if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
        error = _windows_error_code()
        kernel32.CloseHandle(handle)
        raise NativeMemoryLimitUnsupportedError(f"AssignProcessToJobObject failed: {error}")
    return handle
