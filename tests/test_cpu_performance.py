"""CPU policy uses the Windows contract without making work depend on it."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs

from haute import _cpu_performance as cpu


class WindowsPolicy:
    def __init__(self, control: int = 0, state: int = 0) -> None:
        self.control = control
        self.state = state
        self.gets = 0
        self.sets: list[tuple[int, int]] = []
        self.fail_get = 0
        self.fail_set = False
        self.ignore_set = False
        self.handle = 0x123456789ABC
        self.GetCurrentProcess = Mock(return_value=self.handle)
        self.GetProcessInformation = Mock(side_effect=self.read)
        self.SetProcessInformation = Mock(side_effect=self.write)

    def read(self, handle, info_class, pointer, size):
        assert handle == self.handle
        assert info_class == 4
        assert size == 12
        self.gets += 1
        if self.gets == self.fail_get:
            return 0
        value = pointer._obj
        assert value.Version == 1
        value.ControlMask = self.control
        value.StateMask = self.state
        return 1

    def write(self, handle, info_class, pointer, size):
        assert handle == self.handle
        assert info_class == 4
        assert size == 12
        value = pointer._obj
        assert value.Version == 1
        self.sets.append((value.ControlMask, value.StateMask))
        if self.fail_set:
            return 0
        if not self.ignore_set:
            self.control, self.state = self.sets[-1]
        return 1


@pytest.fixture
def windows(monkeypatch):
    policy = WindowsPolicy()
    monkeypatch.setattr(cpu, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(cpu.ctypes, "WinDLL", Mock(return_value=policy), raising=False)
    monkeypatch.setattr(cpu.ctypes, "get_last_error", lambda: 5, raising=False)
    return policy


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_non_windows_does_not_load_native_apis(monkeypatch, platform):
    monkeypatch.setattr(cpu, "sys", SimpleNamespace(platform=platform))
    factory = Mock(side_effect=AssertionError("Windows API on non-Windows"))
    monkeypatch.setattr(cpu.ctypes, "WinDLL", factory, raising=False)
    assert cpu.configure_process_high_qos() == "not_applicable"
    factory.assert_not_called()


@pytest.mark.parametrize("control,state", [(0, 0), (1, 1), (4, 4), (5, 5), (4, 0)])
def test_requests_and_verifies_high_qos_preserving_other_policy_bits(windows, control, state):
    windows.control, windows.state = control, state
    with capture_logs() as logs:
        assert cpu.configure_process_high_qos() == "high"
    assert windows.sets == [(control | 1, state & ~1)]
    assert windows.gets == 2
    assert windows.GetCurrentProcess.restype is ctypes.c_void_p
    assert windows.GetCurrentProcess.argtypes == []
    assert windows.GetProcessInformation.argtypes[0] is ctypes.c_void_p
    assert windows.SetProcessInformation.argtypes[0] is ctypes.c_void_p
    assert logs[-1]["status"] == "high"
    assert logs[-1]["pid"] > 0


def test_already_high_is_idempotent(windows):
    windows.control, windows.state = 5, 4
    assert cpu.configure_process_high_qos() == "high"
    assert windows.sets == []


@pytest.mark.parametrize(
    "failure,operation", [("read", "read"), ("write", "write"), ("verify", "verify")]
)
def test_rejected_native_calls_are_visible_and_do_not_block_work(windows, failure, operation):
    windows.fail_get = {"read": 1, "verify": 2}.get(failure, 0)
    windows.fail_set = failure == "write"
    with capture_logs() as logs:
        assert cpu.configure_process_high_qos() == "unavailable"
    assert logs[-1]["log_level"] == "warning"
    assert logs[-1]["operation"] == operation
    assert logs[-1]["winerror"] == 5
    assert not any(log.get("status") == "high" for log in logs)
    if failure == "read":
        assert windows.sets == []


def test_successful_write_without_effect_is_not_reported_as_high(windows):
    windows.ignore_set = True
    with capture_logs() as logs:
        assert cpu.configure_process_high_qos() == "unavailable"
    assert logs[-1]["reason"] == "policy_not_applied"
    assert logs[-1]["log_level"] == "warning"


@pytest.mark.parametrize("error", [AttributeError("missing API"), OSError("DLL unavailable")])
def test_unavailable_api_is_reported(windows, monkeypatch, error):
    monkeypatch.setattr(cpu.ctypes, "WinDLL", Mock(side_effect=error))
    with capture_logs() as logs:
        assert cpu.configure_process_high_qos() == "unavailable"
    assert logs[-1]["operation"] == "bind"
    assert logs[-1]["log_level"] == "warning"


def test_programming_errors_are_not_hidden(windows):
    windows.GetProcessInformation.side_effect = RuntimeError("broken binding")
    with pytest.raises(RuntimeError, match="broken binding"):
        cpu.configure_process_high_qos()
