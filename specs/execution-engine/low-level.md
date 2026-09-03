# Execution Engine — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/executor.py` | GUI-facing eager entry point: `execute_graph()` (preview, with the `_preview_cache` `LRUCache`), `write_data_output()` (batch/data-output writes), preamble compilation + single-flight cache (`_compile_preamble`), preview-column projection/schema-warning assembly, and output-destination containment. |
| `src/haute/execution.py` | Execution facade and implementation module: re-exports lower-level execution helpers; directly owns strategy-planner entry points, runtime-input fingerprints, `preview_lineage_cache_key`, `PREVIEW_EXECUTION_SEMANTICS_VERSION`, and the process-default dataframe execution-cache singleton. It is the stable application import boundary, but is not currently a thin re-export module. |
| `src/haute/_path_resolution.py` | Cross-component dependency owned by [sandbox-security](../sandbox-security/low-level.md); canonical local-runtime-path resolution: separator normalization, project/pipeline candidate choice, symlink-aware containment, selected-external-pipeline root inference, and the context-local root used by eager/lazy builders. |
| `src/haute/_execute_lazy.py` | The shared execution core: `PreparedExecutionRequest`/`PreparedExecution` (one canonical eager/lazy graph, identity, routing and contract-policy preparation result), `NodeBoundaryRunner` (shared per-node contract resolution, input-frame routing, invocation and boundary assertions), `_build_funcs` (per-node callable construction), `_execute_lazy` (lazy plan + structural parquet checkpointing + dataframe-cache seeding), and `_execute_eager_core`/`EagerResult` (eager materialisation and preview error adaptation). |
| `src/haute/_contracts.py` | Cross-component dependency owned by [pipeline-config](../pipeline-config/low-level.md): execution consumes the shared column-contract model and registry lookup. |
| `src/haute/_registry.py` | Cross-component dependency owned by [pipeline-config](../pipeline-config/low-level.md): execution reads the canonical node registry. |
| `src/haute/projection.py` | Shared execution-strategy planner: backward column demand, profile-independent projection decisions, fan-in edge demands, materialisation/opaque boundaries, source-scan projection, and bounded strategy diagnostics. |
| `src/haute/_execution_schemas.py` | Canonical Pydantic API DTOs for execution-strategy diagnostic boundaries, reasons, provenance, bounded collections, calibration, and the versioned diagnostic payload. `src/haute/schemas.py` re-exports the public models so existing imports remain stable. |
| `src/haute/_column_lineage.py` | Fail-closed AST interpreter for linear Polars frame programs: exact forward schema transfer, per-input backward column demand, and a closed row-effect class (row-preserving, row-non-increasing, bounded-expansion, or unavailable) for the supported operation vocabulary, plus the audited per-namespace registry of `str`/`dt` expression methods whose bare string arguments Polars parses as literals. |
| `src/haute/_polars_operations.py` | The closed, receiver-aware registry of recognised Polars operations (`PolarsOperation` entries keyed by receiver, namespace, and name) with their class, evidence-backed policy, expansion, chunk-proof status, lineage support, and materialisation memory factor in basis points, plus the lookup helpers the chunk classifier, the lineage/cardinality analyser, and the planner derive their vocabularies from. Import-time validation rejects duplicate keys and class/policy/expansion combinations that contradict each other. |
| `src/haute/_execution_context.py` | `ExecutionContext`, `ExecutionProfile`, `ExecutionCancellationToken`, `ExecutionMetricsRecorder`, deterministic request-local fault points, bounded opt-in terminal telemetry, cancellation-latency evidence, cleanup precedence, and RSS-sampling/memory-pressure-event machinery. Contexts created directly may be unbudgeted; admitted contexts carry the resolved limits. |
| `src/haute/_execution_admission.py` | Resolves an `ExecutionBudget` per `ExecutionProfile` (fixed default / explicit env override / adaptive fraction of available RAM), performs pre-flight admission (`create_admitted_execution_context`), and tracks a process-wide in-flight reservation for "heavy" profiles. |
| `src/haute/_polars_utils.py` | Shared with [io-layer](../io-layer/low-level.md): Polars materialisation seams. `execution_collect` selects `auto` or streaming execution and automatically polls a native background query whenever an execution context is active; without one it remains synchronous. `streaming_collect` and `cancellable_streaming_collect` are streaming-engine wrappers over that same contract. All three preserve fault, collect-count, and typed-error telemetry. |
| `src/haute/_node_apply.py` | Config-driven implementations of `liveSwitch` input selection, `scenarioExpander` row expansion, `optimiserApply` artifact dispatch, and output response-document assembly (`assemble_output_from_config`) — the single code path both the canvas executor (via `_builders.py`) and codegen-generated `.py` files call. |
| `src/haute/_builders.py` | Registers every per-`NodeType` runtime builder and column-contract callback in `NODE_REGISTRY`; owns runtime closures shared by eager, lazy, chunked, and deploy execution, including online/ratebook optimiser-apply artifact dispatch consumed by the optimiser component. |
| `src/haute/_node_builder.py` | `NodeBuildHooks` and `wrap_builder`, the interception seam used by deploy scoring while preserving the canonical runtime builders. |
| `src/haute/_topo.py` | Strict `topo_sort_ids` (graphlib-backed topological sort with a custom multi-cycle reporter), explicit `topo_sort_ids_filtered` (opt-in subset traversal returning both the order and every dropped edge/endpoint), and `ancestors` (BFS over reversed edges). The default sorter never silently ignores an unknown endpoint. |
| `src/haute/graph_utils.py` | Canonical outward re-export facade for graph models, execution helpers, topo helpers, and IO helpers used by generated pipeline code and application modules. Low-level engine modules import canonical graph models from `_types.py` and pure helpers from `_graph_utils.py` directly; importing back through this heavyweight facade would re-enter `_execute_lazy.py` and create an execution/RAM-estimation cycle. |
| `src/haute/_graph_utils.py` | Pure-function graph helpers decoupled from the Pydantic models: `build_parents_of`, `upstream_node_ids`, `_sanitize_func_name`, `edge_input_name` (the single edge→input-name derivation: apiInput-frame edge → its frame label verbatim, submodel-output edge → its sanitised public output label resolved through the definition registry, else sanitised source-node label; consumed by the executor, codegen, projection, and the deploy scorer so all four agree byte-for-byte), `build_instance_mapping`, `resolve_orig_source_names`, and edge-id construction. |
| `src/haute/_worker_isolation.py` | `run_isolated_worker()` — spawn a child process for one function call with an optional address-space resource cap, timeout, and cooperative stop-reason polling; typed error hierarchy for every terminal state; the shared supervisor helpers `isolated_worker_failure_is_memory()` (RSS breach, unsupported cap, memory-looking crash exit code, or a memory-typed remote exception) and `isolated_worker_memory_detail()` (the closed memory-limit payload whose reason is one of worker_rss_limit_exceeded, native_memory_cap_unavailable, worker_may_have_exceeded_memory_limit, or worker_memory_limit) that the Data Output writer, training preparation, and the deployed batch path all map their 507 outcomes through. |
| `src/haute/_native_memory_limit.py` | Required/best-effort native memory enforcement for isolated workers: aggregate Linux cgroup and Windows Job Object leases, single-process RLIMIT compatibility, fork-safe ownership, and the context-local active-backend proof used to prevent unaccounted descendant parallelism. |
| `src/haute/routes/_isolated_worker_async.py` | Async route bridge for cancellable isolated-worker transactions: runs the blocking supervisor off-loop, propagates route cancellation/timeout without thread-compute fallback, drains the supervisor to termination, preserves the primary failure when cleanup also fails, and provides the shared linearizable cancellation/publication gate. |
| `src/haute/chunking.py` | `ChunkPlanRequest`/`chunk_plan()` (proves a graph suffix is chunk-safe, sizes chunks from projected target width, and rejects an over-budget single target row), `iter_chunked_frames()`/`run_chunked_reduce()`/`collect_chunked()` (the serial runner), the per-`NodeType` `ChunkCapability` registry, and the receiver-aware AST row-local user-code classifier (`classify_chunk_local_polars_code()` returning a `ChunkLocalDecision`; `is_chunk_local_polars_code()` is its boolean view) with its frame-method, expression-method, namespace-method, and Polars-function allowlists. |
| `src/haute/_host_memory.py` | Host memory observation: `available_ram_bytes()` (per-platform probes behind one shared result contract, including Linux cgroup v2/v1 headroom clamping resolved at the process's own cgroup with ancestor-min semantics — each probe reports a real measurement or a recorded failure reason, never fabricated capacity) and `available_vram_bytes()` (the first GPU's total VRAM via nvidia-smi — the CatBoost single-device sizing basis — or nothing when no GPU is present; detection failures other than an absent binary are logged with a reason). Owns the nvidia-smi subprocess chokepoint. |
| `src/haute/_ram_estimate.py` | Workload-side estimation: `estimate_safe_training_rows()` (parquet-metadata-based peak-memory estimate and downsample decision), `estimate_gpu_vram_bytes()`, and the `MaterialisationEstimate` contract consumed by strategy planning. It imports graph models directly from `_types.py` so admission and route cold imports do not re-enter the execution facade. |
| `src/haute/_cardinality.py` | Pure, overflow-safe join row-bound formulas for every supported join strategy. It validates finite non-negative input bounds and the closed Polars uniqueness contract (`m:m`, `1:1`, `1:m`, `m:1`) and returns both the upper bound and auditable evidence. |
| `src/haute/_estimate_calibration.py` | Process-local, upward-only per-`ExecutionProfile` calibration of materialisation estimates: conservatively rounds calibrated bytes, ratchets observed underestimates with a capped safety margin, exposes immutable diagnostic state, and clears inherited state after fork. |
| `src/haute/_interactive_workers.py` | Warm, killable spawn-worker pool for interactive preview and trace execution: validates process/thread mode, runs affinity-bound serialisable jobs, supervises readiness, timeout, cancellation and RSS limits, and replaces failed workers without leaking stale results. |
| `src/haute/_process_memory.py` | Cross-platform process liveness and resident-memory observation: Linux `/proc`, macOS `libproc`, and Windows process-handle probes return an RSS measurement or an explicit unobservable result for supervised-worker enforcement. |

## Key types and data structures

- **`ExecutionContext`** (`_execution_context.py`, mutable dataclass) — per-run controls:
  `operation`, `profile: ExecutionProfile`, `job_id`, `cancellation_token`,
  `memory_limit_bytes`/`memory_baseline_bytes`/`rss_limit_bytes`, `admission:
  ExecutionAdmission | None`, `projection_plan`, `metrics: ExecutionMetricsRecorder`,
  `memory_sampler`, `memory_pressure_callback`, `admission_release`, optional
  `fault_injector`, and optional bounded `telemetry_sink`. `stage(name,
  node_id=...)` is a context manager that times the block, samples RSS at entry/exit,
  records an `ExecutionStageMetric`, and raises `ExecutionMemoryLimitExceededError`
  before entering the block if already over budget. Stage exit always restores the
  context-local stage stack. If the body is already propagating an exception, an
  exit-sampling or metric-recording failure is attached to that primary exception
  instead of replacing it; without a primary exception the exit failure remains loud.
  `checkpoint(label=...)` is the cheap variant used between statements (no stage timing) — both call
  `cancellation_token.throw_if_cancelled()` first. `_effective_rss_limit_bytes()` is
  `rss_limit_bytes` if set, else `memory_baseline_bytes + memory_limit_bytes`, else
  `memory_limit_bytes` alone, else unbounded.
- **`ExecutionProfile`** (`StrEnum`) — `PREVIEW_EAGER`, `LAZY_SINK`, `TRAINING_PREP`,
  `OPTIMISER_SETUP`, `EXPLORE_ANALYSIS`, `AUTO_RANGE`, `DEPLOY_LIVE`, `DEPLOY_BATCH`,
  `CHUNKED_MAP_REDUCE`. Keys every default memory budget, adaptive-policy entry, and
  environment-variable pair in `_execution_admission.py`.
- **`ProjectionEdgeKey` / `ProjectionPlan`** (`src/haute/projection.py`, frozen
  dataclasses) — immutable execution-strategy identity and result. A key contains
  the persisted edge id plus source, target, visible handles, and retained boundary
  ports. `edge_demands` and edge diagnostics use that key, never a lossy
  `(source_node_id, target_node_id)` pair; the mappings accept complete keys only.
  `ProjectionPlan.demand_for_edge()` is the runtime lookup.
  The plan also carries `needed_by_node`, materialisation/opaque boundaries, and
  bounded diagnostics.
- **`ColumnLineageAnalysis`** (`src/haute/_column_lineage.py`, frozen dataclass) —
  the reusable result of parsing a Polars node's linear frame program. It reports an
  exact output schema when proven, per-input backward demands when proven, a closed
  rule/reason code, and the unsupported operation when the proof stops. The planner
  uses the same result for one-input expressions, group-bys, and fan-in joins.
  `strategy_summary_payload()` reports projected/full-width/schema-derived/
  materialisation-boundary choices without shipping the full column sets.
