from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import haute._native_memory_limit as native


def test_linux_prefers_cgroup_and_uses_current_growth(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []
    group = Path("/private/group")
    lease = native.NativeMemoryLease()
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_create_private_cgroup", lambda: (group, Path("/private")))
    monkeypatch.setattr(native, "_linux_cgroup_current", lambda _path: 100)
    monkeypatch.setattr(Path, "write_text", lambda _self, value, **_kwargs: writes.append(value))

    assert lease.apply(23, required=True)
    assert lease._backend == "cgroup"
    assert writes == ["123\n"]


def test_linux_falls_back_to_rlimit_when_cgroup_is_not_delegated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = native.NativeMemoryLease()
    applied: list[tuple[int, tuple[int, int]]] = []
    fake_resource = SimpleNamespace(
        RLIMIT_AS=9,
        RLIM_INFINITY=-1,
        getrlimit=lambda _limit: (-1, 500),
        setrlimit=lambda limit, values: applied.append((limit, values)),
    )
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(
        native,
        "_create_private_cgroup",
        lambda: (_ for _ in ()).throw(native.NativeMemoryLimitUnsupportedError("no cgroup")),
    )
    monkeypatch.setattr(native, "_rlimit_as_supported", lambda: True)
    monkeypatch.setattr(native, "_native_baseline_bytes", lambda: 200)
    monkeypatch.setitem(__import__("sys").modules, "resource", fake_resource)

    assert lease.apply(400, required=True)
    assert applied == [(9, (500, 500))]


def test_best_effort_setup_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = native.NativeMemoryLease()
    monkeypatch.setattr(native.sys, "platform", "darwin")
    assert lease.apply(10, required=False) is False
    with pytest.raises(native.NativeMemoryLimitUnsupportedError):
        lease.apply(10, required=True)


def test_active_native_backend_is_scoped_to_the_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = native.NativeMemoryLease()
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(
        native.NativeMemoryLease,
        "_apply_windows",
        lambda self, _growth: setattr(self, "_backend", "windows_job"),
    )
    monkeypatch.setattr(native.NativeMemoryLease, "_set_windows_limit", lambda *_args: None)

    assert lease.apply(10, required=True) is True
    assert native.current_native_memory_backend() is None
    with native.native_memory_backend_scope(lease.backend):
        assert native.current_native_memory_backend() == "windows_job"
    lease.restore()
    assert native.current_native_memory_backend() is None


def test_cgroup_close_restores_exact_parent_before_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class Node:
        def __init__(self, name: str) -> None:
            self.name = name

        def __truediv__(self, name: str):
            return File(f"{self.name}/{name}")

        def rmdir(self) -> None:
            calls.append(("rmdir", self.name))

    class File:
        def __init__(self, name: str) -> None:
            self.name = name

        def write_text(self, value: str, **_kwargs: object) -> None:
            calls.append((self.name, value))

    monkeypatch.setattr(
        native,
        "_unwind_private_cgroup",
        lambda path, parent, *, move_self: (
            (parent / "cgroup.procs").write_text(f"{native.os.getpid()}\n"),
            path.rmdir(),
        ),
    )
    lease = native.NativeMemoryLease(
        _backend="cgroup", _cgroup=Node("child"), _cgroup_parent=Node("parent")
    )
    lease.close()
    assert calls == [("parent/cgroup.procs", f"{native.os.getpid()}\n"), ("rmdir", "child")]
    assert lease._cgroup is None


def test_current_cgroup_path_stays_beneath_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "safe").mkdir()
    monkeypatch.setattr(native, "_cgroup_parent", lambda: tmp_path)
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, **kwargs: (
            "0::/safe\n"
            if str(path).replace("\\", "/") == "/proc/self/cgroup"
            else original(path, **kwargs)
        ),
    )
    assert native._current_cgroup_path() == (tmp_path / "safe").resolve()


def test_windows_apis_fail_closed_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(native.ctypes, "WinDLL", raising=False)

    with pytest.raises(
        native.NativeMemoryLimitUnsupportedError, match="Win32 APIs are unavailable"
    ):
        native._windows_apis()


