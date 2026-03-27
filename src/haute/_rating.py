"""Rating and banding helpers extracted from executor.

This module contains the pure-logic functions for applying banding rules
and rating table lookups to Polars frames.  They are used by
``executor._build_node_fn`` but have no dependency on the executor
module itself, keeping the dependency graph acyclic.
"""

from __future__ import annotations

import math
from typing import Any

import polars as pl

from haute._types import _Frame

# ---------------------------------------------------------------------------
# Banding
# ---------------------------------------------------------------------------

_OP_MAP: dict[str, str] = {"<": "lt", "<=": "le", ">": "gt", ">=": "ge", "=": "eq", "==": "eq"}


def _banding_condition(col: pl.Expr, rule: dict[str, Any]) -> pl.Expr | None:
    """Build a Polars boolean expression from a continuous banding rule."""
    parts: list[pl.Expr] = []
    for suffix in ("1", "2"):
        op = str(rule.get(f"op{suffix}", "") or "").strip()
        val = rule.get(f"val{suffix}")
        if not op or val is None or val == "":
            continue
        try:
            num = float(val)
        except (ValueError, TypeError):
            continue
        method = _OP_MAP.get(op)
        if method is None:
            continue
        parts.append(getattr(col, method)(num))
    if not parts:
        return None
    result = parts[0]
    for p in parts[1:]:
        result = result & p
    return result


def _apply_banding(
    lf: _Frame,
    column: str,
    output_column: str,
    banding_type: str,
    rules: list[dict[str, Any]],
    default: Any = None,
) -> _Frame:
    """Apply banding rules to a column, producing a new output column.

    Continuous rules use operator/value pairs to define ranges::

        {"op1": ">", "val1": 0, "op2": "<=", "val2": 25, "assignment": "0-25"}

    Categorical rules map exact values to groups::

        {"value": "Semi-detached House", "assignment": "House"}
    """
    col = pl.col(column)
    default_lit = pl.lit(default) if default is not None else pl.lit(None, dtype=pl.Utf8)

    if banding_type == "categorical":
        # Build a remap dict: value → assignment
        remap: dict[str, str] = {}
        for rule in rules:
            val = rule.get("value", "")
            assignment = rule.get("assignment", "")
            if (val is not None and val != "") and (assignment is not None and assignment != ""):
                remap[str(val)] = str(assignment)
        if not remap:
            return lf
        cat_expr = col.cast(pl.Utf8).replace_strict(remap, default=default_lit).alias(output_column)
        return lf.with_columns(cat_expr)

    # Continuous: build a when/then chain
    chain: Any = None
    for rule in rules:
        cond = _banding_condition(col, rule)
        if cond is None:
            continue
        assignment = str(rule.get("assignment", ""))
        branch = pl.when(cond).then(pl.lit(assignment))
        chain = branch if chain is None else chain.when(cond).then(pl.lit(assignment))

    if chain is None:
        return lf
    final_expr = chain.otherwise(default_lit).alias(output_column)
    return lf.with_columns(final_expr)


