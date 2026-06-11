"""Execution trace: single-row instrumented pipeline execution.

Runs a pipeline graph on a single row and captures per-node snapshots
(input schema, output schema, row values, schema diffs).  This is the
foundation for the data-lineage / explainability feature described in
ARCHITECTURE.md §9.3.

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
  * ``_trace_export``       — TraceResult → report-shape dict.

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
from typing import Any, Protocol, runtime_checkable

import polars as pl

from haute._cache import GraphFingerprintMemo
from haute._expression_parser import (
    evaluate_expression,
    parse_expression,
    parse_expression_chain,
)
from haute._fingerprint_cache import FingerprintCache
from haute._json_safe import to_json_safe
from haute._logging import get_logger
from haute._trace_correlation import (
    SchemaDiff,
    _compute_schema_diff,
    _correlate_rows_posthoc,
    _jsonify_row,
    _trace_values_match,
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
from haute.execution import runtime_input_extra_keys
from haute.executor import (
    ENFORCE_CONTRACTS,
    PREVIEW_CACHE_MAX_BYTES,
    _build_node_fn,
    _compile_preamble,
    _estimate_preview_cache_entry_bytes,
    _pipeline_dir,
    _positive_int_from_env,
    _preview_projection_cache_suffix,
)
from haute.graph_utils import (
    NodeType,
    PipelineGraph,
    _execute_eager_core,
    _prepare_graph,
    graph_fingerprint,
    topo_sort_ids,
)

logger = get_logger(component="trace")

__all__ = [
    "PreviewReader",
    "SchemaDiff",
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

    Any object that exposes ``try_get(fingerprint) -> dict | None``
    satisfies this protocol.  ``FingerprintCache`` already does so by
    construction, which is why the production route handler forwards the
    executor's preview cache directly and tests can inject a trivial
    stub without touching ``haute.executor``.
    """

    def try_get(self, fingerprint: str) -> dict[str, Any] | None:
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

    # True if this node adds/modifies/passes the traced column
    column_relevant: bool = True

    # Execution time for this node (ms)
    execution_ms: float = 0.0

    # Expression parsing and enrichment, populated by _enrich_steps.
    expression: dict[str, Any] | None = None
    calculation: dict[str, Any] | None = None
    node_detail: dict[str, Any] | None = None
    row_lineage_type: str | None = None

    @property
    def row_data(self) -> dict[str, Any]:
        """Alias for output_values — used by export and display layers."""
        return self.output_values


@dataclass
class TraceResult:
    """Full trace for one row through the pipeline."""

    target_node_id: str
    row_index: int
    column: str | None
    output_value: Any

    steps: list[TraceStep]

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


_cache = FingerprintCache(
    slots=("eager_outputs", "order", "parents_of", "node_map", "source_ids"),
    max_bytes=TRACE_CACHE_MAX_BYTES,
    size_of=_estimate_preview_cache_entry_bytes,
    size_sensitive_slots=("eager_outputs",),
)


# Enrichment + expression-parser names are imported at the top of this
# module and re-exported via ``__all__`` so tests can
# ``monkeypatch.setattr("haute.trace.parse_expression", …)`` and the
# dispatch walk in ``_trace_enrichment.enrich_steps`` sees the patched
# version via its ``sys.modules["haute.trace"]`` lookup.


# ---------------------------------------------------------------------------
# Main trace executor
# ---------------------------------------------------------------------------


