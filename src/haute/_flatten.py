"""Graph flattening — dissolve submodel nodes into a flat graph."""

from __future__ import annotations

from haute._edge_join import build_edge_join_boundary_target_roles
from haute._graph_utils import _edge_id
from haute._logging import get_logger
from haute._types import GraphEdge, GraphNode, PipelineGraph
from haute.errors import ParseError

logger = get_logger(component="flatten")


def _embedded_graph(sm_meta: dict) -> PipelineGraph:
    raw_graph = sm_meta.get("graph", {})
    if isinstance(raw_graph, PipelineGraph):
        return raw_graph
    return PipelineGraph.model_validate(raw_graph)


def _boundary_child_id(
    edge: GraphEdge,
    *,
    handle: str | None,
    prefix: str,
    endpoint: str,
    submodel_name: str,
    child_ids: set[str],
) -> str:
    """Resolve and validate one targeted submodel boundary handle."""
    if not handle or not handle.startswith(prefix):
        raise ParseError(
            "Submodel boundary edge has a missing or malformed handle.",
            edge_id=edge.id,
            endpoint=endpoint,
            handle=handle,
            expected=f"{prefix}<child_id>",
            submodel=submodel_name,
            source=edge.source,
            target=edge.target,
        )
    child_id = handle[len(prefix) :]
    if not child_id or child_id not in child_ids:
        raise ParseError(
            "Submodel boundary edge references a child node that does not exist.",
            edge_id=edge.id,
            endpoint=endpoint,
            handle=handle,
            child_id=child_id,
            submodel=submodel_name,
            known_children=sorted(child_ids),
            source=edge.source,
            target=edge.target,
        )
    return child_id


def flatten_graph(
    graph: PipelineGraph,
    target_name: str | None = None,
) -> PipelineGraph:
    """Dissolve submodel nodes into a flat graph for execution.

    When *target_name* is provided, only that specific submodel is
    flattened.  When ``None`` (default), all submodels are dissolved.

    If the graph has no submodels, it is returned unchanged.
    """
    submodels = graph.submodels
    if not submodels:
        return graph

    names_to_flatten = {target_name} & set(submodels) if target_name is not None else set(submodels)
    if not names_to_flatten:
        return graph
    edge_join_boundary_target_roles = build_edge_join_boundary_target_roles(
        submodels,
        names_to_flatten,
    )
    embedded_graphs = {name: _embedded_graph(submodels[name]) for name in names_to_flatten}
    child_ids_by_placeholder = {
        f"submodel__{name}": {node.id for node in embedded_graph.nodes}
        for name, embedded_graph in embedded_graphs.items()
    }

    nodes: list[GraphNode] = list(graph.nodes)
    edges: list[GraphEdge] = list(graph.edges)

    # Remove submodel placeholder nodes (only the targeted ones)
    submodel_node_ids = {f"submodel__{name}" for name in names_to_flatten}
    nodes = [n for n in nodes if n.id not in submodel_node_ids]

    # Inline child nodes and internal edges from each targeted submodel
    for sm_name in submodels:
        if sm_name not in names_to_flatten:
            continue
        embedded_graph = embedded_graphs[sm_name]
        nodes.extend(embedded_graph.nodes)
        edges.extend(embedded_graph.edges)

    # Rewire boundary edges: submodel handles → actual child nodes
    rewired: list[GraphEdge] = []
    for edge in edges:
        src = edge.source
        tgt = edge.target
        eid = edge.id
        new_sh = edge.sourceHandle
        new_th = edge.targetHandle
        new_source_port = edge.sourcePort
        new_target_port = edge.targetPort
        boundary_rewired = False

        if src in submodel_node_ids:
            # e.g. sourceHandle="out__frequency_model" → source="frequency_model"
            sm_name = src.removeprefix("submodel__")
            src = _boundary_child_id(
                edge,
                handle=edge.sourceHandle,
                prefix="out__",
                endpoint="source",
                submodel_name=sm_name,
                child_ids=child_ids_by_placeholder[f"submodel__{sm_name}"],
            )
            new_sh = edge.sourcePort
            new_source_port = None
            boundary_rewired = True

        if tgt in submodel_node_ids:
            # e.g. targetHandle="in__frequency_model" → target="frequency_model"
            original_tgt = tgt
            sm_name = tgt.removeprefix("submodel__")
            tgt = _boundary_child_id(
                edge,
                handle=edge.targetHandle,
                prefix="in__",
                endpoint="target",
                submodel_name=sm_name,
                child_ids=child_ids_by_placeholder[f"submodel__{sm_name}"],
            )
            new_th = edge.targetPort
            if new_th is None:
                new_th = edge_join_boundary_target_roles.get((original_tgt, tgt, src))
            new_target_port = None
            boundary_rewired = True

        # Refuse any edge that still references a targeted placeholder.
        if src in submodel_node_ids or tgt in submodel_node_ids:
            raise ParseError(
                "Submodel boundary edge could not be flattened.",
                edge_id=edge.id,
                source=src,
                target=tgt,
            )

        if boundary_rewired:
            eid = _edge_id(
                src,
                tgt,
                new_sh,
                new_th,
                hidden_source_port=new_source_port,
                hidden_target_port=new_target_port,
            )

        rewired.append(
            GraphEdge(
                id=eid,
                source=src,
                target=tgt,
                sourceHandle=new_sh,
                targetHandle=new_th,
                sourcePort=new_source_port,
                targetPort=new_target_port,
            )
        )

    # Include authored handles still hidden behind an unflattened boundary.
    seen: set[
        tuple[
            str,
            str,
            str | None,
            str | None,
            str | None,
            str | None,
        ]
    ] = set()
    deduped: list[GraphEdge] = []
    for e in rewired:
        key = (
            e.source,
            e.target,
            e.sourceHandle,
            e.targetHandle,
            e.sourcePort,
            e.targetPort,
        )
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    logger.debug(
        "graph_flattened",
        submodel_count=len(names_to_flatten),
        node_count=len(nodes),
        edge_count=len(deduped),
    )
    remaining_submodels = {k: v for k, v in submodels.items() if k not in names_to_flatten} or None
    merged_preamble = graph.preamble
    merged_preserved_blocks = list(graph.preserved_blocks)
    for sm_name in submodels:
        if sm_name not in names_to_flatten:
            continue
        embedded_graph = embedded_graphs[sm_name]
        child_preamble = embedded_graph.preamble
        if child_preamble and child_preamble.strip():
            if merged_preamble and merged_preamble.strip():
                merged_preamble = f"{merged_preamble.rstrip()}\n\n{child_preamble.rstrip()}"
            else:
                merged_preamble = child_preamble.rstrip()
        merged_preserved_blocks.extend(embedded_graph.preserved_blocks)
    return graph.model_copy(
        update={
            "nodes": nodes,
            "edges": deduped,
            "preamble": merged_preamble,
            "preserved_blocks": merged_preserved_blocks,
            "submodels": remaining_submodels,
        },
    )
