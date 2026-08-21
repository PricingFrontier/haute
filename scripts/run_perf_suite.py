"""Run the Python performance test lane with budgets and artifacts."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pytest

try:
    from scripts.memory_smoke import StdlibMemorySampler
except ModuleNotFoundError:  # Direct ``python scripts/run_perf_suite.py`` execution.
    from memory_smoke import StdlibMemorySampler

_POLARS_SCALE_ENV = "HAUTE_POLARS_PERF_SCALE"
_WALL_TIME_PARTITION_TOLERANCE_SECONDS = 0.05


def _nonnegative_float(value: object) -> float:
    """Argparse converter and public validation boundary for regression limits."""
    if isinstance(value, bool):
        raise ValueError("must be a finite non-negative number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("must be a finite non-negative number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError("must be a finite non-negative number")
    return number


def _arg_nonnegative_float(value: str) -> float:
    try:
        return _nonnegative_float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _installed_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


@dataclass(frozen=True)
class PerfTestResult:
    nodeid: str
    outcome: str
    duration_seconds: float
    phase: str
    wasxfail: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PerfBudgets:
    max_total_seconds: float
    max_test_seconds: float


@dataclass(frozen=True)
class PerfRssEnvelope:
    sampler: str
    peak_rss_bytes: int | None
    sample_count: int
    poll_interval_seconds: float


class ProcessRssMonitor:
    """Independently poll this runner process while in-process pytest runs."""

    def __init__(self, *, poll_interval_seconds: float = 0.02, sampler: Any = None) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")
        self._poll_interval_seconds = poll_interval_seconds
        self._sampler = sampler or StdlibMemorySampler()
        self._samples: list[int] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("RSS monitor has already started")

        def poll() -> None:
            while not self._stop.is_set():
                self._sample()
                self._stop.wait(self._poll_interval_seconds)

        self._thread = threading.Thread(target=poll, name="haute-perf-rss", daemon=True)
        self._thread.start()

    def stop(self) -> PerfRssEnvelope:
        if self._thread is None:
            raise RuntimeError("RSS monitor has not started")
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._poll_interval_seconds * 4))
        self._sample()
        return PerfRssEnvelope(
            sampler="independent_process_rss_poll",
            peak_rss_bytes=max(self._samples) if self._samples else None,
            sample_count=len(self._samples),
            poll_interval_seconds=self._poll_interval_seconds,
        )

    def _sample(self) -> None:
        rss = self._sampler.process_rss_bytes(os.getpid())
        if rss is not None:
            self._samples.append(int(rss))


class PerfReportPlugin:
    def __init__(self) -> None:
        self.collected_count = 0
        self.results: list[PerfTestResult] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected_count = len(session.items)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        should_record = report.when == "call" or (
            report.when in {"setup", "teardown"} and report.outcome in {"failed", "skipped"}
        )
        if not should_record:
            return

        evidence: dict[str, object] = {}
        for key, value in getattr(report, "user_properties", ()):
            if key == "haute_perf_evidence":
                if not isinstance(value, dict):
                    raise TypeError("haute_perf_evidence must be a mapping")
                evidence = {str(item_key): item_value for item_key, item_value in value.items()}

        self.results.append(
            PerfTestResult(
                nodeid=report.nodeid,
                outcome=report.outcome,
                duration_seconds=report.duration,
                phase=report.when,
                wasxfail=getattr(report, "wasxfail", None),
                evidence=evidence,
            )
        )


def _budget_violations(
    *,
    exit_code: int,
    collected_count: int,
    total_seconds: float,
    results: Sequence[PerfTestResult],
    budgets: PerfBudgets,
) -> list[str]:
    violations: list[str] = []
    if collected_count == 0:
        violations.append("No performance tests were collected.")
    skipped = [result for result in results if result.outcome == "skipped" and not result.wasxfail]
    xfailed = [result for result in results if result.wasxfail]
    if skipped:
        skipped_list = ", ".join(result.nodeid for result in skipped[:5])
        if len(skipped) > 5:
            skipped_list += ", ..."
        violations.append(
            "Performance tests must not be skipped; skipped perf tests measure nothing: "
            f"{skipped_list}"
        )
    if xfailed:
        xfailed_list = ", ".join(result.nodeid for result in xfailed[:5])
        if len(xfailed) > 5:
            xfailed_list += ", ..."
        violations.append(
            "Performance tests must not be xfailed; expected failures hide perf coverage: "
            f"{xfailed_list}"
        )
    if collected_count > 0 and not any(
        result.outcome == "passed" and not result.wasxfail for result in results
    ):
        violations.append("No performance tests completed successfully.")
    if exit_code != 0:
        violations.append(f"pytest exited with code {exit_code}.")
    if total_seconds > budgets.max_total_seconds:
        violations.append(
            "Performance suite exceeded total budget: "
            f"{total_seconds:.2f}s > {budgets.max_total_seconds:.2f}s."
        )

    slow_tests = [
        result for result in results if result.duration_seconds > budgets.max_test_seconds
    ]
    if slow_tests:
        formatted = ", ".join(
            f"{result.nodeid} ({result.duration_seconds:.2f}s)" for result in slow_tests
        )
        violations.append(
            "Performance tests exceeded individual budget "
            f"{budgets.max_test_seconds:.2f}s: {formatted}."
        )
    return violations


def _build_report(
    *,
    exit_code: int,
    collected_count: int,
    total_seconds: float,
    results: Sequence[PerfTestResult],
    budgets: PerfBudgets,
    command: Sequence[str],
    polars_scale: str = "ci",
    rss: PerfRssEnvelope | None = None,
) -> dict[str, object]:
    _validate_json_safe([result.evidence for result in results])
    reported_phase_seconds = sum(result.duration_seconds for result in results)
    runner_overhead_seconds = total_seconds - reported_phase_seconds
    if runner_overhead_seconds < -_WALL_TIME_PARTITION_TOLERANCE_SECONDS:
        raise ValueError(
            "Reported pytest phase durations overlap wall time: "
            f"{reported_phase_seconds:.6f}s reported > {total_seconds:.6f}s total."
        )
    runner_overhead_seconds = max(0.0, runner_overhead_seconds)
    if not _wall_time_partitions(total_seconds, reported_phase_seconds, runner_overhead_seconds):
        raise ValueError("Wall-time partition does not reconcile to total execution time.")
    outcomes: dict[str, int] = {}
    for result in results:
        key = "xfailed" if result.wasxfail and result.outcome == "skipped" else result.outcome
        outcomes[key] = outcomes.get(key, 0) + 1

    slowest = sorted(results, key=lambda result: result.duration_seconds, reverse=True)[:10]
    rss_payload = (
        asdict(rss)
        if rss is not None
        else {
            "sampler": "independent_process_rss_poll",
            "peak_rss_bytes": None,
            "sample_count": 0,
            "poll_interval_seconds": None,
        }
    )
    workload, resources = _summarise_evidence(results, rss_payload)
    wall_time = {
        "total_seconds": total_seconds,
        "reported_phase_seconds": reported_phase_seconds,
        "runner_overhead_seconds": runner_overhead_seconds,
        "partition_tolerance_seconds": _WALL_TIME_PARTITION_TOLERANCE_SECONDS,
    }
    return {
        "schema_version": 4,
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "haute": _installed_version("haute"),
            "polars": _installed_version("polars"),
            "pytest": _installed_version("pytest"),
            "compatibility": {
                "python_major_minor": ".".join(platform.python_version().split(".")[:2]),
                "platform_system": platform.system(),
                "platform_machine": platform.machine(),
                "polars_version": _installed_version("polars"),
            },
        },
        "scenario": {"polars_scale": polars_scale},
        "rss": rss_payload,
        "workload": workload,
        "resources": resources,
        "wall_time": wall_time,
        "command": list(command),
        "budgets": asdict(budgets),
        "summary": {
            "exit_code": exit_code,
            "collected": collected_count,
            "reported": len(results),
            "total_seconds": total_seconds,
            "outcomes": outcomes,
            "slowest": [asdict(result) for result in slowest],
        },
        "tests": [asdict(result) for result in results],
        "historical_comparison": {
            "status": "not_requested",
            "thresholds": {
                "max_total_regression_fraction": 0.25,
                "max_rss_regression_fraction": 0.25,
                "max_test_regression_fraction": 0.35,
                "min_test_baseline_seconds": 0.10,
            },
            "violations": [],
        },
    }


def _finite_nonnegative(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Baseline {field} must be a finite non-negative number.")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Baseline {field} must be a finite non-negative number.")
    return number


def _load_baseline_report(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read baseline report {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 4:
        raise ValueError("Baseline report must be a JSON object with schema_version=4.")
    for name in ("scenario", "environment", "summary", "rss", "wall_time", "tests"):
        if not isinstance(payload.get(name), dict if name != "tests" else list):
            raise ValueError(f"Baseline report has invalid {name} shape.")
    scenario, environment, summary, rss, wall_time = (
        payload["scenario"],
        payload["environment"],
        payload["summary"],
        payload["rss"],
        payload["wall_time"],
    )
    assert isinstance(scenario, dict) and isinstance(environment, dict)
    assert isinstance(summary, dict) and isinstance(rss, dict) and isinstance(wall_time, dict)
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        raise ValueError("Baseline report has invalid generated_at.")
    try:
        if datetime.fromisoformat(generated_at).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Baseline report has invalid generated_at.") from exc
    if scenario.get("polars_scale") not in {"ci", "1m", "10m"}:
        raise ValueError("Baseline report has invalid scenario.polars_scale.")
    compatibility = environment.get("compatibility")
    if (
        not isinstance(compatibility, dict)
        or set(compatibility)
        != {"python_major_minor", "platform_system", "platform_machine", "polars_version"}
        or not all(isinstance(value, str) and value for value in compatibility.values())
    ):
        raise ValueError("Baseline report has invalid environment.compatibility.")
    total_seconds = _finite_nonnegative(summary.get("total_seconds"), "summary.total_seconds")
    if not isinstance(summary.get("outcomes"), dict) or not isinstance(
        summary.get("slowest"), list
    ):
        raise ValueError("Baseline report has invalid summary shape.")
    if (
        not isinstance(summary.get("exit_code"), int)
        or isinstance(summary["exit_code"], bool)
        or summary["exit_code"] != 0
    ):
        raise ValueError("Baseline report has invalid summary.exit_code.")
    for summary_field in ("collected", "reported"):
        value = summary.get(summary_field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"Baseline report has invalid summary.{summary_field}.")
    if summary["reported"] != len(payload["tests"]):
        raise ValueError("Baseline report summary.reported does not match tests.")
    if summary["collected"] != summary["reported"]:
        raise ValueError("Baseline report collection and result counts do not match.")
    outcomes = summary["outcomes"]
    assert isinstance(outcomes, dict)
    if outcomes != {"passed": summary["reported"]}:
        raise ValueError("Baseline report has invalid summary.outcomes.")
    if not isinstance(rss.get("sampler"), str) or not rss["sampler"]:
        raise ValueError("Baseline report has invalid rss.sampler.")
    peak = rss.get("peak_rss_bytes")
    if not isinstance(peak, int) or isinstance(peak, bool) or peak <= 0:
        raise ValueError("Baseline rss.peak_rss_bytes must be a positive integer.")
    sample_count = rss.get("sample_count")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count <= 0:
        raise ValueError("Baseline rss.sample_count must be a positive integer.")
    poll_interval = rss.get("poll_interval_seconds")
    if _finite_nonnegative(poll_interval, "rss.poll_interval_seconds") <= 0:
        raise ValueError("Baseline rss.poll_interval_seconds must be greater than zero.")
    tests = payload["tests"]
    assert isinstance(tests, list)
    identities: set[tuple[str, str]] = set()
    required_test_fields = {
        "nodeid",
        "outcome",
        "duration_seconds",
        "phase",
        "wasxfail",
        "evidence",
    }
    for index, test in enumerate(tests):
        if (
            not isinstance(test, dict)
            or set(test) != required_test_fields
            or not isinstance(test.get("nodeid"), str)
            or not test["nodeid"]
        ):
            raise ValueError(f"Baseline report has invalid tests[{index}] shape.")
        if (
            test.get("outcome") != "passed"
            or test.get("phase") != "call"
            or test.get("wasxfail") is not None
            or not isinstance(test.get("evidence"), dict)
        ):
            raise ValueError(f"Baseline report has invalid tests[{index}] shape.")
        _finite_nonnegative(test.get("duration_seconds"), f"tests[{index}].duration_seconds")
        identity = (test["nodeid"], test["phase"])
        if identity in identities:
            raise ValueError(f"Baseline report has duplicate test identity {identity!r}.")
        identities.add(identity)
    expected_slowest = sorted(
        tests,
        key=lambda item: float(item["duration_seconds"]),
        reverse=True,
    )[:10]
    if summary["slowest"] != expected_slowest:
        raise ValueError("Baseline report summary.slowest does not match tests.")
    wall_total = _finite_nonnegative(wall_time.get("total_seconds"), "wall_time.total_seconds")
    wall_reported = _finite_nonnegative(
        wall_time.get("reported_phase_seconds"),
        "wall_time.reported_phase_seconds",
    )
    wall_overhead = _finite_nonnegative(
        wall_time.get("runner_overhead_seconds"),
        "wall_time.runner_overhead_seconds",
    )
    tolerance = _finite_nonnegative(
        wall_time.get("partition_tolerance_seconds"),
        "wall_time.partition_tolerance_seconds",
    )
    reported_from_tests = sum(float(item["duration_seconds"]) for item in tests)
    if (
        tolerance != _WALL_TIME_PARTITION_TOLERANCE_SECONDS
        or wall_total != total_seconds
        or abs(wall_reported - reported_from_tests) > tolerance
        or not _wall_time_partitions(wall_total, wall_reported, wall_overhead)
    ):
        raise ValueError("Baseline report has an inconsistent wall-time partition.")
    return payload


def _historical_comparison(
    report: dict[str, object],
    baseline: dict[str, object] | None,
    *,
    max_total: float = 0.25,
    max_rss: float = 0.25,
    max_test: float = 0.35,
    min_test_baseline: float = 0.10,
) -> tuple[dict[str, object], list[str]]:
    thresholds = {
        "max_total_regression_fraction": _nonnegative_float(max_total),
        "max_rss_regression_fraction": _nonnegative_float(max_rss),
        "max_test_regression_fraction": _nonnegative_float(max_test),
        "min_test_baseline_seconds": _nonnegative_float(min_test_baseline),
    }
    if baseline is None:
        return {"status": "not_requested", "thresholds": thresholds, "violations": []}, []
    current_scenario, baseline_scenario = report["scenario"], baseline["scenario"]
    current_environment, baseline_environment = report["environment"], baseline["environment"]
    assert isinstance(current_scenario, dict) and isinstance(baseline_scenario, dict)
    assert isinstance(current_environment, dict) and isinstance(baseline_environment, dict)
    reasons = []
    if current_scenario["polars_scale"] != baseline_scenario["polars_scale"]:
        reasons.append("polars_scale_mismatch")
    if current_environment["compatibility"] != baseline_environment["compatibility"]:
        reasons.append("environment_compatibility_mismatch")
    if reasons:
        return {
            "status": "incompatible",
            "reasons": reasons,
            "thresholds": thresholds,
            "violations": [],
            "baseline_generated_at": baseline.get("generated_at"),
        }, []
    current_summary, baseline_summary = report["summary"], baseline["summary"]
    current_rss, baseline_rss = report["rss"], baseline["rss"]
    assert isinstance(current_summary, dict) and isinstance(baseline_summary, dict)
    assert isinstance(current_rss, dict) and isinstance(baseline_rss, dict)
    comparisons: list[dict[str, object]] = []

    def add(name: str, prior: object, now: object, fraction: float) -> None:
        if prior is None or now is None:
            return
        baseline_value, current_value = (
            _finite_nonnegative(prior, name),
            _finite_nonnegative(now, name),
        )
        limit = baseline_value * (1 + fraction)
        if not math.isfinite(limit):
            raise ValueError(f"Historical comparison limit for {name} is not finite.")
        comparisons.append(
            {
                "name": name,
                "baseline": baseline_value,
                "current": current_value,
                "limit": limit,
                "regressed": current_value > limit,
            }
        )

    baseline_tests = {
        (item["nodeid"], item["phase"]): item
        for item in baseline["tests"]
        if isinstance(item, dict)
        and item.get("outcome") == "passed"
        and item.get("phase") == "call"
    }
    current_tests = {
        (item["nodeid"], item["phase"]): item
        for item in report["tests"]
        if isinstance(item, dict)
        and item.get("outcome") == "passed"
        and item.get("phase") == "call"
    }
    suite_metrics_status = "compared"
    if set(baseline_tests) == set(current_tests):
        add(
            "total_seconds",
            baseline_summary["total_seconds"],
            current_summary["total_seconds"],
            thresholds["max_total_regression_fraction"],
        )
        add(
            "peak_rss_bytes",
            baseline_rss.get("peak_rss_bytes"),
            current_rss.get("peak_rss_bytes"),
            thresholds["max_rss_regression_fraction"],
        )
    else:
        suite_metrics_status = "skipped_test_identity_mismatch"
    material = 0
    for identity, item in current_tests.items():
        prior = baseline_tests.get(identity)
        if (
            prior
            and _finite_nonnegative(prior["duration_seconds"], "test duration")
            >= thresholds["min_test_baseline_seconds"]
        ):
            material += 1
            add(
                f"test:{item['nodeid']}",
                prior["duration_seconds"],
                item.get("duration_seconds"),
                thresholds["max_test_regression_fraction"],
            )
    violations = [
        "Historical performance regression: "
        f"{item['name']} {item['current']:.3f} > {item['limit']:.3f}."
        for item in comparisons
        if item["regressed"]
    ]
    if material == 0:
        violations.append(
            "Historical performance comparison found zero material matching "
            "passed call-phase tests."
        )
    return {
        "status": "compared",
        "baseline_generated_at": baseline.get("generated_at"),
        "thresholds": thresholds,
        "suite_metrics_status": suite_metrics_status,
        "comparisons": comparisons,
        "violations": violations,
    }, violations


def _wall_time_partitions(total: float, reported: float, overhead: float) -> bool:
    return abs((reported + overhead) - total) <= _WALL_TIME_PARTITION_TOLERANCE_SECONDS


def _validate_json_safe(evidence: object) -> None:
    """Reject non-portable test evidence before it becomes a CI artifact."""
    try:
        json.dumps(evidence, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"haute_perf_evidence must be JSON-safe: {exc}") from exc


def _summarise_evidence(
    results: Sequence[PerfTestResult], rss: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    """Create deterministic aggregate evidence while retaining each test's detail."""
    scenarios: dict[str, dict[str, object]] = {}
    input_bytes = output_bytes = n_collects = n_checkpoints = chunk_count = 0
    any_input_bytes = any_output_bytes = any_collects = any_checkpoints = any_chunks = False
    temp_disk_peaks: list[int] = []
    admissions: list[object] = []
    payload_bytes: list[int] = []
    for result in results:
        evidence = result.evidence
        name = evidence.get("scenario")
        if not isinstance(name, str):
            continue
        scenario = scenarios.setdefault(
            name, {"name": name, "scales": set(), "execution_profiles": set(), "inputs": []}
        )
        scale = evidence.get("scale")
        if isinstance(scale, str):
            scenario["scales"].add(scale)  # type: ignore[index]
        profiles = evidence.get("execution_profiles", [])
        if isinstance(profiles, list):
            scenario["execution_profiles"].update(str(profile) for profile in profiles)  # type: ignore[index]
        descriptor = evidence.get("input")
        if isinstance(descriptor, dict):
            scenario["inputs"].append(descriptor)  # type: ignore[index]
            value = descriptor.get("total_bytes")
            if isinstance(value, int):
                input_bytes += value
                any_input_bytes = True
        metrics = evidence.get("product_metrics")
        if isinstance(metrics, dict):
            for key, accumulator in (
                ("n_collects", "collects"),
                ("n_checkpoints", "checkpoints"),
                ("chunk_count", "chunks"),
            ):
                value = metrics.get(key)
                if isinstance(value, int):
                    if accumulator == "collects":
                        n_collects += value
                        any_collects = True
                    elif accumulator == "checkpoints":
                        n_checkpoints += value
                        any_checkpoints = True
                    else:
                        chunk_count += value
                        any_chunks = True
            value = metrics.get("output_bytes")
            if isinstance(value, int):
                output_bytes += value
                any_output_bytes = True
            value = metrics.get("temp_disk_peak_bytes")
            if isinstance(value, int):
                temp_disk_peaks.append(value)
        if "admission" in evidence:
            admissions.append(evidence["admission"])
        value = evidence.get("payload_bytes")
        if isinstance(value, int):
            payload_bytes.append(value)
    workload_scenarios = []
    for scenario in scenarios.values():
        workload_scenarios.append(
            {
                "name": scenario["name"],
                "scales": sorted(scenario["scales"]),
                "execution_profiles": sorted(scenario["execution_profiles"]),
                "inputs": sorted(
                    scenario["inputs"], key=lambda item: json.dumps(item, sort_keys=True)
                ),
            }
        )
    workload = {
        "scenarios": sorted(workload_scenarios, key=lambda item: item["name"]),
        "execution_profiles": sorted(
            {
                profile
                for scenario in workload_scenarios
                for profile in scenario["execution_profiles"]
            }
        ),
    }
    resources = {
        "rss": rss,
        "input_bytes": input_bytes if any_input_bytes else None,
        "output_bytes": output_bytes if any_output_bytes else None,
        "n_collects": n_collects if any_collects else None,
        "n_checkpoints": n_checkpoints if any_checkpoints else None,
        "chunk_count": chunk_count if any_chunks else None,
        "temp_disk_peak_bytes": max(temp_disk_peaks) if temp_disk_peaks else None,
        "admission_states": sorted(
            {
                str(item.get("state"))
                for item in admissions
                if isinstance(item, dict) and item.get("state") is not None
            }
        ),
        "payload_bytes": sum(payload_bytes) if payload_bytes else None,
    }
    return workload, resources