def _find_target_row_index(df: pl.DataFrame, row_values: dict[str, Any]) -> int | None:
    """Find the target row that exactly matches the GUI's clicked row values."""
    shared = [col for col in row_values if col in df.columns]
    if not shared:
        return None

    for idx, row in enumerate(df.select(shared).iter_rows(named=True)):
        if all(_trace_values_match(row.get(col), row_values.get(col)) for col in shared):
            return idx
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
                 object implementing :class:`PreviewReader` (``try_get``)
                 or a pre-materialised snapshot dict with an
                 ``eager_outputs`` slot.  When provided the trace reuses
                 the materialised DataFrames instead of re-executing the
                 upstream graph; when ``None`` (tests, CLI, cold requests)
                 the trace falls back to a fresh execution.  This keeps the
                 trace module decoupled from ``haute.executor._preview_cache``.

    Returns:
        TraceResult with per-node steps showing how the row was produced.
    """
    t_start = time.perf_counter()

    nodes = graph.nodes
    edges = graph.edges

    if not nodes:
        raise ValueError("Empty graph - nothing to trace")

    # Resolve target before _prepare_graph filters to ancestors.
    # Pass the node list in its declared order (not a set) so the topo
    # sort's insertion-order tie-break is deterministic — the previous
    # set-derived list made the chosen sink depend on CPython hash
    # randomisation across process invocations.
    if target_node_id is None:
        target_node_id = topo_sort_ids([n.id for n in nodes], edges)[-1]
    if not any(n.id == target_node_id for n in nodes):
        raise ValueError(f"Target node '{target_node_id}' not found in graph")

    # ---------- Eager execution with single-entry cache ----------
    # Model-scoring nodes can take ~1s on large datasets (678K rows).
    # The pipeline structure doesn't change between trace clicks — only the
    # row_index and column change.  Cache the materialized DataFrames and
    # reuse them: first click ~1.7s, subsequent clicks <10ms.
    fingerprint_memo = GraphFingerprintMemo()
    # Runtime-input extras (flat-file dataSource / external-file / model-
    # artifact signatures + the apiInput JSON-cache state) are part of the
    # trace key so an out-of-band re-export or cache rebuild invalidates
    # cached trace frames.  Computed once and shared with the preview-key
    # reconstruction below so one trace observes one input state and the
    # keys match executor.py's construction exactly.
    runtime_extra_keys = runtime_input_extra_keys(graph)
    fp = graph_fingerprint(
        graph,
        target_node_id,
        f"{row_limit}:{source}",
        *runtime_extra_keys,
        memo=fingerprint_memo,
    )

    cached = _cache.try_get(fp)
    if cached is not None:
        cache_hit = True
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
            prev_fingerprint=(_cache.fingerprint or "")[:8],
        )

        base_preview_key = f"{row_limit}:{source}:contracts={int(ENFORCE_CONTRACTS)}"
        requested_preview_columns = _requested_preview_columns_from_row(row_values, column)
        preview_fps: list[str] = []
        if requested_preview_columns is not None:
            preview_fps.append(
                graph_fingerprint(
                    graph,
                    base_preview_key
                    + _preview_projection_cache_suffix(
                        graph,
                        target_node_id,
                        requested_preview_columns,
                        target_preview_only=True,
                        initial_column_limit=None,
                    ),
                    *runtime_extra_keys,
                    memo=fingerprint_memo,
                )
            )
        preview_fps.append(
            graph_fingerprint(
                graph,
                base_preview_key,
                *runtime_extra_keys,
                memo=fingerprint_memo,
            )
        )

        eager_outputs, order, parents_of, node_map, source_ids = _materialize_eager_outputs(
            graph=graph,
            target_node_id=target_node_id,
            row_limit=row_limit,
            source=source,
            row_values=row_values,
            preamble_ns=preamble_ns,
            # Match executor.py's preview cache keys.  Projected preview
            # requests include the visible row columns in the suffix; the
            # unsuffixed key remains the fallback for full preview calls.
            preview_fps=preview_fps,
            fp=fp,
            preview=preview,
        )

        # Populate cache — unmodified DataFrames from the single execution
        _cache.store(
            fp,
            eager_outputs=eager_outputs,
            order=order,
            parents_of=parents_of,
            node_map=node_map,
            source_ids=source_ids,
        )

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
            actual_row = target_df.row(row_index, named=True)
            mismatched = []
            for col, expected in row_values.items():
                actual = actual_row.get(col)
                if not _trace_values_match(actual, expected):
                    mismatched.append(col)
            row_matches = not mismatched

        if not row_matches:
            # The backend preview cache can be evicted between the GUI
            # preview request and the user's trace click. A cold trace
            # may still reproduce the clicked row, but joins can reorder
            # rows. Treat the clicked values as the source of truth and
            # relocate the target row before correlating upstream rows.
            matched_index = _find_target_row_index(target_df, row_values)
            if matched_index is not None:
                row_index = matched_index
            else:
                raise ValueError(
                    "Trace data does not match the preview row. "
                    "The preview data may have changed. "
                    "Please click the node to refresh, then retry."
                )

    correlation_diagnostics: list[dict[str, Any]] = []

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
        )
    else:
        # Target node execution failed — build partial rows from available nodes
        cached_rows = {}
        for nid in order:
            if nid in eager_outputs:
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
    )

    # ---------- Column relevance: tag then prune irrelevant ancestors ----------
    if column:
        steps = _prune_to_column_relevance(steps, column, parents_of, node_map)

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
        waterfall_data = build_waterfall_from_steps(
            steps,
            column,
            target_node_id=target_node_id,
            final_output_value=output_value,
        )

    return TraceResult(
        target_node_id=target_node_id,
        row_index=row_index,
        column=column,
        output_value=output_value,
        steps=steps,
        row_id_column=row_id_column,
        row_id_value=row_id_value,
        total_nodes_in_pipeline=len(nodes),
        nodes_in_trace=len(steps),
        execution_ms=total_ms,
        waterfall=waterfall_data,
        correlation_diagnostics=correlation_diagnostics,
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
    * A reader with ``try_get(fingerprint) -> dict | None`` — we call it
      with each candidate fingerprint and return the first hit. The executor's
      ``FingerprintCache`` satisfies this protocol unchanged.
    * A snapshot dict — treated as a pre-materialised cache entry.  The
      caller has already done the fingerprint lookup, so we return the
      dict verbatim without consulting *preview_fp*.

    This indirection is what lets :func:`execute_trace` stay agnostic to
    where the preview data came from (executor cache, unit-test stub,
    future Redis-backed reader, …).
    """
    if preview is None:
        return None
    # Duck-type the reader protocol: ``FingerprintCache`` and test stubs
    # both expose ``try_get``.  ``isinstance(..., PreviewReader)`` would
    # also work since the Protocol is ``@runtime_checkable``, but
    # ``hasattr`` is explicit about what we actually call.
    try_get = getattr(preview, "try_get", None)
    if callable(try_get):
        for preview_fp in preview_fps:
            result = try_get(preview_fp)
            if result is None:
                continue
            if not isinstance(result, dict):
                raise TypeError(
                    f"PreviewReader.try_get must return dict | None, got {type(result).__name__}"
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
) -> tuple[
    dict[str, pl.DataFrame],
    list[str],
    dict[str, list[str]],
    dict[str, Any],
    set[str],
]:
    """Populate the trace cache: reuse preview outputs if available, else execute.

    Returns ``(eager_outputs, order, parents_of, node_map, source_ids)``.

    The *preview* parameter is the sole source of preview-cache data.
    Passing ``None`` forces a cold execution; passing a reader or a
    snapshot dict lets callers reuse already-materialised DataFrames
    without this module reaching into ``haute.executor``'s private
    singleton.
    """
    # --- Try to reuse outputs from the injected preview ---------------
    # The injected preview is either a reader object (``try_get(fp) ->
    # dict | None``; the executor's FingerprintCache satisfies this
    # protocol) or a pre-materialised snapshot dict.  ``None`` disables
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
            node_map, order, parents_of, _id_to_name = _prepare_graph(
                graph,
                target_node_id,
                source=source,
            )
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
                return eager_outputs, order, parents_of, node_map, source_ids

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
    )
    eager_outputs = {nid: df for nid, df in result.outputs.items() if df is not None}
    order = result.order
    parents_of = result.parents_of
    node_map = result.node_map
    source_ids = {nid for nid in order if not parents_of.get(nid)}
    return eager_outputs, order, parents_of, node_map, source_ids


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

    for nid in order:
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
        else:
            input_ids = parents_of.get(nid, [])
            if input_ids:
                input_row = {}
                for pid in input_ids:
                    parent_row = cached_rows.get(pid)
                    if parent_row is None:
                        # Parent row correlation failed — skip this parent
                        continue
                    for k, v in parent_row.items():
                        # Namespace-prefix on collision to avoid overwriting
                        key = f"{pid}.{k}" if k in input_row else k
                        input_row[key] = v
            else:
                input_row = {}

        schema_diff = _compute_schema_diff(input_row, output_row)

        steps.append(
            TraceStep(
                node_id=nid,
                node_name=node_name,
                node_type=node_type,
                schema_diff=schema_diff,
                input_values=input_row if input_row is not None else {},
                output_values=output_row,
            )
        )

    return steps


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
    return {
        "target_node_id": result.target_node_id,
        "row_index": result.row_index,
        "column": result.column,
        "output_value": to_json_safe(result.output_value),
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
                "input_values": to_json_safe(s.input_values),
                "output_values": to_json_safe(s.output_values),
                "column_relevant": s.column_relevant,
                "execution_ms": s.execution_ms,
                "expression": to_json_safe(s.expression),
                "calculation": to_json_safe(s.calculation),
                "node_detail": to_json_safe(s.node_detail),
                "row_lineage_type": s.row_lineage_type,
            }
            for s in result.steps
        ],
        "row_id_column": result.row_id_column,
        "row_id_value": to_json_safe(result.row_id_value),
        "total_nodes_in_pipeline": result.total_nodes_in_pipeline,
        "nodes_in_trace": result.nodes_in_trace,
        "execution_ms": result.execution_ms,
        "waterfall": to_json_safe(result.waterfall),
        "correlation_diagnostics": to_json_safe(result.correlation_diagnostics),
    }
