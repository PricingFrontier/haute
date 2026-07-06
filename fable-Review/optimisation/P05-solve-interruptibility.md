# P05 — Timeout/cancel cannot reach a running solve; admission slot held for the runaway's duration

**Severity:** MEDIUM (robustness; advertised behaviour not enforced) · **Effort:** M–L

Cross-reference: P04 (backgrounding `/frontier`) and P06 (frontier sweep sizing) shrink the
windows this package hardens.

## FO-10 — [MEDIUM] `solver.solve()` is uninterruptible and holds its admission slot until it returns
`routes/_optimiser_service.py:2336-2347` (online), `:2475-2477` (ratebook); release at `:4983`

### Evidence
```python
# _solve_online — cancellation only checked around, never during, the call
if check_cancelled is not None:
    check_cancelled()
solve_result: OnlineSolveResultLike = solver.solve(quote_grid)   # synchronous Rust, no deadline hook
if check_cancelled is not None:
    check_cancelled()
```
`price_contour.OnlineOptimiser.solve` (`solver.py:80`) exposes no cancellation callback or
deadline parameter. `timeout_solve` (`:3106`) and `cancel_solve` (`:3087`) set the cooperative
token and transition the job — then **release the registry slots** (`:3114-3125`) — but the
worker thread keeps executing the Rust solve. Its `execution_context.release_admission()` runs
only in the worker's `finally` (`:4983`), i.e. after the uninterruptible call returns.

### Impact
- The `timeout` config field advertises a bound the engine cannot enforce on actual compute; a
  pathological solve burns CPU to completion regardless of timeout/cancel.
- Because the timed-out job's registry slot is released while admission is still held, the next
  solve may be **denied admission as `memory_limited`** with a confusing message (nothing looks
  running), or — with `_check_no_concurrent_jobs` seeing no running solve job — start while the
  zombie still computes, contending CPU.
- Recovery from a truly hung solve is process restart.

### Fix design
Preferred: run the solver body through the repo's existing isolated-worker facility —
`IsolatedJobSupervisor` in `routes/_background_jobs.py:173` (killable child process, typed
outcome → `JobLifecycle` mapping already implemented; the CLAUDE.md "share existing
functionality" rule points here). `timeout_solve`/`cancel_solve` then terminate the child and
release admission deterministically. QuoteGrid must transit via the already-sunk parquet path
(`_build_grid` already builds from parquet — pass the path to the child instead of the object).
If the process hop is deemed too large a change in one package: (a) release the admission slot
in `timeout_solve`/`cancel_solve` (accepting temporary over-admission by the zombie, and
documenting it), and (b) make the status/timeout messages state that the abandoned computation
continues until it finishes.

Upstream note (see UPSTREAM-price-contour.md): the clean long-term fix is a
`should_stop: Callable[[], bool]` / deadline parameter on `solve()`/`frontier()` checked
between dual-ascent iterations — worth filing regardless of which Haute-side fix lands.

## FO-11 — [MEDIUM] The solve-time frontier sweep runs after "solving" with no cancellation at all
`routes/_optimiser_service.py:2184-2222` (`_finalize_solve_result`), `_compute_frontier` `:1323-1357`

### Evidence
`_finalize_solve_result` takes no `check_cancelled` and passes none to `_compute_frontier`;
with `frontier_enabled` the sweep is `frontier_steps ** n_constraints` full re-solves (capped
at the library's 10,000) run sequentially **after** the primary solve, while the job still
reads `progress: 0.8, "Computing efficient frontier"`. The only guard is the single
`expected_status="running"` progress update at `:2196` before the sweep starts. A cancel or
timeout landing during the sweep is ignored until every point is solved.

### Fix design
Thread the existing `check_cancelled` callback into `_finalize_solve_result` and check it
between frontier points. The engine's Python sweeps call back per point via `solve_one`
(`_frontier_helpers.py:265-269`), so Haute can chunk the sweep (split `threshold_ranges` into
per-point calls or batches with `initial_lambdas` chaining — the same warm-start field it
already passes) and check between chunks; the Rust fast path needs the upstream hook, or accept
chunk-level granularity by sweeping axis slices. Combine with P06's smaller point budgets and
the window shrinks to seconds.

## TDD plan (failing tests first)
1. `tests/test_optimiser_service_coverage.py::test_timeout_releases_admission_for_next_solve` —
   solver stub blocking on a `threading.Event`; poll status past `timeout` (job → `timed_out`);
   assert a second `start()` is admitted (not 409/`memory_limited`) while the first stub is
   still blocked; then set the event and assert the zombie's completion is skipped
   (`expected_status` guard) and artifacts cleaned. Fails today at the admission step.
2. `tests/test_optimiser_frontier_materialisation.py::test_frontier_sweep_aborts_on_cancel` —
   frontier stub invoking the injected cancel hook between points, cancel after point 1; assert
   job ends `cancelled`, not `completed`, and no frontier payload is attached.
3. If the isolated-worker route is taken: a supervisor-level test that `cancel_solve` kills the
   child and the job transitions `cancelled` within a bounded wait.

### Acceptance
- A timed-out/cancelled solve never blocks the next solve's admission.
- Cancel during the frontier tail takes effect between points.
- Status messages never claim a bound that is not enforced.
- Full solve-lifecycle suites (`test_optimiser_service_coverage.py`,
  `test_optimiser_routes_critical_edges.py`) pass.
