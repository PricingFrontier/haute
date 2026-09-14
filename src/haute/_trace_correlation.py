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

import ast
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import lru_cache
from typing import Any, NamedTuple

import polars as pl

from haute._edge_join import (
    build_edge_join_kwargs,
    edge_join_key_columns_by_role,
    resolve_edge_join_role_indices,
)
from haute._json_safe import (
    MAX_SAFE_INTEGER,
    non_finite_float_token,
)
from haute._json_safe import row_to_json_safe as _jsonify_row
from haute._logging import get_logger
from haute._python_syntax import StructuredSyntaxError, method_call_sites
from haute._types import GraphNode, NodeType
from haute.errors import ConfigError

logger = get_logger(component="trace_correlation")

#: Float comparison tolerances for the non-Polars trace edges.  The
#: relative tolerance absorbs the float noise of values that were carried
#: verbatim through the pipeline; the absolute floor lets a genuine
#: near-zero value still match exactly ``0.0`` (relative tolerance is
#: meaningless around zero).
_TRACE_REL_TOL = 1e-9
_TRACE_ABS_TOL = 1e-12


@dataclass
class SchemaDiff:
    """Column-level diff between a node's input and output."""

    columns_added: list[str]
    columns_removed: list[str]
    columns_modified: list[str]
    columns_passed: list[str]


@dataclass(slots=True)
class CorrelationWork:
    """Mutable counters for work performed during post-hoc correlation."""

    candidate_frames_considered: int = 0
    match_scans: int = 0
    rows_scanned: int = 0
    key_columns_scanned: int = 0
    comparison_cells: int = 0
    ambiguity_count: int = 0


class _RowMatchStatus(StrEnum):
    NO_MATCH = "no_match"
    UNIQUE_STRICT = "unique_strict"
    UNIQUE_RELAXED = "unique_relaxed"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED_DTYPE = "unsupported_dtype"


class _CandidateIndicesState(StrEnum):
    AVAILABLE = "available"
    TRUNCATED = "truncated"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class _RowMatchResult:
    status: _RowMatchStatus
    strict_key_columns: tuple[str, ...]
    effective_key_columns: tuple[str, ...]
    relaxation_reason: str | None
    candidate_count: int | None
    candidate_indices: tuple[int, ...]
    candidate_indices_state: _CandidateIndicesState
    dtypes: tuple[str, ...]
    reason_code: str | None = None
    omitted_key_columns: tuple[str, ...] = ()


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


def _trace_values_match(actual: Any, expected: Any) -> bool:
    """Compare scalar explainability values under the correlation truth table."""
    if actual is None or expected is None:
        return actual is None and expected is None
    if type(actual) is bool or type(expected) is bool:
        return type(actual) is bool and type(expected) is bool and actual == expected
    actual_non_finite = _value_non_finite_token(actual)
    expected_non_finite = _value_non_finite_token(expected)
    if actual_non_finite is not None or expected_non_finite is not None:
        return actual_non_finite is not None and actual_non_finite == expected_non_finite
    if isinstance(actual, int) and isinstance(expected, int):
        return actual == expected
    if isinstance(actual, int) and isinstance(expected, float):
        if abs(actual) > 2**53 or not math.isfinite(expected):
            return False
        return abs(actual - expected) <= max(
            _TRACE_ABS_TOL,
            _TRACE_REL_TOL * max(abs(actual), abs(expected)),
        )
    if isinstance(actual, float) and isinstance(expected, int):
        return _trace_values_match(expected, actual)
    if isinstance(actual, float) and isinstance(expected, float):
        if not math.isfinite(actual) or not math.isfinite(expected):
            return False
        return abs(actual - expected) <= max(
            _TRACE_ABS_TOL,
            _TRACE_REL_TOL * max(abs(actual), abs(expected)),
        )
    if isinstance(actual, int) and isinstance(expected, str):
        return (
            abs(actual) > MAX_SAFE_INTEGER
            and _CANONICAL_INTEGER_RE.fullmatch(expected) is not None
            and expected == str(actual)
        )
    if isinstance(expected, int) and isinstance(actual, str):
        return _trace_values_match(expected, actual)
    if isinstance(actual, Decimal) or isinstance(expected, Decimal):
        return (
            not isinstance(actual, float) and not isinstance(expected, float) and actual == expected
        )
    if isinstance(actual, datetime) or isinstance(expected, datetime):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (date, time, timedelta)) or isinstance(expected, (date, time, timedelta)):
        return type(actual) is type(expected) and actual == expected
    return type(actual) is type(expected) and actual == expected


# ---------------------------------------------------------------------------
# Schema diff
# ---------------------------------------------------------------------------


def _compute_schema_diff(
    input_row: dict[str, Any] | None,
    output_row: dict[str, Any],
    *,
    provenance_aliases: Mapping[str, str] | None = None,
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

    # Multi-parent trace assembly namespaces collisions as ``parent.column``.
    # A consuming node sees the unqualified column, so this representation
    # change is not a removal/addition in the node's schema.
    qualified_inputs: dict[str, list[str]] = {}
    for alias, base in (provenance_aliases or {}).items():
        if alias in in_cols:
            qualified_inputs.setdefault(base, []).append(alias)
    aliased_outputs = {
        column for column in out_cols if column in qualified_inputs and column not in in_cols
    }
    aliased_inputs = {
        column
        for base, columns in qualified_inputs.items()
        if base in out_cols
        for column in columns
    }
    added = sorted((out_cols - in_cols) - aliased_outputs)
    removed = sorted((in_cols - out_cols) - aliased_inputs)

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
    for col in sorted(aliased_outputs):
        input_values = [input_row[key] for key in qualified_inputs[col]]
        if any(
            value == output_row[col] or (_is_nan(value) and _is_nan(output_row[col]))
            for value in input_values
        ):
            passed.append(col)
        else:
            modified.append(col)

    return SchemaDiff(
        columns_added=added,
        columns_removed=removed,
        columns_modified=modified,
        columns_passed=passed,
    )


# ---------------------------------------------------------------------------
# Post-hoc row correlation
# ---------------------------------------------------------------------------


_CANONICAL_INTEGER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_INT_BOUNDS: dict[object, tuple[int, int]] = {
    pl.Int8: (-(2**7), 2**7 - 1),
    pl.Int16: (-(2**15), 2**15 - 1),
    pl.Int32: (-(2**31), 2**31 - 1),
    pl.Int64: (-(2**63), 2**63 - 1),
    pl.Int128: (-(2**127), 2**127 - 1),
    pl.UInt8: (0, 2**8 - 1),
    pl.UInt16: (0, 2**16 - 1),
    pl.UInt32: (0, 2**32 - 1),
    pl.UInt64: (0, 2**64 - 1),
}
_TIME_UNIT_NS = {"ms": 1_000_000, "us": 1_000, "ns": 1}
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _never_match_expr(column: str) -> pl.Expr:
    """Return a frame-height false expression tied to *column*."""
    return pl.col(column).is_null() & pl.lit(False)


def _finite_numeric_match_expr(column: str, expected: float) -> pl.Expr:
    actual = pl.col(column).cast(pl.Float64)
    delta = (actual - pl.lit(expected)).abs()
    magnitude = pl.max_horizontal(actual.abs(), pl.lit(abs(expected)))
    tolerance = pl.max_horizontal(
        pl.lit(_TRACE_ABS_TOL),
        pl.lit(_TRACE_REL_TOL) * magnitude,
    )
    return actual.is_finite() & (delta <= tolerance)


def _datetime_epoch_ns(value: datetime) -> tuple[int, bool] | None:
    aware = value.utcoffset() is not None
    normalised = value.astimezone(UTC).replace(tzinfo=None) if aware else value.replace(tzinfo=None)
    delta = normalised - datetime(1970, 1, 1)
    nanoseconds = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
    if not _INT64_MIN <= nanoseconds <= _INT64_MAX:
        return None
    return nanoseconds, aware


def _parse_duration(value: Any) -> timedelta | None:
    if isinstance(value, timedelta):
        return value
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"(?:(?P<days>-?[0-9]+) day(?:s)?, )?"
        r"(?P<hours>[0-9]+):(?P<minutes>[0-9]{2}):(?P<seconds>[0-9]{2})"
        r"(?:\.(?P<microseconds>[0-9]{1,6}))?",
        value,
    )
    if match is None:
        return None
    micros = (match.group("microseconds") or "0").ljust(6, "0")
    return timedelta(
        days=int(match.group("days") or 0),
        hours=int(match.group("hours")),
        minutes=int(match.group("minutes")),
        seconds=int(match.group("seconds")),
        microseconds=int(micros),
    )


