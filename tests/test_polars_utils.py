"""Tests for haute._polars_utils."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._hashing import content_hash
from haute._polars_utils import (
    BOUNDED_MEMORY_EXEMPT_PROFILES,
    _malloc_trim,
    _streaming_sink_to_path,
    atomic_write,
    bounded_collect_batches,
    bounded_hashed_sink,
    bounded_sink,
    cancellable_streaming_collect,
    execution_collect,
    fanout_python_scan,
    hashed_streaming_sink,
    is_bounded_execution_profile,
    key_prefix_python_scan,
    limited_python_scan,
    normalise_execution_profile,
    read_parquet_metadata,
    row_local_python_scan,
    streaming_collect,
    temporary_streaming_chunk_size,
)

# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_bounded_profile_policy_has_one_shared_classification() -> None:
    assert BOUNDED_MEMORY_EXEMPT_PROFILES == {
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.DEPLOY_LIVE,
    }
    assert normalise_execution_profile("lazy_sink") is ExecutionProfile.LAZY_SINK
    assert is_bounded_execution_profile(ExecutionProfile.LAZY_SINK)
    assert not is_bounded_execution_profile(ExecutionProfile.PREVIEW_EAGER)
    assert not is_bounded_execution_profile(ExecutionProfile.DEPLOY_LIVE)
    assert not is_bounded_execution_profile(None)


def test_streaming_collect_uses_polars_streaming_engine() -> None:
    """streaming_collect is intentionally a no-fallback streaming collect."""
    captured: dict[str, object] = {}

    class Lazy:
        def collect(self, *, engine: str) -> pl.DataFrame:
            captured["engine"] = engine
            return pl.DataFrame({"x": [1]})

    result = streaming_collect(Lazy())  # type: ignore[arg-type]

    assert result["x"].to_list() == [1]
    assert captured == {"engine": "streaming"}


def test_execution_collect_rejects_unknown_engine() -> None:
    with pytest.raises(ValueError, match="engine must be 'auto', 'streaming' or 'in-memory'"):
        execution_collect(pl.LazyFrame({"x": [1]}), engine="gpu")  # type: ignore[arg-type]


def test_streaming_sink_requires_polars_to_return_a_lazy_plan(tmp_path: Path) -> None:
    class MissingPlan:
        def sink_csv(self, *_args: object, **_kwargs: object) -> None:
            return None

    with pytest.raises(RuntimeError, match="did not return the requested lazy sink plan"):
        _streaming_sink_to_path(  # type: ignore[arg-type]
            MissingPlan(),
            tmp_path / "out.csv",
            fmt="csv",
            compression="zstd",
        )


def test_temporary_streaming_chunk_size_restores_default_auto_state() -> None:
    """A scoped chunk size must not leak when Polars started in auto mode."""
    saved_config = pl.Config.save()
    try:
        pl.Config.restore_defaults()
        assert pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE") is None

        with temporary_streaming_chunk_size(12_345):
            assert pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE") == "12345"

        assert pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE") is None
    finally:
        pl.Config.load(saved_config)


def test_streaming_collect_records_collect_metric_on_active_context_stage() -> None:
    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1_000,
    )

    with context.stage("row_count", node_id="sink"):
        result = streaming_collect(
            pl.LazyFrame({"x": [1]}),
            execution_context=context,
        )

    assert result["x"].to_list() == [1]
    metric = context.metrics.snapshot()[0]
    assert metric.n_collects == 1
    assert metric.to_summary().to_dict()["n_collects"] == 1


def test_streaming_collect_uses_cancellable_background_query_with_active_context() -> None:
    class Query:
        def fetch(self) -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        def cancel(self) -> None:
            raise AssertionError("completed query must not be cancelled")

    captured: dict[str, object] = {}

    class Lazy:
        def collect(self, **kwargs: object) -> Query:
            captured.update(kwargs)
            return Query()

    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1,
    )

    result = streaming_collect(
        Lazy(),  # type: ignore[arg-type]
        execution_context=context,
    )

    assert result.to_dict(as_series=False) == {"x": [1]}
    assert captured == {"engine": "streaming", "background": True}


def test_streaming_collect_fault_point_runs_immediately_before_native_collect() -> None:
    timeline: list[str] = []

    class Query:
        def fetch(self) -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

    class Lazy:
        def collect(self, *, engine: str, background: bool) -> Query:
            assert engine == "streaming"
            assert background is True
            timeline.append("native")
            return Query()

    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        fault_injector=lambda point: timeline.append(point.name),
    )

    streaming_collect(
        Lazy(),  # type: ignore[arg-type]
        execution_context=context,
    )

    assert timeline == [
        "streaming_collect_before_native",
        "collect_before_native",
        "native",
    ]


def test_streaming_collect_fault_prevents_native_operation() -> None:
    class Lazy:
        def collect(self, *, engine: str) -> pl.DataFrame:
            raise AssertionError(f"native collect unexpectedly ran with {engine}")

    def inject(_point) -> None:
        raise RuntimeError("collect fault")

    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        fault_injector=inject,
    )

    with pytest.raises(RuntimeError, match="collect fault"):
        streaming_collect(
            Lazy(),  # type: ignore[arg-type]
            execution_context=context,
        )

    assert context.metrics_payload()["n_collects"] == 0


def test_streaming_collect_preserves_non_streaming_data_errors() -> None:
    """Data validation failures must not be mislabeled as streaming incompatibility."""

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise pl.exceptions.InvalidOperationError("conversion from str to f64 failed")

    with pytest.raises(pl.exceptions.InvalidOperationError, match="conversion"):
        streaming_collect(Lazy())  # type: ignore[arg-type]


def test_streaming_collect_preserves_generic_unsupported_data_errors() -> None:
    """Generic unsupported-operation errors are not necessarily streaming failures."""

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise pl.exceptions.InvalidOperationError("operation not supported for dtype date")

    with pytest.raises(pl.exceptions.InvalidOperationError, match="not supported"):
        streaming_collect(Lazy())  # type: ignore[arg-type]


def test_streaming_collect_preserves_execution_cancellation() -> None:
    """Execution cancellation must not be wrapped as a streaming incompatibility."""
    cancellation = ExecutionCancelledError("pipeline_sink", job_id="job-1")

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise cancellation

    with pytest.raises(ExecutionCancelledError) as exc_info:
        streaming_collect(Lazy())  # type: ignore[arg-type]

    assert exc_info.value is cancellation


def test_cancellable_streaming_collect_cancels_native_query_on_checkpoint_failure() -> None:
    cancellation = ExecutionCancelledError("explore_cache")

    class Query:
        cancelled = False
        fetch_calls = 0

        def fetch(self) -> None:
            self.fetch_calls += 1

        def cancel(self) -> None:
            self.cancelled = True

    class Lazy:
        collect_kwargs: dict[str, object] | None = None

        def __init__(self, query: Query) -> None:
            self.query = query

        def collect(self, **kwargs) -> Query:
            self.collect_kwargs = kwargs
            return self.query

    class CancellingContext:
        recorded_collects = 0
        checkpoint_calls = 0

        def fault_point(self, *args, **kwargs) -> None:
            pass

        def record_collect(self) -> None:
            self.recorded_collects += 1

        def checkpoint(self, *args, **kwargs) -> None:
            self.checkpoint_calls += 1
            if self.checkpoint_calls > 1:
                raise cancellation

    query = Query()
    lazy_frame = Lazy(query)
    context = CancellingContext()

    with pytest.raises(ExecutionCancelledError) as exc_info:
        cancellable_streaming_collect(
            lazy_frame,  # type: ignore[arg-type]
            execution_context=context,  # type: ignore[arg-type]
        )

    assert exc_info.value is cancellation
    assert lazy_frame.collect_kwargs == {"engine": "streaming", "background": True}
    assert query.fetch_calls == 1
    assert query.cancelled is True
    assert context.recorded_collects == 1
    assert context.checkpoint_calls == 2


def test_cancellable_streaming_collect_preserves_native_fetch_failure() -> None:
    failure = RuntimeError("native background query failed")

    class Query:
        cancelled = False

        def fetch(self) -> None:
            raise failure

        def cancel(self) -> None:
            self.cancelled = True

    class Lazy:
        def __init__(self, query: Query) -> None:
            self.query = query

        def collect(self, **kwargs) -> Query:
            assert kwargs == {"engine": "streaming", "background": True}
            return self.query

    context = ExecutionContext(
        operation="explore",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
    )
    query = Query()

    with pytest.raises(RuntimeError) as exc_info:
        cancellable_streaming_collect(
            Lazy(query),  # type: ignore[arg-type]
            execution_context=context,
        )

    assert exc_info.value is failure
    assert query.cancelled is False
    assert context.metrics_payload()["n_collects"] == 1


@pytest.mark.parametrize("poll_seconds", [0, -0.01, float("nan"), float("inf"), True])
def test_cancellable_streaming_collect_rejects_invalid_poll_interval(
    poll_seconds: float,
) -> None:
    context = ExecutionContext(
        operation="explore",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
    )

    with pytest.raises(ValueError, match="positive finite"):
        cancellable_streaming_collect(
            pl.LazyFrame({"x": [1]}),
            execution_context=context,
            poll_seconds=poll_seconds,
        )


def test_streaming_collect_preserves_execution_memory_limit() -> None:
    """Execution memory failures should keep their typed payload intact."""
    memory_error = ExecutionMemoryLimitExceededError(
        "pipeline_sink",
        rss_bytes=600,
        limit_bytes=512,
        baseline_rss_bytes=1,
        rss_limit_bytes=513,
        job_id="job-1",
    )

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise memory_error

    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        streaming_collect(Lazy())  # type: ignore[arg-type]

    assert exc_info.value is memory_error


def test_bounded_collect_batches_streams_ordered_chunks_of_the_real_query() -> None:
    batches = list(
        bounded_collect_batches(
            pl.LazyFrame({"x": list(range(10))}).with_columns(y=pl.col("x") * 2),
            chunk_size=3,
            maintain_order=True,
        )
    )

    assert [batch["x"].to_list() for batch in batches] == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]
    assert pl.concat(batches)["y"].to_list() == [value * 2 for value in range(10)]


def test_bounded_collect_batches_records_only_real_batch_stages() -> None:
    context = ExecutionContext(
        operation="chunked",
        profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        memory_sampler=lambda: 1_000,
    )

    batches = list(
        bounded_collect_batches(
            pl.LazyFrame({"x": [1, 2]}),
            chunk_size=1,
            maintain_order=True,
            execution_context=context,
            stage_name="batch_collect",
        )
    )

    assert [batch["x"].to_list() for batch in batches] == [[1], [2]]
    stages = context.metrics.snapshot()
    assert [stage.name for stage in stages] == ["batch_collect", "batch_collect"]
    assert [stage.n_collects for stage in stages] == [1, 1]
    # One query counts the rows; each batch is one more.
    assert context.metrics_summary().n_collects == 3


def test_bounded_collect_batches_shrinks_a_sliceable_wide_source_under_budget(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wide.parquet"
    pl.DataFrame({"x": list(range(3_000))}).write_parquet(path)
    frame = pl.scan_parquet(path).with_columns(wide=pl.lit("x" * 4096))
    context = ExecutionContext(
        operation="wide-batches",
        profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        memory_limit_bytes=8 * 1024 * 1024,
        memory_sampler=lambda: 0,
    )

    batches = list(bounded_collect_batches(frame, chunk_size=3_000, execution_context=context))

    assert max(batch.height for batch in batches) < 3_000
    assert pl.concat(batches).equals(frame.collect())


def test_bounded_collect_batches_raises_the_engine_error() -> None:
    query = pl.LazyFrame({"x": ["1", "not a number"]}).select(pl.col("x").str.to_integer())

    with pytest.raises(pl.exceptions.ComputeError, match="not a number"):
        list(bounded_collect_batches(query, chunk_size=5))


def test_bounded_collect_batches_raises_an_engine_panic_instead_of_ending_early() -> None:
    """Polars' own ``collect_batches`` ends its stream as though exhausted when
    the engine panics, which would read as an empty result."""

    def panicking_udf(frame: pl.DataFrame) -> pl.DataFrame:
        raise pl.exceptions.PanicException("engine panic")

    query = pl.LazyFrame({"x": [1, 2, 3]}).map_batches(
        panicking_udf, schema=pl.Schema({"x": pl.Int64})
    )

    with pytest.raises(pl.exceptions.PanicException, match="engine panic"):
        list(bounded_collect_batches(query, chunk_size=1))


def _counting_collects(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the height of every frame ``bounded_collect_batches`` collects."""
    import haute._polars_utils as polars_utils

    heights: list[int] = []
    real = polars_utils.execution_collect

    def counting(lf: pl.LazyFrame, **kwargs: object) -> pl.DataFrame:
        frame = real(lf, **kwargs)  # type: ignore[arg-type]
        heights.append(frame.height)
        return frame

    monkeypatch.setattr(polars_utils, "execution_collect", counting)
    return heights


