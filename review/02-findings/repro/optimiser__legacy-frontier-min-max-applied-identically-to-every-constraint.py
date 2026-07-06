"""Adversarial reproduction.

CLAIM: `_auto_frontier_ranges_from_config` reuses the legacy scalar
`frontier_min`/`frontier_max` as the SAME absolute (min, max) range for EVERY
constraint, regardless of each constraint's scale.

Location: src/haute/routes/_optimiser_service.py:1392-1406

Scenario from the claim: a multi-constraint config that omits per-constraint
`frontier_ranges` but carries the legacy scalar pair. The two constraints live
on wildly different scales:
  - premium_total : a SUM constraint in the millions  (min ~ 4_000_000)
  - loss_ratio    : a ratio constraint in [0, 1]       (max ~ 0.65)

The legacy pair is frontier_min=0.9, frontier_max=1.1 -- a plausible RATIO
sweep range, but nonsensical as an absolute threshold for a premium sum in the
millions. The bug: BOTH constraints get the identical absolute interval
(0.9, 1.1). The premium_total axis is swept over 0.9..1.1, which is ~6 orders
of magnitude away from any feasible premium total, so every frontier point on
that axis is degenerate/infeasible -- with no error raised.

This repro ISOLATES: it only imports the pure function and calls it on an
in-memory dict. No disk I/O, no rating/, src/, or tests/ reads.
"""

import sys
from pathlib import Path

# Make `haute` importable from the repo's src/ layout WITHOUT importing any
# real project data file. (Pure-function import only.)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from haute.routes._optimiser_service import _auto_frontier_ranges_from_config


def main() -> None:
    # Multi-constraint config on DIFFERENT scales, no per-constraint
    # frontier_ranges, only the legacy scalar pair.
    config = {
        "constraints": {
            "premium_total": {"min": 4_000_000.0},  # millions-scale SUM
            "loss_ratio": {"max": 0.65},            # [0, 1]-scale RATIO
        },
        "frontier_min": 0.9,   # plausible ratio sweep low
        "frontier_max": 1.1,   # plausible ratio sweep high
    }

    ranges = _auto_frontier_ranges_from_config(config)

    print("returned ranges:", ranges)

    # --- Assertion 1: the SAME absolute interval is broadcast to BOTH axes. ---
    assert ranges["premium_total"] == (0.9, 1.1), (
        f"premium_total range expected broadcast (0.9, 1.1), got {ranges['premium_total']}"
    )
    assert ranges["loss_ratio"] == (0.9, 1.1), (
        f"loss_ratio range expected broadcast (0.9, 1.1), got {ranges['loss_ratio']}"
    )
    assert ranges["premium_total"] == ranges["loss_ratio"], (
        "BUG CONFIRMED would require these to differ; they are identical."
    )

    # --- Assertion 2: demonstrate WHY this is wrong (not merely that it is
    # identical). A correct per-constraint sweep for premium_total must reach
    # near its own scale (millions). The broadcast interval (0.9, 1.1) does not
    # come within 6 orders of magnitude of the constraint's own threshold.
    premium_threshold = config["constraints"]["premium_total"]["min"]  # 4_000_000
    swept_low, swept_high = ranges["premium_total"]
    # The entire premium_total sweep sits absurdly below its own threshold.
    assert swept_high < premium_threshold / 1_000_000, (
        "premium_total sweep should be ~millions; broadcast leaves it at ~1.0, "
        f"swept_high={swept_high}, threshold={premium_threshold}"
    )

    print(
        "REPRODUCED: legacy scalar (frontier_min, frontier_max)=(0.9, 1.1) is "
        "broadcast identically to premium_total (millions-scale) and loss_ratio "
        "([0,1]-scale). The premium_total axis is swept over 0.9..1.1, ~6 orders "
        "of magnitude below its own min threshold of "
        f"{premium_threshold:,.0f} -- a silent inverse-units misconfiguration."
    )


if __name__ == "__main__":
    main()
