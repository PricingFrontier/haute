"""Profile-aware execution admission and budget construction."""

from __future__ import annotations

import os
import threading
import weakref
from collections.abc import Callable
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
    ExecutionProfile.EXPLORE_ANALYSIS: 4 * 1024 * _MIB,
    ExecutionProfile.AUTO_RANGE: 2 * 1024 * _MIB,
    ExecutionProfile.DEPLOY_LIVE: 1024 * _MIB,
    ExecutionProfile.DEPLOY_BATCH: 4 * 1024 * _MIB,
    ExecutionProfile.CHUNKED_MAP_REDUCE: 4 * 1024 * _MIB,
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
}

_ADAPTIVE_LOCAL_PROFILES = frozenset(
    {
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
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
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
    }
)
_IN_FLIGHT_LOCK = threading.RLock()
_IN_FLIGHT_COUNTER = count(1)
_IN_FLIGHT_RESERVATIONS: dict[int, tuple[ExecutionProfile, int, str]] = {}


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
) -> ExecutionContext:
    """Construct an ``ExecutionContext`` after a small memory admission check."""
    budget = execution_budget_for_profile(profile)
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
        and rss_at_admission > budget.process_rss_limit_bytes
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
    admission_release = _reserve_in_flight_budget(
        operation=operation,
        profile=profile,
        budget=budget,
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


def _memory_env_candidates(profile: ExecutionProfile) -> tuple[tuple[str, int], ...]:
    profile_bytes, profile_mb = _PROFILE_MEMORY_ENV[profile]
    global_bytes, global_mb = _GLOBAL_MEMORY_ENV
    return (
        (profile_bytes, 1),
        (profile_mb, _MIB),
        (global_bytes, 1),
        (global_mb, _MIB),
    )


def _reserve_in_flight_budget(
    *,
    operation: str,
    profile: ExecutionProfile,
    budget: ExecutionBudget,
    rss_at_admission_bytes: int | None,
) -> Callable[[], None] | None:
    """Reserve a share of process-wide in-flight memory for heavy work."""
    if profile not in _IN_FLIGHT_PROFILE_SET:
        return None
    limit_bytes = _in_flight_limit_bytes(budget)
    reservation_bytes = budget.memory_limit_bytes
    with _IN_FLIGHT_LOCK:
        reserved = sum(amount for _profile, amount, _operation in _IN_FLIGHT_RESERVATIONS.values())
        if reserved + reservation_bytes > limit_bytes:
            raise ExecutionAdmissionError(
                operation,
                profile=profile,
                memory_limit_bytes=budget.memory_limit_bytes,
                rss_at_admission_bytes=rss_at_admission_bytes,
                reason="in_flight_memory_budget_exceeded",
                process_rss_limit_bytes=budget.process_rss_limit_bytes,
                in_flight_reserved_bytes=reserved,
                in_flight_limit_bytes=limit_bytes,
            )
        reservation_id = next(_IN_FLIGHT_COUNTER)
        _IN_FLIGHT_RESERVATIONS[reservation_id] = (
            profile,
            reservation_bytes,
            operation,
        )

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


def _process_rss_env_candidates(profile: ExecutionProfile) -> tuple[tuple[str, int], ...]:
    profile_bytes, profile_mb = _PROFILE_PROCESS_RSS_ENV[profile]
    global_bytes, global_mb = _GLOBAL_PROCESS_RSS_ENV
    return (
        (profile_bytes, 1),
        (profile_mb, _MIB),
        (global_bytes, 1),
        (global_mb, _MIB),
    )
