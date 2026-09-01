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
- The bounded version-1 worker transport, including progress/result envelopes,
  artifact-manifest validation, and the parent protocol loop (`_worker_protocol.py`).
- Marker-based creation and restart cleanup of owned temporary artifact directories
  (`_artifact_housekeeping.py`).
- Bounding HTTP response latency for blocking work run on a thread, independent of the
  job store (`_timeouts.py`).

Out of scope (owned elsewhere):

- The low-level single-result process primitive, process memory-cap enforcement,
  termination, and crash classification — see the worker-isolation piece of
  [execution-engine](../execution-engine/high-level.md). This component owns the
  versioned transport layered on that primitive.
- Cancellation-token semantics, checkpoints, and memory-pressure sampling themselves —
  see [execution-engine](../execution-engine/high-level.md) for
  `ExecutionContext` / `ExecutionCancellationToken`.
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
  `completed` is a special case: ordinary transitions can only reach it directly
  from `running`, and precedence-based races cannot overwrite it. The sole explicit
  exception is publication correction: a caller may compare-and-swap
  `expected_status="completed"` to `to="error"` after validating that a supposedly
  published result is unusable.
- **Latest-job-per-key supersession.** Registering a new job for a key that still has a
  previous job registered automatically requests cancellation of the previous job with
  reason `superseded`; the registry does not inspect the previous job's persisted status.
  Cancellation is cooperative: the previous job's worker must itself check for cancellation
  and stop; nothing in this component can forcibly kill a running thread.
- **Single-flight keys.** A key can have at most one *owning job ID* at a time. An acquire by
  a different job ID is rejected with a typed conflict rather than queued or silently
  ignored; the current owner may re-acquire idempotently. Callers translate conflicts into
  HTTP 409.
- **Input-cache builds use the shared lifecycle.** The closed `input_cache` namespace
  stores bounded progress and terminal metadata only; snapshot generations,
  credentials, connector objects, dataframes, and cache leases do not live in job
  records. Same-identity starts join the active job through
  `SingleFlightCoordinator`; job TTL never deletes a published snapshot.
  A cancel response acknowledges a request only; the job becomes `cancelled` when
  the builder observes its checkpoint. Different identities may run concurrently
  only within the input-cache route's separate global admission limit.
- **Bounded retention.** Job metadata is evicted lazily on store access once its TTL
  expires (24 hours by default). A running job uses its latest locked update time so active
  progress keeps it alive; terminal jobs use their original `created_at`, not `ended_at`.
  Every stored job has the `created_at` stamped by `create_job`; the store has no alternate
  missing-timestamp record shape.
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
- **The worker transport is bounded and versioned.** Spawn children receive a
  plain-data version-1 request and return bounded progress events, a progress-end
  marker, one result manifest, or one failure payload. Progress delivery is
  non-blocking: capacity/budget exhaustion increments a reported drop count rather
  than stalling work. The parent rejects malformed versions, order, bounds,
  non-plain data, unknown artifact kinds, integrity mismatches, and artifact paths
  outside its pre-created root before publication.
  Version 1 caps an event at 64 KiB, result metadata at 4 MiB, artifacts at 64,
  identifiers/messages at 512 characters, relative paths at 4,096 characters, and
  plain-data nesting at 64.
- **Publication is parent-owned.** Child artifacts are staged and described by
  relative path, kind, lifetime, size, and SHA-256. Only a completely validated
  manifest is published. Cleanup before commit may fail the outcome; cleanup after a
  committed publication is retained as diagnostics and cannot discard the completed
  result or rewrite it as an error.
- **Crash-surviving artifacts are marker-owned.** Reaping visits only explicitly
  registered roots and removes a stale direct child only when its versioned marker
  names the expected owner. Symlinks, Windows reparse points, unmarked/malformed
  children, wrong owners, and fresh children are preserved. Server readiness does not
  wait for the tracked background reap.
- **Process-memory enforcement is explicit and fail-closed.** `required` is the
  default and installs the platform's native hard worker cap before user work;
  `best_effort` is an explicit compatibility override using any available native
  cap plus admitted/RSS checkpoints. Required mode fails before work when the
  requested hard cap is unavailable.
  Generated scoring selects `strict_server` admission explicitly but never represents
  application RSS sampling as a hosting-platform/container hard limit.
