from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "PERFORMANCE_CHECKS.md"
PERFORMANCE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "performance.yml"
RUN_PERF_SUITE_PATH = ROOT / "scripts" / "run_perf_suite.py"


def _read_doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _runner_help() -> str:
    completed = subprocess.run(
        [sys.executable, str(RUN_PERF_SUITE_PATH), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _runner_long_options() -> set[str]:
    return set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", _runner_help())) - {"--help"}


def _non_comment_run_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_local_performance_docs_cover_python_perf_suite_contract() -> None:
    doc = _read_doc()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]
    runner_options = _runner_long_options()

    assert "pytest.mark.perf" in doc
    assert addopts in doc
    assert "uv run python scripts/run_perf_suite.py" in doc
    for option in sorted(runner_options):
        assert option in doc
    focused_command = (
        "uv run python scripts/run_perf_suite.py "
        "--pytest-target tests/performance/test_preview_trace_perf.py "
        "--max-total-seconds 120 --max-test-seconds 30"
    )
    assert focused_command in doc
    assert ".cache/perf/perf-report.json" in doc
    assert ".cache/perf/perf-report.md" in doc
    assert ".cache/perf/perf-junit.xml" in doc
    assert "cached target preview: `< 0.5s`" in doc
    assert "first trace backed by a full preview cache: `< 0.8s`" in doc
    assert "trace-cache hit: `< 0.3s`" in doc
    assert "Generated scale fixtures are never committed." in doc


def test_local_performance_docs_describe_scheduled_polars_scale_workflow() -> None:
    doc = _read_doc()

    assert "ordinary pull-request suite excludes `pytest.mark.perf`" in doc
    assert "one-million-row scale weekly" in doc
    assert "ten-million-row stress scale monthly" in doc
    assert "--polars-scale 1m" in doc
    assert "--polars-scale 10m" in doc
    assert "Generated scale fixtures are never committed." in doc
    assert "semantic evidence and product metrics" in doc
    assert "independent runner RSS baseline" in doc
    assert "Numeric thresholds are calibrated only against a baseline" in doc


def test_local_performance_docs_cover_frontend_benchmark_and_bundle_commands() -> None:
    doc = _read_doc()
    package_json = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))

    assert "npm run test:e2e:benchmark" in doc
    assert package_json["scripts"]["test:e2e:benchmark"] in doc
    assert package_json["scripts"]["test:e2e"] in doc
    assert "@benchmark" in doc
    assert "npm run build" in doc
    assert "npm run check:bundle" in doc

    benchmark_specs = sorted(
        path.name for path in (ROOT / "frontend" / "e2e").glob("*.benchmark.spec.ts")
    )
    assert benchmark_specs
    for spec in benchmark_specs:
        assert spec in doc


def test_local_performance_docs_cover_memory_smoke_command() -> None:
    doc = _read_doc()

    assert "uv run python scripts/memory_smoke.py --" in doc
    assert "JSON to stdout" in doc


def test_performance_workflow_runs_heavy_frontend_perf_lanes_outside_pr_ci() -> None:
    workflow = "\n".join(_non_comment_run_lines(PERFORMANCE_WORKFLOW_PATH))

    assert "npm run analyze:bundle" in workflow
    assert "npm run test:e2e:benchmark" in workflow
    assert "./node_modules/.bin/playwright install --with-deps chromium" in workflow
    assert "frontend-performance-${{ github.run_id }}" in workflow


def test_performance_workflow_certifies_scales_and_execution_platforms() -> None:
    workflow = "\n".join(_non_comment_run_lines(PERFORMANCE_WORKFLOW_PATH))

    assert 'cron: "17 3 * * 1"' in workflow
    assert 'cron: "43 2 1 * *"' in workflow
    assert "options: [ci, 1m, 10m]" in workflow
    assert '--polars-scale "$HAUTE_POLARS_PERF_SCALE"' in workflow
    assert "actions/cache/restore@v4" in workflow
    assert "actions/cache/save@v4" in workflow
    assert "--baseline-report .perf-baseline/perf-report.json" in workflow
    assert "perf-baseline-${{ runner.os }}-${{ env.HAUTE_POLARS_PERF_SCALE }}-" in workflow
    assert "actions: read" in workflow
    assert (
        'gh run list --workflow performance.yml --branch "$GITHUB_REF_NAME" --status success'
        in workflow
    )
    assert "--limit 100 --jq '.[].databaseId'" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert 'for prior_run_id in "${prior_run_ids[@]}"' in workflow
    assert (
        'gh run download "$prior_run_id" --name "python-performance-$HAUTE_POLARS_PERF_SCALE"'
        in workflow
    )
    assert "name: python-performance-${{ env.HAUTE_POLARS_PERF_SCALE }}" in workflow
    assert "os: [ubuntu-latest, windows-latest, macos-latest]" in workflow
    for test_path in (
        "tests/test_process_memory.py",
        "tests/test_native_memory_limit.py",
        "tests/test_worker_isolation.py",
        "tests/test_interactive_worker_pool.py",
        "tests/test_interactive_route_isolation.py",
        "tests/test_json_direct_spill.py",
        "tests/test_json_cache_cross_process.py",
        "tests/test_json_runtime_storage.py",
        "test_fresh_process_restart_reuses_cache_proof_and_safe_telemetry",
    ):
        assert test_path in workflow


def test_preview_trace_perf_suite_uses_supersession_snapshot_contract() -> None:
    perf_test = (ROOT / "tests" / "performance" / "test_preview_trace_perf.py").read_text(
        encoding="utf-8"
    )

    assert "snapshot_for_tests()" in perf_test
    assert "._states" not in perf_test


def test_local_performance_docs_are_linked_from_contributor_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    commit_standards = (ROOT / "docs" / "COMMIT_STANDARDS.md").read_text(encoding="utf-8")

    assert "[Local Performance Checks](docs/PERFORMANCE_CHECKS.md)" in readme
    assert "[Local Performance Checks](PERFORMANCE_CHECKS.md)" in commit_standards
