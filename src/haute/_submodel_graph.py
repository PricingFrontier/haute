"""Shared helpers for building submodel placeholder nodes and rewiring edges.

Used by both ``_parser_submodels.py`` (parse-time hierarchical view) and
``routes/_submodel_ops.py`` (GUI create-submodel operation).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import blake2b

from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType


def _boundary_edge_id(
    base_id: str,
    source_port: str | None,
    target_port: str | None,
) -> str:
    """Create a port-aware deterministic boundary edge identifier."""
    payload = "\0".join((source_port or "", target_port or "")).encode()
    digest = blake2b(payload, digest_size=6).hexdigest()
    return f"{base_id}_{digest}"


def build_submodel_placeholder(
    sm_name: str,
    sm_file: str,
    child_node_ids: list[str],
    input_ports: list[str],
    output_ports: list[str],
    *,
    output_port_labels: Mapping[str, str] | None = None,
    description: str = "",
    position: dict[str, float] | None = None,
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
    output_port_labels:
        Presentation labels for output ports, keyed by child node ID. Only
        declared output ports are retained, in ``output_ports`` order.
    description:
        Optional submodel description.
    """
    sm_node_id = f"submodel__{sm_name}"
    labels = output_port_labels or {}
    return GraphNode(
        id=sm_node_id,
        type=NodeType.SUBMODEL,
        position=position or {"x": 0, "y": 0},
        data=NodeData(
            label=sm_name,
            description=description,
            nodeType=NodeType.SUBMODEL,
            config={
                "file": sm_file,
                "childNodeIds": list(child_node_ids),
                "inputPorts": list(input_ports),
                "outputPorts": list(output_ports),
                "outputPortLabels": {port: labels[port] for port in output_ports if port in labels},
            },
        ),
    )


def classify_ports(
    cross_edges: Sequence[GraphEdge],
    child_node_ids: set[str],
) -> tuple[list[str], list[str]]:
    """Determine input and output ports from cross-boundary edges.

    Parameters
    ----------
    cross_edges:
        Canonical graph edges that cross the submodel boundary. Port
        classification depends only on which side of the boundary each
        endpoint sits.
    child_node_ids:
        Set of node IDs that belong to the submodel.

    Returns
    -------
    tuple[list[str], list[str]]
        ``(input_ports, output_ports)`` — deduplicated, order-preserving.
    """
    input_ports: list[str] = []
    output_ports: list[str] = []
    for edge in cross_edges:
        src, tgt = edge.source, edge.target
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
            # External → internal: target becomes submodel node. Preserve the
            # source side's handle so a child-of-A → child-of-B edge (rewired
            # once per submodel) keeps the boundary handle set by the earlier
            # pass instead of clobbering it to None.
            authored_source_port = (
                e.sourcePort if e.source.startswith("submodel__") else e.sourceHandle
            )
            authored_target_port = e.targetPort if e.targetPort is not None else e.targetHandle
            target_handle = f"in__{e.target}"
            base_id = f"e_{e.source}_{sm_node_id}__{e.target}"
            result.append(
                GraphEdge(
                    id=_boundary_edge_id(
                        base_id,
                        authored_source_port,
                        authored_target_port,
                    ),
                    source=e.source,
                    sourceHandle=e.sourceHandle,
                    target=sm_node_id,
                    targetHandle=target_handle,
                    sourcePort=e.sourcePort,
                    targetPort=authored_target_port,
                )
            )
        elif src_inside:
            # Internal → external: source becomes submodel node. Preserve the
            # target side's handle for the same cross-submodel rewire reason.
            authored_source_port = e.sourcePort if e.sourcePort is not None else e.sourceHandle
            authored_target_port = (
                e.targetPort if e.target.startswith("submodel__") else e.targetHandle
            )
            source_handle = f"out__{e.source}"
            base_id = f"e_{sm_node_id}_{e.target}__{e.source}"
            result.append(
                GraphEdge(
                    id=_boundary_edge_id(
                        base_id,
                        authored_source_port,
                        authored_target_port,
                    ),
                    source=sm_node_id,
                    sourceHandle=source_handle,
                    target=e.target,
                    targetHandle=e.targetHandle,
                    sourcePort=authored_source_port,
                    targetPort=e.targetPort,
                )
            )
        else:
            result.append(e)
    return result
