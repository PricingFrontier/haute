# Background Jobs — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/_artifact_housekeeping.py` | Creates versioned ownership markers for crash-surviving artifact directories and safely reaps only stale, marked, direct children of an explicitly supplied root. |
| `src/haute/_worker_protocol.py` | Owns the bounded version-1 spawn request, progress, result, failure, and artifact-manifest transport plus parent-side validation and cleanup. |
| `src/haute/routes/_job_store.py` | Thread-safe, TTL-evicting, dict-backed `JobStore`; per-prefix singleton factory `get_job_store`; artifact-cleanup hook registry. |
| `src/haute/routes/_job_lifecycle.py` | `JobLifecycle.transition()` — the single race-safe path from `running` to a terminal status, with reason precedence; `require_job_status`; `bind_running_execution_metrics_publisher`. |
| `src/haute/routes/_background_jobs.py` | `CancellableJobRegistry` (latest-wins supersession + cooperative cancellation), `SingleFlightCoordinator` (mutual exclusion per key), `IsolatedJobSupervisor` (isolated-worker → lifecycle adapter), `BackgroundJobStoppedError`. |
| `src/haute/routes/_timeouts.py` | `run_blocking_with_response_timeout` / `BlockingWorkTimeoutError` — bounds HTTP response latency for thread-backed blocking work without abandoning it. |

## Key types and data structures

### `_job_store.py`

- **`JobStore`** — wraps `_jobs: dict[str, dict[str, Any]]` plus `_running_activity_at:
  dict[str, float]` (per-job last-active timestamp, used only while `status ==
  "running"`), guarded by a single `_write_lock: threading.RLock`. Invariant: every
  inserted job has a numeric `created_at`; a completed job with retained heavy objects has
  a numeric `completed_at`; and every value in optional `artifact_handles` is a current
  typed handle dict. Internal consumers access those required fields directly and do not
  support missing-timestamp or malformed-handle record variants. Every
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
- **`_KNOWN_PREFIXES: frozenset[str] = {"training", "optimiser", "explore", "input_cache"}`** — the
  closed allow-list behind `get_job_store`, a `functools.cache`d factory returning one
  `JobStore` singleton per prefix for the life of the process.

### `_job_lifecycle.py`

- **`TerminalReason`** — `Literal["completed", "superseded", "timed_out",
  "cancelled", "memory_limited", "contract_error", "error"]`.
- **`TERMINAL_REASONS`** — the closed terminal-status set. A terminal reason is
  stored directly as `status`; there is no parallel identity map or alias constant
  layer.
- **`_TERMINAL_REASON_PRECEDENCE`** — `error=10 < contract_error=20 <
  memory_limited=30 < cancelled=40 < timed_out=50 < superseded=60`. Higher wins a
  race between two *already-terminal* reasons; `completed` is not in this map because
  it is handled as a separate, non-precedence special case (see Control flow).
- **`JobLifecycle`** — a frozen dataclass wrapping one `store: JobStore` and an optional
  test-only terminal-transition `fault_injector`. It is a
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
  (`{"running", *TERMINAL_REASONS}`), which matches `haute.schemas.JobStatus`
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
- **`IsolatedJobSupervisor`** — wraps one `JobLifecycle`. `launch_protocol()` is the
  production entry point for the bounded version-1 worker transport; `launch()` keeps
  the legacy single-result isolation primitive available and tested. Both return a
  started daemon `IsolatedSupervisorThread`.
- **`BackgroundJobStoppedError(RuntimeError)`** — carries `job_id` and the canonical
  `terminal_reason`; it does not duplicate that value under a second status attribute.

### `_timeouts.py`

- **`BlockingWorkTimeoutError(TimeoutError)`** — carries `background_task:
  asyncio.Future[Any]`, the still-running task. Supersession and route handlers use
  this handle to defer admission/permit release until the underlying thread really
  finishes, even though the HTTP response has already timed out.

### `_worker_protocol.py`

- **`WorkerRequest`** — immutable version-1 request with non-empty bounded
  `request_id`/`kind` and one recursively bounded plain-data payload.
