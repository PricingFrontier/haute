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
| `ROAD-WORKER-05` | Planned | P3 | One worker primitive and one worker-failure taxonomy serve one-shot, pooled and job workers. |
| `ROAD-WORKER-06` | Planned | P2 | A graceful server exit cancels every running job and returns within a bound instead of joining every in-flight worker. |
| `ROAD-WORKER-07` | Planned | P3 | The three parents classify a memory failure the same way, a supervisor's persistence failure reaches the status route, a worker's own `TimeoutError` is not reported as a response timeout, and teardown failures are recorded. |
| `ROAD-WORKER-08` | Planned | P3 | The worker modules carry one error envelope, one cgroup resolver and no dead feature. |
| `ROAD-WORKER-09` | Planned | P2 | SharedFlights and LatestWinsQueue propagate an operation's own TimeoutError immediately instead of spinning on a completed future. |

## Planned improvements

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

**Dependencies:** None. The optimiser's input estimate already runs on the
warm interactive worker pool.

**Evidence:** `src/haute/_worker_isolation.py::run_isolated_worker`;
`src/haute/_interactive_workers.py::InteractiveWorkerPool`;
`src/haute/_worker_protocol.py::run_worker_protocol`;
`src/haute/routes/_background_jobs.py::IsolatedJobSupervisor`;
`src/haute/routes/_isolated_worker_async.py`; `src/haute/routes/_timeouts.py`;
`src/haute/_dedicated_workers.py::DedicatedWorker` (the optimiser's solver session,
which already reuses the warm pool's error family).

### ROAD-WORKER-06 — Shutdown cancels every job
**Why:** Every one-shot and protocol worker is a non-daemon `multiprocessing`
child, so the interpreter's exit hook joins each one still alive. The lifespan
teardown stops only the dedicated workers and the warm pool, and no job
registry is cancelled at shutdown, so a training, dispersion, Explore,
node-data, input-cache or output-write job in flight keeps the server process
alive until it finishes or hits its own timeout (probe: a 6 s child in flight at
a 2 s exit ends the process at 7.6 s). An orchestrator's kill after its grace
period is then the only way out, which orphans the job's artifacts until the
24 h reaper. The background-jobs specification covers pool teardown only. This
package comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

**Plan:** At lifespan shutdown cancel every registered job across the training,
Explore, node-data, optimiser, pivot and input-cache registries so each
`stop_reason` fires, then join the supervisor threads with a bound. Daemonic
children would make the exit hook terminate instead of join, but a daemonic
process cannot spawn the descendants a cgroup or Job Object lease allows, so
cancellation is preferred.

**Acceptance:** A shutdown test with a sleeping protocol worker returns within
the bound and leaves no child; the job is recorded `cancelled`.

**Dependencies:** None.

**Evidence:** `src/haute/server.py::_lifespan`;
`src/haute/_worker_isolation.py::run_isolated_worker`;
`src/haute/_worker_protocol.py::run_worker_protocol`;
`src/haute/routes/_training_lifecycle.py`; `src/haute/routes/_node_data_service.py`;
`src/haute/routes/_optimiser_service.py`; `src/haute/routes/_pivot_service.py`;
`src/haute/routes/input_cache.py`.

### ROAD-WORKER-07 — Failures classified and surfaced the same way
**Why:** Four places lose or misname a failure. The one-shot parent maps only
the exact name `"MemoryError"`, the pool maps `endswith("MemoryError")`, the
protocol child maps `isinstance(exc, MemoryError)`, and the shared HTTP
predicate names two `MemoryError` subclasses, so the same child failure is
`error` for a one-shot or pool worker and `memory_limited` for a protocol
worker; the node-data service records a one-shot child's memory failure
without `error_code=memory_limit` and the dedicated worker special-cases one
class name to compensate. `IsolatedSupervisorThread.infrastructure_failure`
and `join_and_raise` are read by no production code, so a job whose terminal
state could not be persisted stays `running` until its TTL with only a log
line. `run_blocking_with_response_timeout` catches `TimeoutError` around
`asyncio.wait_for`, which on Python 3.11 is also what the worker raises, so a
worker that fails with any `TimeoutError` is answered 504 "timed out" and
recorded `response_timeout` (probed). The protocol parent and the dedicated
worker swallow queue-close and join failures in bare `except: pass` blocks
while the one-shot attaches each as a note. This package comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

**Plan:** One memory classification shared by the three parents (an `is_memory`
flag the child sets from `isinstance(exc, MemoryError)`); surface a dead
supervisor thread's `infrastructure_failure` from the status route and
transition the job; in the timeout branch re-raise the task's own exception
when `blocking_task.done()`; record teardown failures as notes.

**Acceptance:** Each parent reports `memory_limited` for both `MemoryError`
subclasses; a job whose persistence failed is answered as failed by its status
route; a worker-raised `TimeoutError` reaches the caller as itself; a failing
queue close appears as a note on the primary error.

**Dependencies:** `ROAD-WORKER-05` would subsume the classification.

**Evidence:** `src/haute/_worker_isolation.py::IsolatedWorkerRemoteError`;
`src/haute/_interactive_workers.py::InteractiveWorkerRemoteError`;
`src/haute/_worker_protocol.py::_terminal_reason_for_exception`;
`src/haute/_worker_isolation.py::isolated_worker_failure_is_memory`;
`src/haute/routes/_background_jobs.py::IsolatedSupervisorThread`;
`src/haute/routes/_timeouts.py::run_blocking_with_response_timeout`;
`src/haute/_dedicated_workers.py::DedicatedWorker`.

### ROAD-WORKER-08 — One envelope, one resolver, no dead feature
**Why:** The remote-error 5-tuple is unpacked into an
`InteractiveWorkerRemoteError` three times and the child-side error envelope is
built four times in the pool entrypoint and once more in the dedicated worker.
Two resolvers find "this process's cgroup v2 directory": the lease hard-codes
`/sys/fs/cgroup` and reads only the `0::` line, the headroom probe parses
`/proc/self/mountinfo` and handles a relocated mount, so on a hybrid host the
lease silently falls back to `RLIMIT_AS` while the headroom clamp works. No
production caller passes `cleanup_callbacks`, so `_run_cleanup_callbacks` and
`IsolatedWorkerCleanupError` never act; `address_space_caps_supported`,
`process_rss_sampling_supported` and `JobCommonFields` are referenced only by
tests or by nothing; `IsolatedJobSupervisor.launch` is never called
(`launch_protocol` is). This package comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

**Plan:** One `_remote_error_from_evidence` and one child-side
`_error_envelope` imported by the dedicated worker; one mountinfo-aware cgroup
resolver used by both the lease and the headroom probe; delete the dead
features or wire a real caller.

**Acceptance:** jscpd reports no clone between the pool and the dedicated
worker; the lease resolves its cgroup on a hybrid mount layout in a test with
a fake `mountinfo`; the named symbols are gone or called from production.

**Dependencies:** None.

**Evidence:** `src/haute/_interactive_workers.py`;
`src/haute/_dedicated_workers.py::_error_envelope`;
`src/haute/_native_memory_limit.py::_current_cgroup_path`;
`src/haute/_host_memory.py::_resolve_cgroup_locations`;
`src/haute/_worker_isolation.py::_run_cleanup_callbacks`;
`src/haute/_worker_isolation.py::address_space_caps_supported`;
`src/haute/routes/_background_jobs.py::IsolatedJobSupervisor`;
`src/haute/_process_memory.py::process_rss_sampling_supported`;
`src/haute/routes/_job_store.py::JobCommonFields`.

### ROAD-WORKER-09 — Completed operation timeouts are terminal failures

**Why:** SharedFlights catches `concurrent.futures.TimeoutError` from
`Future.result(timeout=...)` as a polling timeout even when it is the completed
operation's own exception. On Python 3.11 these are the same exception type. Real
SharedFlights and LatestWinsQueue probes show a completed failed future spinning until
cancellation, then returning ExecutionCancelledError instead of its original TimeoutError.
Optimiser outcome queries and frontier point work consume these abstractions.

**Plan:** Distinguish incomplete wait expiry from completed-operation failure and propagate
the original exception immediately. Preserve cancellation checks during an actual wait.

**Acceptance:** In both SharedFlights and LatestWinsQueue, an operation-raised TimeoutError
reaches all concurrent waiters as the original terminal failure without repeated polling
or cancellation. A successful retry completes normally. A genuinely incomplete future
still checks cancellation while waiting.

**Dependencies:** None; the correctness fix is independent of ROAD-WORKER-07's broader
failure-taxonomy work.

**Evidence:** `src/haute/routes/_shared_flights.py::FlightSubscription.wait`;
`src/haute/routes/_shared_flights.py::SharedFlights`;
`src/haute/routes/_shared_flights.py::LatestWinsQueue`.
