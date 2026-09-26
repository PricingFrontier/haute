"""OPT-V10: the adjustment report, the distribution of chosen scenario values.

The pure half builds reports from in-memory choice frames through the same
``ScenarioHistogram`` reducer the routes use, and checks each figure against a
hand count. The route half drives real solves: the as-solved report computed at
finalize, a frontier point's report through ``POST /frontier/select`` with
``include_adjustments``, and the per-job cache of point reports.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from haute.routes._optimiser_adjustments import (
    ADJUSTMENT_REPORTS_KEY,
    MAX_CACHED_ADJUSTMENT_REPORTS,
    adjustment_report,
    cache_point_report,
)
from haute.routes._optimiser_outcomes import ChoiceFrameSpec, histogram_of_frame
from haute.routes.optimiser import _store
from haute.schemas import OptimiserAdjustmentReport
from tests.optimiser_fixtures import run_frontier_and_wait
from tests.test_optimiser_outcomes import _data_graph, _scored, _solved

# ---------------------------------------------------------------------------
# Pure: the statistical contract over hand-built choice frames
# ---------------------------------------------------------------------------


def _spec(grid: Sequence[float], constraints: Sequence[str] = ("volume",)) -> ChoiceFrameSpec:
    return ChoiceFrameSpec(
        mode="online",
        constraint_names=tuple(constraints),
        analysis_columns=(),
        scenario_grid=tuple((step, float(np.float32(value))) for step, value in enumerate(grid)),
        factor_columns=(),
    )


def _frame(
    grid: Sequence[float],
    steps: Sequence[int],
    *,
    objective: Sequence[float] | None = None,
    volume: Sequence[float] | None = None,
) -> pl.LazyFrame:
    """One chosen row per quote, as price-contour writes the apply frame."""
    n = len(steps)
    return pl.LazyFrame(
        {
            "quote_id": pl.Series([f"q{i:03d}" for i in range(n)], dtype=pl.String),
            "optimal_step": pl.Series(list(steps), dtype=pl.Int32),
            "optimal_scenario_value": pl.Series(
                [float(grid[step]) for step in steps], dtype=pl.Float32
            ),
            "optimal_objective": pl.Series(
                list(objective) if objective is not None else [1.0] * n, dtype=pl.Float32
            ),
            "optimal_volume": pl.Series(
                list(volume) if volume is not None else [1.0] * n, dtype=pl.Float32
            ),
        }
    )


def _report(
    grid: Sequence[float], steps: Sequence[int], **values: Sequence[float]
) -> OptimiserAdjustmentReport:
    spec = _spec(grid)
    return adjustment_report(histogram_of_frame(_frame(grid, steps, **values), spec), spec)


def _weighting(report: OptimiserAdjustmentReport, key: str) -> Any:
    matches = [weighting for weighting in report.weightings if weighting.key == key]
    assert len(matches) == 1, [weighting.key for weighting in report.weightings]
    return matches[0]


def _f32(value: float) -> float:
    return float(np.float32(value))


class TestBars:
    def test_a_float32_linspace_grid_has_one_bar_per_value_with_hand_counts(self) -> None:
        grid = np.linspace(0.8, 1.2, 5).astype(np.float32)
        report = _report(grid, [0, 0, 1, 2, 2, 2, 4])

        assert [bar.optimal_step for bar in report.bars] == [0, 1, 2, 3, 4]
        assert [bar.scenario_value for bar in report.bars] == [float(v) for v in grid]
        # Step 3 (1.1) was chosen by nobody and is still a bar, empty.
        assert [bar.quotes for bar in report.bars] == [2, 1, 3, 0, 1]
        assert report.n_quotes == 7

    def test_a_non_uniform_grid_is_counted_exactly_without_binning(self) -> None:
        report = _report([0.8, 1.0, 1.3], [2, 0, 2, 2, 1])

        assert [bar.scenario_value for bar in report.bars] == [_f32(0.8), 1.0, _f32(1.3)]
        assert [bar.quotes for bar in report.bars] == [1, 1, 3]
        assert report.has_unadjusted is True

    def test_empty_steps_have_zero_weight_under_every_weighting(self) -> None:
        report = _report([0.9, 1.0, 1.1], [0, 0], objective=[2.0, 3.0], volume=[1.0, 1.0])

        assert [bar.quotes for bar in report.bars] == [2, 0, 0]
        assert [bar.weights["optimal_objective"] for bar in report.bars] == [5.0, 0.0, 0.0]


class TestBasePrice:
    def test_an_unchosen_one_is_unadjusted_zero_and_a_grid_without_one_omits_it(self) -> None:
        # The same chosen rows (0.8, 1.2, 1.4 by step 0, 2, 3) under two grids: only
        # the recorded grid, never the rows, says whether 1.0 was possible.
        steps = [0, 2, 2, 3]
        with_one = [0.8, 1.0, 1.2, 1.4]
        without_one = [0.8, 0.95, 1.2, 1.4]
        spec_with, spec_without = _spec(with_one), _spec(without_one)
        frame = _frame(with_one, steps)
        assert frame.collect().equals(_frame(without_one, steps).collect())

        grid_one = adjustment_report(histogram_of_frame(frame, spec_with), spec_with)
        no_one = adjustment_report(histogram_of_frame(frame, spec_without), spec_without)

        quotes_one = _weighting(grid_one, "quotes")
        assert grid_one.has_unadjusted is True
        assert quotes_one.share_unadjusted == 0.0
        assert quotes_one.share_up == 0.75
        assert quotes_one.share_down == 0.25
        assert no_one.has_unadjusted is False
        assert _weighting(no_one, "quotes").share_unadjusted is None
        assert [bar.quotes for bar in no_one.bars] == [1, 0, 2, 1]
        assert no_one.bars[1].scenario_value == _f32(0.95)

    def test_exactly_one_is_neither_up_nor_down(self) -> None:
        at_one = _weighting(_report([0.9, 1.0, 1.1], [1, 1, 1]), "quotes")
        assert (at_one.share_up, at_one.share_down, at_one.share_unadjusted) == (0.0, 0.0, 1.0)

        below = _weighting(_report([0.9, 1.0, 1.1], [0, 0]), "quotes")
        above = _weighting(_report([0.9, 1.0, 1.1], [2]), "quotes")
        assert (below.share_down, below.share_up) == (1.0, 0.0)
        assert (above.share_up, above.share_down) == (1.0, 0.0)


class TestEdges:
    def test_unchosen_endpoints_have_zero_edge_shares_and_the_bars_span_the_grid(self) -> None:
        report = _report([0.8, 0.9, 1.0, 1.1, 1.2], [1, 2, 3, 2])
        quotes = _weighting(report, "quotes")

        assert (quotes.share_at_min, quotes.share_at_max) == (0.0, 0.0)
        assert [bar.scenario_value for bar in report.bars][0] == _f32(0.8)
        assert [bar.scenario_value for bar in report.bars][-1] == _f32(1.2)
        assert len(report.bars) == 5

    def test_edge_shares_are_the_weight_at_the_first_and_last_step(self) -> None:
        report = _report(
            [0.8, 0.9, 1.0, 1.1, 1.2],
            [0, 0, 0, 2, 4],
            objective=[1.0, 1.0, 1.0, 3.0, 4.0],
        )
        quotes = _weighting(report, "quotes")
        objective = _weighting(report, "optimal_objective")

        assert (quotes.share_at_min, quotes.share_at_max) == (0.6, 0.2)
        assert (objective.share_at_min, objective.share_at_max) == (0.3, 0.4)

    def test_a_one_step_grid_is_at_both_edges(self) -> None:
        quotes = _weighting(_report([1.0], [0, 0]), "quotes")
        assert (quotes.share_at_min, quotes.share_at_max, quotes.share_unadjusted) == (1, 1, 1)


class TestQuantilesAndMeans:
    GRID = (0.8, 0.9, 1.0, 1.1, 1.2)

    def test_unweighted_quantiles_are_the_lower_inverted_cdf_grid_values(self) -> None:
        quotes = _weighting(_report(self.GRID, [0, 1, 2, 3, 4]), "quotes")

        assert quotes.quantiles.model_dump() == {
            "p5": _f32(0.8),
            "p25": _f32(0.9),
            "p50": 1.0,
            "p75": _f32(1.1),
            "p95": _f32(1.2),
        }
        assert quotes.total == 5
        assert quotes.mean == pytest.approx(sum(_f32(v) for v in self.GRID) / 5, abs=1e-12)

    def test_weighted_quantiles_skip_zero_weight_steps_and_take_the_lower_value(self) -> None:
        # Objective weights 4, 0, 0, 1, 5 by step (one quote per step): W = 10.
        report = _report(self.GRID, [0, 1, 2, 3, 4], objective=[4.0, 0.0, 0.0, 1.0, 5.0])
        objective = _weighting(report, "optimal_objective")

        # p50: the cumulative weight reaches exactly 5 at 1.1, so the lower value is 1.1,
        # never the zero-weight 0.9 or 1.0 and never the next value 1.2.
        assert objective.quantiles.model_dump() == {
            "p5": _f32(0.8),
            "p25": _f32(0.8),
            "p50": _f32(1.1),
            "p75": _f32(1.2),
            "p95": _f32(1.2),
        }
        assert objective.total == 10.0
        expected_mean = (4 * _f32(0.8) + _f32(1.1) + 5 * _f32(1.2)) / 10
        assert objective.mean == pytest.approx(expected_mean, abs=1e-12)
        assert (objective.share_down, objective.share_up) == (0.4, 0.6)
        assert objective.share_unadjusted == 0.0
        assert objective.label == "Objective at the chosen scenario"
        assert _weighting(report, "optimal_volume").label == "volume at the chosen scenario"


class TestWeightGuard:
    def test_a_negative_value_refuses_that_weighting_by_name(self) -> None:
        # Step 0's volume sums to +4, so only a per-quote check sees the -1.
        report = _report([0.9, 1.0, 1.1], [0, 0, 2], volume=[-1.0, 5.0, 2.0])

        assert [weighting.key for weighting in report.weightings] == [
            "quotes",
            "optimal_objective",
        ]
        assert all("optimal_volume" not in bar.weights for bar in report.bars)
        [error] = report.diagnostics_errors
        assert error.diagnostic == "adjustment_weight"
        assert error.error_type == "NegativeWeight"
        assert "volume" in error.message
        assert "1 quote" in error.message

    def test_a_zero_total_refuses_that_weighting_by_name(self) -> None:
        report = _report([0.9, 1.0, 1.1], [0, 1], objective=[0.0, 0.0])

        assert [weighting.key for weighting in report.weightings] == ["quotes", "optimal_volume"]
        [error] = report.diagnostics_errors
        assert (error.diagnostic, error.error_type) == ("adjustment_weight", "ZeroTotalWeight")
        assert "objective" in error.message.lower()

    def test_quote_count_always_exists_and_no_quotes_fails_loudly(self) -> None:
        spec = _spec([0.9, 1.0])
        with pytest.raises(ValueError, match="no quotes"):
            adjustment_report(histogram_of_frame(_frame([0.9, 1.0], []), spec), spec)


class TestContract:
    def test_the_report_is_strict(self) -> None:
        payload = _report([0.9, 1.0], [0, 1]).model_dump()
        with pytest.raises(ValidationError):
            OptimiserAdjustmentReport.model_validate({**payload, "unexpected": 1})
        with pytest.raises(ValidationError):
            OptimiserAdjustmentReport.model_validate({**payload, "has_unadjusted": False})

    def test_the_point_cache_keeps_the_newest_sixty_four(self) -> None:
        report = _report([0.9, 1.0], [0, 1]).model_dump()
        cache: dict[tuple[int, int], dict[str, Any]] = {}
        for point in range(MAX_CACHED_ADJUSTMENT_REPORTS + 1):
            cache = cache_point_report(cache, (0, point), report)

        assert len(cache) == MAX_CACHED_ADJUSTMENT_REPORTS
        assert (0, 0) not in cache
        assert (0, MAX_CACHED_ADJUSTMENT_REPORTS) in cache


# ---------------------------------------------------------------------------
# Routes: real solves
# ---------------------------------------------------------------------------


def _file_report(job_id: str, key: str) -> dict[str, Any]:
    """The report of the job's apply artifact under *key*, built directly from its parquet."""
    from haute.routes._optimiser_outcomes import _choice_spec

    job = _store.require_job(job_id)
    spec = _choice_spec(job)
    frame = pl.scan_parquet(job["artifact_handles"][key]["path"])
    return adjustment_report(histogram_of_frame(frame, spec), spec).model_dump()


