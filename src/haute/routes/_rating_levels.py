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
from fastapi import HTTPException

from haute._analysis_results import SynchronousAnalysisCache
from haute._data_points import (
    CacheRequiredError,
    ConsumerPoint,
    DataPointResolver,
    LeasedPointFrame,
    NodeDataPointInvalidError,
    PointColumnsMissingError,
    PointDataChangedError,
    consumer_point,
)
from haute._execution_context import ExecutionCancellationToken, ExecutionContext
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._polars_utils import cancellable_streaming_collect
from haute._rating import _rating_key_expr
from haute.routes._node_data_service import NodeDataService, node_data_project_root
from haute.routes._synchronous_analysis import run_synchronous_analysis
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


def _unprocessable(message: str) -> HTTPException:
    """Columns this data cannot offer levels for.

    The repository keeps `HTTPException.detail` a plain string (issue #76), so
    the two conditions are told apart by their message rather than by a code in
    the body: the data has no such column, or the column is not the kind of
    column a raw factor level comes from.
    """
    return HTTPException(status_code=422, detail=message)


def _is_string_like(dtype: pl.DataType) -> bool:
    """Whether a column's values are the text a raw factor level is written as.

    The same kinds the preview path offers today: plain strings, and the two
    dictionary-backed forms of them.
    """
    return dtype == pl.String or dtype == pl.Categorical or isinstance(dtype, pl.Enum)


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
        raise _unprocessable("This request names no column to read levels from.")
    return tuple(seen)


def _column_list(columns: list[str]) -> str:
    return ", ".join(repr(column) for column in columns)


class RatingLevelsService:
    """Raw factor levels for a Rating Step, over the node's shared data point."""

    def __init__(self, node_data: NodeDataService) -> None:
        self._node_data = node_data
        self._cache: SynchronousAnalysisCache = SynchronousAnalysisCache()

    def levels(
        self,
        body: RatingLevelsRequest,
        *,
        cancellation_token: ExecutionCancellationToken | None = None,
    ) -> RatingLevelsResponse:
        """Return the columns' levels, or say the data is not there to read.

        The token is the request's, so a browser that abandons the question —
        the editor supersedes its own request when the factors change — stops
        the scan rather than leaving it holding an admission and a lease for an
        answer nobody will read.
        """
        consumer, resolver = self._consumer(body)
        columns = _requested_columns(body.columns)
        # The factors being edited may name columns the saved node does not
        # read, so the demand covers them: a snapshot without one of them is not
        # current for this request rather than answering from data that lacks it.
        reader = ConsumerPoint(
            consumer.consumer_node_id,
            consumer.point,
            consumer.demand
            if consumer.demand.names is None
            else NodeSnapshotColumns.of({*consumer.demand.names, *columns}),
        )
        point = self._node_data.point_for(reader, resolver)

        request = {
            "analysis": "rating_levels",
            "version": RATING_LEVELS_VERSION,
            "columns": list(columns),
            "value_limit": body.value_limit,
        }
        try:
            levels = run_synchronous_analysis(
                resolver,
                reader.point,
                reader.demand,
                operation="rating_levels",
                request=request,
                cache=self._cache,
                compute=lambda leased, context: self._compute(body, columns, leased, context),
                cancellation_token=cancellation_token,
            )
        except (CacheRequiredError, PointDataChangedError):
            return RatingLevelsResponse(status="cache_required", point=point)
        except PointColumnsMissingError as exc:
            # A narrower *snapshot* resolves partial above and asks to be
            # cached; reaching here means the data itself has no such column.
            raise _unprocessable(
                f"Column(s) {_column_list(exc.missing)} are not in the data this node reads."
            ) from exc
        return RatingLevelsResponse(
            status="ok",
            point=point,
            # The version the analysis read, which a refresh during it can make
            # different from the one the point reported when it was resolved.
            data_version=levels.data_version,
            total_rows=levels.total_rows,
            columns=list(levels.columns),
        )

    def _consumer(self, body: RatingLevelsRequest) -> tuple[ConsumerPoint, DataPointResolver]:
        try:
            consumer = consumer_point(body.graph, body.node_id)
        except NodeDataPointInvalidError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        resolver = DataPointResolver(
            body.graph, source=body.source, store=NodeSnapshotStore(node_data_project_root())
        )
        return consumer, resolver

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
            raise _unprocessable(
                f"Column(s) {_column_list(missing)} are not in the data this node reads."
            )
        unsupported = [column for column in columns if not _is_string_like(schema[column])]
        if unsupported:
            kinds = ", ".join(f"{column!r} is {schema[column]}" for column in unsupported)
            raise _unprocessable(f"Raw factor levels come from text columns, but {kinds}.")

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
        # on — not a rendering of the value that happens to look like it.
        key = _rating_key_expr(column, dtype).alias("value")
        grouped = cancellable_streaming_collect(
            frame.select(key)
            .drop_nulls()
            # A blank is not a level anyone can rate on, and the preview path
            # has never offered one.
            .filter(pl.col("value").str.strip_chars() != "")
            .group_by("value")
            .agg(pl.len().alias("count"))
            # Most common first, so what the cap keeps is what the data is
            # mostly made of rather than whatever sorts first.
            .sort(["count", "value"], descending=[True, False]),
            execution_context=context,
        )
        return RatingLevelColumn(
            column=column,
            values=[
                RatingLevelValue(value=str(row["value"]), count=int(row["count"]))
                for row in grouped.head(body.value_limit).iter_rows(named=True)
            ],
            distinct_count=grouped.height,
            null_count=null_count,
        )
