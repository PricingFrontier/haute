"""Execution admission: memory budgets, grants, waits and process caps.

Memory sampling enforces the budget, adaptive and explicit budgets per engine profile come from the
environment and available RAM, growth grants and training admission wait for or refuse competing
work, isolated contexts reserve their parent's headroom once, the absolute process RSS cap refuses
work above it, and an in-flight refusal names its holders.
"""

import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_admitted_execution_context,
    create_isolated_execution_context,
    execution_budget_for_profile,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionAdmission,
    ExecutionCancellationToken,
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._pipeline_settings import PipelineSettings
from tests._execution_context_helpers import (
    _clear_execution_memory_env,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def test_remaining_memory_bytes_samples_once_and_enforces_the_budget() -> None:
    samples = Mock(return_value=75)
    context = ExecutionContext(
        operation="remaining",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=100,
        memory_sampler=samples,
    )

    assert context.remaining_memory_bytes() == 25
    samples.assert_called_once_with()


def test_remaining_memory_bytes_preserves_memory_sampler_failures() -> None:
    unavailable = ExecutionContext(
        operation="remaining-unavailable",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=100,
        memory_sampler=lambda: None,
    )
    over_budget = ExecutionContext(
        operation="remaining-over-budget",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=100,
        memory_sampler=lambda: 101,
    )

    with pytest.raises(ExecutionMemoryLimitExceededError, match="sampler became unavailable"):
        unavailable.remaining_memory_bytes()
    with pytest.raises(ExecutionMemoryLimitExceededError, match="exceeded its memory budget"):
        over_budget.remaining_memory_bytes()


def test_current_rss_bytes_is_a_real_positive_int() -> None:
    from haute._execution_context import current_rss_bytes

    rss = current_rss_bytes()
    assert isinstance(rss, int)
    assert rss > 0


def test_current_rss_bytes_is_none_when_the_probe_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_context as context_mod

    monkeypatch.setattr(context_mod, "current_process_rss_bytes", lambda: None)
    assert context_mod.current_rss_bytes() is None


def test_execution_metrics_payload_includes_memory_budget() -> None:
    context = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=123_456,
        memory_sampler=lambda: 1_000,
    )

    with context.stage("collect", node_id="node-1"):
        pass

    payload = context.metrics_payload(status="completed")

    assert payload["memory_limit_bytes"] == 123_456


def test_execution_admission_policy_covers_every_engine_profile() -> None:
    from haute import _execution_admission as admission

    expected_profiles = set(ExecutionProfile)

    assert set(admission._ADAPTIVE_MEMORY_POLICY) == expected_profiles
    # A preview's budget is the pipeline settings' preview memory, never a variable.
    assert set(admission._PROFILE_MEMORY_ENV) == expected_profiles - {
        ExecutionProfile.PREVIEW_EAGER
    }
    assert set(admission._PROFILE_PROCESS_RSS_ENV) == expected_profiles
    assert ExecutionProfile.DEPLOY_LIVE not in admission._ADAPTIVE_LOCAL_PROFILES


def test_default_memory_budgets_adapt_to_available_ram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 20 * gib)

    for profile in (
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.DEPLOY_BATCH,
    ):
        budget = execution_budget_for_profile(profile)
        assert budget.config_key == f"adaptive:{profile.value}"
        assert budget.budget_policy == "adaptive_local"
        assert budget.available_ram_bytes == 20 * gib
        assert budget.os_reserve_bytes == 2 * gib
        assert budget.memory_limit_bytes > 0

    live_budget = execution_budget_for_profile(ExecutionProfile.DEPLOY_LIVE)
    assert live_budget.memory_limit_bytes == gib
    assert live_budget.config_key == "default:deploy_live"
    assert live_budget.budget_policy == "fixed_default"


@pytest.mark.parametrize(
    "profile",
    [ExecutionProfile.PREVIEW_EAGER, ExecutionProfile.LAZY_SINK],
)
def test_adaptive_admission_fails_when_physical_ram_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    profile: ExecutionProfile,
) -> None:
    """Neither strict nor non-strict local profiles invent host capacity."""
    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: None)

    with pytest.raises(RuntimeError, match="physical RAM is unavailable"):
        execution_budget_for_profile(profile)


@pytest.mark.parametrize(
    "profile",
    [ExecutionProfile.PREVIEW_EAGER, ExecutionProfile.LAZY_SINK],
)
def test_adaptive_admission_reports_exhaustion_when_headroom_is_zero(
    monkeypatch: pytest.MonkeyPatch,
    profile: ExecutionProfile,
) -> None:
    """An observed zero is an exhausted limit, not unobservable RAM."""
    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 0)

    with pytest.raises(RuntimeError, match="available memory is exhausted"):
        execution_budget_for_profile(profile)


