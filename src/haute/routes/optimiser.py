"""Optimiser endpoints: solve, status, apply, save, frontier, mlflow log."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException

from haute._env import float_env
from haute._execution_admission import (
    create_admitted_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
)
from haute._file_ops import atomic_write_text
from haute._interactive_workers import (
    InteractiveWorkerCrashedError,
    InteractiveWorkerMemoryLimitError,
    InteractiveWorkerRemoteError,
    InteractiveWorkerStoppedError,
    InteractiveWorkerTimeoutError,
    interactive_worker_pool,
    resolve_interactive_execution_mode,
)
from haute._logging import get_logger
from haute._mlflow_utils import (
    allow_file_store_if_local,
    ensure_experiment,
    registry_uri_for_tracking,
)
from haute._ratebook_collar import (
    COMBINED_FACTOR_BOUNDS_KEY,
    CombinedFactorBoundsError,
    parse_combined_factor_bounds,
)
from haute._rating import is_rating_dtype_descriptor
from haute._sandbox import _get_project_root, contained_path
from haute._types import RatebookSolveResultLike, SolveResultLike
from haute._worker_isolation import resolve_worker_memory_enforcement
from haute.routes._frontier_point_summary import frontier_point_library_row
from haute.routes._job_lifecycle import require_job_status
from haute.routes._job_store import get_job_store
from haute.routes._mlflow_log_errors import mlflow_log_http_exception, require_mlflow_installed
from haute.routes._optimiser_artifacts import (
    _APPLY_RESULT_HANDLE_KEY,
    _load_apply_result_artifact,
)
from haute.routes._optimiser_frontier import (
    _CONSTRAINT_THRESHOLD_KEYS,
    OptimiserFrontierService,
    _artifact_handles_or_raise,
    _base_result_for_frontier,
    _dataframe_or_raise,
    _frontier_point_constraints_override,
    _frontier_point_mlflow_summary,
    _job_has_frontier_points,
    _reject_ratebook_apply_detail,
    _summary_solve_result,
)
from haute.routes._optimiser_input import (
    ESTIMATE_MAPPED_ERRORS,
    _estimate_quote_id_column_or_raise,  # noqa: F401 - estimate pre-flight, re-exported
    _find_optimiser_node,
    estimate_failure_http_exception,
)
from haute.routes._optimiser_limits import (
    limited_apply_preview_payload,
)
from haute.routes._optimiser_service import OptimiserSolveService, _with_flattened_optimiser_graph
from haute.routes._optimiser_solver import _job_elapsed_seconds
from haute.routes._optimiser_worker import OptimiserEstimateOutcome, optimiser_estimate_worker
from haute.routes.pipeline import (
    _interactive_affinity_key,
    _prepare_runtime_graph,
    _raise_interactive_remote_http_error,
    _raise_interactive_worker_crash_http_error,
)
from haute.schemas import (
    OptimiserApplyRequest,
    OptimiserApplyResponse,
    OptimiserEstimateRequest,
    OptimiserEstimateResponse,
    OptimiserFrontierAutoRangeRequest,
    OptimiserFrontierAutoRangeStartResponse,
    OptimiserFrontierAutoRangeStatusResponse,
    OptimiserFrontierRequest,
    OptimiserFrontierResponse,
    OptimiserFrontierSelectRequest,
    OptimiserFrontierSelectResponse,
    OptimiserFrontierStatusResponse,
    OptimiserMlflowLogRequest,
    OptimiserMlflowLogResponse,
    OptimiserSaveRequest,
    OptimiserSaveResponse,
    OptimiserSolveRequest,
    OptimiserSolveResponse,
    OptimiserStatusResponse,
)

logger = get_logger(component="server.optimiser")

router = APIRouter(prefix="/api/optimiser", tags=["optimiser"])

# In-memory job store — same pattern as modelling, acquired through
# the central factory so the "optimiser" prefix is a single source of
# truth for every importer.
_store = get_job_store("optimiser")
_solve_service = OptimiserSolveService(_store)
_frontier_service = OptimiserFrontierService(_store)


def _prepare_optimiser_execution_request(
    body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
) -> OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest:
    """Confine and flatten graph-bearing optimiser requests before execution."""
    graph = _prepare_runtime_graph(body.graph)
    if graph is body.graph:
        return body
    return body.model_copy(update={"graph": graph})


def _estimate_timeout() -> float:
    return float_env("HAUTE_OPTIMISER_ESTIMATE_TIMEOUT", 300.0)


def _optimiser_input_metrics(body: OptimiserEstimateRequest) -> dict[str, int | float | None]:
    """Return quote/scenario counts for the actual projected optimiser input.

    The route owns admission and the answer; the count itself
    (``OptimiserSolveService.estimate_input``: the pipeline, then ONE
    streaming aggregation scan) runs in the warm interactive worker pool in
    process mode, under that admission's memory caps, and in-process in the
    explicit ``thread`` compatibility mode. No frame is collected in the
    server process.
    """
    body = cast(OptimiserEstimateRequest, _with_flattened_optimiser_graph(body))
    node = _find_optimiser_node(body.graph, body.node_id)
    _solve_service._validate_config(node.data.config)
    execution_context = create_admitted_execution_context(
        operation="optimiser_estimate",
        profile=ExecutionProfile.OPTIMISER_SETUP,
    )
    try:
        if resolve_interactive_execution_mode() == "process":
            return _optimiser_input_metrics_in_worker(body, execution_context)
        return _solve_service.estimate_input(body, execution_context=execution_context)
    finally:
        execution_context.release_admission(preserve_primary_error=True)


def _optimiser_input_metrics_in_worker(
    body: OptimiserEstimateRequest,
    execution_context: ExecutionContext,
) -> dict[str, int | float | None]:
    """Run the estimate on the warm pool and answer its failures as preview does."""
    budget = isolated_execution_budget(execution_context)
    try:
        outcome = interactive_worker_pool().run(
            optimiser_estimate_worker,
            body,
            budget,
            affinity_key=_interactive_affinity_key(body.graph, body.source),
            timeout_seconds=_estimate_timeout(),
            absolute_rss_limit_bytes=budget.process_rss_limit_bytes,
            memory_growth_limit_bytes=budget.memory_limit_bytes,
            require_memory_limit=resolve_worker_memory_enforcement() == "required",
        )
    except InteractiveWorkerMemoryLimitError as exc:
        raise HTTPException(status_code=507, detail=exc.to_payload()) from None
    except InteractiveWorkerTimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Optimiser estimate timed out ({_estimate_timeout():.0f}s limit)",
        ) from None
    except InteractiveWorkerStoppedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except InteractiveWorkerCrashedError as exc:
        _raise_interactive_worker_crash_http_error(exc, operation="optimiser_estimate")
    except InteractiveWorkerRemoteError as exc:
        _raise_interactive_remote_http_error(exc, operation="optimiser_estimate")
    if not isinstance(outcome, OptimiserEstimateOutcome):
        raise RuntimeError(f"Optimiser estimate worker returned {type(outcome).__name__}")
    if outcome.failure is not None:
        status_code, detail = outcome.failure
        raise HTTPException(status_code=status_code, detail=detail)
    if outcome.metrics is None:
        raise RuntimeError("Optimiser estimate worker returned neither counts nor a failure")
    return outcome.metrics


def _frontier_select_response(result: dict[str, Any]) -> OptimiserFrontierSelectResponse:
    return OptimiserFrontierSelectResponse(
        status="ok",
        point_index=result.get("selected_frontier_point"),
        total_objective=result["total_objective"],
        constraints=result["constraints"],
        baseline_objective=result["baseline_objective"],
        baseline_constraints=result["baseline_constraints"],
        effective_bounds=result["effective_bounds"],
        lambdas=result["lambdas"],
        converged=result["converged"],
        iterations=result.get("iterations"),
        cd_iterations=result.get("cd_iterations"),
        factor_tables=result.get("factor_tables", {}),
        history=result.get("history"),
        ratebook_cd_trace=result.get("ratebook_cd_trace"),
        warning=result.get("warning"),
        scenario_value_stats=result.get("scenario_value_stats"),
        scenario_value_histogram=result.get("scenario_value_histogram"),
        clamp_rate=result.get("clamp_rate"),
        combined_factor_bounds=result.get(COMBINED_FACTOR_BOUNDS_KEY),
        frontier_generation=result["frontier_generation"],
        diagnostics_errors=result["diagnostics_errors"],
    )


def _clear_result_data_after_user_action(job_id: str) -> None:
    """Slim result data without ending an active frontier-analysis session."""

    job = _store.get_job(job_id)
    if job is not None and _job_has_frontier_points(job):
        _store.clear_result_data(job_id, keys=("solve_result",))
        return
    _store.clear_result_data(job_id)


@router.post("/solve", response_model=OptimiserSolveResponse)
def solve(body: OptimiserSolveRequest) -> OptimiserSolveResponse:
    """Start optimisation for an optimiser node.

    Executes the pipeline up to the optimiser node to materialise the
    scored DataFrame, then runs the solver in a background thread.
    """
    body = cast(OptimiserSolveRequest, _prepare_optimiser_execution_request(body))
    return _solve_service.start(body)


@router.post("/estimate", response_model=OptimiserEstimateResponse)
def estimate_solve(body: OptimiserEstimateRequest) -> OptimiserEstimateResponse:
    """Preview the data volume the solver will see for a given optimiser node.

    Cost: ``total_rows`` is cheap (ancestor parquet metadata, null when
    unavailable — e.g. live data without parquet backing).  The quote and
    scenario counts are exact and therefore NOT free: they execute the
    pipeline up to the optimiser's data input (reusing the optimiser-setup
    dataframe-execution cache when warm) followed by a single streaming
    aggregation scan over the projected solver columns.  The solver itself
    is never invoked.
    """
    from haute._ram_estimate import _detailed_ancestor_source_metadata

    body = cast(OptimiserEstimateRequest, _prepare_optimiser_execution_request(body))
    # The resolver answers an unknown source size with no row count; anything
    # it raises is a failure, not an unknown total.
    total_rows = _detailed_ancestor_source_metadata(
        body.graph,
        body.node_id,
        body.source,
    ).row_count

    try:
        metrics = _optimiser_input_metrics(body)
    except ESTIMATE_MAPPED_ERRORS as exc:
        raise estimate_failure_http_exception(exc, node_id=body.node_id) from None
    return OptimiserEstimateResponse(
        total_rows=total_rows,
        quote_count=cast(int | None, metrics.get("quote_count")),
        scenarios_per_quote_min=cast(int | None, metrics.get("scenarios_per_quote_min")),
        scenarios_per_quote_max=cast(int | None, metrics.get("scenarios_per_quote_max")),
        scenarios_per_quote_mean=cast(float | None, metrics.get("scenarios_per_quote_mean")),
        expanded_row_count=cast(int | None, metrics.get("expanded_row_count")),
    )


@router.post("/frontier/auto-range/start", response_model=OptimiserFrontierAutoRangeStartResponse)
def start_frontier_auto_range(
    body: OptimiserFrontierAutoRangeRequest,
) -> OptimiserFrontierAutoRangeStartResponse:
    """Start efficient-frontier auto-range estimation as a background job."""
    body = cast(
        OptimiserFrontierAutoRangeRequest,
        _prepare_optimiser_execution_request(body),
    )
    return _solve_service.start_frontier_auto_range(body)


@router.get(
    "/frontier/auto-range/status/{job_id}",
    response_model=OptimiserFrontierAutoRangeStatusResponse,
)
async def frontier_auto_range_status(job_id: str) -> OptimiserFrontierAutoRangeStatusResponse:
    """Poll efficient-frontier auto-range progress."""
    return _solve_service.frontier_auto_range_status(job_id)


@router.post(
    "/frontier/auto-range/cancel/{job_id}",
    response_model=OptimiserFrontierAutoRangeStatusResponse,
)
def cancel_frontier_auto_range(job_id: str) -> OptimiserFrontierAutoRangeStatusResponse:
    """Cancel efficient-frontier auto-range progress."""
    return _solve_service.cancel_frontier_auto_range(job_id)


_FINITE_VALIDATED_KEY = "_result_finite_validated_for"


def _finite_validated_job(job_id: str, job: Mapping[str, Any]) -> Mapping[str, Any]:
    """A completed job whose ``result`` and ``frontier_data`` hold no NaN or Infinity.

    Mirrors the modelling status route's finite-JSON check: a non-finite value
    corrects the job from ``completed`` to ``error``. A clean walk is cached as
    the ``(frontier_generation, selected_frontier_point)`` it was made for,
    since a recompute or a selection rewrites the result. The flag is private
    and never part of a response.
    """
    result = job.get("result")
    if job.get("status") != "completed" or result is None:
        return job
    validated_for = [result["frontier_generation"], job.get("selected_frontier_point")]
    if job.get(_FINITE_VALIDATED_KEY) == validated_for:
        return job
    bad_paths = _non_finite_paths({"result": result, "frontier": job.get("frontier_data")})
    if bad_paths:
        shown = ", ".join(bad_paths[:5])
        if len(bad_paths) > 5:
            shown += f", … ({len(bad_paths)} in total)"
        message = f"Optimiser result cannot be published: non-finite values at {shown}"
        logger.error("optimiser_result_not_json_finite", paths=bad_paths[:5], job_id=job_id)
        return _solve_service.reject_completed_result(job_id, message=message)
    # ``None`` means the job changed concurrently; the next poll walks it again.
    updated = _store.atomic_update(
        job_id,
        {_FINITE_VALIDATED_KEY: validated_for},
        expected_status="completed",
    )
    return updated if updated is not None else _store.require_job(job_id)


def _solve_status_response(
    job: Mapping[str, Any],
    *,
    elapsed_seconds: float,
) -> OptimiserStatusResponse:
    frontier_resp = None
    if job.get("status") == "completed":
        fd = job.get("frontier_data")
        if fd:
            frontier_resp = OptimiserFrontierResponse(**fd)
    return OptimiserStatusResponse(
        status=require_job_status(job),
        progress=job.get("progress", 0.0),
        message=job.get("message", ""),
        elapsed_seconds=elapsed_seconds,
        result=job.get("result"),
        frontier=frontier_resp,
        terminal_reason=job.get("terminal_reason"),
        execution_metrics=job.get("execution_metrics"),
    )


@router.get("/solve/status/{job_id}", response_model=OptimiserStatusResponse)
async def solve_status(job_id: str) -> OptimiserStatusResponse:
    """Poll optimisation job progress."""
    job: Mapping[str, Any] = _store.require_job(job_id)

    # Explicit per-job timeouts remain opt-in, but long local solves should
    # otherwise keep running until they complete or the user cancels them.
    if job.get("status") == "running":
        start = job.get("start_time")
        timeout = job.get("timeout")
        if (
            start
            and isinstance(timeout, int | float)
            and not isinstance(timeout, bool)
            and timeout > 0
            and (time.monotonic() - start) > timeout
        ):
            # P7: Atomic update — only if still running (avoids overwriting a
            # completed result with a timeout error).
            job = _solve_service.timeout_solve(job_id, timeout=timeout, start_time=start)

    job = _finite_validated_job(job_id, job)
    elapsed_seconds = job.get("elapsed_seconds", 0.0)
    if job.get("status") == "running":
        elapsed_seconds = _job_elapsed_seconds(job, elapsed_seconds)
    return _solve_status_response(job, elapsed_seconds=elapsed_seconds)


@router.post("/solve/cancel/{job_id}", response_model=OptimiserStatusResponse)
async def cancel_solve(job_id: str) -> OptimiserStatusResponse:
    """Cancel an in-progress optimisation solve."""
    job = _finite_validated_job(job_id, _solve_service.cancel_solve(job_id))
    return _solve_status_response(job, elapsed_seconds=job.get("elapsed_seconds", 0.0))


@router.post("/apply", response_model=OptimiserApplyResponse)
def apply_lambdas(body: OptimiserApplyRequest) -> OptimiserApplyResponse:
    """Apply solved lambdas and return the per-quote detail preview.

    Online mode only: the real ``RatebookResult`` carries factor tables,
    not per-quote scenario selections, so ratebook jobs are rejected with
    an explicit 422 contract error before any solver or artifact work.
    """
    logger.info("apply_requested", job_id=body.job_id)
    job: Mapping[str, Any] = _store.require_completed_job(body.job_id)
    _reject_ratebook_apply_detail(job)

    # ``point_index`` names the target: a frontier point, or (``None``) the
    # job's own solve — never the server-side selected point.
    if body.point_index is not None:
        df, result, from_artifact = _frontier_service.materialise_point_apply(
            body.job_id,
            body.point_index,
        )
        response = OptimiserApplyResponse(
            status="ok",
            total_objective=result["total_objective"],
            constraints=result["constraints"],
            from_artifact=from_artifact,
            **limited_apply_preview_payload(df),
        )
        # Keep the solver and quote grid so the other points stay inspectable.
        _clear_result_data_after_user_action(body.job_id)
        return response

    solve_result = job.get("solve_result")
    from_artifact = False
    if solve_result is not None and not hasattr(solve_result, "dataframe"):
        _dataframe_or_raise(solve_result, context="Job solve_result")
    if solve_result is not None and getattr(solve_result, "dataframe", None) is not None:
        typed_solve_result = cast(SolveResultLike, solve_result)
        df = _dataframe_or_raise(solve_result, context="Job solve_result")
        total_objective = typed_solve_result.total_objective
        constraints = typed_solve_result.total_constraints
    else:
        from_artifact = True
        artifact_handles = _artifact_handles_or_raise(job)
        apply_handle = artifact_handles.get(_APPLY_RESULT_HANDLE_KEY)
        if not isinstance(apply_handle, dict):
            raise HTTPException(
                status_code=400,
                detail="Job has no apply artifact handle. Re-run the solve to regenerate it.",
            )
        df = _load_apply_result_artifact(apply_handle)
        job_result = _base_result_for_frontier(job)
        raw_total_objective = job_result.get("total_objective")
        raw_constraints = job_result.get("constraints", {})
        if not isinstance(raw_total_objective, (int, float)) or not isinstance(
            raw_constraints,
            dict,
        ):
            raise HTTPException(status_code=500, detail="Job summary is incomplete")
        total_objective = float(raw_total_objective)
        constraints = cast(dict[str, float], raw_constraints)

    response = OptimiserApplyResponse(
        status="ok",
        total_objective=total_objective,
        constraints=constraints,
        from_artifact=from_artifact,
        **limited_apply_preview_payload(df),
    )
    _clear_result_data_after_user_action(body.job_id)
    return response


def _frontier_status_response(job: Mapping[str, Any]) -> OptimiserFrontierStatusResponse:
    stored_status = require_job_status(job)
    result = None
    if stored_status == "completed" and job.get("result") is not None:
        result = OptimiserFrontierResponse.model_validate(job["result"])
    elapsed_seconds = job.get("elapsed_seconds", 0.0)
    if stored_status == "running":
        elapsed_seconds = _job_elapsed_seconds(job, elapsed_seconds)
    return OptimiserFrontierStatusResponse(
        status=stored_status,
        progress=job.get("progress", 0.0),
        message=job.get("message", ""),
        elapsed_seconds=elapsed_seconds,
        result=result,
        terminal_reason=job.get("terminal_reason"),
        error_code=job.get("error_code"),
        http_status_code=job.get("http_status_code"),
        error_detail=job.get("error_detail"),
        execution_metrics=job.get("execution_metrics"),
    )


@router.post("/frontier", response_model=OptimiserFrontierResponse)
def run_frontier(body: OptimiserFrontierRequest) -> OptimiserFrontierResponse:
    """Start an efficient-frontier sweep for a completed optimisation job.

    Validation (job/runtime availability, range resolution, the compute-budget
    cap) stays synchronous so contract errors surface as 4xx on this request.
    The sweep itself runs on a
    background thread; the response carries a pollable job handle for
    ``GET /frontier/status/{job_id}``.
    """
    frontier_job_id = _frontier_service.start_sweep(body)
    return OptimiserFrontierResponse(status="started", job_id=frontier_job_id)


@router.get("/frontier/status/{job_id}", response_model=OptimiserFrontierStatusResponse)
def frontier_status(job_id: str) -> OptimiserFrontierStatusResponse:
    """Return status for a background frontier sweep job."""
    return _frontier_status_response(_frontier_service.sweep_status(job_id))


@router.post(
    "/frontier/cancel/{job_id}",
    response_model=OptimiserFrontierStatusResponse,
)
def cancel_frontier(job_id: str) -> OptimiserFrontierStatusResponse:
    """Cancel a background frontier sweep without allowing late publication."""
    return _frontier_status_response(_frontier_service.cancel_sweep(job_id))


@router.post("/frontier/select", response_model=OptimiserFrontierSelectResponse)
def select_frontier_point(body: OptimiserFrontierSelectRequest) -> OptimiserFrontierSelectResponse:
    """Select a frontier summary point without re-solving the optimiser."""
    return _frontier_select_response(_frontier_service.select_point(body))


def _input_summary(solve_summary: Mapping[str, Any]) -> dict[str, Any]:
    """The artifact's ``input_summary``: the solve result's own provenance plus its grid shape.

    Read from the result's ``input_summary`` (built once when the solve
    completed), never rebuilt from the job; nothing is read or hashed.
    """
    provenance = {
        key: value
        for key, value in solve_summary["input_summary"].items()
        if key != "solver_settings"
    }
    return {
        "n_quotes": solve_summary.get("n_quotes"),
        "n_steps": solve_summary.get("n_steps"),
        **provenance,
    }


def _build_artifact_payload(
    job: Mapping[str, Any],
    solve_result: SolveResultLike,
    version_override: str = "",
    *,
    point_index: int | None = None,
    stale_at_publish: bool = False,
) -> dict[str, Any]:
    """Build the JSON payload for an optimiser artifact.

    Shared by both file-save and MLflow-log paths to avoid duplication.
    *solve_result* is the published target: the anchor solve when
    *point_index* is ``None``, otherwise that frontier point.
    """
    from datetime import datetime, timezone

    node_label = job.get("node_label", "optimiser")
    label_slug = node_label.lower().replace(" ", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")  # noqa: UP017
    auto_version = f"{label_slug}_{ts}"
    job_config = job.get("config", {})

    payload: dict[str, Any] = {
        "version": version_override or auto_version,
        "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "mode": job_config.get("mode", "online"),
        "lambdas": solve_result.lambdas,
        "total_objective": solve_result.total_objective,
        "baseline_objective": solve_result.baseline_objective,
        "total_constraints": solve_result.total_constraints,
        "baseline_constraints": solve_result.baseline_constraints,
        "constraints": job_config.get("constraints"),
        "objective": job_config.get("objective"),
        "quote_id": job_config.get("quote_id", "quote_id"),
        "scenario_index": job_config.get("scenario_index", "scenario_index"),
        "scenario_value": job_config.get("scenario_value", "scenario_value"),
        "converged": solve_result.converged,
        "iterations": getattr(solve_result, "iterations", None),
        "cd_iterations": getattr(solve_result, "cd_iterations", None),
    }
    if "chunk_size" in job_config:
        payload["chunk_size"] = job_config["chunk_size"]
    setup_chunking = job.get("setup_chunking")
    if isinstance(setup_chunking, dict):
        payload["setup_chunking"] = setup_chunking
    # Frontier provenance — only a frontier-point target records one.
    frontier_data = job.get("frontier_data")
    if point_index is not None and frontier_data:
        payload["frontier_selection"] = {
            "selected_from_frontier": True,
            "point_index": point_index,
            "n_frontier_points": frontier_data.get("n_points", 0),
        }
    if job_config.get("mode") == "ratebook":
        # The target's own tables first: ``job["result"]`` may hold another
        # (selected) point's.
        result_payload = job.get("result")
        factor_tables = getattr(solve_result, "factor_tables", None)
        if factor_tables is None and isinstance(result_payload, dict):
            factor_tables = result_payload.get("factor_tables")
        factor_dtypes = getattr(solve_result, "factor_dtypes", None)
        if factor_dtypes is None and isinstance(result_payload, dict):
            factor_dtypes = result_payload.get("factor_dtypes")
        if factor_dtypes is None:
            factor_dtypes = job.get("factor_dtypes")
        ratebook_result = cast(RatebookSolveResultLike, solve_result)
        payload["factor_tables"] = factor_tables
        payload["factor_dtypes"] = factor_dtypes
        payload["clamp_rate"] = ratebook_result.clamp_rate
        # The scenario range the solve scored; every apply clamps to it (Q17).
        payload[COMBINED_FACTOR_BOUNDS_KEY] = getattr(solve_result, COMBINED_FACTOR_BOUNDS_KEY)
    # Audit trail. ``constraints`` above stays the configured specs, which
    # OPTIMISER_APPLY reads; ``effective_constraints`` records the thresholds
    # in force for this target.
    solve_summary = _base_result_for_frontier(job)
    payload["solver_settings"] = solve_summary["input_summary"]["solver_settings"]
    payload["effective_constraints"] = (
        _frontier_point_constraints_override(job, point_index)
        if point_index is not None
        else job_config.get("constraints")
    )
    payload["input_summary"] = _input_summary(solve_summary)
    payload["stale_at_publish"] = stale_at_publish
    return payload


def _non_finite_paths(value: Any, path: str = "") -> list[str]:
    """Return JSON paths of every non-finite float nested inside *value*."""
    if isinstance(value, float) and not math.isfinite(value):
        return [path or "<root>"]
    if isinstance(value, dict):
        return [
            found
            for key, item in value.items()
            for found in _non_finite_paths(item, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(value, (list, tuple)):
        return [
            found
            for index, item in enumerate(value)
            for found in _non_finite_paths(item, f"{path}[{index}]")
        ]
    return []


def _validate_artifact_payload(payload: dict[str, Any]) -> None:
    """Gate the artifact payload before it is written to disk.

    The saved artifact is exactly what OPTIMISER_APPLY reads to price, so a
    payload with NaN/Infinity values or a missing ratebook factor-table
    section would mis-price silently downstream. Reject with an actionable
    4xx instead of persisting a poisoned artifact — and because this runs
    before the write and before the post-save result slimming, the in-memory
    solve result survives for the user to inspect.
    """
    lambdas = payload.get("lambdas")
    if not isinstance(lambdas, dict):
        raise HTTPException(
            status_code=400,
            detail="Solve result has no lambda mapping. Re-run the solve before saving.",
        )
    if payload.get("total_objective") is None:
        raise HTTPException(
            status_code=400,
            detail="Solve result has no total objective. Re-run the solve before saving.",
        )
    if payload.get("mode") == "ratebook":
        factor_tables = payload.get("factor_tables")
        if not isinstance(factor_tables, dict):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Ratebook solve result has no factor tables, so the saved artifact "
                    "could not be applied for pricing. Re-run the solve before saving."
                ),
            )
        factor_dtypes = payload.get("factor_dtypes")
        if not isinstance(factor_dtypes, dict):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Ratebook solve result has no factor dtype metadata, so the saved "
                    "artifact could not be applied safely. Re-run the solve before saving."
                ),
            )
        if list(factor_dtypes) != list(factor_tables):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Ratebook factor_dtypes tables do not match factor_tables in "
                    "the same order. Re-run the solve before saving."
                ),
            )
        for table_name, records in factor_dtypes.items():
            if not isinstance(records, list) or not records:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Ratebook factor_dtypes for {table_name!r} must be a "
                        "non-empty ordered list."
                    ),
                )
            seen_columns: set[str] = set()
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Ratebook factor_dtypes for {table_name!r} has a "
                            f"malformed record at index {index}."
                        ),
                    )
                column = record.get("column")
                descriptor = record.get("dtype")
                if (
                    set(record) != {"column", "dtype"}
                    or not isinstance(column, str)
                    or not column
                    or column in seen_columns
                    or not is_rating_dtype_descriptor(descriptor)
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Ratebook factor_dtypes for {table_name!r} has a "
                            f"malformed record at index {index}."
                        ),
                    )
                seen_columns.add(column)
        clamp_rate = payload.get("clamp_rate")
        if (
            isinstance(clamp_rate, bool)
            or not isinstance(clamp_rate, (int, float))
            or not math.isfinite(clamp_rate)
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Ratebook solve result has no finite clamp_rate (got {clamp_rate!r}). "
                    "Re-run the solve before saving."
                ),
            )
        try:
            parse_combined_factor_bounds(payload.get(COMBINED_FACTOR_BOUNDS_KEY))
        except CombinedFactorBoundsError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Ratebook solve result has no valid combined-factor collar: {exc}. "
                    "Re-run the solve before saving."
                ),
            ) from exc
    bad_paths = _non_finite_paths(payload)
    if bad_paths:
        shown = ", ".join(bad_paths[:5])
        if len(bad_paths) > 5:
            shown += f", … ({len(bad_paths)} in total)"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Solve result contains non-finite values ({shown}); the artifact was "
                "not saved. Check the solve inputs and constraints, then re-run the "
                "solve — a pricing artifact must not contain NaN or Infinity."
            ),
        )


def _publish_target(
    job_id: str,
    job: Mapping[str, Any],
    point_index: int | None,
) -> tuple[Mapping[str, Any], SolveResultLike, dict[str, Any] | None]:
    """Resolve an explicit publish target without touching heavy job state.

    ``None`` is the job's own solve, published from its lightweight summary;
    a number is that frontier point (whose selected-result dict is returned).
    """
    if point_index is not None:
        job, selected_result, solve_result = _frontier_service.solve_result_for_selected_point(
            job_id,
            job,
            point_index,
        )
        return job, solve_result, selected_result
    return job, _summary_solve_result(_base_result_for_frontier(job)), None


_save_destination_locks_guard = threading.Lock()
_save_destination_locks: dict[str, threading.Lock] = {}


@contextmanager
def _save_destination_lock(destination: Path) -> Iterator[None]:
    """Serialise saves to one file, so two cannot both pass ``overwrite=False``."""
    key = os.path.normcase(os.path.abspath(destination))
    with _save_destination_locks_guard:
        lock = _save_destination_locks.setdefault(key, threading.Lock())
    with lock:
        yield


@router.post("/save", response_model=OptimiserSaveResponse)
def save_result(body: OptimiserSaveRequest) -> OptimiserSaveResponse:
    """Save the optimisation result to a project file.

    Publishing reads only the job's lightweight summaries, so it never needs
    or releases the solve's heavy runtime state.
    """
    job: Mapping[str, Any] = _store.require_completed_job(body.job_id)
    job, solve_result, _selected_result = _publish_target(body.job_id, job, body.point_index)

    # Relative and absolute paths both resolve inside the project root, the
    # base the pipeline's own file reads use.
    project_root = _get_project_root().resolve()
    out = contained_path(project_root, body.output_path)
    apply_path = Path(os.path.relpath(out, project_root)).as_posix()

    payload = _build_artifact_payload(
        job,
        solve_result,
        version_override=body.version,
        point_index=body.point_index,
        stale_at_publish=body.stale,
    )
    _validate_artifact_payload(payload)
    try:
        with _save_destination_lock(out):
            if not body.overwrite and out.exists():
                # A structured detail: the save pane dispatches on the code.
                result_exists = {
                    "error_code": "optimiser_result_exists",
                    "message": f"Optimiser result already exists: {apply_path}",
                }
                raise HTTPException(status_code=409, detail=result_exists)
            out.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write (same posture as the schema-mapping save in
            # `_helpers.save_sidecar`): stage to a sibling temp then rename, so a
            # crash or disk error mid-write can never tear the previous artifact
            # — this file is what OPTIMISER_APPLY prices from. `allow_nan=False`
            # is a backstop behind `_validate_artifact_payload`: nothing
            # non-finite may ever reach disk.
            atomic_write_text(out, json.dumps(payload, indent=2, default=str, allow_nan=False))
    except OSError as exc:
        logger.error("save_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Filesystem error saving optimiser result. Check the server logs for details.",
        ) from None
    logger.info("result_saved", path=str(out), job_id=body.job_id)
    return OptimiserSaveResponse(
        status="ok",
        path=str(out),
        apply_path=apply_path,
        message=f"Saved optimisation result to {apply_path}",
    )


@router.post("/mlflow/log", response_model=OptimiserMlflowLogResponse)
def mlflow_log(body: OptimiserMlflowLogRequest) -> OptimiserMlflowLogResponse:
    """Log optimisation results to MLflow.

    Like save, this reads only lightweight summaries: the anchor's MLflow
    summary was computed once when the solve completed.
    """
    require_mlflow_installed()
    job: Mapping[str, Any] = _store.require_completed_job(body.job_id)
    job, solve_result, selected_result = _publish_target(body.job_id, job, body.point_index)
    summary: dict[str, Any]
    if body.point_index is None:
        publish_summary = job.get("publish_summary")
        if not isinstance(publish_summary, dict):
            raise HTTPException(
                status_code=400,
                detail=(
                    "The MLflow summary for this solve is not available. "
                    "Re-run the solve to log results to MLflow."
                ),
            )
        summary = publish_summary
    else:
        summary = _frontier_point_mlflow_summary(
            job,
            cast(dict[str, Any], selected_result),
            body.point_index,
        )

    try:
        return _log_optimiser_run(
            body,
            job,
            solve_result=solve_result,
            summary=summary,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise mlflow_log_http_exception(exc, job_id=body.job_id) from None


def _mlflow_param_value(value: Any) -> str:
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _log_optimiser_run(
    body: OptimiserMlflowLogRequest,
    job: Mapping[str, Any],
    *,
    solve_result: SolveResultLike,
    summary: dict[str, Any],
) -> OptimiserMlflowLogResponse:
    """Log one optimiser run through a client bound to the resolved destination.

    The run logs no model, so no call touches MLflow's process-global tracking
    state: logs to different destinations proceed concurrently, and the
    tracking URI never reaches the environment.
    """
    import csv
    import io
    import tempfile
    import time

    from mlflow.entities import Metric, Param
    from mlflow.tracking import MlflowClient

    from haute.modelling._mlflow_log import (
        build_run_url,
        resolve_experiment_name,
        resolve_tracking_backend,
    )

    tracking_uri, backend = resolve_tracking_backend(body.destination)
    allow_file_store_if_local(tracking_uri, backend)
    client = MlflowClient(
        tracking_uri=tracking_uri, registry_uri=registry_uri_for_tracking(tracking_uri)
    )

    node_label = job.get("node_label", "optimiser")
    # The complete artifact OPTIMISER_APPLY reads, built before the run exists.
    complete_payload = _build_artifact_payload(
        job,
        solve_result,
        point_index=body.point_index,
        stale_at_publish=body.stale,
    )
    params = {key: str(value) for key, value in summary["params"].items()}
    for name, value in (complete_payload.get("solver_settings") or {}).items():
        params[f"solver_settings.{name}"] = _mlflow_param_value(value)

    # The request carries the node's current setting; the job's solve-time
    # config snapshot is never a fallback.
    experiment_name = resolve_experiment_name(
        explicit=body.experiment_name,
        node_label=node_label,
        backend=backend,
    )
    experiment_id = ensure_experiment(client, tracking_uri, experiment_name)
    run_id = client.create_run(experiment_id, run_name=node_label).info.run_id
    status = "FAILED"
    try:
        timestamp = int(time.time() * 1000)
        client.log_batch(
            run_id,
            metrics=[Metric(key, value, timestamp, 0) for key, value in summary["metrics"].items()],
            params=[Param(key, value) for key, value in params.items()],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            for name, data in summary.get("artifacts", {}).items():
                if data is None:
                    continue
                artifact_path = Path(tmpdir) / f"{name}.json"
                artifact_path.write_text(json.dumps(data, indent=2, default=str))
                client.log_artifact(run_id, str(artifact_path))

            complete_path = Path(tmpdir) / "optimiser_result.json"
            complete_path.write_text(json.dumps(complete_payload, indent=2, default=str))
            client.log_artifact(run_id, str(complete_path))

            # The frontier CSV and its provenance tags.
            frontier_data = job.get("frontier_data")
            if frontier_data and frontier_data.get("points"):
                # price-contour's points table: each typed point written back
                # as the library's flat row, in its schema's column order.
                constraint_names = frontier_data["constraint_names"]
                rows = [
                    frontier_point_library_row(point, constraint_names)
                    for point in frontier_data["points"]
                ]
                buffer = io.StringIO()
                writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
                frontier_path = Path(tmpdir) / "frontier.csv"
                frontier_path.write_text(buffer.getvalue())
                client.log_artifact(run_id, str(frontier_path))
                client.set_tag(run_id, "frontier.n_points", str(frontier_data["n_points"]))

            client.set_tag(run_id, "stale_at_publish", str(body.stale).lower())
            if body.point_index is not None:
                client.set_tag(run_id, "frontier.selected_point_index", str(body.point_index))
                effective_constraints = complete_payload.get("effective_constraints") or {}
                for name, spec in effective_constraints.items():
                    for key in _CONSTRAINT_THRESHOLD_KEYS:
                        if key in spec:
                            client.set_tag(
                                run_id,
                                f"effective_constraint.{name}.{key}",
                                str(spec[key]),
                            )
        status = "FINISHED"
    finally:
        client.set_terminated(run_id, status)

    return OptimiserMlflowLogResponse(
        status="ok",
        backend=backend,
        experiment_name=experiment_name,
        run_id=run_id,
        run_url=build_run_url(backend, experiment_name, run_id, tracking_uri=tracking_uri),
        tracking_uri=tracking_uri,
    )
