"""Route-level fixed costs (run on the async event loop before offload)
and the serialization triple-cost (to_json_safe -> pydantic -> json.dumps).
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict

os.environ["HAUTE_LOG_LEVEL"] = "CRITICAL"
import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))

import haute.trace as T
from graph_builders import diamond_join_graph, linear_chain_graph
from haute._hashing import content_hash_bytes
from haute.graph_utils import graph_fingerprint
from haute.routes.pipeline import (
    _trace_row_values_fingerprint,
    _trace_supersession_key,
    _validate_runtime_input_paths,
)
from haute.schemas import TraceResponse


def timeit(fn, *, number=200, warmup=5):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(number):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    ts.sort()
    return ts[0], ts[len(ts) // 2]  # best, median (ms)


print("=" * 78)
print("LEAD #1 — Route fixed costs (block the async event loop per request)")
print("=" * 78)
g, target, col = linear_chain_graph(1000, 20, 12)

# graph_fingerprint(graph) — called inside _supersession_key, NO memo.
# base is cached on the instance, so cost = preamble path (none here) + join.
b, m = timeit(lambda: graph_fingerprint(g), number=500)
print(f"  graph_fingerprint(graph) [no preamble, base cached]: best={b:.4f}ms med={m:.4f}ms")

# But a FRESH graph instance each request (flatten_graph makes a new one) means
# the base fingerprint is recomputed once per request. Measure that:
def fresh_base():
    g2 = g.model_copy(deep=True)
    return graph_fingerprint(g2)

b, m = timeit(fresh_base, number=200)
print(f"  graph_fingerprint(FRESH instance) [base recompute]  : best={b:.4f}ms med={m:.4f}ms")

# _trace_row_values_fingerprint at various clicked-row widths
for ncols in [20, 50, 200]:
    row_values = {f"col_{i}": float(i) * 1.5 for i in range(ncols)}
    row_values["region"] = "region_3"
    b, m = timeit(lambda: _trace_row_values_fingerprint(row_values), number=1000)
    print(f"  _trace_row_values_fingerprint ({ncols:3d} cols)          : best={b:.4f}ms med={m:.4f}ms")

# _validate_runtime_input_paths — resolves each runtime input path (filesystem)
try:
    b, m = timeit(lambda: _validate_runtime_input_paths(g), number=200)
    print(f"  _validate_runtime_input_paths(graph)                : best={b:.4f}ms med={m:.4f}ms")
except Exception as e:
    print(f"  _validate_runtime_input_paths: raised ({type(e).__name__}) — skipped")

# Preamble that imports utility => graph_fingerprint hashes utility files.
# The ROUTE's _supersession_key calls graph_fingerprint(graph) with NO memo,
# so this cost is paid on the event loop EVERY click when a preamble imports
# utility. Build a utility package + a graph with such a preamble.
import textwrap
from pathlib import Path

from graph_builders import SCRATCH

util_dir = SCRATCH / "utility"
util_dir.mkdir(exist_ok=True)
(util_dir / "__init__.py").write_text("")
for i in range(6):  # 6-file utility package
    (util_dir / f"mod_{i}.py").write_text("FACTOR = %d\n" % i + "x = 1\n" * 200)

import sys

if str(SCRATCH) not in sys.path:
    sys.path.insert(0, str(SCRATCH))

g_pre = g.model_copy(update={"preamble": "from utility.mod_0 import FACTOR\n"})
try:
    b, m = timeit(lambda: graph_fingerprint(g_pre.model_copy(deep=True)), number=100)
    print(f"  graph_fingerprint w/ utility-import preamble (NO memo, 6-file pkg):")
    print(f"      best={b:.4f}ms med={m:.4f}ms  <-- paid on event loop per click")
except Exception as e:
    print(f"  graph_fingerprint w/ preamble: raised ({type(e).__name__}: {e})")

# full supersession key (what actually runs on the loop)
rv = {f"feat_{i}": float(i) for i in range(50)}
b, m = timeit(
    lambda: _trace_supersession_key(g, "live", target, 500, col, 1000, rv), number=500
)
print(f"  _trace_supersession_key(...) [graph_fp+rowvals hash]: best={b:.4f}ms med={m:.4f}ms")

print()
print("=" * 78)
print("LEAD #4 — Serialization triple cost (per warm click, on the payload)")
print("=" * 78)
for label, (gg, tt, cc, ri) in {
    "linear 1000x20 (13 steps)": (linear_chain_graph(1000, 20, 12) + (500,)),
    "linear 5000x50 (21 steps)": (linear_chain_graph(5000, 50, 20) + (500,)),
}.items():
    res = T.execute_trace(gg, row_index=ri, target_node_id=tt, column=cc, preview=None)

    # Stage 1: trace_result_to_dict = build payload + to_json_safe
    b1, m1 = timeit(lambda: T.trace_result_to_dict(res), number=100)
    payload = T.trace_result_to_dict(res)

    # Stage 2: pydantic TraceResponse validation (FastAPI response_model)
    b2, m2 = timeit(lambda: TraceResponse(status="ok", trace=payload), number=100)
    resp = TraceResponse(status="ok", trace=payload)

    # Stage 3: FastAPI serialization — model_dump + json.dumps
    b3, m3 = timeit(lambda: json.dumps(resp.model_dump(mode="json")), number=100)

    js = json.dumps(payload)
    print(f"\n  {label}:")
    print(f"    1) trace_result_to_dict (to_json_safe): med={m1:.3f}ms")
    print(f"    2) TraceResponse pydantic validation  : med={m2:.3f}ms")
    print(f"    3) model_dump + json.dumps            : med={m3:.3f}ms")
    print(f"    TOTAL serialization                   : med={m1+m2+m3:.3f}ms")
    print(f"    payload size: {len(js)/1024:.1f} KB")
