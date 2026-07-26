# Optimiser roadmap

## Scope

Optimiser configuration, numerical solve/frontier behaviour, artifacts,
ratebooks, performance, interruptibility, and workflows remain reliable.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| OPT-P01–OPT-P03, AUD-C10 | Active | P0 | Preserve state and reject numerical/artifact failures. |
| OPT-P04–OPT-P09 | Active | P1 | Make compute, retention, and jobs bounded and truthful. |
| OPT-P10 | Active | P2 | Remove verified duplication and dead code. |
| OPT-P11–OPT-P14 | Planned | P2 | Carve the solve-service god module behind preserved contracts. |

## Planned improvements

### OPT-P01 — Frontier multi-point apply
**Why:** Applying several frontier points can lose expensive state or recompute inconsistently.

**Plan:** Preserve the solve context and immutable frontier artifacts across point selection and apply.

**Acceptance:** Multi-point tests apply points in any order with stable results and no repeated heavy setup.

**Dependencies:** OPT-P09.

**Evidence:** `src/haute/routes/optimiser.py`; `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_frontier_materialisation.py`.

### OPT-P02 — Save artifact contract
**Why:** Saved artifacts can be partial, non-finite, unversioned, or incompatible with apply.

**Plan:** Define a versioned artifact schema and write it atomically after finite-value validation.

**Acceptance:** Tests cover interrupted write, non-finite values, version migration/rejection, and save-then-apply equivalence.

**Dependencies:** OPT-P01; ratebook contracts.

**Evidence:** `src/haute/routes/optimiser.py`; `tests/test_optimiser_apply_artifacts.py`; `tests/test_optimiser_golden.py`.

### OPT-P03 — Constraint validation
**Why:** Null or non-finite constraints can silently bias a solve.

**Plan:** Validate every constraint input at the domain boundary before model construction.

**Acceptance:** Null, NaN, infinity, and valid boundary fixtures produce typed rejection or correct solve results.

**Dependencies:** AUD-C10.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `src/haute/routes/optimiser.py`; `tests/test_optimiser_service_validation.py`.

### AUD-C10 — Numerical and silent-failure residuals
**Why:** Residual numerical failures can be masked, while duplicated orchestration obscures solver invariants.

**Plan:** Close unowned numerical/silent-failure cases with explicit domain outcomes, then extract duplicated orchestration only behind preserved contracts.

**Acceptance:** Pathological numerical fixtures return typed terminal outcomes; refactoring preserves solve, frontier, save, and apply regressions.

**Dependencies:** OPT-P01–OPT-P03.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_contracts.py`; `tests/test_optimiser_service_validation.py`.

### OPT-P06 — Frontier compute scaling
**Why:** Frontier calculation carries scaling multipliers and misses safe bounded parallelism.

**Plan:** Remove redundant scale factors and introduce bounded parallel frontier work only where solver inputs are isolated.

**Acceptance:** Numerical equivalence and concurrency-bound tests cover serial and parallel frontier execution.

**Dependencies:** AUD-C10; job admission policy.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes_real_library.py`.

### OPT-P07 — Setup I/O passes
**Why:** Setup, auto-range, preview, counts, and statistics repeatedly scan the same data.

**Plan:** Share validated intermediate results and eliminate redundant scans without serving stale data.

**Acceptance:** Structural tests prove bounded scan counts and changed inputs invalidate reused values.

**Dependencies:** OPT-P06.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_service_coverage.py`.

### OPT-P09 — Trace apply recomputation
**Why:** Clicking a trace point reapplies an entire portfolio unnecessarily.

**Plan:** Reuse per-point apply artifacts and calculate only the selected delta where semantics permit.

**Acceptance:** Trace-click tests prove equivalent outputs with no full portfolio reapplication.

**Dependencies:** OPT-P01.

**Evidence:** `src/haute/_optimiser_apply_explainability.py`; `frontend/src/trace/optimiserApplyHelpers.ts`; `tests/test_optimiser_apply_trace_enrichment.py`.

### OPT-P08 — Memory lifecycle
**Why:** Optimiser memory, disk artifacts, and orphaned outputs can accumulate on long-lived servers.

**Plan:** Define bounded retention and safe orphan sweeping for solve/frontier artifacts.

**Acceptance:** Retention, expiry, active-artifact preservation, and orphan cleanup tests pass.

**Dependencies:** Job and cache lifecycle policy.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `src/haute/routes/_job_store.py`; `tests/test_optimiser_apply_artifacts.py`.

### OPT-P05 — Solve interruptibility
**Why:** Cancellation, timeout reporting, and admission release can disagree with actual solver state.

**Plan:** Thread cancellation and deadlines through supported solve boundaries and make terminal transitions release admission exactly once.

**Acceptance:** Tests cover cancellation, timeout, non-interruptible boundary reporting, and admission recovery.

**Dependencies:** Background-job and execution-engine lifecycle.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `src/haute/routes/_job_store.py`; `tests/test_optimiser_routes.py`.

### OPT-P04 — Heavy endpoints as jobs
**Why:** Heavy synchronous endpoints block request workers and lack consistent lifecycle visibility.

**Plan:** Move eligible operations behind the established job protocol with progress, typed terminal results, and polling semantics.

**Acceptance:** Route tests verify immediate job creation, progress, terminal success/failure, and equivalent operation results.

**Dependencies:** OPT-P05; background-job lifecycle.

**Evidence:** `src/haute/routes/optimiser.py`; `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes.py`.

### OPT-P10 — Optimiser hygiene
**Why:** Verified dead code and duplication make numerical paths harder to maintain.

**Plan:** Remove or consolidate only code proved unused or duplicated after core contracts are protected.

**Acceptance:** Each cleanup has focused regression coverage for the affected solve/artifact behaviour.

**Dependencies:** AUD-C10 and relevant P0/P1 packages.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes.py`.

### OPT-P11 — Extract owned artifact lifecycle
**Why:** Persistence, handle validation, load diagnostics, orphan cleanup, and startup reaping are
independent of solve orchestration but occupy the same module.

**Plan:** Move the two artifact families and their registered cleaners to
`src/haute/routes/_optimiser_artifacts.py`. Keep the current handle schema and route imports as
compatibility re-exports for one release.

**Acceptance:** Artifact round-trip, tampered-handle, orphan-race, TTL-cleanup, and stale-startup
tests pass unchanged; `_optimiser_service.py` owns no filesystem deletion.

**Dependencies:** OPT-P08.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_apply_artifacts.py`.

### OPT-P12 — Extract frontier domain service
**Why:** Frontier range normalisation, compute dispatch, payload limiting, job lifecycle, point
selection, and point-artifact retention form a cohesive domain separate from initial solve setup.

**Plan:** Move pure range/point helpers first, then introduce an `OptimiserFrontierService` that
owns explicit sweep admission, cancellation, parent publication, and point-artifact retention.
Keep FastAPI response assembly in `src/haute/routes/optimiser.py`.

**Acceptance:** Timeout/cancel, single-flight, compute-budget, point-selection, ratebook
materialisation, and artifact-cap regressions remain green at each extraction step.

**Dependencies:** OPT-P01, OPT-P05, OPT-P06.

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

**Dependencies:** OPT-P03, OPT-P07.

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
