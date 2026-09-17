"""Executed equivalence corpus for the low-code Polars step builder.

``tests/fixtures/polars_steps_corpus/corpus.json`` holds hand-written Polars
snippets across the categories an analyst writes (filters, formulas,
conditionals, strings, dates, casts, sorting, dedup, aggregation, windows,
joins, reshaping, pipelines); ``translations.json`` holds each snippet's step
list, with auxiliary upstream Transform nodes where the snippet builds an
intermediate frame. Every translation must reproduce the snippet exactly on
the normal and the hard synthetic data (exact dtypes, null pattern, row order
unless the snippet leaves it unspecified); a snippet the vocabulary cannot
express is listed in ``NOT_EXPRESSIBLE`` with its reason, so an addition that
makes it expressible fails here until the fixture is retranslated.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._polars_steps import (
    CAST_DTYPES,
    FUNCTIONS,
    STEP_KINDS,
    PolarsStepError,
    render_polars_steps,
)

FIXTURES = Path(__file__).parent / "fixtures" / "polars_steps_corpus"
CORPUS: dict[str, dict[str, Any]] = {
    item["id"]: item for item in json.loads((FIXTURES / "corpus.json").read_text(encoding="utf-8"))
}
TRANSLATIONS: dict[str, dict[str, Any]] = json.loads(
    (FIXTURES / "translations.json").read_text(encoding="utf-8")
)
#: Snippets the vocabulary does not express, with the fragment their fixture reason must carry.
NOT_EXPRESSIBLE: dict[str, str] = {
    "typed_rating_export": "Enum",
    "vehicle_and_age_bands": "Banding node",
}


def synthetic_inputs(*, hard: bool) -> dict[str, pl.LazyFrame]:
    n = 40
    regions = ["north", "south", "east", "west"]
    channels = ["web", "broker", "phone"]
    fuels = ["petrol", "diesel", "electric"]
    quotes = pl.DataFrame(
        {
            "quote_id": list(range(1, n + 1)),
            "policy_id": [100 + (i % 12) for i in range(n)],
            "region": [regions[i % 4] for i in range(n)],
            "channel": [channels[i % 3] for i in range(n)],
            "premium": [
                None if i % 9 == 0 else round(120.0 + (i * 37.5) % 900, 2) for i in range(n)
            ],
            "sum_insured": [5000.0 + (i * 1234.0) % 60000 for i in range(n)],
            "start_date": [
                dt.date(2024, 1, 1) + dt.timedelta(days=(i * 11) % 500) for i in range(n)
            ],
            "age": [18 + (i * 7) % 60 for i in range(n)],
            "claims_count": [(i * 3) % 4 for i in range(n)],
            "ncd_years": [(i * 5) % 10 for i in range(n)],
            "vehicle_value": [2000.0 + (i * 987.0) % 40000 for i in range(n)],
            "fuel": [fuels[i % 3] for i in range(n)],
            "postcode": [f"{'ABCDEFGH'[i % 8]}B{i % 30 + 1} {i % 9}CD" for i in range(n)],
        }
    )
    claims = pl.DataFrame(
        {
            "claim_id": list(range(1, 25)),
            "policy_id": [100 + (i * 5) % 12 for i in range(24)],
            "claim_date": [
                dt.date(2024, 2, 1) + dt.timedelta(days=(i * 23) % 400) for i in range(24)
            ],
            "amount": [round(150.0 + (i * 431.7) % 5000, 2) for i in range(24)],
            "status": [["open", "closed", "declined"][i % 3] for i in range(24)],
        }
    )
    rates = pl.DataFrame(
        {
            "region": regions,
            "base_rate": [1.05, 1.10, 0.95, 1.00],
            "factor": [1.2, 0.9, 1.1, 1.0],
        }
    )
    if hard:
        # Null keys, zero divisors, duplicate policy/date pairs, negative and
        # zero amounts, a region missing from rates, duplicated rate keys, tied
        # values, threshold rows, and a column that collides with a helper name.
        quotes = quotes.with_columns(
            pl.when(pl.col("quote_id") % 7 == 0)
            .then(None)
            .otherwise(pl.col("region"))
            .alias("region"),
            pl.when(pl.col("quote_id") % 11 == 0)
            .then(0.0)
            .otherwise(pl.col("sum_insured"))
            .alias("sum_insured"),
            pl.when(pl.col("quote_id") % 5 == 0)
            .then(pl.date(2024, 3, 3))
            .otherwise(pl.col("start_date"))
            .alias("start_date"),
            pl.when(pl.col("quote_id") % 6 == 0).then(None).otherwise(pl.col("age")).alias("age"),
            pl.when(pl.col("quote_id") % 13 == 0)
            .then(None)
            .otherwise(pl.col("channel"))
            .alias("channel"),
        )
        claims = claims.with_columns(
            pl.when(pl.col("claim_id") % 8 == 0)
            .then(0.0)
            .when(pl.col("claim_id") % 9 == 0)
            .then(-25.0)
            .otherwise(pl.col("amount"))
            .alias("amount"),
            pl.when(pl.col("claim_id") % 10 == 0)
            .then(None)
            .otherwise(pl.col("status"))
            .alias("status"),
        )
        rates = rates.filter(pl.col("region") != "west")
        quotes = quotes.with_columns(
            pl.when(pl.col("quote_id") % 17 == 0)
            .then(2600.0)
            .when(pl.col("quote_id") % 19 == 0)
            .then(1700.0)
            .otherwise(pl.col("premium"))
            .alias("premium"),
            pl.when(pl.col("quote_id").is_in([3, 15]))
            .then(pl.date(2024, 6, 6))
            .otherwise(pl.col("start_date"))
            .alias("start_date"),
            pl.lit(1.0).alias("gap"),
        )
        claims = claims.with_columns(
            pl.when(pl.col("policy_id") == 111)
            .then(None)
            .when(pl.col("claim_id") % 7 == 0)
            .then(12500.0)
            .otherwise(pl.col("amount"))
            .alias("amount"),
        )
        rates = pl.concat(
            [rates, pl.DataFrame({"region": ["north"], "base_rate": [1.5], "factor": [1.5]})]
        )
    return {"quotes": quotes.lazy(), "claims": claims.lazy(), "rates": rates.lazy()}


def run(code: str, inputs: dict[str, pl.LazyFrame]) -> pl.DataFrame:
    namespace: dict[str, object] = {"pl": pl, **inputs}
    exec(code, namespace, namespace)  # noqa: S102 - fixture code, as the executor runs it
    out = namespace["df"]
    assert isinstance(out, (pl.DataFrame, pl.LazyFrame))
    return out.collect() if isinstance(out, pl.LazyFrame) else out


def _canonical(frame: pl.DataFrame) -> pl.DataFrame:
    sortable = [
        name for name, dtype in frame.schema.items() if not isinstance(dtype, (pl.List, pl.Struct))
    ]
    return frame.sort(sortable) if sortable else frame


def frames_equal(a: pl.DataFrame, b: pl.DataFrame, *, order_free: bool) -> tuple[bool, str]:
    """Strict equality: columns, dtypes, null patterns, values; row order unless order-free."""
    if a.columns != b.columns:
        return False, f"columns differ: {a.columns} vs {b.columns}"
    if a.height != b.height:
        return False, f"row counts differ: {a.height} vs {b.height}"
    ca, cb = (_canonical(a), _canonical(b)) if order_free else (a, b)
    for name in a.columns:
        sa, sb = ca[name], cb[name]
        if sa.dtype != sb.dtype:
            return False, f"dtype differs on {name}: {sa.dtype} vs {sb.dtype}"
        if not sa.is_null().equals(sb.is_null()):
            return False, f"null pattern differs on {name}"
        if sa.dtype.is_float():
            fa, fb = sa.fill_null(0.0), sb.fill_null(0.0)
            close = ((fa - fb).abs() < 1e-6) | (fa.is_nan() & fb.is_nan())
            close = close | (fa.is_infinite() & (fa == fb))
            if not close.fill_null(False).all():
                return False, f"values differ on {name}"
        elif not sa.equals(sb):
            return False, f"values differ on {name}"
    return True, ""


def _expected(code: str, inputs: dict[str, pl.LazyFrame]) -> tuple[pl.DataFrame | None, str | None]:
    try:
        return run(code, inputs), None
    except Exception as exc:  # noqa: BLE001 - the snippet's own failure is the expectation
        return None, type(exc).__name__


def test_fixture_and_gap_list_agree() -> None:
    assert set(TRANSLATIONS) == set(CORPUS)
    gaps = {sid for sid, entry in TRANSLATIONS.items() if entry.get("steps") is None}
    assert gaps == set(NOT_EXPRESSIBLE)
    for sid, fragment in NOT_EXPRESSIBLE.items():
        assert fragment in TRANSLATIONS[sid]["reason"], sid
    known = set(synthetic_inputs(hard=False))
    for sid, item in CORPUS.items():
        assert set(item["inputs"]) <= known, sid


def test_the_listed_gaps_are_still_outside_the_vocabulary() -> None:
    """The gap list is honest: the constructs it names are not expressible."""
    assert "Enum" not in CAST_DTYPES
    with pytest.raises(PolarsStepError, match="must be one of"):
        render_polars_steps(
            [
                {"id": "s", "kind": "source", "input": "quotes"},
                {"id": "c", "kind": "cast", "casts": [{"column": "region", "dtype": "Enum"}]},
            ],
            ["quotes"],
            start="input",
        )
    assert not {"cut", "band", "qcut"} & set(FUNCTIONS)
    assert not {"band", "banding", "cut"} & set(STEP_KINDS)


@pytest.mark.parametrize("hard", [False, True], ids=["normal", "hard"])
@pytest.mark.parametrize("snippet_id", sorted(CORPUS))
def test_steps_reproduce_hand_written_polars(snippet_id: str, hard: bool) -> None:
    item = CORPUS[snippet_id]
    entry = TRANSLATIONS[snippet_id]
    if entry.get("steps") is None:
        assert snippet_id in NOT_EXPRESSIBLE
        return
    expected, expected_error = _expected(item["code"], synthetic_inputs(hard=hard))

    # Auxiliary transform nodes render against the base inputs and are bound
    # under their names, as upstream nodes are in the graph.
    bound = synthetic_inputs(hard=hard)
    for aux_name, aux_steps in (entry.get("aux") or {}).items():
        aux_code = render_polars_steps(aux_steps, list(bound), start="input").code
        bound[aux_name] = run(aux_code, dict(bound)).lazy()
    rendered = render_polars_steps(entry["steps"], list(bound), start="input")
    try:
        actual = run(rendered.code, bound)
    except Exception as exc:  # noqa: BLE001 - compared against the snippet's own failure
        assert expected_error is not None, f"steps raised {type(exc).__name__}: {exc}"
        assert type(exc).__name__ == expected_error, (
            f"{type(exc).__name__} != {expected_error}: {exc}"
        )
        return
    assert expected_error is None, f"the snippet raises ({expected_error}) but the steps succeed"
    assert expected is not None
    ok, why = frames_equal(expected, actual, order_free=bool(entry.get("order_free")))
    assert ok, f"{why}\n{rendered.code}"
