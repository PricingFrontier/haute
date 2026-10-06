"""User-facing memory messages: certainty follows the evidence, sizes are named."""

from __future__ import annotations

from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionProfile
from haute.routes._memory_messages import memory_limit_user_message, solver_session_memory_message

_MIB = 1024 * 1024


def test_a_limiter_confirmed_death_is_worded_definitely() -> None:
    message = solver_session_memory_message(
        "cap_confirmed", cap_bytes=512 * _MIB, stage="building the quote grid"
    )
    assert "needed more than the 512.0 MiB of memory" in message
    assert "while building the quote grid" in message
    assert "most likely" not in message


def test_a_suspected_death_is_worded_as_the_likely_cause() -> None:
    message = solver_session_memory_message("suspected", cap_bytes=512 * _MIB, stage="solving")
    assert "most likely because it ran out of the 512.0 MiB of memory" in message
    assert "stopped unexpectedly while solving" in message


def test_a_watchdog_breach_names_what_it_was_given() -> None:
    message = solver_session_memory_message("watchdog", cap_bytes=2 * _MIB, stage="solving")
    assert "used more than the 2.0 MiB of memory it was given" in message
    assert "most likely" not in message


def test_a_sized_estimate_refusal_names_both_sizes() -> None:
    refused = ExecutionAdmissionError(
        "optimiser_solve",
        profile=ExecutionProfile.OPTIMISER_SETUP,
        memory_limit_bytes=1024 * _MIB,
        rss_at_admission_bytes=100,
        reason="resident optimiser grid needs an estimated 3 bytes; 2 bytes remain",
        estimated_bytes=13 * 1024 * _MIB,
        allowance_bytes=12 * 1024 * _MIB,
    )
    message = memory_limit_user_message(refused, operation_noun="Optimisation")
    assert message.startswith("Optimisation was not started: it needs an estimated 13.0 GiB")
    assert "and 12.0 GiB is available" in message
    assert refused.to_payload()["estimated_bytes"] == 13 * 1024 * _MIB


def test_no_memory_beyond_the_reserve_is_its_own_refusal() -> None:
    refused = ExecutionAdmissionError(
        "optimiser_solve",
        profile=ExecutionProfile.OPTIMISER_SOLVE,
        memory_limit_bytes=1,
        rss_at_admission_bytes=100,
        reason="no_memory_available",
    )
    message = memory_limit_user_message(refused, operation_noun="Optimisation")
    assert "no free memory beyond what is kept for the operating system" in message
