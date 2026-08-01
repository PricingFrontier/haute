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
- Linux available-memory discovery clamps the host's reported availability to
  observable container headroom: cgroup v2 `memory.max - memory.current`,
  falling back to the v1 limit/usage pair when v2 is absent. An unlimited
  cgroup leaves the host value unchanged; malformed or incomplete controller
  state is reported and never turned into invented capacity.
- Shared config-driven node-apply logic (`_node_apply.py`) for `liveSwitch`,
  `scenarioExpander`, `optimiserApply`, and `OUTPUT` response-document assembly —
  the one implementation both the canvas executor and codegen-generated `.py`
  files call, so a saved pipeline's `pipeline.run()` behaves identically to the
  GUI. Before `assemble_output_from_config` unified the two, a saved `OUTPUT`
  node's generated code was a bare passthrough of the raw upstream frame instead
  of the assembled document.

**Out of scope** (owned elsewhere, linked where relevant):
- Static node schemas, sidecar validation, and registry configuration are owned by
  [pipeline-config](../pipeline-config/high-level.md). This component owns the
  per-`NodeType` runtime builder implementations registered in `NODE_REGISTRY` and
  the `NodeBuildHooks` interception seam, then orchestrates when and how the resulting
  callables run. The node's public configuration contract remains pipeline-config's
  responsibility.
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
- Assistant revision, risk, consent, mutation, and verification policy is owned by
  [assistant](../assistant/high-level.md). The assistant may ask this component to
  build a target-node lazy plan and read its schema, but v1 exposes no assistant
  preview, training, optimisation, deployment, external-write, or Git execution
  operation.
- Reading/writing the underlying file formats (parquet, CSV, Databricks tables) —
  [io-layer](../io-layer/high-level.md) and [databricks-io](../databricks-io/high-level.md).

## Behaviour

- **Preview execution** (`executor.execute_graph`) runs a graph eagerly up to an
  optional `target_node_id`, returning a `NodeResult` per executed node: status
  (`ok`/`error`), row/column counts, a bounded JSON preview of rows, schema, and
  per-node timing/memory. Repeated calls against the *same* graph reuse previously
  materialised node outputs from an in-process cache; calls that need more of the
  graph than is cached extend the cache rather than starting over. Ordinary
  node-local failures are captured per-node (`status="error"`) rather than
  aborting the whole preview. Once
  `_execute_eager_core` is running, every `HauteError` with a stable public
  `error_code`, cancellation, and memory-limit exhaustion is always raised. This includes
  `ContractResolutionError`,
  `ChunkMemoryRiskError`, `GroupByExecutionUnsupportedError`, and
  `LiveSwitchScenarioError`. `ContractMismatchError` and the base
  `SchemaMismatchError` have no public `error_code`, so the eager core re-raises
  both through one explicit mismatch branch. The preview HTTP adapter converts
  either mismatch into the same in-situ `PreviewNodeResponse(status="error")`
  instead of a generic 500. Preamble compilation happens outside that core:
  interactive preview attaches a `PreambleError` only to nodes that consume its
  namespace, while non-preview execution propagates it.
- **Sink/batch execution** (`executor.write_data_output`, `execution.execute_lazy_graph`)
  builds one Polars lazy plan for the whole graph (or up to a target node). Native
  sink-capable file formats use bounded Polars sinks and fail loudly if the plan cannot
  be sunk in streaming mode. A `dataOutput` format with only an eager writer, and
  database output, instead uses `streaming_collect` and therefore materialises the
  result DataFrame before writing; it still refuses Polars' non-streaming broad-collect
  fallback for bounded profiles.
- **`dataInput` and `dataOutput` are the sole tabular I/O node types.** A file-backed
  Parquet Data Input is scanned directly. Every other data input executes from a
  validated leased snapshot generation; graph execution never builds or refreshes a
  snapshot. Optional input code runs exactly once through `_exec_user_code` after
  provider resolution. Direct source signatures or snapshot generation pointers,
  together with source identity, mode, and code, participate in fingerprints without
  resolved secrets.
