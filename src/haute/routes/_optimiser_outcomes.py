"""The per-quote analysis side table and the durable scenario grid (OPT-V09A).

Analysis columns exist only to break a result down by segment; price-contour's
solve and apply outputs carry no passthrough columns, so setup keeps them in
a haute-owned side table, ``quote_analysis.parquet``, with one row per solved
quote. It is written by streaming — a projected scan reduced per quote and
sunk, never collected — and read back only inside a job-store lease.

The scenario grid is recorded once, from the solver input at setup, and is
the only source for which adjustments were possible.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from haute._polars_utils import bounded_sink, streaming_collect
from haute.routes import _optimiser_artifacts
from haute.routes._job_store import ArtifactHandleUnavailableError, JobStore
from haute.routes._optimiser_input import OptimiserSetupError

if TYPE_CHECKING:
    import polars as pl

    from haute._execution_context import ExecutionContext

ANALYSIS_ROW_PRESENT_COLUMN = "__haute_analysis_row_present"
"""False for a solved quote the chosen analysis frame has no row for (its "Missing" level)."""

_STRING_DTYPE_NAMES = ("String", "Categorical", "Enum")


class AnalysisColumnNotConstantError(OptimiserSetupError):
    """An analysis column holds more than one value for some quote."""

    def __init__(self, varying: Mapping[str, int], *, column: str, example_quote: str) -> None:
        described = ", ".join(
            f"{name!r} ({count} quote{'s' if count != 1 else ''})"
            for name, count in varying.items()
        )
        super().__init__(
            400,
            f"Analysis columns vary within a quote: {described}. Each analysis column must "
            f"hold one value per quote; for example quote {example_quote!r} has more than one "
            f"{column!r} value. Choose columns that are constant per quote, or fix the "
            "upstream join that repeats a quote with different values.",
        )
        self.varying = dict(varying)


_PER_QUOTE_FILENAME = "per_quote.parquet"


def _varies_column(index: int) -> str:
    return f"__haute_varies_{index}"


def _varies_within_group(column: str) -> pl.Expr:
    """Whether a group holds two different values: ``n_unique > 1`` without a per-group set.

    The smallest and largest 64-bit value hash (``Expr.hash``, seed 0) differ
    exactly when two values differ, a null and a NaN each hashing to one
    value, as ``n_unique`` counts them. Measured against ``n_unique`` at 1M
    quotes x 10 steps it holds about 300 MiB less; two different values of
    one quote colliding (probability about 2^-64 per pair) is the only miss.
    """
    import polars as pl

    hashed = pl.col(column).hash()
    return hashed.min() != hashed.max()


def _require_constant_within_quote(
    per_quote_path: Path,
    quote_id: str,
    columns: Sequence[str],
    execution_context: ExecutionContext | None,
) -> None:
    """Refuse a column with two values for one quote, from the per-quote reduction.

    The flags were computed by the one streamed ``group_by``
    (``_varies_within_group``); they are summed to one row before collecting.
    """
    import polars as pl

    per_quote = pl.scan_parquet(per_quote_path)
    counts = streaming_collect(
        per_quote.select(
            [
                pl.col(_varies_column(index)).sum().alias(column)
                for index, column in enumerate(columns)
            ]
        ),
        execution_context=execution_context,
    ).row(0, named=True)
    varying = {column: int(counts[column]) for column in columns if counts[column]}
    if not varying:
        return
    first = next(iter(varying))
    example = streaming_collect(
        per_quote.filter(pl.col(_varies_column(list(columns).index(first))))
        .select(quote_id)
        .sort(quote_id)
        .head(1),
        execution_context=execution_context,
    ).item()
    raise AnalysisColumnNotConstantError(varying, column=first, example_quote=str(example))


def _column_stats(
    schema: pl.Schema,
    counts: Mapping[str, Any],
    columns: Sequence[str],
) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for column in columns:
        dtype = schema[column]
        max_bytes = counts[f"max_bytes:{column}"]
        stats[column] = {
            "dtype": str(dtype.base_type()),
            "approx_n_unique": int(counts[f"approx_n_unique:{column}"]),
            "max_string_bytes": None if max_bytes is None else int(max_bytes),
        }
    return stats


def write_quote_analysis(
    *,
    solver_input_path: str,
    analysis_frame: pl.LazyFrame | None,
    quote_id: str,
    columns: Sequence[str],
    directory: Path | None = None,
    execution_context: ExecutionContext | None,
) -> dict[str, Any]:
    """Stream ``quote_analysis.parquet`` and return its handle.

    The columns come from the written solver input (the data-input path,
    *analysis_frame* ``None``) or from the projected side-input analysis frame,
    which is left-joined to the solved quotes so the table holds exactly
    those quotes. One streamed ``group_by`` reduces the source to a per-quote
    file carrying each column's first value and whether it varies; the check
    and the table read that small file.

    A setup worker writes into the *directory* its parent created and owns: on
    failure it removes only its own files and leaves the directory to the
    parent. Without one, a fresh directory is created and removed on failure.
    The caller owns the returned handle.
    """
    import polars as pl

    analysis_columns = list(columns)
    # Grouped on the key as stored; the table's key is cast to String after.
    source = (
        pl.scan_parquet(solver_input_path) if analysis_frame is None else analysis_frame
    ).select(quote_id, *analysis_columns)
    per_quote = source.group_by(quote_id).agg(
        [pl.col(column).first() for column in analysis_columns]
        + [
            _varies_within_group(column).alias(_varies_column(index))
            for index, column in enumerate(analysis_columns)
        ]
    )
    owns_directory = directory is None
    if directory is None:
        directory = _optimiser_artifacts._new_quote_analysis_directory()
    handle = _optimiser_artifacts._quote_analysis_handle(directory)
    per_quote_path = directory / _PER_QUOTE_FILENAME
    try:
        bounded_sink(per_quote, per_quote_path)
        _require_constant_within_quote(
            per_quote_path, quote_id, analysis_columns, execution_context
        )
        reduced = pl.scan_parquet(per_quote_path).select(
            pl.col(quote_id).cast(pl.String),
            *analysis_columns,
            pl.lit(True).alias(ANALYSIS_ROW_PRESENT_COLUMN),
        )
        if analysis_frame is None:
            table = reduced
        else:
            solved = (
                pl.scan_parquet(solver_input_path)
                .select(quote_id)
                .unique()
                .select(pl.col(quote_id).cast(pl.String))
            )
            table = solved.join(reduced, on=quote_id, how="left").with_columns(
                pl.col(ANALYSIS_ROW_PRESENT_COLUMN).fill_null(False)
            )
        bounded_sink(
            table.select(quote_id, *analysis_columns, ANALYSIS_ROW_PRESENT_COLUMN),
            handle["path"],
        )
        per_quote_path.unlink()
        written = pl.scan_parquet(handle["path"])
        schema = written.collect_schema()
        summary_exprs: list[pl.Expr] = [
            pl.len().alias("row_count"),
            (~pl.col(ANALYSIS_ROW_PRESENT_COLUMN)).sum().alias("missing_quote_count"),
        ]
        for column in analysis_columns:
            summary_exprs.append(
                pl.col(column).approx_n_unique().alias(f"approx_n_unique:{column}")
            )
            if schema[column].base_type().__name__ in _STRING_DTYPE_NAMES:
                max_bytes = pl.col(column).cast(pl.String).str.len_bytes().max()
            else:
                max_bytes = pl.lit(None, dtype=pl.UInt32)
            summary_exprs.append(max_bytes.alias(f"max_bytes:{column}"))
        summary = streaming_collect(
            written.select(summary_exprs), execution_context=execution_context
        ).row(0, named=True)
    except BaseException:
        if owns_directory:
            shutil.rmtree(directory, ignore_errors=True)
        else:
            per_quote_path.unlink(missing_ok=True)
            Path(handle["path"]).unlink(missing_ok=True)
        raise
    handle.update(
        row_count=int(summary["row_count"]),
        columns=analysis_columns,
        analysis_source="data_input" if analysis_frame is None else "side_input",
        missing_quote_count=int(summary["missing_quote_count"]),
        column_stats=_column_stats(schema, summary, analysis_columns),
    )
    return handle


def require_one_row_per_solved_quote(handle: Mapping[str, Any], n_quotes: int) -> None:
    """The table must hold exactly the grid's quotes; a mismatch is a defect, raised."""
    if handle["row_count"] != n_quotes:
        raise RuntimeError(
            f"quote_analysis.parquet has {handle['row_count']} rows for {n_quotes} solved "
            "quotes; the analysis table must hold exactly one row per solved quote."
        )


