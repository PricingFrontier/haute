"""Row correlation and schema-diff primitives for the trace layer.

Post-hoc correlation: given the materialized per-node DataFrames from a
preview execution, walk backward from the target node and match each
parent's row by shared column values with the already-resolved child
row.  This guarantees the trace always shows exactly the data the user
sees in the preview table — no re-execution, no injected columns.

Schema diff: column-level classification (added / removed / modified /
passed) between a node's input and output row.

Value coercion: JSON-safe row dicts, NaN-aware equality, and tolerant
string/float comparisons for the non-Polars edges of the trace surface.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import polars as pl

from haute._edge_join import build_edge_join_kwargs
from haute._json_safe import (
    MAX_SAFE_INTEGER,
    non_finite_float_token,
    to_json_safe,
)
from haute._logging import get_logger
from haute._types import GraphNode, NodeType

logger = get_logger(component="trace_correlation")


@dataclass
class SchemaDiff:
    """Column-level diff between a node's input and output."""

    columns_added: list[str]
    columns_removed: list[str]
    columns_modified: list[str]
    columns_passed: list[str]


# ---------------------------------------------------------------------------
# Value predicates / coercion
# ---------------------------------------------------------------------------


def _is_nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _float_non_finite_token(value: float) -> str | None:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return None


def _value_non_finite_token(value: Any) -> str | None:
    if isinstance(value, float):
        return _float_non_finite_token(value)
    return non_finite_float_token(value)


def _jsonify_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert Polars row values to JSON-serialisable Python types.

    Primitive scalars use the shared preview JSON boundary helper; older
    trace behavior for non-primitive values remains stringification.
    """
    clean: dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            clean[k] = None
        elif isinstance(v, (bool, int, float, str)):
            clean[k] = to_json_safe(v)
        else:
            clean[k] = str(v)
    return clean


def _trace_values_match(actual: Any, expected: Any) -> bool:
    """Compare a DataFrame cell value against a JSON-serialized value from the frontend.

    Handles type coercion (JSON ints ↔ Python floats, date strings, etc.)
    and floating-point tolerance.
    """
    if actual == expected:
        return True
    if actual is None and expected is None:
        return True
    actual_non_finite = _value_non_finite_token(actual)
    expected_non_finite = _value_non_finite_token(expected)
    if actual_non_finite is not None or expected_non_finite is not None:
        return actual_non_finite == expected_non_finite
    if isinstance(actual, float) and isinstance(expected, (int, float)):
        if math.isnan(actual):
            return isinstance(expected, float) and math.isnan(expected)
        return math.isclose(actual, float(expected), rel_tol=1e-9)
    if isinstance(actual, int) and isinstance(expected, float):
        return math.isclose(float(actual), expected, rel_tol=1e-9)
    if isinstance(actual, int) and isinstance(expected, str):
        return abs(actual) > MAX_SAFE_INTEGER and expected == str(actual)
    # String coercion for dates/datetimes only
    from datetime import date, datetime

    if isinstance(actual, (date, datetime)) or isinstance(expected, (date, datetime)):
        if str(actual) == str(expected):
            return True
    return False


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
        non_finite = non_finite_float_token(val)
        if non_finite == "nan":
            mask = mask & pl.col(c).is_nan()
        elif non_finite == "inf":
            mask = mask & (pl.col(c).is_infinite() & (pl.col(c) > 0))
        elif non_finite == "-inf":
            mask = mask & (pl.col(c).is_infinite() & (pl.col(c) < 0))
        elif val is None:
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


def _edge_join_key_pairs(join_kwargs: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``(left_key, right_key)`` column pairs from validated join kwargs.

    ``on=[k]`` pairs ``k`` with itself; ``left_on``/``right_on`` zip
    positionally (validated to equal lengths by ``build_edge_join_kwargs``).
    Cross joins have no keys and return an empty list.
    """
    on = join_kwargs.get("on")
    if on is not None:
        keys = on if isinstance(on, list) else [on]
        return [(key, key) for key in keys]
    left_on = join_kwargs.get("left_on")
    right_on = join_kwargs.get("right_on")
    if left_on is None or right_on is None:
        return []
    left_keys = left_on if isinstance(left_on, list) else [left_on]
    right_keys = right_on if isinstance(right_on, list) else [right_on]
    return list(zip(left_keys, right_keys, strict=True))


