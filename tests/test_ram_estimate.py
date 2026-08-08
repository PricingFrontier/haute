"""Tests for haute._ram_estimate — RAM estimation and safe downsampling."""

from __future__ import annotations

import math
from unittest.mock import patch

import polars as pl
import pytest
from structlog.testing import capture_logs

from haute._polars_io_registry import data_input_is_direct
from haute._polars_utils import read_parquet_metadata
from haute._ram_estimate import (
    MaterialisationEstimate,
    MaterialisationEstimateState,
    RamEstimate,
    _dedupe_resolved_columns,
    _detailed_ancestor_source_metadata,
    _detailed_source_metadata_for_node,
    _DetailedSourceMetadata,
    _edge_join_key_columns_on_path,
    _estimate_base_bytes_per_row,
    _estimate_peak_bytes,
    _EstimateGraphIndex,
    _parquet_metadata,
    _resolve_edge_join_column_names,
    _resolve_target_column_names,
    _resolve_target_columns,
    _source_column_base_widths,
    estimate_gpu_vram_bytes,
    estimate_materialisation_boundary,
    estimate_safe_training_rows,
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
    # Resolution is memoized per (node, arrival port): two consumers of
    # different tables of one multi-frame source resolve different columns.
    index.resolving_targets.add((source.id, None))

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


def _json_api_input_graph() -> PipelineGraph:
    """A JSON API input emitting two tables, each feeding a different node."""

    source = _make_source_node(node_id="quote_in", config={"path": "data/quotes.jsonl"})
    consumer = _make_transform_node(node_id="claims")
    other = _make_transform_node(node_id="drivers")
    return PipelineGraph(
        nodes=[source, consumer, other],
        edges=[
            GraphEdge(
                id="e1",
                source="quote_in",
                target="claims",
                sourceHandle="proposer_claims",
            ),
            GraphEdge(
                id="e2",
                source="quote_in",
                target="drivers",
                sourceHandle="additional_drivers",
            ),
        ],
    )


def test_json_api_input_boundary_is_sized_from_the_tables_that_feed_the_target() -> None:
    """A v2 JSON cache has no whole-node summary, but each emitted table does.

    Before this, the node contributed nothing and every boundary under an API
    input was unestimatable — which refused every group-by beneath one.
    """

    graph = _json_api_input_graph()
    per_port = {
        "proposer_claims": _DetailedSourceMetadata(
            row_count=519481,
            column_count=8,
            columns={f"c{index}": "int64" for index in range(8)},
            column_width_keys={f"c{index}": f"c{index}" for index in range(8)},
            column_uncompressed_size_bytes={},
            uncompressed_size_bytes=17417163,
        ),
        "additional_drivers": _DetailedSourceMetadata(
            row_count=999999,
            column_count=3,
            columns={"d0": "int64"},
            column_width_keys={"d0": "d0"},
            column_uncompressed_size_bytes={},
            uncompressed_size_bytes=1,
        ),
    }

    with patch(
        "haute._ram_estimate._json_api_input_port_metadata",
        side_effect=lambda _node, port: per_port.get(port),
    ) as resolver:
        metadata = _detailed_ancestor_source_metadata(graph, "claims")

    # Only the port actually feeding `claims` is consulted or counted; the
    # sibling branch's larger table must not inflate this boundary.
    assert [call.args[1] for call in resolver.call_args_list] == ["proposer_claims"]
    assert metadata.row_count == 519481
    assert metadata.column_count == 8


def test_json_api_input_target_columns_resolve_through_the_arrival_port() -> None:
    graph = _json_api_input_graph()
    metadata = _DetailedSourceMetadata(
        row_count=10,
        column_count=2,
        columns={"quote_id": "string", "amount_paid": "double"},
        column_width_keys={"quote_id": "quote_id", "amount_paid": "amount_paid"},
        column_uncompressed_size_bytes={},
        uncompressed_size_bytes=64,
    )

    with patch(
        "haute._ram_estimate._json_api_input_port_metadata",
        side_effect=lambda _node, port: metadata if port == "proposer_claims" else None,
    ):
        resolved = _resolve_target_columns(graph, "claims", "live")

    assert resolved is not None
    assert set(resolved.columns) == {"quote_id", "amount_paid"}


def test_json_api_input_ports_resolve_when_their_consumer_is_an_edge_join() -> None:
    base = _make_source_node(node_id="base_api", config={"path": "data/base.jsonl"})
    join = _make_source_node(node_id="join_api", config={"path": "data/join.jsonl"})
    joined = _make_edge_join_node(
        config={
            "baseInput": "base_api",
            "joinInput": "join_api",
            "how": "left",
            "on": ["quote_id"],
            "coalesce": False,
        }
    )
    graph = PipelineGraph(
        nodes=[base, join, joined],
        edges=[
            GraphEdge(
                id="e1",
                source=base.id,
                target=joined.id,
                sourceHandle="policies",
            ),
            GraphEdge(
                id="e2",
                source=join.id,
                target=joined.id,
                sourceHandle="claims",
            ),
        ],
    )
    per_port = {
        (base.id, "policies"): _DetailedSourceMetadata(
            row_count=10,
            column_count=2,
            columns={"quote_id": "string", "premium": "double"},
            column_width_keys={"quote_id": "quote_id", "premium": "premium"},
            column_uncompressed_size_bytes={},
            uncompressed_size_bytes=64,
        ),
        (join.id, "claims"): _DetailedSourceMetadata(
            row_count=10,
            column_count=2,
            columns={"quote_id": "string", "claim_count": "int64"},
            column_width_keys={"quote_id": "quote_id", "claim_count": "claim_count"},
            column_uncompressed_size_bytes={},
            uncompressed_size_bytes=64,
        ),
    }

    with patch(
        "haute._ram_estimate._json_api_input_port_metadata",
        side_effect=lambda node, port: per_port.get((node.id, port)),
    ):
        resolved = _resolve_target_columns(graph, joined.id, "live")
        join_keys = _edge_join_key_columns_on_path(graph, joined.id, "live")

    assert resolved is not None
    assert resolved.columns == ("quote_id", "premium", "quote_id_right", "claim_count")
    assert join_keys == frozenset({"quote_id", "quote_id_right"})


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
        graph = PipelineGraph(
            nodes=[base, join, joined],
            edges=[
                GraphEdge(id="e1", source=base.id, target=joined.id),
                GraphEdge(id="e2", source=join.id, target=joined.id),
            ],
        )

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

        with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: testing cleanup behavior, not exception type
            _parquet_metadata(str(tmp_path / "does_not_exist.parquet"))


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


# ---------------------------------------------------------------------------
# JSON apiInput per-port cache metadata
# ---------------------------------------------------------------------------


def _json_port_config(data_path) -> dict:
    """A two-emit-table shred config: one parquet per emitted table."""

    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {"name": "policy_id", "path": "$[:].policy_id", "type": "int", "selected": True}
                ],
            },
            {
                "path": "$[:].drivers[:]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[:].drivers[:].driver_id",
                        "type": "int",
                        "selected": True,
                    }
                ],
            },
        ],
    }


