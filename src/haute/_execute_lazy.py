"""Lazy and eager graph execution — shared by executor, trace, and scorer."""

from __future__ import annotations

import contextlib
import gc
import re
import time
from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

import polars as pl

import haute.execution as execution_facade
import haute.projection as projection_planner
from haute._builders import _passthrough_fn
from haute._contracts import Contract, get_column_contract
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._graph_utils import resolve_orig_source_names, upstream_node_ids
from haute._logging import get_logger
from haute._polars_utils import _malloc_trim, bounded_sink, streaming_collect
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.errors import ContractMismatchError, SchemaMismatchError

logger = get_logger(component="execute")


def _resolve_graph_paths(graph: PipelineGraph) -> PipelineGraph:
    """Resolve project/pipeline-relative file paths before building node functions."""
    return execution_facade.canonical_dataframe_execution_graph(graph)


# ---------------------------------------------------------------------------
# Column contract enforcement
# ---------------------------------------------------------------------------


def _compute_boundary_check_exceptions() -> tuple[type[BaseException], ...]:
    """Exception classes the boundary contract check treats as recoverable.

    We only catch classes that describe genuine "can't resolve the
    contract right now" conditions — bad config, missing files, MLflow
    reachability.  Programmer bugs (``AttributeError``, ``TypeError``,
    ``KeyError``) propagate so they aren't silently masked.

    Narrowed deliberately:

    * ``RuntimeError`` is **not** included.  The
      ``"Persistently corrupt model artifact"`` ``RuntimeError`` raised by
      ``_load_with_bounded_retry`` is a real infrastructure problem the
      operator must see — swallowing it at contract-check time and
      falling back to opaque hides the failure until the node itself
      runs, by which point the log signal is buried under whatever
      follow-on noise the rewrap produced.
    * ``ImportError`` is **not** included.  A missing optional backend
      (catboost / rustystats) is a deploy-configuration bug and should
      surface loudly at the first site that notices it, not be silently
      downgraded to an opaque contract.

    MLflow's ``MlflowException`` covers the legitimate "tracking store
    unreachable" case and is included when the dep is importable.
    """
    from haute.errors import ConfigError

    exc_types: list[type[BaseException]] = [ConfigError, OSError]
    try:
        from mlflow.exceptions import MlflowException  # type: ignore[import-untyped]

        exc_types.append(MlflowException)
    except ImportError:
        pass
    return tuple(exc_types)


def _is_boundary_check_exception(exc: BaseException) -> bool:
    """Return whether *exc* should degrade contract checking to opaque."""
    from haute.errors import ConfigError

    if isinstance(exc, (ConfigError, OSError)):
        return True
    try:
        from mlflow.exceptions import MlflowException  # type: ignore[import-untyped]
    except ImportError:
        return False
    return isinstance(exc, MlflowException)


def _effective_contract(node: GraphNode) -> Contract:
    """Return the effective contract for a node at boundary-check time.

    Combines the builder-derived contract with any user-declared
    contract on the node's config so the executor has a single answer
    to "what columns does this node read / produce?".

    User-declared sides override the builder when they are concrete
    (non-None).  This lets a user tighten an opaque POLARS contract to
    a concrete set; the reverse — a user declaring opaque on top of a
    concrete builder contract — is accepted silently because the parser
    has already cross-checked against ``get_column_contract``.

    If the builder contract raises (MLflow unreachable, config mis-set
    in a way only the builder knows about), the executor treats the
    node as opaque rather than failing the whole run: the runtime path
    for such nodes is typically ``_passthrough_fn`` and the caller will
    still get the original error on the direct ``_model_score_columns``
    call path that the loud-errors suite exercises.  Silencing here is
    scoped strictly to the boundary check; it does not hide the
    configuration issue elsewhere in the system.
    """
    from haute.errors import ConfigError

    try:
        builder = Contract.from_tuple(get_column_contract(node.data.nodeType, node.data.config))
    except Exception as exc:
        if not _is_boundary_check_exception(exc):
            raise
        # Contract resolution for MODEL_SCORE etc. may touch MLflow /
        # external stores.  A transient or deploy-mode lookup failure
        # (ConfigError, OSError, MLflow REST) must not prevent the
        # pipeline from running — the fn builder path has its own
        # error reporting and will surface the real problem when the
        # node actually executes.  We fall back to opaque so the
        # boundary check is skipped for this node; the actual node
        # code path still runs and still fails loudly via whichever
        # error it has always produced.  Programmer errors
        # (AttributeError / TypeError / KeyError) propagate.
        if not isinstance(exc, ConfigError):
            logger.debug(
                "effective_contract_unresolved",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                error=repr(exc),
            )
        builder = Contract.opaque()
    return projection_planner.overlay_declared_contract(node, builder)


def _assert_inputs_satisfy_contract(
    node: GraphNode,
    contract: Contract,
    upstream_columns: frozenset[str],
) -> None:
    """Raise ``ContractMismatchError`` if *upstream_columns* is missing
    any column the node's contract says it reads.

    No-op when the contract's input side is opaque (``None``).
    """
    if contract.inputs is None:
        return
    missing = contract.inputs - upstream_columns
    if not missing:
        return
    raise ContractMismatchError(
        "Input columns required by the node's contract are missing from the upstream frame.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        missing=sorted(missing),
        extra=sorted(upstream_columns - contract.inputs),
        declared_inputs=sorted(contract.inputs),
        upstream_columns=sorted(upstream_columns),
    )


def _assert_outputs_satisfy_contract(
    node: GraphNode,
    contract: Contract,
    output_columns: frozenset[str],
) -> None:
    """Raise ``ContractMismatchError`` if *output_columns* is missing
    any column the node's contract promised to produce.

    We check ⊇ (outputs must be present) rather than == because
    pass-through style nodes legitimately carry additional columns
    through from their input.  A declared output that is absent is a
    bug (typo or buggy user code); an extra column is expected.

    No-op when the contract's output side is opaque (``None``).
    """
    if contract.outputs is None:
        return
    missing = contract.outputs - output_columns
    if not missing:
        return
    raise ContractMismatchError(
        "Output columns promised by the node's contract are missing from the node's result.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        missing=sorted(missing),
        extra=sorted(output_columns - contract.outputs),
        declared_outputs=sorted(contract.outputs),
        observed_columns=sorted(output_columns),
    )


def _should_check_contract(contract: Contract) -> bool:
    """Return ``True`` iff either side of *contract* is concrete.

    A fully-opaque contract cannot be disproven, so skipping the check
    saves the per-node column-set computation entirely.  This matters
    for the <5% overhead bound when a pipeline is dominated by opaque
    nodes (user polars transforms).
    """
    return contract.inputs is not None or contract.outputs is not None


def _normalise_required_columns_by_node(
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None,
    order: list[str],
) -> dict[str, set[str] | projection_planner.AllExceptColumns]:
    """Validate caller-provided projection seeds for concrete node outputs."""
    return projection_planner.normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )


# ---------------------------------------------------------------------------
# Checkpoint projection — backward column analysis
# ---------------------------------------------------------------------------


class _ProjectionPlan(NamedTuple):
    """Column projection needs at nodes and parent-specific fan-in edges."""

    needed_by_node: dict[str, set[str] | None]
    edge_demands: dict[tuple[str, str], set[str] | None]


def _compat_projection_plan(
    public_plan: projection_planner.ProjectionPlan,
) -> _ProjectionPlan:
    return _ProjectionPlan(
        needed_by_node={
            node_id: None if columns is None else set(columns)
            for node_id, columns in public_plan.needed_by_node.items()
        },
        edge_demands={
            edge: None if columns is None else set(columns)
            for edge, columns in public_plan.edge_demands.items()
        },
    )


