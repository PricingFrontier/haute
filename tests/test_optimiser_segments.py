"""OPT-V11: segment breakdowns of the chosen scenarios, and the adjustment-spread index.

The pure half runs the segment reducers over hand-built, in-memory choice frames
(the same ``ChoiceFrames`` the routes lease) and checks every figure against a
hand calculation. The route half drives real price-contour solves through
``POST /api/optimiser/segments`` and ``GET /api/optimiser/segments/index``:
availability, a frontier point, the cardinality gate and the exact level check.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from haute.routes._optimiser_outcomes import (
    ANALYSIS_ROW_PRESENT_COLUMN,
    ChoiceFrames,
    ChoiceFrameSpec,
    ChoiceJoinError,
)
from haute.routes._optimiser_segments import (
    ADMITTED_APPROX_LEVELS,
    MAX_CATEGORICAL_LEVELS,
    MAX_FACTOR_SEGMENT_KEYS,
    MAX_SEGMENT_KEY_BYTES,
    MAX_SEGMENT_LEVELS,
    SEGMENT_INDEXES_KEY,
    AnalysisSegments,
    SegmentLevelsExceededError,
    segment_keys,
    segment_spread,
    segments_response,
)
from haute.routes.optimiser import (
    OPTIMISER_SEGMENT_INDEX_ROUTE,
    OPTIMISER_SEGMENTS_ROUTE,
    _store,
)
from haute.schemas import OptimiserSegmentKey
from tests.optimiser_fixtures import run_frontier_and_wait
from tests.test_optimiser_outcomes import _data_graph, _scored, _side_graph, _solved

_GRID = (0.9, 1.0, 1.1)


def _f32(value: float) -> float:
    return float(np.float32(value))


def _spec(
    grid: Sequence[float] = _GRID,
    analysis: Sequence[str] = ("region",),
    mode: str = "online",
) -> ChoiceFrameSpec:
    return ChoiceFrameSpec(
        mode=mode,  # type: ignore[arg-type]
        constraint_names=("volume",),
        analysis_columns=tuple(analysis),
        scenario_grid=tuple((step, _f32(value)) for step, value in enumerate(grid)),
        factor_columns=(),
    )


def _frames(
    steps: Sequence[int],
    analysis: dict[str, pl.Series],
    *,
    grid: Sequence[float] = _GRID,
    objective: Sequence[float] | None = None,
    volume: Sequence[float] | None = None,
    present: Sequence[bool] | None = None,
) -> tuple[ChoiceFrames, int]:
    """One chosen row per quote and its analysis row, as the leased tables hold them."""
    n = len(steps)
    quotes = [f"q{i:04d}" for i in range(n)]
    choice = pl.LazyFrame(
        {
            "quote_id": pl.Series(quotes, dtype=pl.String),
            "optimal_step": pl.Series(list(steps), dtype=pl.Int32),
            "optimal_scenario_value": pl.Series([grid[s] for s in steps], dtype=pl.Float32),
            "optimal_objective": pl.Series(
                list(objective) if objective is not None else [1.0] * n, dtype=pl.Float32
            ),
            "optimal_volume": pl.Series(
                list(volume) if volume is not None else [1.0] * n, dtype=pl.Float32
            ),
        }
    )
    side = pl.LazyFrame(
        {
            "quote_id": pl.Series(quotes, dtype=pl.String),
            **analysis,
            ANALYSIS_ROW_PRESENT_COLUMN: pl.Series(
                list(present) if present is not None else [True] * n, dtype=pl.Boolean
            ),
        }
    )
    frames = ChoiceFrames(
        choice=choice,
        analysis=side,
        analysis_key="quote_id",
        collect=lambda plan: plan.collect(),
    )
    return frames, n


def _key(column: str = "region", binning: str = "categorical") -> OptimiserSegmentKey:
    return OptimiserSegmentKey(
        key=column, source="analysis", binning=binning, available=True, unavailable_reason=None
    )


def _run(
    frames: ChoiceFrames,
    n: int,
    *,
    column: str = "region",
    binning: str = "categorical",
    weight: str = "quotes",
    spec: ChoiceFrameSpec | None = None,
) -> Any:
    spec = spec or _spec(analysis=(column,))
    reducer = AnalysisSegments(column=column, binning=binning, weight=weight)  # type: ignore[arg-type]
    reducer.validate(spec)
    result = reducer.run(frames, spec, n)
    return segments_response(
        result,
        spec,
        _key(column, binning),
        weight,
        point_index=None,
        frontier_generation=0,
    )


def _row(response: Any, label: str) -> Any:
    [row] = [row for row in response.rows if row.label == label]
    return row


# ---------------------------------------------------------------------------
# Pure: the statistical contract over hand-built choice frames
# ---------------------------------------------------------------------------


class TestHandCalculatedLevels:
    """Eight quotes in three regions, weighted by volume at the chosen scenario.

    ======  ====  =====  ======
    quote   reg   step   volume
    ======  ====  =====  ======
    q0000   A     0      2.0
    q0001   A     1      1.0
    q0002   A     2      1.0
    q0003   B     2      0.0
    q0004   B     2      0.0
    q0005   B     0      0.0
    q0006   C     1      3.0
    q0007   C     0      1.0
    ======  ====  =====  ======
    """

    STEPS = (0, 1, 2, 2, 2, 0, 1, 0)
    REGION = ("A", "A", "A", "B", "B", "B", "C", "C")
    VOLUME = (2.0, 1.0, 1.0, 0.0, 0.0, 0.0, 3.0, 1.0)

    def _response(self, weight: str = "optimal_volume") -> Any:
        frames, n = _frames(self.STEPS, {"region": pl.Series(self.REGION)}, volume=self.VOLUME)
        return _run(frames, n, weight=weight)

    def test_positive_weight_levels_match_hand_values(self) -> None:
        response = self._response()
        a = _row(response, "A")
        sv = [_f32(v) for v in _GRID]

        assert a.quotes == 3
        assert a.kind == "value"
        assert a.weight_total == pytest.approx(4.0)
        # (2*0.9 + 1*1.0 + 1*1.1) / 4, with the Float32 grid values.
        assert a.weighted.mean_scenario_value == pytest.approx(
            (2 * sv[0] + sv[1] + sv[2]) / 4, rel=1e-12
        )
        assert a.weighted.share_up == pytest.approx(0.25)
        assert a.weighted.share_down == pytest.approx(0.5)
        assert a.weighted.share_at_edge == pytest.approx(0.75)
        assert a.unweighted.mean_scenario_value == pytest.approx(sum(sv) / 3, rel=1e-12)
        assert a.unweighted.share_up == pytest.approx(1 / 3)
        assert a.unweighted.share_down == pytest.approx(1 / 3)
        assert a.unweighted.share_at_edge == pytest.approx(2 / 3)

        c = _row(response, "C")
        assert c.weighted.mean_scenario_value == pytest.approx((3 * sv[1] + sv[0]) / 4, rel=1e-12)
        assert c.weighted.share_down == pytest.approx(0.25)
        assert c.weighted.share_up == 0.0
        assert c.weighted.share_at_edge == pytest.approx(0.25)

    def test_a_zero_weight_level_keeps_its_count_and_unweighted_figures(self) -> None:
        response = self._response()
        b = _row(response, "B")
        sv = [_f32(v) for v in _GRID]

        assert b.quotes == 3
        assert b.weight_total == 0.0
        assert b.weighted is None
        assert b.unweighted.mean_scenario_value == pytest.approx((2 * sv[2] + sv[0]) / 3, rel=1e-12)
        assert b.unweighted.share_up == pytest.approx(2 / 3)
        assert b.unweighted.share_at_edge == 1.0
        [error] = response.diagnostics_errors
        assert error.diagnostic == "segment_weight"
        assert error.error_type == "ZeroLevelWeight"
        assert "'B'" in error.message
        assert "volume" in error.message

    def test_counts_sum_to_n_quotes_and_weighted_means_reconcile(self) -> None:
        response = self._response()
        sv = [_f32(_GRID[s]) for s in self.STEPS]

        assert response.n_quotes == 8
        assert sum(row.quotes for row in response.rows) == 8
        portfolio = math.fsum(w * v for w, v in zip(self.VOLUME, sv, strict=True)) / sum(
            self.VOLUME
        )
        assert response.weighted_mean_scenario_value == pytest.approx(portfolio, rel=1e-12)
        reconciled = math.fsum(
            row.weight_total * row.weighted.mean_scenario_value
            for row in response.rows
            if row.weighted is not None
        ) / math.fsum(row.weight_total for row in response.rows)
        assert reconciled == pytest.approx(portfolio, rel=1e-12)
        assert response.mean_scenario_value == pytest.approx(sum(sv) / 8, rel=1e-12)

    def test_levels_are_ordered_by_quotes_then_value(self) -> None:
        response = self._response()
        assert [row.label for row in response.rows] == ["A", "B", "C"]
        assert response.n_levels == 3
        assert response.weight_label == "volume at the chosen scenario"

    def test_quote_weighting_equals_the_unweighted_figures(self) -> None:
        response = self._response(weight="quotes")
        assert response.weight_label == "Quotes"
        assert response.diagnostics_errors == []
        for row in response.rows:
            assert row.weighted == row.unweighted
            assert row.weight_total == row.quotes
        assert response.weighted_mean_scenario_value == response.mean_scenario_value


class TestRefusedWeightings:
    def test_a_negative_weight_refuses_the_weighting_for_every_level(self) -> None:
        frames, n = _frames(
            [0, 1, 2], {"region": pl.Series(["A", "A", "B"])}, volume=[1.0, -0.5, 2.0]
        )
        response = _run(frames, n, weight="optimal_volume")

        assert all(row.weighted is None and row.weight_total is None for row in response.rows)
        assert response.weighted_mean_scenario_value is None
        [error] = response.diagnostics_errors
        assert error.error_type == "NegativeWeight"
        assert "1 quote" in error.message
        # The unweighted figures are still there.
        assert _row(response, "A").unweighted.mean_scenario_value == pytest.approx(
            (_f32(0.9) + 1.0) / 2, rel=1e-12
        )

    def test_a_zero_total_weight_refuses_the_weighting(self) -> None:
        frames, n = _frames([0, 1], {"region": pl.Series(["A", "B"])}, volume=[0.0, 0.0])
        response = _run(frames, n, weight="optimal_volume")

        assert [error.error_type for error in response.diagnostics_errors] == ["ZeroTotalWeight"]
        assert all(row.weighted is None for row in response.rows)

    def test_an_unknown_weight_is_refused(self) -> None:
        reducer = AnalysisSegments(column="region", binning="categorical", weight="optimal_price")
        with pytest.raises(HTTPException) as refused:
            reducer.validate(_spec())
        assert refused.value.status_code == 400


class TestMissingAndOther:
    def test_null_and_absent_quotes_are_one_missing_level_listed_last(self) -> None:
        frames, n = _frames(
            [0, 1, 2, 2, 0],
            {"region": pl.Series(["A", None, "A", None, "B"], dtype=pl.String)},
            # q0003 has no row in the analysis frame (its value is null there too).
            present=[True, True, True, False, True],
        )
        response = _run(frames, n)

        assert [row.label for row in response.rows] == ["A", "B", "Missing"]
        missing = response.rows[-1]
        assert missing.kind == "missing"
        assert missing.quotes == 2
        assert response.n_levels == 2

    def test_an_absent_quote_is_missing_even_with_a_value(self) -> None:
        frames, n = _frames([0, 1], {"region": pl.Series(["A", "A"])}, present=[True, False])
        response = _run(frames, n)
        assert [(row.label, row.quotes) for row in response.rows] == [("A", 1), ("Missing", 1)]

    def test_levels_beyond_the_fifteenth_are_summed_into_other(self) -> None:
        # 18 levels: L00 has 4 quotes, L01 3, L02..L17 one each; plus one Missing.
        regions = ["L00"] * 4 + ["L01"] * 3 + [f"L{i:02d}" for i in range(2, 18)] + [None]
        steps = [i % 3 for i in range(len(regions))]
        frames, n = _frames(steps, {"region": pl.Series(regions, dtype=pl.String)})
        response = _run(frames, n)

        labels = [row.label for row in response.rows]
        assert labels[:3] == ["L00", "L01", "L02"]
        assert len([row for row in response.rows if row.kind == "value"]) == MAX_CATEGORICAL_LEVELS
        assert labels[-2:] == ["Other", "Missing"]
        other = _row(response, "Other")
        assert other.kind == "other"
        # L15, L16, L17 are the smallest by quotes then value.
        assert other.merged_levels == 3
        assert other.quotes == 3
        assert response.n_levels == 18
        assert sum(row.quotes for row in response.rows) == n == response.n_quotes
        # Other's mean is the mean over the quotes it merges.
        merged_steps = [steps[regions.index(f"L{i:02d}")] for i in (15, 16, 17)]
        assert other.unweighted.mean_scenario_value == pytest.approx(
            sum(_f32(_GRID[s]) for s in merged_steps) / 3, rel=1e-12
        )

    def test_a_value_named_other_or_missing_is_still_a_value(self) -> None:
        frames, n = _frames([0, 1], {"region": pl.Series(["Other", "Missing"])})
        response = _run(frames, n)
        assert {(row.label, row.kind) for row in response.rows} == {
            ("Other", "value"),
            ("Missing", "value"),
        }

    def test_sparse_levels_have_their_own_quote_figures(self) -> None:
        frames, n = _frames([0, 2, 1], {"region": pl.Series(["x", "y", "z"])})
        response = _run(frames, n)

        x, y, z = (_row(response, label) for label in ("x", "y", "z"))
        assert (x.quotes, y.quotes, z.quotes) == (1, 1, 1)
        assert x.unweighted.share_down == 1.0 and x.unweighted.share_at_edge == 1.0
        assert y.unweighted.share_up == 1.0 and y.unweighted.share_at_edge == 1.0
        assert z.unweighted.share_up == 0.0 and z.unweighted.share_down == 0.0
        assert z.unweighted.share_at_edge == 0.0
        assert z.unweighted.mean_scenario_value == 1.0


class TestNumericBins:
    def test_ten_distinct_values_are_cut_at_their_lower_quantiles(self) -> None:
        values = list(range(1, 11))
        frames, n = _frames([i % 3 for i in range(10)], {"age": pl.Series(values, dtype=pl.Int64)})
        response = _run(frames, n, column="age", binning="numeric")

        # Lower quantiles at 0, 0.05, ..., 0.95 of 1..10 are 1..9 (index floor(0.95 * 9) = 8),
        # so nine bins start at 1..9 and the last runs to the maximum, 10.
        assert [row.label for row in response.rows] == [
            *(f"[{i}, {i + 1})" for i in range(1, 9)),
            "[9, 10]",
        ]
        assert [row.quotes for row in response.rows] == [*([1] * 8), 2]
        assert all(row.kind == "bin" for row in response.rows)
        assert (response.rows[0].lower, response.rows[0].upper) == (1.0, 2.0)
        assert (response.rows[-1].lower, response.rows[-1].upper) == (9.0, 10.0)
        assert response.n_levels == 9

    def test_tied_edges_collapse_into_one_bin(self) -> None:
        values = [5] * 8 + [1, 9]
        frames, n = _frames([1] * 10, {"age": pl.Series(values, dtype=pl.Int32)})
        response = _run(frames, n, column="age", binning="numeric")

        # The quantiles at 0.05..0.95 are all 5: the ties are one bin, which runs to 9.
        assert [(row.label, row.quotes) for row in response.rows] == [("[1, 5)", 1), ("[5, 9]", 9)]

    def test_a_two_valued_column_keeps_one_bin_per_value(self) -> None:
        frames, n = _frames([0, 1, 2, 0, 1], {"flag": pl.Series([0, 1, 0, 1, 0], dtype=pl.Int8)})
        response = _run(frames, n, column="flag", binning="numeric")
        assert [(row.label, row.quotes) for row in response.rows] == [("[0, 1)", 3), ("1", 2)]

    def test_a_single_value_is_one_bin_and_nulls_and_nans_are_missing(self) -> None:
        frames, n = _frames(
            [0, 1, 2, 0],
            {"score": pl.Series([0.1, None, float("nan"), 0.1], dtype=pl.Float32)},
        )
        response = _run(frames, n, column="score", binning="numeric")

        assert [(row.label, row.quotes, row.kind) for row in response.rows] == [
            ("0.1", 2, "bin"),
            ("Missing", 2, "missing"),
        ]
        assert response.rows[0].lower == response.rows[0].upper == _f32(0.1)

    def test_at_most_twenty_bins(self) -> None:
        frames, n = _frames(
            [i % 3 for i in range(1000)], {"x": pl.Series(np.arange(1000), dtype=pl.Int64)}
        )
        response = _run(frames, n, column="x", binning="numeric")
        assert len(response.rows) == 20
        assert sum(row.quotes for row in response.rows) == 1000

    def test_only_missing_values_give_only_the_missing_level(self) -> None:
        frames, n = _frames([0, 1], {"x": pl.Series([None, None], dtype=pl.Float64)})
        response = _run(frames, n, column="x", binning="numeric")
        assert [(row.label, row.quotes) for row in response.rows] == [("Missing", 2)]
        assert response.n_levels == 0


class TestExactLevelCheck:
    def _levels(self, n_levels: int) -> tuple[ChoiceFrames, int]:
        labels = [f"k{i}" for i in range(n_levels)]
        if n_levels == MAX_SEGMENT_LEVELS:
            labels.append("k0")
        return _frames([i % 3 for i in range(len(labels))], {"region": pl.Series(labels)})

    def test_more_than_two_thousand_levels_are_refused_before_truncation(self) -> None:
        frames, n = self._levels(MAX_SEGMENT_LEVELS + 1)
        with pytest.raises(SegmentLevelsExceededError, match="2,001 levels"):
            _run(frames, n)

    def test_two_thousand_levels_are_accepted_and_truncated(self) -> None:
        frames, n = self._levels(MAX_SEGMENT_LEVELS)
        response = _run(frames, n)
        assert response.n_levels == MAX_SEGMENT_LEVELS
        assert response.rows[0].label == "k0"
        assert response.rows[0].quotes == 2
        assert _row(response, "Other").merged_levels == MAX_SEGMENT_LEVELS - MAX_CATEGORICAL_LEVELS


class TestSpread:
    def test_the_spread_is_the_quote_weighted_std_of_level_means(self) -> None:
        # A: steps 0,0 (mean 0.9); B: steps 2,2,2,1 (mean (3*1.1+1)/4).
        frames, n = _frames(
            [0, 0, 2, 2, 2, 1], {"region": pl.Series(["A", "A", "B", "B", "B", "B"])}
        )
        response = _run(frames, n)
        sv = [_f32(v) for v in _GRID]
        means = {"A": sv[0], "B": (3 * sv[2] + sv[1]) / 4}
        counts = {"A": 2, "B": 4}
        overall = sum(counts[k] * means[k] for k in means) / 6
        expected = math.sqrt(sum(counts[k] * (means[k] - overall) ** 2 for k in means) / 6)

        assert segment_spread(response) == pytest.approx(expected, rel=1e-12)

    def test_one_level_has_no_spread(self) -> None:
        frames, n = _frames([0, 2], {"region": pl.Series(["A", "A"])})
        assert segment_spread(_run(frames, n)) == 0.0


class TestCountsAreChecked:
    def test_levels_that_do_not_count_every_quote_fail_loudly(self) -> None:
        frames, n = _frames([0, 1], {"region": pl.Series(["A", "B"])})
        reducer = AnalysisSegments(column="region", binning="categorical", weight="quotes")
        with pytest.raises(ChoiceJoinError, match="2 of 3 chosen quotes"):
            reducer.run(frames, _spec(), n + 1)


# ---------------------------------------------------------------------------
# The key catalogue and its cardinality gate
# ---------------------------------------------------------------------------


def _analysis_handle(**stats: dict[str, Any]) -> dict[str, Any]:
    return {"columns": list(stats), "column_stats": stats}


def _string_stats(approx: int, max_bytes: int = 8) -> dict[str, Any]:
    return {"dtype": "String", "approx_n_unique": approx, "max_string_bytes": max_bytes}


def _factor_rows(n: int, label: str = "L") -> list[dict[str, Any]]:
    return [
        {"__factor_group__": f"{label}{i}", "optimal_scenario_value": 1.0, "quote_count": 1}
        for i in range(n)
    ]


class TestSegmentKeys:
    def test_numeric_columns_are_binned_and_never_refused_for_cardinality(self) -> None:
        keys = segment_keys(
            _analysis_handle(
                premium={"dtype": "Float64", "approx_n_unique": 10**6, "max_string_bytes": None},
                region=_string_stats(3),
            ),
            factor_columns=(),
            factor_tables={},
        )
        assert [(k.key, k.source, k.binning, k.available) for k in keys] == [
            ("premium", "analysis", "numeric", True),
            ("region", "analysis", "categorical", True),
        ]

    def test_the_heuristic_margin_and_the_byte_width_refuse_a_categorical_column(self) -> None:
        keys = segment_keys(
            _analysis_handle(
                at_margin=_string_stats(ADMITTED_APPROX_LEVELS),
                over_margin=_string_stats(ADMITTED_APPROX_LEVELS + 180),
                wide=_string_stats(3, max_bytes=MAX_SEGMENT_KEY_BYTES + 1),
                flag={"dtype": "Boolean", "approx_n_unique": 2, "max_string_bytes": None},
            ),
            factor_columns=(),
            factor_tables={},
        )
        by_key = {key.key: key for key in keys}
        assert by_key["at_margin"].available is True
        assert by_key["over_margin"].available is False
        assert "1,980" in by_key["over_margin"].unavailable_reason
        assert "1,800" in by_key["over_margin"].unavailable_reason
        assert by_key["wide"].available is False
        assert "257 bytes" in by_key["wide"].unavailable_reason
        assert by_key["flag"].binning == "categorical" and by_key["flag"].available

    def test_rating_factors_use_exact_table_counts_and_composite_width(self) -> None:
        wide_label = "x" * 200 + "\x1f" + "y" * 60
        keys = segment_keys(
            None,
            factor_columns=(("region",), ("region", "age"), ("postcode",)),
            factor_tables={
                "region": _factor_rows(3),
                "region:age": [
                    {
                        "__factor_group__": wide_label,
                        "optimal_scenario_value": 1.0,
                        "quote_count": 1,
                    }
                ],
                "postcode": _factor_rows(MAX_SEGMENT_LEVELS + 1),
            },
        )
        by_key = {key.key: key for key in keys}
        assert [key.key for key in keys] == ["region", "region:age", "postcode"]
        assert all(key.source == "factor" and key.binning == "categorical" for key in keys)
        assert by_key["region"].available is True
        assert by_key["region:age"].available is False
        assert "261 bytes" in by_key["region:age"].unavailable_reason
        assert by_key["postcode"].available is False
        assert "2,001 levels" in by_key["postcode"].unavailable_reason

    def test_factors_beyond_the_thirtieth_are_listed_as_unavailable(self) -> None:
        names = [f"f{i}" for i in range(MAX_FACTOR_SEGMENT_KEYS + 2)]
        keys = segment_keys(
            None,
            factor_columns=tuple((name,) for name in names),
            factor_tables={name: _factor_rows(2) for name in names},
        )
        assert [key.available for key in keys] == [True] * 30 + [False, False]
        assert "30" in keys[-1].unavailable_reason

    def test_a_factor_named_like_an_analysis_column_is_listed_once_as_the_factor(self) -> None:
        keys = segment_keys(
            _analysis_handle(region=_string_stats(3), tier=_string_stats(2)),
            factor_columns=(("region",),),
            factor_tables={"region": _factor_rows(3)},
        )
        assert [(key.key, key.source) for key in keys] == [
            ("tier", "analysis"),
            ("region", "factor"),
        ]

    def test_no_analysis_columns_and_no_factors_has_no_keys(self) -> None:
        assert segment_keys(None, factor_columns=(), factor_tables={}) == []


# ---------------------------------------------------------------------------
# Routes: real solves
# ---------------------------------------------------------------------------


def _segments(client: Any, job_id: str, key: str, **extra: Any) -> Any:
    return client.post(OPTIMISER_SEGMENTS_ROUTE, json={"job_id": job_id, "key": key, **extra})


def _index(client: Any, job_id: str, point_index: int | None = None) -> Any:
    params: dict[str, Any] = {"job_id": job_id}
    if point_index is not None:
        params["point_index"] = point_index
    return client.get(OPTIMISER_SEGMENT_INDEX_ROUTE, params=params)


def test_the_route_constants_are_the_served_paths() -> None:
    assert OPTIMISER_SEGMENTS_ROUTE == "/api/optimiser/segments"
    assert OPTIMISER_SEGMENT_INDEX_ROUTE == "/api/optimiser/segments/index"


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestSegmentsRoute:
    def test_the_as_solved_breakdown_by_an_analysis_column(self, client, tmp_path):
        job_id, status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        assert status["result"]["segment_keys"] == [
            {
                "key": "region",
                "source": "analysis",
                "binning": "categorical",
                "available": True,
                "unavailable_reason": None,
            }
        ]

        response = _segments(client, job_id, "region", weight="optimal_objective")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["point_index"] is None
        assert body["n_quotes"] == 9
        assert {row["label"]: row["quotes"] for row in body["rows"]} == {
            "North": 3,
            "South": 3,
            "Île": 3,
        }
        # Objective-weighted: the level weights sum to the solve's total objective.
        assert math.fsum(row["weight_total"] for row in body["rows"]) == pytest.approx(
            status["result"]["total_objective"], rel=1e-9
        )
        assert body["weight_label"] == "Objective at the chosen scenario"

    def test_an_unknown_key_or_weight_is_a_422(self, client, tmp_path):
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )

        unknown = _segments(client, job_id, "noise")
        assert unknown.status_code == 422
        assert unknown.json()["detail"]["error_code"] == "segment_key_unknown"
        assert "region" in unknown.json()["detail"]["message"]

        bad_weight = _segments(client, job_id, "region", weight="optimal_price")
        assert bad_weight.status_code == 422
        assert bad_weight.json()["detail"]["error_code"] == "segment_weight_unknown"

    def test_a_side_input_quote_absent_from_the_frame_is_missing(self, client, tmp_path):
        from tests.test_optimiser_outcomes import _regions_frame

        job_id, _status = _solved(client, _side_graph(_scored(tmp_path), _regions_frame(tmp_path)))
        body = _segments(client, job_id, "region").json()

        assert body["rows"][-1] == {**body["rows"][-1], "label": "Missing", "kind": "missing"}
        assert body["rows"][-1]["quotes"] == 1
        assert sum(row["quotes"] for row in body["rows"]) == 9

    def test_a_frontier_point_is_described_from_its_own_choices(self, client, tmp_path):
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        frontier = run_frontier_and_wait(
            client,
            {"job_id": job_id, "threshold_ranges": {"volume": [5.0, 8.0]}, "n_points_per_dim": 3},
        )
        assert frontier["status"] == "completed", frontier
        point = frontier["result"]["points"][2]

        response = _segments(client, job_id, "region", point_index=2, weight="optimal_volume")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["point_index"] == 2
        assert body["frontier_generation"] == _store.require_job(job_id)["frontier_generation"] == 1
        assert math.fsum(row["weight_total"] for row in body["rows"]) == pytest.approx(
            point["totals"]["volume"], rel=1e-9
        )
        apply = pl.read_parquet(
            _store.require_job(job_id)["artifact_handles"]["frontier_apply_result:2"]["path"]
        )
        assert body["mean_scenario_value"] == pytest.approx(
            apply["optimal_scenario_value"].cast(pl.Float64).mean(), rel=1e-12
        )

    def test_an_unavailable_target_is_a_410(self, client, tmp_path):
        job_id, _status = _solved(
            client, _data_graph(_scored(tmp_path), analysis_columns=["region"])
        )
        frontier = run_frontier_and_wait(
            client,
            {"job_id": job_id, "threshold_ranges": {"volume": [5.0, 8.0]}, "n_points_per_dim": 3},
        )
        assert frontier["status"] == "completed", frontier
        _store.clear_result_data(job_id)

        gone = _segments(client, job_id, "region", point_index=1)
        assert gone.status_code == 410
        assert gone.json()["detail"]["error_code"] == "frontier_point_unavailable"

        Path(_store.require_job(job_id)["artifact_handles"]["apply_result"]["path"]).unlink()
        assert _segments(client, job_id, "region").status_code == 410

    def test_a_ratebook_result_is_broken_down_by_rating_factor(self, client, tmp_path):
        from tests.test_optimiser_routes_real_library import (
            _poll_until_done,
            _ratebook_fixture_paths,
            _ratebook_graph,
            _solve_completed,
        )

        scored_path, banding_path = _ratebook_fixture_paths(tmp_path, with_age=True)
        job_id = _solve_completed(
            client, _ratebook_graph(scored_path, banding_path, [["region", "age"]])
        )
        result = _poll_until_done(client, job_id)["result"]
        assert [key["key"] for key in result["segment_keys"]] == ["region:age"]

        response = _segments(client, job_id, "region:age")

        assert response.status_code == 200, response.text
        body = response.json()
        rates = result["factor_tables"]["region:age"]
        assert {row["label"]: row["quotes"] for row in body["rows"]} == {
            row["__factor_group__"]: row["quote_count"] for row in rates
        }
        assert sum(row["quotes"] for row in body["rows"]) == 9
        assert (
            sum(row["deployed_factor_differs"] for row in body["rows"])
            == result["adjustments"]["deployed_factor_differs"]
        )


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestSegmentIndexRoute:
    def test_the_index_ranks_keys_by_spread_and_is_cached_until_a_recompute(
        self, client, tmp_path, monkeypatch
    ):
        from haute.routes import optimiser as optimiser_routes

        scored = _scored(tmp_path)
        frame = pl.read_parquet(scored).with_columns(
            (pl.col("quote_id").str.slice(1).cast(pl.Int32) % 2).alias("parity")
        )
        frame.write_parquet(scored)
        job_id, _status = _solved(
            client, _data_graph(scored, analysis_columns=["region", "parity"])
        )

        response = _index(client, job_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["statistic"]["label"] == "Adjustment spread"
        spreads = {key["key"]: key["spread"] for key in body["keys"]}
        for key in ("region", "parity"):
            breakdown = _segments(client, job_id, key).json()
            rows = breakdown["rows"]
            n = sum(row["quotes"] for row in rows)
            mean = sum(row["quotes"] * row["unweighted"]["mean_scenario_value"] for row in rows) / n
            expected = math.sqrt(
                sum(
                    row["quotes"] * (row["unweighted"]["mean_scenario_value"] - mean) ** 2
                    for row in rows
                )
                / n
            )
            assert spreads[key] == pytest.approx(expected, abs=1e-12)
        ranked = [key["key"] for key in body["keys"]]
        assert ranked == sorted(spreads, key=lambda k: (-spreads[k], k))
        generation = _store.require_job(job_id)["frontier_generation"]
        assert set(_store.require_job(job_id)[SEGMENT_INDEXES_KEY]) == {(generation, None)}

        # A cached index is answered without a query.
        def refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("the cached index ran a query")

        with monkeypatch.context() as patched:
            patched.setattr(optimiser_routes._choice_service, "choice_query", refuse)
            assert _index(client, job_id).json() == body

        recomputed = run_frontier_and_wait(
            client,
            {"job_id": job_id, "threshold_ranges": {"volume": [5.0, 8.0]}, "n_points_per_dim": 3},
        )
        assert recomputed["status"] == "completed", recomputed
        assert _store.require_job(job_id)[SEGMENT_INDEXES_KEY] == {}


def _k_frame(root: Path, n_quotes: int) -> Path:
    """One row per solved quote, whose ``region`` is ``k0``..``k<n-1>``."""
    frame = pl.DataFrame(
        {
            "quote_id": [f"q{quote:03d}" for quote in range(n_quotes)],
            "region": [f"k{quote}" for quote in range(n_quotes)],
        }
    )
    path = root / "keys.parquet"
    frame.write_parquet(path)
    return path


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestCardinalityGate:
    N_QUOTES = MAX_SEGMENT_LEVELS + 1

    def _solve(self, client: Any, tmp_path: Path) -> str:
        job_id, _status = _solved(
            client,
            _side_graph(
                _scored(tmp_path, n_quotes=self.N_QUOTES), _k_frame(tmp_path, self.N_QUOTES)
            ),
        )
        return job_id

    def test_k0_to_k2000_is_refused_by_the_heuristic_margin(self, client, tmp_path):
        job_id = self._solve(client, tmp_path)
        stats = _store.require_job(job_id)["artifact_handles"]["quote_analysis"]["column_stats"]
        assert stats["region"]["approx_n_unique"] == 1980
        [key] = _store.require_job(job_id)["result"]["segment_keys"]
        assert key["available"] is False
        assert "1,980" in key["unavailable_reason"]

        refused = _segments(client, job_id, "region")
        assert refused.status_code == 422
        assert refused.json()["detail"]["error_code"] == "segment_key_unavailable"
        index = _index(client, job_id).json()
        assert index["keys"] == [{**key, "spread": None}]

    def test_the_exact_check_refuses_2001_levels_and_accepts_2000(self, client, tmp_path):
        job_id = self._solve(client, tmp_path)
        # Inject an admitted estimate: the gate lets the key through.
        job = _store.require_job(job_id)
        for result_key in ("result", "base_result"):
            result = dict(job[result_key])
            result["segment_keys"] = [
                {**key, "available": True, "unavailable_reason": None}
                for key in result["segment_keys"]
            ]
            _store.atomic_update(job_id, {result_key: result}, expected_status="completed")

        refused = _segments(client, job_id, "region")
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"]["error_code"] == "segment_key_unavailable"
        assert "2,001 levels" in refused.json()["detail"]["message"]
        index = _index(client, job_id).json()
        [indexed] = index["keys"]
        assert indexed["available"] is False
        assert "2,001 levels" in indexed["unavailable_reason"]

        # One quote shares another's level: 2,000 levels are accepted.
        path = _store.require_job(job_id)["artifact_handles"]["quote_analysis"]["path"]
        table = pl.read_parquet(path).with_columns(
            pl.when(pl.col("region") == "k2000")
            .then(pl.lit("k0"))
            .otherwise(pl.col("region"))
            .alias("region")
        )
        table.write_parquet(path)

        accepted = _segments(client, job_id, "region")
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body["n_levels"] == MAX_SEGMENT_LEVELS
        assert body["rows"][0]["label"] == "k0"
        other = [row for row in body["rows"] if row["kind"] == "other"]
        assert other[0]["merged_levels"] == MAX_SEGMENT_LEVELS - MAX_CATEGORICAL_LEVELS
        assert sum(row["quotes"] for row in body["rows"]) == self.N_QUOTES
