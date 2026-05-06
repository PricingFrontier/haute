"""OptimiserSolveService — orchestrates optimisation solving, extracted from the route handler.

The route handler becomes a thin adapter that delegates to
``OptimiserSolveService.start()``.
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from fastapi import HTTPException

if TYPE_CHECKING:
    import polars as pl
    from price_contour import QuoteGrid

from haute._logging import get_logger
from haute._types import (
    GraphNode,
    OnlineSolveResultLike,
    PipelineGraph,
    RatebookSolveResultLike,
    SolveResultLike,
)
from haute.graph_utils import NodeType
from haute.routes._helpers import find_typed_node
from haute.routes._job_store import JobStore, register_artifact_cleaner
from haute.routes._optimiser_limits import limited_frontier_payload
from haute.schemas import (
    OptimiserEstimateRequest,
    OptimiserFrontierAutoRangeRequest,
    OptimiserFrontierAutoRangeResponse,
    OptimiserFrontierRange,
    OptimiserSolveRequest,
    OptimiserSolveResponse,
    _normalise_frontier_range_pair,
)

logger = get_logger(component="server.optimiser.solve")

# ── Default constants ─────────────────────────────────────────────
_DEFAULT_TIMEOUT = int(os.environ.get("HAUTE_SOLVER_TIMEOUT", "300"))
_HISTOGRAM_BINS = 20  # bin count for scenario-value distribution histogram
_DEFAULT_MAX_ITER = 50  # max solver iterations (online & ratebook)
_DEFAULT_CHUNK_SIZE = 500_000  # rows per chunk for solver processing
_DEFAULT_TOLERANCE = 1e-6  # convergence tolerance for solver
_DEFAULT_MAX_CD_ITERATIONS = 10  # max coordinate-descent iterations (ratebook)
_DEFAULT_CD_TOLERANCE = 1e-3  # coordinate-descent convergence tolerance (ratebook)
_APPLY_RESULT_HANDLE_KEY = "apply_result"
_APPLY_RESULT_HANDLE_KIND = "optimiser_apply_result"
_ARTIFACT_HANDLE_VERSION = 1
_APPLY_ARTIFACT_ROOT_NAME = "haute/optimiser_apply"
_APPLY_ARTIFACT_DIR_PREFIX = "apply_"
_APPLY_RESULT_FILENAME = "result.parquet"
_JOB_TYPE_KEY = "job_type"
_SOLVE_JOB_TYPE = "solve"
_ESTIMATE_JOB_TYPE = "estimate"
_FRONTIER_AUTO_RANGE_JOB_TYPE = "frontier_auto_range"
_NULL_QUOTE_ID_DETAIL_PREFIX = "Null quote_id values found in optimiser input"
_NON_BLOCKING_RUNNING_JOB_TYPES = frozenset(
    {
        _ESTIMATE_JOB_TYPE,
        _FRONTIER_AUTO_RANGE_JOB_TYPE,
    }
)


def _chunk_size_from_config(config: dict[str, Any]) -> int:
    raw_chunk_size = config.get("chunk_size", _DEFAULT_CHUNK_SIZE)
    if isinstance(raw_chunk_size, bool) or not isinstance(raw_chunk_size, int):
        raise ValueError("chunk_size must be a positive integer.")
    if raw_chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer.")
    return int(raw_chunk_size)


def _job_elapsed_seconds(job: dict[str, Any], fallback: float = 0.0) -> float:
    """Return wall-clock elapsed seconds for a job when start_time is available."""
    start_time = job.get("start_time")
    fallback_elapsed = max(0.0, float(fallback))
    if isinstance(start_time, bool) or not isinstance(start_time, int | float):
        return fallback_elapsed
    return max(fallback_elapsed, time.monotonic() - float(start_time), 0.0)


def _optimiser_side_input_ids(graph: PipelineGraph, node_id: str) -> frozenset[str]:
    """Return optimiser side-input node ids the solver needs after graph execution."""
    node = _find_optimiser_node(graph, node_id)
    config = node.data.config
    if config.get("mode", "online") != "ratebook":
        return frozenset()
    banding_source = config.get("banding_source")
    if isinstance(banding_source, str) and banding_source:
        return frozenset({banding_source})
    return frozenset()


def _apply_artifact_root() -> Path:
    return (Path(tempfile.gettempdir()) / _APPLY_ARTIFACT_ROOT_NAME).resolve()


def _prepare_apply_artifact_root() -> Path:
    root = _apply_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_apply_result_artifact_handle(handle: dict[str, Any]) -> tuple[Path, Path]:
    """Return validated ``(path, directory)`` for a server-owned apply artifact."""
    if handle.get("kind") != _APPLY_RESULT_HANDLE_KIND:
        raise ValueError("Invalid optimiser apply artifact handle.")
    if handle.get("version") != _ARTIFACT_HANDLE_VERSION:
        raise ValueError("Unsupported optimiser apply artifact handle.")
    if handle.get("format") != "parquet":
        raise ValueError("Unsupported optimiser apply artifact format.")

    raw_directory = handle.get("directory")
    if not isinstance(raw_directory, str) or not raw_directory:
        raise ValueError("Optimiser apply artifact handle has no directory.")
    raw_path = handle.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("Optimiser apply artifact handle has no path.")
    if "\x00" in raw_directory or "\x00" in raw_path:
        raise ValueError("Optimiser apply artifact handle contains an invalid path.")

    directory_input = Path(raw_directory)
    path_input = Path(raw_path)
    if not directory_input.is_absolute() or not path_input.is_absolute():
        raise ValueError("Optimiser apply artifact handle must use absolute paths.")

    root = _apply_artifact_root()
    directory = directory_input.resolve(strict=directory_input.exists())
    artifact_path = path_input.resolve(strict=path_input.exists())

    if not directory.is_relative_to(root):
        raise ValueError("Optimiser apply artifact directory is outside the artifact root.")
    if directory.parent != root or not directory.name.startswith(_APPLY_ARTIFACT_DIR_PREFIX):
        raise ValueError("Optimiser apply artifact directory is invalid.")
    if artifact_path.parent != directory:
        raise ValueError("Optimiser apply artifact path is outside its directory.")
    if artifact_path.name != _APPLY_RESULT_FILENAME:
        raise ValueError("Optimiser apply artifact path is invalid.")
    return artifact_path, directory


def _persist_apply_result_artifact(solve_result: SolveResultLike) -> dict[str, Any] | None:
    """Persist the large apply/detail dataframe behind an explicit handle."""
    if not hasattr(solve_result, "dataframe"):
        return None

    import polars as pl

    df = solve_result.dataframe
    if not isinstance(df, pl.DataFrame):
        return None

    artifact_dir = Path(
        tempfile.mkdtemp(
            prefix=_APPLY_ARTIFACT_DIR_PREFIX,
            dir=_prepare_apply_artifact_root(),
        ),
    )
    artifact_path = artifact_dir / _APPLY_RESULT_FILENAME
    try:
        df.write_parquet(artifact_path)
        row_count = len(df)
    except BaseException:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise

    return {
        "kind": _APPLY_RESULT_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
        "path": str(artifact_path),
        "directory": str(artifact_dir),
        "row_count": row_count,
    }


def _cleanup_apply_result_artifact(handle: dict[str, Any]) -> None:
    """Remove a newly-created apply artifact that no job owns."""
    _artifact_path, artifact_dir = _validate_apply_result_artifact_handle(handle)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)


def _load_apply_result_artifact(handle: dict[str, Any]) -> Any:
    """Load a persisted optimiser apply dataframe from a validated handle."""
    import polars as pl

    try:
        artifact_path, _artifact_dir = _validate_apply_result_artifact_handle(handle)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not artifact_path.is_file():
        raise HTTPException(
            status_code=500,
            detail="Optimiser apply artifact is missing. Re-run the solve to regenerate it.",
        )

    try:
        return pl.read_parquet(artifact_path)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Optimiser apply artifact is corrupt. Re-run the solve to regenerate it.",
        ) from exc


register_artifact_cleaner(_APPLY_RESULT_HANDLE_KIND, _cleanup_apply_result_artifact)


def _cleanup_orphan_apply_result_artifact(
    handle: dict[str, Any],
    *,
    job_id: str,
    event: str,
) -> None:
    """Best-effort cleanup for apply artifacts that were never attached to a job."""
    try:
        _cleanup_apply_result_artifact(handle)
    except Exception as cleanup_exc:
        raw_path = handle.get("directory") or handle.get("path") or "<unknown>"
        logger.warning(
            event,
            job_id=job_id,
            path=str(raw_path),
            error=str(cleanup_exc),
            exc_info=True,
        )


def _find_optimiser_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find and validate an optimiser node in the graph."""
    return find_typed_node(graph, node_id, NodeType.OPTIMISER, "optimiser")


