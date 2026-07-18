"""Isolated reproduction for V048.

Claim: ``haute.deploy._mlflow._build_signature`` silently maps every
unrecognized Polars dtype (Decimal/Time/Duration/List/Struct/...) to
``DataType.string`` via ``dtype_map.get(base_type, DataType.string)``,
producing a WRONG serving contract (e.g. a numeric Decimal money column is
advertised to Databricks Model Serving as text), instead of failing loudly
like the sibling builder ``haute.modelling._signature.build_signature``.

ISOLATION: no disk, no network, no project files. We build the schema dict
exactly as ``infer_input_schema``/``infer_output_schema`` would
(``{col: str(dtype)}``) using real Polars dtypes, then drive ``_build_signature``
through a minimal stand-in object exposing only ``input_schema`` /
``output_schema`` (the only two attributes the function reads).
"""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl
from mlflow.types import DataType

from haute.deploy._mlflow import _build_signature


def _input_type_map(sig) -> dict[str, DataType]:
    return {col.name: col.type for col in sig.inputs.inputs}


def main() -> None:
    # Reproduce schema dicts EXACTLY as deploy._schema does: {col: str(dtype)}.
    # A premium/money column is a Decimal; a policy time-of-day is Time; a
    # duration column is Duration; a multi-value column is List.
    premium_dtype_str = str(pl.Decimal(18, 2))  # 'Decimal(precision=18, scale=2)'
    time_dtype_str = str(pl.Time)               # 'Time'
    duration_dtype_str = str(pl.Duration)       # 'Duration'
    list_dtype_str = str(pl.List(pl.Int64))     # 'List(Int64)'

    print(f"premium dtype str   = {premium_dtype_str!r}")
    print(f"time dtype str      = {time_dtype_str!r}")
    print(f"duration dtype str  = {duration_dtype_str!r}")
    print(f"list dtype str      = {list_dtype_str!r}")

    input_schema = {
        "premium": premium_dtype_str,
        "tod": time_dtype_str,
        "elapsed": duration_dtype_str,
        "tags": list_dtype_str,
    }
    output_schema = {"final_premium": premium_dtype_str}

    resolved = SimpleNamespace(input_schema=input_schema, output_schema=output_schema)

    sig = _build_signature(resolved)
    type_map = _input_type_map(sig)
    out_map = {col.name: col.type for col in sig.outputs.inputs}

    print(f"\nMLflow signature input type map: {type_map}")
    print(f"MLflow signature output type map: {out_map}")

    # ---- Assert the SPECIFIC wrong VALUES (expected vs actual) ----
    # A Decimal premium column is numeric money; the serving contract must NOT
    # advertise it as free text. The bug coerces it to DataType.string.
    assert type_map["premium"] == DataType.string, (
        "Repro premise failed: expected the BUGGY behaviour where a Decimal "
        f"premium maps to DataType.string, got {type_map['premium']!r}"
    )
    # Sanity: this is genuinely WRONG. A money column being typed as 'string'
    # in the serving signature is a contract defect.
    assert type_map["premium"] != DataType.double, (
        "If this fired the bug would already be fixed (Decimal -> double)."
    )

    # Same silent-and-wrong coercion for Time / Duration / List, and even the
    # OUTPUT premium column (the scored final premium) is advertised as text.
    assert type_map["tod"] == DataType.string, type_map["tod"]
    assert type_map["elapsed"] == DataType.string, type_map["elapsed"]
    assert type_map["tags"] == DataType.string, type_map["tags"]
    assert out_map["final_premium"] == DataType.string, out_map["final_premium"]

    print(
        "\nBUG CONFIRMED: Decimal/Time/Duration/List input columns AND the "
        "Decimal output column are all silently advertised as DataType.string "
        "in the serving signature (expected a numeric/temporal type or a loud "
        "ValueError, as the sibling modelling._signature builder raises)."
    )

    # Cross-check the contrast: the sibling builder DOES fail loudly on Decimal,
    # proving the deploy path's behaviour is the inconsistent / wrong one.
    from haute.modelling._signature import _map_dtype

    raised = False
    try:
        _map_dtype(premium_dtype_str)
    except ValueError as exc:
        raised = True
        print(f"\nSibling modelling._signature._map_dtype raises as expected: {exc}")
    assert raised, (
        "Expected modelling._signature._map_dtype to RAISE on Decimal, "
        "demonstrating the deploy path takes the opposite silent behaviour."
    )


if __name__ == "__main__":
    main()
    print("\nALL ASSERTIONS PASSED — V048 reproduced.")
