"""Isolated reproduction for V050.

Claim: ``_live_only_edges`` (deploy pruning, MAPPED ``input_scenario_map``
branch) resolves a liveSwitch's live edge by scanning the switch's incoming
edges and taking the FIRST whose source label sanitizes to ``live_input_name``,
then ``break``-ing (src/haute/deploy/_pruner.py:58-69). The only guard
(lines 65-69) raises when NO edge matches. It does NOT detect the case where
TWO distinct upstream source nodes have labels that COLLIDE under
``_sanitize_func_name`` (e.g. ``'Live Source'`` and ``'Live-Source'`` both ->
``'Live_Source'`` because sanitize maps both space and hyphen to ``'_'``).
Both edges then "match", whichever is listed first in ``graph.edges`` wins,
the other live-candidate is silently dropped, and ``prune_for_deploy`` returns
WITHOUT error. The deployed scoring path therefore depends on edge ordering and
can keep the WRONG upstream branch.

This repro builds a small synthetic in-memory ``PipelineGraph`` and runs the
real ``prune_for_deploy`` for both edge orderings, asserting that:
  (a) neither ordering raises (so the bug is silent), and
  (b) the surviving liveSwitch parent FLIPS between the two colliding sources
      purely as a function of edge order (demonstrably wrong, order-dependent
      branch selection).

ISOLATION: pure in-memory Pydantic models; no disk I/O; no rating/, src/,
tests/, or real project files are read or written.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Make the in-repo source importable without touching project data files.
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(_REPO_SRC))

from haute.deploy._pruner import prune_for_deploy  # noqa: E402
from haute.graph_utils import (  # noqa: E402
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    _sanitize_func_name,
)

# The two upstream source labels that collide under sanitization.
LABEL_A = "Live Source"   # -> "Live_Source"
LABEL_B = "Live-Source"   # -> "Live_Source"
LIVE_KEY = "Live_Source"  # the input_scenario_map 'live' key


def _build_graph(edge_order: tuple[str, str]) -> PipelineGraph:
    """Build a graph: a, b (colliding dataSources) -> switch -> output.

    ``edge_order`` controls the order of the two source->switch edges in
    ``graph.edges`` so we can observe first-match-wins behaviour.
    """
    nodes = [
        GraphNode(id="a", data=NodeData(label=LABEL_A, nodeType=NodeType.DATA_SOURCE)),
        GraphNode(id="b", data=NodeData(label=LABEL_B, nodeType=NodeType.DATA_SOURCE)),
        GraphNode(
            id="switch",
            data=NodeData(
                label="Switch",
                nodeType=NodeType.LIVE_SWITCH,
                config={
                    "input_scenario_map": {LIVE_KEY: "live", "Other": "batch"},
                    "inputs": [LIVE_KEY, "Other"],
                },
            ),
        ),
        GraphNode(id="output", data=NodeData(label="Output", nodeType=NodeType.OUTPUT)),
    ]

    # Two incoming edges to the switch from the colliding sources, plus the
    # switch -> output edge.  ``edge_order`` decides which source edge is first.
    src_first, src_second = edge_order
    edges = [
        GraphEdge(id=f"e_{src_first}", source=src_first, target="switch"),
        GraphEdge(id=f"e_{src_second}", source=src_second, target="switch"),
        GraphEdge(id="e_out", source="switch", target="output"),
    ]
    return PipelineGraph(nodes=nodes, edges=edges)


def _switch_parent_after_prune(edge_order: tuple[str, str]) -> dict:
    """Run prune_for_deploy; return the surviving switch parent + error (if any)."""
    graph = _build_graph(edge_order)
    error: BaseException | None = None
    parents: set[str] = set()
    kept_ids: list[str] = []
    try:
        pruned, kept_ids, _removed = prune_for_deploy(graph, "output")
        parents = {e.source for e in pruned.edges if e.target == "switch"}
    except BaseException as exc:  # noqa: BLE001 - characterising the failure
        error = exc
    return {
        "edge_order": edge_order,
        "switch_parents": parents,
        "kept_ids": kept_ids,
        "error": error,
    }


def main() -> int:
    failures: list[str] = []

    # Precondition: the two labels genuinely collide under sanitization, and
    # both equal the input_scenario_map 'live' key.
    san_a = _sanitize_func_name(LABEL_A)
    san_b = _sanitize_func_name(LABEL_B)
    print(f"_sanitize_func_name({LABEL_A!r}) -> {san_a!r}")
    print(f"_sanitize_func_name({LABEL_B!r}) -> {san_b!r}")
    if not (san_a == san_b == LIVE_KEY):
        failures.append(
            f"precondition: expected both labels to sanitize to {LIVE_KEY!r}, "
            f"got {san_a!r} and {san_b!r} — collision mechanism absent."
        )
        print("\nREPRO RESULT: claim NOT reproduced (precondition failed)")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    # Run both edge orderings.
    res_ab = _switch_parent_after_prune(("a", "b"))
    res_ba = _switch_parent_after_prune(("b", "a"))

    for res in (res_ab, res_ba):
        print(f"\n--- edge_order {res['edge_order']} ---")
        if res["error"] is not None:
            print(f"  prune_for_deploy -> {type(res['error']).__name__}: {res['error']}")
        else:
            print("  prune_for_deploy -> no error (SILENT)")
        print(f"  surviving switch parent(s) -> {res['switch_parents']}")
        print(f"  kept node ids              -> {res['kept_ids']}")

    # The bug prediction (1): both orderings return WITHOUT raising.  The
    # mapped-path guard only fires on no-match, never on ambiguous collision.
    if res_ab["error"] is not None or res_ba["error"] is not None:
        failures.append(
            "expected NO exception for either edge order (silent wrong-branch "
            f"selection), but got: ab={res_ab['error']!r}, ba={res_ba['error']!r}"
        )

    # The bug prediction (2): exactly one colliding source survives in each
    # case, and which one survives FLIPS with edge order (order-dependent).
    if res_ab["error"] is None and res_ba["error"] is None:
        if res_ab["switch_parents"] != {"a"}:
            failures.append(
                f"edge_order ('a','b'): expected surviving switch parent {{'a'}} "
                f"(first-match-wins), got {res_ab['switch_parents']}."
            )
        if res_ba["switch_parents"] != {"b"}:
            failures.append(
                f"edge_order ('b','a'): expected surviving switch parent {{'b'}} "
                f"(first-match-wins), got {res_ba['switch_parents']}."
            )
        if (
            res_ab["switch_parents"] == res_ba["switch_parents"]
            and not failures
        ):
            failures.append(
                "expected the kept live branch to FLIP with edge order, but both "
                f"orderings kept {res_ab['switch_parents']} — no order-dependence shown."
            )

    print()
    if failures:
        print("REPRO RESULT: claim NOT reproduced as predicted")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print("REPRO RESULT: BUG REPRODUCED — two upstream source labels colliding under")
    print("_sanitize_func_name both satisfy the live-input match; prune_for_deploy")
    print("silently keeps whichever edge is listed first and drops the other.  The")
    print("surviving liveSwitch parent flips from {'a'} to {'b'} purely with edge")
    print("order, with NO error raised — order-dependent wrong-branch selection.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # pragma: no cover - surface unexpected harness errors
        traceback.print_exc()
        raise SystemExit(2)
