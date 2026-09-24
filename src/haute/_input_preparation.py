"""Automatic snapshot preparation planned before an execution runs.

Acquisition stays a distinct, contained operation: this module never invents a
new read path. It checks the store's freshness, decides whether the same
explicit build (:func:`haute._input_providers.build_input_snapshot` for a Data
Input, :func:`haute._json_shred._snapshots.build_api_input_tables` for a
structured API Input's tables) has to run, and then runs it under a hard memory
cap — in the current process when that process already runs inside an isolated
worker, otherwise in a spawned hard-capped worker admitted from the execution's
own budget.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from haute._env import float_env
from haute._execution_admission import (
    IsolatedExecutionBudget,
    create_isolated_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionProfile,
)
from haute._logging import get_logger
from haute._native_memory_limit import current_native_memory_backend
from haute._polars_io_registry import PolarsIoConfigError, data_input_is_direct
from haute._source_cache import (
    SourceCacheBuildError,
    SourceCacheCorruptError,
    SourceCacheGeneration,
    SourceCacheStatus,
    SourceCacheStore,
    new_staging_token,
)
from haute._worker_isolation import (
    IsolatedWorkerRemoteError,
    WorkerTerminalReason,
    isolated_worker_failure_is_memory,
    process_memory_caps_supported,
    run_isolated_worker,
    worker_config_for_memory_policy,
)
from haute.errors import InputPreparationError

logger = get_logger(component="input_preparation")

PreparationAction = Literal["reused", "built", "refreshed"]
PreparationExecution = Literal["in_process", "worker"]

_DEFAULT_BUILD_TIMEOUT_SECONDS = 30 * 60
_REMEDIATION = (
    "Build this Data Input's snapshot from the Data Input panel, or give the "
    "execution more memory headroom, and try again."
)
# A host without a native memory cap and a cancelled build need different
# actions from the operator.
_REMEDIATION_BY_REASON: Mapping[str, str] = {
    "cap_unavailable": (
        "This host cannot install the native memory cap an automatic snapshot "
        "build requires. Build this Data Input's snapshot explicitly from the "
        "Data Input panel, or run on a host that supports the cap."
    ),
    "memory_limited": (
        "Preparing this Data Input's snapshot ran out of memory. Give the "
        "execution more memory headroom, or build the snapshot explicitly from "
        "the Data Input panel, and try again."
    ),
    "cancelled": "Preparing this Data Input's snapshot was cancelled. Try again.",
    "timed_out": (
        "Preparing this Data Input's snapshot exceeded its time budget. Build it "
        "from the Data Input panel, or raise the preparation timeout, and try again."
    ),
}
_API_INPUT_REMEDIATION = (
    "Build this API Input's tables from the API Input panel, or give the "
    "execution more memory headroom, and try again."
)
_API_INPUT_REMEDIATION_BY_REASON: Mapping[str, str] = {
    "cap_unavailable": (
        "This host cannot install the native memory cap an automatic snapshot "
        "build requires. Build this API Input's tables explicitly from the API "
        "Input panel, or run on a host that supports the cap."
    ),
    "memory_limited": (
        "Preparing this API Input's tables ran out of memory. Give the execution "
        "more memory headroom, or build them explicitly from the API Input "
        "panel, and try again."
    ),
    "cancelled": "Preparing this API Input's tables was cancelled. Try again.",
    "timed_out": (
        "Preparing this API Input's tables exceeded its time budget. Build them "
        "from the API Input panel, or raise the preparation timeout, and try again."
    ),
}


@dataclass(slots=True)
class _SingleFlightEntry:
    """The in-flight slot for one identity, carrying the owner's outcome."""

    event: threading.Event = dataclasses.field(default_factory=threading.Event)
    error: BaseException | None = None


_SINGLE_FLIGHT_LOCK = threading.Lock()
_SINGLE_FLIGHT: dict[str, _SingleFlightEntry] = {}


def _build_timeout_seconds() -> float:
    """Wall-clock budget for one automatic snapshot build."""
    return float_env("HAUTE_INPUT_PREPARATION_TIMEOUT_SECONDS", _DEFAULT_BUILD_TIMEOUT_SECONDS)


def _build_deadline() -> float:
    """Monotonic deadline for one automatic snapshot build."""
    return time.monotonic() + _build_timeout_seconds()


