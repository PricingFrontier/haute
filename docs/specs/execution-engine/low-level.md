# Execution Engine — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `executor.py` | GUI-facing eager entry point: `execute_graph()` (preview, with the `_preview_cache` `FingerprintCache`), `execute_sink()` (batch/data-output writes), preamble compilation + single-flight cache (`_compile_preamble`), preview-column projection/schema-warning assembly, sink path resolution/containment. |
| `execution.py` | Stable internal facade re-exporting execution helpers (`execute_lazy_graph`, `plan_execution_strategy`, `build_dataframe_execution_cache_request`, graph/path/frame input-fingerprint helpers, `runtime_input_extra_keys`) so application code has one import boundary instead of reaching into `_execute_lazy`/`graph_utils` internals directly. |
| `_execute_lazy.py` | The shared execution core: `_prepare_graph`/`_prepare_graph_with_edges` (topo order + adjacency), `_build_funcs` (per-node callable construction, shared by eager and lazy), `_execute_lazy` (lazy plan + adaptive checkpointing + dataframe-cache seeding), `_execute_eager_core`/`EagerResult` (eager materialisation with contract checks), column-contract assertion helpers, multi-frame (`dict[label, Frame]`) source routing (`_pick_source_frame`). |
| `_execution_context.py` | `ExecutionContext`, `ExecutionProfile`, `ExecutionCancellationToken`, `ExecutionMetricsRecorder`, and the RSS-sampling/memory-pressure-event machinery every long-running operation runs through. |
| `_execution_admission.py` | Resolves an `ExecutionBudget` per `ExecutionProfile` (fixed default / explicit env override / adaptive fraction of available RAM), performs pre-flight admission (`create_admitted_execution_context`), and tracks a process-wide in-flight reservation for "heavy" profiles. |
| `_node_apply.py` | Config-driven implementations of `liveSwitch` input selection, `scenarioExpander` row expansion, and `optimiserApply` artifact dispatch — the single code path both the canvas executor (via `_builders.py`) and codegen-generated `.py` files call. |
| `_topo.py` | `topo_sort_ids` (graphlib-backed topological sort with a custom multi-cycle reporter), `ancestors` (BFS over reversed edges). |
| `graph_utils.py` | Canonical re-export facade for graph models, execution helpers, topo helpers, and IO helpers used by generated pipeline code and application modules. |
| `_graph_utils.py` | Pure-function graph helpers decoupled from the Pydantic models: `build_parents_of`, `upstream_node_ids`, `_sanitize_func_name`, `build_instance_mapping`, `resolve_orig_source_names`, edge-id construction, sink-path normalisation. |
| `_worker_isolation.py` | `run_isolated_worker()` — spawn a child process for one function call with an optional `RLIMIT_AS` cap, timeout, and cooperative stop-reason polling; typed error hierarchy for every terminal state. |
| `chunking.py` | `ChunkPlanRequest`/`chunk_plan()` (proves a graph suffix is chunk-safe, sizes chunks), `iter_chunked_frames()`/`run_chunked_reduce()`/`collect_chunked()` (the runner), the per-`NodeType` `ChunkCapability` registry, and the AST-based row-local user-code whitelist. |
| `_ram_estimate.py` | `available_ram_bytes()`/`available_vram_bytes()` (OS-level memory probing), `estimate_safe_training_rows()` (parquet-metadata-based peak-memory estimate and downsample decision). |

## Key types and data structures

- **`ExecutionContext`** (`_execution_context.py`, mutable dataclass) — per-run controls:
  `operation`, `profile: ExecutionProfile`, `job_id`, `cancellation_token`,
  `memory_limit_bytes`/`memory_baseline_bytes`/`rss_limit_bytes`, `admission:
  ExecutionAdmission | None`, `projection_plan`, `metrics: ExecutionMetricsRecorder`,
  `memory_sampler`, `memory_pressure_callback`, `admission_release`. `stage(name,
  node_id=...)` is a context manager that times the block, samples RSS at entry/exit,
  records an `ExecutionStageMetric`, and raises `ExecutionMemoryLimitExceededError`
  before entering the block if already over budget. `checkpoint(label=...)` is the
  cheap variant used between statements (no stage timing) — both call
  `cancellation_token.throw_if_cancelled()` first. `_effective_rss_limit_bytes()` is
  `rss_limit_bytes` if set, else `memory_baseline_bytes + memory_limit_bytes`, else
  `memory_limit_bytes` alone, else unbounded.
