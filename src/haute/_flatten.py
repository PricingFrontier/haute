"""Graph flattening — dissolve submodel nodes into a flat graph."""

from __future__ import annotations

from haute._logging import get_logger
from haute._types import GraphEdge, GraphNode, PipelineGraph

logger = get_logger(component="flatten")


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

    nodes: list[GraphNode] = list(graph.nodes)
    edges: list[GraphEdge] = list(graph.edges)

    # Remove submodel placeholder nodes (only the targeted ones)
    submodel_node_ids = {f"submodel__{name}" for name in names_to_flatten}
    nodes = [n for n in nodes if n.id not in submodel_node_ids]

    # Inline child nodes and internal edges from each targeted submodel
    for _sm_name, sm_meta in submodels.items():
        if _sm_name not in names_to_flatten:
            continue
        sm_graph = sm_meta.get("graph", {})
        for nd in sm_graph.get("nodes", []):
            nodes.append(GraphNode.model_validate(nd) if isinstance(nd, dict) else nd)
        for ed in sm_graph.get("edges", []):
            edges.append(GraphEdge.model_validate(ed) if isinstance(ed, dict) else ed)

    # Rewire boundary edges: submodel handles → actual child nodes
    rewired: list[GraphEdge] = []
    for edge in edges:
        src = edge.source
        tgt = edge.target
        eid = edge.id
        sh = edge.sourceHandle or ""
        th = edge.targetHandle or ""
        new_sh = edge.sourceHandle
        new_th = edge.targetHandle

        if src in submodel_node_ids and sh:
            # e.g. sourceHandle="out__frequency_model" → source="frequency_model"
            src = sh.removeprefix("out__")
            eid = f"e_{src}_{tgt}"
            new_sh = None

        if tgt in submodel_node_ids and th:
            # e.g. targetHandle="in__frequency_model" → target="frequency_model"
            tgt = th.removeprefix("in__")
            eid = f"e_{src}_{tgt}"
            new_th = None

        # Skip edges that still reference a submodel node (shouldn't happen)
        if src in submodel_node_ids or tgt in submodel_node_ids:
            continue

        rewired.append(
            GraphEdge(
                id=eid,
                source=src,
                target=tgt,
                sourceHandle=new_sh,
                targetHandle=new_th,
            )
        )

    # Deduplicate edges by (source, target, sourceHandle, targetHandle)
    seen: set[tuple[str, str, str | None, str | None]] = set()
    deduped: list[GraphEdge] = []
    for e in rewired:
        key = (e.source, e.target, e.sourceHandle, e.targetHandle)
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    logger.debug(
        "graph_flattened",
        submodel_count=len(submodels),
        node_count=len(nodes),
        edge_count=len(deduped),
    )
    remaining_submodels = {k: v for k, v in submodels.items() if k not in names_to_flatten} or None
    return graph.model_copy(
        update={"nodes": nodes, "edges": deduped, "submodels": remaining_submodels},
    )
