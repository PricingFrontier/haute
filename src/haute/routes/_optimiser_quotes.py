"""The Quotes explorer (OPT-V12): bounded pages of one target's chosen scenarios.

A page is a V09B reducer over the choice frame: it filters (a quote-id prefix,
a scenario-value range, the grid's end steps, analysis values, a ratebook
quote's deployed-factor flag), counts the matches, sorts with the quote-id
tie-break and slices one page, all in the lazy plan, so no request ever holds
more than ``offset + limit`` sorted rows. The recorded ``scenario_grid``, not
the quote grid, decides which steps are the range edges, so a page is answered
for the job's whole lifetime.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from fastapi import HTTPException

from haute.routes._optimiser_limits import APPLY_PREVIEW_ROW_LIMIT, QUOTE_PAGE_DEPTH_LIMIT
from haute.routes._optimiser_outcomes import (
    CHOICE_QUOTE_ID,
    DEPLOYED_FACTOR_DIFFERS,
    ChoiceFrames,
    ChoiceFrameSpec,
    ChoiceQueryResult,
    ChoiceQueryService,
    ChoiceTarget,
    _choice_spec,
    ranked,
)
from haute.schemas import (
    OptimiserApplyRequest,
    OptimiserApplyResponse,
    OptimiserQuoteColumn,
    OptimiserQuoteFilters,
)

if TYPE_CHECKING:
    import polars as pl

    from haute._execution_context import ExecutionCancellationToken
    from haute.routes._job_store import JobStore
    from haute.routes._optimiser_frontier import OptimiserFrontierService

EqualityValue = str | int | float | bool | None

_STRING_DTYPES = frozenset({"String", "Categorical", "Enum"})
_INTEGER_DTYPES = frozenset(
    {"Int8", "Int16", "Int32", "Int64", "Int128", "UInt8", "UInt16", "UInt32", "UInt64"}
)
_FLOAT_DTYPES = frozenset({"Float32", "Float64"})
_BOOLEAN_DTYPES = frozenset({"Boolean"})

_SCENARIO = "optimal_scenario_value"
_FACTOR_PRODUCT = "factor_product"


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def equality_filter_supported(dtype_name: str) -> bool:
    """Whether an analysis column of *dtype_name* (``str(dtype.base_type())``) can be
    filtered by equality: strings, integers, floats and booleans."""
    return dtype_name in _STRING_DTYPES | _INTEGER_DTYPES | _FLOAT_DTYPES | _BOOLEAN_DTYPES


def _value_suits(dtype_name: str, value: EqualityValue) -> bool:
    if value is None:
        return True
    if dtype_name in _STRING_DTYPES:
        return isinstance(value, str)
    if dtype_name in _INTEGER_DTYPES:
        return isinstance(value, int) and not isinstance(value, bool)
    if dtype_name in _FLOAT_DTYPES:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if dtype_name in _BOOLEAN_DTYPES:
        return isinstance(value, bool)
    return False


def _equals(column: str, dtype_name: str, value: EqualityValue) -> pl.Expr:
    """*column* equal to *value*; ``None`` matches a missing value (null, or NaN for a float)."""
    import polars as pl

    if value is None:
        missing = pl.col(column).is_null()
        return missing | pl.col(column).is_nan() if dtype_name in _FLOAT_DTYPES else missing
    if dtype_name in _STRING_DTYPES:
        return pl.col(column).cast(pl.String) == pl.lit(value, dtype=pl.String)
    if dtype_name in _FLOAT_DTYPES:
        return pl.col(column).cast(pl.Float64) == pl.lit(float(value), dtype=pl.Float64)
    return pl.col(column) == pl.lit(value)


def sortable_columns(spec: ChoiceFrameSpec) -> tuple[str, ...]:
    """The columns ``sort_by`` accepts: the chosen values, a ratebook factor product, analysis."""
    factor = (_FACTOR_PRODUCT,) if spec.mode == "ratebook" else ()
    return (_SCENARIO, *spec.value_columns, *factor, *spec.analysis_columns)


def quote_output_columns(spec: ChoiceFrameSpec) -> tuple[str, ...]:
    """A page row's keys, in order."""
    ratebook = (_FACTOR_PRODUCT, DEPLOYED_FACTOR_DIFFERS) if spec.mode == "ratebook" else ()
    return (CHOICE_QUOTE_ID, _SCENARIO, *spec.value_columns, *ratebook, *spec.analysis_columns)


