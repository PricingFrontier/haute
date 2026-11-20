"""Rating and banding helpers extracted from executor.

This module contains the pure-logic functions for applying banding rules
and rating table lookups to Polars frames.  They are used by
``executor._build_node_fn`` but have no dependency on the executor
module itself, keeping the dependency graph acyclic.
"""

from __future__ import annotations

import operator
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

import polars as pl

from haute._banding_config import (
    banding_default,
    banding_factor_is_active,
    compact_banding_config_for_sidecar,
    normalise_banding_factors,
    normalise_banding_rules,
    require_distinct_banding_outputs,
)
from haute._logging import get_logger
from haute._polars_dtypes import DATE_KINDS, column_kind, dtype_to_spec, parse_dtype
from haute._rating_step_config import (
    normalise_rating_tables,
    rating_default_value,
    validate_unique_rating_table_outputs,
)
from haute._typed_numbers import read_typed_number, setting_number, trim_typed
from haute._types import NodeType, _Frame
from haute._validation_error import ConfigSettingError
from haute.errors import (
    RatingExtremaUndefinedError,
    RatingFactorMissingError,
    SchemaMismatchError,
)

logger = get_logger(component="rating")


class RatingDuplicateEntriesError(ConfigSettingError):
    """A rating table with two entries for the same levels (READ-06).

    Refused at run time once the levels are converted to the factor columns'
    types, and on save for levels identical as written or equal under the
    table's ``factor_dtypes``; no path keeps one of the two. ``levels`` are the
    entries' levels as written.
    """

    def __init__(self, table: Mapping[str, Any], levels: list[dict[str, Any]]) -> None:
        name = table.get("name") or table.get("output_column") or "unnamed"
        written = "; ".join(
            ", ".join(f"{factor}={value!r}" for factor, value in level.items()) for level in levels
        )
        super().__init__(
            f"Rating table {name!r} has more than one entry for the same levels: {written}. "
            "Keep one entry for each combination of levels.",
            setting="tables",
            fix="Remove or merge the duplicate entries.",
            values=tuple(levels),
        )
        self.table = name
        self.levels = levels


# ---------------------------------------------------------------------------
# Banding
# ---------------------------------------------------------------------------

# The operators of the interval rules breakpoints become (`_breakpoints_to_rules`).
SUPPORTED_BANDING_OPERATORS = MappingProxyType(
    {
        "<": operator.lt,
        "<=": operator.le,
        ">": operator.gt,
        ">=": operator.ge,
    }
)
SUPPORTED_BANDING_TYPES = frozenset({"breakpoints", "categorical"})


def require_banding_type(banding_type: str, *, subject: str = "Banding") -> None:
    """Raise unless *banding_type* is one of the banding types; there is no default."""
    if banding_type not in SUPPORTED_BANDING_TYPES:
        allowed = ", ".join(sorted(SUPPORTED_BANDING_TYPES))
        raise ConfigSettingError(
            f"{subject} has unsupported banding type {banding_type!r}; expected one of: {allowed}",
            setting="factors",
            values=(banding_type,),
        )


BoundaryKind = Literal["number", "date", "datetime"]

_DATE_BOUNDARY = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME_BOUNDARY = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?")


def parse_breakpoint_boundary(boundary: str) -> float | date | datetime:
    """Read one breakpoint boundary: a number, a date, or a date and time.

    A number is read by the one grammar for a typed number
    (``haute._typed_numbers``), finite; a date is ``YYYY-MM-DD``; a date and
    time is ``YYYY-MM-DD HH:MM`` with optional seconds and fraction, ``T`` or a
    space between. A time carries no UTC offset: it is read in the banded
    column's own time zone.
    """
    text = trim_typed(boundary)
    number = read_typed_number(text)
    if number is not None:
        return number
    try:
        if _DATE_BOUNDARY.fullmatch(text):
            return date.fromisoformat(text)
        if _DATETIME_BOUNDARY.fullmatch(text):
            return datetime.fromisoformat(text)
    except ValueError:
        pass
    raise ConfigSettingError(
        f"Breakpoint has unreadable boundary '{boundary}'; expected a number, a date "
        "(YYYY-MM-DD) or a date and time (YYYY-MM-DD HH:MM)",
        setting="factors",
        values=(boundary,),
    )


def _boundary_kind(value: float | date | datetime) -> BoundaryKind:
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    return "number"


def _boundary_text(breakpoint: dict[str, Any]) -> str:
    """A breakpoint's boundary as text; only a missing or empty one is open-ended."""
    boundary = breakpoint.get("boundary")
    return "" if boundary is None else str(boundary).strip()


def breakpoints_kind(breakpoints: list[dict[str, Any]] | dict[str, Any]) -> BoundaryKind:
    """The one kind every bounded breakpoint shares; a mix is rejected."""
    kinds = {
        _boundary_kind(parse_breakpoint_boundary(boundary))
        for bp in normalise_banding_rules("breakpoints", breakpoints)
        if (boundary := _boundary_text(bp))
    }
    if len(kinds) > 1:
        raise ConfigSettingError(
            "Breakpoints mix " + " and ".join(sorted(kinds)) + " boundaries; use one kind",
            setting="factors",
        )
    return kinds.pop() if kinds else "number"


def _banding_rule_comparators(
    rule: dict[str, Any],
) -> list[tuple[str, float | date | datetime]]:
    """Return the validated operator/threshold pairs usable by one rule."""
    comparators: list[tuple[str, float | date | datetime]] = []
    for suffix in ("1", "2"):
        op = str(rule.get(f"op{suffix}", "") or "").strip()
        val = rule.get(f"val{suffix}")
        if not op or val is None or val == "":
            continue
        evaluator = SUPPORTED_BANDING_OPERATORS.get(op)
        if evaluator is None:
            raise ConfigSettingError(
                f"Banding rule has unsupported operator '{op}' for op{suffix}",
                setting="factors",
                values=(op,),
            )
        if isinstance(val, date):
            # A date or date-and-time boundary, already read from the breakpoint.
            comparators.append((op, val))
            continue
        num = setting_number(val)
        if num is None:
            finite = isinstance(val, int | float) and not isinstance(val, bool)
            raise ConfigSettingError(
                f"Banding rule has non-{'finite' if finite else 'numeric'} threshold '{val}' "
                f"for op{suffix}",
                setting="factors",
                values=(val,),
            )
        comparators.append((op, num))
    return comparators


def _banding_condition(col: pl.Expr, rule: dict[str, Any]) -> pl.Expr | None:
    """Build a Polars boolean expression from one interval rule of a breakpoint."""
    parts: list[pl.Expr] = [
        SUPPORTED_BANDING_OPERATORS[op](col, threshold)
        for op, threshold in _banding_rule_comparators(rule)
    ]
    if not parts:
        return None
    result = parts[0]
    for p in parts[1:]:
        result = result & p
    return result


def _column_kind_fix(dtype: Any) -> str:
    """The boundaries a column of *dtype* takes, for a breakpoint-kind mismatch."""

    if isinstance(dtype, pl.Datetime) or dtype == pl.Datetime:
        return (
            "A Datetime column's boundaries are dates like 2024-12-31 or dates and times "
            "like 2024-12-31 18:00."
        )
    if dtype == pl.Date:
        return "A Date column's boundaries are dates like 2024-12-31."
    if dtype is not None and dtype.is_numeric():
        return "A number column's boundaries are numbers like 25."
    return (
        "Breakpoints band a number, Date or Datetime column; band this column with "
        "categorical rules."
    )


def _is_temporal(dtype: Any) -> bool:
    return dtype is not None and column_kind(dtype) in DATE_KINDS


def _banding_subject(output_column: str, column: str) -> str:
    """How a refusal names a banding factor: its output and the column it reads."""
    subject = f"Banding output {output_column!r}" if output_column else "Banding"
    return f"{subject} of column {column!r}" if column else subject


def banding_comparison_expr(
    col: pl.Expr,
    dtype: Any,
    kind: BoundaryKind,
    output_column: str = "",
    *,
    column: str = "",
) -> pl.Expr:
    """The column breakpoints of *kind* are compared against.

    Numbers compare a numeric column, with NaN and infinity made missing first
    so they fall to the default rather than matching an arbitrary band. Dates
    compare a Date column, or a Datetime column's calendar date; dates and times
    compare a Datetime column's wall-clock time. Both read a time-zoned column
    in its own zone. Any other pairing is refused, naming the output and
    *column*. The expression stays local to one banding output — aliasing it
    back onto the source column would change that column for every downstream
    node.
    """
    subject = _banding_subject(output_column, column)
    if dtype == pl.Null:
        # An all-null column holds nothing to compare: every row takes the default.
        return col
    if kind == "number":
        if _is_temporal(dtype) or (dtype is not None and not dtype.is_numeric()):
            raise ConfigSettingError(
                f"{subject} has number breakpoints, but the column is {dtype}",
                setting="factors",
                fix=_column_kind_fix(dtype),
            )
        if dtype is not None and column_kind(dtype) == "float":
            return pl.when(col.is_nan() | col.is_infinite()).then(pl.lit(None)).otherwise(col)
        return col
    if isinstance(dtype, pl.Datetime) or dtype == pl.Datetime:
        wall_clock = col.dt.replace_time_zone(None) if getattr(dtype, "time_zone", None) else col
        return wall_clock.dt.date() if kind == "date" else wall_clock
    if (dtype == pl.Date) and kind == "date":
        return col
    what = "date" if kind == "date" else "date-and-time"
    raise ConfigSettingError(
        f"{subject} has {what} breakpoints, but the column is {dtype}",
        setting="factors",
        fix=_column_kind_fix(dtype),
    )


