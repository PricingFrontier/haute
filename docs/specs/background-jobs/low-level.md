# Background Jobs — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/routes/_job_store.py` | Thread-safe, TTL-evicting, dict-backed `JobStore`; per-prefix singleton factory `get_job_store`; artifact-cleanup hook registry. |
| `src/haute/routes/_job_lifecycle.py` | `JobLifecycle.transition()` — the single race-safe path from `running` to a terminal status, with reason precedence; `require_job_status`; `bind_running_execution_metrics_publisher`. |
| `src/haute/routes/_background_jobs.py` | `CancellableJobRegistry` (latest-wins supersession + cooperative cancellation), `SingleFlightCoordinator` (mutual exclusion per key), `IsolatedJobSupervisor` (isolated-worker → lifecycle adapter), `BackgroundJobStoppedError`. |
| `src/haute/routes/_timeouts.py` | `run_blocking_with_response_timeout` / `BlockingWorkTimeoutError` — bounds HTTP response latency for thread-backed blocking work without abandoning it. |

## Key types and data structures

### `_job_store.py`

- **`JobStore`** — wraps `_jobs: dict[str, dict[str, Any]]` plus `_running_activity_at:
  dict[str, float]` (per-job last-active timestamp, used only while `status ==
  "running"`), guarded by a single `_write_lock: threading.RLock`. Invariant: every
  store mutation method replaces the job's dict object wholesale
  (`_store_merged_job_locked`) — never mutates an existing dict in place — so a reader's
  previous reference is never torn by those methods.
- **`ArtifactCleaner`** — `Callable[[dict[str, Any]], None]` registered per artifact
  `kind` in the module-level `_ARTIFACT_CLEANERS` dict via `register_artifact_cleaner`.
  Registering two *distinct* callables for the same `kind` raises `RuntimeError`;
  re-registering the identical callable object is a no-op (identity check, not `==`,
  specifically to reject two "equal" callables from different registration sites —
  see `TestJobStoreTTL::test_distinct_equal_artifact_cleaners_are_rejected`).
- **Heavy-object keys** — `_HEAVY_OBJECT_KEYS = ("solver", "solve_result",
  "quote_grid", "factors_df", "ratebook_factor_contexts")`. Only jobs with
  `status == "completed"` are ever eligible to have these keys stripped.
  `_HEAVY_OBJECT_EXPIRES_AT_KEY = "heavy_objects_expires_at"` is the stamped field
  that pins when stripping becomes due; once set it is not recomputed on subsequent
  updates (`_prepare_heavy_object_policy_locked` returns early if the key already
  exists).
- **`_KNOWN_PREFIXES: frozenset[str] = {"training", "optimiser", "explore"}`** — the
  closed allow-list behind `get_job_store`, a `functools.cache`d factory returning one
  `JobStore` singleton per prefix for the life of the process.

### `_job_lifecycle.py`

- **`TerminalReason`** — `Literal["completed", "superseded", "timed_out",
  "cancelled", "memory_limited", "contract_error", "error"]`.
- **`TERMINAL_REASON_TO_STATUS`** — maps each reason to its stored `status` string
  (currently identity-mapped 1:1, i.e. reason and status share the same literal
  spelling).
- **`_TERMINAL_REASON_PRECEDENCE`** — `error=10 < contract_error=20 <
  memory_limited=30 < cancelled=40 < timed_out=50 < superseded=60`. Higher wins a
  race between two *already-terminal* reasons; `completed` is not in this map because
  it is handled as a separate, non-precedence special case (see Control flow).
- **`JobLifecycle`** — a frozen dataclass wrapping one `store: JobStore`. It is a
  privileged collaborator: `transition()` reaches into `store._write_lock`,
  `store.jobs`, `store._store_merged_job_locked`, and
  `store._schedule_heavy_object_cleanup_if_needed` directly (marked `# noqa: SLF001`
  at each call site). This tight coupling is intentional — `JobLifecycle` is meant to
  be the only code outside `JobStore` itself allowed to perform a guarded terminal
  transition, mirroring the project's closed-prefix discipline for `JobStore`
  construction.
