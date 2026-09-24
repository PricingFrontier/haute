"""Provider-neutral background jobs for explicit input snapshots.

A Data Input has one snapshot. A structured API Input (JSON, JSONL, NDJSON,
XML) has one per emitting table; its requests act on the node's tables
together, and its build shreds the source once in a hard-capped worker.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

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
    InputCacheTableStatus,
)

if TYPE_CHECKING:
    from haute._json_shred._snapshots import ApiInputSnapshotSource

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
# The API-input table identities each running API Input build writes, keyed
# by table digest, so a data point can tell that its own table is building.
_building_tables: dict[str, str] = {}


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
        # Canonicalise, don't merely validate: the snapshot identity, the
        # freshness signature, and the bytes the build reads all follow this
        # path. Execution resolves the same locator through this resolver with
        # these arguments (``canonical_dataframe_execution_graph``), so
        # anchoring it any other way here would publish a generation under an
        # identity execution never looks up, built from a file it never reads.
        try:
            config["path"] = str(
                resolve_runtime_file_path(
                    str(config["path"]),
                    pipeline_dir=_pipeline_base_dir(),
                    project_root=_project_root(),
                    prefer="project",
                    enforce_project_root=True,
                )
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


def _invalid_api_input() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail="invalid_input_config: The API Input configuration is invalid.",
    )


def _safe_api_input(body: InputCacheSourceRequest) -> ApiInputSnapshotSource:
    """Validate a structured API Input config and name its tables' identities."""
    from haute._api_input_schema import ApiInputSchemaError, is_json_api_input_path
    from haute._json_shred._snapshots import api_input_snapshot_source

    config = body.config
    path = config.get("path")
    if (
        not isinstance(path, str)
        or not path
        or not is_json_api_input_path(path)
        or not isinstance(config.get("tables"), list)
    ):
        raise _invalid_api_input()
    try:
        data_path = resolve_runtime_file_path(
            path,
            pipeline_dir=_pipeline_base_dir(),
            project_root=_project_root(),
            prefer="project",
            enforce_project_root=True,
        )
    except RuntimePathError as exc:
        raise runtime_path_http_exception(exc) from None
    try:
        source = api_input_snapshot_source(config, data_path)
    except (ApiInputSchemaError, TypeError, ValueError, KeyError):
        raise _invalid_api_input() from None
    if not source.tables:
        raise HTTPException(
            status_code=400,
            detail=(
                "invalid_input_config: The API Input emits no table; tick 'emit' and at "
                "least one column on a table."
            ),
        )
    return source


def api_input_table_build_running(identity_digest: str) -> bool:
    """Whether a running API Input build writes the table *identity_digest*."""
    with _start_lock:
        job_id = _building_tables.get(identity_digest)
    if job_id is None:
        return False
    job = _store.get_job(job_id)
    return job is not None and job.get("status") == "running"


def _api_input_status(
    source: ApiInputSnapshotSource, *, include_running_build: bool = True
) -> InputCacheSnapshotStatusResponse:
    """The node's tables, each with its own state, and their summary.

    A running build of the node reports every table not yet ready as
    building; the build's own completion reads the tables as they are.
    """
    from haute._json_shred._snapshots import (
        api_input_source_signature,
        api_input_table_statuses,
    )

    building = include_running_build and input_snapshot_build_running(source.group_digest)
    statuses = api_input_table_statuses(
        source,
        _cache_store(),
        source_signature=api_input_source_signature(source.data_path),
    )
    tables = [
        InputCacheTableStatus(
            label=table.label,
            identity_digest=table.identity.digest,
            state="building" if building and status.state != "ready" else status.state,
            freshness=status.freshness,
            generation=_generation_payload(status.generation),
        )
        for table, status in statuses
    ]
    states = {status.state for _table, status in statuses}
    state: str
    if building:
        state = "building"
    elif "corrupt" in states:
        state = "corrupt"
    elif states == {"ready"}:
        state = "ready"
    else:
        state = "missing"
    ready_freshness = {status.freshness for _table, status in statuses if status.state == "ready"}
    freshness: Literal["fresh", "stale", "unknown"]
    if "stale" in ready_freshness:
        freshness = "stale"
    elif state == "ready" and ready_freshness == {"fresh"}:
        freshness = "fresh"
    else:
        freshness = "unknown"
    return InputCacheSnapshotStatusResponse(
        identity_digest=source.group_digest,
        state=state,  # type: ignore[arg-type]
        freshness=freshness,
        generation=None,
        tables=tables,
    )


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