def test_windows_job_uses_pointer_width_safe_handle_and_reports_assignment_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Function:
        def __init__(self, value):
            self.value = value
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.value(*args) if callable(self.value) else self.value

    huge_handle = 1 << 40
    closed: list[int] = []
    kernel32 = SimpleNamespace(
        CreateJobObjectW=Function(huge_handle),
        AssignProcessToJobObject=Function(True),
        SetInformationJobObject=Function(True),
        CloseHandle=Function(lambda handle: closed.append(handle) or True),
        GetCurrentProcess=Function(-1),
    )
    psapi = SimpleNamespace(GetProcessMemoryInfo=Function(True))
    monkeypatch.setattr(
        native.ctypes,
        "WinDLL",
        lambda name, **_kwargs: kernel32 if name == "kernel32" else psapi,
        raising=False,
    )
    assert native._create_windows_job() == huge_handle
    assert kernel32.CreateJobObjectW.restype is native.wintypes.HANDLE
    assert kernel32.AssignProcessToJobObject.argtypes == (
        native.wintypes.HANDLE,
        native.wintypes.HANDLE,
    )

    kernel32.AssignProcessToJobObject.value = False
    monkeypatch.setattr(native.ctypes, "get_last_error", lambda: 5, raising=False)
    with pytest.raises(
        native.NativeMemoryLimitUnsupportedError, match="AssignProcessToJobObject failed: 5"
    ):
        native._create_windows_job()
    assert closed == [huge_handle]


def test_windows_lease_programs_an_aggregate_job_memory_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[int, int, int]] = []

    def set_information(_job, _kind, pointer, _size):
        info = native.ctypes.cast(
            pointer,
            native.ctypes.POINTER(native._JOBOBJECT_EXTENDED_LIMIT_INFORMATION),
        ).contents
        observed.append(
            (
                int(info.BasicLimitInformation.LimitFlags),
                int(info.ProcessMemoryLimit),
                int(info.JobMemoryLimit),
            )
        )
        return True

    kernel32 = SimpleNamespace(SetInformationJobObject=set_information)
    monkeypatch.setattr(native, "_windows_apis", lambda: (kernel32, SimpleNamespace()))
    lease = native.NativeMemoryLease(_job=123)

    lease._set_windows_limit(456)
    lease._set_windows_limit(None)

    assert observed == [
        (native._JOB_OBJECT_LIMIT_JOB_MEMORY, 0, 456),
        (0, 0, 0),
    ]


def test_failed_best_effort_windows_programming_clears_backend_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int | None] = []

    def set_limit(_lease: native.NativeMemoryLease, limit: int | None) -> None:
        calls.append(limit)
        if limit is not None:
            raise OSError("job policy rejected the requested limit")

    lease = native.NativeMemoryLease(_backend="windows_job", _job=123)
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native, "_windows_private_usage", lambda: 100)
    monkeypatch.setattr(native.NativeMemoryLease, "_set_windows_limit", set_limit)

    assert lease.apply(25, required=False) is False
    assert lease.backend is None
    lease.restore()
    assert calls == [125]


def test_rlimit_never_widens_finite_soft_limit_and_restores_exact_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []
    current = [50, 80]

    def setrlimit(limit: int, values: tuple[int, int]) -> None:
        calls.append((limit, values))
        current[:] = values

    fake_resource = SimpleNamespace(
        RLIMIT_AS=9,
        RLIM_INFINITY=-1,
        getrlimit=lambda _limit: tuple(current),
        setrlimit=setrlimit,
    )
    monkeypatch.setattr(native, "_rlimit_as_supported", lambda: True)
    monkeypatch.setattr(native, "_native_baseline_bytes", lambda: 100)
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    lease = native.NativeMemoryLease()

    lease._apply_rlimit(500)
    lease.restore()
    lease._apply_rlimit(20)
    lease.restore()

    assert calls == [(9, (50, 80)), (9, (50, 80)), (9, (50, 80)), (9, (50, 80))]