def _staging_dirs(monkeypatch: pytest.MonkeyPatch, root: Path) -> list[Path]:
    """Route the staged strategy's temporary directories under *root*."""
    import tempfile

    made: list[Path] = []
    real = tempfile.mkdtemp

    def mkdtemp(*args: object, **kwargs: object) -> str:
        kwargs["dir"] = str(root)
        path = real(*args, **kwargs)  # type: ignore[call-overload]
        made.append(Path(path))
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    return made


def _parquet(tmp_path: Path, frame: pl.DataFrame) -> pl.LazyFrame:
    path = tmp_path / "input.parquet"
    frame.write_parquet(path)
    return pl.scan_parquet(path)


# -- sliced: the frame can be sliced at its input, so each batch is one slice --


def test_sliced_batches_issue_one_query_per_consumed_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heights = _counting_collects(monkeypatch)
    frame = _parquet(tmp_path, pl.DataFrame({"x": list(range(10))})).with_columns(y=pl.col("x") * 2)

    batches = bounded_collect_batches(frame, chunk_size=3)
    consumed = [next(batches), next(batches)]

    # The row count, then exactly the two batches the consumer asked for.
    assert heights == [1, 3, 3]
    assert [batch["x"].to_list() for batch in consumed] == [[0, 1, 2], [3, 4, 5]]
    assert [batch["x"].to_list() for batch in batches] == [[6, 7, 8], [9]]
    assert heights == [1, 3, 3, 3, 1]


def test_sliced_batches_deliver_before_a_later_failure(tmp_path: Path) -> None:
    frame = _parquet(tmp_path, pl.DataFrame({"x": ["1", "2", "3", "4", "bad"]})).select(
        pl.col("x").str.to_integer()
    )
    delivered: list[list[int]] = []

    with pytest.raises(pl.exceptions.ComputeError, match="bad"):
        for batch in bounded_collect_batches(frame, chunk_size=2):
            delivered.append(batch["x"].to_list())

    assert delivered == [[1, 2], [3, 4]]


def test_sliced_batches_stop_reading_when_closed_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heights = _counting_collects(monkeypatch)
    batches = bounded_collect_batches(
        _parquet(tmp_path, pl.DataFrame({"x": list(range(1_000))})), chunk_size=1
    )

    assert next(batches)["x"].to_list() == [0]
    batches.close()

    # The row count and the one batch; closing reads nothing more.
    assert heights == [1, 1]


# -- derived in-memory plans are staged too: their output can expand --


def test_derived_in_memory_batches_stage_without_collecting_the_full_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heights = _counting_collects(monkeypatch)
    staged = _staging_dirs(monkeypatch, tmp_path)
    frame = (
        pl.LazyFrame({"k": [3, 1, 2, 1], "v": [1, 2, 3, 4]})
        .group_by("k")
        .agg(pl.col("v").sum())
        .sort("k")
    )

    batches = list(bounded_collect_batches(frame, chunk_size=2))

    assert max(heights) <= 2
    assert len(staged) == 1
    assert not staged[0].exists()
    assert pl.concat(batches).to_dict(as_series=False) == {"k": [1, 2, 3], "v": [6, 3, 1]}


