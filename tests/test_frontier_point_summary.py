"""Typed frontier points and the one derivation of a point's solve summary.

A price-contour frontier row becomes a typed ``OptimiserFrontierPoint`` once,
when the frontier payload is built (OPT-V04); its summary (OPT-P18) is derived
from that typed point only.
"""

from __future__ import annotations

import price_contour
import pytest
from pydantic import ValidationError

from haute.routes._frontier_point_summary import (
    NON_CONVERGED_WARNING,
    FrontierPointDataError,
    apply_frontier_point_summary,
    constraint_kinds,
    effective_bounds,
    frontier_point_library_row,
    frontier_point_rows,
    frontier_point_summary,
)
from haute.routes._optimiser_limits import limited_frontier_payload
from haute.schemas import OptimiserFrontierResponse
from tests.optimiser_fixtures import SV_STATS, library_frontier_frame

_KINDS = {"volume": "min"}

_ONLINE_ROW = {
    "threshold_volume": 5.0,
    "bound_volume": 5.0,
    "total_objective": 120.0,
    "total_volume": 5.2,
    "lambda_volume": 0.4,
    "iterations": 7,
    "converged": True,
}


def _online_frame(**overrides: object):
    return library_frontier_frame([{**_ONLINE_ROW, **overrides}])


def _online_point(**overrides: object) -> dict[str, object]:
    return frontier_point_rows(
        _online_frame(**overrides), mode="online", constraint_names=["volume"]
    )[0]


def _ratebook_point(**overrides: object) -> dict[str, object]:
    frame = library_frontier_frame(
        [{**_ONLINE_ROW, "clamp_rate": 0.25, "n_quotes_clamped_low": 2, **overrides}],
        mode="ratebook",
    )
    return frontier_point_rows(frame, mode="ratebook", constraint_names=["volume"])[0]


# ---------------------------------------------------------------------------
# Library frame -> typed points
# ---------------------------------------------------------------------------


def test_an_online_library_row_becomes_a_typed_point_with_constraint_maps() -> None:
    assert _online_point() == {
        "mode": "online",
        "total_objective": 120.0,
        "thresholds": {"volume": 5.0},
        "bounds": {"volume": 5.0},
        "totals": {"volume": 5.2},
        "lambdas": {"volume": 0.4},
        "iterations": 7,
        "converged": True,
        "solver_path": "bisection",
        "non_convergence_reason": None,
        **SV_STATS,
    }


def test_a_ratebook_library_row_carries_its_clamp_diagnostics_and_no_sv_columns() -> None:
    point = _ratebook_point()

    assert point["mode"] == "ratebook"
    assert point["clamp_rate"] == 0.25
    assert point["n_quotes_clamped_low"] == 2
    assert point["n_quotes_clamped_high"] == 0
    assert not any(key.startswith("sv_") for key in point)
    assert "solver_path" not in point


def test_every_configured_constraint_becomes_a_map_key_in_configured_order() -> None:
    frame = library_frontier_frame(
        [{"total_volume": 5.2, "total_margin": 40.0, "bound_margin": 45.0, "lambda_margin": 0.0}],
        constraint_names=["volume", "margin"],
    )

    (point,) = frontier_point_rows(frame, mode="online", constraint_names=["volume", "margin"])

    for field in ("thresholds", "bounds", "totals", "lambdas"):
        assert list(point[field]) == ["volume", "margin"], field
    assert point["bounds"]["margin"] == 45.0


@pytest.mark.parametrize(
    ("drop", "add", "message"),
    [
        ("lambda_volume", None, r"missing \['lambda_volume'\]"),
        ("sv_median", None, r"missing \['sv_median'\]"),
        (None, "cd_iterations", r"unexpected \['cd_iterations'\]"),
    ],
)
def test_a_frame_that_is_not_the_library_schema_fails_loudly(
    drop: str | None, add: str | None, message: str
) -> None:
    frame = _online_frame()
    if drop is not None:
        frame = frame.drop(drop)
    if add is not None:
        frame = frame.with_columns(frame["iterations"].alias(add))

    with pytest.raises(FrontierPointDataError, match=message) as exc:
        frontier_point_rows(frame, mode="online", constraint_names=["volume"])
    assert exc.value.status_code == 500


