# E04 — `start()` content-hashes every upstream source file on every request, even warm hits

**Severity:** HIGH (request-thread latency) · **Effort:** S · **Review:** dev/reviewer pair (cache identity)
Files: `src/haute/execution.py` (one call site) · Tests: `tests/test_explore_routes.py`,
`tests/test_fingerprint_cache.py`

## EF-11 [HIGH]

### Current behaviour (verified at af3eb2ea)

- `ExploreService._prepare_spec` calls `dataframe_graph_input_fingerprint(...)` synchronously
  (`_explore_service.py:659-663`) **before** the report-cache check (`:577-585`), on the FastAPI
  route thread (`run_explore` is sync, `explore.py:19-25`).
- That fingerprint builds `_runtime_input_fingerprint_entry` per file-backed node, which calls the
  **non-gated** `_runtime_path_fingerprint` (`execution.py:465` → `:297-321`) — a full chunked
  content read of every upstream source file on every call.
- The stat-gated memo built for exactly this problem exists 30 lines away:
  `_stat_gated_runtime_path_fingerprint` (`execution.py:331-366`), used by preview/trace. Its
  docstring states the rationale verbatim: "content-hashing every file-backed input per preview
  would scale request cost with data size instead of edit rate."

### Impact

- Every Explore run blocks the request thread for a full read of all upstream sources first.
- Because `report_cache_key` derives from this fingerprint (`_explore_service.py:676-690`), even a
  **report-cache hit** pays the full read and then returns "cached" — a warm Explore on a large
  source (the codebase documents a 38 GB input case at `_execute_lazy.py:696`) appears to hang for
  tens of seconds, then claims it was instant.
- Cold runs pay this read *in addition to* the materialise read.

### Fix design

Route `_runtime_input_fingerprint_entry`'s file fingerprinting through
`_stat_gated_runtime_path_fingerprint` (swap the callee at `execution.py:465`). That accepts the
same documented `(mtime_ns, size)` gate trade preview/trace already accept — a byte-rewrite
preserving both size and mtime_ns is below the gate's resolution. Notes:

- `dataframe_graph_input_fingerprint` is shared (grep call sites before changing: the explore
  service, and any deploy/batch callers). The stat-gate is a strict superset of correctness for
  *interactive* callers; if a deployment-side caller requires content-exactness regardless of cost,
  add a `stat_gated: bool = True` parameter defaulted True and pass False there — do NOT fork the
  function.
- The memo is process-wide and keyed by resolved path; no extra invalidation work needed (documented
  double-stat race guard already in place).
- Missing paths/directories were never memoised and stay loud on OS errors — preserve that.

### TDD plan (failing tests first)

1. `test_explore_start_does_not_rehash_unchanged_sources` — monkeypatch
   `haute._hashing.content_hash` (or the module-local import) to count calls; two consecutive
   `start()` calls on a graph with an unchanged file-backed source: second call performs **zero**
   content hashes. **Fails today** (hashes every time).
2. `test_explore_fingerprint_rolls_on_metadata_change` — rewrite the source file (new mtime/size):
   `report_cache_key` changes and the report cache misses (exactness preserved at the gate's
   resolution).
3. `test_report_cache_hit_is_fast_path` — with the memo warm, assert `start()` on a cached spec
   performs no file reads (stat only) — structural assertion via the same call counter.
4. Existing `tests/test_fingerprint_cache.py` memo tests cover the gate's race guard — extend only
   if the parameter is added.
