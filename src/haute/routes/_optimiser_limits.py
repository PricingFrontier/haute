"""Shared optimiser response-size budgets."""

from __future__ import annotations

from typing import Any

APPLY_PREVIEW_ROW_LIMIT = 100
FRONTIER_POINT_LIMIT = 2_000


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
