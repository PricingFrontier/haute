"""Hard-capped spawn workers that materialise optimiser inputs.

Solve setup and frontier auto-range execute the user's pipeline. In production
(``HAUTE_INTERACTIVE_EXECUTION_MODE=process``) that execution runs here, in a
killable spawn worker under the parent's admitted headroom as a native cap, the
way training preparation runs; the explicit ``thread`` compatibility mode runs
the same service methods on the job's own thread instead.

A child runs the unchanged :class:`OptimiserSolveService` steps against a
private job record in the ``optimiser_worker`` store, deleted when the child
finishes: the steps' own failure mapping classifies any failure onto that
record, and the record crosses the process boundary as plain data
(:class:`OptimiserWorkerFailure`) for the parent to replay onto the real job.
The child adopts the seed plan its parent opened, so input preparation and the
plan's leases stay with the parent, and it never touches the parent's store.

Everything a child writes lives in a location its parent created and removes
after the worker exits, however it exits: the solver-input parquet, the
ratebook factor artifact directory, and a scratch directory every Python
temporary file of the child is routed into (the range reducer's bucket parts,
staged batches, model-scoring temp files). A kill never orphans them. The
child never borrows a captured snapshot for the solver input: a capture it made
is released when its adopted plan closes, before the parent reads the file.
A ``MemoryError`` a native cap raised in the child leaves it as that error, so
the parent classifies it as ``memory_limited`` like any memory-shaped worker
failure, instead of the child's generic failure mapping reporting a 500.

The input estimate runs on the warm interactive worker pool instead
(:func:`optimiser_estimate_worker`): it answers a request, so it returns the
counts or a typed answer and records nothing.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_isolated_execution_context,
)
from haute._execution_context import ExecutionContext, ExecutionMemoryLimitExceededError
from haute._logging import get_logger
from haute._seed_plans import SeedPlanHandoff
from haute.routes._job_lifecycle import TERMINAL_REASONS, TerminalReason
from haute.routes._job_store import get_job_store
from haute.routes._optimiser_input import (
    ESTIMATE_MAPPED_ERRORS,
    estimate_failure_http_exception,
)
from haute.schemas import (
    OptimiserEstimateRequest,
    OptimiserFrontierAutoRangeRequest,
    OptimiserSolveRequest,
)

if TYPE_CHECKING:
    from haute.routes._job_store import JobStore

logger = get_logger(component="server.optimiser.solve")

#: The failure fields a terminal job record carries besides its status and message.
_FAILURE_FIELDS = ("error", "error_code", "error_detail", "http_status_code", "execution_metrics")


@dataclass(frozen=True)
class OptimiserWorkerFailure:
    """A child's terminal failure record, as plain data the parent replays onto its job."""

    terminal_reason: TerminalReason
    message: str
    fields: dict[str, Any]

    @classmethod
    def from_job_record(cls, record: Mapping[str, Any]) -> OptimiserWorkerFailure:
        status = record.get("status")
        if status not in TERMINAL_REASONS or status == "completed":
            raise RuntimeError(f"Optimiser worker job record is not a failure: {status!r}")
        return cls(
            terminal_reason=status,
            message=str(record.get("message", "")),
            fields={key: record[key] for key in _FAILURE_FIELDS if key in record},
        )

    @property
    def http_status_code(self) -> int:
        code = self.fields.get("http_status_code")
        return code if isinstance(code, int) and not isinstance(code, bool) else 500

    @property
    def http_detail(self) -> Any:
        return self.fields.get("error_detail", self.message)


