"""Graph pruning for deployment - keep only ancestors of the output node."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from haute._graph_utils import edge_input_name
from haute._logging import get_logger
from haute._types import REQUEST_INPUT_NODE_TYPES, RESPONSE_NODE_TYPES
from haute.graph_utils import GraphEdge, GraphNode, NodeType, PipelineGraph

logger = get_logger(component="deploy.pruner")


def _live_input_edges(
    switch: GraphNode,
    edges: list[GraphEdge],
    node_map: Mapping[str, GraphNode],
    *,
    submodels: Mapping[str, Any] | None,
) -> list[GraphEdge]:
    """The edges into *switch* that carry its live input.

    Selection is per edge, by the edge-derived input name, so two frames from one
    Quote Input are routed independently. A switch with no input mapped to ``live``,
    or whose live input matches no connected edge, raises ``ValueError``.
    """
    input_scenario_map = switch.data.config.get("input_scenario_map") or {}
    live_inputs = [name for name, scenario in input_scenario_map.items() if scenario == "live"]
    if not live_inputs:
        raise ValueError(
            f"LiveSwitch node '{switch.id}' maps no input to the 'live' scenario in "
            "input_scenario_map, so a deployment cannot tell which branch it serves. "
            "Map one of its inputs to 'live'."
        )
    live_input_name = live_inputs[0]
    matching = [
        edge
        for edge in edges
        if edge_input_name(edge, node_map[edge.source], submodels=submodels) == live_input_name
    ]
    if not matching:
        raise ValueError(
            f"LiveSwitch node '{switch.id}': input_scenario_map live input '{live_input_name}' "
            "does not match any connected node"
        )
    return matching


def _live_ancestry(graph: PipelineGraph, output_node_id: str) -> tuple[set[str], set[str]]:
    """The nodes and edges the output depends on when served: ``(node ids, edge ids)``.

    Walks backward from *output_node_id*; at each Live Switch it reaches, only the
    edges of the switch's live input are followed. A switch the output does not
    reach, or reaches only through another switch's non-live branch, is never
    inspected.
    """
    node_map = {node.id: node for node in graph.nodes}
    incoming: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        if edge.source in node_map and edge.target in node_map:
            incoming.setdefault(edge.target, []).append(edge)
    needed = {output_node_id}
    kept_edges: set[str] = set()
    pending = [output_node_id]
    while pending:
        node = node_map[pending.pop()]
        edges = incoming.get(node.id, [])
        if node.data.nodeType == NodeType.LIVE_SWITCH:
            edges = _live_input_edges(node, edges, node_map, submodels=graph.submodels)
        for edge in edges:
            kept_edges.add(edge.id)
            if edge.source not in needed:
                needed.add(edge.source)
                pending.append(edge.source)
    return needed, kept_edges


def prune_for_deploy(
    graph: PipelineGraph,
    output_node_id: str,
) -> tuple[PipelineGraph, list[str], list[str]]:
    """Prune a graph to only the ancestors of the output node.

    For liveSwitch nodes, only the configured live input branch is kept.

    Args:
        graph: Full React Flow graph with "nodes" and "edges".
        output_node_id: The node ID whose ancestors form the scoring path.

    Returns:
        (pruned_graph, kept_node_ids, removed_node_ids)

    Raises:
        ValueError: If output_node_id is not in the graph, or a Live Switch the
            output reaches has no live input or one matching no connected edge.
    """
    all_ids = {n.id for n in graph.nodes}
    if output_node_id not in all_ids:
        raise ValueError(
            f"Output node '{output_node_id}' not found in graph. Available nodes: {sorted(all_ids)}"
        )

    needed, kept_edge_ids = _live_ancestry(graph, output_node_id)

    kept_nodes = [n for n in graph.nodes if n.id in needed]
    kept_edges = [e for e in graph.edges if e.id in kept_edge_ids]
    removed_ids = sorted(all_ids - needed)

    pruned_graph = graph.model_copy(update={"nodes": kept_nodes, "edges": kept_edges})

    return pruned_graph, sorted(needed), removed_ids


def find_output_node(graph: PipelineGraph) -> str:
    """Find the single output node in a graph.

    Looks for a response node (a Quote Response or Workbench Output, see
    ``RESPONSE_NODE_TYPES``) or a node with ``config.output=True``.

    Raises:
        ValueError: If zero or multiple output nodes are found.
    """
    seen: set[str] = set()
    candidates: list[str] = []
    for n in graph.nodes:
        if n.id in seen:
            continue
        if n.data.nodeType in RESPONSE_NODE_TYPES or n.data.config.get("output"):
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
    """Find the request inputs (Quote and Workbench Inputs) in a graph.

    Returns:
        List of node IDs (may be empty if none are marked).
    """
    return [n.id for n in graph.nodes if n.data.nodeType in REQUEST_INPUT_NODE_TYPES]


def find_source_nodes(graph: PipelineGraph) -> list[str]:
    """Find all source nodes in a graph (dataInput, the request inputs, constant)."""
    return [
        n.id
        for n in graph.nodes
        if n.data.nodeType in REQUEST_INPUT_NODE_TYPES
        or n.data.nodeType in (NodeType.DATA_INPUT, NodeType.CONSTANT)
    ]
