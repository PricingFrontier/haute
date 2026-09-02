"""Multi-row deploy scoring inside a hard-capped isolated worker.

A deployed container serves single-row quotes in-process (one live request,
one small frame).  A multi-row batch is the request shape that can genuinely
materialise: its input frame, any group-by boundary, and the scored output all
grow with the payload.  Those runs are therefore executed in a spawn worker
whose RSS is hard-capped at the parent's admitted headroom, exactly like the
Data Output writer (``haute.routes.pipeline._prepare_data_output_worker``).

The parent admits ``DEPLOY_BATCH`` once, writes the request rows to a private
temp directory, and supervises the child; the child owns execution and sinks
its scored rows to a parquet file inside that directory.  Nothing but plain
picklable evidence crosses the process boundary.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._env import float_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_isolated_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._logging import get_logger
from haute._polars_utils import bounded_sink
from haute._types import PipelineGraph
from haute._worker_isolation import IsolatedWorkerConfig, worker_config_for_memory_policy
from haute.errors import BoundedMemoryUnsupportedError
from haute.routes._contract_errors import PUBLIC_CONTRACT_ERROR_TYPES

logger = get_logger(component="deploy.batch_scoring")

DEPLOY_BATCH_TIMEOUT_ENV = "HAUTE_DEPLOY_BATCH_TIMEOUT"
DEFAULT_DEPLOY_BATCH_TIMEOUT_SECONDS = 300.0
DEPLOY_BATCH_PROCESS_NAME = "haute-deploy-batch"

BatchScoreFailureKind = Literal["contract", "bounded", "memory", "cancelled", "error"]


def deploy_batch_timeout_seconds() -> float:
    """Return the wall-clock cap for one batch worker."""
    return float_env(DEPLOY_BATCH_TIMEOUT_ENV, DEFAULT_DEPLOY_BATCH_TIMEOUT_SECONDS)


@dataclass(frozen=True, slots=True)
class BatchScoreRequest:
    """Everything the child needs to score one batch, all picklable."""

    graph: PipelineGraph
    input_node_ids: list[str]
    output_node_id: str
    artifact_paths: dict[str, str]
    output_fields: list[str] | None
    input_path: str
    result_path: str
    operation: str


@dataclass(frozen=True, slots=True)
class BatchScoreOutcome:
    """Picklable child result: either scored rows or a classified failure."""

    row_count: int | None = None
    execution_metrics: dict[str, Any] | None = None
    failure_kind: BatchScoreFailureKind | None = None
    detail: str | None = None
    payload: dict[str, Any] | None = None


class BatchScoreCleanupError(RuntimeError):
    """Raised when a batch's private temp directory could not be removed.

    The directory holds the request rows and the scored parquet, so a leftover
    copy is a data-retention defect even after a successful score. It is
    therefore raised rather than logged whenever no primary error is in flight.
    """

    def __init__(self, temp_dir: str, cause: BaseException) -> None:
        super().__init__(
            f"Deploy batch scoring could not remove its temporary directory {temp_dir!r}: {cause}"
        )
        self.temp_dir = temp_dir


class BatchScoreError(RuntimeError):
    """Typed parent-side view of a classified batch failure."""

    def __init__(
        self,
        kind: BatchScoreFailureKind,
        detail: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.payload = payload


@dataclass(frozen=True, slots=True)
class BatchScoreResult:
    """Accepted child output: the scored parquet plus the child's metrics."""

    result_path: str
    row_count: int
    execution_metrics: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Child (spawn worker)
# ---------------------------------------------------------------------------


def _parquet_row_count(path: str | Path) -> int:
    """Return the row count of a sunk parquet without materialising its rows."""
    return int(pl.scan_parquet(path).select(pl.len()).collect().item())