class OptimiserWorkerFailureError(Exception):
    """Raised in the parent to replay a child's failure through the job's failure mapping."""

    def __init__(self, failure: OptimiserWorkerFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


@dataclass(frozen=True)
class SolveInput:
    """The materialised solver input: the parent's parquet plus what the grid build needs."""

    path: str
    constraint_cols: list[str]
    ratebook_factors_handle: dict[str, Any] | None
    # The quote-analysis table written into the parent's directory; None without one.
    quote_analysis_handle: dict[str, Any] | None


@dataclass(frozen=True)
class SolveInputWorkerRequest:
    """Everything the solve-input child needs, as picklable plain data."""

    body: OptimiserSolveRequest
    config: dict[str, Any]
    mode: str
    required_columns_by_node: dict[str, frozenset[str]]
    project_root: str
    seed_plan: SeedPlanHandoff
    output_path: str
    scratch_dir: str
    ratebook_factors_dir: str | None
    quote_analysis_dir: str | None


@dataclass(frozen=True)
class SolveInputWorkerOutcome:
    """The solve-input child's only return value."""

    solve_input: SolveInput | None = None
    execution_metrics: dict[str, Any] | None = None
    failure: OptimiserWorkerFailure | None = None


@dataclass(frozen=True)
class FrontierAutoRangeWorkerRequest:
    """Everything the auto-range child needs, as picklable plain data.

    ``chunked`` is the parent's structural plan decision; the child re-plans
    (a chunk plan is not picklable) and sizes the chunks, which samples rows,
    so the server process never reads them. Sizing can lose a chunked plan:
    the child then reports the fallback without computing anything, because
    the parent opened the seed plan for the chunked execution target. Any
    other disagreement fails loudly.
    """

    body: OptimiserFrontierAutoRangeRequest
    project_root: str
    seed_plan: SeedPlanHandoff
    chunked: bool
    scratch_dir: str


@dataclass(frozen=True)
class FrontierAutoRangeWorkerOutcome:
    """The auto-range child's only return value."""

    ranges: dict[str, dict[str, float]] | None = None
    execution_metrics: dict[str, Any] | None = None
    failure: OptimiserWorkerFailure | None = None
    # Set when sizing the chunks lost the parent's chunked plan.
    chunk_fallback: dict[str, Any] | None = None


@contextlib.contextmanager
def worker_scratch_directory() -> Iterator[str]:
    """Create the parent-owned scratch directory a worker writes its temporary files into.

    It is removed once the worker has exited, whether it returned, failed or
    was terminated; a removal failure is logged, never raised over the job's
    own outcome.
    """
    path = tempfile.mkdtemp(prefix="haute_optimiser_worker_")
    try:
        yield path
    finally:
        try:
            shutil.rmtree(path)
        except OSError as cleanup_exc:
            logger.warning(
                "optimiser_worker_scratch_cleanup_failed",
                path=path,
                error=str(cleanup_exc),
            )


@contextlib.contextmanager
def _temporary_files_in(scratch_dir: str) -> Iterator[None]:
    """Route the child's Python temporary files into its parent-owned scratch directory."""
    previous = tempfile.tempdir
    tempfile.tempdir = scratch_dir
    try:
        yield
    finally:
        tempfile.tempdir = previous


def _memory_error_in(exc: BaseException) -> MemoryError | None:
    """Return a ``MemoryError`` behind *exc*, however many translations wrap it."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, MemoryError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _private_solve_job(store: JobStore, request: SolveInputWorkerRequest) -> str:
    from haute.routes._optimiser_service import _OptimiserSolveRunningJob

    job: _OptimiserSolveRunningJob = {
        "status": "running",
        "job_type": "solve",
        "progress": 0.0,
        "config": dict(request.config),
        "node_label": request.body.node_id,
        "start_time": time.monotonic(),
        "timeout": None,
    }
    return store.create_job(job)


def _private_auto_range_job(store: JobStore, request: FrontierAutoRangeWorkerRequest) -> str:
    from haute.routes._optimiser_service import _FrontierAutoRangeRunningJob

    job: _FrontierAutoRangeRunningJob = {
        "status": "running",
        "job_type": "frontier_auto_range",
        "progress": 0.0,
        "config": {},
        "node_label": request.body.node_id,
        "chunk_fallback": None,
    }
    job_id = store.create_job(job)
    store.atomic_update(job_id, {"start_time": time.monotonic()})
    return job_id


def _failure_before_job_mapping(
    exc: BaseException,
    *,
    operation_noun: str,
) -> OptimiserWorkerFailure:
    """Classify a child failure that no job failure mapping recorded.

    Only the worker's own start-up (its execution context, the re-plan) can
    fail before a job step's mapping runs.
    """
    from haute.routes._contract_errors import memory_limit_http_exception

    if isinstance(exc, (ExecutionAdmissionError, ExecutionMemoryLimitExceededError)):
        detail = memory_limit_http_exception(exc, operation_noun="Auto-range").detail
        return OptimiserWorkerFailure(
            terminal_reason="memory_limited",
            message=str(detail.get("message", detail)) if isinstance(detail, dict) else "",
            fields={"error_code": "memory_limit", "http_status_code": 507, "error_detail": detail},
        )
    if isinstance(exc, HTTPException):
        reason: TerminalReason = "contract_error" if 400 <= exc.status_code < 500 else "error"
        return OptimiserWorkerFailure(
            terminal_reason=reason,
            message=str(exc.detail),
            fields={"http_status_code": exc.status_code, "error_detail": exc.detail},
        )
    return OptimiserWorkerFailure(
        terminal_reason="error",
        message=f"{operation_noun} failed: {exc}",
        fields={
            "http_status_code": 500,
            "error_detail": f"{operation_noun} failed. Check the server logs for details.",
        },
    )


def _recorded_failure(
    store: JobStore,
    job_id: str,
    exc: BaseException,
    *,
    operation_noun: str,
) -> OptimiserWorkerFailure:
    record = store.require_job(job_id)
    if record.get("status") == "running":
        return _failure_before_job_mapping(exc, operation_noun=operation_noun)
    return OptimiserWorkerFailure.from_job_record(record)


def materialise_solve_input_worker(
    request: SolveInputWorkerRequest,
    budget: IsolatedExecutionBudget,
) -> SolveInputWorkerOutcome:
    """Spawn entrypoint: materialise the solve's input under the child's own hard cap."""
    from haute._sandbox import set_project_root
    from haute.routes._optimiser_service import OptimiserSolveService

    # The spawned child starts with the interpreter's default sandbox root.
    set_project_root(Path(request.project_root))
    store = get_job_store("optimiser_worker")
    service = OptimiserSolveService(store)
    job_id = _private_solve_job(store, request)
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        with _temporary_files_in(request.scratch_dir), contextlib.ExitStack() as resources:
            try:
                solve_input = service._materialise_solve_input(
                    request.body,
                    job_id,
                    resources,
                    config=request.config,
                    mode=request.mode,
                    required_columns_by_node=request.required_columns_by_node,
                    execution_context=context,
                    output_path=request.output_path,
                    ratebook_factors_dir=request.ratebook_factors_dir,
                    quote_analysis_dir=request.quote_analysis_dir,
                    seed_plan=request.seed_plan,
                )
            except Exception as exc:
                service._record_solve_setup_failure(
                    job_id,
                    exc,
                    node_id=request.body.node_id,
                    execution_context=context,
                    start_time=float(store.require_job(job_id)["start_time"]),
                )
                raise
        return SolveInputWorkerOutcome(
            solve_input=solve_input,
            execution_metrics=context.metrics_payload(),
        )
    except Exception as exc:
        memory_error = _memory_error_in(exc)
        if memory_error is not None:
            raise memory_error from None
        return SolveInputWorkerOutcome(
            execution_metrics=context.metrics_payload() if context is not None else None,
            failure=_recorded_failure(store, job_id, exc, operation_noun="Optimiser setup"),
        )
    finally:
        store.delete_job(job_id)
        if context is not None:
            context.release_admission(preserve_primary_error=True)


def frontier_auto_range_worker(
    request: FrontierAutoRangeWorkerRequest,
    budget: IsolatedExecutionBudget,
) -> FrontierAutoRangeWorkerOutcome:
    """Spawn entrypoint: compute auto-range totals under the child's own hard cap."""
    from haute._sandbox import set_project_root
    from haute.routes._optimiser_service import OptimiserSolveService

    set_project_root(Path(request.project_root))
    store = get_job_store("optimiser_worker")
    service = OptimiserSolveService(store)
    job_id = _private_auto_range_job(store, request)
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        with _temporary_files_in(request.scratch_dir):
            prepared = service._prepare_frontier_auto_range(
                request.body,
                prepare_snapshot_inputs=False,
            )
            planned_chunked = prepared["streaming_plan"] is not None
            if request.chunked and not planned_chunked and prepared["chunk_fallback"]:
                return FrontierAutoRangeWorkerOutcome(
                    execution_metrics=context.metrics_payload(status="completed"),
                    chunk_fallback=prepared["chunk_fallback"],
                )
            if planned_chunked != request.chunked:
                raise RuntimeError(
                    "Auto-range chunk planning changed between the request and its worker; "
                    "start auto-range again."
                )
            response = service._run_frontier_auto_range_job(
                request.body,
                job_id,
                execution_context=context,
                seed_plan=request.seed_plan,
                isolate=False,
                **prepared,
            )
        return FrontierAutoRangeWorkerOutcome(
            ranges={
                name: {"min": value.min, "max": value.max}
                for name, value in response.ranges.items()
            },
            execution_metrics=context.metrics_payload(status="completed"),
        )
    except Exception as exc:
        memory_error = _memory_error_in(exc)
        if memory_error is not None:
            raise memory_error from None
        return FrontierAutoRangeWorkerOutcome(
            execution_metrics=context.metrics_payload() if context is not None else None,
            failure=_recorded_failure(store, job_id, exc, operation_noun="Frontier auto range"),
        )
    finally:
        store.delete_job(job_id)
        if context is not None:
            context.release_admission(preserve_primary_error=True)


@dataclass(frozen=True)
class OptimiserEstimateOutcome:
    """The estimate worker's only return value: the counts, or a typed answer."""

    metrics: dict[str, int | float | None] | None = None
    failure: tuple[int, Any] | None = None


def optimiser_estimate_worker(
    body: OptimiserEstimateRequest,
    budget: IsolatedExecutionBudget,
) -> OptimiserEstimateOutcome:
    """Interactive-pool entrypoint: count the optimiser's input under the worker's cap.

    The estimate job lives in this worker's private ``optimiser_worker`` store
    and is removed when the count finishes. A refusal with a typed answer comes
    back as ``(status_code, detail)``; anything else, including a
    ``MemoryError`` behind a translated failure, leaves the worker as an
    exception for the pool to classify.
    """
    from haute.routes._optimiser_service import OptimiserSolveService

    service = OptimiserSolveService(get_job_store("optimiser_worker"))
    context = create_isolated_execution_context(budget)
    try:
        return OptimiserEstimateOutcome(
            metrics=service.estimate_input(body, execution_context=context)
        )
    except Exception as exc:
        # A MemoryError behind any translation (setup maps it to a 500) leaves
        # the worker as itself, so the pool answers the typed 507.
        memory_error = _memory_error_in(exc)
        if memory_error is not None:
            raise memory_error from None
        if isinstance(exc, HTTPException):
            return OptimiserEstimateOutcome(failure=(exc.status_code, exc.detail))
        if isinstance(exc, ESTIMATE_MAPPED_ERRORS):
            answer = estimate_failure_http_exception(exc, node_id=body.node_id)
            return OptimiserEstimateOutcome(failure=(answer.status_code, answer.detail))
        raise
    finally:
        context.release_admission(preserve_primary_error=True)
