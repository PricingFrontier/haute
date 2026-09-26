# Optimiser — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/routes/optimiser.py` | FastAPI router (`/api/optimiser/*`). Owns request/response assembly, frontier-point selection, artifact-payload building/validation for save and MLflow log, and the module-level `_store`/`_solve_service` singletons. |
| `src/haute/routes/_optimiser_service.py` | `OptimiserSolveService`: job admission, pipeline execution, setup orchestration over the steps in `_optimiser_input.py`, solver launch over `_optimiser_solver.py`, and background frontier-auto-range estimation. Each setup step is one orchestration method over a free step in `_optimiser_input.py`: `_recorded_setup_failures` records an `OptimiserSetupError` on the job as its terminal state and answers the matching `HTTPException`; chunk provenance is recorded by `_record_setup_chunking`; a ratebook factor-extraction refusal is recorded by the setup failure mapping instead. It owns no filesystem deletion. |
| `src/haute/routes/_optimiser_solver.py` | The solver layer: the worker-context guard (`solver_worker_context`, `require_solver_worker_context`), the heavy entry points (`_solve_online`, `_solve_ratebook`, `_compute_frontier`) with `SolveContext`, result finalisation (`_finalize_solve_result`, the inline frontier, scenario-value statistics), and ratebook factor-table canonicalisation, ordering and serialisation. |
| `src/haute/routes/_optimiser_frontier.py` | The frontier domain: `OptimiserFrontierService` (sweep admission, `start_sweep`/`sweep_status`/`cancel_sweep`, background `_run_sweep` publication, `select_point`, `materialise_ratebook_point`, the online point apply (`request_point_apply`, `select_applied_point`) behind one per-job `LatestWinsQueue`, `solve_result_for_selected_point`, and a `parent_lock` per parent solve) and the pure frontier range, point and artifact-handle helpers. |
| `src/haute/routes/_optimiser_input.py` | Solve-input planning with no job or result knowledge: the column demand setup plans at each node (`_optimiser_solve_required_columns_by_node`, `_solve_columns_by_node`), the retained side inputs and execution target, exact data-input edge resolution (`_resolve_optimiser_input_edge`, `_resolve_optimiser_data_input_id`), the analysis-column plan (`resolve_analysis_plan`, `AnalysisPlan`), the value-contract expressions and their failure details, solver-input chunk sizing (`_chunk_size_decision_for_parquet`), resident-grid admission (`_admit_resident_grid`), the projected-parquet borrow check, and `_find_optimiser_node`. It also holds the setup steps themselves as free functions that never touch the job store: `resolve_data_input_frame`, `validate_and_project`, `validate_and_project_auto_range`, `validate_input_value_contracts`, `extract_ratebook_factors`, `resolve_analysis_frame`, `write_solver_input`, `grid_chunk_decision` (returns the chunk size and its provenance) and `build_quote_grid`, plus `grid_construction_failures`, which types a solver-input write or grid-build failure. A refusal is an `OptimiserSetupError` carrying the HTTP status and detail, the terminal reason, the message and the job fields: the HTTP status and detail, or `contract_error_job_fields` for a public contract error. The input estimate's pre-flight and single scan (`estimate_input_metrics`) and its typed answers (`ESTIMATE_MAPPED_ERRORS`, `estimate_failure_http_exception`) live here too, shared by the in-process count and the pool worker. |
| `src/haute/routes/_optimiser_artifacts.py` | The owned artifact lifecycle: the three ownership-marked artifact families (apply result, ratebook factors, quote analysis) — their roots, handle validation, persistence, loading, job-store cleaners, orphan cleanup and stale-startup reaping (`reap_stale_optimiser_artifacts`) — and setup's temporary files (the solver-input parquet, a worker's ratebook factors and quote-analysis directories, the range reducer's spill directory). |
| `src/haute/routes/_optimiser_outcomes.py` | The per-quote analysis side table (OPT-V09A): `write_quote_analysis` (the streamed constant-within-quote check, the one-row-per-quote reduction into `quote_analysis.parquet`, the missing-quote count and the cardinality metadata), `AnalysisColumnNotConstantError`, the table's row-count check against the grid (`require_one_row_per_solved_quote`), the scenario-grid record (`scenario_grid_from_values`, `require_scenario_grid`) and the lease-scoped reader `collect_quote_analysis`. See "Analysis-column side table and scenario grid" below. It also holds the bounded choice queries (OPT-V09B): `ChoiceTarget`, the reducers (`ScenarioHistogram`, `SegmentGroupBy`, `TopK`, `RowIndex`), `ChoiceQueryResult`, `ChoiceJoinError`, `lease_apply_frame` and `ChoiceQueryService.choice_query`. See "Bounded choice queries and point materialisation" below. |
| `src/haute/routes/_shared_flights.py` | The two schedulers behind OPT-V09B: `SharedFlights` (single-flight by key, one shared run, per-caller detach) and `LatestWinsQueue` (one run per group with a waiting slot of depth one, the replaced waiter refused with `FlightReplacedError`), both handing each caller a `FlightSubscription`. |
| `src/haute/routes/_optimiser_worker.py` | The hard-capped spawn workers that materialise optimiser inputs in process mode: `materialise_solve_input_worker` (solve setup's execute/validate/project/factor-extraction and the solver-input parquet) and `frontier_auto_range_worker` (the auto-range totals), their plain-data requests and outcomes, `SolveInput`, and `OptimiserWorkerFailure`, the child's terminal failure record that the parent replays onto the real job (raised there as `OptimiserWorkerFailureError`). It also holds the warm-pool estimate entrypoint `optimiser_estimate_worker`, which returns an `OptimiserEstimateOutcome` (the counts, or a status and detail) and records nothing. |
| `src/haute/routes/_optimiser_limits.py` | Shared response-size and solver-compute budgets: `APPLY_PREVIEW_ROW_LIMIT`, `FRONTIER_POINT_LIMIT`, `FRONTIER_COMPUTE_LIMIT`, `enforce_frontier_compute_budget`, `limited_apply_preview_payload` (a lazy count plus a bounded `head`, collected by the caller inside its lease), `limited_frontier_payload`. |
| `src/haute/routes/_frontier_point_summary.py` | The one derivation of a frontier point's solve summary: `frontier_point_summary` (from a `price-contour` frontier row), `apply_frontier_point_summary` (overlays it on a base result) and `FrontierPointDataError` (a malformed point, carrying the HTTP status the route reports). See Frontier point summaries below. |
| `src/haute/_builders.py` | Cross-component runtime registry owned by [execution-engine](../execution-engine/low-level.md). The optimiser component consumes its optimiser-apply online/ratebook closures; saved artifact validation and trace reconstruction must remain contract-compatible with those closures. |
| `src/haute/_ratebook_collar.py` | The ratebook combined-factor collar: `COMBINED_FACTOR_BOUNDS_KEY`, `combined_factor_bounds_from_grid` (the solve's `[sv_min, sv_max]`), `parse_combined_factor_bounds` and `CombinedFactorBoundsError`, shared by the solver, artifact validation, the runtime apply and the trace. |
| `src/haute/_price_contour.py` | The one runtime import point for the external `price_contour` library: `price_contour()` verifies the installed build once per process and returns the package; `price_contour_install()` reports the verified version and where it was installed from (the deploy container pin reads it). See The price-contour guard below. |
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

### `SolveContext` (`src/haute/routes/_optimiser_solver.py`, frozen dataclass)

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
frontend payload), `frontier_factor_tables` (ratebook only: one `{factor: {level: rate}}` dict per
retained frontier point, aligned with `frontier_data["points"]`, stored by the solve-time
frontier and replaced by every recompute; `None` for online jobs), `frontier_generation` (a non-negative integer initialised to `0` at solve
completion and incremented by every explicit recompute; the same atomic update writes it into
`result`, `base_result`, `frontier_data` and `result["frontier"]`, so every response that
carries a result or a frontier reports it — see "Frontier generation" below), `selected_frontier_point`,
`artifact_handles` (dict of named artifact handles, see below), `publish_summary` (the anchor's
MLflow summary — `params`/`metrics`/`artifacts` from `solver.summary(solve_result)` — computed
once in `_finalize_solve_result` before the apply dataframe is persisted, with every Polars
frame inside it converted to JSON records; `null` if the summary could not be built),
`input_provenance` (recorded when the solve job is created: `node_id`, the batch `data_source`
the solve executes against, the graph's `source_file`, and the `graph_fingerprint` the setup
single-flight key already computes), and, only while heavy state is retained, `solver`,
`quote_grid`, `solve_result`, `factor_level_counts`, `factor_level_order`, `setup_chunking`.
`scenario_grid` (recorded by setup right after the grid build, in both modes: the solver
input's complete grid as `[{optimal_step, scenario_value}]`, see "Analysis-column side table and
scenario grid"), and `artifact_handles["quote_analysis"]` when analysis columns are configured.
`publish_summary`, `input_provenance` and `scenario_grid` are not heavy keys and survive every
slimming.
`_result_finite_validated_for` (private, never in a response) records the
`(frontier_generation, selected_frontier_point)` whose `result` and `frontier_data` the status
route last walked clean for NaN and Infinity (see the solve result contract below).

### Artifact handle shape

`{"kind": "optimiser_apply_result" | "optimiser_ratebook_factors" | "optimiser_quote_analysis",
"version": 1, "format": "parquet", "path": str, "directory": str, "row_count": int,
["size_bytes", "columns"]}`. A quote-analysis handle also carries `analysis_source`
(`"data_input"` or `"side_input"`), `missing_quote_count` and `column_stats` (see
"Analysis-column side table and scenario grid"). Handles
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

### The price-contour guard

`src/haute/_price_contour.py` is the guard. Every runtime use of `price_contour` goes through `price_contour()`; callers look symbols up on
the returned module at call time (`price_contour().RatebookOptimiser(...)`), so tests that
patch `price_contour.<name>` keep working. `if TYPE_CHECKING:` imports of library types remain
allowed; `tests/test_decoupling_contracts.py` rejects any other `price_contour` import in
`src/haute` and any symbol read off `price_contour()` that the guard does not verify.

The first call (cached with `functools.cache`; a failure is not cached) imports the package and
its native extension `price_contour._price_contour` and collects **every** problem into one
`PriceContourCompatibilityError` (a `RuntimeError`):

- no `price-contour` distribution metadata;
- the module's `__version__` differs from the distribution metadata (a stale or shadowing
  build, such as `0.0.0+local`);
- the version is outside `REQUIRED_SPECIFIER`, which equals `pyproject.toml`'s
  `price-contour` specifier exactly (`>=0.5.0,<0.6`, pinned by
  `tests/test_dependency_contracts.py`); prereleases are rejected even inside the range;
- a missing entry of `REQUIRED_SYMBOLS` (every class, function and method haute calls);
- a keyword haute passes by name that a Python wrapper no longer accepts
  (`REQUIRED_PARAMETERS`, checked with `inspect.signature`; PyO3 classes are covered by the
  symbol check only);
- a failed import, chained as the error's cause.

The message names the installed version, the required specifier, the module path, the install
source (`editable checkout <path>` or `direct URL <url>` from PEP 610 `direct_url.json`, else
`wheel`) and the remedy: `uv sync --locked`, or `uv run maturin develop --release` in a
checkout. A verified install logs one `price_contour_verified` INFO line with version, source
and path. The test session verifies the real install once before any test patches it.

The deploy container pins `price-contour==<verified version>` through
`_pinned_price_contour_dependency` and raises `DeployError` when the build is an editable or
direct-URL install, because the container reinstalls from the package index by version and
cannot reproduce it; an incompatible install raises `DeployError` carrying the guard's
diagnosis.

### The price-contour contract haute relies on (0.5)

The library's own contract is section 13 ("Consumer Contract") of the design-decisions document in the price-contour repository.
Haute depends on these parts of it:

- **Canonical ratebook evaluation.** Every ratebook total (solve result and frontier row) is
  the evaluation of the final factor tables by one kernel: each quote at the grid step nearest
  the f32 product of its factor rates, clamped to the end steps, an exact midpoint going to the
  lower step. `RatebookResult.quote_results` is that per-quote frame and
  `RatebookOptimiser.evaluate(grid, factors, factor_tables)` reproduces it exactly. The
  combined-factor collar (`combined_factor_bounds`) is the same end-step clamp applied at
  deployment.
- **Ratebook frontier points.** `RatebookOptimiser.frontier(...).factor_tables` holds each
  point's tables, aligned with `points`; each row's totals are those tables' canonical
  evaluation. Haute keeps the retained points' tables as the job's `frontier_factor_tables` and
  materialises a selected point from them (see "Frontier computation and point selection").
- **Absolute bounds.** Frontier rows carry `bound_<c>` (absolute) beside `threshold_<c>` (the
  user's units, a fraction for `min_pct`/`max_pct`) for every constraint; solve results carry
  `constraint_bounds`.
- **One baseline rule.** Every baseline is the scenario value nearest 1.0 (f32, lowest on a
  tie); totals accumulate f32 values in f64.
- **Fail loud.** The library raises rather than returning a default for missing frontier
  totals or λ, a zero-baseline pct constraint, an unknown warm-start λ name, a non-positive
  candidate range, scenario values that are not strictly increasing, and the reserved
  constraint names `objective`, `step` and `scenario_value` (which haute's config validation
  also rejects).
- **`clamp_rate`** is a search-space diagnostic (the mean share of candidate targets outside
  the scenario range across the search), not a count of quotes at an edge; that count is
  `n_quotes_clamped_low` / `n_quotes_clamped_high`.

### Solver worker-context guard (`_optimiser_solver.py`)

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
   `_validate_and_project`. On the data-input analysis path the configured analysis columns are
   retained, uncast, beside the solver columns (and only those); on the side-input path the
   analysis frame is resolved through the same exact edge-name contract and projected to
   `quote_id` + the analysis columns (`resolve_analysis_frame`).
5. For ratebook mode only, resolves `banding_source` through the same exact edge-name contract,
   then extracts and persists that edge's factor frame to a parquet artifact (`_extract_factors`).
6. Explicitly drops the lazy-output references and runs `gc.collect()` before building the grid,
   to release memory ahead of the (often large) grid-build step.
7. Writes the scored data to a setup-owned temp parquet, or borrows an unchanged captured
   snapshot under the plan's lease (`_write_solver_input`), and builds the solver's `QuoteGrid`
   from that file via `price_contour.build_grid_from_parquet_chunked`
   (`_build_grid_from_parquet`), choosing a chunk size from either explicit config or a
   byte-budget policy against the parquet's own metadata. The builder decodes only the solver
   columns, so retained analysis columns never reach it, and `_build_grid_from_parquet` records
   the job's `scenario_grid` as soon as the grid exists. When analysis columns are configured,
   `quote_analysis.parquet` is reduced from the written solver input (or the side-input frame)
   before that file is removed (`_write_quote_analysis`): by the setup worker right after it
   writes the solver input in process mode, and by `_build_grid` after the grid in the thread
   mode; the parent then requires one table row per grid quote. The temp parquet is removed on
   every exit; a borrowed snapshot never is.
8. Launches the actual solver thread (`_launch_background`), passing the built
   `QuoteGrid`, config, the quote-analysis handle (when one was written) and (ratebook) the
   factors handle and factor-level order.

Steps 2–7's pipeline work is materialisation, and it runs in a hard-capped spawn worker in
production (`HAUTE_INTERACTIVE_EXECUTION_MODE=process`, the default), as training preparation
does. The setup thread creates the temp parquet path, opens the seed plan under its admitted
context (`_open_setup_seed_plan`, which prepares inputs and holds the plan's leases until setup
exits) and runs `materialise_solve_input_worker` through `_run_optimiser_worker`: the admitted
headroom (`isolated_execution_budget`) is both the child's execution budget and its native cap,
and the job's cancellation reason is the worker's stop signal, so cancellation or supersession
terminates the worker. The child adopts the plan (`SeedPlan.adopt`), runs
`_materialise_solve_input` (`_prepare_solver_frame` — steps 2–6 — then `_write_solver_input`
with borrowing off, then, with analysis columns, `_write_quote_analysis`) against a private job record in the `optimiser_worker` job store,
deleted when the child finishes, and returns a `SolveInput` (the parent's
parquet path, the constraint columns, the ratebook factors handle and the quote-analysis
handle) or the private record's
terminal failure. The child never borrows a captured snapshot: a capture it made is released
when its adopted plan closes, before the parent reads the file. Every location the child writes
is created and removed by the parent, however the worker exits: the solver-input parquet, the
marked quote-analysis directory (`_new_quote_analysis_directory`, into which the child writes
`quote_analysis.parquet`; removed when no job adopts its handle), the marked ratebook factors
directory (`_new_ratebook_factors_directory`, into which the child
persists; removed when the job never adopts its handle) and a scratch directory
(`worker_scratch_directory`) that the child routes all of its Python temporary files into (range
reducer bucket parts, staged batches, model-scoring temp files), so a stopped, timed-out or killed
worker leaves nothing behind. The parent checks the returned input is its own file and the
factors and quote-analysis handles name its own directories. A `MemoryError` a native cap raised in the child, however
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
mode without a separate analysis input, or the Optimiser itself (ratebook mode, or any mode
with a separate analysis input), which resolves along its selected `data_input` edge to its
producer — and every banding or analysis side input from the Optimiser's own edges that the run
executes,
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

### Solver execution (`_launch_background` → `_optimiser_solver._solve_online` / `_solve_ratebook`)

The spawned solver thread updates progress to "Solving", then — inside
an execution-context stage — calls:

- **Online** (`_solve_online`): constructs `price_contour.OnlineOptimiser(objective,
  constraints, max_iter, tolerance, record_history=True)` and solves directly against the passed
  `QuoteGrid`. Every online solve records its per-iteration `history` (bounded by `max_iter`);
  there is no configuration flag for it.
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
  cases). It records the solve's **combined-factor collar**,
  `combined_factor_bounds = {"min": sv[0], "max": sv[-1]}` from the solved grid's
  `QuoteGrid.scenario_values` (the Float32 grid values widened to Python floats, exactly what
  the solver scored; `combined_factor_bounds_from_grid` in `src/haute/_ratebook_collar.py`),
  and reads `clamp_rate` and `cd_iterations` directly off the library result. It maps the
  library's `per_factor_results` (one `PerFactorRecord` per inner grouped solve, in (CD pass,
  factor) order) into `ratebook_cd_trace` (see the result contract below). Like an online
  result, a ratebook result reports the shape of the grid it scored: `n_quotes` and `n_steps`
  come from the solved `QuoteGrid`, so the result preview's provenance strip and the artifact's
  `input_summary` name them for both modes. See Runtime ratebook apply below.

