# Codebase review — 25 October 2026

A whole-repository review made at `main` `859548a4d` (the merge of pull request
#322) on the branch `codebase-review-2026-10-25`, before Haute's first production
use. It looked for defects, inconsistencies, duplicated and dead code,
over-complicated and fragile solutions, and anything else that could cause
trouble in production, with particular attention to the deployment and serving
code. It is a dated supporting report: it records the method, its limits, the
evidence and the probes, and maps every finding to the roadmap package that
tracks it. It owns no work; the component roadmaps carry the plans.

## Method

1. **Specifications first.** Each area's high- and low-level specification was
   read before its code, so that drift between the two counts as a finding
   (either the code or the specification is wrong) and a documented deliberate
   choice is not reported as a defect.
2. **Mechanical candidates.** radon (cyclomatic complexity: 285 functions at or
   above 20, 27 at or above 50), vulture at 60 % confidence, jscpd over the
   Python (0.30 %, 41 clones) and TypeScript (0.34 %, 29 clones) sources, knip
   over the frontend (161 unused exports, 256 unused exported types, one
   unlisted dependency), git churn over the last three months, and forty
   regular-expression sweeps (bare excepts, swallowed errors, floating action
   tags, `cwd`-relative paths, `sys.path` edits, test-only hooks in production
   modules). Every mechanical hit was read before it became a finding; most were
   not findings.
3. **Production code read in full by the main session.** Every file under
   `src/haute/deploy`, `src/haute/cli`, the scaffold, the server shell and its
   local security, the hosted project storage and UC transport, the Databricks
   app, the sandbox, the underwriter app and quote store (every migration), the
   rating step, the Workbench output, input, tables, params and config modules
   and their route, the output assembler, the JSON shredder, the three
   production workflows, `pyproject.toml`, the build hook and the release
   scripts; the exceptions (one partial read, the secondary workflows by sweeps
   only, five modules by targeted greps) are in the coverage table.
4. **Area reviewers.** The rest of the repository was split into areas, each
   read by one reviewer at a time (two in flight at most) under one protocol:
   read-only, no git state changes, no full test suites, a fixed finding row
   (severity, location at `HEAD` line numbers, claim, evidence, proposed fix,
   specification), a list of what was already tracked, a coverage table and
   probes kept outside the repository.
5. **Verification at source.** The main session read every reviewer finding at
   its cited lines before accepting it and reran the probes behind the decisive
   ones (memory peaks, written row counts, estimate bytes, shutdown joins). The
   verdicts (confirmed, accepted on the reviewer's evidence, downgraded,
   unconfirmed) decided each finding's place below.
6. **Severity** follows the engineering guide: Critical (a security hole, data
   corruption or silently wrong results, a breaking interface, a sandbox or auth
   bypass), Major, Minor, Suggestion. Package priority follows the roadmap
   README: P1 threatens persisted work, computed results or security; P2 a
   material correctness, UX or maintenance issue; P3 opportunistic.
7. **No second model family.** At the owner's decision this review used no Codex
   passes; the roadmap files it produced go through the usual Codex plan review
   before the branch is merged.

## Limits

- Probes ran on Windows 11 under Polars 2.0.0. The Linux cgroup and `RLIMIT_AS`
  paths, the POSIX freshness revision and the container image were read, not
  executed.
- No test suite was run in full. Each probe was a standalone script against a
  scratch copy of the demo project or a synthetic graph.
- Line numbers in the reviewers' tables are at `859548a4d` and will drift; the
  packages cite symbols, not lines.
- One finding is kept with its reachability unconfirmed (a request-input edge
  with a null handle, `EXEC-R14`); the code path is real, whether the editor
  can produce the input is not shown.
- The modelling reviewer scanned `_charts.py` mechanically and did not read
  the modelling test modules systematically, so "a test that would pass
  against a wrong coefficient" is answered only for the GLM level check.
- One trace question is open and unprobed: the execution strategy is planned
  with the clicked row's columns while the cache key carries no column set, so
  two clicks with different `row_values` columns may share one cache entry built
  from the first click's API-input port columns.
- The assistant-core reviewer read the assistant low-level specification's core
  sections and left the tool and op sections to the tools reviewer; the loop's
  tool-summary wording was not checked against the specification.
- Areas whose findings are not yet in this report are in review: the trace, git
  and assistant UI, the workbench UI, the app shell and canvas; the tests,
  scripts and CI as code.

## Coverage and findings so far

| Area | Read by | Coverage | Findings (Critical / Major / Minor / Suggestion) |
|---|---|---|---|
| Deploy, CLI, scaffold, server shell, hosted storage, Databricks app, sandbox, underwriter app and quote store, rating, Workbench modules, workflows, build and release | main session | every file in full except `scripts/container_smoke.py` (first 120 lines) and the seven secondary workflows (sweeps only); `_global_constants.py`, `_graph_utils.py`, `_execution_admission.py`, `_mlflow_io.py` and `_model_flavors.py` by targeted greps (the last two are the modelling reviewer's) | 0 / 4 / 38 / 8 |
| Execution core (walker, executor, lazy execution, pipeline, contexts) | reviewer, verified | full | 0 / 2 / 10 / 3 |
| Caching and IO (source cache, node snapshots, providers, data points, input preparation, registry) | reviewer, verified; the Critical reproduced by the main session | full | 1 / 3 / 4 / 3 |
| Static analysis (projection, lineage, RAM estimate, chunk classifier, cardinality) | reviewer, verified; two probes rerun | full | 0 / 3 / 5 / 0 |
| Workers and jobs (isolation, protocol, pool, dedicated worker, caps, job store, timeouts) | reviewer, verified; one probe rerun | full | 0 / 4 / 10 / 0 |
| Server routes and the save service (pipeline, save, helpers, node data, project files, utility, banding and rating, submodel, error handlers, supersession) | reviewer, verified; two probes | every owned file in full; `schemas.py` the models those routes use | 0 / 4 / 10 / 3 |
| Pipeline config, codegen, parsing and recovery | reviewer, verified; two probes rerun | core files in full | 0 / 3 / 10 / 2 |
| Pipeline config, second pass (Polars steps, the recovery loader, names, global constants, layout) | reviewer, verified; one probe rerun | every owned file in full | 0 / 1 / 7 / 0 |
| Optimiser (routes, service, solver, session, frontier, auto-range, estimate, outcomes, artifacts, explainability, the price-contour guard, the Optimiser Apply runtime) | reviewer, verified; two probes | every owned file in full; the price-contour library read where haute's schemas depend on it | 0 / 1 / 9 / 2 |
| Modelling, the MLflow model registry and the training routes (every family, contracts, evaluation, tuning, export, MLflow IO, the scorer, explainability, the training lifecycle) | reviewer, verified; the GLM probe rerun | every owned file in full except `_charts.py` (mechanical) | 0 / 2 / 21 / 2 |
| Tracing and expression parsing (trace, correlation, enrichment, lineage, waterfall, the expression parser) | reviewer, verified; the parser probe rerun | every owned file in full | 0 / 2 / 9 / 3 |
| Git integration and Databricks IO (every `_git*` module, the git route, credentials, SQL IO, the output writer, the browsing route) | reviewer, verified; two probes | every owned file in full | 0 / 1 / 12 / 1 |
| Assistant core (application, loop, sessions, providers, configuration, rendering, project knowledge, the route) | reviewer, verified | every owned file in full; the specification's core sections | 0 / 1 / 8 / 5 |
| Assistant tools (tools, ops, data checks, catalog, recipes, assets) | reviewer, verified; two timing probes | every owned file in full; the specification's tool, op and data-check sections | 0 / 2 / 6 / 4 |
| JSON shredding (records, shred, writer, source proof, inference, caches, the path grammar, the Edge Join, the JSON-cache route) | reviewer, verified; the Critical reproduced by the main session | every owned file in full | 1 / 2 / 8 / 1 |
| Submodels and Explore / EDA (instances, paths, flatten, the submodel routes, pivots, profiles, relationships, charts) | reviewer, verified; one probe rerun | every owned file in full | 0 / 0 / 10 / 2 |
| Frontend API client, wire types and guards, stores, generated contracts | reviewer, verified | every owned file in full; a client-path against server-route probe | 0 / 1 / 7 / 4 |
| Frontend node editors, top-level files (45 files) | reviewer, verified | every owned file in full except `CodeMirrorEditor.tsx` lines 1-235 (grep) | 0 / 0 / 16 / 1 |
| Frontend hooks and utilities (36 hooks, 78 utilities; the sheet-visibility mirror compared rule by rule with the server) | reviewer, verified | every owned file in full | 0 / 1 / 13 / 4 |

| Frontend node editors, subdirectories (banding, rating, Polars steps, Explore pivots, shared; 43 files) | reviewer, verified; the validator-versus-writer probe rerun | every owned file in full | 0 / 1 / 11 / 3 |
| Frontend modelling panels (training configuration, results workspace, export) | reviewer, verified | every owned file in full except the stylesheet | 0 / 3 / 8 / 4 |

| Frontend optimiser and Explore panels (the optimiser workspace, the preview frame, pivots, charts, overview, relationships) | reviewer, verified | every owned file in full except `UtilityPanel.tsx` | 0 / 2 / 8 / 2 |

Three hundred and forty-two findings so far: two Critical, forty-three Major, two
hundred and forty Minor and fifty-seven Suggestions. Both Criticals and every Major are confirmed at source by the
main session; the Minors and Suggestions were read at source or accepted on
quoted evidence as the verification table records.

## The Criticals and the Majors

- **A glob or folder Data Input is invisible to the freshness proofs** (Critical,
  `IO-R03`). `source_signature` answers `"missing"` for a path that is not one
  file and the runtime fingerprint answers `{"exists": False}`, so the snapshot's
  signature and the live one are always equal and the snapshot is fresh forever.
  Reproduced: three rows summing to 60, one file rewritten, the live scan gives
  2028 while the snapshot stays fresh. A parquet glob also cannot be deployed
  (it is "direct", then refused as absent) while a CSV glob's stale snapshot is
  bundled.
- **A value that is not a list at a declared array key yields no child rows, no
  skip count and no error** (Critical, `JSON-R01`; reproduced in the build walk
  and in the deployed request path). XML's singleton rule makes it routine: a
  quote with one `<driver>` loses its driver row while a quote with two keeps
  both, and the inferred schema for such a file fails its own build.
- **A served flat Quote Input is typed by inference, not by the manifest**
  (Major, `DEP-R05`): `30` and `30.0` give different dtypes, a `null` gives a
  Null column, so a cast, a Banding level or a categorical feature can differ
  from the dry run.
- **The Databricks model's environment pins three distributions** (Major,
  `DEP-R06`): the model engines resolve at Model Serving build time, which the
  container path refuses for exactly this reason.
- **Most MLflow-sourced models deploy without their training contract** (Major,
  `DEP-R07`): the bundler looks only for a bare contract file beside the
  download; the served scorer runs unchecked while the editor binds the run's
  contract.
- **`haute init --force` overwrites the analyst's pipeline, utility code, tests
  and form** (Major, `CLI-R01`), and a first `init` deletes a root `main.py` it
  did not write.
- **`haute run` is a full eager preview** (Major, `CLI-R02`): every node is
  collected into memory at once under the preview profile.
- **A display walk caches every fan-out input** (Major, `EXEC-R11`): 610 MB peak
  against 329–365 MB without the cache, same results.
- **A column-free `select` is classified row-local** (Major, `EXEC-R09`): an
  input-sliced write yields one row per part (native height 1, written 3). The
  package is P1 because the written generation is wrong and read as fresh.
- **The RAM estimate reads widths from the nearest source and matches dtype
  tokens** (two Majors, `EXEC-R10`): a widened graph estimates 48,000 bytes
  against 4,016,000 actual.
- **A vanished generation is reported corrupt; a generation is removed with a
  bare `rmtree` under the lock; the registry and the engine anchor relative
  paths differently** (three Majors, `IO-R04`, `IO-R05`).
- **The one-shot and protocol watchdogs measure a bare interpreter** (Major,
  `EXEC-R13`): 51 MiB tighter than the native cap under a 256 MiB budget.
- **A graceful server exit joins every in-flight worker** (Major,
  `ROAD-WORKER-06`): a 6 s child in flight at a 2 s exit ends the process at
  7.6 s; no job registry is cancelled at shutdown.
- **A remote user error replaces the warm worker; one failed replacement
  disables the slot for the life of the server** (two Majors, `EXEC-R12`).
- **Every save round-trips through the file watcher** (Major, `API-R08`): the
  sidecar write and the stale-config unlink carry no self-write marker, so the
  saved document is reloaded and broadcast as an external edit.
- **A graph the flattener refuses, and a trace that fails in the user's node
  code, are answered with the sanitized 500** (two Majors, `API-R06`); two tests
  pin the 500.
- **Utility files are written non-atomically, unlocked and without a revision**
  (Major, `API-R07`): a second editor's PUT silently wins and a concurrent
  preview can import a truncated file.
- **A non-UTF-8 sidecar or pipeline file is loaded with replacement characters
  that the next save writes over the author's bytes** (Major, `PCFG-R10`;
  reproduced). The package is P1 because the corruption is silent.
