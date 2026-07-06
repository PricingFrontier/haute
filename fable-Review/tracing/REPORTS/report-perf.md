# Report: performance

## Verdict (≤10 lines)
The warm-click budget (`trace.py:372`: "subsequent clicks <10ms") holds **only for trivial linear `with_columns` pipelines at ≤1000 rows**. Measured warm re-clicks: linear 1000×20 = **4.5ms** (OK), linear 5000×50 = **8.3ms** + ~3ms route serialization ≈ 11ms (at budget), but **any pipeline with an edge-join or a sort/group-by is 3–6× over**: diamond+join 1000×20 = **28.7ms**, 5000×50 = **62.6ms**. The dominant cost is unchanged from P03: `_shared_key_is_unique`'s full-frame Python `iter_rows` scan (**FR-03, still open**), which is **68–73% of every warm click** on join graphs and fires **once per edge-join/reordering node** (an edge-join has no `code`, so it is conservatively treated as "may reorder"). FR-04 (double scan) and FR-08 remain open too. Two **new** HIGH/MEDIUM costs sit *outside* the trace cache: (1) a utility-importing preamble makes the route's supersession-key `graph_fingerprint` cost **12–20ms on the event loop, un-memoised, every click**; (2) triple serialization (`to_json_safe` → pydantic → `json.dumps`) is **~5ms** for a 50-col trace. The preview→trace reuse optimization is effectively **inert** for the common target-only preview flow (cache-key drift, proven empirically). Payload is **66% redundant full-row duplication** (29× compressible). Fixing FR-03 alone brings join pipelines from ~60ms to ~12ms; the remaining budget is then fixed costs (runtime-input stat ~2ms + enrich ~1ms + serialize ~5ms).

## Cost model

**Warm click — realistic join pipeline (diamond + edge-join + sort, 5000×50, 6 nodes), measured median = 62.6ms.** Budget = 10ms.

| Stage | Warm ms | Cold ms | % of budget | Notes |
|---|---|---|---|---|
| route `graph_fingerprint` (supersession key, no preamble) | 0.004 | 0.004 | 0% | base fp cached on instance |
| route `graph_fingerprint` (**with utility preamble**) | **12–20** | 12–20 | **120–200%** | event loop, **no memo**, every click (PERF-02) |
| route `_trace_row_values_fingerprint` | 0.02 | 0.02 | 0% | negligible |
| `runtime_input_extra_keys` (1 file input) | 2.2 | 20.1* | 22% | stat-gated per file, every click (PERF-06); *cold = first-ever content hash |
| `graph_fingerprint` (memo, in execute_trace) | 0.06 | 0.25 | 1% | |
| `_cache.try_get` | 0.01 | 0.005 | 0% | |
| `_materialize_eager_outputs` | 0 (hit) | 19.5† | — | †toy; real model-score ≈1.7s (first click) |
| **`_correlate_rows_posthoc`** | **57.0** | 49.9 | **570%** | **FR-03 `_shared_key_is_unique` 45.8ms + `_find_matching_row` 10.4ms** |
| `_assemble_steps` | 0.22 | 0.25 | 2% | |
| `_enrich_steps` | 1.1 | 1.2 | 11% | no file I/O; parse/eval only |
| `_prune_to_column_relevance` | 0.07 | 0.07 | 1% | |
| `build_waterfall_from_steps` | 0.03 | 0.03 | 0% | |
| `trace_result_to_dict` (`to_json_safe`) | 0.48 | 0.33 | 5% | 6-step payload; **2.1ms at 21 steps** |
| route: pydantic `TraceResponse` + `json.dumps` | ~0.9 | — | 9% | **+2.9ms at 21 steps** (PERF-04) |

**Warm click — best-case linear `with_columns` chain (FR-03 gate NOT triggered), medians:**

| Shape | warm total | runtime_input_extra_keys | _enrich_steps | _correlate | to_json_safe | route pydantic+dumps |
|---|---|---|---|---|---|---|
| linear 1000×20, 13 steps | 4.5ms | 1.5ms (34%) | 1.4ms (31%) | 0.69ms | 0.58ms | ~0.9ms |
| linear 5000×50, 21 steps | 8.3ms | ~2.0ms | ~1.1ms | 2.0ms | 1.76ms | 2.9ms |

