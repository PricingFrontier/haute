"""The optimiser's frontier domain: point selection and point artifacts.

A job's frontier is computed once, with its solve, and never replaced.
``OptimiserFrontierService`` owns frontier-point selection, the ratebook point
materialisation and the retained frontier-point apply artifacts. Every state
change for one parent solve holds that parent's lock, so frontier work on one
solve never waits for another's parquet reads or deletions. The pure point
helpers live here too. FastAPI response assembly stays in ``routes/optimiser.py``.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Protocol, cast

from haute._dedicated_workers import DedicatedWorkerDeadError
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
from haute._interactive_workers import InteractiveWorkerError
from haute._logging import get_logger
from haute._polars_utils import streaming_collect
from haute._price_contour import price_contour
from haute._ram_estimate import decoded_frame_row_width_bytes, estimate_point_apply_peak_bytes
from haute._ratebook_collar import COMBINED_FACTOR_BOUNDS_KEY
from haute._types import SolveResultLike
from haute.errors import (
    ConflictError,
    GoneError,
    InsufficientMemoryError,
    InvalidRequestError,
    RequestRefusedError,
    ServiceFailedError,
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
from haute.routes._job_store import JobStore
from haute.routes._optimiser_artifacts import (
    _APPLY_RESULT_HANDLE_KEY,
    _RATEBOOK_FACTORS_HANDLE_KEY,
    APPLY_RESULT_UNAVAILABLE_DETAIL,
    _cleanup_apply_result_artifact,
    _new_apply_artifact_directory,
    _persist_apply_frame_artifact,
    _remove_apply_artifact_directory,
    frontier_point_unavailable_detail,
)
from haute.routes._optimiser_outcomes import lease_apply_frame
from haute.routes._optimiser_session import (
    RUNTIME_UNAVAILABLE_KEY,
    SESSION_KEY,
    SESSION_RUNTIME,
    SessionCommandError,
    SolverSession,
    runtime_mode,
)
from haute.routes._optimiser_session_worker import (
    SessionPointApplyRequest,
    apply_point,
)
from haute.routes._optimiser_solver import (
    _FRONTIER_FACTOR_TABLES_KEY,
    _RATEBOOK_FACTOR_LEVEL_ORDER_KEY,
    _ratebook_factor_dtypes_from_artifact,
    _ratebook_factor_level_counts_from_artifact,
    _serialise_ratebook_factor_tables,
)
from haute.routes._shared_flights import (
    FlightReplacedError,
    FlightSubscription,
    LatestWinsQueue,
)
from haute.schemas import OptimiserFrontierSelectRequest

logger = get_logger(component="server.optimiser")

_FRONTIER_APPLY_HANDLE_PREFIX = "frontier_apply_result:"
_MAX_FRONTIER_APPLY_ARTIFACTS = 8
_POINT_APPLY_OPERATION = "optimiser_point_apply"
FRONTIER_POINT_APPLY_REPLACED_DETAIL = {
    "error_code": "frontier_point_apply_replaced",
    "message": (
        "Another frontier point was requested while this one waited for the point being "
        "applied, so this request was dropped. Select the point again to inspect it."
    ),
}
_CONSTRAINT_THRESHOLD_KEYS = tuple(CONSTRAINT_THRESHOLD_KINDS)


# The heavy state a ratebook point's per-quote evaluation reads; it is kept and
# slimmed together with the quote grid.
_RATEBOOK_POINT_RUNTIME_KEYS = ("solver", "quote_grid", "ratebook_factor_contexts")


def _job_mode(job: Mapping[str, Any]) -> str:
    """Resolve the optimiser mode for a completed job (config wins)."""
    result = job.get("result")
    result_mode = result.get("mode", "online") if isinstance(result, dict) else "online"
    return str(job.get("config", {}).get("mode", result_mode))


def _point_runtime_keys(job: Mapping[str, Any]) -> tuple[str, ...]:
    """The heavy state materialising one of *job*'s frontier points needs."""
    if runtime_mode(job) == SESSION_RUNTIME:
        return (SESSION_KEY,)
    return _RATEBOOK_POINT_RUNTIME_KEYS if _job_mode(job) == "ratebook" else ("quote_grid",)


