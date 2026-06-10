"""3a.4 — numeric factor keys normalise identically in the engine and traces.

The rating engine joins on Utf8-cast factor columns.  At HEAD an int-like
float column (``25.0``) cast to ``"25.0"`` and missed string-keyed entries
(``"25"``), then fell into the 3a.3 miss path; non-integer floats already
matched their string form (``30.5`` -> ``"30.5"``).  Repro evidence (HEAD):

    frame age [25.0, 30.5] vs keys {"25", "30.5"}  ->  f = [None, 3.0]
    entry key 25.0 vs int frame [25]               ->  f = [None]

The fix gives both join sides one canonical key form (``_rating_key_expr``)
and gives trace enrichment the same canonicalisation through the shared
Python mirror ``normalise_rating_key`` — replacing the ``str()`` re-derivation
in ``_trace_enrichment._enrich_single_table`` that would otherwise lie about
matched/default flags once the engine is fixed.

Canonicalisation contract (documented here, pinned below):

* Finite int-like floats inside the Int64 range collapse to their integer
  digit string ("25.0" -> "25"); all other values keep the engine's Utf8
  cast formatting.
* String keys are verbatim labels — never collapsed ("25.0" != "25").
* Null keys never match (left-join semantics, ``join_nulls=False``).
* The Python mirror is exact for Int*/Float64/Utf8/Boolean values; Float32
  columns lose their dtype at the trace JSON boundary, so non-integer
  Float32 keys are outside the enrichment-agreement guarantee.
"""

from __future__ import annotations

import json
import math
from typing import Any

import polars as pl
import pytest

from haute._rating import (
    RatingTableMissError,
    _apply_rating_table,
    _rating_key_expr,
    apply_rating_step_from_config,
    normalise_rating_key,
)
from haute._rating_step_config import (
    compact_rating_step_config_for_sidecar,
    expand_rating_step_config_from_sidecar,
)
from haute._trace_correlation import _jsonify_row
from haute._trace_enrichment import _enrich_single_table

# ---------------------------------------------------------------------------
# Engine join semantics
# ---------------------------------------------------------------------------


