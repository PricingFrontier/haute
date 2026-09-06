"""Property-based tests for optimiser level tie-breaking (scenario W09-S03).

Invariant: "Solver level tie-breaking is stable under floating-point perturbation
of tied levels: the documented tie order holds for generated level sets, not
only the curated fixtures."

Tie rules proved:
(a) Save time: every spelling of one typed factor value canonicalises to one
    saved __factor_group__ key, distinct typed values never share a key, and two
    emitted levels collapsing to one key fail loudly with ValueError raised by
    _serialise_ratebook_factor_table_rows (never last-writer-wins at save).
(b) Apply time: a duplicate saved key resolves to the LAST entry in both the
    Polars engine (_apply_ratebook, unique keep="last") and the explainability
    mirror (_match_ratebook_entry walks reversed).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
import pytest
import structlog.testing
from hypothesis import assume, example, find, given
from hypothesis import strategies as st

from haute._builders import _apply_ratebook
from haute._optimiser_apply_explainability import _match_ratebook_entry
from haute._rating import normalise_rating_key
from haute.routes._optimiser_service import (
    _ratebook_factor_dtypes,
    _ratebook_factor_level_counts,
    _serialise_ratebook_factor_tables,
)
from tests._property_budget import pr_budget
from tests.test_optimiser_ratebook_apply_agreement import (
    MISS_EVENT,
    SEP,
    _artifact,
    _engine_matches,
    _mirror_matches,
)

STRING_ALPHABET = "abcxyz_0123456789-"
BINARY32_IRRATIONALS = [0.1, 0.2, 0.3, 0.7, 1.1]
NON_INT_OFFSETS = [0.5, 0.25, 0.75, 0.125]


# ---------------------------------------------------------------------------
# Generators & Independent Oracle
# ---------------------------------------------------------------------------


def independent_oracle_key(val: Any, dtype: pl.DataType) -> str:
    """Derive expected canonical factor group key independently from production code."""
    if dtype == pl.Int64:
        return str(int(val))
    if dtype == pl.Float64:
        fval = float(val)
        if fval.is_integer():
            return str(int(fval))
        return repr(fval)
    if dtype == pl.Float32:
        fval = float(val)
        if fval.is_integer():
            return str(int(fval))
        return str(np.float32(fval))
    if dtype == pl.String:
        return str(val)
    raise ValueError(f"Unsupported dtype: {dtype}")


def spellings_for_value(val: Any, dtype: pl.DataType) -> list[Any]:
    """All perturbed spellings of one typed factor value."""
    if dtype == pl.Int64:
        return [str(int(val))]
    if dtype == pl.Float64:
        fval = float(val)
        # raw float plus string representations
        return [str(fval), repr(fval), f"{fval:.3f}", f"{fval:e}", fval]
    if dtype == pl.Float32:
        f32 = np.float32(val)
        w = float(f32)
        return [str(w), str(f32), w]
    if dtype == pl.String:
        return [str(val)]
    raise ValueError(f"Unsupported dtype: {dtype}")


def distinct_spellings_for_value(val: Any, dtype: pl.DataType) -> list[Any]:
    """Spellings that are distinct Python dictionary keys."""
    raw = spellings_for_value(val, dtype)
    seen: list[Any] = []
    for sp in raw:
        # distinct keys in a dict
        if not any(type(sp) is type(prev) and sp == prev for prev in seen):
            seen.append(sp)
    return seen


# Strategies for typed values
st_int64 = st.integers(min_value=-50, max_value=50)

st_float64_int_like = st.integers(min_value=-50, max_value=50).map(lambda k: float(k))
st_float64_non_int = st.tuples(
    st.integers(min_value=-50, max_value=50),
    st.sampled_from(NON_INT_OFFSETS),
).map(lambda pair: float(pair[0] + pair[1]))
st_float64 = st.one_of(st_float64_int_like, st_float64_non_int)

st_float32_binary32 = st.sampled_from(BINARY32_IRRATIONALS).map(lambda x: float(np.float32(x)))
st_float32 = st.one_of(st_float64, st_float32_binary32)

st_string = st.one_of(
    st.text(alphabet=STRING_ALPHABET, min_size=1, max_size=6),
    st.sampled_from(["25", "25.0", "30.5", "0.1", "-10", "40.0"]),
)


def typed_value_strategy(dtype: pl.DataType) -> st.SearchStrategy[Any]:
    if dtype == pl.Int64:
        return st_int64
    if dtype == pl.Float64:
        return st_float64
    if dtype == pl.Float32:
        return st_float32
    if dtype == pl.String:
        return st_string
    raise ValueError(f"Unsupported dtype: {dtype}")


st_dtype = st.sampled_from([pl.Int64, pl.Float64, pl.Float32, pl.String])

st_typed_case = st.one_of(
    st.tuples(st.just(pl.Int64), st_int64),
    st.tuples(st.just(pl.Float64), st_float64),
    st.tuples(st.just(pl.Float32), st_float32),
    st.tuples(st.just(pl.String), st_string),
)


# ---------------------------------------------------------------------------
# Property 1: Save-time single-key canonicalisation
# ---------------------------------------------------------------------------


@pr_budget(60)
@example(case=(pl.Float64, 25.0))
@example(case=(pl.Float64, 30.5))
@given(case=st_typed_case)
def test_every_spelling_of_one_typed_value_saves_to_one_key(
    case: tuple[pl.DataType, Any],
) -> None:
    """Rule (a): every spelling of one typed factor value canonicalises to one
    saved __factor_group__ key, and matches the independent oracle."""
    dtype, val = case
    spellings = spellings_for_value(val, dtype)
    expected_oracle = independent_oracle_key(val, dtype)

    df = pl.DataFrame({"factor": [val, val]}, schema={"factor": dtype})
    counts = _ratebook_factor_level_counts(df, [["factor"]])
    dtypes = _ratebook_factor_dtypes(df, [["factor"]])

    saved_keys: list[str] = []
    for sp in spellings:
        table = {"factor": {sp: 1.23}}
        serialised = _serialise_ratebook_factor_tables(table, counts, {}, dtypes)
        key = serialised["factor"][0]["__factor_group__"]
        saved_keys.append(key)
        assert key == expected_oracle, (
            f"Spelling {sp!r} of value {val!r} ({dtype}) saved as {key!r}, "
            f"expected oracle key {expected_oracle!r}"
        )

    # All spellings of the same typed value must canonicalise identically
    assert len(set(saved_keys)) == 1, (
        f"Spellings {spellings!r} yielded divergent keys: {saved_keys!r}"
    )


# ---------------------------------------------------------------------------
# Property 2: Distinct typed values never share a saved key
# ---------------------------------------------------------------------------


@pr_budget(60)
@given(
    dtype=st_dtype,
    data=st.data(),
)
def test_distinct_typed_values_never_share_a_key(
    dtype: pl.DataType,
    data: st.DataObject,
) -> None:
    """Rule (a): distinct typed values never share a key, and quote_count
    per row equals the generator's row count for that value."""
    # Draw 1..5 distinct values
    values = data.draw(
        st.lists(typed_value_strategy(dtype), min_size=1, max_size=5, unique=True),
        label="values",
    )
    # Give each distinct value a repeat count >= 1 (some > 1 so quote_count > 1)
    counts_per_val = [data.draw(st.integers(min_value=1, max_value=4)) for _ in values]

    expanded_values: list[Any] = []
    for val, cnt in zip(values, counts_per_val):
        expanded_values.extend([val] * cnt)

    df = pl.DataFrame({"factor": expanded_values}, schema={"factor": dtype})
    counts = _ratebook_factor_level_counts(df, [["factor"]])
    dtypes = _ratebook_factor_dtypes(df, [["factor"]])

    # Choose one spelling per distinct value
    chosen_spellings: list[Any] = []
    for val in values:
        all_sp = spellings_for_value(val, dtype)
        sp = data.draw(st.sampled_from(all_sp))
        chosen_spellings.append(sp)

    table = {"factor": {sp: 1.0 + i / 10.0 for i, sp in enumerate(chosen_spellings)}}
    serialised = _serialise_ratebook_factor_tables(table, counts, {}, dtypes)
    rows = serialised["factor"]

    # Saved keys must be pairwise distinct
    saved_keys = [row["__factor_group__"] for row in rows]
    assert len(set(saved_keys)) == len(values), (
        f"Key collision detected among distinct values {values!r}: {saved_keys!r}"
    )

    # quote_count per row equals generator row count for that value
    oracle_to_count = {
        independent_oracle_key(val, dtype): cnt for val, cnt in zip(values, counts_per_val)
    }
    for row in rows:
        key = row["__factor_group__"]
        assert row["quote_count"] == oracle_to_count[key], (
            f"Row key {key!r} quote_count {row['quote_count']} != expected {oracle_to_count[key]}"
        )


