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
from haute.routes._job_store import JobStore
from haute.schemas import OptimiserSolveRequest, OptimiserSolveResponse

logger = get_logger(component="server.optimiser.solve")

# ── Default constants ─────────────────────────────────────────────
_DEFAULT_TIMEOUT = int(os.environ.get("HAUTE_SOLVER_TIMEOUT", "300"))
_HISTOGRAM_BINS = 20  # bin count for scenario-value distribution histogram
_DEFAULT_MAX_ITER = 50  # max solver iterations (online & ratebook)
_DEFAULT_CHUNK_SIZE = 500_000  # rows per chunk for solver processing
_DEFAULT_TOLERANCE = 1e-6  # convergence tolerance for solver
_DEFAULT_MAX_CD_ITERATIONS = 10  # max coordinate-descent iterations (ratebook)
_DEFAULT_CD_TOLERANCE = 1e-3  # coordinate-descent convergence tolerance (ratebook)


def _find_optimiser_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find and validate an optimiser node in the graph."""
    return find_typed_node(graph, node_id, NodeType.OPTIMISER, "optimiser")


def _compute_scenario_value_stats(
    solve_result: SolveResultLike,
) -> tuple[dict[str, float], dict[str, list[int] | list[float]]]:
    """Compute scenario value distribution statistics and histogram from solve result."""
    if not hasattr(solve_result, "dataframe"):
        return {}, {}
    df = solve_result.dataframe
    if "optimal_scenario_value" not in df.columns:
        return {}, {}

    col = df["optimal_scenario_value"]
    n = len(col)
    if n == 0:
        return {"n": 0}, {}
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

    # ── Compute efficient frontier (online mode only, non-fatal) ────
    frontier_data = None
    # M6: Use direct dict access to avoid _evict_stale() from background thread
    job_snapshot = store.jobs.get(job_id, {})
    config = job_snapshot.get("config", {})
    constraints = config.get("constraints")
    if mode == "online" and constraints:
        try:
            frontier_min = config.get("frontier_min", 0.80)
            frontier_max = config.get("frontier_max", 1.10)
            frontier_steps = config.get("frontier_steps", 15)

            baseline_constraints = solve_result.baseline_constraints
            ranges: dict[str, tuple[float, float]] = {}
            for cname in constraints:
                baseline = baseline_constraints.get(cname)
                if baseline is not None and baseline != 0:
                    ranges[cname] = (baseline * frontier_min, baseline * frontier_max)
            if ranges:
                frontier_result = solver.frontier(
                    quote_grid,
                    threshold_ranges=ranges,
                    n_points_per_dim=frontier_steps,
                )
                frontier_data = {
                    "status": "ok",
                    "points": frontier_result.points.to_dicts(),
                    "n_points": len(frontier_result.points),
                    "constraint_names": list(ranges.keys()),
                }
                logger.info(
                    "frontier_computed",
                    n_points=frontier_data["n_points"],
                    job_id=job_id,
                )
        except Exception as exc:
            logger.warning("frontier_computation_failed", error=str(exc), job_id=job_id)

    result_dict["frontier"] = frontier_data

    # P7: Atomic update — replace the entire dict to avoid races with
    # status-polling reads on the main thread.
    # L5: result_dict["frontier"] is the frontend-serialised frontier payload
    # (consumed by OptimiserStatusResponse).  "frontier_data" is a top-level
    # job key used by internal endpoints (e.g. /frontier/select) to look up
    # raw frontier points without going through the result dict.
    store.atomic_update(
        job_id,
        {
            "status": "completed",
            "progress": 1.0,
            "message": "Completed",
            "elapsed_seconds": elapsed,
            "solver": solver,
            "solve_result": solve_result,
            "quote_grid": quote_grid,
            "result": result_dict,
            "frontier_data": frontier_data,
        },
    )

    # Release heavy objects now that the result summary has been stored.
    # The job retains lightweight metadata (total_objective, lambdas,
    # constraints, frontier, etc.) but drops the solver engine and raw
    # data grid to avoid holding ~100s of MB for the 24h TTL window.
    store.clear_result_data(job_id, keys=("solver", "quote_grid"))


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
        chunk_size=config.get("chunk_size", _DEFAULT_CHUNK_SIZE),
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

    solver = RatebookOptimiser(
        objective=config["objective"],
        constraints=constraints,
        factor_columns=factor_columns_valid,
        max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
        max_cd_iterations=config.get("max_cd_iterations", _DEFAULT_MAX_CD_ITERATIONS),
        cd_tolerance=config.get("cd_tolerance", _DEFAULT_CD_TOLERANCE),
        tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
        chunk_size=config.get("chunk_size", _DEFAULT_CHUNK_SIZE),
    )

    factor_cols_flat = [c for group in factor_columns_valid for c in group]
    avail = factors_df.columns
    if qid_col in avail:
        keep = [qid_col] + [c for c in factor_cols_flat if c in avail]
        factors_df = factors_df.select(keep)
        if qid_col != "quote_id":
            factors_df = factors_df.rename({qid_col: "quote_id"})
    elif "quote_id" in avail:
        keep = ["quote_id"] + [c for c in factor_cols_flat if c in avail]
        factors_df = factors_df.select(keep)

    factors_df = factors_df.with_columns(pl.col("quote_id").cast(pl.Utf8))
    factors_df = factors_df.unique(subset=["quote_id"])

    quote_order = pl.DataFrame({"quote_id": quote_grid.quote_ids})
    quote_order = quote_order.unique(maintain_order=True)
    factors_df = quote_order.join(factors_df, on="quote_id", how="left")
    factors_df = factors_df.drop("quote_id")

    solve_result: RatebookSolveResultLike = solver.solve(quote_grid, factors_df)
    elapsed = time.monotonic() - start_time
    converged = solve_result.converged
    logger.info("solve_completed", mode="ratebook", elapsed=f"{elapsed:.2f}s", converged=converged)

    factor_tables_serialised = {}
    for name, table in solve_result.factor_tables.items():
        factor_tables_serialised[name] = [
            {"__factor_group__": level, "optimal_scenario_value": sv} for level, sv in table.items()
        ]

    _finalize_solve_result(
        solve_result,
        mode="ratebook",
        solver=solver,
        quote_grid=quote_grid,
        store=store,
        job_id=job_id,
        elapsed=elapsed,
        extra_fields={
            "cd_iterations": solve_result.cd_iterations,
            "factor_tables": factor_tables_serialised,
            "clamp_rate": getattr(solve_result, "clamp_rate", None),
            "history": None,
        },
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

        job_id = self._store.create_job(
            {
                "status": "running",
                "progress": 0.0,
                "message": "Starting",
                "config": dict(config),
                "node_label": node.data.label,
            }
        )
        logger.info("solve_started", node_id=body.node_id, mode=mode, job_id=job_id)

        checkpoint_dir = Path(tempfile.mkdtemp(prefix="haute_opt_"))
        try:
            lazy_outputs = self._execute_pipeline(body, job_id, checkpoint_dir)
            source_lf = self._resolve_data_source(lazy_outputs, config, body.node_id, job_id)
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
        finally:
            shutil.rmtree(checkpoint_dir, ignore_errors=True)
        self._launch_background(job_id, body.node_id, config, mode, quote_grid, factors_df)
        return OptimiserSolveResponse(status="started", job_id=job_id)

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

    def _execute_pipeline(
        self,
        body: OptimiserSolveRequest,
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
                lazy_outputs, *_ = _execute_lazy(
                    body.graph,
                    _build_node_fn,
                    target_node_id=body.node_id,
                    preamble_ns=preamble_ns,
                    source=scenario,
                    checkpoint_dir=checkpoint_dir,
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
            logger.error("pipeline_exec_failed", error=str(exc), node_id=body.node_id)
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

        available_cols = set(source_lf.collect_schema().names())
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

        solver_cols = [qid_col, step_col, mult_col, objective] + [
            c for c in constraint_cols if c in available_cols
        ]
        cast_map: dict[str, pl.DataType] = {
            qid_col: pl.Utf8(),
            step_col: pl.Int32(),
            mult_col: pl.Float32(),
            objective: pl.Float32(),
        }
        for c in constraint_cols:
            cast_map[c] = pl.Float32()
        cast_exprs = [pl.col(c).cast(t) for c, t in cast_map.items()]

        scored_lf = (
            source_lf.select(solver_cols)
            .with_columns(cast_exprs)
            .filter(pl.col(qid_col).is_not_null())
        )
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
        from price_contour import build_grid_from_parquet

        objective = config["objective"]
        qid_col = config.get("quote_id", "quote_id")
        mult_col = config.get("scenario_value", "scenario_value")
        step_col = config.get("scenario_index", "scenario_index")

        from haute._polars_utils import safe_sink

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".parquet")
        os.close(tmp_fd)
        try:
            safe_sink(scored_lf, tmp_path)
            del scored_lf

            quote_grid = build_grid_from_parquet(
                tmp_path,
                constraint_cols,
                quote_id=qid_col,
                scenario_index=step_col,
                scenario_value_col=mult_col,
                objective=objective,
            )
        except HTTPException:
            raise
        except Exception as exc:
            detail = f"Grid construction failed: {exc}"
            logger.error("grid_build_failed", error=str(exc), node_id=node_id)
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail) from exc
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

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
                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Solving",
                        "progress": 0.1,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                )
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
                logger.error("solve_failed", error=str(exc), node_id=node_id, category=category)
                self._store.atomic_update(
                    job_id,
                    {
                        "status": "error",
                        "message": error_msg,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                )

        thread = threading.Thread(target=_solve_background, daemon=True)
        thread.start()
