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
| OPT-P15 | Planned | P2 | One auto-range job remains; the chunked path and its disk-bucket reducer go only if one streaming group-by keeps within the specified memory bound. |
| OPT-P16 | Planned | P2 | Optimiser inputs are materialised in a hard-capped worker, not on a server thread. |
| OPT-P17 | Planned | P2 | A failed estimate reports the failure instead of an estimate with missing fields. |
| OPT-P18 | Planned | P2 | Selecting a frontier point is computed once, by the server. |

## Planned improvements

Delivery order is `OPT-P11` → `OPT-P13` → the `OPT-P06` performance
decision → `OPT-P12` → `OPT-P14`; later packages must not bypass those
isolation boundaries. The packages from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md) are
independent of that order and each shrinks what the extractions must move:
`OPT-P15` removes a duplicated auto-range job and possibly the chunked path
(about 600 lines of the service), and `OPT-P16` is a step towards
`ROAD-WORKER-04` that needs no solver persistence.

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

### OPT-P15 — One auto-range job, bounded by measurement
**Why:** Auto-range needs, for each constraint, the sum over quotes of each
quote's minimum and maximum across its scenarios. Two jobs compute it:
`_run_frontier_auto_range_job` and `_run_streaming_frontier_auto_range_job`,
which is largely a clone of the first (72- and 34-line copied blocks) plus a
chunked-execution plan and fallback. Both feed
`_ScenarioFrontierRangeAccumulator`, a hand-written out-of-core,
hash-partitioned group-by that writes per-bucket Parquet parts so that no
global per-quote aggregate table is held in memory. The streaming path is the
only production consumer of the 2,251-line chunked runner. The same quantity
can be written as one streaming query,
`group_by(quote).agg(min, max).select(sum)`.

That query is not yet known to be as frugal. The optimiser specification
promises that, when the upstream chain is provably row-local, auto-range runs
chunk by chunk and never materialises the fully expanded scenario frame, and
`test_frontier_auto_range_streams_before_scenario_expansion_and_recombines_quotes`
pins the bounded chunks. One streaming group-by keeps that bound only if
every node between the base and the optimiser, including scenario expansion
and model scoring, streams in Polars, and only if the per-quote group-by state
fits in memory at high quote cardinality. The estimate endpoint's group-by
does not settle this: it selects only the quote-id column, so projection can
skip the scoring nodes that auto-range needs.

**Plan:** Build a representative fixture with high quote cardinality, scenario
expansion and model scoring, and measure peak memory for the current
streaming path and for one streaming group-by run under the job's execution
context and cancellation. If the group-by stays within the bound, state the
bound in the optimiser specification in place of the chunk-by-chunk
paragraph, replace both jobs with one that runs it, and delete the streaming
plan, `_ChunkFallback`, the accumulator and the duplicate job. If it does not,
keep the chunk-before-expansion path and merge only the duplicated job code.

**Acceptance:** One auto-range job remains; its ranges equal the current
jobs' ranges on the existing fixtures, including null quote-id and
non-finite rejections; a test on the representative fixture asserts that
peak memory stays within the bound the optimiser specification states,
replacing the chunk-size assertion only if the chunked path is removed; the
specification describes whichever path remains.

**Dependencies:** None. `EXEC-R03` (execution engine) removes the runner
afterwards, if this package removes its consumer.

**Evidence:** `src/haute/routes/_optimiser_service.py::_run_frontier_auto_range_job`;
`src/haute/routes/_optimiser_service.py::_run_streaming_frontier_auto_range_job`;
`src/haute/routes/_optimiser_service.py::_ScenarioFrontierRangeAccumulator`;
`src/haute/routes/_optimiser_service.py::_build_streaming_auto_range_plan`;
`src/haute/routes/_optimiser_service.py::_ChunkFallback`;
`src/haute/routes/optimiser.py::_optimiser_input_metrics`;
`tests/test_optimiser_routes.py`.

### OPT-P16 — Materialise optimiser inputs in a capped worker
**Why:** The optimiser runs its upstream pipeline execution and its solve on
daemon threads in the server process, with no hard memory cap and
cooperative cancellation only. Every other heavy surface (preview, trace,
Explore, JSON-cache builds, output writes, training preparation) runs in
killable spawn workers under a native cap. `ROAD-WORKER-04` defers isolating
the whole optimiser until solvers have versioned persistence, but the
pipeline execution does not need that: the grid builder already reads a
Parquet file.

**Plan:** Run setup and auto-range materialisation in the existing
hard-capped worker, as training preparation does, writing the projected,
validated solver input to a parent-owned Parquet artifact. The solve thread
builds the quote grid from that file as it does now. This also removes the
in-process pipeline execution that holds the chunk-size lock (`EXEC-R01`).

**Acceptance:** No optimiser code path collects or sinks a pipeline frame in
the server process; a memory-limited setup ends as a typed `memory_limited`
job; cancellation during setup terminates the worker; solve results match
the existing golden tests.

**Dependencies:** The worker protocol and artifact publication specified in
background jobs; `ROAD-WORKER-04` remains the package for the solver itself.

**Evidence:** `src/haute/routes/_optimiser_service.py::_execute_pipeline`;
`src/haute/routes/_optimiser_service.py::_build_grid`;
`src/haute/routes/_training_preparation.py`; `src/haute/_worker_protocol.py`;
`tests/test_optimiser_golden.py`.

### OPT-P17 — A failed estimate is reported as a failure
**Why:** `estimate_solve` catches any exception while reading source metadata,
logs a warning and continues with no row total, so an internal error looks
the same as a source whose size is honestly unknown.

**Plan:** Distinguish "unavailable" (a specified outcome with a reason) from an
unexpected failure, which propagates through the ordinary error path.

**Acceptance:** A test injects an unexpected error into metadata resolution
and receives an error response; the specified unavailable cases still
return an estimate that names why the total is missing.

**Dependencies:** None.

**Evidence:** `src/haute/routes/optimiser.py::estimate_solve`;
`src/haute/_ram_estimate.py::_detailed_ancestor_source_metadata`.

### OPT-P18 — Selecting a frontier point is server-authoritative
**Why:** A frontier point is turned into a solve summary twice: by
`_frontier_point_result_dict` on the server and by
`deriveSolveResultForFrontierPoint` in the browser's results store, and the
two disagree. The browser accepts two lambda shapes (a nested `lambdas`
object or flat `lambda_*` fields), silently falls back to the original
`converged`, `baseline_objective` and `baseline_constraints` when a field is
absent, and keeps the scenario histogram that the server drops; the server
rejects a point without `converged`. The non-converged warning text is
duplicated in both languages.

**Plan:** Use the server's selection result as the only derivation and delete
the browser copy. If the browser needs the summary without a round trip,
include the derived summary for each point in the frontier response.

**Acceptance:** No frontend code derives a solve summary from a frontier row;
selecting a point shows exactly the server's summary; a point missing
required fields is an error in one place.

**Dependencies:** None.

**Evidence:** `src/haute/routes/optimiser.py::_frontier_point_result_dict`;
`src/haute/routes/optimiser.py::select_frontier_point`;
`frontend/src/stores/useNodeResultsStore.ts::deriveSolveResultForFrontierPoint`.
