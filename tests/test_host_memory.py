"""Tests for haute._host_memory — host RAM/VRAM observation.

Mirrors the module split: everything here exercises the observation side
(platform probes, cgroup clamping, nvidia-smi parsing); workload estimation
stays in tests/test_ram_estimate.py.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from haute import _host_memory
from haute._host_memory import (
    available_ram_bytes,
    available_vram_bytes,
    require_positive_available_ram,
)

# ---------------------------------------------------------------------------
# require_positive_available_ram — the shared consumer-side validator
# ---------------------------------------------------------------------------


class TestRequirePositiveAvailableRam:
    def test_none_is_unobservable(self) -> None:
        with pytest.raises(RuntimeError, match="physical RAM is unavailable"):
            require_positive_available_ram(None)

    def test_bool_is_unobservable_not_one_byte(self) -> None:
        """True must not pass as an integer budget of one byte."""
        with pytest.raises(RuntimeError, match="physical RAM is unavailable"):
            require_positive_available_ram(True)

    def test_negative_is_a_probe_defect_not_exhaustion(self) -> None:
        """The cgroup clamp floors headroom at zero, so negatives are defects."""
        with pytest.raises(RuntimeError, match="memory probe defect"):
            require_positive_available_ram(-1)

    def test_zero_is_exhaustion_with_retry_remedy(self) -> None:
        with pytest.raises(RuntimeError, match="available memory is exhausted"):
            require_positive_available_ram(0)

    def test_positive_passes_through(self) -> None:
        assert require_positive_available_ram(42) == 42


# ---------------------------------------------------------------------------
# available_ram_bytes
# ---------------------------------------------------------------------------


class TestAvailableRam:
    def test_returns_positive_int(self) -> None:
        ram = available_ram_bytes()
        assert isinstance(ram, int)
        assert ram > 0

    def test_returns_reasonable_value(self) -> None:
        """Should be at least 100 MB on any modern system."""
        ram = available_ram_bytes()
        assert ram > 100 * 1024 * 1024

    def test_fallback_when_proc_unavailable(self) -> None:
        """If /proc/meminfo is not readable, falls back gracefully."""
        with patch("builtins.open", side_effect=OSError):
            ram = available_ram_bytes()
            assert ram > 0

    def test_windows_path_calls_global_memory_status(self, monkeypatch) -> None:
        """On Windows, GlobalMemoryStatusEx is used to read available RAM."""
        mock_windll = MagicMock()
        # GlobalMemoryStatusEx populates the struct; simulate by returning True
        # and patching ullAvailPhys via side_effect
        mock_windll.kernel32.GlobalMemoryStatusEx.return_value = True

        mock_ctypes = MagicMock()
        mock_ctypes.windll = mock_windll
        mock_ctypes.c_ulong = int
        mock_ctypes.c_ulonglong = int

        monkeypatch.setattr("sys.platform", "win32")

        # Block /proc and sysconf so only the Windows path runs
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=AttributeError, create=True):
                with patch.dict(
                    "sys.modules",
                    {"ctypes": mock_ctypes},
                ):
                    # The ctypes struct is defined inline; we can't easily
                    # intercept field values. Verify the API was called.
                    available_ram_bytes()

        mock_windll.kernel32.GlobalMemoryStatusEx.assert_called_once()

    def test_unavailable_probe_does_not_invent_capacity(self, monkeypatch) -> None:
        """When all platform methods fail, capacity is explicitly unavailable."""
        monkeypatch.setattr("sys.platform", "freebsd13")
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=AttributeError, create=True):
                ram = available_ram_bytes()
        assert ram is None

    def test_unavailable_probe_logs_structured_warning(self, monkeypatch) -> None:
        """An unavailable probe is visible in logs with platform context."""
        monkeypatch.setattr("sys.platform", "freebsd13")
        with (
            patch("builtins.open", side_effect=OSError("proc unavailable")),
            patch("os.sysconf", side_effect=AttributeError("no sysconf"), create=True),
            capture_logs() as logs,
        ):
            ram = available_ram_bytes()

        assert ram is None
        unavailable_logs = [
            event for event in logs if event.get("event") == "available_ram_unavailable"
        ]
        assert unavailable_logs
        assert unavailable_logs[0]["platform"] == "freebsd13"
        assert "proc unavailable" in unavailable_logs[0]["proc_meminfo_error"]
        assert "no sysconf" in unavailable_logs[0]["sysconf_error"]
        # A source is attempted exactly when it reports a reason; the darwin
        # and win32 probes do not apply on this platform, so no keys at all.
        assert "macos_attempted" not in unavailable_logs[0]
        assert "windows_attempted" not in unavailable_logs[0]


# ---------------------------------------------------------------------------
# available_ram_bytes — platform-specific paths
# ---------------------------------------------------------------------------


class TestAvailableRamPlatformPaths:
    def test_linux_v2_cgroup_clamps_host_available_memory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        cgroup = {
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._host_memory._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == 750

    def test_linux_keeps_tighter_host_available_memory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        cgroup = {
            "/sys/fs/cgroup/memory.max": "10000",
            "/sys/fs/cgroup/memory.current": "100",
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._host_memory._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == 2 * 1024

    def test_linux_v2_max_does_not_clamp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        cgroup = {
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/memory.current": "250",
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._host_memory._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == 2 * 1024

    @pytest.mark.parametrize(
        ("limit", "current", "expected"),
        [("1000", "250", 750), (str(1 << 60), "250", 2 * 1024), ("100", "250", 0)],
    )
    def test_linux_v1_cgroup_fallback_and_limits(
        self, monkeypatch: pytest.MonkeyPatch, limit: str, current: str, expected: int
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        cgroup = {
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": limit,
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": current,
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._host_memory._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == expected

    @pytest.mark.parametrize(
        "cgroup",
        [
            {"/sys/fs/cgroup/memory.max": "oops", "/sys/fs/cgroup/memory.current": "1"},
            {"/sys/fs/cgroup/memory.max": "1000"},
        ],
    )
    def test_linux_malformed_or_incomplete_cgroup_keeps_host_memory(
        self, monkeypatch: pytest.MonkeyPatch, cgroup: dict[str, str]
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._host_memory._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == 2 * 1024

    @pytest.mark.parametrize(
        "cgroup",
        [
            {"/sys/fs/cgroup/memory.max": "-5", "/sys/fs/cgroup/memory.current": "1"},
            {"/sys/fs/cgroup/memory.max": "1000", "/sys/fs/cgroup/memory.current": "-1"},
        ],
    )
    def test_linux_negative_cgroup_values_keep_host_memory(
        self, monkeypatch: pytest.MonkeyPatch, cgroup: dict[str, str]
    ) -> None:
        """Negative controller values are malformed, not a zero-byte clamp."""
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._host_memory._read_cgroup_memory_file", side_effect=cgroup.get),
            capture_logs() as logs,
        ):
            assert available_ram_bytes() == 2 * 1024
        assert any(entry["event"] == "cgroup_memory_state_malformed" for entry in logs)

    def test_read_cgroup_memory_file_reads_strips_and_tolerates_absence(
        self, haute_scratch: Path
    ) -> None:
        """The unmocked control-file reader strips content and maps absence to None."""
        control = haute_scratch / "memory.max"
        control.write_text(" 1000\n", encoding="utf-8")
        assert _host_memory._read_cgroup_memory_file(str(control)) == "1000"
        assert _host_memory._read_cgroup_memory_file(str(haute_scratch / "absent")) is None

    def test_non_linux_does_not_probe_cgroups(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "darwin")
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._host_memory._read_cgroup_memory_file") as read_cgroup,
        ):
            assert available_ram_bytes() == 2 * 1024
        read_cgroup.assert_not_called()

    def test_unavailable_host_does_not_fabricate_cgroup_capacity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=AttributeError, create=True),
            patch("haute._host_memory._read_cgroup_memory_file") as read_cgroup,
        ):
            assert available_ram_bytes() is None
        read_cgroup.assert_not_called()

    def test_linux_proc_meminfo(self) -> None:
        """Successful /proc/meminfo read returns parsed MemAvailable.

        The real cgroup controller files are masked so a memory-constrained
        Linux CI container cannot clamp the expected value.
        """
        fake_meminfo = (
            "MemTotal:       16384000 kB\n"
            "MemFree:         2000000 kB\n"
            "MemAvailable:    8000000 kB\n"
        )
        from io import StringIO

        with (
            patch("builtins.open", return_value=StringIO(fake_meminfo)),
            patch("haute._host_memory._read_cgroup_memory_file", return_value=None),
        ):
            result = available_ram_bytes()
        assert result == 8_000_000 * 1024

    def test_linux_proc_meminfo_no_memavailable_falls_through(self) -> None:
        """If MemAvailable line is absent, falls through to sysconf."""
        fake_meminfo = "MemTotal:       16384000 kB\nMemFree:  2000000 kB\n"
        from io import StringIO

        with (
            patch("builtins.open", return_value=StringIO(fake_meminfo)),
            patch("haute._host_memory._read_cgroup_memory_file", return_value=None),
        ):
            # sysconf path should be tried next
            with patch("os.sysconf", side_effect=[4096, 4096], create=True):
                result = available_ram_bytes()
        assert result == 4096 * 4096

    def test_macos_sysconf_path(self) -> None:
        """When /proc/meminfo fails, sysconf is used."""
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=[2000, 4096], create=True):
                result = available_ram_bytes()
        assert result == 2000 * 4096

    def test_non_positive_sysconf_values_are_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid POSIX memory values are ignored instead of producing zero RAM."""
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=[0, 4096], create=True),
        ):
            result = available_ram_bytes()

        assert result is None

    def test_windows_success_reports_available_not_total_bytes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Windows probe returns ullAvailPhys, not ullTotalPhys.

        Guards the total-versus-available defect class on the Windows leg the
        way the darwin tests guard it on macOS: the fake populates both fields
        with distinguishable values and the exact available figure must win.
        """
        from types import SimpleNamespace

        def fill_status(mem: object) -> bool:
            mem.ullTotalPhys = 64 * 1024**3
            mem.ullAvailPhys = 3 * 1024**3
            return True

        fake_ctypes = SimpleNamespace(
            Structure=object,
            c_ulong=int,
            c_ulonglong=int,
            sizeof=lambda _value: 1,
            byref=lambda value: value,
            windll=SimpleNamespace(
                kernel32=SimpleNamespace(GlobalMemoryStatusEx=fill_status),
            ),
        )

        monkeypatch.setattr("sys.platform", "win32")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=AttributeError, create=True),
            patch.dict("sys.modules", {"ctypes": fake_ctypes}),
        ):
            assert available_ram_bytes() == 3 * 1024**3

    def test_windows_global_memory_status_false_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed Windows memory probe is explicitly unavailable."""
        from types import SimpleNamespace

        status_probe = MagicMock(return_value=False)
        fake_ctypes = SimpleNamespace(
            Structure=object,
            c_ulong=int,
            c_ulonglong=int,
            sizeof=lambda _value: 1,
            byref=lambda value: value,
            windll=SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=status_probe)),
        )

        monkeypatch.setattr("sys.platform", "win32")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=AttributeError, create=True),
            patch.dict("sys.modules", {"ctypes": fake_ctypes}),
        ):
            result = available_ram_bytes()

        assert result is None
        status_probe.assert_called_once()

    def test_windows_ctypes_exception_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ctypes failures on the Windows path are logged as unavailable."""
        from types import SimpleNamespace

        fake_ctypes = SimpleNamespace(
            Structure=object,
            c_ulong=int,
            c_ulonglong=int,
            sizeof=MagicMock(side_effect=OSError("ctypes unavailable")),
            byref=lambda value: value,
            windll=SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=MagicMock())),
        )

        monkeypatch.setattr("sys.platform", "win32")
        with (
            patch("builtins.open", side_effect=OSError("proc unavailable")),
            patch("os.sysconf", side_effect=AttributeError("no sysconf"), create=True),
            patch.dict("sys.modules", {"ctypes": fake_ctypes}),
            capture_logs() as logs,
        ):
            result = available_ram_bytes()

        assert result is None
        unavailable_log = next(
            event for event in logs if event.get("event") == "available_ram_unavailable"
        )
        assert unavailable_log["windows_attempted"] is True
        assert "ctypes unavailable" in unavailable_log["windows_error"]

    @pytest.mark.skipif(sys.platform != "win32", reason="ctypes.windll only exists on Windows")
    def test_windows_ctypes_failure_is_unavailable(self, monkeypatch) -> None:
        """When GlobalMemoryStatusEx raises OSError, capacity is unavailable."""
        monkeypatch.setattr("sys.platform", "win32")
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=AttributeError, create=True):
                with patch(
                    "ctypes.windll.kernel32.GlobalMemoryStatusEx",
                    side_effect=OSError,
                    create=True,
                ):
                    result = available_ram_bytes()
        assert result is None


# ---------------------------------------------------------------------------
# available_ram_bytes — macOS Mach VM counters
# ---------------------------------------------------------------------------


_MACOS_PAGE_SIZE = 4096
_FAKE_HOST_PORT = 7
_FAKE_TASK_PORT = 3


def _darwin_sysconf(name: str) -> int:
    """Mirror real darwin: ``SC_AVPHYS_PAGES`` is not a valid name there.

    On macOS it is absent from ``os.sysconf_names`` entirely, so the POSIX
    probe raises rather than returning a page count.
    """
    if name == "SC_PAGE_SIZE":
        return _MACOS_PAGE_SIZE
    raise ValueError(f"unrecognized configuration name {name}")


def _fake_libsystem(
    *,
    result: int = 0,
    page_size_result: int = 0,
    page_size: int = _MACOS_PAGE_SIZE,
    **counters: int,
) -> MagicMock:
    """A libSystem stand-in whose Mach calls fill the real ctypes structures.

    The struct is the shipped ``_VMStatistics64``, so the field layout under
    test is the one production reads; only the kernel calls are replaced.
    """

    def host_page_size(_port: int, size_ref: object) -> int:
        ctypes.cast(size_ref, ctypes.POINTER(ctypes.c_size_t)).contents.value = page_size
        return page_size_result

    def host_statistics64(_port: int, flavour: int, info: object, count: object) -> int:
        # Pin the ABI the shipped probe must speak, hard-coded independently of
        # the module constants: HOST_VM_INFO64 (4) with the 38-word REV1 count.
        # A drifted flavour or an off-boundary count would otherwise be
        # invisible on Linux CI, where only this fake ever answers.
        assert flavour == 4
        assert ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32)).contents.value == 38
        stats = ctypes.cast(info, ctypes.POINTER(_host_memory._VMStatistics64)).contents
        for field, value in counters.items():
            setattr(stats, field, value)
        return result

    lib = MagicMock()
    lib.host_page_size.side_effect = host_page_size
    lib.host_statistics64.side_effect = host_statistics64
    lib.mach_host_self.return_value = _FAKE_HOST_PORT
    lib.mach_task_self.return_value = _FAKE_TASK_PORT
    return lib


@contextmanager
def _host_probe_env(
    lib: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    platform: str = "darwin",
) -> Iterator[None]:
    """Simulate *platform* with no /proc, darwin-shaped sysconf, and *lib* as libSystem."""
    monkeypatch.setattr("sys.platform", platform)
    with (
        patch("builtins.open", side_effect=OSError("no /proc on darwin")),
        patch("os.sysconf", side_effect=_darwin_sysconf, create=True),
        patch("ctypes.CDLL", return_value=lib),
    ):
        yield


class TestAvailableRamMacOS:
    """The darwin probe — neither /proc/meminfo nor SC_AVPHYS_PAGES exists."""

    def test_available_is_free_plus_inactive_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Available memory is the free and inactive queues, in bytes."""
        lib = _fake_libsystem(free_count=1_000, inactive_count=2_000)
        with _host_probe_env(lib, monkeypatch):
            assert available_ram_bytes() == 3_000 * _MACOS_PAGE_SIZE

    def test_page_size_comes_from_mach_not_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counts are in host VM pages, so the multiplier is Mach's, not sysconf's.

        The two diverge for translated processes (Rosetta reports a 4 KiB
        POSIX page while the host counts 16 KiB pages); scaling host page
        counts by ``SC_PAGE_SIZE`` would mis-state availability 4×.
        """
        lib = _fake_libsystem(free_count=1_000, inactive_count=1_000, page_size=16_384)
        with _host_probe_env(lib, monkeypatch):
            assert available_ram_bytes() == 2_000 * 16_384

    def test_resident_and_purgeable_pages_are_excluded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wired/active pages are in use, and purgeable overlays the queues.

        ``purgeable_count`` is an attribute of pages that stay on the
        active/inactive queues, not a disjoint pool, so counting it would
        double-count the purgeable pages already inside ``inactive_count`` and
        over-admit work on a loaded machine.  Speculative pages are already
        inside ``free_count`` and must not be added a second time either.
        """
        lib = _fake_libsystem(
            free_count=1_000,
            inactive_count=2_000,
            active_count=500_000,
            wire_count=200_000,
            purgeable_count=50_000,
            speculative_count=800,
            compressor_page_count=300_000,
        )
        with _host_probe_env(lib, monkeypatch):
            assert available_ram_bytes() == 3_000 * _MACOS_PAGE_SIZE

    def test_total_installed_ram_is_never_substituted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A nearly-full machine reports its small remainder, not its capacity."""
        lib = _fake_libsystem(free_count=10, inactive_count=10, wire_count=1_500_000)
        with _host_probe_env(lib, monkeypatch):
            assert available_ram_bytes() == 20 * _MACOS_PAGE_SIZE

    def test_mach_port_right_is_released(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``mach_host_self`` hands back a send right on every call."""
        lib = _fake_libsystem(free_count=1_000, inactive_count=2_000)
        with _host_probe_env(lib, monkeypatch):
            available_ram_bytes()
        lib.mach_port_deallocate.assert_called_once_with(_FAKE_TASK_PORT, _FAKE_HOST_PORT)

    def test_kern_failure_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A refused Mach call records a reason instead of inventing capacity."""
        lib = _fake_libsystem(free_count=1_000, inactive_count=2_000, result=5)
        with _host_probe_env(lib, monkeypatch), capture_logs() as logs:
            result = available_ram_bytes()

        assert result is None
        unavailable_log = next(
            event for event in logs if event.get("event") == "available_ram_unavailable"
        )
        assert unavailable_log["macos_attempted"] is True
        assert "host_statistics64 returned 5" in unavailable_log["macos_error"]
        assert "unrecognized configuration name" in unavailable_log["sysconf_error"]

    def test_page_size_failure_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A refused ``host_page_size`` aborts the probe before the counters."""
        lib = _fake_libsystem(free_count=1_000, inactive_count=2_000, page_size_result=5)
        with _host_probe_env(lib, monkeypatch), capture_logs() as logs:
            result = available_ram_bytes()

        assert result is None
        lib.host_statistics64.assert_not_called()
        lib.mach_port_deallocate.assert_called_once_with(_FAKE_TASK_PORT, _FAKE_HOST_PORT)
        unavailable_log = next(
            event for event in logs if event.get("event") == "available_ram_unavailable"
        )
        assert "host_page_size returned 5" in unavailable_log["macos_error"]

    def test_missing_symbol_is_unavailable_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A libSystem without the Mach symbols fails softly, not loudly."""
        lib = _fake_libsystem(free_count=1_000, inactive_count=2_000)
        del lib.host_page_size  # MagicMock raises AttributeError for deleted attrs

        with _host_probe_env(lib, monkeypatch), capture_logs() as logs:
            result = available_ram_bytes()

        assert result is None
        unavailable_log = next(
            event for event in logs if event.get("event") == "available_ram_unavailable"
        )
        assert unavailable_log["macos_attempted"] is True
        assert unavailable_log["macos_error"]

    def test_probe_failure_still_releases_the_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The send right is released even when the Mach call raises."""
        lib = _fake_libsystem(free_count=1_000, inactive_count=2_000)
        lib.host_statistics64.side_effect = OSError("mach call failed")
        with _host_probe_env(lib, monkeypatch):
            assert available_ram_bytes() is None
        lib.mach_port_deallocate.assert_called_once_with(_FAKE_TASK_PORT, _FAKE_HOST_PORT)

    def test_non_positive_page_counts_are_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero reclaimable pages is a broken probe, not a zero-byte budget."""
        lib = _fake_libsystem(free_count=0, inactive_count=0)
        with _host_probe_env(lib, monkeypatch):
            assert available_ram_bytes() is None
        # Exactly one kernel query — a retry loop must not creep in here.
        lib.host_statistics64.assert_called_once()

    def test_deallocate_failure_does_not_discard_the_reading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing port release is swallowed; the observation still wins."""
        lib = _fake_libsystem(free_count=1_000, inactive_count=2_000)
        lib.mach_port_deallocate.side_effect = OSError("deallocate failed")
        with _host_probe_env(lib, monkeypatch):
            assert available_ram_bytes() == 3_000 * _MACOS_PAGE_SIZE

    def test_non_darwin_platforms_do_not_probe_mach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The Mach probe is darwin-only; other POSIX platforms skip it."""
        lib = _fake_libsystem(free_count=1_000, inactive_count=2_000)
        with _host_probe_env(lib, monkeypatch, platform="freebsd13"):
            assert available_ram_bytes() is None
        lib.mach_host_self.assert_not_called()

    @pytest.mark.skipif(sys.platform != "darwin", reason="Mach VM counters are darwin-only")
    def test_real_darwin_kernel_reports_available_memory(self) -> None:
        """The unmocked kernel path works — admission is unusable without it.

        Guards the original defect: darwin fell through /proc/meminfo and a
        ``SC_AVPHYS_PAGES`` name that does not exist on the platform, so every
        request through execution admission failed.
        """
        import os

        assert "SC_AVPHYS_PAGES" not in os.sysconf_names

        ram = available_ram_bytes()
        assert isinstance(ram, int)
        assert ram > 100 * 1024 * 1024
        # Strictly below total installed RAM — the one assertion that
        # distinguishes *available* from *total* against the real kernel, so a
        # slide back to hw.memsize-style capacity cannot pass.
        assert ram < os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")


# ---------------------------------------------------------------------------
# available_vram_bytes — nvidia-smi parsing
# ---------------------------------------------------------------------------


class TestAvailableVram:
    def test_returns_int_or_none(self) -> None:
        result = available_vram_bytes()
        assert result is None or (isinstance(result, int) and result > 0)

    def test_returns_none_when_nvidia_smi_missing(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert available_vram_bytes() is None


class TestAvailableVramParsing:
    def test_successful_nvidia_smi_single_gpu(self) -> None:
        """Parse nvidia-smi output for a single GPU."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "8192\n"
        with patch("subprocess.run", return_value=mock_result):
            result = available_vram_bytes()
        assert result == 8192 * 1024 * 1024

    def test_successful_nvidia_smi_multiple_gpus(self) -> None:
        """With multiple GPUs, the first line is used."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "16384\n8192\n"
        with patch("subprocess.run", return_value=mock_result):
            result = available_vram_bytes()
        # First GPU's VRAM
        assert result == 16384 * 1024 * 1024

    def test_nvidia_smi_nonzero_returncode(self) -> None:
        """Non-zero returncode means no GPU detected."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = available_vram_bytes()
        assert result is None

    def test_nvidia_smi_timeout(self) -> None:
        """TimeoutExpired returns None."""
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            result = available_vram_bytes()
        assert result is None

    def test_nvidia_smi_oserror(self) -> None:
        """OSError returns None."""
        with patch("subprocess.run", side_effect=OSError):
            result = available_vram_bytes()
        assert result is None
