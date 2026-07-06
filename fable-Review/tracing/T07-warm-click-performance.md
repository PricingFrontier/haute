# T07 — Warm-click budget: fixed costs outside the correlation hot path

**Severity:** HIGH (PERF-02) + MEDIUM ×5 · **Effort:** M overall (each item S)
**Pairing:** batch-review class (mechanical, measurable) — per the calibrated split, one reviewer
over the wave is sufficient; no silent-wrongness here.
**Files:** `src/haute/routes/pipeline.py`, `src/haute/_cache.py`, `src/haute/trace.py`,
`src/haute/execution.py`, `src/haute/routes/_supersession.py`, `frontend/src/hooks/useTracing.ts`
**Origin:** PERF-02/04/05/06/08/11/13 (performance review; all measured except 08/11 which are
code-confirmed). The correlation hot path itself (FR-03/04/08) is T03/P03 — fix that first; this doc
is what remains of the 10 ms budget afterwards.
**Benchmarks:** `repros/bench_route.py`, `repros/bench_e2e.py`, `repros/bench_payload_mem.py`,
`repros/bench_coldkey.py` (+ `repros/graph_builders.py`). Re-run before and after each item.

## Measured cost model (current tree)

Warm click, diamond+edge-join+sort, 5000×50: **62.6 ms median** (budget 10 ms) — 73 % is FR-03
(T03). Best-case linear chain 5000×50: **8.3 ms** in `execute_trace` **plus ~2.9 ms** route
serialization ≈ 11 ms — at budget *before any join*. Post-T03, the remaining floor is this doc:

| Fixed cost | ms (measured) | Item |
|---|---|---|
| Supersession-key `graph_fingerprint` with a utility-importing preamble | **12–20 (event loop!)** | T07.1 |
| `runtime_input_extra_keys` per click (1 file input) | 1.5–2.2 | T07.4 |
| `to_json_safe` + pydantic + `json.dumps` (21 steps, 50 cols) | ~5.0 | T07.2 |
| `_enrich_steps` warm | 1.1–1.4 | (FR-10 in T03) |

## T07.1 (PERF-02, HIGH) — memoise the preamble fingerprint; get it off the event loop

`routes/pipeline.py` computes the supersession key synchronously in the async handler before the
thread offload; `_supersession_key → graph_fingerprint(graph)` with **no memo** →
`preamble_execution_fingerprint` (`_cache.py:496`) → `_resolve_utility_locations` (`:378`, calls
`importlib.invalidate_caches()` **every time**) + `_utility_file_hash` (`:396`, `memo=None` ⇒
re-reads every `utility/**/*.py`). Measured 11.8–19.9 ms per click on a 6-file utility package —
blocking the event loop — and `execute_trace` re-hashes the same files again with its own fresh
per-call memo (≥2 full hashes per click).

**Fix:** (a) add a process-wide stat-gated memo for utility-file hashing, mirroring
`execution._stat_gated_runtime_path_fingerprint` (re-hash only when mtime/size changes);
(b) move `importlib.invalidate_caches()` off the per-click path (invalidate on file-watcher events
instead); (c) compute the supersession key inside the offloaded thread, or from the memoised value.
**Failing test:** spy on `haute._cache.content_hash`; two identical clicks with a utility-import
preamble → utility files read at most once (currently ≥2× per click).

## T07.2 (PERF-04) — collapse the triple serialization

`trace_result_to_dict` runs `to_json_safe` over the whole payload (`trace.py:996`) although row
values are already JSON-safe from `_jsonify_row`; pydantic `TraceResponse` re-validates; FastAPI
re-walks for `json.dumps`. Measured 5.0 ms total at 21 steps/50 cols.
**Fix:** scope `to_json_safe` to the sub-dicts that need it (enrichment/waterfall payloads produced
outside `_jsonify_row`), and return a pre-serialized `Response`/`ORJSONResponse` (or
`response_model=None`) to skip re-validation of a dict we just built. Keep the response *shape*
identical — `tests/test_frontend_backend_contract.py` and guards.contract tests are the net.
**Failing test:** byte-identity of `trace_result_to_dict` output under a second `to_json_safe`
pass (proves redundancy), then an equality-of-wire-shape test route-level before/after.

## T07.3 (PERF-05) — project step row payloads to the columns the UI reads

`steps[].input_values`/`output_values` ship every parent's full row + the node's full row:
66 % of a 68.9 KB payload at 21×50; projecting to (traced column ∪ schema-diff added/modified/
removed ∪ `expression.referenced_columns` ∪ `node_detail`-referenced columns) measures 29× smaller
on the row-value portion. **Caveat found by the frontend review:** `StepCard.tsx:87` iterates
`Object.keys(output_values)` for its expanded "all columns" table, and T04.5 adds `{pid}.{k}` keys —
so projection is a **product decision** (the expanded card would show the projected set, with a
"show all columns" fetch if wanted). Recommended: ship projected by default with a
`?full_rows=true` escape hatch on the endpoint; update StepCard copy accordingly.
**Failing test:** contract test enumerating every column the frontend reads (traceValueMap,
StepCard diff table, detail components, InputSourceTree) against a projected payload for the golden
pipelines — no missing keys.

## T07.4 (PERF-06) — stop re-statting file inputs on every warm click

`runtime_input_extra_keys(graph)` runs before the cache lookup on every click (`trace.py:380`):
resolve + 2×stat per file-backed input, 1.5–2.2 ms warm for one input, scaling linearly.
**Fix:** memoise the extras tuple keyed on the graph instance + a short-TTL/mtime gate (the same
stat-gate pattern as T07.1; a 250 ms TTL is enough to collapse click bursts without losing
out-of-band invalidation). **Failing test:** spy `Path.stat`; two warm clicks → file inputs stat'd
once.

## T07.5 (PERF-08) — superseded trace should not hold a work slot to completion

`run_latest` is called without `cancel_active`, so an already-running stale trace runs to completion
holding one of `_TRACE_MAX_CONCURRENCY=2` slots; two stale slow traces block fresh clicks. **Fix:**
re-check `generation == latest_generation` at worker entry (before doing any work inside the slot)
and skip straight to `SupersededRequestError`; optionally plumb `cancel_active` for the waiting
case. **Failing test:** three rapid distinct-key requests; spy `execute_trace` — the middle one
never executes.

## T07.6 (PERF-11) — client-side memo for identical re-clicks

`handleCellClick` refetches unconditionally; re-clicking the same cell pays the full server warm
cost. **Fix:** memoise `traceResult` by `(target_node_id, rowIndex, column, hash(row_values))`,
invalidated by T01's graph-fingerprint invalidation (they must share the invalidation hook — a memo
without T01 would *extend* staleness windows; sequence after T01). **Failing test:** two identical
clicks → one `traceCell` call.

## Acceptance for the package

- `repros/bench_e2e.py` warm medians (post-T03 + this doc): linear 5000×50 ≤ 5 ms in-process;
  diamond+join 5000×50 ≤ 15 ms end-to-end route path. Assert structurally in CI (call-count spies),
  keep wall-clock numbers in the bench script output for humans.
- Event loop never blocks >1 ms in the trace handler before offload (measure via
  `bench_route.py`).
- No wire-shape change without a matching guards/contract-test update (T07.3 is the only shape
  change, behind its contract test).