@pytest.fixture()
def json_api_input(tmp_path, monkeypatch):
    """A JSON apiInput with a real built v2 working-layer cache.

    Built through the same reader/writer the engine uses rather than a stub:
    the point of resolving per port is that a stale or absent cache is
    rejected here exactly as it is at execution.
    """

    import json as _json

    from haute._json_flatten import _json_cache_dir
    from haute._json_shred import build_per_port_cache
    from haute._sandbox import _get_project_root, set_project_root

    monkeypatch.chdir(tmp_path)
    original_root = _get_project_root()
    set_project_root(tmp_path)

    data_path = tmp_path / "quotes.json"
    data_path.write_text(
        _json.dumps(
            [
                {"policy_id": 1, "drivers": [{"driver_id": 1}, {"driver_id": 2}]},
                {"policy_id": 2, "drivers": [{"driver_id": 3}]},
            ]
        ),
        encoding="utf-8",
    )
    config = _json_port_config(data_path)
    cache_dir = _json_cache_dir(data_path, "working")
    committed_dir = _json_cache_dir(data_path, "committed")
    build_per_port_cache(data_path, config, cache_dir)
    try:
        yield data_path, config, cache_dir, committed_dir
    finally:
        set_project_root(original_root)


class TestJsonApiInputPortMetadata:
    """A v2 cache has no whole-node summary; each emitted table has its own."""

    def test_each_emitted_table_is_sized_from_its_own_parquet(self, json_api_input) -> None:
        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, _cache_dir, _committed_dir = json_api_input
        node = _make_source_node(node_type="apiInput", config=config)

        policies = _json_api_input_port_metadata(node, "policies")
        drivers = _json_api_input_port_metadata(node, "drivers")

        assert policies is not None and policies.row_count == 2
        assert drivers is not None and drivers.row_count == 3
        # Sizing a boundary from the wrong table is the failure this prevents.
        assert policies.row_count != drivers.row_count

    def test_committed_layer_is_used_when_working_holds_no_match(self, json_api_input) -> None:
        """Layer preference is the reader's, not this module's."""

        import shutil

        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, working_dir, committed_dir = json_api_input
        committed_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(working_dir, committed_dir, dirs_exist_ok=True)
        shutil.rmtree(working_dir)

        node = _make_source_node(node_type="apiInput", config=config)

        assert _json_api_input_port_metadata(node, "policies").row_count == 2

    def test_a_port_the_cache_never_emitted_is_unavailable(self, json_api_input) -> None:
        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, _cache_dir, _committed_dir = json_api_input
        node = _make_source_node(node_type="apiInput", config=config)

        assert _json_api_input_port_metadata(node, "vehicles") is None

    def test_a_stale_cache_is_rejected_rather_than_sized_from(self, json_api_input) -> None:
        """The signature check is the engine's; a boundary must never be
        estimated from a cache the run itself would rebuild."""

        from haute._ram_estimate import _json_api_input_port_metadata

        data_path, config, _cache_dir, _committed_dir = json_api_input
        data_path.write_text('[{"policy_id": 9, "drivers": []}]', encoding="utf-8")
        node = _make_source_node(node_type="apiInput", config=config)

        assert _json_api_input_port_metadata(node, "policies") is None

    @pytest.mark.parametrize("path_value", ["", None, 17])
    def test_a_node_without_a_usable_path_is_unavailable(self, path_value) -> None:
        from haute._ram_estimate import _json_api_input_port_metadata

        node = _make_source_node(node_type="apiInput", config={"path": path_value})

        assert _json_api_input_port_metadata(node, "policies") is None

    def test_a_missing_data_file_is_unavailable(self, tmp_path) -> None:
        from haute._ram_estimate import _json_api_input_port_metadata

        node = _make_source_node(
            node_type="apiInput", config={"path": str(tmp_path / "absent.json")}
        )

        assert _json_api_input_port_metadata(node, "policies") is None

    def test_an_unreadable_cache_warns_and_reports_unavailable(self, json_api_input) -> None:
        """Estimation degrades to "unknown" rather than raising into a caller
        that would treat the failure as "unlimited"."""

        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, _cache_dir, _committed_dir = json_api_input
        node = _make_source_node(node_type="apiInput", config=config)

        with patch(
            "haute._json_shred._data_file_signature",
            side_effect=OSError("cache device is gone"),
        ):
            with capture_logs() as logs:
                assert _json_api_input_port_metadata(node, "policies") is None

        assert any(entry["event"] == "api_input_port_metadata_failed" for entry in logs)

    def test_ancestor_sizing_uses_only_the_tables_feeding_the_target(self, json_api_input) -> None:
        """Sibling branches of one apiInput must not inflate a boundary they
        do not feed — the whole reason the walk carries the arrival handle."""

        _data_path, config, _cache_dir, _committed_dir = json_api_input
        source = _make_source_node(node_id="quote_in", node_type="apiInput", config=config)
        consumer = _make_transform_node(node_id="claims")
        sibling = _make_transform_node(node_id="drivers_only")
        graph = PipelineGraph(
            nodes=[source, consumer, sibling],
            edges=[
                GraphEdge(id="e1", source="quote_in", target="claims", sourceHandle="policies"),
                GraphEdge(
                    id="e2", source="quote_in", target="drivers_only", sourceHandle="drivers"
                ),
            ],
        )

        metadata = _detailed_ancestor_source_metadata(graph, "claims")

        assert metadata.row_count == 2
        assert [item.row_count for item in metadata.sources] == [2]


