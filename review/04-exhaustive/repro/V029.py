"""Reproduction for V029.

Claim: ``_reserve_in_flight_budget`` (src/haute/_execution_admission.py:507-523)
refuses the VERY FIRST in-flight admission on an EMPTY ledger whenever a single
profile's configured memory LIMIT exceeds ``available_ram - os_reserve``. There
is no clamp guaranteeing that at least the first reservation is admitted.

Mechanism (lines 509-523):
    limit_bytes       = _in_flight_limit_bytes(budget)   # max(available - reserve, 1)
    reservation_bytes = budget.memory_limit_bytes        # the per-profile LIMIT
    with _IN_FLIGHT_LOCK:
        reserved = sum(... _IN_FLIGHT_RESERVATIONS ...)   # 0 on empty ledger
        if reserved + reservation_bytes > limit_bytes:    # 0 + LIMIT > limit_bytes
            raise ExecutionAdmissionError(reason="in_flight_memory_budget_exceeded")

This is DISTINCT from the known accumulation/leak finding (which needs 2-3
lingering reservations to sum past the limit): here a single fresh reservation
on an empty ledger is rejected even though real RSS is near zero.

The trigger is the FIXED (or strict_server) memory policy, where
``budget.memory_limit_bytes`` is a hardcoded constant
(_DEFAULT_MEMORY_LIMIT_BYTES, e.g. 4 GiB for LAZY_SINK) that does NOT scale with
available RAM, while ``_in_flight_limit_bytes`` is still derived from real RAM
(``available_ram - os_reserve``). On a box where available_ram < LIMIT +
os_reserve, the first/only heavy job is refused.

(Note: under the ADAPTIVE policy the limit is clamped to ``usable`` =
``max(available - reserve, 1)`` at line 372, so reservation == limit and the
gate does NOT trigger on the first job. The defect lives specifically on the
fixed/strict_server path, which the finding's title/description name as
"default fixed budgets".)

ISOLATION: no disk I/O, no project root, no reads/writes of rating/, src/,
tests/, or any real project file. We synthesise a small RAM value in memory by
patching ``haute._ram_estimate.available_ram_bytes`` (the patch point the
module function ``_execution_admission.available_ram_bytes`` delegates to) and
select the fixed policy via an environment variable that we set and restore
ourselves. We ASSERT on the SPECIFIC wrong values: an empty ledger
(reserved == 0) with reservation_bytes (4 GiB) strictly above the in-flight
limit (3 GiB) causes the first admission to be refused.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from haute import _execution_admission as admission_mod
from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
    execution_budget_for_profile,
)
from haute._execution_context import ExecutionProfile

_GIB = 1024 * 1024 * 1024

# Profile in the in-flight set with a fixed default LIMIT of 4 GiB.
PROFILE = ExecutionProfile.LAZY_SINK

# Memory-budget env vars that could perturb the fixed-default resolution.
# We clear them so the repro exercises the documented default fixed budget.
_ENV_KEYS_TO_CLEAR = []
for _profile in ExecutionProfile:
    for _key, _mult in admission_mod._memory_env_candidates(_profile):
        _ENV_KEYS_TO_CLEAR.append(_key)
    for _key, _mult in admission_mod._process_rss_env_candidates(_profile):
        _ENV_KEYS_TO_CLEAR.append(_key)
_ENV_KEYS_TO_CLEAR += [
    "HAUTE_EXECUTION_OS_RESERVE_BYTES",
    "HAUTE_EXECUTION_OS_RESERVE_MB",
]

failures: list[str] = []


def run_repro() -> None:
    # Small box: 5 GiB available RAM. Default OS reserve is 2 GiB, so the
    # in-flight limit becomes max(5 - 2, 1) = 3 GiB, BELOW the 4 GiB fixed
    # LAZY_SINK limit.
    available = 5 * _GIB

    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS_TO_CLEAR}
    saved_policy = os.environ.get("HAUTE_EXECUTION_MEMORY_POLICY")
    try:
        for k in _ENV_KEYS_TO_CLEAR:
            os.environ.pop(k, None)
        # Fixed policy -> hardcoded per-profile limit, available_ram_bytes=None
        # on the budget, so _in_flight_limit_bytes recomputes from real RAM.
        os.environ["HAUTE_EXECUTION_MEMORY_POLICY"] = "fixed"

        admission_mod._clear_in_flight_reservations_for_tests()

        with patch(
            "haute._ram_estimate.available_ram_bytes",
            lambda: available,
        ):
            budget = execution_budget_for_profile(PROFILE)
            limit_bytes = admission_mod._in_flight_limit_bytes(budget)
            reservation_bytes = budget.memory_limit_bytes

            print(f"profile                = {PROFILE.value}")
            print(f"budget_policy          = {budget.budget_policy}")
            print(f"available_ram (box)    = {available} ({available / _GIB:.2f} GiB)")
            print(
                f"budget.memory_limit    = {reservation_bytes} "
                f"({reservation_bytes / _GIB:.2f} GiB)"
            )
            print(
                f"in_flight limit_bytes  = {limit_bytes} "
                f"({limit_bytes / _GIB:.2f} GiB)"
            )
            print(
                f"in-flight ledger size  = "
                f"{len(admission_mod._IN_FLIGHT_RESERVATIONS)} (expect 0, empty)"
            )

            # Sanity: this scenario is the one the finding predicts.
            assert budget.budget_policy == "fixed_default", budget.budget_policy
            assert reservation_bytes == 4 * 1024 * 1024 * 1024, reservation_bytes
            assert limit_bytes == 3 * _GIB, limit_bytes
            assert reservation_bytes > limit_bytes, (
                "scenario invalid: reservation does not exceed the in-flight "
                "limit, so the first-admission refusal cannot be demonstrated"
            )

            # THE ACTUAL CALL: first and only admission on an empty ledger.
            ctx = None
            err: ExecutionAdmissionError | None = None
            assert (
                len(admission_mod._IN_FLIGHT_RESERVATIONS) == 0
            ), "ledger must be empty before the first admission"
            try:
                ctx = create_admitted_execution_context(
                    operation="first_and_only_heavy_job",
                    profile=PROFILE,
                    memory_sampler=lambda: 64 * 1024 * 1024,  # ~64 MiB RSS, near zero
                )
            except ExecutionAdmissionError as exc:
                err = exc

            if ctx is not None:
                ctx.release_admission()
                failures.append(
                    "first admission on an EMPTY ledger was ADMITTED -- the "
                    "predicted refusal did not occur (claim not reproduced)"
                )
                print("[no-bug] first heavy job was admitted")
                return

            assert err is not None, "no context and no error -- impossible"

            print()
            print("[admission refused]")
            print(f"  reason                 = {err.reason}")
            print(f"  in_flight_reserved     = {err.in_flight_reserved_bytes}")
            print(f"  in_flight_limit_bytes  = {err.in_flight_limit_bytes}")
            print(f"  memory_limit_bytes     = {err.memory_limit_bytes}")
            print(f"  rss_at_admission_bytes = {err.rss_at_admission_bytes}")

            # The defining wrong-value assertions:
            #  - it was refused for the in-flight budget reason
            #  - on a demonstrably EMPTY ledger (reserved == 0)
            #  - despite near-zero RSS at admission
            if err.reason != "in_flight_memory_budget_exceeded":
                failures.append(
                    "refused for the wrong reason "
                    f"{err.reason!r} (expected 'in_flight_memory_budget_exceeded')"
                )
            if err.in_flight_reserved_bytes != 0:
                failures.append(
                    "ledger was NOT empty at refusal: "
                    f"in_flight_reserved_bytes={err.in_flight_reserved_bytes} "
                    "(expected 0 -- this would make it the accumulation finding, "
                    "not the first-admission defect)"
                )
            if err.in_flight_limit_bytes != limit_bytes:
                failures.append(
                    f"limit reported {err.in_flight_limit_bytes} != computed "
                    f"{limit_bytes}"
                )
    finally:
        admission_mod._clear_in_flight_reservations_for_tests()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if saved_policy is None:
            os.environ.pop("HAUTE_EXECUTION_MEMORY_POLICY", None)
        else:
            os.environ["HAUTE_EXECUTION_MEMORY_POLICY"] = saved_policy


run_repro()

print()
if failures:
    print("REPRO RESULT: NOT reproduced -- discrepancies:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
else:
    print("REPRO RESULT: REPRODUCED -- with the fixed memory policy, the FIRST and")
    print("ONLY in-flight admission (LAZY_SINK, 4 GiB fixed limit) on an EMPTY ledger")
    print("(reserved == 0) is refused with reason 'in_flight_memory_budget_exceeded'")
    print("on a 5 GiB box (in-flight limit 3 GiB), despite ~64 MiB real RSS. No clamp")
    print("guarantees admission of the first reservation. Defect confirmed.")
