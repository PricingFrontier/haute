"""A row of temporal values finer than a microsecond, and the text that shows them whole.

Python's ``datetime``, ``time`` and ``timedelta`` hold microseconds, so a surface that
reads these values through Python shows six of their nine digits (specs/server-api,
specs/deploy). pandas writes a timestamp to Parquet as ``Datetime("ns")``.
"""

from __future__ import annotations

import polars as pl

#: 2024-03-01T09:00:00 as nanoseconds from 1970.
NINE_AM_NS = 1_709_283_600 * 1_000_000_000

#: The row as every surface shows it: ISO 8601 with a ``T``, a duration as
#: ``str(timedelta)`` writes one, nine digits below a microsecond.
NANOSECOND_TEXTS = {
    "at": "2024-03-01T09:00:00.000000100",
    "clock": "09:00:00.000000300",
    "wait": "0:00:00.000000400",
    "cover": {"at": "2024-03-01T09:00:00.000000200"},
}


def nanosecond_frame() -> pl.DataFrame:
    """One row: a ``Datetime("ns")``, a ``Time``, a ``Duration("ns")`` and a struct holding
    a ``Datetime("ns")``, each with digits below a microsecond."""
    return pl.DataFrame(
        {
            "at": [NINE_AM_NS + 100],
            "clock": [9 * 3_600 * 1_000_000_000 + 300],
            "wait": [400],
            "cover": [{"at": NINE_AM_NS + 200}],
        },
        schema={
            "at": pl.Int64,
            "clock": pl.Int64,
            "wait": pl.Int64,
            "cover": pl.Struct({"at": pl.Int64}),
        },
    ).cast(
        {
            "at": pl.Datetime("ns"),
            "clock": pl.Time,
            "wait": pl.Duration("ns"),
            "cover": pl.Struct({"at": pl.Datetime("ns")}),
        }
    )
