# Execution Engine — High-Level Specification

## Purpose

A Haute pipeline is a graph of typed nodes (sources, transforms, model-scoring steps,
sinks) built either interactively in the GUI canvas or loaded from a saved `.py`
pipeline file. Something has to turn that graph into actual computation: decide an
execution order, decide whether to run eagerly (materialising DataFrames as it goes,
for instant GUI feedback) or lazily (building one Polars query plan Polars can
optimise end-to-end, for batch/deploy throughput), decide when intermediate results
need to be checkpointed to disk to avoid re-computing shared upstream work, and stop a
run before it exhausts the host's memory rather than letting the OS kill the process.

The execution engine is that layer. It owns graph traversal (topological ordering,
ancestor/cycle detection), the two execution strategies (eager-with-caching for
interactive preview, lazy-with-parquet-checkpointing for sinks/scoring/training), the shared
per-node building block both strategies call through (`_build_funcs`), column-contract
enforcement at node boundaries, execution contexts that route/service call sites admit
with profile-specific memory budgets, a bounded chunked map-reduce mode for datasets too
large to hold as a single Polars plan, and a small process-isolation primitive for
running heavy work in a child process the parent can kill on timeout or memory limit.

## Scope

**In scope:**
- Turning a `PipelineGraph` (nodes + edges) into an executable order and running it,
  both eagerly (`executor.execute_graph`, `_execute_lazy._execute_eager_core`) and
  lazily (`execution.execute_lazy_graph`, `_execute_lazy._execute_lazy`).
- The GUI preview cache — reusing materialised node outputs across clicks on the same
  graph, extending the cache when a new target requires more of the graph, and
  discarding entries once source data or graph shape changes.
- Column-contract enforcement at node input/output boundaries (via `_contracts.py`'s
  `Contract`, consumed here) and the column-projection planning that lets checkpoints
  and lazy scans avoid materialising unused columns.
- Structural parquet checkpointing of the lazy plan at fan-in/fan-out/join-feeder
  boundaries when the caller supplies a checkpoint directory (the normal sink path
  does) to bound Polars plan duplication.
- `ExecutionContext`/`ExecutionProfile`: the per-run cancellation token, optional
  memory budget, stage-timing/RSS-sampling instrumentation, and admission control used
  by route/service long-running operations (preview, sink, training prep, optimiser
  setup, deploy, chunked map-reduce). Low-level callers may omit a context or construct
  one without admission limits.
- Bounded chunked execution (`chunking.py`): proving a graph's suffix is safe to run
  chunk-by-chunk (an AST whitelist for user Polars code, per-`NodeType` capability
  declarations), sizing chunks, and streaming chunk batches through the same node
  builder functions the eager/lazy paths use.
- Process isolation (`_worker_isolation.py`) for running a function in a spawned child
  process with an optional address-space cap, timeout, and cooperative-stop support.
- Metadata-based RAM pre-estimation for training (`_ram_estimate.py`) so a training run
  can downsample before it starts rather than OOM mid-fit.
- Shared config-driven node-apply logic (`_node_apply.py`) for `liveSwitch`,
  `scenarioExpander`, `optimiserApply`, and `OUTPUT` response-document assembly —
  the one implementation both the canvas executor and codegen-generated `.py`
  files call, so a saved pipeline's `pipeline.run()` behaves identically to the
  GUI. Before `assemble_output_from_config` unified the two, a saved `OUTPUT`
  node's generated code was a bare passthrough of the raw upstream frame instead
  of the assembled document.

**Out of scope** (owned elsewhere, linked where relevant):
- *What* each node type computes (reading a data source, applying a banding table,
  scoring a model) — that is the per-`NodeType` builder registered in `NODE_REGISTRY`,
  owned by [pipeline-config](../pipeline-config/high-level.md). This component only
  calls the builder-supplied callable (`build_node_fn`) and orchestrates *when* and
  *how* it runs.
- Whether a materialised DataFrame or lazy scan is reused across calls without
  recomputation — the dataframe execution cache's storage/eviction/fingerprinting
  policy belongs to [caching](../caching/high-level.md); this component only decides
  *what* to cache and consumes the cache's `get`/`scan`/`materialize` API.
