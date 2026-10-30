"""Whole-dataset levels for the raw factor columns a Rating Step rates on.

The Rating Step editor builds its tables from the levels it can see, and what it
could see was a preview: a level that appears only outside those rows could not
be chosen, so the table had no row for it and the rating default was applied to
data the user meant to rate. The levels are read from the whole data point the
node reads here, keyed exactly as the lookup keys them, so a level chosen in the
editor is one that matches at lookup time.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from haute._data_points import LeasedPointFrame
from haute._execution_context import ExecutionCancellationToken, ExecutionContext
from haute._polars_dtypes import is_string_or_categorical
from haute._polars_utils import cancellable_streaming_collect
from haute._rating import _rating_key_expr
from haute.routes._point_analysis import (
    PointAnalysisService,
    columns_missing,
    most_common_values,
    unprocessable,
)
from haute.schemas import (
    RatingLevelColumn,
    RatingLevelsRequest,
    RatingLevelsResponse,
    RatingLevelValue,
)

RATING_LEVELS_VERSION = 1


@dataclass(frozen=True, slots=True)
class _RatingLevels:
    """The levels read, and which version of the data they were read from.

    The version is the lease's rather than the one the point reported when the
    request resolved: a refresh between the two makes those different, and the
    editor compares this against its own point to decide whether these levels
    describe the data it is looking at.
    """

    data_version: str
    total_rows: int
    columns: tuple[RatingLevelColumn, ...]


def _is_string_like(dtype: pl.DataType) -> bool:
    """Whether a column's values are the text a raw factor level is written as.

    The same kinds the preview path offers today: plain strings, and the two
    dictionary-backed forms of them.
    """
    return is_string_or_categorical(dtype) or isinstance(dtype, pl.Enum)


def _requested_columns(columns: list[str]) -> tuple[str, ...]:
    """The columns asked about, once each, in the order they were asked for.

    A name is used exactly as given. Trimming is only how a blank entry is
    recognised: a column really named `"region "` is one execution rates on, and
    trimming it here would read a different column or report it missing.
    """
    seen: dict[str, None] = {}
    for column in columns:
        if column.strip():
            seen.setdefault(column, None)
    if not seen:
        raise unprocessable("This request names no column to read levels from.")
    return tuple(seen)


class RatingLevelsService(PointAnalysisService):
    """Raw factor levels for a Rating Step, over the node's shared data point."""

    def levels(
        self,
        body: RatingLevelsRequest,
        *,
        cancellation_token: ExecutionCancellationToken | None = None,
    ) -> RatingLevelsResponse:
        """Return the columns' levels, or say the data is not there to read."""
        columns = _requested_columns(body.columns)
        point, levels = self._analyse(
            body,
            columns,
            operation="rating_levels",
            request={
                "analysis": "rating_levels",
                "version": RATING_LEVELS_VERSION,
                "columns": list(columns),
                "value_limit": body.value_limit,
            },
            compute=lambda leased, context: self._compute(body, columns, leased, context),
            cancellation_token=cancellation_token,
        )
        if levels is None:
            return RatingLevelsResponse(status="cache_required", point=point)
        return RatingLevelsResponse(
            status="ok",
            point=point,
            # The version the analysis read, which a refresh during it can make
            # different from the one the point reported when it was resolved.
            data_version=levels.data_version,
            total_rows=levels.total_rows,
            columns=list(levels.columns),
        )

    def _compute(
        self,
        body: RatingLevelsRequest,
        columns: tuple[str, ...],
        leased: LeasedPointFrame,
        context: ExecutionContext,
    ) -> _RatingLevels:
        frame = leased.scan
        schema = frame.collect_schema()
        missing = [column for column in columns if column not in schema]
        if missing:
            raise columns_missing(missing)
        unsupported = [column for column in columns if not _is_string_like(schema[column])]
        if unsupported:
            kinds = ", ".join(f"{column!r} is {schema[column]}" for column in unsupported)
            raise unprocessable(f"Raw factor levels come from text columns, but {kinds}.")

        # One pass for everything that does not need grouping: the row count the
        # editor reports its levels against, and each column's missing rows.
        totals = cancellable_streaming_collect(
            frame.select(
                pl.len().alias("rows"),
                *[pl.col(column).null_count().alias(f"nulls:{column}") for column in columns],
            ),
            execution_context=context,
        )
        return _RatingLevels(
            data_version=leased.data_version,
            total_rows=int(totals.item(0, "rows")),
            columns=tuple(
                self._column_levels(
                    body,
                    frame,
                    column,
                    schema[column],
                    int(totals.item(0, f"nulls:{column}")),
                    context,
                )
                for column in columns
            ),
        )

    def _column_levels(
        self,
        body: RatingLevelsRequest,
        frame: pl.LazyFrame,
        column: str,
        dtype: pl.DataType,
        null_count: int,
        context: ExecutionContext,
    ) -> RatingLevelColumn:
        # The lookup's own key, so a level chosen here is the level a run joins
        # on — not a rendering of the value that happens to look like it. A blank
        # is not a level anyone can rate on, and the preview path has never
        # offered one.
        values, distinct_count = most_common_values(
            frame,
            _rating_key_expr(column, dtype),
            limit=body.value_limit,
            context=context,
            keep=pl.col("value").str.strip_chars() != "",
        )
        return RatingLevelColumn(
            column=column,
            values=[RatingLevelValue(value=value, count=count) for value, count in values],
            distinct_count=distinct_count,
            null_count=null_count,
        )
