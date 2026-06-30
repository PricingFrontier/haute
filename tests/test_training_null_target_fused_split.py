from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute.modelling._split import (
    PARTITION_HOLDOUT,
    PARTITION_TRAIN,
    PARTITION_VALIDATION,
    split_mask,
)
from haute.modelling._training_job import TrainingJob


def _counts_by_partition(path: str) -> dict[int, int]:
    rows = pl.read_parquet(path).group_by("_partition").len().sort("_partition").to_dicts()
    return {int(row["_partition"]): int(row["len"]) for row in rows}


def test_null_target_filter_is_fused_into_split_sink_for_parquet_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_path = tmp_path / "training.parquet"
    pl.DataFrame(
        {
            "feature": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "target": [1.0, None, 0.0, 1.0, None, 0.0],
            "wide_unused": [f"payload-{idx}" for idx in range(6)],
        }
    ).write_parquet(data_path)
    job = TrainingJob(
        name="fused_null_split",
        data=str(data_path),
        target="target",
        split={"strategy": "random", "validation_size": 0.25, "holdout_size": 0.25, "seed": 7},
    )
    sink_calls: list[dict[str, Any]] = []

    def recording_bounded_sink(lf: pl.LazyFrame, path: str | Path, **kwargs: Any) -> None:
        sink_calls.append(
            {
                "path": str(path),
                "schema": lf.collect_schema().names(),
                "kwargs": dict(kwargs),
            }
        )
        lf.sink_parquet(path)

    monkeypatch.setattr("haute._polars_utils.bounded_sink", recording_bounded_sink)

    prepared = job._prepare_data(lambda _msg, _frac: None)
    assert prepared.data_path == str(data_path)
    assert prepared.total_rows == 4

    split = job._split_data(prepared, lambda _msg, _frac: None)
    try:
        assert len(sink_calls) == 1
        assert "haute_clean_" not in sink_calls[0]["path"]
        assert sink_calls[0]["schema"] == ["feature", "target", "wide_unused", "_partition"]
        assert sink_calls[0]["kwargs"] == {"fast_checkpoint": True}

        split_df = pl.read_parquet(split.split_path)
        assert len(split_df) == 4
        assert split_df["target"].null_count() == 0

        expected_mask = split_mask(4, job.split_config)
        assert split.n_train == int((expected_mask == PARTITION_TRAIN).sum())
        assert split.n_validation == int((expected_mask == PARTITION_VALIDATION).sum())
        assert split.n_holdout == int((expected_mask == PARTITION_HOLDOUT).sum())
        assert _counts_by_partition(split.split_path) == {
            PARTITION_TRAIN: split.n_train,
            PARTITION_VALIDATION: split.n_validation,
            PARTITION_HOLDOUT: split.n_holdout,
        }
    finally:
        os.unlink(split.split_path)


def test_owned_temp_null_target_source_is_cleaned_after_fused_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = TrainingJob(
        name="owned_fused_null_split",
        data=pl.DataFrame(
            {
                "feature": [10.0, 20.0, 30.0, 40.0],
                "target": [1.0, None, 0.0, 1.0],
            }
        ),
        target="target",
        split={"strategy": "random", "validation_size": 0.25, "seed": 3},
    )
    sink_calls: list[dict[str, Any]] = []

    def recording_bounded_sink(lf: pl.LazyFrame, path: str | Path, **_kwargs: Any) -> None:
        sink_calls.append(
            {
                "path": str(path),
                "schema": lf.collect_schema().names(),
                "kwargs": dict(_kwargs),
            }
        )
        lf.sink_parquet(path)

    monkeypatch.setattr("haute._polars_utils.bounded_sink", recording_bounded_sink)

    prepared = job._prepare_data(lambda _msg, _frac: None)
    source_path = Path(prepared.data_path)
    assert prepared.owns_tmp is True
    assert source_path.exists()
    assert prepared.total_rows == 3
    assert pl.read_parquet(source_path)["target"].null_count() == 0

    split = job._split_data(prepared, lambda _msg, _frac: None)
    try:
        assert len(sink_calls) == 2
        assert "haute_clean_" in sink_calls[0]["path"]
        assert sink_calls[0]["schema"] == ["feature", "target"]
        assert sink_calls[0]["kwargs"] == {"fast_checkpoint": True}
        assert "haute_split_" in sink_calls[1]["path"]
        assert sink_calls[1]["schema"] == ["feature", "target", "_partition"]
        assert sink_calls[1]["kwargs"] == {"fast_checkpoint": True}
        assert source_path.exists() is False
        split_df = pl.read_parquet(split.split_path)
        assert len(split_df) == 3
        assert split_df["target"].null_count() == 0
    finally:
        os.unlink(split.split_path)


def test_all_null_owned_temp_source_is_removed_before_loud_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = TrainingJob(
        name="all_null_targets",
        data=pl.DataFrame(
            {
                "feature": [10.0, 20.0, 30.0],
                "target": [None, None, None],
            }
        ),
        target="target",
    )
    original_write_parquet = pl.DataFrame.write_parquet
    written_paths: list[Path] = []

    def recording_write_parquet(
        self: pl.DataFrame,
        path: str | Path,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        written_paths.append(Path(path))
        original_write_parquet(self, path, *args, **kwargs)

    monkeypatch.setattr(pl.DataFrame, "write_parquet", recording_write_parquet)

    with pytest.raises(ValueError, match="contains only null values"):
        job._prepare_data(lambda _msg, _frac: None)

    assert written_paths
    assert all(not path.exists() for path in written_paths)


def test_glm_term_narrowing_preserves_null_target_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_path = tmp_path / "glm_null_targets.parquet"
    pl.DataFrame(
        {
            "feature": [1.0, 2.0, 3.0, 4.0],
            "unused": [10.0, 20.0, 30.0, 40.0],
            "target": [1.0, None, 0.0, 1.0],
        }
    ).write_parquet(data_path)
    job = TrainingJob(
        name="glm_null_terms",
        data=str(data_path),
        target="target",
        algorithm="glm",
        params={"family": "gaussian", "terms": {"feature": {"type": "linear"}}},
    )

    def assert_prepared_contract(self: TrainingJob, prepared, _report, **_kwargs):
        assert prepared.features == ["feature"]
        assert prepared.total_rows == 3
        assert prepared.target_null_count == 1
        raise RuntimeError("stop before fitting")

    monkeypatch.setattr(TrainingJob, "_split_data", assert_prepared_contract)

    with pytest.raises(RuntimeError, match="stop before fitting"):
        job.run()
