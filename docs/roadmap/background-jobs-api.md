# Background jobs and API lifecycle roadmap

## Scope

Owns job lifecycle transitions, worker supervision, request supersession,
artifact/event transfer, route timeouts, and deterministic cleanup for
long-running work.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| ROAD-WORKER-01 | Implemented | P0 | Worker supervision terminalises every launched job. |
| ROAD-WORKER-02 | Implemented | P0 | Typed, bounded worker artifact and event contracts. |
| AUD-C15 | Implemented | P0 | Coherent JobStore access, timeout stamping, and terminal correction. |
| ROAD-WORKER-03 | Implemented | P1 | Fit/evaluation and dispersion cross the worker boundary. |
| ROAD-WORKER-04 | Deferred | P1 | Requires versioned solver-specific persistence before isolation is safe. |
| AUD-C19 | Verified | P1 | Existing cache and retained GPU-work bounds already satisfy the audit. |
| ROAD-WORKER-05 | Implemented | P2 | Explicit local-worker and generated-server enforcement policies. |

## 0.8.0 audit outcome

ROAD-WORKER-01, ROAD-WORKER-02, AUD-C15, and ROAD-WORKER-05 are direct
correctness or operability improvements and were accepted. ROAD-WORKER-03 was
accepted with a narrower boundary: synchronous request validation, graph
compilation, admission, and bounded Parquet materialisation stay in the parent
so HTTP contract errors and admission decisions remain immediate; the
crash-prone fit/evaluation, model staging, and dispersion search run in spawned
workers.

ROAD-WORKER-04 is not an improvement in its current form. Supported optimiser
follow-ons intentionally reuse live solver and frame state, and the codebase has
no stable, versioned, solver-specific persistence format from which that state
can be reconstructed. Ad-hoc process pickling would weaken correctness and
restart guarantees. Reconsider the item only after each supported solver has a
declared reopenable format, schema/version migration rules, rebuild-cost
limits, and equivalence tests.

AUD-C19 required no implementation change: sandbox validation already uses the
bounded shared `LRUCache`, while cancellation-constrained GPU writer retention
is deliberately observable and tested rather than unsafely deleted.

## Planned improvements

### ROAD-WORKER-01 — Total supervisor terminalisation

**Why:** An unexpected parent-side supervisor error can leave a writable job marked `running`.

**Plan:** Make the parent map success, failure, timeout, cancellation, process loss, malformed protocol, and cleanup failure into exactly one precedence-aware lifecycle outcome; expose terminal-persistence failure as infrastructure failure.

**Acceptance:** Every supervisor exit terminalises a writable job; late outcomes cannot replace a higher-precedence terminal state; typed reasons and worker fields survive where available.

**Dependencies:** Uses the shared lifecycle transition API and execution fault vocabulary.

**Evidence:** `src/haute/_worker_isolation.py`; `src/haute/routes/_job_lifecycle.py`; `tests/test_worker_isolation.py`; `tests/test_job_lifecycle.py`.

### ROAD-WORKER-02 — Versioned worker artifacts and events

**Why:** Spawned work needs serialisable, bounded communication without sharing route-owned state.

**Plan:** Define versioned picklable request, progress-event, result-manifest, and failure payloads. Validate parent-owned artifact publication, monotonic bounded events, integrity metadata, and terminal TTL cleanup.

**Acceptance:** Windows-spawn entrypoints reconstruct status from events/manifests alone; no worker receives `JobStore`, callbacks, data frames, or solvers; manifests cannot escape the parent artifact root.

**Dependencies:** ROAD-WORKER-01; artifact ownership and execution metrics.

**Evidence:** `src/haute/_worker_protocol.py`; `src/haute/_worker_isolation.py`;
`src/haute/routes/_background_jobs.py`; `tests/test_worker_protocol.py`;
`tests/test_worker_isolation.py`; `tests/test_job_store.py`.

### AUD-C15 — JobStore and timeout coherence