- **Timeout accounting starts with job creation.** Training writes monotonic start
  time and timeout in the initial locked record so parent preparation counts.
  Optimiser elapsed reporting uses a locked snapshot/helper or worker-local time,
  never direct subscripting of the store's backing mapping.
- **Restart cleanup has a strict operator knob.**
  `HAUTE_ARTIFACT_STALE_SECONDS` is a non-negative integer (default 86,400).
  Startup validates it synchronously, schedules one tracked lifespan reaper without
  delaying readiness, and observes the task at shutdown.
- **Bounded HTTP response latency for explicitly thread-backed work.** A blocking call started on a
  worker thread can be capped so the HTTP response returns (504) within a timeout,
  but the underlying work is *not* aborted — it keeps running. On completion the task is
  drained: a late successful return value is discarded, while an ordinary late exception is
  logged. If the HTTP request itself is
  cancelled (e.g. client disconnect), this component still waits for started thread
  work to actually finish before letting the cancellation propagate, since Python
  cannot forcibly terminate a running thread. Heavy execution routes use killable process
  isolation in production instead; this thread contract is retained only for bounded I/O helpers
  and explicit development compatibility mode.

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
- **`completed` is sticky against races.** A user-visible success must never be quietly downgraded
  to a cancellation/timeout artifact of a race that occurs after the result is
  already in hand. The explicit completed-to-error compare-and-swap is deliberately
  narrower: it corrects a publication that was first recorded as successful and then
  failed validation; it is not part of terminal-reason precedence.
- **Immutable snapshots over mutable storage exposure.** `JobStore` never exposes
  its backing mapping or lock. Every read returns a detached, read-only snapshot;
  every update builds a new record and swaps it under one private `RLock`, so readers
  see a fully-old or fully-new state and concurrent disjoint writes cannot be lost.
  The same boundary validates the closed status vocabulary, reserves lifecycle
  metadata from generic updates, and owns terminal precedence.
- **Publication and completion share one claim.** A job-specific publisher may need
  to atomically replace several durable artifacts before it can construct the final
  result. The store's compare-and-swap completion operation checks `running`, holds
  the lifecycle claim while that publisher commits, then swaps the final result and
  completed metadata together. Cancellation therefore wins before publication or
  observes completed afterwards; it cannot split artifact publication from the job
  result.
- **Two-tier TTL for heavy objects.** Splitting "how long is a job's status visible"
  from "how long does a completed job hold onto multi-object solver/dataframe state"
  lets a UI keep polling a finished job's summary long after the expensive payload
  has been released, bounding peak memory without shortening the useful life of job
  metadata.
- **Cooperative cancellation for thread-backed work.** Because Python cannot forcibly stop a
  running thread, cancellation here is always a signal (an `Event` plus a shared
  `ExecutionCancellationToken`) that a worker must poll at safe checkpoints. This
  pushes responsibility for *where* it's safe to stop onto the worker code, which is
  a deliberate trade-off documented at the call sites that consume this component. Isolated
  heavy workers additionally support preemptive parent termination and join on timeout,
  supersession, and cancellation.
- **Response-timeout without abandoning the task.** Returning a 504 does not cancel the
  in-flight thread. The task remains observable through `BlockingWorkTimeoutError`'s
  `background_task` (used by supersession to retain real worker occupancy), and a done
  callback consumes its eventual outcome. The helper does not preserve a late successful
  value for the original caller or a job record; it discards that value, while logging an
  ordinary late exception.
- **Fail loudly on store misuse.** Negative TTLs, unknown/non-running initial
  statuses, attempts to change lifecycle fields through generic updates, unknown job
  IDs, and cleanup scheduling without a concrete expiry all raise immediately.
  Canonical timestamps and artifact handles are consumed directly rather than
  repaired or skipped as older record shapes.

## Interactions

Consumers (own their route-specific job semantics on top of this component):

- [modelling](../modelling/high-level.md) — training jobs, one `CancellableJobRegistry`
  per training-job namespace.
