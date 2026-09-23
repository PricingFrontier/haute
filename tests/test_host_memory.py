"""Tests for haute._host_memory — host RAM/VRAM observation.

Mirrors the module split: everything here exercises the observation side
(platform probes, cgroup clamping, nvidia-smi parsing); workload estimation
stays in tests/test_ram_estimate.py.
"""

from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from haute import _host_memory
from haute._host_memory import (
    available_ram_bytes,
    available_vram_bytes,
    nvidia_gpu_name,
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

    def test_patched_psutil_reading_with_no_cgroup_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_host_memory, "_cgroup_memory_headroom_bytes", lambda: None)
        fake_virtual_memory = SimpleNamespace(available=123)
        with patch.object(_host_memory.psutil, "virtual_memory", return_value=fake_virtual_memory):
            assert available_ram_bytes() == 123

    def test_cgroup_clamp_still_applies_to_the_psutil_reading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(_host_memory, "_cgroup_memory_headroom_bytes", lambda: 100)
        fake_virtual_memory = SimpleNamespace(available=123)
        with patch.object(_host_memory.psutil, "virtual_memory", return_value=fake_virtual_memory):
            assert available_ram_bytes() == 100

    def test_psutil_error_is_unavailable_and_logs(self) -> None:
        with (
            patch.object(_host_memory.psutil, "virtual_memory", side_effect=OSError("no psutil")),
            capture_logs() as logs,
        ):
            ram = available_ram_bytes()
        assert ram is None
        assert any(entry["event"] == "available_ram_unavailable" for entry in logs)

    def test_negative_reading_is_unavailable_and_logs(self) -> None:
        fake_virtual_memory = SimpleNamespace(available=-1)
        with (
            patch.object(_host_memory.psutil, "virtual_memory", return_value=fake_virtual_memory),
            capture_logs() as logs,
        ):
            ram = available_ram_bytes()
        assert ram is None
        assert any(entry["event"] == "available_ram_unavailable" for entry in logs)


# ---------------------------------------------------------------------------
# available_ram_bytes — cgroup clamping
# ---------------------------------------------------------------------------


_FAKE_HOST_PORT = 0x1003
_FAKE_TASK_PORT = 0x203


def _fake_libsystem(*, host_page_size: int, result: int = 0) -> MagicMock:
    """A libSystem stand-in whose ``host_page_size`` fills the real ctypes out-param."""

    def fill_page_size(_port: int, size_ref: object) -> int:
        ctypes.cast(size_ref, ctypes.POINTER(ctypes.c_size_t)).contents.value = host_page_size
        return result

    lib = MagicMock()
    lib.host_page_size.side_effect = fill_page_size
    lib.mach_host_self.return_value = _FAKE_HOST_PORT
    lib.mach_task_self.return_value = _FAKE_TASK_PORT
    return lib