def _edge_join_right_match_row(
    child_row: dict[str, Any],
    right_cols: set[str],
    left_cols: set[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build the value-match row for an edge-join's JOIN-role (right) parent.

    Polars keeps the BASE (left) frame's copy of every colliding column
    under its original name and emits the right frame's copy as
    ``<col><suffix>`` — in every join strategy.  Projecting the child row
    onto the right parent by *name* therefore discards the right parent's
    actual values (the suffixed copies) and matches the right frame
    against LEFT-row values instead, correlating to whichever wrong right
    row those values happen to hit.

    Provenance rules, derived from the exact kwargs the runtime applied
    (``build_edge_join_kwargs`` — the same single source of truth
    ``execute_edge_join`` uses):

    1. ``<col><suffix>`` where ``<col>`` exists in BOTH parents is the
       right frame's copy of a colliding column → match the parent's
       ``<col>`` against it.
    2. An unsuffixed child column that exists ONLY in the right parent is
       right-provenance → match it under its own name.  If it exists in
       both parents the child carries the left row's value, which must
       not be matched against the right frame.
    3. Join keys: for every ``(left_key, right_key)`` pair the child's
       left-key value equals the matched right row's right-key value on
       every row where the right side participated (coalesced ``on``
       keys, ``left_on``/``right_on`` with differing names, semi/anti
       joins whose output carries no right columns at all).  Map it onto
       the parent's right-key column unless rule 1/2 already supplied it.

    Rows where the right side did NOT participate (left-join misses,
    full-join left-only rows) produce values matching no right row, so
    correlation fails loudly (step omitted) instead of inventing lineage.
    """
    join_kwargs = build_edge_join_kwargs(config)
    suffix: str = join_kwargs["suffix"]
    match_row: dict[str, Any] = {}
    for name, value in child_row.items():
        if name.endswith(suffix) and len(name) > len(suffix):
            original = name[: -len(suffix)]
            if original in right_cols and original in left_cols:
                match_row[original] = value
                continue
        if name in right_cols and name not in left_cols:
            match_row[name] = value
    for left_key, right_key in _edge_join_key_pairs(join_kwargs):
        if right_key in match_row or right_key not in right_cols:
            continue
        if left_key in child_row:
            match_row[right_key] = child_row[left_key]
    return match_row


def _build_parent_match_row(
    child_row: dict[str, Any],
    parent_id: str,
    parent_cols: set[str],
    child_node: GraphNode | None,
    eager_outputs: dict[str, pl.DataFrame],
) -> dict[str, Any]:
    """Project *child_row* onto *parent_id*'s columns for value matching.

    Generic nodes keep the child columns that exist in the parent —
    name-faithful provenance.  Edge-join children break that assumption
    for the JOIN-role parent, where colliding columns were suffixed and
    the unsuffixed names carry the other parent's values; those are
    routed through :func:`_edge_join_right_match_row`.  The BASE-role
    parent's columns survive a join under their original names with the
    base row's values, so the generic projection remains correct there.
    """
    if child_node is not None and child_node.data.nodeType == NodeType.EDGE_JOIN:
        config = child_node.data.config
        base_id = config.get("baseInput")
        join_id = config.get("joinInput")
        if parent_id == join_id:
            base_df = eager_outputs.get(base_id) if isinstance(base_id, str) else None
            if base_df is None:
                raise ValueError(
                    f"edge-join node '{child_node.id}' has no materialized output for "
                    f"its base parent '{base_id}' — cannot correlate the join parent"
                )
            return _edge_join_right_match_row(
                child_row,
                parent_cols,
                set(base_df.columns),
                config,
            )
        if parent_id != base_id:
            raise ValueError(
                f"node '{parent_id}' is wired as a parent of edge-join "
                f"'{child_node.id}' but matches neither baseInput ({base_id!r}) "
                f"nor joinInput ({join_id!r})"
            )
    return {c: v for c, v in child_row.items() if c in parent_cols}


def _correlate_rows_posthoc(
    eager_outputs: dict[str, pl.DataFrame],
    order: list[str],
    parents_of: dict[str, list[str]],
    target_node_id: str,
    row_index: int,
    *,
    node_map: Mapping[str, GraphNode],
) -> dict[str, dict[str, Any] | None]:
    """Extract the correct row from each node using post-hoc correlation.

    Uses the preview-cached DataFrames directly — no re-execution, no
    injected columns.  Walks backward from the target node and matches
    each parent's row by shared column values with the already-resolved
    child row.  *node_map* supplies node type and config so that
    edge-join children can route suffixed/colliding columns to the
    correct parent (see :func:`_build_parent_match_row`).

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
        # that exist in this parent's DataFrame, and — when the child is
        # an edge-join — route suffixed/colliding columns to the parent
        # they actually came from.  This prevents columns brought in by
        # a *different* parent (via a join) from confusing the value
        # matcher.
        parent_cols = set(parent_df.columns)
        if child_row is None:
            result[nid] = None
            row_indices[nid] = -1
            continue
        match_row = _build_parent_match_row(
            child_row,
            nid,
            parent_cols,
            node_map.get(resolved_child_id),
            eager_outputs,
        )

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