def _typed_value_match_expr(
    column: str,
    value: Any,
    dtype: pl.DataType,
) -> tuple[pl.Expr | None, str | None]:
    """Build one exhaustive V1 comparison or return an unsupported reason."""
    never_match = _never_match_expr(column)
    base = dtype.base_type()

    if base is pl.Object:
        return None, "unsupported_dtype"
    if value is None:
        return pl.col(column).is_null(), None
    if base is pl.Null:
        return never_match, None

    non_finite = _value_non_finite_token(value)
    if non_finite is not None:
        if not dtype.is_float():
            return None, "incompatible_non_finite_dtype"
        if non_finite == "nan":
            return pl.col(column).is_nan(), None
        sign = 1 if non_finite == "inf" else -1
        return pl.col(column).is_infinite() & (pl.col(column) * sign > 0), None

    if base is pl.Boolean:
        if type(value) is not bool:
            return None, "boolean_is_not_numeric"
        return pl.col(column) == pl.lit(value), None
    if type(value) is bool:
        return None, "boolean_is_not_numeric"

    if dtype.is_integer():
        bounds = _INT_BOUNDS.get(dtype)
        if bounds is None:
            return None, "unsupported_integer_dtype"
        if isinstance(value, int):
            if not bounds[0] <= value <= bounds[1]:
                return None, "integer_range_mismatch"
            return pl.col(column) == pl.lit(value), None
        if isinstance(value, float):
            if not math.isfinite(value):
                return None, "incompatible_non_finite_dtype"
            within_safe_range = (pl.col(column) >= -(2**53)) & (pl.col(column) <= 2**53)
            return within_safe_range & _finite_numeric_match_expr(column, value), None
        if isinstance(value, str):
            if _CANONICAL_INTEGER_RE.fullmatch(value) is None:
                return never_match, None
            parsed = int(value)
            if abs(parsed) <= MAX_SAFE_INTEGER or not bounds[0] <= parsed <= bounds[1]:
                return never_match, None
            return pl.col(column) == pl.lit(parsed), None
        return None, "incompatible_integer_value"

    if dtype.is_float():
        if isinstance(value, int):
            if abs(value) > 2**53:
                return None, "unsafe_integer_float_comparison"
            return _finite_numeric_match_expr(column, float(value)), None
        if isinstance(value, float):
            if not math.isfinite(value):
                token = _float_non_finite_token(value)
                assert token is not None
                if token == "nan":
                    return pl.col(column).is_nan(), None
                sign = 1 if token == "inf" else -1
                return pl.col(column).is_infinite() & (pl.col(column) * sign > 0), None
            return _finite_numeric_match_expr(column, value), None
        return None, "incompatible_float_value"

    if base is pl.Decimal:
        if isinstance(value, float):
            return None, "decimal_float_unsupported"
        try:
            decimal_value = value if isinstance(value, Decimal) else Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            return None, "invalid_decimal_value"
        if not decimal_value.is_finite():
            return None, "invalid_decimal_value"
        return pl.col(column) == pl.lit(decimal_value), None

    if base in (pl.String, pl.Categorical, pl.Enum):
        if not isinstance(value, str):
            return None, "incompatible_string_value"
        expression = pl.col(column).cast(pl.String) if base is not pl.String else pl.col(column)
        return expression == pl.lit(value), None

    if base is pl.Binary:
        if not isinstance(value, (bytes, bytearray)):
            return None, "incompatible_binary_value"
        return pl.col(column) == pl.lit(bytes(value)), None

    if base is pl.Date:
        if isinstance(value, datetime):
            return None, "date_datetime_mismatch"
        try:
            date_value = value if isinstance(value, date) else date.fromisoformat(value)
        except (TypeError, ValueError):
            return None, "invalid_date_value"
        return pl.col(column) == pl.lit(date_value), None

    if base is pl.Time:
        try:
            time_value = value if isinstance(value, time) else time.fromisoformat(value)
        except (TypeError, ValueError):
            return None, "invalid_time_value"
        if time_value.utcoffset() is not None:
            return None, "timezone_time_unsupported"
        nanoseconds = (
            time_value.hour * 3_600 + time_value.minute * 60 + time_value.second
        ) * 1_000_000_000 + time_value.microsecond * 1_000
        return pl.col(column).cast(pl.Int64) == nanoseconds, None

    if isinstance(dtype, pl.Datetime):
        if isinstance(value, date) and not isinstance(value, datetime):
            return None, "date_datetime_mismatch"
        try:
            datetime_value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None, "invalid_datetime_value"
        normalised = _datetime_epoch_ns(datetime_value)
        if normalised is None:
            return None, "datetime_nanosecond_overflow"
        nanoseconds, aware = normalised
        if aware != (dtype.time_zone is not None):
            return None, "datetime_timezone_mismatch"
        factor = _TIME_UNIT_NS[dtype.time_unit]
        if nanoseconds % factor:
            return never_match, None
        return pl.col(column).cast(pl.Int64) == nanoseconds // factor, None

    if isinstance(dtype, pl.Duration):
        duration = _parse_duration(value)
        if duration is None:
            return None, "invalid_duration_value"
        nanoseconds = (
            duration.days * 86_400 + duration.seconds
        ) * 1_000_000_000 + duration.microseconds * 1_000
        if not _INT64_MIN <= nanoseconds <= _INT64_MAX:
            return None, "duration_nanosecond_overflow"
        factor = _TIME_UNIT_NS[dtype.time_unit]
        if nanoseconds % factor:
            return never_match, None
        return pl.col(column).cast(pl.Int64) == nanoseconds // factor, None

    if dtype.is_nested():
        if not isinstance(value, (list, tuple, dict)):
            return None, "incompatible_nested_value"
        try:
            literal = pl.lit(value, dtype=dtype)
        except (TypeError, ValueError, pl.exceptions.PolarsError):
            return None, "incompatible_nested_schema"
        return pl.col(column) == literal, None

    return None, "unsupported_dtype"


def _candidate_payload(
    index_frame: pl.DataFrame,
) -> tuple[int, tuple[int, ...], _CandidateIndicesState]:
    ordered = index_frame.sort("__trace_row_index")
    count = ordered.height
    indices = tuple(
        int(value) for value in ordered.get_column("__trace_row_index").head(16).to_list()
    )
    state = _CandidateIndicesState.AVAILABLE if count <= 16 else _CandidateIndicesState.TRUNCATED
    return count, indices, state


def _unsupported_match_result(
    key_columns: tuple[str, ...],
    dtypes: tuple[str, ...],
    reason_code: str,
) -> _RowMatchResult:
    return _RowMatchResult(
        status=_RowMatchStatus.UNSUPPORTED_DTYPE,
        strict_key_columns=key_columns,
        effective_key_columns=(),
        relaxation_reason=None,
        candidate_count=None,
        candidate_indices=(),
        candidate_indices_state=_CandidateIndicesState.UNAVAILABLE,
        dtypes=dtypes,
        reason_code=reason_code,
    )


def _match_rows_vectorized(
    frame: pl.DataFrame,
    row_values: Mapping[str, Any],
    key_columns: Sequence[str],
    *,
    allow_relaxed: bool = False,
    work: CorrelationWork | None = None,
) -> _RowMatchResult:
    """Match row identity with native Polars expressions and bounded output."""
    keys = tuple(
        column for column in key_columns if column in frame.columns and column in row_values
    )
    if work is not None:
        work.match_scans += 1
        work.rows_scanned += frame.height
        work.key_columns_scanned += len(keys)
        work.comparison_cells += frame.height * len(keys)
    dtypes = tuple(str(frame.schema[column]) for column in keys)
    if not keys:
        return _RowMatchResult(
            status=_RowMatchStatus.NO_MATCH,
            strict_key_columns=(),
            effective_key_columns=(),
            relaxation_reason=None,
            candidate_count=0,
            candidate_indices=(),
            candidate_indices_state=_CandidateIndicesState.AVAILABLE,
            dtypes=(),
        )

    expressions: list[pl.Expr] = []
    for column in keys:
        expression, reason = _typed_value_match_expr(
            column,
            row_values[column],
            frame.schema[column],
        )
        if expression is None:
            return _unsupported_match_result(keys, dtypes, reason or "unsupported_dtype")
        expressions.append(expression.fill_null(False))

    indexed = frame.with_row_index("__trace_row_index")
    strict_predicate = pl.all_horizontal(expressions)
    try:
        strict_indices = (
            indexed.select(
                pl.col("__trace_row_index"),
                strict_predicate.alias("__trace_matches"),
            )
            .filter(pl.col("__trace_matches"))
            .select("__trace_row_index")
        )
    except pl.exceptions.PolarsError:
        return _unsupported_match_result(keys, dtypes, "incompatible_nested_schema")
    strict_count, strict_candidates, strict_state = _candidate_payload(strict_indices)
    if strict_count:
        status = _RowMatchStatus.UNIQUE_STRICT if strict_count == 1 else _RowMatchStatus.AMBIGUOUS
        if status is _RowMatchStatus.AMBIGUOUS and work is not None:
            work.ambiguity_count += 1
        return _RowMatchResult(
            status=status,
            strict_key_columns=keys,
            effective_key_columns=keys,
            relaxation_reason=None,
            candidate_count=strict_count,
            candidate_indices=strict_candidates,
            candidate_indices_state=strict_state,
            dtypes=dtypes,
        )

    # A float cannot safely distinguish integer values outside ±2**53.
    # A safe candidate may still win above, but when no candidate exists
    # and the compared column contains such values the result is typed as
    # unsupported rather than pretending to be a trustworthy no-match.
    for column in keys:
        expected = row_values[column]
        if frame.schema[column].is_integer() and isinstance(expected, float):
            has_unsafe = frame.select(
                ((pl.col(column) < -(2**53)) | (pl.col(column) > 2**53)).any().alias("unsafe")
            ).item()
            if bool(has_unsafe):
                return _unsupported_match_result(
                    keys,
                    dtypes,
                    "unsafe_integer_float_comparison",
                )

    if not allow_relaxed or len(keys) < 2 or frame.height == 0:
        return _RowMatchResult(
            status=_RowMatchStatus.NO_MATCH,
            strict_key_columns=keys,
            effective_key_columns=keys,
            relaxation_reason=None,
            candidate_count=0,
            candidate_indices=(),
            candidate_indices_state=_CandidateIndicesState.AVAILABLE,
            dtypes=dtypes,
        )

    aliases = [f"__trace_key_match_{index}" for index in range(len(keys))]
    scored = indexed.select(
        pl.col("__trace_row_index"),
        *[expression.alias(alias) for expression, alias in zip(expressions, aliases, strict=True)],
    ).with_columns(
        pl.sum_horizontal(pl.col(alias).cast(pl.UInt16) for alias in aliases).alias(
            "__trace_match_width"
        )
    )
    best_width = scored.get_column("__trace_match_width").max()
    if not isinstance(best_width, int) or best_width <= 0:
        return _RowMatchResult(
            status=_RowMatchStatus.NO_MATCH,
            strict_key_columns=keys,
            effective_key_columns=keys,
            relaxation_reason=None,
            candidate_count=0,
            candidate_indices=(),
            candidate_indices_state=_CandidateIndicesState.AVAILABLE,
            dtypes=dtypes,
        )
    best = scored.filter(pl.col("__trace_match_width") == best_width)
    relaxed_count, relaxed_candidates, relaxed_state = _candidate_payload(
        best.select("__trace_row_index")
    )
    effective = tuple(
        key for key, alias in zip(keys, aliases, strict=True) if bool(best.get_column(alias).any())
    )
    omitted = tuple(
        key
        for key, alias in zip(keys, aliases, strict=True)
        if not bool(best.get_column(alias).all())
    )
    status = _RowMatchStatus.UNIQUE_RELAXED if relaxed_count == 1 else _RowMatchStatus.AMBIGUOUS
    if status is _RowMatchStatus.AMBIGUOUS and work is not None:
        work.ambiguity_count += 1
    return _RowMatchResult(
        status=status,
        strict_key_columns=keys,
        effective_key_columns=effective,
        relaxation_reason="strict_keys_no_match_best_subset",
        candidate_count=relaxed_count,
        candidate_indices=relaxed_candidates,
        candidate_indices_state=relaxed_state,
        dtypes=dtypes,
        omitted_key_columns=omitted,
    )


