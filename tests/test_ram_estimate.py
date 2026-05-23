"""Tests for haute._ram_estimate — RAM estimation and safe downsampling."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._ram_estimate import (
    RamEstimate,
    _ancestor_source_metadata,
    _count_source_rows_for_node,
    _csv_row_count,
    _estimate_peak_bytes,
    _jsonl_row_count,
    _parquet_metadata,
    _resolve_target_columns,
    _source_metadata_for_node,
    available_ram_bytes,
    available_vram_bytes,
    estimate_gpu_vram_bytes,
    estimate_safe_training_rows,
    estimate_source_rows,
)
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source_node(
    node_id: str = "src1",
    label: str = "quotes",
    node_type: str = "apiInput",
    config: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        type="custom",
        position={"x": 0, "y": 0},
        data=NodeData(
            label=label,
            nodeType=node_type,
            config=config or {},
        ),
    )


def _make_transform_node(
    node_id: str = "t1",
    label: str = "features",
    config: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        type="custom",
        position={"x": 0, "y": 100},
        data=NodeData(
            label=label,
            nodeType="polars",
            config=config or {},
        ),
    )


def _make_modelling_node(
    node_id: str = "m1",
    label: str = "model",
    config: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        type="custom",
        position={"x": 0, "y": 200},
        data=NodeData(
            label=label,
            nodeType="modelling",
            config=config or {},
        ),
    )


# ---------------------------------------------------------------------------
# available_ram_bytes
# ---------------------------------------------------------------------------


class TestAvailableRam:
    def test_returns_positive_int(self) -> None:
        ram = available_ram_bytes()
        assert isinstance(ram, int)
        assert ram > 0

    def test_returns_reasonable_value(self) -> None:
        """Should be at least 100 MB on any modern system."""
        ram = available_ram_bytes()
        assert ram > 100 * 1024 * 1024

    def test_fallback_when_proc_unavailable(self) -> None:
        """If /proc/meminfo is not readable, falls back gracefully."""
        with patch("builtins.open", side_effect=OSError):
            ram = available_ram_bytes()
            assert ram > 0

    def test_windows_path_calls_global_memory_status(self, monkeypatch) -> None:
        """On Windows, GlobalMemoryStatusEx is used to read available RAM."""
        from unittest.mock import MagicMock

        mock_windll = MagicMock()
        # GlobalMemoryStatusEx populates the struct; simulate by returning True
        # and patching ullAvailPhys via side_effect
        mock_windll.kernel32.GlobalMemoryStatusEx.return_value = True

        mock_ctypes = MagicMock()
        mock_ctypes.windll = mock_windll
        mock_ctypes.c_ulong = int
        mock_ctypes.c_ulonglong = int

        monkeypatch.setattr("sys.platform", "win32")

        # Block /proc and sysconf so only the Windows path runs
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=AttributeError, create=True):
                with patch.dict(
                    "sys.modules",
                    {"ctypes": mock_ctypes},
                ):
                    # The ctypes struct is defined inline; we can't easily
                    # intercept field values. Verify the API was called.
                    available_ram_bytes()

        mock_windll.kernel32.GlobalMemoryStatusEx.assert_called_once()

    def test_ultimate_fallback_returns_4gib(self, monkeypatch) -> None:
        """When all platform methods fail, returns 4 GiB default."""
        monkeypatch.setattr("sys.platform", "freebsd13")
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=AttributeError, create=True):
                ram = available_ram_bytes()
        assert ram == 4 * 1024**3


# ---------------------------------------------------------------------------
# Parquet/CSV row counts
# ---------------------------------------------------------------------------


class TestRowCounts:
    def test_parquet_row_count(self, tmp_path) -> None:
        path = tmp_path / "test.parquet"
        df = pl.DataFrame({"a": range(500), "b": range(500)})
        df.write_parquet(str(path))
        rows, cols = _parquet_metadata(str(path))
        assert rows == 500
        assert cols == 2

    def test_csv_row_count(self, tmp_path) -> None:
        path = tmp_path / "test.csv"
        df = pl.DataFrame({"x": range(123)})
        df.write_csv(str(path))
        assert _csv_row_count(str(path)) == 123


# ---------------------------------------------------------------------------
# estimate_source_rows
# ---------------------------------------------------------------------------


class TestEstimateSourceRows:
    def test_parquet_datasource(self, tmp_path) -> None:
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": range(1000)}).write_parquet(str(path))

        node = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) == 1000

    def test_returns_none_for_databricks(self) -> None:
        node = _make_source_node(
            node_type="dataSource",
            config={"sourceType": "databricks", "table": "cat.schema.tbl"},
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) is None

    def test_returns_none_for_missing_file(self) -> None:
        node = _make_source_node(
            node_type="dataSource",
            config={"path": "/nonexistent/file.parquet", "sourceType": "flat_file"},
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) is None

    def test_max_across_multiple_sources(self, tmp_path) -> None:
        p1 = tmp_path / "small.parquet"
        pl.DataFrame({"a": range(100)}).write_parquet(str(p1))
        p2 = tmp_path / "big.parquet"
        pl.DataFrame({"a": range(5000)}).write_parquet(str(p2))

        n1 = _make_source_node(
            node_id="s1",
            label="small",
            node_type="dataSource",
            config={"path": str(p1), "sourceType": "flat_file"},
        )
        n2 = _make_source_node(
            node_id="s2",
            label="big",
            node_type="dataSource",
            config={"path": str(p2), "sourceType": "flat_file"},
        )
        graph = PipelineGraph(nodes=[n1, n2], edges=[])
        assert estimate_source_rows(graph) == 5000


# ---------------------------------------------------------------------------
# estimate_safe_training_rows — integration
# ---------------------------------------------------------------------------


def _build_dummy_node_fn(
    node,
    *,
    source_names=None,
    row_limit=None,
    node_map=None,
    orig_source_names=None,
    preamble_ns=None,
    source="live",
    **_kwargs,
):
    """Minimal build_node_fn that creates a dummy source or passthrough."""
    label = node.data.label
    nt = node.data.nodeType

    if nt in ("dataSource", "apiInput"):
        n_rows = row_limit or 10_000

        def source_fn():
            return pl.LazyFrame(
                {
                    "a": range(n_rows),
                    "b": [f"val_{i}" for i in range(n_rows)],
                    "c": [float(i) for i in range(n_rows)],
                }
            )

        return label, source_fn, True

    def transform_fn(*inputs):
        return inputs[0] if inputs else pl.LazyFrame()

    return label, transform_fn, False


class TestEstimateSafeTrainingRows:
    def _make_graph(self) -> PipelineGraph:
        src = _make_source_node(
            config={"path": "data/test.parquet", "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        return PipelineGraph(nodes=[src, target], edges=[edge])

    def test_no_downsample_when_ram_sufficient(self, tmp_path) -> None:
        # Create a small parquet file
        path = tmp_path / "test.parquet"
        pl.DataFrame({"a": range(100)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        result = estimate_safe_training_rows(
            graph,
            target.id,
            _build_dummy_node_fn,
        )
        assert not result.was_downsampled
        assert result.safe_row_limit is None
        assert result.warning is None
        assert result.bytes_per_row > 0

    def test_downsample_when_ram_insufficient(self, tmp_path) -> None:
        # Create a source file claiming many rows
        path = tmp_path / "big.parquet"
        pl.DataFrame({"a": range(1000)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        # Pretend we only have 1 KB of RAM
        with patch("haute._ram_estimate.available_ram_bytes", return_value=1024):
            result = estimate_safe_training_rows(
                graph,
                target.id,
                _build_dummy_node_fn,
            )
        assert result.was_downsampled
        assert result.safe_row_limit is not None
        assert result.safe_row_limit < 1000
        assert result.warning is not None
        assert "downsampled" in result.warning.lower()
        assert result.total_rows == 1000

    def test_returns_no_limit_when_source_rows_unknown(self) -> None:
        # Databricks source — no row count available
        src = _make_source_node(
            node_type="dataSource",
            config={"sourceType": "databricks", "table": "cat.schema.tbl"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        result = estimate_safe_training_rows(
            graph,
            target.id,
            _build_dummy_node_fn,
        )
        assert not result.was_downsampled
        assert result.safe_row_limit is None
        assert result.warning is None

    def test_warning_includes_row_counts(self, tmp_path) -> None:
        path = tmp_path / "medium.parquet"
        pl.DataFrame({"a": range(50_000)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        # Very low RAM to force downsampling
        with patch("haute._ram_estimate.available_ram_bytes", return_value=5000):
            result = estimate_safe_training_rows(
                graph,
                target.id,
                _build_dummy_node_fn,
            )
        assert result.was_downsampled
        assert "50,000" in result.warning
        assert result.total_rows == 50_000

    def test_safe_row_limit_respects_minimum(self, tmp_path) -> None:
        path = tmp_path / "tiny_ram.parquet"
        pl.DataFrame({"a": range(10_000)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        # Absurdly low RAM — should clamp to _MIN_PROBE_ROWS (500)
        with patch("haute._ram_estimate.available_ram_bytes", return_value=1):
            result = estimate_safe_training_rows(
                graph,
                target.id,
                _build_dummy_node_fn,
            )
        assert result.was_downsampled
        assert result.safe_row_limit >= 500

    def test_probe_columns_populated(self, tmp_path) -> None:
        """RamEstimate should include the probe column count."""
        path = tmp_path / "cols.parquet"
        pl.DataFrame({"a": range(100), "b": range(100)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        result = estimate_safe_training_rows(
            graph,
            target.id,
            _build_dummy_node_fn,
        )
        # Column count is resolved from the parquet file metadata (2 cols)
        assert result.probe_columns == 2


# ---------------------------------------------------------------------------
# GPU VRAM estimation
# ---------------------------------------------------------------------------


class TestAvailableVram:
    def test_returns_int_or_none(self) -> None:
        result = available_vram_bytes()
        assert result is None or (isinstance(result, int) and result > 0)

    def test_returns_none_when_nvidia_smi_missing(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert available_vram_bytes() is None


class TestEstimateGpuVram:
    def test_returns_positive_int(self) -> None:
        result = estimate_gpu_vram_bytes(1000, 50)
        assert isinstance(result, int)
        assert result > 0

    def test_scales_with_rows(self) -> None:
        # Use large row counts where data dominates over constant histogram cost
        small = estimate_gpu_vram_bytes(100_000, 100)
        large = estimate_gpu_vram_bytes(1_000_000, 100)
        assert large > small
        # Should scale roughly linearly with rows (histograms are constant)
        assert large / small > 5

    def test_scales_with_features(self) -> None:
        few = estimate_gpu_vram_bytes(10_000, 10)
        many = estimate_gpu_vram_bytes(10_000, 100)
        assert many > few

    def test_realistic_estimate_8gb_gpu(self) -> None:
        """10M rows × 100 features should exceed 8 GB VRAM."""
        estimate = estimate_gpu_vram_bytes(10_000_000, 100)
        eight_gb = 8 * 1024**3
        assert estimate > eight_gb, f"Expected >8 GB for 10M×100, got {estimate / 1024**3:.1f} GB"

    def test_small_dataset_fits_in_8gb(self) -> None:
        """100K rows × 50 features should fit in 8 GB."""
        estimate = estimate_gpu_vram_bytes(100_000, 50)
        eight_gb = 8 * 1024**3
        assert estimate < eight_gb, f"Expected <8 GB for 100K×50, got {estimate / 1024**3:.1f} GB"

    def test_depth_affects_estimate(self) -> None:
        shallow = estimate_gpu_vram_bytes(1_000_000, 100, depth=4)
        deep = estimate_gpu_vram_bytes(1_000_000, 100, depth=8)
        assert deep > shallow

    def test_border_count_affects_estimate(self) -> None:
        low = estimate_gpu_vram_bytes(1_000_000, 100, border_count=32)
        high = estimate_gpu_vram_bytes(1_000_000, 100, border_count=254)
        assert high > low


# ---------------------------------------------------------------------------
# _estimate_peak_bytes
# ---------------------------------------------------------------------------


class TestEstimatePeakBytes:
    def test_basic_formula(self) -> None:
        result = _estimate_peak_bytes(1000, 10)
        assert result == 1000 * 10 * 8 * 3.0

    def test_zero_rows(self) -> None:
        assert _estimate_peak_bytes(0, 10) == 0

    def test_zero_columns(self) -> None:
        assert _estimate_peak_bytes(1000, 0) == 0

    def test_large_values(self) -> None:
        n_rows = 10_000_000
        n_cols = 200
        expected = int(n_rows * n_cols * 8 * 3.0)
        assert _estimate_peak_bytes(n_rows, n_cols) == expected


# ---------------------------------------------------------------------------
# _csv_row_count edge cases
# ---------------------------------------------------------------------------


class TestCsvRowCountEdgeCases:
    def test_header_only(self, tmp_path) -> None:
        path = tmp_path / "header_only.csv"
        path.write_text("a,b,c\n")
        assert _csv_row_count(str(path)) == 0

    def test_empty_file(self, tmp_path) -> None:
        path = tmp_path / "empty.csv"
        path.write_bytes(b"")
        assert _csv_row_count(str(path)) == 0

    def test_single_data_row(self, tmp_path) -> None:
        path = tmp_path / "one_row.csv"
        path.write_text("a,b\n1,2\n")
        assert _csv_row_count(str(path)) == 1


# ---------------------------------------------------------------------------
# _jsonl_row_count
# ---------------------------------------------------------------------------


class TestJsonlRowCount:
    def test_multiple_lines(self, tmp_path) -> None:
        path = tmp_path / "data.jsonl"
        lines = [b'{"a":1}\n', b'{"a":2}\n', b'{"a":3}\n']
        path.write_bytes(b"".join(lines))
        assert _jsonl_row_count(str(path)) == 3

    def test_empty_file(self, tmp_path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_bytes(b"")
        assert _jsonl_row_count(str(path)) == 0

    def test_single_line(self, tmp_path) -> None:
        path = tmp_path / "single.jsonl"
        path.write_bytes(b'{"x":1}\n')
        assert _jsonl_row_count(str(path)) == 1


# ---------------------------------------------------------------------------
# estimate_gpu_vram_bytes edge cases
# ---------------------------------------------------------------------------


class TestEstimateGpuVramEdgeCases:
    def test_zero_rows(self) -> None:
        result = estimate_gpu_vram_bytes(0, 50)
        assert result >= 0

    def test_zero_features(self) -> None:
        result = estimate_gpu_vram_bytes(1000, 0)
        assert result >= 0

    def test_depth_zero_caps_at_one_leaf(self) -> None:
        result = estimate_gpu_vram_bytes(1000, 50, depth=0)
        n_leaves = 2**0  # 1
        expected_hist = 50 * 128 * n_leaves * 8
        feature_bytes = 1000 * 50 * 5
        per_row_bytes = 1000 * 12
        expected = int((feature_bytes + per_row_bytes + expected_hist) * 2.0)
        assert result == expected

    def test_depth_above_10_caps_at_1024_leaves(self) -> None:
        result_11 = estimate_gpu_vram_bytes(1000, 50, depth=11)
        result_10 = estimate_gpu_vram_bytes(1000, 50, depth=10)
        result_20 = estimate_gpu_vram_bytes(1000, 50, depth=20)
        assert result_11 == result_10
        assert result_20 == result_10

    def test_border_count_zero(self) -> None:
        result = estimate_gpu_vram_bytes(1000, 50, border_count=0)
        feature_bytes = 1000 * 50 * 5
        per_row_bytes = 1000 * 12
        expected = int((feature_bytes + per_row_bytes + 0) * 2.0)
        assert result == expected


# ---------------------------------------------------------------------------
# _parquet_metadata edge cases
# ---------------------------------------------------------------------------


class TestParquetMetadataEdgeCases:
    def test_returns_row_and_column_count(self, tmp_path) -> None:
        path = tmp_path / "meta.parquet"
        df = pl.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6], "z": [7, 8, 9]})
        df.write_parquet(str(path))
        rows, cols = _parquet_metadata(str(path))
        assert rows == 3
        assert cols == 3

    def test_nonexistent_file_raises(self, tmp_path) -> None:
        import pytest

        with pytest.raises(Exception):
            _parquet_metadata(str(tmp_path / "does_not_exist.parquet"))


# ---------------------------------------------------------------------------
# estimate_source_rows edge cases
# ---------------------------------------------------------------------------


class TestEstimateSourceRowsEdgeCases:
    def test_returns_none_for_databricks(self) -> None:
        node = _make_source_node(
            node_type="dataSource",
            config={"sourceType": "databricks", "table": "db.schema.tbl"},
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) is None

    def test_returns_none_for_missing_file(self) -> None:
        node = _make_source_node(
            node_type="dataSource",
            config={"path": "/no/such/file.parquet", "sourceType": "flat_file"},
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) is None

    def test_max_across_multiple_sources(self, tmp_path) -> None:
        p1 = tmp_path / "a.parquet"
        pl.DataFrame({"x": range(200)}).write_parquet(str(p1))
        p2 = tmp_path / "b.parquet"
        pl.DataFrame({"x": range(3000)}).write_parquet(str(p2))

        n1 = _make_source_node(
            node_id="s1",
            node_type="dataSource",
            config={"path": str(p1), "sourceType": "flat_file"},
        )
        n2 = _make_source_node(
            node_id="s2",
            node_type="dataSource",
            config={"path": str(p2), "sourceType": "flat_file"},
        )
        graph = PipelineGraph(nodes=[n1, n2], edges=[])
        assert estimate_source_rows(graph) == 3000

    def test_json_jsonl_source(self, tmp_path) -> None:
        path = tmp_path / "data.jsonl"
        path.write_bytes(b'{"a":1}\n{"a":2}\n{"a":3}\n')

        node = _make_source_node(
            node_type="apiInput",
            config={"path": str(path)},
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) == 3

    def test_csv_datasource(self, tmp_path) -> None:
        path = tmp_path / "data.csv"
        pl.DataFrame({"a": range(42)}).write_csv(str(path))

        node = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) == 42

    def test_ignores_non_source_nodes(self) -> None:
        node = _make_transform_node(node_id="transform")
        graph = PipelineGraph(nodes=[node], edges=[])

        assert estimate_source_rows(graph) is None


# ---------------------------------------------------------------------------
# estimate_safe_training_rows edge cases
# ---------------------------------------------------------------------------


class TestEstimateSafeTrainingRowsEdgeCases:
    def test_no_downsample_when_ram_sufficient(self, tmp_path) -> None:
        path = tmp_path / "small.parquet"
        pl.DataFrame({"a": range(50), "b": range(50)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)
        assert not result.was_downsampled
        assert result.safe_row_limit is None
        assert result.warning is None

    def test_downsample_when_ram_insufficient(self, tmp_path) -> None:
        path = tmp_path / "big.parquet"
        pl.DataFrame({"a": range(5000), "b": range(5000)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        with patch("haute._ram_estimate.available_ram_bytes", return_value=512):
            result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)
        assert result.was_downsampled
        assert result.safe_row_limit is not None
        assert result.safe_row_limit < 5000
        assert result.warning is not None

    def test_returns_none_limit_when_source_rows_unknown(self) -> None:
        src = _make_source_node(
            node_type="dataSource",
            config={"sourceType": "databricks", "table": "cat.schema.tbl"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)
        assert result.safe_row_limit is None
        assert result.total_rows is None
        assert not result.was_downsampled

    def test_safe_row_limit_respects_minimum(self, tmp_path) -> None:
        path = tmp_path / "rows.parquet"
        pl.DataFrame({"a": range(10_000)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        with patch("haute._ram_estimate.available_ram_bytes", return_value=1):
            result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)
        assert result.was_downsampled
        assert result.safe_row_limit >= 500

    def test_probe_columns_populated(self, tmp_path) -> None:
        path = tmp_path / "cols.parquet"
        pl.DataFrame({"a": [1], "b": [2], "c": [3]}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)
        assert result.probe_columns == 3


# ---------------------------------------------------------------------------
# available_ram_bytes — platform-specific paths
# ---------------------------------------------------------------------------


class TestAvailableRamPlatformPaths:
    def test_linux_proc_meminfo(self) -> None:
        """Successful /proc/meminfo read returns parsed MemAvailable."""
        fake_meminfo = (
            "MemTotal:       16384000 kB\n"
            "MemFree:         2000000 kB\n"
            "MemAvailable:    8000000 kB\n"
        )
        from io import StringIO

        with patch("builtins.open", return_value=StringIO(fake_meminfo)):
            result = available_ram_bytes()
        assert result == 8_000_000 * 1024

    def test_linux_proc_meminfo_no_memavailable_falls_through(self) -> None:
        """If MemAvailable line is absent, falls through to sysconf."""
        fake_meminfo = "MemTotal:       16384000 kB\nMemFree:  2000000 kB\n"
        from io import StringIO

        with patch("builtins.open", return_value=StringIO(fake_meminfo)):
            # sysconf path should be tried next
            with patch("os.sysconf", side_effect=[4096, 4096], create=True):
                result = available_ram_bytes()
        assert result == 4096 * 4096

    def test_macos_sysconf_path(self) -> None:
        """When /proc/meminfo fails, sysconf is used."""
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=[2000, 4096], create=True):
                result = available_ram_bytes()
        assert result == 2000 * 4096

    def test_non_positive_sysconf_values_fall_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid POSIX memory values are ignored instead of producing zero RAM."""
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=[0, 4096], create=True),
        ):
            result = available_ram_bytes()

        assert result == 4 * 1024**3

    def test_windows_global_memory_status_false_falls_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed Windows memory probe falls through to the conservative default."""
        from types import SimpleNamespace

        status_probe = MagicMock(return_value=False)
        fake_ctypes = SimpleNamespace(
            Structure=object,
            c_ulong=int,
            c_ulonglong=int,
            sizeof=lambda _value: 1,
            byref=lambda value: value,
            windll=SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=status_probe)),
        )

        monkeypatch.setattr("sys.platform", "win32")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=AttributeError, create=True),
            patch.dict("sys.modules", {"ctypes": fake_ctypes}),
        ):
            result = available_ram_bytes()

        assert result == 4 * 1024**3
        status_probe.assert_called_once()

    @pytest.mark.skipif(sys.platform != "win32", reason="ctypes.windll only exists on Windows")
    def test_windows_ctypes_failure_falls_to_4gib(self, monkeypatch) -> None:
        """When GlobalMemoryStatusEx raises OSError, falls to 4 GiB."""
        monkeypatch.setattr("sys.platform", "win32")
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=AttributeError, create=True):
                with patch(
                    "ctypes.windll.kernel32.GlobalMemoryStatusEx",
                    side_effect=OSError,
                    create=True,
                ):
                    result = available_ram_bytes()
        assert result == 4 * 1024**3


# ---------------------------------------------------------------------------
# available_vram_bytes — nvidia-smi parsing
# ---------------------------------------------------------------------------


class TestAvailableVramParsing:
    def test_successful_nvidia_smi_single_gpu(self) -> None:
        """Parse nvidia-smi output for a single GPU."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "8192\n"
        with patch("subprocess.run", return_value=mock_result):
            result = available_vram_bytes()
        assert result == 8192 * 1024 * 1024

    def test_successful_nvidia_smi_multiple_gpus(self) -> None:
        """With multiple GPUs, the first line is used."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "16384\n8192\n"
        with patch("subprocess.run", return_value=mock_result):
            result = available_vram_bytes()
        # First GPU's VRAM
        assert result == 16384 * 1024 * 1024

    def test_nvidia_smi_nonzero_returncode(self) -> None:
        """Non-zero returncode means no GPU detected."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = available_vram_bytes()
        assert result is None

    def test_nvidia_smi_timeout(self) -> None:
        """TimeoutExpired returns None."""
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            result = available_vram_bytes()
        assert result is None

    def test_nvidia_smi_oserror(self) -> None:
        """OSError returns None."""
        with patch("subprocess.run", side_effect=OSError):
            result = available_vram_bytes()
        assert result is None


