from __future__ import annotations

import subprocess
import sys

import psutil
import pytest

import haute._process_memory as memory_mod


def test_current_process_readings_are_real_and_positive() -> None:
    assert isinstance(memory_mod.current_process_rss_bytes(), int)
    assert memory_mod.current_process_rss_bytes() > 0
    assert isinstance(memory_mod.current_process_virtual_bytes(), int)
    assert memory_mod.current_process_virtual_bytes() > 0
    assert isinstance(memory_mod.current_process_thread_count(), int)
    assert memory_mod.current_process_thread_count() > 0

    private = memory_mod.current_process_private_bytes()
    if sys.platform == "win32":
        assert isinstance(private, int)
        assert private > 0
    else:
        assert private is None


def test_process_rss_bytes_and_liveness_for_a_real_child_process() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert memory_mod.process_rss_bytes(child.pid) is not None
        assert memory_mod.process_rss_bytes(child.pid) > 0
        assert memory_mod.process_is_alive(child.pid) is True
    finally:
        child.terminate()
        child.wait()

    assert memory_mod.process_rss_bytes(child.pid) is None
    assert memory_mod.process_is_alive(child.pid) is False


def test_process_rss_bytes_rejects_invalid_pid() -> None:
    for value in (0, -1, True, "1"):
        with pytest.raises(ValueError, match="positive integer"):
            memory_mod.process_rss_bytes(value)  # type: ignore[arg-type]


def test_process_is_alive_rejects_invalid_pid() -> None:
    for value in (0, -1, True, "1"):
        with pytest.raises(ValueError, match="positive integer"):
            memory_mod.process_is_alive(value)  # type: ignore[arg-type]


def test_readings_are_none_when_psutil_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: object) -> None:
        raise psutil.AccessDenied(1)

    monkeypatch.setattr(memory_mod.psutil, "Process", _raise)
    assert memory_mod.process_rss_bytes(1) is None
    assert memory_mod.current_process_rss_bytes() is None
    assert memory_mod.current_process_virtual_bytes() is None
    assert memory_mod.current_process_thread_count() is None
    assert memory_mod.process_rss_sampling_supported() is False


def test_process_is_alive_preserves_uncertainty_on_psutil_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(_pid: int) -> bool:
        raise psutil.AccessDenied(1)

    monkeypatch.setattr(memory_mod.psutil, "pid_exists", _raise)
    assert memory_mod.process_is_alive(1) is True


def test_process_rss_sampling_supported_on_this_host() -> None:
    assert memory_mod.process_rss_sampling_supported() is True


class _FakeProcess:
    """A psutil.Process stand-in with distinct figures for every field."""

    def __init__(self, memory: object, threads: int = 7) -> None:
        self._memory = memory
        self._threads = threads

    def memory_info(self) -> object:
        return self._memory

    def num_threads(self) -> int:
        return self._threads


def _fake_psutil_process(
    monkeypatch: pytest.MonkeyPatch, memory: object, threads: int = 7
) -> list[object]:
    asked: list[object] = []

    def process(pid: object = None) -> _FakeProcess:
        asked.append(pid)
        return _FakeProcess(memory, threads)

    monkeypatch.setattr(memory_mod.psutil, "Process", process)
    return asked


def test_each_reading_comes_from_its_own_memory_field(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    asked = _fake_psutil_process(monkeypatch, SimpleNamespace(rss=11, vms=22, private=33))
    monkeypatch.setattr(memory_mod.sys, "platform", "win32")

    assert memory_mod.current_process_rss_bytes() == 11
    assert memory_mod.current_process_virtual_bytes() == 22
    assert memory_mod.current_process_private_bytes() == 33
    assert memory_mod.current_process_thread_count() == 7
    assert memory_mod.process_rss_bytes(4242) == 11
    assert asked[-1] == 4242


def test_private_bytes_are_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    asked = _fake_psutil_process(monkeypatch, SimpleNamespace(rss=11, vms=22, private=33))
    monkeypatch.setattr(memory_mod.sys, "platform", "linux")

    assert memory_mod.current_process_private_bytes() is None
    assert asked == []


@pytest.mark.parametrize("value", [-1, None, True, 1.5])
def test_a_missing_or_invalid_memory_field_is_unobservable(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    from types import SimpleNamespace

    fields = {"vms": 22} if value is None else {"rss": value, "vms": 22}
    _fake_psutil_process(monkeypatch, SimpleNamespace(**fields))

    assert memory_mod.current_process_rss_bytes() is None
    assert memory_mod.process_rss_bytes(4242) is None
    assert memory_mod.current_process_virtual_bytes() == 22
