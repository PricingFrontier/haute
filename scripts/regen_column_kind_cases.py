#!/usr/bin/env python3
"""Regenerate the column-kind cases the editor's type rules are checked against.

``frontend/src/utils/__tests__/fixtures/columnKindCases.json`` holds a column type of
every class Polars defines a column can have (a parametrised class in its common forms),
as Polars renders it (``str(dtype)``, what a preview sends the editor), with what each
server rule the editor mirrors answers for it: its kind (``column_kind``), its cast
family (``cast_family``), whether it is a number, a date, or String-or-Categorical, its
GLM class (``glm_dtype_class``) and the Workbench column types it fills
(``column_fits``). ``tests/test_column_kinds.py`` requires this file to equal
``canonical(build_fixture())`` and every Polars type class to have a sample here, so a
type a later Polars adds fails until it has one; the editor's ``columnKinds.test.ts``
requires its rules to give the same answers. After a Polars upgrade or a change to a
rule:

    uv run python scripts/regen_column_kind_cases.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import polars as pl  # noqa: E402

from haute._polars_dtypes import (  # noqa: E402
    DATE_KINDS,
    NUMBER_KINDS,
    column_kind,
    is_string_or_categorical,
)
from haute._polars_steps import cast_family  # noqa: E402
from haute._workbench_output import column_fits  # noqa: E402
from haute.modelling._glm_terms import glm_dtype_class  # noqa: E402

FIXTURE = ROOT / "frontend" / "src" / "utils" / "__tests__" / "fixtures" / "columnKindCases.json"
#: The column types a Workbench table declares (``WorkbenchColumn.type``).
WORKBENCH_COLUMN_TYPES = ("int", "float", "str", "bool", "date")


def sample_dtypes() -> list[pl.DataType]:
    """A column type of every class Polars defines a column can have."""
    return [
        pl.Int8(),
        pl.Int16(),
        pl.Int32(),
        pl.Int64(),
        pl.Int128(),
        pl.UInt8(),
        pl.UInt16(),
        pl.UInt32(),
        pl.UInt64(),
        pl.UInt128(),
        pl.Float16(),
        pl.Float32(),
        pl.Float64(),
        pl.Decimal(10, 2),
        pl.Boolean(),
        pl.String(),
        pl.Categorical(),
        pl.Categorical(pl.Categories("pool", "haute", pl.UInt8)),
        pl.Enum(["a", "b", "c"]),
        pl.Date(),
        pl.Datetime("us"),
        pl.Datetime("ms", "Europe/London"),
        pl.Duration("ns"),
        pl.Time(),
        pl.Binary(),
        pl.Null(),
        pl.List(pl.Int64),
        pl.Array(pl.Int64, 2),
        pl.Struct({"a": pl.Int64}),
        pl.Map(pl.String, pl.Int64),
        pl.Object(),
        pl.Extension("haute.test", pl.Int64),
    ]


def case(dtype: pl.DataType) -> dict[str, Any]:
    """What each rule the editor mirrors answers for *dtype*."""
    kind = column_kind(dtype)
    rendered = str(dtype)
    return {
        "dtype": rendered,
        "kind": kind,
        "cast_family": cast_family(dtype),
        "number": kind in NUMBER_KINDS,
        "date": kind in DATE_KINDS,
        "string_or_categorical": is_string_or_categorical(dtype),
        "glm_class": glm_dtype_class(rendered),
        "fills": [declared for declared in WORKBENCH_COLUMN_TYPES if column_fits(declared, dtype)],
    }


def build_fixture() -> dict[str, Any]:
    return {"cases": [case(dtype) for dtype in sample_dtypes()]}


def canonical(fixture: dict[str, Any]) -> str:
    """One case per line, so a change reads as the cases it touches."""
    lines = [
        json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in fixture["cases"]
    ]
    return '{"cases":[\n' + ",\n".join(lines) + "\n]}\n"


def main() -> None:
    FIXTURE.write_bytes(canonical(build_fixture()).encode("utf-8"))
    print(f"wrote {FIXTURE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