# ---------------------------------------------------------------------------
# _count_source_rows_for_node — unit tests
# ---------------------------------------------------------------------------


class TestCountSourceRowsForNode:
    def test_api_input_json_uncached_returns_none(self) -> None:
        """API_INPUT .json with no cache returns None (v2 has no aggregate row count)."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": "/nonexistent/data.json"},
        )
        result = _count_source_rows_for_node(node)
        assert result is None

    def test_api_input_jsonl_uncached_file_exists(self, tmp_path) -> None:
        """API_INPUT .jsonl with no cache but file exists uses line count."""
        path = tmp_path / "data.jsonl"
        path.write_bytes(b'{"a":1}\n{"a":2}\n{"a":3}\n{"a":4}\n')
        node = _make_source_node(
            node_type="apiInput",
            config={"path": str(path)},
        )
        result = _count_source_rows_for_node(node)
        assert result == 4

    def test_api_input_parquet_exists(self, tmp_path) -> None:
        """API_INPUT with existing parquet file reads metadata."""
        path = tmp_path / "data.parquet"
        pl.DataFrame({"x": range(200), "y": range(200)}).write_parquet(str(path))
        node = _make_source_node(
            node_type="apiInput",
            config={"path": str(path)},
        )
        result = _count_source_rows_for_node(node)
        assert result == 200

    def test_api_input_parquet_missing_returns_none(self) -> None:
        """API_INPUT with missing parquet path returns None."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": "/nonexistent/data.parquet"},
        )
        result = _count_source_rows_for_node(node)
        assert result is None

    def test_datasource_databricks_returns_none(self) -> None:
        """DATA_SOURCE with databricks sourceType returns None."""
        node = _make_source_node(
            node_type="dataSource",
            config={"sourceType": "databricks", "table": "db.tbl"},
        )
        result = _count_source_rows_for_node(node)
        assert result is None

    def test_datasource_csv(self, tmp_path) -> None:
        """DATA_SOURCE with CSV file counts lines."""
        path = tmp_path / "data.csv"
        pl.DataFrame({"a": range(77)}).write_csv(str(path))
        node = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        result = _count_source_rows_for_node(node)
        assert result == 77

    def test_datasource_existing_unsupported_file_returns_none(self, tmp_path) -> None:
        """Existing flat files only provide row estimates for known tabular formats."""
        path = tmp_path / "notes.txt"
        path.write_text("not,a,supported,table\n", encoding="utf-8")
        node = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )

        assert _count_source_rows_for_node(node) is None

    def test_exception_returns_none(self) -> None:
        """If _parquet_metadata raises, returns None and logs warning."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": "/some/file.parquet"},
        )
        with patch("haute._ram_estimate.Path") as mock_path:
            mock_path.return_value.exists.side_effect = RuntimeError("boom")
            # The path check `Path(path).exists()` will raise
            result = _count_source_rows_for_node(node)
        assert result is None

    def test_unknown_node_type_returns_none(self) -> None:
        """A node type that's neither API_INPUT nor DATA_SOURCE returns None."""
        node = _make_source_node(
            node_type="polars",
            config={"path": "/some/file.parquet"},
        )
        result = _count_source_rows_for_node(node)
        assert result is None