def _strict_projection_for_context(
    execution_context: ExecutionContext | None,
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns],
) -> bool:
    """Return whether projection-impossible cases should fail loudly."""
    return execution_context is not None and projection_planner.strict_projection_required(
        execution_context.profile,
        required_columns_by_node,
    )


def _compute_projection_plan(
    order: list[str],
    children_of: dict[str, list[str]],
    node_map: dict[str, GraphNode],
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None = None,
    *,
    strict_projection: bool = False,
) -> _ProjectionPlan:
    """Compatibility wrapper around the public projection planner."""
    public_plan = projection_planner.compute_prepared_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node=required_columns_by_node,
        strict_projection=strict_projection,
    )
    return _compat_projection_plan(public_plan)


def _compute_needed_columns(
    order: list[str],
    children_of: dict[str, list[str]],
    node_map: dict[str, GraphNode],
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None = None,
    *,
    strict_projection: bool = False,
) -> dict[str, set[str] | None]:
    """Return per-node output needs from the full projection plan."""
    return _compute_projection_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node=required_columns_by_node,
        strict_projection=strict_projection,
    ).needed_by_node


# ---------------------------------------------------------------------------
# Adaptive checkpoint strategy
# ---------------------------------------------------------------------------

# Number of checkpoints between gc.collect() + _malloc_trim() calls.
# Polars objects use Rust Arc refcounting and are freed immediately on
# ``del``; Python gc.collect() only helps with cyclic garbage (rare here).
# Batching avoids the overhead of scanning all Python objects per checkpoint.
_GC_BATCH_INTERVAL = 3


class _CheckpointAction(StrEnum):
    """What to do at a potential checkpoint boundary."""

    SKIP = "skip"
    """Keep the LazyFrame as-is — no materialization needed."""

    COLLECT_LAZY = "collect_lazy"
    """Materialize in RAM via ``collect().lazy()`` to break plan
    duplication without disk I/O.  Only used when the estimated
    intermediate fits comfortably in available memory."""

    PARQUET = "parquet"
    """Sink to a temp parquet file and replace with ``scan_parquet``.
    The safest option — frees RAM and isolates the query plan."""


def _checkpoint_decision(
    nid: str,
    is_source: bool,
    n_parents: int,
    n_children: int,
    feeds_join: bool,
    node_map: dict[str, GraphNode],
    scenario: str,
) -> _CheckpointAction:
    """Decide whether and how to checkpoint a node's output.

    Uses the same three structural triggers as before (joins, fan-outs,
    join-feeders) but skips MODEL_SCORE nodes in batch mode because
    the batched scorer already sinks to temp parquet and returns
    ``scan_parquet(scored_path)`` — an implicit checkpoint.  Adding
    another parquet round-trip on top is pure waste.
    """
    if is_source:
        return _CheckpointAction.SKIP

    needs_checkpoint = n_parents > 1 or n_children > 1 or feeds_join
    if not needs_checkpoint:
        return _CheckpointAction.SKIP

    # MODEL_SCORE in batch mode already returns scan_parquet — skip.
    node = node_map.get(nid)
    if node is not None and node.data.nodeType == NodeType.MODEL_SCORE and scenario != "live":
        return _CheckpointAction.SKIP

    return _CheckpointAction.PARQUET


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _apply_column_renames(
    frame: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
) -> pl.LazyFrame | pl.DataFrame:
    """Apply column renames from *config*'s ``column_renames``.

    ``column_renames`` is a ``dict[str, str]`` mapping original column names
    to new names.  Only renames for columns that actually exist in the frame
    are applied.  A no-op when the dict is absent or empty.
    """
    renames: dict[str, str] | None = config.get("column_renames")
    if not renames:
        return frame

    if isinstance(frame, pl.LazyFrame):
        all_cols = set(frame.collect_schema().names())
    else:
        all_cols = set(frame.columns)

    valid = {old: new for old, new in renames.items() if old in all_cols and old != new}
    if valid:
        return frame.rename(valid)
    return frame


def _apply_selected_columns(
    frame: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
) -> pl.LazyFrame | pl.DataFrame:
    """Filter *frame* to only the columns listed in *config*'s ``selected_columns``.

    If ``selected_columns`` is absent, empty, or names no valid columns the
    frame is returned unchanged.  Only columns that actually exist in the
    frame are kept, and the filter is a no-op when every column is selected
    (avoids an unnecessary projection).
    """
    sel_cols: list[str] | None = config.get("selected_columns")
    if not sel_cols:
        return frame

    if isinstance(frame, pl.LazyFrame):
        all_cols = frame.collect_schema().names()
    else:
        all_cols = frame.columns

    seen: set[str] = set()
    valid = []
    for c in sel_cols:
        if c in all_cols and c not in seen:
            valid.append(c)
            seen.add(c)
    if valid and len(valid) < len(all_cols):
        return frame.select(valid)
    return frame


def _assert_simple_join_key_dtypes_compatible(
    node: GraphNode,
    input_ids: list[str],
    input_lfs: list[_Frame],
) -> None:
    """Validate dtype parity for simple inferred multi-parent Polars joins."""
    if node.data.nodeType != NodeType.POLARS or len(input_ids) < 2:
        return
    joins = projection_planner.simple_join_calls_for_parent_inputs(node, input_ids)
    if not joins:
        return

    frame_by_parent = dict(zip(input_ids, input_lfs, strict=True))
    schema_by_parent: dict[str, pl.Schema] = {}

    def _schema(parent_id: str) -> pl.Schema:
        cached = schema_by_parent.get(parent_id)
        if cached is not None:
            return cached
        frame = frame_by_parent[parent_id]
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema = lazy_frame.collect_schema()
        schema_by_parent[parent_id] = schema
        return schema

    for join in joins:
        left_schema = _schema(join.left_parent)
        right_schema = _schema(join.right_parent)
        left_columns = set(left_schema.names())
        right_columns = set(right_schema.names())
        for left_key, right_key in join.key_pairs:
            if left_key not in left_columns:
                raise ContractMismatchError(
                    "Join key is missing from the parent frame.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    parent_id=join.left_parent,
                    missing=[left_key],
                    join_key=left_key,
                    parent_columns=sorted(left_columns),
                )
            if right_key not in right_columns:
                raise ContractMismatchError(
                    "Join key is missing from the parent frame.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    parent_id=join.right_parent,
                    missing=[right_key],
                    join_key=right_key,
                    parent_columns=sorted(right_columns),
                )
            left_dtype = left_schema[left_key]
            right_dtype = right_schema[right_key]
            if left_dtype != right_dtype:
                raise SchemaMismatchError(
                    "Join key dtype mismatch between parent frames.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    left_parent=join.left_parent,
                    right_parent=join.right_parent,
                    left_key=left_key,
                    right_key=right_key,
                    left_dtype=str(left_dtype),
                    right_dtype=str(right_dtype),
                )


def _prune_live_switch_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    source: str,
) -> list[GraphEdge]:
    """Remove edges to live_switch nodes from inputs inactive for *source*.

    A live_switch node's config contains ``input_scenario_map`` which maps
    each input name to the scenario it serves.  Only edges from inputs
    matching the active source are kept; the unused branch is pruned so
    it is neither executed nor shown in profilers.
    """
    return projection_planner.prune_live_switch_edges(edges, node_map, source)


def _prepare_graph(
    graph: PipelineGraph,
    target_node_id: str | None = None,
    source: str = "live",
) -> tuple[
    dict[str, GraphNode],  # node_map
    list[str],  # order (topo-sorted node IDs)
    dict[str, list[str]],  # parents_of
    dict[str, str],  # id_to_name
]:
    """Shared graph preparation: filter, topo-sort, and build lookups.

    Returns (node_map, order, parents_of, id_to_name).
    """
    prepared = projection_planner.prepare_graph(
        graph,
        target_node_id,
        source=source,
    )
    return prepared.node_map, prepared.order, prepared.parents_of, prepared.id_to_name


