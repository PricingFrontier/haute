"""Shared execution context for long-running graph work."""

from __future__ import annotations

import contextlib
import contextvars
import ctypes
import math
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, cast

from haute._logging import get_logger

EXECUTION_METRICS_SCHEMA_VERSION = 1
EXECUTION_TELEMETRY_SCHEMA_VERSION = 1
_DEFAULT_MAX_RETAINED_STAGES = 200
_DEFAULT_MAX_RETAINED_MEMORY_PRESSURE_EVENTS = 32
_MAX_RETAINED_COLUMN_WIDTHS = 128
_MAX_STREAMABILITY_EVIDENCE = 32
_MAX_TELEMETRY_ATTRIBUTES = 48
_MAX_TELEMETRY_STRING_LENGTH = 128
_MEMORY_PRESSURE_THRESHOLDS: tuple[float, ...] = (0.50, 0.75, 0.90)
_RSS_SAMPLE_UNSET = object()
_TERMINAL_NON_STATES = frozenset({"pending", "queued", "running"})
_CURRENT_EXECUTION_CONTEXT: contextvars.ContextVar[ExecutionContext | None] = (
    contextvars.ContextVar("haute_current_execution_context", default=None)
)
logger = get_logger(component="execution_context")


class ExecutionProfile(StrEnum):
    """Named execution profiles shared by execution, routes, and metrics."""

    PREVIEW_EAGER = "preview_eager"
    LAZY_SINK = "lazy_sink"
    TRAINING_PREP = "training_prep"
    OPTIMISER_SETUP = "optimiser_setup"
    EXPLORE_ANALYSIS = "explore_analysis"
    AUTO_RANGE = "auto_range"
    DEPLOY_LIVE = "deploy_live"
    DEPLOY_BATCH = "deploy_batch"
    CHUNKED_MAP_REDUCE = "chunked_map_reduce"


class ExecutionCacheProofMissReason(StrEnum):
    """Closed, data-free reasons an execution cache proof could not be used."""

    METADATA_SOURCE_MISMATCH = "metadata_source_mismatch"
    ARTIFACT_INTEGRITY_SCHEMA_FAILURE = "artifact_integrity_schema_failure"
    UNREADABLE_ARTIFACT = "unreadable_artifact"
    PROOF_UNAVAILABLE = "proof_unavailable"


@dataclass(frozen=True, slots=True)
class ExecutionFaultPoint:
    """One deterministic, request-local execution fault boundary."""

    name: str
    operation: str
    node_id: str | None
    sequence: int


@dataclass(frozen=True, slots=True)
class ExecutionTelemetryEvent:
    """Bounded terminal telemetry event with no request identifiers."""

    schema_version: int
    event: str
    attributes: Mapping[str, str | int | float | bool | None]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event": self.event,
            "attributes": dict(self.attributes),
        }


@cache
def _execution_telemetry_enabled() -> bool:
    """Parse the process-wide telemetry toggle once."""
    raw = os.environ.get("HAUTE_EXECUTION_TELEMETRY")
    if raw is None:
        return False
    normalised = raw.strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError(
        "HAUTE_EXECUTION_TELEMETRY must be an explicit boolean (true/false, yes/no, on/off, or 1/0)"
    )


def configure_execution_telemetry() -> bool:
    """Validate and warm telemetry configuration after server environment loading."""
    _execution_telemetry_enabled.cache_clear()
    return _execution_telemetry_enabled()


def _bounded_telemetry_attributes(
    raw_attributes: Mapping[str, object],
) -> dict[str, str | int | float | bool | None]:
    """Normalise one complete bounded telemetry allow-list without truncation."""
    if len(raw_attributes) > _MAX_TELEMETRY_ATTRIBUTES:
        raise ValueError(
            f"Execution telemetry attribute allow-list exceeds {_MAX_TELEMETRY_ATTRIBUTES} entries"
        )
    attributes: dict[str, str | int | float | bool | None] = {}
    for name, value in raw_attributes.items():
        if value is None or isinstance(value, (bool, int)):
            attributes[name] = value
        elif isinstance(value, float):
            attributes[name] = value if math.isfinite(value) else None
        elif isinstance(value, str):
            attributes[name] = value[:_MAX_TELEMETRY_STRING_LENGTH]
        else:
            attributes[name] = None
    return attributes


class ExecutionCancelledError(RuntimeError):
    """Raised when an execution context has been cancelled."""

    def __init__(
        self,
        operation: str,
        *,
        job_id: str | None = None,
        cancellation_latency_ms: float | None = None,
    ) -> None:
        detail = f"Execution {operation!r} was cancelled"
        if job_id:
            detail = f"{detail} (job_id={job_id!r})"
        super().__init__(detail)
        self.operation = operation
        self.job_id = job_id
        self.cancellation_latency_ms = cancellation_latency_ms


