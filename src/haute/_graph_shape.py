"""Graph-shape contracts shared by parser, codegen, and execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from haute._submodel_instances import resolve_submodel_instances
from haute._types import NodeType, PipelineGraph
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
    for definition_id, child_graph, incoming, outgoing in _iter_submodel_definition_contexts(
        graph,
        instance_ids=validate_node_ids,
    ):
        validate_graph_shape_contracts(
            child_graph,
            graph_label=f"{graph_label}:definition:{definition_id}",
            extra_incoming_by_node=incoming,
            extra_outgoing_by_node=outgoing,
        )


def _iter_submodel_definition_contexts(
    graph: PipelineGraph,
    *,
    instance_ids: set[str] | None,
) -> list[tuple[str, PipelineGraph, dict[str, list[str]], dict[str, list[str]]]]:
    """Return each referenced definition once with its declared boundary topology."""
    contexts: list[tuple[str, PipelineGraph, dict[str, list[str]], dict[str, list[str]]]] = []
    seen_definitions: set[str] = set()
    for instance_id, instance in resolve_submodel_instances(graph).items():
        if instance_ids is not None and instance_id not in instance_ids:
            continue
        definition_id = instance.config.definition_id
        if definition_id in seen_definitions:
            continue
        seen_definitions.add(definition_id)

        incoming: dict[str, list[str]] = {}
        outgoing: dict[str, list[str]] = {}
        for input_port in instance.definition.input_ports:
            public_source = f"public:{input_port.name}"
            for target in input_port.targets:
                incoming.setdefault(target.node_id, []).append(public_source)
        for output_port in instance.definition.output_ports:
            outgoing.setdefault(output_port.source.node_id, []).append(f"public:{output_port.name}")
        contexts.append(
            (
                definition_id,
                instance.definition.graph,
                incoming,
                outgoing,
            )
        )
    return contexts
