"""NodeDataService: point status and explicit snapshot builds for any consumer node.

A consumer node names the data it reads; the data-point resolver turns that
into a point and a column demand. Node-output points are built here as pinned,
full-width snapshots in an isolated worker. Snapshot-backed Data Inputs and
API-input tables keep their existing build routes, which ``run`` delegates to.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import HTTPException

from haute._data_points import (
    ConsumerPoint,
    DataPointResolver,
    NodeDataPointInvalidError,
    PointKind,
    PointResolution,
    consumer_point,
)
from haute._env import float_env
from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_admitted_execution_context,
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
from haute._node_snapshots import (
    NodeSnapshotColumns,
    NodeSnapshotMultiFrameUnsupportedError,
    NodeSnapshotQuotaRejectedError,
    NodeSnapshotSlot,
    NodeSnapshotStore,
)
from haute._source_cache import SourceCacheIdentity, new_staging_token
from haute._worker_isolation import (
    IsolatedWorkerError,
    IsolatedWorkerRemoteError,
    run_isolated_worker,
    worker_config_for_memory_policy,
)
from haute.errors import BoundedMemoryUnsupportedError, ContractMismatchError, SchemaMismatchError
from haute.routes._background_jobs import CancellableJobRegistry, JobCancellation
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_job_fields,
    contract_error_terminal_reason,
)
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.routes._job_lifecycle import (
    JobLifecycle,
    TerminalReason,
    bind_running_execution_metrics_publisher,
)
from haute.routes._job_store import JobStore, RunningJobFields
from haute.schemas import (
    ExecutionMetricsPayload,
    NodeDataClearResponse,
    NodeDataGeneration,
    NodeDataJob,
    NodeDataPointRef,
    NodeDataPointResponse,
    NodeDataRequest,
    NodeDataRunRequest,
    NodeDataRunResponse,
    NodeDataStatusResponse,
)

logger = get_logger(component="server.node_data")

_INPUT_CACHE_BUILD_ENDPOINT = "/api/input-cache/build"
_INPUT_CACHE_CLEAR_ENDPOINT = "/api/input-cache/clear"
_JSON_CACHE_BUILD_ENDPOINT = "/api/json-cache/build"
_JSON_CACHE_CLEAR_ENDPOINT = "/api/json-cache"


class _NodeDataRunningJob(RunningJobFields):
    kind: Literal["node_data"]
    progress: float
    node_id: str
    producer_node_id: str
    source: str
    identity_digest: str


@dataclass(frozen=True, slots=True)
class _NodeSnapshotWorkerRequest:
    """Closed, pickle-safe description of one explicit node-output build."""

    graph: Any
    node_id: str
    source: str
    identity_digest: str
    slot_digest: str
    refresh: bool
    project_root: str
    streaming_chunk_size: int | None
    # Parent-chosen, so the parent can discard the staging directory of a
    # worker killed by cancellation, timeout, or the memory cap.
    staging_token: str


@dataclass(frozen=True, slots=True)
class _NodeSnapshotWorkerOutcome:
    generation_id: str | None = None
    outcome: Literal["published", "superseded"] | None = None
    failure_kind: Literal["public_contract", "contract", "memory", "quota"] | None = None
    detail: str | None = None
    payload: dict[str, Any] | None = None
    terminal_reason: str | None = None


class _NodeSnapshotWorkerReportedError(RuntimeError):
    def __init__(
        self,
        kind: Literal["public_contract", "contract", "memory", "quota"],
        detail: str,
        payload: dict[str, Any] | None,
        terminal_reason: str | None,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.payload = payload
        self.terminal_reason = terminal_reason


class NodeSnapshotInputsChangedError(RuntimeError):
    """A node's inputs changed between its build's identity binding and publication."""


def _node_identity(
    graph: Any, node_id: str, source: str, store: NodeSnapshotStore
) -> SourceCacheIdentity:
    resolver = DataPointResolver(graph, source=source, store=store)
    return resolver.node_output_slot(node_id).identity(resolver.node_output_signature(node_id))


def node_data_project_root() -> Path:
    """Return the project root whose snapshot store holds node data."""
    from haute._sandbox import _get_project_root

    return _get_project_root()


def _slot_key(resolution: PointResolution, source: str) -> str:
    point = resolution.point
    return f"{point.producer_node_id}|{point.port_label or ''}|{source}"


def _columns_payload(columns: NodeSnapshotColumns) -> list[str] | Literal["all"]:
    return "all" if columns.names is None else sorted(columns.names)


def _run_node_snapshot_worker(
    request: _NodeSnapshotWorkerRequest,
    budget: IsolatedExecutionBudget,
) -> _NodeSnapshotWorkerOutcome:
    """One-shot child entrypoint: build and publish one explicit node-output snapshot."""
    from haute._sandbox import set_project_root

    set_project_root(Path(request.project_root))
    try:
        execution_context = create_isolated_execution_context(budget)
        try:
            return _build_node_snapshot(request, execution_context)
        finally:
            execution_context.release_admission(preserve_primary_error=True)
    except PUBLIC_CONTRACT_ERROR_TYPES as exc:
        return _NodeSnapshotWorkerOutcome(
            failure_kind="public_contract",
            detail=str(exc),
            payload=contract_error_job_fields(exc),
            terminal_reason=contract_error_terminal_reason(exc),
        )
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        return _NodeSnapshotWorkerOutcome(
            failure_kind="memory", detail=str(exc), payload=exc.to_payload()
        )
    except NodeSnapshotQuotaRejectedError as exc:
        return _NodeSnapshotWorkerOutcome(failure_kind="quota", detail=str(exc))
    except (
        ContractMismatchError,
        SchemaMismatchError,
        BoundedMemoryUnsupportedError,
        NodeSnapshotMultiFrameUnsupportedError,
        NodeSnapshotInputsChangedError,
        NodeDataPointInvalidError,
    ) as exc:
        return _NodeSnapshotWorkerOutcome(failure_kind="contract", detail=str(exc))


def _build_node_snapshot(
    request: _NodeSnapshotWorkerRequest,
    execution_context: ExecutionContext,
) -> _NodeSnapshotWorkerOutcome:
    """Build under the identity the parent bound after preparing inputs.

    Input preparation already ran in the supervising parent, so execution reads
    exactly the prepared inputs. The identity is checked before execution and
    again after the sink: any source or snapshot change inside that window would
    otherwise publish rows under a signature they were not computed from.
    """
    import polars as pl

    from haute._polars_utils import bounded_sink
    from haute.execution import execute_lazy_graph
    from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir

    store = NodeSnapshotStore(request.project_root)
    changed = NodeSnapshotInputsChangedError(
        "This node's inputs changed while its data was being cached; cache it again."
    )
    identity = _node_identity(request.graph, request.node_id, request.source, store)
    if identity.digest != request.identity_digest:
        raise changed
    graph = DataPointResolver(request.graph, source=request.source, store=store).graph
    preamble_ns = _compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph))
    outputs, *_ = execute_lazy_graph(
        graph,
        _build_node_fn,
        target_node_id=request.node_id,
        preamble_ns=preamble_ns or None,
        source=request.source,
        enforce_contracts=True,
        execution_context=execution_context,
        prepare_inputs=False,
    )
    output = outputs[request.node_id]
    if isinstance(output, dict):
        raise NodeSnapshotMultiFrameUnsupportedError(
            "A node that emits several frames cannot be cached as one node-output snapshot."
        )
    artifact = store.stage_node_output(identity, staging_token=request.staging_token)
    try:
        with execution_context.stage("node_snapshot_write"):
            bounded_sink(
                output.lazy() if isinstance(output, pl.DataFrame) else output,
                artifact.data_path,
                fast_checkpoint=True,
                streaming_chunk_size=request.streaming_chunk_size,
            )
        if _node_identity(request.graph, request.node_id, request.source, store).digest != (
            identity.digest
        ):
            raise changed
        publication = store.publish_node_output(
            identity,
            artifact,
            columns=NodeSnapshotColumns.all(),
            dependencies={},
            explicit=True,
            profile=ExecutionProfile.NODE_SNAPSHOT,
            refresh=request.refresh,
        )
    except NodeSnapshotQuotaRejectedError as exc:
        exc.artifact.close()
        raise
    except BaseException:
        artifact.close()
        raise
    with publication:
        generation_id = (
            publication.generation.generation_id if publication.generation is not None else None
        )
        return _NodeSnapshotWorkerOutcome(
            generation_id=generation_id,
            outcome=publication.outcome,
        )


def _validated_worker_success(outcome: object) -> _NodeSnapshotWorkerOutcome:
    """Validate the closed child envelope before trusting any field."""
    if not isinstance(outcome, _NodeSnapshotWorkerOutcome):
        raise RuntimeError("node-snapshot worker returned an invalid result envelope")
    if outcome.failure_kind is not None:
        if outcome.outcome is not None or outcome.generation_id is not None:
            raise RuntimeError("node-snapshot worker mixed success and failure fields")
        if not isinstance(outcome.detail, str) or not outcome.detail:
            raise RuntimeError("node-snapshot worker failure omitted its detail")
        if outcome.failure_kind == "public_contract":
            payload = outcome.payload
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("error_code"), str)
                or not isinstance(payload.get("error_detail"), dict)
                or outcome.terminal_reason not in ("contract_error", "memory_limited")
            ):
                raise RuntimeError("node-snapshot worker returned an invalid contract payload")
        elif outcome.failure_kind == "memory":
            if (
                not isinstance(outcome.payload, dict)
                or outcome.payload.get("error_code") != "memory_limit"
            ):
                raise RuntimeError("node-snapshot worker returned an invalid memory payload")
        elif outcome.payload is not None:
            raise RuntimeError("node-snapshot worker failure carried an unexpected payload")
        raise _NodeSnapshotWorkerReportedError(
            outcome.failure_kind, outcome.detail, outcome.payload, outcome.terminal_reason
        )
    if outcome.outcome not in ("published", "superseded"):
        raise RuntimeError("node-snapshot worker omitted its publication outcome")
    if (outcome.outcome == "published") != isinstance(outcome.generation_id, str):
        raise RuntimeError("node-snapshot worker outcome and generation disagree")
    return outcome


class NodeDataService:
    """Report and build the data points consumer nodes read."""

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._lifecycle = JobLifecycle(store)
        self._jobs = CancellableJobRegistry()
        self._lock = threading.Lock()
        self._slot_lock = threading.Lock()
        self._job_by_identity: dict[str, str] = {}
        self._job_by_slot: dict[str, str] = {}
        self._threads: dict[str, threading.Thread] = {}

    # ------------------------------------------------------------ probes

    def _running_job_id(self, identity_digest: str) -> str | None:
        with self._lock:
            job_id = self._job_by_identity.get(identity_digest)
        if job_id is None:
            return None
        job = self._store.get_job(job_id)
        return job_id if job is not None and job.get("status") == "running" else None

    def _building(self, kind: PointKind, key: str) -> bool:
        if kind == "node_output":
            return self._running_job_id(key) is not None
        if kind == "data_input":
            from haute.routes.input_cache import input_snapshot_build_running

            return input_snapshot_build_running(key)
        from haute.routes.json_cache import json_cache_build_running

        return json_cache_build_running(key)

    def _resolver(self, body: NodeDataRequest) -> tuple[ConsumerPoint, DataPointResolver]:
        try:
            consumer = consumer_point(body.graph, body.node_id)
        except NodeDataPointInvalidError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error_code": exc.error_code, "message": str(exc)},
            ) from exc
        resolver = DataPointResolver(
            body.graph,
            source=body.source,
            store=NodeSnapshotStore(node_data_project_root()),
            building=self._building,
        )
        return consumer, resolver

    # ------------------------------------------------------------ surface

    def point(self, body: NodeDataRequest) -> NodeDataPointResponse:
        consumer, resolver = self._resolver(body)
        return self._point_response(consumer, resolver)

    def _point_response(
        self, consumer: ConsumerPoint, resolver: DataPointResolver
    ) -> NodeDataPointResponse:
        resolution = self._resolve(consumer, resolver)
        response = NodeDataPointResponse(
            consumer_node_id=consumer.consumer_node_id,
            point=NodeDataPointRef(
                producer_node_id=consumer.point.producer_node_id,
                port_label=consumer.point.port_label,
            ),
            slot_key=_slot_key(resolution, resolver.source),
            kind=resolution.kind,
            state=resolution.state,
            demand=_columns_payload(consumer.demand),
            data_version=resolution.data_version,
        )
        if resolution.kind == "node_output":
            return self._node_output_details(response, resolution)
        if resolution.kind == "api_input_table":
            return response.model_copy(
                update={
                    "build_endpoint": _JSON_CACHE_BUILD_ENDPOINT,
                    "clear_endpoint": _JSON_CACHE_CLEAR_ENDPOINT,
                }
            )
        if resolution.input_identity is None:
            return response.model_copy(update={"reads_directly": True})
        details: dict[str, Any] = {
            "build_endpoint": _INPUT_CACHE_BUILD_ENDPOINT,
            "clear_endpoint": _INPUT_CACHE_CLEAR_ENDPOINT,
        }
        if resolution.input_generation_id is not None:
            status = resolver.store.status(resolution.input_identity)
            if status.generation is not None:
                details["row_count"] = status.generation.metadata.row_count
                details["size_bytes"] = status.generation.metadata.size_bytes
        return response.model_copy(update=details)

    def _node_output_details(
        self, response: NodeDataPointResponse, resolution: PointResolution
    ) -> NodeDataPointResponse:
        generation = resolution.node_output_generation
        identity = resolution.node_output_identity
        assert identity is not None
        details: dict[str, Any] = {}
        job_id = self._running_job_id(identity.digest)
        if job_id is not None:
            job = self._store.require_job(job_id)
            details["job"] = NodeDataJob(
                job_id=job_id,
                progress=float(job.get("progress", 0.0)),
                message=str(job.get("message", "")),
            )
        if generation is not None:
            metadata = generation.generation.metadata
            details.update(
                {
                    "row_count": metadata.row_count,
                    "size_bytes": metadata.size_bytes,
                    "retention": generation.retention,
                    "generation": NodeDataGeneration(
                        generation_id=generation.generation_id,
                        columns=_columns_payload(generation.columns),
                        row_count=metadata.row_count,
                        column_count=metadata.column_count,
                        size_bytes=metadata.size_bytes,
                        retention=generation.retention,
                        fresh=generation.fresh,
                        created_at=metadata.created_at,
                    ),
                }
            )
        return response.model_copy(update=details)

    def _resolve(self, consumer: ConsumerPoint, resolver: DataPointResolver) -> PointResolution:
        try:
            return resolver.resolve(consumer.point, consumer.demand)
        except NodeDataPointInvalidError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error_code": exc.error_code, "message": str(exc)},
            ) from exc

    def run(self, body: NodeDataRunRequest) -> NodeDataRunResponse:
        consumer, resolver = self._resolver(body)
        # One slot decision at a time: a join check and a job registration, or a
        # clear and its wait for the running build, are never interleaved.
        with self._slot_lock:
            return self._run_locked(body, consumer, resolver)

    def _run_locked(
        self, body: NodeDataRunRequest, consumer: ConsumerPoint, resolver: DataPointResolver
    ) -> NodeDataRunResponse:
        resolution = self._resolve(consumer, resolver)
        point = self._point_response(consumer, resolver)
        if resolution.kind != "node_output":
            if point.reads_directly:
                return NodeDataRunResponse(
                    status="completed",
                    cached=True,
                    message="This Data Input reads Parquet directly; there is nothing to cache.",
                    point=point,
                )
            return NodeDataRunResponse(
                status="delegated",
                message=f"Build this data through {point.build_endpoint}.",
                point=point,
            )
        identity = resolution.node_output_identity
        assert identity is not None
        slot = resolver.node_output_slot(consumer.point.producer_node_id)
        generation = resolution.node_output_generation
        if (
            not body.refresh
            and resolution.state == "current"
            and generation is not None
            and generation.columns.is_all
        ):
            resolver.store.pin(identity)
            return NodeDataRunResponse(
                status="completed",
                cached=True,
                message="Data is cached",
                point=self._point_response(consumer, resolver),
            )
        with self._lock:
            running = self._job_by_identity.get(identity.digest)
        if running is not None and self._running_job_id(identity.digest) == running:
            return NodeDataRunResponse(
                status="joined",
                job_id=running,
                message="Joined the running cache build",
                point=point,
            )
        job_id = self._start_job(body, consumer, identity, slot)
        return NodeDataRunResponse(
            status="started",
            job_id=job_id,
            message="Caching started",
            point=self._point_response(consumer, resolver),
        )

    def _start_job(
        self,
        body: NodeDataRunRequest,
        consumer: ConsumerPoint,
        identity: SourceCacheIdentity,
        slot: NodeSnapshotSlot,
    ) -> str:
        initial_job: _NodeDataRunningJob = {
            "kind": "node_data",
            "status": "running",
            "progress": 0.0,
            "message": "Caching data",
            "node_id": consumer.consumer_node_id,
            "producer_node_id": consumer.point.producer_node_id,
            "source": body.source,
            "identity_digest": identity.digest,
        }
        job_id = self._store.create_job(initial_job)
        with self._lock:
            token, previous_job_id = self._jobs.register_latest(slot.digest, job_id)
            self._job_by_identity[identity.digest] = job_id
            self._job_by_slot[slot.digest] = job_id
        if previous_job_id is not None:
            self._lifecycle.transition(
                previous_job_id,
                to="superseded",
                message="Superseded by a newer cache build for this data.",
                expected_status="running",
            )
        request = _NodeSnapshotWorkerRequest(
            graph=body.graph,
            node_id=consumer.point.producer_node_id,
            source=body.source,
            identity_digest=identity.digest,
            slot_digest=slot.digest,
            refresh=body.refresh,
            project_root=str(node_data_project_root()),
            streaming_chunk_size=body.streaming_chunk_size,
            staging_token=new_staging_token(),
        )
        with self._lock:
            previous_thread = (
                self._threads.get(previous_job_id) if previous_job_id is not None else None
            )
            thread = threading.Thread(
                target=self._run_job,
                args=(job_id, request, token, previous_thread),
                name=f"haute-node-data-{job_id}",
                daemon=True,
            )
            self._threads[job_id] = thread
        thread.start()
        return job_id

    def status(self, job_id: str) -> NodeDataStatusResponse:
        job = self._store.require_job(job_id)
        if job.get("kind") != "node_data":
            raise HTTPException(status_code=404, detail="Node data job not found")
        return NodeDataStatusResponse(
            status=job["status"],
            progress=float(job.get("progress", 0.0)),
            message=str(job.get("message", "")),
            terminal_reason=job.get("terminal_reason"),
            execution_metrics=job.get("execution_metrics"),
            generation_id=job.get("generation_id"),
            outcome=job.get("outcome"),
        )

    def cancel(self, job_id: str) -> NodeDataStatusResponse:
        current = self.status(job_id)
        if current.status == "running":
            self._jobs.cancel(job_id)
        return self.status(job_id)

    def clear(self, body: NodeDataRequest) -> NodeDataClearResponse:
        consumer, resolver = self._resolver(body)
        point = self._point_response(consumer, resolver)
        if point.kind != "node_output":
            return NodeDataClearResponse(status="delegated", point=point)
        slot = resolver.node_output_slot(consumer.point.producer_node_id)
        with self._slot_lock:
            with self._lock:
                running_job_id = self._job_by_slot.get(slot.digest)
                running_thread = (
                    self._threads.get(running_job_id) if running_job_id is not None else None
                )
            if running_job_id is not None:
                # A running build publishes directly from its worker, so it must
                # stop before the slot is cleared or it could publish afterwards.
                self._jobs.cancel(running_job_id)
                if running_thread is not None:
                    running_thread.join()
            resolver.store.clear_slot(slot)
        return NodeDataClearResponse(
            status="cleared", point=self._point_response(consumer, resolver)
        )

    # --------------------------------------------------------------- jobs

    def _prepare_inputs(
        self,
        job_id: str,
        request: _NodeSnapshotWorkerRequest,
        execution_context: ExecutionContext,
    ) -> _NodeSnapshotWorkerRequest:
        """Prepare snapshot-backed inputs here and bind the build to their identity.

        Preparing inputs can build or refresh input snapshots whose generations
        belong to the node's signature. Binding the identity afterwards lets the
        worker publish under the signature of the inputs it reads, and re-keying
        the running job lets ``point`` and joins find it under that identity.
        """
        from dataclasses import replace

        from haute._input_preparation import preparation_base_dir, prepare_input_snapshots
        from haute.projection import prepare_graph

        store = NodeSnapshotStore(request.project_root)
        graph = DataPointResolver(request.graph, source=request.source, store=store).graph
        prepared = prepare_graph(graph, request.node_id, source=request.source)
        prepare_input_snapshots(
            prepared.order,
            prepared.node_map,
            profile=ExecutionProfile.NODE_SNAPSHOT,
            execution_context=execution_context,
            base_dir=preparation_base_dir(graph),
            schema_only=False,
        )
        identity = _node_identity(request.graph, request.node_id, request.source, store)
        if identity.digest == request.identity_digest:
            return request
        with self._lock:
            if self._job_by_slot.get(request.slot_digest) == job_id:
                self._job_by_identity[identity.digest] = job_id
        self._store.update_job(job_id, identity_digest=identity.digest)
        return replace(request, identity_digest=identity.digest)

    def _run_job(
        self,
        job_id: str,
        request: _NodeSnapshotWorkerRequest,
        token: JobCancellation,
        superseded_thread: threading.Thread | None,
    ) -> None:
        start_time = time.monotonic()
        execution_context: ExecutionContext | None = None
        try:
            if superseded_thread is not None:
                # The replaced build was cancelled when this one registered; wait
                # for its worker to stop so both never hold memory admission.
                superseded_thread.join()
            if token.cancelled:
                raise ExecutionCancelledError("node_snapshot", job_id=job_id)
            execution_context = create_admitted_execution_context(
                operation="node_snapshot",
                profile=ExecutionProfile.NODE_SNAPSHOT,
                job_id=job_id,
                cancellation_token=token.execution_token,
            )
            bind_running_execution_metrics_publisher(self._store, job_id, execution_context)
            request = self._prepare_inputs(job_id, request, execution_context)
            budget = isolated_execution_budget(execution_context)
            outcome = _validated_worker_success(
                run_isolated_worker(
                    _run_node_snapshot_worker,
                    request,
                    budget,
                    config=worker_config_for_memory_policy(
                        memory_limit_bytes=budget.memory_limit_bytes,
                        timeout_seconds=float_env("HAUTE_NODE_SNAPSHOT_TIMEOUT", 1800.0),
                        stop_reason=lambda: token.terminal_reason if token.cancelled else None,
                        process_name=f"haute-node-snapshot-worker-{job_id}",
                    ),
                )
            )
            with self._jobs.latest_publication(job_id) as owns:
                if not owns:
                    reason = token.terminal_reason or "cancelled"
                    self._lifecycle.transition(
                        job_id,
                        to=reason,
                        message=f"Cache build {reason}",
                        expected_status="running",
                        elapsed_seconds=time.monotonic() - start_time,
                    )
                    return
                self._lifecycle.transition(
                    job_id,
                    to="completed",
                    message="Data is cached",
                    fields={
                        "progress": 1.0,
                        "generation_id": outcome.generation_id,
                        "outcome": outcome.outcome,
                        "execution_metrics": ExecutionMetricsPayload.model_validate(
                            execution_context.metrics_payload(status="completed")
                        ),
                    },
                    expected_status="running",
                    elapsed_seconds=time.monotonic() - start_time,
                )
        except _NodeSnapshotWorkerReportedError as exc:
            terminal_reason: TerminalReason
            if exc.kind == "memory":
                fields: dict[str, Any] = {
                    "error": exc.detail,
                    "error_code": "memory_limit",
                    "error_detail": cast(dict[str, Any], exc.payload),
                }
                terminal_reason = "memory_limited"
            elif exc.kind == "public_contract":
                fields = cast(dict[str, Any], exc.payload)
                terminal_reason = cast(TerminalReason, exc.terminal_reason or "contract_error")
            elif exc.kind == "quota":
                fields = {"error": exc.detail}
                terminal_reason = "error"
            else:
                fields = {"error": exc.detail}
                terminal_reason = "contract_error"
            self._lifecycle.transition(
                job_id,
                to=terminal_reason,
                message=exc.detail,
                fields=fields,
                elapsed_seconds=time.monotonic() - start_time,
            )
        except (ExecutionCancelledError, IsolatedWorkerError) as exc:
            reason = token.terminal_reason or (
                exc.terminal_reason if isinstance(exc, IsolatedWorkerError) else "cancelled"
            )
            if isinstance(exc, IsolatedWorkerRemoteError):
                logger.error(
                    "node_snapshot_worker_failed",
                    job_id=job_id,
                    remote_type=exc.remote_type,
                    remote_message=exc.remote_message,
                    remote_traceback=exc.remote_traceback,
                )
                message = _INTERNAL_ERROR_DETAIL
                transition_fields: dict[str, Any] = {"error": _INTERNAL_ERROR_DETAIL}
            else:
                message = (
                    str(exc) if isinstance(exc, IsolatedWorkerError) else f"Cache build {reason}"
                )
                transition_fields = {}
            self._lifecycle.transition(
                job_id,
                to=reason,
                message=message,
                fields=transition_fields,
                elapsed_seconds=time.monotonic() - start_time,
            )
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            if token.cancelled:
                # Preparation reports a cancelled build as a contract error; the
                # job's own cancellation or supersession reason is the outcome.
                reason = token.terminal_reason or "cancelled"
                self._lifecycle.transition(
                    job_id,
                    to=reason,
                    message=f"Cache build {reason}",
                    elapsed_seconds=time.monotonic() - start_time,
                )
            else:
                self._lifecycle.transition(
                    job_id,
                    to=contract_error_terminal_reason(exc),
                    message=str(exc),
                    fields=contract_error_job_fields(exc),
                    elapsed_seconds=time.monotonic() - start_time,
                )
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                message=str(exc.to_payload()),
                fields={"error": str(exc.to_payload())},
                elapsed_seconds=time.monotonic() - start_time,
            )
        except Exception:  # noqa: BLE001 - a background job records unexpected failures.
            logger.error("node_snapshot_job_failed", job_id=job_id, exc_info=True)
            self._lifecycle.transition(
                job_id,
                to="error",
                message=_INTERNAL_ERROR_DETAIL,
                fields={"error": _INTERNAL_ERROR_DETAIL},
                elapsed_seconds=time.monotonic() - start_time,
            )
        finally:
            if execution_context is not None:
                execution_context.release_admission()
            # The worker has terminated; a killed worker could not remove its
            # own staging directory, which would otherwise hold quota for days.
            NodeSnapshotStore(request.project_root).discard_node_output_staging(
                request.staging_token
            )
            self._jobs.release(job_id)
            with self._lock:
                self._threads.pop(job_id, None)
                for digest in [
                    digest for digest, running in self._job_by_identity.items() if running == job_id
                ]:
                    del self._job_by_identity[digest]
                if self._job_by_slot.get(request.slot_digest) == job_id:
                    del self._job_by_slot[request.slot_digest]