def test_rlimit_infinity_restores_exact_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []
    current = [-1, -1]
    fake_resource = SimpleNamespace(
        RLIMIT_AS=9,
        RLIM_INFINITY=-1,
        getrlimit=lambda _limit: tuple(current),
        setrlimit=lambda limit, values: (
            calls.append((limit, values)),
            current.__setitem__(slice(None), values),
        ),
    )
    monkeypatch.setattr(native, "_rlimit_as_supported", lambda: True)
    monkeypatch.setattr(native, "_native_baseline_bytes", lambda: 100)
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    lease = native.NativeMemoryLease()

    lease._apply_rlimit(25)
    lease.restore()

    assert calls == [(9, (125, -1)), (9, (-1, -1))]


def test_parent_cleanup_removes_only_empty_exact_private_cgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = 123
    exact = tmp_path / "haute-123-aaaaaaaaaaaaaaaa"
    exact.mkdir()
    (exact / "cgroup.procs").write_text("", encoding="ascii")
    malformed = tmp_path / "haute-123-not-hex"
    malformed.mkdir()
    (malformed / "cgroup.procs").write_text("", encoding="ascii")
    nonempty = tmp_path / "haute-123-bbbbbbbbbbbbbbbb"
    nonempty.mkdir()
    (nonempty / "cgroup.procs").write_text("9\n", encoding="ascii")
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_current_cgroup_path", lambda: tmp_path)
    removed: list[Path] = []
    monkeypatch.setattr(Path, "rmdir", lambda path: removed.append(path))

    native.cleanup_private_cgroups_for_pid(pid)

    assert removed == [exact]
    assert malformed.exists()
    assert nonempty.exists()


def test_parent_cleanup_refuses_outside_candidate_and_surfaces_rmdir_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Candidate:
        name = "haute-123-aaaaaaaaaaaaaaaa"

        def resolve(self, *, strict: bool):
            return tmp_path.parent / "outside"

    assert native._validated_private_cgroup(tmp_path, Candidate(), pid=123) is False

    child = tmp_path / "haute-123-aaaaaaaaaaaaaaaa"
    child.mkdir()
    (child / "cgroup.procs").write_text("", encoding="ascii")
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_current_cgroup_path", lambda: tmp_path)
    monkeypatch.setattr(Path, "rmdir", lambda _path: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(native.NativeMemoryLimitCleanupError, match="could not remove"):
        native.cleanup_private_cgroups_for_pid(123)


def test_linux_programming_failure_unwinds_then_falls_back_to_rlimit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = native.NativeMemoryLease()
    child, parent = Path("/child"), Path("/parent")
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_create_private_cgroup", lambda: (child, parent))
    monkeypatch.setattr(
        native,
        "_linux_cgroup_current",
        lambda _path: (_ for _ in ()).throw(OSError("no memory.current")),
    )
    monkeypatch.setattr(
        native, "_unwind_private_cgroup", lambda *_args, **_kwargs: calls.append(("unwind", 0))
    )
    monkeypatch.setattr(
        native.NativeMemoryLease,
        "_apply_rlimit",
        lambda _lease, growth: calls.append(("rlimit", growth)),
    )

    assert lease.apply(77, required=True)
    assert calls == [("unwind", 0), ("rlimit", 77)]
    assert lease._cgroup is None


def test_linux_programming_unwind_failure_is_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = native.NativeMemoryLease()
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_create_private_cgroup", lambda: (Path("/child"), Path("/parent")))
    monkeypatch.setattr(
        native, "_linux_cgroup_current", lambda _path: (_ for _ in ()).throw(OSError("bad"))
    )
    monkeypatch.setattr(
        native,
        "_unwind_private_cgroup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            native.NativeMemoryLimitCleanupError("leak")
        ),
    )
    with pytest.raises(native.NativeMemoryLimitCleanupError, match="leak"):
        lease.apply(77, required=False)


def test_native_support_helpers_and_invalid_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="unknown"):
        with native.native_memory_backend_scope("bogus"):  # type: ignore[arg-type]
            pass
    monkeypatch.setattr(native, "_resource_api", lambda: None)
    monkeypatch.setattr(native.sys, "platform", "darwin")
    assert not native.native_memory_caps_supported()
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_linux_cgroup_v2_available", lambda: False)
    assert not native.native_memory_caps_supported()
    monkeypatch.setattr(native, "_resource_api", lambda: object())
    monkeypatch.setattr(native.sys, "platform", "freebsd")
    assert native.native_memory_caps_supported()
    monkeypatch.setattr(native.sys, "platform", "win32")
    assert native.native_memory_caps_supported()
    native.cleanup_private_cgroups_for_pid(123)


