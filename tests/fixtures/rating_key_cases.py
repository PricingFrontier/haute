"""Shared dtype-faithful rating-key cases.

Runtime, trace, and optimiser tests import this one matrix so adding a factor
dtype cannot accidentally cover only one of the three key consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import polars as pl


@dataclass(frozen=True)
class RatingKeyCase:
    name: str
    dtype: pl.DataType
    value: Any
    entry_value: Any
    descriptor: dict[str, Any]
    matches: bool = True


RATING_KEY_CASES = (
    RatingKeyCase("int8", pl.Int8, -8, -8, {"kind": "Int8"}),
    RatingKeyCase("int16", pl.Int16, -16, -16, {"kind": "Int16"}),
    RatingKeyCase("int32", pl.Int32, -32, -32, {"kind": "Int32"}),
    RatingKeyCase("int64", pl.Int64, -(2**40), -(2**40), {"kind": "Int64"}),
    RatingKeyCase("int128", pl.Int128, -(2**80), str(-(2**80)), {"kind": "Int128"}),
    RatingKeyCase("uint8", pl.UInt8, 8, 8, {"kind": "UInt8"}),
    RatingKeyCase("uint16", pl.UInt16, 16, 16, {"kind": "UInt16"}),
    RatingKeyCase("uint32", pl.UInt32, 32, 32, {"kind": "UInt32"}),
    RatingKeyCase("uint64", pl.UInt64, 2**63 + 1, str(2**63 + 1), {"kind": "UInt64"}),
    RatingKeyCase("float32", pl.Float32, 0.1, 0.1, {"kind": "Float32"}),
    RatingKeyCase("float64", pl.Float64, 0.1, 0.1, {"kind": "Float64"}),
    RatingKeyCase("boolean", pl.Boolean, True, True, {"kind": "Boolean"}),
    RatingKeyCase("string", pl.String, "25.0", "25.0", {"kind": "String"}),
    RatingKeyCase(
        "categorical",
        pl.Categorical,
        "north",
        "north",
        {"kind": "Categorical"},
    ),
    RatingKeyCase(
        "enum",
        pl.Enum(["north", "south"]),
        "north",
        "north",
        {"kind": "Enum", "categories": ["north", "south"]},
    ),
    RatingKeyCase(
        "decimal",
        pl.Decimal(10, 2),
        Decimal("25.50"),
        "25.50",
        {"kind": "Decimal", "precision": 10, "scale": 2},
    ),
    RatingKeyCase(
        "date",
        pl.Date,
        date(2024, 1, 2),
        "2024-01-02",
        {"kind": "Date"},
    ),
    RatingKeyCase(
        "datetime",
        pl.Datetime("ms"),
        datetime(2024, 1, 2, 3, 4, 5, 123000),
        "2024-01-02 03:04:05.123",
        {"kind": "Datetime", "timeUnit": "ms", "timeZone": None},
    ),
    RatingKeyCase(
        "datetime_utc",
        pl.Datetime("us", "UTC"),
        datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        "2024-01-02 03:04:05.123456+00:00",
        {"kind": "Datetime", "timeUnit": "us", "timeZone": "UTC"},
    ),
    RatingKeyCase(
        "time",
        pl.Time,
        time(3, 4, 5, 123456),
        "03:04:05.123456",
        {"kind": "Time"},
    ),
    RatingKeyCase(
        "duration",
        pl.Duration("ms"),
        timedelta(seconds=1, milliseconds=234),
        "PT1.234S",
        {"kind": "Duration", "timeUnit": "ms"},
    ),
    RatingKeyCase("null", pl.Null, None, None, {"kind": "Null"}, matches=False),
)
