# Execution engine roadmap

## Scope

The lazy walker and its display and sink walks, admission, the static analyses
that admit work (projection, column lineage, the RAM estimate, the chunk-local
classifier), the eager preview executor, the standalone `Pipeline.run` path, and
the worker processes that run engine work (the one-shot and protocol workers,
the warm interactive pool, the memory caps). Current behaviour is specified in
[the execution-engine specification](../execution-engine/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md);
the numbering continues from the September 2026 review's packages, which are
retired.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXEC-R17 | Decision | P3 | The preamble compile stops reordering `sys.path` for the whole process, while a project's imports, including ones deferred to collect time, still resolve to that project and its edited `utility` package. |

## Planned improvements

### EXEC-R17 — The preamble compile stops reordering `sys.path`
**Why:** Every preamble compile moves the pipeline and project directories to the front
of `sys.path` for the whole process and leaves them there. A scope around the compile and
the walk alone does not fix this: a helper that imports `utility.sub` inside a Python
function Polars calls at collect time runs after that scope has exited, so its import
would resolve against whatever `sys.path` holds by then. The utility bytecode deletion
is a documented freshness defence: eviction from `sys.modules` and import-cache
invalidation alone can reuse a stale `.pyc` after an edit that keeps the file's size and
mtime.

**Decision:** How long the project's import entry lives. (a) For the execution: each
execution that compiles a preamble holds a reference-counted entry until its last
collect returns, and the last holder restores `sys.path`, on success and on failure.
(b) Keep the process-wide reordering and specify it as the engine's import model.
(c) Resolve project imports through a finder bound to the project root, so `sys.path` is
never edited.

**Plan:** Implement the chosen option. Preserve the existing utility `.pyc` invalidation;
a replacement is acceptable only if its source-loading mechanism is specified and proves
the same freshness guarantee.

**Acceptance:** Under (a) or (c), `sys.path` is unchanged after compilation and
execution, including failures; under (b), the specification states the reordering. In
every option, a same-size/same-mtime edit to `utility.py` changes the next run's result,
and a helper importing `utility.sub` during node execution, inside a function Polars
calls at collect time, resolves the correct project's package.

**Dependencies:** None.

**Evidence:** `src/haute/executor.py::_prioritise_preamble_import_paths`;
`src/haute/executor.py::_evict_utility_import_state`.