Even with FR-03 out of the picture, the fixed-cost floor (`runtime_input_extra_keys` + `_enrich_steps` + `_correlate` + triple serialization) is **~8–11ms** at 5000×50 — already at/over budget before any join.

## Findings

### PERF-01 [HIGH] — `_shared_key_is_unique` full-frame Python `iter_rows` scan dominates every warm click on join/reorder graphs
- **Location:** `_trace_correlation.py:499-529`, called at `:772`.
- **Claim:** The positional fast-path uniqueness gate `for raw_row in df.iter_rows(named=True): candidate = _jsonify_row(raw_row)` jsonifies **all** columns of **every** parent row and cannot short-circuit for a unique key. It fires whenever a node has a same-row-count parent, the positional candidate matches on shared columns, and the child `_child_transform_may_reorder` is True. **An edge-join node has no `code` config, so `_child_transform_may_reorder` returns True by default** (`:489-496`) — so *every edge-join triggers one full scan of its base parent's frame per click*. Real pricing pipelines join policies/claims/exposure → several scans/click.
- **Measurement (my run):**
```
FR-03 _shared_key_is_unique (worst case, match at last row):
  1000x20 : current 11.882ms mean (9.582ms best)  | vectorized filter+len 0.546ms → 21.8x
  5000x50 : current 144.778ms mean (115ms best)    | vectorized 0.794ms          → 182.3x
DIAMOND+JOIN+SORT e2e warm: _shared_key_is_unique = 19.5ms (68% of 28.7ms warm) @1000×20;
                                                     45.8ms (73% of 62.6ms warm) @5000×50 — ONE call each.
```
- **Fix sketch + saving:** vectorise via `_build_value_match_expr` (already dtype-robust in the current tree) — `df.lazy().filter(reduce(and_, exprs)).select(pl.len()).collect().item() == 1`. Saves ~19ms/edge-join @1000×20, ~45ms @5000×50; brings the diamond warm click from 62.6ms → ~12ms. (Exactly the P03 FR-03 fix; FR-04 then removes the gate for most cases.)
- **First failing test (structural):** monkeypatch `pl.DataFrame.iter_rows` with a spy, run a warm click on a graph with an edge-join, assert `iter_rows` is not called on the base parent frame.
- **Confidence:** High. **Overlap:** tracked-as-FR-03 (**still open**; W4 commit b19ff1f4 did not touch it).

### PERF-02 [HIGH] — utility-importing preamble makes the route's supersession-key `graph_fingerprint` cost 12–20ms on the event loop, un-memoised, per click
- **Location:** `routes/pipeline.py:195` (`_supersession_key` → `graph_fingerprint(graph)`, **no memo**) → `_cache.py:496 preamble_execution_fingerprint` → `:378 _resolve_utility_locations` (`importlib.invalidate_caches()` + `PathFinder.find_spec`) + `:396 _utility_file_hash` (memo=None → `content_hash` reads every `utility/**/*.py` on every call).
- **Claim:** `_trace_supersession_key` is computed **synchronously in the async handler** (`:447`, before the `to_thread` offload), so it blocks the event loop. With a `from utility... import` preamble (standard in Haute; see `tests/test_trace.py:787`), each click re-hashes the whole utility package with no memo, plus a global `invalidate_caches()`. `execute_trace` then recomputes the same fingerprint again (`trace.py:381`, with a *fresh per-call* memo), so utility files are hashed **≥2× per click**.
- **Measurement (my run, 6-file utility package):**
```
graph_fingerprint w/ utility-import preamble (NO memo): best=11.80ms med=19.92ms  <- on event loop, per click
graph_fingerprint no preamble (base cached)           : 0.004ms
```
- **Fix sketch + saving:** thread a process-wide stat-gated memo through `preamble_execution_fingerprint` (mirror `execution._stat_gated_runtime_path_fingerprint`); compute the supersession key off the event loop or reuse `execute_trace`'s memo. Saves 12–20ms event-loop block/click (100–200% of budget) and the second re-hash. Also drop the per-call `invalidate_caches()` to an edit-triggered path.
- **First failing test:** spy on `haute._cache.content_hash`; issue two identical trace clicks with a utility preamble; assert utility files are read at most once (currently ≥2× per click).
- **Confidence:** High (measured). **Overlap:** new (not in P03).

