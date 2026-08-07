"""Shared user-facing wording for memory-limit failures.

Every route that terminates a job on :class:`ExecutionMemoryLimitExceededError`
or :class:`ExecutionAdmissionError` (training, dispersion, auto-range, the
input-snapshot build) authors its terminal message here, so the wording cannot
drift between surfaces. ``str(exc)`` on those exceptions names the internal
operation and raw byte counts — that text stays in diagnostic fields; these
shapes say what ran out of memory and what to do about it.
"""

from __future__ import annotations

from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionMemoryLimitExceededError


def format_byte_size(size_bytes: int) -> str:
    """Render a byte count in human units for user-facing messages."""
    value = float(size_bytes)
    unit = "bytes"
    for larger_unit in ("KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0:
            break
        value /= 1024.0
        unit = larger_unit
    return f"{size_bytes} bytes" if unit == "bytes" else f"{value:.1f} {unit}"


def _sizes_detail(used: int | None, allowed: int | None) -> str:
    # This runs on a failure path: a partially-populated exception must
    # degrade to omitting the sizes, never crash into a TypeError that
    # replaces the real memory error.
    if used is None or allowed is None:
        return ""
    return f" ({format_byte_size(used)} used, {format_byte_size(allowed)} allowed)"


def memory_limit_user_message(
    exc: ExecutionMemoryLimitExceededError | ExecutionAdmissionError,
    *,
    operation_noun: str,
) -> str:
    """Author the user-facing memory-limit message from structured attributes."""
    if isinstance(exc, ExecutionAdmissionError):
        # ``reason`` is an internal free-form string — branch on the known
        # values, and for anything else keep the generic refusal shape with no
        # sizes (the admission attributes are only known to be comparable for
        # the process-cap refusal).
        if exc.reason == "memory_sampler_unavailable":
            return (
                f"{operation_noun} was not started because the server could not "
                "measure its memory use. Try again; if this keeps happening, "
                "restart the app."
            )
        if exc.reason == "in_flight_memory_budget_exceeded":
            reserved = exc.in_flight_reserved_bytes
            allowed = exc.in_flight_limit_bytes
            if reserved is None or allowed is None:
                detail = ""
            else:
                detail = (
                    f" ({format_byte_size(reserved)} reserved, {format_byte_size(allowed)} allowed)"
                )
            return (
                f"{operation_noun} was not started because other running work has "
                f"already reserved the server's memory budget{detail}. Wait for "
                "other work to finish, then try again."
            )
        detail = ""
        if (
            exc.reason == "process_rss_limit_exceeded"
            and exc.rss_at_admission_bytes is not None
            and exc.process_rss_limit_bytes is not None
        ):
            # Not the "(X used, Y allowed)" runtime pair: nothing ran yet, so
            # the numbers are the SERVER's current use against its cap.
            detail = (
                f" (the server is already using "
                f"{format_byte_size(exc.rss_at_admission_bytes)} of its "
                f"{format_byte_size(exc.process_rss_limit_bytes)} memory cap)"
            )
        return (
            f"{operation_noun} was not started because the server does not have "
            f"enough free memory for it{detail}. Wait for other work to finish, "
            "reduce the data size, or run on a server with more memory, then "
            "try again."
        )
    if exc.reason == "memory_sampler_unavailable":
        return (
            f"{operation_noun} was stopped because the server could no longer "
            "measure its memory use. Try again; if this keeps happening, "
            "restart the app."
        )
    used = exc.rss_bytes
    allowed = exc.limit_bytes
    if exc.reason == "process_rss_limit_exceeded" and exc.rss_limit_bytes is not None:
        allowed = exc.rss_limit_bytes
    elif used is not None and exc.baseline_rss_bytes is not None:
        # Sampler noise can put the observed RSS below the admission baseline;
        # never render a negative usage.
        used = max(used - exc.baseline_rss_bytes, 0)
    else:
        # Without a baseline, ``used`` is TOTAL process RSS and the growth
        # budget in ``limit_bytes`` is not comparable to it — pair it with the
        # absolute enforced bound instead, or omit the sizes entirely.
        allowed = exc.rss_limit_bytes
    return (
        f"{operation_noun} needs more memory than this server allows"
        f"{_sizes_detail(used, allowed)}. "
        "Reduce the data size or the number of features, or run on a server "
        "with more memory, then try again."
    )
