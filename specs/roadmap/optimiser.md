# Optimiser roadmap

## Scope

Optimiser configuration, numerical solve/frontier behaviour, artifacts,
ratebooks, performance, interruptibility, and workflows remain reliable.
Current behaviour is specified in [the optimiser specification](../optimiser/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| OPT-P11 | Planned | P2 | Extract the canonical artifact-lifecycle owner. |
| OPT-P13 | Planned | P2 | Isolate immutable solve-input planning and grid construction. |
| OPT-P06 | Planned | P2 | Benchmark bounded frontier parallelism after input isolation. |
| OPT-P12 | Planned | P2 | Extract the frontier domain service after the scaling decision. |
| OPT-P14 | Planned | P2 | Complete solver/result publication extraction. |

## Planned improvements

Delivery order is `OPT-P11` → `OPT-P13` → the `OPT-P06` performance
decision → `OPT-P12` → `OPT-P14`; later packages must not bypass those
isolation boundaries.

### OPT-P06 — Frontier compute scaling
**Why:** Frontier calculation misses safe bounded parallelism.

**Plan:** Start only after OPT-P13 has made each frontier point's solver inputs
immutable and isolated. Benchmark serial execution against a fixed bounded
worker count over representative small and large frontiers. Implement
parallelism only when median wall-clock improves by at least 20% without
raising peak memory, weakening admission/cancellation, or changing ordering or
numerical results; otherwise record a no-change decision.

**Acceptance:** The performance artifact records workload, solver/library and
worker counts, wall-clock and peak-memory evidence, numerical equivalence,
stable point ordering, cancellation latency, and the implement/no-change
decision. If implemented, concurrency-bound tests cover serial and parallel
execution and prove admission is released exactly once.

**Dependencies:** OPT-P13 and the current typed failure-classification and job-admission
contracts.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes_real_library.py`.

### OPT-P11 — Extract owned artifact lifecycle
**Why:** Persistence, handle validation, load diagnostics, orphan cleanup, and startup reaping are
independent of solve orchestration but occupy the same module.

**Plan:** Move the two artifact families and their registered cleaners to
`src/haute/routes/_optimiser_artifacts.py`. Move every maintained internal
importer in the same package and remove the obsolete service-module names
immediately; Haute has no released internal import surface, so no compatibility
re-export or deprecation shim is permitted. Preserve the current artifact-handle
wire schema because it is the canonical persisted contract.

**Acceptance:** Artifact round-trip, tampered-handle, orphan-race, TTL-cleanup, and stale-startup
tests pass unchanged; `_optimiser_service.py` owns no filesystem deletion.

**Dependencies:** The current bounded artifact-memory lifecycle.

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

**Dependencies:** OPT-P11, OPT-P13, and the OPT-P06 implement/no-change
decision, plus the current frontier apply and interruptibility contracts.

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

**Dependencies:** OPT-P11 and the current constraint-validation and scan-bounding
contracts.

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

**Dependencies:** OPT-P11–OPT-P13 and the OPT-P06 scaling decision.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes.py`;
`tests/test_optimiser_golden.py`; `tests/test_optimiser_ratebook_apply_agreement.py`.
