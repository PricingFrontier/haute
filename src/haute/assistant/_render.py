"""The compact graph rendering shared by live pipelines and packaged examples.

A live pipeline's node configurations are project data, read in full only
through the policy-gated ``get_node_config``, so its rendering names their keys.
A packaged example is library content, so its rendering carries each node's
configuration with its values: that is what the example teaches.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from haute._types import GraphNode, NodeType, PipelineGraph
from haute.assistant._catalog import capability_manifest


def _node_type(node: GraphNode) -> str:
    value = node.data.nodeType
    return value.value if isinstance(value, NodeType) else str(value)


def _render_config_summary(config: Mapping[str, Any]) -> dict[str, object]:
    return {"keys": sorted(config), "count": len(config)}


def render_pipeline_graph(
    graph: PipelineGraph, *, config_values: bool = False
) -> dict[str, object]:
    """Render the compact graph shape shared by live pipelines and examples.

    With *config_values* each node's configuration is rendered whole, as JSON
    values; otherwise only its key names and count.
    """

    nodes = [
        {
            "id": node.id,
            "type": _node_type(node),
            "label": node.data.label,
            "config": (
                json.loads(json.dumps(node.data.config))
                if config_values
                else _render_config_summary(node.data.config)
            ),
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
        descriptor.id: any(_node_type(node) == descriptor.id for node in graph.nodes)
        for descriptor in capability_manifest().nodes
        if descriptor.singleton
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
