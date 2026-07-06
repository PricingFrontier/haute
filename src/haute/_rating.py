"""Rating and banding helpers extracted from executor.

This module contains the pure-logic functions for applying banding rules
and rating table lookups to Polars frames.  They are used by
``executor._build_node_fn`` but have no dependency on the executor
module itself, keeping the dependency graph acyclic.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from os import PathLike
from pathlib import Path
from typing import Any

import polars as pl

from haute._banding_config import (
    normalise_banding_factors,
    normalise_banding_rules,
)
from haute._logging import get_logger
from haute._rating_step_config import normalise_rating_tables
from haute._types import _Frame

logger = get_logger(component="rating")

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
    rules: list[dict[str, Any]] | dict[str, Any],
    default: Any = None,
    right_closed: bool = True,
) -> _Frame:
    """Apply banding rules to a column, producing a new output column.

    Continuous rules use operator/value pairs to define ranges::

        {"op1": ">", "val1": 0, "op2": "<=", "val2": 25, "assignment": "0-25"}

    Categorical rules map exact values to groups::

        {"value": "Semi-detached House", "assignment": "House"}
    """
    rules = normalise_banding_rules(banding_type, rules)
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
        # Build the NaN/Inf-safe expression LOCALLY and feed it into the
        # rule chain below.  Aliasing it back onto the source ``column``
        # would overwrite NaN/Inf for every downstream node, not just this
        # banding output — only ``output_column`` may be added/changed.
        col = pl.when(col.is_nan() | col.is_infinite()).then(pl.lit(None)).otherwise(col)

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
    breakpoints: list[dict[str, Any]] | dict[str, Any],
    right_closed: bool = True,
) -> list[dict[str, Any]]:
    """Convert breakpoint-format rules to continuous banding rules.

    Each breakpoint has a ``boundary`` (numeric string) and a ``label``.
    The last breakpoint may have an empty boundary to create an open-ended rule.

    When *right_closed* is True, intervals are ``(lower, upper]`` — the first
    rule uses ``<=`` for its upper bound and subsequent rules use ``>`` / ``<=``.
    When False, intervals are ``[lower, upper)`` using ``>=`` / ``<``.
    """
    breakpoints = normalise_banding_rules("breakpoints", breakpoints)
    if not breakpoints:
        return []

    # Separate breakpoints with boundaries from the open-ended tail
    bounded: list[dict[str, Any]] = []
    open_ended: dict[str, Any] | None = None
    open_ended_count = 0
    for bp in breakpoints:
        boundary = str(bp.get("boundary", "") or "").strip()
        label = str(bp.get("label", "") or "")
        if not boundary:
            open_ended = bp
            open_ended_count += 1
        else:
            try:
                num = float(boundary)
            except (ValueError, TypeError):
                raise ValueError(f"Breakpoint has non-numeric boundary '{boundary}'")
            if not math.isfinite(num):
                raise ValueError(f"Breakpoint has non-finite boundary '{boundary}'")
            bounded.append({"boundary": num, "label": label})

    # Reject more than one open-ended boundary: only the last would ever win,
    # so extras would be silently dropped (fail loud instead).
    if open_ended_count > 1:
        raise ValueError(
            "A breakpoints factor may have at most one open-ended boundary "
            f"(empty boundary); found {open_ended_count}"
        )

    # An open-ended boundary needs at least one bounded breakpoint to anchor
    # its lower edge.  A sole open-ended breakpoint would otherwise produce no
    # rules at all and silently emit no output column — fail loud instead.
    if open_ended is not None and not bounded:
        raise ValueError(
            "A breakpoints factor with only an open-ended boundary (no bounded "
            "breakpoints) cannot define any interval; add at least one bounded "
            "breakpoint"
        )

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
    return normalise_banding_factors(config)


def _apply_banding_factors(lf: _Frame, factors: Iterable[dict[str, Any]]) -> _Frame:
    """Apply normalised banding *factors* to a frame, in order.

    The single application loop shared by the executor's banding node
    builder (``_builders._build_banding``) and the generated-code entry
    point :func:`apply_banding_from_config`, so the GUI canvas and a
    standalone run of the saved file band identically.

    Factors missing a column, output column, or rules are skipped — the
    node is a passthrough for those factors (matching the executor's
    long-standing semantics; an empty config is a documented no-op).
    """
    for factor in factors:
        col = factor.get("column", "")
        out = factor.get("outputColumn", "")
        rules = factor.get("rules", []) or []
        if not col or not out or not rules:
            continue
        lf = _apply_banding(
            lf,
            col,
            out,
            factor.get("banding", "continuous"),
            rules,
            factor.get("default"),
            right_closed=factor.get("rightClosed", True),
        )
    return lf


def apply_banding_from_config(
    lf: _Frame,
    config: dict[str, Any] | str | PathLike[str],
    *,
    base_dir: str | Path | None = None,
) -> _Frame:
    """Apply a banding JSON/dict config to a frame.

    The generated-code twin of the executor's banding builder — saved
    pipeline files embed ``apply_banding_from_config(df, "config/banding/
    <name>.json", base_dir=...)`` so a standalone ``pipeline.run()`` bands
    exactly like the GUI executor.  Mirrors
    :func:`apply_rating_step_from_config`.
    """
    if isinstance(config, dict):
        resolved_config = config
    else:
        from haute._config_io import load_node_config

        config_path = config if isinstance(config, str) else Path(config)
        resolved_config = load_node_config(
            config_path, base_dir=Path(base_dir) if base_dir else None
        )

    return _apply_banding_factors(lf, _normalise_banding_factors(resolved_config))


# ---------------------------------------------------------------------------
# Rating tables
# ---------------------------------------------------------------------------

_LOOKUP_VAL = "__haute_lookup_val__"
_SUPPORTED_COMBINE_OPERATIONS = frozenset({"multiply", "add", "min", "max"})
_SUPPORTED_ON_MISSING = frozenset({"error", "neutral"})
_MISS_KEY_DISPLAY_CAP = 10
# Int-like floats collapse to integer digit strings only inside the Int64
# range, where the cast on the engine side is exact and lossless.
_INT64_MIN = -(2**63)
_INT64_MAX_EXCL = 2**63


class RatingTableMissError(ValueError):
    """A rating-table lookup left rows without a matching entry.

    Raised at materialisation time when a table has no usable
    ``defaultValue`` and ``onMissing`` is ``"error"`` (the default).
    The message names the table, the missing key(s) (capped at
    ``_MISS_KEY_DISPLAY_CAP``) and the affected row count.
    """


def normalise_rating_key(value: Any) -> str | None:
    """Canonical string form of a rating-table factor key.

    Single source of truth shared by the rating engine (whose join sides
    use the expression twin :func:`_rating_key_expr`), sidecar
    persistence (``_rating_step_config``) and trace enrichment
    (``_trace_enrichment._enrich_single_table``) — so the matched/default
    flags shown in a trace agree with what the lookup join actually did.

    Rules:

    * ``None`` stays ``None`` — null keys never match the lookup join.
    * Booleans format as the engine casts them: ``"true"`` / ``"false"``.
    * Finite int-like floats inside the Int64 range collapse to their
      integer digit string (``25.0`` -> ``"25"``) so numeric factor
      columns match string-keyed table entries deterministically.
    * Other floats delegate to Polars' Utf8 cast so exotic values
      (exponent-formatted, NaN, inf) have exactly one formatting.
    * Everything else is ``str(value)``.

    String keys are deliberately verbatim — ``"25.0"`` is a label, not a
    number, and never collapses.  ``Float32`` engine columns are widened to
    ``Float64`` by the expression twin before formatting, and this mirror
    only ever sees a value already promoted to ``Float64`` across the
    trace/JSON boundary (``Float32`` has no distinct Python scalar), so the
    two agree for every float dtype (pinned in
    ``tests/test_rating_key_agreement.py``).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer() and _INT64_MIN <= value < _INT64_MAX_EXCL:
            return str(int(value))
        return str(pl.Series([value], dtype=pl.Float64).cast(pl.Utf8).item())
    return str(value)


def _rating_key_expr(name: str, dtype: pl.DataType) -> pl.Expr:
    """Expression twin of :func:`normalise_rating_key` for a frame column.

    Applied to *both* sides of the rating lookup join, so the engine is
    internally consistent by construction; agreement with the Python
    mirror is pinned by ``tests/test_rating_key_agreement.py``.

    ``Float32`` columns are widened to ``Float64`` before any formatting.
    The Python mirror only ever sees the value already promoted to
    ``Float64`` across the trace/JSON boundary, so formatting the column at
    native ``f32`` precision here made the engine key diverge from the mirror
    — a trace could then report matched/default disagreeing with the join
    (a silent neutral/default mispricing).  Widening keeps engine == mirror
    for every float dtype.

    ``Decimal`` (and other exact) columns fall through to a plain ``Utf8``
    cast at their declared scale: a ``Decimal`` factor level must be authored
    at the column's scale (``"25.50"`` for a scale-2 column), because
    ``"25.5"`` and ``"25.50"`` are distinct string keys.
    """
    col = pl.col(name)
    if dtype in (pl.Float32, pl.Float64):
        if dtype == pl.Float32:
            col = col.cast(pl.Float64)
        int_like = (
            col.is_finite()
            & (col == col.round())
            & (col >= float(_INT64_MIN))
            & (col < float(_INT64_MAX_EXCL))
        )
        # strict=False: when/then evaluates the cast on every row, including
        # rows routed to otherwise (e.g. 1e300); the condition guarantees the
        # nulled overflows are never selected.
        return (
            pl.when(int_like)
            .then(col.cast(pl.Int64, strict=False).cast(pl.Utf8))
            .otherwise(col.cast(pl.Utf8))
            .alias(name)
        )
    return col.cast(pl.Utf8).alias(name)


def _frame_schema(lf: _Frame) -> Any:
    if hasattr(lf, "collect_schema"):
        return lf.collect_schema()
    return dict(zip(lf.columns, lf.dtypes))


def _schema_names(schema: Any) -> list[str]:
    names = getattr(schema, "names", None)
    if callable(names):
        return list(names())
    return list(schema.keys())


def _is_unsupported_factor_dtype(dtype: pl.DataType) -> bool:
    return dtype in (pl.Date, pl.Datetime)


def _validate_supported_factor_dtypes(
    original_dtypes: dict[str, pl.DataType],
    *,
    table_label: str,
) -> None:
    for factor, dtype in original_dtypes.items():
        if _is_unsupported_factor_dtype(dtype):
            raise ValueError(
                f"Rating table {table_label!r} factor {factor!r} has unsupported "
                f"dtype {dtype}; date/datetime factor columns are not supported"
            )


def _normalise_on_missing(value: object) -> str:
    """Return a validated rating-table miss policy ("error" | "neutral")."""
    if value is None:
        return "error"
    normalised = str(value).strip()
    if not normalised:
        return "error"
    if normalised not in _SUPPORTED_ON_MISSING:
        raise ValueError(
            f"Unsupported rating table onMissing {normalised!r}; "
            f"expected one of {sorted(_SUPPORTED_ON_MISSING)!r}"
        )
    return normalised


def _rating_miss_guard_expr(
    factors: list[str],
    *,
    table_label: str,
    output_col: str,
    on_missing: str,
    default_note: str = "",
) -> pl.Expr:
    """Validate lookup misses inside the lazy plan, batch by batch.

    Runs as a ``map_batches`` over a struct of the canonical (Utf8)
    factor columns plus the joined value, so it stays lazy- and
    streaming-compatible, never re-executes the upstream plan, and fires
    exactly when the plan materialises.  ``on_missing == "error"`` raises
    :class:`RatingTableMissError`; ``"neutral"`` logs a WARNING with the
    table name, miss count and missing keys.  Under the streaming engine
    rows arrive in batches, so counts are per batch.
    """

    def _check(batch: pl.Series) -> pl.Series:
        frame = batch.struct.unnest()
        values = frame[_LOOKUP_VAL]
        miss_mask = values.is_null()
        miss_count = int(miss_mask.sum())
        if not miss_count:
            return values
        missed = frame.filter(miss_mask).select(factors).unique(maintain_order=True)
        shown = missed.head(_MISS_KEY_DISPLAY_CAP).to_dicts()
        if on_missing == "neutral":
            logger.warning(
                "rating_table_lookup_misses",
                table=table_label,
                output_column=output_col,
                miss_count=miss_count,
                row_count=frame.height,
                distinct_missing_keys=missed.height,
                missing_keys=shown,
            )
            return values
        shown_text = ", ".join(str(key) for key in shown)
        raise RatingTableMissError(
            f"Rating table {table_label!r} (output column {output_col!r}): "
            f"{miss_count} of {frame.height} row(s) had no matching entry for "
            f"factor(s) {factors!r}. Missing key(s): {shown_text} "
            f"(showing {len(shown)} of {missed.height} distinct).{default_note} "
            "Add the missing level(s) to the table, set a numeric "
            '\'defaultValue\', or set "onMissing": "neutral" to accept '
            "neutral pricing for misses."
        )

    return (
        pl.struct([*factors, _LOOKUP_VAL])
        .map_batches(_check, return_dtype=pl.Float64, is_elementwise=True)
        .alias(_LOOKUP_VAL)
    )


def _normalise_combine_operation(operation: object) -> str:
    """Return a validated rating combine operation."""
    normalised = str(operation or "multiply")
    if normalised not in _SUPPORTED_COMBINE_OPERATIONS:
        raise ValueError(
            f"Unsupported rating combine operation {normalised!r}; "
            f"expected one of {sorted(_SUPPORTED_COMBINE_OPERATIONS)!r}"
        )
    return normalised


def _apply_rating_table(
    lf: _Frame,
    table: dict[str, Any],
    *,
    input_schema: Any | None = None,
) -> _Frame:
    """Apply a single rating table lookup via a Polars left join.

    *table* must contain ``factors`` (list of column names to join on),
    ``outputColumn``, ``entries`` (list of dicts with one key per factor
    plus a ``value`` key), and optionally ``defaultValue`` and
    ``onMissing``.

    Both join sides are canonicalised with :func:`_rating_key_expr`, so
    int-like float keys (``25.0``) match string-keyed entries (``"25"``)
    deterministically.

    Miss policy (3a.3 — breaking change, release-noted): a lookup miss
    with no usable ``defaultValue`` raises :class:`RatingTableMissError`
    at materialisation.  ``"onMissing": "neutral"`` opts back in to the
    old behaviour explicitly — the table output stays null (combined
    outputs fill the operation's neutral element) and every miss is
    counted and logged at WARNING.  A usable ``defaultValue`` always
    fills misses with no error or warning.
    """
    factors: list[str] = table.get("factors", []) or []
    entries: list[dict[str, Any]] = table.get("entries", []) or []
    output_col: str = table.get("outputColumn", "")
    default_raw = table.get("defaultValue")
    on_missing = _normalise_on_missing(table.get("onMissing"))

    if not factors or not entries or not output_col:
        return lf

    # Parse the default up front (B13: tolerate non-numeric/non-finite
    # values) — whether a usable default exists decides if the miss guard
    # is wired into the plan at all, and an unusable one is named in the
    # miss error instead of being silently ignored.
    has_default = bool(default_raw is not None and str(default_raw).strip())
    try:
        default_val: float | None = float(str(default_raw)) if has_default else None
    except (ValueError, TypeError):
        default_val = None
    # Reject inf/nan — they corrupt downstream arithmetic silently
    if default_val is not None and not math.isfinite(default_val):
        default_val = None
    default_note = ""
    if has_default and default_val is None:
        default_note = (
            f" Note: configured defaultValue {default_raw!r} is not a usable "
            "finite number and was ignored."
        )

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
    # Reject null entry values too (3a.3): sidecar validation has always
    # required a value per entry, and a null rate would neutral-fill in
    # combined outputs exactly like a miss — but invisibly, because after
    # the join it is indistinguishable from one.  Rejecting nulls here
    # keeps the miss guard's "no matching entry" diagnosis always true.
    _null_count = lookup.get_column("value").null_count()
    if _null_count:
        raise ValueError(
            f"Rating table for '{output_col}' contains {_null_count} null entry "
            "value(s); every entry requires a finite numeric value"
        )

    # B15: Select only factor columns + "value" to avoid polluting the main
    # frame with extra keys that may be present in the entries dicts.
    # Guard: if any factor column is missing from entries, config is invalid.
    missing = [f for f in factors if f not in lookup.columns]
    if missing:
        return lf
    lookup = lookup.select([*factors, "value"])

    # Canonicalise factor keys on the lookup side (3a.4) BEFORE deduplication.
    # The same expression is applied to both join sides, so int-like float
    # keys match their string form deterministically.  After the B15 select
    # every factor is guaranteed to be a lookup column.  Canonicalising first
    # is load-bearing for B14 below: two raw-distinct keys that collapse to
    # the same canonical form (e.g. 25.0 and "25") must not both survive and
    # fan out the left join.
    lookup_schema = lookup.schema
    lookup = lookup.with_columns([_rating_key_expr(f, lookup_schema[f]) for f in factors])

    # B14: Deduplicate on the CANONICAL factor keys so a left join cannot fan
    # out rows.  keep="last" preserves the last-authored entry among a
    # canonical collision, matching trace enrichment which walks entries in
    # reverse to report the same winning row.
    lookup = lookup.unique(subset=factors, keep="last")

    # Rename "value" to an internal name to avoid collision with any
    # input "value" column in the input frame (Bug #1/#2).
    lookup = lookup.rename({"value": _LOOKUP_VAL})

    # Canonicalise factor columns in the main frame too.  Collect the frame
    # schema once: it gives both the existing-column set and the original
    # dtypes needed to restore factor columns after the join.
    frame_schema = input_schema if input_schema is not None else _frame_schema(lf)
    existing_cols = set(_schema_names(frame_schema))
    original_dtypes = {f: frame_schema[f] for f in factors if f in existing_cols}
    _validate_supported_factor_dtypes(
        original_dtypes,
        table_label=str(table.get("name") or "").strip() or output_col,
    )

    cast_exprs = [_rating_key_expr(f, original_dtypes[f]) for f in factors if f in existing_cols]
    if cast_exprs:
        lf = lf.with_columns(cast_exprs)

    # Left join.  Preserve the input row order explicitly because Polars
    # streaming joins may otherwise emit hash-partition order.
    lf = lf.join(lookup.lazy(), on=factors, how="left", maintain_order="left")

    # Miss guard (3a.3): only when no usable default exists — a usable
    # defaultValue fills every miss below, so nothing can be silent.
    # Placed before the dtype revert so the error/warning shows the
    # canonical key strings the join actually used.
    if default_val is None:
        lf = lf.with_columns(
            _rating_miss_guard_expr(
                factors,
                table_label=str(table.get("name") or "").strip() or output_col,
                output_col=output_col,
                on_missing=on_missing,
                default_note=default_note,
            )
        )

    # Revert factor columns to their original dtypes
    revert_exprs = [pl.col(f).cast(dtype) for f, dtype in original_dtypes.items()]
    if revert_exprs:
        lf = lf.with_columns(revert_exprs)

    # Rename value → outputColumn, apply default
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

    Null inputs fold in as the operation's neutral element (1.0 multiply,
    0.0 add; min/max skip nulls horizontally).  In the rating-step path
    nulls can only reach this point when a table explicitly opted in with
    ``"onMissing": "neutral"`` — every such miss has already been counted
    and logged by the miss guard in ``_apply_rating_table`` (3a.3);
    misses are otherwise rejected loudly there.
    """
    operation = _normalise_combine_operation(operation)
    if not columns:
        return lf
    if len(columns) == 1:
        return lf.with_columns(pl.col(columns[0]).alias(output_col))

    if operation == "add":
        # fill_null(0.0) for add: an opted-in miss contributes nothing
        # fill_nan(0.0) also catches NaN from bad lookup entries
        expr = pl.col(columns[0]).fill_null(0.0).fill_nan(0.0)
        for c in columns[1:]:
            expr = expr + pl.col(c).fill_null(0.0).fill_nan(0.0)
    elif operation == "min":
        expr = pl.min_horizontal(*[pl.col(c) for c in columns])
    elif operation == "max":
        expr = pl.max_horizontal(*[pl.col(c) for c in columns])
    else:  # multiply (default)
        # fill_null(1.0) for multiply: an opted-in miss has no effect (neutral element)
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
    operation = _normalise_combine_operation(config.get("operation", "multiply"))
    legacy_output = (
        {
            "outputColumn": str(combined),
            "operation": operation,
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
        operation = _normalise_combine_operation(item.get("operation", "multiply"))

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


def _rating_table_materialises(table: dict[str, Any]) -> bool:
    """Whether :func:`_apply_rating_table` will add *table*'s output column.

    Mirrors that function's passthrough guards: a table with no factors, no
    entries, no output column, entries lacking a ``value`` key, or a factor
    column absent from every entry is a documented no-op and produces no
    output column.  Used so the rating-step loop never registers a phantom
    output column for combining/reference when the table was skipped.
    """
    factors: list[str] = table.get("factors", []) or []
    entries: list[dict[str, Any]] = table.get("entries", []) or []
    output_col = table.get("outputColumn", "")
    if not factors or not entries or not output_col:
        return False
    entry_cols: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            entry_cols.update(entry.keys())
    if "value" not in entry_cols:
        return False
    return all(f in entry_cols for f in factors)


def _apply_rating_step_outputs(
    lf: _Frame,
    tables: list[dict[str, Any]],
    combined_outputs: list[dict[str, Any]],
) -> _Frame:
    """Apply rating tables and combined outputs to a frame."""
    if isinstance(lf, pl.DataFrame):
        lf = lf.lazy()

    # Resolve the frame schema once and thread it through every table so each
    # _apply_rating_table avoids re-running collect_schema() on a growing lazy
    # plan (O(N^2) -> O(N)).  Each materialised table adds its Float64 output
    # column to this local view for subsequent tables.
    schema: dict[str, Any] = dict(_frame_schema(lf))

    out_cols: list[str] = []
    for table in tables:
        lf = _apply_rating_table(lf, table, input_schema=schema)
        output_col = str(table.get("outputColumn", "") or "").strip()
        # Only register the output column when the table actually materialised
        # it.  An incomplete table is a documented passthrough; registering a
        # phantom column would make a downstream combined output combine or
        # reference a column that never appears in the frame.
        if output_col and _rating_table_materialises(table):
            out_cols.append(output_col)
            schema[output_col] = pl.Float64

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
        normalise_rating_tables(resolved_config),
        _normalise_combined_outputs(resolved_config),
    )
