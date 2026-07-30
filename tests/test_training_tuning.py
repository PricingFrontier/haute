"""Bounded tuning reuses one evaluation plan and publishes one final model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._execution_context import ExecutionCancelledError
from haute.errors import BoundedMemoryUnsupportedError
from haute.modelling._evaluation import EvaluationFitResult, EvaluationPlan
from haute.modelling._training_job import (
    TrainingJob,
    TrainResult,
    _PreparedData,
    tuning_artifact_filenames,
)
from haute.modelling._tuning import (
    load_tuning_plan,
    load_tuning_report,
    load_tuning_trials,
)

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 11,
    "test": {"size": 0.2},
    "validation": {"method": "cross_validation", "fold_count": 2},
}

TUNING = {
    "schema_version": 1,
    "trial_count": 5,
    "seed": 19,
    "metric": "gini",
    "search_space": {
        "depth": [4, 5, 6],
    },
}


def test_tuning_requires_an_explicit_evaluation_contract() -> None:
    with pytest.raises(ValueError, match="tuning requires an explicit evaluation"):
        TrainingJob(
            name="missing-evaluation",
            data=pl.DataFrame({"y": [0, 1], "feature": [1, 2]}),
            target="y",
            metrics=["gini", "rmse"],
            tuning=TUNING,
        )


def completed_final(
    tmp_path: Path,
    *,
    development_rows: int,
    final_test_rows: int,
) -> TrainResult:
    return TrainResult(
        metrics={"gini": 0.6, "rmse": 1.0},
        feature_importance=[],
        model_path=str(tmp_path / "model.cbm"),
        train_rows=development_rows,
        validation_rows=0,
        holdout_rows=final_test_rows,
        holdout_metrics={"gini": 0.6, "rmse": 1.0},
        diagnostics_set="holdout",
        features=["feature"],
        cat_features=[],
    )


def test_tuning_runs_baseline_and_seeded_trials_then_one_selected_final_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(20)), "feature": list(range(20))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        algorithm="catboost",
        params={"iterations": 100, "depth": 4, "metadata": {"owner": "pricing"}},
        metrics=["gini", "rmse"],
        output_dir=str(tmp_path),
        evaluation=EVALUATION,
        tuning=TUNING,
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 20)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)
    selection_calls: list[tuple[int, int, dict[str, Any]]] = []
    final_calls: list[dict[str, Any]] = []
    iteration_events: list[int] = []
    tuning_progress: list[dict[str, Any]] = []

    class FakeJob:
        def __init__(
            self,
            *,
            fit_index: int | None,
            plan: EvaluationPlan,
            params: dict[str, Any],
        ) -> None:
            self.fit_index = fit_index
            self.plan = plan
            self.params = params

        def run_evaluation_fit(self, **_kwargs: Any) -> EvaluationFitResult:
            assert self.fit_index is not None
            depth = int(self.params["depth"])
            trial_index = len(selection_calls) // 2
            selection_calls.append((trial_index, self.fit_index, dict(self.params)))
            fit = self.plan.validation_fits[self.fit_index]
            return EvaluationFitResult(
                schema_version=1,
                fit_index=self.fit_index,
                train_rows=fit.train_rows,
                validation_rows=fit.validation_rows,
                metrics={"gini": depth / 10, "rmse": float(10 - depth)},
                best_iteration=9 + self.fit_index,
            )

        def run(self, *, on_iteration=None, **_kwargs: Any) -> TrainResult:
            final_calls.append(dict(self.params))
            if on_iteration is not None:
                on_iteration(1, 1, {"loss": 1.0})
            return completed_final(
                tmp_path,
                development_rows=len(self.plan.development_positions),
                final_test_rows=len(self.plan.test_positions),
            )

    monkeypatch.setattr(
        job,
        "_new_evaluation_job",
        lambda **kwargs: FakeJob(
            fit_index=kwargs["fit_index"],
            plan=kwargs["plan"],
            params=kwargs["params"],
        ),
    )
    result = job.run(
        on_iteration=lambda iteration, *_: iteration_events.append(iteration),
        on_tuning_progress=tuning_progress.append,
    )

    assert len(selection_calls) == 10
    assert [call[:2] for call in selection_calls] == [
        (trial, fold) for trial in range(5) for fold in range(2)
    ]
    assert len(final_calls) == 1
    assert iteration_events == [1]
    assert final_calls[0]["metadata"] == {"owner": "pricing"}
    assert final_calls[0]["iterations"] == 10
    assert result.tuning is not None
    assert result.tuning["trial_count"] == 5
    assert result.tuning["trial_fit_count"] == 10
    assert result.tuning["total_fit_count"] == 11
    assert result.tuning["winner_objective"] >= result.tuning["baseline_objective"]
    assert result.tuning["final_params"] == final_calls[0]
    assert result.evaluation is not None
    assert result.evaluation["plan_sha256"] == result.tuning["evaluation_plan_sha256"]

    completed = [event["completed_fits"] for event in tuning_progress]
    assert completed == sorted(completed)
    assert completed[-1] == 11
    assert all(event["total_fits"] == 11 for event in tuning_progress)
    # The training child has prepared the staged publication set; the parent
    # service emits "completed" only after transactional publication commits.
    assert tuning_progress[-1]["phase"] == "publication"

    plan = load_tuning_plan(result.tuning["plan_path"])
    trials = load_tuning_trials(
        result.tuning["trials_path"],
        plan_sha256=result.tuning["plan_sha256"],
    )
    report = load_tuning_report(result.tuning["report_path"])
    assert len(trials.trials) == 5
    assert trials.trials[0].label == "baseline"
    assert trials.trials[0].sampled_params == {}
    assert plan.evaluation_plan_sha256 == result.evaluation["plan_sha256"]
    assert report.winner_trial_index == result.tuning["winner_trial_index"]
    assert tuning_artifact_filenames("model") == {
        "plan": "model.tuning-plan.json",
        "trials": "model.tuning-trials.json",
        "report": "model.tuning-report.json",
    }


def test_sampled_candidate_failure_aborts_with_trial_and_parameters_and_cleans_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(20)), "feature": list(range(20))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        params={"iterations": 100, "depth": 4},
        metrics=["gini", "rmse"],
        output_dir=str(tmp_path),
        evaluation=EVALUATION,
        tuning=TUNING,
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 20)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)
    calls = 0

    class FailedCandidate:
        def __init__(self, params: dict[str, Any]) -> None:
            self.params = params

        def run_evaluation_fit(self, **_kwargs: Any) -> EvaluationFitResult:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ValueError("depth is unsupported")
            return EvaluationFitResult(
                1,
                (calls - 1) % 2,
                8,
                8,
                {"gini": 0.4, "rmse": 1.0},
                9,
            )

    monkeypatch.setattr(
        job,
        "_new_evaluation_job",
        lambda **kwargs: FailedCandidate(kwargs["params"]),
    )
    with pytest.raises(
        RuntimeError,
        match=r"trial 1.*depth.*unsupported",
    ):
        job.run()
    assert not any(
        (tmp_path / filename).exists() for filename in tuning_artifact_filenames("model").values()
    )


@pytest.mark.parametrize(
    "failure",
    [
        ExecutionCancelledError("tuning", job_id="job-1"),
        MemoryError("tuning exceeded its memory budget"),
        BoundedMemoryUnsupportedError("tuning fit cannot remain bounded"),
    ],
)
def test_tuning_preserves_lifecycle_failure_identity_and_cleans_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(20)), "feature": list(range(20))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        params={"iterations": 100, "depth": 4},
        metrics=["gini", "rmse"],
        output_dir=str(tmp_path),
        evaluation=EVALUATION,
        tuning=TUNING,
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 20)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)

    class CancelledCandidate:
        def run_evaluation_fit(self, **_kwargs: Any) -> EvaluationFitResult:
            raise failure

    monkeypatch.setattr(
        job,
        "_new_evaluation_job",
        lambda **_kwargs: CancelledCandidate(),
    )

    with pytest.raises(type(failure)):
        job.run()

    assert not any(
        (tmp_path / filename).exists() for filename in tuning_artifact_filenames("model").values()
    )
