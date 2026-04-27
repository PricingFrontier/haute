from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import polars as pl
import pytest

import haute.modelling._training_job as training_job
from haute.modelling._split import PARTITION_TRAIN, PARTITION_VALIDATION
from haute.modelling._training_job import TrainingJob, _SplitResult


class _FakeCatBoostAlgorithm:
    def fit(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(model=object(), best_iteration=None, loss_history=[])


def _write_split_parquet(tmp_path: Path) -> str:
    path = tmp_path / "split.parquet"
    pl.DataFrame(
        {
            "feature_b": [10.0, 20.0, 30.0, 40.0],
            "unused_wide_0": ["drop-a", "drop-b", "drop-c", "drop-d"],
            "target": [1.0, 0.0, 1.0, 0.0],
            "feature_a": [1.0, 2.0, 3.0, 4.0],
            "weight": [0.5, 0.6, 0.7, 0.8],
            "unused_wide_1": [100, 200, 300, 400],
            "offset": [0.1, 0.2, 0.3, 0.4],
            "category": ["x", "y", "x", "z"],
            "_partition": [
                PARTITION_TRAIN,
                PARTITION_VALIDATION,
                PARTITION_TRAIN,
                PARTITION_VALIDATION,
            ],
        }
    ).write_parquet(path)
    return str(path)


def test_catboost_train_and_eval_reads_project_required_columns_before_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split_path = _write_split_parquet(tmp_path)
    features = ["feature_b", "feature_a", "category"]
    job = TrainingJob(
        name="catboost_projection",
        data=split_path,
        target="target",
        weight="weight",
        offset="offset",
        algorithm="catboost",
        task="regression",
    )
    split = _SplitResult(
        split_path=split_path,
        owns_tmp=False,
        n_train=2,
        n_validation=2,
        n_holdout=0,
    )
    original_collect = pl.LazyFrame.collect
    collected_schemas: list[list[str]] = []
    pool_calls: list[dict[str, Any]] = []

    def guarded_collect(self: pl.LazyFrame, *args: Any, **kwargs: Any) -> pl.DataFrame:
        if "optimizations" in kwargs:
            return original_collect(self, *args, **kwargs)
        columns = self.collect_schema().names()
        collected_schemas.append(columns)
        assert columns == ["feature_b", "feature_a", "category", "target", "weight", "offset"]
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
        pool_calls.append(
            {
                "columns": df.columns,
                "features": list(pool_features),
                "cat_features": list(cat_features),
                "y": None if y is None else y.tolist(),
                "w": None if w is None else w.tolist(),
                "baseline": None if baseline is None else baseline.tolist(),
            }
        )
        return object()

    monkeypatch.setitem(training_job.ALGORITHM_REGISTRY, "catboost", _FakeCatBoostAlgorithm)
    monkeypatch.setattr(pl.LazyFrame, "collect", guarded_collect)
    monkeypatch.setattr("haute.modelling._algorithms._build_pool", fake_build_pool)

    job._train_model(split, features, ["category"], None, lambda _msg, _frac: None)

    assert collected_schemas == [
        ["feature_b", "feature_a", "category", "target", "weight", "offset"],
        ["feature_b", "feature_a", "category", "target", "weight", "offset"],
    ]
    assert [call["columns"] for call in pool_calls] == [features, features]
    assert [call["features"] for call in pool_calls] == [features, features]
    assert [call["cat_features"] for call in pool_calls] == [["category"], ["category"]]
    assert pool_calls[0]["y"] == [1.0, 1.0]
    assert pool_calls[0]["w"] == [0.5, 0.7]
    assert pool_calls[0]["baseline"] == [0.1, 0.3]
    assert pool_calls[1]["y"] == [0.0, 0.0]
    assert pool_calls[1]["w"] == [0.6, 0.8]
    assert pool_calls[1]["baseline"] == [0.2, 0.4]


def test_glm_partition_projection_stays_limited_to_terms_and_aux_columns(tmp_path: Path) -> None:
    path = tmp_path / "glm_split.parquet"
    pl.DataFrame(
        {
            "term_b": [1.0],
            "unused_wide": [99],
            "target": [2.0],
            "term_a": [3.0],
            "weight": [0.5],
            "offset": [0.1],
            "_partition": [PARTITION_TRAIN],
        }
    ).write_parquet(path)
    job = TrainingJob(
        name="glm_projection",
        data=str(path),
        target="target",
        weight="weight",
        offset="offset",
        algorithm="glm",
    )

    projected = job._scan_with_columns(
        str(path),
        ["term_b", "term_a"],
    )

    assert projected.collect_schema().names() == [
        "offset",
        "target",
        "term_a",
        "term_b",
        "weight",
        "_partition",
    ]