- **`require_job_status(job)`** — returns the job's `status` cast to `JobStatus` after
  validating it is a string present in `JOB_STATUSES`; raises `ValueError` otherwise.
  Note this validates against the *lifecycle's* closed status set
  (`{"running", *TERMINAL_STATUSES}`), which matches `haute.schemas.JobStatus`
  exactly — but `JobStore` itself does not enforce this set on arbitrary
  `create_job`/`update_job` calls (e.g. tests freely use `"pending"`); only code paths
  that go through `require_job_status` (the metrics publisher) are constrained.

### `_background_jobs.py`

- **`JobCancellation`** (slotted dataclass) — one active coordination record: `job_id`,
  `key: Hashable`, `event: threading.Event`, `execution_token:
  ExecutionCancellationToken`, `terminal_reason: TerminalReason | None`. `cancel(reason)`
  writes `terminal_reason`, then sets the event, then cancels the execution token. Registry
  calls and registry reads are serialised by `CancellableJobRegistry._lock`, but the three
  field writes are not themselves an atomic machine operation; a holder that bypasses the
  registry and inspects the returned `JobCancellation` directly can observe an intermediate
  state.
- **`CancellableJobRegistry`** — `_latest_by_key: dict[Hashable, str]` (key → current
  job id) and `_tokens_by_job_id: dict[str, JobCancellation]`, both guarded by one
  `RLock`. Deliberately owns *only* runtime coordination state — job status/result
  metadata stays in `JobStore` so there is one source of truth for what a job's
  observable state is.
- **`SingleFlightHandle`** (frozen slotted dataclass) — `key`, `job_id`, `kind`, the
  value type returned by `SingleFlightCoordinator.acquire`/`.active`.
- **`SingleFlightConflictError(RuntimeError)`** — carries `key`, `active_job_id`,
  `active_kind` for the caller to build a specific conflict message/response.
- **`SingleFlightCoordinator`** — `_active_by_key: dict[Hashable, SingleFlightHandle]`
  guarded by one `RLock`.
- **`IsolatedJobSupervisor`** — wraps one `JobLifecycle`. Stateless beyond that
  reference; `launch()` starts a new daemon `threading.Thread` per call.
- **`BackgroundJobStoppedError(RuntimeError)`** — carries `job_id`, `terminal_reason`,
  and a `status` attribute that duplicates `terminal_reason` (kept for call sites that
  read either name).

### `_timeouts.py`

- **`BlockingWorkTimeoutError(TimeoutError)`** — carries `background_task:
  asyncio.Future[Any]`, the still-running task, so a caller could in principle inspect
  or await it later (current call sites only use the exception for its message).

## Control flow

### `JobLifecycle.transition(job_id, *, to, message, fields, expected_status="running", elapsed_seconds, now)`

1. Compute `update` dict: merges `fields`, sets `status` (from
   `TERMINAL_REASON_TO_STATUS[to]`), `terminal_reason=to`, `ended_at=now`; sets
   `completed_at` (via `setdefault`, so only stamped once) when `to == "completed"`;
   optionally sets `message`/`elapsed_seconds`.
2. Acquire `store._write_lock` and read the current stored job (`old =
   store.jobs[job_id]`; raises `KeyError` if the id doesn't exist — no guard, by
   design, since every caller already holds a job id it created).
3. **Fast path** — if `old["status"] == expected_status` (default `"running"`): merge
   and store via `store._store_merged_job_locked`, return the merged dict.
4. **Race path** — if the status has already moved past `expected_status` (another
   transition won first):
   - If the old status has no valid `terminal_reason` recorded, return `None`
     (nothing to compare against — treated as "can't safely proceed").
   - If the old reason is `"completed"`, or the *new* `to` is `"completed"`, return
     `None` — completed is a one-way door in both directions: you can't overwrite a
     completed job, and you can't "complete" a job that already terminated for a
     different reason.
   - Otherwise compare `_TERMINAL_REASON_PRECEDENCE[to]` against
     `_TERMINAL_REASON_PRECEDENCE[old_reason]`. If `to`'s precedence is not strictly
     greater, return `None` (a lower-or-equal-precedence reason loses silently — the
     caller gets `None` back to detect the race, not an exception). Otherwise merge
     and store, same as the fast path.
