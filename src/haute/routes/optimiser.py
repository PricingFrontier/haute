"""Optimiser endpoints: solve, status, apply, save, frontier, mlflow log."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from haute._logging import get_logger
from haute._sandbox import _get_project_root
from haute._types import SolveResultLike
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, validate_safe_path
from haute.routes._job_store import get_job_store
from haute.routes._optimiser_limits import (
    limited_apply_preview_payload,
    limited_frontier_payload,
)
from haute.routes._optimiser_service import (
    _APPLY_RESULT_HANDLE_KEY,
    _DEFAULT_CHUNK_SIZE,
    _DEFAULT_TIMEOUT,
    OptimiserSolveService,
    _cleanup_apply_result_artifact,
    _compute_scenario_value_stats,
    _load_apply_result_artifact,
    _persist_apply_result_artifact,
)
from haute.schemas import (
    OptimiserApplyRequest,
    OptimiserApplyResponse,
    OptimiserEstimateRequest,
    OptimiserEstimateResponse,
    OptimiserFrontierRequest,
    OptimiserFrontierResponse,
    OptimiserFrontierSelectRequest,
    OptimiserFrontierSelectResponse,
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


def _touch_heavy_objects_or_raise(
    job_id: str,
    *,
    required_keys: tuple[str, ...],
    detail: str,
) -> None:
    """Reserve completed-job heavy runtime state before using it."""
    if not _store.touch_heavy_objects(job_id, required_keys=required_keys):
        raise HTTPException(status_code=400, detail=detail)


def _cleanup_orphan_apply_artifact(handle: dict[str, Any], *, job_id: str) -> None:
    """Clean up a request-created apply artifact without masking the main failure."""
    try:
        _cleanup_apply_result_artifact(handle)
    except Exception as cleanup_exc:
        raw_path = handle.get("directory") or handle.get("path") or "<unknown>"
        logger.warning(
            "frontier_select_orphan_apply_artifact_cleanup_failed",
            job_id=job_id,
            path=str(raw_path),
            error=str(cleanup_exc),
            exc_info=True,
        )


@router.post("/solve", response_model=OptimiserSolveResponse)
def solve(body: OptimiserSolveRequest) -> OptimiserSolveResponse:
    """Start optimisation for an optimiser node.

    Executes the pipeline up to the optimiser node to materialise the
    scored DataFrame, then runs the solver in a background thread.
    """
    return _solve_service.start(body)


@router.post("/estimate", response_model=OptimiserEstimateResponse)
def estimate_solve(body: OptimiserEstimateRequest) -> OptimiserEstimateResponse:
    """Preview the data volume the solver will see for a given optimiser node.

    Reads parquet metadata from ancestor source nodes to report row/column
    counts without running the pipeline. Mirrors the modelling RAM estimate
    but is simpler — there's no training pool construction to size, and no
    GPU path to check. Returns an empty response if metadata isn't
    available (e.g. live data without parquet backing).
    """
    from haute._ram_estimate import _ancestor_source_metadata

    try:
        total_rows, _max_cols = _ancestor_source_metadata(
            body.graph,
            body.node_id,
            body.source,
        )
    except Exception as exc:
        logger.warning("optimiser_estimate_failed", error=str(exc), node_id=body.node_id)
        return OptimiserEstimateResponse()

    return OptimiserEstimateResponse(total_rows=total_rows)


@router.get("/solve/status/{job_id}", response_model=OptimiserStatusResponse)
async def solve_status(job_id: str) -> OptimiserStatusResponse:
    """Poll optimisation job progress."""
    job = _store.require_job(job_id)

    # Check for timeout on running jobs
    if job.get("status") == "running":
        start = job.get("start_time")
        timeout = job.get("timeout", _DEFAULT_TIMEOUT)
        if start and (time.monotonic() - start) > timeout:
            # P7: Atomic update — only if still running (avoids overwriting a
            # completed result with a timeout error).
            updated_job = _store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": f"Solve timed out after {timeout}s. "
                    "Increase timeout or simplify the problem.",
                    "elapsed_seconds": time.monotonic() - start,
                },
                expected_status="running",
            )
            if updated_job is None:
                job = _store.require_job(job_id)
            else:
                job = updated_job

    frontier_resp = None
    if job.get("status") == "completed":
        fd = job.get("frontier_data")
        if fd:
            frontier_resp = OptimiserFrontierResponse(**fd)

    return OptimiserStatusResponse(
        status=job.get("status", "unknown"),
        progress=job.get("progress", 0.0),
        message=job.get("message", ""),
        elapsed_seconds=job.get("elapsed_seconds", 0.0),
        result=job.get("result"),
        frontier=frontier_resp,
    )


@router.post("/apply", response_model=OptimiserApplyResponse)
def apply_lambdas(body: OptimiserApplyRequest) -> OptimiserApplyResponse:
    """Apply solved lambdas to the scored data."""
    logger.info("apply_requested", job_id=body.job_id)
    job = _store.require_completed_job(body.job_id)

    try:
        solve_result = job.get("solve_result")
        from_artifact = False
        if solve_result is not None:
            df = solve_result.dataframe
            total_objective = solve_result.total_objective
            constraints = solve_result.total_constraints
        else:
            from_artifact = True
            artifact_handles = job.get("artifact_handles", {})
            if not isinstance(artifact_handles, dict):
                raise HTTPException(status_code=500, detail="Job artifact handles are invalid")
            apply_handle = artifact_handles.get(_APPLY_RESULT_HANDLE_KEY)
            if not isinstance(apply_handle, dict):
                raise HTTPException(
                    status_code=400,
                    detail="Job has no apply artifact handle. Re-run the solve to regenerate it.",
                )
            df = _load_apply_result_artifact(apply_handle)
            result = job.get("result")
            if not isinstance(result, dict):
                raise HTTPException(status_code=500, detail="Job summary is missing")
            total_objective = result.get("total_objective")
            constraints = result.get("constraints", {})
            if not isinstance(total_objective, (int, float)) or not isinstance(constraints, dict):
                raise HTTPException(status_code=500, detail="Job summary is incomplete")

        response = OptimiserApplyResponse(
            status="ok",
            total_objective=total_objective,
            constraints=constraints,
            from_artifact=from_artifact,
            **limited_apply_preview_payload(df),
        )
        _store.clear_result_data(body.job_id)
        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("apply_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/frontier", response_model=OptimiserFrontierResponse)
def run_frontier(body: OptimiserFrontierRequest) -> OptimiserFrontierResponse:
    """Compute efficient frontier for a completed optimisation job."""
    job = _store.require_completed_job(body.job_id)

    missing_runtime_detail = (
        "Solver and quote grid are not available for this job. "
        "Re-run the solve to compute a new frontier."
    )
    _touch_heavy_objects_or_raise(
        body.job_id,
        required_keys=("solver", "quote_grid"),
        detail=missing_runtime_detail,
    )
    job = _store.require_completed_job(body.job_id)

    solver = job.get("solver")
    quote_grid = job.get("quote_grid")
    if solver is None or quote_grid is None:
        raise HTTPException(status_code=400, detail=missing_runtime_detail)

    try:
        # Convert threshold ranges from lists to tuples for Rust binding
        ranges = {k: tuple(v) for k, v in body.threshold_ranges.items()}
        frontier_result = solver.frontier(
            quote_grid,
            threshold_ranges=ranges,
            n_points_per_dim=body.n_points_per_dim,
        )
        points_df = frontier_result.points
        response = OptimiserFrontierResponse(
            **limited_frontier_payload(
                points_df,
                constraint_names=list(body.threshold_ranges.keys()),
            )
        )
        return response
    except Exception as exc:
        logger.error("frontier_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/frontier/select", response_model=OptimiserFrontierSelectResponse)
def select_frontier_point(body: OptimiserFrontierSelectRequest) -> OptimiserFrontierSelectResponse:
    """Re-solve at a specific frontier point's lambdas and swap the job's active result."""
    job = _store.require_completed_job(body.job_id)

    # M10: Short-circuit if this point is already selected (idempotent).
    # This cached response does not need solver state, so it remains available
    # after terminal workflows have intentionally cleared heavy objects.
    if job.get("selected_frontier_point") == body.point_index:
        result = job.get("result", {})
        response = OptimiserFrontierSelectResponse(
            status="ok",
            total_objective=result.get("total_objective", 0.0),
            constraints=result.get("constraints", {}),
            baseline_objective=result.get("baseline_objective", 0.0),
            baseline_constraints=result.get("baseline_constraints", {}),
            lambdas=result.get("lambdas", {}),
            converged=result.get("converged", True),
        )
        _store.touch_heavy_objects(body.job_id, required_keys=("solver", "quote_grid"))
        return response

    missing_runtime_detail = (
        "Solver and quote grid are not available for this job. "
        "Re-run the solve to select a frontier point."
    )
    _touch_heavy_objects_or_raise(
        body.job_id,
        required_keys=("solver", "quote_grid"),
        detail=missing_runtime_detail,
    )
    job = _store.require_completed_job(body.job_id)

    solver = job.get("solver")
    quote_grid = job.get("quote_grid")
    frontier_data = job.get("frontier_data")

    if solver is None or quote_grid is None:
        raise HTTPException(status_code=400, detail=missing_runtime_detail)

    if not frontier_data or not frontier_data.get("points"):
        raise HTTPException(status_code=400, detail="Job has no frontier data")

    points = frontier_data["points"]
    total_points = int(frontier_data.get("n_points", len(points)))
    points_returned = int(frontier_data.get("points_returned", len(points)))
    points_limit = frontier_data.get("points_limit", points_returned)
    if body.point_index >= total_points:
        raise HTTPException(
            status_code=400,
            detail=f"Point index {body.point_index} out of range [0, {total_points})",
        )
    if body.point_index >= len(points):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Point index {body.point_index} is not available in the capped frontier "
                f"payload. Only {points_returned} of {total_points} points are retained "
                f"(limit {points_limit}). Re-run the frontier with narrower ranges or "
                "fewer points per dimension."
            ),
        )

    point = points[body.point_index]
    # Extract lambdas from frontier point (columns are lambda_{constraint_name})
    new_lambdas = {
        k.removeprefix("lambda_"): v
        for k, v in point.items()
        if k.startswith("lambda_") and isinstance(v, (int, float))
    }

    if not new_lambdas:
        raise HTTPException(status_code=400, detail="Frontier point has no lambda values")

    new_apply_result_handle: dict[str, Any] | None = None
    try:
        # Re-solve with the selected point's lambdas (warm-start = fast)
        new_result = solver.solve(quote_grid, lambdas=new_lambdas)
        artifact_handles = job.get("artifact_handles", {})
        if not isinstance(artifact_handles, dict):
            raise HTTPException(status_code=500, detail="Job artifact handles are invalid")
        updated_artifact_handles = dict(artifact_handles)
        old_apply_result_handle = updated_artifact_handles.get(_APPLY_RESULT_HANDLE_KEY)
        if old_apply_result_handle is not None and not isinstance(old_apply_result_handle, dict):
            raise HTTPException(status_code=500, detail="Job apply artifact handle is invalid")

        new_apply_result_handle = _persist_apply_result_artifact(new_result)
        if new_apply_result_handle is not None:
            updated_artifact_handles[_APPLY_RESULT_HANDLE_KEY] = new_apply_result_handle
        else:
            updated_artifact_handles.pop(_APPLY_RESULT_HANDLE_KEY, None)

        # Swap the solve_result; preserve original for revert
        result_dict = dict(job.get("result", {}))
        result_dict.update(
            {
                "total_objective": new_result.total_objective,
                "baseline_objective": new_result.baseline_objective,
                "constraints": new_result.total_constraints,
                "baseline_constraints": new_result.baseline_constraints,
                "lambdas": new_result.lambdas,
                "converged": new_result.converged,
                "selected_frontier_point": body.point_index,
            }
        )

        # M1: Recompute scenario value stats for the new solve result
        scenario_stats, scenario_histogram = _compute_scenario_value_stats(new_result)
        result_dict["scenario_value_stats"] = scenario_stats
        result_dict["scenario_value_histogram"] = scenario_histogram

        # M2: Update convergence warning for the new solve result
        if not new_result.converged:
            result_dict["warning"] = (
                "Solver did not converge. Consider increasing max_iter or relaxing tolerance."
            )
        else:
            result_dict.pop("warning", None)

        updated_job = _store.atomic_update_if_heavy_present(
            body.job_id,
            {
                "solve_result": new_result,
                "selected_frontier_point": body.point_index,
                "result": result_dict,
                "artifact_handles": updated_artifact_handles,
            },
            required_keys=("solver", "quote_grid"),
            expected_status="completed",
        )
        if updated_job is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Optimiser runtime state changed while selecting the frontier point. "
                    "Re-run the solve to select a new point."
                ),
            )
        new_apply_result_handle = None
        if old_apply_result_handle is not None:
            try:
                _cleanup_apply_result_artifact(old_apply_result_handle)
            except Exception as cleanup_exc:
                old_apply_result_path = (
                    old_apply_result_handle.get("directory")
                    or old_apply_result_handle.get("path")
                    or "<unknown>"
                )
                logger.warning(
                    "frontier_select_old_apply_artifact_cleanup_failed",
                    job_id=body.job_id,
                    path=str(old_apply_result_path),
                    error=str(cleanup_exc),
                    exc_info=True,
                )

        return OptimiserFrontierSelectResponse(
            status="ok",
            total_objective=new_result.total_objective,
            constraints=new_result.total_constraints,
            baseline_objective=new_result.baseline_objective,
            baseline_constraints=new_result.baseline_constraints,
            lambdas=new_result.lambdas,
            converged=new_result.converged,
        )
    except HTTPException:
        if new_apply_result_handle is not None:
            _cleanup_orphan_apply_artifact(new_apply_result_handle, job_id=body.job_id)
        raise
    except Exception as exc:
        if new_apply_result_handle is not None:
            _cleanup_orphan_apply_artifact(new_apply_result_handle, job_id=body.job_id)
        logger.error("frontier_select_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


def _build_artifact_payload(
    job: dict[str, Any],
    solve_result: SolveResultLike,
    version_override: str = "",
) -> dict[str, Any]:
    """Build the JSON payload for an optimiser artifact.

    Shared by both file-save and MLflow-log paths to avoid duplication.
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
        "baseline_objective": getattr(solve_result, "baseline_objective", None),
        "total_constraints": solve_result.total_constraints,
        "baseline_constraints": getattr(solve_result, "baseline_constraints", None),
        "constraints": job_config.get("constraints"),
        "objective": job_config.get("objective"),
        "quote_id": job_config.get("quote_id", "quote_id"),
        "scenario_index": job_config.get("scenario_index", "scenario_index"),
        "scenario_value": job_config.get("scenario_value", "scenario_value"),
        "chunk_size": job_config.get("chunk_size", _DEFAULT_CHUNK_SIZE),
        "converged": solve_result.converged,
        "iterations": getattr(solve_result, "iterations", None),
        "cd_iterations": getattr(solve_result, "cd_iterations", None),
    }
    # Frontier provenance — record which point was selected (if any)
    selected_idx = job.get("selected_frontier_point")
    frontier_data = job.get("frontier_data")
    if selected_idx is not None and frontier_data:
        payload["frontier_selection"] = {
            "selected_from_frontier": True,
            "point_index": selected_idx,
            "n_frontier_points": frontier_data.get("n_points", 0),
        }
    if job_config.get("mode") == "ratebook":
        payload["factor_tables"] = job.get("result", {}).get("factor_tables")
        payload["clamp_rate"] = getattr(solve_result, "clamp_rate", None)
    return payload