def _remove_batch_temp_dir(temp_dir: str, *, primary_error: BaseException | None) -> None:
    """Remove one batch's private directory, never silently."""
    try:
        shutil.rmtree(temp_dir)
    except OSError as exc:
        if primary_error is None:
            raise BatchScoreCleanupError(temp_dir, exc) from exc
        primary_error.add_note(f"Deploy batch cleanup failed: {exc}")
        logger.warning(
            "deploy_batch_cleanup_failed",
            temp_dir=temp_dir,
            error=str(exc),
        )


def _remove_result_file(path: str) -> None:
    """Drop a partial result so no half-scored parquet can be published."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("deploy_batch_result_cleanup_failed", path=path, error=str(exc))


def _classify_batch_failure(exc: BaseException) -> BatchScoreOutcome | None:
    """Map an in-child exception to its outcome, or ``None`` to re-raise."""
    if isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
        return BatchScoreOutcome(
            failure_kind="contract",
            detail=str(exc),
            payload=exc.to_payload(),
        )
    if isinstance(exc, BoundedMemoryUnsupportedError):
        return BatchScoreOutcome(failure_kind="bounded", detail=str(exc))
    if isinstance(exc, ExecutionAdmissionError | ExecutionMemoryLimitExceededError):
        return BatchScoreOutcome(
            failure_kind="memory",
            detail=str(exc),
            payload=exc.to_payload(),
        )
    if isinstance(exc, ExecutionCancelledError):
        return BatchScoreOutcome(failure_kind="cancelled", detail=str(exc))
    if isinstance(exc, Exception):
        return BatchScoreOutcome(failure_kind="error", detail=str(exc))
    return None


def score_batch_scoring_request(
    request: BatchScoreRequest,
    *,
    execution_context: ExecutionContext,
) -> BatchScoreOutcome:
    """Score one batch under an already-constructed worker-local context."""
    from haute.deploy._scorer import score_graph_lazy

    execution_context.checkpoint(label="before_deploy_batch_dataframe")
    rows = json.loads(Path(request.input_path).read_text(encoding="utf-8"))
    input_df = pl.DataFrame(rows)
    execution_context.checkpoint(label="after_deploy_batch_dataframe")

    plan = score_graph_lazy(
        graph=request.graph,
        input_df=input_df,
        input_node_ids=list(request.input_node_ids),
        output_node_id=request.output_node_id,
        artifact_paths=dict(request.artifact_paths),
        output_fields=list(request.output_fields) if request.output_fields else None,
        execution_context=execution_context,
    )
    preserve_primary_error = False
    try:
        with execution_context.stage("deploy_batch_sink", node_id=request.output_node_id):
            bounded_sink(plan.lazy_frame, request.result_path)
    except BaseException:
        preserve_primary_error = True
        raise
    finally:
        # The worker entrypoint owns the single admission release; the plan
        # only owns its model-score temp files here.
        plan.cleanup(
            preserve_primary_error=preserve_primary_error,
            release_admission=False,
        )
    return BatchScoreOutcome(
        row_count=_parquet_row_count(request.result_path),
        execution_metrics=execution_context.metrics_payload(status="completed"),
    )


def score_batch_worker(
    request: BatchScoreRequest,
    budget: IsolatedExecutionBudget,
) -> BatchScoreOutcome:
    """Spawn entrypoint: score one batch inside the hard-capped child process."""
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        return score_batch_scoring_request(request, execution_context=context)
    except BaseException as exc:
        _remove_result_file(request.result_path)
        outcome = _classify_batch_failure(exc)
        if outcome is None:
            raise
        return outcome
    finally:
        if context is not None:
            context.release_admission(preserve_primary_error=True)


# ---------------------------------------------------------------------------
# Parent (supervisor)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BatchScorePlan:
    """Parent-owned resources for one supervised batch worker."""

    request: BatchScoreRequest
    budget: IsolatedExecutionBudget
    execution_context: ExecutionContext
    worker_config: IsolatedWorkerConfig
    temp_dir: str
    _cleaned_up: bool = field(default=False, repr=False)

    def cleanup(self, *, primary_error: BaseException | None = None) -> None:
        """Remove the private temp directory and release admission exactly once.

        A cleanup-only failure is raised as :class:`BatchScoreCleanupError`; a
        failure while *primary_error* is unwinding is attached to it as a note
        so the original failure still reaches the client.
        """
        if self._cleaned_up:
            return
        self._cleaned_up = True
        try:
            _remove_batch_temp_dir(self.temp_dir, primary_error=primary_error)
        finally:
            self.execution_context.release_admission(
                preserve_primary_error=primary_error is not None,
            )


def prepare_batch_scoring(
    rows: list[dict[str, Any]],
    *,
    graph: PipelineGraph,
    input_node_ids: list[str],
    output_node_id: str,
    artifact_paths: dict[str, str],
    output_fields: list[str] | None,
    operation: str = "deploy_quote",
) -> BatchScorePlan:
    """Admit the parent batch context and stage the child's inputs on disk."""
    from haute.deploy._scorer import admit_deploy_execution

    # The batch path is one fixed execution path: it always admits the served
    # ``DEPLOY_BATCH`` envelope and batch ``modelScore`` contract, even when the
    # bundle's schema dry-run sends a single sample row through it.
    execution_context = admit_deploy_execution(
        operation=operation,
        row_count=len(rows),
        profile=ExecutionProfile.DEPLOY_BATCH,
    )
    try:
        budget = isolated_execution_budget(execution_context)
        temp_dir = tempfile.mkdtemp(prefix="haute_deploy_batch_")
        try:
            input_path = Path(temp_dir) / "input.json"
            input_path.write_text(json.dumps(rows), encoding="utf-8")
            request = BatchScoreRequest(
                graph=graph,
                input_node_ids=list(input_node_ids),
                output_node_id=output_node_id,
                artifact_paths=dict(artifact_paths),
                output_fields=list(output_fields) if output_fields else None,
                input_path=str(input_path),
                result_path=str(Path(temp_dir) / "result.parquet"),
                operation=operation,
            )
            worker_config = worker_config_for_memory_policy(
                memory_limit_bytes=budget.memory_limit_bytes,
                timeout_seconds=deploy_batch_timeout_seconds(),
                process_name=DEPLOY_BATCH_PROCESS_NAME,
            )
        except BaseException as exc:
            _remove_batch_temp_dir(temp_dir, primary_error=exc)
            raise
    except BaseException:
        execution_context.release_admission(preserve_primary_error=True)
        raise
    return BatchScorePlan(
        request=request,
        budget=budget,
        execution_context=execution_context,
        worker_config=worker_config,
        temp_dir=temp_dir,
    )


def accept_batch_outcome(plan: BatchScorePlan, outcome: object) -> BatchScoreResult:
    """Validate one child outcome and the parquet it claims to have written."""
    if not isinstance(outcome, BatchScoreOutcome):
        raise BatchScoreError(
            "error",
            f"Deploy batch worker returned {type(outcome).__name__}, not a BatchScoreOutcome.",
        )
    if outcome.failure_kind is not None:
        raise BatchScoreError(
            outcome.failure_kind,
            outcome.detail or "Deploy batch scoring failed.",
            outcome.payload,
        )
    result_path = plan.request.result_path
    if outcome.row_count is None or not Path(result_path).is_file():
        raise BatchScoreError("error", "Deploy batch worker did not produce its scored rows.")
    try:
        row_count = _parquet_row_count(result_path)
    except Exception as exc:
        raise BatchScoreError(
            "error",
            f"Deploy batch worker wrote an unreadable result file: {exc}",
        ) from exc
    if row_count != outcome.row_count:
        raise BatchScoreError(
            "error",
            f"Deploy batch worker reported {outcome.row_count} rows but wrote {row_count}.",
        )
    return BatchScoreResult(
        result_path=result_path,
        row_count=row_count,
        execution_metrics=outcome.execution_metrics,
    )