def _write_markdown_summary(report: dict[str, object], path: Path) -> None:
    summary = report["summary"]
    assert isinstance(summary, dict)
    budgets = report["budgets"]
    assert isinstance(budgets, dict)
    scenario = report["scenario"]
    assert isinstance(scenario, dict)
    rss = report["rss"]
    assert isinstance(rss, dict)
    slowest = summary["slowest"]
    assert isinstance(slowest, list)
    tests = report["tests"]
    assert isinstance(tests, list)
    peak_rss = rss["peak_rss_bytes"]
    peak_rss_label = "unavailable" if peak_rss is None else f"{int(peak_rss):,} bytes"

    lines = [
        "# Performance Test Report",
        "",
        f"- Polars scale: {scenario['polars_scale']}",
        f"- Independent peak RSS: {peak_rss_label}",
        f"- RSS samples: {rss['sample_count']}",
        f"- Total: {summary['total_seconds']:.2f}s",
        f"- Collected: {summary['collected']}",
        f"- Reported: {summary['reported']}",
        f"- Exit code: {summary['exit_code']}",
        f"- Total budget: {budgets['max_total_seconds']:.2f}s",
        f"- Per-test budget: {budgets['max_test_seconds']:.2f}s",
        "",
        "## Slowest Tests",
        "",
        "| Duration | Outcome | Test |",
        "| ---: | --- | --- |",
    ]
    for item in slowest:
        assert isinstance(item, dict)
        lines.append(
            f"| {item['duration_seconds']:.2f}s | {item['outcome']} | `{item['nodeid']}` |"
        )
    historical = report["historical_comparison"]
    assert isinstance(historical, dict)
    lines.extend(["", "## Historical Comparison", "", f"- Status: {historical['status']}"])
    if historical.get("baseline_generated_at"):
        lines.append(f"- Baseline generated at: {historical['baseline_generated_at']}")
    thresholds = historical.get("thresholds", {})
    if isinstance(thresholds, dict):
        lines.append(f"- Thresholds: `{json.dumps(thresholds, sort_keys=True)}`")
    if historical.get("suite_metrics_status"):
        lines.append(f"- Suite metrics: {historical['suite_metrics_status']}")
    for reason in historical.get("reasons", []):
        lines.append(f"- Incompatible: {reason}")
    for violation in historical.get("violations", []):
        lines.append(f"- {violation}")
    comparisons = historical.get("comparisons", [])
    if isinstance(comparisons, list):
        lines.extend(
            [
                "",
                "| Metric | Baseline | Current | Limit | Regressed |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
        )
        for comparison in comparisons:
            if isinstance(comparison, dict):
                lines.append(
                    f"| {comparison['name']} | {comparison['baseline']:.3f} | "
                    f"{comparison['current']:.3f} | {comparison['limit']:.3f} | "
                    f"{comparison['regressed']} |"
                )
    evidence_rows = []
    for item in tests:
        assert isinstance(item, dict)
        evidence = item.get("evidence")
        if isinstance(evidence, dict) and evidence:
            evidence_rows.append((str(item["nodeid"]), json.dumps(evidence, sort_keys=True)))
    if evidence_rows:
        lines.extend(
            [
                "",
                "## Scenario Evidence",
                "",
                "| Test | Evidence |",
                "| --- | --- |",
            ]
        )
        for nodeid, evidence_json in evidence_rows:
            lines.append(f"| `{nodeid}` | `{evidence_json}` |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_artifacts(report: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "perf-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown_summary(report, output_dir / "perf-report.md")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".cache/perf"),
        help="Directory for JSON, Markdown, and JUnit performance artifacts.",
    )
    parser.add_argument(
        "--max-total-seconds",
        type=float,
        default=360.0,
        help="Maximum wall-clock time for the whole performance lane.",
    )
    parser.add_argument(
        "--max-test-seconds",
        type=float,
        default=120.0,
        help="Maximum call/setup/teardown duration for an individual perf test.",
    )
    parser.add_argument(
        "--polars-scale",
        choices=("ci", "1m", "10m"),
        default="ci",
        help="Generated Polars scenario scale. The 1m and 10m variants are opt-in.",
    )
    parser.add_argument(
        "--rss-poll-interval",
        type=float,
        default=0.02,
        help="Seconds between independent process-RSS samples.",
    )
    parser.add_argument(
        "--baseline-report", type=Path, help="Schema-v4 report used as a historical baseline."
    )
    parser.add_argument(
        "--max-total-regression-fraction", type=_arg_nonnegative_float, default=0.25
    )
    parser.add_argument("--max-rss-regression-fraction", type=_arg_nonnegative_float, default=0.25)
    parser.add_argument("--max-test-regression-fraction", type=_arg_nonnegative_float, default=0.35)
    parser.add_argument("--min-test-baseline-seconds", type=_arg_nonnegative_float, default=0.10)
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Additional argument forwarded to pytest. May be provided more than once.",
    )
    parser.add_argument(
        "--pytest-target",
        action="append",
        default=None,
        help=(
            "Pytest file or directory target to collect. May be provided more than once. "
            "Defaults to tests/."
        ),
    )
    return parser.parse_args(argv)


