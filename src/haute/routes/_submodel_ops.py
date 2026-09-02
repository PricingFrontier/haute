"""Pure graph operations for submodel create/dissolve — no I/O or HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn
from uuid import uuid4

from pydantic import ValidationError

from haute._graph_utils import _edge_id, edge_input_name
from haute._submodel_instances import (
    canonical_downstream_identity,
    rewrite_input_selectors,
)
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


def _build_public_interface(
    cross_edges: list[GraphEdge],
    child_node_ids: set[str],
    node_map: dict[str, GraphNode],
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
        label = edges[0].sourceHandle or source.data.label
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
        label = edge.sourceHandle or source.data.label
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


def _mapping_error(*, code: str, node_id: str, field: str, detail: str) -> NoReturn:
    raise SubmodelValidationError(
        code=code,
        status_code=400,
        detail=(
            f"Cannot create submodel: node {node_id!r} has an invalid {field!r} mapping ({detail})."
        ),
    )


def _rewrite_mapping_keys(
    value: object,
    renames: dict[str, str],
    *,
    node_id: str,
    field: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        _mapping_error(
            code="invalid_input_mapping",
            node_id=node_id,
            field=field,
            detail="expected an object",
        )

    rewritten: dict[str, object] = {}
    for key, mapping_value in value.items():
        if not isinstance(key, str):
            _mapping_error(
                code="invalid_input_mapping",
                node_id=node_id,
                field=field,
                detail="all keys must be strings",
            )
        rewritten_key = renames.get(key, key)
        if rewritten_key in rewritten:
            _mapping_error(
                code="input_mapping_collision",
                node_id=node_id,
                field=field,
                detail=f"renaming {key!r} would duplicate {rewritten_key!r}",
            )
        rewritten[rewritten_key] = mapping_value
    return rewritten


def _rewrite_mapping_values(
    value: object,
    renames: dict[str, str],
    *,
    node_id: str,
    field: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        _mapping_error(
            code="invalid_input_mapping",
            node_id=node_id,
            field=field,
            detail="expected an object",
        )

    rewritten: dict[str, object] = {}
    for key, mapping_value in value.items():
        if not isinstance(key, str):
            _mapping_error(
                code="invalid_input_mapping",
                node_id=node_id,
                field=field,
                detail="all keys must be strings",
            )
        rewritten[key] = (
            renames.get(mapping_value, mapping_value)
            if isinstance(mapping_value, str)
            else mapping_value
        )
    return rewritten


def _normalise_public_input_config_names(
    child_nodes: list[GraphNode],
    cross_edges: list[GraphEdge],
    *,
    child_node_ids: set[str],
    input_ids: dict[tuple[str, str | None], str],
    node_map: dict[str, GraphNode],
) -> list[GraphNode]:
    """Replace external input names with canonical public-port parameter names."""
    renames_by_target: dict[str, dict[str, str]] = {}
    for edge in cross_edges:
        if edge.target not in child_node_ids:
            continue
        try:
            old_name = edge_input_name(edge, node_map[edge.source])
        except ValueError as exc:
            raise SubmodelValidationError(
                code="invalid_input_binding",
                status_code=400,
                detail=(
                    f"Cannot create submodel: input edge {edge.id!r} "
                    "does not identify an executable source frame."
                ),
            ) from exc
        new_name = _sanitize_func_name(input_ids[(edge.source, edge.sourceHandle)])
        target_renames = renames_by_target.setdefault(edge.target, {})
        previous = target_renames.get(old_name)
        if previous is not None and previous != new_name:
            _mapping_error(
                code="input_mapping_collision",
                node_id=edge.target,
                field="public input",
                detail=(f"input name {old_name!r} maps to both {previous!r} and {new_name!r}"),
            )
        target_renames[old_name] = new_name

    normalised: list[GraphNode] = []
    for node in child_nodes:
        config = dict(node.data.config)
        changed = False
        direct_renames = renames_by_target.get(node.id)
        if direct_renames:
            rewritten_node = rewrite_input_selectors([node], {node.id: direct_renames})[0]
            rewritten_config = dict(rewritten_node.data.config)
            changed = changed or rewritten_config != config
            config = rewritten_config
            if "input_scenario_map" in config:
                rewritten = _rewrite_mapping_keys(
                    config["input_scenario_map"],
                    direct_renames,
                    node_id=node.id,
                    field="input_scenario_map",
                )
                changed = changed or rewritten != config["input_scenario_map"]
                config["input_scenario_map"] = rewritten
            if "inputMapping" in config:
                rewritten = _rewrite_mapping_values(
                    config["inputMapping"],
                    direct_renames,
                    node_id=node.id,
                    field="inputMapping",
                )
                changed = changed or rewritten != config["inputMapping"]
                config["inputMapping"] = rewritten

        original_id = config.get("instanceOf")
        original_renames = (
            renames_by_target.get(original_id) if isinstance(original_id, str) else None
        )
        if original_renames and "inputMapping" in config:
            rewritten = _rewrite_mapping_keys(
                config["inputMapping"],
                original_renames,
                node_id=node.id,
                field="inputMapping",
            )
            changed = changed or rewritten != config["inputMapping"]
            config["inputMapping"] = rewritten

        if not changed:
            normalised.append(node)
            continue
        normalised.append(
            node.model_copy(
                deep=True,
                update={
                    "data": node.data.model_copy(
                        deep=True,
                        update={"config": config},
                    )
                },
            )
        )
    return normalised


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
    )
    parent_reference_maps: dict[str, dict[str, str]] = {}
    for edge in cross_edges:
        if edge.source not in child_node_id_set:
            continue
        port_id = output_ids[(edge.source, edge.sourceHandle)]
        target_map = parent_reference_maps.setdefault(edge.target, {})
        identity = canonical_downstream_identity(sm_name, port_id)
        old_name = edge_input_name(edge, graph.node_map[edge.source])
        previous = target_map.get(old_name)
        if previous is not None and previous != identity:
            raise SubmodelValidationError(
                code="boundary_reference_collision",
                status_code=400,
                detail=(
                    "Cannot create submodel: parent node "
                    f"{edge.target!r} would map input {old_name!r} to both "
                    f"{previous!r} and {identity!r}."
                ),
            )
        target_map[old_name] = identity
    parent_nodes = rewrite_input_selectors(parent_nodes, parent_reference_maps)
    local_child_nodes = _normalise_public_input_config_names(
        local_child_nodes,
        cross_edges,
        child_node_ids=child_node_id_set,
        input_ids=input_ids,
        node_map=graph.node_map,
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
