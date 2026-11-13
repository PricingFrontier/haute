# CLI roadmap

## Scope

The `haute` commands, as [the CLI specification](../cli/high-level.md)
describes them: what a command prints when the analyst's own mistake stops
it, where it looks for the project, and what the commands of the generated
CI jobs say. The packages below come from the review plan's sweep of 11
November 2026, which ran the commands 499 times with wrong arguments, wrong
paths, a damaged `haute.toml` and awkward folders. The main session ran
each reproduction again in a fresh folder. `CLI-05` comes from the main
session's run, that day, of a submodel's whole life through the routes.
`CLI-06` comes from the plan's phase 5, a reader from a second model
family (Codex) over the code changed since the 31 October audit, and was
reproduced with the real executable on 13 November. `CLI-07` comes from
the plan's phase 4, which carried projects made on Windows to Linux.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CLI-01 | Planned | P3 | A mistake in `haute.toml`, missing credentials and a file that cannot be written each end a command in one message, not a traceback. |
| CLI-02 | Planned | P3 | Every command finds the project from a folder inside it, as `run`, `lint`, `deploy` and `serve` do. |
| CLI-03 | Planned | P3 | `haute ci registry-host` refuses an empty registry by name, and a mistyped deploy target or table is named where it is. |
| CLI-04 | Planned | P3 | The project `haute init` makes passes the lint step of the CI it generates. |
| CLI-05 | Planned | P3 | `haute run --node` still finds a node by its name after it is grouped into a submodel. |
| CLI-06 | Planned | P2 | `haute ci update` and a second `haute init` remove only the CI files Haute wrote; a team's own workflow with one of Haute's file names is left, and whatever is removed is named. |
| CLI-07 | Planned | P3 | `haute init` on Windows records its pre-commit hook as executable, so a clone on Linux or macOS can run it. |

## Planned improvements

### CLI-01 — An expected failure is one message, not a traceback
**Why:** Reproduced on 11 November 2026 by running the commands. Each of
these ordinary situations ends in a Python traceback, with the useful
sentence as its last line:

- `haute status`, and `haute status --version-only`, before the Databricks
  credentials are in `.env`: "Databricks MLflow is not ready for deploy:
  Databricks is not configured for MLflow", for a command that deploys
  nothing.
- A key misspelt by one letter in `haute.toml`, under
  `haute deploy --dry-run`, `haute impact` and `haute smoke`, and a misspelt
  table under `haute status`: "Check for typos in your configuration keys."
- A number written in quotes, `min_approvers = "2"`, under the dry run:
  "[safety.approval] min_approvers must be an integer; got '2'."
- A `haute.toml` that is not TOML, under `haute impact`, `haute smoke` and
  `haute serve` (run through a wrapper that keeps it out of development
  mode). `haute run` and the dry run give the same file one line.
- A `haute.toml` marked read-only when `haute init` runs again, and a
  workflow file marked read-only under `haute ci update`: "Permission
  denied" and the path.
- A port out of range, `haute serve --port 70000`: an `OverflowError`
  (seen by the sweep, not run again).
- A file of the built release open in another program when
  `haute app build` runs again: "Access is denied", once the folder
  rename's four seconds of waiting are over. The old release is kept and
  the next build succeeds.

The specification lets some errors reach the terminal with their traceback,
and names where a command turns the expected ones into one line: Haute's
own errors, `ValueError` and `OSError`. Every case above is one of those
three, raised just outside the place that catches it. `DPL-03` records the
same for a missing pipeline file.

**Plan:** Put each command's reading of `haute.toml`, its credential check
and its file writes inside the catch the command already has for expected
failures, so that each prints its sentence and exits 1. Word the status
check for what it is, not "for deploy".

**Acceptance:** Each case above prints one message naming the key, the file
or the missing setting, exits 1 and prints no traceback. A test runs the
commands whole (`EQ-04`).

**Dependencies:** `DPL-03` covers the missing pipeline file in the same
branch of `haute deploy`.

**Evidence:** `src/haute/cli/_helpers.py::EXPECTED_FAILURES`;
`src/haute/cli/_status.py::handle_status`;
`src/haute/deploy/_config.py::_validate_toml_keys`;
`src/haute/cli/_serve.py::_load_toml_server_host`;
`src/haute/_scaffold_files.py::write_lf`; `src/haute/cli/_impact.py`;
`src/haute/cli/_deploy.py`; `src/haute/cli/_app.py`;
`src/haute/_file_ops.py::rename_directory`.

