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
| CLI-R03 | Planned | P2 | `haute lint` applies the editor's save-time validation, so CI's lint step refuses what the editor would refuse. |
| CLI-R05 | Planned | P3 | Unexpected errors keep their tracebacks, the transport helper raises instead of exiting, and `haute serve` opens the browser when the server is ready. |

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

### CLI-R03 — `haute lint` runs the editor's validators
**Why:** The generated CI's "Pipeline lint" step runs `haute lint`, which parses the file
and checks only dangling edges and disconnected nodes; it runs none of the save-time
validation the editor applies (node config validation, name violations, global constants,
statements a save would drop), so CI's lint passes a pipeline the editor would refuse to
save and only the deploy dry run catches it. It parses unflattened, unlike `haute run`.

**Plan:** Run the save validators over the parsed document and report their diagnostics
with file and line; parse flattened.

**Acceptance:** A pipeline with a node config the editor's save refuses fails `haute
lint` with the same message.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_lint.py::handle_lint`; `src/haute/routes/_save_pipeline.py`
(the save-time validation).

### CLI-R05 — Error reporting and helpers
**Why:** `deploy`, `run`, `lint` and `train` catch `Exception` around resolution, parsing
and execution and exit 1 with one line, so a programmer error loses its traceback, which
the deploy specification reserves for expected errors. `resolve_transport` raises
`SystemExit` from a helper. `resolve_model_name`'s docstring omits the environment step
it implements. In production mode `haute serve` opens the browser after a fixed 1.5 s
while dev mode waits for the sockets; the readiness helpers written for production mode
have no caller. `_helpers.py` keeps an unused `subprocess` import so a test can patch it.

**Plan:** Catch the expected types (`DeployError`, `ValueError`, `FileNotFoundError`,
`ConfigError`) and let the rest propagate; raise a `ClickException` from the transport
helper; correct the docstring; use the readiness wait in production mode and delete the
fixed delay or the unused helpers; have the test assert on `webbrowser` and drop the
import.

**Acceptance:** A `TypeError` raised inside resolution reaches the terminal with its
traceback; `haute serve` opens the browser only once the port accepts connections.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_deploy.py::handle_deploy`; `src/haute/cli/_run.py::handle_run`;
`src/haute/cli/_lint.py::handle_lint`; `src/haute/cli/_train.py::handle_train`;
`src/haute/cli/_helpers.py::resolve_transport` and `::resolve_model_name`;
`src/haute/cli/_serve.py::_run_prod_mode`, `::_schedule_browser_open` and
`::_open_browser_after_backend_ready`.