- Sandboxing of user-written Polars/Python snippets (`code` config fields) — the
  actual restricted-`exec` mechanism is [sandbox-security](../sandbox-security/high-level.md);
  this component calls it at preamble-compile and node-build time.
- Correlating a completed run into a human-readable trace/waterfall — that is
  [tracing](../tracing/high-level.md), which is built on top of the same
  `_execute_eager_core`/`ExecutionContext` primitives this component exposes.
- HTTP request/response shapes and route wiring for preview/run/sink/train endpoints —
  [server-api](../server-api/high-level.md).
- Reading/writing the underlying file formats (parquet, CSV, Databricks tables) —
  [io-layer](../io-layer/high-level.md) and [databricks-io](../databricks-io/high-level.md).

## Behaviour

- **Preview execution** (`executor.execute_graph`) runs a graph eagerly up to an
  optional `target_node_id`, returning a `NodeResult` per executed node: status
  (`ok`/`error`), row/column counts, a bounded JSON preview of rows, schema, and
  per-node timing/memory. Repeated calls against the *same* graph reuse previously
  materialised node outputs from an in-process cache; calls that need more of the
  graph than is cached extend the cache rather than starting over. Node failures are
  captured per-node (`status="error"`) rather than aborting the whole preview, except
  for `ContractMismatchError`, cancellation, and memory-limit exhaustion, which are
  always raised. A join-key dtype `SchemaMismatchError` is currently captured as a
  node error on this swallow-errors path (but propagates from lazy/fail-fast execution).
- **Sink/batch execution** (`executor.execute_sink`, `execution.execute_lazy_graph`)
  builds one Polars lazy plan for the whole graph (or up to a target node). Native
  sink-capable file formats use bounded Polars sinks and fail loudly if the plan cannot
  be sunk in streaming mode. A `dataOutput` format with only an eager writer, and
  database output, instead uses `streaming_collect` and therefore materialises the
  result DataFrame before writing; it still refuses Polars' non-streaming broad-collect
  fallback for bounded profiles.
- **Chunked map-reduce execution** (`chunking.chunk_plan` / `iter_chunked_frames`)
  proves, ahead of running anything, that a graph's tail from a chosen `chunk_start`
  node to the target is chunk-safe — a single-parent chain of node types whose
  transforms are provably row-local — and then streams bounded batches through it,
  never holding more than one chunk's worth of intermediate rows. Any node or user-code
  construct outside the proven-safe set fails chunk *planning*, not execution, so
  callers can fall back to the always-correct full executor. The shipped runner is
  deliberately serial (`max_in_flight_chunks=1`); it does not execute chunks in
  parallel. A non-root `chunk_start_node_id` requires the caller to supply a
  `start_frame`; the runner bounds the suffix from that frame onward and does not
  claim that producing or retaining the caller-owned start frame was bounded.
- Route/service long-running operations create an admitted `ExecutionContext` bound
  to an `ExecutionProfile` (preview, lazy sink, training prep, optimiser setup, deploy
  live/batch, chunked map-reduce, ...). An admitted context enforces a resident-memory
  growth budget resolved from that profile (fixed default, environment override, or
  an adaptive fraction of currently-available system RAM), samples RSS at stage
  boundaries, and raises after a sampled boundary crosses the limit. Low-level APIs
  also accept `None` or a directly-constructed context; those direct/test/library
  calls are not memory-admitted unless the caller supplies limits.
- A cancellation token threaded through the same context lets a caller (e.g. a
  background-job supervisor) stop a run cooperatively between stages; the engine
  checks it at every checkpoint rather than polling continuously.
- Column contracts are enforced at both input and output boundaries of every node
  whose builder declares a concrete (non-opaque) contract, on both the eager and lazy
  paths, so a mismatch (missing column, wrong dtype on a join key) is detected at the
  offending node rather than as an opaque Polars error three nodes later. Missing
  columns use `ContractMismatchError`; join-key dtype disagreement uses
  `SchemaMismatchError`, with the eager-preview reporting asymmetry noted above.