def _execute_lazy(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
    checkpoint_dir: Path | None = None,
    enforce_contracts: bool = False,
    preserve_node_ids: set[str] | frozenset[str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None = None,
    execution_context: ExecutionContext | None = None,
    source_by_node: Mapping[str, str] | None = None,
    dataframe_cache_request: execution_facade.DataFrameExecutionCacheRequest | None = None,
) -> tuple[dict[str, _Frame], list[str], dict[str, list[str]], dict[str, str]]:
    """Execute a graph lazily and return per-node LazyFrames.

    Used by execute_sink (batch writes) and score_graph (deploy scoring)
    where Polars can optimise the full lazy plan end-to-end.
    Interactive paths (preview, trace) use eager execution with caching
    instead — see executor._eager_execute and trace.execute_trace.

    Args:
        graph: React Flow graph with "nodes" and "edges".
        build_node_fn: Function (node_dict, source_names) -> (name, fn, is_source).
        target_node_id: If set, only execute ancestors of this node.
        source: Active execution source (``"live"`` = eager scoring).
        checkpoint_dir: If set, multi-input nodes (joins) and fan-out
            nodes (>1 downstream consumer) are checkpointed to parquet
            files in this directory and replaced with ``scan_parquet``
            references.  This breaks both chained-join memory
            accumulation and plan duplication across branches
            (GitHub pola-rs/polars#24206).
        preserve_node_ids: Non-source intermediate outputs that must remain
            available to the caller after their final downstream consumer has
            executed. Optimiser ratebook solves use this for the selected
            banding source side input.
        required_columns_by_node: Optional exact output-column demand for
            caller-consumed nodes.  These seeds supplement concrete
            descendant-derived projection for the named nodes, and replace
            opaque descendant demand so callers that consume a non-OUTPUT
            node directly can avoid terminal "all columns" propagation.
        source_by_node: Optional per-node source override passed only to node
            builders.  Graph pruning still uses ``source`` so live-switch
            routing remains stable while selected nodes, such as deploy
            modelScore, can opt into batch execution.
        dataframe_cache_request: Optional request describing node outputs that
            may be materialized to and reused from the shared backend dataframe
            cache.  Cached hits seed the lazy output map, letting execution skip
            covered upstream lineage while still building any uncached downstream
            target nodes.
        enforce_contracts: When ``True`` (see ``executor.ENFORCE_CONTRACTS``
            for the default), assert declared column contracts at each
            node boundary via ``.collect_schema()``.  Polars computes
            schemas without executing the query, so this stays cheap.
            Production code paths (batch sink, deploy scoring, training,
            optimiser) run through here — enforcement on the lazy path
            is what makes contract coverage real end-to-end.

    Returns:
        (lazy_outputs, order, parents_of, id_to_name)
    """
    graph = _resolve_graph_paths(graph)
    preserved_outputs = frozenset(preserve_node_ids or ())
    node_source_overrides = dict(source_by_node or {})
    if execution_context is not None:
        execution_context.checkpoint(label="lazy_start")
    node_map, order, parents_of, id_to_name = _prepare_graph(
        graph,
        target_node_id,
        source=source,
    )
    normalised_required_columns = _normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )
    cache_request = dataframe_cache_request

    # Count downstream consumers per node so we can checkpoint fan-out
    # points (nodes whose output feeds >1 consumer).  Without this,
    # Polars duplicates the entire upstream plan for each branch —
    # e.g. a 38 GB JSONL scan runs twice when two siblings share a parent.
    children_count: dict[str, int] = {nid: 0 for nid in order}
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            if pid in children_count:
                children_count[pid] += 1
                children_of[pid].append(nid)

    cached_seed_outputs: dict[str, _Frame] = {}
    skip_cache_covered_nodes: set[str] = set()
    cache_backed_node_ids: set[str] = set()
    cache_hit_rejected_node_ids: set[str] = set()
    if cache_request is not None:
        unknown_cache_nodes = sorted(
            node_id for node_id in cache_request.keys_by_node if node_id not in node_map
        )
        if unknown_cache_nodes:
            raise ValueError(
                "Dataframe cache request references node IDs that are not in the "
                f"prepared execution graph: {unknown_cache_nodes}"
            )

        effective_profile = (
            execution_context.profile
            if execution_context is not None
            else ExecutionProfile.LAZY_SINK
        )
        effective_cache_profile = execution_facade.dataframe_execution_cache_profile(
            effective_profile
        )

        def _merge_cache_required_columns(
            runtime_demand: set[str] | projection_planner.AllExceptColumns | None,
            cache_key: execution_facade.DataFrameExecutionCacheKey,
        ) -> set[str] | projection_planner.AllExceptColumns | None:
            if isinstance(runtime_demand, projection_planner.AllExceptColumns):
                return runtime_demand
            merged = set(runtime_demand or ())
            merged.update(cache_key.required_columns)
            return merged if merged else runtime_demand

        def _required_columns_for_cached_seed(
            demand: set[str] | projection_planner.AllExceptColumns | None,
        ) -> set[str]:
            if demand is None:
                return set()
            if isinstance(demand, projection_planner.AllExceptColumns):
                return set(demand.required_columns)
            return set(demand)

        cache_required_columns: dict[str, set[str] | projection_planner.AllExceptColumns] = dict(
            normalised_required_columns
        )
        for node_id, cache_key in cache_request.keys_by_node.items():
            merged_demand = _merge_cache_required_columns(
                cache_required_columns.get(node_id),
                cache_key,
            )
            if merged_demand is not None:
                cache_required_columns[node_id] = merged_demand
        cache_policy = execution_facade.dataframe_lazy_execution_policy(
            target_node_id=target_node_id,
            source_by_node=node_source_overrides,
            required_columns_by_node=cache_required_columns,
            preserve_node_ids=preserved_outputs,
            enforce_contracts=enforce_contracts,
            preamble_ns_supplied=preamble_ns is not None,
        )
        cache_policy_fingerprint = execution_facade.dataframe_execution_policy_fingerprint(
            cache_policy
        )
        cache_key_memo = execution_facade.GraphFingerprintMemo()
        for node_id, cache_key in cache_request.keys_by_node.items():
            effective_source = source or "live"
            if cache_key.source != effective_source:
                raise ValueError(
                    "Dataframe cache key source does not match lazy execution source "
                    f"(node_id={node_id!r}, key.source={cache_key.source!r}, "
                    f"execution.source={effective_source!r})"
                )
            if cache_key.profile != effective_cache_profile:
                raise ValueError(
                    "Dataframe cache key profile does not match lazy execution profile "
                    f"(node_id={node_id!r}, key.profile={cache_key.profile!r}, "
                    f"execution.profile={effective_cache_profile!r})"
                )
            if cache_key.execution_policy_fingerprint != cache_policy_fingerprint:
                raise ValueError(
                    "Dataframe cache key execution policy does not match lazy execution "
                    f"policy (node_id={node_id!r})"
                )
            demand = cache_required_columns.get(node_id)
            required_columns = (
                None if isinstance(demand, projection_planner.AllExceptColumns) else demand
            )
            expected_key = execution_facade.dataframe_execution_cache_key(
                graph,
                node_id=node_id,
                namespace=cache_key.namespace,
                source=effective_source,
                profile=effective_cache_profile,
                input_fingerprint=cache_key.input_fingerprint,
                required_columns=required_columns,
                extra_keys=cache_key.extra_keys,
                execution_policy=cache_policy,
                memo=cache_key_memo,
            )
            if cache_key != expected_key:
                raise ValueError(
                    "Dataframe cache key does not match the current lazy execution "
                    f"graph and policy (node_id={node_id!r})"
                )
            # Broken/missing cache entries are auto-evicted by ``cache.get``
            # which then returns None; no explicit error handling needed.
            cached_entry = cache_request.cache.get(cache_key)
            if cached_entry is not None:
                required_for_seed = _required_columns_for_cached_seed(
                    cache_required_columns.get(node_id)
                )
                missing_for_seed = sorted(required_for_seed - set(cached_entry.columns))
                if missing_for_seed:
                    logger.warning(
                        "dataframe_execution_cache_hit_missing_required_columns",
                        node_id=node_id,
                        missing=missing_for_seed,
                    )
                    cache_hit_rejected_node_ids.add(node_id)
                else:
                    cached_lf = cache_request.cache.scan(cache_key)
                    if cached_lf is not None:
                        cached_seed_outputs[node_id] = cached_lf
                        cache_backed_node_ids.add(node_id)

        cache_covers_downstream: dict[str, bool] = {}
        for nid in reversed(order):
            if nid in cached_seed_outputs:
                cache_covers_downstream[nid] = True
            elif nid in preserved_outputs:
                cache_covers_downstream[nid] = False
            else:
                children = children_of.get(nid, [])
                cache_covers_downstream[nid] = bool(children) and all(
                    cache_covers_downstream.get(child_id, False) for child_id in children
                )
        skip_cache_covered_nodes = {
            node_id
            for node_id, covered in cache_covers_downstream.items()
            if covered and node_id not in cached_seed_outputs
        }

    # Backward column analysis: compute the minimal set of columns
    # needed at each node's output so checkpoints can project away
    # unneeded columns before writing to parquet.  Batch MODEL_SCORE
    # nodes also consume this demand locally so their internal temp
    # parquet write can avoid unused passthrough columns even when the
    # outer checkpoint layer skips model-score nodes.
    strict_projection = _strict_projection_for_context(
        execution_context,
        normalised_required_columns,
    )
    needs_projection_analysis = (
        checkpoint_dir is not None
        or source != "live"
        or bool(normalised_required_columns)
        or strict_projection
    )
    public_projection_plan: projection_planner.ProjectionPlan | None = None
    if needs_projection_analysis:
        public_projection_plan = execution_facade.plan_prepared_execution_strategy(
            order,
            children_of,
            node_map,
            profile=(
                execution_context.profile
                if execution_context is not None
                else ExecutionProfile.PREVIEW_EAGER
            ),
            required_columns_by_node=normalised_required_columns,
            execution_context=execution_context,
        )
    projection_plan: _ProjectionPlan | None = (
        _compat_projection_plan(public_projection_plan)
        if public_projection_plan is not None
        else None
    )
    needed_cols: dict[str, set[str] | None] = (
        projection_plan.needed_by_node if projection_plan is not None else {}
    )
    edge_demands: dict[tuple[str, str], set[str] | None] = (
        projection_plan.edge_demands if projection_plan is not None else {}
    )

    # Full parent lookup from ALL edges for instance resolution
    all_parents = graph.parents_of

    # Build executable functions — delegates to _build_funcs with
    # row_limit=None (lazy path never caps source output).
    with (
        execution_context.stage("lazy_build_functions")
        if execution_context is not None
        else contextlib.nullcontext()
    ):
        builder_needed_cols = projection_planner.builder_required_output_columns_by_node(
            node_map,
            needed_cols,
            preserve_eager_model_score_inputs=False,
        )
        build_order = [
            node_id
            for node_id in order
            if node_id not in skip_cache_covered_nodes and node_id not in cached_seed_outputs
        ]
        funcs = _build_funcs(
            build_order,
            node_map,
            parents_of,
            id_to_name,
            all_parents,
            build_node_fn,
            row_limit=None,
            preamble_ns=preamble_ns,
            source=source,
            source_by_node=node_source_overrides,
            required_output_columns_by_node=builder_needed_cols,
            execution_profile=(
                execution_context.profile if execution_context is not None else None
            ),
        )

    # Execute - all intermediate results stay lazy
    lazy_outputs: dict[str, _Frame] = {}

    # Separate mutable counter for tracking remaining downstream consumers.
    # Decremented at checkpoint time so we know when a parent's LazyFrame
    # can be safely deleted (freeing Polars/Rust Arrow buffers).
    remaining: dict[str, int] = dict(children_count)

    # Batch gc.collect() calls — Polars objects use Rust Arc refcounting
    # and are freed immediately on ``del``.  gc.collect() only helps with
    # cyclic Python garbage (rare here) and adds 50-200 ms per call.
    checkpoints_since_gc = 0

    def _release_consumed_parents(nid: str) -> None:
        # Drop parent LazyFrame refs that have no remaining consumers
        # downstream — lets Polars/Rust release the backing buffers.
        # Source nodes are kept: they hold cheap scan_* references and
        # callers may need them (e.g. optimiser extracting banding factors).
        # Cache-backed nodes are likewise kept: they hold scan_parquet
        # references against artifacts the downstream plan composes from,
        # and dropping the LazyFrame would let the cache release the
        # underlying file before the downstream collect.
        for pid in parents_of.get(nid, []):
            remaining[pid] -= 1
            _, pid_is_source = funcs.get(pid, (None, False))
            if (
                remaining[pid] <= 0
                and pid in lazy_outputs
                and not pid_is_source
                and pid not in preserved_outputs
                and pid not in cache_backed_node_ids
            ):
                del lazy_outputs[pid]

    # Per-node column sets used by the boundary contract checks.  Polars
    # computes schema without executing the query, so collect_schema()
    # is cheap; caching keeps repeated lookups free when the same
    # upstream feeds multiple consumers.
    column_cache: dict[str, frozenset[str]] = {}

    def _schema_names_of(frame: pl.LazyFrame | pl.DataFrame) -> list[str]:
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        return lazy_frame.collect_schema().names()

    def _columns_of(frame: pl.LazyFrame | pl.DataFrame) -> frozenset[str]:
        return frozenset(_schema_names_of(frame))

    def _apply_edge_projection(
        child_id: str,
        parent_id: str,
        frame: _Frame,
        *,
        runtime_demand: set[str] | None = None,
    ) -> tuple[_Frame, frozenset[str] | None]:
        demand = runtime_demand
        if demand is None and (parent_id, child_id) not in edge_demands:
            return frame, None
        if demand is None:
            demand = edge_demands[(parent_id, child_id)]
        if demand is None:
            return frame, None

        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema_cols = _schema_names_of(lazy_frame)
        schema_set = set(schema_cols)
        missing = demand - schema_set
        if missing:
            raise ContractMismatchError(
                "Columns required by a fan-in projection contract are "
                "missing from the parent frame.",
                node_id=child_id,
                parent_id=parent_id,
                missing=sorted(missing),
                required_columns=sorted(demand),
                parent_columns=sorted(schema_set),
            )

        ordered = [column for column in schema_cols if column in demand]
        return lazy_frame.select(ordered), frozenset(ordered)

    def _runtime_simple_join_edge_demands(
        child_id: str,
        input_ids: list[str],
        input_lfs: list[_Frame],
    ) -> dict[str, set[str]]:
        """Infer per-parent projection for a simple uncontracted Polars join.

        Static planning intentionally treats contract-free fan-in Polars code as
        an unprojected streaming boundary because it lacks parent schemas.  Once
        the lazy parents exist, their schemas are available without collecting
        data, so common joins can be narrowed safely before the join executes.
        If any requested output cannot be mapped mechanically to join inputs, we
        keep the full-width boundary instead of guessing.
        """
        if any((parent_id, child_id) in edge_demands for parent_id in input_ids):
            return {}
        node = node_map[child_id]
        if node.data.nodeType != NodeType.POLARS or len(input_ids) != 2:
            return {}
        projection = needed_cols.get(child_id)
        if projection is None:
            return {}
        joins = projection_planner.simple_join_calls_for_parent_inputs(node, input_ids)
        if len(joins) != 1:
            return {}
        join = joins[0]
        if {join.left_parent, join.right_parent} != set(input_ids):
            return {}
        if join.how not in {"inner", "left", "semi", "anti"} or not join.key_pairs:
            return {}
        if join.suffix == "":
            return {}

        frame_by_parent = dict(zip(input_ids, input_lfs, strict=True))
        left_schema = set(_schema_names_of(frame_by_parent[join.left_parent]))
        right_schema = set(_schema_names_of(frame_by_parent[join.right_parent]))
        left_keys = {left_key for left_key, _right_key in join.key_pairs}
        right_keys = {right_key for _left_key, right_key in join.key_pairs}
        left_demand: set[str] = set(left_keys)
        right_demand: set[str] = set(right_keys)

        for output_column in projection:
            if output_column in left_keys:
                continue
            mapped = False
            if join.suffix and output_column.endswith(join.suffix):
                original = output_column[: -len(join.suffix)]
                if original and original in right_schema and original in left_schema:
                    if output_column in left_schema or output_column in right_schema:
                        return {}
                    # Keep the left column too so Polars preserves the expected
                    # suffixed right-hand output name.
                    left_demand.add(original)
                    right_demand.add(original)
                    mapped = True
            if mapped:
                continue
            if output_column in left_schema:
                left_demand.add(output_column)
                mapped = True
            if output_column in right_schema and output_column not in left_schema:
                right_demand.add(output_column)
                mapped = True
            if output_column in right_keys:
                right_demand.add(output_column)
                mapped = True
            if not mapped:
                return {}

        return {
            join.left_parent: left_demand,
            join.right_parent: right_demand,
        }

    def _build_lazy_node(nid: str) -> tuple[_Frame, bool, GraphNode]:
        nonlocal public_projection_plan

        fn, is_source = funcs[nid]
        node = node_map[nid]
        contract = _effective_contract(node) if enforce_contracts else None
        check_here = bool(contract) and _should_check_contract(contract)  # type: ignore[arg-type]
        # Builder-wired ``_passthrough_fn`` means the node is in a stub
        # state (MODEL_SCORE without a model, OPTIMISER_APPLY without
        # an artifact).  Its declared contract describes the configured
        # shape the runtime does not produce yet; skip the output check
        # to preserve the "configure later" UX while still enforcing
        # contracts the moment a real function is wired.
        is_passthrough_runtime = fn is _passthrough_fn

        if is_source:
            lf = fn()
        else:
            input_ids = parents_of.get(nid, [])
            missing = [pid for pid in input_ids if pid not in lazy_outputs]
            if missing:
                raise ValueError(
                    f"Node '{nid}' is missing input(s) from: {missing}. "
                    "Upstream node(s) may have failed or not been registered."
                )
            input_lfs = [lazy_outputs[pid] for pid in input_ids]
            if not input_lfs:
                raise ValueError(f"No input data available for node '{nid}'")

            projected_input_lfs: list[_Frame] = []
            projected_input_columns: list[frozenset[str] | None] = []
            runtime_edge_demands = _runtime_simple_join_edge_demands(
                nid,
                input_ids,
                input_lfs,
            )
            if (
                runtime_edge_demands
                and execution_context is not None
                and public_projection_plan is not None
            ):
                public_projection_plan = projection_planner.with_runtime_inferred_streaming_edges(
                    public_projection_plan,
                    child_id=nid,
                    demands_by_parent=runtime_edge_demands,
                )
                execution_context.projection_plan = public_projection_plan
            for input_id, input_lf in zip(input_ids, input_lfs, strict=True):
                projected_lf, projected_cols = _apply_edge_projection(
                    nid,
                    input_id,
                    input_lf,
                    runtime_demand=runtime_edge_demands.get(input_id),
                )
                projected_input_lfs.append(projected_lf)
                projected_input_columns.append(projected_cols)
            input_lfs = projected_input_lfs

            if check_here and contract is not None and contract.inputs is not None:
                upstream_col_sets: list[frozenset[str]] = []
                for upstream_pid, upstream_lf, projected_cols in zip(
                    input_ids,
                    input_lfs,
                    projected_input_columns,
                    strict=True,
                ):
                    upstream_cols: frozenset[str]
                    if projected_cols is not None:
                        upstream_cols = projected_cols
                    else:
                        cached_cols = column_cache.get(upstream_pid)
                        if cached_cols is None:
                            cached_cols = _columns_of(upstream_lf)
                            column_cache[upstream_pid] = cached_cols
                        upstream_cols = cached_cols
                    upstream_col_sets.append(upstream_cols)
                upstream_cols = frozenset().union(*upstream_col_sets)
                _assert_inputs_satisfy_contract(node, contract, upstream_cols)

            if enforce_contracts:
                _assert_simple_join_key_dtypes_compatible(node, input_ids, input_lfs)

            lf = fn(*input_lfs)

        if isinstance(lf, pl.DataFrame):
            lf = lf.lazy()

        # Apply selected_columns filter first (uses pre-rename names),
        # then column renames on the surviving columns.
        lf = _apply_selected_columns(lf, node_map[nid].data.config)
        if isinstance(lf, pl.DataFrame):
            lf = lf.lazy()
        lf = _apply_column_renames(lf, node_map[nid].data.config)
        if isinstance(lf, pl.DataFrame):
            lf = lf.lazy()

        if (
            check_here
            and contract is not None
            and contract.outputs is not None
            and not is_passthrough_runtime
        ):
            out_cols = _columns_of(lf)
            column_cache[nid] = out_cols
            _assert_outputs_satisfy_contract(node, contract, out_cols)

        return lf, is_source, node

    for nid in order:
        if nid in skip_cache_covered_nodes:
            continue
        cached_seed = cached_seed_outputs.get(nid)
        if cached_seed is not None:
            lazy_outputs[nid] = cached_seed
            column_cache[nid] = _columns_of(cached_seed)
            logger.info("dataframe_execution_cache_seed_hit", node_id=nid)
            if execution_context is not None:
                execution_context.checkpoint(label="lazy_dataframe_cache_seed_hit", node_id=nid)
            continue
        if execution_context is not None:
            execution_context.checkpoint(label="before_node", node_id=nid)
        with (
            execution_context.stage("lazy_build", node_id=nid)
            if execution_context is not None
            else contextlib.nullcontext()
        ):
            lf, is_source, node = _build_lazy_node(nid)

        cache_materialized = False
        if cache_request is not None and nid not in cache_hit_rejected_node_ids:
            materialize_cache_key = cache_request.keys_by_node.get(nid)
            if materialize_cache_key is not None:
                with (
                    execution_context.stage("lazy_dataframe_cache_materialize", node_id=nid)
                    if execution_context is not None
                    else contextlib.nullcontext()
                ):
                    try:
                        lazy_frame_for_cache = lf if isinstance(lf, pl.LazyFrame) else lf.lazy()
                        required_for_cache = sorted(materialize_cache_key.required_columns)
                        if required_for_cache:
                            cache_columns = set(_schema_names_of(lazy_frame_for_cache))
                            missing_for_cache = sorted(
                                set(required_for_cache) - cache_columns
                            )
                        else:
                            missing_for_cache = []
                        if missing_for_cache:
                            logger.warning(
                                "dataframe_execution_cache_required_columns_missing_skip",
                                node_id=nid,
                                missing=missing_for_cache,
                            )
                            cached_lf = None
                        else:
                            if required_for_cache:
                                lazy_frame_for_cache = lazy_frame_for_cache.select(
                                    required_for_cache
                                )
                            cached_lf = execution_facade.materialize_lazy_frame_with_cache(
                                lazy_frame_for_cache,
                                cache=cache_request.cache,
                                key=materialize_cache_key,
                                profile=(
                                    execution_context.profile
                                    if execution_context is not None
                                    else ExecutionProfile.LAZY_SINK
                                ),
                                streaming_chunk_size=cache_request.streaming_chunk_size,
                                fast_checkpoint=cache_request.fast_checkpoint,
                            )
                    except execution_facade.CacheArtifactTooLargeError as exc:
                        logger.warning(
                            "dataframe_execution_cache_artifact_too_large_skip",
                            node_id=nid,
                            error=str(exc),
                        )
                    else:
                        if cached_lf is not None:
                            lf = cached_lf
                            cache_materialized = True
                            cache_backed_node_ids.add(nid)
                            column_cache[nid] = _columns_of(lf)
                            _release_consumed_parents(nid)
                            checkpoints_since_gc += 1
                            if checkpoints_since_gc >= _GC_BATCH_INTERVAL:
                                gc.collect()
                                _malloc_trim()
                                checkpoints_since_gc = 0
                            logger.info("dataframe_execution_cache_materialized", node_id=nid)
                            if execution_context is not None:
                                execution_context.checkpoint(
                                    label="after_dataframe_cache_materialize",
                                    node_id=nid,
                                )

        # Adaptive checkpoint to break Polars plan duplication and
        # chained-join memory accumulation (pola-rs/polars#24206).
        #
        # Three structural triggers (joins, fan-outs, join-feeders) are
        # evaluated by _checkpoint_decision which chooses the cheapest
        # safe strategy:
        #   PARQUET      — disk round-trip, safest, frees RAM
        #   COLLECT_LAZY — in-memory materialization, no I/O, breaks
        #                  plan duplication but holds data in RAM
        #   SKIP         — keep the LazyFrame as-is (source nodes,
        #                  batch MODEL_SCORE which already checkpoints
        #                  internally, or nodes that don't need it)
        n_parents = len(parents_of.get(nid, []))
        n_children = children_count.get(nid, 0)
        feeds_join = any(len(parents_of.get(cid, [])) > 1 for cid in children_of.get(nid, []))

        action = _checkpoint_decision(
            nid,
            is_source,
            n_parents,
            n_children,
            feeds_join,
            node_map,
            node_source_overrides.get(nid, source or "live"),
        )

        if (
            not cache_materialized
            and checkpoint_dir is not None
            and action == _CheckpointAction.PARQUET
        ):
            tmp = checkpoint_dir / f"{nid}.parquet"

            # Project to only the columns needed downstream before
            # writing the checkpoint.  This avoids writing (and later
            # re-reading) columns that no downstream node will use —
            # e.g. 100 source columns when the model only needs 8.
            sink_lf = lf if isinstance(lf, pl.LazyFrame) else lf.lazy()
            projection = needed_cols.get(nid)
            if projection is not None:
                schema_cols = sink_lf.collect_schema().names()
                schema_set = set(schema_cols)
                missing = projection - schema_set
                if missing:
                    raise ContractMismatchError(
                        "Checkpoint projection references columns missing "
                        "from the node output schema.",
                        node_id=nid,
                        node_type=node.data.nodeType.value,
                        missing=sorted(missing),
                        required_columns=sorted(projection),
                        output_columns=sorted(schema_set),
                    )
                valid = [c for c in schema_cols if c in projection]
                if valid and len(valid) < len(schema_cols):
                    logger.info(
                        "checkpoint_projection",
                        node_id=nid,
                        total_cols=len(schema_cols),
                        projected_cols=len(valid),
                    )
                    sink_lf = sink_lf.select(valid)
                    column_cache[nid] = frozenset(valid)

            with (
                execution_context.stage("lazy_checkpoint_parquet", node_id=nid)
                if execution_context is not None
                else contextlib.nullcontext()
            ):
                bounded_sink(sink_lf, tmp, fast_checkpoint=True)

            # Drop the old LazyFrame (and any cached Arrow buffers it
            # holds) before replacing with a fresh scan reference.
            del lf
            _release_consumed_parents(nid)

            checkpoints_since_gc += 1
            if checkpoints_since_gc >= _GC_BATCH_INTERVAL:
                gc.collect()
                _malloc_trim()
                checkpoints_since_gc = 0

            lf = pl.scan_parquet(tmp)
            logger.info("checkpoint_parquet", node_id=nid, path=str(tmp))
            if execution_context is not None:
                execution_context.checkpoint(label="after_checkpoint", node_id=nid)

        lazy_outputs[nid] = lf

    return lazy_outputs, order, parents_of, id_to_name


