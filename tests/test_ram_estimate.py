"""Tests for haute._ram_estimate — RAM estimation and safe downsampling."""

from __future__ import annotations

import math
import sys
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from structlog.testing import capture_logs

from haute._polars_io_registry import data_input_is_direct
from haute._polars_utils import read_parquet_metadata
from haute._ram_estimate import (
    MaterialisationEstimate,
    MaterialisationEstimateState,
    RamEstimate,
    _count_source_rows_for_node,
    _csv_row_count,
    _dedupe_resolved_columns,
    _detailed_source_metadata_for_node,
    _DetailedSourceMetadata,
    _edge_join_key_columns_on_path,
    _estimate_base_bytes_per_row,
    _estimate_peak_bytes,
    _EstimateGraphIndex,
    _jsonl_row_count,
    _parquet_metadata,
    _resolve_edge_join_column_names,
    _resolve_target_column_names,
    _resolve_target_columns,
    _source_column_base_widths,
    available_ram_bytes,
    available_vram_bytes,
    estimate_gpu_vram_bytes,
    estimate_materialisation_boundary,
    estimate_safe_training_rows,
    estimate_source_rows,
)
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph
from tests.conftest import build_test_input_snapshot

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file_input_config(path: object, *, format: str | None = None) -> dict[str, object]:
    path_string = str(path)
    if format is None:
        suffix = path_string.lower().rsplit(".", 1)[-1] if "." in path_string else ""
        format = {
            "parquet": "parquet",
            "csv": "csv",
            "jsonl": "ndjson",
            "ndjson": "ndjson",
        }.get(suffix, "parquet")
    return {
        "inputType": "file",
        "format": format,
        "mode": "scan",
        "path": path_string,
        "arguments": {},
    }


def _ready_file_input_config(
    path: object,
    *,
    format: str | None = None,
) -> dict[str, object]:
    config = _file_input_config(path, format=format)
    if not data_input_is_direct(config):
        build_test_input_snapshot(config)
    return config


def _databricks_input_config(table: str) -> dict[str, object]:
    return {
        "inputType": "databricks",
        "http_path": "/sql/1.0/warehouses/test",
        "table": table,
        "arguments": {},
    }


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


def test_materialisation_estimate_distinguishes_empty_from_unavailable() -> None:
    empty = MaterialisationEstimate.available(0)
    unavailable = MaterialisationEstimate.unavailable("metadata_unavailable")

    assert empty.state is MaterialisationEstimateState.AVAILABLE
    assert empty.estimated_peak_bytes == 0
    assert unavailable.state is MaterialisationEstimateState.UNAVAILABLE
    assert unavailable.estimated_peak_bytes is None
    with pytest.raises(ValueError):
        MaterialisationEstimate(MaterialisationEstimateState.AVAILABLE, None)
    with pytest.raises(ValueError):
        MaterialisationEstimate(MaterialisationEstimateState.UNAVAILABLE, 0)
    with pytest.raises(ValueError, match="has no unavailable reason"):
        MaterialisationEstimate(
            MaterialisationEstimateState.AVAILABLE,
            1,
            unavailable_reason="not allowed",
        )
    with pytest.raises(ValueError, match="requires a reason"):
        MaterialisationEstimate.unavailable("")


def test_ram_estimate_column_index_rejects_recursive_resolution() -> None:
    source = _make_source_node()
    index = _EstimateGraphIndex.build(PipelineGraph(nodes=[source], edges=[]), "live")
    index.resolving_targets.add(source.id)

    with pytest.raises(RuntimeError, match="cycle encountered"):
        index.resolve_columns(source.id)


def test_source_metadata_propagates_programming_errors_but_marks_os_errors_unavailable(
    tmp_path,
) -> None:
    path = tmp_path / "source.parquet"
    pl.DataFrame({"value": [1]}).write_parquet(path)
    node = _make_source_node(
        node_type="dataInput",
        config=_ready_file_input_config(path),
    )

    with patch("haute._ram_estimate._detailed_parquet_metadata", side_effect=KeyError("bug")):
        with pytest.raises(KeyError, match="bug"):
            _detailed_source_metadata_for_node(node)

    with patch("haute._ram_estimate._detailed_parquet_metadata", side_effect=OSError("offline")):
        assert _detailed_source_metadata_for_node(node) is None


@pytest.mark.parametrize("suffix", [None, ".json", ".parquet"])
def test_api_input_detailed_metadata_reports_unavailable_sources(
    tmp_path,
    suffix: str | None,
) -> None:
    path = "" if suffix is None else str(tmp_path / f"missing{suffix}")
    node = _make_source_node(node_type="apiInput", config={"path": path})

    assert _detailed_source_metadata_for_node(node) is None


def test_api_input_detailed_metadata_reads_existing_parquet(tmp_path) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"quote_id": [1, 2], "premium": [100.0, 125.0]}).write_parquet(path)
    node = _make_source_node(node_type="apiInput", config={"path": str(path)})

    metadata = _detailed_source_metadata_for_node(node)

    assert metadata is not None
    assert metadata.row_count == 2
    assert metadata.column_count == 2
    assert metadata.column_width_keys == {
        "quote_id": f"{node.id}\0quote_id",
        "premium": f"{node.id}\0premium",
    }


def test_non_source_node_has_no_detailed_metadata() -> None:
    node = _make_source_node(
        node_type="polars",
        config={"path": "/unused/source.parquet"},
    )

    assert _detailed_source_metadata_for_node(node) is None


