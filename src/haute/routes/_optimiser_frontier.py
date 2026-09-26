"""The optimiser's frontier domain: sweeps, point selection and point artifacts.

``OptimiserFrontierService`` owns the background frontier sweep (single-flight
admission per parent solve, cancellation, timeout, publication onto the parent
job), frontier-point selection, the ratebook point materialisation and the
retained frontier-point apply artifacts. Every state change for one parent
solve holds that parent's lock, so frontier work on one solve never waits for
another's parquet reads or deletions. The pure range and point helpers live
here too. FastAPI response assembly stays in ``routes/optimiser.py``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, Protocol, cast

from fastapi import HTTPException

from haute._execution_admission import (
    ExecutionAdmissionError,
    WorkEstimate,
    create_admitted_execution_context,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._logging import get_logger
from haute._price_contour import price_contour
from haute._ram_estimate import decoded_frame_row_width_bytes, estimate_point_apply_peak_bytes
from haute._ratebook_collar import COMBINED_FACTOR_BOUNDS_KEY
from haute._types import SolveResultLike
from haute.routes._background_jobs import (
    BackgroundJobStoppedError,
    CancellableJobRegistry,
)
from haute.routes._frontier_point_summary import (
    CONSTRAINT_THRESHOLD_KINDS,
    NON_CONVERGED_WARNING,
    ConstraintKind,
    apply_frontier_point_summary,
    constraint_kinds,
    frontier_point_summary,
)
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.routes._job_lifecycle import JobLifecycle, TerminalReason
from haute.routes._job_store import JobSnapshot, JobStore, RunningJobFields
from haute.routes._optimiser_adjustments import ADJUSTMENT_REPORTS_KEY
from haute.routes._optimiser_artifacts import (
    _APPLY_RESULT_HANDLE_KEY,
    _RATEBOOK_FACTORS_HANDLE_KEY,
    APPLY_RESULT_UNAVAILABLE_DETAIL,
    _cleanup_apply_result_artifact,
    _persist_apply_result_artifact,
    frontier_point_unavailable_detail,
)
from haute.routes._optimiser_input import (
    _estimate_quote_id_column_or_raise,  # noqa: F401 - estimate pre-flight, re-exported
)
from haute.routes._optimiser_limits import (
    FrontierComputeBudgetExceededError,
    enforce_frontier_compute_budget,
    limited_frontier_payload,
)
from haute.routes._optimiser_outcomes import lease_apply_frame
from haute.routes._optimiser_service import (
    _FRONTIER_RECOMPUTE_JOB_TYPE,
    _JOB_TYPE_KEY,
    _solve_timeout_from_config,
)
from haute.routes._optimiser_solver import (
    _FRONTIER_FACTOR_TABLES_KEY,
    _FRONTIER_GENERATION_KEY,
    _RATEBOOK_FACTOR_LEVEL_ORDER_KEY,
    _auto_frontier_ranges_from_config,
    _build_ratebook_factor_contexts,
    _compute_frontier,
    _ratebook_factor_dtypes_from_artifact,
    _ratebook_factor_level_counts_from_artifact,
    _serialise_ratebook_factor_tables,
    frontier_point_factor_tables,
    solver_worker_context,
)
from haute.routes._shared_flights import (
    FlightReplacedError,
    FlightSubscription,
    LatestWinsQueue,
)
from haute.schemas import (
    OptimiserFrontierRequest,
    OptimiserFrontierResponse,
    OptimiserFrontierSelectRequest,
)

logger = get_logger(component="server.optimiser")

_FRONTIER_APPLY_HANDLE_PREFIX = "frontier_apply_result:"
_MAX_FRONTIER_APPLY_ARTIFACTS = 8
_POINT_APPLY_OPERATION = "optimiser_point_apply"
_FRONTIER_CHANGED_DETAIL = (
    "The frontier changed while materialising the selected point. "
    "Select a point from the current frontier and try again."
)
FRONTIER_POINT_APPLY_REPLACED_DETAIL = {
    "error_code": "frontier_point_apply_replaced",
    "message": (
        "Another frontier point was requested while this one waited for the point being "
        "applied, so this request was dropped. Select the point again to inspect it."
    ),
}
_CONSTRAINT_THRESHOLD_KEYS = tuple(CONSTRAINT_THRESHOLD_KINDS)


class _FrontierRecomputeRunningJob(RunningJobFields):
    job_type: Literal["frontier_recompute"]
    progress: float
    parent_job_id: str
    start_time: float
    timeout: int | None


# The real ``price_contour.RatebookResult`` carries factor tables and
# portfolio aggregates only — there is NO per-quote dataframe to serve, so
# the ``/apply`` ("Load detail") affordance has no ratebook backend.  Pinned
# by ``tests/test_optimiser_routes_real_library.py``.
_RATEBOOK_APPLY_DETAIL_UNSUPPORTED = (
    "Per-quote apply detail is not available for ratebook optimiser results: "
    "the ratebook solver produces factor tables, not per-quote scenario "
    "selections. Use the factor tables on the result (Rates tab), or save the "
    "result and apply it with an Optimiser Apply node."
)


def _job_mode(job: Mapping[str, Any]) -> str:
    """Resolve the optimiser mode for a completed job (config wins)."""
    result = job.get("result")
    result_mode = result.get("mode", "online") if isinstance(result, dict) else "online"
    return str(job.get("config", {}).get("mode", result_mode))


def _reject_ratebook_apply_detail(job: Mapping[str, Any]) -> None:
    """Gate ``/apply`` for ratebook jobs with an explicit contract error.

    Raising here — before any heavy-state lookups or solver work — keeps the
    failure cheap and actionable.  Without the gate the request either dies
    on the missing ``RatebookResult.dataframe`` (opaque 500) or, worse, an
    ``apply_from_grid`` fallback would return per-quote selections that
    ignore the solved factor tables: silently wrong output.
    """
    if _job_mode(job) == "ratebook":
        raise HTTPException(status_code=422, detail=_RATEBOOK_APPLY_DETAIL_UNSUPPORTED)


class _DataFrameResultLike(Protocol):
    @property
    def dataframe(self) -> Any: ...  # pragma: no cover  (typing-only stub)


def _dataframe_or_raise(result: Any, *, context: str) -> Any:
    """Read ``.dataframe`` from a solver/apply result, with a typed error.

    The Protocol-cast at call sites is a pure type-checker hint and gives
    no runtime guarantee.  Without this guard a missing attribute surfaces as
    an ``AttributeError`` and an opaque 500 from the application handler.
    """
    if not hasattr(result, "dataframe"):
        raise HTTPException(
            status_code=500,
            detail=f"{context} is missing a 'dataframe' attribute",
        )
    return cast(_DataFrameResultLike, result).dataframe


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


def _frontier_ranges_for_request(
    body: OptimiserFrontierRequest,
    job: Mapping[str, Any],
) -> dict[str, tuple[float, float]]:
    """Resolve explicit or config-derived absolute frontier ranges.

    The request-body shape is already validated by ``OptimiserFrontierRequest``'s
    Pydantic field validator, so explicit ranges only need to be tupled here.
    Config-derived ranges still go through validation because configs are
    arbitrary user JSON, not schema-validated bodies.
    """
    if body.threshold_ranges:
        return {
            str(name): (float(value[0]), float(value[1]))
            for name, value in body.threshold_ranges.items()
        }

    try:
        ranges = _auto_frontier_ranges_from_config(job.get("config", {}))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


def _frontier_generation_or_raise(job: Mapping[str, Any]) -> int:
    """Return the store-stable generation for the parent's current frontier."""
    generation = job.get(_FRONTIER_GENERATION_KEY, 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise HTTPException(
            status_code=500,
            detail="Job frontier generation is invalid",
        )
    return int(generation)


def _with_bounded_frontier_apply_handle(
    existing_handles: dict[str, Any],
    handle_key: str,
    new_handle: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Append one point artifact and evict the oldest point handles over the cap."""
    updated_handles = dict(existing_handles)
    updated_handles.pop(handle_key, None)
    updated_handles[handle_key] = new_handle
    frontier_keys = [
        key for key in updated_handles if key.startswith(_FRONTIER_APPLY_HANDLE_PREFIX)
    ]
    evicted_handles: list[dict[str, Any]] = []
    for stale_key in frontier_keys[:-_MAX_FRONTIER_APPLY_ARTIFACTS]:
        stale_handle = updated_handles.pop(stale_key)
        if not isinstance(stale_handle, dict):
            raise HTTPException(
                status_code=500,
                detail="Job frontier apply artifact handle is invalid",
            )
        evicted_handles.append(stale_handle)
    return updated_handles, evicted_handles


def _frontier_points_or_raise(
    job: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frontier_data = job.get("frontier_data")
    if not isinstance(frontier_data, dict) or not frontier_data.get("points"):
        raise HTTPException(status_code=400, detail="Job has no frontier data")
    points = frontier_data["points"]
    if not isinstance(points, list) or not all(isinstance(point, dict) for point in points):
        raise HTTPException(status_code=500, detail="Job frontier data is invalid")
    return points, frontier_data


def _frontier_point_or_raise(
    job: Mapping[str, Any],
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


def _base_result_for_frontier(job: Mapping[str, Any]) -> dict[str, Any]:
    base_result = job.get("base_result")
    if isinstance(base_result, dict):
        return base_result
    result = job.get("result")
    if isinstance(result, dict):
        return result
    raise HTTPException(status_code=500, detail="Job summary is missing")


def _base_result_for_frontier_recompute(job: Mapping[str, Any]) -> dict[str, Any]:
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
            raise HTTPException(status_code=500, detail="Job summary is missing")
        result = dict(current_result)
    result.pop("selected_frontier_point", None)
    return result


def _job_constraint_kinds(job: Mapping[str, Any]) -> dict[str, ConstraintKind]:
    """Every configured constraint's kind; a malformed config is a 500."""
    try:
        return constraint_kinds(job.get("config", {}).get("constraints") or {})
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail=f"Job optimiser constraints are invalid: {exc}"
        ) from exc


def _frontier_point_result_dict(job: Mapping[str, Any], point_index: int) -> dict[str, Any]:
    point, frontier_data = _frontier_point_or_raise(job, point_index)
    kinds = _job_constraint_kinds(job)
    # The stored frontier summarised every configured constraint, swept or not.
    if frontier_data.get("constraint_names") != list(kinds):
        raise HTTPException(
            status_code=500,
            detail="Job frontier constraint names do not match the configured constraints",
        )
    summary = frontier_point_summary(point, kinds)

    base_result = _base_result_for_frontier(job)
    result_dict = apply_frontier_point_summary(base_result, summary)
    result_dict["baseline_objective"] = float(base_result["baseline_objective"])
    result_dict["baseline_constraints"] = dict(base_result["baseline_constraints"])
    result_dict["selected_frontier_point"] = point_index
    return result_dict


def _frontier_point_constraints_override(
    job: Mapping[str, Any],
    point_index: int,
) -> dict[str, dict[str, Any]]:
    point, frontier_data = _frontier_point_or_raise(job, point_index)
    raw_constraints = job.get("config", {}).get("constraints")
    if not isinstance(raw_constraints, dict):
        raise HTTPException(status_code=500, detail="Job optimiser constraints are invalid")

    overrides: dict[str, dict[str, Any]] = {}
    for name, spec in raw_constraints.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise HTTPException(status_code=500, detail="Job optimiser constraints are invalid")
        overrides[name] = dict(spec)

    constraint_names = frontier_data.get("constraint_names", [])
    if not isinstance(constraint_names, list):
        raise HTTPException(status_code=500, detail="Job frontier constraint names are invalid")

    for name in constraint_names:
        if not isinstance(name, str):
            raise HTTPException(status_code=500, detail="Job frontier constraint names are invalid")
        spec = overrides.get(name)
        if spec is None:
            raise HTTPException(status_code=500, detail="Job frontier constraint is missing")
        threshold_keys = [key for key in _CONSTRAINT_THRESHOLD_KEYS if key in spec]
        if len(threshold_keys) != 1:
            raise HTTPException(
                status_code=500,
                detail="Job optimiser constraint threshold is invalid",
            )
        # A typed point holds every configured constraint's threshold, in the
        # user's units (a fraction for a pct constraint), as the spec states it.
        spec[threshold_keys[0]] = point["thresholds"][name]

    return overrides


def _job_has_frontier_points(job: Mapping[str, Any]) -> bool:
    frontier_data = job.get("frontier_data")
    if not isinstance(frontier_data, dict):
        return False
    points = frontier_data.get("points")
    return isinstance(points, list) and len(points) > 0


def _cached_result_matches_frontier_selection(result: dict[str, Any], point_index: int) -> bool:
    selected_point = result.get("selected_frontier_point")
    return (
        isinstance(selected_point, int)
        and not isinstance(selected_point, bool)
        and (selected_point == point_index)
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
        baseline_objective=result["baseline_objective"],
        baseline_constraints=result["baseline_constraints"],
        converged=result["converged"],
        iterations=result.get("iterations"),
        cd_iterations=result.get("cd_iterations"),
        clamp_rate=result.get("clamp_rate"),
        factor_tables=result.get("factor_tables"),
        factor_dtypes=result.get("factor_dtypes"),
        # Ratebook only; a frontier point inherits its solve's collar.
        combined_factor_bounds=result.get(COMBINED_FACTOR_BOUNDS_KEY),
    )


def _frontier_point_result_for_job(job: Mapping[str, Any], point_index: int) -> dict[str, Any]:
    return _frontier_point_result_dict(
        {**job, "base_result": _base_result_for_frontier(job)},
        point_index,
    )


def _result_mode(job: Mapping[str, Any], result: dict[str, Any]) -> str:
    return str(job.get("config", {}).get("mode", result.get("mode", "online")))


def _cached_materialised_ratebook_frontier_result(
    job: Mapping[str, Any],
    point_index: int,
    expected_lambdas: dict[str, Any],
) -> dict[str, Any] | None:
    result = job.get("result")
    if (
        isinstance(result, dict)
        and _cached_result_matches_frontier_selection(result, point_index)
        and isinstance(result.get("factor_tables"), dict)
        and isinstance(result.get("factor_dtypes"), dict)
        and _lambda_mappings_match(result.get("lambdas"), expected_lambdas)
    ):
        return result
    return None


def _frontier_point_factor_tables_or_raise(
    job: Mapping[str, Any],
    point_index: int,
) -> dict[str, dict[str, float]]:
    """The factor tables price-contour kept for a retained ratebook frontier point."""
    tables = job.get(_FRONTIER_FACTOR_TABLES_KEY)
    points, _frontier_data = _frontier_points_or_raise(job)
    if not isinstance(tables, list) or len(tables) != len(points):
        raise HTTPException(
            status_code=500,
            detail="Job frontier factor tables are missing or do not match the frontier points",
        )
    point_tables = tables[point_index]
    if not isinstance(point_tables, dict):
        raise HTTPException(status_code=500, detail="Job frontier factor tables are invalid")
    return point_tables


def _materialised_ratebook_result_dict(
    result_dict: dict[str, Any],
    factor_tables: dict[str, dict[str, float]],
    factor_level_counts: dict[str, dict[str, int]],
    factor_level_order: dict[str, list[str]],
    factor_dtypes: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """A ratebook frontier point as a full result.

    ``result_dict`` is the point's frontier row summary. price-contour reports
    a row's totals as the canonical evaluation of that point's factor tables,
    so the row's totals, λ, convergence and clamp rate are the point's, and the
    tables are attached as they are, with no re-solve.
    """
    materialised = dict(result_dict)
    materialised.update(
        {
            "cd_iterations": result_dict["iterations"],
            "factor_tables": _serialise_ratebook_factor_tables(
                factor_tables,
                factor_level_counts,
                factor_level_order,
                factor_dtypes,
            ),
            "factor_dtypes": factor_dtypes,
            "history": None,
            # The trace is the solve's coordinate descent, not this point's.
            "ratebook_cd_trace": None,
        }
    )
    if materialised["converged"]:
        materialised.pop("warning", None)
    else:
        materialised["warning"] = NON_CONVERGED_WARNING
    return materialised


def _frontier_point_mlflow_summary(
    job: Mapping[str, Any],
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


def _artifact_handles_or_raise(job: Mapping[str, Any]) -> dict[str, Any]:
    artifact_handles = job.get("artifact_handles", {})
    if not isinstance(artifact_handles, dict):
        raise HTTPException(status_code=500, detail="Job artifact handles are invalid")
    return artifact_handles


def _invalidate_frontier_apply_artifact_handles(
    job: Mapping[str, Any],
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


@dataclass(frozen=True, slots=True)
class PointApplyTicket:
    """One request for a frontier point's apply artifact: retained, or queued to materialise."""

    point_index: int
    generation: int
    handle_key: str
    from_artifact: bool
    subscription: FlightSubscription[None] | None

    def wait(self, cancellation_token: ExecutionCancellationToken | None = None) -> None:
        """Wait for the point's artifact; a cancelled token detaches only this request."""
        if self.subscription is None:
            return
        try:
            self.subscription.wait(cancellation_token, operation=_POINT_APPLY_OPERATION)
        except FlightReplacedError:
            raise HTTPException(
                status_code=409, detail=FRONTIER_POINT_APPLY_REPLACED_DETAIL
            ) from None


class OptimiserFrontierService:
    """Frontier sweeps, point selection and point artifacts for one job store."""

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._lifecycle = JobLifecycle(store)
        self.sweeps = CancellableJobRegistry()
        self._locks_guard = threading.Lock()
        self._parent_locks: dict[str, threading.RLock] = {}
        # One point apply per job at a time; the latest other request waits.
        self._point_applies: LatestWinsQueue[str, tuple[int, int], None] = LatestWinsQueue()

    def parent_lock(self, parent_job_id: str) -> threading.RLock:
        """The lock every frontier state change for *parent_job_id* holds."""
        with self._locks_guard:
            lock = self._parent_locks.get(parent_job_id)
            if lock is None:
                lock = self._parent_locks[parent_job_id] = threading.RLock()
            return lock

    def _touch_heavy_objects_or_raise(
        self,
        job_id: str,
        *,
        required_keys: tuple[str, ...],
        detail: str,
    ) -> None:
        """Reserve completed-job heavy runtime state before using it."""
        if not self._store.touch_heavy_objects(job_id, required_keys=required_keys):
            raise HTTPException(status_code=400, detail=detail)

    def _require_sweep_job(self, job_id: str) -> JobSnapshot:
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _FRONTIER_RECOMPUTE_JOB_TYPE:
            raise HTTPException(status_code=404, detail=f"Frontier job '{job_id}' not found")
        return job

    def start_sweep(self, body: OptimiserFrontierRequest) -> str:
        """Validate a sweep synchronously, then run it on a background thread.

        Job and runtime availability, range resolution and the compute-budget
        cap are checked here, so their contract errors are this request's 4xx.
        Returns the pollable sweep job id.
        """
        job: Mapping[str, Any] = self._store.require_completed_job(body.job_id)
        mode = _job_mode(job)
        missing_runtime_detail = (
            "Solver and quote grid are not available for this job. "
            "Re-run the solve to compute a new frontier."
        )
        if mode == "ratebook":
            (
                job,
                solver,
                quote_grid,
                ratebook_factors,
                factor_columns,
            ) = self.ratebook_runtime_state_or_raise(body.job_id)
        else:
            self._touch_heavy_objects_or_raise(
                body.job_id,
                required_keys=("solver", "quote_grid"),
                detail=missing_runtime_detail,
            )
            job = self._store.require_completed_job(body.job_id)
            solver = job.get("solver")
            quote_grid = job.get("quote_grid")
            ratebook_factors = None
            factor_columns = None
            if solver is None or quote_grid is None:
                raise HTTPException(status_code=400, detail=missing_runtime_detail)

        base_result = _base_result_for_frontier_recompute(job)
        ranges = _frontier_ranges_for_request(body, job)
        kinds = _job_constraint_kinds(job)
        try:
            enforce_frontier_compute_budget(
                n_points_per_dim=body.n_points_per_dim,
                n_constraints=len(ranges),
            )
        except FrontierComputeBudgetExceededError as exc:
            # 422: the request is well-formed but its projected solver
            # workload exceeds the library cap; the message names both the
            # projection and the cap.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        with self.parent_lock(body.job_id):
            if self._has_running_sweep(body.job_id):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A frontier computation is already running for this job. "
                        "Wait for it to finish before starting another sweep."
                    ),
                )
            start_time = time.monotonic()
            initial_job: _FrontierRecomputeRunningJob = {
                "status": "running",
                "job_type": _FRONTIER_RECOMPUTE_JOB_TYPE,
                "progress": 0.0,
                "message": "Computing efficient frontier",
                "parent_job_id": body.job_id,
                "start_time": start_time,
                "timeout": _solve_timeout_from_config(job.get("config", {})),
            }
            frontier_job_id = self._store.create_job(initial_job)
            self.sweeps.register_latest(
                (_FRONTIER_RECOMPUTE_JOB_TYPE, body.job_id),
                frontier_job_id,
            )

        def _frontier_background() -> None:
            self._run_sweep(
                frontier_job_id=frontier_job_id,
                parent_job_id=body.job_id,
                solver=solver,
                quote_grid=quote_grid,
                mode=mode,
                ratebook_factors=ratebook_factors,
                factor_columns=factor_columns,
                ranges=ranges,
                constraint_kinds=kinds,
                n_points_per_dim=body.n_points_per_dim,
                initial_lambdas=base_result["lambdas"],
                base_result=base_result,
                start_time=start_time,
            )

        thread = threading.Thread(target=_frontier_background, daemon=True)
        try:
            thread.start()
        except Exception as exc:
            logger.error(
                "frontier_worker_start_failed",
                error=str(exc),
                job_id=body.job_id,
                frontier_job_id=frontier_job_id,
                exc_info=True,
            )
            with self.parent_lock(body.job_id):
                self._lifecycle.transition(
                    frontier_job_id,
                    to="error",
                    message=f"Failed to start frontier worker: {exc}",
                    elapsed_seconds=time.monotonic() - start_time,
                )
                self.sweeps.release(frontier_job_id)
            raise HTTPException(
                status_code=500,
                detail="Frontier worker failed to start. Check the server logs for details.",
            ) from exc
        return frontier_job_id

    def sweep_status(self, job_id: str) -> JobSnapshot:
        """The sweep job, after enforcing its timeout on this poll."""
        parent_job_id = str(self._require_sweep_job(job_id)["parent_job_id"])
        with self.parent_lock(parent_job_id):
            job = self._store.require_job(job_id)
            if job.get("status") == "running":
                start = job.get("start_time")
                timeout = job.get("timeout")
                if (
                    isinstance(start, int | float)
                    and not isinstance(start, bool)
                    and isinstance(timeout, int | float)
                    and not isinstance(timeout, bool)
                    and timeout > 0
                    and (time.monotonic() - start) > timeout
                ):
                    self.sweeps.cancel(job_id, reason="timed_out")
                    updated_job = self._lifecycle.transition(
                        job_id,
                        to="timed_out",
                        message=(
                            f"Frontier timed out after {timeout}s. "
                            "Reduce the sweep resolution or increase the solve timeout."
                        ),
                        elapsed_seconds=time.monotonic() - start,
                    )
                    job = (
                        updated_job if updated_job is not None else self._store.require_job(job_id)
                    )
            return job

    def cancel_sweep(self, job_id: str) -> JobSnapshot:
        """Cancel a sweep so it can never publish late."""
        parent_job_id = str(self._require_sweep_job(job_id)["parent_job_id"])
        with self.parent_lock(parent_job_id):
            job = self._store.require_job(job_id)
            if job.get("status") == "running":
                self.sweeps.cancel(job_id, reason="cancelled")
                start = job.get("start_time")
                elapsed_seconds = job.get("elapsed_seconds", 0.0)
                if isinstance(start, int | float) and not isinstance(start, bool):
                    elapsed_seconds = time.monotonic() - start
                updated_job = self._lifecycle.transition(
                    job_id,
                    to="cancelled",
                    message="Frontier cancelled",
                    elapsed_seconds=elapsed_seconds,
                )
                job = updated_job if updated_job is not None else self._store.require_job(job_id)
            return job

    def select_point(self, body: OptimiserFrontierSelectRequest) -> dict[str, Any]:
        """Select a frontier summary point without re-solving; returns the result dict."""
        job = self._store.require_completed_job(body.job_id)

        if (
            body.point_index is not None
            and job.get("selected_frontier_point") == body.point_index
            and isinstance(job.get("result"), dict)
            and _cached_result_matches_frontier_selection(job["result"], body.point_index)
            and not _job_has_frontier_points(job)
        ):
            return dict(job["result"])

        existing_base_result = job.get("base_result")
        base_result = (
            existing_base_result
            if isinstance(existing_base_result, dict)
            else dict(job.get("result", {}))
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
            if (
                body.include_ratebook_tables
                and _result_mode({**job, "base_result": base_result}, result_dict) == "ratebook"
            ):
                _materialised_job, materialised_result = self.materialise_ratebook_point(
                    body.job_id,
                    selected_point,
                )
                self._store.clear_result_data(body.job_id, keys=("solve_result",))
                return materialised_result

        updated_job = self._store.atomic_update(
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
            self._store.clear_result_data(body.job_id, keys=("solve_result",))
        return result_dict

    def ratebook_runtime_state_or_raise(
        self,
        job_id: str,
    ) -> tuple[JobSnapshot, Any, Any, Any, list[list[str]]]:
        if not self._store.touch_heavy_objects(
            job_id,
            required_keys=("solver", "quote_grid"),
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Ratebook runtime state is not available for this job. Re-run the "
                    "solve to materialise this frontier point."
                ),
            )

        job = self._store.require_completed_job(job_id)
        solver = job.get("solver")
        quote_grid = job.get("quote_grid")
        factor_columns = job.get("factor_columns_valid")
        if solver is None or quote_grid is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Ratebook runtime state is not available for this job. Re-run the "
                    "solve to materialise this frontier point."
                ),
            )
        factor_contexts = job.get("ratebook_factor_contexts")
        artifact_handles = _artifact_handles_or_raise(job)
        factors_handle = artifact_handles.get(_RATEBOOK_FACTORS_HANDLE_KEY)
        has_factor_source = factor_contexts is not None or isinstance(factors_handle, dict)
        if factor_columns is None and not has_factor_source:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Ratebook runtime state is not available for this job. Re-run the "
                    "solve to materialise this frontier point."
                ),
            )
        if not isinstance(factor_columns, list) or not all(
            isinstance(group, list) and all(isinstance(col, str) for col in group)
            for group in factor_columns
        ):
            raise HTTPException(
                status_code=500, detail="Ratebook factor column metadata is invalid"
            )
        if factor_contexts is None:
            if not isinstance(factors_handle, dict):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Ratebook runtime state is not available for this job. Re-run the "
                        "solve to materialise this frontier point."
                    ),
                )
            factor_contexts = _build_ratebook_factor_contexts(
                factors_handle,
                quote_grid,
                job.get("config", {}),
                factor_columns,
            )
        return job, solver, quote_grid, factor_contexts, factor_columns

    def materialise_ratebook_point(
        self,
        job_id: str,
        point_index: int,
    ) -> tuple[Mapping[str, Any], dict[str, Any]]:
        """Attach a ratebook frontier point's factor tables to its row summary.

        Needs no solver or grid: the frontier kept each point's tables, and
        the row's totals are the canonical evaluation of exactly those tables.
        The row, its tables and the commit all come from one snapshot under the
        parent lock that a frontier recompute publishes under, so a recompute
        can never pair one point's totals with another point's tables.
        """
        with self.parent_lock(job_id):
            return self._materialise_ratebook_point_locked(job_id, point_index)

    def _materialise_ratebook_point_locked(
        self,
        job_id: str,
        point_index: int,
    ) -> tuple[Mapping[str, Any], dict[str, Any]]:
        job = self._store.require_completed_job(job_id)
        result_dict = _frontier_point_result_for_job(job, point_index)
        cached_result = _cached_materialised_ratebook_frontier_result(
            job,
            point_index,
            result_dict["lambdas"],
        )
        if cached_result is not None:
            return job, cached_result

        factor_tables = _frontier_point_factor_tables_or_raise(job, point_index)
        base_result = _base_result_for_frontier(job)
        factor_columns = job.get("factor_columns_valid")
        if not isinstance(factor_columns, list) or not all(
            isinstance(group, list) and all(isinstance(col, str) for col in group)
            for group in factor_columns
        ):
            raise HTTPException(
                status_code=500, detail="Ratebook factor column metadata is invalid"
            )
        factor_level_counts = job.get("factor_level_counts")
        if not isinstance(factor_level_counts, dict):
            artifact_handles = _artifact_handles_or_raise(job)
            factors_handle = artifact_handles.get(_RATEBOOK_FACTORS_HANDLE_KEY)
            if not isinstance(factors_handle, dict):
                raise HTTPException(status_code=500, detail="Ratebook factor artifact is missing")
            factor_level_counts = _ratebook_factor_level_counts_from_artifact(
                factors_handle,
                factor_columns,
            )
        factor_level_order = job.get(_RATEBOOK_FACTOR_LEVEL_ORDER_KEY) or {}
        factor_dtypes = job.get("factor_dtypes")
        if not isinstance(factor_dtypes, dict):
            result_with_dtypes = job.get("result")
            if isinstance(result_with_dtypes, dict):
                factor_dtypes = result_with_dtypes.get("factor_dtypes")
        if not isinstance(factor_dtypes, dict):
            artifact_handles = _artifact_handles_or_raise(job)
            factors_handle = artifact_handles.get(_RATEBOOK_FACTORS_HANDLE_KEY)
            if not isinstance(factors_handle, dict):
                raise HTTPException(
                    status_code=500,
                    detail="Ratebook factor dtype metadata is missing",
                )
            factor_dtypes = _ratebook_factor_dtypes_from_artifact(
                factors_handle,
                factor_columns,
            )
        materialised = _materialised_ratebook_result_dict(
            result_dict,
            factor_tables,
            factor_level_counts,
            factor_level_order,
            factor_dtypes,
        )
        updated_job = self._store.atomic_update(
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
        return updated_job, materialised

    def solve_result_for_selected_point(
        self,
        job_id: str,
        job: Mapping[str, Any],
        point_index: int,
    ) -> tuple[Mapping[str, Any], dict[str, Any], SolveResultLike]:
        selected_result = _frontier_point_result_for_job(job, point_index)
        if _result_mode(job, selected_result) == "ratebook":
            updated_job, selected_result = self.materialise_ratebook_point(job_id, point_index)
            return updated_job, selected_result, _summary_solve_result(selected_result)
        return job, selected_result, _summary_solve_result(selected_result)

    def request_point_apply(self, job_id: str, point_index: int) -> PointApplyTicket:
        """The per-quote apply result of one frontier point, retained or queued for materialising.

        Online-mode only: ratebook jobs are rejected (see
        ``_RATEBOOK_APPLY_DETAIL_UNSUPPORTED``). A retained artifact answers at
        once. Otherwise the point needs the solve's live quote grid (a named 410
        without it) and joins the job's latest-wins queue: at most one
        ``apply_from_grid`` runs per job, since it cannot be interrupted.
        """
        with self.parent_lock(job_id):
            job = self._store.require_completed_job(job_id)
            # Running ``apply_from_grid`` for a ratebook job would silently
            # discard the solved factor tables.
            _reject_ratebook_apply_detail(job)
            result_dict = _frontier_point_result_for_job(job, point_index)
            generation = _frontier_generation_or_raise(job)
            handle_key = _frontier_apply_handle_key(point_index)
            existing_handle = _artifact_handles_or_raise(job).get(handle_key)
            if existing_handle is not None:
                if not isinstance(existing_handle, dict):
                    raise HTTPException(
                        status_code=500,
                        detail="Job frontier apply artifact handle is invalid",
                    )
                return PointApplyTicket(point_index, generation, handle_key, True, None)
        if not self._store.touch_heavy_objects(job_id, required_keys=("quote_grid",)):
            raise HTTPException(
                status_code=410,
                detail=frontier_point_unavailable_detail(point_index, grid_expired=True),
            )
        lambdas = dict(result_dict["lambdas"])
        subscription = self._point_applies.subscribe(
            job_id,
            (generation, point_index),
            lambda _token: self._materialise_point(job_id, point_index, generation, lambdas),
        )
        return PointApplyTicket(point_index, generation, handle_key, False, subscription)

    def select_applied_point(
        self, job_id: str, point_index: int, generation: int
    ) -> dict[str, Any]:
        """Record *point_index* as the job's selected point; 409 if the frontier moved on."""
        with self.parent_lock(job_id):
            job = self._store.require_completed_job(job_id)
            if _frontier_generation_or_raise(job) != generation:
                raise HTTPException(status_code=409, detail=_FRONTIER_CHANGED_DETAIL)
            base_result = _base_result_for_frontier(job)
            result_dict = _frontier_point_result_dict(
                {**job, "base_result": base_result}, point_index
            )
            updated_job = self._store.atomic_update(
                job_id,
                {
                    "base_result": base_result,
                    "selected_frontier_point": point_index,
                    "result": result_dict,
                },
                expected_status="completed",
            )
            if updated_job is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Optimiser job state changed while loading the frontier point. "
                        "Re-run the solve to materialise it again."
                    ),
                )
            return result_dict

    def _point_apply_estimate(self, job_id: str) -> int:
        """A point's apply peak, from the as-solved apply frame it shares its shape with."""
        import polars as pl

        with lease_apply_frame(
            self._store,
            job_id,
            _APPLY_RESULT_HANDLE_KEY,
            unavailable_detail=APPLY_RESULT_UNAVAILABLE_DETAIL,
        ) as as_solved:
            row_count = int(as_solved.select(pl.len()).collect().item())
            sample = as_solved.head(512).collect()
        return estimate_point_apply_peak_bytes(
            row_count=row_count, row_width_bytes=decoded_frame_row_width_bytes(sample)
        )

    def _materialise_point(
        self,
        job_id: str,
        point_index: int,
        generation: int,
        lambdas: dict[str, Any],
    ) -> None:
        """Apply one point's λ to the live grid and publish its artifact (a queue run)."""
        handle_key = _frontier_apply_handle_key(point_index)
        with self.parent_lock(job_id):
            job = self._store.require_completed_job(job_id)
            if _frontier_generation_or_raise(job) != generation:
                raise HTTPException(status_code=409, detail=_FRONTIER_CHANGED_DETAIL)
            if _artifact_handles_or_raise(job).get(handle_key) is not None:
                return
            quote_grid = job.get("quote_grid")
            if quote_grid is None:
                raise HTTPException(
                    status_code=410,
                    detail=frontier_point_unavailable_detail(point_index, grid_expired=True),
                )
            constraints = job.get("config", {}).get("constraints", {})

        context: ExecutionContext | None = None
        new_handle: dict[str, Any] | None = None
        owns_new_handle = False
        try:
            # Admitted on its own estimate before the uninterruptible apply starts.
            context = create_admitted_execution_context(
                operation=_POINT_APPLY_OPERATION,
                profile=ExecutionProfile.EXPLORE_ANALYSIS,
                job_id=job_id,
                estimate=WorkEstimate(
                    estimated_bytes=self._point_apply_estimate(job_id),
                    subject=f"The frontier point apply (point {point_index})",
                    remedy="Raise HAUTE_EXPLORE_MEMORY_LIMIT_MB to inspect this point.",
                ),
            )
            apply_result = price_contour().apply_from_grid(
                quote_grid, lambdas=lambdas, constraints=constraints
            )
            _dataframe_or_raise(apply_result, context="Apply result")
            new_handle = _persist_apply_result_artifact(apply_result)
            owns_new_handle = True
            del apply_result
            owns_new_handle = self._publish_point_handle(job_id, handle_key, generation, new_handle)
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            raise HTTPException(status_code=507, detail=exc.to_payload()) from None
        finally:
            if context is not None:
                context.release_admission()
            # A request-created artifact the job did not adopt is removed on any
            # outcome; a failure itself propagates to every waiting caller.
            if owns_new_handle and new_handle is not None:
                _cleanup_orphan_apply_artifact(new_handle, job_id=job_id)

    def _publish_point_handle(
        self,
        job_id: str,
        handle_key: str,
        generation: int,
        new_handle: dict[str, Any],
    ) -> bool:
        """Adopt *new_handle* into the job; returns whether the caller still owns it."""
        with self.parent_lock(job_id):
            latest_job = self._store.require_completed_job(job_id)
            if _frontier_generation_or_raise(latest_job) != generation:
                raise HTTPException(status_code=409, detail=_FRONTIER_CHANGED_DETAIL)
            latest_handles = _artifact_handles_or_raise(latest_job)
            if latest_handles.get(handle_key) is not None:
                return True
            updated_handles, evicted_handles = _with_bounded_frontier_apply_handle(
                latest_handles, handle_key, new_handle
            )
            updated_job = self._store.atomic_update_if_heavy_present(
                job_id,
                {"artifact_handles": updated_handles},
                required_keys=("quote_grid",),
                expected_status="completed",
            )
            if updated_job is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Optimiser runtime state changed while materialising the frontier "
                        "point. Re-run the solve to materialise it again."
                    ),
                )
        self._store.release_detached_artifact_handles(job_id, evicted_handles)
        return False

    def _raise_if_sweep_stopped(self, frontier_job_id: str) -> None:
        reason = self.sweeps.cancellation_reason(frontier_job_id)
        if reason is not None:
            raise BackgroundJobStoppedError(frontier_job_id, reason)
        job = self._store.require_job(frontier_job_id)
        if job.get("status") != "running":
            terminal_reason = job.get("terminal_reason")
            reason_text = (
                terminal_reason if isinstance(terminal_reason, str) else str(job["status"])
            )
            raise BackgroundJobStoppedError(frontier_job_id, reason_text)

    def _has_running_sweep(self, parent_job_id: str) -> bool:
        def _matches(job: JobSnapshot) -> bool:
            return (
                job.get("status") == "running"
                and job.get(_JOB_TYPE_KEY) == _FRONTIER_RECOMPUTE_JOB_TYPE
                and job.get("parent_job_id") == parent_job_id
            )

        return self._store.has_job_matching(_matches)

    def _run_sweep(
        self,
        *,
        frontier_job_id: str,
        parent_job_id: str,
        solver: Any,
        quote_grid: Any,
        mode: str,
        ratebook_factors: Any,
        factor_columns: Any,
        ranges: dict[str, tuple[float, float]],
        constraint_kinds: dict[str, ConstraintKind],
        n_points_per_dim: int,
        initial_lambdas: Any,
        base_result: dict[str, Any],
        start_time: float,
    ) -> None:
        """Run the frontier sweep off the request thread and record the outcome.

        The sweep is hundreds of sequential re-solves at 2-3 constraints; it must
        never block a FastAPI worker.  On success the parent solve job is updated
        exactly as the old inline route did, and the sweep job completes with the
        frontier payload as its result.
        """
        try:
            with solver_worker_context():
                self._raise_if_sweep_stopped(frontier_job_id)
                frontier_result = _compute_frontier(
                    solver,
                    quote_grid,
                    mode=mode,
                    ratebook_factors=ratebook_factors,
                    factor_columns=factor_columns,
                    threshold_ranges=ranges,
                    n_points_per_dim=n_points_per_dim,
                    initial_lambdas=initial_lambdas,
                    check_cancelled=lambda: self._raise_if_sweep_stopped(frontier_job_id),
                )
                self._raise_if_sweep_stopped(frontier_job_id)
            with self.parent_lock(parent_job_id):
                self._raise_if_sweep_stopped(frontier_job_id)
                latest_job = self._store.require_completed_job(parent_job_id)
                next_frontier_generation = _frontier_generation_or_raise(latest_job) + 1
                # The payload and the reset result report the generation this
                # update publishes, read under the same lock that increments it.
                response = OptimiserFrontierResponse(
                    **limited_frontier_payload(
                        frontier_result.points,
                        mode=mode,
                        constraint_kinds=constraint_kinds,
                        swept_axes=list(ranges),
                        frontier_generation=next_frontier_generation,
                    )
                )
                frontier_dict = response.model_dump(exclude={"job_id"})
                result_dict = dict(base_result)
                result_dict["frontier"] = frontier_dict
                result_dict["frontier_generation"] = next_frontier_generation
                # A frontier now exists: the solve-time frontier failure is history.
                result_dict.pop("frontier_error", None)
                result_dict["diagnostics_errors"] = [
                    error
                    for error in result_dict["diagnostics_errors"]
                    if error["diagnostic"] != "frontier"
                ]
                result_dict.pop("selected_frontier_point", None)
                retained_handles, invalidated_handles = _invalidate_frontier_apply_artifact_handles(
                    latest_job
                )
                updated_job = self._store.atomic_update(
                    parent_job_id,
                    {
                        "result": result_dict,
                        "base_result": dict(result_dict),
                        "frontier_data": frontier_dict,
                        _FRONTIER_FACTOR_TABLES_KEY: frontier_point_factor_tables(
                            frontier_result,
                            mode=mode,
                            points_returned=frontier_dict["points_returned"],
                        ),
                        _FRONTIER_GENERATION_KEY: next_frontier_generation,
                        # The old points' reports describe points that no longer exist.
                        ADJUSTMENT_REPORTS_KEY: {},
                        "selected_frontier_point": None,
                        "artifact_handles": retained_handles,
                    },
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
                self._store.release_detached_artifact_handles(parent_job_id, invalidated_handles)
                completed_job = self._lifecycle.transition(
                    frontier_job_id,
                    to="completed",
                    message="Frontier computed",
                    fields={"result": frontier_dict},
                    elapsed_seconds=time.monotonic() - start_time,
                )
                if completed_job is None:
                    self._raise_if_sweep_stopped(frontier_job_id)
                    raise RuntimeError("Frontier completion could not be recorded")
        except BackgroundJobStoppedError:
            return
        except HTTPException as exc:
            reason: TerminalReason = (
                "contract_error" if exc.status_code in (400, 409, 422) else "error"
            )
            with self.parent_lock(parent_job_id):
                self._lifecycle.transition(
                    frontier_job_id,
                    to=reason,
                    message=str(exc.detail),
                    fields={"http_status_code": exc.status_code, "error_detail": exc.detail},
                    elapsed_seconds=time.monotonic() - start_time,
                )
        except Exception as exc:
            logger.error(
                "frontier_failed",
                error=str(exc),
                job_id=parent_job_id,
                frontier_job_id=frontier_job_id,
                exc_info=True,
            )
            with self.parent_lock(parent_job_id):
                self._lifecycle.transition(
                    frontier_job_id,
                    to="error",
                    message=_INTERNAL_ERROR_DETAIL,
                    elapsed_seconds=time.monotonic() - start_time,
                )
        finally:
            self.sweeps.release(frontier_job_id)