def _runtime_gone_detail(job: Mapping[str, Any], point_index: int) -> dict[str, str]:
    """The 410 for a point whose solve runtime is gone, naming why when the job knows."""
    unavailable = job.get(RUNTIME_UNAVAILABLE_KEY)
    return frontier_point_unavailable_detail(
        point_index,
        grid_expired=True,
        runtime_unavailable=unavailable if isinstance(unavailable, Mapping) else None,
    )


def _require_evaluation_is_the_point(
    evaluation: Any, point: Mapping[str, Any], point_index: int
) -> None:
    """A ratebook point's evaluation must reproduce its frontier row exactly (price-contour
    0.5 evaluates each row from these very tables); anything else is a different point."""
    evaluated = (float(evaluation.total_objective), dict(evaluation.total_constraints))
    row = (float(point["total_objective"]), dict(point["totals"]))
    if evaluated != row:
        raise RuntimeError(
            f"Frontier point {point_index}'s factor tables evaluate to objective "
            f"{evaluated[0]!r} and constraints {evaluated[1]!r}, not its frontier row's "
            f"{row[0]!r} and {row[1]!r}; the point's per-quote choices cannot be described."
        )


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
        raise ServiceFailedError(f"{context} is missing a 'dataframe' attribute")
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


def _frontier_apply_handle_key(point_index: int) -> str:
    return f"{_FRONTIER_APPLY_HANDLE_PREFIX}{point_index}"


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
            raise ServiceFailedError("Job frontier apply artifact handle is invalid")
        evicted_handles.append(stale_handle)
    return updated_handles, evicted_handles