# ---------------------------------------------------------------------------
# _source_metadata_for_node — unit tests
# ---------------------------------------------------------------------------


class TestSourceMetadataForNode:
    def test_api_input_json_returns_none(self) -> None:
        """API_INPUT with .json path returns None (v2 has no single aggregate metadata)."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": "/data/test.json"},
        )
        result = _source_metadata_for_node(node)
        assert result is None

    def test_api_input_jsonl_returns_none(self) -> None:
        """API_INPUT .jsonl returns None (v2 per-port cache has no single metadata)."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": "/data/test.jsonl"},
        )
        result = _source_metadata_for_node(node)
        assert result is None

    def test_api_input_parquet(self, tmp_path) -> None:
        """API_INPUT with parquet returns metadata."""
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]}).write_parquet(str(path))
        node = _make_source_node(
            node_type="apiInput",
            config={"path": str(path)},
        )
        result = _source_metadata_for_node(node)
        assert result == (3, 2)

    def test_api_input_parquet_missing(self) -> None:
        """API_INPUT with missing parquet returns None."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": "/nonexistent/data.parquet"},
        )
        result = _source_metadata_for_node(node)
        assert result is None

    def test_datasource_databricks_returns_none(self) -> None:
        """DATA_SOURCE databricks returns None."""
        node = _make_source_node(
            node_type="dataSource",
            config={"path": "/some/path", "sourceType": "databricks"},
        )
        result = _source_metadata_for_node(node)
        assert result is None

    def test_datasource_parquet(self, tmp_path) -> None:
        """DATA_SOURCE with parquet returns metadata."""
        path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1], "y": [2], "z": [3]}).write_parquet(str(path))
        node = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        result = _source_metadata_for_node(node)
        assert result == (1, 3)

    def test_datasource_non_parquet_returns_none(self, tmp_path) -> None:
        """DATA_SOURCE with CSV file returns None (metadata only for parquet)."""
        path = tmp_path / "data.csv"
        path.write_text("a,b\n1,2\n")
        node = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        result = _source_metadata_for_node(node)
        assert result is None

    def test_empty_path_returns_none(self) -> None:
        """Node with empty path returns None."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": ""},
        )
        result = _source_metadata_for_node(node)
        assert result is None

    def test_exception_returns_none(self) -> None:
        """Exception during metadata read returns None."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": "/some/file.parquet"},
        )
        with patch("haute._ram_estimate.Path") as mock_path:
            mock_path.return_value.exists.side_effect = RuntimeError("boom")
            result = _source_metadata_for_node(node)
        assert result is None

    def test_unknown_node_type_returns_none(self) -> None:
        """Polars node type falls through to None."""
        node = _make_source_node(
            node_type="polars",
            config={"path": "/some/file.parquet"},
        )
        result = _source_metadata_for_node(node)
        assert result is None


# ---------------------------------------------------------------------------
# _ancestor_source_metadata — graph traversal
# ---------------------------------------------------------------------------


class TestAncestorSourceMetadata:
    def test_multiple_sources_returns_max(self, tmp_path) -> None:
        """With two sources, returns max rows and max cols."""
        p1 = tmp_path / "small.parquet"
        pl.DataFrame({"a": range(100)}).write_parquet(str(p1))
        p2 = tmp_path / "big.parquet"
        pl.DataFrame({"x": range(500), "y": range(500), "z": range(500)}).write_parquet(str(p2))

        s1 = _make_source_node(
            node_id="s1",
            node_type="dataSource",
            config={"path": str(p1), "sourceType": "flat_file"},
        )
        s2 = _make_source_node(
            node_id="s2",
            node_type="dataSource",
            config={"path": str(p2), "sourceType": "flat_file"},
        )
        target = _make_modelling_node(node_id="m1")
        edges = [
            GraphEdge(id="e1", source="s1", target="m1"),
            GraphEdge(id="e2", source="s2", target="m1"),
        ]
        graph = PipelineGraph(nodes=[s1, s2, target], edges=edges)
        rows, cols = _ancestor_source_metadata(graph, "m1")
        assert rows == 500
        assert cols == 3

    def test_later_smaller_source_keeps_max_rows_but_updates_max_cols(self, tmp_path) -> None:
        """Ancestor metadata keeps independent maxima for rows and column width."""
        p1 = tmp_path / "big_rows.parquet"
        pl.DataFrame({"a": range(500)}).write_parquet(str(p1))
        p2 = tmp_path / "wide.parquet"
        pl.DataFrame({"x": range(100), "y": range(100), "z": range(100)}).write_parquet(str(p2))

        s1 = _make_source_node(
            node_id="s1",
            node_type="dataSource",
            config={"path": str(p1), "sourceType": "flat_file"},
        )
        s2 = _make_source_node(
            node_id="s2",
            node_type="dataSource",
            config={"path": str(p2), "sourceType": "flat_file"},
        )
        target = _make_modelling_node(node_id="m1")
        graph = PipelineGraph(
            nodes=[s1, s2, target],
            edges=[
                GraphEdge(id="e1", source="s1", target="m1"),
                GraphEdge(id="e2", source="s2", target="m1"),
            ],
        )

        rows, cols = _ancestor_source_metadata(graph, "m1")

        assert rows == 500
        assert cols == 3

    def test_no_source_nodes(self) -> None:
        """Graph with only transform + model returns (None, 0)."""
        t1 = _make_transform_node(node_id="t1")
        m1 = _make_modelling_node(node_id="m1")
        edges = [GraphEdge(id="e1", source="t1", target="m1")]
        graph = PipelineGraph(nodes=[t1, m1], edges=edges)
        rows, cols = _ancestor_source_metadata(graph, "m1")
        assert rows is None
        assert cols == 0

    def test_skips_unknown_ancestor_ids(self, tmp_path) -> None:
        """If ancestors() returns an ID not in node_map, it is skipped."""
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": range(50)}).write_parquet(str(path))

        src = _make_source_node(
            node_id="s1",
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        m1 = _make_modelling_node(node_id="m1")
        edges = [GraphEdge(id="e1", source="s1", target="m1")]
        graph = PipelineGraph(nodes=[src, m1], edges=edges)

        # Patch ancestors to also return a ghost ID not in node_map
        with patch(
            "haute._topo.ancestors",
            return_value={"s1", "ghost_node"},
        ):
            rows, cols = _ancestor_source_metadata(graph, "m1")
        assert rows == 50
        assert cols == 1

    def test_skips_non_source_ancestors(self, tmp_path) -> None:
        """Transform nodes in the chain are skipped, source is found."""
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": range(300), "b": range(300)}).write_parquet(str(path))

        src = _make_source_node(
            node_id="s1",
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        t1 = _make_transform_node(node_id="t1")
        m1 = _make_modelling_node(node_id="m1")
        edges = [
            GraphEdge(id="e1", source="s1", target="t1"),
            GraphEdge(id="e2", source="t1", target="m1"),
        ]
        graph = PipelineGraph(nodes=[src, t1, m1], edges=edges)
        rows, cols = _ancestor_source_metadata(graph, "m1")
        assert rows == 300
        assert cols == 2


# ---------------------------------------------------------------------------
# _resolve_target_columns — BFS column resolution
# ---------------------------------------------------------------------------


class TestResolveTargetColumns:
    def test_selected_columns_at_target(self) -> None:
        """If the target node has selected_columns, return its length."""
        m1 = _make_modelling_node(
            node_id="m1",
            config={"selected_columns": ["a", "b", "c"]},
        )
        graph = PipelineGraph(nodes=[m1], edges=[])
        result = _resolve_target_columns(graph, "m1", "live")
        assert result == 3

    def test_bfs_finds_selected_columns_on_parent(self) -> None:
        """BFS traverses to parent that has selected_columns."""
        t1 = _make_transform_node(
            node_id="t1",
            config={"selected_columns": ["x", "y"]},
        )
        m1 = _make_modelling_node(node_id="m1")
        edges = [GraphEdge(id="e1", source="t1", target="m1")]
        graph = PipelineGraph(nodes=[t1, m1], edges=edges)
        result = _resolve_target_columns(graph, "m1", "live")
        assert result == 2

    def test_falls_back_to_source_metadata(self, tmp_path) -> None:
        """With no selected_columns, BFS falls back to source metadata."""
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": [1], "b": [2], "c": [3], "d": [4]}).write_parquet(str(path))

        src = _make_source_node(
            node_id="s1",
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        m1 = _make_modelling_node(node_id="m1")
        edges = [GraphEdge(id="e1", source="s1", target="m1")]
        graph = PipelineGraph(nodes=[src, m1], edges=edges)
        result = _resolve_target_columns(graph, "m1", "live")
        assert result == 4

    def test_returns_none_when_no_columns_found(self) -> None:
        """If no node has columns info, returns None."""
        m1 = _make_modelling_node(node_id="m1")
        graph = PipelineGraph(nodes=[m1], edges=[])
        result = _resolve_target_columns(graph, "m1", "live")
        assert result is None

    def test_source_without_metadata_does_not_stop_parent_search(self) -> None:
        """A source node with no metadata is treated like an unknown column source."""
        src = _make_source_node(
            node_id="s1",
            node_type="dataSource",
            config={"path": "/missing/source.parquet", "sourceType": "flat_file"},
        )
        m1 = _make_modelling_node(node_id="m1")
        graph = PipelineGraph(
            nodes=[src, m1],
            edges=[GraphEdge(id="e1", source="s1", target="m1")],
        )

        result = _resolve_target_columns(graph, "m1", "live")

        assert result is None

    def test_skips_node_not_in_map(self) -> None:
        """If a parent ID is not in node_map, it is skipped gracefully."""
        m1 = _make_modelling_node(node_id="m1")
        # Create an edge referencing a source node that is NOT in the nodes list
        edges = [GraphEdge(id="e1", source="ghost", target="m1")]
        graph = PipelineGraph(nodes=[m1], edges=edges)
        result = _resolve_target_columns(graph, "m1", "live")
        assert result is None

    def test_skips_already_visited_nodes(self) -> None:
        """Cycles or diamond graphs don't cause infinite loops."""
        t1 = _make_transform_node(node_id="t1")
        t2 = _make_transform_node(node_id="t2")
        m1 = _make_modelling_node(node_id="m1")
        edges = [
            GraphEdge(id="e1", source="t1", target="t2"),
            GraphEdge(id="e2", source="t2", target="m1"),
            GraphEdge(id="e3", source="t1", target="m1"),
        ]
        graph = PipelineGraph(nodes=[t1, t2, m1], edges=edges)
        # Should not hang; returns None since no columns info
        result = _resolve_target_columns(graph, "m1", "live")
        assert result is None