def _record_ambiguous_row_match(
    diagnostics: list[dict[str, Any]] | None,
    *,
    reason: str,
    node_id: str | None,
    child_node_id: str | None,
    match_strategy: str,
    match_columns: list[str],
    ignored_columns: list[str],
    matched_row_indices: list[int],
    candidate_count: int | None = None,
    candidate_indices_state: _CandidateIndicesState = _CandidateIndicesState.AVAILABLE,
) -> None:
    """Surface an ambiguous correlation match instead of selecting row zero."""
    node_label = "parent row" if node_id is None else f"node {node_id!r}"
    child_label = f" for child node {child_node_id!r}" if child_node_id is not None else ""
    column_label = ", ".join(match_columns) if match_columns else "(none)"
    exact_count = len(matched_row_indices) if candidate_count is None else candidate_count
    message = (
        f"Row correlation for {node_label}{child_label} is ambiguous: "
        f"{exact_count} {match_strategy} matches on columns {column_label}."
    )
    diagnostic = {
        "code": "ambiguous_row_match",
        "severity": "warning",
        "reason": reason,
        "message": message,
        "node_id": node_id,
        "child_node_id": child_node_id,
        "match_strategy": match_strategy,
        "match_columns": list(match_columns),
        "ignored_columns": list(ignored_columns),
        "matched_row_count": exact_count,
        "matched_row_indices": list(matched_row_indices),
        "candidate_count": exact_count,
        "candidate_indices": list(matched_row_indices),
        "candidate_indices_state": candidate_indices_state.value,
    }
    logger.warning(
        "trace_row_match_ambiguous",
        reason=reason,
        node_id=node_id,
        child_node_id=child_node_id,
        match_strategy=match_strategy,
        match_columns=match_columns,
        ignored_columns=ignored_columns,
        matched_row_count=exact_count,
        matched_row_indices=matched_row_indices,
    )
    if diagnostics is not None:
        diagnostics.append(diagnostic)


def _find_matching_row(
    df: pl.DataFrame,
    child_row: dict[str, Any],
    *,
    diagnostics: list[dict[str, Any]] | None = None,
    node_id: str | None = None,
    child_node_id: str | None = None,
    allow_relaxed: bool = True,
    work: CorrelationWork | None = None,
) -> tuple[dict[str, Any] | None, int]:
    """Find the row in *df* that matches *child_row* on shared columns.

    Returns ``(row_dict, positional_index)`` — the row dict is already
    run through ``_jsonify_row``.  Returns ``(None, -1)`` when no match
    can be found — callers must handle the unresolved case rather than
    silently showing incorrect data.

    Strategy:
      1. Try matching on ALL shared columns.
      2. If no match, score each row by how many shared columns match.
         The highest score is the widest relaxed subset that could match
         that row, so this preserves the previous "most-specific relaxed
         match wins" behavior without enumerating every subset.
         Competing best rows are ambiguous and no row is selected.
      3. If still no match, return None (fail loudly).
    """
    shared = [column for column in child_row if column in df.columns]
    match = _match_rows_vectorized(
        df,
        child_row,
        shared,
        allow_relaxed=allow_relaxed,
        work=work,
    )
    if match.status in {_RowMatchStatus.UNIQUE_STRICT, _RowMatchStatus.UNIQUE_RELAXED}:
        idx = match.candidate_indices[0]
        if match.status is _RowMatchStatus.UNIQUE_RELAXED and diagnostics is not None:
            effective = list(match.effective_key_columns)
            omitted = list(match.omitted_key_columns)
            diagnostics.append(
                {
                    "code": "low_confidence_relaxed_match",
                    "severity": "warning",
                    "reason": match.relaxation_reason,
                    "message": (
                        f"Row correlation for node {node_id!r} used a relaxed "
                        f"match on columns {effective}; omitted columns {omitted}."
                    ),
                    "node_id": node_id,
                    "child_node_id": child_node_id,
                    "match_strategy": "relaxed",
                    "match_columns": effective,
                    "ignored_columns": omitted,
                    "matched_row_count": match.candidate_count,
                    "matched_row_indices": list(match.candidate_indices),
                    "strict_key_columns": list(match.strict_key_columns),
                    "effective_key_columns": effective,
                    "omitted_key_columns": omitted,
                    "candidate_count": match.candidate_count,
                    "candidate_indices": list(match.candidate_indices),
                    "candidate_indices_state": match.candidate_indices_state.value,
                }
            )
        return _jsonify_row(df.row(idx, named=True)), idx

    if match.status is _RowMatchStatus.AMBIGUOUS:
        relaxed = match.relaxation_reason is not None
        _record_ambiguous_row_match(
            diagnostics,
            reason="relaxed_match_ambiguous" if relaxed else "duplicate_exact_match",
            node_id=node_id,
            child_node_id=child_node_id,
            match_strategy="relaxed" if relaxed else "exact",
            match_columns=list(match.effective_key_columns),
            ignored_columns=list(match.omitted_key_columns),
            matched_row_indices=list(match.candidate_indices),
            candidate_count=match.candidate_count,
            candidate_indices_state=match.candidate_indices_state,
        )
        return None, -1

    if match.status is _RowMatchStatus.UNSUPPORTED_DTYPE:
        if diagnostics is not None:
            key_columns = list(match.strict_key_columns[:16])
            diagnostics.append(
                {
                    "code": "unsupported_dtype",
                    "severity": "warning",
                    "reason": match.reason_code,
                    "message": (
                        f"Row correlation for node {node_id!r} could not compare "
                        f"the selected key columns {key_columns}."
                    ),
                    "node_id": node_id,
                    "child_node_id": child_node_id,
                    "match_strategy": "typed",
                    "match_columns": key_columns,
                    "ignored_columns": [],
                    "matched_row_count": 0,
                    "matched_row_indices": [],
                    "key_columns": key_columns,
                    "dtypes": list(match.dtypes[:16]),
                    "candidate_count": None,
                    "candidate_indices": [],
                    "candidate_indices_state": _CandidateIndicesState.UNAVAILABLE.value,
                }
            )
        return None, -1

    # No match found — return None so the caller can mark the step
    # as unresolved rather than silently showing wrong data.
    logger.warning(
        "trace_row_match_failed",
        shared_cols_tried=len(shared),
        df_rows=len(df),
        relaxed_matching=allow_relaxed,
    )
    if diagnostics is not None:
        diagnostics.append(
            {
                "code": "row_match_not_found",
                "severity": "warning",
                "reason": "no_matching_row",
                "message": (
                    f"Row correlation for node {node_id!r} found no matching "
                    f"parent row on columns {shared}."
                ),
                "node_id": node_id,
                "child_node_id": child_node_id,
                "match_strategy": "relaxed" if allow_relaxed else "exact",
                "match_columns": list(shared),
                "ignored_columns": [],
                "matched_row_count": 0,
                "matched_row_indices": [],
            }
        )
    return None, -1


#: Exact called-method names whose semantics can reorder rows or change row
#: identity, so positional alignment cannot be trusted without shared columns.
#: Structured call discovery deliberately excludes lookalikes in comments,
#: strings, longer attribute names, and bare attribute references.
_ROW_REORDERING_METHODS = frozenset(
    {
        "bottom_k",
        "cross_join",
        "explode",
        "gather",
        "group_by",
        "groupby",
        "join",
        "pivot",
        "reverse",
        "sample",
        "shuffle",
        "sort",
        "sort_by",
        "take",
        "top_k",
        "unique",
    }
)