def _compute_scenario_value_stats(
    solve_result: SolveResultLike,
) -> tuple[
    dict[str, float] | None,
    dict[str, list[int] | list[float]] | None,
]:
    """Compute scenario value distribution statistics and histogram from solve result."""
    if not hasattr(solve_result, "dataframe"):
        return None, None
    df = solve_result.dataframe
    if "optimal_scenario_value" not in df.columns:
        return None, None

    col = df["optimal_scenario_value"]
    n = len(col)
    if n == 0:
        return None, None
    stats = {
        "mean": float(col.mean()),
        "std": float(col.std()),
        "min": float(col.min()),
        "max": float(col.max()),
        "p5": float(col.quantile(0.05)),
        "p25": float(col.quantile(0.25)),
        "p50": float(col.quantile(0.50)),
        "p75": float(col.quantile(0.75)),
        "p95": float(col.quantile(0.95)),
        "pct_increase": float((col > 1.0).sum() / n) if n else 0.0,
        "pct_decrease": float((col < 1.0).sum() / n) if n else 0.0,
    }

    vals = col.to_numpy()
    counts, edges = np.histogram(vals, bins=_HISTOGRAM_BINS)
    histogram: dict[str, list[int] | list[float]] = {
        "counts": [int(c) for c in counts],
        "edges": [float(e) for e in edges],
    }
    return stats, histogram


def _compute_frontier(
    solver: Any,
    quote_grid: QuoteGrid,
    *,
    mode: str,
    factors_df: pl.DataFrame | None,
    threshold_ranges: dict[str, tuple[float, float]],
    n_points_per_dim: int,
    factor_columns: list[list[str]] | None = None,
    initial_lambdas: dict[str, float] | None = None,
) -> Any:
    """Call the mode-specific frontier API."""
    if mode == "ratebook":
        if factors_df is None:
            raise RuntimeError("Ratebook frontier requires a factors dataframe.")
        frontier_kwargs: dict[str, Any] = {
            "threshold_ranges": threshold_ranges,
            "n_points_per_dim": n_points_per_dim,
        }
        if factor_columns is not None:
            frontier_kwargs["factor_columns"] = factor_columns
        if initial_lambdas is not None:
            frontier_kwargs["initial_lambdas"] = initial_lambdas
        return solver.frontier(
            quote_grid,
            factors_df,
            **frontier_kwargs,
        )
    frontier_kwargs = {
        "threshold_ranges": threshold_ranges,
        "n_points_per_dim": n_points_per_dim,
    }
    if initial_lambdas is not None:
        frontier_kwargs["initial_lambdas"] = initial_lambdas
    return solver.frontier(quote_grid, **frontier_kwargs)