class TestEngineKeyNormalisation:
    def test_int_like_float_column_matches_string_keys(self) -> None:
        """The 3a.4 repro: Float64 25.0 must match the string key "25"."""
        config = {
            "tables": [
                {
                    "name": "Age",
                    "factors": ["age"],
                    "outputColumn": "f",
                    "entries": [
                        {"age": "25", "value": 2.0},
                        {"age": "30.5", "value": 3.0},
                    ],
                }
            ]
        }
        lf = pl.DataFrame({"age": [25.0, 30.5]}).lazy()
        out = apply_rating_step_from_config(lf, config).collect()
        assert out["f"].to_list() == [2.0, 3.0]
        # Dtype revert is preserved (B1).
        assert out["age"].dtype == pl.Float64

    def test_float_entry_keys_match_int_frame_column(self) -> None:
        """Numeric entry keys (JSON floats) must match an integer column."""
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [{"age": 25.0, "value": 2.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"age": [25]}).lazy(), table).collect()
        assert out["f"].to_list() == [2.0]

    def test_float_entry_keys_match_float_frame_column(self) -> None:
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [{"age": 25.0, "value": 2.0}, {"age": 30.5, "value": 3.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"age": [25.0, 30.5]}).lazy(), table).collect()
        assert out["f"].to_list() == [2.0, 3.0]

    def test_int_column_still_matches_string_keys(self) -> None:
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [{"age": "25", "value": 2.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"age": [25]}).lazy(), table).collect()
        assert out["f"].to_list() == [2.0]

    def test_non_integer_floats_match_their_string_form(self) -> None:
        """HEAD already matched these; the fix must not regress them."""
        table = {
            "name": "Score",
            "factors": ["score"],
            "outputColumn": "f",
            "entries": [{"score": "1.5", "value": 10.0}, {"score": "0.1", "value": 20.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"score": [1.5, 0.1]}).lazy(), table).collect()
        assert out["f"].to_list() == [10.0, 20.0]

    def test_negative_zero_collapses_to_zero(self) -> None:
        table = {
            "name": "Z",
            "factors": ["z"],
            "outputColumn": "f",
            "entries": [{"z": "0", "value": 5.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"z": [-0.0]}).lazy(), table).collect()
        assert out["f"].to_list() == [5.0]

    def test_negative_int_like_float_matches(self) -> None:
        table = {
            "name": "T",
            "factors": ["t"],
            "outputColumn": "f",
            "entries": [{"t": "-3", "value": 7.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"t": [-3.0]}).lazy(), table).collect()
        assert out["f"].to_list() == [7.0]

    def test_large_int_like_float_within_int64_matches(self) -> None:
        """1e16 is integral and Int64-representable: collapses to digits."""
        table = {
            "name": "Big",
            "factors": ["b"],
            "outputColumn": "f",
            "entries": [{"b": "10000000000000000", "value": 1.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"b": [1e16]}).lazy(), table).collect()
        assert out["f"].to_list() == [1.0]

    def test_int_like_float_beyond_int64_keeps_float_form(self) -> None:
        """Beyond the Int64 range, keys keep the engine's float formatting."""
        table = {
            "name": "Huge",
            "factors": ["h"],
            "outputColumn": "f",
            "entries": [{"h": "1e+300", "value": 1.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"h": [1e300]}).lazy(), table).collect()
        assert out["f"].to_list() == [1.0]

    def test_string_keys_are_verbatim_never_collapsed(self) -> None:
        """A string key "25.0" is a label; the float 25.0 canonicalises to
        "25" and must NOT match it — and the miss is loud (3a.3)."""
        table = {
            "name": "Verbatim",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [{"age": "25.0", "value": 2.0}],
        }
        with pytest.raises(RatingTableMissError, match="25"):
            _apply_rating_table(pl.DataFrame({"age": [25.0]}).lazy(), table).collect()

    def test_float32_int_like_column_matches_string_keys(self) -> None:
        table = {
            "name": "F32",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [{"age": "25", "value": 2.0}],
        }
        lf = pl.DataFrame({"age": pl.Series("age", [25.0], dtype=pl.Float32)}).lazy()
        out = _apply_rating_table(lf, table).collect()
        assert out["f"].to_list() == [2.0]
        assert out["age"].dtype == pl.Float32

    def test_float32_plain_decimal_matches_string_keys(self) -> None:
        """Float32 0.1 renders as "0.1" (shortest repr) and matches."""
        table = {
            "name": "F32",
            "factors": ["score"],
            "outputColumn": "f",
            "entries": [{"score": "0.1", "value": 9.0}],
        }
        lf = pl.DataFrame({"score": pl.Series("score", [0.1], dtype=pl.Float32)}).lazy()
        out = _apply_rating_table(lf, table).collect()
        assert out["f"].to_list() == [9.0]

    def test_boolean_factor_column_fails_loudly(self) -> None:
        """Characterisation: Boolean frame columns cannot round-trip the
        Utf8 join (revert cast fails loudly at HEAD and still does)."""
        table = {
            "name": "B",
            "factors": ["b"],
            "outputColumn": "f",
            "entries": [{"b": "true", "value": 2.0}],
        }
        lf = pl.DataFrame({"b": [True]}).lazy()
        with pytest.raises(pl.exceptions.InvalidOperationError):
            _apply_rating_table(lf, table).collect()


# ---------------------------------------------------------------------------
# The shared Python mirror vs the engine-side expression
# ---------------------------------------------------------------------------

# One value per row: (value, expected canonical form). None means "stays null".
_CANONICAL_GRID: list[tuple[Any, str | None]] = [
    (None, None),
    (True, "true"),
    (False, "false"),
    (0, "0"),
    (25, "25"),
    (-3, "-3"),
    (2**63, "9223372036854775808"),  # UInt64 column territory
    (25.0, "25"),
    (-0.0, "0"),
    (-3.0, "-3"),
    (0.5, "0.5"),
    (25.5, "25.5"),
    (0.1, "0.1"),
    (0.0001, "0.0001"),
    (1e15, "1000000000000000"),
    (1e16, "10000000000000000"),
    (float(2**53), "9007199254740992"),
    (9223372036854774784.0, "9223372036854774784"),  # largest f64 below 2^63
    ("25", "25"),
    ("25.0", "25.0"),
    ("North", "North"),
    ("", ""),
]

