"""Whole-dataset statistics for one banding factor.

The Banding editor needs to see the data it is banding: the distribution of a
numeric column, the values of a categorical one, and how many rows each rule
claims. Preview rows cannot answer any of those honestly — rare categories and
tails are missing, and counts drawn from a sample are not the counts execution
produces — so they are computed here, over the whole data point the node reads,
under the same admitted context and lease as every other request-time analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import polars as pl
from fastapi import HTTPException

from haute._analysis_results import SynchronousAnalysisCache
from haute._binning import equal_width_bins
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
from haute._rating import (
    banding_rule_claim_expr,
    banding_temporal_ordinal_expr,
    require_banding_type,
)
from haute.routes._node_data_service import NodeDataService, node_data_project_root
from haute.routes._synchronous_analysis import run_synchronous_analysis
from haute.schemas import (
    BandingHistogramBin,
    BandingStatsRequest,
    BandingStatsResponse,
    BandingValueCount,
)

BANDING_STATS_VERSION = 1
NUMERIC_BANDING_MODES = frozenset({"breakpoints"})


@dataclass(frozen=True, slots=True)
class _BandingStats:
    """What one factor's data says, and which version of it said so.

    The version is the one the lease served, not the one the request resolved:
    a refresh between the two makes those different, and a result labelled with
    a version it was not computed from is exactly what the editor uses to decide
    whether it may show whole-dataset counts.
    """

    data_version: str
    total_rows: int
    null_count: int
    non_finite_count: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    bins: tuple[BandingHistogramBin, ...] = ()
    values: tuple[BandingValueCount, ...] = ()
    distinct_count: int | None = None
    other_count: int | None = None
    rule_counts: tuple[int, ...] = field(default=())
    unmatched_count: int | None = None


def _unprocessable(message: str) -> HTTPException:
    """A factor this data cannot answer for.

    The repository keeps `HTTPException.detail` a plain string (issue #76), so
    each condition is told apart by its own message rather than by a code in the
    body: the column is not in the data, the column is not a kind the mode can
    compare, or the rules are ones execution itself would refuse — in which case
    the message is execution's own.
    """
    return HTTPException(status_code=422, detail=message)


def _factor_mode(factor: dict[str, Any]) -> str:
    return str(factor.get("banding") or "")


def _factor_column(factor: dict[str, Any]) -> str:
    column = factor.get("column")
    return str(column) if isinstance(column, str) else ""


def _is_numeric(dtype: Any) -> bool:
    return bool(getattr(dtype, "is_numeric", lambda: False)())


def _is_date(dtype: Any) -> bool:
    """Date and Datetime, the temporal dtypes breakpoints can band."""
    return isinstance(dtype, pl.Date | pl.Datetime) or dtype in (pl.Date, pl.Datetime)


class BandingStatsService:
    """Statistics for the factor being edited, over the node's shared data point."""

    def __init__(self, node_data: NodeDataService) -> None:
        self._node_data = node_data
        self._cache: SynchronousAnalysisCache = SynchronousAnalysisCache()

    def stats(
        self,
        body: BandingStatsRequest,
        *,
        cancellation_token: ExecutionCancellationToken | None = None,
    ) -> BandingStatsResponse:
        """Return the factor's statistics, or say the data is not there to read.

        The token is the request's: a browser that abandons this question — the
        editor supersedes its own request on every edit — stops the scan rather
        than leaving a whole-dataset collection holding its admission and lease
        until it finishes for nobody.
        """
        consumer, resolver = self._consumer(body)
        column = _factor_column(body.factor)
        if not column:
            raise _unprocessable("This factor has no input column.")
        # The factor being edited may name a column the saved node does not, so
        # the demand covers it: a point without that column is not current for
        # this request rather than answering from data that lacks it.
        reader = ConsumerPoint(
            consumer.consumer_node_id,
            consumer.point,
            consumer.demand
            if consumer.demand.names is None
            else NodeSnapshotColumns.of({*consumer.demand.names, column}),
        )
        point = self._node_data.point_for(reader, resolver)

        request = {
            "analysis": "banding_stats",
            "version": BANDING_STATS_VERSION,
            "column": column,
            "mode": _factor_mode(body.factor),
            "rules": body.factor.get("rules") or [],
            "right_closed": bool(body.factor.get("rightClosed", True)),
            "output_column": str(body.factor.get("outputColumn") or ""),
            "histogram_bins": body.histogram_bins,
            "value_limit": body.value_limit,
        }
        try:
            stats = run_synchronous_analysis(
                resolver,
                reader.point,
                reader.demand,
                operation="banding_stats",
                request=request,
                cache=self._cache,
                compute=lambda leased, context: self._compute(body, column, leased, context),
                cancellation_token=cancellation_token,
            )
        except (CacheRequiredError, PointDataChangedError):
            return BandingStatsResponse(status="cache_required", point=point)
        except PointColumnsMissingError as exc:
            # A narrower *snapshot* resolves partial above and asks to be
            # cached; reaching here means the data itself has no such column.
            raise _unprocessable(f"Column {column!r} is not in the data this node reads.") from exc
        return BandingStatsResponse(
            status="ok",
            point=point,
            # The version the analysis read, which a refresh during it can make
            # different from the one the point reported when it was resolved.
            data_version=stats.data_version,
            total_rows=stats.total_rows,
            null_count=stats.null_count,
            non_finite_count=stats.non_finite_count,
            minimum=stats.minimum,
            maximum=stats.maximum,
            bins=list(stats.bins),
            values=list(stats.values),
            distinct_count=stats.distinct_count,
            other_count=stats.other_count,
            rule_counts=list(stats.rule_counts),
            unmatched_count=stats.unmatched_count,
        )

    def _consumer(self, body: BandingStatsRequest) -> tuple[ConsumerPoint, DataPointResolver]:
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
        body: BandingStatsRequest,
        column: str,
        leased: LeasedPointFrame,
        context: ExecutionContext,
    ) -> _BandingStats:
        mode = _factor_mode(body.factor)
        try:
            require_banding_type(mode)
        except ValueError as exc:
            raise _unprocessable(str(exc)) from exc
        frame = leased.scan
        schema = frame.collect_schema()
        if column not in schema:
            raise _unprocessable(f"Column {column!r} is not in the data this node reads.")
        dtype = schema[column]
        if mode in NUMERIC_BANDING_MODES and not (_is_numeric(dtype) or _is_date(dtype)):
            raise _unprocessable(
                f"Column {column!r} is {dtype}, which {mode} banding cannot compare."
            )

        totals = cancellable_streaming_collect(
            frame.select(
                pl.len().alias("rows"),
                pl.col(column).null_count().alias("nulls"),
            ),
            execution_context=context,
        )
        stats = _BandingStats(
            data_version=leased.data_version,
            total_rows=int(totals.item(0, "rows")),
            null_count=int(totals.item(0, "nulls")),
        )
        if mode in NUMERIC_BANDING_MODES:
            measured = (
                # A date column is measured in wall-clock days since 1970-01-01.
                frame.select(banding_temporal_ordinal_expr(pl.col(column), dtype).alias(column))
                if _is_date(dtype)
                else frame
            )
            stats = self._numeric(stats, body, measured, column, context)
        else:
            stats = self._categorical(stats, body, frame, column, context)
        return self._with_rule_counts(stats, body, frame, column, dtype, context)

    def _numeric(
        self,
        stats: _BandingStats,
        body: BandingStatsRequest,
        frame: pl.LazyFrame,
        column: str,
        context: ExecutionContext,
    ) -> _BandingStats:
        histogram = equal_width_bins(
            frame, column, bins=body.histogram_bins, execution_context=context
        )
        return replace(
            stats,
            # Everything present that no bin can hold: NaN and infinity.
            non_finite_count=stats.total_rows - stats.null_count - histogram.finite_count,
            minimum=histogram.minimum,
            maximum=histogram.maximum,
            bins=tuple(
                BandingHistogramBin(lower=item.lower, upper=item.upper, count=item.count)
                for item in histogram.bins
            ),
        )

    def _categorical(
        self,
        stats: _BandingStats,
        body: BandingStatsRequest,
        frame: pl.LazyFrame,
        column: str,
        context: ExecutionContext,
    ) -> _BandingStats:
        # The text execution matches on, so every value shown is a value a
        # categorical rule can be written against.
        grouped = cancellable_streaming_collect(
            frame.select(pl.col(column).cast(pl.Utf8).alias("value"))
            .drop_nulls()
            .group_by("value")
            .agg(pl.len().alias("count"))
            .sort(["count", "value"], descending=[True, False]),
            execution_context=context,
        )
        values = tuple(
            BandingValueCount(value=str(row["value"]), count=int(row["count"]))
            for row in grouped.head(body.value_limit).iter_rows(named=True)
        )
        shown = sum(item.count for item in values)
        return replace(
            stats,
            values=values,
            distinct_count=grouped.height,
            other_count=stats.total_rows - stats.null_count - shown,
        )

    def _with_rule_counts(
        self,
        stats: _BandingStats,
        body: BandingStatsRequest,
        frame: pl.LazyFrame,
        column: str,
        dtype: Any,
        context: ExecutionContext,
    ) -> _BandingStats:
        rules = body.factor.get("rules") or []
        if not rules:
            return stats
        try:
            claim = banding_rule_claim_expr(
                pl.col(column),
                dtype,
                _factor_mode(body.factor),
                rules,
                bool(body.factor.get("rightClosed", True)),
                output_column=str(body.factor.get("outputColumn") or ""),
            )
        except ValueError as exc:
            # Execution's own message, so the editor says what a run would say
            # rather than its own account of the same rules.
            raise _unprocessable(str(exc)) from exc
        counted = cancellable_streaming_collect(
            frame.select(claim).group_by("claim").agg(pl.len().alias("count")),
            execution_context=context,
        )
        counts = [0] * len(rules)
        unmatched = 0
        for row in counted.iter_rows(named=True):
            index = row["claim"]
            if index is None:
                unmatched = int(row["count"])
            elif 0 <= int(index) < len(counts):
                counts[int(index)] = int(row["count"])
        return replace(stats, rule_counts=tuple(counts), unmatched_count=unmatched)