- **`WorkerProgressEvent` / `WorkerProgressEnd`** — monotonic delivered sequence,
  finite progress, bounded fields/message, and explicit counts for updates dropped by
  the non-blocking bounded progress queue.
- **`WorkerArtifactManifest` / `WorkerResultManifest`** — relative normalized
  artifact paths with kind/lifetime, size, SHA-256, and bounded plain result metadata.
  No absolute path, open object, dataframe, callback, store, or cancellation token may
  cross the spawn boundary.
- **`WorkerFailurePayload`** — a versioned known terminal reason plus bounded
  error-type/message/traceback and plain fields.
  Version-1 bounds are: queue capacity 64; at most 10,000 delivered events;
  64 KiB per event; 4 MiB result metadata; 64 artifacts; 512-character identifiers
  and messages; 4,096-character relative paths; nesting depth 64.

### `_artifact_housekeeping.py`

- **Ownership marker** — `.haute-artifact.json` with `schema_version=1`, non-empty
  owner, and finite non-negative creation time.
- **`create_owned_artifact_directory` / `reap_stale_artifact_directories`** —
  create marked direct children and reap only stale, valid, expected-owner children
  under an explicit root. Results report bounded counts and reclaimed bytes.

## Control flow

### `JobLifecycle.transition(job_id, *, to, message, fields, expected_status="running", elapsed_seconds, now)`

1. Compute `update` dict: merges `fields`, sets `status=to`,
   `terminal_reason=to`, `ended_at=now`; sets
   `completed_at` (via `setdefault`, so only stamped once) when `to == "completed"`;
   optionally sets `message`/`elapsed_seconds`.
2. Acquire `store._write_lock` and read the current stored job (`old =
   store.jobs[job_id]`; raises `KeyError` if the id doesn't exist — no guard, by
   design, since every caller already holds a job id it created).
3. Validate the optimistic-lock state: `expected_status` may be only `"running"` or
   `"completed"`; the latter is valid only with `to="error"`. Any other value or
   destination raises `ValueError`.
4. **Fast path** — if `old["status"] == expected_status` (default `"running"`): merge
   and store via `store._store_merged_job_locked`, return the merged dict. This is how
   the sole completed-to-error publication correction succeeds.
5. **Race path** — if the status has already moved past `expected_status` (another
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
6. After releasing the lock, call `store._schedule_heavy_object_cleanup_if_needed`
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
this function ignores). A missing job or store failure from `require_job` propagates;
the publisher does not silently suppress corrupted lifecycle state.

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

**TTL eviction** (`_evict_stale_locked`, invoked by public paths inside
`_write_locked_with_artifact_cleanup`):

1. `_clear_expired_heavy_objects_locked(now)` — for every completed job with expired
   heavy objects, calls `_clear_heavy_objects_locked` to build and atomically swap a
   replacement dict without the heavy keys, remove the expiry stamp, and add
   `heavy_objects_cleared_at=now` plus
   `heavy_objects_retention_seconds=heavy_object_ttl_seconds`.
2. Computes `cutoff = now - ttl_seconds` and evicts every job whose
   `_job_eviction_timestamp_locked` is older than cutoff.
   `_job_eviction_timestamp_locked` returns `_running_activity_at.get(job_id,
   created_at)` for a still-`"running"` job (so an actively-updated long-running job
   is not evicted mid-flight even past `created_at + ttl_seconds`), or plain
   `created_at` for anything else.
3. `_remove_job_locked` pops the job, drops its `_running_activity_at` entry, cancels
   the pending heavy-object timer, and appends detached `(job_id, handles)` work to
   the collector supplied by the enclosing context manager. `_evict_stale_locked`
   appends each stale job's work to that same collector.
4. `_write_locked_with_artifact_cleanup` releases `_write_lock`, then drains the
   shared collector through `_run_artifact_cleanups` from its `finally` block. Cleanup
   therefore runs on success and on exceptional public write paths, while recursive
   filesystem deletion never holds the global store lock. Missing cleaner
   registrations and cleaner exceptions are logged and isolated to their handle.

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