**Why:** Unlocked store reads, late timeout stamping, and raw status updates can mask failure or create contradictory terminal state.

**Plan:** Reverify each finding, replace unlocked worker reads with safe lifecycle access, stamp timeout state when jobs are created, and route terminal errors through the lifecycle transition API.

**Acceptance:** Concurrent eviction cannot hide a terminal transition; hung work can time out while executing; `status` and `terminal_reason` always agree.

**Dependencies:** ROAD-WORKER-01 must retain lifecycle precedence.

**Evidence:** `src/haute/routes/_job_store.py`; `src/haute/routes/_optimiser_service.py`; `src/haute/routes/_train_service.py`; `tests/test_job_store.py`; `tests/test_state_transitions.py`.

### ROAD-WORKER-03 — Isolate training and dispersion

**Why:** Training and dispersion still retain route-thread callbacks and rich in-memory results.

**Plan:** Keep request validation, graph/pipeline preparation, and admission in
the parent. Run fit/evaluation, dispersion profiling, progress transport, and
model staging through worker contracts; validate and publish the model pair in
the parent while preserving cancellation, bounded loss history, diagnostics,
and existing responses.

**Acceptance:** A killed worker does not take down the host; synchronous
validation and admission retain their existing HTTP behavior; both
status/cancellation flows retain truthful terminal states; artifacts are
published or cleaned according to declared lifetime.

**Dependencies:** ROAD-WORKER-01 and ROAD-WORKER-02; execution admission.

**Evidence:** `src/haute/modelling/_training_job.py`; `src/haute/routes/_train_service.py`; `tests/test_training_memory_safety.py`; `tests/test_train_service_coverage.py`; `tests/test_modelling_routes.py`.

### ROAD-WORKER-04 — Isolate optimiser workflows

**Why:** Setup, solve, auto-range, and frontier recomputation retain solver/data-frame state across route threads.

**Plan:** Deferred pending solver-specific persistence contracts. Do not pass
live solvers or frames across spawn boundaries and do not use unversioned
pickles as restart artifacts.

**Acceptance:** Supported workflows share no solver/data-frame state across processes, recover deterministically after cancellation/crash/restart, and leak no reservation or temporary artifact.

**Dependencies:** ROAD-WORKER-02; canonical execution boundary; lifecycle ownership.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `src/haute/routes/optimiser.py`; `tests/test_optimiser_routes.py`; `tests/test_optimiser_contracts.py`; `tests/test_streaming_chunk_size_threading.py`.

### AUD-C19 — Long-lived resource bounds

**Why:** Reverify whether validation caches and cancellation-constrained GPU work remain bounded or observable on long-lived servers.

**Plan:** Bound cache entries with the established LRU primitive; expose accepted irreducible GPU-fit thread/artifact retention rather than deleting files under a live writer.

**Acceptance:** Repeated distinct validations have a fixed memory bound; retained cancellation-constrained resources have explicit diagnostics and no unsafe cleanup.

**Dependencies:** Execution cleanup and operational diagnostics.

**Evidence:** `src/haute/_sandbox.py`; `src/haute/_lru_cache.py`; `src/haute/modelling/_algorithms.py`; `tests/test_server_concurrency.py`.

### ROAD-WORKER-05 — Deployment enforcement decision

**Why:** Admission and RSS sampling do not by themselves state an OS/container isolation guarantee.

**Plan:** Decide and encode supported platforms, required versus best-effort caps, operator configuration, unavailable-enforcement failure behaviour, and generated-container server policy.

**Acceptance:** Documentation and runtime configuration state one unambiguous guarantee; required limits fail loudly when unavailable; supported platform policies run in CI.

**Dependencies:** ROAD-WORKER-01; execution memory/admission contract.

**Evidence:** `src/haute/deploy/_container.py`; `src/haute/deploy/_request_limits.py`; `src/haute/_execution_admission.py`; `tests/test_execution_context.py`; `tests/test_ram_estimate.py`.
