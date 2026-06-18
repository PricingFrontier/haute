"""GraphNode / GraphEdge construction for the pipeline parser.

Turns the raw decorator-walk output of ``_extract_decorated_nodes`` into
the Pydantic models the frontend consumes, and derives edges either from
explicit ``pipeline.connect()`` calls or from parameter-name matching.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

from haute._ast_helpers import (
    _get_decorator_kwargs,
    _get_decorator_node_type,
    _get_docstring,
)
from haute._config_builder import _resolve_node_config
from haute._graph_utils import _edge_id
from haute._types import GraphEdge, GraphNode, NodeData, NodeType

__all__ = [
    "_extract_decorated_nodes",
    "_build_edges",
    "_build_rf_nodes",
]

# Node types whose input parameter names are structural, not user-aliasable.
# Kept in sync with ``codegen._ALIAS_INELIGIBLE_NODE_TYPES`` — recovery and
# emission must agree on which inputs carry binding aliases.
_ALIAS_INELIGIBLE_NODE_TYPES = frozenset(
    {
        NodeType.EDGE_JOIN,
        NodeType.LIVE_SWITCH,
        NodeType.SUBMODEL,
        NodeType.SUBMODEL_PORT,
    }
)


def _extract_decorated_nodes(
    tree: ast.Module,
    decorator_checker: Callable[[ast.expr], bool],
    func_bodies: dict[str, str],
    base_dir: Path | None,
) -> list[dict[str, Any]]:
    """Extract decorated function nodes from an AST tree.

    Iterates over top-level ``ast.FunctionDef`` nodes, finds those whose
    decorator matches *decorator_checker*, resolves their config, and
    returns a list of raw-node dicts ready for ``_build_rf_nodes`` /
    ``_build_edges``.

    Args:
        tree: The parsed AST module.
        decorator_checker: A callable that returns True for matching
            decorators (e.g. ``_is_pipeline_node_decorator``).
        func_bodies: Pre-extracted function body source, keyed by name
            (from ``_extract_function_bodies``).
        base_dir: Project root for resolving ``config=`` references.

    Returns:
        A list of dicts with keys ``func_name``, ``node_type``,
        ``description``, ``config``, and ``param_names``.
    """
    raw_nodes: list[dict[str, Any]] = []

    for stmt in ast.iter_child_nodes(tree):
        if not isinstance(stmt, ast.FunctionDef):
            continue

        matched_decorator = None
        for dec in stmt.decorator_list:
            if decorator_checker(dec):
                matched_decorator = dec
                break

        if matched_decorator is None:
            continue

        func_name = stmt.name
        decorator_kwargs = _get_decorator_kwargs(matched_decorator)
        param_names = [arg.arg for arg in stmt.args.args]
        n_params = len(param_names)
        description = _get_docstring(stmt)
        body = func_bodies.get(func_name, "")
        explicit_node_type = _get_decorator_node_type(matched_decorator)

        node_type, config = _resolve_node_config(
            decorator_kwargs,
            body,
            param_names,
            n_params,
            base_dir,
            func_name=func_name,
            explicit_node_type=explicit_node_type,
        )

        raw_nodes.append(
            {
                "func_name": func_name,
                "node_type": node_type,
                "description": description,
                "config": config,
                "param_names": param_names,
            }
        )

    return raw_nodes


def _build_edges(
    raw_nodes: list[dict],
    explicit_connect_pairs: (
        list[tuple[str, str, str | None, str | None]]
        | list[tuple[str, str, str | None]]
        | list[tuple[str, str]]
    ),
) -> list[GraphEdge]:
    """Build GraphEdge models from explicit connect() calls and implicit param-name matching.

    Accepts legacy 2-tuples, source-port 3-tuples, and full
    ``(src, tgt, source_port, target_port)`` tuples. Port metadata is
    lifted onto the corresponding React Flow handle fields.
    """
    node_names = {n["func_name"] for n in raw_nodes}
    edges: list[GraphEdge] = []
    explicit_edges: set[tuple[str, str]] = set()

    for edge_tuple in explicit_connect_pairs:
        if len(edge_tuple) == 4:
            src, tgt, source_port, target_port = edge_tuple
        elif len(edge_tuple) == 3:
            src, tgt, source_port = edge_tuple
            target_port = None
        else:
            src, tgt = edge_tuple
            source_port = None
            target_port = None
        if src in node_names and tgt in node_names:
            explicit_edges.add((src, tgt))
            edges.append(
                GraphEdge(
                    id=_edge_id(src, tgt, source_port, target_port),
                    source=src,
                    target=tgt,
                    sourceHandle=source_port,
                    targetHandle=target_port,
                )
            )

    # Implicit edges from parameter names matching node names
    for node_info in raw_nodes:
        for param in node_info["param_names"]:
            if param in node_names and param != node_info["func_name"]:
                pair = (param, node_info["func_name"])
                if pair not in explicit_edges:
                    edges.append(
                        GraphEdge(
                            id=f"e_{pair[0]}_{pair[1]}",
                            source=pair[0],
                            target=pair[1],
                        )
                    )

    # Fallback: if still no edges, infer linear chain from definition order
    if not edges and len(raw_nodes) > 1:
        for i in range(1, len(raw_nodes)):
            src = raw_nodes[i - 1]["func_name"]
            tgt = raw_nodes[i]["func_name"]
            edges.append(GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt))

    _recover_input_aliases(edges, raw_nodes)
    return edges


def _raw_node_accepts_input_alias(node: dict[str, Any]) -> bool:
    """Whether a parsed raw node's input parameters may carry a binding alias."""
    if node["node_type"] in _ALIAS_INELIGIBLE_NODE_TYPES:
        return False
    if (node.get("config") or {}).get("instanceOf"):
        return False
    return True


