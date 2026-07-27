# Execution Engine — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/executor.py` | GUI-facing eager entry point: `execute_graph()` (preview, with the `_preview_cache` `LRUCache`), `write_data_output()` (batch/data-output writes), preamble compilation + single-flight cache (`_compile_preamble`), preview-column projection/schema-warning assembly, and output-destination containment. |
| `src/haute/execution.py` | Execution facade and implementation module: re-exports lower-level execution helpers; directly owns strategy-planner entry points, runtime-input fingerprints, `preview_lineage_cache_key`, `PREVIEW_EXECUTION_SEMANTICS_VERSION`, and the process-default dataframe execution-cache singleton. It is the stable application import boundary, but is not currently a thin re-export module. |
| `src/haute/_path_resolution.py` | Cross-component dependency owned by [sandbox-security](../sandbox-security/low-level.md); canonical local-runtime-path resolution: separator normalization, project/pipeline candidate choice, symlink-aware containment, selected-external-pipeline root inference, and the context-local root used by eager/lazy builders. |
| `src/haute/_execute_lazy.py` | The shared execution core: `_build_funcs` (per-node callable construction, shared by eager and lazy), `_execute_lazy` (lazy plan + structural parquet checkpointing + dataframe-cache seeding), `_execute_eager_core`/`EagerResult` (eager materialisation with contract checks), strict/non-strict `ContractResolution`, column-contract assertion helpers, multi-frame (`dict[label, Frame]`) source routing (`_pick_source_frame`). Graph preparation is consumed directly through `src/haute/projection.py::prepare_graph` and its prepared-plan value. |
| `src/haute/_contracts.py` | Cross-component dependency owned by [pipeline-config](../pipeline-config/low-level.md): execution consumes the shared column-contract model and registry lookup. |
| `src/haute/_registry.py` | Cross-component dependency owned by [pipeline-config](../pipeline-config/low-level.md): execution reads the canonical node registry. |
| `src/haute/projection.py` | Shared execution-strategy planner: backward column demand, strict-profile decisions, fan-in edge demands, materialisation/opaque boundaries, source-scan projection, and bounded strategy diagnostics. |
| `src/haute/_execution_context.py` | `ExecutionContext`, `ExecutionProfile`, `ExecutionCancellationToken`, `ExecutionMetricsRecorder`, deterministic request-local fault points, bounded opt-in terminal telemetry, cancellation-latency evidence, cleanup precedence, and RSS-sampling/memory-pressure-event machinery. Contexts created directly may be unbudgeted; admitted contexts carry the resolved limits. |
| `src/haute/_execution_admission.py` | Resolves an `ExecutionBudget` per `ExecutionProfile` (fixed default / explicit env override / adaptive fraction of available RAM), performs pre-flight admission (`create_admitted_execution_context`), and tracks a process-wide in-flight reservation for "heavy" profiles. |
| `src/haute/_node_apply.py` | Config-driven implementations of `liveSwitch` input selection, `scenarioExpander` row expansion, `optimiserApply` artifact dispatch, and output response-document assembly (`assemble_output_from_config`) — the single code path both the canvas executor (via `_builders.py`) and codegen-generated `.py` files call. |
| `src/haute/_builders.py` | Registers every per-`NodeType` runtime builder and column-contract callback in `NODE_REGISTRY`; owns runtime closures shared by eager, lazy, chunked, and deploy execution, including online/ratebook optimiser-apply artifact dispatch consumed by the optimiser component. |
| `src/haute/_node_builder.py` | `NodeBuildHooks` and `wrap_builder`, the interception seam used by deploy scoring while preserving the canonical runtime builders. |
| `src/haute/_topo.py` | `topo_sort_ids` (graphlib-backed topological sort with a custom multi-cycle reporter), `ancestors` (BFS over reversed edges). |
| `src/haute/graph_utils.py` | Canonical outward re-export facade for graph models, execution helpers, topo helpers, and IO helpers used by generated pipeline code and application modules. Low-level engine modules import canonical graph models from `_types.py` and pure helpers from `_graph_utils.py` directly; importing back through this heavyweight facade would re-enter `_execute_lazy.py` and create an execution/RAM-estimation cycle. |
| `src/haute/_graph_utils.py` | Pure-function graph helpers decoupled from the Pydantic models: `build_parents_of`, `upstream_node_ids`, `_sanitize_func_name`, `edge_input_name` (the single edge→input-name derivation: apiInput-frame edge → its frame label verbatim, else sanitised source-node label; consumed by the executor, codegen, projection, and the deploy scorer so all four agree byte-for-byte), `build_instance_mapping`, `resolve_orig_source_names`, and edge-id construction. |
| `src/haute/_worker_isolation.py` | `run_isolated_worker()` — spawn a child process for one function call with an optional address-space resource cap, timeout, and cooperative stop-reason polling; typed error hierarchy for every terminal state. |
| `src/haute/chunking.py` | `ChunkPlanRequest`/`chunk_plan()` (proves a graph suffix is chunk-safe, sizes chunks from projected target width, and rejects an over-budget single target row), `iter_chunked_frames()`/`run_chunked_reduce()`/`collect_chunked()` (the serial runner), the per-`NodeType` `ChunkCapability` registry, and the AST-based row-local user-code whitelist. |
| `src/haute/_ram_estimate.py` | `available_ram_bytes()`/`available_vram_bytes()` (OS-level memory probing), `estimate_safe_training_rows()` (parquet-metadata-based peak-memory estimate and downsample decision), and the `MaterialisationEstimate` contract consumed by strategy planning. It imports graph models directly from `_types.py` so admission and route cold imports do not re-enter the execution facade. |

