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
that serialises unrelated optimiser work. The execution-engine specification
records the memory-safety decision: hard-capped workers are the mechanism, and
the static estimate admits only what no cap bounds.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXEC-R03 | Planned | P3 | The chunked map-reduce planner and runner are removed with their only consumer. |
| EXEC-R05 | Planned | P2 | One graph walker builds every execution; eager, preview, trace and scoring differ only in their collect policy. |

## Planned improvements

`EXEC-R03` follows the optimiser's `OPT-P15` if that package removes the
runner's only consumer. The walker in `EXEC-R05` keeps projection planning, which
the memory-safety decision retains for uncapped surfaces.

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
small module next to its remaining callers. Remove the chunked map-reduce text from the
execution-engine specification in the same change.

**Acceptance:** No production module imports the planner or runner; the
execution-engine specification no longer describes a chunked map-reduce
mode; the chunk tests that only exercised the runner are gone and the
row-locality classifier keeps its own tests.

**Dependencies:** `OPT-P15` (optimiser); this package does not proceed if
`OPT-P15` keeps the chunked auto-range path. The classifier survives: lazy
execution and trace correlation keep using it.

**Evidence:** `src/haute/chunking.py::chunk_plan`;
`src/haute/chunking.py::iter_chunked_frames`;
`src/haute/chunking.py::classify_chunk_local_polars_code`;
`src/haute/routes/_optimiser_service.py::_run_streaming_frontier_auto_range_job`;
`tests/test_chunk_plan.py`; `tests/test_chunk_runner.py`;
`tests/test_chunk_whitelist_proofs.py`.

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

**Dependencies:** `EXEC-R03`, or, if the chunked runner stays, re-expressing it as one more
policy over the walker.

**Evidence:** `src/haute/_execute_lazy.py::_execute_lazy`;
`src/haute/_execute_lazy.py::_execute_eager_core`;
`src/haute/executor.py::_execute_graph_core`;
`src/haute/trace.py::_execute_trace_core`;
`src/haute/deploy/_scorer.py::_score_graph_lazy`;
`src/haute/pipeline.py::Pipeline`; `docs/COMMIT_STANDARDS.md`.
