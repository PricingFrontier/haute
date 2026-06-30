"""Port-aware executor behavior.

These tests cover the live pieces that keep multi-port data flow explicit:

1. ``GraphEdge.sourceHandle`` / ``.targetHandle`` reject empty string at
   Pydantic ingest. Null means "no port specified"; empty string is an
   invalid serialisation that surfaces immediately.

2. ``PreparedGraph.relevant_edges`` and ``_prepare_graph_with_edges``
   expose the post-pruning, ancestor-filtered edge list. The executor
   uses this to look up per-edge ``sourceHandle`` when picking frames
   from multi-port sources.

3. Framework function wrappers accept both positional and keyword forms.
   Direct callers and deploy paths keep working while port-aware routing
   happens at the source-emit layer by picking frames per edge.
"""

from __future__ import annotations

import polars as pl
import pytest
from pydantic import ValidationError

from haute._execute_lazy import _prepare_graph, _prepare_graph_with_edges
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

# 1. Pydantic validator rejects "" handles


def test_edge_accepts_null_source_handle() -> None:
    edge = GraphEdge(id="e", source="a", target="b")
    assert edge.sourceHandle is None
    assert edge.targetHandle is None


def test_edge_accepts_nonempty_string_source_handle() -> None:
    edge = GraphEdge(id="e", source="a", target="b", sourceHandle="policies")
    assert edge.sourceHandle == "policies"


def test_edge_rejects_empty_source_handle() -> None:
    with pytest.raises(ValidationError, match="Edge handle must be either"):
        GraphEdge(id="e", source="a", target="b", sourceHandle="")


def test_edge_rejects_empty_target_handle() -> None:
    with pytest.raises(ValidationError, match="Edge handle must be either"):
        GraphEdge(id="e", source="a", target="b", targetHandle="")


# 2. PreparedGraph.relevant_edges + _prepare_graph_with_edges


def _simple_polars_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(
                    label="src",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": "ignored.parquet"},
                ),
            ),
            GraphNode(
                id="tfm",
                data=NodeData(
                    label="tfm",
                    nodeType=NodeType.POLARS,
                    config={"code": "df = df.with_columns(y=pl.col('x') * 2)"},
                ),
            ),
        ],
        edges=[GraphEdge(id="e_src_tfm", source="src", target="tfm")],
    )


def test_prepare_graph_with_edges_exposes_relevant_edges() -> None:
    g = _simple_polars_graph()
    node_map, order, parents_of, id_to_name, relevant_edges = _prepare_graph_with_edges(g)
    assert [e.source for e in relevant_edges] == ["src"]
    assert [e.target for e in relevant_edges] == ["tfm"]
    # Backward-compat helper still returns the 4-tuple it always did.
    legacy = _prepare_graph(g)
    assert len(legacy) == 4
    assert legacy[2] == parents_of
    assert node_map["src"].data.label == "src"
    assert order == ["src", "tfm"]
    assert id_to_name["tfm"] == "tfm"


def test_prepare_graph_with_edges_prunes_live_switch_inactive_edges() -> None:
    """Only the active source's edge survives in ``relevant_edges``."""
    g = PipelineGraph(
        nodes=[
            GraphNode(
                id="batch",
                data=NodeData(
                    label="batch",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": "ignored.parquet"},
                ),
            ),
            GraphNode(
                id="live",
                data=NodeData(
                    label="live",
                    nodeType=NodeType.DATA_SOURCE,
                    config={"path": "ignored.parquet"},
                ),
            ),
            GraphNode(
                id="sw",
                data=NodeData(
                    label="sw",
                    nodeType=NodeType.LIVE_SWITCH,
                    config={"input_scenario_map": {"batch": "nb_batch", "live": "live"}},
                ),
            ),
        ],
        edges=[
            GraphEdge(id="e_batch_sw", source="batch", target="sw"),
            GraphEdge(id="e_live_sw", source="live", target="sw"),
        ],
        sources=["live", "nb_batch"],
        active_source="live",
    )
    _, _, parents_of, _, relevant_edges = _prepare_graph_with_edges(g, source="live")
    edges_into_sw = [e for e in relevant_edges if e.target == "sw"]
    assert len(edges_into_sw) == 1
    assert edges_into_sw[0].source == "live"
    assert parents_of.get("sw") == ["live"]


def test_dead_build_input_kwargs_api_is_absent() -> None:
    import haute._execute_lazy as execute_lazy

    assert not hasattr(execute_lazy, "_build_input_kwargs")


# 3. Framework wrappers accept positional AND kwarg forms


def test_passthrough_fn_accepts_positional() -> None:
    """Existing positional callers (tests, deploy code) still work."""
    from haute._builders import _passthrough_fn

    frame = pl.LazyFrame({"x": [1, 2]})
    result = _passthrough_fn(frame)
    assert result is frame


def test_passthrough_fn_accepts_keyword() -> None:
    """Keyword callers get the same behaviour."""
    from haute._builders import _passthrough_fn

    frame = pl.LazyFrame({"x": [1, 2]})
    result = _passthrough_fn(some_label=frame)
    assert result is frame


def test_passthrough_fn_returns_first_when_multiple() -> None:
    """The wrapper preserves insertion order for first-input semantics."""
    from haute._builders import _passthrough_fn

    frame_a = pl.LazyFrame({"x": [1]})
    frame_b = pl.LazyFrame({"y": [2]})
    assert _passthrough_fn(frame_a, frame_b) is frame_a
    assert _passthrough_fn(first=frame_a, second=frame_b) is frame_a


def test_passthrough_fn_empty_returns_empty_lazyframe() -> None:
    from haute._builders import _passthrough_fn

    result = _passthrough_fn()
    assert isinstance(result, pl.LazyFrame)
    assert result.collect().shape == (0, 0)
