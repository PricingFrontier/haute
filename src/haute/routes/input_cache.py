"""Provider-neutral background jobs for explicit Data Input snapshots."""

from __future__ import annotations

import dataclasses
import functools
import os
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from fastapi import APIRouter, HTTPException, status

from haute._credential_security import redact_sensitive_text
from haute._env import float_env, int_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_admitted_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._input_preparation import (
    InputPreparationOutcome,
    InputPreparationRequest,
    build_input_snapshot_worker,
)
from haute._input_providers import (
    build_input_snapshot,
    input_snapshot_build_class,
    source_cache_identity,
    source_signature,
)
from haute._logging import get_logger
from haute._path_resolution import RuntimePathError, resolve_runtime_file_path
from haute._polars_io_registry import PolarsIoConfigError, validate_data_input_config
from haute._project import _toml_configured_pipeline
from haute._source_cache import (
    BuildClass,
    SourceCacheBuildError,
    SourceCacheGeneration,
    SourceCacheIdentity,
    SourceCacheQuotaExceededError,
    SourceCacheStatus,
    SourceCacheStore,
    new_staging_token,
)
from haute._worker_isolation import (
    WorkerTerminalReason,
    isolated_worker_failure_is_memory,
    isolated_worker_memory_detail,
    run_isolated_worker,
    worker_config_for_memory_policy,
)
from haute.routes._background_jobs import CancellableJobRegistry, SingleFlightCoordinator
from haute.routes._job_lifecycle import JobLifecycle, require_job_status
from haute.routes._job_store import RunningJobFields, get_job_store
from haute.routes._memory_messages import memory_limit_user_message
from haute.routes._runtime_path_errors import runtime_path_http_exception
from haute.schemas import (
    InputCacheBuildRequest,
    InputCacheBuildResponse,
    InputCacheCancelResponse,
    InputCacheGenerationPayload,
    InputCacheJobStatusResponse,
    InputCacheProgress,
    InputCacheSnapshotStatusResponse,
    InputCacheSourceRequest,
)

router = APIRouter(prefix="/api/input-cache", tags=["input-cache"])
logger = get_logger(component="input_cache")


class _InputCacheQueuedProgress(TypedDict):
    phase: Literal["queued"]
    rows: int
    batches: int
    bytes: int


class _InputCacheRunningJob(RunningJobFields):
    identity_digest: str
    identity: dict[str, object]
    refresh: bool
    build_class: BuildClass
    progress: _InputCacheQueuedProgress


_store = get_job_store("input_cache")
_lifecycle = JobLifecycle(_store)
_jobs = CancellableJobRegistry()
_singleflight = SingleFlightCoordinator()
_start_lock = threading.RLock()
_active_builds = 0


def _build_timeout() -> float:
    """Return the cooperative snapshot-build deadline in seconds."""
    return float_env("HAUTE_BUILD_TIMEOUT", 1800.0)


def _max_concurrent_builds() -> int:
    return int_env("HAUTE_INPUT_CACHE_MAX_CONCURRENT_BUILDS", 4)


def _provider_error_diagnostic(
    exc: Exception,
    config: dict[str, Any],
) -> str:
    """Return a useful provider diagnostic without resolved credential material."""
    secret_references = {"DATABRICKS_TOKEN", "DATABRICKS_CLIENT_SECRET"}
    connection = config.get("connection")
    if isinstance(connection, str):
        secret_references.add(connection)
    return redact_sensitive_text(
        str(exc),
        known_secrets=(
            value for reference in secret_references if (value := os.environ.get(reference, ""))
        ),
    )


def _project_root() -> Path:
    return Path.cwd().resolve()


def _pipeline_base_dir() -> Path:
    root = _project_root()
    configured = _toml_configured_pipeline(root)
    return configured.parent.resolve() if configured is not None else root


@functools.cache
def _source_store(root: str) -> SourceCacheStore:
    return SourceCacheStore(Path(root))


def _cache_store() -> SourceCacheStore:
    return _source_store(str(_project_root()))