def test_native_linux_path_and_measurement_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(native.sys, "platform", "linux")
    current_cgroup_path = native._current_cgroup_path
    monkeypatch.setattr(
        native,
        "_current_cgroup_path",
        lambda: (_ for _ in ()).throw(native.NativeMemoryLimitUnsupportedError("no")),
    )
    assert not native._linux_cgroup_v2_available()
    monkeypatch.setattr(native, "_current_cgroup_path", current_cgroup_path)
    monkeypatch.setattr(native, "_cgroup_parent", lambda: tmp_path)
    monkeypatch.setattr(native.Path, "read_text", lambda *_args, **_kwargs: "0::/../../bad\n")
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="unsafe"):
        native._current_cgroup_path()
    monkeypatch.setattr(native.Path, "read_text", lambda *_args, **_kwargs: "missing")
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="cannot locate"):
        native._current_cgroup_path()
    monkeypatch.setattr(native.Path, "read_text", lambda *_args, **_kwargs: "not-a-number")
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="cannot measure"):
        native._linux_virtual_bytes()


def test_native_lease_errors_restore_close_and_windows_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        native.NativeMemoryLease().apply(0, required=True)
    lease = native.NativeMemoryLease(_backend="rlimit", _rlimit_original=(1, 2))
    monkeypatch.setattr(native, "_resource_api", lambda: None)
    with pytest.raises(native.NativeMemoryLimitUnsupportedError):
        lease.restore()
    group = tmp_path / "group"
    group.mkdir()
    lease = native.NativeMemoryLease(_backend="cgroup", _cgroup=group)
    lease.restore()
    assert (group / "memory.max").read_text(encoding="ascii") == "max\n"
    closed: list[object] = []
    kernel = SimpleNamespace(CloseHandle=lambda handle: closed.append(handle) or True)
    lease = native.NativeMemoryLease(_job=99)
    monkeypatch.setattr(native, "_windows_apis", lambda: (kernel, None))
    lease.close()
    assert closed == [99]
    create_windows_job = native._create_windows_job
    monkeypatch.setattr(native, "_windows_private_usage", lambda: 10)
    monkeypatch.setattr(native.atexit, "register", lambda *_args: None)
    job = SimpleNamespace()
    lease = native.NativeMemoryLease()
    monkeypatch.setattr(native, "_create_windows_job", lambda: job)
    limits: list[int | None] = []
    monkeypatch.setattr(
        native.NativeMemoryLease, "_set_windows_limit", lambda _lease, value: limits.append(value)
    )
    lease._apply_windows(3)
    assert lease.backend == "windows_job" and limits == [13]
    monkeypatch.setattr(native, "_create_windows_job", create_windows_job)
    monkeypatch.setattr(
        native, "_windows_apis", lambda: (SimpleNamespace(CreateJobObjectW=lambda *_: 0), None)
    )
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="CreateJobObject"):
        native._create_windows_job()


def test_native_rlimit_and_cgroup_cleanup_error_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = SimpleNamespace(
        RLIMIT_AS=1, RLIM_INFINITY=-1, getrlimit=lambda _x: (0, 0), setrlimit=lambda *_x: None
    )
    monkeypatch.setattr(native, "_resource_api", lambda: api)
    monkeypatch.setattr(native, "_native_baseline_bytes", lambda: 0)
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="exhausted"):
        native.NativeMemoryLease()._apply_rlimit(1)
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / f"haute-{native.os.getpid()}-{'a' * 16}"
    child.mkdir()
    monkeypatch.setattr(native, "_validated_private_cgroup", lambda *_args, **_kwargs: False)
    with pytest.raises(native.NativeMemoryLimitCleanupError, match="unvalidated"):
        native._unwind_private_cgroup(child, parent, move_self=False)
    monkeypatch.setattr(native, "_validated_private_cgroup", lambda *_args, **_kwargs: True)
    native._unwind_private_cgroup(child, parent, move_self=False)
    assert not child.exists()