- [optimiser](../optimiser/high-level.md) — solve, frontier auto-range, and
  graph-node-setup jobs; the heaviest user of both `CancellableJobRegistry` (three
  independent registries) and `SingleFlightCoordinator` (one per graph/node key).
- [explore-eda](../explore-eda/high-level.md) — EDA jobs.
- [server-api input cache](../server-api/high-level.md) — owns the `input_cache`
  store/lifecycle/registry and a `SingleFlightCoordinator` per source-identity digest;
  joins an active same-identity build and repairs stale ownership before starting.
- [pipeline, json-cache, and output-assemble routes](../server-api/high-level.md)
  — use admitted killable workers for heavy execution; bounded I/O-only helpers may still use
  `_timeouts.py`. Explore uses the job store with a parent supervisor and isolated child.

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
  the running-job metrics publisher also lets this failure propagate instead of
  silently dropping evidence of inconsistent lifecycle state.
  `require_completed_job` raises HTTP 400 (with the actual status in the message) if
  the job exists but isn't `completed`.
- **Invalid job status or transition.** Creation accepts only `running`; unknown
  statuses, generic status writes, unsupported terminal destinations, and invalid
  completed-record corrections raise `ValueError` at the job boundary before state
  changes. `require_job_status` applies the same validation to external mappings.
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
- **Cooperative cancellation for in-process work.** `BackgroundJobStoppedError` is
  raised inside legacy thread worker code when it observes its own job has been
  cancelled or superseded; a thread worker that never checks
  `CancellableJobRegistry.cancellation_reason` can keep running. Supervised process
  workers additionally poll the registry in the parent and are terminated and joined
  when a stop reason appears.
- **Isolated worker failures.** Every `IsolatedWorkerError` subtype (crash, remote
  exception, timeout, memory limit, contract violation) is caught by
  `IsolatedJobSupervisor` and turned into a terminal transition with diagnostic
  fields (`worker_error_class`, `worker_error_type`/`worker_remote_traceback` for
  remote exceptions, `worker_exitcode` for crashes, `error_code` for memory limits).
  A child that curated a user-facing failure message marks it on the payload's
  `user_message` field, and the supervisor uses that curated wording as the job's
  terminal message; failures without one keep the typed wrapper text. When the
  supervisor claims the `error` field for that wrapper text, a child-supplied
  `error` string is not lost — it moves to `worker_error`. The crash and timeout
  wrappers — the surfaces that can only be parent-authored, since a crashed or
  timed-out child left no payload to curate — are themselves written as
  user-facing wording: a hedged may-have-run-out-of-memory phrasing when the
  exit code looks memory-limited (the heuristic is indicative, not proof), an
  unexpected-stop phrasing otherwise, each carrying the exit code when
  available; and a stopped-after-its-time-limit phrasing naming the limit for
  timeouts.
  An unrecognised reason string coerces to `error` rather than raising, so the job
  still reaches a terminal state.

  The supervisor also catches unexpected parent-side exceptions and attempts an
  `error` transition. If the terminal write itself cannot be persisted or verified,
  `IsolatedSupervisorThread.infrastructure_failure` and `join_and_raise()` expose a
  typed `SupervisorInfrastructureError`; the failure is not left only on stderr.
- **Training child boundary.** Request validation and pipeline materialisation remain
  in the parent with its admitted context; fit/evaluation/model-write and dispersion
  search run in spawn children using plain requests and staged artifacts. Only a
  validated manifest may complete the job, and the parent retains publication,
  lifecycle, admission, and cleanup ownership.

- **Background work outliving its HTTP response.** If a client-facing response
  timeout fires, or the request itself is cancelled, the background thread is neither
  killed nor left with an unobserved task exception: its eventual result or exception is
  drained. The ordinary timeout callback discards success and logs an `Exception`; a
  non-`Exception` `BaseException` still follows asyncio's callback reporting. The
  request-cancellation path instead consumes and logs every worker `BaseException`
  through `_drain_cancelled_future_result`, then re-raises the original
  `CancelledError` so a worker failure cannot mask request cancellation.