@lru_cache(maxsize=512)
def _code_may_reorder_rows(code: str) -> bool:
    """Classify one valid Python fragment through exact structured call sites."""

    try:
        calls = method_call_sites(code.lstrip("\ufeff"))
    except StructuredSyntaxError:
        # Correlation is an observation surface. If syntax cannot be proved,
        # leave the step unresolved rather than revive a substring guess.
        return True
    return any(call.name in _ROW_REORDERING_METHODS for call in calls)


def _child_transform_may_reorder(child_node: GraphNode | None) -> bool:
    """Whether the child's transform can reorder rows / change row identity.

    Used to decide whether a positional alignment can be trusted when
    there are NO shared columns to verify it against.  Order-preserving
    1:1 ops (rename, with_columns, select) keep row identity, so the
    position is correct; sorts, joins, group-bys, gathers, etc. can
    reorder, so a positional guess would misattribute lineage.  When the
    code cannot be inspected we conservatively assume it MAY reorder —
    failing loud (the step is left unresolved) rather than guessing.
    """
    if child_node is None:
        return True
    config = getattr(child_node.data, "config", None)
    code = config.get("code", "") if isinstance(config, dict) else ""
    if not isinstance(code, str) or not code:
        return True
    return _code_may_reorder_rows(code)


def _allows_relaxed_parent_match(
    child_node: GraphNode | None,
    target_role: str | None,
) -> bool:
    """Edge-join right parents must not relax a miss into false lineage."""
    if child_node is None or child_node.data.nodeType != NodeType.EDGE_JOIN:
        return True
    return target_role != "join"


class EdgeJoinRoleEdge(NamedTuple):
    """One physical incoming edge of an Edge Join, identified by its endpoint."""

    source_id: str
    source_handle: str | None
    target_handle: str | None


class EdgeJoinRoleEdges(NamedTuple):
    """The one BASE and one JOIN physical edge of an Edge Join."""

    base: EdgeJoinRoleEdge
    join: EdgeJoinRoleEdge


def edge_join_role_edges(
    child_node: GraphNode,
    edge_metadata: Mapping[tuple[str, str], Sequence[tuple[str | None, str | None]]] | None,
) -> EdgeJoinRoleEdges:
    """Return the one physical BASE and JOIN edge for an Edge Join.

    Roles are properties of physical incoming edges, never persisted node
    configuration.  Keeping the source handle here is essential when the
    same multi-frame source feeds both ports.  The role rule itself is
    :func:`haute._edge_join.resolve_edge_join_role_indices`; a violation is
    re-raised as ``ValueError`` so correlation callers keep one error type.
    """
    physical = [
        EdgeJoinRoleEdge(source_id, source_handle, target_handle)
        for (source_id, target_id), edges in (edge_metadata or {}).items()
        if target_id == child_node.id
        for source_handle, target_handle in edges
    ]
    try:
        base_index, join_index = resolve_edge_join_role_indices(
            [edge.target_handle for edge in physical]
        )
    except ConfigError as exc:
        raise ValueError(
            f"edge-join node {child_node.id!r} requires exactly one physical 'base' "
            f"edge and one physical 'join' edge; found {physical!r}"
        ) from exc
    return EdgeJoinRoleEdges(base=physical[base_index], join=physical[join_index])


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


def _edge_join_base_columns(
    *,
    child_node: GraphNode,
    base_id: str,
    base_handle: str | None,
    eager_outputs: Mapping[str, Any],
) -> set[str]:
    """Resolve the columns emitted by an edge-join's base input."""
    base_output = eager_outputs.get(base_id)
    if isinstance(base_output, pl.DataFrame):
        return set(base_output.columns)
    if isinstance(base_output, dict):
        selected_frame = base_output.get(base_handle) if isinstance(base_handle, str) else None
        if isinstance(selected_frame, pl.DataFrame):
            return set(selected_frame.columns)
        raise ValueError(
            f"edge-join node {child_node.id!r} cannot correlate join parent because "
            f"multi-frame base parent {base_id!r} has no usable sourceHandle; "
            f"base edge sourceHandle={base_handle!r}, available frames={sorted(base_output)!r}"
        )
    raise ValueError(
        f"edge-join node {child_node.id!r} has no materialized DataFrame output "
        f"for its base parent {base_id!r}; cannot correlate the join parent"
    )


