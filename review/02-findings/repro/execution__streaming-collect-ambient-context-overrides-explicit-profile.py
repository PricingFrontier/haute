"""Reproduction for claim: streaming-collect-ambient-context-overrides-explicit-profile.

Claim: ``streaming_collect`` (src/haute/_polars_utils.py:65-71) computes

    metrics_context = execution_context or current_execution_context()
    normalised_profile = (
        metrics_context.profile if metrics_context is not None
        else _normalise_profile(profile)
    )

so the *explicitly passed* ``profile=`` argument is IGNORED whenever ANY
ExecutionContext is active on the contextvar (every ``ExecutionContext.stage()``
sets it).  The ``allow_broad`` validation at line 70
(``if allow_broad and normalised_profile not in _BROAD_COLLECT_PROFILES``) then
gates on the contextvar profile, not the caller's profile.

This repro proves the override with a DETERMINISTIC, Polars-version-independent
signal: the ``allow_broad=True`` gate.  ``_BROAD_COLLECT_PROFILES`` contains only
``PREVIEW_EAGER``.  Therefore for ``profile=LAZY_SINK`` (a bounded profile):

  * Case A (no ambient ExecutionContext): the explicit LAZY_SINK is honoured;
    LAZY_SINK is NOT broad, so ``allow_broad=True`` MUST raise
    ``ValueError('allow_broad=True is not permitted for profile lazy_sink')``.

  * Case B (a PREVIEW_EAGER ExecutionContext active via ``ctx.stage`` on the
    SAME thread, NO ``execution_context=`` kwarg): the contextvar profile
    PREVIEW_EAGER overrides the explicit LAZY_SINK, PREVIEW_EAGER IS broad, so
    the IDENTICAL call is ACCEPTED (no ValueError) and the collect runs.

Same arguments, opposite outcome, decided purely by an ambient context the
caller did not pass: that divergence is the bug.

To make the override concrete (not just inferred from the gate), Case B also
asserts the recorded stage metric carries ``profile == PREVIEW_EAGER`` even
though the call requested ``LAZY_SINK`` -- i.e. the bounded contract's profile
was silently replaced.

Isolation: builds a tiny in-memory LazyFrame; the collect succeeds via the
streaming engine.  No disk I/O, no project root, no real project files, so no
tempfile is required.  Nothing under rating/, src/, or tests/ is touched.
"""

from __future__ import annotations

import sys

import polars as pl

from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
    current_execution_context,
)
from haute._polars_utils import _BROAD_COLLECT_PROFILES, streaming_collect


def main() -> int:
    # Sanity: the broad-profile allow-list is exactly {PREVIEW_EAGER}, and
    # LAZY_SINK is a bounded (non-broad) profile.  The whole divergence below
    # hinges on this asymmetry.
    assert _BROAD_COLLECT_PROFILES == frozenset({ExecutionProfile.PREVIEW_EAGER}), (
        f"unexpected broad-profile set: {_BROAD_COLLECT_PROFILES!r}"
    )
    assert ExecutionProfile.LAZY_SINK not in _BROAD_COLLECT_PROFILES

    lf = pl.LazyFrame({"a": [1, 2, 3]})

    # ---- Case A: no ambient ExecutionContext on the contextvar. ----
    assert current_execution_context() is None, "test precondition: no ambient context"

    case_a_raised: ValueError | None = None
    try:
        streaming_collect(lf, profile=ExecutionProfile.LAZY_SINK, allow_broad=True)
    except ValueError as exc:  # expected per the bounded LAZY_SINK contract
        case_a_raised = exc

    print(f"[repro] Case A (no ambient ctx): raised = {case_a_raised!r}")
    assert case_a_raised is not None, (
        "Case A: expected ValueError('allow_broad=True is not permitted for "
        "profile lazy_sink') because LAZY_SINK is a bounded profile and the "
        "explicit profile= should govern when no context is active."
    )
    assert "lazy_sink" in str(case_a_raised), (
        f"Case A: ValueError should name the explicit profile 'lazy_sink'; got: {case_a_raised}"
    )
    assert "allow_broad" in str(case_a_raised), (
        f"Case A: ValueError should mention allow_broad; got: {case_a_raised}"
    )

    # ---- Case B: a PREVIEW_EAGER context active on the SAME thread. ----
    # This mirrors a preview/diamond stage (PREVIEW_EAGER) wrapping a helper
    # that calls streaming_collect with a bounded profile and no execution_context.
    ctx = ExecutionContext(operation="preview", profile=ExecutionProfile.PREVIEW_EAGER)

    case_b_raised: BaseException | None = None
    result_df: pl.DataFrame | None = None
    with ctx.stage("diamond_multi_port_collect"):
        # Confirm the stage really published itself on the contextvar.
        ambient = current_execution_context()
        assert ambient is ctx, "ctx.stage() did not publish ctx on the contextvar"
        assert ambient.profile is ExecutionProfile.PREVIEW_EAGER

        try:
            # IDENTICAL call to Case A: same explicit bounded profile, same
            # allow_broad=True, and NO execution_context kwarg.
            result_df = streaming_collect(
                lf, profile=ExecutionProfile.LAZY_SINK, allow_broad=True
            )
        except ValueError as exc:
            case_b_raised = exc

    print(f"[repro] Case B (PREVIEW_EAGER ctx ambient): raised = {case_b_raised!r}")
    print(f"[repro] Case B result rows = {None if result_df is None else result_df.height}")

    # The crux: the identical call that RAISED in Case A is ACCEPTED in Case B
    # purely because an ambient PREVIEW_EAGER context overrode the explicit
    # LAZY_SINK profile.
    assert case_b_raised is None, (
        "Case B: streaming_collect unexpectedly raised. If the explicit profile= "
        "were honoured (LAZY_SINK), allow_broad=True would be rejected here too. "
        f"Got: {case_b_raised!r}"
    )
    assert result_df is not None and result_df.height == 3, (
        "Case B: collect should have succeeded under the (overridden) broad profile"
    )

    # ---- Direct evidence of the silent override: the recorded stage metric ----
    # carries PREVIEW_EAGER, the contextvar profile -- not the LAZY_SINK the
    # caller explicitly requested for its bounded collect.
    stages = ctx.metrics.snapshot()
    assert stages, "expected at least one recorded stage metric"
    recorded = stages[-1]
    print(
        f"[repro] recorded stage: name={recorded.name!r} "
        f"profile={recorded.profile.value!r} n_collects={recorded.n_collects}"
    )
    assert recorded.profile is ExecutionProfile.PREVIEW_EAGER, (
        f"recorded stage profile should be the ambient PREVIEW_EAGER; got {recorded.profile}"
    )
    # The collect was attributed to the stage, confirming streaming_collect ran
    # under the ambient context (record_collect path), not the caller's profile.
    assert recorded.n_collects >= 1, (
        "the LAZY_SINK-requested collect should have been recorded against the "
        "ambient PREVIEW_EAGER stage"
    )

    print()
    print(
        "REPRODUCED: the IDENTICAL streaming_collect(lf, profile=LAZY_SINK, "
        "allow_broad=True) call RAISES ValueError when no context is active but "
        "is ACCEPTED when a PREVIEW_EAGER ExecutionContext is ambient on the "
        "contextvar. The explicit bounded profile (LAZY_SINK) is silently "
        "overridden by the ambient profile (PREVIEW_EAGER), so allow_broad "
        "gating and bounded-vs-broad collect routing follow the wrong profile "
        "under nested execution."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