def test_resident_cross_join_batches_never_collect_the_expanded_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heights = _counting_collects(monkeypatch)
    staged = _staging_dirs(monkeypatch, tmp_path)
    frame = pl.LazyFrame({"a": range(20)}).join(pl.LazyFrame({"b": range(30)}), how="cross")
    batches = bounded_collect_batches(frame, chunk_size=10)
    assert next(batches).height == 10
    batches.close()
    assert max(heights) <= 10
    assert len(staged) == 1
    assert not staged[0].exists()


# -- staged: anything else is written once, then sliced --


def _io_source(values: list[int], *, fail_after: int | None = None) -> pl.LazyFrame:
    from polars.io.plugins import register_io_source

    def source(
        with_columns: list[str] | None,
        predicate: pl.Expr | None,
        n_rows: int | None,
        batch_size: int | None,
    ):
        del with_columns, predicate, n_rows, batch_size
        for index, value in enumerate(values):
            if fail_after is not None and index == fail_after:
                raise pl.exceptions.PanicException("panic in the source")
            yield pl.DataFrame({"x": [value]})

    return register_io_source(source, schema=pl.Schema({"x": pl.Int64}))


def test_staged_batches_raise_before_any_batch_and_remove_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = _staging_dirs(monkeypatch, tmp_path)
    delivered: list[list[int]] = []

    with pytest.raises(pl.exceptions.PanicException):
        for batch in bounded_collect_batches(_io_source([1, 2, 3], fail_after=2), chunk_size=1):
            delivered.append(batch["x"].to_list())

    # A frame that cannot be sliced is written out before its first batch, so
    # its failure comes before any batch, and nothing is left behind.
    assert delivered == []
    assert len(staged) == 1
    assert not staged[0].exists()


def test_staged_batches_remove_staging_on_close_and_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = _staging_dirs(monkeypatch, tmp_path)

    closed_early = bounded_collect_batches(_io_source(list(range(20))), chunk_size=3)
    assert next(closed_early)["x"].to_list() == [0, 1, 2]
    assert len(staged) == 1 and staged[0].exists()
    closed_early.close()
    assert not staged[0].exists()

    exhausted = list(bounded_collect_batches(_io_source(list(range(7))), chunk_size=3))
    assert [batch["x"].to_list() for batch in exhausted] == [[0, 1, 2], [3, 4, 5], [6]]
    assert len(staged) == 2
    assert not staged[1].exists()


def test_bounded_collect_batches_restores_a_parked_python_scan_failure() -> None:
    class TransformFailedError(RuntimeError):
        pass

    def failing_transform(frame: pl.DataFrame) -> pl.DataFrame:
        raise TransformFailedError("typed failure")

    scan = row_local_python_scan(
        pl.LazyFrame({"x": [1, 2]}),
        failing_transform,
        schema=pl.Schema({"x": pl.Int64, "y": pl.Int64}),
        generated_columns=("y",),
        required_input_columns=("x",),
        input_predicates_allowed=True,
        elide_transform_when_unused=False,
    )

    with pytest.raises(TransformFailedError, match="typed failure"):
        list(bounded_collect_batches(scan, chunk_size=1))


def test_bounded_collect_batches_runs_the_query_in_the_caller_context() -> None:
    context = ExecutionContext(
        operation="chunked",
        profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        memory_sampler=lambda: 1_000,
    )
    seen: list[ExecutionContext | None] = []
    real_collect = pl.LazyFrame.collect

    def spying_collect(self: pl.LazyFrame, *args: object, **kwargs: object) -> object:
        from haute._execution_context import current_execution_context

        seen.append(current_execution_context())
        return real_collect(self, *args, **kwargs)  # type: ignore[arg-type]

    with (
        context.stage("caller"),
        patch.object(pl.LazyFrame, "collect", spying_collect),
    ):
        batches = list(bounded_collect_batches(pl.LazyFrame({"x": [1]}), chunk_size=1))

    assert [batch["x"].to_list() for batch in batches] == [[1]]
    assert seen and all(entry is context for entry in seen)


def test_streaming_collect_with_active_context_preserves_unverified_error() -> None:
    original = pl.exceptions.ComputeError("streaming collect failed")

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise original

    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_BATCH,
        memory_sampler=lambda: 1_000,
    )

    with (
        context.stage("collect"),
        pytest.raises(pl.exceptions.ComputeError) as exc_info,
    ):
        streaming_collect(
            Lazy(),  # type: ignore[arg-type]
            execution_context=context,
        )

    assert exc_info.value is original


def test_bounded_sink_writes_parquet(tmp_path: Path):
    """Happy path: LazyFrame -> sink_parquet -> read back matches."""
    lf = pl.LazyFrame({"x": [10, 20, 30], "y": ["a", "b", "c"]})
    out = tmp_path / "out.parquet"

    bounded_sink(lf, out)

    result = pl.read_parquet(out)
    assert result.shape == (3, 2)
    assert result["x"].to_list() == [10, 20, 30]
    assert result["y"].to_list() == ["a", "b", "c"]


def test_bounded_sink_writes_csv(tmp_path: Path):
    """Happy path with fmt='csv'."""
    lf = pl.LazyFrame({"a": [1, 2], "b": [3.5, 4.5]})
    out = tmp_path / "out.csv"

    bounded_sink(lf, out, fmt="csv")

    result = pl.read_csv(out)
    assert result.shape == (2, 2)
    assert result["a"].to_list() == [1, 2]
    assert result["b"].to_list() == [3.5, 4.5]


def test_bounded_sink_emits_fault_points_around_native_sink(tmp_path: Path) -> None:
    points: list[str] = []
    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        fault_injector=lambda point: points.append(point.name),
    )
    out = tmp_path / "out.parquet"

    with context.stage("sink"):
        bounded_sink(pl.LazyFrame({"x": [1]}), out)

    assert points[:3] == [
        "sink_before_native",
        "streaming_collect_before_native",
        "collect_before_native",
    ]
    assert set(points[3:-1]) <= {"streaming_collect_poll"}
    assert points[-1] == "sink_after_native"
    assert out.exists()


def test_bounded_sink_materialises_a_lazy_sink_as_a_background_query(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    sink_target: Path | None = None

    class Query:
        def fetch(self) -> pl.DataFrame:
            assert sink_target is not None
            # ``sink_target`` is the tmp_path-derived path passed to bounded_sink.
            sink_target.write_bytes(b"completed")  # write-sandbox: deliberate
            return pl.DataFrame()

        def cancel(self) -> None:
            raise AssertionError("completed sink must not be cancelled")

    class SinkPlan:
        def collect(self, **kwargs: object) -> Query:
            captured["collect"] = kwargs
            return Query()

    def fake_sink(_lf: pl.LazyFrame, path: str | Path, **kwargs: object) -> SinkPlan:
        nonlocal sink_target
        sink_target = Path(path)
        captured["sink"] = kwargs
        return SinkPlan()

    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1,
    )
    out = tmp_path / "out.parquet"

    with (
        patch.object(pl.LazyFrame, "sink_parquet", autospec=True, side_effect=fake_sink),
        context.stage("sink"),
    ):
        bounded_sink(pl.LazyFrame({"x": [1]}), out)

    assert captured["sink"] == {
        "compression": "zstd",
        "lazy": True,
        "engine": "streaming",
    }
    assert captured["collect"] == {"engine": "streaming", "background": True}
    assert out.read_bytes() == b"completed"


def test_bounded_sink_cancels_native_query_and_does_not_publish_partial_file(
    tmp_path: Path,
) -> None:
    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1,
    )
    cancelled = False

    class Query:
        def fetch(self) -> None:
            context.cancellation_token.cancel()
            return None

        def cancel(self) -> None:
            nonlocal cancelled
            cancelled = True

    class SinkPlan:
        def collect(self, **_kwargs: object) -> Query:
            return Query()

    out = tmp_path / "cancelled.parquet"
    with (
        patch.object(pl.LazyFrame, "sink_parquet", return_value=SinkPlan()),
        context.stage("sink"),
        pytest.raises(ExecutionCancelledError),
    ):
        bounded_sink(pl.LazyFrame({"x": [1]}), out)

    assert cancelled is True
    assert not out.exists()
    assert not out.with_suffix(".parquet.tmp").exists()