- **`ExecutionProfile`** (`StrEnum`) — `PREVIEW_EAGER`, `LAZY_SINK`, `TRAINING_PREP`,
  `OPTIMISER_SETUP`, `EXPLORE_ANALYSIS`, `AUTO_RANGE`, `DEPLOY_LIVE`, `DEPLOY_BATCH`,
  `CHUNKED_MAP_REDUCE`. Keys every default memory budget, adaptive-policy entry, and
  environment-variable pair in `_execution_admission.py`.
- **`ExecutionAdmission`** (frozen dataclass) — the immutable admission decision
  (`memory_limit_bytes`, `rss_at_admission_bytes`, `rss_limit_bytes`,
  `process_rss_limit_bytes`, `headroom_bytes`, `config_key`, `budget_policy`,
  `available_ram_bytes`, `os_reserve_bytes`), serialisable via `to_dict()`.
- **`ExecutionBudget`** (`_execution_admission.py`, frozen dataclass) — the resolved
  per-profile budget before an admission attempt: `memory_limit_bytes`, `config_key`,
  optional `process_rss_limit_bytes`, `budget_policy`
  (`"explicit_env"`/`"fixed_default"`/`"adaptive_local"`), `available_ram_bytes`,
  `os_reserve_bytes`.
- **`ExecutionStageMetric`**/`ExecutionStageSummary`/`ExecutionTraceSummary`/
  `ExecutionMemoryPressureEvent` (frozen dataclasses) — the bounded, serialisable
  timing/memory record produced by `ExecutionMetricsRecorder`; `ExecutionTraceSummary`
  is the top-level payload (`metrics_summary()`/`metrics_payload()` on
  `ExecutionContext`) with per-stage retention capped at `_DEFAULT_MAX_RETAINED_STAGES`
  (200) and memory-pressure events capped at 32, with `truncated_*_count` properties
  so a caller can tell a summary is partial without re-deriving it.
- **`EagerResult`** (`_execute_lazy.py`, `NamedTuple`) — the full result of
  `_execute_eager_core`: `outputs` (`dict[node_id, DataFrame | dict[label, DataFrame] |
  None]`), `order`, `parents_of`, `node_map`, `id_to_name`, `errors`, `timings`,
  `memory_bytes`, `error_lines`, `available_columns`, `output_columns`,
  `frame_columns` (per-`(node_id, port_label)` schema for multi-frame emitters).
- **`ChunkPlan`** (`chunking.py`, frozen dataclass) — the proven physical plan:
  `source_node_id`, `chunk_start_node_id`, `target_node_id`, `node_ids`,
  `pre_chunk_node_ids`/`chunk_node_ids`, `chunk_size`/`source_chunk_size` (post row-
  expansion), `capabilities: Mapping[node_id, ChunkCapability]`,
  `required_columns_by_node`/`edge_demands` (the embedded projection plan),
  `row_expansion_factor`, `chunk_size_policy` (`"explicit_rows"`/`"byte_budget"`),
  `max_in_flight_chunks`/`serial` (currently always `1`/`True` — the runner rejects
  anything else).
- **`ChunkCapability`** (frozen dataclass) — `kind` (`MAP_ONLY`/`BOUNDED_STATE`),
  `preserves_row_order`, `supports_fan_in`, `expands_rows`, `state_crosses_chunks`,
  `model_reuse_lifetime`, `row_multiplier`. `_CHUNK_CAPABILITY_DECLARATIONS` is a
  `MappingProxyType` covering every `NodeType` exactly once (enforced by
  `validate_chunk_capability_declarations()`, called at import time).
- **`IsolatedWorkerConfig`** (frozen dataclass) — `timeout_seconds`,
  `memory_limit_bytes`, `require_memory_limit`, `cleanup_callbacks`, `stop_reason`
  (polled callback returning a `WorkerTerminalReason | None`),
  `stop_poll_interval_seconds`, `process_name`; validates positivity in
  `__post_init__`.
- **`RamEstimate`** (`_ram_estimate.py`, `NamedTuple`) — `safe_row_limit`,
  `total_rows`, `estimated_bytes`, `available_bytes`, `bytes_per_row`,
  `was_downsampled`, `warning`, `probe_columns`.

