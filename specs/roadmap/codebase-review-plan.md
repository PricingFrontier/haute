# Codebase review plan — the remainder

The method and the order for finding defects in the part of the backend and
frontend that the 9 November 2026 codebase review did not reach. This file
owns no work packages. A reproduced finding becomes a package in its
component's roadmap; this file says what to run, in what order, and what
counts as covered. Remove a phase when it has run and its findings are
written up. Delete the file, its section in the [index](README.md) and its
entry in `_ROADMAP_SUPPORTING_REPORTS` when no phase remains.

All five phases ran from 10 to 16 November 2026 and are removed from this
file. Their findings are packages in the component roadmaps; what they did
not finish is under [Left from phases 1 to 3](#left-from-phases-1-to-3)
and [Left from phases 4 and 5](#left-from-phases-4-and-5).

## Where the review stands on 12 November 2026

| | Size | Lines changed since the 31 October audit |
|---|---:|---:|
| Backend: Python under `src/haute` | 254,817 lines in 399 files | 55,072 |
| Frontend: TypeScript under `frontend/src`, without tests and generated files | 143,289 lines in 647 files | 23,870 |

Changed lines are added plus deleted lines in
`git diff --numstat edde9e169..HEAD`, where `edde9e169` is the commit the
31 October audit reviewed, counted on 10 November.

Three audits covered the production source in the week to 31 October 2026,
and the 9 November review then read about 31% of the backend and 6% of the
frontend in full. Phases 1 to 3 of this plan did not read the remainder a
fourth time. They covered it by what the product does: every node type,
reader, route and command was given wrong inputs made by machine, writing
operations were failed and killed at each step, results were compared with
answers computed another way, and the editor and the underwriter app were
driven in a real browser. Phases 4 and 5 then ran on Linux and against
database servers, and had a second model family read the changed code.
Nothing ran against Databricks.

## Method

Do not read the remainder a fourth time. Cover it by what the product does:
give every node type, reader, route, command, panel and writing operation
wrong inputs made by machine, failures injected on purpose, and a second way
of computing the same answer to compare with. Read only the code changed
since the last audit, the leads already on file, and whatever a sweep points
at.

### What a sweep states before it runs

- The specification that applies, and the tests that already cover the
  ground, so the sweep does not repeat them.
- A finite list of cases, a baseline of valid inputs that must succeed, and
  for each kind of wrong input the result or the refusal expected.
- How two results are compared: row order, data types, numerical tolerance.
- The runner, the seed, the limits on time, memory and disk, and the
  condition on which it stops.

Pilot a few representative cases before estimating the rest.

### How an outcome is sorted

1. The right answer.
2. A refusal that names the node, file or field and says how to fix it. A
   refusal of a valid input is a candidate, however well it is worded.
3. A raw error: a bare Python exception at a boundary, a traceback, an HTTP
   5xx, "unexpected internal error", "check the server logs", or a hang past
   its deadline.
4. A silent difference: two ways of running that must agree and do not, or a
   value changed with nothing said.

The third and fourth are candidates, with two limits. An outcome the
specification prescribes is not a new candidate: an unexpected failure's
words and incident id are the accepted error-detail policy, and an input
that cannot be prepared says the cause Haute names, keeping the fixed text
only for one it does not (the IO layer's `IOL-01`, 23 November 2026). An injected failure passes when its classification, what it hides,
what it logs and how the operation recovers all match the contract.

Agreement between two callers of one engine proves little. A comparison
needs an answer computed independently, or a change that must not alter the
answer, and that change counts only where its precondition holds: reordering
rows or splitting a batch must leave the answer alone only for a pipeline
the execution specification classes as row-local.

### What happens to a candidate

A candidate is reproduced on its own, outside the sweep, before it is
written up. Each one ends as one of four:

- reproduced, and written as a package;
- already owned, naming the package;
- refuted for a stated trigger, with the observation that refuted it;
- unresolved, naming the evidence or the environment it lacks.

A failed reproduction is not a refutation. Keep the seed, the inputs, the
order of events and what was seen, and control a race with barriers before
concluding anything about it.

### Isolation

Haute keeps training and optimiser artefacts under the machine's temporary
folder, which every project shares, and its git code can push. A scratch
project alone does not contain a sweep.

- Each sweep runs in a child process with its own project, temporary, cache,
  configuration and artefact directories.
- A git sweep uses a repository made for it with a local bare remote, its
  own git configuration and no hooks; never a clone that can reach a real
  remote.
- Service credentials are removed from the environment. Only local services
  chosen for the sweep can be reached.
- A traversal probe aims at sentinel files inside the sweep's own enclosure.
- A fault is injected only into the operation under test, never into the
  observer or the clean-up.
- Every run has a deadline for its process tree and limits on memory and
  disk. It never exhausts the machine.
- Sweeps run one at a time. A concurrency probe uses a fixed number of
  participants and barriers.
- WSL uses its own checkout and environment. A container uses throwaway
  volumes, loopback ports and resource limits, and never a writable mount of
  the owner's checkout.
- Clean-up removes only what the sweep recorded creating.

### Working rules

- The review changes no product code and no tests. A finding is written as
  a package in its component's roadmap, saying whether it was reproduced,
  measured or read from the source, as the 9 November packages do.
- Sweeps, scratch projects and probe files live outside the repository.
- Only targeted tests are run, never a full suite.
- The main session judges every finding. A worker may build and run a
  sweep; it reports outcomes and reproductions, and judges none.

## Left from phases 1 to 3

Phases 1 to 3 ran from 10 to 12 November 2026: 20 sweeps by workers, each
candidate run again by the main session before it was written up, and the
main session's own runs where no sweep reached. Their findings are packages
in the component roadmaps. What they did not finish is here, with what each
item lacks.

### Planned cases that were not reached

- **In a browser.** Most confirmations (charts, utility modules, sources,
  GLM terms, the git panel's other actions, every workbench one); the
  fields of Quote Input, Data Input, Data Output, Model Score, Source
  Switch, Explore, Scenario Expander and the submodel nodes, of Pipeline
  settings, the utility editor and the dialogs, and the Transform steps
  beyond the first few; failed calls beyond save, load, preview, commit
  and the two job polls; a response that never comes; the assistant's
  stream and every other event stream. Around an open editor: pipeline
  settings and constants changed in another tab, a restore in one tab while
  another has unsaved work, the file edited under a drilled-in submodel, a
  restart during an upload or an input build, and a connection lost at the
  moment of a save. In the underwriter app: reopen, amend, alternatives,
  archive and restore, search and paging, a draft after the form changed,
  an old release's page. In the workbench designer: the schema editor, the
  sample priced live, test quotes.
- **Outside a browser.** 103 of the 127 frontend functions listed for the
  logic sweep; the modelling, optimiser and quote-list stores; the pipeline
  repair's node-scoped save and its recovery preview; MLflow logging to a
  local store under failure; the caches under failed writes; every Excel
  case (no reader package on the review's machine); random sequences of
  editor and git operations against a model; sizes beyond 400 nodes, 100
  columns and 800 commits, and any size of rows, rating entries or form
  fields.
- **Answers computed a second way.** The trace's derivations on the shipped
  examples; a ratebook's frontier points; the Quotes explorer's sort, search
  and filters; the optimiser's MLflow log; ratio constraints, which the
  Optimiser node's settings cannot hold.

### Leads without an ending

| Lead | What it lacks |
|---|---|
| A project inside a cloud-synced folder with files on demand: every file may carry the reparse attribute that the path check treats as a link | A machine or a registered sync root where the attribute is set |
| Four leads of the 28 October audit that name no trigger, input or expected answer (its L20, L21, L23, L28) | A trigger |
| Two historical benchmark scripts (L18, L19) | Phase 5 leaves them last |
| One lead that needs macOS (L29) | macOS |
| Explore's profile took 7 seconds at 50 and at 100 columns, after a first run of 45 to 110 seconds | A quiet machine: three other runs shared it |
| A page reloaded during a training run shows a refused memory estimate, nothing of the run, and never its result | A decision: no route lets a page find a node's running job |
| `haute run` prints a traceback after its one-line error when a node's own code raises, as the CLI specification prescribes | A decision |

## Left from phases 4 and 5

Phases 4 and 5 ran from 12 to 16 November 2026. Phase 4 ran the quote
store on SQL Server 2022 and PostgreSQL 16 in Docker, a project made on
Windows on Linux under WSL, and the Linux-only code: memory limits read
from the control group, file locks, freshness and signals. Phase 5 gave a
second model family (Codex) the diff since the 31 October audit, one area
a run; a worker built a run for each candidate it named, and the main
session ran each again. It also read the likeliest findings of the
compilers and linters and the scripts CI depends on. Their findings are
packages in the component roadmaps.

### Planned cases that were not reached

- **Containers.** The sweep was stopped part-way. The underwriter app's
  image and the deploy image for ten examples were built; only the deploy
  image's answers under memory and CPU limits were run (their two findings
  were fixed on 20 November 2026).
- **Database servers under failure.** The sweep of amendment conflicts,
  failed migrations and dropped connections was stopped part-way; its
  findings are `WB-70` to `WB-74`, and what else it planned was not
  recorded.
- **Linux.** Two snapshot builds at once and a build against a clear; the
  git lock across two servers; a kernel older than 6.13, where a file's
  change time is coarser.
- **Hosted and Databricks.** Not run: no workspace.
- **Linters.** About 1,770 of 1,950 findings were not read; the 180
  likeliest held no defect.

### Seen and judged below the bar

- A held lock file that is deleted or replaced on Linux lets a second
  process take the lock at once. It needs the cache folder removed under a
  running server.
- Two servers started with different `TMPDIR` values both hold one
  project's lock. The specification puts the lock in the user's temporary
  directory.
- A trained result is not marked out of date after its data file is
  replaced. The specification defines that mark on the configuration.
- The release's smoke test passes a package that holds no pages; the
  monthly sandbox survey and the mutation lane stay as their owner left
  them.

## Order, budget and stopping

Phases 1 to 3 took about 80 hours of agent time over two days and a
morning, against a first budget of twenty to thirty: each sweep's cost came
from its list of cases and its pilot, as this plan said it would, and every
candidate was run a second time by the main session before it was written
up.

Phases 4 and 5 took four days, most of it workers' time. The reader from
a second model family was the most productive finder: 82 candidates in 22
runs, and nearly every one that was run reproduced.

What is left needs nothing new and can be taken in any order.

A phase has run when every planned case has evidence or a recorded deferral.
The work can stop after any phase with a statement of coverage.

## What the last report states

A table of every node type, reader, registered route, command, panel and
writing operation against the sweeps that ran on it; what was checked and
found sound; every lead and candidate left unresolved, with what it lacks;
and the providers and environments that were not run.

## Decisions

| Decision | Recommendation |
|---|---|
| A Databricks workspace, for the hosted app and the Databricks inputs | Provide one, or accept the limit. |