- **`ExecutionStrategyDiagnostic` / `ExecutionStrategyResult`** (`projection.py`,
  frozen dataclasses) — the internal planning result. The corresponding public
  API DTOs are the `ExecutionStrategy*Payload` models in
  `_execution_schemas.py`; those Pydantic models are the generated structural
  authority for the schema-version-1 network payload. Non-negative ranks,
  counts, byte values, and headroom are capped at JavaScript's maximum safe
  integer; boundary/reason collections cap at 32 items and provenance at 128.
  The closed internal vocabulary is
  `projected`, `schema-all-except`, `full-width-admitted-eager`,
  `unprojected-streaming-boundary`, `materialisation-boundary`,
  `full-width-conservative`, `unsupported`, and
  `not-planned`; it maps exactly to API statuses `projected`, `admitted_eager`,
  `boundary`, `warned`, `rejected`, and `not_planned`
  (`full-width-conservative` is the only `warned` strategy). The addition of
  `full-width-conservative`/`warned` was made within schema version 1: the Pydantic
  DTO, the generated frontend contracts, and the frontend guards changed together,
  and every other unknown value stays invalid. Required fields also include profile,
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
- **`PreparedExecutionRequest` / `PreparedExecution`** (`_execute_lazy.py`, frozen
  dataclasses) — the single eager/lazy preparation boundary. The request carries
  the authored graph, selected target/source, required-column seeds, and active
  execution profile. The result carries the canonical path-resolved graph, one
  `PreparedGraph`, normalized required columns, strict/degraded contract policy,
  child/fan-out indexes, complete-parent identity, and both relevant-edge and
  all-edge incoming-route indexes. The result is consumed directly by both engines;
  neither engine rebuilds these indexes.
- **`NodeBoundaryRunner` / `NodeBoundary`** (`_execute_lazy.py`) — request-local
  shared node orchestration. It resolves the effective contract once when a node
  boundary opens, records the common execution checkpoint/column-demand metrics,
  routes source frames from the prepared incoming edges, invokes the registered
  callable, and applies identical input/output contract assertions. Eager-only
  materialisation, lazy-only checkpoint/cache policy, and preview-only error
  swallowing remain outside the runner and therefore remain explicit.
- **`FilteredTopology`** (`_topo.py`, frozen dataclass) — result of the explicitly
  named filtered traversal: ordered known node IDs plus every dropped edge and
  the deterministic set of unknown endpoint IDs. `topo_sort_ids` instead raises
  `UnknownEdgeEndpointError` before sorting when an edge names an unknown node.
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
  requires `None` and a reason. One strategy request shares a single graph index
  across all of its materialisation boundaries, memoises metadata/schema lookups,
  accounts conservatively for variable-width columns, and lets unexpected failures
  propagate.
- **`RowCardinalityAnalysis`** (`_column_lineage.py`, frozen dataclass) — the
  fail-closed row analogue of column lineage for a linear Polars program. It reports
  the output and peak finite upper bounds plus operation evidence, or the exact
  unsupported operation/reason. Every accepted operation has a closed row-effect
  class. `filter`, `fill_null`, `drop`, `drop_nulls`, `rename`, `with_columns`,
  `with_row_index`, `sort`, `cast`, `shift`, row-only slicing, and `select` are
  row-preserving or row-non-increasing (`pl.all()` and `pl.exclude()` are
  row-preserving selectors, so a selection built from them is proven) (a scalar `select`/`with_columns` still materialises one row
  over an empty frame); `unique` and `group_by(...).agg(...)` are row-non-increasing;
  `unpivot` with a literal non-empty `on` list is bounded expansion by exactly that
  column count and records `unpivot_factor=<count>` in its evidence; join operations
  carry their literal `validate` contract into the shared formulas. `explode`,
  `unpivot` without a literal `on` list (`dynamic_unpivot`), and the audited
  row-expanding expression methods are unavailable because no length evidence
  exists. Cardinality analysis shares the lineage parser but never an input schema,
  so it cannot resolve an omitted `on` list from upstream columns.
- **Available RAM** (`_host_memory.available_ram_bytes`) — tries the platform
  sources in a fixed order (Linux `/proc/meminfo` `MemAvailable`, POSIX
  `sysconf` pages, macOS Mach VM counters, Windows `GlobalMemoryStatusEx`);
  every source reports through one shared probe contract (a measurement, an
  attempted-but-failed reason, or not-applicable-on-this-platform). The first
  observation wins and is clamped once to any finite Linux cgroup headroom:
  cgroup v2 `memory.max - memory.current`, else the v1
  `memory.limit_in_bytes - memory.usage_in_bytes` pair. The controller files
  are read at the process's **own** cgroup, resolved from `/proc/self/cgroup`
  and `/proc/self/mountinfo` (v2 unified and v1 hybrid), not just the mount
  root — a systemd service slice or a container sharing the host cgroup
  namespace keeps its binding limits below `/sys/fs/cgroup`. Headroom is the
  **minimum** across the process's cgroup and its ancestors up to the mount
  point (a parent's limit binds its children; a level whose controller files
  are absent contributes nothing). With several mounts of one hierarchy the
  mount whose root most specifically contains the cgroup path wins. mountinfo
  octal decoding is restricted to the kernel's escape set
  (space/tab/newline/backslash) and decoded path fields are rejected on
  traversal or NUL content, so a hostile mount record cannot redirect
  controller reads. Known limitation, accepted: the proc files are re-parsed
  per probe call (admission is per-execution, not per-row, and caching would
  mis-locate a process migrated between cgroups). `max` and the v1 unlimited
  sentinel do not clamp; negative headroom clamps to zero. Resolution and
  read failures degrade per the Degraded-observation contract below.
