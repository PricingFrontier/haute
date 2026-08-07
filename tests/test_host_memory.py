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
# Linux cgroup — nested-cgroup path resolution
# ---------------------------------------------------------------------------


_V2_ROOT_MOUNTINFO = "35 24 0:30 / /sys/fs/cgroup rw,nosuid - cgroup2 cgroup2 rw\n"
_V1_MEMORY_ROOT_MOUNTINFO = (
    "36 24 0:31 / /sys/fs/cgroup/memory rw,nosuid - cgroup cgroup rw,memory\n"
)


def _probe(files: dict[str, str]) -> int | None:
    """Run the cgroup headroom probe with all file reads answered from *files*."""
    with patch("haute._host_memory._read_cgroup_memory_file", side_effect=files.get):
        return _host_memory._cgroup_memory_headroom_bytes()


class TestCgroupNestedResolution:
    """The probe reads the process's own cgroup, not just the mount root.

    A process in a systemd service slice or a shared-cgroup-namespace
    container has its binding limits below ``/sys/fs/cgroup``; the probe must
    resolve its directory via /proc/self/cgroup + /proc/self/mountinfo and
    apply ancestor-min semantics, while every resolution failure degrades to
    the historical mount-root read (fail-open).
    """

    def test_mount_root_process_reads_mount_root(self) -> None:
        files = {
            "/proc/self/cgroup": "0::/\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        assert _probe(files) == 750

    def test_nested_one_level_v2(self) -> None:
        """Limits one level below the mount root bind the process."""
        files = {
            "/proc/self/cgroup": "0::/haute\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/haute/memory.max": "1000",
            "/sys/fs/cgroup/haute/memory.current": "600",
        }
        assert _probe(files) == 400

    def test_nested_systemd_slice_ancestor_min(self) -> None:
        """A tighter slice-level limit wins over a looser service-level one."""
        files = {
            "/proc/self/cgroup": "0::/system.slice/haute.service\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/system.slice/haute.service/memory.max": "10000",
            "/sys/fs/cgroup/system.slice/haute.service/memory.current": "1000",
            "/sys/fs/cgroup/system.slice/memory.max": "4000",
            "/sys/fs/cgroup/system.slice/memory.current": "3500",
        }
        assert _probe(files) == 500

    def test_leaf_tighter_than_ancestor(self) -> None:
        files = {
            "/proc/self/cgroup": "0::/system.slice/haute.service\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/system.slice/haute.service/memory.max": "300",
            "/sys/fs/cgroup/system.slice/haute.service/memory.current": "100",
            "/sys/fs/cgroup/system.slice/memory.max": "100000",
            "/sys/fs/cgroup/system.slice/memory.current": "2000",
        }
        assert _probe(files) == 200

    def test_unlimited_leaf_finite_ancestor(self) -> None:
        """``max`` at the leaf does not hide a finite ancestor limit."""
        files = {
            "/proc/self/cgroup": "0::/system.slice/haute.service\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/system.slice/haute.service/memory.max": "max",
            "/sys/fs/cgroup/system.slice/haute.service/memory.current": "100",
            "/sys/fs/cgroup/system.slice/memory.max": "5000",
            "/sys/fs/cgroup/system.slice/memory.current": "4000",
        }
        assert _probe(files) == 1000

    def test_controller_enabled_only_at_ancestor(self) -> None:
        """Absent leaf files (controller not enabled there) walk up to a limit."""
        files = {
            "/proc/self/cgroup": "0::/system.slice/haute.service\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/system.slice/memory.max": "900",
            "/sys/fs/cgroup/system.slice/memory.current": "150",
        }
        assert _probe(files) == 750

    def test_v1_nested_docker_shared_namespace(self) -> None:
        """A v1 memory hierarchy resolves the /docker/<id> path below its mount."""
        files = {
            "/proc/self/cgroup": "4:memory:/docker/abc123\n1:name=systemd:/\n",
            "/proc/self/mountinfo": _V1_MEMORY_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/memory/docker/abc123/memory.limit_in_bytes": "1000",
            "/sys/fs/cgroup/memory/docker/abc123/memory.usage_in_bytes": "400",
        }
        assert _probe(files) == 600

    def test_v1_ancestor_min_and_unlimited_sentinel(self) -> None:
        """v1 walks ancestors too, ignoring near-sentinel unlimited levels."""
        files = {
            "/proc/self/cgroup": "4:memory:/docker/abc123\n",
            "/proc/self/mountinfo": _V1_MEMORY_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/memory/docker/abc123/memory.limit_in_bytes": str(1 << 60),
            "/sys/fs/cgroup/memory/docker/abc123/memory.usage_in_bytes": "400",
            "/sys/fs/cgroup/memory/docker/memory.limit_in_bytes": "700",
            "/sys/fs/cgroup/memory/docker/memory.usage_in_bytes": "300",
        }
        assert _probe(files) == 400

    def test_v1_mount_root_is_container_subtree(self) -> None:
        """When the mount's root IS the container's cgroup, read the mount point."""
        files = {
            "/proc/self/cgroup": "4:memory:/docker/abc123\n",
            "/proc/self/mountinfo": (
                "36 24 0:31 /docker/abc123 /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n"
            ),
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": "1000",
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": "250",
        }
        assert _probe(files) == 750

    def test_hybrid_v2_without_memory_falls_to_v1(self) -> None:
        """On a hybrid host the v2 walk finds no memory files and v1 answers."""
        files = {
            "/proc/self/cgroup": "0::/user.slice\n4:memory:/docker/abc123\n",
            "/proc/self/mountinfo": (
                "35 24 0:30 / /sys/fs/cgroup/unified rw - cgroup2 cgroup2 rw\n"
                + _V1_MEMORY_ROOT_MOUNTINFO
            ),
            "/sys/fs/cgroup/memory/docker/abc123/memory.limit_in_bytes": "1000",
            "/sys/fs/cgroup/memory/docker/abc123/memory.usage_in_bytes": "800",
        }
        assert _probe(files) == 200

    def test_malformed_proc_self_cgroup_falls_back_to_mount_root(self) -> None:
        """Unparseable /proc/self/cgroup lines degrade to the mount-root read."""
        files = {
            "/proc/self/cgroup": "not a cgroup line\n0:no-path-field\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        assert _probe(files) == 750

    def test_malformed_mountinfo_falls_back_to_mount_root(self) -> None:
        """mountinfo lines without the options separator degrade gracefully."""
        files = {
            "/proc/self/cgroup": "0::/haute\n",
            "/proc/self/mountinfo": "garbage line\n1 2 0:30 / /sys/fs/cgroup rw cgroup2\n",
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        assert _probe(files) == 750

    def test_missing_proc_files_fall_back_to_mount_root(self) -> None:
        files = {
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        assert _probe(files) == 750

    def test_path_outside_mount_root_warns_and_falls_back(self) -> None:
        """A cgroup path this mount cannot expose is logged, then fail-open."""
        files = {
            "/proc/self/cgroup": "0::/machine.slice/vm\n",
            "/proc/self/mountinfo": (
                "35 24 0:30 /user.slice /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n"
            ),
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        with capture_logs() as logs:
            assert _probe(files) == 750
        assert any(entry["event"] == "cgroup_self_path_unresolved" for entry in logs)

    def test_unresolvable_path_still_reads_the_parsed_mount_point(self) -> None:
        """The fallback honours a non-default mount, not the compiled-in path.

        On a host with cgroup2 mounted away from ``/sys/fs/cgroup``, an
        unresolvable cgroup path must degrade to the parsed mount point —
        falling back to the default location would silently observe nothing.
        """
        files = {
            "/proc/self/cgroup": "0::/machine.slice/vm\n",
            "/proc/self/mountinfo": (
                "35 24 0:30 /user.slice /sys/fs/cgroup/unified rw - cgroup2 cgroup2 rw\n"
            ),
            "/sys/fs/cgroup/unified/memory.max": "1000",
            "/sys/fs/cgroup/unified/memory.current": "400",
        }
        with capture_logs() as logs:
            assert _probe(files) == 600
        assert any(entry["event"] == "cgroup_self_path_unresolved" for entry in logs)

    def test_mount_without_cgroup_line_reads_the_parsed_mount_point(self) -> None:
        """A parsed mount beats the default even with no usable cgroup path."""
        files = {
            "/proc/self/cgroup": "not parseable\n",
            "/proc/self/mountinfo": (
                "35 24 0:30 / /sys/fs/cgroup/unified rw - cgroup2 cgroup2 rw\n"
            ),
            "/sys/fs/cgroup/unified/memory.max": "1000",
            "/sys/fs/cgroup/unified/memory.current": "250",
        }
        assert _probe(files) == 750

    def test_unreadable_proc_files_warn_and_use_default_mounts(self) -> None:
        files = {
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        with capture_logs() as logs:
            assert _probe(files) == 750
        unreadable = [e for e in logs if e["event"] == "cgroup_self_state_unreadable"]
        assert unreadable
        assert unreadable[0]["cgroup_readable"] is False
        assert unreadable[0]["mountinfo_readable"] is False

    def test_malformed_v2_dominates_a_healthy_v1(self) -> None:
        """A present-but-broken v2 controller fails open without consulting v1.

        Deliberate: a v2 hierarchy that answers at all is the authoritative
        one, and falling through to v1 on a malformed read could substitute a
        stale or unrelated limit for the broken authoritative state.
        """
        files = {
            "/proc/self/cgroup": "0::/haute\n4:memory:/haute\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO + _V1_MEMORY_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/haute/memory.max": "oops",
            "/sys/fs/cgroup/haute/memory.current": "1",
            "/sys/fs/cgroup/memory/haute/memory.limit_in_bytes": "1000",
            "/sys/fs/cgroup/memory/haute/memory.usage_in_bytes": "250",
        }
        assert _probe(files) is None

    def test_depth_truncated_walk_fails_the_probe_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A too-deep cgroup fails open rather than reporting a partial walk.

        The levels past the cutoff are the ones nearest the mount point — the
        broadest limits — so a truncated walk that returned its finite leaf
        headroom could over-admit against a tighter unseen ancestor.
        """
        monkeypatch.setattr(_host_memory, "_CGROUP_WALK_DEPTH_LIMIT", 2)
        files = {
            "/proc/self/cgroup": "0::/a/b/c\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/a/b/c/memory.max": "1000",
            "/sys/fs/cgroup/a/b/c/memory.current": "250",
        }
        with capture_logs() as logs:
            assert _probe(files) is None
        assert any(entry["event"] == "cgroup_ancestor_walk_truncated" for entry in logs)

    def test_most_specific_mount_root_wins_across_multiple_mounts(self) -> None:
        """Among several cgroup2 mounts, the deepest containing root is used.

        The binding limit may only be reachable through a subtree mount; a
        first-match rule would resolve through the broad mount and read a
        different (or absent) set of controller files.
        """
        files = {
            "/proc/self/cgroup": "0::/docker/abc/task\n",
            "/proc/self/mountinfo": (
                "35 24 0:30 / /broad rw - cgroup2 cgroup2 rw\n"
                "36 24 0:30 /docker/abc /narrow rw - cgroup2 cgroup2 rw\n"
            ),
            # Reachable through the broad mount too, but with no limits there;
            # the binding limit lives under the subtree mount.
            "/narrow/task/memory.max": "1000",
            "/narrow/task/memory.current": "600",
            "/narrow/memory.max": "2000",
            "/narrow/memory.current": "1900",
        }
        assert _probe(files) == 100
        # Mount order must not matter: the subtree root wins listed either way.
        files["/proc/self/mountinfo"] = (
            "36 24 0:30 /docker/abc /narrow rw - cgroup2 cgroup2 rw\n"
            "35 24 0:30 / /broad rw - cgroup2 cgroup2 rw\n"
        )
        assert _probe(files) == 100

    def test_reader_returns_none_for_nul_bearing_path(self) -> None:
        """An embedded NUL raises ValueError at open; the reader absorbs it."""
        assert _host_memory._read_cgroup_memory_file("/sys/fs/\x00bad") is None

    def test_non_utf8_proc_content_degrades_instead_of_raising(self, haute_scratch: Path) -> None:
        """Raw kernel dentry bytes must never crash the reader.

        /proc/self/cgroup is not octal-escaped like mountinfo: a sibling
        cgroup named with non-UTF-8 bytes appears verbatim, and the reader
        must substitute rather than raise ``UnicodeDecodeError``.
        """
        proc_file = haute_scratch / "cgroup"
        proc_file.write_bytes(b"0::/bad\xffname\n")
        content = _host_memory._read_cgroup_memory_file(str(proc_file))
        assert content == "0::/bad�name"

    def test_dot_dot_cgroup_path_is_rejected(self) -> None:
        files = {
            "/proc/self/cgroup": "0::/../escape\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        with capture_logs() as logs:
            assert _probe(files) == 750
        assert any(entry["event"] == "cgroup_self_path_unresolved" for entry in logs)

    def test_malformed_nested_level_fails_open(self) -> None:
        """A malformed ancestor value fails the whole probe open, not partial."""
        files = {
            "/proc/self/cgroup": "0::/system.slice/haute.service\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/system.slice/haute.service/memory.max": "1000",
            "/sys/fs/cgroup/system.slice/haute.service/memory.current": "250",
            "/sys/fs/cgroup/system.slice/memory.max": "oops",
            "/sys/fs/cgroup/system.slice/memory.current": "1",
        }
        with capture_logs() as logs:
            assert _probe(files) is None
        assert any(entry["event"] == "cgroup_memory_state_malformed" for entry in logs)

    def test_incomplete_nested_level_fails_open(self) -> None:
        files = {
            "/proc/self/cgroup": "0::/haute\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/haute/memory.max": "1000",
        }
        with capture_logs() as logs:
            assert _probe(files) is None
        assert any(entry["event"] == "cgroup_memory_state_incomplete" for entry in logs)

    def test_deleted_cgroup_line_is_ignored(self) -> None:
        files = {
            "/proc/self/cgroup": "0::/gone (deleted)\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        assert _probe(files) == 750

    def test_escaped_mount_point_is_decoded(self) -> None:
        """Octal escapes in mountinfo paths (e.g. \\040 for space) are decoded."""
        files = {
            "/proc/self/cgroup": "0::/haute\n",
            "/proc/self/mountinfo": (
                "35 24 0:30 / /sys/fs/my\\040cgroup rw - cgroup2 cgroup2 rw\n"
            ),
            "/sys/fs/my cgroup/haute/memory.max": "1000",
            "/sys/fs/my cgroup/haute/memory.current": "100",
        }
        assert _probe(files) == 900

    def test_nested_clamp_applies_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """available_ram_bytes clamps to the nested cgroup, not the host figure."""
        monkeypatch.setattr("sys.platform", "linux")
        files = {
            "/proc/self/cgroup": "0::/system.slice/haute.service\n",
            "/proc/self/mountinfo": _V2_ROOT_MOUNTINFO,
            "/sys/fs/cgroup/system.slice/haute.service/memory.max": "700",
            "/sys/fs/cgroup/system.slice/haute.service/memory.current": "200",
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._host_memory._read_cgroup_memory_file", side_effect=files.get),
        ):
            assert available_ram_bytes() == 500


class TestCgroupParsers:
    def test_unescape_mountinfo_field(self) -> None:
        unescape = _host_memory._unescape_mountinfo_field
        assert unescape("/plain/path") == "/plain/path"
        assert unescape("/with\\040space") == "/with space"
        assert unescape("/tab\\011here") == "/tab\there"
        # A trailing or non-octal backslash sequence passes through untouched.
        assert unescape("/odd\\") == "/odd\\"
        assert unescape("/not\\09octal") == "/not\\09octal"
        # Only the kernel's escape set decodes: \057 (/) and \056 (.) must
        # NOT — a permissive decoder would let a hostile name synthesise
        # traversal components after validation.
        assert unescape("/a\\057\\056\\056\\057etc") == "/a\\057\\056\\056\\057etc"
        assert unescape("/nul\\000byte") == "/nul\\000byte"

    def test_parse_proc_self_cgroup_prefers_first_match(self) -> None:
        v2, v1 = _host_memory._parse_proc_self_cgroup(
            "0::/first\n0::/second\n5:cpu,memory:/one\n4:memory:/two\n"
        )
        assert v2 == "/first"
        assert v1 == "/one"

    def test_parse_proc_self_mountinfo_matches_fstype_and_super_options(self) -> None:
        text = (
            "30 24 0:26 / /sys/fs/cgroup/cpu rw - cgroup cgroup rw,cpu\n"
            "31 24 0:27 / /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n"
            "35 24 0:30 / /sys/fs/cgroup/unified rw shared:1 - cgroup2 cgroup2 rw\n"
        )
        v2_mounts, v1_mounts = _host_memory._parse_proc_self_mountinfo(text)
        assert v2_mounts == [("/", "/sys/fs/cgroup/unified")]
        assert v1_mounts == [("/", "/sys/fs/cgroup/memory")]

    def test_parse_proc_self_mountinfo_skips_unusable_lines(self) -> None:
        text = (
            # Separator too early: no room for the mandatory leading fields.
            "1 2 - cgroup2 cgroup2 rw\n"
            # Five fields before the separator is still short of the six
            # mandatory ones — a missing field shifts the path positions.
            "30 24 0:26 / /sys/fs/cgroup - cgroup2 cgroup2 rw\n"
            # Relative root and mount point are not usable paths.
            "30 24 0:26 rel /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n"
            # Traversal components in a decoded path field must not redirect
            # controller reads outside the hierarchy.
            "30 24 0:26 / /sys/fs/../etc rw - cgroup2 cgroup2 rw\n"
            # A v1 cgroup line with no super-options field cannot prove it
            # carries the memory controller.
            "31 24 0:27 / /sys/fs/cgroup/memory rw - cgroup cgroup\n"
        )
        assert _host_memory._parse_proc_self_mountinfo(text) == ([], [])

    def test_parse_proc_self_mountinfo_six_field_boundary(self) -> None:
        """Exactly the six mandatory fields before the separator is accepted."""
        text = "35 24 0:30 / /sys/fs/cgroup rw - cgroup2 cgroup2 rw\n"
        assert _host_memory._parse_proc_self_mountinfo(text) == (
            [("/", "/sys/fs/cgroup")],
            [],
        )

    def test_v1_path_outside_mount_root_warns_and_falls_back(self) -> None:
        files = {
            "/proc/self/cgroup": "4:memory:/other\n",
            "/proc/self/mountinfo": (
                "36 24 0:31 /docker/abc /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n"
            ),
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": "1000",
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": "250",
        }
        with capture_logs() as logs:
            assert _probe(files) == 750
        assert any(
            entry["event"] == "cgroup_self_path_unresolved" and entry["version"] == "v1"
            for entry in logs
        )

    def test_ancestor_chain_depth_limit_bounds_the_walk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_host_memory, "_CGROUP_WALK_DEPTH_LIMIT", 2)
        with capture_logs() as logs:
            chain = _host_memory._cgroup_ancestor_chain(
                _host_memory._CgroupLocation("/a/b/c/d", "/a")
            )
        # A truncated walk is no walk at all: the dropped levels nearest the
        # mount point hold the broadest limits, so a partial chain must not
        # pass for a complete observation.
        assert chain is None
        assert any(entry["event"] == "cgroup_ancestor_walk_truncated" for entry in logs)
        within_limit = _host_memory._cgroup_ancestor_chain(
            _host_memory._CgroupLocation("/a/b", "/a")
        )
        assert within_limit == ["/a/b", "/a"]

    def test_resolve_cgroup_directory_shapes(self) -> None:
        resolve = _host_memory._resolve_cgroup_directory
        assert resolve("/", "/", "/sys/fs/cgroup") == "/sys/fs/cgroup"
        assert resolve("/a/b", "/", "/sys/fs/cgroup") == "/sys/fs/cgroup/a/b"
        assert resolve("/a/b", "/a", "/sys/fs/cgroup") == "/sys/fs/cgroup/b"
        assert resolve("/a/b", "/a/b", "/sys/fs/cgroup") == "/sys/fs/cgroup"
        assert resolve("/other", "/a", "/sys/fs/cgroup") is None
        assert resolve("/a/../b", "/", "/sys/fs/cgroup") is None


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
