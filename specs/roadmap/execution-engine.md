# Execution engine roadmap

## Scope

Graph execution: the eager and lazy cores, the chunked runner, execution
contexts and admission, process-memory observation, and graph traversal.
Current behaviour is specified in
[the execution-engine specification](../execution-engine/high-level.md).
These packages come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md), which
found several generations of machinery for the same concern side by side:
two thousand-line execution cores, a chunked runner with one consumer, a
static memory prover beside the hard worker caps, and one process-wide lock
that serialises unrelated optimiser work.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXEC-R01 | Planned | P2 | The streaming chunk size is set once per process, so no request holds a lock across user work. |
| EXEC-R03 | Planned | P3 | The chunked map-reduce planner and runner are removed with their only consumer. |
| EXEC-R04 | Decision | P2 | One memory-safety mechanism: hard-capped workers, or static proofs where no cap exists. |
| EXEC-R05 | Planned | P2 | One graph walker builds every execution; eager, preview, trace and scoring differ only in their collect policy. |
| EXEC-R06 | Planned | P3 | Process and host memory are read by one module. |
| EXEC-R07 | Planned | P3 | `ExecutionContext` is split into cancellation, admission, and evidence parts. |
| EXEC-R08 | Planned | P3 | Graph traversal has one implementation. |

## Planned improvements

`EXEC-R01` goes first because it removes a user-visible stall.
`EXEC-R03` follows the optimiser's `OPT-P15` if that package removes the
runner's only consumer. `EXEC-R04` is a decision that `EXEC-R05` should wait
for, because the walker's shape depends on whether projection proofs survive;
`EXEC-R05` needs only the recorded decision, not its implementation.
`EXEC-R06`–`EXEC-R08` are independent and can be taken whenever their files
are next open.

### EXEC-R01 — The streaming chunk size is process configuration
**Why:** `temporary_streaming_chunk_size` takes the module-level
`_STREAMING_CHUNK_SIZE_LOCK` and holds it for the whole `with` body, then
restores every Polars setting with `pl.Config.load`. The optimiser runs
setup, auto-range and the solve on daemon threads in the server process, and
wraps the whole pipeline execution, the auto-range reduction and the solve
itself in that scope; preview and trace take the same scope in the server
process when interactive execution runs on threads. Any other server thread
that enters the scope waits for the solve to finish: the synchronous estimate
endpoint, a second optimiser setup, and the auto-range jobs that the
optimiser specification says do not reserve the solve slot. The lock is a
hidden global serialisation point.

The lock cannot simply be narrowed. Polars exposes the chunk size only as
process-global configuration (`set_streaming_chunk_size` writes the
`POLARS_STREAMING_CHUNK_SIZE` environment variable), and Haute's chunked
writer sizes its slices from the ambient value. If two scopes with different
values overlapped, each would run with the other's setting and restore the
wrong one on exit. The lock exists only because the value varies per request:
`streaming_chunk_size` is a user setting in the pipeline settings modal and a
field on thirteen request models, threaded through every route and service,
with a 1,617-line test module proving it reaches every call site. An analyst
has no basis for choosing a Polars engine knob per request.

**Plan:** Read the chunk size once from `haute.toml` or the environment when
the server and each worker process start, and apply it there. Remove the
field from the request models, the frontend settings store and modal, and
the client helpers. Delete `temporary_streaming_chunk_size` and its lock
rather than narrowing them. Replace the threading tests with one test that
the configured value is applied at start-up. Update the execution-engine and
frontend-shared specifications in the same change.