def _normalise_banding_factors(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ``factors`` list from banding config."""
    factors = config.get("factors")
    if isinstance(factors, list):
        return factors
    return []


# ---------------------------------------------------------------------------
# Rating tables
# ---------------------------------------------------------------------------

_LOOKUP_VAL = "__haute_lookup_val__"


def _apply_rating_table(
    lf: _Frame,
    table: dict[str, Any],
) -> _Frame:
    """Apply a single rating table lookup via a Polars left join.

    *table* must contain ``factors`` (list of column names to join on),
    ``outputColumn``, ``entries`` (list of dicts with one key per factor
    plus a ``value`` key), and optionally ``defaultValue``.
    """
    factors: list[str] = table.get("factors", []) or []
    entries: list[dict[str, Any]] = table.get("entries", []) or []
    output_col: str = table.get("outputColumn", "")
    default_raw = table.get("defaultValue")

    if not factors or not entries or not output_col:
        return lf

    # Build lookup DataFrame — cast value to Float64
    lookup = pl.DataFrame(entries)
    if "value" not in lookup.columns:
        return lf
    lookup = lookup.with_columns(pl.col("value").cast(pl.Float64))

    # Reject NaN/Inf in rating table entries — they corrupt pricing silently
    _bad_count = lookup.filter(pl.col("value").is_nan() | pl.col("value").is_infinite()).height
    if _bad_count:
        raise ValueError(
            f"Rating table for '{output_col}' contains {_bad_count} NaN or Inf entries"
        )

    # B15: Select only factor columns + "value" to avoid polluting the main
    # frame with extra keys that may be present in the entries dicts.
    # Guard: if any factor column is missing from entries, config is invalid.
    missing = [f for f in factors if f not in lookup.columns]
    if missing:
        return lf
    lookup = lookup.select([*factors, "value"])

    # B14: Deduplicate on factor columns so a left join cannot fan out rows.
    lookup = lookup.unique(subset=factors, keep="last")

    # Rename "value" to an internal name to avoid collision with any
    # pre-existing "value" column in the input frame (Bug #1/#2).
    lookup = lookup.rename({"value": _LOOKUP_VAL})

    # Cast factor columns in lookup to Utf8 so the join matches string bands
    for f in factors:
        if f in lookup.columns:
            lookup = lookup.with_columns(pl.col(f).cast(pl.Utf8))

    # Cast factor columns in the main frame to Utf8 too
    existing_cols = set(
        lf.collect_schema().names() if hasattr(lf, "collect_schema") else lf.columns
    )
    # Save original dtypes so we can revert after the join
    if hasattr(lf, "collect_schema"):
        _schema = lf.collect_schema()
        original_dtypes = {f: _schema[f] for f in factors if f in existing_cols}
    else:
        _dtypes = dict(zip(lf.columns, lf.dtypes))
        original_dtypes = {f: _dtypes[f] for f in factors if f in existing_cols}

    cast_exprs = [pl.col(f).cast(pl.Utf8).alias(f) for f in factors if f in existing_cols]
    if cast_exprs:
        lf = lf.with_columns(cast_exprs)

    # Left join
    lf = lf.join(lookup.lazy(), on=factors, how="left")

    # Revert factor columns to their original dtypes
    revert_exprs = [pl.col(f).cast(dtype) for f, dtype in original_dtypes.items()]
    if revert_exprs:
        lf = lf.with_columns(revert_exprs)

    # Rename value → outputColumn, apply default
    # B13: Gracefully handle non-numeric defaultValue (e.g. "N/A", "", inf, nan)
    has_default = default_raw is not None and str(default_raw).strip()
    try:
        default_val = float(str(default_raw)) if has_default else None
    except (ValueError, TypeError):
        default_val = None
    # Reject inf/nan — they corrupt downstream arithmetic silently
    if default_val is not None and not math.isfinite(default_val):
        default_val = None
    if default_val is not None:
        lf = lf.with_columns(
            pl.col(_LOOKUP_VAL).fill_null(default_val).alias(output_col),
        )
    else:
        lf = lf.with_columns(pl.col(_LOOKUP_VAL).alias(output_col))

    # Drop the internal lookup column (always; it can never collide now)
    lf = lf.drop(_LOOKUP_VAL)

    return lf


def _combine_rating_columns(
    lf: _Frame,
    columns: list[str],
    operation: str,
    output_col: str,
) -> _Frame:
    """Combine multiple rating table output columns into a single column.

    Supported operations: multiply (default), add, min, max.
    """
    if not columns:
        return lf
    if len(columns) == 1:
        return lf.with_columns(pl.col(columns[0]).alias(output_col))

    if operation == "add":
        # fill_null(0.0) for add: missing factor contributes nothing
        # fill_nan(0.0) also catches NaN from bad lookup entries
        expr = pl.col(columns[0]).fill_null(0.0).fill_nan(0.0)
        for c in columns[1:]:
            expr = expr + pl.col(c).fill_null(0.0).fill_nan(0.0)
    elif operation == "min":
        expr = pl.min_horizontal(*[pl.col(c) for c in columns])
    elif operation == "max":
        expr = pl.max_horizontal(*[pl.col(c) for c in columns])
    else:  # multiply (default)
        # fill_null(1.0) for multiply: missing factor = no effect (neutral element)
        # fill_nan(1.0) also catches NaN from bad lookup entries
        expr = pl.col(columns[0]).fill_null(1.0).fill_nan(1.0)
        for c in columns[1:]:
            expr = expr * pl.col(c).fill_null(1.0).fill_nan(1.0)

    return lf.with_columns(expr.alias(output_col))