def preparation_base_dir(graph: Any) -> Path | None:
    """Anchor directory used by preparation, matching ``resolve_data_input``."""
    from haute._builders import _configured_pipeline_dir
    from haute._cache import _pipeline_dir

    return _pipeline_dir(graph) or _configured_pipeline_dir()


def _cache_root() -> Path:
    from haute._sandbox import _get_project_root

    return _get_project_root().resolve()


@dataclass(frozen=True, slots=True)
class InputPreparationRecord:
    """Per-input diagnostic record of one execution's automatic preparation."""

    node_id: str
    identity_digest: str
    action: PreparationAction
    build_class: str
    execution: PreparationExecution
    memory_limit_bytes: int | None
    elapsed_seconds: float
    row_count: int | None
    size_bytes: int | None
    generation_id: str | None
    warning_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "identity_digest": self.identity_digest,
            "action": self.action,
            "build_class": self.build_class,
            "execution": self.execution,
            "memory_limit_bytes": self.memory_limit_bytes,
            "elapsed_seconds": self.elapsed_seconds,
            "row_count": self.row_count,
            "size_bytes": self.size_bytes,
            "generation_id": self.generation_id,
            "warning_code": self.warning_code,
        }


@dataclass(frozen=True, slots=True)
class InputPreparationRequest:
    """Picklable request handed to the spawned build worker."""

    config: dict[str, Any]
    base_dir: str
    cache_root: str
    project_root: str
    profile: ExecutionProfile
    refresh: bool
    generation_id: str
    staging_token: str


@dataclass(frozen=True, slots=True)
class InputPreparationOutcome:
    """Picklable outcome of one spawned build."""

    generation_id: str
    row_count: int
    size_bytes: int


def build_input_snapshot_worker(
    request: InputPreparationRequest,
    budget: IsolatedExecutionBudget,
) -> InputPreparationOutcome:
    """Spawn entrypoint: run the explicit build under the child's own hard cap."""
    from haute._input_providers import build_input_snapshot
    from haute._sandbox import set_project_root

    set_project_root(Path(request.project_root))
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        context.checkpoint(label="input_snapshot_preparation")
        generation = build_input_snapshot(
            request.config,
            store=SourceCacheStore(request.cache_root),
            base_dir=request.base_dir,
            profile=request.profile,
            refresh=request.refresh,
            execution_context=context,
            generation_id=request.generation_id,
            staging_token=request.staging_token,
            allow_admitted_eager=True,
            defer_retirement=True,
        )
        return InputPreparationOutcome(
            generation_id=generation.generation_id,
            row_count=generation.metadata.row_count,
            size_bytes=generation.metadata.size_bytes,
        )
    finally:
        if context is not None:
            context.release_admission(preserve_primary_error=True)


InputKind = Literal["data_input", "api_input"]


def _is_structured_api_input(config: Mapping[str, Any]) -> bool:
    """A JSON, JSONL, NDJSON, or XML API Input with a v2 table schema."""
    from haute._api_input_schema import is_json_api_input_path

    path = config.get("path")
    return (
        isinstance(path, str)
        and bool(path)
        and is_json_api_input_path(path)
        and isinstance(config.get("tables"), list)
    )


def snapshot_backed_inputs(
    order: Iterable[str],
    node_map: Mapping[str, Any],
) -> list[tuple[str, InputKind, Mapping[str, Any]]]:
    """The inputs in *order* that execute from input snapshots, in execution order.

    Snapshot-backed Data Inputs and structured API Inputs, each with its
    runtime path resolved through the node builder's project-root guard.
    """
    from haute._builders import _config_with_resolved_data_path
    from haute.schemas import NodeType

    found: list[tuple[str, InputKind, Mapping[str, Any]]] = []
    for node_id in order:
        node = node_map.get(node_id)
        if node is None:
            continue
        if node.data.nodeType == NodeType.API_INPUT:
            if _is_structured_api_input(node.data.config):
                found.append(
                    (node_id, "api_input", _config_with_resolved_data_path(node.data.config))
                )
            continue
        if node.data.nodeType != NodeType.DATA_INPUT:
            continue
        config = node.data.config
        if not config.get("inputType"):
            # A config that names no provider is not a resolvable Data Input;
            # the node builder owns that rejection, not preparation.
            continue
        if data_input_is_direct(config):
            continue
        # Resolve the runtime path through the same project-root guard the node
        # builder uses, so a path escape is refused here — with its own error —
        # rather than inside a build the guard never saw.
        found.append((node_id, "data_input", _config_with_resolved_data_path(config)))
    return found


