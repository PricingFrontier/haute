# CLI roadmap

## Scope

The command-line surface: `haute init`, `run`, `lint`, `serve`, `deploy`, `smoke`,
`impact`, `train`, `status`, `ci` and `app`, and the files `haute init` generates.
Current behaviour is specified in [the CLI specification](../cli/high-level.md). These
packages come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CLI-R01 | Planned | P1 | `haute init --force` regenerates only the files Haute wrote and never overwrites the pipeline, utility code, tests or form. |
| CLI-R02 | Planned | P2 | `haute run` walks the pipeline lazily to its output node under the batch profile, with a named source, instead of materialising every node in memory. |
| CLI-R03 | Planned | P2 | `haute lint` applies the editor's save-time validation, so CI's lint step refuses what the editor would refuse. |
| CLI-R04 | Planned | P3 | `haute init` edits `pyproject.toml` through `tomlkit` instead of a hand-written TOML scanner. |
| CLI-R05 | Planned | P3 | Unexpected errors keep their tracebacks, the transport helper raises instead of exiting, and `haute serve` opens the browser when the server is ready. |
| CLI-R06 | Planned | P3 | Generated workflows pin actions by commit, and the generated pre-commit hook formats only what is staged. |
| CLI-R07 | Planned | P2 | `haute impact` confines `impact_dataset` to the project root and preserves non-string scalar fields from `test_quotes_dir`, `haute app migrate --sql` honours `--base` schemas and refuses restoring plans without `--restore-point`, and `haute lint` and `haute run` catch `ConfigError` from `resolve_pipeline_file`. |

## Planned improvements

### CLI-R01 — `init --force` keeps the analyst's work
**Why:** After the `haute.toml` guard, `handle_init` writes every scaffold file
unconditionally: with `--force`, `rating/main.py`, `rating/utility/__init__.py` and
`features.py`, `tests/test_pipeline.py` and `tests/quotes/example.json` are replaced by
the starters, so an analyst who re-runs init to change the CI provider or
the target loses uncommitted pipeline work. The first `init` also deletes a root `main.py`
it did not write. `haute ci update` already regenerates the CI files alone.

**Plan:** Make `--force` regenerate only Haute's own generated files (`haute.toml`, the CI
files, `.env.example`, the pre-commit hook) and refuse to overwrite a pipeline, utility
or test that differs from the starter, naming it; remove a root `main.py` only when
it is the `uv init` placeholder byte for byte.

**Acceptance:** `haute init --force` in a project with an edited `rating/main.py` leaves
the file unchanged and says so; a first `init` next to a non-placeholder `main.py` keeps
it.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_init_cmd.py::handle_init`; `src/haute/cli/_ci.py::handle_ci_update`.

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

### CLI-R04 — `pyproject.toml` through `tomlkit`
**Why:** About 250 lines of `_init_cmd.py` hand-roll a TOML header scanner and splicer to
insert `"haute"` into `[project].dependencies`, each with its own edge cases, while
`tomlkit`, a style-preserving TOML editor, is already a runtime dependency.

**Plan:** Edit the document with `tomlkit` and delete the scanner, the bracket matcher and
the string serialiser.

**Acceptance:** The existing `init` tests pass against the `tomlkit` edit; the scanner
functions are gone.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_init_cmd.py::_ensure_haute_dependency`,
`::_rewrite_project_dependencies`, `::_scan_table_headers` and `::_find_matching_bracket`.

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

### CLI-R06 — Generated CI hygiene
**Why:** The generated deploy workflows, which hold registry and Databricks tokens, pin
actions by floating major tag (`actions/checkout@v4`), so a compromised tag runs with the
secrets. The generated pre-commit hook runs `git add $FILES`, staging whole files so an
unstaged hunk of a partially staged file is committed, and `|| true` hides a failing
`ruff check --fix`.

**Plan:** Pin actions to commit SHAs with the tag in a comment; format only the staged
content and let the check's failure stop the commit.

**Acceptance:** Every `uses:` in the generated workflows names a 40-hex commit; a
partially staged file commits only its staged hunks under the hook.

**Dependencies:** None.

**Evidence:** `src/haute/_scaffold.py::github_ci_yml`, `::github_deploy_yml`,
`::github_deploy_prod_yml` and `::pre_commit_hook`.