class ExecutionMemoryLimitExceededError(MemoryError):
    """Raised when a sampled memory checkpoint exceeds the configured budget."""

    def __init__(
        self,
        operation: str,
        *,
        rss_bytes: int | None,
        limit_bytes: int,
        job_id: str | None = None,
        baseline_rss_bytes: int | None = None,
        rss_limit_bytes: int | None = None,
        reason: str = "rss_exceeds_memory_limit",
    ) -> None:
        if reason == "memory_sampler_unavailable":
            detail = (
                f"Execution {operation!r} could not enforce its memory budget "
                "because the RSS sampler became unavailable"
            )
        else:
            if rss_bytes is None:
                raise ValueError(
                    "rss_bytes may be None only when reason is 'memory_sampler_unavailable'"
                )
            if baseline_rss_bytes is None:
                detail = (
                    f"Execution {operation!r} exceeded its memory budget: "
                    f"{rss_bytes} bytes used > {limit_bytes} bytes allowed"
                )
            elif reason == "process_rss_limit_exceeded":
                detail = (
                    f"Execution {operation!r} exceeded its process RSS cap: "
                    f"{rss_bytes} bytes used > {rss_limit_bytes} bytes allowed"
                )
            else:
                rss_growth_bytes = rss_bytes - baseline_rss_bytes
                detail = (
                    f"Execution {operation!r} exceeded its memory growth budget: "
                    f"{rss_growth_bytes} bytes over admission baseline > "
                    f"{limit_bytes} bytes allowed"
                )
        if job_id:
            detail = f"{detail} (job_id={job_id!r})"
        super().__init__(detail)
        self.operation = operation
        self.job_id = job_id
        self.rss_bytes = rss_bytes
        self.limit_bytes = limit_bytes
        self.baseline_rss_bytes = baseline_rss_bytes
        self.rss_limit_bytes = rss_limit_bytes
        self.reason = reason

    def to_payload(self) -> dict[str, object]:
        return {
            "error_code": "memory_limit",
            "operation": self.operation,
            "job_id": self.job_id,
            "memory_limit_bytes": self.limit_bytes,
            "rss_bytes": self.rss_bytes,
            "baseline_rss_bytes": self.baseline_rss_bytes,
            "rss_limit_bytes": self.rss_limit_bytes,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ExecutionAdmission:
    """Decision metadata captured before a bounded execution starts."""

    operation: str
    profile: ExecutionProfile
    memory_limit_bytes: int
    rss_at_admission_bytes: int | None
    rss_limit_bytes: int | None
    headroom_bytes: int | None
    config_key: str
    process_rss_limit_bytes: int | None = None
    budget_policy: str = "fixed_default"
    available_ram_bytes: int | None = None
    os_reserve_bytes: int | None = None
    admitted: bool = True
    reason: str = "within_memory_budget"

    def to_dict(self) -> dict[str, object]:
        return {
            "admitted": self.admitted,
            "operation": self.operation,
            "profile": self.profile.value,
            "memory_limit_bytes": self.memory_limit_bytes,
            "rss_at_admission_bytes": self.rss_at_admission_bytes,
            "rss_limit_bytes": self.rss_limit_bytes,
            "process_rss_limit_bytes": self.process_rss_limit_bytes,
            "headroom_bytes": self.headroom_bytes,
            "config_key": self.config_key,
            "budget_policy": self.budget_policy,
            "available_ram_bytes": self.available_ram_bytes,
            "os_reserve_bytes": self.os_reserve_bytes,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ExecutionStageMetric:
    """One timed execution stage with RSS samples at the stage boundary."""

    name: str
    elapsed_ms: float
    operation: str
    profile: ExecutionProfile
    node_id: str | None = None
    job_id: str | None = None
    rss_start_bytes: int | None = None
    rss_end_bytes: int | None = None
    rss_peak_bytes: int | None = None
    rows_in: int | None = None
    rows_out: int | None = None
    bytes_read: int | None = None
    bytes_written: int | None = None
    columns_scanned: int | None = None
    n_collects: int = 0
    n_checkpoints: int = 0
    schema_version: int = EXECUTION_METRICS_SCHEMA_VERSION

    @property
    def rss_delta_bytes(self) -> int | None:
        if self.rss_start_bytes is None or self.rss_end_bytes is None:
            return None
        return self.rss_end_bytes - self.rss_start_bytes

    def to_summary(self) -> ExecutionStageSummary:
        return ExecutionStageSummary(
            name=self.name,
            operation=self.operation,
            profile=self.profile,
            elapsed_ms=self.elapsed_ms,
            node_id=self.node_id,
            job_id=self.job_id,
            rss_start_bytes=self.rss_start_bytes,
            rss_end_bytes=self.rss_end_bytes,
            rss_delta_bytes=self.rss_delta_bytes,
            rss_peak_bytes=self.rss_peak_bytes,
            rows_in=self.rows_in,
            rows_out=self.rows_out,
            bytes_read=self.bytes_read,
            bytes_written=self.bytes_written,
            columns_scanned=self.columns_scanned,
            n_collects=self.n_collects,
            n_checkpoints=self.n_checkpoints,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True, slots=True)
class ExecutionStageSummary:
    """Serializable public shape for one retained execution stage."""

    name: str
    operation: str
    profile: ExecutionProfile
    elapsed_ms: float
    node_id: str | None
    job_id: str | None
    rss_start_bytes: int | None
    rss_end_bytes: int | None
    rss_delta_bytes: int | None
    rss_peak_bytes: int | None
    rows_in: int | None = None
    rows_out: int | None = None
    bytes_read: int | None = None
    bytes_written: int | None = None
    columns_scanned: int | None = None
    n_collects: int = 0
    n_checkpoints: int = 0
    schema_version: int = EXECUTION_METRICS_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "operation": self.operation,
            "profile": self.profile.value,
            "elapsed_ms": self.elapsed_ms,
            "node_id": self.node_id,
            "job_id": self.job_id,
            "rss_start_bytes": self.rss_start_bytes,
            "rss_end_bytes": self.rss_end_bytes,
            "rss_delta_bytes": self.rss_delta_bytes,
            "rss_peak_bytes": self.rss_peak_bytes,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
            "columns_scanned": self.columns_scanned,
            "n_collects": self.n_collects,
            "n_checkpoints": self.n_checkpoints,
        }


@dataclass(frozen=True, slots=True)
class ExecutionMemoryPressureEvent:
    """One bounded advisory memory-pressure event."""

    operation: str
    profile: ExecutionProfile
    threshold_ratio: float
    threshold_percent: int
    rss_bytes: int
    rss_limit_bytes: int
    headroom_bytes: int
    headroom_used_bytes: int
    rss_peak_bytes: int
    job_id: str | None = None
    node_id: str | None = None
    stage: str | None = None
    label: str | None = None
    memory_limit_bytes: int | None = None
    memory_baseline_bytes: int | None = None
    baseline_rss_bytes: int | None = None
    budget_policy: str | None = None
    config_key: str | None = None
    available_ram_bytes: int | None = None
    os_reserve_bytes: int | None = None
    pressure_ratio: float = 0.0
    event: str = "memory_pressure"
    schema_version: int = EXECUTION_METRICS_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event": self.event,
            "operation": self.operation,
            "profile": self.profile.value,
            "job_id": self.job_id,
            "node_id": self.node_id,
            "stage": self.stage,
            "label": self.label,
            "threshold_ratio": self.threshold_ratio,
            "threshold_percent": self.threshold_percent,
            "rss_bytes": self.rss_bytes,
            "rss_limit_bytes": self.rss_limit_bytes,
            "headroom_bytes": self.headroom_bytes,
            "headroom_used_bytes": self.headroom_used_bytes,
            "rss_peak_bytes": self.rss_peak_bytes,
            "memory_limit_bytes": self.memory_limit_bytes,
            "memory_baseline_bytes": self.memory_baseline_bytes,
            "baseline_rss_bytes": self.baseline_rss_bytes,
            "budget_policy": self.budget_policy,
            "config_key": self.config_key,
            "available_ram_bytes": self.available_ram_bytes,
            "os_reserve_bytes": self.os_reserve_bytes,
            "pressure_ratio": self.pressure_ratio,
        }


