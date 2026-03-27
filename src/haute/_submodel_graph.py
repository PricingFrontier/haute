"""Shared helpers for building submodel placeholder nodes and rewiring edges.

Used by both ``_parser_submodels.py`` (parse-time hierarchical view) and
``routes/_submodel_ops.py`` (GUI create-submodel operation).
"""

from __future__ import annotations

from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType


def build_submodel_placeholder(
    sm_name: str,
    sm_file: str,
    child_node_ids: list[str],
    input_ports: list[str],
    output_ports: list[str],
    *,
    description: str = "",
) -> GraphNode:
    """Build a ``SUBMODEL`` placeholder node for the parent graph.

    Parameters
    ----------
    sm_name:
        Sanitized submodel name (used as node label).
    sm_file:
        Source file path for the submodel.
    child_node_ids:
        IDs of nodes contained in the submodel.
    input_ports:
        Node IDs that receive edges from outside the submodel.
    output_ports:
        Node IDs that send edges to outside the submodel.
    description:
        Optional submodel description.
    """
    sm_node_id = f"submodel__{sm_name}"
    return GraphNode(
        id=sm_node_id,
        type=NodeType.SUBMODEL,
        position={"x": 0, "y": 0},
        data=NodeData(
            label=sm_name,
            description=description,
            nodeType=NodeType.SUBMODEL,
            config={
                "file": sm_file,
                "childNodeIds": list(child_node_ids),
                "inputPorts": list(input_ports),
                "outputPorts": list(output_ports),
            },
        ),
    )


def classify_ports(
    cross_edges: list[tuple[str, str]],
    child_node_ids: set[str],
) -> tuple[list[str], list[str]]:
    """Determine input and output ports from cross-boundary edges.

    Parameters
    ----------
    cross_edges:
        ``(source, target)`` tuples for edges that cross the submodel boundary.
    child_node_ids:
        Set of node IDs that belong to the submodel.

    Returns
    -------
    tuple[list[str], list[str]]
        ``(input_ports, output_ports)`` — deduplicated, order-preserving.
    """
    input_ports: list[str] = []
    output_ports: list[str] = []
    for src, tgt in cross_edges:
        if tgt in child_node_ids and src not in child_node_ids:
            if tgt not in input_ports:
                input_ports.append(tgt)
        if src in child_node_ids and tgt not in child_node_ids:
            if src not in output_ports:
                output_ports.append(src)
    return input_ports, output_ports


def rewire_edges(
    edges: list[GraphEdge],
    sm_node_id: str,
    child_node_ids: set[str],
) -> list[GraphEdge]:
    """Rewire cross-boundary edges to/from the submodel placeholder node.

    Edges fully inside the submodel are dropped.
    Edges fully outside are preserved unchanged.
    Cross-boundary edges are replaced with edges to/from ``sm_node_id``
    using ``in__<child>`` / ``out__<child>`` handles.
    """
    result: list[GraphEdge] = []
    for e in edges:
        src_inside = e.source in child_node_ids
        tgt_inside = e.target in child_node_ids
        if src_inside and tgt_inside:
            continue  # internal edge — lives inside submodel
        elif tgt_inside:
            # External → internal: target becomes submodel node
            result.append(
                GraphEdge(
                    id=f"e_{e.source}_{sm_node_id}__{e.target}",
                    source=e.source,
                    target=sm_node_id,
                    targetHandle=f"in__{e.target}",
                )
            )
        elif src_inside:
            # Internal → external: source becomes submodel node
            result.append(
                GraphEdge(
                    id=f"e_{sm_node_id}_{e.target}__{e.source}",
                    source=sm_node_id,
                    sourceHandle=f"out__{e.source}",
                    target=e.target,
                )
            )
        else:
            result.append(e)
    return result
