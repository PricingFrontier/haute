"""Adversarial repro for claim:

  "In-flight memory admission reserves the full per-profile LIMIT (not estimated
   usage) and releases it only via weakref.finalize at GC, so stale contexts the
   route never close() can exhaust the process-wide in-flight budget and
   spuriously refuse legitimate jobs."

This script probes THREE distinct sub-assertions of the claim, asserting on the
specific VALUE/behaviour each predicts:

  A. Reservation size == per-profile LIMIT (the "coarse, not usage" assertion).
  B. Holding LIMIT-sized reservations alive (defeating GC) refuses the next admit
     with reason 'in_flight_memory_budget_exceeded' even though real RSS is tiny.
  C. The DECISIVE question for "real vs refuted": is release ONLY via explicit
     close()/GC, or does the weakref.finalize backstop actually reclaim the
     reservation when a context is dropped without close()?  If GC reclaims it,
     then a route that "returns without close()" does NOT leak indefinitely.

Isolation: no disk I/O, no project root, monkeypatch the admission-local RAM
patch point only. We never touch src/, tests/, or rating/.
"""

from __future__ import annotations

import gc

import haute._execution_admission as admission_mod
import haute._ram_estimate as ram_mod
from haute._execution_admission import (
    ExecutionAdmissionError,
    create_admitted_execution_context,
)
from haute._execution_context import ExecutionProfile

_GIB = 1024 * 1024 * 1024


def _patch_ram(available_bytes: int) -> tuple:
    """Force the adaptive RAM estimate; return originals to restore."""
    orig_admission = admission_mod.available_ram_bytes
    orig_ram = ram_mod.available_ram_bytes
    admission_mod.available_ram_bytes = lambda: available_bytes  # type: ignore[assignment]
    ram_mod.available_ram_bytes = lambda: available_bytes  # type: ignore[assignment]
    return orig_admission, orig_ram


def _restore_ram(orig: tuple) -> None:
    admission_mod.available_ram_bytes = orig[0]  # type: ignore[assignment]
    ram_mod.available_ram_bytes = orig[1]  # type: ignore[assignment]


