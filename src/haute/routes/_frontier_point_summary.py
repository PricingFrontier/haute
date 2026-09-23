"""The one derivation of a frontier point's solve summary.

A frontier point is a ``price-contour`` frontier row. Its summary is every
result field that differs from the solve it was swept from, and it is derived
only here: once per returned point when the frontier payload is built, and
again when the point is selected, so both always agree. The browser applies
the summary it is sent and never derives one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

NON_CONVERGED_WARNING = (
    "Solver did not converge. Consider increasing max_iter or relaxing tolerance."
)

_SCENARIO_VALUE_STAT_COLUMNS = {
    "mean": "sv_mean",
    "std": "sv_std",
    "min": "sv_min",
    "p5": "sv_p5",
    "p25": "sv_p25",
    "p50": "sv_median",
    "p75": "sv_p75",
    "p95": "sv_p95",
    "max": "sv_max",
    "pct_increase": "sv_pct_increase",
    "pct_decrease": "sv_pct_decrease",
}


class FrontierPointDataError(ValueError):
    """A frontier point that cannot be summarised.

    ``status_code`` is the HTTP status a route reports it with.
    """

    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def finite_frontier_value(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FrontierPointDataError(
            f"Frontier point data is malformed: field {field!r} is missing"
        )
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise FrontierPointDataError(
            f"Frontier point data is malformed: field {field!r} is not finite"
        )
    return result


def add_frontier_point_lambda(
    lambdas: dict[str, float],
    name: Any,
    value: Any,
    *,
    field: str,
) -> None:
    if not isinstance(name, str) or not name:
        raise FrontierPointDataError(
            "Frontier point data is malformed: lambda names must be non-empty strings"
        )
    parsed_value = finite_frontier_value(value, field=field)
    existing_value = lambdas.get(name)
    if existing_value is not None and abs(existing_value - parsed_value) > 1e-9:
        raise FrontierPointDataError(
            f"Frontier point data is malformed: conflicting lambda for {name!r}"
        )
    lambdas[name] = parsed_value


def frontier_point_lambdas(point: Mapping[str, Any]) -> dict[str, float]:
    lambdas: dict[str, float] = {}
    for key, value in point.items():
        if key.startswith("lambda_"):
            add_frontier_point_lambda(lambdas, key.removeprefix("lambda_"), value, field=key)
    if not lambdas:
        raise FrontierPointDataError("Frontier point has no lambda values", status_code=400)
    return lambdas


def frontier_point_constraint_value(point: Mapping[str, Any], name: str) -> float:
    total_key = f"total_{name}"
    if total_key in point:
        return finite_frontier_value(point[total_key], field=total_key)
    constraints = point.get("constraints")
    if isinstance(constraints, dict) and name in constraints:
        return finite_frontier_value(constraints[name], field=f"constraints.{name}")
    if name in point:
        return finite_frontier_value(point[name], field=name)
    return finite_frontier_value(None, field=total_key)


def frontier_point_scenario_value_stats(point: Mapping[str, Any]) -> dict[str, float] | None:
    if "sv_mean" not in point:
        return None
    return {
        stat: finite_frontier_value(point.get(column), field=column)
        for stat, column in _SCENARIO_VALUE_STAT_COLUMNS.items()
    }


def frontier_point_summary(
    point: Mapping[str, Any],
    constraint_names: Sequence[str],
) -> dict[str, Any]:
    """Every point-specific result field, ``None`` where the point has none."""
    lambdas = frontier_point_lambdas(point)
    constraints = {name: frontier_point_constraint_value(point, name) for name in constraint_names}
    converged = point.get("converged")
    if not isinstance(converged, bool):
        raise FrontierPointDataError(
            "Frontier point field 'converged' is missing",
            status_code=400,
        )
    total_objective = finite_frontier_value(point.get("total_objective"), field="total_objective")
    iterations = (
        int(finite_frontier_value(point["iterations"], field="iterations"))
        if "iterations" in point
        else None
    )
    cd_iterations = (
        int(finite_frontier_value(point["cd_iterations"], field="cd_iterations"))
        if "cd_iterations" in point
        else None
    )
    clamp_rate = (
        finite_frontier_value(point["clamp_rate"], field="clamp_rate")
        if "clamp_rate" in point
        else None
    )
    return {
        "total_objective": total_objective,
        "constraints": constraints,
        "lambdas": lambdas,
        "converged": converged,
        "iterations": iterations,
        "cd_iterations": cd_iterations,
        "clamp_rate": clamp_rate,
        "history": None,
        "scenario_value_stats": frontier_point_scenario_value_stats(point),
        "scenario_value_histogram": None,
        "factor_tables": None,
        "warning": None if converged else NON_CONVERGED_WARNING,
        "frontier_error": None,
    }


def apply_frontier_point_summary(
    base: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """The base result with the point's fields; a ``None`` field is removed."""
    result = dict(base)
    for field, value in summary.items():
        if value is None:
            result.pop(field, None)
        else:
            result[field] = value
    return result
