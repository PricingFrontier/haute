"""The adjustment report (OPT-V10): what the optimiser chose, against the 1.0 base price.

Pure: one ``ScenarioHistogram`` result in, one strict ``OptimiserAdjustmentReport``
out. It reads no file, job or grid; the histogram already holds one row per step
of the job's recorded scenario grid, the only source for which adjustments were
possible (OPT-V09A), so nothing here is inferred from the chosen rows.

A quote is adjusted up above 1.0, down below it and unadjusted at exactly 1.0,
which is a category only when the grid has a 1.0 step. Quote count always
weighs the report; the objective and each constraint, evaluated at the chosen
scenario, weigh it too unless a quote's value is negative or the total is zero,
in which case the weighting is refused by name, never computed. A ratebook
report also counts the quotes whose deployed factor differs from the step the
solver evaluated (OPT-V09C).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from itertools import accumulate
from typing import Any

from haute.routes._optimiser_outcomes import (
    DEPLOYED_FACTOR_DIFFERS,
    NEGATIVE_PREFIX,
    ChoiceFrameSpec,
    ChoiceQueryResult,
)
from haute.schemas import OptimiserAdjustmentReport

QUOTES_WEIGHT = "quotes"
"""The weighting in which every quote weighs 1; always first."""

BASE_PRICE = 1.0
"""The unadjusted scenario value: the base price itself."""

_QUANTILES = (("p5", 0.05), ("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p95", 0.95))

ADJUSTMENT_REPORTS_KEY = "adjustment_reports"
"""The job field caching point reports by ``(frontier_generation, point_index)``."""

MAX_CACHED_ADJUSTMENT_REPORTS = 64
"""The most point reports one job keeps; the oldest is dropped first."""

PointReportKey = tuple[int, int]


def adjustment_report(
    histogram: ChoiceQueryResult, spec: ChoiceFrameSpec
) -> OptimiserAdjustmentReport:
    """The report of one target's chosen scenarios, from its ``ScenarioHistogram``.

    Raises ``ValueError`` for a target with no quotes.
    """
    if histogram.total == 0:
        raise ValueError("The target has no quotes to describe.")
    rows = histogram.rows
    values = [float(value) for value in rows["scenario_value"]]
    weights: dict[str, list[float]] = {QUOTES_WEIGHT: [float(count) for count in rows["quotes"]]}
    labels = {QUOTES_WEIGHT: "Quotes"}
    errors: list[dict[str, str]] = []
    for column, label in _candidate_weightings(spec):
        negative = int(rows[f"{NEGATIVE_PREFIX}{column}"].sum())
        per_step = [float(value) for value in rows[column]]
        if negative:
            quotes = f"{negative:,} quote{'' if negative == 1 else 's'}"
            errors.append(
                _weight_error(
                    "NegativeWeight",
                    f"{label} cannot weigh the adjustments: {quotes} "
                    f"{'has' if negative == 1 else 'have'} a negative value ({column}).",
                )
            )
            continue
        if math.fsum(per_step) <= 0:
            errors.append(
                _weight_error(
                    "ZeroTotalWeight",
                    f"{label} cannot weigh the adjustments: it is zero for every quote ({column}).",
                )
            )
            continue
        weights[column] = per_step
        labels[column] = label

    has_unadjusted = BASE_PRICE in values
    weighted = [key for key in weights if key != QUOTES_WEIGHT]
    return OptimiserAdjustmentReport.model_validate(
        {
            "n_quotes": histogram.total,
            "has_unadjusted": has_unadjusted,
            "bars": [
                {
                    "optimal_step": int(step),
                    "scenario_value": values[index],
                    "quotes": int(rows["quotes"][index]),
                    "weights": {key: weights[key][index] for key in weighted},
                }
                for index, step in enumerate(rows["optimal_step"])
            ],
            "weightings": [
                _weighting(key, labels[key], values, weights[key], has_unadjusted)
                for key in weights
            ],
            "diagnostics_errors": errors,
            "deployed_factor_differs": (
                int(rows[DEPLOYED_FACTOR_DIFFERS].sum()) if spec.mode == "ratebook" else None
            ),
        }
    )


def cache_point_report(
    reports: Mapping[PointReportKey, Any], key: PointReportKey, report: Any
) -> dict[PointReportKey, Any]:
    """*reports* with *report* stored as the newest under *key*, at most 64 kept."""
    cached = {existing: value for existing, value in reports.items() if existing != key}
    cached[key] = report
    while len(cached) > MAX_CACHED_ADJUSTMENT_REPORTS:
        del cached[next(iter(cached))]
    return cached


def _candidate_weightings(spec: ChoiceFrameSpec) -> list[tuple[str, str]]:
    """Each value column that may weigh the report, with its label, in configured order."""
    names = ("Objective", *spec.constraint_names)
    return [
        (column, f"{name} at the chosen scenario")
        for column, name in zip(spec.value_columns, names, strict=True)
    ]


def _weight_error(error_type: str, message: str) -> dict[str, str]:
    return {"diagnostic": "adjustment_weight", "error_type": error_type, "message": message}


def _weighting(
    key: str,
    label: str,
    values: Sequence[float],
    weights: Sequence[float],
    has_unadjusted: bool,
) -> dict[str, Any]:
    """One weighting's figures over the grid's *values* and each step's weight."""
    total = math.fsum(weights)

    def share(where: Callable[[float], bool]) -> float:
        return math.fsum(w for v, w in zip(values, weights, strict=True) if where(v)) / total

    cumulative = list(accumulate(weights))
    return {
        "key": key,
        "label": label,
        "total": total,
        "mean": math.fsum(v * w for v, w in zip(values, weights, strict=True)) / total,
        "quantiles": {
            name: _inverted_cdf(values, weights, cumulative, level * total)
            for name, level in _QUANTILES
        },
        "share_up": share(lambda value: value > BASE_PRICE),
        "share_down": share(lambda value: value < BASE_PRICE),
        "share_unadjusted": share(lambda value: value == BASE_PRICE) if has_unadjusted else None,
        "share_at_min": weights[0] / total,
        "share_at_max": weights[-1] / total,
    }


def _inverted_cdf(
    values: Sequence[float],
    weights: Sequence[float],
    cumulative: Sequence[float],
    target: float,
) -> float:
    """The smallest grid value whose cumulative weight reaches *target* (a positive weight)."""
    for value, weight, reached in zip(values, weights, cumulative, strict=True):
        if weight > 0 and reached >= target:
            return value
    raise ValueError(f"No grid value reaches the cumulative weight {target}.")
