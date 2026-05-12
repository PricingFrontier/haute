"""Tests for haute._polars_utils."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._polars_utils import (
    _malloc_trim,
    atomic_write,
    best_effort_sink,
    bounded_collect_batches,
    bounded_sink,
    read_parquet_metadata,
    safe_sink,
    streaming_collect,
    temporary_streaming_chunk_size,
)
from haute.errors import BoundedMemoryUnsupportedError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Newer Polars (>= 1.x) routes DataFrame.write_parquet through
# LazyFrame.sink_parquet internally.  That means a class-level mock on
# sink_parquet also breaks the eager fallback path.  We work around this by
# patching write_parquet / write_csv so the fallback writes the file
# directly via PyArrow, completely bypassing the mocked sink methods.


def _pyarrow_write_parquet(self: pl.DataFrame, path, **_kw) -> None:
    """Write a DataFrame to Parquet via PyArrow, bypassing Polars sinks."""
    import pyarrow.parquet as pq

    pq.write_table(self.to_arrow(), str(path))


def _manual_write_csv(self: pl.DataFrame, path, **_kw) -> None:
    """Write a DataFrame to CSV via stdlib, bypassing Polars sinks."""
    import csv

    cols = self.columns
    with open(str(path), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for row in self.iter_rows():
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_streaming_collect_uses_polars_streaming_engine() -> None:
    """streaming_collect is intentionally a no-fallback streaming collect."""
    captured: dict[str, object] = {}

    class Lazy:
        def collect(self, *, engine: str) -> pl.DataFrame:
            captured["engine"] = engine
            return pl.DataFrame({"x": [1]})

    result = streaming_collect(Lazy(), profile=ExecutionProfile.LAZY_SINK)  # type: ignore[arg-type]

    assert result["x"].to_list() == [1]
    assert captured == {"engine": "streaming"}


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
            profile=ExecutionProfile.LAZY_SINK,
        )

    assert result["x"].to_list() == [1]
    metric = context.metrics.snapshot()[0]
    assert metric.n_collects == 1
    assert metric.to_summary().to_dict()["n_collects"] == 1


def test_streaming_collect_raises_typed_error_without_broad_fallback() -> None:
    """Bounded profiles must not silently broaden when streaming collect fails."""

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            if kwargs == {"engine": "streaming"}:
                raise pl.exceptions.ComputeError("streaming collect failed")
            raise AssertionError("broad collect fallback should not run")

    with pytest.raises(BoundedMemoryUnsupportedError, match="Bounded streaming collect failed"):
        streaming_collect(Lazy(), profile=ExecutionProfile.LAZY_SINK)  # type: ignore[arg-type]


def test_streaming_collect_preserves_non_streaming_data_errors() -> None:
    """Data validation failures must not be mislabeled as streaming incompatibility."""

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise pl.exceptions.InvalidOperationError("conversion from str to f64 failed")

    with pytest.raises(pl.exceptions.InvalidOperationError, match="conversion"):
        streaming_collect(Lazy(), profile=ExecutionProfile.DEPLOY_BATCH)  # type: ignore[arg-type]


def test_streaming_collect_preserves_generic_unsupported_data_errors() -> None:
    """Generic unsupported-operation errors are not necessarily streaming failures."""

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise pl.exceptions.InvalidOperationError("operation not supported for dtype date")

    with pytest.raises(pl.exceptions.InvalidOperationError, match="not supported"):
        streaming_collect(Lazy(), profile=ExecutionProfile.DEPLOY_BATCH)  # type: ignore[arg-type]


def test_streaming_collect_preserves_execution_cancellation() -> None:
    """Execution cancellation must not be wrapped as a streaming incompatibility."""
    cancellation = ExecutionCancelledError("pipeline_sink", job_id="job-1")

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise cancellation

    with pytest.raises(ExecutionCancelledError) as exc_info:
        streaming_collect(Lazy(), profile=ExecutionProfile.LAZY_SINK)  # type: ignore[arg-type]

    assert exc_info.value is cancellation


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
        streaming_collect(Lazy(), profile=ExecutionProfile.LAZY_SINK)  # type: ignore[arg-type]

    assert exc_info.value is memory_error


def test_streaming_collect_rejects_broad_fallback_for_bounded_profile() -> None:
    """Only explicitly broad profiles can opt into non-streaming collect."""
    calls = 0

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            nonlocal calls
            calls += 1
            del args
            if kwargs == {"engine": "streaming"}:
                raise pl.exceptions.ComputeError("streaming collect failed")
            return pl.DataFrame({"x": [2]})

    with pytest.raises(ValueError, match="allow_broad=True"):
        streaming_collect(
            Lazy(),  # type: ignore[arg-type]
            profile=ExecutionProfile.DEPLOY_BATCH,
            allow_broad=True,
        )

    assert calls == 0


def test_streaming_collect_rejects_unknown_profile() -> None:
    """Profile strings are validated so typos do not become silent labels."""
    lf = pl.LazyFrame({"x": [1]})

    with pytest.raises(ValueError):
        streaming_collect(lf, profile="typo")


def test_bounded_collect_batches_uses_polars_streaming_batches() -> None:
    captured: dict[str, object] = {}

    class Lazy:
        def collect_batches(
            self,
            *,
            chunk_size: int,
            maintain_order: bool,
            engine: str,
        ):
            captured.update(
                {
                    "chunk_size": chunk_size,
                    "maintain_order": maintain_order,
                    "engine": engine,
                }
            )
            return iter([pl.DataFrame({"x": [1]}), pl.DataFrame({"x": [2]})])

    batches = list(
        bounded_collect_batches(
            Lazy(),  # type: ignore[arg-type]
            profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
            chunk_size=7,
            maintain_order=True,
        )
    )

    assert captured == {
        "chunk_size": 7,
        "maintain_order": True,
        "engine": "streaming",
    }
    assert [batch["x"].to_list() for batch in batches] == [[1], [2]]


def test_bounded_collect_batches_records_only_real_batch_stages() -> None:
    class Lazy:
        def collect_batches(self, **_kwargs):
            return iter([pl.DataFrame({"x": [1]}), pl.DataFrame({"x": [2]})])

    context = ExecutionContext(
        operation="chunked",
        profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        memory_sampler=lambda: 1_000,
    )

    batches = list(
        bounded_collect_batches(
            Lazy(),  # type: ignore[arg-type]
            profile=ExecutionProfile.LAZY_SINK,
            chunk_size=7,
            execution_context=context,
            stage_name="batch_collect",
        )
    )

    assert [batch["x"].to_list() for batch in batches] == [[1], [2]]
    stages = context.metrics.snapshot()
    assert [stage.name for stage in stages] == ["batch_collect", "batch_collect"]
    assert [stage.n_collects for stage in stages] == [1, 1]
    summary = context.metrics_summary()
    assert summary.n_collects == 2
    assert summary.n_checkpoints == 3


def test_bounded_collect_batches_maps_streaming_iteration_failure() -> None:
    class FailingIterator:
        def __iter__(self):
            return self

        def __next__(self) -> pl.DataFrame:
            raise pl.exceptions.ComputeError("streaming batch failed")

    class Lazy:
        def collect_batches(self, **_kwargs):
            return FailingIterator()

    with pytest.raises(
        BoundedMemoryUnsupportedError,
        match="Bounded streaming batch collection failed",
    ):
        list(
            bounded_collect_batches(
                Lazy(),  # type: ignore[arg-type]
                profile=ExecutionProfile.AUTO_RANGE,
                chunk_size=5,
            )
        )


def test_streaming_collect_explicit_broad_fallback() -> None:
    """Preview-style callers can opt into broad collect fallback deliberately."""
    calls: list[dict[str, object]] = []

    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            calls.append(dict(kwargs))
            if kwargs == {"engine": "streaming"}:
                raise pl.exceptions.ComputeError("streaming collect failed")
            return pl.DataFrame({"x": [2]})

    result = streaming_collect(
        Lazy(),  # type: ignore[arg-type]
        profile=ExecutionProfile.PREVIEW_EAGER,
        allow_broad=True,
    )

    assert result["x"].to_list() == [2]
    assert calls == [{"engine": "streaming"}, {}]


def test_streaming_collect_uses_active_context_profile_for_typed_errors() -> None:
    class Lazy:
        def collect(self, *args, **kwargs) -> pl.DataFrame:
            del args, kwargs
            raise pl.exceptions.ComputeError("streaming collect failed")

    context = ExecutionContext(
        operation="deploy",
        profile=ExecutionProfile.DEPLOY_BATCH,
        memory_sampler=lambda: 1_000,
    )

    with (
        context.stage("collect"),
        pytest.raises(BoundedMemoryUnsupportedError) as exc_info,
    ):
        streaming_collect(
            Lazy(),  # type: ignore[arg-type]
            profile=ExecutionProfile.PREVIEW_EAGER,
        )

    assert exc_info.value.context["profile"] == ExecutionProfile.DEPLOY_BATCH.value


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


# ---------------------------------------------------------------------------
# Fallback tests (parquet & csv, parametrized)
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
    """Bounded sinks fail loudly instead of materialising the full LazyFrame."""
    lf = pl.LazyFrame({"a": [1, 2, 3]})
    out = tmp_path / "test.parquet"

    with (
        patch.object(
            pl.LazyFrame,
            "sink_parquet",
            side_effect=error_cls("streaming sink failed"),
        ),
        patch.object(pl.LazyFrame, "collect", autospec=True) as collect_mock,
    ):
        with pytest.raises(BoundedMemoryUnsupportedError, match="Bounded streaming sink failed"):
            bounded_sink(lf, out)

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


def test_bounded_sink_maps_streaming_compute_error_without_collect_fallback(
    tmp_path: Path,
) -> None:
    """Streaming ComputeErrors are bounded sink failures, not broad collects."""
    lf = pl.LazyFrame({"a": [1]})
    out = tmp_path / "test.parquet"

    with (
        patch.object(
            pl.LazyFrame,
            "sink_parquet",
            side_effect=pl.exceptions.ComputeError("streaming sink failed"),
        ),
        patch.object(pl.LazyFrame, "collect", autospec=True) as collect_mock,
    ):
        with pytest.raises(BoundedMemoryUnsupportedError, match="Bounded streaming sink failed"):
            bounded_sink(lf, out)

    collect_mock.assert_not_called()


def test_best_effort_sink_requires_explicit_broadening(tmp_path: Path) -> None:
    """Fallback-capable sink callers must opt into the broad collect path."""
    lf = pl.LazyFrame({"a": [1]})

    with pytest.raises(ValueError, match="allow_broad=True"):
        best_effort_sink(lf, tmp_path / "out.parquet")


@pytest.mark.parametrize("error_cls", _POLARS_FALLBACK_ERRORS)
def test_best_effort_sink_parquet_fallback_on_error(tmp_path: Path, error_cls: type):
    """Polars error in sink_parquet triggers collect+write_parquet fallback."""
    lf = pl.LazyFrame({"a": [1, 2, 3]})
    out = tmp_path / "test.parquet"

    with (
        patch.object(pl.LazyFrame, "sink_parquet", side_effect=error_cls("sink failed")),
        patch.object(
            pl.DataFrame,
            "write_parquet",
            autospec=True,
            side_effect=_pyarrow_write_parquet,
        ),
    ):
        best_effort_sink(lf, out, allow_broad=True)

    result = pl.read_parquet(out)
    assert result["a"].to_list() == [1, 2, 3]


def test_best_effort_sink_fallback_records_collect_on_active_context(tmp_path: Path) -> None:
    lf = pl.LazyFrame({"a": [1, 2, 3]})
    out = tmp_path / "test.parquet"
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 1_000,
    )

    with (
        context.stage("fallback_sink"),
        patch.object(
            pl.LazyFrame,
            "sink_parquet",
            side_effect=pl.exceptions.ComputeError("streaming sink failed"),
        ),
        patch.object(
            pl.DataFrame,
            "write_parquet",
            autospec=True,
            side_effect=_pyarrow_write_parquet,
        ),
    ):
        best_effort_sink(lf, out, allow_broad=True)

    assert pl.read_parquet(out)["a"].to_list() == [1, 2, 3]
    assert context.metrics.snapshot()[0].n_collects == 1


@pytest.mark.parametrize("error_cls", _POLARS_FALLBACK_ERRORS)
def test_best_effort_sink_csv_fallback_on_error(tmp_path: Path, error_cls: type):
    """Polars error in sink_csv triggers collect+write_csv fallback."""
    lf = pl.LazyFrame({"v": [10, 20]})
    out = tmp_path / "test.csv"

    with (
        patch.object(
            pl.LazyFrame,
            "sink_csv",
            side_effect=error_cls("csv sink failed"),
        ),
        patch.object(
            pl.DataFrame,
            "write_csv",
            autospec=True,
            side_effect=_manual_write_csv,
        ),
    ):
        best_effort_sink(lf, out, fmt="csv", allow_broad=True)

    result = pl.read_csv(out)
    assert result["v"].to_list() == [10, 20]


# ---------------------------------------------------------------------------
# Non-Polars errors must propagate
# ---------------------------------------------------------------------------


def test_bounded_sink_real_error_propagates(tmp_path: Path):
    """PermissionError (non-Polars) must NOT be caught by the fallback."""
    lf = pl.LazyFrame({"a": [1]})
    out = tmp_path / "test.parquet"

    with patch.object(
        pl.LazyFrame,
        "sink_parquet",
        side_effect=PermissionError("permission denied"),
    ):
        with pytest.raises(PermissionError, match="permission denied"):
            bounded_sink(lf, out)


def test_safe_sink_keeps_compatibility_alias_for_best_effort(tmp_path: Path) -> None:
    """Existing extension code can still call safe_sink as the explicit fallback path."""
    lf = pl.LazyFrame({"a": [1, 2]})
    out = tmp_path / "test.parquet"

    safe_sink(lf, out)

    assert pl.read_parquet(out)["a"].to_list() == [1, 2]


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
# safe_sink edge cases
# ---------------------------------------------------------------------------


class TestSafeSinkEdgeCases:
    def test_fast_checkpoint_uses_lz4(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": list(range(1000))})
        lz4_path = tmp_path / "lz4.parquet"
        zstd_path = tmp_path / "zstd.parquet"

        safe_sink(lf, lz4_path, fast_checkpoint=True)
        safe_sink(lf, zstd_path, fast_checkpoint=False)

        assert lz4_path.exists()
        assert zstd_path.exists()
        lz4_size = lz4_path.stat().st_size
        zstd_size = zstd_path.stat().st_size
        assert lz4_size != zstd_size

    def test_fast_checkpoint_false_uses_zstd(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": [1, 2, 3]})
        out = tmp_path / "zstd.parquet"
        safe_sink(lf, out, fast_checkpoint=False)

        import pyarrow.parquet as pq

        meta = pq.read_metadata(str(out))
        compression = meta.row_group(0).column(0).compression
        assert compression.lower() == "zstd"

    def test_fast_checkpoint_true_uses_lz4_compression(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": [1, 2, 3]})
        out = tmp_path / "lz4.parquet"
        safe_sink(lf, out, fast_checkpoint=True)

        import pyarrow.parquet as pq

        meta = pq.read_metadata(str(out))
        compression = meta.row_group(0).column(0).compression
        assert compression.lower() == "lz4"

    def test_csv_write_and_read_back(self, tmp_path: Path):
        lf = pl.LazyFrame({"name": ["alice", "bob"], "age": [30, 25]})
        out = tmp_path / "test.csv"
        safe_sink(lf, out, fmt="csv")

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
        safe_sink(lf, out)

        result = pl.read_parquet(out)
        assert result.shape == (0, 3)
        assert result.columns == ["a", "b", "c"]

    def test_parent_directory_creatable(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "c" / "out.parquet"
        nested.parent.mkdir(parents=True, exist_ok=True)
        lf = pl.LazyFrame({"x": [1]})
        safe_sink(lf, nested)

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
                safe_sink(lf, out)

    def test_oserror_propagates(self, tmp_path: Path):
        lf = pl.LazyFrame({"a": [1]})
        out = tmp_path / "test.parquet"

        with patch.object(
            pl.LazyFrame,
            "sink_parquet",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                safe_sink(lf, out)

    def test_path_as_string(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": [1, 2]})
        out = str(tmp_path / "string_path.parquet")
        safe_sink(lf, out)

        result = pl.read_parquet(out)
        assert result["x"].to_list() == [1, 2]

    def test_path_as_path_object(self, tmp_path: Path):
        lf = pl.LazyFrame({"x": [3, 4]})
        out = tmp_path / "path_obj.parquet"
        safe_sink(lf, out)

        result = pl.read_parquet(out)
        assert result["x"].to_list() == [3, 4]

    def test_nonexistent_parent_skips_atomic_write(self, tmp_path: Path):
        """When parent dir does not exist, safe_sink uses direct _do_sink
        (no atomic_write wrapper) — this exercises the else branch at line 166."""
        lf = pl.LazyFrame({"x": [1, 2]})
        # Create a path whose parent does NOT exist
        out = tmp_path / "nonexistent_dir" / "out.parquet"
        assert not out.parent.exists()

        # sink_parquet will fail because dir doesn't exist — that's expected.
        # The important thing is it goes through _do_sink(path) not atomic_write.
        with pytest.raises((FileNotFoundError, OSError)):
            safe_sink(lf, out)


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
