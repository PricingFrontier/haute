"""The per-quote analysis side table and the durable scenario grid (OPT-V09A).

Analysis columns exist only to break a result down by segment; price-contour's
solve and apply outputs carry no passthrough columns, so setup keeps them in
a haute-owned side table, ``quote_analysis.parquet``, with one row per solved
quote. It is written by streaming — a projected scan reduced per quote and
sunk, never collected — and read back only inside a job-store lease.

The scenario grid is recorded once, from the solver input at setup, and is
the only source for which adjustments were possible.

Bounded choice queries (OPT-V09B) read one target's per-quote apply frame --
the as-solved result or a frontier point -- inside leases, join the side
table 1:1 when there is one, and run a reducer in the lazy plan so only a
small result is ever collected. Each run is admitted with its own estimate
and single-flighted by job, frontier generation, target and reducer.

A ratebook target's frame is price-contour's canonical per-quote evaluation
(OPT-V09C): the online columns plus the factor product and the clamp flags,
from which each quote's "deployed factor differs from evaluated step" flag is
read, and a ratebook job's factor rows are a second leased side table that
the factor segments join 1:1.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, cast

from fastapi import HTTPException

from haute._execution_admission import (
    ExecutionAdmissionError,
    WorkEstimate,
    create_admitted_execution_context,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._polars_utils import bounded_sink, streaming_collect
from haute._ram_estimate import decoded_frame_row_width_bytes, estimate_choice_query_peak_bytes
from haute.routes import _optimiser_artifacts
from haute.routes._job_store import ArtifactHandleUnavailableError, JobStore
from haute.routes._optimiser_input import OptimiserSetupError, carried_analysis_column
from haute.routes._shared_flights import SharedFlights

if TYPE_CHECKING:
    import polars as pl

    from haute._execution_context import ExecutionContext
    from haute.routes._optimiser_frontier import OptimiserFrontierService

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
    # Grouped on the key as stored; the table's key is cast to String after. The
    # solver input carries each analysis column as its own uncast copy.
    if analysis_frame is None:
        source = pl.scan_parquet(solver_input_path).select(
            quote_id,
            *(pl.col(carried_analysis_column(column)).alias(column) for column in analysis_columns),
        )
    else:
        source = analysis_frame.select(quote_id, *analysis_columns)
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


# ---------------------------------------------------------------------------
# OPT-V09B: bounded queries over the chosen scenarios
# ---------------------------------------------------------------------------

MAX_CHOICE_ROWS = 1000
"""The most rows (or groups) any reducer returns."""

CHOICE_QUOTE_ID = "quote_id"
"""The apply frame's key column: price-contour always writes it under this name."""

_SAMPLE_ROWS = 512
_TOTAL_COLUMN = "__haute_total"
_TWO_32 = 1 << 32
_CHOICE_OPERATION = "optimiser_choice_query"
_CHOICE_REMEDY = "Raise HAUTE_EXPLORE_MEMORY_LIMIT_MB, or ask for fewer groups or rows."

ChoiceMode = Literal["online", "ratebook"]

DEPLOYED_FACTOR_DIFFERS = "deployed_factor_differs"
"""A ratebook quote whose deployed factor differs from the step the solver evaluated.

Inside the scenario range the Optimiser Apply node deploys the unsnapped product
of the factor rates, while the solve evaluated the nearest step; past a grid end
the deployed factor is collared to that end, the evaluated step (Q17). So the
flag is the frame's own Float32 ``factor_product`` differing from its
``optimal_scenario_value`` on a quote the kernel did not clamp; the product is
never recomputed here.
"""


