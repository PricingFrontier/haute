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

`CLI-09` comes from the review plan's phase 5, a reader from a second
model family (Codex) over the code changed since the 31 October audit. A
worker built its run and the main session ran it again on 14 November
2026.

`CLI-10` and `CLI-11` come from the review plan's phase 4, a sweep
of the server and the commands on Linux (WSL2) under memory limits,
signals and pipes. The main session ran each case again on 15 November
2026.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CLI-03 | Planned | P3 | `haute ci registry-host` refuses an empty registry by name, and a mistyped deploy target is named where it is. |
| CLI-04 | Planned | P3 | The project `haute init` makes passes the lint step of the CI it generates. |
| CLI-05 | Planned | P3 | `haute run --node` still finds a node by its name after it is grouped into a submodel. |
| CLI-06 | Planned | P2 | `haute ci update` and a second `haute init` remove only the CI files Haute wrote; a team's own workflow with one of Haute's file names is left, and whatever is removed is named. |
| CLI-07 | Planned | P3 | `haute init` on Windows records its pre-commit hook as executable, so a clone on Linux or macOS can run it. |
| CLI-09 | Planned | P3 | A second `haute init` lists the `.gitattributes` it writes, and the file is recorded like the others. |
| CLI-11 | Planned | P3 | `haute lint` and `haute run` piped into `head` end quietly, not with "Parse error: Broken pipe". |

## Planned improvements

### CLI-03 — The CI commands name the setting that is wrong
**Why:** Reproduced on 11 November 2026. A workbench project as
`haute init --workbench` leaves it has `registry = ""`. Its generated
release job runs `haute ci registry-host` to log in to the registry before
it pushes the app's image. The command exits 0 and prints nothing, so the
job logs in to an empty host. `haute app build --push` refuses the same
file: "[deploy.container] registry is empty: name the registry the app's
image is pushed to in haute.toml."

One more answer names the wrong place. With `[deploy] target` mistyped as
`databrick`, `haute ci update` answers "Unknown target: databrick in [ci]
provider of haute.toml.", though the `[ci]` table is right.

**Plan:** Have `haute ci registry-host` refuse an empty registry in the
words `haute app build --push` uses. Name `[deploy] target` in the unknown
target's message.

**Acceptance:** `haute ci registry-host` on a project with no registry exits
non-zero naming `[deploy.container] registry`. A mistyped `[deploy] target`
is named as it is written.

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

**Evidence:** `src/haute/cli/_run.py::_run_in_project`;
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

### CLI-09 — A second `haute init` lists and records the `.gitattributes` it writes
**Why:** Reproduced on 14 November 2026. In a project whose
`.gitattributes` is gone (deleted, or made before Haute wrote one),
`haute init` run again writes the file. Its summary lists every other
generated file and not this one. On a working branch the editor then has
no pending save for it, and Commit answers "No new saves to commit — the
working branch is up to date." while `git status` shows the file
untracked. The specification has every generated, untracked file recorded
as a save or named as not captured.

**Plan:** Put `.gitattributes` in the list of files the command wrote, so
it is reported and captured like the others.

**Acceptance:** After the steps above the summary names `.gitattributes`
as written and the file is in the working branch's history, or is named as
not captured.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_init_cmd.py`; `specs/cli/high-level.md`.

### CLI-11 — A command piped into `head` ends quietly
**Why:** Reproduced on 15 November 2026 on Linux with a valid two-node
project. `haute lint | head -1` prints "✗ Parse error: [Errno 32] Broken
pipe", then Python's "Exception ignored ... BrokenPipeError" notice, and
exits 120. `haute run --node rated | head -1` prints "Error parsing
pipeline: [Errno 32] Broken pipe" the same way. Nothing is wrong with the
pipeline: the reader closed the pipe, the parser's next log line to
standard output raised an `OSError`, and both commands catch `OSError`
around the parse and call it a parse error. `| less` quit early does the
same.

**Plan:** Handle a closed standard output once, at the command group: end
without a message and without the interpreter's notice. Keep the parse
handlers from naming it a parse error.

**Acceptance:** Both commands above print the one line `head` asked for,
write nothing to standard error, and never print "Parse error".

**Dependencies:** None.

**Evidence:** `src/haute/cli/_lint.py`; `src/haute/cli/_run.py`;
`src/haute/cli/_helpers.py`.
