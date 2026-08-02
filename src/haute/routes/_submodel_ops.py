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
    child_node_ids: list[str] = field(default_factory=list)


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
    placeholder_id = f"submodel__{sm_name}"
    existing_submodel_names = {existing.casefold() for existing in existing_submodels}
    existing_node_ids = {node_id.casefold() for node_id in graph_node_ids}
    if (
        sm_name.casefold() in existing_submodel_names
        or placeholder_id.casefold() in existing_node_ids
    ):
        raise SubmodelValidationError(
            code="submodel_exists",
            status_code=409,
            detail=f"Submodel {sm_name!r} already exists.",
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

    # Determine input/output ports from cross-boundary edges
    input_ports, output_ports = classify_ports(cross_edges, child_node_id_set)
    child_node_labels = {node.id: node.data.label for node in child_nodes}
    output_port_labels = {port: child_node_labels[port] for port in output_ports}

    # Build submodel internal graph dict
    sm_graph = {
        "nodes": [n.model_dump() for n in child_nodes],
        "edges": [e.model_dump() for e in internal_edges],
        "pipeline_name": sm_name,
        "pipeline_description": "",
        "preamble": graph.preamble or "",
        "preserved_blocks": list(graph.preserved_blocks),
        "source_file": sm_file,
    }

    xs = [node.position["x"] for node in child_nodes]
    ys = [node.position["y"] for node in child_nodes]
    placeholder_position = {
        "x": (min(xs) + max(xs)) / 2,
        "y": (min(ys) + max(ys)) / 2,
    }

    # Build submodel placeholder node
    sm_node = build_submodel_placeholder(
        sm_name,
        sm_file,
        child_node_ids,
        input_ports,
        output_ports,
        output_port_labels=output_port_labels,
        position=placeholder_position,
    )
    sm_node_id = sm_node.id

    # Rewire cross-boundary edges
    rewired_cross = rewire_edges(cross_edges, sm_node_id, child_node_id_set)

    # Assemble new parent graph
    existing_submodels[sm_name] = {
        "file": sm_file,
        "childNodeIds": child_node_ids,
        "inputPorts": input_ports,
        "outputPorts": output_ports,
        "managed": True,
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
        child_node_ids=child_node_ids,
    )