def apply_frame_schema(mode: ChoiceMode, constraint_names: Sequence[str]) -> dict[str, Any]:
    """The exact ``{column: dtype}`` of a *mode*'s persisted apply frame, in column order.

    Online, price-contour's apply frame: the quote id, the chosen step and scenario
    value, and the objective and each constraint at it. Ratebook, its
    ``quote_results_schema``: the same, then the factor product and the clamp flags.
    """
    import polars as pl

    schema: dict[str, Any] = {
        CHOICE_QUOTE_ID: pl.String(),
        "optimal_step": pl.Int32(),
        "optimal_scenario_value": pl.Float32(),
        "optimal_objective": pl.Float32(),
    }
    for name in constraint_names:
        schema[f"optimal_{name}"] = pl.Float32()
    if mode == "ratebook":
        schema["factor_product"] = pl.Float32()
        schema["clamped_low"] = pl.Boolean()
        schema["clamped_high"] = pl.Boolean()
    elif mode != "online":
        raise ValueError(f"Unknown optimiser mode {mode!r}; expected 'online' or 'ratebook'.")
    return schema


class ChoiceJoinError(RuntimeError):
    """The chosen rows and the analysis side table do not describe the same quotes.

    Both are written for the solved quotes, so a disagreement is a defect,
    raised rather than answered with a partial breakdown.
    """


@dataclass(frozen=True, slots=True)
class ChoiceTarget:
    """What a query reads: the as-solved result (``None``) or one frontier point."""

    point_index: int | None


@dataclass(frozen=True, slots=True)
class ChoiceQueryResult:
    """A reducer's bounded rows, the count they were taken from and the target's quotes."""

    rows: pl.DataFrame
    total: int
    quotes: int


@dataclass(frozen=True, slots=True)
class ChoiceFrameSpec:
    """What a reducer may rely on about the choice frame it runs over.

    ``factor_columns`` are a ratebook solve's factor specs (each named
    ``":".join(columns)``, as the Rates tab names its tables); empty online.
    """

    mode: ChoiceMode
    constraint_names: tuple[str, ...]
    analysis_columns: tuple[str, ...]
    scenario_grid: tuple[tuple[int, float], ...]
    factor_columns: tuple[tuple[str, ...], ...]

    @property
    def value_columns(self) -> tuple[str, ...]:
        """The per-quote values summed into totals: the objective, then each constraint."""
        return ("optimal_objective", *(f"optimal_{name}" for name in self.constraint_names))

    @property
    def apply_schema(self) -> dict[str, Any]:
        return apply_frame_schema(self.mode, self.constraint_names)

    @property
    def choice_columns(self) -> tuple[str, ...]:
        """The choice frame's columns: the apply frame's, and a ratebook quote's flag."""
        flag = (DEPLOYED_FACTOR_DIFFERS,) if self.mode == "ratebook" else ()
        return (*self.apply_schema, *flag)

    @property
    def factor_names(self) -> tuple[str, ...]:
        return tuple(":".join(columns) for columns in self.factor_columns)


Collect = Callable[["pl.LazyFrame"], "pl.DataFrame"]


@dataclass(frozen=True, slots=True)
class ChoiceFrames:
    """The leased choice frame, and the side tables it corresponds to 1:1.

    The correspondence is checked before a reducer runs (``_require_same_quotes``).
    A reducer that needs analysis values for every quote reads ``joined()``; one
    that keeps a few rows attaches them to those rows only (``with_analysis``);
    one that groups by rating factor reads ``with_factors()``, the ratebook factor
    rows being leased only for such a reducer.
    """

    choice: pl.LazyFrame
    analysis: pl.LazyFrame | None
    analysis_key: str
    collect: Collect
    factors: pl.LazyFrame | None = None
    factors_key: str = CHOICE_QUOTE_ID

    def with_factors(self) -> pl.LazyFrame:
        """Every chosen row with its factor levels: an inner 1:1 join in apply order."""
        import polars as pl

        if self.factors is None:
            raise RuntimeError("with_factors() needs the leased ratebook factor rows.")
        return self.choice.join(
            self.factors.with_columns(pl.col(self.factors_key).cast(pl.String)),
            left_on=CHOICE_QUOTE_ID,
            right_on=self.factors_key,
            how="inner",
            validate="1:1",
            maintain_order="left",
        )

    def joined(self) -> pl.LazyFrame:
        """Every chosen row with its analysis values: an inner 1:1 join in apply order."""
        if self.analysis is None:
            return self.choice
        return self.choice.join(
            self.analysis,
            left_on=CHOICE_QUOTE_ID,
            right_on=self.analysis_key,
            how="inner",
            validate="1:1",
            maintain_order="left",
        )

    def with_analysis(self, rows: pl.DataFrame) -> pl.DataFrame:
        """*rows* (a bounded result of the choice frame) with their analysis values attached."""
        import polars as pl

        if self.analysis is None:
            return rows
        found = self.collect(
            self.analysis.filter(pl.col(self.analysis_key).is_in(rows[CHOICE_QUOTE_ID].implode()))
        )
        if found.height != rows.height:
            raise ChoiceJoinError(
                f"The analysis side table holds {found.height} of the {rows.height} requested "
                "quotes; it must hold exactly one row per solved quote."
            )
        return rows.join(
            found,
            left_on=CHOICE_QUOTE_ID,
            right_on=self.analysis_key,
            how="left",
            validate="1:1",
            maintain_order="left",
        )


