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
import os
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from haute._env import int_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_isolated_execution_context,
)
from haute._execution_context import ExecutionContext, ExecutionMemoryLimitExceededError
from haute._logging import get_logger
from haute._memory_errors import memory_error_in
from haute._seed_plans import SeedPlanHandoff
from haute.errors import RequestRefusedError
from haute.routes._job_lifecycle import TERMINAL_REASONS, TerminalReason
from haute.routes._job_store import get_job_store
from haute.routes._optimiser_input import (
    ESTIMATE_MAPPED_ERRORS,
    OptimiserSetupError,
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

    The parent resolved the job's config and demand once; the child runs the
    solve setup's pipeline stage under the parent's seed plan and never
    re-plans.
    """

    body: OptimiserFrontierAutoRangeRequest
    config: dict[str, Any]
    mode: str
    timeout: int | None
    required_columns_by_node: dict[str, frozenset[str]]
    seed_plan: SeedPlanHandoff
    scratch_dir: str


@dataclass(frozen=True)
class FrontierAutoRangeWorkerOutcome:
    """The auto-range child's only return value."""

    ranges: dict[str, dict[str, float]] | None = None
    execution_metrics: dict[str, Any] | None = None
    failure: OptimiserWorkerFailure | None = None


_OPTIMISER_POLARS_THREADS_ENV = "HAUTE_OPTIMISER_POLARS_THREADS"
_DEFAULT_OPTIMISER_POLARS_THREADS = 8


def resolve_optimiser_polars_threads() -> int:
    """The Polars thread-pool size of the optimiser's setup and auto-range workers.

    Polars bounds a streaming pipeline by morsels per thread, not bytes, so a
    fan-out such as the scenario expander's explode multiplies what every
    thread holds. On a 10M-quote x 11-scenario pipeline the scorer's input
    sink peaked at 5.3 GiB with 22 threads and 1.8 GiB with 8, in the same
    time; the batch reducer likewise loses no time at 8. Polars reads the
    setting once at import, so it is applied to each worker at spawn.
    """
    return int_env(
        _OPTIMISER_POLARS_THREADS_ENV,
        min(os.cpu_count() or 1, _DEFAULT_OPTIMISER_POLARS_THREADS),
    )


@contextlib.contextmanager
def worker_scratch_directory() -> Iterator[str]:
    """Create the parent-owned scratch directory a worker writes its temporary files into.

    It is removed once the worker has exited, whether it returned, failed or
    was terminated; a removal failure is logged, never raised over the job's
    own outcome.
    """
    from haute.routes._optimiser_artifacts import scratch_directory

    with scratch_directory("worker_") as directory:
        yield str(directory)


@contextlib.contextmanager
def _temporary_files_in(scratch_dir: str) -> Iterator[None]:
    """Route the child's Python temporary files into its parent-owned scratch directory."""
    previous = tempfile.tempdir
    tempfile.tempdir = scratch_dir
    try:
        yield
    finally:
        tempfile.tempdir = previous


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
    }
    job_id = store.create_job(job)
    store.atomic_update(job_id, {"start_time": time.monotonic()})
    return job_id


SETUP_OPERATION_NOUN = "Optimiser setup"
AUTO_RANGE_OPERATION_NOUN = "Auto-range"


def classify_optimiser_failure(
    exc: BaseException,
    *,
    operation_noun: str,
    log_event: str,
    **log_context: Any,
) -> OptimiserWorkerFailure:
    """The one classification of an optimiser setup or auto-range failure.

    A memory refusal or overrun is ``memory_limited`` (507) with the curated
    message naming *operation_noun*; a refusal is ``contract_error`` for a
    4xx and ``error`` otherwise; a public contract error keeps its own
    reason; a pipeline that cannot run bounded is a 422 ``contract_error``.
    Anything else, a library or Polars ``ValueError`` among them, is a
    server fault: an ``error`` whose HTTP detail is fixed and whose cause is
    logged as *log_event*. A replayed child failure is already classified.
    Stops and cancellations are the caller's, never a failure.
    """
    from haute.errors import BoundedMemoryUnsupportedError
    from haute.routes._contract_errors import (
        PUBLIC_CONTRACT_ERROR_TYPES,
        contract_error_job_fields,
        contract_error_terminal_reason,
        memory_limit_refusal,
    )

    if isinstance(exc, OptimiserWorkerFailureError):
        return exc.failure
    if isinstance(exc, OptimiserSetupError):
        return OptimiserWorkerFailure(
            terminal_reason=exc.reason, message=exc.message, fields=dict(exc.fields)
        )
    if isinstance(exc, (ExecutionAdmissionError, ExecutionMemoryLimitExceededError)):
        detail = memory_limit_refusal(exc, operation_noun=operation_noun).detail
        return _memory_failure(detail, operation_noun=operation_noun)
    if isinstance(exc, RequestRefusedError):
        if exc.status_code == 507 and isinstance(exc.detail, Mapping):
            if exc.detail.get("error_code") == "memory_limit":
                return _memory_failure(exc.detail, operation_noun=operation_noun)
        reason: TerminalReason = "contract_error" if 400 <= exc.status_code < 500 else "error"
        return OptimiserWorkerFailure(
            terminal_reason=reason,
            message=str(exc.detail),
            fields={"http_status_code": exc.status_code, "error_detail": exc.detail},
        )
    if isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
        return OptimiserWorkerFailure(
            terminal_reason=contract_error_terminal_reason(exc),
            message=str(exc),
            fields=contract_error_job_fields(exc),
        )
    if isinstance(exc, BoundedMemoryUnsupportedError):
        detail = f"{operation_noun} cannot run in bounded streaming mode: {exc}"
        logger.warning(f"{log_event}_bounded_streaming_unsupported", error=str(exc), **log_context)
        return OptimiserWorkerFailure(
            terminal_reason="contract_error",
            message=detail,
            fields={"http_status_code": 422, "error_detail": detail},
        )
    logger.error(log_event, error=str(exc), exc_info=exc, **log_context)
    return OptimiserWorkerFailure(
        terminal_reason="error",
        message=f"{operation_noun} failed: {exc}",
        fields={
            "http_status_code": 500,
            "error_detail": f"{operation_noun} failed. Check the server logs for details.",
        },
    )