## Key types and data structures

- **`ExecutionContext`** (`_execution_context.py`, mutable dataclass) — per-run controls:
  `operation`, `profile: ExecutionProfile`, `job_id`, `cancellation_token`,
  `memory_limit_bytes`/`memory_baseline_bytes`/`rss_limit_bytes`, `admission:
  ExecutionAdmission | None`, `projection_plan`, `metrics: ExecutionMetricsRecorder`,
  `memory_sampler`, `memory_pressure_callback`, `admission_release`, optional
  `fault_injector`, and optional bounded `telemetry_sink`. `stage(name,
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
- **`ProjectionPlan`** (`src/haute/projection.py`, frozen dataclass) — immutable
  execution-strategy result: `needed_by_node`, per-parent `edge_demands`,
  `materialisation_boundaries`, `opaque_boundaries`, and bounded diagnostics.
  `strategy_summary_payload()` reports projected/full-width/schema-derived/
  materialisation-boundary choices without shipping the full column sets.
- **`ExecutionStrategyDiagnostic` / `ExecutionStrategyResult`** (`projection.py`) —
  the sole schema-version-1 strategy result. The closed internal vocabulary is
  `projected`, `schema-all-except`, `full-width-admitted-eager`,
  `unprojected-streaming-boundary`, `materialisation-boundary`, `unsupported`, and
  `not-planned`; it maps exactly to API statuses `projected`, `admitted_eager`,
  `boundary`, `rejected`, and `not_planned`. Required fields also include profile,
  `bounded|unbounded|unknown`, reason code, and
  `available|unavailable|truncated` detail state.
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
- **`MaterialisationEstimate`** (`_ram_estimate.py`, frozen dataclass) — explicit
  `available|unavailable` state with `estimated_peak_bytes`. Available requires a
  non-negative integer (zero is a legitimate empty-input estimate); unavailable
  requires `None` and a reason. One estimate memoises metadata/schema lookups,
  accounts conservatively for variable-width columns, and lets unexpected failures
  propagate.
- **`ExecutionFaultPoint` / `ExecutionTelemetryEvent`** (`_execution_context.py`) —
  immutable sequenced request-local fault boundaries and schema-versioned,
  identifier-free terminal telemetry with a bounded scalar attribute allow-list.

## Control flow

**Preview (`executor.execute_graph`).** Calls
`execution.preview_lineage_cache_key()`, which prepares the selected target lineage and
passes a `LineageCacheKeyRequest` to `_cache.lineage_cache_key()`. The versioned payload
includes `PREVIEW_EXECUTION_SEMANTICS_VERSION`, target/source/port selection, requested
and initial columns, row limit, selected live-switch path, runtime-input evidence, and a
contract fingerprint over enforcement plus target-only/full materialisation scope.
File-backed input and JSON-cache changes therefore invalidate the selected lineage
without unrelated graph state changing the key. There is no separate preview-projection
cache suffix.

The resulting key addresses `_preview_cache` (an `LRUCache`, one entry per unique
lineage request). On a full hit, cached `eager_outputs`/`errors`/`timings` are served directly.
On a partial hit (same graph, new target needs more materialised nodes), calls
`_eager_execute()` for only the newly-needed portion and merges into the cached entry
— fresh outputs win over stale cached ones for any overlapping node id, and a node
that re-executed successfully clears any stale cached error. On a full miss, executes
from scratch. `_eager_execute()` compiles the preamble (`_compile_preamble`, tolerant
of failure — the error is attached only to nodes whose builder actually consumes the
preamble namespace) and delegates to `_execute_eager_core()`.

An API input bundle containing exactly one labelled frame has one canonical flat
frame, so that frame remains the node's ordinary preview without requiring
`port_label`. A valid multi-frame target with no `port_label` has no canonical flat
frame to preview. Its `NodeResult` is therefore `status="ok"` with empty flat
`columns` and `preview`, while `frame_columns` carries every labelled frame schema.
Supplying `port_label` selects exactly that frame for the flat preview; unknown
labels fail clearly and never fall back to an arbitrary first frame.

Before fingerprinting or building functions,
`canonical_dataframe_execution_graph()` resolves every local runtime input field
with `enforce_project_root=True`. `_execute_eager_core` and `_execute_lazy` are
wrapped by `runtime_project_root_scoped`; its wrapper resolves the declared
`graph` argument from either positional or keyword calls and fails clearly when
the value is not a `PipelineGraph`. `_resolve_runtime_data_path` therefore
repeats the same check at the final builder seam. Relative, absolute, traversal,
mixed-separator, and symlink spellings therefore share one resolved containment
decision on both strategies. An absolute selected pipeline outside cwd establishes
its parent as the scoped root; it does not authorize sibling directories. A path
spelled inside the configured project cannot acquire that exception by resolving
through a symlink, and HTTP graph payloads are validated against the configured
project before execution so only a direct/operator-controlled caller can select
an external pipeline.

**`_execute_eager_core()`** (`_execute_lazy.py`): prepares the graph
(`projection_planner.prepare_graph`, which topo-sorts via
`_topo.topo_sort_ids` and prunes live-switch edges for the inactive scenario), re-
validates graph-shape contracts for the nodes actually being executed (routes can
submit raw frontend graphs that bypass the parser), computes a backward column-
projection plan when required-column seeds are supplied, and builds per-node callables
via `_build_funcs()`. It then walks `order` once: for each node, resolves its effective
column contract (`_resolve_effective_contract`; only `PREVIEW_EAGER` degrades known
`ConfigError`/`OSError`/MLflow boundary failures to opaque),
checks input columns against the contract before calling the node function, calls it,
applies `selected_columns`/`column_renames`, checks output columns against the
contract, and either materialises the result (`streaming_collect`) or — when
`materialize_node_ids` restricts collection to a target-only preview — keeps it lazy
and reports schema via `collect_schema()` without collecting. Exceptions are captured
per-node when `swallow_errors=True`, except any `HauteError` whose class declares a
stable public `error_code`, plus `ExecutionCancelledError` and
`ExecutionMemoryLimitExceededError`, which always propagate.

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
more than one parent, more than one child, or that feeds a join. When the dataframe
cache did not already materialise the node **and** `checkpoint_dir` is non-`None`, a
`PARQUET` decision writes a projected (`needed_cols`-filtered) parquet file, replaces
the in-memory `LazyFrame` with `pl.scan_parquet(tmp)`, and calls
`_release_consumed_parents()` to drop now-unreferenced parent frames. With no
checkpoint directory, the decision has no materialisation effect. `gc.collect()`/
`_malloc_trim()` run every
`_GC_BATCH_INTERVAL` (3) checkpoints, not every one, since Polars/Arrow buffers are
freed immediately on `del` and full GC only matters for cyclic Python garbage.

Checkpoint paths never interpolate an arbitrary node id. `_checkpoint_filename`
preserves readable `<node_id>.parquet` names for the lower-case safe grammar (at
most 200 characters and not a Windows reserved stem); traversal syntax,
platform-reserved names, and overlong ids use deterministic
`node=<sha256>.parquet`. The `=` delimiter is outside the authored-safe grammar, so
the readable and digest namespaces cannot collide.

`executor.write_data_output()` then writes the terminal lazy frame. A sink-capable `dataOutput`
format uses a bounded Polars sink. Writer-only formats and
database outputs use `write_polars_output()`, whose `streaming_collect()` evaluates
with Polars' streaming engine but returns a fully materialised `DataFrame` before the
eager writer/database API is called; it never performs a second broad `.collect()`.

Every Data Output destination is resolved through the current I/O registry and contained
inside the selected project root. When the root is not passed explicitly, it is inferred
from the graph source using the same canonical project-root resolver as runtime inputs.
There is no uncontained direct-executor output mode or separate sink-path façade.

**Admission (`_execution_admission.create_admitted_execution_context`).**
`execution_budget_for_profile()` resolves an `ExecutionBudget`: checks profile-specific
then global environment overrides first (`budget_policy="explicit_env"`), else — for
profiles in `_ADAPTIVE_LOCAL_PROFILES` under the default `local_adaptive` memory
policy — computes `usable = available_ram_bytes() - min(os_reserve, available/2)` then
`limit = usable * basis_points / 10_000`, clamped to the profile's floor/ceiling
(`budget_policy="adaptive_local"`), else the fixed per-profile default
(`_DEFAULT_MEMORY_LIMIT_BYTES`). Both `fixed` and `strict_server` select the fixed
defaults; an unknown `HAUTE_EXECUTION_MEMORY_POLICY` value raises `RuntimeError`.
Admission then samples current RSS; refuses
(`ExecutionAdmissionError`) if the sampler is unavailable or RSS already exceeds any
configured process-RSS cap. For profiles in `_IN_FLIGHT_PROFILE_SET` (the "heavy"
batch-shaped profiles), `_reserve_in_flight_budget()` adds this run's
`memory_limit_bytes` to a process-global running total under `_IN_FLIGHT_LOCK` and
refuses if the total would exceed `available - os_reserve`; the returned
`admission_release` callable (also wired to `weakref.finalize` on the context) removes
the reservation exactly once.

**Chunked map-reduce (`chunking.chunk_plan` → `iter_chunked_frames`).** `chunk_plan()`
prepares the graph the same way as the other two paths, identifies the chunk-start
node (single `DATA_INPUT` root, or an explicit `chunk_start_node_id`), classifies
every node from `chunk_start_node_id` to the target via `_capability_for_node()`
(consulting `_CHUNK_CAPABILITY_DECLARATIONS`, validating chunk-local user code for
`POLARS`/`SCENARIO_EXPANDER` nodes via `is_chunk_local_polars_code()`), validates the
chunk suffix is a single-parent chain, and sizes chunks either from an explicit
`chunk_size` or from `target_chunk_bytes` (which requires building the real projected
target-output schema through `execute_lazy_graph` and either costing fixed-width
dtypes exactly or sampling up to 128 rows for variable-width columns).
`iter_chunked_frames()` re-validates the plan still matches the currently-prepared
graph order, collects the source in `plan.source_chunk_size`-row batches via
`bounded_collect_batches`, and for each batch runs the SAME `_build_funcs`-built node
functions serially down the chunk suffix, projecting and checkpointing (optionally, to
parquet) each `ChunkBatch` before yielding it. `run_chunked_reduce()` requires the
caller's reducer to declare `bounded=True`; `collect_chunked()` requires an explicit
`allow_unbounded=True` opt-in since it retains every chunk. For a non-root
`chunk_start_node_id`, `ChunkRunnerRequest.start_frame` is mandatory; the runner
batches that supplied frame but neither constructs nor bounds the caller-owned prefix
represented by `pre_chunk_node_ids`.

`DATA_INPUT` chunk-source selection is provider/format capability-driven: the provider
must expose a direct batch source or a leased cached Parquet generation. There is no
filename-suffix switch and execution never starts a cache build or remote fetch.
Post-read input code is accepted only when the shared AST proof establishes row-local
semantics and is applied exactly once after provider resolution.

**Worker isolation (`run_isolated_worker`).** Starts a `spawn`-context child process
running `_isolated_worker_entrypoint`, which applies an `RLIMIT_AS` cap (POSIX only,
and not on macOS — `process_memory_caps_supported()` excludes `darwin` because the
kernel doesn't actually enforce the limit) before calling the target function and
putting `("ok", result)` or `("error", (type, message, traceback))` on a
maxsize-1 queue. The parent polls `process.is_alive()` in `_wait_for_worker()`,
checking `config.stop_reason()` and the timeout deadline each iteration. While the
child remains alive, the parent opportunistically drains and retains the single
result envelope from the maxsize-1 queue; this lets the multiprocessing feeder finish
even when a serialized result exceeds the pipe buffer. The envelope is interpreted
only after the child has stopped, preserving exit/crash classification. A stop or
deadline terminates the process and raises a typed stopped/timeout error. Cleanup
callbacks always run (via `_run_cleanup_callbacks`), even when the primary path
already failed — a cleanup failure is attached to the primary error via `add_note()`
rather than replacing it.

`HAUTE_WORKER_MEMORY_ENFORCEMENT` has only `best_effort|required`. Configuration is
resolved before spawn from plain admitted-context fields; the child constructs a
fresh context rather than receiving the parent object. `required` rejects a missing
positive limit or unsupported hard cap before process creation. `best_effort`
continues with platform-supported caps plus child RSS checkpoints. Cancellation or
timeout terminates, escalates to kill, joins, and verifies death; a surviving child is
`IsolatedWorkerTerminationError`, never reported as successful cancellation.

## Edge cases and invariants

- **Frame sources are uniform from one frame up.** An `apiInput` with ≥1
  emit-eligible table returns `dict[frame_label, Frame]` — there is no bare-frame
  single-table special case, so one frame and eight frames route identically. Every
  `apiInput` edge carries its frame label as `sourceHandle`/`source_port`. Both eager
  and lazy paths route per-edge via `_pick_source_frame(source_output, edge)`, keyed
  on `edge.sourceHandle`; a `None` `sourceHandle` against a dict source raises
  `ValueError` (an invalid edge that names no frame), an unknown
  `sourceHandle` raises `KeyError`, and an empty-dict source (no eligible tables
  configured) raises a `RuntimeError` blaming the source node, not the edge. Column
  caches for these are keyed `(node_id, frame_label)` instead of just `node_id` so
  two consumers of different frames from the same source don't collide on
  contract-check state.
- **Per-edge input names, not per-source names.** `_build_funcs` derives each
  node's `source_names` per incoming edge via `edge_input_name(edge, source_node)`
  (`_graph_utils.py`) — an apiInput edge contributes its frame label, every other
  edge its sanitised source label — in the same edge-declaration order the frames
  are bound in, so parameter i's name always describes frame i. `_build_funcs`
  requires incoming-edge metadata together with the complete graph's edge and node
  maps; `_execute_lazy` (lazy and eager cores),
  `executor.py`'s preview path, `execution.py`'s linear/optimiser execution, and
  `chunking.py`'s chunked runner all pass their node's incoming edges through the
  same derivation. There is no parent-name reconstruction path.
  `resolve_orig_source_names` likewise derives an instance's
  *original* input names from the original node's incoming edges (edge-derived, not
  parent-node-id-derived), so instance alias injection speaks the same names as the
  original's signature. Duplicate derived names among one node's incoming edges
  raise `ConfigError` naming the node and the colliding name (matching codegen's
  save-time rejection); the executor never suffixes or renames. Detection is
  the shared pure helper `duplicate_input_names(names)` (`_graph_utils.py`):
  given one target's derived input names in edge order it returns the names
  appearing more than once — each once, in first-duplicate-occurrence order,
  `[]` when all unique — and never raises itself; the executor wraps a
  non-empty result in `ConfigError`, codegen in `ParseError`, so both
  surfaces report the same collision identically.
- **The standalone `Pipeline.run()`/`score()` executor binds ports the same way.**
  `src/haute/pipeline.py`'s live-object runner consults each `RegisteredEdge`'s
  `source_port` through the shared `_pick_source_frame` selection before calling a
  node function — a generated one-frame apiInput pipeline run standalone receives
  its frame, not the `{label: frame}` dict — keeping the single-execution-engine
  invariant across in-process, standalone, and deploy contexts (see
  [pipeline-config](../pipeline-config/low-level.md) for the module owner).
  `score(df)`'s seed follows the complete shape × port-count matrix owned by
  [pipeline-config](../pipeline-config/high-level.md): bare frame for zero
  (source-only) or one connected port; an exactly-matching
  `{frame_label: DataFrame}` dict for one or more ports; everything else —
  bare frame at 2+ ports, missing/unknown dict keys, any dict at zero ports —
  raises `ExecutionError` naming the ports. Never a silent fan-out of one
  frame to every port.
- **Contract-resolution degradation is scoped narrowly.** In `PREVIEW_EAGER`,
  `_resolve_effective_contract()` converts `ConfigError`/`OSError`/`MlflowException`
  from the builder's contract callback to `Contract.opaque()` — skipping only the
  boundary check, not the node's actual execution. Strict and unprofiled execution
  raises `ContractResolutionError`; programmer errors always propagate unchanged.
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
- **Checkpoint actions are `SKIP` or `PARQUET` only.** No execution path performs
  an in-memory `.collect().lazy()` checkpoint, and no dormant action advertises it.
- **Checkpoint filenames are one safe component.** Ordinary safe node ids preserve
  their readable filename; every unsafe spelling is hashed into the disjoint
  `node=<sha256>.parquet` namespace before joining it to `checkpoint_dir`.
- **RAM estimation returns `None` rather than guessing** when parquet metadata or the
  canonical detailed target schema is unavailable (Databricks sources, JSON-shape
  `apiInput` caches which are one parquet per emit-true table rather than a single
  summarisable file) — callers must treat
  `None` as "estimate unavailable," not "unlimited."
- **Version-1 strategy diagnostics are strictly bounded.** Boundary/reason collections
  retain at most 32 entries, provenance at most 128, and remediation/messages at most
  512 characters with deterministic truncation. Missing/malformed required fields,
  unknown version-1 enums, and higher schema versions are invalid; callers may ignore
  only additive fields within version 1.
- **Group-by profile matrix is closed:**

  | Profile | Version-1 result |
  | --- | --- |
  | `PREVIEW_EAGER`, `DEPLOY_LIVE` | `materialisation-boundary` only when admission and estimate fit |
  | `LAZY_SINK`, `TRAINING_PREP`, `OPTIMISER_SETUP`, `EXPLORE_ANALYSIS`, `AUTO_RANGE`, `DEPLOY_BATCH`, `CHUNKED_MAP_REDUCE` | reject with `profile_requires_bounded_execution` |

  Eligible profiles require a context with admission, positive memory/headroom, and
  `MaterialisationEstimate(state=available)` satisfying
  `estimated_peak_bytes <= min(memory_limit_bytes, headroom_bytes)` (equality is
  admitted). Missing/non-positive admission yields
  `execution_admission_unavailable`; unavailable estimate yields
  `materialisation_estimate_unavailable`; excess yields
  `materialisation_exceeds_headroom`. There is no chunk/streaming fallback.
- **Sampler/fault/cleanup machinery is stable.** Windows RSS bindings initialize once
  per sampler-factory identity under concurrency, reset explicitly, and reinitialize
  after a factory change. Eager diamonds share one producer-side cached `LazyFrame`.
  Timings are milliseconds. Fault points are no-ops without an injector. Cleanup runs
  callbacks in reverse registration order and releases admission once; preserving a
  genuinely propagating primary exception is an explicit opt-in.
- **Terminal telemetry is opt-in and redacted.** `HAUTE_EXECUTION_TELEMETRY` is a
  strict boolean validated/warmed at startup and defaults false. One terminal event
  has at most 32 allow-listed scalar attributes and 128-character string values; it
  excludes identifiers, paths, columns, plans, user data, messages, and exception
  text. Overflow drops/logs the event rather than truncating the allow-list, and
  assembly/sink failures cannot alter execution status.

## Error handling

- `PreambleError` (`haute.errors`, extends `ExecutionError`) — preamble compile/exec
  failure with stable public code `preamble_failed` and optional public
  `source_line`. Interactive preview catches it inside `_eager_execute` and
  attaches it only to `POLARS`/`LIVE_SWITCH` nodes rather than aborting the whole
  preview; every non-preview profile propagates it through the shared HTTP/job
  contract-error adapter.
- `PreviewProjectionError` (`executor.py`, extends `ValueError`) — a requested
  preview-column projection references columns not present on the target frame.
- `CycleError` (`_topo.py`, extends `HauteError`) — raised from `topo_sort_ids` on a
  cyclic graph, listing every participating node.
- `ContractMismatchError` (`haute.errors`, extends `HauteError`) — raised by
  missing input/output columns and checkpoint/eager projection mismatches in
  `_execute_lazy.py`, and by chunking's `_project_frame`; it is re-raised even when
  eager preview uses `swallow_errors=True`.
- `SchemaMismatchError` (`haute.errors`, extends `HauteError`) — raised for a
  simple inferred join whose parent key dtypes differ. It propagates on lazy and
  fail-fast eager calls. In ordinary eager preview (`swallow_errors=True`) it is
  currently caught by the generic per-node failure handler and returned as the
  node's error status.

  > NOTE: [Tracked by EXEC-01](../roadmap/execution-engine.md#exec-01--symmetric-eager-mismatch-propagation).
  > The eager-preview treatment of `SchemaMismatchError` is an existing
  > asymmetry: unlike `ContractMismatchError`, it is not in `_execute_eager_core`'s
  > explicit re-raise clause. Specs and callers must not treat it as a run-level
  > exception until the implementation changes.
- `ContractResolutionError` (`haute.errors`, extends `ExecutionError`) — strict
  profiled and unprofiled execution could not resolve a node contract. It carries
  public code `contract_resolution_failed` and stable `node_id`, `node_type`, and
  `failure_kind` fields; only `PREVIEW_EAGER` may degrade supported boundary failures
  to an opaque contract.
- `ChunkMemoryRiskError` (`haute.errors`, extends
  `BoundedMemoryUnsupportedError`) — a byte-budgeted plan either estimates one
  target row above budget (`single_row_exceeds_budget`) or proves that the minimum
  executable one-source-row chunk expands above budget
  (`minimum_source_row_expansion_exceeds_budget`). Its public payload includes
  `target_node_id`, `reason_code`, `estimated_target_row_bytes`,
  `estimated_minimum_chunk_bytes`, `row_expansion_factor`, and
  `target_chunk_bytes`.
- `GroupByExecutionUnsupportedError` (`haute.errors`, extends
  `BoundedMemoryUnsupportedError`) — a group-by cannot meet the active execution
  profile/admission contract. Its public payload names the node, operator, profile,
  stable reason/remediation, and any available estimate/headroom.
- `LiveSwitchScenarioError` (`haute.errors`, extends `ExecutionError`) — a configured
  live-switch scenario does not map to an available input. It carries a stable public
  code and named scenario/input fields instead of falling through to a generic
  selection error.
- `ExecutionCancelledError`/`ExecutionMemoryLimitExceededError`
  (`_execution_context.py`) — raised from `ExecutionContext.checkpoint()`/`stage()`;
  the latter carries `to_payload()` for route-layer serialisation and distinguishes
  `reason="rss_exceeds_memory_limit"` and `"process_rss_limit_exceeded"` from
  `"memory_sampler_unavailable"`. A sampler that becomes unavailable mid-run therefore
  fails loudly rather than silently disabling the remaining memory budget.
- `ExecutionAdmissionError` (`_execution_admission.py`, extends `MemoryError`) —
  raised before a bounded operation starts (the RSS sampler returned `None`, RSS is
  already over a configured process cap, or the in-flight budget is exhausted);
  carries `to_payload()` with the same shape family as the mid-run variant.
- `RuntimeError` from admission configuration — raised before context creation for
  an unknown `HAUTE_EXECUTION_MEMORY_POLICY`, a malformed/non-positive explicit
  memory or RSS limit, an invalid OS-reserve override, or a non-positive RAM sample.
  Each numeric environment candidate is read once through `_env.optional_int_env`
  before its byte/megabyte multiplier is applied, so a concurrent environment
  mutation cannot race a presence check against a second read.
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
  first), `IsolatedWorkerTerminationError` (the child remained alive after terminate
  and kill attempts), `IsolatedWorkerCleanupError` (one or more cleanup callbacks raised; attached
  via `add_note()` to a primary error rather than replacing it, or raised alone if
  the worker itself succeeded).
- Other generic `ValueError`/`TypeError`/`RuntimeError` are used for internal-invariant
  violations that should never occur given the calling contract (e.g. a node
  returning a non-Polars-frame type, a dataframe-cache key that doesn't match the
  current graph/policy, a missing sink output path) — these are not part of the
  typed-error surface external callers are expected to catch by type.

## Testing

- `tests/performance/test_polars_scale_scenario.py` — bounded Polars join/training projection scale generation and CI-small execution-profile smoke contracts.
- `tests/test_bounded_collect_contracts.py` — bounded execution modules route collection through the streaming helper rather than direct Polars `.collect()`.
- `tests/test_builder_edge_cases.py` — builder edge cases for instance resolution, constants, outputs, live-switch/scenario expansion, banding, dispatch, and empty frames.
- `tests/test_column_renames.py` — column-rename application for configured, empty, missing, and edge-name mappings.
- `tests/test_compute_needed_columns.py` — topology, contract-algebra, and one-computation-per-node performance invariants for backward needed-column analysis.
- `tests/test_data_input_chunking.py` — Data Input provider snapshots and chunk-plan/runner execution, including unsupported chunk plans.
- `tests/test_extract_column_refs.py` — extraction of referenced columns across empty/minimal, selected/excluded, and node-config shapes.
- `tests/test_graph_input_identity.py` — edge-derived pipeline input-name derivation contract across source handles and graph edges.
- `tests/test_polars_backend_strategy_contract.py` — execution-strategy planning, boundedness/diagnostics payloads, projection/chunking, and error contracts.
- `tests/test_scenario_propagation.py` — active scenario propagation through routes, executor, builders, and live-switch pruning.
- `tests/test_streaming_collect_contract.py` — static contract that bounded callers use `streaming_collect` across execution/deploy/training/optimiser modules.

Tests live in `tests/` (flat layout, no package-per-component subdirectories).

- **`test_execute_lazy.py`** — the core suite: `_prune_live_switch_edges`,
  graph preparation, `_execute_lazy`, `_build_funcs`, `_execute_eager_core` (swallow
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
- **`test_executor.py`** — `execute_graph`/`write_data_output`/preamble
  compilation end to end.
- **`test_executor_critical_edges.py`**, **`test_executor_edge_cases.py`**,
  **`test_executor_mut_witnesses.py`** — focused/mutation-witness pins on the pure
  helper functions in `executor.py` (preview row-limit math, dangerous-binding
  detection, cache-satisfies-request logic) that the big integration suites don't
  exercise branch-by-branch.
- **`test_executor_builders.py`**, **`test_port_aware_executor.py`** — per-`NodeType`
  builder dispatch and multi-port/multi-frame routing through the executor; the
  input-identity scenarios live here: `edge_input_name` derivation (apiInput frame
  label verbatim incl. the single-frame dict source, sanitised label for ordinary
  nodes, flattened submodel child edges), per-edge `source_names` order matching
  frame binding order under edge reordering (delete + reconnect in reverse order
  keeps every name bound to its own frame), duplicate-input-name `ConfigError`, and
  the loud `ValueError` for a null-handle edge against a dict source.
- **`test_preview_cache_byte_awareness.py`**, **`test_preview_cache_regressions.py`**,
  **`test_preview_cache_hint.py`**, **`test_preview_json_serialization.py`** —
  preview-cache eviction-by-bytes, pinned regression scenarios, and JSON payload
  shape.
- **`test_trace_matches_preview.py`** — cross-checks that the preview cache and
  tracing's cache reconstruction agree on fingerprint/cache-key shape.
- **`test_execution_context.py`** — `ExecutionContext` stage/checkpoint
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
- **`test_graph_utils.py`** — `_execute_lazy` re-export surface,
  `_sanitize_func_name`, `ancestors`/`topo_sort_ids`
  via the `graph_utils` facade.
- **`test_ram_estimate.py`** — RAM/VRAM probing across platforms (mocked),
  source-metadata resolution (including edge-join key coalescing), and the
  downsample decision.
- **`test_worker_isolation.py`** — picklable-result round-trip, remote-exception
  reporting, live draining of a large result before child join (the pipe-feeder
  deadlock regression), crash-without-killing-parent, cleanup-on-failure, timeout,
  cooperative-stop, termination/kill escalation with a loud
  `IsolatedWorkerTerminationError` if the child remains alive, memory-cap enforcement
  (including the "unsupported on this platform" path), and the isolated-job-supervisor
  wrapper.
- **`test_dataframe_execution_cache.py`** — shared with
  [caching](../caching/low-level.md); covers the cache API this component's
  `_execute_lazy` calls into, not owned here.
- **`test_codegen_execution_equivalence.py`** — cross-checks that codegen-generated
  `.py` pipeline execution and the GUI executor produce identical results for the
  same graph, pinning the `_node_apply.py` shared-implementation guarantee.
- **`test_polars_execution_strategy_slice0.py`** — projection/streaming strategy
  selection (`plan_execution_strategy`/`plan_prepared_execution_strategy`).

**Known coverage note:** `_execution_admission.py` has no dedicated test file; its
behaviour is tested directly from `test_execution_context.py` and through route/service
tests that construct real admitted contexts. The direct suite asserts complete
coverage of `_ADAPTIVE_MEMORY_POLICY`, `_PROFILE_MEMORY_ENV`, and
`_PROFILE_PROCESS_RSS_ENV` for every `ExecutionProfile`, and exercises adaptive,
fixed, strict-server, explicit-override, process-RSS, and in-flight-reservation paths.

## Approved change contract — canonical-only execution interfaces

Under [ROAD-CANON-01](../roadmap/engineering-quality.md#road-canon-01--prerelease-canonical-only-contract),
maintained execution call sites use the current typed planner, admission, runtime-input, and
diagnostic result objects directly. Private compatibility wrappers, tuple projections, and
test-only call shapes are removed together with their wrapper-specific tests.
