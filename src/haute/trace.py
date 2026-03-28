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
        return {nid: {} for nid in order}

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
    # String coercion for dates/datetimes
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
            preamble_ns = _compile_preamble(
                graph.preamble or "",
                pipeline_dir=_pipeline_dir(graph),
            )
            result = _execute_eager_core(
                graph,
                _build_node_fn,
                target_node_id=target_node_id,
                row_limit=row_limit,
                swallow_errors=False,
                preamble_ns=preamble_ns or None,
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
    cached_rows = _correlate_rows_posthoc(
        eager_outputs,
        order,
        parents_of,
        target_node_id,
        row_index,
    )

    # ---------- Build trace steps from cached rows ----------
    steps: list[TraceStep] = []

    for nid in order:
        is_source = nid in source_ids
        node_data = node_map[nid].data
        node_name = node_data.label
        node_type = node_data.nodeType

        output_row = cached_rows[nid]

        # Skip nodes where row correlation failed — better to show
        # nothing than to show incorrect data from a wrong row.
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
    _enrich_steps(steps, node_map, eager_outputs, parents_of, column, source)

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
        # Collect all ancestors of origin nodes — they contribute to the calc
        ancestor_ids: set[str] = set()
        if origin_ids:
            queue = list(origin_ids)
            while queue:
                nid = queue.pop()
                for pid in parents_of.get(nid, []):
                    if pid not in ancestor_ids:
                        ancestor_ids.add(pid)
                        queue.append(pid)

        steps = [s for s in steps if s.column_relevant or s.node_id in ancestor_ids]

    # ---------- Output value (already in cache from batch collect) ----------
    target_row = cached_rows[target_node_id] or {}
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
    )


# ---------------------------------------------------------------------------
# Step enrichment (expression parsing + node details)
# ---------------------------------------------------------------------------

# Lazy imports — these modules may not exist yet; enrichment is non-breaking.
try:
    from haute._expression_parser import evaluate_expression, parse_expression

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


def _enrich_steps(
    steps: list[TraceStep],
    node_map: dict[str, Any],
    eager_outputs: dict[str, pl.DataFrame],
    parents_of: dict[str, list[str]],
    column: str | None,
    source: str,
) -> None:
    """Enrich trace steps in-place with expression/calculation/detail data.

    This is a best-effort pass — if the enrichment modules are unavailable
    or a per-step enrichment fails, the fields stay ``None``.
    """
    for step in steps:
        try:
            node_data = node_map[step.node_id].data
            cfg = node_data.config if isinstance(node_data.config, dict) else {}
            code = cfg.get("code", "") or ""
            node_type = step.node_type

            # --- Expression parsing ---
            if (
                _HAS_EXPRESSION_PARSER
                and column
                and (
                    column in step.schema_diff.columns_added
                    or column in step.schema_diff.columns_modified
                )
            ):
                try:
                    parsed = parse_expression(code, column)
                    if parsed is not None:
                        step.expression = dataclasses.asdict(parsed)
                except Exception:
                    logger.debug("expression_parse_failed", node_id=step.node_id)
                try:
                    evaluated = evaluate_expression(
                        code, column, {**step.input_values, **step.output_values}
                    )
                    if evaluated is not None:
                        step.calculation = dataclasses.asdict(evaluated)
                except Exception:
                    logger.debug("expression_eval_failed", node_id=step.node_id)

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
                except Exception:
                    logger.debug("node_enrichment_failed", node_id=step.node_id)

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
                except Exception:
                    logger.debug("row_lineage_detection_failed", node_id=step.node_id)
        except Exception:
            logger.debug("trace_enrichment_step_failed", node_id=step.node_id)
            continue


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
    }
