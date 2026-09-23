"""The one derivation of a frontier point's solve summary (OPT-P18)."""

from __future__ import annotations

import polars as pl
import pytest

from haute.routes._frontier_point_summary import (
    NON_CONVERGED_WARNING,
    FrontierPointDataError,
    apply_frontier_point_summary,
    frontier_point_summary,
)
from haute.routes._optimiser_limits import limited_frontier_payload

_SV = {
    "sv_mean": 1.0,
    "sv_std": 0.5,
    "sv_min": 0.1,
    "sv_p5": 0.2,
    "sv_p25": 0.6,
    "sv_median": 1.0,
    "sv_p75": 1.4,
    "sv_p95": 1.8,
    "sv_max": 2.0,
    "sv_pct_increase": 0.7,
    "sv_pct_decrease": 0.3,
}


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "threshold_volume": 5.0,
        "total_objective": 120.0,
        "total_volume": 5.2,
        "lambda_volume": 0.4,
        "iterations": 7,
        "converged": True,
        **_SV,
    }
    row.update(overrides)
    return row


def test_summary_reads_every_point_specific_field_from_the_library_row() -> None:
    summary = frontier_point_summary(_row(), ["volume"])

    assert summary == {
        "total_objective": 120.0,
        "constraints": {"volume": 5.2},
        "lambdas": {"volume": 0.4},
        "converged": True,
        "iterations": 7,
        "cd_iterations": None,
        "clamp_rate": None,
        "history": None,
        "scenario_value_stats": {
            "mean": 1.0,
            "std": 0.5,
            "min": 0.1,
            "p5": 0.2,
            "p25": 0.6,
            "p50": 1.0,
            "p75": 1.4,
            "p95": 1.8,
            "max": 2.0,
            "pct_increase": 0.7,
            "pct_decrease": 0.3,
        },
        "scenario_value_histogram": None,
        "factor_tables": None,
        "warning": None,
        "frontier_error": None,
    }


def test_a_non_converged_point_carries_the_warning() -> None:
    summary = frontier_point_summary(_row(converged=False), ["volume"])

    assert summary["converged"] is False
    assert summary["warning"] == NON_CONVERGED_WARNING


def test_applying_a_summary_replaces_point_fields_and_removes_absent_ones() -> None:
    base = {
        "mode": "online",
        "total_objective": 100.0,
        "baseline_objective": 90.0,
        "lambdas": {"volume": 0.1},
        "iterations": 3,
        "history": [{"iteration": 1}],
        "scenario_value_histogram": {"counts": [1], "edges": [0.0, 1.0]},
        "warning": "stale",
    }

    result = apply_frontier_point_summary(base, frontier_point_summary(_row(), ["volume"]))

    assert result["mode"] == "online"
    assert result["baseline_objective"] == 90.0
    assert result["total_objective"] == 120.0
    assert result["lambdas"] == {"volume": 0.4}
    assert result["iterations"] == 7
    for removed in ("history", "scenario_value_histogram", "warning", "factor_tables"):
        assert removed not in result


def test_the_frontier_payload_carries_one_summary_per_returned_point() -> None:
    points = pl.DataFrame([_row(total_objective=110.0), _row(total_objective=130.0)])

    payload = limited_frontier_payload(points, constraint_names=["volume"])

    assert [summary["total_objective"] for summary in payload["point_summaries"]] == [
        110.0,
        130.0,
    ]
    assert payload["point_summaries"][0] == frontier_point_summary(payload["points"][0], ["volume"])


@pytest.mark.parametrize(
    ("row", "status_code", "message"),
    [
        (_row(converged=None), 400, "Frontier point field 'converged' is missing"),
        ({k: v for k, v in _row().items() if k != "lambda_volume"}, 400, "no lambda values"),
        (_row(total_volume=float("nan")), 500, "'total_volume' is not finite"),
    ],
)
def test_a_point_that_cannot_be_summarised_fails_the_frontier(
    row: dict[str, object], status_code: int, message: str
) -> None:
    with pytest.raises(FrontierPointDataError, match=message) as exc:
        limited_frontier_payload(pl.DataFrame([row]), constraint_names=["volume"])

    assert exc.value.status_code == status_code