# ---------------------------------------------------------------------------
# Eager execution core — shared by executor (preview) and trace
# ---------------------------------------------------------------------------


def _build_funcs(
    order: list[str],
    node_map: dict[str, GraphNode],
    parents_of: dict[str, list[str]],
    id_to_name: dict[str, str],
    all_parents: dict[str, list[str]],
    build_node_fn: Callable,
    *,
    row_limit: int | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
    source_by_node: Mapping[str, str] | None = None,
    required_output_columns_by_node: Mapping[str, frozenset[str] | set[str] | None] | None = None,
    reuse_loaded_model_by_node: Mapping[str, bool] | None = None,
    execution_profile: ExecutionProfile | None = None,
) -> dict[str, tuple[Callable, bool]]:
    """Build per-node executable functions from the graph.

    Shared between eager and lazy paths.  ``row_limit`` is forwarded to
    ``build_node_fn`` so Databricks sources can push LIMIT into SQL.
    ``preamble_ns`` is a compiled namespace of user-defined helpers from
    the pipeline file's preamble section.
    ``source`` is the active execution source forwarded to build_node_fn.
    ``source_by_node`` overrides that builder source for individual nodes
    without changing graph pruning/source-switch routing.
    ``reuse_loaded_model_by_node`` opts selected modelScore nodes into
    scorer-instance model reuse for chunked callers.
    """
    funcs: dict[str, tuple[Callable, bool]] = {}
    node_source_overrides = source_by_node or {}
    for nid in order:
        src_ids = [pid for pid in parents_of.get(nid, []) if pid in id_to_name]
        src_names = [id_to_name[pid] for pid in src_ids]
        orig_src_names = resolve_orig_source_names(
            node_map[nid],
            node_map,
            all_parents,
            id_to_name,
        )
        node_source = node_source_overrides.get(nid, source)
        _, fn, is_source = build_node_fn(
            node_map[nid],
            source_names=src_names,
            source_ids=src_ids,
            row_limit=row_limit,
            node_map=node_map,
            orig_source_names=orig_src_names,
            upstream_ids=upstream_node_ids(nid, all_parents),
            preamble_ns=preamble_ns,
            source=node_source,
            required_output_columns=(
                required_output_columns_by_node.get(nid)
                if required_output_columns_by_node is not None
                else None
            ),
            reuse_loaded_model=(
                bool(reuse_loaded_model_by_node.get(nid))
                if reuse_loaded_model_by_node is not None
                else False
            ),
            execution_profile=execution_profile.value if execution_profile is not None else None,
        )
        funcs[nid] = (fn, is_source)
    return funcs


