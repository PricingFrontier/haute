"""Adversarial repro for claim:
  batch-scenario-resolution-routes-to-missing-branch

Claim: When a graph has live_switch nodes whose ISM maps inputs only to
'live' (mode a), _resolve_batch_scenario returns None -> sink_scenario='batch';
then prune_live_switch_edges(source='batch') is alleged to drop EVERY edge into
the switch, so _build_lazy_node raises an opaque
  ValueError("No input data available for node '<switch>'")
instead of a clear "no batch branch configured" config error.

Mode (b): two switches, one ISM {in:'overnight'}, one ISM {in:'live'};
picked='overnight' but the live-only switch is alleged to lose all its inputs.

This script tests the ACTUAL runtime behaviour of the real functions and
asserts on the SPECIFIC predicted wrong behaviour (all edges pruned -> opaque
"No input data available").  If the line-2091 guard
   `if source not in input_scenario_map.values(): continue`
prevents pruning, the claim's mechanism does NOT hold and the prediction is
refuted.

Isolation: pure in-memory graphs, no disk I/O, no project files.
"""

from __future__ import annotations

import polars as pl

from haute._builders import _build_node_fn
from haute._execute_lazy import _execute_lazy
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _resolve_batch_scenario
from haute.projection import prune_live_switch_edges


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _source(nid: str) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=NodeType.DATA_SOURCE))


def _switch(nid: str, ism: dict[str, str]) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.LIVE_SWITCH,
            config={"input_scenario_map": ism},
        ),
    )


def _sink(nid: str) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.DATA_SINK,
            config={"path": "out.parquet", "format": "parquet"},
        ),
    )


def _real_source_build_fn(node, *, source="live", source_names=None, **kwargs):
    """Real-enough builder: data sources emit a frame; everything else is
    pass-through.  We bypass execute_sink's disk write and instead drive
    _execute_lazy directly to observe whether the switch raises 'No input
    data available' under the resolved batch scenario."""
    nt = node.data.nodeType
    if nt == NodeType.DATA_SOURCE:
        return node.id, (lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy()), True
    # For real live_switch behaviour we must use the actual builder so the
    # switch_fn raises if it truly receives no inputs.  But to isolate the
    # *graph pruning* prediction we only need pass-through semantics for the
    # downstream sink; the switch's own builder is what matters.
    return _build_node_fn(node, source=source, source_names=source_names, **kwargs)


def run_mode_a() -> dict:
    """source -> switch(L) -> sink ; ISM maps the single input to 'live' only.

    Predicted by claim: sink_scenario='batch' (since _resolve_batch_scenario
    returns None), pruning drops the only edge into L, and _execute_lazy raises
    'No input data available for node L'.
    """
    nodes = [_source("src"), _switch("L", {"src": "live"}), _sink("snk")]
    edges = [_e("src", "L"), _e("L", "snk")]
    graph = PipelineGraph(nodes=nodes, edges=edges)

    resolved = _resolve_batch_scenario(graph)
    sink_scenario = resolved or "batch"

    # What does pruning actually do at the resolved sink scenario?
    pruned = prune_live_switch_edges(graph.edges, graph.node_map, sink_scenario)
    pruned_pairs = {(e.source, e.target) for e in pruned}
    edge_into_switch_survives = ("src", "L") in pruned_pairs

    # Now actually execute lazily up to the sink under the resolved scenario.
    raised = None
    try:
        outputs, order, _parents, _names = _execute_lazy(
            graph,
            _real_source_build_fn,
            target_node_id="snk",
            source=sink_scenario,
        )
        # Force the plan so any lazy 'no input' would surface; switch_fn raises
        # eagerly at build time when it has zero inputs, but collect to be safe.
        lf = outputs.get("snk")
        collected_ok = lf is not None and lf.collect().height == 3
    except Exception as exc:  # noqa: BLE001 - we are characterising the failure
        raised = exc
        collected_ok = False

    return {
        "resolved_batch_scenario": resolved,
        "sink_scenario": sink_scenario,
        "edge_into_switch_survives_pruning": edge_into_switch_survives,
        "raised_type": type(raised).__name__ if raised else None,
        "raised_msg": str(raised) if raised else None,
        "collected_ok": collected_ok,
    }


