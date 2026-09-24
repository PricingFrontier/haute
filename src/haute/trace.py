"""Execution trace: single-row instrumented pipeline execution.

Runs a pipeline graph on a single row and captures per-node snapshots
(input schema, output schema, row values, schema diffs).  This is the
foundation for the data-lineage / explainability feature specified in
specs/tracing/high-level.md.

Current surface:
  • execute_trace()  - run graph, collect 1-row snapshots at every node
  • SchemaDiff       - classify columns as added/removed/modified/passed
  • TraceStep / TraceResult dataclasses

The trace is a pure observation layer — it never modifies the execution
pipeline.  It runs the same eager path the preview uses and correlates rows
between parent and child nodes post-hoc using column value matching.  This
guarantees that the trace always shows exactly the data the user sees in the
preview table.

Module layout — this file is the public facade and execute-trace
orchestrator.  Heavy lifting lives in sibling modules:

  * ``_trace_correlation``  — post-hoc row-value correlation, schema
    diff, JSON-safe row coercion.
  * ``_trace_enrichment``   — per-step enrichment dispatch
    (``enrich_steps``) plus node-type enrichers (rating step, banding,
    model score, scenario expansion, live switch, row lineage).
  * ``_trace_waterfall``    — sequential multiplicative / additive
    waterfall assembly.

This module re-exports selected parser and enrichment helpers as part of
the trace facade; the enrichment implementation imports its dependencies
directly and does not depend on this facade being imported first.
"""

from __future__ import annotations

import functools
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import polars as pl

import haute.execution as execution_facade
from haute._builders import resolve_instance_nodes
from haute._cache import GraphFingerprintMemo
from haute._env import int_env
from haute._execute_lazy import lineage_preparation_order
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._expression_parser import (
    evaluate_expression,
    parse_expression,
    parse_expression_chain,
)
from haute._graph_utils import edge_input_name
from haute._graph_walker import CollectPolicy, walk_graph
from haute._input_preparation import preparation_base_dir, prepare_input_snapshots
from haute._json_safe import to_json_safe
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._path_resolution import runtime_project_root_scope
from haute._polars_selectors import preamble_selector_aliases
from haute._seed_plans import ListedSeed, SeedPlan, SeedPlanRequest, open_listed_seed_plan
from haute._trace_correlation import (
    CorrelationWork,
    RowScopeResolver,
    SchemaDiff,
    TraceEdgeAlignment,
    TraceEdgeKey,
    _compute_schema_diff,
    _correlate_rows_posthoc,
    _jsonify_row,
    _match_rows_vectorized,
    _RowMatchStatus,
    edge_join_role_edges,
    trace_edge_alignment,
    trace_head_prefixes,
)
from haute._trace_enrichment import (
    detect_row_lineage_type,
    enrich_banding,
    enrich_live_switch,
    enrich_model_score,
    enrich_optimiser_apply,
    enrich_rating_step,
    enrich_scenario_expansion,
)
from haute._trace_enrichment import enrich_steps as _enrich_steps
from haute._trace_waterfall import build_waterfall_from_steps
from haute.errors import TraceCorrelationUnsupportedError
from haute.executor import (
    PREVIEW_CACHE_MAX_BYTES,
    _build_node_fn,
    _compile_preamble,
    _estimate_preview_cache_entry_bytes,
    _pipeline_dir,
    _seeded_fingerprint,
)
from haute.graph_utils import (
    GraphEdge,
    NodeType,
    PipelineGraph,
    topo_sort_ids,
)

logger = get_logger(component="trace")