- **Inline decorator keywords on a sidecar-backed node are dropped by the parse
  and merged over the sidecar by Recover** (Major, `PCFG-R11`).
- **Every save drops authored comments outside the kept regions** (Major,
  `PCFG-R12`; five kinds reproduced) with no diagnostic.
- **A closing `return df` on the same line as a statement drops the statement**
  (Major, `PCFG-R17`; reproduced): the node loads empty and the next save writes
  a placeholder over the authored body. The package is P1.
- **The optimiser's MLflow log writes the complete result artifact without the
  finiteness gate the save applies** (Major, `OPT-R01`): a missing defence layer
  on a pricing artifact, since the status poll corrects the UI path.
- **A GLM prices an unseen or null categorical level as the reference level**
  (Major, `MOD-R01`; reproduced): the GLM contract records no fitted levels and
  the scorer knows no GLM categoricals, so a new region code or a typo at
  scoring time is a silently base-level price. The package is P1.
- **Cancelling a dispersion estimate during preparation cancels nothing**
  (Major, `MOD-R02`): the token is registered after preparation, so the job
  reads `cancelled` and frees the running slot while the preparation child
  keeps running under the job's budget.
- **A trace's input sources name the nearest step in declaration order, not
  the node whose value the row carries** (Major, `TRACE-R01`; reproduced): on a
  join with a shared non-key column the join side is shown as the source of a
  base-side value, flipping with node order.
