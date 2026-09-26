"""The one derivation of a frontier point's solve summary.

A frontier point is a ``price-contour`` frontier row, typed once here
(``frontier_point_rows``) when the frontier payload is built. Its summary is
every result field that differs from the solve it was swept from, and it is
derived only here, from the typed point: once per returned point when the
frontier payload is built, and again when the point is selected, so both
always agree. The browser applies the summary it is sent and never derives one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from haute._price_contour import price_contour
from haute.schemas import OptimiserOnlineFrontierPoint, OptimiserRatebookFrontierPoint

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


ConstraintKind = Literal["min", "max"]

# The configured threshold key of a constraint and the direction it bounds.
# A pct key's threshold is a fraction of the constraint's baseline total; the
# absolute bound is price-contour's, never derived here.
CONSTRAINT_THRESHOLD_KINDS: Mapping[str, ConstraintKind] = {
    "min": "min",
    "max": "max",
    "min_pct": "min",
    "max_pct": "max",
}


def constraint_kinds(constraints: Any) -> dict[str, ConstraintKind]:
    """Each configured constraint's kind, in configured order.

    Raises ``ValueError`` for a spec without exactly one threshold key.
    """
    if not isinstance(constraints, Mapping):
        raise ValueError("Optimiser constraints must map each constraint name to its spec")
    kinds: dict[str, ConstraintKind] = {}
    for name, spec in constraints.items():
        if not isinstance(name, str) or not name or not isinstance(spec, Mapping):
            raise ValueError(f"Optimiser constraint {name!r} must map a name to a spec")
        keys = [key for key in CONSTRAINT_THRESHOLD_KINDS if key in spec]
        if len(keys) != 1:
            raise ValueError(
                f"Optimiser constraint {name!r} must set exactly one of "
                f"{', '.join(CONSTRAINT_THRESHOLD_KINDS)}"
            )
        kinds[name] = CONSTRAINT_THRESHOLD_KINDS[keys[0]]
    return kinds


def effective_bounds(
    kinds: Mapping[str, ConstraintKind],
    bounds: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """``{name: {"kind", "bound"}}`` for every configured constraint.

    ``bounds`` are price-contour's absolute bounds (a solve result's
    ``constraint_bounds``, or a frontier row's ``bound_<name>`` values); they
    must cover exactly the configured constraints.
    """
    missing = [name for name in kinds if name not in bounds]
    unexpected = [name for name in bounds if name not in kinds]
    if missing or unexpected:
        raise ValueError(
            "Constraint bounds do not match the configured constraints: "
            f"missing {missing}, unexpected {unexpected}"
        )
    result: dict[str, dict[str, Any]] = {}
    for name, kind in kinds.items():
        bound = bounds[name]
        if isinstance(bound, bool) or not isinstance(bound, int | float):
            raise ValueError(f"Constraint bound for {name!r} is missing")
        if not math.isfinite(bound):
            raise ValueError(f"Constraint bound for {name!r} is not finite")
        result[name] = {"kind": kind, "bound": float(bound)}
    return result


class FrontierPointDataError(ValueError):
    """A frontier point that cannot be typed or summarised.

    ``status_code`` is the HTTP status a route reports it with.
    """

    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


# The per-constraint library column prefixes and the typed point's map for each.
_CONSTRAINT_COLUMN_MAPS = {
    "threshold": "thresholds",
    "bound": "bounds",
    "total": "totals",
    "lambda": "lambdas",
}

_POINT_MODELS: Mapping[str, type[BaseModel]] = {
    "online": OptimiserOnlineFrontierPoint,
    "ratebook": OptimiserRatebookFrontierPoint,
}


def _library_columns(mode: str, constraint_names: Sequence[str]) -> list[str]:
    return list(price_contour().frontier_points_schema(mode, list(constraint_names)))


def _constraint_columns(constraint_names: Sequence[str]) -> dict[str, tuple[str, str]]:
    """``{library column: (typed map, constraint name)}`` for every per-constraint column."""
    return {
        f"{prefix}_{name}": (field, name)
        for prefix, field in _CONSTRAINT_COLUMN_MAPS.items()
        for name in constraint_names
    }


def frontier_point_rows(
    points_df: Any,
    *,
    mode: str,
    constraint_names: Sequence[str],
) -> list[dict[str, Any]]:
    """price-contour's frontier ``points`` frame as typed ``OptimiserFrontierPoint`` rows.

    The frame's columns must be exactly ``frontier_points_schema(mode,
    constraint_names)``. Each per-constraint column (``threshold_<c>``,
    ``bound_<c>``, ``total_<c>``, ``lambda_<c>``) becomes a map keyed by
    constraint name in ``constraint_names`` order; every other column keeps its
    library name. Each row is validated by its mode's strict model.
    """
    model = _POINT_MODELS.get(mode)
    if model is None:
        raise FrontierPointDataError(f"Unknown optimiser mode {mode!r} for frontier points")
    expected = _library_columns(mode, constraint_names)
    columns = list(points_df.columns)
    if set(columns) != set(expected):
        missing = [column for column in expected if column not in columns]
        unexpected = [column for column in columns if column not in expected]
        raise FrontierPointDataError(
            f"Frontier points do not match price-contour's {mode} points schema: "
            f"missing {missing}, unexpected {unexpected}"
        )
    constraint_columns = _constraint_columns(constraint_names)
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(points_df.to_dicts()):
        point: dict[str, Any] = {
            "mode": mode,
            **{field: {} for field in _CONSTRAINT_COLUMN_MAPS.values()},
        }
        for column in expected:
            if column in constraint_columns:
                field, name = constraint_columns[column]
                point[field][name] = raw[column]
            else:
                point[column] = raw[column]
        try:
            rows.append(model.model_validate(point).model_dump())
        except ValidationError as exc:
            raise FrontierPointDataError(f"Frontier point {index} is malformed: {exc}") from exc
    return rows


def frontier_point_library_row(
    point: Mapping[str, Any],
    constraint_names: Sequence[str],
) -> dict[str, Any]:
    """A typed frontier point written back as price-contour's flat row, in schema order."""
    constraint_columns = _constraint_columns(constraint_names)
    row: dict[str, Any] = {}
    for column in _library_columns(point["mode"], constraint_names):
        if column in constraint_columns:
            field, name = constraint_columns[column]
            row[column] = point[field][name]
        else:
            row[column] = point[column]
    return row