def banding_temporal_ordinal_expr(col: pl.Expr, dtype: Any) -> pl.Expr:
    """Days since 1970-01-01 of a Date or Datetime column's wall-clock value.

    The banding statistics measure a date column on this scale, fractional for
    a Datetime, so the editor can draw and generate bands in dates.
    """
    if isinstance(dtype, pl.Datetime) or dtype == pl.Datetime:
        wall_clock = col.dt.replace_time_zone(None) if getattr(dtype, "time_zone", None) else col
        return wall_clock.dt.epoch("ms").cast(pl.Float64) / 86_400_000
    return col.cast(pl.Int32).cast(pl.Float64)


def banding_categorical_claims(
    rules: list[dict[str, Any]],
    dtype: Any,
    *,
    output_column: str = "",
    column: str = "",
) -> dict[str, tuple[int, str]]:
    """Map each claimed key to its rule: ``key -> (index in rules, assignment)``.

    A rule's value is keyed as a Rating Step entry's level is
    (:func:`typed_level_keys`), through the type *dtype* of the column it
    bands, and the column is matched on :func:`rating_key_of`: so ``true``,
    ``"true"`` and ``1`` all claim a Boolean column's ``true``, and a value the
    column cannot hold is refused, naming the output column and the value. Two
    rules claiming one value are refused, never resolved by one of them. A rule
    missing either its value or its assignment claims nothing, and rules none
    of which has both are refused. On a column of Null type no rule can claim
    a row, so there are no claims.
    """
    usable = [
        (index, rule["value"], str(rule["assignment"]))
        for index, rule in enumerate(rules)
        if rule.get("value") not in (None, "") and rule.get("assignment") not in (None, "")
    ]
    if not rules:
        return {}
    if not usable:
        raise ConfigSettingError(
            _no_usable_rule_message(output_column, "categorical"), setting="factors"
        )
    subject = _banding_subject(output_column, column)
    if dtype is None:
        raise SchemaMismatchError(f"{subject}: the column is not in the node's input")
    keys = typed_level_keys(
        [value for _index, value, _assignment in usable],
        dtype,
        subject=subject,
        setting="factors",
        noun="rule value",
    )
    claims: dict[str, tuple[int, str]] = {}
    # A rule whose key is null (a Null column) claims nothing, but two rules
    # written alike are still two rules for one value.
    unmatchable: dict[tuple[type, Any], tuple[int, str]] = {}
    for key, (index, value, assignment) in zip(keys, usable, strict=True):
        held: dict[Any, tuple[int, str]] = claims if key is not None else unmatchable
        ident: Any = key if key is not None else _written(value)
        if ident in held:
            earlier = held[ident][0]
            raise ConfigSettingError(
                f"{subject} has rules {earlier + 1} and {index + 1} for one value "
                f"({rules[earlier]['value']!r} and {value!r}) of its column's type, {dtype}",
                setting="factors",
                fix="Keep one rule for each value.",
                values=(rules[earlier]["value"], value),
            )
        held[ident] = (index, assignment)
    return claims


def banding_interval_claims(
    breakpoints: list[dict[str, Any]],
    *,
    right_closed: bool = True,
) -> list[tuple[int, dict[str, Any]]]:
    """The interval rules execution evaluates for *breakpoints*, in order, each with its source.

    Execution evaluates the intervals sorted by boundary with the open-ended one
    last — so a rule's index is its breakpoint's position in *breakpoints* as
    the user wrote them, not its position in the chain.
    """
    return [
        (source, rule)
        for rule, source in _breakpoints_to_rules_with_sources(
            breakpoints, right_closed=right_closed
        )
    ]


def banding_rule_claim_expr(
    column_expr: pl.Expr,
    dtype: Any,
    mode: str,
    rules: list[dict[str, Any]] | dict[str, Any],
    right_closed: bool = True,
    *,
    output_column: str = "",
) -> pl.Expr:
    """Per row, the index in *rules* of the rule that claims it, or null.

    A claim is exactly the rule whose assignment :func:`_apply_banding` writes
    for that row, because both are built from the same preparation: the same
    normalisation, the same breakpoint intervals, the same typed categorical
    remap, the same refusal of a rule without an assignment, and the same
    treatment of NaN and infinity as missing. A row no rule claims takes the
    default, so a null claim is exactly a defaulted row — which grouping the
    output column cannot tell you, because rules may share an assignment and the
    default may equal one.

    Rules execution would reject raise the same :class:`ValueError` it raises.
    """
    require_banding_type(mode)
    prepared = normalise_banding_rules(mode, rules, output_column=output_column)
    unclaimed = pl.lit(None, dtype=pl.Int32)
    if not prepared:
        # A factor with no rules is a no-op in execution, so every row is
        # unclaimed — one null per row, not a single null.
        return pl.repeat(None, pl.len(), dtype=pl.Int32).alias("claim")
    column = column_expr.meta.output_name(raise_if_undetermined=False) or ""

    if mode == "categorical":
        claims = banding_categorical_claims(
            prepared, dtype, output_column=output_column, column=column
        )
        return (
            rating_key_of(column_expr, dtype)
            .replace_strict(
                {key: index for key, (index, _assignment) in claims.items()},
                default=None,
                return_dtype=pl.Int32,
            )
            .alias("claim")
        )

    col = banding_comparison_expr(
        column_expr, dtype, breakpoints_kind(prepared), output_column, column=column
    )
    chain: Any = None
    for index, rule in banding_interval_claims(prepared, right_closed=right_closed):
        cond = _banding_condition(col, rule)
        if cond is None or not str(rule.get("assignment", "")):
            continue
        claim = pl.lit(index, dtype=pl.Int32)
        chain = pl.when(cond).then(claim) if chain is None else chain.when(cond).then(claim)
    if chain is None:
        raise ConfigSettingError(
            _no_usable_rule_message(output_column, "breakpoints"), setting="factors"
        )
    claimed: pl.Expr = chain.otherwise(unclaimed).alias("claim")
    return claimed


def _no_usable_rule_message(output_column: str, mode: str) -> str:
    """Execution's message for rules that define nothing it can apply."""
    if output_column:
        return f"Banding output {output_column!r} has no usable {mode} rule"
    return f"Banding rules have no usable {mode} rule"


def _apply_banding(
    lf: _Frame,
    column: str,
    output_column: str,
    banding_type: str,
    rules: list[dict[str, Any]] | dict[str, Any],
    default: Any = None,
    right_closed: bool = True,
    *,
    schema: dict[str, Any] | None = None,
) -> _Frame:
    """Apply banding rules to a column, producing a new output column.

    Breakpoint rules name the upper bound of each band::

        {"boundary": "25", "label": "0-25"}

    Categorical rules map exact values to groups::

        {"value": "Semi-detached House", "assignment": "House"}

    *schema* is the frame's schema when the caller already holds it: the
    factor's column's dtype is read from it, and the band written is recorded in
    it as text, so a caller applying many factors resolves its growing plan once.
    Without it, the frame's schema is resolved here.
    """
    require_banding_type(banding_type, subject=f"Banding output {output_column!r}")
    rules = normalise_banding_rules(banding_type, rules, output_column=output_column)
    has_configured_rules = bool(rules)
    if not has_configured_rules:
        return lf
    default_band = banding_default({"default": default, "output_column": output_column})
    default_lit = pl.lit(default_band, dtype=pl.Utf8)
    if schema is None:
        schema = dict(_frame_schema(lf))
    dtype = schema.get(column)
    if dtype is None:
        raise SchemaMismatchError(
            f"{_banding_subject(output_column, column)}: the column is not in the node's input"
        )

    if banding_type == "categorical":
        # One remap from each rule's typed key to its band, matched on the
        # column's own key form (as a Rating Step entry is).
        remap = {
            key: assignment
            for key, (_index, assignment) in banding_categorical_claims(
                rules, dtype, output_column=output_column, column=column
            ).items()
        }
        cat_expr = (
            rating_key_of(pl.col(column), dtype)
            .replace_strict(remap, default=default_lit, return_dtype=pl.Utf8)
            .alias(output_column)
        )
        schema[output_column] = pl.String
        return lf.with_columns(cat_expr)

    # Breakpoints become interval rules, evaluated as a when/then chain.
    kind = breakpoints_kind(rules)
    rules = _breakpoints_to_rules(rules, right_closed=right_closed)

    # The column as breakpoints of this kind compare it; for numbers, NaN/Inf
    # are made missing so they fall cleanly to the default.
    col = banding_comparison_expr(pl.col(column), dtype, kind, output_column, column=column)

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
        raise ConfigSettingError(
            _no_usable_rule_message(output_column, "breakpoints"), setting="factors"
        )
    final_expr = chain.otherwise(default_lit).alias(output_column)
    schema[output_column] = pl.String
    return lf.with_columns(final_expr)


