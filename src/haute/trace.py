"""Execution trace: single-row instrumented pipeline execution.

Runs a pipeline graph on a single row and captures per-node snapshots
(input schema, output schema, row values, schema diffs).  This is the
foundation for the data-lineage / explainability feature specified in
docs/specs/tracing/high-level.md.

Current surface:
  • execute_trace()  - run graph, collect 1-row snapshots at every node
  • SchemaDiff       - classify columns as added/removed/modified/passed
  • TraceStep / TraceResult dataclasses

The trace is a pure observation layer — it never modifies the execution
pipeline.  It uses the same DataFrames produced by the preview execution
and correlates rows between parent and child nodes post-hoc using column
value matching.  This guarantees that the trace always shows exactly the
data the user sees in the preview table.

Module layout — this file is the public facade and execute-trace
orchestrator.  Heavy lifting lives in sibling modules:

  * ``_trace_correlation``  — post-hoc row-value correlation, schema
    diff, JSON-safe row coercion.
  * ``_trace_enrichment``   — per-step enrichment dispatch
    (``enrich_steps``) plus node-type enrichers (rating step, banding,
    model score, scenario expansion, live switch, row lineage).
  * ``_trace_waterfall``    — sequential multiplicative / additive
    waterfall assembly.

This module re-imports a handful of names (``parse_expression``,
``evaluate_expression``, ``parse_expression_chain``, the node-type
enrichers) at module scope so that tests can ``monkeypatch.setattr`` on
``haute.trace.<name>`` and have the dispatch walk in
``_trace_enrichment`` pick up the patched version via its
``sys.modules["haute.trace"]`` lookup.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast, runtime_checkable

import polars as pl

import haute.execution as execution_facade
from haute._cache import GraphFingerprintMemo
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._expression_parser import (
    evaluate_expression,
    parse_expression,
    parse_expression_chain,
)
from haute._json_safe import to_json_safe
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._trace_correlation import (
    SchemaDiff,
    _compute_schema_diff,
    _correlate_rows_posthoc,
    _jsonify_row,
    _match_rows_vectorized,
    _RowMatchStatus,
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
    _positive_int_from_env,
)
from haute.graph_utils import (
    NodeType,
    PipelineGraph,
    _execute_eager_core,
    topo_sort_ids,
)

logger = get_logger(component="trace")

__all__ = [
    "PreviewReader",
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
# Preview injection — decouples the trace from ``haute.executor``'s private
# ``_preview_cache`` singleton.  Callers (the FastAPI route handler, tests,
# future CLI commands) construct a snapshot or reader themselves and pass
# it in.  This keeps the trace a pure observation layer with an explicit
# data dependency instead of a module-level reach-through.
# ---------------------------------------------------------------------------


@runtime_checkable
class PreviewReader(Protocol):
    """Read-only preview-cache lookup surface used by :func:`execute_trace`.

    Any object that exposes ``get(fingerprint) -> dict | None`` satisfies
    this protocol, so the production route handler can forward the
    executor's cache directly and tests can inject a trivial stub without
    touching ``haute.executor``.
    """

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        """Return the preview slot-dict for *fingerprint*, or ``None`` on miss."""
        ...


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


TRACE_CACHE_MAX_BYTES = _positive_int_from_env(
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
    preview: PreviewReader | dict[str, Any] | None = None,
    fingerprint_memo: GraphFingerprintMemo | None = None,
    execution_context: ExecutionContext | None = None,
) -> TraceResult:
    """Execute a pipeline graph and return a single-row trace.

    The trace is a pure observation layer — it uses the same DataFrames
    produced by the preview execution and correlates rows between parent
    and child nodes post-hoc.  The execution pipeline is never modified.

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
        preview: Optional preview-cache lookup surface — either a reader
                 object implementing :class:`PreviewReader` (``get``)
                 or a pre-materialised snapshot dict with an
                 ``eager_outputs`` slot.  When provided the trace reuses
                 the materialised DataFrames instead of re-executing the
                 upstream graph; when ``None`` (tests, CLI, cold requests)
                 the trace falls back to a fresh execution.  This keeps the
                 trace module decoupled from ``haute.executor._preview_cache``.
        fingerprint_memo: Optional request-scoped
                 :class:`~haute._cache.GraphFingerprintMemo` shared with the
                 caller (the trace route reuses the memo from its
                 supersession-key computation) so preamble utility files are
                 hashed at most once per request.  ``None`` creates a fresh
                 memo scoped to this call.

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
                preview=preview,
                fingerprint_memo=fingerprint_memo,
                execution_context=admitted_context,
            )
        finally:
            admitted_context.release_admission(preserve_primary_error=True)

    requested_columns = _requested_preview_columns_from_row(row_values, column)
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
    else:
        cache_hit = False
        logger.debug(
            "trace_cache_miss",
            fingerprint=fp[:8],
            target=target_node_id,
            prev_fingerprint=(_cache.most_recent_key or "")[:8],
        )

        # A target-only preview deliberately retains only the selected node,
        # so it can never satisfy a trace's full-ancestor evidence invariant.
        # Consult only the exact full-lineage key; projected target previews
        # otherwise add a guaranteed miss before every first trace click.
        preview_fps = [fp]

        (
            eager_outputs,
            order,
            parents_of,
            node_map,
            source_ids,
            execution_origin,
        ) = _materialize_eager_outputs(
            graph=graph,
            target_node_id=target_node_id,
            row_limit=row_limit,
            source=source,
            row_values=row_values,
            preamble_ns=preamble_ns,
            preview_fps=preview_fps,
            fp=fp,
            preview=preview,
            execution_context=execution_context,
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

    # Multi-frame sources (e.g. a ≥2-table apiInput) store a
    # dict[label, DataFrame] in eager_outputs; a trace must target a node
    # downstream of a specific frame, never the bundle itself.
    if isinstance(eager_outputs.get(target_node_id), dict):
        raise ValueError(
            f"Target node {target_node_id!r} emits multiple frames; "
            "trace a node downstream of a specific frame instead."
        )

    # Edge sourceHandles record which frame of a multi-frame source each
    # child EDGE consumes (the selection _pick_source_frame makes at
    # execution time); the correlation walk makes the same per-edge
    # selection.  One entry per edge: a multi-frame source can feed the
    # same child through several edges, each naming a distinct frame
    # (e.g. the four-port apiInput → OUTPUT topology), so collapsing to
    # one handle per (source, target) pair would correlate against an
    # arbitrary frame.
    source_frames_of: dict[tuple[str, str], list[str | None]] = {}
    for e in graph.edges:
        source_frames_of.setdefault((e.source, e.target), []).append(e.sourceHandle)

    # ---------- Verify row identity ----------
    # If the frontend sent the clicked row's values, verify that the
    # DataFrame at the target node has the same values at row_index.
    # A mismatch means the preview and trace are using different
    # DataFrames (e.g., due to non-deterministic Polars join ordering
    # after a cache miss).
    if row_values is not None:
        target_df = eager_outputs[target_node_id]
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

    # Extract correct row from each node via post-hoc correlation
    # (only if target node has output data)
    if target_node_id in eager_outputs:
        cached_rows = _correlate_rows_posthoc(
            eager_outputs,
            order,
            parents_of,
            target_node_id,
            row_index,
            node_map=node_map,
            diagnostics=correlation_diagnostics,
            unresolved=unresolved_rows,
            source_frames_of=source_frames_of,
            traced_column=column,
        )
    else:
        # Target node execution failed — build partial rows from available nodes
        cached_rows = {}
        for nid in order:
            if nid in eager_outputs and not isinstance(eager_outputs[nid], dict):
                df = eager_outputs[nid]
                if row_index < len(df):
                    cached_rows[nid] = _jsonify_row(df.row(row_index, named=True))
                else:
                    cached_rows[nid] = {}
            else:
                cached_rows[nid] = {}

    # ---------- Build trace steps from cached rows ----------
    steps = _assemble_steps(
        order=order,
        source_ids=source_ids,
        node_map=node_map,
        parents_of=parents_of,
        cached_rows=cached_rows,
    )

    # ---------- Enrich steps with expression/detail data ----------
    _enrich_steps(
        steps,
        node_map,
        eager_outputs,
        parents_of,
        column,
        source,
        preamble_ns=preamble_ns,
        source_frames_of=source_frames_of,
    )

    # ---------- Column relevance: tag then prune irrelevant ancestors ----------
    if column:
        steps = _prune_to_column_relevance(steps, column, parents_of, node_map)

    omissions = _build_trace_omissions(
        unresolved_rows=unresolved_rows,
        order=order,
        node_map=node_map,
        eager_outputs=eager_outputs,
        steps=steps,
        column=column,
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
        integer_output_node_ids = _integer_output_node_ids(eager_outputs, steps, column)
        waterfall_data = build_waterfall_from_steps(
            steps,
            column,
            target_node_id=target_node_id,
            final_output_value=output_value,
            parents_of=parents_of,
            node_map=node_map,
            integer_output_node_ids=integer_output_node_ids,
            final_output_is_integer=_is_integer_output_column(
                eager_outputs,
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


def _resolve_preview_snapshot(
    preview: PreviewReader | dict[str, Any] | None,
    preview_fps: list[str],
) -> tuple[dict[str, Any], str] | None:
    """Normalise *preview* into the slot-dict shape or ``None``.

    Accepts three input shapes so callers can inject whichever is
    cheapest to construct:

    * ``None`` — caller opted out of preview reuse; returns ``None``.
    * A reader with ``get(fingerprint) -> dict | None`` — we call it with
      each candidate fingerprint and return the first hit.
    * A snapshot dict — treated as a pre-materialised cache entry.  The
      caller has already done the fingerprint lookup, so we return the
      dict verbatim without consulting *preview_fp*.

    This indirection is what lets :func:`execute_trace` stay agnostic to
    where the preview data came from (executor cache, unit-test stub,
    future Redis-backed reader, …).
    """
    if preview is None:
        return None
    # Duck-type the reader protocol. ``isinstance(..., PreviewReader)`` would
    # also work since the Protocol is ``@runtime_checkable``, but
    # ``hasattr`` is explicit about what we actually call.
    get = getattr(preview, "get", None)
    if callable(get):
        for preview_fp in preview_fps:
            result = get(preview_fp)
            if result is None:
                continue
            if not isinstance(result, dict):
                raise TypeError(
                    f"PreviewReader.get must return dict | None, got {type(result).__name__}"
                )
            return result, preview_fp
        return None
    if isinstance(preview, dict):
        return preview, preview_fps[0] if preview_fps else ""
    raise TypeError(
        "execute_trace(preview=...) expects a PreviewReader, a snapshot dict, or None; "
        f"got {type(preview).__name__}"
    )


def _materialize_eager_outputs(
    *,
    graph: PipelineGraph,
    target_node_id: str,
    row_limit: int,
    source: str,
    row_values: dict[str, Any] | None,
    preamble_ns: dict[str, Any] | None,
    preview_fps: list[str],
    fp: str,
    preview: PreviewReader | dict[str, Any] | None,
    execution_context: ExecutionContext | None,
) -> tuple[
    dict[str, pl.DataFrame],
    list[str],
    dict[str, list[str]],
    dict[str, Any],
    set[str],
    str,
]:
    """Populate the trace cache: reuse preview outputs if available, else execute.

    Returns ``(eager_outputs, order, parents_of, node_map, source_ids,
    execution_origin)``.

    The *preview* parameter is the sole source of preview-cache data.
    Passing ``None`` forces a cold execution; passing a reader or a
    snapshot dict lets callers reuse already-materialised DataFrames
    without this module reaching into ``haute.executor``'s private
    singleton.
    """
    # --- Try to reuse outputs from the injected preview ---------------
    # The injected preview is either a reader object (``get(fp) -> dict |
    # None``) or a pre-materialised snapshot dict. ``None`` disables
    # cache lookup entirely and forces a fresh execution.
    preview_lookup = _resolve_preview_snapshot(preview, preview_fps)
    preview_data = preview_lookup[0] if preview_lookup is not None else None
    matched_preview_fp = preview_lookup[1] if preview_lookup is not None else ""

    if preview_data is not None:
        # Snapshot dicts without an ``eager_outputs`` slot are treated
        # as empty — the cold-execute path below will handle them.
        prev_outputs = preview_data.get("eager_outputs") or {}
        # Preview uses swallow_errors=True, so some outputs may be None on
        # error.  Only reuse when the full ancestor chain is present; target-
        # only previews intentionally cache just the selected node.
        if target_node_id in prev_outputs and prev_outputs[target_node_id] is not None:
            # Graph-structure metadata still needs computing for
            # the trace-specific fields (parents_of, node_map, etc.)
            prepared = execution_facade.prepare_graph(
                graph,
                target_node_id,
                source=source,
            )
            node_map = prepared.node_map
            order = prepared.order
            parents_of = prepared.parents_of
            # A full trace needs every executed ancestor so its waterfall
            # remains truthful. Partial target-only preview caches fall
            # through to cold trace execution below.
            missing_preview_nodes = [nid for nid in order if prev_outputs.get(nid) is None]
            if missing_preview_nodes:
                logger.debug(
                    "trace_preview_cache_partial",
                    fingerprint=fp[:8],
                    preview_fingerprint=matched_preview_fp[:8],
                    target=target_node_id,
                    missing_nodes=missing_preview_nodes,
                )
            else:
                eager_outputs = {nid: prev_outputs[nid] for nid in order}
                source_ids = {nid for nid in order if not parents_of.get(nid)}
                logger.debug(
                    "trace_reused_preview_cache",
                    fingerprint=fp[:8],
                    preview_fingerprint=matched_preview_fp[:8],
                    target=target_node_id,
                    reused_nodes=len(eager_outputs),
                )
                return (
                    eager_outputs,
                    order,
                    parents_of,
                    node_map,
                    source_ids,
                    "preview_cache",
                )

    # No usable preview cache. Execute fresh; if the frontend supplied
    # clicked row values, execute_trace verifies or relocates the target
    # row before correlation so the trace stays anchored to the preview
    # row the user clicked.
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
    result = _execute_eager_core(
        graph,
        _build_node_fn,
        target_node_id=target_node_id,
        row_limit=row_limit,
        swallow_errors=False,
        preamble_ns=effective_preamble or None,
        source=source,
        execution_context=execution_context,
    )
    eager_outputs = {nid: df for nid, df in result.outputs.items() if df is not None}
    order = result.order
    parents_of = result.parents_of
    node_map = result.node_map
    source_ids = {nid for nid in order if not parents_of.get(nid)}
    return eager_outputs, order, parents_of, node_map, source_ids, "fresh_execution"


def _assemble_steps(
    *,
    order: list[str],
    source_ids: set[str],
    node_map: dict[str, Any],
    parents_of: dict[str, list[str]],
    cached_rows: dict[str, dict[str, Any] | None],
) -> list[TraceStep]:
    """Build TraceStep entries from the post-hoc-correlated per-node rows.

    Skips nodes where row correlation produced ``None`` (better to omit
    than to show wrong data).
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
        origin_without_references = False
        for step in steps:
            diff = step.schema_diff
            is_origin = column in diff.columns_added or column in diff.columns_modified
            if not is_origin:
                continue
            references = (
                step.expression.get("referenced_columns", [])
                if isinstance(step.expression, dict)
                else []
            )
            if references:
                referenced_columns.update(str(name) for name in references)
            else:
                origin_without_references = True

        if not origin_without_references:
            relevant_columns = {column, *referenced_columns}
            relevant_node_ids = {
                node_id
                for node_id in unresolved_rows
                if _materialized_output_columns(eager_outputs.get(node_id)) & relevant_columns
            }

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
