"""Profile-aware execution admission and budget construction."""

from __future__ import annotations

import os
import threading
import time
import weakref
from collections.abc import Callable, Collection
from dataclasses import dataclass
from itertools import count

from haute import _host_memory
from haute._env import optional_int_env
from haute._execution_context import (
    ExecutionAdmission,
    ExecutionCancellationToken,
    ExecutionContext,
    ExecutionProfile,
    current_rss_bytes,
)

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_MEMORY_POLICY_ENV = "HAUTE_EXECUTION_MEMORY_POLICY"
_ADAPTIVE_MEMORY_POLICY_NAME = "local_adaptive"
_FIXED_MEMORY_POLICY_NAME = "fixed"
_STRICT_SERVER_MEMORY_POLICY_NAME = "strict_server"
_OS_RESERVE_ENV = (
    "HAUTE_EXECUTION_OS_RESERVE_BYTES",
    "HAUTE_EXECUTION_OS_RESERVE_MB",
)
_DEFAULT_OS_RESERVE_BYTES = 2 * _GIB

_DEFAULT_MEMORY_LIMIT_BYTES: dict[ExecutionProfile, int] = {
    ExecutionProfile.PREVIEW_EAGER: 2 * 1024 * _MIB,
    ExecutionProfile.LAZY_SINK: 4 * 1024 * _MIB,
    ExecutionProfile.TRAINING_PREP: 4 * 1024 * _MIB,
    ExecutionProfile.OPTIMISER_SETUP: 4 * 1024 * _MIB,
    ExecutionProfile.OPTIMISER_SOLVE: 4 * 1024 * _MIB,
    ExecutionProfile.EXPLORE_ANALYSIS: 4 * 1024 * _MIB,
    ExecutionProfile.AUTO_RANGE: 2 * 1024 * _MIB,
    ExecutionProfile.DEPLOY_LIVE: 1024 * _MIB,
    ExecutionProfile.DEPLOY_BATCH: 4 * 1024 * _MIB,
    ExecutionProfile.CHUNKED_MAP_REDUCE: 4 * 1024 * _MIB,
    ExecutionProfile.NODE_SNAPSHOT: 4 * 1024 * _MIB,
}


@dataclass(frozen=True, slots=True)
class _AdaptiveMemoryPolicy:
    available_ram_basis_points: int
    floor_bytes: int
    ceiling_bytes: int | None = None


_ADAPTIVE_MEMORY_POLICY: dict[ExecutionProfile, _AdaptiveMemoryPolicy] = {
    ExecutionProfile.PREVIEW_EAGER: _AdaptiveMemoryPolicy(
        available_ram_basis_points=3_500,
        floor_bytes=2 * 1024 * _MIB,
        ceiling_bytes=4 * 1024 * _MIB,
    ),
    ExecutionProfile.LAZY_SINK: _AdaptiveMemoryPolicy(
        available_ram_basis_points=7_000,
        floor_bytes=4 * 1024 * _MIB,
    ),
    ExecutionProfile.TRAINING_PREP: _AdaptiveMemoryPolicy(
        available_ram_basis_points=7_500,
        floor_bytes=4 * 1024 * _MIB,
    ),
    ExecutionProfile.OPTIMISER_SETUP: _AdaptiveMemoryPolicy(
        available_ram_basis_points=7_500,
        floor_bytes=4 * 1024 * _MIB,
    ),
    # The optimiser's solver session runs under a native cap that kills only
    # itself, so it may take everything above the OS reserve (OPT-W01).
    ExecutionProfile.OPTIMISER_SOLVE: _AdaptiveMemoryPolicy(
        available_ram_basis_points=10_000,
        floor_bytes=4 * 1024 * _MIB,
    ),
    ExecutionProfile.EXPLORE_ANALYSIS: _AdaptiveMemoryPolicy(
        available_ram_basis_points=7_000,
        floor_bytes=4 * 1024 * _MIB,
    ),
    ExecutionProfile.AUTO_RANGE: _AdaptiveMemoryPolicy(
        available_ram_basis_points=6_000,
        floor_bytes=2 * 1024 * _MIB,
    ),
    ExecutionProfile.DEPLOY_LIVE: _AdaptiveMemoryPolicy(
        available_ram_basis_points=2_500,
        floor_bytes=1024 * _MIB,
        ceiling_bytes=2 * 1024 * _MIB,
    ),
    ExecutionProfile.DEPLOY_BATCH: _AdaptiveMemoryPolicy(
        available_ram_basis_points=7_000,
        floor_bytes=4 * 1024 * _MIB,
    ),
    ExecutionProfile.CHUNKED_MAP_REDUCE: _AdaptiveMemoryPolicy(
        available_ram_basis_points=6_000,
        floor_bytes=4 * 1024 * _MIB,
    ),
    ExecutionProfile.NODE_SNAPSHOT: _AdaptiveMemoryPolicy(
        available_ram_basis_points=7_000,
        floor_bytes=4 * 1024 * _MIB,
    ),
}

_ADAPTIVE_LOCAL_PROFILES = frozenset(
    {
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.OPTIMISER_SOLVE,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
        ExecutionProfile.NODE_SNAPSHOT,
    }
)