**Acceptance:** No request schema carries `streaming_chunk_size` and the
frontend sends none; no production code changes Polars configuration after
process start-up, and the scoped override and its lock are gone; a configured
value is observed inside a spawned worker and in the server process; a test
runs an optimiser solve stub that blocks during its execution and proves a
concurrent estimate request on another thread completes without waiting for
it; the optimiser specification's statement that estimates and auto-range
jobs do not reserve the solve slot is true under test.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_utils.py::temporary_streaming_chunk_size`;
`src/haute/_polars_utils.py::_STREAMING_CHUNK_SIZE_LOCK`;
`src/haute/_chunked_writes.py::_chunk_rows`;
`src/haute/routes/_optimiser_service.py::_execute_pipeline`;
`src/haute/routes/_optimiser_service.py::_launch_background`;
`src/haute/routes/_optimiser_service.py::_estimate_scenario_frontier_ranges`;
`src/haute/routes/optimiser.py::_optimiser_input_metrics`;
`src/haute/routes/optimiser.py::estimate_solve`;
`src/haute/routes/pipeline.py::preview_node`;
`src/haute/routes/pipeline.py::trace_row`; `src/haute/schemas.py`;
`frontend/src/components/PipelineSettingsModal.tsx`;
`frontend/src/api/client.ts`; `frontend/src/stores/useSettingsStore.ts`;
`tests/test_streaming_chunk_size_threading.py`.

### EXEC-R03 — Retire the chunked map-reduce runner
**Why:** `chunking.py` is a 2,251-line planner and runner, with per-node-type
capability declarations, an AST row-locality whitelist, chunk sizing and a
hypothesis-based proof suite. It has exactly one production consumer: the
streaming frontier auto-range job. If `OPT-P15` shows that one streaming
group-by keeps auto-range within its memory bound and replaces that job, the
planner and runner are dead. `classify_chunk_local_polars_code` is also used
for row-locality checks in lazy execution and trace correlation, and
`EXPR-R01` may use it too, so it can stay while those checks exist.

**Plan:** After `OPT-P15` removes the consumer, delete `chunk_plan`,
`iter_chunked_frames`, `run_chunked_reduce`, `collect_chunked`, the
capability declarations and their tests. Move `classify_chunk_local_polars_code` and its helpers to a
small module next to its remaining callers, or delete it too if `EXEC-R04`
removes those callers. Remove the chunked map-reduce text from the
execution-engine specification in the same change.

**Acceptance:** No production module imports the planner or runner; the
execution-engine specification no longer describes a chunked map-reduce
mode; the chunk tests that only exercised the runner are gone and the
row-locality classifier keeps its own tests.

**Dependencies:** `OPT-P15` (optimiser); this package does not proceed if
`OPT-P15` keeps the chunked auto-range path. `EXEC-R04` decides whether the
classifier survives.

**Evidence:** `src/haute/chunking.py::chunk_plan`;
`src/haute/chunking.py::iter_chunked_frames`;
`src/haute/chunking.py::classify_chunk_local_polars_code`;
`src/haute/routes/_optimiser_service.py::_run_streaming_frontier_auto_range_job`;
`tests/test_chunk_plan.py`; `tests/test_chunk_runner.py`;
`tests/test_chunk_whitelist_proofs.py`.

### EXEC-R04 — One memory-safety mechanism
**Why:** Two systems protect the host from memory exhaustion. The first is a
static analyser of user Polars code, about 16,000 lines: column lineage,
projection planning, row-cardinality bounds, RAM estimation, a
per-operator memory-factor registry and estimate calibration, with roughly
30,000 lines of tests. It fails closed on any unregistered Polars method and
has to follow Polars behaviour release by release. The second is the set of
killable spawn workers with native hard caps (Job Object, cgroup or
`RLIMIT_AS`) that already run preview, trace, Explore, JSON-cache builds,
output writes, training preparation and multi-row deploy scoring. The
specification already downgrades an unavailable estimate under a cap to
"warned, run conservatively". The static estimate is only load-bearing
where no cap exists: the in-process optimiser, Databricks serving, and hosts
without a native cap. The chunked writer also inspects Polars' internal plan
IR (`_SUPPORTED_IR_MAJOR`), which a Polars upgrade can silently change.

**Plan:** Decide, and record in the execution-engine specification, whether
the hard-capped worker becomes the single safety mechanism. If it does:
move the remaining uncapped surfaces into workers (`OPT-P16`), keep a coarse
Parquet-footer size warning, and remove the admission estimator, the
operator memory registry and cardinality bounds. Decide separately what
column demand at capture points still needs proving; one option is to write
captures at the full width that reaches them and rely on Parquet projection
on read, which also removes snapshot widening. Measure the disk and write
cost of that option on the widest real sources before choosing it.

**Acceptance:** The decision, its evidence, and the resulting single
mechanism are specified. If the static stack is kept, the specification names
the surfaces that genuinely need it and why a hard cap cannot serve them.
If it is removed, every heavy surface runs under a hard cap and a
memory-limited failure is typed and tested on each.

**Dependencies:** The decision has none. Carrying out a removal depends on
`OPT-P16` (optimiser) and `ROAD-WORKER-04` (background jobs, deferred), which
move the last uncapped surfaces into workers. Take the decision before
investing in `CACHE-S18` (caching), whose bounded-join work extends the
chunked writer this decision may retire.

**Evidence:** `src/haute/_column_lineage.py`; `src/haute/projection.py`;
`src/haute/_ram_estimate.py`; `src/haute/_polars_operations.py`;
`src/haute/_execution_admission.py`; `src/haute/_estimate_calibration.py`;
`src/haute/_chunked_writes.py::_SUPPORTED_IR_MAJOR`;
`src/haute/_native_memory_limit.py`; `tests/test_projection_planner.py`;
`tests/test_ram_estimate.py`; `tests/test_column_lineage.py`.

### EXEC-R05 — One graph walker with a collect policy
**Why:** `_execute_lazy` (1,062 lines, 18 parameters, 11 nested functions
including a 214-line `_build_lazy_node`) and `_execute_eager_core` (977
lines, cyclomatic complexity 191) repeat the same prologue: prepare the
execution, check the seed plan, plan projection, build node functions and
open the node-boundary runner. On top of them sit
`_execute_graph_core` (579 lines, nine closures), `_execute_trace_core`
(474), `_score_graph_lazy` (442) and the live `Pipeline.run`/`score` loop.
Target-only preview now keeps ancestors lazy and collects only the target,
so "eager" is lazy execution plus a collect policy. `_execute_lazy.py` and
`_builders.py` are among the most-changed files in the repository. The
repository's own commit standards require "one topo sort, one graph
executor" because "Parallel implementations drift and break".

**Plan:** Introduce one walker that builds a `LazyFrame` per node with
contract enforcement, seed-plan reads and captures, and takes a policy object
for what to collect, at which row limits, and whether node failures are
recorded or raised. Re-express preview, trace, sink execution and deploy
scoring as policies over it, one at a time, and delete each old core when its
last caller moves. Decide whether the live decorator `Pipeline.run`/`score`
path can delegate to the same walker.

**Acceptance:** One module owns graph walking; preview, trace, lazy sinks and
deploy scoring pass their existing suites unchanged through the new walker;
the two old cores are deleted; no function in the walker exceeds a
cyclomatic complexity the team agrees in the specification.

**Dependencies:** `CACHE-S23` (removes the dead cache-request branches first);
`EXEC-R03`, or, if the chunked runner stays, re-expressing it as one more
policy over the walker; and the recorded `EXEC-R04` decision, not its
implementation.

**Evidence:** `src/haute/_execute_lazy.py::_execute_lazy`;
`src/haute/_execute_lazy.py::_execute_eager_core`;
`src/haute/executor.py::_execute_graph_core`;
`src/haute/trace.py::_execute_trace_core`;
`src/haute/deploy/_scorer.py::_score_graph_lazy`;
`src/haute/pipeline.py::Pipeline`; `docs/COMMIT_STANDARDS.md`.

### EXEC-R06 — One process-memory probe
**Why:** Process RSS is read four times, each with its own Windows `ctypes`
`PROCESS_MEMORY_COUNTERS` structure and its own `/proc` parsing:
in the execution context, the process-memory module (a 22-line clone of the
context's version), the native memory-limit module and the CatBoost
algorithm wrapper. Host availability adds a further 743-line module.

**Plan:** Keep one module that reads current-process RSS, another process's
RSS and host availability, and import it everywhere. Evaluate `psutil` as
that module's implementation; if it is adopted, it becomes a declared
dependency and the `ctypes` bindings go.

**Acceptance:** Exactly one definition of a Windows memory-counter structure
remains, or none if `psutil` is adopted; every caller imports the shared
probe; the existing RSS and admission tests pass.

**Dependencies:** None.

**Evidence:** `src/haute/_execution_context.py::_WindowsProcessMemoryCountersEx`;
`src/haute/_process_memory.py::process_rss_bytes`;
`src/haute/_native_memory_limit.py::_PROCESS_MEMORY_COUNTERS_EX`;
`src/haute/modelling/_algorithms.py::_get_rss_mb`;
`src/haute/_host_memory.py`.

### EXEC-R07 — Split `ExecutionContext`
**Why:** `ExecutionContext` is a 1,011-line class that combines the
cancellation token, admission and memory budget, RSS sampling, stage metrics,
column-width evidence, estimate calibration, terminal telemetry, evidence
payloads and request-local fault-injection points. Every execution surface
depends on all of it.

**Plan:** Separate cancellation, admission and budget enforcement, and
evidence and telemetry recording into collaborating objects composed by a
thin context. Move test-only fault injection behind a test seam so production
code carries no fault points.

**Acceptance:** No class in the module exceeds an agreed size; cancellation
and admission can be constructed without the evidence recorder; the existing
execution-context suite passes; `ExecutionFaultPoint` is not reachable from
production entry points.

**Dependencies:** Best taken after `EXEC-R04`, which may remove calibration
and estimate evidence.

**Evidence:** `src/haute/_execution_context.py::ExecutionContext`;
`src/haute/_execution_context.py::ExecutionFaultPoint`;
`tests/test_execution_context.py`.

### EXEC-R08 — One graph-traversal module
**Why:** Ancestor walks are implemented five times: the topology module's
`ancestors`, `upstream_node_ids`, the waterfall's lineage check, the recovery
route's ancestor closure and the optimiser's upstream node-type search.
Projection hand-rolls Kahn's algorithm with a heap while the topology module
uses `graphlib`. Preview and trace each define a preparation-order function
with an identical body. The commit standards ask for one topological sort.

**Plan:** Put ancestors, descendants, topological order and canonical ranks
in the topology module and route every caller through it. Merge the two
preparation-order functions into one.

**Acceptance:** One ancestor implementation and one topological sort remain;
the duplicated preparation-order functions are one function; the existing
topology, projection, trace and recovery suites pass.

**Dependencies:** None.

**Evidence:** `src/haute/_topo.py::ancestors`;
`src/haute/_graph_utils.py::upstream_node_ids`;
`src/haute/_trace_waterfall.py::_has_lineage_path`;
`src/haute/routes/pipeline.py::_recovery_ancestor_ids`;
`src/haute/routes/_optimiser_service.py::_upstream_slice_contains_node_type`;
`src/haute/projection.py::_canonical_topological_ranks`;
`src/haute/executor.py::_preview_preparation_order`;
`src/haute/trace.py::_trace_preparation_order`.