- **`.over(` anywhere in a node's code types every column of the node as a
  window** (Major, `TRACE-R02`; reproduced): `y = a * 2` is shown as "rank of x
  over g" with the right value.
- **Move, Catch up and Spin off a copy delete the project's `outputs/` folder**
  (Major, `GIT-R01`, a decision): the specification lists it among the wiped
  caches, but it is the documented local folder of analyst results, which git
  cannot restore.
- **The assistant's SDK clients are built without the validated endpoint**
  (Major, `ASSIST-54`): `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` in the
  server's environment redirects the key, the prompt and project material to a
  host the endpoint policy never checked.
- **`inspect_node` holds the project's save lock while profiling up to a million
  rows, and the capability manifest is rebuilt on every call** (two Majors,
  `ASSIST-57`): every save, write and other server process on the project waits
  for the profile; 47 ms of descriptor construction per tool result on the
  event-loop thread.
- **The root JSON array tokeniser reads one byte per Python call** (Major,
  `JSON-R02`): forty-one times slower than the same records as JSONL; a 1 GB
  root array tokenises for about three minutes before any row is walked.
- **A WebSocket reconnect after a save discards every running job's tracking
  and the undo history** (Major, `FSH-R05`): the save stores no document
  fingerprint, so the resync is answered with a full document the client takes
  for an external change.
