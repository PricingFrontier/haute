"""Tests for haute._ram_estimate — RAM estimation and safe downsampling."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from types import SimpleNamespace
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
    _bounded_cardinality_evidence,
    _cardinality_name_bindings,
    _data_input_parquet_artifact,
    _dedupe_resolved_columns,
    _detailed_ancestor_source_metadata,
    _detailed_source_metadata_for_node,
    _DetailedSourceMetadata,
    _edge_join_key_columns_on_path,
    _estimate_base_bytes_per_row,
    _estimate_peak_bytes,
    _EstimateGraphIndex,
    _named_cardinality_inputs,
    _parquet_metadata,
    _passthrough_cardinality,
    _port_operand_counts,
    _resolve_edge_join_column_names,
    _resolve_row_cardinality_from_index,
    _resolve_target_column_names,
    _resolve_target_columns,
    _ResolvedRowCardinality,
    _safe_edge_input_name,
    _source_column_base_widths,
    estimate_gpu_vram_bytes,
    estimate_materialisation_boundaries,
    estimate_safe_training_rows,
)
from haute._types import NodeType
from haute.errors import ConfigError
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph
from tests.conftest import build_test_input_snapshot


def _boundary_estimate(graph: PipelineGraph, target_node_id: str) -> MaterialisationEstimate:
    [(_, estimate)] = list(estimate_materialisation_boundaries(graph, [target_node_id]))
    return estimate


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
    with pytest.raises(TypeError, match="basis must be"):
        MaterialisationEstimate(
            MaterialisationEstimateState.AVAILABLE,
            1,
            basis="provided",  # type: ignore[arg-type]
        )


def test_ram_estimate_column_index_rejects_recursive_resolution() -> None:
    source = _make_source_node()
    index = _EstimateGraphIndex.build(PipelineGraph(nodes=[source], edges=[]), "live")
    # Resolution is memoized per (node, arrival port): two consumers of
    # different tables of one multi-frame source resolve different columns.
    index.resolving_targets.add((source.id, None))

    with pytest.raises(RuntimeError, match="cycle encountered"):
        index.resolve_columns(source.id)


@pytest.mark.parametrize("duplicate_count", [0, 2])
def test_ram_estimate_column_index_requires_exactly_one_parent_edge(
    duplicate_count: int,
) -> None:
    source = _make_source_node(node_id="source")
    target = _make_transform_node(node_id="target")
    edges = [
        GraphEdge(id=f"edge-{index}", source="source", target="target")
        for index in range(duplicate_count)
    ]
    index = _EstimateGraphIndex.build(PipelineGraph(nodes=[source, target], edges=edges), "live")

    with pytest.raises(RuntimeError, match=f"found {duplicate_count}"):
        index.parent_port("target", "source")


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


def test_persistent_data_input_uses_verified_generation_row_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_path = tmp_path / "generation.parquet"
    generation = SimpleNamespace(
        metadata=SimpleNamespace(row_count=17),
        data_path=generation_path,
    )
    opened: list[object] = []

    monkeypatch.setattr("haute._builders._configured_pipeline_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "haute._polars_io_registry.validate_data_input_config",
        lambda _config: {"validated": True},
    )
    monkeypatch.setattr(
        "haute._polars_io_registry.data_input_is_direct",
        lambda _config: False,
    )
    monkeypatch.setattr(
        "haute._input_providers.source_cache_identity",
        lambda _config, *, base_dir: ("identity", base_dir),
    )
    monkeypatch.setattr("haute._sandbox._get_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        "haute._source_cache.SourceCacheStore",
        lambda root: SimpleNamespace(
            open_generation=lambda identity: opened.append((root, identity)) or generation
        ),
    )

    assert _data_input_parquet_artifact({"source": "persistent"}) == (17, generation_path)
    assert opened == [(tmp_path, ("identity", tmp_path))]


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

    estimate = _boundary_estimate(graph, source.id)

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
                targetHandle="base",
            ),
            GraphEdge(
                id="e2",
                source=join.id,
                target=joined.id,
                sourceHandle="claims",
                targetHandle="join",
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
        estimate = _boundary_estimate(graph, target.id)

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
                "how": "left",
                "on": ["quote_id"],
                "validate": "1:1",
                "selected_columns": ["quote_id", "premium", "claim_count"],
            },
        )
        joined.data.nodeType = NodeType.EDGE_JOIN
        target = _make_modelling_node(config={"exclude": ["quote_id"]})
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined", targetHandle="base"),
                GraphEdge(id="e2", source="join", target="joined", targetHandle="join"),
                GraphEdge(id="e3", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.probe_columns == 3
        assert result.bytes_per_row == 3 * 8 * 3.0
        assert result.estimated_bytes == _estimate_peak_bytes(rows, 3)

    def test_many_to_many_join_uses_target_cardinality_for_training_rows(
        self,
        tmp_path,
    ) -> None:
        left_path = tmp_path / "left.parquet"
        right_path = tmp_path / "right.parquet"
        pl.DataFrame({"id": [1, 1, 1, 1], "left_value": range(4)}).write_parquet(left_path)
        pl.DataFrame({"id": [1, 1, 1], "right_value": range(3)}).write_parquet(right_path)
        left = _make_source_node(
            node_id="left",
            node_type="dataInput",
            config=_ready_file_input_config(left_path),
        )
        right = _make_source_node(
            node_id="right",
            node_type="dataInput",
            config=_ready_file_input_config(right_path),
        )
        joined = _make_transform_node(
            node_id="joined",
            config={
                "how": "left",
                "on": ["id"],
                "validate": "m:m",
            },
        )
        joined.data.nodeType = NodeType.EDGE_JOIN
        target = _make_modelling_node()
        graph = PipelineGraph(
            nodes=[left, right, joined, target],
            edges=[
                GraphEdge(id="left-join", source="left", target="joined", targetHandle="base"),
                GraphEdge(id="right-join", source="right", target="joined", targetHandle="join"),
                GraphEdge(id="join-model", source="joined", target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.total_rows == 12
        assert result.probe_columns == 3
        assert result.estimated_bytes == _estimate_peak_bytes(12, 3)

    def test_unproven_target_cardinality_returns_unavailable_training_estimate(
        self,
        tmp_path,
    ) -> None:
        path = tmp_path / "lists.parquet"
        pl.DataFrame({"items": [[1, 2], [3]], "value": [10, 20]}).write_parquet(path)
        source = _make_source_node(
            node_type="dataInput",
            config=_ready_file_input_config(path),
        )
        transform = _make_transform_node(config={"code": "df = df.explode('items')"})
        target = _make_modelling_node()
        graph = PipelineGraph(
            nodes=[source, transform, target],
            edges=[
                GraphEdge(id="source-transform", source=source.id, target=transform.id),
                GraphEdge(id="transform-model", source=transform.id, target=target.id),
            ],
        )

        result = estimate_safe_training_rows(graph, target.id, _build_dummy_node_fn)

        assert result.total_rows is None
        assert result.safe_row_limit is None
        assert result.estimated_bytes == 0
        assert result.probe_columns == 0

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
                "how": "left",
                "on": ["quote_id"],
                "validate": "1:1",
            },
        )
        joined.data.nodeType = NodeType.EDGE_JOIN
        target = _make_modelling_node(config={"exclude": ["quote_id"]})
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined", targetHandle="base"),
                GraphEdge(id="e2", source="join", target="joined", targetHandle="join"),
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
                "how": "left",
                "on": ["quote_id"],
                "validate": "1:1",
                "coalesce": False,
            },
        )
        joined.data.nodeType = NodeType.EDGE_JOIN
        target = _make_modelling_node(config={"exclude": ["quote_id"]})
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined", targetHandle="base"),
                GraphEdge(id="e2", source="join", target="joined", targetHandle="join"),
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
                "how": "left",
                "on": ["quote_id"],
                "validate": "1:1",
                "coalesce": False,
            },
        )
        joined.data.nodeType = NodeType.EDGE_JOIN
        target = _make_modelling_node(config={"exclude": ["quote_id", "quote_id_right"]})
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined", targetHandle="base"),
                GraphEdge(id="e2", source="join", target="joined", targetHandle="join"),
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
                "how": "left",
                "leftOn": ["id"],
                "rightOn": ["jid"],
                "validate": "1:1",
                "coalesce": False,
            },
        )
        joined.data.nodeType = NodeType.EDGE_JOIN
        target = _make_modelling_node()
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined", targetHandle="base"),
                GraphEdge(id="e2", source="join", target="joined", targetHandle="join"),
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
                "how": "left",
                "leftOn": ["id"],
                "rightOn": ["jid"],
                "validate": "1:1",
            },
        )
        joined.data.nodeType = NodeType.EDGE_JOIN
        target = _make_modelling_node()
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined", targetHandle="base"),
                GraphEdge(id="e2", source="join", target="joined", targetHandle="join"),
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
                "how": "left",
                "on": ["quote_id"],
                "validate": "1:1",
            },
        )
        joined.data.nodeType = NodeType.EDGE_JOIN
        target = _make_modelling_node()
        graph = PipelineGraph(
            nodes=[base, join, joined, target],
            edges=[
                GraphEdge(id="e1", source="base", target="joined", targetHandle="base"),
                GraphEdge(id="e2", source="join", target="joined", targetHandle="join"),
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
            )
            is None
        )

    def test_unsupported_edge_join_strategy_has_no_static_schema(self) -> None:
        """RAM estimation only synthesizes schemas for inner and left joins."""
        joined = _make_edge_join_node(
            config={
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

    def test_fractional_width_rounds_up_for_conservative_admission(self) -> None:
        assert _estimate_peak_bytes(1, 1, base_bytes_per_row=0.5) == 2

    def test_extreme_finite_cardinality_does_not_overflow_float(self) -> None:
        rows = 10**400
        assert _estimate_peak_bytes(rows, 1, base_bytes_per_row=0.5) == (rows * 3) // 2

    @pytest.mark.parametrize("width", [-0.5, math.inf, math.nan])
    def test_invalid_measured_width_fails_loudly(self, width: float) -> None:
        with pytest.raises(ValueError, match="base_bytes_per_row"):
            _estimate_peak_bytes(1, 1, base_bytes_per_row=width)

    @pytest.mark.parametrize("rows,columns", [(True, 1), (1, True), (-1, 1), (1, -1)])
    def test_invalid_dimensions_fail_loudly(self, rows: object, columns: object) -> None:
        with pytest.raises(ValueError, match="non-negative integer"):
            _estimate_peak_bytes(rows, columns)  # type: ignore[arg-type]


def test_cardinality_proof_validation_and_evidence_cap_are_explicit() -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        _ResolvedRowCardinality.proven(3, 2, ("proof",))

    items = [f"proof-{index}" for index in range(65)]
    evidence = _bounded_cardinality_evidence(items + ["proof-0", ""])

    assert len(evidence) == 64
    assert evidence[-1] == "cardinality_evidence_truncated=2"


def test_estimate_graph_index_rejects_non_dataframe_runtime_source_frame() -> None:
    source = GraphNode(id="source", data=NodeData(nodeType=NodeType.CONSTANT))

    with pytest.raises(TypeError, match="polars DataFrames"):
        _EstimateGraphIndex.build(
            PipelineGraph(nodes=[source]),
            "batch",
            runtime_source_frames_by_node={"source": object()},
        )  # type: ignore[arg-type]


def test_cardinality_binding_collision_and_safe_edge_names_fail_closed(monkeypatch) -> None:
    import haute._ram_estimate as ram_estimate

    index = _cardinality_index_for_node(NodeType.POLARS, {}, parent_count=2)
    index.node_map["parent-0"].data.label = "left_source"
    index.node_map["parent-1"].data.label = "right_source"
    node = index.node_map["target"]
    edges = tuple(edge for edge in index.pruned_edges if edge.target == "target")
    names = [ram_estimate._safe_edge_input_name(edge, index) for edge in edges]
    assert all(names)
    monkeypatch.setattr(
        ram_estimate,
        "resolve_input_mapping_names",
        lambda source_names, mapping: (source_names[1], source_names[1]),
    )
    node.data.config["inputMapping"] = {"left": names[0], "right": names[1]}
    assert _cardinality_name_bindings(index, node, edges) is None

    missing = GraphEdge(id="missing", source="absent", target="target")
    assert _safe_edge_input_name(missing, index) is None
    api = GraphNode(id="api", data=NodeData(nodeType=NodeType.API_INPUT))
    api_index = _EstimateGraphIndex.build(PipelineGraph(nodes=[api, node], edges=[]), "batch")
    malformed = GraphEdge(id="malformed", source="api", target="target")
    assert _safe_edge_input_name(malformed, api_index) is None


def test_port_operand_counts_rejects_missing_bindings_and_unbound_names(monkeypatch) -> None:
    import haute._ram_estimate as ram_estimate

    index = _cardinality_index_for_node(NodeType.POLARS, {}, parent_count=1)
    node = index.node_map["target"]
    edges = tuple(edge for edge in index.pruned_edges if edge.target == "target")
    with monkeypatch.context() as patcher:
        patcher.setattr(ram_estimate, "_cardinality_name_bindings", lambda *_args: None)
        assert _port_operand_counts(edges, index, node, {"parent_0": 1}) is None

    assert _port_operand_counts(edges, index, node, {"not_bound": 1}) is None


def test_join_estimate_reports_unresolved_operand_binding(tmp_path: Path, monkeypatch) -> None:
    import haute._ram_estimate as ram_estimate

    path = tmp_path / "source.parquet"
    _write_shape_source(path)
    graph = _self_join_graph(path, "df = src.join(src, on='segment', validate='m:1')")
    monkeypatch.setattr(ram_estimate, "_port_operand_counts", lambda *_args: None)

    [(_, estimate)] = list(
        estimate_materialisation_boundaries(
            graph, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )

    assert estimate.state is MaterialisationEstimateState.UNAVAILABLE
    assert estimate.unavailable_reason == "join_operand_binding_unresolved"


def _cardinality_index_for_node(
    node_type: NodeType, config: dict[str, object], parent_count: int = 1
) -> _EstimateGraphIndex:
    parents = [
        GraphNode(id=f"parent-{index}", data=NodeData(nodeType=NodeType.CONSTANT))
        for index in range(parent_count)
    ]
    target = GraphNode(id="target", data=NodeData(nodeType=node_type, config=config))
    edges = [
        GraphEdge(id=f"edge-{index}", source=parent.id, target=target.id)
        for index, parent in enumerate(parents)
    ]
    index = _EstimateGraphIndex.build(PipelineGraph(nodes=[*parents, target], edges=edges), "batch")
    for parent in parents:
        index.cardinality_by_target[(parent.id, None)] = _ResolvedRowCardinality.proven(
            4, 5, (f"source={parent.id}",)
        )
    return index


@pytest.mark.parametrize(
    ("node_type", "config", "expected_rows"),
    [
        (NodeType.POLARS, {"code": "df = df.filter(pl.col('value') > 0)"}, 4),
        (NodeType.SCENARIO_EXPANDER, {"steps": 3}, 12),
        (NodeType.RATING_STEP, {}, 4),
        (NodeType.EXPLORE, {"code": "df = df.filter(pl.col('value') > 0)"}, 4),
        (NodeType.MODEL_SCORE, {}, 4),
        (NodeType.OPTIMISER, {}, 4),
        (NodeType.OPTIMISER_APPLY, {}, 4),
        (NodeType.EXTERNAL_FILE, {}, 4),
        (NodeType.DATA_OUTPUT, {}, 4),
    ],
)
def test_row_cardinality_resolution_proves_closed_node_semantics(
    node_type: NodeType, config: dict[str, object], expected_rows: int
) -> None:
    index = _cardinality_index_for_node(node_type, config)

    result = _resolve_row_cardinality_from_index(index, "target", None)

    assert result.available
    assert result.output_rows == expected_rows
    assert result.peak_rows is not None and result.peak_rows >= expected_rows


@pytest.mark.parametrize(
    ("node_type", "config", "parent_count", "reason"),
    [
        (NodeType.POLARS, {}, 1, "empty_code"),
        (NodeType.SCENARIO_EXPANDER, {"steps": 0}, 1, "invalid_scenario_steps"),
        (NodeType.SCENARIO_EXPANDER, {}, 2, "invalid_input_cardinality"),
        (NodeType.RATING_STEP, {}, 2, "invalid_input_cardinality"),
        (NodeType.OPTIMISER, {"data_input": "absent"}, 1, "invalid_optimiser_input"),
        (NodeType.OPTIMISER, {"data_input": 0}, 1, "invalid_optimiser_input"),
        (NodeType.OPTIMISER, {}, 2, "invalid_optimiser_input"),
        (
            NodeType.OPTIMISER_APPLY,
            {"optimiser_mode": "ratebook"},
            1,
            "invalid_optimiser_apply_input",
        ),
        (NodeType.EXTERNAL_FILE, {}, 0, "external_object_row_cardinality_unknown"),
        (NodeType.DATA_OUTPUT, {}, 2, "invalid_input_cardinality"),
    ],
)
def test_row_cardinality_resolution_refuses_ambiguous_or_invalid_semantics(
    node_type: NodeType, config: dict[str, object], parent_count: int, reason: str
) -> None:
    index = _cardinality_index_for_node(node_type, config, parent_count)

    result = _resolve_row_cardinality_from_index(index, "target", None)

    assert not result.available
    assert result.unavailable_reason == reason


def test_ratebook_apply_cardinality_uses_exact_incoming_edge_name() -> None:
    index = _cardinality_index_for_node(
        NodeType.OPTIMISER_APPLY,
        {"optimiser_mode": "ratebook", "ratebook_input": "banded_quotes"},
        2,
    )
    index.node_map["parent-0"].data.label = "scored_quotes"
    index.node_map["parent-1"].data.label = "banded_quotes"
    index.cardinality_by_target[("parent-0", None)] = _ResolvedRowCardinality.proven(
        11,
        12,
        ("source=parent-0",),
    )
    index.cardinality_by_target[("parent-1", None)] = _ResolvedRowCardinality.proven(
        7,
        8,
        ("source=parent-1",),
    )

    result = _resolve_row_cardinality_from_index(index, "target", None)

    assert result.available
    assert result.output_rows == 7


def test_cardinality_helpers_fail_closed_for_invalid_bindings_and_missing_nodes() -> None:
    index = _cardinality_index_for_node(NodeType.POLARS, {"inputMapping": {"alias": 1}})
    target = index.node_map["target"]
    edge = index.pruned_edges[0]
    parent = index.cardinality_by_target[(edge.source, None)]

    assert _named_cardinality_inputs(index, target, ((edge, parent),)) is None
    unavailable = _passthrough_cardinality("target", (), evidence="preserved")
    assert unavailable.unavailable_reason == "input_cardinality_unavailable"
    missing = _resolve_row_cardinality_from_index(index, "missing", None)
    assert missing.unavailable_reason == "node_missing"


def test_cardinality_binding_uses_collapsed_submodel_public_output_label() -> None:
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "occurrence",
                    "type": "submodel",
                    "data": {
                        "label": "Occurrence presentation",
                        "nodeType": "submodel",
                        "config": {
                            "definitionId": "definition_public_output",
                            "alias": "unrelated_alias",
                        },
                    },
                },
                {
                    "id": "target",
                    "data": {
                        "label": "target",
                        "nodeType": "polars",
                        "config": {},
                    },
                },
            ],
            "edges": [
                {
                    "id": "public-result-edge",
                    "source": "occurrence",
                    "target": "target",
                    "sourceHandle": "out__opaque-output-id",
                }
            ],
            "submodels": {
                "definition_public_output": {
                    "definitionId": "definition_public_output",
                    "file": "modules/public_output.py",
                    "graph": {
                        "nodes": [
                            {
                                "id": "internal_result",
                                "data": {
                                    "label": "private implementation result",
                                    "nodeType": "polars",
                                    "config": {},
                                },
                            }
                        ],
                        "edges": [],
                    },
                    "inputPorts": [],
                    "outputPorts": [
                        {
                            "portId": "opaque-output-id",
                            "label": "public result",
                            "source": {
                                "nodeId": "internal_result",
                                "handleId": None,
                            },
                        }
                    ],
                }
            },
        }
    )
    index = _EstimateGraphIndex.build(graph, "batch")
    edge = index.pruned_edges[0]
    proof = _ResolvedRowCardinality.proven(4, 4, ("proof",))

    bindings = _named_cardinality_inputs(
        index,
        index.node_map["target"],
        ((edge, proof),),
    )

    assert bindings == {"public_result": proof}


def test_cardinality_resolution_handles_constants_and_rejects_invalid_join_arity() -> None:
    constant_index = _EstimateGraphIndex.build(
        PipelineGraph(nodes=[GraphNode(id="constant", data=NodeData(nodeType=NodeType.CONSTANT))]),
        "batch",
    )
    constant = _resolve_row_cardinality_from_index(constant_index, "constant", None)
    assert (constant.output_rows, constant.peak_rows) == (1, 1)

    join_index = _cardinality_index_for_node(NodeType.EDGE_JOIN, {}, parent_count=1)
    result = _resolve_row_cardinality_from_index(join_index, "target", None)
    assert result.unavailable_reason == "invalid_join_arity"


def test_cardinality_binding_and_node_failure_paths_fail_closed() -> None:
    api = GraphNode(id="api", data=NodeData(nodeType=NodeType.API_INPUT))
    target = GraphNode(id="target", data=NodeData(nodeType=NodeType.POLARS))
    api_edge = GraphEdge(id="api-edge", source="api", target="target")
    index = _EstimateGraphIndex.build(PipelineGraph(nodes=[api, target], edges=[api_edge]), "batch")
    proof = _ResolvedRowCardinality.proven(4, 4, ("proof",))
    assert _named_cardinality_inputs(index, target, ((api_edge, proof),)) is None

    duplicate = _cardinality_index_for_node(NodeType.POLARS, {}, parent_count=2)
    duplicate.node_map["parent-1"].data.label = duplicate.node_map["parent-0"].data.label
    duplicate_edges = tuple(
        (edge, duplicate.cardinality_by_target[(edge.source, None)])
        for edge in duplicate.pruned_edges
    )
    assert (
        _named_cardinality_inputs(duplicate, duplicate.node_map["target"], duplicate_edges) is None
    )

    cases = [
        (
            NodeType.POLARS,
            {"code": "df = df", "inputMapping": {"df": 1}},
            1,
            "invalid_input_name_binding",
        ),
        (NodeType.SCENARIO_EXPANDER, {"steps": "invalid"}, 1, "invalid_scenario_steps"),
        (
            NodeType.SCENARIO_EXPANDER,
            {"steps": 2, "code": "df = df.filter(pl.col('x') > 0)"},
            1,
            None,
        ),
        (NodeType.MODEL_SCORE, {"code": "df = df.filter(pl.col('x') > 0)"}, 1, None),
        (
            NodeType.MODEL_SCORE,
            {"code": "df = df", "inputMapping": {"df": 1}},
            1,
            "invalid_input_name_binding",
        ),
        (NodeType.OPTIMISER, {}, 0, "input_cardinality_unavailable"),
        (NodeType.OPTIMISER_APPLY, {}, 0, "input_cardinality_unavailable"),
        (NodeType.EXTERNAL_FILE, {"code": "df = df.filter(pl.col('x') > 0)"}, 1, None),
        (
            NodeType.EXTERNAL_FILE,
            {"code": "df = df", "inputMapping": {"df": 1}},
            1,
            "invalid_input_name_binding",
        ),
        (NodeType.OUTPUT, {}, 1, "row_semantics_unsupported"),
    ]
    for node_type, config, parent_count, reason in cases:
        result = _resolve_row_cardinality_from_index(
            _cardinality_index_for_node(node_type, config, parent_count), "target", None
        )
        if reason is None:
            assert result.available or result.unavailable_reason in {
                "ambiguous_frame_root",
                "unknown_frame_root",
            }
        else:
            assert result.unavailable_reason == reason


def test_cardinality_resolution_covers_malformed_bindings_and_source_transforms() -> None:
    proof = _ResolvedRowCardinality.proven(4, 4, ("proof",))
    malformed = _cardinality_index_for_node(NodeType.POLARS, {"inputMapping": ["not-a-map"]})
    edge = malformed.pruned_edges[0]
    assert (
        _named_cardinality_inputs(malformed, malformed.node_map["target"], ((edge, proof),)) is None
    )
    # A falsey non-mapping is malformed, not absent: runtime code generation
    # validates every non-``None`` value, so the estimator must not treat it
    # as "no mapping" and hand back an available estimate.
    for falsey in ([], "", 0):
        shaped = _cardinality_index_for_node(NodeType.POLARS, {"inputMapping": falsey})
        shaped_edge = shaped.pruned_edges[0]
        assert (
            _named_cardinality_inputs(shaped, shaped.node_map["target"], ((shaped_edge, proof),))
            is None
        ), falsey

    mapped = _cardinality_index_for_node(NodeType.POLARS, {"inputMapping": {"alias": "Unnamed"}})
    mapped_edge = mapped.pruned_edges[0]
    bindings = _named_cardinality_inputs(mapped, mapped.node_map["target"], ((mapped_edge, proof),))
    assert bindings is not None and bindings["alias"] == proof

    # Two edges collapsing onto one logical name is a graph the executor
    # refuses, so the estimator raises its error rather than quietly declining
    # to measure a run that cannot start.
    collision = _cardinality_index_for_node(NodeType.POLARS, {"inputMapping": {"a": "b"}}, 2)
    collision.node_map["parent-0"].data.label = "a"
    collision.node_map["parent-1"].data.label = "b"
    collision_edges = tuple(
        (item, collision.cardinality_by_target[(item.source, None)])
        for item in collision.pruned_edges
    )
    with pytest.raises(ConfigError, match="duplicate logical input names"):
        _named_cardinality_inputs(collision, collision.node_map["target"], collision_edges)

    no_edge_alias = _cardinality_index_for_node(NodeType.POLARS, {})
    assert (
        _named_cardinality_inputs(
            no_edge_alias,
            no_edge_alias.node_map["target"],
            (),
            alias_first_as_df=True,
        )
        is None
    )
    alias_collision = _cardinality_index_for_node(NodeType.POLARS, {}, 2)
    alias_collision.node_map["parent-0"].data.label = "first"
    alias_collision.node_map["parent-1"].data.label = "df"
    alias_edges = tuple(
        (item, alias_collision.cardinality_by_target[(item.source, None)])
        for item in alias_collision.pruned_edges
    )
    assert (
        _named_cardinality_inputs(
            alias_collision, alias_collision.node_map["target"], alias_edges, alias_first_as_df=True
        )
        is None
    )

    source = GraphNode(
        id="source",
        data=NodeData(
            nodeType=NodeType.DATA_INPUT, config={"code": "df = df.filter(pl.col('x') > 0)"}
        ),
    )
    source_index = _EstimateGraphIndex.build(PipelineGraph(nodes=[source]), "batch")
    source_index.metadata_by_node["source"] = _DetailedSourceMetadata(
        4, 1, {"x": "Int64"}, {}, {}, 32
    )
    source_result = _resolve_row_cardinality_from_index(source_index, "source", None)
    assert source_result.available and source_result.output_rows == 4


def test_cardinality_resolution_detects_cycles_and_invalid_join_or_score_inputs() -> None:
    cycle = _cardinality_index_for_node(NodeType.POLARS, {})
    cycle.resolving_cardinality.add(("target", None))
    with pytest.raises(RuntimeError, match="cycle"):
        cycle.resolve_cardinality("target")

    join = _cardinality_index_for_node(NodeType.EDGE_JOIN, {}, 2)
    assert (
        _resolve_row_cardinality_from_index(join, "target", None).unavailable_reason
        == "invalid_join_config"
    )
    score = _cardinality_index_for_node(NodeType.MODEL_SCORE, {}, 0)
    assert (
        _resolve_row_cardinality_from_index(score, "target", None).unavailable_reason
        == "input_cardinality_unavailable"
    )
    optimiser = _cardinality_index_for_node(NodeType.OPTIMISER, {"data_input": "Unnamed"})
    resolved = _resolve_row_cardinality_from_index(optimiser, "target", None)
    assert resolved.available and resolved.output_rows == 4


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

        # noqa: PT011 - broad by design. The contract under test is "a missing file
        # raises rather than returning empty metadata"; polars does not promise a
        # stable exception type for a missing parquet path across versions.
        with pytest.raises(Exception):  # noqa: PT011
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
    from haute._json_shred._cache import build_per_port_cache
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

    def test_group_by_boundary_batch_reuses_one_graph_and_port_metadata_index(
        self,
        json_api_input,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One strategy request must not reopen the same source metadata per boundary."""

        import haute._ram_estimate as ram_estimate_mod
        from haute.execution import _estimate_materialising_boundaries

        _data_path, config, _cache_dir, _committed_dir = json_api_input
        source = _make_source_node(node_id="quote_in", node_type="apiInput", config=config)
        aggregate_code = {"code": "df = df.group_by('policy_id').agg(pl.len().alias('count'))"}
        first = _make_transform_node(node_id="agg1", config=aggregate_code)
        second = _make_transform_node(node_id="agg2", config=aggregate_code)
        graph = PipelineGraph(
            nodes=[source, first, second],
            edges=[
                GraphEdge(
                    id="e1",
                    source=source.id,
                    target=first.id,
                    sourceHandle="policies",
                ),
                GraphEdge(id="e2", source=first.id, target=second.id),
            ],
        )
        real_port_metadata = ram_estimate_mod._json_api_input_port_metadata
        metadata_calls = 0

        def counting_port_metadata(node, port):
            nonlocal metadata_calls
            metadata_calls += 1
            return real_port_metadata(node, port)

        monkeypatch.setattr(
            ram_estimate_mod,
            "_json_api_input_port_metadata",
            counting_port_metadata,
        )

        estimate = _estimate_materialising_boundaries(
            graph, {first.id: "group_by", second.id: "group_by"}, source="live"
        )

        assert estimate.state is MaterialisationEstimateState.AVAILABLE
        assert metadata_calls == 1

    def test_group_by_boundary_batch_stops_after_first_unavailable_estimate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unusable first boundary must not probe unrelated later sources."""

        import haute.execution as execution_mod
        from haute.execution import _estimate_materialising_boundaries

        graph = PipelineGraph(nodes=[], edges=[])

        def estimates(*_args, **_kwargs):
            yield "first", MaterialisationEstimate.unavailable("metadata_missing")
            raise AssertionError("later boundary should not be estimated")

        monkeypatch.setattr(execution_mod, "estimate_materialisation_boundaries", estimates)

        estimate = _estimate_materialising_boundaries(graph, ["first", "later"], source="live")

        assert estimate.state is MaterialisationEstimateState.UNAVAILABLE
        assert estimate.unavailable_reason == "first:metadata_missing"

    def test_runtime_source_frame_replaces_unreadable_configured_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = _make_source_node(
            node_id="src",
            label="src",
            node_type="apiInput",
            config={"path": ""},
        )
        aggregate = _make_transform_node(
            node_id="agg",
            config={
                "code": (
                    "df = src.group_by('segment').agg(pl.col('premium').sum().alias('premium'))"
                )
            },
        )
        graph = PipelineGraph(
            nodes=[source, aggregate],
            edges=[
                GraphEdge(
                    id="e1",
                    source="src",
                    target="agg",
                    sourceHandle="src",
                )
            ],
        )
        runtime_frame = pl.DataFrame({"segment": ["a", "a", "b"], "premium": [1.0, 2.0, 4.0]})

        monkeypatch.setattr(
            "haute._ram_estimate._detailed_source_metadata_for_node",
            lambda _node: pytest.fail("configured source metadata must not be read"),
        )

        [(_, estimate)] = list(
            estimate_materialisation_boundaries(
                graph,
                ["agg"],
                runtime_source_frames_by_node={"src": runtime_frame},
            )
        )

        assert estimate.state is MaterialisationEstimateState.AVAILABLE
        assert estimate.estimated_peak_bytes is not None
        assert estimate.estimated_peak_bytes > 0

    def test_planning_and_loading_share_one_unchanged_source_content_proof(
        self,
        json_api_input,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Planner metadata and loading reuse the persisted cache-build source proof."""

        from haute._json_shred import _source_proof
        from haute._json_shred._cache import load_v2_api_source
        from haute._ram_estimate import _json_api_input_port_metadata

        data_path, config, _cache_dir, _committed_dir = json_api_input
        node = _make_source_node(node_type="apiInput", config=config)
        _source_proof._clear_data_file_signature_memo()
        real_hash_file = _source_proof._hash_file
        raw_hashes = 0

        def counting_hash_file(path):
            nonlocal raw_hashes
            if Path(path).resolve() == data_path.resolve():
                raw_hashes += 1
            return real_hash_file(path)

        monkeypatch.setattr(_source_proof, "_hash_file", counting_hash_file)

        metadata = _json_api_input_port_metadata(node, "policies")
        frames = load_v2_api_source(
            str(data_path),
            config,
            port_columns={"policies": {"policy_id"}},
        )

        assert metadata is not None and metadata.row_count == 2
        assert frames["policies"].collect()["policy_id"].to_list() == [1, 2]
        assert raw_hashes == 0

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

    def test_source_proof_is_reused_when_plausible_working_is_stale(
        self,
        json_api_input,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import shutil

        import orjson

        from haute._json_shred import _source_proof
        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, working_dir, committed_dir = json_api_input
        committed_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(working_dir, committed_dir, dirs_exist_ok=True)
        working_meta_path = working_dir / "meta.json"
        working_meta = orjson.loads(working_meta_path.read_bytes())
        working_meta["data_file"]["sha256"] = "0" * 64
        working_meta_path.write_bytes(orjson.dumps(working_meta))
        node = _make_source_node(node_type="apiInput", config=config)
        real_source_proof = _source_proof._data_file_signature
        source_proof_calls = 0

        def counting_source_proof(path):
            nonlocal source_proof_calls
            source_proof_calls += 1
            return real_source_proof(path)

        monkeypatch.setattr(
            "haute._json_shred._source_proof._data_file_signature",
            counting_source_proof,
        )

        metadata = _json_api_input_port_metadata(node, "policies")

        assert metadata is not None and metadata.row_count == 2
        assert source_proof_calls == 1

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

    def test_a_tampered_cache_artifact_is_not_used_for_admission(
        self,
        json_api_input,
    ) -> None:
        """Admission must size the exact generation runtime would accept."""

        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, cache_dir, _committed_dir = json_api_input
        pl.DataFrame({"policy_id": [999]}).write_parquet(cache_dir / "policies.parquet")
        node = _make_source_node(node_type="apiInput", config=config)

        assert _json_api_input_port_metadata(node, "policies") is None

    def test_a_snapshot_with_a_different_schema_is_not_used_for_admission(
        self,
        json_api_input,
        tmp_path: Path,
    ) -> None:
        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, _cache_dir, _committed_dir = json_api_input
        incompatible_snapshot = tmp_path / "incompatible.parquet"
        pl.DataFrame({"unexpected": [1]}).write_parquet(incompatible_snapshot)
        node = _make_source_node(node_type="apiInput", config=config)

        with (
            patch(
                "haute._json_shred._runtime_storage._snapshot_cache_artifact_locked",
                return_value=incompatible_snapshot,
            ),
            patch("haute._json_shred._runtime_storage._release_runtime_snapshot") as release,
        ):
            assert _json_api_input_port_metadata(node, "policies") is None

        assert release.call_count == 1

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

    def test_absent_cache_metadata_does_not_hash_the_uncached_source(
        self,
        json_api_input,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Admission must not add a full-file pass before direct execution."""

        import shutil

        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, working_dir, committed_dir = json_api_input
        shutil.rmtree(working_dir)
        if committed_dir.exists():
            shutil.rmtree(committed_dir)
        node = _make_source_node(node_type="apiInput", config=config)

        def unexpected_source_proof(_path):
            raise AssertionError("an absent cache must not require a source hash")

        monkeypatch.setattr(
            "haute._json_shred._source_proof._data_file_signature",
            unexpected_source_proof,
        )

        assert _json_api_input_port_metadata(node, "policies") is None

    def test_an_unreadable_cache_warns_and_reports_unavailable(self, json_api_input) -> None:
        """Estimation degrades to "unknown" rather than raising into a caller
        that would treat the failure as "unlimited"."""

        from haute._ram_estimate import _json_api_input_port_metadata

        _data_path, config, _cache_dir, _committed_dir = json_api_input
        node = _make_source_node(node_type="apiInput", config=config)

        with patch(
            "haute._json_shred._source_proof._data_file_signature",
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
        target = _make_transform_node(config={"code": "df = df"})
        graph = PipelineGraph(
            nodes=[source, target],
            edges=[
                GraphEdge(
                    id="e1",
                    source=source.id,
                    target=target.id,
                    sourceHandle="quotes",
                )
            ],
        )

        estimate = _boundary_estimate(graph, target.id)

        assert estimate.state is MaterialisationEstimateState.UNAVAILABLE
        assert estimate.unavailable_reason == "source_row_count_unavailable"

    def test_an_unresolvable_target_reports_target_schema_unavailable(self, tmp_path) -> None:
        path = tmp_path / "quotes.parquet"
        pl.DataFrame({"a": range(4)}).write_parquet(str(path))
        source = _make_source_node(node_type="apiInput", config={"path": str(path)})
        target = _make_transform_node(config={"code": "df = df"})
        graph = PipelineGraph(
            nodes=[source, target],
            edges=[
                GraphEdge(
                    id="e1",
                    source=source.id,
                    target=target.id,
                    sourceHandle="quotes",
                )
            ],
        )

        with patch("haute._ram_estimate._resolve_target_columns_from_index", return_value=None):
            estimate = _boundary_estimate(graph, target.id)

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


# ---------------------------------------------------------------------------
# Provable Polars shapes beneath a materialisation boundary
# ---------------------------------------------------------------------------

_GROUP_BY_PREMIUM = "df = df.group_by('segment').agg(pl.col('premium').sum().alias('premium'))"
_GROUP_BY_VALUE = "df = df.group_by('segment').agg(pl.col('value').sum().alias('total'))"

PROVABLE_SHAPES: tuple[tuple[str, str, str], ...] = (
    ("control_filter", "df = df.filter(pl.col('premium') > 0)", _GROUP_BY_PREMIUM),
    ("drop", "df = df.drop('extra')", _GROUP_BY_PREMIUM),
    ("drop_nulls_subset", "df = df.drop_nulls(subset=['premium'])", _GROUP_BY_PREMIUM),
    ("drop_nulls", "df = df.drop_nulls()", _GROUP_BY_PREMIUM),
    ("with_row_index", "df = df.with_row_index('row_id')", _GROUP_BY_PREMIUM),
    ("str_contains", "df = df.filter(pl.col('s').str.contains('x'))", _GROUP_BY_PREMIUM),
    (
        "dt_truncate",
        "df = df.with_columns(pl.col('t').dt.truncate('1mo').alias('month'))",
        _GROUP_BY_PREMIUM,
    ),
    (
        "literal_unpivot",
        "df = df.unpivot(on=['premium', 'extra'], index=['segment'])",
        _GROUP_BY_VALUE,
    ),
)

_PROVABLE_SHAPE_ROWS = 20


def _write_shape_source(path: Path) -> None:
    """A parquet with the column types every provable shape exercises."""
    rows = _PROVABLE_SHAPE_ROWS
    pl.DataFrame(
        {
            "segment": [f"seg-{index % 4}" for index in range(rows)],
            "premium": [None if index % 7 == 0 else float(index) for index in range(rows)],
            "extra": [index for index in range(rows)],
            "s": [
                None if index % 5 == 0 else f"a{'x' if index % 2 else 'y'}{index}"
                for index in range(rows)
            ],
            "t": [
                None if index % 6 == 0 else date(2024, 1 + (index % 12), 1 + (index % 28))
                for index in range(rows)
            ],
        },
        schema={
            "segment": pl.String,
            "premium": pl.Float64,
            "extra": pl.Int64,
            "s": pl.String,
            "t": pl.Date,
        },
    ).write_parquet(str(path))


def _shape_graph(path: Path, transform_code: str, group_by_code: str) -> PipelineGraph:
    source = _make_source_node(
        node_id="source",
        node_type="dataInput",
        config=_ready_file_input_config(path),
    )
    transform = _make_transform_node(node_id="shape", config={"code": transform_code})
    group_by = _make_transform_node(node_id="agg", config={"code": group_by_code})
    return PipelineGraph(
        nodes=[source, transform, group_by],
        edges=[
            GraphEdge(id="e1", source=source.id, target=transform.id),
            GraphEdge(id="e2", source=transform.id, target=group_by.id),
        ],
    )


@pytest.mark.parametrize(
    ("transform_code", "group_by_code"),
    [pytest.param(shape[1], shape[2], id=shape[0]) for shape in PROVABLE_SHAPES],
)
def test_provable_polars_shapes_keep_the_group_by_estimate_available(
    tmp_path: Path,
    transform_code: str,
    group_by_code: str,
) -> None:
    path = tmp_path / "shapes.parquet"
    _write_shape_source(path)
    graph = _shape_graph(path, transform_code, group_by_code)

    estimate = _boundary_estimate(graph, "agg")

    assert estimate.state is MaterialisationEstimateState.AVAILABLE, estimate.unavailable_reason
    assert estimate.estimated_peak_bytes is not None
    assert estimate.estimated_peak_bytes > 0


def test_dynamic_unpivot_keeps_the_group_by_estimate_unavailable(tmp_path: Path) -> None:
    """Without a literal ``on`` list the expansion factor has no length evidence."""
    path = tmp_path / "shapes.parquet"
    _write_shape_source(path)
    graph = _shape_graph(path, "df = df.unpivot(index=['segment'])", _GROUP_BY_VALUE)

    estimate = _boundary_estimate(graph, "agg")

    assert estimate.state is MaterialisationEstimateState.UNAVAILABLE
    assert estimate.unavailable_reason is not None
    assert estimate.unavailable_reason.startswith("row_cardinality_unavailable:")
    assert "dynamic_unpivot" in estimate.unavailable_reason


def test_literal_unpivot_cardinality_is_bounded_by_the_on_column_count() -> None:
    """A two-column literal ``unpivot`` is exactly a doubling of the input rows."""
    index = _cardinality_index_for_node(
        NodeType.POLARS,
        {"code": "df = df.unpivot(on=['premium', 'extra'], index=['segment'])"},
    )
    index.cardinality_by_target[("parent-0", None)] = _ResolvedRowCardinality.proven(
        _PROVABLE_SHAPE_ROWS,
        _PROVABLE_SHAPE_ROWS,
        ("source=parent-0",),
    )

    result = _resolve_row_cardinality_from_index(index, "target", None)

    assert result.available, result.unavailable_reason
    assert result.output_rows == 2 * _PROVABLE_SHAPE_ROWS
    assert result.peak_rows == 2 * _PROVABLE_SHAPE_ROWS


@pytest.mark.parametrize(
    ("operator", "factor_basis_points"),
    [
        ("group_by", 100),
        ("sort", 300),
        ("unique", 350),
        ("reverse", 250),
        ("over", 250),
        ("join_asof", 250),
        ("top_k", 100),
        ("bottom_k", 100),
    ],
)
def test_boundary_estimate_applies_and_records_the_operator_memory_factor(
    tmp_path: Path,
    operator: str,
    factor_basis_points: int,
) -> None:
    """EXEC-P07: the measured operator surcharge multiplies the finished estimate."""
    path = tmp_path / "shapes.parquet"
    _write_shape_source(path)
    graph = _shape_graph(path, "df = df.filter(pl.col('premium') > 0)", _GROUP_BY_PREMIUM)

    [(_, base)] = list(estimate_materialisation_boundaries(graph, ["agg"]))
    [(_, scaled)] = list(
        estimate_materialisation_boundaries(
            graph,
            ["agg"],
            boundary_operators={"agg": (operator,)},
        )
    )

    assert base.estimated_peak_bytes is not None
    assert scaled.estimated_peak_bytes is not None
    assert (
        scaled.estimated_peak_bytes == (base.estimated_peak_bytes * factor_basis_points + 99) // 100
    )
    assert f"boundary_operator={operator}" in scaled.assumptions
    assert f"materialisation_factor_basis_points={factor_basis_points}" in scaled.assumptions


def test_boundary_estimate_without_an_operator_carries_no_surcharge(tmp_path: Path) -> None:
    path = tmp_path / "shapes.parquet"
    _write_shape_source(path)
    graph = _shape_graph(path, "df = df.filter(pl.col('premium') > 0)", _GROUP_BY_PREMIUM)

    estimate = _boundary_estimate(graph, "agg")

    assert "materialisation_factor_basis_points=100" in estimate.assumptions
    assert not any(item.startswith("boundary_operator=") for item in estimate.assumptions)


def _join_graph(left_path: Path, right_path: Path, join_code: str) -> PipelineGraph:
    """left/right sources -> join -> group_by."""
    left = _make_source_node(
        node_id="left",
        label="left",
        node_type="dataInput",
        config=_ready_file_input_config(left_path),
    )
    right = _make_source_node(
        node_id="right",
        label="right",
        node_type="dataInput",
        config=_ready_file_input_config(right_path),
    )
    joined = _make_transform_node(node_id="joined", label="joined", config={"code": join_code})
    aggregated = _make_transform_node(
        node_id="agg",
        label="agg",
        config={
            "code": "df = df.group_by('segment').agg(pl.col('premium').sum().alias('premium'))"
        },
    )
    return PipelineGraph(
        nodes=[left, right, joined, aggregated],
        edges=[
            GraphEdge(id="e1", source="left", target="joined"),
            GraphEdge(id="e2", source="right", target="joined"),
            GraphEdge(id="e3", source="joined", target="agg"),
        ],
    )


def test_declared_join_boundary_is_sized_from_its_ports_not_its_output(tmp_path: Path) -> None:
    """A declared ``m:1`` join cannot emit more rows than its left operand.

    The contract is what makes input sizing sound: the join holds both ports and
    streams an output the contract already bounds by one of them.
    """
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    graph = _join_graph(
        left_path, right_path, "df = left.join(right, on='segment', how='left', validate='m:1')"
    )

    estimate = _boundary_estimate(graph, "joined")

    assert estimate.state is MaterialisationEstimateState.AVAILABLE, estimate.unavailable_reason
    assert estimate.estimated_peak_bytes is not None
    assert estimate.estimated_peak_bytes > 0

    [(_, scaled)] = list(
        estimate_materialisation_boundaries(
            graph, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )
    assert scaled.state is MaterialisationEstimateState.AVAILABLE, scaled.unavailable_reason
    assert scaled.estimated_peak_bytes is not None
    assert scaled.depends_on_many_to_many_join is False
    # The port bound is one source's rows, and the declared contract keeps the
    # output there too.
    assert f"boundary_input_rows_upper_bound={_PROVABLE_SHAPE_ROWS}" in scaled.assumptions
    assert f"boundary_output_rows_upper_bound={_PROVABLE_SHAPE_ROWS}" in scaled.assumptions
    # Same rows and same widths as the unoperated estimate, so only the operator
    # factor separates them.
    assert "materialisation_factor_basis_points=200" in scaled.assumptions
    assert scaled.estimated_peak_bytes == (estimate.estimated_peak_bytes * 200 + 99) // 100


def test_undeclared_join_boundary_is_sized_from_the_row_product(tmp_path: Path) -> None:
    """Without a contract the row product is the only bound the join has.

    The certification lane measured a three-times fan-out join above the
    input-sized figure, so input sizing is reserved for declared joins.
    """
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    graph = _join_graph(left_path, right_path, "df = left.join(right, on='segment')")

    [(_, scaled)] = list(
        estimate_materialisation_boundaries(
            graph, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )

    assert scaled.state is MaterialisationEstimateState.AVAILABLE, scaled.unavailable_reason
    assert scaled.depends_on_many_to_many_join is True
    product = _PROVABLE_SHAPE_ROWS * _PROVABLE_SHAPE_ROWS
    assert f"boundary_input_rows_upper_bound={product}" in scaled.assumptions
    assert f"boundary_output_rows_upper_bound={product}" in scaled.assumptions


def test_an_explicit_many_to_many_contract_is_also_unbounded(tmp_path: Path) -> None:
    """``validate='m:m'`` declares the absence of a bound, not a bound."""
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    graph = _join_graph(
        left_path, right_path, "df = left.join(right, on='segment', validate='m:m')"
    )

    [(_, scaled)] = list(
        estimate_materialisation_boundaries(
            graph, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )

    assert scaled.state is MaterialisationEstimateState.AVAILABLE, scaled.unavailable_reason
    assert scaled.depends_on_many_to_many_join is True


def test_group_by_after_an_undeclared_join_still_sees_the_row_product(tmp_path: Path) -> None:
    """The unbounded-join flag is inherited by whatever materialises downstream."""
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    graph = _join_graph(left_path, right_path, "df = left.join(right, on='segment')")

    [(_, downstream)] = list(
        estimate_materialisation_boundaries(
            graph, ["agg"], boundary_operators={"agg": ("group_by",)}
        )
    )

    assert downstream.state is MaterialisationEstimateState.AVAILABLE
    assert downstream.depends_on_many_to_many_join is True
    assert (
        f"cardinality_peak_upper_bound={_PROVABLE_SHAPE_ROWS * _PROVABLE_SHAPE_ROWS}"
        in downstream.assumptions
    )


def test_declared_join_uniqueness_keeps_the_downstream_bound_tight(tmp_path: Path) -> None:
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    graph = _join_graph(
        left_path,
        right_path,
        "df = left.join(right, on='segment', how='left', validate='m:1')",
    )

    downstream = _boundary_estimate(graph, "agg")

    assert downstream.state is MaterialisationEstimateState.AVAILABLE
    assert f"cardinality_peak_upper_bound={_PROVABLE_SHAPE_ROWS}" in downstream.assumptions


def test_join_boundary_with_an_unresolvable_port_stays_unavailable(tmp_path: Path) -> None:
    left_path = tmp_path / "left.parquet"
    _write_shape_source(left_path)
    left = _make_source_node(
        node_id="left",
        label="left",
        node_type="dataInput",
        config=_ready_file_input_config(left_path),
    )
    dynamic = _make_transform_node(
        node_id="right",
        label="right",
        config={"code": "df = df.unpivot(index=['segment'])"},
    )
    joined = _make_transform_node(
        node_id="joined", label="joined", config={"code": "df = left.join(right, on='segment')"}
    )
    graph = PipelineGraph(
        nodes=[left, dynamic, joined],
        edges=[
            GraphEdge(id="e1", source="left", target="right"),
            GraphEdge(id="e2", source="left", target="joined"),
            GraphEdge(id="e3", source="right", target="joined"),
        ],
    )

    [(_, estimate)] = list(
        estimate_materialisation_boundaries(
            graph, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )

    assert estimate.state is MaterialisationEstimateState.UNAVAILABLE
    assert estimate.unavailable_reason is not None
    assert "dynamic_unpivot" in estimate.unavailable_reason


@pytest.mark.parametrize(
    ("operators", "expected_factor"),
    [
        (("unique", "reverse"), 350),
        (("reverse", "unique"), 350),
        (("sort", "reverse"), 300),
        (("reverse",), 250),
    ],
)
def test_boundary_estimate_applies_the_maximum_chained_factor(
    tmp_path: Path,
    operators: tuple[str, ...],
    expected_factor: int,
) -> None:
    """A chained node's estimate must not depend on which operator came first."""
    path = tmp_path / "shapes.parquet"
    _write_shape_source(path)
    graph = _shape_graph(path, "df = df.filter(pl.col('premium') > 0)", _GROUP_BY_PREMIUM)

    [(_, base)] = list(estimate_materialisation_boundaries(graph, ["agg"]))
    [(_, scaled)] = list(
        estimate_materialisation_boundaries(graph, ["agg"], boundary_operators={"agg": operators})
    )

    assert base.estimated_peak_bytes is not None
    assert scaled.estimated_peak_bytes is not None
    assert scaled.estimated_peak_bytes == (base.estimated_peak_bytes * expected_factor + 99) // 100
    assert f"materialisation_factor_basis_points={expected_factor}" in scaled.assumptions
    # The diagnostic still blames the first operator evaluated; the whole chain
    # is recorded so the factor can be audited.
    assert f"boundary_operator={operators[0]}" in scaled.assumptions
    assert f"boundary_operators={','.join(operators)}" in scaled.assumptions


def _three_source_join_graph(
    left_path: Path,
    middle_path: Path,
    right_path: Path,
    join_code: str,
) -> PipelineGraph:
    nodes = [
        _make_source_node(
            node_id=name, label=name, node_type="dataInput", config=_ready_file_input_config(path)
        )
        for name, path in (
            ("left", left_path),
            ("middle", middle_path),
            ("right", right_path),
        )
    ]
    nodes.append(_make_transform_node(node_id="joined", label="joined", config={"code": join_code}))
    return PipelineGraph(
        nodes=nodes,
        edges=[
            GraphEdge(id="e1", source="left", target="joined"),
            GraphEdge(id="e2", source="middle", target="joined"),
            GraphEdge(id="e3", source="right", target="joined"),
        ],
    )


def test_chained_join_is_sized_from_the_previous_join_not_the_original_ports(
    tmp_path: Path,
) -> None:
    """The second join consumes the first join's result, product included."""
    paths = []
    for name in ("left", "middle", "right"):
        path = tmp_path / f"{name}.parquet"
        _write_shape_source(path)
        paths.append(path)
    undeclared = _three_source_join_graph(
        *paths,
        "df = left.join(middle, on='segment').join(right, on='segment')",
    )
    declared = _three_source_join_graph(
        *paths,
        "df = left.join(middle, on='segment', how='left', validate='m:1')"
        ".join(right, on='segment', how='left', validate='m:1')",
    )

    [(_, chained)] = list(
        estimate_materialisation_boundaries(
            undeclared, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )
    [(_, linear)] = list(
        estimate_materialisation_boundaries(
            declared, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )

    assert chained.state is MaterialisationEstimateState.AVAILABLE, chained.unavailable_reason
    assert linear.state is MaterialisationEstimateState.AVAILABLE, linear.unavailable_reason
    # The undeclared chain's second join consumes the first join's product and
    # is itself bounded only by its own product on top of that.
    assert (
        "boundary_input_rows_upper_bound="
        f"{_PROVABLE_SHAPE_ROWS * _PROVABLE_SHAPE_ROWS * _PROVABLE_SHAPE_ROWS}"
        in chained.assumptions
    )
    # A declared m:1 chain never expands, so it stays at one port's rows.
    assert f"boundary_input_rows_upper_bound={_PROVABLE_SHAPE_ROWS}" in linear.assumptions
    assert chained.estimated_peak_bytes is not None
    assert linear.estimated_peak_bytes is not None
    assert chained.estimated_peak_bytes > linear.estimated_peak_bytes


def test_cross_join_boundary_is_unmeasured_and_therefore_unavailable(tmp_path: Path) -> None:
    """EXEC-P07 measured inner/left/asof joins; a cross join inherits nothing."""
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    graph = _join_graph(left_path, right_path, "df = left.join(right, how='cross')")

    [(_, estimate)] = list(
        estimate_materialisation_boundaries(
            graph, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )

    assert estimate.state is MaterialisationEstimateState.UNAVAILABLE
    assert estimate.unavailable_reason == "cross_join_unmeasured"


def test_cross_join_output_product_still_propagates_downstream(tmp_path: Path) -> None:
    """Only the join's own admission is withheld; its output bound is unchanged."""
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    graph = _join_graph(left_path, right_path, "df = left.join(right, how='cross')")

    downstream = _boundary_estimate(graph, "agg")

    assert downstream.state is MaterialisationEstimateState.AVAILABLE
    assert (
        f"cardinality_peak_upper_bound={_PROVABLE_SHAPE_ROWS * _PROVABLE_SHAPE_ROWS}"
        in downstream.assumptions
    )


def _self_join_graph(path: Path, join_code: str) -> PipelineGraph:
    """One source wired into a join node twice: both ports hold the same frame."""
    source = _make_source_node(
        node_id="src", label="src", node_type="dataInput", config=_ready_file_input_config(path)
    )
    joined = _make_transform_node(node_id="joined", label="joined", config={"code": join_code})
    return PipelineGraph(
        nodes=[source, joined],
        edges=[GraphEdge(id="e1", source="src", target="joined")],
    )


def test_self_join_charges_the_shared_port_width_twice(tmp_path: Path) -> None:
    """``df.join(df, ...)`` holds one frame as two operands, so it costs two."""
    path = tmp_path / "src.parquet"
    _write_shape_source(path)
    self_join = _self_join_graph(path, "df = src.join(src, on='segment', validate='m:1')")
    single = _self_join_graph(path, "df = src.sort('premium')")

    [(_, joined)] = list(
        estimate_materialisation_boundaries(
            self_join, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )
    [(_, sorted_once)] = list(
        estimate_materialisation_boundaries(
            single, ["joined"], boundary_operators={"joined": ("sort",)}
        )
    )

    assert joined.state is MaterialisationEstimateState.AVAILABLE, joined.unavailable_reason
    assert "boundary_resident_operand_count=2" in joined.assumptions
    assert joined.estimated_peak_bytes is not None
    assert sorted_once.estimated_peak_bytes is not None
    # Same rows and same source columns: only the doubled port width and the
    # two operators' factors (200 for join, 300 for sort) differ.
    single_port_width_at_join_factor = (sorted_once.estimated_peak_bytes * 200 + 299) // 300
    assert joined.estimated_peak_bytes == 2 * single_port_width_at_join_factor


def test_a_lookup_joined_twice_is_charged_twice(tmp_path: Path) -> None:
    """A chain that joins the same lookup twice holds it twice."""
    left_path = tmp_path / "left.parquet"
    lookup_path = tmp_path / "lookup.parquet"
    _write_shape_source(left_path)
    _write_shape_source(lookup_path)

    def _graph(code: str) -> PipelineGraph:
        nodes = [
            _make_source_node(
                node_id=name,
                label=name,
                node_type="dataInput",
                config=_ready_file_input_config(path),
            )
            for name, path in (("left", left_path), ("lookup", lookup_path))
        ]
        nodes.append(_make_transform_node(node_id="joined", label="joined", config={"code": code}))
        return PipelineGraph(
            nodes=nodes,
            edges=[
                GraphEdge(id="e1", source="left", target="joined"),
                GraphEdge(id="e2", source="lookup", target="joined"),
            ],
        )

    twice = _graph(
        "df = left.join(lookup, on='segment', how='left', validate='m:1')"
        ".join(lookup, on='segment', how='left', validate='m:1')"
    )
    once = _graph("df = left.join(lookup, on='segment', how='left', validate='m:1')")

    estimates = {}
    for name, graph in (("twice", twice), ("once", once)):
        [(_, estimate)] = list(
            estimate_materialisation_boundaries(
                graph, ["joined"], boundary_operators={"joined": ("join",)}
            )
        )
        estimates[name] = estimate

    assert estimates["twice"].state is MaterialisationEstimateState.AVAILABLE
    assert "boundary_resident_operand_count=3" in estimates["twice"].assumptions
    # Two ports resident once each is the ordinary case and stays unannotated.
    assert not any(
        item.startswith("boundary_resident_operand_count=")
        for item in estimates["once"].assumptions
    )
    assert estimates["twice"].estimated_peak_bytes is not None
    assert estimates["once"].estimated_peak_bytes is not None
    assert estimates["twice"].estimated_peak_bytes > estimates["once"].estimated_peak_bytes


def test_an_ordinary_two_port_join_is_unchanged_by_operand_counting(tmp_path: Path) -> None:
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    graph = _join_graph(
        left_path, right_path, "df = left.join(right, on='segment', how='left', validate='m:1')"
    )

    [(_, estimate)] = list(
        estimate_materialisation_boundaries(
            graph, ["joined"], boundary_operators={"joined": ("join",)}
        )
    )

    assert estimate.state is MaterialisationEstimateState.AVAILABLE
    assert not any(
        item.startswith("boundary_resident_operand_count=") for item in estimate.assumptions
    )
    assert f"boundary_input_rows_upper_bound={_PROVABLE_SHAPE_ROWS}" in estimate.assumptions


def _alias_join_graph(path: Path, code: str, input_mapping: dict[str, str]) -> PipelineGraph:
    """One source into a join node that renames it through ``inputMapping``."""
    source = _make_source_node(
        node_id="src", label="src", node_type="dataInput", config=_ready_file_input_config(path)
    )
    joined = _make_transform_node(
        node_id="joined",
        label="joined",
        config={"code": code, "inputMapping": input_mapping},
    )
    return PipelineGraph(
        nodes=[source, joined],
        edges=[GraphEdge(id="e1", source="src", target="joined")],
    )


def test_a_self_join_through_an_input_mapping_alias_is_charged_twice(tmp_path: Path) -> None:
    """``inputMapping`` renames the frame; both operands are still resident.

    Counting by the edge's own name and defaulting the alias to one reference
    silently halved this estimate.
    """
    path = tmp_path / "src.parquet"
    _write_shape_source(path)
    aliased = _alias_join_graph(
        path,
        "df = logical.join(logical, on='segment', validate='m:1')",
        {"logical": "src"},
    )
    direct = _self_join_graph(path, "df = src.join(src, on='segment', validate='m:1')")

    estimates = {}
    for name, graph in (("aliased", aliased), ("direct", direct)):
        [(_, estimate)] = list(
            estimate_materialisation_boundaries(
                graph, ["joined"], boundary_operators={"joined": ("join",)}
            )
        )
        estimates[name] = estimate

    assert estimates["aliased"].state is MaterialisationEstimateState.AVAILABLE, estimates[
        "aliased"
    ].unavailable_reason
    assert "boundary_resident_operand_count=2" in estimates["aliased"].assumptions
    # The alias must not change what the estimate costs.
    assert estimates["aliased"].estimated_peak_bytes == estimates["direct"].estimated_peak_bytes


def test_a_duplicate_valued_input_mapping_fails_loudly_instead_of_estimating(
    tmp_path: Path,
) -> None:
    """Two logical names for one edge is not a graph the runtime will execute.

    ``resolve_input_mapping_names`` is the canonical contract: the mapping is
    one-to-one. Summing the two aliases into one edge would have produced a
    confident estimate for a graph the executor rejects, so the estimator
    raises the same error rather than inventing an answer.
    """
    from haute._graph_utils import resolve_input_mapping_names

    mapping = {"alpha": "src", "beta": "src"}

    with pytest.raises(ConfigError) as runtime_error:
        resolve_input_mapping_names(["src"], mapping)

    path = tmp_path / "src.parquet"
    _write_shape_source(path)
    graph = _alias_join_graph(
        path,
        "df = alpha.join(beta, on='segment', validate='m:1')",
        mapping,
    )

    with pytest.raises(ConfigError) as estimator_error:
        list(
            estimate_materialisation_boundaries(
                graph, ["joined"], boundary_operators={"joined": ("join",)}
            )
        )

    # The analyst sees the executor's diagnosis, not an estimator-specific one.
    assert str(estimator_error.value) == str(runtime_error.value)
    assert "one distinct current edge input name" in str(estimator_error.value)


@pytest.mark.parametrize(
    ("validate", "expected"),
    [(None, True), ("m:m", True), ("m:1", False)],
)
def test_edge_join_contract_decides_the_many_to_many_flag(
    tmp_path: Path, validate: str | None, expected: bool
) -> None:
    """An Edge Join without a bounding contract carries the row product downstream."""
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    pl.DataFrame({"segment": ["a", "b"], "rate": [1.0, 2.0]}).write_parquet(right_path)
    left = _make_source_node(
        node_id="left",
        label="left",
        node_type="dataInput",
        config=_ready_file_input_config(left_path),
    )
    right = _make_source_node(
        node_id="right",
        label="right",
        node_type="dataInput",
        config=_ready_file_input_config(right_path),
    )
    config: dict[str, object] = {"how": "inner", "on": ["segment"]}
    if validate is not None:
        config["validate"] = validate
    joined = _make_edge_join_node(node_id="joined", label="joined", config=config)
    joined.data.nodeType = NodeType.EDGE_JOIN
    aggregated = _make_transform_node(
        node_id="agg",
        label="agg",
        config={
            "code": "df = df.group_by('segment').agg(pl.col('premium').sum().alias('premium'))"
        },
    )
    graph = PipelineGraph(
        nodes=[left, right, joined, aggregated],
        edges=[
            GraphEdge(id="e1", source="left", target="joined", targetHandle="base"),
            GraphEdge(id="e2", source="right", target="joined", targetHandle="join"),
            GraphEdge(id="e3", source="joined", target="agg"),
        ],
    )

    [(_, downstream)] = list(
        estimate_materialisation_boundaries(
            graph, ["agg"], boundary_operators={"agg": ("group_by",)}
        )
    )

    assert downstream.state is MaterialisationEstimateState.AVAILABLE, downstream.unavailable_reason
    assert downstream.depends_on_many_to_many_join is expected
