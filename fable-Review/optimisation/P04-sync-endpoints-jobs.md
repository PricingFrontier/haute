# P04 — Heavy solver work runs synchronously in request threads with no job, no cancel, no gate

**Severity:** HIGH (availability) · **Effort:** L · **Silent-wrongness:** no

Cross-reference: P05 provides the cancellation machinery the backgrounded frontier needs;
design them together.

## FO-08 — [HIGH] `/frontier`, `/apply`, `/save`, `/mlflow/log`, `/frontier/select` do solver/parquet work inline
`routes/optimiser.py:1339-1340` (`run_frontier`), `:1263` (`apply_lambdas`), `:1583`
(`save_result`), `:1652` (`mlflow_log`), `:1444` (`select_frontier_point`)

### Evidence
```python
@router.post("/frontier", response_model=OptimiserFrontierResponse)
def run_frontier(body: OptimiserFrontierRequest) -> OptimiserFrontierResponse:   # sync def → anyio threadpool
    ...
    enforce_frontier_compute_budget(n_points_per_dim=body.n_points_per_dim, n_constraints=len(ranges))
    frontier_result = _compute_frontier(solver, quote_grid, ..., n_points_per_dim=body.n_points_per_dim, ...)
```
The budget gate (`_optimiser_limits.py:27`) admits up to **10,000 grid points — each a full
solve over the quote grid — executed sequentially inside one FastAPI worker thread**. `/apply`
runs `apply_from_grid` + a full parquet write/read inline (`optimiser.py:1044-1052` via
`_materialise_frontier_point_apply`); `/save` and `/mlflow/log` for a ratebook-selected
frontier point call `solver.solve()` inline via `_solve_result_for_selected_frontier_point` →
`_materialise_ratebook_frontier_point` (`optimiser.py:846-910`); `/frontier/select` with
`include_ratebook_tables` likewise (`:1478-1486`). None registers a pollable job; none checks
`_check_no_concurrent_jobs` (`_optimiser_service.py:4129-4135` guards only solve starts; the
`_NON_BLOCKING_RUNNING_JOB_TYPES` set at `:174-179` excludes only estimate/auto-range).

This is the exact anti-pattern the solve path documents against
(`_optimiser_service.py:2549-2551`): *"Expensive data work must be attached to a pollable job…
Otherwise large local runs can outlive the browser request and surface as an unhelpful aborted
signal in the GUI."*

### Impact
- One `/frontier` request can pin a worker thread for minutes-to-hours; the browser aborts and
  retries while the server keeps computing, with no way to cancel and no progress reporting.
- N concurrent `/frontier`/`/apply` calls each pin a thread from anyio's default pool
  (default cap ~40): heavy requests can starve **every** endpoint, including status polling.
- Concurrent frontier recomputes double-run the apply-handle invalidation
  (`optimiser.py:1406-1434`), racing artifact cleanup.

### Fix design
Phase the work; each phase lands independently:
1. **Gate** (S): make heavy endpoints mutually exclusive with running solves and each other via
   the existing single-flight/registry machinery (`SingleFlightCoordinator`,
   `CancellableJobRegistry` in `routes/_background_jobs.py`) keyed on the job id. Return 409
   with an actionable message when busy. This kills the thread-pile-up without changing the
   response contract.
2. **Background** (M): route `/frontier` through the solve-style background-job pattern —
   `POST /frontier` returns `{status: "started", job_id}`, progress via the existing status
   endpoint, cancel via the existing cancel endpoint. The frontend already polls this pattern
   for solve. Ratebook point materialisation used by select/save/mlflow can stay synchronous
   *after* step 1 (it is one CD solve, not a sweep), or reuse the same pattern if profiling
   says otherwise.
3. `/apply`'s parquet cost shrinks separately via FO-17 (P07 — scan+head instead of full read).

## FO-09 — [MEDIUM] The synchronous `/frontier/auto-range` endpoint discards its own timeout
`routes/_optimiser_service.py:3444` (`del node, mode, timeout`), `:2908-2956`
(`estimate_frontier_auto_range`), `:4003-4009` (async path arms it)

### Evidence
`_run_frontier_auto_range_job` receives the configured `timeout` and immediately
`del`etes it (`:3444`). Only the async `/frontier/auto-range/start` path stores
`start_time`/`timeout` on the job (`_launch_frontier_auto_range_background`, `:4003-4009`), and
even there it is enforced only when a client polls the status endpoint (`:3056-3071`). The
plain synchronous `POST /frontier/auto-range` (used by `estimate_frontier_auto_range`,
`:2908-2956`, which also runs under `finally: delete_job`) never applies
`_DEFAULT_AUTO_RANGE_TIMEOUT` (1800 s) at all. The sync path also skips the
`_graph_node_setup_singleflight` dedupe the async path uses — concurrent same-node estimates
neither coalesce nor supersede.

### Fix design
Derive a deadline (`time.monotonic() + timeout`) in `_prepare_frontier_auto_range` and check it
inside the existing `check_cancelled` hook that `_estimate_scenario_frontier_ranges` and
`accumulator.finish` already call between batches/buckets — raise the same timed-out error the
async path produces. Stop deleting `timeout`. Register the sync run in the same single-flight
group as the async path.

## TDD plan (failing tests first)
1. `tests/test_optimiser_routes.py::test_run_frontier_gated_against_concurrent_solve` — start a
   blocking solve (stub solver on an Event); POST `/frontier` for a completed prior job; assert
   409 (or a started job id under phase 2), not inline execution.
2. `tests/test_optimiser_routes.py::test_concurrent_frontier_requests_serialise` — two
   overlapping `/frontier` calls on the same job; assert the second 409s (phase 1) or both
   complete via the job store without concurrent `_compute_frontier` entry (spy on call
   overlap).
3. Phase 2: `tests/test_optimiser_routes.py::test_frontier_backgrounded_and_cancellable` — stub
   sweep that checks the injected cancel hook; start `/frontier`, cancel, assert terminal
   status `cancelled` and no result mutation.
4. `tests/test_optimiser_routes.py::test_sync_auto_range_enforces_timeout` — monkeypatch
   `_estimate_scenario_frontier_ranges` to block past a 1 s `auto_range_timeout`; assert the
   sync endpoint raises the timed-out error rather than running unbounded.

### Acceptance
- No endpoint can run an unbounded solver sweep in a request thread.
- Heavy optimiser work is serialised against solves (and itself) with actionable 409s or jobs.
- Sync auto-range honours `auto_range_timeout`/default 1800 s.
- Existing endpoint contract tests (`test_optimiser_contracts.py`, ui_contract fixtures) pass
  unchanged for phase 1; phase 2 updates the frontier fixtures deliberately.
