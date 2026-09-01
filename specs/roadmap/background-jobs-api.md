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