# ---------------------------------------------------------------------------
# RamEstimate — NamedTuple field access
# ---------------------------------------------------------------------------


class TestRamEstimateFields:
    def test_field_access(self) -> None:
        est = RamEstimate(
            safe_row_limit=1000,
            total_rows=5000,
            estimated_bytes=240_000,
            available_bytes=1_000_000,
            bytes_per_row=48.0,
            was_downsampled=True,
            warning="downsampled",
            probe_columns=10,
        )
        assert est.safe_row_limit == 1000
        assert est.total_rows == 5000
        assert est.estimated_bytes == 240_000
        assert est.available_bytes == 1_000_000
        assert est.bytes_per_row == 48.0
        assert est.was_downsampled is True
        assert est.warning == "downsampled"
        assert est.probe_columns == 10

    def test_bytes_per_row_calculation(self) -> None:
        """bytes_per_row = n_cols * 8 * 3.0 (overhead)."""
        n_cols = 10
        expected_bpr = n_cols * 8 * 3.0
        est = RamEstimate(
            safe_row_limit=None,
            total_rows=100,
            estimated_bytes=int(100 * expected_bpr),
            available_bytes=999_999_999,
            bytes_per_row=expected_bpr,
            was_downsampled=False,
            warning=None,
            probe_columns=n_cols,
        )
        assert est.bytes_per_row == expected_bpr

    def test_default_probe_columns(self) -> None:
        """probe_columns defaults to 0."""
        est = RamEstimate(
            safe_row_limit=None,
            total_rows=None,
            estimated_bytes=0,
            available_bytes=1,
            bytes_per_row=0,
            was_downsampled=False,
            warning=None,
        )
        assert est.probe_columns == 0


# ---------------------------------------------------------------------------
# estimate_safe_training_rows — schema unavailable path
# ---------------------------------------------------------------------------


class TestEstimateSafeTrainingRowsSchemaUnavailable:
    def test_returns_no_limit_when_columns_unknown(self, tmp_path) -> None:
        """When _resolve_target_columns returns None, no downsample."""
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": range(100)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataSource",
            config={"path": str(path), "sourceType": "flat_file"},
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        with patch("haute._ram_estimate._resolve_target_columns", return_value=None):
            result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)
        assert not result.was_downsampled
        assert result.safe_row_limit is None
        assert result.total_rows == 100
        assert result.bytes_per_row == 0
        assert result.probe_columns == 0
