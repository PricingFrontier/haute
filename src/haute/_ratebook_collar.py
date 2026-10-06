"""The combined-factor collar a ratebook solve scored, carried to every deployment.

A ratebook solve prices each quote at the scenario-grid step nearest the product
of its factor rates, clamped to the grid ends (price-contour's step rule). The
deployed ratebook multiplies the same rates into ``optimised_factor``; without
a collar, a quote whose product lies past a grid end would deploy at a factor
the solver never scored. The collar is ``[sv_min, sv_max]`` of the grid the
solve used, captured once from ``QuoteGrid.scenario_values`` (Float32 widened
to Python floats, exactly the values scored) and saved on the artifact as
``combined_factor_bounds = {"min": sv_min, "max": sv_max}``. Every apply path
clamps the combined factor to it; per-factor columns stay unclamped.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

COMBINED_FACTOR_BOUNDS_KEY = "combined_factor_bounds"


class CombinedFactorBoundsError(ValueError):
    """A ratebook artifact's ``combined_factor_bounds`` is missing or malformed."""


def combined_factor_bounds_from_grid(scenario_values: Sequence[float]) -> dict[str, float]:
    """The collar of a solve: the first and last of the grid's sorted scenario values."""
    if len(scenario_values) == 0:
        raise CombinedFactorBoundsError("the scenario grid has no scenario values")
    values = [float(value) for value in scenario_values]
    if any(later < earlier for earlier, later in zip(values, values[1:])):
        raise CombinedFactorBoundsError(
            f"the scenario grid's values are not sorted ascending: {values}"
        )
    return {"min": values[0], "max": values[-1]}


def parse_combined_factor_bounds(value: Any) -> tuple[float, float]:
    """Return ``(min, max)`` or raise naming exactly what is wrong.

    The value must be a mapping with exactly the keys ``min`` and ``max``, each
    a finite real number (``bool`` is not a number here), with ``min <= max``.
    """
    if not isinstance(value, dict):
        raise CombinedFactorBoundsError(
            f"{COMBINED_FACTOR_BOUNDS_KEY} must be an object with 'min' and 'max', "
            f"got {type(value).__name__}"
        )
    if set(value) != {"min", "max"}:
        raise CombinedFactorBoundsError(
            f"{COMBINED_FACTOR_BOUNDS_KEY} must have exactly the keys 'min' and 'max', "
            f"got {sorted(map(str, value))}"
        )
    bounds: list[float] = []
    for key in ("min", "max"):
        raw = value[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise CombinedFactorBoundsError(
                f"{COMBINED_FACTOR_BOUNDS_KEY}.{key} must be a number, got {raw!r}"
            )
        number = float(raw)
        if not math.isfinite(number):
            raise CombinedFactorBoundsError(
                f"{COMBINED_FACTOR_BOUNDS_KEY}.{key} must be finite, got {raw!r}"
            )
        bounds.append(number)
    low, high = bounds
    if low > high:
        raise CombinedFactorBoundsError(
            f"{COMBINED_FACTOR_BOUNDS_KEY} min {low!r} is greater than max {high!r}"
        )
    return low, high
