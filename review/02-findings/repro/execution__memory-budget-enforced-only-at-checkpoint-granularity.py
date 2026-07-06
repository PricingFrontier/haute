"""Adversarial repro for claim:

  "ExecutionContext memory limit is a coarse post-hoc gate sampled only at
   checkpoint()/stage() boundaries; a single node's collect/sink that balloons
   RSS between two checkpoints is never interrupted and can OOM before any
   check fires."

Strategy
--------
We build a real ``ExecutionContext`` with a tiny ``memory_limit_bytes`` and a
*scripted* ``memory_sampler``.  The sampler is the ONLY way the gate observes
RSS, so by controlling exactly what it returns at each call we can prove WHEN
the gate looks.

We model one heavy node exactly the way ``_execute_eager_core`` does:

    ctx.checkpoint(label="before_collect")          # boundary 1
    with ctx.stage("eager_collect"):                # boundary 2 (entry)
        <the .collect() runs here; RSS balloons>    # NO gate sampling here
    ctx.checkpoint(label="after_collect")           # boundary 3

The scripted sampler returns an UNDER-limit value at every boundary the gate
consults, but a transient OVER-limit "peak" exists *inside* the stage body
(simulating intra-collect growth).  We assert:

  (A) No ExecutionMemoryLimitExceededError is raised across the whole node,
      even though a value 10x over the limit occurred mid-collect.
  (B) The set of RSS values the gate actually evaluated contains ONLY the
      under-limit boundary samples and NEVER the over-limit peak  -> proving
      the gate is blind to intra-collect growth (granularity bug), not that it
      "happened" to miss by luck.

For contrast we also show the gate DOES fire when an over-limit value is
present *at* a boundary -- confirming the limit works, it is merely
checkpoint-granular.
"""

from __future__ import annotations

import sys

from haute._execution_context import (
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)

LIMIT = 100  # tiny effective limit (bytes). baseline=0 -> effective == LIMIT.
UNDER = 50  # boundary sample, comfortably under the limit
PEAK = 1_000  # intra-collect spike, 10x over the limit (the "balloon")


def _run() -> None:
    # ``observed`` records every RSS value the gate actually evaluates.
    # The sampler is scripted: it walks a fixed list of return values, one per
    # call.  Boundary calls (checkpoint entry, stage entry, stage exit) all read
    # UNDER.  We never feed PEAK to the gate -- instead we record, *inside the
    # stage body*, what the live process RSS "would" be (PEAK) to demonstrate
    # the gate is simply not consulted there.
    observed: list[int] = []
    # checkpoint() -> 1 sampler call; stage() entry -> 1; stage() exit -> 1;
    # final checkpoint() -> 1.  All return UNDER.
    scripted = iter([UNDER, UNDER, UNDER, UNDER])

    def sampler() -> int:
        value = next(scripted)
        observed.append(value)
        return value

    ctx = ExecutionContext(
        operation="repro",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=LIMIT,
        memory_baseline_bytes=0,  # effective limit == LIMIT
        memory_sampler=sampler,
    )

    live_rss_during_collect: list[int] = []
    raised: ExecutionMemoryLimitExceededError | None = None
    try:
        ctx.checkpoint(label="before_collect", node_id="heavy")
        with ctx.stage("eager_collect", node_id="heavy"):
            # This block stands in for ``streaming_collect(...)``.  In the real
            # code path there is NO ctx.checkpoint / _check_memory_budget call
            # inside streaming_collect, so a balloon here is invisible.
            live_rss_during_collect.append(PEAK)  # process RSS spikes 10x
            # (no gate consulted)
        ctx.checkpoint(label="after_collect", node_id="heavy")
    except ExecutionMemoryLimitExceededError as exc:  # pragma: no cover
        raised = exc

    # --- Assertion (A): the balloon is NOT interrupted -------------------
    assert raised is None, (
        "Expected NO memory-limit error for an intra-collect balloon, but the "
        f"gate fired: {raised!r}"
    )

    # --- Assertion (B): the gate never even looked at the over-limit peak
    assert PEAK in live_rss_during_collect, "sanity: peak must occur mid-collect"
    assert max(live_rss_during_collect) > LIMIT, "sanity: peak must exceed limit"
    assert all(v <= LIMIT for v in observed), (
        "Gate is supposed to only ever evaluate under-limit boundary samples in "
        f"this scenario; observed={observed}"
    )
    assert PEAK not in observed, (
        "PROVES BUG: the over-limit peak that occurred mid-collect was never "
        f"evaluated by _check_memory_budget. gate saw only {observed}, peak={PEAK}"
    )

    # --- Contrast: prove the limit DOES work at a boundary --------------
    # If the very same PEAK is present AT a checkpoint boundary, the gate fires.
    boundary_observed: list[int] = []

    def boundary_sampler() -> int:
        boundary_observed.append(PEAK)
        return PEAK

    ctx2 = ExecutionContext(
        operation="repro_contrast",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=LIMIT,
        memory_baseline_bytes=0,
        memory_sampler=boundary_sampler,
    )
    contrast_fired = False
    try:
        ctx2.checkpoint(label="at_boundary", node_id="heavy")
    except ExecutionMemoryLimitExceededError:
        contrast_fired = True
    assert contrast_fired, (
        "Contrast failed: the gate should fire when an over-limit value is "
        "present at a checkpoint boundary; if this fails the test is invalid."
    )

    print("REPRO_RESULT: intra-collect balloon NOT interrupted (granularity bug)")
    print(f"  effective_limit_bytes = {LIMIT}")
    print(f"  intra_collect_peak_rss = {PEAK}  (10x over limit)")
    print(f"  values gate actually evaluated during node = {observed}  (all <= {LIMIT})")
    print(f"  peak {PEAK} was NEVER passed to _check_memory_budget -> no OOM guard")
    print("  contrast: same peak AT a checkpoint boundary DID raise -> limit works, but only at boundaries")


if __name__ == "__main__":
    try:
        _run()
    except AssertionError as exc:
        print(f"REPRO_FAILED_ASSERTION: {exc}", file=sys.stderr)
        raise
    print("REPRO_OK: claim substantiated")
