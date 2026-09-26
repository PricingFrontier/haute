"""The optimiser's solver layer: solve entry points and result builders.

The heavy solver entry points (``_solve_online``, ``_solve_ratebook``,
``_compute_frontier``) run only inside ``solver_worker_context`` on a background
solver thread. The result builders normalise a solve into its published result
(``_finalize_solve_result``, including the inline frontier and scenario-value
statistics) and canonicalise and serialise ratebook factor tables.
``OptimiserSolveService`` composes them with job admission, setup and workers.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING, Any, cast

import numpy as np

if TYPE_CHECKING:
    import polars as pl
    from price_contour import QuoteGrid


from haute._banding_config import normalise_banding_factors
from haute._env import int_env
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
)
from haute._logging import get_logger
from haute._polars_utils import (
    streaming_collect,
)
from haute._price_contour import price_contour
from haute._ratebook_collar import COMBINED_FACTOR_BOUNDS_KEY, combined_factor_bounds_from_grid
from haute._rating import (
    normalise_rating_key,
    rating_dtype_descriptor,
    rating_dtype_from_descriptor,
)
from haute._types import (
    GraphNode,
    OnlineSolveResultLike,
    PerFactorRecordLike,
    PipelineGraph,
    RatebookSolveResultLike,
    SolveResultLike,
)
from haute.graph_utils import NodeType
from haute.routes import _optimiser_artifacts
from haute.routes._background_jobs import (
    BackgroundJobStoppedError,
)
from haute.routes._frontier_point_summary import (
    NON_CONVERGED_WARNING,
    constraint_kinds,
    effective_bounds,
)
from haute.routes._job_lifecycle import (
    JobLifecycle,
)
from haute.routes._job_store import (
    JobStore,
)
from haute.routes._optimiser_input import (
    _chunk_size_decision_for_parquet,
    _ChunkSizeDecision,
    _resolve_optimiser_input_edge,
)
from haute.routes._optimiser_limits import (
    enforce_frontier_compute_budget,
    limited_frontier_payload,
)
from haute.routes._optimiser_outcomes import require_scenario_grid
from haute.schemas import (
    OptimiserRatebookCdTrace,
    _normalise_frontier_range_pair,
)

logger = get_logger(component="server.optimiser.solve")

# ── Default constants ─────────────────────────────────────────────
_HISTOGRAM_BINS = 20  # bin count for scenario-value distribution histogram


_DEFAULT_MAX_ITER = 50  # max solver iterations (online & ratebook)


_DEFAULT_TOLERANCE = 1e-6  # convergence tolerance for solver


_DEFAULT_MAX_CD_ITERATIONS = 10  # max coordinate-descent iterations (ratebook)


_DEFAULT_CD_TOLERANCE = 1e-3  # coordinate-descent convergence tolerance (ratebook)


_DEFAULT_FRONTIER_STEPS = 15  # frontier points per constraint dimension (inline frontier)


class _OptimiserSolveInputError(Exception):
    """A user-actionable error while adapting optimiser solver input."""


class _OptimiserSolverExecutionError(Exception):
    """An exception raised by the external price-contour solver boundary."""


_FRONTIER_GENERATION_KEY = "frontier_generation"
# Job key: one ``factor_tables`` dict per retained ratebook frontier point,
# aligned with ``frontier_data["points"]``. price-contour reports each row's
# totals as the canonical evaluation of these tables, so a point is
# materialised from them exactly, without re-solving (roadmap OPT-PC01).
_FRONTIER_FACTOR_TABLES_KEY = "frontier_factor_tables"


def frontier_point_factor_tables(
    frontier_result: Any, *, mode: str, points_returned: int
) -> list[dict[str, dict[str, float]]] | None:
    """The retained points' factor tables for a ratebook frontier, else None."""
    if mode != "ratebook":
        return None
    return list(frontier_result.factor_tables[:points_returned])


_SOLVER_WORKER_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "haute_optimiser_solver_worker",
    default=False,
)


@contextlib.contextmanager
def solver_worker_context() -> Iterator[None]:
    """Mark the current thread of execution as an optimiser solver worker.

    Entered only by the background job runners (solve worker, frontier sweep
    worker). Guarded entrypoints refuse to run outside it.
    """
    token = _SOLVER_WORKER_ACTIVE.set(True)
    try:
        yield
    finally:
        _SOLVER_WORKER_ACTIVE.reset(token)