def _breakpoints_to_rules(
    breakpoints: list[dict[str, Any]] | dict[str, Any],
    right_closed: bool = True,
) -> list[dict[str, Any]]:
    """The intervals execution evaluates, without which breakpoint defined each."""
    return [
        rule
        for rule, _source in _breakpoints_to_rules_with_sources(
            breakpoints, right_closed=right_closed
        )
    ]


def _breakpoints_to_rules_with_sources(
    breakpoints: list[dict[str, Any]] | dict[str, Any],
    right_closed: bool = True,
) -> list[tuple[dict[str, Any], int]]:
    """Convert breakpoint-format rules to the interval rules execution evaluates.

    Each breakpoint has a ``boundary`` (a number, date or date and time; see
    :func:`parse_breakpoint_boundary`) and a ``label``, and every bounded one is
    the same kind. The last breakpoint may have an empty boundary to create an
    open-ended rule.

    When *right_closed* is True, intervals are ``(lower, upper]`` — the first
    rule uses ``<=`` for its upper bound and subsequent rules use ``>`` / ``<=``.
    When False, intervals are ``[lower, upper)`` using ``>=`` / ``<``.
    """
    breakpoints = normalise_banding_rules("breakpoints", breakpoints)
    if not breakpoints:
        return []
    breakpoints_kind(breakpoints)

    # Separate breakpoints with boundaries from the open-ended tail
    bounded: list[dict[str, Any]] = []
    open_ended: dict[str, Any] | None = None
    open_ended_source = -1
    open_ended_count = 0
    for source, bp in enumerate(breakpoints):
        boundary = _boundary_text(bp)
        label = bp.get("label") or ""
        if not boundary:
            open_ended = bp
            open_ended_source = source
            open_ended_count += 1
        else:
            parsed = parse_breakpoint_boundary(boundary)
            bounded.append({"boundary": parsed, "label": label, "source": source})

    # Reject more than one open-ended boundary: only the last would ever win,
    # so extras would be silently dropped (fail loud instead).
    if open_ended_count > 1:
        raise ConfigSettingError(
            "A breakpoints factor may have at most one open-ended boundary "
            f"(empty boundary); found {open_ended_count}",
            setting="factors",
        )

    # An open-ended boundary needs at least one bounded breakpoint to anchor
    # its lower edge.  A sole open-ended breakpoint would otherwise produce no
    # rules at all and silently emit no output column — fail loud instead.
    if open_ended is not None and not bounded:
        raise ConfigSettingError(
            "A breakpoints factor with only an open-ended boundary (no bounded "
            "breakpoints) cannot define any interval; add at least one bounded "
            "breakpoint",
            setting="factors",
        )

    # Sort by boundary value
    bounded.sort(key=lambda b: b["boundary"])

    # Reject duplicate boundaries — they produce empty intervals
    seen_boundaries: set[float | date] = set()
    for entry in bounded:
        if entry["boundary"] in seen_boundaries:
            raise ConfigSettingError(
                f"Duplicate breakpoint boundary '{entry['boundary']}'",
                setting="factors",
                values=(entry["boundary"],),
            )
        seen_boundaries.add(entry["boundary"])

    rules: list[tuple[dict[str, Any], int]] = []
    prev_boundary: float | date | None = None

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

        rules.append((rule, entry["source"]))
        prev_boundary = b

    # Open-ended tail
    if open_ended is not None and prev_boundary is not None:
        label = open_ended.get("label") or ""
        op = ">" if right_closed else ">="
        rules.append(({"op1": op, "val1": prev_boundary, "assignment": label}, open_ended_source))

    return rules