def run_mode_b() -> dict:
    """Two switches: A ISM {srcA:'overnight'}, B ISM {srcB:'live'}.

    Predicted by claim: resolved='overnight'; the live-only switch B loses all
    inputs under source='overnight' and raises 'No input data available'.
    """
    nodes = [
        _source("srcA"),
        _source("srcB"),
        _switch("A", {"srcA": "overnight"}),
        _switch("B", {"srcB": "live"}),
        _sink("snk"),
    ]
    edges = [
        _e("srcA", "A"),
        _e("srcB", "B"),
        _e("A", "snk"),
        _e("B", "snk"),
    ]
    graph = PipelineGraph(nodes=nodes, edges=edges)

    resolved = _resolve_batch_scenario(graph)
    sink_scenario = resolved or "batch"

    pruned = prune_live_switch_edges(graph.edges, graph.node_map, sink_scenario)
    pruned_pairs = {(e.source, e.target) for e in pruned}
    edge_into_B_survives = ("srcB", "B") in pruned_pairs
    edge_into_A_survives = ("srcA", "A") in pruned_pairs

    raised = None
    try:
        outputs, order, _parents, _names = _execute_lazy(
            graph,
            _real_source_build_fn,
            target_node_id="snk",
            source=sink_scenario,
        )
        lf = outputs.get("snk")
        collected_ok = lf is not None and lf.collect().height >= 1
    except Exception as exc:  # noqa: BLE001
        raised = exc
        collected_ok = False

    return {
        "resolved_batch_scenario": resolved,
        "sink_scenario": sink_scenario,
        "edge_into_A_survives_pruning": edge_into_A_survives,
        "edge_into_B_survives_pruning": edge_into_B_survives,
        "raised_type": type(raised).__name__ if raised else None,
        "raised_msg": str(raised) if raised else None,
        "collected_ok": collected_ok,
    }


def main() -> None:
    print("=" * 72)
    print("MODE (a): single switch, ISM -> 'live' only")
    a = run_mode_a()
    for k, v in a.items():
        print(f"  {k}: {v}")

    print("=" * 72)
    print("MODE (b): switch A ISM->'overnight', switch B ISM->'live'")
    b = run_mode_b()
    for k, v in b.items():
        print(f"  {k}: {v}")

    print("=" * 72)
    # ---- ASSERTIONS encoding the CLAIM's prediction --------------------
    # The claim predicts: edge into switch is pruned away (False survives) AND
    # the run raises a 'No input data available' ValueError.
    #
    # We assert the OPPOSITE of "claim reproduced" so this script PASSES iff
    # the claim's mechanism is REFUTED.  If the claim were real these asserts
    # would fail and print the discrepancy.

    claim_a_reproduced = (
        not a["edge_into_switch_survives_pruning"]
        and a["raised_type"] == "ValueError"
        and a["raised_msg"] is not None
        and "No input data available" in a["raised_msg"]
    )
    claim_b_reproduced = (
        not b["edge_into_B_survives_pruning"]
        and b["raised_type"] == "ValueError"
        and b["raised_msg"] is not None
        and "No input data available" in b["raised_msg"]
    )

    print(f"claim_mode_a_reproduced (all-edges-pruned + opaque error): {claim_a_reproduced}")
    print(f"claim_mode_b_reproduced (all-edges-pruned + opaque error): {claim_b_reproduced}")

    # The guard at projection.py:2091 means a scenario absent from the ISM
    # values causes that switch to be SKIPPED for pruning, so its input edge
    # survives.  Verify that explicitly (expected vs actual).
    assert a["edge_into_switch_survives_pruning"] is True, (
        "REFUTATION BASIS BROKEN: expected the 'live'-only switch's input edge "
        "to SURVIVE pruning at source='batch' (line-2091 guard), but it was "
        f"pruned. Actual mode-a result: {a}"
    )
    assert b["edge_into_B_survives_pruning"] is True, (
        "REFUTATION BASIS BROKEN: expected the 'live'-only switch B's input "
        "edge to SURVIVE pruning at source='overnight', but it was pruned. "
        f"Actual mode-b result: {b}"
    )
    assert claim_a_reproduced is False, (
        f"CLAIM MODE (a) REPRODUCED — finding is REAL. Actual: {a}"
    )
    assert claim_b_reproduced is False, (
        f"CLAIM MODE (b) REPRODUCED — finding is REAL. Actual: {b}"
    )

    print()
    print("RESULT: Claim mechanism REFUTED — the line-2091 guard keeps the")
    print("switch's input edge when the resolved scenario is absent from its")
    print("ISM values, so no all-edges-pruned 'No input data available' occurs.")


if __name__ == "__main__":
    main()
