"""Pure graph operations for submodel create/dissolve — no I/O or HTTP."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from haute._editor_identities import NamingCandidate, name_candidates
from haute._graph_shape import SINGLETON_NODE_GROUPS
from haute._graph_utils import _edge_id, edge_input_name
from haute._submodel_instances import rewrite_boundary_input_names
from haute._support_code_names import UtilityReader
from haute._types import (
    SINK_ONLY_NODE_TYPES,
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


def _boundary_input_name(
    edge: GraphEdge,
    source: GraphNode,
    submodels: dict[str, SubmodelDefinition] | None,
) -> str:
    """Return the executable name a boundary edge carries, or refuse creation."""
    try:
        return edge_input_name(edge, source, submodels=submodels)
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
    *,
    reserved_output: SubmodelOutputPort | None = None,
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

    allocated_port_names = {reserved_output.name} if reserved_output else set()

    def _mint_port_name(base: str) -> str:
        candidate = base
        suffix = 2
        while candidate in allocated_port_names:
            candidate = f"{base}_{suffix}"
            suffix += 1
        allocated_port_names.add(candidate)
        return candidate

    input_ports: list[SubmodelInputPort] = []
    input_names: dict[tuple[str, str | None], str] = {}
    for identity, edges in input_groups.items():
        source = node_map[edges[0].source]
        base = _boundary_input_name(edges[0], source, submodels)
        port_name = _mint_port_name(base)
        input_names[identity] = port_name
        input_ports.append(
            SubmodelInputPort(
                name=port_name,
                targets=[
                    SubmodelEndpoint(nodeId=edge.target, handleId=edge.targetHandle)
                    for edge in edges
                ],
            )
        )

    output_ports = [reserved_output] if reserved_output else []
    output_names = (
        {(reserved_output.source.node_id, reserved_output.source.handle_id): reserved_output.name}
        if reserved_output
        else {}
    )
    for identity, edges in output_groups.items():
        if identity in output_names:
            continue
        edge = edges[0]
        source = node_map[edge.source]
        base = _boundary_input_name(edge, source, submodels)
        port_name = _mint_port_name(base)
        output_names[identity] = port_name
        output_ports.append(
            SubmodelOutputPort(
                name=port_name,
                source=SubmodelEndpoint(nodeId=edge.source, handleId=edge.sourceHandle),
            )
        )
    return input_ports, output_ports, input_names, output_names


def _rewire_canonical_boundary_edges(
    cross_edges: list[GraphEdge],
    *,
    instance_id: str,
    child_node_ids: set[str],
    input_names: dict[tuple[str, str | None], str],
    output_names: dict[tuple[str, str | None], str],
) -> list[GraphEdge]:
    rewired: list[GraphEdge] = []
    seen_input_bindings: set[tuple[str, str | None]] = set()
    for edge in cross_edges:
        if edge.target in child_node_ids:
            identity = (edge.source, edge.sourceHandle)
            if identity in seen_input_bindings:
                continue
            seen_input_bindings.add(identity)
            target_handle = f"in__{input_names[identity]}"
            rewired.append(
                GraphEdge(
                    id=_edge_id(edge.source, instance_id, edge.sourceHandle, target_handle),
                    source=edge.source,
                    target=instance_id,
                    sourceHandle=edge.sourceHandle,
                    targetHandle=target_handle,
                )
            )
        else:
            source_handle = f"out__{output_names[(edge.source, edge.sourceHandle)]}"
            rewired.append(
                GraphEdge(
                    id=_edge_id(instance_id, edge.target, source_handle, edge.targetHandle),
                    source=instance_id,
                    target=edge.target,
                    sourceHandle=source_handle,
                    targetHandle=edge.targetHandle,
                )
            )
    return rewired


def _preserve_consumer_input_names(
    parent_nodes: list[GraphNode],
    cross_edges: list[GraphEdge],
    *,
    child_node_ids: set[str],
    node_map: dict[str, GraphNode],
    occurrence: GraphNode,
    output_names: dict[tuple[str, str | None], str],
    submodels_before: dict[str, SubmodelDefinition] | None,
    submodels_after: dict[str, SubmodelDefinition],
) -> list[GraphNode]:
    """Keep every remaining consumer bound to the frame it read before grouping.

    A consumer of a grouped node used that node's own name as its input; after
    grouping the same frame arrives under its public output port name. If the
    physical name changes, the authored code does not: record the old name as
    the logical name through ``inputMapping`` and rewrite schema-owned
    selectors, exactly as flattening does across the same boundary.
    """
    renames: dict[str, dict[str, str]] = {}
    for edge in cross_edges:
        if edge.source not in child_node_ids or edge.target in child_node_ids:
            continue
        old_name = edge_input_name(edge, node_map[edge.source], submodels=submodels_before)
        rewired = GraphEdge(
            id=edge.id,
            source=occurrence.id,
            target=edge.target,
            sourceHandle=f"out__{output_names[(edge.source, edge.sourceHandle)]}",
            targetHandle=edge.targetHandle,
        )
        new_name = edge_input_name(rewired, occurrence, submodels=submodels_after)
        if old_name != new_name:
            renames.setdefault(edge.target, {})[old_name] = new_name
    if not renames:
        return parent_nodes
    return rewrite_boundary_input_names(parent_nodes, renames)


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
    instance_id = sm_name
    existing_submodel_names = {existing.casefold() for existing in existing_submodels}
    existing_parent_node_ids = {node.id.casefold() for node in parent_nodes}
    if sm_name.casefold() in existing_submodel_names:
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
    input_ports, output_ports, input_names, output_names = _build_public_interface(
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
        input_names=input_names,
        output_names=output_names,
    )

    # Assemble new parent graph
    existing_submodels[sm_name] = definition
    parent_nodes = _preserve_consumer_input_names(
        parent_nodes,
        cross_edges,
        child_node_ids=child_node_id_set,
        node_map=graph.node_map,
        occurrence=sm_node,
        output_names=output_names,
        submodels_before=graph.submodels,
        submodels_after=existing_submodels,
    )
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


@dataclass
class NodeInstanceGraphResult:
    graph: PipelineGraph
    sm_file: str
    definition_id: str
    owner_name: str
    instance_name: str


def instance_source_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Validate the parent-view selection before allocating any names."""
    if any(node.data.nodeType == NodeType.SUBMODEL_PORT for node in graph.nodes):
        raise SubmodelValidationError(
            code="drilled_definition",
            status_code=400,
            detail="Return to the parent pipeline before creating an instance of a node.",
        )
    node = graph.node_map.get(node_id)
    if node is None:
        raise SubmodelValidationError(
            code="missing_node",
            status_code=400,
            detail=f"Node {node_id!r} is not in the parent graph. Reload and select a node.",
        )
    if any(
        node.data.nodeType in group for group, _ in SINGLETON_NODE_GROUPS
    ) or node.data.nodeType in {
        NodeType.SUBMODEL,
        NodeType.SUBMODEL_PORT,
        NodeType.LIVE_SWITCH,
        NodeType.DATA_OUTPUT,
    }:
        raise SubmodelValidationError(
            code="unsupported_instance",
            status_code=400,
            detail=(
                f"Cannot wrap {node.data.label!r}: "
                f"{node.data.nodeType.value} does not support a node instance."
            ),
        )
    from haute._config_validation import reject_removed_config_keys

    reject_removed_config_keys(node.data.nodeType, node.data.config)
    return node


