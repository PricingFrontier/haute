"""Per-column statistics and overview summary of a frame: the data profile analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import polars as pl

from haute._column_summary import CATEGORICAL_COUNT_FIELD, is_unhashable_dtype
from haute._execution_context import ExecutionContext
from haute._polars_utils import cancellable_streaming_collect
from haute.schemas import (
    ExploreCategoricalColumnProfile,
    ExploreColumnKind,
    ExploreColumnStat,
    ExploreDataQualityIssue,
    ExploreDataQualitySummary,
    ExploreDistinctValueCount,
    ExploreOverviewSummary,
)

# Display-truncation length for individual sample values (min/max and
# categorical value labels). Eighty characters keeps values readable without
# letting a single wide value dominate a card.
_VALUE_DISPLAY_MAX_CHARS = 80
_VALUE_DISPLAY_TRUNCATION_MARKER = "…"
_SUMMARY_NAME_LIMIT = 3
_CATEGORICAL_VALUE_COUNT_LIMIT = 50
_PROFILE_COLUMN_BATCH_SIZE = 8
_CATEGORICAL_VALUE_FIELD = "__haute_categorical_value"
_TEXT_DTYPE_BASES = (pl.String, pl.Categorical, pl.Enum, pl.Binary)
_LEXICAL_MIN_MAX_DTYPE_BASES = (pl.String, pl.Categorical, pl.Enum)
# Bases cast to String for min/max display so the values match the categorical
# value counts: text sorts alphabetically (not by category code) and booleans
# render as the same lowercase "true"/"false" rather than capitalised str(bool).
_STRING_MIN_MAX_DTYPE_BASES = (*_LEXICAL_MIN_MAX_DTYPE_BASES, pl.Boolean)


@dataclass(frozen=True, slots=True)
class ExploreFrameStats:
    row_count: int
    columns: list[ExploreColumnStat]
    overview_summary: ExploreOverviewSummary


def _is_float_dtype(dtype: pl.DataType) -> bool:
    """Return True for float dtypes, the only columns that can hold NaN.

    ``is_nan()`` raises ``InvalidOperationError`` against a non-float column
    (see ``_trace_correlation``), so NaN counting must be gated strictly on
    float dtype rather than the broader ``is_numeric()``.
    """

    return dtype.base_type() in (pl.Float32, pl.Float64)


def _is_identifier_candidate(
    name: str,
    *,
    row_count: int,
    null_count: int,
    nan_count: int | None,
    distinct_count: int | None,
) -> bool:
    lower_name = name.lower()
    has_identifier_name = (
        lower_name in {"id", "key", "uuid", "guid"}
        or lower_name.startswith(("id_", "key_"))
        or lower_name.endswith(("_id", "_key"))
    )
    return (
        row_count >= 2
        and null_count == 0
        and not (nan_count or 0)
        and distinct_count == row_count
        and has_identifier_name
    )


def _truncate_for_display(text: str) -> str:
    """Clip *text* to the display budget with an ellipsis marker."""

    if len(text) <= _VALUE_DISPLAY_MAX_CHARS:
        return text
    return text[:_VALUE_DISPLAY_MAX_CHARS] + _VALUE_DISPLAY_TRUNCATION_MARKER


def _format_display_value(value: Any) -> str | None:
    """Return a compact, one-cell display string for a single column value.

    Only ever receives scalar min/max and categorical values (text is cast to
    String upstream), so there is no nested/Series handling to do here.
    """

    if value is None:
        return None
    if isinstance(value, str):
        return _truncate_for_display(value)
    return _truncate_for_display(str(value))


def _format_numeric_profile_value(value: Any) -> str | None:
    """Return a compact display string for a numeric aggregate."""

    if value is None:
        return None
    if isinstance(value, float):
        return f"{value:.6g}"
    return _format_display_value(value)


def _supports_categorical_value_counts(dtype: pl.DataType) -> bool:
    """Return True when bounded distinct values can be displayed directly."""

    return _supports_min_max(dtype) or dtype.base_type() in _TEXT_DTYPE_BASES


def _supports_min_max(dtype: pl.DataType) -> bool:
    """Return True when min/max have a stable, user-facing ordering."""

    base = dtype.base_type()
    return (
        dtype.is_numeric()
        or dtype.is_temporal()
        or base == pl.Boolean
        or base in _LEXICAL_MIN_MAX_DTYPE_BASES
    )


def _has_categorical_value_counts(dtype: pl.DataType) -> bool:
    """Return True when this column gets a bounded value-count aggregation.

    Single source of truth for the categorical value-count branch: it gates
    both the expression added to the batched collect and the parse that reads
    it back, so the two can never drift (a drift would read an alias that was
    never aggregated, or vice versa).
    """

    return (
        not dtype.is_numeric()
        and not is_unhashable_dtype(dtype)
        and _supports_categorical_value_counts(dtype)
    )


def _min_max_column_expr(name: str, dtype: pl.DataType) -> pl.Expr:
    """Return the expression used for user-facing min/max values.

    Text-like and boolean columns are cast to String so their min/max match
    the categorical value counts (alphabetical text, lowercase booleans)
    instead of category codes or capitalised ``str(bool)`` output.
    """

    expr = pl.col(name)
    if dtype.base_type() in _STRING_MIN_MAX_DTYPE_BASES:
        return expr.cast(pl.String)
    return expr


def _column_kind(dtype: pl.DataType) -> ExploreColumnKind:
    """Classify a Polars dtype for concise analyst-facing inventory counts."""

    base = dtype.base_type()
    if dtype.is_numeric():
        return "Numeric"
    if dtype.is_temporal():
        return "Temporal"
    if base == pl.Boolean:
        return "Boolean"
    if dtype.is_nested():
        return "Nested"
    if base in _TEXT_DTYPE_BASES:
        return "Text"
    return "Other"


def _percent_text(numerator: int, denominator: int) -> str:
    if denominator <= 0 or numerator <= 0:
        return "0%"
    ratio = numerator / denominator
    if ratio < 0.01:
        return "<1%"
    return f"{ratio:.0%}"


def _limited_names(names: list[str], limit: int = _SUMMARY_NAME_LIMIT) -> list[str]:
    return names[:limit]


def _names_text(names: list[str]) -> str:
    return ", ".join(_limited_names(names)) if names else "None"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else plural or f"{singular}s"


def _build_data_quality_summary(
    row_count: int,
    columns: list[ExploreColumnStat],
    duplicate_row_count: int | None,
) -> ExploreDataQualitySummary:
    issues: list[ExploreDataQualityIssue] = []

    missing_columns = sorted(
        [column for column in columns if column.null_count > 0],
        key=lambda column: (
            -(column.null_count / row_count if row_count > 0 else 0),
            column.name.lower(),
        ),
    )
    mostly_null_columns = [
        column
        for column in missing_columns
        if row_count > 0 and column.null_count / row_count >= 0.5
    ]
    if missing_columns:
        worst = missing_columns[0]
        issues.append(
            ExploreDataQualityIssue(
                severity="danger" if mostly_null_columns else "warning",
                label=(
                    f"{len(missing_columns)} "
                    f"{_plural(len(missing_columns), 'column')} with missing values"
                ),
                detail=f"{worst.name} worst at {_percent_text(worst.null_count, row_count)}",
            )
        )

    # NaN is invalid-numeric signal, reported separately from null: a float
    # column that is usually numeric but carries non-numeric error/default
    # values materialises NaN, which ``null_count`` does not see.
    nan_columns = sorted(
        [column for column in columns if (column.nan_count or 0) > 0],
        key=lambda column: (
            -((column.nan_count or 0) / row_count if row_count > 0 else 0),
            column.name.lower(),
        ),
    )
    mostly_nan_columns = [
        column
        for column in nan_columns
        if row_count > 0 and (column.nan_count or 0) / row_count >= 0.5
    ]
    if nan_columns:
        worst = nan_columns[0]
        issues.append(
            ExploreDataQualityIssue(
                severity="danger" if mostly_nan_columns else "warning",
                label=(
                    f"{len(nan_columns)} numeric "
                    f"{_plural(len(nan_columns), 'column')} with NaN values"
                ),
                detail=f"{worst.name} worst at {_percent_text(worst.nan_count or 0, row_count)}",
            )
        )

    zero_heavy_columns = sorted(
        [
            column
            for column in columns
            if row_count - column.null_count > 0
            and (column.zero_count or 0) > 0
            and (column.zero_count or 0) / (row_count - column.null_count) >= 0.95
        ],
        key=lambda column: (-(column.zero_count or 0), column.name.lower()),
    )
    zero_heavy_names = {column.name for column in zero_heavy_columns}

    # Constant means every row holds the same valid value: a column with nulls
    # or NaNs alongside its single value is a missing-values / NaN column, not
    # a constant one (ruled 2026-07-16).
    constant_columns = sorted(
        [
            column
            for column in columns
            if row_count > 0
            and column.distinct_count == 1
            and column.null_count == 0
            and not (column.nan_count or 0)
            and column.name not in zero_heavy_names
        ],
        key=lambda column: column.name.lower(),
    )
    if constant_columns:
        issues.append(
            ExploreDataQualityIssue(
                severity="warning",
                label=(
                    f"{len(constant_columns)} constant / single-value "
                    f"{_plural(len(constant_columns), 'column')}"
                ),
                detail=_names_text([column.name for column in constant_columns]),
            )
        )

    negative_columns = sorted(
        [column for column in columns if (column.negative_count or 0) > 0],
        key=lambda column: (-(column.negative_count or 0), column.name.lower()),
    )
    if negative_columns:
        top = negative_columns[0]
        issues.append(
            ExploreDataQualityIssue(
                severity="warning",
                label=(
                    f"{len(negative_columns)} numeric "
                    f"{_plural(len(negative_columns), 'column')} with negatives"
                ),
                detail=f"{top.name}: {(top.negative_count or 0):,} rows",
            )
        )

    if zero_heavy_columns:
        issues.append(
            ExploreDataQualityIssue(
                severity="warning",
                label=(
                    f"{len(zero_heavy_columns)} mostly-zero numeric "
                    f"{_plural(len(zero_heavy_columns), 'column')}"
                ),
                detail=_names_text([column.name for column in zero_heavy_columns]),
            )
        )

    high_cardinality_columns = sorted(
        [column for column in columns if column.is_high_cardinality],
        key=lambda column: column.name.lower(),
    )
    if high_cardinality_columns:
        issues.append(
            ExploreDataQualityIssue(
                severity="warning",
                label=(
                    f"{len(high_cardinality_columns)} high-cardinality "
                    f"{_plural(len(high_cardinality_columns), 'column')}"
                ),
                detail=_names_text([column.name for column in high_cardinality_columns]),
            )
        )

    duplicate_ratio = (
        duplicate_row_count / row_count
        if row_count > 0 and duplicate_row_count is not None
        else None
    )
    if duplicate_row_count and duplicate_ratio is not None:
        issues.append(
            ExploreDataQualityIssue(
                severity="danger" if duplicate_ratio >= 0.5 else "warning",
                label=f"{duplicate_row_count:,} duplicate {_plural(duplicate_row_count, 'row')}",
                detail=(
                    f"{_percent_text(duplicate_row_count, row_count)} of rows are exact duplicates"
                ),
            )
        )

    return ExploreDataQualitySummary(
        issue_count=len(issues),
        issues=issues,
        duplicate_row_count=duplicate_row_count,
        duplicate_ratio=duplicate_ratio,
    )


def _categorical_value_counts_alias(name: str) -> str:
    return f"categorical_values::{name}"


def _categorical_label_group_count_alias(name: str) -> str:
    return f"categorical_label_groups::{name}"


def _lossy_decode_binary(value: bytes | None) -> str | None:
    """Decode raw bytes to text, mapping undecodable bytes to U+FFFD."""

    if value is None:
        return None
    return value.decode("utf-8", errors="replace")


def _format_duration(value: timedelta | None) -> str | None:
    """Format a Duration value as text, matching ``str(timedelta)``.

    Mirrors the min/max path, which leaves Duration uncast and lets
    ``_format_display_value`` apply ``str(timedelta)``, so a Duration column's
    value-count labels read identically to its min/max ("2:00:00",
    "1 day, 0:00:00").
    """

    if value is None:
        return None
    return str(value)


def _categorical_value_label_expr(name: str, dtype: pl.DataType) -> pl.Expr:
    """Return the String-typed expression whose distinct values are counted.

    Binary columns may hold arbitrary, non-UTF-8 bytes. A strict
    ``cast(pl.String)`` — and even ``cast(pl.String, strict=False)`` — raises
    ``ComputeError: invalid utf8`` on the first undecodable row, aborting the
    entire batched ``streaming_collect`` and taking down the whole Explore
    materialisation. Decode Binary leniently instead so undecodable bytes
    surface as the Unicode replacement character rather than crashing.

    Duration columns are temporal, so they reach this branch too, but Polars
    cannot ``cast(pl.Duration, pl.String)`` at all — the strict cast raises
    ``InvalidOperationError`` and aborts the same collect. Format Duration
    element-wise instead. Every other text-like dtype is already valid UTF-8
    and casts cheaply.
    """

    base = dtype.base_type()
    if base == pl.Binary:
        return pl.col(name).map_elements(_lossy_decode_binary, return_dtype=pl.String)
    if base == pl.Duration:
        return pl.col(name).map_elements(_format_duration, return_dtype=pl.String)
    return pl.col(name).cast(pl.String)


def _categorical_value_counts_expr(name: str, dtype: pl.DataType) -> pl.Expr:
    return (
        _categorical_value_label_expr(name, dtype)
        .value_counts(sort=True, name=CATEGORICAL_COUNT_FIELD)
        .struct.rename_fields([_CATEGORICAL_VALUE_FIELD, CATEGORICAL_COUNT_FIELD])
        .head(_CATEGORICAL_VALUE_COUNT_LIMIT)
        .implode()
    )


def _categorical_label_group_count_expr(name: str, dtype: pl.DataType) -> pl.Expr:
    """Count value-count groups after conversion to their display labels."""

    return _categorical_value_label_expr(name, dtype).n_unique()


def _parse_categorical_value_counts(
    value_counts: list[dict[str, Any]] | None,
) -> list[ExploreDistinctValueCount]:
    if not value_counts:
        return []

    values: list[ExploreDistinctValueCount] = []
    for value_count in value_counts:
        values.append(
            ExploreDistinctValueCount(
                value=_format_display_value(value_count[_CATEGORICAL_VALUE_FIELD]),
                count=int(value_count[CATEGORICAL_COUNT_FIELD]),
            )
        )
    return sorted(
        values,
        key=lambda item: (-item.count, item.value is None, item.value or ""),
    )


def _build_categorical_summary(
    schema: pl.Schema,
    columns: list[ExploreColumnStat],
    values_by_column: dict[str, list[ExploreDistinctValueCount]],
    label_group_counts: dict[str, int],
) -> list[ExploreCategoricalColumnProfile]:
    profiles: list[ExploreCategoricalColumnProfile] = []
    for column in columns:
        dtype = schema[column.name]
        if dtype.is_numeric():
            continue

        expandable = (
            column.distinct_count is not None
            and column.distinct_count > 0
            and _has_categorical_value_counts(dtype)
        )
        # Value counts group the display-label expression, which can differ
        # from raw values (notably lossy Binary decoding). Only columns that
        # actually collect values can be truncated.
        group_count = label_group_counts.get(column.name)
        values_truncated = group_count is not None and group_count > _CATEGORICAL_VALUE_COUNT_LIMIT
        values = values_by_column.get(column.name, [])
        profiles.append(
            ExploreCategoricalColumnProfile(
                field=column.name,
                distinct_count=column.distinct_count,
                expandable=expandable and bool(values),
                values_truncated=values_truncated,
                values=values,
            )
        )
    return profiles


def _build_overview_summary(
    row_count: int,
    schema: pl.Schema,
    columns: list[ExploreColumnStat],
    values_by_column: dict[str, list[ExploreDistinctValueCount]],
    label_group_counts: dict[str, int],
    duplicate_row_count: int | None,
) -> ExploreOverviewSummary:
    return ExploreOverviewSummary(
        data_quality=_build_data_quality_summary(row_count, columns, duplicate_row_count),
        categorical_summary=_build_categorical_summary(
            schema,
            columns,
            values_by_column,
            label_group_counts,
        ),
    )


def _build_frame_stats(
    lf: pl.LazyFrame,
    schema: pl.Schema,
    *,
    execution_context: ExecutionContext,
) -> ExploreFrameStats:
    """Compute row count and per-column schema stats for an Explore frame.

    Runs sequential column batches for core stats and bounded categorical
    value counts. Object columns skip ``n_unique`` (their
    distinct_count stays ``None``).
    """

    column_names = list(schema.names())
    aggregations: list[pl.Expr] = [pl.len().alias("row_count")]
    aggregation_batches = [aggregations]
    can_count_unique_rows = bool(column_names) and all(
        not is_unhashable_dtype(schema[name]) for name in column_names
    )
    for index, name in enumerate(column_names):
        if index and index % _PROFILE_COLUMN_BATCH_SIZE == 0:
            aggregations = []
            aggregation_batches.append(aggregations)
        dtype = schema[name]
        aggregations.append(pl.col(name).null_count().alias(f"null::{name}"))
        if not is_unhashable_dtype(dtype):
            aggregations.append(pl.col(name).n_unique().alias(f"unique::{name}"))
        if _supports_min_max(dtype):
            min_max_expr = _min_max_column_expr(name, dtype)
            aggregations.append(min_max_expr.min().alias(f"min::{name}"))
            aggregations.append(min_max_expr.max().alias(f"max::{name}"))
        if dtype.base_type() in _TEXT_DTYPE_BASES:
            text_expr = _categorical_value_label_expr(name, dtype).str.len_chars()
            aggregations.append(text_expr.min().alias(f"text_min_length::{name}"))
            aggregations.append(text_expr.mean().alias(f"text_mean_length::{name}"))
            aggregations.append(text_expr.max().alias(f"text_max_length::{name}"))
        if dtype.is_temporal():
            aggregations.append((pl.col(name).max() - pl.col(name).min()).alias(f"span::{name}"))
        if dtype.is_numeric():
            numeric_expr = pl.col(name)
            aggregations.append(
                numeric_expr.quantile(0.25, interpolation="linear").alias(f"p25::{name}")
            )
            aggregations.append(numeric_expr.median().alias(f"median::{name}"))
            aggregations.append(numeric_expr.mean().alias(f"mean::{name}"))
            aggregations.append(
                numeric_expr.quantile(0.75, interpolation="linear").alias(f"p75::{name}")
            )
            aggregations.append(numeric_expr.std().alias(f"std::{name}"))
            aggregations.append((pl.col(name) == 0).sum().alias(f"zero::{name}"))
            aggregations.append((pl.col(name) < 0).sum().alias(f"negative::{name}"))
            if _is_float_dtype(dtype):
                # NaN is the third missingness bucket, distinct from null.
                # ``is_nan()`` yields null for null rows, so ``.sum()`` counts
                # only genuine NaN values.
                aggregations.append(pl.col(name).is_nan().sum().alias(f"nan::{name}"))
        elif _has_categorical_value_counts(dtype):
            aggregations.append(
                _categorical_value_counts_expr(name, dtype).alias(
                    _categorical_value_counts_alias(name)
                )
            )
            aggregations.append(
                _categorical_label_group_count_expr(name, dtype).alias(
                    _categorical_label_group_count_alias(name)
                )
            )

    # Exact quantiles and distinct counts retain state even in streaming mode.
    # Project only a few columns per query so wide frames do not multiply that state.
    aggregate_row: dict[str, Any] = {}
    for batch in aggregation_batches:
        aggregate_row.update(
            cancellable_streaming_collect(
                lf.select(batch), execution_context=execution_context
            ).row(0, named=True)
        )

    row_count = int(aggregate_row["row_count"])
    duplicate_row_count = None
    if can_count_unique_rows:
        if any(int(aggregate_row[f"unique::{name}"]) == row_count for name in column_names):
            duplicate_row_count = 0
        elif len(column_names) == 1:
            duplicate_row_count = row_count - int(aggregate_row[f"unique::{column_names[0]}"])
        else:
            unique_rows = cancellable_streaming_collect(
                lf.select(pl.struct(column_names).n_unique().alias("unique_rows")),
                execution_context=execution_context,
            ).item()
            duplicate_row_count = row_count - int(unique_rows)
    stats: list[ExploreColumnStat] = []
    categorical_values_by_column: dict[str, list[ExploreDistinctValueCount]] = {}
    categorical_label_group_counts: dict[str, int] = {}
    for name in column_names:
        dtype = schema[name]
        null_count = int(aggregate_row[f"null::{name}"])
        nan_count = int(aggregate_row[f"nan::{name}"]) if _is_float_dtype(dtype) else None
        distinct_count: int | None
        if is_unhashable_dtype(dtype):
            distinct_count = None
        else:
            distinct_count = int(aggregate_row[f"unique::{name}"])
            # ``n_unique`` counts the null bucket and the NaN bucket each as one
            # distinct value; the analyst-facing distinct count is of valid
            # values only (null and NaN are reported separately as their own
            # counts, so an all-NaN column reads distinct == 0).
            if null_count > 0:
                distinct_count -= 1
            if nan_count:
                distinct_count -= 1
        valid_row_count = row_count - null_count - (nan_count or 0)
        unique_ratio = (
            distinct_count / valid_row_count
            if distinct_count is not None and valid_row_count > 0
            else None
        )
        # Only text-like columns get bounded categorical display, so only they
        # can outgrow it; numeric/temporal columns legitimately hold many
        # distinct values and are never flagged.
        is_high_cardinality = (
            dtype.base_type() in _TEXT_DTYPE_BASES
            and distinct_count is not None
            and distinct_count > _CATEGORICAL_VALUE_COUNT_LIMIT
        )
        is_identifier_candidate = _is_identifier_candidate(
            name,
            row_count=row_count,
            null_count=null_count,
            nan_count=nan_count,
            distinct_count=distinct_count,
        )
        profile_stats: dict[str, Any] = {}
        if _supports_min_max(dtype):
            profile_stats.update(
                {
                    "min_value": _format_display_value(aggregate_row[f"min::{name}"]),
                    "max_value": _format_display_value(aggregate_row[f"max::{name}"]),
                }
            )
        if dtype.is_numeric():
            profile_stats.update(
                {
                    "p25_value": _format_numeric_profile_value(aggregate_row[f"p25::{name}"]),
                    "median_value": _format_numeric_profile_value(aggregate_row[f"median::{name}"]),
                    "mean_value": _format_numeric_profile_value(aggregate_row[f"mean::{name}"]),
                    "p75_value": _format_numeric_profile_value(aggregate_row[f"p75::{name}"]),
                    "std_value": _format_numeric_profile_value(aggregate_row[f"std::{name}"]),
                    "zero_count": int(aggregate_row[f"zero::{name}"]),
                    "negative_count": int(aggregate_row[f"negative::{name}"]),
                }
            )
            if _is_float_dtype(dtype):
                profile_stats["nan_count"] = nan_count
        elif _has_categorical_value_counts(dtype):
            categorical_values_by_column[name] = _parse_categorical_value_counts(
                aggregate_row[_categorical_value_counts_alias(name)]
            )
            categorical_label_group_counts[name] = int(
                aggregate_row[_categorical_label_group_count_alias(name)]
            )
        if dtype.base_type() in _TEXT_DTYPE_BASES:
            profile_stats.update(
                {
                    "text_min_length": aggregate_row[f"text_min_length::{name}"],
                    "text_mean_length": aggregate_row[f"text_mean_length::{name}"],
                    "text_max_length": aggregate_row[f"text_max_length::{name}"],
                }
            )
        if dtype.is_temporal():
            profile_stats["temporal_span"] = _format_duration(aggregate_row[f"span::{name}"])

        stats.append(
            ExploreColumnStat(
                name=name,
                dtype=str(dtype),
                kind=_column_kind(dtype),
                null_count=null_count,
                distinct_count=distinct_count,
                unique_ratio=unique_ratio,
                is_high_cardinality=is_high_cardinality,
                is_identifier_candidate=is_identifier_candidate,
                **profile_stats,
            )
        )
    return ExploreFrameStats(
        row_count=row_count,
        columns=stats,
        overview_summary=_build_overview_summary(
            row_count,
            schema,
            stats,
            categorical_values_by_column,
            categorical_label_group_counts,
            duplicate_row_count,
        ),
    )
