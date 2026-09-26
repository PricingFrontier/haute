"""OPT-V12: the Quotes explorer over the chosen scenarios.

The pure half runs the page reducers over hand-built, in-memory choice frames
(the same ``ChoiceFrames`` the routes lease) and checks each page against a
hand-ordered expectation. The route half drives real price-contour solves
through ``POST /api/optimiser/apply``: the default page, sorting by an analysis
column, the limit cap, a frontier point, the at-range-edge filter after the
quote grid is evicted, admission refusal and a ratebook page.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from haute.routes._optimiser_limits import APPLY_PREVIEW_ROW_LIMIT, QUOTE_PAGE_DEPTH_LIMIT
from haute.routes._optimiser_outcomes import (
    ANALYSIS_ROW_PRESENT_COLUMN,
    ChoiceFrames,
    ChoiceFrameSpec,
    _choice_frame,
)
from haute.routes._optimiser_quotes import (
    AnalysisQuotePage,
    QuoteFilters,
    QuotePage,
    quote_columns,
    quote_page,
)
from haute.routes.optimiser import _store
from tests.optimiser_fixtures import run_frontier_and_wait
from tests.test_optimiser_outcomes import _data_graph, _region, _scored, _solved

_GRID = (0.9, 1.0, 1.1, 1.2)


def _f32(value: float) -> float:
    return float(np.float32(value))


def _spec(
    *,
    analysis: Sequence[str] = ("region",),
    mode: str = "online",
    grid: Sequence[float] = _GRID,
) -> ChoiceFrameSpec:
    return ChoiceFrameSpec(
        mode=mode,  # type: ignore[arg-type]
        constraint_names=("volume",),
        analysis_columns=tuple(analysis),
        scenario_grid=tuple((step, _f32(value)) for step, value in enumerate(grid)),
        factor_columns=(),
    )


# Eight quotes, deliberately not in quote-id order, so a page that lost the
# quote-id tie-break would come back in this frame order instead.
_QUOTES = ("q05", "q02", "q07", "q00", "q03", "q06", "q01", "q04")
_STEPS = (3, 0, 1, 3, 2, 0, 1, 3)
_OBJECTIVE = (3.0, 5.0, 5.0, 5.0, 1.0, 2.0, 3.0, 5.0)
_REGION = ("North", "South", None, "North", "South", "North", "East", None)
_TIER = (1, 2, 3, 1, 2, 3, 1, 2)


def _frames(*, mode: str = "online", analysis: bool = True) -> tuple[ChoiceFrames, int]:
    """The leased tables of eight chosen quotes, as ``choice_query`` hands them to a reducer."""
    n = len(_QUOTES)
    apply: dict[str, pl.Series] = {
        "quote_id": pl.Series(_QUOTES, dtype=pl.String),
        "optimal_step": pl.Series(_STEPS, dtype=pl.Int32),
        "optimal_scenario_value": pl.Series([_GRID[s] for s in _STEPS], dtype=pl.Float32),
        "optimal_objective": pl.Series(_OBJECTIVE, dtype=pl.Float32),
        "optimal_volume": pl.Series([1.0] * n, dtype=pl.Float32),
    }
    if mode == "ratebook":
        # q07 (1.02 at 1.0) and q06 (0.93 at 0.9) are within-range rounding (flagged); q05's
        # product is past the grid end and clamped high (not flagged).
        apply["factor_product"] = pl.Series(
            [1.35, 0.9, 1.02, 1.2, 1.1, 0.93, 1.0, 1.2], dtype=pl.Float32
        )
        apply["clamped_low"] = pl.Series([False] * n)
        apply["clamped_high"] = pl.Series([True, False, False, False, False, False, False, False])
    spec = _spec(mode=mode, analysis=("region", "tier") if analysis else ())
    choice = _choice_frame(pl.LazyFrame(apply), spec)
    side = None
    if analysis:
        side = pl.LazyFrame(
            {
                # The side table's own order differs from the apply frame's.
                "quote_id": pl.Series(sorted(_QUOTES), dtype=pl.String),
                "region": pl.Series(
                    [dict(zip(_QUOTES, _REGION, strict=True))[q] for q in sorted(_QUOTES)],
                    dtype=pl.String,
                ),
                "tier": pl.Series(
                    [dict(zip(_QUOTES, _TIER, strict=True))[q] for q in sorted(_QUOTES)],
                    dtype=pl.Int64,
                ),
                ANALYSIS_ROW_PRESENT_COLUMN: pl.Series([True] * n),
            }
        )
    frames = ChoiceFrames(
        choice=choice,
        analysis=side,
        analysis_key="quote_id",
        collect=lambda plan: plan.collect(),
    )
    return frames, n


def _page(
    *,
    mode: str = "online",
    analysis: bool = True,
    **query: Any,
) -> Any:
    frames, n = _frames(mode=mode, analysis=analysis)
    spec = _spec(mode=mode, analysis=("region", "tier") if analysis else ())
    reducer = quote_page(spec, **query)
    reducer.validate(spec)
    return reducer.run(frames, spec, n)


def _ids(result: Any) -> list[str]:
    return list(result.rows["quote_id"].to_list())


# ---------------------------------------------------------------------------
# Pure: pages over hand-built choice frames
# ---------------------------------------------------------------------------


class TestSortWithTies:
    def test_descending_ties_are_paged_in_quote_id_order(self) -> None:
        result = _page(sort_by="optimal_objective", descending=True)
        # 5.0 x4 (q00 q02 q04 q07), 3.0 x2 (q01 q05), 2.0 (q06), 1.0 (q03).
        assert _ids(result) == ["q00", "q02", "q04", "q07", "q01", "q05", "q06", "q03"]
        assert result.total == 8
        assert result.quotes == 8

    def test_ascending_ties_are_also_in_quote_id_order(self) -> None:
        result = _page(sort_by="optimal_objective", descending=False)
        assert _ids(result) == ["q03", "q06", "q01", "q05", "q00", "q02", "q04", "q07"]

    def test_a_page_inside_a_run_of_ties_is_stable(self) -> None:
        result = _page(sort_by="optimal_objective", descending=True, offset=2, limit=3)
        assert _ids(result) == ["q04", "q07", "q01"]

    def test_no_sort_keeps_the_apply_frames_quote_order(self) -> None:
        result = _page(offset=1, limit=3)
        assert _ids(result) == ["q02", "q07", "q00"]
        assert not quote_page(_spec()).scans_every_quote()

    def test_an_analysis_sort_joins_every_quote_and_puts_nulls_last(self) -> None:
        reducer = quote_page(_spec(analysis=("region", "tier")), sort_by="region")
        assert isinstance(reducer, AnalysisQuotePage)
        assert reducer.joins_every_quote

        ascending = _page(sort_by="region")
        # East(q01); North(q00 q05 q06); South(q02 q03); null(q04 q07) last.
        assert _ids(ascending) == ["q01", "q00", "q05", "q06", "q02", "q03", "q04", "q07"]
        descending = _page(sort_by="region", descending=True)
        assert _ids(descending) == ["q02", "q03", "q00", "q05", "q06", "q01", "q04", "q07"]

    def test_a_ratebook_page_sorts_by_the_factor_product(self) -> None:
        result = _page(mode="ratebook", sort_by="factor_product", descending=True, limit=2)
        assert _ids(result) == ["q05", "q00"]

    @pytest.mark.parametrize(
        ("mode", "sort_by"),
        [("online", "optimal_step"), ("online", "factor_product"), ("online", "noise")],
    )
    def test_an_unsortable_column_is_refused_with_the_choices(
        self, mode: str, sort_by: str
    ) -> None:
        spec = _spec(mode=mode)
        with pytest.raises(HTTPException) as refused:
            quote_page(spec, sort_by=sort_by).validate(spec)
        assert refused.value.status_code == 400
        assert "optimal_scenario_value" in refused.value.detail


class TestFilters:
    def test_a_scenario_value_range_at_grid_values_is_inclusive(self) -> None:
        result = _page(
            filters=QuoteFilters(scenario_value_min=_f32(1.0), scenario_value_max=_f32(1.1)),
            sort_by="optimal_scenario_value",
        )
        # Steps 1 and 2: q07 q01 (1.0), q03 (1.1).
        assert _ids(result) == ["q01", "q07", "q03"]
        assert result.total == 3
        assert result.quotes == 8

    def test_at_range_edge_is_the_first_and_last_grid_step(self) -> None:
        result = _page(filters=QuoteFilters(at_range_edge=True))
        # Steps 0 (q02 q06) and 3 (q05 q00 q04), never the interior steps 1 and 2.
        assert _ids(result) == ["q05", "q02", "q00", "q06", "q04"]

    def test_at_range_edge_reads_the_recorded_grid_not_the_chosen_rows(self) -> None:
        # A six-step grid the quotes never reach the end of: nobody is at its last step.
        spec = _spec(grid=(0.9, 1.0, 1.1, 1.2, 1.3, 1.4))
        frames, n = _frames()
        reducer = quote_page(spec, filters=QuoteFilters(at_range_edge=True))
        reducer.validate(spec)
        assert _ids(reducer.run(frames, spec, n)) == ["q02", "q06"]

    def test_analysis_equality_with_a_value_and_with_null(self) -> None:
        north = _page(filters=QuoteFilters(analysis_equals=(("region", "North"),)))
        assert _ids(north) == ["q05", "q00", "q06"]
        assert north.rows["region"].to_list() == ["North"] * 3
        missing = _page(filters=QuoteFilters(analysis_equals=(("region", None),)))
        assert _ids(missing) == ["q07", "q04"]
        both = _page(filters=QuoteFilters(analysis_equals=(("region", "North"), ("tier", 1))))
        assert _ids(both) == ["q05", "q00"]

    @pytest.mark.parametrize(
        ("column", "value"),
        [("region", 1), ("tier", "1"), ("tier", True), ("region", True)],
    )
    def test_an_equality_value_of_the_wrong_type_is_refused(self, column: str, value: Any) -> None:
        with pytest.raises(HTTPException) as refused:
            _page(filters=QuoteFilters(analysis_equals=((column, value),)))
        assert refused.value.status_code == 400
        assert column in refused.value.detail

    def test_an_unknown_analysis_column_is_refused(self) -> None:
        spec = _spec(analysis=("region", "tier"))
        with pytest.raises(HTTPException) as refused:
            quote_page(spec, filters=QuoteFilters(analysis_equals=(("noise", 1),))).validate(spec)
        assert refused.value.status_code == 400
        assert "noise" in refused.value.detail

    def test_deployed_factor_differs_on_a_ratebook_frame(self) -> None:
        result = _page(mode="ratebook", filters=QuoteFilters(deployed_factor_differs=True))
        # q07 (1.02 at 1.0) and q06 (0.93 at 0.9); q05 is clamped high, so not flagged.
        assert _ids(result) == ["q07", "q06"]
        assert result.rows["deployed_factor_differs"].to_list() == [True, True]

    def test_deployed_factor_differs_is_refused_online(self) -> None:
        spec = _spec()
        with pytest.raises(HTTPException) as refused:
            quote_page(spec, filters=QuoteFilters(deployed_factor_differs=True)).validate(spec)
        assert refused.value.status_code == 400
        assert "ratebook" in refused.value.detail

    def test_a_minimum_above_the_maximum_is_refused(self) -> None:
        spec = _spec()
        with pytest.raises(HTTPException) as refused:
            quote_page(
                spec, filters=QuoteFilters(scenario_value_min=1.2, scenario_value_max=1.0)
            ).validate(spec)
        assert refused.value.status_code == 400


class TestSearchAndPaging:
    def test_a_quote_id_prefix_search(self) -> None:
        assert _ids(_page(quote_id_prefix="q0")) == list(_QUOTES)
        assert _ids(_page(quote_id_prefix="q00")) == ["q00"]
        nothing = _page(quote_id_prefix="Q0")
        assert _ids(nothing) == []
        assert nothing.total == 0
        assert nothing.quotes == 8

    def test_an_offset_past_the_end_is_an_empty_page_with_the_counts(self) -> None:
        result = _page(filters=QuoteFilters(at_range_edge=True), offset=40, limit=10)
        assert _ids(result) == []
        assert result.total == 5
        assert result.quotes == 8
        assert result.rows.columns == [
            column.name
            for column in quote_columns(
                _spec(analysis=("region", "tier")), {"region": "String", "tier": "Int64"}
            )
        ]

    def test_the_depth_guard_admits_exactly_the_limit_and_refuses_one_more(self) -> None:
        spec = _spec()
        quote_page(
            spec, offset=QUOTE_PAGE_DEPTH_LIMIT - APPLY_PREVIEW_ROW_LIMIT, limit=100
        ).validate(spec)
        with pytest.raises(HTTPException) as refused:
            quote_page(
                spec, offset=QUOTE_PAGE_DEPTH_LIMIT - APPLY_PREVIEW_ROW_LIMIT + 1, limit=100
            ).validate(spec)
        assert refused.value.status_code == 400
        assert "narrow" in refused.value.detail.lower()
        assert "10,000" in refused.value.detail

    def test_a_sorted_page_estimates_its_top_k_to_the_page_end(self) -> None:
        spec = _spec()
        assert (
            quote_page(spec, sort_by="optimal_objective", offset=300, limit=50).result_rows(spec)
            == 350
        )
        assert quote_page(spec, offset=300, limit=50).result_rows(spec) == 50

    @pytest.mark.parametrize("limit", [0, APPLY_PREVIEW_ROW_LIMIT + 1])
    def test_a_limit_outside_the_cap_is_refused(self, limit: int) -> None:
        spec = _spec()
        with pytest.raises(HTTPException) as refused:
            quote_page(spec, limit=limit).validate(spec)
        assert refused.value.status_code == 400


class TestColumns:
    def test_online_columns_and_roles(self) -> None:
        columns = quote_columns(
            _spec(analysis=("region", "tier")), {"region": "String", "tier": "Date"}
        )
        assert [(c.name, c.role, c.sortable, c.filterable) for c in columns] == [
            ("quote_id", "id", False, False),
            ("optimal_scenario_value", "scenario", True, False),
            ("optimal_objective", "objective", True, False),
            ("optimal_volume", "constraint", True, False),
            ("region", "analysis", True, True),
            ("tier", "analysis", True, False),
        ]

    def test_ratebook_columns_add_the_factor_product_and_the_flag(self) -> None:
        columns = quote_columns(_spec(mode="ratebook", analysis=()), {})
        assert [(c.name, c.role, c.sortable) for c in columns] == [
            ("quote_id", "id", False),
            ("optimal_scenario_value", "scenario", True),
            ("optimal_objective", "objective", True),
            ("optimal_volume", "constraint", True),
            ("factor_product", "factor", True),
            ("deployed_factor_differs", "flag", False),
        ]

    def test_rows_hold_exactly_the_columns_in_order(self) -> None:
        result = _page(mode="ratebook", analysis=True, limit=2)
        spec = _spec(mode="ratebook", analysis=("region", "tier"))
        assert result.rows.columns == [
            c.name for c in quote_columns(spec, {"region": "String", "tier": "Int64"})
        ]
        assert isinstance(quote_page(spec), QuotePage)


# ---------------------------------------------------------------------------
# Routes: real solves through POST /apply
# ---------------------------------------------------------------------------


def _apply(client: Any, job_id: str, **query: Any) -> Any:
    return client.post("/api/optimiser/apply", json={"job_id": job_id, **query})


def _apply_frame(job_id: str, key: str = "apply_result") -> pl.DataFrame:
    return pl.read_parquet(_store.require_job(job_id)["artifact_handles"][key]["path"])


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestApplyRoute:
    def test_the_default_page_is_the_apply_order_with_typed_columns(self, client, tmp_path):
        job_id, status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )

        response = _apply(client, job_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert [(c["name"], c["role"]) for c in body["columns"]] == [
            ("quote_id", "id"),
            ("optimal_scenario_value", "scenario"),
            ("optimal_objective", "objective"),
            ("optimal_volume", "constraint"),
            ("region", "analysis"),
        ]
        assert body["row_count"] == body["matched_row_count"] == 9
        assert body["offset"] == 0
        assert body["preview_row_limit"] == APPLY_PREVIEW_ROW_LIMIT
        assert body["preview_row_count"] == 9
        assert body["from_artifact"] is True
        assert body["total_objective"] == status["result"]["total_objective"]
        apply = _apply_frame(job_id)
        assert [row["quote_id"] for row in body["preview"]] == apply["quote_id"].to_list()
        assert [row["region"] for row in body["preview"]] == [
            _region(int(q[1:])) for q in apply["quote_id"].to_list()
        ]

    def test_a_search_and_an_analysis_sort_over_every_quote(self, client, tmp_path):
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )

        response = _apply(
            client,
            job_id,
            sort_by="region",
            descending=True,
            filters={"analysis_equals": {"region": "North"}},
            limit=2,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        # North is q000, q003, q006: equal regions page in quote-id order.
        assert [row["quote_id"] for row in body["preview"]] == ["q000", "q003"]
        assert body["matched_row_count"] == 3
        mistyped = _apply(client, job_id, filters={"analysis_equals": {"region": 1}})
        assert mistyped.status_code == 400
        assert "'region' (String)" in mistyped.json()["detail"]
        searched = _apply(client, job_id, quote_id_prefix="q00", sort_by="optimal_objective").json()
        apply = _apply_frame(job_id).filter(pl.col("quote_id").str.starts_with("q00"))
        assert [row["quote_id"] for row in searched["preview"]] == apply.sort(
            ["optimal_objective", "quote_id"]
        )["quote_id"].to_list()

    def test_the_limit_is_capped_and_deep_offsets_are_refused(self, client, tmp_path):
        job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))

        assert _apply(client, job_id, limit=APPLY_PREVIEW_ROW_LIMIT + 1).status_code == 422
        assert _apply(client, job_id, unknown_field=True).status_code == 422
        deep = _apply(client, job_id, offset=QUOTE_PAGE_DEPTH_LIMIT, limit=1)
        assert deep.status_code == 400
        assert "narrow" in deep.json()["detail"].lower()
        past = _apply(client, job_id, offset=50).json()
        assert past["preview"] == []
        assert past["matched_row_count"] == past["row_count"] == 9

    def test_a_frontier_point_is_paged_from_its_own_choices(self, client, tmp_path):
        job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))
        frontier = run_frontier_and_wait(
            client,
            {"job_id": job_id, "threshold_ranges": {"volume": [5.0, 8.0]}, "n_points_per_dim": 3},
        )
        assert frontier["status"] == "completed", frontier

        response = _apply(
            client, job_id, point_index=2, sort_by="optimal_scenario_value", descending=True
        )

        assert response.status_code == 200, response.text
        body = response.json()
        point = _apply_frame(job_id, "frontier_apply_result:2")
        assert [row["quote_id"] for row in body["preview"]] == point.sort(
            ["optimal_scenario_value", "quote_id"], descending=[True, False]
        )["quote_id"].to_list()
        assert body["total_objective"] == pytest.approx(
            frontier["result"]["points"][2]["total_objective"]
        )
        assert _store.require_job(job_id)["selected_frontier_point"] == 2

    def test_at_range_edge_is_answered_after_the_grid_is_evicted(self, client, tmp_path):
        job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))
        _store.clear_result_data(job_id)
        assert "quote_grid" not in _store.require_job(job_id)

        response = _apply(client, job_id, filters={"at_range_edge": True})

        assert response.status_code == 200, response.text
        apply = _apply_frame(job_id)
        expected = apply.filter(pl.col("optimal_step").is_in([0, 2]))["quote_id"].to_list()
        assert [row["quote_id"] for row in response.json()["preview"]] == expected
        assert response.json()["matched_row_count"] == len(expected)

    def test_an_over_budget_page_is_refused_by_admission(self, client, tmp_path, monkeypatch):
        job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))
        monkeypatch.setenv("HAUTE_EXPLORE_MEMORY_LIMIT_BYTES", str(32 * 1024 * 1024))

        response = _apply(client, job_id, sort_by="optimal_objective")

        assert response.status_code == 507, response.text
        assert response.json()["detail"]["error_code"] == "memory_limit"

    def test_a_ratebook_page_carries_the_factor_product_and_the_flag(self, client, tmp_path):
        from tests.test_optimiser_routes_real_library import (
            _poll_until_done,
            _ratebook_fixture_paths,
            _ratebook_graph,
            _solve_completed,
        )

        scored_path, banding_path = _ratebook_fixture_paths(tmp_path, with_age=True)
        job_id = _solve_completed(
            client, _ratebook_graph(scored_path, banding_path, [["region"], ["age"]])
        )
        result = _poll_until_done(client, job_id)["result"]

        body = _apply(client, job_id, sort_by="factor_product", descending=True).json()

        assert [(c["name"], c["role"]) for c in body["columns"]][-2:] == [
            ("factor_product", "factor"),
            ("deployed_factor_differs", "flag"),
        ]
        frame = _apply_frame(job_id)
        assert [row["quote_id"] for row in body["preview"]] == frame.sort(
            ["factor_product", "quote_id"], descending=[True, False]
        )["quote_id"].to_list()
        flagged = _apply(client, job_id, filters={"deployed_factor_differs": True}).json()
        assert flagged["matched_row_count"] == result["adjustments"]["deployed_factor_differs"]
        assert all(row["deployed_factor_differs"] for row in flagged["preview"])

    def test_deployed_factor_differs_is_refused_for_an_online_result(self, client, tmp_path):
        job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))
        response = _apply(client, job_id, filters={"deployed_factor_differs": True})
        assert response.status_code == 400
        assert "ratebook" in response.json()["detail"]