def create_node_instance_graph(
    graph: PipelineGraph,
    node_id: str,
    definition_name: str,
    *,
    read_utility: UtilityReader,
) -> NodeInstanceGraphResult:
    """Wrap a plain node and create an unbound occurrence, without file I/O."""
    node = instance_source_node(graph, node_id)
    owner_name = _sanitize_func_name(node.data.label)
    sm_file = f"modules/{definition_name}.py"
    cross_edges = [edge for edge in graph.edges if node_id in (edge.source, edge.target)]
    public_output = (
        None
        if node.data.nodeType in SINK_ONLY_NODE_TYPES
        else SubmodelOutputPort(
            name=owner_name,
            source=SubmodelEndpoint(nodeId=node_id),
        )
    )
    inputs, outputs, input_names, output_names = _build_public_interface(
        cross_edges,
        {node_id},
        graph.node_map,
        graph.submodels,
        reserved_output=public_output,
    )
    renames = {
        _boundary_input_name(edge, graph.node_map[edge.source], graph.submodels): input_names[
            (edge.source, edge.sourceHandle)
        ]
        for edge in cross_edges
        if edge.target == node_id
    }
    (child,) = rewrite_boundary_input_names([node], {node_id: renames})
    child = child.model_copy(deep=True, update={"position": {"x": 0.0, "y": 0.0}})
    definition = SubmodelDefinition(
        definitionId=definition_name,
        file=sm_file,
        graph=PipelineGraph(
            nodes=[child],
            edges=[],
            pipeline_name=definition_name,
            source_file=sm_file,
            preamble=graph.preamble,
            preserved_blocks=list(graph.preserved_blocks),
        ),
        inputPorts=inputs,
        outputPorts=outputs,
    )
    owner = GraphNode(
        id=owner_name,
        type="submodel",
        position=dict(node.position),
        data=NodeData(
            label=owner_name,
            nodeType=NodeType.SUBMODEL,
            config={"definitionId": definition_name, "alias": owner_name},
        ),
    )
    definitions = {**(graph.submodels or {}), definition_name: definition}
    parent_nodes = [owner if n.id == node_id else n for n in graph.nodes]
    rewired = _rewire_canonical_boundary_edges(
        cross_edges,
        instance_id=owner_name,
        child_node_ids={node_id},
        input_names=input_names,
        output_names=output_names,
    )
    result = graph.model_copy(
        update={
            "nodes": parent_nodes,
            "edges": [e for e in graph.edges if e not in cross_edges] + rewired,
            "submodels": definitions,
        }
    )
    names, _ = name_candidates(
        result,
        [
            NamingCandidate(
                node_id=node_id,
                label=f"{owner_name}_node",
                node_type=node.data.nodeType,
                alias=None,
                submodel=definition_name,
            )
        ],
        read_utility,
        allocate=True,
    )
    child_name = _sanitize_func_name(names[0].label)
    child = child.model_copy(
        update={
            "id": child_name,
            "data": child.data.model_copy(update={"label": child_name}),
        }
    )
    definition = definition.model_copy(
        update={
            "graph": definition.graph.model_copy(update={"nodes": [child]}),
            "input_ports": [
                port.model_copy(
                    update={
                        "targets": [
                            target.model_copy(update={"node_id": child_name})
                            for target in port.targets
                        ]
                    }
                )
                for port in inputs
            ],
            "output_ports": [
                port.model_copy(
                    update={
                        "source": port.source.model_copy(update={"node_id": child_name}),
                    }
                )
                for port in outputs
            ],
        }
    )
    result = result.model_copy(update={"submodels": {**definitions, definition_name: definition}})
    # Match the browser's copy numbering, including copies numbered above nine.
    base = re.sub(r"_([2-9]|[1-9]\d+)$", "", owner_name)
    names, _ = name_candidates(
        result,
        [
            NamingCandidate(
                node_id=uuid4().hex,
                label=base,
                node_type=NodeType.SUBMODEL,
                alias=base,
            )
        ],
        read_utility,
        allocate=True,
    )
    instance_name = names[0].label
    instance = GraphNode(
        id=instance_name,
        type="submodel",
        position={"x": node.position["x"] + 60, "y": node.position["y"] + 80},
        data=NodeData(
            label=instance_name,
            nodeType=NodeType.SUBMODEL,
            config={
                "definitionId": definition_name,
                "alias": instance_name,
                "instanceOf": owner_name,
            },
        ),
    )
    return NodeInstanceGraphResult(
        graph=result.model_copy(update={"nodes": [*result.nodes, instance]}),
        sm_file=sm_file,
        definition_id=definition_name,
        owner_name=owner_name,
        instance_name=instance_name,
    )