_PROFILE_MEMORY_ENV: dict[ExecutionProfile, tuple[str, str]] = {
    ExecutionProfile.PREVIEW_EAGER: (
        "HAUTE_PREVIEW_MEMORY_LIMIT_BYTES",
        "HAUTE_PREVIEW_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.LAZY_SINK: (
        "HAUTE_SINK_MEMORY_LIMIT_BYTES",
        "HAUTE_SINK_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.TRAINING_PREP: (
        "HAUTE_TRAINING_MEMORY_LIMIT_BYTES",
        "HAUTE_TRAINING_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.OPTIMISER_SETUP: (
        "HAUTE_OPTIMISER_MEMORY_LIMIT_BYTES",
        "HAUTE_OPTIMISER_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.OPTIMISER_SOLVE: (
        "HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_BYTES",
        "HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.EXPLORE_ANALYSIS: (
        "HAUTE_EXPLORE_MEMORY_LIMIT_BYTES",
        "HAUTE_EXPLORE_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.AUTO_RANGE: (
        "HAUTE_AUTO_RANGE_MEMORY_LIMIT_BYTES",
        "HAUTE_AUTO_RANGE_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.DEPLOY_LIVE: (
        "HAUTE_DEPLOY_LIVE_MEMORY_LIMIT_BYTES",
        "HAUTE_DEPLOY_LIVE_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.DEPLOY_BATCH: (
        "HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_BYTES",
        "HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.CHUNKED_MAP_REDUCE: (
        "HAUTE_CHUNKED_MEMORY_LIMIT_BYTES",
        "HAUTE_CHUNKED_MEMORY_LIMIT_MB",
    ),
    ExecutionProfile.NODE_SNAPSHOT: (
        "HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_BYTES",
        "HAUTE_NODE_SNAPSHOT_MEMORY_LIMIT_MB",
    ),
}

_GLOBAL_MEMORY_ENV = (
    "HAUTE_EXECUTION_MEMORY_LIMIT_BYTES",
    "HAUTE_EXECUTION_MEMORY_LIMIT_MB",
)

_PROFILE_PROCESS_RSS_ENV: dict[ExecutionProfile, tuple[str, str]] = {
    ExecutionProfile.PREVIEW_EAGER: (
        "HAUTE_PREVIEW_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.LAZY_SINK: (
        "HAUTE_SINK_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_SINK_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.TRAINING_PREP: (
        "HAUTE_TRAINING_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_TRAINING_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.OPTIMISER_SETUP: (
        "HAUTE_OPTIMISER_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_OPTIMISER_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.OPTIMISER_SOLVE: (
        "HAUTE_OPTIMISER_SOLVE_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_OPTIMISER_SOLVE_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.EXPLORE_ANALYSIS: (
        "HAUTE_EXPLORE_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_EXPLORE_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.AUTO_RANGE: (
        "HAUTE_AUTO_RANGE_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_AUTO_RANGE_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.DEPLOY_LIVE: (
        "HAUTE_DEPLOY_LIVE_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_DEPLOY_LIVE_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.DEPLOY_BATCH: (
        "HAUTE_DEPLOY_BATCH_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_DEPLOY_BATCH_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.CHUNKED_MAP_REDUCE: (
        "HAUTE_CHUNKED_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_CHUNKED_PROCESS_RSS_LIMIT_MB",
    ),
    ExecutionProfile.NODE_SNAPSHOT: (
        "HAUTE_NODE_SNAPSHOT_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_NODE_SNAPSHOT_PROCESS_RSS_LIMIT_MB",
    ),
}

_GLOBAL_PROCESS_RSS_ENV = (
    "HAUTE_EXECUTION_PROCESS_RSS_LIMIT_BYTES",
    "HAUTE_EXECUTION_PROCESS_RSS_LIMIT_MB",
)

_IN_FLIGHT_PROFILE_SET = frozenset(
    {
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.OPTIMISER_SOLVE,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
        ExecutionProfile.NODE_SNAPSHOT,
    }
)
_IN_FLIGHT_LOCK = threading.RLock()
# Notified on every release so an admission can wait out short-lived holders.
_IN_FLIGHT_RELEASED = threading.Condition(_IN_FLIGHT_LOCK)
# A waiting admission re-checks cancellation at least this often.
_IN_FLIGHT_WAIT_SLICE_SECONDS = 0.25
# A refusal names at most this many distinct holders; the byte totals stay exact.
_MAX_REPORTED_IN_FLIGHT_OPERATIONS = 8
_IN_FLIGHT_COUNTER = count(1)
_IN_FLIGHT_RESERVATIONS: dict[int, tuple[ExecutionProfile, int, str]] = {}