def test_native_platform_adapter_error_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(native.importlib, "import_module", lambda _name: SimpleNamespace())
    assert native._resource_api() is None
    assert native._cgroup_parent() == Path("/sys/fs/cgroup")
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native, "_windows_private_usage", lambda: 12)
    assert native._native_baseline_bytes() == 12
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(native, "_linux_virtual_bytes", lambda: 13)
    assert native._native_baseline_bytes() == 13
    monkeypatch.setattr(native.sys, "platform", "freebsd")
    assert native._native_baseline_bytes() == 0
    monkeypatch.setattr(native, "_cgroup_parent", lambda: tmp_path / "missing")
    monkeypatch.setattr(native.Path, "read_text", lambda *_args, **_kwargs: "0::/x\n")
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="outside"):
        native._current_cgroup_path()

    kernel = SimpleNamespace(CloseHandle=lambda _handle: False)
    monkeypatch.setattr(native, "_windows_apis", lambda: (kernel, None))
    monkeypatch.setattr(native, "_windows_error", lambda: OSError("close"))
    with pytest.raises(OSError, match="close"):
        native.NativeMemoryLease(_job=1).close()


def test_resource_api_returns_none_when_resource_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def import_module(_name: str) -> None:
        raise ImportError("resource is unavailable")

    monkeypatch.setattr(native.importlib, "import_module", import_module)

    assert native._resource_api() is None


def test_native_cgroup_creation_and_adapter_failure_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    monkeypatch.setattr(native, "_current_cgroup_path", lambda: parent)
    monkeypatch.setattr(native.os, "urandom", lambda _size: b"a" * 8)
    child, returned_parent = native._create_private_cgroup()
    assert returned_parent == parent
    (child / "cgroup.procs").unlink()
    native._unwind_private_cgroup(child, parent, move_self=True)
    assert not child.exists()

    wrong_pid = parent / f"haute-{native.os.getpid() + 1}-{'a' * 16}"
    wrong_pid.mkdir()
    assert not native._validated_private_cgroup(parent, wrong_pid, pid=native.os.getpid())
    assert not native._validated_private_cgroup(
        parent, parent / "not-private", pid=native.os.getpid()
    )

    monkeypatch.setattr(native.os, "sysconf", None, raising=False)
    monkeypatch.setattr(native.Path, "read_text", lambda *_args, **_kwargs: "1 0")
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="cannot measure"):
        native._linux_virtual_bytes()

    kernel = SimpleNamespace(GetCurrentProcess=lambda: 1)
    psapi = SimpleNamespace(GetProcessMemoryInfo=lambda *_args: False)
    monkeypatch.setattr(native, "_windows_apis", lambda: (kernel, psapi))
    monkeypatch.setattr(native, "_windows_error", lambda: OSError("memory"))
    with pytest.raises(OSError, match="memory"):
        native._windows_private_usage()


def test_resource_protocol_default_methods_are_callable_for_test_adapters() -> None:
    class ConcreteResourceApi(native._ResourceApi):
        RLIMIT_AS = 1
        RLIM_INFINITY = -1

    api = ConcreteResourceApi()

    assert api.getrlimit(1) is None
    assert api.setrlimit(1, (2, 3)) is None


def test_native_linux_measurement_and_cleanup_defensive_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_text = Path.read_text

    def read_statm(path: Path, **kwargs: object) -> str:
        if path == Path("/proc/self/statm"):
            return "2 0"
        return original_read_text(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_statm)
    monkeypatch.setattr(native.os, "sysconf", lambda _name: 4096, raising=False)
    assert native._linux_virtual_bytes() == 8192

    group = tmp_path / "group"
    group.mkdir()
    (group / "memory.current").write_text("123\n", encoding="ascii")
    assert native._linux_cgroup_current(group) == 123

    class UnresolvableCandidate:
        name = "haute-123-aaaaaaaaaaaaaaaa"

        def resolve(self, *, strict: bool) -> Path:
            raise OSError("identity changed")

    assert not native._validated_private_cgroup(
        tmp_path,
        UnresolvableCandidate(),  # type: ignore[arg-type]
        pid=123,
    )

    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(
        native,
        "_current_cgroup_path",
        lambda: (_ for _ in ()).throw(native.NativeMemoryLimitUnsupportedError("no cgroup")),
    )
    native.cleanup_private_cgroups_for_pid(123)


