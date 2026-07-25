# Background Jobs — High-Level Specification

## Purpose

Several server routes (model training, optimiser solves/auto-range/graph-node setup,
exploratory data analysis) kick off work that is too slow to finish inside a single
HTTP request/response cycle. This component is the shared, in-process infrastructure
that turns that work into pollable, cancellable, memory-bounded background jobs:

- A place to store job status/progress/result that survives across HTTP requests.
- A single, race-safe way to move a job from "running" to a terminal state, even when
  several signals (success, cancellation, timeout, memory pressure, a worker crash)
  could all fire for the same job.
- A way to say "only the most recent job for this thing matters" and have starting a
  new one cooperatively cancel the previous one.
- A way to say "only one job may run for this key at a time" and reject a conflicting
  second start.
- A bridge from work that runs in an isolated worker process back into the same
  job-lifecycle contract as in-process background threads.
- A way to bound how long an HTTP handler waits on blocking work without losing track
  of that work once the response has moved on.

It exists so every long-running route reuses the same job semantics instead of each
route inventing its own polling/cancellation/cleanup story.

## Scope

In scope:

- The in-memory job record store and its TTL-based eviction of both job metadata and
  large result payloads (`_job_store.py`).
- The terminal-state transition contract: which reasons can overwrite which, and when
  a transition is rejected (`_job_lifecycle.py`).
- Latest-job-wins supersession and cooperative cancellation tokens per logical key
  (`_background_jobs.py::CancellableJobRegistry`).
- Single-flight mutual exclusion per logical key (`_background_jobs.py::SingleFlightCoordinator`).
- The parent-side adapter that turns an isolated worker process's outcome into a job
  lifecycle transition (`_background_jobs.py::IsolatedJobSupervisor`).
- Bounding HTTP response latency for blocking work run on a thread, independent of the
  job store (`_timeouts.py`).

Out of scope (owned elsewhere):

- The actual mechanics of running work in an isolated subprocess (memory limits,
  pickling, crash detection) — see the worker-isolation piece of
  [execution-engine](../execution-engine/high-level.md).
- Cancellation-token semantics, checkpoints, and memory-pressure sampling themselves —
  see [tracing](../tracing/high-level.md) / [execution-engine](../execution-engine/high-level.md)
  for `ExecutionContext` / `ExecutionCancellationToken`.
- The route handlers that decide *when* to create a job, what config to store in it,
  and how to render its status to a client — see
  [server-api](../server-api/high-level.md), [modelling](../modelling/high-level.md),
  [optimiser](../optimiser/high-level.md), and [explore-eda](../explore-eda/high-level.md).
- Any frontend polling/rendering of job status — see
  [frontend-shared](../frontend-shared/high-level.md).

## Behaviour

- **Create and poll.** A route creates a job with an initial status dict and receives
  a generated job ID. Callers poll by ID; a missing or expired ID surfaces as HTTP 404.
- **One-way terminal transition.** A job moves from `running` to a terminal status:
  `completed`, `error`, `cancelled`, `superseded`, `timed_out`, `memory_limited`, or
  `contract_error`. It never goes back to `running`; a non-completed terminal reason may
  still be replaced by a strictly higher-precedence terminal reason as described below.
- **Terminal reason precedence, not first-write-wins.** When multiple terminal
  transitions race for the same job (e.g. a timeout fires while the worker is also
  failing), the reason with the *highest* precedence wins, in the order (low to high):
  `error < contract_error < memory_limited < cancelled < timed_out < superseded`.
  `completed` is a special case: it can only be reached directly from `running`, and
  once a job is `completed` no other reason can ever overwrite it, regardless of
  precedence.
- **Latest-job-per-key supersession.** Registering a new job for a key that still has a
  previous job registered automatically requests cancellation of the previous job with
  reason `superseded`; the registry does not inspect the previous job's persisted status.
  Cancellation is cooperative: the previous job's worker must itself check for cancellation
  and stop; nothing in this component can forcibly kill a running thread.
- **Single-flight keys.** A key can have at most one *owning job ID* at a time. An acquire by
  a different job ID is rejected with a typed conflict rather than queued or silently
  ignored; the current owner may re-acquire idempotently. Callers translate conflicts into
  HTTP 409.
- **Bounded retention.** Job metadata is evicted lazily on store access once its TTL
  expires (24 hours by default). A running job uses its latest locked update time so active
  progress keeps it alive; terminal jobs use their original `created_at`, not `ended_at`.
  Heavy result payloads attached to a *completed* job (solver objects, full
  solve-result dataframes, quote grids) are stripped much sooner (15 minutes by
  default) so status polling keeps working without holding onto large in-memory
  objects for the full metadata TTL. Any artifact files referenced by an evicted job
  are also cleaned up via a registered cleaner.
- **Isolated worker outcomes join the same lifecycle.** Work that runs in a separate
  process (for memory isolation) reports back through the identical terminal-reason
  contract as in-process background threads. Failure records additionally carry
  worker-specific diagnostic fields, so a poller can distinguish those failures if it
  inspects more than status and terminal reason.
