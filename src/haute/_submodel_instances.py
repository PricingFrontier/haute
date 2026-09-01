"""Reusable submodel definitions and occurrence-level graph expansion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from haute._graph_utils import _edge_id, canonical_downstream_identity
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelInputPort,
    SubmodelInstanceConfig,
    SubmodelOutputPort,
)
from haute.errors import ParseError

_GLOBAL_NODE_REFERENCE_FIELDS: frozenset[str] = frozenset({"instanceOf"})
_NODE_REFERENCE_FIELDS: dict[NodeType, frozenset[str]] = {
    NodeType.EDGE_JOIN: frozenset({"baseInput", "joinInput"}),
    NodeType.OPTIMISER: frozenset(
        {
            "scored_input",
            "factors_input",
            "data_input",
            "banding_source",
        }
    ),
    NodeType.OPTIMISER_APPLY: frozenset({"ratebook_input"}),
}


@dataclass(frozen=True)
class ResolvedSubmodelInstance:
    """One validated parent occurrence and its shared definition."""

    node: GraphNode
    config: SubmodelInstanceConfig
    definition: SubmodelDefinition


def qualified_runtime_node_id(instance_id: str, local_node_id: str) -> str:
    """Return a deterministic, delimiter-safe runtime id for one cloned child."""
    if not instance_id or not local_node_id:
        raise ValueError("Runtime submodel ids require non-empty instance and local ids.")
    return f"submodel_runtime/{quote(instance_id, safe='')}/{quote(local_node_id, safe='')}"


def _definition_registry(graph: PipelineGraph) -> dict[str, SubmodelDefinition]:
    return dict(graph.submodels or {})


def _parse_instance_config(node: GraphNode) -> SubmodelInstanceConfig | None:
    if node.data.nodeType != NodeType.SUBMODEL:
        return None
    try:
        return SubmodelInstanceConfig.model_validate(node.data.config)
    except ValueError as exc:
        raise ParseError(
            "Submodel instance config is invalid.",
            instance_id=node.id,
        ) from exc


def resolve_submodel_instances(
    graph: PipelineGraph,
) -> dict[str, ResolvedSubmodelInstance]:
    """Resolve and validate every occurrence without mutating the graph."""
    definitions = _definition_registry(graph)
    node_ids = [node.id for node in graph.nodes]
    duplicate_node_ids = sorted(node_id for node_id in set(node_ids) if node_ids.count(node_id) > 1)
    if duplicate_node_ids:
        raise ParseError(
            "Pipeline graph contains duplicate node ids.",
            duplicate_node_ids=duplicate_node_ids,
        )

    resolved: dict[str, ResolvedSubmodelInstance] = {}
    aliases: dict[str, str] = {}
    for node in graph.nodes:
        config = _parse_instance_config(node)
        if config is None:
            continue
        definition = definitions.get(config.definition_id)
        if definition is None:
            raise ParseError(
                "Submodel instance references a definition that does not exist.",
                instance_id=node.id,
                definition_id=config.definition_id,
                known_definitions=sorted(definitions),
            )
        previous = aliases.get(config.alias)
        if previous is not None:
            raise ParseError(
                "Submodel instance alias is duplicated in the parent pipeline.",
                alias=config.alias,
                instance_ids=[previous, node.id],
            )
        aliases[config.alias] = node.id
        resolved[node.id] = ResolvedSubmodelInstance(
            node=node,
            config=config,
            definition=definition,
        )

    parent_node_ids = set(node_ids)
    alias_node_collisions = sorted(set(aliases) & parent_node_ids)
    if alias_node_collisions:
        raise ParseError(
            "Submodel instance alias collides with a parent node id.",
            aliases=alias_node_collisions,
        )

    instances_by_definition: dict[str, list[ResolvedSubmodelInstance]] = {}
    for instance in resolved.values():
        instances_by_definition.setdefault(instance.config.definition_id, []).append(instance)
    for definition_id, definition_instances in instances_by_definition.items():
        owners = [
            instance for instance in definition_instances if instance.config.instance_of is None
        ]
        if len(owners) != 1:
            raise ParseError(
                "Each submodel definition must have exactly one owner occurrence.",
                definition_id=definition_id,
                instance_ids=sorted(instance.node.id for instance in definition_instances),
                owner_instance_ids=sorted(instance.node.id for instance in owners),
            )
        owner_id = owners[0].node.id
        for instance in definition_instances:
            reference = instance.config.instance_of
            if reference is None:
                continue
            if reference == instance.node.id:
                raise ParseError(
                    "Submodel instance cannot reference itself as its owner.",
                    instance_id=instance.node.id,
                    definition_id=definition_id,
                )
            referenced = resolved.get(reference)
            if referenced is None:
                raise ParseError(
                    "Submodel instance references an owner occurrence that does not exist.",
                    instance_id=instance.node.id,
                    definition_id=definition_id,
                    instance_of=reference,
                )
            if referenced.config.definition_id != definition_id:
                raise ParseError(
                    "Submodel instance owner belongs to a different definition.",
                    instance_id=instance.node.id,
                    definition_id=definition_id,
                    instance_of=reference,
                    owner_definition_id=referenced.config.definition_id,
                )
            if referenced.config.instance_of is not None:
                raise ParseError(
                    "Submodel instance must reference the definition owner directly, "
                    "not another copy.",
                    instance_id=instance.node.id,
                    definition_id=definition_id,
                    instance_of=reference,
                    owner_instance_id=owner_id,
                )
            if reference != owner_id:
                raise ParseError(
                    "Submodel instance must reference its definition owner.",
                    instance_id=instance.node.id,
                    definition_id=definition_id,
                    instance_of=reference,
                    owner_instance_id=owner_id,
                )
    bound_input_ports: dict[tuple[str, str], str] = {}
    for edge in graph.edges:
        if edge.source in resolved:
            if edge.target not in parent_node_ids:
                raise ParseError(
                    "Submodel output binding targets a node outside the parent graph.",
                    edge_id=edge.id,
                    source=edge.source,
                    target=edge.target,
                )
            _output_port(resolved[edge.source], edge)
        if edge.target in resolved:
            if edge.source not in parent_node_ids:
                raise ParseError(
                    "Submodel input binding starts at a node outside the parent graph.",
                    edge_id=edge.id,
                    source=edge.source,
                    target=edge.target,
                )
            instance = resolved[edge.target]
            port = _input_port(instance, edge)
            binding_key = (edge.target, port.port_id)
            previous_edge_id = bound_input_ports.get(binding_key)
            if previous_edge_id is not None:
                raise ParseError(
                    "Submodel input port is bound more than once.",
                    instance_id=edge.target,
                    definition_id=instance.config.definition_id,
                    port_id=port.port_id,
                    edge_ids=[previous_edge_id, edge.id],
                )
            bound_input_ports[binding_key] = edge.id
    return resolved


def validate_submodel_instances(graph: PipelineGraph) -> None:
    """Fail loudly when definition, occurrence, or public binding state is invalid."""
    resolve_submodel_instances(graph)


def _port_id(
    *,
    edge: GraphEdge,
    handle: str | None,
    prefix: str,
    endpoint: str,
    instance: ResolvedSubmodelInstance,
) -> str:
    if not handle or not handle.startswith(prefix):
        raise ParseError(
            "Submodel boundary edge has a missing or malformed public-port handle.",
            edge_id=edge.id,
            endpoint=endpoint,
            handle=handle,
            expected=f"{prefix}<portId>",
            instance_id=instance.node.id,
            definition_id=instance.config.definition_id,
        )
    port_id = handle[len(prefix) :]
    if not port_id:
        raise ParseError(
            "Submodel boundary edge has an empty public port id.",
            edge_id=edge.id,
            endpoint=endpoint,
            instance_id=instance.node.id,
        )
    return port_id


def _input_port(
    instance: ResolvedSubmodelInstance,
    edge: GraphEdge,
) -> SubmodelInputPort:
    port_id = _port_id(
        edge=edge,
        handle=edge.targetHandle,
        prefix="in__",
        endpoint="target",
        instance=instance,
    )
    for port in instance.definition.input_ports:
        if port.port_id == port_id:
            return port

    raise ParseError(
        "Submodel input binding references an undeclared public port.",
        edge_id=edge.id,
        instance_id=instance.node.id,
        definition_id=instance.config.definition_id,
        port_id=port_id,
        known_ports=[port.port_id for port in instance.definition.input_ports],
    )


def _output_port(
    instance: ResolvedSubmodelInstance,
    edge: GraphEdge,
) -> SubmodelOutputPort:
    port_id = _port_id(
        edge=edge,
        handle=edge.sourceHandle,
        prefix="out__",
        endpoint="source",
        instance=instance,
    )
    for port in instance.definition.output_ports:
        if port.port_id == port_id:
            return port

    raise ParseError(
        "Submodel output binding references an undeclared public port.",
        edge_id=edge.id,
        instance_id=instance.node.id,
        definition_id=instance.config.definition_id,
        port_id=port_id,
        known_ports=[port.port_id for port in instance.definition.output_ports],
    )


def _node_reference_fields(node_type: NodeType) -> frozenset[str]:
    return _GLOBAL_NODE_REFERENCE_FIELDS | _NODE_REFERENCE_FIELDS.get(
        node_type,
        frozenset(),
    )


def rewrite_node_references(
    nodes: list[GraphNode],
    reference_map_by_node: dict[str, dict[str, str]],
) -> list[GraphNode]:
    """Rewrite only schema-declared node-reference config fields."""
    rewritten: list[GraphNode] = []
    for node in nodes:
        config = dict(node.data.config)
        changed = False
        references = reference_map_by_node.get(node.id, {})
        for field in _node_reference_fields(node.data.nodeType):
            reference = config.get(field)
            replacement = references.get(reference) if isinstance(reference, str) else None
            if replacement is not None:
                config[field] = replacement
                changed = True
        if not changed:
            rewritten.append(node)
            continue
        rewritten.append(
            node.model_copy(
                deep=True,
                update={
                    "data": node.data.model_copy(update={"config": config}),
                },
            )
        )
    return rewritten


def rewrite_submodel_alias_references(
    nodes: list[GraphNode],
    alias_to_instance_id: dict[str, str],
) -> list[GraphNode]:
    """Rewrite schema-declared parent config references from aliases to ids."""
    return rewrite_node_references(nodes, {node.id: alias_to_instance_id for node in nodes})


def _rewrite_config_references(
    node: GraphNode,
    *,
    instance: ResolvedSubmodelInstance,
    reference_map: dict[str, str],
) -> dict[str, Any]:
    config = dict(node.data.config)
    for field in _node_reference_fields(node.data.nodeType):
        if field not in config:
            continue
        raw_reference = config[field]
        if raw_reference is None or raw_reference == "":
            continue
        if not isinstance(raw_reference, str):
            raise ParseError(
                "Submodel node-id config reference must be a string.",
                instance_id=instance.node.id,
                definition_id=instance.config.definition_id,
                local_node_id=node.id,
                field=field,
                value=raw_reference,
            )
        qualified = reference_map.get(raw_reference)
        if qualified is None:
            raise ParseError(
                "Submodel config contains a stale declared node-id reference.",
                instance_id=instance.node.id,
                definition_id=instance.config.definition_id,
                local_node_id=node.id,
                field=field,
                reference=raw_reference,
                known_reference_ids=sorted(reference_map),
            )
        config[field] = qualified

    return config


def _clone_definition(
    instance: ResolvedSubmodelInstance,
    reference_map: dict[str, str],
) -> tuple[list[GraphNode], list[GraphEdge], dict[str, str]]:
    definition_graph = instance.definition.graph
    local_ids = [node.id for node in definition_graph.nodes]
    duplicates = sorted(node_id for node_id in set(local_ids) if local_ids.count(node_id) > 1)
    if duplicates:
        raise ParseError(
            "Submodel definition contains duplicate local node ids.",
            definition_id=instance.config.definition_id,
            duplicate_node_ids=duplicates,
        )
    id_map = {
        local_id: qualified_runtime_node_id(instance.node.id, local_id) for local_id in local_ids
    }

    nodes = [
        node.model_copy(
            deep=True,
            update={
                "id": id_map[node.id],
                "position": {
                    "x": node.position.get("x", 0.0) + instance.node.position.get("x", 0.0),
                    "y": node.position.get("y", 0.0) + instance.node.position.get("y", 0.0),
                },
                "data": NodeData(
                    label=node.data.label,
                    description=node.data.description,
                    nodeType=node.data.nodeType,
                    config=_rewrite_config_references(
                        node,
                        instance=instance,
                        reference_map=reference_map,
                    ),
                ),
            },
        )
        for node in definition_graph.nodes
    ]

    edges: list[GraphEdge] = []
    for edge in definition_graph.edges:
        source = id_map.get(edge.source)
        target = id_map.get(edge.target)
        if source is None or target is None:
            raise ParseError(
                "Submodel internal edge references a missing local node.",
                definition_id=instance.config.definition_id,
                instance_id=instance.node.id,
                edge_id=edge.id,
                source=edge.source,
                target=edge.target,
            )
        edges.append(
            GraphEdge(
                id=_edge_id(
                    source,
                    target,
                    edge.sourceHandle,
                    edge.targetHandle,
                    hidden_source_port=edge.sourcePort,
                    hidden_target_port=edge.targetPort,
                ),
                source=source,
                target=target,
                sourceHandle=edge.sourceHandle,
                targetHandle=edge.targetHandle,
                sourcePort=edge.sourcePort,
                targetPort=edge.targetPort,
            )
        )
    return nodes, edges, id_map


def _expanded_edge(
    *,
    source: str,
    target: str,
    source_handle: str | None,
    target_handle: str | None,
    source_port: str | None = None,
    target_port: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        id=_edge_id(
            source,
            target,
            source_handle,
            target_handle,
            hidden_source_port=source_port,
            hidden_target_port=target_port,
        ),
        source=source,
        target=target,
        sourceHandle=source_handle,
        targetHandle=target_handle,
        sourcePort=source_port,
        targetPort=target_port,
    )


def _deduplicate_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    seen: set[tuple[str, str, str | None, str | None, str | None, str | None]] = set()
    result: list[GraphEdge] = []
    for edge in edges:
        identity = (
            edge.source,
            edge.target,
            edge.sourceHandle,
            edge.targetHandle,
            edge.sourcePort,
            edge.targetPort,
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(edge)
    return result


def _contains_line_block(container: str, block: str) -> bool:
    """True when *block*'s lines appear contiguously in *container*.

    Whole-line comparison, not substring search: ``import a`` must never be
    treated as already present because ``import ab`` is. Leading indentation
    is significant — an ``import`` nested inside a function body must not
    satisfy a top-level import the merge still needs to append.
    """
    block_lines = [line.rstrip() for line in block.strip("\n").splitlines()]
    if not block_lines:
        return True
    container_lines = [line.rstrip() for line in container.strip("\n").splitlines()]
    span = len(block_lines)
    return any(
        container_lines[start : start + span] == block_lines
        for start in range(len(container_lines) - span + 1)
    )


def _merge_definition_support_code(
    graph: PipelineGraph,
    selected: list[ResolvedSubmodelInstance],
) -> tuple[str | None, list[str]]:
    merged_preamble = graph.preamble
    merged_blocks = list(graph.preserved_blocks)
    seen_blocks = {block.strip() for block in merged_blocks}
    seen_definitions: set[str] = set()

    for instance in selected:
        definition_id = instance.config.definition_id
        if definition_id in seen_definitions:
            continue
        seen_definitions.add(definition_id)
        child_graph = instance.definition.graph
        child_preamble = (child_graph.preamble or "").strip()
        # Containment, not set identity: a staged dissolve has already merged
        # this definition's preamble into the parent blob, and re-appending it
        # on the next expansion would duplicate the support code.
        if child_preamble and not _contains_line_block(merged_preamble or "", child_preamble):
            if merged_preamble and merged_preamble.strip():
                merged_preamble = f"{merged_preamble.rstrip()}\n\n{child_preamble}"
            else:
                merged_preamble = child_preamble
        for block in child_graph.preserved_blocks:
            identity = block.strip()
            if identity in seen_blocks:
                continue
            seen_blocks.add(identity)
            merged_blocks.append(block)
    return merged_preamble, merged_blocks


def expand_submodel_instances(
    graph: PipelineGraph,
    *,
    instance_ids: set[str] | None = None,
) -> PipelineGraph:
    """Purely expand selected occurrences using shared definition state."""
    instances = resolve_submodel_instances(graph)
    selected_ids = set(instances) if instance_ids is None else set(instance_ids)
    missing = sorted(selected_ids - set(instances))
    if missing:
        raise ParseError(
            "Requested submodel instance does not exist.",
            instance_ids=missing,
        )
    if not selected_ids:
        return graph

    unselected_dependents = sorted(
        instance.node.id
        for instance in instances.values()
        if instance.config.instance_of in selected_ids and instance.node.id not in selected_ids
    )
    if unselected_dependents:
        raise ParseError(
            "Cannot expand a submodel owner while dependent copies remain unexpanded.",
            dependent_instance_ids=unselected_dependents,
        )

    selected = [instances[node.id] for node in graph.nodes if node.id in selected_ids]
    id_maps = {
        instance.node.id: {
            node.id: qualified_runtime_node_id(instance.node.id, node.id)
            for node in instance.definition.graph.nodes
        }
        for instance in selected
    }
    reference_maps = {instance_id: dict(id_map) for instance_id, id_map in id_maps.items()}

    def add_reference(
        reference_map: dict[str, str],
        *,
        key: str,
        value: str,
        instance: ResolvedSubmodelInstance,
        edge: GraphEdge,
    ) -> None:
        previous = reference_map.get(key)
        if previous is not None and previous != value:
            raise ParseError(
                "Submodel boundary reference maps ambiguously to multiple runtime ids.",
                instance_id=instance.node.id,
                definition_id=instance.config.definition_id,
                edge_id=edge.id,
                reference=key,
                runtime_ids=sorted({previous, value}),
            )
        reference_map[key] = value

    for edge in graph.edges:
        target_instance = instances.get(edge.target)
        if target_instance is None or edge.target not in selected_ids:
            continue
        input_port = _input_port(target_instance, edge)
        source_instance = instances.get(edge.source)
        if edge.source in selected_ids:
            if source_instance is None:
                raise ParseError(
                    "Selected submodel source instance could not be resolved.", edge_id=edge.id
                )
            output = _output_port(source_instance, edge)
            source_identity = id_maps[edge.source][output.source.node_id]
        elif source_instance is not None:
            output = _output_port(source_instance, edge)
            source_identity = canonical_downstream_identity(
                source_instance.config.alias, output.port_id
            )
        else:
            source_identity = edge.source
        add_reference(
            reference_maps[edge.target],
            key=input_port.port_id,
            value=source_identity,
            instance=target_instance,
            edge=edge,
        )

    cloned_nodes: list[GraphNode] = []
    cloned_edges: list[GraphEdge] = []
    for instance in selected:
        nodes, edges, _ = _clone_definition(instance, reference_maps[instance.node.id])
        cloned_nodes.extend(nodes)
        cloned_edges.extend(edges)

    remaining_node_ids = {node.id for node in graph.nodes if node.id not in selected_ids}
    runtime_ids = [node.id for node in cloned_nodes]
    collisions = sorted(
        (set(runtime_ids) & remaining_node_ids)
        | {node_id for node_id in set(runtime_ids) if runtime_ids.count(node_id) > 1}
    )
    if collisions:
        raise ParseError(
            "Qualified submodel runtime ids collide with the parent graph.",
            node_ids=collisions,
        )

    expanded_parent_edges: list[GraphEdge] = []
    for edge in graph.edges:
        source_variants = [
            (
                edge.source,
                edge.sourceHandle,
                edge.sourcePort,
            )
        ]
        source_instance = instances.get(edge.source)
        if edge.source in selected_ids:
            if source_instance is None:
                raise ParseError(
                    "Selected submodel source instance could not be resolved.",
                    edge_id=edge.id,
                    instance_id=edge.source,
                )
            output = _output_port(source_instance, edge)
            source_variants = [
                (
                    id_maps[edge.source][output.source.node_id],
                    output.source.handle_id,
                    None,
                )
            ]

        target_variants = [
            (
                edge.target,
                edge.targetHandle,
                edge.targetPort,
            )
        ]
        target_instance = instances.get(edge.target)
        if edge.target in selected_ids:
            if target_instance is None:
                raise ParseError(
                    "Selected submodel target instance could not be resolved.",
                    edge_id=edge.id,
                    instance_id=edge.target,
                )
            input_port = _input_port(target_instance, edge)
            target_variants = [
                (
                    id_maps[edge.target][target.node_id],
                    target.handle_id,
                    None,
                )
                for target in input_port.targets
            ]

        for source, source_handle, source_port in source_variants:
            for target, target_handle, target_port in target_variants:
                expanded_parent_edges.append(
                    _expanded_edge(
                        source=source,
                        target=target,
                        source_handle=source_handle,
                        target_handle=target_handle,
                        source_port=source_port,
                        target_port=target_port,
                    )
                )

    parent_reference_maps: dict[str, dict[str, str]] = {}
    for edge in graph.edges:
        source_instance = instances.get(edge.source)
        if (
            source_instance is None
            or edge.source not in selected_ids
            or edge.target in selected_ids
        ):
            continue
        output = _output_port(source_instance, edge)
        key = canonical_downstream_identity(source_instance.config.alias, output.port_id)
        value = id_maps[edge.source][output.source.node_id]
        target_map = parent_reference_maps.setdefault(edge.target, {})
        previous = target_map.get(key)
        if previous is not None and previous != value:
            raise ParseError(
                "Parent boundary reference maps ambiguously to multiple runtime ids.",
                instance_id=edge.source,
                edge_id=edge.id,
                target_id=edge.target,
                reference=key,
                runtime_ids=sorted({previous, value}),
            )
        target_map[key] = value

    remaining_nodes = rewrite_node_references(
        [node for node in graph.nodes if node.id not in selected_ids],
        parent_reference_maps,
    )
    remaining_instances = {
        instance.config.definition_id
        for instance_id, instance in instances.items()
        if instance_id not in selected_ids
    }
    definitions = _definition_registry(graph)
    remaining_definitions = {
        definition_id: definition
        for definition_id, definition in definitions.items()
        if definition_id in remaining_instances
    } or None
    merged_preamble, merged_blocks = _merge_definition_support_code(graph, selected)
    return graph.model_copy(
        update={
            "nodes": [*remaining_nodes, *cloned_nodes],
            "edges": _deduplicate_edges([*expanded_parent_edges, *cloned_edges]),
            "preamble": merged_preamble,
            "preserved_blocks": merged_blocks,
            "submodels": remaining_definitions,
        }
    )
