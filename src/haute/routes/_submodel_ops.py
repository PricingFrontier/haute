"""Pure graph operations for submodel create/dissolve — no I/O or HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from haute._graph_utils import _edge_id, edge_input_label
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelInputPort,
    SubmodelInstanceConfig,
    SubmodelOutputPort,
)
from haute.graph_utils import _sanitize_func_name


class SubmodelValidationError(ValueError):
    """Stable, user-safe validation failure for a submodel graph mutation."""

    def __init__(self, *, code: str, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.status_code = status_code
        self.detail = detail


@dataclass
class SubmodelGraphResult:
    """Result of ``create_submodel_graph`` — everything the caller needs."""

    graph: PipelineGraph
    sm_file: str
    sm_name: str


def _public_frame_label(
    edge: GraphEdge,
    source: GraphNode,
    submodels: dict[str, SubmodelDefinition] | None,
) -> str:
    """Return the public label that preserves this edge's executable name."""
    try:
        return edge_input_label(edge, source, submodels=submodels)
    except ValueError as exc:
        raise SubmodelValidationError(
            code="invalid_input_binding",
            status_code=400,
            detail=(
                f"Cannot create submodel: boundary edge {edge.id!r} "
                "does not identify an executable source frame."
            ),
        ) from exc


def _build_public_interface(
    cross_edges: list[GraphEdge],
    child_node_ids: set[str],
    node_map: dict[str, GraphNode],
    submodels: dict[str, SubmodelDefinition] | None,
) -> tuple[
    list[SubmodelInputPort],
    list[SubmodelOutputPort],
    dict[tuple[str, str | None], str],
    dict[tuple[str, str | None], str],
]:
    """Build stable public ports without exposing internal child identities."""
    input_groups: dict[tuple[str, str | None], list[GraphEdge]] = {}
    output_groups: dict[tuple[str, str | None], list[GraphEdge]] = {}
    for edge in cross_edges:
        if edge.target in child_node_ids:
            input_groups.setdefault((edge.source, edge.sourceHandle), []).append(edge)
        else:
            output_groups.setdefault((edge.source, edge.sourceHandle), []).append(edge)

    input_ports: list[SubmodelInputPort] = []
    input_ids: dict[tuple[str, str | None], str] = {}
    for index, (identity, edges) in enumerate(input_groups.items(), start=1):
        port_id = f"input_{index}"
        input_ids[identity] = port_id
        source = node_map[edges[0].source]
        label = _public_frame_label(edges[0], source, submodels)
        input_ports.append(
            SubmodelInputPort(
                portId=port_id,
                label=label,
                targets=[
                    SubmodelEndpoint(
                        nodeId=edge.target,
                        handleId=(
                            edge.targetHandle if edge.targetHandle is not None else edge.targetPort
                        ),
                    )
                    for edge in edges
                ],
            )
        )

    output_ports: list[SubmodelOutputPort] = []
    output_ids: dict[tuple[str, str | None], str] = {}
    for index, (identity, edges) in enumerate(output_groups.items(), start=1):
        port_id = f"output_{index}"
        output_ids[identity] = port_id
        edge = edges[0]
        source = node_map[edge.source]
        label = _public_frame_label(edge, source, submodels)
        output_ports.append(
            SubmodelOutputPort(
                portId=port_id,
                label=label,
                source=SubmodelEndpoint(
                    nodeId=edge.source,
                    handleId=(
                        edge.sourceHandle if edge.sourceHandle is not None else edge.sourcePort
                    ),
                ),
            )
        )
    return input_ports, output_ports, input_ids, output_ids


def _rewire_canonical_boundary_edges(
    cross_edges: list[GraphEdge],
    *,
    instance_id: str,
    child_node_ids: set[str],
    input_ids: dict[tuple[str, str | None], str],
    output_ids: dict[tuple[str, str | None], str],
) -> list[GraphEdge]:
    rewired: list[GraphEdge] = []
    seen_input_bindings: set[tuple[str, str | None]] = set()
    for edge in cross_edges:
        if edge.target in child_node_ids:
            identity = (edge.source, edge.sourceHandle)
            if identity in seen_input_bindings:
                continue
            seen_input_bindings.add(identity)
            target_handle = f"in__{input_ids[identity]}"
            rewired.append(
                GraphEdge(
                    id=_edge_id(
                        edge.source,
                        instance_id,
                        edge.sourceHandle,
                        target_handle,
                        hidden_source_port=edge.sourcePort,
                    ),
                    source=edge.source,
                    target=instance_id,
                    sourceHandle=edge.sourceHandle,
                    targetHandle=target_handle,
                    sourcePort=edge.sourcePort,
                )
            )
        else:
            source_handle = f"out__{output_ids[(edge.source, edge.sourceHandle)]}"
            rewired.append(
                GraphEdge(
                    id=_edge_id(
                        instance_id,
                        edge.target,
                        source_handle,
                        edge.targetHandle,
                        hidden_target_port=edge.targetPort,
                    ),
                    source=instance_id,
                    target=edge.target,
                    sourceHandle=source_handle,
                    targetHandle=edge.targetHandle,
                    targetPort=edge.targetPort,
                )
            )
    return rewired