def require_solver_worker_context(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Fail loud if a heavy solver entrypoint runs outside a worker context."""

    @functools.wraps(fn)
    def _guarded(*args: Any, **kwargs: Any) -> Any:
        if not _SOLVER_WORKER_ACTIVE.get():
            raise RuntimeError(
                f"{fn.__name__} is a heavy solver entrypoint and must run inside a "
                "background solver worker (solver_worker_context), never inline in a "
                "request handler. Submit a job and poll its status instead."
            )
        return fn(*args, **kwargs)

    return _guarded


def _job_elapsed_seconds(job: Mapping[str, Any], fallback: float = 0.0) -> float:
    """Return wall-clock elapsed seconds for a job when start_time is available."""
    start_time = job.get("start_time")
    fallback_elapsed = max(0.0, float(fallback))
    if isinstance(start_time, bool) or not isinstance(start_time, int | float):
        return fallback_elapsed
    return max(fallback_elapsed, time.monotonic() - float(start_time), 0.0)


def _compute_scenario_value_stats(
    solve_result: SolveResultLike,
) -> tuple[dict[str, float], dict[str, list[int] | list[float]]]:
    """Scenario value distribution statistics and histogram of an online solve.

    Raises ``ValueError`` when the result has no per-quote frame, no
    ``optimal_scenario_value`` column or no quotes; the caller records any
    failure in ``diagnostics_errors`` rather than dropping the statistics.
    """
    if not hasattr(solve_result, "dataframe"):
        raise ValueError("The solve result has no per-quote frame to summarise")
    df = solve_result.dataframe
    if "optimal_scenario_value" not in df.columns:
        raise ValueError("The solve result's per-quote frame has no optimal_scenario_value column")

    col = df["optimal_scenario_value"]
    n = len(col)
    if n == 0:
        raise ValueError("The solve result has no quotes to summarise")
    # polars' sample std (ddof=1) is undefined (null) for a single quote and
    # would crash the float() cast after the solve already succeeded. A
    # complete one-quote result set has exactly zero spread, so 0.0 is the
    # true population statistic for n == 1 — not a fabricated estimate
    # (mirrors the degenerate-input convention used by the gini metrics).
    # The response schema (OptimiserScenarioValueStats.std) and the frontend
    # guard both require ``std`` to be a number, so omitting or nulling just
    # this field is not a shape the contract permits.
    stats = {
        "mean": float(col.mean()),
        "std": 0.0 if n == 1 else float(col.std()),
        "min": float(col.min()),
        "max": float(col.max()),
        "p5": float(col.quantile(0.05)),
        "p25": float(col.quantile(0.25)),
        "p50": float(col.quantile(0.50)),
        "p75": float(col.quantile(0.75)),
        "p95": float(col.quantile(0.95)),
        "pct_increase": float((col > 1.0).sum() / n),
        "pct_decrease": float((col < 1.0).sum() / n),
    }

    vals = col.to_numpy()
    counts, edges = np.histogram(vals, bins=_HISTOGRAM_BINS)
    histogram: dict[str, list[int] | list[float]] = {
        "counts": [int(c) for c in counts],
        "edges": [float(e) for e in edges],
    }
    return stats, histogram


def _diagnostic_error(
    diagnostic: str,
    exc: BaseException,
    *,
    job_id: str,
) -> dict[str, str]:
    """A ``diagnostics_errors`` entry for a diagnostic that could not be produced."""
    logger.warning(
        "optimiser_diagnostic_skipped",
        diagnostic=diagnostic,
        error=str(exc),
        error_type=type(exc).__name__,
        job_id=job_id,
        exc_info=True,
    )
    return {"diagnostic": diagnostic, "error_type": type(exc).__name__, "message": str(exc)}


def solver_settings(job_config: Mapping[str, Any]) -> dict[str, Any]:
    """The solver settings a solve ran with, from its solve-time config snapshot."""
    settings: dict[str, Any] = {
        "max_iter": job_config.get("max_iter", _DEFAULT_MAX_ITER),
        "tolerance": job_config.get("tolerance", _DEFAULT_TOLERANCE),
        "chunk_size": job_config.get("chunk_size"),
    }
    if job_config.get("mode") == "ratebook":
        settings["max_cd_iterations"] = job_config.get(
            "max_cd_iterations", _DEFAULT_MAX_CD_ITERATIONS
        )
        settings["cd_tolerance"] = job_config.get("cd_tolerance", _DEFAULT_CD_TOLERANCE)
    if job_config.get("frontier_enabled") is True:
        settings["frontier_enabled"] = True
        settings["frontier_steps"] = job_config.get("frontier_steps", _DEFAULT_FRONTIER_STEPS)
        settings["frontier_ranges"] = job_config.get("frontier_ranges")
    return settings


def _max_ratebook_cd_trace() -> int:
    return int_env("HAUTE_OPTIMISER_CD_TRACE_LIMIT", 1000)


def ratebook_cd_trace(
    per_factor_results: Sequence[PerFactorRecordLike],
    constraint_names: Sequence[str],
) -> dict[str, Any]:
    """A ratebook solve's coordinate-descent trace from price-contour's per-factor records.

    Each record is read by field name (its pass and factor are the library's,
    never inferred from position). The trace keeps the last
    ``HAUTE_OPTIMISER_CD_TRACE_LIMIT`` records, as ``loss_history`` is capped,
    and is validated here so a malformed record fails the solve, not a later read.
    """
    records = [
        {
            "cd_iteration": record.cd_iteration,
            "factor": record.factor,
            "factor_index": record.factor_index,
            "total_objective": record.total_objective,
            "total_constraints": dict(record.total_constraints),
            "lambdas": dict(record.lambdas),
        }
        for record in per_factor_results
    ]
    limit = _max_ratebook_cd_trace()
    trace = OptimiserRatebookCdTrace.model_validate(
        {"records": records[-limit:], "truncated": len(records) > limit}
    )
    trace_names = trace.constraint_names()
    if set(trace_names) != set(constraint_names):
        raise ValueError(
            f"ratebook_cd_trace records hold the constraint names {trace_names}, "
            f"not the solve's {list(constraint_names)}"
        )
    return trace.model_dump()


def solve_input_summary(job: Mapping[str, Any]) -> dict[str, Any]:
    """What a solve ran on: the job's ``input_provenance`` and its solver settings.

    Built once when the solve completes; the published artifact reads it back.
    A solve job always records its provenance when it is created, so a missing
    one raises ``KeyError``.
    """
    return {**job["input_provenance"], "solver_settings": solver_settings(job["config"])}


@require_solver_worker_context
def _compute_frontier(
    solver: Any,
    quote_grid: QuoteGrid,
    *,
    mode: str,
    ratebook_factors: Any | None,
    threshold_ranges: dict[str, tuple[float, float]],
    n_points_per_dim: int,
    factor_columns: list[list[str]] | None = None,
    initial_lambdas: dict[str, float] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> Any:
    """Call the mode-specific frontier API."""
    if check_cancelled is not None:
        check_cancelled()
    if mode == "ratebook":
        if ratebook_factors is None:
            raise RuntimeError("Ratebook frontier requires prepared factor contexts.")
        frontier_kwargs: dict[str, Any] = {
            "threshold_ranges": threshold_ranges,
            "n_points_per_dim": n_points_per_dim,
        }
        if factor_columns is not None:
            frontier_kwargs["factor_columns"] = factor_columns
        if initial_lambdas is not None:
            frontier_kwargs["initial_lambdas"] = initial_lambdas
        result = solver.frontier(
            quote_grid,
            ratebook_factors,
            **frontier_kwargs,
        )
        if check_cancelled is not None:
            check_cancelled()
        return result
    frontier_kwargs = {
        "threshold_ranges": threshold_ranges,
        "n_points_per_dim": n_points_per_dim,
    }
    if initial_lambdas is not None:
        frontier_kwargs["initial_lambdas"] = initial_lambdas
    result = solver.frontier(quote_grid, **frontier_kwargs)
    if check_cancelled is not None:
        check_cancelled()
    return result


def _auto_frontier_ranges_from_config(config: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Build absolute frontier ranges from canonical per-constraint config."""
    constraints = config.get("constraints") or {}
    if not constraints:
        return {}

    configured_ranges = config.get("frontier_ranges")
    if configured_ranges is not None:
        if not isinstance(configured_ranges, dict):
            raise ValueError("frontier_ranges must be an object keyed by constraint name.")
        ranges: dict[str, tuple[float, float]] = {}
        for cname in constraints:
            if cname not in configured_ranges:
                raise ValueError(f"frontier_ranges is missing a range for constraint {cname!r}.")
            ranges[str(cname)] = _normalise_frontier_range_pair(
                configured_ranges[cname],
                field=f"frontier_ranges.{cname}",
            )
        return ranges

    raise ValueError("frontier_ranges must provide min and max for each constraint.")


_RATEBOOK_FACTOR_LEVEL_SEPARATOR = "\x1f"


_RATEBOOK_FACTOR_LEVEL_ORDER_KEY = "factor_level_order"


def _ratebook_factor_table_name(columns: list[str]) -> str:
    return ":".join(columns)


def _ratebook_factor_level_key(
    values: list[Any],
    dtypes: list[pl.DataType],
) -> str:
    """Canonical level key for one observed factor-level tuple (3b.10).

    Components are canonicalised through the shared
    :func:`haute._rating.normalise_rating_key`, so save-time level keys agree
    with the keys the apply-side rating join derives from frame values
    (Float64 ``25.0`` -> ``"25"``; strings stay verbatim).
    """
    parts: list[str] = []
    if len(dtypes) != len(values):
        raise ValueError("Ratebook factor values and dtypes must have the same length.")
    for value, dtype in zip(values, dtypes):
        canonical = normalise_rating_key(value, dtype)
        if canonical is None:
            raise ValueError("Ratebook factor counts cannot be computed with null factor levels.")
        parts.append(canonical)
    return _RATEBOOK_FACTOR_LEVEL_SEPARATOR.join(parts)


def _canonical_ratebook_table_level(
    name: str,
    level: Any,
    level_counts: dict[str, int],
    dtypes: list[pl.DataType],
) -> str:
    """Save-time canonical key for a solver-emitted factor level (3b.10).

    price-contour stringifies typed factor values, which widens Float32 values
    to Python Float64 representations. Reconstruct each emitted component
    through the exact originating dtype from the solved factor artifact before
    canonicalisation. ``level_counts`` was built from that same typed artifact,
    so the resulting key must exist exactly; no candidate search or dtype
    inference is permitted.
    """
    if len(dtypes) == 1:
        components = [level]
    elif isinstance(level, str):
        components = level.split(_RATEBOOK_FACTOR_LEVEL_SEPARATOR)
    else:
        raise ValueError(
            f"Ratebook factor table {name!r} has a non-string composite level {level!r}."
        )
    if len(components) != len(dtypes):
        raise ValueError(
            f"Ratebook factor table {name!r} level {level!r} has {len(components)} "
            f"component(s), expected {len(dtypes)}."
        )
    canonical = _ratebook_factor_level_key(components, dtypes)
    if canonical not in level_counts:
        raise ValueError(
            f"Ratebook factor counts missing for level {level!r} in factor table {name!r}."
        )
    return canonical


def _append_unique_factor_level(levels: list[str], seen: set[str], value: object) -> None:
    if value is None or value == "":
        return
    level = str(value)
    if level in seen:
        return
    seen.add(level)
    levels.append(level)


def _banding_rule_output_level(rule: dict[str, Any]) -> object:
    """Return the rule's output level (``assignment`` or ``label``)."""
    if "assignment" in rule:
        return rule["assignment"]
    return rule["label"]


def _find_node_by_id(graph: PipelineGraph, node_id: str) -> GraphNode | None:
    """Return the graph node with the given id, or ``None`` if absent."""
    return next((node for node in graph.nodes if node.id == node_id), None)


def _ratebook_factor_level_order(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> dict[str, list[str]]:
    """Extract factor-level display order from the configured banding source."""
    banding_edge = _resolve_optimiser_input_edge(
        graph,
        node_id,
        config,
        field="banding_source",
    )
    if banding_edge is None:
        return {}

    banding_node = _find_node_by_id(graph, banding_edge.source)
    if banding_node is None or banding_node.data.nodeType != NodeType.BANDING:
        return {}

    order: dict[str, list[str]] = {}
    for factor in normalise_banding_factors(banding_node.data.config):
        output_column = factor.get("outputColumn")
        if not isinstance(output_column, str) or not output_column:
            continue

        levels: list[str] = []
        seen: set[str] = set()
        rules = factor.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, dict):
                    _append_unique_factor_level(levels, seen, _banding_rule_output_level(rule))
        _append_unique_factor_level(levels, seen, factor.get("default"))
        if levels:
            order[output_column] = levels
    return order


def _ratebook_factor_table_level_order(
    table_name: str,
    factor_level_order: dict[str, list[str]],
) -> list[str]:
    direct_order = factor_level_order.get(table_name)
    if direct_order is not None:
        return direct_order

    columns = table_name.split(":")
    if len(columns) <= 1:
        return []

    component_orders = [factor_level_order.get(column) for column in columns]
    if any(order is None for order in component_orders):
        return []
    populated_orders = [order for order in component_orders if order is not None]
    return [_RATEBOOK_FACTOR_LEVEL_SEPARATOR.join(values) for values in product(*populated_orders)]


def _ratebook_factor_table_position(
    table_name: str,
    factor_level_order: dict[str, list[str]],
) -> tuple[int, ...] | None:
    factor_positions = {name: index for index, name in enumerate(factor_level_order)}
    direct_position = factor_positions.get(table_name)
    if direct_position is not None:
        return (direct_position,)

    columns = table_name.split(":")
    if len(columns) <= 1:
        return None
    positions = [factor_positions.get(column) for column in columns]
    if any(position is None for position in positions):
        return None
    return tuple(position for position in positions if position is not None)


def _ratebook_factor_table_sort_key(
    index_and_item: tuple[int, tuple[Any, Any]],
    factor_level_order: dict[str, list[str]],
) -> tuple[int, tuple[int, ...]]:
    original_index, (name, _table) = index_and_item
    fallback = (1, (original_index,))
    if not isinstance(name, str):
        return fallback
    position = _ratebook_factor_table_position(name, factor_level_order)
    return (0, position) if position is not None else fallback


def _ratebook_factor_level_counts(
    factors_df: Any | None,
    factor_columns: list[list[str]] | None,
) -> dict[str, dict[str, int]]:
    """Count quote exposure for each ratebook factor level.

    Table keys mirror price-contour's factor table output: single-column
    groups use the column name, composite groups join column names with
    ":".  Level keys are CANONICAL (3b.10): each component goes through the
    shared ``normalise_rating_key`` (joined with the unit separator), so the
    saved keys agree with what the apply-side rating join derives from frame
    values.  Two raw levels collapsing to one canonical key — possible only
    when a source column mixes value types — fail loudly, never merge.
    """
    import polars as pl

    if factors_df is None:
        return {}

    is_lazy = isinstance(factors_df, pl.LazyFrame)
    schema = factors_df.collect_schema() if is_lazy else factors_df.schema
    schema_names = set(schema.names())
    counts: dict[str, dict[str, int]] = {}
    for columns in factor_columns or []:
        if not columns:
            continue
        missing = [column for column in columns if column not in schema_names]
        if missing:
            raise ValueError(
                "Ratebook factor count columns are missing from aligned factors dataframe: "
                f"{missing}"
            )
        table_name = _ratebook_factor_table_name(columns)
        grouped = factors_df.group_by(columns).agg(pl.len().alias("quote_count"))
        if is_lazy:
            count_rows = streaming_collect(grouped).to_dicts()
        else:
            count_rows = grouped.to_dicts()
        table_counts: dict[str, int] = {}
        level_sources: dict[str, list[Any]] = {}
        column_dtypes = [schema[column] for column in columns]
        for row in count_rows:
            values = [row[column] for column in columns]
            level_key = _ratebook_factor_level_key(values, column_dtypes)
            if level_key in table_counts:
                raise ValueError(
                    f"Ratebook factor levels {level_sources[level_key]!r} and {values!r} in "
                    f"factor table {table_name!r} both canonicalise to {level_key!r}; the "
                    "source column mixes value types. Cast it to a single type upstream."
                )
            table_counts[level_key] = int(row["quote_count"])
            level_sources[level_key] = values
        counts[table_name] = table_counts
    return counts


def _ratebook_factor_dtypes(
    factors_df: Any | None,
    factor_columns: list[list[str]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Describe every solved factor table's ordered originating dtypes."""
    import polars as pl

    if factors_df is None:
        return {}
    schema = (
        factors_df.collect_schema() if isinstance(factors_df, pl.LazyFrame) else factors_df.schema
    )
    schema_names = set(schema.names())
    result: dict[str, list[dict[str, Any]]] = {}
    for columns in factor_columns or []:
        if not columns:
            continue
        missing = [column for column in columns if column not in schema_names]
        if missing:
            raise ValueError(
                "Ratebook factor dtype columns are missing from aligned factors "
                f"dataframe: {missing}"
            )
        result[_ratebook_factor_table_name(columns)] = [
            {
                "column": column,
                "dtype": rating_dtype_descriptor(schema[column]),
            }
            for column in columns
        ]
    return result


def _ratebook_factor_level_counts_from_artifact(
    handle: dict[str, Any],
    factor_columns: list[list[str]] | None,
) -> dict[str, dict[str, int]]:
    """Count ratebook factor levels from the persisted factor artifact lazily."""
    return _ratebook_factor_level_counts(
        _optimiser_artifacts._scan_ratebook_factors_artifact(handle),
        factor_columns,
    )


def _ratebook_factor_dtypes_from_artifact(
    handle: dict[str, Any],
    factor_columns: list[list[str]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Read ratebook dtype metadata from the persisted solved-factor schema."""
    return _ratebook_factor_dtypes(
        _optimiser_artifacts._scan_ratebook_factors_artifact(handle),
        factor_columns,
    )


def _ratebook_factor_artifact_quote_id(
    handle: dict[str, Any],
    config: Mapping[str, Any],
) -> str:
    """Resolve the quote-id column available in a ratebook factor artifact."""
    columns = handle.get("columns")
    available = set(columns) if isinstance(columns, list) else set()
    qid_col = str(config.get("quote_id", "quote_id"))
    if qid_col in available:
        return qid_col
    raise RuntimeError(f"Ratebook banding source must include quote id column {qid_col!r}.")


def _build_ratebook_factor_contexts(
    handle: dict[str, Any],
    quote_grid: Any,
    config: Mapping[str, Any],
    factor_columns: list[list[str]],
    *,
    chunk_decision: _ChunkSizeDecision | None = None,
) -> Any:
    """Build price-contour factor contexts from a persisted ratebook factor artifact."""
    artifact_path, _artifact_dir = _optimiser_artifacts._validate_ratebook_factors_artifact_handle(
        handle
    )
    # ``QuoteGrid.quote_ids`` is already a fresh ``list[str]`` (a PyO3 ``Vec<String>``).
    quote_ids = quote_grid.quote_ids
    try:
        if chunk_decision is None:
            chunk_decision = _chunk_size_decision_for_parquet(
                config,
                artifact_path,
                source="ratebook_factor_contexts",
            )
        chunk_size = chunk_decision.chunk_size
    except ValueError as exc:
        raise RuntimeError(f"Ratebook factor context chunk sizing failed: {exc}") from exc
    return price_contour().build_ratebook_factor_contexts_from_parquet_chunked(
        str(artifact_path),
        factor_columns,
        chunk_size,
        quote_id=_ratebook_factor_artifact_quote_id(handle, config),
        expected_quote_ids=quote_ids,
        expected_n_quotes=quote_grid.n_quotes,
    )


def _sort_ratebook_factor_tables(
    factor_tables: dict[Any, Any],
    factor_level_order: dict[str, list[str]],
) -> list[tuple[Any, Any]]:
    """Order factor tables by the configured banding-rule order.

    Tables not present in ``factor_level_order`` retain their original
    insertion order behind the configured ones (fallback bucket ``1``).
    """
    return [
        item
        for _index, item in sorted(
            enumerate(factor_tables.items()),
            key=lambda item: _ratebook_factor_table_sort_key(item, factor_level_order),
        )
    ]


def _serialise_ratebook_factor_table_rows(
    name: str,
    table: dict[Any, Any],
    level_counts: dict[str, int],
    factor_level_order: dict[str, list[str]],
    factor_dtypes: list[pl.DataType],
) -> list[dict[str, Any]]:
    """Serialise one factor table's rows, ordered by configured level order.

    Levels not present in the configured order fall through to insertion order
    behind the ordered ones, matching the table-level ordering convention.

    Saved ``__factor_group__`` labels are CANONICAL (3b.10): solver-emitted
    levels are translated through :func:`_canonical_ratebook_table_level`
    using the solved frame's exact ordered factor dtypes, so a Float32 value
    widened by Python is reconstructed before the apply key is saved. Two
    emitted levels collapsing to one canonical key fail loudly —
    last-writer-wins would silently drop a solved rate.
    """
    configured_level_order = _ratebook_factor_table_level_order(name, factor_level_order)
    level_positions = {level: index for index, level in enumerate(configured_level_order)}
    ordered_rows: list[tuple[tuple[int, int], dict[str, Any]]] = []
    emitted_by_canonical: dict[str, Any] = {}
    for original_index, (level, scenario_value) in enumerate(table.items()):
        level_key = _canonical_ratebook_table_level(
            name,
            level,
            level_counts,
            factor_dtypes,
        )
        if level_key in emitted_by_canonical:
            raise ValueError(
                f"Ratebook factor table {name!r} levels {emitted_by_canonical[level_key]!r} "
                f"and {level!r} both canonicalise to {level_key!r}; the solver input mixed "
                "value types in one factor column. Cast it to a single type and re-solve."
            )
        emitted_by_canonical[level_key] = level
        scenario_float = float(scenario_value)
        if not np.isfinite(scenario_float):
            raise ValueError(f"Ratebook factor table {name!r} contains a non-finite rate.")
        sort_key = (
            (0, level_positions[level_key]) if level_key in level_positions else (1, original_index)
        )
        ordered_rows.append(
            (
                sort_key,
                {
                    "__factor_group__": level_key,
                    "optimal_scenario_value": scenario_float,
                    # The canonical key is always counted: the translation
                    # fails loudly when no counts key matches the level.
                    "quote_count": int(level_counts[level_key]),
                },
            )
        )
    return [row for _sort_key, row in sorted(ordered_rows, key=lambda item: item[0])]


def _ratebook_serialisation_dtypes(
    table_name: str,
    records: object,
) -> list[pl.DataType]:
    """Validate and reconstruct one table's ordered factor dtype metadata."""
    expected_columns = table_name.split(":")
    if not isinstance(records, list) or len(records) != len(expected_columns):
        raise ValueError(
            f"Ratebook factor_dtypes for {table_name!r} must contain one ordered "
            "record per factor column."
        )
    dtypes: list[pl.DataType] = []
    for index, (expected_column, record) in enumerate(zip(expected_columns, records)):
        if not isinstance(record, dict) or set(record) != {"column", "dtype"}:
            raise ValueError(
                f"Ratebook factor_dtypes for {table_name!r} has a malformed "
                f"record at index {index}."
            )
        column = record.get("column")
        descriptor = record.get("dtype")
        if column != expected_column:
            raise ValueError(
                f"Ratebook factor_dtypes for {table_name!r} expected column "
                f"{expected_column!r} at index {index}, got {column!r}."
            )
        try:
            dtypes.append(rating_dtype_from_descriptor(descriptor))
        except ValueError as exc:
            raise ValueError(
                f"Ratebook factor_dtypes for {table_name!r} has an invalid dtype "
                f"descriptor at index {index}."
            ) from exc
    return dtypes


def _serialise_ratebook_factor_tables(
    factor_tables: Any,
    factor_level_counts: dict[str, dict[str, int]],
    factor_level_order: dict[str, list[str]],
    factor_dtypes: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Serialise ratebook factor tables for the API, ordered by banding rules.

    Level labels are canonicalised against ``factor_level_counts`` at save
    time (3b.10) — see :func:`_serialise_ratebook_factor_table_rows`.
    """
    if not isinstance(factor_tables, dict):
        raise ValueError("Ratebook factor tables are invalid")
    if not isinstance(factor_dtypes, dict):
        raise ValueError("Ratebook factor_dtypes are invalid")

    serialised: dict[str, list[dict[str, Any]]] = {}
    for name, table in _sort_ratebook_factor_tables(factor_tables, factor_level_order):
        if not isinstance(name, str) or not isinstance(table, dict):
            raise ValueError("Ratebook factor tables are invalid")
        level_counts = factor_level_counts.get(name)
        if level_counts is None:
            raise ValueError(f"Ratebook factor counts missing for factor table {name!r}.")
        table_dtypes = _ratebook_serialisation_dtypes(name, factor_dtypes.get(name))
        serialised[name] = _serialise_ratebook_factor_table_rows(
            name,
            table,
            level_counts,
            factor_level_order,
            table_dtypes,
        )
    return serialised


def _compute_ratebook_factor_level_order(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
    mode: str,
) -> dict[str, list[str]]:
    """Return the banding-rule level order for ratebook mode, ``{}`` otherwise.

    Computed once at solve start from the graph and threaded through to the
    background solver as an explicit parameter — never injected into the
    user-facing config dict.
    """
    if mode != "ratebook":
        return {}
    return _ratebook_factor_level_order(graph, node_id, config)


def _json_records(value: Any) -> Any:
    """Replace every Polars frame nested in *value* with its row records."""
    import polars as pl

    if isinstance(value, pl.DataFrame):
        return value.to_dicts()
    if isinstance(value, dict):
        return {key: _json_records(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_records(item) for item in value]
    return value


def _publish_summary(
    solver: Any,
    solve_result: SolveResultLike,
    *,
    job_id: str,
) -> dict[str, Any] | None:
    """The anchor's MLflow summary, holding no frame, or ``None`` if it cannot be built.

    A failure here must not fail a finished solve: it is logged, and logging the
    anchor to MLflow later asks the user to re-run the solve.
    """
    try:
        summary = solver.summary(solve_result)
    except Exception as exc:
        logger.warning("publish_summary_failed", error=str(exc), job_id=job_id, exc_info=True)
        return None
    if not isinstance(summary, dict):
        logger.warning(
            "publish_summary_invalid",
            summary_type=type(summary).__name__,
            job_id=job_id,
        )
        return None
    return cast(dict[str, Any], _json_records(summary))


def _finalize_solve_result(
    solve_result: SolveResultLike,
    *,
    mode: str,
    solver: Any,
    quote_grid: QuoteGrid,
    store: JobStore,
    job_id: str,
    elapsed: float,
    extra_fields: dict[str, Any] | None = None,
    extra_job_fields: dict[str, Any] | None = None,
    factors_df: pl.DataFrame | None = None,
    ratebook_factors_handle: dict[str, Any] | None = None,
    ratebook_factor_contexts: Any | None = None,
    factor_columns: list[list[str]] | None = None,
    check_cancelled: Callable[[], None] | None = None,
    quote_analysis_handle: dict[str, Any] | None = None,
) -> bool:
    """Build the result dict and update the job with the solve outcome.

    Returns whether this call published the completion, and so whether the job
    adopted the artifact handles it was given.

    Shared by ``_solve_online`` and ``_solve_ratebook`` to avoid duplicating
    the ~30 lines of result-dict construction, convergence warning, and
    store update boilerplate.

    Parameters
    ----------
    solve_result:
        The solver result object (online or ratebook).
    mode:
        ``"online"`` or ``"ratebook"``.
    solver:
        The solver instance (stored on the job for later use).
    quote_grid:
        The QuoteGrid (stored on the job for apply/frontier operations).
    store:
        The job store — updates are applied atomically via dict replacement.
    job_id:
        The job ID to update in the store.
    elapsed:
        Wall-clock seconds since the solve started.
    extra_fields:
        Mode-specific keys to merge into the result dict (e.g.
        ``iterations``, ``factor_tables``).
    quote_analysis_handle:
        The setup-owned analysis table, adopted into ``artifact_handles`` by
        the completion that publishes this result.
    """
    diagnostics_errors: list[dict[str, str]] = []
    scenario_value_stats: dict[str, float] | None = None
    scenario_value_histogram: dict[str, list[int] | list[float]] | None = None
    # Only an online solve has a per-quote frame; a ratebook solve reports no
    # statistics by design.
    if mode == "online":
        try:
            scenario_value_stats, scenario_value_histogram = _compute_scenario_value_stats(
                solve_result
            )
        except Exception as exc:
            diagnostics_errors.append(_diagnostic_error("scenario_value_stats", exc, job_id=job_id))

    result_dict: dict[str, Any] = {
        "mode": mode,
        "total_objective": solve_result.total_objective,
        "baseline_objective": solve_result.baseline_objective,
        "constraints": solve_result.total_constraints,
        "baseline_constraints": solve_result.baseline_constraints,
        "lambdas": solve_result.lambdas,
        "converged": solve_result.converged,
        "scenario_value_stats": scenario_value_stats,
        "scenario_value_histogram": scenario_value_histogram,
    }
    if extra_fields:
        result_dict.update(extra_fields)
    if not solve_result.converged:
        result_dict["warning"] = NON_CONVERGED_WARNING

    # ── Compute efficient frontier when explicitly requested (non-fatal) ────
    frontier_data = None
    frontier_factor_tables: list[dict[str, dict[str, float]]] | None = None
    frontier_error = None
    # Read through JobStore so concurrent eviction cannot race this snapshot.
    job_snapshot: Mapping[str, Any] = store.get_job(job_id) or {}
    config = job_snapshot.get("config", {})
    result_dict["input_summary"] = solve_input_summary(job_snapshot)
    # The grid setup recorded from the solver input; never re-derived here.
    result_dict["scenario_grid"] = require_scenario_grid(job_snapshot)
    constraints = config.get("constraints")
    kinds = constraint_kinds(constraints or {})
    # The absolute bounds the library solved at (pct constraints already scaled).
    result_dict["effective_bounds"] = effective_bounds(kinds, solve_result.constraint_bounds)
    if constraints and config.get("frontier_enabled") is True:
        try:
            frontier_steps = config.get("frontier_steps", _DEFAULT_FRONTIER_STEPS)
            ranges = _auto_frontier_ranges_from_config(config)
            if ranges:
                enforce_frontier_compute_budget(
                    n_points_per_dim=frontier_steps,
                    n_constraints=len(ranges),
                )
                progress_job = store.atomic_update(
                    job_id,
                    {
                        "message": "Computing efficient frontier",
                        "progress": 0.8,
                        "elapsed_seconds": _job_elapsed_seconds(job_snapshot, elapsed),
                    },
                    expected_status="running",
                )
                if progress_job is None:
                    logger.info(
                        "frontier_start_skipped",
                        job_id=job_id,
                        expected_status="running",
                    )
                    return False
                job_snapshot = progress_job
                frontier_result = _compute_frontier(
                    solver,
                    quote_grid,
                    mode=mode,
                    ratebook_factors=ratebook_factor_contexts if mode == "ratebook" else None,
                    factor_columns=factor_columns,
                    threshold_ranges=ranges,
                    n_points_per_dim=frontier_steps,
                    initial_lambdas=solve_result.lambdas,
                    check_cancelled=check_cancelled,
                )
                frontier_data = limited_frontier_payload(
                    frontier_result.points,
                    mode=mode,
                    constraint_kinds=kinds,
                    swept_axes=list(ranges),
                    frontier_generation=0,
                )
                frontier_factor_tables = frontier_point_factor_tables(
                    frontier_result,
                    mode=mode,
                    points_returned=frontier_data["points_returned"],
                )
                logger.info(
                    "frontier_computed",
                    n_points=frontier_data["n_points"],
                    job_id=job_id,
                )
        except (BackgroundJobStoppedError, ExecutionCancelledError):
            raise
        except Exception as exc:
            frontier_error = f"Frontier unavailable: {exc}"
            diagnostics_errors.append(_diagnostic_error("frontier", exc, job_id=job_id))

    result_dict["frontier"] = frontier_data
    # A solve starts the job's frontier generations; see ``completion_fields``.
    result_dict["frontier_generation"] = 0
    if frontier_error is not None:
        result_dict["frontier_error"] = frontier_error
    result_dict["diagnostics_errors"] = diagnostics_errors
    # Built before the publisher persists (and drops) the apply dataframe the
    # online summary reads, so publishing never needs the solver again.
    publish_summary = _publish_summary(solver, solve_result, job_id=job_id)
    completion_elapsed = _job_elapsed_seconds(
        store.get_job(job_id) or job_snapshot,
        elapsed,
    )
    uncommitted_handles: list[tuple[dict[str, Any], str]] = []
    if quote_analysis_handle is not None:
        uncommitted_handles.append(
            (
                quote_analysis_handle,
                "solve_completion_orphan_quote_analysis_cleanup_failed",
            )
        )
    if ratebook_factors_handle is not None:
        uncommitted_handles.append(
            (
                ratebook_factors_handle,
                "solve_completion_orphan_factor_artifact_cleanup_failed",
            )
        )

    def publish_completion_fields() -> Mapping[str, Any]:
        """Persist durable artifacts only after this worker owns completion."""
        artifact_handles: dict[str, Any] = {}
        # Only an online solve has a per-quote frame; ratebook has factor tables.
        if mode == "online":
            apply_result_handle = _optimiser_artifacts._persist_apply_result_artifact(solve_result)
            artifact_handles[_optimiser_artifacts._APPLY_RESULT_HANDLE_KEY] = apply_result_handle
            uncommitted_handles.append(
                (
                    apply_result_handle,
                    "solve_completion_orphan_apply_artifact_cleanup_failed",
                )
            )

        factor_handle = ratebook_factors_handle
        if factor_handle is None:
            factor_handle = _optimiser_artifacts._persist_ratebook_factors_artifact(factors_df)
            if factor_handle is not None:
                uncommitted_handles.append(
                    (
                        factor_handle,
                        "solve_completion_orphan_factor_artifact_cleanup_failed",
                    )
                )
        if factor_handle is not None:
            artifact_handles[_optimiser_artifacts._RATEBOOK_FACTORS_HANDLE_KEY] = factor_handle
        if quote_analysis_handle is not None:
            artifact_handles[_optimiser_artifacts._QUOTE_ANALYSIS_HANDLE_KEY] = (
                quote_analysis_handle
            )

        completion_fields: dict[str, Any] = {
            "progress": 1.0,
            "solver": solver,
            "solve_result": solve_result,
            "quote_grid": quote_grid,
            "factor_columns_valid": factor_columns,
            "result": result_dict,
            "base_result": dict(result_dict),
            "publish_summary": publish_summary,
            "frontier_data": frontier_data,
            _FRONTIER_FACTOR_TABLES_KEY: frontier_factor_tables,
            "artifact_handles": artifact_handles,
            **(extra_job_fields or {}),
            _FRONTIER_GENERATION_KEY: 0,
        }
        if ratebook_factor_contexts is not None:
            completion_fields["ratebook_factor_contexts"] = ratebook_factor_contexts
        return completion_fields

    def cleanup_uncommitted_handles() -> None:
        for handle, event in uncommitted_handles:
            _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                handle,
                job_id=job_id,
                event=event,
            )

    # Artifact persistence and the terminal record now share the store's one
    # running-job claim. Cancellation either wins before the publisher runs or
    # observes the complete artifact/result pair afterwards.
    try:
        updated_job = JobLifecycle(store).publish_completion(
            job_id,
            publish=publish_completion_fields,
            message="Completed",
            elapsed_seconds=completion_elapsed,
        )
    except BaseException:
        cleanup_uncommitted_handles()
        raise
    if updated_job is None:
        logger.info("solve_completion_skipped", job_id=job_id, expected_status="running")
        cleanup_uncommitted_handles()
        return False
    return True


@require_solver_worker_context
def _solve_online(
    ctx: SolveContext,
    *,
    quote_grid: QuoteGrid,
    config: dict[str, Any],
    quote_analysis_handle: dict[str, Any] | None = None,
) -> bool:
    """Run the online optimiser solver on a pre-built QuoteGrid.

    *quote_analysis_handle* is the setup-owned analysis table the completion
    adopts (``None`` without analysis columns). Returns whether the completion
    was published, the job then owning the handles.
    """
    if ctx.store is None:
        raise RuntimeError("_solve_online requires SolveContext.store to be set.")
    store = ctx.store
    job_id = ctx.job_id
    check_cancelled = ctx.check_cancelled
    if ctx.start_time is None:
        raise RuntimeError("_solve_online requires SolveContext.start_time to be set.")
    start_time = ctx.start_time

    if check_cancelled is not None:
        check_cancelled()
    try:
        solver = price_contour().OnlineOptimiser(
            objective=config["objective"],
            constraints=config["constraints"] or None,
            max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
            tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
            # Every online solve records its history, bounded by max_iter (Q5).
            record_history=True,
        )
        solve_result: OnlineSolveResultLike = solver.solve(quote_grid)
    except (BackgroundJobStoppedError, ExecutionCancelledError):
        raise
    except Exception as exc:
        raise _OptimiserSolverExecutionError(str(exc)) from exc
    if check_cancelled is not None:
        check_cancelled()
    if solve_result.history is None:
        raise RuntimeError(
            "price_contour returned no history for an online solve run with record_history=True"
        )
    elapsed = time.monotonic() - start_time
    logger.info(
        "solve_completed",
        mode="online",
        elapsed=f"{elapsed:.2f}s",
        converged=solve_result.converged,
    )

    return _finalize_solve_result(
        solve_result,
        mode="online",
        solver=solver,
        quote_grid=solve_result.grid,
        factors_df=None,
        store=store,
        job_id=job_id,
        elapsed=elapsed,
        extra_fields={
            "iterations": solve_result.iterations,
            "n_quotes": solve_result.n_quotes,
            "n_steps": solve_result.n_steps,
            "history": solve_result.history,
            "ratebook_cd_trace": None,
        },
        check_cancelled=check_cancelled,
        quote_analysis_handle=quote_analysis_handle,
    )


@dataclass(frozen=True, slots=True)
class SolveContext:
    """Per-solve context that travels end-to-end through the solver pipeline."""

    job_id: str
    node_id: str
    mode: str
    store: JobStore | None = None
    execution_context: ExecutionContext | None = None
    setup_singleflight_key: tuple[str, str, str] | None = None
    registration_already_active: bool = False
    start_time: float | None = None
    check_cancelled: Callable[[], None] | None = None


@require_solver_worker_context
def _solve_ratebook(
    ctx: SolveContext,
    *,
    quote_grid: QuoteGrid,
    config: dict[str, Any],
    ratebook_factors_handle: dict[str, Any] | None,
    factor_level_order: dict[str, list[str]] | None = None,
    quote_analysis_handle: dict[str, Any] | None = None,
) -> bool:
    """Run the ratebook optimiser solver on a pre-built QuoteGrid.

    *quote_analysis_handle* is the setup-owned analysis table the completion
    adopts (``None`` without analysis columns). Returns whether the completion
    was published, the job then owning the handles.
    """
    if ctx.store is None:
        raise RuntimeError("_solve_ratebook requires SolveContext.store to be set.")
    store = ctx.store
    job_id = ctx.job_id
    check_cancelled = ctx.check_cancelled
    if ctx.start_time is None:
        raise RuntimeError("_solve_ratebook requires SolveContext.start_time to be set.")
    start_time = ctx.start_time

    if ratebook_factors_handle is None:
        raise _OptimiserSolveInputError(
            "Ratebook mode requires a banding source. "
            "Select a banding node in the Rating Factor Source dropdown."
        )
    if check_cancelled is not None:
        check_cancelled()

    constraints = config["constraints"]

    raw_factor_columns = config.get("factor_columns", [])
    available_raw = ratebook_factors_handle.get("columns")
    available_cols = set(available_raw) if isinstance(available_raw, list) else set()
    missing = [c for group in raw_factor_columns for c in group if c not in available_cols]
    if missing:
        raise _OptimiserSolveInputError(
            f"Missing ratebook factor columns in banding source: {missing}. "
            f"Available columns: {sorted(available_cols)}"
        )
    factor_columns_valid = [list(group) for group in raw_factor_columns]

    factor_artifact_path, _factor_artifact_dir = (
        _optimiser_artifacts._validate_ratebook_factors_artifact_handle(ratebook_factors_handle)
    )
    factor_chunk_decision = _chunk_size_decision_for_parquet(
        config,
        factor_artifact_path,
        source="ratebook_factor_contexts",
    )
    try:
        factor_contexts = _build_ratebook_factor_contexts(
            ratebook_factors_handle,
            quote_grid,
            config,
            factor_columns_valid,
            chunk_decision=factor_chunk_decision,
        )
    except ValueError as exc:
        raise _OptimiserSolveInputError(str(exc)) from exc

    try:
        solver = price_contour().RatebookOptimiser(
            objective=config["objective"],
            constraints=constraints,
            factor_columns=factor_columns_valid,
            max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
            max_cd_iterations=config.get("max_cd_iterations", _DEFAULT_MAX_CD_ITERATIONS),
            cd_tolerance=config.get("cd_tolerance", _DEFAULT_CD_TOLERANCE),
            tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
        )
        solve_result: RatebookSolveResultLike = solver.solve(quote_grid, factor_contexts)
    except (BackgroundJobStoppedError, ExecutionCancelledError):
        raise
    except Exception as exc:
        raise _OptimiserSolverExecutionError(str(exc)) from exc
    if check_cancelled is not None:
        check_cancelled()
    elapsed = time.monotonic() - start_time
    converged = solve_result.converged
    logger.info("solve_completed", mode="ratebook", elapsed=f"{elapsed:.2f}s", converged=converged)
    cd_trace = ratebook_cd_trace(
        solve_result.per_factor_results, list(solve_result.total_constraints)
    )

    factor_level_counts = _ratebook_factor_level_counts_from_artifact(
        ratebook_factors_handle,
        factor_columns_valid,
    )
    factor_dtypes = _ratebook_factor_dtypes_from_artifact(
        ratebook_factors_handle,
        factor_columns_valid,
    )
    resolved_level_order = factor_level_order or {}
    existing_setup_chunking = store.require_job(job_id).get("setup_chunking")
    setup_chunking = (
        dict(existing_setup_chunking) if isinstance(existing_setup_chunking, Mapping) else {}
    )
    setup_chunking["ratebook_factor_contexts"] = factor_chunk_decision.provenance
    factor_tables_serialised = _serialise_ratebook_factor_tables(
        solve_result.factor_tables,
        factor_level_counts,
        resolved_level_order,
        factor_dtypes,
    )

    return _finalize_solve_result(
        solve_result,
        mode="ratebook",
        solver=solver,
        quote_grid=quote_grid,
        ratebook_factors_handle=ratebook_factors_handle,
        ratebook_factor_contexts=factor_contexts,
        factor_columns=factor_columns_valid,
        store=store,
        job_id=job_id,
        elapsed=elapsed,
        extra_fields={
            "cd_iterations": solve_result.cd_iterations,
            # The grid the solve scored, as an online result reports it.
            "n_quotes": quote_grid.n_quotes,
            "n_steps": quote_grid.n_steps,
            "factor_tables": factor_tables_serialised,
            "factor_dtypes": factor_dtypes,
            "clamp_rate": solve_result.clamp_rate,
            # The scenario range the solve scored: the deployed collar (Q17).
            COMBINED_FACTOR_BOUNDS_KEY: combined_factor_bounds_from_grid(
                quote_grid.scenario_values
            ),
            "history": None,
            "ratebook_cd_trace": cd_trace,
        },
        extra_job_fields={
            "factor_level_counts": factor_level_counts,
            "factor_dtypes": factor_dtypes,
            _RATEBOOK_FACTOR_LEVEL_ORDER_KEY: resolved_level_order,
            "setup_chunking": setup_chunking,
        },
        check_cancelled=check_cancelled,
        quote_analysis_handle=quote_analysis_handle,
    )