__all__ = [
    "SchemaDiff",
    "TraceOmission",
    "TraceResult",
    "TraceStep",
    "detect_row_lineage_type",
    "enrich_banding",
    "enrich_live_switch",
    "enrich_model_score",
    "enrich_optimiser_apply",
    "enrich_rating_step",
    "enrich_scenario_expansion",
    "evaluate_expression",
    "execute_trace",
    "parse_expression",
    "parse_expression_chain",
    "trace_result_to_dict",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TraceStep:
    """One node's contribution to the trace."""

    node_id: str
    node_name: str
    node_type: str

    # Schema changes
    schema_diff: SchemaDiff

    # Single-row snapshots (column → value)
    input_values: dict[str, Any]
    output_values: dict[str, Any]

    # Stable position in the target ancestor graph's topological order.
    topological_rank: int = 0

    # True if this node adds/modifies/passes the traced column
    column_relevant: bool = True

    # Expression parsing and enrichment, populated by _enrich_steps.
    expression: dict[str, Any] | None = None
    calculation: dict[str, Any] | None = None
    node_detail: dict[str, Any] | None = None
    row_lineage_type: str | None = None

    # The shared-snapshot generation this step's row was read from, when the
    # trace was seeded there instead of computing the node.
    snapshot_generation_id: str | None = None

    # The same rows as one-row frames in the dtypes the pipeline gave them, for
    # evaluating the step's formulas; ``None`` when the trace could not recover
    # them. Not part of the serialised trace.
    input_row: pl.DataFrame | None = field(default=None, repr=False, compare=False)
    output_row: pl.DataFrame | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class TraceOmission:
    """A relevant node whose row could not be correlated truthfully."""

    node_id: str
    node_name: str
    node_type: str
    topological_rank: int
    reason: str
    diagnostic_index: int


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TraceResult:
    """Full trace for one row through the pipeline."""

    target_node_id: str
    row_index: int
    column: str | None
    output_value: Any

    steps: list[TraceStep]
    omissions: list[TraceOmission] = field(default_factory=list)

    # Row identity (from apiInput node's row_id_column config)
    row_id_column: str | None = None
    row_id_value: Any = None

    # Summary counts
    total_nodes_in_pipeline: int = 0
    nodes_in_trace: int = 0
    execution_ms: float = 0.0

    # Waterfall summary for sequential multiplicative/additive rating chains.
    # On the happy path this is a list of entry dicts.  If waterfall
    # construction fails, the field carries a structured
    # ``{"error": "..."}`` payload instead — never a silent ``None``.
    waterfall: list[dict[str, Any]] | dict[str, Any] | None = None

    # Non-fatal row-correlation diagnostics. These explain why an upstream
    # row was left unresolved instead of selecting an ambiguous candidate.
    correlation_diagnostics: list[dict[str, Any]] = field(default_factory=list)

    # Assembly provenance. This describes how the trace was built; it is not
    # a claim that cached source data is current.
    generated_at: str = field(default_factory=_utc_now_iso)
    pipeline_source: str | None = None
    execution_origin: str = "fresh_execution"


# ---------------------------------------------------------------------------
# Execution cache — avoids re-running the full pipeline on every trace click.
# The graph structure (node IDs, types, code, paths, edges) is hashed into a
# fingerprint.  When only row_index or column changes, the cached per-node
# DataFrames are reused and we just extract a different row — sub-millisecond.
#
# Bounding: entries hold materialized per-node DataFrames whose sizes vary
# wildly, so the cache is bounded by retained bytes as well as entry count,
# reusing the preview cache's frame-size estimator.  Eviction is LRU —
# oldest entry first — and the just-stored trace is always most-recently-
# used, so the click-different-cells flow keeps its instant cache hit.  A
# single entry larger than the whole budget is deterministically rejected
# at store time with a loud log (the same admit-or-reject-at-store policy
# the dataframe-execution cache applies to oversized artifacts); the trace
# itself still succeeds — only the re-click loses its cache hit.
# ---------------------------------------------------------------------------


TRACE_CACHE_MAX_BYTES = int_env(
    "HAUTE_TRACE_CACHE_MAX_BYTES",
    PREVIEW_CACHE_MAX_BYTES,
)
"""Maximum retained bytes for materialized trace DataFrames.

Defaults to the preview cache budget: both caches retain the same class
of payload (materialized per-node frames), so one knob bounds both
unless ``HAUTE_TRACE_CACHE_MAX_BYTES`` overrides the trace side
explicitly.
"""


_cache: LRUCache[str, dict[str, Any]] = LRUCache(
    max_size=8,
    max_bytes=TRACE_CACHE_MAX_BYTES,
    size_of=_estimate_preview_cache_entry_bytes,
)


# Enrichment + expression-parser names are imported at the top of this
# module and re-exported via ``__all__`` so tests can
# ``monkeypatch.setattr("haute.trace.parse_expression", …)`` and the
# dispatch walk in ``_trace_enrichment.enrich_steps`` sees the patched
# version via its ``sys.modules["haute.trace"]`` lookup.


# ---------------------------------------------------------------------------
# Main trace executor
# ---------------------------------------------------------------------------


def _find_target_row_index(
    df: pl.DataFrame,
    row_values: dict[str, Any],
    *,
    node_id: str = "target",
) -> int | None:
    """Find the target row that exactly matches the GUI's clicked row values."""
    shared = [col for col in row_values if col in df.columns]
    if not shared:
        return None

    match = _match_rows_vectorized(df, row_values, shared)
    if match.status is _RowMatchStatus.UNSUPPORTED_DTYPE:
        raise TraceCorrelationUnsupportedError(
            "Trace row correlation cannot compare the selected key dtype.",
            node_id=node_id,
            key_columns=match.strict_key_columns,
            dtypes=match.dtypes,
            reason_code="unsupported_dtype",
        )
    if match.status is _RowMatchStatus.AMBIGUOUS:
        raise ValueError(
            "Trace row match is ambiguous: "
            f"{match.candidate_count} rows match the clicked values on "
            f"columns {shared}. The preview data may have changed. "
            "Please click the node to refresh, then retry."
        )
    if match.status is _RowMatchStatus.UNIQUE_STRICT:
        return match.candidate_indices[0]
    return None


def _requested_preview_columns_from_row(
    row_values: dict[str, Any] | None,
    column: str | None,
) -> list[str] | None:
    if not row_values:
        return None
    columns = [str(name) for name in row_values]
    if column and column not in columns:
        columns.append(column)
    return columns


def _is_integer_output_column(
    eager_outputs: dict[str, pl.DataFrame],
    node_id: str,
    column: str,
) -> bool:
    df = eager_outputs.get(node_id)
    if not isinstance(df, pl.DataFrame) or column not in df.schema:
        return False
    is_integer = getattr(df.schema[column], "is_integer", None)
    return bool(is_integer()) if callable(is_integer) else False


def _integer_output_node_ids(
    eager_outputs: dict[str, pl.DataFrame],
    steps: list[TraceStep],
    column: str,
) -> set[str]:
    return {
        step.node_id
        for step in steps
        if _is_integer_output_column(eager_outputs, step.node_id, column)
    }


def execute_trace(
    graph: PipelineGraph,
    row_index: int = 0,
    target_node_id: str | None = None,
    column: str | None = None,
    row_limit: int = 1000,
    source: str = "live",
    row_values: dict[str, Any] | None = None,
    preamble_ns: dict[str, Any] | None = None,
    fingerprint_memo: GraphFingerprintMemo | None = None,
    execution_context: ExecutionContext | None = None,
    seed_plan: Sequence[ListedSeed] | None = None,
) -> TraceResult:
    """Execute a pipeline graph and return a single-row trace.

    The trace is a pure observation layer — it runs the same eager path the
    preview uses and correlates rows between parent and child nodes post-hoc.
    The execution pipeline is never modified.

    Args:
        graph: React Flow graph with "nodes" and "edges".
        row_index: Which row in the target node's output to trace (0-indexed).
        target_node_id: Node to trace from. Defaults to the last node in topo order.
        column: Optional column name - if set, only include nodes that touch it.
        row_limit: Max rows to process per source node (matches the preview limit
                   so the trace operates on the same data the user sees).
        source: Active execution source (``"live"`` = API path).
        row_values: Optional dict of the clicked row's values from the frontend.
                    Used to verify the trace is operating on the same data the
                    user sees.  If the values don't match, a ValueError is raised.
        fingerprint_memo: Optional request-scoped
                 :class:`~haute._cache.GraphFingerprintMemo` shared with the
                 caller (the trace route reuses the memo from its
                 supersession-key computation) so preamble utility files are
                 hashed at most once per request.  ``None`` creates a fresh
                 memo scoped to this call.
        seed_plan: The generations the preview this trace explains was
                 computed from (its response's ``seed_plan``). The trace reads
                 exactly those that cover what it reads, leased for the whole
                 trace, and captures nothing; a retired generation, or one the
                 graph no longer produces at its point, raises
                 :class:`~haute.errors.SeedPlanExpiredError`. Empty or ``None``
                 seeds nothing, even if snapshots now exist.

    Returns:
        TraceResult with per-node steps showing how the row was produced.
    """
    t_start = time.perf_counter()

    nodes = graph.nodes
    edges = graph.edges

    if not nodes:
        raise ValueError("Empty graph - nothing to trace")

    # Resolve target before graph preparation filters to ancestors.
    # Pass the node list in its declared order (not a set) so the topo
    # sort's insertion-order tie-break is deterministic — the previous
    # set-derived list made the chosen sink depend on CPython hash
    # randomisation across process invocations.
    if target_node_id is None:
        target_node_id = topo_sort_ids([n.id for n in nodes], edges)[-1]
    if not any(n.id == target_node_id for n in nodes):
        raise ValueError(f"Target node '{target_node_id}' not found in graph")
    if execution_context is None:
        admitted_context = create_admitted_execution_context(
            operation="execute_trace",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
        try:
            return execute_trace(
                graph,
                row_index=row_index,
                target_node_id=target_node_id,
                column=column,
                row_limit=row_limit,
                source=source,
                row_values=row_values,
                preamble_ns=preamble_ns,
                fingerprint_memo=fingerprint_memo,
                execution_context=admitted_context,
                seed_plan=seed_plan,
            )
        finally:
            admitted_context.release_admission(preserve_primary_error=True)

    core = functools.partial(
        _execute_trace_core,
        graph,
        row_index=row_index,
        target_node_id=target_node_id,
        column=column,
        row_limit=row_limit,
        source=source,
        row_values=row_values,
        preamble_ns=preamble_ns,
        fingerprint_memo=fingerprint_memo,
        execution_context=execution_context,
        t_start=t_start,
    )
    if not seed_plan:
        return core(snapshot_plan=None)
    with runtime_project_root_scope(graph.source_file):
        snapshot_plan = _open_trace_seed_plan(
            graph,
            target_node_id,
            source=source,
            seed_plan=seed_plan,
            execution_context=execution_context,
        )
    with snapshot_plan:
        if not snapshot_plan.decision.seeds:
            # Nothing listed covers what the trace reads: it runs as one
            # without a plan, computing every ancestor itself.
            return core(snapshot_plan=None)
        return core(snapshot_plan=snapshot_plan)


def _open_trace_seed_plan(
    graph: PipelineGraph,
    target_node_id: str,
    *,
    source: str,
    seed_plan: Sequence[ListedSeed],
    execution_context: ExecutionContext,
) -> SeedPlan:
    """Lease the listed generations, then prepare only the inputs the trace computes.

    Preparation can move an input's pointer and with it the signatures the
    listed generations were checked against, so a plan whose preparation did
    anything is opened, and checked, again.
    """
    request = SeedPlanRequest(
        graph=graph,
        target_node_id=target_node_id,
        source=source,
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    plan = open_listed_seed_plan(request, seed_plan)
    if execution_context.admission is None:
        return plan
    executed = [node.id for node in graph.nodes if node.id in plan.decision.executed_node_ids]
    try:
        records = prepare_input_snapshots(
            executed,
            resolve_instance_nodes(graph).node_map,
            profile=execution_context.profile,
            execution_context=execution_context,
            base_dir=preparation_base_dir(graph),
            schema_only=False,
        )
    except BaseException:
        plan.close()
        raise
    if all(record.action == "reused" for record in records):
        return plan
    plan.close()
    return open_listed_seed_plan(request, seed_plan)


def _snapshot_seed_skips(prepared_lineage: Any, plan: SeedPlan) -> dict[str, list[str]]:
    """Each lineage node the trace skipped because of a seed, with the seeds below it."""
    decision = plan.decision
    ran = set(decision.executed_node_ids) | set(decision.seeds)
    children: dict[str, set[str]] = {}
    for edge in prepared_lineage.relevant_edges:
        children.setdefault(edge.source, set()).add(edge.target)
    position = {node_id: index for index, node_id in enumerate(prepared_lineage.order)}
    skips: dict[str, list[str]] = {}
    for node_id in prepared_lineage.order:
        if node_id in ran:
            continue
        seeds: set[str] = set()
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            for child in children.get(stack.pop(), ()):
                if child in seen:
                    continue
                seen.add(child)
                if child in decision.seeds:
                    seeds.add(child)
                elif child not in ran:
                    stack.append(child)
        if seeds:
            skips[node_id] = sorted(seeds, key=lambda seed: position.get(seed, 0))
    return skips


def _execute_trace_core(
    graph: PipelineGraph,
    *,
    row_index: int,
    target_node_id: str,
    column: str | None,
    row_limit: int,
    source: str,
    row_values: dict[str, Any] | None,
    preamble_ns: dict[str, Any] | None,
    fingerprint_memo: GraphFingerprintMemo | None,
    execution_context: ExecutionContext,
    snapshot_plan: SeedPlan | None,
    t_start: float,
) -> TraceResult:
    """One trace, under the plan its preview listed when that plan seeds anything."""
    nodes = graph.nodes
    requested_columns = _requested_preview_columns_from_row(row_values, column)
    # A cold trace executes the graph itself, so it prepares its own snapshot
    # inputs first — before the strategy is planned and before the lineage cache
    # key is computed, so a refreshed generation is the one this entry is keyed
    # by. Only an admitted context can spawn the hard-capped build. A seed plan
    # already prepared exactly the inputs this trace computes.
    if snapshot_plan is None and execution_context.admission is not None:
        with runtime_project_root_scope(graph.source_file):
            prepare_input_snapshots(
                lineage_preparation_order(graph, target_node_id, source),
                graph.node_map,
                profile=execution_context.profile,
                execution_context=execution_context,
                base_dir=preparation_base_dir(graph),
                schema_only=False,
            )
    execution_facade.plan_execution_strategy(
        execution_facade.ProjectionRequest(
            graph=graph,
            target_node_id=target_node_id,
            profile=(
                execution_context.profile
                if execution_context is not None
                else ExecutionProfile.PREVIEW_EAGER
            ),
            required_columns_by_node=(
                {target_node_id: requested_columns} if requested_columns is not None else None
            ),
            source=source,
        ),
        execution_context=execution_context,
        # Work a seed covers is neither admitted nor estimated: the trace reads
        # the seed's generation instead of recomputing it.
        **_planned_strategy_scope(graph, snapshot_plan),
    )

    # ---------- Eager execution with a byte-bounded LRU cache ----------
    # Model-scoring nodes can take ~1s on large datasets (678K rows).
    # The pipeline structure doesn't change between trace clicks — only the
    # row_index and column change.  Cache the materialized DataFrames and
    # reuse them: first click ~1.7s, subsequent clicks <10ms.
    # A caller (the trace route) may pass in the request-scoped memo it
    # already used for the supersession key, so the preamble's utility
    # files are hashed once per request rather than once per call.
    if fingerprint_memo is None:
        fingerprint_memo = GraphFingerprintMemo()
    # The shared lineage key scopes both graph structure and runtime inputs
    # to this target's source-selected ancestors. The same full-materialisation
    # identity is used by preview, which makes reuse explicit rather than a
    # private reconstruction of executor key text.
    fp = execution_facade.preview_lineage_cache_key(
        graph,
        target_node_id=target_node_id,
        source=source,
        requested_columns=None,
        initial_column_limit=None,
        row_limit=row_limit,
        port_label=None,
        enforce_contracts=True,
        materialisation_scope="full",
        memo=fingerprint_memo,
        seed_plan_fingerprint=(
            _seeded_fingerprint(snapshot_plan.decision) if snapshot_plan is not None else None
        ),
    )
    prepared_lineage = execution_facade.prepare_graph(graph, target_node_id, source=source)
    selector_aliases = preamble_selector_aliases(graph.preamble or "")
    alignments, lineage_input_names, child_input_names, child_input_aliases = (
        _trace_lineage_alignments(prepared_lineage, selector_aliases)
    )
    prefixes = trace_head_prefixes(
        prepared_lineage.order,
        alignments,
        target_node_id=target_node_id,
        row_limit=row_limit,
    )

    cached = _cache.get(fp)
    if cached is not None:
        cache_hit = True
        execution_origin = "trace_cache"
        logger.debug(
            "trace_cache_hit",
            fingerprint=fp[:8],
            target=target_node_id,
            cached_nodes=len(cached["eager_outputs"]),
        )
        eager_outputs = cached["eager_outputs"]
        order = cached["order"]
        parents_of = cached["parents_of"]
        node_map = cached["node_map"]
        source_ids = cached["source_ids"]
        plans = None
    else:
        cache_hit = False
        logger.debug(
            "trace_cache_miss",
            fingerprint=fp[:8],
            target=target_node_id,
            prev_fingerprint=(_cache.most_recent_key or "")[:8],
        )

        execution_origin = "fresh_execution"
        (
            eager_outputs,
            order,
            parents_of,
            node_map,
            source_ids,
            plans,
        ) = _materialize_eager_outputs(
            graph=graph,
            target_node_id=target_node_id,
            row_limit=row_limit,
            prefixes=prefixes,
            source=source,
            preamble_ns=preamble_ns,
            execution_context=execution_context,
            snapshot_plan=snapshot_plan,
        )

        # Populate cache — unmodified DataFrames from the single execution
        _cache.put(
            fp,
            {
                "eager_outputs": eager_outputs,
                "order": order,
                "parents_of": parents_of,
                "node_map": node_map,
                "source_ids": source_ids,
            },
        )

    seeded_ids = (
        frozenset(snapshot_plan.decision.seeds) if snapshot_plan is not None else frozenset()
    )
    if seeded_ids:
        # A seeded point's row is read, not derived: correlation, steps, and
        # relevance never look above it, as at any other source.
        parents_of = {
            node_id: ([] if node_id in seeded_ids else list(parent_ids))
            for node_id, parent_ids in parents_of.items()
        }

    # Multi-frame sources (e.g. a ≥2-table apiInput) store a
    # dict[label, DataFrame] in eager_outputs; a trace must target a node
    # downstream of a specific frame, never the bundle itself.
    target_output = eager_outputs.get(target_node_id)
    if isinstance(target_output, dict):
        raise ValueError(
            f"Target node {target_node_id!r} emits multiple frames; "
            "trace a node downstream of a specific frame instead."
        )

    # Preserve every physical edge.  Edge Join input roles are defined by
    # targetHandle, and a multi-frame source can feed both roles of one join.
    edge_metadata: dict[tuple[str, str], list[tuple[str | None, str | None]]] = {}
    incoming_edges_of: dict[str, list[GraphEdge]] = {}
    for e in graph.edges:
        edge_metadata.setdefault((e.source, e.target), []).append((e.sourceHandle, e.targetHandle))
        incoming_edges_of.setdefault(e.target, []).append(e)
    source_frames_of = {
        pair: [source_handle for source_handle, _ in metadata_edges]
        for pair, metadata_edges in edge_metadata.items()
    }
    edge_join_roles: dict[str, tuple[str, str]] = {}
    for node in nodes:
        if node.data.nodeType != NodeType.EDGE_JOIN:
            continue
        roles = edge_join_role_edges(node, edge_metadata)
        edge_join_roles[node.id] = (roles.base.source_id, roles.join.source_id)

    # Plans hold Python scans bound to this request's execution context, so they
    # are built per request and never cached with the head frames.
    plan_state: dict[str, Any] = {"plans": plans}

    def _lineage_plans() -> dict[str, Any]:
        if plan_state["plans"] is None:
            plan_state["plans"] = _build_trace_plans(
                graph=graph,
                target_node_id=target_node_id,
                row_limit=row_limit,
                source=source,
                preamble_ns=preamble_ns,
                execution_context=execution_context,
                snapshot_plan=snapshot_plan,
            )
        return cast(dict[str, Any], plan_state["plans"])

    # Row-scoped lookups belong to this click only; never write them into the
    # cached head frames another click reuses.
    frames: dict[str, Any] = dict(eager_outputs)
    row_scope = RowScopeResolver(
        node_map=node_map,
        prefixes=prefixes,
        alignments=alignments,
        edge_metadata=edge_metadata,
        input_names=lineage_input_names,
        child_input_names=child_input_names,
        plans=_lineage_plans,
        frames=frames,
        head_resolved={target_node_id},
        execution_context=execution_context,
        selector_aliases=selector_aliases,
        child_input_aliases=child_input_aliases,
    )

    # ---------- Verify row identity ----------
    # If the frontend sent the clicked row's values, verify that the
    # DataFrame at the target node has the same values at row_index.
    # A mismatch means the preview and trace are using different
    # DataFrames (e.g., due to non-deterministic Polars join ordering
    # after a cache miss).
    if row_values is not None and isinstance(target_output, pl.DataFrame):
        target_df = target_output
        row_matches = False
        if row_index < len(target_df):
            shared = [column for column in row_values if column in target_df.columns]
            row_matches = (
                bool(shared)
                and _match_rows_vectorized(
                    target_df.slice(row_index, 1),
                    row_values,
                    shared,
                ).status
                is _RowMatchStatus.UNIQUE_STRICT
            )

        if not row_matches:
            # The backend preview cache can be evicted between the GUI
            # preview request and the user's trace click. A cold trace
            # may still reproduce the clicked row, but joins can reorder
            # rows. Treat the clicked values as the source of truth and
            # relocate the target row before correlating upstream rows.
            matched_index = _find_target_row_index(
                target_df,
                row_values,
                node_id=target_node_id,
            )
            if matched_index is None:
                # The clicked row lies outside the limited target frame: look
                # it up in the target's uncapped plan. No head frame then
                # proves any ancestor's lineage.
                target_lookup = _lookup_clicked_row(row_scope, target_node_id, row_values)
                if target_lookup is not None:
                    matched_index = _find_target_row_index(
                        target_lookup,
                        row_values,
                        node_id=target_node_id,
                    )
                    if matched_index is not None:
                        frames[target_node_id] = target_lookup
                        target_output = target_lookup
                        row_scope.head_resolved.clear()
                        if target_lookup.height == 1:
                            row_scope.record_unique_row(target_node_id, target_lookup)
            if matched_index is not None:
                row_index = matched_index
            else:
                raise ValueError(
                    "Trace data does not match the preview row. "
                    "The preview data may have changed. "
                    "Please click the node to refresh, then retry."
                )

    correlation_diagnostics: list[dict[str, Any]] = []
    unresolved_rows: dict[str, tuple[str, int]] = {}
    row_positions: dict[str, int] = {}
    correlation_work = CorrelationWork()
    correlation_started = time.perf_counter()

    # Extract correct row from each node via post-hoc correlation
    # (only if target node has output data)
    try:
        if isinstance(target_output, pl.DataFrame):
            cached_rows = _correlate_rows_posthoc(
                frames,
                order,
                parents_of,
                target_node_id,
                row_index,
                node_map=node_map,
                diagnostics=correlation_diagnostics,
                unresolved=unresolved_rows,
                edge_metadata=edge_metadata,
                traced_column=column,
                work=correlation_work,
                row_scope=row_scope,
                row_positions=row_positions,
            )
        else:
            # Target node execution failed — build partial rows from available nodes
            cached_rows = {}
            for nid in order:
                df = eager_outputs.get(nid)
                if isinstance(df, pl.DataFrame):
                    if row_index < len(df):
                        cached_rows[nid] = _jsonify_row(df.row(row_index, named=True))
                        row_positions[nid] = row_index
                    else:
                        cached_rows[nid] = {}
                else:
                    cached_rows[nid] = {}
    finally:
        duration_ms = max(0.0, (time.perf_counter() - correlation_started) * 1000)
        logger.info(
            "trace_correlation_completed",
            execution_origin=execution_origin,
            duration_ms=duration_ms,
            candidate_frames_considered=correlation_work.candidate_frames_considered,
            match_scans=correlation_work.match_scans,
            rows_scanned=correlation_work.rows_scanned,
            key_columns_scanned=correlation_work.key_columns_scanned,
            comparison_cells=correlation_work.comparison_cells,
            ambiguity_count=correlation_work.ambiguity_count,
        )

    # ---------- Build trace steps from cached rows ----------
    # Ranks are positions in the whole lineage, so a node a seed skipped keeps
    # its place among the steps that did run.
    rank_order = list(prepared_lineage.order) if snapshot_plan is not None else order
    steps = _assemble_steps(
        order=rank_order,
        source_ids=source_ids,
        node_map=node_map,
        parents_of=parents_of,
        cached_rows=cached_rows,
        typed_rows=_TypedRows(
            frames=frames,
            positions=row_positions,
            source_frames_of=source_frames_of,
            cached_rows=cached_rows,
        ),
    )
    if snapshot_plan is not None:
        for step in steps:
            seed = snapshot_plan.decision.seeds.get(step.node_id)
            if seed is not None:
                step.snapshot_generation_id = seed.generation_id

    # ---------- Enrich steps with expression/detail data ----------
    # A seeded step stays in the list — it is where downstream provenance
    # ends — but enrichment never reconstructs its own calculation. Formulas
    # are evaluated with the names the node code ran with: the compiled
    # preamble (cached per process), then any caller-supplied names.
    formula_names = {
        **_compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph)),
        **(preamble_ns or {}),
    }
    _enrich_steps(
        steps,
        node_map,
        frames,
        parents_of,
        column,
        source,
        preamble_ns=formula_names,
        source_frames_of=source_frames_of,
        incoming_edges_of=incoming_edges_of,
        lineage_plans=_lineage_plans,
    )

    # ---------- Column relevance: tag then prune irrelevant ancestors ----------
    if column:
        steps = _prune_to_column_relevance(steps, column, parents_of, node_map)

    # A node the trace skipped because a seed below it was read instead is
    # an omission naming that seed. It never ran, so no schema says whether it
    # bears on a traced column: it is always reported.
    seeded_skips = (
        _snapshot_seed_skips(prepared_lineage, snapshot_plan) if snapshot_plan is not None else {}
    )
    for skipped_id, seed_ids in seeded_skips.items():
        labels = ", ".join(node_map[seed_id].data.label for seed_id in seed_ids)
        correlation_diagnostics.append(
            {
                "code": "snapshot_seed",
                "severity": "info",
                "reason": "snapshot_seed",
                "message": f"Not computed: the trace read the snapshot of {labels}.",
                "node_id": skipped_id,
                "seed_node_ids": seed_ids,
            }
        )
        unresolved_rows[skipped_id] = ("snapshot_seed", len(correlation_diagnostics) - 1)

    omissions = _build_trace_omissions(
        unresolved_rows=unresolved_rows,
        order=rank_order,
        node_map=node_map,
        eager_outputs=frames,
        steps=steps,
        column=column,
        always_relevant=frozenset(seeded_skips),
    )

    # ---------- Output value (already in cache from batch collect) ----------
    target_row = cached_rows.get(target_node_id) or {}
    output_value = target_row.get(column) if column else target_row

    # ---------- Row identity from apiInput node ----------
    row_id_column: str | None = None
    row_id_value: Any = None
    for n in nodes:
        if n.data.nodeType == NodeType.API_INPUT and n.data.config.get("row_id_column"):
            row_id_column = n.data.config["row_id_column"]
            row_id_value = target_row.get(row_id_column)
            break

    total_ms = round((time.perf_counter() - t_start) * 1000, 2)

    logger.info(
        "trace_executed",
        target=target_node_id,
        row_index=row_index,
        column=column,
        steps=len(steps),
        cache_hit=cache_hit,
        duration_ms=total_ms,
    )

    # Build waterfall from trace steps — derives each contribution from
    # consecutive observed output values along the traced path and must
    # reconcile with the traced output value displayed beside it (C8).
    waterfall_data: list[dict[str, Any]] | dict[str, Any] | None = None
    if column:
        integer_output_node_ids = _integer_output_node_ids(frames, steps, column)
        waterfall_data = build_waterfall_from_steps(
            steps,
            column,
            target_node_id=target_node_id,
            final_output_value=output_value,
            parents_of=parents_of,
            node_map=node_map,
            edge_join_roles=edge_join_roles,
            integer_output_node_ids=integer_output_node_ids,
            final_output_is_integer=_is_integer_output_column(
                frames,
                target_node_id,
                column,
            ),
        )

    return TraceResult(
        target_node_id=target_node_id,
        row_index=row_index,
        column=column,
        output_value=output_value,
        steps=steps,
        omissions=omissions,
        row_id_column=row_id_column,
        row_id_value=row_id_value,
        total_nodes_in_pipeline=len(nodes),
        nodes_in_trace=len(steps) + len(omissions),
        execution_ms=total_ms,
        waterfall=waterfall_data,
        correlation_diagnostics=correlation_diagnostics,
        generated_at=_utc_now_iso(),
        pipeline_source=graph.source_file or None,
        execution_origin=execution_origin,
    )


# ---------------------------------------------------------------------------
# execute_trace internals — materialize, assemble, prune
# ---------------------------------------------------------------------------


def _materialize_eager_outputs(
    *,
    graph: PipelineGraph,
    target_node_id: str,
    row_limit: int,
    prefixes: Mapping[str, int],
    source: str,
    preamble_ns: dict[str, Any] | None,
    execution_context: ExecutionContext | None,
    snapshot_plan: SeedPlan | None = None,
) -> tuple[
    dict[str, pl.DataFrame | dict[str, pl.DataFrame]],
    list[str],
    dict[str, list[str]],
    dict[str, Any],
    set[str],
    dict[str, Any],
]:
    """Execute the trace lineage for the trace cache.

    Only head-framed nodes (those whose own first *prefixes* rows contain the
    target preview's lineage) are materialised, each to its prefix length.
    If the frontend supplied clicked row values, execute_trace verifies or
    relocates the target row before correlation so the trace stays anchored
    to the preview row the user clicked.

    Returns ``(eager_outputs, order, parents_of, node_map, source_ids,
    plans)``, where ``plans`` holds every lineage node's uncapped runtime plan.
    """
    compiled_preamble_ns = _compile_preamble(
        graph.preamble or "",
        pipeline_dir=_pipeline_dir(graph),
    )
    # Merge caller-supplied preamble_ns with compiled preamble
    # (caller-supplied takes priority for testing convenience)
    effective_preamble = dict(compiled_preamble_ns or {})
    if preamble_ns:
        effective_preamble.update(preamble_ns)
    # Run the graph — if it fails, let the original exception
    # propagate unchanged.  Previous versions of this code
    # regex-matched "unable to find column" in the error message
    # and silently retried with swallow_errors=True, which masked
    # genuine column-name typos whenever another node in the graph
    # happened to define the same kwarg name.  Fail loudly instead.
    result = walk_graph(
        graph,
        _build_node_fn,
        policy=CollectPolicy.display(
            collect=prefixes,
            row_limit=row_limit,
            row_limits_by_node=prefixes,
        ),
        target_node_id=target_node_id,
        preamble_ns=effective_preamble or None,
        source=source,
        execution_context=execution_context,
        snapshot_plan=snapshot_plan,
    )
    eager_outputs = {nid: df for nid, df in result.collected.items() if df is not None}
    order = result.run_order
    parents_of = result.parents_of
    node_map = result.node_map
    source_ids = _trace_source_ids(order, parents_of, snapshot_plan)
    return (
        eager_outputs,
        order,
        parents_of,
        node_map,
        source_ids,
        dict(result.frames),
    )


def _build_trace_plans(
    *,
    graph: PipelineGraph,
    target_node_id: str,
    row_limit: int,
    source: str,
    preamble_ns: dict[str, Any] | None,
    execution_context: ExecutionContext | None,
    snapshot_plan: SeedPlan | None = None,
) -> dict[str, Any]:
    """Build the uncapped runtime plan of every lineage node without collecting."""
    compiled_preamble_ns = _compile_preamble(
        graph.preamble or "",
        pipeline_dir=_pipeline_dir(graph),
    )
    effective_preamble = dict(compiled_preamble_ns or {})
    if preamble_ns:
        effective_preamble.update(preamble_ns)
    result = walk_graph(
        graph,
        _build_node_fn,
        policy=CollectPolicy.display(collect=(), row_limit=row_limit),
        target_node_id=target_node_id,
        preamble_ns=effective_preamble or None,
        source=source,
        execution_context=execution_context,
        snapshot_plan=snapshot_plan,
    )
    return dict(result.frames)


def _planned_strategy_scope(graph: PipelineGraph, snapshot_plan: SeedPlan | None) -> dict[str, Any]:
    """Strategy planning scoped to what a planned execution builds, estimated from its seeds."""
    if snapshot_plan is None:
        return {}
    return {
        "materialising_node_ids": snapshot_plan.decision.executed_node_ids,
        "estimation_graph": snapshot_plan.estimation_graph(graph),
    }


def _planned_order(order: Sequence[str], snapshot_plan: SeedPlan | None) -> list[str]:
    """The lineage nodes a trace runs: under a plan, its seeds and executed nodes."""
    if snapshot_plan is None:
        return list(order)
    ran = set(snapshot_plan.decision.executed_node_ids) | set(snapshot_plan.decision.seeds)
    return [node_id for node_id in order if node_id in ran]


def _trace_source_ids(
    order: Sequence[str],
    parents_of: Mapping[str, Sequence[str]],
    snapshot_plan: SeedPlan | None,
) -> set[str]:
    """Where the trace's correlation stops: sources, and every seeded point."""
    sources = {node_id for node_id in order if not parents_of.get(node_id)}
    if snapshot_plan is not None:
        sources |= set(snapshot_plan.decision.seeds)
    return sources


def _trace_lineage_alignments(
    prepared: Any,
    selector_aliases: frozenset[str] = frozenset(),
) -> tuple[
    dict[TraceEdgeKey, TraceEdgeAlignment],
    dict[TraceEdgeKey, str],
    dict[str, tuple[str, ...]],
    dict[str, dict[str, str]],
]:
    """Classify every physical lineage edge for limited-preview tracing.

    Input names are keyed by physical edge: one source may feed several ports of
    a child, each under its own executable name.
    """
    input_names: dict[TraceEdgeKey, str] = {}
    child_inputs: dict[str, list[str]] = {}
    for edge in prepared.relevant_edges:
        try:
            name = edge_input_name(
                edge,
                prepared.node_map[edge.source],
                submodels=prepared.submodels,
            )
        except ValueError:
            continue
        input_names[(edge.source, edge.target, edge.sourceHandle, edge.targetHandle)] = name
        child_inputs.setdefault(edge.target, []).append(name)
    # A Polars transform's ``inputMapping`` lets its code name an input by a
    # stable logical name (``logical -> current edge name``); the executor binds
    # both, so the trace analysers must read both too.
    input_aliases: dict[str, dict[str, str]] = {}
    for target, names in child_inputs.items():
        node = prepared.node_map[target]
        mapping = node.data.config.get("inputMapping")
        if node.data.nodeType != NodeType.POLARS or not isinstance(mapping, dict):
            continue
        aliases = {
            logical: current
            for logical, current in mapping.items()
            if isinstance(logical, str) and logical and current in names and logical not in names
        }
        if aliases:
            input_aliases[target] = aliases
    alignments: dict[TraceEdgeKey, TraceEdgeAlignment] = {}
    for edge in prepared.relevant_edges:
        key = (edge.source, edge.target, edge.sourceHandle, edge.targetHandle)
        alignments[key] = trace_edge_alignment(
            prepared.node_map[edge.target],
            target_role=edge.targetHandle,
            edge_input=input_names.get(key),
            input_names=tuple(child_inputs.get(edge.target, ())),
            selector_aliases=selector_aliases,
            input_aliases=input_aliases.get(edge.target),
        )
    return (
        alignments,
        input_names,
        {child: tuple(names) for child, names in child_inputs.items()},
        input_aliases,
    )


def _lookup_clicked_row(
    row_scope: RowScopeResolver,
    target_node_id: str,
    row_values: Mapping[str, Any],
) -> pl.DataFrame | None:
    """Look the clicked preview row up in the target's uncapped plan."""
    schema = row_scope.schema_for(target_node_id, None)
    if schema is None:
        return None
    columns = set(schema.names())
    values = {name: value for name, value in row_values.items() if name in columns}
    if not values:
        return None
    return row_scope.lookup(target_node_id, None, values)


@dataclass(frozen=True)
class _TypedRows:
    """The correlated rows as one-row frames, sliced from the frames they were read in."""

    frames: Mapping[str, Any]
    positions: Mapping[str, int]
    source_frames_of: Mapping[tuple[str, str], Sequence[str | None]]
    cached_rows: Mapping[str, dict[str, Any] | None]

    def row(self, node_id: str, *, consumer: str | None = None) -> pl.DataFrame | None:
        """*node_id*'s traced row, as *consumer* reads it when that is a child.

        A multi-frame node's row is the one frame its consumer reads. The slice is
        used only when it is the very row the trace shows, so a frame that has
        since changed can never supply another row's values.
        """
        frame = self.frames.get(node_id)
        if isinstance(frame, dict):
            handles = {
                handle
                for handle in self.source_frames_of.get((node_id, consumer or ""), ())
                if handle is not None
            }
            frame = frame.get(next(iter(handles))) if len(handles) == 1 else None
        position = self.positions.get(node_id)
        shown = self.cached_rows.get(node_id)
        if not isinstance(frame, pl.DataFrame) or position is None or shown is None:
            return None
        if not 0 <= position < frame.height:
            return None
        row = frame.slice(position, 1)
        return row if _jsonify_row(row.row(0, named=True)) == shown else None


def _typed_input_row(
    typed_rows: _TypedRows,
    node_id: str,
    parent_ids: Sequence[str],
    key_counts: Mapping[str, int],
) -> pl.DataFrame | None:
    """The parents' typed rows combined as ``input_values`` combines their values."""
    parts: list[pl.DataFrame] = []
    for parent_id in parent_ids:
        if typed_rows.cached_rows.get(parent_id) is None:
            continue
        parent_row = typed_rows.row(parent_id, consumer=node_id)
        if parent_row is None:
            return None
        parts.append(
            parent_row.rename(
                {
                    name: f"{parent_id}.{name}"
                    for name in parent_row.columns
                    if key_counts.get(name, 0) > 1
                }
            )
        )
    return (
        pl.DataFrame([column for part in parts for column in part.get_columns()]) if parts else None
    )


def _assemble_steps(
    *,
    order: list[str],
    source_ids: set[str],
    node_map: dict[str, Any],
    parents_of: dict[str, list[str]],
    cached_rows: dict[str, dict[str, Any] | None],
    typed_rows: _TypedRows | None = None,
) -> list[TraceStep]:
    """Build TraceStep entries from the post-hoc-correlated per-node rows.

    Skips nodes where row correlation produced ``None`` (better to omit
    than to show wrong data). With *typed_rows*, each step also carries its
    rows as one-row frames for evaluating its formulas.
    """
    steps: list[TraceStep] = []

    for topological_rank, nid in enumerate(order):
        is_source = nid in source_ids
        node_data = node_map[nid].data
        node_name = node_data.label
        node_type = node_data.nodeType

        output_row = cached_rows.get(nid)

        # Skip nodes where row correlation failed — better to show
        # nothing than to show incorrect data from a wrong row.
        # But keep nodes with empty dicts (they may still get enrichment).
        if output_row is None:
            continue

        input_row: dict[str, Any] | None
        typed_input: pl.DataFrame | None = None
        if is_source:
            input_row = None
            provenance_aliases: dict[str, str] = {}
        else:
            input_ids = parents_of.get(nid, [])
            provenance_aliases = {}
            if input_ids:
                input_row = {}
                parent_rows = {
                    pid: cached_rows.get(pid)
                    for pid in input_ids
                    if cached_rows.get(pid) is not None
                }
                key_counts: dict[str, int] = {}
                for parent_row in parent_rows.values():
                    assert parent_row is not None
                    for key in parent_row:
                        key_counts[key] = key_counts.get(key, 0) + 1
                for pid in input_ids:
                    parent_row = parent_rows.get(pid)
                    if parent_row is None:
                        # Parent row correlation failed — skip this parent
                        continue
                    for k, v in parent_row.items():
                        # Namespace every collision, irrespective of parent
                        # order: an unqualified first value would make the
                        # trace's provenance depend on graph ordering.
                        key = f"{pid}.{k}" if key_counts[k] > 1 else k
                        if key != k:
                            provenance_aliases[key] = k
                        input_row[key] = v
                if typed_rows is not None:
                    typed_input = _typed_input_row(typed_rows, nid, input_ids, key_counts)
            else:
                input_row = {}

        schema_diff = _compute_schema_diff(
            input_row,
            output_row,
            provenance_aliases=provenance_aliases,
        )

        steps.append(
            TraceStep(
                node_id=nid,
                node_name=node_name,
                node_type=node_type,
                schema_diff=schema_diff,
                input_values=input_row if input_row is not None else {},
                output_values=output_row,
                topological_rank=topological_rank,
                input_row=typed_input,
                output_row=typed_rows.row(nid) if typed_rows is not None else None,
            )
        )

    return steps


def _materialized_output_columns(output: Any) -> set[str]:
    if isinstance(output, pl.DataFrame):
        return set(output.columns)
    if isinstance(output, dict):
        return {
            column
            for frame in output.values()
            if isinstance(frame, pl.DataFrame)
            for column in frame.columns
        }
    return set()


def _build_trace_omissions(
    *,
    unresolved_rows: dict[str, tuple[str, int]],
    order: list[str],
    node_map: dict[str, Any],
    eager_outputs: dict[str, Any],
    steps: list[TraceStep],
    column: str | None,
    always_relevant: frozenset[str] = frozenset(),
) -> list[TraceOmission]:
    """Build evidence entries only for unresolved nodes relevant to the trace.

    Successful column pruning is deliberately not represented as a gap. For a
    column trace, observed output schemas and authoritative expression
    references identify which failed ancestors could have contributed. When an
    assigning expression cannot identify its inputs, the existing conservative
    relevance fallback keeps all attempted unresolved ancestors.
    """
    if not unresolved_rows:
        return []

    relevant_node_ids = set(unresolved_rows)
    if column is not None:
        referenced_columns: set[str] = set()
        origin_found = False
        origin_without_references = False
        for step in steps:
            diff = step.schema_diff
            is_origin = column in diff.columns_added or column in diff.columns_modified
            if not is_origin:
                continue
            origin_found = True
            references = (
                step.expression.get("referenced_columns", [])
                if isinstance(step.expression, dict)
                else []
            )
            if references:
                referenced_columns.update(str(name) for name in references)
            else:
                origin_without_references = True

        if origin_found and not origin_without_references:
            relevant_columns = {column, *referenced_columns}
            relevant_node_ids = {
                node_id
                for node_id in unresolved_rows
                if _materialized_output_columns(eager_outputs.get(node_id)) & relevant_columns
            }
    relevant_node_ids |= always_relevant & set(unresolved_rows)

    ranks = {node_id: rank for rank, node_id in enumerate(order)}
    omissions: list[TraceOmission] = []
    for node_id in sorted(relevant_node_ids, key=lambda value: ranks.get(value, len(order))):
        reason, diagnostic_index = unresolved_rows[node_id]
        if diagnostic_index < 0:
            # execute_trace always supplies a diagnostics list; fail clearly if
            # a future call path violates the linkable-evidence invariant.
            raise ValueError(f"Trace omission for node {node_id!r} has no correlation diagnostic")
        node = node_map[node_id]
        omissions.append(
            TraceOmission(
                node_id=node_id,
                node_name=node.data.label,
                node_type=node.data.nodeType,
                topological_rank=ranks[node_id],
                reason=reason,
                diagnostic_index=diagnostic_index,
            )
        )
    return omissions


def _prune_to_column_relevance(
    steps: list[TraceStep],
    column: str,
    parents_of: dict[str, list[str]],
    node_map: dict[str, Any],
) -> list[TraceStep]:
    """Tag column relevance and prune steps that don't contribute to *column*.

    Two cases:
      1. Pass-through column (e.g. VehGas): exists in multiple nodes' output.
         Keep only nodes whose output contains the column — this prunes
         unrelated source branches (e.g. claims/exposure when tracing VehGas
         which only comes from policies).
      2. Calculated or modified column (e.g. premium): nodes that assign the
         traced column define the value seen downstream.  Their referenced
         inputs must stay in the trace even when they live on branches that do
         not themselves carry the traced column.
    """
    _tag_column_relevance(steps, column)

    # Find nodes where the traced value is assigned.  Later modifications are
    # origins for the downstream value just as much as the first creation is.
    origin_ids = {
        s.node_id
        for s in steps
        if column in s.schema_diff.columns_added or column in s.schema_diff.columns_modified
    }

    # Also check for nodes whose code creates the column (for failed-execution cases)
    for s in steps:
        nd = node_map.get(s.node_id)
        if nd:
            cfg = nd.data.config if isinstance(nd.data.config, dict) else {}
            rc = cfg.get("code", "") or ""
            if rc and ".with_columns(" in rc and re.search(rf"\b{re.escape(column)}\s*=", rc):
                origin_ids.add(s.node_id)
                s.column_relevant = True

    # Collect ancestors that actually contribute to the formula.
    # If the expression tells us which columns are referenced (e.g.
    # burn_cost = premium * 0.7 references ["premium"]), only keep
    # ancestors that produce those columns.  This prunes unrelated
    # branches (e.g. competitor_scoring when tracing burn_cost).
    ancestor_ids: set[str] = set()
    contributing_ids: set[str] = set()
    if origin_ids:
        # Check if expressions tell us what columns matter.  Multiple nodes may
        # assign the traced column; later assignments can reference side-branch
        # columns that the first creation did not.
        ref_cols: set[str] = set()
        for s in steps:
            if s.node_id in origin_ids and s.expression:
                expr_refs = s.expression.get("referenced_columns", [])
                if expr_refs:
                    ref_cols.update(expr_refs)

        if ref_cols:
            # Targeted walk: find nodes that produce referenced columns
            # and only walk their ancestors
            contributing_ids = set(origin_ids)
            for s in steps:
                if s.node_id in origin_ids:
                    continue
                sd = s.schema_diff
                produced = set(sd.columns_added) | set(sd.columns_modified)
                if produced & ref_cols:
                    contributing_ids.add(s.node_id)
                elif any(c in s.output_values for c in ref_cols):
                    contributing_ids.add(s.node_id)
            queue = list(contributing_ids)
            while queue:
                nid = queue.pop()
                for pid in parents_of.get(nid, []):
                    if pid not in ancestor_ids:
                        ancestor_ids.add(pid)
                        queue.append(pid)
        else:
            # No expression info — fall back to keeping all ancestors
            queue = list(origin_ids)
            while queue:
                nid = queue.pop()
                for pid in parents_of.get(nid, []):
                    if pid not in ancestor_ids:
                        ancestor_ids.add(pid)
                        queue.append(pid)

    # Also keep contributing nodes (those that produce referenced columns)
    keep_ids = ancestor_ids | origin_ids
    if contributing_ids:
        keep_ids |= contributing_ids
    return [s for s in steps if s.column_relevant or s.node_id in keep_ids]


# ---------------------------------------------------------------------------
# Column relevance tagging
# ---------------------------------------------------------------------------


def _tag_column_relevance(steps: list[TraceStep], column: str) -> None:
    """Tag each step with whether its output contains the target column.

    After tagging, the caller filters steps — see execute_trace() for the
    two-case logic (pass-through vs calculated columns).
    """
    for step in steps:
        sd = step.schema_diff
        step.column_relevant = (
            column in sd.columns_added
            or column in sd.columns_modified
            or column in sd.columns_passed
            or column in step.output_values
        )


# ---------------------------------------------------------------------------
# Serialisation - TraceResult → JSON-safe dict
# ---------------------------------------------------------------------------


def trace_result_to_dict(result: TraceResult) -> dict[str, Any]:
    """Convert a TraceResult to a JSON-serialisable dict for the API."""
    payload = {
        "target_node_id": result.target_node_id,
        "row_index": result.row_index,
        "column": result.column,
        "output_value": result.output_value,
        "steps": [
            {
                "node_id": s.node_id,
                "node_name": s.node_name,
                "node_type": s.node_type,
                "schema_diff": {
                    "columns_added": s.schema_diff.columns_added,
                    "columns_removed": s.schema_diff.columns_removed,
                    "columns_modified": s.schema_diff.columns_modified,
                    "columns_passed": s.schema_diff.columns_passed,
                },
                "input_values": s.input_values,
                "output_values": s.output_values,
                "topological_rank": s.topological_rank,
                "column_relevant": s.column_relevant,
                "expression": s.expression,
                "calculation": s.calculation,
                "node_detail": s.node_detail,
                "row_lineage_type": s.row_lineage_type,
                "snapshot_generation_id": s.snapshot_generation_id,
            }
            for s in result.steps
        ],
        "omissions": [
            {
                "node_id": omission.node_id,
                "node_name": omission.node_name,
                "node_type": omission.node_type,
                "topological_rank": omission.topological_rank,
                "reason": omission.reason,
                "diagnostic_index": omission.diagnostic_index,
            }
            for omission in result.omissions
        ],
        "row_id_column": result.row_id_column,
        "row_id_value": result.row_id_value,
        "total_nodes_in_pipeline": result.total_nodes_in_pipeline,
        "nodes_in_trace": result.nodes_in_trace,
        "execution_ms": result.execution_ms,
        "waterfall": result.waterfall,
        "correlation_diagnostics": result.correlation_diagnostics,
        "generated_at": result.generated_at,
        "pipeline_source": result.pipeline_source,
        "execution_origin": result.execution_origin,
    }
    return cast(dict[str, Any], to_json_safe(payload))
