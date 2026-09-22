from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

import haute._model_scorer as scorer

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


class _SumModel:
    def __init__(self, fail_on: int | None = None) -> None:
        self.calls = 0
        self.fail_on = fail_on

    def predict(self, frame):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("predict failed")
        return (frame["feature_a"] + frame["feature_b"]).to_numpy()


def _scan(tmp_path: Path, *, multipart: bool, empty: bool) -> tuple[pl.LazyFrame, list[Path]]:
    frame = pl.DataFrame(
        {
            "quote_id": pl.Series([] if empty else ["q1", "q2", "q3"], dtype=pl.String),
            "feature_a": pl.Series([] if empty else [1.0, 2.0, 3.0], dtype=pl.Float64),
            "feature_b": pl.Series([] if empty else [10.0, 20.0, 30.0], dtype=pl.Float64),
            "unused": pl.Series([] if empty else [9, 8, 7], dtype=pl.Int64),
        }
    )
    paths = [tmp_path / "one.parquet"]
    if multipart:
        paths = [tmp_path / "one.parquet", tmp_path / "two.parquet"]
        frame.slice(0, 1).write_parquet(paths[0])
        frame.slice(1).write_parquet(paths[1])
    else:
        frame.write_parquet(paths[0])
    return pl.scan_parquet([str(path) for path in paths]), paths


@pytest.mark.parametrize("multipart", [False, True])
@pytest.mark.parametrize("projected", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_sliceable_parquet_scores_without_staging_and_preserves_schema(
    tmp_path: Path, multipart: bool, projected: bool, empty: bool
) -> None:
    lf, paths = _scan(tmp_path, multipart=multipart, empty=empty)
    projection = (
        scorer.ScoreWriteProjection(passthrough_columns=frozenset({"quote_id"}))
        if projected
        else None
    )
    old_size = scorer._SCORE_BATCH_SIZE
    scorer._SCORE_BATCH_SIZE = 2
    try:
        with patch(
            "haute._model_scorer._sink_to_temp", side_effect=AssertionError("must not stage")
        ):
            result = scorer._score_batched_unified(
                _SumModel(),
                lf,
                ["feature_a", "feature_b"],
                frozenset(),
                "pyfunc",
                "regression",
                "prediction",
                projection,
            ).collect()
    finally:
        scorer._SCORE_BATCH_SIZE = old_size
    assert all(path.exists() for path in paths)
    assert result.columns == (
        ["quote_id", "prediction"]
        if projected
        else ["quote_id", "feature_a", "feature_b", "unused", "prediction"]
    )
    assert result.schema["prediction"] == pl.Float64
    if not empty:
        assert result["prediction"].to_list() == [11.0, 22.0, 33.0]


def test_derived_input_stages_once_and_removes_owned_input(tmp_path: Path) -> None:
    lf, paths = _scan(tmp_path, multipart=False, empty=False)
    staged: list[Path] = []
    real_sink = scorer._sink_to_temp

    def record_sink(*args, **kwargs):
        path = Path(real_sink(*args, **kwargs))
        staged.append(path)
        return str(path)

    with patch("haute._model_scorer._sink_to_temp", side_effect=record_sink):
        result = scorer._score_batched_unified(
            _SumModel(),
            lf.filter(pl.col("feature_a") > 0),
            ["feature_a", "feature_b"],
            frozenset(),
            "pyfunc",
            "regression",
            "prediction",
        ).collect()
    assert result["prediction"].to_list() == [11.0, 22.0, 33.0]
    assert len(staged) == 1 and not staged[0].exists()
    assert all(path.exists() for path in paths)


@pytest.mark.parametrize("derived", [False, True])
def test_predict_failure_cleans_output_and_keeps_sources(tmp_path: Path, derived: bool) -> None:
    lf, paths = _scan(tmp_path, multipart=False, empty=False)
    if derived:
        lf = lf.filter(pl.col("feature_a") > 0)
    output = tmp_path / "failed.parquet"
    old_size = scorer._SCORE_BATCH_SIZE
    scorer._SCORE_BATCH_SIZE = 2
    try:
        with pytest.raises(RuntimeError, match="predict failed"):
            with scorer.model_score_output_destination(output):
                scorer._score_batched_unified(
                    _SumModel(fail_on=2),
                    lf,
                    ["feature_a", "feature_b"],
                    frozenset(),
                    "pyfunc",
                    "regression",
                    "prediction",
                ).collect()
    finally:
        scorer._SCORE_BATCH_SIZE = old_size
    assert not output.exists()
    assert all(path.exists() for path in paths)


@pytest.mark.parametrize("derived", [False, True])
def test_disk_refusal_on_second_score_batch_cleans_output_and_releases_input(
    tmp_path: Path,
    derived: bool,
) -> None:
    lf, paths = _scan(tmp_path, multipart=False, empty=False)
    if derived:
        lf = lf.filter(pl.col("feature_a") > 0)
    output = tmp_path / "disk-refused.parquet"
    staged: list[Path] = []
    real_sink = scorer._sink_to_temp

    def record_sink(*args, **kwargs):
        path = Path(real_sink(*args, **kwargs))
        staged.append(path)
        return str(path)

    checks = 0

    def refuse_second_table(*_args, **_kwargs) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise OSError(errno.ENOSPC, "insufficient disk space")

    old_size = scorer._SCORE_BATCH_SIZE
    scorer._SCORE_BATCH_SIZE = 2
    try:
        with (
            patch("haute._model_scorer.ensure_disk_headroom", side_effect=refuse_second_table),
            patch("haute._model_scorer._sink_to_temp", side_effect=record_sink),
            pytest.raises(OSError) as exc_info,
        ):
            with scorer.model_score_output_destination(output):
                scorer._score_batched_unified(
                    _SumModel(),
                    lf,
                    ["feature_a", "feature_b"],
                    frozenset(),
                    "pyfunc",
                    "regression",
                    "prediction",
                ).collect()
    finally:
        scorer._SCORE_BATCH_SIZE = old_size

    assert exc_info.value.errno == errno.ENOSPC
    assert checks == 2
    assert not output.exists()
    assert all(path.exists() for path in paths)
    assert all(not path.exists() for path in staged)
    paths[0].unlink()
    assert not paths[0].exists()
