"""Micro-benchmarks for the P03 correlation hot path in the CURRENT tree.

Targets FR-03 (_shared_key_is_unique), FR-08 (_match_columns_by_row_index),
and the whole _find_matching_row + _correlate_rows_posthoc path.
"""
from __future__ import annotations

import time
from functools import reduce
from operator import and_

import polars as pl

from haute._trace_correlation import (
    _build_value_match_expr,
    _find_matching_row,
    _jsonify_row,
    _match_columns_by_row_index,
    _shared_key_is_unique,
)


def timeit(fn, *, number: int, warmup: int = 3):
    for _ in range(warmup):
        fn()
    best = float("inf")
    total = 0.0
    for _ in range(number):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        total += dt
        best = min(best, dt)
    return best * 1000.0, (total / number) * 1000.0  # best_ms, mean_ms


def make_frame(rows: int, cols: int, *, unique_key: bool = True) -> pl.DataFrame:
    data = {}
    # id column - unique or with duplicates
    if unique_key:
        data["policy_id"] = pl.arange(0, rows, eager=True)
    else:
        data["policy_id"] = pl.Series([i % (rows // 2) for i in range(rows)])
    data["region"] = pl.Series([f"region_{i % 8}" for i in range(rows)])
    # float columns
    for c in range(cols - 2):
        data[f"col_{c}"] = pl.Series([float(i) * 1.0001 + c for i in range(rows)])
    return pl.DataFrame(data)


def vectorized_key_is_unique(df: pl.DataFrame, match_row: dict, shared_cols: list[str]) -> bool:
    """Vectorized equivalent of _shared_key_is_unique (FR-03 proposed fix)."""
    schema = df.schema
    exprs = [
        _build_value_match_expr(c, match_row.get(c), schema.get(c)).fill_null(False)
        for c in shared_cols
    ]
    height = df.lazy().filter(reduce(and_, exprs)).select(pl.len()).collect().item()
    return height == 1


print("=" * 78)
print("FR-03: _shared_key_is_unique (full-frame Python iter_rows scan)")
print("=" * 78)
for rows, cols in [(1000, 20), (5000, 50)]:
    df = make_frame(rows, cols, unique_key=True)
    # match_row = the last row's values (worst case: must scan whole frame)
    match_row = _jsonify_row(df.row(rows - 1, named=True))
    shared = ["policy_id", "region"]

    # sanity: both agree
    cur = _shared_key_is_unique(df, match_row, shared)
    vec = vectorized_key_is_unique(df, match_row, shared)
    assert cur == vec == True, (cur, vec)

    best_cur, mean_cur = timeit(lambda: _shared_key_is_unique(df, match_row, shared), number=50)
    best_vec, mean_vec = timeit(lambda: vectorized_key_is_unique(df, match_row, shared), number=50)
    print(f"\n  {rows}x{cols} unique key, worst-case (match at last row):")
    print(f"    current  _shared_key_is_unique : best={best_cur:8.3f}ms mean={mean_cur:8.3f}ms")
    print(f"    vectorized filter+len          : best={best_vec:8.3f}ms mean={mean_vec:8.3f}ms")
    print(f"    speedup (mean)                 : {mean_cur/mean_vec:6.1f}x")

print()
print("=" * 78)
print("FR-08: _match_columns_by_row_index (called by _find_matching_row)")
print("=" * 78)
for rows, cols in [(1000, 20), (5000, 50)]:
    df = make_frame(rows, cols, unique_key=True)
    indexed = df.with_row_index("__tmp_idx")
    child_row = _jsonify_row(df.row(rows - 1, named=True))
    all_shared = [c for c in child_row if c in df.columns]

    best, mean = timeit(
        lambda: _match_columns_by_row_index(indexed, child_row, all_shared), number=50
    )
    print(f"\n  {rows}x{cols}, {len(all_shared)} shared cols (exact-match hit):")
    print(f"    _match_columns_by_row_index    : best={best:8.3f}ms mean={mean:8.3f}ms")

    # vectorized exact-match-first alternative (FR-08 proposed): one filter -> <=2 idx
    def vec_exact():
        schema = indexed.schema
        exprs = [
            _build_value_match_expr(c, child_row[c], schema.get(c)).fill_null(False)
            for c in all_shared
        ]
        return indexed.lazy().filter(reduce(and_, exprs)).select("__tmp_idx").head(2).collect()

    best_v, mean_v = timeit(vec_exact, number=50)
    print(f"    vectorized exact-first (<=2 row): best={best_v:8.3f}ms mean={mean_v:8.3f}ms")
    print(f"    speedup (mean)                 : {mean/mean_v:6.1f}x")

print()
print("=" * 78)
print("_find_matching_row end-to-end (exact-match path, what warm click uses)")
print("=" * 78)
for rows, cols in [(1000, 20), (5000, 50)]:
    df = make_frame(rows, cols, unique_key=True)
    child_row = _jsonify_row(df.row(rows - 1, named=True))
    best, mean = timeit(lambda: _find_matching_row(df, child_row), number=50)
    print(f"  {rows}x{cols}: _find_matching_row best={best:8.3f}ms mean={mean:8.3f}ms")
