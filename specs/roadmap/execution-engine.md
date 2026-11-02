# Execution engine roadmap

## Scope

The lazy walker and its display and sink walks, admission, the static analyses
that admit work (projection, column lineage, the RAM estimate, the chunk-local
classifier), the eager preview executor, the standalone `Pipeline.run` path, and
the worker processes that run engine work (the one-shot and protocol workers,
the warm interactive pool, the memory caps). Current behaviour is specified in
[the execution-engine specification](../execution-engine/high-level.md). EXEC-R17
comes from the [25 October 2026 codebase review](codebase-review-2026-10-25.md) and
EXEC-R20 from the preview-memory measurements of November 2026; the numbering
continues from the September 2026 review's packages, which are retired.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXEC-R17 | Planned | P3 | The project's `utility` package is imported through a finder bound to the project, so `sys.path` is never edited, an import deferred to collect time still resolves, and a same-size, same-mtime edit is always seen. |
| EXEC-R20 | Planned | P2 | A preview computes a frame several nodes read once only where its row limit cannot reach that frame's producer, and recomputes it everywhere else. |

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

### EXEC-R20 — A preview shares a frame only where its row limit cannot reach
**Why:** Whether computing a frame several nodes read once lowers a preview's peak
depends on whether the preview's row limit reaches that frame's producer. Measured on
the streaming engine previews collect with (a 5M-row parquet source, a nested diamond
collected at one node, a 1,000-row preview, a fresh process per run, Windows peak
working set): a producer of plain row-local columns peaks at 56 MB recomputed and
464 MB shared, because the cache stops the limit reaching the scan; a sort at 812 MB
recomputed, where the limit becomes a top-k, and 953 MB shared; an opaque `map_batches`
producer at 983 MB recomputed and 574 MB shared; a `group_by` producer at 1,083 MB
recomputed and 861 MB shared. With no row limit, sharing is lower for every producer
(454 to 1,109 MB against 683 to 1,628 MB). The display walk caches nothing, and Polars'
common-subplan elimination, on for every preview collect, shares a simple diamond's
producer through its own cache: a simple diamond of plain columns previews at 450 MB
where recomputing holds 56 MB, and a simple diamond over a `group_by` at 847 MB where
recomputing holds 1,110 MB. It does not share a nested diamond's outer producer, which
runs once per path.

**Plan:** A display walk's collects run with common-subplan elimination off, and the
walker decides sharing itself. It caches a frame several edges read when two or more of
its consumers reach the same collected node without passing through another collected
node, and the row limit cannot reach the frame's producer: the collect has no row limit,
or the frame's lineage below the limit holds an aggregation, a join, an Edge Join or an
opaque Python step, classified through the operation registry and the chunk-local
classifier. A sort counts as reachable. A frame whose lineage cannot be classified is
recomputed, since a wrong cache costs up to eight times the peak and a wrong recompute
about one and a half. Measure the same shapes through the sink walk's streaming sinks
and apply the rule there only where it lowers the peak. Update the low-level
specification's fan-out sentence.

**Acceptance:** On each measured shape, a 1,000-row preview peaks within ten percent of
the lower of its two figures above, with identical results. A simple diamond of plain
columns previews with no cache in its optimised plan. A nested diamond over a `group_by`
computes it once. A frame with an unclassifiable lineage is never cached. A
representative project preview, such as the demo project's, peaks no higher than under
the current rule.

**Dependencies:** None.

**Evidence:** `src/haute/_graph_walker.py::_input_frame`;
`src/haute/_graph_walker.py::_run_collect`;
`src/haute/_polars_utils.py::streaming_collect`;
`src/haute/_polars_operations.py::OperationClass`;
`src/haute/chunking.py::ChunkLocalDecision`.