def _acquire_single_flight(digest: str) -> _SingleFlightEntry | None:
    """Own the in-flight slot for *digest*, or return the entry to wait on."""
    with _SINGLE_FLIGHT_LOCK:
        waiting = _SINGLE_FLIGHT.get(digest)
        if waiting is not None:
            return waiting
        _SINGLE_FLIGHT[digest] = _SingleFlightEntry()
        return None


def _release_single_flight(digest: str, error: BaseException | None = None) -> None:
    with _SINGLE_FLIGHT_LOCK:
        entry = _SINGLE_FLIGHT.pop(digest, None)
    if entry is not None:
        # Record before waking: a waiter reads the owner's failure rather than
        # repeating a build that has already been refused.
        entry.error = error
        entry.event.set()


def _reused_record(
    *,
    node_id: str,
    digest: str,
    build_class: str,
    generation: SourceCacheGeneration,
    elapsed_seconds: float,
    warning_code: str | None,
) -> InputPreparationRecord:
    return InputPreparationRecord(
        node_id=node_id,
        identity_digest=digest,
        action="reused",
        build_class=build_class,
        execution="in_process",
        memory_limit_bytes=None,
        elapsed_seconds=elapsed_seconds,
        row_count=generation.metadata.row_count,
        size_bytes=generation.metadata.size_bytes,
        generation_id=generation.generation_id,
        warning_code=warning_code,
    )


def prepare_input_snapshots(
    order: Iterable[str],
    node_map: Mapping[str, Any],
    *,
    profile: ExecutionProfile | None,
    execution_context: ExecutionContext | None,
    base_dir: str | Path | None,
    schema_only: bool,
    store: SourceCacheStore | None = None,
    spawn: Any = None,
    deadline: float | None = None,
) -> tuple[InputPreparationRecord, ...]:
    """Reuse, build, or refresh every snapshot-backed input in *order*.

    Returns the per-input records, which are also recorded on the execution
    context so the terminal diagnostics carry them. *deadline* is the
    caller's monotonic deadline, which bounds every build and wait.
    """
    if schema_only:
        return ()
    if execution_context is None or execution_context.admission is None:
        return ()

    from haute._input_providers import (
        input_snapshot_build_class,
        input_snapshot_warning_code,
        source_cache_identity,
        source_signature,
    )

    candidates = snapshot_backed_inputs(order, node_map)
    if not candidates:
        return ()

    cache_store = store or SourceCacheStore(_cache_root())
    effective_profile = profile if profile is not None else execution_context.profile
    records: list[InputPreparationRecord] = []
    for node_id, kind, config in candidates:
        if kind == "api_input":
            for record in _prepare_api_input(
                node_id=node_id,
                config=config,
                store=cache_store,
                profile=effective_profile,
                execution_context=execution_context,
                spawn=spawn or run_isolated_worker,
                deadline=deadline,
            ):
                records.append(record)
                execution_context.record_input_preparation(record)
            continue
        record = _prepare_one(
            node_id=node_id,
            config=config,
            store=cache_store,
            profile=effective_profile,
            execution_context=execution_context,
            base_dir=base_dir,
            spawn=spawn or run_isolated_worker,
            identity=source_cache_identity(config, base_dir=base_dir),
            signature=source_signature(config, base_dir=base_dir),
            build_class=input_snapshot_build_class(
                config,
                base_dir=base_dir,
                profile=effective_profile,
                allow_admitted_eager=True,
            ),
            warning_code=input_snapshot_warning_code(config, base_dir=base_dir),
            deadline=deadline,
        )
        records.append(record)
        execution_context.record_input_preparation(record)
    return tuple(records)