- **Degraded-observation contract** — one policy governs every degraded memory
  state, split by whether the value is a capacity *source* or a best-effort
  *refinement* of an independent observation:
  - *Unreadable/malformed/incomplete cgroup state fails open, by decision.*
    The cgroup clamp refines a host measurement that was independently
    observed; a controller whose files are missing, unreadable, or
    non-numeric is logged (`cgroup_memory_state_incomplete` /
    `cgroup_memory_state_malformed`) and leaves the host value unchanged.
    Self-cgroup resolution failures degrade a step at a time before reaching
    that fail-open floor: an unresolvable cgroup path still reads at the
    **parsed** mount point (`cgroup_self_path_unresolved`), and only unusable
    proc files fall back to the compiled-in default mount
    (`cgroup_self_state_unreadable`). An ancestor walk deeper than the depth
    limit fails open outright (`cgroup_ancestor_walk_truncated`) — a partial
    chain must never pass for a complete observation, because the dropped
    levels nearest the mount point hold the broadest limits.
    Failing closed would refuse all adaptive admission on any host with an
    odd cgroup mount for a harm that is conditional (a real limit must exist
    *and* be smaller than the estimate), while the runtime defence-in-depth
    (per-context RSS budgets, the in-flight reservation, the OS reserve, and
    the memory-pressure sampler) bounds the over-admission cost. This is an
    availability-over-strictness trade taken deliberately.
  - *An observed zero is exhaustion, not absence.* `available_ram_bytes()`
    returning `0` means the host or cgroup memory limit is configured and
    fully consumed *right now* — an honest measurement, never conflated with
    unobservable. Capacity-deriving consumers (adaptive admission, the
    in-flight limit, `estimate_safe_training_rows`) all validate through one
    shared helper (`_host_memory.require_positive_available_ram`) and refuse
    a zero with the exhaustion remedy ("available memory is exhausted (the
    host or cgroup memory limit is currently fully used); free memory and
    retry"). The remedy deliberately does **not** offer configuring an
    explicit limit: an explicit limit bypasses the zero observation rather
    than creating capacity, so it belongs only to the unobservable remedy
    ("physical RAM is unavailable; configure an explicit execution memory
    limit"). Because cgroup v2 `memory.current` includes reclaimable page
    cache, a zero can be transient I/O pressure that self-heals — hence
    retry guidance rather than a configuration change. Consumers never
    floor a zero budget up into fabricated capacity: the training
    estimator's refusal here is a behaviour change from the pre-#171-review
    code, which floored a zero budget to the 500-row minimum and proceeded.
    (A tiny-but-positive budget still floors to the minimum-safe-rows
    constant — that is the deliberate minimum-viability floor, applied only
    to capacity that was actually observed.) A *negative* value is a probe
    defect, not exhaustion — the cgroup clamp floors real headroom at zero,
    so no honest observation is negative; the helper routes negatives to the
    defect remedy ("memory probe defect; configure an explicit execution
    memory limit").
  - *Host availability itself never fabricates.* When every applicable probe
    fails the result is `None`; no synthetic capacity is returned, and
    capacity sources fail closed on it with the unobservable remedy above.
  - *Unknown VRAM warns, observed-insufficient VRAM refuses.* The GPU VRAM
    pre-check is advisory ahead of CatBoost's own device errors: when
    `available_vram_bytes()` is `None` on a GPU-selected training path, the
    check attaches a user-visible warning (estimated need, detection
    unavailable) but does not refuse the launch (`_VramCheck.insufficient`
    stays false); only VRAM actually observed and smaller than the estimate
    blocks GPU training with the switch-to-CPU remedy. An internal failure
    of the pre-check itself is likewise swallowed but surfaced as a job
    advisory, never silently.
  - *Visibility bar per refinement.* "Fails open, visibly" means a different
    surface per refinement, matched to who can act on it: the cgroup clamp's
    failure is an operator condition and logs server-side warnings only; the
    VRAM pre-check's unknown state is a user decision point and surfaces in
    the job warning and the `/estimate` response (`gpu_warning`).
- **macOS available RAM** — darwin has neither `/proc/meminfo` nor
  `SC_AVPHYS_PAGES` (absent from `os.sysconf_names` entirely), so its source is
  the Mach `host_statistics64(HOST_VM_INFO64)` VM page counters: available is
  `free_count + inactive_count` pages times the host VM page size (from Mach
  `host_page_size`, not the POSIX page size — the two diverge for translated
  processes). Speculative read-ahead pages are already inside `free_count` and
  contribute no separate term. `purgeable_count` is deliberately **excluded**:
  purgeable is an attribute of pages that remain on the active/inactive queues
  rather than a disjoint pool, so adding it would double-count pages already
  inside `inactive_count` and over-admit work. The value is an optimistic
  bound — `inactive_count` includes dirty pages reclaimable only through
  compression or swap, and compressor-held memory is not subtracted; the
  admission OS reserve and safety factor absorb that gap. Total installed RAM
  is never substituted for availability.
- **Unobservable availability** — a probe that fails records its reason, and
  the single `available_ram_unavailable` warning reports every attempted
  source's reason (a source is attempted exactly when it reports a reason), so
  the diagnostic stays honest when all of them fail. When every applicable
  probe has failed the result is `None`, never a fabricated capacity; an
  earlier source's failure never blocks a later source's real observation.
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
lineage request). Runtime identity is computed after automatic input preparation and
before strategy planning so a full hit does not repeat graph projection or source/footer
estimation while a refreshed generation is keyed by its new pointer. Every retained entry
therefore carries the immutable `ExecutionStrategyResult` that produced it; a full hit
installs that result on the new `ExecutionContext`, preserving the same visible bounded
diagnostic and provenance without re-planning. The current context still reports its
own admission metrics, while the cached diagnostic describes the execution that created
the cached data. A miss or partial extension always plans against the current context
before any execution. On a full hit, cached `eager_outputs`/`errors`/`timings` are served directly.
On a partial hit (same graph, new target needs more materialised nodes), calls
`_eager_execute()` for only the newly-needed portion and merges into the cached entry
— fresh outputs win over stale cached ones for any overlapping node id, and a node
that re-executed successfully clears any stale cached error. On a full miss, executes
from scratch. `_eager_execute()` compiles the preamble (`_compile_preamble`, tolerant
of failure — the error is attached only to nodes whose builder actually consumes the
preamble namespace) and delegates to `_execute_eager_core()`.
When the caller omits an execution context, `execute_graph()` creates its admitted
`PREVIEW_EAGER` context before any preview-cache work. Cache lookup, hit, extension,
and miss stages therefore always use a concrete context and always record telemetry;
there is no silent no-op stage path.

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

**Shared preparation and node boundaries.** `_prepare_execution()` consumes a
`PreparedExecutionRequest` before either engine starts policy-specific planning. It
canonicalises runtime paths, calls `projection_planner.prepare_graph` once, validates
graph-shape contracts for exactly the executed subset (routes can submit raw frontend
graphs that bypass the parser), normalises required-column seeds, fixes the active
strict/degraded contract policy, and builds child, full-parent, and incoming-edge
indexes. Ancestor/live-switch filtering is explicit in `prepare_graph`; the strict
topological sorter receives only the resulting relevant edges. Code that intentionally
sorts a node subset with a broader edge set must call `topo_sort_ids_filtered` and
inspect its dropped-edge evidence.

**Automatic input preparation.** Between `_prepare_execution()` and strategy planning, the
lazy engine calls `haute._input_preparation.prepare_input_snapshots()` over the pruned
order (and `executor.execute_graph` calls it before its request planning), so a missing or
stale snapshot generation is built or refreshed — under the current native cap in-process,
or in a spawned hard-capped worker admitted from the execution's budget — before the RAM
estimator reads generation metadata, and before the preview path computes its runtime
identity, so a refreshed generation's pointer is the one keyed. The Explore and
training-preparation surfaces build their dataframe-cache request before the engine
prepares, so after a refresh that entry is keyed by the superseded pointer and misses once;
the next execution keys the new pointer, and correctness never depends on it because the
current source signature is part of every key. `schema_only` executions
and executions without an admitted context skip it. The IO-layer specification owns the
lifecycle, the cap gate, the single-flight, and the `InputPreparationError` reason codes;
the engine owns the call order, the `input_snapshot_auto_build` warning, and the
`input_preparation` list in `ExecutionContext.metrics_payload()`, typed by
`InputPreparationRecordPayload` on `haute._execution_schemas.ExecutionMetricsPayload` and
regenerated into the frontend contracts. `_runtime_input_paths` signs a snapshot-backed
input by its generation pointer and its current source signature.

Both engines construct one `NodeBoundaryRunner` from that result and their common
function table. Opening a boundary resolves its effective column contract
(`_resolve_effective_contract`; only `PREVIEW_EAGER` degrades known
`ConfigError`/`OSError`/MLflow boundary failures to opaque), records the shared
checkpoint/demand metric, and supplies edge-aligned frame routing. The runner applies
the same pre-call input contract and simple-join schema checks and the same post-call
output contract on both paths. It does not own collection, projection refinement,
cache/checkpoint decisions, timings, or error adaptation.

**`_execute_eager_core()`** (`_execute_lazy.py`): consumes the shared prepared
execution, computes a backward column-projection plan when required-column seeds are
supplied, and builds per-node callables via `_build_funcs()`. It then walks `order`
once: for each node, the shared runner checks input columns against the contract before
calling the node function, calls it,
applies `selected_columns`/`column_renames`, checks output columns against the
contract, and either materialises the result (`streaming_collect`) or — when
`materialize_node_ids` restricts collection to a target-only preview — keeps it lazy
and reports schema via `collect_schema()` without collecting. Exceptions are
captured per-node when `swallow_errors=True`, except
`ContractMismatchError` and `SchemaMismatchError`, any `HauteError` whose class
declares a stable public `error_code`, plus `ExecutionCancelledError` and
`ExecutionMemoryLimitExceededError`, which always propagate. The preview route
adapts either explicit mismatch to the same in-situ error response.

Before `_build_funcs()` constructs a JSON `apiInput`, eager and lazy execution
derive a per-source `{port_label: columns | None}` demand from the prepared
lineage's complete edge keys and actual `sourceHandle` values. Only ports
on relevant edges are requested. A concrete edge demand is used only when every
demanded column belongs to that declared port; otherwise that port remains
full-width and its unprojected boundary remains diagnostic. Multiple consumers
of one port union their proven columns, while one opaque consumer makes that port
full-width. Previewing an API-input source without a selected port requests the
full bundle. Demand-scoped ancestors continue to report the complete declared
schema through flat `columns` for a single port and through `frame_columns` for
every configured multi-port frame; schema visibility never requires loading an
unused parquet payload.

The strategy planner's JSON footer estimator, runtime-input fingerprint, and runtime
loader all consult the JSON-shredding source-signature boundary. Their independently
initiated calls share one process-wide SHA-256 proof only behind the same native
identity/change revision. The JSON `apiInput` runtime-file record therefore carries
that SHA-256 proof directly and must not additionally call the generic xxHash runtime
path boundary. This is a versioned `RUNTIME_GRAPH_INPUT` byte-layout change. The first
observation or any revision movement still performs the full content hash, and
native-revision failure stays visible through the JSON component's structured
conservative-fallback warning. Non-JSON runtime paths continue through the generic
stat-gated runtime-path fingerprint contract.

An exact empty edge demand means that the consumer needs row cardinality but no
user column (for example `select(pl.len())`). Polars cannot preserve non-zero row
cardinality in a zero-column frame, so source, edge, and checkpoint projection
retain exactly one deterministic schema-ordered carrier column. The carrier is a
physical execution detail, not a logical demand: it is removed naturally by the
consumer and must never broaden to the whole source or collapse the row count.

**Sink/lazy execution (`execution.execute_lazy_graph` → `_execute_lazy._execute_lazy`).**
Consumes the same `PreparedExecution` and `NodeBoundaryRunner` as eager execution,
plus: optional seeding from a
`DataFrameExecutionCacheRequest` (skips rebuilding any node whose entire downstream
lineage is already cache-covered, via a reverse topo pass computing
`cache_covers_downstream`), a fuller backward projection analysis (a checkpoint dir,
a non-live source, or explicit required columns triggers it; the execution profile
never does), then `_build_funcs()` for the nodes still needing construction. Each node's lazy
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

When a dataframe-cache key names a broader concrete `required_columns` set than the
immediate runtime request, backward projection is planned from their union. The cache
key is therefore a physical materialisation demand, not only an identity check: source
and intermediate projections must retain every passthrough dependency needed to write
the declared artifact. A narrow runtime request may warm a broader cache entry, but it
must never silently prune a cache-key column and then skip the cache write. If a column
required only by that broader cache key is absent from the actual runtime schema, cache
population is skipped and the cache-only demand is removed from both edge and structural
checkpoint projections. A missing cache-only column must never fail otherwise-valid
runtime execution; a missing runtime-required column still raises the typed contract
mismatch at the first proven boundary.

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
`POLARS`/`SCENARIO_EXPANDER` nodes and post-read Data Input code via
`classify_chunk_local_polars_code()`, whose ineligible `ChunkLocalDecision` becomes a
`ChunkUserCodeUnsupportedError` carrying the node, blocking operator, reason, line, and
column), validates the
chunk suffix is a single-parent chain, and sizes chunks either from an explicit
`chunk_size` or from `target_chunk_bytes` (which requires building the real projected
target-output schema through `execute_lazy_graph` under the schema-only declaration
and either costing fixed-width dtypes exactly or sampling up to 128 rows for
variable-width columns through a bounded `limit` of the lazy target plan; an OUTPUT
target's document is described from its schema at plan time and never assembled, so
its fixed-width document columns are costed from the derived schema, a variable-width
flat column (`$[:].name`) is sampled from the mapped source column of the target's
single parent plan when no materialising operator sits upstream (a sample through one
would execute it at plan time), and a nested document column, a multi-frame document,
or a variable-width document column under a materialising operator is a
`ChunkPlanUnsupportedError` that routes the caller to the full executor instead of the
nominal width the planner still uses for other targets under a materialising operator,
which would silently under-bound the chunk).
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
running `_isolated_worker_entrypoint`. Before user work begins, Linux prefers a
delegated cgroup-v2 child with a finite `memory.max` and otherwise applies
`RLIMIT_AS`; Windows assigns the child to a Job Object with a finite aggregate
job-memory commit limit, so descendants cannot each consume the complete admitted
budget. Limits are growth budgets: the native baseline is measured before the
hard ceiling is installed. `NativeMemoryLease.apply()` clears its recorded backend
before attempting an installation and `restore()` clears it after releasing the
cap, so `lease.backend` is non-`None` only while a cap installed by the current
request is active; `_isolated_worker_entrypoint` and the warm interactive worker
loop enter `native_memory_backend_scope` with that backend only when `apply()`
returned `True`. Independently, the parent samples child RSS and
terminates it when the configured cap is crossed; this watchdog is secondary
defence and observability, never evidence that a hard kernel cap exists.
`process_memory_caps_supported()` therefore means a native hard cap is available,
not merely that RSS can be sampled. The child calls the target function and
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

`HAUTE_WORKER_MEMORY_ENFORCEMENT` has only `best_effort|required` and defaults to
`required`. Configuration is
resolved before spawn from plain admitted-context fields; the child constructs a
fresh context rather than receiving the parent object. The envelope carries the
parent's effective admitted growth headroom (which may be narrower than the profile
default under an absolute process cap) and that optional absolute cap. The child and
parent watchdog both enforce the narrower of those limits against the warm child's
own RSS baseline. `required` rejects a missing positive native growth limit or an
unsupported cap before process creation; an absolute RSS watchdog value alone never
satisfies that contract. A native-cap setup failure is a contract error before user
work begins. `best_effort` is an explicit compatibility override that continues with
any platform-supported cap plus child RSS checkpoints, without claiming hard
enforcement. A failed best-effort programming attempt clears its active-backend evidence;
the request cannot advertise or restore a limit that was never installed, including when
a warm Windows worker retains an otherwise reusable Job Object handle. Native cgroup
directories and Job Object handles are scoped to the
worker and cleaned after exit. Cancellation or
timeout terminates, escalates to kill, joins, and verifies death; a surviving child is
`IsolatedWorkerTerminationError`, never reported as successful cancellation.

macOS exposes resource-limit symbols but does not provide a dependable hard
per-process memory ceiling for this contract. Required mode therefore rejects it;
macOS callers and cross-platform transport tests must opt into `best_effort`
explicitly, retaining RSS supervision without claiming native enforcement.

The active native lease exposes only its bounded backend kind within the worker's
execution context. Code that may create descendants can do so only under an aggregate
`cgroup` or Windows Job Object lease. `RLIMIT_AS`, an unavailable best-effort lease,
and an ordinary in-process execution context are not descendant-wide evidence; those
paths must retain a single-process bounded algorithm instead of multiplying the
admitted budget across a process pool.

The native lease covers result construction, synchronous pickle serialization, and
transport-buffer publication—not merely the user callable. A one-shot child closes and joins
its result-queue feeder before it exits and never widens its finite lease first; OS teardown and
the joined parent's exact native-resource cleanup end that lease. A warm child sends one
serialized result while the lease remains active, waits for `("ack", job_id)`, restores the
lease, and returns a matching release acknowledgement. The parent validates both envelopes
before returning or reusing the slot. Any missing, stale, malformed, or failed release kills and
replaces that exact worker, so queue buffering cannot become an unaccounted post-request memory
spike.

**Warm interactive worker pool.** `InteractiveWorkerPool` owns a fixed number of
long-lived `spawn` workers (default two, configured by
`HAUTE_INTERACTIVE_WORKER_COUNT`). Each slot has a single request in flight and a
bounded result queue. A stable, non-user-visible affinity digest maps the same
graph/source lineage to the same slot so `_preview_cache` and trace cache hits survive
between clicks. The server starts the pool after environment/project initialisation
and closes it during lifespan teardown; a lazy start is retained for non-ASGI callers.
Worker readiness is polled at the configured supervisor interval. If a child exits
before publishing its ready envelope, startup fails immediately with its exit code;
it never waits out the full startup deadline for a process that is already dead.
The wire request is `(job_id, module-level callable, pickle-safe args/kwargs,
IsolatedExecutionBudget)`. A worker constructs a fresh local `ExecutionContext` from
that budget and returns only the route result/metrics envelope. Parent locks,
cancellation tokens, callbacks, Polars frames, and contexts never cross the process
boundary.

