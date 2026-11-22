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

