"""GraphNode / GraphEdge construction for the pipeline parser.

Turns the raw decorator-walk output of ``_extract_decorated_nodes`` into
the Pydantic models the frontend consumes, and derives edges either from
explicit ``pipeline.connect()`` calls or from parameter-name matching.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haute._ast_helpers import (
    _get_decorator_kwargs,
    _get_decorator_node_type,
)
from haute._config_builder import _resolve_node_config
from haute._graph_utils import _edge_id, resolve_input_mapping_names
from haute._types import GraphEdge, GraphNode, NodeData, NodeType
from haute.errors import ConfigError, ParseError

__all__ = [
    "PipelineNodeSkeleton",
    "_extract_decorated_node_skeletons",
    "_resolve_node_skeleton",
    "_extract_decorated_nodes",
    "_build_edges",
    "_build_rf_nodes",
    "_edge_param_names_for_node",
]


@dataclass(frozen=True, slots=True)
class PipelineNodeSkeleton:
    """One authored pipeline node before config or contract resolution."""

    authored_id: str
    decorator_name: str
    decorator: ast.expr
    explicit_node_type: NodeType | None
    description: str
    body: str
    param_names: tuple[str, ...]
    edge_param_names: tuple[str, ...]
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    is_async: bool


def _decorator_method(decorator: ast.expr) -> str:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute):
        return target.attr
    return "unknown"


def _extract_decorated_node_skeletons(
    tree: ast.Module,
    decorator_checker: Callable[[ast.expr], bool],
    func_bodies: dict[str, str],
    *,
    source: str | None = None,
) -> list[PipelineNodeSkeleton]:
    """Discover authored nodes without resolving their configuration.

    ``source`` lets duplicate function names retain their own body rather
    than sharing the last value from the legacy name-keyed body map.
    """
    skeletons: list[PipelineNodeSkeleton] = []
    source_lines = source.splitlines() if source is not None else None
    for stmt in ast.iter_child_nodes(tree):
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        matched_decorator = next(
            (decorator for decorator in stmt.decorator_list if decorator_checker(decorator)),
            None,
        )
        if matched_decorator is None:
            continue

        positional_param_names = tuple(arg.arg for arg in (*stmt.args.posonlyargs, *stmt.args.args))
        param_names = (
            *positional_param_names,
            *(arg.arg for arg in stmt.args.kwonlyargs),
        )
        body = func_bodies.get(stmt.name, "")
        if source_lines is not None and stmt.body:
            body_start = stmt.body[0].lineno - 1
            body_end = stmt.body[-1].end_lineno or stmt.body[-1].lineno
            body = "\n".join(source_lines[body_start:body_end])
        decorator_start = min(
            getattr(decorator, "lineno", stmt.lineno)
            for decorator in stmt.decorator_list
            if decorator_checker(decorator)
        )
        decorator_column = min(
            getattr(decorator, "col_offset", stmt.col_offset)
            for decorator in stmt.decorator_list
            if decorator_checker(decorator)
        )
        skeletons.append(
            PipelineNodeSkeleton(
                authored_id=stmt.name,
                decorator_name=_decorator_method(matched_decorator),
                decorator=matched_decorator,
                explicit_node_type=_get_decorator_node_type(matched_decorator),
                description=ast.get_docstring(stmt) or "",
                body=body,
                param_names=tuple(param_names),
                edge_param_names=positional_param_names,
                start_line=decorator_start,
                start_column=decorator_column,
                end_line=stmt.end_lineno or stmt.lineno,
                end_column=stmt.end_col_offset or stmt.col_offset,
                is_async=isinstance(stmt, ast.AsyncFunctionDef),
            )
        )
    return skeletons


def _resolve_node_skeleton(
    skeleton: PipelineNodeSkeleton,
    base_dir: Path | None,
) -> dict[str, Any]:
    """Resolve one skeleton into the strict builder's raw-node representation."""
    if skeleton.explicit_node_type is None:
        target = (
            skeleton.decorator.func
            if isinstance(skeleton.decorator, ast.Call)
            else skeleton.decorator
        )
        receiver = (
            target.value.id
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
            else "pipeline"
        )
        raise ParseError(
            f"unknown @{receiver} node decorator {skeleton.decorator_name!r}; "
            "this Haute version does not support that node type.",
            decorator=skeleton.decorator_name,
            line=skeleton.start_line,
        )
    decorator_kwargs = _get_decorator_kwargs(skeleton.decorator)
    node_type, config = _resolve_node_config(
        decorator_kwargs,
        skeleton.body,
        list(skeleton.param_names),
        len(skeleton.param_names),
        base_dir,
        func_name=skeleton.authored_id,
        explicit_node_type=skeleton.explicit_node_type,
        edge_param_names=list(skeleton.edge_param_names),
    )
    return {
        "func_name": skeleton.authored_id,
        "node_type": node_type,
        "description": skeleton.description,
        "config": config,
        "param_names": list(skeleton.param_names),
        "edge_param_names": list(skeleton.edge_param_names),
    }