def _build_pytest_args(args: argparse.Namespace, junit_path: Path) -> list[str]:
    targets = args.pytest_target or ["tests/"]
    return [
        *targets,
        "-q",
        "-m",
        "perf",
        "--override-ini=addopts=",
        "--strict-markers",
        "--strict-config",
        f"--timeout={args.max_test_seconds}",
        "--timeout-method=thread",
        "--durations=20",
        f"--junitxml={junit_path}",
        *args.pytest_arg,
    ]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    budgets = PerfBudgets(
        max_total_seconds=args.max_total_seconds,
        max_test_seconds=args.max_test_seconds,
    )
    baseline = _load_baseline_report(args.baseline_report) if args.baseline_report else None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    junit_path = args.output_dir / "perf-junit.xml"
    pytest_args = _build_pytest_args(args, junit_path)

    plugin = PerfReportPlugin()
    rss_monitor = ProcessRssMonitor(poll_interval_seconds=args.rss_poll_interval)
    previous_scale = os.environ.get(_POLARS_SCALE_ENV)
    os.environ[_POLARS_SCALE_ENV] = args.polars_scale
    start = time.perf_counter()
    rss_monitor.start()
    try:
        exit_code = pytest.main(pytest_args, plugins=[plugin])
    finally:
        total_seconds = time.perf_counter() - start
        rss = rss_monitor.stop()
        if previous_scale is None:
            os.environ.pop(_POLARS_SCALE_ENV, None)
        else:
            os.environ[_POLARS_SCALE_ENV] = previous_scale

    report = _build_report(
        exit_code=exit_code,
        collected_count=plugin.collected_count,
        total_seconds=total_seconds,
        results=plugin.results,
        budgets=budgets,
        command=["pytest", *pytest_args],
        polars_scale=args.polars_scale,
        rss=rss,
    )
    historical, historical_violations = _historical_comparison(
        report,
        baseline,
        max_total=args.max_total_regression_fraction,
        max_rss=args.max_rss_regression_fraction,
        max_test=args.max_test_regression_fraction,
        min_test_baseline=args.min_test_baseline_seconds,
    )
    report["historical_comparison"] = historical
    _write_artifacts(report, args.output_dir)

    violations = _budget_violations(
        exit_code=exit_code,
        collected_count=plugin.collected_count,
        total_seconds=total_seconds,
        results=plugin.results,
        budgets=budgets,
    )
    violations.extend(historical_violations)
    if violations:
        for violation in violations:
            print(f"PERF FAIL: {violation}", file=sys.stderr)
        print(f"Artifacts written to {args.output_dir}", file=sys.stderr)
        return 1

    print(f"Performance lane passed in {total_seconds:.2f}s")
    print(f"Artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