- **Input identity is 1:1 and edge-derived.** Every incoming edge of a node has
  exactly one *input name*, derived by `edge_input_name` (`_graph_utils.py`): an
  `apiInput`-frame edge's name is its frame label verbatim (frame labels are
  validated as ASCII Python identifiers by the api-input schema); every other edge's name
  is the sanitised source-node label. That name is simultaneously the name listed in
  the editor, the generated function's parameter, and the key used by
  name-referencing configs (`input_scenario_map`, instance `inputMapping`,
  `config["inputs"]`) — there are no hidden names, positional suffixes, or
  display-vs-executable mappings. Two incoming edges deriving the same input name on
  one node are a loud validation error, never silently suffixed. Binding remains
  positional in mechanism, but because each name derives from its own edge, edge
  reordering can never re-mean a name.

## Design rationale

- **Two execution strategies, one shared node-building step.** Eager execution
  (`_execute_eager_core`) and lazy execution (`_execute_lazy`) both call
  `_build_funcs`, which asks each node's `NODE_REGISTRY` builder for the same
  `(name, callable, is_source)` triple. This is deliberate: the GUI preview and a
  batch sink run *the same per-node logic*, differing only in when the result is
  collected. Divergence between "what preview shows" and "what the batch run
  produces" would otherwise be a permanent trust problem for users.
- **Eager-with-caching for interactivity, lazy-with-checkpointing for throughput.**
  Interactive preview needs low click-to-result latency on the *same* graph across
  many small edits — caching materialised DataFrames keyed by a graph fingerprint
  wins. Batch/deploy/training need to process rows that may not fit in memory at all —
  Polars' lazy engine, with the executor breaking join-chain plan duplication via
  periodic parquet checkpoints, wins there instead. Running one
  strategy for both would either make preview too slow (rebuild the whole plan per
  click) or make batch runs memory-unsafe (materialise everything eagerly).