def _frontier_points_or_raise(
    job: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frontier_data = job.get("frontier_data")
    if not isinstance(frontier_data, dict) or not frontier_data.get("points"):
        raise InvalidRequestError("Job has no frontier data")
    points = frontier_data["points"]
    if not isinstance(points, list) or not all(isinstance(point, dict) for point in points):
        raise ServiceFailedError("Job frontier data is invalid")
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
        raise InvalidRequestError(f"Point index {point_index} out of range [0, {total_points})")
    if point_index >= len(points):
        raise InvalidRequestError(
            f"Point index {point_index} is not available in the capped frontier "
            f"payload. Only {points_returned} of {total_points} points are retained "
            f"(limit {points_limit}). Narrow the node's frontier ranges or lower its "
            "points per dimension, then solve again."
        )
    return points[point_index], frontier_data


def _base_result_for_frontier(job: Mapping[str, Any]) -> dict[str, Any]:
    base_result = job.get("base_result")
    if isinstance(base_result, dict):
        return base_result
    result = job.get("result")
    if isinstance(result, dict):
        return result
    raise ServiceFailedError("Job summary is missing")


def _job_constraint_kinds(job: Mapping[str, Any]) -> dict[str, ConstraintKind]:
    """Every configured constraint's kind; a malformed config is a 500."""
    try:
        return constraint_kinds(job.get("config", {}).get("constraints") or {})
    except ValueError as exc:
        raise ServiceFailedError(f"Job optimiser constraints are invalid: {exc}") from exc


def _frontier_point_result_dict(job: Mapping[str, Any], point_index: int) -> dict[str, Any]:
    point, frontier_data = _frontier_point_or_raise(job, point_index)
    kinds = _job_constraint_kinds(job)
    # The stored frontier summarised every configured constraint, swept or not.
    if frontier_data.get("constraint_names") != list(kinds):
        raise ServiceFailedError(
            "Job frontier constraint names do not match the configured constraints"
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
        raise ServiceFailedError("Job optimiser constraints are invalid")

    overrides: dict[str, dict[str, Any]] = {}
    for name, spec in raw_constraints.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise ServiceFailedError("Job optimiser constraints are invalid")
        overrides[name] = dict(spec)

    constraint_names = frontier_data.get("constraint_names", [])
    if not isinstance(constraint_names, list):
        raise ServiceFailedError("Job frontier constraint names are invalid")

    for name in constraint_names:
        if not isinstance(name, str):
            raise ServiceFailedError("Job frontier constraint names are invalid")
        spec = overrides.get(name)
        if spec is None:
            raise ServiceFailedError("Job frontier constraint is missing")
        threshold_keys = [key for key in _CONSTRAINT_THRESHOLD_KEYS if key in spec]
        if len(threshold_keys) != 1:
            raise ServiceFailedError("Job optimiser constraint threshold is invalid")
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
        raise ServiceFailedError(
            "Job frontier factor tables are missing or do not match the frontier points"
        )
    point_tables = tables[point_index]
    if not isinstance(point_tables, dict):
        raise ServiceFailedError("Job frontier factor tables are invalid")
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


# The anchor's scenario-value statistics (price-contour's ``summary``), by the
# frontier point column that reports each one.
_POINT_SCENARIO_VALUE_METRICS = {
    "scenario_value_mean": "sv_mean",
    "scenario_value_std": "sv_std",
    "scenario_value_p5": "sv_p5",
    "scenario_value_p25": "sv_p25",
    "scenario_value_p50": "sv_median",
    "scenario_value_p75": "sv_p75",
    "scenario_value_p95": "sv_p95",
}


def _frontier_point_mlflow_summary(
    job: Mapping[str, Any],
    result: dict[str, Any],
    point_index: int,
) -> dict[str, dict[str, Any]]:
    """A frontier point's MLflow summary, on price-contour's ``summary`` key scheme.

    The anchor logs ``solver.summary(result)``; a point logs the same metric
    names from its frontier row and summary, so two runs from one solve
    compare by name.
    """
    job_config = job.get("config", {})
    mode = _result_mode(job, result)
    point, _frontier_data = _frontier_point_or_raise(job, point_index)
    baseline_objective = float(result["baseline_objective"])
    total_objective = float(result["total_objective"])
    metrics: dict[str, float] = {
        "total_objective": total_objective,
        "baseline_objective": baseline_objective,
        "converged": 1.0 if result.get("converged") is True else 0.0,
    }
    if mode == "ratebook":
        metrics["cd_iterations"] = float(point["iterations"])
        metrics["clamp_rate"] = float(point["clamp_rate"])
    else:
        metrics["iterations"] = float(point["iterations"])
    if baseline_objective != 0:
        metrics["uplift_pct"] = (
            (total_objective - baseline_objective) / abs(baseline_objective) * 100
        )
    baselines = result["baseline_constraints"]
    for name, total in result["constraints"].items():
        metrics[f"constraint_{name}_total"] = float(total)
        metrics[f"constraint_{name}_baseline"] = float(baselines[name])
        if mode != "ratebook" and baselines[name] != 0:
            metrics[f"constraint_{name}_ratio"] = float(total) / float(baselines[name])
    for name, value in result["lambdas"].items():
        metrics[f"lambda_{name}"] = float(value)
    if mode != "ratebook":
        for metric, column in _POINT_SCENARIO_VALUE_METRICS.items():
            metrics[metric] = float(point[column])

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
        raise ServiceFailedError("Job artifact handles are invalid")
    return artifact_handles


@dataclass(frozen=True, slots=True)
class PointApplyTicket:
    """One request for a frontier point's apply artifact: retained, or queued to materialise."""

    point_index: int
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
            raise ConflictError(FRONTIER_POINT_APPLY_REPLACED_DETAIL) from None


class OptimiserFrontierService:
    """Frontier point selection and point artifacts for one job store."""

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._locks_guard = threading.Lock()
        self._parent_locks: dict[str, threading.RLock] = {}
        # One point apply per job at a time; the latest other request waits. A
        # running apply nobody waits for any more is cancelled (OPT-PC02).
        self._point_applies: LatestWinsQueue[str, int, None] = LatestWinsQueue(
            cancel_abandoned=True
        )

    def parent_lock(self, parent_job_id: str) -> threading.RLock:
        """The lock every frontier state change for *parent_job_id* holds."""
        with self._locks_guard:
            lock = self._parent_locks.get(parent_job_id)
            if lock is None:
                lock = self._parent_locks[parent_job_id] = threading.RLock()
            return lock

    def select_point(self, body: OptimiserFrontierSelectRequest) -> dict[str, Any]:
        """Select a frontier summary point without re-solving; returns the result dict."""
        with self.parent_lock(body.job_id):
            return self._select_point_locked(body)

    def _select_point_locked(self, body: OptimiserFrontierSelectRequest) -> dict[str, Any]:
        job = self._store.require_completed_job(body.job_id)
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
                _materialised_job, materialised_result = self._materialise_ratebook_point_locked(
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
            raise ConflictError(
                "Optimiser job state changed while selecting the frontier point. "
                "Re-run the solve to select a new point."
            )

        if selected_point is not None:
            self._store.clear_result_data(body.job_id, keys=("solve_result",))
        return result_dict

    def materialise_ratebook_point(
        self,
        job_id: str,
        point_index: int,
    ) -> tuple[Mapping[str, Any], dict[str, Any]]:
        """Attach a ratebook frontier point's factor tables to its row summary.

        Needs no solver or grid: the frontier kept each point's tables, and
        the row's totals are the canonical evaluation of exactly those tables.
        The row, its tables and the commit all come from one snapshot under the
        parent lock, so a concurrent selection never interleaves with it.
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
            raise ServiceFailedError("Ratebook factor column metadata is invalid")
        factor_level_counts = job.get("factor_level_counts")
        if not isinstance(factor_level_counts, dict):
            artifact_handles = _artifact_handles_or_raise(job)
            factors_handle = artifact_handles.get(_RATEBOOK_FACTORS_HANDLE_KEY)
            if not isinstance(factors_handle, dict):
                raise ServiceFailedError("Ratebook factor artifact is missing")
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
                raise ServiceFailedError("Ratebook factor dtype metadata is missing")
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
            raise ConflictError(
                "Optimiser job state changed while materialising the frontier point. "
                "Re-run the solve to materialise it again."
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

        A retained artifact answers at once. Otherwise the point needs the
        solve's live quote grid (a named 410 without it; a ratebook point also
        its solver and factor contexts, slimmed with the grid) and joins the
        job's latest-wins queue: at most one point apply runs per job, and one
        whose every request has left is cancelled.
        """
        with self.parent_lock(job_id):
            job = self._store.require_completed_job(job_id)
            # Validates the point before anything is queued.
            _frontier_point_result_for_job(job, point_index)
            runtime_keys = _point_runtime_keys(job)
            handle_key = _frontier_apply_handle_key(point_index)
            existing_handle = _artifact_handles_or_raise(job).get(handle_key)
            if existing_handle is not None:
                if not isinstance(existing_handle, dict):
                    raise ServiceFailedError("Job frontier apply artifact handle is invalid")
                return PointApplyTicket(point_index, handle_key, True, None)
        if not self._store.touch_heavy_objects(job_id, required_keys=runtime_keys):
            raise GoneError(_runtime_gone_detail(self._store.require_job(job_id), point_index))
        subscription = self._point_applies.subscribe(
            job_id,
            point_index,
            lambda token: self._materialise_point(job_id, point_index, token),
        )
        return PointApplyTicket(point_index, handle_key, False, subscription)

    def select_applied_point(self, job_id: str, point_index: int) -> dict[str, Any]:
        """Record *point_index* as the job's selected point.

        A ratebook point is recorded materialised, with its own factor tables.
        """
        with self.parent_lock(job_id):
            job = self._store.require_completed_job(job_id)
            if _job_mode(job) == "ratebook":
                return self._materialise_ratebook_point_locked(job_id, point_index)[1]
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
                raise ConflictError(
                    "Optimiser job state changed while loading the frontier point. "
                    "Re-run the solve to materialise it again."
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
            row_count = int(streaming_collect(as_solved.select(pl.len())).item())
            sample = streaming_collect(as_solved.head(512))
        return estimate_point_apply_peak_bytes(
            row_count=row_count, row_width_bytes=decoded_frame_row_width_bytes(sample)
        )

    def _point_frame_computation(
        self, job: Mapping[str, Any], point_index: int
    ) -> Callable[[Any], Any]:
        """How one point's per-quote frame is computed, from *job* read under the parent lock.

        Online, ``apply_from_grid`` with the point's λ; ratebook, price-contour's
        canonical evaluation of the tables the frontier kept for the point, which
        must reproduce the point's frontier row. The computation takes a
        price-contour ``CancelToken``, which also stops the lazily built frame.
        """
        runtime: dict[str, Any] = {}
        for key in _point_runtime_keys(job):
            value = job.get(key)
            if value is None:
                raise GoneError(frontier_point_unavailable_detail(point_index, grid_expired=True))
            runtime[key] = value
        quote_grid = runtime["quote_grid"]
        if _job_mode(job) == "ratebook":
            point, _frontier_data = _frontier_point_or_raise(job, point_index)
            tables = _frontier_point_factor_tables_or_raise(job, point_index)
            solver, contexts = runtime["solver"], runtime["ratebook_factor_contexts"]

            def evaluate(cancel: Any) -> Any:
                evaluation = solver.evaluate(quote_grid, contexts, tables, cancel=cancel)
                _require_evaluation_is_the_point(evaluation, point, point_index)
                return evaluation.quote_results

            return evaluate
        lambdas = dict(_frontier_point_result_for_job(job, point_index)["lambdas"])
        constraints = job.get("config", {}).get("constraints", {})

        def apply(cancel: Any) -> Any:
            apply_result = price_contour().apply_from_grid(
                quote_grid, lambdas=lambdas, constraints=constraints, cancel=cancel
            )
            return _dataframe_or_raise(apply_result, context="Apply result")

        return apply

    def _materialise_point(
        self,
        job_id: str,
        point_index: int,
        cancellation_token: ExecutionCancellationToken,
    ) -> None:
        """Compute one point's per-quote frame and publish its artifact (a queue run).

        The queue cancels *cancellation_token* once no request waits for the
        point. The cancel reaches price-contour through a native token, and the
        run checks it after admission, between computing and persisting the
        frame, and under the parent lock just before adopting the handle, which
        is the one commit point. A cancel that lands after that last check
        loses the race: the finished point is adopted and kept.
        """
        handle_key = _frontier_apply_handle_key(point_index)
        with self.parent_lock(job_id):
            job = self._store.require_completed_job(job_id)
            if _artifact_handles_or_raise(job).get(handle_key) is not None:
                return
            if runtime_mode(job) == SESSION_RUNTIME:
                session, request = self._session_point_apply(job, point_index)
            else:
                compute_frame = self._point_frame_computation(job, point_index)
        if runtime_mode(job) == SESSION_RUNTIME:
            self._materialise_point_in_session(
                job_id, point_index, handle_key, session, request, cancellation_token
            )
            return

        pc = price_contour()
        native_cancel = pc.CancelToken()
        cancellation_token.on_cancel(native_cancel.cancel)

        def throw_if_cancelled() -> None:
            cancellation_token.throw_if_cancelled(_POINT_APPLY_OPERATION, job_id=job_id)

        context: ExecutionContext | None = None
        new_handle: dict[str, Any] | None = None
        owns_new_handle = False
        try:
            # Admitted on its own estimate before the apply starts.
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
            throw_if_cancelled()
            library_cancelled = False
            try:
                frame = compute_frame(native_cancel)
            except pc.Cancelled:
                # Only haute's own cancel makes this a cancellation; a native
                # Cancelled without one is a library fault and propagates as is.
                if not cancellation_token.cancelled:
                    raise
                library_cancelled = True
            # The queue keeps a run's exception in its outcome, so a
            # cancellation is raised outside the except block (dropping the
            # library's traceback) and after the frame is released: neither
            # may outlive admission while the next point runs.
            if library_cancelled:
                throw_if_cancelled()
            try:
                throw_if_cancelled()
                new_handle = _persist_apply_frame_artifact(frame)
                owns_new_handle = True
            finally:
                del frame
            owns_new_handle = self._publish_point_handle(
                job_id, handle_key, new_handle, throw_if_cancelled
            )
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            raise InsufficientMemoryError(exc.to_payload()) from None
        finally:
            if context is not None:
                context.release_admission()
            # A request-created artifact the job did not adopt is removed on any
            # outcome; a failure itself propagates to every waiting caller.
            if owns_new_handle and new_handle is not None:
                _cleanup_orphan_apply_artifact(new_handle, job_id=job_id)

    def _session_point_apply(
        self, job: Mapping[str, Any], point_index: int
    ) -> tuple[SolverSession, SessionPointApplyRequest]:
        """The session and the command that materialise one point, from *job* read under lock."""
        session = job.get(SESSION_KEY)
        if not isinstance(session, SolverSession):
            raise GoneError(_runtime_gone_detail(job, point_index))
        constraints = dict(job.get("config", {}).get("constraints") or {})
        if _job_mode(job) == "ratebook":
            point, _frontier_data = _frontier_point_or_raise(job, point_index)
            request = SessionPointApplyRequest(
                session_id=session.job_id,
                point_index=point_index,
                lambdas=None,
                constraints=constraints,
                factor_tables=_frontier_point_factor_tables_or_raise(job, point_index),
                expected_point=dict(point),
                artifact_dir="",
            )
        else:
            request = SessionPointApplyRequest(
                session_id=session.job_id,
                point_index=point_index,
                lambdas=dict(_frontier_point_result_for_job(job, point_index)["lambdas"]),
                constraints=constraints,
                factor_tables=None,
                expected_point=None,
                artifact_dir="",
            )
        return session, request

    def _materialise_point_in_session(
        self,
        job_id: str,
        point_index: int,
        handle_key: str,
        session: SolverSession,
        request: SessionPointApplyRequest,
        cancellation_token: ExecutionCancellationToken,
    ) -> None:
        """Run one point's apply in the solve's session and adopt its artifact under the slot.

        The artifact is written into a directory created here and removed unless
        the job adopted it, so a discarded, cancelled or killed apply leaves
        nothing behind. The flight's *cancellation_token* reaches the running
        command through the session's worker (OPT-PC02), and adoption checks it
        under the parent lock, the same commit point as the in-process run.
        """
        artifact_dir = _new_apply_artifact_directory()
        request = dataclasses.replace(request, artifact_dir=str(artifact_dir))
        adopted = False

        def throw_if_cancelled() -> None:
            cancellation_token.throw_if_cancelled(_POINT_APPLY_OPERATION, job_id=job_id)

        def on_slot() -> None:
            throw_if_cancelled()
            with self.parent_lock(job_id):
                self._store.require_completed_job(job_id)

        def publish(handle: dict[str, Any]) -> None:
            nonlocal adopted
            adopted = not self._publish_point_handle(job_id, handle_key, handle, throw_if_cancelled)

        try:
            session.run_command(
                apply_point,
                request,
                operation=_POINT_APPLY_OPERATION,
                stage=f"applying frontier point {point_index}",
                publish=publish,
                cancellation_token=cancellation_token,
                on_slot=on_slot,
            )
        except SessionCommandError as exc:
            raise RequestRefusedError(exc.detail, status_code=exc.http_status_code) from None
        except DedicatedWorkerDeadError:
            raise GoneError(
                _runtime_gone_detail(self._store.require_job(job_id), point_index)
            ) from None
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            raise InsufficientMemoryError(exc.to_payload()) from None
        except InteractiveWorkerError as exc:
            logger.error(
                "frontier_point_session_failed",
                job_id=job_id,
                point_index=point_index,
                error=str(exc),
            )
            raise ServiceFailedError(_INTERNAL_ERROR_DETAIL) from None
        finally:
            if not adopted:
                _remove_apply_artifact_directory(artifact_dir)

    def _publish_point_handle(
        self,
        job_id: str,
        handle_key: str,
        new_handle: dict[str, Any],
        throw_if_cancelled: Callable[[], None] | None = None,
    ) -> bool:
        """Adopt *new_handle* into the job; returns whether the caller still owns it.

        *throw_if_cancelled* (an in-process run's) runs under the parent lock
        before anything else: a cancel seen here adopts nothing, and adoption is
        the commit point.
        """
        with self.parent_lock(job_id):
            if throw_if_cancelled is not None:
                throw_if_cancelled()
            latest_job = self._store.require_completed_job(job_id)
            latest_handles = _artifact_handles_or_raise(latest_job)
            if latest_handles.get(handle_key) is not None:
                return True
            updated_handles, evicted_handles = _with_bounded_frontier_apply_handle(
                latest_handles, handle_key, new_handle
            )
            updated_job = self._store.atomic_update_if_heavy_present(
                job_id,
                {"artifact_handles": updated_handles},
                required_keys=_point_runtime_keys(latest_job)[-1:],
                expected_status="completed",
            )
            if updated_job is None:
                raise ConflictError(
                    "Optimiser runtime state changed while materialising the frontier "
                    "point. Re-run the solve to materialise it again."
                )
        self._store.release_detached_artifact_handles(job_id, evicted_handles)
        return False
