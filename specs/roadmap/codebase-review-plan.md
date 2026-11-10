# Codebase review plan — the remainder

The method and the order for finding defects in the part of the backend and
frontend that the 9 November 2026 codebase review did not reach. This file
owns no work packages. A reproduced finding becomes a package in its
component's roadmap; this file says what to run, in what order, and what
counts as covered. Remove a phase when it has run and its findings are
written up. Delete the file, its section in the [index](README.md) and its
entry in `_ROADMAP_SUPPORTING_REPORTS` when no phase remains.

Not started. It needs the owner's go-ahead and the [decisions](#decisions)
at the end.

## Where the review stands on 10 November 2026

| | Size | Read in full by the 9 November review | Lines changed since 31 October |
|---|---:|---:|---:|
| Backend, `src/haute` | 254,817 lines in 399 files | about 31% | 62,835 |
| Frontend, `frontend/src` without tests | 143,289 lines in 647 files | about 6% | 29,124 |

The product's surface, which is what this plan covers:

- 21 node types;
- 166 route handlers;
- about 17 commands;
- about 175 API paths called by the browser client;
- the input formats and providers: CSV, Parquet, JSON, NDJSON, Excel, Delta,
  database, lakehouse, Databricks and inline;
- the quote store on SQLite, SQL Server and PostgreSQL;
- every operation that writes: a save, a git operation, a cache publication,
  a model or optimiser artefact, a quote, a release.

By their own reports, three audits read every production file in the week
to 31 October 2026, on 25, 28 and 31 October. The 9 November review read the
riskiest third again. None of the four ran the editor in a browser, ran on
Linux or in a container, or used a real model registry, and scale was
measured in a few places only. About 620 commits have landed since
31 October (base `0bff817d5`). Pull requests are reviewed one diff at a
time, and nobody has looked at the result as a whole.

The 9 November review's 41 packages sort into six kinds: two parts of Haute
applying different rules to one thing (7), an input nobody had tried (9), a
failure hidden or reported wrongly (10), slowness or a hang at scale (5), a
missing check (6), and upkeep of tests and lint (4). Reading found most of
the first 31. After that, more reading found almost nothing, and wrong
inputs and specific questions found the rest.

## Method

Do not read the remainder a fourth time. Cover it by what the product does:
give every node type, reader, route, command, panel and writing operation
wrong inputs made by machine, failures injected on purpose, and a second way
of computing the same answer to compare with. Read only the code changed
since 31 October, the leads already on file, and whatever a sweep points at.

Every sweep sorts each outcome into one of four:

1. the right answer;
2. a refusal that names the node, file or field and says how to fix it;
3. a raw error: a bare Python exception at a boundary, a traceback, an HTTP
   5xx, "unexpected internal error", "check the server logs", or a hang past
   its deadline;
4. a silent difference: two ways of running that must agree and do not, or a
   value changed with nothing said.

The third and fourth are candidates. A candidate is reproduced on its own,
outside the sweep, before it is written up. One that cannot be reproduced is
recorded as checked and dropped.

Working rules:

- The review changes no product code and no tests. A finding is written as
  a package in its component's roadmap, saying whether it was reproduced,
  measured or read from the source, as the 9 November packages do.
- Sweeps, scratch projects and probe files live outside the repository.
- Runs are serial and sized for the development machine. Only targeted
  tests are run, never a full suite.
- The main session judges every finding.

## Phase 1: leads already on file

The 28 October audit left 36 candidates marked unverified, each with the
observation that would confirm it. They are in that audit's report, under
"Unverified and source-screened candidates":
`git show c4a53fe58:specs/roadmap/codebase-review-2026-10-28.md`. Run each
stated experiment at `HEAD`.

Add the two questions the 25 October audit left open, where they are still
open: whether two trace clicks with different columns can share one cache
entry, and whether a saved form can hold a column name the tables route
refuses.

The phase has run when every candidate is a package or is refuted, with the
observation that refuted it.

## Phase 2: the backend, swept by machine

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
  utility modules.
