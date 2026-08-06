"""The compact graph rendering shared by live pipelines and packaged examples."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from haute._types import GraphNode, NodeType, PipelineGraph
from haute.assistant._catalog import NODE_CATALOG


def _node_type(node: GraphNode) -> str:
    value = node.data.nodeType
    return value.value if isinstance(value, NodeType) else str(value)


def _render_config_summary(config: Mapping[str, Any]) -> dict[str, object]:
    return {"keys": sorted(config), "count": len(config)}


def render_pipeline_graph(graph: PipelineGraph) -> dict[str, object]:
    """Render the compact graph shape shared by live pipelines and examples."""

    nodes = [
        {
            "id": node.id,
            "type": _node_type(node),
            "label": node.data.label,
            "config": _render_config_summary(node.data.config),
        }
        for node in graph.nodes
    ]
    # Snake-case deliberately: these are the exact field names the graph-edit
    # operations accept. The camel-case persisted spelling is an internal wire
    # detail, and echoing it here invited edit operations written in the shape
    # the model had just read, which the closed operation schema then rejected.
    edges = [
        {
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "source_handle": edge.sourceHandle,
            "target_handle": edge.targetHandle,
        }
        for edge in graph.edges
    ]
    singletons = {
        entry.node_type.value: any(
            _node_type(node) == entry.node_type.value for node in graph.nodes
        )
        for entry in NODE_CATALOG.values()
        if entry.singleton
    }
    return {
        "name": graph.pipeline_name,
        "description": graph.pipeline_description,
        "nodes": nodes,
        "edges": edges,
        "preamble": {
            "present": bool(graph.preamble),
            "sha256": (
                sha256(graph.preamble.encode("utf-8")).hexdigest() if graph.preamble else None
            ),
        },
        "singletons": singletons,
    }


__all__ = ["render_pipeline_graph"]
