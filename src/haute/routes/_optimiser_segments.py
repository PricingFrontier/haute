"""Segment breakdowns of the chosen scenarios (OPT-V11).

For one key -- an analysis column, or a ratebook result's rating factor -- each
level's quotes and chosen scenario values against 1.0, the unadjusted base
price: the mean (weighted and unweighted), the shares adjusted up and down and
the share at the edge of the scenario range. It describes the solution only;
nothing is compared with current or deployed pricing.

The key catalogue and its cardinality gate are decided once, at finalize, from
metadata already held (the side table's ``column_stats`` and the factor
tables); the reducers run through ``ChoiceQueryService.choice_query`` like the
other OPT-V09B reducers, so admission, single-flight, point materialisation and
availability are theirs. Every level figure is an additive Float64 sum of the
Float32 choice columns, summed in the lazy plan; only a handful of rows is
collected.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from fastapi import HTTPException

from haute.routes._optimiser_adjustments import (
    BASE_PRICE,
    QUOTES_WEIGHT,
    cache_point_report,
    candidate_weightings,
)
from haute.routes._optimiser_outcomes import (
    ANALYSIS_ROW_PRESENT_COLUMN,
    DEPLOYED_FACTOR_DIFFERS,
    ChoiceFrames,
    ChoiceFrameSpec,
    ChoiceJoinError,
    ChoiceQueryResult,
    ChoiceQueryService,
    ChoiceTarget,
    _bad_request,
    _choice_spec,
)
from haute.schemas import (
    OptimiserSegmentIndexResponse,
    OptimiserSegmentKey,
    OptimiserSegmentsResponse,
)

if TYPE_CHECKING:
    import polars as pl

    from haute._execution_context import ExecutionCancellationToken
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_frontier import OptimiserFrontierService

Binning = Literal["numeric", "categorical"]

MAX_NUMERIC_BINS = 20
"""The most quantile bins a numeric key is cut into."""

MAX_CATEGORICAL_LEVELS = 15
"""The largest levels a categorical key lists; the rest are summed into Other."""

MAX_SEGMENT_LEVELS = 2000
"""The most distinct levels a categorical key may have, checked exactly before truncation."""

ADMITTED_APPROX_LEVELS = 1800
"""The HyperLogLog estimate above which a categorical analysis column is refused.

