"""OPT-V09C: ratebook per-quote choices from price-contour's canonical evaluation.

A ratebook solve's per-quote frame is ``RatebookResult.quote_results`` (and, for a
frontier point, ``RatebookOptimiser.evaluate`` of the tables the frontier kept):
persisted at completion as the job's apply artifact and read by the same bounded
choice queries as an online result. Every test here drives the real price-contour
library; the per-quote values are checked against the scored input itself, the
totals against the solve's, and the "deployed factor differs from evaluated step"
flag against a hand count.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from haute.routes._optimiser_adjustments import adjustment_report
from haute.routes._optimiser_outcomes import (
    ChoiceFrameSpec,
    ChoiceJoinError,
    ChoiceTarget,
    FactorSegments,
    ScenarioHistogram,
    SegmentGroupBy,
    apply_frame_schema,
    histogram_of_frame,
)
from haute.routes.optimiser import _store
from tests.optimiser_fixtures import run_frontier_and_wait
from tests.test_optimiser_routes_real_library import (
    _poll_until_done,
    _ratebook_fixture_paths,
    _ratebook_graph,
    _solve_completed,
)

_SEPARATOR = "\x1f"
_CONSTRAINTS = ("volume",)


def _close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= 1e-9 * max(1.0, abs(expected))


def _choices(job_id: str, reducer: Any, point_index: int | None = None) -> Any:
    from haute.routes.optimiser import _choice_service

    return _choice_service.choice_query(job_id, ChoiceTarget(point_index), reducer)


def _artifact(job_id: str, key: str = "apply_result") -> pl.DataFrame:
    return pl.read_parquet(_store.require_job(job_id)["artifact_handles"][key]["path"])


def _ratebook_spec(grid: Sequence[float]) -> ChoiceFrameSpec:
    return ChoiceFrameSpec(
        mode="ratebook",
        constraint_names=_CONSTRAINTS,
        analysis_columns=(),
        scenario_grid=tuple((step, float(np.float32(value))) for step, value in enumerate(grid)),
        factor_columns=(),
    )


def _evaluator(factor_columns: list[list[str]]) -> Any:
    import price_contour as pc

    return pc.RatebookOptimiser(
        objective="expected_income",
        constraints={"volume": {"min": 0.90}},
        factor_columns=factor_columns,
    )


def _chosen_inputs(scored: pl.DataFrame, frame: pl.DataFrame) -> pl.DataFrame:
    """Each quote's scored row at the step its frame chose: the independent oracle."""
    return frame.join(
        scored.select(
            pl.col("quote_id").cast(pl.String),
            pl.col("scenario_index").alias("optimal_step"),
            pl.col("scenario_value").alias("scored_scenario_value"),
            "expected_income",
            "volume",
        ),
        on=["quote_id", "optimal_step"],
        how="left",
        validate="1:1",
    )


