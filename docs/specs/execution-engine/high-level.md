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

  > NOTE: `_CheckpointAction.COLLECT_LAZY` is defined but `_checkpoint_decision()`
  > never returns it and the executor has no handler for it. The current strategy is
  > therefore `SKIP` or `PARQUET` only; in-memory `.collect().lazy()` checkpointing is
  > not implemented behaviour.
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
