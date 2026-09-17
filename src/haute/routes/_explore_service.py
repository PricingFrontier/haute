"""ExploreService: cache the analysis dataset for an Explore node.

The Explore node materialises its upstream frame into
``DataFrameExecutionCache`` so future analysis work can reuse it without
re-executing the graph. The returned report is a lightweight cache descriptor
with concise automatic overview summaries; the actual frame stays on disk.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import HTTPException

import haute.execution as execution_facade
from haute._cache import canonical_json
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
from haute._explore_cache import (
    ExplorePersistentCachePublication,
    ExplorePersistentCacheStore,
)
from haute._frame_profile import _build_frame_stats
from haute._hashing import content_hash_bytes
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._path_resolution import _infer_project_root
from haute._polars_utils import DEFAULT_STREAMING_CHUNK_SIZE
from haute._types import NodeType
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
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, find_typed_node
from haute.routes._job_lifecycle import (
    JobLifecycle,
    TerminalReason,
    bind_running_execution_metrics_publisher,
)
from haute.routes._job_store import JobStore, RunningJobFields
from haute.schemas import (
    ExecutionMetricsPayload,
    ExploreCacheReport,
    ExploreCacheSnapshotResponse,
    ExploreRunRequest,
    ExploreRunResponse,
    ExploreStatusResponse,
)

logger = get_logger(component="server.explore")


class _ExploreRunningJob(RunningJobFields):
    kind: Literal["explore"]
    progress: float
    node_id: str
    upstream_node_id: str
    source: str
    analysis_key: str


# Keep report-cache identity tied to the underlying analysis dataframe and the
# required report schema. Bump when older in-memory report payloads are
# schema-incompatible OR compute the same fields differently; dataframe cache
# identity is handled separately.
# v3: NaN counts added and distinct_count now counts valid values only
# (excludes the null and NaN buckets), so a v2 report cached for an unchanged
# frame would serve stale distinct counts.
# v4: categorical truncation now follows the number of display-label groups
# emitted to clients, rather than the raw-value cardinality.
# v5: column quality profiles and exact duplicate-row statistics added.
EXPLORE_CACHE_VERSION = 5
EXPLORE_REPORT_CACHE_MAX_ENTRIES = 16


@dataclass(frozen=True, slots=True)
class ExploreCacheSpec:
    node_id: str
    upstream_node_id: str
    source: str
    dataframe_cache_request: execution_facade.DataFrameExecutionCacheRequest
    dataframe_cache_key: str
    report_cache_key: str
    family_key: tuple[str, str, str, str]
    project_root: Path


@dataclass(frozen=True, slots=True)
class _ExploreWorkerOutcome:
    """Closed, pickle-safe result returned by the isolated Explore worker."""

    report: ExploreCacheReport | None = None
    publication: ExplorePersistentCachePublication | None = None
    failure_kind: Literal["public_contract", "contract", "memory"] | None = None
    detail: str | None = None
    payload: dict[str, Any] | None = None
    # A public contract error the child raised carries its own terminal reason
    # (automatic input preparation can report ``memory_limited``), so the parent
    # does not flatten every child contract failure to ``contract_error``.
    terminal_reason: str | None = None


class _ExploreWorkerReportedError(RuntimeError):
    """A validated, explicitly classified failure returned by the child."""

    def __init__(
        self,
        kind: Literal["public_contract", "contract", "memory"],
        detail: str,
        payload: dict[str, Any] | None,
        terminal_reason: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.payload = payload
        self.terminal_reason = terminal_reason


def _prepare_explore_spec(body: ExploreRunRequest) -> ExploreCacheSpec:
    """Build only canonical, process-independent Explore identity state."""
    graph = body.graph
    node = find_typed_node(graph, body.node_id, NodeType.EXPLORE, "explore")
    parents = graph.parents_of.get(node.id, [])
    if len(parents) != 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Explore node '{node.id}' must have exactly one upstream input "
                f"(found {len(parents)})."
            ),
        )
    upstream_node_id = parents[0]
    input_fingerprint = execution_facade.dataframe_graph_input_fingerprint(
        graph, target_node_id=node.id, source=body.source
    )
    dataframe_cache_request = execution_facade.build_dataframe_execution_cache_request(
        graph,
        node_ids=[node.id],
        namespace="explore_dataset",
        source=body.source,
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        input_fingerprint=input_fingerprint,
        target_node_id=node.id,
        enforce_contracts=True,
        preamble_ns_supplied=bool(graph.preamble),
        streaming_chunk_size=body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
    )
    dataframe_key = dataframe_cache_request.keys_by_node[node.id].cache_key
    report_payload = {
        "dataframe_cache_key": dataframe_key,
        "node_id": body.node_id,
        "source": body.source,
        "version": EXPLORE_CACHE_VERSION,
    }
    report_cache_key = (
        f"explore:v{EXPLORE_CACHE_VERSION}:"
        f"{content_hash_bytes(canonical_json(report_payload).encode())}"
    )
    project_root = _infer_project_root(project_root=None, source_file=graph.source_file)
    if project_root.parent == project_root and graph.source_file:
        pipeline_source = Path(graph.source_file)
        if pipeline_source.is_absolute():
            project_root = pipeline_source.resolve().parent
    source_file = Path(graph.source_file or "")
    if not source_file.is_absolute():
        source_file = project_root / source_file
    return ExploreCacheSpec(
        node_id=body.node_id,
        upstream_node_id=upstream_node_id,
        source=body.source,
        dataframe_cache_request=dataframe_cache_request,
        dataframe_cache_key=dataframe_key,
        report_cache_key=report_cache_key,
        family_key=("explore", str(source_file.resolve()), body.node_id, body.source),
        project_root=project_root,
    )


def _materialise_and_summarise_worker(
    body: ExploreRunRequest,
    spec: ExploreCacheSpec,
    budget: IsolatedExecutionBudget,
) -> ExploreCacheReport:
    """Execute and profile in the child, with no parent lifecycle/cache mutation."""
    from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir

    execution_context = create_isolated_execution_context(budget)
    try:
        preamble_ns = _compile_preamble(
            body.graph.preamble or "", pipeline_dir=_pipeline_dir(body.graph)
        )
        lazy_outputs, *_ = execution_facade.execute_lazy_graph(
            body.graph,
            _build_node_fn,
            target_node_id=spec.node_id,
            preamble_ns=preamble_ns or None,
            source=body.source,
            enforce_contracts=True,
            execution_context=execution_context,
            dataframe_cache_request=spec.dataframe_cache_request,
        )
        explore_lf = lazy_outputs.get(spec.node_id)
        if explore_lf is None:
            raise ValueError(f"No data arrived at Explore node '{spec.node_id}'.")
        schema = explore_lf.collect_schema()
        with execution_context.stage("explore_frame_stats"):
            frame_stats = _build_frame_stats(
                explore_lf, schema, execution_context=execution_context
            )
        return ExploreCacheReport(
            node_id=spec.node_id,
            upstream_node_id=spec.upstream_node_id,
            source=body.source,
            dataframe_cache_key=spec.dataframe_cache_key,
            row_count=frame_stats.row_count,
            column_count=len(schema.names()),
            columns=frame_stats.columns,
            overview_summary=frame_stats.overview_summary,
            generated_at=time.time(),
            execution_metrics=ExecutionMetricsPayload.model_validate(
                execution_context.metrics_payload(status="completed")
            ),
        )
    finally:
        execution_context.release_admission(preserve_primary_error=True)


def _run_explore_worker(
    body: ExploreRunRequest,
    publication: ExplorePersistentCachePublication,
    budget: IsolatedExecutionBudget,
) -> _ExploreWorkerOutcome:
    """One-shot child entrypoint: materialise and stage exactly its allocated generation."""
    try:
        spec = _prepare_explore_spec(body)
        store = ExplorePersistentCacheStore(spec.project_root)
        if publication.family_key != spec.family_key:
            raise ValueError("worker publication family does not match Explore request")
        report = _materialise_and_summarise_worker(body, spec, budget)
        key = spec.dataframe_cache_request.keys_by_node[spec.node_id]
        entry = spec.dataframe_cache_request.cache.get(key)
        if entry is None:
            raise RuntimeError("Explore worker did not materialise its dataframe cache artifact")
        prepared = store.prepare_publication(
            spec.family_key,
            report_cache_key=spec.report_cache_key,
            report=report,
            entry=entry,
            generation_id=publication.generation_id,
        )
        if prepared != publication:
            raise RuntimeError("Explore worker prepared an unexpected publication path")
        store.validate_publication(
            prepared,
            report_cache_key=spec.report_cache_key,
            report=report,
        )
        return _ExploreWorkerOutcome(report=report, publication=prepared)
    except PUBLIC_CONTRACT_ERROR_TYPES as exc:
        return _ExploreWorkerOutcome(
            failure_kind="public_contract",
            detail=str(exc),
            payload=contract_error_job_fields(exc),
            terminal_reason=contract_error_terminal_reason(exc),
        )
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        return _ExploreWorkerOutcome(
            failure_kind="memory",
            detail=str(exc),
            payload=exc.to_payload(),
        )
    except (ContractMismatchError, SchemaMismatchError, BoundedMemoryUnsupportedError) as exc:
        return _ExploreWorkerOutcome(failure_kind="contract", detail=str(exc))


def _validated_explore_worker_success(
    outcome: _ExploreWorkerOutcome,
    *,
    expected_publication: ExplorePersistentCachePublication,
) -> ExploreCacheReport:
    """Validate the closed child envelope before using any returned artifact."""
    if not isinstance(outcome, _ExploreWorkerOutcome):
        raise RuntimeError("Explore worker returned an invalid result envelope")
    if outcome.failure_kind is not None:
        if outcome.report is not None or outcome.publication is not None:
            raise RuntimeError("Explore worker mixed success and failure outcome fields")
        if not isinstance(outcome.detail, str) or not outcome.detail:
            raise RuntimeError("Explore worker failure omitted its detail")
        if outcome.failure_kind == "public_contract":
            payload = outcome.payload
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("error_code"), str)
                or not isinstance(payload.get("error_detail"), dict)
            ):
                raise RuntimeError("Explore worker returned an invalid public-contract payload")
            if outcome.terminal_reason not in ("contract_error", "memory_limited"):
                raise RuntimeError(
                    "Explore worker returned an invalid public-contract terminal reason"
                )
        elif outcome.failure_kind == "memory":
            if (
                not isinstance(outcome.payload, dict)
                or outcome.payload.get("error_code") != "memory_limit"
            ):
                raise RuntimeError("Explore worker returned an invalid memory payload")
        elif outcome.failure_kind == "contract":
            if outcome.payload is not None:
                raise RuntimeError("Explore worker contract failure carried an unexpected payload")
        else:
            raise RuntimeError("Explore worker returned an unknown failure kind")
        raise _ExploreWorkerReportedError(
            outcome.failure_kind,
            outcome.detail,
            outcome.payload,
            outcome.terminal_reason,
        )
    if outcome.detail is not None or outcome.payload is not None:
        raise RuntimeError("Explore worker success carried failure fields")
    if not isinstance(outcome.report, ExploreCacheReport):
        raise RuntimeError("Explore worker omitted its validated report")
    if outcome.publication != expected_publication:
        raise RuntimeError("Explore worker returned an unexpected publication")
    return outcome.report


class ExploreService:
    """Materialise upstream data for Explore nodes and cache the result."""

    def __init__(
        self,
        store: JobStore,
        *,
        report_cache: LRUCache[str, ExploreCacheReport] | None = None,
    ) -> None:
        self._store = store
        self._lifecycle = JobLifecycle(store)
        self._jobs = CancellableJobRegistry()
        self._report_cache = report_cache or LRUCache(max_size=EXPLORE_REPORT_CACHE_MAX_ENTRIES)

    def start(self, body: ExploreRunRequest) -> ExploreRunResponse:
        spec = self.prepare_spec(body)
        key = spec.dataframe_cache_request.keys_by_node[spec.node_id]
        if not body.refresh:
            cached = self._report_cache.get(spec.report_cache_key)
            if cached is not None and spec.dataframe_cache_request.cache.get(key) is not None:
                return ExploreRunResponse(
                    status="completed",
                    cached=True,
                    message="Explore cache hit",
                    result=cached,
                )

            persistent_store = ExplorePersistentCacheStore(spec.project_root)
            with persistent_store.lease(
                spec.family_key,
                report_cache_key=spec.report_cache_key,
            ) as snapshot:
                if snapshot is not None and snapshot.state == "current":
                    if snapshot.report is None:
                        raise RuntimeError("Current durable Explore cache omitted its report")
                    persistent_store.restore(
                        snapshot,
                        spec.dataframe_cache_request,
                        node_id=spec.node_id,
                    )
                    self._report_cache.put(spec.report_cache_key, snapshot.report)
                    return ExploreRunResponse(
                        status="completed",
                        cached=True,
                        message="Explore cache hit",
                        result=snapshot.report,
                    )
        else:
            self._report_cache.evict_where(lambda cache_key: cache_key == spec.report_cache_key)
            spec.dataframe_cache_request.cache.evict_where(
                lambda cache_key: cache_key == key.cache_key
            )

        initial_job: _ExploreRunningJob = {
            "kind": "explore",
            "status": "running",
            "progress": 0.0,
            "message": "Starting Explore cache materialisation",
            "node_id": body.node_id,
            "upstream_node_id": spec.upstream_node_id,
            "source": body.source,
            "analysis_key": spec.report_cache_key,
        }
        job_id = self._store.create_job(initial_job)
        token, previous_job_id = self._jobs.register_latest(spec.family_key, job_id)
        if previous_job_id is not None:
            self._lifecycle.transition(
                previous_job_id,
                to="superseded",
                message="Superseded by a newer Explore request.",
                expected_status="running",
            )
        publication = ExplorePersistentCacheStore(spec.project_root).new_publication(
            spec.family_key
        )

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, body, spec, token, publication),
            name=f"haute-explore-{job_id}",
            daemon=True,
        )
        thread.start()
        return ExploreRunResponse(
            status="started",
            job_id=job_id,
            message="Explore cache materialisation started",
        )

    def cache_status(self, body: ExploreRunRequest) -> ExploreCacheSnapshotResponse:
        """Inspect and, on an exact hit, restore one durable Explore generation."""

        spec = self.prepare_spec(body)
        persistent_store = ExplorePersistentCacheStore(spec.project_root)
        with persistent_store.lease(
            spec.family_key,
            report_cache_key=spec.report_cache_key,
        ) as snapshot:
            if snapshot is not None:
                if snapshot.state == "stale":
                    return ExploreCacheSnapshotResponse(
                        state="stale",
                        message="Explore cache is stale",
                    )
                if snapshot.report is None:
                    raise RuntimeError("Current durable Explore cache omitted its report")

                persistent_store.restore(
                    snapshot,
                    spec.dataframe_cache_request,
                    node_id=spec.node_id,
                )
                self._report_cache.put(spec.report_cache_key, snapshot.report)
                return ExploreCacheSnapshotResponse(
                    state="current",
                    message="Explore data is cached",
                    result=snapshot.report,
                )

        # Only when no durable generation exists may an exact report plus
        # dataframe already present in the process-local caches satisfy
        # `current`; the durable generation is always inspected first so a
        # corrupt or stale generation is never hidden by warm process caches.
        key = spec.dataframe_cache_request.keys_by_node[spec.node_id]
        cached = self._report_cache.get(spec.report_cache_key)
        if cached is not None and spec.dataframe_cache_request.cache.get(key) is not None:
            return ExploreCacheSnapshotResponse(
                state="current",
                message="Explore data is cached",
                result=cached,
            )
        return ExploreCacheSnapshotResponse(
            state="missing",
            message="Explore data needs caching",
        )

    def status(self, job_id: str) -> ExploreStatusResponse:
        job = self._store.require_job(job_id)
        if job.get("kind") != "explore":
            raise HTTPException(status_code=404, detail="Explore job not found")
        return ExploreStatusResponse(
            status=job["status"],
            progress=job.get("progress", 0.0),
            message=job.get("message", ""),
            result=job.get("result"),
            terminal_reason=job.get("terminal_reason"),
            execution_metrics=job.get("execution_metrics"),
        )

    def cancel(self, job_id: str) -> ExploreStatusResponse:
        current = self.status(job_id)
        if current.status == "running":
            self._jobs.cancel(job_id)
        return self.status(job_id)

    def prepare_spec(self, body: ExploreRunRequest) -> ExploreCacheSpec:
        """Resolve the canonical Explore dataframe/report cache identities."""
        return _prepare_explore_spec(body)

    def _run_job(
        self,
        job_id: str,
        body: ExploreRunRequest,
        spec: ExploreCacheSpec,
        token: JobCancellation,
        publication: ExplorePersistentCachePublication,
    ) -> None:
        start_time = time.monotonic()
        execution_context: ExecutionContext | None = None
        try:
            execution_context = create_admitted_execution_context(
                operation="explore_cache",
                profile=ExecutionProfile.EXPLORE_ANALYSIS,
                job_id=job_id,
                cancellation_token=token.execution_token,
            )
            bind_running_execution_metrics_publisher(self._store, job_id, execution_context)
            budget = isolated_execution_budget(execution_context)
            outcome = run_isolated_worker(
                _run_explore_worker,
                body,
                publication,
                budget,
                config=worker_config_for_memory_policy(
                    memory_limit_bytes=budget.memory_limit_bytes,
                    timeout_seconds=float_env("HAUTE_EXPLORE_TIMEOUT", 1800.0),
                    stop_reason=lambda: token.terminal_reason if token.cancelled else None,
                    process_name=f"haute-explore-worker-{job_id}",
                ),
            )
            report = _validated_explore_worker_success(
                outcome,
                expected_publication=publication,
            )
            if not self._publish_completed_result(
                job_id,
                spec,
                report,
                publication,
                start_time=start_time,
            ):
                reason = token.terminal_reason or "cancelled"
                self._lifecycle.transition(
                    job_id,
                    to=reason,
                    message=f"Explore cache materialisation {reason}",
                    expected_status="running",
                    elapsed_seconds=time.monotonic() - start_time,
                )
                return
        except _ExploreWorkerReportedError as exc:
            if exc.kind == "memory":
                payload = cast(dict[str, Any], exc.payload)
                fields = {
                    "error": exc.detail,
                    "error_code": "memory_limit",
                    "error_detail": payload,
                }
                terminal_reason: TerminalReason = "memory_limited"
            elif exc.kind == "public_contract":
                fields = cast(dict[str, Any], exc.payload)
                terminal_reason = cast(TerminalReason, exc.terminal_reason or "contract_error")
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
                    "explore_cache_worker_failed",
                    job_id=job_id,
                    remote_type=exc.remote_type,
                    remote_message=exc.remote_message,
                    remote_traceback=exc.remote_traceback,
                    exc_info=True,
                )
                message = _INTERNAL_ERROR_DETAIL
                transition_fields: dict[str, Any] = {"error": _INTERNAL_ERROR_DETAIL}
            else:
                message = (
                    str(exc)
                    if isinstance(exc, IsolatedWorkerError)
                    else f"Explore cache materialisation {reason}"
                )
                transition_fields = {}
            self._lifecycle.transition(
                job_id,
                to=reason,
                message=message,
                fields=transition_fields,
                elapsed_seconds=time.monotonic() - start_time,
            )
        except ExecutionAdmissionError as exc:
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                message=str(exc.to_payload()),
                fields={"error": str(exc.to_payload())},
                elapsed_seconds=time.monotonic() - start_time,
            )
        except ExecutionMemoryLimitExceededError as exc:
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                message=str(exc.to_payload()),
                fields={"error": str(exc.to_payload())},
                elapsed_seconds=time.monotonic() - start_time,
            )
        except PUBLIC_CONTRACT_ERROR_TYPES as exc:
            self._lifecycle.transition(
                job_id,
                to=contract_error_terminal_reason(exc),
                message=str(exc),
                fields=contract_error_job_fields(exc),
                elapsed_seconds=time.monotonic() - start_time,
            )
        except (ContractMismatchError, SchemaMismatchError, BoundedMemoryUnsupportedError) as exc:
            self._lifecycle.transition(
                job_id,
                to="contract_error",
                message=str(exc),
                fields={"error": str(exc)},
                elapsed_seconds=time.monotonic() - start_time,
            )
        except Exception as exc:  # noqa: BLE001 - route job captures unexpected worker failures.
            logger.error("explore_cache_failed", job_id=job_id, error=str(exc), exc_info=True)
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
            # The child never owns selection; every unsuccessful path removes
            # only the staging directory allocated for this exact job.
            persistent_store = ExplorePersistentCacheStore(spec.project_root)
            try:
                persistent_store.discard_publication(publication)
            except Exception:
                logger.warning("explore_cache_staging_discard_failed", job_id=job_id, exc_info=True)
            self._jobs.release(job_id)

    def _publish_completed_result(
        self,
        job_id: str,
        spec: ExploreCacheSpec,
        report: ExploreCacheReport,
        publication: ExplorePersistentCachePublication,
        *,
        start_time: float,
    ) -> bool:
        """Select and expose a prepared result only while this job is latest."""

        dataframe_key = spec.dataframe_cache_request.keys_by_node[spec.node_id]
        dataframe_cache = spec.dataframe_cache_request.cache
        persistent_store = ExplorePersistentCacheStore(spec.project_root)
        committed = False
        restored = False
        try:
            persistent_store.validate_publication(
                publication, report_cache_key=spec.report_cache_key, report=report
            )

            with self._jobs.latest_publication(job_id) as owns_publication:
                if not owns_publication:
                    return False
                persistent_store.restore_publication(
                    publication,
                    spec.dataframe_cache_request,
                    node_id=spec.node_id,
                    report_cache_key=spec.report_cache_key,
                    report=report,
                )
                restored = True
                persistent_store.commit_publication(publication)
                committed = True
                self._report_cache.put(spec.report_cache_key, report)
                try:
                    transitioned = self._lifecycle.transition(
                        job_id,
                        to="completed",
                        message="Explore cache materialisation complete",
                        fields={
                            "progress": 1.0,
                            "result": report,
                            "execution_metrics": report.execution_metrics,
                        },
                        expected_status="running",
                        elapsed_seconds=time.monotonic() - start_time,
                    )
                except BaseException:
                    self._report_cache.evict_where(
                        lambda cache_key: cache_key == spec.report_cache_key
                    )
                    raise
                if transitioned is None:
                    self._report_cache.evict_where(
                        lambda cache_key: cache_key == spec.report_cache_key
                    )
                    raise RuntimeError(
                        f"Explore job {job_id!r} lost running status during publication"
                    )
            return True
        finally:
            if committed:
                persistent_store.retire_unleased_generations(spec.family_key)
            else:
                if restored:
                    dataframe_cache.evict_where(
                        lambda cache_key: cache_key == dataframe_key.cache_key
                    )
                persistent_store.discard_publication(publication)