def _f32_products(
    banding: pl.DataFrame,
    factor_columns: list[list[str]],
    tables: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    """Each quote's factor product, multiplied in Float32 in factor-spec order."""
    products: dict[str, float] = {}
    for row in banding.iter_rows(named=True):
        product = np.float32(1.0)
        for columns in factor_columns:
            level = _SEPARATOR.join(str(row[column]) for column in columns)
            product = np.float32(product * np.float32(tables[":".join(columns)][level]))
        products[str(row["quote_id"])] = float(product)
    return products


def _assert_frame_is_the_scored_choice(
    frame: pl.DataFrame,
    scored_path: str,
    banding_path: str,
    factor_columns: list[list[str]],
    tables: Mapping[str, Mapping[str, float]],
) -> None:
    """Per quote: the chosen values are the scored input's at the chosen step, and the
    factor product is the product of the published rates."""
    scored = pl.read_parquet(scored_path)
    joined = _chosen_inputs(scored, frame)
    assert joined["expected_income"].null_count() == 0
    assert joined["optimal_objective"].to_list() == joined["expected_income"].to_list()
    assert joined["optimal_volume"].to_list() == joined["volume"].to_list()
    assert joined["optimal_scenario_value"].to_list() == joined["scored_scenario_value"].to_list()
    expected = _f32_products(pl.read_parquet(banding_path), factor_columns, tables)
    assert dict(zip(frame["quote_id"], frame["factor_product"], strict=True)) == expected


def _tables_of(result_tables: Mapping[str, list[dict[str, Any]]]) -> dict[str, dict[str, float]]:
    return {
        name: {row["__factor_group__"]: row["optimal_scenario_value"] for row in rows}
        for name, rows in result_tables.items()
    }


# ---------------------------------------------------------------------------
# The flag rule, over a frame the real kernel evaluated from hand-chosen tables
# ---------------------------------------------------------------------------

_FLAG_GRID = np.array([0.9, 1.0, 1.1], dtype=np.float32)
_FLAG_REGIONS = {"N": 1.0, "S": 1.04, "E": 1.2, "W": 0.8}
_FLAG_AGES = {"y": 1.0, "o": 0.9}
# Products: N-y 1.0 and N-o 0.9 land on grid values; S-y 1.04, S-o 0.936 and E-o 1.08
# round to a neighbouring step inside the range; E-y 1.2, W-y 0.8 and W-o 0.72 lie past
# a grid end and deploy at it, the step the solver evaluated.
_HAND_FLAGGED = {"S-y", "S-o", "E-o"}
_PAST_THE_EDGE = {"E-y", "W-y", "W-o"}


def _flag_fixture() -> pl.DataFrame:
    quotes = [f"{region}-{age}" for region in _FLAG_REGIONS for age in _FLAG_AGES]
    scored = pl.DataFrame(
        {
            "quote_id": [quote for quote in quotes for _ in _FLAG_GRID],
            "scenario_index": pl.Series(
                [step for _ in quotes for step in range(len(_FLAG_GRID))], dtype=pl.Int32
            ),
            "scenario_value": pl.Series(
                [float(value) for _ in quotes for value in _FLAG_GRID], dtype=pl.Float32
            ),
            "expected_income": pl.Series(
                [100.0 * float(value) for _ in quotes for value in _FLAG_GRID], dtype=pl.Float32
            ),
            "volume": pl.Series(
                [2.0 - float(value) for _ in quotes for value in _FLAG_GRID], dtype=pl.Float32
            ),
        }
    )
    factors = pl.DataFrame(
        {
            "quote_id": quotes,
            "region": [quote.split("-")[0] for quote in quotes],
            "age": [quote.split("-")[1] for quote in quotes],
        }
    )
    evaluation = _evaluator([["region"], ["age"]]).evaluate(
        scored, factors, {"region": _FLAG_REGIONS, "age": _FLAG_AGES}
    )
    return evaluation.quote_results


class TestDeployedFactorDiffersFlag:
    def test_the_flag_matches_a_hand_count_and_skips_products_past_the_edge(self) -> None:
        from haute.routes._optimiser_outcomes import _choice_frame

        frame = _flag_fixture()
        # The kernel clamped exactly the quotes past an end.
        clamped = frame.filter(pl.col("clamped_low") | pl.col("clamped_high"))
        assert set(clamped["quote_id"]) == _PAST_THE_EDGE

        spec = _ratebook_spec(_FLAG_GRID)
        flags = _choice_frame(frame.lazy(), spec).collect()
        flagged = set(flags.filter(pl.col("deployed_factor_differs"))["quote_id"])
        assert flagged == _HAND_FLAGGED
        assert not flagged & _PAST_THE_EDGE

        report = adjustment_report(histogram_of_frame(frame.lazy(), spec), spec)
        assert report.deployed_factor_differs == len(_HAND_FLAGGED)
        assert report.n_quotes == 8

    def test_a_report_cannot_count_more_flagged_quotes_than_it_has(self) -> None:
        from pydantic import ValidationError

        spec = _ratebook_spec(_FLAG_GRID)
        payload = adjustment_report(histogram_of_frame(_flag_fixture().lazy(), spec), spec)
        with pytest.raises(ValidationError, match="deployed_factor_differs"):
            type(payload).model_validate({**payload.model_dump(), "deployed_factor_differs": 9})

    def test_the_histogram_counts_flagged_quotes_per_evaluated_step(self) -> None:
        frame = _flag_fixture()
        spec = _ratebook_spec(_FLAG_GRID)

        rows = histogram_of_frame(frame.lazy(), spec).rows
        # S-o rounds down to 0.9, S-y down to 1.0, E-o up to 1.1.
        assert rows["deployed_factor_differs"].to_list() == [1, 1, 1]

    def test_an_online_report_has_no_deployed_factor_count(self) -> None:
        grid = [0.9, 1.0, 1.1]
        spec = ChoiceFrameSpec(
            mode="online",
            constraint_names=_CONSTRAINTS,
            analysis_columns=(),
            scenario_grid=tuple((step, value) for step, value in enumerate(grid)),
            factor_columns=(),
        )
        frame = _flag_fixture().select(list(apply_frame_schema("online", list(_CONSTRAINTS))))

        report = adjustment_report(histogram_of_frame(frame.lazy(), spec), spec)
        assert report.deployed_factor_differs is None
        assert "deployed_factor_differs" not in histogram_of_frame(frame.lazy(), spec).rows.columns


class TestPerModeSchemas:
    def test_the_ratebook_schema_is_price_contours_quote_results_schema(self) -> None:
        import price_contour as pc

        names = ["volume", "conversion"]
        assert apply_frame_schema("ratebook", names) == pc.quote_results_schema(names)
        online = apply_frame_schema("online", names)
        assert list(online) == [
            "quote_id",
            "optimal_step",
            "optimal_scenario_value",
            "optimal_objective",
            "optimal_volume",
            "optimal_conversion",
        ]
        assert list(apply_frame_schema("ratebook", names))[: len(online)] == list(online)

    @pytest.mark.parametrize(
        ("damage", "named"),
        [
            ("drop_clamped_high", "clamped_high"),
            ("widen_factor_product", "factor_product"),
            ("extra_column", "surprise"),
        ],
    )
    def test_a_frame_that_is_not_its_modes_schema_fails_loudly(
        self, damage: str, named: str
    ) -> None:
        frame = _flag_fixture()
        damaged = {
            "drop_clamped_high": lambda: frame.drop("clamped_high"),
            "widen_factor_product": lambda: frame.with_columns(
                pl.col("factor_product").cast(pl.Float64)
            ),
            "extra_column": lambda: frame.with_columns(pl.lit(1).alias("surprise")),
        }[damage]()

        with pytest.raises(ChoiceJoinError, match=named):
            histogram_of_frame(damaged.lazy(), _ratebook_spec(_FLAG_GRID))

    def test_a_ratebook_frame_read_as_online_fails_loudly(self) -> None:
        spec = ChoiceFrameSpec(
            mode="online",
            constraint_names=_CONSTRAINTS,
            analysis_columns=(),
            scenario_grid=tuple((step, float(v)) for step, v in enumerate(_FLAG_GRID)),
            factor_columns=(),
        )
        with pytest.raises(ChoiceJoinError, match="factor_product"):
            histogram_of_frame(_flag_fixture().lazy(), spec)


# ---------------------------------------------------------------------------
# Real ratebook solves through the routes
# ---------------------------------------------------------------------------


def _solve(
    client: Any, tmp_path: Path, factor_columns: list[list[str]], **paths: Any
) -> tuple[str, dict[str, Any], str, str]:
    scored_path, banding_path = _ratebook_fixture_paths(tmp_path, with_age=True, **paths)
    job_id = _solve_completed(client, _ratebook_graph(scored_path, banding_path, factor_columns))
    return job_id, _poll_until_done(client, job_id), scored_path, banding_path


def _frontier(client: Any, job_id: str, n_points: int = 3) -> list[dict[str, Any]]:
    status = run_frontier_and_wait(
        client,
        {
            "job_id": job_id,
            "threshold_ranges": {"volume": [8.0, 11.2]},
            "n_points_per_dim": n_points,
        },
    )
    assert status["status"] == "completed", status.get("message", "")
    return list(status["result"]["points"])


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestAsSolvedRatebookChoices:
    def test_the_as_solved_frame_is_persisted_and_agrees_per_quote_and_in_aggregate(
        self, client, tmp_path
    ):
        factor_columns = [["region"], ["age"]]
        job_id, status, scored_path, banding_path = _solve(client, tmp_path, factor_columns)
        result = status["result"]

        frame = _artifact(job_id)
        assert dict(frame.schema) == apply_frame_schema("ratebook", list(_CONSTRAINTS))
        assert frame.height == 9
        _assert_frame_is_the_scored_choice(
            frame, scored_path, banding_path, factor_columns, _tables_of(result["factor_tables"])
        )
        # Aggregate: Float64 sums of the Float32 values equal the solve's totals.
        assert _close(frame["optimal_objective"].cast(pl.Float64).sum(), result["total_objective"])
        assert _close(
            frame["optimal_volume"].cast(pl.Float64).sum(), result["constraints"]["volume"]
        )
        histogram = _choices(job_id, ScenarioHistogram())
        assert histogram.total == 9
        assert _close(histogram.rows["optimal_objective"].sum(), result["total_objective"])
        assert _close(histogram.rows["optimal_volume"].sum(), result["constraints"]["volume"])

    def test_the_as_solved_report_is_computed_at_finalize_with_the_flag_count(
        self, client, tmp_path
    ):
        job_id, status, _scored, _banding = _solve(client, tmp_path, [["region"], ["age"]])
        report = status["result"]["adjustments"]

        assert report is not None, status["result"]["diagnostics_errors"]
        assert report["n_quotes"] == 9
        frame = _artifact(job_id)
        flagged = frame.filter(
            (pl.col("factor_product") != pl.col("optimal_scenario_value"))
            & ~pl.col("clamped_low")
            & ~pl.col("clamped_high")
        ).height
        assert report["deployed_factor_differs"] == flagged
        steps = frame["optimal_step"].value_counts()
        assert [bar["quotes"] for bar in report["bars"]] == [
            int(steps.filter(pl.col("optimal_step") == step)["count"].sum()) for step in range(3)
        ]

    def test_apply_serves_the_ratebook_as_solved_detail(self, client, tmp_path):
        job_id, status, _scored, _banding = _solve(client, tmp_path, [["region"]])

        response = client.post("/api/optimiser/apply", json={"job_id": job_id})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["from_artifact"] is True
        assert body["row_count"] == 9
        assert body["total_objective"] == status["result"]["total_objective"]
        # A Quotes page row: the frame's columns but the chosen step and the clamp flags,
        # with the per-quote "deployed factor differs" flag.
        schema = apply_frame_schema("ratebook", ["volume"])
        assert list(body["preview"][0]) == [
            *(c for c in schema if c not in ("optimal_step", "clamped_low", "clamped_high")),
            "deployed_factor_differs",
        ]

    def test_composite_factor_segments_need_no_analysis_columns(self, client, tmp_path):
        job_id, status, _scored, _banding = _solve(client, tmp_path, [["region", "age"]])
        rates = status["result"]["factor_tables"]["region:age"]

        segments = _choices(job_id, FactorSegments("region:age", limit=100))

        rows = segments.rows
        assert segments.total == len(rates) == 6
        assert rows["quotes"].sum() == 9
        # Each level is labelled as the Rates tab labels it, with the same quote count.
        assert dict(zip(rows["level"], rows["quotes"], strict=True)) == {
            row["__factor_group__"]: row["quote_count"] for row in rates
        }
        assert "North\x1fyoung" in set(rows["level"])
        assert _close(rows["optimal_objective"].sum(), status["result"]["total_objective"])
        assert (
            rows["deployed_factor_differs"].sum()
            == status["result"]["adjustments"]["deployed_factor_differs"]
        )
        # Largest first, then by the constituent values.
        assert rows["quotes"].to_list() == sorted(rows["quotes"].to_list(), reverse=True)

    def test_a_numeric_factor_level_is_labelled_as_the_rates_tab_labels_it(self, client, tmp_path):
        scored_path, banding_path = _ratebook_fixture_paths(tmp_path)
        banding = pl.read_parquet(banding_path).with_columns(
            pl.Series("band", [float(1 + q % 2) for q in range(9)], dtype=pl.Float64)
        )
        banding.write_parquet(banding_path)
        job_id = _solve_completed(
            client, _ratebook_graph(scored_path, banding_path, [["region", "band"]])
        )
        rates = _poll_until_done(client, job_id)["result"]["factor_tables"]["region:band"]

        rows = _choices(job_id, FactorSegments("region:band", limit=100)).rows

        assert set(rows["level"]) == {row["__factor_group__"] for row in rates}
        assert "North\x1f1" in set(rows["level"])

    def test_the_group_limit_keeps_the_largest_levels_and_counts_them_all(self, client, tmp_path):
        job_id, _status, _scored, _banding = _solve(client, tmp_path, [["region", "age"]])

        segments = _choices(job_id, FactorSegments("region:age", limit=2))

        assert segments.total == 6
        assert segments.rows["level"].to_list() == ["East\x1fyoung", "North\x1fyoung"]
        assert segments.rows["quotes"].to_list() == [2, 2]

    @pytest.mark.parametrize("invalid", ["unknown_factor", "zero_limit", "over_cap"])
    def test_an_invalid_factor_breakdown_is_refused(self, client, tmp_path, invalid):
        from haute.routes._optimiser_outcomes import MAX_CHOICE_ROWS

        job_id, _status, _scored, _banding = _solve(client, tmp_path, [["region"], ["age"]])
        reducer = {
            "unknown_factor": FactorSegments("region:age", limit=5),
            "zero_limit": FactorSegments("region", limit=0),
            "over_cap": FactorSegments("region", limit=MAX_CHOICE_ROWS + 1),
        }[invalid]

        with pytest.raises(HTTPException) as caught:
            _choices(job_id, reducer)
        assert caught.value.status_code == 400
        if invalid == "unknown_factor":
            assert "region:age" in str(caught.value.detail)
            assert "'region'" in str(caught.value.detail)

    @pytest.mark.parametrize("damage", ["dropped_row", "foreign_quote", "repeated_quote"])
    def test_factor_rows_that_do_not_join_one_to_one_fail_loudly(self, client, tmp_path, damage):
        job_id, _status, _scored, _banding = _solve(client, tmp_path, [["region"]])
        path = _store.require_job(job_id)["artifact_handles"]["ratebook_factors"]["path"]
        edits = {
            "dropped_row": lambda frame: frame.filter(pl.col("quote_id") != "q_0004"),
            "foreign_quote": lambda frame: frame.with_columns(
                pl.when(pl.col("quote_id") == "q_0004")
                .then(pl.lit("q_9999"))
                .otherwise(pl.col("quote_id"))
                .alias("quote_id")
            ),
            "repeated_quote": lambda frame: frame.with_columns(
                pl.when(pl.col("quote_id") == "q_0004")
                .then(pl.lit("q_0005"))
                .otherwise(pl.col("quote_id"))
                .alias("quote_id")
            ),
        }
        edits[damage](pl.read_parquet(path)).write_parquet(path)

        with pytest.raises(ChoiceJoinError):
            _choices(job_id, FactorSegments("region", limit=10))
        # Only a reducer that reads the factor rows leases and checks them.
        assert _choices(job_id, ScenarioHistogram()).total == 9

    def test_factor_segments_are_refused_for_an_online_job(self, client, tmp_path):
        from tests.test_optimiser_outcomes import _data_graph, _scored, _solved

        job_id, _status = _solved(client, _data_graph(_scored(tmp_path)))
        with pytest.raises(HTTPException) as caught:
            _choices(job_id, FactorSegments("region", limit=5))
        assert caught.value.status_code == 400
        assert "ratebook" in str(caught.value.detail).lower()

    def test_analysis_segments_carry_the_flag_count_for_a_ratebook_job(self, client, tmp_path):
        scored_path, banding_path = _ratebook_fixture_paths(tmp_path, with_age=True)
        scored = pl.read_parquet(scored_path).with_columns(
            pl.col("quote_id").str.slice(-1).alias("tail")
        )
        scored.write_parquet(scored_path)
        graph = _ratebook_graph(scored_path, banding_path, [["region"], ["age"]])
        graph["nodes"][2]["data"]["config"]["analysis_columns"] = ["tail"]
        job_id = _solve_completed(client, graph)
        report = _poll_until_done(client, job_id)["result"]["adjustments"]

        rows = _choices(job_id, SegmentGroupBy(("tail",), limit=20)).rows

        assert rows["quotes"].sum() == 9
        assert rows["deployed_factor_differs"].sum() == report["deployed_factor_differs"]


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestRatebookFrontierPointChoices:
    def test_a_points_frame_agrees_per_quote_and_in_aggregate(self, client, tmp_path):
        factor_columns = [["region"], ["age"]]
        job_id, _status, scored_path, banding_path = _solve(client, tmp_path, factor_columns)
        points = _frontier(client, job_id)

        for index, point in enumerate(points):
            histogram = _choices(job_id, ScenarioHistogram(), point_index=index)
            assert histogram.total == 9
            assert _close(histogram.rows["optimal_objective"].sum(), point["total_objective"])
            assert _close(histogram.rows["optimal_volume"].sum(), point["totals"]["volume"])
            frame = _artifact(job_id, f"frontier_apply_result:{index}")
            assert dict(frame.schema) == apply_frame_schema("ratebook", list(_CONSTRAINTS))
            tables = _store.require_job(job_id)["frontier_factor_tables"][index]
            _assert_frame_is_the_scored_choice(
                frame, scored_path, banding_path, factor_columns, tables
            )
        # Querying never selects a point.
        assert _store.require_job(job_id).get("selected_frontier_point") is None

    def test_a_points_report_is_answered_by_frontier_select(self, client, tmp_path):
        job_id, _status, _scored, _banding = _solve(client, tmp_path, [["region"], ["age"]])
        _frontier(client, job_id)

        response = client.post(
            "/api/optimiser/frontier/select",
            json={"job_id": job_id, "point_index": 1, "include_adjustments": True},
        )

        assert response.status_code == 200, response.text
        report = response.json()["adjustments"]
        frame = _artifact(job_id, "frontier_apply_result:1")
        spec = _ratebook_spec(
            [step["scenario_value"] for step in _store.require_job(job_id)["scenario_grid"]]
        )
        expected = adjustment_report(histogram_of_frame(frame.lazy(), spec), spec)
        assert report == expected.model_dump()
        assert report["deployed_factor_differs"] is not None

    def test_apply_serves_a_ratebook_points_detail_and_selects_it_with_its_tables(
        self, client, tmp_path
    ):
        job_id, _status, _scored, _banding = _solve(client, tmp_path, [["region"], ["age"]])
        points = _frontier(client, job_id)

        first = client.post("/api/optimiser/apply", json={"job_id": job_id, "point_index": 2})
        again = client.post("/api/optimiser/apply", json={"job_id": job_id, "point_index": 2})

        assert first.status_code == 200, first.text
        assert first.json()["from_artifact"] is False
        assert again.json()["from_artifact"] is True
        assert first.json()["total_objective"] == points[2]["total_objective"]
        assert first.json()["row_count"] == 9
        job = _store.require_job(job_id)
        assert job["selected_frontier_point"] == 2
        # The recorded result is the point's own, factor tables included.
        selected_tables = _tables_of(job["result"]["factor_tables"])
        assert selected_tables == job["frontier_factor_tables"][2]

    def test_an_unmaterialised_point_is_the_named_410_once_the_grid_has_gone(
        self, client, tmp_path, clean_job_store
    ):
        job_id, _status, _scored, _banding = _solve(client, tmp_path, [["region"], ["age"]])
        _frontier(client, job_id)
        retained = _choices(job_id, ScenarioHistogram(), point_index=0)

        clean_job_store.clear_result_data(job_id)

        with pytest.raises(HTTPException) as caught:
            _choices(job_id, ScenarioHistogram(), point_index=1)
        assert caught.value.status_code == 410
        assert caught.value.detail["error_code"] == "frontier_point_unavailable"
        response = client.post("/api/optimiser/apply", json={"job_id": job_id, "point_index": 1})
        assert response.status_code == 410
        assert response.json()["detail"]["error_code"] == "frontier_point_unavailable"
        # A retained point's artifact still answers without the grid.
        assert _choices(job_id, ScenarioHistogram(), point_index=0).rows.equals(retained.rows)

    def test_a_point_whose_evaluation_differs_from_its_row_fails_loudly(
        self, client, tmp_path, clean_job_store
    ):
        from tests.job_store_support import replace_job

        job_id, _status, _scored, _banding = _solve(client, tmp_path, [["region"], ["age"]])
        points = _frontier(client, job_id)
        other = next(
            index
            for index, point in enumerate(points)
            if point["total_objective"] != points[0]["total_objective"]
        )

        def swap_tables(job: dict[str, Any]) -> None:
            # Point 0 is paired with another point's tables: a different point.
            job["frontier_factor_tables"][0] = job["frontier_factor_tables"][other]

        replace_job(clean_job_store, job_id, swap_tables)

        with pytest.raises(RuntimeError, match="frontier row"):
            _choices(job_id, ScenarioHistogram(), point_index=0)
        assert "frontier_apply_result:0" not in _store.require_job(job_id)["artifact_handles"]
