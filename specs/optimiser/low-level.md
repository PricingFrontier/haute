# Optimiser — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/routes/optimiser.py` | FastAPI router (`/api/optimiser/*`). Owns request/response assembly, frontier-point selection, artifact-payload building/validation for save and MLflow log, and the module-level `_store`/`_solve_service` singletons. |
| `src/haute/routes/_optimiser_service.py` | `OptimiserSolveService` and its supporting free functions: pipeline execution, schema/value-contract validation, quote-grid construction, solver dispatch (online and ratebook), background frontier-auto-range estimation, and ratebook factor-table canonicalisation/serialisation. It owns no filesystem deletion. |
| `src/haute/routes/_optimiser_input.py` | Solve-input planning with no job or result knowledge: the column demand setup plans at each node (`_optimiser_solve_required_columns_by_node`, `_solve_columns_by_node`), the retained side inputs and execution target, exact data-input edge resolution (`_resolve_optimiser_input_edge`, `_resolve_optimiser_data_input_id`), the value-contract expressions and their failure details, solver-input chunk sizing (`_chunk_size_decision_for_parquet`), resident-grid admission (`_admit_resident_grid`), the projected-parquet borrow check, and `_find_optimiser_node`. The service's setup steps call them. |
| `src/haute/routes/_optimiser_artifacts.py` | The owned artifact lifecycle: the two ownership-marked artifact families (apply result, ratebook factors) — their roots, handle validation, persistence, loading, job-store cleaners, orphan cleanup and stale-startup reaping (`reap_stale_optimiser_artifacts`) — and setup's temporary files (the solver-input parquet, a worker's ratebook factors directory, the range reducer's spill directory). |
| `src/haute/routes/_optimiser_worker.py` | The hard-capped spawn workers that materialise optimiser inputs in process mode: `materialise_solve_input_worker` (solve setup's execute/validate/project/factor-extraction and the solver-input parquet) and `frontier_auto_range_worker` (the auto-range totals), their plain-data requests and outcomes, `SolveInput`, and `OptimiserWorkerFailure`, the child's terminal failure record that the parent replays onto the real job (raised there as `OptimiserWorkerFailureError`). |
| `src/haute/routes/_optimiser_limits.py` | Shared response-size and solver-compute budgets: `APPLY_PREVIEW_ROW_LIMIT`, `FRONTIER_POINT_LIMIT`, `FRONTIER_COMPUTE_LIMIT`, `enforce_frontier_compute_budget`, `limited_apply_preview_payload`, `limited_frontier_payload`. |
| `src/haute/routes/_frontier_point_summary.py` | The one derivation of a frontier point's solve summary: `frontier_point_summary` (from a `price-contour` frontier row), `apply_frontier_point_summary` (overlays it on a base result) and `FrontierPointDataError` (a malformed point, carrying the HTTP status the route reports). See Frontier point summaries below. |
| `src/haute/_builders.py` | Cross-component runtime registry owned by [execution-engine](../execution-engine/low-level.md). The optimiser component consumes its optimiser-apply online/ratebook closures; saved artifact validation and trace reconstruction must remain contract-compatible with those closures. |
| `src/haute/_optimiser_io.py` | Loads a previously saved optimiser artifact for an optimiser-apply node — from a local JSON file (content-hash cached) or from MLflow (`load_mlflow_optimiser_artifact(..., destination="")`, cached on the resolved backend identity plus run-id/version, so the same run on two destinations never aliases and an unresolvable destination fails before any cache lookup). Analogous to `_mlflow_io.py` and `_io.py`. |
| `src/haute/_optimiser_apply_explainability.py` | Builds a structured trace-detail payload for one clicked optimiser-apply output row, for both online and ratebook modes. Consumed by the tracing subsystem, not exposed as its own route. |
| `src/haute/schemas.py` | Shared Pydantic contracts owned by [server-api](../server-api/low-level.md) for optimiser solve/estimate/status, auto-range, frontier, apply, save, and MLflow-log routes. |
| `frontend/src/api/types.ts` | Canonical frontend optimiser response contracts owned by [frontend-shared](../frontend-shared/low-level.md), including `OptimiserSolveResult`; panels, stores, and tests import this type directly without panel-owned aliases. |

## Key types and data structures

### `OptimiserSolveService` (`src/haute/routes/_optimiser_service.py`)

Constructed once per process with the shared `JobStore` (`_store = get_job_store("optimiser")`
in `optimiser.py`). Instance state:

- `_lifecycle: JobLifecycle` — the single choke-point for CAS-guarded terminal-state
  transitions (`completed`/`error`/`cancelled`/`superseded`/`timed_out`/`contract_error`/
  `memory_limited`).
- `_start_lock: threading.Lock` — a coarse, process-wide lock held only across the global
  solve-slot check and/or graph-node registration sections of `start()` and
  `start_frontier_auto_range()` — not held for the duration of either job.
- `_jobs: CancellableJobRegistry` — the single cancellation-token registry for solve and
  background auto-range workers. Each job id is registered once and shares the same
  `ExecutionCancellationToken` with its execution context.
- `_graph_node_setup_singleflight: SingleFlightCoordinator` — the graph+node admission
  coordinator that rejects (409) overlap between solve setup and background auto-range setup.
  Worker runners hold its registration through a scoped `finally` release together with `_jobs`,
  so normal, exceptional, cancelled, and thread-start-failure paths cannot release only half of
  the coordination state.

### `SolveContext` (`src/haute/routes/_optimiser_service.py`, frozen dataclass)

Per-solve context threaded through `_solve_online`/`_solve_ratebook`: job id, node id, mode,
store, execution context, single-flight key, required worker `start_time`,
and a `check_cancelled` callable — the single object both solver code paths use to check for
cooperative cancellation. The orchestration context may omit `start_time` before
`_launch_background` resolves it, but both solver entry points fail loudly if they receive a
context without the resolved value rather than silently resetting elapsed-time accounting.

### Other dataclasses

- `_StreamingAutoRangePlan` (`src/haute/routes/_optimiser_service.py`, frozen) — a *proven*
  streaming/chunked auto-range plan:
  base node id, scenario-expander node id, the intermediate node chain, required columns, and a
  `ChunkPlan`. Only constructed when every intermediate node is verified row-local (see
  Edge cases below).
- `_ChunkSizeDecision` (`src/haute/routes/_optimiser_service.py`, frozen) —
  `(chunk_size, provenance)`, recording whether a chunk
  size came from explicit config or a byte-budget policy.
- `FrontierAutoRangeContext` (`src/haute/routes/_optimiser_service.py`, frozen) — per-job bundle
  of chunk size, partition count, and
  execution context for one auto-range run.
- `_ScenarioFrontierRangeAccumulator` (`src/haute/routes/_optimiser_service.py`) — a
  disk-bucketed accumulator that combines
  per-quote scenario min/max across many batches by hash-partitioning into parquet parts and
  combining them in `finish()`, so auto-range estimation never has to hold the full per-quote
  range set in memory at once.

No `TypedDict`s are defined anywhere in the component; job-store entries and artifact handles
are plain `dict[str, Any]`, validated defensively at each read site rather than at a type
boundary.

### Job dict shape

A job is a plain dict living in the shared `JobStore`. Fields this component reads or writes
(non-exhaustive, see [background-jobs](../background-jobs/high-level.md) for the store itself):
`status`, `job_type` (`"solve"` / `"estimate"` / `"frontier_auto_range"` /
`"frontier_recompute"`),
`progress`, `message`, `config`,
`node_label`, `start_time`, `timeout`, `result`, `base_result` (the pre-frontier-point-overlay
summary, used to reconstruct any frontier point without re-solving), `frontier_data` (the raw,
unlimited frontier points — distinct from `result["frontier"]`, which is the size-limited
frontend payload), `frontier_generation` (a non-negative integer initialised to `0` at solve
completion and incremented by every explicit recompute), `selected_frontier_point`,
`artifact_handles` (dict of named artifact handles, see below), and, only while heavy state is
retained, `solver`, `quote_grid`, `solve_result`, `factor_level_counts`, `factor_level_order`,
`setup_chunking`.

### Artifact handle shape

`{"kind": "optimiser_apply_result" | "optimiser_ratebook_factors", "version": 1, "format":
"parquet", "path": str, "directory": str, "row_count": int, ["size_bytes", "columns"]}`. Handles
are validated structurally on every load/cleanup (`_validate_server_owned_parquet_handle` in
`src/haute/routes/_optimiser_service.py`) — kind/version/format must match exactly,
`directory`/`path` must
be non-empty absolute strings with no NUL bytes, and after resolving symlinks the directory must
be a direct child of the component's own artifact root with the expected name prefix. This
prevents a tampered or foreign handle from causing a read/delete outside the artifact root.
`tests/test_optimiser_apply_artifacts.py` pins this contract directly (round-trip, path-outside-
root rejection, relative-path rejection, directory/file mismatch rejection).

### `OptimiserApplyTraceError` (`src/haute/_optimiser_apply_explainability.py`)

A `RuntimeError` subclass raised for every trace-enrichment failure; always caught at the
top-level `explain_optimiser_apply_from_config` entry point and turned into an `"error"` status
payload — it never escapes to the caller.

### Solver worker-context guard (`_optimiser_service.py`)

`_SOLVER_WORKER_ACTIVE` is a `contextvars.ContextVar[bool]` (default `False`) that marks the
current thread of execution as running inside a background solver worker. `solver_worker_context()`
is the sole way to set it (a context manager entered only by the solve background thread and the
frontier sweep background thread); `require_solver_worker_context` is a decorator that raises
`RuntimeError` immediately if the wrapped function is called while the contextvar is unset. It is
applied to the three heavy, minutes-of-sequential-CPU solver entrypoints: `_compute_frontier`,
`_solve_online`, `_solve_ratebook`. Because `contextvars.ContextVar` values propagate into threads
started via `threading.Thread` only if the thread explicitly re-enters the context (they are not
inherited automatically the way they are across `asyncio` tasks), both `_solve_background` and the
frontier sweep's background function wrap their body in `with solver_worker_context():` themselves
— the guard is a call-site check, not a mechanism that follows the thread implicitly. Pinned by
`tests/test_optimiser_routes.py::TestSolverWorkerContextGuard` (a direct call to any guarded
entrypoint outside the context raises; a call inside `solver_worker_context()` succeeds; the
contextvar resets to `False` after the context exits, including via `finally`).

## Control flow

### Solve submission and setup (`haute.routes.optimiser.solve` → `haute.routes._optimiser_service.OptimiserSolveService`)

`POST /api/optimiser/solve` flattens the graph, calls `OptimiserSolveService.start(body)`
in `src/haute/routes/_optimiser_service.py`, which:

1. Finds the `OPTIMISER` node and validates its config (`_validate_config`, static) —
   objective present, `mode` in `{online, ratebook}`, ratebook has `factor_columns`, `timeout`
   config value valid.
2. Computes the ratebook factor-level display order up front
   (`_compute_ratebook_factor_level_order`; `{}` for online) and the required-column projection
   seed (`_optimiser_solve_required_columns_by_node`).
3. Under `_start_lock`: rejects (409) if another blocking solve is already `running`
   (`_check_no_concurrent_jobs`) or if this exact graph+node already has an active setup/solve/
   auto-range job (`_active_graph_node_setup`); otherwise creates the job (`status: "running"`),
   registers a fresh `ExecutionCancellationToken` once in `_jobs` and acquires the graph/node
   single-flight coordinator.
4. Outside the lock, spawns a daemon thread (`_launch_setup_background` →
   `_run_solve_setup_and_launch`) and returns
   `OptimiserSolveResponse(status="started", job_id=...)` immediately.

The setup thread runs inside a `contextlib.ExitStack` on which `_execute_pipeline` enters the
run's [seed plan](../caching/low-level.md#seed-plans); the plan's seed leases and capture staging
are held until the grid is built and released on every exit. No checkpoint directory is written.

1. Admits an `ExecutionContext` (profile `OPTIMISER_SETUP`) — an admission failure here is caught
   by the setup worker and published as the solve job's `memory_limited` terminal status, not
   returned as a synchronous HTTP 507 from the already-completed `/solve` submission.
2. Runs the pipeline up to the optimiser node via `_execute_pipeline` — see below.
3. Resolves the configured exact `data_input` name to one incoming edge and selects that edge's
   source frame via `_resolve_data_input_frame`; node-id matching is not accepted.
4. Validates schema and value contracts and projects/casts to solver dtypes via
   `_validate_and_project`.
5. For ratebook mode only, resolves `banding_source` through the same exact edge-name contract,
   then extracts and persists that edge's factor frame to a parquet artifact (`_extract_factors`).
6. Explicitly drops the lazy-output references and runs `gc.collect()` before building the grid,
   to release memory ahead of the (often large) grid-build step.
7. Writes the scored data to a setup-owned temp parquet, or borrows an unchanged captured
   snapshot under the plan's lease (`_write_solver_input`), and builds the solver's `QuoteGrid`
   from that file via `price_contour.build_grid_from_parquet_chunked`
   (`_build_grid_from_parquet`), choosing a chunk size from either explicit config or a
   byte-budget policy against the parquet's own metadata. The temp parquet is removed on every
   exit; a borrowed snapshot never is.
8. Launches the actual solver thread (`_launch_background`), passing the built
   `QuoteGrid`, config, and (ratebook) the factors handle and factor-level order.

Steps 2–7's pipeline work is materialisation, and it runs in a hard-capped spawn worker in
production (`HAUTE_INTERACTIVE_EXECUTION_MODE=process`, the default), as training preparation
does. The setup thread creates the temp parquet path, opens the seed plan under its admitted
context (`_open_setup_seed_plan`, which prepares inputs and holds the plan's leases until setup
exits) and runs `materialise_solve_input_worker` through `_run_optimiser_worker`: the admitted
headroom (`isolated_execution_budget`) is both the child's execution budget and its native cap,
and the job's cancellation reason is the worker's stop signal, so cancellation or supersession
terminates the worker. The child adopts the plan (`SeedPlan.adopt`), runs
`_materialise_solve_input` (`_prepare_solver_frame` — steps 2–6 — then `_write_solver_input`
with borrowing off) against a private job record, and returns a `SolveInput` (the parent's
parquet path, the constraint columns and the ratebook factors handle) or the private record's
terminal failure. The child never borrows a captured snapshot: a capture it made is released
when its adopted plan closes, before the parent reads the file. Every location the child writes
is created and removed by the parent, however the worker exits: the solver-input parquet, the
marked ratebook factors directory (`_new_ratebook_factors_directory`, into which the child
persists; removed when the job never adopts its handle) and a scratch directory
(`worker_scratch_directory`) that the child routes all of its Python temporary files into (range
reducer bucket parts, staged batches, model-scoring temp files), so a stopped, timed-out or killed
worker leaves nothing behind. The parent checks the returned input is its own file and the handle
names its own factors directory. A `MemoryError` a native cap raised in the child, however
translated, leaves the child as that error, so the parent classifies it as `memory_limited`
rather than the child's generic mapping reporting a 500. A failure is classified in the
child by `_record_solve_setup_failure`, the same mapping the setup thread uses, and the parent
replays the record (terminal reason, message, `error`/`error_code`/`error_detail`/
`http_status_code`, and the child's metrics adopted as worker evidence) onto the real job.
Worker-level failures map as training preparation's do: a stopped worker is the job's stop, a
memory-shaped worker failure (`isolated_worker_failure_is_memory`) is a 507 `memory_limit` with
`isolated_worker_memory_detail`, and any other is a 500 `error`. Only then does the parent build
the grid from the file (step 7). The explicit `thread` compatibility mode runs steps 2–7 on the
setup thread against the real job (`_prepare_solver_frame`, then `_build_grid`), with the same
failure mapping.

`_execute_pipeline(body, job_id, resources, ...)` opens the run's seed plan on the caller's
`resources` stack (`open_seed_plan`, which prepares the lineage's snapshot-backed inputs, under
the job's profile — `OPTIMISER_SETUP` or `AUTO_RANGE`), or adopts the `seed_plan` handoff a
worker's supervising parent opened from the same `_setup_seed_plan_request`, and executes with
`prepare_inputs=False`
and `snapshot_plan=` that plan, passing the required-column seed to the execution facade, whose
typed strategy result is attached to the admitted context. The plan's consumed nodes are what
setup reads afterwards: an explicit target alone (the estimate's data input, the streaming
auto-range base node); otherwise the execution target — the resolved `data_input` in online
mode, or the Optimiser itself, which resolves along its selected `data_input` edge to its
producer — and every banding side input from the Optimiser's own edges that the run executes,
`apiInput` ports included. The plan applies capture eligibility itself: a node-output producer
is seeded or captured, an `apiInput` is built and never captured (its tables have their own
store), and the two-input Optimiser is a pass-through, so it is neither a join nor captured and
its unselected inputs are built only because setup consumes them. Auto-range passes, as the
plan's `capture_columns_by_node`, the column demand the solve's own setup plans at every node
(`_solve_columns_by_node`: the execution facade's `plan_projection` for the solve's target and
`_optimiser_solve_required_columns_by_node`), so whichever node auto-range captures — the data
input, or the streaming path's base below the scenario expander — carries what the solve reads
there, and the following solve (and the input estimate, which reads the data input with the
solve's columns) seeds instead of recomputing. A node the solve reads whole carries no demand
and is not widened. Auto-range uses that same `_execute_pipeline` boundary and request context;
there is no second planning policy.

Every failure mode in this thread (cancellation, `HTTPException`, memory-admission error,
bounded-streaming-unsupported error, or a bare exception) is mapped to a terminal job-store
transition rather than propagated — nothing in the setup thread's failure path is visible to a
caller except through the status-polling endpoint.

### Solver execution (`_launch_background` → `_solve_online` / `_solve_ratebook`)

The spawned solver thread updates progress to "Solving", then — inside
an execution-context stage — calls:

- **Online** (`_solve_online`): constructs `price_contour.OnlineOptimiser(objective,
  constraints, max_iter, tolerance, record_history)` and solves directly against the passed
  `QuoteGrid`.
- **Ratebook** (`_solve_ratebook`): requires a persisted ratebook factors handle
  (raises `RuntimeError` otherwise — "Ratebook mode requires a banding source"); validates
  the configured factor columns and configured quote-id column against the artifact's own
  columns; builds `factor_contexts` via
  `_build_ratebook_factor_contexts` (which calls
  `price_contour.build_ratebook_factor_contexts_from_parquet_chunked` with the solved
  `QuoteGrid`'s quote population passed in for cross-validation); constructs
  `price_contour.RatebookOptimiser(objective, constraints, factor_columns, max_iter,
  max_cd_iterations, cd_tolerance, tolerance)` and solves; after solving, computes per-level
  quote-exposure counts and originating dtype descriptors, then serialises the factor tables
  into their canonical, apply-joinable level keys
  (`_ratebook_factor_level_counts_from_artifact` /
  `_ratebook_factor_dtypes_from_artifact` → `_serialise_ratebook_factor_tables`, see Edge
  cases).

Both call the shared `_finalize_solve_result`, which builds the API-facing
`result_dict`, optionally computes an efficient frontier inline (non-fatal on failure — a
frontier failure is recorded but does not fail the solve), persists the apply-result artifact
(freeing the in-memory result dataframe as a side effect — see Artifact lifecycle below), and
atomically transitions the job to `completed`.

The solver worker classifies failures by the boundary that translated them.
`_OptimiserSolveInputError` names a user-actionable input-adaptation failure and
transitions the job to `contract_error`. `_OptimiserSolverExecutionError`
wraps exceptions from external `price-contour` optimiser construction/solve
calls and transitions to algorithm `error`, even when the library raised
`ValueError`. Any untyped orchestration or post-processing exception is an
unexpected `error`. Versioned `PUBLIC_CONTRACT_ERROR_TYPES` remain the first
and structured classification layer.

### Frontier auto-range estimation

`start_frontier_auto_range` uses `_prepare_frontier_auto_range` to validate config/mode, resolve
chunk size/partition count/timeout, compute the required-column projection, prepare the
lineage's snapshot-backed Data Inputs (`_prepare_auto_range_snapshot_inputs`: chunk planning
runs the engine schema-only, which never builds a snapshot, so preparation runs first under a
scoped `frontier_auto_range_preparation` admission that is released before the job admits, and
a missing or stale generation never costs the first run its chunk plan; a preparation
failure answers the typed contract-error status before any job exists), and attempt to
prove a `_StreamingAutoRangePlan` (`_build_streaming_auto_range_plan`), falling back to
the classic non-streaming path when the plan cannot be proven. A structural reason (ratebook
mode, no resolvable data input, no scenario expander on the chain) falls back silently because
chunking never applied. A lost chunk optimisation is reported: when a chain node's user code is
chunk-ineligible (`classify_chunk_local_polars_code`), when a model-score node keeps
post-processing code or renames, or when `chunk_plan` raises `ChunkPlanUnsupportedError`,
the preparation records a
`chunk_fallback` payload — the typed `schemas.OptimiserChunkFallback` (`code`, one of
`chunk_user_code_ineligible` / `model_score_ineligible` / `chunk_plan_unsupported`;
`node_id`, `operator`, `reason`, `line`, `column`, `message`) — on the job and the completed result's `warning` string names the node and reason.
The classic path then runs under the same admitted context. Chunk ineligibility is never an
HTTP 422; the 422 mapping remains for bounded streaming-collect failures.

- `start_frontier_auto_range` — **background**: under `_start_lock`, idempotently
  returns the existing job id if an auto-range job with the same graph fingerprint and node id is
  already running (unlike
  `start()`'s stricter conflict behaviour), otherwise creates a cancellable job, registers it,
  and spawns a worker thread.
- `_run_frontier_auto_range_job` is the one auto-range job. It owns admission, cancellation,
  completion (the result's `warning` and `chunk_fallback` come from the recorded fallback) and a
  single failure classification, and takes its range batches from one of two sources:
  `_chunked_frontier_ranges` (execute to the streaming plan's base node, then
  `iter_chunked_frames` expands and scores one base chunk at a time, each chunk validated,
  projected and collected before the next; only reached when a streaming plan was proven) or
  `_full_frame_frontier_ranges` (execute pipeline → resolve source → validate/project →
  `_estimate_scenario_frontier_ranges`' bounded batches). Both feed
  `_reduce_frontier_range_batches`, which reduces every batch into
  `_ScenarioFrontierRangeAccumulator` and calls `finish()`. In process mode the job computes its
  totals in a hard-capped worker instead (`_frontier_ranges_in_worker`): it opens the seed plan
  that source would execute under (at the streaming plan's base for a chunked job, at the data
  input otherwise), runs `frontier_auto_range_worker` through `_run_optimiser_worker` with the
  job's remaining timeout as the worker's timeout (its expiry publishes the job's `timed_out`),
  and completes with the returned totals and the worker's metrics adopted as evidence. The child
  re-plans (`_prepare_frontier_auto_range(prepare_snapshot_inputs=False)`; a chunk plan is not
  picklable), fails if its chunk decision differs from the parent's, and runs this same job with
  `isolate=False` against a private job record and the adopted plan, with its temporary files
  (the reducer's bucket parts) in a parent-owned scratch directory removed after the worker
  exits; its terminal failure record is replayed through the job's failure mapping
  (`OptimiserWorkerFailureError`), and a `MemoryError` behind a failure is a 507 as for setup. Progress
  messages inside the worker are not relayed; the job reports "Estimating frontier range" until
  its terminal state.
- `frontier_auto_range_status` enforces the job's timeout lazily, on poll — solve, frontier-sweep,
  and auto-range timeouts are all enforced lazily on their respective status polls (`solve_status`,
  `frontier_status`, `frontier_auto_range_status`), with auto-range additionally checking elapsed
  time within its worker loop. There is no dedicated timeout-watcher thread.
- `cancel_frontier_auto_range` delegates to `_stop_frontier_auto_range_job`.

### Frontier computation and point selection (`src/haute/routes/optimiser.py`)

`POST /frontier` (`haute.routes.optimiser.run_frontier`) is split into a synchronous validation
phase and a background sweep phase, mirroring the solve submission pattern:

1. **Synchronous** (still on the request thread, so contract errors surface as 4xx on this
   request): resolves the job's solver/quote-grid
   runtime state (touching heavy objects back into presence if evicted), enforces the compute
   budget (`haute.routes._optimiser_limits.enforce_frontier_compute_budget` — rejects with 422 before
   the solver is invoked if `n_points_per_dim ** n_constraints` would exceed
   `FRONTIER_COMPUTE_LIMIT`). Under `_frontier_state_lock`, it then checks
   `_has_running_frontier_job(body.job_id)`, creates the job, and registers its cancellation
   token as one atomic admission step; a sweep already in flight for this solve job is rejected
   with 409.
2. Creates a `frontier_recompute`-typed job (`status: "running"`, `parent_job_id` set to the solve
   job id, `timeout` derived from the solve job's own config via `_solve_timeout_from_config`) and
   spawns a daemon thread running `_run_frontier_sweep` inside `solver_worker_context()`.
   The route returns immediately with `OptimiserFrontierResponse(status="started",
   job_id=<frontier_job_id>)` — a *different* job id from `body.job_id`, since the frontier sweep
   is tracked as its own job. If `thread.start()` itself raises, the route transitions that
   frontier job to terminal `error`, releases its cancellation-registry entry, and returns a
   sanitised 500 response without starting sweep work.
3. **Background** (`_run_frontier_sweep`): calls
   `haute.routes._optimiser_service._compute_frontier`, a thin dispatcher: ratebook passes
   `ratebook_factors`/`factor_columns` kwargs to `solver.frontier(...)`, while online omits them.
   A cancellation checkpoint runs before and after the external frontier call. The response is
   capped via `haute.routes._optimiser_limits.limited_frontier_payload` (caps to
   `FRONTIER_POINT_LIMIT` while always reporting the true total and truncation flag, and attaches
   `point_summaries`, one `frontier_point_summary` per returned point in point order; a point that
   cannot be summarised fails the frontier) and the
   result stored as both the size-limited `result["frontier"]` and the raw `frontier_data` field
   on the *parent solve job* (via `_store.atomic_update(parent_job_id, ..., expected_status=
   "completed")` — 409-shaped as a `contract_error` on the frontier job if the solve job's state
   changed concurrently, since the atomic update itself cannot raise past the background thread).
   The same atomic update increments the parent's integer `frontier_generation`; point
   materialisation uses that value as its recompute fence, so correctness does not depend on a
   job store preserving nested Python object identity.
   Any previously materialised frontier-point apply artifacts are invalidated (their handles
   removed from `artifact_handles` and their files cleaned up) since a recomputed frontier makes
   old point indices meaningless. The stop check, parent update, and frontier-job completion
   transition share `_frontier_state_lock`; therefore cancel/timeout cannot become terminal
   between the check and parent mutation. On success the frontier job itself transitions to `completed`
   with the frontier payload as its own `result` field (a full `OptimiserFrontierResponse`, so a
   status poll and the (historical) inline response shape carry the same fields). Every failure
   path — an `HTTPException` raised inside the sweep (classified `contract_error` for 400/409/422,
   `error` otherwise) or a bare `Exception` — transitions the frontier job to a terminal status via
   `_frontier_lifecycle` (a dedicated `JobLifecycle(_store)` instance) rather
   than propagating; nothing about a post-validation frontier failure is visible to the caller
   except through polling.

`GET /frontier/status/{job_id}` (`haute.routes.optimiser.frontier_status`) is a synchronous
FastAPI handler so acquisition of the thread-backed frontier lock runs in the server threadpool
rather than blocking the event loop. It 404s if the job type isn't `frontier_recompute`, lazily
enforces the job's timeout on poll (the same "no dedicated timeout-watcher thread" pattern as
frontier auto-range — see below), requests cooperative cancellation before the `timed_out`
transition, and
returns `OptimiserFrontierStatusResponse` (`status`, `progress`, `message`, `elapsed_seconds`,
`result: OptimiserFrontierResponse | None`, `terminal_reason`, `error_code`, `http_status_code`,
`error_detail`, `execution_metrics`) — the same status-envelope shape used elsewhere in the
component (solve status, frontier auto-range status).

`POST /frontier/cancel/{job_id}` requests cooperative cancellation and atomically transitions a
running frontier job to `cancelled`. A terminal job is returned unchanged. The worker treats
`BackgroundJobStoppedError` as an expected exit and never publishes a late result to the parent.

`POST /frontier/select` (`haute.routes.optimiser.select_frontier_point`) resolves one frontier
point's totals/constraints/lambdas into a full result summary
(`_frontier_point_result_dict`, which applies `frontier_point_summary` to the base result, so it
equals the point's entry in `point_summaries`) without re-solving; for a ratebook job with
`include_ratebook_tables` requested, it additionally *materialises* that point by re-running the
already-built solver against the point's lambdas
(`haute.routes.optimiser._materialise_ratebook_frontier_point`) —
cached if the job's current result already matches that point's lambdas exactly, otherwise
re-solved and the job updated atomically (409 if the job's state changed concurrently).

### Frontier point summaries

A frontier point's summary holds every result field that differs from the solve it was swept
from: total objective, constraint totals (from `total_<name>`, else a nested constraints map,
else the bare name), lambdas (from the `lambda_<name>` columns), converged, iterations, CD
iterations, clamp rate, history, scenario-value stats (from the `sv_*` columns), scenario-value
histogram, factor tables, the non-converged warning and the frontier error. Every field is
always present and `null` where the point has none; applying the summary to a base result
removes a `null` field. A point with no lambdas or no `converged` is a 400 when selected, and a
missing or non-finite number or conflicting lambdas a 500; while the frontier is being built
either fails the frontier.

### Apply preview (`POST /apply`, `haute.routes.optimiser.apply_lambdas`)

Rejects ratebook jobs outright (`_reject_ratebook_apply_detail` — checked before any
heavy-state lookup, since a ratebook `RatebookResult` has no per-quote dataframe). For online
jobs, resolves either a specific frontier point (materialising its apply dataframe via
`_materialise_frontier_point_apply` — persisted once per point index and reused via
handle lookup thereafter) or the base solve result — from the still-live in-memory
`solve_result.dataframe` if present, or from the persisted apply-result artifact otherwise. The
response is capped via `haute.routes._optimiser_limits.limited_apply_preview_payload` (first
`APPLY_PREVIEW_ROW_LIMIT` rows plus explicit `row_count`/`preview_truncated` metadata).
Handle insertion re-reads and merges the latest mapping while holding `_frontier_state_lock`.
Materialisation captures `frontier_generation` before external solver/artifact work and compares
the integer again before publishing, returning 409 only when a recompute actually advanced it;
copying or serialising the unchanged `frontier_data` payload does not invalidate the request.
At most eight `frontier_apply_result:*` handles are retained; the oldest excess handle is
removed from the job before its owned parquet is deleted.

### Save and MLflow log (`src/haute/routes/optimiser.py`)

Both `save_result` and `mlflow_log` resolve the applicable `SolveResultLike`
(from a selected frontier point via `_solve_result_for_selected_frontier_point`, or directly from
the job's retained `solve_result`), build a shared JSON payload via `_build_artifact_payload`
(lambdas, objective/constraint totals, baseline totals, convergence/iteration counts,
column-name config, frontier-selection provenance, and for ratebook the factor tables plus ordered
dtype descriptors), validate it with `_validate_artifact_payload` (rejects a missing lambda
mapping, a missing total objective, missing or malformed ratebook factor-table/dtype metadata, or
*any* non-finite float anywhere in the payload, naming up to 5 offending JSON paths), then either
atomically write it to disk
(`atomic_write_text`, with `allow_nan=False` as a defence-in-depth backstop behind the explicit
validation) or attach it as an MLflow run artifact alongside metrics/params and (if present) a
frontier-points CSV. Tracking-URI/registry setup and experiment-name resolution for `mlflow_log`
go through the same shared `configure_mlflow_tracking(destination)` / `resolve_experiment_name()` /
`build_run_url()` helpers in `haute.modelling._mlflow_log` that `routes/modelling.py` uses
(see [modelling low-level](../modelling/low-level.md#shared-mlflow-trackingexperiment-name-resolution))
without calling `log_experiment()` itself, since the optimiser's artifact shape (solver params, frontier CSV,
`optimiser_result.json`) doesn't fit `log_experiment()`'s model-diagnostics-shaped signature.
`OptimiserMlflowLogRequest.destination` (`""|"databricks"|"server"|"local"`, default `""` =
the local folder) is authoritative for where the run goes: the job's training-time `mlflow_destination`
snapshot is never consulted, an unknown value is a `422` before any work, and an unconfigured
destination fails with `400` carrying the prerequisite-naming `MlflowConfigError` detail and
writes nothing; the experiment-name default follows the resolved backend. `experiment_name` is
equally authoritative — the frontend sends the node's current `mlflow_experiment`, and a blank
value uses the backend default rather than the job's solve-time snapshot. Failures share the
modelling log route's outcomes (`routes/_mlflow_log_errors.py`): a missing MLflow package is the
shared `503` checked before any work, a classified remote failure is a `502` with an
`mlflow_<category>` code and write-specific message, anything unclassified is the generic `500`,
and only the category and error type are logged. The OPTIMISER node
config gains the optional `mlflow_destination` field (absent = the local folder; `databricks`
or `server` when chosen), declared on the
`OptimiserConfig` TypedDict and `OPTIMISER_CONFIG_KEYS`, classified as node config for the
execution cache, and rejected by `validate_node_config` for unknown values; solving never logs
automatically.

### Artifact lifecycle (persist / validate / load / cleanup) (`src/haute/routes/_optimiser_artifacts.py`)

Two artifact families, both rooted under the versioned marker-aware OS-temp hierarchy
(`<tempdir>/haute/artifacts/v1/optimiser_apply`,
`<tempdir>/haute/artifacts/v1/optimiser_ratebook_factors`):

- **Persist** (`_persist_apply_result_artifact`, `_persist_ratebook_factors_artifact`,
  `_persist_ratebook_factors_lazy_artifact` — the lazy variant sinks a
  `LazyFrame` via `bounded_sink` without ever collecting it into memory): each writes into a
  freshly created direct child under the artifact root and writes a versioned family-specific
  ownership marker before writing payload data. For the apply-result case it explicitly nulls the
  source object's `.dataframe` attribute afterward (logging at debug level if the attribute
  cannot be cleared) so the heavy dataframe is not held twice, once on disk and once in the job
  store's retained `solve_result`.
- **Validate** (`_validate_server_owned_parquet_handle`): re-derives the expected
  directory/path from the handle's own fields and checks they resolve (with a TOCTOU-aware
  strict-resolution-only-if-exists rule, so validating a handle whose artifact was already
  deleted does not itself crash) to a direct child of the artifact root with the expected name
  prefix and filename — every load and cleanup call goes through this check first.
- **Load** (`_load_apply_result_artifact`, `_load_ratebook_factors_artifact`,
  `_scan_ratebook_factors_artifact`): eager or lazy re-reads of the persisted
  parquet. A valid handle whose file is absent is a 410 lifecycle outcome with
  a stable "no longer available; re-run the solve" detail. Invalid
  server-owned handles and present-but-unreadable parquet remain sanitized 500
  outcomes with distinct stable invalid/corrupt details. Validation and
  parquet-library failures are logged server-side with `exc_info=True`; missing
  paths are logged without exposing the path to the caller. The complete
  rationale and rejected alternatives are in
  [the OPT-D01 decision record](error-detail-policy.md).
- **Cleanup**: `_cleanup_apply_result_artifact`/`_cleanup_ratebook_factors_artifact` are
  registered with the job store's own artifact-cleaner registry
  (`register_artifact_cleaner` in `src/haute/routes/_optimiser_artifacts.py`) so a job's TTL/eviction sweep
  in [background-jobs](../background-jobs/high-level.md) can garbage-collect artifacts still
  attached to a job. This component separately handles the **orphan** case — an artifact created
  during a request but never successfully attached to a job, e.g. because a concurrent
  cancellation raced the atomic update that would have recorded its handle — via
  `_cleanup_orphan_apply_result_artifact`, called from many `finally`/failure branches.
  The attachment check itself is an *identity* comparison
  (`updated_job.get("artifact_handles") is not artifact_handles`) rather than an equality check,
  used to detect when the atomic job-store update silently no-opped because the job's expected
  status no longer held.
- **Restart cleanup**: the server lifespan reads the strict non-negative
  `HAUTE_ARTIFACT_STALE_SECONDS` interval (default 86,400 seconds) after loading the project
  environment, then passes it to `reap_stale_optimiser_artifacts`. The reaper checks only valid
  stale markers from the two dedicated roots and logs bounded
  inspected/removed/failed/reclaimed-byte counts. One tracked worker-thread reap is scheduled
  without delaying readiness; shutdown observes the task.

### Ratebook factor-table canonicalisation (`src/haute/routes/_optimiser_service.py`)

`price-contour` emits factor-table level labels from the verbatim string form of the source
value (e.g. a Float64 `25.0` becomes the label `"25.0"`), while the runtime rating join this
component's own apply path canonicalises keys via
`normalise_rating_key(value, originating_dtype)` ([rating](../rating/high-level.md)). To make a
saved ratebook artifact's factor tables actually joinable at apply time,
`_canonical_ratebook_table_level` receives the exact ordered factor dtypes read from the same
persisted solved-factor schema as the counted levels. It splits a composite solver label into
components, coerces each component directly through its corresponding originating dtype, joins
the canonical components, and requires that exact key to exist in the counted keyset. In
particular, a widened Python label such as a Float32 source value emitted as
`"0.10000000149011612"` is reconstructed through Float32 and saves as `"0.1"`. The serializer
does not infer Float64 from the Python value and does not search verbatim/collapsed candidates
or apply a minimum-collapse tie-break. A level with the wrong component count or no exact
counted key raises `ValueError` rather than guessing.

`_ratebook_factor_level_counts` computes the per-level quote-exposure counts this
canonicalisation is checked against, and deliberately raises rather than merging if two distinct
raw level tuples canonicalise to the same key — a last-writer-wins merge here would silently
drop a solved rate.

`_ratebook_factor_dtypes_from_artifact` reads the solved factor parquet schema and stores
`factor_dtypes[table]` as an ordered list of
`{"column": <name>, "dtype": <rating dtype descriptor>}` records in the job result and every
saved/logged artifact. The same descriptor records are required by factor-table serialization
and converted back to exact Polars dtypes before canonicalising solver labels.
`_apply_ratebook` requires one descriptor record per join factor, validates the order/name and
exact descriptor against its once-resolved apply schema, and raises
`RatingFactorDtypeContractError` before lookup construction when metadata is absent or differs.
It does not coerce an apply column to the saved dtype and does not run legacy artifacts through
the neutral-miss path.

### Runtime online apply (`_apply_online` in `src/haute/_builders.py`)

`_apply_online` applies an online artifact through `price_contour.ApplyOptimiser` on the frame
`_prepare_online_apply_frame` materialises (null quote ids dropped, apply dtypes cast). The
apply emits one row per quote in ascending order of the `Utf8` quote id, with the literal id
column `quote_id` whatever the artifact names its input quote-id column. How the result is
exposed depends on the artifact's constraints (`has_ratio_constraint`):

- A `pl.DataFrame` input is applied eagerly.
- A ratio constraint linearises against the whole apply-time frame, so the apply always reads
  every input row and returns its eager result.
- Otherwise each quote is chosen independently, so the result is a
  `key_prefix_python_scan` (execution engine) keyed by the quote-id column with the schema
  `online_apply_output_schema` derives from the artifact: `quote_id` (`Utf8`), `optimal_step`
  (`Int32`), the optimised value column or its configured rename (`Float32`),
  `optimal_objective` (`Float32`), `optimal_<constraint>` (`Float32`) per sorted constraint,
  and the version column when configured. A limit Polars pushes to the scan reads only the
  quote-id column to choose the first quotes in that order — which elides upstream row-local
  scoring for that read — and applies to the input filtered to those quotes, so upstream
  scoring runs only for their scenario rows. Without a limit the apply reads every row.

### Trace explainability (`src/haute/_optimiser_apply_explainability.py`)

`explain_optimiser_apply_from_config(config, input_row, output_row, *, input_frames,
source_names)` is the sole public entry point:

1. Loads the artifact the `OPTIMISER_APPLY` node was configured with (`_load_artifact_from_config`
   — file or MLflow, delegating to `_optimiser_io.py` with the node's `mlflow_destination`, absent
   = the local folder, exactly as the runtime apply in `_node_apply.py` and the deploy scorer's request-time
   loader do), reading `mode` from it (defaulting to
   `"online"` if absent, but rejecting an explicitly present-but-blank `mode` as a
   misconfiguration). A registered source may name an `alias` instead of a `version`
   (see [mlflow-model-registry](../mlflow-model-registry/low-level.md#registered-model-aliases)).
   The OPTIMISER_APPLY node config's optional `mlflow_destination`
   (MLflow source types only) is declared on the `OptimiserApplyConfig` TypedDict and
   `OPTIMISER_APPLY_CONFIG_KEYS` (round-tripping through save, parse, and the codegen sidecar),
   classified as an artifact input for the execution cache, and rejected by `validate_node_config`
   for unknown values. An explicit destination must be configured in the environment that loads
   the artifact, including deployed scoring and explanations; it never falls back to another
   backend.
2. Selects the correct parent lazy frame from `input_frames` (`_select_optimiser_apply_input`,
   shared with the runtime executor in `haute._builders`, so the trace path resolves the same
   input the real apply ran against). A ratebook artifact requires a non-empty
   `ratebook_input` matching exactly one executable `source_names` entry; missing, stale, and
   ambiguous values fail. There is no first-connected-input default. Online artifacts use their
   primary frame and ignore `ratebook_input`.
3. Dispatches to `_explain_online` or `_explain_ratebook`.

RAM cardinality estimation follows the same selector identity when `optimiser_mode` is known to
be `ratebook`: `ratebook_input` must be present and match one exact executable incoming-edge name.
It never estimates a missing or stale selector by choosing the first connected frame.

Trace enrichment passes an online apply's inputs from the trace's uncapped lineage plans and a
ratebook-mode apply's inputs from the frames that produced the clicked row (see
[tracing](../tracing/low-level.md)). `_explain_online` resolves the clicked quote id from the
output row's artifact quote-id column when present, else from the literal `quote_id` the apply
emits; filters the parent frame to that quote unless the artifact has a ratio constraint (whose
linearisation baseline is the whole input); raises `OptimiserApplyTraceError` when no input row
remains; builds the online apply input frame
(`_prepare_online_apply_frame`), constructs a `price_contour.ApplyOptimiser` with the artifact's
lambdas/constraints/column names, and calls `applier.with_explainer_columns(df)` — the
`price-contour` API documented in full in
[`with_explainer_columns` contract](#withexplainercolumns-contract) below. It filters to the
clicked quote's rows, asserts exactly one `selected` and exactly one `is_baseline` candidate, and
checks the selected candidate's `scenario_value` against the actual output column (tolerant
numeric match, see `_values_match`) before returning the full candidate ladder plus the
selected/baseline rows.

#### with_explainer_columns contract

`price_contour.ApplyOptimiser.with_explainer_columns(df)` is the one piece of online-apply
explainability this component deliberately does not reimplement — ratio-constraint linearisation
and exact fixed-lambda score semantics stay owned by `price-contour` so there is exactly one
implementation of "how a scenario is scored." It takes the same candidate frame
`ApplyOptimiser.apply(df)` would score and returns it unchanged plus these appended columns:

- `decision_score` (float) — the exact fixed-lambda score used to choose the winning candidate.
- `selected` (bool) — true for the one candidate `apply(df)` selects for that quote.
- `is_baseline` (bool) — true for the one baseline scenario for that quote.
- Per constraint `name`: `linearised_<name>` (the value used in the fixed-lambda score — the
  original constraint column for a sum constraint, or the internal ratio-linearisation value for
  a ratio constraint) and `lambda_term_<name>` (that value's signed contribution to
  `decision_score`).

Score reconstruction holds for every candidate row:

```text
decision_score == objective + sum(lambda_term_<constraint> for every constraint)
lambda_term_<name> == signed_lambda_<name> * linearised_<name>
```

where `signed_lambda_<name>` is `+lambda` for a minimum constraint and `-lambda` for a maximum
constraint. Ratio-constraint linearisation (the sum-shaped internal value substituted for the raw
numerator/denominator columns) is entirely library-owned; this component never re-derives it, only
reads `linearised_<name>`/`lambda_term_<name>` off the returned frame.

Baseline selection (`is_baseline`) follows deterministic rules, applied per quote: prefer an exact
`scenario_value == 1.0`; if none exists, take the scenario with `scenario_value` nearest to `1.0`;
if still tied, fall back to stable scenario ordering. Exactly one candidate per quote with at least
one row gets `is_baseline == True`.

`selected` is required to match `ApplyOptimiser.apply(df)` exactly, including tie-breaking — this
is the guarantee `_explain_online` leans on when it asserts exactly one `selected` row per quote
and reconciles it against the real output value; a mismatch here would mean the trace is
explaining a different decision than the one that actually priced the row.

Validation is fail-loud with the same rules `apply(df)` itself applies: a missing quote id,
scenario index, scenario value, objective, or constraint column; invalid/null data `apply(df)`
would reject; an unknown lambda key; or an invalid ratio-constraint numerator/denominator column
all raise rather than silently omitting explainer columns or falling back to an approximate score.

`_explain_ratebook` locates the matching input row for the clicked output row
(`_match_ratebook_input_row` — Polars-pushed-down equality filter first, falling back to a
bounded Python batch scan for cross-dtype/NaN-tolerant matching if the fast predicate errors or
finds nothing), then walks the artifact's `factor_tables` in order, for each factor: resolves
whether the table is a composite (joins on multiple columns, split via
`_split_ratebook_level`) or single-column table, looks up the matching entry via
`_match_ratebook_entry` (keys normalised through the same dtype descriptor and
`normalise_rating_key` used at runtime; ties resolved by walking entries in *reverse* to mirror the engine's
`unique(keep="last")` deduplication), applies the multiplicative neutral element `1.0` and marks
the factor `unseen` if no entry matches (the engine's own loud-neutral miss-path behaviour, not
an error), and accumulates a running product. The ladder is reconciled against the actual output
column at the end; a mismatch raises `OptimiserApplyTraceError`. An artifact with no usable
factor-table entries also raises because an empty ladder has no value that can be reconciled.

Every raised exception anywhere in this call graph is caught by
`explain_optimiser_apply_from_config`'s outer `try`/`except` and converted to `_error_detail(...)`
— an `ImportError` is specifically rewritten into an actionable
"install the missing library" message (falling back to a generic phrasing if the import
machinery didn't populate `exc.name`), everything else is logged with `exc_info=True` and
returned as a generic `status: "error"` payload.

## Edge cases and invariants

- **Preamble dependencies are pinned per operation.** Estimate, solve and streaming
  auto-range resolve `preamble_execution_fingerprint` once when the job starts executing
  and pass it to every `_compile_preamble` call of that job, so chunks never mix helper
  versions; a later job resolves a fresh fingerprint and computes with edited helpers. See
  the execution-engine `_compile_preamble` contract.
- **One blocking solve process-wide, plus graph/node setup single-flight.**
  `_check_no_concurrent_jobs` scans the shared optimiser store and blocks a second solve for any
  graph/node. `estimate`, `frontier_auto_range`, and `frontier_recompute` are explicitly excluded
  (`_NON_BLOCKING_RUNNING_JOB_TYPES`), so they do not reserve that global solve slot, and none of them
  waits on a running solve: no optimiser path holds a lock across its work to apply the streaming
  chunk size. Independently,
  the graph/node coordinator prevents a solve setup and a background auto-range setup from
  overlapping on the same graph/node; synchronous estimate does not use that coordinator.
- **`_ESTIMATE_JOB_TYPE` is assigned by `/estimate`, not by frontier auto-range.**
  `haute.routes.optimiser._optimiser_input_metrics` (backing `POST /api/optimiser/estimate`)
  creates a short-lived job tagged `job_type = _ESTIMATE_JOB_TYPE`, owns an admitted
  `OPTIMISER_SETUP` context for the complete pipeline-and-aggregation scan, releases that
  admission, and unconditionally removes the job in a
  `finally: _remove_estimate_job(job_id)` block. An admission or sampled-memory failure is a
  structured HTTP 507 response with optimiser-estimate-specific user wording, never a generic
  HTTP 500. The job tag is what lets `_NON_BLOCKING_RUNNING_JOB_TYPES` exempt an in-flight
  `/estimate` call from `_check_no_concurrent_jobs`'s store-wide scan.
- **`/estimate`'s `total_rows` is null only when the source size is unknown.**
  `_detailed_ancestor_source_metadata` answers an unknown size itself, with no row count:
  live data without Parquet backing, or a source whose metadata read raises `OSError`,
  `TypeError` or `ValueError`. The route does not catch anything else it raises. An
  unexpected failure is an error response, never an estimate with the total missing.
- **std of a single-quote scenario-value distribution is hardcoded to `0.0`.**
  `_compute_scenario_value_stats` special-cases `n == 1` rather than calling Polars' sample
  standard deviation (`ddof=1`), which is undefined (`null`) for a single observation and would
  otherwise crash the subsequent numeric cast; `0.0` is treated as the true population value for
  a singleton, not a fabricated fallback.
- **Non-finite value validation happens post-cast, at Float32 precision.** The solver consumes
  Float32; `_validate_input_value_contracts` checks for NaN/Inf *after* the Float32 cast
  specifically so a Float64 value that only overflows to ±Infinity once down-cast is still caught
  as a contract violation, not silently passed through.
- **Null-value validation spans every dtype; non-finite validation is float-only.**
  `_null_check_columns` checks all of `finite_columns` (objective, constraint, and
  scenario-value columns) regardless of dtype via `pl.col(cname).null_count()`, while
  `_non_finite_check_columns` filters that same column set down to `schema[cname].is_float()`
  before checking `is_nan()`/`is_infinite()` — a non-float column (e.g. an integer objective) can
  never hold NaN/Inf, but it can hold null, so both checks run over the same source columns with
  different dtype gates and are reported as two independently-named detail messages
  (`_NON_FINITE_DETAIL_PREFIX` vs. `_NULL_VALUE_DETAIL_PREFIX`) if both fire.
- **Frontier sweep concurrency is scoped to the parent solve job, not the graph/node coordination
  key.** `_has_running_frontier_job` scans for a running `frontier_recompute` job whose
  `parent_job_id` matches while `_frontier_state_lock` makes the scan and job creation atomic.
  This mechanism is independent of `_graph_node_setup_singleflight` (which keys by graph+node and gates solve/background-auto-range
  submission). `_FRONTIER_RECOMPUTE_JOB_TYPE` is also listed in `_NON_BLOCKING_RUNNING_JOB_TYPES`
  precisely because it never reserved a solve slot when frontier computation ran inline, and the
  background offload preserves that semantics — an in-flight frontier sweep never blocks a new
  solve/estimate/auto-range submission for the same graph/node.
- **Streaming auto-range only engages for provably row-local pipeline chains.**
  `classify_chunk_local_polars_code` (the shared receiver-aware AST classifier) decides whether
  user code between the data-input node and the scenario expander is safe to run per-chunk;
  anything not provably row-local (global state, ordering-sensitive logic, arbitrary custom
  code) falls back to the full non-streaming estimate path rather than raising, and the lost
  optimisation is recorded rather than hidden: `_streaming_auto_range_node_is_eligible` returns
  the classifier decision as a `chunk_user_code_ineligible` fallback, a model-score node reports
  `model_score_ineligible` with the reason `model_reuse_lifetime`, `post_processing_code`, or
  `column_renames`, and a `chunk_plan` rejection reports `chunk_plan_unsupported` with the node
  the planner rejected (from the error's public payload or its `node_id`/`target_node_id`
  context), never the optimiser node. This is a memory/latency trade-off, not a correctness
  gate.
- **Cancellation and graph/node exclusion have separate owners.** `_jobs` owns one cancellation
  token per solve/auto-range job; `_graph_node_setup_singleflight` owns only graph/node exclusion.
  Worker scopes release both once, after the actual worker has stopped, so a cancelled job keeps
  the exclusion lease until it can no longer mutate state.
- **Generic setup failure detail is boundary-safe.** Explicit grid chunk-size
  validation remains an actionable 400. Unknown `_execute_pipeline` and
  `_build_grid` failures are fixed-detail 500s and preserve their raw
  exception only in server logs, as defined by
  [OPT-D01](error-detail-policy.md).
- **`decision_score`/`is_baseline` tie-breaking is fully owned by `price-contour`.** The
  [`with_explainer_columns` contract](#withexplainercolumns-contract) requires it to match
  `apply(df)`'s tie-breaking exactly, including which `scenario_value` is treated as baseline
  (`== 1.0` exactly, else nearest to `1.0`, else stable ordering) — this component never
  re-derives that rule, only consumes and reconciles the result.
- **Ratebook "unseen" factor levels are not errors.** A ratebook factor lookup that finds no
  matching entry for an input value is the engine's documented loud-neutral behaviour (counted
  and logged upstream, multiplicative identity `1.0` applied) — the trace payload marks it
  `unseen: true` / `status: "default"` rather than failing the trace.

## Error handling

- **Synchronous request-thread paths** (`optimiser.py` route handlers) raise
  `fastapi.HTTPException` directly for validation and
  contract failures: 400 (bad config, missing/wrong-dtype columns, null quote ids, non-finite
  values, a null value in an objective/constraint/scenario column, disconnected `data_input`,
  missing ratebook banding source, malformed frontier-point data, incomplete job summaries), 404
  (job not found or wrong job type), 409 (concurrent job/graph-node conflict, a frontier sweep
  already running for the target solve job, or an atomic job-store update losing a race against a
  concurrent state change), 422 (`BoundedMemoryUnsupportedError` from solve setup or a bounded
  streaming collect, and the frontier compute-budget rejection; a projection gap keeps a
  full-width boundary and an auto-range chunk-plan gap is a recorded fallback, never 422), 410 (a valid
  server-owned handle whose artifact is no longer present), 500 (a background
  worker thread failing to even start; a generic/unclassified pipeline or grid failure; an
  invalid server-owned artifact handle; a corrupt persisted artifact), 507
  (`ExecutionAdmissionError`/`ExecutionMemoryLimitExceededError`
  wrapped via `_memory_limit_http_exception`, whose payload `message` is the
  shared curated wording from `routes/_memory_messages.memory_limit_user_message`
  — the same shape training and the input-snapshot build use — and the
  memory-limited job's terminal message reuses it rather than the generic
  exceeded-its-memory-budget fallback). This applies to `POST /frontier` only up through its
  synchronous validation phase (runtime resolution, compute budget, already-running-sweep check);
  once validation and worker launch succeed, the request returns 200 with a `status: "started"`
  body.
- **Background-thread paths** (the setup thread, the solver thread, the streaming/non-streaming
  auto-range worker, and — since the frontier sweep offload — the frontier sweep worker) never let
  an exception propagate out of the thread; every failure branch is caught and converted into a
  `JobLifecycle.transition(...)` call recording a terminal status, a human message, and (for
  `HTTPException`s specifically) the original status code and detail string in the job for later
  inspection. `_run_frontier_sweep` follows the same pattern via its own `_frontier_lifecycle`
  instance: an `HTTPException` with status 400/409/422 transitions the frontier job to
  `contract_error` (preserving `http_status_code`/`error_detail`), any other `HTTPException`
  transitions it to `error`, and a bare `Exception` is logged (`frontier_failed`, `exc_info=True`)
  and also transitions it to `error` with the generic `_INTERNAL_ERROR_DETAIL` message.
  `BackgroundJobStoppedError` is the unified signal for both job-store-driven cancellation and
  execution-context-driven cancellation (`_coerce_stopped_terminal_reason` maps it to
  `cancelled`/`superseded`/`timed_out` as appropriate).
- **Domain exception types specifically handled**: `BoundedMemoryUnsupportedError`,
  `ChunkPlanUnsupportedError`, `ContractMismatchError`,
  `SchemaMismatchError` (`haute.errors`); `ExecutionAdmissionError`
  (`haute._execution_admission`); `ExecutionCancelledError`,
  `ExecutionMemoryLimitExceededError` (`haute._execution_context`); `BackgroundJobStoppedError`
  (`haute.routes._background_jobs`).
- **Trace explainability** raises exactly one exception type internally,
  `OptimiserApplyTraceError` (a `RuntimeError`), for every domain failure (missing artifact
  source, missing/blank artifact or config column, unmatched input row, empty ratebook input
  frame, non-numeric/non-finite factor value, output-column reconciliation mismatch). The public
  entry point catches `ImportError` separately (to give an actionable "install the missing
  library" message) and `Exception` generally, converting both into the `status: "error"`
  payload described above — no exception from this module is ever allowed to reach the tracing
  subsystem's caller.
- **Artifact-load failures never leak library internals.** Every wrapped artifact-load
  `HTTPException` in `_optimiser_service.py` uses a fixed, generic message
  ("... is missing or corrupted. Re-run the solve to regenerate it.") regardless of the specific
  underlying `OSError`/parquet exception, which is logged server-side with `exc_info=True` but
  never included in the client-facing detail string.

## Testing

- `tests/test_optimiser_contracts.py` verifies optimiser job-store isolation, streaming quote-contiguous projections, factor extraction, low-memory sink behavior, null/interleaved/range validation, and solve/apply totals.

Tests live under `tests/` (unit/integration, `tests/performance/` for size/perf assertions), and
share fixtures from `tests/optimiser_fixtures.py`. No dedicated property-based tests were found
for this component; coverage is unit + integration + golden-fixture + real-library contract
tests.

Since the frontier sweep became a background job, every test file that calls `POST /frontier` and
expects a completed result uses the shared helpers in `tests/optimiser_fixtures.py`:
`poll_frontier_until_done(client, job_id, timeout=30.0)` polls to any terminal status;
`run_frontier_and_wait(client, payload, timeout=30.0)` posts, checks the immediate job handle, and
polls; `frontier_result(client, payload, timeout=30.0)` additionally requires completion and
returns the nested result. The helpers are used across `test_optimiser_routes.py`,
`test_optimiser_routes_critical_edges.py`, `test_optimiser_routes_real_library.py`, and
`tests/performance/test_optimiser_memory_response_perf.py`.

- **`tests/test_optimiser_routes.py`** — by far the largest file (dozens of test
  classes) covering the full route surface end-to-end against the FastAPI test client: node
  registration/codegen/executor passthrough, solve/status/estimate/apply/save/frontier/
  frontier-select/mlflow-log routes, ratebook solve, solve-with-history, scenario-value stats,
  column validation, non-convergence warnings, background-thread error classification, job-state
  guards (cancel/timeout/supersede races), pipeline-execution argument wiring, bounded-sink grid
  building, execute-pipeline cleanup, artifact-payload building (including extended/edge-case
  variants), save-artifact required-section validation
  (`test_artifact_gate_rejects_invalid_required_sections`), mlflow-log extended paths, and many
  CAS/atomic-update race scenarios (`atomic_update`
  returning `None`, artifact orphaning on a lost race, etc.). Also covers: null-input rejection
  (`test_solve_rejects_null_input_values`,
  `test_frontier_auto_range_rejects_null_values_before_deriving_ranges`); the frontier
  background-job handshake (`test_frontier_returns_job_handle_promptly` — asserts the initial
  response returns before the sweep finishes, `test_frontier_after_solve`,
  `test_frontier_worker_start_failure_marks_job_error_and_releases_registry` — a synchronous
  worker-launch failure leaves a terminal frontier job and no live cancellation registration,
  `test_frontier_solver_exception_surfaces_as_job_error` — a solver exception inside the sweep
  surfaces as the frontier job's terminal `error` status, not a synchronous 5xx); and
  `TestSolverWorkerContextGuard` (`_compute_frontier`/`_solve_online`/`_solve_ratebook` each raise
  `RuntimeError` when called outside `solver_worker_context()`, succeed inside it, and the guard's
  contextvar resets after the context exits). The frontier status route is pinned as a synchronous
  handler, and both solver entry points are pinned to reject a missing worker `start_time`.
- **`tests/test_optimiser_routes_critical_edges.py`** — targeted edge cases not covered by the
  main route test file: rejecting non-mapping/invalid artifact handles, missing/incomplete
  artifact summaries, runtime state disappearing mid-request after a `touch_heavy_objects` call,
  orphan-artifact cleanup after a lost atomic-update race, frontier-select null-point-index
  clearing, and race/invalid-handle paths for `/frontier` (now asserted through
  `run_frontier_and_wait`'s terminal frontier-job status — `contract_error`/`http_status_code: 409`
  for a lost atomic-update race, `error`/`http_status_code: 500` for an invalid persisted apply
  artifact handle — rather than a synchronous response code) and 409-on-race for `/frontier/select`
  (still synchronous; point selection was not moved to a background job).
- **`tests/test_optimiser_routes_real_library.py`** — runs against the real `price-contour`
  library (not mocked) rather than a stub, organized into `TestRealLibraryShapeContracts` (pins
  that the route's frontier compute budget constant equals the library's own
  `max_total_points` default — the two must never drift apart), `TestRatebookApplyDetailContract`,
  `TestOnlineApplyDetailRealSchema`, `TestFrontierComputeBudgetContract`, and
  `TestEstimateSingleScanContract` (pins the "exactly one streaming scan" cost contract for
  `/estimate`); the frontier-touching tests in these classes all go through
  `run_frontier_and_wait` and assert on the polled `result` payload rather than an immediate
  response body.
- **`tests/test_optimiser_service_coverage.py`** — apply-result and ratebook-factor artifact
  handles: validation of kind, version, format, filename, directory containment and null bytes;
  persistence returning `None` for non-dataframe inputs and cleaning its directory when a write
  or lazy sink fails; loading and scanning that report a missing artifact and reject a corrupt
  Parquet file; side-input identity for online versus ratebook modes; and orphan cleanup that
  logs and swallows failures and dispatches to the ratebook-factor cleaner. (The streaming
  contiguity, projection, checkpoint, memory-limit, non-finite, interleaving and frontier-range
  contracts live in `tests/test_optimiser_contracts.py`, above.)
- **`tests/test_optimiser_seeding.py`** — setup, auto-range, and the input estimate under seed
  plans, run through `_execute_pipeline` as their callers run it: setup after a batch training
  run seeds the training run's captured join and builds nothing; a ratebook setup cold builds
  each node once and captures the data producer and the banding side input — the two-input
  Optimiser is neither checkpointed nor captured — and warm seeds both and builds nothing; a
  banding side input from an `apiInput` port is built on every run but never captured, and
  factor extraction succeeds cold and warm; auto-range, whose own demand is narrower than the
  solve's, captures the solve's columns so the following solve seeds, and streaming auto-range
  does the same at its pre-expansion base, so the solve seeds the base and builds nothing above
  it; the input estimate seeds
  setup's capture; and a real solve, auto-range, and estimate through the routes create no
  checkpoint directory.
- **`tests/test_optimiser_service_validation.py`** — focused unit tests for
  `_validate_and_project`'s non-finite/overflow/null-quote-id detection (including float64→
  float32 overflow rejection) and end-to-end single-/multi-quote real-solver lifecycle tests
  pinning response shape.
- **`tests/test_optimiser_apply.py`** — node-type registration, parser inference, codegen,
  executor passthrough for both modes, `ApplyOnlineHelper`/`ApplyRatebookHelper` (composite
  ratebook factor tables and their contract-error cases), and a "bundler" test class.
  `TestLimitedOnlineApply` pins limited reads: the limited result equals the unlimited
  prefix and applies to only the selected quotes' rows, an upstream row-local scorer predicts
  only those quotes' scenario rows, ratio-constraint results are identical under any limit,
  and `online_apply_output_schema` equals the eager apply's schema with renames, version
  columns, and a custom quote-id column.
- **`tests/test_optimiser_apply_artifacts.py`** — the artifact-handle contract directly: round-
  trip persist→load, rejecting a path outside the owned root, rejecting a directory/file
  mismatch, rejecting a relative path.
- **`tests/test_optimiser_apply_trace_enrichment.py`** — the explainability module directly:
  online candidate-explanation attachment, ratio-constraint linearisation via the real library,
  unconstrained-artifact handling, ratebook factor-ladder explanation (plain, composite,
  unseen-level-as-neutral, float-keyed-level agreement with the engine), missing-component-column
  errors, reconciliation-mismatch and missing-output-column error surfacing, explicit-empty
  config-value rejection (`optimised_value_column`, artifact `quote_id`, artifact `mode`),
  Polars-type-mismatch fallback to the Python match path, duplicate-level "last wins" agreement
  with the runtime engine, and the `ImportError`-without-`exc.name` safe-rendering case. Limited
  traces pin a `max_pct` ratio explanation linearised against the whole portfolio, a custom
  quote-id column explained from exactly the clicked quote's rows, and
  `test_trace_correlates_only_the_input_an_apply_reads`.
- **`tests/test_optimiser_frontier_materialisation.py`** — frontier-point selection/materialisation
  in isolation: cached-summary reuse without touching the solver, malformed/partial frontier-
  point rejection, explicit-point save/mlflow-log without a live solve result, stale-solve-result
  avoidance, ratebook-point contract error on `/apply`, artifact-vs-response-preview agreement,
  distinct data per point index, store-copy-stable generation fencing, and
  config-name-vs-column-name normalisation.
- **`tests/test_optimiser_ratebook_apply_agreement.py`** — cross-checks that the saved-artifact
  ratebook path (`TestMirrorAgreesWithEngine`) and a real end-to-end solver run
  (`TestRealSolverEndToEnd`) produce identical priced factors, plus float-emitted-level
  canonicalisation pinning (`TestFloatEmittedLevelsCanonicalisedAtSave`).
- **`tests/test_rating_dtype_contract.py`** + **`tests/fixtures/rating_key_cases.py`** —
  the shared rating-dtype matrix, mandatory descriptor persistence, malformed/legacy-metadata
  rejection, and exact save/apply dtype mismatch errors.
- **`tests/test_optimiser_io.py`** — `load_optimiser_artifact`/`load_mlflow_optimiser_artifact`
  caching behaviour (content-hash cache hit/miss for file loads across the two MLflow source
  types), version resolution, the `destination` argument forwarded to `resolve_backend` for the
  empty and explicit keys, and the same run on two backend identities producing two distinct cache
  entries. `tests/test_optimiser_apply.py`, the deploy scorer tests, and
  `tests/test_optimiser_apply_trace_enrichment.py` pin that every config-driven caller forwards
  `mlflow_destination`; `tests/test_mlflow_destinations_e2e.py` (mlflow-model-registry) proves the
  local-folder-while-Databricks-is-configured apply, deployed scoring, and generated-script paths end to end.
- **`tests/test_optimiser_golden.py`** — golden-snapshot pinning: the `/solve/status` route
  response against `tests/fixtures/ui_contracts/solve_optimiser_response.json`, and
  `_build_artifact_payload` against `tests/fixtures/golden/optimiser_artifact_online.json` /
  `optimiser_artifact_ratebook.json`.
- **`tests/performance/test_optimiser_memory_response_perf.py`** — the frontier route's response-
  size cap under a large point frame (polls `/frontier/status/{job_id}` to `completed` and asserts
  the cap against the polled `result`, since the sweep itself now runs off the request thread), and
  that completed optimiser jobs get their heavy runtime objects slimmed and owned artifacts
  evicted (a job-store/memory-discipline test, not a wall-clock benchmark).
- **`tests/test_optimiser_setup_worker.py`** — process-mode materialisation: real spawn workers
  produce the same online and ratebook solves and the same chunked and full-frame auto-range
  totals as the thread path, and remove the setup-owned parquet; an input the thread path would
  borrow is written to the parent's file; an auto-range worker stopped mid-reduction leaves no
  scratch files; a stopped setup worker leaves neither scratch, factors nor input files; a
  `MemoryError` behind a setup or auto-range failure is a 507; with an inline stand-in for the
  worker, a child's failure is replayed exactly as the thread path records it, a child over its
  memory budget ends the job `memory_limited` (auto-range and solve setup), a memory-shaped worker
  failure is a 507, a stopped worker is the job's stop and its stop signal follows the job's
  cancellation, the worker's own timeout times the job out, an auto-range already out of time
  never starts its worker, a failed solver-input write removes the ratebook factors the child
  persisted, and a crashed worker, a timed-out setup worker (which has no timeout) and a
  malformed outcome are errors. `OptimiserWorkerFailure` keeps only a record's failure fields,
  and a child failure before any job mapping (admission, a pre-job HTTP error, a changed chunk
  plan) is classified in the child.
- **`tests/performance/test_auto_range_memory.py`** — the chunked auto-range memory bound on the
  representative fixture (200,000 quotes, a scenario expander and real CatBoost scoring between
  the base and the optimiser): at a fixed chunk size, 20 scenarios peak within one and a half
  times the 5-scenario peak plus 64 MiB, each run measured in a fresh interpreter by
  `tests/performance/_auto_range_memory_probe.py`.

- **`tests/test_optimiser_level_tie_properties.py`** — the generated level-tie family
  (ENG-T11, ledger W09-S03): for generated Int64, Float64, Float32 and String levels, single
  and composite, the generator plays price-contour's emission role (`str()` of the typed
  value) and perturbs the spelling (`repr`, fixed and scientific notation, the widened
  binary32 representation, the raw float). Properties: every spelling of one typed value
  saves to one `__factor_group__` key that an independent oracle predicts, distinct typed
  values never share a key and carry the generator's quote counts, two spellings of one value
  in one emitted table are refused with the loud `ValueError`, the saved artifact rates every
  typed row with its own level with the engine and the explainability mirror agreeing and no
  miss logged, and a duplicate saved key resolves to the last entry in both. Negative
  controls built with `hypothesis.find`: verbatim (uncanonicalised) labels let the apply
  path silently keep the last of two rates for one value (the 3b.10 fault the loud check
  prevents), and a first-entry-wins mirror disagrees with the engine. The real solver's
  verbatim emission format stays pinned by the fixtures in
  `test_optimiser_ratebook_apply_agreement.py`; it is not run per example.

The generated family above covers level-label tie-breaking across the supported dtype
families; the dtype agreement matrix in `test_optimiser_ratebook_apply_agreement.py` and
`test_optimiser_apply_trace_enrichment.py` remains the exhaustive curated counterpart.

## Canonical frontier range implementation

The required behaviour is defined in
[the optimiser high-level contract](high-level.md#canonical-frontier-ranges).

- `src/haute/routes/_optimiser_service.py::_auto_frontier_ranges_from_config` resolves ranges
  exclusively from `frontier_ranges`; it contains no global-range compatibility branch.
- The missing/malformed/range-order failure model remains strict and names the exact constraint.
- Backend fixtures that exercise frontier computation use per-constraint ranges; historical
  scalar-field fixtures are deleted.
## Decoded input widths for setup chunking

Automatic optimiser-grid and ratebook-factor chunk sizing uses the larger of
the existing Parquet page-size estimate and a bounded decoded sample (at most
512 rows). Each sampled column has an eight-byte minimum, and strings/binary
use their decoded resident width. Repeated dictionary values must not make a
large decoded batch look like a tiny encoded page. Explicit positive row
overrides retain their current meaning. The existing setup execution limits
remain authoritative: chunked input still builds a resident solver grid.
## Reuse and admit the resident grid input

Grid setup reuses a single local Parquet scan when its optimised plan is only
the scan/projection, without predicates, slices, virtual columns, schema
overrides, hive partitions, column mapping or deletion files. Its physical
column dtypes must agree with the projected frame. No-op casts are omitted.
The current caller keeps its input lease alive; borrowed input is never removed
by grid cleanup. Multipart or transformed input retains one projected adapter
file because the installed solver interface accepts one file.

Before constructing the resident grid, estimate numeric vectors, quote IDs,
sorting/conversion overlap and one reader batch from row count and decoded
sample widths. Refuse an estimate exceeding the current execution context's
remaining allowance with the existing typed admission error. The estimate is
conservative and does not replace runtime limits. The existing solver/frontier
work keeps sharing its prepared grid; this change adds no parallel grid copies.