class TestUnavailableEstimateReasons:
    """The two reasons a boundary cannot be sized.

    Each is now carried into the group-by rejection's remediation, so an
    analyst can tell an unreadable file from an unsummarisable source shape.
    Their identity is part of that contract.
    """

    def test_an_unsizable_ancestor_reports_source_row_count_unavailable(self, tmp_path) -> None:
        source = _make_source_node(node_type="apiInput", config={"path": ""})
        target = _make_transform_node()
        graph = PipelineGraph(
            nodes=[source, target],
            edges=[GraphEdge(id="e1", source=source.id, target=target.id)],
        )

        estimate = estimate_materialisation_boundary(graph, target.id)

        assert estimate.state is MaterialisationEstimateState.UNAVAILABLE
        assert estimate.unavailable_reason == "source_row_count_unavailable"

    def test_an_unresolvable_target_reports_target_schema_unavailable(self, tmp_path) -> None:
        path = tmp_path / "quotes.parquet"
        pl.DataFrame({"a": range(4)}).write_parquet(str(path))
        source = _make_source_node(node_type="apiInput", config={"path": str(path)})
        target = _make_transform_node()
        graph = PipelineGraph(
            nodes=[source, target],
            edges=[GraphEdge(id="e1", source=source.id, target=target.id)],
        )

        with patch("haute._ram_estimate._resolve_target_columns", return_value=None):
            estimate = estimate_materialisation_boundary(graph, target.id)

        assert estimate.state is MaterialisationEstimateState.UNAVAILABLE
        assert estimate.unavailable_reason == "target_schema_unavailable"