- **Bounded HTTP response latency, unbounded work.** A blocking call started on a
  worker thread can be capped so the HTTP response returns (504) within a timeout,
  but the underlying work is *not* aborted — it keeps running. On completion the task is
  drained: a late successful return value is discarded, while an ordinary late exception is
  logged. If the HTTP request itself is
  cancelled (e.g. client disconnect), this component still waits for started thread
  work to actually finish before letting the cancellation propagate, since Python
  cannot forcibly terminate a running thread.

## Design rationale

- **In-memory, single-process store.** `JobStore` is explicitly documented as "fine
  for a single-server dev tool" — there is no distributed coordination, persistence,
  or multi-worker sharing. This keeps the implementation simple at the cost of jobs
  not surviving a process restart and not being visible across server replicas.
- **Reason precedence over first-write-wins.** Multiple asynchronous signals (a
  worker's own error, a timeout timer, a memory-pressure callback, a new job
  superseding this one) can all try to terminate the same job concurrently. The explicit
  ordering makes the result deterministic and deliberately gives later user/control-plane
  stop intent priority over generic worker failure: for example, `superseded` can replace
  an already-recorded `error`, but an `error` can never replace `superseded`. This is a
  policy ranking, not a claim that the winner is always the most diagnostic reason.
- **`completed` is sticky.** A user-visible success must never be quietly downgraded
  to a cancellation/timeout artifact of a race that occurs after the result is
  already in hand; the transition rules special-case `completed` to be both the only
  way in (from `running`) and immune to being overwritten once reached.
- **Read-merge-swap over per-field mutation.** `JobStore`'s mutation methods never mutate a
  stored job dict in place. Every update builds a new dict and swaps it in with a single
  `dict.__setitem__`, which CPython's GIL makes atomic — a reader holding the previous
  reference always sees a fully-old or fully-new record, never a torn one. A single
  `RLock` still serialises the read-merge-swap sequence itself so two concurrent
  writers touching disjoint keys cannot lose each other's writes (a hazard that bare
  atomic dict-swap alone does not prevent).
- **Two-tier TTL for heavy objects.** Splitting "how long is a job's status visible"
  from "how long does a completed job hold onto multi-object solver/dataframe state"
  lets a UI keep polling a finished job's summary long after the expensive payload
  has been released, bounding peak memory without shortening the useful life of job
  metadata.
- **Cooperative, not preemptive, cancellation.** Because Python cannot forcibly stop a
  running thread, cancellation here is always a signal (an `Event` plus a shared
  `ExecutionCancellationToken`) that a worker must poll at safe checkpoints. This
  pushes responsibility for *where* it's safe to stop onto the worker code, which is
  a deliberate trade-off documented at the call sites that consume this component.
- **Response-timeout without abandoning the task.** Returning a 504 does not cancel the
  in-flight thread. The task remains observable through `BlockingWorkTimeoutError`'s
  `background_task` (used by supersession to retain real worker occupancy), and a done
  callback consumes its eventual outcome. The helper does not preserve a late successful
  value for the original caller or a job record; it discards that value, while logging an
  ordinary late exception.
- **Fail loudly on store misuse.** Constructing a `JobStore` with a negative TTL,
  updating an unknown job ID, or scheduling heavy-object cleanup without a concrete
  expiry all raise immediately rather than degrading gracefully — consistent with the
  project's preference for loud failures over silent, hard-to-notice fallbacks.

## Interactions

Consumers (own their route-specific job semantics on top of this component):

- [modelling](../modelling/high-level.md) — training jobs, one `CancellableJobRegistry`
  per training-job namespace.
- [optimiser](../optimiser/high-level.md) — solve, frontier auto-range, and
  graph-node-setup jobs; the heaviest user of both `CancellableJobRegistry` (three
  independent registries) and `SingleFlightCoordinator` (one per graph/node key).
- [explore-eda](../explore-eda/high-level.md) — EDA jobs.
- [pipeline, databricks, json-cache, output-assemble routes](../server-api/high-level.md)
  — use only the response-timeout helper (`_timeouts.py`), not the job store.

Depended on:

- [execution-engine](../execution-engine/high-level.md) — `ExecutionContext` and
  `ExecutionCancellationToken` supply the cancellation/checkpoint machinery that
  `CancellableJobRegistry` wraps, and `ExecutionMemoryPressureEvent` drives the
  running-job metrics publisher.
- [execution-engine](../execution-engine/high-level.md) (worker isolation) —
  `run_isolated_worker`, `IsolatedWorkerConfig`, and the `IsolatedWorkerError` family
  are what `IsolatedJobSupervisor` adapts into job-lifecycle transitions.
- [tracing](../tracing/high-level.md) — execution metrics published onto running jobs
  feed the same metrics surfaces documented there.

## Failure model

- **Unknown job ID.** `require_job` / `require_completed_job` raise HTTP 404;
  `require_completed_job` raises HTTP 400 (with the actual status in the message) if
  the job exists but isn't `completed`.
- **Corrupt persisted status.** A job whose `status` field is missing or not one of
  the known statuses raises `ValueError` from `require_job_status` rather than being
  treated as any particular state.