### PERF-03 [HIGH] — shared-columns fast path double-scans on non-unique keys
- **Location:** `_trace_correlation.py:764-780` (fast path) then falls through to `_find_matching_row` at `:783`.
- **Claim:** When the positional candidate matches but the key is non-unique (or the reorder gate fails), the code pays the `_shared_key_is_unique` scan **and then** `_find_matching_row` (which re-scans via `_match_columns_by_row_index`). Two full passes over the parent frame. My diamond warm run shows both paths active (`_shared_key_is_unique` ×1 **and** `_find_matching_row` ×2).
- **Measurement:** `_find_matching_row` end-to-end: 3.4ms mean @1000×20, 17.7ms @5000×50 — paid *in addition* to the FR-03 scan on the non-unique branch.
- **Fix sketch + saving:** delete the shared-columns fast-path branch (keep only the no-shared-columns positional `elif`); let `_find_matching_row` handle everything (precondition: FR-05 float-tolerance merge first). Removes the double scan; ~one full-frame pass saved per affected node.
- **First failing test:** spy counting filter/collect passes on a non-unique-shared-key correlation; assert exactly one scan.
- **Confidence:** High. **Overlap:** tracked-as-FR-04 (still open).

### PERF-04 [MEDIUM] — triple serialization re-walks the whole payload three times
- **Location:** `trace.py:996` (`to_json_safe` over the full payload) → `routes/pipeline.py:462` (`TraceResponse(...)` pydantic validation) → FastAPI `jsonable_encoder` + `json.dumps`.
- **Claim:** The payload is already JSON-safe after `_jsonify_row` ran during correlation, yet `to_json_safe` re-coerces the entire nested structure, then pydantic re-validates/copies it, then FastAPI re-walks it to serialize. Three full traversals per warm click.
- **Measurement (linear 5000×50, 21 steps, 68.9KB):**
```
1) trace_result_to_dict (to_json_safe): 2.139ms
2) TraceResponse pydantic validation  : 0.371ms
3) model_dump + json.dumps            : 2.531ms
TOTAL serialization                   : 5.042ms  (50% of the 10ms budget)
```
- **Fix sketch + saving:** the row values are already primitives post-`_jsonify_row`; skip the blanket `to_json_safe` (or apply it only to enrichment/waterfall sub-dicts). Return a pre-serialized `Response` to bypass pydantic re-validation, or set `response_model=None` and hand FastAPI the already-safe dict. Saves ~2–3ms/wide click.
- **First failing test:** assert `trace_result_to_dict` output is byte-identical after a second `to_json_safe` pass (proves stage 1 redundant for row values).
- **Confidence:** High (measured). **Overlap:** new.

