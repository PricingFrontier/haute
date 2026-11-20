"""What a deployed service says when it refuses or stops a request for memory.

Every ``507`` the generated ``/quote`` route sends carries the typed ``memory_limit``
payload its cause gives (an admission refusal, a run that passed its budget, a batch
worker that hit its hard cap) and, beside it, ``message``: the limit that refused the
request and the setting that changes it, in words, so whoever runs the service can act
on the answer without reading byte counts or the service's log (specs/deploy).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from haute._execution_admission import memory_limit_setting, process_rss_limit_setting
from haute._execution_context import ExecutionProfile
from haute._logging import get_logger
from haute._memory_messages import format_byte_size

logger = get_logger(component="deploy.memory")

#: What one request of each profile is: a batch of several quotes, or one quote.
_WORK = {
    ExecutionProfile.DEPLOY_BATCH: "a request of several quotes",
    ExecutionProfile.DEPLOY_LIVE: "a request of one quote",
}


def memory_answer(payload: Mapping[str, Any], *, profile: ExecutionProfile) -> dict[str, Any]:
    """*payload*, a ``memory_limit`` payload for work of *profile*, with its ``message``,
    which the service's log carries too."""
    message = memory_message(payload, profile=profile)
    logger.warning("deploy_memory_refused", reason=payload.get("reason"), message=message)
    return {**payload, "message": message}


def memory_message(payload: Mapping[str, Any], *, profile: ExecutionProfile) -> str:
    """The limit that refused or stopped the request, and the setting that changes it."""
    work = _WORK.get(profile, "this request")
    limit = _size(payload.get("memory_limit_bytes"))
    setting = memory_limit_setting(profile)
    budget = (
        f"The service gives {work} {limit} of memory, set by {setting}."
        if limit is not None
        else f"The memory the service gives {work} is set by {setting}."
    )
    reason = payload.get("reason")
    reserved = payload.get("in_flight_reserved_bytes")
    allowed = _size(payload.get("in_flight_limit_bytes"))
    if reason == "in_flight_memory_budget_exceeded" and allowed is not None:
        if isinstance(reserved, int) and reserved > 0:
            return (
                f"The quotes were not priced: requests being priced already hold "
                f"{_size(reserved)} of the {allowed} this server can give. {budget} Send "
                f"the request again once they finish, lower {setting} to price more "
                "requests at once, or give the server more memory."
            )
        return (
            f"The quotes were not priced: this server can give only {allowed}. {budget} "
            f"Lower {setting}, or give the server more memory."
        )
    if reason == "process_rss_limit_exceeded":
        cap = _size(payload.get("process_rss_limit_bytes"))
        cap_setting = process_rss_limit_setting(profile)
        return (
            f"The quotes were not priced: the service has reached its memory cap of {cap}, "
            f"set by {cap_setting}. Raise {cap_setting}, or give the server more memory."
        )
    if reason == "native_memory_cap_unavailable":
        return (
            f"The quotes were not priced: this host cannot hold the worker that prices "
            f"{work} to its memory cap. Serve the image on a host that supports native "
            "memory caps."
        )
    if reason in {"no_memory_available", "memory_sampler_unavailable"}:
        return (
            "The quotes were not priced: this server has no memory free beyond what it "
            f"keeps for the system. {budget} Give the server more memory."
        )
    return (
        f"The quotes were not priced: pricing them needed more memory than the service "
        f"gives. {budget} Raise {setting}, send fewer quotes in a request, or give the "
        "server more memory."
    )


def _size(value: object) -> str | None:
    return format_byte_size(value) if isinstance(value, int) else None
