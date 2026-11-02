# CLI roadmap

## Scope

The command-line surface: `haute init`, `run`, `lint`, `serve`, `deploy`, `smoke`,
`impact`, `train`, `status`, `ci` and `app`, and the files `haute init` generates.
Current behaviour is specified in [the CLI specification](../cli/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CLI-R02 | Planned | P2 | `haute run` walks the pipeline lazily to its output node under the batch profile, with a named source, instead of materialising every node in memory. |

## Planned improvements

### CLI-R02 — `haute run` as a lazy walk
**Why:** `handle_run` calls `execute_graph(graph)` with no target and no row limit, which
is the editor's full-materialisation preview: every node is collected into a DataFrame
under the eager preview profile and handed to its consumers as a DataFrame, so the whole
pipeline's outputs sit in memory at once, with no `--source`, under the live source only.
The execution specification calls materialising everything eagerly memory-unsafe for a
batch run, and `haute run` is the only caller of that mode; its one test mocks the
executor away. The CLI specification endorses the path as "the same parse → flatten →
`execute_graph` path the GUI uses".

**Plan:** Run `haute run` as a sink walk of the output node through
`execute_lazy_graph` under the batch profile, collecting only the output (or `--rows N`
of it), with a `--source` option; report each node's schema from the lazy plan. Update
the CLI specification's description of the command.

**Acceptance:** `haute run` on a pipeline whose intermediate nodes hold more rows than
memory finishes within the output's memory; a test runs the real executor on a small
pipeline and asserts no node but the output is collected.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_run.py::handle_run`; `src/haute/executor.py::execute_graph`;
`src/haute/execution.py::execute_lazy_graph`; `tests/test_cli_architecture.py` (the
mocked `handle_run` test).