# Values whose canonical form is delegated to the engine's formatter; the
# mirror must agree with polars exactly, whatever that formatting is.
_DELEGATED_FLOATS = [
    2.0**63,
    1.5e20,
    1e300,
    1e-5,
    1.5e-7,
    float("inf"),
    float("-inf"),
    float("nan"),
]


def _expr_canonical(value: Any) -> str | None:
    """Canonicalise one value through the engine-side expression."""
    series = pl.Series("k", [value])
    frame = pl.DataFrame({"k": series})
    return frame.select(_rating_key_expr("k", frame.schema["k"]))["k"][0]


class TestNormaliseRatingKeyMirrorsEngine:
    @pytest.mark.parametrize(("value", "expected"), _CANONICAL_GRID)
    def test_known_canonical_forms(self, value: Any, expected: str | None) -> None:
        assert normalise_rating_key(value) == expected
        assert _expr_canonical(value) == expected

    @pytest.mark.parametrize("value", _DELEGATED_FLOATS)
    def test_delegated_floats_agree_with_engine(self, value: float) -> None:
        mirror = normalise_rating_key(value)
        engine = _expr_canonical(value)
        assert mirror == engine
        if math.isnan(value):
            assert engine == "NaN"

    def test_int_like_collapse_boundary_is_int64_range(self) -> None:
        inside = 9223372036854774784.0  # largest float64 strictly below 2^63
        outside = 2.0**63
        assert normalise_rating_key(inside) == "9223372036854774784"
        assert normalise_rating_key(outside) != str(int(outside))


# ---------------------------------------------------------------------------
# Sidecar persistence canonicalises numeric keys
# ---------------------------------------------------------------------------


class TestSidecarKeyCanonicalisation:
    def test_compact_collapses_int_like_float_keys(self) -> None:
        config = {
            "tables": [
                {
                    "name": "Age",
                    "factors": ["age"],
                    "outputColumn": "f",
                    "entries": [{"age": 25.0, "value": 2.0}],
                }
            ]
        }
        compacted = compact_rating_step_config_for_sidecar(config)
        assert compacted["tables"][0]["entries"] == {"25": 2.0}

    def test_round_trip_still_matches_float_column(self) -> None:
        """Save/load must not break what the engine matched before saving."""
        config = {
            "tables": [
                {
                    "name": "Age",
                    "factors": ["age"],
                    "outputColumn": "f",
                    "entries": [{"age": 25.0, "value": 2.0}],
                }
            ]
        }
        rehydrated = expand_rating_step_config_from_sidecar(
            json.loads(json.dumps(compact_rating_step_config_for_sidecar(config)))
        )
        lf = pl.DataFrame({"age": [25.0]}).lazy()
        out = apply_rating_step_from_config(lf, rehydrated).collect()
        assert out["f"].to_list() == [2.0]


# ---------------------------------------------------------------------------
# Engine <-> enrichment agreement (the 3a.4 pairing)
# ---------------------------------------------------------------------------


def _engine_truth(frame: pl.DataFrame, table: dict[str, Any]) -> list[bool]:
    """Per-row engine ground truth: did the lookup join match a real entry?

    Runs the engine itself with the default stripped and misses allowed,
    so nulls mark true misses — independent of the enrichment code under
    test.
    """
    probe = {key: value for key, value in table.items() if key != "defaultValue"}
    probe["onMissing"] = "neutral"
    out = _apply_rating_table(frame.lazy(), probe).collect()
    return [value is not None for value in out[table["outputColumn"]].to_list()]