def quote_columns(
    spec: ChoiceFrameSpec, analysis_dtypes: Mapping[str, str]
) -> list[OptimiserQuoteColumn]:
    """Every page column with its role; *analysis_dtypes* names each analysis column's dtype."""
    sortable = set(sortable_columns(spec))
    roles: dict[str, Any] = {
        CHOICE_QUOTE_ID: "id",
        _SCENARIO: "scenario",
        "optimal_objective": "objective",
        **{f"optimal_{name}": "constraint" for name in spec.constraint_names},
        _FACTOR_PRODUCT: "factor",
        DEPLOYED_FACTOR_DIFFERS: "flag",
    }
    columns: list[OptimiserQuoteColumn] = []
    for name in quote_output_columns(spec):
        analysis = name in spec.analysis_columns
        columns.append(
            OptimiserQuoteColumn(
                name=name,
                role="analysis" if analysis else roles[name],
                sortable=name in sortable,
                filterable=analysis and equality_filter_supported(analysis_dtypes[name]),
            )
        )
    return columns


@dataclass(frozen=True, slots=True)
class QuoteFilters:
    """The page filters, combined with AND (a hashable mirror of the request's)."""

    scenario_value_min: float | None = None
    scenario_value_max: float | None = None
    at_range_edge: bool = False
    analysis_equals: tuple[tuple[str, EqualityValue], ...] = ()
    deployed_factor_differs: bool = False

    @classmethod
    def from_request(cls, filters: OptimiserQuoteFilters) -> QuoteFilters:
        return cls(
            scenario_value_min=filters.scenario_value_min,
            scenario_value_max=filters.scenario_value_max,
            at_range_edge=filters.at_range_edge,
            analysis_equals=tuple(filters.analysis_equals.items()),
            deployed_factor_differs=filters.deployed_factor_differs,
        )

    @property
    def filters_anything(self) -> bool:
        return (
            self.scenario_value_min is not None
            or self.scenario_value_max is not None
            or self.at_range_edge
            or bool(self.analysis_equals)
            or self.deployed_factor_differs
        )


