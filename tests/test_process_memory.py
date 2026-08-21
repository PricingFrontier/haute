from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

import pytest

import haute._process_memory as memory_mod


class _Callable:
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        return self.callback(*args)


def test_process_rss_rejects_invalid_pid() -> None:
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            memory_mod.process_rss_bytes(value)  # type: ignore[arg-type]


def test_proc_rss_parses_kib_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kwargs: "Name:\tworker\nVmRSS:\t123 kB\n",
    )
    assert memory_mod._proc_process_rss_bytes(7) == 123 * 1024

    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "VmRSS: bad kB")
    assert memory_mod._proc_process_rss_bytes(7) is None

    def _missing(*_args: Any, **_kwargs: Any) -> str:
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_text", _missing)
    assert memory_mod._proc_process_rss_bytes(7) is None


def test_process_rss_dispatches_by_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_mod, "_windows_process_rss_bytes", lambda pid: pid + 1)
    monkeypatch.setattr(memory_mod, "_darwin_process_rss_bytes", lambda pid: pid + 2)
    monkeypatch.setattr(memory_mod, "_proc_process_rss_bytes", lambda pid: pid + 3)

    monkeypatch.setattr(memory_mod.sys, "platform", "win32")
    assert memory_mod.process_rss_bytes(10) == 11
    monkeypatch.setattr(memory_mod.sys, "platform", "darwin")
    assert memory_mod.process_rss_bytes(10) == 12
    monkeypatch.setattr(memory_mod.sys, "platform", "linux")
    assert memory_mod.process_rss_bytes(10) == 13


def test_windows_rss_closes_handle_and_returns_working_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    class _Kernel:
        OpenProcess = _Callable(lambda *_args: 99)
        CloseHandle = _Callable(lambda handle: closed.append(handle) or 1)

    def _memory_info(_handle: int, counters: Any, _size: int) -> int:
        counters._obj.WorkingSetSize = 456  # noqa: SLF001 - ctypes output parameter
        return 1

    class _Psapi:
        GetProcessMemoryInfo = _Callable(_memory_info)

    monkeypatch.setattr(
        memory_mod.ctypes,
        "WinDLL",
        lambda name, **_kwargs: _Kernel() if name == "kernel32" else _Psapi(),
        raising=False,
    )

    assert memory_mod._windows_process_rss_bytes(8) == 456
    assert closed == [99]


def test_darwin_rss_reads_resident_field(monkeypatch: pytest.MonkeyPatch) -> None:
    def _proc_pidinfo(
        _pid: int,
        _flavour: int,
        _arg: int,
        buffer: Any,
        _size: int,
    ) -> int:
        fields = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint64))
        fields[0] = 999
        fields[1] = 321
        return 2 * ctypes.sizeof(ctypes.c_uint64)

    class _Libproc:
        proc_pidinfo = _Callable(_proc_pidinfo)

    monkeypatch.setattr(memory_mod.ctypes, "CDLL", lambda *_args, **_kwargs: _Libproc())

    assert memory_mod._darwin_process_rss_bytes(9) == 321


def test_sampling_support_reflects_current_process_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_mod, "process_rss_bytes", lambda _pid: None)
    assert memory_mod.process_rss_sampling_supported() is False
    monkeypatch.setattr(memory_mod, "process_rss_bytes", lambda _pid: 1)
    assert memory_mod.process_rss_sampling_supported() is True


def test_process_is_alive_uses_signal_zero_and_preserves_uncertainty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_mod.sys, "platform", "linux")
    seen: list[tuple[int, int]] = []
    monkeypatch.setattr(memory_mod.os, "kill", lambda pid, signal: seen.append((pid, signal)))
    assert memory_mod.process_is_alive(12) is True
    assert seen == [(12, 0)]

    monkeypatch.setattr(
        memory_mod.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert memory_mod.process_is_alive(12) is False
    monkeypatch.setattr(
        memory_mod.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(PermissionError()),
    )
    assert memory_mod.process_is_alive(12) is True


def test_process_is_alive_dispatches_to_windows_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_mod.sys, "platform", "win32")
    monkeypatch.setattr(memory_mod, "_windows_process_is_alive", lambda pid: pid == 7)
    assert memory_mod.process_is_alive(7) is True
    assert memory_mod.process_is_alive(8) is False