def _normalise_banding_factors(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ``factors`` list from banding config."""
    return normalise_banding_factors(config)


def validate_banding_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and return canonical banding factors.

    Every factor names a banding type, draft or not, and every rule row is one the
    sidecar writer can write (:func:`compact_banding_config_for_sidecar`): a value or
    boundary, what it maps to, no value twice. A factor without a column, output
    column, or rules remains a supported draft no-op; once configured, its breakpoints
    must make intervals at least one of which bands rows.
    """

    factors = normalise_banding_factors(config)
    for index, factor in enumerate(factors):
        require_banding_type(
            str(factor.get("banding", "") or "").strip(), subject=f"Banding factor {index}"
        )
    # Every rule row the sidecar writer would refuse (a blank band name, value or
    # mapping, a repeated value), in a draft factor too, is refused here, before a
    # save writes anything.
    compact_banding_config_for_sidecar(config)
    for factor in factors:
        output_column = str(factor.get("output_column", "") or "").strip()
        configured_rules = factor.get("rules", []) or []
        if not str(factor.get("column", "") or "").strip() or not output_column:
            continue
        if factor.get("banding") == "breakpoints" and configured_rules:
            # The boundaries must make intervals (a lone open-ended band makes none),
            # and one must band rows: a band with no name bands none.
            intervals = _breakpoints_to_rules(
                normalise_banding_rules("breakpoints", configured_rules),
                right_closed=bool(factor.get("right_closed", True)),
            )
            if not any(
                _banding_rule_comparators(rule) and rule.get("assignment") not in (None, "")
                for rule in intervals
            ):
                raise ConfigSettingError(
                    _no_usable_rule_message(output_column, "breakpoints"), setting="factors"
                )
    require_distinct_banding_outputs(factors)
    return factors


def apply_banding_factors(lf: _Frame, factors: Iterable[dict[str, Any]]) -> _Frame:
    """Apply normalised banding *factors* to a frame, in order.

    The single application loop shared by the executor's banding node
    builder (``_builders._build_banding``) and the generated-code entry
    point :func:`apply_banding_from_config`, so the GUI canvas and a
    standalone run of the saved file band identically.

    Draft factors (see :func:`banding_factor_is_active`) are skipped — the
    node is a passthrough for those factors (an empty config is a
    documented no-op) — but only once their type is checked: a draft names
    one of the banding types too. Two active factors writing one output
    column are refused before the frame is touched.

    The frame's schema is resolved once, when the first active factor needs
    its column's dtype, and kept in step with every band written after it, as
    :func:`_apply_rating_step_outputs` threads its own: resolving the growing
    lazy plan per factor made a node of many factors quadratic.
    """
    factors = list(factors)
    require_distinct_banding_outputs(factors)
    schema: dict[str, Any] | None = None
    for index, factor in enumerate(factors):
        banding_type = str(factor.get("banding", "") or "")
        require_banding_type(banding_type, subject=f"Banding factor {index}")
        if not banding_factor_is_active(factor):
            continue
        if schema is None:
            schema = dict(_frame_schema(lf))
        lf = _apply_banding(
            lf,
            factor["column"],
            factor["output_column"],
            banding_type,
            factor["rules"],
            factor.get("default"),
            right_closed=factor.get("right_closed", True),
            schema=schema,
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
            config_path,
            base_dir=Path(base_dir) if base_dir else None,
            node_type=NodeType.BANDING,
        )

    return apply_banding_factors(lf, _normalise_banding_factors(resolved_config))


# ---------------------------------------------------------------------------
# Rating tables
# ---------------------------------------------------------------------------

_LOOKUP_VAL = "__haute_lookup_val__"
_SUPPORTED_COMBINE_OPERATIONS = frozenset({"multiply", "add", "min", "max"})
_MISS_KEY_DISPLAY_CAP = 10
# The Int64 range a Duration key's physical count must lie in.
_INT64_MIN = -(2**63)
_INT64_MAX_EXCL = 2**63
_DURATION_KEY_RE = re.compile(
    r"^(?P<sign>-)?P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)
_DURATION_UNITS_PER_SECOND = MappingProxyType(
    {
        "ms": 1_000,
        "us": 1_000_000,
        "ns": 1_000_000_000,
    }
)
_DURATION_UNIT_NAMES = MappingProxyType(
    {
        "ms": "milliseconds",
        "us": "microseconds",
        "ns": "nanoseconds",
    }
)
_DURATION_KEY_FIX = "A Duration factor's key is an ISO-8601 duration like PT1.5S, PT30M or P1DT2H."
_RATING_PRIMITIVE_DESCRIPTOR_KINDS = frozenset(
    {
        "Int8",
        "Int16",
        "Int32",
        "Int64",
        "Int128",
        "UInt8",
        "UInt16",
        "UInt32",
        "UInt64",
        "Float32",
        "Float64",
        "Boolean",
        "String",
        "Categorical",
        "Date",
        "Time",
        "Null",
    }
)
# A rating descriptor is the shared dtype codec's spec
# (``haute._polars_dtypes.dtype_to_spec``) under rating's persisted key names,
# restricted to the dtypes a rating factor supports.
_RATING_PARAMETRIC_DESCRIPTOR_KINDS = frozenset(
    {"Categorical", "Datetime", "Duration", "Decimal", "Enum"}
)
_RATING_DESCRIPTOR_KEY_BY_SPEC_KEY: Mapping[str, str] = MappingProxyType(
    {"type": "kind", "time_unit": "time_unit", "time_zone": "time_zone"}
)
_RATING_SPEC_KEY_BY_DESCRIPTOR_KEY: Mapping[str, str] = MappingProxyType(
    {descriptor: spec for spec, descriptor in _RATING_DESCRIPTOR_KEY_BY_SPEC_KEY.items()}
)


def rating_dtype_descriptor(dtype: pl.DataType) -> dict[str, Any]:
    """Return the stable JSON descriptor for a supported rating-factor dtype.

    A categorical factor is looked up by its text, so its category pool is not
    part of the descriptor: a categorical of any pool is ``{"kind": "Categorical"}``.
    """
    if isinstance(dtype, pl.Categorical):
        return {"kind": "Categorical"}
    try:
        spec: Any = dtype_to_spec(dtype)
    except SchemaMismatchError:
        # A map or an extension column has no spec, and no rating factor is one.
        spec = None
    if isinstance(spec, str):
        if spec in _RATING_PRIMITIVE_DESCRIPTOR_KINDS:
            return {"kind": spec}
    elif spec is not None and spec["type"] in _RATING_PARAMETRIC_DESCRIPTOR_KINDS:
        return {
            _RATING_DESCRIPTOR_KEY_BY_SPEC_KEY.get(key, key): value for key, value in spec.items()
        }
    raise ConfigSettingError(f"unsupported rating factor dtype {dtype}", setting="tables")


def is_rating_dtype_descriptor(value: object) -> bool:
    """Return whether *value* is one exact stable rating dtype descriptor."""
    if not isinstance(value, dict):
        return False
    kind = value.get("kind")
    if kind in _RATING_PRIMITIVE_DESCRIPTOR_KINDS:
        return set(value) == {"kind"}
    if kind == "Datetime":
        return (
            set(value) == {"kind", "time_unit", "time_zone"}
            and value.get("time_unit") in {"ms", "us", "ns"}
            and (value.get("time_zone") is None or isinstance(value.get("time_zone"), str))
        )
    if kind == "Duration":
        return set(value) == {"kind", "time_unit"} and value.get("time_unit") in {"ms", "us", "ns"}
    if kind == "Decimal":
        precision = value.get("precision")
        scale = value.get("scale")
        return (
            set(value) == {"kind", "precision", "scale"}
            and (
                precision is None
                or (isinstance(precision, int) and not isinstance(precision, bool))
            )
            and isinstance(scale, int)
            and not isinstance(scale, bool)
        )
    if kind == "Enum":
        categories = value.get("categories")
        return (
            set(value) == {"kind", "categories"}
            and isinstance(categories, list)
            and all(isinstance(category, str) for category in categories)
            and len(categories) == len(set(categories))
        )
    return False


def rating_dtype_from_descriptor(descriptor: object) -> pl.DataType:
    """Reconstruct a supported rating-factor dtype from its JSON descriptor."""
    if not is_rating_dtype_descriptor(descriptor):
        raise ConfigSettingError(
            f"invalid rating factor dtype descriptor {descriptor!r}",
            setting="tables",
            values=(descriptor,),
        )

    # The predicate above checks the exact descriptor shape, so the shared
    # codec only has to parse a spec it already accepts.
    assert isinstance(descriptor, dict)
    kind = descriptor["kind"]
    if kind in _RATING_PARAMETRIC_DESCRIPTOR_KINDS:
        spec: Any = {
            _RATING_SPEC_KEY_BY_DESCRIPTOR_KEY.get(key, key): value
            for key, value in descriptor.items()
        }
    else:
        spec = kind
    return cast(pl.DataType, parse_dtype(spec))


def _duration_key_to_physical(value: str, time_unit: str) -> int:
    """Parse Polars' ISO-8601 duration display into an exact physical value.

    The arithmetic is exact, so a key finer than *time_unit* is refused
    however many digits it spells, never rounded onto a neighbouring key.
    """
    match = _DURATION_KEY_RE.fullmatch(value)
    if match is None or not any(
        match.group(name) is not None for name in ("days", "hours", "minutes", "seconds")
    ):
        raise ConfigSettingError(
            f"invalid ISO-8601 duration rating key {value!r}",
            setting="tables",
            fix=_DURATION_KEY_FIX,
            values=(value,),
        )
    whole_seconds = (
        int(match.group("days") or 0) * 86_400
        + int(match.group("hours") or 0) * 3_600
        + int(match.group("minutes") or 0) * 60
    )
    seconds = whole_seconds + Fraction(match.group("seconds") or 0)
    physical = seconds * _DURATION_UNITS_PER_SECOND[time_unit]
    unit_name = _DURATION_UNIT_NAMES[time_unit]
    if physical.denominator != 1:
        raise ConfigSettingError(
            f"duration rating key {value!r} is not exactly representable as {time_unit}",
            setting="tables",
            fix=f"Round the key to whole {unit_name}.",
            values=(value,),
        )
    result = -physical.numerator if match.group("sign") else physical.numerator
    if not _INT64_MIN <= result < _INT64_MAX_EXCL:
        raise ConfigSettingError(
            f"duration rating key {value!r} is out of range for {time_unit}",
            setting="tables",
            fix=f"A Duration({time_unit}) column holds between -2**63 and 2**63 - 1 {unit_name}.",
            values=(value,),
        )
    return result


def _duration_key_series(name: str, keys: pl.Series, dtype: pl.Duration) -> pl.Series:
    """Parse String duration *keys* into *dtype* in Python, before any expression runs.

    A key that names no exact duration of *dtype* is refused here as a
    ``ConfigSettingError``. Raised inside a Polars callback, the error would be
    recreated from its message alone and surface as a ``TypeError`` instead.
    """
    physical = [
        None if key is None else _duration_key_to_physical(key, dtype.time_unit)
        for key in keys.to_list()
    ]
    return pl.Series(name, physical, dtype=pl.Int64).cast(dtype)


# Date entry strings are ISO calendar dates; surrounding whitespace is
# stripped first. Polars 1.44 deprecates the String-to-Date cast, and
# ``str.to_date`` without a format would infer other spellings.
_ISO_DATE_FORMAT = "%Y-%m-%d"
# Whole numbers below this are the only ones a Float64 cannot round onto from a
# neighbour: text read as a number at or past it is refused rather than rounded.
_FLOAT_EXACT_INT = 2**53
# How many of a configured level list's misfits a refusal quotes.
_MISFIT_DISPLAY_CAP = 3


def _is_time_kind(dtype: pl.DataType) -> bool:
    """A Date, Datetime, Time or Duration: a value written as text, never as a number."""
    return dtype in (pl.Date, pl.Time) or isinstance(dtype, pl.Datetime | pl.Duration)


def _parsed_text(
    levels: pl.Series, dtype: pl.DataType, parse: Callable[[pl.Series], pl.Series]
) -> pl.Series:
    """*levels* parsed as times by *parse*, each alone, a level no format reads coming back null.

    Polars infers one format from a column's first value, so read together a
    level's reading would depend on the other levels of its table, and a first
    level no format reads refuses the whole list in Polars' words. Read alone,
    each level parses as the scalar path parses it.
    """
    parsed: list[pl.Series] = []
    for value in levels.to_list():
        try:
            parsed.append(parse(pl.Series(levels.name, [value], dtype=pl.String)))
        except pl.exceptions.PolarsError:
            parsed.append(pl.Series(levels.name, [None], dtype=dtype))
    return pl.concat(parsed) if parsed else pl.Series(levels.name, [], dtype=dtype)


def _text_as_number(
    levels: pl.Series, direct: pl.Series, dtype: pl.DataType
) -> tuple[pl.Series, pl.Series | None]:
    """Text levels of an integer or Boolean column: the type's own spelling, else a number.

    *direct* is each level read in the type's own spelling (``25``, ``true``);
    a level it does not read is read as a number (``25.0``, ``1.0``, the text a
    sidecar's map key holds for one) and held to the number rules, so text and
    the number it spells are one level, whichever was written.
    """
    if direct.null_count() == levels.null_count():
        return direct, None
    numbers, number_misfit = _coerce_levels(levels.cast(pl.Float64, strict=False), dtype)
    read = numbers.to_list()
    fraction = [False] * len(read) if number_misfit is None else number_misfit.to_list()
    typed: list[Any] = []
    misfit: list[bool] = []
    for level, own, number, not_whole in zip(
        levels.to_list(), direct.to_list(), read, fraction, strict=True
    ):
        exact = isinstance(number, bool) or (number is not None and abs(number) < _FLOAT_EXACT_INT)
        value = own if own is not None else (number if exact and not not_whole else None)
        typed.append(value)
        misfit.append(level is not None and value is None)
    return pl.Series(levels.name, typed, dtype=dtype), pl.Series(misfit, dtype=pl.Boolean)


def _coerce_levels(levels: pl.Series, dtype: pl.DataType) -> tuple[pl.Series, pl.Series | None]:
    """Configured *levels* read as values of a column of *dtype*, and which of them are not one.

    The one reading of a configured level (a rating entry's key, a categorical
    banding rule's value, a value traced through either) as a value of its
    column's type. A level is a value of the type when the type holds it:
    ``true``/``false`` or ``1``/``0`` for a Boolean, a whole number for an
    integer (text such as ``"25.0"`` read as the number it spells), text a date
    or time parses from for a temporal type, and whatever Polars' cast reads for
    the rest. A level that is not one comes back null and is marked in the
    second series (``None`` when nothing can misfit). A Null column holds no
    value, so every level reads as null there and none misfits: it can never
    match. A Duration's text is parsed by :func:`_duration_key_series`, which
    refuses a misfit itself.
    """
    source = levels.dtype
    if dtype == pl.Null:
        return pl.Series(levels.name, [None] * len(levels), dtype=pl.Null), None
    if source == pl.Null:
        return levels.cast(dtype), None
    if isinstance(dtype, pl.Duration) and source == pl.String:
        return _duration_key_series(levels.name, levels, dtype), None
    numeric_source = source.is_numeric() or source == pl.Boolean
    if dtype == pl.Boolean and source == pl.String:
        own = levels.replace_strict(
            {"true": True, "false": False}, default=None, return_dtype=pl.Boolean
        )
        return _text_as_number(levels, own, dtype)
    if dtype.is_integer() and source == pl.String:
        return _text_as_number(levels, levels.cast(dtype, strict=False), dtype)
    if dtype == pl.Boolean and source.is_numeric():
        typed = levels.replace_strict({0: False, 1: True}, default=None, return_dtype=pl.Boolean)
    elif dtype == pl.Date and source == pl.String:
        typed = levels.str.strip_chars().str.to_date(_ISO_DATE_FORMAT, strict=False)
    elif dtype == pl.Time and source == pl.String:
        typed = _parsed_text(levels, dtype, lambda text: text.str.to_time(strict=False))
    elif isinstance(dtype, pl.Datetime) and source == pl.String:
        datetime_dtype = dtype
        typed = _parsed_text(
            levels,
            dtype,
            lambda text: text.str.to_datetime(
                time_unit=datetime_dtype.time_unit,
                time_zone=datetime_dtype.time_zone,
                strict=False,
            ),
        )
    elif (_is_time_kind(dtype) and numeric_source) or (dtype.is_numeric() and source == pl.Boolean):
        # A number is a temporal type's physical count, and a Boolean is no number:
        # neither is a value as the analyst wrote it.
        typed = pl.Series(levels.name, [None] * len(levels), dtype=dtype)
    else:
        typed = levels.cast(dtype, strict=False)
    fraction = dtype.is_integer() and source.is_float()
    if typed.null_count() == levels.null_count() and not fraction:
        # Every level read as a value (a null level stays null): none misfits.
        return typed, None
    misfit = levels.is_not_null() & typed.is_null()
    if fraction:
        # Polars casts 1.5 to 1: a fraction is no value of an integer column.
        misfit = misfit | (levels.is_not_null() & (levels != levels.round())).fill_null(False)
    return typed, misfit


def _written(value: Any) -> tuple[type, Any]:
    """A configured level's identity as written: ``1``, ``"1"`` and ``True`` stay distinct."""
    return type(value), value


def typed_level_keys(
    levels: Sequence[Any],
    dtype: pl.DataType,
    *,
    subject: str,
    setting: str,
    noun: str = "level",
) -> list[str | None]:
    """The canonical key of each configured level of a column of *dtype*.

    The one path from a configured level to the key a column's values are
    matched on (:func:`rating_key_of`): a rating entry's key, a categorical
    banding rule's value and the scalar :func:`normalise_rating_key` all take
    it. Each level is read as it was written: levels are grouped by their
    Python type before Polars sees them, so a list holding ``"true"`` and ``0``
    is not first made all text. A level that is not a value of the column's
    type is refused, naming *subject*, the first such levels and the type,
    never matched as something else or left to Polars' own words. A Null
    column's keys are all null.
    """
    if _is_unsupported_factor_dtype(dtype):
        raise ConfigSettingError(
            f"{subject} reads a column of type {dtype}, whose values cannot be matched; "
            "cast it upstream to a scalar type.",
            setting=setting,
        )
    keys: list[str | None] = [None] * len(levels)
    by_type: dict[type, list[int]] = {}
    for index, level in enumerate(levels):
        if level is not None:
            by_type.setdefault(type(level), []).append(index)
    misfits: list[int] = []
    for indices in by_type.values():
        written = pl.Series("level", [levels[index] for index in indices])
        try:
            typed, misfit = _coerce_levels(written, dtype)
        except ConfigSettingError as exc:
            # A Duration level's own refusal (_duration_key_to_physical), placed.
            raise ConfigSettingError(
                f"{subject}: {exc}", setting=setting, fix=exc.fix, values=exc.values
            ) from exc
        if misfit is not None:
            misfits.extend(index for index, wrong in zip(indices, misfit, strict=True) if wrong)
        for index, key in zip(indices, _typed_keys(typed, dtype), strict=True):
            keys[index] = key
    if misfits:
        wrong = list({_written(levels[index]): levels[index] for index in sorted(misfits)}.values())
        shown = ", ".join(repr(value) for value in wrong[:_MISFIT_DISPLAY_CAP])
        if len(wrong) > _MISFIT_DISPLAY_CAP:
            shown += f" and {len(wrong) - _MISFIT_DISPLAY_CAP} more"
        one = len(wrong) == 1
        raise ConfigSettingError(
            f"{subject} has the {noun}{'' if one else 's'} {shown}, which "
            f"{'is not a value' if one else 'are not values'} of its column's type, {dtype}",
            setting=setting,
            fix=(
                f"Write each {noun} as a value the column holds, or change the column's "
                "type upstream."
            ),
            values=tuple(wrong[:_MISFIT_DISPLAY_CAP]),
        )
    return keys


def _typed_keys(typed: pl.Series, dtype: pl.DataType) -> list[str | None]:
    """The keys of values already of *dtype*, step for step as :func:`rating_key_of`.

    Each step is a native Series operation (a cast, a comparison), so a scalar
    costs no frame; a Time or Duration takes the expression's one string format.
    """
    if _key_is_text(dtype):
        return typed.cast(pl.String).to_list()
    if column_kind(dtype) == "float":
        whole = typed.cast(pl.Int64, strict=False)
        exact = (whole.cast(typed.dtype) == typed).to_list()
        return [
            whole_text if is_whole else text
            for whole_text, is_whole, text in zip(
                whole.cast(pl.String).to_list(), exact, typed.cast(pl.String).to_list(), strict=True
            )
        ]
    return typed.dt.to_string().to_list()


def normalise_rating_key(
    value: Any,
    dtype: pl.DataType,
) -> str | None:
    """Canonicalise one scalar through the runtime's exact typed key path.

    Supplying the originating dtype is mandatory at persistence and trace
    boundaries, where Python/JSON may otherwise widen a Float32 or erase
    categorical and temporal type information. A value that is not one of
    *dtype* is refused, as a table holding it is.
    """
    rating_dtype_descriptor(dtype)
    (key,) = typed_level_keys(
        [value], dtype, subject="A rating key", setting="tables", noun="value"
    )
    return key


def _key_is_text(dtype: pl.DataType) -> bool:
    """Whether a column of *dtype* is keyed by its plain text (:func:`rating_key_of`)."""
    return column_kind(dtype) != "float" and not (
        dtype == pl.Time or isinstance(dtype, pl.Duration)
    )


def rating_key_of(col: pl.Expr, dtype: pl.DataType) -> pl.Expr:
    """The canonical key of a column of *dtype*, the form every level is matched in.

    Applied to the frame side of every match (a rating lookup's join, a
    categorical banding's remap, the levels the editors offer); the configured
    side takes :func:`typed_level_keys`, whose agreement with this expression is
    pinned by ``tests/test_rating_key_agreement.py``.

    Float widths remain native, so a Float32 key is formatted in Float32.
    ``Decimal`` (and other exact) columns retain their declared representation
    in the final ``Utf8`` key; configured levels are read through that declared
    dtype first, so ``"25.5"`` and ``"25.50"`` share the same scale-2 key.
    """
    rating_dtype_descriptor(dtype)
    if column_kind(dtype) == "float":
        # A finite whole value inside the Int64 range keys as its digits: the
        # non-strict cast nulls NaN, infinity and anything out of range, truncates
        # a fraction, and the cast back equals the value only when it was whole.
        whole = col.cast(pl.Int64, strict=False)
        return (
            pl.when(whole.cast(dtype) == col).then(whole.cast(pl.Utf8)).otherwise(col.cast(pl.Utf8))
        )
    if not _key_is_text(dtype):
        return col.dt.to_string()
    return col.cast(pl.Utf8)


def _rating_key_expr(name: str, dtype: pl.DataType) -> pl.Expr:
    """:func:`rating_key_of` the column *name*, under its own name."""
    return rating_key_of(pl.col(name), dtype).alias(name)


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
    try:
        rating_dtype_descriptor(dtype)
    except ValueError:
        return True
    return False


def _validate_supported_factor_dtypes(
    original_dtypes: dict[str, pl.DataType],
    *,
    table_label: str,
) -> None:
    for factor, dtype in original_dtypes.items():
        if _is_unsupported_factor_dtype(dtype):
            raise ConfigSettingError(
                f"Rating table {table_label!r} factor {factor!r} has unsupported dtype {dtype}. "
                "Supported scalar dtypes are Int8, Int16, Int32, Int64, Int128, "
                "UInt8, UInt16, UInt32, UInt64, Float32, Float64, Boolean, String, "
                "Categorical, Enum, Date, Time, Datetime, Duration, Decimal, and Null. "
                "Cast this factor upstream to a supported scalar dtype.",
                setting="tables",
            )


def _apply_rating_miss_guard(
    lf: _Frame,
    factors: list[str],
    *,
    key_columns: list[str] | None = None,
    lookup_value_column: str = _LOOKUP_VAL,
    table_label: str,
    output_col: str,
    input_schema: pl.Schema | None = None,
) -> _Frame:
    """Report lookup misses as a row-local Python scan.

    A miss never stops the run: each batch with misses is logged at WARNING,
    naming the table, its miss count and the missing keys, and returned
    unchanged, its misses null. The scan always reads the key and lookup-value
    columns and refuses pushed input predicates, so neither a downstream
    projection nor a filter can prune the report; a pushed row limit bounds it
    to the rows computed.
    """

    resolved_key_columns = key_columns if key_columns is not None else factors
    if len(resolved_key_columns) != len(factors):
        raise ValueError("rating miss-guard key columns must align with public factors")

    def _check(frame: pl.DataFrame) -> pl.DataFrame:
        values = frame[lookup_value_column]
        miss_mask = values.is_null()
        miss_count = int(miss_mask.sum())
        if not miss_count:
            return frame
        missed = (
            frame.filter(miss_mask)
            .select(resolved_key_columns)
            .rename(dict(zip(resolved_key_columns, factors)))
            .unique(maintain_order=True)
        )
        logger.warning(
            "rating_table_lookup_misses",
            table=table_label,
            output_column=output_col,
            miss_count=miss_count,
            row_count=frame.height,
            distinct_missing_keys=missed.height,
            missing_keys=missed.head(_MISS_KEY_DISPLAY_CAP).to_dicts(),
        )
        return frame

    if isinstance(lf, pl.DataFrame):
        return _check(lf)
    from haute._polars_utils import row_local_python_scan

    guard_schema = input_schema if input_schema is not None else lf.collect_schema()
    return row_local_python_scan(
        lf,
        _check,
        schema=guard_schema,
        input_schema=guard_schema,
        generated_columns=(),
        required_input_columns=(*resolved_key_columns, lookup_value_column),
        input_predicates_allowed=False,
        elide_transform_when_unused=False,
    )


def _normalise_combine_operation(operation: object) -> str:
    """Return a validated rating combine operation."""
    normalised = str(operation or "multiply")
    if normalised not in _SUPPORTED_COMBINE_OPERATIONS:
        raise ConfigSettingError(
            f"Unsupported rating combine operation {normalised!r}; "
            f"expected one of {sorted(_SUPPORTED_COMBINE_OPERATIONS)!r}",
            setting="combined_outputs",
            values=(normalised,),
        )
    return normalised


def _rating_internal_column(occupied: set[str], stem: str) -> str:
    """Reserve a collision-free internal column name."""
    candidate = stem
    while candidate in occupied:
        candidate = f"_{candidate}"
    occupied.add(candidate)
    return candidate


@dataclass(frozen=True, slots=True)
class RatingTableLookup:
    """One rating table's lookup, built exactly as execution builds it.

    ``keyed_entries`` holds every authored entry's canonical key columns and
    value, in authored order (no two share a key a row can match); ``lookup``
    is what execution joins, its value under ``lookup_value_column``, empty for
    a table priced by its Default alone. ``default_value`` is the finite
    default every miss takes, or ``None``, in which case a miss stays null and
    execution reports it.
    """

    factors: tuple[str, ...]
    output_column: str
    original_dtypes: Mapping[str, pl.DataType]
    key_columns: tuple[str, ...]
    lookup_value_column: str
    keyed_entries: pl.DataFrame
    lookup: pl.DataFrame
    default_value: float | None

    def frame_key_exprs(self) -> list[pl.Expr]:
        """The canonical key of each factor of the frame a lookup reads."""
        return [
            _rating_key_expr(factor, self.original_dtypes[factor]).alias(key_column)
            for factor, key_column in zip(self.factors, self.key_columns, strict=True)
        ]


def _entry_with_rate(entry: Mapping[str, Any], output_col: str) -> dict[str, Any]:
    """*entry* with a rate held as text read as the number it is, by the one grammar for a
    typed number; text that is not one is refused, naming the table."""
    rate = entry.get("value")
    if not isinstance(rate, str):
        return dict(entry)
    number = setting_number(rate)
    if number is None:
        raise ConfigSettingError(
            f"Rating table for '{output_col}' has the rate {rate!r}, which is not a number",
            setting="tables",
            values=(rate,),
        )
    return {**entry, "value": number}


def rating_table_lookup(table: dict[str, Any], frame_schema: Any) -> RatingTableLookup | None:
    """Build *table*'s lookup against a frame of *frame_schema*, or ``None`` for a no-op table.

    Raises what :func:`_apply_rating_table` raises for the table: a malformed
    table, an absent factor, an unsupported factor dtype, a level its factor
    column cannot hold, two entries for one level, or a ``default_value`` that
    is not blank and not a finite number. ``None`` is a table with nothing to
    look up: no output column, or one :func:`_rating_table_gap` names, which
    execution refuses. Once any entry has a key, every entry is read for it: an
    entry without a ``value`` is a null value.
    """
    raw_factors = table.get("factors")
    raw_entries = table.get("entries")
    factors = [] if raw_factors is None else raw_factors
    entries = [] if raw_entries is None else raw_entries
    if not isinstance(factors, list):
        raise ConfigSettingError("rating table factors must be a list", setting="tables")
    if not isinstance(entries, list):
        raise ConfigSettingError("rating table entries must be a list", setting="tables")
    if entries and not factors:
        raise ConfigSettingError(
            "rating table entries require a non-empty factors list", setting="tables"
        )
    output_col: str = table.get("output_column", "")

    if not factors or not output_col:
        return None

    # Validate the declared input contract before every incomplete-table guard.
    # An entry-less lookup is still a no-op, but it must never hide a typo in a
    # required factor name.
    existing_cols = set(_schema_names(frame_schema))
    table_label = output_col
    for factor in factors:
        if factor not in existing_cols:
            raise RatingFactorMissingError(
                f"Rating table {table_label!r} requires input factor {factor!r}, "
                "but that column is absent from the input schema",
                table=table_label,
                factor=factor,
            )

    # A blank default is none: misses stay null and the miss guard reports
    # them. Any other default must be a finite number every miss takes;
    # ignoring one that is not would leave its misses empty unseen.
    default_val = rating_default_value(
        table.get("default_value"), where=f"Rating table {table_label!r}"
    )
    if _rating_table_gap(table) is not None:
        return None

    entry_cols: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            entry_cols.update(entry.keys())
    if entries and "value" not in entry_cols:
        raise ConfigSettingError(
            f"Rating table {table_label!r} has entries with no rate ('value')", setting="tables"
        )
    unwritten = [factor for factor in factors if entries and factor not in entry_cols]
    if unwritten:
        raise ConfigSettingError(
            f"Rating table {table_label!r} has entries with no level for factor {unwritten[0]!r}",
            setting="tables",
            values=(unwritten[0],),
        )

    original_dtypes = {factor: frame_schema[factor] for factor in factors}
    _validate_supported_factor_dtypes(
        original_dtypes,
        table_label=table_label,
    )

    # Build the lookup eagerly: rating tables are small configuration data,
    # and strict factor/value conversion should fail before the lazy input
    # plan is returned to a caller.  The frame reads every entry, so each key
    # the check above found in any entry is a column: an entry without a value
    # is a null value, refused below, wherever it sits in the table.
    # A rate held as text is read by the one grammar for a typed number, as the config
    # codec validated it (``1,000.5`` is a number), never by Polars' own cast.
    # A table with a Default and no entries has an empty lookup: every row
    # misses, and the Default prices it.
    # Levels stay the Python values written (typed_level_keys reads each as written);
    # only the rates are a frame.
    rated = [_entry_with_rate(entry, output_col) for entry in entries]
    lookup = pl.DataFrame(
        {"value": pl.Series("value", [entry.get("value") for entry in rated], strict=False)}
    ).with_columns(pl.col("value").cast(pl.Float64, strict=True))

    # Reject NaN/Inf in rating table entries — they corrupt pricing silently
    _bad_count = lookup.filter(pl.col("value").is_nan() | pl.col("value").is_infinite()).height
    if _bad_count:
        raise ConfigSettingError(
            f"Rating table for '{output_col}' contains {_bad_count} NaN or Inf entries",
            setting="tables",
        )
    # Reject null entry values too (3a.3): sidecar validation has always
    # required a value per entry, and a null rate would neutral-fill in
    # combined outputs exactly like a miss — but invisibly, because after
    # the join it is indistinguishable from one.  Rejecting nulls here
    # keeps the miss guard's "no matching entry" diagnosis always true.
    _null_count = lookup.get_column("value").null_count()
    if _null_count:
        raise ConfigSettingError(
            f"Rating table for '{output_col}' contains {_null_count} null entry "
            "value(s); every entry requires a finite numeric value",
            setting="tables",
        )

    # B15: only each factor's level and the rate are read, so stray keys an entry
    # holds (leftover UI state) never reach the frame.
    written = {factor: [entry.get(factor) for entry in entries] for factor in factors}

    occupied = existing_cols | entry_cols | {output_col}
    key_columns = [
        _rating_internal_column(occupied, f"__haute_rating_key_{index}__")
        for index, _factor in enumerate(factors)
    ]
    lookup_value_column = _rating_internal_column(occupied, _LOOKUP_VAL)

    # Each entry's level is read as a value of its factor column's type and
    # keyed as that column's values are (``typed_level_keys``), so JSON scalar
    # widening cannot change a key, and a level the column cannot hold is
    # refused naming the table, the factor and the level.
    keyed_entries = pl.DataFrame(
        [
            pl.Series(
                key_column,
                typed_level_keys(
                    written[factor],
                    original_dtypes[factor],
                    subject=f"Rating table {table_label!r} factor {factor!r}",
                    setting="tables",
                ),
                dtype=pl.String,
            )
            for factor, key_column in zip(factors, key_columns, strict=True)
        ]
        + [lookup.get_column("value")]
    )

    # READ-06: two entries for the same levels are refused, never resolved by
    # one of them. The check runs after strict originating-dtype coercion and
    # key generation, so representational aliases such as Float64 entry strings
    # "25.0" and "25.00" are one level, and a duplicate an upstream dtype change
    # creates is refused at that run. Without duplicates a left join cannot fan
    # out rows. An entry with a null key can match no row (a join never matches
    # null, and a Null factor column keys every entry null), so its key collides
    # with nothing; entries identical as written are still two entries for one
    # level. "value" is renamed to an internal name to avoid collision with
    # any input "value" column in the input frame (Bug #1/#2).
    as_written = Counter(
        tuple(_written(written[factor][row]) for factor in factors) for row in range(len(entries))
    )
    duplicated = keyed_entries.select(
        pl.struct(key_columns).is_duplicated()
        & pl.all_horizontal(pl.col(key_columns).is_not_null())
    ).to_series() | pl.Series(
        [
            as_written[tuple(_written(written[factor][row]) for factor in factors)] > 1
            for row in range(len(entries))
        ],
        dtype=pl.Boolean,
    )
    if duplicated.any():
        levels = [
            {factor: written[factor][row] for factor in factors}
            for row, is_duplicate in enumerate(duplicated)
            if is_duplicate
        ]
        raise RatingDuplicateEntriesError(table, levels)
    deduplicated = keyed_entries.rename({"value": lookup_value_column})
    return RatingTableLookup(
        factors=tuple(factors),
        output_column=output_col,
        original_dtypes=original_dtypes,
        key_columns=tuple(key_columns),
        lookup_value_column=lookup_value_column,
        keyed_entries=keyed_entries,
        lookup=deduplicated,
        default_value=default_val,
    )


def apply_rating_table_lookup(
    lf: _Frame,
    built: RatingTableLookup,
    *,
    frame_schema: Any,
    guard_misses: bool = True,
) -> _Frame:
    """Join *built* onto *lf* and write its output column, as execution does.

    A lookup without a default leaves its misses null, and the miss guard
    reports them. Without *guard_misses* it does not: a measurement reads the
    misses itself and needs no report of them.
    """
    key_columns = list(built.key_columns)
    lookup_value_column = built.lookup_value_column
    # Source factors stay untouched.  Temporary keys are collision-free and
    # removed after the lookup.
    lf = lf.with_columns(built.frame_key_exprs())

    # Left join.  Preserve the input row order explicitly because Polars
    # streaming joins may otherwise emit hash-partition order.
    lf = lf.join(built.lookup.lazy(), on=key_columns, how="left", maintain_order="left")

    # Miss guard: only when there is no default — a default fills every miss
    # below. It reports misses and never raises; its diagnostics relabel the
    # temporary keys with the public factor names.
    if built.default_value is None and guard_misses:
        joined_schema = pl.Schema(
            {
                **dict(zip(_schema_names(frame_schema), frame_schema.values(), strict=True)),
                **dict.fromkeys(key_columns, pl.String()),
                lookup_value_column: built.lookup.schema[lookup_value_column],
            }
        )
        lf = _apply_rating_miss_guard(
            lf,
            list(built.factors),
            key_columns=key_columns,
            lookup_value_column=lookup_value_column,
            table_label=built.output_column,
            output_col=built.output_column,
            input_schema=joined_schema,
        )

    # Rename value → output_column, apply default
    if built.default_value is not None:
        lf = lf.with_columns(
            pl.col(lookup_value_column).fill_null(built.default_value).alias(built.output_column),
        )
    else:
        lf = lf.with_columns(pl.col(lookup_value_column).alias(built.output_column))

    return lf.drop([*key_columns, lookup_value_column])


def _apply_rating_table(
    lf: _Frame,
    table: dict[str, Any],
    *,
    input_schema: Any | None = None,
) -> _Frame:
    """Apply a single rating table lookup via a Polars left join.

    *table* must contain ``factors`` (list of column names to join on),
    ``output_column``, ``entries`` (list of dicts with one key per factor
    plus a ``value`` key), and optionally ``default_value``.

    Both join sides are canonicalised with :func:`_rating_key_expr`, so
    int-like float keys (``25.0``) match string-keyed entries (``"25"``)
    deterministically.

    Miss policy: a miss never stops the run. A ``default_value`` fills every
    miss with no warning; without one the table output stays null (combined
    outputs fill the operation's neutral element) and every batch with
    misses is counted and logged at WARNING.

    A table with an output column produces it or is refused: one with no
    factors, or with no entries and no Default (:func:`_rating_table_gap`),
    is refused naming the table and what it lacks. A table with no output
    column passes the frame through.
    """
    frame_schema = input_schema if input_schema is not None else _frame_schema(lf)
    built = rating_table_lookup(table, frame_schema)
    if built is not None:
        return apply_rating_table_lookup(lf, built, frame_schema=frame_schema)
    output_col = str(table.get("output_column") or "")
    gap = _rating_table_gap(table)
    if output_col and gap is not None:
        lacks, fix = gap
        raise ConfigSettingError(
            f"Rating table {output_col!r} {lacks}", setting="tables", fix=fix, values=(output_col,)
        )
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
    nulls reach this point from a table without a default, whose misses the
    miss guard in ``_apply_rating_table`` has already counted and logged.
    """
    operation = _normalise_combine_operation(operation)
    seen_columns: set[str] = set()
    duplicate_columns: list[str] = []
    for column in columns:
        if column in seen_columns and column not in duplicate_columns:
            duplicate_columns.append(column)
        seen_columns.add(column)
    if duplicate_columns:
        raise ConfigSettingError(
            f"Rating output {output_col!r} cannot combine duplicate participant "
            f"column(s) {duplicate_columns!r}",
            setting="combined_outputs",
        )
    if output_col in seen_columns:
        raise ConfigSettingError(
            f"Rating output column {output_col!r} cannot overwrite a participant column",
            setting="combined_outputs",
        )
    if not columns:
        return lf
    if len(columns) == 1:
        expr = pl.col(columns[0])
        if operation not in {"min", "max"}:
            return lf.with_columns(expr.alias(output_col))
        return lf.with_columns(_rating_extrema_expr(expr, columns, operation, output_col))

    if operation == "add":
        # fill_null(0.0) for add: a miss left empty contributes nothing.
        expr = pl.col(columns[0]).fill_null(0.0)
        for c in columns[1:]:
            expr = expr + pl.col(c).fill_null(0.0)
    elif operation == "min":
        expr = pl.min_horizontal(*[pl.col(c) for c in columns])
    elif operation == "max":
        expr = pl.max_horizontal(*[pl.col(c) for c in columns])
    else:  # multiply (default)
        # fill_null(1.0) for multiply: a miss left empty has no effect (neutral element).
        expr = pl.col(columns[0]).fill_null(1.0)
        for c in columns[1:]:
            expr = expr * pl.col(c).fill_null(1.0)

    if operation in {"min", "max"}:
        expr = _rating_extrema_expr(expr, columns, operation, output_col)
    else:
        expr = expr.alias(output_col)
    return lf.with_columns(expr)


def _rating_extrema_expr(
    extrema: pl.Expr,
    columns: list[str],
    operation: str,
    output_col: str,
) -> pl.Expr:
    """Return an extrema expression that fails for rows with no value.

    The batch UDF is deliberately embedded in the output expression: it must
    run only when the lazy result materialises, and cannot be pruned as an
    unrelated validation side-column.
    """

    message = (
        f"Rating {operation} output {output_col!r} is undefined for a row "
        "where every participating value is null"
    )

    class _LazyRatingExtremaUndefinedError(RatingExtremaUndefinedError):
        """Carry public error fields through Polars' positional recreation."""

        def __init__(
            self,
            error_message: str | None = None,
            error_output_column: str | None = None,
            error_operation: str | None = None,
        ) -> None:
            error_message = error_message or message
            error_output_column = error_output_column or output_col
            error_operation = error_operation or operation
            super().__init__(
                error_message,
                output_column=error_output_column,
                operation=error_operation,
            )
            self.args = (error_message, error_output_column, error_operation)

    def _require_value(batch: pl.Series) -> pl.Series:
        values = batch.struct.unnest()
        undefined = values.select(pl.all_horizontal(pl.all().is_null())).to_series()
        if bool(undefined.any()):
            raise _LazyRatingExtremaUndefinedError(message, output_col, operation)
        return pl.Series("", [False] * len(batch), dtype=pl.Boolean)

    guard = pl.struct(columns).map_batches(
        _require_value,
        return_dtype=pl.Boolean,
        is_elementwise=True,
    )
    return pl.when(guard).then(pl.lit(None)).otherwise(extrema).alias(output_col)


def normalise_combined_outputs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return strictly validated canonical combined-output definitions."""
    raw_outputs = config.get("combined_outputs")
    if raw_outputs is None or raw_outputs == []:
        return []
    if not isinstance(raw_outputs, list):
        raise ConfigSettingError(
            "ratingStep combined_outputs must be a list", setting="combined_outputs"
        )

    outputs: list[dict[str, Any]] = []
    seen_output_cols: set[str] = {
        str(t.get("output_column", "") or "").strip()
        for t in config.get("tables", []) or []
        if str(t.get("output_column", "") or "").strip()
    }
    for idx, item in enumerate(raw_outputs):
        if not isinstance(item, dict):
            raise ConfigSettingError(
                f"ratingStep combined_outputs[{idx}] must be an object", setting="combined_outputs"
            )
        output_col = str(item.get("output_column", "") or "").strip()
        if not output_col:
            raise ConfigSettingError(
                f"ratingStep combined_outputs[{idx}] requires output_column",
                setting="combined_outputs",
            )
        if output_col in seen_output_cols:
            raise ConfigSettingError(
                f"ratingStep combined_outputs[{idx}].output_column {output_col!r} "
                "duplicates another rating output column",
                setting="combined_outputs",
            )
        seen_output_cols.add(output_col)
        operation = _normalise_combine_operation(item.get("operation", "multiply"))

        base_raw = item.get("base_value")
        if (
            base_raw is None
            or isinstance(base_raw, bool)
            or (isinstance(base_raw, str) and not base_raw.strip())
        ):
            raise ConfigSettingError(
                f"ratingStep combined_outputs[{idx}] requires base_value",
                setting="combined_outputs",
            )
        base_value = setting_number(base_raw)
        if base_value is None:
            finite = isinstance(base_raw, int | float)
            raise ConfigSettingError(
                f"ratingStep combined_outputs[{idx}].base_value must be "
                f"{'finite' if finite else 'numeric'}",
                setting="combined_outputs",
            )
        outputs.append(
            {
                "output_column": output_col,
                "operation": operation,
                "base_value": base_value,
            }
        )
    return outputs


def _combine_rating_output(
    lf: _Frame,
    columns: list[str],
    operation: str,
    output_col: str,
    base_value: float | None = None,
    input_schema: Any | None = None,
) -> _Frame:
    """Combine table outputs, optionally including a numeric base value."""
    if base_value is None:
        return _combine_rating_columns(lf, columns, operation, output_col)
    base_col = f"__haute_rating_base_{output_col}__"
    existing_cols = (
        set(_schema_names(input_schema))
        if input_schema is not None
        else set(_schema_names(_frame_schema(lf)))
    )
    while base_col in columns or base_col in existing_cols:
        base_col = f"_{base_col}"
    with_base = lf.with_columns(pl.lit(base_value, dtype=pl.Float64).alias(base_col))
    combined = _combine_rating_columns(with_base, [base_col, *columns], operation, output_col)
    return combined.drop(base_col)


def _rating_table_gap(table: dict[str, Any]) -> tuple[str, str] | None:
    """What *table* lacks to produce its output column, and how to supply it, or ``None``.

    The one answer to whether a table can price: it needs factors, and an
    entry or a Default (with no entries every row misses, and the Default
    prices it). Execution refuses a table with an output column that lacks
    either, rather than leave out a column the node's contract promises.
    """
    if not (table.get("factors") or []):
        return "has no factors", "Add the column the table looks up as a factor."
    output_col = str(table.get("output_column") or "")
    default = rating_default_value(table.get("default_value"), where=f"Rating table {output_col!r}")
    if not (table.get("entries") or []) and default is None:
        return (
            "has no entries and no Default",
            "Add an entry, or set On miss to Use default so its Default prices every row.",
        )
    return None


def _apply_rating_step_outputs(
    lf: _Frame,
    tables: list[dict[str, Any]],
    combined_outputs: list[dict[str, Any]],
) -> _Frame:
    """Apply rating tables and combined outputs to a frame."""
    if isinstance(lf, pl.DataFrame):
        lf = lf.lazy()

    # Keep this assertion at the execution boundary as well as the config
    # codec: internal callers can pass already-expanded table lists directly.
    validate_unique_rating_table_outputs(tables)

    # Resolve the frame schema once and thread it through every table so each
    # _apply_rating_table avoids re-running collect_schema() on a growing lazy
    # plan (O(N^2) -> O(N)).  Each materialised table adds its Float64 output
    # column to this local view for subsequent tables.
    schema: dict[str, Any] = dict(_frame_schema(lf))

    out_cols: list[str] = []
    for table in tables:
        lf = _apply_rating_table(lf, table, input_schema=schema)
        output_col = str(table.get("output_column") or "")
        # A table with no output column was never asked for a column; every
        # other table produced its column or was refused above.
        if output_col:
            out_cols.append(output_col)
            schema[output_col] = pl.Float64

    for combined in combined_outputs:
        lf = _combine_rating_output(
            lf,
            out_cols,
            combined["operation"],
            combined["output_column"],
            combined["base_value"],
            input_schema=schema,
        )
        if combined["base_value"] is not None or out_cols:
            schema[combined["output_column"]] = pl.Float64
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
            config_path,
            base_dir=Path(base_dir) if base_dir else None,
            node_type=NodeType.RATING_STEP,
        )

    return _apply_rating_step_outputs(
        lf,
        normalise_rating_tables(resolved_config),
        normalise_combined_outputs(resolved_config),
    )