@dataclass(frozen=True, slots=True)
class QuotePage:
    """One page of the chosen scenarios: filtered, counted, sorted (ties by quote id), sliced.

    Build it with :func:`quote_page`, which picks :class:`AnalysisQuotePage` when the
    sort or a filter reads an analysis column.
    """

    sort_by: str | None = None
    descending: bool = False
    quote_id_prefix: str | None = None
    filters: QuoteFilters = field(default_factory=QuoteFilters)
    offset: int = 0
    limit: int = APPLY_PREVIEW_ROW_LIMIT

    joins_every_quote: ClassVar[bool] = False
    reads_factors: ClassVar[bool] = False

    def validate(self, spec: ChoiceFrameSpec) -> None:
        limit, offset = self.limit, self.offset
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not (1 <= limit <= APPLY_PREVIEW_ROW_LIMIT)
        ):
            raise _bad_request(
                f"The page limit must be an integer from 1 to {APPLY_PREVIEW_ROW_LIMIT}; "
                f"got {limit!r}."
            )
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise _bad_request(f"The page offset must be a non-negative integer; got {offset!r}.")
        if offset + limit > QUOTE_PAGE_DEPTH_LIMIT:
            raise _bad_request(
                f"This page ends at row {offset + limit:,}, past the first "
                f"{QUOTE_PAGE_DEPTH_LIMIT:,} rows a page can reach. Narrow the filter or "
                "search the quote ID to see these quotes."
            )
        if self.sort_by is not None and self.sort_by not in sortable_columns(spec):
            raise _bad_request(
                f"Quotes can be sorted by {list(sortable_columns(spec))}; got {self.sort_by!r}."
            )
        if self.quote_id_prefix is not None and (
            not isinstance(self.quote_id_prefix, str) or not self.quote_id_prefix
        ):
            raise _bad_request("The quote ID search must be a non-empty string.")
        self._validate_filters(spec)
        if _reads_analysis(self.sort_by, self.filters, spec) != self.joins_every_quote:
            raise RuntimeError(
                f"{type(self).__name__} does not match its query's analysis reads; build pages "
                "with quote_page()."
            )

    def _validate_filters(self, spec: ChoiceFrameSpec) -> None:
        filters = self.filters
        bounds = [
            value
            for value in (filters.scenario_value_min, filters.scenario_value_max)
            if value is not None
        ]
        if not all(math.isfinite(value) for value in bounds):
            raise _bad_request(f"The scenario-value range must be finite; got {bounds}.")
        low, high = filters.scenario_value_min, filters.scenario_value_max
        if low is not None and high is not None and low > high:
            raise _bad_request(
                f"The scenario-value minimum ({low}) is above the maximum ({high}); no quote "
                "can match."
            )
        unknown = [
            name for name, _value in filters.analysis_equals if name not in spec.analysis_columns
        ]
        if unknown:
            configured = list(spec.analysis_columns) or "none"
            raise _bad_request(
                f"{unknown} are not analysis columns of this solve (configured: {configured}), "
                "so quotes cannot be filtered by them."
            )
        if filters.deployed_factor_differs and spec.mode != "ratebook":
            raise _bad_request(
                "Only a ratebook result has quotes whose deployed factor differs from the "
                f"evaluated step; this is an {spec.mode} result."
            )

    def result_rows(self, spec: ChoiceFrameSpec) -> int:
        # A sorted page holds the top (offset + limit) matches before it slices.
        return self.offset + self.limit if self.sort_by is not None else self.limit

    def scans_every_quote(self) -> bool:
        return (
            self.sort_by is not None
            or self.quote_id_prefix is not None
            or self.filters.filters_anything
        )

    def run(self, frames: ChoiceFrames, spec: ChoiceFrameSpec, row_count: int) -> ChoiceQueryResult:
        import polars as pl

        frame = frames.joined() if self.joins_every_quote else frames.choice
        predicate = self._predicate(frames, spec)
        if predicate is None:
            matched = row_count
        else:
            frame = frame.filter(predicate)
            matched = int(frames.collect(frame.select(pl.len())).item())
        if self.sort_by is not None:
            frame = ranked(frame, self.sort_by, self.descending)
        rows = frames.collect(frame.slice(self.offset, self.limit))
        if not self.joins_every_quote:
            rows = frames.with_analysis(rows)
        rows = rows.select(quote_output_columns(spec))
        # JSON has no NaN: a NaN analysis value is missing, as Segments counts it.
        rows = rows.with_columns(
            [
                pl.col(column).fill_nan(None)
                for column in spec.analysis_columns
                if rows.schema[column].is_float()
            ]
        )
        return ChoiceQueryResult(rows=rows, total=matched, quotes=row_count)

    def _predicate(self, frames: ChoiceFrames, spec: ChoiceFrameSpec) -> pl.Expr | None:
        import polars as pl

        filters = self.filters
        parts: list[pl.Expr] = []
        if self.quote_id_prefix is not None:
            parts.append(pl.col(CHOICE_QUOTE_ID).str.starts_with(self.quote_id_prefix))
        scenario = pl.col(_SCENARIO).cast(pl.Float64)
        if filters.scenario_value_min is not None:
            parts.append(scenario >= filters.scenario_value_min)
        if filters.scenario_value_max is not None:
            parts.append(scenario <= filters.scenario_value_max)
        if filters.at_range_edge:
            # The recorded grid's end steps, never inferred from the chosen rows.
            first, last = spec.scenario_grid[0][0], spec.scenario_grid[-1][0]
            parts.append(pl.col("optimal_step").is_in([first, last]))
        if filters.deployed_factor_differs:
            parts.append(pl.col(DEPLOYED_FACTOR_DIFFERS))
        if filters.analysis_equals:
            if frames.analysis is None:
                raise RuntimeError("An analysis filter needs the leased analysis side table.")
            schema = frames.analysis.collect_schema()
            for column, value in filters.analysis_equals:
                dtype_name = str(schema[column].base_type())
                if not equality_filter_supported(dtype_name) or not _value_suits(dtype_name, value):
                    raise _bad_request(
                        f"Analysis column {column!r} ({dtype_name}) cannot be filtered by "
                        f"equality to {value!r}: use a string for a text column, an integer "
                        "for an integer column, a number for a float column, a boolean for a "
                        "Boolean column, or null for a missing value."
                    )
                parts.append(_equals(column, dtype_name, value))
        return pl.all_horizontal(parts) if parts else None


@dataclass(frozen=True, slots=True)
class AnalysisQuotePage(QuotePage):
    """A page whose sort or filters read an analysis column: it joins every quote's values."""

    joins_every_quote: ClassVar[bool] = True


def _reads_analysis(sort_by: str | None, filters: QuoteFilters, spec: ChoiceFrameSpec) -> bool:
    return bool(filters.analysis_equals) or (
        sort_by is not None and sort_by in spec.analysis_columns
    )


def quote_page(
    spec: ChoiceFrameSpec,
    *,
    sort_by: str | None = None,
    descending: bool = False,
    quote_id_prefix: str | None = None,
    filters: QuoteFilters | None = None,
    offset: int = 0,
    limit: int = APPLY_PREVIEW_ROW_LIMIT,
) -> QuotePage:
    """The page reducer for a query: :class:`AnalysisQuotePage` when it reads analysis values."""
    resolved = filters if filters is not None else QuoteFilters()
    page_type = AnalysisQuotePage if _reads_analysis(sort_by, resolved, spec) else QuotePage
    return page_type(
        sort_by=sort_by,
        descending=descending,
        quote_id_prefix=quote_id_prefix,
        filters=resolved,
        offset=offset,
        limit=limit,
    )


