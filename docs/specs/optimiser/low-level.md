# Optimiser — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/routes/optimiser.py` | FastAPI router (`/api/optimiser/*`). Owns request/response assembly, frontier-point summary derivation, artifact-payload building/validation for save and MLflow log, and the module-level `_store`/`_solve_service` singletons. |
| `src/haute/routes/_optimiser_service.py` | `OptimiserSolveService` and its supporting free functions: pipeline execution, schema/value-contract validation, quote-grid construction, solver dispatch (online and ratebook), frontier-auto-range estimation (foreground and streaming/background), apply/ratebook-factor artifact persistence, and ratebook factor-table canonicalisation/serialisation. This is the largest and most detailed module in the component (~5,060 lines). |
| `src/haute/routes/_optimiser_limits.py` | Shared response-size and solver-compute budgets: `APPLY_PREVIEW_ROW_LIMIT`, `FRONTIER_POINT_LIMIT`, `FRONTIER_COMPUTE_LIMIT`, `enforce_frontier_compute_budget`, `limited_apply_preview_payload`, `limited_frontier_payload`. |
| `src/haute/_optimiser_io.py` | Loads a previously saved optimiser artifact for the `OPTIMISER_APPLY` node — from a local JSON file (content-hash cached) or from MLflow (run-id/version cached). Analogous to `_mlflow_io.py` and `_io.py`. |
| `src/haute/_optimiser_apply_explainability.py` | Builds a structured trace-detail payload for one clicked `OPTIMISER_APPLY` output row, for both online and ratebook modes. Consumed by the tracing subsystem, not exposed as its own route. |

## Key types and data structures

### `OptimiserSolveService` (`_optimiser_service.py:2539`)

Constructed once per process with the shared `JobStore` (`_store = get_job_store("optimiser")`
in `optimiser.py`). Instance state:

- `_lifecycle: JobLifecycle` — the single choke-point for CAS-guarded terminal-state
  transitions (`completed`/`error`/`cancelled`/`superseded`/`timed_out`/`contract_error`/
  `memory_limited`).
- `_start_lock: threading.Lock` — a coarse, process-wide lock held only across the "check no
  concurrent job, register job, acquire singleflight" admission section of `start()` and
  `start_frontier_auto_range()` — not held for the duration of the solve itself.
- `_auto_range_jobs`, `_solve_jobs`, `_graph_node_setup_jobs`: three independent
  `CancellableJobRegistry` instances. `_solve_jobs`/`_auto_range_jobs` key single-job
  cancellation by `(job_type, job_id)`/a job key respectively; `_graph_node_setup_jobs` keys by
  the *graph+node* coordination key so at most one heavy operation (solve, estimate, or
  auto-range) is registered as "active" for a given graph/node — `register_latest` on this
  registry supersedes/stops whatever was previously registered under the same key, which is how
  a solve's setup phase hands off its own job id across the same coordination key it was
  registered under.
- `_graph_node_setup_singleflight: SingleFlightCoordinator` — a second bookkeeping structure
  keyed identically to `_graph_node_setup_jobs`, used to *reject* (409) a concurrent submission
  for the same graph/node rather than superseding it. The two structures overlap by design (one
  supersedes within a job-type handoff chain, the other enforces "only one active op" across job
  types) and every exit path in the file releases both together.

### `SolveContext` (`_optimiser_service.py:2389`, frozen dataclass)

Per-solve context threaded through `_solve_online`/`_solve_ratebook`: job id, node id, mode,
store, execution context, streaming chunk size, single-flight key, and a `check_cancelled`
callable — the single object both solver code paths use to check for cooperative cancellation.

### Other dataclasses

- `_StreamingAutoRangePlan` (line 412, frozen) — a *proven* streaming/chunked auto-range plan:
  base node id, scenario-expander node id, the intermediate node chain, required columns, and a
  `ChunkPlan`. Only constructed when every intermediate node is verified row-local (see
  Edge cases below).
- `_ChunkSizeDecision` (422, frozen) — `(chunk_size, provenance)`, recording whether a chunk
  size came from explicit config or a byte-budget policy.
- `FrontierAutoRangeContext` (1590, frozen) — per-job bundle of chunk size, partition count,
  execution context, and streaming chunk size for one auto-range run.
- `_ScenarioFrontierRangeAccumulator` (1424) — a disk-bucketed accumulator that combines
  per-quote scenario min/max across many batches by hash-partitioning into parquet parts and
  combining them in `finish()`, so auto-range estimation never has to hold the full per-quote
  range set in memory at once.

No `TypedDict`s are defined anywhere in the component; job-store entries and artifact handles
are plain `dict[str, Any]`, validated defensively at each read site rather than at a type
boundary.

### Job dict shape