def test_in_flight_limit_reports_exhaustion_when_probe_observes_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit-env budget's in-flight check still refuses observed-zero RAM.

    This is exactly where the old None/zero conflation lived: the budget
    carries no available-RAM figure, the live probe answers zero, and the
    reservation must report exhaustion — not "physical RAM is unavailable".
    """
    from haute._execution_admission import create_admitted_execution_context

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setenv("HAUTE_OPTIMISER_MEMORY_LIMIT_MB", "512")
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 0)

    with pytest.raises(RuntimeError, match="available memory is exhausted"):
        create_admitted_execution_context(
            operation="optimiser_setup",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            memory_sampler=lambda: 100,
        )


def test_adaptive_default_memory_budgets_never_exceed_available_ram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_execution_memory_env(monkeypatch)
    available = 384 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: available)

    for profile in set(ExecutionProfile) - {ExecutionProfile.DEPLOY_LIVE}:
        budget = execution_budget_for_profile(profile)
        assert 0 < budget.memory_limit_bytes <= available


def test_adaptive_default_memory_budgets_honor_configured_os_reserve(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_settings,
) -> None:
    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 20 * gib)

    default_budget = execution_budget_for_profile(ExecutionProfile.EXPLORE_ANALYSIS)
    pipeline_settings(kept_free_gb=6)
    reserved_budget = execution_budget_for_profile(ExecutionProfile.EXPLORE_ANALYSIS)

    assert default_budget.os_reserve_bytes == 2 * gib
    assert reserved_budget.os_reserve_bytes == 6 * gib
    assert reserved_budget.memory_limit_bytes < default_budget.memory_limit_bytes


def test_explicit_profile_memory_cap_remains_hard_when_default_would_be_larger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 64 * gib)
    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", "1024")

    budget = execution_budget_for_profile(ExecutionProfile.TRAINING_PREP)

    assert budget.memory_limit_bytes == gib
    assert budget.config_key == "HAUTE_TRAINING_MEMORY_LIMIT_MB"


def test_explicit_global_memory_cap_remains_hard_for_all_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 64 * gib)
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_LIMIT_MB", "768")

    for profile in set(ExecutionProfile) - {ExecutionProfile.PREVIEW_EAGER}:
        budget = execution_budget_for_profile(profile)
        assert budget.memory_limit_bytes == 768 * 1024 * 1024
        assert budget.config_key == "HAUTE_EXECUTION_MEMORY_LIMIT_MB"
    # Previews are sized by the pipeline settings alone.
    preview = execution_budget_for_profile(ExecutionProfile.PREVIEW_EAGER)
    assert preview.budget_policy == "adaptive_local"


@pytest.mark.parametrize(
    ("env_name", "resolver", "expected"),
    [
        (
            "HAUTE_SINK_MEMORY_LIMIT_BYTES",
            lambda module: module._resolve_required_budget(
                ExecutionProfile.LAZY_SINK, PipelineSettings()
            ),
            7,
        ),
        (
            "HAUTE_PREVIEW_PROCESS_RSS_LIMIT_BYTES",
            lambda module: module._resolve_optional_rss_limit(ExecutionProfile.PREVIEW_EAGER),
            (7, "HAUTE_PREVIEW_PROCESS_RSS_LIMIT_BYTES"),
        ),
    ],
)
def test_admission_numeric_env_is_read_once(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    resolver,
    expected,
) -> None:
    """A concurrent env mutation cannot race a presence check and second read."""
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)

    class SingleReadEnvironment(dict):
        def __init__(self) -> None:
            super().__init__(admission_mod.os.environ)
            self[env_name] = "7"
            self.reads = 0

        def get(self, key, default=None):
            if key == env_name:
                self.reads += 1
            return super().get(key, default)

        def __getitem__(self, key):
            if key == env_name and self.reads:
                raise AssertionError(f"{env_name} was read more than once")
            return super().__getitem__(key)

    environment = SingleReadEnvironment()
    monkeypatch.setattr(admission_mod.os, "environ", environment)

    resolved = resolver(admission_mod)

    if hasattr(resolved, "memory_limit_bytes"):
        resolved = resolved.memory_limit_bytes
    assert resolved == expected
    assert environment.reads == 1


@pytest.mark.parametrize(
    ("invalid_key", "lower_precedence_key", "resolver"),
    [
        (
            "HAUTE_SINK_MEMORY_LIMIT_BYTES",
            "HAUTE_SINK_MEMORY_LIMIT_MB",
            lambda module: module._resolve_required_budget(
                ExecutionProfile.LAZY_SINK, PipelineSettings()
            ),
        ),
        (
            "HAUTE_PREVIEW_PROCESS_RSS_LIMIT_BYTES",
            "HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB",
            lambda module: module._resolve_optional_rss_limit(ExecutionProfile.PREVIEW_EAGER),
        ),
    ],
)
@pytest.mark.parametrize("invalid_value", ["", "0"])
def test_invalid_highest_precedence_admission_env_does_not_fall_through(
    monkeypatch: pytest.MonkeyPatch,
    invalid_key: str,
    lower_precedence_key: str,
    resolver,
    invalid_value: str,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setenv(invalid_key, invalid_value)
    monkeypatch.setenv(lower_precedence_key, "3")

    with pytest.raises(RuntimeError, match=invalid_key):
        resolver(admission_mod)


@pytest.mark.parametrize(
    ("env_name", "resolver", "expected"),
    [
        (
            "HAUTE_SINK_MEMORY_LIMIT_MB",
            lambda module: (
                module._resolve_required_budget(
                    ExecutionProfile.LAZY_SINK, PipelineSettings()
                ).memory_limit_bytes
            ),
            3 * 1024 * 1024,
        ),
        (
            "HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB",
            lambda module: module._resolve_optional_rss_limit(ExecutionProfile.PREVIEW_EAGER)[0],
            3 * 1024 * 1024,
        ),
    ],
)
def test_admission_mb_env_applies_unit_after_shared_parse(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    resolver,
    expected: int,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setenv(env_name, "3")

    assert resolver(admission_mod) == expected


def test_default_execution_budget_is_adaptive_across_local_engine_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 16 * gib)

    heavy_profiles = {
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.DEPLOY_BATCH,
    }
    for profile in heavy_profiles:
        budget = admission_mod.execution_budget_for_profile(profile)
        assert budget.budget_policy == "adaptive_local"
        assert budget.config_key == f"adaptive:{profile.value}"
        assert budget.available_ram_bytes == 16 * gib
        assert budget.memory_limit_bytes > admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[profile]

    preview_budget = admission_mod.execution_budget_for_profile(ExecutionProfile.PREVIEW_EAGER)
    assert preview_budget.budget_policy == "adaptive_local"
    assert (
        preview_budget.memory_limit_bytes
        > admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[ExecutionProfile.PREVIEW_EAGER]
    )

    deploy_live_budget = admission_mod.execution_budget_for_profile(ExecutionProfile.DEPLOY_LIVE)
    assert deploy_live_budget.budget_policy == "fixed_default"
    assert (
        deploy_live_budget.memory_limit_bytes
        == admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[ExecutionProfile.DEPLOY_LIVE]
    )


def test_explicit_memory_limit_env_still_overrides_adaptive_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 64 * 1024**3)
    monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_MB", "512")

    budget = admission_mod.execution_budget_for_profile(ExecutionProfile.EXPLORE_ANALYSIS)

    assert budget.memory_limit_bytes == 512 * 1024 * 1024
    assert budget.config_key == "HAUTE_EXPLORE_MEMORY_LIMIT_MB"
    assert budget.budget_policy == "explicit_env"
    assert budget.available_ram_bytes is None


def test_heavy_execution_admission_counts_in_flight_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 10 * gib)

    first = create_admitted_execution_context(
        operation="optimiser_setup_a",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        memory_sampler=lambda: 100,
    )
    try:
        with pytest.raises(ExecutionAdmissionError) as exc_info:
            create_admitted_execution_context(
                operation="optimiser_setup_b",
                profile=ExecutionProfile.OPTIMISER_SETUP,
                memory_sampler=lambda: 100,
            )
        assert exc_info.value.reason == "in_flight_memory_budget_exceeded"
        # The refusal names the work it lost to, not just the byte totals.
        assert exc_info.value.in_flight_operations == ("optimiser_setup:optimiser_setup_a",)
        assert exc_info.value.to_payload()["in_flight_operations"] == [
            "optimiser_setup:optimiser_setup_a"
        ]
        from haute.routes._memory_messages import memory_limit_user_message

        message = memory_limit_user_message(exc_info.value, operation_noun="Optimiser estimate")
        assert "reserved by optimiser_setup:optimiser_setup_a" in message

        first.release_admission()

        second = create_admitted_execution_context(
            operation="optimiser_setup_b",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            memory_sampler=lambda: 100,
        )
        second.release_admission()
    finally:
        first.release_admission()


_PREVIEW_HOLDER = "training_prep:training_evaluation_preview"


def _pin_ten_gib_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 10 * gib)


def test_training_admission_waits_out_an_evaluation_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    preview = create_admitted_execution_context(
        operation="training_evaluation_preview",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    releaser = threading.Timer(0.2, preview.release_admission)
    started = time.monotonic()
    releaser.start()
    try:
        training = create_admitted_execution_context(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
            memory_sampler=lambda: 100,
            wait_out_holders={_PREVIEW_HOLDER},
            wait_seconds=10.0,
        )
    finally:
        releaser.join()
        preview.release_admission()
    waited = time.monotonic() - started
    training.release_admission()
    # Admitted by the release notification, well before the wait bound.
    assert 0.15 <= waited < 5.0


_GIB = 1024 * 1024 * 1024


def _grant(**kwargs: Any) -> ExecutionContext:
    from haute._execution_admission import admit_growth_grant

    return admit_growth_grant(
        operation=kwargs.pop("operation", "optimiser_solve"),
        profile=ExecutionProfile.OPTIMISER_SOLVE,
        memory_sampler=kwargs.pop("memory_sampler", lambda: 100),
        **kwargs,
    )


def test_a_growth_grant_waits_for_the_estimate_then_sees_what_it_freed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context
    from haute.routes._optimiser_session import ESTIMATE_HOLDERS

    _pin_ten_gib_host(monkeypatch)
    estimate = create_admitted_execution_context(
        operation="optimiser_estimate",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        memory_sampler=lambda: 100,
    )
    releaser = threading.Timer(0.2, estimate.release_admission)
    started = time.monotonic()
    releaser.start()
    try:
        grant = _grant(wait_out_holders=ESTIMATE_HOLDERS, wait_seconds=10.0)
    finally:
        releaser.join()
        estimate.release_admission()
    try:
        assert 0.15 <= time.monotonic() - started < 5.0
        # Sampled after the estimate released: the whole usable machine, 10 GiB
        # less the 2 GiB OS reserve.
        assert grant.memory_limit_bytes == 8 * _GIB
        assert grant.admission is not None
        assert grant.admission.headroom_bytes == 8 * _GIB
    finally:
        grant.release_admission()


def test_a_competing_reservation_shrinks_the_grant_instead_of_refusing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", str(3 * 1024))
    training = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    try:
        grant = _grant()
        try:
            assert grant.memory_limit_bytes == 5 * _GIB
        finally:
            grant.release_admission()
    finally:
        training.release_admission()


def test_any_positive_grant_is_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no minimum: whether a small grant suffices is for execution to find out."""
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_BYTES", str(8 * _GIB - 8 * 1024 * 1024))
    training = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    try:
        grant = _grant()
        try:
            assert grant.memory_limit_bytes == 8 * 1024 * 1024
        finally:
            grant.release_admission()
    finally:
        training.release_admission()


