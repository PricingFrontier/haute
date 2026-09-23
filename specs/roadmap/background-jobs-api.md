# Background jobs and API lifecycle roadmap

## Scope

Owns job lifecycle transitions, worker supervision, request supersession,
artifact/event transfer, route timeouts, and deterministic cleanup for
long-running work.

Current supervision, transport, lifecycle, cache-bound, and enforcement
behaviour is defined by the background-jobs and server-api component
specifications and their ordinary regression tests.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `ROAD-WORKER-04` | Deferred | P1 | Requires versioned solver-specific persistence before optimiser isolation is safe. |
| `ROAD-WORKER-05` | Planned | P3 | One worker primitive and one worker-failure taxonomy serve one-shot, pooled and job workers. |

## Planned improvements

### ROAD-WORKER-04 — Isolate optimiser workflows

**Why:** Setup, solve, auto-range, and frontier recomputation retain solver/data-frame state across route threads.

**Plan:** Defer implementation until every supported solver has a stable,
versioned persistence format. Do not pass live solvers or frames across spawn
boundaries and do not use unversioned pickles as restart artifacts.

**Activation trigger:** Each supported online and ratebook solver publishes a
canonical versioned persistence adapter with round-trip, corrupt/unknown-version,
and restart reconstruction tests. Until that trigger is met this package is
intentionally non-startable; thread-backed isolation remains the truthful
runtime contract.

**Acceptance:** Supported workflows share no solver/data-frame state across processes, recover deterministically after cancellation/crash/restart, and leak no reservation or temporary artifact.

**Dependencies:** Canonical execution boundary, lifecycle ownership, and versioned solver-specific persistence contracts.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `src/haute/routes/optimiser.py`; `tests/test_optimiser_routes.py`; `tests/test_optimiser_contracts.py`; `tests/test_streaming_chunk_size_threading.py`.

### ROAD-WORKER-05 — One worker primitive and one failure taxonomy
**Why:** Three subprocess mechanisms carry parallel error hierarchies. The
one-shot `run_isolated_worker` has eleven `IsolatedWorker*Error` classes; the
warm `InteractiveWorkerPool` has eight `InteractiveWorker*Error` classes that
mirror them (start, timeout, stopped, memory limit, crashed, remote); and the
versioned job transport `run_worker_protocol` adds its own. The async adapter,
the job supervisor and the thread timeout helper sit on top. Each taxonomy is
mapped to HTTP status and job terminal states separately. This package comes
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

**Plan:** Make the warm pool the single primitive, with one-shot work as a
pool slot that is retired after one use, and the versioned transport as the
message format every worker speaks. Replace the parallel hierarchies with one
worker-failure family mapped once to HTTP and job states.

**Acceptance:** One worker-failure hierarchy exists and one mapping to HTTP
and job terminal states; preview, trace, Explore, JSON-cache, output-write,
training and deploy batch workers use the same primitive; the existing
supervision, timeout, memory-limit and crash-classification tests pass
against it.

**Dependencies:** None. Taking it after `OPT-P16` (optimiser) avoids moving
the optimiser's new worker twice.

**Evidence:** `src/haute/_worker_isolation.py::run_isolated_worker`;
`src/haute/_interactive_workers.py::InteractiveWorkerPool`;
`src/haute/_worker_protocol.py::run_worker_protocol`;
`src/haute/routes/_background_jobs.py::IsolatedJobSupervisor`;
`src/haute/routes/_isolated_worker_async.py`; `src/haute/routes/_timeouts.py`.