A job is a plain dict living in the shared `JobStore`. Fields this component reads or writes
(non-exhaustive, see [background-jobs](../background-jobs/high-level.md) for the store itself):
`status`, `job_type` (`"solve"` / `"frontier_auto_range"` / `"frontier_recompute"`; the
`"estimate"` job type constant is defined but never actually assigned — see Edge cases),
`progress`, `message`, `config`,
`node_label`, `start_time`, `timeout`, `result`, `base_result` (the pre-frontier-point-overlay
summary, used to reconstruct any frontier point without re-solving), `frontier_data` (the raw,
unlimited frontier points — distinct from `result["frontier"]`, which is the size-limited
frontend payload), `selected_frontier_point`, `artifact_handles` (dict of named artifact
handles, see below), and, only while heavy state is retained, `solver`, `quote_grid`,
`solve_result`, `factor_level_counts`, `factor_level_order`, `setup_chunking`.

### Artifact handle shape

`{"kind": "optimiser_apply_result" | "optimiser_ratebook_factors", "version": 1, "format":
"parquet", "path": str, "directory": str, "row_count": int, ["size_bytes", "columns"]}`. Handles
are validated structurally on every load/cleanup (`_validate_server_owned_parquet_handle`,
`_optimiser_service.py:994`) — kind/version/format must match exactly, `directory`/`path` must
be non-empty absolute strings with no NUL bytes, and after resolving symlinks the directory must
be a direct child of the component's own artifact root with the expected name prefix. This
prevents a tampered or foreign handle from causing a read/delete outside the artifact root.
`tests/test_optimiser_apply_artifacts.py` pins this contract directly (round-trip, path-outside-
root rejection, relative-path rejection, directory/file mismatch rejection).

### `OptimiserApplyTraceError` (`_optimiser_apply_explainability.py:25`)

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

### Solve submission and setup (`optimiser.py:solve` → `_optimiser_service.py`)

`POST /api/optimiser/solve` flattens the graph, calls `OptimiserSolveService.start(body)`
(`_optimiser_service.py:2561`), which:

1. Finds the `OPTIMISER` node and validates its config (`_validate_config`, static, `:4100`) —
   objective present, `mode` in `{online, ratebook}`, ratebook has `factor_columns`, `timeout`
   config value valid.
2. Computes the ratebook factor-level display order up front (`_compute_ratebook_factor_level_order`, `:2121`; `{}` for online) and the required-column projection seed
   (`_optimiser_solve_required_columns_by_node`, `:736`).
3. Under `_start_lock`: rejects (409) if another blocking solve is already `running`
   (`_check_no_concurrent_jobs`) or if this exact graph+node already has an active setup/solve/
   auto-range job (`_active_graph_node_setup`); otherwise creates the job (`status: "running"`),
   registers a fresh `ExecutionCancellationToken` in `_graph_node_setup_jobs` (superseding any
   prior job for the key) and the singleflight coordinator, then registers the same job id in
   `_solve_jobs`.
4. Outside the lock, spawns a daemon thread (`_launch_setup_background` → `_run_solve_setup_and_launch`, `:2680`) and returns `OptimiserSolveResponse(status="started", job_id=...)`
   immediately.

The setup thread, inside a `tempfile.TemporaryDirectory` checkpoint dir (chosen specifically
because it is cleaned up even on a crash or signal, unlike a manual `mkdtemp` + `finally
rmtree`):

1. Admits an `ExecutionContext` (profile `OPTIMISER_SETUP`) — this is where a memory-admission
   failure (507) can originate.
2. Runs the pipeline up to the optimiser node via `_execute_pipeline` (`:4152`) — see below.
3. Resolves the actual scored-data lazy frame via `_resolve_data_source` (`:4361`).
4. Validates schema and value contracts and projects/casts to solver dtypes via
   `_validate_and_project` (`:4405`).
5. For ratebook mode only, extracts and persists the banding-source factor columns to a parquet
   artifact (`_extract_factors`, `:4622`).
6. Explicitly drops the lazy-output references and runs `gc.collect()` before building the grid,
   to release memory ahead of the (often large) grid-build step.
7. Sinks the scored data to a temp parquet and builds the solver's `QuoteGrid` via
   `price_contour.build_grid_from_parquet_chunked` (`_build_grid`, `:4719`), choosing a chunk
   size from either explicit config or a byte-budget policy against the parquet's own metadata.
8. Launches the actual solver thread (`_launch_background`, `:4839`), passing the built
   `QuoteGrid`, config, and (ratebook) the factors handle and factor-level order.

Every failure mode in this thread (cancellation, `HTTPException`, memory-admission error,
bounded-streaming-unsupported error, or a bare exception) is mapped to a terminal job-store
transition rather than propagated — nothing in the setup thread's failure path is visible to a
caller except through the status-polling endpoint.

### Solver execution (`_launch_background` → `_solve_online` / `_solve_ratebook`)

The spawned solver thread updates progress to "Solving", then — inside
`temporary_streaming_chunk_size(...)` and an execution-context stage — calls:

- **Online** (`_solve_online`, `:2339`): constructs `price_contour.OnlineOptimiser(objective,
  constraints, max_iter, tolerance, record_history)` and solves directly against the passed
  `QuoteGrid`.
