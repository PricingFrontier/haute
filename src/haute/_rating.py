"""Rating and banding helpers extracted from executor.

This module contains the pure-logic functions for applying banding rules
and rating table lookups to Polars frames.  They are used by
``executor._build_node_fn`` but have no dependency on the executor
module itself, keeping the dependency graph acyclic.
"""

from __future__ import annotations

import math
from os import PathLike
from pathlib import Path
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
            raise ValueError(f"Banding rule has non-numeric value '{val}' for op{suffix}")
        if not math.isfinite(num):
            raise ValueError(f"Banding rule has non-finite value '{val}' for op{suffix}")
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
    right_closed: bool = True,
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

    # Breakpoints mode: convert to continuous rules first
    if banding_type == "breakpoints":
        rules = _breakpoints_to_rules(rules, right_closed=right_closed)

    # For continuous banding, sanitize NaN/Inf in float columns so they
    # don't match arbitrary rules — they fall cleanly to the default.
    if hasattr(lf, "collect_schema"):
        schema = lf.collect_schema()
    else:
        schema = dict(zip(lf.columns, lf.dtypes))  # type: ignore[assignment]
    col_dtype = schema.get(column)
    if col_dtype in (pl.Float32, pl.Float64):
        lf = lf.with_columns(
            pl.when(col.is_nan() | col.is_infinite())
            .then(pl.lit(None))
            .otherwise(col)
            .alias(column)
        )
        col = pl.col(column)

    # Continuous: build a when/then chain
    chain: Any = None
    for rule in rules:
        cond = _banding_condition(col, rule)
        if cond is None:
            continue
        assignment = str(rule.get("assignment", ""))
        if not assignment:
            continue
        branch = pl.when(cond).then(pl.lit(assignment))
        chain = branch if chain is None else chain.when(cond).then(pl.lit(assignment))

    if chain is None:
        return lf
    final_expr = chain.otherwise(default_lit).alias(output_column)
    return lf.with_columns(final_expr)


def _breakpoints_to_rules(
    breakpoints: list[dict[str, Any]],
    right_closed: bool = True,
) -> list[dict[str, Any]]:
    """Convert breakpoint-format rules to continuous banding rules.

    Each breakpoint has a ``boundary`` (numeric string) and a ``label``.
    The last breakpoint may have an empty boundary to create an open-ended rule.

    When *right_closed* is True, intervals are ``(lower, upper]`` — the first
    rule uses ``<=`` for its upper bound and subsequent rules use ``>`` / ``<=``.
    When False, intervals are ``[lower, upper)`` using ``>=`` / ``<``.
    """
    if not breakpoints:
        return []

    # Separate breakpoints with boundaries from the open-ended tail
    bounded: list[dict[str, Any]] = []
    open_ended: dict[str, Any] | None = None
    for bp in breakpoints:
        boundary = str(bp.get("boundary", "") or "").strip()
        label = str(bp.get("label", "") or "")
        if not boundary:
            open_ended = bp
        else:
            try:
                num = float(boundary)
            except (ValueError, TypeError):
                raise ValueError(f"Breakpoint has non-numeric boundary '{boundary}'")
            if not math.isfinite(num):
                raise ValueError(f"Breakpoint has non-finite boundary '{boundary}'")
            bounded.append({"boundary": num, "label": label})

    # Sort by boundary value
    bounded.sort(key=lambda b: b["boundary"])

    # Reject duplicate boundaries — they produce empty intervals
    seen_boundaries: set[float] = set()
    for entry in bounded:
        if entry["boundary"] in seen_boundaries:
            raise ValueError(f"Duplicate breakpoint boundary '{entry['boundary']}'")
        seen_boundaries.add(entry["boundary"])

    rules: list[dict[str, Any]] = []
    prev_boundary: float | None = None

    for entry in bounded:
        b = entry["boundary"]
        label = entry["label"]
        rule: dict[str, Any] = {"assignment": label}

        if prev_boundary is None:
            # First rule: everything up to b
            if right_closed:
                rule["op1"] = "<="
                rule["val1"] = b
            else:
                rule["op1"] = "<"
                rule["val1"] = b
        else:
            # Middle rule: from prev_boundary to b
            if right_closed:
                rule["op1"] = ">"
                rule["val1"] = prev_boundary
                rule["op2"] = "<="
                rule["val2"] = b
            else:
                rule["op1"] = ">="
                rule["val1"] = prev_boundary
                rule["op2"] = "<"
                rule["val2"] = b

        rules.append(rule)
        prev_boundary = b

    # Open-ended tail
    if open_ended is not None and prev_boundary is not None:
        label = str(open_ended.get("label", "") or "")
        if right_closed:
            rules.append({"op1": ">", "val1": prev_boundary, "assignment": label})
        else:
            rules.append({"op1": ">=", "val1": prev_boundary, "assignment": label})

    return rules


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
_SUPPORTED_COMBINE_OPERATIONS = frozenset({"multiply", "add", "min", "max"})


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
    # input "value" column in the input frame (Bug #1/#2).
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