def test_a_grant_of_nothing_refuses_with_the_in_flight_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_BYTES", str(8 * _GIB))
    training = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    try:
        with pytest.raises(ExecutionAdmissionError) as refused:
            _grant()
        assert refused.value.reason == "in_flight_memory_budget_exceeded"
        assert refused.value.in_flight_operations == ("training_prep:training_pipeline",)
    finally:
        training.release_admission()


def test_an_absolute_process_rss_limit_caps_the_grant_and_names_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ten_gib_host(monkeypatch)
    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_PROCESS_RSS_LIMIT_BYTES", str(1 * _GIB))
    grant = _grant(memory_sampler=lambda: 256 * 1024 * 1024)
    try:
        assert grant.memory_limit_bytes == 768 * 1024 * 1024
    finally:
        grant.release_admission()
    with pytest.raises(ExecutionAdmissionError) as refused:
        _grant(memory_sampler=lambda: 2 * _GIB)
    assert refused.value.reason == "process_rss_limit_exceeded"


def _preview(**kwargs: Any) -> int:
    from haute._execution_admission import preview_growth_grant_bytes

    return preview_growth_grant_bytes(
        ExecutionProfile.OPTIMISER_SOLVE,
        operation="optimiser_estimate",
        memory_sampler=kwargs.pop("memory_sampler", lambda: 100),
        **kwargs,
    )


