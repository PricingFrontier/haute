"""Tests for ``resolve_run_plan`` — the global Run button's scope/export decision.

Pins the four run modes and the one product decision that may still flip: the
*default* mode (the Run button face) exports the data sinks the user has
selected, while the no-export modes never write — and the ``output`` (Quote
Response) node is never an export target.
"""

from __future__ import annotations

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import resolve_run_plan


def _node(nid: str, node_type: NodeType) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type))


def _graph() -> PipelineGraph:
    # src → mid(polars) → out(output);  src → sinkA;  mid → sinkB
    return PipelineGraph(
        nodes=[
            _node("src", NodeType.DATA_SOURCE),
            _node("mid", NodeType.POLARS),
            _node("out", NodeType.OUTPUT),
            _node("sinkA", NodeType.DATA_SINK),
            _node("sinkB", NodeType.DATA_SINK),
        ],
        edges=[
            GraphEdge(id="e1", source="src", target="mid"),
            GraphEdge(id="e2", source="mid", target="out"),
            GraphEdge(id="e3", source="src", target="sinkA"),
            GraphEdge(id="e4", source="mid", target="sinkB"),
        ],
    )


def test_all_export_writes_every_sink():
    plan = resolve_run_plan(_graph(), "all-export", [])
    assert plan.scope == "all"
    assert plan.compute_targets is None
    assert set(plan.export_sink_ids) == {"sinkA", "sinkB"}


def test_all_no_export_writes_nothing_even_with_selection():
    plan = resolve_run_plan(_graph(), "all-no-export", ["sinkA"])  # selection ignored
    assert plan.scope == "all"
    assert plan.compute_targets is None
    assert plan.export_sink_ids == []


def test_default_exports_only_selected_sinks():
    # Default-export rule: selecting a sink + a non-sink → default writes the sink only.
    plan = resolve_run_plan(_graph(), "default", ["sinkA", "mid"])
    assert plan.scope == "selected"
    assert plan.compute_targets == ["sinkA", "mid"]
    assert plan.export_sink_ids == ["sinkA"]


def test_default_with_non_sink_selection_writes_nothing():
    plan = resolve_run_plan(_graph(), "default", ["mid"])
    assert plan.scope == "selected"
    assert plan.export_sink_ids == []


def test_default_with_no_selection_runs_whole_canvas_no_export():
    # Nothing selected → whole canvas, but never export (default export keys on a
    # *selected* sink, of which there is none).
    plan = resolve_run_plan(_graph(), "default", [])
    assert plan.scope == "all"
    assert plan.compute_targets is None
    assert plan.export_sink_ids == []


def test_selected_no_export_never_writes_even_a_selected_sink():
    # Shift+Enter on a selected sink computes it but writes nothing.
    plan = resolve_run_plan(_graph(), "selected-no-export", ["sinkA"])
    assert plan.scope == "selected"
    assert plan.compute_targets == ["sinkA"]
    assert plan.export_sink_ids == []


def test_output_node_is_never_an_export_target():
    # Selecting the API output node in default mode writes nothing — `output`
    # is not a data sink.
    plan = resolve_run_plan(_graph(), "default", ["out"])
    assert plan.export_sink_ids == []


def test_unknown_selected_ids_are_dropped():
    plan = resolve_run_plan(_graph(), "default", ["ghost", "sinkB"])
    assert plan.compute_targets == ["sinkB"]
    assert plan.export_sink_ids == ["sinkB"]
