"""Tests for haute._polars_utils (safe_sink, _malloc_trim, atomic_write, read_parquet_metadata)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from haute._polars_utils import _malloc_trim, atomic_write, read_parquet_metadata, safe_sink

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


def test_safe_sink_writes_parquet(tmp_path: Path):
    """Happy path: LazyFrame -> sink_parquet -> read back matches."""
    lf = pl.LazyFrame({"x": [10, 20, 30], "y": ["a", "b", "c"]})
    out = tmp_path / "out.parquet"

    safe_sink(lf, out)

    result = pl.read_parquet(out)
    assert result.shape == (3, 2)
    assert result["x"].to_list() == [10, 20, 30]
    assert result["y"].to_list() == ["a", "b", "c"]


def test_safe_sink_writes_csv(tmp_path: Path):
    """Happy path with fmt='csv'."""
    lf = pl.LazyFrame({"a": [1, 2], "b": [3.5, 4.5]})
    out = tmp_path / "out.csv"

    safe_sink(lf, out, fmt="csv")

    result = pl.read_csv(out)
    assert result.shape == (2, 2)
    assert result["a"].to_list() == [1, 2]
    assert result["b"].to_list() == [3.5, 4.5]


# ---------------------------------------------------------------------------
# Fallback tests (parquet)
# ---------------------------------------------------------------------------


def _run_parquet_fallback(tmp_path: Path, error: Exception) -> None:
    """Shared helper: verify parquet fallback for a given Polars error type."""
    lf = pl.LazyFrame({"a": [1, 2, 3]})
    out = tmp_path / "test.parquet"

    with (
        patch.object(pl.LazyFrame, "sink_parquet", side_effect=error),
        patch.object(
            pl.DataFrame,
            "write_parquet",
            autospec=True,
            side_effect=_pyarrow_write_parquet,
        ),
    ):
        safe_sink(lf, out)

    result = pl.read_parquet(out)
    assert result["a"].to_list() == [1, 2, 3]


def test_safe_sink_fallback_on_compute_error(tmp_path: Path):
    """ComputeError in sink_parquet triggers collect+write_parquet fallback."""
    _run_parquet_fallback(tmp_path, pl.exceptions.ComputeError("streaming not supported"))


def test_safe_sink_fallback_on_invalid_operation_error(tmp_path: Path):
    """InvalidOperationError in sink_parquet triggers fallback."""
    _run_parquet_fallback(tmp_path, pl.exceptions.InvalidOperationError("bad op"))


def test_safe_sink_fallback_on_schema_error(tmp_path: Path):
    """SchemaError in sink_parquet triggers fallback."""
    _run_parquet_fallback(tmp_path, pl.exceptions.SchemaError("schema mismatch"))


# ---------------------------------------------------------------------------
# Fallback tests (csv)
# ---------------------------------------------------------------------------


def test_safe_sink_csv_fallback(tmp_path: Path):
    """ComputeError in sink_csv triggers collect+write_csv fallback."""
    lf = pl.LazyFrame({"name": ["alice", "bob"], "score": [90, 85]})
    out = tmp_path / "test.csv"

    with (
        patch.object(
            pl.LazyFrame,
            "sink_csv",
            side_effect=pl.exceptions.ComputeError("csv streaming failed"),
        ),
        patch.object(
            pl.DataFrame,
            "write_csv",
            autospec=True,
            side_effect=_manual_write_csv,
        ),
    ):
        safe_sink(lf, out, fmt="csv")

    result = pl.read_csv(out)
    assert result["name"].to_list() == ["alice", "bob"]
    assert result["score"].to_list() == [90, 85]


# ---------------------------------------------------------------------------
# Non-Polars errors must propagate
# ---------------------------------------------------------------------------


def test_safe_sink_real_error_propagates(tmp_path: Path):
    """PermissionError (non-Polars) must NOT be caught by the fallback."""
    lf = pl.LazyFrame({"a": [1]})
    out = tmp_path / "test.parquet"

    with patch.object(
        pl.LazyFrame,
        "sink_parquet",
        side_effect=PermissionError("permission denied"),
    ):
        with pytest.raises(PermissionError, match="permission denied"):
            safe_sink(lf, out)


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
        df = pl.DataFrame({
            "int_col": [1, 2],
            "float_col": [1.5, 2.5],
            "str_col": ["a", "b"],
            "bool_col": [True, False],
        })
        df.write_parquet(p)

        meta = read_parquet_metadata(p)
        assert meta["row_count"] == 2
        assert meta["column_count"] == 4
        assert set(meta["columns"].keys()) == {"int_col", "float_col", "str_col", "bool_col"}

    def test_empty_multi_column(self, tmp_path: Path):
        p = tmp_path / "empty_multi.parquet"
        df = pl.DataFrame({
            "a": pl.Series([], dtype=pl.Int64),
            "b": pl.Series([], dtype=pl.Utf8),
            "c": pl.Series([], dtype=pl.Float64),
        })
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
        lf = pl.LazyFrame({
            "a": pl.Series([], dtype=pl.Int64),
            "b": pl.Series([], dtype=pl.Utf8),
            "c": pl.Series([], dtype=pl.Float64),
        })
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

    def test_csv_fallback_on_compute_error(self, tmp_path: Path):
        lf = pl.LazyFrame({"v": [10, 20]})
        out = tmp_path / "fallback.csv"

        with (
            patch.object(
                pl.LazyFrame,
                "sink_csv",
                side_effect=pl.exceptions.ComputeError("csv sink failed"),
            ),
            patch.object(
                pl.DataFrame,
                "write_csv",
                autospec=True,
                side_effect=_manual_write_csv,
            ),
        ):
            safe_sink(lf, out, fmt="csv")

        result = pl.read_csv(out)
        assert result["v"].to_list() == [10, 20]

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

    def test_parquet_fallback_on_schema_error_csv(self, tmp_path: Path):
        """SchemaError in sink_csv triggers collect+write_csv fallback."""
        lf = pl.LazyFrame({"a": [1, 2, 3]})
        out = tmp_path / "schema_fallback.csv"

        with (
            patch.object(
                pl.LazyFrame,
                "sink_csv",
                side_effect=pl.exceptions.SchemaError("schema mismatch"),
            ),
            patch.object(
                pl.DataFrame,
                "write_csv",
                autospec=True,
                side_effect=_manual_write_csv,
            ),
        ):
            safe_sink(lf, out, fmt="csv")

        result = pl.read_csv(out)
        assert result["a"].to_list() == [1, 2, 3]

    def test_invalid_operation_error_csv_fallback(self, tmp_path: Path):
        """InvalidOperationError in sink_csv triggers fallback."""
        lf = pl.LazyFrame({"z": [10]})
        out = tmp_path / "inv_op_fallback.csv"

        with (
            patch.object(
                pl.LazyFrame,
                "sink_csv",
                side_effect=pl.exceptions.InvalidOperationError("bad csv op"),
            ),
            patch.object(
                pl.DataFrame,
                "write_csv",
                autospec=True,
                side_effect=_manual_write_csv,
            ),
        ):
            safe_sink(lf, out, fmt="csv")

        result = pl.read_csv(out)
        assert result["z"].to_list() == [10]


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
                raise IOError("write failed")

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