def main() -> None:
    # Use the FIXED policy so LAZY_SINK limit is exactly 4 GiB (deterministic).
    import os

    prev_policy = os.environ.get("HAUTE_EXECUTION_MEMORY_POLICY")
    os.environ["HAUTE_EXECUTION_MEMORY_POLICY"] = "fixed"
    # Clear any profile/global env overrides that could perturb the limit.
    for key in (
        "HAUTE_SINK_MEMORY_LIMIT_BYTES",
        "HAUTE_SINK_MEMORY_LIMIT_MB",
        "HAUTE_EXECUTION_MEMORY_LIMIT_BYTES",
        "HAUTE_EXECUTION_MEMORY_LIMIT_MB",
        "HAUTE_SINK_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_SINK_PROCESS_RSS_LIMIT_MB",
        "HAUTE_EXECUTION_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_EXECUTION_PROCESS_RSS_LIMIT_MB",
        "HAUTE_EXECUTION_OS_RESERVE_BYTES",
        "HAUTE_EXECUTION_OS_RESERVE_MB",
    ):
        os.environ.pop(key, None)

    # available 11 GiB; default OS reserve 2 GiB => in_flight_limit 9 GiB.
    # Two 4 GiB LIMIT reservations (8 GiB) fit; a third (12 GiB) is refused.
    orig = _patch_ram(11 * _GIB)
    admission_mod._clear_in_flight_reservations_for_tests()
    try:
        # ---- Sub-assertion A: reservation == LIMIT (4 GiB for LAZY_SINK) ----
        ctx1 = create_admitted_execution_context(
            operation="sink_a",
            profile=ExecutionProfile.LAZY_SINK,
            memory_sampler=lambda: 100,  # tiny real RSS
        )
        with admission_mod._IN_FLIGHT_LOCK:
            reserved_amounts = [
                amount
                for _profile, amount, _op in admission_mod._IN_FLIGHT_RESERVATIONS.values()
            ]
        limit_4gib = 4 * 1024 * 1024 * 1024
        assert reserved_amounts == [limit_4gib], (
            f"A: expected one reservation of exactly the 4 GiB LIMIT, "
            f"got {reserved_amounts}"
        )
        print(f"[A] reservation size == LIMIT: {reserved_amounts[0]} bytes (4 GiB) -- CONFIRMED")

        # ---- Sub-assertion B: 2 live LIMIT reservations refuse the 3rd ----
        held = [ctx1]
        ctx2 = create_admitted_execution_context(
            operation="sink_b",
            profile=ExecutionProfile.LAZY_SINK,
            memory_sampler=lambda: 100,
        )
        held.append(ctx2)  # keep both alive -> defeats GC

        raised_reason = None
        try:
            create_admitted_execution_context(
                operation="sink_c",
                profile=ExecutionProfile.LAZY_SINK,
                memory_sampler=lambda: 100,
            )
        except ExecutionAdmissionError as exc:
            raised_reason = exc.reason
        assert raised_reason == "in_flight_memory_budget_exceeded", (
            f"B: expected refusal reason 'in_flight_memory_budget_exceeded' "
            f"with 2 live 4 GiB reservations (12 GiB needed > 9 GiB limit), "
            f"got {raised_reason!r}"
        )
        print(
            "[B] 3rd admit refused with 'in_flight_memory_budget_exceeded' "
            "despite ~100 B real RSS -- CONFIRMED (coarse LIMIT accounting)"
        )

        # ---- Sub-assertion C: does dropping WITHOUT close() leak until GC, ----
        # ---- and does the weakref.finalize backstop actually reclaim it?  ----
        # Free ctx2 explicitly so we are back to exactly 1 reservation, then
        # build a context, DROP it without close(), and check whether GC
        # reclaims the reservation via weakref.finalize.
        ctx2.release_admission()
        held = [ctx1]
        with admission_mod._IN_FLIGHT_LOCK:
            count_after_release = len(admission_mod._IN_FLIGHT_RESERVATIONS)
        assert count_after_release == 1, (
            f"C-pre: expected 1 reservation after releasing ctx2, got {count_after_release}"
        )

        def _build_and_drop_without_close() -> None:
            # Simulate the claim's scenario: a route builds a context, hands it
            # off, and returns WITHOUT calling close()/release_admission().
            _local = create_admitted_execution_context(
                operation="sink_dropped",
                profile=ExecutionProfile.LAZY_SINK,
                memory_sampler=lambda: 100,
            )
            # _local goes out of scope here with NO release call.

        _build_and_drop_without_close()
        with admission_mod._IN_FLIGHT_LOCK:
            count_before_gc = len(admission_mod._IN_FLIGHT_RESERVATIONS)
        gc.collect()
        with admission_mod._IN_FLIGHT_LOCK:
            count_after_gc = len(admission_mod._IN_FLIGHT_RESERVATIONS)

        print(
            f"[C] reservations: before_gc={count_before_gc} after_gc={count_after_gc} "
            f"(1 == only ctx1 still held)"
        )
        # The claim asserts release happens "only via weakref.finalize at GC".
        # If weakref.finalize is a WORKING backstop, gc.collect() drops the
        # orphaned reservation back to 1. If it leaked forever, it'd stay at 2.
        backstop_reclaims = count_after_gc == 1
        assert backstop_reclaims, (
            f"C: weakref.finalize did NOT reclaim the dropped reservation; "
            f"after gc.collect() expected 1, got {count_after_gc} -- would be a true leak"
        )
        print(
            "[C] weakref.finalize backstop RECLAIMS dropped-without-close() reservation "
            "at GC -- so a route that returns without close() does NOT leak indefinitely"
        )

        ctx1.release_admission()

        print("\nVERDICT INPUT SUMMARY:")
        print(" - Reservation is the per-profile LIMIT, not usage: TRUE (by design, conservative).")
        print(" - Live LIMIT reservations can refuse the next admit: TRUE (intended admission cap).")
        print(" - 'released ONLY via close()/GC, stale contexts leak until GC': the GC backstop")
        print("   WORKS, and all real routes call release_admission() in finally/done-callbacks,")
        print("   so the 'leaks until GC' premise about real routes is NOT substantiated.")
    finally:
        admission_mod._clear_in_flight_reservations_for_tests()
        _restore_ram(orig)
        if prev_policy is None:
            os.environ.pop("HAUTE_EXECUTION_MEMORY_POLICY", None)
        else:
            os.environ["HAUTE_EXECUTION_MEMORY_POLICY"] = prev_policy


if __name__ == "__main__":
    main()