### PERF-05 [MEDIUM] — `steps[].input_values` / `output_values` ship every parent's full row; 66% of payload, 29× compressible
- **Location:** `trace.py:832-834` (`input_values` = union of parents' full rows), `_assemble_steps`; serialized at `:977-978`.
- **Claim:** Each step ships its node's entire output row **and** the union of its parents' entire output rows, for all columns — but the graph overlay reads only the one traced column (`useTracing.ts:313` reads `s.output_values[column]` only) and `StepCard` shows per-column input→output only for schema-diff/referenced columns. Most shipped values are never displayed.
- **Measurement (my run):**
```
5000x50, 21 steps: total 68.9KB | input_values 22.3KB (32%) | output_values 23.7KB (34%)
projected to (traced col + changed cols + referenced cols): 1.6KB vs 46.0KB → 29.3x smaller
```
A 20-node×50-col trace ships ~2000 cell values, ~66% redundant; browser `JSON.parse` + guard validation scale with this.
- **Fix sketch + saving:** project `input_values`/`output_values` to (traced column ∪ `schema_diff` added/modified/removed ∪ `expression.referenced_columns`) before serialization. Cuts the 68.9KB payload to ~24KB (~2.9× overall; row-value portion 29×), proportional client parse savings. Verify `StepCard`'s "all columns" view against the projected set (it iterates `Object.keys(output_values)` at `StepCard.tsx:87`).
- **First failing test:** assert a projected payload still contains every column `StepCard`/`traceValueMap` reads for a representative trace.
- **Confidence:** High (measured). **Overlap:** new.

### PERF-06 [MEDIUM] — `runtime_input_extra_keys` re-stats every file input on every click (warm too)
- **Location:** `trace.py:380` (unconditional, before the cache lookup) → `execution.py:555 _runtime_file_inputs_signature` → `:331 _stat_gated_runtime_path_fingerprint` (`path.resolve()` + `is_file()` + `stat()` per file).
- **Claim:** Runs on every warm click even on a trace-cache hit; each file-backed input costs ~2–3 filesystem calls (`resolve` + 2× `stat`). Scales with the number of source/model/external-file inputs.
- **Measurement:** 1.5–2.2ms/click warm for a **single** dataSource input (22–34% of the linear warm budget); 18–20ms on the first-ever call (cold content hash of the parquet).
- **Fix sketch + saving:** cache the runtime-extra-keys tuple per `(PipelineGraph instance, mtime-gate)` for the life of the request/trace-cache entry — the graph structure is fixed between clicks, so re-stat only when the trace-cache key would change. Saves ~2ms × (file inputs) per warm click.
- **First failing test:** spy on `os.stat`/`Path.stat`; two warm clicks on the same cached trace; assert file inputs are stat'd at most once.
- **Confidence:** High (measured). **Overlap:** new.

### PERF-07 [MEDIUM] — preview→trace reuse is inert for the common target-only preview flow (cache-key drift, empirically proven)
- **Location:** `trace.py:412-438` (`preview_fps` reconstruction) vs `executor.py:912-941` (preview store key).
- **Claim:** The common GUI flow previews a node **target-only, without explicit columns** (`client.ts:549` omits `requested_preview_columns` → key suffix `:preview_target_only=…:initial_col_limit=N`). Trace never reconstructs the `initial_col_limit` suffix — with `row_values` it builds `:preview_cols=(…)`, without it only the unsuffixed key. Neither matches. Additionally, target-only previews materialize *only the clicked node*, so even a key hit can't supply the ancestor chain (`trace.py:719` `missing_preview_nodes`). Net: the first trace click **cold-executes the full ancestor chain** regardless of preview.
- **Measurement (my `bench_coldkey.py`, exact key reconstruction):**
```
CASE A (common flow): preview fp = v6:67d4307b…  ; trace fps = v6:4df497a4… , v6:e893b8f4…  → MISS → cold re-execution
CASE B (no row_values): unsuffixed only → MISS vs target-only preview
CASE C: trace unsuffixed == FULL non-target-only preview → MATCH (reuse fires only here)
```
- **Nuance (severity calibration):** this is **not** the "first click costs 2× the full pipeline" the brief feared — the target-only preview didn't do the ancestor work, so there's no duplicated full run. The real cost is (a) the reuse optimization + its key-reconstruction complexity almost never pays off, and (b) the target node itself is re-executed by the trace though the preview just materialized it. Hence MEDIUM, not HIGH.
- **Fix sketch:** either have the preview endpoint also cache under the base (unsuffixed) full-graph key when it materializes ancestors, or drop the projected `preview_fps` reconstruction and document that trace's first click is always a full cold run (matching the README). Removes dead complexity.
- **First failing test:** assert `set(trace preview_fps) ∩ {preview stored fp}` is non-empty for the target-only + `row_values` flow (currently empty).
- **Confidence:** High (empirical). **Overlap:** new (brief's lead #6, resolved to a no-op-optimization rather than a 2× waste).

### PERF-08 [MEDIUM] — superseded-but-active trace runs to completion holding its semaphore slot
- **Location:** `routes/pipeline.py:446-459` calls `run_latest(...)` **without `cancel_active`**; `_supersession.py:63,97,110-130`.
- **Claim:** Because `cancel_active` is None, an already-active trace is never signalled on supersession (`_supersession.py:63` guard is a no-op). The worker (`await worker()`, `:103`) runs to completion; only afterward does `:124-126` raise `SupersededRequestError` and discard the result. With `_TRACE_MAX_CONCURRENCY=2`, two slow stale traces (e.g. drag-selecting cells over a 5000×50 join graph at ~60ms each, or a cold ~1.7s model-score) hold both slots, blocking fresh clicks up to the 120s timeout. (Python/polars C calls aren't interruptible anyway, so early cancel would only help *waiting*, not *active*, requests — but the slot is still burned.)
- **Measurement:** structural; per-click active work = the warm/cold cost model above (28–62ms warm on join graphs, up to ~1.7s cold).
- **Fix sketch:** at worker entry, skip execution when `generation != latest_generation` before acquiring/holding the slot for the full run.
- **First failing test:** fire 3 rapid distinct trace keys; assert the middle (superseded-while-waiting) one does not enter `execute_trace` (spy on it).
- **Confidence:** Medium-High (code-confirmed). **Overlap:** new.

### PERF-09 [MEDIUM] — trace and preview caches double-count shared frames against equal budgets
- **Location:** `trace.py:229-247` (`TRACE_CACHE_MAX_BYTES` defaults to `PREVIEW_CACHE_MAX_BYTES`), `executor.py:475 _estimate_preview_cache_entry_bytes`.
- **Claim:** When trace reuses preview frames (`trace.py:729`, shared refs), both caches count the full `estimated_size()` against their own budget → the accounting believes ~2× the real resident bytes → premature eviction (conservative). When frames are distinct (cold execute), peak RSS is ~2× a single budget because the two budgets are equal and independent.
- **Measurement:** the estimator itself is cheap — `_estimate_preview_cache_entry_bytes` = 0.017–0.023ms even for 12 frames @50k rows (`estimated_size()` is O(1) metadata, ~0.001ms/frame) — so **no latency finding**, purely accounting/memory.
- **Fix sketch:** share one budget across both caches, or track shared frames by identity so a frame counted in the preview cache isn't re-counted in the trace cache.
- **First failing test:** store the same frame dict in both caches; assert combined accounted bytes ≈ 1× (not 2×) resident.
- **Confidence:** High. **Overlap:** new.

### PERF-10 [MEDIUM] — `_match_columns_by_row_index` O(rows×cols) Python double-loop
- **Location:** `_trace_correlation.py:283-315`, via `_find_matching_row`.
- **Measurement:** 2.5ms @1000×20, **13.0ms @5000×50** (materialises every alias column with `.to_list()` and double-loops per cell). A vectorised exact-first (`filter(...).head(2)`) is ~1.6× faster (8.1ms @5000×50) — modest because building 50 predicates dominates.
- **Fix sketch:** try the exact match vectorised first (≤2 indices); build the per-cell `matched_by_row` accounting only when exact matching fails and `allow_relaxed`. **Overlap:** tracked-as-FR-08 (still open). **Confidence:** High (measured).

### PERF-11 [MEDIUM] — no client-side trace cache: re-clicking the same cell refetches
- **Location:** `frontend/src/hooks/useTracing.ts:212 handleCellClick` → `traceCell(...)` unconditionally; no memo keyed on `(node,row,col)`.
- **Claim:** The projection/edge caches memoise *node rendering*, not *trace results*. Re-clicking an already-traced cell issues a fresh backend request, paying the full server warm cost every time (28–62ms on join graphs per PERF-01). The backend trace `_cache` hit is fast, but the request round-trip + correlation + serialization still runs.
- **Fix sketch:** memoise `traceResult` by `(target_node_id, rowIndex, column, row_values-hash)` in the hook; return the cached trace on identical re-click. Saves a full server round-trip + the 28–62ms server warm cost per repeat click.
- **First failing test (frontend):** click a cell twice; assert `traceCell` fetch is invoked once.
- **Confidence:** Medium (static). **Overlap:** new.

### PERF-12 [LOW] — enrichment recursion re-derives shared subtrees (FR-10) and O(n) `all_steps.index`
- **Location:** `_trace_enrichment.py:1227` (`visited=set(visited)` per branch) and `:1092` (`all_steps.index(current_step)`, value-equality on a dataclass carrying the large row dicts).
- **Measurement:** `_enrich_steps` total is only ~1–2ms warm (no file I/O; confirmed), so absolute impact is small, but both remain unfixed. `.index()` does deep dict comparisons on `TraceStep` value-equality.
- **Fix sketch:** share one `visited`/memo dict across the invocation; build a `{node_id: index}` map once. **Overlap:** tracked-as-FR-10/FR-11 (still open). **Confidence:** Medium.

### PERF-13 [LOW] — relocation path (`_find_target_row_index`) is a full Python `iter_rows` scan
- **Location:** `trace.py:262-271`, taken at `:489` when the cached preview row was evicted/reordered.
- **Claim:** Same O(rows) Python scan class as FR-03; only on the relocation branch (cache eviction between preview and click), so rare. The row-**verification** loop (`trace.py:471-481`) is cheap: one `target_df.row(row_index, named=True)` + a dict compare over the clicked columns (sub-ms even at 200 columns).
- **Fix sketch:** vectorise with `_build_value_match_expr` (same as FR-03/FR-06), and raise on ambiguity rather than returning the first match. **Overlap:** tracked-as-FR-06 (still open, silent-wrongness + perf). **Confidence:** Medium.

## P03 perf-item status (FR-03/04/08/10)
All four remain **open**; commit b19ff1f4 (W4-trace fail-loud) added `_build_value_match_expr` dtype-robustness and routed `_match_columns_by_row_index` through it, but did not vectorise the scans or delete the double path.
- **FR-03** (`_shared_key_is_unique`): **OPEN, worse at scale than the prior report.** Current best-case matches the prior 8.94ms (my 9.58ms best @1000×20); mean **11.9ms @1000×20**, **144.8ms @5000×50** (prior report only measured 1000×20). Vectorised equivalent 0.5–0.8ms (**22–182×**). Confirmed reachable once per edge-join/reorder node on the warm path (diamond e2e: 1 call = 68–73% of the warm click).
- **FR-04** (double scan): **OPEN.** `:764-780` fast path still present ahead of `_find_matching_row` at `:783`; diamond e2e shows both `_shared_key_is_unique` and `_find_matching_row` firing.
- **FR-08** (`_match_columns_by_row_index`): **OPEN.** 2.5ms @1000×20, 13.0ms @5000×50; ~1.6× recoverable.
- **FR-10** (`visited=set(visited)`): **OPEN** at `:1227`; low absolute impact (enrich ≈1–2ms).

## Strengths
- The trace `_cache` (byte-bounded LRU) genuinely eliminates re-execution: warm `_materialize_eager_outputs` = 0 and `_cache.try_get` = 0.01ms. The <10ms promise is *achievable* once correlation is vectorised.
- Fingerprint base is cached per `PipelineGraph` instance (`graph_fingerprint` = 0.004ms warm without a preamble); the `GraphFingerprintMemo` and stat-gated file memo are well-designed — the gap is only that the route path bypasses them for the preamble.
- `_build_value_match_expr` is already dtype-robust and tolerance-plumbed, so the FR-03/FR-08 vectorisation can reuse it with no new predicate logic.
- `estimated_size()`-based cache accounting is O(1) and cheap; enrichment does zero per-click file I/O (verified).
- Frontend node/edge rendering is well-memoised (`useMemo`/projection caches); the only frontend gap is trace-result caching.

## Coverage note
All HIGH/MEDIUM latency findings carry a number from my own runs (`bench_correlation.py`, `bench_e2e.py`, `bench_route.py`, `bench_payload_mem.py`, `bench_coldkey.py` in the scratchpad; representative shapes 1000×20 and 5000×50, linear + diamond/edge-join/sort graphs backed by parquet, warm=cache-hit re-click medians over 30 iterations). I could not measure a real model-scoring cold click (no artifact available) — the ~1.7s figure is the README's; my cold numbers isolate correlation/materialization on toy frames. Frontend findings (PERF-05 consumption, PERF-11) are static analysis only (no browser). The supersession finding (PERF-08) is code-confirmed, not load-tested. I did not exercise the multi-frame/port_label preview path beyond the key-drift static diff.