def test_native_apply_preserves_required_and_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native.sys, "platform", "freebsd")
    monkeypatch.setattr(
        native.NativeMemoryLease,
        "_apply_rlimit",
        lambda _lease, _growth: (_ for _ in ()).throw(OSError("rlimit failed")),
    )
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="setup failed"):
        native.NativeMemoryLease().apply(1, required=True)

    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(
        native,
        "_create_private_cgroup",
        lambda: (_ for _ in ()).throw(native.NativeMemoryLimitCleanupError("cleanup failed")),
    )
    with pytest.raises(native.NativeMemoryLimitCleanupError, match="cleanup failed"):
        native.NativeMemoryLease().apply(1, required=False)


def test_linux_existing_cgroup_and_double_fallback_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child, parent = Path("/child"), Path("/parent")
    lease = native.NativeMemoryLease(_cgroup=child, _cgroup_parent=parent)
    writes: list[str] = []
    monkeypatch.setattr(native, "_linux_cgroup_current", lambda _path: 10)
    monkeypatch.setattr(Path, "write_text", lambda _path, value, **_kwargs: writes.append(value))

    lease._apply_linux(5)

    assert writes == ["15\n"]

    lease = native.NativeMemoryLease()
    monkeypatch.setattr(native, "_create_private_cgroup", lambda: (child, parent))
    monkeypatch.setattr(
        native,
        "_linux_cgroup_current",
        lambda _path: (_ for _ in ()).throw(OSError("programming failed")),
    )
    monkeypatch.setattr(native, "_unwind_private_cgroup", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        native.NativeMemoryLease,
        "_apply_rlimit",
        lambda _lease, _growth: (_ for _ in ()).throw(
            native.NativeMemoryLimitUnsupportedError("no rlimit")
        ),
    )

    with pytest.raises(
        native.NativeMemoryLimitUnsupportedError,
        match="cgroup memory limit programming failed",
    ):
        lease._apply_linux(5)


def test_rlimit_reapplication_retains_original_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []
    api = SimpleNamespace(
        RLIMIT_AS=1,
        RLIM_INFINITY=-1,
        getrlimit=lambda _limit: (100, 200),
        setrlimit=lambda limit, values: calls.append((limit, values)),
    )
    monkeypatch.setattr(native, "_resource_api", lambda: api)
    monkeypatch.setattr(native, "_native_baseline_bytes", lambda: 10)
    lease = native.NativeMemoryLease()

    lease._apply_rlimit(20)
    lease._apply_rlimit(30)

    assert lease._rlimit_original == (100, 200)
    assert calls == [(1, (30, 200)), (1, (40, 200))]


def test_windows_limit_programming_and_usage_error_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows_error = native._windows_error
    lease = native.NativeMemoryLease(_job=1)
    monkeypatch.setattr(
        native,
        "_windows_apis",
        lambda: (SimpleNamespace(SetInformationJobObject=lambda *_args: False), None),
    )
    monkeypatch.setattr(native, "_windows_error", lambda: OSError("set failed"))
    with pytest.raises(OSError, match="set failed"):
        lease._set_windows_limit(10)

    def get_process_memory_info(_process: object, pointer: object, _size: int) -> bool:
        counters = native.ctypes.cast(
            pointer,
            native.ctypes.POINTER(native._PROCESS_MEMORY_COUNTERS_EX),
        ).contents
        counters.PrivateUsage = 321
        return True

    kernel = SimpleNamespace(GetCurrentProcess=lambda: 1)
    psapi = SimpleNamespace(GetProcessMemoryInfo=get_process_memory_info)
    monkeypatch.setattr(native, "_windows_apis", lambda: (kernel, psapi))
    assert native._windows_private_usage() == 321

    monkeypatch.setattr(native, "_windows_error", windows_error)
    monkeypatch.setattr(native.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        native.ctypes,
        "WinError",
        lambda error: OSError(error, "win32 failure"),
        raising=False,
    )
    assert native._windows_error().errno == 5
    monkeypatch.setattr(native.ctypes, "WinError", None, raising=False)
    assert native._windows_error().errno == 5


