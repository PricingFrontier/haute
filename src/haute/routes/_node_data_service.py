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

from haute._analysis_results import AnalysisKey, AnalysisResultStore
from haute._data_points import (
    CacheRequiredError,
    ConsumerPoint,
    DataPointResolver,
    NodeDataPointInvalidError,
    PointColumnsMissingError,
    PointDataChangedError,
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
    IsolatedWorkerMemoryLimitExceededError,
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
    NodeDataProfile,
    NodeDataProfileResponse,
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


class _NodeProfileRunningJob(RunningJobFields):
    kind: Literal["node_profile"]
    progress: float
    node_id: str
    producer_node_id: str
    source: str
    data_version: str


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


class _WorkerReportedError(RuntimeError):
    """A validated, explicitly classified failure returned by a node-data worker."""

    def __init__(
        self,
        kind: Literal["public_contract", "contract", "memory", "quota", "changed"],
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


PROFILE_ANALYSIS_KIND = "profile"
PROFILE_ANALYSIS_VERSION = 1


@dataclass(frozen=True, slots=True)
class _ProfileWorkerRequest:
    """Closed, pickle-safe handoff of the resolution the parent leases."""

    graph: Any
    source: str
    project_root: str
    resolution: PointResolution


@dataclass(frozen=True, slots=True)
class _ProfileWorkerOutcome:
    profile: NodeDataProfile | None = None
    failure_kind: Literal["public_contract", "contract", "memory", "changed"] | None = None
    detail: str | None = None
    payload: dict[str, Any] | None = None
    terminal_reason: str | None = None


def _run_profile_worker(
    request: _ProfileWorkerRequest,
    budget: IsolatedExecutionBudget,
) -> _ProfileWorkerOutcome:
    """One-shot child entrypoint: profile exactly the data the parent leased."""
    from haute._sandbox import set_project_root

    set_project_root(Path(request.project_root))
    try:
        execution_context = create_isolated_execution_context(budget)
        try:
            return _ProfileWorkerOutcome(profile=_profile_point(request, execution_context))
        finally:
            execution_context.release_admission(preserve_primary_error=True)
    except PUBLIC_CONTRACT_ERROR_TYPES as exc:
        return _ProfileWorkerOutcome(
            failure_kind="public_contract",
            detail=str(exc),
            payload=contract_error_job_fields(exc),
            terminal_reason=contract_error_terminal_reason(exc),
        )
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        return _ProfileWorkerOutcome(
            failure_kind="memory", detail=str(exc), payload=exc.to_payload()
        )
    except (CacheRequiredError, PointDataChangedError) as exc:
        return _ProfileWorkerOutcome(failure_kind="changed", detail=str(exc))
    except (
        ContractMismatchError,
        SchemaMismatchError,
        BoundedMemoryUnsupportedError,
        PointColumnsMissingError,
    ) as exc:
        return _ProfileWorkerOutcome(failure_kind="contract", detail=str(exc))


def _profile_point(
    request: _ProfileWorkerRequest, execution_context: ExecutionContext
) -> NodeDataProfile:
    from haute._frame_profile import _build_frame_stats

    resolver = DataPointResolver(
        request.graph, source=request.source, store=NodeSnapshotStore(request.project_root)
    )
    with resolver.lease_resolved(
        request.resolution, exact=True, execution_context=execution_context
    ) as leased:
        schema = leased.scan.collect_schema()
        with execution_context.stage("node_data_profile"):
            stats = _build_frame_stats(leased.scan, schema, execution_context=execution_context)
        if request.resolution.kind == "data_input" and request.resolution.input_identity is None:
            # A direct file is not pinned by the lease: prove it was not rewritten
            # while its statistics were read.
            observed = resolver.resolve(request.resolution.point, request.resolution.demand)
            if observed.data_version != leased.data_version:
                raise PointDataChangedError(request.resolution)
        return NodeDataProfile(
            row_count=stats.row_count,
            column_count=len(schema.names()),
            columns=stats.columns,
            overview_summary=stats.overview_summary,
            data_version=leased.data_version,
            generated_at=time.time(),
        )


def _validated_profile_success(outcome: object) -> NodeDataProfile:
    if not isinstance(outcome, _ProfileWorkerOutcome):
        raise RuntimeError("profile worker returned an invalid result envelope")
    if outcome.failure_kind is not None:
        if outcome.profile is not None:
            raise RuntimeError("profile worker mixed success and failure fields")
        if not isinstance(outcome.detail, str) or not outcome.detail:
            raise RuntimeError("profile worker failure omitted its detail")
        if outcome.failure_kind == "public_contract" and (
            not isinstance(outcome.payload, dict)
            or outcome.terminal_reason not in ("contract_error", "memory_limited")
        ):
            raise RuntimeError("profile worker returned an invalid contract payload")
        if outcome.failure_kind == "memory" and (
            not isinstance(outcome.payload, dict)
            or outcome.payload.get("error_code") != "memory_limit"
        ):
            raise RuntimeError("profile worker returned an invalid memory payload")
        raise _WorkerReportedError(
            outcome.failure_kind, outcome.detail, outcome.payload, outcome.terminal_reason
        )
    if not isinstance(outcome.profile, NodeDataProfile):
        raise RuntimeError("profile worker omitted its profile")
    return outcome.profile


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
        raise _WorkerReportedError(
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
        self._profile_lock = threading.Lock()
        self._profile_jobs: dict[tuple[str, str], str] = {}

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
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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

    def point_for(
        self, consumer: ConsumerPoint, resolver: DataPointResolver
    ) -> NodeDataPointResponse:
        """The point for an explicit consumer and column demand.

        A caller that reads more than the saved node does — an editor asking
        about the factor being edited — reports the point for what it is about
        to read, so a point missing that column reads as not current rather
        than current for somebody else's demand.
        """
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
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    def profile(self, body: NodeDataRequest) -> NodeDataProfileResponse:
        """Return the point's data profile for its current data version, or start computing it."""
        consumer, resolver = self._resolver(body)
        point = self._point_response(consumer, resolver)
        with self._profile_lock:
            try:
                resolution = resolver.resolve(consumer.point, NodeSnapshotColumns.all())
            except NodeDataPointInvalidError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if resolution.state != "current" or resolution.data_version is None:
                return NodeDataProfileResponse(
                    status="cache_required",
                    message=f"The whole dataset is {resolution.state}; cache it to profile it.",
                    point=point,
                )
            point_digest = resolver.point_digest(consumer.point)
            key = AnalysisKey(
                point_digest,
                resolution.data_version,
                PROFILE_ANALYSIS_KIND,
                PROFILE_ANALYSIS_VERSION,
            )
            project_root = node_data_project_root()
            stored = AnalysisResultStore(project_root).read(key, NodeDataProfile)
            if stored is not None:
                return NodeDataProfileResponse(
                    status="completed", message="Profile is ready", result=stored, point=point
                )
            with self._lock:
                running = self._profile_jobs.get((point_digest, resolution.data_version))
            if running is not None:
                job = self._store.get_job(running)
                if job is not None and job.get("status") == "running":
                    return NodeDataProfileResponse(
                        status="joined",
                        job_id=running,
                        message="Joined the running profile",
                        point=point,
                    )
            initial_job: _NodeProfileRunningJob = {
                "kind": "node_profile",
                "status": "running",
                "progress": 0.0,
                "message": "Profiling data",
                "node_id": consumer.consumer_node_id,
                "producer_node_id": consumer.point.producer_node_id,
                "source": body.source,
                "data_version": resolution.data_version,
            }
            job_id = self._store.create_job(initial_job)
            with self._lock:
                self._profile_jobs[(point_digest, resolution.data_version)] = job_id
                token, _previous = self._jobs.register_latest(("profile", job_id), job_id)
            request = _ProfileWorkerRequest(
                graph=body.graph,
                source=body.source,
                project_root=str(project_root),
                resolution=resolution,
            )
            thread = threading.Thread(
                target=self._run_profile_job,
                args=(job_id, key, request, resolver, token),
                name=f"haute-node-profile-{job_id}",
                daemon=True,
            )
            with self._lock:
                self._threads[job_id] = thread
            thread.start()
        return NodeDataProfileResponse(
            status="started", job_id=job_id, message="Profiling started", point=point
        )

    def _run_profile_job(
        self,
        job_id: str,
        key: AnalysisKey,
        request: _ProfileWorkerRequest,
        resolver: DataPointResolver,
        token: JobCancellation,
    ) -> None:
        start_time = time.monotonic()
        execution_context: ExecutionContext | None = None
        try:
            execution_context = create_admitted_execution_context(
                operation="node_data_profile",
                profile=ExecutionProfile.EXPLORE_ANALYSIS,
                job_id=job_id,
                cancellation_token=token.execution_token,
            )
            bind_running_execution_metrics_publisher(self._store, job_id, execution_context)
            # The parent holds the lease for the whole job, so the worker reads
            # exactly this generation even if the point is refreshed or cleared.
            with resolver.lease_resolved(request.resolution, exact=True):
                budget = isolated_execution_budget(execution_context)
                profile = _validated_profile_success(
                    run_isolated_worker(
                        _run_profile_worker,
                        request,
                        budget,
                        config=worker_config_for_memory_policy(
                            memory_limit_bytes=budget.memory_limit_bytes,
                            timeout_seconds=float_env("HAUTE_NODE_DATA_PROFILE_TIMEOUT", 1800.0),
                            stop_reason=lambda: token.terminal_reason if token.cancelled else None,
                            process_name=f"haute-node-profile-worker-{job_id}",
                        ),
                    )
                )
            # Publication is one indivisible step against cancellation and clear:
            # the profile lock excludes ``_clear_analyses``, and the registry guard
            # excludes a cancellation racing the store write.
            with self._profile_lock, self._jobs.latest_publication(job_id) as owns:
                if not owns:
                    raise ExecutionCancelledError("node_data_profile", job_id=job_id)
                AnalysisResultStore(request.project_root).write(key, profile)
                self._lifecycle.transition(
                    job_id,
                    to="completed",
                    message="Profile is ready",
                    fields={
                        "progress": 1.0,
                        "profile": profile,
                        "execution_metrics": ExecutionMetricsPayload.model_validate(
                            execution_context.metrics_payload(status="completed")
                        ),
                    },
                    expected_status="running",
                    elapsed_seconds=time.monotonic() - start_time,
                )
        except Exception as exc:  # noqa: BLE001 - a background job records every failure.
            self._fail_job(job_id, exc, token, start_time, label="Profile")
        finally:
            if execution_context is not None:
                execution_context.release_admission()
            self._jobs.release(job_id)
            with self._lock:
                self._threads.pop(job_id, None)
                profile_key = (key.point_digest, key.data_version)
                if self._profile_jobs.get(profile_key) == job_id:
                    del self._profile_jobs[profile_key]

    def status(self, job_id: str) -> NodeDataStatusResponse:
        job = self._store.require_job(job_id)
        if job.get("kind") not in ("node_data", "node_profile"):
            raise HTTPException(status_code=404, detail="Node data job not found")
        return NodeDataStatusResponse(
            status=job["status"],
            progress=float(job.get("progress", 0.0)),
            message=str(job.get("message", "")),
            terminal_reason=job.get("terminal_reason"),
            error=job.get("error"),
            error_code=job.get("error_code"),
            error_detail=job.get("error_detail"),
            execution_metrics=job.get("execution_metrics"),
            generation_id=job.get("generation_id"),
            outcome=job.get("outcome"),
            profile=job.get("profile"),
        )

    def cancel(self, job_id: str) -> NodeDataStatusResponse:
        current = self.status(job_id)
        if current.status == "running":
            self._jobs.cancel(job_id)
        return self.status(job_id)

    def clear(self, body: NodeDataRequest) -> NodeDataClearResponse:
        consumer, resolver = self._resolver(body)
        point = self._point_response(consumer, resolver)
        self._clear_analyses(resolver.point_digest(consumer.point))
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

    def _clear_analyses(self, point_digest: str) -> None:
        """Stop the point's running profiles and remove every stored analysis of it."""
        with self._profile_lock:
            with self._lock:
                running = [
                    job_id
                    for (digest, _version), job_id in self._profile_jobs.items()
                    if digest == point_digest
                ]
            for job_id in running:
                self._jobs.cancel(job_id)
            AnalysisResultStore(node_data_project_root()).clear_point(point_digest)

    # --------------------------------------------------------------- jobs

    def _fail_job(
        self,
        job_id: str,
        exc: Exception,
        token: JobCancellation,
        start_time: float,
        *,
        label: str,
    ) -> None:
        """Record one job failure with the shared worker failure envelope."""
        elapsed = time.monotonic() - start_time
        if isinstance(exc, _WorkerReportedError):
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
            elif exc.kind == "changed":
                fields = {"error": exc.detail, "error_code": PointDataChangedError.error_code}
                terminal_reason = "contract_error"
            else:
                fields = {"error": exc.detail}
                terminal_reason = "contract_error"
            self._lifecycle.transition(
                job_id,
                to=terminal_reason,
                message=exc.detail,
                fields=fields,
                elapsed_seconds=elapsed,
            )
        elif isinstance(exc, (ExecutionCancelledError, IsolatedWorkerError)):
            reason = token.terminal_reason or (
                exc.terminal_reason if isinstance(exc, IsolatedWorkerError) else "cancelled"
            )
            if isinstance(exc, IsolatedWorkerRemoteError):
                logger.error(
                    "node_data_worker_failed",
                    job_id=job_id,
                    remote_type=exc.remote_type,
                    remote_message=exc.remote_message,
                    remote_traceback=exc.remote_traceback,
                )
                message = _INTERNAL_ERROR_DETAIL
                transition_fields: dict[str, Any] = {"error": _INTERNAL_ERROR_DETAIL}
            else:
                message = str(exc) if isinstance(exc, IsolatedWorkerError) else f"{label} {reason}"
                transition_fields = {}
            if reason == "memory_limited":
                # A worker the parent killed over its RSS limit reports the same
                # error code as one that reported the limit itself.
                transition_fields["error"] = message
                transition_fields["error_code"] = "memory_limit"
                if isinstance(exc, IsolatedWorkerMemoryLimitExceededError):
                    transition_fields["error_detail"] = {
                        "error_code": "memory_limit",
                        "rss_bytes": exc.rss_bytes,
                        "rss_limit_bytes": exc.rss_limit_bytes,
                    }
            self._lifecycle.transition(
                job_id,
                to=reason,
                message=message,
                fields=transition_fields,
                elapsed_seconds=elapsed,
            )
        elif isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
            if token.cancelled:
                # Preparation reports a cancelled job as a contract error; the
                # job's own cancellation or supersession reason is the outcome.
                reason = token.terminal_reason or "cancelled"
                self._lifecycle.transition(
                    job_id, to=reason, message=f"{label} {reason}", elapsed_seconds=elapsed
                )
            else:
                self._lifecycle.transition(
                    job_id,
                    to=contract_error_terminal_reason(exc),
                    message=str(exc),
                    fields=contract_error_job_fields(exc),
                    elapsed_seconds=elapsed,
                )
        elif isinstance(exc, (ExecutionAdmissionError, ExecutionMemoryLimitExceededError)):
            payload = exc.to_payload()
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                message=str(payload),
                fields={
                    "error": str(payload),
                    "error_code": "memory_limit",
                    "error_detail": payload,
                },
                elapsed_seconds=elapsed,
            )
        elif isinstance(exc, (CacheRequiredError, PointDataChangedError)):
            self._lifecycle.transition(
                job_id,
                to="contract_error",
                message=str(exc),
                fields={"error": str(exc), "error_code": exc.error_code},
                elapsed_seconds=elapsed,
            )
        else:
            logger.error("node_data_job_failed", job_id=job_id, exc_info=True)
            self._lifecycle.transition(
                job_id,
                to="error",
                message=_INTERNAL_ERROR_DETAIL,
                fields={"error": _INTERNAL_ERROR_DETAIL},
                elapsed_seconds=elapsed,
            )

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
        from haute.execution import prepare_graph

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

    def _discard_staging(self, request: _NodeSnapshotWorkerRequest) -> None:
        """Remove anything left under this build's staging token."""
        NodeSnapshotStore(request.project_root).discard_node_output_staging(request.staging_token)

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
            # The worker has terminated and published whatever it published, so
            # anything left under its staging token is waste. It goes before the
            # terminal status below, so a client that sees the outcome never
            # sees a staging directory the build left behind.
            self._discard_staging(request)
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
        except Exception as exc:  # noqa: BLE001 - a background job records every failure.
            # Before the failure is published, for the same reason.
            self._discard_staging(request)
            self._fail_job(job_id, exc, token, start_time, label="Cache build")
        finally:
            if execution_context is not None:
                execution_context.release_admission()
            # Last resort, for a path that failed while publishing its status: a
            # killed worker could not remove its own staging directory, which
            # would otherwise hold quota for days.
            self._discard_staging(request)
            self._jobs.release(job_id)
            with self._lock:
                self._threads.pop(job_id, None)
                for digest in [
                    digest for digest, running in self._job_by_identity.items() if running == job_id
                ]:
                    del self._job_by_identity[digest]
                if self._job_by_slot.get(request.slot_digest) == job_id:
                    del self._job_by_slot[request.slot_digest]
