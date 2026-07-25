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
* The Python mirror requires the originating dtype and is exact for every
  supported factor dtype, including Float32 values widened at Python/JSON
  boundaries.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock

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

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

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

    def test_numeric_string_entry_is_coerced_through_float_dtype(self) -> None:
        """An entry scalar is interpreted in the originating factor dtype."""
        table = {
            "name": "Verbatim",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [{"age": "25.0", "value": 2.0}],
        }
        out = _apply_rating_table(pl.DataFrame({"age": [25.0]}).lazy(), table).collect()
        assert out["f"].to_list() == [2.0]

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

    def test_float32_promoted_decimal_entry_coerces_back_to_native_width(self) -> None:
        """A widened Python/JSON decimal is reconstructed through Float32
        before both lookup sides format their native-width key."""
        promoted = str(pl.Series("s", [0.1], dtype=pl.Float32).cast(pl.Float64)[0])
        assert promoted == "0.10000000149011612"
        table = {
            "name": "F32",
            "factors": ["score"],
            "outputColumn": "f",
            "entries": [{"score": promoted, "value": 9.0}],
        }
        lf = pl.DataFrame({"score": pl.Series("score", [0.1], dtype=pl.Float32)}).lazy()
        out = _apply_rating_table(lf, table).collect()
        assert out["f"].to_list() == [9.0]

    def test_float32_shortest_decimal_string_matches_natively(self) -> None:
        """Entry strings are cast to Float32 before native-width formatting."""
        table = {
            "name": "F32",
            "factors": ["score"],
            "outputColumn": "f",
            "entries": [{"score": "0.1", "value": 9.0}],
        }
        lf = pl.DataFrame({"score": pl.Series("score", [0.1], dtype=pl.Float32)}).lazy()
        out = _apply_rating_table(lf, table).collect()
        assert out["f"].to_list() == [9.0]

    def test_boolean_factor_column_matches_and_is_preserved(self) -> None:
        table = {
            "name": "B",
            "factors": ["b"],
            "outputColumn": "f",
            "entries": [{"b": "true", "value": 2.0}],
        }
        lf = pl.DataFrame({"b": [True]}).lazy()
        out = _apply_rating_table(lf, table).collect()
        assert out["f"].to_list() == [2.0]
        assert out["b"].dtype == pl.Boolean


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
        dtype = pl.Series("k", [value]).dtype
        assert normalise_rating_key(value, dtype) == expected
        assert _expr_canonical(value) == expected

    @pytest.mark.parametrize("value", _DELEGATED_FLOATS)
    def test_delegated_floats_agree_with_engine(self, value: float) -> None:
        mirror = normalise_rating_key(value, pl.Float64)
        engine = _expr_canonical(value)
        assert mirror == engine
        if math.isnan(value):
            assert engine == "NaN"

    def test_int_like_collapse_boundary_is_int64_range(self) -> None:
        inside = 9223372036854774784.0  # largest float64 strictly below 2^63
        outside = 2.0**63
        assert normalise_rating_key(inside, pl.Float64) == "9223372036854774784"
        assert normalise_rating_key(outside, pl.Float64) != str(int(outside))


def _expr_canonical_dtype(value: Any, dtype: pl.DataType) -> str | None:
    """Canonicalise one value through the engine twin for a specific dtype."""
    series = pl.Series("k", [value], dtype=dtype)
    frame = pl.DataFrame({"k": series})
    return frame.select(_rating_key_expr("k", frame.schema["k"]))["k"][0]