- **Invalid construction parameters.** A negative `ttl_seconds` or
  `heavy_object_ttl_seconds`, or an unlisted `JobStore` prefix, raises `ValueError`
  immediately at construction/lookup time. The prefix allow-list is closed
  specifically to prevent a caller-supplied value from reaching an unbounded cache.
- **Contract violations inside the store.** Scheduling heavy-object cleanup without a
  numeric expiry timestamp raises `RuntimeError` — this is treated as an internal bug,
  not a condition to route around.
- **Single-flight contention.** Acquiring an already-owned key raises
  `SingleFlightConflictError`; the optimiser route layer converts this into an HTTP
  409 rather than queuing the second request or silently reusing the existing job.
- **Cooperative cancellation.** `BackgroundJobStoppedError` is raised *inside* worker
  code when it observes its own job has been cancelled or superseded; it is not raised
  by this component automatically — a worker that never checks
  `CancellableJobRegistry.cancellation_reason` will keep running to completion
  regardless of a cancellation request.
- **Isolated worker failures.** Every `IsolatedWorkerError` subtype (crash, remote
  exception, timeout, memory limit, contract violation) is caught by
  `IsolatedJobSupervisor` and turned into a terminal transition with diagnostic
  fields (`worker_error_class`, `worker_error_type`/`worker_remote_traceback` for
  remote exceptions, `worker_exitcode` for crashes, `error_code` for memory limits).
  An unrecognised reason string coerces to `error` rather than raising, so the job
  still reaches a terminal state.

  > NOTE: `IsolatedJobSupervisor.launch`'s background `run()` closure only catches
  > `IsolatedWorkerError`. Any other exception escaping `run_isolated_worker` (e.g. a
  > bug in the parent-side result-collection path itself, not the worker) is not
  > caught, is not written to the job record, and is only visible via the default
  > `threading.Thread` excepthook (stderr). The job is left permanently `running`
  > until the 24-hour metadata TTL evicts it — no HTTP-visible terminal transition
  > ever occurs for that failure mode.

- **Background work outliving its HTTP response.** If a client-facing response
  timeout fires, or the request itself is cancelled, the background thread is neither
  killed nor left with an unobserved task exception: its eventual result or exception is
  drained through `_drain_background_future_result`. A successful value is discarded; an
  ordinary exception is logged. A `BaseException` (e.g. `SystemExit`)
  raised by the background work is intentionally *not* swallowed by the drain path
  and propagates out of the callback.

## Approved change contract — 0.7.0 input-cache jobs

Remaining background-job improvement work is tracked in the
[background jobs and API roadmap](../../roadmap/background-jobs-api.md).
The shared input-snapshot API uses this component rather than creating another route-local job
state machine.

- Add one closed `input_cache` job-store namespace. Each build/refresh has a stable job id,
  ordinary lifecycle status, cooperative cancellation token, bounded progress fields, and a safe
  typed terminal error. The source-cache identity digest is the single-flight key.
- A request for an identity already building returns the existing active job id; the route
  obtains that join through `SingleFlightCoordinator.active()` before attempting `acquire()`.
  Different identities may run concurrently under the input-cache route's explicit global
  admission limit. The coordinator itself keeps its existing reject-on-conflicting-acquire
  semantics.
- Job TTL governs status observability only. Evicting an input-cache job never deletes a
  published snapshot generation, and snapshot clear/garbage collection never depends on a job
  record still existing. Jobs contain no resolved credentials, connector objects, dataframes, or
  cache reader leases.
- A cancel response means cancellation was requested; the job becomes `cancelled` only when the
  builder observes a checkpoint and the lifecycle transition wins. A completed publication
  remains sticky if cancellation races after commit.

Acceptance covers namespace closure, same-identity join, different-identity concurrency,
cancel/complete races, progress isolation, TTL independence from snapshots, bounded job payloads,
and redaction.

## Approved execution-housekeeping contract

Execution-owned artifacts that can survive a process crash live only in explicitly named Haute
artifact roots. Every reapable child directory contains a versioned ownership marker written at
creation. Server startup may remove a child only when the root is explicitly registered, the child
is a direct non-symlink descendant, its marker is valid for the expected owner, and its marker age
exceeds the configured stale interval. Unmarked directories, malformed markers, symlinks,
unexpected owners, and unrelated operating-system temporary data are preserved.

Optimiser apply-result and ratebook-factor directories adopt this marker contract and are reaped
from their existing dedicated roots during server lifespan startup. Ordinary job eviction and
artifact-handle cleanup remain the primary live-process lifecycle; startup reaping is only the
crash/restart backstop.

Completed heavy runtime objects remain bounded by the existing closed key set and short TTL.
Their expiry timestamp and clearing timestamp remain observable in job metadata, while artifact
handles survive long enough for ordinary TTL eviction to invoke their typed cleaners. Repeated
status reads may extend heavy-object retention only up to the existing metadata lifetime.

An isolated-job supervisor must transition an unexpected ordinary parent-side exception to
`error` before reporting it to the thread exception hook. No supported supervisor failure leaves a
job permanently `running`. Terminal-transition fault tests cover status-store failure,
supersession races, and cleanup scheduling without replacing the original worker error.