A margin under ``MAX_SEGMENT_LEVELS`` for the estimate's error: ``k0``..``k2000``
(2,001 values) estimates as 1,980.
"""

MAX_SEGMENT_KEY_BYTES = 256
"""The longest level value, in UTF-8 bytes, a key may have."""

MAX_FACTOR_SEGMENT_KEYS = 30
"""The most rating factors that can be broken down; later ones are listed as unavailable."""

SEGMENT_INDEXES_KEY = "segment_indexes"
"""The job field caching indexes by ``(frontier_generation, point_index)``."""

MAX_CACHED_SEGMENT_INDEXES = 64
"""The most indexes one job keeps; the oldest is dropped first."""

MISSING_LABEL = "Missing"
OTHER_LABEL = "Other"

SPREAD_LABEL = "Adjustment spread"
SPREAD_DESCRIPTION = (
    "How differently the optimiser adjusted the key's levels: the quote-weighted standard "
    "deviation of the levels' mean chosen scenario values."
)

_NUMERIC_DTYPES = frozenset(
    {
        "Int8",
        "Int16",
        "Int32",
        "Int64",
        "Int128",
        "UInt8",
        "UInt16",
        "UInt32",
        "UInt64",
        "Float32",
        "Float64",
        "Decimal",
    }
)

# The additive per-level sums every breakdown collects.
_QUOTES = "quotes"
_SV_SUM = "sv_sum"
_UP = "up"
_DOWN = "down"
_EDGE = "edge"
_W_TOTAL = "w_total"
_W_SV = "w_sv"
_W_UP = "w_up"
_W_DOWN = "w_down"
_W_EDGE = "w_edge"
_W_NEGATIVE = "w_negative"
_BIN = "__haute_bin"
_LEVEL = "__haute_level"
_RANK = "__haute_rank"
_BUCKET = "__haute_bucket"
_N_LEVELS = "__haute_n_levels"
_MERGED = "__haute_merged"
_WEIGHT = "__haute_weight"


class SegmentLevelsExceededError(ValueError):
    """A categorical key has more distinct levels than a breakdown may group."""


# ---------------------------------------------------------------------------
# The key catalogue and its cardinality gate
# ---------------------------------------------------------------------------


def segment_keys(
    analysis_handle: Mapping[str, Any] | None,
    *,
    factor_columns: Sequence[Sequence[str]],
    factor_tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[OptimiserSegmentKey]:
    """A result's segment keys: analysis columns, then rating factors, each gated.

    *analysis_handle* is the adopted ``quote_analysis`` handle (``None`` without
    analysis columns); *factor_columns* and *factor_tables* are a ratebook
    result's (empty online). A factor named like an analysis column is listed
    once, as the factor, so its levels are the Rates tab's.
    """
    factor_names = [":".join(columns) for columns in factor_columns]
    keys: list[OptimiserSegmentKey] = []
    if analysis_handle is not None:
        stats = analysis_handle["column_stats"]
        for column in analysis_handle["columns"]:
            if column not in factor_names:
                keys.append(_analysis_key(str(column), stats[column]))
    for position, name in enumerate(factor_names):
        keys.append(_factor_key(name, position, factor_tables[name]))
    return keys


def _analysis_key(column: str, stats: Mapping[str, Any]) -> OptimiserSegmentKey:
    binning: Binning = "numeric" if stats["dtype"] in _NUMERIC_DTYPES else "categorical"
    reason: str | None = None
    if binning == "categorical":
        approx = int(stats["approx_n_unique"])
        width = stats["max_string_bytes"]
        if approx > ADMITTED_APPROX_LEVELS:
            reason = (
                f"about {approx:,} distinct values; at most {ADMITTED_APPROX_LEVELS:,} (estimated) "
                "can be broken down."
            )
        elif width is not None and int(width) > MAX_SEGMENT_KEY_BYTES:
            reason = (
                f"a value is {int(width):,} bytes long; values of at most "
                f"{MAX_SEGMENT_KEY_BYTES} bytes can be broken down."
            )
    return _key(column, "analysis", binning, reason)


def _factor_key(
    name: str, position: int, table: Sequence[Mapping[str, Any]]
) -> OptimiserSegmentKey:
    width = max((len(str(row["__factor_group__"]).encode()) for row in table), default=0)
    reason: str | None = None
    if position >= MAX_FACTOR_SEGMENT_KEYS:
        reason = f"only the first {MAX_FACTOR_SEGMENT_KEYS} rating factors can be broken down."
    elif len(table) > MAX_SEGMENT_LEVELS:
        reason = f"{len(table):,} levels; at most {MAX_SEGMENT_LEVELS:,} can be broken down."
    elif width > MAX_SEGMENT_KEY_BYTES:
        reason = (
            f"a level is {width:,} bytes long; levels of at most {MAX_SEGMENT_KEY_BYTES} bytes "
            "can be broken down."
        )
    return _key(name, "factor", "categorical", reason)


def _key(key: str, source: str, binning: Binning, reason: str | None) -> OptimiserSegmentKey:
    return OptimiserSegmentKey(
        key=key,
        source=cast(Literal["analysis", "factor"], source),
        binning=binning,
        available=reason is None,
        unavailable_reason=reason,
    )


def weight_label(spec: ChoiceFrameSpec, weight: str) -> str | None:
    """The label of *weight*, or ``None`` when it is not one of the target's weightings."""
    if weight == QUOTES_WEIGHT:
        return "Quotes"
    return dict(candidate_weightings(spec)).get(weight)


# ---------------------------------------------------------------------------
# The reducers
# ---------------------------------------------------------------------------


