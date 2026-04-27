from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute.modelling._split import PARTITION_TRAIN, split_mask
from haute.modelling._training_job import TrainingJob, _PreparedData


def _write_parquet(tmp_path: Path, df: pl.DataFrame, name: str = "training.parquet") -> str:
    path = tmp_path / name
    df.write_parquet(path)
    return str(path)


def _prepared(data_path: str, row_count: int) -> _PreparedData:
    return _PreparedData(
        data_path=data_path,
        owns_tmp=False,
        features=["feature"],
        cat_features=[],
        total_rows=row_count,
        feature_dtypes={"feature": "Float64"},
        target_dtype="Float64",
    )


def _partition_counts(path: str) -> dict[int, int]:
    counts = pl.read_parquet(path).group_by("_partition").len().sort("_partition").to_dicts()
    return {int(row["_partition"]): int(row["len"]) for row in counts}


def test_random_split_write_sinks_without_collecting_full_split_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    df = pl.DataFrame(
        {
            "feature": [float(i) for i in range(20)],
            "target": [float(i % 3) for i in range(20)],
            "unused_wide": [f"payload-{i}" for i in range(20)],
        }
    )
    data_path = _write_parquet(tmp_path, df)
    job = TrainingJob(
        name="stream_random",
        data=data_path,
        target="target",
        split={"strategy": "random", "validation_size": 0.25, "holdout_size": 0.2, "seed": 17},
    )

    sink_schemas: list[list[str]] = []
    sink_kwargs: list[dict[str, Any]] = []

    def recording_safe_sink(lf: pl.LazyFrame, path: str | Path, **kwargs: Any) -> None:
        sink_schemas.append(lf.collect_schema().names())
        sink_kwargs.append(dict(kwargs))
        lf.sink_parquet(path)

    monkeypatch.setattr("haute._polars_utils.safe_sink", recording_safe_sink)

    split = job._split_data(_prepared(data_path, len(df)), lambda _msg, _frac: None)
    try:
        assert sink_schemas == [["feature", "target", "unused_wide", "_partition"]]
        assert sink_kwargs == [{"fast_checkpoint": True}]
        expected_mask = split_mask(len(df), job.split_config)
        assert split.n_train == int((expected_mask == 0).sum())
        assert split.n_validation == int((expected_mask == 1).sum())
        assert split.n_holdout == int((expected_mask == 2).sum())
        assert _partition_counts(split.split_path) == {
            PARTITION_TRAIN: split.n_train,
            1: split.n_validation,
            2: split.n_holdout,
        }
        assert pl.read_parquet(split.split_path).columns == [
            "feature",
            "target",
            "unused_wide",
            "_partition",
        ]
    finally:
        os.unlink(split.split_path)


def test_group_split_only_collects_group_column_before_sink_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    df = pl.DataFrame(
        {
            "feature": [float(i) for i in range(12)],
            "target": [float(i % 2) for i in range(12)],
            "group": ["a", "a", "b", "b", "c", "c", "d", "d", "e", "e", "f", "f"],
            "unused_wide": [f"payload-{i}" for i in range(12)],
        }
    )
    data_path = _write_parquet(tmp_path, df)
    job = TrainingJob(
        name="stream_group",
        data=data_path,
        target="target",
        split={
            "strategy": "group",
            "group_column": "group",
            "validation_size": 0.35,
            "holdout_size": 0.2,
            "seed": 11,
        },
    )
    original_collect = pl.LazyFrame.collect
    collected_schemas: list[list[str]] = []
    sink_schemas: list[list[str]] = []
    sink_kwargs: list[dict[str, Any]] = []

    def guarded_collect(self: pl.LazyFrame, *args: Any, **kwargs: Any) -> pl.DataFrame:
        columns = self.collect_schema().names()
        collected_schemas.append(columns)
        if columns in (["group"], []):
            return original_collect(self, *args, **kwargs)
        raise AssertionError(
            f"split writer should sink the full split plan instead of collecting columns {columns}"
        )

    def recording_safe_sink(lf: pl.LazyFrame, path: str | Path, **kwargs: Any) -> None:
        sink_schemas.append(lf.collect_schema().names())
        sink_kwargs.append(dict(kwargs))
        lf.sink_parquet(path)

    monkeypatch.setattr(pl.LazyFrame, "collect", guarded_collect)
    monkeypatch.setattr("haute._polars_utils.safe_sink", recording_safe_sink)

    split = job._split_data(_prepared(data_path, len(df)), lambda _msg, _frac: None)
    try:
        assert collected_schemas[0] == ["group"]
        assert all(columns in (["group"], []) for columns in collected_schemas), collected_schemas
        assert sink_schemas == [["feature", "target", "group", "unused_wide", "_partition"]]
        assert sink_kwargs == [{"fast_checkpoint": True}]
        monkeypatch.setattr(pl.LazyFrame, "collect", original_collect)
        split_df = pl.read_parquet(split.split_path)
        assert set(split_df.columns) == {"feature", "target", "group", "unused_wide", "_partition"}
        group_partitions = split_df.group_by("group").agg(pl.col("_partition").n_unique())
        assert group_partitions["_partition"].max() == 1
        assert split.n_train + split.n_validation + split.n_holdout == len(df)
    finally:
        os.unlink(split.split_path)