def _prepare_one(
    *,
    node_id: str,
    config: Mapping[str, Any],
    store: SourceCacheStore,
    profile: ExecutionProfile,
    execution_context: ExecutionContext,
    base_dir: str | Path | None,
    spawn: Any,
    identity: Any,
    signature: str | None,
    build_class: str,
    warning_code: str | None,
    deadline: float | None,
) -> InputPreparationRecord:
    started_at = time.monotonic()
    # A caller's own deadline (a job's) bounds the build as well: preparing an
    # input never outlasts the run it prepares for.
    deadline = _build_deadline() if deadline is None else min(_build_deadline(), deadline)
    digest = identity.digest
    while True:
        status = store.status(identity, source_signature=signature)
        if status.state == "corrupt":
            raise SourceCacheCorruptError(
                "source-cache generation is corrupt; clear and rebuild this Data Input snapshot"
            )
        generation = status.generation
        if signature == "missing":
            # An absent local source cannot refresh anything. A published
            # generation stays authoritative; without one there is nothing to
            # run from, and this is refused before any slot or worker is taken.
            if status.state == "ready" and generation is not None:
                logger.warning(
                    "input_snapshot_source_unavailable",
                    node_id=node_id,
                    identity_digest=digest,
                    generation_id=generation.generation_id,
                )
                return _reused_record(
                    node_id=node_id,
                    digest=digest,
                    build_class=build_class,
                    generation=generation,
                    elapsed_seconds=time.monotonic() - started_at,
                    warning_code="source_unavailable",
                )
            raise _failure(
                node_id=node_id,
                digest=digest,
                build_class=build_class,
                reason_code="build_failed",
                message=(
                    "This Data Input's source is unavailable and no published snapshot exists."
                ),
            )
        if (
            status.state == "ready"
            and status.freshness in ("fresh", "unknown")
            and generation is not None
        ):
            return _reused_record(
                node_id=node_id,
                digest=digest,
                build_class=build_class,
                generation=generation,
                elapsed_seconds=time.monotonic() - started_at,
                warning_code=warning_code,
            )
        refresh = status.state == "ready"
        waiting = _acquire_single_flight(digest)
        if waiting is None:
            break
        # Another execution in this process is building the same identity;
        # wait for it and re-read status rather than building again. The wait
        # is polled so this execution's own cancellation and time budget still
        # apply while another execution owns the build.
        _wait_for_single_flight(
            waiting.event,
            execution_context=execution_context,
            node_id=node_id,
            digest=digest,
            build_class=build_class,
            deadline=deadline,
        )
        owner_error = waiting.error
        if isinstance(owner_error, InputPreparationError):
            # The owner's refusal is this execution's refusal too: rebuilding
            # would repeat exactly the failure that has just been recorded.
            raise _failure(
                node_id=node_id,
                digest=digest,
                build_class=build_class,
                reason_code=owner_error.reason_code,
                message=(
                    f"Another execution's snapshot build of this Data Input failed: {owner_error}"
                ),
            ) from owner_error

    build_error: BaseException | None = None
    try:
        return _run_build(
            node_id=node_id,
            config=config,
            store=store,
            profile=profile,
            execution_context=execution_context,
            base_dir=base_dir,
            spawn=spawn,
            identity=identity,
            signature=signature,
            build_class=build_class,
            warning_code=warning_code,
            refresh=refresh,
            started_at=started_at,
            deadline=deadline,
            current_generation=generation,
        )
    except BaseException as exc:
        build_error = exc
        raise
    finally:
        _release_single_flight(digest, build_error)


def _failure(
    *,
    node_id: str,
    digest: str,
    build_class: str,
    reason_code: str,
    message: str,
    kind: InputKind = "data_input",
) -> InputPreparationError:
    remediation = (
        _API_INPUT_REMEDIATION_BY_REASON.get(reason_code, _API_INPUT_REMEDIATION)
        if kind == "api_input"
        else _REMEDIATION_BY_REASON.get(reason_code, _REMEDIATION)
    )
    return InputPreparationError(
        message,
        node_id=node_id,
        identity_digest=digest,
        build_class=build_class,
        reason_code=reason_code,
        remediation=remediation,
    )


def _wait_for_single_flight(
    event: threading.Event,
    *,
    execution_context: ExecutionContext,
    node_id: str,
    digest: str,
    build_class: str,
    deadline: float,
    kind: InputKind = "data_input",
) -> None:
    """Wait for another execution's build, staying cancellable and bounded."""
    noun = "API Input's tables" if kind == "api_input" else "Data Input's snapshot"
    while not event.wait(timeout=0.1):
        if time.monotonic() > deadline:
            raise _failure(
                node_id=node_id,
                digest=digest,
                build_class=build_class,
                reason_code="timed_out",
                message=(
                    f"Waiting for another execution's build of this {noun} "
                    "exceeded its time budget."
                ),
                kind=kind,
            )
        try:
            execution_context.checkpoint(label="input_snapshot_preparation_wait")
        except ExecutionCancelledError as exc:
            raise _failure(
                node_id=node_id,
                digest=digest,
                build_class=build_class,
                reason_code="cancelled",
                message=f"Preparing this {noun} was cancelled.",
                kind=kind,
            ) from exc


