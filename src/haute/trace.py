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
"""

from __future__ import annotations

import dataclasses
import math
import re
import time
from dataclasses import dataclass
from typing import Any

import polars as pl

from haute._fingerprint_cache import FingerprintCache
from haute._logging import get_logger
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

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SchemaDiff:
    """Column-level diff between a node's input and output."""

    columns_added: list[str]
    columns_removed: list[str]
    columns_modified: list[str]
    columns_passed: list[str]


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
# Schema diff
# ---------------------------------------------------------------------------


def _compute_schema_diff(
    input_row: dict[str, Any] | None,
    output_row: dict[str, Any],
) -> SchemaDiff:
    """Compare input and output row dicts to classify columns."""
    if input_row is None:
        # Source node - everything is "added"
        return SchemaDiff(
            columns_added=list(output_row.keys()),
            columns_removed=[],
            columns_modified=[],
            columns_passed=[],
        )

    in_cols = set(input_row.keys())
    out_cols = set(output_row.keys())

    added = sorted(out_cols - in_cols)
    removed = sorted(in_cols - out_cols)

    modified = []
    passed = []
    for col in sorted(in_cols & out_cols):
        in_val = input_row[col]
        out_val = output_row[col]
        # Treat NaN == NaN as equal
        if in_val != out_val and not (_is_nan(in_val) and _is_nan(out_val)):
            modified.append(col)
        else:
            passed.append(col)

    return SchemaDiff(
        columns_added=added,
        columns_removed=removed,
        columns_modified=modified,
        columns_passed=passed,
    )


def _is_nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _is_non_finite(v: Any) -> bool:
    """Return True if *v* is a float that is NaN, +Inf, or -Inf."""
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