def _extract_decorated_nodes(
    tree: ast.Module,
    decorator_checker: Callable[[ast.expr], bool],
    func_bodies: dict[str, str],
    base_dir: Path | None,
    *,
    source: str | None = None,
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
    skeletons = _extract_decorated_node_skeletons(
        tree,
        decorator_checker,
        func_bodies,
        source=source,
    )
    raw_nodes: list[dict[str, Any]] = []
    seen_func_names: set[str] = set()
    for skeleton in skeletons:
        # ``async def`` node bodies cannot round-trip through codegen (the
        # save path re-emits synchronous bodies), and silently dropping the
        # node hides an authored pricing body from the graph. Fail loud.
        if skeleton.is_async:
            raise ParseError(
                f"@pipeline node {skeleton.authored_id!r} is declared `async def`; pipeline "
                f"node bodies must be synchronous — remove the `async` keyword.",
                line=skeleton.start_line,
            )

        func_name = skeleton.authored_id
        # Two decorated functions with the same name collapse to a single
        # GraphNode id downstream (the executor keys nodes by id), silently
        # discarding the first node's pricing body. Reject the collision at
        # the point the data actually collides.
        if func_name in seen_func_names:
            raise ParseError(
                f"duplicate @pipeline node function name {func_name!r}; each "
                f"decorated node function must have a unique name because the name "
                f"becomes the graph node id.",
                line=skeleton.start_line,
            )
        seen_func_names.add(func_name)
        raw_nodes.append(_resolve_node_skeleton(skeleton, base_dir))

    return raw_nodes


def _edge_param_names_for_node(node_info: dict[str, Any]) -> list[str]:
    """Return physical names used to infer a parsed node's incoming edges.

    A mapped Polars signature intentionally keeps logical parameter names in
    authored code.  Its decorator records the current physical edge names, so
    implicit edge reconstruction must apply that mapping before matching node
    identifiers.  Instances keep their existing semantics: their signature is
    already physical and ``inputMapping`` describes the referenced original.
    """
    logical_params = list(node_info.get("edge_param_names", node_info["param_names"]))
    config = node_info.get("config", {})
    input_mapping = config.get("inputMapping") if isinstance(config, dict) else None
    if (
        node_info.get("node_type") != NodeType.POLARS
        or not isinstance(config, dict)
        or "instanceOf" in config
        or input_mapping is None
    ):
        return logical_params
    if not isinstance(input_mapping, dict):
        raise ConfigError(
            "inputMapping must be an object mapping logical input names to "
            "current edge input names.",
            input_mapping=input_mapping,
        )

    edge_params = [input_mapping.get(name, name) for name in logical_params]
    resolved_logical_params = resolve_input_mapping_names(edge_params, input_mapping)
    if resolved_logical_params != logical_params:
        raise ConfigError(
            "inputMapping logical names must match the Polars function's "
            "positional input parameters.",
            logical_params=logical_params,
            resolved_logical_params=resolved_logical_params,
        )
    return edge_params


def _build_edges(
    raw_nodes: list[dict],
    explicit_connect_pairs: list[tuple[str, str, str | None, str | None]],
) -> list[GraphEdge]:
    """Build GraphEdge models from explicit connect() calls and implicit param-name matching.

    Port metadata from each four-field tuple is lifted onto the corresponding
    React Flow handle fields.
    """
    node_names = {n["func_name"] for n in raw_nodes}
    edges: list[GraphEdge] = []
    explicit_edges: set[tuple[str, str]] = set()

    for src, tgt, source_port, target_port in explicit_connect_pairs:
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
    seen_implicit: set[tuple[str, str]] = set()
    for node_info in raw_nodes:
        edge_params = _edge_param_names_for_node(node_info)

        for param in edge_params:
            if param in node_names and param != node_info["func_name"]:
                pair = (param, node_info["func_name"])
                # A duplicated parameter name (reachable via the regex
                # fallback, e.g. ``def f(a, a)``) would otherwise emit two
                # GraphEdges sharing the same id ``e_a_f``. Dedupe here so the
                # edge id stays unique.
                if pair not in explicit_edges and pair not in seen_implicit:
                    seen_implicit.add(pair)
                    edges.append(
                        GraphEdge(
                            id=f"e_{pair[0]}_{pair[1]}",
                            source=pair[0],
                            target=pair[1],
                        )
                    )

    return edges


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