def create_submodel_graph(
    graph: PipelineGraph,
    node_ids: list[str],
    name: str,
) -> SubmodelGraphResult:
    """Split *node_ids* out of *graph* into a submodel named *name*.

    Returns a ``SubmodelGraphResult`` containing the new parent graph
    (with a placeholder submodel node and rewired edges) plus metadata.

    Raises ``SubmodelValidationError`` when the name or selection is invalid.
    """
    trimmed_name = name.strip()
    if not trimmed_name:
        raise SubmodelValidationError(
            code="blank_name",
            status_code=400,
            detail="Submodel name must not be blank.",
        )
    sm_name = _sanitize_func_name(trimmed_name)
    sm_file = f"modules/{sm_name}.py"
    if len(node_ids) != len(set(node_ids)):
        raise SubmodelValidationError(
            code="duplicate_selection",
            status_code=400,
            detail="Submodel selection contains duplicate node IDs.",
        )
    selected_ids = set(node_ids)

    nodes = graph.nodes
    edges = graph.edges
    graph_node_ids = {node.id for node in nodes}
    missing_ids = selected_ids - graph_node_ids
    if missing_ids:
        raise SubmodelValidationError(
            code="stale_selection",
            status_code=409,
            detail="The selected nodes changed on disk. Reload the pipeline and try again.",
        )

    # Separate child vs parent nodes
    child_nodes = [n for n in nodes if n.id in selected_ids]
    parent_nodes = [n for n in nodes if n.id not in selected_ids]
    child_node_ids = [n.id for n in child_nodes]
    child_node_id_set = set(child_node_ids)

    # Reject nesting: selected nodes must not include submodel nodes
    for n in child_nodes:
        if n.data.nodeType == NodeType.SUBMODEL:
            raise SubmodelValidationError(
                code="nested_submodel",
                status_code=400,
                detail="Submodels cannot be nested inside other submodels.",
            )

    if len(child_node_ids) < 2:
        raise SubmodelValidationError(
            code="too_few_nodes",
            status_code=400,
            detail="A submodel must contain at least 2 nodes.",
        )

    existing_submodels = dict(graph.submodels or {})
    existing_occurrence_aliases: set[str] = set()
    for node in graph.nodes:
        if node.data.nodeType != NodeType.SUBMODEL:
            continue
        try:
            occurrence = SubmodelInstanceConfig.model_validate(node.data.config)
        except ValidationError as exc:
            raise SubmodelValidationError(
                code="invalid_submodel_instance",
                status_code=400,
                detail=f"Submodel occurrence {node.id!r} has an invalid canonical config: {exc}",
            ) from exc
        existing_occurrence_aliases.add(occurrence.alias.casefold())
    instance_id = f"submodel_instance_{uuid4().hex}"
    existing_submodel_names = {existing.casefold() for existing in existing_submodels}
    existing_parent_node_ids = {node.id.casefold() for node in parent_nodes}
    existing_node_ids = {node_id.casefold() for node_id in graph_node_ids}
    if sm_name.casefold() in existing_submodel_names or instance_id.casefold() in existing_node_ids:
        raise SubmodelValidationError(
            code="submodel_exists",
            status_code=409,
            detail=f"Submodel {sm_name!r} already exists.",
        )
    if sm_name.casefold() in existing_parent_node_ids:
        raise SubmodelValidationError(
            code="alias_conflict",
            status_code=409,
            detail=f"Submodel alias {sm_name!r} conflicts with a parent node id.",
        )
    if sm_name.casefold() in existing_occurrence_aliases:
        raise SubmodelValidationError(
            code="alias_conflict",
            status_code=409,
            detail=f"Submodel alias {sm_name!r} conflicts with an existing submodel occurrence.",
        )

    # Classify edges
    internal_edges = [
        e for e in edges if e.source in child_node_id_set and e.target in child_node_id_set
    ]
    cross_edges = [
        e for e in edges if (e.source in child_node_id_set) != (e.target in child_node_id_set)
    ]
    external_edges = [
        e for e in edges if e.source not in child_node_id_set and e.target not in child_node_id_set
    ]

    xs = [node.position["x"] for node in child_nodes]
    ys = [node.position["y"] for node in child_nodes]
    placeholder_position = {
        "x": (min(xs) + max(xs)) / 2,
        "y": (min(ys) + max(ys)) / 2,
    }
    local_child_nodes = [
        node.model_copy(
            deep=True,
            update={
                "position": {
                    "x": node.position["x"] - placeholder_position["x"],
                    "y": node.position["y"] - placeholder_position["y"],
                }
            },
        )
        for node in child_nodes
    ]
    input_ports, output_ports, input_ids, output_ids = _build_public_interface(
        cross_edges,
        child_node_id_set,
        graph.node_map,
        graph.submodels,
    )
    sm_graph = PipelineGraph(
        nodes=local_child_nodes,
        edges=internal_edges,
        pipeline_name=sm_name,
        pipeline_description="",
        preamble=graph.preamble or "",
        preserved_blocks=list(graph.preserved_blocks),
        source_file=sm_file,
    )

    definition = SubmodelDefinition.model_validate(
        {
            "definitionId": sm_name,
            "file": sm_file,
            "graph": sm_graph,
            "inputPorts": input_ports,
            "outputPorts": output_ports,
        }
    )
    sm_node = GraphNode(
        id=instance_id,
        type="submodel",
        position=placeholder_position,
        data=NodeData(
            label=sm_name,
            nodeType=NodeType.SUBMODEL,
            config=SubmodelInstanceConfig(
                definitionId=sm_name,
                alias=sm_name,
            ).model_dump(by_alias=True),
        ),
    )
    sm_node_id = sm_node.id

    rewired_cross = _rewire_canonical_boundary_edges(
        cross_edges,
        instance_id=sm_node_id,
        child_node_ids=child_node_id_set,
        input_ids=input_ids,
        output_ids=output_ids,
    )

    # Assemble new parent graph
    existing_submodels[sm_name] = definition
    new_graph = graph.model_copy(
        update={
            "nodes": parent_nodes + [sm_node],
            "edges": external_edges + rewired_cross,
            "submodels": existing_submodels,
        }
    )

    return SubmodelGraphResult(
        graph=new_graph,
        sm_file=sm_file,
        sm_name=sm_name,
    )