# ---------------------------------------------------------------------------
# Native sink error propagation (parquet & csv, parametrized)
# ---------------------------------------------------------------------------

_POLARS_FALLBACK_ERRORS = [
    pl.exceptions.ComputeError,
    pl.exceptions.SchemaError,
    pl.exceptions.InvalidOperationError,
]


@pytest.mark.parametrize("error_cls", _POLARS_FALLBACK_ERRORS)
def test_bounded_sink_raises_typed_error_without_collect_fallback(
    tmp_path: Path,
    error_cls: type[BaseException],
) -> None:
    """A native sink error propagates without materialising the LazyFrame."""
    lf = pl.LazyFrame({"a": [1, 2, 3]})
    out = tmp_path / "test.parquet"

    original = error_cls("streaming sink failed")
    with (
        patch.object(
            pl.LazyFrame,
            "sink_parquet",
            side_effect=original,
        ),
        patch.object(pl.LazyFrame, "collect", autospec=True) as collect_mock,
    ):
        with pytest.raises(error_cls) as exc_info:
            bounded_sink(lf, out)

    assert exc_info.value is original
    collect_mock.assert_not_called()
    assert not out.exists()
    assert not out.with_suffix(".parquet.tmp").exists()


def test_bounded_sink_preserves_non_streaming_polars_errors(tmp_path: Path) -> None:
    """Data/schema errors must not be mislabeled as bounded streaming failures."""
    lf = pl.LazyFrame({"a": [1]})
    out = tmp_path / "test.parquet"

    with patch.object(
        pl.LazyFrame,
        "sink_parquet",
        side_effect=pl.exceptions.SchemaError("column not found"),
    ):
        with pytest.raises(pl.exceptions.SchemaError, match="column not found"):
            bounded_sink(lf, out)


def test_bounded_sink_preserves_non_streaming_compute_errors(tmp_path: Path) -> None:
    """Compute errors are only bounded-memory errors when they mention streaming."""
    lf = pl.LazyFrame({"a": [1]})
    out = tmp_path / "test.parquet"

    with patch.object(
        pl.LazyFrame,
        "sink_parquet",
        side_effect=pl.exceptions.ComputeError("division by zero"),
    ):
        with pytest.raises(pl.exceptions.ComputeError, match="division by zero"):
            bounded_sink(lf, out)


def test_bounded_sink_preserves_unverified_streaming_compute_error(
    tmp_path: Path,
) -> None:
    """Streaming ComputeErrors are bounded sink failures, not broad collects."""
    lf = pl.LazyFrame({"a": [1]})
    out = tmp_path / "test.parquet"

    original = pl.exceptions.ComputeError("streaming sink failed")
    with (
        patch.object(
            pl.LazyFrame,
            "sink_parquet",
            side_effect=original,
        ),
        patch.object(pl.LazyFrame, "collect", autospec=True) as collect_mock,
    ):
        with pytest.raises(pl.exceptions.ComputeError) as exc_info:
            bounded_sink(lf, out)

    assert exc_info.value is original
    collect_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Non-Polars errors must propagate
# ---------------------------------------------------------------------------


def test_bounded_sink_real_error_propagates(tmp_path: Path):
    """PermissionError from the native sink propagates unchanged."""
    lf = pl.LazyFrame({"a": [1]})
    out = tmp_path / "test.parquet"

    with patch.object(
        pl.LazyFrame,
        "sink_parquet",
        side_effect=PermissionError("permission denied"),
    ):
        with pytest.raises(PermissionError, match="permission denied"):
            bounded_sink(lf, out)


# ---------------------------------------------------------------------------
# _malloc_trim
# ---------------------------------------------------------------------------


def test_malloc_trim_does_not_raise():
    """_malloc_trim must never raise regardless of platform."""
    result = _malloc_trim()
    # _malloc_trim should return None (it's a void helper)
    assert result is None


class TestMallocTrimDispatch:
    """Verify _malloc_trim calls the correct platform API."""

    def test_linux_calls_glibc_malloc_trim(self, monkeypatch):
        from unittest.mock import MagicMock

        mock_cdll = MagicMock()
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("ctypes.CDLL", mock_cdll)
        _malloc_trim()
        mock_cdll.assert_called_once_with("libc.so.6")
        mock_cdll.return_value.malloc_trim.assert_called_once_with(0)

    def test_windows_calls_heap_compact(self, monkeypatch):
        import ctypes
        from unittest.mock import MagicMock

        mock_kernel32 = MagicMock()
        mock_kernel32.GetProcessHeap.return_value = 12345
        mock_windll = MagicMock(kernel32=mock_kernel32)
        monkeypatch.setattr("sys.platform", "win32")
        if not hasattr(ctypes, "windll"):
            monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)
        else:
            monkeypatch.setattr("ctypes.windll", mock_windll)
        _malloc_trim()
        mock_kernel32.GetProcessHeap.assert_called_once()
        mock_kernel32.HeapCompact.assert_called_once_with(12345, 0)

    def test_macos_is_noop(self, monkeypatch):
        """macOS has no native heap compaction — verify no ctypes calls."""
        from unittest.mock import MagicMock

        mock_cdll_cls = MagicMock()
        mock_cdll_inst = MagicMock()
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("ctypes.CDLL", mock_cdll_cls)
        monkeypatch.setattr("ctypes.cdll", mock_cdll_inst)
        _malloc_trim()
        mock_cdll_cls.assert_not_called()
        mock_cdll_inst.assert_not_called()

    def test_linux_graceful_on_oserror(self, monkeypatch):
        """If libc.so.6 can't be loaded, _malloc_trim must not raise."""
        from unittest.mock import MagicMock

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("ctypes.CDLL", MagicMock(side_effect=OSError))
        _malloc_trim()  # should not raise

    def test_windows_graceful_on_attribute_error(self, monkeypatch):
        """If kernel32.HeapCompact is missing, _malloc_trim must not raise."""
        import ctypes
        from unittest.mock import MagicMock, PropertyMock

        mock_windll = MagicMock()
        type(mock_windll).kernel32 = PropertyMock(side_effect=AttributeError)
        monkeypatch.setattr("sys.platform", "win32")
        if not hasattr(ctypes, "windll"):
            monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)
        else:
            monkeypatch.setattr("ctypes.windll", mock_windll)
        _malloc_trim()  # should not raise


# ---------------------------------------------------------------------------
# atomic_write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    """Tests for the atomic_write context manager."""

    def test_happy_path_creates_file(self, tmp_path: Path):
        """On success, the destination file exists and temp file does not."""
        dest = tmp_path / "out.parquet"
        with atomic_write(dest) as tmp:
            pl.DataFrame({"x": [1, 2, 3]}).write_parquet(tmp, compression="zstd")

        assert dest.exists()
        assert not tmp.exists()
        result = pl.read_parquet(dest)
        assert result["x"].to_list() == [1, 2, 3]

    def test_creates_parent_dirs(self, tmp_path: Path):
        """Parent directories are created automatically."""
        dest = tmp_path / "sub" / "dir" / "out.parquet"
        with atomic_write(dest) as tmp:
            pl.DataFrame({"a": [1]}).write_parquet(tmp)
        assert dest.exists()

    def test_cleans_up_on_error(self, tmp_path: Path):
        """On exception, temp file is removed and destination does not exist."""
        dest = tmp_path / "out.parquet"
        with pytest.raises(ValueError, match="boom"):
            with atomic_write(dest) as tmp:
                pl.DataFrame({"a": [1]}).write_parquet(tmp)
                raise ValueError("boom")

        assert not dest.exists()
        assert not tmp.exists()

    def test_temp_suffix(self, tmp_path: Path):
        """The temp path has .parquet.tmp suffix."""
        dest = tmp_path / "out.parquet"
        with atomic_write(dest) as tmp:
            assert tmp.suffix == ".tmp"
            assert tmp.stem == "out.parquet"
            pl.DataFrame({"a": [1]}).write_parquet(tmp)

    def test_overwrite_existing(self, tmp_path: Path):
        """atomic_write can overwrite an existing destination file."""
        dest = tmp_path / "out.parquet"
        pl.DataFrame({"old": [1]}).write_parquet(dest)

        with atomic_write(dest) as tmp:
            pl.DataFrame({"new": [99]}).write_parquet(tmp)

        result = pl.read_parquet(dest)
        assert "new" in result.columns
        assert result["new"].to_list() == [99]