def test_process_liveness_rejects_invalid_and_handles_os_uncertainty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in (0, -1, True, "1"):
        with pytest.raises(ValueError, match="positive integer"):
            memory_mod.process_is_alive(value)  # type: ignore[arg-type]
    monkeypatch.setattr(memory_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        memory_mod.os, "kill", lambda *_args: (_ for _ in ()).throw(OSError("busy"))
    )
    assert memory_mod.process_is_alive(1) is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Name: worker\n", None),
        ("VmRSS: 1 MB\n", None),
        ("VmRSS: -1 kB\n", None),
        ("VmRSS: 12 kB\n", 12 * 1024),
    ],
)
def test_proc_rss_defensive_shapes(
    monkeypatch: pytest.MonkeyPatch, text: str, expected: int | None
) -> None:
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: text)
    assert memory_mod._proc_process_rss_bytes(1) == expected
    monkeypatch.setattr(
        Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError())
    )
    assert memory_mod._proc_process_rss_bytes(1) is None


def test_windows_probes_fail_closed_or_preserve_liveness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(memory_mod.ctypes, "WinDLL", raising=False)
    assert memory_mod._windows_process_rss_bytes(1) is None
    assert memory_mod._windows_process_is_alive(1) is True

    class Kernel:
        OpenProcess = _Callable(lambda *_args: 0)
        CloseHandle = _Callable(lambda *_args: 1)
        GetExitCodeProcess = _Callable(lambda *_args: 1)

    monkeypatch.setattr(memory_mod.ctypes, "WinDLL", lambda *_args, **_kw: Kernel(), raising=False)
    monkeypatch.setattr(memory_mod.ctypes, "get_last_error", lambda: 87, raising=False)
    assert memory_mod._windows_process_is_alive(1) is False
    monkeypatch.setattr(memory_mod.ctypes, "get_last_error", lambda: 5, raising=False)
    assert memory_mod._windows_process_is_alive(1) is True


def test_windows_probe_exception_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        memory_mod.ctypes,
        "WinDLL",
        lambda *_args, **_kw: (_ for _ in ()).throw(OSError()),
        raising=False,
    )
    assert memory_mod._windows_process_rss_bytes(1) is None
    assert memory_mod._windows_process_is_alive(1) is True


def test_darwin_rss_short_and_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class ShortLibrary:
        proc_pidinfo = _Callable(lambda *_args: 0)

    monkeypatch.setattr(memory_mod.ctypes, "CDLL", lambda *_args, **_kw: ShortLibrary())
    assert memory_mod._darwin_process_rss_bytes(1) is None


def test_windows_rss_fallback_info_failure_and_liveness_exit_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    class Kernel:
        OpenProcess = _Callable(lambda *_args: 0)
        CloseHandle = _Callable(lambda handle: closed.append(handle) or 1)
        GetExitCodeProcess = _Callable(lambda _handle, code: setattr(code._obj, "value", 259) or 1)

    class Psapi:
        GetProcessMemoryInfo = _Callable(lambda *_args: 0)

    monkeypatch.setattr(
        memory_mod.ctypes,
        "WinDLL",
        lambda name, **_kwargs: Kernel() if name == "kernel32" else Psapi(),
        raising=False,
    )
    assert memory_mod._windows_process_rss_bytes(1) is None

    Kernel.OpenProcess = _Callable(lambda *_args: 99)
    assert memory_mod._windows_process_rss_bytes(1) is None
    assert closed == [99]
    assert memory_mod._windows_process_is_alive(1) is True
    assert closed[-1] == 99

    Kernel.GetExitCodeProcess = _Callable(lambda _handle, _code: 0)
    assert memory_mod._windows_process_is_alive(1) is True
    Kernel.GetExitCodeProcess = _Callable(lambda _handle, code: setattr(code._obj, "value", 1) or 1)
    assert memory_mod._windows_process_is_alive(1) is False
    monkeypatch.setattr(
        memory_mod.ctypes, "CDLL", lambda *_args, **_kw: (_ for _ in ()).throw(OSError())
    )
    assert memory_mod._darwin_process_rss_bytes(1) is None