- **Output publication is explicit and contained.** `dataOutput` is preview
  pass-through; only `write_data_output` persists it. Local files stage to a unique
  contained same-filesystem sibling, validate completion, and replace atomically.
  Cancellation before commit removes staging; once an atomic/transactional commit
  succeeds, later cancellation or cleanup cannot claim rollback or erase success.
- **Every local runtime input is contained before eager or lazy execution.**
  `canonical_dataframe_execution_graph` normalizes separators, resolves symlinks,
  and rejects absolute/traversal/mixed-separator paths outside the execution root.
  The normal root is cwd; explicitly selecting an absolute pipeline outside cwd
  establishes only that pipeline's parent as its root. The eager/lazy cores also
  scope the final builder read to the same root. Named database/Databricks/provider
  identifiers are not local paths and retain their external-resource semantics.
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
- **Strategy decisions use one versioned public vocabulary.** The shared planner
  reports version 1 as `projected`, `schema-all-except`,
  `full-width-admitted-eager`, `unprojected-streaming-boundary`,
  `materialisation-boundary`, `unsupported`, or `not-planned`, with an exact API
  status, profile, boundedness, reason code, and available/unavailable/truncated
  detail state. Diagnostics are JSON-safe, deterministically capped, and never carry
  plans, frames, source values, or other user data.
- **Group-by is never smuggled through chunking or streaming.** Only
  `PREVIEW_EAGER` and `DEPLOY_LIVE` may use an admitted materialisation boundary,
  and only when a present positive admission plus an available estimate fits both
  memory limit and headroom. Every batch/bounded profile rejects before execution;
  missing admission, unavailable estimate, and excess headroom have distinct typed
  reason codes.
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
  `SchemaMismatchError`. Both propagate identically through the eager core and
  are adapted identically by the preview route.
- **Contract resolution is fail-loud outside interactive preview.** Only
  `PREVIEW_EAGER` may turn classified configuration/I/O/model-boundary resolution
  failures into a diagnosed opaque contract. Every non-preview profile, and an
  unprofiled low-level eager or lazy call, raises `ContractResolutionError` before
  node work. This policy is independent of projection/materialisation strictness.
- **Assistant schema inspection is plan-only.** `get_node_schema` performs the
  same flattening, preamble compilation, active-source selection, node building,
  and contract enforcement as production lazy execution up to the requested
  top-level node, then calls `collect_schema()` on the preserved lazy result.
  It never collects rows. Multi-frame results report one schema per output port.
  This is execution-plan evidence, not proof of row values or commercial
  correctness. The current assistant mutation service declares structural
  verification after save; it does not claim that this schema read ran for every
  mutation.
- **Assistant schema inspection is plan-only.** `get_node_schema` performs the
  same flattening, preamble compilation, active-source selection, node building,
  and contract enforcement as production lazy execution up to the requested
  top-level node, then calls `collect_schema()` on the preserved lazy result.
  It never collects rows. Multi-frame results report one schema per output port.
  This is execution-plan evidence, not proof of row values or commercial
  correctness. The current assistant mutation service declares structural
  verification after save; it does not claim that this schema read ran for every
  mutation.
- **Partitioned Parquet remains lazy and projected.** Directory-backed inputs retain
  Hive-partition predicates and required columns in the optimized scan, pruning
  irrelevant files/columns before checkpointing, caching, or response materialisation.
  Fan-in demand attribution comes only from operand/contract/join-key/schema evidence;
  ambiguous ownership is an observable boundary (or a strict-profile failure), never
  a guessed source assignment.
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
- **Operational evidence is deterministic and bounded.** Execution contexts expose
  request-local sequenced fault points at named collect/sink/checkpoint/reducer/
  response/terminal boundaries, record cancellation latency from the first request,
  and release cleanup callbacks in reverse order plus admission exactly once.
  Optional terminal telemetry is disabled by default; when enabled it emits at most
  one schema-versioned, allow-listed aggregate event per terminal status/reason,
  excluding identifiers, paths, columns, plans, messages, exception text, and user
  data. Telemetry failure cannot change execution status.

