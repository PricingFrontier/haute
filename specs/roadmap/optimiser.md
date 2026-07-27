# Optimiser roadmap

## Scope

Optimiser configuration, numerical solve/frontier behaviour, artifacts,
ratebooks, performance, interruptibility, and workflows remain reliable.
Current behaviour is specified in [the optimiser specification](../optimiser/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| AUD-C10 | Active | P0 | Replace origin-blind solver error classification with typed boundary outcomes. |
| OPT-P06 | Active | P1 | Add bounded parallel frontier computation where solver inputs are isolated. |
| OPT-P10 | Active | P2 | Remove verified duplication and dead code. |
| OPT-P11–OPT-P14 | Planned | P2 | Carve the solve-service god module behind preserved contracts. |
| OPT-D01 | Decision | P2 | Choose one safe client-detail policy for setup and artifact-load failures. |

## Planned improvements

### AUD-C10 — Numerical and silent-failure residuals
**Why:** The solve worker's fallback classification is purely exception-type based
(`ValueError` → data/contract error, `RuntimeError` → algorithm error), so an internal
algorithm defect that surfaces as a `ValueError` inside the solver stack is still reported
to the user as a data problem.

**Plan:** Replace the remaining origin-blind `ValueError`/`RuntimeError` fallback
classification with typed boundary errors so terminal categories reflect an error's origin,
not its Python type. The typed public-contract-error layer in front of this fallback is
already delivered; orchestration extraction stays with OPT-P11–OPT-P14.

**Acceptance:** Pathological numerical fixtures return typed terminal outcomes whose
category reflects the error's origin; solve, frontier, save, and apply regressions are
preserved.

**Dependencies:** Delivered validation and artifact contracts (formerly OPT-P01–OPT-P03).

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_contracts.py`; `tests/test_optimiser_service_validation.py`.

### OPT-P06 — Frontier compute scaling
**Why:** Frontier calculation misses safe bounded parallelism.

**Plan:** Introduce bounded parallel frontier work only where solver inputs are isolated. The redundant scale factors named by the original audit are already removed.

**Acceptance:** Numerical equivalence and concurrency-bound tests cover serial and parallel frontier execution.

**Dependencies:** AUD-C10; job admission policy.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes_real_library.py`.

### OPT-P10 — Optimiser hygiene
**Why:** Verified dead code and duplication make numerical paths harder to maintain.

**Plan:** Re-run a dead-code/duplication inventory against `HEAD` to identify concrete candidates, then remove or consolidate only code proved unused or duplicated after core contracts are protected.

**Acceptance:** Each cleanup has focused regression coverage for the affected solve/artifact behaviour.

**Dependencies:** AUD-C10; otherwise delivered P0/P1 packages.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes.py`.

### OPT-D01 — Generic setup error-detail policy

**Why:** The pipeline setup catch-all hides exception text while grid
construction's catch-all sends it to the client. Either distinction may be
intentional, but it is not currently an explicit security or product policy.
Relatedly, `_load_apply_result_artifact`/`_load_ratebook_factors_artifact`
report a missing artifact — including one evicted by TTL after user delay —
with the same 500 status as a corrupt server-owned artifact, though the former
is arguably a client-shaped 400/404 outcome.

**Plan:** Decide which setup failures are user-actionable, define the sanitized
detail vocabulary for all other failures, classify missing-versus-corrupt
artifact loads within the same vocabulary, and record why the rejected policy
would be less safe or less useful.

**Acceptance:** A decision record classifies pipeline, grid, and artifact-load
failures before implementation; route tests then prove stable user-facing
details and server-side diagnostic logging for each branch.

**Dependencies:** Security owns information-disclosure policy.

**Evidence:** `src/haute/routes/_optimiser_service.py`,
`src/haute/routes/optimiser.py`, and `tests/test_optimiser_routes.py`.

### OPT-P11 — Extract owned artifact lifecycle
**Why:** Persistence, handle validation, load diagnostics, orphan cleanup, and startup reaping are
independent of solve orchestration but occupy the same module.

**Plan:** Move the two artifact families and their registered cleaners to
`src/haute/routes/_optimiser_artifacts.py`. Keep the current handle schema and route imports as
compatibility re-exports for one release.

**Acceptance:** Artifact round-trip, tampered-handle, orphan-race, TTL-cleanup, and stale-startup
tests pass unchanged; `_optimiser_service.py` owns no filesystem deletion.

**Dependencies:** Delivered memory lifecycle (formerly OPT-P08).

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_apply_artifacts.py`.

### OPT-P12 — Extract frontier domain service
**Why:** Frontier range normalisation, compute dispatch, payload limiting, job lifecycle, point
selection, and point-artifact retention form a cohesive domain separate from initial solve setup.

**Plan:** Move pure range/point helpers first, then introduce an `OptimiserFrontierService` that
owns explicit sweep admission, cancellation, parent publication, and point-artifact retention.
Replace the current process-global `_frontier_state_lock` with per-parent-job locking, or pin
artifact handles while reads occur so slow parquet reads/deletions can safely move outside the
state lock. Keep FastAPI response assembly in `src/haute/routes/optimiser.py`.

**Acceptance:** Timeout/cancel, single-flight, compute-budget, point-selection, ratebook
materialisation, artifact-cap, and unrelated-parent concurrency regressions remain green at each
extraction step.

**Dependencies:** OPT-P06; delivered frontier apply and interruptibility (formerly OPT-P01,
OPT-P05).

**Evidence:** `src/haute/routes/optimiser.py`; `src/haute/routes/_optimiser_service.py`;
`tests/test_optimiser_frontier_materialisation.py`; `tests/test_optimiser_routes.py`.

### OPT-P13 — Extract input planning and grid construction
**Why:** Projection planning, retained-input resolution, schema/value validation, ratebook-factor
extraction, chunk sizing, and quote-grid construction are one setup pipeline with no need to know
solver result publication.

**Plan:** Move those functions and their small dataclasses to
`src/haute/routes/_optimiser_input.py`, preserving `ExecutionContext` checkpoints and typed
contract errors. `OptimiserSolveService` retains only orchestration calls.

**Acceptance:** Projection, bounded-memory, multi-input, null/non-finite, chunk provenance, and
grid ordering tests pass without fixture rewrites.

**Dependencies:** Delivered constraint validation and scan bounding (formerly OPT-P03, OPT-P07).

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_service_coverage.py`;
`tests/test_optimiser_service_validation.py`.

### OPT-P14 — Extract solver execution and result publication
**Why:** Online/ratebook construction, `SolveContext`, result normalisation, inline frontier
policy, factor-table serialisation, and terminal publication are the final cohesive solver layer.

**Plan:** Move solver-context entry points and result builders to
`src/haute/routes/_optimiser_solver.py`; leave `OptimiserSolveService` as job admission plus
setup/worker composition. Retain the worker-context guard at the extracted public boundary.

**Acceptance:** Online/ratebook solve, cancellation, inline-frontier, golden response, factor
dtype, and save/apply agreement suites pass; `_optimiser_service.py` is an orchestration module
rather than a mixed domain/utilities module.

**Dependencies:** OPT-P11–OPT-P13.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes.py`;
`tests/test_optimiser_golden.py`; `tests/test_optimiser_ratebook_apply_agreement.py`.

## Delivered outcomes

- Frontier multi-point apply without repeated heavy setup, the versioned
  save-artifact contract with sanitized corrupt-artifact failures,
  domain-boundary constraint validation, heavy endpoints behind the job
  protocol, solve interruptibility with exactly-once admission release,
  single-scan setup estimation, trace-apply reuse of stored point summaries,
  and bounded artifact memory lifecycle (`OPT-P01`–`OPT-P05`,
  `OPT-P07`–`OPT-P09`) are present-tense contracts in
  [the optimiser specification](../optimiser/low-level.md), enforced by
  `tests/test_optimiser_frontier_materialisation.py`,
  `tests/test_optimiser_apply_artifacts.py`,
  `tests/test_optimiser_service_validation.py`,
  `tests/test_optimiser_routes_real_library.py`, and
  `tests/test_optimiser_routes.py`.
- The typed public-contract-error layer from `AUD-C10`'s first phase is
  delivered; the origin-blind fallback classification it fronts remains active
  above.
