"""Port-aware executor scaffolding (MULTI_FRAME_PLAN commit 2).

This commit lays the foundation for port-aware data flow without changing
the consumer-side function-call mechanism. Three concrete deliverables:

1. ``GraphEdge.sourceHandle`` / ``.targetHandle`` reject empty string at
   Pydantic ingest. Null means "no port specified"; empty string is an
   invalid serialisation that surfaces immediately.

2. ``PreparedGraph.relevant_edges`` and ``_prepare_graph_with_edges``
   expose the post-pruning, ancestor-filtered edge list — downstream
   commits use this to look up per-edge ``sourceHandle`` when picking
   frames from multi-port sources.

3. The :func:`_build_input_kwargs` module-level helper in
   ``_execute_lazy`` captures the §4b binding rule (``sourceHandle or
   source_node_label``) for future use. Framework function wrappers in
   ``_builders.py`` accept both positional and keyword forms so the
   executor can switch its call convention incrementally without
   breaking direct callers (tests, deploy paths).

The function-call mechanism itself stays positional in this commit —
switching to kwargs requires reworking every wrapper + test-helper
lambda in the project, and the multi-port use-case is solved at the
source-emit layer (commit 4) by picking frames per edge rather than by
reshaping the consumer's call.
"""

from __future__ import annotations

import polars as pl
import pytest
from pydantic import ValidationError

from haute._execute_lazy import (
    _build_input_kwargs,
    _prepare_graph,
    _prepare_graph_with_edges,
)
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


# ─── 1. Pydantic validator rejects "" handles ─────────────────────


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


# ─── 2. PreparedGraph.relevant_edges + _prepare_graph_with_edges ──


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


def test_prepare_graph_with_edges_prunes_live_switch_inactive_edges() -> None:
    """If a live_switch has multiple incoming scenarios, only the active
    source's edge survives in ``relevant_edges``. The new helper must
    reflect that pruning so downstream port-aware code never sees an
    inactive edge.
    """
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
    # Only one edge survives to the switch under the "live" source.
    edges_into_sw = [e for e in relevant_edges if e.target == "sw"]
    assert len(edges_into_sw) == 1
    assert edges_into_sw[0].source == "live"
    # parents_of agrees.
    assert parents_of.get("sw") == ["live"]


# ─── 3. _build_input_kwargs binding rule ──────────────────────────


def test_build_input_kwargs_uses_source_label_when_handle_is_null() -> None:
    frame_a = pl.LazyFrame({"x": [1]})
    frame_b = pl.LazyFrame({"y": [2]})
    edges = [
        GraphEdge(id="e1", source="policies", target="child"),
        GraphEdge(id="e2", source="competitor_scoring", target="child"),
    ]
    kwargs = _build_input_kwargs(edges, [frame_a, frame_b], target_node_id="child")
    assert set(kwargs.keys()) == {"policies", "competitor_scoring"}
    assert kwargs["policies"] is frame_a
    assert kwargs["competitor_scoring"] is frame_b


def test_build_input_kwargs_uses_source_handle_when_set() -> None:
    """When sourceHandle is set (multi-port source), it overrides the
    source-label fallback. Two edges from the same source with distinct
    sourceHandles produce distinct kwarg keys."""
    frame_p = pl.LazyFrame({"policy_id": [1]})
    frame_d = pl.LazyFrame({"driver_id": [10]})
    edges = [
        GraphEdge(id="e1", source="quotes", target="child", sourceHandle="policies"),
        GraphEdge(id="e2", source="quotes", target="child", sourceHandle="drivers"),
    ]
    kwargs = _build_input_kwargs(edges, [frame_p, frame_d], target_node_id="child")
    assert set(kwargs.keys()) == {"policies", "drivers"}
    assert kwargs["policies"] is frame_p
    assert kwargs["drivers"] is frame_d


def test_build_input_kwargs_raises_on_duplicate_binding_key() -> None:
    """Two edges that resolve to the same key (e.g. two single-port edges
    from the same source — which would be a duplicate-edge bug — or two
    multi-port edges with the same sourceHandle) raise loudly."""
    frame_a = pl.LazyFrame({"x": [1]})
    frame_b = pl.LazyFrame({"y": [2]})
    edges = [
        GraphEdge(id="e1", source="a", target="child"),
        GraphEdge(id="e2", source="a", target="child"),
    ]
    with pytest.raises(ValueError, match="Duplicate parameter binding"):
        _build_input_kwargs(edges, [frame_a, frame_b], target_node_id="child")


def test_build_input_kwargs_raises_on_edge_count_mismatch() -> None:
    frame_a = pl.LazyFrame({"x": [1]})
    edges = [
        GraphEdge(id="e1", source="a", target="child"),
        GraphEdge(id="e2", source="b", target="child"),
    ]
    with pytest.raises(ValueError, match="binding mismatch"):
        _build_input_kwargs(edges, [frame_a], target_node_id="child")


# ─── 4. Framework wrappers accept positional AND kwarg forms ──────


def test_passthrough_fn_accepts_positional() -> None:
    """Existing positional callers (tests, deploy code) still work."""
    from haute._builders import _passthrough_fn

    frame = pl.LazyFrame({"x": [1, 2]})
    result = _passthrough_fn(frame)
    assert result is frame


def test_passthrough_fn_accepts_keyword() -> None:
    """New kwarg callers (forward-compatible) get the same behaviour."""
    from haute._builders import _passthrough_fn

    frame = pl.LazyFrame({"x": [1, 2]})
    result = _passthrough_fn(some_label=frame)
    assert result is frame


def test_passthrough_fn_returns_first_when_multiple() -> None:
    """The wrapper preserves insertion order so the "first incoming edge"
    semantics that the old positional code had carry over verbatim."""
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