- **Ratebook** (`_solve_ratebook`, `:2405`): requires a non-`None` ratebook factors handle
  (raises `RuntimeError` otherwise — "Ratebook mode requires a banding source"); persists an
  eager `pl.DataFrame` handle if one was passed directly rather than already on disk; validates
  the configured factor columns against the artifact's own columns; builds `factor_contexts` via
  `_build_ratebook_factor_contexts` (`:1993`, which calls
  `price_contour.build_ratebook_factor_contexts_from_parquet_chunked` with the solved
  `QuoteGrid`'s quote population passed in for cross-validation); constructs
  `price_contour.RatebookOptimiser(objective, constraints, factor_columns, max_iter,
  max_cd_iterations, cd_tolerance, tolerance)` and solves; after solving, computes per-level
  quote-exposure counts and serialises the factor tables into their canonical, apply-joinable
  level keys (`_ratebook_factor_level_counts_from_artifact` → `_serialise_ratebook_factor_tables`, see Edge cases).

Both call the shared `_finalize_solve_result` (`:2137`), which builds the API-facing
`result_dict`, optionally computes an efficient frontier inline (non-fatal on failure — a
frontier failure is recorded but does not fail the solve), persists the apply-result artifact
(freeing the in-memory result dataframe as a side effect — see Artifact lifecycle below), and
atomically transitions the job to `completed`.

Any `ValueError` raised anywhere in the solver call stack is classified as a "Data error" and
transitions the job to `contract_error`; any `RuntimeError` is classified as an "Algorithm
error" and transitions to `error`; anything else is an "Unexpected error" → `error`. This
classification is purely by exception type, not by origin.

> NOTE: because the classification is type-based, a `ValueError` raised inside `price-contour`
> for what is actually an internal algorithm defect (not a data problem) is still reported to
> the user as a data/contract error. See `_optimiser_service.py:4950-4975`.

### Frontier auto-range estimation

Two entry points share `_prepare_frontier_auto_range` (`:3396`) — validate config/mode, resolve
chunk size/partition count/timeout, compute the required-column projection, and attempt to
prove a `_StreamingAutoRangePlan` (`_build_streaming_auto_range_plan`, `:863`), falling back to
the classic non-streaming path when the plan cannot be proven (`ProjectionImpossibleError`) or
raising a 422 when the chunking itself is unsupported (`ChunkPlanUnsupportedError`).

- `estimate_frontier_auto_range` (`:2923`) — **synchronous**: runs on the request thread, admits
  a non-cancellable execution context, calls `_run_frontier_auto_range_job` directly, and
  unconditionally deletes the job from the store in `finally` regardless of outcome. There is no
  polling API for this variant; any progress/metrics written to the job during execution are
  discarded the moment the request returns.
- `start_frontier_auto_range` (`:2973`) — **background**: under `_start_lock`, idempotently
  returns the existing job id if an identical graph/node auto-range job is already running
  (rather than 409-conflicting, unlike `start()`'s stricter behaviour), otherwise creates a
  cancellable job, registers it, and spawns a worker thread.
- `_run_frontier_auto_range_job` (`:3444`) dispatches to `_run_streaming_frontier_auto_range_job`
  (`:3675`, chunk-by-chunk via `iter_chunked_frames`, only reached when a streaming plan was
  proven) or the classic path (execute pipeline → resolve source → validate/project → batched
  collection into `_ScenarioFrontierRangeAccumulator` → `finish()`).
- `frontier_auto_range_status` (`:3062`) enforces the job's timeout lazily, on poll — there is no
  dedicated timeout-watcher thread for auto-range jobs, unlike solves (see below).
- `cancel_frontier_auto_range` (`:3090`) delegates to `_stop_frontier_auto_range_job`.

### Frontier computation and point selection (`optimiser.py`)

`POST /frontier` (`optimiser.py:run_frontier`, `:1468`) is split into a synchronous validation
phase and a background sweep phase, mirroring the solve submission pattern:

1. **Synchronous** (still on the request thread, so contract errors surface as 4xx on this
   request exactly as the old fully-inline route did): resolves the job's solver/quote-grid
   runtime state (touching heavy objects back into presence if evicted), enforces the compute
   budget (`enforce_frontier_compute_budget`, `_optimiser_limits.py:30` — rejects with 422 before
   the solver is invoked if `n_points_per_dim ** n_constraints` would exceed
   `FRONTIER_COMPUTE_LIMIT`), then checks `_has_running_frontier_job(body.job_id)` (`:1349` — a
   store scan for a `running` job of type `frontier_recompute` whose `parent_job_id` matches),
   rejecting with 409 if a sweep is already in flight for this solve job.
2. Creates a `frontier_recompute`-typed job (`status: "running"`, `parent_job_id` set to the solve
   job id, `timeout` derived from the solve job's own config via `_solve_timeout_from_config`) and
   spawns a daemon thread running `_run_frontier_sweep` (`:1360`) inside `solver_worker_context()`.
   The route returns immediately with `OptimiserFrontierResponse(status="started",
   job_id=<frontier_job_id>)` — a *different* job id from `body.job_id`, since the frontier sweep
   is tracked as its own job.
3. **Background** (`_run_frontier_sweep`): calls `_compute_frontier`
   (`_optimiser_service.py:1338`, a thin dispatcher: ratebook passes
   `ratebook_factors`/`factor_columns` kwargs to `solver.frontier(...)`, online omits them). The
   response is capped via `limited_frontier_payload` (`_optimiser_limits.py:81`, caps to
   `FRONTIER_POINT_LIMIT` while always reporting the true total and truncation flag) and the
   result stored as both the size-limited `result["frontier"]` and the raw `frontier_data` field
   on the *parent solve job* (via `_store.atomic_update(parent_job_id, ..., expected_status=
   "completed")` — 409-shaped as a `contract_error` on the frontier job if the solve job's state
   changed concurrently, since the atomic update itself cannot raise past the background thread).
   Any previously materialised frontier-point apply artifacts are invalidated (their handles
   removed from `artifact_handles` and their files cleaned up) since a recomputed frontier makes
   old point indices meaningless. On success the frontier job itself transitions to `completed`
   with the frontier payload as its own `result` field (a full `OptimiserFrontierResponse`, so a
   status poll and the (historical) inline response shape carry the same fields). Every failure
   path — an `HTTPException` raised inside the sweep (classified `contract_error` for 400/409/422,
   `error` otherwise) or a bare `Exception` — transitions the frontier job to a terminal status via
   `_frontier_lifecycle` (a dedicated `JobLifecycle(_store)` instance, `optimiser.py:1345`) rather
   than propagating; nothing about a post-validation frontier failure is visible to the caller
   except through polling.

`GET /frontier/status/{job_id}` (`optimiser.py:frontier_status`, `:1586`) polls the frontier job:
404s if the job type isn't `frontier_recompute`, lazily enforces the job's timeout on poll (the
same "no dedicated timeout-watcher thread" pattern as frontier auto-range — see below), and
returns `OptimiserFrontierStatusResponse` (`status`, `progress`, `message`, `elapsed_seconds`,
`result: OptimiserFrontierResponse | None`, `terminal_reason`, `error_code`, `http_status_code`,
`error_detail`, `execution_metrics`) — the same status-envelope shape used elsewhere in the
component (solve status, frontier auto-range status).

`POST /frontier/select` (`optimiser.py:select_frontier_point`, `:1446`) resolves one frontier
point's totals/constraints/lambdas into a full result summary
(`_frontier_point_result_dict`) without re-solving; for a ratebook job with
`include_ratebook_tables` requested, it additionally *materialises* that point by re-running the
already-built solver against the point's lambdas
(`_materialise_ratebook_frontier_point`, `_optimiser_service.py` via `optimiser.py:848`) —
cached if the job's current result already matches that point's lambdas exactly, otherwise
re-solved and the job updated atomically (409 if the job's state changed concurrently).

### Apply preview (`POST /apply`, `optimiser.py:apply_lambdas`, `:1264`)

Rejects ratebook jobs outright (`_reject_ratebook_apply_detail`, `:121` — checked before any
heavy-state lookup, since a ratebook `RatebookResult` has no per-quote dataframe). For online
jobs, resolves either a specific frontier point (materialising its apply dataframe via
`_materialise_frontier_point_apply`, `:988` — persisted once per point index and reused via
handle lookup thereafter) or the base solve result — from the still-live in-memory
`solve_result.dataframe` if present, or from the persisted apply-result artifact otherwise. The
response is capped via `limited_apply_preview_payload` (`_optimiser_limits.py:65`, first
`APPLY_PREVIEW_ROW_LIMIT` rows plus explicit `row_count`/`preview_truncated` metadata).

### Save and MLflow log (`optimiser.py`)

Both `save_result` (`:1648`) and `mlflow_log` (`:1723`) resolve the applicable `SolveResultLike`
(from a selected frontier point via `_solve_result_for_selected_frontier_point`, or directly from
the job's retained `solve_result`), build a shared JSON payload via `_build_artifact_payload`
(`:1519` — lambdas, objective/constraint totals, baseline totals, convergence/iteration counts,
column-name config, frontier-selection provenance, and for ratebook the factor tables), validate
it with `_validate_artifact_payload` (`:1603` — rejects a missing lambda mapping, a missing
total objective, a missing ratebook factor-table section, or *any* non-finite float anywhere in
the payload, naming up to 5 offending JSON paths), then either atomically write it to disk
(`atomic_write_text`, with `allow_nan=False` as a defence-in-depth backstop behind the explicit
validation) or attach it as an MLflow run artifact alongside metrics/params and (if present) a
frontier-points CSV. Tracking-URI/registry setup and experiment-name resolution for `mlflow_log`
go through the same shared `configure_mlflow_tracking()` / `resolve_experiment_name()` /
`build_run_url()` helpers in `haute.modelling._mlflow_log` that `routes/modelling.py` uses
(see [modelling low-level](../modelling/low-level.md#shared-mlflow-trackingexperiment-name-resolution))
— this component previously duplicated that logic inline rather than calling through
`log_experiment()` itself, since the optimiser's artifact shape (solver params, frontier CSV,
`optimiser_result.json`) doesn't fit `log_experiment()`'s model-diagnostics-shaped signature.

### Artifact lifecycle (persist / validate / load / cleanup)

Two artifact families, both rooted under a resolved subdirectory of the OS temp directory
(`<tempdir>/haute/optimiser_apply`, `<tempdir>/haute/optimiser_ratebook_factors`):

- **Persist** (`_persist_apply_result_artifact` `:1063`, `_persist_ratebook_factors_artifact`
  `:1105`, `_persist_ratebook_factors_lazy_artifact` `:1144` — the lazy variant sinks a
  `LazyFrame` via `bounded_sink` without ever collecting it into memory): each writes into a
  freshly created `mkdtemp` directory under the artifact root, and — for the apply-result case —
  explicitly nulls the source object's `.dataframe` attribute afterward (logging at debug level
  if the attribute cannot be cleared) so the heavy dataframe is not held twice, once on disk and
  once in the job store's retained `solve_result`.
- **Validate** (`_validate_server_owned_parquet_handle`, `:994`): re-derives the expected
  directory/path from the handle's own fields and checks they resolve (with a TOCTOU-aware
  strict-resolution-only-if-exists rule, so validating a handle whose artifact was already
  deleted does not itself crash) to a direct child of the artifact root with the expected name
  prefix and filename — every load and cleanup call goes through this check first.
- **Load** (`_load_apply_result_artifact`, `_load_ratebook_factors_artifact`,
  `_scan_ratebook_factors_artifact`): eager or lazy re-reads of the persisted parquet; any
  missing file, corrupt parquet, or invalid handle is wrapped into a 500 `HTTPException` with a
  "re-run the solve" message — the underlying OS/parquet exception text is never surfaced to the
  caller.
- **Cleanup**: `_cleanup_apply_result_artifact`/`_cleanup_ratebook_factors_artifact` are
  registered with the job store's own artifact-cleaner registry
  (`register_artifact_cleaner`, `_optimiser_service.py:1258-1259`) so a job's TTL/eviction sweep
  in [background-jobs](../background-jobs/high-level.md) can garbage-collect artifacts still
  attached to a job. This component separately handles the **orphan** case — an artifact created
  during a request but never successfully attached to a job, e.g. because a concurrent
  cancellation raced the atomic update that would have recorded its handle — via
  `_cleanup_orphan_apply_result_artifact` (`:1262`), called from many `finally`/failure branches.
  The attachment check itself is an *identity* comparison
  (`updated_job.get("artifact_handles") is not artifact_handles`) rather than an equality check,
  used to detect when the atomic job-store update silently no-opped because the job's expected
  status no longer held.

### Ratebook factor-table canonicalisation (`_optimiser_service.py:1670-2120`)

`price-contour` emits factor-table level labels from the verbatim string form of the source
value (e.g. a Float64 `25.0` becomes the label `"25.0"`), while the runtime rating join this
component's own apply path uses canonicalises keys via `normalise_rating_key`
([rating](../rating/high-level.md); `25.0` and `"25"` canonicalise to the same key). To make a
saved ratebook artifact's factor tables actually joinable at apply time,
`_canonical_ratebook_table_level` (`:1716`) tries every combination of verbatim vs.
float-collapsed candidate forms for each component of a (possibly composite) level label,
against the counted level keys actually observed in the solved frame, and keeps the combination
that collapses the *fewest* components — proven (per the function's docstring) to always be
unique, since a canonical key never itself contains a float's verbatim `"25.0"`-shaped form. A
level that matches zero candidate combinations, or — which the code treats as structurally
impossible outside of stale/mismatched inputs — ties on the fewest-collapsed-components rule,
raises `ValueError` rather than guessing.

`_ratebook_factor_level_counts` (`:1889`) computes the per-level quote-exposure counts this
canonicalisation is checked against, and deliberately raises rather than merging if two distinct
raw level tuples canonicalise to the same key — a last-writer-wins merge here would silently
drop a solved rate.

### Trace explainability (`_optimiser_apply_explainability.py`)

`explain_optimiser_apply_from_config(config, input_row, output_row, *, input_frames,
source_names, source_ids)` is the sole public entry point:

1. Loads the artifact the `OPTIMISER_APPLY` node was configured with (`_load_artifact_from_config`
   — file or MLflow, delegating to `_optimiser_io.py`), reading `mode` from it (defaulting to
   `"online"` if absent, but rejecting an explicitly present-but-blank `mode` as a
   misconfiguration).
2. Selects the correct parent lazy frame from `input_frames` (`_select_optimiser_apply_input`,
   shared with the runtime executor in `haute._builders`, so the trace path resolves the same
   input the real apply ran against).
3. Dispatches to `_explain_online` or `_explain_ratebook`.

`_explain_online` (`:141`) builds the online apply input frame
(`_prepare_online_apply_frame`), constructs a `price_contour.ApplyOptimiser` with the artifact's
lambdas/constraints/column names, and calls `applier.with_explainer_columns(df)` — the
`price-contour` API documented in full in
[`with_explainer_columns` contract](#with_explainer_columns-contract) below. It filters to the
clicked quote's rows, asserts exactly one `selected` and exactly one `is_baseline` candidate, and
checks the selected candidate's `scenario_value` against the actual output column (tolerant
numeric match, see `_values_match`) before returning the full candidate ladder plus the
selected/baseline rows.

#### `with_explainer_columns` contract

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

`_explain_ratebook` (`:291`) locates the matching input row for the clicked output row
(`_match_ratebook_input_row` — Polars-pushed-down equality filter first, falling back to a
bounded Python batch scan for cross-dtype/NaN-tolerant matching if the fast predicate errors or
finds nothing), then walks the artifact's `factor_tables` in order, for each factor: resolves
whether the table is a composite (joins on multiple columns, split via
`_split_ratebook_level`) or single-column table, looks up the matching entry via
`_match_ratebook_entry` (keys normalised through the same `normalise_rating_key` used at
runtime; ties resolved by walking entries in *reverse* to mirror the engine's
`unique(keep="last")` deduplication), applies the multiplicative neutral element `1.0` and marks
the factor `unseen` if no entry matches (the engine's own loud-neutral miss-path behaviour, not
an error), and accumulates a running product. The ladder is reconciled against the actual output
column at the end; a mismatch raises `OptimiserApplyTraceError`.

Every raised exception anywhere in this call graph is caught by
`explain_optimiser_apply_from_config`'s outer `try`/`except` and converted to `_error_detail(...)`
(`:619`) — an `ImportError` is specifically rewritten into an actionable
"install the missing library" message (falling back to a generic phrasing if the import
machinery didn't populate `exc.name`), everything else is logged with `exc_info=True` and
returned as a generic `status: "error"` payload.

## Edge cases and invariants

- **Single blocking op per graph/node.** `_check_no_concurrent_jobs` only blocks on the current
  job types considered "blocking" — `estimate` and `frontier_auto_range` are explicitly excluded
  (`_NON_BLOCKING_RUNNING_JOB_TYPES`), so an in-flight solve does not prevent an estimate or
  auto-range request for the *same* node, but a second solve does.
- **`_ESTIMATE_JOB_TYPE` is assigned by `/estimate`, not by frontier auto-range.**
  `_optimiser_input_metrics` (`routes/optimiser.py:231`, backing `POST /api/optimiser/estimate`)
  creates a short-lived job tagged `job_type = _ESTIMATE_JOB_TYPE` (`routes/optimiser.py:259`) and
  unconditionally removes it in a `finally: _remove_estimate_job(job_id)` block
  (`routes/optimiser.py:343`) — mirroring the "synchronous auto-range job is unconditionally
  deleted" pattern below. This tag is what lets `_NON_BLOCKING_RUNNING_JOB_TYPES` exempt an
  in-flight `/estimate` call from `_check_no_concurrent_jobs`'s store-wide scan.
  `estimate_frontier_auto_range`'s own internal job, by contrast, is created with
  `job_type = _FRONTIER_AUTO_RANGE_JOB_TYPE`, not `_ESTIMATE_JOB_TYPE` — a separate, correctly
  distinct job type, not evidence that `_ESTIMATE_JOB_TYPE` itself is unused.
- **Synchronous auto-range job is unconditionally deleted.** `estimate_frontier_auto_range`
  deletes its internal job from the store in `finally` regardless of success or failure, so any
  execution-metrics publisher bound to that job during the run is write-only from an external
  poller's perspective — nothing can observe its progress before the request returns.
- **std of a single-quote scenario-value distribution is hardcoded to `0.0`.**
  `_compute_scenario_value_stats` special-cases `n == 1` rather than calling Polars' sample
  standard deviation (`ddof=1`), which is undefined (`null`) for a single observation and would
  otherwise crash the subsequent numeric cast; `0.0` is treated as the true population value for
  a singleton, not a fabricated fallback (`_optimiser_service.py:1307-1314`).
- **Non-finite value validation happens post-cast, at Float32 precision.** The solver consumes
  Float32; `_validate_input_value_contracts` checks for NaN/Inf *after* the Float32 cast
  specifically so a Float64 value that only overflows to ±Infinity once down-cast is still caught
  as a contract violation, not silently passed through.
- **Null-value validation spans every dtype; non-finite validation is float-only.**
  `_null_check_columns` (`:302`) checks all of `finite_columns` (objective, constraint, and
  scenario-value columns) regardless of dtype via `pl.col(cname).null_count()`, while
  `_non_finite_check_columns` (`:294`) filters that same column set down to `schema[cname].is_float()`
  before checking `is_nan()`/`is_infinite()` — a non-float column (e.g. an integer objective) can
  never hold NaN/Inf, but it can hold null, so both checks run over the same source columns with
  different dtype gates and are reported as two independently-named detail messages
  (`_NON_FINITE_DETAIL_PREFIX` vs. `_NULL_VALUE_DETAIL_PREFIX`) if both fire.
- **Frontier sweep concurrency is scoped to the parent solve job, not the graph/node coordination
  key.** `_has_running_frontier_job` scans for a running `frontier_recompute` job whose
  `parent_job_id` matches — a mechanism independent of `_graph_node_setup_jobs`/
  `_graph_node_setup_singleflight` (which key by graph+node and gate solve/estimate/auto-range
  submission). `_FRONTIER_RECOMPUTE_JOB_TYPE` is also listed in `_NON_BLOCKING_RUNNING_JOB_TYPES`
  precisely because it never reserved a solve slot when frontier computation ran inline, and the
  background offload preserves that semantics — an in-flight frontier sweep never blocks a new
  solve/estimate/auto-range submission for the same graph/node.
- **Streaming auto-range only engages for provably row-local pipeline chains.**
  `_looks_chunk_local_user_code` uses an AST allow-list to decide whether user code between the
  data-input node and the scenario expander is safe to run per-chunk; anything not provably
  row-local (global state, ordering-sensitive logic, arbitrary custom code) silently falls back
  to the full non-streaming estimate path rather than raising — this is a memory/latency
  trade-off, not a correctness gate.
- **Ratebook factor artifact quote-id fallback.** `_ratebook_factor_artifact_quote_id` prefers
  the configured `quote_id` column name but falls back to a literal `"quote_id"` column if
  present, for compatibility with older artifacts that used the default name unconditionally.
- **Two overlapping single-active-job mechanisms.** `_graph_node_setup_jobs` (a
  `CancellableJobRegistry`) and `_graph_node_setup_singleflight` (a `SingleFlightCoordinator`)
  are both keyed identically by graph+node and released together at every exit path found in the
  file; this dual bookkeeping increases the surface area for a future edit that adds a new exit
  path and releases only one of the two.
- **Inconsistent error-detail exposure between two "generic setup failure" branches.**
  `_execute_pipeline`'s catch-all deliberately hides the real exception text from the client
  ("Pipeline execution failed. Check the server logs for details.") while `_build_grid`'s
  catch-all surfaces `f"Grid construction failed: {exc}"` directly. Both are plausibly
  intentional (grid failures are more likely user-actionable data issues) but the asymmetry is
  not documented as a deliberate choice in either function.
  > NOTE: worth confirming with the team whether this split is intentional policy or an
  > inconsistency to fix.
- **`decision_score`/`is_baseline` tie-breaking is fully owned by `price-contour`.** The
  [`with_explainer_columns` contract](#with_explainer_columns-contract) requires it to match
  `apply(df)`'s tie-breaking exactly, including which `scenario_value` is treated as baseline
  (`== 1.0` exactly, else nearest to `1.0`, else stable ordering) — this component never
  re-derives that rule, only consumes and reconciles the result.
- **Ratebook "unseen" factor levels are not errors.** A ratebook factor lookup that finds no
  matching entry for an input value is the engine's documented loud-neutral behaviour (counted
  and logged upstream, multiplicative identity `1.0` applied) — the trace payload marks it
  `unseen: true` / `status: "default"` rather than failing the trace.

## Error handling

- **Synchronous request-thread paths** (`optimiser.py` route handlers, and
  `estimate_frontier_auto_range`) raise `fastapi.HTTPException` directly for validation and
  contract failures: 400 (bad config, missing/wrong-dtype columns, null quote ids, non-finite
  values, a null value in an objective/constraint/scenario column, disconnected `data_input`,
  missing ratebook banding source, malformed frontier-point data, incomplete job summaries), 404
  (job not found or wrong job type), 409 (concurrent job/graph-node conflict, a frontier sweep
  already running for the target solve job, or an atomic job-store update losing a race against a
  concurrent state change), 422 (`ProjectionImpossibleError`/`ChunkPlanUnsupportedError`/
  `BoundedMemoryUnsupportedError`, and the frontier compute-budget rejection), 500 (a background
  worker thread failing to even start; a generic/unclassified pipeline failure; a missing or
  corrupt persisted artifact), 507 (`ExecutionAdmissionError`/`ExecutionMemoryLimitExceededError`
  wrapped via `_memory_limit_http_exception`). This applies to `POST /frontier` only up through its
  synchronous validation phase (runtime resolution, compute budget, already-running-sweep check);
  once validation passes, the request always returns 200 with a `status: "started"` body.
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
  `ChunkPlanUnsupportedError`, `ContractMismatchError`, `ProjectionImpossibleError`,
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

Tests live under `tests/` (unit/integration, `tests/performance/` for size/perf assertions), and
share fixtures from `tests/optimiser_fixtures.py`. No dedicated property-based tests were found
for this component; coverage is unit + integration + golden-fixture + real-library contract
tests.

Since the frontier sweep became a background job, every test file that calls `POST /frontier` and
expects a completed result now polls it via two shared helpers added to
`tests/optimiser_fixtures.py`: `poll_frontier_until_done(client, job_id, timeout=30.0)` (polls
`/frontier/status/{job_id}` to any terminal status) and `run_frontier_and_wait(client, payload,
timeout=30.0)` (posts to `/frontier`, asserts the immediate response is `status: "started"` with a
`job_id`, then polls it to completion) — used across `test_optimiser_routes.py`,
`test_optimiser_routes_critical_edges.py`, `test_optimiser_routes_real_library.py`, and
`tests/performance/test_optimiser_memory_response_perf.py`.

- **`tests/test_optimiser_routes.py`** — by far the largest file (~14k lines, dozens of test
  classes) covering the full route surface end-to-end against the FastAPI test client: node
  registration/codegen/executor passthrough, solve/status/estimate/apply/save/frontier/
  frontier-select/mlflow-log routes, ratebook solve, solve-with-history, scenario-value stats,
  column validation, non-convergence warnings, background-thread error classification, job-state
  guards (cancel/timeout/supersede races), pipeline-execution argument wiring, bounded-sink grid
  building, execute-pipeline cleanup, artifact-payload building (including extended/edge-case
  variants), mlflow-log extended paths, and many CAS/atomic-update race scenarios (`atomic_update`
  returning `None`, artifact orphaning on a lost race, etc.). Also covers: null-input rejection
  (`test_solve_rejects_null_input_values`,
  `test_frontier_auto_range_rejects_null_values_before_deriving_ranges`); the frontier
  background-job handshake (`test_frontier_returns_job_handle_promptly` — asserts the initial
  response returns before the sweep finishes, `test_frontier_after_solve`,
  `test_frontier_solver_exception_surfaces_as_job_error` — a solver exception inside the sweep
  surfaces as the frontier job's terminal `error` status, not a synchronous 5xx); and
  `TestSolverWorkerContextGuard` (`_compute_frontier`/`_solve_online`/`_solve_ratebook` each raise
  `RuntimeError` when called outside `solver_worker_context()`, succeed inside it, and the guard's
  contextvar resets after the context exits).
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
- **`tests/test_optimiser_service_coverage.py`** — scenario-expander/optimiser-input streaming
  contiguity, slim-projection column pruning, ratebook non-source-banding-input preservation
  across a checkpoint, ratebook factor extraction under a low memory limit, non-finite/null
  rejection in `_validate_and_project`, quote-block interleaving rejection in grid building, and
  explicit-frontier-range rejection both at the schema layer and the route layer before the
  solver is invoked.
- **`tests/test_optimiser_service_validation.py`** — focused unit tests for
  `_validate_and_project`'s non-finite/overflow/null-quote-id detection (including float64→
  float32 overflow rejection) and end-to-end single-/multi-quote real-solver lifecycle tests
  pinning response shape.
- **`tests/test_optimiser_apply.py`** — node-type registration, parser inference, codegen,
  executor passthrough for both modes, `ApplyOnlineHelper`/`ApplyRatebookHelper` (composite
  ratebook factor tables and their contract-error cases), and a "bundler" test class.
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
  with the runtime engine, and the `ImportError`-without-`exc.name` safe-rendering case.
- **`tests/test_optimiser_frontier_materialisation.py`** — frontier-point selection/materialisation
  in isolation: cached-summary reuse without touching the solver, malformed/partial frontier-
  point rejection, explicit-point save/mlflow-log without a live solve result, stale-solve-result
  avoidance, ratebook-point contract error on `/apply`, artifact-vs-response-preview agreement,
  distinct data per point index, and config-name-vs-column-name normalisation.
- **`tests/test_optimiser_ratebook_apply_agreement.py`** — cross-checks that the saved-artifact
  ratebook path (`TestMirrorAgreesWithEngine`) and a real end-to-end solver run
  (`TestRealSolverEndToEnd`) produce identical priced factors, plus float-emitted-level
  canonicalisation pinning (`TestFloatEmittedLevelsCanonicalisedAtSave`).
- **`tests/test_optimiser_io.py`** — `load_optimiser_artifact`/`load_mlflow_optimiser_artifact`
  caching behaviour (content-hash cache hit/miss for file loads across the two MLflow source
  types) and version resolution.
- **`tests/test_optimiser_golden.py`** — golden-snapshot pinning: the `/solve/status` route
  response against `tests/fixtures/ui_contracts/solve_optimiser_response.json`, and
  `_build_artifact_payload` against `tests/fixtures/golden/optimiser_artifact_online.json` /
  `optimiser_artifact_ratebook.json`.
- **`tests/performance/test_optimiser_memory_response_perf.py`** — the frontier route's response-
  size cap under a large point frame (polls `/frontier/status/{job_id}` to `completed` and asserts
  the cap against the polled `result`, since the sweep itself now runs off the request thread), and
  that completed optimiser jobs get their heavy runtime objects slimmed and owned artifacts
  evicted (a job-store/memory-discipline test, not a wall-clock benchmark).

Known coverage gaps: no property-based/fuzz testing of the ratebook level-canonicalisation
tie-breaking logic beyond the specific fixture cases in
`test_optimiser_ratebook_apply_agreement.py` and `test_optimiser_apply_trace_enrichment.py`; the
`_build_streaming_auto_range_chain_functions`/`_streaming_scenario_steps` functions noted as
possibly-dead code in `_optimiser_service.py` were not confirmed to be exercised by name in any
test file found during this review — a repository-wide reference check would be needed to
confirm whether they are load-bearing or vestigial.