### CLI-02 — Every command finds the project from a folder inside it
**Why:** Reproduced on 11 November 2026. With the shell in the project's
`rating` folder, where the pipeline file is, `haute run`, `haute lint`,
`haute deploy` and `haute serve` find the project above. The others do not,
and say something else: `haute status` answers "model_name is required —
pass --model-name on the command line or run inside a Haute project with
[deploy].model_name set in haute.toml."; `haute impact` answers "No
haute.toml found."; `haute ci update` answers "No haute.toml found: run this
in a Haute project."; and `haute app build`, in a workbench project, answers
"This project has no workbench: add [workbench] enabled = true to
haute.toml.", which is false. The sweep saw `haute smoke` and
`haute ci registry-host` answer as `impact` and `ci update` do, and saw
`haute init`, run from that folder, make a second project inside the
first.

**Plan:** Have every command resolve the project as `run` does, walking up
from the shell's folder to the `haute.toml`, and read its settings from
there.

**Acceptance:** From a folder inside a project, `status`, `impact`, `smoke`,
`ci update`, `ci registry-host` and `app build` each behave as from the
project's root.

**Dependencies:** None.

**Evidence:** `src/haute/_project_context.py::resolve_project`;
`src/haute/cli/_status.py`; `src/haute/cli/_impact.py`;
`src/haute/cli/_ci.py`; `src/haute/cli/_app.py`.

### CLI-03 — The CI commands name the setting that is wrong
**Why:** Reproduced on 11 November 2026. A workbench project as
`haute init --workbench` leaves it has `registry = ""`. Its generated
release job runs `haute ci registry-host` to log in to the registry before
it pushes the app's image. The command exits 0 and prints nothing, so the
job logs in to an empty host. `haute app build --push` refuses the same
file: "[deploy.container] registry is empty: name the registry the app's
image is pushed to in haute.toml."

Two more answers name the wrong place. With `[deploy] target` mistyped as
`databrick`, `haute ci update` answers "Unknown target: databrick in [ci]
provider of haute.toml.", though the `[ci]` table is right. With the
`[workbench]` table mistyped as `[workbnch]`, `haute app build` answers
"[workbench].enabled must be true or false", while `haute ci update` names
the unknown section.

**Plan:** Have `haute ci registry-host` refuse an empty registry in the
words `haute app build --push` uses. Name `[deploy] target` in the unknown
target's message, and let `haute app build` and `haute app serve` report an
unknown table as `haute ci update` does.

**Acceptance:** `haute ci registry-host` on a project with no registry exits
non-zero naming `[deploy.container] registry`. A mistyped `[deploy] target`
and a mistyped `[workbench]` table are each named as they are written.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_ci.py::registry_host_command`;
`src/haute/_scaffold.py`; `src/haute/cli/_app.py`.

### CLI-04 — A new project passes the lint its own CI runs
**Why:** Reproduced on 11 November 2026. `haute init` writes a pipeline file
with no nodes and a CI workflow whose first check is `haute lint`. On that
project `haute lint` exits 1: "✗ No nodes found in pipeline." So the first
push of every new project fails its CI, with no remedy named. The
workflow's next check, the dry run, fails on the same project too
("Resolution failed: No nodes found in" the file); its four other checks
pass. The editor
loads the same file as ready and saves it (run through the save route). The
specification says lint "checks a pipeline as the editor's save does ... so
the generated CI's lint step refuses what the editor would refuse to save".

**Plan:** Let `haute lint` pass a pipeline with no nodes, with a line saying
that it has none; or, if an empty pipeline is to fail, say what to do next
and have the save refuse it too.

**Acceptance:** `haute lint` on the project `haute init` makes exits 0, or
exits non-zero with a message that names the remedy and the editor's save
refuses the same file. Either way lint and the save agree.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_lint.py`; `src/haute/_scaffold.py`;
`specs/cli/high-level.md`.

