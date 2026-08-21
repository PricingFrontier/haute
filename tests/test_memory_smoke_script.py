"""Contract tests for the memory smoke command wrapper."""

from __future__ import annotations

import json
import os
import sys

import pytest

from scripts import memory_smoke


def _python_command(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def _summary_from_stdout(stdout: str) -> dict[str, object]:
    return json.loads(stdout)


def test_successful_command_emits_json_summary(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = memory_smoke.main(["--", *_python_command("print('ok')")])

    captured = capsys.readouterr()
    summary = _summary_from_stdout(captured.out)
    assert exit_code == 0
    assert summary["command"] == _python_command("print('ok')")
    assert summary["exit_code"] == 0
    assert summary["elapsed_seconds"] >= 0
    assert isinstance(summary["python_peak_tracemalloc_bytes"], int)
    assert "ok" in captured.err


def test_failing_command_propagates_exit_code_and_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = memory_smoke.main(["--", *_python_command("raise SystemExit(7)")])

    captured = capsys.readouterr()
    summary = _summary_from_stdout(captured.out)
    assert exit_code == 7
    assert summary["exit_code"] == 7
    assert summary["command"] == _python_command("raise SystemExit(7)")


def test_invalid_args_fail_before_running_command() -> None:
    with pytest.raises(SystemExit) as exc_info:
        memory_smoke._parse_args([])

    assert exc_info.value.code == 2


class FakeSampler:
    def __init__(self) -> None:
        self.parent_rss = iter([100, 120])
        self.child_rss = iter([500, 300])

    def process_rss_bytes(self, pid: int) -> int | None:
        if pid == os.getpid():
            return next(self.parent_rss)
        return next(self.child_rss, None)

    def process_peak_rss_bytes(self) -> int | None:
        return 256

    def child_peak_rss_bytes(self) -> int | None:
        return None


def test_memory_metrics_can_be_supplied_by_sampler() -> None:
    summary = memory_smoke.run_smoke(
        command=_python_command("pass"),
        enable_tracemalloc=False,
        poll_interval_seconds=0.001,
        sampler=FakeSampler(),
        child_output=None,
    )

    assert summary["process_rss_before_bytes"] == 100
    assert summary["process_rss_after_bytes"] == 120
    assert summary["process_peak_rss_bytes"] == 256
    assert summary["child_peak_rss_bytes"] == 500
    assert summary["child_rss_sample_count"] >= 1
    assert summary["python_peak_tracemalloc_bytes"] is None


class StickyChildPeakSampler:
    def __init__(self) -> None:
        self.parent_rss = iter([100, 120])
        self.resource_peak = iter([700, 900])

    def process_rss_bytes(self, pid: int) -> int | None:
        if pid == os.getpid():
            return next(self.parent_rss)
        return 200

    def process_peak_rss_bytes(self) -> int | None:
        return 256

    def child_peak_rss_bytes(self) -> int | None:
        return next(self.resource_peak)


def test_live_child_samples_are_not_contaminated_by_sticky_resource_peak() -> None:
    summary = memory_smoke.run_smoke(
        command=_python_command("import time; time.sleep(0.02)"),
        enable_tracemalloc=False,
        poll_interval_seconds=0.001,
        sampler=StickyChildPeakSampler(),
        child_output=None,
    )

    assert summary["child_rss_sample_count"] >= 1
    assert summary["child_peak_rss_bytes"] == 200


class NoLiveChildPeakSampler(StickyChildPeakSampler):
    def process_rss_bytes(self, pid: int) -> int | None:
        return next(self.parent_rss) if pid == os.getpid() else None


def test_resource_peak_is_retained_when_no_live_child_sample_exists() -> None:
    sampler = NoLiveChildPeakSampler()

    summary = memory_smoke.run_smoke(
        command=_python_command("pass"),
        enable_tracemalloc=False,
        poll_interval_seconds=0.001,
        sampler=sampler,
        child_output=None,
    )

    assert summary["child_rss_sample_count"] == 0
    assert summary["child_peak_rss_bytes"] == 900


def test_poll_interval_must_be_positive() -> None:
    with pytest.raises(SystemExit) as exc_info:
        memory_smoke._parse_args(["--poll-interval", "0", "--", *_python_command("pass")])

    assert exc_info.value.code == 2
