from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_mutation_suite


def test_load_targets_uses_target_file_and_deterministic_names() -> None:
    targets = run_mutation_suite._load_targets(
        [],
        target_config=run_mutation_suite.REPO_ROOT / "mutation" / "targets.json",
        fail_over_override=None,
    )

    assert [target.name for target in targets] == [
        "json-flatten-schema",
        "job-store",
        "path-resolution",
        "registry",
    ]
    assert {target.name: target.fail_over for target in targets} == {
        "json-flatten-schema": 10.0,
        "job-store": 6.0,
        "path-resolution": 5.0,
        "registry": 0.0,
    }
    assert (
        targets[0].module_path
        == (run_mutation_suite.REPO_ROOT / "src" / "haute" / "_json_flatten_schema.py").resolve()
    )
    json_flatten_tests = (
        run_mutation_suite.REPO_ROOT / "tests" / "test_json_flatten_schema_contracts.py"
    ).resolve()
    assert targets[0].test_paths == (json_flatten_tests,)


def test_changed_file_selection_picks_matching_module_and_tests() -> None:
    targets = run_mutation_suite._load_targets(
        [],
        target_config=run_mutation_suite.REPO_ROOT / "mutation" / "targets.json",
        fail_over_override=None,
    )

    module_selection = run_mutation_suite._select_targets_for_changed_files(
        targets,
        ["src/haute/routes/_job_store.py"],
    )
    test_selection = run_mutation_suite._select_targets_for_changed_files(
        targets,
        ["tests/test_path_resolution_properties.py"],
    )

    assert [target.name for target in module_selection] == ["job-store"]
    assert [target.name for target in test_selection] == ["path-resolution"]


def test_changed_mutation_gate_file_selects_all_targets() -> None:
    targets = run_mutation_suite._load_targets(
        [],
        target_config=run_mutation_suite.REPO_ROOT / "mutation" / "targets.json",
        fail_over_override=None,
    )

    selected = run_mutation_suite._select_targets_for_changed_files(
        targets,
        ["mutation/targets.json"],
    )

    assert selected == targets


def test_unowned_backend_python_change_selects_all_targets() -> None:
    targets = run_mutation_suite._load_targets(
        [],
        target_config=run_mutation_suite.REPO_ROOT / "mutation" / "targets.json",
        fail_over_override=None,
    )

    selected = run_mutation_suite._select_targets_for_changed_files(
        targets,
        ["src/haute/parser.py"],
    )

    assert selected == targets


def test_unowned_test_python_change_selects_all_targets() -> None:
    targets = run_mutation_suite._load_targets(
        [],
        target_config=run_mutation_suite.REPO_ROOT / "mutation" / "targets.json",
        fail_over_override=None,
    )

    selected = run_mutation_suite._select_targets_for_changed_files(
        targets,
        ["tests/test_api_contracts.py"],
    )

    assert selected == targets


def test_mixed_owned_and_unowned_backend_python_change_selects_all_targets() -> None:
    targets = run_mutation_suite._load_targets(
        [],
        target_config=run_mutation_suite.REPO_ROOT / "mutation" / "targets.json",
        fail_over_override=None,
    )

    selected = run_mutation_suite._select_targets_for_changed_files(
        targets,
        ["src/haute/routes/_job_store.py", "src/haute/parser.py"],
    )

    assert selected == targets


def test_irrelevant_changed_files_select_no_targets() -> None:
    targets = run_mutation_suite._load_targets(
        [],
        target_config=run_mutation_suite.REPO_ROOT / "mutation" / "targets.json",
        fail_over_override=None,
    )

    selected = run_mutation_suite._select_targets_for_changed_files(
        targets,
        ["README.md", "frontend/src/App.tsx"],
    )

    assert selected == []


def test_fail_over_override_cannot_loosen_checked_in_threshold() -> None:
    with pytest.raises(SystemExit, match="would loosen"):
        run_mutation_suite._load_targets(
            [],
            target_config=run_mutation_suite.REPO_ROOT / "mutation" / "targets.json",
            fail_over_override=99.0,
        )