def _validate_weight(spec: ChoiceFrameSpec, weight: str) -> None:
    if weight_label(spec, weight) is None:
        raise _bad_request(
            f"Segments can be weighted by {[QUOTES_WEIGHT, *spec.value_columns]}; got {weight!r}."
        )


def _weighted(frame: pl.LazyFrame, weight: str) -> pl.LazyFrame:
    """*frame* with each quote's Float64 weight: 1, or its value at the chosen scenario."""
    import polars as pl

    w = pl.lit(1.0) if weight == QUOTES_WEIGHT else pl.col(weight).cast(pl.Float64)
    return frame.with_columns(w.alias(_WEIGHT))


def _level_sums(spec: ChoiceFrameSpec) -> list[pl.Expr]:
    """Every additive per-level figure, as Float64 sums of the Float32 choice columns.

    The frame carries each quote's weight (``_weighted``).
    """
    import polars as pl

    sv = pl.col("optimal_scenario_value").cast(pl.Float64)
    w = pl.col(_WEIGHT)
    last_step = len(spec.scenario_grid) - 1
    up = sv > BASE_PRICE
    down = sv < BASE_PRICE
    edge = (pl.col("optimal_step") == 0) | (pl.col("optimal_step") == last_step)

    def weighted_where(condition: pl.Expr) -> pl.Expr:
        return pl.when(condition).then(w).otherwise(0.0).sum()

    sums = [
        pl.len().cast(pl.Int64).alias(_QUOTES),
        sv.sum().alias(_SV_SUM),
        up.sum().cast(pl.Int64).alias(_UP),
        down.sum().cast(pl.Int64).alias(_DOWN),
        edge.sum().cast(pl.Int64).alias(_EDGE),
        w.sum().alias(_W_TOTAL),
        (w * sv).sum().alias(_W_SV),
        weighted_where(up).alias(_W_UP),
        weighted_where(down).alias(_W_DOWN),
        weighted_where(edge).alias(_W_EDGE),
        (w < 0).sum().cast(pl.Int64).alias(_W_NEGATIVE),
    ]
    if spec.mode == "ratebook":
        flagged = pl.col(DEPLOYED_FACTOR_DIFFERS).sum().cast(pl.Int64)
        sums.append(flagged.alias(DEPLOYED_FACTOR_DIFFERS))
    return sums


def _sum_columns(spec: ChoiceFrameSpec) -> list[str]:
    """The columns ``_level_sums`` produces, which re-aggregate by summing."""
    columns = [_QUOTES, _SV_SUM, _UP, _DOWN, _EDGE]
    columns += [_W_TOTAL, _W_SV, _W_UP, _W_DOWN, _W_EDGE, _W_NEGATIVE]
    if spec.mode == "ratebook":
        columns.append(DEPLOYED_FACTOR_DIFFERS)
    return columns


def _require_every_quote(rows: pl.DataFrame, row_count: int) -> None:
    counted = int(rows[_QUOTES].sum()) if rows.height else 0
    if counted != row_count:
        raise ChoiceJoinError(
            f"The segment levels count {counted} of {row_count} chosen quotes; every quote "
            "must be in exactly one level."
        )


