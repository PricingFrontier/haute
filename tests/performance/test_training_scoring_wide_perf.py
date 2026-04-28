from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import polars as pl
import pytest

import haute._model_scorer as model_scorer
import haute.modelling._training_job as training_job
from haute._mlflow_io import ScoringModel
from haute.modelling._split import PARTITION_TRAIN, PARTITION_VALIDATION
from haute.modelling._training_job import TrainingJob, _PreparedData, _SplitResult

pytestmark = pytest.mark.perf

_N_UNUSED_SCORING_COLUMNS = 320
_N_UNUSED_TRAINING_COLUMNS = 260


class _ShapeRecordingModel:
    def __init__(self) -> None:
        self.predict_shapes: list[tuple[int, int]] = []

    def predict(self, x_data: Any) -> np.ndarray:
        shape = getattr(x_data, "shape", None)
        if shape is None or len(shape) != 2:
            raise AssertionError(f"predict expected a 2-D object, got {type(x_data).__name__}")
        rows, width = int(shape[0]), int(shape[1])
        self.predict_shapes.append((rows, width))
        return np.full(rows, float(len(self.predict_shapes)), dtype=np.float64)


class _FakeCatBoostAlgorithm:
    def fit(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(model=object(), best_iteration=None, loss_history=[])


def _wide_numeric_frame(rows: int, unused_columns: int) -> pl.DataFrame:
    data: dict[str, list[float] | list[int]] = {
        "feature_a": [float(i) for i in range(rows)],
        "feature_b": [float(i * 2) for i in range(rows)],
    }
    for idx in range(unused_columns):
        data[f"unused_{idx:03d}"] = [idx * 1_000 + row for row in range(rows)]
    return pl.DataFrame(data)


def _raise_if_unused_column_is_evaluated(value: Any) -> Any:
    raise AssertionError(f"unused wide scoring column was evaluated: {value!r}")


def _prepared_split_input(data_path: str, row_count: int, features: list[str]) -> _PreparedData:
    return _PreparedData(
        data_path=data_path,
        owns_tmp=False,
        features=features,
        cat_features=[],
        total_rows=row_count,
        feature_dtypes={feature: "Float64" for feature in features},
        target_dtype="Float64",
    )


def test_eager_scoring_projects_features_before_prediction_prep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = 12
    features = ["feature_a", "feature_b"]
    base = pl.DataFrame(
        {
            "feature_a": [float(i) for i in range(rows)],
            "feature_b": [float(i * 10) for i in range(rows)],
        }
    )
    wide_unused_exprs = [
        pl.col("feature_a")
        .map_elements(_raise_if_unused_column_is_evaluated, return_dtype=pl.Float64)
        .alias(f"unused_raises_{idx:03d}")
        for idx in range(_N_UNUSED_SCORING_COLUMNS)
    ]
    lf = base.lazy().with_columns(wide_unused_exprs)
    model = _ShapeRecordingModel()
    prep_columns: list[list[str]] = []

    def record_prepare(
        df: pl.DataFrame,
        received_features: list[str],
        *,
        cat_feature_names: frozenset[str],
        flavor: str,
    ) -> np.ndarray:
        prep_columns.append(df.columns)
        assert received_features == features
        assert cat_feature_names == frozenset()
        assert flavor == "pyfunc"
        return df.to_numpy()

    monkeypatch.setattr("haute._mlflow_io._prepare_predict_frame", record_prepare)

    result = model_scorer.score_frame(
        model=model,
        lf=lf,
        features=features,
        cat_feature_names=frozenset(),
        flavor="pyfunc",
        output_col="prediction",
        batch=False,
    )

    assert isinstance(result, pl.LazyFrame)
    assert prep_columns == [features]
    assert model.predict_shapes == [(rows, len(features))]
    assert len(result.collect_schema().names()) == len(features) + _N_UNUSED_SCORING_COLUMNS + 1


def test_batch_scoring_prediction_prep_width_stays_feature_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = 10
    features = ["feature_a", "feature_b"]
    input_path = tmp_path / "wide_scoring.parquet"
    _wide_numeric_frame(rows, _N_UNUSED_SCORING_COLUMNS).write_parquet(input_path)
    model = _ShapeRecordingModel()
    scoring_model = ScoringModel(
        model,
        feature_names=features,
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )
    prep_columns: list[list[str]] = []
    prep_shapes: list[tuple[int, int]] = []

    def record_prepare(
        df: pl.DataFrame,
        received_features: list[str],
        *,
        cat_feature_names: frozenset[str],
        flavor: str,
    ) -> np.ndarray:
        prep_columns.append(df.columns)
        prep_shapes.append((df.height, df.width))
        assert received_features == features
        assert cat_feature_names == frozenset()
        assert flavor == "pyfunc"
        return df.to_numpy()

    monkeypatch.setattr("haute._mlflow_io._prepare_predict_frame", record_prepare)
    monkeypatch.setattr(model_scorer, "_SCORE_BATCH_SIZE", 4)

    out_path = model_scorer._batch_score_to_parquet(
        scoring_model,
        str(input_path),
        features,
        "prediction",
        "regression",
    )
    try:
        result = pl.read_parquet(out_path)
    finally:
        os.unlink(out_path)

    assert prep_columns == [features, features, features]
    assert prep_shapes == [(4, len(features)), (4, len(features)), (2, len(features))]
    assert model.predict_shapes == prep_shapes
    assert result.shape == (rows, len(features) + _N_UNUSED_SCORING_COLUMNS + 1)
    assert result["prediction"].to_list() == [1.0] * 4 + [2.0] * 4 + [3.0] * 2


def test_group_split_write_sinks_wide_plan_without_full_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = 48
    data: dict[str, list[float] | list[int] | list[str]] = {
        "feature": [float(i) for i in range(rows)],
        "target": [float(i % 5) for i in range(rows)],
        "group": [f"group_{i // 4:02d}" for i in range(rows)],
    }
    for idx in range(_N_UNUSED_TRAINING_COLUMNS):
        data[f"unused_{idx:03d}"] = [idx * 10_000 + row for row in range(rows)]
    data_path = tmp_path / "wide_training.parquet"
    pl.DataFrame(data).write_parquet(data_path)
    job = TrainingJob(
        name="wide_group_split_perf",
        data=str(data_path),
        target="target",
        split={
            "strategy": "group",
            "group_column": "group",
            "validation_size": 0.25,
            "holdout_size": 0.0,
            "seed": 7,
        },
    )
    original_collect = pl.LazyFrame.collect
    collected_schemas: list[list[str]] = []
    sink_schemas: list[list[str]] = []
    sink_kwargs: list[dict[str, Any]] = []

    def guarded_collect(self: pl.LazyFrame, *args: Any, **kwargs: Any) -> pl.DataFrame:
        if "optimizations" in kwargs:
            return original_collect(self, *args, **kwargs)
        columns = self.collect_schema().names()
        collected_schemas.append(columns)
        if columns not in (["group"], []):
            raise AssertionError(f"split pre-scan collected wide columns: {columns[:8]}")
        return original_collect(self, *args, **kwargs)

    def recording_safe_sink(lf: pl.LazyFrame, path: str | Path, **kwargs: Any) -> None:
        sink_schemas.append(lf.collect_schema().names())
        sink_kwargs.append(dict(kwargs))
        lf.sink_parquet(path)

    monkeypatch.setattr(pl.LazyFrame, "collect", guarded_collect)
    monkeypatch.setattr("haute._polars_utils.safe_sink", recording_safe_sink)

    split = job._split_data(
        _prepared_split_input(str(data_path), rows, ["feature"]),
        lambda _msg, _frac: None,
    )
    try:
        assert collected_schemas[0] == ["group"]
        assert all(columns in (["group"], []) for columns in collected_schemas)
        assert sink_kwargs == [{"fast_checkpoint": True}]
        assert len(sink_schemas) == 1
        assert len(sink_schemas[0]) == 4 + _N_UNUSED_TRAINING_COLUMNS
        assert sink_schemas[0][-1] == "_partition"

        monkeypatch.setattr(pl.LazyFrame, "collect", original_collect)
        split_df = pl.read_parquet(split.split_path)
        assert split_df.height == rows
        assert split.n_train + split.n_validation + split.n_holdout == rows
        assert (
            split_df.group_by("group").agg(pl.col("_partition").n_unique())["_partition"].max() == 1
        )
    finally:
        os.unlink(split.split_path)


def test_catboost_train_collects_only_projected_columns_on_wide_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = 18
    features = ["feature_b", "feature_a", "category"]
    data: dict[str, list[float] | list[int] | list[str]] = {
        "feature_b": [float(i * 2) for i in range(rows)],
        "target": [float(i % 2) for i in range(rows)],
        "feature_a": [float(i) for i in range(rows)],
        "weight": [1.0 + (i % 3) / 10.0 for i in range(rows)],
        "offset": [0.01 * i for i in range(rows)],
        "category": [f"segment_{i % 4}" for i in range(rows)],
        "_partition": [PARTITION_TRAIN if i % 3 else PARTITION_VALIDATION for i in range(rows)],
    }
    for idx in range(_N_UNUSED_TRAINING_COLUMNS):
        data[f"unused_{idx:03d}"] = [idx * 100_000 + row for row in range(rows)]
    split_path = tmp_path / "wide_catboost_split.parquet"
    pl.DataFrame(data).write_parquet(split_path)
    job = TrainingJob(
        name="wide_catboost_projection_perf",
        data=str(split_path),
        target="target",
        weight="weight",
        offset="offset",
        algorithm="catboost",
        task="regression",
    )
    split = _SplitResult(
        split_path=str(split_path),
        owns_tmp=False,
        n_train=12,
        n_validation=6,
        n_holdout=0,
    )
    expected_collect_schema = [*features, "target", "weight", "offset"]
    original_collect = pl.LazyFrame.collect
    collected_schemas: list[list[str]] = []
    pool_columns: list[list[str]] = []
    pool_aux_lengths: list[dict[str, int | None]] = []

    def guarded_collect(self: pl.LazyFrame, *args: Any, **kwargs: Any) -> pl.DataFrame:
        if "optimizations" in kwargs:
            return original_collect(self, *args, **kwargs)
        columns = self.collect_schema().names()
        collected_schemas.append(columns)
        if columns != expected_collect_schema:
            raise AssertionError(f"CatBoost collect was not projected: {columns[:10]}")
        return original_collect(self, *args, **kwargs)

    def fake_build_pool(
        df: pl.DataFrame,
        pool_features: list[str],
        cat_features: list[str],
        *,
        y: np.ndarray | None = None,
        w: np.ndarray | None = None,
        baseline: np.ndarray | None = None,
    ) -> object:
        assert pool_features == features
        assert cat_features == ["category"]
        pool_columns.append(df.columns)
        pool_aux_lengths.append(
            {
                "y": None if y is None else len(y),
                "w": None if w is None else len(w),
                "baseline": None if baseline is None else len(baseline),
            }
        )
        return object()

    monkeypatch.setitem(training_job.ALGORITHM_REGISTRY, "catboost", _FakeCatBoostAlgorithm)
    monkeypatch.setattr(pl.LazyFrame, "collect", guarded_collect)
    monkeypatch.setattr("haute.modelling._algorithms._build_pool", fake_build_pool)

    job._train_model(split, features, ["category"], None, lambda _msg, _frac: None)

    assert collected_schemas == [expected_collect_schema, expected_collect_schema]
    assert pool_columns == [features, features]
    assert pool_aux_lengths == [
        {"y": 12, "w": 12, "baseline": 12},
        {"y": 6, "w": 6, "baseline": 6},
    ]
