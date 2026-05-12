"""Shared execution context for long-running graph work."""

from __future__ import annotations

import contextlib
import contextvars
import ctypes
import math
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

EXECUTION_METRICS_SCHEMA_VERSION = 1
_DEFAULT_MAX_RETAINED_STAGES = 200
_DEFAULT_MAX_RETAINED_MEMORY_PRESSURE_EVENTS = 32
_MEMORY_PRESSURE_THRESHOLDS: tuple[float, ...] = (0.50, 0.75, 0.90)
_CURRENT_EXECUTION_CONTEXT: contextvars.ContextVar[ExecutionContext | None] = (
    contextvars.ContextVar("haute_current_execution_context", default=None)
)


class ExecutionProfile(StrEnum):
    """Named execution profiles shared by execution, routes, and metrics."""

    PREVIEW_EAGER = "preview_eager"
    LAZY_SINK = "lazy_sink"
    TRAINING_PREP = "training_prep"
    OPTIMISER_SETUP = "optimiser_setup"
    AUTO_RANGE = "auto_range"
    DEPLOY_LIVE = "deploy_live"
    DEPLOY_BATCH = "deploy_batch"
    CHUNKED_MAP_REDUCE = "chunked_map_reduce"


class ExecutionCancelledError(RuntimeError):
    """Raised when an execution context has been cancelled."""

    def __init__(self, operation: str, *, job_id: str | None = None) -> None:
        detail = f"Execution {operation!r} was cancelled"
        if job_id:
            detail = f"{detail} (job_id={job_id!r})"
        super().__init__(detail)
        self.operation = operation
        self.job_id = job_id


class ExecutionMemoryLimitExceededError(MemoryError):
    """Raised when a sampled memory checkpoint exceeds the configured budget."""

    def __init__(
        self,
        operation: str,
        *,
        rss_bytes: int,
        limit_bytes: int,
        job_id: str | None = None,
        baseline_rss_bytes: int | None = None,
        rss_limit_bytes: int | None = None,
        reason: str = "rss_exceeds_memory_limit",
    ) -> None:
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

    _event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self, operation: str, *, job_id: str | None = None) -> None:
        if self.cancelled:
            raise ExecutionCancelledError(operation, job_id=job_id)


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
    if not os.path.exists(status_path):
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


def _windows_current_rss_bytes() -> int | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCountersEx(ctypes.Structure):
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

    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    windll_factory = getattr(ctypes, "WinDLL", None)
    if windll_factory is None:
        return None
    try:
        kernel32 = windll_factory("kernel32.dll", use_last_error=True)
        psapi = windll_factory("psapi.dll", use_last_error=True)
        handle = kernel32.GetCurrentProcess()
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCountersEx),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        ok = psapi.GetProcessMemoryInfo(
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

    def cancel(self) -> None:
        self.cancellation_token.cancel()

    def release_admission(self) -> None:
        """Release any in-flight memory reservation held by this context."""
        with self._admission_release_lock:
            if self._admission_released:
                return
            self._admission_released = True
            release = self.admission_release
            self.admission_release = None
        if release is not None:
            release()

    def close(self) -> None:
        """Alias for callers that treat execution contexts as resources."""
        self.release_admission()

    def checkpoint(self, *, label: str, node_id: str | None = None) -> None:
        self.cancellation_token.throw_if_cancelled(self.operation, job_id=self.job_id)
        self._record_checkpoint()
        rss_bytes = (
            self.memory_sampler()
            if self.memory_limit_bytes is not None or self._active_stage_stack()
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
        self.cancellation_token.throw_if_cancelled(self.operation, job_id=self.job_id)
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
        failed = False
        skip_metric = False
        try:
            yield
        except BaseException as exc:
            failed = True
            skip_metric = bool(
                skip_metric_on_exception and isinstance(exc, skip_metric_on_exception)
            )
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
            rss_end = self.memory_sampler()
            self._observe_rss(rss_end, stage=name, node_id=node_id)
            self._active_stage_stack().pop()
            _CURRENT_EXECUTION_CONTEXT.reset(current_context_token)
            if not skip_metric:
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
            if not failed:
                self._check_memory_budget(rss_bytes=rss_end)

    def record_collect(self) -> None:
        """Record a Polars materialisation against active execution stages."""
        self.metrics.record_collect()
        for stage in self._active_stage_stack():
            stage.n_collects += 1

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
        payload["projection_plan_diagnostics"] = (
            projection_plan.diagnostics_payload(profile=self.profile.value)
            if projection_plan is not None and hasattr(projection_plan, "diagnostics_payload")
            else None
        )
        return payload

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

    def _check_memory_budget(self, *, rss_bytes: int | None = None) -> None:
        effective_limit = self._effective_rss_limit_bytes()
        if effective_limit is None:
            return
        sampled = self.memory_sampler() if rss_bytes is None else rss_bytes
        if sampled is None:
            return
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
