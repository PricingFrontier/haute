"""Frames shared by the Polars step corpus and the assistant evaluation.

``synthetic_inputs`` builds the corpus's ``quotes``, ``claims`` and ``rates``
frames; the ``polars_corpus`` evaluation project's data files are the normal
variant. ``run`` executes plain Polars code that binds ``df``. ``frames_equal``
is the harness's own comparison, re-exported so every caller compares frames
one way.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from scripts.run_assistant_self_test import frames_equal

__all__ = ["frames_equal", "run", "synthetic_inputs"]


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
