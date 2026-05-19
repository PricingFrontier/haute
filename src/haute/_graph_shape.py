"""Graph-shape contracts shared by parser, codegen, and execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from haute._types import GraphNode, NodeType, PipelineGraph
from haute.errors import ParseError


def validate_graph_shape_contracts(
    graph: PipelineGraph,
    *,
    graph_label: str = "graph",
    extra_incoming_by_node: Mapping[str, Sequence[str]] | None = None,
    extra_outgoing_by_node: Mapping[str, Sequence[str]] | None = None,
    node_ids_to_validate: Iterable[str] | None = None,
) -> None:
    """Fail loudly when node topology violates backend node contracts."""

    validate_node_ids = set(node_ids_to_validate) if node_ids_to_validate is not None else None
    incoming: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.target in incoming:
            incoming[edge.target].append(edge.source)
        if edge.source in outgoing:
            outgoing[edge.source].append(edge.target)
    for node_id, sources in (extra_incoming_by_node or {}).items():
        if node_id in incoming:
            incoming[node_id].extend(sources)
    for node_id, targets in (extra_outgoing_by_node or {}).items():
        if node_id in outgoing:
            outgoing[node_id].extend(targets)

    for node in graph.nodes:
        if validate_node_ids is not None and node.id not in validate_node_ids:
            continue
        if node.data.nodeType != NodeType.EXPLORE:
            continue

        incoming_sources = incoming.get(node.id, [])
        if len(incoming_sources) != 1:
            raise ParseError(
                "Explore nodes must have exactly one incoming edge.",
                graph=graph_label,
                node_id=node.id,
                node_label=node.data.label,
                incoming_count=len(incoming_sources),
                incoming_sources=incoming_sources,
            )

        outgoing_targets = outgoing.get(node.id, [])
        if outgoing_targets:
            raise ParseError(
                "Explore nodes cannot have outgoing edges.",
                graph=graph_label,
                node_id=node.id,
                node_label=node.data.label,
                outgoing_count=len(outgoing_targets),
                outgoing_targets=outgoing_targets,
            )


def validate_pipeline_graph_shape_contracts(
    graph: PipelineGraph,
    *,
    graph_label: str = "graph",
    node_ids_to_validate: Iterable[str] | None = None,
) -> None:
    """Validate a graph plus any collapsed submodel child graphs."""

    validate_node_ids = set(node_ids_to_validate) if node_ids_to_validate is not None else None
    validate_graph_shape_contracts(
        graph,
        graph_label=graph_label,
        node_ids_to_validate=validate_node_ids,
    )
    for submodel_name, child_graph, incoming, outgoing in _iter_submodel_child_contexts(graph):
        if validate_node_ids is not None and f"submodel__{submodel_name}" not in validate_node_ids:
            continue
        validate_graph_shape_contracts(
            child_graph,
            graph_label=f"{graph_label}:{submodel_name}",
            extra_incoming_by_node=incoming,
            extra_outgoing_by_node=outgoing,
        )


def _iter_submodel_child_contexts(
    graph: PipelineGraph,
) -> list[tuple[str, PipelineGraph, dict[str, list[str]], dict[str, list[str]]]]:
    submodels = graph.submodels or {}
    contexts: list[tuple[str, PipelineGraph, dict[str, list[str]], dict[str, list[str]]]] = []
    for submodel_name, metadata in submodels.items():
        if not isinstance(metadata, dict):
            continue
        child_raw = metadata.get("graph")
        if child_raw is None:
            continue
        child_graph = (
            child_raw
            if isinstance(child_raw, PipelineGraph)
            else PipelineGraph.model_validate(child_raw)
        )
        child_nodes = set(_node_ids(child_graph.nodes))
        incoming: dict[str, list[str]] = {}
        outgoing: dict[str, list[str]] = {}
        submodel_node_id = f"submodel__{submodel_name}"
        for edge in graph.edges:
            if edge.target == submodel_node_id:
                child_id = _boundary_child_id(edge.targetHandle, prefix="in__")
                if child_id in child_nodes:
                    incoming.setdefault(child_id, []).append(edge.source)
            if edge.source == submodel_node_id:
                child_id = _boundary_child_id(edge.sourceHandle, prefix="out__")
                if child_id in child_nodes:
                    outgoing.setdefault(child_id, []).append(edge.target)
        contexts.append((submodel_name, child_graph, incoming, outgoing))
    return contexts


def _node_ids(nodes: Sequence[GraphNode]) -> list[str]:
    return [node.id for node in nodes]


def _boundary_child_id(handle: str | None, *, prefix: str) -> str | None:
    if not handle or not handle.startswith(prefix):
        return None
    return handle[len(prefix) :]