def _extract_error_line(exc: Exception) -> int | None:
    """Extract user-code line number from an exception, if available.

    - SyntaxError: use .lineno (already adjusted by _exec_user_code).
    - _user_code_line attr: set by _exec_user_code from the traceback
      for runtime errors like NameError that don't embed line info
      in their message string.
    - Fallback: parse 'line N' from the error message
      (already adjusted by _exec_user_code's regex substitution).
    - Returns None when no line info is available.
    """
    if isinstance(exc, SyntaxError) and exc.lineno is not None:
        return exc.lineno
    user_line: int | None = getattr(exc, "_user_code_line", None)
    if user_line is not None:
        return int(user_line)
    match = re.search(r"\bline (\d+)\b", str(exc))
    if match:
        return int(match.group(1))
    return None


class EagerResult(NamedTuple):
    """Result of eager graph execution."""

    outputs: dict[str, pl.DataFrame | None]
    order: list[str]
    parents_of: dict[str, list[str]]
    node_map: dict[str, GraphNode]
    id_to_name: dict[str, str]
    errors: dict[str, str]
    timings: dict[str, float]
    memory_bytes: dict[str, int]
    error_lines: dict[str, int]
    available_columns: dict[str, list[tuple[str, str]]]
    output_columns: dict[str, list[tuple[str, str]]]