def _memory_failure(detail: object, *, operation_noun: str) -> OptimiserWorkerFailure:
    """A 507 memory failure; its message is the curated one the refusal carries."""
    payload = (
        {str(key): value for key, value in detail.items()}
        if isinstance(detail, Mapping)
        else {"message": str(detail)}
    )
    payload.setdefault("error_code", "memory_limit")
    message = payload.get("message")
    if not isinstance(message, str) or not message:
        reason = payload.get("reason")
        message = f"{operation_noun} exceeded its memory budget" + (
            f" ({reason})." if isinstance(reason, str) and reason else "."
        )
    return OptimiserWorkerFailure(
        terminal_reason="memory_limited",
        message=message,
        fields={"error_code": "memory_limit", "http_status_code": 507, "error_detail": payload},
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
        return classify_optimiser_failure(
            exc, operation_noun=operation_noun, log_event="optimiser_worker_failed", job_id=job_id
        )
    return OptimiserWorkerFailure.from_job_record(record)


def materialise_solve_input_worker(
    request: SolveInputWorkerRequest,
    budget: IsolatedExecutionBudget,
) -> SolveInputWorkerOutcome:
    """Spawn entrypoint: materialise the solve's input under the child's own hard cap."""
    from haute.routes._optimiser_service import OptimiserSolveService

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
        memory_error = memory_error_in(exc)
        if memory_error is not None:
            raise memory_error from None
        return SolveInputWorkerOutcome(
            execution_metrics=context.metrics_payload() if context is not None else None,
            failure=_recorded_failure(store, job_id, exc, operation_noun=SETUP_OPERATION_NOUN),
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
    from haute.routes._optimiser_service import OptimiserSolveService

    store = get_job_store("optimiser_worker")
    service = OptimiserSolveService(store)
    job_id = _private_auto_range_job(store, request)
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        with _temporary_files_in(request.scratch_dir):
            response = service._run_frontier_auto_range_job(
                request.body,
                job_id,
                config=request.config,
                mode=request.mode,
                timeout=request.timeout,
                required_columns_by_node=request.required_columns_by_node,
                execution_context=context,
                seed_plan=request.seed_plan,
                isolate=False,
            )
        return FrontierAutoRangeWorkerOutcome(
            ranges={
                name: {"min": value.min, "max": value.max}
                for name, value in response.ranges.items()
            },
            execution_metrics=context.metrics_payload(status="completed"),
        )
    except Exception as exc:
        memory_error = memory_error_in(exc)
        if memory_error is not None:
            raise memory_error from None
        return FrontierAutoRangeWorkerOutcome(
            execution_metrics=context.metrics_payload() if context is not None else None,
            failure=_recorded_failure(store, job_id, exc, operation_noun=AUTO_RANGE_OPERATION_NOUN),
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
        memory_error = memory_error_in(exc)
        if memory_error is not None:
            raise memory_error from None
        if isinstance(exc, RequestRefusedError):
            return OptimiserEstimateOutcome(failure=(exc.status_code, exc.detail))
        if isinstance(exc, ESTIMATE_MAPPED_ERRORS):
            answer = estimate_failure_http_exception(exc, node_id=body.node_id)
            return OptimiserEstimateOutcome(failure=(answer.status_code, answer.detail))
        raise
    finally:
        context.release_admission(preserve_primary_error=True)