def _classify_local_failure(
    exc: BaseException,
    *,
    deadline: float,
    cancelled: bool,
) -> str:
    if isinstance(exc, ExecutionCancelledError):
        return "cancelled"
    if isinstance(exc, MemoryError):
        return "memory_limited"
    if isinstance(exc, SourceCacheBuildError):
        if time.monotonic() > deadline:
            return "timed_out"
        if cancelled:
            return "cancelled"
    return "build_failed"


def _classify_worker_failure(exc: BaseException) -> str:
    # A worker exception arrives as ``IsolatedWorkerRemoteError`` carrying the
    # child's exception type name, so classify on that name before falling back
    # to the generic memory heuristic.
    remote_type = exc.remote_type if isinstance(exc, IsolatedWorkerRemoteError) else None
    if remote_type in ("NativeMemoryLimitUnsupportedError", "NativeMemoryLimitCleanupError"):
        return "cap_unavailable"
    if isolated_worker_failure_is_memory(exc):
        return "memory_limited"
    reason = getattr(exc, "terminal_reason", None)
    if reason in ("timed_out", "cancelled"):
        return str(reason)
    return "build_failed"


def _run_build(
    *,
    node_id: str,
    config: Mapping[str, Any],
    store: SourceCacheStore,
    profile: ExecutionProfile,
    execution_context: ExecutionContext,
    base_dir: str | Path | None,
    spawn: Any,
    identity: Any,
    signature: str | None,
    build_class: str,
    warning_code: str | None,
    refresh: bool,
    started_at: float,
    deadline: float,
    current_generation: SourceCacheGeneration | None,
) -> InputPreparationRecord:
    from haute._input_providers import build_input_snapshot

    digest = identity.digest
    action: PreparationAction = "refreshed" if refresh else "built"
    in_process = current_native_memory_backend() is not None
    execution: PreparationExecution = "in_process" if in_process else "worker"
    budget = None if in_process else isolated_execution_budget(execution_context)
    memory_limit_bytes = (
        execution_context.memory_limit_bytes if budget is None else budget.memory_limit_bytes
    )

    # Spawned build: the cap is mandatory whatever the process-memory
    # enforcement policy says. A host that cannot install one still has a
    # ready-but-stale generation to fall back to; only a missing generation is
    # refused typed, before any provider access.
    if not in_process and not process_memory_caps_supported():
        if refresh and current_generation is not None:
            logger.warning(
                "input_snapshot_cap_unavailable_stale_reused",
                node_id=node_id,
                identity_digest=digest,
                generation_id=current_generation.generation_id,
            )
            return _reused_record(
                node_id=node_id,
                digest=digest,
                build_class=build_class,
                generation=current_generation,
                elapsed_seconds=time.monotonic() - started_at,
                warning_code="cap_unavailable_stale_reused",
            )
        raise _failure(
            node_id=node_id,
            digest=digest,
            build_class=build_class,
            reason_code="cap_unavailable",
            message=(
                "This host cannot install the native memory cap an automatic "
                "snapshot build requires."
            ),
        )

    # Announced only once a build actually starts: a refusal or a reuse above
    # never reports an automatic build that did not happen.
    logger.warning(
        "input_snapshot_auto_build",
        node_id=node_id,
        identity_digest=digest,
        build_class=build_class,
        action=action,
        execution=execution,
        memory_limit_bytes=memory_limit_bytes,
    )

    if in_process:
        token = execution_context.cancellation_token
        try:
            generation = build_input_snapshot(
                config,
                store=store,
                base_dir=base_dir,
                profile=profile,
                refresh=refresh,
                cancellation=lambda: token.cancelled,
                deadline=deadline,
                execution_context=execution_context,
                allow_admitted_eager=True,
            )
        except (SourceCacheCorruptError, PolarsIoConfigError):
            raise
        except Exception as exc:
            raise _failure(
                node_id=node_id,
                digest=digest,
                build_class=build_class,
                reason_code=_classify_local_failure(
                    exc, deadline=deadline, cancelled=token.cancelled
                ),
                message="Preparing this Data Input's snapshot failed.",
            ) from exc
        return InputPreparationRecord(
            node_id=node_id,
            identity_digest=digest,
            action=action,
            build_class=build_class,
            execution=execution,
            memory_limit_bytes=memory_limit_bytes,
            elapsed_seconds=time.monotonic() - started_at,
            row_count=generation.metadata.row_count,
            size_bytes=generation.metadata.size_bytes,
            generation_id=generation.generation_id,
            warning_code=warning_code,
        )

    if budget is None:
        raise RuntimeError("a spawned snapshot build requires an admitted budget")
    generation_id = str(uuid.uuid4())
    staging_token = new_staging_token()
    cancellation_token = execution_context.cancellation_token

    def stop_reason() -> WorkerTerminalReason | None:
        return "cancelled" if cancellation_token.cancelled else None

    worker_config = dataclasses.replace(
        worker_config_for_memory_policy(
            memory_limit_bytes=budget.memory_limit_bytes,
            # One deadline covers the whole preparation, including any time
            # already spent waiting on another execution's build.
            timeout_seconds=max(1.0, deadline - time.monotonic()),
            stop_reason=stop_reason,
            process_name="haute-input-prep",
        ),
        require_memory_limit=True,
    )
    from haute._sandbox import _get_project_root

    request = InputPreparationRequest(
        config=dict(config),
        base_dir=str(Path(base_dir).resolve() if base_dir is not None else Path.cwd().resolve()),
        cache_root=str(store.root),
        project_root=str(_get_project_root()),
        profile=profile,
        refresh=refresh,
        generation_id=generation_id,
        staging_token=staging_token,
    )
    try:
        outcome = spawn(
            build_input_snapshot_worker,
            request,
            budget,
            config=worker_config,
        )
    except BaseException as exc:
        reason_code = _classify_worker_failure(exc)
        settled = store.reconcile_unpublished(identity, generation_id, staging_token)
        # Reconcile first so nothing is left behind, but never convert a base
        # exception (an interrupt, a system exit) into this build's success.
        if not isinstance(exc, Exception):
            raise
        if settled == "published":
            generation = store.open_generation(identity)
            # The child deferred retirement; retire here, where this process's
            # own lease counts are visible.
            store.retire_unleased(identity)
            return InputPreparationRecord(
                node_id=node_id,
                identity_digest=digest,
                action=action,
                build_class=build_class,
                execution=execution,
                memory_limit_bytes=memory_limit_bytes,
                elapsed_seconds=time.monotonic() - started_at,
                row_count=generation.metadata.row_count,
                size_bytes=generation.metadata.size_bytes,
                generation_id=generation.generation_id,
                warning_code=warning_code,
            )
        successor = store.status(identity, source_signature=signature)
        successor_generation = successor.generation
        if (
            successor.state == "ready"
            and successor.freshness in ("fresh", "unknown")
            and successor_generation is not None
        ):
            return _reused_record(
                node_id=node_id,
                digest=digest,
                build_class=build_class,
                generation=successor_generation,
                elapsed_seconds=time.monotonic() - started_at,
                warning_code=warning_code,
            )
        raise _failure(
            node_id=node_id,
            digest=digest,
            build_class=build_class,
            reason_code=reason_code,
            message="Preparing this Data Input's snapshot failed.",
        ) from exc
    # The child deferred retirement; retire here, where this process's own lease
    # counts are visible.
    store.retire_unleased(identity)
    return InputPreparationRecord(
        node_id=node_id,
        identity_digest=digest,
        action=action,
        build_class=build_class,
        execution=execution,
        memory_limit_bytes=memory_limit_bytes,
        elapsed_seconds=time.monotonic() - started_at,
        row_count=outcome.row_count,
        size_bytes=outcome.size_bytes,
        generation_id=outcome.generation_id,
        warning_code=warning_code,
    )


