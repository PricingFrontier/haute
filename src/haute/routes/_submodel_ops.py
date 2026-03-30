"""Pure graph operations for submodel create/dissolve — no I/O or HTTP."""

from __future__ import annotations

from dataclasses import dataclass, field

from haute._submodel_graph import (
    build_submodel_placeholder,
    classify_ports,
    rewire_edges,
)
from haute._types import NodeType, PipelineGraph
from haute.graph_utils import _sanitize_func_name


@dataclass
class SubmodelGraphResult:
    """Result of ``create_submodel_graph`` — everything the caller needs."""

    graph: PipelineGraph
    sm_file: str
    sm_name: str
    child_node_ids: list[str] = field(default_factory=list)


def create_submodel_graph(
    graph: PipelineGraph,
    node_ids: list[str],
    name: str,
) -> SubmodelGraphResult:
    """Split *node_ids* out of *graph* into a submodel named *name*.

    Returns a ``SubmodelGraphResult`` containing the new parent graph
    (with a placeholder submodel node and rewired edges) plus metadata.

    Raises ``ValueError`` if fewer than 2 nodes are selected.
    """
    sm_name = _sanitize_func_name(name)
    sm_file = f"modules/{sm_name}.py"
    selected_ids = set(node_ids)

    nodes = graph.nodes
    edges = graph.edges

    # Separate child vs parent nodes
    child_nodes = [n for n in nodes if n.id in selected_ids]
    parent_nodes = [n for n in nodes if n.id not in selected_ids]
    child_node_ids = {n.id for n in child_nodes}

    # Reject nesting: selected nodes must not include submodel nodes
    for n in child_nodes:
        if n.data.nodeType == NodeType.SUBMODEL:
            raise ValueError("Submodels cannot be nested inside other submodels")

    # Validate after filtering against actual graph nodes (not raw input)
    if len(child_node_ids) < 2:
        raise ValueError("A submodel must contain at least 2 nodes.")

    # Classify edges
    internal_edges = [e for e in edges if e.source in child_node_ids and e.target in child_node_ids]
    cross_edges = [e for e in edges if (e.source in child_node_ids) != (e.target in child_node_ids)]
    external_edges = [
        e for e in edges if e.source not in child_node_ids and e.target not in child_node_ids
    ]

    # Determine input/output ports from cross-boundary edges
    cross_tuples = [(e.source, e.target) for e in cross_edges]
    input_ports, output_ports = classify_ports(cross_tuples, child_node_ids)

    # Build submodel internal graph dict
    sm_graph = {
        "nodes": [n.model_dump() for n in child_nodes],
        "edges": [e.model_dump() for e in internal_edges],
        "submodel_name": sm_name,
        "submodel_description": "",
        "source_file": sm_file,
    }

    # Build submodel placeholder node
    sm_node = build_submodel_placeholder(
        sm_name,
        sm_file,
        list(child_node_ids),
        input_ports,
        output_ports,
    )
    sm_node_id = sm_node.id

    # Rewire cross-boundary edges
    rewired_cross = rewire_edges(cross_edges, sm_node_id, child_node_ids)

    # Assemble new parent graph
    existing_submodels = dict(graph.submodels or {})
    existing_submodels[sm_name] = {
        "file": sm_file,
        "childNodeIds": list(child_node_ids),
        "inputPorts": input_ports,
        "outputPorts": output_ports,
        "graph": sm_graph,
    }
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
        child_node_ids=list(child_node_ids),
    )