def _jsonify_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert Polars row values to JSON-serialisable Python types.

    NaN, +Inf, and -Inf are replaced with ``None`` because they are not
    valid JSON values and would cause frontend parsing errors.
    """
    clean: dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            clean[k] = None
        elif _is_non_finite(v):
            clean[k] = None
        elif isinstance(v, (int, float, str, bool)):
            clean[k] = v
        else:
            # date, datetime, duration, list, struct → str fallback
            clean[k] = str(v)
    return clean


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
# Post-hoc row correlation
# ---------------------------------------------------------------------------


def _build_value_mask(
    cols: list[str],
    vals: dict[str, Any],
) -> pl.Expr:
    """Build a Polars boolean mask matching *vals* on *cols*."""
    mask = pl.lit(True)
    for c in cols:
        val = vals[c]
        if val is None:
            mask = mask & pl.col(c).is_null()
        elif isinstance(val, float) and math.isnan(val):
            mask = mask & pl.col(c).is_nan()
        elif isinstance(val, str):
            # Cast column to Utf8 so stringified dates/datetimes match
            mask = mask & (pl.col(c).cast(pl.Utf8) == val)
        else:
            mask = mask & (pl.col(c) == val)
    return mask


def _find_matching_row(
    df: pl.DataFrame,
    child_row: dict[str, Any],
    fallback_index: int,
) -> tuple[dict[str, Any] | None, int]:
    """Find the row in *df* that matches *child_row* on shared columns.

    Returns ``(row_dict, positional_index)`` — the row dict is already
    run through ``_jsonify_row``.  Returns ``(None, -1)`` when no match
    can be found — callers must handle the unresolved case rather than
    silently showing incorrect data.

    Strategy:
      1. Try matching on ALL shared columns.
      2. If no match, progressively remove the column that is blocking
         the match (handles aggregated values like SUM that don't exist
         in the source).
      3. If still no match, return None (fail loudly).
    """
    df_cols = set(df.columns)
    shared = [c for c in child_row if c in df_cols]

    if shared:
        # Add a temporary positional index so we can report *which* row matched.
        indexed = df.with_row_index("__tmp_idx")
        cols_to_try = list(shared)

        while cols_to_try:
            mask = _build_value_mask(cols_to_try, child_row)
            matched = indexed.filter(mask)
            if len(matched) > 0:
                idx = int(matched[0, "__tmp_idx"])
                return _jsonify_row(df.row(idx, named=True)), idx

            # Progressively remove the column that blocks matching.
            removed = False
            for i in range(len(cols_to_try) - 1, -1, -1):
                candidate = cols_to_try[:i] + cols_to_try[i + 1 :]
                if not candidate:
                    break
                test_mask = _build_value_mask(candidate, child_row)
                if len(indexed.filter(test_mask)) > 0:
                    cols_to_try = candidate
                    removed = True
                    break
            if not removed:
                break

    # No match found — return None so the caller can mark the step
    # as unresolved rather than silently showing wrong data.
    logger.warning(
        "trace_row_match_failed",
        shared_cols_tried=len(shared) if shared else 0,
        df_rows=len(df),
    )
    return None, -1


def _correlate_rows_posthoc(
    eager_outputs: dict[str, pl.DataFrame],
    order: list[str],
    parents_of: dict[str, list[str]],
    target_node_id: str,
    row_index: int,
) -> dict[str, dict[str, Any] | None]:
    """Extract the correct row from each node using post-hoc correlation.

    Uses the preview-cached DataFrames directly — no re-execution, no
    injected columns.  Walks backward from the target node and matches
    each parent's row by shared column values with the already-resolved
    child row.

    Returns a dict mapping node_id → row values (JSON-safe), or None
    for nodes where row correlation failed.
    """
    target_df = eager_outputs[target_node_id]
    if row_index >= len(target_df):
        raise ValueError(
            f"row_index {row_index} is out of range (target node has {len(target_df)} rows)"
        )

    # Step 1: extract the target row — this is exactly what the user clicked
    target_row_raw = target_df.row(row_index, named=True)

    result: dict[str, dict[str, Any] | None] = {}
    row_indices: dict[str, int] = {}  # track positional index per node

    result[target_node_id] = _jsonify_row(target_row_raw)
    row_indices[target_node_id] = row_index

    # Step 2: build children_of (reverse of parents_of)
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for cid, pids in parents_of.items():
        for pid in pids:
            if pid in children_of:
                children_of[pid].append(cid)

    # Step 3: walk backward through topo order
    for nid in reversed(order):
        if nid in result:
            continue

        parent_df = eager_outputs.get(nid)
        if parent_df is None or len(parent_df) == 0:
            result[nid] = {}
            row_indices[nid] = 0
            continue

        # Find a child of this node that's already resolved (with actual data)
        resolved_child_id = None
        for cid in children_of.get(nid, []):
            if cid in result and result[cid] is not None and result[cid]:
                resolved_child_id = cid
                break

        if resolved_child_id is None:
            # Node not on path to target — cannot correlate
            result[nid] = None
            row_indices[nid] = -1
            continue

        child_row = result[resolved_child_id]
        child_row_idx = row_indices.get(resolved_child_id, 0)
        child_df = eager_outputs.get(resolved_child_id)
        child_len = len(child_df) if child_df is not None else 0

        # Build a filtered child_row for matching: only include columns
        # that exist in this parent's DataFrame.  This prevents columns
        # brought in by a *different* parent (via a join) from confusing
        # the value matcher.
        parent_cols = set(parent_df.columns)
        if child_row is None:
            result[nid] = None
            row_indices[nid] = -1
            continue
        match_row = {c: v for c, v in child_row.items() if c in parent_cols}

        # Fast path: same row count → likely 1:1 (with_columns, rename, select).
        # Check if the row at the same position matches on shared columns.
        if len(parent_df) == child_len and child_row_idx < len(parent_df):
            candidate = _jsonify_row(parent_df.row(child_row_idx, named=True))
            shared = [c for c in match_row if c in candidate]
            if not shared:
                # No shared columns (e.g., full rename or select) but same
                # row count → positional match is the best we can do and is
                # correct for 1:1 transforms.
                result[nid] = candidate
                row_indices[nid] = child_row_idx
                continue
            if all(_trace_values_match(candidate.get(c), match_row.get(c)) for c in shared):
                result[nid] = candidate
                row_indices[nid] = child_row_idx
                continue

        # Value matching: find the parent row that matches the child row
        row_dict, idx = _find_matching_row(parent_df, match_row, child_row_idx)
        result[nid] = row_dict  # may be None if no match found
        row_indices[nid] = idx

    return result


def _is_nan_like(v: Any) -> bool:
    """Return True for None or float NaN (treated as equal in matching)."""
    if v is None:
        return True
    return isinstance(v, float) and math.isnan(v)


def _trace_values_match(actual: Any, expected: Any) -> bool:
    """Compare a DataFrame cell value against a JSON-serialized value from the frontend.

    Handles type coercion (JSON ints ↔ Python floats, date strings, etc.)
    and floating-point tolerance.
    """
    if actual == expected:
        return True
    if actual is None and expected is None:
        return True
    if _is_nan_like(actual) and _is_nan_like(expected):
        return True
    if isinstance(actual, float) and isinstance(expected, (int, float)):
        if math.isnan(actual):
            return expected is None or (isinstance(expected, float) and math.isnan(expected))
        return math.isclose(actual, float(expected), rel_tol=1e-9)
    if isinstance(actual, int) and isinstance(expected, float):
        return math.isclose(float(actual), expected, rel_tol=1e-9)
    # String coercion for dates/datetimes only
    from datetime import date, datetime

    if isinstance(actual, (date, datetime)) or isinstance(expected, (date, datetime)):
        if str(actual) == str(expected):
            return True
    return False


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

        # --- Try to reuse outputs from the preview cache ----------------
        # Preview uses fingerprint f"{row_limit}:{scenario}" (no target),
        # so compute that separately and check if we can skip execution.
        preview_fp = graph_fingerprint(graph, f"{row_limit}:{source}")
        preview_data = _preview_cache.try_get(preview_fp)
        reused_preview = False

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
                eager_outputs = {}
                for nid in order:
                    df = prev_outputs.get(nid)
                    if df is not None:
                        eager_outputs[nid] = df
                if target_node_id in eager_outputs:
                    source_ids = {nid for nid in order if not parents_of.get(nid)}
                    reused_preview = True
                    logger.debug(
                        "trace_reused_preview_cache",
                        fingerprint=fp[:8],
                        preview_fingerprint=preview_fp[:8],
                        target=target_node_id,
                        reused_nodes=len(eager_outputs),
                    )

        if not reused_preview:
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
    #
    # Two cases:
    #   1. Pass-through column (e.g. VehGas): exists in multiple nodes' output.
    #      Keep only nodes whose output contains the column — this prunes
    #      unrelated source branches (e.g. claims/exposure when tracing VehGas
    #      which only comes from policies).
    #   2. Calculated column (e.g. premium): only exists at the node that creates
    #      it (columns_added).  ALL ancestors of that node feed the calculation,
    #      so they must stay in the trace even though they don't carry the column
    #      in their output.  Without this, calculated-field traces collapse to a
    #      single node with no edges.
    if column:
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
        steps = [s for s in steps if s.column_relevant or s.node_id in keep_ids]

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
    if column and len(steps) >= 3:
        try:
            from haute._trace_waterfall import build_waterfall

            waterfall_steps: list[dict[str, Any]] = []
            for step in steps:
                val = step.output_values.get(column)
                if val is None:
                    continue
                if column in step.schema_diff.columns_added and not waterfall_steps:
                    waterfall_steps.append(
                        {"label": step.node_name, "operation": "base", "value": val}
                    )
                elif column in step.schema_diff.columns_modified:
                    # Detect multiply vs add from the expression
                    op = "multiply"
                    if step.expression and isinstance(step.expression, dict):
                        expr_text = step.expression.get("expression_text", "")
                        if "+" in expr_text or "-" in expr_text:
                            op = "add"
                    waterfall_steps.append({"label": step.node_name, "operation": op, "value": val})
            wf_result = build_waterfall(waterfall_steps)
            if wf_result is not None:
                waterfall_data = [
                    {
                        "label": e.label,
                        "operation": e.operation,
                        "value": e.value,
                        "delta": e.delta,
                        "cumulative": e.cumulative,
                    }
                    for e in wf_result.entries
                ]
        except Exception as exc:
            # Surface the waterfall build failure as a structured payload
            # on TraceResult.waterfall so the user can see what went wrong,
            # and log at WARNING level.  Previously this swallowed the
            # failure silently and returned waterfall=None.
            logger.warning(
                "waterfall_build_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                target=target_node_id,
                column=column,
                exc_info=True,
            )
            waterfall_data = {
                "error": f"waterfall build failed: {exc}",
                "error_type": type(exc).__name__,
            }

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
# Step enrichment (expression parsing + node details)
# ---------------------------------------------------------------------------

# Lazy imports — these modules may not exist yet; enrichment is non-breaking.
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


def _fix_upstream_values(
    input_sources: dict[str, Any],
    steps: list[TraceStep],
    eager_outputs: dict[str, pl.DataFrame],
) -> None:
    """Fix upstream step output_values using known-good values from input_sources.

    When the row correlator matched the wrong row in a source node (due to
    non-deterministic join ordering or value changes through scenario
    expansion), the step's output_values shows null for columns that
    actually have values.  This function uses the known-good values from
    expression evaluation to find the correct row in the source DataFrame
    and update the step's output_values.
    """
    for col_name, src_info in input_sources.items():
        if not isinstance(src_info, dict):
            continue
        src_node_name = src_info.get("node_name")
        known_value = src_info.get("result_value")
        if src_node_name is None or known_value is None:
            continue

        # Find the step for this source node
        for s in steps:
            if s.node_name != src_node_name:
                continue
            current_val = s.output_values.get(col_name)
            if current_val is not None:
                break  # value is already correct

            # Step has null but we know the correct value — try to find
            # the right row in the source DataFrame using the known value.
            df = eager_outputs.get(s.node_id)
            if df is None or col_name not in df.columns:
                break
            try:
                # Filter to rows where this column matches the known value
                if isinstance(known_value, float):
                    matched = df.filter((pl.col(col_name) - known_value).abs() < 1e-6)
                else:
                    matched = df.filter(pl.col(col_name) == known_value)
                if len(matched) > 0:
                    new_row = _jsonify_row(matched.row(0, named=True))
                    s.output_values[col_name] = new_row.get(col_name)
            except Exception as exc:
                # Row-fixup is opportunistic — it patches upstream rows
                # that the post-hoc correlator got wrong.  If the filter
                # itself errors (type mismatch, non-comparable value),
                # log visibly so the user can see the fixup was skipped
                # rather than silently leaving the wrong row in place.
                logger.warning(
                    "fix_upstream_row_failed",
                    node_id=s.node_id,
                    column=col_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
            break

        # Recurse into nested input_sources
        nested = src_info.get("input_sources")
        if isinstance(nested, dict):
            _fix_upstream_values(nested, steps, eager_outputs)


def _wrap_node_code(raw_code: str) -> str:
    """Wrap dot-chain or bare-expression code so the parser sees valid Python."""
    if not raw_code:
        return raw_code
    if raw_code.startswith("."):
        return f"df = (df\n{raw_code})"
    stripped = raw_code.lstrip()
    if not stripped.startswith("df") and "=" not in raw_code.split("\n")[0].split("(")[0]:
        return f"df = (\n{raw_code}\n)"
    return raw_code


def _build_input_sources(
    ref_cols: list[str],
    current_step: TraceStep,
    all_steps: list[TraceStep],
    node_map: dict[str, Any],
    preamble_ns: dict[str, Any] | None,
    *,
    depth: int = 0,
    max_depth: int = 3,
    visited: set[str] | None = None,
) -> dict[str, Any]:
    """Recursively build input source derivations for referenced columns.

    For each column in *ref_cols*, finds the upstream step that created it
    and extracts its formula + values.  If that source itself has an
    expression with referenced columns, recurses to build nested sources.
    """
    if visited is None:
        visited = set()
    result: dict[str, Any] = {}
    for ref_col in ref_cols:
        if ref_col in visited:
            continue
        visited.add(ref_col)
        for other_step in all_steps:
            if other_step is current_step:
                continue
            if ref_col not in other_step.schema_diff.columns_added:
                continue
            other_combined = {**other_step.input_values, **other_step.output_values}
            source_info: dict[str, Any] = {
                "node_name": other_step.node_name,
            }

            # Parse the expression for this specific column from the
            # upstream node's code — don't rely on other_step.expression
            # since that's only populated for the traced column.
            parsed_refs: list[str] = []
            try:
                other_code = ""
                nd = node_map.get(other_step.node_id)
                if nd is not None:
                    cfg = nd.data.config if isinstance(nd.data.config, dict) else {}
                    raw = cfg.get("code", "") or ""

                    # Instance resolution: if this node is an instance
                    # and its code doesn't contain with_columns, use the
                    # original node's code instead.
                    instance_of = cfg.get("instanceOf", "")
                    if instance_of and ".with_columns(" not in raw and instance_of in node_map:
                        orig_cfg = node_map[instance_of].data.config
                        if isinstance(orig_cfg, dict):
                            raw = orig_cfg.get("code", "") or ""

                    other_code = _wrap_node_code(raw)
                if other_code:
                    parsed = parse_expression(other_code, ref_col)
                    if parsed and parsed.expression_text:
                        source_info["expression_text"] = parsed.expression_text
                        parsed_refs = list(parsed.referenced_columns)
                    ev = evaluate_expression(
                        other_code,
                        ref_col,
                        other_combined,
                        preamble_ns=preamble_ns,
                    )
                    if ev is not None:
                        source_info["substituted_text"] = ev.substituted_text
                        source_info["result_value"] = ev.result_value
            except Exception as exc:
                # Surface the derivation failure on the source entry so
                # the caller can see why an input column's value/
                # expression is missing, rather than silently falling
                # back to the raw cell value.
                logger.warning(
                    "input_source_derivation_failed",
                    node_id=other_step.node_id,
                    column=ref_col,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                source_info["error"] = f"input-source derivation failed: {exc}"
                source_info["error_type"] = type(exc).__name__
                source_info.setdefault("result_value", other_combined.get(ref_col))

            # If no expression was found (source node with no code),
            # use the value from the step's output_values directly.
            if "result_value" not in source_info:
                source_info["result_value"] = other_combined.get(ref_col)

            # Recurse into this source's dependencies
            if depth < max_depth and parsed_refs:
                sub_sources = _build_input_sources(
                    parsed_refs,
                    other_step,
                    all_steps,
                    node_map,
                    preamble_ns,
                    depth=depth + 1,
                    max_depth=max_depth,
                    visited=set(visited),
                )
                if sub_sources:
                    source_info["input_sources"] = sub_sources

            result[ref_col] = source_info
            break
    return result


def _enrich_steps(
    steps: list[TraceStep],
    node_map: dict[str, Any],
    eager_outputs: dict[str, pl.DataFrame],
    parents_of: dict[str, list[str]],
    column: str | None,
    source: str,
    preamble_ns: dict[str, Any] | None = None,
) -> None:
    """Enrich trace steps in-place with expression/calculation/detail data.

    This is a best-effort pass — if the enrichment modules are unavailable
    or a per-step enrichment fails, the fields stay ``None``.
    """
    for step in steps:
        try:
            node_data = node_map[step.node_id].data
            cfg = node_data.config if isinstance(node_data.config, dict) else {}
            raw_code = cfg.get("code", "") or ""

            # Instance resolution: if this node is an instance and its
            # code doesn't contain with_columns, use the original node's
            # code.  This ensures the step that CREATED the column gets
            # the correct expression, not just the target step.
            instance_of = cfg.get("instanceOf", "")
            if instance_of and ".with_columns(" not in raw_code and instance_of in node_map:
                orig_cfg = node_map[instance_of].data.config
                if isinstance(orig_cfg, dict):
                    orig_code = orig_cfg.get("code", "") or ""
                    if orig_code:
                        raw_code = orig_code

            # The executor wraps dot-chain syntax (e.g. ".filter(...)") as
            # "df = (df\n.filter(...))".  Apply the same wrapping so the
            # expression parser sees valid Python.
            code = _wrap_node_code(raw_code)
            node_type = step.node_type

            # --- Expression parsing ---
            # Trigger if column is added/modified at THIS step, OR if the
            # column is a pass-through at the target step (created upstream).
            # For pass-throughs, find the upstream step that created it and
            # use its expression.
            _col_in_schema = (
                (
                    column in step.schema_diff.columns_added
                    or column in step.schema_diff.columns_modified
                )
                if column
                else False
            )

            # If this is the target step and the column is just passing
            # through, look upstream for the creating step's expression
            if (
                _HAS_EXPRESSION_PARSER
                and column
                and not _col_in_schema
                and step.node_id == steps[-1].node_id  # target step
                and column in step.schema_diff.columns_passed
            ):
                for upstream in steps:
                    if upstream is step:
                        continue
                    if column in upstream.schema_diff.columns_added:
                        # Found the upstream creator — parse its code
                        u_cfg = (
                            node_map[upstream.node_id].data.config
                            if isinstance(node_map[upstream.node_id].data.config, dict)
                            else {}
                        )
                        u_raw = u_cfg.get("code", "") or ""
                        # Instance resolution
                        u_inst = u_cfg.get("instanceOf", "")
                        if u_inst and ".with_columns(" not in u_raw and u_inst in node_map:
                            u_orig = node_map[u_inst].data.config
                            if isinstance(u_orig, dict):
                                u_raw = u_orig.get("code", "") or ""
                        u_code = _wrap_node_code(u_raw)
                        if u_code:
                            try:
                                u_combined = {
                                    **upstream.input_values,
                                    **upstream.output_values,
                                }
                                parsed = parse_expression(u_code, column)
                                if parsed and parsed.expression_text:
                                    step.expression = dataclasses.asdict(parsed)
                                ev = evaluate_expression(
                                    u_code,
                                    column,
                                    u_combined,
                                    preamble_ns=preamble_ns,
                                )
                                if ev is not None:
                                    step.calculation = dataclasses.asdict(ev)
                            except Exception as exc:
                                logger.warning(
                                    "upstream_expression_failed",
                                    node_id=upstream.node_id,
                                    column=column,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                    exc_info=True,
                                )
                                err_payload: dict[str, Any] = {
                                    "error": f"upstream expression lookup failed: {exc}",
                                    "error_type": type(exc).__name__,
                                    "upstream_node_id": upstream.node_id,
                                }
                                # Surface the error on both enrichment
                                # fields so downstream consumers see it
                                # regardless of which one they inspect.
                                if step.expression is None:
                                    step.expression = dict(err_payload)
                                else:
                                    step.expression.setdefault("error", err_payload["error"])
                                if step.calculation is None:
                                    step.calculation = dict(err_payload)
                                else:
                                    step.calculation.setdefault("error", err_payload["error"])
                        break
            _col_in_code = False
            if column and raw_code and ".with_columns(" in raw_code:
                # Check if the column is a keyword arg or appears as an alias target
                _col_in_code = bool(re.search(rf"\b{re.escape(column)}\s*=", raw_code))
            if _HAS_EXPRESSION_PARSER and column and (_col_in_schema or _col_in_code):
                try:
                    parsed = parse_expression(code, column)
                    if parsed is not None:
                        step.expression = dataclasses.asdict(parsed)
                except Exception as exc:
                    logger.warning(
                        "expression_parse_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    # Surface the parse failure on the enrichment field
                    # so downstream consumers see it instead of an
                    # unexplained missing expression.
                    step.expression = {
                        "error": f"parse_expression failed: {exc}",
                        "error_type": type(exc).__name__,
                        "target_column": column,
                    }
                try:
                    evaluated = evaluate_expression(
                        code,
                        column,
                        {**step.input_values, **step.output_values},
                        preamble_ns=preamble_ns,
                    )
                    if evaluated is not None:
                        calc_dict = dataclasses.asdict(evaluated)
                        # Add taken_branch info to calculation dict
                        if evaluated.taken_branch is not None:
                            calc_dict["taken_branch"] = evaluated.taken_branch
                        if evaluated.taken_branch_index is not None:
                            calc_dict["taken_branch_index"] = evaluated.taken_branch_index
                        # For window functions, use the actual output value
                        if evaluated.expression_type == "window" and column in step.output_values:
                            calc_dict["result_value"] = step.output_values[column]
                        step.calculation = calc_dict
                except Exception as exc:
                    logger.warning(
                        "expression_eval_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    # Seed calculation with a visible error marker that
                    # persists even if later enrichment stages (chain,
                    # input_sources) add more fields to the dict.
                    step.calculation = {
                        "error": f"evaluate_expression failed: {exc}",
                        "error_type": type(exc).__name__,
                        "target_column": column,
                    }

                # --- Expression chain (intra-node dependencies) ---
                try:
                    chain = parse_expression_chain(raw_code, column)
                    if chain and len(chain) > 1:
                        if step.calculation is None:
                            step.calculation = {}
                        combined_values = {**step.input_values, **step.output_values}
                        enriched_chain: list[dict[str, Any]] = []
                        for p in chain:
                            entry = dataclasses.asdict(p)
                            # Enrich with substituted values and result
                            try:
                                ev = evaluate_expression(
                                    raw_code,
                                    p.target_column,
                                    combined_values,
                                    preamble_ns=preamble_ns,
                                )
                                if ev is not None:
                                    entry["substituted_text"] = ev.substituted_text
                                    entry["result_value"] = ev.result_value
                            except Exception as inner_exc:
                                logger.warning(
                                    "chain_entry_eval_failed",
                                    node_id=step.node_id,
                                    column=p.target_column,
                                    error=str(inner_exc),
                                    error_type=type(inner_exc).__name__,
                                    exc_info=True,
                                )
                                entry["error"] = (
                                    f"chain entry evaluation failed: {inner_exc}"
                                )
                                entry["error_type"] = type(inner_exc).__name__
                                entry.setdefault("substituted_text", p.expression_text)
                                fallback = combined_values.get(p.target_column)
                                entry.setdefault("result_value", fallback)
                            enriched_chain.append(entry)
                        step.calculation["expression_chain"] = enriched_chain
                except Exception as exc:
                    logger.warning(
                        "expression_chain_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    # Surface a visible failure on the chain sub-field so
                    # the user can see why intra-node dependencies were
                    # not analysed, rather than it looking like "no chain
                    # detected".  Also seed an outer ``error`` key on the
                    # calculation dict (without overwriting a more
                    # specific evaluate-expression error) so generic
                    # consumers that inspect only the outer dict still
                    # see the failure.
                    if step.calculation is None:
                        step.calculation = {}
                    chain_error_msg = f"parse_expression_chain failed: {exc}"
                    chain_error_type = type(exc).__name__
                    step.calculation["expression_chain"] = {
                        "error": chain_error_msg,
                        "error_type": chain_error_type,
                    }
                    step.calculation.setdefault("error", chain_error_msg)
                    step.calculation.setdefault("error_type", chain_error_type)

                # --- Input sources (recursive upstream derivations) ---
                try:
                    # Collect ALL referenced columns: from the target
                    # expression AND from every chain entry. This ensures
                    # upstream derivations are found for intra-node deps
                    # too (e.g., margin = premium - burn_cost in the same
                    # node — we still need to trace premium and burn_cost
                    # to their upstream origins).
                    all_ref_cols: list[str] = []
                    if step.expression and step.expression.get("referenced_columns"):
                        all_ref_cols.extend(step.expression["referenced_columns"])
                    if step.calculation and step.calculation.get("expression_chain"):
                        chain_val = step.calculation["expression_chain"]
                        if isinstance(chain_val, list):
                            for chain_entry in chain_val:
                                if not isinstance(chain_entry, dict):
                                    continue
                                for rc in chain_entry.get("referenced_columns", []):
                                    if rc not in all_ref_cols:
                                        all_ref_cols.append(rc)
                    if all_ref_cols:
                        input_sources = _build_input_sources(
                            all_ref_cols,
                            step,
                            steps,
                            node_map,
                            preamble_ns,
                            depth=0,
                            max_depth=3,
                        )
                        if input_sources:
                            if step.calculation is None:
                                step.calculation = {}
                            step.calculation["input_sources"] = input_sources

                            # Fix upstream steps that have wrong row data.
                            # When input_sources found the correct value
                            # for a column via expression evaluation, but
                            # the upstream step's output_values shows null
                            # (from a row correlation failure), re-correlate
                            # using the known-good value.
                            _fix_upstream_values(
                                input_sources,
                                steps,
                                eager_outputs,
                            )
                except Exception as exc:
                    logger.warning(
                        "input_sources_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    if step.calculation is None:
                        step.calculation = {}
                    step.calculation.setdefault(
                        "error", f"input_sources build failed: {exc}"
                    )
                    step.calculation.setdefault(
                        "error_type", type(exc).__name__
                    )

            # --- Rename detection ---
            if _HAS_EXPRESSION_PARSER and column:
                try:
                    _detect_rename(step, code, raw_code, column, steps, node_map)
                except Exception as exc:
                    logger.warning(
                        "rename_detection_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    if step.calculation is None:
                        step.calculation = {}
                    step.calculation.setdefault(
                        "rename_detection_error",
                        f"rename detection failed: {exc}",
                    )

            # --- Node-type enrichment ---
            if _HAS_TRACE_ENRICHMENT:
                # cfg already extracted above from node_data.config
                try:
                    detail: dict[str, Any] | None = None
                    if node_type == "ratingStep":
                        detail = enrich_rating_step(cfg, step.input_values, step.output_values)
                    elif node_type == "banding":
                        detail = enrich_banding(cfg, step.input_values, step.output_values)
                    elif node_type == "modelScore":
                        detail = enrich_model_score(cfg, step.input_values, step.output_values)
                    elif node_type == "scenarioExpander":
                        detail = enrich_scenario_expansion(
                            cfg,
                            step.input_values,
                            step.output_values,
                        )
                    elif node_type == "liveSwitch":
                        detail = enrich_live_switch(cfg, source)
                    if detail is not None:
                        step.node_detail = detail
                except Exception as exc:
                    logger.warning(
                        "node_enrichment_failed",
                        node_id=step.node_id,
                        node_type=str(node_type),
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    step.node_detail = {
                        "error": f"node enrichment failed: {exc}",
                        "error_type": type(exc).__name__,
                        "node_type": str(node_type),
                    }

                # --- Row lineage type ---
                try:
                    parent_ids = parents_of.get(step.node_id, [])
                    parent_row_count = 0
                    for pid in parent_ids:
                        df = eager_outputs.get(pid)
                        if df is not None:
                            parent_row_count = max(parent_row_count, len(df))
                    child_df = eager_outputs.get(step.node_id)
                    child_row_count = len(child_df) if child_df is not None else 0

                    # Sniff operation type from code string
                    operation_type = ""
                    if code:
                        code_lower = code.lower()
                        if ".group_by(" in code_lower or ".groupby(" in code_lower:
                            operation_type = "group_by"
                        elif ".cross_join(" in code_lower:
                            operation_type = "cross_join"
                        elif ".join(" in code_lower:
                            operation_type = "join"
                        elif ".filter(" in code_lower:
                            operation_type = "filter"
                        elif ".sort(" in code_lower or ".sort_by(" in code_lower:
                            operation_type = "sort"
                        elif ".explode(" in code_lower:
                            operation_type = "explode"

                    step.row_lineage_type = detect_row_lineage_type(
                        input_row_count=parent_row_count,
                        output_row_count=child_row_count,
                        node_type=node_type,
                        operation_type=operation_type,
                    )
                except Exception as exc:
                    logger.warning(
                        "row_lineage_detection_failed",
                        node_id=step.node_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    # row_lineage_type is a plain string; encode the
                    # error visibly so UI consumers see "error: ..."
                    # rather than a silent None.
                    step.row_lineage_type = (
                        f"error: row lineage detection failed: {exc}"
                    )
        except Exception as exc:
            # Outer catch-all for any enrichment step.  Surface the
            # failure on the step so downstream consumers can see it,
            # then continue with the next step rather than aborting the
            # whole trace.  Raising here would poison every trace if a
            # single step hits an unforeseen bug — instead we emit a
            # WARNING log and annotate the step with an error marker.
            logger.warning(
                "trace_enrichment_step_failed",
                node_id=step.node_id,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            if step.node_detail is None:
                step.node_detail = {
                    "error": f"trace enrichment step failed: {exc}",
                    "error_type": type(exc).__name__,
                }
            else:
                step.node_detail.setdefault(
                    "error", f"trace enrichment step failed: {exc}"
                )
                step.node_detail.setdefault("error_type", type(exc).__name__)
            continue


def _detect_rename(
    step: TraceStep,
    code: str,
    raw_code: str,
    column: str,
    all_steps: list[TraceStep],
    node_map: dict[str, Any] | None = None,
) -> None:
    """Detect if the column is a rename and populate calculation/node_detail."""

    # Case 1: .rename({'old': 'new'}) syntax
    rename_match = re.search(r"\.rename\s*\(\s*\{", raw_code)
    if rename_match:
        # Parse rename mapping from the raw code
        pairs = re.findall(r"['\"](\w+)['\"]\s*:\s*['\"](\w+)['\"]", raw_code)
        for old_name, new_name in pairs:
            if new_name == column:
                if step.calculation is None:
                    step.calculation = {}
                step.calculation["original_name"] = old_name

                # Build rename chain by looking at previous steps
                chain = _build_rename_chain(all_steps, step, old_name, column, node_map)
                if chain and len(chain) > 2:
                    step.calculation["rename_chain"] = chain

                if step.node_detail is None:
                    step.node_detail = {}
                step.node_detail["detail_type"] = "rename"
                step.node_detail["original_name"] = old_name
                step.node_detail["new_name"] = new_name
                return

    # Case 2: .with_columns(new_name=pl.col('old_name')) — pure rename (col reference only)
    if ".with_columns(" in raw_code:
        col_match = re.search(
            rf"{re.escape(column)}\s*=\s*pl\.col\(\s*['\"](\w+)['\"]\s*\)",
            raw_code,
        )
        if col_match:
            old_name = col_match.group(1)
            # Check if this is a pure rename (no additional operations)
            # The expression text should be just the column name
            expr = step.expression
            if expr and expr.get("expression_type") == "arithmetic":
                expr_text = expr.get("expression_text", "")
                if expr_text.strip() == old_name:
                    if step.calculation is None:
                        step.calculation = {}
                    step.calculation["original_name"] = old_name

                    # Build rename chain
                    chain = _build_rename_chain(all_steps, step, old_name, column, node_map)
                    if chain and len(chain) > 2:
                        step.calculation["rename_chain"] = chain


def _build_rename_chain(
    all_steps: list[TraceStep],
    current_step: TraceStep,
    old_name: str,
    new_name: str,
    node_map: dict[str, Any] | None = None,
) -> list[str]:
    """Build a chain of renames by looking backward through steps."""
    # Start with the current rename: old_name -> new_name
    chain = [old_name, new_name]

    step_idx = None
    for i, s in enumerate(all_steps):
        if s.node_id == current_step.node_id:
            step_idx = i
            break

    if step_idx is None:
        return chain

    current_name = old_name
    for i in range(step_idx - 1, -1, -1):
        prev_step = all_steps[i]
        # Check if current_name was added by this step (indicating a possible rename)
        if current_name not in prev_step.schema_diff.columns_added:
            continue

        # Try to detect what column was the source by parsing the step's code
        if _HAS_EXPRESSION_PARSER and node_map:
            try:
                nd = node_map.get(prev_step.node_id)
                if nd:
                    cfg = nd.data.config if isinstance(nd.data.config, dict) else {}
                    raw_code = cfg.get("code", "") or ""
                    if raw_code:
                        wrapped = _wrap_node_code(raw_code)
                        parsed = parse_expression(wrapped, current_name)
                        if parsed and parsed.expression_type == "arithmetic":
                            refs = parsed.referenced_columns
                            if len(refs) == 1 and refs[0] != current_name:
                                if parsed.expression_text.strip() == refs[0]:
                                    chain.insert(0, refs[0])
                                    current_name = refs[0]
                                    continue
            except Exception as exc:
                # Walking the rename chain is best-effort — if parsing a
                # prior step's code blows up we stop walking and return
                # what we have so far.  Log loudly rather than silently
                # truncate the chain.
                logger.warning(
                    "rename_chain_walk_failed",
                    node_id=prev_step.node_id,
                    column=current_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                break

    # Remove duplicates while preserving order
    seen = set()
    unique_chain = []
    for c in chain:
        if c not in seen:
            seen.add(c)
            unique_chain.append(c)

    return unique_chain


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