def test_low_cardinality_wide_strings_use_expanded_probe_width(tmp_path) -> None:
    path = tmp_path / "wide_dictionary.parquet"
    wide_value = "x" * 2048
    pl.DataFrame({"category": [wide_value] * 256}).write_parquet(path)
    node = _make_source_node(
        node_type="dataInput",
        config=_ready_file_input_config(path),
    )

    metadata = _detailed_source_metadata_for_node(node)

    assert metadata is not None
    widths = _source_column_base_widths((metadata,))
    assert widths["category"] >= len(wide_value)


def test_materialisation_estimate_reports_known_empty_parquet_as_available_zero(tmp_path) -> None:
    path = tmp_path / "empty.parquet"
    pl.DataFrame(schema={"x": pl.Int64}).write_parquet(path)
    source = _make_source_node(
        node_type="dataInput",
        config=_ready_file_input_config(path),
    )
    graph = PipelineGraph(nodes=[source], edges=[])

    estimate = estimate_materialisation_boundary(graph, source.id)

    assert estimate.state is MaterialisationEstimateState.AVAILABLE
    assert estimate.estimated_peak_bytes == 0


def test_materialisation_estimate_reads_each_source_metadata_once(tmp_path) -> None:
    path = tmp_path / "source.parquet"
    pl.DataFrame({"x": [1, 2, 3]}).write_parquet(path)
    source = _make_source_node(
        node_type="dataInput",
        config=_ready_file_input_config(path),
    )
    target = _make_modelling_node()
    graph = PipelineGraph(
        nodes=[source, target],
        edges=[GraphEdge(id="edge", source=source.id, target=target.id)],
    )

    with patch("haute._ram_estimate.read_parquet_metadata", wraps=read_parquet_metadata) as read:
        estimate = estimate_materialisation_boundary(graph, target.id)

    assert estimate.state is MaterialisationEstimateState.AVAILABLE
    assert read.call_count == 1