def _api_input_reused_records(
    node_id: str,
    statuses: Iterable[tuple[Any, SourceCacheStatus]],
    *,
    started_at: float,
    warning_code: str | None,
) -> list[InputPreparationRecord]:
    records: list[InputPreparationRecord] = []
    for table, status in statuses:
        assert status.generation is not None
        records.append(
            _reused_record(
                node_id=node_id,
                digest=table.identity.digest,
                build_class="bounded",
                generation=status.generation,
                elapsed_seconds=time.monotonic() - started_at,
                warning_code=warning_code,
            )
        )
    return records


def _prepare_api_input(
    *,
    node_id: str,
    config: Mapping[str, Any],
    store: SourceCacheStore,
    profile: ExecutionProfile,
    execution_context: ExecutionContext,
    spawn: Any,
    deadline: float | None,
) -> list[InputPreparationRecord]:
    """Reuse, build, or refresh every emitting table of one structured API Input.

    Every table the node emits is prepared. When any is missing or stale the
    source is shredded once and each missing or stale table is written in
    that pass; fresh tables are left as they are. One record per table.
    """
    from haute._json_shred._snapshots import (
        api_input_snapshot_source,
        api_input_source_signature,
        api_input_table_statuses,
    )

    started_at = time.monotonic()
    deadline = _build_deadline() if deadline is None else min(_build_deadline(), deadline)
    source = api_input_snapshot_source(config, str(config["path"]))
    if not source.tables:
        # The node builder owns the rejection of an API Input that emits nothing.
        return []
    group = source.group_digest
    while True:
        signature = api_input_source_signature(source.data_path)
        statuses = api_input_table_statuses(source, store, source_signature=signature)
        if any(status.state == "corrupt" for _table, status in statuses):
            raise SourceCacheCorruptError(
                "source-cache generation is corrupt; clear and rebuild this API Input's tables"
            )
        if signature == "missing":
            # An absent source refreshes nothing. Published tables stay
            # authoritative; a table without one has nothing to run from.
            if all(status.state == "ready" for _table, status in statuses):
                logger.warning(
                    "input_snapshot_source_unavailable",
                    node_id=node_id,
                    identity_digest=group,
                )
                return _api_input_reused_records(
                    node_id, statuses, started_at=started_at, warning_code="source_unavailable"
                )
            raise _failure(
                node_id=node_id,
                digest=group,
                build_class="bounded",
                reason_code="build_failed",
                message=(
                    "This API Input's source is unavailable and not every table has a "
                    "published snapshot."
                ),
                kind="api_input",
            )
        to_build = [
            table.label
            for table, status in statuses
            if not (
                status.state == "ready"
                and status.freshness in ("fresh", "unknown")
                and status.generation is not None
            )
        ]
        if not to_build:
            return _api_input_reused_records(
                node_id, statuses, started_at=started_at, warning_code=None
            )
        waiting = _acquire_single_flight(group)
        if waiting is None:
            break
        # Another execution in this process is building this node's tables;
        # wait, then read every status again rather than building twice.
        _wait_for_single_flight(
            waiting.event,
            execution_context=execution_context,
            node_id=node_id,
            digest=group,
            build_class="bounded",
            deadline=deadline,
            kind="api_input",
        )
        owner_error = waiting.error
        if isinstance(owner_error, InputPreparationError):
            raise _failure(
                node_id=node_id,
                digest=group,
                build_class="bounded",
                reason_code=owner_error.reason_code,
                message=(
                    f"Another execution's build of this API Input's tables failed: {owner_error}"
                ),
                kind="api_input",
            ) from owner_error

    build_error: BaseException | None = None
    try:
        return _run_api_input_build(
            node_id=node_id,
            source=source,
            statuses=statuses,
            to_build=to_build,
            signature=signature,
            store=store,
            profile=profile,
            execution_context=execution_context,
            spawn=spawn,
            started_at=started_at,
            deadline=deadline,
        )
    except BaseException as exc:
        build_error = exc
        raise
    finally:
        _release_single_flight(group, build_error)