def _execute_eager_core(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    row_limit: int | None = None,
    swallow_errors: bool = False,
    preamble_ns: dict | None = None,
    source: str = "live",
    enforce_contracts: bool = True,
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None = None,
    materialize_node_ids: set[str] | frozenset[str] | None = None,
    materialize_column_limits_by_node: Mapping[str, int] | None = None,
    execution_context: ExecutionContext | None = None,
) -> EagerResult:
    """Execute the graph eagerly in topo order and collect DataFrames.

    Shared core for the preview executor and the trace engine.

    Args:
        graph: React Flow graph.
        build_node_fn: ``(node, source_names=..., ...) -> (name, fn, is_source)``.
        target_node_id: If set, only execute ancestors of this node.
        row_limit: Cap source-node output to this many rows.
        swallow_errors: If ``True``, record per-node errors and continue
            (preview behaviour).  If ``False``, raise immediately (trace).
        source: Active execution source (``"live"`` = eager scoring).
        enforce_contracts: If ``True`` (default), assert each node's
            column contract at its input and output boundaries.  A
            mismatch always raises ``ContractMismatchError`` regardless
            of *swallow_errors* — the contract is an API-level claim
            and a silent error would defeat the adoption effort.
        required_columns_by_node: Optional exact output-column demand for
            caller-consumed nodes.  Eager preview uses this to collect only
            the visible target columns while still reporting the full schema.
        materialize_node_ids: Optional set of nodes whose outputs should be
            collected into concrete DataFrames.  ``None`` preserves the
            traditional eager behaviour and materialises every executed node.
            Target-only preview passes ``{target_node_id}`` so ancestors stay
            lazy while still participating in schema, contract, and projection
            planning.
        materialize_column_limits_by_node: Optional per-node cap on the
            columns collected into materialised DataFrames.  The full output
            schema is still reported from ``collect_schema()`` before this
            cap is applied.  Used by first-click preview when the frontend
            has not yet sent explicit requested preview columns.

    Returns:
        An ``EagerResult`` with named fields for outputs, order,
        parents_of, node_map, id_to_name, errors, timings, and
        memory_bytes.
    """
    graph = _resolve_graph_paths(graph)
    node_map, order, parents_of, id_to_name = _prepare_graph(
        graph,
        target_node_id,
        source=source,
    )
    normalised_required_columns = _normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )
    materialized_ids = None if materialize_node_ids is None else frozenset(materialize_node_ids)
    materialize_column_limits = dict(materialize_column_limits_by_node or {})
    for limit_node_id, limit in materialize_column_limits.items():
        if not isinstance(limit_node_id, str) or not limit_node_id:
            raise ValueError("materialize_column_limits_by_node keys must be node ids")
        if type(limit) is not int or limit < 1:
            raise ValueError("materialize column limits must be positive integers")

    # Full parent lookup from ALL edges for instance resolution
    all_parents = graph.parents_of

    # Fan-out count per node — how many direct children consume this
    # node's output.  Used to add a Polars ``.cache()`` hint when the
    # parent feeds >1 consumer so the optimiser reuses one materialized
    # plan across branches (diamond graphs) instead of duplicating the
    # upstream work.  A parent may be either a concrete DataFrame
    # (traditional eager preview/trace) or a LazyFrame (target-only
    # preview), and both can carry the hint into downstream collection.
    children_count: dict[str, int] = dict.fromkeys(order, 0)
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for _nid, _pids in parents_of.items():
        for _pid in _pids:
            if _pid in children_count:
                children_count[_pid] += 1
                children_of[_pid].append(_nid)

    projection_plan: _ProjectionPlan | None = (
        _compute_projection_plan(
            order,
            children_of,
            node_map,
            required_columns_by_node=normalised_required_columns,
            strict_projection=_strict_projection_for_context(
                execution_context,
                normalised_required_columns,
            ),
        )
        if normalised_required_columns
        else None
    )
    needed_cols: dict[str, set[str] | None] = (
        projection_plan.needed_by_node if projection_plan is not None else {}
    )
    builder_needed_cols = projection_planner.builder_required_output_columns_by_node(
        node_map,
        needed_cols,
        preserve_eager_model_score_inputs=True,
    )

    funcs = _build_funcs(
        order,
        node_map,
        parents_of,
        id_to_name,
        all_parents,
        build_node_fn,
        row_limit=row_limit,
        preamble_ns=preamble_ns,
        source=source,
        required_output_columns_by_node=builder_needed_cols,
        execution_profile=execution_context.profile if execution_context is not None else None,
    )

    eager_outputs: dict[str, pl.DataFrame | None] = {}
    runtime_outputs: dict[str, pl.LazyFrame | pl.DataFrame | None] = {}
    errors: dict[str, str] = {}
    error_lines: dict[str, int] = {}
    timings: dict[str, float] = {}
    memory_bytes: dict[str, int] = {}
    available_columns: dict[str, list[tuple[str, str]]] = {}
    output_columns: dict[str, list[tuple[str, str]]] = {}

    # Per-node column sets used by the boundary contract checks.  We
    # compute each frame's column set exactly once and reuse it — both
    # as an output check for the producing node and as an input check
    # for its consumer(s).  Polars' ``.columns`` is O(n) in the number
    # of columns, but frozenset construction dominates anyway; caching
    # keeps the contract-enforced path within the <5% budget.
    column_cache: dict[str, frozenset[str]] = {}

    def _schema_items_of(frame: pl.LazyFrame | pl.DataFrame) -> list[tuple[str, str]]:
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema = lazy_frame.collect_schema()
        return [(name, str(schema[name])) for name in schema.names()]

    def _full_model_score_schema(
        node_id: str,
        node: GraphNode,
        actual_columns: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        config = node.data.config
        if (
            node.data.nodeType != NodeType.MODEL_SCORE
            or config.get("code")
            or config.get("column_renames")
            or config.get("selected_columns")
        ):
            return actual_columns

        parent_ids = parents_of.get(node_id, [])
        if not parent_ids:
            return actual_columns
        parent_columns = output_columns.get(parent_ids[0])
        if parent_columns is None:
            return actual_columns

        actual_by_name = dict(actual_columns)
        generated_names = [str(config.get("output_column") or "prediction")]
        proba_col = f"{generated_names[0]}_proba"
        if proba_col in actual_by_name:
            generated_names.append(proba_col)

        seen: set[str] = set()
        full_columns: list[tuple[str, str]] = []
        for name, dtype in parent_columns:
            full_columns.append((name, actual_by_name.get(name, dtype)))
            seen.add(name)
        for name in generated_names:
            if name in seen or name not in actual_by_name:
                continue
            full_columns.append((name, actual_by_name[name]))
            seen.add(name)
        return full_columns

    for nid in order:
        fn, is_source = funcs[nid]
        node = node_map[nid]
        contract = _effective_contract(node) if enforce_contracts else None
        check_here = bool(contract) and _should_check_contract(contract)  # type: ignore[arg-type]
        # A node that the builder chose to wire to ``_passthrough_fn`` is
        # running in a stub/unconfigured state (MODEL_SCORE without a
        # loaded model, OPTIMISER_APPLY without an artifact, etc.).  Its
        # contract describes the *configured* shape, which the runtime
        # intentionally does not produce yet.  Skip the output-side
        # check to preserve the "drag node onto canvas, configure later"
        # UX while still enforcing contracts the moment a real function
        # is wired in.
        is_passthrough_runtime = fn is _passthrough_fn
        if execution_context is not None:
            execution_context.checkpoint(label="before_node", node_id=nid)
        t0 = time.perf_counter()
        try:
            if is_source:
                result = fn()
                if row_limit and isinstance(result, (pl.LazyFrame, pl.DataFrame)):
                    result = result.head(row_limit)
            else:
                input_ids = parents_of.get(nid, [])
                missing_parents = [pid for pid in input_ids if pid not in runtime_outputs]
                if missing_parents:
                    raise ValueError(
                        f"Node '{nid}' is missing input(s) from: {missing_parents}. "
                        "Upstream node(s) may not have been registered."
                    )
                failed_parents = [pid for pid in input_ids if runtime_outputs[pid] is None]
                if failed_parents:
                    eager_outputs[nid] = None
                    runtime_outputs[nid] = None
                    parent_errors = [
                        f"{pid}: {errors[pid]}" if pid in errors else f"{pid}: failed"
                        for pid in failed_parents
                    ]
                    errors[nid] = "Upstream node(s) failed: " + "; ".join(parent_errors)
                    for pid in failed_parents:
                        if pid in error_lines:
                            error_lines[nid] = error_lines[pid]
                            break
                    continue
                # Add ``.cache()`` on parents that feed >1 consumer so a
                # downstream ``.collect()`` re-uses the materialised plan
                # across branches instead of duplicating upstream work.
                # This is the diamond optimisation: src -> (left, right)
                # -> sink should compute src's plan once, not twice.
                # Parents with exactly one consumer skip the hint — it's
                # cheap but non-zero overhead and adds no value there.
                input_lfs = []
                for pid in input_ids:
                    if pid not in runtime_outputs:
                        continue
                    parent_frame = runtime_outputs[pid]
                    if parent_frame is None:
                        continue
                    parent_lf = (
                        parent_frame
                        if isinstance(parent_frame, pl.LazyFrame)
                        else parent_frame.lazy()
                    )
                    if children_count.get(pid, 0) > 1:
                        parent_lf = parent_lf.cache()
                    input_lfs.append(parent_lf)
                if not input_lfs:
                    raise ValueError(
                        f"No input data available for node '{nid}'",
                    )

                # Input-side contract check: every column the node's
                # contract says it reads must be present upstream.
                # Using the union across all parents matches how the
                # node's function receives inputs — multi-input joins
                # combine them before the contract columns are read.
                if check_here and contract.inputs is not None:  # type: ignore[union-attr]
                    upstream_cols: frozenset[str] = frozenset().union(
                        *(column_cache[pid] for pid in input_ids if pid in column_cache)
                    )
                    _assert_inputs_satisfy_contract(node, contract, upstream_cols)  # type: ignore[arg-type]

                if enforce_contracts:
                    _assert_simple_join_key_dtypes_compatible(node, input_ids, input_lfs)

                result = fn(*input_lfs)

            if not isinstance(result, (pl.LazyFrame, pl.DataFrame)):
                raise TypeError(
                    f"Node '{nid}' returned {type(result).__name__}; expected a Polars frame."
                )

            result_lf = result if isinstance(result, pl.LazyFrame) else result.lazy()

            # Capture full column set before selected_columns filtering
            available_columns[nid] = _schema_items_of(result_lf)

            # Apply selected_columns filter first (uses pre-rename names),
            # then column renames on the surviving columns.
            filtered = _apply_selected_columns(result_lf, node_map[nid].data.config)
            renamed = _apply_column_renames(filtered, node_map[nid].data.config)
            output_lf = renamed if isinstance(renamed, pl.LazyFrame) else renamed.lazy()
            full_output_columns = _schema_items_of(output_lf)
            full_output_columns = _full_model_score_schema(nid, node, full_output_columns)
            if (
                node.data.nodeType == NodeType.MODEL_SCORE
                and not node.data.config.get("code")
                and not node.data.config.get("column_renames")
                and not node.data.config.get("selected_columns")
            ):
                available_columns[nid] = full_output_columns
            output_columns[nid] = full_output_columns
            output_column_names = [name for name, _dtype in full_output_columns]
            output_column_set = set(output_column_names)

            # Output-side contract check: every column the node promises
            # to produce must be present on the result.  We check the
            # post-rename/post-select frame because that's what
            # downstream consumers actually see.  Passthrough-runtime
            # nodes are exempt — see the ``is_passthrough_runtime``
            # note above.
            final_cols = frozenset(output_column_names)
            if (
                check_here
                and contract.outputs is not None  # type: ignore[union-attr]
                and not is_passthrough_runtime
            ):
                _assert_outputs_satisfy_contract(node, contract, final_cols)  # type: ignore[arg-type]

            projection = needed_cols.get(nid)
            projected_columns: list[str] | None = None
            if projection is not None:
                missing = projection - output_column_set
                if missing and nid not in normalised_required_columns:
                    raise ContractMismatchError(
                        "Eager projection references columns missing from the node output schema.",
                        node_id=nid,
                        node_type=node.data.nodeType.value,
                        missing=sorted(missing),
                        required_columns=sorted(projection),
                        output_columns=sorted(output_column_set),
                    )
                candidate_columns = [c for c in output_column_names if c in projection]
                if len(candidate_columns) < len(output_column_names):
                    projected_columns = candidate_columns
            column_cache[nid] = final_cols

            should_materialize = materialized_ids is None or nid in materialized_ids
            if should_materialize:
                collect_lf = output_lf
                if projected_columns is not None:
                    logger.info(
                        "eager_projection",
                        node_id=nid,
                        total_cols=len(output_column_names),
                        projected_cols=len(projected_columns),
                    )
                    collect_lf = collect_lf.select(projected_columns)
                column_limit = materialize_column_limits.get(nid)
                if (
                    column_limit is not None
                    and projection is None
                    and len(output_column_names) > column_limit
                ):
                    collect_lf = output_lf.select(output_column_names[:column_limit])
                collect_profile = (
                    execution_context.profile
                    if execution_context is not None
                    else ExecutionProfile.PREVIEW_EAGER
                )
                allow_broad_collect = collect_profile == ExecutionProfile.PREVIEW_EAGER
                if execution_context is not None:
                    execution_context.checkpoint(label="before_collect", node_id=nid)
                    with execution_context.stage("eager_collect", node_id=nid):
                        df = streaming_collect(
                            collect_lf,
                            profile=collect_profile,
                            allow_broad=allow_broad_collect,
                        )
                    execution_context.checkpoint(label="after_collect", node_id=nid)
                else:
                    df = streaming_collect(
                        collect_lf,
                        profile=collect_profile,
                        allow_broad=allow_broad_collect,
                    )
                eager_outputs[nid] = df
                runtime_outputs[nid] = df
                memory_bytes[nid] = int(df.estimated_size("b"))
            else:
                runtime_outputs[nid] = output_lf
        except ContractMismatchError:
            # Contract errors are API-level — raise even in swallow mode
            # so GUI users see the crisp error instead of a silent
            # per-node "failed" status card.
            raise
        except (ExecutionCancelledError, ExecutionMemoryLimitExceededError):
            # Execution-control signals are run-level failures, not
            # user-code node errors. They must reach the route/job layer
            # so cancellation and memory-limit semantics stay consistent.
            raise
        except Exception as exc:
            if not swallow_errors:
                raise
            logger.error("node_failed", node_id=nid, error=str(exc))
            eager_outputs[nid] = None
            runtime_outputs[nid] = None
            errors[nid] = str(exc)
            error_line = _extract_error_line(exc)
            if error_line is not None:
                error_lines[nid] = error_line
        timings[nid] = round((time.perf_counter() - t0) * 1000, 1)

    return EagerResult(
        eager_outputs,
        order,
        parents_of,
        node_map,
        id_to_name,
        errors,
        timings,
        memory_bytes,
        error_lines,
        available_columns,
        output_columns,
    )