def input_snapshot_build_running(identity_digest: str) -> bool:
    """Whether an input-snapshot build for *identity_digest* is running."""
    active = _singleflight.active(identity_digest)
    if active is None:
        return False
    active_job = _store.get_job(active.job_id)
    return active_job is not None and active_job.get("status") == "running"


def _status_for_config(
    config: dict[str, Any],
    identity: SourceCacheIdentity,
) -> InputCacheSnapshotStatusResponse:
    signature = source_signature(config, base_dir=_pipeline_base_dir())
    cache_status = _cache_store().status(identity, source_signature=signature)
    if input_snapshot_build_running(identity.digest):
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


def _supervise_api_input_build(
    *,
    source: ApiInputSnapshotSource,
    refresh: bool,
    execution_context: ExecutionContext,
    token: Any,
) -> None:
    """Build the node's missing or stale tables (all of them on refresh) in a capped worker."""
    from haute._json_shred._snapshots import (
        api_input_source_signature,
        api_input_table_statuses,
        run_supervised_api_input_build,
    )

    store = _cache_store()
    statuses = api_input_table_statuses(
        source, store, source_signature=api_input_source_signature(source.data_path)
    )
    labels = [
        table.label
        for table, status in statuses
        if refresh
        or not (
            status.state == "ready"
            and status.freshness in ("fresh", "unknown")
            and status.generation is not None
        )
    ]
    if not labels:
        return
    budget = isolated_execution_budget(execution_context)

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
    try:
        run_supervised_api_input_build(
            source,
            labels,
            store=store,
            profile=ExecutionProfile.LAZY_SINK,
            budget=budget,
            worker_config=worker_config,
            spawn=run_isolated_worker,
        )
    except Exception as exc:
        raise _admitted_eager_failure(exc, budget=budget, token=token) from exc


