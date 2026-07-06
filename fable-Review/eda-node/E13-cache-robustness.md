# E13 — Cache lifetime & job-path robustness

**Severity:** MEDIUM (bounded costs, edge paths) · **Effort:** M · **Review:** batch
Files: `src/haute/routes/_explore_service.py`, `src/haute/_execute_lazy.py` (one edge path),
`src/haute/execution.py` (only if report persistence lands)
Tests: `tests/test_explore_routes.py`, `tests/test_fingerprint_cache.py`

## EF-23 [MEDIUM] — the report cache is in-memory only; a restart forces full re-materialisation

**Evidence:** the report cache is `LRUCache(max_size=16)` in-process (`_explore_service.py:62`,
`:574`). The dataframe cache from `default_dataframe_execution_cache()` also dies with the process
— it lives in `tempfile.mkdtemp` with an `atexit` rmtree AND its key→entry index is in-memory
(`execution.py:137-144`), so there is no "parquet survives restart" asymmetry to exploit; after a
restart both are cold and the pipeline re-runs.

**Assessment & fix:** persisting *reports* is cheap and correct-by-construction: `report_cache_key`
already encodes the upstream lineage fingerprint + runtime-input fingerprint + schema version
(`:676-690`), so a persisted report is valid exactly when data/graph/config are unchanged. Store
report JSON under the project's existing cache directory conventions (keyed by
`report_cache_key`), read-through on `start()`. This makes a server restart a **stats-free,
materialise-free** hit for unchanged pipelines. Keep the in-memory LRU in front. Also raise
`EXPLORE_REPORT_CACHE_MAX_ENTRIES` 16 → 64 (reports are small; analysts flip between many
node/source combinations — 16 slots thrash across a 10-node pipeline × sources).
E04 must land first (the key derivation this validity argument leans on becomes cheap).

## EF-24 [MEDIUM] — the too-large-artifact skip path executes the upstream pipeline twice

**Evidence:** when `HAUTE_DATAFRAME_EXECUTION_CACHE_MAX_BYTES` is set and the frame exceeds it,
`store_artifact` raises `CacheArtifactTooLargeError` **after** `bounded_sink` already wrote the
parquet (`_dataframe_execution_cache.py:472-486`); `_execute_lazy` catches it and leaves the
node's lazyframe as the original upstream plan (`_execute_lazy.py:1275-1280`). `_build_frame_stats`
then re-executes the entire upstream lineage: sink cost paid, artifact deleted, plan run again.
Default `max_bytes=None` — the path only fires when the env cap is configured.

**Fix:** on the too-large branch, let the explore path consume the just-written parquet once
before unlinking (hand back a scan with a delete-on-close finalizer via the existing pin/finalizer
machinery), or at minimum scan-before-unlink within the same call. Keep the cache *store* rejected
(the budget is respected); only the wasted second execution goes away. Fail loud if the temp
artifact vanished.

## EF-25 [LOW] — sized-but-acceptable items (fix opportunistically, don't build infrastructure)

- **a. Poll-path eviction scan under the global write lock:** every `status()` →
  `require_job` → `_evict_stale()` under `_write_lock` (`_job_store.py:360-367`, `:92-104`),
  O(jobs) at 500 ms polling cadence. Acceptable for a single-server dev tool; if touched, gate
  eviction to ≥1/s.
- **b. Thread-per-job:** unbounded `threading.Thread` per `start()` (`:607-613`), but
  memory-admission backpressure gates real concurrency (CLEARED.md). A worker pool is tidier, not
  required.
- **c. Progress dead zone 0.1 → 0.85:** resolved by E03's per-batch progress; listed here so it
  isn't double-fixed.

## TDD plan (failing tests first)

1. `test_report_cache_survives_restart` — build a report, construct a fresh `ExploreService` (new
   store/LRU, same persistence dir), `start()` the same spec: response is `completed/cached`
   without any `execute_lazy_graph` call (monkeypatch-count). **Fails today.**
2. `test_persisted_report_invalidated_by_input_change` — touch the source file (mtime/size roll):
   persisted report missed, job runs. Guards against serving stale persisted reports.
3. `test_persisted_report_version_gate` — bump `EXPLORE_CACHE_VERSION`: old persisted payloads are
   ignored (schema-incompat guard, mirrors the in-memory key versioning).
4. `test_too_large_artifact_single_execution` — tiny byte cap; count upstream source-builder
   invocations: exactly one. **Fails today** (two).
5. LRU sizing: constant change only — existing eviction tests cover behaviour.