def test_private_cgroup_creation_rejects_unsafe_identity_and_cleans_partial_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    monkeypatch.setattr(native, "_current_cgroup_path", lambda: parent)
    monkeypatch.setattr(native.os, "urandom", lambda _size: b"a" * 8)

    monkeypatch.setattr(native.os, "getpid", lambda: "bad/name")
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="unsafe cgroup name"):
        native._create_private_cgroup()

    monkeypatch.setattr(native.os, "getpid", lambda: 123)
    child = parent / "haute-123-6161616161616161"
    original_resolve = Path.resolve

    def escaped_resolve(path: Path, *, strict: bool = False) -> Path:
        if path == child:
            return tmp_path / "outside" / child.name
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", escaped_resolve)
    with pytest.raises(native.NativeMemoryLimitCleanupError, match="unvalidated private cgroup"):
        native._create_private_cgroup()
    assert child.exists()

    monkeypatch.setattr(Path, "resolve", original_resolve)
    child.rmdir()
    original_mkdir = Path.mkdir

    def failed_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if path == child:
            raise OSError("mkdir failed")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failed_mkdir)
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="no writable"):
        native._create_private_cgroup()
    monkeypatch.setattr(Path, "mkdir", original_mkdir)

    original_write_text = Path.write_text

    def failed_join(path: Path, value: str, **kwargs: object) -> int:
        if path == child / "cgroup.procs":
            raise OSError("join failed")
        return original_write_text(path, value, **kwargs)

    monkeypatch.setattr(Path, "write_text", failed_join)
    with pytest.raises(native.NativeMemoryLimitUnsupportedError, match="no writable"):
        native._create_private_cgroup()
    assert not child.exists()


def test_private_cgroup_unwind_wraps_filesystem_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / f"haute-{native.os.getpid()}-{'a' * 16}"
    child.mkdir()
    monkeypatch.setattr(native, "_validated_private_cgroup", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(Path, "rmdir", lambda _path: (_ for _ in ()).throw(OSError("busy")))

    with pytest.raises(native.NativeMemoryLimitCleanupError, match="could not safely remove"):
        native._unwind_private_cgroup(child, parent, move_self=False)


def test_restore_clears_backend_evidence_between_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hard-cap evidence lives only between a successful apply and its restore."""
    lease = native.NativeMemoryLease()
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(
        native.NativeMemoryLease,
        "_apply_windows",
        lambda self, _growth: setattr(self, "_backend", "windows_job"),
    )
    monkeypatch.setattr(native.NativeMemoryLease, "_set_windows_limit", lambda *_args: None)

    assert lease.apply(10, required=True) is True
    assert lease.backend == "windows_job"
    lease.restore()
    assert lease.backend is None


def test_failed_best_effort_apply_after_a_successful_request_leaves_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later failed RLIMIT attempt must not inherit the earlier request's backend."""
    attempts: list[int] = []

    def apply_rlimit(self: native.NativeMemoryLease, growth: int) -> None:
        attempts.append(growth)
        if len(attempts) == 1:
            self._backend = "rlimit"
            return
        raise OSError("setrlimit rejected the request")

    lease = native.NativeMemoryLease()
    monkeypatch.setattr(native.sys, "platform", "sunos5")
    monkeypatch.setattr(native.NativeMemoryLease, "_apply_rlimit", apply_rlimit)

    assert lease.apply(10, required=True) is True
    assert lease.backend == "rlimit"
    # A second request without an intervening restore.
    assert lease.apply(10, required=False) is False
    assert lease.backend is None
    # And a second request after the normal restore.
    attempts.clear()
    assert lease.apply(10, required=True) is True
    lease.restore()
    assert lease.backend is None
    assert lease.apply(10, required=False) is False
    assert lease.backend is None
    with pytest.raises(native.NativeMemoryLimitUnsupportedError):
        lease.apply(10, required=True)
    assert lease.backend is None
