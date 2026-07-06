"""End-to-end execute_trace cost model: per-stage timing, warm vs cold.

Wraps the stage functions execute_trace calls (by patching haute.trace.<name>)
to attribute wall-clock to each stage, then runs cold + warm clicks on
representative graphs and shapes.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict

os.environ["HAUTE_LOG_LEVEL"] = "CRITICAL"
import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

import haute._trace_correlation as C
import haute.trace as T
from graph_builders import diamond_join_graph, linear_chain_graph

STAGE_MS: dict[str, float] = defaultdict(float)
STAGE_N: dict[str, int] = defaultdict(int)


def _wrap(mod, name, label):
    orig = getattr(mod, name)

    def timed(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            STAGE_MS[label] += (time.perf_counter() - t0) * 1000.0
            STAGE_N[label] += 1

    setattr(mod, name, timed)
    return orig


# Patch the stages execute_trace invokes.
_wrap(T, "runtime_input_extra_keys", "runtime_input_extra_keys")
_wrap(T, "graph_fingerprint", "graph_fingerprint")
_wrap(T, "_correlate_rows_posthoc", "_correlate_rows_posthoc")
_wrap(T, "_enrich_steps", "_enrich_steps")
_wrap(T, "build_waterfall_from_steps", "build_waterfall")
_wrap(T, "_assemble_steps", "_assemble_steps")
_wrap(T, "_prune_to_column_relevance", "_prune_to_column_relevance")
_wrap(T, "_materialize_eager_outputs", "_materialize_eager_outputs (cold only)")
# correlation internals (module-global lookups inside _correlate_rows_posthoc)
_wrap(C, "_shared_key_is_unique", "  FR-03 _shared_key_is_unique")
_wrap(C, "_find_matching_row", "  FR-04/08 _find_matching_row")
# cache: wrap in a delegating proxy (FingerprintCache uses __slots__)
class _CacheProxy:
    def __init__(self, real):
        self._real = real

    def try_get(self, fp):
        t0 = time.perf_counter()
        try:
            return self._real.try_get(fp)
        finally:
            STAGE_MS["_cache.try_get"] += (time.perf_counter() - t0) * 1000.0
            STAGE_N["_cache.try_get"] += 1

    def store(self, fp, **kw):
        t0 = time.perf_counter()
        try:
            return self._real.store(fp, **kw)
        finally:
            STAGE_MS["_cache.store (cold only)"] += (time.perf_counter() - t0) * 1000.0
            STAGE_N["_cache.store (cold only)"] += 1

    def __getattr__(self, name):
        return getattr(self._real, name)


T._cache = _CacheProxy(T._cache)


def reset():
    STAGE_MS.clear()
    STAGE_N.clear()


def run_click(g, target, col, row_index, row_values=None):
    t0 = time.perf_counter()
    res = T.execute_trace(g, row_index=row_index, target_node_id=target, column=col,
                          row_values=row_values, preview=None)
    total = (time.perf_counter() - t0) * 1000.0
    # serialization (route does this after execute_trace)
    t1 = time.perf_counter()
    payload = T.trace_result_to_dict(res)
    ser = (time.perf_counter() - t1) * 1000.0
    return res, total, ser, payload


def bench(label, g, target, col, row_index, warm_iters=30):
    print("\n" + "=" * 78)
    print(f"{label}")
    print("=" * 78)
    # Cold click
    reset()
    res, cold_total, cold_ser, payload = run_click(g, target, col, row_index)
    cold_stage = dict(STAGE_MS)
    print(f"  COLD total={cold_total:.2f}ms  steps={len(res.steps)}  serialize={cold_ser:.2f}ms")
    for k in sorted(cold_stage, key=lambda x: -cold_stage[x]):
        print(f"      {k:42s} {cold_stage[k]:8.3f}ms  x{STAGE_N[k]}")

    # Warm clicks (cache hit) - average over iters, re-click same cell
    warm_totals = []
    warm_sers = []
    warm_accum: dict[str, float] = defaultdict(float)
    for _ in range(warm_iters):
        reset()
        res, wt, ws, payload = run_click(g, target, col, row_index)
        warm_totals.append(wt)
        warm_sers.append(ws)
        for k, v in STAGE_MS.items():
            warm_accum[k] += v
    warm_totals.sort()
    warm_sers.sort()
    median = warm_totals[len(warm_totals) // 2]
    best = warm_totals[0]
    print(f"\n  WARM total: best={best:.3f}ms median={median:.3f}ms  "
          f"serialize median={warm_sers[len(warm_sers)//2]:.3f}ms")
    print(f"  WARM per-stage (mean over {warm_iters} clicks):")
    for k in sorted(warm_accum, key=lambda x: -warm_accum[x]):
        mean = warm_accum[k] / warm_iters
        if mean < 0.001:
            continue
        pct = mean / median * 100 if median else 0
        print(f"      {k:42s} {mean:8.3f}ms  ({pct:4.0f}% of warm)")
    # bytes
    import json
    js = json.dumps(payload)
    print(f"  payload JSON size: {len(js)/1024:.1f} KB  ({len(res.steps)} steps)")
    return best, median


# ---- Linear chains (no reorder => FR-03 gate NOT triggered) ----
for rows, cols, nt in [(1000, 20, 12), (5000, 50, 20)]:
    g, target, col = linear_chain_graph(rows, cols, nt)
    bench(f"LINEAR chain {rows}x{cols}, {nt} transforms, trace '{col}' row 500",
          g, target, col, min(500, rows - 1))

# ---- Diamond + join + sort (reorder => value-matching + FR-03 gate) ----
for rows, cols in [(1000, 20), (5000, 50)]:
    g, target, col = diamond_join_graph(rows, cols)
    bench(f"DIAMOND+JOIN+SORT {rows}x{cols}, trace '{col}' row 100",
          g, target, col, min(100, rows - 1))
