"""Graph pruning for deployment - keep only ancestors of the output node."""

from __future__ import annotations

from haute._graph_utils import edge_input_name
from haute._logging import get_logger
from haute.graph_utils import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    ancestors,
)

logger = get_logger(component="deploy.pruner")


def _live_only_edges(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> list[GraphEdge]:
    """Filter edges so liveSwitch nodes only keep their live input.

    For deployment we only want the live branch.  Input names are derived
    from each incoming edge, so multiple frames emitted by one apiInput are
    routed independently.
    """
    switch_ids: set[str] = set()
    for n in nodes:
        if n.data.nodeType == NodeType.LIVE_SWITCH:
            switch_ids.add(n.id)

    if not switch_ids:
        return edges

    # For each switch, identify the live input name from config and retain
    # only edges whose shared edge-derived name matches it.
    switch_live_edge_ids: dict[str, set[str]] = {}
    node_map = {n.id: n for n in nodes}

    def _matching_edge_ids(switch_id: str, input_name: str) -> set[str]:
        return {
            edge.id
            for edge in edges
            if edge.target == switch_id
            and edge_input_name(edge, node_map[edge.source]) == input_name
        }

    for sid in switch_ids:
        config = node_map[sid].data.config
        input_scenario_map = config.get("input_scenario_map", {})
        mapped_live_input_name = next(
            (k for k, v in input_scenario_map.items() if v == "live"),
            None,
        )
        if mapped_live_input_name is None:
            # Legacy fallback: use inputs[0] as the live input name and match
            # it against each connected edge using the same derivation.
            inputs = config.get("inputs", [])
            live_input_name = inputs[0] if isinstance(inputs, list) and inputs else "<missing>"
        else:
            live_input_name = mapped_live_input_name
        matching_edges = _matching_edge_ids(sid, live_input_name)
        if not matching_edges:
            source = (
                "input_scenario_map live input"
                if mapped_live_input_name is not None
                else "inputs[0]"
            )
            raise ValueError(
                f"LiveSwitch node '{sid}': {source} '{live_input_name}' "
                "does not match any connected node"
            )
        switch_live_edge_ids[sid] = matching_edges

    filtered: list[GraphEdge] = []
    for e in edges:
        if e.target in switch_ids:
            if e.id in switch_live_edge_ids.get(e.target, set()):
                filtered.append(e)
        else:
            filtered.append(e)

    return filtered


def prune_for_deploy(
    graph: PipelineGraph,
    output_node_id: str,
) -> tuple[PipelineGraph, list[str], list[str]]:
    """Prune a graph to only the ancestors of the output node.

    For liveSwitch nodes, only the live (first) input branch is kept.

    Args:
        graph: Full React Flow graph with "nodes" and "edges".
        output_node_id: The node ID whose ancestors form the scoring path.

    Returns:
        (pruned_graph, kept_node_ids, removed_node_ids)

    Raises:
        ValueError: If output_node_id is not in the graph.
    """
    nodes = graph.nodes
    edges = graph.edges

    all_ids = {n.id for n in nodes}
    if output_node_id not in all_ids:
        raise ValueError(
            f"Output node '{output_node_id}' not found in graph. Available nodes: {sorted(all_ids)}"
        )

    deploy_edges = _live_only_edges(nodes, edges)
    needed = ancestors(output_node_id, deploy_edges, all_ids)

    kept_nodes = [n for n in nodes if n.id in needed]
    kept_edges = [e for e in deploy_edges if e.source in needed and e.target in needed]
    removed_ids = sorted(all_ids - needed)

    pruned_graph = graph.model_copy(update={"nodes": kept_nodes, "edges": kept_edges})

    return pruned_graph, sorted(needed), removed_ids


def find_output_node(graph: PipelineGraph) -> str:
    """Find the single output node in a graph.

    Looks for nodes with ``nodeType="output"`` or ``config.output=True``.

    Raises:
        ValueError: If zero or multiple output nodes are found.
    """
    seen: set[str] = set()
    candidates: list[str] = []
    for n in graph.nodes:
        if n.id in seen:
            continue
        if n.data.nodeType == NodeType.OUTPUT or n.data.config.get("output"):
            candidates.append(n.id)
            seen.add(n.id)

    if len(candidates) == 0:
        raise ValueError("No output node found. Mark a node with @pipeline.output().")
    if len(candidates) > 1:
        raise ValueError(
            f"Multiple output nodes found: {candidates}. "
            "Only one node should be marked output=True."
        )
    return candidates[0]


def find_deploy_input_nodes(graph: PipelineGraph) -> list[str]:
    """Find apiInput nodes in a graph.

    Returns:
        List of node IDs (may be empty if none are marked).
    """
    return [n.id for n in graph.nodes if n.data.nodeType == NodeType.API_INPUT]


def find_source_nodes(graph: PipelineGraph) -> list[str]:
    """Find all source nodes in a graph (dataSource, apiInput, constant)."""
    return [
        n.id
        for n in graph.nodes
        if n.data.nodeType in (NodeType.DATA_SOURCE, NodeType.API_INPUT, NodeType.CONSTANT)
    ]