5. After releasing the lock, call `store._schedule_heavy_object_cleanup_if_needed`
   with the `schedule_cleanup`/`expires_at` values returned by the merge step (see
   `JobStore` control flow below) — heavy-object timer scheduling always happens
   outside the write lock.

`bind_running_execution_metrics_publisher(store, job_id, execution_context)` closes
over a `weakref.ref` to the `ExecutionContext` (does not keep it alive) and installs
itself as `execution_context.memory_pressure_callback`. On each memory-pressure event
it re-reads the job, calls `require_job_status`, and if the job is still `"running"`,
writes `execution_metrics` via `store.atomic_update(..., expected_status="running")`
— itself a guarded write, so a job that terminated between the pressure event firing
and the write landing is left alone (the `atomic_update` call returns `None`, which
this function ignores).

### `JobStore` mutation paths

`update_job`, `atomic_update`, `atomic_update_if_heavy_present`, and lifecycle transitions
funnel through `_store_merged_job_locked(job_id, old, fields, now)` while holding
`_write_lock`. `create_job` applies the same heavy-object/activity policy directly because
there is no old record to merge; `delete_job` and `clear_result_data` have dedicated locked
paths.

1. `merged = {**old, **fields}` (shallow merge — a plain dict "swap in a new object",
   not a deep merge; nested dict/list values in `fields` fully replace the
   corresponding old values).
2. `_prepare_heavy_object_policy_locked(merged, now)` — if `merged["status"] ==
   "completed"`: stamps `completed_at` via `setdefault`; if any `_HEAVY_OBJECT_KEYS`
   key is present and `heavy_objects_expires_at` isn't already stamped, stamps it
   (`completed_at + heavy_object_ttl_seconds`) and reports `schedule_cleanup=True`.
   If no heavy keys are present, clears any stale expiry stamp. Non-completed jobs are
   a no-op here.
3. Swap `self._jobs[job_id] = merged` (single dict `__setitem__` — GIL-atomic).
4. `_record_running_activity_locked` — stamps `_running_activity_at[job_id] = now` if
   still `"running"`, else evicts that entry (so a job that just went terminal
   immediately reverts to `created_at`-based TTL accounting).
5. If the merged status isn't `"completed"`, or it is but no heavy keys remain,
   cancel any pending heavy-object cleanup `Timer` for this job (`
   _cancel_heavy_object_timer_locked`) — this runs on *every* update, not just
   heavy-object-related ones, so e.g. re-opening a completed job back to `"error"`
   (via a later lifecycle correction) always tears down a stale timer rather than
   letting it fire against a job it no longer applies to.
6. Return `(merged, schedule_cleanup, expires_at)` to the caller, which — outside the
   lock — calls `_schedule_heavy_object_cleanup_if_needed`.

