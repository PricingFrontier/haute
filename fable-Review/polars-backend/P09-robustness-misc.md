# P09 — Robustness misc: timing units, error classification, probes, pins, fallbacks

**Severity:** MEDIUM cluster · **Effort:** M (each item is S; batch them) ·
**Dev/reviewer pair REQUIRED for FR-27/28/29/30** (silent-wrongness class); FR-31/32/33 may batch.

---

## FR-27 [MEDIUM, silent wrongness — wrong number in UI] — multi-frame node timings recorded in seconds, everything else in milliseconds
**`_execute_lazy.py:1937-1938` vs `:2069` (`_execute_eager_core`)**

The multi-frame dict branch does:
```python
t1 = time.perf_counter()
timings[nid] = round(t1 - t0, 6)      # SECONDS
...
continue                               # skips line 2069
```
while every other node hits `timings[nid] = round((time.perf_counter() - t0) * 1000, 1)` (ms). The
value flows into `NodeResult.timing_ms` (`executor.py:1272,1324`) — the timing panel shows a
multi-frame apiInput at 0.25 "ms" when it took 250 ms.

**Fix:** `timings[nid] = round((t1 - t0) * 1000, 1)` in the dict branch (keep the branch-local capture
so the `continue` stays).
**Failing test first:** fake clock via monkeypatched `time.perf_counter` returning scripted values;
execute a multi-frame apiInput fixture; assert `timings[nid]` equals the delta × 1000 (today: × 1).

---

## FR-28 [MEDIUM, error masking] — streaming-compatibility classified by `"stream" in str(exc)`
**`_polars_utils.py:42-48`, used by `streaming_collect` (:76-92), `bounded_collect_batches` (:145-153), `bounded_sink` (:255-264), `best_effort_sink` (:425)**

Any `ComputeError`/`InvalidOperationError`/`SchemaError` whose message merely CONTAINS "stream" —
including "downstream"/"upstream", or a user column literally named `stream_id` echoed in a
SchemaError — is rebranded `BoundedMemoryUnsupportedError` (masking the real error) or, on the
preview path, triggers the broad in-memory fallback collect. Substring classification of exceptions is
the anti-pattern the fail-loud mandate targets.

**Fix (pragmatic, still heuristic but far tighter):**
1. Enumerate the actual Polars messages: grep the installed Polars for the streaming-unsupported
   error texts (e.g. "not supported in streaming", "streaming engine", "cannot run … in streaming
   mode" — verify against the pinned version) and match on those phrases, anchored, case-insensitive.
2. Prefer structural signals where available: the failure comes from `collect(engine="streaming")` /
   `sink_*` — check for the modern polars exception subtype if one exists in the pinned version
   before falling back to phrase matching.
3. Keep the conversion contract identical (BoundedMemoryUnsupportedError with the same fields).

**Failing tests first:** (a) a `ComputeError("column 'downstream_factor' not found")` raised from the
collect must propagate UNCHANGED (today: converted); (b) a genuine streaming-unsupported error string
still converts; (c) `best_effort_sink` does not broad-collect on (a).

---

## FR-29 [MEDIUM] — zero-row batch scoring requires the model to predict a synthetic all-null probe row
**`_model_scorer.py:1474-1493`**

With zero rows, a 1-row all-null probe frame is scored purely to learn the output dtype; a strict
pyfunc with signature enforcement (non-nullable ints) or a null-rejecting GLM raises on a
legitimately-empty batch (upstream filter matched nothing) where the correct output is an empty scored
frame. (History: the probe replaced a hardcoded Float64 that caused a real dtype-divergence bug — do
not regress that.)

**Fix:** derive the prediction dtype from model/task metadata when available (task + flavor already
imply Int64 hard-label vs Float64 score for the supported flavors — the mapping lives in
`_mlflow_io`/flavor dispatch); fall back to the probe ONLY when metadata is absent; alternatively
build the probe from representative non-null values (feature-contract dtypes' zero values) rather
than nulls.
**Failing test first:** stub scoring model whose `predict` raises on null input; zero-row batch;
assert an empty scored frame with the correct dtype (today: raises). Keep the existing
dtype-divergence regression test green — it is the counter-guard.

---

## FR-30 [MEDIUM] — LRU silently refuses oversized entries, turning the trace-reuse `pin()` into a no-op
**`_lru_cache.py:145-158` (`put`), consumer contract `executor.py:1104-1121`**

An entry larger than `max_bytes` is warn-logged and NOT cached; the caller's subsequent
`pin(fp)` is a silent no-op (pin of unknown key). The immediate preview response is still correct
(it uses local variables), but the documented "trace reuses the exact same DataFrames" contract is
quietly voided — trace recomputes with only a warning to show for it. Against fail-loud.

**Fix:** make "not cached" an explicit signal — `put()` returns bool (or `store()` raises a typed
`EntryOversizedError` the caller catches deliberately); `execute_graph` then branches consciously:
skip the pin, log at INFO with the size, and (optionally) surface `cache_skipped=True` in the response
metadata. No behaviour change for fitting entries.
**Failing test first:** preview whose frame exceeds a tiny test `PREVIEW_CACHE_MAX_BYTES`; assert the
new explicit signal fires and `pin` is not attempted (today: silent no-op pin — assert via spy).

---

## FR-31 [LOW] — live_switch falls back to the first input on an unmapped scenario
**`_node_apply.py:80-96`** — configured-but-unmatched scenario returns `input_order[0]` with only a
WARNING; wrong-branch data flows on as if correct. **Fix:** raise when `input_scenario_map` is
non-empty and the scenario is unmapped (config error); keep the fallback only for the genuinely
unconfigured case (`not input_scenario_map`). Failing test: mapped switch + unknown scenario → typed
error (today: warning + first input).

## FR-32 [LOW] — in-flight admission reservation can leak if context construction raises
**`_execution_admission.py:430-465`** — `_reserve_in_flight_budget` (:430) precedes
`ExecutionContext(...)` construction (:450-462) and the `weakref.finalize` registration (:463-464);
an exception in between leaks the reservation permanently (budget shrinks process-wide). Construction
is near-infallible today, but this path runs under memory pressure. **Fix:** wrap :450-465 in
`try/except: release + raise`. Test: monkeypatch the context class to raise; assert the budget is
restored.

## FR-33 [LOW] — eager input-contract check silently skips column-cache misses
**`_execute_lazy.py:1838-1841`** — `column_cache[k] for k in edge_cache_keys if k in column_cache`
under-counts upstream columns when an edge's `(source, sourceHandle)` key is absent (e.g. a
sourceHandle set on a single-frame parent stored under `(pid, None)`), which can produce a spurious
`ContractMismatchError`. The lazy path (:1152-1161) computes missing entries via `_columns_of`
instead — correct. **Fix:** mirror the lazy path's fallback (compute + cache on miss). Failing test:
single-frame parent + edge with an explicit sourceHandle + concrete input contract → passes (today:
raises/under-counts).
