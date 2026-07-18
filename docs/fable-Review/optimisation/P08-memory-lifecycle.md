# P08 — Memory/disk lifecycle: restart-orphaned artifacts and unbounded heavy-set retention

**Severity:** MEDIUM · **Effort:** M

## FO-19 — [MEDIUM] Artifact parquet dirs leak permanently on crash/restart
`routes/_optimiser_service.py:959-964` (roots under `tempfile.gettempdir()`),
`server.py:334-356` (lifespan has no sweep)

### Evidence
```python
def _apply_artifact_root() -> Path:
    return (Path(tempfile.gettempdir()) / _APPLY_ARTIFACT_ROOT_NAME).resolve()
    # %TEMP%/haute/optimiser_apply ; factors root analogous
```
Cleanup is exclusively in-process: artifact cleaners fire on job TTL eviction/deletion
(`_job_store.py:113-150`). The FastAPI lifespan (`server.py:334-356`) clears bytecache,
configures logging, loads env, ensures the pipeline index, starts the watcher — **no artifact
sweep, no atexit hook**. Verified: no `iterdir`/glob/sweep of either root anywhere in `src/`.

### Impact
Every crash, kill, redeploy, or OOM strands `apply_*/result.parquet` and
`factors_*/factors.parquet` dirs whose owning job store died with the process. Apply results
are full per-quote frames — potentially hundreds of MB each — and Windows never cleans `%TEMP%`
automatically. Unbounded disk growth on any long-lived dev box.

### Fix design
On lifespan startup, sweep both roots and delete every `apply_*` / `factors_*` subdir: no live
process can legitimately own a pre-boot artifact (handles exist only in the in-memory store).
Log a single summary line (count + bytes reclaimed). Guard with the same handle-shape
validation used by `_validate_server_owned_parquet_handle` roots so the sweep can never leave
the roots. A shutdown sweep is optional; startup covers the crash case, which shutdown cannot.

## FO-20 — [MEDIUM] N solves in 15 minutes pin N full heavy sets; admission never sees them
`routes/_optimiser_service.py:2253-2268` (completion fields), `_job_store.py:29-31`
(retention keys/TTL), `:4983` (admission released at worker exit)

### Evidence
```python
completion_fields = {..., "solver": solver, "solve_result": solve_result,
                     "quote_grid": quote_grid, ...}                     # per completed job
_DEFAULT_HEAVY_OBJECT_TTL_SECONDS = 15 * 60
```
Each completed solve gets a fresh job id and retains `quote_grid` + `solver` (+ ratebook
contexts) for 15 minutes, extendable by every access (`touch_heavy_objects`,
`_job_store.py:299-336`). `register_latest` supersedes only the previous *running* job.
Meanwhile the worker's `finally` released the memory-admission budget at completion — so the
retained grids are invisible to admission. A user iterating on config (the normal workflow)
accumulates one multi-GB grid per solve; peak RSS scales with solves-per-15-min while the
admission gate believes memory is free.

### Fix design
Bound retention by count, not just time: when a solve completes for a `setup_job_key`
(graph-node key already computed at `_optimiser_service.py:3159`), clear heavy objects on any
*older completed* job sharing that key (store the key on the job at creation; add a
`JobStore.clear_heavy_for_matching(predicate, exclude_job_id)` walking under `_write_lock`).
Keep the 15-min TTL as the cross-key backstop. Result: at most one live heavy set per graph
node plus whatever a frontier session pins via `touch_heavy_objects` — the P01 workflow is
unaffected because it operates on the newest job.

## TDD plan (failing tests first)
1. `tests/test_optimiser_routes.py::test_startup_sweeps_orphan_artifacts` — pre-create
   `apply_stale/result.parquet` and `factors_stale/factors.parquet` under the roots; run the
   lifespan startup (TestClient context enter); assert both dirs removed and live-job artifacts
   created *after* boot untouched.
2. `tests/test_job_store.py::test_completed_heavy_sets_bounded_per_key` — complete three
   solves for the same setup key via the store API; assert only the newest job retains
   `quote_grid`/`solver`, the older two are slimmed (metadata intact), and their artifact
   handles were *not* cleaned (artifacts outlive heavy objects by design — status/result
   endpoints still work).
3. Regression: `touch_heavy_objects` on the newest job still extends its window
   (existing tests in `test_job_store.py` cover the mechanics).

### Acceptance
- A restart reclaims all orphaned optimiser artifact directories, logged with totals.
- Peak retained heavy sets per graph node is 1; cross-node retention unchanged.
- Frontier session workflows (P01) and status polling on older jobs keep working.