@dataclass(frozen=True, slots=True)
class WorkEstimate:
    """Bounded work's own peak estimate, admitted and reserved in place of a whole budget."""

    estimated_bytes: int
    subject: str
    """What the work is, for the refusal: "The optimiser choice query (TopK)"."""
    remedy: str
    """What the user can do when it does not fit."""

    def __post_init__(self) -> None:
        if (
            isinstance(self.estimated_bytes, bool)
            or not isinstance(self.estimated_bytes, int)
            or self.estimated_bytes <= 0
        ):
            raise ValueError("estimated_bytes must be a positive integer")


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    """Resolved per-profile execution budget."""

    memory_limit_bytes: int
    config_key: str
    process_rss_limit_bytes: int | None = None
    process_rss_limit_config_key: str | None = None
    budget_policy: str = "fixed_default"
    available_ram_bytes: int | None = None
    os_reserve_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class IsolatedExecutionBudget:
    """Pickle-safe admitted budget used to construct a child-local context."""

    operation: str
    profile: ExecutionProfile
    memory_limit_bytes: int
    config_key: str
    budget_policy: str
    process_rss_limit_bytes: int | None = None
    available_ram_bytes: int | None = None
    os_reserve_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.memory_limit_bytes, int)
            or isinstance(self.memory_limit_bytes, bool)
            or self.memory_limit_bytes <= 0
        ):
            raise ValueError("isolated memory_limit_bytes must be a positive integer")
        if self.process_rss_limit_bytes is not None and (
            not isinstance(self.process_rss_limit_bytes, int)
            or isinstance(self.process_rss_limit_bytes, bool)
            or self.process_rss_limit_bytes <= 0
        ):
            raise ValueError("isolated process_rss_limit_bytes must be positive or None")


def isolated_execution_budget(context: ExecutionContext) -> IsolatedExecutionBudget:
    """Extract plain immutable budget evidence from a parent-owned admission."""
    admission = context.admission
    if admission is None or not admission.admitted:
        raise ValueError("isolated execution requires an admitted parent context")
    effective_headroom = admission.headroom_bytes
    if (
        not isinstance(effective_headroom, int)
        or isinstance(effective_headroom, bool)
        or effective_headroom <= 0
    ):
        raise ValueError("isolated execution requires positive admitted memory headroom")
    return IsolatedExecutionBudget(
        operation=context.operation,
        profile=context.profile,
        memory_limit_bytes=effective_headroom,
        config_key=admission.config_key,
        budget_policy=admission.budget_policy,
        process_rss_limit_bytes=admission.process_rss_limit_bytes,
        available_ram_bytes=admission.available_ram_bytes,
        os_reserve_bytes=admission.os_reserve_bytes,
    )


def create_isolated_execution_context(
    budget: IsolatedExecutionBudget,
) -> ExecutionContext:
    """Construct a worker-local limited context without double-reserving admission."""
    if not isinstance(budget, IsolatedExecutionBudget):
        raise TypeError("budget must be an IsolatedExecutionBudget")
    rss_at_start = current_rss_bytes()
    if rss_at_start is None:
        raise ExecutionAdmissionError(
            budget.operation,
            profile=budget.profile,
            memory_limit_bytes=budget.memory_limit_bytes,
            rss_at_admission_bytes=None,
            reason="memory_sampler_unavailable",
        )
    rss_limit_bytes = rss_at_start + budget.memory_limit_bytes
    if budget.process_rss_limit_bytes is not None:
        if rss_at_start >= budget.process_rss_limit_bytes:
            raise ExecutionAdmissionError(
                budget.operation,
                profile=budget.profile,
                memory_limit_bytes=budget.memory_limit_bytes,
                rss_at_admission_bytes=rss_at_start,
                process_rss_limit_bytes=budget.process_rss_limit_bytes,
                reason="process_rss_limit_exceeded",
            )
        rss_limit_bytes = min(rss_limit_bytes, budget.process_rss_limit_bytes)
    admission = ExecutionAdmission(
        operation=budget.operation,
        profile=budget.profile,
        memory_limit_bytes=budget.memory_limit_bytes,
        rss_at_admission_bytes=rss_at_start,
        rss_limit_bytes=rss_limit_bytes,
        process_rss_limit_bytes=budget.process_rss_limit_bytes,
        headroom_bytes=rss_limit_bytes - rss_at_start,
        config_key=budget.config_key,
        budget_policy=budget.budget_policy,
        available_ram_bytes=budget.available_ram_bytes,
        os_reserve_bytes=budget.os_reserve_bytes,
    )
    return ExecutionContext(
        operation=budget.operation,
        profile=budget.profile,
        memory_limit_bytes=budget.memory_limit_bytes,
        memory_baseline_bytes=rss_at_start,
        rss_limit_bytes=rss_limit_bytes,
        admission=admission,
    )


@dataclass(frozen=True, slots=True)
class _ResolvedMemoryLimit:
    memory_limit_bytes: int
    config_key: str
    budget_policy: str
    available_ram_bytes: int | None = None
    os_reserve_bytes: int | None = None