def _top_levels(
    frame: pl.LazyFrame,
    keys: list[str],
    spec: ChoiceFrameSpec,
    *,
    has_missing: bool,
) -> pl.LazyFrame:
    """Every distinct level of *keys* grouped, the top 15 kept and the rest summed as Other.

    With *has_missing*, a missing quote's key is null, grouped as its own bucket after Other.
    Each bucket carries the number of distinct non-missing levels, counted before
    truncation, and how many levels it merges.
    """
    import polars as pl

    grouped = frame.group_by(keys).agg(_level_sums(spec))
    by_size: list[str | pl.Expr] = [_QUOTES, *keys]
    descending = [True, *([False] * len(keys))]
    top = pl.min_horizontal(
        pl.col(_RANK).cast(pl.Int64), pl.lit(MAX_CATEGORICAL_LEVELS, dtype=pl.Int64)
    )
    if not has_missing:
        n_levels = pl.len()
        bucket = top
    else:
        # The missing quotes' group sorts last and is its own bucket.
        is_missing = pl.col(keys[0]).is_null()
        by_size = [is_missing, *by_size]
        descending = [False, *descending]
        n_levels = (~is_missing).sum()
        bucket = pl.when(is_missing).then(pl.lit(MAX_CATEGORICAL_LEVELS + 1, dtype=pl.Int64))
        bucket = bucket.otherwise(top)
    ranked = (
        grouped.sort(by_size, descending=descending, nulls_last=True)
        .with_row_index(_RANK)
        .with_columns(n_levels.cast(pl.Int64).alias(_N_LEVELS), bucket.alias(_BUCKET))
    )
    return (
        ranked.group_by(_BUCKET)
        .agg(
            *(pl.col(key).first() for key in keys),
            *(pl.col(column).sum() for column in _sum_columns(spec)),
            pl.len().cast(pl.Int64).alias(_MERGED),
            pl.col(_N_LEVELS).first(),
        )
        .sort(_BUCKET)
    )


def _require_level_limit(key: str, rows: pl.DataFrame) -> int:
    """The distinct levels counted before truncation; more than 2,000 is refused."""
    n_levels = int(rows[_N_LEVELS][0]) if rows.height else 0
    if n_levels > MAX_SEGMENT_LEVELS:
        raise SegmentLevelsExceededError(
            f"{key!r} has {n_levels:,} levels; at most {MAX_SEGMENT_LEVELS:,} can be broken down."
        )
    return n_levels


def _categorical_levels(
    rows: pl.DataFrame, labels: Sequence[str], spec: ChoiceFrameSpec
) -> list[dict[str, Any]]:
    """Level rows of a top-15 grouping: the values, Other, then Missing."""
    levels = []
    for row, label in zip(rows.iter_rows(named=True), labels, strict=True):
        bucket = int(row[_BUCKET])
        if bucket < MAX_CATEGORICAL_LEVELS:
            kind, merged = "value", None
        elif bucket == MAX_CATEGORICAL_LEVELS:
            kind, merged, label = "other", int(row[_MERGED]), OTHER_LABEL
        else:
            kind, merged, label = "missing", None, MISSING_LABEL
        levels.append(
            {"label": label, "kind": kind, "lower": None, "upper": None, "merged_levels": merged}
            | {column: row[column] for column in _sum_columns(spec)}
        )
    return levels


