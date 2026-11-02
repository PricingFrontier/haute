# Execution engine roadmap

## Scope

The lazy walker and its display and sink walks, admission, the static analyses
that admit work (projection, column lineage, the RAM estimate, the chunk-local
classifier), the eager preview executor, the standalone `Pipeline.run` path, and
the worker processes that run engine work (the one-shot and protocol workers,
the warm interactive pool, the memory caps). Current behaviour is specified in
[the execution-engine specification](../execution-engine/high-level.md). EXEC-R17
comes from the [25 October 2026 codebase review](codebase-review-2026-10-25.md); the
numbering continues from the September 2026 review's packages, which are retired.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXEC-R17 | Planned | P3 | The project's `utility` package is imported through a finder bound to the project, so `sys.path` is never edited, an import deferred to collect time still resolves, and a same-size, same-mtime edit is always seen. |

## Planned improvements

### EXEC-R17 — Project imports through a finder, not `sys.path`
**Why:** Every preamble compile moves the pipeline and project directories to the front
of `sys.path` for the whole process and leaves them there, so any file in those
directories shadows an installed package of the same name for the rest of the process,
and a local module outside `utility` imports in the editor although `validate_deploy`
refuses it. Scoping the edit to the compile and the walk does not work: a helper that
imports `utility.sub` inside a Python function Polars calls at collect time runs after
the scope has exited, and lazy frames the engine returns are collected later by their
callers, so an execution has no knowable end at which to restore the path. The cache
fingerprint (`_utility_search_path`) and the deploy bundle (`_project_utility`) each
repeat the executor's resolution order by hand. Freshness rests on deleting `utility`
bytecode, because timestamp-validated `.pyc` files can hide a same-size, same-mtime edit.

**Plan:** `utility` is the only project code a preamble imports; everything else a
project needs is data, read by path from its data folders. One resolver finds the
project's `utility` module or package: the pipeline's directory first, then the project
root, never the working directory. A meta-path finder installed once answers `utility`
and every `utility.*` name from that resolver, with a loader that compiles each module
from source and never reads or writes bytecode; every other name falls through to the
normal import system, so installed packages import as before. The finder is bound to the
project whose preamble was compiled last: compiling another project's preamble, under the
existing preamble lock, rebinds it and evicts `utility` from `sys.modules`, and the
server and each worker bind it to their project the same way. Eviction on a changed
preamble digest stays; the bytecode deletion goes, since the loader reads none.
`_prioritise_preamble_import_paths` and the compile path's `sys.path` insertion are
deleted. The cache fingerprint and the deploy bundle take their `utility` files from the
same resolver. A local import outside `utility` fails in the editor naming the module,
as it already fails at deploy. Update the execution-engine, deploy and codegen
specifications and `docs/building-models/project-files.md`.

**Acceptance:** `sys.path` is identical before and after a compile and a run, including
a failing preamble and a failing node. A helper importing `utility.sub` inside a function
Polars calls at collect time resolves the bound project's package. A same-size,
same-mtime edit to `utility.py`, and to a module inside a `utility/` package, changes the
next run's result with no bytecode deleted. A `rating/utility/` beside the pipeline wins
over a root-level `utility`. A file named after an installed package in the pipeline
directory no longer shadows it. A local import outside `utility` fails in the editor with
the module named. The cache fingerprint and the deploy bundle name exactly the files the
finder loads. Data files under the project's data folders read as before.

**Dependencies:** None. `PCFG-R04` owns the other working-directory resolvers; this
package's resolver uses the project root.

**Evidence:** `src/haute/executor.py::_prioritise_preamble_import_paths`;
`src/haute/executor.py::_evict_utility_import_state`;
`src/haute/executor.py::_compile_preamble`;
`src/haute/_cache.py::_utility_search_path`;
`src/haute/deploy/_project_modules.py::_project_utility`.