def test_variable_width_probe_is_empty_when_it_reads_no_rows(tmp_path) -> None:
    """Parquet metadata can claim rows the scan does not return. The probe
    measures the representation materialisation actually allocates, so with
    no sampled row there is nothing to measure and no width to assert."""

    from haute._ram_estimate import _probe_expanded_variable_widths

    path = tmp_path / "empty.parquet"
    pl.DataFrame(schema={"category": pl.String}).write_parquet(str(path))

    widths = _probe_expanded_variable_widths(str(path), {"category": "string"}, row_count=8)

    assert dict(widths) == {}


def test_training_estimate_refuses_to_guess_when_physical_ram_is_unknown() -> None:
    """ "Unknown" must not silently become "unlimited" on the training path."""

    graph = PipelineGraph(nodes=[_make_source_node()], edges=[])

    with patch("haute._ram_estimate.available_ram_bytes", return_value=None):
        with pytest.raises(RuntimeError, match="physical RAM is unavailable"):
            estimate_safe_training_rows(graph, "src1", _build_dummy_node_fn)


def test_training_estimate_refuses_zero_headroom_instead_of_flooring() -> None:
    """A zero observed budget refuses; it never floors up to minimum safe rows."""

    graph = PipelineGraph(nodes=[_make_source_node()], edges=[])

    with patch("haute._ram_estimate.available_ram_bytes", return_value=0):
        with pytest.raises(RuntimeError, match="available memory is exhausted"):
            estimate_safe_training_rows(graph, "src1", _build_dummy_node_fn)