Both call the shared `_finalize_solve_result`, which builds the API-facing
`result_dict` (including `effective_bounds`, see Constraint bounds below), optionally computes an efficient frontier inline (non-fatal on failure — a
frontier failure is recorded but does not fail the solve), persists the online apply-result
artifact (online mode only: `_persist_apply_result_artifact` requires a per-quote Polars
frame and raises `TypeError` otherwise; it frees the in-memory result dataframe as a side
effect — see Artifact lifecycle below), and atomically transitions the job to `completed`.

**The solve result contract (`OptimiserSolveResult`).** `mode` is `"online"` or `"ratebook"`
and always present. Beside the totals, baselines, `effective_bounds` and λ (whose constraint
names must all agree, see Constraint bounds) the result carries:

- `input_summary` (`OptimiserInputSummary`, strict): the job's `input_provenance` (`node_id`,
  `data_source`, `source_file`, `graph_fingerprint`) plus `solver_settings`
  (`OptimiserSolverSettings`: `max_iter`, `tolerance`, `chunk_size`, and
  `max_cd_iterations`/`cd_tolerance` for ratebook; `frontier_enabled`,
  `frontier_steps` and `frontier_ranges` only when the solve requested a frontier). It is built
  once in `_finalize_solve_result` by `solve_input_summary(job)` from the solve-time config
  snapshot and `input_provenance`; a solve job without `input_provenance` fails loudly. The
  published artifact's `input_summary` and `solver_settings` are read from it (see Save and
  MLflow log), so there is one summary.