def _recover_input_aliases(edges: list[GraphEdge], raw_nodes: list[dict[str, Any]]) -> None:
    """Recover per-connection input aliases by position (mirror of codegen emit).

    An edge's parameter name is normally the upstream node's sanitized func
    name; when the author chose a binding alias, codegen emits that name in its
    place.  We recover it positionally: the i-th inbound edge of a node — in
    connect order, which codegen emits in parameter order — pairs with the
    node's i-th signature parameter.  A parameter that differs from its edge's
    source func name is an alias and is written back onto the edge.

    Skipped for node types whose params are structural rather than aliasable
    (edge-join roles, live-switch, instance, submodel placeholders).  ``df`` is
    the codegen default parameter sentinel (used for source / no-input nodes)
    and is never recorded as an explicit alias, so conventional single-input
    bodies round-trip unchanged.
    """
    nodes_by_func = {n["func_name"]: n for n in raw_nodes}
    inbound: dict[str, list[GraphEdge]] = {}
    for edge in edges:
        inbound.setdefault(edge.target, []).append(edge)

    for func_name, node in nodes_by_func.items():
        if not _raw_node_accepts_input_alias(node):
            continue
        params = node["param_names"]
        edges_in = inbound.get(func_name, [])
        # Only a clean 1:1 alignment is safe to interpret positionally; a
        # mismatch (e.g. inferred implicit edges mixed in) is left untouched.
        if not edges_in or len(edges_in) != len(params):
            continue
        for edge, param in zip(edges_in, params, strict=True):
            if param != edge.source and param != "df":
                edge.inputAlias = param


def _build_rf_nodes(raw_nodes: list[dict], x_spacing: int = 300) -> list[GraphNode]:
    """Convert raw parsed nodes into GraphNode Pydantic models."""
    return [
        GraphNode(
            id=n["func_name"],
            type=n["node_type"],
            position={"x": i * x_spacing, "y": 0},
            data=NodeData(
                label=n["func_name"],
                description=n["description"],
                nodeType=n["node_type"],
                config=n["config"],
            ),
        )
        for i, n in enumerate(raw_nodes)
    ]