@dataclass(frozen=True, slots=True)
class ExecutionTraceSummary:
    """Bounded, serializable summary safe to keep in job status metadata."""

    operation: str
    profile: ExecutionProfile
    job_id: str | None
    stages: tuple[ExecutionStageSummary, ...]
    stage_count: int
    total_elapsed_ms: float
    node_elapsed_ms: dict[str, float]
    stage_elapsed_ms: dict[str, float]
    rss_start_bytes: int | None = None
    rss_end_bytes: int | None = None
    rss_delta_bytes: int | None = None
    rss_peak_bytes: int | None = None
    n_collects: int = 0
    n_checkpoints: int = 0
    memory_pressure_events: tuple[ExecutionMemoryPressureEvent, ...] = ()
    memory_pressure_event_count: int = 0
    status: str | None = None
    terminal_reason: str | None = None
    schema_version: int = EXECUTION_METRICS_SCHEMA_VERSION

    @property
    def retained_stage_count(self) -> int:
        return len(self.stages)

    @property
    def truncated_stage_count(self) -> int:
        return max(0, self.stage_count - self.retained_stage_count)

    @property
    def retained_memory_pressure_event_count(self) -> int:
        return len(self.memory_pressure_events)

    @property
    def truncated_memory_pressure_event_count(self) -> int:
        return max(
            0,
            self.memory_pressure_event_count - self.retained_memory_pressure_event_count,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "profile": self.profile.value,
            "job_id": self.job_id,
            "status": self.status,
            "terminal_reason": self.terminal_reason,
            "stage_count": self.stage_count,
            "retained_stage_count": self.retained_stage_count,
            "truncated_stage_count": self.truncated_stage_count,
            "stages_truncated": self.truncated_stage_count > 0,
            "total_elapsed_ms": self.total_elapsed_ms,
            "node_elapsed_ms": dict(self.node_elapsed_ms),
            "stage_elapsed_ms": dict(self.stage_elapsed_ms),
            "rss_start_bytes": self.rss_start_bytes,
            "rss_end_bytes": self.rss_end_bytes,
            "rss_delta_bytes": self.rss_delta_bytes,
            "rss_peak_bytes": self.rss_peak_bytes,
            "max_rss_bytes": self.rss_peak_bytes,
            "n_collects": self.n_collects,
            "n_checkpoints": self.n_checkpoints,
            "stages": [stage.to_dict() for stage in self.stages],
            "memory_pressure_event_count": self.memory_pressure_event_count,
            "retained_memory_pressure_event_count": (self.retained_memory_pressure_event_count),
            "truncated_memory_pressure_event_count": (self.truncated_memory_pressure_event_count),
            "memory_pressure_events_truncated": (self.truncated_memory_pressure_event_count > 0),
            "memory_pressure_events": [event.to_dict() for event in self.memory_pressure_events],
        }


@dataclass(slots=True)
class _ActiveStage:
    """Mutable RSS peak tracker for stages active on the current thread."""

    name: str
    node_id: str | None
    rss_peak_bytes: int | None = None
    n_collects: int = 0
    n_checkpoints: int = 0


@dataclass(frozen=True, slots=True)
class ExecutionColumnWidths:
    """Observed or planned column widths for one execution node."""

    node_id: str
    input_width: int | None = None
    output_width: int | None = None
    requested_width: int | None = None
    physically_scanned_width: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "input_width": self.input_width,
            "output_width": self.output_width,
            "requested_width": self.requested_width,
            "physically_scanned_width": self.physically_scanned_width,
        }


class ExecutionMetricsRecorder:
    """Thread-safe in-memory recorder for execution timing and memory samples."""

    def __init__(
        self,
        *,
        max_stages: int = _DEFAULT_MAX_RETAINED_STAGES,
        max_memory_pressure_events: int = _DEFAULT_MAX_RETAINED_MEMORY_PRESSURE_EVENTS,
    ) -> None:
        if max_stages < 0:
            raise ValueError("max_stages must be >= 0")
        if max_memory_pressure_events < 0:
            raise ValueError("max_memory_pressure_events must be >= 0")
        self._max_stages = max_stages
        self._max_memory_pressure_events = max_memory_pressure_events
        self._lock = threading.RLock()
        self._stages: list[ExecutionStageMetric] = []
        self._memory_pressure_events: list[ExecutionMemoryPressureEvent] = []
        self._stage_count = 0
        self._memory_pressure_event_count = 0
        self._total_elapsed_ms = 0.0
        self._node_elapsed_ms: dict[str, float] = {}
        self._stage_elapsed_ms: dict[str, float] = {}
        self._rss_start_bytes: int | None = None
        self._rss_end_bytes: int | None = None
        self._rss_peak_bytes: int | None = None
        self._n_collects = 0
        self._n_checkpoints = 0

    def record(self, metric: ExecutionStageMetric) -> None:
        self._validate_metric(metric)
        with self._lock:
            self._stage_count += 1
            self._total_elapsed_ms = _round_ms(self._total_elapsed_ms + metric.elapsed_ms)
            self._stage_elapsed_ms[metric.name] = _round_ms(
                self._stage_elapsed_ms.get(metric.name, 0.0) + metric.elapsed_ms
            )
            if metric.node_id is not None:
                self._node_elapsed_ms[metric.node_id] = _round_ms(
                    self._node_elapsed_ms.get(metric.node_id, 0.0) + metric.elapsed_ms
                )
            if self._rss_start_bytes is None and metric.rss_start_bytes is not None:
                self._rss_start_bytes = metric.rss_start_bytes
            if metric.rss_end_bytes is not None:
                self._rss_end_bytes = metric.rss_end_bytes
            if metric.rss_peak_bytes is not None:
                self._rss_peak_bytes = (
                    metric.rss_peak_bytes
                    if self._rss_peak_bytes is None
                    else max(self._rss_peak_bytes, metric.rss_peak_bytes)
                )
            if len(self._stages) < self._max_stages:
                self._stages.append(metric)

    def record_memory_pressure_event(self, event: ExecutionMemoryPressureEvent) -> None:
        """Record a bounded advisory memory-pressure event."""
        self._validate_memory_pressure_event(event)
        with self._lock:
            self._memory_pressure_event_count += 1
            if len(self._memory_pressure_events) < self._max_memory_pressure_events:
                self._memory_pressure_events.append(event)

    def record_collect(self) -> None:
        """Record one materialisation attempt through a shared collect helper."""
        with self._lock:
            self._n_collects += 1

    def record_checkpoint(self) -> None:
        """Record one cooperative cancellation/memory checkpoint."""
        with self._lock:
            self._n_checkpoints += 1

    @staticmethod
    def _validate_metric(metric: ExecutionStageMetric) -> None:
        for field_name in (
            "rss_start_bytes",
            "rss_end_bytes",
            "rss_peak_bytes",
            "rows_in",
            "rows_out",
            "bytes_read",
            "bytes_written",
            "columns_scanned",
            "n_collects",
            "n_checkpoints",
        ):
            value = getattr(metric, field_name)
            if value is not None and not isinstance(value, int):
                raise TypeError(f"{field_name} must be an int or None")

    @staticmethod
    def _validate_memory_pressure_event(event: ExecutionMemoryPressureEvent) -> None:
        for field_name in (
            "rss_bytes",
            "rss_limit_bytes",
            "headroom_bytes",
            "headroom_used_bytes",
            "rss_peak_bytes",
            "memory_limit_bytes",
            "memory_baseline_bytes",
            "baseline_rss_bytes",
            "available_ram_bytes",
            "os_reserve_bytes",
            "threshold_percent",
        ):
            value = getattr(event, field_name)
            if value is not None and not isinstance(value, int):
                raise TypeError(f"{field_name} must be an int or None")

    def snapshot(self) -> tuple[ExecutionStageMetric, ...]:
        with self._lock:
            return tuple(self._stages)

    def memory_pressure_snapshot(self) -> tuple[ExecutionMemoryPressureEvent, ...]:
        with self._lock:
            return tuple(self._memory_pressure_events)

    def by_node_elapsed_ms(self) -> dict[str, float]:
        with self._lock:
            return dict(self._node_elapsed_ms)

    def summary(
        self,
        *,
        operation: str,
        profile: ExecutionProfile,
        job_id: str | None = None,
        status: str | None = None,
        terminal_reason: str | None = None,
        max_stages: int | None = None,
    ) -> ExecutionTraceSummary:
        """Return a bounded summary of retained stages plus full rollups."""
        if max_stages is not None and max_stages < 0:
            raise ValueError("max_stages must be >= 0")
        with self._lock:
            stage_limit = self._max_stages if max_stages is None else max_stages
            retained = tuple(metric.to_summary() for metric in self._stages[:stage_limit])
            rss_delta_bytes = (
                None
                if self._rss_start_bytes is None or self._rss_end_bytes is None
                else self._rss_end_bytes - self._rss_start_bytes
            )
            return ExecutionTraceSummary(
                operation=operation,
                profile=profile,
                job_id=job_id,
                status=status,
                terminal_reason=terminal_reason,
                stages=retained,
                stage_count=self._stage_count,
                total_elapsed_ms=self._total_elapsed_ms,
                node_elapsed_ms=dict(self._node_elapsed_ms),
                stage_elapsed_ms=dict(self._stage_elapsed_ms),
                rss_start_bytes=self._rss_start_bytes,
                rss_end_bytes=self._rss_end_bytes,
                rss_delta_bytes=rss_delta_bytes,
                rss_peak_bytes=self._rss_peak_bytes,
                n_collects=self._n_collects,
                n_checkpoints=self._n_checkpoints,
                memory_pressure_events=tuple(self._memory_pressure_events),
                memory_pressure_event_count=self._memory_pressure_event_count,
            )