class ChoiceReducer(Protocol):
    """A bounded reduction of the choice frame, run in its lazy plan."""

    joins_every_quote: ClassVar[bool]
    """Whether the plan joins a whole side table (it groups by analysis values or factors)."""

    reads_factors: ClassVar[bool]
    """Whether the plan reads the ratebook factor rows (``ChoiceFrames.with_factors``)."""

    def validate(self, spec: ChoiceFrameSpec) -> None:
        """Refuse arguments the frame cannot answer, or an unbounded result, with a 400."""

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        """The most rows the result can hold."""

    def scans_every_quote(self) -> bool:
        """Whether the plan reads every chosen row (a page of rows reads only its own)."""

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        """Run the plan over the *row_count* chosen rows and collect its bounded result."""


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _require_row_bound(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CHOICE_ROWS:
        raise _bad_request(f"{name} must be an integer from 1 to {MAX_CHOICE_ROWS}; got {value!r}.")


def _float64_sums(spec: ChoiceFrameSpec) -> list[pl.Expr]:
    import polars as pl

    return [pl.col(column).cast(pl.Float64).sum() for column in spec.value_columns]


NEGATIVE_PREFIX = "negative_"
"""A histogram column counting a step's quotes whose value column is below zero."""


def _negative_counts(spec: ChoiceFrameSpec) -> list[pl.Expr]:
    import polars as pl

    return [
        (pl.col(column) < 0).sum().cast(pl.Int64).alias(f"{NEGATIVE_PREFIX}{column}")
        for column in spec.value_columns
    ]


def _deployed_counts(spec: ChoiceFrameSpec) -> list[pl.Expr]:
    """A ratebook group's count of flagged quotes; nothing online."""
    import polars as pl

    if spec.mode != "ratebook":
        return []
    return [pl.col(DEPLOYED_FACTOR_DIFFERS).sum().cast(pl.Int64).alias(DEPLOYED_FACTOR_DIFFERS)]


def _deployed_columns(spec: ChoiceFrameSpec) -> list[str]:
    return [DEPLOYED_FACTOR_DIFFERS] if spec.mode == "ratebook" else []


def _segment_aggregates(spec: ChoiceFrameSpec) -> list[pl.Expr]:
    """One segment's quotes, mean scenario value, Float64 totals and (ratebook) flag count."""
    import polars as pl

    return [
        pl.len().cast(pl.Int64).alias("quotes"),
        pl.col("optimal_scenario_value").cast(pl.Float64).mean().alias("mean_scenario_value"),
        *_float64_sums(spec),
        *_deployed_counts(spec),
    ]


@dataclass(frozen=True, slots=True)
class ScenarioHistogram:
    """Quotes, Float64 totals and negative-value counts per step of the recorded grid.

    Every step is included, chosen or not.
    """

    joins_every_quote: ClassVar[bool] = False
    reads_factors: ClassVar[bool] = False

    def validate(self, spec: ChoiceFrameSpec) -> None:
        return None

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        return len(spec.scenario_grid)

    def scans_every_quote(self) -> bool:
        return True

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        import polars as pl

        per_step = frames.choice.group_by("optimal_step").agg(
            pl.len().cast(pl.Int64).alias("quotes"),
            *_float64_sums(spec),
            *_negative_counts(spec),
            *_deployed_counts(spec),
        )
        counted_columns = [
            "quotes",
            *spec.value_columns,
            *(f"{NEGATIVE_PREFIX}{column}" for column in spec.value_columns),
            *_deployed_columns(spec),
        ]
        grid = pl.LazyFrame(
            {
                "optimal_step": [step for step, _value in spec.scenario_grid],
                "scenario_value": [value for _step, value in spec.scenario_grid],
            },
            schema={"optimal_step": pl.Int32, "scenario_value": pl.Float64},
        )
        rows = frames.collect(
            grid.join(per_step, on="optimal_step", how="left")
            .with_columns(pl.col(counted_columns).fill_null(0))
            .sort("optimal_step")
        )
        counted = int(rows["quotes"].sum())
        if counted != row_count:
            raise ChoiceJoinError(
                f"{row_count - counted} of {row_count} quotes chose a step outside the "
                "recorded scenario grid."
            )
        return ChoiceQueryResult(rows=rows, total=row_count, quotes=row_count)


def histogram_of_frame(frame: pl.LazyFrame, spec: ChoiceFrameSpec) -> ChoiceQueryResult:
    """``ScenarioHistogram`` over an apply frame already held in memory.

    For the solve's own per-quote frame at finalize, before any artifact
    exists: projected to the choice columns and collected directly (the frame
    is resident, so there is nothing to admit).
    """
    import polars as pl

    choice = _choice_frame(frame, spec)
    row_count = int(choice.select(pl.len()).collect().item())
    frames = ChoiceFrames(
        choice=choice,
        analysis=None,
        analysis_key=CHOICE_QUOTE_ID,
        collect=lambda plan: plan.collect(),
    )
    return ScenarioHistogram().run(frames, spec, row_count)


@dataclass(frozen=True, slots=True)
class SegmentGroupBy:
    """Quotes, mean scenario value and Float64 totals per analysis segment, largest first."""

    columns: tuple[str, ...]
    limit: int

    joins_every_quote: ClassVar[bool] = True
    reads_factors: ClassVar[bool] = False

    def validate(self, spec: ChoiceFrameSpec) -> None:
        _require_row_bound("The group limit", self.limit)
        if not self.columns:
            raise _bad_request("A segment breakdown needs at least one analysis column.")
        if len(set(self.columns)) != len(self.columns):
            raise _bad_request(f"A segment breakdown lists a column twice: {list(self.columns)}.")
        unknown = [column for column in self.columns if column not in spec.analysis_columns]
        if unknown:
            configured = list(spec.analysis_columns) or "none"
            raise _bad_request(
                f"{unknown} are not analysis columns of this solve (configured: {configured}). "
                "Add them to the optimiser's analysis columns and re-run the solve."
            )

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        return self.limit

    def scans_every_quote(self) -> bool:
        return True

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        keys = list(self.columns)
        rows = frames.collect(_largest_groups(frames.joined(), keys, spec, self.limit))
        total = int(rows[_TOTAL_COLUMN][0]) if rows.height else 0
        return ChoiceQueryResult(rows=rows.drop(_TOTAL_COLUMN), total=total, quotes=row_count)


def _largest_groups(
    frame: pl.LazyFrame, keys: list[str], spec: ChoiceFrameSpec, limit: int
) -> pl.LazyFrame:
    """The *limit* largest groups of *frame* by *keys*, then by the keys ascending (nulls last),
    each carrying the number of groups."""
    import polars as pl

    return (
        frame.group_by(keys)
        .agg(_segment_aggregates(spec))
        .with_columns(pl.len().alias(_TOTAL_COLUMN))
        .sort(["quotes", *keys], descending=[True, *([False] * len(keys))], nulls_last=True)
        .head(limit)
    )


@dataclass(frozen=True, slots=True)
class FactorSegments:
    """Quotes, mean scenario value, Float64 totals and flag counts per level of one rating
    factor of a ratebook result, largest first.

    *factor* is the factor spec's name as the Rates tab names it (``":".join(columns)``);
    a composite factor is grouped by all its columns, and each level is labelled as the
    Rates tab labels it (``__factor_group__``).
    """

    factor: str
    limit: int

    joins_every_quote: ClassVar[bool] = True
    reads_factors: ClassVar[bool] = True

    def validate(self, spec: ChoiceFrameSpec) -> None:
        _require_row_bound("The level limit", self.limit)
        if spec.mode != "ratebook":
            raise _bad_request(
                "A breakdown by rating factor exists only for ratebook results; this is an "
                f"{spec.mode} result."
            )
        if self.factor not in spec.factor_names:
            raise _bad_request(
                f"{self.factor!r} is not a rating factor of this solve (factors: "
                f"{list(spec.factor_names)})."
            )

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        return self.limit

    def scans_every_quote(self) -> bool:
        return True

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        import polars as pl

        from haute.routes._optimiser_solver import _ratebook_factor_level_key

        columns = list(spec.factor_columns[spec.factor_names.index(self.factor)])
        if frames.factors is None:
            raise RuntimeError("Factor segments need the leased ratebook factor rows.")
        factor_schema = frames.factors.collect_schema()
        dtypes = [factor_schema[column] for column in columns]
        grouped = frames.collect(_largest_groups(frames.with_factors(), columns, spec, self.limit))
        total = int(grouped[_TOTAL_COLUMN][0]) if grouped.height else 0
        levels = [
            _ratebook_factor_level_key([row[column] for column in columns], dtypes)
            for row in grouped.select(columns).iter_rows(named=True)
        ]
        rows = grouped.drop(_TOTAL_COLUMN, *columns).insert_column(
            0, pl.Series("level", levels, dtype=pl.String)
        )
        return ChoiceQueryResult(rows=rows, total=total, quotes=row_count)


def ranked(frame: pl.LazyFrame, by: str, descending: bool) -> pl.LazyFrame:
    """*frame* sorted by *by*, ties broken by quote id ascending in either direction.

    Nulls sort last both ways, so a page of equal values is always the same rows.
    """
    return frame.sort([by, CHOICE_QUOTE_ID], descending=[descending, False], nulls_last=True)


@dataclass(frozen=True, slots=True)
class TopK:
    """The *k* quotes with the largest (or smallest) chosen value of *by*; ties by quote id."""

    by: str
    k: int
    descending: bool = True

    joins_every_quote: ClassVar[bool] = False
    reads_factors: ClassVar[bool] = False

    def validate(self, spec: ChoiceFrameSpec) -> None:
        _require_row_bound("k", self.k)
        rankable = ("optimal_scenario_value", *spec.value_columns)
        if self.by not in rankable:
            raise _bad_request(f"Quotes can be ranked by {list(rankable)}; got {self.by!r}.")

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        return self.k

    def scans_every_quote(self) -> bool:
        return True

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        rows = frames.collect(ranked(frames.choice, self.by, self.descending).head(self.k))
        return ChoiceQueryResult(rows=frames.with_analysis(rows), total=row_count, quotes=row_count)


@dataclass(frozen=True, slots=True)
class RowIndex:
    """The rows at positions ``[offset, offset + limit)`` of the apply frame's quote order."""

    offset: int
    limit: int

    joins_every_quote: ClassVar[bool] = False
    reads_factors: ClassVar[bool] = False

    def validate(self, spec: ChoiceFrameSpec) -> None:
        _require_row_bound("The row limit", self.limit)
        if isinstance(self.offset, bool) or not isinstance(self.offset, int) or self.offset < 0:
            raise _bad_request(
                f"The row offset must be a non-negative integer; got {self.offset!r}."
            )

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        return self.limit

    def scans_every_quote(self) -> bool:
        return False

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        rows = frames.collect(frames.choice.slice(self.offset, self.limit))
        return ChoiceQueryResult(rows=frames.with_analysis(rows), total=row_count, quotes=row_count)


@contextmanager
def lease_apply_frame(
    store: JobStore,
    job_id: str,
    key: str,
    *,
    unavailable_detail: str | Mapping[str, str],
) -> Iterator[pl.LazyFrame]:
    """Scan the job's apply artifact under *key* while holding a lease on it.

    Collect only inside the block. A job or handle that is gone is a 410 with
    *unavailable_detail*.
    """
    with ExitStack() as stack:
        try:
            handle = stack.enter_context(store.lease(job_id, key))
        except ArtifactHandleUnavailableError as exc:
            raise HTTPException(status_code=410, detail=unavailable_detail) from exc
        yield _optimiser_artifacts._scan_apply_result_artifact(handle)


def _sample_width(frame: pl.LazyFrame) -> float:
    """The decoded width of one row, from a bounded sample of the first rows."""
    return decoded_frame_row_width_bytes(frame.head(_SAMPLE_ROWS).collect())


def _choice_frame(apply: pl.LazyFrame, spec: ChoiceFrameSpec) -> pl.LazyFrame:
    """The target's choice frame: its apply frame, which must have exactly its mode's
    schema, and for a ratebook target each quote's ``deployed_factor_differs`` flag."""
    import polars as pl

    expected = spec.apply_schema
    actual = dict(apply.collect_schema())
    if list(actual.items()) != list(expected.items()):
        missing = [column for column in expected if column not in actual]
        unexpected = [column for column in actual if column not in expected]
        mistyped = [
            f"{column} ({actual[column]}, expected {dtype})"
            for column, dtype in expected.items()
            if column in actual and actual[column] != dtype
        ]
        raise ChoiceJoinError(
            f"The {spec.mode} apply result does not have the {spec.mode} apply-frame schema: "
            f"missing {missing}, unexpected {unexpected}, mistyped {mistyped}, "
            f"column order {list(actual)} (expected {list(expected)})."
        )
    if spec.mode != "ratebook":
        return apply
    return apply.with_columns(
        (
            (pl.col("factor_product") != pl.col("optimal_scenario_value"))
            & ~pl.col("clamped_low")
            & ~pl.col("clamped_high")
        ).alias(DEPLOYED_FACTOR_DIFFERS)
    )


def _key_fingerprint(frame: pl.LazyFrame, key: str, collect: Collect) -> tuple[int, int, int]:
    """Rows, and the sums of the high and low halves of each key's 64-bit hash.

    Two key columns of distinct values with equal fingerprints hold the same
    keys, but for a hash-sum collision (about 2^-64). One streamed aggregate:
    nothing per key is held, unlike a join.
    """
    import polars as pl

    hashed = pl.col(key).cast(pl.String).hash(seed=0)
    return cast(
        tuple[int, int, int],
        collect(
            frame.select(
                pl.len().cast(pl.UInt64).alias("rows"),
                (hashed // _TWO_32).sum().alias("high"),
                (hashed % _TWO_32).sum().alias("low"),
            )
        ).row(0),
    )


def _require_same_quotes(
    frames: ChoiceFrames, side: pl.LazyFrame, key: str, row_count: int, table: str
) -> None:
    """Assert the *table* side table holds exactly the chosen rows' quotes, one row each."""
    chosen = _key_fingerprint(frames.choice, CHOICE_QUOTE_ID, frames.collect)
    fingerprint = _key_fingerprint(side, key, frames.collect)
    if fingerprint[0] != row_count or fingerprint != chosen:
        raise ChoiceJoinError(
            f"The {table} ({fingerprint[0]} rows) does not hold the {row_count} chosen "
            "quotes one row each; it must hold exactly one row per solved quote."
        )


class ChoiceQueryService:
    """Bounded, admitted, single-flighted queries over one job's chosen scenarios."""

    def __init__(self, store: JobStore, frontier: OptimiserFrontierService) -> None:
        self._store = store
        self._frontier = frontier
        self._flights: SharedFlights[tuple[Any, ...], ChoiceQueryResult] = SharedFlights()

    def choice_query(
        self,
        job_id: str,
        target: ChoiceTarget,
        reducer: ChoiceReducer,
        *,
        cancellation_token: ExecutionCancellationToken | None = None,
    ) -> ChoiceQueryResult:
        """Answer *reducer* over *target*'s chosen scenarios with a bounded result.

        A frontier point without an artifact is materialised first (through
        the job's point queue). A disconnected caller (*cancellation_token*)
        detaches from the shared run without stopping it for the others.
        """
        from haute.routes._optimiser_frontier import _frontier_generation_or_raise

        job = self._store.require_completed_job(job_id)
        spec = _choice_spec(job)
        reducer.validate(spec)
        if target.point_index is None:
            generation = _frontier_generation_or_raise(job)
            handle_key = _optimiser_artifacts._APPLY_RESULT_HANDLE_KEY
            unavailable: str | Mapping[str, str] = (
                _optimiser_artifacts.APPLY_RESULT_UNAVAILABLE_DETAIL
            )
        else:
            ticket = self._frontier.request_point_apply(job_id, target.point_index)
            ticket.wait(cancellation_token)
            generation, handle_key = ticket.generation, ticket.handle_key
            unavailable = _optimiser_artifacts.frontier_point_unavailable_detail(
                target.point_index, grid_expired=False
            )
        subscription = self._flights.subscribe(
            (job_id, generation, target, reducer),
            lambda token: self._run(job_id, handle_key, unavailable, reducer, token),
        )
        return subscription.wait(cancellation_token, operation=_CHOICE_OPERATION)

    def _run(
        self,
        job_id: str,
        handle_key: str,
        unavailable: str | Mapping[str, str],
        reducer: ChoiceReducer,
        token: ExecutionCancellationToken,
    ) -> ChoiceQueryResult:
        import polars as pl

        job = self._store.require_completed_job(job_id)
        spec = _choice_spec(job)
        context: ExecutionContext | None = None
        try:
            with ExitStack() as stack:
                apply = stack.enter_context(
                    lease_apply_frame(
                        self._store, job_id, handle_key, unavailable_detail=unavailable
                    )
                )
                choice = _choice_frame(apply, spec)
                row_count = int(choice.select(pl.len()).collect().item())
                analysis: pl.LazyFrame | None = None
                factors: pl.LazyFrame | None = None
                side_widths: list[float] = []
                if spec.analysis_columns:
                    analysis = self._leased_side_table(
                        stack,
                        job_id,
                        _optimiser_artifacts._QUOTE_ANALYSIS_HANDLE_KEY,
                        row_count,
                        unavailable=_optimiser_artifacts.QUOTE_ANALYSIS_UNAVAILABLE_DETAIL,
                        scan=_optimiser_artifacts._quote_analysis_path,
                    )
                    side_widths.append(_sample_width(analysis))
                if reducer.reads_factors:
                    factors = self._leased_side_table(
                        stack,
                        job_id,
                        _optimiser_artifacts._RATEBOOK_FACTORS_HANDLE_KEY,
                        row_count,
                        unavailable=_optimiser_artifacts.RATEBOOK_FACTORS_UNAVAILABLE_DETAIL,
                        scan=_optimiser_artifacts._ratebook_factors_path,
                    )
                    side_widths.append(_sample_width(factors))
                context = create_admitted_execution_context(
                    operation=_CHOICE_OPERATION,
                    profile=ExecutionProfile.EXPLORE_ANALYSIS,
                    job_id=job_id,
                    cancellation_token=token,
                    estimate=WorkEstimate(
                        estimated_bytes=estimate_choice_query_peak_bytes(
                            row_count=row_count,
                            choice_row_width_bytes=_sample_width(choice),
                            side_row_width_bytes=sum(side_widths) if side_widths else None,
                            scans_every_quote=reducer.scans_every_quote(),
                            joins_every_quote=reducer.joins_every_quote,
                            result_rows=reducer.result_rows(spec),
                        ),
                        subject=f"The optimiser choice query ({type(reducer).__name__})",
                        remedy=_CHOICE_REMEDY,
                    ),
                )
                admitted = context

                def collect(plan: pl.LazyFrame) -> pl.DataFrame:
                    return streaming_collect(plan, execution_context=admitted)

                quote_key = _configured_quote_id(job)
                frames = ChoiceFrames(
                    choice=choice,
                    analysis=analysis,
                    analysis_key=quote_key,
                    collect=collect,
                    factors=factors,
                    factors_key=quote_key,
                )
                if analysis is not None:
                    _require_same_quotes(
                        frames, analysis, quote_key, row_count, "analysis side table"
                    )
                if factors is not None:
                    _require_same_quotes(
                        frames, factors, quote_key, row_count, "ratebook factor table"
                    )
                return reducer.run(frames, spec, row_count)
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            raise HTTPException(status_code=507, detail=exc.to_payload()) from None
        finally:
            if context is not None:
                context.release_admission()

    def _leased_side_table(
        self,
        stack: ExitStack,
        job_id: str,
        key: str,
        row_count: int,
        *,
        unavailable: str,
        scan: Callable[[dict[str, Any]], Path],
    ) -> pl.LazyFrame:
        """Lease the job's side table under *key* and scan it; its rows must be the chosen rows."""
        import polars as pl

        try:
            handle = stack.enter_context(self._store.lease(job_id, key))
        except ArtifactHandleUnavailableError as exc:
            raise HTTPException(status_code=410, detail=unavailable) from exc
        if handle["row_count"] != row_count:
            raise ChoiceJoinError(
                f"The {key} side table records {handle['row_count']} rows for {row_count} "
                "chosen rows; it must hold exactly one row per solved quote."
            )
        return pl.scan_parquet(scan(handle))


def _configured_quote_id(job: Mapping[str, Any]) -> str:
    """The quote-id column the side tables are keyed by (the configured one)."""
    return str(job["config"].get("quote_id") or CHOICE_QUOTE_ID)


def _choice_spec(job: Mapping[str, Any], mode: ChoiceMode | None = None) -> ChoiceFrameSpec:
    """The job's choice-frame description, refusing a side column the join cannot keep.

    *mode* is the solve's; a completed job's is read from it (``None``). The solve's
    finalize passes its own, before the job records a result.
    """
    from haute.routes._optimiser_frontier import _artifact_handles_or_raise, _job_mode

    resolved_mode = _job_mode(job) if mode is None else mode
    if resolved_mode not in ("online", "ratebook"):
        raise ValueError(f"Unknown optimiser mode {resolved_mode!r}.")
    config = job["config"]
    constraints = config.get("constraints") or {}
    handle = _artifact_handles_or_raise(job).get(_optimiser_artifacts._QUOTE_ANALYSIS_HANDLE_KEY)
    analysis_columns = tuple(handle["columns"]) if isinstance(handle, Mapping) else ()
    factor_columns = (
        tuple(tuple(str(column) for column in group) for group in config["factor_columns"])
        if resolved_mode == "ratebook"
        else ()
    )
    spec = ChoiceFrameSpec(
        mode=cast(ChoiceMode, resolved_mode),
        constraint_names=tuple(str(name) for name in constraints),
        analysis_columns=analysis_columns,
        scenario_grid=tuple(
            (int(step["optimal_step"]), float(step["scenario_value"]))
            for step in require_scenario_grid(job)
        ),
        factor_columns=factor_columns,
    )
    choice_columns = set(spec.choice_columns)
    for kind, columns in (
        ("Analysis", analysis_columns),
        ("Rating factor", tuple(column for group in factor_columns for column in group)),
    ):
        clashing = sorted(set(columns) & choice_columns)
        if clashing:
            raise _bad_request(
                f"{kind} columns {clashing} have the names of chosen-scenario columns, so a "
                "breakdown cannot keep both. Rename them upstream and re-run the solve."
            )
    return spec