- **Parquet checkpointing only at structural fan-in/fan-out/join-feeder points.**
  Polars duplicates the upstream plan for every downstream branch of a lazy frame
  (a known upstream limitation — pola-rs/polars#24206); checkpointing *every* node
  would erase the benefit of staying lazy at all, so the engine only checkpoints where
  a node has more than one parent, more than one child, or feeds a join. The
  decision is acted on only when `checkpoint_dir` is non-`None`; direct lazy callers
  may intentionally omit checkpointing.

  The checkpoint action set is intentionally `SKIP` or `PARQUET` only;
  in-memory `.collect().lazy()` checkpointing is not supported behaviour.
- **Profile-scoped memory budgets, not one global limit.** A preview click and a
  10M-row training run have wildly different acceptable memory footprints and
  latency expectations. `ExecutionProfile` lets each call site (preview route,
  training service, optimiser service, chunked runner) get its own default budget,
  its own environment-variable override, and — for the "heavy" batch-shaped profiles —
  a process-wide in-flight reservation so several concurrent heavy jobs cannot each
  assume the full adaptive budget and collectively overrun the host.
- **Chunk safety is proven, not assumed.** Silent-wrongness is worse than a hard
  failure: a `fill_null(strategy="forward")` or `is_in(full_column)` inside chunked
  user code would produce *different, wrong* numbers per chunk boundary rather than an
  error. `chunking.py` only admits constructs it has a hypothesis-based proof for
  (`test_chunk_whitelist_proofs.py`: chunked output == full-execution output on
  randomised boundary-heavy frames); anything else fails chunk planning loudly and the
  caller falls back to the always-correct full executor.
- **Process isolation via `spawn`, not `fork`.** The child worker in
  `_worker_isolation.py` is started with `multiprocessing`'s `spawn` context
  specifically so it does not inherit the parent's already-large native heaps
  (Polars/Arrow buffers, loaded models) — a `fork`'d child would start already near
  its own memory limit.
- **Metadata-only RAM estimation, not a sample run.** An earlier probe-based approach
  ran a 1,000-row sample through the pipeline before training; inner joins with no key
  overlap in a small sample produced zero rows and broke the estimate. Reading
  parquet footer metadata (row/column counts, per-column uncompressed size) is
  instant and always available, at the cost of being an estimate rather than an exact
  measurement — hence a 3× empirical overhead multiplier and a configurable safety
  factor rather than a computed exact figure.

## Interactions

- [pipeline-config](../pipeline-config/high-level.md): supplies `NODE_REGISTRY` and
  the per-`NodeType` builder callables (`build_node_fn`) that `_build_funcs` invokes;
  this component owns none of the per-node computation, only the traversal and
  materialisation strategy around it.
- [caching](../caching/high-level.md): the dataframe execution cache
  (`DataFrameExecutionCache`) that `_execute_lazy` seeds from and materialises into on
  a cache miss; the graph-fingerprint helpers this component's preview cache and
  `execution.py`'s fingerprint functions build on.
- [sandbox-security](../sandbox-security/high-level.md): `executor._compile_preamble`
  and node builders execute user-written preamble/transform code through the sandbox's
  restricted-globals `exec`.
- [tracing](../tracing/high-level.md): built directly on `_execute_eager_core` and
  `ExecutionContext`'s stage/checkpoint instrumentation to reconstruct a run's
  timeline; shares the preview cache's fingerprint shape so a trace can reuse
  preview-cached frames.
- [io-layer](../io-layer/high-level.md) / [databricks-io](../databricks-io/high-level.md):
  supply the actual scan/read functions that source-node builders call; this
  component only decides when a source is re-read vs. reused.
- [server-api](../server-api/high-level.md): the FastAPI preview/run/sink/train
  routes call into `executor.execute_graph`/`execute_sink` and construct
  `ExecutionContext`s via `_execution_admission.create_admitted_execution_context`.

## Failure model

- **Per-node failures during preview are swallowed and reported, not raised** —
  `execute_graph` returns a `NodeResult(status="error", error=...)` for the failing
  node (and every downstream node that depended on it) so one bad node doesn't blank
  the whole canvas. `ContractMismatchError`, cancellation, and memory-limit exhaustion
  are the deliberate exceptions: these propagate even in swallow mode because they
  are API-level correctness/resource signals. A join-key dtype `SchemaMismatchError`
  does not share that exception clause today and is returned as a node error.
- **Lazy (sink/batch/deploy) execution never swallows node failures** — any exception
  during plan construction or the final streaming collect propagates to the caller.
- **Contract mismatches are typed at the offending node.** Missing/extra columns raise
  `ContractMismatchError`, carrying the column diff and node id. A simple inferred
  join whose parent key dtypes differ raises `SchemaMismatchError`; it propagates on
  lazy/fail-fast execution but is captured into a `NodeResult(status="error")` by
  ordinary eager preview.
- **Memory-budget exhaustion raises `ExecutionMemoryLimitExceededError`** (a
  `MemoryError` subclass) at the next checkpoint after RSS crosses the resolved
  budget; **admission is refused up front** with `ExecutionAdmissionError` if a
  current-RSS sample is unavailable, the process is already over its RSS cap, or a
  heavy profile's process-wide in-flight reservation is exhausted before the run
  starts. These guarantees apply only
  when the caller uses an admitted/limited context; direct unbounded contexts have no
  RSS limit to exceed.
- **Invalid admission configuration raises `RuntimeError` before admission.** An
  unknown `HAUTE_EXECUTION_MEMORY_POLICY`, malformed/non-positive memory/RSS limit,
  or invalid reserve setting is configuration failure, not an
  `ExecutionAdmissionError`; no context is created.
- **Cancellation raises `ExecutionCancelledError`** at the next checkpoint once a
  context's cancellation token has been set; the engine does not poll independently,
  so cancellation latency is bounded by the distance between checkpoints, not
  instantaneous.
- **Chunk planning fails loudly, never silently downgrades.** Any node type, user-code
  construct, or graph shape the chunk contract does not have a proof for raises
  `ChunkPlanUnsupportedError` at *plan* time; there is no silent fallback to full
  materialisation inside the chunk runner itself — callers choose the full executor
  explicitly.
- **Isolated-worker failures are reclassified into typed errors** rather than leaking
  raw `multiprocessing` exit codes: a remote Python exception becomes
  `IsolatedWorkerRemoteError`, a process that exits without a result payload becomes
  `IsolatedWorkerCrashedError` (with a `terminal_reason="memory_limited"` guess when
  the exit code looks like `SIGKILL`/`SIGABRT` under a configured memory cap), a
  timeout becomes `IsolatedWorkerTimeoutError`, and parent-owned cleanup callback
  failures are collected into `IsolatedWorkerCleanupError`. A cleanup-only failure is
  raised; when there is already a primary worker failure, cleanup detail is attached
  to it with `add_note()` rather than replacing it or raising a second exception.
- **RAM estimation degrades to "unknown" rather than guessing.** When source row
  counts or column schema cannot be determined from parquet metadata (Databricks
  sources, JSON-shape apiInput caches), `estimate_safe_training_rows` returns
  `safe_row_limit=None` / `total_rows=None` — the caller proceeds without a downsample
  rather than receiving a fabricated number.

## Polars backend contracts (0.6.0)

This is an approved spec-first change. The implementation plan is
[F_0.6.0_polars-backend-remediation.plan.md](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md).

### Current limitations

Execution strategy selection is projection-centred but does not yet provide one stable,
versioned vocabulary at the `haute.execution` boundary, nor cover every execution entry
point when no projection seed is available. Strategy diagnostics do not yet consistently
describe boundedness, boundaries, cost, and feature provenance. Chunk planning rejects
group-by implicitly through capability limits rather than exposing a deliberate execution
boundary/rejection contract. Several execution paths also retain avoidable overhead or
ambiguous operational behaviour: Windows RSS sampler setup is repeated, eager diamonds
may cache consumers rather than their shared producer, RAM estimation can suppress
unexpected failures or repeat work, and selected context/lifecycle paths have inconsistent
timing, error, and cleanup semantics.

### Approved target behaviour

- `haute.execution` shall expose the sole projection-owned, stable, versioned strategy
  vocabulary. Version 1 has the internal strategies `projected`, `schema-all-except`,
  `full-width-admitted-eager`, `unprojected-streaming-boundary`,
  `materialisation-boundary`, `unsupported`, and `not-planned`. Existing consumers shall
  migrate to that facade rather than carrying parallel strategy representations.
- Every strategy result shall contain integer `schema_version=1`, API `status`, internal
  `strategy`, `profile`, `boundedness` (`bounded`, `unbounded`, or `unknown`), stable
  `reason_code`, and `detail_state` (`available`, `unavailable`, or `truncated`). Optional
  blocking/remediation, cost, metric, and provenance detail is bounded and contains no
  plans, frames, or user data. Boundary and reason collections are capped at 32 entries,
  provenance at 128 entries, and human messages/remediation at 512 characters, with
  deterministic truncation.
- The version-1 strategy-to-status mapping is authoritative: `projected` and
  `schema-all-except` map to `projected`; `full-width-admitted-eager` maps to
  `admitted_eager`; both boundary strategies map to `boundary`; `unsupported` maps to
  `rejected`; and `not-planned` maps to `not_planned`.
- The execution planner shall cover projection-seeded and seedless preview and deploy-live
  entry points. When no safe bounded plan is available, the result shall explicitly describe
  the admitted eager/materialised boundary or raise the applicable typed unsupported error;
  it shall not silently claim bounded execution.
- Group-by version 1 is never chunked and follows this authoritative profile matrix:

  | `ExecutionProfile` | Version-1 group-by outcome |
  | --- | --- |
  | `PREVIEW_EAGER` | `materialisation-boundary` only after the admission and estimate checks below; otherwise typed rejection |
  | `DEPLOY_LIVE` | `materialisation-boundary` only after the admission and estimate checks below; otherwise typed rejection |
  | `LAZY_SINK` | reject with `profile_requires_bounded_execution` regardless of estimate |
  | `TRAINING_PREP` | reject with `profile_requires_bounded_execution` regardless of estimate |
  | `OPTIMISER_SETUP` | reject with `profile_requires_bounded_execution` regardless of estimate |
  | `EXPLORE_ANALYSIS` | reject with `profile_requires_bounded_execution` regardless of estimate |
  | `AUTO_RANGE` | reject with `profile_requires_bounded_execution` regardless of estimate |
  | `DEPLOY_BATCH` | reject with `profile_requires_bounded_execution` regardless of estimate |
  | `CHUNKED_MAP_REDUCE` | reject with `profile_requires_bounded_execution` regardless of estimate |

  For `PREVIEW_EAGER` and `DEPLOY_LIVE`, a boundary is admitted only when an
  `ExecutionContext` with an admission exists, both `admission.memory_limit_bytes` and
  `admission.headroom_bytes` are positive, the `MaterialisationEstimate` has
  `state=available`, and `estimated_peak_bytes <= min(admission.memory_limit_bytes,
  admission.headroom_bytes)`. Equality is admitted. A missing context/admission or
  non-positive admission value rejects with `execution_admission_unavailable`; an
  unavailable estimate rejects with `materialisation_estimate_unavailable`; and an
  available estimate above effective headroom rejects with
  `materialisation_exceeds_headroom`. Every rejection raises
  `GroupByExecutionUnsupportedError`, a `BoundedMemoryUnsupportedError`, before execution,
  with stable fields `node_id`, `operator`, `profile`, `reason_code`, `remediation`, nullable
  `estimated_peak_bytes`, and nullable `headroom_bytes`. Nullable fields are populated when
  their values are known at decision time.
- Group-by must never be represented as ordinary checked execution or an
  `unprojected-streaming-boundary`, and has no streaming or chunk fallback. Chunk-local
  partial/final reducers are not part of this change. The implementation plan's P1 group-by
  integration depends on the P4 `MaterialisationEstimate` work: P1 may establish the typed
  rejection surface first, but its admitted boundary must not ship until P4 supplies the
  estimate contract.
- Execution diagnostics shall remain bounded in size and safe to expose to callers; they
  shall identify the decisive unsupported/opaque feature and any materialisation boundary
  without embedding unbounded plans, frames, or user data.
- Windows RSS sampling shall memoise process/DLL bindings per factory object identity,
  without changing sampling failure semantics, and expose an explicit reset seam. The
  cache must initialise once under concurrent access for one identity and initialise a new
  binding after a factory switch. Eager diamond execution shall create one cached lazy-plan
  node at the common producer and share that same `LazyFrame` with dependent branches; it
  must not add an eager collection or wrap an already-materialised `DataFrame`.
- RAM estimation shall distinguish unavailable metadata from unexpected failures (which
  propagate), memoise metadata/schema resolution for a single estimate, and account
  conservatively for variable-width string columns. P4 shall expose an explicit
  `MaterialisationEstimate` with `state=available|unavailable`: an available estimate has a
  non-negative integer `estimated_peak_bytes`, while an unavailable estimate has
  `estimated_peak_bytes=None`. Zero bytes is a legitimate available estimate for empty input
  and must never be overloaded to mean unknown. The estimator shall not fabricate a safe
  row limit.
- Execution-owned lifecycle semantics shall report stage timings in milliseconds,
  raise `LiveSwitchScenarioError` (an `ExecutionError`) with a stable code and named fields
  for invalid `liveSwitch` selection, and release every
  acquired admission reservation on every terminal path. FR33's eager contract-cache miss
  behaviour is already delivered and is not reopened by this change.
- The dormant `COLLECT_LAZY` action and its unreachable guard are absent. Duplicate
  execution predicates and measured execution hot-path cleanup are consolidated without
  changing supported semantics.

### Non-goals and compatibility

- This change does not add chunked group-by reducers, alter node computation semantics,
  introduce broad best-effort fallbacks, or promise that all Polars operations are bounded.
- Before 1.0, 0.6 intentionally replaces unsafe silent fallback with typed failure. Existing
  version-1 consumers may ignore unknown additive fields, but missing/malformed required
  fields, unknown version-1 enum values, and unsupported higher schema versions are invalid.
  Release notes and a migration note are required; no compatibility shim may preserve the
  unsafe group-by or live-switch behaviour.
- Performance cleanups require representative benchmarks; unmeasured speculative Review-P11 changes
  are not required merely because they appear in the review.

### Acceptance evidence

- Contract tests prove all public execution entry points consume the same versioned strategy
  result, including seedless preview and deploy-live paths, enforce the authoritative mapping,
  version rules and deterministic caps, and keep diagnostics data-free and size-bounded.
- Table-driven scenario tests cover every `ExecutionProfile` and every group-by reason code,
  and prove the sole admitted result is a `materialisation-boundary`. For both eligible
  profiles they cover missing context, missing/invalid admission, unavailable estimate,
  empty-input zero estimate, estimate below headroom, estimate equal to headroom, and
  estimate over headroom. No test may route group-by through streaming, chunking, ordinary
  checked execution, or an unprojected streaming boundary.
- Focused tests prove sampler initialisation is once per factory identity under concurrency,
  switching factories creates a fresh binding, the reset seam works, one shared producer
  materialisation across an eager diamond, RAM-estimate
  unknown/fail-loud/memoisation/string-width behaviour, millisecond timing, typed live-switch
  mapping failure, and reservation release after success,
  cancellation, and exceptions.
- The relevant execution, projection, RAM-estimate, admission/context, eager/lazy, and
  deploy/preview test suites pass; benchmark evidence accompanies any Review-P11 optimisation claim.

## Approved change contract — 0.7.0 unified data I/O execution

Implementation follows
[`F_0.7.0_data-io-convergence.plan.md`](../../trip/plans/F_0.7.0_data-io-convergence.plan.md)
and the approved [I/O contract](../io-layer/high-level.md#approved-change-contract-070-data-io-convergence).

- `dataInput` is the sole tabular source type understood by eager, lazy, projected, chunked,
  tracing, optimiser, RAM-estimation, and deployment execution paths. A pipeline may have
  multiple inputs; source discovery and explicit `chunk_start_node_id` retain their existing
  multi-source semantics. `dataSource` is removed rather than aliased.
- A data input executes either directly through its provider or from a validated shared Parquet
  snapshot. Runtime execution never builds or refreshes a snapshot. Database and Databricks
  inputs without a ready matching snapshot fail before the graph runs; local-file/lakehouse
  direct reads follow their declared capability.
- The optional input Polars body is applied after source resolution. It participates in column
  contracts, projection, fingerprints, tracing, and codegen exactly once. Chunk planning accepts
  it only when the shared AST proof establishes row-local semantics; it never treats whole-frame
  code as independently correct on each batch.
- Chunk-source selection is provider/format capability-driven. A valid direct-batch source or
  valid cached Parquet generation feeds `bounded_collect_batches`; there is no CSV/Parquet
  extension switch. Capability is opt-in and versioned. A scanner-backed format is still rejected
  until format-specific evidence proves bounded ordered batches with the pinned Polars version.
- Strategy diagnostics distinguish direct scan, cached scan, bounded snapshot build (reported by
  the cache job rather than graph execution), admitted-eager build, native output sink, eager
  writer, and unsupported boundaries. “Cached” never implies externally fresh and “Parquet”
  never implies the cache build was bounded.
- `dataOutput` is the sole persistence target and remains a preview pass-through. Only the
  explicit write endpoint runs it. Native sink formats preserve bounded lazy execution;
  writer-only formats require materialisation admission. Local single-file publication is staged
  and atomic. Database/lakehouse publication reports its transactional semantics. `dataSink` is
  removed rather than routed through a compatibility branch.

The hard cutover deletes legacy node branches from graph path resolution, source enumeration,
projection coverage, RAM estimation, chunk declarations, executor builders, sink dispatch,
tracing/deploy hooks, and tests. Acceptance covers each execution strategy with direct and cached
inputs, multiple roots, row-local and rejected global code, stale/missing/corrupt snapshots,
format-capability mismatch, native/eager output modes, cancellation/admission, and assertions
that graph execution causes no cache-build or remote-provider call.
