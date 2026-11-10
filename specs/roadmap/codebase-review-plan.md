# Codebase review plan — the remainder

The method and the order for finding defects in the part of the backend and
frontend that the 9 November 2026 codebase review did not reach. This file
owns no work packages. A reproduced finding becomes a package in its
component's roadmap; this file says what to run, in what order, and what
counts as covered. Remove a phase when it has run and its findings are
written up. Delete the file, its section in the [index](README.md) and its
entry in `_ROADMAP_SUPPORTING_REPORTS` when no phase remains.

Phases 1 to 3 have the owner's go-ahead and are under way. Phases 4 and 5
wait on the [decisions](#decisions) at the end.

## Where the review stands on 10 November 2026

| | Size | Lines changed since the 31 October audit |
|---|---:|---:|
| Backend: Python under `src/haute` | 254,817 lines in 399 files | 55,072 |
| Frontend: TypeScript under `frontend/src`, without tests and generated files | 143,289 lines in 647 files | 23,870 |

Changed lines are added plus deleted lines in
`git diff --numstat edde9e169..HEAD`, where `edde9e169` is the commit the
31 October audit reviewed. 616 commits have landed since it, not counting
this review's own.

The product's surface, which is what this plan covers:

- 21 node types;
- 166 route decorators, the routes registered directly (the session routes
  and the underwriter app's quote changes), the `/ws/sync` WebSocket and the
  event streams;
- 17 commands;
- about 140 distinct API path literals in the browser client, before the
  paths its helpers build;
- the input formats and providers: CSV, Parquet, JSON, NDJSON, XML, Excel,
  Delta, database, lakehouse, Databricks and inline;
- the quote store on SQLite, SQL Server and PostgreSQL;
- every operation that writes: a save, a git operation, a cache publication,
  a model or optimiser artefact, a quote, a release.

Three audits covered the production source in the week to 31 October 2026.
The 25 October audit read each area in the main session or through a
reviewer, and lists its partial and mechanical reads. The 28 October audit
made an inventory-wide source pass, which it says does not establish that
every statement was inspected. The 31 October audit reports a file-by-file
pass over all 961 production files at `edde9e169`. The 9 November review
then read about 31% of the backend and 6% of the frontend in full, by its
own ledger, which is kept outside the repository. By their reports, none of
the four ran the editor in a browser, ran on Linux or in a container, or
used an external model registry, and scale was measured in a few places
only. Pull requests are reviewed one diff at a time; no audit of the whole
codebase has covered the changes since.

By the 9 November review's own sorting, its 41 packages are of six kinds:
two parts of Haute applying different rules to one thing (7), an input
nobody had tried (9), a failure hidden or reported wrongly (10), slowness or
a hang at scale (5), a missing check (6), and upkeep of tests and lint (4).
Reading found most of the first 31. After that, more reading found almost
nothing, and wrong inputs and specific questions found the rest.

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

## Phase 1: leads already on file

The 28 October audit left 36 candidates marked unverified, each with the
observation that would confirm it. They are in that audit's report, under
"Unverified and source-screened candidates":
`git show c4a53fe58:specs/roadmap/codebase-review-2026-10-28.md`. The
25 October audit adds one: a saved form may hold a column name the tables
route refuses.

First reconcile the leads with the current packages and with the regression
tests added since, merging duplicates. Some are already owned: the retry
policy is `FES-01` in [frontend shared](frontend-shared.md), and the quote
list's pagination is `WB-41` in [workbench](workbench.md). Several name no
trigger, and one needs macOS. Then run the stated experiment for each lead
that is left, at `HEAD`.

The phase has run when every distinct lead has one of the four endings above
and a next action, deferrals included. Leads left unresolved stay in this
plan, with what each lacks.

## Phase 2: the backend, swept by machine

Build the inventories first: the node types; the readers; the method and
path pairs both applications register, with the routes added directly, the
WebSocket and the event streams; the commands. A handler that parses its own
body, as the underwriter app's quote changes do, needs valid bodies supplied
as seeds, because a schema does not describe them.

### The same answer every way

- **Composed pipelines.** Small generated graphs with fan-out, joins,
  selectors and reused submodels, compared with values, schemas and row
  counts computed independently; through projection, a cold and a warm
  cache, and a save and a reload. The single operations already have proofs
  in the tests, so the sweep aims at their combinations.
- **Every way of running.** One pipeline through preview, trace,
  `haute run`, the deploy scorer, the packaged model, the batch worker and
  workbench pricing. The rows must be identical. Each shipped example
  records which of these it can enter and what it needs first: one has no
  request input and cannot be deployed, and one scores a model that has to
  be trained before it exists.
- **Changes that must not change the answer**, where the precondition holds:
  row order, how a batch is split, chunking on and off, an unused column
  added, a consistent rename.
- **Independent answers.** Each model library's own prediction, for every
  model family and every level of a grouped category. The optimiser against
  brute force on problems small enough to enumerate. Explore's pivots
  against plain Polars. A trace's stated derivation recomputed from its
  stated inputs. What JSON and XML inference declares against what the
  runtime then reads.
- **A model's whole life, on a real registry.** A local MLflow tracking
  server needs no decision. Fit small models, register them, load by run,
  version and alias, change the alias or the source, restart, and compare
  the library's own predictions with what Haute restores and scores.

### Wrong inputs

- **Values against node types.** A fixed set of awkward values for each data
  type, run through one minimal graph per node type: null, empty,
  whitespace, leading zeros, NaN and the infinities, integers past 2^53,
  non-ASCII text, very long text, boundary dates, time zones, high-scale
  decimals, booleans as text, mixed types, duplicate keys.
- **Files against readers.** A byte-order mark, CRLF, an empty file, a
  header alone, a truncated file, a wrong encoding, duplicate headers or
  keys, ragged rows, deep nesting, unusual Parquet types, and files written
  by older library versions; against every input format, Load File, the
  config sidecars, `haute.toml`, hand-written forms of a pipeline file, and
  utility modules. For XML: one child against repeated children,
  namespaces, empty elements, colliding names, and declarations the reader
  must refuse; through inference, shredding and a served request.
- **Requests against routes.** Start from a valid request for each
  registered method and path. Remove, add and retype fields; use unknown
  identifiers, very large values and traversal strings; send duplicates in
  flight.
- **Arguments against commands.** Every command with wrong arguments, wrong
  paths and a wrong `haute.toml`.

### Failures

- **Where an operation writes.** Inject a failure at each logical step
  (stage, replace, commit, publish), and at every file or subprocess call
  where the operation makes few enough to enumerate; then kill and restart
  the process at a chosen few. In a save, a config write, each git
  operation, a snapshot or source-cache publication and eviction, a model or
  optimiser artefact, a quote store write and migration, a release, and a
  scaffold run a second time. After each: the old state or the new, never
  half; no orphaned files; locks released; the next attempt succeeds; the
  message names the cause.
- **Of the real kinds, at every external call.** The file system, the git
  subprocess, MLflow, a database, a worker that dies, memory. This finishes
  over the whole backend the three sweeps the 9 November review began: what
  else reaches each broad `except`, what each delete or overwrite removes
  when something fails, and what each lock guards and whether it holds
  across processes.

### Load

- **Two at once.** Two saves, two builds of one input, a cancel during a
  build, a job finishing late, the server and `haute run` on one project,
  two amendments of one quote. Random sequences of editor and git operations
  through the HTTP API, checked against a plain model of what should result.
- **Size.** Each operation at n, 2n, 4n and 8n nodes, columns, rows, commits
  or quotes, judged against how it should grow and against a practical
  budget. Growth the algorithm or the size of the output requires is not a
  finding.

## Phase 3: the frontend

### In a browser

Serve this branch's source on spare ports against scratch projects outside
the repository, and drive it with Playwright. Record console errors, failed
requests and anything left spinning. Do not edit `frontend/src` while it
runs.

- **State that outlives its moment**, first. Hold a destructive confirmation
  open across an unrelated edit, then confirm. Complete two settings loads
  out of order, then fail a save and see what is restored. Replace a
  modelling result under a view that stays mounted. Wherever a load follows
  a selection, hold the first response, make a second selection, and release
  the two in the wrong order; the screen must show the second. Cancel an
  event stream part-way, and end one without its terminal event.
- **Fields.** Every field of every node editor takes an awkward value; save;
  reload. The screen and the saved file must agree.
- **Failed calls.** Each method and path the client calls fails once, with a
  500, a timeout and a malformed body, in a journey that uses it. The user
  must be told, the screen must recover, and nothing unsaved may be lost.
- **Around the editor.** Two tabs on one pipeline, a file edited underneath,
  the server restarted, the connection dropped and restored, a refresh in
  the middle of an operation.
- **The workbench and the underwriter app**, with a quote store.

### Outside the browser

- Bundle the logic that has no screen (`utils`, `stores`, the API client,
  the type guards) and run it under Node with the same awkward values, and
  with random sequences of edits: an undo and a redo must return the same
  graph.
- Wherever one rule lives on both sides (parsing a number or a date, names,
  visibility, the lists of statuses and error codes), run both and compare.
- Read in `stores`, `hooks` and `api` what the sweeps point at and what has
  changed most: they hold 23,514 lines, with 6,338 added or deleted since
  the last audit. Reading is not a precondition for the sweeps.

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

Phase 1 first. Then, from phase 2, the same answer every way and a model's
whole life, then wrong values and files, then failures where an operation
writes: they aim at wrong answers and lost work. Then phase 3, starting with
state that outlives its moment. Then the rest of phase 2, and phases 4
and 5.

A phase has run when every planned case has evidence or a recorded deferral.
The work can stop after any phase with a statement of coverage.

Twenty to thirty hours of agent time is the first budget, over several
sessions. It is not an estimate of what the whole plan costs: that comes
from each sweep's list of cases and its pilot.

## What the last report states

A table of every node type, reader, registered route, command, panel and
writing operation against the sweeps that ran on it; what was checked and
found sound; every lead and candidate left unresolved, with what it lacks;
and the providers and environments that were not run.

## Decisions

| Decision | Recommendation |
|---|---|
| A second model family (Codex) over the diff since the last audit and over each sweep's candidates | Yes, for the diff. It costs quota. |
| A local server, a browser, a local MLflow server, and small training and optimiser runs on the development machine, one at a time | Agreed for phases 1 to 3, which need nothing else. |
| A clone and a Linux environment inside WSL, which downloads public packages and never touches the Windows `.venv` | Yes. |
| Docker Desktop, for the container items in phase 4 and for a SQL Server | Decide when phase 4 comes up. PostgreSQL can be installed inside WSL without it. |
| Two public tools run without installing them: schemathesis and pyright | Yes. |
| A Databricks workspace, for the hosted app and the Databricks inputs | Provide one, or accept the limit. |