def assert_engine_and_enrichment_agree(
    frame: pl.DataFrame, table: dict[str, Any]
) -> list[dict[str, Any]]:
    """Shared fixture: the engine's matched/default outcome per row must be
    exactly what ``_enrich_single_table`` reports for that row's trace."""
    engine_matched = _engine_truth(frame, table)
    out = _apply_rating_table(frame.lazy(), table).collect()

    has_usable_default = False
    default_raw = table.get("defaultValue")
    if default_raw is not None and str(default_raw).strip():
        try:
            has_usable_default = math.isfinite(float(str(default_raw)))
        except (TypeError, ValueError):
            has_usable_default = False

    details: list[dict[str, Any]] = []
    for index in range(out.height):
        input_row = _jsonify_row(frame.row(index, named=True))
        output_row = _jsonify_row(out.row(index, named=True))
        detail = _enrich_single_table(table, input_row, output_row)
        details.append(detail)
        context = f"row {index}: input={input_row}, detail status={detail['status']!r}"
        if engine_matched[index]:
            assert detail["status"] == "matched", context
            assert detail["matched"] is True, context
            assert detail["matched_entry"] is not None, context
            assert detail["default_used"] is False, context
        elif has_usable_default:
            assert detail["status"] == "default", context
            assert detail["default_used"] is True, context
            assert detail["matched"] is False, context
        else:
            assert detail["status"] == "no_match", context
            assert detail["matched"] is False, context
            assert detail["default_used"] is False, context
    return details


class TestEnrichmentAgreesWithEngine:
    def test_int_like_floats_true_floats_and_miss(self) -> None:
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "onMissing": "neutral",
            "entries": [
                {"age": "25", "value": 2.0},
                {"age": "30.5", "value": 3.0},
            ],
        }
        frame = pl.DataFrame({"age": [25.0, 30.5, 99.0]})
        details = assert_engine_and_enrichment_agree(frame, table)
        assert [detail["status"] for detail in details] == [
            "matched",
            "matched",
            "no_match",
        ]

    def test_float_entry_keys_vs_int_frame_column(self) -> None:
        """The exact case where fixing the engine alone makes traces lie:
        entry key 25.0 vs frame value 25 — engine matches post-fix, and
        str()-based enrichment would report no_match."""
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "onMissing": "neutral",
            "entries": [{"age": 25.0, "value": 2.0}],
        }
        frame = pl.DataFrame({"age": [25, 26]})
        details = assert_engine_and_enrichment_agree(frame, table)
        assert details[0]["matched_entry"] == {"age": 25.0, "value": 2.0}
        assert details[1]["status"] == "no_match"

    def test_string_keys_regression_guard(self) -> None:
        table = {
            "name": "Region",
            "factors": ["region"],
            "outputColumn": "f",
            "onMissing": "neutral",
            "entries": [
                {"region": "North", "value": 1.2},
                {"region": "South", "value": 0.9},
            ],
        }
        frame = pl.DataFrame({"region": ["North", "South", "East"]})
        assert_engine_and_enrichment_agree(frame, table)

    def test_default_fill_reported_as_default(self) -> None:
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "defaultValue": "1.0",
            "entries": [{"age": "25", "value": 2.0}],
        }
        frame = pl.DataFrame({"age": [25.0, 99.0]})
        details = assert_engine_and_enrichment_agree(frame, table)
        assert [detail["status"] for detail in details] == ["matched", "default"]

    def test_entry_value_equal_to_default_is_matched_not_default(self) -> None:
        """Value collision: a real entry hit whose value equals defaultValue
        must be reported as matched — possible only if key comparison uses
        the engine's canonical form."""
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "defaultValue": "1.0",
            "entries": [{"age": 25.0, "value": 1.0}],
        }
        frame = pl.DataFrame({"age": [25.0]})
        details = assert_engine_and_enrichment_agree(frame, table)
        assert details[0]["status"] == "matched"

    def test_bool_entry_keys_vs_string_frame_column(self) -> None:
        """Engine: Boolean entries cast to "true"/"false". str(True) is
        "True" — the old enrichment disagreed with an engine match here."""
        table = {
            "name": "Flag",
            "factors": ["flag"],
            "outputColumn": "f",
            "onMissing": "neutral",
            "entries": [{"flag": True, "value": 2.0}, {"flag": False, "value": 3.0}],
        }
        frame = pl.DataFrame({"flag": ["true", "false", "TRUE"]})
        details = assert_engine_and_enrichment_agree(frame, table)
        assert [detail["status"] for detail in details] == [
            "matched",
            "matched",
            "no_match",
        ]

    def test_multi_factor_mixed_numeric_and_string(self) -> None:
        table = {
            "name": "Two-way",
            "factors": ["age", "region"],
            "outputColumn": "f",
            "onMissing": "neutral",
            "entries": [
                {"age": "25", "region": "North", "value": 1.5},
                {"age": "30.5", "region": "South", "value": 0.9},
            ],
        }
        frame = pl.DataFrame({"age": [25.0, 30.5, 25.0], "region": ["North", "South", "South"]})
        details = assert_engine_and_enrichment_agree(frame, table)
        assert [detail["status"] for detail in details] == [
            "matched",
            "matched",
            "no_match",
        ]

    def test_null_factor_value_never_matches(self) -> None:
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "onMissing": "neutral",
            "entries": [{"age": "25", "value": 2.0}],
        }
        frame = pl.DataFrame({"age": [25.0, None]})
        details = assert_engine_and_enrichment_agree(frame, table)
        assert [detail["status"] for detail in details] == ["matched", "no_match"]


