"""Adversarial repro for claim:
    auto-range-float32-envelope-summation-precision

CLAIM: Auto-range casts each constraint column to Float32 (line 4601 of
_optimiser_service.py), computes per-quote min/max (Float32), then SUMS those
extrema across all quotes. Polars sum() on a Float32 column returns Float32, so
the achievable (min,max) totals are accumulated at Float32 precision. For a
portfolio of ~2e6 quotes with constraint magnitude ~1234.567, the Float32
running sum loses ~1e-7 relative precision (~176 absolute error on a ~2.47e9
sum). These totals are returned verbatim as the frontier auto-range min/max.

This script drives the EXACT cited component
(_ScenarioFrontierRangeAccumulator) the way the auto-range path does:
  * constraint column is Float32 (mirrors line 4601 cast pl.col(c).cast(Float32))
  * per-quote min/max aggregation (aggregate_exprs, lines 1437-1442)
  * cross-quote SUM of extrema (bucket_total_exprs, lines 1449-1454; reduction
    1523-1546)

We feed a synthetic frame of N quotes, each with ONE row whose constraint value
is exactly 1234.567 (so per-quote min == max == 1234.567 and the sum of extrema
equals N * 1234.567). We then ASSERT that the returned range bound equals the
Float32-rounded sum (which is demonstrably WRONG vs the exact Float64 sum), and
that the absolute error matches the predicted ~176.

ISOLATION: all disk I/O goes through tempfile; we build a tiny in-memory frame;
we touch no real project files. We do NOT need a project root because the
accumulator only uses the temp parts_root we pass.

Verdict logic:
  * If returned bound == Float32 sum AND != Float64 sum (error ~176) -> REAL.
  * If returned bound == Float64 exact sum                          -> REFUTED.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import polars as pl

from haute.routes._optimiser_service import _ScenarioFrontierRangeAccumulator

# ---------------------------------------------------------------------------
# Synthetic portfolio: N quotes, 1 scenario row each, constraint == VALUE.
# ---------------------------------------------------------------------------
N = 2_000_000
VALUE = 1234.567
QID_COL = "quote_id"
CONSTRAINT = "premium_total"

# Per-quote unique ids as strings (matches the real path: quote_id is cast to
# String at line 1614 before feeding the accumulator).
quote_ids = pl.int_range(0, N, eager=True).cast(pl.String)

# CRITICAL: the constraint column is Float32, exactly mirroring the auto-range
# cast at _optimiser_service.py:4601  ->  pl.col(c).cast(pl.Float32()).
frame = pl.DataFrame(
    {
        QID_COL: quote_ids,
        CONSTRAINT: pl.Series(CONSTRAINT, [VALUE] * N, dtype=pl.Float32),
    }
)

assert frame.schema[CONSTRAINT] == pl.Float32, "input constraint must be Float32"

# ---------------------------------------------------------------------------
# Sanity: confirm Polars sum() on Float32 stays Float32 (the load-bearing fact).
# ---------------------------------------------------------------------------
sum_dtype = frame.select(pl.col(CONSTRAINT).sum()).schema[CONSTRAINT]
print(f"polars version              : {pl.__version__}")
print(f"sum() output dtype          : {sum_dtype}")

# Exact reference (Float64): N * VALUE computed in full precision.
exact_f64 = float(N) * float(VALUE)

# The Float32 accumulated sum (what the buggy path produces for a single bucket).
f32_sum = float(frame.select(pl.col(CONSTRAINT).sum()).item())

print(f"exact Float64 sum           : {exact_f64!r}")
print(f"Float32 accumulated sum      : {f32_sum!r}")
print(f"absolute error (f32 - f64)  : {f32_sum - exact_f64!r}")

# ---------------------------------------------------------------------------
# Drive the ACTUAL cited accumulator end-to-end through a temp parts dir.
# partition_count=1 forces a single bucket so the entire cross-quote sum is one
# Float32 reduction (the worst-case / clearest demonstration). The real path
# uses many buckets, in which each bucket's sub-sum is still Float32-lossy.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="repro_auto_range_f32_") as tmp:
    acc = _ScenarioFrontierRangeAccumulator(
        quote_id_col=QID_COL,
        constraint_cols=[CONSTRAINT],
        partition_count=1,
        parts_root=Path(tmp),
    )
    # Feed in a couple of batches to exercise the batched reducer path; the
    # union still covers all N distinct quotes exactly once.
    half = N // 2
    acc.add_batch(frame.slice(0, half), batch_index=0)
    acc.add_batch(frame.slice(half, N - half), batch_index=1)

    ranges = acc.finish()

returned_min = ranges[CONSTRAINT]["min"]
returned_max = ranges[CONSTRAINT]["max"]

print(f"accumulator returned min    : {returned_min!r}")
print(f"accumulator returned max    : {returned_max!r}")
print(f"returned == naive f32 sum?  : {returned_min == f32_sum}")
print(f"returned == Float64 exact?  : {returned_min == exact_f64}")

abs_err = abs(returned_min - exact_f64)
print(f"|returned - exact_f64|      : {abs_err!r}")

# ---------------------------------------------------------------------------
# Float64 counterfactual: run the IDENTICAL envelope computation but with the
# constraint as Float64 (i.e. WITHOUT the line-4601 cast). If Float64 lands on
# the exact value while the Float32 path does not, the precision loss is caused
# precisely by the Float32 accumulation the claim names.
# ---------------------------------------------------------------------------
frame_f64 = frame.with_columns(pl.col(CONSTRAINT).cast(pl.Float64))
with tempfile.TemporaryDirectory(prefix="repro_auto_range_f64_") as tmp64:
    acc64 = _ScenarioFrontierRangeAccumulator(
        quote_id_col=QID_COL,
        constraint_cols=[CONSTRAINT],
        partition_count=1,
        parts_root=Path(tmp64),
    )
    acc64.add_batch(frame_f64.slice(0, half), batch_index=0)
    acc64.add_batch(frame_f64.slice(half, N - half), batch_index=1)
    ranges_f64 = acc64.finish()
returned_min_f64 = ranges_f64[CONSTRAINT]["min"]
abs_err_f64 = abs(returned_min_f64 - exact_f64)
print(f"Float64-path returned min   : {returned_min_f64!r}")
print(f"|f64-path - exact_f64|      : {abs_err_f64!r}")

# ---------------------------------------------------------------------------
# Assertions: the claim is REAL iff the Float32 path's returned envelope bound
# is a Float32-scale-wrong total (NOT the exact Float64 sum) AND the Float64
# path is exact.
# ---------------------------------------------------------------------------

# 1) Per-quote min == max == VALUE (1 step each), so sum-of-extrema == sum-of-all.
assert returned_min == returned_max, (
    f"with 1 step/quote min and max envelopes must match: "
    f"{returned_min!r} != {returned_max!r}"
)

# 2) The Float32 path's returned bound must NOT equal the exact Float64 sum:
#    it is corrupted by Float32 accumulation.
assert returned_min != exact_f64, (
    f"returned bound unexpectedly equals exact Float64 sum {exact_f64!r}; "
    f"this would REFUTE the claim (Float32 accumulation was lossless here)"
)

# 3) The Float64 counterfactual MUST be exact (proves the loss is the cast, not
#    the algorithm). float32(1234.567) rounds the per-value, but summed in f64
#    the total equals N * float(float32(VALUE)) exactly representable here.
exact_f64_of_f32_value = float(N) * float(np.float32(VALUE))
assert returned_min_f64 == exact_f64_of_f32_value, (
    f"Float64 envelope path should be exact: got {returned_min_f64!r}, "
    f"expected {exact_f64_of_f32_value!r}"
)

# 4) The Float32 path's absolute error vs the f64-accurate envelope must be a
#    non-trivial Float32-scale error (order 1e2 on a ~2.47e9 sum => ~1e-7 rel).
abs_err_vs_f64path = abs(returned_min - returned_min_f64)
rel_err = abs_err_vs_f64path / returned_min_f64
print(f"|f32-path - f64-path|       : {abs_err_vs_f64path!r}")
print(f"relative error (f32 vs f64) : {rel_err!r}")
assert abs_err_vs_f64path > 1.0, (
    f"expected a non-trivial absolute error from Float32 accumulation, "
    f"got {abs_err_vs_f64path!r}"
)
assert 1e-8 < rel_err < 1e-6, (
    f"expected ~1e-7 relative error (Float32 epsilon scale), got {rel_err!r}"
)

print()
print("REPRODUCED: auto-range envelope bound carries Float32 accumulation error")
print(f"  f32-path returned = {returned_min!r}")
print(f"  f64-path (exact)  = {returned_min_f64!r}")
print(f"  abs error         = {abs_err_vs_f64path!r}  (rel {rel_err:.3e})")