def _run_api_input_build(
    *,
    node_id: str,
    source: Any,
    statuses: tuple[tuple[Any, SourceCacheStatus], ...],
    to_build: list[str],
    signature: str,
    store: SourceCacheStore,
    profile: ExecutionProfile,
    execution_context: ExecutionContext,
    spawn: Any,
    started_at: float,
    deadline: float,
) -> list[InputPreparationRecord]:
    from haute._api_input_schema import ApiInputSchemaError
    from haute._json_shred._snapshots import (
        api_input_table_statuses,
        build_api_input_tables,
        run_supervised_api_input_build,
    )

    group = source.group_digest
    building = set(to_build)
    refreshed = {
        table.identity.digest
        for table, status in statuses
        if table.label in building and status.state == "ready"
    }
    in_process = current_native_memory_backend() is not None
    execution: PreparationExecution = "in_process" if in_process else "worker"
    budget = None if in_process else isolated_execution_budget(execution_context)
    memory_limit_bytes = (
        execution_context.memory_limit_bytes if budget is None else budget.memory_limit_bytes
    )

    if not in_process and not process_memory_caps_supported():
        # Without a cap only already-published tables can serve: stale ones
        # are reused, and a missing one is refused before the source is read.
        if all(
            status.generation is not None for table, status in statuses if table.label in building
        ):
            logger.warning(
                "input_snapshot_cap_unavailable_stale_reused",
                node_id=node_id,
                identity_digest=group,
            )
            return _api_input_reused_records(
                node_id,
                statuses,
                started_at=started_at,
                warning_code="cap_unavailable_stale_reused",
            )
        raise _failure(
            node_id=node_id,
            digest=group,
            build_class="bounded",
            reason_code="cap_unavailable",
            message=(
                "This host cannot install the native memory cap an automatic "
                "snapshot build requires."
            ),
            kind="api_input",
        )

    logger.warning(
        "input_snapshot_auto_build",
        node_id=node_id,
        identity_digest=group,
        build_class="bounded",
        action="refreshed" if refreshed else "built",
        execution=execution,
        memory_limit_bytes=memory_limit_bytes,
        tables=to_build,
    )

    if in_process:
        token = execution_context.cancellation_token
        try:
            build_api_input_tables(
                source,
                to_build,
                store=store,
                profile=profile,
                cancellation=lambda: token.cancelled,
                deadline=deadline,
                execution_context=execution_context,
            )
        except (SourceCacheCorruptError, PolarsIoConfigError, ApiInputSchemaError):
            raise
        except Exception as exc:
            raise _failure(
                node_id=node_id,
                digest=group,
                build_class="bounded",
                reason_code=_classify_local_failure(
                    exc, deadline=deadline, cancelled=token.cancelled
                ),
                message="Preparing this API Input's tables failed.",
                kind="api_input",
            ) from exc
    else:
        if budget is None:
            raise RuntimeError("a spawned snapshot build requires an admitted budget")
        cancellation_token = execution_context.cancellation_token

        def stop_reason() -> WorkerTerminalReason | None:
            return "cancelled" if cancellation_token.cancelled else None

        worker_config = dataclasses.replace(
            worker_config_for_memory_policy(
                memory_limit_bytes=budget.memory_limit_bytes,
                timeout_seconds=max(1.0, deadline - time.monotonic()),
                stop_reason=stop_reason,
                process_name="haute-input-prep",
            ),
            require_memory_limit=True,
        )
        try:
            run_supervised_api_input_build(
                source,
                to_build,
                store=store,
                profile=profile,
                budget=budget,
                worker_config=worker_config,
                spawn=spawn,
            )
        except Exception as exc:
            reason_code = _classify_worker_failure(exc)
            successors = api_input_table_statuses(source, store, source_signature=signature)
            if all(
                status.state == "ready"
                and status.freshness in ("fresh", "unknown")
                and status.generation is not None
                for _table, status in successors
            ):
                return _api_input_reused_records(
                    node_id, successors, started_at=started_at, warning_code=None
                )
            raise _failure(
                node_id=node_id,
                digest=group,
                build_class="bounded",
                reason_code=reason_code,
                message="Preparing this API Input's tables failed.",
                kind="api_input",
            ) from exc

    records: list[InputPreparationRecord] = []
    for table, status in api_input_table_statuses(source, store, source_signature=signature):
        generation = status.generation
        if generation is None:
            raise RuntimeError(f"API Input table {table.label!r} has no generation after its build")
        action: PreparationAction = (
            "reused"
            if table.label not in building
            else "refreshed"
            if table.identity.digest in refreshed
            else "built"
        )
        records.append(
            InputPreparationRecord(
                node_id=node_id,
                identity_digest=table.identity.digest,
                action=action,
                build_class="bounded",
                execution="in_process" if action == "reused" else execution,
                memory_limit_bytes=None if action == "reused" else memory_limit_bytes,
                elapsed_seconds=time.monotonic() - started_at,
                row_count=generation.metadata.row_count,
                size_bytes=generation.metadata.size_bytes,
                generation_id=generation.generation_id,
            )
        )
    return records