@dataclass(slots=True)
class ExecutionCancellationToken:
    """Cooperative cancellation token shared by route jobs and executors."""

    monotonic_clock: Callable[[], float] = time.monotonic
    _event: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _cancelled_at: float | None = field(default=None, init=False)

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled_at is None:
                self._cancelled_at = self.monotonic_clock()
            self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self, operation: str, *, job_id: str | None = None) -> None:
        if self.cancelled:
            with self._lock:
                cancelled_at = self._cancelled_at
            latency_ms = (
                None
                if cancelled_at is None
                else max(0.0, (self.monotonic_clock() - cancelled_at) * 1_000)
            )
            raise ExecutionCancelledError(
                operation,
                job_id=job_id,
                cancellation_latency_ms=latency_ms,
            )


def current_rss_bytes() -> int | None:
    """Return current process resident memory in bytes where the OS exposes it."""
    linux_value = _linux_current_rss_bytes()
    if linux_value is not None:
        return linux_value
    windows_value = _windows_current_rss_bytes()
    if windows_value is not None:
        return windows_value
    return _resource_current_rss_bytes()


def _linux_current_rss_bytes() -> int | None:
    status_path = "/proc/self/status"
    if not Path(status_path).exists():
        return None
    try:
        with open(status_path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except OSError:
        return None
    return None


class _WindowsProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


@dataclass(frozen=True, slots=True)
class _WindowsRssBindings:
    get_current_process: Callable[[], Any]
    get_process_memory_info: Callable[[Any, Any, int], int]


_WINDOWS_RSS_BINDINGS_LOCK = threading.RLock()
_WINDOWS_RSS_BINDINGS_BY_FACTORY: dict[int, tuple[object, _WindowsRssBindings | None]] = {}


def _reset_windows_rss_sampler_for_tests() -> None:
    """Clear cached Windows RSS API bindings for isolated tests."""
    with _WINDOWS_RSS_BINDINGS_LOCK:
        _WINDOWS_RSS_BINDINGS_BY_FACTORY.clear()


def _windows_rss_bindings(
    windll_factory: Callable[..., Any],
) -> _WindowsRssBindings | None:
    """Return bindings initialised once for this exact WinDLL factory."""
    factory_id = id(windll_factory)
    with _WINDOWS_RSS_BINDINGS_LOCK:
        cached = _WINDOWS_RSS_BINDINGS_BY_FACTORY.get(factory_id)
        if cached is not None and cached[0] is windll_factory:
            return cached[1]
        try:
            kernel32 = windll_factory("kernel32.dll", use_last_error=True)
            psapi = windll_factory("psapi.dll", use_last_error=True)
            get_current_process = kernel32.GetCurrentProcess
            get_process_memory_info = psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_WindowsProcessMemoryCountersEx),
                ctypes.c_ulong,
            ]
            get_process_memory_info.restype = ctypes.c_int
        except (AttributeError, OSError):
            bindings = None
        else:
            bindings = _WindowsRssBindings(
                get_current_process=get_current_process,
                get_process_memory_info=get_process_memory_info,
            )
        _WINDOWS_RSS_BINDINGS_BY_FACTORY[factory_id] = (windll_factory, bindings)
        return bindings


