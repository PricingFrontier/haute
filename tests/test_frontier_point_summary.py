"""The one derivation of a frontier point's solve summary (OPT-P18)."""

from __future__ import annotations

import polars as pl
import pytest
from pydantic import ValidationError

from haute.routes._frontier_point_summary import (
    NON_CONVERGED_WARNING,
    FrontierPointDataError,
    apply_frontier_point_summary,
    constraint_kinds,
    effective_bounds,
    frontier_point_summary,
)
from haute.routes._optimiser_limits import limited_frontier_payload
from haute.schemas import OptimiserFrontierResponse

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


_KINDS = {"volume": "min"}


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "threshold_volume": 5.0,
        "bound_volume": 5.0,
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
    summary = frontier_point_summary(_row(), _KINDS)

    assert summary == {
        "total_objective": 120.0,
        "constraints": {"volume": 5.2},
        "effective_bounds": {"volume": {"kind": "min", "bound": 5.0}},
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
    summary = frontier_point_summary(_row(converged=False), _KINDS)

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

    result = apply_frontier_point_summary(base, frontier_point_summary(_row(), _KINDS))

    assert result["mode"] == "online"
    assert result["baseline_objective"] == 90.0
    assert result["total_objective"] == 120.0
    assert result["lambdas"] == {"volume": 0.4}
    assert result["iterations"] == 7
    for removed in ("history", "scenario_value_histogram", "warning", "factor_tables"):
        assert removed not in result


def test_the_frontier_payload_carries_one_summary_per_returned_point() -> None:
    points = pl.DataFrame([_row(total_objective=110.0), _row(total_objective=130.0)])

    payload = limited_frontier_payload(
        points, constraint_kinds=_KINDS, swept_axes=["volume"], frontier_generation=0
    )

    assert [summary["total_objective"] for summary in payload["point_summaries"]] == [
        110.0,
        130.0,
    ]
    assert payload["point_summaries"][0] == frontier_point_summary(payload["points"][0], _KINDS)


# ---------------------------------------------------------------------------
# OPT-V02: every computed frontier reports the job's frontier generation
# ---------------------------------------------------------------------------


def test_the_frontier_payload_reports_its_generation() -> None:
    payload = limited_frontier_payload(
        pl.DataFrame([_row()]),
        constraint_kinds=_KINDS,
        swept_axes=["volume"],
        frontier_generation=3,
    )

    assert payload["frontier_generation"] == 3
    assert OptimiserFrontierResponse(**payload).frontier_generation == 3


def test_a_computed_frontier_without_a_generation_is_rejected() -> None:
    payload = limited_frontier_payload(
        pl.DataFrame([_row()]),
        constraint_kinds=_KINDS,
        swept_axes=["volume"],
        frontier_generation=0,
    )
    del payload["frontier_generation"]

    with pytest.raises(ValidationError, match="frontier_generation"):
        OptimiserFrontierResponse(**payload)


def test_a_negative_generation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="frontier_generation"):
        OptimiserFrontierResponse(status="ok", frontier_generation=-1)


def test_only_the_started_handle_has_no_generation() -> None:
    started = OptimiserFrontierResponse(status="started", job_id="sweep_1")
    assert started.frontier_generation is None

    with pytest.raises(ValidationError, match="frontier_generation"):
        OptimiserFrontierResponse(status="started", job_id="sweep_1", frontier_generation=0)


@pytest.mark.parametrize(
    ("row", "status_code", "message"),
    [
        (_row(converged=None), 400, "Frontier point field 'converged' is missing"),
        ({k: v for k, v in _row().items() if k != "lambda_volume"}, 400, "no lambda values"),
        (_row(total_volume=float("nan")), 500, "'total_volume' is not finite"),
        (
            {k: v for k, v in _row().items() if k != "bound_volume"},
            500,
            "'bound_volume' is missing",
        ),
        (_row(bound_volume=float("inf")), 500, "'bound_volume' is not finite"),
    ],
)
def test_a_point_that_cannot_be_summarised_fails_the_frontier(
    row: dict[str, object], status_code: int, message: str
) -> None:
    with pytest.raises(FrontierPointDataError, match=message) as exc:
        limited_frontier_payload(
            pl.DataFrame([row]),
            constraint_kinds=_KINDS,
            swept_axes=["volume"],
            frontier_generation=0,
        )

    assert exc.value.status_code == status_code