- **A banding rule the grid leaves unmarked fails the save with a 500** (Major,
  `FNED-R04`): the save validator accepts a blank band name, a blank categorical
  value or a repeated value, the sidecar writer refuses them, and the writer's
  error has no HTTP handler.
- **The Train pane's VRAM figure is rescaled from the wrong row basis, the Lift
  tab's Gini is recomputed in the browser, and the Gamma GLM family writes the
  wrong default metric** (three Majors, `FMOU-R01`, `FMOU-R02`).
- **Saving the pipeline cancels an auto-range run in flight, and a percentage
  constraint is shown as "at least 0" and rewritten as an absolute bound on
  edit** (two Majors, `FMOU-R04`).
- **Ctrl+S with the cursor in a code box saves without the last keystrokes**
  (Major, `FSH-R07`): the save helper blurs only text fields, the code editor's
  debounced commit flushes only on blur, and the toolbar shows "Saved" while the
  canvas re-dirties with no indicator.

## Design choices the review asks the owner to confirm

Four behaviours are specified and implemented as specified, and the review
still puts them in front of the owner because of what they can cost in
production:

- A move, catch-up or spin-off wipes `outputs/` (`GIT-R01`).
- The assistant's dry run executes model-produced Python in the server process
  (the planned preamble, free-code steps and node bodies are compiled and run
  for their schemas, and a planned Load File's pickle is deserialised) before
  anything is saved; the specification says so openly and gates it by the
  egress ceiling, so a prompt-injected project document or a misbehaving model
  can run code on the server without saving. No package is filed; the choice
  is the owner's.
- `haute run` executes the whole pipeline eagerly in memory (`CLI-R02`).
- Automatic ledger saves run the project's git hooks (`GIT-R06`).

## Findings mapped to packages

| Area | Findings | Package |
|---|---|---|
| Deploy | PM-13, PM-14, PM-26 | `DEP-R05` |
| Deploy | PM-22 | `DEP-R06` |
| Deploy | PM-12 | `DEP-R07` |
| Deploy | PM-23, PM-24, PM-28 | `DEP-R08` |
| Deploy | PM-16 | `DEP-R09` |
| Deploy | PM-1, PM-2, PM-5 | `DEP-R10` |
| Deploy | PM-37, PM-38 | `DEP-R11` |
| Deploy | PM-7, PM-15, PM-25 | `DEP-R12` |
| Deploy | PM-3, PM-4, PM-8, PM-9, PM-10, PM-11, PM-17, PM-19, PM-20, PM-21 | `DEP-R13` |
| Deploy | PM-6 | `DEP-R14` |
| Deploy | PM-18 | `DEP-R15` |
| Deploy | PM-39 | `DEP-R16` (Decision) |
| CLI | PM-34 | `CLI-R01` |
| CLI | EXE-1 | `CLI-R02` |
| CLI | PM-36 | `CLI-R03` |
| CLI | PM-35 | `CLI-R04` |
| CLI | PM-27, PM-31, PM-32, PM-33 | `CLI-R05` |
| CLI | PM-29 (templates), PM-30 | `CLI-R06` |
| Execution engine | STA-1 | `EXEC-R09` |
| Execution engine | STA-2, STA-3 | `EXEC-R10` |
| Execution engine | EXE-2 | `EXEC-R11` |
| Execution engine | WRK-3, WRK-4, WRK-7, WRK-12, WRK-13 | `EXEC-R12` |
| Execution engine | WRK-1 | `EXEC-R13` |
| Execution engine | EXE-10, EXE-11, EXE-12, EXE-13, EXE-14 | `EXEC-R14` |
| Execution engine | STA-4, STA-5, STA-8 | `EXEC-R15` |
| Execution engine | EXE-3 | `EXEC-R16` (Decision) |
| Execution engine | EXE-4, EXE-5, EXE-6, EXE-7, EXE-8, EXE-9, EXE-15, STA-6, STA-7, the preamble `sys.path` edit | `EXEC-R17` |
| IO layer | CIO-1 | `IO-R03` |
| IO layer | CIO-2, CIO-3 | `IO-R04` |
| IO layer | CIO-4 | `IO-R05` |
| IO layer | CIO-5, CIO-11 | `IO-R06` |
| IO layer | CIO-8, CIO-9, CIO-10 | `IO-R07` |
| IO layer | CIO-6, CIO-7 | `IO-R08` |
| Background jobs | WRK-2 | `ROAD-WORKER-06` |
| Background jobs | WRK-5, WRK-6, WRK-11, WRK-14 | `ROAD-WORKER-07` |
| Background jobs | WRK-8, WRK-9, WRK-10 | `ROAD-WORKER-08` |
| Server API | PM-43 | `API-R05` |
| Server API | SRV-2, SRV-3, SRV-9, SRV-11 | `API-R06` |
| Server API | SRV-4 | `API-R07` |
| Server API | SRV-1, SRV-5 | `API-R08` |
| Server API | SRV-6, SRV-7, SRV-8, SRV-13, SRV-15 | `API-R09` |
| Server API | SRV-10, SRV-12, SRV-14, SRV-16, SRV-17 | `API-R10` |
| Pipeline config | PCF-1 | `PCFG-R10` |
| Pipeline config | PCF-2, PCF-5 | `PCFG-R11` |
| Pipeline config | PCF-3 | `PCFG-R12` |
| Pipeline config | PCF-6, PCF-7 | `PCFG-R13` |
| Pipeline config | PCF-8, PCF-11, PCF-14, PCF-15 | `PCFG-R14` |
| Pipeline config | PCF-4, PCF-9, PCF-10, PCF-12 | `PCFG-R15` |
| Pipeline config | PCF-13 | `PCFG-R16` (Decision) |
| Pipeline config | PCR-1 | `PCFG-R17` |
| Pipeline config | PCR-2, PCR-3, PCR-4 | `PCFG-R18` |
| Pipeline config | PCR-5, PCR-6, PCR-7, PCR-8 | `PCFG-R19` |
| Optimiser | OPR-1, OPR-8, OPR-12 | `OPT-R01` |
| Optimiser | OPR-3 | `OPT-R02` |
| Optimiser | OPR-4, OPR-5, OPR-6, OPR-7 | `OPT-R03` |
| Optimiser | OPR-2 | `OPT-R04` (Decision) |
| Optimiser | OPR-9, OPR-10, OPR-11 | `OPT-R05` |
| Modelling | MDL-1 | `MOD-R01` |
| Modelling | MDL-14, MDL-15, MDL-19 | `MOD-R02` |
| Modelling | MDL-24 | `MOD-R03` |
| Modelling | MDL-4, MDL-5, MDL-6, MDL-17, MDL-25 | `MOD-R04` |
| Modelling | MDL-3, MDL-9, MDL-11, MDL-16, MDL-20, MDL-21 | `MOD-R05` |
| Modelling | MDL-2, MDL-18, MDL-22 | `MOD-R06` |
| Model scoring | MDL-12, MDL-23 | `MSC-08` |
| Model scoring | MDL-7, MDL-8, MDL-10, MDL-13 | `MSC-09` |
| Tracing | TRC-1 | `TRACE-R01` |
| Tracing | TRC-2, TRC-3, TRC-4 | `TRACE-R02` |
| Tracing | TRC-8, TRC-9, TRC-10, TRC-14 | `TRACE-R03` |
| Tracing | TRC-5, TRC-6, TRC-7, TRC-11, TRC-12, TRC-13 | `TRACE-R04` |
| Git integration | GIT-1 | `GIT-R01` (Decision) |
| Git integration | GIT-3, GIT-4, GIT-5, GIT-8, GIT-9 | `GIT-R02` |
| Git integration | GIT-6, GIT-7 | `GIT-R03` |
| Git integration | GIT-2 | `GIT-R04` |
| Git integration | GIT-12, GIT-13 | `GIT-R05` |
| Git integration | GIT-14 | `GIT-R06` (Decision) |
| Bugs | GIT-10 | `BUG-44` |
| Bugs | GIT-11 | `BUG-45` |
| Assistant | ASA-1, ASA-2, ASA-7, ASA-13 | `ASSIST-54` |
| Assistant | ASA-3, ASA-4, ASA-6, ASA-9 | `ASSIST-55` |
| Assistant | ASA-5, ASA-8, ASA-10, ASA-11, ASA-12, ASA-14 | `ASSIST-56` |
| Assistant | ASB-1, ASB-2, ASB-10, ASB-12 | `ASSIST-57` |
| Assistant | ASB-4, ASB-5, ASB-6, ASB-8, ASB-9 | `ASSIST-58` |
| Assistant | ASB-3, ASB-7, ASB-11 | `ASSIST-59` |
| JSON shredding | JSN-1, JSN-3 | `JSON-R01` |
| JSON shredding | JSN-2 | `JSON-R02` |
| JSON shredding | JSN-4, JSN-5, JSN-8, JSN-9 | `JSON-R03` |
| JSON shredding | JSN-6, JSN-7 | `JSON-R04` |
| JSON shredding | JSN-10, JSN-11, JSN-12 | `JSON-R05` |
| Submodels | SUB-1 | `SUB-R04` |
| Submodels | SUB-2, SUB-3, SUB-4, SUB-11 | `SUB-R05` |
| Explore / EDA | SUB-5, SUB-6, SUB-7, SUB-10 | `EDA-R01` |
| Explore / EDA | SUB-8, SUB-9, SUB-12 | `EDA-R02` |
| Frontend shared | FSA-1, FSA-2, FSA-8, FSA-10 | `FSH-R05` |
| Frontend shared | FSA-3, FSA-4, FSA-5, FSA-6, FSA-7, FSA-9, FSA-12 | `FSH-R06` |
| Server API | FSA-11 | `API-R08` (extended) |
| Frontend node editors | FNE-1, FNE-2, FNE-4, FNE-7, FNE-8, FNE-17 | `FNED-R01` |
| Frontend node editors | FNE-3, FNE-5, FNE-6, FNE-9, FNE-11 | `FNED-R02` |
| Frontend node editors | FNE-10, FNE-12, FNE-13, FNE-14, FNE-15, FNE-16 | `FNED-R03` |
| Frontend shared | FSB-1, FSB-2, FSB-10, FSB-12, FSB-15 | `FSH-R07` |
| Frontend shared | FSB-3, FSB-4, FSB-5, FSB-6, FSB-9, FSB-13, FSB-14, FSB-16, FSB-17, FSB-18 | `FSH-R08` |
| Frontend shared | FSB-7, FSB-8, FSB-11 | `FSH-R09` |
| Frontend node editors | FNB-1, FNB-2, FNB-3, FNB-5, FNB-13 | `FNED-R04` |
| Frontend node editors | FNB-4, FNB-6, FNB-7, FNB-8, FNB-9, FNB-10, FNB-11, FNB-12, FNB-14, FNB-15 | `FNED-R05` |
| Frontend modelling | FMD-1, FMD-2, FMD-4, FMD-5, FMD-12 | `FMOU-R01` |
| Frontend modelling | FMD-3, FMD-6 | `FMOU-R02` |
| Frontend modelling | FMD-7, FMD-8, FMD-9, FMD-10, FMD-11, FMD-13, FMD-14, FMD-15 | `FMOU-R03` |
| Frontend optimiser | FOX-1, FOX-2, FOX-3 | `FMOU-R04` |
| Frontend optimiser | FOX-4, FOX-5, FOX-6, FOX-8, FOX-10 (optimiser part) | `FMOU-R05` |
| Frontend Explore | FOX-7, FOX-9, FOX-10 (Explore part), FOX-11, FOX-12 | `EDA-R03` |
| Sandbox security | PM-42 | `SBX-R04` |
| Workbench | PM-44 | `WB-33` |
| Workbench | PM-45 | `WB-34` |
| Bugs | PM-40, PM-41 | `BUG-40` |
| Bugs | PM-46 | `BUG-41` |
| Bugs | PM-47 | `BUG-42` (Decision) |
| Bugs | PM-49 | `BUG-43` |
| Engineering quality | PM-29 (Haute's own workflows) | `ENGQ-CI06` |
| Engineering quality | PM-48 | `ENGQ-R06` |
| Engineering quality | PM-50 | `ENGQ-R01` (evidence added) |
| Engineering quality | the campaign-named test modules recounted | `ENGQ-R05` (evidence added) |

Already tracked before this review and not repeated: the `cwd`-captured sandbox
root used by the deploy modules, the underwriter app and the server
(`PCFG-R04`); the SPA path containment and the build hook's asset containment
(`SBX-R01`); the generated container smoke, impact, registry-login and GitLab
cache steps (`BUG-21`, `BUG-24`, `BUG-26`); the CLI docstring (`BUG-27`); the
three worker mechanisms' parallel error hierarchies (`ROAD-WORKER-05`);
`HTTPException` raised from service modules (`API-R02`) and the hand-maintained
browser contract (`API-R03`); the cwd-dependent resolvers in the routes and the
parser (`PCFG-R04`), editor state inside the config (`PCFG-R08`), the per-type
validators and branches (`PCFG-R07`, `PCFG-R09`); name-violation reporting
(`NAME-09`); the instance mechanism (`SUB-R01`); unkept statements in submodel
files (`BUG-19`); the test-only config helpers and event-bus hooks (`ENGQ-R01`);
the optimiser's unshown solve-memory forecast (`OPT-W02`) and its frontier
points without convergence traces (`OPT-PC04`); the optimiser's job registries
not cancelled at shutdown (`ROAD-WORKER-06`); MLflow-sourced models scored
without their training contract in the preview, batch and trace paths as well
as in deploy (`DEP-R07`, whose plan already names the editor); the test-only
`_compute_schema_hash` and the training facade (`ENGQ-R01`); the cost of the
training test modules (`ENGQ-CI03`); fits that exceed their budget
(`MOD-M01`, `MOD-M02`); the trace route's status by message prefix and its
sanitized 500 for a node failure (`API-R06`); two trace inconsistencies that
vanish with node-level instances (`SUB-R01`); the git routes' and the
assistant's current-directory project root (`PCFG-R04`); the Databricks client
factory's and the save service's `HTTPException`s (`API-R02`); the assistant's
own containment comparisons (`SBX-R01`); the per-request workspace client in the
browsing routes (`BUG-41`); the assistant catalog's hand tables and key-only
config validation (`PCFG-R09`, `PCFG-R07`), its whole-list rewrites
(`ASSIST-52`) and the test-only manifest cache reset (`ENGQ-R01`); the API
Input's supervised build (`IO-R08`), the Edge Join as a boundary (`EXEC-R15`)
and Recover's `how="asof"` (`PCFG-R13`); node-level instances and their
rewriters (`SUB-R01`), the Explore routes' `ParseError` and the submodel route's
400 (`API-R06`), the pivot registry at shutdown (`ROAD-WORKER-06`); the
browser's hand-written contract mirrors (`API-R03`), `hashConfig`'s exclusion
list (`PCFG-R08`), the results store's optimiser logic (`FSH-R03`) and the dead
`checkHauteSession`, `isPipelineResponse` and `parseRemoveUnavailableNodeRequest`
(`ENGQ-R01`); the editors' unused barrel and unlisted `@lezer/highlight`
(`ENGQ-R01`), Banding's `_prevRules` stash (`PCFG-R08`), the path-grammar
mirrors, which agree construct for construct (`JSON-R04`), the Edge Join
editor's `coalesce` (`JSON-R03`), the API Input label rule (`NAME-09`) and the
contract path no editor sets (`DEP-R07`, `MSC-08`); the dead
`submodelGraphFromMetadata` and two dead duplicate exports in the hooks and
utilities (`ENGQ-R01`), and the sheet-visibility fault on an output condition
(`WB-29`); Banding's `_id` and the step error string inside the config
(`PCFG-R08`), the editors' barrels (`ENGQ-R01`), the pivot editor writing the
fields the backend defaults (`EDA-R02`); the panel half of the GPU loss-history
numbering and the GLM `best_iteration` (`MOD-R05`), the offset help text, the
tuned-refit wording and the t-boost starters (`BUG-28`, `BUG-29`, `BUG-30`), and
a second fabricated train-failure sentinel (`FSH-R06`); the browser's frontier
generation fences that only ever see generation 0 (`OPT-R04`), the optimiser
panels' three locales (`FSH-R08`), the pivot date validators (`FSH-R06`) and
`applyFrontierPointSummary`'s null handling (`FSH-R03`).

## Verdicts that changed or stayed open

- **PM-13 downgraded from Major to Minor.** The generated `app.py` admits an
  execution context before framing the request and never releases it when
  `pl.DataFrame(rows)` raises. A probe held 199 unreleased admissions and every
  further admission was still granted, so the leak is hygiene (contexts,
  telemetry), not a denial of service.
- **CIO-1 kept Critical** after the main session reproduced it independently of
  the reviewer's probe.
- **STA-1 rated Major in the ledger, P1 as a package.** The shape is narrow (a
  `select` that reads no input column) but the consequence is a cached
  generation with the wrong row count, read as fresh.
- **PCF-1 rated Major in the ledger, P1 as a package.** The lenient decode needs
  a non-UTF-8 file Haute never writes, but when it meets one the save corrupts
  authored bytes silently, which is the Critical definition.
- **PCR-1 rated Major in the ledger, P1 as a package.** The trigger is a
  hand-written one-line body, but the loss of authored code is silent and the
  next save makes it permanent.
- **MDL-1 rated Major in the ledger, P1 as a package.** The reviewer rated it
  Major because it needs a value absent from training and the low-level
  specification deliberately keeps declared domains apart from observed values;
  the package is P1 because the result is a silently wrong price for a routine
  production event.
- **EXE-11 kept Minor, reachability unconfirmed.** The positional binding after
  a dropped name is real; whether the canvas or codegen can emit a request-input
  edge with a null handle was not shown.
- **WRK-1 proportion noted.** The 51 MiB gap is a fifth of a 256 MiB budget and
  a small fraction of a multi-GiB one; the package is P2.

## Probes

All probes live outside the repository and wrote nothing into it.

| Probe | What it showed |
|---|---|
| glob freshness reproduction | A parquet glob Data Input validates and scans (3 rows, sum 60); after one file is rewritten the scan gives 2028 while the signature stays `"missing"` and the status `fresh`. |
| fan-out cache peak | Display walk with the fan-out cache: 610 MB peak; without: 329–365 MB; with Polars CSE off: 318–340 MB; identical results. |
| column-free select | `classify_chunk_local_polars_code` accepts `source.select(pl.lit(1).alias("one"))`; native height 1, input-sliced write under three parts: height 3. |
| estimate widths | Widened graph estimate 48,000 bytes, equal to the plain graph; actual widened input 4,016,000 bytes. |
| admission leak | 199 unreleased deploy admissions, every one granted. |
| watchdog baseline | Parent sample 2,154,496 bytes straight after start; child RSS after importing Polars and two haute modules 55,517,184 bytes. |
| shutdown join | Main thread exits at 2.0 s with a 6 s child in flight; the process exits at 7.57 s (8 s child: 9.48 s). |
| pool replacement | Worker pid changes after a child `ValueError`; after one forced start failure, two further requests on the slot fail with "its replacement did not start". |
| worker `TimeoutError` | A worker raising `TimeoutError` under a 5 s response timeout surfaces as `BlockingWorkTimeoutError` with `outcome=response_timeout`. |
| sidecar self-write | After `save_sidecar(...)` the self-write registry is empty and `is_self_write(sidecar)` is false; `ParseError` is not a `ValueError`. |
| save request order | `save_sidecar` with an `active_source` not in `sources` raises pydantic's `ValidationError` after the other files were staged; two nodes labelled `Rates` and `rates` make `flatten_executable_graph` raise `ParseError`. |
| lenient decode | A cp1252 sidecar `{"name": "Prämie"}` loads as `Pr�mie` and is rewritten as `EF BF BD`; the constants reader refuses the same bytes. |
| dropped comments | Five comment kinds (a section, decorator-to-`def`, trailing, between functions, on `connect`) are absent after parse → `graph_to_code`; `unkept_module_statements` reports none. |
| inline keywords | `@pipeline.constant(config=..., values=[...], colour="red")` parses to the sidecar's values with no warning; the regenerated decorator keeps only `config=`. |
| corrupt parquet scan | `pl.scan_parquet` on a corrupt file returns a lazy frame; `collect_schema()` raises `ComputeError` (Polars 2.0.0). |
| GLM unseen level | A Poisson GLM on levels A, B, C (RustyStats 0.9) predicts A, B, C, D, Z as 1.133071, 2.475292, 0.634106, 1.133071, 1.133071 and a null as 1.133071. |
| Tweedie power | XGBoost trains `reg:tweedie` with `tweedie_variance_power=None` at 1.5; LightGBM trains with the `None` kept and its default applied. |
| evaluation plan size | 2,000,000 rows, five folds: a 71 MB plan; the parent's reload takes 13.3 s with a 0.65 GB peak. |
| same-line return | `extract_user_code("df = quotes.head(1); return df")` returns `''` for the Polars and hook kinds; `"a = 1\ndf = quotes.head(a); return df"` returns `'a = 1'`. |
| reformat discards steps | A free-code step whose comment carries trailing spaces survives two saves; `ruff format` of the file then logs `polars_steps_discarded`. |
| `match` bindings | `support_code_inventory` on a preamble `match` lists only `os`; the equivalent `if`/`else` lists `helper` twice. |
| trace input sources | A left join of A `(policy, x=10)` with B `(policy, x=1000)` and `y = x * 2`: `calc.x` is 10.0 while `input_sources["x"]` names B with 1000.0; declaring B first names A with 10.0; `derivations[].reads` names A both ways. |
| parser display | `with_columns(rank=pl.col('x').rank().over('g'), y=pl.col('a') * 2)` evaluated for `y`: `expression_type: window`, `substituted_text: rank of x over g`, `result_value: 42.0`; a `replace({'A': 'premium'})` beside a `rename` labels the step a rename of `A`. |
| typed branch names | Seven names git refuses (`my branch`, `feat..x`, `feat.lock`, `.hidden`, `a//b`, `trail/`, `x@{1}`) pass `_validate_ref_name` and fail in `set_working_branch` with a plain `GitError`. |
| argv limit | `git rev-parse` with 800 forty-character SHAs fails on Windows with `WinError 206` at 32,814 characters; 500 succeed. |
| manifest timing | Five warm `capability_manifest()` calls: 47.3, 47.5, 48.6, 47.2, 47.2 ms, the same object each time. |
| shred shape mismatch | Records with an object and a scalar at a declared `drivers[:]` key give two driver rows for four quotes and empty skip statistics; `shred_request` answers without raising; an XML quote with one `<driver>` loses it while a quote with two keeps both. |
| root-array tokeniser | 167,103 records (41.9 MB): JSONL 0.18 s, root JSON array 7.60 s, `orjson.loads` of the whole file 0.66 s. |
| date guessing | `['15/01/2024']` builds a `Date` series as 15 January and `['01/02/2024']` as 1 February; an ISO timestamp is refused. |
| tz-aware pivot member | A member `2020-06-01T10:00:00+01:00` restored to an aware `datetime` compares against a `Datetime('μs', 'Europe/London')` column with `SchemaError`; a UTC column and a nanosecond column each match their row. |
| client paths | Every `/api/...` literal in `frontend/src/api/*.ts` has a server route with the same method and body fields; eight server routes have no browser caller. |
| formatter boundaries | `formatNumber(999999)` is "1000.0K", `formatBytes(1048575)` is "1024.0 KB"; `(1234.5).toLocaleString("de-DE")` is "1.234,5"; `Date.UTC(99, 0, 1)` reads as 1999. |
| banding validator versus writer | A breakpoint rule with a blank label beside a valid rule passes `validate_banding_config` and `compact_banding_config_for_sidecar` raises `ConfigSettingError` ("breakpoint rule '40' requires label"), a `HauteValidationError` with no HTTP handler. |
| browser Gini | The browser's trapezoid Gini over the 200-point downsampled Lorenz curve differs from the server's tie-aggregated Gini by about 1.2e-4 at n = 2,000, 20,000 and 200,000. |
| percent display | `0.29 * 100` is `28.999999999999996`; 0.07, 0.14, 0.57 and 0.58 likewise; 0.20, 0.25 and 0.30 are exact. |
| price-contour column order | `solve`, `apply_from_grid` and the quote-results schema emit `optimal_<c>` in config order; `ApplyOptimiser.apply` in sorted order, so both haute schemas match their own library path (no finding). |

## Mechanical metrics

| Tool | Result |
|---|---|
| radon | 285 functions with cyclomatic complexity ≥ 20; 27 ≥ 50 (the largest in the optimiser service, the save service, the assistant tools and the RAM estimate). |
| vulture (60 %) | 61 candidates; those confirmed unreferenced are in `EXEC-R17`, `IO-R08`, `ROAD-WORKER-08`, `DEP-R13`, `CLI-R05`, `API-R10`, `PCFG-R15` and `ENGQ-R01`. |
| jscpd Python | 0.30 % duplicated, 41 clones; the production ones are in `DEP-R13`, `EXEC-R17`, `IO-R08`, `ROAD-WORKER-08`, `API-R09` and `PCFG-R15`. |
| jscpd TypeScript | 0.34 % duplicated, 29 clones (frontend areas pending). |
| knip | 161 unused exports, 256 unused exported types, `@lezer/highlight` unlisted (frontend areas pending). |
| sweeps | 100 floating action tags in `.github/workflows` (`ENGQ-CI06`, `CLI-R06`); 35 campaign-named test modules (`ENGQ-R05`). |