The supervisor starts a request deadline only after its affinity slot is acquired. It
polls the result channel, worker liveness, parent cancellation/supersession, and child
RSS at the configured interval. Timeout, cancellation, supersession, memory excess,
protocol mismatch, or an unresponsive termination kills and joins that exact worker;
the slot is synchronously replaced before another request can acquire it. Successful
results and structured remote errors carry a matching job id. A stale result from a
previous worker generation is a protocol error, never accepted for a later request.
Pool shutdown signals every in-flight slot; active work is killed and joined at the
next supervisor poll without starting a replacement, so ASGI teardown cannot wait for
the request deadline or leave computation behind. Slot cleanup is idempotent across
the request and teardown paths.
Only the closed public contract-error identities and Haute's two structured memory
errors may contribute an HTTP detail payload; an arbitrary exception that happens to
define `to_payload()` is still an internal 500 and cannot smuggle child data into a
response. Three additional memory outcomes are classified from parent-side evidence
and answered with a parent-authored, data-free 507 detail (never the child payload):
an `InteractiveWorkerCrashedError` whose exit code looks memory-limited under a
configured growth cap (the same `SIGKILL`/`SIGABRT` heuristic as one-shot workers,
recorded as `terminal_reason="memory_limited"` on the exception), a remote error
whose exact identity is `builtins.MemoryError`, and a remote
`haute._native_memory_limit.NativeMemoryLimitUnsupportedError`. A remote exception
merely *named* like one of these from another module remains an internal 500.
`HAUTE_INTERACTIVE_EXECUTION_MODE` is the closed set `process|thread`; production
defaults to `process`. `thread` exists as an explicit compatibility/test mode and
retains the documented non-killable timeout semantics—it is never an automatic
fallback after a process failure.

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
- **Polars lineage is structural, compositional, and fail-closed.** The analyser
  accepts a plain sequence assigning the live frame to `df`, including an identity
  program, with inert imports, docstrings, and scalar literal helpers. It turns each
  supported call into an
  operation with a forward schema transfer and a backward demand transfer. Literal
  `select`/`select_seq`, `with_columns`, `rename`, `filter`, `fill_null`, literal
  `drop`, `drop_nulls`, `with_row_index`, row-only slicing, `sort`, literal `cast`,
  literal `shift`, literal-subset
  `unique`, literal `explode`, literal `unpivot`, closed `group_by(...).agg(...)`, and
  supported literal-key joins can be composed in one chain. Every expression or
  aggregate that Polars executes contributes its input columns even if a later
  select omits its output. A select, an aggregate, or an `unpivot` whose `on` and
  `index` are both literal establishes an exact schema without requiring an upstream
  schema; rename, join, `with_row_index`, and `unpivot` collision decisions use exact
  schemas and otherwise fail closed (`invalid_rename`, `join_schema_ambiguous`,
  `invalid_with_row_index`, `invalid_unpivot`). A strict `drop` demands every dropped
  column because Polars requires them at runtime, and `drop(strict=False)` demands
  none; a `cast` with a literal `{name: dtype}` mapping keeps the schema and demands
  every mapped column (Polars raises `ColumnNotFound` for one that is missing) while a
  whole-frame `cast(<dtype>)` demands nothing extra, and `shift` with a literal period
  keeps the schema and passes demand through unchanged (`dynamic_cast`,
  `dynamic_shift` for any other argument); `drop_nulls` without a subset and `unpivot` without a literal `on` list
  demand the exact upstream schema and fail closed without one
  (`drop_nulls_schema_unknown`, `unpivot_schema_unknown`); `with_row_index` produces
  its index column and demands nothing, and without an exact schema it fails closed
  (`with_row_index_schema_unknown`) because Polars raises on a duplicate index name
  and projecting that column away could otherwise hide the failure. Non-literal
  arguments to any of these operations return the operation's `dynamic_<method>`
  reason. A bare `*` or `^...$` string is selector syntax wherever Polars accepts a
  column name and expands to zero or many columns on the lazy path, so it never
  proves one literal column: every bare-string column read (`select`/`with_columns`
  names, `drop`, `drop_nulls`, `sort`, `unique`, `explode`, `unpivot`, group-by keys
  and aggregate names, `filter` predicates, and horizontal helpers) returns the
  operation's dynamic reason for it, exactly as `pl.col('^...$')` already did.
  Expression methods are attributable only when their bare string arguments are
  provably literals. The closed `_LITERAL_STRING_ARGUMENT_METHODS` registry names,
  per `str` or `dt` receiver namespace, the methods Polars parses with
  `str_as_lit=True` or as plain format/configuration strings (for example
  `str.contains`, `str.starts_with`, `str.replace`, `str.strip_chars`,
  `str.to_date`, `dt.truncate`, `dt.offset_by`, `dt.convert_time_zone`); the match
  is receiver-aware, so a same-named method on another namespace does not inherit
  it. A method outside the registry with a direct string argument remains
  unsupported. `tests/test_column_lineage.py` verifies the registry against the
  pinned Polars source, so an upgrade that changes a registered method's string
  semantics fails the suite instead of silently under-demanding.
- **Lineage inputs are incoming edges, not parent node ids.** Each input binding
  carries its complete `ProjectionEdgeKey`, executable `edge_input_name`, and exact
  schema when one is available. Exact API schemas are resolved per source handle;
  exact structural Polars outputs propagate in topological order. This permits two
  ports of one `apiInput` to join one another and permits several joins in one linear
  transform without conflating their demands. Generic contracts that address only a
  parent node id remain usable when that id identifies exactly one incoming edge;
  duplicate-parent ambiguity is an observable boundary.
- **Concrete registered contracts propagate exact single-input schemas.** In the
  same topological forward pass, a non-opaque registered builder contract with one
  exact incoming edge transfers `input columns ∪ produced columns` to the node's
  exact output. This is the schema counterpart of the existing backward contract
  algebra, which treats non-produced output columns as passthrough. It lets a
  terminal identity preview (including `modelling`) turn an unseeded all-column
  request into a concrete demand without claiming that any columns were pruned.
  An `AllExceptColumns(required_columns, excluded_columns)` seed is resolved whenever
  that exact output is available as
  `(exact output - excluded_columns) ∪ required_columns`; the resolved seed is
  unioned with any independent downstream demand and propagated through ordinary
  edge/port algebra. This makes CatBoost's include/exclude feature menu a physical
  source projection rather than a post-load dataframe drop. Without an exact output
  schema the seed remains schema-dependent and conservative; the planner must not
  guess that `required_columns` alone is the complete training input.
  Declared annotations are not used as forward-schema evidence for arbitrary user
  code; missing input schemas, opaque registered contracts, and multi-input nodes
  remain unproven.
- **Unowned fan-in never uses ordinary contract algebra.** After the dedicated
  optimiser, edge-join, and compositional Polars fan-in rules have had an
  opportunity to assign columns to individual incoming edges, any remaining node
  with more than one distinct parent retains an unprojected streaming boundary.
  A generic passthrough contract cannot be broadcast to every parent because its
  output-column demand does not prove which parent owns each input column. An
  `edgeJoin` may receive its `base` and `join` roles through two distinct frame
  edges from the same multi-frame source node. Because a parent-id demand cannot
  distinguish those physical edges, the edge-join rule validates both role handles
  and keeps both edges full-width under edge-join diagnostics; it never overwrites
  one role's demand with the other or collapses their identities.
- **Projection decisions do not depend on the execution profile.** The planner has no
  strict-profile switch. A multi-parent polars node and user code with an unbounded
  projection contract keep a visible full-width boundary in every profile; a fan-in
  join whose code cannot be parsed or whose `how`/`suffix` is not literal, and a
  projection seed that cannot replace opaque demand from several downstream
  consumers, keep the boundary and record a `ProjectionReason` (`fan_in_join_unparsed`,
  `fan_in_join_dynamic_arguments`, `projection_seed_blocked_by_opaque_fan_out`) instead
  of raising. Only a contradictory declared contract is an error
  (`ContractMismatchError`), in every profile. Given the same graph and inputs, two
  profiles may differ in budgets, in eager-versus-streaming output mechanics, and in
  the diagnostic labels that describe those mechanics, never in which columns or
  boundaries the plan keeps.
- **Unsupported syntax stays visible.** Dynamic selectors/keys, dataframe-dependent
  helper assignments, unregistered expression functions, string-argument methods
  outside the audited `str`/`dt` literal-argument registry,
  branches/loops/functions, unsupported join options, schema-
  dependent ownership without an exact schema, and operations without a registered
  transfer return a structured unsupported lineage result. The planner retains the
  full-width edge/node boundary and its diagnostic; neither planning nor the UI
  silently treats it as projected.
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
  `_passthrough_fn` (a `MODEL_SCORE` with no source type selected, or an
  unconfigured `OPTIMISER_APPLY`, "drag onto canvas, configure later") has a
  contract describing its *configured* shape, which
  the stub doesn't yet produce; the output check is skipped for exactly this state so
  the unconfigured UX doesn't look broken, while input checks and any contract that
  becomes concrete once the node IS configured still apply.
  Selecting `run` or `registered` is a configuration commitment: a missing `run_id`
  or `registered_model` is a loud `ConfigError` in both contract planning and the
  runtime builder, never an identity passthrough.
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
- **A non-instance Polars `inputMapping` preserves authored input identity across
  parent replacement.** `resolve_orig_source_names` derives the current input names
  from the node's incoming edges, validates the mapping as a one-to-one
  `{logical_name: current_edge_name}` relation, and returns logical names in current
  edge order. `_exec_user_code` therefore exposes both the current binding and the
  stable logical alias during canvas execution. Stale, malformed, or colliding
  mappings raise `ConfigError`; they never fall back positionally. Instance nodes
  retain the existing original-node mapping path and take precedence over this
  ordinary-transform behaviour.