The input-cache route uses a second coordinator keyed by the versioned safe
source-identity digest. Under its start lock it checks `active`, verifies the
referenced job is still running, and joins it; stale coordinator ownership is
released/repaired before create/register/acquire, so two builders cannot start for
one identity. Its `finally` releases registry/coordinator ownership without deleting
the published generation.

### `IsolatedJobSupervisor.launch(...)` / `launch_protocol(...)`

`launch_protocol()` is the production path: it runs a versioned `WorkerRequest`
through `run_worker_protocol`, drains bounded progress events, validates the result
and artifact manifests, and maps the validated result through the caller's
`completed_fields`. `launch()` retains the smaller `run_isolated_worker` single-result
primitive for supported direct callers and its focused tests. Both delegate to
`_launch_callable` and return an `IsolatedSupervisorThread`.

The thread executes one total outcome pipeline:

1. `_produce_outcome` maps success or a typed `IsolatedWorkerError` to a
   `_SupervisorOutcome`. Any other `BaseException` becomes an `error` outcome with a
   bounded generic message plus `worker_error_class`/`supervisor_error_class`; the
   original exception is retained as `exception_to_report`.
2. `_finish_outcome` runs the parent completion/cleanup callback. A cleanup failure
   is retained as `cleanup_error`/`cleanup_error_class` without changing the worker
   outcome, and is logged with its job ID and traceback for operator diagnosis. In
   particular, cleanup after a committed completed publication cannot discard its
   result or turn success into `error`.
3. `_persist_terminal_outcome` attempts the lifecycle transition once and verifies
   that a precedence-rejected write still left the job terminal.
4. A missing/unverifiable job or failed terminal write is recorded on
   `IsolatedSupervisorThread.infrastructure_failure` as
   `SupervisorInfrastructureError`; `join_and_raise()` is the caller-visible raising
   join. If persistence succeeds, an unexpected parent exception is re-raised from
   the thread only after the job is safely terminal, preserving diagnostic visibility
   without stranding it as running.

**Version-1 worker protocol.** The child serializes each progress update once and
attempts a non-blocking write to the capacity-64 queue; a full queue or exhausted
10,000-event delivery budget accumulates a drop count for the next event/end marker.
The parent drains events and the result while the child is alive, validates exact
delivered ordering and bounded plain data, then validates every artifact as a
non-symlink regular file contained under the resolved root with matching size/digest.
Unknown versions/kinds, malformed ordering/data, traversal, symlink escape, tampering,
and partial manifests become `contract_error`. Cancellation/timeout terminates and
joins the child before cleanup.

**Artifact restart housekeeping.** `create_owned_artifact_directory` validates a
single-component prefix and writes the ownership marker before returning the child.
`reap_stale_artifact_directories` enumerates direct children only and removes a child
only after non-following metadata, marker schema/owner/age, and inclusive stale-cutoff
checks pass. Per-child access/cleanup failures are counted and logged without widening
the sweep; symlinks, reparse points, unmarked/malformed/wrong-owner/fresh children and
the root itself are never removed.

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
   ignoring `CancelledError` on each iteration and breaking after any worker
   `BaseException`, until the shielded task is actually done — i.e. cancellation of
   the *caller* does not propagate until the background thread work has actually
   finished, because it cannot be forcibly stopped. Then
   `_drain_cancelled_future_result` consumes/logs every worker `BaseException` and
   re-raises the original `CancelledError` to the caller.
5. `_drain_background_future_result(future)` calls `future.result()` inside a
   `try/except Exception` — returns silently for success and therefore does not retain the
   value; logs `error` type/message and re-raises nothing for ordinary exceptions
   (`exc_info=True` on the log). A `BaseException` (e.g.
   `SystemExit`, `KeyboardInterrupt`) is *not* caught by the `except Exception` clause
   and propagates out of the callback/point of use instead of being logged.