def _run_build(
    *,
    job_id: str,
    config: dict[str, Any],
    identity: SourceCacheIdentity | None,
    key: str,
    refresh: bool,
    profile: ExecutionProfile,
    build_class: BuildClass,
    store: Any,
    lifecycle: JobLifecycle,
    jobs: CancellableJobRegistry,
    singleflight: SingleFlightCoordinator,
    token: Any,
    api_input: ApiInputSnapshotSource | None = None,
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
        if api_input is not None:
            execution_context = create_admitted_execution_context(
                operation="input_snapshot_build",
                profile=profile,
                job_id=job_id,
                cancellation_token=token.execution_token,
            )
            _supervise_api_input_build(
                source=api_input,
                refresh=refresh,
                execution_context=execution_context,
                token=token,
            )
            timeout_timer.cancel()
            _complete_api_input_job(job_id, api_input, lifecycle, store, started_at)
            return
        assert identity is not None
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
        singleflight.release(key, job_id=job_id)
        with _start_lock:
            _active_builds -= 1
            for table_digest in [
                digest for digest, owner in _building_tables.items() if owner == job_id
            ]:
                del _building_tables[table_digest]


def _complete_api_input_job(
    job_id: str,
    source: ApiInputSnapshotSource,
    lifecycle: JobLifecycle,
    store: Any,
    started_at: float,
) -> None:
    snapshot = _api_input_status(source, include_running_build=False)
    tables = snapshot.tables or []
    if snapshot.state != "ready":
        # A table the worker did not publish is a failed build, not a ready one.
        raise SourceCacheBuildError("the API Input build left a table unpublished")
    lifecycle.transition(
        job_id,
        to="completed",
        message="Input snapshot is ready.",
        fields={
            "snapshot": snapshot.model_dump(),
            "progress": {
                "phase": "completed",
                "rows": sum(table.generation.row_count for table in tables if table.generation),
                "batches": max(1, len(tables)),
                "bytes": sum(table.generation.size_bytes for table in tables if table.generation),
            },
        },
        elapsed_seconds=time.monotonic() - started_at,
    )


# The build classes a started build can have; "unsupported" never starts one.
_BuildableClass = Literal["bounded", "admitted_eager"]


def _chosen_build(config: dict[str, Any]) -> tuple[ExecutionProfile, _BuildableClass]:
    """How the server builds a Data Input's snapshot: the profile and build class.

    A format the registry reads in bounded slices streams through a lazy sink;
    one that needs an eager read is admitted eagerly and contained by the
    hard-capped worker. Raises for a config that cannot build a snapshot.
    """
    build_class = input_snapshot_build_class(
        config,
        base_dir=_pipeline_base_dir(),
        profile=ExecutionProfile.LAZY_SINK,
        allow_admitted_eager=True,
    )
    if build_class == "bounded":
        return ExecutionProfile.LAZY_SINK, build_class
    if build_class == "admitted_eager":
        return ExecutionProfile.PREVIEW_EAGER, build_class
    raise PolarsIoConfigError("This Data Input cannot build a snapshot.")


def _start_build(
    *,
    key: str,
    identity_payload: dict[str, object],
    refresh: bool,
    build_class: _BuildableClass,
    target_kwargs: dict[str, Any],
    table_digests: tuple[str, ...] = (),
) -> InputCacheBuildResponse:
    """Join the running build of *key*, or admit and start a new one."""
    global _active_builds

    with _start_lock:
        active = _singleflight.active(key)
        if active is not None:
            job = _store.get_job(active.job_id)
            if job is not None and job.get("status") == "running":
                return InputCacheBuildResponse(
                    job_id=active.job_id,
                    identity_digest=key,
                    status="running",
                    joined=True,
                    build_class=job["build_class"],
                )
            _singleflight.release(key, job_id=active.job_id)
            _jobs.release(active.job_id)

        if _active_builds >= _max_concurrent_builds():
            raise HTTPException(
                status_code=429,
                detail=("input_cache_busy: The input snapshot build limit is currently reached."),
            )

        initial_job: _InputCacheRunningJob = {
            "status": "running",
            "identity_digest": key,
            "identity": identity_payload,
            "refresh": refresh,
            "build_class": build_class,
            "progress": {"phase": "queued", "rows": 0, "batches": 0, "bytes": 0},
            "message": "Input snapshot build queued.",
        }
        job_id = _store.create_job(initial_job)
        _singleflight.acquire(key, job_id=job_id, kind="input_cache_build")
        token, _ = _jobs.register_latest(key, job_id)
        _active_builds += 1
        for table_digest in table_digests:
            _building_tables[table_digest] = job_id
        thread = threading.Thread(
            target=_run_build,
            kwargs={
                **target_kwargs,
                "job_id": job_id,
                "key": key,
                "refresh": refresh,
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
        identity_digest=key,
        status="running",
        joined=False,
        build_class=build_class,
    )


@router.post(
    "/build",
    response_model=InputCacheBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def build_input_cache(body: InputCacheBuildRequest) -> InputCacheBuildResponse:
    """Start or join an explicit snapshot build for one safe source identity."""
    if body.node_type == "apiInput":
        source = _safe_api_input(body)
        return _start_build(
            key=source.group_digest,
            identity_payload={
                "node_type": "apiInput",
                "path": str(source.data_path),
                "tables": [table.label for table in source.tables],
            },
            refresh=body.refresh,
            build_class="bounded",
            target_kwargs={
                "config": dict(body.config),
                "identity": None,
                "profile": ExecutionProfile.LAZY_SINK,
                "api_input": source,
            },
            table_digests=tuple(table.identity.digest for table in source.tables),
        )

    config, identity = _safe_config(body)
    try:
        profile, build_class = _chosen_build(config)
    except (PolarsIoConfigError, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="snapshot_build_unsupported: This Data Input cannot build a snapshot.",
        ) from None

    return _start_build(
        key=identity.digest,
        identity_payload=identity.payload,
        refresh=body.refresh,
        build_class=build_class,
        target_kwargs={
            "config": config,
            "identity": identity,
            "profile": profile,
        },
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
    if body.node_type == "apiInput":
        return _api_input_status(_safe_api_input(body))
    config, identity = _safe_config(body)
    return _status_for_config(config, identity)


@router.post("/clear", response_model=InputCacheSnapshotStatusResponse)
def clear_input_cache(
    body: InputCacheSourceRequest,
) -> InputCacheSnapshotStatusResponse:
    """Clear a Data Input's snapshot, or every table of an API Input."""
    if body.node_type == "apiInput":
        source = _safe_api_input(body)
        key = source.group_digest
        identities = [table.identity for table in source.tables]
    else:
        config, identity = _safe_config(body)
        key = identity.digest
        identities = [identity]
    # Linearize the active-build check, clear, and response with build
    # admission. Whichever request acquires this lock first has an unambiguous
    # outcome: an admitted build makes clear return 409, while a completed
    # clear precedes any newly admitted build.
    with _start_lock:
        active = _singleflight.active(key)
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
        for cleared in identities:
            _cache_store().clear(cleared)
        if body.node_type == "apiInput":
            return _api_input_status(source)
        return _status_for_config(config, identity)


def _reset_for_tests() -> None:
    """Reset process-local route coordination without touching snapshots."""
    global _jobs, _singleflight, _active_builds

    with _start_lock:
        _store.clear_all()
        _jobs = CancellableJobRegistry()
        _singleflight = SingleFlightCoordinator()
        _active_builds = 0
        _building_tables.clear()
        _source_store.cache_clear()