- **`topo_sort_ids`** is insertion-order deterministic (via `graphlib.TopologicalSorter`,
  not the previous heap-based sort), so callers must pass `node_ids` as an
  insertion-ordered sequence, never a `set`, or tie-break order becomes
  hash-randomisation-dependent across process runs. An unknown endpoint raises
  `UnknownEdgeEndpointError`; an intentional subset traversal must instead call
  `topo_sort_ids_filtered` and inspect its dropped-edge evidence. `CycleError` reports
  every node participating in *any* cycle
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
- **The operation registry is the single operation vocabulary.**
  `haute._polars_operations.POLARS_OPERATIONS` is a frozen mapping keyed by
  `(receiver, namespace, name)` where the receiver is `frame`, `expr`, `namespace`,
  or `polars_function`. Each `PolarsOperation` carries `operation_class`
  (`row_local`, `order_dependent`, `row_expanding`, `fan_in_stateful`, `opaque`),
  `policy` (`row_local`, `streaming`, `materialisation_boundary`, `opaque`),
  `expansion` (`none`, `bounded`, `unbounded`), `chunk_admitted`, `lineage_supported`,
  `materialisation_factor_basis_points`, `memory_evidence` (`measured` or `none`),
  and a one-line note. Import-time validation
  rejects duplicate keys, a missing note,
  a chunk admission outside `row_local`, a materialisation policy outside
  `fan_in_stateful` or `order_dependent`/`row_expanding`, an expansion outside
  `row_expanding` or `opaque` (an opaque
  callback such as `map_batches` may record its unbounded expansion), a
  materialisation boundary without a factor, and a streaming or row-local policy
  carrying any factor other than exactly 100 basis points (no multiplier). The chunk classifier's frame, expression,
  namespace, and `pl` allowlists are the registry's `chunk_admitted` names; the
  cardinality analyser's unbounded-expansion expression set is the registry's
  `unbounded` expressions; the planner's materialisation-boundary operators are
  `materialising_frame_methods()` — every frame method with the
  `materialisation_boundary` policy (`group_by`/`groupby`, `sort`, `unique`,
  `join`, `join_asof`, `top_k`, `bottom_k`, `reverse`, `explode`) — plus
  `materialising_expression_methods()` (`over`), matched receiver-aware in
  evaluation order. The
  classifier tracks, for every simple name, whether it is a *frame* (one of
  the node's input frame names per incoming edge, as `_build_funcs` binds
  them, `df`, or a name bound from a frame), a *provable non-frame* (`pl`
  itself, a literal, an operator or comparison result, a `pl`-rooted
  expression chain through a registered `pl` function, a function or lambda
  object, or a name definitely rebound to one of those), or a *may-frame*
  (every other name: a preamble name, a function parameter, or a name bound
  from a call the analyser cannot see through, such as a user function, a
  preamble helper, or an unregistered `pl` constructor like `pl.concat`). A
  materialising call counts unless its receiver is a provable non-frame, so a
  preamble frame's `group_by`, a helper's returned frame, and a parameter
  inside a user function all admit a boundary, while
  `pl.col(...).list.group_by(...)` never does. Facts are captured in Python
  evaluation order (receiver before arguments, the first `and`/`or` operand
  and the first comparator before the rest, dictionary pairs in order, a
  comprehension's first iterable in the enclosing scope); one assignment binds
  all of its targets from the captured facts (parallel semantics, so a tuple
  swap moves the frame fact with the value); and function and lambda bodies
  are analysed in a scoped environment whose parameters are may-frames. A
  definite rebinding (a top-level statement, or a walrus in a position that is
  always evaluated) to a provable non-frame removes the frame fact only for
  the code that follows, so rebinding an alias after its group-by cannot hide
  the boundary. Bindings inside nested blocks, short-circuit operands,
  conditional branches, lambda and function bodies, and comprehensions are
  may-bindings that can add a frame fact but never remove one; unresolvable
  shapes (unpacking from an unknown value, starred, loop, `with`, and
  comprehension targets) and every mutable container (a list, set, or dict
  display or comprehension, whatever it holds, since it may receive a frame
  later) yield may-frames, a tuple is a provable non-frame only when every
  element is, and a subscript, attribute, or augmented assignment marks the
  root name it mutates as a may-frame, so an unsupported shape can only add a
  boundary, never hide one. A frame method taken as a value, an unbound
  frame-class method called through `pl`, and any `pl` attribute that is not a
  registered expression helper are treated as frame receivers, so binding a
  method to a name or calling `pl.LazyFrame.group_by(frame, ...)` cannot hide a
  boundary within the node's own code. Calls into functions the analyser cannot
  see (a `utility` module import) are not boundaries; the process RSS watchdog
  remains the defence for those. A same-named method on a `pl` expression never
  creates a
  boundary. `over` is the exception to the receiver rule: window expressions are
  written on a `pl` chain, so an `over` call admits a boundary on any receiver
  that is not a provable frame method of another name, and the containing node
  records `over` as its operator.
  `unpivot`, `rolling`, `group_by_dynamic`, `shift`, `merge_sorted`,
  `interpolate`, and `filter` keep the `streaming`/`row_local` policy with the
  measurement that justifies it in the note; `join_where`, `pivot`, `upsample`,
  `gather`, and `sample` keep `streaming` with `memory_evidence=none` recorded.
  `memory_evidence` is what makes the lane self-checking: `measured_operation_names(receiver)`
  returns every entry claiming evidence, and the certification test requires each
  of them to have a probe plan, so a policy can never rest on a measurement that
  was never taken. Each boundary entry's `materialisation_factor_basis_points` is
  derived from that evidence — measured peak divided by the estimator's
  rows × width × 3.0 figure for the same frame, with margin, rounded up to a whole
  multiple: `sort` 300, `unique` 350, `join` 200, `join_asof` 250, `over` 250,
  `reverse` 250, `top_k`/`bottom_k` 100, `group_by` 100.
  `join_asof` holds its right (lookup) port while streaming its left, so its
  evidence is the big-right variant: a wide left against a small right sits near
  the streaming floor and proves nothing, while swapping the ports puts the large
  frame in the buffered position and shows the state plainly. `interpolate`
  streams: it measures about 1.1x its like-for-like control at 1.5M rows -- a
  passthrough of the same two columns with the same nullability (mean of
  interleaved pairs; observed 1.0 to 1.2 across fresh-process runs). Two earlier readings were
  measurement artefacts rather than the operator: one near 1.5x was the real-work
  verification running inside the sampled process, which is why that verification
  now runs in the parent, and one near 1.4x came from comparing a nullable read
  against a dense two-column scan. `explode` carries the
  default 100 that is never applied, because its estimate is unavailable. `tests/test_polars_operations.py` keeps
  every derived set equal to the registry, and
  `tests/performance/test_execution_engine_certification.py` keeps the policies
  equal to fresh-process measurements.
- **The chunk classifier is a receiver-aware AST walk with a closed decision
  vocabulary.** There is no textual prefilter: a comment or string literal containing
  `.sort(` cannot affect eligibility. Frame-level methods are admitted only when the
  receiver derives from a frame (`_ROW_LOCAL_DF_METHOD_NAMES`), bare expression methods
  only on an expression receiver (`_ROW_LOCAL_EXPR_METHOD_NAMES`), namespace methods only
  on their `str`/`dt` receiver (`_ROW_LOCAL_NAMESPACE_METHOD_NAMES`, keyed by namespace),
  and top-level helpers only from `pl.` (`_ROW_LOCAL_POLARS_FUNCTIONS`); a same-named
  method elsewhere is rejected, so `pl.col('x').list.sort()` never inherits a `sort`
  decision and `df.sort(...)` never inherits a `list.sort` one. Every admitted entry
  cites a chunked-equals-full proof case and the inventory test keeps the allowlists and
  proofs one-to-one. `Expr.replace` is admitted only with a literal scalar mapping (a
  dict of scalars, or literal `old`/`new` sequences; the deprecated `default=` form is
  rejected); `str`/`dt` methods are admitted only with literal arguments, and
  `str.to_date`, `str.to_datetime`, `str.to_time`, and `str.strptime` additionally
  require an explicit non-empty literal `format`, because Polars otherwise infers the
  format from the data and two chunks can infer differently. Methods whose validator
  admits more than one materially distinct shape declare those shapes in
  `_ADMITTED_CALL_SHAPES` (for example `expr.replace`: `mapping`, `old_new`; the
  temporal parsers: `explicit_format_lenient`, `explicit_format_strict`), and the proof
  inventory requires a chunked-equals-full proof per declared shape, comparing raised
  exception classes as well as frames. `classify_chunk_local_polars_code(code, frame_names=...)`
  returns `ChunkLocalDecision(eligible, reason, blocking_operator, line, column)` with
  the closed reasons `eligible`, `empty_code`, `no_frame_names`, `syntax_error`,
  `unsupported_statement`, `assignment_not_frame_derived`,
  `frame_embedded_in_expression`, `unsupported_frame_method`,
  `unsupported_expression_method`, `unsupported_namespace_method`,
  `unsupported_polars_function`, `unsupported_call_shape`, and
  `unsupported_expression`; the operator is the method, function, frame, or AST node
  name that blocked the walk and the location is its 1-based source line and column.
  The first blocking construct in source order wins: sibling children are visited by
  source position, so a dictionary value or a conditional body reports before a
  textually later key or test. `with_row_index`, forward or
  backward fills, column-derived `is_in`, categorical or Enum casts, joins, ordering,
  uniqueness, windows, group-bys, and global reductions stay rejected.
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
- **RAM estimation returns `None` rather than guessing** when parquet metadata, the
  target row-cardinality proof, or the canonical detailed target schema is unavailable
  (for example Databricks sources or opaque row expansion) — callers must treat `None`
  as "estimate unavailable," not "unlimited." `estimate_safe_training_rows()` uses the
  target node's proven output upper bound for training/pool sizing and user-facing row
  counts. It does not reuse the largest ancestor row count. The independent
  materialisation estimator uses the greatest intermediate bound and is admitted before
  the full-frame cache/checkpoint; a final target-row limit cannot conceal or mitigate an
  over-budget upstream join.
- **A JSON `apiInput` is sized per emitted table, not per node.** Its v2 cache is one
  parquet per emit-true table, so the node has no single `(row_count, column_count)`
  summary — but each table does, and an edge's source handle names the exact table it
  carries. Ancestor sizing therefore resolves one metadata record per source handle that
  actually feeds the target, and target-column resolution carries the arrival handle so a
  consumer — including a direct Edge Join base or join input — resolves its own table's
  columns; the per-estimate memo keys are
  `(node, handle)` accordingly. Sibling branches of the same input never inflate a
  boundary they do not feed. Layer preference (`working` then `committed`) and cache
  validity are delegated to the same reader the engine executes with, so a stale cache is
  rejected here exactly as it is at execution rather than silently sizing a boundary from
  the wrong data; an unreadable or unmatched cache still yields "estimate unavailable."
  Without this, every group-by beneath an `apiInput` was refused for want of an estimate —
  which is the ordinary shape of aggregating a shredded child table per quote.
- **Demand-scoped admission is proof-sensitive.** For each materialisation boundary,
  the planner inspects every relevant incoming edge. When all edge demands are exact,
  estimation uses the union of the physically required columns, including columns
  read only by grouping, filters, joins, and expressions. A cardinality-only demand
  retains one physical carrier column. If any edge is opaque, lacks exact schema, or
  cannot map a demanded column to source metadata, estimation uses the complete
  relevant source-port width. Unrelated sibling ports are never counted. Diagnostics
  record `projected_columns` or `complete_width_fallback` as the admission basis.
  JSON-port metadata first requires plausible schema metadata in a cache layer and
  only then computes the complete source-content proof needed to trust that layer.
  With no plausible cache generation, admission reports the estimate unavailable
  without hashing a source that uncached execution will immediately parse itself.
- **Cardinality-scoped admission is independently proof-sensitive.** Exact source or
  API-port row counts seed a memoised graph walk. Known row-preserving and
  row-reducing built-ins retain the incoming upper bound; scenario expansion applies
  its validated step multiplier; linear Polars code uses `RowCardinalityAnalysis`;
  and Edge Join uses its validated roles, strategy, and uniqueness contract directly.
  The estimator records both the output bound and the greatest bound reached while
  executing the materialising node. Join bounds are mathematical: Cartesian product
  for unconstrained inner/cross joins, one-side bounds when the opposite key is
  unique, left/right preservation for semi/anti and unique outer sides, and explicit
  unmatched-row terms for full joins. These formulas are not the empirical 3x
  lifecycle overhead factor, which is applied only after row count and physical row
  width are established. Fractional measured widths are multiplied as exact rational
  values and rounded upward, so very large finite join bounds neither overflow a float
  nor lose bytes through truncation. Missing row metadata, dynamic/unsupported join semantics,
  `explode`, opaque row-changing code, invalid input-name binding, or an unknown
  multi-input built-in returns `row_cardinality_unavailable:<node>:<reason>`; the
  normal materialisation-estimate-unavailable diagnostic surfaces that detail and
  refuses admission.
- **The boundary operator scales the estimate.** The planner passes the boundary
  operator it recorded for the node to
  `haute._ram_estimate::_estimate_materialisation_boundary_from_index`, which
  multiplies the rows × width × 3.0 figure by the registry's
  `materialisation_factor_basis_points` for that operator before calibration and
  the headroom comparison, and records `boundary_operator=<op>` and
  `materialisation_factor_basis_points=<n>` in the assumptions. `explode`'s
  unbounded row expansion keeps its cardinality — and therefore its estimate —
  unavailable, so it is the first admitted boundary that routinely reaches the
  unavailable-estimate contract rather than a number.
- **A declared join boundary is sized from its inputs, not its output.** A `join`
  or `join_asof` holds both ports' rows while it builds and probes; when a
  declared `1:1`, `1:m`, or `m:1` contract bounds its output by one of those
  operands, the joined rows it emits are the next node's problem, not this
  boundary's peak. The row term is therefore
  `max(operand_peak_rows, output_rows)`: for a declared join that is
  `operand_peak_rows` — the largest frame any operation in the node consumes, so
  a chained declared join is sized from the previous join's result rather than
  from the original sources — and for a many-to-many join (no `validate=`, or
  `m:m`) it is the row product, because nothing else bounds it. The
  certification lane measured a three-times fan-out join at about 1.57x the
  input-sized figure, which falsified input sizing for undeclared joins. The
  cardinality carries `depends_on_many_to_many_join`, set by the node's own
  program and inherited from every input, and the estimate carries it too. The
  width term sums each port's width once per *logical
  reference* rather than once per port (`operand_reference_counts`): a self-join,
  or a lookup table joined twice, is resident twice and is charged twice, with the
  count recorded as `boundary_resident_operand_count`. The estimate is that
  `operand_peak_rows × <referenced port widths> × 3.0 × <factor>`, where the factor
  is the maximum across the node's chained boundary operators, and the assumptions
  record both `boundary_input_rows_upper_bound=<n>` and
  `boundary_output_rows_upper_bound=<n>` so the two numbers can never be confused
  for one another. The output bound — the
  many-to-many row product for an undeclared join, the validation-bounded count
  for a declared one — still propagates to every downstream boundary, so a
  `group_by` after an undeclared `m:m` join estimates that product and inherits
  the flag. Whenever an estimate carrying `depends_on_many_to_many_join` exceeds
  the headroom, the planner does not report `materialisation_exceeds_headroom`:
  the product is the absence of an estimate, not a measured over-run. It takes
  the unavailable-estimate path with detail
  `<node_id>:join_cardinality_many_to_many` — `full-width-conservative`/`warned`
  with `proof_gap=<detail>` under a native worker cap, and the typed
  `materialisation_estimate_unavailable` rejection without one — and both
  remediations end with the advice to declare `validate='m:1'`, `'1:m'`, or
  `'1:1'` where a key side is unique. A product that fits the headroom is
  admitted as usual, since it over-estimates in the safe direction. An incoming port whose
  cardinality or width cannot be resolved keeps the whole estimate unavailable, as
  for any other boundary: a join sized from one readable port and one unreadable
  one would be an under-estimate, which is the one failure mode admission must
  never have. A cross join (`how='cross'`) is a boundary like any other
  join, but nobody has measured what it costs, so its estimate is unavailable
  with reason `cross_join_unmeasured`: under an active native worker cap it plans
  `full-width-conservative` with status `warned`, and without one it is the typed
  `materialisation_estimate_unavailable` rejection. Its row product still
  propagates to every downstream boundary, so a later `group_by` is estimated on
  that product and, when it does not fit, takes the same unavailable-estimate
  path as an undeclared many-to-many join.

- **Estimate calibration only tightens admission.** A bounded process-local registry
  stores one basis-point multiplier per `ExecutionProfile`. On terminal metrics with
  a positive estimate and observable RSS growth, an observed/estimated ratio above
  the current multiplier ratchets that profile upward with a fixed safety margin, to
  an 80,000-basis-point (8x) cap. It never ratchets downward, never converts unavailable evidence
  into an estimate, and never keys on a graph, path, node, or column. Planning applies
  the current factor before the headroom comparison and exposes both raw/calibrated
  bytes and factor in diagnostic assumptions and aggregate telemetry. Test reset and
  snapshot seams make the state deterministic.
- **Version-1 strategy diagnostics are strictly bounded.** Boundary/reason collections
  retain at most 32 entries, provenance at most 128, and remediation/messages at most
  512 characters with deterministic truncation. Missing/malformed required fields,
  unknown version-1 enums, and higher schema versions are invalid; callers may ignore
  only additive fields within version 1. When a plan contains more than one kind of
  boundary, `blocking_node_id` and `blocking_operator` identify the first boundary of
  the selected strategy kind: a `materialisation-boundary` diagnostic points at the
  admitted materialisation rather than an earlier projection boundary, while
  projection/full-width diagnostics point at the first unprojected boundary. The
  bounded `boundaries` collection reports capped, total-counted detail in
  topological order and, when truncated, retains the earliest representative of
  every boundary kind present before filling the remaining capacity. A mixed plan
  therefore cannot truncate away its only unprojected-boundary evidence.
- **Boundary admission is profile-independent** (every admitted materialisation
  operator, not only `group_by`):

  | Profile | Version-1 result |
  | --- | --- |
  | Every `ExecutionProfile` | `materialisation-boundary` when admission and estimate fit; `full-width-conservative` when admission is present, the estimate is unavailable, and `current_native_memory_backend()` reports an active cap; otherwise a typed rejection |

  The boundary remains a materialisation boundary inside the caller's admitted budget;
  a streaming sink or chunked consumer is not treated as proof that the operator
  itself streams. An admitted boundary's reason code is `materialisation_admitted`
  for every operator, and the diagnostic's `blocking_operator` names which one
  (`group_by`, `sort`, `unique`, `join`, `join_asof`, `top_k`, `bottom_k`,
  `reverse`, `explode`, or `over`); the remediation text names that operator too,
  so an analyst is told which call forced the boundary. `haute._execution_schemas`
  and the generated frontend contracts carry the same reason-code vocabulary.
  The executor's boundary lookups are named for the general case
  (`materialising_operators_by_node`, `materialising_operators_by_input_names`). An edge whose
  executable input name cannot be derived (a malformed apiInput edge with no frame label) is skipped
  conservatively by the classifier — an unnamed input never hides a boundary — because the node
  builder, not the planner, is the fail-loud point for that edge. The lazy executor runs the graph-aware request planner so the estimate
  is derived from the same prepared target lineage before any node executes. Every
  materialising profile requires a context with admission and positive
  memory/headroom. The ordinary admission branch additionally requires
  `MaterialisationEstimate(state=available)` satisfying
  `estimated_peak_bytes <= min(memory_limit_bytes, headroom_bytes)` (equality is
  admitted); the unavailable-estimate branch is the next bullet. Missing/non-positive
  admission yields `execution_admission_unavailable`; excess yields
  `materialisation_exceeds_headroom`, except where the estimate carries
  `depends_on_many_to_many_join`, which takes the unavailable-estimate branch
  with detail `<node_id>:join_cardinality_many_to_many` instead. There is no
  chunk/streaming fallback.
- **An unavailable estimate is warned under a native cap and rejected without one.**
  `_finalise_execution_strategy` consults
  `haute._native_memory_limit.current_native_memory_backend()`, which is set only
  while a worker's native lease is active. With an active backend the result is
  strategy `full-width-conservative`, status `warned`, boundedness `unbounded`,
  `reason_code="materialisation_estimate_unavailable_conservative"`; the projection
  plan still records the group-by as a materialisation boundary (so
  `blocking_node_id`/`blocking_operator` point at it exactly as for
  `materialisation-boundary`), `headroom_bytes` carries the reserved envelope
  `min(memory_limit_bytes, headroom_bytes)`, every estimate field
  (`estimated_peak_bytes`, `raw_estimated_peak_bytes`, calibration factor, admission
  basis) is `None`, and `assumptions` carries
  `proof_gap=<estimator detail>`, `reserved_envelope_bytes=<n>`,
  `hard_cap_backend=<cgroup|rlimit|windows_job>`, and
  `disabled_optimisations=estimate_based_admission`. The remediation (≤512 chars)
  states that the run continued under its full envelope because the estimate was
  unavailable, repeats the proof gap, and asks for readable source metadata or a
  provable rewrite of the blocking operator. The lazy executor's runtime projection
  rebuilds pass the previous `full-width-conservative` strategy, reason, and
  remediation through unchanged; every other strategy is re-derived from the refined
  plan as before. Estimate calibration ignores conservative runs because they carry
  no estimate. Without an active backend the unavailable estimate is the typed
  `GroupByExecutionUnsupportedError` with `materialisation_estimate_unavailable`,
  and its remediation ends with the sentence "This surface runs without a hard
  worker memory cap, so Haute cannot run the plan conservatively here." In both
  outcomes the estimator's own reason (its `<node>:<reason>` detail, such as
  `source_row_count_unavailable`, `target_schema_unavailable`,
  `cross_join_unmeasured`, or the planner's own
  `join_cardinality_many_to_many`) is retained, and a
  `join_cardinality_many_to_many` detail additionally ends the remediation with
  the advice to declare `validate='m:1'`, `'1:m'`, or `'1:1'` where a key side is
  unique. The
  estimator already knows which node it could not measure and why; discarding that
  left an analyst with "provide readable metadata" and no way to tell an unreadable
  file from an unsummarisable source shape.
