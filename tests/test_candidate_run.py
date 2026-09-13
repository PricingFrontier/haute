"""The candidate-run contract every logged haute training run follows."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from haute.errors import HauteValidationError
from haute.modelling._candidate_run import (
    CANDIDATE_RUN_CONTRACT_VERSION,
    CandidateArtifacts,
    CandidateProvenance,
    build_candidate_run,
    capture_provenance,
    training_identity_sha256,
)
from haute.modelling._feature_contract import build_contract, save_contract
from haute.modelling._result_types import ModelDiagnostics

_TRAINED_AT = datetime(2026, 9, 13, 8, 30, 45, tzinfo=UTC)


def _artifacts(
    directory: Path, *, feature_types: dict[str, str] | None = None
) -> CandidateArtifacts:
    model = directory / "freq.cbm"
    model.write_bytes(b"model")
    contract = directory / "freq.feature_contract.json"
    save_contract(
        build_contract(
            features=["age", "region"],
            feature_types=(
                {"age": "Float64", "region": "String"} if feature_types is None else feature_types
            ),
            categorical_features=["region"],
            target_name="claims",
            target_type="Float64",
            task="regression",
        ),
        contract,
    )
    evidence = {}
    for kind in ("evaluation_plan", "evaluation_results", "evaluation_report"):
        (directory / f"freq.{kind}.json").write_text("{}", encoding="utf-8")
        evidence[kind] = directory / f"freq.{kind}.json"
    return CandidateArtifacts(model=model, feature_contract=contract, evidence=evidence)


def _provenance(**overrides: object) -> CandidateProvenance:
    values: dict[str, object] = {
        "job_id": "job-7",
        "node_label": "Frequency",
        "trained_at": _TRAINED_AT,
        "training_identity_sha256": "c" * 64,
        "node_id": "node_freq",
        "pipeline": "rating/main.py",
        "git_commit": "0123abcd",
        "git_dirty": True,
    }
    values.update(overrides)
    return CandidateProvenance(**values)  # type: ignore[arg-type]


def _build(tmp_path: Path, **overrides: object):
    values: dict[str, object] = {
        "provenance": _provenance(),
        "algorithm": "catboost",
        "weight": "exposure",
        "evaluation_strategy": "random",
        "validation_method": "cross_validation",
        "evaluation_config": {"strategy": "random"},
        "evaluation_plan_sha256": "e" * 64,
        "final_params": {"depth": 4, "iterations": 200},
        "final_test_metrics": {"gini": 0.41, "rmse": 1.2},
        "development_metrics": {},
        "diagnostics": ModelDiagnostics(
            selection_metrics={"gini": {"mean": 0.4, "stddev": 0.02, "min": 0.37, "max": 0.43}},
            glm_fit_statistics={"aic": 10.0, "deviance": 4.0, "unrelated": 1.0},
        ),
        "development_rows": 800,
        "final_test_rows": 200,
        "best_iteration": 150,
    }
    values.update(overrides)
    if "artifacts" not in values:
        values["artifacts"] = _artifacts(tmp_path)
    return build_candidate_run(**values)  # type: ignore[arg-type]


class TestBuildCandidateRun:
    def test_run_name_is_the_label_and_minute_resolution_utc_time(self, tmp_path: Path) -> None:
        local = _TRAINED_AT.astimezone(timezone(timedelta(hours=5)))
        run = _build(tmp_path, provenance=_provenance(trained_at=local))
        assert run.run_name == "Frequency · 2026-09-13 08:30 UTC"

    def test_tags_carry_the_contract_and_provenance(self, tmp_path: Path) -> None:
        from haute import __version__

        run = _build(tmp_path)
        assert run.tags == {
            "haute.contract_version": CANDIDATE_RUN_CONTRACT_VERSION,
            "haute.job_id": "job-7",
            "haute.node_label": "Frequency",
            "haute.trained_at": "2026-09-13T08:30:45+00:00",
            "haute.version": __version__,
            "haute.algorithm": "catboost",
            "haute.task": "regression",
            "haute.target": "claims",
            "haute.evaluation_plan_sha256": "e" * 64,
            "haute.training_identity_sha256": "c" * 64,
            "haute.node_id": "node_freq",
            "haute.pipeline": "rating/main.py",
            "haute.git_commit": "0123abcd",
            "haute.git_dirty": "true",
        }

    def test_unknown_provenance_is_omitted_not_blank(self, tmp_path: Path) -> None:
        run = _build(
            tmp_path,
            provenance=_provenance(node_id=None, pipeline=None, git_commit=None, git_dirty=None),
        )
        for key in ("haute.node_id", "haute.pipeline", "haute.git_commit", "haute.git_dirty"):
            assert key not in run.tags

    def test_metrics_are_namespaced_by_the_data_they_were_measured_on(self, tmp_path: Path) -> None:
        run = _build(tmp_path)
        assert run.metrics == {
            "final_test_gini": 0.41,
            "final_test_rmse": 1.2,
            "selection_gini_mean": 0.4,
            "selection_gini_stddev": 0.02,
            "selection_gini_min": 0.37,
            "selection_gini_max": 0.43,
            "glm_aic": 10.0,
            "glm_deviance": 4.0,
        }

    def test_development_metrics_are_namespaced_when_no_final_test_exists(
        self, tmp_path: Path
    ) -> None:
        run = _build(
            tmp_path,
            final_test_metrics={},
            development_metrics={"gini": 0.5},
            diagnostics=ModelDiagnostics(),
        )
        assert run.metrics == {"development_gini": 0.5}

    def test_final_test_and_development_metrics_never_mix(self, tmp_path: Path) -> None:
        with pytest.raises(HauteValidationError, match="final test"):
            _build(tmp_path, development_metrics={"gini": 0.5})

    def test_tuning_summary_becomes_tuning_metrics_and_params(self, tmp_path: Path) -> None:
        tuning = {
            "metric": "gini",
            "direction": "maximize",
            "baseline_objective": 0.3,
            "winner_objective": 0.4,
            "improvement": 0.1,
            "winner_trial_index": 3,
            "trial_count": 8,
            "total_fit_count": 24,
            "final_tree_count": 180,
        }
        run = _build(tmp_path, diagnostics=ModelDiagnostics(tuning=tuning))
        assert run.metrics["tuning_baseline_gini"] == 0.3
        assert run.metrics["tuning_winner_gini"] == 0.4
        assert run.metrics["tuning_improvement_gini"] == pytest.approx(0.1)
        assert run.params["tuning_direction"] == "maximize"
        assert run.params["tuning_trial_count"] == 8

    def test_incomplete_tuning_summary_is_rejected(self, tmp_path: Path) -> None:
        tuning = {"metric": "gini", "baseline_objective": 0.3, "winner_objective": 0.4}
        with pytest.raises(HauteValidationError, match="tuning summary is missing direction"):
            _build(tmp_path, diagnostics=ModelDiagnostics(tuning={**tuning, "improvement": 0.1}))

    def test_non_finite_metric_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(HauteValidationError, match="final_test_gini must be a finite"):
            _build(tmp_path, final_test_metrics={"gini": float("nan")})

    def test_params_describe_the_trained_model(self, tmp_path: Path) -> None:
        run = _build(tmp_path)
        assert run.params == {
            "algorithm": "catboost",
            "task": "regression",
            "target": "claims",
            "weight": "exposure",
            "evaluation_strategy": "random",
            "validation_method": "cross_validation",
            "param_depth": 4,
            "param_iterations": 200,
            "development_rows": 800,
            "final_test_rows": 200,
            "n_features": 2,
            "best_iteration": 150,
        }

    def test_metadata_comes_from_the_feature_contract(self, tmp_path: Path) -> None:
        run = _build(tmp_path)
        assert run.metadata.features == ["age", "region"]
        assert run.metadata.feature_types == {"age": "Float64", "region": "String"}
        assert run.metadata.categorical_features == ["region"]
        assert run.metadata.task == "regression"
        assert run.metadata.target_name == "claims"

    def test_a_missing_artifact_is_named(self, tmp_path: Path) -> None:
        artifacts = _artifacts(tmp_path)
        artifacts.evidence["evaluation_report"].unlink()
        with pytest.raises(HauteValidationError, match="evaluation_report"):
            _build(tmp_path, artifacts=artifacts)

    def test_a_contract_without_complete_feature_types_is_rejected(self, tmp_path: Path) -> None:
        artifacts = _artifacts(tmp_path, feature_types={"age": "Float64"})
        with pytest.raises(HauteValidationError, match="Missing types: region"):
            _build(tmp_path, artifacts=artifacts)


class TestTrainingIdentity:
    def test_identity_ignores_keys_that_do_not_change_what_is_trained(self) -> None:
        base = {"target": "y", "algorithm": "catboost", "params": {"depth": 4}}
        assert training_identity_sha256(base) == training_identity_sha256(
            {**base, "name": "other", "output_dir": "elsewhere", "mlflow_experiment": "/x"}
        )

    def test_identity_is_order_independent_and_changes_with_training_inputs(self) -> None:
        first = training_identity_sha256({"params": {"depth": 4, "iterations": 10}})
        reordered = training_identity_sha256({"params": {"iterations": 10, "depth": 4}})
        changed = training_identity_sha256({"params": {"depth": 5, "iterations": 10}})
        assert first == reordered
        assert first != changed
        assert len(first) == 64

    def test_identity_rejects_values_without_a_canonical_form(self) -> None:
        with pytest.raises(TypeError, match="no canonical JSON form"):
            training_identity_sha256({"params": {"depth": object()}})


class TestProvenance:
    def test_round_trips_through_plain_data(self) -> None:
        provenance = _provenance()
        assert CandidateProvenance.from_plain_data(provenance.to_plain_data()) == provenance

    def test_trained_at_must_be_timezone_aware(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _provenance(trained_at=datetime(2026, 9, 13, 8, 30))

    def test_pipeline_is_recorded_relative_to_the_project_only(self, tmp_path: Path) -> None:
        inside = capture_provenance(
            job_id="j",
            node_label="n",
            training_identity_sha256="a" * 64,
            project_root=tmp_path,
            pipeline_source=str(tmp_path / "rating" / "main.py"),
        )
        (tmp_path / "project").mkdir()
        outside = capture_provenance(
            job_id="j",
            node_label="n",
            training_identity_sha256="a" * 64,
            project_root=tmp_path / "project",
            pipeline_source=str(tmp_path / "elsewhere.py"),
        )
        assert inside.pipeline == "rating/main.py"
        assert outside.pipeline is None
        assert inside.trained_at.tzinfo is not None

    def test_git_state_is_unknown_outside_a_repository(self, tmp_path: Path) -> None:
        provenance = capture_provenance(
            job_id="j", node_label="n", training_identity_sha256="a" * 64, project_root=tmp_path
        )
        assert (provenance.git_commit, provenance.git_dirty) == (None, None)

    def test_git_state_records_the_commit_and_uncommitted_changes(self, tmp_path: Path) -> None:
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "-q")
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        git("add", "main.py")
        git("commit", "-q", "-m", "init")
        head = git("rev-parse", "HEAD")

        def capture() -> CandidateProvenance:
            return capture_provenance(
                job_id="j", node_label="n", training_identity_sha256="a" * 64, project_root=tmp_path
            )

        clean = capture()
        (tmp_path / "main.py").write_text("x = 2\n", encoding="utf-8")
        dirty = capture()
        assert (clean.git_commit, clean.git_dirty) == (head, False)
        assert (dirty.git_commit, dirty.git_dirty) == (head, True)