def _normalise_combined_outputs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return validated combined-output definitions for a rating step.

    Legacy configs with ``combinedColumn``/``operation`` are converted to a
    single output definition. New ``combinedOutputs`` entries are validated
    strictly so misspelled operations or non-finite base values fail loudly.
    """
    raw_outputs = config.get("combinedOutputs")
    combined_raw = config.get("combinedColumn")
    combined = str(combined_raw).strip() if combined_raw is not None else ""
    legacy_output = (
        {
            "outputColumn": str(combined),
            "operation": str(config.get("operation", "multiply") or "multiply"),
            "baseValue": None,
            "_legacy": True,
        }
        if combined
        else None
    )
    if raw_outputs is None or raw_outputs == []:
        return [legacy_output] if legacy_output else []
    if not isinstance(raw_outputs, list):
        raise ValueError("ratingStep combinedOutputs must be a list")

    outputs: list[dict[str, Any]] = []
    seen_output_cols: set[str] = {
        str(t.get("outputColumn", "") or "").strip()
        for t in config.get("tables", []) or []
        if str(t.get("outputColumn", "") or "").strip()
    }
    raw_output_cols = {
        str(item.get("outputColumn", "") or "").strip()
        for item in raw_outputs
        if isinstance(item, dict)
    }
    if legacy_output and combined not in raw_output_cols:
        outputs.append(legacy_output)
        seen_output_cols.add(combined)
    for idx, item in enumerate(raw_outputs):
        if not isinstance(item, dict):
            raise ValueError(f"ratingStep combinedOutputs[{idx}] must be an object")
        output_col = str(item.get("outputColumn", "") or "").strip()
        if not output_col:
            raise ValueError(f"ratingStep combinedOutputs[{idx}] requires outputColumn")
        if output_col in seen_output_cols:
            raise ValueError(
                f"ratingStep combinedOutputs[{idx}].outputColumn {output_col!r} "
                "duplicates another rating output column"
            )
        seen_output_cols.add(output_col)
        operation = str(item.get("operation", "multiply") or "multiply")
        if operation not in _SUPPORTED_COMBINE_OPERATIONS:
            raise ValueError(
                f"Unsupported rating combine operation {operation!r}; "
                f"expected one of {sorted(_SUPPORTED_COMBINE_OPERATIONS)!r}"
            )

        base_raw = item.get("baseValue")
        if (
            base_raw is None
            or isinstance(base_raw, bool)
            or (isinstance(base_raw, str) and not base_raw.strip())
        ):
            raise ValueError(f"ratingStep combinedOutputs[{idx}] requires baseValue")
        try:
            base_value = float(base_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"ratingStep combinedOutputs[{idx}].baseValue must be numeric"
            ) from exc
        if not math.isfinite(base_value):
            raise ValueError(f"ratingStep combinedOutputs[{idx}].baseValue must be finite")
        outputs.append(
            {
                "outputColumn": output_col,
                "operation": operation,
                "baseValue": base_value,
            }
        )
    return outputs


def _combine_rating_output(
    lf: _Frame,
    columns: list[str],
    operation: str,
    output_col: str,
    base_value: float | None = None,
) -> _Frame:
    """Combine table outputs, optionally including a numeric base value."""
    if base_value is None:
        return _combine_rating_columns(lf, columns, operation, output_col)
    base_col = f"__haute_rating_base_{output_col}__"
    existing_cols = set(
        lf.collect_schema().names() if hasattr(lf, "collect_schema") else lf.columns
    )
    while base_col in columns or base_col in existing_cols:
        base_col = f"_{base_col}"
    with_base = lf.with_columns(pl.lit(base_value, dtype=pl.Float64).alias(base_col))
    combined = _combine_rating_columns(with_base, [base_col, *columns], operation, output_col)
    return combined.drop(base_col)


def _apply_rating_step_outputs(
    lf: _Frame,
    tables: list[dict[str, Any]],
    combined_outputs: list[dict[str, Any]],
) -> _Frame:
    """Apply rating tables and combined outputs to a frame."""
    if isinstance(lf, pl.DataFrame):
        lf = lf.lazy()

    out_cols: list[str] = []
    for table in tables:
        lf = _apply_rating_table(lf, table)
        output_col = str(table.get("outputColumn", "") or "").strip()
        if output_col:
            out_cols.append(output_col)

    for combined in combined_outputs:
        if combined.get("_legacy") and len(out_cols) < 2:
            continue
        lf = _combine_rating_output(
            lf,
            out_cols,
            combined["operation"],
            combined["outputColumn"],
            combined["baseValue"],
        )
    return lf


def apply_rating_step_from_config(
    lf: _Frame,
    config: dict[str, Any] | str | PathLike[str],
    *,
    base_dir: str | Path | None = None,
) -> _Frame:
    """Apply a rating-step JSON/dict config before custom post-processing code."""
    if isinstance(config, dict):
        resolved_config = config
    else:
        from haute._config_io import load_node_config

        config_path = config if isinstance(config, str) else Path(config)
        resolved_config = load_node_config(
            config_path, base_dir=Path(base_dir) if base_dir else None
        )

    return _apply_rating_step_outputs(
        lf,
        resolved_config.get("tables", []) or [],
        _normalise_combined_outputs(resolved_config),
    )
