# Root cause: ways of running a pipeline

## Scope

The same pipeline giving different answers from the preview, a trace,
`haute run`, plain Python, the dry run and the deployed service.

This file is one pull request. It holds one package, `EXE-10`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `EXE-06` | [execution-engine](execution-engine.md) | `pipeline.run()` applies a node's column selection and renames | Resolves |
| `EXE-09` | [execution-engine](execution-engine.md) | A Source Switch input with a non-ASCII name is chosen in plain Python too | Resolves |
| `DPL-08` | [deploy](deploy.md) | The dry run types a test quote as the service types a request | Resolves |
| `DPL-09` | [deploy](deploy.md) | The dry run accepts a test quote that leaves out its exposure | Resolves |
| `EXE-05` | [execution-engine](execution-engine.md) | A Transform that cross-joins a Constant runs outside the editor | In part |
| `IOL-03` | [io-layer](io-layer.md) | A Quote Input on a CSV file runs from the command line | In part |

`EXE-05` and `IOL-03` are differences of memory admission, which the
execution specification allows between a capped and an uncapped process.
This work states those differences and tests them; the planner's reading of
a one-row cross join and the reader's use of a Quote Input's declared types
stay with the two packages.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXE-10 | Planned | P2 | One test runs each pipeline as a preview, a trace, `haute run`, plain Python, the dry run and the service, and requires one answer wherever the ways are meant to agree. |

## Planned improvements

### EXE-10 — Every way of running a pipeline gives the same answer
**Why:** Read on 16 November 2026. A pipeline is run six ways: the editor's
preview, a trace, `haute run`, the file imported and run as Python, the
deploy's dry run, and the deployed service. An earlier roadmap gave the
first three one graph walker. The file run as Python is a second
implementation by design: its decorators do each node's work "through the
same shared helpers". Column selection and renames are applied at four
places in the walker's two modules and at none in that path (`EXE-06`), and
it names a Source Switch's inputs differently (`EXE-09`). The dry run builds
its frame from a test quote with no types where the service types the
request (`DPL-08`, `DPL-09`). Some differences are meant: a preview's row
limit, the nodes a deployment drops, and what a process with a memory cap
admits where one without refuses. Nothing states which differences are
meant, so the ones that are not (`EXE-05`, `IOL-03`) read as more of the
same. Each difference was found by running one pipeline two ways by hand.
No test runs one pipeline every way.

**Plan:** Give "what a configured node does", its shaping and the names it
gives its inputs included, one function that the walker and the decorators
both call. Make the dry run read a test quote through the service's own
request reader. Write down, in the execution specification, the differences
that are meant, each with its reason. Then hold the ways together with one
test, described below, so a difference is found when it is written.

**Acceptance:** A test runs every shipped example, and generated pipelines
that cover each node type and each setting of the Columns tab, all six
ways, with the same inputs, target, source and memory profile. It requires
the same frame from each, in values, types and column order, or the same
refusal in the same words. Each difference that is meant has its own case:
the test shows it and names the line of the specification that allows it,
and a difference with no such line fails. A node type or a way of running
without a row in the test fails it. The acceptance of `EXE-06`, `EXE-09`,
`DPL-08` and `DPL-09` is met.

**Dependencies:** None. Deliver before the packages its table names.

**Evidence:** `src/haute/_standalone_nodes.py`; `src/haute/_execute_lazy.py`;
`src/haute/_graph_walker.py`; `src/haute/deploy/_validators.py`;
`src/haute/deploy/_container.py`; `src/haute/_ram_estimate.py`;
`specs/execution-engine/high-level.md`.