def _safe_config(
    body: InputCacheSourceRequest,
) -> tuple[dict[str, Any], SourceCacheIdentity]:
    try:
        config = validate_data_input_config(body.config)
    except (PolarsIoConfigError, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="invalid_input_config: The Data Input configuration is invalid.",
        ) from None

    if config["inputType"] in {"file", "lakehouse"}:
        try:
            resolve_runtime_file_path(
                str(config["path"]),
                pipeline_dir=_pipeline_base_dir(),
                project_root=_project_root(),
                prefer="pipeline",
                enforce_project_root=True,
            )
        except RuntimePathError as exc:
            raise runtime_path_http_exception(exc) from None

    if config["inputType"] == "database" and "uri" in config:
        from haute._database_io import (
            DatabaseConfigError,
            validate_sqlite_project_path,
        )

        try:
            validate_sqlite_project_path(
                str(config["uri"]),
                base_dir=_pipeline_base_dir(),
                project_root=_project_root(),
            )
        except DatabaseConfigError:
            raise HTTPException(
                status_code=400,
                detail="invalid_input_config: The Data Input configuration is invalid.",
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None

    try:
        identity = source_cache_identity(config, base_dir=_pipeline_base_dir())
    except (PolarsIoConfigError, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="invalid_input_config: The Data Input configuration is invalid.",
        ) from None
    return config, identity


def _generation_payload(
    generation: SourceCacheGeneration | None,
) -> InputCacheGenerationPayload | None:
    if generation is None:
        return None
    metadata = generation.metadata
    return InputCacheGenerationPayload(
        generation_id=generation.generation_id,
        row_count=metadata.row_count,
        column_count=metadata.column_count,
        columns=metadata.columns,
        size_bytes=metadata.size_bytes,
        created_at=metadata.created_at,
        build_class=metadata.build_class,
    )


def _snapshot_payload(
    identity: SourceCacheIdentity,
    cache_status: SourceCacheStatus,
    *,
    state: str | None = None,
) -> InputCacheSnapshotStatusResponse:
    return InputCacheSnapshotStatusResponse(
        identity_digest=identity.digest,
        state=state or cache_status.state,  # type: ignore[arg-type]
        freshness=cache_status.freshness,
        generation=_generation_payload(cache_status.generation),
    )


def _status_for_config(
    config: dict[str, Any],
    identity: SourceCacheIdentity,
) -> InputCacheSnapshotStatusResponse:
    signature = source_signature(config, base_dir=_pipeline_base_dir())
    cache_status = _cache_store().status(identity, source_signature=signature)
    active = _singleflight.active(identity.digest)
    if active is not None:
        active_job = _store.get_job(active.job_id)
        if active_job is not None and active_job.get("status") == "running":
            return _snapshot_payload(identity, cache_status, state="building")
    return _snapshot_payload(identity, cache_status)


def _progress_payload(job: Mapping[str, Any]) -> InputCacheProgress:
    raw = job.get("progress")
    if not isinstance(raw, dict):
        raw = {}
    started_at = float(job.get("started_at", job.get("created_at", time.time())))
    elapsed = float(job.get("elapsed_seconds", max(0.0, time.time() - started_at)))
    return InputCacheProgress(
        phase=raw.get("phase", "queued"),
        rows=raw.get("rows", 0),
        batches=raw.get("batches", 0),
        bytes=raw.get("bytes", 0),
        elapsed_seconds=elapsed,
    )


def _job_response(job_id: str, job: Mapping[str, Any]) -> InputCacheJobStatusResponse:
    return InputCacheJobStatusResponse(
        job_id=job_id,
        identity_digest=str(job["identity_digest"]),
        status=require_job_status(job),
        terminal_reason=job.get("terminal_reason"),
        message=str(job.get("message") or ""),
        refresh=bool(job.get("refresh", False)),
        build_class=job["build_class"],
        progress=_progress_payload(job),
        snapshot=job.get("snapshot"),
        error_code=job.get("error_code"),
    )


class _AdmittedEagerWorkerError(Exception):
    """Terminal outcome of one supervised admitted-eager build worker.

    Carries the lifecycle state, user-facing message, and job fields the
    supervisor already decided, so ``_run_build`` performs exactly one
    transition for a worker failure.
    """

    def __init__(
        self,
        *,
        terminal: WorkerTerminalReason,
        message: str,
        error_code: str,
        phase: str,
        fields: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.terminal = terminal
        self.message = message
        self.error_code = error_code
        self.phase = phase
        self.fields = fields or {}


def _is_quota_failure(exc: BaseException) -> bool:
    """Whether a worker failure is the store's quota rejection."""
    if isinstance(exc, SourceCacheQuotaExceededError):
        return True
    return getattr(exc, "remote_type", None) == "SourceCacheQuotaExceededError"


def _admitted_eager_failure(
    exc: BaseException,
    *,
    budget: IsolatedExecutionBudget,
    token: Any,
) -> _AdmittedEagerWorkerError:
    """Map one worker failure onto this job's terminal lifecycle state."""
    if isolated_worker_failure_is_memory(exc):
        return _AdmittedEagerWorkerError(
            terminal="memory_limited",
            message=(
                "The input snapshot build needs more memory than this server "
                "allows. Reduce the data size, or run on a server with more "
                "memory, then try again."
            ),
            error_code="memory_limit",
            phase="failed",
            fields={
                "error": str(exc),
                "error_detail": isolated_worker_memory_detail(
                    exc,
                    operation=budget.operation,
                    memory_limit_bytes=budget.memory_limit_bytes,
                ),
            },
        )
    if _is_quota_failure(exc):
        return _AdmittedEagerWorkerError(
            terminal="error",
            message="Input snapshot exceeds the configured cache quota.",
            error_code="cache_quota_exceeded",
            phase="failed",
        )
    reason = token.terminal_reason if token.cancelled else getattr(exc, "terminal_reason", None)
    if reason in {"cancelled", "superseded", "timed_out"}:
        timed_out = reason == "timed_out"
        return _AdmittedEagerWorkerError(
            terminal=cast(WorkerTerminalReason, reason),
            message=(
                "Input snapshot build exceeded its deadline."
                if timed_out
                else "Input snapshot build was cancelled."
            ),
            error_code="build_timed_out" if timed_out else "build_cancelled",
            phase="cancelled",
        )
    return _AdmittedEagerWorkerError(
        terminal="error",
        message="Input snapshot build failed.",
        error_code="build_failed",
        phase="failed",
    )


def _supervise_admitted_eager_build(
    *,
    config: dict[str, Any],
    identity: SourceCacheIdentity,
    refresh: bool,
    profile: ExecutionProfile,
    execution_context: ExecutionContext,
    token: Any,
) -> SourceCacheGeneration:
    """Run one admitted-eager explicit build in a hard-capped spawn worker.

    The parent chooses the generation id and the staging token, so after a
    worker failure or death it reconciles exactly that build: a generation the
    child already published is the job's result, and anything unpublished is
    removed without touching the previous current generation.
    """
    store = _cache_store()
    budget = isolated_execution_budget(execution_context)
    generation_id = str(uuid.uuid4())
    staging_token = new_staging_token()

    def stop_reason() -> WorkerTerminalReason | None:
        if not token.cancelled:
            return None
        reason = token.terminal_reason
        return reason if reason in {"cancelled", "superseded", "timed_out"} else "cancelled"

    worker_config = dataclasses.replace(
        worker_config_for_memory_policy(
            memory_limit_bytes=budget.memory_limit_bytes,
            timeout_seconds=_build_timeout(),
            stop_reason=stop_reason,
            process_name="haute-input-cache-build",
        ),
        require_memory_limit=True,
    )
    request = InputPreparationRequest(
        config=dict(config),
        base_dir=str(_pipeline_base_dir()),
        cache_root=str(store.root),
        project_root=str(_project_root()),
        profile=profile,
        refresh=refresh,
        generation_id=generation_id,
        staging_token=staging_token,
        retained_generation_ids=tuple(sorted(store.leased_generation_ids(identity))),
    )
    try:
        outcome = run_isolated_worker(
            build_input_snapshot_worker,
            request,
            budget,
            config=worker_config,
        )
    except BaseException as exc:
        settled = store.reconcile_unpublished(identity, generation_id, staging_token)
        # Reconcile first so nothing is left behind, but never convert a base
        # exception (an interrupt, a system exit) into this build's success.
        if not isinstance(exc, Exception):
            raise
        if settled == "published":
            published = store.open_generation(identity)
            # The child deferred retirement; retire here, where this process's
            # own lease counts are visible.
            store.retire_unleased(identity)
            return published
        raise _admitted_eager_failure(exc, budget=budget, token=token) from exc
    if not isinstance(outcome, InputPreparationOutcome):
        raise RuntimeError("input snapshot build worker returned an unexpected outcome")
    generation = store.open_generation(identity)
    # The child deferred retirement; retire here, where this process's own lease
    # counts are visible.
    store.retire_unleased(identity)
    return generation


def _run_build(
    *,
    job_id: str,
    config: dict[str, Any],
    identity: SourceCacheIdentity,
    refresh: bool,
    profile: ExecutionProfile,
    build_class: BuildClass,
    store: Any,
    lifecycle: JobLifecycle,
    jobs: CancellableJobRegistry,
    singleflight: SingleFlightCoordinator,
    token: Any,
) -> None:
    global _active_builds

    started_at = time.monotonic()
    execution_context: ExecutionContext | None = None
    timeout = _build_timeout()
    deadline = started_at + timeout
    timeout_timer = threading.Timer(
        timeout,
        lambda: jobs.cancel(job_id, reason="timed_out"),
    )
    timeout_timer.daemon = True
    timeout_timer.start()

    def progress(units: int) -> None:
        job = store.get_job(job_id)
        if job is None or job.get("status") != "running":
            return
        previous = job.get("progress")
        previous = previous if isinstance(previous, dict) else {}
        store.atomic_update(
            job_id,
            {
                "progress": {
                    "phase": "building",
                    "rows": int(previous.get("rows", 0)) + units,
                    "batches": int(previous.get("batches", 0)) + 1,
                    "bytes": int(previous.get("bytes", 0)),
                }
            },
            expected_status="running",
        )

    try:
        store.update_job(
            job_id,
            started_at=time.time(),
            progress={"phase": "building", "rows": 0, "batches": 0, "bytes": 0},
        )
        if build_class == "admitted_eager":
            execution_context = create_admitted_execution_context(
                operation="input_snapshot_build",
                profile=profile,
                job_id=job_id,
                cancellation_token=token.execution_token,
            )
            # An admitted-eager build materialises: it runs in a hard-capped
            # spawn worker, never on this server thread. The child owns its own
            # progress, so this job keeps the `building` phase until completion.
            generation = _supervise_admitted_eager_build(
                config=config,
                identity=identity,
                refresh=refresh,
                profile=profile,
                execution_context=execution_context,
                token=token,
            )
        else:
            generation = build_input_snapshot(
                config,
                store=_cache_store(),
                base_dir=_pipeline_base_dir(),
                profile=profile,
                refresh=refresh,
                cancellation=token.event,
                deadline=deadline,
                progress=progress,
                execution_context=execution_context,
            )
        timeout_timer.cancel()
        metadata = generation.metadata
        snapshot = InputCacheSnapshotStatusResponse(
            identity_digest=identity.digest,
            state="ready",
            freshness=("unknown" if metadata.source_signature is None else "fresh"),
            generation=_generation_payload(generation),
        )
        lifecycle.transition(
            job_id,
            to="completed",
            message="Input snapshot is ready.",
            fields={
                "snapshot": snapshot.model_dump(),
                "progress": {
                    "phase": "completed",
                    "rows": metadata.row_count,
                    "batches": max(1, int(store.get_job(job_id)["progress"]["batches"])),
                    "bytes": metadata.size_bytes,
                },
            },
            elapsed_seconds=time.monotonic() - started_at,
        )
    except _AdmittedEagerWorkerError as failure:
        logger.warning(
            "input_cache_worker_build_stopped",
            job_id=job_id,
            reason=failure.terminal,
            error_code=failure.error_code,
        )
        lifecycle.transition(
            job_id,
            to=failure.terminal,
            message=failure.message,
            fields={
                **failure.fields,
                "error_code": failure.error_code,
                "progress": {
                    **_progress_payload(store.require_job(job_id)).model_dump(
                        exclude={"elapsed_seconds"}
                    ),
                    "phase": failure.phase,
                },
            },
            elapsed_seconds=time.monotonic() - started_at,
        )
    except SourceCacheQuotaExceededError as exc:
        logger.warning(
            "input_cache_quota_rejected",
            job_id=job_id,
            error_type=type(exc).__name__,
        )
        lifecycle.transition(
            job_id,
            to="error",
            message="Input snapshot exceeds the configured cache quota.",
            fields={
                "error_code": "cache_quota_exceeded",
                "progress": {
                    **_progress_payload(store.require_job(job_id)).model_dump(
                        exclude={"elapsed_seconds"}
                    ),
                    "phase": "failed",
                },
            },
            elapsed_seconds=time.monotonic() - started_at,
        )
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        logger.warning(
            "input_cache_memory_limited",
            job_id=job_id,
            error_type=type(exc).__name__,
        )
        lifecycle.transition(
            job_id,
            to="memory_limited",
            # Shared user-facing shape (matching training and auto-range);
            # str(exc) names the internal operation and stays diagnostic.
            message=memory_limit_user_message(exc, operation_noun="The input snapshot build"),
            fields={
                "error": str(exc),
                "error_code": "memory_limit",
                "progress": {
                    **_progress_payload(store.require_job(job_id)).model_dump(
                        exclude={"elapsed_seconds"}
                    ),
                    "phase": "failed",
                },
            },
            elapsed_seconds=time.monotonic() - started_at,
        )
    except (SourceCacheBuildError, ExecutionCancelledError) as exc:
        reason = token.terminal_reason
        logger.warning(
            "input_cache_build_stopped",
            job_id=job_id,
            reason=reason or "build_failed",
            error_type=type(exc).__name__,
        )
        if reason in {"cancelled", "superseded", "timed_out"}:
            timed_out = reason == "timed_out"
            lifecycle.transition(
                job_id,
                to=reason,
                message=(
                    "Input snapshot build exceeded its deadline."
                    if timed_out
                    else "Input snapshot build was cancelled."
                ),
                fields={
                    "error_code": ("build_timed_out" if timed_out else "build_cancelled"),
                    "progress": {
                        **_progress_payload(store.require_job(job_id)).model_dump(
                            exclude={"elapsed_seconds"}
                        ),
                        "phase": "cancelled",
                    },
                },
                elapsed_seconds=time.monotonic() - started_at,
            )
        else:
            lifecycle.transition(
                job_id,
                to="error",
                message="Input snapshot build failed.",
                fields={
                    "error_code": "build_failed",
                    "progress": {
                        **_progress_payload(store.require_job(job_id)).model_dump(
                            exclude={"elapsed_seconds"}
                        ),
                        "phase": "failed",
                    },
                },
                elapsed_seconds=time.monotonic() - started_at,
            )
    except Exception as exc:
        logger.error(
            "input_cache_build_failed",
            job_id=job_id,
            error_type=type(exc).__name__,
            error=_provider_error_diagnostic(exc, config),
        )
        lifecycle.transition(
            job_id,
            to="error",
            message="Input snapshot build failed.",
            fields={
                "error_code": "build_failed",
                "progress": {
                    **_progress_payload(store.require_job(job_id)).model_dump(
                        exclude={"elapsed_seconds"}
                    ),
                    "phase": "failed",
                },
            },
            elapsed_seconds=time.monotonic() - started_at,
        )
    finally:
        timeout_timer.cancel()
        if execution_context is not None:
            execution_context.release_admission()
        jobs.release(job_id)
        singleflight.release(identity.digest, job_id=job_id)
        with _start_lock:
            _active_builds -= 1


@router.post(
    "/build",
    response_model=InputCacheBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def build_input_cache(body: InputCacheBuildRequest) -> InputCacheBuildResponse:
    """Start or join an explicit snapshot build for one safe source identity."""
    global _active_builds

    config, identity = _safe_config(body)
    try:
        profile = ExecutionProfile(body.profile)
        build_class = input_snapshot_build_class(
            config,
            base_dir=_pipeline_base_dir(),
            profile=profile,
        )
    except (PolarsIoConfigError, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=(
                "snapshot_build_unsupported: This Data Input cannot build a "
                "snapshot in the requested profile."
            ),
        ) from None

    with _start_lock:
        active = _singleflight.active(identity.digest)
        if active is not None:
            job = _store.get_job(active.job_id)
            if job is not None and job.get("status") == "running":
                return InputCacheBuildResponse(
                    job_id=active.job_id,
                    identity_digest=identity.digest,
                    status="running",
                    joined=True,
                )
            _singleflight.release(identity.digest, job_id=active.job_id)
            _jobs.release(active.job_id)

        if _active_builds >= _max_concurrent_builds():
            raise HTTPException(
                status_code=429,
                detail=("input_cache_busy: The input snapshot build limit is currently reached."),
            )

        initial_job: _InputCacheRunningJob = {
            "status": "running",
            "identity_digest": identity.digest,
            "identity": identity.payload,
            "refresh": body.refresh,
            "build_class": build_class,
            "progress": {"phase": "queued", "rows": 0, "batches": 0, "bytes": 0},
            "message": "Input snapshot build queued.",
        }
        job_id = _store.create_job(initial_job)
        _singleflight.acquire(identity.digest, job_id=job_id, kind="input_cache_build")
        token, _ = _jobs.register_latest(identity.digest, job_id)
        _active_builds += 1
        thread = threading.Thread(
            target=_run_build,
            kwargs={
                "job_id": job_id,
                "config": config,
                "identity": identity,
                "refresh": body.refresh,
                "profile": profile,
                "build_class": build_class,
                "store": _store,
                "lifecycle": _lifecycle,
                "jobs": _jobs,
                "singleflight": _singleflight,
                "token": token,
            },
            daemon=True,
            name=f"haute-input-cache-{job_id}",
        )
        thread.start()

    return InputCacheBuildResponse(
        job_id=job_id,
        identity_digest=identity.digest,
        status="running",
        joined=False,
    )


@router.get("/jobs/{job_id}", response_model=InputCacheJobStatusResponse)
def get_input_cache_job(job_id: str) -> InputCacheJobStatusResponse:
    return _job_response(job_id, _store.require_job(job_id))


@router.delete(
    "/jobs/{job_id}",
    response_model=InputCacheCancelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_input_cache_job(job_id: str) -> InputCacheCancelResponse:
    job = _store.require_job(job_id)
    job_status = require_job_status(job)
    requested = job_status == "running" and _jobs.cancel(job_id)
    latest = _store.require_job(job_id)
    return InputCacheCancelResponse(
        job_id=job_id,
        cancellation_requested=requested,
        status=require_job_status(latest),
    )


@router.post("/status", response_model=InputCacheSnapshotStatusResponse)
def get_input_cache_status(
    body: InputCacheSourceRequest,
) -> InputCacheSnapshotStatusResponse:
    config, identity = _safe_config(body)
    return _status_for_config(config, identity)


@router.post("/clear", response_model=InputCacheSnapshotStatusResponse)
def clear_input_cache(
    body: InputCacheSourceRequest,
) -> InputCacheSnapshotStatusResponse:
    config, identity = _safe_config(body)
    # Linearize the active-build check, clear, and response with build
    # admission. Whichever request acquires this lock first has an unambiguous
    # outcome: an admitted build makes clear return 409, while a completed
    # clear precedes any newly admitted build.
    with _start_lock:
        active = _singleflight.active(identity.digest)
        if active is not None:
            job = _store.get_job(active.job_id)
            if job is not None and job.get("status") == "running":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "snapshot_build_active: Cancel the active snapshot "
                        "build before clearing it."
                    ),
                )
        _cache_store().clear(identity)
        return _status_for_config(config, identity)


def _reset_for_tests() -> None:
    """Reset process-local route coordination without touching snapshots."""
    global _jobs, _singleflight, _active_builds

    with _start_lock:
        _store.clear_all()
        _jobs = CancellableJobRegistry()
        _singleflight = SingleFlightCoordinator()
        _active_builds = 0
        _source_store.cache_clear()