@router.post("/save", response_model=OptimiserSaveResponse)
def save_result(body: OptimiserSaveRequest) -> OptimiserSaveResponse:
    """Save the optimisation result to disk."""
    job = _store.require_completed_job(body.job_id)

    _touch_heavy_objects_or_raise(
        body.job_id,
        required_keys=("solve_result",),
        detail="Job has no solve result",
    )
    job = _store.require_completed_job(body.job_id)
    solve_result = job.get("solve_result")
    if solve_result is None:
        raise HTTPException(status_code=400, detail="Job has no solve result")

    from haute.routes._helpers import pipeline_dir

    # Absolute paths resolve against the project root (security boundary).
    # Relative paths resolve against the pipeline directory (e.g. rating/)
    # so outputs land next to the pipeline file, not at the project root.
    user_path = Path(body.output_path)
    if user_path.is_absolute():
        base = _get_project_root()
    else:
        base = pipeline_dir()
    out = validate_safe_path(base, body.output_path)

    try:
        out.parent.mkdir(parents=True, exist_ok=True)

        payload = _build_artifact_payload(job, solve_result, version_override=body.version)
        out.write_text(json.dumps(payload, indent=2, default=str))
        logger.info("result_saved", path=str(out), job_id=body.job_id)

        response = OptimiserSaveResponse(
            status="ok",
            path=str(out),
            message=f"Saved optimisation result to {out}",
        )
        _store.clear_result_data(body.job_id)
        return response
    except HTTPException:
        raise
    except OSError as exc:
        logger.error("save_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Filesystem error saving optimiser result. Check the server logs for details.",
        )
    except Exception as exc:
        logger.error("save_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/mlflow/log", response_model=OptimiserMlflowLogResponse)