def _windows_current_rss_bytes() -> int | None:
    if os.name != "nt":
        return None
    windll_factory = getattr(ctypes, "WinDLL", None)
    if windll_factory is None:
        return None
    bindings = _windows_rss_bindings(windll_factory)
    if bindings is None:
        return None
    counters = _WindowsProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    try:
        handle = bindings.get_current_process()
        ok = bindings.get_process_memory_info(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
    except (AttributeError, OSError):
        return None
    return int(counters.WorkingSetSize) if ok else None


def _resource_current_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    getrusage = getattr(resource, "getrusage", None)
    rus_self = getattr(resource, "RUSAGE_SELF", None)
    if getrusage is None or rus_self is None:
        return None
    try:
        rss = int(getrusage(rus_self).ru_maxrss)
    except (OSError, ValueError):
        return None
    if rss <= 0:
        return None
    # Linux reports KiB; macOS reports bytes. ``/proc`` covers Linux first.
    return rss if rss > 10_000_000 else rss * 1024


@dataclass(slots=True, weakref_slot=True)
class ExecutionContext:
    """Shared per-run execution controls and instrumentation."""

    operation: str
    profile: ExecutionProfile
    job_id: str | None = None
    cancellation_token: ExecutionCancellationToken = field(
        default_factory=ExecutionCancellationToken
    )
    memory_limit_bytes: int | None = None
    memory_baseline_bytes: int | None = None
    rss_limit_bytes: int | None = None
    admission: ExecutionAdmission | None = None
    projection_plan: Any | None = None
    metrics: ExecutionMetricsRecorder = field(default_factory=ExecutionMetricsRecorder)
    memory_sampler: Callable[[], int | None] = current_rss_bytes
    memory_pressure_callback: Callable[[ExecutionMemoryPressureEvent], None] | None = None
    admission_release: Callable[[], None] | None = None
    fault_injector: Callable[[ExecutionFaultPoint], None] | None = None
    telemetry_enabled: bool = field(default_factory=_execution_telemetry_enabled)
    telemetry_sink: Callable[[ExecutionTelemetryEvent], None] | None = None
    _stage_local: threading.local = field(default_factory=threading.local, init=False)
    _memory_pressure_seen: set[int] = field(default_factory=set, init=False)
    _memory_pressure_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
    )
    _admission_release_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
    )
    _admission_released: bool = field(default=False, init=False)
    _cleanup_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
    )
    _cleanup_callbacks: list[Callable[[], None]] = field(default_factory=list, init=False)
    _cleanups_released: bool = field(default=False, init=False)
    _fault_lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _fault_sequence: int = field(default=0, init=False)
    _telemetry_lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _telemetry_emitted: set[tuple[str, str | None]] = field(
        default_factory=set,
        init=False,
    )
    _evidence_lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _column_widths: dict[str, ExecutionColumnWidths] = field(
        default_factory=dict,
        init=False,
    )
    _bytes_read: int | None = field(default=None, init=False)
    _bytes_written: int | None = field(default=None, init=False)
    _chunk_count: int = field(default=0, init=False)
    _observed_peak_rss_bytes: int | None = field(default=None, init=False)
    _cancellation_latency_ms: float | None = field(default=None, init=False)
    _estimate_calibration_recorded: bool = field(default=False, init=False)
    _cache_proof_hits: int = field(default=0, init=False)
    _cache_proof_misses: int = field(default=0, init=False)
    _cache_direct_fallbacks: int = field(default=0, init=False)
    _cache_proof_miss_reason_counts: dict[ExecutionCacheProofMissReason, int] = field(
        default_factory=lambda: {reason: 0 for reason in ExecutionCacheProofMissReason},
        init=False,
    )

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    def add_cleanup(self, callback: Callable[[], None]) -> None:
        """Retain a runtime resource until this execution context is released."""
        with self._cleanup_lock:
            if self._cleanups_released:
                raise RuntimeError("cannot register a resource on a released execution context")
            self._cleanup_callbacks.append(callback)

    def _release_cleanups(self) -> list[BaseException]:
        with self._cleanup_lock:
            if self._cleanups_released:
                return []
            self._cleanups_released = True
            callbacks = list(reversed(self._cleanup_callbacks))
            self._cleanup_callbacks.clear()
        errors: list[BaseException] = []
        for callback in callbacks:
            try:
                callback()
            except BaseException as exc:
                errors.append(exc)
        return errors

    @staticmethod
    def _cleanup_failure_note(error: BaseException) -> str:
        return f"Execution cleanup failed: {type(error).__name__}: {error}"

    def release_admission(self, *, preserve_primary_error: bool = False) -> None:
        """Release resources, optionally preserving an exception propagating through ``finally``."""
        primary_error = sys.exception() if preserve_primary_error else None
        release: Callable[[], None] | None = None
        errors = self._release_cleanups()
        with self._admission_release_lock:
            if not self._admission_released:
                self._admission_released = True
                release = self.admission_release
                self.admission_release = None
        if release is not None:
            try:
                release()
            except BaseException as exc:
                errors.append(exc)
        if not errors:
            return
        if primary_error is not None:
            for error in errors:
                primary_error.add_note(self._cleanup_failure_note(error))
            return
        first_error, *later_errors = errors
        for error in later_errors:
            first_error.add_note(self._cleanup_failure_note(error))
        raise first_error

    def fault_point(self, name: str, *, node_id: str | None = None) -> None:
        """Invoke the request-local deterministic fault seam when configured."""
        injector = self.fault_injector
        if injector is None:
            return
        if not isinstance(name, str) or not name:
            raise ValueError("execution fault-point name must be a non-empty string")
        with self._fault_lock:
            self._fault_sequence += 1
            point = ExecutionFaultPoint(
                name=name,
                operation=self.operation,
                node_id=node_id,
                sequence=self._fault_sequence,
            )
        injector(point)

    def _throw_if_cancelled(self) -> None:
        try:
            self.cancellation_token.throw_if_cancelled(
                self.operation,
                job_id=self.job_id,
            )
        except ExecutionCancelledError as exc:
            if exc.cancellation_latency_ms is not None:
                with self._evidence_lock:
                    if self._cancellation_latency_ms is None:
                        self._cancellation_latency_ms = exc.cancellation_latency_ms
            raise

    def checkpoint(self, *, label: str, node_id: str | None = None) -> None:
        self._throw_if_cancelled()
        self.fault_point(label, node_id=node_id)
        self._record_checkpoint()
        rss_bytes = (
            self.memory_sampler()
            if self._effective_rss_limit_bytes() is not None or self._active_stage_stack()
            else None
        )
        if rss_bytes is not None:
            self._observe_rss(rss_bytes, label=label, node_id=node_id)
        self._check_memory_budget(rss_bytes=rss_bytes)

    @contextlib.contextmanager
    def stage(
        self,
        name: str,
        *,
        node_id: str | None = None,
        skip_metric_on_exception: tuple[type[BaseException], ...] = (),
    ) -> Iterator[None]:
        t0 = time.perf_counter()
        self._throw_if_cancelled()
        rss_start = self.memory_sampler()
        self._observe_rss(rss_start, stage=name, node_id=node_id)
        try:
            self._check_memory_budget(rss_bytes=rss_start)
        except ExecutionMemoryLimitExceededError:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
            self.metrics.record(
                ExecutionStageMetric(
                    name=name,
                    elapsed_ms=elapsed_ms,
                    operation=self.operation,
                    profile=self.profile,
                    node_id=node_id,
                    job_id=self.job_id,
                    rss_start_bytes=rss_start,
                    rss_end_bytes=rss_start,
                    rss_peak_bytes=rss_start,
                )
            )
            raise
        active_stage = _ActiveStage(name=name, node_id=node_id, rss_peak_bytes=rss_start)
        self._active_stage_stack().append(active_stage)
        current_context_token = _CURRENT_EXECUTION_CONTEXT.set(self)
        primary_error: BaseException | None = None
        skip_metric = False
        try:
            yield
        except BaseException as exc:
            primary_error = exc
            skip_metric = bool(
                skip_metric_on_exception and isinstance(exc, skip_metric_on_exception)
            )
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
            rss_end = active_stage.rss_peak_bytes
            finalization_errors: list[BaseException] = []
            try:
                rss_end = self.memory_sampler()
                self._observe_rss(rss_end, stage=name, node_id=node_id)
            except BaseException as exc:
                finalization_errors.append(exc)
            try:
                active_stack = self._active_stage_stack()
                if not active_stack or active_stack[-1] is not active_stage:
                    raise RuntimeError("execution stage stack is unbalanced")
                active_stack.pop()
            except BaseException as exc:
                finalization_errors.append(exc)
            try:
                _CURRENT_EXECUTION_CONTEXT.reset(current_context_token)
            except BaseException as exc:
                finalization_errors.append(exc)
            if not skip_metric:
                try:
                    self.metrics.record(
                        ExecutionStageMetric(
                            name=name,
                            elapsed_ms=elapsed_ms,
                            operation=self.operation,
                            profile=self.profile,
                            node_id=node_id,
                            job_id=self.job_id,
                            rss_start_bytes=rss_start,
                            rss_end_bytes=rss_end,
                            rss_peak_bytes=active_stage.rss_peak_bytes,
                            n_collects=active_stage.n_collects,
                            n_checkpoints=active_stage.n_checkpoints,
                        )
                    )
                except BaseException as exc:
                    finalization_errors.append(exc)
            if primary_error is None and not finalization_errors:
                self._check_memory_budget(rss_bytes=rss_end)
            if finalization_errors:
                if primary_error is not None:
                    primary_error.add_note(
                        "\n".join(
                            map(
                                lambda error: (
                                    "Execution stage finalization failed: "
                                    f"{type(error).__name__}: {error}"
                                ),
                                finalization_errors,
                            )
                        )
                    )
                else:
                    first_error, *later_errors = finalization_errors
                    for error in later_errors:
                        first_error.add_note(
                            f"Execution stage finalization failed: {type(error).__name__}: {error}"
                        )
                    raise first_error

    def record_collect(self) -> None:
        """Record a Polars materialisation against active execution stages."""
        self.metrics.record_collect()
        for stage in self._active_stage_stack():
            stage.n_collects += 1

    def record_column_widths(
        self,
        *,
        node_id: str,
        input_width: int | None = None,
        output_width: int | None = None,
        requested_width: int | None = None,
        physically_scanned_width: int | None = None,
    ) -> None:
        """Merge width evidence without collecting a frame solely for metrics."""
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("column-width evidence requires a non-empty node_id")
        values = {
            "input_width": input_width,
            "output_width": output_width,
            "requested_width": requested_width,
            "physically_scanned_width": physically_scanned_width,
        }
        for name, value in values.items():
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        with self._evidence_lock:
            previous = self._column_widths.get(node_id)
            self._column_widths[node_id] = ExecutionColumnWidths(
                node_id=node_id,
                input_width=(
                    input_width
                    if input_width is not None
                    else previous.input_width
                    if previous is not None
                    else None
                ),
                output_width=(
                    output_width
                    if output_width is not None
                    else previous.output_width
                    if previous is not None
                    else None
                ),
                requested_width=(
                    requested_width
                    if requested_width is not None
                    else previous.requested_width
                    if previous is not None
                    else None
                ),
                physically_scanned_width=(
                    physically_scanned_width
                    if physically_scanned_width is not None
                    else previous.physically_scanned_width
                    if previous is not None
                    else None
                ),
            )

    def record_bytes_read(self, byte_count: int) -> None:
        self._record_supported_bytes("read", byte_count)

    def record_bytes_written(self, byte_count: int) -> None:
        self._record_supported_bytes("written", byte_count)

    def _record_supported_bytes(self, direction: str, byte_count: int) -> None:
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise ValueError("supported byte counters must be non-negative integers")
        with self._evidence_lock:
            if direction == "read":
                self._bytes_read = (self._bytes_read or 0) + byte_count
            elif direction == "written":
                self._bytes_written = (self._bytes_written or 0) + byte_count
            else:
                raise ValueError(f"unknown byte-counter direction: {direction!r}")

    def record_chunk(self) -> None:
        with self._evidence_lock:
            self._chunk_count += 1

    def record_cache_proof_hit(self) -> None:
        with self._evidence_lock:
            self._cache_proof_hits += 1

    def record_cache_proof_miss(self, reason: ExecutionCacheProofMissReason) -> None:
        if not isinstance(reason, ExecutionCacheProofMissReason):
            raise TypeError("cache proof miss reason must be an ExecutionCacheProofMissReason")
        with self._evidence_lock:
            self._cache_proof_misses += 1
            self._cache_proof_miss_reason_counts[reason] += 1

    def record_cache_direct_fallback(self) -> None:
        with self._evidence_lock:
            self._cache_direct_fallbacks += 1

    def metrics_summary(
        self,
        *,
        status: str | None = None,
        terminal_reason: str | None = None,
        max_stages: int | None = None,
    ) -> ExecutionTraceSummary:
        return self.metrics.summary(
            operation=self.operation,
            profile=self.profile,
            job_id=self.job_id,
            status=status,
            terminal_reason=terminal_reason,
            max_stages=max_stages,
        )

    def metrics_payload(
        self,
        *,
        status: str | None = None,
        terminal_reason: str | None = None,
        max_stages: int | None = None,
    ) -> dict[str, object]:
        payload = self.metrics_summary(
            status=status,
            terminal_reason=terminal_reason,
            max_stages=max_stages,
        ).to_dict()
        payload["memory_limit_bytes"] = self.memory_limit_bytes
        payload["memory_baseline_bytes"] = self.memory_baseline_bytes
        payload["rss_limit_bytes"] = self._effective_rss_limit_bytes()
        payload["admission"] = self.admission.to_dict() if self.admission is not None else None
        projection_plan = self.projection_plan
        diagnostic = getattr(projection_plan, "diagnostic", None)
        payload["execution_strategy"] = (
            diagnostic.to_dict()
            if diagnostic is not None and hasattr(diagnostic, "to_dict")
            else None
        )
        payload.update(self._execution_evidence_payload(payload, diagnostic=diagnostic))
        self._record_estimate_calibration(payload, diagnostic=diagnostic)
        self._emit_terminal_telemetry(payload)
        return payload

    def _record_estimate_calibration(
        self,
        payload: Mapping[str, object],
        *,
        diagnostic: Any | None,
    ) -> None:
        """Consume one terminal, positive estimate/observation pair at most once."""

        status = payload.get("status")
        if (
            not isinstance(status, str)
            or not status
            or status.lower() in _TERMINAL_NON_STATES
            or getattr(getattr(diagnostic, "strategy", None), "value", None)
            != "materialisation-boundary"
        ):
            return
        raw_estimate = payload.get("raw_estimated_bytes")
        observed_growth = payload.get("observed_peak_rss_growth_bytes")
        if (
            not isinstance(raw_estimate, int)
            or isinstance(raw_estimate, bool)
            or raw_estimate <= 0
            or not isinstance(observed_growth, int)
            or isinstance(observed_growth, bool)
            or observed_growth <= 0
        ):
            return
        with self._evidence_lock:
            if self._estimate_calibration_recorded:
                return
            self._estimate_calibration_recorded = True

        from haute._estimate_calibration import observe_materialisation_estimate

        observe_materialisation_estimate(
            self.profile,
            estimated_bytes=raw_estimate,
            observed_growth_bytes=observed_growth,
        )

    def _emit_terminal_telemetry(self, payload: Mapping[str, object]) -> None:
        if not self.telemetry_enabled:
            return
        status = payload.get("status")
        if not isinstance(status, str) or not status or status.lower() in _TERMINAL_NON_STATES:
            return
        raw_reason = payload.get("terminal_reason")
        terminal_reason = raw_reason if isinstance(raw_reason, str) else None
        emission_key = (status, terminal_reason)
        with self._telemetry_lock:
            if emission_key in self._telemetry_emitted:
                return
            self._telemetry_emitted.add(emission_key)

        try:
            attributes = self._telemetry_attributes(payload)
            event = ExecutionTelemetryEvent(
                schema_version=EXECUTION_TELEMETRY_SCHEMA_VERSION,
                event="execution_terminal",
                attributes=attributes,
            )
            sink = self.telemetry_sink
            if sink is None:
                logger.info(
                    event.event,
                    schema_version=event.schema_version,
                    **dict(event.attributes),
                )
            else:
                sink(event)
        except Exception as exc:
            logger.warning(
                "execution_telemetry_sink_failed",
                error_type=type(exc).__name__,
            )

    @staticmethod
    def _telemetry_attributes(
        payload: Mapping[str, object],
    ) -> dict[str, str | int | float | bool | None]:
        strategy = payload.get("execution_strategy")
        strategy_payload = strategy if isinstance(strategy, Mapping) else {}
        admission = payload.get("admission")
        admission_payload = admission if isinstance(admission, Mapping) else {}
        cache_proof = payload.get("cache_proof")
        cache_proof_payload = cache_proof if isinstance(cache_proof, Mapping) else {}
        miss_reasons = cache_proof_payload.get("miss_reason_counts")
        miss_reason_payload = miss_reasons if isinstance(miss_reasons, Mapping) else {}
        widths = payload.get("column_widths")
        widths_payload = widths if isinstance(widths, Mapping) else {}
        raw_attributes: dict[str, object] = {
            "profile": payload.get("profile"),
            "status": payload.get("status"),
            "total_elapsed_ms": payload.get("total_elapsed_ms"),
            "rss_start_bytes": payload.get("rss_start_bytes"),
            "rss_end_bytes": payload.get("rss_end_bytes"),
            "rss_delta_bytes": payload.get("rss_delta_bytes"),
            "rss_peak_bytes": payload.get("rss_peak_bytes"),
            "n_collects": payload.get("n_collects"),
            "n_checkpoints": payload.get("n_checkpoints"),
            "checkpoint_count": payload.get("checkpoint_count"),
            "chunk_count": payload.get("chunk_count"),
            "bytes_read": payload.get("bytes_read"),
            "bytes_written": payload.get("bytes_written"),
            "estimated_bytes": payload.get("estimated_bytes"),
            "raw_estimated_bytes": payload.get("raw_estimated_bytes"),
            "observed_peak_rss_bytes": payload.get("observed_peak_rss_bytes"),
            "observed_peak_rss_growth_bytes": payload.get("observed_peak_rss_growth_bytes"),
            "estimate_calibration_factor_basis_points": payload.get(
                "estimate_calibration_factor_basis_points"
            ),
            "estimate_admission_basis": payload.get("estimate_admission_basis"),
            "cancellation_latency_ms": payload.get("cancellation_latency_ms"),
            "stage_count": payload.get("stage_count"),
            "stages_truncated": payload.get("stages_truncated"),
            "memory_pressure_event_count": payload.get("memory_pressure_event_count"),
            "memory_pressure_events_truncated": payload.get("memory_pressure_events_truncated"),
            "streamability": payload.get("streamability"),
            "strategy_status": strategy_payload.get("status"),
            "strategy": strategy_payload.get("strategy"),
            "boundedness": strategy_payload.get("boundedness"),
            "strategy_reason_code": strategy_payload.get("reason_code"),
            "admission_admitted": admission_payload.get("admitted"),
            "admission_budget_policy": admission_payload.get("budget_policy"),
            "admission_headroom_bytes": admission_payload.get("headroom_bytes"),
            "memory_limit_bytes": payload.get("memory_limit_bytes"),
            "rss_limit_bytes": payload.get("rss_limit_bytes"),
            "column_widths_state": widths_payload.get("state"),
            "requested_column_width_total": payload.get("requested_column_width_total"),
            "physically_scanned_column_width_total": payload.get(
                "physically_scanned_column_width_total"
            ),
            "cache_proof_hits": cache_proof_payload.get("hits"),
            "cache_proof_misses": cache_proof_payload.get("misses"),
            "cache_direct_fallbacks": cache_proof_payload.get("direct_fallbacks"),
            "cache_miss_metadata_source_mismatch": miss_reason_payload.get(
                "metadata_source_mismatch"
            ),
            "cache_miss_artifact_integrity_schema_failure": miss_reason_payload.get(
                "artifact_integrity_schema_failure"
            ),
            "cache_miss_unreadable_artifact": miss_reason_payload.get("unreadable_artifact"),
            "cache_miss_proof_unavailable": miss_reason_payload.get("proof_unavailable"),
        }
        return _bounded_telemetry_attributes(raw_attributes)

    def _execution_evidence_payload(
        self,
        metrics_payload: dict[str, object],
        *,
        diagnostic: Any | None,
    ) -> dict[str, object]:
        with self._evidence_lock:
            widths = tuple(self._column_widths[node_id] for node_id in sorted(self._column_widths))
            bytes_read = self._bytes_read
            bytes_written = self._bytes_written
            chunk_count = self._chunk_count
            observed_peak_rss_bytes = self._observed_peak_rss_bytes
            cancellation_latency_ms = self._cancellation_latency_ms
            cache_proof_hits = self._cache_proof_hits
            cache_proof_misses = self._cache_proof_misses
            cache_direct_fallbacks = self._cache_direct_fallbacks
            cache_proof_miss_reason_counts = dict(self._cache_proof_miss_reason_counts)

        retained_widths = widths[:_MAX_RETAINED_COLUMN_WIDTHS]
        width_state = "truncated" if len(widths) > len(retained_widths) else "available"
        strategy = getattr(diagnostic, "strategy", None)
        strategy_value = getattr(strategy, "value", strategy)
        if strategy_value in {
            "projected",
            "schema-all-except",
            "unprojected-streaming-boundary",
        }:
            streamability: str | None = "streaming"
        elif strategy_value in {
            "full-width-admitted-eager",
            "materialisation-boundary",
        }:
            streamability = "materialising"
        else:
            streamability = None

        evidence: list[str] = []
        reason_code = getattr(diagnostic, "reason_code", None)
        if isinstance(reason_code, str) and reason_code:
            evidence.append(reason_code)
        boundaries = getattr(getattr(diagnostic, "boundaries", None), "items", ())
        evidence.extend(
            str(item["boundary_kind"])
            for item in boundaries
            if isinstance(item, Mapping) and "boundary_kind" in item
        )
        canonical_evidence = tuple(sorted(set(evidence)))
        retained_evidence = canonical_evidence[:_MAX_STREAMABILITY_EVIDENCE]
        evidence_state = (
            "unavailable"
            if diagnostic is None
            else "truncated"
            if len(canonical_evidence) > len(retained_evidence)
            else "available"
        )
        estimated_bytes = getattr(diagnostic, "estimated_peak_bytes", None)
        raw_estimated_bytes = getattr(diagnostic, "raw_estimated_peak_bytes", None)
        calibration_factor = getattr(
            diagnostic,
            "estimate_calibration_factor_basis_points",
            None,
        )
        estimate_admission_basis = getattr(diagnostic, "estimate_admission_basis", None)
        baseline = self.memory_baseline_bytes
        if baseline is None:
            raw_rss_start = metrics_payload.get("rss_start_bytes")
            baseline = raw_rss_start if isinstance(raw_rss_start, int) else None
        observed_peak_rss_growth_bytes = (
            max(0, observed_peak_rss_bytes - baseline)
            if observed_peak_rss_bytes is not None and baseline is not None
            else None
        )
        requested_width_total = (
            sum(cast(int, item.requested_width) for item in widths)
            if widths and all(item.requested_width is not None for item in widths)
            else None
        )
        scanned_width_total = (
            sum(cast(int, item.physically_scanned_width) for item in widths)
            if widths and all(item.physically_scanned_width is not None for item in widths)
            else None
        )
        return {
            "streamability": streamability,
            "streamability_evidence": {
                "state": evidence_state,
                "total_count": None if evidence_state == "unavailable" else len(canonical_evidence),
                "items": list(retained_evidence),
            },
            "column_widths": {
                "state": width_state,
                "total_count": len(widths),
                "items": [item.to_dict() for item in retained_widths],
            },
            "requested_column_width_total": requested_width_total,
            "physically_scanned_column_width_total": scanned_width_total,
            "cache_proof": {
                "hits": cache_proof_hits,
                "misses": cache_proof_misses,
                "direct_fallbacks": cache_direct_fallbacks,
                "miss_reason_counts": {
                    reason.value: cache_proof_miss_reason_counts[reason]
                    for reason in ExecutionCacheProofMissReason
                },
            },
            "bytes_read": bytes_read,
            "bytes_written": bytes_written,
            "estimated_bytes": estimated_bytes,
            "raw_estimated_bytes": raw_estimated_bytes,
            "estimate_calibration_factor_basis_points": calibration_factor,
            "estimate_admission_basis": estimate_admission_basis,
            "checkpoint_count": metrics_payload["n_checkpoints"],
            "chunk_count": chunk_count,
            "observed_peak_rss_bytes": observed_peak_rss_bytes,
            "observed_peak_rss_growth_bytes": observed_peak_rss_growth_bytes,
            "cancellation_latency_ms": cancellation_latency_ms,
        }

    def _active_stage_stack(self) -> list[_ActiveStage]:
        stack = getattr(self._stage_local, "stack", None)
        if stack is None:
            stack = []
            self._stage_local.stack = stack
        return stack

    def _observe_rss(
        self,
        rss_bytes: int | None,
        *,
        label: str | None = None,
        stage: str | None = None,
        node_id: str | None = None,
    ) -> None:
        if rss_bytes is None:
            return
        with self._evidence_lock:
            self._observed_peak_rss_bytes = (
                rss_bytes
                if self._observed_peak_rss_bytes is None
                else max(self._observed_peak_rss_bytes, rss_bytes)
            )
        for active_stage in self._active_stage_stack():
            active_stage.rss_peak_bytes = (
                rss_bytes
                if active_stage.rss_peak_bytes is None
                else max(active_stage.rss_peak_bytes, rss_bytes)
            )
        self._record_memory_pressure_events(
            rss_bytes=rss_bytes,
            label=label,
            stage=stage,
            node_id=node_id,
        )

    def _record_checkpoint(self) -> None:
        self.metrics.record_checkpoint()
        for stage in self._active_stage_stack():
            stage.n_checkpoints += 1

    def _check_memory_budget(
        self,
        *,
        rss_bytes: int | None | object = _RSS_SAMPLE_UNSET,
    ) -> None:
        effective_limit = self._effective_rss_limit_bytes()
        if effective_limit is None:
            return
        sampled = (
            self.memory_sampler() if rss_bytes is _RSS_SAMPLE_UNSET else cast(int | None, rss_bytes)
        )
        if sampled is None:
            raise ExecutionMemoryLimitExceededError(
                self.operation,
                job_id=self.job_id,
                rss_bytes=None,
                limit_bytes=(
                    self.memory_limit_bytes
                    if self.memory_limit_bytes is not None
                    else effective_limit
                ),
                baseline_rss_bytes=self.memory_baseline_bytes,
                rss_limit_bytes=effective_limit,
                reason="memory_sampler_unavailable",
            )
        if sampled > effective_limit:
            reason = self._memory_limit_reason(
                sampled=sampled,
                effective_limit=effective_limit,
            )
            raise ExecutionMemoryLimitExceededError(
                self.operation,
                job_id=self.job_id,
                rss_bytes=sampled,
                limit_bytes=(
                    self.memory_limit_bytes
                    if self.memory_limit_bytes is not None
                    else effective_limit
                ),
                baseline_rss_bytes=self.memory_baseline_bytes,
                rss_limit_bytes=effective_limit,
                reason=reason,
            )

    def _record_memory_pressure_events(
        self,
        *,
        rss_bytes: int,
        label: str | None,
        stage: str | None,
        node_id: str | None,
    ) -> None:
        effective_limit = self._effective_rss_limit_bytes()
        if effective_limit is None or effective_limit <= 0:
            return
        baseline = self.memory_baseline_bytes or 0
        budget_window = effective_limit - baseline
        if budget_window <= 0:
            return
        headroom_used_bytes = max(0, rss_bytes - baseline)
        active_stack = self._active_stage_stack()
        active_stage = active_stack[-1] if active_stack else None
        event_stage = stage if stage is not None else active_stage.name if active_stage else None
        event_node_id = (
            node_id if node_id is not None else active_stage.node_id if active_stage else None
        )
        admission = self.admission
        pressure_ratio = round(headroom_used_bytes / budget_window, 6)
        for threshold in _MEMORY_PRESSURE_THRESHOLDS:
            threshold_percent = int(threshold * 100)
            if headroom_used_bytes < math.ceil(budget_window * threshold):
                continue
            with self._memory_pressure_lock:
                if threshold_percent in self._memory_pressure_seen:
                    continue
                self._memory_pressure_seen.add(threshold_percent)
            event = ExecutionMemoryPressureEvent(
                operation=self.operation,
                profile=self.profile,
                job_id=self.job_id,
                node_id=event_node_id,
                stage=event_stage,
                label=label,
                threshold_ratio=threshold,
                threshold_percent=threshold_percent,
                rss_bytes=rss_bytes,
                rss_limit_bytes=effective_limit,
                headroom_bytes=effective_limit - rss_bytes,
                headroom_used_bytes=headroom_used_bytes,
                rss_peak_bytes=rss_bytes,
                memory_limit_bytes=self.memory_limit_bytes,
                memory_baseline_bytes=self.memory_baseline_bytes,
                baseline_rss_bytes=self.memory_baseline_bytes,
                budget_policy=admission.budget_policy if admission is not None else None,
                config_key=admission.config_key if admission is not None else None,
                available_ram_bytes=(
                    admission.available_ram_bytes if admission is not None else None
                ),
                os_reserve_bytes=(admission.os_reserve_bytes if admission is not None else None),
                pressure_ratio=pressure_ratio,
            )
            self.metrics.record_memory_pressure_event(event)
            if self.memory_pressure_callback is not None:
                self.memory_pressure_callback(event)

    def _effective_rss_limit_bytes(self) -> int | None:
        if self.rss_limit_bytes is not None:
            return self.rss_limit_bytes
        if self.memory_limit_bytes is None:
            return None
        if self.memory_baseline_bytes is None:
            return self.memory_limit_bytes
        return self.memory_baseline_bytes + self.memory_limit_bytes

    def _memory_limit_reason(self, *, sampled: int, effective_limit: int) -> str:
        if self.memory_baseline_bytes is None or self.memory_limit_bytes is None:
            return "rss_exceeds_memory_limit"
        growth_limit = self.memory_baseline_bytes + self.memory_limit_bytes
        if effective_limit < growth_limit and sampled > effective_limit:
            return "process_rss_limit_exceeded"
        return "rss_exceeds_memory_limit"


def ensure_execution_context(
    context: ExecutionContext | None,
    *,
    operation: str,
    profile: ExecutionProfile,
    job_id: str | None = None,
) -> ExecutionContext:
    if context is not None:
        return context
    return ExecutionContext(operation=operation, profile=profile, job_id=job_id)


def current_execution_context() -> ExecutionContext | None:
    return _CURRENT_EXECUTION_CONTEXT.get()


def _round_ms(value: float) -> float:
    return round(value, 3)