- `diagnostics_errors: [{diagnostic, error_type, message}]` (`OptimiserDiagnosticError`,
  `diagnostic` is `"scenario_value_stats"` or `"frontier"`): a diagnostic that could not be
  produced is recorded here instead of silently becoming `null`. An online solve whose
  scenario-value statistics cannot be computed (no per-quote frame, no
  `optimal_scenario_value` column, no quotes, or a computation error) keeps
  `scenario_value_stats`/`scenario_value_histogram` `null` and records the failure; a ratebook
  solve reports no statistics by design and records nothing. A failed inline frontier keeps
  `frontier_error` ("Frontier unavailable: …") and records the same failure as the `"frontier"`
  entry. Every entry is also logged (`optimiser_diagnostic_skipped`).
- `history: [OptimiserHistoryEntry]` (online): every online solve's per-iteration record
  (`iteration`, `total_objective`, `max_lambda_change`, `all_constraints_satisfied`, `lambdas`,
  `total_constraints`), always recorded and bounded by `max_iter`; `null` for ratebook results
  and frontier points.
- `ratebook_cd_trace: {records, truncated}` (`OptimiserRatebookCdTrace`, strict; ratebook): one
  record per inner grouped solve of the coordinate descent, in (CD pass, factor) order, each
  `{cd_iteration (1-based), factor, factor_index, total_objective, total_constraints, lambdas}`
  taken from price-contour's `PerFactorRecord` (the factor is named by the library, never
  inferred from position). Every record's `total_constraints` and `lambdas` hold exactly the
  result's constraint names, and there is at least one record. The trace keeps the last
  `HAUTE_OPTIMISER_CD_TRACE_LIMIT` records (default 1,000; a malformed value fails loudly) and
  sets `truncated` when it dropped earlier ones, as `loss_history` does. A record's totals are
  that inner solve's, on the search's working multiplier, so the last record's objective agrees
  with the canonical `total_objective` only to about 1e-6 relative. `null` for online results
  and for frontier points (a materialised ratebook point has no trace). The frontier-select
  response carries `history` and `ratebook_cd_trace` of the result it returns (the solve's when
  `point_index` is `null`).
- `scenario_grid: [OptimiserScenarioGridStep]` (both modes, required): the job's immutable
  scenario grid, `{optimal_step, scenario_value}` for every step in step order, copied from the
  job record `_finalize_solve_result` reads (a job without one fails loudly, as a missing
  `input_provenance` does). Steps are `0..n-1` and values strictly increasing; when `n_steps` is
  present it equals the grid's length. Frontier-point results carry the solve's grid (they
  share it).
- `factor_tables: {table: [OptimiserFactorTableRow]}`: each row is strict, exactly
  `__factor_group__` (the canonical level key, `level` in Python), `optimal_scenario_value`
  (finite) and `quote_count` (a non-negative integer). Online results carry `{}`.

