"""Contracts for the bounded mutation-suite runner."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from scripts import run_mutation_suite
from scripts.run_mutation_suite import (
    DEFAULT_TARGET_CONFIG,
    REPO_ROOT,
    MutationStageResult,
    _load_target_specs,
    _load_targets,
    _parse_survival_rate,
    _select_targets_for_changed_files,
    _write_summary_markdown,
    main,
)


def _targets():
    return _load_targets(
        [],
        target_config=REPO_ROOT / DEFAULT_TARGET_CONFIG,
        fail_over_override=None,
    )


def test_threshold_config_owns_all_default_mutation_targets() -> None:
    """The default target set pins every module guarded by the mutation gate.

    The v1 `json-flatten-schema` target was dropped alongside the v1 codec
    module; the multi-frame OUTPUT initiative then added the v2 targets —
    `output-assembler`, `jsonpath`, the `json-shred` v2 codec that replaces the
    former `json-per-port-shred` backlog item, the `json-cache` route over it,
    and the `executor` graph engine. Adding or removing a target must update
    this contract deliberately.
    """
    targets = _targets()

    assert {target.name for target in targets} == {
        "job-store",
        "path-resolution",
        "registry",
        "output-assembler",
        "jsonpath",
        "json-shred",
        "json-cache",
        "executor",
    }
    assert all(target.config_path.exists() for target in targets)
    assert all(target.module_path.exists() for target in targets)
    assert all(target.test_paths for target in targets)
    assert {target.name: target.fail_over for target in targets} == {
        "job-store": 6.0,
        "path-resolution": 5.0,
        "registry": 0.0,
        "output-assembler": 10.0,
        "jsonpath": 4.0,
        "json-shred": 5.0,
        "json-cache": 11.0,
        "executor": 15.0,
    }
    assert {target.name: target.max_pending_per_shard for target in targets} == {
        "job-store": 80,
        "path-resolution": 80,
        "registry": 80,
        "output-assembler": 80,
        "jsonpath": 80,
        "json-shred": 20,
        "json-cache": 80,
        "executor": 80,
    }
    for target in targets:
        config = tomllib.loads(target.config_path.read_text(encoding="utf-8"))["cosmic-ray"]
        assert config["test-command"].startswith("__HAUTE_PYTHON__ scripts/run_mutation_pytest.py ")
    json_shred = next(target for target in targets if target.name == "json-shred")
    assert REPO_ROOT / "tests" / "mutation" / "json_shred_targets.txt" in json_shred.test_paths
    assert REPO_ROOT / "tests" / "test_json_shred_parallel.py" in json_shred.test_paths
    assert REPO_ROOT / "tests" / "test_json_cache_integrity.py" in json_shred.test_paths
    json_cache = next(target for target in targets if target.name == "json-cache")
    assert REPO_ROOT / "tests" / "mutation" / "json_cache_targets.txt" in json_cache.test_paths
    assert REPO_ROOT / "tests" / "test_json_cache_routes.py" in json_cache.test_paths
    assert REPO_ROOT / "tests" / "test_json_cache_integrity.py" in json_cache.test_paths


def test_test_target_manifest_validation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_mutation_suite, "REPO_ROOT", tmp_path)

    def resolve_from_fixture(raw_path: str, *, base: Path = tmp_path) -> Path:
        path = Path(raw_path)
        return (path if path.is_absolute() else base / path).resolve()

    monkeypatch.setattr(run_mutation_suite, "_resolve_repo_path", resolve_from_fixture)
    manifest = tmp_path / "tests" / "mutation" / "targets.txt"
    manifest.parent.mkdir(parents=True)

    with pytest.raises(SystemExit, match="requires a path"):
        run_mutation_suite._extract_test_paths("pytest --test-targets-file")
    with pytest.raises(SystemExit, match="Cannot read repository test-targets file"):
        run_mutation_suite._extract_test_paths(
            "pytest --test-targets-file tests/mutation/missing.txt"
        )
    outside_manifest = tmp_path.parent / f"{tmp_path.name}-outside-targets.txt"
    outside_manifest.write_text("tests/test_one.py\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Cannot read repository test-targets file"):
        run_mutation_suite._extract_test_paths(
            f"pytest --test-targets-file ../{outside_manifest.name}"
        )
    with pytest.raises(SystemExit, match="Could not parse test-command"):
        run_mutation_suite._extract_test_paths('pytest "unterminated')

    for contents, message in (
        ("# comments only\n", "empty"),
        ("tests/test_one.py\ntests/test_one.py\n", "duplicate"),
        ("--disable-warnings\n", "non-test target"),
        ("tests/../pyproject.toml\n", "escapes the repository tests directory"),
    ):
        manifest.write_text(contents, encoding="utf-8")
        with pytest.raises(SystemExit, match=message):
            run_mutation_suite._extract_test_paths(
                "pytest --test-targets-file tests/mutation/targets.txt"
            )


def test_mutation_target_config_rejects_malformed_entries(tmp_path: Path) -> None:
    base = {
        "schema_version": 2,
        "targets": [
            {
                "name": "example",
                "config": "mutation/cosmic-ray.registry.toml",
                "max_survival_rate": 0.0,
                "rationale": "example",
                "max_pending_per_shard": 80,
            }
        ],
    }
    cases = (
        ("wrong-schema", {"schema_version": 1}, "schema_version 2"),
        ("missing-rationale", {"rationale": None}, "must define rationale"),
        ("bad-rate", {"max_survival_rate": 250}, "between 0 and 100"),
        ("missing-cap", {"max_pending_per_shard": None}, "positive integer"),
        ("bool-cap", {"max_pending_per_shard": True}, "positive integer"),
        ("float-cap", {"max_pending_per_shard": 80.0}, "positive integer"),
        ("zero-cap", {"max_pending_per_shard": 0}, "positive integer"),
        ("negative-cap", {"max_pending_per_shard": -1}, "positive integer"),
    )
    for name, change, expected in cases:
        payload = json.loads(json.dumps(base))
        for key, value in change.items():
            record = payload if key == "schema_version" else payload["targets"][0]
            if value is None:
                record.pop(key)
            else:
                record[key] = value
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            _load_target_specs(path)
        except SystemExit as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("expected malformed target config to fail closed")


def test_changed_file_selection_limits_pr_smoke_to_owned_target() -> None:
    selected = _select_targets_for_changed_files(
        _targets(),
        ["src/haute/_path_resolution.py"],
    )

    assert [target.name for target in selected] == ["path-resolution"]


def test_changed_manifest_selected_test_file_selects_json_shred_target() -> None:
    selected = _select_targets_for_changed_files(
        _targets(),
        ["tests/test_json_shred_parallel.py"],
    )

    assert [target.name for target in selected] == ["json-shred"]


def test_changed_file_selection_ignores_unowned_python_files() -> None:
    selected = _select_targets_for_changed_files(
        _targets(),
        ["tests/test_column_contracts_adoption.py", "src/haute/_rating.py"],
    )

    assert selected == []


def test_changed_runner_script_does_not_select_unrelated_targets() -> None:
    selected = _select_targets_for_changed_files(
        _targets(),
        ["scripts/run_mutation_suite.py"],
    )

    assert selected == []


def test_changed_mutation_pytest_runner_selects_every_target() -> None:
    targets = _targets()

    selected = _select_targets_for_changed_files(
        targets,
        ["scripts/run_mutation_pytest.py"],
    )

    assert selected == targets


def test_mutation_runner_dry_run_writes_manifest_without_cosmic_ray(tmp_path) -> None:
    exit_code = main(["--dry-run", "--output-dir", str(tmp_path), "--run-id", "dry"])

    assert exit_code == 0
    manifest = json.loads((tmp_path / "dry" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "full"
    assert {
        target["name"]: target["max_pending_per_shard"] for target in manifest["selected_targets"]
    }["json-shred"] == 20
    assert {target["name"] for target in manifest["selected_targets"]} == {
        "job-store",
        "path-resolution",
        "registry",
        "output-assembler",
        "jsonpath",
        "json-shred",
        "json-cache",
        "executor",
    }


def test_mutation_runner_skips_when_changed_files_select_no_target(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "no-target",
            "--changed-file",
            "docs/architecture.md",
        ]
    )

    manifest = json.loads((tmp_path / "no-target" / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads(
        (tmp_path / "no-target" / "mutation-summary.json").read_text(encoding="utf-8")
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert manifest["mode"] == "pr-smoke"
    assert manifest["selected_targets"] == []
    assert summary["status"] == "passed"
    assert summary["results"][0]["name"] == "target-selection"
    assert summary["results"][0]["status"] == "skipped"
    assert summary["results"][0]["failures"] == []
    assert "skipping mutation run" in output


def test_mutation_runner_dry_run_allows_changed_files_with_no_target(tmp_path) -> None:
    exit_code = main(
        [
            "--dry-run",
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "dry-no-target",
            "--changed-file",
            "docs/architecture.md",
        ]
    )

    manifest = json.loads(
        (tmp_path / "dry-no-target" / "manifest.json").read_text(encoding="utf-8")
    )

    assert exit_code == 0
    assert manifest["selected_targets"] == []


def test_parse_survival_rate_reads_cosmic_ray_rate_output(tmp_path: Path) -> None:
    rate = tmp_path / "rate.txt"
    rate.write_text("8.96 8.96 8.96\n", encoding="utf-8")

    assert _parse_survival_rate(rate) == 8.96


def test_summary_markdown_includes_all_target_failures(tmp_path: Path) -> None:
    summary = {
        "run_id": "mutation-run",
        "results": [
            {
                "name": "path-resolution",
                "status": "failed",
                "survival_rate": 6.5,
                "fail_over": 5.0,
                "failures": ["survival rate 6.50% exceeds threshold 5.00%"],
                "stages": [
                    MutationStageResult(
                        "rate",
                        0,
                        "rate.txt",
                        "rate.stderr.txt",
                    ).__dict__
                ],
            }
        ],
    }

    _write_summary_markdown(summary, tmp_path / "mutation-summary.md")

    markdown = (tmp_path / "mutation-summary.md").read_text(encoding="utf-8")
    assert "`path-resolution`" in markdown
    assert "6.50%" in markdown
    assert "survival rate 6.50% exceeds threshold 5.00%" in markdown
