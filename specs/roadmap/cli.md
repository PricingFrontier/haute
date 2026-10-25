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
| CLI-R07 | Decision | P2 | A team can require a reviewer before a production deploy in its CI provider's settings, and `haute ci update` keeps what that needs. |

## Planned improvements

### CLI-R01 — `init --force` keeps the analyst's work
**Why:** After the `haute.toml` guard, `handle_init` writes every scaffold file
unconditionally: with `--force`, `rating/main.py`, `rating/utility/__init__.py` and
`features.py`, `tests/test_pipeline.py`, `tests/quotes/example.json` and `forms/form.json`
are replaced by the starters, so an analyst who re-runs init to change the CI provider or
the target loses uncommitted pipeline work. The first `init` also deletes a root `main.py`
it did not write. `haute ci update` already regenerates the CI files alone.

**Plan:** Make `--force` regenerate only Haute's own generated files (`haute.toml`, the CI
files, `.env.example`, the pre-commit hook) and refuse to overwrite a pipeline, utility,
test or form that differs from the starter, naming it; remove a root `main.py` only when
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

### CLI-R07 — Who may start a production deploy
**Why:** The generated workflows gate production only by who may start it. GitHub's
production workflow runs on `workflow_dispatch`, so anyone with write access to the
repository can run it, and GitLab's `deploy-production` job is `when: manual`, so anyone who
may run the pipeline's jobs can play it; Azure DevOps' production stage runs under
`environment: production`, whose checks are configured in the portal. A GitHub or GitLab
team that wants a required reviewer needs the production job to name an environment, which
the generated files do not, and an `environment:` added by hand is lost at the next
`haute ci update`, which rewrites the generated files. Which reviewers an environment allows
depends on the plan: GitHub offers required reviewers to private repositories only on
GitHub Enterprise (Free, Pro and Team have them for public repositories), and GitLab's
protected environments and deployment approvals need Premium or Ultimate; that is why the
pipeline-config specification has GitHub's production split work without environment
protection rules. `[safety.approval] min_approvers`, which `haute init` writes, is read by
nothing, as both CI pages say.

**Decision:** Whether the generated GitHub and GitLab production jobs name
`environment: production`, always or when `haute.toml` asks, so that reviewers configured
in the provider's settings gate them and survive `haute ci update`; and whether
`min_approvers` stays as a record or goes.

**Plan:** Once decided, the generators name the environment as decided; the
pipeline-config specification's release flow changes with them, and DOC-02 describes it.

**Acceptance:** With required reviewers configured on the named environment, a production
deploy waits for a reviewer, and `haute ci update` keeps the job's environment.

**Dependencies:** None.

**Evidence:** `src/haute/_scaffold.py::github_deploy_prod_yml`, `::gitlab_ci_yml` and
`::azure_devops_yml`; `specs/pipeline-config/high-level.md` (the release flow's approval
mechanisms); `docs/deployment/ci/github-actions.md` and `gitlab.md` (the approval advice).