def _make_edge_join_node(
    node_id: str = "joined",
    label: str = "joined",
    config: dict | None = None,
) -> GraphNode:
    node = _make_transform_node(node_id=node_id, label=label, config=config)
    node.data.nodeType = "edgeJoin"
    return node


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

    def test_unavailable_probe_does_not_invent_capacity(self, monkeypatch) -> None:
        """When all platform methods fail, capacity is explicitly unavailable."""
        monkeypatch.setattr("sys.platform", "freebsd13")
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=AttributeError, create=True):
                ram = available_ram_bytes()
        assert ram is None

    def test_unavailable_probe_logs_structured_warning(self, monkeypatch) -> None:
        """An unavailable probe is visible in logs with platform context."""
        monkeypatch.setattr("sys.platform", "freebsd13")
        with (
            patch("builtins.open", side_effect=OSError("proc unavailable")),
            patch("os.sysconf", side_effect=AttributeError("no sysconf"), create=True),
            capture_logs() as logs,
        ):
            ram = available_ram_bytes()

        assert ram is None
        unavailable_logs = [
            event for event in logs if event.get("event") == "available_ram_unavailable"
        ]
        assert unavailable_logs
        assert unavailable_logs[0]["platform"] == "freebsd13"
        assert "proc unavailable" in unavailable_logs[0]["proc_meminfo_error"]
        assert "no sysconf" in unavailable_logs[0]["sysconf_error"]
        assert unavailable_logs[0]["windows_attempted"] is False


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
    def test_parquet_data_input(self, tmp_path) -> None:
        path = tmp_path / "data.parquet"
        pl.DataFrame({"a": range(1000)}).write_parquet(str(path))

        node = _make_source_node(
            node_type="dataInput",
            config=_ready_file_input_config(path),
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) == 1000

    def test_returns_none_for_databricks(self) -> None:
        node = _make_source_node(
            node_type="dataInput",
            config=_databricks_input_config("cat.schema.tbl"),
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) is None

    def test_returns_none_for_missing_file(self) -> None:
        node = _make_source_node(
            node_type="dataInput",
            config=_file_input_config("/nonexistent/file.parquet"),
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
            node_type="dataInput",
            config=_ready_file_input_config(p1),
        )
        n2 = _make_source_node(
            node_id="s2",
            label="big",
            node_type="dataInput",
            config=_ready_file_input_config(p2),
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

    if nt in ("dataInput", "apiInput"):
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
            node_type="dataInput",
            config=_file_input_config("data/test.parquet"),
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        return PipelineGraph(nodes=[src, target], edges=[edge])

    def test_no_downsample_when_ram_sufficient(self, tmp_path) -> None:
        # Create a small parquet file
        path = tmp_path / "test.parquet"
        pl.DataFrame({"a": range(100)}).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
            node_type="dataInput",
            config=_databricks_input_config("cat.schema.tbl"),
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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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

    def test_string_heavy_parquet_estimates_above_numeric_width(self, tmp_path) -> None:
        """String parquet metadata should raise the byte estimate without scanning rows."""
        path = tmp_path / "string_heavy.parquet"
        rows = 100
        pl.DataFrame(
            {
                "id": range(rows),
                "description": [f"policy-{i}-" + ("x" * 250) for i in range(rows)],
            }
        ).write_parquet(str(path))

        src = _make_source_node(
            node_type="dataInput",
            config=_ready_file_input_config(path),
        )
        target = _make_modelling_node()
        edge = GraphEdge(id="e1", source=src.id, target=target.id)
        graph = PipelineGraph(nodes=[src, target], edges=[edge])

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        numeric_baseline = rows * 2 * 8 * 3.0
        assert result.estimated_bytes > numeric_baseline
        assert result.bytes_per_row > 2 * 8 * 3.0

    def test_excluded_edge_join_key_still_counts_for_pipeline_peak(self, tmp_path) -> None:
        """Join keys excluded from modelling remain needed during edgeJoin execution."""
        base_path = tmp_path / "base.parquet"
        join_path = tmp_path / "join.parquet"
        rows = 1000
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "premium": [float(i) for i in range(rows)],
                "claim_count": [i % 3 for i in range(rows)],
            }
        ).write_parquet(str(base_path))
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "competitor_premium": [float(i) * 1.1 for i in range(rows)],
            }
        ).write_parquet(str(join_path))

        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_ready_file_input_config(join_path),
        )
        joined = _make_transform_node(
            node_id="joined",
            label="joined",
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "on": ["quote_id"],
                "selected_columns": ["quote_id", "premium", "claim_count"],
            },
        )
        joined.data.nodeType = "edgeJoin"
        target = _make_modelling_node(config={"exclude": ["quote_id"]})
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined"),
                GraphEdge(id="e2", source="join", target="joined"),
                GraphEdge(id="e3", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.probe_columns == 3
        assert result.bytes_per_row == 3 * 8 * 3.0
        assert result.estimated_bytes == _estimate_peak_bytes(rows, 3)

    def test_string_exclude_config_is_ignored_as_invalid_sequence(self, tmp_path) -> None:
        """A bare string is not treated as a sequence of excluded column names."""
        path = tmp_path / "cols.parquet"
        pl.DataFrame({"a": range(20), "b": range(20)}).write_parquet(str(path))
        src = _make_source_node(
            node_type="dataInput",
            config=_ready_file_input_config(path),
        )
        target = _make_modelling_node(config={"exclude": "a"})
        graph = PipelineGraph(
            nodes=[src, target],
            edges=[GraphEdge(id="e1", source=src.id, target=target.id)],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.probe_columns == 2
        assert result.bytes_per_row == 2 * 8 * 3.0

    def test_edge_join_without_selected_columns_counts_both_parent_outputs(
        self,
        tmp_path,
    ) -> None:
        """EdgeJoin output width must include join-side non-key columns."""
        base_path = tmp_path / "base.parquet"
        join_path = tmp_path / "join.parquet"
        rows = 1000
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "premium": [float(i) for i in range(rows)],
                "claim_count": [i % 3 for i in range(rows)],
            }
        ).write_parquet(str(base_path))
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "competitor_premium": [float(i) * 1.1 for i in range(rows)],
            }
        ).write_parquet(str(join_path))

        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_ready_file_input_config(join_path),
        )
        joined = _make_transform_node(
            node_id="joined",
            label="joined",
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "on": ["quote_id"],
            },
        )
        joined.data.nodeType = "edgeJoin"
        target = _make_modelling_node(config={"exclude": ["quote_id"]})
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined"),
                GraphEdge(id="e2", source="join", target="joined"),
                GraphEdge(id="e3", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.probe_columns == 4
        assert result.bytes_per_row == 4 * 8 * 3.0
        assert result.estimated_bytes == _estimate_peak_bytes(rows, 4)

    def test_edge_join_coalesce_false_counts_suffixed_right_key(self, tmp_path) -> None:
        """coalesce=False keeps the join-side key as a suffixed output column."""
        base_path = tmp_path / "base.parquet"
        join_path = tmp_path / "join.parquet"
        rows = 1000
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "premium": [float(i) for i in range(rows)],
                "claim_count": [i % 3 for i in range(rows)],
            }
        ).write_parquet(str(base_path))
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "competitor_premium": [float(i) * 1.1 for i in range(rows)],
            }
        ).write_parquet(str(join_path))

        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_ready_file_input_config(join_path),
        )
        joined = _make_transform_node(
            node_id="joined",
            label="joined",
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "on": ["quote_id"],
                "coalesce": False,
            },
        )
        joined.data.nodeType = "edgeJoin"
        target = _make_modelling_node(config={"exclude": ["quote_id"]})
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined"),
                GraphEdge(id="e2", source="join", target="joined"),
                GraphEdge(id="e3", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.probe_columns == 5
        assert result.bytes_per_row == 5 * 8 * 3.0
        assert result.estimated_bytes == _estimate_peak_bytes(rows, 5)

    def test_excluded_coalesce_false_right_key_still_counts_for_pipeline_peak(
        self,
        tmp_path,
    ) -> None:
        """Excluded suffixed right keys are still materialized by edgeJoin."""
        base_path = tmp_path / "base.parquet"
        join_path = tmp_path / "join.parquet"
        rows = 1000
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "premium": [float(i) for i in range(rows)],
                "claim_count": [i % 3 for i in range(rows)],
            }
        ).write_parquet(str(base_path))
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "competitor_premium": [float(i) * 1.1 for i in range(rows)],
            }
        ).write_parquet(str(join_path))

        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_ready_file_input_config(join_path),
        )
        joined = _make_transform_node(
            node_id="joined",
            label="joined",
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "on": ["quote_id"],
                "coalesce": False,
            },
        )
        joined.data.nodeType = "edgeJoin"
        target = _make_modelling_node(config={"exclude": ["quote_id", "quote_id_right"]})
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined"),
                GraphEdge(id="e2", source="join", target="joined"),
                GraphEdge(id="e3", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.probe_columns == 5
        assert result.bytes_per_row == 5 * 8 * 3.0
        assert result.estimated_bytes == _estimate_peak_bytes(rows, 5)

    def test_edge_join_left_right_on_coalesce_false_suffixes_colliding_right_key(
        self,
        tmp_path,
    ) -> None:
        """leftOn/rightOn right keys still suffix when they collide with base columns."""
        base_path = tmp_path / "base.parquet"
        join_path = tmp_path / "join.parquet"
        rows = 1000
        pl.DataFrame(
            {
                "id": range(rows),
                "jid": [i + 10_000 for i in range(rows)],
                "premium": [float(i) for i in range(rows)],
            }
        ).write_parquet(str(base_path))
        pl.DataFrame(
            {
                "jid": range(rows),
                "competitor_premium": [float(i) * 1.1 for i in range(rows)],
            }
        ).write_parquet(str(join_path))

        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_ready_file_input_config(join_path),
        )
        joined = _make_transform_node(
            node_id="joined",
            label="joined",
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "leftOn": ["id"],
                "rightOn": ["jid"],
                "coalesce": False,
            },
        )
        joined.data.nodeType = "edgeJoin"
        target = _make_modelling_node()
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined"),
                GraphEdge(id="e2", source="join", target="joined"),
                GraphEdge(id="e3", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.probe_columns == 5
        assert result.bytes_per_row == 5 * 8 * 3.0
        assert result.estimated_bytes == _estimate_peak_bytes(rows, 5)

    def test_edge_join_left_right_on_default_coalesces_right_key(
        self,
        tmp_path,
    ) -> None:
        """Default leftOn/rightOn joins coalesce the right key like Polars."""
        base_path = tmp_path / "base.parquet"
        join_path = tmp_path / "join.parquet"
        rows = 1000
        pl.DataFrame(
            {
                "id": range(rows),
                "premium": [float(i) for i in range(rows)],
            }
        ).write_parquet(str(base_path))
        pl.DataFrame(
            {
                "jid": range(rows),
                "competitor_premium": [float(i) * 1.1 for i in range(rows)],
            }
        ).write_parquet(str(join_path))

        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_ready_file_input_config(join_path),
        )
        joined = _make_transform_node(
            node_id="joined",
            label="joined",
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "leftOn": ["id"],
                "rightOn": ["jid"],
            },
        )
        joined.data.nodeType = "edgeJoin"
        target = _make_modelling_node()
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined"),
                GraphEdge(id="e2", source="join", target="joined"),
                GraphEdge(id="e3", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.probe_columns == 3
        assert result.bytes_per_row == 3 * 8 * 3.0
        assert result.estimated_bytes == _estimate_peak_bytes(rows, 3)

    def test_edge_join_suffixed_string_column_uses_join_source_width(
        self,
        tmp_path,
    ) -> None:
        """Suffixed right-side columns should keep their parquet width estimate."""
        base_path = tmp_path / "base.parquet"
        join_path = tmp_path / "join.parquet"
        rows = 250
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "segment": [f"base-{i}-" + ("x" * 120) for i in range(rows)],
            }
        ).write_parquet(str(base_path))
        pl.DataFrame(
            {
                "quote_id": range(rows),
                "segment": [f"join-{i}-" + ("y" * 260) for i in range(rows)],
            }
        ).write_parquet(str(join_path))

        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_ready_file_input_config(join_path),
        )
        joined = _make_transform_node(
            node_id="joined",
            label="joined",
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "on": ["quote_id"],
            },
        )
        joined.data.nodeType = "edgeJoin"
        target = _make_modelling_node()
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined"),
                GraphEdge(id="e2", source="join", target="joined"),
                GraphEdge(id="e3", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        base_meta = read_parquet_metadata(base_path)
        join_meta = read_parquet_metadata(join_path)
        base_segment_width = max(
            8,
            math.ceil(base_meta["column_uncompressed_size_bytes"]["segment"] / rows),
        )
        join_segment_width = max(
            8,
            math.ceil(join_meta["column_uncompressed_size_bytes"]["segment"] / rows),
        )
        expected_base_bytes_per_row = 8 + base_segment_width + join_segment_width
        assert result.probe_columns == 3
        assert result.bytes_per_row == expected_base_bytes_per_row * 3.0
        assert result.estimated_bytes == _estimate_peak_bytes(
            rows,
            3,
            base_bytes_per_row=expected_base_bytes_per_row,
        )


# ---------------------------------------------------------------------------
# Detailed column resolution helpers
# ---------------------------------------------------------------------------


class TestDetailedColumnResolution:
    def test_selected_columns_without_parent_are_deduped(self) -> None:
        """Manual selected_columns still provide a stable output schema."""
        target = _make_modelling_node(
            config={"selected_columns": ["premium", "premium", "quote_id"]},
        )
        graph = PipelineGraph(nodes=[target], edges=[])

        resolved = _resolve_target_columns(graph, target.id, "live")

        assert resolved is not None
        assert resolved.columns == ("premium", "quote_id")
        assert resolved.width_columns == {
            "premium": "premium",
            "quote_id": "quote_id",
        }

    def test_selected_columns_filter_parent_metadata_width_keys(self, tmp_path) -> None:
        """A projection keeps source-scoped width keys for the selected columns."""
        path = tmp_path / "source.parquet"
        pl.DataFrame(
            {
                "quote_id": range(10),
                "premium": [float(i) for i in range(10)],
                "segment": [f"segment-{i}" for i in range(10)],
            }
        ).write_parquet(str(path))
        src = _make_source_node(
            node_id="source",
            node_type="dataInput",
            config=_ready_file_input_config(path),
        )
        projection = _make_transform_node(
            node_id="projection",
            config={"selected_columns": ["segment", "segment", "premium"]},
        )
        graph = PipelineGraph(
            nodes=[src, projection],
            edges=[GraphEdge(id="e1", source=src.id, target=projection.id)],
        )

        resolved = _resolve_target_columns(graph, projection.id, "live")

        assert resolved is not None
        assert resolved.columns == ("segment", "premium")
        assert resolved.width_columns == {
            "segment": "source\0segment",
            "premium": "source\0premium",
        }

    def test_selected_columns_with_unknown_parent_use_declared_names(self) -> None:
        """Declared selected_columns are still useful when parent schema is unknown."""
        parent = _make_transform_node(node_id="unknown_parent")
        projection = _make_transform_node(
            node_id="projection",
            config={"selected_columns": ["premium", "quote_id"]},
        )
        graph = PipelineGraph(
            nodes=[parent, projection],
            edges=[GraphEdge(id="e1", source=parent.id, target=projection.id)],
        )

        resolved = _resolve_target_columns(graph, projection.id, "live")

        assert resolved is not None
        assert resolved.columns == ("premium", "quote_id")
        assert resolved.width_columns == {
            "premium": "premium",
            "quote_id": "quote_id",
        }

    def test_invalid_selected_columns_are_rejected(self) -> None:
        """A selected_columns list with non-string entries is not a usable schema."""
        target = _make_modelling_node(config={"selected_columns": ["premium", 1]})
        graph = PipelineGraph(nodes=[target], edges=[])

        assert _resolve_target_columns(graph, target.id, "live") is None

    def test_target_column_names_wraps_detailed_resolution(self, tmp_path) -> None:
        path = tmp_path / "source.parquet"
        pl.DataFrame({"a": [1], "b": [2]}).write_parquet(str(path))
        src = _make_source_node(
            node_id="source",
            node_type="dataInput",
            config=_ready_file_input_config(path),
        )
        graph = PipelineGraph(nodes=[src], edges=[])

        assert _resolve_target_column_names(graph, src.id, "live") == ("a", "b")
        assert _resolve_target_column_names(graph, "missing", "live") is None

    def test_detail_resolution_skips_revisited_nodes(self) -> None:
        """Diamond-shaped parent graphs do not loop when no schema is available."""
        t1 = _make_transform_node(node_id="t1")
        t2 = _make_transform_node(node_id="t2")
        target = _make_modelling_node(node_id="target")
        graph = PipelineGraph(
            nodes=[t1, t2, target],
            edges=[
                GraphEdge(id="e1", source=t1.id, target=t2.id),
                GraphEdge(id="e2", source=t1.id, target=target.id),
                GraphEdge(id="e3", source=t2.id, target=target.id),
            ],
        )

        assert _resolve_target_columns(graph, target.id, "live") is None

    def test_source_without_detail_metadata_returns_none(self) -> None:
        src = _make_source_node(
            node_id="source",
            node_type="dataInput",
            config=_file_input_config("/missing/source.parquet"),
        )
        graph = PipelineGraph(nodes=[src], edges=[])

        assert _resolve_target_columns(graph, src.id, "live") is None


class TestDetailedEdgeJoinColumnResolution:
    def test_malformed_edge_join_roles_fall_back_to_selected_columns(self) -> None:
        """Selected columns remain usable when edgeJoin role metadata is incomplete."""
        joined = _make_edge_join_node(
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "on": ["quote_id"],
                "selected_columns": ["premium", "premium"],
            },
        )
        graph = PipelineGraph(nodes=[joined], edges=[])

        resolved = _resolve_target_columns(graph, joined.id, "live")

        assert resolved is not None
        assert resolved.columns == ("premium",)
        assert (
            _resolve_edge_join_column_names(
                joined,
                graph,
                "live",
                {"joined": ("base",)},
            )
            is None
        )

    def test_unsupported_edge_join_strategy_has_no_static_schema(self) -> None:
        """RAM estimation only synthesizes schemas for inner and left joins."""
        joined = _make_edge_join_node(
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "right",
                "on": ["quote_id"],
            },
        )
        graph = PipelineGraph(nodes=[joined], edges=[])

        assert (
            _resolve_edge_join_column_names(
                joined,
                graph,
                "live",
                {"joined": ("base", "join")},
            )
            is None
        )

    def test_edge_join_with_unresolved_parent_has_no_static_schema(self, tmp_path) -> None:
        """If either input schema is unknown, the join output schema is unknown."""
        base_path = tmp_path / "base.parquet"
        pl.DataFrame({"quote_id": [1, 2], "premium": [10.0, 20.0]}).write_parquet(str(base_path))
        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_file_input_config("/missing/join.parquet"),
        )
        joined = _make_edge_join_node(
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "on": ["quote_id"],
            },
        )
        graph = PipelineGraph(nodes=[base, join, joined], edges=[])

        assert (
            _resolve_edge_join_column_names(
                joined,
                graph,
                "live",
                {"joined": ("base", "join")},
            )
            is None
        )

    def test_dedupe_resolved_columns_keeps_first_width_key(self) -> None:
        """Duplicate output columns should not double count estimated RAM."""
        resolved = _dedupe_resolved_columns(
            [
                ("premium", "base\0premium"),
                ("premium", "join\0premium"),
                ("segment", "join\0segment"),
            ]
        )

        assert resolved.columns == ("premium", "segment")
        assert resolved.width_columns == {
            "premium": "base\0premium",
            "segment": "join\0segment",
        }