@dataclass(frozen=True, slots=True)
class AnalysisSegments:
    """The chosen scenarios per level of one analysis column: bins or the top 15 values.

    A missing quote (a null or NaN value, or a side-input quote the analysis
    frame had no row for) is its own level, listed last.
    """

    column: str
    binning: Binning
    weight: str

    joins_every_quote: ClassVar[bool] = True
    reads_factors: ClassVar[bool] = False

    def validate(self, spec: ChoiceFrameSpec) -> None:
        if self.column not in spec.analysis_columns:
            raise _bad_request(
                f"{self.column!r} is not an analysis column of this solve (configured: "
                f"{list(spec.analysis_columns) or 'none'})."
            )
        _validate_weight(spec, self.weight)

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        return max(MAX_NUMERIC_BINS, MAX_CATEGORICAL_LEVELS + 1) + 1

    def scans_every_quote(self) -> bool:
        return True

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        import polars as pl

        joined = _weighted(frames.joined(), self.weight)
        dtype = joined.collect_schema()[self.column]
        value = pl.col(self.column)
        missing = ~pl.col(ANALYSIS_ROW_PRESENT_COLUMN) | value.is_null()
        if dtype.is_float():
            missing = missing | value.is_nan()
        if self.binning == "numeric":
            rows, n_levels = self._numeric(frames, joined, missing, dtype, spec)
        else:
            level = pl.when(missing).then(None).otherwise(value.cast(pl.String)).alias(_LEVEL)
            grouped = frames.collect(
                _top_levels(joined.with_columns(level), [_LEVEL], spec, has_missing=True)
            )
            n_levels = _require_level_limit(self.column, grouped)
            rows = _categorical_levels(grouped, [str(v) for v in grouped[_LEVEL]], spec)
        frame = pl.DataFrame(rows, infer_schema_length=None)
        _require_every_quote(frame, row_count)
        return ChoiceQueryResult(rows=frame, total=n_levels, quotes=row_count)

    def _numeric(
        self,
        frames: ChoiceFrames,
        joined: pl.LazyFrame,
        missing: pl.Expr,
        dtype: pl.DataType,
        spec: ChoiceFrameSpec,
    ) -> tuple[list[dict[str, Any]], int]:
        import polars as pl

        value = pl.col(self.column)
        quantiles = [index / MAX_NUMERIC_BINS for index in range(MAX_NUMERIC_BINS)]
        bounds = frames.collect(
            joined.filter(~missing).select(
                *(
                    value.quantile(q, "lower").alias(f"q{index}")
                    for index, q in enumerate(quantiles)
                ),
                value.max().alias("max"),
            )
        ).row(0, named=True)
        if bounds["max"] is None:
            starts: list[Any] = []
        else:
            starts = sorted({bounds[f"q{index}"] for index in range(len(quantiles))})
        bin_index = pl.sum_horizontal(
            *((value >= start).cast(pl.Int64) for start in starts[1:]),
            pl.lit(0, dtype=pl.Int64),
        ).cast(pl.Int64)
        binned = joined.with_columns(pl.when(missing).then(None).otherwise(bin_index).alias(_BIN))
        grouped = frames.collect(
            binned.group_by(_BIN).agg(_level_sums(spec)).sort(_BIN, nulls_last=True)
        )
        rows = []
        for row in grouped.iter_rows(named=True):
            level: dict[str, Any]
            if row[_BIN] is None:
                level = {"label": MISSING_LABEL, "kind": "missing", "lower": None, "upper": None}
            else:
                index = int(row[_BIN])
                last = index == len(starts) - 1
                lower = starts[index]
                upper = bounds["max"] if last else starts[index + 1]
                level = {
                    "label": _bin_label(lower, upper, last, dtype),
                    "kind": "bin",
                    "lower": float(lower),
                    "upper": float(upper),
                }
            rows.append(
                level
                | {"merged_levels": None}
                | {column: row[column] for column in _sum_columns(spec)}
            )
        return rows, len(starts)


@dataclass(frozen=True, slots=True)
class FactorLevelSegments:
    """The chosen scenarios per level of one rating factor of a ratebook result.

    Grouped by all the factor's columns and labelled as the Rates tab labels the
    level; the top 15 are listed and the rest summed into Other.
    """

    factor: str
    weight: str

    joins_every_quote: ClassVar[bool] = True
    reads_factors: ClassVar[bool] = True

    def validate(self, spec: ChoiceFrameSpec) -> None:
        if self.factor not in spec.factor_names:
            raise _bad_request(
                f"{self.factor!r} is not a rating factor of this solve (factors: "
                f"{list(spec.factor_names)})."
            )
        _validate_weight(spec, self.weight)

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        return MAX_CATEGORICAL_LEVELS + 1

    def scans_every_quote(self) -> bool:
        return True

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        import polars as pl

        from haute.routes._optimiser_solver import _ratebook_factor_level_key

        if frames.factors is None:
            raise RuntimeError("Factor segments need the leased ratebook factor rows.")
        columns = list(spec.factor_columns[spec.factor_names.index(self.factor)])
        factor_schema = frames.factors.collect_schema()
        dtypes = [factor_schema[column] for column in columns]
        grouped = frames.collect(
            _top_levels(
                _weighted(frames.with_factors(), self.weight), columns, spec, has_missing=False
            )
        )
        n_levels = _require_level_limit(self.factor, grouped)
        labels = [
            _ratebook_factor_level_key([row[column] for column in columns], dtypes)
            for row in grouped.select(columns).iter_rows(named=True)
        ]
        frame = pl.DataFrame(_categorical_levels(grouped, labels, spec), infer_schema_length=None)
        _require_every_quote(frame, row_count)
        return ChoiceQueryResult(rows=frame, total=n_levels, quotes=row_count)


