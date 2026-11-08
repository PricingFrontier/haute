from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
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


def _recorded_collects(
    monkeypatch: pytest.MonkeyPatch, *, slice_rows: int
) -> list[tuple[list[str], int]]:
    """Force *slice_rows*-row slices, and record each frame the split writer collects."""
    from haute import _chunked_writes

    monkeypatch.setattr(_chunked_writes, "SINGLE_FILE_ROW_GROUP_SIZE", slice_rows)
    collected: list[tuple[list[str], int]] = []
    original = _chunked_writes.execution_collect

    def recording(lf: pl.LazyFrame, **kwargs: Any) -> pl.DataFrame:
        frame = original(lf, **kwargs)
        collected.append((frame.columns, frame.height))
        return frame

    monkeypatch.setattr(_chunked_writes, "execution_collect", recording)
    return collected


def _slice_heights(collected: list[tuple[list[str], int]]) -> list[int]:
    return [height for columns, height in collected if "_partition" in columns]


def test_random_split_is_written_a_slice_at_a_time(
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

    collected = _recorded_collects(monkeypatch, slice_rows=6)

    split = job._split_data(_prepared(data_path, len(df)), lambda _msg, _frac: None)
    try:
        # The whole split plan is never held: every frame is one slice of it.
        assert _slice_heights(collected) == [6, 6, 6, 2]
        # The counts, not the positions, are compared: any order gives them.
        expected_mask = split_mask(len(df), job.split_config, row_order=np.arange(len(df)))
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


def test_group_split_collects_only_its_group_column_before_the_slices(
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
    collected = _recorded_collects(monkeypatch, slice_rows=5)

    def guarded_collect(self: pl.LazyFrame, *args: Any, **kwargs: Any) -> pl.DataFrame:
        columns = self.collect_schema().names()
        collected_schemas.append(columns)
        if columns in (["group"], [], ["len"]) or "_partition" in columns:
            return original_collect(self, *args, **kwargs)
        raise AssertionError(
            f"split writer should read only the group column and slices, not {columns}"
        )

    monkeypatch.setattr(pl.LazyFrame, "collect", guarded_collect)

    split = job._split_data(_prepared(data_path, len(df)), lambda _msg, _frac: None)
    try:
        assert collected_schemas[0] == ["group"]
        assert _slice_heights(collected) == [5, 5, 2]
        monkeypatch.setattr(pl.LazyFrame, "collect", original_collect)
        split_df = pl.read_parquet(split.split_path)
        assert set(split_df.columns) == {"feature", "target", "group", "unused_wide", "_partition"}
        group_partitions = split_df.group_by("group").agg(pl.col("_partition").n_unique())
        assert group_partitions["_partition"].max() == 1
        assert split.n_train + split.n_validation + split.n_holdout == len(df)
    finally:
        os.unlink(split.split_path)