def test_the_grant_preview_is_what_a_grant_would_get_and_reserves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import (
        _IN_FLIGHT_RESERVATIONS,
        create_admitted_execution_context,
    )

    _pin_ten_gib_host(monkeypatch)
    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", str(3 * 1024))
    training = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    try:
        held = dict(_IN_FLIGHT_RESERVATIONS)
        assert _preview() == 5 * _GIB
        assert _preview() == 5 * _GIB
        assert dict(_IN_FLIGHT_RESERVATIONS) == held
        grant = _grant()
        try:
            assert grant.memory_limit_bytes == 5 * _GIB
        finally:
            grant.release_admission()
    finally:
        training.release_admission()
    assert _preview() == 8 * _GIB


def test_the_grant_preview_is_zero_where_a_grant_would_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_BYTES", str(8 * _GIB))
    training = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    try:
        assert _preview() == 0
        with pytest.raises(ExecutionAdmissionError):
            _grant()
    finally:
        training.release_admission()
    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_PROCESS_RSS_LIMIT_BYTES", str(1 * _GIB))
    assert _preview(memory_sampler=lambda: 256 * 1024 * 1024) == 768 * 1024 * 1024
    assert _preview(memory_sampler=lambda: 2 * _GIB) == 0


def test_the_solve_profile_takes_the_whole_usable_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ten_gib_host(monkeypatch)
    assert execution_budget_for_profile(ExecutionProfile.OPTIMISER_SOLVE).memory_limit_bytes == (
        8 * _GIB
    )
    assert execution_budget_for_profile(ExecutionProfile.OPTIMISER_SETUP).memory_limit_bytes == (
        6 * _GIB
    )
    monkeypatch.setenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB", "512")
    assert execution_budget_for_profile(ExecutionProfile.OPTIMISER_SOLVE).memory_limit_bytes == (
        512 * 1024 * 1024
    )
    monkeypatch.delenv("HAUTE_OPTIMISER_SOLVE_MEMORY_LIMIT_MB")
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_POLICY", "fixed")
    assert execution_budget_for_profile(ExecutionProfile.OPTIMISER_SOLVE).memory_limit_bytes == (
        4 * _GIB
    )


def test_training_admission_keeps_waiting_when_a_preview_takes_the_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    original_wait = admission_mod._wait_out_in_flight_holders
    interlopers: list[ExecutionContext] = []
    releasers: list[threading.Timer] = []

    def wait_then_lose_the_race(*args: object, **kwargs: object) -> None:
        original_wait(*args, **kwargs)  # type: ignore[arg-type]
        if not interlopers:
            # Another preview reserves between the wait and the reservation.
            interloper = create_admitted_execution_context(
                operation="training_evaluation_preview",
                profile=ExecutionProfile.TRAINING_PREP,
                memory_sampler=lambda: 100,
            )
            interlopers.append(interloper)
            releaser = threading.Timer(0.2, interloper.release_admission)
            releasers.append(releaser)
            releaser.start()

    monkeypatch.setattr(admission_mod, "_wait_out_in_flight_holders", wait_then_lose_the_race)
    try:
        training = create_admitted_execution_context(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
            memory_sampler=lambda: 100,
            wait_out_holders={_PREVIEW_HOLDER},
            wait_seconds=10.0,
        )
        training.release_admission()
    finally:
        for releaser in releasers:
            releaser.join()
        for interloper in interlopers:
            interloper.release_admission()
    assert len(interlopers) == 1