class TestFloat32AndCrossDtypeAgreement:
    """The flagship: the engine twin must agree with the Python mirror for
    EVERY supported factor dtype — including Float32, which the mirror only
    ever sees already promoted to Float64 across the trace/JSON boundary.  A
    save-dtype != apply-dtype drift must therefore MATCH or fail LOUD, never
    silently neutral/default-miss.
    """

    _DTYPES = [pl.Float32, pl.Float64, pl.Int32, pl.Int64]

    @pytest.mark.parametrize("value", [0.1, 0.123456789, 25.7, 1.5, 25.0, -0.0, -3.0])
    def test_float32_twin_equals_mirror_of_promoted_value(self, value: float) -> None:
        s32 = pl.Series("k", [value], dtype=pl.Float32)
        engine = _expr_canonical_dtype(value, pl.Float32)
        # The mirror sees the value already promoted to Float64 (f32 has no
        # distinct Python scalar) — this is what a trace/JSON row carries.
        mirror = normalise_rating_key(s32.item(), pl.Float32)
        assert engine == mirror

    @pytest.mark.parametrize("dtype", _DTYPES)
    @pytest.mark.parametrize("value", [0, 1, 25, -3, 100, 2**20])
    def test_int_like_twin_equals_mirror_for_every_dtype(
        self, dtype: pl.DataType, value: int
    ) -> None:
        series = pl.Series("k", [value], dtype=dtype)
        engine = _expr_canonical_dtype(value, dtype)
        mirror = normalise_rating_key(series.item(), dtype)
        assert engine == mirror

    @pytest.mark.parametrize("save_dtype", _DTYPES)
    @pytest.mark.parametrize("apply_dtype", _DTYPES)
    def test_cross_dtype_int_like_is_match_or_loud(
        self, save_dtype: pl.DataType, apply_dtype: pl.DataType
    ) -> None:
        """25 is representable in all four dtypes, so the canonical key agrees
        and the lookup matches regardless of save/apply dtype drift."""
        save_series = pl.Series("age", [25], dtype=save_dtype)
        entry_key = normalise_rating_key(save_series.item(), save_dtype)
        table = {
            "name": "X",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [{"age": entry_key, "value": 2.0}],
        }
        lf = pl.DataFrame({"age": pl.Series("age", [25], dtype=apply_dtype)}).lazy()
        try:
            out = _apply_rating_table(lf, table).collect()
        except RatingTableMissError:
            return  # a loud mismatch is acceptable; a silent neutral is not
        assert out["f"].to_list() == [2.0]

    @pytest.mark.parametrize("save_dtype", [pl.Float32, pl.Float64])
    @pytest.mark.parametrize("apply_dtype", [pl.Float32, pl.Float64])
    def test_cross_dtype_non_dyadic_is_match_or_loud(
        self, save_dtype: pl.DataType, apply_dtype: pl.DataType
    ) -> None:
        """A non-dyadic decimal whose f32 and f64 bit patterns differ: the
        lookup either matches (keys agree) or raises RatingTableMissError —
        it must never silently return a neutral/None value."""
        value = 0.123456789
        entry_key = normalise_rating_key(
            pl.Series("s", [value], dtype=save_dtype).item(),
            save_dtype,
        )
        table = {
            "name": "X",
            "factors": ["s"],
            "outputColumn": "f",
            "entries": [{"s": entry_key, "value": 2.0}],
        }
        lf = pl.DataFrame({"s": pl.Series("s", [value], dtype=apply_dtype)}).lazy()
        try:
            out = _apply_rating_table(lf, table).collect()
        except RatingTableMissError:
            return
        assert out["f"].to_list() == [2.0]