class TestDarwinPageScale:
    """psutil scales Mach page counts by the POSIX page size; the counts are host pages."""

    @pytest.fixture
    def darwin(self, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
        monkeypatch.setattr(_host_memory.sys, "platform", "darwin")
        monkeypatch.setitem(
            __import__("sys").modules, "resource", SimpleNamespace(getpagesize=lambda: 4096)
        )
        monkeypatch.setattr(_host_memory, "_cgroup_memory_headroom_bytes", lambda: None)
        return monkeypatch

    def _available(self, lib: MagicMock, darwin: pytest.MonkeyPatch) -> int | None:
        darwin.setattr(_host_memory.ctypes, "CDLL", lambda _name: lib)
        with patch.object(
            _host_memory.psutil, "virtual_memory", return_value=SimpleNamespace(available=1000)
        ):
            return available_ram_bytes()

    def test_a_translated_process_counts_16k_host_pages_not_4k_posix_pages(
        self, darwin: pytest.MonkeyPatch
    ) -> None:
        lib = _fake_libsystem(host_page_size=16384)

        assert self._available(lib, darwin) == 4000
        lib.mach_port_deallocate.assert_called_once_with(_FAKE_TASK_PORT, _FAKE_HOST_PORT)

    @pytest.mark.parametrize("host_page_size", [4096, 6144])
    def test_matching_or_uneven_page_sizes_leave_the_reading_unscaled(
        self, darwin: pytest.MonkeyPatch, host_page_size: int
    ) -> None:
        lib = _fake_libsystem(host_page_size=host_page_size)
        assert self._available(lib, darwin) == 1000

    @pytest.mark.parametrize(
        ("host_page_size", "result"),
        [(16384, 5), (0, 0)],
    )
    def test_an_unreadable_host_page_size_logs_and_leaves_the_reading_unscaled(
        self, darwin: pytest.MonkeyPatch, host_page_size: int, result: int
    ) -> None:
        lib = _fake_libsystem(host_page_size=host_page_size, result=result)
        with capture_logs() as logs:
            assert self._available(lib, darwin) == 1000
        assert any(log["event"] == "darwin_host_page_size_unavailable" for log in logs)

    def test_a_missing_libsystem_logs_and_leaves_the_reading_unscaled(
        self, darwin: pytest.MonkeyPatch
    ) -> None:
        def refuse(_name: object) -> None:
            raise OSError("no libSystem")

        darwin.setattr(_host_memory.ctypes, "CDLL", refuse)
        with (
            patch.object(
                _host_memory.psutil, "virtual_memory", return_value=SimpleNamespace(available=1000)
            ),
            capture_logs() as logs,
        ):
            assert available_ram_bytes() == 1000
        assert any(log["event"] == "darwin_host_page_size_unavailable" for log in logs)

    def test_a_failed_port_release_keeps_the_reading(self, darwin: pytest.MonkeyPatch) -> None:
        lib = _fake_libsystem(host_page_size=16384)
        lib.mach_port_deallocate.side_effect = OSError("release failed")

        assert self._available(lib, darwin) == 4000

    def test_other_platforms_do_not_ask_mach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_host_memory.sys, "platform", "linux")
        cdll = MagicMock()
        monkeypatch.setattr(_host_memory.ctypes, "CDLL", cdll)

        assert _host_memory._darwin_page_scale() == 1
        cdll.assert_not_called()


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
            patch.object(
                _host_memory.psutil,
                "virtual_memory",
                return_value=SimpleNamespace(available=2 * 1024),
            ),
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
            patch.object(
                _host_memory.psutil,
                "virtual_memory",
                return_value=SimpleNamespace(available=2 * 1024),
            ),
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
            patch.object(
                _host_memory.psutil,
                "virtual_memory",
                return_value=SimpleNamespace(available=2 * 1024),
            ),
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
            patch.object(
                _host_memory.psutil,
                "virtual_memory",
                return_value=SimpleNamespace(available=2 * 1024),
            ),
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
            patch.object(
                _host_memory.psutil,
                "virtual_memory",
                return_value=SimpleNamespace(available=2 * 1024),
            ),
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
            patch.object(
                _host_memory.psutil,
                "virtual_memory",
                return_value=SimpleNamespace(available=2 * 1024),
            ),
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
            patch.object(
                _host_memory.psutil,
                "virtual_memory",
                return_value=SimpleNamespace(available=2 * 1024),
            ),
            patch("haute._host_memory._read_cgroup_memory_file") as read_cgroup,
        ):
            assert available_ram_bytes() == 2 * 1024
        read_cgroup.assert_not_called()

    def test_unavailable_host_does_not_fabricate_cgroup_capacity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch.object(_host_memory.psutil, "virtual_memory", side_effect=OSError("no psutil")),
            patch("haute._host_memory._read_cgroup_memory_file") as read_cgroup,
        ):
            assert available_ram_bytes() is None
        read_cgroup.assert_not_called()


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
            patch.object(
                _host_memory.psutil,
                "virtual_memory",
                return_value=SimpleNamespace(available=2 * 1024),
            ),
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
# available_vram_bytes — nvidia-smi parsing
# ---------------------------------------------------------------------------


class TestAvailableVram:
    def test_returns_int_or_none(self) -> None:
        result = available_vram_bytes()
        assert result is None or (isinstance(result, int) and result > 0)

    def test_returns_none_when_nvidia_smi_missing(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert available_vram_bytes() is None


class TestNvidiaGpuName:
    def test_reports_the_first_listed_gpu(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NVIDIA GeForce RTX 4070\nNVIDIA A100\n"
        with patch("subprocess.run", return_value=mock_result) as run:
            assert nvidia_gpu_name() == "NVIDIA GeForce RTX 4070"
        assert run.call_args.kwargs["encoding"] == "utf-8"

    @pytest.mark.parametrize(("returncode", "stdout"), [(1, "NVIDIA GeForce RTX 4070\n"), (0, "")])
    def test_none_when_nvidia_smi_fails_or_lists_nothing(
        self, returncode: int, stdout: str
    ) -> None:
        mock_result = MagicMock()
        mock_result.returncode = returncode
        mock_result.stdout = stdout
        with patch("subprocess.run", return_value=mock_result):
            assert nvidia_gpu_name() is None

    @pytest.mark.parametrize(
        "error", [FileNotFoundError(), OSError(), subprocess.TimeoutExpired("nvidia-smi", 10)]
    )
    def test_none_when_nvidia_smi_cannot_run(self, error: Exception) -> None:
        with patch("subprocess.run", side_effect=error):
            assert nvidia_gpu_name() is None


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

    def test_queries_free_memory_on_the_visible_training_gpu(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Admission compares with free VRAM on the GPU CUDA will train on."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "2048\n"
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
        with patch("subprocess.run", return_value=mock_result) as run:
            assert available_vram_bytes() == 2048 * 1024 * 1024
        command = run.call_args.args[0]
        assert "--query-gpu=memory.free" in command
        assert not any(arg.startswith("--id=") for arg in command)
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,0")
        with patch("subprocess.run", return_value=mock_result) as run:
            available_vram_bytes()
        assert "--id=1" in run.call_args.args[0]

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