## Interactions

- [pipeline-config](../pipeline-config/high-level.md): owns node schemas, sidecar
  validation, and registry/configuration contracts. Execution-engine owns the runtime
  builder implementations and interception seam registered behind those contracts.
- [caching](../caching/high-level.md): the dataframe execution cache
  (`DataFrameExecutionCache`) that `_execute_lazy` seeds from and materialises into on
  a cache miss, plus `_cache.lineage_cache_key()`, which
  `execution.preview_lineage_cache_key()` uses with
  `PREVIEW_EXECUTION_SEMANTICS_VERSION` and the complete selected-lineage payload as
  the sole preview/trace cache identity.
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
  routes call into `executor.execute_graph`/`write_data_output` and construct
  `ExecutionContext`s via `_execution_admission.create_admitted_execution_context`.

## Failure model

- **Ordinary per-node failures during preview are swallowed and reported** —
  `execute_graph` returns a `NodeResult(status="error", error=...)` for the failing
  node (and every downstream node that depended on it) so one bad node doesn't blank
  the whole canvas. Any `HauteError` that opts into the public contract with a stable
  `error_code`, plus cancellation and memory-limit exhaustion, propagates from the
  eager core even in swallow mode because these are API-level correctness/resource
  signals. `ContractMismatchError` and `SchemaMismatchError` also propagate via
  the explicit mismatch branch; the preview route then presents either one as
  the target node's in-situ error response. Interactive preamble compilation
  retains the node-local handling described above.
- **Lazy (sink/batch/deploy) execution never swallows node failures** — any exception
  during plan construction or the final streaming collect propagates to the caller.
- **Contract mismatches are typed at the offending node.** Missing/extra columns raise
  `ContractMismatchError`, carrying the column diff and node id. A simple inferred
  join whose parent key dtypes differ raises `SchemaMismatchError`; both errors
  propagate on lazy/fail-fast and swallow-mode eager execution. The preview HTTP
  boundary converts either into the same `NodeResult(status="error")` shape.
- **Memory-budget exhaustion raises `ExecutionMemoryLimitExceededError`** (a
  `MemoryError` subclass) at the next checkpoint after RSS crosses the resolved
  budget. A sampler that becomes unavailable mid-run raises the same typed error with
  `reason="memory_sampler_unavailable"` rather than silently disabling enforcement;
  **admission is refused up front** with `ExecutionAdmissionError` if a
  current-RSS sample is unavailable, the process is already over its RSS cap, or a
  heavy profile's process-wide in-flight reservation is exhausted before the run
  starts. These guarantees apply only
  when the caller uses an admitted/limited context; direct unbounded contexts have no
  RSS limit to exceed.
- **Invalid admission configuration raises `RuntimeError` before admission.** An
  unknown `HAUTE_EXECUTION_MEMORY_POLICY`, malformed/non-positive memory/RSS limit,
  or invalid reserve setting is configuration failure, not an
  `ExecutionAdmissionError`; no context is created. Numeric admission overrides are
  parsed from one environment read before local unit conversion.
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
- **Worker memory enforcement has a closed policy.**
  `HAUTE_WORKER_MEMORY_ENFORCEMENT=best_effort|required`; unknown values and missing
  required limits fail loudly. Required mode rejects before spawn when a hard process
  cap is unavailable, while best-effort retains process isolation and in-child
  admission/RSS checkpoints without misrepresenting them as an OS hard cap.
- **RAM estimation degrades to "unknown" rather than guessing.** When source row
  counts or column schema cannot be determined from parquet metadata (Databricks
  sources, JSON-shape apiInput caches), `estimate_safe_training_rows` returns
  `safe_row_limit=None` / `total_rows=None` — the caller proceeds without a downsample
  rather than receiving a fabricated number.