# ---------------------------------------------------------------------------
# End-to-end: execute_trace over a float-keyed rating node
# ---------------------------------------------------------------------------


class TestTraceEndToEndAgreement:
    """Full real path: source -> ratingStep -> execute_trace -> node_detail.

    The unit fixture above covers per-row agreement; these pin that the
    same flags survive the whole trace pipeline (row correlation, value
    jsonification, ``enrich_rating_step`` dispatch)."""

    def _rating_graph(self, tmp_path: Any, on_missing: str | None):
        from haute.graph_utils import GraphNode, NodeData
        from tests.conftest import make_edge, make_graph, make_source_node

        data = tmp_path / "data.parquet"
        # Float64 ages: 25.0 must match the string key "25"; 99.0 misses.
        pl.DataFrame({"age": [25.0, 30.5, 99.0]}).write_parquet(data)
        table: dict[str, Any] = {
            "name": "Age Factor",
            "factors": ["age"],
            "outputColumn": "age_factor",
            "entries": [
                {"age": "25", "value": 2.0},
                {"age": "30.5", "value": 3.0},
            ],
        }
        if on_missing:
            table["onMissing"] = on_missing
        rating = GraphNode(
            id="rate",
            data=NodeData(label="rate", nodeType="ratingStep", config={"tables": [table]}),
        )
        return make_graph(
            {
                "nodes": [make_source_node("src", str(data)), rating],
                "edges": [make_edge("src", "rate")],
            }
        )

    def test_engine_matched_rows_trace_as_matched(self, tmp_path: Any) -> None:
        from haute.trace import execute_trace

        graph = self._rating_graph(tmp_path, on_missing="neutral")
        for row_index, expected_value in [(0, 2.0), (1, 3.0)]:
            result = execute_trace(
                graph, row_index=row_index, target_node_id="rate", column="age_factor"
            )
            assert result.output_value == expected_value
            step = next(s for s in result.steps if s.node_id == "rate")
            assert step.node_detail is not None
            table_detail = step.node_detail["tables"][0]
            assert table_detail["status"] == "matched"
            assert table_detail["matched"] is True
            assert table_detail["matched_entry"] is not None

    def test_engine_miss_traces_as_no_match(self, tmp_path: Any) -> None:
        from haute.trace import execute_trace

        graph = self._rating_graph(tmp_path, on_missing="neutral")
        result = execute_trace(graph, row_index=2, target_node_id="rate", column="age_factor")
        assert result.output_value is None
        step = next(s for s in result.steps if s.node_id == "rate")
        assert step.node_detail is not None
        table_detail = step.node_detail["tables"][0]
        assert table_detail["status"] == "no_match"
        assert table_detail["matched"] is False
