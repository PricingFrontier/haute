"""3b.2 + 3b.5 + 3b.8 — composite ratebook apply, loud-neutral misses, mirrors.

Artifact-format finding (pinned by the real-solver tests below): price-contour
names a composite factor table by colon-joining its component columns
(``":".join(spec)`` — ``ratebook.py:566``) and keys composite levels by
joining the component values with the ASCII unit separator ``"\\x1f"`` (the
library's documented ``separator`` default; its own
``RatebookResult.to_rating_entries`` decodes exactly this way).  haute's save
path (``_serialise_ratebook_factor_tables`` → ``_build_artifact_payload`` →
``json.dumps``) carries those keys verbatim (JSON ``\\u001f``), so saved
composite levels are componentwise splittable — therefore 3b.2 is fixed by a
multi-column join, not by rejecting composite groups at save time.

Composite-ness is self-describing: a join of two or more component values
always embeds the unit separator, and a control character never appears in
real level labels.  A table whose name contains ``":"`` but whose levels have
no separator is a literal single column named ``"a:b"`` and joins as such;
malformed shapes (level arity mismatch, separator levels under a
non-composite name, duplicate/empty components, missing frame columns) raise
loud ``ValueError``s naming the table.

Miss semantics (3b.5): the blanket ``defaultValue: "1.0"`` is gone.  The
apply path opts in to the W3a ``onMissing: "neutral"`` machinery explicitly —
unseen levels still rate 1.0 (an optimiser relativity is a multiplicative
adjustment on an already-rated price, so "no adjustment" is the safe
degradation for apply-to-broader-population deploy scoring; failing the whole
request would turn one novel quote into an outage) — but every miss is now
counted and logged at WARNING (``rating_table_lookup_misses`` with table,
count and the missing keys) and flagged per row in the explainability ladder.

The mirror: ``_match_ratebook_entry`` compares keys through the shared
``normalise_rating_key`` (W3a.4), so for float/int-keyed frames the trace
agrees with what the engine join actually did.
"""

from __future__ import annotations

import json
from typing import Any

import polars as pl
import pytest
import structlog.testing

from haute._builders import _apply_ratebook, _ratebook_lookup_table
from haute._optimiser_apply_explainability import _match_ratebook_entry
from haute._rating import _apply_rating_table

MISS_EVENT = "rating_table_lookup_misses"
SEP = "\x1f"


def _table(name: str, levels: dict[Any, float]) -> list[dict[str, Any]]:
    """Saved-artifact rows for one factor table (real serialisation shape)."""
    return [
        {"__factor_group__": level, "optimal_scenario_value": value, "quote_count": 1}
        for level, value in levels.items()
    ]