def _select(client: Any, job_id: str, point: int | None, **extra: Any) -> Any:
    return client.post(
        "/api/optimiser/frontier/select",
        json={"job_id": job_id, "point_index": point, **extra},
    )


def _with_frontier(client: Any, tmp_path: Any) -> tuple[str, dict[str, Any]]:
    job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))
    frontier = run_frontier_and_wait(
        client,
        {"job_id": job_id, "threshold_ranges": {"volume": [5.0, 8.0]}, "n_points_per_dim": 3},
    )
    assert frontier["status"] == "completed", frontier
    return job_id, frontier["result"]


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestAsSolved:
    def test_finalize_reports_the_as_solved_choices(self, client, tmp_path):
        job_id, status = _solved(client, _data_graph(_scored(tmp_path)))
        report = status["result"]["adjustments"]

        assert report == _file_report(job_id, "apply_result")
        assert report["n_quotes"] == 9
        assert [bar["scenario_value"] for bar in report["bars"]] == [
            step["scenario_value"] for step in status["result"]["scenario_grid"]
        ]
        objective = next(w for w in report["weightings"] if w["key"] == "optimal_objective")
        assert objective["total"] == pytest.approx(status["result"]["total_objective"], rel=1e-9)
        assert not any(
            error["diagnostic"] == "adjustments" for error in status["result"]["diagnostics_errors"]
        )

    def test_a_report_that_cannot_be_built_is_a_diagnostics_error(
        self, client, tmp_path, monkeypatch
    ):
        from haute.routes import _optimiser_solver

        def broken(*_args: Any, **_kwargs: Any) -> Any:
            raise ValueError("chosen step outside the grid")

        monkeypatch.setattr(_optimiser_solver, "histogram_of_frame", broken)
        _job_id, status = _solved(client, _data_graph(_scored(tmp_path)))

        assert status["result"]["adjustments"] is None
        [error] = [
            error
            for error in status["result"]["diagnostics_errors"]
            if error["diagnostic"] == "adjustments"
        ]
        assert error["message"] == "chosen step outside the grid"

    def test_select_without_a_point_answers_the_as_solved_report(self, client, tmp_path):
        job_id, status = _solved(client, _data_graph(_scored(tmp_path)))
        response = _select(client, job_id, None)
        assert response.status_code == 200, response.text
        assert response.json()["adjustments"] == status["result"]["adjustments"]


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestFrontierPoint:
    def test_a_point_report_equals_the_report_of_that_points_apply(self, client, tmp_path):
        job_id, frontier = _with_frontier(client, tmp_path)

        response = _select(client, job_id, 2, include_adjustments=True)

        assert response.status_code == 200, response.text
        report = response.json()["adjustments"]
        assert report == _file_report(job_id, "frontier_apply_result:2")
        objective = next(w for w in report["weightings"] if w["key"] == "optimal_objective")
        volume = next(w for w in report["weightings"] if w["key"] == "optimal_volume")
        point = frontier["points"][2]
        assert objective["total"] == pytest.approx(point["total_objective"], rel=1e-9)
        assert volume["total"] == pytest.approx(point["totals"]["volume"], rel=1e-9)
        # The point is selected as before, and its summary carries no report.
        job = _store.require_job(job_id)
        assert job["selected_frontier_point"] == 2
        assert "adjustments" not in job["result"]
        assert frontier["point_summaries"][2]["adjustments"] is None

    def test_without_the_flag_a_point_answers_no_report(self, client, tmp_path):
        job_id, _frontier = _with_frontier(client, tmp_path)
        response = _select(client, job_id, 1)
        assert response.status_code == 200, response.text
        assert response.json()["adjustments"] is None
        assert "frontier_apply_result:1" not in _store.require_job(job_id)["artifact_handles"]

    def test_a_cached_report_is_served_after_the_grid_and_artifact_are_gone(self, client, tmp_path):
        job_id, _frontier = _with_frontier(client, tmp_path)
        first = _select(client, job_id, 1, include_adjustments=True).json()["adjustments"]
        generation = _store.require_job(job_id)["frontier_generation"]
        assert _store.require_job(job_id)[ADJUSTMENT_REPORTS_KEY] == {(generation, 1): first}

        # Drop the point's artifact and the heavy state (the quote grid).
        handles = dict(_store.require_job(job_id)["artifact_handles"])
        handles.pop("frontier_apply_result:1")
        _store.atomic_update(job_id, {"artifact_handles": handles}, expected_status="completed")
        _store.clear_result_data(job_id)
        assert _store.require_job(job_id).get("quote_grid") is None

        again = _select(client, job_id, 1, include_adjustments=True)
        assert again.status_code == 200, again.text
        assert again.json()["adjustments"] == first
        # An uncached point without its artifact or the grid is the named 410.
        gone = _select(client, job_id, 0, include_adjustments=True)
        assert gone.status_code == 410
        assert gone.json()["detail"]["error_code"] == "frontier_point_unavailable"

    def test_a_retained_point_is_reported_after_grid_eviction_from_the_recorded_grid(
        self, client, tmp_path
    ):
        job_id, _frontier = _with_frontier(client, tmp_path)
        first = _select(client, job_id, 2, include_adjustments=True).json()["adjustments"]
        _store.atomic_update(job_id, {ADJUSTMENT_REPORTS_KEY: {}}, expected_status="completed")
        _store.clear_result_data(job_id)

        again = _select(client, job_id, 2, include_adjustments=True)

        assert again.status_code == 200, again.text
        assert again.json()["adjustments"] == first

    def test_a_recompute_invalidates_the_cached_reports(self, client, tmp_path):
        job_id, _frontier = _with_frontier(client, tmp_path)
        _select(client, job_id, 1, include_adjustments=True)
        generation = _store.require_job(job_id)["frontier_generation"]
        assert set(_store.require_job(job_id)[ADJUSTMENT_REPORTS_KEY]) == {(generation, 1)}

        recomputed = run_frontier_and_wait(
            client,
            {"job_id": job_id, "threshold_ranges": {"volume": [6.0, 7.0]}, "n_points_per_dim": 3},
        )
        assert recomputed["status"] == "completed", recomputed
        assert _store.require_job(job_id)[ADJUSTMENT_REPORTS_KEY] == {}

        response = _select(client, job_id, 1, include_adjustments=True)
        assert response.status_code == 200, response.text
        assert response.json()["adjustments"] == _file_report(job_id, "frontier_apply_result:1")
        assert set(_store.require_job(job_id)[ADJUSTMENT_REPORTS_KEY]) == {(generation + 1, 1)}

    def test_a_recompute_during_the_query_is_a_conflict_and_caches_nothing(
        self, client, tmp_path, monkeypatch
    ):
        from haute.routes import optimiser as optimiser_routes

        job_id, _frontier = _with_frontier(client, tmp_path)
        real_query = optimiser_routes._choice_service.choice_query

        def query_then_recompute(*args: Any, **kwargs: Any) -> Any:
            result = real_query(*args, **kwargs)
            generation = _store.require_job(job_id)["frontier_generation"]
            _store.atomic_update(
                job_id, {"frontier_generation": generation + 1}, expected_status="completed"
            )
            return result

        monkeypatch.setattr(optimiser_routes._choice_service, "choice_query", query_then_recompute)
        response = _select(client, job_id, 1, include_adjustments=True)

        assert response.status_code == 409
        assert not _store.require_job(job_id).get(ADJUSTMENT_REPORTS_KEY)
