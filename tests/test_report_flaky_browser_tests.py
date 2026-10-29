"""Flaky browser tests: named on a pull request, red on a push to main."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts import report_flaky_browser_tests as report
from tests._source_files import REPO_ROOT


def _test(status: str, project: str = "chromium") -> dict[str, object]:
    return {"projectName": project, "status": status, "results": []}


_REPORT = {
    "suites": [
        {
            "title": "core-flows.spec.ts",
            "file": "core-flows.spec.ts",
            "specs": [{"title": "opens the pipeline", "tests": [_test("expected")]}],
            "suites": [
                {
                    "title": "Banding",
                    "file": "core-flows.spec.ts",
                    "specs": [
                        {"title": "saves rules", "tests": [_test("flaky")]},
                        {"title": "rejects a gap", "tests": [_test("unexpected")]},
                    ],
                },
            ],
        },
        {
            "title": "smoke.spec.ts",
            "file": "smoke.spec.ts",
            "specs": [
                {
                    "title": "loads @smoke",
                    "tests": [_test("expected"), _test("flaky", "firefox-smoke")],
                }
            ],
        },
    ]
}


def test_only_tests_that_passed_on_a_retry_are_named_with_their_path() -> None:
    assert report.flaky_tests(_REPORT) == [
        "core-flows.spec.ts › Banding › saves rules [chromium]",
        "smoke.spec.ts › loads @smoke [firefox-smoke]",
    ]


def test_the_job_summary_lists_them_and_each_becomes_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_REPORT), encoding="utf-8")
    summary_path = tmp_path / "summary.md"
    summary_path.write_text("earlier steps\n", encoding="utf-8")

    assert report.main(["--report", str(report_path), "--summary", str(summary_path)]) == 0

    summary = summary_path.read_text(encoding="utf-8")
    assert summary.startswith("earlier steps\n### Flaky browser tests")
    assert "- core-flows.spec.ts › Banding › saves rules [chromium]" in summary
    assert "2 browser test(s) passed only on a retry" in summary
    assert capsys.readouterr().out.count("::warning title=Flaky browser test::") == 2


@pytest.mark.parametrize("status", ["expected", "unexpected"])
def test_a_run_with_no_flaky_test_says_so_and_no_more(tmp_path: Path, status: str) -> None:
    # A test that failed outright is not flaky, and the summary claims nothing about it.
    runs = {"suites": [{"title": "a.spec.ts", "specs": [{"title": "t", "tests": [_test(status)]}]}]}
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(runs), encoding="utf-8")
    summary_path = tmp_path / "summary.md"

    assert report.main(["--report", str(report_path), "--summary", str(summary_path)]) == 0
    written = summary_path.read_text(encoding="utf-8")
    assert "None: no browser test passed only on a retry." in written
    assert "passed on its first run" not in written


def test_a_missing_report_fails_rather_than_reporting_nothing(tmp_path: Path) -> None:
    assert (
        report.main(
            ["--report", str(tmp_path / "absent.json"), "--summary", str(tmp_path / "summary.md")]
        )
        == 1
    )


def test_a_report_without_suites_is_refused() -> None:
    with pytest.raises(ValueError, match="Playwright's JSON report"):
        report.flaky_tests({"stats": {}})


@pytest.mark.meta
def test_ci_fails_a_main_push_on_a_flaky_test_and_reports_it_on_a_pull_request() -> None:
    config = (REPO_ROOT / "frontend" / "playwright.config.ts").read_text(encoding="utf-8")
    assert 'failOnFlakyTests: process.env.HAUTE_E2E_FAIL_ON_FLAKY === "1",' in config
    assert '["json", { outputFile: "test-results/report.json" }]' in config

    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = workflow["jobs"]["browser-e2e"]["steps"]
    e2e = next(step for step in steps if "npm run test:e2e" in step.get("run", ""))
    assert e2e["env"]["HAUTE_E2E_FAIL_ON_FLAKY"] == (
        "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && '1' || '0' }}"
    )
    summary = next(
        step for step in steps if "scripts/report_flaky_browser_tests.py" in step.get("run", "")
    )
    assert summary["if"] == "${{ !cancelled() }}"
    assert "--report frontend/test-results/report.json" in summary["run"]
    assert steps.index(summary) > steps.index(e2e)
