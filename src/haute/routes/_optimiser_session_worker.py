"""The solver session's child side: the commands a solve's dedicated worker runs.

Every native price-contour call a process-mode solve makes happens here, in
the job's solver session (OPT-W01), under a native cap its parent re-sizes
before each command. The session keeps the solver, the quote grid and the
ratebook factor contexts between commands; only plain data and parquet
artifacts written here cross back to the server.

``build_and_solve`` runs the unchanged solver layer (``_solve_online`` /
``_solve_ratebook`` and their finalize) against a private record in this
process's ``optimiser_worker`` store, then hands the record's plain completion
fields to the parent and keeps its native objects. A failure the solver layer
classifies comes back as an ``OptimiserWorkerFailure``; a ``MemoryError``
behind any translation leaves the child as itself, so its parent classifies it
as the memory death it is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haute._memory_errors import memory_error_in
from haute._step_progress import StepProgress, current_job_progress_reporter

# The job-record keys that stay in this process: the native solve state.
NATIVE_RUNTIME_KEYS = ("solver", "solve_result", "quote_grid", "ratebook_factor_contexts")
# Keys of the private record that are its own bookkeeping, not completion fields.
_PRIVATE_RECORD_KEYS = frozenset(
    {
        "status",
        "terminal_reason",
        "created_at",
        "ended_at",
        "completed_at",
        "message",
        "elapsed_seconds",
        "heavy_objects_expires_at",
        "job_type",
        "config",
        "node_label",
        "input_provenance",
        "start_time",
        "timeout",
    }
)
_PROGRESS_STEPS = 1000


@dataclass(slots=True)
class _SessionState:
    mode: str
    solver: Any
    quote_grid: Any
    factor_contexts: Any | None
    factor_columns: list[list[str]] | None


# One solver session per process; keyed so a stale command cannot reach another solve.
_SESSIONS: dict[str, _SessionState] = {}


@dataclass(frozen=True)
class SessionSolveRequest:
    """Everything ``build_and_solve`` needs, as plain data."""

    session_id: str
    node_id: str
    mode: str
    config: dict[str, Any]
    node_label: str
    input_provenance: dict[str, Any]
    setup_chunking: dict[str, Any]
    input_path: str
    constraint_cols: list[str]
    ratebook_factors_handle: dict[str, Any] | None
    quote_analysis_handle: dict[str, Any] | None
    factor_level_order: dict[str, list[str]]
    # The parent-owned directory the as-solved apply artifact is written into.
    apply_artifact_dir: str
    # The parent-owned directory the frontier traces are written into; None without a sweep.
    frontier_history_dir: str | None


@dataclass(frozen=True)
class SessionSolveOutcome:
    """The solve's plain completion fields, or its classified failure."""

    completion_fields: dict[str, Any] | None = None
    failure: Any | None = None
    # The solve's memory forecast from its input, recorded beside the measured charge.
    solve_memory_forecast_bytes: int | None = None


@dataclass(frozen=True)
class SessionPointApplyRequest:
    session_id: str
    point_index: int
    lambdas: dict[str, float] | None
    constraints: dict[str, Any]
    factor_tables: dict[str, dict[str, float]] | None
    # The point's frontier row (ratebook): its evaluation must reproduce it exactly.
    expected_point: dict[str, Any] | None
    # The parent-owned directory the point's apply artifact is written into.
    artifact_dir: str


def _report_stage(label: str, fraction: float) -> None:
    reporter = current_job_progress_reporter()
    if reporter is not None:
        reporter(
            StepProgress(
                done=int(max(0.0, min(fraction, 1.0)) * _PROGRESS_STEPS),
                total=_PROGRESS_STEPS,
                label=label,
            )
        )


def _session(session_id: str) -> _SessionState:
    state = _SESSIONS.get(session_id)
    if state is None:
        raise RuntimeError(f"Solver session {session_id!r} holds no solved runtime")
    return state