def test_an_online_frame_is_not_a_ratebook_frame() -> None:
    with pytest.raises(FrontierPointDataError, match="clamp_rate"):
        frontier_point_rows(_online_frame(), mode="ratebook", constraint_names=["volume"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_volume": float("nan")},
        {"bound_volume": float("inf")},
        {"sv_mean": None},
        {"total_objective": None},
        {"solver_path": "newton"},
    ],
)
def test_a_row_with_a_missing_or_non_finite_value_fails_loudly(overrides: dict) -> None:
    with pytest.raises(FrontierPointDataError, match="Frontier point 0 is malformed") as exc:
        _online_point(**overrides)
    assert exc.value.status_code == 500


def test_a_typed_point_writes_back_as_the_library_row_in_schema_order() -> None:
    frame = library_frontier_frame(
        [{"total_volume": 5.2, "total_margin": 40.0}], constraint_names=["volume", "margin"]
    )
    (point,) = frontier_point_rows(frame, mode="online", constraint_names=["volume", "margin"])

    row = frontier_point_library_row(point, ["volume", "margin"])

    assert row == frame.to_dicts()[0]
    assert list(row) == list(price_contour.frontier_points_schema("online", ["volume", "margin"]))


# ---------------------------------------------------------------------------
# Point summaries
# ---------------------------------------------------------------------------


def test_summary_reads_every_point_specific_field_from_the_typed_point() -> None:
    summary = frontier_point_summary(_online_point(), _KINDS)

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
        "ratebook_cd_trace": None,
        "adjustments": None,
        "factor_tables": None,
        "warning": None,
        "frontier_error": None,
        "diagnostics_errors": [],
    }


def test_a_ratebook_summary_reports_the_clamp_rate_and_no_report() -> None:
    summary = frontier_point_summary(_ratebook_point(), _KINDS)

    assert summary["clamp_rate"] == 0.25
    assert summary["adjustments"] is None
    assert summary["iterations"] == 7


def test_a_non_converged_point_carries_the_warning() -> None:
    summary = frontier_point_summary(
        _online_point(converged=False, non_convergence_reason="bracket_exhausted"), _KINDS
    )

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
        "adjustments": {"n_quotes": 3},
        "warning": "stale",
        "diagnostics_errors": [
            {"diagnostic": "adjustments", "error_type": "ValueError", "message": "x"}
        ],
    }

    result = apply_frontier_point_summary(base, frontier_point_summary(_online_point(), _KINDS))

    assert result["mode"] == "online"
    assert result["baseline_objective"] == 90.0
    assert result["total_objective"] == 120.0
    assert result["lambdas"] == {"volume": 0.4}
    assert result["iterations"] == 7
    # The solve's degraded diagnostics do not describe a point.
    assert result["diagnostics_errors"] == []
    # The solve's adjustment report does not describe a point: its own loads on request.
    for removed in ("history", "adjustments", "warning", "factor_tables"):
        assert removed not in result


def test_a_summary_carries_every_configured_constraint_swept_or_not() -> None:
    frame = library_frontier_frame(
        [
            {
                "total_volume": 5.2,
                "bound_volume": 5.0,
                "lambda_volume": 0.4,
                "total_margin": 40.0,
                "lambda_margin": 0.0,
                "bound_margin": 45.0,
            }
        ],
        constraint_names=["margin", "volume"],
    )
    (point,) = frontier_point_rows(frame, mode="online", constraint_names=["margin", "volume"])

    summary = frontier_point_summary(point, {"volume": "min", "margin": "max"})

    assert summary["constraints"] == {"volume": 5.2, "margin": 40.0}
    assert summary["lambdas"] == {"volume": 0.4, "margin": 0.0}
    assert summary["effective_bounds"] == {
        "volume": {"kind": "min", "bound": 5.0},
        "margin": {"kind": "max", "bound": 45.0},
    }
    # Configured order, not the point's map order.
    assert list(summary["effective_bounds"]) == ["volume", "margin"]
    assert list(summary["lambdas"]) == ["volume", "margin"]


