"""TrainingJob's bounded CV orchestration seam, with no real model backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute.modelling._cross_validation import (
    CrossValidationConfig,
    FoldPlan,
    file_sha256,
    generate_fold_plan,
    load_cross_validation_report,
    load_fold_results,
)
from haute.modelling._training_job import (
    TrainingJob,
    TrainResult,
    _PreparedData,
    cross_validation_artifact_filenames,
)


def cv_config(strategy: str = "random") -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "strategy": strategy,
        "fold_count": 3,
        "seed": 9,
    }
    if strategy == "group":
        result["group_column"] = "group"
    if strategy == "temporal":
        result["date_column"] = "date"
    return result


def test_constructor_rejects_internal_pair_and_cv_key_features() -> None:
    plan = generate_fold_plan(CrossValidationConfig.from_plain_data(cv_config()), "a" * 64, 6)
    with pytest.raises(ValueError):
        TrainingJob(name="x", data=pl.DataFrame({"y": [1]}), target="y", fold_plan=plan)
    with pytest.raises(ValueError):
        TrainingJob(
            name="x",
            data=pl.DataFrame({"y": [1], "group": ["a"]}),
            target="y",
            cross_validation=cv_config("group"),
            feature_columns=["group"],
        )
    with pytest.raises(ValueError):
        TrainingJob(
            name="x",
            data=pl.DataFrame({"y": [1], "group": ["a"]}),
            target="y",
            algorithm="glm",
            params={"terms": {"group": {}}},
            cross_validation=cv_config("group"),
        )
    existing_identifier = TrainingJob(
        name="x",
        data=pl.DataFrame({"y": [1], "group": ["a"]}),
        target="y",
        id_columns=["group"],
        cross_validation=cv_config("group"),
    )
    assert existing_identifier.id_columns == ["group"]


def test_internal_fold_split_uses_plan_mask_and_keeps_temporal_future_unused(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame(
        {
            "y": [1, 2, 3, 4, 5],
            "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        }
    ).write_parquet(source)
    config = CrossValidationConfig.from_plain_data(cv_config("temporal"))
    from haute.modelling._cross_validation import file_sha256

    plan = generate_fold_plan(config, file_sha256(source), pl.read_parquet(source)["date"])
    job = TrainingJob(name="x", data=str(source), target="y", fold_plan=plan, fold_index=0)
    prepared = _PreparedData(str(source), False, ["date"], [], 5)
    split = job._split_data(prepared, lambda *_: None)
    data = pl.read_parquet(split.split_path)
    assert data["_partition"].to_list() == plan.partition_mask(0).tolist()
    assert 3 in data["_partition"].to_list() and split.n_holdout == 0
    Path(split.split_path).unlink()


def test_cv_orchestration_is_sequential_and_only_final_forwards_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(9)), "feature": list(range(9))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["loss"],
        output_dir=str(tmp_path),
        cross_validation=cv_config(),
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 9)
    calls: list[str] = []

    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)

    class FakeJob:
        def __init__(self, fold: int | None, plan: FoldPlan | None) -> None:
            self.fold, self.plan = fold, plan

        def run(self, *, on_iteration=None, **_kwargs: Any) -> TrainResult:
            calls.append("final" if self.fold is None else f"fold-{self.fold}")
            if on_iteration:
                on_iteration(1, 1, {"loss": 1.0})
            if self.fold is None:
                return TrainResult({}, [], str(tmp_path / "model.cbm"), 1, 1, [], [])
            assert self.plan is not None
            train, validation = self.plan.fold_counts[self.fold]
            return TrainResult({"loss": float(self.fold + 1)}, [], "", train, validation, [], [])

    monkeypatch.setattr(
        job,
        "_new_cross_validation_job",
        lambda **kwargs: FakeJob(kwargs["fold_index"], kwargs["fold_plan"]),
    )
    iterations: list[int] = []
    result = job.run(on_iteration=lambda iteration, *_: iterations.append(iteration))
    assert calls == ["fold-0", "fold-1", "fold-2", "final"]
    assert iterations == [1]
    assert result.cross_validation is not None
    assert result.cross_validation["fit_count"] == 4
    for path in ("fold_plan_path", "fold_results_path", "report_path"):
        assert Path(result.cross_validation[path]).is_absolute()
        assert Path(result.cross_validation[path]).exists()
    loaded_results = load_fold_results(result.cross_validation["fold_results_path"])
    loaded_report = load_cross_validation_report(result.cross_validation["report_path"])
    assert loaded_report.folds == loaded_results.results
    assert loaded_report.results_sha256 == file_sha256(result.cross_validation["fold_results_path"])
    assert loaded_report.plan_sha256 == file_sha256(result.cross_validation["fold_plan_path"])
    assert cross_validation_artifact_filenames("model") == {
        "fold_plan": "model.cv-fold-plan.json",
        "fold_results": "model.cv-fold-results.json",
        "report": "model.cv-report.json",
    }


def test_cv_reuses_owned_source_already_cleaned_by_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "clean-source.parquet"
    pl.DataFrame({"y": list(range(6)), "feature": list(range(6))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["loss"],
        output_dir=str(tmp_path),
        cross_validation=cv_config(),
    )
    # _prepare_data has already removed the one null-target row from an owned
    # source. The retained count records what it cleaned; the parquet itself is
    # the stable eligible source and must not be scanned and written again.
    prepared = _PreparedData(
        str(source),
        True,
        ["feature"],
        [],
        6,
        target_null_count=1,
    )
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)

    rematerialisations = 0
    from haute import _polars_utils

    original_bounded_sink = _polars_utils.bounded_sink

    def count_bounded_sink(*args: Any, **kwargs: Any) -> Any:
        nonlocal rematerialisations
        rematerialisations += 1
        return original_bounded_sink(*args, **kwargs)

    monkeypatch.setattr(_polars_utils, "bounded_sink", count_bounded_sink)

    class StopAfterPlanError(RuntimeError):
        pass

    class StoppingFold:
        def run(self, **_kwargs: Any) -> TrainResult:
            raise StopAfterPlanError

    monkeypatch.setattr(job, "_new_cross_validation_job", lambda **_kwargs: StoppingFold())

    with pytest.raises(StopAfterPlanError):
        job.run()

    assert rematerialisations == 0


def test_ten_folds_are_exactly_eleven_sequential_fits_and_preserve_final_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(20)), "feature": list(range(20))}).write_parquet(source)
    config = cv_config()
    config["fold_count"] = 10
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["loss"],
        output_dir=str(tmp_path),
        cross_validation=config,
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 20)
    calls: list[int | None] = []
    final_result = TrainResult(
        metrics={"loss": 0.25},
        feature_importance=[{"feature": "feature", "importance": 1.0}],
        model_path=str(tmp_path / "model.cbm"),
        train_rows=16,
        validation_rows=4,
        features=["feature"],
        cat_features=[],
        diagnostics_errors=[
            {"diagnostic": "pdp", "error": "unavailable", "error_type": "ValueError"}
        ],
    )
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)

    class FakeJob:
        def __init__(self, fold: int | None, plan: FoldPlan | None) -> None:
            self.fold = fold
            self.plan = plan

        def run(self, **_kwargs: Any) -> TrainResult:
            calls.append(self.fold)
            if self.fold is None:
                return final_result
            assert self.plan is not None
            train, validation = self.plan.fold_counts[self.fold]
            return TrainResult(
                {"loss": float(self.fold)},
                [],
                "",
                train,
                validation,
                [],
                [],
            )

    monkeypatch.setattr(
        job,
        "_new_cross_validation_job",
        lambda **kwargs: FakeJob(kwargs["fold_index"], kwargs["fold_plan"]),
    )

    result = job.run()

    assert calls == [*range(10), None]
    assert result is final_result
    assert result.metrics == {"loss": 0.25}
    assert result.feature_importance == [{"feature": "feature", "importance": 1.0}]
    assert result.diagnostics_errors[0]["diagnostic"] == "pdp"
    assert result.cross_validation is not None
    assert result.cross_validation["fit_count"] == 11


def test_final_fit_rechecks_prepared_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(9)), "feature": list(range(9))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["loss"],
        output_dir=str(tmp_path),
        cross_validation=cv_config(),
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 9)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)
    calls: list[int | None] = []

    class FakeJob:
        def __init__(self, fold: int | None, plan: FoldPlan | None) -> None:
            self.fold = fold
            self.plan = plan

        def run(self, **_kwargs: Any) -> TrainResult:
            calls.append(self.fold)
            if self.fold is None:
                return TrainResult({}, [], str(tmp_path / "model.cbm"), 1, 1, [], [])
            assert self.plan is not None
            train, validation = self.plan.fold_counts[self.fold]
            if self.fold == self.plan.config.fold_count - 1:
                pl.DataFrame({"y": list(range(10)), "feature": list(range(10))}).write_parquet(
                    source
                )
            return TrainResult(
                {"loss": float(self.fold)},
                [],
                "",
                train,
                validation,
                [],
                [],
            )

    monkeypatch.setattr(
        job,
        "_new_cross_validation_job",
        lambda **kwargs: FakeJob(kwargs["fold_index"], kwargs["fold_plan"]),
    )

    with pytest.raises(ValueError, match="source SHA-256"):
        job.run()

    assert calls == [0, 1, 2]


def test_cancellation_token_stops_current_and_queued_folds_and_cleans_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    pl.DataFrame({"y": list(range(9)), "feature": list(range(9))}).write_parquet(source)
    job = TrainingJob(
        name="model",
        data=str(source),
        target="y",
        metrics=["loss"],
        output_dir=str(tmp_path),
        cross_validation=cv_config(),
    )
    prepared = _PreparedData(str(source), False, ["feature"], [], 9)
    monkeypatch.setattr(job, "_prepare_data", lambda *_args, **_kwargs: prepared)
    calls: list[int | None] = []
    cancelled = False
    context = ExecutionContext(
        operation="training_job",
        profile=ExecutionProfile.TRAINING_PREP,
    )

    class CancelledError(RuntimeError):
        pass

    def check_cancelled() -> None:
        if cancelled:
            raise CancelledError("cancelled")

    class FakeJob:
        def __init__(self, fold: int | None, plan: FoldPlan | None) -> None:
            self.fold = fold
            self.plan = plan

        def run(
            self,
            *,
            check_cancelled: Any,
            execution_context: Any,
            **_kwargs: Any,
        ) -> TrainResult:
            nonlocal cancelled
            assert check_cancelled is not None
            assert execution_context is context
            calls.append(self.fold)
            if self.fold == 1:
                cancelled = True
                check_cancelled()
            assert self.fold is not None and self.plan is not None
            train, validation = self.plan.fold_counts[self.fold]
            return TrainResult({"loss": 1.0}, [], "", train, validation, [], [])

    monkeypatch.setattr(
        job,
        "_new_cross_validation_job",
        lambda **kwargs: FakeJob(kwargs["fold_index"], kwargs["fold_plan"]),
    )

    with pytest.raises(CancelledError):
        job.run(check_cancelled=check_cancelled, execution_context=context)

    assert calls == [0, 1]
    names = cross_validation_artifact_filenames("model")
    assert not any((tmp_path / name).exists() for name in names.values())