`GET /solve/status` and `POST /solve/cancel` walk a completed job's `result` and
`frontier_data` for NaN or Infinity with `_non_finite_paths` (the walk the artifact validation
uses) before answering, the way the modelling status route applies `_assert_json_finite`. A
non-finite value corrects the job from `completed` to `error` ("Optimiser result cannot be
published: non-finite values at …", naming up to five paths) and clears its `result` and
`frontier_data`. A clean walk is cached on the job as `_result_finite_validated_for`, the
`(frontier_generation, selected_frontier_point)` it was made for, because a recompute or a
selection rewrites the result; a later poll for the same pair skips the walk. The flag is never
part of a response.

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
- `_run_frontier_auto_range_job` is the one auto-range job. It owns admission (entered
  without a context, as the background launcher enters it, it admits its own and releases it on
  every exit, so a failed job never leaves its memory reservation to garbage collection),
  cancellation,
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
  and completes with the returned totals and the worker's metrics adopted as evidence. In process
  mode `start_frontier_auto_range` plans with `sample_row_widths=False`: without a configured
  `auto_range_chunk_size` the plan is structural (`_StreamingAutoRangePlan.sized` is false), fixing
  the base node and its columns without sampling rows, and the in-process chunk runner refuses an
  unsized plan. The child re-plans (`_prepare_frontier_auto_range(prepare_snapshot_inputs=False)`;
  a chunk plan is not picklable) and sizes the chunks. When sizing loses the chunked plan the child
  returns only the `chunk_fallback`; the parent records it on the job, opens a whole-frame seed
  plan and runs a whole-frame worker (`_frontier_ranges_attempt` opens each attempt's plan and
  closes it when that worker exits), and the result carries the fallback. A sizing refusal that is
  not a chunk-plan rejection (`ChunkMemoryRiskError`) is the job's failure, not a start-time 422.
  Any other difference between the parent's and child's chunk decisions fails. The child runs this
  same job with
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

### Frontier computation and point selection (`src/haute/routes/_optimiser_frontier.py`)

`OptimiserFrontierService` owns the frontier domain: sweep admission, cancellation and
timeout, publication onto the parent solve, point selection, ratebook point materialisation
and the retained point apply artifacts. Every frontier state change for one parent solve holds
that parent's lock (`parent_lock(parent_job_id)`), so a slow artifact read, write or deletion
for one solve never delays frontier work on another; the routes in
`src/haute/routes/optimiser.py` only assemble responses. `POST /frontier`
(`run_frontier`, delegating to `start_sweep`) is split into a synchronous validation phase and
a background sweep phase, mirroring the solve submission pattern:

1. **Synchronous** (still on the request thread, so contract errors surface as 4xx on this
   request): resolves the job's solver/quote-grid
   runtime state (touching heavy objects back into presence if evicted), enforces the compute
   budget (`haute.routes._optimiser_limits.enforce_frontier_compute_budget` — rejects with 422 before
   the solver is invoked if `n_points_per_dim ** n_constraints` would exceed
   `FRONTIER_COMPUTE_LIMIT`). Under the parent's lock, it then checks
   `_has_running_sweep(body.job_id)`, creates the job, and registers its cancellation
   token as one atomic admission step; a sweep already in flight for this solve job is rejected
   with 409.
2. Creates a `frontier_recompute`-typed job (`status: "running"`, `parent_job_id` set to the solve
   job id, `timeout` derived from the solve job's own config via `_solve_timeout_from_config`) and
   spawns a daemon thread running `_run_sweep` inside `solver_worker_context()`.
   The route returns immediately with `OptimiserFrontierResponse(status="started",
   job_id=<frontier_job_id>)` — a *different* job id from `body.job_id`, since the frontier sweep
   is tracked as its own job. If `thread.start()` itself raises, the route transitions that
   frontier job to terminal `error`, releases its cancellation-registry entry, and returns a
   sanitised 500 response without starting sweep work.
3. **Background** (`_run_sweep`): calls
   `haute.routes._optimiser_solver._compute_frontier`, a thin dispatcher: ratebook passes
   `ratebook_factors`/`factor_columns` kwargs to `solver.frontier(...)`, while online omits them.
   The library sweeps serially (its `parallel` flag stays off): its parallel sweep was 39-89%
   faster on the measured frontiers but raised peak memory by up to forty times, moved 1-D lambdas
   by up to 9e-4 relative and changed 2-D points' convergence and lambdas, so it was not adopted
   (`scripts/benchmarks/opt-p06-frontier-parallelism.json`).
   A cancellation checkpoint runs before and after the external frontier call, so a cancel is
   observed when the sweep returns. The response is
   capped via `haute.routes._optimiser_limits.limited_frontier_payload` (caps to
   `FRONTIER_POINT_LIMIT` while always reporting the true total and truncation flag, and attaches
   `point_summaries`, one `frontier_point_summary` per returned point in point order; a point that
   cannot be summarised fails the frontier). The payload's `constraint_names` is **every**
   configured constraint and `swept_axes` is the constraints the sweep varied (the ranges'
   keys); the solve-time frontier, the recompute sweep and frontier select all summarise every
   configured constraint, never only the swept ones. The
   result stored as both the size-limited `result["frontier"]` and the raw `frontier_data` field
   on the *parent solve job* (via `_store.atomic_update(parent_job_id, ..., expected_status=
   "completed")` — 409-shaped as a `contract_error` on the frontier job if the solve job's state
   changed concurrently, since the atomic update itself cannot raise past the background thread).
   The same atomic update increments the parent's integer `frontier_generation`; point
   materialisation uses that value as its recompute fence, so correctness does not depend on a
   job store preserving nested Python object identity.
   Any previously materialised frontier-point apply artifacts are invalidated (their handles
   removed from `artifact_handles` and released through
   `JobStore.release_detached_artifact_handles`, which deletes each file at once or when the last
   reader's lease on it ends) since a recomputed frontier makes old point indices meaningless. The stop check, parent update, and frontier-job completion
   transition share the parent's lock; therefore cancel/timeout cannot become terminal
   between the check and parent mutation. On success the frontier job itself transitions to `completed`
   with the frontier payload as its own `result` field (a full `OptimiserFrontierResponse`, so a
   status poll and the (historical) inline response shape carry the same fields). Every failure
   path — an `HTTPException` raised inside the sweep (classified `contract_error` for 400/409/422,
   `error` otherwise) or a bare `Exception` — transitions the frontier job to a terminal status via
   the service's own `JobLifecycle` rather
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
`include_ratebook_tables` requested, it additionally *materialises* that point
(`OptimiserFrontierService.materialise_ratebook_point`) by attaching the factor tables the
library's frontier kept for it (`frontier_factor_tables[point_index]`) to the point's row
summary. It never re-solves and needs no solver, grid or factor contexts: price-contour 0.5
reports every ratebook frontier row's totals as the canonical evaluation of that point's
tables, so the row's totals, λ, convergence and clamp rate are the point's exactly, and
`cd_iterations` is the row's `iterations` (the ratebook frontier's CD pass count). (Re-solving
with the row's λ, as haute did before 0.5, could land on different tables than the row the user
picked.) The result is cached when the job's current result already matches that point's
lambdas exactly, otherwise the job is updated atomically (409 if its state changed
concurrently). Missing or misaligned `frontier_factor_tables` is a `500`, never a re-solve.

### Frontier generation

The job's `frontier_generation` is exposed as `frontier_generation` on `OptimiserSolveResult`
(the solve status `result`), on `OptimiserFrontierSelectResponse`, and on every computed
`OptimiserFrontierResponse` (the solve-time frontier, the solve status `frontier`, and a recompute
sweep's own `result`). It is `0` for a solve and its solve-time frontier and the parent's
incremented value for a recompute, set on the recompute's frontier payload and its reset result
under the parent's lock in the same update that increments the job field. Only the
`status="started"` handle returned by `POST /frontier` has none (`null`); a computed frontier
without one fails validation. Clients key anything derived from a frontier point (the
browser's `/apply` cache) by `(job_id, frontier_generation)`, since a recompute reuses point
indices for different points.

### Typed frontier points (`OptimiserFrontierPoint`)

A frontier's `points` are typed rows, never open objects. `frontier_point_rows(points_df,
mode=, constraint_names=)` in `_frontier_point_summary.py` converts price-contour's points frame
once, when the frontier payload is built (`limited_frontier_payload`, which takes the job's
`mode`): the frame's columns must be exactly `price_contour.frontier_points_schema(mode,
constraint_names)` (any missing or unexpected column fails the frontier, naming both lists), and
each row becomes one point in which the per-constraint columns are maps keyed by constraint
name, in configured order (`thresholds` from `threshold_<c>`, `bounds` from `bound_<c>`, `totals`
from `total_<c>`, `lambdas` from `lambda_<c>`) and every other column keeps its library name.
The point carries its `mode`, so the two shapes are distinct models (a plain union, no
discriminator keyword):

- `OptimiserOnlineFrontierPoint` (`mode: "online"`): `total_objective`, the four maps,
  `iterations`, `converged`, `solver_path` (`"bisection" | "subgradient"`),
  `non_convergence_reason` (`"above_envelope" | "bracket_exhausted" |
  "iteration_budget_exhausted" | null`; null on a converged point) and the eleven `sv_*`
  statistics (`sv_mean`, `sv_std`, `sv_min`, `sv_p5`, `sv_p25`, `sv_median`, `sv_p75`, `sv_p95`,
  `sv_max`, `sv_pct_increase`, `sv_pct_decrease`), all required.
- `OptimiserRatebookFrontierPoint` (`mode: "ratebook"`): `total_objective`, the four maps,
  `iterations` (the CD pass count), `converged`, `clamp_rate`, `n_quotes_clamped_low` and
  `n_quotes_clamped_high`, and no `sv_*`.

Both are strict (`extra="forbid"`, no coercion, no NaN or Infinity), and the four maps must hold
the same constraint names. Each row is validated with its mode's model as it is converted, so a
malformed library row fails the frontier (`FrontierPointDataError`, a 500) rather than reaching a
client. `OptimiserFrontierResponse` then enforces **map-key completeness** against its
`constraint_names`: every point's four maps and every point summary's `constraints`,
`effective_bounds` and `lambdas` hold exactly those names; `swept_axes` is a subset of them;
every point has the same mode; and a computed frontier has one summary per point. The stored
`frontier_data["points"]` are these typed rows, so select, the effective-constraints override
(`thresholds[name]`) and the MLflow CSV read them by name, never by parsing column prefixes.
The MLflow `frontier.csv` writes each point back as price-contour's flat row, in
`frontier_points_schema` column order (`frontier_point_library_row`), so the logged file is the
library's points table.

### Frontier point summaries

A frontier point's summary holds every result field that differs from the solve it was swept
from, read from the typed point: total objective, constraint totals for every configured
constraint, swept or not (`totals`), `effective_bounds` (see Constraint bounds below, from the
point's `bounds`), `lambdas`, converged, iterations, CD iterations (`null` on the row summary),
clamp rate (a ratebook point's; `null` online), history, CD trace (`ratebook_cd_trace`, always
`null`), scenario-value stats (an online point's
`sv_*` columns; `null` for ratebook), scenario-value histogram, factor tables, the non-converged
warning, the frontier error and `diagnostics_errors` (always `[]`: a point's statistics come from
its row, so the solve's degraded diagnostics do not describe it). Every field is always present
and `null` where the point has none; applying the summary to a base result removes a `null`
field. `OptimiserFrontierPointSummary` is strict and its `constraints`, `effective_bounds` and
`lambdas` must hold the same names. Select summarises the stored
`frontier_data["constraint_names"]`, which must equal the job's configured constraints (a 500
otherwise), so a selected ratebook point already carries every configured total when it is
materialised.

### Constraint bounds (`effective_bounds`)

Every solve result, frontier point summary and select response carries
`effective_bounds: {name: {"kind": "min" | "max", "bound": float}}` for every configured
constraint, in configured order: the absolute bound that result was solved at. `kind` comes
from the configured threshold key (`min`/`min_pct` → `min`, `max`/`max_pct` → `max`;
`constraint_kinds` in `_frontier_point_summary.py`). `bound` is read from price-contour and
never derived by haute: a solve result's `constraint_bounds[name]` (`OnlineResult` and
`RatebookResult` alike) and a frontier row's `bound_<name>`. For a pct constraint the frontier
`threshold_<name>` is a fraction, and the library's bound is that fraction times the
constraint's baseline total at the grid's nearest-1.0 step; haute never multiplies a fraction
by a baseline itself. A missing or non-finite bound, or a bound set that differs from the
configured constraints, fails loudly. The solve result, point summary and select response
each validate that `constraints`, `lambdas` and `effective_bounds` (and, on a solve result or
select response, `baseline_constraints`) hold exactly the same constraint names. `effective_constraints` (the published artifact's
constraint specs) still comes from `_frontier_point_constraints_override`. The results pane
judges attainment only against `effective_bounds` (see the
[frontend spec](../frontend-modelling-optimiser-ui/high-level.md)).

### Apply preview (`POST /apply`, `haute.routes.optimiser.apply_lambdas`)

Rejects ratebook jobs outright (`_reject_ratebook_apply_detail` — checked before any
heavy-state lookup, since a ratebook `RatebookResult` has no per-quote dataframe). For online
jobs, `point_index` names the target explicitly: a number resolves that frontier point and
`null` resolves the anchor solve — never the server-side `selected_frontier_point`. The route is
asynchronous and runs its blocking work through `run_until_disconnected`, so a client that
leaves stops waiting (see "Bounded choice queries and point materialisation").

- *Anchor.* The still-live in-memory `solve_result.dataframe` if present, or otherwise the
  persisted apply-result artifact, read with `scan_parquet` inside a `JobStore.lease` on
  `artifact_handles["apply_result"]` (`lease_apply_frame`); totals come from the anchor summary
  (`base_result`, else `result`).
- *Frontier point.* `OptimiserFrontierService.request_point_apply` answers at once from the
  point's retained `frontier_apply_result:<i>` artifact (`from_artifact: true`), or queues its
  materialisation on the job's `LatestWinsQueue` and the request waits for it. Then
  `select_applied_point` records the point as selected (`base_result`, `selected_frontier_point`,
  `result`) under the parent's lock, refusing with 409 if `frontier_generation` moved since the
  request captured it, and the preview reads the point's artifact inside a lease.

`limited_apply_preview_payload(frame)` takes the lazy frame and returns a lazy count and the
first `APPLY_PREVIEW_ROW_LIMIT` rows (`head`), both collected inside the lease, with explicit
`row_count`/`preview_truncated` metadata; the whole frame is never read. After answering, a
frontier-point request slims only `solve_result` (`_clear_result_data_after_user_action`), so
`solver` and `quote_grid` stay available for other points; an anchor request on a job without
frontier points clears all heavy state, and a later anchor preview reads the persisted apply
artifact. Handle insertion re-reads and merges the latest mapping while holding the parent's
lock. Materialisation captures `frontier_generation` before external solver/artifact work and
compares the integer again before publishing, returning 409 only when a recompute actually
advanced it; copying or serialising the unchanged `frontier_data` payload does not invalidate the
request. At most eight `frontier_apply_result:*` handles are retained; the oldest excess handle is
removed from the job and then released through `JobStore.release_detached_artifact_handles`, so
its parquet is deleted at once, or when the last reader holding a lease on it finishes.

### Save and MLflow log (`src/haute/routes/optimiser.py`)

Both `save_result` and `mlflow_log` take an explicit target: `point_index: null` is the anchor
solve and a number is that frontier point; the server-side `selected_frontier_point` is never a
fallback. Neither route reads, touches or clears heavy job state for the anchor, and neither
clears any heavy state afterwards. The anchor resolves to
`_summary_solve_result(_base_result_for_frontier(job))` — the lightweight completion summary,
which carries lambdas, totals, baselines, `converged`, `iterations` (online), `cd_iterations`,
`clamp_rate`, `factor_tables`, `factor_dtypes` and `combined_factor_bounds` (ratebook) — and its
MLflow metrics, params and
artifacts come from the job's `publish_summary` (a `400` telling the user to re-run the solve
when it is missing). A frontier point resolves through
`OptimiserFrontierService.solve_result_for_selected_point`: online points from their stored
summaries, ratebook points from the cached materialised result or, when not cached, by
materialising from the job's `frontier_factor_tables` (no runtime state needed). The routes build a shared JSON payload via `_build_artifact_payload`
(lambdas, objective/constraint totals, baseline totals, convergence/iteration counts,
column-name config, frontier-selection provenance for a point target only, and for ratebook the
factor tables plus ordered dtype descriptors taken from the target's own result, `clamp_rate`,
and the solve's `combined_factor_bounds` — a frontier point shares its solve's grid and so its
collar), plus the audit
trail: `solver_settings` (the result's `input_summary.solver_settings`, built from the solve-time
config snapshot with the solver defaults applied:
`max_iter`, `tolerance` and `chunk_size`, plus `max_cd_iterations` and
`cd_tolerance` for ratebook; `frontier_enabled`, `frontier_steps` and `frontier_ranges` when the
solve requested a frontier), `effective_constraints` (a point's constraint specs with that point's
thresholds via `_frontier_point_constraints_override`; the configured constraints for the
anchor), `input_summary` (`n_quotes`/`n_steps` from the result plus the provenance fields of the
result's own `input_summary`), and `stale_at_publish` (the request's `stale` flag, which the UI sets when
the node configuration changed since the solve). The existing `constraints` key stays the
configured constraints, because `OPTIMISER_APPLY` reads it. The payload is validated with
`_validate_artifact_payload` (rejects a missing lambda
mapping, a missing total objective, missing or malformed ratebook factor-table/dtype metadata, a
ratebook `clamp_rate` that is not a finite number, ratebook `combined_factor_bounds` that are not
exactly `{"min", "max"}` finite numbers with `min <= max` (there is no legacy reader: a ratebook
artifact without them is invalid), or *any* non-finite float anywhere in the payload, naming up
to 5 offending JSON paths), then either
atomically write it to disk
(`atomic_write_text`, with `allow_nan=False` as a defence-in-depth backstop behind the explicit
validation) or attach it as an MLflow run artifact alongside metrics/params and (if present) a
frontier-points CSV (`frontier.csv`, the typed points written back as price-contour's flat rows,
see Typed frontier points). Save resolves `output_path` with `contained_path(_get_project_root(), ...)`
— a relative path against the project root, the base `/pipeline/read-json` uses, and an absolute
path only inside it. `OptimiserSaveRequest.overwrite` (default `false`) guards an existing
destination: under a per-destination lock, an existing file without `overwrite` is a `409` with
the structured detail `{"error_code": "optimiser_result_exists", "message": ...}` and nothing is
written. `OptimiserSaveResponse` carries `path` (absolute), `apply_path` (the written file
relative to the project root with POSIX separators — the value an `OPTIMISER_APPLY` node's
`artifact_path` takes) and `message`. The MLflow run additionally logs every
`solver_settings` entry as a `solver_settings.<name>` param and sets the tags
`stale_at_publish` (`"true"`/`"false"`) and, for a frontier point, one
`effective_constraint.<name>.<threshold key>` tag per constraint threshold. `mlflow_log` logs through an `MlflowClient` bound to the destination
`resolve_tracking_backend(destination)` resolves (registry from `registry_uri_for_tracking`),
selects the experiment with `ensure_experiment`, creates the run with `client.create_run`,
logs parameters, metrics, tags and artifacts through the client, and terminates the run
as `FINISHED` or, in a `finally`, `FAILED`. It logs no model, so it never enters
`mlflow_fluent_operation()`, never waits for another log, and never writes the tracking URI
into the environment. Experiment-name resolution and the run URL use the same shared
`resolve_experiment_name()` / `build_run_url(..., tracking_uri=...)` helpers in
`haute.modelling._mlflow_log` that `routes/modelling.py` uses
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
config gains the optional `result_export_path` string — the Export pane's save path,
relative to the project root exactly as `/save` resolves it, with no default — declared on
the `OptimiserConfig` TypedDict and `OPTIMISER_CONFIG_KEYS` so it round-trips through save,
parse and codegen, and classified as node config for the execution cache like its MLflow
siblings; the solve never reads it, so it is not part of the solve's identity. The OPTIMISER node
config also gains the optional `mlflow_destination` field (absent = the local folder; `databricks`
or `server` when chosen), declared on the
`OptimiserConfig` TypedDict and `OPTIMISER_CONFIG_KEYS`, classified as node config for the
execution cache, and rejected by `validate_node_config` for unknown values; solving never logs
automatically.

### Artifact lifecycle (persist / validate / load / cleanup) (`src/haute/routes/_optimiser_artifacts.py`)

Three artifact families, all rooted under the versioned marker-aware OS-temp hierarchy
(`<tempdir>/haute/artifacts/v1/optimiser_apply`,
`<tempdir>/haute/artifacts/v1/optimiser_ratebook_factors`,
`<tempdir>/haute/artifacts/v1/optimiser_quote_analysis`; the third is described in
"Analysis-column side table and scenario grid"):

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
- **Load** (`_scan_apply_result_artifact`, `_load_ratebook_factors_artifact`,
  `_scan_ratebook_factors_artifact`): lazy (apply result, read only inside a lease and only
  through bounded plans) or eager (ratebook factors) re-reads of the persisted parquet. The apply
  scan reads the parquet footer at once, so a corrupt file is the 500 below, not a later
  collection failure. A valid handle whose file is absent is a 410 lifecycle outcome with
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
  stale markers from the three dedicated roots and logs bounded
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

### Runtime ratebook apply (`_apply_ratebook` in `src/haute/_builders.py`)

`_apply_ratebook` is the one ratebook apply: the executor, generated code (`_node_apply.py`) and
the deploy scorer all reach it through `_dispatch_apply`. It first parses the artifact's
`combined_factor_bounds` with `parse_combined_factor_bounds` (`src/haute/_ratebook_collar.py`),
raising `CombinedFactorBoundsError` (a `ValueError`) when they are missing or malformed. Then,
per factor table, it validates the dtype contract, joins the rates into
`{table}_optimised_factor` and fills an unseen level with the neutral `1.0` (after the miss is
counted and logged). The per-factor columns are never clamped. Their product becomes
`optimised_factor`, which is then clipped to `[min, max]`: the order is neutral fill → product →
collar. A product inside the collar, or exactly on an edge, is unchanged bit for bit; a product
past an edge deploys at that edge, which is the grid-end step the solver evaluated for it. This
is the Q17 decision in [optimiser validation](../roadmap/optimiser-validation.md): the deployed
factor never leaves the range the solve scored. Inside the range the deployed factor is the
unsnapped product; the solver evaluated the nearest grid step, so the two agree exactly only on
grid values.

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

Both detail payloads carry the artifact's `constraints` (the configured specs) and, when the
artifact records them as a mapping, its `effective_constraints` (the thresholds in force for the
published target, e.g. a frontier point's); an artifact without them omits the key.

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
an error), and accumulates a running product. After the ladder it applies the artifact's
`combined_factor_bounds` exactly as `_apply_ratebook` does and reports it as `collar`
(`min`, `max`, `before` — the running product — `after` — the clamped value — and `applied`,
true when the product lay outside the bounds). The clamped value is reconciled against the
actual output column; a mismatch, or missing or malformed bounds, raises
`OptimiserApplyTraceError`. An artifact with no usable
factor-table entries also raises because an empty ladder has no value that can be reconciled.

Every raised exception anywhere in this call graph is caught by
`explain_optimiser_apply_from_config`'s outer `try`/`except`, logged with `exc_info=True` and
converted to `_error_detail(...)`, a `status: "error"` payload carrying the exception type and
message. An unusable `price-contour` install surfaces as the guard's own
`PriceContourCompatibilityError` (see [The price-contour guard](#the-price-contour-guard)),
whose message already names every problem and the remedy.

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
  validates the config, owns an admitted `OPTIMISER_SETUP` context for the whole count and
  releases it on every exit. `OptimiserSolveService.estimate_input` does the count: it creates a
  short-lived job tagged `job_type = _ESTIMATE_JOB_TYPE`, executes the pipeline to the data
  input, runs `estimate_input_metrics` (one streaming aggregation scan with the null-`quote_id`
  check folded in) and removes the job in a `finally`. In process mode the count runs on the
  warm interactive worker pool (`optimiser_estimate_worker`, the pipeline's lineage affinity
  key, the admission's native and RSS caps, `HAUTE_OPTIMISER_ESTIMATE_TIMEOUT` seconds,
  default 300): the job lives in the worker's private `optimiser_worker` store, and the worker
  returns the counts or a `(status, detail)` answer (`OptimiserEstimateOutcome`). The route
  answers a pool memory kill with the typed 507, a timeout with 504, and a crash or unexpected
  worker error as preview does. In `thread` mode the service counts in-process. An admission,
  sampled-memory, bounded-streaming or contract failure has its typed answer
  (`estimate_failure_http_exception`: 507 with optimiser-estimate wording, 422, or the public
  contract answer), never a generic 500. The job tag is what lets
  `_NON_BLOCKING_RUNNING_JOB_TYPES` exempt an in-flight in-process `/estimate` call from
  `_check_no_concurrent_jobs`'s store-wide scan.
- **`/estimate`'s `total_rows` is null only when the source size is unknown.**
  `_detailed_ancestor_source_metadata` answers an unknown size itself, with no row count:
  live data without Parquet backing, or a source whose metadata read raises `OSError`,
  `TypeError` or `ValueError`. The route does not catch anything else it raises. An
  unexpected failure is an error response, never an estimate with the total missing.
- **Scenario-value statistics never silently disappear.** `_compute_scenario_value_stats`
  raises when an online result has no per-quote frame, no `optimal_scenario_value` column or no
  quotes; `_finalize_solve_result` records that (or any computation error) in
  `diagnostics_errors` and leaves the statistics `null`, so a degraded result says why.
- **std of a single-quote scenario-value distribution is hardcoded to `0.0`.**
  `_compute_scenario_value_stats` special-cases `n == 1` rather than calling Polars' sample
  standard deviation (`ddof=1`), which is undefined (`null`) for a single observation and would
  otherwise crash the subsequent numeric cast; `0.0` is treated as the true population value for
  a singleton, not a fabricated fallback.
- **Non-finite value validation happens post-cast, at Float32 precision.** The solver consumes
  Float32; `validate_input_value_contracts` checks for NaN/Inf *after* the Float32 cast
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
  key.** `_has_running_sweep` scans for a running `frontier_recompute` job whose
  `parent_job_id` matches while the parent's lock makes the scan and job creation atomic.
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
  mapped by the shared `memory_limit_http_exception(exc, operation_noun="Auto-range")`,
  whose payload `message` is the
  shared curated wording from `routes/_memory_messages.memory_limit_user_message`
  — the same shape training and the input-snapshot build use — and the
  memory-limited job's terminal message reuses it rather than the generic
  exceeded-its-memory-budget fallback). Any other exception a request handler raises is not
  caught in the route: it reaches the application handler, which logs it as
  `unhandled_exception` and answers the sanitized 500. The frontier apply path first
  removes the apply artifact that request created, then re-raises. This applies to `POST /frontier` only up through its
  synchronous validation phase (runtime resolution, compute budget, already-running-sweep check);
  once validation and worker launch succeed, the request returns 200 with a `status: "started"`
  body.
- **Background-thread paths** (the setup thread, the solver thread, the streaming/non-streaming
  auto-range worker, and — since the frontier sweep offload — the frontier sweep worker) never let
  an exception propagate out of the thread; every failure branch is caught and converted into a
  `JobLifecycle.transition(...)` call recording a terminal status, a human message, and (for
  `HTTPException`s specifically) the original status code and detail string in the job for later
  inspection. `OptimiserFrontierService._run_sweep` follows the same pattern via the service's
  own `JobLifecycle`: an `HTTPException` with status 400/409/422 transitions the frontier job to
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
  entry point catches `Exception` generally (including the guard's
  `PriceContourCompatibilityError`), converting it into the `status: "error"`
  payload described above — no exception from this module is ever allowed to reach the tracing
  subsystem's caller.
- **Artifact-load failures never leak library internals.** Every wrapped artifact-load
  `HTTPException` in `_optimiser_service.py` uses a fixed, generic message
  ("... is missing or corrupted. Re-run the solve to regenerate it.") regardless of the specific
  underlying `OSError`/parquet exception, which is logged server-side with `exc_info=True` but
  never included in the client-facing detail string.

## Testing

- `tests/test_optimiser_contracts.py` verifies optimiser job-store isolation, streaming quote-contiguous projections, factor extraction, low-memory sink behavior, null/interleaved/range validation, and solve/apply totals. Its OPT-V04 classes pin the result contract: strict frontier points (extra, missing, coerced or
  non-finite fields and constraint maps that disagree are rejected), frontier map-key completeness
  against `constraint_names`, strict factor-table rows and point summaries, the solve result's
  required `mode`, `input_summary` and `diagnostics_errors` and agreeing constraint maps, a select
  response with no default totals or baseline, statistics failures and a failed frontier recorded
  in `diagnostics_errors`, the artifact reading the result's input summary, and the status route's
  non-finite walk (a NaN or Infinity corrects the job to `error`; a clean walk is cached per
  generation and selection).
- `tests/test_optimiser_outcomes.py` covers OPT-V09A end to end through the real input
  preparation (thread and process mode): a non-solver `region` column configured as an analysis
  column reaches `quote_analysis.parquet` one row per quote while the solver input and grid are
  unchanged; a column varying within a quote is refused by name; the side-input path (a
  separate connected frame joined by `quote_id`, a missing quote marked missing and counted, a
  frame without `quote_id` refused, execution and preservation of a frame outside the data
  input's lineage in online mode, the demand narrowed to `quote_id` + the analysis columns);
  adoption at completion surviving `/apply`, cancellation and failure leaving no file, frontier
  recompute and user-action slimming keeping it, startup reaping; the cardinality metadata; and
  the scenario grid on every result. `tests/test_job_store.py` covers `JobStore.lease` and
  `release_detached_artifact_handles`. Its OPT-V09B classes cover the choice queries over real
  solves: the histogram reconciling to the solved totals for the as-solved result and a frontier
  point, the join-count failure, the 1:1 join to the side table, each reducer's bound and
  validation, admission refusal, single-flight, and the ratebook refusal.
- `tests/test_optimiser_frontier_materialisation.py` covers the point queue: A/B/C rapid
  stepping (one apply at a time, B replaced with 409, C run after A), shared-subscriber
  disconnect, admission refusal before `apply_from_grid` (the mock is never called), availability
  for a retained point, an unmaterialised point after heavy-state expiry and a point evicted as
  the ninth artifact, and an eviction during a read deferred by the reader's lease.
  `tests/test_shared_flights.py` covers `SharedFlights` and `LatestWinsQueue` directly.
- `tests/test_frontier_point_summary.py` covers `frontier_point_rows` (the library frame to typed
  points, per mode, failing on any schema mismatch or malformed value), the write-back to the
  library row the MLflow CSV uses, and `frontier_point_summary` over typed points.

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
  with the runtime engine, and an incompatible `price-contour` install surfacing the guard's
  diagnosis. Limited
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

- `src/haute/routes/_optimiser_solver.py::_auto_frontier_ranges_from_config` resolves ranges
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
## Analysis-column side table and scenario grid

The behaviour is defined in [the high-level specification](high-level.md#behaviour).

**Configuration.** `OptimiserConfig` declares `analysis_input: str` (one exact connected
incoming-edge name, resolved by `_resolve_optimiser_input_edge` like `data_input` and
`banding_source`; absent or empty means the data input) and `analysis_columns: list[str]`
(optional, at most `MAX_ANALYSIS_COLUMNS = 12`, unique, non-empty, never the configured
`quote_id` and never a reserved `__haute_` name). Both are in `OPTIMISER_CONFIG_KEYS`; the
execution cache classifies `analysis_columns` as node config and `analysis_input` as a source
selection, so changing either makes the node's cached result stale. The shape rules are one
function, `validate_optimiser_analysis_config` (`src/haute/_config_validation.py`), called by
`validate_node_config` on save/parse and by `OptimiserSolveService._validate_config` at solve
start (a `400`); `validate_optimiser_input_selectors` checks `analysis_input` as an optional
exact selector at save and code generation, and submodel instances rewrite it like the other
optimiser selectors. `analysis_input` without `analysis_columns` is inert.

**The plan.** `resolve_analysis_plan(graph, node_id, config)` returns `None` without analysis
columns, else an `AnalysisPlan(columns, path, source_node_id)`. The path is `"data_input"` when
the resolved analysis edge is the data-input edge (including `analysis_input` unset or naming
the data input) and `"side_input"` otherwise.

- *Data-input path (carry-through).* `_optimiser_solve_required_columns_by_node` adds the
  analysis columns to the data input's seed, and `validate_and_project(analysis_columns=...)`
  requires them (a missing one is a `400` naming it) and keeps exactly them, uncast, after the
  solver columns. The worker's solver-input parquet therefore carries them; `build_quote_grid`
  and `_admit_resident_grid` read only solver columns.
- *Side-input path.* The execution target becomes the Optimiser itself in online mode too
  (`_setup_execution_target_node_id`), `_optimiser_side_input_ids` preserves the analysis source
  in both modes, `_optimiser_solve_required_columns_by_node` seeds it with `quote_id` + the
  analysis columns (unioned with a banding seed on the same node; skipped when the source also
  feeds the data input through a parallel edge, which the Optimiser seed keeps full width), and
  `OptimiserParentDemandRule` demands exactly those columns from the analysis parent.
  `resolve_analysis_frame` selects the edge's frame, requires `quote_id` under the data input's
  dtype rules (`_invalid_quote_id_dtype_detail`) and the analysis columns, drops null quote ids
  (they match no solved quote) and projects to `quote_id` (as String) + the analysis columns.
  The extraction reduces that lazy projection directly; nothing is staged.

**Extraction** (`write_quote_analysis`, through `_write_quote_analysis`, which types a
failure as a solver-input write failure is). In process mode the setup worker runs it right
after writing the solver input, under the worker's native cap and into the directory its parent
created, so an oversized reduction ends the job `memory_limited` and never grows the server
process; the thread compatibility mode runs it in `_build_grid` after the grid is built
(extracting first measured a higher peak in one process, because the grid build does not reuse
memory the extraction has freed). Every step is a lazy plan; the only collections are one-row
reductions.

1. *One streamed reduction.* Over the source (the written solver input's `quote_id` +
   analysis columns, or the side-input frame's projection), a single `group_by(quote_id)` — grouped on the key as
   stored — gives each column's `first()` value and a varies flag, and is sunk to a per-quote
   file (`per_quote.parquet`) in the table's own directory, never collected. The flag is
   `n_unique > 1` computed without a per-group set: the group's smallest and largest 64-bit
   value hash (`Expr.hash`, seed 0) differ exactly when two values differ, a null and a NaN
   each hashing to one value as `n_unique` counts them. Measured against `n_unique` at 1M
   quotes × 10 steps it holds about 300 MiB less; two different values of one quote colliding
   (about 2^-64 per pair) is the only way a varying quote could pass.
2. *Constant within a quote.* The flags are summed to one row per column and collected. A
   non-zero count raises `AnalysisColumnNotConstantError` (an `OptimiserSetupError`, `400`)
   naming each column, its varying-quote count and one example quote id (a bounded query of
   the per-quote file, run only on failure).
3. *Table.* From the per-quote file, `quote_id` is cast to String. On the side-input path the
   solved quotes (`unique` over the solver input's `quote_id`) are left-joined to it, so the
   table has exactly the solved quotes: a quote the frame lacks has null analysis values and
   `__haute_analysis_row_present = false`, and frame quotes that were not solved are dropped.
   The data-input path writes the flag as `true`. `bounded_sink` writes
   `quote_analysis.parquet` into the fresh marked directory under the `optimiser_quote_analysis`
   root (`analysis_` prefix) and the per-quote file is removed.
4. *Metadata.* One streamed scan of the written file gives the row count,
   `missing_quote_count` (rows with the flag false) and, per column,
   `column_stats[c] = {dtype, approx_n_unique, max_string_bytes}` — `approx_n_unique` is Polars'
   HyperLogLog estimate, and `max_string_bytes` the largest UTF-8 byte length of a String,
   Categorical or Enum value (`null` for other dtypes). OPT-V11's cardinality gate reads them
   from the handle. Once the grid exists, `require_one_row_per_solved_quote` requires the row
   count to equal its `n_quotes` (a `RuntimeError` otherwise; setup removes the table).

A failure removes what the extraction wrote before it propagates: its own files in a parent's
directory (which the parent then removes), or the whole directory it created.

With no analysis columns nothing is written and the job has no `quote_analysis` handle.

**Ownership.** Setup owns the handle (and, in process mode, the directory it created for the
worker) until completion: a setup failure, stop or worker failure removes it in setup's
`finally`; `_launch_background` passes it to `_solve_online` /
`_solve_ratebook`, whose `_finalize_solve_result` adopts it into
`artifact_handles["quote_analysis"]` inside the completion publisher (and removes it if
completion is lost). `_finalize_solve_result`, and so `_solve_online` / `_solve_ratebook`,
returns whether it published the completion; the solve worker's `finally` removes the table
only when that is false (cancel, supersession, timeout, solver failure). It never re-reads
the job to decide: a job removed just after completion would look like one that never adopted
the table, and deleting it directly would bypass a reader's lease. Once adopted, only job removal deletes it —
TTL eviction 24 hours after creation, `delete_job` or `clear_all` — through the registered
`optimiser_quote_analysis` cleaner; `JobStore.clear_result_data` (heavy-state and user-action
slimming) never touches `artifact_handles`, and frontier recompute retains every handle that
is not a `frontier_apply_result:*` handle. Startup reaping covers the root like the other two.

**Leases.** `JobStore.lease(job_id, key)` ([background-jobs](../background-jobs/low-level.md))
yields the handle while holding a read lease on it; `collect_quote_analysis(store, job_id,
query)` validates the handle, scans the file and collects the caller's lazy query inside the
lease, so a job expiring mid-read deletes the file only after the read. A job or handle that
is gone raises `ArtifactHandleUnavailableError`, which `collect_quote_analysis` answers as the
stable `410` "no longer available; re-run the solve".

**Scenario grid.** Right after the grid build, setup records
`scenario_grid = scenario_grid_from_values(quote_grid.scenario_values)` on the job: one
`{optimal_step: i, scenario_value: v}` per step, `v` being the Float32 grid value widened to a
Python float, exactly what the solver scored. `scenario_grid_from_values` fails loudly on an
empty grid, a non-finite value or values that are not strictly increasing.
`_finalize_solve_result` copies it into the result through `require_scenario_grid(job)`; it is
never recomputed, and step membership is always by `optimal_step`, never by comparing floats.

**Precision.** Analysis columns keep their source dtype. The solver ingests objective,
constraint and scenario values as Float32 (`validate_and_project`'s casts); every total,
reconciliation and breakdown accumulates those Float32 values in Float64.

**Measured scaling.** See "Measured setup memory" below for the method, results and thresholds.
## Measured setup memory

Peak RSS (`ru_maxrss`) of solve setup's processes with and without the quote-analysis
extraction, measured on 26 September 2026 (Polars 1.44.2, price-contour 0.5.0, 22 logical
CPUs so a 22-thread Polars pool, WSL2 with 31 GiB). The input is quote-contiguous, 10 scenario
steps per quote, one Float32 constraint, Categorical quote ids as the setup worker writes them,
and two analysis columns (an 8-level String `region` and an Int32 `tier`); the side-input case
reduces a separate one-row-per-quote frame of the same two columns. Each case ran in a fresh
process: three runs at 1M quotes (range shown), one run at 5M quotes (50M rows).

| Process, step | 1M quotes | 5M quotes |
|---|---|---|
| Server (parent): grid build, with or without analysis columns | 707–708 MiB | 1,783 MiB |
| Setup worker: write the solver input, no analysis columns | 818–939 MiB | 2,555 MiB |
| Setup worker: write the solver input, then the data-input table | 1,024–1,164 MiB | 3,295 MiB |
| Setup worker: write the solver input, then the side-input table | 1,142–1,205 MiB | 3,494 MiB |
| Thread mode (one process): grid, then the data-input table | 1,251–1,282 MiB | 4,726 MiB |
| Thread mode (one process): grid, then the side-input table | 1,015–1,039 MiB | 2,998 MiB |

The extraction runs in the setup worker because the same reduction in the server process added
about 2.9 GiB at 5M quotes (the thread-mode rows). The design choices behind these numbers were
measured at 1M quotes: one `group_by` pass instead of a separate check pass (−160 MiB), the
hash comparison instead of `n_unique` (−300 MiB), and the grid built before, not after, the
extraction when both share a process (−150 MiB).

Thresholds, which a change to this path must re-measure against with the same method:

- The server process's setup peak with analysis columns stays within 5% of its peak without
  them: the extraction never runs in the server in process mode.
- The setup worker's peak with the extraction stays within 1.5× its peak without it (measured,
  median against median, 1.29× and 1.41× at 1M quotes and 1.29× and 1.37× at 5M quotes for the
  data-input and side-input paths), and adds at most 250 bytes per quote at 5M quotes (measured
  155 bytes per quote on the data-input path and 197 on the side-input path).
- The thread compatibility mode, which runs everything in one process, stays within 3× the
  grid-only peak (measured 2.65× at 5M quotes on the data-input path).

## Bounded choice queries and point materialisation

The behaviour is defined in [the high-level specification](high-level.md#behaviour). OPT-V09B
covers online solves only; a ratebook job is refused (see "Ratebook" below) until OPT-V09C
gives it a canonical per-quote frame.

**Targets.** `ChoiceTarget(point_index)` names what a query reads: `None` is the as-solved
result (`artifact_handles["apply_result"]`), and an integer is that point of the job's current
frontier (`artifact_handles["frontier_apply_result:<i>"]`), materialised first when it has no
artifact. It is never the server-side selected point.

**The choice frame.** `ChoiceQueryService.choice_query(job_id, target, reducer,
cancellation_token=...)` reads the target's apply artifact with `scan_parquet` inside a
`JobStore.lease` (`lease_apply_frame`) and projects it to the chosen scenario of each quote:
`quote_id` (String), `optimal_step` (Int32, the index into the job's `scenario_grid`),
`optimal_scenario_value`, `optimal_objective` and `optimal_<c>` for each configured constraint
in config order (Float32, as price-contour wrote them). A frame missing one of those columns is a
`ChoiceJoinError`.

**The side table, 1:1.** When the job holds `artifact_handles["quote_analysis"]`, a second lease
is taken on the side table (its key is the configured quote-id column) and its correspondence to
the chosen rows is asserted before any reducer runs: the handle's recorded `row_count` must equal
the apply frame's row count, and `_require_same_quotes` compares one streamed key fingerprint of
each table (`_key_fingerprint`: the row count and the sums of the high and low 32-bit halves of
each quote id's 64-bit hash, seed 0). Equal fingerprints mean the side table holds exactly the
chosen quotes, one row each, but for a hash-sum collision (about 2^-64), the same bound OPT-V09A
accepts for its varies-within-a-quote check; a mismatch raises `ChoiceJoinError` (a
`RuntimeError`: both tables were written for the same solved quotes, so a disagreement is a defect
and surfaces as the sanitised 500). The fingerprint holds nothing per quote, where the full join
it replaces held about 1 GiB at 5M quotes (see "Measured choice-query memory"). The reducers then
reach the side table through `ChoiceFrames`: `joined()` is the inner 1:1 join on the quote id
(Polars' `validate="1:1"`, `maintain_order="left"`), used only by a reducer that needs every
quote's analysis values (the group-by); `with_analysis(rows)` attaches the analysis values to a
bounded result, reading only those quotes from the side table (`is_in`) and failing with
`ChoiceJoinError` unless every one is found. The joined rows carry the analysis columns and
`__haute_analysis_row_present`. An analysis column whose name is a choice-frame column is refused
with a 400 naming it, since the rows could not keep both.

**Reducers.** A reducer is a frozen dataclass whose lazy plan runs over the choice frame and
returns a small, bounded result; no API returns the whole frame. Each validates its own
arguments with a 400 (`MAX_CHOICE_ROWS = 1000` bounds every row count) and supplies its own
memory estimate. `choice_query` returns `ChoiceQueryResult(rows, total)`, where `total` is the
count the rows were taken from.

- `ScenarioHistogram()` — one row per step of the job's `scenario_grid`, in step order, including
  steps no quote chose (zero quotes and zero sums): `optimal_step`, `scenario_value` (from the
  grid, never from the chosen rows), `quotes`, and `optimal_objective` and each `optimal_<c>` as
  Float64 sums of the Float32 values. `total` is the number of quotes. A chosen step outside the
  grid is a `ChoiceJoinError`.
- `SegmentGroupBy(columns, limit)` — groups by one or more analysis columns (each must be one;
  none configured is a 400): the keys, `quotes`, `mean_scenario_value` (Float64 mean) and the
  Float64 sums, ordered by `quotes` descending and then the keys ascending with nulls last, the
  first `limit` groups. `total` is the number of groups.
- `TopK(by, k, descending)` — the `k` quotes with the largest (or smallest) value of `by`, one of
  `optimal_scenario_value`, `optimal_objective` or `optimal_<c>`, ties broken by `quote_id`;
  every choice column, with the analysis values attached. `total` is the number of quotes.
- `RowIndex(offset, limit)` — the rows at positions `[offset, offset + limit)` of the apply
  frame's quote order; every choice column, with the analysis values attached. `total` is the
  number of quotes.

The histogram reads only the choice frame; the group-by reads `joined()`; top-k and the row
index attach the analysis values to their own rows.

`ApplyOptimiser.with_explainer_columns` emits `selected`, `is_baseline` and `linearised_<c>` per
candidate row of one traced quote; none of them is a per-quote chosen value a reducer returns,
so nothing here derives the same value twice. OPT-V12 repeats that check for its columns.

**Precision.** Sums and means cast the Float32 values to Float64 before aggregating, as
price-contour accumulates its totals. The histogram's sums over every step equal the solve's
`total_objective` and `constraints` (for a frontier point, that point's totals) to Float64
rounding.

**Admission.** Inside the leases, each run first computes its own estimate,
`estimate_choice_query_peak_bytes`, from the apply frame's row count, the decoded widths of a
512-row sample of each table (`decoded_frame_row_width_bytes`) and the reducer's own shape
(whether it scans every chosen row, whether it joins the whole side table, and its result
rows), and then admits an
`ExecutionProfile.EXPLORE_ANALYSIS` context (`operation="optimiser_choice_query"`, the run's
cancellation token) with that `WorkEstimate`. `create_admitted_execution_context` refuses an
estimate above the profile's allowance with `ExecutionAdmissionError`, whose reason names the
estimate, the allowance and the remedy (raise `HAUTE_EXPLORE_MEMORY_LIMIT_MB`, or ask for fewer
groups or rows), before anything but the probes has been read; and it reserves the estimate,
not the profile's whole budget, in flight, so several bounded queries run side by side. The run
answers the refusal, and an `ExecutionMemoryLimitExceededError` during a collection, as HTTP 507
with the memory-limit payload. The join check and the reducer collect under the context
(native cancellation and RSS checks), and admission is released when the run ends.

**Single-flight.** Runs are single-flighted by `(job_id, frontier_generation, target,
reducer)` through `SharedFlights`: identical concurrent queries share one run and its result or
error. The run executes on its own thread. Each caller waits on its own `FlightSubscription`;
a caller whose cancellation token fires (a client that disconnected) detaches only itself, and
when the last subscriber detaches the run's token is cancelled and its key released, so a later
identical query starts afresh. A finished run's key is released at once: results are not
cached.

**Point materialisation.** `OptimiserFrontierService.request_point_apply(job_id, point_index)`
validates the job (completed, online), the point and captures `frontier_generation`:

- A retained `frontier_apply_result:<i>` handle answers at once.
- Otherwise the point is available only while the quote grid is alive
  (`touch_heavy_objects(("quote_grid",))`); when it is not, the answer is the named 410
  `{"error_code": "frontier_point_unavailable", "message": ...}`. A point whose artifact was
  evicted (as the ninth) is therefore re-materialised while the grid lives and a 410 after.
- Materialisation goes through the job's `LatestWinsQueue`, keyed `(frontier_generation,
  point_index)`: at most one runs per job, because `apply_from_grid` cannot be interrupted
  (OPT-PC02). A request for the running or the waiting key subscribes to it. A request for any
  other key while one runs takes the single waiting slot; the waiter it replaces fails for all of
  its subscribers with 409 `{"error_code": "frontier_point_apply_replaced", "message": ...}`.
  When the running one ends, the waiter starts. A detaching subscriber leaves only itself; a
  waiter left with no subscriber is dropped before it starts; a running one finishes and keeps
  its artifact for the next request.
- The run re-checks for a handle published meanwhile, reads the grid under the parent's lock
  with the generation fence (409 when a recompute advanced it; the named 410 when the grid is
  gone), then admits an `EXPLORE_ANALYSIS` context (`operation="optimiser_point_apply"`) with
  its own `WorkEstimate` **before** `apply_from_grid` is called, so a refusal (507) never starts
  the uninterruptible apply. The estimate is `estimate_point_apply_peak_bytes` over the as-solved
  apply artifact's row count and sampled decoded width, read inside a lease: a point's frame has
  exactly its shape. It then
  applies, persists and publishes the handle under the parent's lock (generation fence, heavy
  state present, at most eight point handles). The run never changes the selection; `/apply`
  selects after it has waited.
- Evicted and recompute-invalidated point handles are released through
  `JobStore.release_detached_artifact_handles`, so an eviction during a read deletes the file
  only when the reader's lease ends.

**Availability.** The as-solved target is available for the job's 24-hour life; a job that is
gone is a 404, and a handle whose file is gone the stable 410 of the artifact lifecycle. A
point is available while its artifact exists or while the grid is alive; otherwise the named
410 above. A point handle evicted between `request_point_apply` and the read is the same named
410, telling the user to select the point again.

**Ratebook.** A choice query on a ratebook job is refused before any lease with 422
`{"error_code": "optimiser_choices_online_only", "message": ...}`; `/apply` keeps its own 422.

**Measured scaling.** See "Measured choice-query memory" below.

## Measured choice-query memory

Growth of the server process's peak RSS over its RSS just before one choice query (the
process's own `VmHWM` less its RSS at the start; `ru_maxrss` is carried over `fork` and `exec`, so
it would report the parent's peak), measured on 26 September 2026 with
`scripts/benchmarks/opt-v09b-choice-query-memory.py` (Polars 1.44.2, price-contour 0.5.0, 22
logical CPUs so a 22-thread Polars pool, WSL2 with 31 GiB). The as-solved apply artifact holds
one row per quote as price-contour writes it (a ten-character String quote id, Int32 step, and
Float32 scenario value, objective and one constraint); the side table holds an 8-level String
`region` and an Int32 `tier`, in a different quote order. Each query ran in a fresh process:
three runs at 1M quotes (range shown), one at 5M. The group-by is by `region` and `tier`
(limit 100), the row index is 1,000 rows from the middle, and top-k is 1,000 quotes by
objective.

| Query | 1M quotes | 5M quotes | Estimate at 5M |
|---|---|---|---|
| Histogram, no side table | 82–83 MiB | 272 MiB | 865 MiB |
| Row index, no side table | 25 MiB | 25 MiB | 64 MiB |
| Top-k, no side table | 138–139 MiB | 589 MiB | 865 MiB |
| Histogram, with the side table | 117–133 MiB | 388 MiB | 865 MiB |
| Row index, with the side table | 102–115 MiB | 301 MiB | 712 MiB |
| Top-k, with the side table | 184–194 MiB | 657 MiB | 865 MiB |
| Group-by, with the side table | 346–363 MiB | 1,271 MiB | 1,875 MiB |

Asserting the side table's correspondence by a whole-table join, as first built, held 971 MiB
(histogram), 1,041 MiB (row index) and 1,281 MiB (top-k) at 5M quotes; the key fingerprint and
attaching analysis values to the result rows only brought those to the figures above. The
group-by still joins every quote, and is the largest. A frontier point's apply grew the process
by 42 MiB at 1M quotes (10 steps, one constraint), against an estimate of 144 MiB.

Thresholds, which a change to this path must re-measure against with the same method:

- Every measured growth stays at or below the query's own admission estimate
  (`estimate_choice_query_peak_bytes`), so admission never lets through a query it
  under-counted; at 1M quotes the closest margin is top-k with the side table (194 against
  224 MiB).
- At 5M quotes, per quote: the histogram at most 100 bytes (measured 57 without and 81 with the
  side table), top-k at most 160 bytes (123 and 138), the group-by at most 320 bytes (267), and
  the row index at most 64 MiB in all without the side table (25 MiB) and 100 bytes per quote
  with it (63).