# Public alias preserved for existing in-tree imports; canonical
# implementation lives in ``haute.schemas`` so request-body validators and
# the config-side path share one source of truth.
_normalise_frontier_range = _normalise_frontier_range_pair


def _auto_frontier_ranges_from_config(config: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Build absolute frontier ranges from optimiser config.

    Prefer per-constraint ``frontier_ranges``.  The legacy scalar
    ``frontier_min`` / ``frontier_max`` pair remains a compatibility path for
    existing single-range configs, but it is no longer treated as a multiplier
    on baseline values.
    """
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
            ranges[str(cname)] = _normalise_frontier_range(
                configured_ranges[cname],
                field=f"frontier_ranges.{cname}",
            )
        return ranges

    if "frontier_min" not in config or "frontier_max" not in config:
        raise ValueError(
            "frontier_ranges must provide min and max for each constraint. "
            "Legacy frontier_min/frontier_max values are only used when both are "
            "explicitly configured."
        )

    frontier_min = float(config["frontier_min"])
    frontier_max = float(config["frontier_max"])
    if not np.isfinite(frontier_min) or not np.isfinite(frontier_max):
        raise ValueError("frontier_min and frontier_max must be finite values.")
    if frontier_min > frontier_max:
        raise ValueError("frontier_min must be less than or equal to frontier_max.")

    return {str(cname): (frontier_min, frontier_max) for cname in constraints}


def _estimate_scenario_frontier_ranges(
    scored_lf: Any,
    *,
    quote_id_col: str,
    constraint_cols: list[str],
) -> dict[str, dict[str, float]]:
    """Return exact online achievable min/max totals from the scenario frame.

    For each constraint, each quote can independently choose the scenario that
    minimises or maximises that constraint total.  This avoids relying on
    scenario ordering and only materialises two aggregate floats per
    constraint.
    """
    import polars as pl

    if not constraint_cols:
        return {}

    aggregate_exprs = []
    total_exprs = []
    aliases: dict[str, tuple[str, str]] = {}
    for idx, cname in enumerate(constraint_cols):
        min_alias = f"__haute_frontier_min_{idx}"
        max_alias = f"__haute_frontier_max_{idx}"
        aliases[cname] = (min_alias, max_alias)
        aggregate_exprs.extend(
            [
                pl.col(cname).min().alias(min_alias),
                pl.col(cname).max().alias(max_alias),
            ]
        )
        total_exprs.extend(
            [
                pl.col(min_alias).sum().alias(min_alias),
                pl.col(max_alias).sum().alias(max_alias),
            ]
        )

    totals = (
        scored_lf.group_by(quote_id_col)
        .agg(aggregate_exprs)
        .select(total_exprs)
        .collect(engine="streaming")
    )
    if totals.height != 1:
        raise ValueError("Unable to estimate frontier ranges from an empty scenario frame.")
    row = totals.row(0, named=True)

    ranges: dict[str, dict[str, float]] = {}
    for cname, (min_alias, max_alias) in aliases.items():
        min_value = float(row[min_alias])
        max_value = float(row[max_alias])
        if not np.isfinite(min_value) or not np.isfinite(max_value):
            raise ValueError(f"Estimated frontier range for {cname!r} is not finite.")
        if min_value > max_value:
            raise ValueError(f"Estimated frontier range for {cname!r} is invalid.")
        ranges[cname] = {"min": min_value, "max": max_value}
    return ranges


_RATEBOOK_FACTOR_LEVEL_SEPARATOR = "\x1f"


def _ratebook_factor_table_name(columns: list[str]) -> str:
    return ":".join(columns)


def _ratebook_factor_level_key(values: list[Any]) -> str:
    if any(value is None for value in values):
        raise ValueError("Ratebook factor counts cannot be computed with null factor levels.")
    return _RATEBOOK_FACTOR_LEVEL_SEPARATOR.join(str(value) for value in values)


def _ratebook_factor_level_counts(
    factors_df: pl.DataFrame | None,
    factor_columns: list[list[str]] | None,
) -> dict[str, dict[str, int]]:
    """Count quote exposure for each ratebook factor level.

    The table and level keys mirror price-contour's factor table output:
    single-column groups use the column name, composite groups join column
    names with ":" and level values with the unit separator.
    """
    import polars as pl

    if factors_df is None:
        return {}

    counts: dict[str, dict[str, int]] = {}
    for columns in factor_columns or []:
        if not columns:
            continue
        missing = [column for column in columns if column not in factors_df.columns]
        if missing:
            raise ValueError(
                "Ratebook factor count columns are missing from aligned factors dataframe: "
                f"{missing}"
            )
        table_name = _ratebook_factor_table_name(columns)
        count_rows = factors_df.group_by(columns).agg(pl.len().alias("quote_count")).to_dicts()
        counts[table_name] = {
            _ratebook_factor_level_key([row[column] for column in columns]): int(row["quote_count"])
            for row in count_rows
        }
    return counts


def _serialise_ratebook_factor_tables(
    factor_tables: Any,
    factor_level_counts: dict[str, dict[str, int]],
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(factor_tables, dict):
        raise ValueError("Ratebook factor tables are invalid")

    serialised: dict[str, list[dict[str, Any]]] = {}
    for name, table in factor_tables.items():
        if not isinstance(name, str) or not isinstance(table, dict):
            raise ValueError("Ratebook factor tables are invalid")
        level_counts = factor_level_counts.get(name)
        if level_counts is None:
            raise ValueError(f"Ratebook factor counts missing for factor table {name!r}.")
        rows: list[dict[str, Any]] = []
        for level, scenario_value in table.items():
            level_key = str(level)
            quote_count = level_counts.get(level_key)
            if quote_count is None:
                raise ValueError(
                    "Ratebook factor counts missing for level "
                    f"{level_key!r} in factor table {name!r}."
                )
            scenario_float = float(scenario_value)
            if not np.isfinite(scenario_float):
                raise ValueError(f"Ratebook factor table {name!r} contains a non-finite rate.")
            rows.append(
                {
                    "__factor_group__": level,
                    "optimal_scenario_value": scenario_float,
                    "quote_count": int(quote_count),
                }
            )
        serialised[name] = rows
    return serialised


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
    factor_columns: list[list[str]] | None = None,
) -> None:
    """Build the result dict and update the job with the solve outcome.

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
    """
    scenario_value_stats, scenario_value_histogram = _compute_scenario_value_stats(solve_result)

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
        result_dict["warning"] = (
            "Solver did not converge. Consider increasing max_iter or relaxing tolerance."
        )

    # ── Compute efficient frontier when explicitly requested (non-fatal) ────
    frontier_data = None
    frontier_error = None
    # M6: Use direct dict access to avoid _evict_stale() from background thread
    job_snapshot = store.jobs.get(job_id, {})
    config = job_snapshot.get("config", {})
    constraints = config.get("constraints")
    if constraints and config.get("frontier_enabled") is True:
        try:
            frontier_steps = config.get("frontier_steps", 15)
            ranges = _auto_frontier_ranges_from_config(config)
            if ranges:
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
                    return
                job_snapshot = progress_job
                frontier_result = _compute_frontier(
                    solver,
                    quote_grid,
                    mode=mode,
                    factors_df=factors_df,
                    factor_columns=factor_columns,
                    threshold_ranges=ranges,
                    n_points_per_dim=frontier_steps,
                    initial_lambdas=solve_result.lambdas,
                )
                frontier_data = limited_frontier_payload(
                    frontier_result.points,
                    constraint_names=list(ranges.keys()),
                )
                logger.info(
                    "frontier_computed",
                    n_points=frontier_data["n_points"],
                    job_id=job_id,
                )
        except Exception as exc:
            frontier_error = f"Frontier unavailable: {exc}"
            logger.warning(
                "frontier_computation_failed",
                error=str(exc),
                job_id=job_id,
                exc_info=True,
            )

    result_dict["frontier"] = frontier_data
    if frontier_error is not None:
        result_dict["frontier_error"] = frontier_error
    artifact_handles: dict[str, Any] = {}
    apply_result_handle = _persist_apply_result_artifact(solve_result)
    if apply_result_handle is not None:
        artifact_handles[_APPLY_RESULT_HANDLE_KEY] = apply_result_handle

    # P7: Atomic update — replace the entire dict to avoid races with
    # status-polling reads on the main thread.
    # L5: result_dict["frontier"] is the frontend-serialised frontier payload
    # (consumed by OptimiserStatusResponse).  "frontier_data" is a top-level
    # job key used by internal endpoints (e.g. /frontier/select) to look up
    # raw frontier points without going through the result dict.
    updated_job = store.atomic_update(
        job_id,
        {
            "status": "completed",
            "progress": 1.0,
            "message": "Completed",
            "elapsed_seconds": _job_elapsed_seconds(
                store.jobs.get(job_id, job_snapshot),
                elapsed,
            ),
            "solver": solver,
            "solve_result": solve_result,
            "quote_grid": quote_grid,
            "factors_df": factors_df,
            "factor_columns_valid": factor_columns,
            "result": result_dict,
            "base_result": dict(result_dict),
            "frontier_data": frontier_data,
            "artifact_handles": artifact_handles,
            **(extra_job_fields or {}),
        },
        expected_status="running",
    )
    if updated_job is None:
        logger.info("solve_completion_skipped", job_id=job_id, expected_status="running")
        if apply_result_handle is not None:
            _cleanup_orphan_apply_result_artifact(
                apply_result_handle,
                job_id=job_id,
                event="solve_completion_orphan_apply_artifact_cleanup_failed",
            )
        return
    if (
        apply_result_handle is not None
        and updated_job.get("artifact_handles") is not artifact_handles
    ):
        _cleanup_orphan_apply_result_artifact(
            apply_result_handle,
            job_id=job_id,
            event="solve_completion_orphan_apply_artifact_cleanup_failed",
        )

    # The job store keeps these heavy runtime objects for its short
    # heavy-object retention window, then slims the completed job down to
    # API-facing summaries/metadata while preserving the 24h status record.


def _solve_online(
    quote_grid: QuoteGrid,
    config: dict[str, Any],
    store: JobStore,
    job_id: str,
    start_time: float,
) -> None:
    """Run the online optimiser solver on a pre-built QuoteGrid."""
    from price_contour import OnlineOptimiser

    solver = OnlineOptimiser(
        objective=config["objective"],
        constraints=config["constraints"] or None,
        max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
        tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
        record_history=config.get("record_history", False),
    )
    solve_result: OnlineSolveResultLike = solver.solve(quote_grid)
    elapsed = time.monotonic() - start_time
    logger.info(
        "solve_completed",
        mode="online",
        elapsed=f"{elapsed:.2f}s",
        converged=solve_result.converged,
    )

    _finalize_solve_result(
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
            "history": solve_result.history if config.get("record_history") else None,
        },
    )


def _solve_ratebook(
    quote_grid: QuoteGrid,
    config: dict[str, Any],
    factors_df: pl.DataFrame | None,
    store: JobStore,
    job_id: str,
    start_time: float,
) -> None:
    """Run the ratebook optimiser solver on a pre-built QuoteGrid."""
    import polars as pl
    from price_contour import RatebookOptimiser

    if factors_df is None:
        raise RuntimeError(
            "Ratebook mode requires a banding source. "
            "Select a banding node in the Rating Factor Source dropdown."
        )

    constraints = config["constraints"]
    qid_col = config.get("quote_id", "quote_id")

    raw_factor_columns = config.get("factor_columns", [])
    available_cols = set(factors_df.columns)
    factor_columns_valid = [
        group for group in raw_factor_columns if all(c in available_cols for c in group)
    ]
    if not factor_columns_valid:
        missing = [c for group in raw_factor_columns for c in group if c not in available_cols]
        raise RuntimeError(
            f"No valid factor groups found. Missing columns in banding source: {missing}. "
            f"Available columns: {sorted(available_cols)}"
        )

    factor_cols_flat = list(dict.fromkeys(c for group in factor_columns_valid for c in group))
    avail = factors_df.columns
    if qid_col in avail:
        keep = [qid_col] + [c for c in factor_cols_flat if c in avail]
        factors_df = factors_df.select(keep)
        if qid_col != "quote_id":
            factors_df = factors_df.rename({qid_col: "quote_id"})
    elif "quote_id" in avail:
        keep = ["quote_id"] + [c for c in factor_cols_flat if c in avail]
        factors_df = factors_df.select(keep)
    else:
        raise RuntimeError(
            f"Ratebook banding source must include quote id column '{qid_col}' "
            "or a 'quote_id' column."
        )

    factors_df = factors_df.with_columns(pl.col("quote_id").cast(pl.Utf8))
    factors_df = factors_df.unique(subset=["quote_id"])

    quote_order = pl.DataFrame({"quote_id": quote_grid.quote_ids})
    quote_order = quote_order.unique(maintain_order=True)
    factors_df = quote_order.join(factors_df, on="quote_id", how="left")
    factors_df = factors_df.drop("quote_id")
    null_counts = factors_df.select(
        [pl.col(c).null_count().alias(c) for c in factor_cols_flat],
    ).row(0, named=True)
    null_factor_counts = {name: count for name, count in null_counts.items() if int(count) > 0}
    if null_factor_counts:
        formatted_counts = ", ".join(
            f"{name} ({count} {'row' if count == 1 else 'rows'})"
            for name, count in null_factor_counts.items()
        )
        raise ValueError(
            "Ratebook factor columns contain null values after aligning to quote grid: "
            f"{formatted_counts}. Configure non-null banding defaults, remove the "
            "affected factor columns, or ensure every quote_id has banding values."
        )

    solver = RatebookOptimiser(
        objective=config["objective"],
        constraints=constraints,
        factor_columns=factor_columns_valid,
        max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
        max_cd_iterations=config.get("max_cd_iterations", _DEFAULT_MAX_CD_ITERATIONS),
        cd_tolerance=config.get("cd_tolerance", _DEFAULT_CD_TOLERANCE),
        tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
    )

    solve_result: RatebookSolveResultLike = solver.solve(quote_grid, factors_df)
    elapsed = time.monotonic() - start_time
    converged = solve_result.converged
    logger.info("solve_completed", mode="ratebook", elapsed=f"{elapsed:.2f}s", converged=converged)

    factor_level_counts = _ratebook_factor_level_counts(factors_df, factor_columns_valid)
    factor_tables_serialised = _serialise_ratebook_factor_tables(
        solve_result.factor_tables,
        factor_level_counts,
    )

    _finalize_solve_result(
        solve_result,
        mode="ratebook",
        solver=solver,
        quote_grid=quote_grid,
        factors_df=factors_df,
        factor_columns=factor_columns_valid,
        store=store,
        job_id=job_id,
        elapsed=elapsed,
        extra_fields={
            "cd_iterations": solve_result.cd_iterations,
            "factor_tables": factor_tables_serialised,
            "clamp_rate": getattr(solve_result, "clamp_rate", None),
            "history": None,
        },
        extra_job_fields={"factor_level_counts": factor_level_counts},
    )


class OptimiserSolveService:
    """Orchestrates the full optimisation solve lifecycle.

    Parameters
    ----------
    store:
        The in-memory job store used to track optimisation jobs.
    """

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._start_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start(self, body: OptimiserSolveRequest) -> OptimiserSolveResponse:
        """Validate config, execute pipeline, build grid, and launch solver.

        Returns an ``OptimiserSolveResponse`` with status ``"started"``.
        Raises ``HTTPException`` on validation or pipeline failures.
        """
        node = _find_optimiser_node(body.graph, body.node_id)
        config = node.data.config

        mode = self._validate_config(config)

        with self._start_lock:
            self._check_no_concurrent_jobs()
            job_id = self._store.create_job(
                {
                    "status": "running",
                    _JOB_TYPE_KEY: _SOLVE_JOB_TYPE,
                    "progress": 0.0,
                    "message": "Starting",
                    "config": dict(config),
                    "node_label": node.data.label,
                }
            )
        logger.info("solve_started", node_id=body.node_id, mode=mode, job_id=job_id)

        # ``TemporaryDirectory`` removes the checkpoint dir even on signal/
        # crash; an interrupted long solve will not leak GBs of staging data.
        with tempfile.TemporaryDirectory(prefix="haute_opt_") as raw_dir:
            checkpoint_dir = Path(raw_dir)
            try:
                lazy_outputs = self._execute_pipeline(body, job_id, checkpoint_dir)
                source_lf = self._resolve_data_source(
                    lazy_outputs,
                    config,
                    body.node_id,
                    job_id,
                )
                constraint_cols, scored_lf = self._validate_and_project(
                    source_lf,
                    config,
                    job_id,
                )
                factors_df = self._extract_factors(lazy_outputs, config, mode)
                del lazy_outputs
                gc.collect()

                quote_grid = self._build_grid(
                    scored_lf,
                    constraint_cols,
                    config,
                    body.node_id,
                    job_id,
                )
            except HTTPException as exc:
                self._store.atomic_update(
                    job_id,
                    {"status": "error", "message": str(exc.detail)},
                    expected_status="running",
                )
                raise
            except Exception as exc:
                detail = f"Optimiser setup failed: {exc}"
                logger.error(
                    "optimiser_setup_failed",
                    error=str(exc),
                    node_id=body.node_id,
                    job_id=job_id,
                    exc_info=True,
                )
                self._store.atomic_update(
                    job_id,
                    {"status": "error", "message": detail},
                    expected_status="running",
                )
                raise HTTPException(
                    status_code=500,
                    detail="Optimiser setup failed. Check the server logs for details.",
                ) from exc
        self._launch_background(job_id, body.node_id, config, mode, quote_grid, factors_df)
        return OptimiserSolveResponse(status="started", job_id=job_id)

    def estimate_frontier_auto_range(
        self,
        body: OptimiserFrontierAutoRangeRequest,
    ) -> OptimiserFrontierAutoRangeResponse:
        """Estimate absolute frontier ranges from the scenario dataframe.

        The online optimiser can independently choose one scenario per quote,
        so summing per-quote extrema gives the exact achievable envelope for
        each constraint.  The calculation operates on the projected lazy frame
        and returns only tiny metadata.
        """
        node = _find_optimiser_node(body.graph, body.node_id)
        config = node.data.config
        mode = self._validate_config(config)

        job_id = self._store.create_job(
            {
                "status": "running",
                _JOB_TYPE_KEY: _FRONTIER_AUTO_RANGE_JOB_TYPE,
                "progress": 0.0,
                "message": "Estimating frontier range",
                "config": dict(config),
                "node_label": node.data.label,
            }
        )
        # ``TemporaryDirectory`` ensures the checkpoint dir is removed even
        # on signal/abort, where ``mkdtemp`` + ``rmtree`` in finally would
        # leak.  The store-level cleanup (``delete_job``) stays in an outer
        # finally so the job entry never outlives this call.
        try:
            with tempfile.TemporaryDirectory(prefix="haute_frontier_range_") as raw_dir:
                checkpoint_dir = Path(raw_dir)
                try:
                    lazy_outputs = self._execute_pipeline(body, job_id, checkpoint_dir)
                    source_lf = self._resolve_data_source(
                        lazy_outputs,
                        config,
                        body.node_id,
                        job_id,
                    )
                    constraint_cols, scored_lf = self._validate_and_project(
                        source_lf,
                        config,
                        job_id,
                    )
                    ranges = _estimate_scenario_frontier_ranges(
                        scored_lf,
                        quote_id_col=str(config.get("quote_id", "quote_id")),
                        constraint_cols=constraint_cols,
                    )
                    warning = None
                    if mode == "ratebook":
                        warning = (
                            "Auto range uses the scenario dataframe envelope. "
                            "Ratebook factor-table coupling can make the exact achievable "
                            "range narrower."
                        )
                    self._store.atomic_update(
                        job_id,
                        {
                            "status": "completed",
                            "progress": 1.0,
                            "message": "Completed",
                            "result": {"ranges": ranges},
                        },
                    )
                    response_ranges = {
                        name: OptimiserFrontierRange(min=value["min"], max=value["max"])
                        for name, value in ranges.items()
                    }
                    return OptimiserFrontierAutoRangeResponse(
                        status="ok",
                        ranges=response_ranges,
                        warning=warning,
                    )
                except HTTPException as exc:
                    self._store.atomic_update(
                        job_id,
                        {
                            "status": "error",
                            "message": str(exc.detail),
                        },
                        expected_status="running",
                    )
                    raise
                except Exception as exc:
                    logger.error(
                        "frontier_auto_range_failed",
                        error=str(exc),
                        node_id=body.node_id,
                        exc_info=True,
                    )
                    self._store.atomic_update(
                        job_id,
                        {
                            "status": "error",
                            "message": f"Frontier auto range failed: {exc}",
                        },
                    )
                    raise HTTPException(
                        status_code=500,
                        detail="Frontier auto range failed. Check the server logs for details.",
                    ) from exc
        finally:
            self._store.delete_job(job_id)

    # ------------------------------------------------------------------
    # Private orchestration steps
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> str:
        """Validate optimiser config; return the mode ('online' or 'ratebook')."""
        objective = config.get("objective")
        if not objective:
            raise HTTPException(
                status_code=400,
                detail="No objective column configured."
                " Open the config panel and set an objective.",
            )

        mode = config.get("mode", "online")
        if mode not in ("online", "ratebook"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported optimiser mode '{mode}'."
                " Currently supported: online, ratebook.",
            )

        if mode == "ratebook":
            factor_columns = config.get("factor_columns")
            if not factor_columns:
                raise HTTPException(
                    status_code=400,
                    detail="Ratebook mode requires factor_columns. Add at least one factor group.",
                )

        return str(mode)

    @staticmethod
    def _is_blocking_solve_job(job: dict[str, Any]) -> bool:
        """Return whether a job should reserve the real optimiser solve slot."""
        if job.get("status") != "running":
            return False
        return job.get(_JOB_TYPE_KEY, _SOLVE_JOB_TYPE) not in _NON_BLOCKING_RUNNING_JOB_TYPES

    def _has_running_solve_job(self) -> bool:
        return self._store.has_job_matching(self._is_blocking_solve_job)

    def _check_no_concurrent_jobs(self) -> None:
        """Reject if an optimisation solve job is already running."""
        if self._has_running_solve_job():
            raise HTTPException(
                status_code=409,
                detail="An optimisation job is already running. Please wait for it to finish.",
            )

    def _execute_pipeline(
        self,
        body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
        job_id: str,
        checkpoint_dir: Path,
    ) -> dict[str, Any]:
        """Execute the pipeline lazily up to the optimiser node.

        The caller owns *checkpoint_dir* lifecycle (creation + cleanup).
        """
        try:
            import polars as pl

            from haute.executor import (
                _build_node_fn,
                _compile_preamble,
                _pipeline_dir,
                _resolve_batch_scenario,
            )
            from haute.graph_utils import _execute_lazy

            # Resolve scenario: optimiser runs on batch data, not live.
            scenario = _resolve_batch_scenario(body.graph) or "batch"

            preamble_ns = (
                _compile_preamble(
                    body.graph.preamble or "",
                    force_refresh=False,
                    pipeline_dir=_pipeline_dir(body.graph),
                )
                or None
            )

            # Reduce streaming chunk size for the optimiser path (same
            # rationale as execute_sink — wide schemas with 100+ columns
            # can cause OOM with the default auto-sized chunk).
            _prev_chunk = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
            pl.Config.set_streaming_chunk_size(50_000)
            try:
                from haute.executor import ENFORCE_CONTRACTS

                lazy_outputs, *_ = _execute_lazy(
                    body.graph,
                    _build_node_fn,
                    target_node_id=body.node_id,
                    preamble_ns=preamble_ns,
                    source=scenario,
                    checkpoint_dir=checkpoint_dir,
                    enforce_contracts=ENFORCE_CONTRACTS,
                    preserve_node_ids=_optimiser_side_input_ids(body.graph, body.node_id),
                )
            finally:
                # Restore previous streaming chunk size if one was explicitly set.
                # When _prev_chunk is None (Polars auto-default), skip the restore
                # — Polars does not accept 0 and has no "unset" API.
                if _prev_chunk is not None:
                    pl.Config.set_streaming_chunk_size(int(_prev_chunk))
            return lazy_outputs
        except HTTPException:
            raise
        except Exception as exc:
            error_msg = f"Pipeline execution failed: {exc}"
            logger.error(
                "pipeline_exec_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._store.atomic_update(job_id, {"status": "error", "message": error_msg})
            raise HTTPException(
                status_code=500,
                detail="Pipeline execution failed. Check the server logs for details.",
            )

    def _resolve_data_source(
        self,
        lazy_outputs: dict[str, Any],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
    ) -> Any:
        """Pick the correct lazy source from pipeline outputs."""
        data_input_id = config.get("data_input")
        if data_input_id and data_input_id in lazy_outputs:
            source_lf = lazy_outputs[data_input_id]
        else:
            source_lf = lazy_outputs.get(node_id)

        if source_lf is None:
            error_msg = (
                "No data arrived at the optimiser node. "
                "Make sure an upstream data source is connected and producing data."
            )
            self._store.atomic_update(job_id, {"status": "error", "message": error_msg})
            raise HTTPException(status_code=400, detail=error_msg)

        return source_lf

    def _validate_and_project(
        self,
        source_lf: Any,
        config: dict[str, Any],
        job_id: str,
    ) -> tuple[list[str], Any]:
        """Validate columns and build the projection for the solver.

        Returns (constraint_cols, projected_lazy_frame).
        """
        import polars as pl

        objective = str(config["objective"])
        constraints = config["constraints"]
        qid_col = str(config.get("quote_id", "quote_id"))
        mult_col = str(config.get("scenario_value", "scenario_value"))
        step_col = str(config.get("scenario_index", "scenario_index"))

        schema = source_lf.collect_schema()
        available_cols = set(schema.names())
        required_cols = {objective, qid_col, mult_col, step_col}
        for cname in constraints:
            required_cols.add(cname)
        missing_cols = sorted(required_cols - available_cols)
        if missing_cols:
            avail = sorted(available_cols)
            detail = f"Missing columns in scored data: {missing_cols}. Available: {avail}"
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail)

        constraint_cols = list(constraints.keys()) if isinstance(constraints, dict) else []
        qid_dtype = schema[qid_col]
        if not (
            qid_dtype == pl.String or qid_dtype == pl.Categorical or isinstance(qid_dtype, pl.Enum)
        ):
            detail = (
                f"{qid_col} must be Utf8 (String), Categorical, or Enum, got {qid_dtype}. "
                "Numeric, binary, and other dtypes are not supported as quote_id columns."
            )
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail)

        null_count = int(
            source_lf.select(pl.col(qid_col).null_count().alias("n"))
            .collect(engine="streaming")
            .item()
        )
        if null_count > 0:
            detail = (
                f"{_NULL_QUOTE_ID_DETAIL_PREFIX} ({null_count} rows). "
                "Every row must have a non-null quote_id; check upstream filters and joins."
            )
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail)

        solver_cols = [qid_col, step_col, mult_col, objective] + [
            c for c in constraint_cols if c in available_cols
        ]
        cast_map: dict[str, pl.DataType] = {
            step_col: pl.Int32(),
            mult_col: pl.Float32(),
            objective: pl.Float32(),
        }
        for c in constraint_cols:
            cast_map[c] = pl.Float32()
        cast_exprs = [pl.col(c).cast(t) for c, t in cast_map.items()]
        if qid_dtype == pl.String:
            cast_exprs.append(pl.col(qid_col).cast(pl.Categorical))

        scored_lf = source_lf.select(solver_cols).with_columns(cast_exprs)
        return constraint_cols, scored_lf

    @staticmethod
    def _extract_factors(
        lazy_outputs: dict[str, Any],
        config: dict[str, Any],
        mode: str,
    ) -> Any:
        """Extract ratebook factors DataFrame (None for online mode)."""
        if mode != "ratebook":
            return None
        banding_source_id = config.get("banding_source")
        if banding_source_id and banding_source_id in lazy_outputs:
            return lazy_outputs[banding_source_id].collect(engine="streaming")
        return None

    def _build_grid(
        self,
        scored_lf: Any,
        constraint_cols: list[str],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
    ) -> QuoteGrid:
        """Sink scored data to parquet and build the QuoteGrid."""
        from price_contour import build_grid_from_parquet_chunked

        objective = config["objective"]
        qid_col = config.get("quote_id", "quote_id")
        mult_col = config.get("scenario_value", "scenario_value")
        step_col = config.get("scenario_index", "scenario_index")
        try:
            chunk_size = _chunk_size_from_config(config)
        except ValueError as exc:
            detail = f"Grid construction failed: {exc}"
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail) from exc

        from haute._polars_utils import safe_sink

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".parquet")
        os.close(tmp_fd)
        try:
            safe_sink(scored_lf, tmp_path)
            del scored_lf

            build_kwargs = {
                "quote_id": qid_col,
                "scenario_index": step_col,
                "scenario_value": mult_col,
                "objective": objective,
            }
            quote_grid = build_grid_from_parquet_chunked(
                tmp_path,
                constraint_cols,
                chunk_size,
                **build_kwargs,
            )
        except HTTPException:
            raise
        except Exception as exc:
            detail = f"Grid construction failed: {exc}"
            logger.error("grid_build_failed", error=str(exc), node_id=node_id, exc_info=True)
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail) from exc
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception as cleanup_exc:
                    logger.warning(
                        "optimiser_grid_temp_cleanup_failed",
                        path=tmp_path,
                        error=str(cleanup_exc),
                        exc_info=True,
                    )

        return quote_grid

    def _launch_background(
        self,
        job_id: str,
        node_id: str,
        config: dict[str, Any],
        mode: str,
        quote_grid: QuoteGrid,
        factors_df: Any,
    ) -> None:
        """Start the solver in a background thread."""
        start_time = time.monotonic()
        self._store.atomic_update(
            job_id,
            {
                "start_time": start_time,
                "timeout": config.get("timeout", _DEFAULT_TIMEOUT),
            },
        )

        def _solve_background() -> None:
            try:
                # P7: Use atomic_update instead of mutating the job dict
                # directly, so status-polling reads on the main thread
                # always see a consistent snapshot.
                progress_job = self._store.atomic_update(
                    job_id,
                    {
                        "message": "Solving",
                        "progress": 0.1,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                    expected_status="running",
                )
                if progress_job is None:
                    logger.info("solve_start_skipped", job_id=job_id, expected_status="running")
                    return
                if mode == "ratebook":
                    _solve_ratebook(
                        quote_grid,
                        config,
                        factors_df,
                        self._store,
                        job_id,
                        start_time,
                    )
                else:
                    _solve_online(
                        quote_grid,
                        config,
                        self._store,
                        job_id,
                        start_time,
                    )
            except Exception as exc:
                error_categories: dict[type, tuple[str, str]] = {
                    ValueError: ("Data error", "data"),
                    RuntimeError: ("Algorithm error", "algorithm"),
                }
                prefix, category = error_categories.get(
                    type(exc),
                    ("Unexpected error", "unexpected"),
                )
                error_msg = f"{prefix}: {exc}"
                logger.error(
                    "solve_failed",
                    error=str(exc),
                    node_id=node_id,
                    category=category,
                    exc_info=True,
                )
                error_job = self._store.atomic_update(
                    job_id,
                    {
                        "status": "error",
                        "message": error_msg,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                    expected_status="running",
                )
                if error_job is None:
                    logger.info("solve_error_update_skipped", job_id=job_id)

        thread = threading.Thread(target=_solve_background, daemon=True)
        try:
            thread.start()
        except Exception as exc:
            logger.error(
                "solve_worker_start_failed",
                error=str(exc),
                node_id=node_id,
                exc_info=True,
            )
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": f"Failed to start optimiser worker: {exc}",
                    "elapsed_seconds": time.monotonic() - start_time,
                },
            )
            raise HTTPException(
                status_code=500,
                detail="Optimiser worker failed to start. Check the server logs for details.",
            ) from exc
