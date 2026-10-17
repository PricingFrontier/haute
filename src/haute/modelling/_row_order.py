"""The canonical row order every positional selection in training is taken over.

Training selects rows by position: a random split, a random evaluation plan, the
holdout's share of a boundary date, the SHAP, partial-dependence and
actual-against-predicted samples. Over the order a pipeline happens to deliver, the
same seed would select other rows whenever that order changes; over the canonical
order it selects the same rows.

The canonical order sorts the rows by the identity columns, then by every other
column in the frame's order, ascending with nulls last (Polars compares text and
categorical values by their characters, an Enum by its declared levels, and treats
NaN as equal to NaN and -0.0 as equal to 0.0). Rows equal in every column keep
their arrival order; no selection can tell them apart.

The order is built one column at a time, most significant first. A run id, shared by
the rows every column read so far ties, is refined by each further column: a stable
sort by (run id, column), then a new run id wherever either changes. It stops once
every row is distinct, so a unique identity column is read alone. Only the column
being read and the order are held, never the frame: on a 2-million-row, 40-column
frame read from parquet it took 0.4 GB and 2.6 seconds, where one sort over every
column took 2.3 GB, four times the frame.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import polars as pl


def row_order_columns(columns: Sequence[str], id_columns: Sequence[str]) -> list[str]:
    """The columns the canonical order sorts by: the identity columns, then the rest."""
    identity = [column for column in id_columns if column in columns]
    return [*identity, *(column for column in columns if column not in identity)]


def canonical_row_order(
    columns: Sequence[str],
    read_column: Callable[[str], pl.Series],
    height: int,
) -> np.ndarray:
    """The source positions of *height* rows in canonical order, sorted by *columns*.

    *read_column* returns one column of the source, in the source's order; it is
    called once per column, most significant first, until every row is distinct.
    """
    index = pl.get_index_type()
    order = pl.int_range(height, dtype=index, eager=True)
    run = pl.zeros(height, dtype=index, eager=True)
    for column in columns:
        if height < 2 or run[-1] == height - 1:
            break
        values = read_column(column)
        if values.len() != height:
            raise ValueError(
                f"column {column!r} has {values.len()} rows; the canonical order sorts {height}"
            )
        values = values.gather(order).alias("value")
        step = (
            pl.DataFrame([run.alias("run"), values])
            .select(pl.arg_sort_by(["run", "value"], maintain_order=True, nulls_last=True))
            .to_series()
        )
        order = order.gather(step)
        run = run.gather(step)
        values = values.gather(step)
        changed = (run.ne_missing(run.shift()) | values.ne_missing(values.shift())).fill_null(True)
        run = changed.cast(index).cum_sum() - 1
    return order.to_numpy()


def frame_row_order(frame: pl.DataFrame, id_columns: Sequence[str] = ()) -> np.ndarray:
    """The canonical order of an in-memory frame's rows."""
    return canonical_row_order(
        row_order_columns(frame.columns, id_columns),
        frame.get_column,
        frame.height,
    )


def scan_row_order(
    lf: pl.LazyFrame,
    height: int,
    id_columns: Sequence[str],
    *,
    collect: Callable[[pl.LazyFrame], pl.DataFrame],
) -> np.ndarray:
    """The canonical order of a lazy frame's *height* rows, one column per *collect*."""
    return canonical_row_order(
        row_order_columns(lf.collect_schema().names(), id_columns),
        lambda column: collect(lf.select(column)).to_series(),
        height,
    )
