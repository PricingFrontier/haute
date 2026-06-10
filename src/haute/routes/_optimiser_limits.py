"""Shared optimiser response-size and compute budgets."""

from __future__ import annotations

from typing import Any

APPLY_PREVIEW_ROW_LIMIT = 100
FRONTIER_POINT_LIMIT = 2_000


class FrontierComputeBudgetExceededError(ValueError):
    """A frontier request projects more grid points than the solver cap."""


# Hard ceiling on solver evaluations per frontier request.  The solver computes
# ``n_points_per_dim ** n_constraints`` grid points before truncation; without
# this gate a single client request can pin the worker for hours.  Rejection is
# at the request boundary so the solver is never invoked for an infeasible grid.
#
# The value MUST equal price-contour's ``max_total_points`` default (10,000 on
# both ``OnlineOptimiser.frontier`` and ``RatebookOptimiser.frontier``): the
# routes call ``solver.frontier(...)`` without overriding that cap, so any
# request the gate admits above the library default would die inside the
# library as an opaque 500.  Pinned against the real library by
# ``tests/test_optimiser_routes_real_library.py::
# TestRealLibraryShapeContracts::test_route_budget_equals_library_frontier_cap``.
FRONTIER_COMPUTE_LIMIT = 10_000


def enforce_frontier_compute_budget(
    *,
    n_points_per_dim: int,
    n_constraints: int,
) -> None:
    """Raise :class:`FrontierComputeBudgetExceededError` for oversized sweeps.

    The grid the solver materialises is ``n_points_per_dim ** n_constraints``.
    The cap is not on the response (which is truncated by ``FRONTIER_POINT_LIMIT``)
    but on the solver workload itself, and mirrors the library's own
    ``max_total_points`` rejection (strictly greater-than, so exactly-at-cap
    requests pass here and in price-contour alike).  The error names both the
    projected grid size and the cap so the caller can surface an actionable
    422 instead of letting the library raise an opaque error mid-request.
    """
    if n_constraints <= 0:
        return
    # Repeated multiplication so a malicious request cannot trigger a giant
    # ``int`` allocation before the comparison short-circuits.  When the
    # overflow happens on the final factor the partial product IS the exact
    # projection; on an earlier factor it is a lower bound, reported as such.
    projected = 1
    for dim in range(1, n_constraints + 1):
        projected *= n_points_per_dim
        if projected > FRONTIER_COMPUTE_LIMIT:
            quantifier = "" if dim == n_constraints else "at least "
            raise FrontierComputeBudgetExceededError(
                f"Frontier compute budget exceeded: n_points_per_dim={n_points_per_dim} "
                f"across {n_constraints} constraint(s) projects {quantifier}"
                f"{projected:,} solver grid points, above the solver cap of "
                f"{FRONTIER_COMPUTE_LIMIT:,} points (price-contour max_total_points). "
                "Reduce n_points_per_dim or the number of swept constraints."
            )


def limited_apply_preview_payload(df: Any) -> dict[str, Any]:
    """Return a capped optimiser-apply preview with explicit row metadata."""

    row_count = len(df)
    visible_df = df.head(APPLY_PREVIEW_ROW_LIMIT)
    preview = visible_df.to_dicts()

    return {
        "preview": preview,
        "row_count": row_count,
        "preview_row_count": len(preview),
        "preview_row_limit": APPLY_PREVIEW_ROW_LIMIT,
        "preview_truncated": row_count > len(preview),
    }


def limited_frontier_payload(
    points_df: Any,
    *,
    constraint_names: list[str],
) -> dict[str, Any]:
    """Return a capped frontier payload while preserving total point count."""

    total_points = len(points_df)
    is_truncated = total_points > FRONTIER_POINT_LIMIT
    visible_points_df = points_df.head(FRONTIER_POINT_LIMIT) if is_truncated else points_df
    points = visible_points_df.to_dicts()
    for point in points:
        for name in constraint_names:
            total_key = f"total_{name}"
            if total_key not in point and name in point:
                point[total_key] = point[name]

    return {
        "status": "ok",
        "points": points,
        "n_points": total_points,
        "points_returned": len(points),
        "constraint_names": constraint_names,
        "points_limit": FRONTIER_POINT_LIMIT,
        "points_truncated": total_points > len(points),
    }