def _artifact(factor_tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {"version": "rb_v1", "mode": "ratebook", "factor_tables": factor_tables}


# ---------------------------------------------------------------------------
# Engine <-> explainability mirror agreement (the W3a.4 pattern)
# ---------------------------------------------------------------------------


def _engine_matches(frame: pl.DataFrame, name: str, entries: list[dict[str, Any]]) -> list[bool]:
    """Per-row engine ground truth: did the ratebook lookup join match?

    Runs the engine's own lookup table (as built by the apply path) without
    the neutral fill, so nulls mark true misses — independent of the mirror
    under test.
    """
    table = _ratebook_lookup_table(name, entries)
    assert table is not None
    out = _apply_rating_table(frame.lazy(), table).collect()
    return [value is not None for value in out[table["outputColumn"]].to_list()]


def _mirror_matches(frame: pl.DataFrame, name: str, entries: list[dict[str, Any]]) -> list[bool]:
    table = _ratebook_lookup_table(name, entries)
    assert table is not None
    join_columns = table["factors"]
    results: list[bool] = []
    for row in frame.iter_rows(named=True):
        input_values = [row.get(column) for column in join_columns]
        results.append(_match_ratebook_entry(entries, join_columns, input_values, name) is not None)
    return results


def assert_engine_and_mirror_agree(
    frame: pl.DataFrame,
    name: str,
    entries: list[dict[str, Any]],
    expected: list[bool] | None = None,
) -> None:
    engine = _engine_matches(frame, name, entries)
    mirror = _mirror_matches(frame, name, entries)
    assert engine == mirror, f"engine={engine} mirror={mirror}"
    if expected is not None:
        assert engine == expected


class TestMirrorAgreesWithEngine:
    def test_float_frame_column_vs_string_levels(self) -> None:
        """The mandated float-key case: Float64 25.0 must match level "25"
        in BOTH the engine join and the trace mirror; the verbatim label
        "25.0" must match in NEITHER."""
        entries = _table("age", {"25": 2.0, "30.5": 3.0, "40.0": 4.0})
        frame = pl.DataFrame({"age": [25.0, 30.5, 40.0, 99.0]})
        assert_engine_and_mirror_agree(frame, "age", entries, [True, True, False, False])

    def test_str_mirror_would_lie_about_float_keys(self) -> None:
        """Documents the divergence the normalise_rating_key switch removes:
        plain str(25.0) is "25.0" and would have missed the "25" level."""
        entries = _table("age", {"25": 2.0})
        assert _match_ratebook_entry(entries, ["age"], [25.0], "age") is not None
        assert str(25.0) != "25"  # the old comparison basis

    def test_int_frame_column_vs_string_levels(self) -> None:
        entries = _table("age", {"25": 2.0})
        frame = pl.DataFrame({"age": [25, 26]})
        assert_engine_and_mirror_agree(frame, "age", entries, [True, False])

    def test_numeric_levels_vs_int_frame_column(self) -> None:
        """Hand-built artifacts can carry numeric levels (JSON numbers)."""
        entries = _table("age", {25.0: 2.0})
        frame = pl.DataFrame({"age": [25, 26]})
        assert_engine_and_mirror_agree(frame, "age", entries, [True, False])

    def test_null_input_never_matches(self) -> None:
        entries = _table("age", {"25": 2.0})
        frame = pl.DataFrame({"age": [25.0, None]})
        assert_engine_and_mirror_agree(frame, "age", entries, [True, False])

    def test_composite_float_components(self) -> None:
        """Composite levels split before normalisation, so an int-like float
        component column (25.0) matches its digit-string part ("25")."""
        entries = _table(
            "channel:age",
            {f"online{SEP}25": 1.1, f"phone{SEP}30.5": 0.9},
        )
        frame = pl.DataFrame(
            {
                "channel": ["online", "phone", "online", "phone"],
                "age": [25.0, 30.5, 30.5, None],
            }
        )
        assert_engine_and_mirror_agree(frame, "channel:age", entries, [True, True, False, False])

    def test_composite_verbatim_string_components_stay_verbatim(self) -> None:
        """A part "25.0" is a label: the float 25.0 canonicalises to "25"
        and must miss — in both engine and mirror."""
        entries = _table("channel:age", {f"online{SEP}25.0": 1.1})
        frame = pl.DataFrame({"channel": ["online"], "age": [25.0]})
        assert_engine_and_mirror_agree(frame, "channel:age", entries, [False])

    def test_duplicate_level_resolves_to_last_in_both(self) -> None:
        """Engine dedups keep="last"; the mirror walks reversed."""
        entries = [
            {"__factor_group__": f"online{SEP}18-25", "optimal_scenario_value": 1.05},
            {"__factor_group__": f"online{SEP}18-25", "optimal_scenario_value": 1.20},
        ]
        frame = pl.DataFrame({"channel": ["online"], "age_band": ["18-25"]})
        out = _apply_ratebook(frame.lazy(), _artifact({"channel:age_band": entries}), "", "__v__")
        engine_value = out.collect()["channel:age_band_optimised_factor"][0]
        matched = _match_ratebook_entry(
            entries, ["channel", "age_band"], ["online", "18-25"], "channel:age_band"
        )
        assert matched is not None
        assert engine_value == pytest.approx(1.20)
        assert matched["optimal_scenario_value"] == pytest.approx(1.20)


# ---------------------------------------------------------------------------
# 3b.8 — real solver end-to-end: solve -> save (real helpers) -> apply
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_ratebook_solve() -> dict[str, Any]:
    """One real RatebookOptimiser solve over a composite and a single group.

    Objectives are shaped so per-group optima differ (peaks at different
    scenario values), giving non-trivial factor tables to assert against.
    """
    from price_contour import RatebookOptimiser

    peaks = {
        ("online", "18-25"): 0.9,
        ("phone", "18-25"): 1.1,
        ("online", "26-40"): 1.0,
        ("phone", "26-40"): 1.1,
    }
    region_peak = {"north": 0.9, "south": 1.1}

    quotes = []
    for index, (channel, age_band) in enumerate(peaks):
        for sub, region in enumerate(("north", "south")):
            quotes.append((f"q{index}_{sub}", channel, age_band, region))

    rows = []
    for quote_id, channel, age_band, region in quotes:
        peak = 0.5 * peaks[(channel, age_band)] + 0.5 * region_peak[region]
        for step, scenario_value in enumerate((0.9, 1.0, 1.1)):
            rows.append(
                {
                    "quote_id": quote_id,
                    "scenario_index": step,
                    "scenario_value": scenario_value,
                    "expected_income": 100.0 - 200.0 * (scenario_value - peak) ** 2,
                }
            )
    scored = pl.DataFrame(rows).with_columns(
        pl.col("scenario_index").cast(pl.Int32),
        pl.col("scenario_value").cast(pl.Float32),
        pl.col("expected_income").cast(pl.Float32),
    )
    factors_df = pl.DataFrame(
        {
            "quote_id": [quote[0] for quote in quotes],
            "channel": [quote[1] for quote in quotes],
            "age_band": [quote[2] for quote in quotes],
            "region": [quote[3] for quote in quotes],
        }
    )
    factor_columns = [["channel", "age_band"], ["region"]]

    solver = RatebookOptimiser(
        objective="expected_income",
        constraints={},
        factor_columns=factor_columns,
        candidate_min=0.9,
        candidate_max=1.1,
        candidate_steps=3,
        max_cd_iterations=2,
        max_iter=10,
    )
    solve_result = solver.solve(scored, factors_df)
    return {
        "solve_result": solve_result,
        "factors_df": factors_df,
        "factor_columns": factor_columns,
    }


@pytest.fixture(scope="module")
def real_ratebook_artifact_payload(real_ratebook_solve: dict[str, Any]) -> dict[str, Any]:
    """The artifact exactly as the save route would write it.

    Uses the production serialisation (`_serialise_ratebook_factor_tables` +
    `_build_artifact_payload`) so a drift in the saved format fails here, not
    in a hand-rolled fixture.
    """
    from haute.routes._optimiser_service import (
        _ratebook_factor_level_counts,
        _serialise_ratebook_factor_tables,
    )
    from haute.routes.optimiser import _build_artifact_payload

    solve_result = real_ratebook_solve["solve_result"]
    counts = _ratebook_factor_level_counts(
        real_ratebook_solve["factors_df"], real_ratebook_solve["factor_columns"]
    )
    serialised = _serialise_ratebook_factor_tables(solve_result.factor_tables, counts, {})
    job = {
        "node_label": "Ratebook Opt",
        "config": {
            "mode": "ratebook",
            "objective": "expected_income",
            "constraints": {},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        },
        "result": {"factor_tables": serialised},
    }
    return _build_artifact_payload(job, solve_result, version_override="rb_e2e_v1")


class TestRealSolverEndToEnd:
    def test_solver_emits_colon_named_unit_separator_levels(
        self, real_ratebook_solve: dict[str, Any]
    ) -> None:
        """Pins the artifact-format finding the 3b.2 decision rests on."""
        tables = real_ratebook_solve["solve_result"].factor_tables
        assert set(tables) == {"channel:age_band", "region"}
        assert all(SEP in level for level in tables["channel:age_band"])
        assert all(SEP not in level for level in tables["region"])

    def test_factor_values_land_on_the_right_rows(
        self,
        real_ratebook_solve: dict[str, Any],
        real_ratebook_artifact_payload: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """solve -> save (real payload, JSON round-trip) -> apply via the
        executor node: every row gets its own group's solved factor."""
        from haute._types import GraphNode, NodeData, NodeType
        from haute.executor import _build_node_fn

        artifact_path = tmp_path / "ratebook_e2e.json"
        artifact_path.write_text(
            json.dumps(real_ratebook_artifact_payload, indent=2, default=str),
            encoding="utf-8",
        )

        tables = real_ratebook_solve["solve_result"].factor_tables
        composite = tables["channel:age_band"]
        region_table = tables["region"]

        apply_frame = pl.DataFrame(
            {
                "quote_id": ["a1", "a2", "a3", "a4"],
                "channel": ["online", "phone", "online", "phone"],
                "age_band": ["18-25", "18-25", "26-40", "26-40"],
                "region": ["north", "south", "south", "north"],
                "price": [100.0, 200.0, 300.0, 400.0],
            }
        )
        node = GraphNode(
            id="apply_1",
            data=NodeData(
                label="apply",
                nodeType=NodeType.OPTIMISER_APPLY,
                config={"sourceType": "file", "artifact_path": str(artifact_path)},
            ),
        )
        _, fn, _ = _build_node_fn(node, source_names=["base"])
        out = fn(apply_frame.lazy()).collect()

        expected_composite = [
            composite[f"online{SEP}18-25"],
            composite[f"phone{SEP}18-25"],
            composite[f"online{SEP}26-40"],
            composite[f"phone{SEP}26-40"],
        ]
        expected_region = [
            region_table["north"],
            region_table["south"],
            region_table["south"],
            region_table["north"],
        ]
        assert out["channel:age_band_optimised_factor"].to_list() == pytest.approx(
            expected_composite
        )
        assert out["region_optimised_factor"].to_list() == pytest.approx(expected_region)
        assert out["optimised_factor"].to_list() == pytest.approx(
            [c * r for c, r in zip(expected_composite, expected_region)]
        )
        assert out["__optimiser_version__"].to_list() == ["rb_e2e_v1"] * 4
        # Distinct groups got distinct factors — the join really is per-group.
        assert len(set(expected_composite)) > 1
        assert len(set(expected_region)) > 1

    def test_unseen_levels_rate_neutral_and_warn_end_to_end(
        self,
        real_ratebook_artifact_payload: dict[str, Any],
        real_ratebook_solve: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        """A quote in a level the optimiser never saw gets 1.0 — loudly."""
        from haute._types import GraphNode, NodeData, NodeType
        from haute.executor import _build_node_fn

        artifact_path = tmp_path / "ratebook_unseen.json"
        artifact_path.write_text(
            json.dumps(real_ratebook_artifact_payload, indent=2, default=str),
            encoding="utf-8",
        )
        apply_frame = pl.DataFrame(
            {
                "quote_id": ["new1"],
                "channel": ["online"],
                "age_band": ["18-25"],
                "region": ["east"],  # never seen by the solver
                "price": [100.0],
            }
        )
        node = GraphNode(
            id="apply_1",
            data=NodeData(
                label="apply",
                nodeType=NodeType.OPTIMISER_APPLY,
                config={"sourceType": "file", "artifact_path": str(artifact_path)},
            ),
        )
        _, fn, _ = _build_node_fn(node, source_names=["base"])
        with structlog.testing.capture_logs() as logs:
            out = fn(apply_frame.lazy()).collect()

        assert out["region_optimised_factor"].to_list() == [pytest.approx(1.0)]
        composite_value = real_ratebook_solve["solve_result"].factor_tables["channel:age_band"][
            f"online{SEP}18-25"
        ]
        assert out["optimised_factor"].to_list() == [pytest.approx(composite_value * 1.0)]
        miss_logs = [log for log in logs if log["event"] == MISS_EVENT]
        assert len(miss_logs) == 1
        assert miss_logs[0]["table"] == "region"
        assert miss_logs[0]["miss_count"] == 1
        assert miss_logs[0]["missing_keys"] == [{"region": "east"}]


# ---------------------------------------------------------------------------
# 3b.10 — float-emitted level labels canonicalised at save time
# ---------------------------------------------------------------------------


def _float_factor_solve(factor_columns: list[list[str]]) -> tuple[Any, pl.DataFrame]:
    """Micro real solve whose factor column is Float64 [25.0, 30.5]."""
    from price_contour import RatebookOptimiser

    quote_specs = [
        ("q1", "online", 25.0, 0.9),
        ("q2", "phone", 30.5, 1.1),
        ("q3", "online", 25.0, 0.9),
        ("q4", "phone", 30.5, 1.1),
    ]
    rows = []
    for quote_id, _channel, _age, peak in quote_specs:
        for step, scenario_value in enumerate((0.9, 1.0, 1.1)):
            rows.append(
                {
                    "quote_id": quote_id,
                    "scenario_index": step,
                    "scenario_value": scenario_value,
                    "expected_income": 100.0 - 200.0 * (scenario_value - peak) ** 2,
                }
            )
    scored = pl.DataFrame(rows).with_columns(
        pl.col("scenario_index").cast(pl.Int32),
        pl.col("scenario_value").cast(pl.Float32),
        pl.col("expected_income").cast(pl.Float32),
    )
    factors_df = pl.DataFrame(
        {
            "quote_id": [spec[0] for spec in quote_specs],
            "channel": [spec[1] for spec in quote_specs],
            "age": [spec[2] for spec in quote_specs],
        }
    )
    solver = RatebookOptimiser(
        objective="expected_income",
        constraints={},
        factor_columns=factor_columns,
        candidate_min=0.9,
        candidate_max=1.1,
        candidate_steps=3,
        max_cd_iterations=1,
        max_iter=5,
    )
    return solver.solve(scored, factors_df), factors_df


class TestFloatEmittedLevelsCanonicalisedAtSave:
    """3b.10 FIXED — save-time canonicalisation of solver-emitted levels.

    REVISED (3b.10): this class previously pinned the KNOWN LIMITATION that
    price-contour's verbatim float labels ("25.0") missed apply's canonical
    frame keys ("25"), rating every int-like row of a float-typed factor
    column loud-neutral.  It was built as the tripwire for exactly this
    change; with the save path now canonicalising emitted levels through
    ``normalise_rating_key`` (``_serialise_ratebook_factor_tables`` /
    ``_ratebook_factor_level_counts``), it pins the FIXED contract instead:

    * price-contour still emits float VALUES verbatim ("25.0" — the
      emitted-format pins stay so a library-side format change is caught
      here), but the SAVED artifact carries canonical labels ("25").
    * A Float64 factor column therefore round-trips solve -> save -> apply
      with every row matching its own solved level: zero misses, no
      ``rating_table_lookup_misses`` WARNING, engine and mirror agree.
    * Non-integer floats ("30.5") and never-numeric string labels are
      unchanged by canonicalisation.
    """

    def test_float64_levels_round_trip_solve_save_apply_with_zero_misses(self) -> None:
        from haute.routes._optimiser_service import (
            _ratebook_factor_level_counts,
            _serialise_ratebook_factor_tables,
        )

        solve_result, factors_df = _float_factor_solve([["age"]])

        # Emitted-format pin: the library still emits verbatim float reprs.
        assert set(solve_result.factor_tables["age"]) == {"25.0", "30.5"}

        counts = _ratebook_factor_level_counts(factors_df, [["age"]])
        serialised = _serialise_ratebook_factor_tables(solve_result.factor_tables, counts, {})

        # Save-time canonicalisation: int-like float labels collapse to the
        # canonical digit string; non-integer floats keep their digits.
        assert [row["__factor_group__"] for row in serialised["age"]] == ["25", "30.5"]
        assert [row["quote_count"] for row in serialised["age"]] == [2, 2]

        artifact = _artifact(serialised)
        apply_frame = pl.DataFrame({"age": [25.0, 30.5]})
        with structlog.testing.capture_logs() as logs:
            out = _apply_ratebook(apply_frame.lazy(), artifact, "v1", "__ver__").collect()

        # Every row gets its own solved factor — zero misses, no WARNING.
        assert out["age_optimised_factor"].to_list() == pytest.approx(
            [
                solve_result.factor_tables["age"]["25.0"],
                solve_result.factor_tables["age"]["30.5"],
            ]
        )
        assert [log for log in logs if log["event"] == MISS_EVENT] == []

        # Engine and mirror agree: both rows are seen levels now.
        entries = serialised["age"]
        assert _engine_matches(apply_frame, "age", entries) == [True, True]
        assert _mirror_matches(apply_frame, "age", entries) == [True, True]
        assert _match_ratebook_entry(entries, ["age"], [25.0], "age") is not None
        assert _match_ratebook_entry(entries, ["age"], [30.5], "age") is not None

    def test_composite_float_component_round_trips_with_zero_misses(self) -> None:
        from haute.routes._optimiser_service import (
            _ratebook_factor_level_counts,
            _serialise_ratebook_factor_tables,
        )

        solve_result, factors_df = _float_factor_solve([["channel", "age"]])

        # Emitted-format pin for the composite shape.
        assert set(solve_result.factor_tables["channel:age"]) == {
            f"online{SEP}25.0",
            f"phone{SEP}30.5",
        }

        counts = _ratebook_factor_level_counts(factors_df, [["channel", "age"]])
        serialised = _serialise_ratebook_factor_tables(solve_result.factor_tables, counts, {})

        # Composite levels canonicalise per component: the string component
        # stays verbatim, the int-like float component collapses.
        assert [row["__factor_group__"] for row in serialised["channel:age"]] == [
            f"online{SEP}25",
            f"phone{SEP}30.5",
        ]

        apply_frame = pl.DataFrame({"channel": ["online", "phone"], "age": [25.0, 30.5]})
        with structlog.testing.capture_logs() as logs:
            out = _apply_ratebook(
                apply_frame.lazy(), _artifact(serialised), "v1", "__ver__"
            ).collect()

        assert out["channel:age_optimised_factor"].to_list() == pytest.approx(
            [
                solve_result.factor_tables["channel:age"][f"online{SEP}25.0"],
                solve_result.factor_tables["channel:age"][f"phone{SEP}30.5"],
            ]
        )
        assert [log for log in logs if log["event"] == MISS_EVENT] == []

        # Engine and mirror agree row by row: every row is a seen level.
        entries = serialised["channel:age"]
        assert _engine_matches(apply_frame, "channel:age", entries) == [True, True]
        assert _mirror_matches(apply_frame, "channel:age", entries) == [True, True]