def test_work_estimates_reserve_only_their_own_bytes_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two bounded operations fit side by side where two whole budgets would not."""
    from haute._execution_admission import WorkEstimate, create_admitted_execution_context

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 10 * gib)

    def admit(name: str) -> ExecutionContext:
        return create_admitted_execution_context(
            operation=name,
            profile=ExecutionProfile.EXPLORE_ANALYSIS,
            memory_sampler=lambda: 100,
            estimate=WorkEstimate(estimated_bytes=2 * gib, subject=name, remedy="Ask for less."),
        )

    first, second = admit("query_a"), admit("query_b")
    try:
        with pytest.raises(ExecutionAdmissionError) as whole_budget:
            create_admitted_execution_context(
                operation="whole_budget",
                profile=ExecutionProfile.EXPLORE_ANALYSIS,
                memory_sampler=lambda: 100,
            )
        assert whole_budget.value.reason == "in_flight_memory_budget_exceeded"
        # The context still enforces the profile's whole budget as its RSS limit.
        assert first.memory_limit_bytes is not None and first.memory_limit_bytes > 2 * gib
    finally:
        first.release_admission()
        second.release_admission()


def test_an_estimate_above_the_allowance_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import WorkEstimate, create_admitted_execution_context

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_BYTES", str(1000))

    with pytest.raises(ExecutionAdmissionError) as refused:
        create_admitted_execution_context(
            operation="big_query",
            profile=ExecutionProfile.EXPLORE_ANALYSIS,
            memory_sampler=lambda: 100,
            estimate=WorkEstimate(
                estimated_bytes=1001, subject="The big query", remedy="Ask for fewer rows."
            ),
        )
    assert refused.value.reason == (
        "The big query needs an estimated 1001 bytes; the explore_analysis allowance is "
        "1000 bytes. Ask for fewer rows."
    )
    # At the allowance it is admitted.
    admitted = create_admitted_execution_context(
        operation="fitting_query",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        memory_sampler=lambda: 100,
        estimate=WorkEstimate(estimated_bytes=1000, subject="The query", remedy="n/a"),
    )
    admitted.release_admission()


def test_training_admission_retries_when_the_preview_releases_after_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    original_admit = admission_mod._admit_once
    attempts: list[str] = []

    def refused_by_a_preview_that_then_releases(**kwargs: Any) -> ExecutionContext:
        attempts.append(kwargs["operation"])
        if len(attempts) == 1:
            # The reservation lost to a preview that has released by the time
            # admission decides whether to keep waiting: no holders remain.
            raise ExecutionAdmissionError(
                kwargs["operation"],
                profile=kwargs["profile"],
                memory_limit_bytes=kwargs["budget"].memory_limit_bytes,
                rss_at_admission_bytes=100,
                reason="in_flight_memory_budget_exceeded",
                in_flight_operations=(_PREVIEW_HOLDER,),
            )
        return original_admit(**kwargs)

    monkeypatch.setattr(admission_mod, "_admit_once", refused_by_a_preview_that_then_releases)
    training = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
        wait_out_holders={_PREVIEW_HOLDER},
        wait_seconds=10.0,
    )
    training.release_admission()
    assert attempts == ["training_pipeline", "training_pipeline"]


def test_training_admission_does_not_wait_for_a_budget_it_can_never_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", str(64 * 1024))
    started = time.monotonic()
    with pytest.raises(ExecutionAdmissionError) as exc_info:
        create_admitted_execution_context(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
            memory_sampler=lambda: 100,
            wait_out_holders={_PREVIEW_HOLDER},
            wait_seconds=10.0,
        )
    assert time.monotonic() - started < 1.0
    assert exc_info.value.reason == "in_flight_memory_budget_exceeded"


def test_training_admission_refuses_at_once_behind_other_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    running = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    try:
        started = time.monotonic()
        with pytest.raises(ExecutionAdmissionError) as exc_info:
            create_admitted_execution_context(
                operation="training_pipeline",
                profile=ExecutionProfile.TRAINING_PREP,
                memory_sampler=lambda: 100,
                wait_out_holders={_PREVIEW_HOLDER},
                wait_seconds=10.0,
            )
        assert time.monotonic() - started < 1.0
        assert exc_info.value.reason == "in_flight_memory_budget_exceeded"
        assert exc_info.value.in_flight_operations == ("training_prep:training_pipeline",)
    finally:
        running.release_admission()


def test_training_admission_refuses_when_a_preview_outlasts_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    preview = create_admitted_execution_context(
        operation="training_evaluation_preview",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    try:
        started = time.monotonic()
        with pytest.raises(ExecutionAdmissionError) as exc_info:
            create_admitted_execution_context(
                operation="training_pipeline",
                profile=ExecutionProfile.TRAINING_PREP,
                memory_sampler=lambda: 100,
                wait_out_holders={_PREVIEW_HOLDER},
                wait_seconds=0.3,
            )
        assert time.monotonic() - started >= 0.3
        assert exc_info.value.in_flight_operations == (_PREVIEW_HOLDER,)
    finally:
        preview.release_admission()


def test_waiting_training_admission_honours_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _pin_ten_gib_host(monkeypatch)
    preview = create_admitted_execution_context(
        operation="training_evaluation_preview",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_sampler=lambda: 100,
    )
    token = ExecutionCancellationToken()
    canceller = threading.Timer(0.2, token.cancel)
    canceller.start()
    try:
        started = time.monotonic()
        with pytest.raises(ExecutionCancelledError):
            create_admitted_execution_context(
                operation="training_pipeline",
                profile=ExecutionProfile.TRAINING_PREP,
                memory_sampler=lambda: 100,
                cancellation_token=token,
                wait_out_holders={_PREVIEW_HOLDER},
                wait_seconds=10.0,
            )
        assert time.monotonic() - started < 2.0
    finally:
        canceller.join()
        preview.release_admission()


def test_heavy_admission_releases_reservation_when_context_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr(admission_mod, "available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 10 * gib)

    real_context = admission_mod.ExecutionContext

    def fail_context(**_kwargs):
        raise RuntimeError("context construction failed")

    monkeypatch.setattr(admission_mod, "ExecutionContext", fail_context)
    with pytest.raises(RuntimeError, match="context construction failed"):
        admission_mod.create_admitted_execution_context(
            operation="optimiser_setup_failed",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            memory_sampler=lambda: 100,
        )

    assert admission_mod._IN_FLIGHT_RESERVATIONS == {}
    monkeypatch.setattr(admission_mod, "ExecutionContext", real_context)
    context = admission_mod.create_admitted_execution_context(
        operation="optimiser_setup_after_failure",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        memory_sampler=lambda: 100,
    )
    context.release_admission()


def test_preview_execution_admission_does_not_reserve_heavy_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import create_admitted_execution_context

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 10 * gib)

    previews = [
        create_admitted_execution_context(
            operation=f"preview_{idx}",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_sampler=lambda: 100,
        )
        for idx in range(4)
    ]
    for context in previews:
        context.release_admission()


def test_fixed_memory_policy_uses_profile_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 64 * 1024**3)
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_POLICY", "fixed")

    budget = admission_mod.execution_budget_for_profile(ExecutionProfile.EXPLORE_ANALYSIS)

    assert (
        budget.memory_limit_bytes
        == admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[ExecutionProfile.EXPLORE_ANALYSIS]
    )
    assert budget.config_key == "default:explore_analysis"
    assert budget.budget_policy == "fixed_default"
    assert budget.available_ram_bytes is None


def test_strict_server_memory_policy_uses_profile_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _execution_admission as admission_mod

    _clear_execution_memory_env(monkeypatch)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 64 * 1024**3)
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_POLICY", "strict_server")

    budget = admission_mod.execution_budget_for_profile(ExecutionProfile.EXPLORE_ANALYSIS)

    assert (
        budget.memory_limit_bytes
        == admission_mod._DEFAULT_MEMORY_LIMIT_BYTES[ExecutionProfile.EXPLORE_ANALYSIS]
    )
    assert budget.config_key == "default:explore_analysis"
    assert budget.budget_policy == "fixed_default"
    assert budget.available_ram_bytes is None


def test_adaptive_budget_still_respects_process_rss_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_execution_memory_env(monkeypatch)
    mib = 1024 * 1024
    gib = 1024 * mib
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 16 * gib)
    monkeypatch.setenv("HAUTE_EXPLORE_PROCESS_RSS_LIMIT_MB", "2048")
    samples = iter([1500 * mib, 2100 * mib])

    context = create_admitted_execution_context(
        operation="explore_analysis",
        profile=ExecutionProfile.EXPLORE_ANALYSIS,
        memory_sampler=lambda: next(samples),
    )

    assert context.admission is not None
    assert context.admission.budget_policy == "adaptive_local"
    assert context.rss_limit_bytes == 2048 * mib
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="over-process-cap")
    assert exc_info.value.reason == "process_rss_limit_exceeded"


def test_admitted_execution_context_uses_profile_specific_memory_limit(
    monkeypatch, pipeline_settings
) -> None:
    pipeline_settings(preview_memory_gb=512 / 1024)

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 128 * 1024 * 1024,
    )

    assert context.memory_limit_bytes == 512 * 1024 * 1024
    assert context.admission is not None
    assert context.admission.admitted
    assert context.admission.profile == ExecutionProfile.PREVIEW_EAGER
    assert context.admission.memory_limit_bytes == 512 * 1024 * 1024
    assert context.admission.rss_at_admission_bytes == 128 * 1024 * 1024
    assert context.budget.memory_baseline_bytes == 128 * 1024 * 1024
    assert context.rss_limit_bytes == 640 * 1024 * 1024
    assert context.admission.rss_limit_bytes == 640 * 1024 * 1024
    assert context.admission.headroom_bytes == 512 * 1024 * 1024
    assert context.admission.config_key == "preview_memory_gb"


def test_isolated_context_uses_plain_parent_budget_without_reserving_twice(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_settings,
) -> None:
    import haute._execution_admission as admission_mod

    pipeline_settings(preview_memory_gb=64 / 1024)
    parent = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 100,
    )
    budget = isolated_execution_budget(parent)
    monkeypatch.setattr(admission_mod, "current_rss_bytes", lambda: 200)

    child = create_isolated_execution_context(budget)

    assert budget.memory_limit_bytes == 64 * 1024 * 1024
    assert child.budget.memory_baseline_bytes == 200
    assert child.rss_limit_bytes == 200 + 64 * 1024 * 1024
    assert child.admission is not None
    assert child.admission.operation == "pipeline_preview"
    assert child.lease.admission_release is None
    child.release_admission()
    parent.release_admission()


def test_isolated_context_requires_admitted_parent_and_child_rss_sampler(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_settings,
) -> None:
    import haute._execution_admission as admission_mod

    with pytest.raises(ValueError, match="admitted parent context"):
        isolated_execution_budget(
            ExecutionContext(
                operation="pipeline_preview",
                profile=ExecutionProfile.PREVIEW_EAGER,
                memory_limit_bytes=1,
            )
        )

    pipeline_settings(preview_memory_gb=64 / 1024)
    parent = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 100,
    )
    budget = isolated_execution_budget(parent)
    monkeypatch.setattr(admission_mod, "current_rss_bytes", lambda: None)
    try:
        with pytest.raises(ExecutionAdmissionError) as exc_info:
            create_isolated_execution_context(budget)
    finally:
        parent.release_admission()

    assert exc_info.value.reason == "memory_sampler_unavailable"


@pytest.mark.parametrize("memory_limit", [0, -1, True, 1.5])
def test_isolated_budget_rejects_invalid_memory_limit(memory_limit: object) -> None:
    with pytest.raises(ValueError, match="memory_limit_bytes"):
        IsolatedExecutionBudget(
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=memory_limit,  # type: ignore[arg-type]
            config_key="test",
            budget_policy="fixed_default",
        )


@pytest.mark.parametrize("process_limit", [0, -1, True, 1.5])
def test_isolated_budget_rejects_invalid_optional_process_limit(process_limit: object) -> None:
    with pytest.raises(ValueError, match="process_rss_limit_bytes"):
        IsolatedExecutionBudget(
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=1,
            process_rss_limit_bytes=process_limit,  # type: ignore[arg-type]
            config_key="test",
            budget_policy="fixed_default",
        )


@pytest.mark.parametrize("headroom", [None, 0, -1, True])
def test_isolated_budget_requires_positive_admitted_headroom(headroom: object) -> None:
    parent = ExecutionContext(
        operation="preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission=ExecutionAdmission(
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=10,
            rss_at_admission_bytes=1,
            rss_limit_bytes=11,
            headroom_bytes=headroom,  # type: ignore[arg-type]
            config_key="test",
        ),
    )

    with pytest.raises(ValueError, match="positive admitted memory headroom"):
        isolated_execution_budget(parent)


def test_create_isolated_context_rejects_the_wrong_budget_type() -> None:
    with pytest.raises(TypeError, match="IsolatedExecutionBudget"):
        create_isolated_execution_context(object())  # type: ignore[arg-type]


def test_admitted_context_rejects_an_unavailable_initial_sampler() -> None:
    with pytest.raises(ExecutionAdmissionError) as exc_info:
        create_admitted_execution_context(
            operation="preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_sampler=lambda: None,
        )

    assert exc_info.value.reason == "memory_sampler_unavailable"


def test_invalid_execution_memory_policy_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_POLICY", "guess")

    with pytest.raises(RuntimeError, match="HAUTE_EXECUTION_MEMORY_POLICY"):
        execution_budget_for_profile(ExecutionProfile.PREVIEW_EAGER)


def test_terminal_calibration_ignores_invalid_positive_evidence() -> None:
    context = ExecutionContext(operation="preview", profile=ExecutionProfile.PREVIEW_EAGER)
    diagnostic = SimpleNamespace(strategy=SimpleNamespace(value="materialisation-boundary"))

    context.evidence.record_estimate_calibration(
        {
            "status": "completed",
            "raw_estimated_bytes": True,
            "observed_peak_rss_growth_bytes": 1,
        },
        profile=context.profile,
        diagnostic=diagnostic,
    )

    assert context.evidence._calibration_recorded is False


def test_isolated_context_uses_admitted_headroom_and_absolute_process_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child cannot reacquire the profile's wider nominal allowance."""

    import haute._execution_admission as admission_mod

    parent = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=100,
        admission=ExecutionAdmission(
            operation="pipeline_preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=100,
            rss_at_admission_bytes=100,
            rss_limit_bytes=125,
            process_rss_limit_bytes=125,
            headroom_bytes=25,
            config_key="test",
        ),
    )
    budget = isolated_execution_budget(parent)
    monkeypatch.setattr(admission_mod, "current_rss_bytes", lambda: 110)

    child = create_isolated_execution_context(budget)

    assert budget.memory_limit_bytes == 25
    assert budget.process_rss_limit_bytes == 125
    assert child.memory_limit_bytes == 25
    assert child.budget.memory_baseline_bytes == 110
    assert child.rss_limit_bytes == 125
    assert child.admission is not None
    assert child.admission.headroom_bytes == 15