### CLI-05 — `haute run --node` finds a node that was grouped into a submodel
**Why:** Reproduced on 11 November 2026. In a pipeline of four nodes,
`haute run --node capped` prints that node's rows. The analyst groups the
nodes `loaded` and `capped` as the submodel `loading` and saves. From then
on `haute run --node capped` exits 1 with "Error: The pipeline has no node
'capped'." `--node loaded` answers the same, and so does `--node loading`,
the name of the box itself. The run still walks those nodes: `haute lint`
counts four, and the specification says a run is "parsed and flattened as
the editor's runs are (each submodel occurrence runs as its definition's
nodes". One spelling works, the flattened graph's own id,
`--node submodel_runtime/loading/capped`. An analyst meets it only in the
progress lines of a run that passes through the box, and here no run does:
the box holds the pipeline's last node. So tidying a canvas into submodels
takes `--node` away for every node inside them, and the message says the
node is not there.

**Plan:** Resolve `--node` against the names an analyst sees. A node inside
a submodel is found by its name when one occurrence holds it, and a submodel
occurrence by its own name, as its first declared output, which is what the
editor previews. A name that fits nodes in several occurrences is refused
listing them, and a name that fits nothing lists the names there are.

**Acceptance:** After the grouping, `haute run --node capped` prints the
rows it printed before. `haute run --node loading` prints the occurrence's
first output, and for a submodel without outputs says so and names the
nodes inside it. A name held by two occurrences is refused naming both.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_run.py::_handle_run_in_project`;
`specs/cli/high-level.md`; `docs/building-models/nodes/submodel.md`.

### CLI-06 — `haute ci update` removes only the CI files Haute wrote
**Why:** Reproduced on 13 November 2026 with the real executable. A project
made by `haute init --target container --ci github` has three workflows:
`ci.yml`, `deploy-staging.yml` and `deploy-production.yml`. The team adds a
workflow of its own, `.github/workflows/release.yml`, and commits it.
`haute ci update` then lists its five files as unchanged, says "The CI
files are up to date." and exits 0. The team's `release.yml` is gone, and
`git status` shows it deleted.

The command holds a fixed list of eight file names, every CI file either
kind of project could have on any provider, and removes each one this run
does not write, "so a switch of provider ... leaves no orphaned workflow
behind". `release.yml` is on the list because a workbench project's
workflow has that name; this project never had one from Haute. The list of
what was removed is not printed. A second `haute init` in the project
removes the file as well, and does list it as removed.
By the same list a workbench project would lose a `deploy-staging.yml` of
its own, and a GitHub project a `.gitlab-ci.yml` or an
`azure-pipelines.yml` it keeps for a mirror (read from the list, not run).
The CLI specification gives the command "the CI files `haute init`
generated" and no others.

**Plan:** Remove a file only when Haute wrote it: keep a record of the CI
files a run generated, in the project, and prune from that record. A file
with a listed name that is not in the record is left where it is. Print
every file a run removes.

**Acceptance:** In the project above, `haute ci update` and a second
`haute init` leave the team's `release.yml` in place. After a real
change of provider, the files the earlier run wrote are removed and each is
named in the output. A run that removed a file does not say the CI files
are up to date.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_init_cmd.py::_prune_stale_ci_files`;
`src/haute/cli/_ci.py::handle_ci_update`;
`src/haute/cli/_init_cmd.py::handle_init`; `specs/cli/high-level.md`.

### CLI-07 — `haute init` on Windows records its pre-commit hook as executable
**Why:** Reproduced on 13 November 2026 with the real executable. After
`haute init` on Windows in a git repository, git records
`.githooks/pre-commit` with mode 100644. The plan's sweep ran the same
command on Linux, where it is 100755; every other file `haute init` writes
is the same byte for byte on the two systems. A project made on Windows and
cloned on Linux or macOS therefore carries a hook file that git will not
run, and a colleague who installs it as the file's own comment says, by
copying it into `.git/hooks`, gets no check of the staged Python.

The command skips setting the mode on Windows, where the file system has no
such bit, and keeps the remedy as a comment in its own source:
`git update-index --chmod=+x .githooks/pre-commit`. Nothing tells the
analyst.

**Plan:** Have `haute init` record the hook as executable in git's index
when it captures the files it wrote, on every system.

**Acceptance:** After `haute init` on Windows, `git ls-files -s` shows
`.githooks/pre-commit` as 100755, and a clone of the project on Linux has
an executable hook.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_init_cmd.py::handle_init`;
`src/haute/_scaffold.py::pre_commit_hook`.