class TestDedupOnCanonicalKeys:
    """B14/F084: deduplicate final typed keys and keep the last authored row."""

    def test_mixed_float_string_entries_coerce_to_one_key_keep_last(self) -> None:
        """Mixed source scalars still deduplicate on the final Float64 key."""
        entries = [
            {"age": 25.0, "value": 1.0},
            {"age": "25", "value": 2.0},
        ]
        # Polars first constructs one lookup column; target-dtype coercion and
        # canonical-key deduplication then preserve the same last-wins result.
        coerced = pl.DataFrame(entries)
        assert coerced.schema["age"] == pl.String
        assert coerced["age"].to_list() == ["25", "25"]
        table = {"name": "Age", "factors": ["age"], "outputColumn": "f", "entries": entries}
        out = _apply_rating_table(pl.DataFrame({"age": [25.0]}).lazy(), table).collect()
        assert out.height == 1  # no fan-out
        assert out["f"].to_list() == [2.0]  # last-authored entry wins

    def test_target_dtype_aliases_dedup_after_coercion_keep_last(self) -> None:
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [
                {"age": "25.0", "value": 1.0},
                {"age": "25.00", "value": 2.0},
            ],
        }

        out = _apply_rating_table(
            pl.DataFrame({"age": pl.Series("age", [25.0], dtype=pl.Float64)}).lazy(),
            table,
        ).collect()

        assert out.height == 1
        assert out["f"].to_list() == [2.0]

    def test_exact_duplicate_keys_dedup_keep_last(self) -> None:
        """The reachable dedup case: two entries with the identical key "25"
        form a duplicate group.  unique(keep="last") keeps the last-authored
        entry — this pins keep="last"; keep="first" would return 1.0."""
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "entries": [
                {"age": "25", "value": 1.0},
                {"age": "25", "value": 2.0},
            ],
        }
        out = _apply_rating_table(pl.DataFrame({"age": [25.0]}).lazy(), table).collect()
        assert out.height == 1
        assert out["f"].to_list() == [2.0]

    def test_canonical_collision_agrees_with_trace_enrichment(self) -> None:
        table = {
            "name": "Age",
            "factors": ["age"],
            "outputColumn": "f",
            "onMissing": "neutral",
            "entries": [
                {"age": 25.0, "value": 1.0},
                {"age": "25", "value": 2.0},
            ],
        }
        frame = pl.DataFrame({"age": [25.0]})
        details = assert_engine_and_enrichment_agree(frame, table)
        assert details[0]["status"] == "matched"


class TestDecimalFactorScale:
    """Decimal entries are coerced through the factor's declared scale."""

    def test_decimal_matches_entry_authored_at_declared_scale(self) -> None:
        from decimal import Decimal

        table = {
            "name": "Money",
            "factors": ["amount"],
            "outputColumn": "f",
            "entries": [{"amount": "25.50", "value": 3.0}],
        }
        series = pl.Series("amount", [Decimal("25.50")], dtype=pl.Decimal(scale=2))
        out = _apply_rating_table(pl.DataFrame({"amount": series}).lazy(), table).collect()
        assert out["f"].to_list() == [3.0]

    def test_decimal_entry_is_coerced_to_declared_scale(self) -> None:
        from decimal import Decimal

        table = {
            "name": "Money",
            "factors": ["amount"],
            "outputColumn": "f",
            "entries": [{"amount": "25.5", "value": 3.0}],
        }
        series = pl.Series("amount", [Decimal("25.50")], dtype=pl.Decimal(scale=2))
        out = _apply_rating_table(pl.DataFrame({"amount": series}).lazy(), table).collect()
        assert out["f"].to_list() == [3.0]


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
        assert compacted["tables"][0]["entries"] == [{"age": 25.0, "value": 2.0}]

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

    def test_legacy_compact_float_key_migrates_on_load(self) -> None:
        """Older sidecars wrote compact float keys as "25.0"; load migrates
        them to the canonical lookup key without changing row-array strings."""
        config = {
            "tables": [
                {
                    "name": "Age",
                    "factors": ["age"],
                    "outputColumn": "f",
                    "entries": {"25.0": 2.0},
                }
            ]
        }

        rehydrated = expand_rating_step_config_from_sidecar(config)
        assert rehydrated["tables"][0]["entries"] == [{"age": "25", "value": 2.0}]

        out = apply_rating_step_from_config(
            pl.DataFrame({"age": [25.0]}).lazy(),
            rehydrated,
        ).collect()
        assert out["f"].to_list() == [2.0]

    def test_legacy_compact_float_key_beats_default_value(self) -> None:
        config = {
            "tables": [
                {
                    "name": "Age",
                    "factors": ["age"],
                    "outputColumn": "f",
                    "defaultValue": 9.0,
                    "entries": {"25.0": 2.0},
                }
            ]
        }

        out = apply_rating_step_from_config(
            pl.DataFrame({"age": [25.0, 99.0]}).lazy(),
            config,
        ).collect()
        assert out["f"].to_list() == [2.0, 9.0]

    def test_legacy_compact_key_collision_fails_loudly(self) -> None:
        config = {
            "tables": [
                {
                    "name": "Age",
                    "factors": ["age"],
                    "outputColumn": "f",
                    "entries": {"25": 1.0, "25.0": 2.0},
                }
            ]
        }

        with pytest.raises(
            ValueError,
            match=r"ratingStep tables\[0\]\.entries.*age.*25\.0.*25",
        ):
            expand_rating_step_config_from_sidecar(config)

    def test_nested_legacy_compact_float_key_migrates_on_load(self) -> None:
        config = {
            "tables": [
                {
                    "name": "Age x Region",
                    "factors": ["age", "region"],
                    "outputColumn": "f",
                    "entries": {"25.0": {"North": 2.0}},
                }
            ]
        }

        rehydrated = expand_rating_step_config_from_sidecar(config)
        assert rehydrated["tables"][0]["entries"] == [
            {"age": "25", "region": "North", "value": 2.0}
        ]

        out = apply_rating_step_from_config(
            pl.DataFrame({"age": [25.0], "region": ["North"]}).lazy(),
            rehydrated,
        ).collect()
        assert out["f"].to_list() == [2.0]

    def test_nested_legacy_compact_key_collision_fails_loudly(self) -> None:
        config = {
            "tables": [
                {
                    "name": "Age x Region",
                    "factors": ["age", "region"],
                    "outputColumn": "f",
                    "entries": {"25": {"North": 1.0}, "25.0": {"North": 2.0}},
                }
            ]
        }

        with pytest.raises(
            ValueError,
            match=r"ratingStep tables\[0\]\.entries.*age.*25\.0.*25",
        ):
            expand_rating_step_config_from_sidecar(config)