def test_isolated_context_rejects_child_already_above_absolute_process_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._execution_admission as admission_mod

    parent = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=100,
        admission=ExecutionAdmission(
            operation="pipeline_preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=100,
            rss_at_admission_bytes=100,
            rss_limit_bytes=125,
            process_rss_limit_bytes=125,
            headroom_bytes=25,
            config_key="test",
        ),
    )
    budget = isolated_execution_budget(parent)
    monkeypatch.setattr(admission_mod, "current_rss_bytes", lambda: 126)

    with pytest.raises(ExecutionAdmissionError) as exc_info:
        create_isolated_execution_context(budget)

    assert exc_info.value.reason == "process_rss_limit_exceeded"
    assert exc_info.value.process_rss_limit_bytes == 125


def test_isolated_context_rejects_child_with_no_absolute_cap_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._execution_admission as admission_mod

    parent = ExecutionContext(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        admission=ExecutionAdmission(
            operation="pipeline_preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_limit_bytes=100,
            rss_at_admission_bytes=100,
            rss_limit_bytes=125,
            process_rss_limit_bytes=125,
            headroom_bytes=25,
            config_key="test",
        ),
    )
    budget = isolated_execution_budget(parent)
    monkeypatch.setattr(admission_mod, "current_rss_bytes", lambda: 125)

    with pytest.raises(ExecutionAdmissionError) as exc_info:
        create_isolated_execution_context(budget)

    assert exc_info.value.reason == "process_rss_limit_exceeded"
    assert exc_info.value.process_rss_limit_bytes == 125


