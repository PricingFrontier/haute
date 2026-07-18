# T08 — Trace cache keys and budgets: three consistency gaps

**Severity:** MEDIUM ×3 (+1 pending verification) · **Effort:** S–M
**Pairing:** batch-review class except T08.4 (crash path) which needs a dev/reviewer pair if confirmed.
**Files:** `src/haute/trace.py`, `src/haute/executor.py`, `src/haute/_fingerprint_cache.py`
**Origin:** CORE-07, PERF-07, PERF-09, CORE-08 (verification in flight — see status note at bottom)
**Repros:** `repros/bench_coldkey.py` (PERF-07 key-drift proof)

## T08.1 (CORE-07) — trace cache key omits the contracts flag

The trace fingerprint is `graph_fingerprint(graph, target, f"{row_limit}:{source}", *extras)`
(`trace.py:381-387`) while the preview base key — including the copy the trace itself reconstructs
at `trace.py:412` — carries `:contracts={int(ENFORCE_CONTRACTS)}` (`executor.py:934`). Visible in
the repro logs: trace extras `('t', '1000:live', 'runtime_files=…')` vs preview
`('1000:live:contracts=1', …)`. If enforcement ever flips between requests (today: tests,
env-driven setups; tomorrow: request-scoped), a cache hit serves frames that skipped the
`ContractMismatchError` check. Defense-in-depth + consistency fix.

**Fix:** append `:contracts={int(ENFORCE_CONTRACTS)}` to the trace key segment, exactly mirroring
the executor. **Failing test:** store a trace with enforcement off for a contract-violating graph;
flip on; assert the second call misses the cache and raises.

## T08.2 (PERF-07) — preview→trace reuse is inert for the real GUI flow; decide its fate

Empirically proven key drift (`repros/bench_coldkey.py`): the GUI previews target-only without
explicit columns → preview stores under `…:preview_target_only=…:initial_col_limit=N`; the trace
reconstructs either the `preview_cols=(…)` suffix (when `row_values` present) or the unsuffixed
key — **neither matches**, and even on a hypothetical hit a target-only preview lacks the ancestor
frames the trace needs (`trace.py:719` guard). Net: the reuse machinery + its key-reconstruction
complexity (~30 lines) fires only for full non-target-only previews, which the GUI does not issue
from the preview panel. Not the feared 2× duplicate execution — the ancestors were never
materialised by the preview — but the target node *is* re-executed and the code advertises an
optimization it doesn't deliver.

**Fix (choose one, explicitly):**
- (a) Make it real: when the preview materialises a full ancestor chain (non-target-only), *also*
  store under the base unsuffixed key (one-line addition in `executor.py`'s store site); teach the
  GUI's first trace click to tolerate the `initial_col_limit` suffix by having the *preview* store
  the suffix-free alias — the trace side stays as-is. Reuse then fires for the "user previewed the
  full node then clicked" flow.
- (b) Delete the projected-suffix reconstruction (`trace.py:413-430`) and keep only the unsuffixed
  fallback, documenting that the first click is a full cold run (matches README wording anyway).
Recommendation: (a) — it is small and makes the first-click promise cheaper for the most common
"preview then trace the same node" path. Either way, add the missing test.
**Failing test:** for the target-only+`row_values` GUI flow, assert
`set(trace preview_fps) ∩ {stored preview fps} != ∅` (currently empty — this pins whichever option
is chosen; for (b), assert the reconstruction code is gone and cold path is taken deliberately).

## T08.3 (PERF-09) — double-counted budgets for shared frames

Trace and preview caches default to the **same** byte budget (`TRACE_CACHE_MAX_BYTES =
PREVIEW_CACHE_MAX_BYTES`, `trace.py:229-247`) with independent accounting. Shared-reference entries
(preview-reuse path) are counted twice → premature eviction (conservative but wasteful); distinct
entries (cold trace) → true peak retention ≈ 2× one budget (surprising operator maths on
memory-constrained laptops — the audience the README's "knows your machine's limits" section
targets). The backend-core review confirmed there is **no** 2× resident blow-up for shared refs and
no corruption — this is an accounting/architecture cleanup, not a leak.

**Fix:** either share one `FingerprintCache` byte-pool across both caches (a `shared_budget`
parameter summing retained bytes), or identity-track frames (`id(df)`) so a frame counted once is
not re-counted. Document the chosen invariant in both module headers. **Failing test:** store the
same frame dict via both caches; combined accounted bytes ≈ 1× the frame size.

## T08.4 (CORE-08) — multi-frame trace target: **CONFIRMED, escalated to HIGH → moved to T06**

Adversarial verification confirmed the crash and broadened it: tracing **any node downstream** of a
multi-frame apiInput 500s (dict-valued ancestor at `_trace_correlation.py:741`), with a
preview-succeeds/trace-crashes asymmetry. Full verified evidence and the corrected per-port-routing
fix design live in `T06-multiframe-trace-support.md`; implement it there, not here.

## Acceptance for the package

- Trace and preview key construction share every segment (contracts, projection, runtime extras) —
  add a unit test that builds both keys from one graph and diffs the segments (this is the
  regression net for *future* drift, the root cause behind T08.1/T08.2).
- Reuse decision implemented + tested per the chosen option.
- One documented budget model; `store` paths green under `tests/test_trace_cache_byte_awareness.py`.