def test_the_bound_is_the_library_absolute_bound_not_the_fractional_threshold() -> None:
    """For a pct constraint ``threshold_<c>`` is the user's fraction; the
    summary reports the library's absolute ``bound_<c>`` and never rescales."""
    point = _online_point(threshold_volume=0.95, bound_volume=4.75)

    summary = frontier_point_summary(point, {"volume": "min"})

    assert summary["effective_bounds"] == {"volume": {"kind": "min", "bound": 4.75}}


# ---------------------------------------------------------------------------
# The frontier payload
# ---------------------------------------------------------------------------


def _payload(frame=None, *, mode: str = "online", kinds=None, swept=None, generation: int = 0):
    return limited_frontier_payload(
        frame if frame is not None else _online_frame(),
        mode=mode,
        constraint_kinds=kinds if kinds is not None else _KINDS,
        swept_axes=swept if swept is not None else ["volume"],
        frontier_generation=generation,
    )


def test_the_frontier_payload_carries_typed_points_and_one_summary_per_point() -> None:
    frame = library_frontier_frame(
        [{**_ONLINE_ROW, "total_objective": 110.0}, {**_ONLINE_ROW, "total_objective": 130.0}]
    )

    payload = _payload(frame)

    assert [point["total_objective"] for point in payload["points"]] == [110.0, 130.0]
    assert payload["points"][0]["totals"] == {"volume": 5.2}
    assert [summary["total_objective"] for summary in payload["point_summaries"]] == [
        110.0,
        130.0,
    ]
    assert payload["point_summaries"][0] == frontier_point_summary(payload["points"][0], _KINDS)
    OptimiserFrontierResponse.model_validate(payload)


def test_a_ratebook_payload_validates_as_ratebook_points() -> None:
    frame = library_frontier_frame([_ONLINE_ROW], mode="ratebook")

    response = OptimiserFrontierResponse.model_validate(_payload(frame, mode="ratebook"))

    assert response.points[0].mode == "ratebook"


def test_the_frontier_payload_lists_every_constraint_and_the_swept_axes_apart() -> None:
    frame = library_frontier_frame([{}], constraint_names=["volume", "margin"])

    payload = _payload(frame, kinds={"volume": "min", "margin": "max"})

    assert payload["constraint_names"] == ["volume", "margin"]
    assert payload["swept_axes"] == ["volume"]
    assert set(payload["point_summaries"][0]["constraints"]) == {"volume", "margin"}


def test_a_swept_axis_that_is_not_a_configured_constraint_is_rejected() -> None:
    with pytest.raises(ValueError, match="not configured constraints: \\['conversion'\\]"):
        _payload(swept=["conversion"])


def test_a_frame_for_other_constraints_fails_the_frontier() -> None:
    frame = library_frontier_frame([{}], constraint_names=["margin"])

    with pytest.raises(FrontierPointDataError, match="missing"):
        _payload(frame)


# ---------------------------------------------------------------------------
# OPT-V02: every computed frontier reports the job's frontier generation
# ---------------------------------------------------------------------------


def test_the_frontier_payload_reports_its_generation() -> None:
    payload = _payload(generation=3)

    assert payload["frontier_generation"] == 3
    assert OptimiserFrontierResponse(**payload).frontier_generation == 3


def test_a_computed_frontier_without_a_generation_is_rejected() -> None:
    payload = _payload()
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


# ---------------------------------------------------------------------------
# Constraint kinds and bounds
# ---------------------------------------------------------------------------


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