# ---------------------------------------------------------------------------
# OPT-V01: every configured constraint, swept or not, with its absolute bound
# ---------------------------------------------------------------------------


def _two_constraint_row(**overrides: object) -> dict[str, object]:
    """A library row sweeping ``volume`` only: ``margin`` has no threshold but
    still carries its total, λ and absolute bound, as price-contour 0.5 emits."""
    return _row(total_margin=40.0, lambda_margin=0.0, bound_margin=45.0, **overrides)


_TWO_KINDS = {"volume": "min", "margin": "max"}


def test_a_summary_carries_every_configured_constraint_swept_or_not() -> None:
    summary = frontier_point_summary(_two_constraint_row(), _TWO_KINDS)

    assert summary["constraints"] == {"volume": 5.2, "margin": 40.0}
    assert summary["lambdas"] == {"volume": 0.4, "margin": 0.0}
    assert summary["effective_bounds"] == {
        "volume": {"kind": "min", "bound": 5.0},
        "margin": {"kind": "max", "bound": 45.0},
    }
    # Configured order, not the row's column order.
    assert list(summary["effective_bounds"]) == ["volume", "margin"]


def test_the_bound_is_the_library_absolute_bound_not_the_fractional_threshold() -> None:
    """For a pct constraint ``threshold_<c>`` is the user's fraction; the
    summary reports the library's absolute ``bound_<c>`` and never rescales."""
    row = _row(threshold_volume=0.95, bound_volume=4.75)

    summary = frontier_point_summary(row, {"volume": "min"})

    assert summary["effective_bounds"] == {"volume": {"kind": "min", "bound": 4.75}}


def test_the_frontier_payload_lists_every_constraint_and_the_swept_axes_apart() -> None:
    payload = limited_frontier_payload(
        pl.DataFrame([_two_constraint_row()]),
        constraint_kinds=_TWO_KINDS,
        swept_axes=["volume"],
        frontier_generation=0,
    )

    assert payload["constraint_names"] == ["volume", "margin"]
    assert payload["swept_axes"] == ["volume"]
    assert set(payload["point_summaries"][0]["constraints"]) == {"volume", "margin"}


def test_a_swept_axis_that_is_not_a_configured_constraint_is_rejected() -> None:
    with pytest.raises(ValueError, match="not configured constraints: \\['conversion'\\]"):
        limited_frontier_payload(
            pl.DataFrame([_row()]),
            constraint_kinds=_KINDS,
            swept_axes=["conversion"],
            frontier_generation=0,
        )


@pytest.mark.parametrize(
    ("spec", "kind"),
    [
        ({"min": 1.0}, "min"),
        ({"min_pct": 0.95}, "min"),
        ({"max": 2.0}, "max"),
        ({"max_pct": 1.05}, "max"),
    ],
)
def test_constraint_kinds_come_from_the_configured_threshold_key(
    spec: dict[str, float], kind: str
) -> None:
    assert constraint_kinds({"volume": spec}) == {"volume": kind}


@pytest.mark.parametrize(
    "constraints",
    [
        {"volume": {}},
        {"volume": {"min": 1.0, "max": 2.0}},
        {"volume": {"target": 1.0}},
        {"volume": 1.0},
        [("volume", {"min": 1.0})],
    ],
)
def test_constraint_kinds_reject_a_spec_without_exactly_one_threshold(constraints: object) -> None:
    with pytest.raises(ValueError, match="constraint"):
        constraint_kinds(constraints)


def test_effective_bounds_pair_each_kind_with_the_library_bound_in_configured_order() -> None:
    bounds = effective_bounds(
        {"volume": "min", "margin": "max"},
        {"margin": 45.0, "volume": 5.0},
    )

    assert bounds == {
        "volume": {"kind": "min", "bound": 5.0},
        "margin": {"kind": "max", "bound": 45.0},
    }
    assert list(bounds) == ["volume", "margin"]


@pytest.mark.parametrize(
    ("library_bounds", "message"),
    [
        ({"volume": 5.0}, "margin"),
        ({"volume": 5.0, "margin": 45.0, "extra": 1.0}, "extra"),
        ({"volume": 5.0, "margin": float("nan")}, "not finite"),
        ({"volume": 5.0, "margin": None}, "margin"),
    ],
)
def test_effective_bounds_fail_loudly_when_the_library_bounds_do_not_match(
    library_bounds: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        effective_bounds({"volume": "min", "margin": "max"}, library_bounds)
