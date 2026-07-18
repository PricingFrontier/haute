"""V027 reproduction — _build_value_match_expr dtype-asymmetric crash.

Claim: _build_value_match_expr reconciles dtypes only when the match VALUE is a
str (casts the column to Utf8, line 188). When the value is numeric and the
parent column is string, control falls to ``pl.col(column) == value`` (line 189)
which Polars rejects with ComputeError ("cannot compare string with numeric
type"). Symmetrically, a raw float NaN value against a non-float column emits
``pl.col(column).is_nan()`` which raises InvalidOperationError on a Utf8 column.

Because _correlate_rows_posthoc -> _find_matching_row -> _match_columns_by_row_index
runs the expression inside ``indexed.select(...)`` with NO try/except anywhere on
the trace.py:504 call path, the exception aborts the entire post-hoc correlation
(a hard failure for a 'click a price to trace' request) instead of degrading to
the documented (None, -1) 'no match' result.

ISOLATION: pure in-memory synthetic Polars frames; no disk I/O, no project files,
no rating/ or src/ reads. node_map is empty because the synthetic children are
generic (non-edge-join) nodes, for which node_map.get(...) -> None is handled.
"""

from __future__ import annotations

import math

import polars as pl

from haute._trace_correlation import (
    _build_value_match_expr,
    _correlate_rows_posthoc,
)

failures: list[str] = []


# ---------------------------------------------------------------------------
# Part A — direct expression: numeric value vs string column (line 189 path)
# ---------------------------------------------------------------------------
str_col_df = pl.DataFrame({"x": ["1", "2", "3"]})
crashed_numeric_vs_str = None
try:
    str_col_df.select(_build_value_match_expr("x", 2))
    crashed_numeric_vs_str = False
except Exception as exc:  # noqa: BLE001 - we classify below
    crashed_numeric_vs_str = True
    numeric_vs_str_exc = exc

if crashed_numeric_vs_str:
    msg = str(numeric_vs_str_exc)
    print(f"[A] numeric value vs string column RAISED {type(numeric_vs_str_exc).__name__}: {msg}")
    if "cannot compare string with numeric" not in msg:
        failures.append(
            f"[A] raised but not the predicted compare error: {type(numeric_vs_str_exc).__name__}: {msg}"
        )
else:
    failures.append("[A] expected ComputeError for numeric value vs string column, got none")


# ---------------------------------------------------------------------------
# Part B — mirror direction: string value vs numeric column WORKS (line 188 cast)
# Proves the asymmetry: only the str-value direction is guarded.
# ---------------------------------------------------------------------------
num_col_df = pl.DataFrame({"x": [1, 2, 3]})
try:
    mirror = num_col_df.select(_build_value_match_expr("x", "2").alias("m"))["m"].to_list()
    print(f"[B] string value vs numeric column OK -> {mirror}")
    if mirror != [False, True, False]:
        failures.append(f"[B] mirror direction wrong result: {mirror}")
except Exception as exc:  # noqa: BLE001
    failures.append(f"[B] mirror direction unexpectedly raised {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Part C — NaN value vs string column (lines 177/184 path) raises on Utf8
# ---------------------------------------------------------------------------
nan_str_df = pl.DataFrame({"s": ["a", "b"]})
crashed_nan_vs_str = None
try:
    nan_str_df.select(_build_value_match_expr("s", float("nan")))
    crashed_nan_vs_str = False
except Exception as exc:  # noqa: BLE001
    crashed_nan_vs_str = True
    nan_vs_str_exc = exc

if crashed_nan_vs_str:
    print(f"[C] NaN value vs string column RAISED {type(nan_vs_str_exc).__name__}: {nan_vs_str_exc}")
else:
    failures.append("[C] expected InvalidOperationError for NaN value vs string column, got none")


# ---------------------------------------------------------------------------
# Part D — end-to-end through _correlate_rows_posthoc: the whole trace aborts.
# Parent has a string 'code' column; child casts 'code' -> Int64 AND filters
# rows (3 -> 2), so the same-row-count positional fast path (len(parent)==child)
# is bypassed and value matching is reached. child_row['code'] is a Python int
# (Int64 cell -> _jsonify_row keeps it as int), driving the numeric-vs-string
# crash on the parent's Utf8 column. This is the realistic 'click to trace' path.
# ---------------------------------------------------------------------------
parent_df = pl.DataFrame({"code": ["10", "20", "30"], "v": [1, 2, 3]})
child_df = parent_df.filter(pl.col("v") > 1).with_columns(pl.col("code").cast(pl.Int64))
# child_df: code=[20,30] (Int64), v=[2,3]; len 2 != parent len 3 -> fast path bypassed

eager_outputs = {"p": parent_df, "c": child_df}
order = ["p", "c"]            # topo order: parent before child
parents_of = {"c": ["p"], "p": []}

crashed_e2e = None
try:
    _correlate_rows_posthoc(
        eager_outputs,
        order,
        parents_of,
        "c",          # target node = child
        0,            # row_index 0 of child -> code=20
        node_map={},  # generic (non-edge-join) children: node_map.get(...) -> None, handled
    )
    crashed_e2e = False
except Exception as exc:  # noqa: BLE001
    crashed_e2e = True
    e2e_exc = exc

if crashed_e2e:
    msg = str(e2e_exc)
    print(f"[D] _correlate_rows_posthoc RAISED {type(e2e_exc).__name__}: {msg}")
    if "cannot compare string with numeric" not in msg:
        failures.append(
            f"[D] raised but not the predicted compare error: {type(e2e_exc).__name__}: {msg}"
        )
else:
    failures.append(
        "[D] expected _correlate_rows_posthoc to abort with ComputeError, but it returned"
    )


print()
if failures:
    for f in failures:
        print("FAIL:", f)
    raise SystemExit(
        f"V027 NOT cleanly reproduced ({len(failures)} check(s) failed) — see above"
    )

print(
    "V027 REPRODUCED: numeric-value-vs-string-column and NaN-value-vs-string-column "
    "both raise; the str-value mirror direction is guarded; and the crash propagates "
    "out of _correlate_rows_posthoc, aborting the whole trace instead of yielding (None,-1)."
)