def build_and_solve(request: SessionSolveRequest) -> SessionSolveOutcome:
    """Build the grid, solve and finalize; keep the native state here, return the rest."""
    from haute.routes._job_store import get_job_store
    from haute.routes._optimiser_input import (
        forecast_solve_memory_from_input,
        grid_chunk_decision,
        scenario_step_count,
    )
    from haute.routes._optimiser_outcomes import require_one_row_per_solved_quote
    from haute.routes._optimiser_service import (
        OptimiserSolveService,
        _OptimiserSolveRunningJob,
        solve_failure_transition,
    )
    from haute.routes._optimiser_solver import (
        SolveContext,
        _solve_online,
        _solve_ratebook,
        solver_worker_context,
    )
    from haute.routes._optimiser_worker import OptimiserWorkerFailure

    if request.session_id in _SESSIONS:
        raise RuntimeError(f"Solver session {request.session_id!r} has already solved")
    store = get_job_store("optimiser_worker")
    service = OptimiserSolveService(store)
    start_time = time.monotonic()
    seed: _OptimiserSolveRunningJob = {
        "status": "running",
        "job_type": "solve",
        "progress": 0.0,
        "config": dict(request.config),
        "node_label": request.node_label,
        "input_provenance": dict(request.input_provenance),
        "start_time": start_time,
        "timeout": None,
    }
    job_id = store.create_job(seed)
    if request.setup_chunking:
        store.update_job(job_id, setup_chunking=dict(request.setup_chunking))
    config = dict(request.config)
    solve_forecast: int | None = None
    try:
        with solver_worker_context():
            _report_stage("Building quote grid", 0.05)
            step_col = str(config.get("scenario_index", "scenario_index"))
            n_steps = scenario_step_count(Path(request.input_path), step_col)
            solve_forecast = forecast_solve_memory_from_input(
                Path(request.input_path),
                config,
                n_steps=n_steps,
                chunk_rows=grid_chunk_decision(n_steps).chunk_size,
            )
            quote_grid = service._build_grid_from_parquet(
                request.input_path,
                request.constraint_cols,
                config,
                request.node_id,
                job_id,
                execution_context=None,
                n_steps=n_steps,
            )
            if request.quote_analysis_handle is not None:
                require_one_row_per_solved_quote(request.quote_analysis_handle, quote_grid.n_quotes)
            _report_stage("Solving", 0.1)
            ctx = SolveContext(
                job_id=job_id,
                node_id=request.node_id,
                mode=request.mode,
                store=store,
                start_time=start_time,
                report_stage=_report_stage,
                apply_artifact_dir=request.apply_artifact_dir,
                frontier_history_dir=request.frontier_history_dir,
            )
            if request.mode == "ratebook":
                _solve_ratebook(
                    ctx,
                    quote_grid=quote_grid,
                    config=config,
                    ratebook_factors_handle=request.ratebook_factors_handle,
                    factor_level_order=request.factor_level_order,
                    quote_analysis_handle=request.quote_analysis_handle,
                )
            else:
                _solve_online(
                    ctx,
                    quote_grid=quote_grid,
                    config=config,
                    quote_analysis_handle=request.quote_analysis_handle,
                )
        record = store.require_job(job_id)
        if record.get("status") != "completed":
            return SessionSolveOutcome(
                failure=OptimiserWorkerFailure.from_job_record(record),
                solve_memory_forecast_bytes=solve_forecast,
            )
        _SESSIONS[request.session_id] = _SessionState(
            mode=request.mode,
            solver=record["solver"],
            quote_grid=record["quote_grid"],
            factor_contexts=record.get("ratebook_factor_contexts"),
            factor_columns=record.get("factor_columns_valid"),
        )
        completion_fields = {
            key: value
            for key, value in record.items()
            if key not in _PRIVATE_RECORD_KEYS and key not in NATIVE_RUNTIME_KEYS
        }
        # The parent adopts the artifacts; this private record must not clean them.
        store.update_job(job_id, artifact_handles={})
        return SessionSolveOutcome(
            completion_fields=completion_fields, solve_memory_forecast_bytes=solve_forecast
        )
    except Exception as exc:
        memory_error = memory_error_in(exc)
        if memory_error is not None:
            raise memory_error from None
        record = store.require_job(job_id)
        if record.get("status") not in (None, "running"):
            failure = OptimiserWorkerFailure.from_job_record(record)
        else:
            reason, message, fields = solve_failure_transition(
                exc,
                node_id=request.node_id,
                elapsed_seconds=time.monotonic() - start_time,
            )
            failure = OptimiserWorkerFailure(terminal_reason=reason, message=message, fields=fields)
        return SessionSolveOutcome(failure=failure, solve_memory_forecast_bytes=solve_forecast)
    finally:
        store.delete_job(job_id)


def apply_point(request: SessionPointApplyRequest) -> dict[str, Any]:
    """Compute one frontier point's per-quote frame and write its apply artifact.

    The owner's cancel (OPT-PC02) reaches price-contour's token through the
    dedicated worker's cancel cell; a cancelled apply raises the library's
    ``Cancelled`` and writes nothing.
    """
    from haute._dedicated_workers import on_command_cancel
    from haute._price_contour import price_contour
    from haute.routes._optimiser_artifacts import _persist_apply_frame_artifact
    from haute.routes._optimiser_frontier import (
        _dataframe_or_raise,
        _require_evaluation_is_the_point,
    )

    pc = price_contour()
    state = _session(request.session_id)
    _report_stage(f"Applying frontier point {request.point_index}", 0.1)
    cancel = pc.CancelToken()
    with on_command_cancel(cancel.cancel):
        if state.mode == "ratebook":
            if request.factor_tables is None or request.expected_point is None:
                raise RuntimeError(
                    "A ratebook point apply needs the point's tables and frontier row"
                )
            evaluation = state.solver.evaluate(
                state.quote_grid, state.factor_contexts, request.factor_tables, cancel=cancel
            )
            _require_evaluation_is_the_point(
                evaluation, request.expected_point, request.point_index
            )
            frame = evaluation.quote_results
        else:
            if request.lambdas is None:
                raise RuntimeError("An online point apply needs the point's lambdas")
            apply_result = pc.apply_from_grid(
                state.quote_grid,
                lambdas=request.lambdas,
                constraints=request.constraints,
                cancel=cancel,
            )
            frame = _dataframe_or_raise(apply_result, context="Apply result")
        if cancel.cancelled:
            raise pc.Cancelled("the point apply was cancelled through its CancelToken")
    return _persist_apply_frame_artifact(frame, artifact_dir=Path(request.artifact_dir))