- **Runtime sources participate in group-by admission.** When an execution surface
  replaces graph inputs with in-memory frames, as deploy scoring does, the planner uses
  those exact frames as request-local source metadata for row cardinality, schema, and
  expanded variable-width sizing. Replaced inputs are not estimated from their persisted
  path configuration. Static inputs in the same graph continue to use their ordinary
  source metadata.
- **Chunking starts after global materialisation.** Pure chunk planning performs
  schema-only
  strategy analysis and may place a materialisation boundary in the pre-chunk prefix. The
  prefix is
  executed exactly once by the normal graph-aware admitted executor; only its resulting
  frame may become a chunk-runner `start_frame`. Any boundary operator inside the
  chunk-local suffix
  remains a `ChunkPlanUnsupportedError`, because evaluating a global operation per chunk
  is not equivalent to evaluating it once — aggregating, sorting, de-duplicating,
  joining, or taking a top-k per chunk all differ from the global result. The
  chunk-local allowlist already excludes every boundary operator, so the guard is
  a second, explicit refusal keyed on the registry rather than a new restriction.
  This is a physical-plan constraint, not an
  execution-profile rejection.
- **Context ownership follows materialisation lifetime.** Top-level helpers that own the
  complete materialising operation, including the compatibility `write_data_output`
  entry point, optimiser estimate route, and assistant column profiler, create and release
  an admitted context when the caller did not provide one. Helpers that return uncollected
  lazy frames continue to require a caller-owned context whose admission outlives
  collection.
- **Schema-only planning is orthogonal to group-by admission.**
  `execute_lazy_graph(..., schema_only=True)` and
  `plan_prepared_execution_strategy(..., schema_only=True)` declare that the caller
  reads `collect_schema()` and never collects a frame or invokes a sink. The gate
  above bounds peak memory *during materialisation*; a schema-only plan materialises
  nothing, so the whole gate — admission and estimate — is not
  evaluated, no materialisation boundary is inserted, and the ordinary derived
  strategy stands. The declaration relaxes nothing else: contract resolution,
  projection, and every other planning rule are unchanged, and the flag is honoured
  only for this gate. It exists because a caller that only resolves schemas would
  otherwise be refused for an operator it never runs — which made every
  aggregation invisible to the assistant's schema and dry-run boundaries.
- **Node builders receive the schema-only declaration.** `execute_lazy_graph`
  forwards its `schema_only` value to every builder through
  `NodeBuildContext.schema_only`, so a builder that would otherwise materialise
  while the graph is being built honours it. The only such builder is OUTPUT:
  under the declaration `assemble_output_from_config` returns
  `pl.LazyFrame(schema=output_document_schema(source_schemas, mapping))` and the
  document is never assembled. `output_document_schema` is the **single schema
  authority** — the collected path declares that same schema over the assembled
  document instead of inferring it — so a schema-only execution and a collected
  execution report the identical OUTPUT schema by construction. Python inference
  no longer decides OUTPUT dtypes: an all-null column keeps its source dtype
  rather than becoming `Null`, a narrow integer is not widened, and an empty
  document keeps the typed schema instead of reporting no columns at all.
  Rendering is unaffected, because `render_output_document` prunes the null
  padding a declared uniform schema introduces. A referenced source port or
  column that no incoming frame provides, and two entries mapping one output
  path from source columns of different dtypes, are
  `OutputMappingSchemaError` rejections.
- **Sampler/fault/cleanup machinery is stable.** Windows RSS bindings initialize once
  per sampler-factory identity under concurrency, reset explicitly, and reinitialize
  after a factory change. Eager diamonds share one producer-side cached `LazyFrame`.
  Timings are milliseconds. Fault points are no-ops without an injector. Cleanup runs
  callbacks in reverse registration order and releases admission once; preserving a
  genuinely propagating primary exception is an explicit opt-in.
- **Terminal telemetry is opt-in and redacted.** `HAUTE_EXECUTION_TELEMETRY` is a
  strict boolean validated/warmed at startup and defaults false. One terminal event
  has at most 48 allow-listed scalar attributes and 128-character string values; it
  excludes identifiers, paths, columns, plans, user data, messages, and exception
  text. Overflow drops/logs the event rather than truncating the allow-list, and
  assembly/sink failures cannot alter execution status. New efficiency counters are
  additive: the existing V1 RSS, truncation, strategy, admission, streamability, and
  width-state attributes remain present.
- **Efficiency evidence has closed reason sets.** `ExecutionContext` retains bounded
  counters for cache proof hits, misses, and direct fallbacks. Miss reasons are a
  closed enum (metadata/source mismatch, artifact integrity/schema failure,
  unreadable artifact, and proof unavailable); unknown strings are rejected at the
  recording boundary. Every metrics payload carries the cache-proof counters and
  their reason-code breakdown — the field is required on both the backend response
  model and the frontend guard, and `misses` must equal the closed reason-count
  total — plus aggregate
  requested/scanned widths and estimate calibration. A width total is emitted only
  when that width is known for every recorded node; partial evidence remains `null`
  rather than looking like a deceptively narrow complete total. They never include cache paths,
  source identities, column names, graph ids, or exception messages.

- **The projected root frame is never empty where the full frame is not.** The input whose
  rows form the output must carry at least one column at every step: a zero-column frame is
  an empty frame, so a demand that names only generated columns (`select(pl.len())`, a
  `with_row_index` column, a literal projection) or only columns the program later drops
  (`drop_nulls(['a']).drop('a')...with_row_index()`) would otherwise lose every row in
  silence. `analyze_polars_lineage` re-evaluates the program over the projected root schema
  (`_ensure_root_carrier`) and, at the first step where the full frame still has columns but
  the projected frame has none, adds the first root column present in the full frame at that
  step, in sorted order, repeating until no step is empty; a projected program that cannot be
  evaluated at all is reported unsupported (`carrier_unresolvable`). Demanding an extra
  column never changes a result, so the rule only widens. Other inputs keep an exact empty
  demand: an unused port has no rows to carry, and the runtime join path (`_execute_lazy.py`)
  keeps its own carrier for an empty-demand edge. Pinned by `tests/test_column_lineage.py`
  (including the drop-everything program the property test falsified) and the
  projected-versus-full property tests in `tests/test_column_lineage_properties.py`.

## Error handling

- `PreambleError` (`haute.errors`, extends `ExecutionError`) — preamble compile/exec
  failure with stable public code `preamble_failed` and optional public
  `source_line`. Interactive preview catches it inside `_eager_execute` and
  attaches it only to `POLARS`/`LIVE_SWITCH` nodes rather than aborting the whole
  preview; every non-preview profile propagates it through the shared HTTP/job
  contract-error adapter.
- `PreviewProjectionError` (`executor.py`, extends `HauteValidationError`, a
  `ValueError` subclass) — a requested
  preview-column projection references columns not present on the target frame.
- `CycleError` (`_topo.py`, extends `HauteError`) — raised from `topo_sort_ids` on a
  cyclic graph, listing every participating node.
- `UnknownEdgeEndpointError` (`_topo.py`, extends `HauteError`) — strict topology
  input contains an edge whose source or target is absent from `node_ids`. It carries
  deterministic `unknown_node_ids` and dropped-edge evidence. Intentional subset
  traversal uses `topo_sort_ids_filtered` instead and receives the same evidence in
  its return value.
- `ContractMismatchError` (`haute.errors`, extends `HauteError`) — raised by
  missing input/output columns and checkpoint/eager projection mismatches in
  `_execute_lazy.py`, and by chunking's `_project_frame`; it is re-raised with
  `SchemaMismatchError` even when eager preview uses `swallow_errors=True`.
