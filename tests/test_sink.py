"""Tests for execute_sink — T10: dedicated sink node tests.

Covers:
  - Parquet sink (normal path)
  - CSV sink
  - Missing sink node ID
  - Missing output path configuration
  - Output directory auto-creation
  - Scenario coercion (live -> batch)
  - Custom scenario passthrough
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.schemas import SinkResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _source_node(nid: str, label: str | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.DATA_SOURCE),
    )


def _sink_node(
    nid: str,
    path: str = "",
    fmt: str = "parquet",
    label: str | None = None,
) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=label or nid,
            nodeType=NodeType.DATA_SINK,
            config={"path": path, "format": fmt},
        ),
    )


def _transform_node(nid: str, label: str | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.POLARS),
    )


def _make_graph(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> PipelineGraph:
    return PipelineGraph(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExecuteSinkErrors:
    """Edge cases that raise before writing."""

    def test_unknown_sink_node_raises(self):
        """Raises ValueError when sink_node_id is not in the graph."""
        from haute.executor import execute_sink

        graph = _make_graph(
            nodes=[_source_node("s")],
            edges=[],
        )
        with pytest.raises(ValueError, match="not found"):
            execute_sink(graph, "nonexistent_sink")

    def test_missing_path_raises(self):
        """Raises ValueError when sink node has no path configured."""
        from haute.executor import execute_sink

        graph = _make_graph(
            nodes=[_source_node("s"), _sink_node("sink", path="")],
            edges=[_e("s", "sink")],
        )
        with pytest.raises(ValueError, match="no output path"):
            execute_sink(graph, "sink")


class TestExecuteSinkParquet:
    """Normal parquet sink path."""

    def test_writes_parquet_file(self, tmp_path):
        """Sink writes a parquet file with correct row count."""
        from haute.executor import execute_sink

        out_path = str(tmp_path / "output.parquet")
        graph = _make_graph(
            nodes=[
                _source_node("s"),
                _sink_node("sink", path=out_path, fmt="parquet"),
            ],
            edges=[_e("s", "sink")],
        )

        # Mock _build_node_fn and _execute_lazy to return a simple LazyFrame
        lf = pl.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]}).lazy()
        mock_outputs = {"sink": lf}

        with patch(
            "haute.executor._execute_lazy",
            return_value=(mock_outputs, ["s", "sink"], {}, {}),
        ):
            result = execute_sink(graph, "sink")

        assert isinstance(result, SinkResponse)
        assert result.status == "ok"
        assert result.row_count == 3
        assert result.path == out_path
        assert result.format == "parquet"
        assert Path(out_path).exists()

        # Verify data integrity
        df = pl.read_parquet(out_path)
        assert df.shape == (3, 2)
        assert df["x"].to_list() == [1, 2, 3]

    def test_default_format_is_parquet(self):
        """Sink without explicit format defaults to parquet."""
        node = _sink_node("s", path="/tmp/out.parquet")
        assert node.data.config.get("format", "parquet") == "parquet"


class TestExecuteSinkCSV:
    """CSV sink path."""

    def test_writes_csv_file(self, tmp_path):
        """Sink writes a CSV file with correct row count."""
        from haute.executor import execute_sink

        out_path = str(tmp_path / "output.csv")
        graph = _make_graph(
            nodes=[
                _source_node("s"),
                _sink_node("sink", path=out_path, fmt="csv"),
            ],
            edges=[_e("s", "sink")],
        )

        lf = pl.DataFrame({"a": [10, 20], "b": ["x", "y"]}).lazy()
        mock_outputs = {"sink": lf}

        with patch(
            "haute.executor._execute_lazy",
            return_value=(mock_outputs, ["s", "sink"], {}, {}),
        ):
            result = execute_sink(graph, "sink")

        assert result.status == "ok"
        assert result.row_count == 2
        assert result.format == "csv"
        assert Path(out_path).exists()

        # Verify CSV content
        df = pl.read_csv(out_path)
        assert df["a"].to_list() == [10, 20]
        assert df["b"].to_list() == ["x", "y"]


class TestExecuteSinkDirectoryCreation:
    """Output directory auto-creation."""

    def test_creates_nested_output_directory(self, tmp_path):
        """Sink creates parent directories if they don't exist."""
        from haute.executor import execute_sink

        out_path = str(tmp_path / "deep" / "nested" / "dir" / "output.parquet")
        graph = _make_graph(
            nodes=[
                _source_node("s"),
                _sink_node("sink", path=out_path),
            ],
            edges=[_e("s", "sink")],
        )

        lf = pl.DataFrame({"x": [1]}).lazy()
        with patch(
            "haute.executor._execute_lazy",
            return_value=({"sink": lf}, ["s", "sink"], {}, {}),
        ):
            result = execute_sink(graph, "sink")

        assert result.status == "ok"
        assert Path(out_path).exists()