# ---------------------------------------------------------------------------
# Property 3: Two spellings of one value fail loudly
# ---------------------------------------------------------------------------


@pr_budget(60)
@given(
    dtype=st.sampled_from([pl.Float64, pl.Float32]),
    data=st.data(),
)
def test_two_spellings_of_one_value_in_one_table_fail_loudly(
    dtype: pl.DataType,
    data: st.DataObject,
) -> None:
    """Rule (a): two emitted levels collapsing to one key fail loudly with
    ValueError raised by _serialise_ratebook_factor_table_rows naming the table."""
    val = data.draw(typed_value_strategy(dtype), label="val")
    distinct_sp = distinct_spellings_for_value(val, dtype)
    assume(len(distinct_sp) >= 2)

    pair = data.draw(st.lists(st.sampled_from(distinct_sp), min_size=2, max_size=2, unique=True))
    sp1, sp2 = pair[0], pair[1]

    table_name = "test_factor"
    df = pl.DataFrame({table_name: [val, val]}, schema={table_name: dtype})
    counts = _ratebook_factor_level_counts(df, [[table_name]])
    dtypes = _ratebook_factor_dtypes(df, [[table_name]])

    table = {table_name: {sp1: 1.1, sp2: 1.2}}
    with pytest.raises(ValueError, match="canonicalise") as exc_info:
        _serialise_ratebook_factor_tables(table, counts, {}, dtypes)

    assert table_name in str(exc_info.value), (
        f"Error message did not name table {table_name!r}: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# Property 4: Saved artifact rates every typed row with its own level
# ---------------------------------------------------------------------------


@pr_budget(60)
@given(
    is_composite=st.booleans(),
    dtype_a=st_dtype,
    dtype_b=st_dtype,
    data=st.data(),
)
def test_saved_artifact_rates_every_typed_row_with_its_own_level(
    is_composite: bool,
    dtype_a: pl.DataType,
    dtype_b: pl.DataType,
    data: st.DataObject,
) -> None:
    """Rule (a)+(b): build saved artifact from serialised tables plus descriptor
    dtypes, apply with _apply_ratebook to typed frame: every row rates with its
    own level, no MISS_EVENT logged, and engine and mirror agree."""
    if not is_composite:
        # Single column table
        vals = data.draw(
            st.lists(typed_value_strategy(dtype_a), min_size=1, max_size=5, unique=True)
        )
        spellings = [data.draw(st.sampled_from(spellings_for_value(v, dtype_a))) for v in vals]
        rates = [1.0 + idx / 100.0 for idx in range(len(vals))]

        table_name = "f1"
        df = pl.DataFrame({table_name: vals}, schema={table_name: dtype_a})
        counts = _ratebook_factor_level_counts(df, [[table_name]])
        dtypes = _ratebook_factor_dtypes(df, [[table_name]])

        table = {table_name: dict(zip(spellings, rates))}
        serialised = _serialise_ratebook_factor_tables(table, counts, {}, dtypes)
        artifact = _artifact(serialised, dtypes)
        apply_frame = pl.DataFrame({table_name: vals}, schema={table_name: dtype_a})

        with structlog.testing.capture_logs() as logs:
            out = _apply_ratebook(apply_frame.lazy(), artifact, "v1", "__ver__").collect()

        assert out[f"{table_name}_optimised_factor"].to_list() == pytest.approx(rates)
        assert [log for log in logs if log["event"] == MISS_EVENT] == []
        assert _engine_matches(apply_frame, table_name, serialised[table_name]) == [True] * len(
            vals
        )
        assert _mirror_matches(apply_frame, table_name, serialised[table_name]) == [True] * len(
            vals
        )
    else:
        # Composite table
        vals_a = data.draw(
            st.lists(typed_value_strategy(dtype_a), min_size=1, max_size=3, unique=True)
        )
        vals_b = data.draw(
            st.lists(typed_value_strategy(dtype_b), min_size=1, max_size=3, unique=True)
        )
        pairs: list[tuple[Any, Any]] = []
        for va in vals_a:
            for vb in vals_b:
                pairs.append((va, vb))
        pairs = pairs[:4]  # limit to 4 rows

        spellings_comp: list[str] = []
        for va, vb in pairs:
            sp_a = data.draw(st.sampled_from(spellings_for_value(va, dtype_a)))
            sp_b = data.draw(st.sampled_from(spellings_for_value(vb, dtype_b)))
            spellings_comp.append(f"{str(sp_a)}{SEP}{str(sp_b)}")

        rates = [1.0 + idx / 100.0 for idx in range(len(pairs))]
        table_name = "col_a:col_b"
        df = pl.DataFrame(
            {
                "col_a": [p[0] for p in pairs],
                "col_b": [p[1] for p in pairs],
            },
            schema={"col_a": dtype_a, "col_b": dtype_b},
        )
        counts = _ratebook_factor_level_counts(df, [["col_a", "col_b"]])
        dtypes = _ratebook_factor_dtypes(df, [["col_a", "col_b"]])

        table = {table_name: dict(zip(spellings_comp, rates))}
        serialised = _serialise_ratebook_factor_tables(table, counts, {}, dtypes)
        artifact = _artifact(serialised, dtypes)
        apply_frame = pl.DataFrame(
            {
                "col_a": [p[0] for p in pairs],
                "col_b": [p[1] for p in pairs],
            },
            schema={"col_a": dtype_a, "col_b": dtype_b},
        )

        with structlog.testing.capture_logs() as logs:
            out = _apply_ratebook(apply_frame.lazy(), artifact, "v1", "__ver__").collect()

        assert out[f"{table_name}_optimised_factor"].to_list() == pytest.approx(rates)
        assert [log for log in logs if log["event"] == MISS_EVENT] == []
        assert _engine_matches(apply_frame, table_name, serialised[table_name]) == [True] * len(
            pairs
        )
        assert _mirror_matches(apply_frame, table_name, serialised[table_name]) == [True] * len(
            pairs
        )


# ---------------------------------------------------------------------------
# Property 5: Duplicate saved key resolves to last entry in engine and mirror
# ---------------------------------------------------------------------------


@pr_budget(60)
@given(
    rates=st.lists(
        st.floats(min_value=0.5, max_value=2.5, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=3,
        unique=True,
    )
)
def test_duplicate_saved_key_resolves_to_the_last_entry_in_engine_and_mirror(
    rates: list[float],
) -> None:
    """Rule (b): duplicate saved key resolves to the LAST entry in both the
    Polars engine (_apply_ratebook, unique keep="last") and the explainability
    mirror (_match_ratebook_entry walks reversed)."""
    table_name = "channel:age_band"
    entries = [
        {"__factor_group__": f"online{SEP}18-25", "optimal_scenario_value": r, "quote_count": 1}
        for r in rates
    ]
    artifact = _artifact(
        {table_name: entries},
        {
            table_name: [
                {"column": "channel", "dtype": {"kind": "String"}},
                {"column": "age_band", "dtype": {"kind": "String"}},
            ]
        },
    )
    frame = pl.DataFrame({"channel": ["online"], "age_band": ["18-25"]})
    out = _apply_ratebook(frame.lazy(), artifact, "v1", "__ver__").collect()
    engine_value = out[f"{table_name}_optimised_factor"][0]

    matched = _match_ratebook_entry(
        entries,
        ["channel", "age_band"],
        ["online", "18-25"],
        table_name,
        input_dtypes=[pl.String, pl.String],
    )

    expected_last = rates[-1]
    assert matched is not None
    assert engine_value == pytest.approx(expected_last)
    assert matched["optimal_scenario_value"] == pytest.approx(expected_last)


# ---------------------------------------------------------------------------
# Negative Controls
# ---------------------------------------------------------------------------


def _string_spellings(val: Any, dtype: pl.DataType) -> list[str]:
    """The distinct string spellings price-contour could emit for one typed value."""
    seen: list[str] = []
    for sp in spellings_for_value(val, dtype):
        if isinstance(sp, str) and sp not in seen:
            seen.append(sp)
    return seen


st_float_case = st.one_of(
    st.tuples(st.just(pl.Float64), st_float64),
    st.tuples(st.just(pl.Float32), st_float32),
)


def _verbatim_table_drops_a_rate_silently(case: tuple[pl.DataType, Any]) -> bool:
    """The 3b.10 fault: two spellings of one value saved verbatim reach the apply
    path as two entries, and its unique(keep="last") dedup keeps the last rate
    without any error (a solved rate silently disappears)."""
    dtype, val = case
    spellings = _string_spellings(val, dtype)
    if len(spellings) < 2:
        return False
    kind = "Float32" if dtype == pl.Float32 else "Float64"
    entries = [
        {"__factor_group__": spellings[0], "optimal_scenario_value": 1.1, "quote_count": 1},
        {"__factor_group__": spellings[1], "optimal_scenario_value": 1.9, "quote_count": 1},
    ]
    artifact = _artifact({"age": entries}, {"age": [{"column": "age", "dtype": {"kind": kind}}]})
    frame = pl.DataFrame({"age": [val]}, schema={"age": dtype})
    out = _apply_ratebook(frame.lazy(), artifact, "v1", "__ver__").collect()
    return out["age_optimised_factor"][0] == pytest.approx(1.9)


def test_verbatim_labels_would_silently_drop_a_solved_rate() -> None:
    """Negative control: what the loud save-time collision check detects.

    Without canonicalisation at save (the historical 3b.10 fault) two emitted
    spellings of one typed value are stored verbatim as two entries; the apply
    path coerces both through the factor dtype, deduplicates keep="last" and
    silently rates the row with the second entry. The canonicalising save path
    refuses the very same emitted table with the ValueError property 3 proves.
    """
    found = find(st_float_case, _verbatim_table_drops_a_rate_silently, settings=pr_budget(60))
    assert _verbatim_table_drops_a_rate_silently(found), found

    dtype, val = found
    spellings = _string_spellings(val, dtype)
    df = pl.DataFrame({"age": [val, val]}, schema={"age": dtype})
    counts = _ratebook_factor_level_counts(df, [["age"]])
    dtypes = _ratebook_factor_dtypes(df, [["age"]])
    with pytest.raises(ValueError, match="canonicalise"):
        _serialise_ratebook_factor_tables(
            {"age": {spellings[0]: 1.1, spellings[1]: 1.9}}, counts, {}, dtypes
        )


def test_first_entry_wins_mirror_would_disagree_with_engine() -> None:
    """Negative control: a test-local mirror that walks entries forward (first wins)
    is compared with the engine on duplicate-key entries; find an example where
    they disagree and assert the disagreement."""
    strat = st.tuples(
        st.floats(min_value=1.0, max_value=2.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=2.1, max_value=3.0, allow_nan=False, allow_infinity=False),
    )

    def forward_mirror_disagrees(rates: tuple[float, float]) -> bool:
        r1, r2 = rates
        table_name = "age"
        entries = [
            {"__factor_group__": "25", "optimal_scenario_value": r1, "quote_count": 1},
            {"__factor_group__": "25", "optimal_scenario_value": r2, "quote_count": 1},
        ]
        artifact = _artifact(
            {table_name: entries},
            {table_name: [{"column": "age", "dtype": {"kind": "Float64"}}]},
        )
        apply_frame = pl.DataFrame({"age": [25.0]}, schema={"age": pl.Float64})
        out = _apply_ratebook(apply_frame.lazy(), artifact, "v1", "__ver__").collect()
        engine_factor = out[f"{table_name}_optimised_factor"][0]

        # Test-local forward-walking mirror: first entry wins
        forward_val: float | None = None
        for entry in entries:
            lvl = normalise_rating_key(entry["__factor_group__"], pl.Float64)
            if lvl == normalise_rating_key(25.0, pl.Float64):
                forward_val = entry["optimal_scenario_value"]
                break

        return forward_val is not None and abs(engine_factor - forward_val) > 1e-4

    found = find(strat, forward_mirror_disagrees, settings=pr_budget(60))
    assert forward_mirror_disagrees(found), f"Forward mirror agreed with engine on {found}"
