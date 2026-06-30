"""Regression coverage for the frontend initial bundle-budget CI gate."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(_read(REPO_ROOT / ".github" / "workflows" / "ci.yml"))
    assert isinstance(workflow, dict)
    if True in workflow and "on" not in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def _run_steps(job: dict[str, Any]) -> list[str]:
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return [step["run"] for step in steps if isinstance(step, dict) and "run" in step]


def _run_lines(job: dict[str, Any]) -> list[str]:
    return [
        line.strip()
        for step in _run_steps(job)
        for line in step.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _uses_frontend_preflight(run_lines: list[str]) -> bool:
    preflight = re.compile(r"\bbash\s+scripts/preflight\.sh\b.*\s--frontend-only\b")
    return any(preflight.search(line) for line in run_lines)


def _runs_bundle_budget_after_build(run_lines: list[str]) -> bool:
    build_index = next(
        (index for index, line in enumerate(run_lines) if "npm run build" in line),
        None,
    )
    budget_index = next(
        (index for index, line in enumerate(run_lines) if "npm run check:bundle" in line),
        None,
    )
    return build_index is not None and budget_index is not None and build_index < budget_index


def _find_command_index(script: str, command: str) -> int:
    normalized_lines = [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    try:
        return normalized_lines.index(command)
    except ValueError as exc:
        raise AssertionError(f"Expected preflight command missing: {command}") from exc


def test_frontend_ci_runs_on_main_pushes_and_pull_requests() -> None:
    workflow = _workflow()

    triggers = workflow["on"]

    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]


def test_frontend_ci_runs_bundle_gate_through_preflight_or_direct_commands() -> None:
    workflow = _workflow()
    frontend_job = workflow["jobs"]["frontend"]

    run_lines = _run_lines(frontend_job)

    assert _uses_frontend_preflight(run_lines) or _runs_bundle_budget_after_build(run_lines)


def test_preflight_builds_frontend_before_checking_bundle_budget() -> None:
    preflight = _read(REPO_ROOT / "scripts" / "preflight.sh")

    build_index = _find_command_index(preflight, "if (cd frontend && npm run build); then")
    budget_index = _find_command_index(preflight, "if (cd frontend && npm run check:bundle); then")

    assert build_index < budget_index


def test_frontend_pr_benchmark_gate_is_explicit_and_cheap() -> None:
    package_json = json.loads(_read(REPO_ROOT / "frontend" / "package.json"))

    benchmark_gate = package_json["scripts"]["test:benchmark:pr"]
    expected_tests = [
        "src/__tests__/App.shallowHash.test.ts",
        "src/hooks/__tests__/columnsEqual.fingerprint.test.ts",
        "src/hooks/__tests__/nodesWithStatus.memo.test.tsx",
        "src/utils/__tests__/graphPerformance.test.ts",
    ]

    assert re.search(r"(^|&&)\s*vitest run\s", benchmark_gate)
    for test_path in expected_tests:
        assert test_path in benchmark_gate
    assert "playwright" not in benchmark_gate.lower()
    assert "e2e" not in benchmark_gate.lower()
    assert "@benchmark" not in benchmark_gate


def test_preflight_runs_frontend_pr_benchmark_gate_between_bundle_and_coverage() -> None:
    bash_preflight = _read(REPO_ROOT / "scripts" / "preflight.sh")
    powershell_preflight = _read(REPO_ROOT / "scripts" / "preflight.ps1")

    bash_build_index = _find_command_index(
        bash_preflight,
        "if (cd frontend && npm run build); then",
    )
    bash_budget_index = _find_command_index(
        bash_preflight,
        "if (cd frontend && npm run check:bundle); then",
    )
    bash_gate_index = _find_command_index(
        bash_preflight,
        "if (cd frontend && npm run test:benchmark:pr); then",
    )
    bash_coverage_index = _find_command_index(
        bash_preflight,
        "if (cd frontend && npm run test:coverage); then",
    )

    assert bash_build_index < bash_budget_index < bash_gate_index < bash_coverage_index

    powershell_build_index = _find_command_index(powershell_preflight, "& npm run build")
    powershell_budget_index = _find_command_index(
        powershell_preflight,
        "& npm run check:bundle",
    )
    powershell_gate_index = _find_command_index(
        powershell_preflight,
        "& npm run test:benchmark:pr",
    )
    powershell_coverage_index = _find_command_index(
        powershell_preflight,
        "& npm run test:coverage",
    )

    assert (
        powershell_build_index
        < powershell_budget_index
        < powershell_gate_index
        < powershell_coverage_index
    )


def test_frontend_check_bundle_script_runs_bundle_and_dependency_checkers() -> None:
    package_json = json.loads(_read(REPO_ROOT / "frontend" / "package.json"))

    check_bundle = package_json["scripts"]["check:bundle"]

    assert re.search(r"(^|&&)\s*node scripts/check-bundle-size\.mjs(\s|&&|$)", check_bundle)
    assert re.search(r"(^|&&)\s*node scripts/check-ui-dependencies\.mjs(\s|&&|$)", check_bundle)

    checker = _read(REPO_ROOT / "frontend" / "scripts" / "check-bundle-size.mjs")
    assert "DEFAULT_MAX_INITIAL_JS_GZIP_KIB" in checker
    assert "parseInitialJsAssetNames" in checker
    assert "maxInitialJsGzipKiB" in checker

    dependency_checker = _read(REPO_ROOT / "frontend" / "scripts" / "check-ui-dependencies.mjs")
    assert "lucide-react" in dependency_checker
    assert "vendor-ui" in dependency_checker


def test_frontend_analyze_bundle_script_runs_sourcemap_analyzer_after_sourcemap_build() -> None:
    package_json = json.loads(_read(REPO_ROOT / "frontend" / "package.json"))

    analyze_bundle = package_json["scripts"]["analyze:bundle"]

    build_match = re.search(r"\bvite build --sourcemap\b", analyze_bundle)
    analyzer_match = re.search(r"\bnode scripts/analyze-bundle-sourcemaps\.mjs\b", analyze_bundle)
    assert build_match is not None
    assert analyzer_match is not None
    assert build_match.start() < analyzer_match.start()