class TestExecuteSinkScenario:
    """Scenario coercion for sinks."""

    def test_live_scenario_coerced_to_batch(self, tmp_path):
        """When scenario='live', sink coerces to 'batch' (or ISM non-live value)."""
        from haute.executor import execute_sink

        out_path = str(tmp_path / "out.parquet")
        graph = _make_graph(
            nodes=[
                _source_node("s"),
                _sink_node("sink", path=out_path),
            ],
            edges=[_e("s", "sink")],
        )

        captured_kwargs = {}
        original_execute_lazy = None

        def mock_execute_lazy(graph, build_fn, **kwargs):
            captured_kwargs.update(kwargs)
            lf = pl.DataFrame({"x": [1]}).lazy()
            return {"sink": lf}, ["s", "sink"], {}, {}

        with patch("haute.executor._execute_lazy", side_effect=mock_execute_lazy):
            execute_sink(graph, "sink", source="live")

        # Should NOT be "live" — should be coerced to "batch"
        assert captured_kwargs["source"] != "live"

    def test_custom_scenario_passed_through(self, tmp_path):
        """Non-'live' scenario is passed through unchanged."""
        from haute.executor import execute_sink

        out_path = str(tmp_path / "out.parquet")
        graph = _make_graph(
            nodes=[
                _source_node("s"),
                _sink_node("sink", path=out_path),
            ],
            edges=[_e("s", "sink")],
        )

        captured_kwargs = {}

        def mock_execute_lazy(graph, build_fn, **kwargs):
            captured_kwargs.update(kwargs)
            lf = pl.DataFrame({"x": [1]}).lazy()
            return {"sink": lf}, ["s", "sink"], {}, {}

        with patch("haute.executor._execute_lazy", side_effect=mock_execute_lazy):
            execute_sink(graph, "sink", source="test_batch")

        assert captured_kwargs["source"] == "test_batch"


class TestExecuteSinkComputeFailure:
    """Sink compute failure when lazy outputs are empty."""

    def test_none_lazy_output_raises(self, tmp_path):
        """Raises RuntimeError when lazy execution produces no output for sink."""
        from haute.executor import execute_sink

        out_path = str(tmp_path / "out.parquet")
        graph = _make_graph(
            nodes=[
                _source_node("s"),
                _sink_node("sink", path=out_path),
            ],
            edges=[_e("s", "sink")],
        )

        # Return empty outputs (missing the sink node)
        with patch(
            "haute.executor._execute_lazy",
            return_value=({}, ["s", "sink"], {}, {}),
        ):
            with pytest.raises(RuntimeError, match="Failed to compute sink input"):
                execute_sink(graph, "sink")


class TestExecuteSinkResponse:
    """Response message formatting."""

    def test_response_message_format(self, tmp_path):
        """Response message includes row count and path."""
        from haute.executor import execute_sink

        out_path = str(tmp_path / "result.parquet")
        graph = _make_graph(
            nodes=[
                _source_node("s"),
                _sink_node("sink", path=out_path),
            ],
            edges=[_e("s", "sink")],
        )

        lf = pl.DataFrame({"x": list(range(1000))}).lazy()
        with patch(
            "haute.executor._execute_lazy",
            return_value=({"sink": lf}, ["s", "sink"], {}, {}),
        ):
            result = execute_sink(graph, "sink")

        assert "1,000" in result.message
        assert out_path in result.message
