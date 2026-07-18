"""LEAD #6 — cold-path preview reuse: do trace's preview_fps match the key
the preview endpoint actually stored under? Reproduce both constructions."""
from __future__ import annotations

import logging
import os

os.environ["HAUTE_LOG_LEVEL"] = "CRITICAL"
import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

from graph_builders import linear_chain_graph
from haute._cache import GraphFingerprintMemo
from haute.execution import runtime_input_extra_keys
from haute.executor import (
    ENFORCE_CONTRACTS,
    _preview_projection_cache_suffix,
    _positive_int_from_env,
)
from haute.graph_utils import graph_fingerprint

# PREVIEW_INITIAL_COLUMN_LIMIT
from haute.executor import PREVIEW_INITIAL_COLUMN_LIMIT

g, target, col = linear_chain_graph(1000, 20, 12)
row_limit, source = 1000, "live"
runtime = tuple(runtime_input_extra_keys(g))


def preview_stored_fp(*, target_preview_only, requested_cols, port_label=None):
    """Mirror executor.py:912-941 exactly."""
    memo = GraphFingerprintMemo()
    suffix = _preview_projection_cache_suffix(
        g, target, requested_cols,
        target_preview_only=target_preview_only,
        initial_column_limit=(
            PREVIEW_INITIAL_COLUMN_LIMIT
            if target_preview_only and requested_cols is None else None
        ),
        port_label=port_label,
    )
    extra = [f"{row_limit}:{source}:contracts={int(ENFORCE_CONTRACTS)}{suffix}", *runtime]
    return graph_fingerprint(g, *extra, memo=memo), suffix


def trace_preview_fps(row_values, column):
    """Mirror trace.py:412-438 exactly."""
    memo = GraphFingerprintMemo()
    base = f"{row_limit}:{source}:contracts={int(ENFORCE_CONTRACTS)}"
    req = None
    if row_values:
        req = [str(n) for n in row_values]
        if column and column not in req:
            req.append(column)
    fps = []
    if req is not None:
        suffix = _preview_projection_cache_suffix(
            g, target, req, target_preview_only=True, initial_column_limit=None,
        )
        fps.append((graph_fingerprint(g, base + suffix, *runtime, memo=memo), "projected:" + suffix))
    fps.append((graph_fingerprint(g, base, *runtime, memo=memo), "unsuffixed:(none)"))
    return fps


print("=" * 78)
print("CASE A: common GUI flow — node selected, table shown WITHOUT explicit")
print("        columns (initial column-limit path), then a cell is clicked.")
print("=" * 78)
pv_fp, pv_suffix = preview_stored_fp(target_preview_only=True, requested_cols=None)
print(f"  PREVIEW stored under:")
print(f"    suffix = {pv_suffix!r}")
print(f"    fp     = {pv_fp}")
row_values = {"policy_id": 500, "region": "region_4", "premium_base": 350.0, "factor_0": 385.0}
print(f"\n  TRACE reconstructs (row_values has {len(row_values)} cols, column={col!r}):")
hit = False
for fp, desc in trace_preview_fps(row_values, col):
    match = "  <== MATCH" if fp == pv_fp else ""
    if fp == pv_fp:
        hit = True
    print(f"    {desc[:60]:60s}\n        fp={fp}{match}")
print(f"\n  RESULT: preview reuse {'HITS' if hit else 'MISSES -> COLD full re-execution'}")

print()
print("=" * 78)
print("CASE B: trace click WITHOUT row_values (row_values=None).")
print("=" * 78)
hit = False
for fp, desc in trace_preview_fps(None, col):
    match = "  <== MATCH" if fp == pv_fp else ""
    if fp == pv_fp:
        hit = True
    print(f"    {desc[:60]:60s} {match}")
print(f"  vs preview target-only key -> {'HIT' if hit else 'MISS'}")

print()
print("=" * 78)
print("CASE C: does trace's unsuffixed key match a FULL preview (all nodes,")
print("        target_preview_only=False, requested=None)?")
print("=" * 78)
full_fp, full_suffix = preview_stored_fp(target_preview_only=False, requested_cols=None)
print(f"  FULL preview suffix = {full_suffix!r}  fp={full_fp[:16]}...")
uns_fp, _ = trace_preview_fps(None, col)[-1]
print(f"  trace unsuffixed fp = {uns_fp[:16]}...")
print(f"  MATCH: {full_fp == uns_fp}  "
      f"(=> reuse works ONLY when a full non-target-only preview was cached)")

print()
print("=" * 78)
print("CASE D: happy path — GUI preview WITH explicit requested_preview_columns")
print("        equal to the clicked row's columns, same order.")
print("=" * 78)
cols = ["policy_id", "region", "premium_base", "factor_0"]
pv_fp2, pv_suffix2 = preview_stored_fp(target_preview_only=True, requested_cols=cols)
rv = {c: 0 for c in cols}
proj_fp, proj_desc = trace_preview_fps(rv, col)[0]
print(f"  preview suffix = {pv_suffix2!r}")
print(f"  trace   suffix = {proj_desc.split('projected:')[1]!r}")
print(f"  fingerprints MATCH: {pv_fp2 == proj_fp}")
