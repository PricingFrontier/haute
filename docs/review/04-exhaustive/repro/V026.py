"""Isolated reproduction for V026.

Claim: build_linear_execution_chain_functions validates base_node_id /
chain_node_ids against ``node_map`` (the FULL graph returned by
projection.prepare_graph), NOT against the routing-scoped ``id_to_name``
(topo order of ancestors of target_node_id, post live-switch/source prune).

Consequence: a node that is present in node_map but ABSENT from id_to_name
(e.g. because it is not an ancestor of target_node_id) PASSES the
``missing`` check at execution.py:752-757, and is then silently dropped from
wiring by _build_funcs at _execute_lazy.py:1475
(``src_ids = [pid for pid in parents_of.get(nid, []) if pid in id_to_name]``).
The first chain node is therefore built with an EMPTY source list and no
error is raised -- a silently mis-wired chain instead of a loud failure.

This repro builds a tiny synthetic in-memory graph (no disk I/O, no project
files), calls the PUBLIC API directly with a base_node_id that is in the
graph but is NOT an ancestor of target_node_id, and ASSERTS on the specific
wrong behaviour: the chain node receives source_names == [] (mis-wired)
while no ValueError was raised.

If the bug were fixed (validation against the routing-scoped set), the call
would instead raise ValueError because base/chain are not in id_to_name.
"""

from __future__ import annotations

from typing import Any

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import build_linear_execution_chain_functions


def _node(node_id: str, label: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=label, nodeType=NodeType.POLARS, config={}),
    )


def main() -> None:
    # Graph topology:
    #
    #   base ---> chain0          (the chain we ask to wire)
    #   target                    (the routing target -- a SEPARATE, isolated
    #                              node that is NOT downstream of base/chain)
    #
    # All three nodes exist in node_map. But ancestors(target) == {target}
    # only, so neither ``base`` nor ``chain0`` is in id_to_name when we
    # prepare the graph for target_node_id="target".
    nodes = [
        _node("base", "Base Frame"),
        _node("chain0", "Chain Zero"),
        _node("target", "Routing Target"),
    ]
    edges = [
        GraphEdge(id="e_base_chain0", source="base", target="chain0"),
    ]
    graph = PipelineGraph(nodes=nodes, edges=edges)

    # Sanity: confirm the precondition the finding relies on.
    # base/chain0 are in node_map (full graph) but NOT ancestors of target.
    assert "base" in graph.node_map
    assert "chain0" in graph.node_map
    assert "target" in graph.node_map
    # target's only ancestor is itself; base/chain0 are unrelated to it.
    assert graph.parents_of.get("target", []) == []

    # Spy build_node_fn matching _build_funcs' call contract. It records the
    # source_names/source_ids actually wired for each node, and returns the
    # required (orig_node, fn, is_source) 3-tuple.
    recorded: dict[str, dict[str, Any]] = {}

    def spy_build_node_fn(
        node: GraphNode,
        *,
        source_names: list[str],
        source_ids: list[str],
        target_handles: Any = None,
        row_limit: Any = None,
        node_map: Any = None,
        orig_source_names: Any = None,
        upstream_ids: Any = None,
        preamble_ns: Any = None,
        source: str = "live",
        required_output_columns: Any = None,
        reuse_loaded_model: bool = False,
        execution_profile: Any = None,
    ) -> tuple[GraphNode, Any, bool]:
        recorded[node.id] = {
            "source_names": list(source_names),
            "source_ids": list(source_ids),
        }

        def _fn() -> None:  # pragma: no cover - never executed in this repro
            return None

        return node, _fn, False

    raised: Exception | None = None
    funcs: dict[str, Any] = {}
    try:
        funcs = build_linear_execution_chain_functions(
            graph,
            spy_build_node_fn,
            target_node_id="target",
            base_node_id="base",
            chain_node_ids=["chain0"],
            routing_source="batch",
            build_source="live",
        )
    except Exception as exc:  # noqa: BLE001 - we want to observe loud failures too
        raised = exc

    print("raised:", repr(raised))
    print("recorded:", recorded)

    # --- The crux of the bug --------------------------------------------
    # If V026 is REAL, the call did NOT raise (the line-752 check passed
    # against node_map), but the chain node ``chain0`` was built with an
    # EMPTY source list because its declared parent ``base`` is not in
    # id_to_name and was silently filtered out at _execute_lazy.py:1475.
    assert raised is None, (
        "Expected the mis-wiring to be SILENT (no error). The function raised "
        f"instead: {raised!r}. If this is a ValueError about base/chain not "
        "being in the prepared graph, the bug has been FIXED."
    )
    assert "chain0" in recorded, (
        "chain0 was never built -- repro setup is wrong, not the bug."
    )

    chain0_sources = recorded["chain0"]["source_names"]
    chain0_source_ids = recorded["chain0"]["source_ids"]

    # The DECLARED parent of chain0 is ``base`` (set up by chain_parents in
    # build_linear_execution_chain_functions). The CORRECT, non-buggy wiring
    # would feed chain0 from base. Instead we get an empty source list.
    assert chain0_sources == [], (
        "Expected chain0 to be SILENTLY mis-wired with an empty source list "
        f"(the bug). Got source_names={chain0_sources!r}. If this equals "
        "['base_frame'] the chain was correctly wired and the bug does not "
        "reproduce."
    )
    assert chain0_source_ids == [], (
        f"Expected chain0 source_ids==[] (silently dropped parent). Got "
        f"{chain0_source_ids!r}."
    )

    # And the function returned a func for chain0 regardless -- a built,
    # mis-wired chain rather than a loud failure.
    assert "chain0" in funcs, "chain0 should have produced a built function."

    print(
        "V026 REPRODUCED: base ('base') passed the node_map membership check "
        "but was silently dropped from wiring; chain0 built with EMPTY "
        "source_names=[] and NO ValueError was raised."
    )


if __name__ == "__main__":
    main()
