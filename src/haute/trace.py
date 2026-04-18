"""Execution trace: single-row instrumented pipeline execution.

Runs a pipeline graph on a single row and captures per-node snapshots
(input schema, output schema, row values, schema diffs).  This is the
foundation for the data-lineage / explainability feature described in
ARCHITECTURE.md §9.3.

Phase A - what's here now:
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
from dataclasses import dataclass
from typing import Any

import polars as pl

from haute._fingerprint_cache import FingerprintCache
from haute._logging import get_logger
from haute._trace_correlation import (
    SchemaDiff,
    _compute_schema_diff,
    _correlate_rows_posthoc,
    _jsonify_row,
    _trace_values_match,
)
from haute._trace_enrichment import enrich_steps as _enrich_steps
from haute._trace_waterfall import build_waterfall_from_steps
from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir, _preview_cache
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
    "SchemaDiff",
    "TraceStep",
    "TraceResult",
    "execute_trace",
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

    # True if this node adds/modifies/passes the traced column
    column_relevant: bool = True

    # Execution time for this node (ms)
    execution_ms: float = 0.0

    # Expression parsing & enrichment (Phase B — populated by _enrich_steps)
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


# ---------------------------------------------------------------------------
# Execution cache — avoids re-running the full pipeline on every trace click.
# The graph structure (node IDs, types, code, paths, edges) is hashed into a
# fingerprint.  When only row_index or column changes, the cached per-node
# DataFrames are reused and we just extract a different row — sub-millisecond.
# ---------------------------------------------------------------------------


_cache = FingerprintCache(
    slots=("eager_outputs", "order", "parents_of", "node_map", "source_ids"),
)


# ---------------------------------------------------------------------------
# Enrichment imports — re-exported at module scope so tests can
# ``monkeypatch.setattr("haute.trace.parse_expression", …)`` and so the
# dispatch walk in ``_trace_enrichment.enrich_steps`` sees the patched
# version via its ``sys.modules["haute.trace"]`` lookup.
# ---------------------------------------------------------------------------


try:
    from haute._expression_parser import (
        evaluate_expression,
        parse_expression,
        parse_expression_chain,
    )

    _HAS_EXPRESSION_PARSER = True
except ImportError:
    _HAS_EXPRESSION_PARSER = False

try:
    from haute._trace_enrichment import (
        detect_row_lineage_type,
        enrich_banding,
        enrich_live_switch,
        enrich_model_score,
        enrich_rating_step,
        enrich_scenario_expansion,
    )

    _HAS_TRACE_ENRICHMENT = True
except ImportError:
    _HAS_TRACE_ENRICHMENT = False


# ---------------------------------------------------------------------------
# Main trace executor
# ---------------------------------------------------------------------------


def execute_trace(
    graph: PipelineGraph,
    row_index: int = 0,
    target_node_id: str | None = None,
    column: str | None = None,
    row_limit: int = 1000,
    source: str = "live",
    row_values: dict[str, Any] | None = None,
    preamble_ns: dict[str, Any] | None = None,
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

    Returns:
        TraceResult with per-node steps showing how the row was produced.
    """
    t_start = time.perf_counter()

    nodes = graph.nodes
    edges = graph.edges

    if not nodes:
        raise ValueError("Empty graph - nothing to trace")

    # Resolve target before _prepare_graph filters to ancestors
    if target_node_id is None:
        all_ids = {n.id for n in nodes}
        target_node_id = topo_sort_ids(list(all_ids), edges)[-1]
    if not any(n.id == target_node_id for n in nodes):
        raise ValueError(f"Target node '{target_node_id}' not found in graph")

    # ---------- Eager execution with single-entry cache ----------
    # Model-scoring nodes can take ~1s on large datasets (678K rows).
    # The pipeline structure doesn't change between trace clicks — only the
    # row_index and column change.  Cache the materialized DataFrames and
    # reuse them: first click ~1.7s, subsequent clicks <10ms.
    fp = graph_fingerprint(graph, target_node_id, f"{row_limit}:{source}")

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

        eager_outputs, order, parents_of, node_map, source_ids = _materialize_eager_outputs(
            graph=graph,
            target_node_id=target_node_id,
            row_limit=row_limit,
            source=source,
            row_values=row_values,
            preamble_ns=preamble_ns,
            preview_fp=graph_fingerprint(graph, f"{row_limit}:{source}"),
            fp=fp,
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
        if row_index < len(target_df):
            actual_row = target_df.row(row_index, named=True)
            mismatched = []
            for col, expected in row_values.items():
                actual = actual_row.get(col)
                if not _trace_values_match(actual, expected):
                    mismatched.append(col)
            if mismatched:
                raise ValueError(
                    f"Trace data does not match the preview row "
                    f"(mismatched columns: {mismatched[:5]}). "
                    f"The preview data may have changed. "
                    f"Please click the node to refresh, then retry."
                )

    # Extract correct row from each node via post-hoc correlation
    # (only if target node has output data)
    if target_node_id in eager_outputs:
        cached_rows = _correlate_rows_posthoc(
            eager_outputs,
            order,
            parents_of,
            target_node_id,
            row_index,
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

    # Build waterfall from trace steps — looks for sequential steps where
    # the traced column is modified by a multiplicative/additive operation.
    waterfall_data: list[dict[str, Any]] | dict[str, Any] | None = None
    if column:
        waterfall_data = build_waterfall_from_steps(
            steps,
            column,
            target_node_id=target_node_id,
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
    )


# ---------------------------------------------------------------------------
# execute_trace internals — materialize, assemble, prune
# ---------------------------------------------------------------------------


def _materialize_eager_outputs(
    *,
    graph: PipelineGraph,
    target_node_id: str,
    row_limit: int,
    source: str,
    row_values: dict[str, Any] | None,
    preamble_ns: dict[str, Any] | None,
    preview_fp: str,
    fp: str,
) -> tuple[
    dict[str, pl.DataFrame],
    list[str],
    dict[str, list[str]],
    dict[str, Any],
    set[str],
]:
    """Populate the trace cache: reuse preview outputs if available, else execute.

    Returns ``(eager_outputs, order, parents_of, node_map, source_ids)``.
    """
    # --- Try to reuse outputs from the preview cache ----------------
    # Preview uses fingerprint f"{row_limit}:{scenario}" (no target),
    # so compute that separately and check if we can skip execution.
    preview_data = _preview_cache.try_get(preview_fp)

    if preview_data is not None:
        prev_outputs = preview_data["eager_outputs"]
        # Preview uses swallow_errors=True, so some outputs may be
        # None on error.  Only reuse if target node has a real value.
        if target_node_id in prev_outputs and prev_outputs[target_node_id] is not None:
            # Graph-structure metadata still needs computing for
            # the trace-specific fields (parents_of, node_map, etc.)
            node_map, order, parents_of, _id_to_name = _prepare_graph(
                graph,
                target_node_id,
                source=source,
            )
            # Use preview DataFrames for all nodes that have them.
            # Some upstream nodes may have errored in preview
            # (swallow_errors=True) — include only the ones that
            # succeeded.  The post-hoc correlator handles missing
            # nodes gracefully.
            eager_outputs = {
                nid: prev_outputs[nid]
                for nid in order
                if prev_outputs.get(nid) is not None
            }
            if target_node_id in eager_outputs:
                source_ids = {nid for nid in order if not parents_of.get(nid)}
                logger.debug(
                    "trace_reused_preview_cache",
                    fingerprint=fp[:8],
                    preview_fingerprint=preview_fp[:8],
                    target=target_node_id,
                    reused_nodes=len(eager_outputs),
                )
                return eager_outputs, order, parents_of, node_map, source_ids

    # No usable preview cache — fall through to a cold execution.
    if row_values is not None:
        # Frontend sent row values for verification, meaning the
        # user clicked a cell in a live preview.  Re-executing
        # would produce DataFrames with potentially different row
        # ordering (Polars joins are non-deterministic), so refuse
        # rather than show wrong data.
        raise ValueError(
            "Preview data is not available. "
            "Please click the node to refresh its data, then retry the trace."
        )

    # No row_values — cold start or unit test.  Execute fresh.
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
      2. Calculated column (e.g. premium): only exists at the node that creates
         it (columns_added).  ALL ancestors of that node feed the calculation,
         so they must stay in the trace even though they don't carry the column
         in their output.  Without this, calculated-field traces collapse to a
         single node with no edges.
    """
    _tag_column_relevance(steps, column)

    # Find nodes where the column is first created
    origin_ids = {s.node_id for s in steps if column in s.schema_diff.columns_added}

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
        # Check if expression tells us what columns matter
        ref_cols: set[str] | None = None
        for s in steps:
            if s.node_id in origin_ids and s.expression:
                expr_refs = s.expression.get("referenced_columns", [])
                if expr_refs:
                    ref_cols = set(expr_refs)
                    break

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
                "column_relevant": s.column_relevant,
                "execution_ms": s.execution_ms,
                "expression": s.expression,
                "calculation": s.calculation,
                "node_detail": s.node_detail,
                "row_lineage_type": s.row_lineage_type,
            }
            for s in result.steps
        ],
        "row_id_column": result.row_id_column,
        "row_id_value": result.row_id_value,
        "total_nodes_in_pipeline": result.total_nodes_in_pipeline,
        "nodes_in_trace": result.nodes_in_trace,
        "execution_ms": result.execution_ms,
        "waterfall": result.waterfall,
    }
