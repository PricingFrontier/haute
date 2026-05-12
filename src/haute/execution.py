"""Internal execution-engine facade.

Application layers should import execution helpers from this module instead
of reaching into ``haute._execute_lazy`` or underscore re-exports from
``haute.graph_utils``.  The implementation remains deliberately thin: it
keeps one stable internal boundary while the engine underneath continues to
evolve.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph, _Frame
from haute.projection import (
    AllExceptColumns,
    ProjectionPlan,
    ProjectionRequest,
    compute_prepared_plan,
    ratebook_factor_required_columns,
    strict_projection_required,
)
from haute.projection import (
    plan as _plan_projection,
)

__all__ = [
    "AllExceptColumns",
    "LazyExecutionResult",
    "ProjectionPlan",
    "ProjectionRequest",
    "build_linear_execution_chain_functions",
    "execute_lazy_graph",
    "plan_prepared_execution_strategy",
    "plan_execution_strategy",
    "prune_source_switch_edges",
    "ratebook_factor_required_columns",
]

LazyExecutionResult = tuple[dict[str, _Frame], list[str], dict[str, list[str]], dict[str, str]]


def plan_execution_strategy(
    request: ProjectionRequest,
    *,
    execution_context: ExecutionContext | None = None,
) -> ProjectionPlan:
    """Plan projection/streaming strategy through the execution facade.

    This is intentionally thin today: the projection planner remains the
    source of truth, while application layers get one stable execution-engine
    entry point that can grow as strategy selection broadens.
    """
    projection_plan = _plan_projection(request)
    if execution_context is not None:
        execution_context.projection_plan = projection_plan
    return projection_plan


def plan_prepared_execution_strategy(
    order: list[str],
    children_of: Mapping[str, list[str]],
    node_map: Mapping[str, GraphNode],
    *,
    profile: ExecutionProfile,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    execution_context: ExecutionContext | None = None,
) -> ProjectionPlan:
    """Plan projection/streaming strategy for an already prepared graph."""
    projection_plan = compute_prepared_plan(
        order,
        children_of,
        dict(node_map),
        required_columns_by_node=required_columns_by_node,
        strict_projection=strict_projection_required(profile, required_columns_by_node),
    )
    if execution_context is not None:
        execution_context.projection_plan = projection_plan
    return projection_plan


def execute_lazy_graph(
    graph: PipelineGraph,
    build_node_fn: Callable[..., Any],
    *,
    target_node_id: str | None = None,
    preamble_ns: dict[str, Any] | None = None,
    source: str = "live",
    checkpoint_dir: Path | None = None,
    enforce_contracts: bool = False,
    preserve_node_ids: set[str] | frozenset[str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    execution_context: ExecutionContext | None = None,
    source_by_node: Mapping[str, str] | None = None,
) -> LazyExecutionResult:
    """Execute a graph lazily through the shared production engine."""
    from haute._execute_lazy import _execute_lazy

    return _execute_lazy(
        graph,
        build_node_fn,
        target_node_id=target_node_id,
        preamble_ns=preamble_ns,
        source=source,
        checkpoint_dir=checkpoint_dir,
        enforce_contracts=enforce_contracts,
        preserve_node_ids=preserve_node_ids,
        required_columns_by_node=required_columns_by_node,
        execution_context=execution_context,
        source_by_node=source_by_node,
    )


def prune_source_switch_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    source: str,
) -> list[GraphEdge]:
    """Return graph edges pruned to the active source-switch branch."""
    from haute._execute_lazy import _prune_live_switch_edges

    return _prune_live_switch_edges(edges, node_map, source)


def build_linear_execution_chain_functions(
    graph: PipelineGraph,
    build_node_fn: Callable[..., Any],
    *,
    target_node_id: str,
    base_node_id: str,
    chain_node_ids: Iterable[str],
    preamble_ns: dict[str, Any] | None = None,
    routing_source: str = "batch",
    build_source: str = "live",
    required_output_columns_by_node: Mapping[str, frozenset[str] | set[str] | None] | None = None,
    reuse_model_score_functions: bool = False,
    execution_profile: ExecutionProfile | None = None,
) -> dict[str, tuple[Callable[..., Any], bool]]:
    """Build executable functions for a single-parent chain.

    Chunked auto-range executes the expensive scenario-expanded chain one
    chunk at a time.  This helper makes that shape explicit at the execution
    boundary: graph routing is prepared once, then the requested chain is
    rewired so its first node consumes the already-produced base chunk.
    """
    chain_ids = list(chain_node_ids)
    if not chain_ids:
        return {}

    from haute._execute_lazy import _build_funcs, _prepare_graph

    node_map, _order, _parents_of, id_to_name = _prepare_graph(
        graph,
        target_node_id,
        source=routing_source,
    )
    missing = [node_id for node_id in (base_node_id, *chain_ids) if node_id not in node_map]
    if missing:
        raise ValueError(
            "Linear execution chain references node IDs that are not in the prepared graph: "
            f"{missing}"
        )

    chain_parents: dict[str, list[str]] = {}
    parent_id = base_node_id
    for chain_id in chain_ids:
        chain_parents[chain_id] = [parent_id]
        parent_id = chain_id

    reuse_loaded_model_by_node = (
        {
            chain_id: True
            for chain_id in chain_ids
            if node_map[chain_id].data.nodeType == NodeType.MODEL_SCORE
        }
        if reuse_model_score_functions
        else None
    )
    return _build_funcs(
        chain_ids,
        node_map,
        chain_parents,
        id_to_name,
        graph.parents_of,
        build_node_fn,
        preamble_ns=preamble_ns,
        source=build_source,
        required_output_columns_by_node=required_output_columns_by_node,
        reuse_loaded_model_by_node=reuse_loaded_model_by_node,
        execution_profile=execution_profile,
    )
