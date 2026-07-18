"""LEAD #5 (payload duplication) + LEAD #7 (memory / estimated_size)."""
from __future__ import annotations

import json
import logging
import os
import time

os.environ["HAUTE_LOG_LEVEL"] = "CRITICAL"
import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

import polars as pl

import haute.trace as T
from graph_builders import linear_chain_graph, write_source
from haute.executor import _estimate_preview_cache_entry_bytes


def bytes_of(obj) -> int:
    return len(json.dumps(obj).encode())


print("=" * 78)
print("LEAD #5 — payload anatomy: input_values / output_values duplication")
print("=" * 78)
for rows, cols, nt in [(1000, 20, 12), (5000, 50, 20)]:
    g, target, col = linear_chain_graph(rows, cols, nt)
    res = T.execute_trace(g, row_index=500, target_node_id=target, column=col, preview=None)
    payload = T.trace_result_to_dict(res)
    total = bytes_of(payload)
    steps = payload["steps"]
    in_bytes = sum(bytes_of(s["input_values"]) for s in steps)
    out_bytes = sum(bytes_of(s["output_values"]) for s in steps)
    enr_bytes = sum(bytes_of(s.get("expression")) + bytes_of(s.get("calculation")) for s in steps)
    # projection model: keep only traced col + schema-diff changed cols + referenced cols
    proj_in = 0
    proj_out = 0
    for s in steps:
        keep = set(s["schema_diff"]["columns_added"]) | set(s["schema_diff"]["columns_modified"])
        keep.add(col)
        expr = s.get("expression") or {}
        keep |= set(expr.get("referenced_columns", []) or [])
        proj_out += bytes_of({k: v for k, v in s["output_values"].items() if k in keep})
        proj_in += bytes_of({k: v for k, v in s["input_values"].items() if k in keep})
    print(f"\n  {rows}x{cols}, {len(steps)} steps  (traced '{col}'):")
    print(f"    total payload          : {total/1024:8.1f} KB")
    print(f"    sum input_values       : {in_bytes/1024:8.1f} KB  ({in_bytes/total*100:4.0f}%)")
    print(f"    sum output_values      : {out_bytes/1024:8.1f} KB  ({out_bytes/total*100:4.0f}%)")
    print(f"    sum expression+calc    : {enr_bytes/1024:8.1f} KB  ({enr_bytes/total*100:4.0f}%)")
    print(f"    projected in+out (cols relevant only): {(proj_in+proj_out)/1024:.1f} KB "
          f"vs {(in_bytes+out_bytes)/1024:.1f} KB  "
          f"=> {(in_bytes+out_bytes)/(proj_in+proj_out):.1f}x smaller")

print()
print("=" * 78)
print("LEAD #7 — _estimate_preview_cache_entry_bytes cost (per store, all frames)")
print("=" * 78)
for rows, cols in [(1000, 20), (5000, 50), (50000, 50)]:
    # build a dict of eager_outputs like the trace cache stores (N node frames)
    n_nodes = 12
    frame = pl.read_parquet(write_source(rows, cols, f"mem_{rows}x{cols}.parquet"))
    eager = {f"n{i}": frame for i in range(n_nodes)}  # shared refs (worst case)
    entry = {"eager_outputs": eager}

    t0 = time.perf_counter()
    for _ in range(20):
        total_bytes = _estimate_preview_cache_entry_bytes(entry)
    est_ms = (time.perf_counter() - t0) / 20 * 1000

    one = frame.estimated_size()
    t1 = time.perf_counter()
    for _ in range(50):
        frame.estimated_size()
    one_ms = (time.perf_counter() - t1) / 50 * 1000
    print(f"\n  {rows}x{cols}, {n_nodes} node frames (shared ref):")
    print(f"    per-frame estimated_size()   : {one_ms:.4f}ms  ({one/1024/1024:.1f} MB/frame)")
    print(f"    full entry estimate ({n_nodes} frames): {est_ms:.4f}ms  "
          f"(counts {total_bytes/1024/1024:.1f} MB)")
    print(f"    NOTE: {n_nodes} shared refs counted as {total_bytes/one:.0f}x one frame's bytes "
          f"(double-counting when frames share)")