6. `_drain_cancelled_future_result` is the cancellation-only drain: it catches/logs
   `BaseException`, ensuring a worker `SystemExit`, `KeyboardInterrupt`, or ordinary
   error cannot replace the request's `CancelledError`.

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
  `"error"`) through the explicit publication-correction transition cancels any
  pending heavy-object cleanup timer for that job.
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
| `BackgroundJobStoppedError(RuntimeError)` | Not raised by this component itself — it is the typed exception in-process worker code is expected to raise after observing `CancellableJobRegistry.cancellation_reason(job_id)` is non-`None`. | Caught by remaining in-process consumers such as `_optimiser_service.py`; migrated process workers use the protocol stop callback and typed failure payloads. |
| `IsolatedWorkerError` and subtypes | Raised inside `run_isolated_worker` or `run_worker_protocol` (owned by worker isolation/transport) | Converted by `IsolatedJobSupervisor` into a typed lifecycle outcome. Unexpected parent exceptions become `error`; terminal-persistence failure is exposed through `IsolatedSupervisorThread.join_and_raise()`. |
| `BlockingWorkTimeoutError(TimeoutError)` | `run_blocking_with_response_timeout` | Raised to the awaiting route handler on response timeout; route code (`src/haute/routes/pipeline.py`, `json_cache.py`, `output_assemble.py`) catches it to build a 504 response. |
| `asyncio.CancelledError` | Re-raised by `run_blocking_with_response_timeout` after draining the background task | Propagates to the ASGI layer as normal task cancellation. |

## Testing

- `tests/test_partial_failure.py` covers partial-failure handling.
- `tests/test_state_transitions.py` covers background-job state transitions.

- `tests/test_job_store.py` — the largest suite; unit-tests CRUD, TTL eviction
  (including exact-boundary and missing-`created_at` cases), artifact-handle cleanup
  on eviction (success, missing cleaner registration, cleaner failure),
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
  stickiness against precedence races and the sole completed-to-error publication
  correction; exhaustive pairwise
  precedence races across all six non-completed reasons
  (`itertools.combinations`), in both orderings; the running-execution-metrics
  publisher (fires on pressure events while running, ignored once terminal); and one
  `CancellableJobRegistry` case (`register_latest` derives `"superseded"` as the
  stop reason for the previous job). Direct `SingleFlightCoordinator` tests cover
  same-owner idempotence, concurrent conflicting acquire with exactly one winner,
  and stale-release protection.
- `tests/test_execution_context.py` (background-jobs-relevant subset) — confirms
  `CancellableJobRegistry.register_latest` actually cancels the *previous* job's
  `ExecutionContext` checkpoint (raises `ExecutionCancelledError`) and that a
  caller-supplied `ExecutionCancellationToken` is used as-is rather than replaced.
- `tests/test_worker_isolation.py` — `IsolatedJobSupervisor` end-to-end: completed
  result recording, typed remote/stopped/crashed outcomes, totalisation of unexpected
  parent errors, cleanup behavior, coherent terminal precedence, and observable
  terminal-persistence infrastructure failure.
- `tests/test_worker_protocol.py` — the bounded version-1 transport: spawn-safe
  request/result payloads, progress ordering and drops, validation limits,
  cancellation/timeout/crash cleanup, and artifact containment/integrity.
- `tests/test_artifact_housekeeping.py` — ownership markers, direct-child and
  symlink/reparse containment, stale-cutoff semantics, malformed markers, and
  isolated cleanup failures.
- `tests/test_input_cache_route.py` — input-cache job namespace, same-identity join,
  different-identity concurrency, cancellation checkpoints, and release of the
  route's `SingleFlightCoordinator` ownership.
- `tests/test_timeout_helper_contracts.py` — `run_blocking_with_response_timeout`
  returning a worker result, re-raising a worker exception unchanged, logging +
  raising `BlockingWorkTimeoutError` on timeout, and — using real
  `threading.Event`-synchronised threads — that cancelling the awaiting task waits
  for the started blocking work to actually finish before the `CancelledError`
  propagates even if the worker itself raises. Also covers the distinct drain
  policies: `_drain_background_future_result` logs ordinary timeout-path exceptions,
  while `_drain_cancelled_future_result` consumes every worker `BaseException` without
  masking request cancellation.
- Indirect coverage: `tests/test_optimiser_routes.py` exercises
  `BackgroundJobStoppedError` with the registry/lifecycle combination; modelling
  process-worker behavior is covered through the protocol-specific suites.