def collect_quote_analysis(
    store: JobStore,
    job_id: str,
    query: Callable[[pl.LazyFrame], pl.LazyFrame],
    *,
    execution_context: ExecutionContext | None = None,
) -> pl.DataFrame:
    """Collect *query* over the job's analysis table while holding a lease on it.

    A job or table that is gone is the stable 410 "re-run the solve".
    """
    import polars as pl

    try:
        with store.lease(job_id, _optimiser_artifacts._QUOTE_ANALYSIS_HANDLE_KEY) as handle:
            path = _optimiser_artifacts._quote_analysis_path(handle)
            return streaming_collect(
                query(pl.scan_parquet(path)), execution_context=execution_context
            )
    except ArtifactHandleUnavailableError as exc:
        raise HTTPException(
            status_code=410, detail=_optimiser_artifacts.QUOTE_ANALYSIS_UNAVAILABLE_DETAIL
        ) from exc


def scenario_grid_from_values(values: Sequence[float]) -> list[dict[str, int | float]]:
    """The complete grid as ``[{optimal_step, scenario_value}]``, in step order.

    *values* are the solver input's grid (``QuoteGrid.scenario_values``, Float32
    widened): exactly what the solver scored.
    """
    grid = [float(value) for value in values]
    if not grid:
        raise ValueError("The solver input has an empty scenario grid.")
    if not all(math.isfinite(value) for value in grid):
        raise ValueError(f"The solver input's scenario grid has a non-finite value: {grid}.")
    if any(later <= earlier for earlier, later in pairwise(grid)):
        raise ValueError(f"The solver input's scenario grid is not strictly increasing: {grid}.")
    return [{"optimal_step": step, "scenario_value": value} for step, value in enumerate(grid)]


def require_scenario_grid(job: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The scenario grid setup recorded on *job*; its absence is a defect, not a default."""
    grid = job.get("scenario_grid")
    if not isinstance(grid, list) or not grid:
        raise KeyError(
            "Optimiser solve job has no scenario_grid; setup records it from the solver "
            "input before the solve starts."
        )
    return [dict(step) for step in grid]