### CLI-R07 — `impact` dataset confinement, `app migrate --sql` schema and restore-point checks, and `lint`/`run` pipeline resolution errors
**Why:** Four CLI defects affect `haute impact`, `haute app migrate`, `haute lint`
and `haute run`:
1. `_impact_records` resolves `config.safety.impact.dataset_path` with
   `(project_root / impact_path).resolve()` without `contained_path(project_root, impact_path)`,
   allowing a relative `../` or absolute `impact_dataset` in `haute.toml` to read
   outside the project root. When `impact_dataset` is unset and `haute impact`
   falls back to `test_quotes_dir`, `_impact_records` builds `records_df` with
   `[{k: v for k, v in r.items() if isinstance(v, str)} for r in records]`, which
   drops non-string scalar fields (numeric and boolean policy attributes and
   identifiers) from `records_df`.
2. In `handle_app_migrate`, when `--base` and `--sql` are passed together
   (`haute app migrate --environment prod --base <dir> --sql`), the `base is not None`
   branch resolves only the `[workbench.store]` environment list and never calls
   `resolve_store`, leaving `sql_schema = None` even when `HAUTE_STORE_SCHEMA` is set.
3. `render_sql(plan, environment=..., restore_point=..., schema=...)` does not derive
   `mode: Mode = "dev" if environment == DEV else "hosted"` and call
   `_refuse(plan, restore_point, mode)` before rendering DDL, so
   `haute app migrate --sql` without `--restore-point` on a restoring migration
   renders SQL with `restore_point=None` instead of raising `MigrationRefusedError`
   as live migration does.
4. `lint` (`src/haute/cli/_lint.py`) and `run` (`src/haute/cli/_run.py`) catch only
   `FileNotFoundError` around `resolve_pipeline_file(pipeline_file)`, whereas
   `resolve_pipeline_file` also raises `ConfigError` when `[project].pipeline` in
   `haute.toml` escapes the project root or `haute.toml` is malformed, producing an
   unhandled traceback instead of a clean CLI error exit.

**Plan:** Validate `impact_dataset` with `contained_path(project_root, impact_path)`
and preserve scalar top-level fields (`str`, `int`, `float`, `bool`) in
`_impact_records`. In `handle_app_migrate`, read the stripped `HAUTE_STORE_SCHEMA` via
`_given(environ, STORE_SCHEMA_ENV)` (matching `resolve_store` in
`_quote_store/_environment.py:143`, which passes `schema` through to SQLAlchemy
`MetaData(schema=schema)` in `_quote_store/_schema.py:291` while refusing
`HAUTE_STORE_SCHEMA` on file-provider environments) when `--base` and `--sql` are
combined, and derive `mode: Mode = "dev" if environment == DEV else "hosted"` inside
`render_sql` and call `_refuse(plan, restore_point, mode)` so restoring plans require
`restore_point` in `--sql` mode too. Catch `(FileNotFoundError, ConfigError)` around
`resolve_pipeline_file` in `lint` and `run`.

**Acceptance:** An `impact_dataset` outside `project_root` is refused before reading;
`haute impact` with `test_quotes_dir` quotes containing numeric and boolean scalar
fields preserves those columns in `records_df`; `haute app migrate --base <dir> --sql`
with `HAUTE_STORE_SCHEMA=underwriting` qualifies DDL with `"underwriting"`;
`render_sql` on a restoring plan for a hosted environment (`environment="prod"`) with
`restore_point=None` raises `MigrationRefusedError`; and `haute lint` / `haute run`
exit with code 1 and the error message when `[project].pipeline` in `haute.toml` is
out of bounds or `haute.toml` is malformed.

**Dependencies:** Coordinates with `CLI-R05`.

**Evidence:** `src/haute/cli/_impact.py::_impact_records`;
`src/haute/cli/_app.py::handle_app_migrate`;
`src/haute/_quote_store/_migrate.py::render_sql`;
`src/haute/_quote_store/_migrate.py::_refuse`;
`src/haute/cli/_lint.py::lint`;
`src/haute/cli/_run.py::run`;
`src/haute/_project.py::resolve_pipeline_file`.