class TestEdgeJoinKeyColumnsOnPath:
    def test_malformed_edge_join_roles_preserve_both_key_names(self) -> None:
        """Without role metadata, excluded join keys are preserved conservatively."""
        base = _make_source_node(node_id="base", node_type="dataInput")
        joined = _make_edge_join_node(
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "leftOn": ["quote_id"],
                "rightOn": ["external_quote_id"],
            },
        )
        target = _make_modelling_node(node_id="target")
        graph = PipelineGraph(
            nodes=[base, joined, target],
            edges=[
                GraphEdge(id="e1", source=base.id, target=joined.id),
                GraphEdge(id="e2", source=joined.id, target=target.id),
            ],
        )

        assert _edge_join_key_columns_on_path(graph, target.id, "live") == frozenset(
            {"quote_id", "external_quote_id"}
        )

    def test_unresolved_join_input_preserves_joined_key_name(self, tmp_path) -> None:
        """Unknown join input schemas keep right-side keys in the peak projection."""
        base_path = tmp_path / "base.parquet"
        pl.DataFrame({"quote_id": [1, 2], "premium": [10.0, 20.0]}).write_parquet(str(base_path))
        base = _make_source_node(
            node_id="base",
            node_type="dataInput",
            config=_ready_file_input_config(base_path),
        )
        join = _make_source_node(
            node_id="join",
            node_type="dataInput",
            config=_file_input_config("/missing/join.parquet"),
        )
        joined = _make_edge_join_node(
            config={
                "baseInput": "base",
                "joinInput": "join",
                "how": "left",
                "leftOn": ["quote_id"],
                "rightOn": ["external_quote_id"],
            },
        )
        target = _make_modelling_node(node_id="target")
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source=base.id, target=joined.id),
                GraphEdge(id="e2", source=join.id, target=joined.id),
                GraphEdge(id="e3", source=joined.id, target=target.id),
            ],
        )

        assert _edge_join_key_columns_on_path(graph, target.id, "live") == frozenset(
            {"quote_id", "external_quote_id"}
        )