def _format_bound(value: Any, dtype: pl.DataType) -> str:
    """A bin bound in its column's dtype: an integer as one, a Float32 in its shortest form."""
    import numpy as np
    import polars as pl

    if dtype.is_integer():
        return str(int(value))
    if dtype == pl.Float32:
        return str(np.float32(value))
    return repr(float(value))


def _bin_label(lower: Any, upper: Any, last: bool, dtype: pl.DataType) -> str:
    if last and lower == upper:
        return _format_bound(lower, dtype)
    closing = "]" if last else ")"
    return f"[{_format_bound(lower, dtype)}, {_format_bound(upper, dtype)}{closing}"


# ---------------------------------------------------------------------------
# The typed response and the index statistic
# ---------------------------------------------------------------------------


def segment_reducer(
    key: OptimiserSegmentKey, weight: str
) -> AnalysisSegments | FactorLevelSegments:
    """The reducer that breaks the chosen scenarios down by *key*."""
    if key.source == "factor":
        return FactorLevelSegments(factor=key.key, weight=weight)
    return AnalysisSegments(column=key.key, binning=key.binning, weight=weight)


def segments_response(
    result: ChoiceQueryResult,
    spec: ChoiceFrameSpec,
    key: OptimiserSegmentKey,
    weight: str,
    *,
    point_index: int | None,
    frontier_generation: int,
) -> OptimiserSegmentsResponse:
    """The typed breakdown of one segment reducer's levels under *weight*.

    A weighting with a negative value or a zero total is refused for every level;
    otherwise a level whose weight totals 0 has no weighted figures. Each refusal
    is a ``diagnostics_errors`` entry.
    """
    rows = result.rows.to_dicts()
    label = weight_label(spec, weight)
    if label is None:
        raise ValueError(f"{weight!r} is not a weighting of this target.")
    n_quotes = sum(int(row[_QUOTES]) for row in rows)
    total_weight = math.fsum(float(row[_W_TOTAL]) for row in rows)
    negative = sum(int(row[_W_NEGATIVE]) for row in rows)
    errors: list[dict[str, str]] = []
    refused = False
    if negative:
        refused = True
        quotes = f"{negative:,} quote{'' if negative == 1 else 's'}"
        errors.append(
            _weight_error(
                "NegativeWeight",
                f"{label} cannot weigh the segments: {quotes} "
                f"{'has' if negative == 1 else 'have'} a negative value ({weight}).",
            )
        )
    elif total_weight <= 0:
        refused = True
        errors.append(
            _weight_error(
                "ZeroTotalWeight",
                f"{label} cannot weigh the segments: it is zero for every quote ({weight}).",
            )
        )
    levels = []
    for row in rows:
        weighted = None
        if not refused:
            if float(row[_W_TOTAL]) > 0:
                weighted = _figures(row, weighted=True)
            else:
                errors.append(
                    _weight_error(
                        "ZeroLevelWeight",
                        f"{label} is zero for every quote in {row['label']!r} ({weight}), so "
                        "its weighted figures are unavailable.",
                    )
                )
        levels.append(
            {
                "label": row["label"],
                "kind": row["kind"],
                "lower": row["lower"],
                "upper": row["upper"],
                "merged_levels": row["merged_levels"],
                "quotes": int(row[_QUOTES]),
                "weight_total": None if refused else float(row[_W_TOTAL]),
                "unweighted": _figures(row, weighted=False),
                "weighted": weighted,
                "deployed_factor_differs": (
                    int(row[DEPLOYED_FACTOR_DIFFERS]) if spec.mode == "ratebook" else None
                ),
            }
        )
    return OptimiserSegmentsResponse.model_validate(
        {
            "key": key.key,
            "source": key.source,
            "binning": key.binning,
            "weight": weight,
            "weight_label": label,
            "point_index": point_index,
            "frontier_generation": frontier_generation,
            "n_quotes": n_quotes,
            "n_levels": result.total,
            "mean_scenario_value": math.fsum(float(row[_SV_SUM]) for row in rows) / n_quotes,
            "weighted_mean_scenario_value": (
                None if refused else math.fsum(float(row[_W_SV]) for row in rows) / total_weight
            ),
            "rows": levels,
            "diagnostics_errors": errors,
        }
    )