# ---------------------------------------------------------------------------
# read_parquet_metadata
# ---------------------------------------------------------------------------


class TestReadParquetMetadata:
    """Tests for the read_parquet_metadata helper."""

    def test_returns_correct_metadata(self, tmp_path: Path):
        """Metadata matches the written file's schema and row count."""
        p = tmp_path / "test.parquet"
        df = pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        df.write_parquet(p, compression="zstd")

        meta = read_parquet_metadata(p)
        assert meta["row_count"] == 3
        assert meta["column_count"] == 2
        assert "x" in meta["columns"]
        assert "y" in meta["columns"]
        assert meta["size_bytes"] > 0
        assert meta["uncompressed_size_bytes"] > 0
        assert meta["compressed_size_bytes"] > 0
        assert set(meta["column_uncompressed_size_bytes"]) == {"x", "y"}
        assert set(meta["column_compressed_size_bytes"]) == {"x", "y"}
        assert (
            sum(meta["column_uncompressed_size_bytes"].values()) == meta["uncompressed_size_bytes"]
        )
        assert sum(meta["column_compressed_size_bytes"].values()) == meta["compressed_size_bytes"]
        assert meta["mtime"] > 0

    def test_empty_dataframe(self, tmp_path: Path):
        """Works for an empty parquet file."""
        p = tmp_path / "empty.parquet"
        pl.DataFrame({"a": pl.Series([], dtype=pl.Int64)}).write_parquet(p)

        meta = read_parquet_metadata(p)
        assert meta["row_count"] == 0
        assert meta["column_count"] == 1
        assert "a" in meta["columns"]

    def test_nonexistent_file_raises(self, tmp_path: Path):
        """FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            read_parquet_metadata(tmp_path / "nope.parquet")

    def test_multiple_column_types(self, tmp_path: Path):
        p = tmp_path / "multi.parquet"
        df = pl.DataFrame(
            {
                "int_col": [1, 2],
                "float_col": [1.5, 2.5],
                "str_col": ["a", "b"],
                "bool_col": [True, False],
            }
        )
        df.write_parquet(p)

        meta = read_parquet_metadata(p)
        assert meta["row_count"] == 2
        assert meta["column_count"] == 4
        assert set(meta["columns"].keys()) == {"int_col", "float_col", "str_col", "bool_col"}

    def test_empty_multi_column(self, tmp_path: Path):
        p = tmp_path / "empty_multi.parquet"
        df = pl.DataFrame(
            {
                "a": pl.Series([], dtype=pl.Int64),
                "b": pl.Series([], dtype=pl.Utf8),
                "c": pl.Series([], dtype=pl.Float64),
            }
        )
        df.write_parquet(p)

        meta = read_parquet_metadata(p)
        assert meta["row_count"] == 0
        assert meta["column_count"] == 3

    def test_size_bytes_and_mtime_populated(self, tmp_path: Path):
        p = tmp_path / "check.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(p)

        meta = read_parquet_metadata(p)
        assert isinstance(meta["size_bytes"], int)
        assert meta["size_bytes"] > 0
        assert isinstance(meta["uncompressed_size_bytes"], int)
        assert meta["uncompressed_size_bytes"] > 0
        assert isinstance(meta["compressed_size_bytes"], int)
        assert meta["compressed_size_bytes"] > 0
        assert isinstance(meta["mtime"], float)
        assert meta["mtime"] > 0


# ---------------------------------------------------------------------------
# bounded_sink edge cases
# ---------------------------------------------------------------------------


class TestBoundedSinkEdgeCases:
    def test_fast_checkpoint_uses_lz4(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": list(range(1000))})
        lz4_path = tmp_path / "lz4.parquet"
        zstd_path = tmp_path / "zstd.parquet"

        bounded_sink(lf, lz4_path, fast_checkpoint=True)
        bounded_sink(lf, zstd_path, fast_checkpoint=False)

        assert lz4_path.exists()
        assert zstd_path.exists()
        lz4_size = lz4_path.stat().st_size
        zstd_size = zstd_path.stat().st_size
        assert lz4_size != zstd_size

    def test_fast_checkpoint_false_uses_zstd(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": [1, 2, 3]})
        out = tmp_path / "zstd.parquet"
        bounded_sink(lf, out, fast_checkpoint=False)

        import pyarrow.parquet as pq

        meta = pq.read_metadata(str(out))
        compression = meta.row_group(0).column(0).compression
        assert compression.lower() == "zstd"

    def test_fast_checkpoint_true_uses_lz4_compression(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": [1, 2, 3]})
        out = tmp_path / "lz4.parquet"
        bounded_sink(lf, out, fast_checkpoint=True)

        import pyarrow.parquet as pq

        meta = pq.read_metadata(str(out))
        compression = meta.row_group(0).column(0).compression
        assert compression.lower() == "lz4"

    def test_csv_write_and_read_back(self, tmp_path: Path):
        lf = pl.LazyFrame({"name": ["alice", "bob"], "age": [30, 25]})
        out = tmp_path / "test.csv"
        bounded_sink(lf, out, fmt="csv")

        result = pl.read_csv(out)
        assert result.shape == (2, 2)
        assert result["name"].to_list() == ["alice", "bob"]
        assert result["age"].to_list() == [30, 25]

    def test_empty_lazyframe(self, tmp_path: Path):
        lf = pl.LazyFrame(
            {
                "a": pl.Series([], dtype=pl.Int64),
                "b": pl.Series([], dtype=pl.Utf8),
                "c": pl.Series([], dtype=pl.Float64),
            }
        )
        out = tmp_path / "empty.parquet"
        bounded_sink(lf, out)

        result = pl.read_parquet(out)
        assert result.shape == (0, 3)
        assert result.columns == ["a", "b", "c"]

    def test_parent_directory_creatable(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "c" / "out.parquet"
        nested.parent.mkdir(parents=True, exist_ok=True)
        lf = pl.LazyFrame({"x": [1]})
        bounded_sink(lf, nested)

        assert nested.exists()
        assert pl.read_parquet(nested)["x"].to_list() == [1]

    def test_non_polars_error_propagates(self, tmp_path: Path):
        lf = pl.LazyFrame({"a": [1]})
        out = tmp_path / "test.parquet"

        with patch.object(
            pl.LazyFrame,
            "sink_parquet",
            side_effect=RuntimeError("unexpected"),
        ):
            with pytest.raises(RuntimeError, match="unexpected"):
                bounded_sink(lf, out)

    def test_oserror_propagates(self, tmp_path: Path):
        lf = pl.LazyFrame({"a": [1]})
        out = tmp_path / "test.parquet"

        with patch.object(
            pl.LazyFrame,
            "sink_parquet",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                bounded_sink(lf, out)

    def test_path_as_string(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": [1, 2]})
        out = str(tmp_path / "string_path.parquet")
        bounded_sink(lf, out)

        result = pl.read_parquet(out)
        assert result["x"].to_list() == [1, 2]

    def test_path_as_path_object(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": [3, 4]})
        out = tmp_path / "path_obj.parquet"
        bounded_sink(lf, out)

        result = pl.read_parquet(out)
        assert result["x"].to_list() == [3, 4]

    def test_nonexistent_parent_skips_atomic_write(self, tmp_path: Path):
        """A missing parent directory is surfaced by the bounded sink.
        (no atomic_write wrapper) — this exercises the else branch at line 166."""
        lf = pl.LazyFrame({"x": [1, 2]})
        # Create a path whose parent does NOT exist
        out = tmp_path / "nonexistent_dir" / "out.parquet"
        assert not out.parent.exists()

        # sink_parquet will fail because dir doesn't exist — that's expected.
        # The important thing is it goes through _do_sink(path) not atomic_write.
        with pytest.raises((FileNotFoundError, OSError)):
            bounded_sink(lf, out)


# ---------------------------------------------------------------------------
# atomic_write edge cases
# ---------------------------------------------------------------------------


class TestAtomicWriteEdgeCases:
    def test_creates_deeply_nested_parents(self, tmp_path: Path):
        dest = tmp_path / "a" / "b" / "c" / "d" / "out.parquet"
        with atomic_write(dest) as tmp:
            pl.DataFrame({"v": [42]}).write_parquet(tmp)

        assert dest.exists()
        assert pl.read_parquet(dest)["v"].to_list() == [42]

    def test_cleans_up_temp_on_exception(self, tmp_path: Path):
        dest = tmp_path / "fail.parquet"
        tmp_ref = None
        with pytest.raises(IOError):
            with atomic_write(dest) as tmp:
                tmp_ref = tmp
                tmp.write_bytes(b"partial data")
                raise OSError("write failed")

        assert not dest.exists()
        assert tmp_ref is not None
        assert not tmp_ref.exists()

    def test_overwrites_existing_file(self, tmp_path: Path):
        dest = tmp_path / "overwrite.parquet"
        pl.DataFrame({"v": [1]}).write_parquet(dest)
        original_size = dest.stat().st_size

        with atomic_write(dest) as tmp:
            pl.DataFrame({"v": list(range(100))}).write_parquet(tmp)

        assert dest.exists()
        assert dest.stat().st_size != original_size
        assert pl.read_parquet(dest)["v"].to_list() == list(range(100))

    def test_atomic_rename(self, tmp_path: Path):
        dest = tmp_path / "atomic.parquet"
        with atomic_write(dest) as tmp:
            assert tmp.name == "atomic.parquet.tmp"
            pl.DataFrame({"x": [1]}).write_parquet(tmp)
            assert tmp.exists()
            assert not dest.exists()

        assert dest.exists()
        assert not tmp.exists()


# ---------------------------------------------------------------------------
# _malloc_trim edge cases
# ---------------------------------------------------------------------------


class TestMallocTrimEdgeCases:
    def test_returns_none(self):
        assert _malloc_trim() is None

    def test_multiple_consecutive_calls(self):
        for _ in range(5):
            result = _malloc_trim()
            assert result is None


# ---------------------------------------------------------------------------
# row_local_python_scan
# ---------------------------------------------------------------------------


class TestRowLocalPythonScan:
    """Row-local Python steps stay transparent to Polars pushdown."""

    @staticmethod
    def _doubling_scan(
        frame: pl.DataFrame,
        seen: list[pl.DataFrame],
        *,
        input_predicates_allowed: bool = True,
        elide_transform_when_unused: bool = True,
    ) -> pl.LazyFrame:
        def transform(batch: pl.DataFrame) -> pl.DataFrame:
            seen.append(batch)
            return batch.with_columns((pl.col("x") * 2).alias("pred"))

        return row_local_python_scan(
            frame.lazy(),
            transform,
            schema=pl.Schema({**frame.schema, "pred": frame.schema["x"]}),
            generated_columns=("pred",),
            required_input_columns=("x",),
            input_predicates_allowed=input_predicates_allowed,
            elide_transform_when_unused=elide_transform_when_unused,
        )

    @pytest.mark.parametrize("engine", ["in-memory", "streaming"])
    def test_limit_is_read_before_the_pushed_predicate(self, engine: str) -> None:
        frame = pl.DataFrame({"x": [0, 0, 1, 2], "key": ["a", "b", "c", "d"]})
        scan = self._doubling_scan(frame, [])

        head_then_filter = scan.head(2).filter(pl.col("x") > 0).collect(engine=engine)
        filter_then_head = scan.filter(pl.col("x") > 0).head(2).collect(engine=engine)

        assert head_then_filter.height == 0
        assert filter_then_head["x"].to_list() == [1, 2]

    def test_limit_caps_rows_given_to_the_transform(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": list(range(1_000)), "key": [str(i) for i in range(1_000)]})

        result = streaming_collect(self._doubling_scan(frame, seen).head(10))

        assert result["pred"].to_list() == [value * 2 for value in range(10)]
        assert sum(batch.height for batch in seen) == 10

    def test_transform_is_elided_when_no_generated_column_is_read(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": [1, 2, 3], "key": ["a", "b", "c"]})
        scan = self._doubling_scan(frame, seen)

        assert streaming_collect(scan.select(pl.col("key").len())).item() == 3
        assert streaming_collect(scan.select("key"))["key"].to_list() == ["a", "b", "c"]
        assert seen == []

    def test_refused_elision_transforms_every_read_row(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": [1, 2, 3], "key": ["a", "b", "c"]})
        scan = self._doubling_scan(frame, seen, elide_transform_when_unused=False)

        streaming_collect(scan.select("key"))

        assert sum(batch.height for batch in seen) == 3
        assert all("x" in batch.columns for batch in seen)

    def test_refused_input_predicates_still_transform_filtered_rows(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": [1, 2, 3], "key": ["keep", "miss", "keep"]})
        scan = self._doubling_scan(
            frame,
            seen,
            input_predicates_allowed=False,
            elide_transform_when_unused=False,
        )

        result = streaming_collect(scan.filter(pl.col("key") != "miss"))

        assert result["x"].to_list() == [1, 3]
        assert "miss" in pl.concat(seen)["key"].to_list()

    def test_permitted_input_predicates_filter_before_the_transform(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": [1, 2, 3], "key": ["keep", "miss", "keep"]})

        result = streaming_collect(self._doubling_scan(frame, seen).filter(pl.col("key") != "miss"))

        assert result["pred"].to_list() == [2, 6]
        assert pl.concat(seen)["key"].to_list() == ["keep", "keep"]

    def test_predicate_on_a_generated_column_filters_transformed_rows(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": [1, 2, 3], "key": ["a", "b", "c"]})

        result = streaming_collect(self._doubling_scan(frame, seen).filter(pl.col("pred") > 2))

        assert result["x"].to_list() == [2, 3]
        assert sum(batch.height for batch in seen) == 3

    def test_original_exception_survives_every_haute_collect_seam(self, tmp_path: Path) -> None:
        from haute.errors import ConfigError

        def failing(batch: pl.DataFrame) -> pl.DataFrame:
            raise ConfigError("scan transform failed", node_id="scorer")

        def scan() -> pl.LazyFrame:
            frame = pl.LazyFrame({"x": [1, 2]})
            return row_local_python_scan(
                frame,
                failing,
                schema=frame.collect_schema(),
                generated_columns=(),
                required_input_columns=None,
                input_predicates_allowed=False,
                elide_transform_when_unused=False,
            )

        context = ExecutionContext(
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_sampler=lambda: 1,
        )
        seams = [
            lambda: streaming_collect(scan()),
            lambda: execution_collect(scan()),
            lambda: execution_collect(scan(), execution_context=context),
            lambda: list(bounded_collect_batches(scan(), chunk_size=10)),
            lambda: bounded_sink(scan(), tmp_path / "out.parquet"),
        ]
        for seam in seams:
            with pytest.raises(ConfigError, match="scan transform failed"):
                seam()
        with pytest.raises(pl.exceptions.ComputeError, match="ConfigError"):
            scan().collect()

    def test_caller_context_variables_reach_streaming_engine_threads(self) -> None:
        import contextvars

        marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
        observed: list[str] = []
        token = marker.set("caller")
        try:
            frame = pl.LazyFrame({"x": [1]})

            def transform(batch: pl.DataFrame) -> pl.DataFrame:
                observed.append(marker.get())
                return batch

            scan = row_local_python_scan(
                frame,
                transform,
                schema=frame.collect_schema(),
                generated_columns=(),
                required_input_columns=None,
                input_predicates_allowed=False,
                elide_transform_when_unused=False,
            )
        finally:
            marker.reset(token)

        scan.collect(engine="streaming")

        assert observed == ["caller"]

    @pytest.mark.parametrize("engine", ["in-memory", "streaming"])
    @pytest.mark.parametrize(
        "query",
        [
            pytest.param(lambda lf: lf.sort("key").head(2), id="sort-head"),
            pytest.param(
                lambda lf: lf.select("key").unique().sort("key").head(2), id="unique-sort-head"
            ),
            pytest.param(
                lambda lf: lf.filter(pl.col("x") > 1).sort("key").head(2), id="filter-sort-head"
            ),
            pytest.param(lambda lf: lf.top_k(2, by="pred"), id="top-k-generated"),
            pytest.param(lambda lf: lf.bottom_k(2, by=["x", "key"]), id="bottom-k-carried"),
        ],
    )
    def test_sort_limits_read_the_same_rows_as_native_polars(
        self, engine: str, query: Callable[[pl.LazyFrame], pl.LazyFrame]
    ) -> None:
        frame = pl.DataFrame({"x": [3, 1, 4, 1, 5, 2], "key": ["f", "b", "e", "a", "d", "c"]})
        native = frame.lazy().with_columns((pl.col("x") * 2).alias("pred"))

        for elide in (True, False):
            scan = self._doubling_scan(frame, [], elide_transform_when_unused=elide)
            assert_frame_equal(
                query(scan).collect(engine=engine), query(native).collect(engine=engine)
            )

    def test_limited_scan_sort_limit_reads_the_same_rows_as_native_polars(self) -> None:
        frame = pl.DataFrame({"x": [3, 1, 4], "key": ["c", "a", "b"]})
        scan = limited_python_scan(lambda n_rows: frame, schema=frame.schema)

        assert_frame_equal(
            streaming_collect(scan.filter(pl.col("x") > 1).sort("key").head(1)),
            frame.filter(pl.col("x") > 1).sort("key").head(1),
        )

    @pytest.mark.parametrize(
        ("schema", "generated", "required", "message"),
        [
            ({"x": pl.String}, (), None, "same dtype"),
            ({"x": pl.Int64}, ("pred",), None, "absent from the scan schema"),
            ({"x": pl.Int64}, (), ("missing",), "absent from the input"),
        ],
    )
    def test_declarations_that_disagree_with_the_input_fail_at_construction(
        self,
        schema: dict[str, pl.DataType],
        generated: tuple[str, ...],
        required: tuple[str, ...] | None,
        message: str,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            row_local_python_scan(
                pl.LazyFrame({"x": [1]}),
                lambda batch: batch,
                schema=pl.Schema(schema),
                generated_columns=generated,
                required_input_columns=required,
                input_predicates_allowed=False,
                elide_transform_when_unused=False,
            )

    @pytest.mark.parametrize("input_predicates_allowed", [True, False])
    def test_a_predicate_the_input_cannot_evaluate_filters_transformed_rows(
        self, input_predicates_allowed: bool
    ) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": [1, 2, 3], "key": ["a", "b", "c"]})
        scan = self._doubling_scan(
            frame,
            seen,
            input_predicates_allowed=input_predicates_allowed,
            elide_transform_when_unused=False,
        )
        # A generated column cannot filter the input; a refused input predicate
        # must not hide rows from a transform that validates them.
        predicate = pl.col("pred") > 2 if input_predicates_allowed else pl.col("x") > 1

        result = scan.filter(predicate).select("key").collect()

        assert result["key"].to_list() == ["b", "c"]
        assert sum(batch.height for batch in seen) == 3

    def test_limited_scan_caps_filters_and_projects_what_the_source_produces(self) -> None:
        frame = pl.DataFrame(
            {"x": [3, 1, 4, 1, 5], "key": ["c", "a", "b", "d", "e"], "extra": [0] * 5}
        )
        requested: list[int | None] = []

        def produce(n_rows: int | None) -> pl.DataFrame:
            requested.append(n_rows)
            # Returning every row even under a limit: the scan still caps it.
            return frame

        scan = limited_python_scan(produce, schema=frame.schema)

        assert_frame_equal(scan.head(2).collect(), frame.head(2))
        assert requested == [2]
        assert_frame_equal(
            scan.filter(pl.col("x") > 1).select("key").collect(),
            frame.filter(pl.col("x") > 1).select("key"),
        )

    def test_key_prefix_scan_applies_only_to_the_first_keys_rows(self) -> None:
        input_lf = pl.LazyFrame({"k": ["b", "a", "b", None, "c"], "v": [1, 2, 3, 4, 5]})
        applied_rows: list[int] = []

        def apply(rows: pl.LazyFrame) -> pl.DataFrame:
            collected = rows.collect()
            applied_rows.append(collected.height)
            return collected.drop_nulls("k").group_by("k").agg(pl.col("v").sum()).sort("k")

        scan = key_prefix_python_scan(
            input_lf, apply, schema=pl.Schema({"k": pl.String, "v": pl.Int64}), key_column="k"
        )

        assert scan.head(2).collect().to_dicts() == [{"k": "a", "v": 2}, {"k": "b", "v": 4}]
        assert applied_rows == [3]

    def test_key_prefix_scan_without_keys_is_empty_and_applies_nothing(self) -> None:
        applied: list[pl.LazyFrame] = []
        schema = pl.Schema({"k": pl.String, "v": pl.Int64})

        scan = key_prefix_python_scan(
            pl.LazyFrame({"k": [None], "v": [1]}, schema=schema),
            lambda rows: applied.append(rows) or pl.DataFrame(schema=schema),
            schema=schema,
            key_column="k",
        )

        assert_frame_equal(scan.head(1).collect(), pl.DataFrame(schema=schema))
        assert applied == []

    def test_the_parked_failure_registry_evicts_its_oldest_failure(self) -> None:
        from haute import _polars_utils

        oldest = _polars_utils._park_python_scan_failure(ValueError("oldest"))
        newest = oldest
        for index in range(_polars_utils._PYTHON_SCAN_FAILURE_LIMIT):
            newest = _polars_utils._park_python_scan_failure(ValueError(f"failure {index}"))

        _polars_utils._reraise_python_scan_failure(pl.exceptions.ComputeError(oldest))
        with pytest.raises(ValueError, match="failure 63"):
            _polars_utils._reraise_python_scan_failure(pl.exceptions.ComputeError(newest))


# ---------------------------------------------------------------------------
# fanout_python_scan
# ---------------------------------------------------------------------------


class TestFanoutPythonScan:
    """A fixed fan-out expansion stays transparent to Polars pushdown."""

    FANOUT = 3

    @staticmethod
    def _repeating_scan(
        frame: pl.DataFrame,
        seen: list[pl.DataFrame],
        *,
        fanout: int = 3,
    ) -> pl.LazyFrame:
        """Each input row becomes ``fanout`` rows carrying their grid step."""

        def expand(batch: pl.DataFrame) -> pl.DataFrame:
            seen.append(batch)
            return (
                batch.with_columns(pl.lit(list(range(fanout))).alias("step"))
                .explode("step")
                .with_columns(pl.col("step").cast(pl.Int32))
            )

        return fanout_python_scan(
            frame.lazy(),
            expand,
            schema=pl.Schema({**frame.schema, "step": pl.Int32()}),
            fanout=fanout,
            generated_columns=("step",),
            required_input_columns=(),
        )

    @staticmethod
    def _native(frame: pl.DataFrame, *, fanout: int = 3) -> pl.LazyFrame:
        return (
            frame.lazy()
            .with_columns(pl.lit(list(range(fanout))).alias("step"))
            .explode("step")
            .with_columns(pl.col("step").cast(pl.Int32))
        )

    @pytest.mark.parametrize("engine", ["in-memory", "streaming"])
    @pytest.mark.parametrize("limit", [1, 2, 3, 4, 7, 9, 100])
    def test_a_pushed_limit_returns_the_rows_the_expression_returns(
        self, engine: str, limit: int
    ) -> None:
        frame = pl.DataFrame({"x": list(range(3)), "key": ["a", "b", "c"]})
        scan = self._repeating_scan(frame, [])

        assert_frame_equal(
            scan.head(limit).collect(engine=engine),
            self._native(frame).head(limit).collect(engine=engine),
        )

    def test_a_pushed_limit_expands_only_the_input_rows_it_needs(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": list(range(1_000)), "key": [str(i) for i in range(1_000)]})

        result = streaming_collect(self._repeating_scan(frame, seen).head(7))

        assert result["x"].to_list() == [0, 0, 0, 1, 1, 1, 2]
        # ceil(7 / 3) input rows cover the first seven output rows.
        assert sum(batch.height for batch in seen) == 3

    def test_an_aggregation_below_the_limit_still_expands_every_row(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": [1, 2, 3, 4], "key": ["a", "b", "c", "d"]})
        scan = self._repeating_scan(frame, seen)

        assert streaming_collect(scan.select(pl.len())).item() == 12
        assert sum(batch.height for batch in seen) == 4

    def test_a_pushed_projection_narrows_the_input_it_reads(self) -> None:
        seen: list[pl.DataFrame] = []
        frame = pl.DataFrame({"x": [1, 2], "key": ["a", "b"]})

        result = streaming_collect(self._repeating_scan(frame, seen).select("key", "step"))

        assert result["key"].to_list() == ["a", "a", "a", "b", "b", "b"]
        assert [batch.columns for batch in seen] == [["key"]]

    @pytest.mark.parametrize("engine", ["in-memory", "streaming"])
    @pytest.mark.parametrize(
        "query",
        [
            pytest.param(lambda lf: lf.filter(pl.col("x") > 1).head(2), id="filter-head"),
            pytest.param(lambda lf: lf.head(4).filter(pl.col("x") > 1), id="head-filter"),
            pytest.param(lambda lf: lf.filter(pl.col("step") > 0).head(3), id="generated-filter"),
            pytest.param(lambda lf: lf.sort("key", descending=True).head(2), id="sort-head"),
            pytest.param(
                lambda lf: lf.group_by("key").agg(pl.col("step").sum()).sort("key"), id="group-by"
            ),
        ],
    )
    def test_queries_above_the_scan_match_the_expression(
        self, engine: str, query: Callable[[pl.LazyFrame], pl.LazyFrame]
    ) -> None:
        frame = pl.DataFrame({"x": [3, 1, 2], "key": ["c", "a", "b"]})
        scan = self._repeating_scan(frame, [])

        assert_frame_equal(
            query(scan).collect(engine=engine),
            query(self._native(frame)).collect(engine=engine),
        )

    def test_a_fanout_of_one_carries_every_row_through(self) -> None:
        frame = pl.DataFrame({"x": [1, 2, 3], "key": ["a", "b", "c"]})

        assert_frame_equal(
            streaming_collect(self._repeating_scan(frame, [], fanout=1).head(2)),
            self._native(frame, fanout=1).head(2).collect(engine="streaming"),
        )

    @pytest.mark.parametrize("fanout", [0, -1, True, 1.5])
    def test_a_fanout_that_is_not_a_positive_integer_fails_at_construction(
        self, fanout: object
    ) -> None:
        with pytest.raises(ValueError, match="fanout must be a positive integer"):
            fanout_python_scan(
                pl.LazyFrame({"x": [1]}),
                lambda batch: batch,
                schema=pl.Schema({"x": pl.Int64()}),
                fanout=cast(int, fanout),
                generated_columns=(),
                required_input_columns=None,
            )

    @pytest.mark.parametrize(
        ("schema", "generated", "required", "message"),
        [
            ({"x": pl.String}, (), None, "same dtype"),
            ({"x": pl.Int64}, ("step",), None, "absent from the scan schema"),
            ({"x": pl.Int64}, (), ("missing",), "absent from the input"),
        ],
    )
    def test_declarations_that_disagree_with_the_input_fail_at_construction(
        self,
        schema: dict[str, pl.DataType],
        generated: tuple[str, ...],
        required: tuple[str, ...] | None,
        message: str,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            fanout_python_scan(
                pl.LazyFrame({"x": [1]}),
                lambda batch: batch,
                schema=pl.Schema(schema),
                fanout=2,
                generated_columns=generated,
                required_input_columns=required,
            )


def test_hashed_streaming_sink_writes_and_hashes_atomically(tmp_path: Path) -> None:
    out = tmp_path / "part-00000.parquet"
    lf = pl.LazyFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    digest = hashed_streaming_sink(lf, out)

    assert out.is_file()
    assert digest == content_hash(out)
    read_back = pl.read_parquet(out)
    assert_frame_equal(read_back, lf.collect())
    assert list(tmp_path.iterdir()) == [out]


def test_hashed_streaming_sink_runs_the_native_streaming_sink(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    sink_target: Any = None

    class Query:
        def fetch(self) -> pl.DataFrame:
            assert sink_target is not None
            sink_target.write(b"completed")
            return pl.DataFrame()

        def cancel(self) -> None:
            raise AssertionError("completed sink must not be cancelled")

    class SinkPlan:
        def collect(self, **kwargs: object) -> Query:
            captured["collect"] = kwargs
            return Query()

    def fake_sink(_lf: pl.LazyFrame, target: Any, **kwargs: object) -> SinkPlan:
        nonlocal sink_target
        sink_target = target
        captured["sink"] = kwargs
        return SinkPlan()

    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1,
    )
    out = tmp_path / "out.parquet"

    with (
        patch.object(pl.LazyFrame, "sink_parquet", autospec=True, side_effect=fake_sink),
        context.stage("sink"),
    ):
        digest = hashed_streaming_sink(pl.LazyFrame({"x": [1]}), out)

    assert captured["sink"] == {
        "compression": "zstd",
        "lazy": True,
        "engine": "streaming",
    }
    assert hasattr(sink_target, "hexdigest")
    assert captured["collect"] == {"engine": "streaming", "background": True}
    assert out.read_bytes() == b"completed"
    assert digest == content_hash(out)


def test_bounded_hashed_sink_cancels_native_query_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1,
    )
    cancelled = False

    class Query:
        def fetch(self) -> None:
            context.cancellation_token.cancel()
            return None

        def cancel(self) -> None:
            nonlocal cancelled
            cancelled = True

    class SinkPlan:
        def collect(self, **_kwargs: object) -> Query:
            return Query()

    out = tmp_path / "cancelled.parquet"
    with (
        patch.object(pl.LazyFrame, "sink_parquet", return_value=SinkPlan()),
        context.stage("sink"),
        pytest.raises(ExecutionCancelledError),
    ):
        bounded_hashed_sink(pl.LazyFrame({"x": [1]}), out)

    assert cancelled is True
    assert not out.exists()
    assert not out.with_suffix(".parquet.tmp").exists()


def test_hashed_streaming_sink_cleans_up_after_a_partial_write(tmp_path: Path) -> None:
    sink_target: Any = None

    class Query:
        def fetch(self) -> None:
            assert sink_target is not None
            assert hasattr(sink_target, "write")
            sink_target.write(b"partial-data")
            raise RuntimeError("disk full during write")

    class SinkPlan:
        def collect(self, **_kwargs: object) -> Query:
            return Query()

    def fake_sink(_lf: pl.LazyFrame, target: Any, **_kwargs: object) -> SinkPlan:
        nonlocal sink_target
        sink_target = target
        return SinkPlan()

    context = ExecutionContext(
        operation="sink",
        profile=ExecutionProfile.LAZY_SINK,
        memory_sampler=lambda: 1,
    )
    out = tmp_path / "failed.parquet"
    with (
        patch.object(pl.LazyFrame, "sink_parquet", autospec=True, side_effect=fake_sink),
        context.stage("sink"),
        pytest.raises(RuntimeError, match="disk full during write"),
    ):
        hashed_streaming_sink(pl.LazyFrame({"x": [1]}), out)

    assert not out.exists()
    assert list(tmp_path.iterdir()) == []
