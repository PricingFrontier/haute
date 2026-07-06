# P06 — Frontier sizing: uniform 15 steps/dim explodes cost and guarantees failure at ≥4 constraints; Rust parallelism never enabled

**Severity:** HIGH (guaranteed feature failure + dominant latency) · **Effort:** M

The frontier cost is `n_points_per_dim ** n_swept_constraints` full solves (verified:
`price_contour/_frontier_helpers.py:246-258`, `ratebook.py:829-839`, Rust fast path per
`solver.py:308-318`).

## FO-12 — [HIGH] Haute passes a flat 15 points/dim to every frontier, both modes
`routes/_optimiser_service.py:2193` + `:2213-2222` (solve-time), `routes/optimiser.py:1390`
(`/frontier` uses `body.n_points_per_dim`, schema default also 15 — check `schemas.py`)

### Evidence
```python
# _finalize_solve_result (:2193)
frontier_steps = config.get("frontier_steps", 15)
...
frontier_result = _compute_frontier(solver, quote_grid, mode=mode, ...,
    n_points_per_dim=frontier_steps, initial_lambdas=solve_result.lambdas)
```
Engine defaults are 10 (online, `solver.py:212`) and **5** (ratebook, `ratebook.py:696` —
deliberately lower, each point being a full coordinate-descent pass). Consequences of the flat
15:
- ratebook, 2 constraints → 225 sequential CD passes; 3 constraints → 3,375. Each CD pass is
  itself `max_cd_iterations × n_factors` grouped solves.
- **4+ constraints → 15⁴ = 50,625 > max_total_points=10,000 → the library raises → Haute's
  catch-all at `:2232-2239` records `frontier_error = "Frontier unavailable: …"`** — the
  solve-time frontier can never succeed for 4 or more constraints at the default setting.
- The `/frontier` endpoint's budget gate (`enforce_frontier_compute_budget`,
  `_optimiser_limits.py:30`) rejects oversized *requests* with an actionable 422, but the
  **solve-time** path (`_finalize_solve_result`) never calls it — it burns the failure inside
  the library instead.

### Fix design
1. Derive per-dim points from the swept-constraint count against a target total, e.g.
   `points_per_dim = max(2, min(configured, floor(TARGET_TOTAL ** (1 / n_swept))))` with
   `TARGET_TOTAL` ≈ 225–500 (and a lower ratebook target honouring the library's 5/dim
   default). Record the effective value in the result payload so the frontend can display the
   actual resolution.
2. Call `enforce_frontier_compute_budget` in `_finalize_solve_result` before invoking
   `_compute_frontier`, so an over-budget solve-time sweep degrades to a *named, actionable*
   `frontier_error` (or an automatically reduced grid) instead of an opaque library error.
3. Keep user-explicit `frontier_steps` for 1–2 constraints; the cap only bites where the
   exponent does.

## FO-13 — [MEDIUM] The Rust online frontier is never parallelised
`routes/_optimiser_service.py:1351-1357` (`_compute_frontier` online kwargs)

### Evidence
```python
frontier_kwargs = {"threshold_ranges": threshold_ranges, "n_points_per_dim": n_points_per_dim}
if initial_lambdas is not None:
    frontier_kwargs["initial_lambdas"] = initial_lambdas
return solver.frontier(quote_grid, **frontier_kwargs)      # parallel defaults to False
```
`OnlineOptimiser.frontier` supports `parallel: bool` (`solver.py:215`) on the Rust
`sweep_frontier_py` fast path (`:308-318`) — the exact path Haute takes (pre-built grid,
sum-only constraints, all axes swept). 225–3,375 points run on one core.

### Fix design
Pass `parallel=True` for `mode != "ratebook"` in `_compute_frontier`. Verify first (small
benchmark + read of `_price_contour.pyi`) that parallel mode preserves warm-start determinism
or that the point results are order-independent; if results differ from sequential, pin the
expectation in the golden fixtures deliberately. No effect on ratebook (its Python frontier
ignores `parallel` — upstream note filed).

## TDD plan (failing tests first)
1. `tests/test_optimiser_routes.py::test_solve_time_frontier_respects_compute_budget` — config
   with 4 constraints, `frontier_enabled=True`, `frontier_steps=15`; stub solver; assert
   `_compute_frontier` is invoked with a reduced per-dim count (≤ budget) — not that
   `frontier_error` appears. Fails today (library raise → `frontier_error`).
2. `tests/test_optimiser_routes.py::test_frontier_effective_resolution_reported` — the result
   payload carries the effective points/dim used.
3. `tests/test_optimiser_routes_real_library.py::test_online_frontier_passes_parallel` — spy on
   `solver.frontier` kwargs; assert `parallel=True` for online, absent for ratebook.
4. Golden parity: run the real-library online frontier sequential vs parallel on the small
   golden fixture; assert point sets match within tolerance (pins the safety of FO-13).

### Acceptance
- Solve-time frontier succeeds (at reduced resolution) for any constraint count, or fails with
  a named budget message — never an opaque library error.
- Ratebook frontier cost at defaults drops to the library-intended envelope.
- Online frontier uses all cores; golden outputs unchanged within tolerance.
- `test_optimiser_golden.py`, ui-contract fixtures updated only where resolution metadata was
  added.