def _figures(row: Mapping[str, Any], *, weighted: bool) -> dict[str, float]:
    if weighted:
        total = float(row[_W_TOTAL])
        parts = (row[_W_SV], row[_W_UP], row[_W_DOWN], row[_W_EDGE])
    else:
        total = float(row[_QUOTES])
        parts = (row[_SV_SUM], row[_UP], row[_DOWN], row[_EDGE])
    mean, up, down, edge = (float(part) / total for part in parts)
    return {
        "mean_scenario_value": mean,
        "share_up": up,
        "share_down": down,
        "share_at_edge": edge,
    }


def _weight_error(error_type: str, message: str) -> dict[str, str]:
    return {"diagnostic": "segment_weight", "error_type": error_type, "message": message}


def segment_spread(response: OptimiserSegmentsResponse) -> float:
    """The quote-weighted standard deviation of the levels' unweighted mean scenario values."""
    n = response.n_quotes
    means = [(row.quotes, row.unweighted.mean_scenario_value) for row in response.rows]
    overall = math.fsum(quotes * mean for quotes, mean in means) / n
    return math.sqrt(math.fsum(quotes * (mean - overall) ** 2 for quotes, mean in means) / n)


def ranked_index(
    keys: Iterable[tuple[OptimiserSegmentKey, float | None]],
    *,
    point_index: int | None,
    frontier_generation: int,
) -> OptimiserSegmentIndexResponse:
    """The index: available keys by spread descending (then key), then the unavailable ones."""
    entries = [key.model_dump() | {"spread": spread} for key, spread in keys]
    available = sorted(
        (entry for entry in entries if entry["available"]),
        key=lambda entry: (-entry["spread"], entry["key"]),
    )
    unavailable = [entry for entry in entries if not entry["available"]]
    return OptimiserSegmentIndexResponse.model_validate(
        {
            "point_index": point_index,
            "frontier_generation": frontier_generation,
            "statistic": {"label": SPREAD_LABEL, "description": SPREAD_DESCRIPTION},
            "keys": [*available, *unavailable],
        }
    )


def cache_segment_index(
    indexes: Mapping[tuple[int, int | None], Any], key: tuple[int, int | None], index: Any
) -> dict[tuple[int, int | None], Any]:
    """*indexes* with *index* stored as the newest under *key*, at most 64 kept."""
    return cache_point_report(indexes, key, index, limit=MAX_CACHED_SEGMENT_INDEXES)


# ---------------------------------------------------------------------------
# The routes' service
# ---------------------------------------------------------------------------


def _refusal_detail(error_code: str, message: str) -> dict[str, str]:
    """A named refusal the client can tell apart, as the frontier's point refusals are."""
    return {"error_code": error_code, "message": message}


def _unprocessable(error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=_refusal_detail(error_code, message))


def _catalogue(job: Mapping[str, Any]) -> list[OptimiserSegmentKey]:
    """The job's segment keys, recorded on its as-solved result at finalize."""
    from haute.routes._optimiser_frontier import _base_result_for_frontier

    return [
        OptimiserSegmentKey.model_validate(key)
        for key in _base_result_for_frontier(job)["segment_keys"]
    ]


