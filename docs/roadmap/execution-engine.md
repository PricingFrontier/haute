# Execution engine roadmap

## Scope

Owns the canonical eager/lazy/chunked execution boundary, projection planning,
admission and memory bounds, fault vocabulary, metrics, and request-local
cleanup.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| ROAD-EXEC-01 | Active | P0 | Route all execution through one canonical contract boundary. |
| AUD-C13 | Reverify | P0 | Bound chunks from projected target width. |
| AUD-C09 | Reverify | P0 | Make projection demand attribution explicit and observable. |
| ROAD-EXEC-02 | Active | P1 | Add deterministic fault and cleanup service-level evidence. |
| ROAD-EXEC-03 | Active | P1 | Produce reproducible scale and complete metric evidence. |
| ROAD-EXEC-04 | Active | P1 | Cover compatibility, strategy invariants, and opt-in telemetry. |
| ROAD-EXEC-05 | Active | P2 | Harden startup and request-local housekeeping. |

## Planned improvements

### ROAD-EXEC-01 — Canonical execution boundary

**Why:** Entry points can disagree about contract resolution, preamble failure, admission, lifecycle, cleanup, and bounded metrics.

**Plan:** Return one strict resolution result; define preamble propagation; audit preview, trace, sink, deploy, optimiser estimate, training, and request-local writes for profile, admission, error mapping, and owner; complete the partitioned-parquet source boundary.

**Acceptance:** A bounded profile never proceeds after resolution failure; equivalent graph failures have one typed cause and user mapping; all terminal paths release owned data; partitioned reads prune projected columns/partitions before materialisation.

**Dependencies:** Background-jobs routes consume lifecycle semantics.

**Evidence:** `src/haute/execution.py`; `src/haute/_execute_lazy.py`; `src/haute/_execution_context.py`; `src/haute/_execution_admission.py`; `tests/test_execute_lazy_contracts.py`; `tests/test_execution_context.py`.

### AUD-C13 — Target-width chunk memory bound

**Why:** Source-only row-width estimates can understate output width and permit an oversized chunk.

**Plan:** Reverify the current estimator; price projected target schemas and variable-width output columns, then distinguish coarse post-hoc sampling from any continuous memory-enforcement guarantee.

**Acceptance:** Chunk planning uses target demand; supported oversized risk fails according to the documented typed policy; tests prove no source-width-only under-bound regression.

**Dependencies:** ROAD-EXEC-01 establishes profile and memory policy.

**Evidence:** `src/haute/chunking.py`; `src/haute/_execution_context.py`; `tests/test_chunk_plan.py`; `tests/test_chunk_runner.py`; `tests/test_bounded_collect_contracts.py`.

### AUD-C09 — Explicit projection demand attribution

**Why:** Heuristic fan-in ownership and silent seed loss can prune a required column or hide a non-strict degradation.

**Plan:** Reverify collision and opaque fan-out cases; use operand evidence or fail loudly for ownership, omit stale codegen parent attribution instead of guessing, and record a distinct diagnostic when a projection seed cannot be retained.

**Acceptance:** Required demand is assigned to a true producer or fails typed; non-strict seed loss is observable; codegen never reattributes stale parent inputs by heuristic.

**Dependencies:** ROAD-EXEC-01 contract-resolution failure behaviour.

**Evidence:** `src/haute/projection.py`; `src/haute/codegen.py`; `tests/test_projection_planner.py`; `tests/test_compute_needed_columns.py`; `tests/test_codegen_roundtrip_property.py`.

### ROAD-EXEC-02 — Fault injection and cleanup SLOs

**Why:** Cooperative cancellation and cleanup guarantees need repeatable evidence at native-operation and terminal boundaries.

**Plan:** Build deterministic faults for collect, sink/checkpoint, reducer, response shaping, and terminal transition; measure profile-specific cancellation latency; inject filesystem, parquet, metrics, status-store, cancellation, and supersession failures.

**Acceptance:** CI enforces recorded latency budgets with controlled native doubles; every terminal path removes owned temp files, cache pins, and heavy references without error wrapping.

**Dependencies:** ROAD-EXEC-01 lifecycle boundary.

**Evidence:** `src/haute/_execution_context.py`; `src/haute/_execute_lazy.py`; `src/haute/routes/_job_lifecycle.py`; `tests/test_execution_context.py`; `tests/test_server_concurrency.py`.

### ROAD-EXEC-03 — Reproducible scale and metrics

**Why:** Resource claims need a comparable environment/workload artifact, not a streaming flag or elapsed time alone.

**Plan:** Extend the existing performance report with schema/environment/version/input/profile/RSS/bytes/counts/temp-disk/admission/payload fields; add deterministic cross-profile smoke fixtures and explicit wall-time remainder.

**Acceptance:** Two revisions are comparable without reconstructing workload; smoke reports RSS, counters, disk, admission, and bounded metrics; unavailable counters are explicit `null` and wall time is fully partitioned within tolerance.

**Dependencies:** ROAD-EXEC-01 metrics schema; domain scale fixtures.

**Evidence:** `scripts/run_perf_suite.py`; `tests/performance/test_polars_scale_scenario.py`; `tests/performance/test_optimiser_memory_response_perf.py`; `src/haute/_execution_context.py`.

### ROAD-EXEC-04 — Compatibility, invariants, and telemetry

**Why:** Valid graph shapes and supported Polars releases need the same bounded-execution contract, while observability must remain bounded and optional.

**Plan:** Generate valid DAG/profile cases with replay seeds; run focused lower/current Polars conformance; emit bounded redacted events from existing metrics; add opt-in telemetry and decide whether persistent trace artifacts are needed.

**Acceptance:** Generated graphs cannot panic planner/chunk execution; supported endpoints conform; telemetry has no disabled-mode effect and bounded enabled attributes; trace retention has one tested bounded policy.

**Dependencies:** ROAD-EXEC-01 metric and fault vocabulary.

**Evidence:** `src/haute/_execution_context.py`; `src/haute/_execute_lazy.py`; `tests/test_polars_backend_strategy_contract.py`; `tests/test_bounded_collect_contracts.py`; `tests/test_chunk_whitelist_proofs.py`.

### ROAD-EXEC-05 — Startup and request-local housekeeping

**Why:** Restarts and ordinary successful requests can retain stale artifacts, response materialisations, and heavy optimiser state.

**Plan:** Reap only marked stale Haute artifact directories; enumerate creator/owner/cleanup/test hooks for request-local resources; cap completed heavy objects by key and time; test repeated request/status cycles.

**Acceptance:** Restart preserves unrelated temporary data while reclaiming stale owned artifacts; retention is observable and bounded across success, error, cancellation, pin, supersession, and expiry.

**Dependencies:** ROAD-EXEC-02 fault evidence; job lifecycle ownership.

**Evidence:** `src/haute/routes/_job_store.py`; `src/haute/routes/_optimiser_service.py`; `src/haute/routes/_background_jobs.py`; `tests/test_job_store.py`; `tests/test_server_concurrency.py`.
