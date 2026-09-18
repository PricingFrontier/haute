"""Equal-width bins over a numeric column.

One definition of "the histogram of this column" for every surface that shows
one, so a Banding editor's distribution and an Explore distribution cannot
disagree about where a value falls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

from haute._execution_context import ExecutionContext
from haute._polars_utils import cancellable_streaming_collect

MIN_HISTOGRAM_BINS = 1
MAX_HISTOGRAM_BINS = 200


@dataclass(frozen=True, slots=True)
class HistogramBin:
    """One `[lower, upper)` interval, or `[lower, upper]` for the last one."""

    lower: float
    upper: float
    count: int


@dataclass(frozen=True, slots=True)
class Histogram:
    """Equal-width bins over the finite values of a column, and their extent."""

    bins: tuple[HistogramBin, ...]
    minimum: float | None
    maximum: float | None
    finite_count: int


def bin_edges(minimum: float, maximum: float, bins: int) -> list[float]:
    """The lower edge of each bin, which is also what a count is measured against.

    Callers on either side of the wire derive the edges this way and then place
    values by comparing against *these* numbers rather than by repeating the
    arithmetic. Computing an index as ``floor((value - low) / width)`` is a
    second calculation that can round differently from the edge it should agree
    with: over 40 bins of ``[0, 1]`` it puts ``0.3`` in the bin whose published
    lower edge is ``0.30000000000000004`` — above the value itself — and lets
    two runtimes disagree about the same number.
    """
    width = (maximum - minimum) / bins
    return [minimum + width * position for position in range(bins)]


def _distinct(edges: list[float]) -> list[float]:
    """The edges that are actually distinct, in order.

    Equal-width arithmetic over a very narrow extent can repeat an edge, and a
    bin whose lower and upper edge are the same number is not an interval any
    value falls in — Polars refuses such a set of breaks outright.
    """
    unique: list[float] = []
    for edge in edges:
        if not unique or edge > unique[-1]:
            unique.append(edge)
    return unique


def equal_width_bins(
    frame: pl.LazyFrame,
    column: str,
    *,
    bins: int,
    execution_context: ExecutionContext,
) -> Histogram:
    """Bin the finite values of *column* into *bins* equal-width intervals.

    Both collections run under the caller's admitted context, so a whole-dataset
    histogram is cancellable and counts against one memory reservation.

    Non-finite values and nulls are not values a bin holds, so the caller counts
    them separately and they never widen the extent. A column with no finite
    value has no bins and no extent; a constant column has one bin holding every
    finite value, because an equal-width split of a zero-width range would put
    the same value in every bin or none of them.

    The last bin is closed at the maximum, so the largest value falls in the last
    bin rather than outside every one.
    """
    if not MIN_HISTOGRAM_BINS <= bins <= MAX_HISTOGRAM_BINS:
        raise ValueError(
            f"histogram bins must be between {MIN_HISTOGRAM_BINS} and {MAX_HISTOGRAM_BINS}"
        )
    # Measured as a float whatever the numeric dtype, including Decimal, which
    # has no `is_finite` of its own and raises on the check below.
    value = pl.col(column).cast(pl.Float64)
    finite = value.is_finite()
    extent = cancellable_streaming_collect(
        frame.select(
            value.filter(finite).min().alias("min"),
            value.filter(finite).max().alias("max"),
            finite.sum().cast(pl.Int64).alias("finite"),
        ),
        execution_context=execution_context,
    )
    minimum = extent.item(0, "min")
    maximum = extent.item(0, "max")
    finite_count = int(extent.item(0, "finite") or 0)
    if minimum is None or maximum is None or finite_count == 0:
        return Histogram(bins=(), minimum=None, maximum=None, finite_count=0)

    low = float(minimum)
    high = float(maximum)
    if low == high:
        return Histogram(
            bins=(HistogramBin(lower=low, upper=high, count=finite_count),),
            minimum=low,
            maximum=high,
            finite_count=finite_count,
        )

    edges = bin_edges(low, high, bins)
    # An extent narrow enough that equal-width edges are not distinct as floats
    # — `110.0` and `110.00000000000001` over 40 bins — cannot be split that
    # finely at all. The bins published are the distinct edges, so every bin
    # shown is one a value can actually fall in.
    edges = _distinct(edges)
    # Placed against the published edges themselves — `[e0, e1)`, …, `[e(n-1), ∞)`
    # — so the largest value is inside the last bin and no value can land in a
    # bin whose lower edge is above it.
    placed = cancellable_streaming_collect(
        frame.filter(finite)
        .select(value.cut(breaks=edges[1:], left_closed=True, include_breaks=True).alias("cut"))
        .unnest("cut")
        .group_by("breakpoint")
        .agg(pl.len().alias("count")),
        execution_context=execution_context,
    )
    # `include_breaks` reports the upper break of each value's interval, which
    # is the next edge, or infinity for the last bin.
    counts = {float(row["breakpoint"]): int(row["count"]) for row in placed.iter_rows(named=True)}
    uppers = [*edges[1:], math.inf]
    last = len(edges) - 1
    return Histogram(
        bins=tuple(
            HistogramBin(
                lower=edge,
                upper=high if position == last else edges[position + 1],
                count=counts.get(uppers[position], 0),
            )
            for position, edge in enumerate(edges)
        ),
        minimum=low,
        maximum=high,
        finite_count=finite_count,
    )
