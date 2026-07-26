# WS-08 — Optimiser & explore/EDA backend

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: unassigned · Status: not started.

**Branch:** `opus5/ws-08-optimiser-explore`

## Mission

The price-optimiser service (solve, frontier sweeps, ratebook apply, online trace) and the
Explore/EDA statistics service. The optimiser is the review's worst over-complication hot
spot: a 5,336-line god module with four overlapping concurrency structures and a
timeout-that-does-not-cancel. This stream owns the concurrency fixes and a documented carve
plan; the carve itself is staged over follow-up PRs rather than forced into one.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| optimiser | 0 | 1 | 7 | 8 |
| explore-eda | 0 | 0 | 5 | 4 |
| **Total** | **0** | **1** | **12** | **12** |

## Priorities

**P1 — races and uncancellable work (review Wave 2):**

- `optimiser-2` (H): a timed-out frontier sweep is never cancelled and still mutates the
  parent solve job minutes after the caller was told it failed — republishing frontier data
  and destroying materialised apply artifacts. Thread the `BackgroundJobStoppedError` signal
  into the sweep, guard the parent-job update on the frontier job still being `running`, and
  add `/frontier/cancel/{job_id}`.
- `optimiser-1` (M): frontier single-flight is a non-atomic, status-only check — concurrent
  sweeps overwrite the same parent job. Close it under a lock (the service already has
  `_start_lock` as the correct pattern).
- `optimiser-8` (L) / `optimiser-13` (L): read-modify-write of `artifact_handles` loses a
  concurrent handle and orphans its parquet; per-frontier-point apply artifacts accumulate
  with no cap.
- `explore-eda-1` (M): Cancel does not interrupt the statistics collect although the spec
  says it does — add a cancellation checkpoint on the collect path (coordinate with WS-02 if
  the change lands in `_polars_utils.py`).

**P2 — bugs and correctness:** `optimiser-4` (online trace prefers a literal `"quote_id"`
key over the artifact-configured column and can explain the wrong quote), `optimiser-6`
(artifact-load failures discard the cause with no logging), `optimiser-3` (frontier compute
budget enforced on only one of two entry points), `optimiser-5` (ratebook trace returns
status "ok" with no reconciliation when the factor ladder is empty), `explore-eda-4`
(documented 400 is actually 404), `explore-eda-3` (undocumented contract-error branch),
`explore-eda-5` / `explore-eda-6` (false `values_truncated` hints; binary lossy decode breaks
the exact group-count reconstruction).

**P2 — over-complication (staged):**

- `over-complication-4` (M): `OptimiserSolveService` runs three `CancellableJobRegistry`
  instances plus a `SingleFlightCoordinator` keyed identically to one of them, released by
  hand at eight sites — consolidate to one registry plus one coordinator with scoped release.
- `over-complication-5` (M): 5,336-line `_optimiser_service.py` with eight unrelated
  responsibilities and two provably dead helpers. Delete the dead helpers now; write the
  carve plan and land it as follow-up packages.
- `optimiser-9` (L): `_solve_online` takes loose positional args while `_solve_ratebook`
  takes `SolveContext`.

**P3 — spec truth:** fold the shipped 0.7.0 contract and fix the pre-rename
`_resolve_data_source` in present-tense Control flow (`contracts-d-3`, `optimiser-7`),
ownership of the OPTIMISER_APPLY runtime in `_builders.py` (`optimiser-12` — coordinate the
decision with WS-06), stale roadmap and test-helper claims (`optimiser-14`, `optimiser-11`),
explore testing/coverage and module-behaviour claims (`explore-eda-2`, `explore-eda-9`,
`explore-eda-7`, `explore-eda-12`). Apply WS-03's chosen env-knob policy at the optimiser
call site named in `failure-model-6` (`routes/optimiser.py:1257`).

## Finding inventory

High (1): `optimiser-2`.
Medium (12): `contracts-d-3`, `optimiser-1`, `optimiser-4`, `optimiser-6`, `optimiser-7`,
`over-complication-4`, `over-complication-5`, `explore-eda-1`, `explore-eda-2`,
`explore-eda-3`, `explore-eda-4`, `explore-eda-9`.
Low (12): `optimiser-3`, `optimiser-5`, `optimiser-8`, `optimiser-9`, `optimiser-11`,
`optimiser-12`, `optimiser-13`, `optimiser-14`, `explore-eda-5`, `explore-eda-6`,
`explore-eda-7`, `explore-eda-12`.

## File ownership (exclusive)

- `src/haute/routes/optimiser.py`, `routes/_optimiser_service.py`,
  `src/haute/_optimiser_apply_explainability.py`, and optimiser helper modules
- `src/haute/routes/_explore_service.py` and explore helpers
- `docs/specs/optimiser/**`, `docs/specs/explore-eda/**`
- Their tests (`tests/test_optimiser_routes.py`, optimiser service/apply suites,
  `tests/test_explore_routes.py`, `tests/optimiser_fixtures`)

## Cross-stream touchpoints

- `_builders.py` (WS-06 owns the ownership decision): the whole OPTIMISER_APPLY runtime lives
  there — settle the documented owner with WS-06; no code overlap expected.
- `_polars_utils.py` (WS-02): coordinate if `explore-eda-1` needs a checkpoint there.
- `_job_store.py` / `_job_lifecycle.py` (WS-03): frontier CAS uses the shared store — fix
  call sites here, send lifecycle changes to WS-03.
- `_env.py` policy (WS-03): apply at the optimiser call site once WS-03 decides.
- Optimiser UI findings are WS-10's and reference this backend's `ValueError` contracts —
  keep messages and shapes stable, or tell WS-10 what changed.

## Definition of done

- Frontier timeout cancels the sweep and cannot mutate a terminal parent job; single-flight
  admission is atomic; a cancel route exists — each with a regression test.
- Dead optimiser helpers removed, concurrency structures consolidated, and a written carve
  plan for `_optimiser_service.py` exists with roadmap package IDs in WS-01's format.
- Explore cancel actually interrupts the collect; explore specs match the code.
- Baseline entries for both components deleted; every finding fixed or deferred with a
  written reason.

## Verification

- `uv run pytest tests/test_optimiser_routes.py tests/test_explore_routes.py -q`
- Targeted frontier-timeout and single-flight concurrency regressions.
- `uv run pytest tests/test_docs_accuracy.py -q`; quick preflight near completion.
