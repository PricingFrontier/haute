# Codebase review plan — the remainder

The method and the order for finding defects in the part of the backend and
frontend that the 9 November 2026 codebase review did not reach. This file
owns no work packages. A reproduced finding becomes a package in its
component's roadmap; this file says what to run, in what order, and what
counts as covered. Remove a phase when it has run and its findings are
written up. Delete the file, its section in the [index](README.md) and its
entry in `_ROADMAP_SUPPORTING_REPORTS` when no phase remains.

Phases 1 to 3 ran on 10 and 11 November 2026 and are removed from this
file. Their findings are packages in the component roadmaps; what they did
not finish is under [Left from phases 1 to 3](#left-from-phases-1-to-3).
Phases 4 and 5 wait on the [decisions](#decisions) at the end.

## Where the review stands on 11 November 2026

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
driven in a real browser. None of it ran on Linux, in a container, against
a database server or against Databricks, which is phase 4.

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
specification prescribes is not a new candidate: the optimiser's fixed text
for an unexpected failure is the accepted error-detail policy, and `SRV-01`
and `IOL-01` already carry the question of whether such texts should say
more. An injected failure passes when its classification, what it hides,
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

Phases 1 to 3 ran on 10 and 11 November 2026: 19 sweeps by workers, each
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
  case (no reader package on the review's machine); t-boost beyond two fits;
  random sequences of editor and git operations against a model; sizes
  beyond 400 nodes, 100 columns and 800 commits, and any size of rows,
  rating entries or form fields.
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
| The Solve pane read "Did not converge in 20 iterations" beside a preview reading "Converged, 37 iterations" | A second look: probably the two solving methods of `OPM-02` |
| What the browser's client saves from the empty canvas of a mistyped `haute.toml` (`PCF-02`) | A browser run |
| A page reloaded during a training run shows a refused memory estimate, nothing of the run, and never its result | A decision: no route lets a page find a node's running job |
| `haute run` prints a traceback after its one-line error when a node's own code raises, as the CLI specification prescribes | A decision |

## Phase 4: environments not yet run

- **Linux under WSL.** The code that only runs there (memory caps read from
  the control group, file locking, freshness, signals), and a project
  written on Windows run on Linux: path separators, line endings, case.
- **Database servers.** The quote store's amendment conflicts, failed
  migrations and dropped connections on SQL Server and PostgreSQL, through
  the fixtures CI uses (`HAUTE_TEST_SQLSERVER_URL`,
  `HAUTE_TEST_POSTGRESQL_URL`). A provider with no server available is
  recorded as not run; SQLite alone is not coverage of the other two.
- **Containers**, which need Docker. The underwriter app's image, which none
  of Haute's own workflows builds; a container with a memory limit set; and
  the deploy image on more than the one example the weekly workflow runs.
- **Hosted and Databricks** cannot be exercised without a workspace. They
  stay a stated limit unless one is provided.

## Phase 5: reading where it still pays

- **The diff since the last audit** (`edde9e169..HEAD`) in the areas not yet
  read: the assistant, the optimiser, expressions and lineage, the execution
  engine, and the frontend's workbench, editors, utilities and types.
- **Questions asked of the whole codebase.** For every cache, what is in its
  key against what changes its answer. Every rule written twice. Every
  dispatch over node type, job status or error code that could miss a
  member.
- **Compilers and linters as finders.** TypeScript with unchecked-index
  checking, and the type-aware ESLint rules for exhaustive switches, values
  in template strings and conditions that cannot vary; pyright and mypy's
  unreachable-code warning over the backend; a linter over the workflows.
  Their output is read for defects; clearing warnings is not part of the
  review.
- **The scripts CI depends on.** The historical benchmark scripts wait.

## Order, budget and stopping

Phases 1 to 3 took about 77 hours of agent time over two days, against a
first budget of twenty to thirty: each sweep's cost came from its list of
cases and its pilot, as this plan said it would, and every candidate was
run a second time by the main session before it was written up.

Of what is left, the items under [Left from phases 1 to
3](#left-from-phases-1-to-3) need nothing new and can be taken first, in
any order. Phases 4 and 5 follow, each once its decision is made.

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
| A second model family (Codex) over the diff since the last audit and over each sweep's candidates | Yes, for the diff. It costs quota. |
| A local server, a browser, a local MLflow server, and small training and optimiser runs on the development machine, one at a time | Agreed on 10 November 2026. What is left from phases 1 to 3 needs nothing else. |
| A clone and a Linux environment inside WSL, which downloads public packages and never touches the Windows `.venv` | Yes. |
| Docker Desktop, for the container items in phase 4 and for a SQL Server | Decide when phase 4 comes up. PostgreSQL can be installed inside WSL without it. |
| Two public tools run without installing them: schemathesis and pyright | Yes. |
| A Databricks workspace, for the hosted app and the Databricks inputs | Provide one, or accept the limit. |
