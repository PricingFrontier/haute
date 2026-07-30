"""TrainingJob orchestration on one test-safe evaluation plan."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute.modelling._evaluation import (
    EvaluationConfig,
    EvaluationFitResult,
    EvaluationPlan,
    file_sha256,
    generate_evaluation_plan,
    load_evaluation_plan,
    load_evaluation_report,
    load_evaluation_results,
)
from haute.modelling._training_job import (
    TrainingJob,
    TrainResult,
    _PreparedData,
    evaluation_artifact_filenames,
)


def evaluation(
    *,
    validation: dict[str, object] | None = None,
    test: dict[str, object] | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {
        "schema_version": 1,
        "strategy": "random",
        "seed": 9,
        "validation": validation or {"method": "cross_validation", "fold_count": 3},
    }
    if test is not None:
        config["test"] = test
    return config


def final_result(tmp_path: Path, *, development: int, final_test: int) -> TrainResult:
    metrics = {"rmse": 0.25}
    return TrainResult(
        metrics=metrics,
        feature_importance=[{"feature": "feature", "importance": 1.0}],
        model_path=str(tmp_path / "model.cbm"),
        train_rows=development,
        validation_rows=0,
        holdout_rows=final_test,
        holdout_metrics=metrics if final_test else {},
        diagnostics_set="holdout" if final_test else "train",
        features=["feature"],
        cat_features=[],
    )


def test_direct_split_compatibility_is_not_silently_replaced_by_evaluation() -> None:
    job = TrainingJob(
        name="legacy-direct",
        data=pl.DataFrame({"y": [0, 1], "feature": [1, 2]}),
        target="y",
        split={"strategy": "random", "validation_size": 0.3, "seed": 17},
    )

    assert job.evaluation is None
    assert job.split_config.validation_size == 0.3
    assert job.split_config.seed == 17


def test_direct_job_rejects_competing_split_and_evaluation_contracts() -> None:
    with pytest.raises(ValueError, match="split and evaluation are competing"):
        TrainingJob(
            name="competing",
            data=pl.DataFrame({"y": [0, 1], "feature": [1, 2]}),
            target="y",
            split={"strategy": "random", "validation_size": 0.3},
            evaluation=evaluation(),
        )


def test_internal_evaluation_plan_requires_its_explicit_evaluation_contract() -> None:
    plan = generate_evaluation_plan(
        EvaluationConfig.from_plain_data(evaluation()),
        source_sha256="0" * 64,
        row_count=6,
        task="regression",
    )

    with pytest.raises(ValueError, match="evaluation_plan requires an explicit evaluation"):
        TrainingJob(
            name="missing-contract",
            data=pl.DataFrame({"y": range(6), "feature": range(6)}),
            target="y",
            evaluation_plan=plan,
        )


def test_selection_fits_are_sequential_evaluation_only_and_final_fit_is_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(10)), "feature": list(range(10))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["rmse"],
        output_dir=str(tmp_path),
        evaluation=evaluation(test={"size": 0.2}),
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 10)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)
    calls: list[tuple[str, int | None]] = []
    iterations: list[int] = []

    class FakeJob:
        def __init__(self, fit_index: int | None, plan: EvaluationPlan) -> None:
            self.fit_index = fit_index
            self.plan = plan

        def run_evaluation_fit(self, **_kwargs: Any) -> EvaluationFitResult:
            assert self.fit_index is not None
            calls.append(("selection", self.fit_index))
            fit = self.plan.validation_fits[self.fit_index]
            return EvaluationFitResult(
                schema_version=1,
                fit_index=self.fit_index,
                train_rows=fit.train_rows,
                validation_rows=fit.validation_rows,
                metrics={"rmse": float(self.fit_index + 1)},
                best_iteration=self.fit_index,
            )

        def run(self, *, on_iteration=None, **_kwargs: Any) -> TrainResult:
            calls.append(("final", None))
            if on_iteration is not None:
                on_iteration(1, 1, {"loss": 1.0})
            return final_result(
                tmp_path,
                development=len(self.plan.development_positions),
                final_test=len(self.plan.test_positions),
            )

    monkeypatch.setattr(
        job,
        "_new_evaluation_job",
        lambda **kwargs: FakeJob(kwargs["fit_index"], kwargs["plan"]),
    )
    result = job.run(on_iteration=lambda iteration, *_: iterations.append(iteration))

    assert calls == [
        ("selection", 0),
        ("selection", 1),
        ("selection", 2),
        ("final", None),
    ]
    assert iterations == [1]
    assert result.development_rows == 8
    assert result.final_test_rows == 2
    assert result.final_test_metrics == {"rmse": 0.25}
    assert result.diagnostics_set == "final_test"
    assert result.evaluation is not None
    assert result.evaluation["fit_count"] == 4
    assert result.evaluation["selection_metrics"]["rmse"]["mean"] == pytest.approx(1.875)
    assert result.evaluation["development_rows"] == 8
    assert result.evaluation["final_test_rows"] == 2
    assert set(result.evaluation["selection_fits"][0]) == {
        "schema_version",
        "fit_index",
        "train_rows",
        "validation_rows",
        "metrics",
        "best_iteration",
    }

    plan_path = Path(result.evaluation["plan_path"])
    results_path = Path(result.evaluation["results_path"])
    report_path = Path(result.evaluation["report_path"])
    plan = load_evaluation_plan(plan_path, source_sha256=file_sha256(source))
    results = load_evaluation_results(results_path, plan_sha256=file_sha256(plan_path))
    report = load_evaluation_report(report_path)
    assert len(plan.test_positions) == 2
    assert len(results.fits) == 3
    assert report.plan_sha256 == file_sha256(plan_path)
    assert report.results_sha256 == file_sha256(results_path)
    assert evaluation_artifact_filenames("model") == {
        "plan": "model.evaluation-plan.json",
        "results": "model.evaluation-results.json",
        "report": "model.evaluation-report.json",
    }


def test_no_validation_runs_only_one_final_fit_and_reports_no_selection_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(6)), "feature": list(range(6))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["rmse"],
        output_dir=str(tmp_path),
        evaluation=evaluation(validation={"method": "none"}),
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 6)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)
    calls: list[int | None] = []

    class FakeFinal:
        def __init__(self, fit_index: int | None, plan: EvaluationPlan) -> None:
            self.fit_index = fit_index
            self.plan = plan

        def run_evaluation_fit(self, **_kwargs: Any) -> EvaluationFitResult:
            raise AssertionError("no selection fit is allowed")

        def run(self, **_kwargs: Any) -> TrainResult:
            calls.append(self.fit_index)
            return final_result(tmp_path, development=6, final_test=0)

    monkeypatch.setattr(
        job,
        "_new_evaluation_job",
        lambda **kwargs: FakeFinal(kwargs["fit_index"], kwargs["plan"]),
    )
    result = job.run()
    assert calls == [None]
    assert result.evaluation is not None
    assert result.evaluation["fit_count"] == 1
    assert result.evaluation["selection_fits"] == []
    assert result.evaluation["selection_metrics"] == {}
    assert result.final_test_metrics == {}
    assert result.diagnostics_set == "development"


def test_temporal_cross_validation_runs_through_strict_plan_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "temporal-source.parquet"
    pl.DataFrame(
        {
            "y": list(range(8)),
            "feature": list(range(8)),
            "month": [date(2024, month, 1) for month in range(1, 9)],
        }
    ).write_parquet(source)
    job = TrainingJob(
        name="temporal-model",
        data=str(source),
        target="y",
        metrics=["rmse"],
        output_dir=str(tmp_path),
        evaluation={
            "schema_version": 1,
            "strategy": "temporal",
            "date_column": "month",
            "test": {"start": "2024-07-01"},
            "validation": {
                "method": "cross_validation",
                "fold_count": 2,
                "window": "expanding",
            },
        },
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 8)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)

    class FakeJob:
        def __init__(self, fit_index: int | None, plan: EvaluationPlan) -> None:
            self.fit_index = fit_index
            self.plan = plan

        def run_evaluation_fit(self, **_kwargs: Any) -> EvaluationFitResult:
            assert self.fit_index is not None
            fit = self.plan.validation_fits[self.fit_index]
            return EvaluationFitResult(
                schema_version=1,
                fit_index=self.fit_index,
                train_rows=fit.train_rows,
                validation_rows=fit.validation_rows,
                metrics={"rmse": float(self.fit_index + 1)},
                best_iteration=self.fit_index,
            )

        def run(self, **_kwargs: Any) -> TrainResult:
            return final_result(tmp_path, development=6, final_test=2)

    monkeypatch.setattr(
        job,
        "_new_evaluation_job",
        lambda **kwargs: FakeJob(kwargs["fit_index"], kwargs["plan"]),
    )

    result = job.run()

    assert result.evaluation is not None
    assert result.evaluation["validation_fit_count"] == 2
    persisted = load_evaluation_plan(
        result.evaluation["plan_path"],
        source_sha256=file_sha256(source),
    )
    assert persisted.validation_fits[0].train_positions == (0, 1)
    assert persisted.validation_fits[0].validation_positions == (2, 3)
    assert persisted.validation_fits[1].train_positions == (0, 1, 2, 3)
    assert persisted.validation_fits[1].validation_positions == (4, 5)
    assert persisted.test_positions == (6, 7)


def test_final_test_positions_never_enter_any_selection_fit(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(25)), "feature": list(range(25))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["rmse"],
        output_dir=str(tmp_path),
        evaluation=evaluation(test={"size": 0.2}),
    )
    plan = job._build_evaluation_plan(
        _PreparedData(str(source), False, ["feature"], [], 25),
    )
    final_test = set(plan.test_positions)
    assert final_test
    for fit in plan.validation_fits:
        assert final_test.isdisjoint(fit.train_positions)
        assert final_test.isdisjoint(fit.validation_positions)


def test_failure_cleans_all_staged_evaluation_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(9)), "feature": list(range(9))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["rmse"],
        output_dir=str(tmp_path),
        evaluation=evaluation(),
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 9)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)

    class FailedFit:
        def run_evaluation_fit(self, **_kwargs: Any) -> EvaluationFitResult:
            raise RuntimeError("selection failed")

    monkeypatch.setattr(job, "_new_evaluation_job", lambda **_kwargs: FailedFit())
    with pytest.raises(RuntimeError, match="selection failed"):
        job.run()
    assert not any(
        (tmp_path / filename).exists()
        for filename in evaluation_artifact_filenames("model").values()
    )