def _analysis_dtypes(job: Mapping[str, Any], spec: ChoiceFrameSpec) -> dict[str, str]:
    """Each analysis column's dtype, as the side table's handle recorded it at setup."""
    from haute.routes._optimiser_artifacts import _QUOTE_ANALYSIS_HANDLE_KEY

    if not spec.analysis_columns:
        return {}
    stats = job["artifact_handles"][_QUOTE_ANALYSIS_HANDLE_KEY]["column_stats"]
    return {column: str(stats[column]["dtype"]) for column in spec.analysis_columns}


def _anchor_totals(job: Mapping[str, Any]) -> tuple[float, dict[str, float]]:
    """The as-solved totals, checked before any page is read.

    A job without the as-solved apply handle is a 400 (re-run the solve); a
    summary without numeric totals is a 500.
    """
    from haute.routes._optimiser_artifacts import _APPLY_RESULT_HANDLE_KEY
    from haute.routes._optimiser_frontier import (
        _artifact_handles_or_raise,
        _base_result_for_frontier,
    )

    if not isinstance(_artifact_handles_or_raise(job).get(_APPLY_RESULT_HANDLE_KEY), dict):
        raise HTTPException(
            status_code=400,
            detail="Job has no apply artifact handle. Re-run the solve to regenerate it.",
        )
    summary = _base_result_for_frontier(job)
    total_objective = summary.get("total_objective")
    constraints = summary.get("constraints")
    if (
        isinstance(total_objective, bool)
        or not isinstance(total_objective, int | float)
        or not isinstance(constraints, dict)
    ):
        raise HTTPException(status_code=500, detail="Job summary is incomplete")
    return float(total_objective), constraints


def _validated_page(
    job: Mapping[str, Any], body: OptimiserApplyRequest
) -> tuple[ChoiceFrameSpec, QuotePage]:
    """The job's choice-frame spec and the request's page reducer, validated against it."""
    spec = _choice_spec(job)
    reducer = quote_page(
        spec,
        sort_by=body.sort_by,
        descending=body.descending,
        quote_id_prefix=body.quote_id_prefix,
        filters=QuoteFilters.from_request(body.filters),
        offset=body.offset,
        limit=body.limit,
    )
    reducer.validate(spec)
    return spec, reducer


class QuoteQueries:
    """Quotes pages over one job's chosen scenarios, for ``POST /apply``."""

    def __init__(
        self, store: JobStore, choices: ChoiceQueryService, frontier: OptimiserFrontierService
    ) -> None:
        self._store = store
        self._choices = choices
        self._frontier = frontier

    def page(
        self, body: OptimiserApplyRequest, token: ExecutionCancellationToken
    ) -> OptimiserApplyResponse:
        """The requested page of the target, with its totals, typed columns and counts.

        A frontier point is materialised if need be and recorded as the job's
        selected point; its rows must come from the generation it was selected in.
        """
        from haute.routes._optimiser_frontier import (
            _FRONTIER_CHANGED_DETAIL,
            _frontier_generation_or_raise,
        )

        job = self._store.require_completed_job(body.job_id)
        target = ChoiceTarget(body.point_index)
        if body.point_index is None:
            # A broken job is refused before any page is read.
            total_objective, constraints = _anchor_totals(job)
            spec, reducer = _validated_page(job, body)
            result = self._choices.choice_query(
                body.job_id, target, reducer, cancellation_token=token
            )
            from_artifact = True
            generation = _frontier_generation_or_raise(job)
        else:
            # Refused before the point is materialised or selected.
            spec, reducer = _validated_page(job, body)
            ticket = self._frontier.request_point_apply(body.job_id, body.point_index)
            ticket.wait(token)
            selected = self._frontier.select_applied_point(
                body.job_id, body.point_index, ticket.generation
            )
            result = self._choices.choice_query(
                body.job_id, target, reducer, cancellation_token=token
            )
            latest = self._store.require_completed_job(body.job_id)
            if _frontier_generation_or_raise(latest) != ticket.generation:
                raise HTTPException(status_code=409, detail=_FRONTIER_CHANGED_DETAIL)
            total_objective = float(selected["total_objective"])
            constraints = selected["constraints"]
            from_artifact = ticket.from_artifact
            generation = ticket.generation
        rows = result.rows.to_dicts()
        return OptimiserApplyResponse(
            status="ok",
            total_objective=total_objective,
            constraints=constraints,
            from_artifact=from_artifact,
            columns=quote_columns(spec, _analysis_dtypes(job, spec)),
            preview=rows,
            row_count=result.quotes,
            matched_row_count=result.total,
            offset=body.offset,
            preview_row_count=len(rows),
            preview_row_limit=body.limit,
            frontier_generation=generation,
        )