`_schedule_heavy_object_cleanup(job_id, expires_at)` builds a `Timer` via the
injectable `_heavy_object_timer_factory` (production default `threading.Timer`; tests
inject a manual, synchronously-fireable stand-in), then re-acquires the lock to
*validate* the schedule is still current (job still exists, still completed, still
has heavy keys, and `expires_at` still matches what's stamped) before actually
starting the timer and recording it in `_heavy_object_timers`. If validation fails
the timer object is simply never started (never added to `_jobs` bookkeeping) — a
deliberate check-after-build pattern so a fast-moving sequence of updates between
"decide to schedule" and "actually schedule" can't leave a stale timer active.

**TTL eviction** (`_evict_stale`, invoked at the top of `create_job` and `get_job`,
and inside `touch_heavy_objects` / `has_job_with_status` / `has_job_matching`):

1. `_clear_expired_heavy_objects_locked(now)` — for every completed job with expired
   heavy objects, strips them in place (see below).
2. Computes `cutoff = now - ttl_seconds` and evicts every job whose
   `_job_eviction_timestamp_locked` is older than cutoff.
   `_job_eviction_timestamp_locked` returns `_running_activity_at.get(job_id,
   created_at)` for a still-`"running"` job (so an actively-updated long-running job
   is not evicted mid-flight even past `created_at + ttl_seconds`), or plain
   `created_at` for anything else.
3. `_remove_job_locked` pops the job, drops its `_running_activity_at` entry, runs
   `_cleanup_artifact_handles` (calls the registered `ArtifactCleaner` for each
   `artifact_handles` entry whose `kind` has one; logs a warning and skips entries
   with no registered cleaner or malformed/non-dict handles; logs and swallows any
   exception the cleaner itself raises, always with `exc_info=True`), and cancels any
   pending heavy-object timer.

`touch_heavy_objects(job_id, required_keys=...)` lets a consumer that just
successfully read a completed job's heavy state extend its retention window: it
evicts stale jobs first, returns `False` if the job doesn't exist, isn't completed,
or is missing any `required_keys` (the caller is expected to keep raising its own
domain error in that case — this method never fabricates or restores cleared data),
otherwise computes `min(now + heavy_object_ttl_seconds, created_at + ttl_seconds)`
(never extends past the job's own metadata TTL) and, only if that's strictly later
than the current expiry, updates the stamp and reschedules the cleanup timer.

**`atomic_update` / `atomic_update_if_heavy_present`** both take an optional
`expected_status` compare-and-swap guard: if the stored status doesn't match, the
call returns `None` without writing (a caller-visible "your read was stale" signal,
not an exception).  `atomic_update_if_heavy_present` additionally returns `None` if
any `required_keys` heavy object is already absent, so a route committing a result
after the heavy-object timer has already fired sees a clean `None` rather than
silently merging partial state onto a stripped job.

### `CancellableJobRegistry`

- `register_latest(key, job_id, execution_token=None)` — under the lock: looks up the
  previous job id for `key`; if one exists and still has a token, calls
  `previous.cancel("superseded")` (sets its event + terminal_reason + cancels its
  execution token); then unconditionally overwrites `_latest_by_key[key] = job_id`
  and stores the new `JobCancellation`. Returns `(new_token, previous_job_id |
  None)`. A caller-supplied `execution_token` is used as-is (letting the registry
  share a token the caller already created for its `ExecutionContext`); otherwise a
  fresh one is minted.
- `cancel(job_id, reason="cancelled")` — looks up the token by job id (not by key);
  returns `False` if there is none (already released or never registered).
- `is_cancelled` / `cancellation_reason` — pure reads under the lock; the latter
  returns `None` unless `token.cancelled` is true, even if a `terminal_reason` was
  somehow set without the event (not reachable through the public API, since `cancel`
  always sets both together).
- `release(job_id)` — pops the token; if `_latest_by_key[token.key]` still points at
  this `job_id`, removes that mapping too (so a *stale* `job_id` being released after
  a newer one has already superseded it does not accidentally clear the newer job's
  "latest" registration).

### `SingleFlightCoordinator`

- `acquire(key, job_id, kind)` — under the lock: if an active handle exists for `key`
  with a *different* `job_id`, raises `SingleFlightConflictError`. If the active
  handle already belongs to the same `job_id` (idempotent re-acquire) or there is no
  active handle, stores/overwrites the handle and returns it.
- `release(key, job_id)` — removes the active handle only if it's still owned by this
  `job_id` (a stale release from an already-superseded owner is a no-op, not an
  accidental release of a newer owner's key — same pattern as
  `CancellableJobRegistry.release`).

In practice (`_optimiser_service.py`), callers check `SingleFlightCoordinator.active`
themselves *before* calling `acquire`, under their own separate `_start_lock`, and
raise their own typed HTTP 409 directly from that check — `acquire`'s own
`SingleFlightConflictError` mostly guards against the acquire call itself racing
with a concurrent acquire for the same key from a different job id, which the
caller-side check does not fully cover once the caller's lock is released.

### `IsolatedJobSupervisor.launch(job_id, function, *args, config=None,
completed_message="Completed", **kwargs)`

1. Records `start_time = time.monotonic()`.
2. Starts a daemon `threading.Thread` running an inner `run()`:
   - Calls `run_isolated_worker(function, *args, config=config, **kwargs)`.
   - On success: `self._lifecycle.transition(job_id, to="completed",
     message=completed_message, fields={"result": result}, elapsed_seconds=...)`.
   - On `IsolatedWorkerError`: `_transition_failure` maps the exception's own
     `terminal_reason` string through `_coerce_worker_terminal_reason` (falls back to
     `"error"` for any value not in the known `TerminalReason` set) and builds
     diagnostic `fields` via `_isolated_worker_failure_fields` — always `error` and
     `worker_error_class`; adds `worker_error_type` / `worker_remote_traceback` for
     `IsolatedWorkerRemoteError`; adds `worker_exitcode` for
     `IsolatedWorkerCrashedError`; adds `error_code="memory_limit"` when
     `terminal_reason == "memory_limited"`. Then calls `transition(...)` with that
     reason and fields.
3. Returns the started `Thread` object (callers in the test suite `.join()` it; route
   code treats it as fire-and-forget).

> NOTE: the `run()` closure's `try/except IsolatedWorkerError` is the *only* error
> boundary. Any exception of a different type escaping `run_isolated_worker` is
> unhandled inside the thread; Python's default behaviour for an unhandled exception
> in a `threading.Thread` target is to print it to stderr via the thread excepthook
> and let the thread exit — the job record is never touched, so it stays `"running"`
> indefinitely (until 24h metadata TTL eviction removes it, without ever going
> terminal).

### `run_blocking_with_response_timeout(func, *args, timeout, operation, **kwargs)`

1. Wraps `func(*args, **kwargs)` in `asyncio.to_thread(...)`, wrapped again in
   `asyncio.create_task(..., name=f"haute-route-{operation}")` — the task is created
   immediately, before any timeout logic, so the thread pool submission happens
   regardless of what follows.
2. `await asyncio.wait_for(asyncio.shield(blocking_task), timeout=timeout)` — the
   `shield` is what lets a timeout (or outer cancellation) *not* cancel the
   underlying task; only the `wait_for` wrapper is abandoned.
3. **On `TimeoutError`**: attaches `_drain_background_future_result` as a
   done-callback on the still-running task (a successful value is consumed and discarded;
   an ordinary exception is logged), logs a warning, and raises
   `BlockingWorkTimeoutError(operation, timeout,
   blocking_task)` — chained with `from None` to suppress the wrapped `TimeoutError`'s
   own traceback noise.
4. **On `asyncio.CancelledError`** (the calling coroutine itself was cancelled, e.g.
   client disconnect): loops `await asyncio.shield(blocking_task)`, re-catching and
   ignoring `CancelledError` on each iteration, until the shielded task is actually
   done — i.e. cancellation of the *caller* does not propagate until the background
   thread work has actually finished, because it cannot be forcibly stopped. Then
   drains the result (same helper as the timeout path) and re-raises the
   `CancelledError` to the caller.
5. `_drain_background_future_result(future)` calls `future.result()` inside a
   `try/except Exception` — returns silently for success and therefore does not retain the
   value; logs `error` type/message and re-raises nothing for ordinary exceptions
   (`exc_info=True` on the log). A `BaseException` (e.g.
   `SystemExit`, `KeyboardInterrupt`) is *not* caught by the `except Exception` clause
   and propagates out of the callback/point of use instead of being logged.

## Edge cases and invariants

- **Reads expose live mutable records, not snapshots.** `get_job` / `require_job` return the
  stored dict object and the `jobs` property exposes the backing mapping directly. A caller
  that mutates either bypasses `_write_lock`, read-merge-swap, running-activity bookkeeping,
  and heavy-object timer policy. Production consumers treat returned records as read-only
  (apart from `JobLifecycle`'s explicitly locked privileged access), so the concurrency
  guarantees assume all writes use the store/lifecycle mutation APIs.
- **Zero-second TTLs are valid.** `JobStore(ttl_seconds=0)` and
  `heavy_object_ttl_seconds=0` are accepted (only negative values raise); a
  zero heavy-object TTL makes heavy state due for stripping immediately on the next
  access after completion.
- **Missing `created_at` treated as epoch zero for TTL purposes**, not as "always
  fresh" or an error — `float(job.get("created_at", 0))`. A record inserted directly
  into `store.jobs` (bypassing `create_job`) without `created_at` is immediately
  eligible for eviction once `now > ttl_seconds`.
- **`atomic_update`/`update_job` raise `KeyError`** for an unknown job id — there is
  no forgiving "create if missing" behaviour; `create_job` is the only entry point
  that mints new records.
- **Dynamically-equal-but-not-identical `"completed"` strings** are treated
  identically to the literal — `_prepare_heavy_object_policy_locked` and the heavy
  timer bookkeeping compare by value (`==`), not by interned identity, and tests
  explicitly cover a `"".join(["com", "pleted"])`-constructed string to pin this.
- **A status change *away* from `"completed"`** (e.g. a later correction to
  `"error"`) cancels any pending heavy-object cleanup timer for that job, even though
  no code path in this component currently transitions a job away from completed
  after the fact other than direct test manipulation of `atomic_update`.
- **Concurrent `atomic_update_if_heavy_present` vs. the heavy-object eviction timer**
  is explicitly exercised under real threads (`test_job_store.py::
  TestJobStoreConcurrency::test_atomic_update_if_heavy_present_serialises_against_ttl_eviction`):
  the end state is always self-consistent — either the update wins (heavy keys *and*
  new fields both present) or the evictor wins (heavy keys gone *and* the new fields
  were never merged), never a mix.
- **`atomic_update(expected_status=...)` is a true optimistic lock** under contention
  — of N threads racing to transition the same job out of a given status, exactly one
  succeeds and the rest observe `None`
  (`test_atomic_update_with_expected_status_is_optimistic_lock`).
- **`JobLifecycle.transition` returning `None` is not an error** — it's the
  documented way to signal "this write lost the race" or "this write was rejected by
  precedence"; callers must handle it explicitly rather than assume every transition
  call succeeds.
- **`SingleFlightCoordinator.release` and `CancellableJobRegistry.release`** are both
  guarded by "still owned by this id" checks specifically so a release from a job
  that has already been superseded/replaced cannot clobber the newer owner's
  registration — a release ordering bug (old job's cleanup running after a new job
  already started) fails safe rather than corrupting shared state.

## Error handling

| Exception | Raised by | Where it surfaces |
| --- | --- | --- |
| `ValueError` | `JobStore.__init__` (negative TTLs); `get_job_store` (unknown prefix); `require_job_status` (invalid/missing status) | Construction/lookup call sites; propagates to caller, not converted to HTTP by this component. |
| `KeyError` | `JobStore.update_job` / `atomic_update` / `atomic_update_if_heavy_present` (unknown job id); `JobLifecycle.transition` (via `store.jobs[job_id]`) | Propagates; callers generally only reach these with ids they created themselves. |
| `RuntimeError` | `register_artifact_cleaner` (duplicate distinct cleaner for a kind); `_schedule_heavy_object_cleanup_if_needed` (schedule requested without a numeric expiry) | Treated as internal-bug-level failures; not caught anywhere in this component. |
| `HTTPException(404)` | `JobStore.require_job` | Standard FastAPI error response for missing/expired job ids. |
| `HTTPException(400)` | `JobStore.require_completed_job` | When the job exists but isn't `completed`; message includes the actual status. |
| `SingleFlightConflictError(RuntimeError)` | `SingleFlightCoordinator.acquire` | Not caught inside this component; optimiser route code (`_optimiser_service.py`) converts the equivalent caller-side conflict into `HTTPException(409)`. |
| `BackgroundJobStoppedError(RuntimeError)` | Not raised by this component itself — it is the typed exception worker code is expected to raise after observing `CancellableJobRegistry.cancellation_reason(job_id)` is non-`None`. | Caught by consumer services (e.g. `_train_service.py`) to route cooperative-cancellation into the appropriate terminal transition. |
| `IsolatedWorkerError` and subtypes | Raised inside `run_isolated_worker` (owned by worker-isolation, not this component) | Caught exclusively by `IsolatedJobSupervisor._transition_failure`; any other exception type from the same call site is unhandled (see NOTE above). |
| `BlockingWorkTimeoutError(TimeoutError)` | `run_blocking_with_response_timeout` | Raised to the awaiting route handler on response timeout; route code (`pipeline.py`, `output_assemble.py`) catches it to build a 504 response. |
| `asyncio.CancelledError` | Re-raised by `run_blocking_with_response_timeout` after draining the background task | Propagates to the ASGI layer as normal task cancellation. |

## Testing

- `tests/test_job_store.py` — the largest suite; unit-tests CRUD, TTL eviction
  (including exact-boundary and missing-`created_at` cases), artifact-handle cleanup
  on eviction (success, missing cleaner, cleaner failure, malformed handles),
  `require_job` / `require_completed_job` status-code mapping, `atomic_update`
  semantics (merge, new-dict-per-write, optimistic-lock races under real threads),
  `clear_result_data`, and the full heavy-object lifecycle policy (default retention
  shorter than metadata TTL, zero-retention edge case, value-equality status checks
  for dynamically-built `"completed"` strings, active-timer scheduling/cancellation
  across every status transition, `touch_heavy_objects` window extension and
  timer replacement). Concurrency is exercised with real `threading.Barrier`-
  synchronised threads, not mocks, for the highest-risk races (concurrent creates,
  concurrent updates to the same/different jobs, the heavy-object-eviction-vs-update
  race, the optimistic-lock race).
- `tests/test_job_lifecycle.py` — transition metadata correctness; `completed`'s
  immutability against every other terminal reason; exhaustive pairwise
  precedence races across all six non-completed reasons
  (`itertools.combinations`), in both orderings; the running-execution-metrics
  publisher (fires on pressure events while running, ignored once terminal); and one
  `CancellableJobRegistry` case (`register_latest` derives `"superseded"` as the
  stop reason for the previous job).
- `tests/test_execution_context.py` (background-jobs-relevant subset) — confirms
  `CancellableJobRegistry.register_latest` actually cancels the *previous* job's
  `ExecutionContext` checkpoint (raises `ExecutionCancelledError`) and that a
  caller-supplied `ExecutionCancellationToken` is used as-is rather than replaced.
- `tests/test_worker_isolation.py` — `IsolatedJobSupervisor` end-to-end: completed
  result recorded correctly, a remote `ValueError` recorded with
  `worker_error_type`/message, and a stopped/cancelled run triggering its configured
  cleanup callback.
- `tests/test_timeout_helper_contracts.py` — `run_blocking_with_response_timeout`
  returning a worker result, re-raising a worker exception unchanged, logging +
  raising `BlockingWorkTimeoutError` on timeout, and — using real
  `threading.Event`-synchronised threads — that cancelling the awaiting task waits
  for the started blocking work to actually finish before the `CancelledError`
  propagates. Also covers `_drain_background_future_result` logging an ordinary
  exception and *not* swallowing a `BaseException` (`SystemExit`).
- Indirect coverage: `tests/test_optimiser_routes.py`, `tests/test_train_service_coverage.py`,
  and `tests/test_training_temp_cleanup.py` exercise `BackgroundJobStoppedError` and
  the registry/lifecycle combination from the consumer side (training/optimiser
  services), including local fixture stand-ins for the exception in a couple of
  files rather than importing it directly.
- **Known gaps**: no test directly exercises `SingleFlightCoordinator` in isolation
  (it is only exercised indirectly through `_optimiser_service.py`'s route-level
  409 tests); the NOTE'd unhandled-non-`IsolatedWorkerError` path in
  `IsolatedJobSupervisor.launch` has no regression test pinning the "job stuck at
  running forever" behaviour.