def _scenario_value_stats(point: Mapping[str, Any]) -> dict[str, float] | None:
    """An online point's ``sv_*`` statistics; a ratebook point reports none."""
    if point["mode"] != "online":
        return None
    return {stat: point[column] for stat, column in _SCENARIO_VALUE_STAT_COLUMNS.items()}


def frontier_point_summary(
    point: Mapping[str, Any],
    kinds: Mapping[str, ConstraintKind],
) -> dict[str, Any]:
    """Every point-specific result field of a typed point, ``None`` where it has none.

    ``kinds`` is every configured constraint, swept or not, in configured
    order: the point's maps hold each of them (``frontier_point_rows``).
    """
    converged = point["converged"]
    return {
        "total_objective": point["total_objective"],
        "constraints": {name: point["totals"][name] for name in kinds},
        "effective_bounds": effective_bounds(kinds, point["bounds"]),
        "lambdas": {name: point["lambdas"][name] for name in kinds},
        "converged": converged,
        "iterations": point["iterations"],
        "cd_iterations": None,
        "clamp_rate": point["clamp_rate"] if point["mode"] == "ratebook" else None,
        "history": None,
        "scenario_value_stats": _scenario_value_stats(point),
        "scenario_value_histogram": None,
        "factor_tables": None,
        "warning": None if converged else NON_CONVERGED_WARNING,
        "frontier_error": None,
        # A point's statistics come from its own row: the solve's degraded
        # diagnostics do not describe it.
        "diagnostics_errors": [],
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