class ExecutionAdmissionError(MemoryError):
    """Raised when a bounded execution is refused before it starts."""

    def __init__(
        self,
        operation: str,
        *,
        profile: ExecutionProfile,
        memory_limit_bytes: int,
        rss_at_admission_bytes: int | None,
        reason: str,
        rss_limit_bytes: int | None = None,
        process_rss_limit_bytes: int | None = None,
        in_flight_reserved_bytes: int | None = None,
        in_flight_limit_bytes: int | None = None,
        in_flight_operations: tuple[str, ...] = (),
        estimated_bytes: int | None = None,
        allowance_bytes: int | None = None,
    ) -> None:
        headroom_bytes = (
            None
            if rss_at_admission_bytes is None or process_rss_limit_bytes is None
            else process_rss_limit_bytes - rss_at_admission_bytes
        )
        detail = f"Execution {operation!r} was not admitted for profile {profile.value!r}: {reason}"
        super().__init__(detail)
        self.operation = operation
        self.profile = profile
        self.memory_limit_bytes = memory_limit_bytes
        self.rss_at_admission_bytes = rss_at_admission_bytes
        self.rss_limit_bytes = rss_limit_bytes
        self.process_rss_limit_bytes = process_rss_limit_bytes
        self.headroom_bytes = headroom_bytes
        self.in_flight_reserved_bytes = in_flight_reserved_bytes
        self.in_flight_limit_bytes = in_flight_limit_bytes
        # ``profile:operation`` labels of the reservations that were already
        # held, so a refusal names the work it lost to instead of "other work".
        self.in_flight_operations = tuple(in_flight_operations)
        # Set when the work's own estimate was refused: what it needed and what
        # was left, so the user message can name both.
        self.estimated_bytes = estimated_bytes
        self.allowance_bytes = allowance_bytes
        self.reason = reason

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "error_code": "memory_limit",
            "operation": self.operation,
            "profile": self.profile.value,
            "memory_limit_bytes": self.memory_limit_bytes,
            "rss_at_admission_bytes": self.rss_at_admission_bytes,
            "rss_limit_bytes": self.rss_limit_bytes,
            "process_rss_limit_bytes": self.process_rss_limit_bytes,
            "headroom_bytes": self.headroom_bytes,
            "reason": self.reason,
        }
        if self.in_flight_reserved_bytes is not None:
            payload["in_flight_reserved_bytes"] = self.in_flight_reserved_bytes
        if self.in_flight_limit_bytes is not None:
            payload["in_flight_limit_bytes"] = self.in_flight_limit_bytes
        if self.in_flight_operations:
            payload["in_flight_operations"] = list(self.in_flight_operations)
        if self.estimated_bytes is not None:
            payload["estimated_bytes"] = self.estimated_bytes
        if self.allowance_bytes is not None:
            payload["allowance_bytes"] = self.allowance_bytes
        return payload


def execution_budget_for_profile(profile: ExecutionProfile) -> ExecutionBudget:
    """Resolve the configured memory budget for *profile*."""
    memory_limit = _resolve_required_budget(profile)
    process_rss_limit_bytes, process_rss_limit_config_key = _resolve_optional_rss_limit(profile)
    return ExecutionBudget(
        memory_limit_bytes=memory_limit.memory_limit_bytes,
        config_key=memory_limit.config_key,
        process_rss_limit_bytes=process_rss_limit_bytes,
        process_rss_limit_config_key=process_rss_limit_config_key,
        budget_policy=memory_limit.budget_policy,
        available_ram_bytes=memory_limit.available_ram_bytes,
        os_reserve_bytes=memory_limit.os_reserve_bytes,
    )


def _resolve_required_budget(profile: ExecutionProfile) -> _ResolvedMemoryLimit:
    for key, multiplier in _memory_env_candidates(profile):
        value = optional_int_env(key)
        if value is None:
            continue
        return _ResolvedMemoryLimit(
            memory_limit_bytes=value * multiplier,
            config_key=key,
            budget_policy="explicit_env",
        )

    memory_policy = _memory_policy_name()
    if memory_policy in {_FIXED_MEMORY_POLICY_NAME, _STRICT_SERVER_MEMORY_POLICY_NAME}:
        return _fixed_default_memory_limit(profile)

    if profile not in _ADAPTIVE_LOCAL_PROFILES:
        return _fixed_default_memory_limit(profile)

    limit, available, os_reserve_bytes = _adaptive_default_memory_limit_bytes(profile)
    return _ResolvedMemoryLimit(
        memory_limit_bytes=limit,
        config_key=f"adaptive:{profile.value}",
        budget_policy="adaptive_local",
        available_ram_bytes=available,
        os_reserve_bytes=os_reserve_bytes,
    )


def _fixed_default_memory_limit(profile: ExecutionProfile) -> _ResolvedMemoryLimit:
    return _ResolvedMemoryLimit(
        memory_limit_bytes=_DEFAULT_MEMORY_LIMIT_BYTES[profile],
        config_key=f"default:{profile.value}",
        budget_policy="fixed_default",
    )


def _memory_policy_name() -> str:
    raw = os.environ.get(_MEMORY_POLICY_ENV, _ADAPTIVE_MEMORY_POLICY_NAME)
    policy = raw.strip().lower()
    if policy not in {
        _ADAPTIVE_MEMORY_POLICY_NAME,
        _FIXED_MEMORY_POLICY_NAME,
        _STRICT_SERVER_MEMORY_POLICY_NAME,
    }:
        raise RuntimeError(
            f"{_MEMORY_POLICY_ENV} must be one of "
            f"{_ADAPTIVE_MEMORY_POLICY_NAME!r}, {_FIXED_MEMORY_POLICY_NAME!r}, "
            f"or {_STRICT_SERVER_MEMORY_POLICY_NAME!r}"
        )
    return policy


