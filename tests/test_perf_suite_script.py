"""Contract tests for the performance-suite runner."""

from __future__ import annotations

import json

from scripts.run_perf_suite import (
    PerfBudgets,
    PerfRssEnvelope,
    PerfTestResult,
    _budget_violations,
    _build_report,
    _write_artifacts,
)


def test_budget_violations_fail_when_no_perf_tests_are_collected() -> None:
    violations = _budget_violations(
        exit_code=0,
        collected_count=0,
        total_seconds=1.0,
        results=[],
        budgets=PerfBudgets(max_total_seconds=10.0, max_test_seconds=5.0),
    )

    assert violations == ["No performance tests were collected."]


def test_budget_violations_fail_when_perf_tests_are_skipped_or_xfailed() -> None:
    violations = _budget_violations(
        exit_code=0,
        collected_count=2,
        total_seconds=1.0,
        results=[
            PerfTestResult(
                nodeid="tests/test_perf.py::test_skipped",
                outcome="skipped",
                duration_seconds=0.0,
                phase="setup",
            ),
            PerfTestResult(
                nodeid="tests/test_perf.py::test_xfailed",
                outcome="skipped",
                duration_seconds=0.0,
                phase="call",
                wasxfail="known missing measurement",
            ),
        ],
        budgets=PerfBudgets(max_total_seconds=10.0, max_test_seconds=5.0),
    )

    assert violations == [
        "Performance tests must not be skipped; skipped perf tests measure nothing: "
        "tests/test_perf.py::test_skipped",
        "Performance tests must not be xfailed; expected failures hide perf coverage: "
        "tests/test_perf.py::test_xfailed",
        "No performance tests completed successfully.",
    ]


def test_budget_violations_fail_on_total_and_individual_regressions() -> None:
    violations = _budget_violations(
        exit_code=0,
        collected_count=1,
        total_seconds=11.0,
        results=[
            PerfTestResult(
                nodeid="tests/test_perf.py::test_slow",
                outcome="passed",
                duration_seconds=6.0,
                phase="call",
            )
        ],
        budgets=PerfBudgets(max_total_seconds=10.0, max_test_seconds=5.0),
    )

    assert "11.00s > 10.00s" in violations[0]
    assert "tests/test_perf.py::test_slow (6.00s)" in violations[1]


def test_report_artifacts_include_summary_and_slowest_tests(tmp_path) -> None:
    report = _build_report(
        exit_code=0,
        collected_count=2,
        total_seconds=3.5,
        results=[
            PerfTestResult("tests/test_perf.py::test_fast", "passed", 0.1, "call"),
            PerfTestResult(
                "tests/test_perf.py::test_slow",
                "passed",
                1.4,
                "call",
                evidence={"semantic_match": True, "physical_width": 6},
            ),
        ],
        budgets=PerfBudgets(max_total_seconds=10.0, max_test_seconds=5.0),
        command=["pytest", "-m", "perf"],
        polars_scale="1m",
        rss=PerfRssEnvelope(
            sampler="independent_process_rss_poll",
            peak_rss_bytes=123_456,
            sample_count=7,
            poll_interval_seconds=0.02,
        ),
    )

    _write_artifacts(report, tmp_path)

    payload = json.loads((tmp_path / "perf-report.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "perf-report.md").read_text(encoding="utf-8")
    assert payload["summary"]["collected"] == 2
    assert payload["schema_version"] == 2
    assert payload["scenario"]["polars_scale"] == "1m"
    assert payload["rss"]["peak_rss_bytes"] == 123_456
    assert payload["summary"]["slowest"][0]["nodeid"] == "tests/test_perf.py::test_slow"
    assert "`tests/test_perf.py::test_slow`" in markdown
    assert "Polars scale: 1m" in markdown
    assert "Independent peak RSS: 123,456 bytes" in markdown
    assert "Scenario Evidence" in markdown
    assert '"physical_width": 6' in markdown
