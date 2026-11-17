"""A column of every Polars type, holding values of its kind, for the column-kind tests.

The types are ``scripts/regen_column_kind_cases.py``'s samples, one of every class
Polars defines a column can have, so the readers' test and the shared case file send
the same types.
"""

from __future__ import annotations

import importlib.util
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from types import ModuleType
from typing import Any

import polars as pl

from haute._polars_dtypes import column_kind
from tests._source_files import REPO_ROOT

ROWS = 6


@cache
def case_script() -> ModuleType:
    """``scripts/regen_column_kind_cases.py``, which holds the sample types and the cases."""
    path = REPO_ROOT / "scripts" / "regen_column_kind_cases.py"
    spec = importlib.util.spec_from_file_location("regen_column_kind_cases", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_dtypes() -> list[pl.DataType]:
    return list(case_script().sample_dtypes())


def _values(dtype: pl.DataType) -> list[Any]:
    """Six values of *dtype*'s kind: repeats, a null and, for a float, a NaN."""
    kind = column_kind(dtype)
    if kind == "integer":
        return [1, 2, None, 2, 3, 1]
    if kind == "float":
        return [1.0, math.nan, 2.0, None, 3.0, 1.0]
    if kind == "decimal":
        return [Decimal(v) if v else None for v in ("1.50", "2.25", "", "2.25", "3.00", "1.50")]
    if kind == "boolean":
        return [True, False, None, True, False, True]
    if kind in {"text", "category"}:
        return ["a", "b", None, "b", "c", "a"]
    if kind == "date":
        return [date(2024, 1, d) if d else None for d in (1, 2, 0, 2, 3, 1)]
    if kind == "datetime":
        return [datetime(2024, 1, d, 8) if d else None for d in (1, 2, 0, 2, 3, 1)]
    if kind == "duration":
        return [timedelta(seconds=s) if s else None for s in (1, 2, 0, 2, 3, 1)]
    if kind == "time":
        return [time(8, m) if m else None for m in (1, 2, 0, 2, 3, 1)]
    if kind == "binary":
        return [b"a", b"b", None, b"b", b"c", b"a"]
    if kind == "null":
        return [None] * ROWS
    if kind == "object":
        return [object() for _ in range(ROWS)]
    return [1, 2, None, 2, 3, 1]  # nested and extension types are built from these


def sample_column(dtype: pl.DataType, name: str = "x") -> pl.Series:
    """A column *name* of *dtype* holding :func:`_values` of its kind."""
    values = _values(dtype)
    if isinstance(dtype, pl.List):
        return pl.Series(name, [None if v is None else [v] for v in values], dtype=dtype)
    if isinstance(dtype, pl.Array):
        return pl.Series(name, [None if v is None else [v, v] for v in values], dtype=dtype)
    if isinstance(dtype, pl.Struct):
        return pl.Series(name, [None if v is None else {"a": v} for v in values], dtype=dtype)
    if isinstance(dtype, pl.Map):
        entries = pl.List(pl.Struct({"key": pl.String, "value": pl.Int64}))
        pairs = [None if v is None else [{"key": "k", "value": v}] for v in values]
        return pl.Series(name, pairs, dtype=entries).cast(dtype)
    if isinstance(dtype, pl.BaseExtension):
        return pl.Series(name, values, dtype=pl.Int64).cast(dtype)
    if isinstance(dtype, pl.Object):
        return pl.Series(name, values, dtype=pl.Object)
    if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
        naive = pl.Series(name, values, dtype=pl.Datetime(dtype.time_unit))
        return naive.dt.replace_time_zone(dtype.time_zone)
    if column_kind(dtype) in {"text", "category"}:
        return pl.Series(name, values, dtype=pl.String).cast(dtype)
    return pl.Series(name, values, dtype=dtype)
