from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_run_perf_suite():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_perf_suite.py"
    spec = importlib.util.spec_from_file_location("run_perf_suite", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_perf_suite = _load_run_perf_suite()


def test_build_pytest_args_selects_owned_perf_lane(tmp_path: Path) -> None:
    args = run_perf_suite._parse_args(
        [
            "--output-dir",
            str(tmp_path),
            "--max-test-seconds",
            "17",
            "--pytest-arg",
            "tests/custom",
            "--pytest-arg=-k",
            "--pytest-arg",
            "cache",
        ]
    )
    junit_path = tmp_path / "perf-junit.xml"

    pytest_args = run_perf_suite._build_pytest_args(args, junit_path)

    assert pytest_args[:3] == ["tests/", "-q", "-m"]
    assert pytest_args[3] == "perf"
    assert "--override-ini=addopts=" in pytest_args
    assert "--strict-markers" in pytest_args
    assert "--strict-config" in pytest_args
    assert "--timeout=17.0" in pytest_args
    assert "--timeout-method=thread" in pytest_args
    assert f"--junitxml={junit_path}" in pytest_args
    assert pytest_args[-3:] == ["tests/custom", "-k", "cache"]


def test_parse_args_defaults_to_ci_scale_and_requires_opt_in_for_large_scales() -> None:
    assert run_perf_suite._parse_args([]).polars_scale == "ci"
    assert run_perf_suite._parse_args(["--polars-scale", "1m"]).polars_scale == "1m"
    assert run_perf_suite._parse_args(["--polars-scale", "10m"]).polars_scale == "10m"


def test_build_pytest_args_allows_targeted_perf_lane(tmp_path: Path) -> None:
    args = run_perf_suite._parse_args(
        [
            "--output-dir",
            str(tmp_path),
            "--pytest-target",
            "tests/performance/test_preview_trace_perf.py",
            "--pytest-arg=-k",
            "--pytest-arg",
            "trace",
        ]
    )
    junit_path = tmp_path / "perf-junit.xml"

    pytest_args = run_perf_suite._build_pytest_args(args, junit_path)

    assert pytest_args[0] == "tests/performance/test_preview_trace_perf.py"
    assert "tests/" not in pytest_args[:1]
    assert pytest_args[-2:] == ["-k", "trace"]


def test_budget_violations_fail_for_empty_or_slow_lane() -> None:
    budgets = run_perf_suite.PerfBudgets(max_total_seconds=10.0, max_test_seconds=1.0)
    slow_result = run_perf_suite.PerfTestResult(
        nodeid="tests/test_perf.py::test_slow",
        outcome="passed",
        duration_seconds=1.25,
        phase="call",
    )

    violations = run_perf_suite._budget_violations(
        exit_code=0,
        collected_count=0,
        total_seconds=12.0,
        results=[slow_result],
        budgets=budgets,
    )

    assert "No performance tests were collected." in violations
    assert any("exceeded total budget" in violation for violation in violations)
    assert any("exceeded individual budget" in violation for violation in violations)


def test_build_report_records_artifact_ready_summary() -> None:
    budgets = run_perf_suite.PerfBudgets(max_total_seconds=60.0, max_test_seconds=5.0)
    results = [
        run_perf_suite.PerfTestResult(
            nodeid="tests/test_perf.py::test_fast",
            outcome="passed",
            duration_seconds=0.2,
            phase="call",
        ),
        run_perf_suite.PerfTestResult(
            nodeid="tests/test_perf.py::test_expected_skip",
            outcome="skipped",
            duration_seconds=0.1,
            phase="setup",
            wasxfail="known limitation",
        ),
    ]

    report = run_perf_suite._build_report(
        exit_code=0,
        collected_count=2,
        total_seconds=0.3,
        results=results,
        budgets=budgets,
        command=["pytest", "-m", "perf"],
    )

    assert report["schema_version"] == 3
    assert report["scenario"] == {"polars_scale": "ci"}
    assert report["rss"]["peak_rss_bytes"] is None
    assert set(report["environment"]) == {
        "python",
        "platform",
        "haute",
        "polars",
        "pytest",
    }
    assert report["budgets"] == {"max_total_seconds": 60.0, "max_test_seconds": 5.0}
    assert report["summary"]["outcomes"] == {"passed": 1, "xfailed": 1}
    assert report["summary"]["slowest"][0]["nodeid"] == "tests/test_perf.py::test_fast"
    assert report["resources"]["input_bytes"] is None
    assert report["resources"]["temp_disk_peak_bytes"] is None
    assert report["wall_time"] == {
        "total_seconds": 0.3,
        "reported_phase_seconds": 0.30000000000000004,
        "runner_overhead_seconds": 0.0,
        "partition_tolerance_seconds": 0.05,
    }


def test_build_report_aggregates_sorted_evidence_and_rejects_overlapping_wall_time() -> None:
    budgets = run_perf_suite.PerfBudgets(max_total_seconds=60.0, max_test_seconds=5.0)
    result = run_perf_suite.PerfTestResult(
        nodeid="tests/test_perf.py::test_evidence",
        outcome="passed",
        duration_seconds=0.2,
        phase="call",
        evidence={
            "scenario": "z_scenario",
            "scale": "ci",
            "execution_profiles": ["training_prep", "preview_eager"],
            "input": {"rows": 2, "total_bytes": 12, "schema_widths": {"base": 3}},
            "product_metrics": {
                "n_collects": 1,
                "n_checkpoints": None,
                "chunk_count": 0,
                "output_bytes": None,
                "temp_disk_peak_bytes": None,
            },
            "admission": {"state": "admitted"},
            "payload_bytes": None,
        },
    )
    report = run_perf_suite._build_report(
        exit_code=0,
        collected_count=1,
        total_seconds=0.25,
        results=[result],
        budgets=budgets,
        command=["pytest"],
    )

    assert report["workload"]["scenarios"][0]["execution_profiles"] == [
        "preview_eager",
        "training_prep",
    ]
    assert report["workload"]["execution_profiles"] == ["preview_eager", "training_prep"]
    assert report["resources"] == {
        "rss": report["rss"],
        "input_bytes": 12,
        "output_bytes": None,
        "n_collects": 1,
        "n_checkpoints": None,
        "chunk_count": 0,
        "temp_disk_peak_bytes": None,
        "admission_states": ["admitted"],
        "payload_bytes": None,
    }
    import pytest

    with pytest.raises(ValueError, match="overlap"):
        run_perf_suite._build_report(
            exit_code=0,
            collected_count=1,
            total_seconds=0.1,
            results=[result],
            budgets=budgets,
            command=["pytest"],
        )


def test_process_rss_monitor_records_independent_peak() -> None:
    from threading import Event

    class Sampler:
        def __init__(self) -> None:
            self.values = iter([10, 30, 20])
            self.sample_count = 0
            self.third_sample = Event()

        def process_rss_bytes(self, _pid: int) -> int:
            self.sample_count += 1
            if self.sample_count >= 3:
                self.third_sample.set()
            return next(self.values, 20)

    sampler = Sampler()
    monitor = run_perf_suite.ProcessRssMonitor(
        poll_interval_seconds=0.001,
        sampler=sampler,
    )
    monitor.start()
    assert sampler.third_sample.wait(timeout=1)
    envelope = monitor.stop()

    assert envelope.sampler == "independent_process_rss_poll"
    assert envelope.peak_rss_bytes == 30
    assert envelope.sample_count >= 3