def test_admitted_execution_context_allows_warm_process_above_operation_budget(
    monkeypatch,
    pipeline_settings,
) -> None:
    pipeline_settings(preview_memory_gb=512 / 1024)
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    samples = iter([2 * gib, 2 * gib + 511 * mib, 2 * gib + 513 * mib])

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: next(samples),
    )

    assert context.budget.memory_baseline_bytes == 2 * gib
    assert context.memory_limit_bytes == 512 * mib
    assert context.rss_limit_bytes == 2 * gib + 512 * mib
    context.checkpoint(label="within-operation-growth-budget")
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="over-operation-growth-budget")
    assert exc_info.value.limit_bytes == 512 * mib
    assert exc_info.value.baseline_rss_bytes == 2 * gib
    assert exc_info.value.rss_limit_bytes == 2 * gib + 512 * mib


def test_admitted_execution_context_runtime_failure_reports_process_rss_cap(
    monkeypatch,
    pipeline_settings,
) -> None:
    pipeline_settings(preview_memory_gb=512 / 1024)
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB", "2304")
    gib = 1024 * 1024 * 1024
    mib = 1024 * 1024
    samples = iter([2 * gib, 2 * gib + 305 * mib])

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: next(samples),
    )

    assert context.rss_limit_bytes == 2304 * mib
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="over-process-cap")
    assert exc_info.value.reason == "process_rss_limit_exceeded"
    assert exc_info.value.limit_bytes == 512 * mib
    assert exc_info.value.rss_limit_bytes == 2304 * mib