- **Requests against routes.** Start from a valid request for each route.
  Remove, add and retype fields; use unknown identifiers, very large values
  and traversal strings; send duplicates in flight. In process, against a
  scratch project.
- **Arguments against commands.** Every command with wrong arguments, wrong
  paths and a wrong `haute.toml`.

### The same answer every way

- **Every way of running.** One pipeline through preview, trace,
  `haute run`, the deploy scorer, the packaged model, the batch worker and
  workbench pricing, and again after a save and a reload. The rows must be
  identical. Over the 16 shipped examples and over pipelines generated from
  the node types.
- **Changes that must not change the answer.** Row order, how a batch is
  split, chunking on and off, a cold and a warm cache, an unused column
  added, a consistent rename.
- **Independent answers.** Each model library's own prediction, for every
  model family. The optimiser against brute force on problems small enough
  to enumerate. Explore's pivots against plain Polars. A trace's stated
  derivation recomputed from its stated inputs.

### Failures

- **At every step of everything that writes.** Raise at the k-th file or
  subprocess call, for every k, in a save, a config write, each git
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
  or quotes. Anything that grows faster than its input is a candidate, and
  so is each memory peak.

## Phase 3: the frontend

### In a browser

Serve this branch's source on spare ports against scratch projects outside
the repository, and drive it with Playwright. Record console errors, failed
requests and anything left spinning. Do not edit `frontend/src` while it
runs.

- **Fields.** Every field of every node editor takes an awkward value; save;
  reload. The screen and the saved file must agree.
- **Failed calls.** Every API path the client calls fails once, with a 500,
  a timeout and a malformed body, in the journey that uses it. The user must
  be told, the screen must recover, and nothing unsaved may be lost.
- **Order.** Wherever a load follows a selection, hold the first response,
  make a second selection, and release the two in the wrong order. The
  screen must show the second.
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
- Read `stores`, `hooks` and `api` in full: about 23,500 lines, the densest
  logic in the frontend, with 7,300 lines changed since 31 October.

## Phase 4: environments not yet run

- **Linux under WSL.** The code that only runs there (memory caps read from
  the control group, file locking, freshness, signals), and a project
  written on Windows run on Linux: path separators, line endings, case.
- **A real model registry.** A local MLflow tracking server, for loads by
  run, version and alias.
- **Containers**, which need Docker. The underwriter app's image, which none
  of Haute's own workflows builds; a container with a memory limit set; and
  the deploy image on more than the one example the weekly workflow runs.
- **Hosted and Databricks** cannot be exercised without a workspace. They
  stay a stated limit unless one is provided.

## Phase 5: reading where it still pays

- **The diff since 31 October** (`0bff817d5..HEAD`) in the areas not yet
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
- **The scripts CI depends on.**

## Order, size and stopping

Phase 1 first. Then, from phase 2, the same answer every way, wrong values
and files, and failures at every step, because they aim at wrong answers and
lost work. Then phase 3, the rest of phase 2, and phases 4 and 5. The work
can stop after any phase with a statement of coverage.

The whole is roughly 20 to 30 hours of agent time over several sessions.
That is a guess, scaled from the three hours the 9 November review took.

## What the last report states

A table of every node type, reader, route group, command, panel and writing
operation against the sweeps that ran on it; what was checked and found
sound; and what could not be run.

## Decisions

| Decision | Recommendation |
|---|---|
| A second model family (Codex) over the diff since 31 October and over each sweep's candidates | Yes, for the diff. It costs quota. |
| A local server, a browser, and small training and optimiser runs on the development machine, one at a time | Yes. Phases 1 to 3 need nothing else. |
| A clone and a Linux environment inside WSL, which downloads public packages and never touches the Windows `.venv` | Yes. |
| Docker Desktop, for the container items in phase 4 | Decide when phase 4 comes up. |
| Two public tools run without installing them: schemathesis and pyright | Yes. |
| A Databricks workspace, for the hosted app and the Databricks inputs | Provide one, or accept the limit. |
