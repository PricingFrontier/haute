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
| CLI-05 | Planned | P3 | `haute run --node` still finds a node by its name after it is grouped into a submodel. |
| CLI-06 | Planned | P2 | `haute ci update` and a second `haute init` remove only the CI files Haute wrote; a team's own workflow with one of Haute's file names is left, and whatever is removed is named. |
| CLI-11 | Planned | P3 | `haute lint` and `haute run` piped into `head` end quietly, not with "Parse error: Broken pipe". |

## Planned improvements

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