class SegmentQueries:
    """Segment breakdowns and the adjustment-spread index over one job's chosen scenarios."""

    def __init__(
        self, store: JobStore, choices: ChoiceQueryService, frontier: OptimiserFrontierService
    ) -> None:
        self._store = store
        self._choices = choices
        self._frontier = frontier

    def segments(
        self,
        job_id: str,
        point_index: int | None,
        key_name: str,
        weight: str,
        token: ExecutionCancellationToken,
    ) -> OptimiserSegmentsResponse:
        """One key's breakdown for the target; an unknown or unavailable key or weight is a 422."""
        from haute.routes._optimiser_frontier import _frontier_generation_or_raise

        job = self._store.require_completed_job(job_id)
        spec = _choice_spec(job)
        keys = {key.key: key for key in _catalogue(job)}
        key = keys.get(key_name)
        if key is None:
            raise _unprocessable(
                "segment_key_unknown",
                f"{key_name!r} is not a segment key of this result (keys: {list(keys)}).",
            )
        if not key.available:
            raise _unprocessable(
                "segment_key_unavailable",
                f"{key.key!r} cannot be broken down: {key.unavailable_reason}",
            )
        if weight_label(spec, weight) is None:
            raise _unprocessable(
                "segment_weight_unknown",
                f"Segments can be weighted by {[QUOTES_WEIGHT, *spec.value_columns]}; got "
                f"{weight!r}.",
            )
        generation = _frontier_generation_or_raise(job)
        try:
            result = self._query(job_id, point_index, key, weight, token)
        except SegmentLevelsExceededError as exc:
            raise _unprocessable(
                "segment_key_unavailable", f"{key.key!r} cannot be broken down: {exc}"
            ) from None
        self._require_generation(job_id, generation)
        return segments_response(
            result,
            spec,
            key,
            weight,
            point_index=point_index,
            frontier_generation=generation,
        )

    def index(
        self, job_id: str, point_index: int | None, token: ExecutionCancellationToken
    ) -> OptimiserSegmentIndexResponse:
        """The target's keys ranked by adjustment spread: cached, else one query per key."""
        from haute.routes._optimiser_frontier import _frontier_generation_or_raise

        job = self._store.require_completed_job(job_id)
        generation = _frontier_generation_or_raise(job)
        cache_key = (generation, point_index)
        cached = (job.get(SEGMENT_INDEXES_KEY) or {}).get(cache_key)
        if cached is not None:
            return OptimiserSegmentIndexResponse.model_validate(cached)
        spec = _choice_spec(job)
        entries: list[tuple[OptimiserSegmentKey, float | None]] = []
        for key in _catalogue(job):
            if not key.available:
                entries.append((key, None))
                continue
            try:
                result = self._query(job_id, point_index, key, QUOTES_WEIGHT, token)
            except SegmentLevelsExceededError as exc:
                entries.append((_key(key.key, key.source, key.binning, str(exc)), None))
                continue
            breakdown = segments_response(
                result,
                spec,
                key,
                QUOTES_WEIGHT,
                point_index=point_index,
                frontier_generation=generation,
            )
            entries.append((key, segment_spread(breakdown)))
        index = ranked_index(entries, point_index=point_index, frontier_generation=generation)
        with self._frontier.parent_lock(job_id):
            latest = self._require_generation(job_id, generation)
            self._store.atomic_update(
                job_id,
                {
                    SEGMENT_INDEXES_KEY: cache_segment_index(
                        latest.get(SEGMENT_INDEXES_KEY) or {}, cache_key, index.model_dump()
                    )
                },
                expected_status="completed",
            )
        return index

    def _query(
        self,
        job_id: str,
        point_index: int | None,
        key: OptimiserSegmentKey,
        weight: str,
        token: ExecutionCancellationToken,
    ) -> ChoiceQueryResult:
        return self._choices.choice_query(
            job_id,
            ChoiceTarget(point_index),
            segment_reducer(key, weight),
            cancellation_token=token,
        )

    def _require_generation(self, job_id: str, generation: int) -> Mapping[str, Any]:
        """The job, which must still be at *generation*: else the frontier-changed 409."""
        from haute.routes._optimiser_frontier import (
            _FRONTIER_CHANGED_DETAIL,
            _frontier_generation_or_raise,
        )

        latest = self._store.require_completed_job(job_id)
        if _frontier_generation_or_raise(latest) != generation:
            raise HTTPException(status_code=409, detail=_FRONTIER_CHANGED_DETAIL)
        return latest
