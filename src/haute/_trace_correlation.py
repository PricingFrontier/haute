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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, cast

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


def _jsonify_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert Polars row values to JSON-serialisable Python types.

    The preview JSON boundary is the single display-value authority for
    trace too, including recursive temporal, nested, non-finite, object,
    and JavaScript-unsafe integer values.
    """
    return {str(key): to_json_safe(value) for key, value in row.items()}


def _legacy_trace_values_match(actual: Any, expected: Any) -> bool:
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
        return math.isclose(actual, float(expected), rel_tol=_TRACE_REL_TOL, abs_tol=_TRACE_ABS_TOL)
    if isinstance(actual, int) and isinstance(expected, float):
        return math.isclose(float(actual), expected, rel_tol=_TRACE_REL_TOL, abs_tol=_TRACE_ABS_TOL)
    if isinstance(actual, int) and isinstance(expected, str):
        return abs(actual) > MAX_SAFE_INTEGER and expected == str(actual)
    # String coercion for dates/datetimes only
    from datetime import date, datetime

    if isinstance(actual, (date, datetime)) or isinstance(expected, (date, datetime)):
        if str(actual) == str(expected):
            return True
    return False


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


def _legacy_build_value_match_expr(
    column: str, value: Any, dtype: pl.DataType | None = None
) -> pl.Expr:
    """Build a Polars boolean expression matching one column to one trace value.

    *dtype* is the column's Polars dtype.  It makes the predicate
    dtype-robust: a numeric/NaN/Inf trace value compared against a column
    of an incompatible dtype (e.g. a numeric value vs a ``Utf8`` column,
    or ``is_nan`` against a non-float column) would otherwise raise a
    ``ComputeError``/``InvalidOperationError`` at collect time and crash
    the whole correlation.  With the dtype known, such comparisons
    degrade to a non-matching predicate — preserving the documented
    fail-soft ``(None, -1)`` — while genuine cross-type coercions
    (int-like ``25`` matching a ``"25"`` string key) still go through a
    stringwise compare.  When *dtype* is ``None`` the historical
    behaviour is kept.
    """
    col_is_float = dtype in (pl.Float32, pl.Float64)
    col_is_numeric = bool(dtype.is_numeric()) if dtype is not None else True
    col_is_string = dtype in (pl.Utf8, pl.String)

    # An always-false predicate that references the column, so it
    # broadcasts to the frame's height (a bare ``pl.lit(False)`` is a
    # length-1 literal and would not align with the other per-row
    # predicates).
    never_match = pl.col(column).is_null() & pl.lit(False)

    non_finite = non_finite_float_token(value)
    if non_finite in ("nan", "inf", "-inf"):
        if dtype is not None and not col_is_float:
            # A non-finite float value can never equal a non-float cell.
            return never_match
        if non_finite == "nan":
            return pl.col(column).is_nan()
        if non_finite == "inf":
            return pl.col(column).is_infinite() & (pl.col(column) > 0)
        return pl.col(column).is_infinite() & (pl.col(column) < 0)
    if value is None:
        return pl.col(column).is_null()
    if isinstance(value, float) and math.isnan(value):
        if dtype is not None and not col_is_float:
            return never_match
        return pl.col(column).is_nan()
    if isinstance(value, str):
        # Cast column to Utf8 so stringified dates/datetimes match.
        return pl.col(column).cast(pl.Utf8) == value
    if dtype is not None and not col_is_numeric:
        # Numeric (or bool) value against a non-numeric column: comparing
        # them directly raises.  A string column can still match an
        # int-like key via a stringwise compare (mirrors the str-value
        # branch); any other dtype degrades to a non-match.
        if col_is_string:
            return pl.col(column) == str(value)
        return never_match
    return cast(pl.Expr, pl.col(column) == value)


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


def _build_value_match_expr(column: str, value: Any, dtype: pl.DataType | None = None) -> pl.Expr:
    """Compatibility expression wrapper over the typed V1 matcher."""
    if dtype is None:
        return pl.col(column) == pl.lit(value)
    expression, _reason = _typed_value_match_expr(column, value, dtype)
    return expression if expression is not None else _never_match_expr(column)


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
) -> _RowMatchResult:
    """Match row identity with native Polars expressions and bounded output."""
    keys = tuple(
        column for column in key_columns if column in frame.columns and column in row_values
    )
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
        return _RowMatchResult(
            status=(
                _RowMatchStatus.UNIQUE_STRICT if strict_count == 1 else _RowMatchStatus.AMBIGUOUS
            ),
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
    return _RowMatchResult(
        status=(
            _RowMatchStatus.UNIQUE_RELAXED if relaxed_count == 1 else _RowMatchStatus.AMBIGUOUS
        ),
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


#: Tokens whose presence in a node's code means the transform can
#: reorder rows or change row identity (so a positional alignment cannot
#: be trusted without shared columns to verify it).
_ROW_REORDERING_TOKENS = (
    ".sort",
    ".reverse",
    ".gather",
    ".take(",
    ".sample",
    ".shuffle",
    ".join(",
    ".group_by(",
    ".groupby(",
    ".unique(",
    ".top_k(",
    ".bottom_k(",
    ".explode(",
    ".pivot(",
    ".cross_join(",
)


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
    low = code.lower()
    return any(token in low for token in _ROW_REORDERING_TOKENS)


def _shared_key_is_unique(
    df: pl.DataFrame,
    match_row: dict[str, Any],
    shared_cols: list[str],
) -> bool:
    """Whether *shared_cols* values identify exactly one row in *df*.

    Used to gate the positional fast path: a positionally-aligned parent
    row may only be trusted when the carried shared columns pin down a
    single parent row.  A non-unique key means a row-reordering transform
    could have placed a *different* equally-matching row at that position,
    so the caller must fall through to the value-matching path (which
    records the ambiguity / marks the step unresolved) rather than guess.

    The comparison mirrors the positional check exactly — jsonify each
    parent row and compare via :func:`_trace_values_match` — so the
    uniqueness notion agrees with the acceptance notion and it is robust
    to exotic column dtypes (List/Struct) that would not cast into a
    Polars predicate.  It short-circuits as soon as a second match is
    seen.
    """
    if not shared_cols:
        return False
    return (
        _match_rows_vectorized(df, match_row, shared_cols).status is _RowMatchStatus.UNIQUE_STRICT
    )


def _allows_relaxed_parent_match(
    parent_id: str,
    child_node: GraphNode | None,
) -> bool:
    """Edge-join right parents must not relax a miss into false lineage."""
    if child_node is None or child_node.data.nodeType != NodeType.EDGE_JOIN:
        return True
    return parent_id != child_node.data.config.get("joinInput")


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


def _match_parent_row(
    parent_df: pl.DataFrame,
    *,
    parent_id: str,
    child_row: dict[str, Any],
    child_row_idx: int,
    child_len: int,
    child_id: str,
    node_map: Mapping[str, GraphNode],
    eager_outputs: dict[str, pl.DataFrame],
    diagnostics: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, int, int]:
    """Correlate one parent FRAME against the resolved child row.

    Returns ``(row_dict, positional_index, match_width)`` where
    ``match_width`` is the number of child columns projected onto this
    frame for matching — the specificity a multi-frame resolver uses to
    rank competing candidate frames.  ``(None, -1, width)`` when no row
    can be confidently identified.
    """
    parent_cols = set(parent_df.columns)
    child_node = node_map.get(child_id)
    match_row = _build_parent_match_row(
        child_row,
        parent_id,
        parent_cols,
        child_node,
        eager_outputs,
    )

    # Fast path: same row count → likely 1:1 (with_columns, rename, select).
    # Trust the positionally-aligned parent row only when it can be
    # verified.  With shared columns, they must match AND uniquely
    # identify the row (a non-unique key means a reorder could have
    # swapped in a different matching row).  With NO shared columns to
    # verify against, position is trustworthy only when the child
    # transform provably preserves row order (rename/with_columns/
    # select) or there is a single candidate row; a reordering
    # transform (sort/join/gather/…) falls through and the step is
    # left unresolved rather than attached to the wrong parent row.
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
        allow_relaxed=_allows_relaxed_parent_match(parent_id, child_node),
    )
    return row_dict, idx, len(match_row)


def _resolve_multi_frame_parent(
    frames: dict[str, pl.DataFrame],
    *,
    parent_id: str,
    child_id: str,
    handles: Sequence[str | None] | None,
    traced_column: str | None,
    child_row: dict[str, Any],
    child_row_idx: int,
    child_len: int,
    node_map: Mapping[str, GraphNode],
    eager_outputs: dict[str, pl.DataFrame],
    diagnostics: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, int]:
    """Correlate a multi-frame parent (``dict[label, DataFrame]``) row.

    *handles* carries ONE entry per edge between parent and child (a
    multi-frame source can feed the same child through several edges,
    each consuming a distinct frame), so resolution is per-edge: every
    candidate frame is matched against the resolved child row, and only
    an actual, confident row match makes a frame the correlated one.
    When several frames match, the frame carrying the traced column is
    preferred, then the most specific match (widest projected-column
    set). A surviving tie remains unresolved and is recorded as a
    diagnostic rather than guessing between frames.
    """
    seen: set[str] = set()
    candidates: list[tuple[str, pl.DataFrame]] = []
    for handle in handles or ():
        if handle is None or handle in seen:
            continue
        seen.add(handle)
        frame = frames.get(handle)
        if frame is not None and len(frame) > 0:
            candidates.append((handle, frame))

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
                    "source_handles": sorted(seen),
                    "frames": sorted(frames.keys()),
                }
            )
        return None, -1

    if len(candidates) == 1:
        row_dict, idx, _ = _match_parent_row(
            candidates[0][1],
            parent_id=parent_id,
            child_row=child_row,
            child_row_idx=child_row_idx,
            child_len=child_len,
            child_id=child_id,
            node_map=node_map,
            eager_outputs=eager_outputs,
            diagnostics=diagnostics,
        )
        return row_dict, idx

    # Several edges consume distinct frames of this parent — match each
    # candidate frame independently (suppressing per-candidate ambiguity
    # noise) and keep the frames that confidently identify a row.
    matches: list[tuple[str, pl.DataFrame, dict[str, Any], int, int]] = []
    for handle, frame in candidates:
        row_dict, idx, width = _match_parent_row(
            frame,
            parent_id=parent_id,
            child_row=child_row,
            child_row_idx=child_row_idx,
            child_len=child_len,
            child_id=child_id,
            node_map=node_map,
            eager_outputs=eager_outputs,
            diagnostics=None,
        )
        if row_dict is not None:
            matches.append((handle, frame, row_dict, idx, width))

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
                    "source_handles": [handle for handle, _ in candidates],
                    "frames": sorted(frames.keys()),
                }
            )
        return None, -1

    picked = matches
    if traced_column is not None:
        with_column = [m for m in picked if traced_column in m[1].columns]
        if with_column:
            picked = with_column
    if len(picked) > 1:
        best_width = max(m[4] for m in picked)
        picked = [m for m in picked if m[4] == best_width]
    if len(picked) > 1:
        if diagnostics is not None:
            diagnostics.append(
                {
                    "code": "ambiguous_source_frame",
                    "severity": "warning",
                    "reason": "multiple_source_frames_matched",
                    "message": (
                        f"Row correlation for multi-frame node {parent_id!r} for child "
                        f"{child_id!r} matched several frames "
                        f"({[m[0] for m in picked]}); no frame was selected."
                    ),
                    "node_id": parent_id,
                    "child_node_id": child_id,
                    "match_strategy": "source_frame",
                    "match_columns": [],
                    "ignored_columns": [],
                    "matched_row_count": len(picked),
                    "matched_row_indices": [],
                    "candidates": [m[0] for m in picked],
                }
            )
        return None, -1
    _, _, row_dict, idx, _ = picked[0]
    return row_dict, idx


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
    eager_outputs: dict[str, pl.DataFrame],
    order: list[str],
    parents_of: dict[str, list[str]],
    target_node_id: str,
    row_index: int,
    *,
    node_map: Mapping[str, GraphNode],
    diagnostics: list[dict[str, Any]] | None = None,
    unresolved: dict[str, tuple[str, int]] | None = None,
    source_frames_of: Mapping[tuple[str, str], Sequence[str | None]] | None = None,
    traced_column: str | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Extract the correct row from each node using post-hoc correlation.

    Uses the preview-cached DataFrames directly — no re-execution, no
    injected columns.  Walks backward from the target node and matches
    each parent's row by shared column values with the already-resolved
    child row.  *node_map* supplies node type and config so that
    edge-join children can route suffixed/colliding columns to the
    correct parent (see :func:`_build_parent_match_row`).

    *source_frames_of* maps a (source, target) node pair to the
    ``sourceHandle`` of EVERY edge between them, in edge order — the
    per-edge frame selection ``_pick_source_frame`` makes at execution
    time for multi-frame sources.  *traced_column* (the column the user
    is tracing, if any) disambiguates when several frames of one source
    feed the same child and more than one matches the child row.

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
                handles=(source_frames_of or {}).get((nid, resolved_child_id)),
                traced_column=traced_column,
                child_row=child_row,
                child_row_idx=child_row_idx,
                child_len=child_len,
                node_map=node_map,
                eager_outputs=eager_outputs,
                diagnostics=diagnostics,
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
        row_dict, idx, _ = _match_parent_row(
            parent_df,
            parent_id=nid,
            child_row=child_row,
            child_row_idx=child_row_idx,
            child_len=child_len,
            child_id=resolved_child_id,
            node_map=node_map,
            eager_outputs=eager_outputs,
            diagnostics=diagnostics,
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