class TestTemporalFactorColumns:
    @pytest.mark.parametrize(
        ("series", "entry_value"),
        [
            (
                pl.Series("inception", [date(2026, 1, 1)], dtype=pl.Date),
                "2026-01-01",
            ),
            (
                pl.Series(
                    "inception",
                    [datetime(2026, 1, 1, 12, 30)],
                    dtype=pl.Datetime,
                ),
                "2026-01-01 12:30:00",
            ),
        ],
    )
    def test_date_and_datetime_factor_columns_match(
        self, series: pl.Series, entry_value: str
    ) -> None:
        table = {
            "name": "Inception Rating",
            "factors": ["inception"],
            "outputColumn": "f",
            "entries": [{"inception": entry_value, "value": 2.0}],
        }
        lf = pl.DataFrame({"inception": series}).lazy()

        result = _apply_rating_table(lf, table).collect()
        assert result["f"].to_list() == [2.0]
        assert result["inception"].dtype == series.dtype


class TestRatebookSchemaCollection:
    def test_apply_ratebook_collects_schema_once_for_multiple_factor_tables(self) -> None:
        from haute._builders import _apply_ratebook

        lf = pl.DataFrame(
            {
                "age": [25.0],
                "region": ["North"],
                "channel": ["online"],
            }
        ).lazy()
        collect_schema = MagicMock(side_effect=lf.collect_schema)

        class _SchemaCountingLF:
            def __init__(self, inner: pl.LazyFrame) -> None:
                self._inner = inner

            def _wrap(self, value: Any) -> Any:
                if isinstance(value, pl.LazyFrame):
                    return _SchemaCountingLF(value)
                return value

            def collect_schema(self) -> pl.Schema:
                return collect_schema()

            def with_columns(self, *args: Any, **kwargs: Any) -> Any:
                return self._wrap(self._inner.with_columns(*args, **kwargs))

            def join(self, *args: Any, **kwargs: Any) -> Any:
                return self._wrap(self._inner.join(*args, **kwargs))

            def drop(self, *args: Any, **kwargs: Any) -> Any:
                return self._wrap(self._inner.drop(*args, **kwargs))

            def rename(self, *args: Any, **kwargs: Any) -> Any:
                return self._wrap(self._inner.rename(*args, **kwargs))

            def collect(self, *args: Any, **kwargs: Any) -> pl.DataFrame:
                return self._inner.collect(*args, **kwargs)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

        artifact = {
            "factor_tables": {
                "age": [{"__factor_group__": "25", "optimal_scenario_value": 1.1}],
                "region": [{"__factor_group__": "North", "optimal_scenario_value": 1.2}],
                "channel": [{"__factor_group__": "online", "optimal_scenario_value": 1.3}],
            },
            "factor_dtypes": {
                "age": [{"column": "age", "dtype": {"kind": "Float64"}}],
                "region": [{"column": "region", "dtype": {"kind": "String"}}],
                "channel": [{"column": "channel", "dtype": {"kind": "String"}}],
            },
        }

        out = _apply_ratebook(
            _SchemaCountingLF(lf),  # type: ignore[arg-type]
            artifact,
            "v1",
            "__ver__",
            "optimised",
        ).collect()

        assert collect_schema.call_count == 1
        assert out["optimised"].to_list() == pytest.approx([1.1 * 1.2 * 1.3])

    def test_rating_step_collects_schema_once_for_multiple_tables(self) -> None:
        """F716: _apply_rating_step_outputs resolves the frame schema once and
        threads it, instead of re-running collect_schema() per table on a
        growing lazy plan (O(N^2))."""
        from haute._rating import _apply_rating_step_outputs

        lf = pl.DataFrame({"age": [25.0], "region": ["North"], "channel": ["online"]}).lazy()
        collect_schema = MagicMock(side_effect=lf.collect_schema)

        class _SchemaCountingLF:
            def __init__(self, inner: pl.LazyFrame) -> None:
                self._inner = inner

            def _wrap(self, value: Any) -> Any:
                if isinstance(value, pl.LazyFrame):
                    return _SchemaCountingLF(value)
                return value

            def collect_schema(self) -> pl.Schema:
                return collect_schema()

            def with_columns(self, *args: Any, **kwargs: Any) -> Any:
                return self._wrap(self._inner.with_columns(*args, **kwargs))

            def join(self, *args: Any, **kwargs: Any) -> Any:
                return self._wrap(self._inner.join(*args, **kwargs))

            def drop(self, *args: Any, **kwargs: Any) -> Any:
                return self._wrap(self._inner.drop(*args, **kwargs))

            def rename(self, *args: Any, **kwargs: Any) -> Any:
                return self._wrap(self._inner.rename(*args, **kwargs))

            def collect(self, *args: Any, **kwargs: Any) -> pl.DataFrame:
                return self._inner.collect(*args, **kwargs)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

        tables = [
            {
                "name": "age",
                "factors": ["age"],
                "outputColumn": "af",
                "entries": [{"age": "25", "value": 1.1}],
            },
            {
                "name": "region",
                "factors": ["region"],
                "outputColumn": "rf",
                "entries": [{"region": "North", "value": 1.2}],
            },
            {
                "name": "channel",
                "factors": ["channel"],
                "outputColumn": "cf",
                "entries": [{"channel": "online", "value": 1.3}],
            },
        ]

        out = _apply_rating_step_outputs(
            _SchemaCountingLF(lf),  # type: ignore[arg-type]
            tables,
            [],
        ).collect()

        assert collect_schema.call_count == 1
        assert out["af"].to_list() == [1.1]
        assert out["rf"].to_list() == [1.2]
        assert out["cf"].to_list() == [1.3]


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
        detail = _enrich_single_table(
            table,
            input_row,
            output_row,
            factor_input_dtypes={factor: frame.schema[factor] for factor in table["factors"]},
        )
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