class TestEstimateBaseBytesPerRow:
    def test_no_target_columns_uses_numeric_width_by_count(self) -> None:
        assert (
            _estimate_base_bytes_per_row(
                3,
                target_columns=None,
                target_width_columns=None,
                sources=(),
            )
            == 24.0
        )

    def test_misaligned_width_columns_raise(self) -> None:
        with pytest.raises(ValueError, match="target_width_columns must align"):
            _estimate_base_bytes_per_row(
                2,
                target_columns=("a", "b"),
                target_width_columns=("a",),
                sources=(),
            )

    def test_string_width_uses_file_average_when_column_size_missing(self) -> None:
        meta = _DetailedSourceMetadata(
            row_count=10,
            column_count=2,
            columns={"description": "large_utf8", "premium": "int64"},
            column_width_keys={
                "description": "source\0description",
                "premium": "source\0premium",
            },
            column_uncompressed_size_bytes={},
            uncompressed_size_bytes=1000,
        )

        assert (
            _estimate_base_bytes_per_row(
                2,
                target_columns=("description", "premium"),
                target_width_columns=("source\0description", "source\0premium"),
                sources=(meta,),
            )
            == 58.0
        )

    def test_string_width_defaults_when_no_size_available(self) -> None:
        meta = _DetailedSourceMetadata(
            row_count=10,
            column_count=0,
            columns={"description": "utf8"},
            column_width_keys={"description": "source\0description"},
            column_uncompressed_size_bytes={},
            uncompressed_size_bytes=0,
        )

        assert (
            _estimate_base_bytes_per_row(
                1,
                target_columns=("description",),
                target_width_columns=("source\0description",),
                sources=(meta,),
            )
            == 8.0
        )


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
            node_type="dataInput",
            config=_databricks_input_config("db.schema.tbl"),
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        assert estimate_source_rows(graph) is None

    def test_returns_none_for_missing_file(self) -> None:
        node = _make_source_node(
            node_type="dataInput",
            config=_file_input_config("/no/such/file.parquet"),
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
            node_type="dataInput",
            config=_ready_file_input_config(p1),
        )
        n2 = _make_source_node(
            node_id="s2",
            node_type="dataInput",
            config=_ready_file_input_config(p2),
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

    def test_csv_data_input(self, tmp_path) -> None:
        path = tmp_path / "data.csv"
        pl.DataFrame({"a": range(42)}).write_csv(str(path))

        node = _make_source_node(
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
            node_type="dataInput",
            config=_databricks_input_config("cat.schema.tbl"),
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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
    def test_linux_v2_cgroup_clamps_host_available_memory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        cgroup = {
            "/sys/fs/cgroup/memory.max": "1000",
            "/sys/fs/cgroup/memory.current": "250",
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._ram_estimate._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == 750

    def test_linux_keeps_tighter_host_available_memory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        cgroup = {
            "/sys/fs/cgroup/memory.max": "10000",
            "/sys/fs/cgroup/memory.current": "100",
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._ram_estimate._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == 2 * 1024

    def test_linux_v2_max_does_not_clamp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        cgroup = {
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/memory.current": "250",
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._ram_estimate._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == 2 * 1024

    @pytest.mark.parametrize(
        ("limit", "current", "expected"),
        [("1000", "250", 750), (str(1 << 60), "250", 2 * 1024), ("100", "250", 0)],
    )
    def test_linux_v1_cgroup_fallback_and_limits(
        self, monkeypatch: pytest.MonkeyPatch, limit: str, current: str, expected: int
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        cgroup = {
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": limit,
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": current,
        }
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._ram_estimate._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == expected

    @pytest.mark.parametrize(
        "cgroup",
        [
            {"/sys/fs/cgroup/memory.max": "oops", "/sys/fs/cgroup/memory.current": "1"},
            {"/sys/fs/cgroup/memory.max": "1000"},
        ],
    )
    def test_linux_malformed_or_incomplete_cgroup_keeps_host_memory(
        self, monkeypatch: pytest.MonkeyPatch, cgroup: dict[str, str]
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._ram_estimate._read_cgroup_memory_file", side_effect=cgroup.get),
        ):
            assert available_ram_bytes() == 2 * 1024

    def test_non_linux_does_not_probe_cgroups(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "darwin")
        with (
            patch("builtins.open", return_value=__import__("io").StringIO("MemAvailable: 2 kB\n")),
            patch("haute._ram_estimate._read_cgroup_memory_file") as read_cgroup,
        ):
            assert available_ram_bytes() == 2 * 1024
        read_cgroup.assert_not_called()

    def test_unavailable_host_does_not_fabricate_cgroup_capacity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=AttributeError, create=True),
            patch("haute._ram_estimate._read_cgroup_memory_file") as read_cgroup,
        ):
            assert available_ram_bytes() is None
        read_cgroup.assert_not_called()

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

    def test_non_positive_sysconf_values_are_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid POSIX memory values are ignored instead of producing zero RAM."""
        monkeypatch.setattr("sys.platform", "linux")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=[0, 4096], create=True),
        ):
            result = available_ram_bytes()

        assert result is None

    def test_windows_global_memory_status_false_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed Windows memory probe is explicitly unavailable."""
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

        assert result is None
        status_probe.assert_called_once()

    def test_windows_ctypes_exception_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ctypes failures on the Windows path are logged as unavailable."""
        from types import SimpleNamespace

        fake_ctypes = SimpleNamespace(
            Structure=object,
            c_ulong=int,
            c_ulonglong=int,
            sizeof=MagicMock(side_effect=OSError("ctypes unavailable")),
            byref=lambda value: value,
            windll=SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=MagicMock())),
        )

        monkeypatch.setattr("sys.platform", "win32")
        with (
            patch("builtins.open", side_effect=OSError("proc unavailable")),
            patch("os.sysconf", side_effect=AttributeError("no sysconf"), create=True),
            patch.dict("sys.modules", {"ctypes": fake_ctypes}),
            capture_logs() as logs,
        ):
            result = available_ram_bytes()

        assert result is None
        unavailable_log = next(
            event for event in logs if event.get("event") == "available_ram_unavailable"
        )
        assert unavailable_log["windows_attempted"] is True
        assert "ctypes unavailable" in unavailable_log["windows_error"]

    @pytest.mark.skipif(sys.platform != "win32", reason="ctypes.windll only exists on Windows")
    def test_windows_ctypes_failure_is_unavailable(self, monkeypatch) -> None:
        """When GlobalMemoryStatusEx raises OSError, capacity is unavailable."""
        monkeypatch.setattr("sys.platform", "win32")
        with patch("builtins.open", side_effect=OSError):
            with patch("os.sysconf", side_effect=AttributeError, create=True):
                with patch(
                    "ctypes.windll.kernel32.GlobalMemoryStatusEx",
                    side_effect=OSError,
                    create=True,
                ):
                    result = available_ram_bytes()
        assert result is None


class _FakeLibSystem:
    """Stand-in for the darwin libSystem CDLL used by the mach probe."""

    def __init__(
        self,
        *,
        free_pages: int = 2000,
        inactive_pages: int = 1000,
        page_size: int = 16384,
        page_size_kern: int = 0,
        statistics_kern: int = 0,
    ) -> None:
        self._free_pages = free_pages
        self._inactive_pages = inactive_pages
        self._page_size = page_size
        self._page_size_kern = page_size_kern
        self._statistics_kern = statistics_kern
        self.host_statistics64_calls = 0
        self.mach_host_self = MagicMock(return_value=99)
        self.mach_task_self = MagicMock(return_value=1)
        self.mach_port_deallocate = MagicMock(return_value=0)

    def host_page_size(self, host: object, page_size_ref: object) -> int:
        page_size_ref._obj.value = self._page_size  # type: ignore[attr-defined]
        return self._page_size_kern

    def host_statistics64(
        self,
        host: object,
        flavor: object,
        stats_ref: object,
        count_ref: object,
    ) -> int:
        self.host_statistics64_calls += 1
        # Independently pin the real mach ABI so a drifted flavor or count
        # (which the kernel would reject or misinterpret) fails loudly here.
        assert flavor == 4  # HOST_VM_INFO64
        assert count_ref._obj.value == 38  # type: ignore[attr-defined]  # HOST_VM_INFO64_REV1_COUNT
        stats = stats_ref._obj  # type: ignore[attr-defined]
        stats.free_count = self._free_pages
        stats.inactive_count = self._inactive_pages
        return self._statistics_kern


class TestAvailableRamDarwinPath:
    def test_darwin_mach_free_plus_inactive_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On darwin the mach probe returns (free + inactive) × page size."""
        monkeypatch.setattr("sys.platform", "darwin")
        fake = _FakeLibSystem(free_pages=2000, inactive_pages=1000, page_size=16384)
        with (
            patch("builtins.open", side_effect=OSError),
            patch(
                "os.sysconf",
                side_effect=ValueError("unrecognized configuration name"),
                create=True,
            ),
            patch("ctypes.CDLL", return_value=fake),
        ):
            assert available_ram_bytes() == 3000 * 16384
        fake.mach_port_deallocate.assert_called_once()

    def test_darwin_mach_statistics_failure_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing host_statistics64 call is unavailable, with the port freed."""
        monkeypatch.setattr("sys.platform", "darwin")
        fake = _FakeLibSystem(statistics_kern=-308)
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=ValueError, create=True),
            patch("ctypes.CDLL", return_value=fake),
            capture_logs() as logs,
        ):
            assert available_ram_bytes() is None
        fake.mach_port_deallocate.assert_called_once()
        unavailable_log = next(
            event for event in logs if event.get("event") == "available_ram_unavailable"
        )
        assert unavailable_log["darwin_attempted"] is True
        assert "host_statistics64" in unavailable_log["darwin_error"]

    def test_darwin_mach_page_size_failure_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.platform", "darwin")
        fake = _FakeLibSystem(page_size_kern=-1)
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=ValueError, create=True),
            patch("ctypes.CDLL", return_value=fake),
            capture_logs() as logs,
        ):
            assert available_ram_bytes() is None
        fake.mach_port_deallocate.assert_called_once()
        unavailable_log = next(
            event for event in logs if event.get("event") == "available_ram_unavailable"
        )
        assert "host_page_size" in unavailable_log["darwin_error"]

    def test_darwin_non_positive_mach_values_are_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero pages from mach are ignored instead of producing zero RAM."""
        monkeypatch.setattr("sys.platform", "darwin")
        fake = _FakeLibSystem(free_pages=0, inactive_pages=0)
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=ValueError, create=True),
            patch("ctypes.CDLL", return_value=fake),
        ):
            assert available_ram_bytes() is None
        assert fake.host_statistics64_calls == 1

    def test_non_darwin_platform_does_not_probe_mach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "freebsd13")
        with (
            patch("builtins.open", side_effect=OSError),
            patch("os.sysconf", side_effect=AttributeError, create=True),
            patch("ctypes.CDLL") as cdll,
            capture_logs() as logs,
        ):
            assert available_ram_bytes() is None
        cdll.assert_not_called()
        unavailable_log = next(
            event for event in logs if event.get("event") == "available_ram_unavailable"
        )
        assert unavailable_log["darwin_attempted"] is False
        assert unavailable_log["darwin_error"] is None

    @pytest.mark.skipif(sys.platform != "darwin", reason="real mach probe needs macOS")
    def test_darwin_real_probe_is_available_not_total(self) -> None:
        """The unmocked mach probe observes availability, bounded by physical RAM."""
        import os

        ram = available_ram_bytes()
        assert isinstance(ram, int)
        assert ram > 100 * 1024 * 1024
        # Available must sit strictly below total physical memory; this is the
        # assertion that catches a mis-read struct, wrong flavor, or a slide
        # back to a total-capacity figure.
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        assert ram < total


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

    @pytest.mark.parametrize("suffix", [".jsonl", ".ndjson", ".NDJSON"])
    def test_api_input_ndjson_uncached_file_exists(self, tmp_path, suffix: str) -> None:
        """API_INPUT NDJSON aliases with no cache use a physical line count."""
        path = tmp_path / f"data{suffix}"
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

    def test_data_input_databricks_returns_none(self) -> None:
        """Data Input with a Databricks configuration returns None."""
        node = _make_source_node(
            node_type="dataInput",
            config=_databricks_input_config("db.tbl"),
        )
        result = _count_source_rows_for_node(node)
        assert result is None

    def test_data_input_csv(self, tmp_path) -> None:
        """Data Input with CSV file counts lines."""
        path = tmp_path / "data.csv"
        pl.DataFrame({"a": range(77)}).write_csv(str(path))
        node = _make_source_node(
            node_type="dataInput",
            config=_ready_file_input_config(path),
        )
        result = _count_source_rows_for_node(node)
        assert result == 77

    def test_data_input_ndjson(self, tmp_path) -> None:
        """Data Input with NDJSON counts physical record lines."""
        path = tmp_path / "data.ndjson"
        path.write_text('{"a":1}\n{"a":2}\n{"a":3}\n', encoding="utf-8")
        node = _make_source_node(
            node_type="dataInput",
            config=_ready_file_input_config(path),
        )

        assert _count_source_rows_for_node(node) == 3

    def test_data_input_without_snapshot_returns_none(self, tmp_path) -> None:
        """RAM estimation never falls back to reading an unbuilt provider source."""
        path = tmp_path / "notes.txt"
        path.write_text("not,a,supported,table\n", encoding="utf-8")
        node = _make_source_node(
            node_type="dataInput",
            config=_file_input_config(str(path)),
        )

        assert _count_source_rows_for_node(node) is None

    def test_unexpected_exception_propagates(self) -> None:
        """Programming failures are not misreported as unavailable metadata."""
        node = _make_source_node(
            node_type="apiInput",
            config={"path": "/some/file.parquet"},
        )
        with patch("haute._ram_estimate.Path") as mock_path:
            mock_path.return_value.exists.side_effect = RuntimeError("boom")
            # The path check `Path(path).exists()` will raise.
            with pytest.raises(RuntimeError, match="boom"):
                _count_source_rows_for_node(node)

    def test_unknown_node_type_returns_none(self) -> None:
        """A node type that's neither API_INPUT nor Data Input returns None."""
        node = _make_source_node(
            node_type="polars",
            config={"path": "/some/file.parquet"},
        )
        result = _count_source_rows_for_node(node)
        assert result is None


# ---------------------------------------------------------------------------
# _resolve_target_columns — BFS column resolution
# ---------------------------------------------------------------------------


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
            node_type="dataInput",
            config=_ready_file_input_config(path),
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
