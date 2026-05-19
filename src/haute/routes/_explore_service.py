"""ExploreService: cache the analysis dataset for an Explore node.

This is the v1 of the Explore node — its only responsibility is to materialise
the Explore node's frame into ``DataFrameExecutionCache`` so future analysis
work can reuse it without re-executing the graph. The returned report is a
lightweight cache descriptor (row/column count, dataframe cache key); the
actual frame stays on disk.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import polars as pl
from fastapi import HTTPException

import haute.execution as execution_facade
from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._hashing import content_hash_bytes
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._polars_utils import DEFAULT_STREAMING_CHUNK_SIZE, streaming_collect
from haute._types import NodeType
from haute.errors import BoundedMemoryUnsupportedError, ContractMismatchError, SchemaMismatchError
from haute.routes._background_jobs import CancellableJobRegistry, JobCancellation
from haute.routes._helpers import find_typed_node
from haute.routes._job_lifecycle import JobLifecycle, bind_running_execution_metrics_publisher
from haute.routes._job_store import JobStore
from haute.schemas import (
    ExecutionMetricsPayload,
    ExploreCacheReport,
    ExploreColumnStat,
    ExploreRunRequest,
    ExploreRunResponse,
    ExploreStatusResponse,
)

logger = get_logger(component="server.explore")

EXPLORE_CACHE_VERSION = 1
EXPLORE_REPORT_CACHE_MAX_ENTRIES = 16

# Dtypes whose values are not hashable in Polars and therefore cannot have
# ``n_unique`` computed.  Pre-detected by dtype so we never invoke n_unique on
# a column that is guaranteed to fail.
_UNHASHABLE_DTYPES: tuple[type[pl.DataType], ...] = (pl.Object,)

# Example-value display-truncation length. Eighty characters mirrors the
# column preview budget the Schema Table card uses on the frontend.
_EXAMPLE_VALUE_MAX_CHARS = 80
_EXAMPLE_VALUE_TRUNCATION_MARKER = "…"


@dataclass(frozen=True, slots=True)
class ExploreFrameStats:
    row_count: int
    columns: list[ExploreColumnStat]


def _is_unhashable_dtype(dtype: pl.DataType) -> bool:
    """Return True when ``n_unique`` cannot be computed for *dtype*.

    Object columns are excluded because Polars raises ``InvalidOperationError``
    when their values are hashed. All other dtypes (including Struct, Decimal,
    Datetime, List, Array, etc.) are allowed through to ``n_unique``.
    """

    return dtype.base_type() in _UNHASHABLE_DTYPES


def _truncate_example(text: str) -> str:
    """Clip *text* to the example-preview budget with an ellipsis marker."""

    if len(text) <= _EXAMPLE_VALUE_MAX_CHARS:
        return text
    return text[:_EXAMPLE_VALUE_MAX_CHARS] + _EXAMPLE_VALUE_TRUNCATION_MARKER


def _format_example_value(value: Any) -> str | None:
    """Return a compact, one-cell display string for a column example value."""

    if value is None:
        return None
    if isinstance(value, str):
        return _truncate_example(value)
    if isinstance(value, pl.Series):
        value = value.to_list()
    if isinstance(value, dict | list | tuple):
        return _truncate_example(json.dumps(value, ensure_ascii=False, default=str))
    return _truncate_example(str(value))


def _build_frame_stats(
    lf: pl.LazyFrame,
    schema: pl.Schema,
    *,
    execution_context: ExecutionContext,
) -> ExploreFrameStats:
    """Compute row count and per-column schema stats for an Explore frame.

    Runs a single batched ``streaming_collect`` for ``row_count``,
    ``null_count``, ``n_unique``, and the first non-null example value so we
    do not pay repeated full-frame scans. Object columns skip ``n_unique``
    (their distinct_count stays ``None``).
    """

    column_names = list(schema.names())
    aggregations: list[pl.Expr] = [pl.len().alias("row_count")]
    for name in column_names:
        aggregations.append(pl.col(name).null_count().alias(f"null::{name}"))
        aggregations.append(pl.col(name).drop_nulls().first().alias(f"example::{name}"))
        if not _is_unhashable_dtype(schema[name]):
            aggregations.append(pl.col(name).n_unique().alias(f"unique::{name}"))

    aggregate_row = streaming_collect(
        lf.select(aggregations),
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        execution_context=execution_context,
    ).row(0, named=True)

    stats: list[ExploreColumnStat] = []
    for name in column_names:
        dtype = schema[name]
        null_count = int(aggregate_row[f"null::{name}"])
        distinct_count: int | None
        if _is_unhashable_dtype(dtype):
            distinct_count = None
        else:
            distinct_count = int(aggregate_row[f"unique::{name}"])

        stats.append(
            ExploreColumnStat(
                name=name,
                dtype=str(dtype),
                null_count=null_count,
                distinct_count=distinct_count,
                example_value=_format_example_value(aggregate_row[f"example::{name}"]),
            )
        )
    return ExploreFrameStats(row_count=int(aggregate_row["row_count"]), columns=stats)


def _build_column_stats(
    lf: pl.LazyFrame,
    schema: pl.Schema,
    *,
    execution_context: ExecutionContext,
) -> list[ExploreColumnStat]:
    """Compute per-column schema stats for tests and legacy internal callers."""

    return _build_frame_stats(lf, schema, execution_context=execution_context).columns


@dataclass(frozen=True, slots=True)
class ExploreCacheSpec:
    node_id: str
    upstream_node_id: str
    source: str
    dataframe_cache_request: execution_facade.DataFrameExecutionCacheRequest
    dataframe_cache_key: str
    report_cache_key: str
    family_key: tuple[str, str, str, str]


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
        spec = self._prepare_spec(body)
        cached = self._report_cache.get(spec.report_cache_key)
        if cached is not None:
            return ExploreRunResponse(
                status="completed",
                cached=True,
                message="Explore cache hit",
                result=cached,
            )

        job_id = self._store.create_job(
            {
                "status": "running",
                "progress": 0.0,
                "message": "Starting Explore cache materialisation",
                "node_id": body.node_id,
                "upstream_node_id": spec.upstream_node_id,
                "source": body.source,
                "analysis_key": spec.report_cache_key,
            }
        )
        token, previous_job_id = self._jobs.register_latest(spec.family_key, job_id)
        if previous_job_id is not None:
            self._lifecycle.transition(
                previous_job_id,
                to="superseded",
                message="Superseded by a newer Explore request.",
                expected_status="running",
            )

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, body, spec, token),
            name=f"haute-explore-{job_id}",
            daemon=True,
        )
        thread.start()
        return ExploreRunResponse(
            status="started",
            job_id=job_id,
            message="Explore cache materialisation started",
        )

    def status(self, job_id: str) -> ExploreStatusResponse:
        job = self._store.require_job(job_id)
        return ExploreStatusResponse(
            status=job["status"],
            progress=job.get("progress", 0.0),
            message=job.get("message", ""),
            result=job.get("result"),
            terminal_reason=job.get("terminal_reason"),
            execution_metrics=job.get("execution_metrics"),
        )

    def cancel(self, job_id: str) -> ExploreStatusResponse:
        cancelled = self._jobs.cancel(job_id)
        job = self._store.require_job(job_id)
        if cancelled or job.get("status") == "running":
            self._lifecycle.transition(
                job_id,
                to="cancelled",
                message="Explore cache materialisation cancelled",
                expected_status="running",
            )
            self._jobs.release(job_id)
        return self.status(job_id)

    def _prepare_spec(self, body: ExploreRunRequest) -> ExploreCacheSpec:
        from haute.executor import ENFORCE_CONTRACTS

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
            graph,
            target_node_id=node.id,
            source=body.source,
        )
        dataframe_cache_request = execution_facade.build_dataframe_execution_cache_request(
            graph,
            node_ids=[node.id],
            namespace="explore_dataset",
            source=body.source,
            profile=ExecutionProfile.EXPLORE_ANALYSIS,
            input_fingerprint=input_fingerprint,
            target_node_id=node.id,
            enforce_contracts=ENFORCE_CONTRACTS,
            preamble_ns_supplied=bool(graph.preamble),
            streaming_chunk_size=body.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
        )
        dataframe_key = dataframe_cache_request.keys_by_node[node.id].cache_key
        report_payload: dict[str, Any] = {
            "dataframe_cache_key": dataframe_key,
            "node_id": body.node_id,
            "source": body.source,
            "version": EXPLORE_CACHE_VERSION,
        }
        report_cache_key = "explore:v{version}:{digest}".format(
            version=EXPLORE_CACHE_VERSION,
            digest=content_hash_bytes(
                json.dumps(
                    report_payload, sort_keys=True, separators=(",", ":"), default=str
                ).encode()
            ),
        )
        return ExploreCacheSpec(
            node_id=body.node_id,
            upstream_node_id=upstream_node_id,
            source=body.source,
            dataframe_cache_request=dataframe_cache_request,
            dataframe_cache_key=dataframe_key,
            report_cache_key=report_cache_key,
            family_key=("explore", graph.source_file or "", body.node_id, body.source),
        )

    def _run_job(
        self,
        job_id: str,
        body: ExploreRunRequest,
        spec: ExploreCacheSpec,
        token: JobCancellation,
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
            report = self._materialise_and_summarise(body, spec, job_id, execution_context)
            execution_context.checkpoint(label="explore_before_store", node_id=spec.node_id)
            report = report.model_copy(
                update={
                    "execution_metrics": ExecutionMetricsPayload.model_validate(
                        execution_context.metrics_payload(status="completed")
                    ),
                }
            )
            self._report_cache.put(spec.report_cache_key, report)
            self._lifecycle.transition(
                job_id,
                to="completed",
                message="Explore cache materialisation complete",
                fields={
                    "progress": 1.0,
                    "result": report,
                    "execution_metrics": report.execution_metrics,
                },
                elapsed_seconds=time.monotonic() - start_time,
            )
        except ExecutionCancelledError:
            reason = token.terminal_reason or "cancelled"
            self._lifecycle.transition(
                job_id,
                to=reason,
                message=f"Explore cache materialisation {reason}",
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
                message=str(exc),
                fields={"error": str(exc)},
                elapsed_seconds=time.monotonic() - start_time,
            )
        finally:
            if execution_context is not None:
                execution_context.release_admission()
            self._jobs.release(job_id)

    def _materialise_and_summarise(
        self,
        body: ExploreRunRequest,
        spec: ExploreCacheSpec,
        job_id: str,
        execution_context: ExecutionContext,
    ) -> ExploreCacheReport:
        from haute.executor import (
            ENFORCE_CONTRACTS,
            _build_node_fn,
            _compile_preamble,
            _pipeline_dir,
        )

        self._store.update_job(job_id, progress=0.1, message="Executing Explore pipeline")
        preamble_ns = _compile_preamble(
            body.graph.preamble or "",
            pipeline_dir=_pipeline_dir(body.graph),
        )
        lazy_outputs, *_ = execution_facade.execute_lazy_graph(
            body.graph,
            _build_node_fn,
            target_node_id=spec.node_id,
            preamble_ns=preamble_ns or None,
            source=body.source,
            enforce_contracts=ENFORCE_CONTRACTS,
            execution_context=execution_context,
            dataframe_cache_request=spec.dataframe_cache_request,
        )
        explore_lf = lazy_outputs.get(spec.node_id)
        if explore_lf is None:
            raise ValueError(f"No data arrived at Explore node '{spec.node_id}'.")

        self._store.update_job(job_id, progress=0.85, message="Reading cached schema")
        schema = explore_lf.collect_schema()

        with execution_context.stage("explore_frame_stats"):
            frame_stats = _build_frame_stats(
                explore_lf,
                schema,
                execution_context=execution_context,
            )

        return ExploreCacheReport(
            node_id=spec.node_id,
            upstream_node_id=spec.upstream_node_id,
            source=body.source,
            dataframe_cache_key=spec.dataframe_cache_key,
            row_count=frame_stats.row_count,
            column_count=len(schema.names()),
            columns=frame_stats.columns,
            generated_at=time.time(),
        )