- `SchemaMismatchError` (`haute.errors`, extends `HauteError`) — raised for a
  simple inferred join whose parent key dtypes differ. It propagates on lazy,
  fail-fast eager, and swallow-mode eager calls through the same explicit branch
  as `ContractMismatchError`. The preview HTTP adapter catches either mismatch
  and returns the same target-node error response.
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
  admission/memory contract. Its public payload names the node, operator, profile,
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
  raised before a bounded operation starts (the RSS sampler returned `None`, RSS has
  no positive headroom below a configured process cap, or the in-flight budget is
  exhausted);
  carries `to_payload()` with the same shape family as the mid-run variant. An
  in-flight refusal additionally carries `in_flight_reserved_bytes`,
  `in_flight_limit_bytes`, and `in_flight_operations`: the reservations held at the
  moment of refusal as `<profile>:<operation>` labels, deduplicated, sorted by code
  point, and truncated to the first `_MAX_REPORTED_IN_FLIGHT_OPERATIONS` (8) labels
  while the byte totals stay exact over every holder. The user-facing message names
  those labels ("reserved by ...") so a refusal identifies the work it lost to.
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
- `ChunkUserCodeUnsupportedError` (`haute.errors`, extends `ChunkPlanUnsupportedError`)
  — the user-code rejection with a public contract: `error_code`
  `chunk_user_code_unsupported` and public fields `node_id`, `node_type`, `reason`,
  `blocking_operator`, `line`, and `column`, copied from the `ChunkLocalDecision` that
  rejected the code. Callers that catch `ChunkPlanUnsupportedError` still catch it;
  callers that surface a warning read `to_payload()` instead of scraping the message.
- `IsolatedWorkerError` hierarchy (`_worker_isolation.py`) —
  `IsolatedWorkerStartError` (process failed to start), `IsolatedWorkerRemoteError`
  (child raised a Python exception; carries `remote_type`/`remote_message`/
  `remote_traceback`), `IsolatedWorkerCrashedError` (child exited without a result;
  reclassified to `terminal_reason="memory_limited"` when the exit code looks like
  `SIGKILL`/`SIGABRT` under a configured cap. `SIGABRT` is deliberately kept in
  the set — under `RLIMIT_AS` a native allocator that cannot allocate raises
  `bad_alloc` and aborts, so `SIGABRT` is the cap's primary out-of-memory
  signature and `SIGKILL`-only would misroute exactly the failures the cap
  exists to catch; the residual misdiagnosis vector (a native assertion or
  heap-corruption abort under a cap) is accepted because the wording hedges and
  the exit code is preserved. The message is parent-authored user-facing
  wording, since a crashed child left no payload to curate: a hedged
  may-have-run-out-of-memory phrasing for the reclassified case (the exit-code
  heuristic is indicative, not proof), an unexpected-stop phrasing otherwise,
  each carrying the exit code when available; the code also stays on the
  exception's `exitcode` attribute), `IsolatedWorkerTimeoutError` (parent
  terminated a child that exceeded its timeout; its message is likewise
  parent-authored user-facing wording — a stopped-after-its-time-limit phrasing
  naming the limit, which also stays on `timeout_seconds`),
  `IsolatedWorkerStoppedError` (parent-requested stop; raises `ValueError` if
  constructed with `terminal_reason="completed"`, which is not a valid stop reason),
  `IsolatedWorkerMemoryLimitUnsupportedError` (neither a usable address-space cap nor
  observable child RSS is available for a required limit),
  `IsolatedWorkerTerminationError` (the child remained alive after terminate
  and kill attempts), `IsolatedWorkerCleanupError` (one or more cleanup callbacks raised; attached
  via `add_note()` to a primary error rather than replacing it, or raised alone if
  the worker itself succeeded).
- Other generic `ValueError`/`TypeError`/`RuntimeError` are used for internal-invariant
  violations that should never occur given the calling contract (e.g. a node
  returning a non-Polars-frame type, a dataframe-cache key that doesn't match the
  current graph/policy, a missing sink output path) — these are not part of the
  typed-error surface external callers are expected to catch by type.

## Testing

- `tests/performance/test_polars_scale_scenario.py` — bounded Polars join/training projection scale generation, modelling-menu demand propagation, and CI-small execution-profile smoke contracts.
- `tests/performance/test_execution_engine_certification.py` — isolated projected-versus-full wide-Parquet RSS comparison, per-port API-input and direct-JSONL checkpoint evidence, a fresh-interpreter restart certificate for cache-proof reuse, telemetry privacy, and snapshot-owner cleanup, and `test_global_operation_memory_policies_match_the_registry`, which measures every global operation's incremental peak RSS in a fresh process through `tests/performance/_operation_memory_probe.py` and `bounded_sink` and certifies it against the policy read from `haute._polars_operations::operation` at runtime. Its 1.5M-row fact fixture and 375k-row dimension table are written with 25,000-row row groups — 60 row groups, more than any host's thread count — so parallel Parquet decoding cannot hold the whole file resident and the control measures streaming rather than the reader. Four controls are measured in the same run: `scan` (full-width passthrough sink), `scan_head` (a 1000-row sink), `scan_narrow` (a dense two-column sink) and `scan_gaps` (the same two columns where one is nullable and carries the gap runs). A control matches the operation's input columns *and* their nullability: a dense two-column scan under-represents the validity-bitmap and gap-handling cost of the same read, so measuring a nullable-column operator against it charges the operator for a read cost the control never paid. That is a correctness requirement for the comparison, not an allowance -- `interpolate` reads the nullable gap column and is therefore floored by `scan_gaps`, while a dense narrow plan keeps `scan_narrow`. Every `streaming` or `row_local` policy is bound by its matched passthrough control -- incremental peak <= 1.3x -- because a streaming pipeline can never need more than the passthrough pipeline over the same input (decode buffers plus output buffers, and a reducing operator's output buffers are smaller); a wide plan is bound by `scan` however few rows it emits, since its output size does not change what it must read. This is the safety-critical direction, since an operator wrongly recorded as streaming is one the planner never admits. `scan_head` is used only as the matched floor for a reducing boundary operator's witness. A `materialisation_boundary` policy is certified against the planner instead of a ratio: the same fixture is planned as a `dataInput` -> `polars` graph through `plan_execution_strategy` under an ample admission, and the admission estimate must bound the observed peak. The join graph declares `validate='m:1'` because that is the practice the product asks of an analyst and because it keeps the bound this join propagates downstream realistic; the join's own estimate is sized from its input ports and does not depend on the declaration. The join probe executes that same `validate='m:1'` code, so the measurement and the estimate describe one plan rather than two. Because a *declared* join estimate is sized from its largest operand, the lane also measures `join_fanout` -- `fact.join(multi, on='key', how='inner')` against an `operation-multi.parquet` fixture holding three rows per dimension key, so the output is three times the fact rows -- as a variant of `join` against the `scan` control. That measurement is what falsified input sizing for undeclared joins (about 1.57x the input-sized figure), so `join_fanout` is certified through the planner's policy rather than against a number: planned under `native_memory_backend_scope("rlimit")` it must be `warned`/`full-width-conservative` with `proof_gap=op:join_cardinality_many_to_many`, and planned without a cap it must raise `materialisation_estimate_unavailable` naming that detail. Only the rows check and those two policy checks are asserted; the evidence payload still records its `rows_out` against the expected 3x, the incremental peak, and `exceeds_declared_join_estimate` -- whether the observed fan-out peak is above the declared `join` case's estimate. Being a variant rather than a registry name, it carries no does-not-stream witness. A fan-in Polars node also carries the declared per-parent contract production requires. `explode` is certified as the typed unavailable-estimate rejection instead, its expansion being unbounded. `sort`, `unique`, `join`, and `explode` additionally carry a does-not-stream witness at 1.25x their matched floor. Two boundaries cannot be witnessed by their own wide-frame measurement and each names the variant that does show its state: `over` is dominated by the passthrough's own buffers on a 12-column frame, so the `over_narrow` probe must reach 1.5x `scan_narrow`, where its partition state dominates; and `join_asof` buffers its right (lookup) port and streams its left, so the wide-left case sits near the floor and the `join_asof_big_right` probe (`dim.join_asof(fact, ...)`, the large frame in the buffered position) must reach 1.25x `scan_head` — the wide case still certifies the estimate and the operator factor. `group_by`, `top_k`, `bottom_k`, and `reverse` are boundaries by construction or conservatism and carry no witness. The lane is registry-complete rather than a hand-kept list: it asserts that every name `measured_operation_names` returns has a plan in the probe (modulo the registered spelling aliases `groupby` and `melt`), so a new measured entry without a measurement fails here. `interpolate`'s probe reads a dedicated `v1_gaps` column — a straight line with 50-row null runs punched across the row-group boundaries — so the measurement cannot be of a passthrough over a column with nothing to fill, and the test verifies that the sunk output has no interior nulls left and that the filled values equal the linear interpolation of their neighbours. That verification runs in the *parent*, lazily over the retained sink after `run_smoke` has returned, precisely so it cannot contaminate the measurement: every child does nothing but build its plan, sink it, and exit, because anything else a child does lands in the lifetime peak the parent attributes to the operator. Reading the result inside the child once cost `interpolate` roughly 60 MiB and made a streaming operator look like a boundary. The `join_asof` fixtures are written pre-sorted for the same reason: a leading `sort` would make the chained boundary take sort's larger factor and certify the wrong operator. The cross join is certified through the planner alone — the graph plans `how='cross'` and must report the unavailable estimate — since there is no measurement to compare it against. Every ratio in the lane is a paired mean rather than a single reading: each operation is run alternately with its control, three of each -- five for `interpolate`, whose narrow-frame ratio sits at about 1.24 against the 1.30 ceiling, close enough that three pairs let batch drift decide the result -- and the ratio is the mean operation peak over the mean control peak. The per-operation sample count is recorded in the evidence payload. A control is re-run inside the pairs that use it and never sampled once and shared, because a single fresh-process sample drifted by about 20% between batches on the development host -- enough for a single-sample ratio to straddle a threshold and for a quoted figure to be noise rather than measurement. Pairing cancels that drift. The mean rather than the median is deliberate: peak RSS is bimodal, with samples clustering around two values about 35 MiB apart -- the granularity of a streaming chunk buffer, not continuous noise -- so a median of three snaps to whichever mode won two of the three samples and jumps between modes instead of settling, while the mean is the stable estimator of average cost over a discrete allocation pattern. Medians are still recorded for information. No threshold is widened to absorb the variance and no max-of-controls bias is applied. The residual is stated rather than hidden: a run can still fail when all three operation samples land in the high mode while all three control samples land in the low one, roughly a 1-in-64 event per operation, which is the accepted noise floor of an opt-in perf lane. The lane must be run through pytest, one fresh process per run: repeating the test inside a single interpreter reuses a warm page cache for the fixture and flatters every ratio, so an in-process repeat is not a valid measurement of this lane. This roughly triples the lane's runtime, to about 70 seconds, which is affordable for an opt-in perf marker. Polars version, thread count, row-group size, fixture rows, and every operation's rows out, all six paired samples, both means, both medians, the control it was paired against, the ratio, the estimate, and each check's outcome are recorded in the test's evidence payload, so the registry cannot claim a policy the measurements contradict.
- `tests/performance/_execution_resilience_probe.py` plus
  `test_execution_engine_certification.py` — fresh-interpreter worker-pool soak with
  real crash replacement and RSS/descriptor-or-handle plateau evidence; five-phase
  cache-publication crash recovery; `ENOSPC` rollback; multi-process same-cache
  contention; and metadata-only extreme-join rejection. The `ci`, `1m`, and `10m`
  scales use respectively bounded local counts, thousands of weekly executions, and
  ten thousand monthly executions with one thousand replacements.
- `scripts/run_perf_suite.py` — writes versioned JSON/Markdown/JUnit evidence and,
  when `--baseline-report` is supplied, validates one compatible retained report and
  applies configurable total-time, peak-RSS, and per-test regression thresholds plus
  a timing noise floor. Suite-wide metrics require identical test identity, while
  matching per-test comparisons survive ordinary suite additions. Baseline parse/schema
  errors are fatal; an environment or scale mismatch is retained as an explicit
  non-comparable result.
- `.github/workflows/performance.yml` — weekly one-million-row and monthly ten-million-row retained performance/resilience certificates, plus Linux/Windows/macOS process-kill, spill, cross-process cache, publication-crash, restart, native-memory, and lifecycle-plateau lanes. A successful scale-specific Python report is cached as the next historical baseline and retained as a workflow artifact used when the cache has expired; failed runs never replace either source. The baseline loader accepts only a timezone-stamped, successful, all-passed call-phase report whose collection/result counts, outcome totals, slowest-test projection, wall-time partition, RSS evidence, unique identities, and closed v4 test records reconcile exactly. Manual dispatch retains an explicit `ci|1m|10m` scale selector.
- `tests/test_bounded_collect_contracts.py` — bounded execution modules route collection through the streaming helper rather than direct Polars `.collect()`.
- `tests/test_builder_edge_cases.py` — builder edge cases for instance resolution, constants, outputs, live-switch/scenario expansion, banding, dispatch, and empty frames.
- `tests/test_column_renames.py` — column-rename application for configured, empty, missing, and edge-name mappings.
- `tests/test_compute_needed_columns.py` — topology, contract-algebra, and one-computation-per-node performance invariants for backward needed-column analysis.
- `tests/test_column_lineage.py` — operation-level schema/demand transfer, the
  row-effect bound of every accepted operation, the executable audit of the
  literal-string-argument method registry against the pinned Polars source, and
  differential execution checks: running supported programs against projected
  inputs must equal running the same programs against full-width inputs.