def test_process_rss_cap_catches_cumulative_warm_process_ratcheting(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_settings,
) -> None:
    """A process cap bounds total RSS even when each operation gets fresh headroom."""
    pipeline_settings(preview_memory_gb=512 / 1024)
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_MB", "1024")
    mib = 1024 * 1024
    samples = iter([900 * mib, 1030 * mib])

    context = create_admitted_execution_context(
        operation="pipeline_preview",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: next(samples),
    )

    assert context.budget.memory_baseline_bytes == 900 * mib
    assert context.memory_limit_bytes == 512 * mib
    assert context.rss_limit_bytes == 1024 * mib
    with pytest.raises(ExecutionMemoryLimitExceededError) as exc_info:
        context.checkpoint(label="cumulative-rss-ratchet")
    assert exc_info.value.reason == "process_rss_limit_exceeded"
    assert exc_info.value.rss_limit_bytes == 1024 * mib


def test_admitted_execution_context_rejects_when_process_rss_cap_exceeded(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HAUTE_SINK_MEMORY_LIMIT_MB", "64")
    monkeypatch.setenv("HAUTE_SINK_PROCESS_RSS_LIMIT_MB", "128")

    with pytest.raises(ExecutionAdmissionError) as exc_info:
        create_admitted_execution_context(
            operation="pipeline_sink",
            profile=ExecutionProfile.LAZY_SINK,
            memory_sampler=lambda: 129 * 1024 * 1024,
        )

    assert exc_info.value.operation == "pipeline_sink"
    assert exc_info.value.profile == ExecutionProfile.LAZY_SINK
    assert exc_info.value.memory_limit_bytes == 64 * 1024 * 1024
    assert exc_info.value.rss_at_admission_bytes == 129 * 1024 * 1024
    assert exc_info.value.process_rss_limit_bytes == 128 * 1024 * 1024
    assert exc_info.value.to_payload() == {
        "error_code": "memory_limit",
        "operation": "pipeline_sink",
        "profile": "lazy_sink",
        "memory_limit_bytes": 64 * 1024 * 1024,
        "rss_at_admission_bytes": 129 * 1024 * 1024,
        "rss_limit_bytes": None,
        "process_rss_limit_bytes": 128 * 1024 * 1024,
        "headroom_bytes": -1 * 1024 * 1024,
        "reason": "process_rss_limit_exceeded",
    }


def test_admitted_execution_context_rejects_at_process_rss_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAUTE_PREVIEW_PROCESS_RSS_LIMIT_BYTES", "100")

    with pytest.raises(ExecutionAdmissionError) as exc_info:
        create_admitted_execution_context(
            operation="pipeline_preview",
            profile=ExecutionProfile.PREVIEW_EAGER,
            memory_sampler=lambda: 100,
        )

    assert exc_info.value.reason == "process_rss_limit_exceeded"
    assert exc_info.value.rss_at_admission_bytes == 100
    assert exc_info.value.process_rss_limit_bytes == 100


def test_in_flight_refusal_names_sorted_unique_holders_up_to_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Holders are ``profile:operation``, deduplicated, sorted, and capped at eight."""
    from haute._execution_admission import (
        _MAX_REPORTED_IN_FLIGHT_OPERATIONS,
        create_admitted_execution_context,
    )

    _clear_execution_memory_env(monkeypatch)
    gib = 1024 * 1024 * 1024
    monkeypatch.setattr("haute._execution_admission.available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setattr("haute._host_memory.available_ram_bytes", lambda: 10 * gib)
    monkeypatch.setenv("HAUTE_OPTIMISER_MEMORY_LIMIT_MB", "1")

    held = []
    # Ten distinct operations plus one duplicate name: eleven reservations, ten
    # distinct labels, of which only the first eight sorted labels are reported.
    operations = [f"setup_{index:02d}" for index in range(10)] + ["setup_00"]
    try:
        for operation in operations:
            held.append(
                create_admitted_execution_context(
                    operation=operation,
                    profile=ExecutionProfile.OPTIMISER_SETUP,
                    memory_sampler=lambda: 100,
                )
            )
        monkeypatch.setenv("HAUTE_TRAINING_MEMORY_LIMIT_MB", str(20 * 1024))
        with pytest.raises(ExecutionAdmissionError) as exc_info:
            create_admitted_execution_context(
                operation="train_prepare",
                profile=ExecutionProfile.TRAINING_PREP,
                memory_sampler=lambda: 100,
            )
    finally:
        for context in held:
            context.release_admission()

    error = exc_info.value
    assert error.reason == "in_flight_memory_budget_exceeded"
    assert error.in_flight_reserved_bytes == 11 * 1024 * 1024
    assert _MAX_REPORTED_IN_FLIGHT_OPERATIONS == 8
    assert error.in_flight_operations == tuple(
        f"optimiser_setup:setup_{index:02d}" for index in range(8)
    )
    assert error.to_payload()["in_flight_operations"] == list(error.in_flight_operations)
