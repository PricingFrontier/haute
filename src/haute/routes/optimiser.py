"""Optimiser endpoints: solve, status, apply, save, frontier, mlflow log."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
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
    _ESTIMATE_JOB_TYPE,
    _JOB_TYPE_KEY,
    _NULL_QUOTE_ID_DETAIL_PREFIX,
    OptimiserSolveService,
    _auto_frontier_ranges_from_config,
    _cleanup_apply_result_artifact,
    _compute_frontier,
    _find_optimiser_node,
    _load_apply_result_artifact,
    _normalise_frontier_range,
    _persist_apply_result_artifact,
)
from haute.schemas import (
    OptimiserApplyRequest,
    OptimiserApplyResponse,
    OptimiserEstimateRequest,
    OptimiserEstimateResponse,
    OptimiserFrontierAutoRangeRequest,
    OptimiserFrontierAutoRangeResponse,
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
_FRONTIER_APPLY_HANDLE_PREFIX = "frontier_apply_result:"
_FRONTIER_POINT_SPECIFIC_RESULT_KEYS = (
    "iterations",
    "cd_iterations",
    "clamp_rate",
    "history",
    "scenario_value_stats",
    "scenario_value_histogram",
    "factor_tables",
    "warning",
    "frontier_error",
)


def _touch_heavy_objects_or_raise(
    job_id: str,
    *,
    required_keys: tuple[str, ...],
    detail: str,
) -> None:
    """Reserve completed-job heavy runtime state before using it."""
    if not _store.touch_heavy_objects(job_id, required_keys=required_keys):
        raise HTTPException(status_code=400, detail=detail)


def _cleanup_orphan_apply_artifact(
    handle: dict[str, Any],
    *,
    job_id: str,
    event: str = "frontier_select_orphan_apply_artifact_cleanup_failed",
) -> None:
    """Clean up a request-created apply artifact without masking the main failure."""
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


def _remove_estimate_job(job_id: str) -> None:
    _store.delete_job(job_id)


def _optimiser_input_metrics(body: OptimiserEstimateRequest) -> dict[str, int | float | None]:
    """Return quote/scenario counts for the actual projected optimiser input."""
    import polars as pl

    node = _find_optimiser_node(body.graph, body.node_id)
    config = node.data.config
    _solve_service._validate_config(config)

    job_id = _store.create_job(
        {
            "status": "running",
            _JOB_TYPE_KEY: _ESTIMATE_JOB_TYPE,
            "message": "Estimating optimiser input",
            "config": dict(config),
            "node_label": node.data.label,
        }
    )
    checkpoint_dir = Path(tempfile.mkdtemp(prefix="haute_opt_estimate_"))
    try:
        lazy_outputs = _solve_service._execute_pipeline(body, job_id, checkpoint_dir)
        source_lf = _solve_service._resolve_data_source(
            lazy_outputs,
            config,
            body.node_id,
            job_id,
        )
        _constraint_cols, scored_lf = _solve_service._validate_and_project(
            source_lf,
            config,
            job_id,
        )
        quote_id_col = str(config.get("quote_id", "quote_id"))
        scenario_counts = (
            scored_lf.filter(pl.col(quote_id_col).is_not_null())
            .group_by(quote_id_col)
            .agg(pl.len().alias("scenario_count"))
        )
        row = scenario_counts.select(
            pl.len().alias("quote_count"),
            pl.col("scenario_count").min().alias("scenarios_per_quote_min"),
            pl.col("scenario_count").max().alias("scenarios_per_quote_max"),
            pl.col("scenario_count").mean().alias("scenarios_per_quote_mean"),
            pl.col("scenario_count").sum().alias("expanded_row_count"),
        ).collect(engine="streaming").row(0, named=True)
        return {
            "quote_count": row["quote_count"],
            "scenarios_per_quote_min": row["scenarios_per_quote_min"],
            "scenarios_per_quote_max": row["scenarios_per_quote_max"],
            "scenarios_per_quote_mean": row["scenarios_per_quote_mean"],
            "expanded_row_count": row["expanded_row_count"],
        }
    finally:
        shutil.rmtree(checkpoint_dir, ignore_errors=True)
        _remove_estimate_job(job_id)


def _frontier_ranges_for_request(
    body: OptimiserFrontierRequest,
    job: dict[str, Any],
) -> dict[str, tuple[float, float]]:
    """Resolve explicit or config-derived absolute frontier ranges."""
    if body.threshold_ranges:
        try:
            return {
                str(name): _normalise_frontier_range(
                    value,
                    field=f"threshold_ranges.{name}",
                )
                for name, value in body.threshold_ranges.items()
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    ranges = _auto_frontier_ranges_from_config(job.get("config", {}))
    if not ranges:
        raise HTTPException(
            status_code=400,
            detail=(
                "No frontier threshold ranges provided and the job has no configured "
                "constraints to derive automatic ranges from."
            ),
        )
    return ranges


def _frontier_apply_handle_key(point_index: int) -> str:
    return f"{_FRONTIER_APPLY_HANDLE_PREFIX}{point_index}"


def _as_finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HTTPException(
            status_code=500,
            detail=f"Frontier point data is malformed: field {field!r} is missing",
        )
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise HTTPException(
            status_code=500,
            detail=f"Frontier point data is malformed: field {field!r} is not finite",
        )
    return result


def _frontier_points_or_raise(job: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frontier_data = job.get("frontier_data")
    if not isinstance(frontier_data, dict) or not frontier_data.get("points"):
        raise HTTPException(status_code=400, detail="Job has no frontier data")
    points = frontier_data["points"]
    if not isinstance(points, list) or not all(isinstance(point, dict) for point in points):
        raise HTTPException(status_code=500, detail="Job frontier data is invalid")
    return points, frontier_data


def _frontier_point_or_raise(
    job: dict[str, Any],
    point_index: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    points, frontier_data = _frontier_points_or_raise(job)
    total_points = int(frontier_data.get("n_points", len(points)))
    points_returned = int(frontier_data.get("points_returned", len(points)))
    points_limit = frontier_data.get("points_limit", points_returned)
    if point_index >= total_points:
        raise HTTPException(
            status_code=400,
            detail=f"Point index {point_index} out of range [0, {total_points})",
        )
    if point_index >= len(points):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Point index {point_index} is not available in the capped frontier "
                f"payload. Only {points_returned} of {total_points} points are retained "
                f"(limit {points_limit}). Re-run the frontier with narrower ranges or "
                "fewer points per dimension."
            ),
        )
    return points[point_index], frontier_data


def _frontier_point_lambdas(point: dict[str, Any]) -> dict[str, float]:
    lambdas = {
        k.removeprefix("lambda_"): float(v)
        for k, v in point.items()
        if k.startswith("lambda_") and not isinstance(v, bool) and isinstance(v, int | float)
    }
    if not lambdas:
        raise HTTPException(status_code=400, detail="Frontier point has no lambda values")
    return lambdas


def _frontier_point_constraint_value(point: dict[str, Any], name: str) -> float:
    total_key = f"total_{name}"
    if total_key in point:
        return _as_finite_float(point[total_key], field=total_key)
    constraints = point.get("constraints")
    if isinstance(constraints, dict) and name in constraints:
        return _as_finite_float(constraints[name], field=f"constraints.{name}")
    if name in point:
        return _as_finite_float(point[name], field=name)
    return _as_finite_float(None, field=total_key)


def _scenario_stats_from_frontier_point(point: dict[str, Any]) -> dict[str, float] | None:
    if "sv_mean" not in point:
        return None
    field_map = {
        "mean": "sv_mean",
        "std": "sv_std",
        "min": "sv_min",
        "p5": "sv_p5",
        "p25": "sv_p25",
        "p50": "sv_median",
        "p75": "sv_p75",
        "p95": "sv_p95",
        "max": "sv_max",
        "pct_increase": "sv_pct_increase",
        "pct_decrease": "sv_pct_decrease",
    }
    return {
        out_key: _as_finite_float(point.get(in_key), field=in_key)
        for out_key, in_key in field_map.items()
    }


def _base_result_for_frontier(job: dict[str, Any]) -> dict[str, Any]:
    base_result = job.get("base_result")
    if isinstance(base_result, dict):
        return base_result
    result = job.get("result")
    if isinstance(result, dict):
        return result
    raise HTTPException(status_code=500, detail="Job summary is missing")


def _base_result_for_frontier_recompute(job: dict[str, Any]) -> dict[str, Any]:
    base_result = job.get("base_result")
    if isinstance(base_result, dict):
        result = dict(base_result)
    else:
        current_result = job.get("result")
        if job.get("selected_frontier_point") is not None or (
            isinstance(current_result, dict) and "selected_frontier_point" in current_result
        ):
            raise HTTPException(
                status_code=500,
                detail=(
                    "Job base optimiser result is missing. Re-run the solve before "
                    "recomputing the frontier."
                ),
            )
        if not isinstance(current_result, dict):
            return {}
        result = dict(current_result)
    result.pop("selected_frontier_point", None)
    return result


def _frontier_point_result_dict(job: dict[str, Any], point_index: int) -> dict[str, Any]:
    point, frontier_data = _frontier_point_or_raise(job, point_index)
    constraint_names = frontier_data.get("constraint_names", [])
    if not isinstance(constraint_names, list):
        raise HTTPException(status_code=500, detail="Job frontier constraint names are invalid")

    lambdas = _frontier_point_lambdas(point)
    constraints: dict[str, float] = {}
    for name in constraint_names:
        if not isinstance(name, str):
            raise HTTPException(status_code=500, detail="Job frontier constraint names are invalid")
        constraints[name] = _frontier_point_constraint_value(point, name)

    if not isinstance(point.get("converged"), bool):
        raise HTTPException(status_code=400, detail="Frontier point field 'converged' is missing")

    base_result = _base_result_for_frontier(job)
    result_dict = dict(base_result)
    for key in _FRONTIER_POINT_SPECIFIC_RESULT_KEYS:
        result_dict.pop(key, None)
    result_dict.update(
        {
            "total_objective": _as_finite_float(
                point.get("total_objective"),
                field="total_objective",
            ),
            "baseline_objective": float(base_result.get("baseline_objective", 0.0)),
            "constraints": constraints,
            "baseline_constraints": dict(base_result.get("baseline_constraints", {})),
            "lambdas": lambdas,
            "converged": bool(point["converged"]),
            "selected_frontier_point": point_index,
        }
    )
    if "iterations" in point:
        result_dict["iterations"] = int(_as_finite_float(point["iterations"], field="iterations"))
    if "cd_iterations" in point:
        result_dict["cd_iterations"] = int(
            _as_finite_float(point["cd_iterations"], field="cd_iterations")
        )
    if "clamp_rate" in point:
        result_dict["clamp_rate"] = _as_finite_float(point["clamp_rate"], field="clamp_rate")

    scenario_stats = _scenario_stats_from_frontier_point(point)
    if scenario_stats is not None:
        result_dict["scenario_value_stats"] = scenario_stats
        result_dict.pop("scenario_value_histogram", None)

    if result_dict["converged"]:
        result_dict.pop("warning", None)
    else:
        result_dict["warning"] = (
            "Solver did not converge. Consider increasing max_iter or relaxing tolerance."
        )
    return result_dict


def _frontier_select_response(result: dict[str, Any]) -> OptimiserFrontierSelectResponse:
    return OptimiserFrontierSelectResponse(
        status="ok",
        point_index=result.get("selected_frontier_point"),
        total_objective=result.get("total_objective", 0.0),
        constraints=result.get("constraints", {}),
        baseline_objective=result.get("baseline_objective", 0.0),
        baseline_constraints=result.get("baseline_constraints", {}),
        lambdas=result.get("lambdas", {}),
        converged=result.get("converged", True),
    )


def _job_has_frontier_points(job: dict[str, Any]) -> bool:
    frontier_data = job.get("frontier_data")
    if not isinstance(frontier_data, dict):
        return False
    points = frontier_data.get("points")
    return isinstance(points, list) and len(points) > 0


def _cached_result_matches_frontier_selection(result: dict[str, Any], point_index: int) -> bool:
    selected_point = result.get("selected_frontier_point")
    return isinstance(selected_point, int) and not isinstance(selected_point, bool) and (
        selected_point == point_index
    )


def _lambda_mappings_match(cached_lambdas: Any, expected_lambdas: Any) -> bool:
    if not isinstance(cached_lambdas, dict) or not isinstance(expected_lambdas, dict):
        return False
    if set(cached_lambdas) != set(expected_lambdas):
        return False
    for name in expected_lambdas:
        try:
            cached_value = float(cached_lambdas[name])
            expected_value = float(expected_lambdas[name])
        except (TypeError, ValueError):
            return False
        if abs(cached_value - expected_value) > 1e-9:
            return False
    return True


def _summary_solve_result(result: dict[str, Any]) -> SolveResultLike:
    return SimpleNamespace(
        lambdas=result["lambdas"],
        total_objective=result["total_objective"],
        total_constraints=result["constraints"],
        baseline_objective=result.get("baseline_objective", 0.0),
        baseline_constraints=result.get("baseline_constraints", {}),
        converged=result["converged"],
        iterations=result.get("iterations"),
        cd_iterations=result.get("cd_iterations"),
        clamp_rate=result.get("clamp_rate"),
        factor_tables=result.get("factor_tables"),
    )


def _selected_or_requested_frontier_point(
    job: dict[str, Any],
    requested_point_index: int | None,
) -> int | None:
    if requested_point_index is not None:
        return requested_point_index
    selected = job.get("selected_frontier_point")
    return selected if isinstance(selected, int) and not isinstance(selected, bool) else None


def _frontier_point_result_for_job(job: dict[str, Any], point_index: int) -> dict[str, Any]:
    return _frontier_point_result_dict(
        {**job, "base_result": _base_result_for_frontier(job)},
        point_index,
    )


def _result_mode(job: dict[str, Any], result: dict[str, Any]) -> str:
    return str(job.get("config", {}).get("mode", result.get("mode", "online")))


def _cached_materialised_ratebook_frontier_result(
    job: dict[str, Any],
    point_index: int,
    expected_lambdas: dict[str, Any],
) -> dict[str, Any] | None:
    result = job.get("result")
    if (
        isinstance(result, dict)
        and _cached_result_matches_frontier_selection(result, point_index)
        and isinstance(result.get("factor_tables"), dict)
        and _lambda_mappings_match(result.get("lambdas"), expected_lambdas)
    ):
        return result
    return None


def _serialise_ratebook_factor_tables(
    factor_tables: Any,
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(factor_tables, dict):
        raise HTTPException(status_code=500, detail="Ratebook factor tables are invalid")

    serialised: dict[str, list[dict[str, Any]]] = {}
    for name, table in factor_tables.items():
        if not isinstance(name, str) or not isinstance(table, dict):
            raise HTTPException(status_code=500, detail="Ratebook factor tables are invalid")
        serialised[name] = [
            {
                "__factor_group__": level,
                "optimal_scenario_value": _as_finite_float(
                    scenario_value,
                    field=f"factor_tables.{name}",
                ),
            }
            for level, scenario_value in table.items()
        ]
    return serialised


def _materialised_ratebook_result_dict(
    result_dict: dict[str, Any],
    solve_result: SolveResultLike,
) -> dict[str, Any]:
    materialised = dict(result_dict)
    materialised.update(
        {
            "total_objective": solve_result.total_objective,
            "baseline_objective": solve_result.baseline_objective,
            "constraints": solve_result.total_constraints,
            "baseline_constraints": solve_result.baseline_constraints,
            "lambdas": solve_result.lambdas,
            "converged": solve_result.converged,
            "cd_iterations": getattr(solve_result, "cd_iterations", None),
            "clamp_rate": getattr(solve_result, "clamp_rate", None),
            "factor_tables": _serialise_ratebook_factor_tables(
                getattr(solve_result, "factor_tables", None)
            ),
            "history": None,
        }
    )
    if materialised["converged"]:
        materialised.pop("warning", None)
    else:
        materialised["warning"] = (
            "Solver did not converge. Consider increasing max_iter or relaxing tolerance."
        )
    return materialised


def _ratebook_runtime_state_or_raise(
    job_id: str,
) -> tuple[dict[str, Any], Any, Any, Any, list[list[str]]]:
    if not _store.touch_heavy_objects(
        job_id,
        required_keys=("solver", "quote_grid", "factors_df"),
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Ratebook runtime state is not available for this job. Re-run the "
                "solve to materialise this frontier point."
            ),
        )

    job = _store.require_completed_job(job_id)
    solver = job.get("solver")
    quote_grid = job.get("quote_grid")
    factors_df = job.get("factors_df")
    factor_columns = job.get("factor_columns_valid")
    if solver is None or quote_grid is None or factors_df is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Ratebook runtime state is not available for this job. Re-run the "
                "solve to materialise this frontier point."
            ),
        )
    if (
        not isinstance(factor_columns, list)
        or not all(
            isinstance(group, list) and all(isinstance(col, str) for col in group)
            for group in factor_columns
        )
    ):
        raise HTTPException(status_code=500, detail="Ratebook factor column metadata is invalid")
    return job, solver, quote_grid, factors_df, factor_columns


def _materialise_ratebook_frontier_point(
    job_id: str,
    point_index: int,
    result_dict: dict[str, Any],
    *,
    require_dataframe: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], SolveResultLike]:
    job = _store.require_completed_job(job_id)
    cached_result = _cached_materialised_ratebook_frontier_result(
        job,
        point_index,
        result_dict["lambdas"],
    )
    if cached_result is not None and not require_dataframe:
        return job, cached_result, _summary_solve_result(cached_result)

    job, solver, quote_grid, factors_df, factor_columns = _ratebook_runtime_state_or_raise(job_id)
    base_result = _base_result_for_frontier(job)
    solve_result = solver.solve(
        quote_grid,
        factors_df,
        factor_columns=factor_columns,
        lambdas=result_dict["lambdas"],
    )
    materialised = _materialised_ratebook_result_dict(result_dict, solve_result)
    updated_job = _store.atomic_update(
        job_id,
        {
            "base_result": base_result,
            "selected_frontier_point": point_index,
            "result": materialised,
        },
        expected_status="completed",
    )
    if updated_job is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Optimiser job state changed while materialising the frontier point. "
                "Re-run the solve to materialise it again."
            ),
        )
    return updated_job, materialised, solve_result


def _solve_result_for_selected_frontier_point(
    job_id: str,
    job: dict[str, Any],
    point_index: int,
) -> tuple[dict[str, Any], dict[str, Any], SolveResultLike]:
    selected_result = _frontier_point_result_for_job(job, point_index)
    if _result_mode(job, selected_result) == "ratebook":
        updated_job, selected_result, _materialised_solve_result = (
            _materialise_ratebook_frontier_point(
                job_id,
                point_index,
                selected_result,
            )
        )
        return updated_job, selected_result, _summary_solve_result(selected_result)
    return job, selected_result, _summary_solve_result(selected_result)


def _frontier_point_mlflow_summary(
    job: dict[str, Any],
    result: dict[str, Any],
    point_index: int,
) -> dict[str, dict[str, Any]]:
    job_config = job.get("config", {})
    metrics: dict[str, float] = {
        "total_objective": float(result["total_objective"]),
        "converged": 1.0 if result.get("converged") is True else 0.0,
    }
    for name, value in result.get("constraints", {}).items():
        metrics[f"constraint.{name}"] = float(value)
    for name, value in result.get("lambdas", {}).items():
        metrics[f"lambda.{name}"] = float(value)

    params = {
        "mode": str(job_config.get("mode", result.get("mode", "online"))),
        "objective": str(job_config.get("objective", "")),
        "selected_frontier_point": str(point_index),
    }
    return {
        "params": params,
        "metrics": metrics,
        "artifacts": {
            "lambdas": result.get("lambdas", {}),
            "frontier_point_summary": result,
        },
    }


def _artifact_handles_or_raise(job: dict[str, Any]) -> dict[str, Any]:
    artifact_handles = job.get("artifact_handles", {})
    if not isinstance(artifact_handles, dict):
        raise HTTPException(status_code=500, detail="Job artifact handles are invalid")
    return artifact_handles


def _invalidate_frontier_apply_artifact_handles(
    job: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    artifact_handles = _artifact_handles_or_raise(job)
    retained_handles: dict[str, Any] = {}
    invalidated_handles: list[dict[str, Any]] = []
    for key, handle in artifact_handles.items():
        if key.startswith(_FRONTIER_APPLY_HANDLE_PREFIX):
            if not isinstance(handle, dict):
                raise HTTPException(
                    status_code=500,
                    detail="Job frontier apply artifact handle is invalid",
                )
            invalidated_handles.append(handle)
        else:
            retained_handles[key] = handle
    return retained_handles, invalidated_handles


def _materialise_frontier_point_apply(
    job_id: str,
    point_index: int,
) -> tuple[Any, dict[str, Any], bool]:
    """Return ``(dataframe, result_summary, from_cached_artifact)`` for a frontier point."""
    job = _store.require_completed_job(job_id)
    base_result = _base_result_for_frontier(job)
    result_dict = _frontier_point_result_dict({**job, "base_result": base_result}, point_index)
    mode = job.get("config", {}).get("mode", result_dict.get("mode", "online"))
    artifact_handles = _artifact_handles_or_raise(job)
    handle_key = _frontier_apply_handle_key(point_index)
    existing_handle = artifact_handles.get(handle_key)
    if existing_handle is not None:
        if not isinstance(existing_handle, dict):
            raise HTTPException(
                status_code=500,
                detail="Job frontier apply artifact handle is invalid",
            )
        cached_result = _cached_materialised_ratebook_frontier_result(
            job,
            point_index,
            result_dict["lambdas"],
        )
        if cached_result is not None:
            result_dict = cached_result
            _store.atomic_update(
                job_id,
                {
                    "base_result": base_result,
                    "selected_frontier_point": point_index,
                    "result": result_dict,
                },
                expected_status="completed",
            )
            return _load_apply_result_artifact(existing_handle), result_dict, True
        if mode != "ratebook":
            _store.atomic_update(
                job_id,
                {
                    "base_result": base_result,
                    "selected_frontier_point": point_index,
                    "result": result_dict,
                },
                expected_status="completed",
            )
            return _load_apply_result_artifact(existing_handle), result_dict, True

    new_handle: dict[str, Any] | None = None
    try:
        if mode == "ratebook":
            job, result_dict, apply_result = _materialise_ratebook_frontier_point(
                job_id,
                point_index,
                result_dict,
                require_dataframe=True,
            )
        else:
            if not _store.touch_heavy_objects(job_id, required_keys=("quote_grid",)):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Quote grid is not available for this job. Re-run the solve to "
                        "materialise this frontier point."
                    ),
                )
            job = _store.require_completed_job(job_id)
            quote_grid = job.get("quote_grid")
            if quote_grid is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Quote grid is not available for this job. Re-run the solve to "
                        "materialise this frontier point."
                    ),
                )

            from price_contour import apply_from_grid

            apply_result = apply_from_grid(
                quote_grid,
                lambdas=result_dict["lambdas"],
                constraints=job.get("config", {}).get("constraints", {}),
            )
        new_handle = _persist_apply_result_artifact(apply_result)
        df = apply_result.dataframe
        if new_handle is None:
            _store.atomic_update(
                job_id,
                {
                    "base_result": base_result,
                    "selected_frontier_point": point_index,
                    "result": result_dict,
                },
                expected_status="completed",
            )
            return df, result_dict, False

        latest_job = _store.require_completed_job(job_id)
        latest_handles = _artifact_handles_or_raise(latest_job)
        updated_handles = dict(latest_handles)
        updated_handles[handle_key] = new_handle
        update_fields = {
            "artifact_handles": updated_handles,
            "base_result": base_result,
            "selected_frontier_point": point_index,
            "result": result_dict,
        }
        if mode == "ratebook":
            updated_job = _store.atomic_update(
                job_id,
                update_fields,
                expected_status="completed",
            )
        else:
            updated_job = _store.atomic_update_if_heavy_present(
                job_id,
                update_fields,
                required_keys=("quote_grid",),
                expected_status="completed",
            )
        if updated_job is None:
            _cleanup_orphan_apply_artifact(new_handle, job_id=job_id)
            raise HTTPException(
                status_code=409,
                detail=(
                    "Optimiser runtime state changed while materialising the frontier point. "
                    "Re-run the solve to materialise it again."
                ),
            )
        return df, result_dict, False
    except HTTPException:
        raise
    except Exception as exc:
        if new_handle is not None:
            _cleanup_orphan_apply_artifact(new_handle, job_id=job_id)
        logger.error(
            "frontier_apply_materialise_failed",
            error=str(exc),
            job_id=job_id,
            point_index=point_index,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


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

    total_rows: int | None = None
    try:
        total_rows, _max_cols = _ancestor_source_metadata(
            body.graph,
            body.node_id,
            body.source,
        )
    except Exception as exc:
        logger.warning("optimiser_estimate_failed", error=str(exc), node_id=body.node_id)

    metrics: dict[str, int | float | None] = {}
    try:
        metrics = _optimiser_input_metrics(body)
    except HTTPException as exc:
        if isinstance(exc.detail, str) and exc.detail.startswith(_NULL_QUOTE_ID_DETAIL_PREFIX):
            raise
        logger.warning(
            "optimiser_input_estimate_failed",
            error=str(exc),
            node_id=body.node_id,
        )
    except Exception as exc:
        logger.warning(
            "optimiser_input_estimate_failed",
            error=str(exc),
            node_id=body.node_id,
        )

    return OptimiserEstimateResponse(total_rows=total_rows, **metrics)


@router.post("/frontier/auto-range", response_model=OptimiserFrontierAutoRangeResponse)
def estimate_frontier_auto_range(
    body: OptimiserFrontierAutoRangeRequest,
) -> OptimiserFrontierAutoRangeResponse:
    """Estimate absolute efficient-frontier ranges from the scenario dataframe."""
    return _solve_service.estimate_frontier_auto_range(body)


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
        target_point_index = _selected_or_requested_frontier_point(job, body.point_index)
        if target_point_index is not None:
            df, result, from_artifact = _materialise_frontier_point_apply(
                body.job_id,
                target_point_index,
            )
            response = OptimiserApplyResponse(
                status="ok",
                total_objective=result["total_objective"],
                constraints=result["constraints"],
                from_artifact=from_artifact,
                **limited_apply_preview_payload(df),
            )
            _store.clear_result_data(body.job_id)
            return response

        solve_result = job.get("solve_result")
        from_artifact = False
        if solve_result is not None:
            df = solve_result.dataframe
            total_objective = solve_result.total_objective
            constraints = solve_result.total_constraints
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
    mode = job.get("config", {}).get("mode", job.get("result", {}).get("mode", "online"))
    required_runtime = ("solver", "quote_grid", "factors_df") if mode == "ratebook" else (
        "solver",
        "quote_grid",
    )

    missing_runtime_detail = (
        "Solver and quote grid are not available for this job. "
        "Re-run the solve to compute a new frontier."
    )
    _touch_heavy_objects_or_raise(
        body.job_id,
        required_keys=required_runtime,
        detail=missing_runtime_detail,
    )
    job = _store.require_completed_job(body.job_id)

    solver = job.get("solver")
    quote_grid = job.get("quote_grid")
    factors_df = job.get("factors_df")
    factor_columns = job.get("factor_columns_valid")
    if solver is None or quote_grid is None:
        raise HTTPException(status_code=400, detail=missing_runtime_detail)

    try:
        base_result = _base_result_for_frontier_recompute(job)
        ranges = _frontier_ranges_for_request(body, job)
        frontier_result = _compute_frontier(
            solver,
            quote_grid,
            mode=mode,
            factors_df=factors_df,
            factor_columns=factor_columns,
            threshold_ranges=ranges,
            n_points_per_dim=body.n_points_per_dim,
            initial_lambdas=base_result.get("lambdas"),
        )
        points_df = frontier_result.points
        response = OptimiserFrontierResponse(
            **limited_frontier_payload(
                points_df,
                constraint_names=list(ranges.keys()),
            )
        )
        frontier_dict = response.model_dump()
        result_dict = dict(base_result)
        result_dict["frontier"] = frontier_dict
        result_dict.pop("frontier_error", None)
        result_dict.pop("selected_frontier_point", None)
        latest_job = _store.require_completed_job(body.job_id)
        retained_handles, invalidated_handles = _invalidate_frontier_apply_artifact_handles(
            latest_job
        )
        update_fields: dict[str, Any] = {
            "result": result_dict,
            "base_result": dict(result_dict),
            "frontier_data": frontier_dict,
            "selected_frontier_point": None,
            "artifact_handles": retained_handles,
        }
        updated_job = _store.atomic_update(
            body.job_id,
            update_fields,
            expected_status="completed",
        )
        if updated_job is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Optimiser job state changed while recomputing the frontier. "
                    "Re-run the solve to compute a new frontier."
                ),
            )
        for handle in invalidated_handles:
            _cleanup_orphan_apply_artifact(
                handle,
                job_id=body.job_id,
                event="frontier_recompute_stale_apply_artifact_cleanup_failed",
            )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("frontier_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/frontier/select", response_model=OptimiserFrontierSelectResponse)
def select_frontier_point(body: OptimiserFrontierSelectRequest) -> OptimiserFrontierSelectResponse:
    """Select a frontier summary point without re-solving the optimiser."""
    job = _store.require_completed_job(body.job_id)

    try:
        if (
            body.point_index is not None
            and job.get("selected_frontier_point") == body.point_index
            and isinstance(job.get("result"), dict)
            and _cached_result_matches_frontier_selection(job["result"], body.point_index)
            and not _job_has_frontier_points(job)
        ):
            return _frontier_select_response(job["result"])

        existing_base_result = job.get("base_result")
        base_result = existing_base_result if isinstance(existing_base_result, dict) else dict(
            job.get("result", {})
        )
        if body.point_index is None:
            result_dict = dict(base_result)
            result_dict.pop("selected_frontier_point", None)
            selected_point: int | None = None
        else:
            result_dict = _frontier_point_result_dict(
                {**job, "base_result": base_result},
                body.point_index,
            )
            selected_point = body.point_index

        updated_job = _store.atomic_update(
            body.job_id,
            {
                "base_result": base_result,
                "selected_frontier_point": selected_point,
                "result": result_dict,
            },
            expected_status="completed",
        )
        if updated_job is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Optimiser job state changed while selecting the frontier point. "
                    "Re-run the solve to select a new point."
                ),
            )

        if selected_point is not None:
            _store.clear_result_data(body.job_id, keys=("solve_result",))

        return _frontier_select_response(result_dict)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("frontier_select_failed", error=str(exc), job_id=body.job_id, exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


def _build_artifact_payload(
    job: dict[str, Any],
    solve_result: SolveResultLike,
    version_override: str = "",
    selected_frontier_point: int | None = None,
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
    selected_idx = (
        selected_frontier_point
        if selected_frontier_point is not None
        else job.get("selected_frontier_point")
    )
    frontier_data = job.get("frontier_data")
    if selected_idx is not None and frontier_data:
        payload["frontier_selection"] = {
            "selected_from_frontier": True,
            "point_index": selected_idx,
            "n_frontier_points": frontier_data.get("n_points", 0),
        }
    if job_config.get("mode") == "ratebook":
        factor_tables = (
            job["result"].get("factor_tables")
            if isinstance(job.get("result"), dict)
            else None
        )
        if factor_tables is None:
            factor_tables = getattr(solve_result, "factor_tables", None)
        payload["factor_tables"] = factor_tables
        payload["clamp_rate"] = getattr(solve_result, "clamp_rate", None)
    return payload


@router.post("/save", response_model=OptimiserSaveResponse)
def save_result(body: OptimiserSaveRequest) -> OptimiserSaveResponse:
    """Save the optimisation result to disk."""
    job = _store.require_completed_job(body.job_id)

    selected_frontier_point = _selected_or_requested_frontier_point(job, body.point_index)
    if selected_frontier_point is not None:
        job, _selected_result, solve_result = _solve_result_for_selected_frontier_point(
            body.job_id,
            job,
            selected_frontier_point,
        )
    else:
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

        payload = _build_artifact_payload(
            job,
            solve_result,
            version_override=body.version,
            selected_frontier_point=selected_frontier_point,
        )
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

    selected_frontier_point = _selected_or_requested_frontier_point(job, body.point_index)
    selected_result: dict[str, Any] | None = None
    if selected_frontier_point is not None:
        job, selected_result, solve_result = _solve_result_for_selected_frontier_point(
            body.job_id,
            job,
            selected_frontier_point,
        )
        solver = None
    else:
        _touch_heavy_objects_or_raise(
            body.job_id,
            required_keys=("solve_result",),
            detail="Job has no solve result",
        )
        _touch_heavy_objects_or_raise(
            body.job_id,
            required_keys=("solver", "solve_result"),
            detail=(
                "Solver is not available for this job. "
                "Re-run the solve to log results to MLflow."
            ),
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
                    "Solver is not available for this job. "
                    "Re-run the solve to log results to MLflow."
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

        node_label = job.get("node_label", "optimiser")
        job_config = job.get("config", {})
        if selected_result is None:
            summary = solver.summary(solve_result)
        else:
            summary = _frontier_point_mlflow_summary(
                job,
                selected_result,
                selected_frontier_point,
            )

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
                complete_payload = _build_artifact_payload(
                    job,
                    solve_result,
                    selected_frontier_point=selected_frontier_point,
                )
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

                selected_idx = (
                    selected_frontier_point
                    if selected_frontier_point is not None
                    else job.get("selected_frontier_point")
                )
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
