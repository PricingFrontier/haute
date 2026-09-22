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


def test_staged_scoring_uses_decoded_width_for_arrow_batch_rows(tmp_path, monkeypatch):
    from haute._execution_context import ExecutionContext, ExecutionProfile

    path = tmp_path / "wide.parquet"
    pl.DataFrame(
        {"feature_a": [1.0] * 50, "feature_b": [2.0] * 50, "wide": ["x" * 4096] * 50}
    ).write_parquet(path)
    context = ExecutionContext(operation="scoring_width", profile=ExecutionProfile.LAZY_SINK)
    monkeypatch.setattr(ExecutionContext, "remaining_memory_bytes", lambda _self: 512 * 1024)
    seen = []

    class Model(_SumModel):
        def predict(self, frame):
            seen.append(len(frame))
            return super().predict(frame)

    with context.stage("score"):
        result = scorer._score_batched_unified(
            Model(),
            pl.scan_parquet(path).filter(pl.col("feature_a") > 0),
            ["feature_a", "feature_b"],
            frozenset(),
            "pyfunc",
            "regression",
            "prediction",
        ).collect()
    assert result["prediction"].to_list() == [3.0] * 50
    assert seen and max(seen) <= 16


@pytest.mark.parametrize("derived", [False, True])
def test_scoring_cancellation_after_prediction_removes_output(tmp_path, monkeypatch, derived):
    from haute._execution_context import ExecutionCancelledError, ExecutionContext, ExecutionProfile

    lf, paths = _scan(tmp_path, multipart=False, empty=False)
    if derived:
        lf = lf.filter(pl.col("feature_a") > 0)
    context = ExecutionContext(operation="score_cancel", profile=ExecutionProfile.LAZY_SINK)
    monkeypatch.setattr(scorer, "_SCORE_BATCH_SIZE", 2)

    class Model(_SumModel):
        def predict(self, frame):
            prediction = super().predict(frame)
            context.cancel()
            return prediction

    model = Model()
    output = tmp_path / "cancelled.parquet"
    with pytest.raises(ExecutionCancelledError), context.stage("score"):
        with scorer.model_score_output_destination(output):
            scorer._score_batched_unified(
                model,
                lf,
                ["feature_a", "feature_b"],
                frozenset(),
                "pyfunc",
                "regression",
                "prediction",
            )
    assert model.calls == 1
    assert not output.exists()
    assert all(path.is_file() for path in paths)