def test_main_dry_run_writes_manifest_without_running_cosmic_ray(tmp_path: Path, capsys) -> None:
    exit_code = run_mutation_suite.main(
        [
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "dry-run",
            "--changed-file",
            "src/haute/_path_resolution.py",
            "--dry-run",
        ]
    )

    manifest = json.loads((tmp_path / "dry-run" / "manifest.json").read_text(encoding="utf-8"))
    output = capsys.readouterr().out

    assert exit_code == 0
    assert manifest["mode"] == "pr-smoke"
    assert manifest["dry_run"] is True
    assert [target["name"] for target in manifest["selected_targets"]] == ["path-resolution"]
    assert "path-resolution" in output
    assert "job-store" not in output


def test_main_non_dry_run_skips_irrelevant_changed_files(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_run(*_args: object, **_kwargs: object) -> dict[str, object]:
        pytest.fail("irrelevant changed files should not run mutation targets")

    monkeypatch.setattr(run_mutation_suite, "_run_target", fail_if_run)

    exit_code = run_mutation_suite.main(
        [
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "irrelevant-skip",
            "--changed-file",
            "README.md",
        ]
    )

    summary = json.loads(
        (tmp_path / "irrelevant-skip" / "mutation-summary.json").read_text(encoding="utf-8")
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert summary["status"] == "passed"
    assert summary["results"] == [
        {
            "fail_over": None,
            "failures": [],
            "name": "target-selection",
            "stages": [],
            "status": "skipped",
            "survival_rate": None,
        }
    ]
    assert "skipping mutation run" in output


def test_main_non_dry_run_prints_target_failure_summary(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_target(
        target: run_mutation_suite.MutationTarget, _output_dir: Path
    ) -> dict[str, object]:
        return {
            "name": target.name,
            "config": "mutation/cosmic-ray.path-resolution.toml",
            "status": "failed",
            "fail_over": 5.0,
            "survival_rate": None,
            "failures": ["baseline exited with code 1"],
            "stages": [
                {
                    "stage": "baseline",
                    "returncode": 1,
                    "stdout": "baseline.stdout.txt",
                    "stderr": "baseline.stderr.txt",
                }
            ],
        }

    monkeypatch.setattr(run_mutation_suite, "_run_target", fake_run_target)

    exit_code = run_mutation_suite.main(
        [
            "--output-dir",
            str(tmp_path),
            "--run-id",
            "failed-run",
            "--changed-file",
            "src/haute/_path_resolution.py",
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "[mutation] path-resolution failed" in output
    assert "survival=n/a threshold=5.00%" in output
    assert "baseline exited with code 1" in output
    assert "stage baseline exited with 1" in output
    assert "stderr=baseline.stderr.txt" in output


def test_print_result_summary_includes_failure_stage_artifacts(capsys) -> None:
    run_mutation_suite._print_result_summary(
        [
            {
                "name": "json-flatten-schema",
                "status": "failed",
                "fail_over": 10.0,
                "survival_rate": 12.5,
                "failures": ["survival rate 12.50% exceeds threshold 10.00%"],
                "stages": [
                    {
                        "stage": "rate",
                        "returncode": 1,
                        "stdout": "rate.txt",
                        "stderr": "rate.stderr.txt",
                    },
                    {
                        "stage": "baseline",
                        "returncode": 0,
                        "stdout": "baseline.stdout.txt",
                        "stderr": "baseline.stderr.txt",
                    },
                ],
            }
        ]
    )

    output = capsys.readouterr().out

    assert "[mutation] json-flatten-schema failed survival=12.50% threshold=10.00%" in output
    assert "survival rate 12.50% exceeds threshold 10.00%" in output
    assert "stage rate exited with 1" in output
    assert "stdout=rate.txt stderr=rate.stderr.txt" in output
    assert "stage baseline" not in output