## Control flow

**Preview (`executor.execute_graph`).** Computes a graph fingerprint (from
`graph_fingerprint()` plus extra keys: row limit, source, contract-enforcement flag,
preview-projection cache suffix, and `runtime_input_extra_keys()` — file-backed input
state and JSON-cache state so out-of-band data changes invalidate the right entries).
Looks up `_preview_cache` (a `FingerprintCache`, one entry per unique graph+config
combination). On a full hit, serves cached `eager_outputs`/`errors`/`timings` directly.
On a partial hit (same graph, new target needs more materialised nodes), calls
`_eager_execute()` for only the newly-needed portion and merges into the cached entry
— fresh outputs win over stale cached ones for any overlapping node id, and a node
that re-executed successfully clears any stale cached error. On a full miss, executes
from scratch. `_eager_execute()` compiles the preamble (`_compile_preamble`, tolerant
of failure — the error is attached only to nodes whose builder actually consumes the
preamble namespace) and delegates to `_execute_eager_core()`.

**`_execute_eager_core()`** (`_execute_lazy.py`): prepares the graph
(`_prepare_graph_with_edges` → `projection_planner.prepare_graph`, which topo-sorts via
`_topo.topo_sort_ids` and prunes live-switch edges for the inactive scenario), re-
validates graph-shape contracts for the nodes actually being executed (routes can
submit raw frontend graphs that bypass the parser), computes a backward column-
projection plan when required-column seeds are supplied, and builds per-node callables
via `_build_funcs()`. It then walks `order` once: for each node, resolves its effective
column contract (`_effective_contract`, degrading to opaque on `ConfigError`/`OSError`/
MLflow errors so a transient artifact-store hiccup doesn't crash the whole preview),
checks input columns against the contract before calling the node function, calls it,
applies `selected_columns`/`column_renames`, checks output columns against the
contract, and either materialises the result (`streaming_collect`) or — when
`materialize_node_ids` restricts collection to a target-only preview — keeps it lazy
and reports schema via `collect_schema()` without collecting. Exceptions are captured
per-node when `swallow_errors=True`, except `ContractMismatchError`,
`ExecutionCancelledError`, and `ExecutionMemoryLimitExceededError`, which always
propagate.

**Sink/lazy execution (`execution.execute_lazy_graph` → `_execute_lazy._execute_lazy`).**
Same graph preparation as the eager path, plus: optional seeding from a
`DataFrameExecutionCacheRequest` (skips rebuilding any node whose entire downstream
lineage is already cache-covered, via a reverse topo pass computing
`cache_covers_downstream`), a fuller backward projection analysis (checkpoint dir set,
non-live source, explicit required columns, or strict-projection profile all trigger
it), then `_build_funcs()` for the nodes still needing construction. Each node's lazy
frame is built by `_build_lazy_node()` (contract-checked the same way as eager),
optionally materialised into the shared dataframe cache
(`materialize_lazy_frame_with_cache`), and then passed through
`_checkpoint_decision()` — `SKIP` for sources and batch-mode `MODEL_SCORE` (which
already checkpoints internally via its own `scan_parquet`), `PARQUET` for any node with
more than one parent, more than one child, or that feeds a join. A `PARQUET` decision
writes a projected (`needed_cols`-filtered) parquet file, replaces the in-memory
`LazyFrame` with `pl.scan_parquet(tmp)`, and calls `_release_consumed_parents()` to drop
now-unreferenced parent frames. `gc.collect()`/`_malloc_trim()` run every
`_GC_BATCH_INTERVAL` (3) checkpoints, not every one, since Polars/Arrow buffers are
freed immediately on `del` and full GC only matters for cyclic Python garbage.

**Admission (`_execution_admission.create_admitted_execution_context`).**
`execution_budget_for_profile()` resolves an `ExecutionBudget`: checks profile-specific
then global environment overrides first (`budget_policy="explicit_env"`), else — for
profiles in `_ADAPTIVE_LOCAL_PROFILES` under the default `local_adaptive` memory
policy — computes `usable = available_ram_bytes() - min(os_reserve, available/2)` then
`limit = usable * basis_points / 10_000`, clamped to the profile's floor/ceiling
(`budget_policy="adaptive_local"`), else the fixed per-profile default
(`_DEFAULT_MEMORY_LIMIT_BYTES`). Admission then samples current RSS; refuses
(`ExecutionAdmissionError`) if the sampler is unavailable or RSS already exceeds any
configured process-RSS cap. For profiles in `_IN_FLIGHT_PROFILE_SET` (the "heavy"
batch-shaped profiles), `_reserve_in_flight_budget()` adds this run's
`memory_limit_bytes` to a process-global running total under `_IN_FLIGHT_LOCK` and
refuses if the total would exceed `available - os_reserve`; the returned
`admission_release` callable (also wired to `weakref.finalize` on the context) removes
the reservation exactly once.

**Chunked map-reduce (`chunking.chunk_plan` → `iter_chunked_frames`).** `chunk_plan()`
prepares the graph the same way as the other two paths, identifies the chunk-start
node (single `DATA_SOURCE` root, or an explicit `chunk_start_node_id`), classifies
every node from `chunk_start_node_id` to the target via `_capability_for_node()`
(consulting `_CHUNK_CAPABILITY_DECLARATIONS`, validating chunk-local user code for
`POLARS`/`SCENARIO_EXPANDER` nodes via `is_chunk_local_polars_code()`), validates the
chunk suffix is a single-parent chain, and sizes chunks either from an explicit
`chunk_size` or from `target_chunk_bytes` (which requires building the real projected
target-output schema through `execute_lazy_graph` and either costing fixed-width
dtypes exactly or sampling up to 128 rows for variable-width columns). `
iter_chunked_frames()` re-validates the plan still matches the currently-prepared
graph order, collects the source in `plan.source_chunk_size`-row batches via
`bounded_collect_batches`, and for each batch runs the SAME `_build_funcs`-built node
functions serially down the chunk suffix, projecting and checkpointing (optionally, to
parquet) each `ChunkBatch` before yielding it. `run_chunked_reduce()` requires the
caller's reducer to declare `bounded=True`; `collect_chunked()` requires an explicit
`allow_unbounded=True` opt-in since it retains every chunk.

**Worker isolation (`run_isolated_worker`).** Starts a `spawn`-context child process
running `_isolated_worker_entrypoint`, which applies an `RLIMIT_AS` cap (POSIX only,
and not on macOS — `process_memory_caps_supported()` excludes `darwin` because the
kernel doesn't actually enforce the limit) before calling the target function and
putting `("ok", result)` or `("error", (type, message, traceback))` on a
maxsize-1 queue. The parent polls `process.is_alive()` in `_wait_for_worker()`,
checking `config.stop_reason()` and the timeout deadline each iteration; either
condition terminates the process and raises a typed stopped/timeout error. Cleanup
callbacks always run (via `_run_cleanup_callbacks`), even when the primary path
already failed — a cleanup failure is attached to the primary error via `add_note()`
rather than replacing it.

## Edge cases and invariants

- **Multi-frame sources.** An `apiInput` with 2+ emit-true tables returns
  `dict[port_label, Frame]` instead of a bare frame. Both eager and lazy paths route
  per-edge via `_pick_source_frame(source_output, edge)`, keyed on
  `edge.sourceHandle`; a `None` `sourceHandle` against a multi-frame source raises
  `ValueError`, an unknown `sourceHandle` raises `KeyError`, and an empty-dict source
  (no emit-true tables configured) raises a `RuntimeError` blaming the source node,
  not the edge. Column caches for these are keyed `(node_id, port_label)` instead of
  just `node_id` so two consumers of different frames from the same source don't
  collide on contract-check state.
- **Contract-resolution degradation is scoped narrowly.** `_effective_contract()`
  catches `ConfigError`/`OSError`/`MlflowException` from the builder's contract
  callback and falls back to `Contract.opaque()` — skipping only the boundary check,
  not the node's actual execution, which still raises its real error when it runs.
  Any other exception type (a genuine programmer error) propagates through
  `_effective_contract` unchanged.
- **`_should_check_contract` short-circuits fully-opaque contracts** so a pipeline
  dominated by unconfigured/opaque user-Polars nodes pays no per-node column-set
  computation cost — cited in the code as keeping contract enforcement under a <5%
  overhead budget.
- **Passthrough-runtime nodes skip output-contract checks.** A node builder-wired to
  `_passthrough_fn` (an unconfigured `MODEL_SCORE`/`OPTIMISER_APPLY`, "drag onto
  canvas, configure later") has a contract describing its *configured* shape, which
  the stub doesn't yet produce; the output check is skipped for exactly this state so
  the unconfigured UX doesn't look broken, while input checks and any contract that
  becomes concrete once the node IS configured still apply.
- **`_compile_preamble` single-flight cache.** Keyed on `(preamble text, cwd,
  pipeline_dir, execution_fingerprint)`; a `_PreambleCell` per key is created under a
  tiny `_preamble_cells_guard` lock (never held during exec, so a hot cache hit never
  waits behind a slow compile in another thread) and populated under the coarser
  `_preamble_lock` with a double-check, so concurrent first-callers of the same key
  return the *same* namespace dict rather than each compiling their own. `force_refresh=True`
  (the default) recomputes a dependency fingerprint and evicts stale `utility` module
  imports (plus matching `.pyc` files, since same-size/same-mtime edits can hide
  behind bytecode-timestamp caching); `force_refresh=False` (sink/optimiser tight
  loops) uses a fixed `"no-refresh"` fingerprint and skips validation/hashing
  entirely, on the caller's promise that imported helper files are stable for the loop.
- **`resolve_orig_source_names`/`build_instance_mapping` reject ambiguity rather than
  guessing.** A substring-match pairing between an instance node's upstream sources
  and the original node's parameter names that is ambiguous in either direction
  raises `ConfigError` instead of falling back to greedy first-fit — the code
  docstring notes a prior greedy implementation could silently swap two inputs and
  produce a clean-looking but wrong run.
- **`topo_sort_ids`** is insertion-order deterministic (via `graphlib.TopologicalSorter`,
  not the previous heap-based sort), so callers must pass `node_ids` as an
  insertion-ordered sequence, never a `set`, or tie-break order becomes
  hash-randomisation-dependent across process runs. Edges with an unknown endpoint are
  silently dropped. `CycleError` reports every node participating in *any* cycle
  (via `_find_cycle_nodes`'s Kahn-style peel), not just the one `graphlib.CycleError`
  names.
- **Chunk AST whitelist treats frame names as chunk-safe only as a method-chain
  receiver.** A frame reference embedded in a call argument, subscript, or collection
  element reads the FULL frame under full execution but only the current chunk under
  chunked execution — `_row_local_subexprs_are_supported` rejects any sub-expression
  "derived from a frame" that isn't the direct receiver. `cast(...)` to
  `Categorical`/`Enum` is explicitly rejected (physical encoding depends on the
  process-global string cache, so first-appearance order differs across chunk
  boundaries); `fill_null`/`is_in` are admitted only in their literal-value /
  literal-collection forms (strategy fills and column-haystack membership read across
  rows or the full column, which a chunk boundary changes).
- **`_IN_FLIGHT_PROFILE_SET`** deliberately excludes `PREVIEW_EAGER` and
  `DEPLOY_LIVE` — only the batch-shaped "heavy" profiles reserve a process-wide
  in-flight memory share; interactive/low-latency paths are not throttled by
  concurrent heavy jobs.
- **Memory-pressure events are deduplicated per threshold per context instance**
  (`_memory_pressure_seen`, a `set[int]` of `threshold_percent` values guarded by
  `_memory_pressure_lock`) — each of the 50/75/90% thresholds fires at most once per
  `ExecutionContext`, not once per checkpoint that happens to be above it.
- **RAM estimation returns `None` rather than guessing** when parquet metadata is
  unavailable (Databricks sources, JSON-shape `apiInput` caches which are one parquet
  per emit-true table rather than a single summarisable file) — callers must treat
  `None` as "estimate unavailable," not "unlimited."

## Error handling

- `PreambleError` (`executor.py`, extends `HauteError`) — preamble compile/exec
  failure, carries an optional `source_line`; caught inside `_eager_execute` and
  attached only to `POLARS`/`LIVE_SWITCH` nodes rather than aborting the whole
  preview.
- `PreviewProjectionError` (`executor.py`, extends `ValueError`) — a requested
  preview-column projection references columns not present on the target frame.
- `CycleError` (`_topo.py`, extends `HauteError`) — raised from `topo_sort_ids` on a
  cyclic graph, listing every participating node.
- `ContractMismatchError`/`SchemaMismatchError` (`haute.errors`, both extend
  `HauteError`) — raised by the boundary-check helpers in `_execute_lazy.py`
  (missing input/output columns, join-key dtype mismatch, checkpoint/eager projection
  referencing a column absent from the actual output schema) and by chunking's
  `_project_frame`. These are never swallowed by `swallow_errors=True`.
- `ExecutionCancelledError`/`ExecutionMemoryLimitExceededError`
  (`_execution_context.py`) — raised from `ExecutionContext.checkpoint()`/`stage()`;
  the latter carries `to_payload()` for route-layer serialisation and distinguishes
  `reason="rss_exceeds_memory_limit"` from `"process_rss_limit_exceeded"`.
- `ExecutionAdmissionError` (`_execution_admission.py`, extends `MemoryError`) —
  raised before a bounded operation starts (RSS already over cap, or in-flight budget
  exhausted); carries `to_payload()` with the same shape family as the mid-run
  variant.
- `ChunkPlanUnsupportedError` (`haute.errors`, extends `BoundedMemoryUnsupportedError`
  → `ExecutionError` → `HauteError`) — raised at `chunk_plan()` time for any
  unsupported node type, ambiguous chunk-suffix shape, or un-whitelisted user code;
  also raised defensively inside `iter_chunked_frames` if the runtime graph no longer
  matches the plan it was built from (`_assert_plan_matches_prepared_graph`,
  `_assert_runner_shape`).
- `IsolatedWorkerError` hierarchy (`_worker_isolation.py`) —
  `IsolatedWorkerStartError` (process failed to start), `IsolatedWorkerRemoteError`
  (child raised a Python exception; carries `remote_type`/`remote_message`/
  `remote_traceback`), `IsolatedWorkerCrashedError` (child exited without a result;
  reclassified to `terminal_reason="memory_limited"` when the exit code looks like
  `SIGKILL`/`SIGABRT` under a configured cap), `IsolatedWorkerTimeoutError`,
  `IsolatedWorkerStoppedError` (parent-requested stop; raises `ValueError` if
  constructed with `terminal_reason="completed"`, which is not a valid stop reason),
  `IsolatedWorkerMemoryLimitUnsupportedError` (platform can't enforce the requested
  cap — always raised on Windows if the address-space-limit code path is ever
  reached, since callers are expected to gate on `process_memory_caps_supported()`
  first), `IsolatedWorkerCleanupError` (one or more cleanup callbacks raised; attached
  via `add_note()` to a primary error rather than replacing it, or raised alone if
  the worker itself succeeded).
- Generic `ValueError`/`TypeError`/`RuntimeError` are used for internal-invariant
  violations that should never occur given the calling contract (e.g. a node
  returning a non-Polars-frame type, a dataframe-cache key that doesn't match the
  current graph/policy, a missing sink output path) — these are not part of the
  typed-error surface external callers are expected to catch by type.

## Testing

Tests live in `tests/` (flat layout, no package-per-component subdirectories).

- **`test_execute_lazy.py`** (70 tests) — the core suite: `_prune_live_switch_edges`,
  `_prepare_graph`, `_execute_lazy`, `_build_funcs`, `_execute_eager_core` (swallow
  vs. raise, timings, memory accounting), `_apply_selected_columns`, `EagerResult`
  shape.
- **`test_execute_lazy_contracts.py`** / **`test_execute_lazy_contract_coverage.py`**
  — column-contract enforcement at node boundaries on both paths, including the
  contract-resolution-degradation behaviour.
- **`test_execute_lazy_dataframe_cache.py`** — dataframe-execution-cache seeding/
  skip-covered-node logic inside `_execute_lazy`.
- **`test_execute_lazy_paths.py`**, **`test_checkpoint_projection.py`**,
  **`test_projection_planner.py`** — backward column-projection analysis and its
  effect on checkpoint/eager collection width.
- **`test_executor.py`** (156 tests) — `execute_graph`/`execute_sink`/preamble
  compilation end to end.
- **`test_executor_critical_edges.py`**, **`test_executor_edge_cases.py`**,
  **`test_executor_mut_witnesses.py`** — focused/mutation-witness pins on the pure
  helper functions in `executor.py` (preview row-limit math, dangerous-binding
  detection, cache-satisfies-request logic) that the big integration suites don't
  exercise branch-by-branch.
- **`test_executor_builders.py`**, **`test_port_aware_executor.py`** — per-`NodeType`
  builder dispatch and multi-port/multi-frame routing through the executor.
- **`test_preview_cache_byte_awareness.py`**, **`test_preview_cache_regressions.py`**,
  **`test_preview_cache_hint.py`**, **`test_preview_json_serialization.py`** —
  preview-cache eviction-by-bytes, pinned regression scenarios, and JSON payload
  shape.
- **`test_trace_matches_preview.py`** — cross-checks that the preview cache and
  tracing's cache reconstruction agree on fingerprint/cache-key shape.
- **`test_execution_context.py`** (75 tests) — `ExecutionContext` stage/checkpoint
  behaviour, `ExecutionMetricsRecorder`, memory-pressure thresholding, and (via
  imports) `_execution_admission` budget resolution.
- **`test_container.py`**, **`test_deploy_internals.py`**, **`test_explore_routes.py`**,
  **`test_optimiser_routes.py`**, **`test_pipeline_route_supersession.py`**,
  **`test_schema_snapshots.py`**, **`test_train_service_coverage.py`**,
  **`test_training_memory_safety.py`** — exercise `_execution_admission` indirectly
  through the route/service layers that construct admitted contexts for real
  operations (training, optimiser setup, deploy).
- **`test_chunk_plan.py`** — per-`NodeType` chunk-capability contract tests,
  including `validate_chunk_capability_declarations()`'s completeness check.
- **`test_chunk_runner.py`** — `iter_chunked_frames`/`run_chunked_reduce` execution,
  cancellation and checkpoint cleanup on failure.
- **`test_chunk_whitelist_proofs.py`** — the AST whitelist's correctness contract: de-
  whitelist regression pins for known silent-wrongness constructs, plus a
  `hypothesis`-driven property test per whitelisted construct
  (`test_whitelisted_construct_chunked_equals_full`) that runs the construct through
  the real chunk runner against full lazy execution on randomised, boundary-heavy
  frames (nulls/NaN/inf anywhere, single-row chunks).
- **`test_streaming_chunk_size_threading.py`** — thread-local streaming chunk-size
  propagation used by the chunk runner and bounded-collect helpers.
- **`test_topo.py`**, **`test_topo_contracts.py`** — topological sort ordering, cycle
  detection/reporting, ancestor traversal.
- **`test_graph_utils.py`** (49 tests) — `_prepare_graph`, `_execute_lazy` re-export
  surface, `_sanitize_func_name`, `_resolve_sink_path`, `ancestors`/`topo_sort_ids`
  via the `graph_utils` facade.
- **`test_ram_estimate.py`** (129 tests) — RAM/VRAM probing across platforms (mocked),
  source-metadata resolution (including edge-join key coalescing), and the
  downsample decision.
- **`test_worker_isolation.py`** — picklable-result round-trip, remote-exception
  reporting, crash-without-killing-parent, cleanup-on-failure, timeout,
  cooperative-stop, memory-cap enforcement (including the "unsupported on this
  platform" path), and the isolated-job-supervisor wrapper.
- **`test_dataframe_execution_cache.py`** — shared with
  [caching](../caching/low-level.md); covers the cache API this component's
  `_execute_lazy` calls into, not owned here.
- **`test_codegen_execution_equivalence.py`** — cross-checks that codegen-generated
  `.py` pipeline execution and the GUI executor produce identical results for the
  same graph, pinning the `_node_apply.py` shared-implementation guarantee.
- **`test_polars_execution_strategy_slice0.py`** — projection/streaming strategy
  selection (`plan_execution_strategy`/`plan_prepared_execution_strategy`).

**Known coverage note:** `_execution_admission.py` has no file of its own; its
behaviour is pinned indirectly through `test_execution_context.py` (direct imports)
and the route/service test files that construct real admitted contexts. There is no
single test asserting every `ExecutionProfile` has both a `_DEFAULT_MEMORY_LIMIT_BYTES`
entry and (where adaptive) an `_ADAPTIVE_MEMORY_POLICY` entry beyond what a
`KeyError` at first use would catch.