- `tests/test_cardinality.py` — overflow-safe join-cardinality formulas, uniqueness
  contracts, evidence payloads, invalid bounds, and row-cardinality lineage analysis.
- `tests/test_column_lineage_properties.py` — Hypothesis differential properties for the closed Polars column-lineage model, including projected-versus-full execution equivalence and row-count bounds that hold over empty, null-heavy, and NaN-heavy frames.
- `tests/test_polars_operations.py` — the operation registry's invariants (frozen, unique receiver-aware keys, import-time validation of class/policy/expansion, a boundary without a memory factor, and a streaming policy carrying one), the consistency of every analyser's derived vocabulary with the registry, the derived boundary sets (`materialising_frame_methods`, `materialising_expression_methods`), and representative snippets per class proving the chunk classifier and the lineage/cardinality analyser agree with the registered class.
- `tests/test_polars_compatibility_corpus.py` — the version-pinned compatibility corpus: `tests/polars_compatibility_corpus.json` records, for representative shapes across every maintained namespace, the classification they receive today (lineage support, cardinality availability, chunk eligibility, and the planner strategy under an admitted context) together with the pinned Polars version; the test fails on any difference, so a Polars upgrade or analyser change cannot silently turn a working shape into a rejection, and every change to the corpus is a reviewed edit of that file. A registry policy change regenerates the corpus, and only the operators whose evidence changed may move.
- `tests/test_interactive_route_isolation.py` — preview and trace routes execute serialisable production targets through the spawn-worker boundary.
- `tests/test_interactive_worker_pool.py` — warm interactive-worker pool readiness, protocol, timeout, cancellation, RSS-limit, replacement, and execution-mode contracts.
- `tests/test_native_memory_limit.py` — native-backend selection, strict-policy
  rejection, Linux/Windows aggregate enforcement, active-backend scoping, lease
  cleanup, and fork-safe ownership contracts.
- `tests/test_materialisation_calibration.py` — upward-only materialisation-estimate calibration, conservative rounding, profile isolation, and planner/admission integration.
- `tests/test_process_memory.py` — platform-dispatched RSS and liveness probes, including malformed, inaccessible, and Windows-handle cases.
- `tests/test_projection_aware_admission.py` — materialisation-boundary admission estimates use exact projected edge demand and preserve conservative fallback behaviour.
- `tests/test_projection_lineage_integration.py` — edge-identity and API-port
  integration of compositional lineage, terminal modelling schema propagation,
  and fail-visible ambiguous/unsupported boundaries.
- `tests/test_data_input_chunking.py` — Data Input provider snapshots and chunk-plan/runner execution, including unsupported chunk plans.
- `tests/test_extract_column_refs.py` — extraction of referenced columns across empty/minimal, selected/excluded, and node-config shapes.
- `tests/test_graph_input_identity.py` — edge-derived pipeline input-name derivation contract across source handles and graph edges.
- `tests/test_polars_backend_strategy_contract.py` — execution-strategy planning, boundedness/diagnostics payloads, projection/chunking, and error contracts, including the cross-profile table that plans each admitted Polars shape (row-preserving, row-reducing, bounded-expansion, and audited string/temporal predicates ahead of a group-by) under every `ExecutionProfile` with the real estimator and requires identical strategy diagnostics apart from the profile itself; it also proves that an unavailable estimate becomes the `warned` `full-width-conservative` strategy under an active native cap (`native_memory_backend_scope`) and the typed `materialisation_estimate_unavailable` rejection without one, with admission and headroom failures unchanged in both. It also covers every newly admitted boundary operator: `sort`, `unique`, `join`, `join_asof`, `top_k`, `bottom_k`, `reverse`, and an `over` inside `with_columns` each plan `materialisation-boundary` with a positive estimate and identical diagnostics on every profile; `explode` plans `warned` `full-width-conservative` under a native cap and rejects without one; and `unpivot`, `rolling`, `shift`, and `merge_sorted` plan no boundary at all.
- `tests/test_data_io_nodes.py` — sink execution and publication: the isolated output worker's admission release and failure classification, atomic staging/commit, overwrite and race handling, and the end-to-end group-by sink, including a conservative (`warned`) run whose written frame equals plain Polars and whose metrics payload carries the warned strategy.
- `tests/test_scenario_propagation.py` — active scenario propagation through routes, executor, builders, and live-switch pruning.
- `tests/test_streaming_collect_contract.py` — static contract that bounded callers use `streaming_collect` across execution/deploy/training/optimiser modules.

Tests live in `tests/` (flat layout, no package-per-component subdirectories).

- **`test_execute_lazy.py`** — the core suite: `_prune_live_switch_edges`, shared
  `PreparedExecution`/`NodeBoundaryRunner` preparation and routing parity,
  `_execute_lazy`, `_build_funcs`, `_execute_eager_core` (swallow
  vs. raise, timings, memory accounting), `_apply_selected_columns`, `EagerResult`
  shape, and full-versus-planned equivalence for every admitted boundary operator —
  a graph executed through the real admitted executor equals the plain lazy result on
  ordering (`sort`, `reverse`, `top_k`), schema, row multiplicity (`unique`, a
  duplicate-key `join`, `explode`), and both ports' retained columns for a join.
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
  including `validate_chunk_capability_declarations()`'s completeness check, and the
  classifier decision contract: comments and string literals cannot change
  eligibility, every rejection names its operator, closed reason, and source
  location, namespace admissions are receiver-specific, and a rejected polars node
  raises `ChunkUserCodeUnsupportedError` with that payload; every registered boundary operator in a chunk-local suffix is a `ChunkPlanUnsupportedError`, not only `group_by`.
  `test_chunk_plan.py` also pins that byte-budget planning over an OUTPUT target
  derives its row width from the derived document schema without ever entering
  `_assemble_document`, that a flat variable-width document column is sampled from
  the parent plan (the downstream-created wide-column guard still holds for an
  OUTPUT target), that a nested document column is a `ChunkPlanUnsupportedError`
  rather than an assembled sample or a nominal width, and that under a
  materialising operator before an explicit chunk start an OUTPUT target is
  rejected while the equivalent polars target keeps the nominal width.
- **`tests/test_output_schema_only.py`** — the schema-only OUTPUT contract: a
  tripwire over every document shape (flat, nested objects, one array level, two
  array levels, a multi-port parent with sibling child arrays) proving a
  schema-only execution neither collects nor assembles and reports exactly
  `output_document_schema(...)`; derivation fidelity against the schema Python
  inference produced from the assembled document on an inference-exact corpus;
  dtype fidelity over a wide dtype matrix, container leaves (`List`, `Struct`,
  `Array`) included (declaring the derived schema is rendering-neutral and every
  leaf keeps its source dtype, all-null columns and an empty source frame
  included); container-valued identities and relation keys (equal container
  values group into one object, and a child carrying the same container key is
  nested under its parent through the ancestor index and scoped lookup while an
  unmatched child is dropped); equality by construction between the collected
  and schema-only frames; and the typed rejections for a missing port, a
  missing column, and conflicting dtypes on one output path.
- **`test_chunk_runner.py`** — `iter_chunked_frames`/`run_chunked_reduce` execution,
  cancellation and checkpoint cleanup on failure.
- **`test_chunk_whitelist_proofs.py`** — the AST whitelist's correctness contract: de-
  whitelist regression pins for known silent-wrongness constructs, plus a
  `hypothesis`-driven property test per whitelisted construct
  (`test_whitelisted_construct_chunked_equals_full`) that runs the construct through
  the real chunk runner against full lazy execution on randomised, boundary-heavy
  frames (nulls/NaN/inf anywhere, empty strings and dates, single-row chunks),
  including every namespace-keyed `str`/`dt` admission and the literal-mapping
  `replace` shape; the inventory test fails when an allowlist entry has no proof or
  a proof cites a retired entry.
- **`test_streaming_chunk_size_threading.py`** — thread-local streaming chunk-size
  propagation used by the chunk runner and bounded-collect helpers.
- **`test_topo.py`**, **`test_topo_contracts.py`** — strict topological sort ordering,
  unknown-endpoint failure, explicit filtered-traversal evidence, cycle
  detection/reporting, and ancestor traversal.
- **`test_graph_utils.py`** — `_execute_lazy` re-export surface,
  `_sanitize_func_name`, `ancestors`/`topo_sort_ids`
  via the `graph_utils` facade.
- **`test_host_memory.py`** — RAM/VRAM probing across platforms: mocked
  probes for every source and failure path (driving the real ctypes
  structures), plus one darwin-gated unmocked real-kernel assertion.
- **`test_ram_estimate.py`** —
  source-metadata resolution (including edge-join key coalescing), the
  downsample decision, and the availability of a group-by estimate behind the
  proven row-preserving, row-reducing, bounded-expansion, and audited
  string/temporal shapes (with `unpivot` multiplying the resolved cardinality
  by its literal column count and an omitted `on` list staying unavailable). Per-port JSON `apiInput` sizing is exercised against a
  cache built by the same writer the engine reads — each emitted table sized
  from its own parquet, the committed layer used when working holds no match,
  and an unemitted port, a stale cache, an unusable path, a missing data file
  and an unreadable cache each reported unavailable rather than guessed; plus
  ancestor sizing drawing only the tables that feed the target. Both
  `unavailable_reason` values are pinned by identity, because the group-by
  rejection now quotes them back to the analyst. The boundary operator's
  `materialisation_factor_basis_points` is proved applied to the estimate and
  recorded in the assumptions alongside `boundary_operator`, and a join boundary
  is proved to sum both ports' widths. This module is under a
  critical coverage gate: estimates protect users from oversized runtime jobs,
  and an untested estimator is how a wrong number reaches a caller that treats
  "unknown" as "unlimited".
- **`test_boundary_operator_equivalence.py`** — full-versus-planned equivalence for every
  admitted boundary operator (sort, reverse, top_k, bottom_k, unique, join inner/left with
  duplicate keys and `validate='m:1'`, join_asof, over, explode under a native cap): each graph
  materialises the boundary mid-graph through the real lazy executor under admission, asserts
  the boundary was planned (`materialisation_boundaries` and `blocking_operator`), and compares
  with plain Polars on ordering (exact in-order equality for the order-defining operators),
  schema (names and dtypes), row multiplicity (heights and multiset equality after a
  deterministic sort), and multi-input column retention (both join ports' columns, suffixes
  included).
- **`tests/test_input_preparation.py`** — automatic preparation through the engine: a
  missing generation is built and a stale one refreshed before strategy planning (the
  estimator then reads the generation), a fresh one is reused without a build, schema-only
  executions never build (store build poisoned), the in-process path is taken under a
  declared native cap and the worker path otherwise (a fake spawn receiving the budget), a
  missing admitted context skips preparation (the node reports `input_snapshot_missing`),
  a warmed preview cache and a warmed dataframe cache both return the new rows after the
  source is rewritten, and the terminal payload's `input_preparation` records carry digests
  and counts only and validate through `ExecutionMetricsPayload`.
- **`test_worker_isolation.py`** — the shared `isolated_worker_failure_is_memory` predicate over every worker outcome; picklable-result round-trip, remote-exception
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

## Canonical execution interfaces

Under the [canonical-only format policy](../README.md#canonical-only-format-policy),
maintained execution call sites use the current typed planner, admission, runtime-input, and
diagnostic result objects directly. No private compatibility wrappers, tuple projections, or
test-only call shapes remain; tests exercise the maintained interfaces.

## Assistant interaction

`src/haute/assistant/_tools.py::get_node_schema` is a cross-component caller of
the public lazy-execution facade. It validates the target against the original
hierarchical graph, flattens submodels for execution, compiles the saved
preamble with the pipeline directory, selects the graph's saved active source,
and calls `execute_lazy_graph` with `target_node_id`, `preserve_node_ids`, and
contract enforcement. It reads only lazy schemas; a dict-shaped multi-frame
result is rendered per port and no frame is collected.

The assistant application service's v1 post-save verification tier is
`structural`: it reparses and validates the saved graph and evaluates closed
structural postconditions. Execution-plan verification is an explicitly
stronger future tier. This component does not own assistant project revisions,
plan hashes or save authority, and no assistant tool may present a structural
result as execution evidence.