def _build_parent_match_row(
    child_row: dict[str, Any],
    parent_id: str,
    parent_cols: set[str],
    child_node: GraphNode | None,
    eager_outputs: Mapping[str, Any],
    edge_metadata: Mapping[tuple[str, str], Sequence[tuple[str | None, str | None]]] | None = None,
    target_role: str | None = None,
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
        base_id, base_handle, _base_target_handle = edge_join_role_edges(
            child_node, edge_metadata
        ).base
        if target_role == "join":
            base_columns = _edge_join_base_columns(
                child_node=child_node,
                base_id=base_id,
                base_handle=base_handle,
                eager_outputs=eager_outputs,
            )
            return _edge_join_right_match_row(
                child_row,
                parent_cols,
                base_columns,
                config,
            )
        if target_role != "base":
            raise ValueError(
                f"node {parent_id!r} is wired as a parent of edge-join "
                f"{child_node.id!r} but its edge role is {target_role!r}"
            )
    return {c: v for c, v in child_row.items() if c in parent_cols}


def _match_parent_row(
    parent_df: pl.DataFrame,
    *,
    parent_id: str,
    child_row: dict[str, Any],
    child_row_idx: int,
    child_len: int,
    child_id: str,
    node_map: Mapping[str, GraphNode],
    eager_outputs: Mapping[str, Any],
    edge_metadata: Mapping[tuple[str, str], Sequence[tuple[str | None, str | None]]] | None,
    target_role: str | None = None,
    diagnostics: list[dict[str, Any]] | None,
    work: CorrelationWork | None = None,
) -> tuple[dict[str, Any] | None, int, int]:
    """Correlate one parent FRAME against the resolved child row.

    Returns ``(row_dict, positional_index, match_width)`` where
    ``match_width`` is the number of child columns projected onto this
    frame for matching — the specificity a multi-frame resolver uses to
    rank competing candidate frames.  ``(None, -1, width)`` when no row
    can be confidently identified.
    """
    if work is not None:
        work.candidate_frames_considered += 1

    parent_cols = set(parent_df.columns)
    child_node = node_map.get(child_id)
    match_row = _build_parent_match_row(
        child_row,
        parent_id,
        parent_cols,
        child_node,
        eager_outputs,
        edge_metadata,
        target_role,
    )

    # Fast path: same row count → likely 1:1 (with_columns, rename, select).
    # Trust the positionally-aligned parent row only when it can be
    # verified. With shared columns, an order-preserving transform only
    # needs the positional row to match; a transform that may reorder
    # additionally requires a unique full-parent match at that physical
    # index. With NO shared columns to verify against, position is
    # trustworthy only when the child transform provably preserves row
    # order (rename/with_columns/select) or there is a single candidate
    # row; a reordering transform (sort/join/gather/…) falls through
    # and the step is left unresolved rather than attached to the wrong
    # parent row.
    if len(parent_df) == child_len and child_row_idx < len(parent_df):
        shared = [column for column in match_row if column in parent_df.columns]
        child_may_reorder = _child_transform_may_reorder(child_node)
        if shared:
            verification_frame = (
                parent_df if child_may_reorder else parent_df.slice(child_row_idx, 1)
            )
            positional_match = _match_rows_vectorized(
                verification_frame,
                match_row,
                shared,
                work=work,
            )
            expected_index = child_row_idx if child_may_reorder else 0
            if (
                positional_match.status is _RowMatchStatus.UNIQUE_STRICT
                and positional_match.candidate_indices == (expected_index,)
            ):
                return (
                    _jsonify_row(parent_df.row(child_row_idx, named=True)),
                    child_row_idx,
                    len(match_row),
                )
        elif len(parent_df) == 1 or not child_may_reorder:
            return (
                _jsonify_row(parent_df.row(child_row_idx, named=True)),
                child_row_idx,
                len(match_row),
            )

    # Value matching: find the parent row that matches the child row
    row_dict, idx = _find_matching_row(
        parent_df,
        match_row,
        diagnostics=diagnostics,
        node_id=parent_id,
        child_node_id=child_id,
        allow_relaxed=_allows_relaxed_parent_match(child_node, target_role),
        work=work,
    )
    return row_dict, idx, len(match_row)


class _FrameCandidate(NamedTuple):
    """A non-empty frame of a multi-frame parent reachable through one edge."""

    handle: str
    target_role: str | None
    frame: pl.DataFrame


class _FrameMatch(NamedTuple):
    """A candidate frame that confidently identified a parent row."""

    candidate: _FrameCandidate
    row: dict[str, Any]
    row_index: int
    width: int


def _resolve_multi_frame_parent(
    frames: dict[str, pl.DataFrame],
    *,
    parent_id: str,
    child_id: str,
    edges: Sequence[tuple[str | None, str | None]] | None,
    traced_column: str | None,
    child_row: dict[str, Any],
    child_row_idx: int,
    child_len: int,
    node_map: Mapping[str, GraphNode],
    eager_outputs: Mapping[str, Any],
    edge_metadata: Mapping[tuple[str, str], Sequence[tuple[str | None, str | None]]] | None,
    diagnostics: list[dict[str, Any]] | None,
    work: CorrelationWork | None = None,
) -> tuple[dict[str, Any] | None, int]:
    """Correlate a multi-frame parent (``dict[label, DataFrame]``) row.

    *edges* carries ONE ``(sourceHandle, targetHandle)`` pair per physical
    edge between parent and child (a
    multi-frame source can feed the same child through several edges,
    each consuming a distinct frame), so resolution is per-edge: every
    candidate frame is matched against the resolved child row, and only
    an actual, confident row match makes a frame the correlated one.
    When several frames match, the frame carrying the traced column is
    preferred, then the most specific match (widest projected-column
    set). A surviving tie remains unresolved and is recorded as a
    diagnostic rather than guessing between frames.
    """
    source_handles = {handle for handle, _target_role in edges or () if handle is not None}
    candidates: list[_FrameCandidate] = []
    for handle, target_role in edges or ():
        if handle is None:
            continue
        frame = frames.get(handle)
        if frame is not None and len(frame) > 0:
            candidates.append(_FrameCandidate(handle, target_role, frame))

    if not candidates:
        if diagnostics is not None:
            diagnostics.append(
                {
                    "code": "unresolved_source_frame",
                    "severity": "warning",
                    "reason": "source_frame_unavailable",
                    "message": (
                        f"Row correlation for multi-frame node {parent_id!r} "
                        f"could not resolve a source frame for child {child_id!r}."
                    ),
                    "node_id": parent_id,
                    "child_node_id": child_id,
                    "match_strategy": "source_frame",
                    "match_columns": [],
                    "ignored_columns": [],
                    "matched_row_count": 0,
                    "matched_row_indices": [],
                    "source_handles": sorted(source_handles),
                    "frames": sorted(frames.keys()),
                }
            )
        return None, -1

    if len(candidates) == 1:
        row_dict, idx, _ = _match_parent_row(
            candidates[0].frame,
            parent_id=parent_id,
            child_row=child_row,
            child_row_idx=child_row_idx,
            child_len=child_len,
            child_id=child_id,
            node_map=node_map,
            eager_outputs=eager_outputs,
            edge_metadata=edge_metadata,
            target_role=candidates[0].target_role,
            diagnostics=diagnostics,
            work=work,
        )
        return row_dict, idx

    # Several edges consume distinct frames of this parent — match each
    # candidate frame independently (suppressing per-candidate ambiguity
    # noise) and keep the frames that confidently identify a row.
    matches: list[_FrameMatch] = []
    for candidate in candidates:
        row_dict, idx, width = _match_parent_row(
            candidate.frame,
            parent_id=parent_id,
            child_row=child_row,
            child_row_idx=child_row_idx,
            child_len=child_len,
            child_id=child_id,
            node_map=node_map,
            eager_outputs=eager_outputs,
            edge_metadata=edge_metadata,
            target_role=candidate.target_role,
            diagnostics=None,
            work=work,
        )
        if row_dict is not None:
            matches.append(_FrameMatch(candidate, row_dict, idx, width))

    if not matches:
        if diagnostics is not None:
            diagnostics.append(
                {
                    "code": "unresolved_source_frame",
                    "severity": "warning",
                    "reason": "source_frame_row_not_correlated",
                    "message": (
                        f"Row correlation for multi-frame node {parent_id!r} "
                        f"found no source frame matching child {child_id!r}."
                    ),
                    "node_id": parent_id,
                    "child_node_id": child_id,
                    "match_strategy": "source_frame",
                    "match_columns": [],
                    "ignored_columns": [],
                    "matched_row_count": 0,
                    "matched_row_indices": [],
                    "source_handles": [candidate.handle for candidate in candidates],
                    "frames": sorted(frames.keys()),
                }
            )
        return None, -1

    picked = matches
    if traced_column is not None:
        with_column = [m for m in picked if traced_column in m.candidate.frame.columns]
        if with_column:
            picked = with_column
    if len(picked) > 1:
        best_width = max(m.width for m in picked)
        picked = [m for m in picked if m.width == best_width]
    if len(picked) > 1:
        if work is not None:
            work.ambiguity_count += 1
        if diagnostics is not None:
            diagnostics.append(
                {
                    "code": "ambiguous_source_frame",
                    "severity": "warning",
                    "reason": "multiple_source_frames_matched",
                    "message": (
                        f"Row correlation for multi-frame node {parent_id!r} for child "
                        f"{child_id!r} matched several frames "
                        f"({[m.candidate.handle for m in picked]}); no frame was selected."
                    ),
                    "node_id": parent_id,
                    "child_node_id": child_id,
                    "match_strategy": "source_frame",
                    "match_columns": [],
                    "ignored_columns": [],
                    "matched_row_count": len(picked),
                    "matched_row_indices": [],
                    "candidates": [m.candidate.handle for m in picked],
                }
            )
        return None, -1
    return picked[0].row, picked[0].row_index


def _ensure_unresolved_diagnostic(
    diagnostics: list[dict[str, Any]] | None,
    *,
    diagnostic_start: int,
    node_id: str,
    child_node_id: str,
) -> int:
    """Return the diagnostic linked to a failed correlation attempt.

    Every omitted node must point at concrete evidence. Match helpers normally
    append that evidence themselves; this final guard supplies a stable generic
    diagnostic if a future matching path returns ``None`` without doing so.
    """
    if diagnostics is None:
        return -1
    for index in range(diagnostic_start, len(diagnostics)):
        diagnostic = diagnostics[index]
        if diagnostic.get("node_id") == node_id:
            return index
    diagnostics.append(
        {
            "code": "row_correlation_failed",
            "severity": "warning",
            "reason": "row_correlation_failed",
            "message": (
                f"Row correlation for node {node_id!r} could not establish "
                f"the parent row for child {child_node_id!r}."
            ),
            "node_id": node_id,
            "child_node_id": child_node_id,
            "match_strategy": "unknown",
            "match_columns": [],
            "ignored_columns": [],
            "matched_row_count": 0,
            "matched_row_indices": [],
        }
    )
    return len(diagnostics) - 1


def _correlate_rows_posthoc(
    eager_outputs: dict[str, Any],
    order: list[str],
    parents_of: dict[str, list[str]],
    target_node_id: str,
    row_index: int,
    *,
    node_map: Mapping[str, GraphNode],
    diagnostics: list[dict[str, Any]] | None = None,
    unresolved: dict[str, tuple[str, int]] | None = None,
    edge_metadata: Mapping[tuple[str, str], Sequence[tuple[str | None, str | None]]] | None = None,
    traced_column: str | None = None,
    work: CorrelationWork | None = None,
    row_scope: RowScopeResolver | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Extract the correct row from each node using post-hoc correlation.

    Uses the preview-cached DataFrames directly — no re-execution, no
    injected columns.  Walks backward from the target node and matches
    each parent's row by shared column values with the already-resolved
    child row.  *node_map* supplies node type and config so that
    edge-join children can route suffixed/colliding columns to the
    correct parent (see :func:`_build_parent_match_row`).

    *edge_metadata* maps a (source, target) node pair to the
    ``(sourceHandle, targetHandle)`` of EVERY physical edge between them,
    in edge order — the
    per-edge frame selection ``_pick_source_frame`` makes at execution
    time for multi-frame sources.  *traced_column* (the column the user
    is tracing, if any) disambiguates when several frames of one source
    feed the same child and more than one matches the child row.

    *row_scope* (limited-preview traces) routes every parent that no head
    frame proves to contain its child's lineage to a row-scoped lookup of the
    parent's uncapped plan instead of its materialised frame.

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

        if row_scope is not None:
            resolved_children = [
                cid
                for cid in children_of.get(nid, [])
                if cid in result and result[cid] and row_scope.reads_parent(nid, cid)
            ]
            if not resolved_children:
                result[nid] = None
                row_indices[nid] = -1
                continue
            head_children = [cid for cid in resolved_children if row_scope.prefers_child(nid, cid)]
            scoped_child_id = (head_children or resolved_children)[0]
            scoped_child_frame = eager_outputs.get(scoped_child_id)
            diagnostic_start = len(diagnostics) if diagnostics is not None else 0
            scoped_row, scoped_idx = row_scope.resolve(
                parent_id=nid,
                child_id=scoped_child_id,
                child_row=result[scoped_child_id] or {},
                child_row_idx=row_indices.get(scoped_child_id, -1),
                child_len=(
                    len(scoped_child_frame) if isinstance(scoped_child_frame, pl.DataFrame) else -1
                ),
                diagnostics=diagnostics,
                work=work,
                traced_column=traced_column,
            )
            result[nid] = scoped_row
            row_indices[nid] = scoped_idx
            if scoped_row is None and unresolved is not None:
                diagnostic_index = _ensure_unresolved_diagnostic(
                    diagnostics,
                    diagnostic_start=diagnostic_start,
                    node_id=nid,
                    child_node_id=scoped_child_id,
                )
                diagnostic = diagnostics[diagnostic_index] if diagnostics is not None else {}
                unresolved[nid] = (
                    str(
                        diagnostic.get("reason")
                        or diagnostic.get("code")
                        or "row_correlation_failed"
                    ),
                    diagnostic_index,
                )
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
        if child_row is None:
            result[nid] = None
            row_indices[nid] = -1
            continue
        child_row_idx = row_indices.get(resolved_child_id, 0)
        child_df = eager_outputs.get(resolved_child_id)
        child_len = len(child_df) if child_df is not None else 0

        # Multi-frame sources store dict[label, DataFrame]. The edges'
        # sourceHandles name the frame each child EDGE consumes (the same
        # per-edge selection _pick_source_frame makes at execution time)
        # — a single child may consume several frames of one source
        # through distinct edges, so the frame is resolved against the
        # child row rather than assumed unique per (source, target) pair.
        if isinstance(parent_df, dict):
            diagnostic_start = len(diagnostics) if diagnostics is not None else 0
            row_dict, idx = _resolve_multi_frame_parent(
                parent_df,
                parent_id=nid,
                child_id=resolved_child_id,
                edges=(edge_metadata or {}).get((nid, resolved_child_id)),
                traced_column=traced_column,
                child_row=child_row,
                child_row_idx=child_row_idx,
                child_len=child_len,
                node_map=node_map,
                eager_outputs=eager_outputs,
                edge_metadata=edge_metadata,
                diagnostics=diagnostics,
                work=work,
            )
            result[nid] = row_dict  # may be None if no frame resolved
            row_indices[nid] = idx
            if row_dict is None and unresolved is not None:
                diagnostic_index = _ensure_unresolved_diagnostic(
                    diagnostics,
                    diagnostic_start=diagnostic_start,
                    node_id=nid,
                    child_node_id=resolved_child_id,
                )
                diagnostic = diagnostics[diagnostic_index] if diagnostics is not None else {}
                unresolved[nid] = (
                    str(
                        diagnostic.get("reason")
                        or diagnostic.get("code")
                        or "row_correlation_failed"
                    ),
                    diagnostic_index,
                )
            continue

        # Build a filtered child_row for matching: only include columns
        # that exist in this parent's DataFrame, and — when the child is
        # an edge-join — route suffixed/colliding columns to the parent
        # they actually came from.  This prevents columns brought in by
        # a *different* parent (via a join) from confusing the value
        # matcher.  (Both steps live in _match_parent_row.)
        diagnostic_start = len(diagnostics) if diagnostics is not None else 0
        edge_roles = (edge_metadata or {}).get((nid, resolved_child_id), ())
        target_role = edge_roles[0][1] if len(edge_roles) == 1 else None
        row_dict, idx, _ = _match_parent_row(
            parent_df,
            parent_id=nid,
            child_row=child_row,
            child_row_idx=child_row_idx,
            child_len=child_len,
            child_id=resolved_child_id,
            node_map=node_map,
            eager_outputs=eager_outputs,
            edge_metadata=edge_metadata,
            target_role=target_role,
            diagnostics=diagnostics,
            work=work,
        )
        result[nid] = row_dict  # may be None if no match found
        row_indices[nid] = idx
        if row_dict is None and unresolved is not None:
            diagnostic_index = _ensure_unresolved_diagnostic(
                diagnostics,
                diagnostic_start=diagnostic_start,
                node_id=nid,
                child_node_id=resolved_child_id,
            )
            diagnostic = diagnostics[diagnostic_index] if diagnostics is not None else {}
            unresolved[nid] = (
                str(diagnostic.get("reason") or diagnostic.get("code") or "row_correlation_failed"),
                diagnostic_index,
            )

    return result


# ---------------------------------------------------------------------------
# Limited-preview lineage frames
# ---------------------------------------------------------------------------
#
# A trace reads the rows the limited preview shows. A parent reached from a
# head-framed child through an order-aligned edge contains that child's lineage
# in its own first rows, so the existing positional and value correlation runs
# over its head frame. Every other parent is looked up in its uncapped plan by
# the values its child carried through unchanged.

_LEFT_ORDER_PRESERVING_JOINS = frozenset({"left", "left_right"})
_ROW_PRESERVING_BUILDER_TYPES = frozenset(
    {NodeType.MODEL_SCORE, NodeType.BANDING, NodeType.RATING_STEP, NodeType.SCENARIO_EXPANDER}
)
_PASS_THROUGH_TRACE_TYPES = frozenset(
    {
        NodeType.LIVE_SWITCH,
        NodeType.DATA_OUTPUT,
        NodeType.MODELLING,
        NodeType.SUBMODEL,
        NodeType.SUBMODEL_PORT,
    }
)
_CODE_FREE_PASS_THROUGH_TYPES = frozenset({NodeType.EXPLORE, NodeType.EXTERNAL_FILE})
_ROW_DROPPING_METHODS = frozenset({"filter", "drop_nulls"})
_ROW_SCOPE_CANDIDATE_LIMIT = 2
# A key probe reading more candidate rows than this falls back to the full filter.
_ROW_SCOPE_PROBE_LIMIT = 1_000
# Edge Join strategies whose output rows for a base row depend only on that row's
# values, and all of whose rows come from a base row.
_ROW_TRANSFER_JOIN_STRATEGIES = frozenset({"left", "inner", "semi", "anti", "cross"})


@dataclass(frozen=True, slots=True)
class TraceEdgeAlignment:
    """Whether a child emits each input row at least once, in input order.

    ``read`` is false for a port the child never reads; such a port is not row
    lineage and trace does not correlate it.
    """

    aligned: bool
    prefix_cap: int | None = None
    read: bool = True


_NOT_ALIGNED = TraceEdgeAlignment(False)
_ALIGNED = TraceEdgeAlignment(True)


def _node_code(node: GraphNode) -> str:
    code = node.data.config.get("code")
    return code.strip() if isinstance(code, str) else ""


def _single_head_limit(code: str, input_names: Sequence[str]) -> int | None:
    """Return ``k`` when the whole program is ``df = <input>.head(k)`` or ``.limit(k)``."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    statements = [
        statement
        for statement in tree.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    ]
    if len(statements) != 1 or not isinstance(statements[0], ast.Assign):
        return None
    assignment = statements[0]
    call = assignment.value
    if (
        len(assignment.targets) != 1
        or not isinstance(assignment.targets[0], ast.Name)
        or assignment.targets[0].id != "df"
        or not isinstance(call, ast.Call)
        or not isinstance(call.func, ast.Attribute)
        or call.func.attr not in {"head", "limit"}
        or not isinstance(call.func.value, ast.Name)
        or call.func.value.id not in {*input_names, "df"}
        or call.keywords
        or len(call.args) != 1
        or not isinstance(call.args[0], ast.Constant)
        or type(call.args[0].value) is not int
    ):
        return None
    return call.args[0].value


def _code_edge_alignment(
    code: str,
    input_names: Sequence[str],
    selector_aliases: frozenset[str] = frozenset(),
) -> TraceEdgeAlignment:
    limit = _single_head_limit(code, input_names)
    if limit is not None:
        return TraceEdgeAlignment(True, limit) if limit >= 1 else _NOT_ALIGNED
    from haute.chunking import classify_chunk_local_polars_code

    if not classify_chunk_local_polars_code(
        code, frame_names=input_names, selector_aliases=selector_aliases
    ).eligible:
        return _NOT_ALIGNED
    try:
        calls = method_call_sites(code.lstrip("﻿"))
    except StructuredSyntaxError:
        return _NOT_ALIGNED
    if any(call.name in _ROW_DROPPING_METHODS for call in calls):
        return _NOT_ALIGNED
    return _ALIGNED


def trace_edge_alignment(
    child: GraphNode,
    *,
    target_role: str | None,
    edge_input: str | None,
    input_names: Sequence[str],
    selector_aliases: frozenset[str] = frozenset(),
    input_aliases: Mapping[str, str] | None = None,
) -> TraceEdgeAlignment:
    """Classify one incoming edge of *child* for limited-preview tracing.

    *input_names* are the child's edge input names; *input_aliases* adds the
    logical names its code may use for them (``inputMapping``).
    """
    node_type = child.data.nodeType
    code = _node_code(child)
    if node_type is NodeType.EDGE_JOIN:
        if target_role != "base":
            return _NOT_ALIGNED
        kwargs = build_edge_join_kwargs(child.data.config)
        return TraceEdgeAlignment(
            kwargs["how"] == "left" and kwargs.get("maintain_order") in _LEFT_ORDER_PRESERVING_JOINS
        )
    if node_type in _ROW_PRESERVING_BUILDER_TYPES:
        return _NOT_ALIGNED if code else _ALIGNED
    if node_type in _PASS_THROUGH_TRACE_TYPES:
        return _ALIGNED
    if node_type in _CODE_FREE_PASS_THROUGH_TYPES and not code:
        return _ALIGNED
    if node_type is NodeType.OPTIMISER:
        data_input = child.data.config.get("data_input")
        selected = len(input_names) == 1 or (bool(data_input) and data_input == edge_input)
        return TraceEdgeAlignment(selected)
    if node_type is NodeType.OPTIMISER_APPLY:
        # Ratebook apply left-joins factor tables onto its selected input in
        # order; online apply collapses each quote's scenarios into one row.
        # The apply reads one input: its ``ratebook_input`` in ratebook mode and
        # its first input in online mode. ``optimiser_mode`` is the artifact's
        # resolved mode; while it is unknown, both candidates count as read.
        config = child.data.config
        mode = config.get("optimiser_mode")
        ratebook_input = config.get("ratebook_input")
        selected = len(input_names) == 1 or (bool(ratebook_input) and ratebook_input == edge_input)
        if len(input_names) <= 1:
            read = True
        elif mode == "ratebook":
            read = selected
        elif mode == "online":
            read = edge_input == input_names[0]
        else:
            read = edge_input in {input_names[0], ratebook_input}
        return TraceEdgeAlignment(mode == "ratebook" and selected, read=read)
    if node_type in (NodeType.POLARS, NodeType.EXPLORE) and code and len(input_names) == 1:
        return _code_edge_alignment(code, (*input_names, *(input_aliases or {})), selector_aliases)
    return _NOT_ALIGNED


TraceEdgeKey = tuple[str, str, str | None, str | None]
"""``(parent, child, source_handle, target_handle)`` of one physical edge."""


def trace_head_prefixes(
    order: Sequence[str],
    alignments: Mapping[TraceEdgeKey, TraceEdgeAlignment],
    *,
    target_node_id: str,
    row_limit: int,
) -> dict[str, int]:
    """Return each head-framed node's prefix length.

    The target's prefix is ``row_limit``. A parent is head-framed when an
    aligned edge reaches it from a head-framed child; its prefix is the largest
    child requirement, each reduced to ``k`` through a ``head(k)`` program, so
    no head frame reads rows the preview did not compute.
    """
    prefixes = {target_node_id: row_limit}
    for node_id in reversed(order):
        if node_id == target_node_id:
            continue
        requirements = [
            prefixes[child_id]
            if alignment.prefix_cap is None
            else min(prefixes[child_id], alignment.prefix_cap)
            for (parent_id, child_id, _handle, _role), alignment in alignments.items()
            if parent_id == node_id and alignment.aligned and child_id in prefixes
        ]
        if requirements:
            prefixes[node_id] = max(requirements)
    return prefixes


PlanProvider = Callable[[], Mapping[str, "pl.LazyFrame | Mapping[str, pl.LazyFrame]"]]


@dataclass
class RowScopeResolver:
    """Correlate every parent of a limited-preview trace, one physical edge at a time.

    A port reached over an aligned edge from a child whose row came from its
    own head frame is matched in the parent's head frame. Every other port is
    looked up in the parent's uncapped plan by the values the child carries
    through unchanged. A parent whose row comes from a lookup is no longer
    head-resolved, so its own parents are looked up too.
    """

    node_map: Mapping[str, GraphNode]
    prefixes: Mapping[str, int]
    alignments: Mapping[TraceEdgeKey, TraceEdgeAlignment]
    edge_metadata: Mapping[tuple[str, str], Sequence[tuple[str | None, str | None]]]
    input_names: Mapping[TraceEdgeKey, str]
    child_input_names: Mapping[str, tuple[str, ...]]
    plans: PlanProvider
    frames: dict[str, Any]
    head_resolved: set[str]
    execution_context: Any = None
    lookups: dict[tuple[str, str | None, str], pl.DataFrame] = field(default_factory=dict)
    selector_aliases: frozenset[str] = frozenset()
    child_input_aliases: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    _join_key_columns: frozenset[str] | None = field(default=None, init=False, repr=False)
    _schemas: dict[tuple[str, str | None], pl.Schema] = field(
        default_factory=dict, init=False, repr=False
    )
    _unique_rows: dict[str, pl.DataFrame] = field(default_factory=dict, init=False, repr=False)

    def _head_edge(
        self,
        parent_id: str,
        child_id: str,
        source_handle: str | None,
        target_handle: str | None,
    ) -> bool:
        alignment = self.alignments.get((parent_id, child_id, source_handle, target_handle))
        return (
            alignment is not None
            and alignment.aligned
            and child_id in self.head_resolved
            and parent_id in self.prefixes
        )

    def _reads_edge(
        self,
        parent_id: str,
        child_id: str,
        source_handle: str | None,
        target_handle: str | None,
    ) -> bool:
        alignment = self.alignments.get((parent_id, child_id, source_handle, target_handle))
        return alignment is None or alignment.read

    def reads_parent(self, parent_id: str, child_id: str) -> bool:
        """Whether *child_id* reads any port *parent_id* feeds it."""
        return any(
            self._reads_edge(parent_id, child_id, handle, role)
            for handle, role in self.edge_metadata.get((parent_id, child_id), ())
        )

    def prefers_child(self, parent_id: str, child_id: str) -> bool:
        """Whether some edge from *parent_id* to *child_id* can use head frames."""
        return any(
            self._head_edge(parent_id, child_id, handle, role)
            for handle, role in self.edge_metadata.get((parent_id, child_id), ())
        )

    def _head_frame(self, parent_id: str, source_handle: str | None) -> pl.DataFrame | None:
        frame = self.frames.get(parent_id)
        if isinstance(frame, dict):
            frame = frame.get(source_handle) if source_handle is not None else None
        return frame if isinstance(frame, pl.DataFrame) else None

    def plan_for(self, node_id: str, source_handle: str | None) -> pl.LazyFrame | None:
        plan = self.plans().get(node_id)
        if isinstance(plan, Mapping):
            return plan.get(source_handle) if source_handle is not None else None
        return plan

    def schema_for(self, node_id: str, source_handle: str | None) -> pl.Schema | None:
        """Return a lineage plan's schema, read at most once per request."""
        key = (node_id, source_handle)
        schema = self._schemas.get(key)
        if schema is None:
            plan = self.plan_for(node_id, source_handle)
            if plan is None:
                return None
            schema = plan.collect_schema()
            self._schemas[key] = schema
        return schema

    def record_unique_row(self, node_id: str, frame: pl.DataFrame) -> None:
        """Record *frame* as *node_id*'s only row in its uncapped plan."""
        if frame.height != 1:
            raise ValueError(f"a unique row frame has one row, got {frame.height}")
        self._unique_rows[node_id] = frame

    def _transferred_parent_frame(
        self,
        *,
        parent_id: str,
        child_id: str,
        source_handle: str | None,
        target_role: str | None,
    ) -> pl.DataFrame | None:
        """Return the parent row a unique child row proves, or ``None``.

        When the child derives its rows from each parent row's values alone and
        carries every parent column unchanged, two identical parent rows would
        yield two identical matching child rows; a unique child row therefore
        proves exactly one parent row, which is the child row restricted to the
        parent's columns.
        """
        child_frame = self._unique_rows.get(child_id)
        if child_frame is None or source_handle is not None:
            return None
        child = self.node_map[child_id]
        if child.data.config.get("column_renames"):
            return None
        node_type = child.data.nodeType
        if node_type is NodeType.EDGE_JOIN:
            if target_role != "base":
                return None
            if build_edge_join_kwargs(child.data.config)["how"] not in (
                _ROW_TRANSFER_JOIN_STRATEGIES
            ):
                return None
        elif node_type in _PASS_THROUGH_TRACE_TYPES:
            # A pass-through node returns one of its inputs; only a sole traced
            # input is the one its rows come from.
            lineage_inputs = [
                key
                for key, alignment in self.alignments.items()
                if key[1] == child_id and alignment.read
            ]
            if len(lineage_inputs) != 1:
                return None
        else:
            return None
        parent_schema = self.schema_for(parent_id, None)
        if parent_schema is None:
            return None
        child_schema = child_frame.schema
        if any(child_schema.get(name) != dtype for name, dtype in parent_schema.items()):
            return None
        return child_frame.select(parent_schema.names())

    def join_key_columns(self) -> frozenset[str]:
        """Every column keying an Edge Join (either role) on the traced lineage."""
        if self._join_key_columns is None:
            columns: set[str] = set()
            lineage_children = {child_id for _parent, child_id, _handle, _role in self.alignments}
            for child_id in sorted(lineage_children):
                node = self.node_map[child_id]
                if node.data.nodeType is NodeType.EDGE_JOIN:
                    base_keys, join_keys = edge_join_key_columns_by_role(node.data.config)
                    columns.update(base_keys, join_keys)
            self._join_key_columns = frozenset(columns)
        return self._join_key_columns

    def lookup(
        self,
        node_id: str,
        source_handle: str | None,
        values: Mapping[str, Any],
    ) -> pl.DataFrame | None:
        """Read up to two rows of a node's uncapped plan matching *values*.

        Two rows decide uniqueness. Filtering on every carried column makes
        Polars decode each of them across the whole input, so a lookup that
        carries a non-null join key first reads the rows matching its keys and
        matches the rest in memory, falling back to the full filter when that
        probe reaches ``_ROW_SCOPE_PROBE_LIMIT`` rows.
        """
        from haute._polars_utils import streaming_collect

        plan = self.plan_for(node_id, source_handle)
        schema = self.schema_for(node_id, source_handle)
        if plan is None or schema is None:
            return None
        expressions: list[pl.Expr] = []
        probe_expressions: list[pl.Expr] = []
        join_keys = self.join_key_columns()
        for column, value in values.items():
            if column not in schema:
                return None
            expression, _reason = _typed_value_match_expr(column, value, schema[column])
            if expression is None:
                return None
            # A filter already drops a row whose comparison is null; null-filling
            # the comparison would stop Parquet statistics from pruning row groups.
            expressions.append(expression)
            if column in join_keys and value is not None:
                probe_expressions.append(expression)
        if not expressions:
            return None
        key = (node_id, source_handle, repr(sorted(values.items(), key=lambda item: item[0])))
        cached = self.lookups.get(key)
        if cached is not None:
            return cached
        matches_every_value = pl.all_horizontal(expressions)
        if probe_expressions:
            candidates = streaming_collect(
                plan.filter(pl.all_horizontal(probe_expressions)).head(_ROW_SCOPE_PROBE_LIMIT + 1),
                execution_context=self.execution_context,
            )
            if candidates.height <= _ROW_SCOPE_PROBE_LIMIT:
                cached = candidates.filter(matches_every_value).head(_ROW_SCOPE_CANDIDATE_LIMIT)
        if cached is None:
            cached = streaming_collect(
                plan.filter(matches_every_value).head(_ROW_SCOPE_CANDIDATE_LIMIT),
                execution_context=self.execution_context,
            )
        self.lookups[key] = cached
        return cached

    def _carried_values(
        self,
        *,
        parent_id: str,
        child_id: str,
        source_handle: str | None,
        target_role: str | None,
        child_row: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        child = self.node_map[child_id]
        node_type = child.data.nodeType
        code = _node_code(child)
        parent_schema = self.schema_for(parent_id, source_handle)
        if parent_schema is None:
            return None
        parent_columns = set(parent_schema.names())
        shared = {name: value for name, value in child_row.items() if name in parent_columns}
        if node_type is NodeType.EDGE_JOIN:
            if target_role == "base":
                return shared
            if target_role != "join":
                return None
            base = edge_join_role_edges(child, self.edge_metadata).base
            base_schema = self.schema_for(base.source_id, base.source_handle)
            if base_schema is None:
                return None
            return _edge_join_right_match_row(
                dict(child_row),
                parent_columns,
                set(base_schema.names()),
                child.data.config,
            )
        if node_type in _PASS_THROUGH_TRACE_TYPES or node_type in (
            NodeType.OPTIMISER,
            NodeType.OUTPUT,
        ):
            return shared
        if node_type in _CODE_FREE_PASS_THROUGH_TYPES and not code:
            return shared
        carried = shared
        if node_type in _ROW_PRESERVING_BUILDER_TYPES or node_type is NodeType.OPTIMISER_APPLY:
            from haute.projection import projection_contract

            builder = child
            if code:
                config = {key: value for key, value in child.data.config.items() if key != "code"}
                builder = child.model_copy(
                    update={"data": child.data.model_copy(update={"config": config})}
                )
            produced, _referenced = projection_contract(builder).to_tuple()
            if produced is None:
                return None
            carried = {name: value for name, value in carried.items() if name not in produced}
        elif node_type not in (NodeType.POLARS, NodeType.EXPLORE, NodeType.EXTERNAL_FILE):
            return None
        if not code:
            return carried
        from haute._column_lineage import carried_column_proof

        input_names = self.child_input_names.get(child_id, ())
        explicit_inputs = node_type in (NodeType.POLARS, NodeType.EXTERNAL_FILE)
        proof = carried_column_proof(
            code,
            input_names if explicit_inputs else ("df",),
            **self._code_input_schemas(child_id, node_type, explicit_inputs=explicit_inputs),
            selector_aliases=self.selector_aliases,
            input_aliases=self.child_input_aliases.get(child_id) if explicit_inputs else None,
        )
        if proof is None:
            return None
        carried = {
            name: value
            for name, value in carried.items()
            if name not in proof.assigned
            and (proof.carried_only is None or name in proof.carried_only)
        }
        edge_input = self.input_names.get((parent_id, child_id, source_handle, target_role))
        if len(input_names) > 1 and proof.root_input != edge_input:
            # A column several inputs share keeps the root input's value (or the
            # value of a key this input was joined on); it identifies no other
            # parent's row.
            input_join_keys = proof.join_keys.get(edge_input or "", frozenset())
            other_columns: set[str] = set()
            for (other_parent, other_child), other_edges in self.edge_metadata.items():
                if other_child != child_id:
                    continue
                for other_handle, _role in other_edges:
                    if other_parent == parent_id and other_handle == source_handle:
                        continue
                    other_schema = self.schema_for(other_parent, other_handle)
                    if other_schema is not None:
                        other_columns.update(other_schema.names())
            # A non-root input's null may be an outer fill rather than its value.
            carried = {
                name: value
                for name, value in carried.items()
                if value is not None and (name not in other_columns or name in input_join_keys)
            }
        return carried

    def _code_input_schemas(
        self,
        child_id: str,
        node_type: NodeType,
        *,
        explicit_inputs: bool,
    ) -> dict[str, Any]:
        """Return the proof's input column names and dtypes for *child_id*'s code.

        Explicit inputs are read from each parent port's plan; Explore code's
        ``df`` is its single parent. Builder post-code runs on the builder's own
        output, whose schema the plans do not hold, so it gets none.
        """
        if not explicit_inputs and node_type is not NodeType.EXPLORE:
            return {}
        columns: dict[str, list[str]] = {}
        dtypes: dict[str, dict[str, pl.DataType]] = {}
        for (parent_id, other_child), edges in self.edge_metadata.items():
            if other_child != child_id:
                continue
            for handle, role in edges:
                name = (
                    self.input_names.get((parent_id, child_id, handle, role))
                    if explicit_inputs
                    else "df"
                )
                schema = self.schema_for(parent_id, handle)
                if name is None or schema is None:
                    return {}
                columns[name] = schema.names()
                dtypes[name] = dict(schema)
        return {"input_columns": columns, "input_dtypes": dtypes}

    def resolve(
        self,
        *,
        parent_id: str,
        child_id: str,
        child_row: Mapping[str, Any],
        child_row_idx: int,
        child_len: int,
        diagnostics: list[dict[str, Any]] | None,
        work: CorrelationWork | None,
        traced_column: str | None = None,
    ) -> tuple[dict[str, Any] | None, int]:
        edges = tuple(
            (handle, role)
            for handle, role in self.edge_metadata.get((parent_id, child_id), ())
            if self._reads_edge(parent_id, child_id, handle, role)
        )
        single = len(edges) == 1
        port_diagnostics = diagnostics if single else None
        # (source_handle, row, index, width, frame, from_head)
        matches: list[tuple[str | None, dict[str, Any], int, int, pl.DataFrame, bool]] = []
        unproven = False
        for source_handle, target_role in edges:
            if self._head_edge(parent_id, child_id, source_handle, target_role):
                frame = self._head_frame(parent_id, source_handle)
                if frame is None or frame.height == 0:
                    continue
                row, index, width = _match_parent_row(
                    frame,
                    parent_id=parent_id,
                    child_row=dict(child_row),
                    child_row_idx=child_row_idx,
                    child_len=child_len,
                    child_id=child_id,
                    node_map=self.node_map,
                    eager_outputs=self.frames,
                    edge_metadata=self.edge_metadata,
                    target_role=target_role,
                    diagnostics=port_diagnostics,
                    work=work,
                )
                if row is not None:
                    matches.append((source_handle, row, index, width, frame, True))
                continue
            transferred = self._transferred_parent_frame(
                parent_id=parent_id,
                child_id=child_id,
                source_handle=source_handle,
                target_role=target_role,
            )
            if transferred is not None:
                self._record_frame(parent_id, source_handle, transferred)
                row = _jsonify_row(transferred.row(0, named=True))
                matches.append((source_handle, row, 0, transferred.width, transferred, False))
                continue
            carried = self._carried_values(
                parent_id=parent_id,
                child_id=child_id,
                source_handle=source_handle,
                target_role=target_role,
                child_row=child_row,
            )
            lookup = self.lookup(parent_id, source_handle, carried) if carried else None
            if not carried or lookup is None:
                unproven = True
                schema = self.schema_for(parent_id, source_handle)
                if schema is not None:
                    self._record_frame(parent_id, source_handle, pl.DataFrame(schema=schema))
                continue
            self._record_frame(parent_id, source_handle, lookup)
            row, index = _find_matching_row(
                lookup,
                dict(carried),
                diagnostics=port_diagnostics,
                node_id=parent_id,
                child_node_id=child_id,
                allow_relaxed=False,
                work=work,
            )
            if row is not None:
                matches.append((source_handle, row, index, len(carried), lookup, False))
        if not matches:
            if diagnostics is not None and (unproven or not single):
                diagnostics.append(
                    {
                        "code": "row_scope_unproven" if single else "unresolved_source_frame",
                        "severity": "warning",
                        "reason": (
                            "row_scope_unproven" if single else "source_frame_row_not_correlated"
                        ),
                        "message": (
                            f"Row correlation for node {parent_id!r} could not identify the "
                            f"row that fed child {child_id!r}."
                        ),
                        "node_id": parent_id,
                        "child_node_id": child_id,
                        "match_strategy": "row_scope" if single else "source_frame",
                        "match_columns": [],
                        "ignored_columns": [],
                        "matched_row_count": 0,
                        "matched_row_indices": [],
                    }
                )
            return None, -1
        if traced_column is not None and len(matches) > 1:
            with_column = [match for match in matches if traced_column in match[4].columns]
            if with_column:
                matches = with_column
        if len(matches) > 1:
            widest = max(match[3] for match in matches)
            matches = [match for match in matches if match[3] == widest]
        if len(matches) != 1:
            if work is not None:
                work.ambiguity_count += 1
            if diagnostics is not None:
                diagnostics.append(
                    {
                        "code": "ambiguous_source_frame",
                        "severity": "warning",
                        "reason": "multiple_source_frames_matched",
                        "message": (
                            f"Row correlation for multi-frame node {parent_id!r} for child "
                            f"{child_id!r} matched several frames; no frame was selected."
                        ),
                        "node_id": parent_id,
                        "child_node_id": child_id,
                        "match_strategy": "source_frame",
                        "match_columns": [],
                        "ignored_columns": [],
                        "matched_row_count": len(matches),
                        "matched_row_indices": [],
                        "candidates": [match[0] for match in matches],
                    }
                )
            return None, -1
        handle, row, index, _width, frame, from_head = matches[0]
        if from_head:
            self.head_resolved.add(parent_id)
        elif handle is None and frame.height == 1:
            self.record_unique_row(parent_id, frame)
        return row, index

    def _record_frame(
        self,
        parent_id: str,
        source_handle: str | None,
        frame: pl.DataFrame,
    ) -> None:
        """Expose the looked-up rows to enrichment as this click's parent frame."""
        if source_handle is not None and isinstance(self.plans().get(parent_id), Mapping):
            existing = self.frames.get(parent_id)
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged[source_handle] = frame
            self.frames[parent_id] = merged
        else:
            self.frames[parent_id] = frame