def available_ram_bytes() -> int | None:
    """Return available RAM through an admission-local patch point."""
    return _host_memory.available_ram_bytes()


def _adaptive_default_memory_limit_bytes(profile: ExecutionProfile) -> tuple[int, int, int]:
    available = _host_memory.require_positive_available_ram(available_ram_bytes())
    policy = _ADAPTIVE_MEMORY_POLICY[profile]
    reserve = min(_resolve_os_reserve_bytes(), max(available // 2, 1))
    usable = max(available - reserve, 1)
    limit = usable * policy.available_ram_basis_points // 10_000
    limit = max(limit, policy.floor_bytes)
    if policy.ceiling_bytes is not None:
        limit = min(limit, policy.ceiling_bytes)
    return min(limit, usable), available, reserve


def _resolve_os_reserve_bytes() -> int:
    bytes_key, mb_key = _OS_RESERVE_ENV
    bytes_value = optional_int_env(bytes_key)
    if bytes_value is not None:
        return bytes_value
    mb_value = optional_int_env(mb_key)
    if mb_value is not None:
        return mb_value * _MIB
    return _DEFAULT_OS_RESERVE_BYTES


def _resolve_optional_rss_limit(profile: ExecutionProfile) -> tuple[int | None, str | None]:
    for key, multiplier in _process_rss_env_candidates(profile):
        value = optional_int_env(key)
        if value is None:
            continue
        return value * multiplier, key
    return None, None


def create_admitted_execution_context(
    *,
    operation: str,
    profile: ExecutionProfile,
    job_id: str | None = None,
    cancellation_token: ExecutionCancellationToken | None = None,
    memory_sampler: Callable[[], int | None] | None = None,
    memory_pressure_callback: Callable[..., None] | None = None,
    wait_out_holders: Collection[str] = (),
    wait_seconds: float = 0.0,
    budget_profile: ExecutionProfile | None = None,
    estimate: WorkEstimate | None = None,
) -> ExecutionContext:
    """Construct an ``ExecutionContext`` after a small memory admission check.

    ``estimate`` is the work's own peak estimate: admission refuses work whose
    estimate exceeds the profile's budget, naming the estimate and its remedy,
    and reserves the estimate rather than the whole budget in flight, so
    several small bounded operations can run side by side.

    ``budget_profile`` sizes the memory budget from another profile's policy
    while admission and in-flight reservation still follow ``profile``: a
    preview that builds node caches gets the cache-build budget, and is still
    admitted, and never reserved, as a preview.

    ``wait_out_holders`` names in-flight holders (``"profile:operation"``) that
    are short-lived and not worth refusing for: while every holder blocking the
    reservation is one of them, admission waits up to ``wait_seconds`` for them
    to release. Any other holder refuses at once, as without a wait. Waiting and
    reserving are separate steps, so a waitable holder that takes the budget in
    between sends admission back to waiting until the same deadline.
    """
    budget = execution_budget_for_profile(budget_profile or profile)
    reservation_bytes = budget.memory_limit_bytes if estimate is None else estimate.estimated_bytes
    waitable = frozenset(wait_out_holders) if profile in _IN_FLIGHT_PROFILE_SET else frozenset()
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    while True:
        if waitable:
            _wait_out_in_flight_holders(
                budget,
                reservation_bytes,
                waitable,
                deadline,
                cancellation_token=cancellation_token,
                operation=operation,
                job_id=job_id,
            )
        try:
            return _admit_once(
                operation=operation,
                profile=profile,
                budget=budget,
                estimate=estimate,
                reservation_bytes=reservation_bytes,
                job_id=job_id,
                cancellation_token=cancellation_token,
                memory_sampler=memory_sampler,
                memory_pressure_callback=memory_pressure_callback,
            )
        except ExecutionAdmissionError as exc:
            if (
                not waitable
                or exc.reason != "in_flight_memory_budget_exceeded"
                or time.monotonic() >= deadline
                or not _refusal_is_transient(budget, reservation_bytes, waitable)
            ):
                raise


def _admit_once(
    *,
    operation: str,
    profile: ExecutionProfile,
    budget: ExecutionBudget,
    estimate: WorkEstimate | None,
    reservation_bytes: int,
    job_id: str | None,
    cancellation_token: ExecutionCancellationToken | None,
    memory_sampler: Callable[[], int | None] | None,
    memory_pressure_callback: Callable[..., None] | None,
) -> ExecutionContext:
    """Sample RSS, reserve the in-flight budget, and build the context once."""
    sampler = current_rss_bytes if memory_sampler is None else memory_sampler
    rss_at_admission = sampler()
    if rss_at_admission is None:
        raise ExecutionAdmissionError(
            operation,
            profile=profile,
            memory_limit_bytes=budget.memory_limit_bytes,
            rss_at_admission_bytes=None,
            reason="memory_sampler_unavailable",
        )
    if (
        budget.process_rss_limit_bytes is not None
        and rss_at_admission >= budget.process_rss_limit_bytes
    ):
        raise ExecutionAdmissionError(
            operation,
            profile=profile,
            memory_limit_bytes=budget.memory_limit_bytes,
            rss_at_admission_bytes=rss_at_admission,
            process_rss_limit_bytes=budget.process_rss_limit_bytes,
            reason="process_rss_limit_exceeded",
        )

    rss_limit_bytes = rss_at_admission + budget.memory_limit_bytes
    if budget.process_rss_limit_bytes is not None:
        rss_limit_bytes = min(rss_limit_bytes, budget.process_rss_limit_bytes)
    allowance_bytes = rss_limit_bytes - rss_at_admission
    if estimate is not None and estimate.estimated_bytes > allowance_bytes:
        raise ExecutionAdmissionError(
            operation,
            profile=profile,
            memory_limit_bytes=budget.memory_limit_bytes,
            rss_at_admission_bytes=rss_at_admission,
            rss_limit_bytes=rss_limit_bytes,
            process_rss_limit_bytes=budget.process_rss_limit_bytes,
            estimated_bytes=estimate.estimated_bytes,
            allowance_bytes=allowance_bytes,
            reason=(
                f"{estimate.subject} needs an estimated {estimate.estimated_bytes} bytes; "
                f"the {profile.value} allowance is {allowance_bytes} bytes. {estimate.remedy}"
            ),
        )
    admission_release = _reserve_in_flight_budget(
        operation=operation,
        profile=profile,
        budget=budget,
        reservation_bytes=reservation_bytes,
        rss_at_admission_bytes=rss_at_admission,
    )
    try:
        admission = ExecutionAdmission(
            operation=operation,
            profile=profile,
            memory_limit_bytes=budget.memory_limit_bytes,
            rss_at_admission_bytes=rss_at_admission,
            rss_limit_bytes=rss_limit_bytes,
            process_rss_limit_bytes=budget.process_rss_limit_bytes,
            headroom_bytes=rss_limit_bytes - rss_at_admission,
            config_key=budget.config_key,
            budget_policy=budget.budget_policy,
            available_ram_bytes=budget.available_ram_bytes,
            os_reserve_bytes=budget.os_reserve_bytes,
        )
        context = ExecutionContext(
            operation=operation,
            profile=profile,
            job_id=job_id,
            cancellation_token=cancellation_token or ExecutionCancellationToken(),
            memory_limit_bytes=budget.memory_limit_bytes,
            memory_baseline_bytes=rss_at_admission,
            rss_limit_bytes=rss_limit_bytes,
            admission=admission,
            memory_sampler=sampler,
            memory_pressure_callback=memory_pressure_callback,
            admission_release=admission_release,
        )
        if admission_release is not None:
            weakref.finalize(context, admission_release)
    except BaseException:
        if admission_release is not None:
            admission_release()
        raise
    return context


def admit_growth_grant(
    *,
    operation: str,
    profile: ExecutionProfile,
    job_id: str | None = None,
    cancellation_token: ExecutionCancellationToken | None = None,
    memory_sampler: Callable[[], int | None] | None = None,
    wait_out_holders: Collection[str] = (),
    wait_seconds: float = 0.0,
) -> ExecutionContext:
    """Admit as much memory growth as the machine can give right now.

    For work that runs under a native cap sized from this grant (the
    optimiser's solver session, OPT-W01). The short-lived *wait_out_holders*
    are waited out first; only then is memory sampled, so the grant sees what
    they freed. The grant is the least of the profile's limit (recomputed from
    that sample), what other in-flight reservations leave of it, and any
    absolute process-RSS headroom. Competing work shrinks the grant instead of
    refusing it, and any positive grant is admitted: whether it suffices is for
    the capped execution to find out. Only a grant of nothing refuses, naming
    the term that bound it.
    """
    if profile not in _IN_FLIGHT_PROFILE_SET:
        raise ValueError(f"growth grants need an in-flight profile, not {profile.value!r}")
    waitable = frozenset(wait_out_holders)
    if waitable:
        _wait_for_holders_to_release(
            waitable,
            time.monotonic() + max(wait_seconds, 0.0),
            cancellation_token=cancellation_token,
            operation=operation,
            job_id=job_id,
        )
    sampler = current_rss_bytes if memory_sampler is None else memory_sampler
    rss_at_admission = sampler()
    process_rss_limit_bytes, _process_rss_limit_key = _resolve_optional_rss_limit(profile)
    with _IN_FLIGHT_LOCK:
        available = _host_memory.require_positive_available_ram(available_ram_bytes())
        os_reserve = min(_resolve_os_reserve_bytes(), max(available // 2, 1))
        usable_now = available - os_reserve
        limit = _profile_limit_from_sample(profile, available=available, usable=usable_now)
        if rss_at_admission is None:
            raise ExecutionAdmissionError(
                operation,
                profile=profile,
                memory_limit_bytes=limit.memory_limit_bytes,
                rss_at_admission_bytes=None,
                reason="memory_sampler_unavailable",
            )
        holders = list(_IN_FLIGHT_RESERVATIONS.values())
        reserved = sum(amount for _profile, amount, _operation in holders)
        in_flight_room = usable_now - reserved
        rss_room = (
            None if process_rss_limit_bytes is None else process_rss_limit_bytes - rss_at_admission
        )
        grant = min(limit.memory_limit_bytes, in_flight_room)
        if rss_room is not None:
            grant = min(grant, rss_room)
        if grant <= 0:
            if rss_room is not None and rss_room <= 0:
                reason = "process_rss_limit_exceeded"
            elif reserved > 0 and in_flight_room <= 0:
                reason = "in_flight_memory_budget_exceeded"
            else:
                reason = "no_memory_available"
            raise ExecutionAdmissionError(
                operation,
                profile=profile,
                memory_limit_bytes=limit.memory_limit_bytes,
                rss_at_admission_bytes=rss_at_admission,
                reason=reason,
                process_rss_limit_bytes=process_rss_limit_bytes,
                in_flight_reserved_bytes=reserved,
                in_flight_limit_bytes=usable_now,
                in_flight_operations=tuple(
                    sorted(
                        {
                            f"{held_profile.value}:{held_operation}"
                            for held_profile, _amount, held_operation in holders
                        }
                    )
                )[:_MAX_REPORTED_IN_FLIGHT_OPERATIONS],
            )
        reservation_id = next(_IN_FLIGHT_COUNTER)
        _IN_FLIGHT_RESERVATIONS[reservation_id] = (profile, grant, operation)

    admission_release = _reservation_release(reservation_id)
    try:
        rss_limit_bytes = rss_at_admission + grant
        admission = ExecutionAdmission(
            operation=operation,
            profile=profile,
            memory_limit_bytes=grant,
            rss_at_admission_bytes=rss_at_admission,
            rss_limit_bytes=rss_limit_bytes,
            process_rss_limit_bytes=process_rss_limit_bytes,
            headroom_bytes=grant,
            config_key=limit.config_key,
            budget_policy=limit.budget_policy,
            available_ram_bytes=available,
            os_reserve_bytes=os_reserve,
        )
        context = ExecutionContext(
            operation=operation,
            profile=profile,
            job_id=job_id,
            cancellation_token=cancellation_token or ExecutionCancellationToken(),
            memory_limit_bytes=grant,
            memory_baseline_bytes=rss_at_admission,
            rss_limit_bytes=rss_limit_bytes,
            admission=admission,
            memory_sampler=sampler,
            admission_release=admission_release,
        )
        weakref.finalize(context, admission_release)
    except BaseException:
        admission_release()
        raise
    return context


def _profile_limit_from_sample(
    profile: ExecutionProfile, *, available: int, usable: int
) -> _ResolvedMemoryLimit:
    """The profile's memory limit resolved against one fresh availability sample."""
    for key, multiplier in _memory_env_candidates(profile):
        value = optional_int_env(key)
        if value is not None:
            return _ResolvedMemoryLimit(
                memory_limit_bytes=value * multiplier,
                config_key=key,
                budget_policy="explicit_env",
            )
    if (
        _memory_policy_name() in {_FIXED_MEMORY_POLICY_NAME, _STRICT_SERVER_MEMORY_POLICY_NAME}
        or profile not in _ADAPTIVE_LOCAL_PROFILES
    ):
        return _fixed_default_memory_limit(profile)
    policy = _ADAPTIVE_MEMORY_POLICY[profile]
    limit = max(usable * policy.available_ram_basis_points // 10_000, policy.floor_bytes)
    if policy.ceiling_bytes is not None:
        limit = min(limit, policy.ceiling_bytes)
    return _ResolvedMemoryLimit(
        memory_limit_bytes=min(limit, max(usable, 1)),
        config_key=f"adaptive:{profile.value}",
        budget_policy="adaptive_local",
        available_ram_bytes=available,
    )


def _wait_for_holders_to_release(
    waitable: frozenset[str],
    deadline: float,
    *,
    cancellation_token: ExecutionCancellationToken | None,
    operation: str,
    job_id: str | None,
) -> None:
    """Wait until none of the *waitable* holders has a reservation, or the deadline passes."""
    with _IN_FLIGHT_LOCK:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not any(
                f"{held_profile.value}:{held_operation}" in waitable
                for held_profile, _amount, held_operation in _IN_FLIGHT_RESERVATIONS.values()
            ):
                return
            _IN_FLIGHT_RELEASED.wait(min(remaining, _IN_FLIGHT_WAIT_SLICE_SECONDS))
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled(operation, job_id=job_id)


def _memory_env_candidates(profile: ExecutionProfile) -> tuple[tuple[str, int], ...]:
    profile_bytes, profile_mb = _PROFILE_MEMORY_ENV[profile]
    global_bytes, global_mb = _GLOBAL_MEMORY_ENV
    return (
        (profile_bytes, 1),
        (profile_mb, _MIB),
        (global_bytes, 1),
        (global_mb, _MIB),
    )


def _refusal_is_transient(
    budget: ExecutionBudget, reservation_bytes: int, waitable: frozenset[str]
) -> bool:
    """Whether an in-flight refusal can clear by waiting.

    True while every current holder is *waitable*, including when they have all
    released since the refusal — unless the reservation could not fit even in an
    empty budget, which no amount of waiting fixes.
    """
    with _IN_FLIGHT_LOCK:
        holders = list(_IN_FLIGHT_RESERVATIONS.values())
    if not holders:
        return reservation_bytes <= _in_flight_limit_bytes(budget)
    return all(
        f"{held_profile.value}:{held_operation}" in waitable
        for held_profile, _amount, held_operation in holders
    )


def _wait_out_in_flight_holders(
    budget: ExecutionBudget,
    reservation_bytes: int,
    waitable: frozenset[str],
    deadline: float,
    *,
    cancellation_token: ExecutionCancellationToken | None,
    operation: str,
    job_id: str | None,
) -> None:
    """Wait while only *waitable* holders keep this reservation out of the budget.

    Returns once nothing holds the budget, the reservation would fit, a
    non-waitable holder blocks it, or the wait runs out; the reservation itself
    then admits or refuses as usual.
    """
    limit_bytes = _in_flight_limit_bytes(budget)
    with _IN_FLIGHT_LOCK:
        while True:
            holders = list(_IN_FLIGHT_RESERVATIONS.values())
            reserved = sum(amount for _profile, amount, _operation in holders)
            remaining = deadline - time.monotonic()
            if (
                not holders
                or reserved + reservation_bytes <= limit_bytes
                or remaining <= 0
                or any(
                    f"{held_profile.value}:{held_operation}" not in waitable
                    for held_profile, _amount, held_operation in holders
                )
            ):
                return
            _IN_FLIGHT_RELEASED.wait(min(remaining, _IN_FLIGHT_WAIT_SLICE_SECONDS))
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled(operation, job_id=job_id)


def _reserve_in_flight_budget(
    *,
    operation: str,
    profile: ExecutionProfile,
    budget: ExecutionBudget,
    reservation_bytes: int,
    rss_at_admission_bytes: int | None,
) -> Callable[[], None] | None:
    """Reserve a share of process-wide in-flight memory for heavy work."""
    if profile not in _IN_FLIGHT_PROFILE_SET:
        return None
    limit_bytes = _in_flight_limit_bytes(budget)
    with _IN_FLIGHT_LOCK:
        reserved = sum(amount for _profile, amount, _operation in _IN_FLIGHT_RESERVATIONS.values())
        if reserved + reservation_bytes > limit_bytes:
            holders = tuple(
                sorted(
                    {
                        f"{held_profile.value}:{held_operation}"
                        for held_profile, _amount, held_operation in (
                            _IN_FLIGHT_RESERVATIONS.values()
                        )
                    }
                )
            )[:_MAX_REPORTED_IN_FLIGHT_OPERATIONS]
            raise ExecutionAdmissionError(
                operation,
                profile=profile,
                memory_limit_bytes=budget.memory_limit_bytes,
                rss_at_admission_bytes=rss_at_admission_bytes,
                reason="in_flight_memory_budget_exceeded",
                process_rss_limit_bytes=budget.process_rss_limit_bytes,
                in_flight_reserved_bytes=reserved,
                in_flight_limit_bytes=limit_bytes,
                in_flight_operations=holders,
            )
        reservation_id = next(_IN_FLIGHT_COUNTER)
        _IN_FLIGHT_RESERVATIONS[reservation_id] = (
            profile,
            reservation_bytes,
            operation,
        )
    return _reservation_release(reservation_id)


def _reservation_release(reservation_id: int) -> Callable[[], None]:
    """Remove one in-flight reservation exactly once and wake waiting admissions."""
    released = False
    release_lock = threading.RLock()

    def release() -> None:
        nonlocal released
        with release_lock:
            if released:
                return
            released = True
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT_RESERVATIONS.pop(reservation_id, None)
            _IN_FLIGHT_RELEASED.notify_all()

    return release


def _in_flight_limit_bytes(budget: ExecutionBudget) -> int:
    available = budget.available_ram_bytes
    if available is None:
        available = available_ram_bytes()
    available = _host_memory.require_positive_available_ram(available)
    reserve = budget.os_reserve_bytes
    if reserve is None:
        reserve = min(_resolve_os_reserve_bytes(), max(available // 2, 1))
    return max(available - reserve, 1)


def _clear_in_flight_reservations_for_tests() -> None:
    """Clear process-local reservations for tests that patch memory policy."""
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT_RESERVATIONS.clear()
        _IN_FLIGHT_RELEASED.notify_all()


def _process_rss_env_candidates(profile: ExecutionProfile) -> tuple[tuple[str, int], ...]:
    profile_bytes, profile_mb = _PROFILE_PROCESS_RSS_ENV[profile]
    global_bytes, global_mb = _GLOBAL_PROCESS_RSS_ENV
    return (
        (profile_bytes, 1),
        (profile_mb, _MIB),
        (global_bytes, 1),
        (global_mb, _MIB),
    )