def mlflow_log(body: OptimiserMlflowLogRequest) -> OptimiserMlflowLogResponse:
    """Log optimisation results to MLflow."""
    job = _store.require_completed_job(body.job_id)

    _touch_heavy_objects_or_raise(
        body.job_id,
        required_keys=("solve_result",),
        detail="Job has no solve result",
    )
    _touch_heavy_objects_or_raise(
        body.job_id,
        required_keys=("solver", "solve_result"),
        detail="Solver is not available for this job. Re-run the solve to log results to MLflow.",
    )
    job = _store.require_completed_job(body.job_id)
    solve_result = job.get("solve_result")
    solver = job.get("solver")
    if solve_result is None:
        raise HTTPException(status_code=400, detail="Job has no solve result")
    if solver is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Solver is not available for this job. Re-run the solve to log results to MLflow."
            ),
        )

    try:
        import mlflow
    except ImportError:
        raise HTTPException(
            status_code=400,
            detail="MLflow is not installed. Install with: pip install mlflow",
        )

    try:
        from haute.modelling._mlflow_log import (
            build_run_url,
            configure_mlflow_tracking,
            resolve_experiment_name,
        )

        tracking_uri, backend = configure_mlflow_tracking()

        summary = solver.summary(solve_result)

        node_label = job.get("node_label", "optimiser")
        job_config = job.get("config", {})

        experiment_name = resolve_experiment_name(
            explicit=body.experiment_name,
            config_value=job_config.get("mlflow_experiment"),
            node_label=node_label,
            backend=backend,
        )
        mlflow.set_experiment(experiment_name)

        with mlflow.start_run(run_name=node_label) as run:
            mlflow.log_params(summary["params"])
            mlflow.log_metrics(summary["metrics"])

            # Log artifacts as JSON files
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                artifacts = summary.get("artifacts", {})
                for name, data in artifacts.items():
                    if data is None:
                        continue
                    artifact_path = Path(tmpdir) / f"{name}.json"
                    artifact_path.write_text(json.dumps(data, indent=2, default=str))
                    mlflow.log_artifact(str(artifact_path))

                # Also log the complete artifact used by OPTIMISER_APPLY
                complete_payload = _build_artifact_payload(job, solve_result)
                complete_path = Path(tmpdir) / "optimiser_result.json"
                complete_path.write_text(json.dumps(complete_payload, indent=2, default=str))
                mlflow.log_artifact(str(complete_path))

                # Log frontier CSV artifact + provenance tags
                frontier_data = job.get("frontier_data")
                if frontier_data and frontier_data.get("points"):
                    import csv
                    import io

                    points = frontier_data["points"]
                    buf = io.StringIO()
                    writer = csv.DictWriter(buf, fieldnames=points[0].keys())
                    writer.writeheader()
                    writer.writerows(points)
                    frontier_path = Path(tmpdir) / "frontier.csv"
                    frontier_path.write_text(buf.getvalue())
                    mlflow.log_artifact(str(frontier_path))
                    mlflow.set_tag("frontier.n_points", str(frontier_data["n_points"]))

                selected_idx = job.get("selected_frontier_point")
                if selected_idx is not None:
                    mlflow.set_tag("frontier.selected_point_index", str(selected_idx))

            run_id = run.info.run_id
            run_url = build_run_url(backend, experiment_name, run_id)

        response = OptimiserMlflowLogResponse(
            status="ok",
            backend=backend,
            experiment_name=experiment_name,
            run_id=run_id,
            run_url=run_url,
            tracking_uri=tracking_uri,
        )
        _store.clear_result_data(body.job_id)
        return response
    except Exception as exc:
        logger.error("mlflow_log_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
