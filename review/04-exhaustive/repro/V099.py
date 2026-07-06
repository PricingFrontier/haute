"""Isolated reproduction for V099.

Claim: a Binary column containing non-UTF-8 bytes crashes the entire Explore
materialisation because `_categorical_value_counts_expr` performs a STRICT
`pl.col(name).cast(pl.String)` (line 331), and Binary is admitted into the
value-counts branch via `_TEXT_DTYPE_BASES` (line 78) -> `_has_categorical_value_counts(Binary)` True.

This repro:
  1. Confirms the in-scope predicates over pl.Binary.
  2. Rebuilds the EXACT aggregation list that `_build_frame_stats` constructs for
     a single Binary column and runs the same batched collect, asserting it raises
     `ComputeError` mentioning invalid utf8 (the demonstrably wrong behaviour:
     the whole collect dies instead of profiling the column).
  3. Shows that a UTF-8-valid Binary column and a Datetime column DO collect fine,
     proving the trigger is specifically non-UTF-8 bytes (not Binary per se).

No disk I/O, no project files: everything is synthetic in-memory LazyFrames.
"""

from __future__ import annotations

import polars as pl

from haute.routes._explore_service import (
    _categorical_value_counts_alias,
    _categorical_value_counts_expr,
    _column_kind,
    _has_categorical_value_counts,
    _is_unhashable_dtype,
    _supports_categorical_value_counts,
    _supports_min_max,
)

# Polars >=1.x raises ComputeError for invalid utf8 strict casts.
from polars.exceptions import ComputeError


def _build_aggregations_like_build_frame_stats(schema: pl.Schema) -> list[pl.Expr]:
    """Reproduce the per-column aggregation construction from _build_frame_stats.

    Mirrors src/haute/routes/_explore_service.py:421-448 for the branches that a
    Binary column exercises (null_count, n_unique, and the categorical value-count
    expr). min/max is intentionally included via the real predicate so we faithfully
    mirror which branches fire.
    """

    column_names = list(schema.names())
    aggregations: list[pl.Expr] = [pl.len().alias("row_count")]
    for name in column_names:
        dtype = schema[name]
        aggregations.append(pl.col(name).null_count().alias(f"null::{name}"))
        if not _is_unhashable_dtype(dtype):
            aggregations.append(pl.col(name).n_unique().alias(f"unique::{name}"))
        if _supports_min_max(dtype):
            # Mirror _min_max_column_expr only enough for non-Binary; Binary won't enter here.
            aggregations.append(pl.col(name).min().alias(f"min::{name}"))
            aggregations.append(pl.col(name).max().alias(f"max::{name}"))
        if dtype.is_numeric():
            pass  # not exercised by Binary
        elif _has_categorical_value_counts(dtype):
            aggregations.append(
                _categorical_value_counts_expr(name).alias(_categorical_value_counts_alias(name))
            )
    return aggregations


def main() -> None:
    binary_dtype = pl.Binary

    # --- Step 1: the in-scope predicates admit Binary into the value-count branch. ---
    assert _supports_min_max(binary_dtype) is False, (
        f"expected _supports_min_max(Binary) False, got {_supports_min_max(binary_dtype)}"
    )
    assert _supports_categorical_value_counts(binary_dtype) is True, (
        "expected Binary to be admitted to categorical value counts via _TEXT_DTYPE_BASES"
    )
    assert _has_categorical_value_counts(binary_dtype) is True, (
        "expected _has_categorical_value_counts(Binary) True (the value-count branch fires)"
    )
    assert _column_kind(binary_dtype) == "Text", (
        f"expected _column_kind(Binary) == 'Text', got {_column_kind(binary_dtype)!r}"
    )
    print("[predicates] _supports_min_max(Binary)=False, "
          "_has_categorical_value_counts(Binary)=True, _column_kind(Binary)='Text' OK")

    # --- Step 2: non-UTF-8 Binary column crashes the whole batched collect. ---
    non_utf8 = pl.DataFrame(
        {"blob": [b"\xff\xfe\x00", b"\x80", b"\xc3\x28"]},
        schema={"blob": pl.Binary},
    ).lazy()
    bad_aggs = _build_aggregations_like_build_frame_stats(non_utf8.collect_schema())

    raised: Exception | None = None
    try:
        non_utf8.select(bad_aggs).collect()
    except ComputeError as exc:  # the predicted failure
        raised = exc
    except Exception as exc:  # noqa: BLE001 - capture anything else to distinguish
        raised = exc

    assert raised is not None, (
        "BUG NOT REPRODUCED: expected the batched collect over a non-UTF-8 Binary "
        "column to raise, but it succeeded."
    )
    msg = str(raised).lower()
    assert isinstance(raised, ComputeError), (
        f"expected polars ComputeError, got {type(raised).__name__}: {raised!r}"
    )
    assert "utf" in msg or "utf-8" in msg or "utf8" in msg, (
        f"expected an 'invalid utf8' style error, got: {raised!r}"
    )
    print(f"[non-utf8 binary] collect raised {type(raised).__name__}: {raised} -> REPRODUCED")

    # --- Step 3: UTF-8-valid Binary and Datetime both collect fine (specific trigger). ---
    ok_binary = pl.DataFrame(
        {"blob": [b"abc", b"hello", b"abc"]},
        schema={"blob": pl.Binary},
    ).lazy()
    good_aggs = _build_aggregations_like_build_frame_stats(ok_binary.collect_schema())
    ok_row = ok_binary.select(good_aggs).collect().row(0, named=True)
    vc = ok_row[_categorical_value_counts_alias("blob")]
    assert ok_row["row_count"] == 3, ok_row
    assert ok_row["unique::blob"] == 2, ok_row
    print(f"[utf8 binary] collect OK; value_counts payload present={vc is not None}")

    dt = pl.DataFrame(
        {"ts": [None]},
        schema={"ts": pl.Datetime("us")},
    ).lazy()
    dt_aggs = _build_aggregations_like_build_frame_stats(dt.collect_schema())
    dt_row = dt.select(dt_aggs).collect().row(0, named=True)
    assert dt_row["row_count"] == 1, dt_row
    # Datetime DOES enter the value-count branch (non-numeric + supports_min_max),
    # but cast(pl.String) on a temporal is always valid utf8 -> collect succeeds.
    assert _has_categorical_value_counts(pl.Datetime("us")) is True
    print("[datetime] collect OK; cast(pl.String) on temporal is valid utf8")

    print("\nVERDICT: REPRODUCED — non-UTF-8 Binary column kills the whole Explore "
          "aggregation collect via strict cast(pl.String), while UTF-8 Binary / Datetime are fine.")


if __name__ == "__main__":
    main()
