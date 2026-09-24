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
| EXEC-R05 | Planned | P2 | One graph walker builds every execution; eager, preview, trace and scoring differ only in their collect policy. |

## Planned improvements

The walker in `EXEC-R05` keeps projection planning, which the memory-safety
decision retains for uncapped surfaces. The chunked map-reduce runner stays:
the optimiser's `OPT-P15` measured the streaming frontier auto-range job and
kept its chunked path, so the retirement once planned for the runner does not
proceed and the runner becomes one more policy over the walker.

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

**Dependencies:** None. The chunked runner stays (see above), so it moves onto
the walker as one more policy.

**Evidence:** `src/haute/_graph_walker.py::walk_graph` (every surface but the chunked runner so far);
`src/haute/executor.py::_execute_graph_core`;
`src/haute/trace.py::_execute_trace_core`;
`src/haute/deploy/_scorer.py::_score_graph_lazy`;
`src/haute/pipeline.py::Pipeline`; `docs/COMMIT_STANDARDS.md`.
