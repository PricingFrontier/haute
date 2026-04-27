"""Run the Python performance test lane with budgets and artifacts."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest


@dataclass(frozen=True)
class PerfTestResult:
    nodeid: str
    outcome: str
    duration_seconds: float
    phase: str
    wasxfail: str | None = None


@dataclass(frozen=True)
class PerfBudgets:
    max_total_seconds: float
    max_test_seconds: float


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

        self.results.append(
            PerfTestResult(
                nodeid=report.nodeid,
                outcome=report.outcome,
                duration_seconds=report.duration,
                phase=report.when,
                wasxfail=getattr(report, "wasxfail", None),
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
) -> dict[str, object]:
    outcomes: dict[str, int] = {}
    for result in results:
        key = "xfailed" if result.wasxfail and result.outcome == "skipped" else result.outcome
        outcomes[key] = outcomes.get(key, 0) + 1

    slowest = sorted(results, key=lambda result: result.duration_seconds, reverse=True)[:10]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
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
    }


def _write_markdown_summary(report: dict[str, object], path: Path) -> None:
    summary = report["summary"]
    assert isinstance(summary, dict)
    budgets = report["budgets"]
    assert isinstance(budgets, dict)
    slowest = summary["slowest"]
    assert isinstance(slowest, list)

    lines = [
        "# Performance Test Report",
        "",
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
    args.output_dir.mkdir(parents=True, exist_ok=True)
    junit_path = args.output_dir / "perf-junit.xml"
    pytest_args = _build_pytest_args(args, junit_path)

    plugin = PerfReportPlugin()
    start = time.perf_counter()
    exit_code = pytest.main(pytest_args, plugins=[plugin])
    total_seconds = time.perf_counter() - start

    report = _build_report(
        exit_code=exit_code,
        collected_count=plugin.collected_count,
        total_seconds=total_seconds,
        results=plugin.results,
        budgets=budgets,
        command=["pytest", *pytest_args],
    )
    _write_artifacts(report, args.output_dir)

    violations = _budget_violations(
        exit_code=exit_code,
        collected_count=plugin.collected_count,
        total_seconds=total_seconds,
        results=plugin.results,
        budgets=budgets,
    )
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
