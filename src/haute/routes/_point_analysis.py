"""What the request-time analyses of a node's data point share.

Banding statistics and rating levels each read the whole data point a node reads,
under one admitted lease, cached per data version. Both resolve the consumer's point
with its column demand widened by the columns the request asks about, answer
``cache_required`` when the point is not current, refuse (422) a column the data lacks,
and count a column's most common values. That skeleton is here once; each service keeps
only what it computes.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from typing import Any, Protocol, TypeVar

import polars as pl

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
from haute.errors import InvalidRequestError, UnprocessableError
from haute.routes._node_data_service import NodeDataService, node_data_project_root
from haute.routes._synchronous_analysis import run_synchronous_analysis
from haute.schemas import Graph, NodeDataPointResponse

_Result = TypeVar("_Result")


class PointRequest(Protocol):
    """What a point analysis is asked about: the node, in its graph, on its source."""

    @property
    def graph(self) -> Graph: ...

    @property
    def node_id(self) -> str: ...

    @property
    def source(self) -> str: ...


def unprocessable(message: str) -> UnprocessableError:
    """A question this data cannot answer.

    The repository keeps a refusal's detail a plain string (issue #76), so each
    condition is told apart by its own message rather than by a code in the body.
    """
    return UnprocessableError(message)


def columns_missing(columns: Collection[str]) -> UnprocessableError:
    """The data the node reads has none of *columns*."""
    names = ", ".join(repr(column) for column in columns)
    if len(columns) == 1:
        return unprocessable(f"Column {names} is not in the data this node reads.")
    return unprocessable(f"Columns {names} are not in the data this node reads.")


def most_common_values(
    frame: pl.LazyFrame,
    value: pl.Expr,
    *,
    limit: int,
    context: ExecutionContext,
    keep: pl.Expr | None = None,
) -> tuple[list[tuple[str, int]], int]:
    """A column's *limit* most common values with their counts, and how many there are.

    Most common first, ties by value, so what the cap keeps is what the data is mostly
    made of. *value* is aliased ``value`` and nulls never count; *keep*, over that
    column, filters what does. The sort and the cap run in the lazy plan, and the
    distinct count is its own ``n_unique``, so a column of millions of distinct values
    returns only the rows shown.
    """
    values = frame.select(value.alias("value")).drop_nulls()
    if keep is not None:
        values = values.filter(keep)
    top = cancellable_streaming_collect(
        values.group_by("value")
        .agg(pl.len().alias("count"))
        .sort(["count", "value"], descending=[True, False])
        .head(limit),
        execution_context=context,
    )
    distinct = cancellable_streaming_collect(
        values.select(pl.col("value").n_unique().alias("distinct")),
        execution_context=context,
    )
    return (
        [(str(row["value"]), int(row["count"])) for row in top.iter_rows(named=True)],
        int(distinct.item(0, "distinct")),
    )


class PointAnalysisService:
    """A short whole-dataset analysis over the node's shared data point."""

    def __init__(self, node_data: NodeDataService) -> None:
        self._node_data = node_data
        self._cache: SynchronousAnalysisCache = SynchronousAnalysisCache()

    def _analyse(
        self,
        body: PointRequest,
        columns: Collection[str],
        *,
        operation: str,
        request: Mapping[str, Any],
        compute: Callable[[LeasedPointFrame, ExecutionContext], _Result],
        cancellation_token: ExecutionCancellationToken | None,
    ) -> tuple[NodeDataPointResponse, _Result | None]:
        """The point *body* asks about and *compute*'s result over it, or ``None`` when the
        point must be cached first.

        The token is the request's, so a browser that abandons the question (an editor
        supersedes its own request on every edit) stops the scan rather than leaving it
        holding an admission and a lease for an answer nobody will read.
        """
        try:
            consumer = consumer_point(body.graph, body.node_id)
        except NodeDataPointInvalidError as exc:
            raise InvalidRequestError(str(exc)) from exc
        resolver = DataPointResolver(
            body.graph, source=body.source, store=NodeSnapshotStore(node_data_project_root())
        )
        # What is being edited may name columns the saved node does not read, so the
        # demand covers them: a point without one of them is not current for this
        # request rather than answering from data that lacks it.
        reader = ConsumerPoint(
            consumer.consumer_node_id,
            consumer.point,
            consumer.demand
            if consumer.demand.names is None
            else NodeSnapshotColumns.of({*consumer.demand.names, *columns}),
        )
        point = self._node_data.point_for(reader, resolver)
        try:
            result = run_synchronous_analysis(
                resolver,
                reader.point,
                reader.demand,
                operation=operation,
                request=dict(request),
                cache=self._cache,
                compute=compute,
                cancellation_token=cancellation_token,
            )
        except (CacheRequiredError, PointDataChangedError):
            return point, None
        except PointColumnsMissingError as exc:
            # A narrower *snapshot* resolves partial above and asks to be cached;
            # reaching here means the data itself has no such column.
            raise columns_missing(exc.missing) from exc
        return point, result
