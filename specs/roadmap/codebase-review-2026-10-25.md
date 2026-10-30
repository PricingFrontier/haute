# Codebase review — 25 October 2026

A whole-repository review made at `main` `859548a4d` (the merge of pull request
#322) on the branch `codebase-review-2026-10-25`, before Haute's first production
use. It looked for defects, inconsistencies, duplicated and dead code,
over-complicated and fragile solutions, and anything else that could cause
trouble in production, with particular attention to the deployment and serving
code. It is a dated supporting report: it records the method, its limits, the
evidence and the probes, and maps every open finding to the roadmap package that
tracks it; findings whose packages were delivered have been removed. It owns no
work; the component roadmaps carry the plans.

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
7. **Original review model.** At the owner's decision the initial audit used no
   Codex passes. The subsequent Codex validation and discussion with Claude Opus 5.5
   are recorded below; the component roadmaps include their agreed corrections.

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
- Two observations are recorded without a package: the server's `FormSpec`
  applies no frame-label check to a schema column's name at save, so a saved
  form can hold a name the tables route refuses, which is consistent with the
  advisory-problems design but unstated in the workbench specification's error
  handling; and whether the `mutation` workflow is a required check in branch
  protection is outside the repository and was not checked.
- The engineering-quality reviewer read seven large scripts structurally (their
  definitions and the subprocess, exception, environment and `cwd` sites), the
  29 browser specifications by a wait-and-selector census, and eight tests in
  depth plus metrics for 24 modules, rather than twenty modules line by line.

## Coverage and findings

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
| Frontend trace, git and assistant UI (the trace panel and its cards, the git panel, branch manager, modals and graph rail, the chat panel) | reviewer, verified | every owned file in full except parts of three modals | 0 / 1 / 13 / 8 |
| Workbench UI (the Workbench view, its sheets, schema editor, panels and Show when editor; the underwriter app's pages and stores) | reviewer, verified | every owned file in full; the tests by name, four bodies read | 0 / 0 / 6 / 3 |
| App shell, graph canvas and shared components (`App.tsx`, the node renderers, menus, dialogs, toolbar, settings, the form kit, the inspector shell and preview frame) | reviewer, verified; the renderers' flag reads re-read | every owned file in full except the stylesheet | 0 / 0 / 9 / 6 |
| Tests, scripts and CI as code (conftest and the shared helpers, the gates and their scripts, the ten workflows, the pytest, coverage, ruff and mypy configuration, the Vitest, Playwright and ESLint configuration, the e2e harness) | reviewer, verified; the coverage-list difference and the gate's `if:` re-checked | conftest, the helpers, the gate scripts, the workflows and the configurations in full; seven large scripts structurally; the e2e specifications by census | 0 / 0 / 10 / 7 |

The original audit recorded four hundred and five findings: two Critical, forty-four
Major, two hundred and seventy-eight Minor and eighty-one Suggestions, organised into
134 new packages. These are the original inventory and grading, not a revised count
of independently reproduced defects. Its main session reported both Criticals and
every Major as confirmed at source; the Minors and Suggestions were read at source or
accepted on quoted evidence as the coverage table records. The validation below
qualifies claims and remedies without inventing a replacement verified-defect count.
The findings of the thirty-eight packages delivered or settled since (thirteen by 28
October 2026, twenty-five more on 29 October) have been removed from the sections below; the coverage table keeps the
original counts.

## Validation — 25 October 2026

Codex reviewed the audit branch and cited source/specification seams, then discussed
13 correction groups plus priorities and scope with Claude Opus 5.5. Claude agreed
with twelve proposals and partly agreed with two, refining input validation and
target dependency resolution. The component roadmaps now carry the resulting plans:

| Packages | Correction |
|---|---|
| `EXEC-R17` | Keep same-size/same-mtime utility freshness and support deferred imports while scoping `sys.path`; bytecode invalidation is a correctness defence. |
| `EXEC-R13` | Measure actual RSS at a readiness handshake; native VMS, private-commit and cgroup baselines are different accounting. Preserve startup supervision. |
| `FSH-R07`, `FSH-R05` | Recover a save only from complete canonical saved-content equality, retain newer dirty edits, and distinguish intended other-tab broadcasts from watcher duplicates and self-echoes. |
| `DEP-R11` | Check every response batch before positional pairing; require explicit first-deploy intent or authoritative absence, never infer absence from an outage. |
| `DEP-R09` | Resolve and verify dependency locks for each target Python/platform and extras. |
| `WB-10` | Shipped SQLite already has atomic insert-if-absent. Require a real selected-engine concurrency test and correct transaction recovery. |

The build-directory work is split into `DEP-R12A` (P2, isolated owned bundles
and cleanup) and `DEP-R12B` (P3, project-scoped schema caching). Unnamed stale artifacts
are an isolation/disclosure concern; the loader reads manifest-named artifacts.
Concurrent builds can interfere with named artifacts, so reassess the isolation
priority if shared-checkout concurrency is an advertised deployment workflow.

The roadmap chooses complete canonical content comparison for save recovery, uses
explicit CLI intent for an HTTP first deployment, and excludes the saving socket while
retaining fingerprint guards. These are planned rules to specify and test in the
component specifications before code changes. WB-10's server-engine choice remains
with the project's IT team; the audit does not choose one or present its generic
branch as a current SQLite release defect.

The follow-up plan review also required the dependency gate to preserve the existing
minimal scoring container. DEP-R09 verifies the selected runtime closure with explicit
accounting for Haute's intentionally omitted non-scoring requirements; it does not
require installing the editor/training stacks merely to pass an unrestricted metadata
check. The full pyfunc and hosted-app profiles keep their full dependency checks.

Root validation probes reproduced literal-select row multiplication, malformed
test-quote configuration disabling the gate, mismatched impact-row acceptance,
same-line return extraction loss, stale utility bytecode and multiline-string changes
from line trimming. The SSH option probe used `ssh -G` without a network connection.
These probes support the named cases, not every subclaim in every package.

No cloud deployment or full backend/frontend suite was run for validation. Linux
memory backends, server-database concurrency and unexecuted browser behaviour remain
source/documentation based. The original JSON throughput ratio and other performance
figures were not all remeasured. FGC-R03 remains measure-first; EXEC-R14 retains its
unconfirmed-reachability qualification. Maintenance packages and product decisions
are not automatically first-release blockers. Release priority is assessed per
package, independently of the original finding count.

## The open Majors

Both Criticals, and the Majors whose packages were delivered, have been removed;
these are the Majors still open.

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
- **The one-shot and protocol watchdogs measure a bare interpreter** (Major,
  `EXEC-R13`): 51 MiB tighter than the native cap under a 256 MiB budget.
- **A graceful server exit joins every in-flight worker** (Major,
  `ROAD-WORKER-06`): a 6 s child in flight at a 2 s exit ends the process at
  7.6 s; no job registry is cancelled at shutdown.
- **A remote user error replaces the warm worker; one failed replacement
  disables the slot for the life of the server** (two Majors, `EXEC-R12`).
- **Every save drops authored comments outside the kept regions** (Major,
  `PCFG-R12`; five kinds reproduced) with no diagnostic.
- **The optimiser's MLflow log writes the complete result artifact without the
  finiteness gate the save applies** (Major, `OPT-R01`): a missing defence layer
  on a pricing artifact, since the status poll corrects the UI path.
- **The assistant's SDK clients are built without the validated endpoint**
  (Major, `ASSIST-54`): `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` in the
  server's environment redirects the key, the prompt and project material to a
  host the endpoint policy never checked.
- **`inspect_node` holds the project's save lock while profiling up to a million
  rows, and the capability manifest is rebuilt on every call** (two Majors,
  `ASSIST-57`): every save, write and other server process on the project waits
  for the profile; 47 ms of descriptor construction per tool result on the
  event-loop thread.
- **A WebSocket reconnect after a save discards every running job's tracking
  and the undo history** (Major, `FSH-R05`): the save stores no document
  fingerprint, so the resync is answered with a full document the client takes
  for an external change.
- **A banding rule the grid leaves unmarked fails the save with a 500** (Major,
  `FNED-R04`): the save validator accepts a blank band name, a blank categorical
  value or a repeated value, the sidecar writer refuses them, and the writer's
  error has no HTTP handler.
- **Saving the pipeline cancels an auto-range run in flight, and a percentage
  constraint is shown as "at least 0" and rewritten as an absolute bound on
  edit** (two Majors, `FMOU-R04`).
- **Ctrl+S with the cursor in a code box saves without the last keystrokes**
  (Major, `FSH-R07`): the save helper blurs only text fields, the code editor's
  debounced commit flushes only on blur, and the toolbar shows "Saved" while the
  canvas re-dirties with no indicator.

## Design choices the review asks the owner to confirm

Three behaviours are specified and implemented as specified, and the review
still puts them in front of the owner because of what they can cost in
production:

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
| Deploy | PM-16 | `DEP-R09` |
| Deploy | PM-1, PM-2, PM-5 | `DEP-R10` |
| Deploy | PM-37, PM-38 | `DEP-R11` |
| Deploy | PM-7, PM-15, PM-25 | `DEP-R12A`, `DEP-R12B` |
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
| IO layer | CIO-5, CIO-11 | `IO-R06` |
| Background jobs | WRK-2 | `ROAD-WORKER-06` |
| Background jobs | WRK-5, WRK-6, WRK-11, WRK-14 | `ROAD-WORKER-07` |
| Background jobs | WRK-8, WRK-9, WRK-10 | `ROAD-WORKER-08` |
| Pipeline config | PCF-3 | `PCFG-R12` |
| Pipeline config | PCF-13 | `PCFG-R16` (Decision) |
| Pipeline config | PCR-8 | `PCFG-R19` |
| Optimiser | OPR-1, OPR-8, OPR-12 | `OPT-R01` |
| Optimiser | OPR-3 | `OPT-R02` |
| Optimiser | OPR-4, OPR-5, OPR-6, OPR-7 | `OPT-R03` |
| Optimiser | OPR-2 | `OPT-R04` (Decision) |
| Optimiser | OPR-9, OPR-10, OPR-11 | `OPT-R05` |
| Modelling | MDL-24 | `MOD-R03` |
| Modelling | MDL-3, MDL-9, MDL-11, MDL-16, MDL-20, MDL-21 | `MOD-R05` |
| Modelling | MDL-2, MDL-18, MDL-22 | `MOD-R06` |
| Model scoring | MDL-7, MDL-8, MDL-10, MDL-13 | `MSC-09` |
| Tracing | TRC-8, TRC-9, TRC-10, TRC-14 | `TRACE-R03` |
| Git integration | GIT-12, GIT-13 | `GIT-R05` |
| Git integration | GIT-14 | `GIT-R06` (Decision) |
| Assistant | ASA-1, ASA-2, ASA-7, ASA-13 | `ASSIST-54` |
| Assistant | ASA-3, ASA-4, ASA-6, ASA-9 | `ASSIST-55` |
| Assistant | ASA-5, ASA-8, ASA-10, ASA-11, ASA-12, ASA-14 | `ASSIST-56` |
| Assistant | ASB-1, ASB-2, ASB-10, ASB-12 | `ASSIST-57` |
| Assistant | ASB-4, ASB-5, ASB-6, ASB-8, ASB-9 | `ASSIST-58` |
| Assistant | ASB-3, ASB-7, ASB-11 | `ASSIST-59` |
| Explore / EDA | SUB-5, SUB-6, SUB-7, SUB-10 | `EDA-R01` |
| Explore / EDA | SUB-8, SUB-9, SUB-12 | `EDA-R02` |
| Frontend shared | FSA-1, FSA-2, FSA-8, FSA-10 | `FSH-R05` |
| Frontend shared | FSA-3, FSA-4, FSA-5, FSA-6, FSA-7, FSA-9, FSA-12 | `FSH-R06` |
| Frontend node editors | FNE-1, FNE-2, FNE-4, FNE-7, FNE-8, FNE-17 | `FNED-R01` |
| Frontend node editors | FNE-3, FNE-5, FNE-6, FNE-9, FNE-11 | `FNED-R02` |
| Frontend node editors | FNE-10, FNE-12, FNE-13, FNE-14, FNE-15, FNE-16 | `FNED-R03` |
| Frontend shared | FSB-1, FSB-2, FSB-10, FSB-12, FSB-15 | `FSH-R07` |
| Frontend shared | FSB-3, FSB-4, FSB-5, FSB-6, FSB-9, FSB-13, FSB-14, FSB-16, FSB-17, FSB-18 | `FSH-R08` |
| Frontend shared | FSB-7, FSB-8, FSB-11 | `FSH-R09` |
| Frontend node editors | FNB-1, FNB-2, FNB-3, FNB-5, FNB-13 | `FNED-R04` |
| Frontend node editors | FNB-4, FNB-6, FNB-7, FNB-8, FNB-9, FNB-10, FNB-11, FNB-12, FNB-14, FNB-15 | `FNED-R05` |
| Frontend modelling | FMD-7, FMD-8, FMD-9, FMD-10, FMD-11, FMD-13, FMD-14, FMD-15 | `FMOU-R03` |
| Frontend optimiser | FOX-1, FOX-2, FOX-3 | `FMOU-R04` |
| Frontend optimiser | FOX-4, FOX-5, FOX-6, FOX-8, FOX-10 (optimiser part) | `FMOU-R05` |
| Frontend Explore | FOX-7, FOX-9, FOX-10 (Explore part), FOX-11, FOX-12 | `EDA-R03` |
| Assistant UI | FTG-5, FTG-18, FTG-20 | `ASSIST-60` |
| Workbench | PM-45 | `WB-10` (selected server engine's atomic form insertion) |
| Engineering quality | PM-50 | `ENGQ-R01` (evidence added) |
| Engineering quality | the campaign-named test modules recounted | `ENGQ-R05` (evidence added) |
| Workbench UI | FWB-7 | `WB-38` (Decision) |
| App shell and canvas | FAP-2, FAP-3, FAP-5 | `FGC-R01` |
| App shell and canvas | FAP-1, FAP-11 | `FGC-R02` |
| App shell and canvas | FAP-9 | `FGC-R03` |
| App shell and canvas | FAP-4, FAP-6, FAP-7, FAP-8, FAP-10, FAP-12, FAP-13, FAP-15 | `FGC-R04` |
| Tests, scripts and CI | ENG-6 | `ENGQ-R01` |
| Tests, scripts and CI | ENG-17 | `ENGQ-R11` (Decision) |

Already tracked before this review and not repeated: the `cwd`-captured sandbox
root used by the deploy modules, the underwriter app and the server
(`PCFG-R04`); the
three worker mechanisms' parallel error hierarchies (`ROAD-WORKER-05`);
the hand-maintained
browser contract (`API-R03`); the cwd-dependent resolvers in the routes and the
parser (`PCFG-R04`), editor state inside the config (`PCFG-R08`), the per-type
validators and branches (`PCFG-R07`, `PCFG-R09`); name-violation reporting
(`NAME-09`); the test-only config helpers and
event-bus hooks (`ENGQ-R01`); the optimiser's unshown solve-memory forecast (`OPT-W02`) and its frontier
points without convergence traces (`OPT-PC04`); the optimiser's job registries
not cancelled at shutdown (`ROAD-WORKER-06`); the test-only
`_compute_schema_hash` and the training facade (`ENGQ-R01`); fits that exceed
their budget
(`MOD-M01`, `MOD-M02`); the git routes' and the
assistant's current-directory project root (`PCFG-R04`); the assistant
catalog's hand tables and
key-only config validation (`PCFG-R09`, `PCFG-R07`), its whole-list rewrites
(`ASSIST-52`) and the test-only manifest cache reset (`ENGQ-R01`); the Edge
Join as a boundary (`EXEC-R15`); the pivot registry at shutdown (`ROAD-WORKER-06`); the
browser's hand-written contract mirrors (`API-R03`), `hashConfig`'s exclusion
list (`PCFG-R08`), the results store's optimiser logic (`FSH-R03`) and the dead
`checkHauteSession`, `isPipelineResponse` and `parseRemoveUnavailableNodeRequest`
(`ENGQ-R01`); the editors' unused barrel and unlisted `@lezer/highlight`
(`ENGQ-R01`), Banding's `_prevRules` stash (`PCFG-R08`) and the API Input label
rule (`NAME-09`); the dead
`submodelGraphFromMetadata` and two dead duplicate exports in the hooks and
utilities (`ENGQ-R01`); Banding's `_id` and the step error string inside the config
(`PCFG-R08`), the editors' barrels (`ENGQ-R01`), the pivot editor writing the
fields the backend defaults (`EDA-R02`); the panel half of the GPU loss-history
numbering and the GLM `best_iteration` (`MOD-R05`) and a second fabricated
train-failure sentinel (`FSH-R06`); the browser's frontier
generation fences that only ever see generation 0 (`OPT-R04`), the optimiser
panels' three locales (`FSH-R08`), the pivot date validators (`FSH-R06`) and
`applyFrontierPointSummary`'s null handling (`FSH-R03`); in the workbench UI and
the app shell, the
chart theme observer written twice (`EDA-R03`), the params-folder listing in the
Allowed field (`FNED-R05`), knip's export-only symbols (`ENGQ-R01`), browser-locale dates (`FSH-R08`), the bootstrap without
a deadline (`FSH-R05`), Ctrl+S inside a code box (`FSH-R07`), editor state inside
node data (`PCFG-R08`) and the
modelling pane badges (`FMOU-R03`); in the tests, scripts and CI, the floating
action tags in the generated workflows (`CLI-R06`), the campaign-named test modules
(`ENGQ-R05`), the test-only reset hooks in production code (`ENGQ-R01`), and the
189 test modules that change directory because production resolves the project
root from it (`PCFG-R04`).

## Verdicts that changed or stayed open

- **STA-1 rated Major in the ledger, P1 as a package.** The shape is narrow (a
  `select` that reads no input column) but the consequence is a cached
  generation with the wrong row count, read as fresh.
- **PCF-1 rated Major in the ledger, P1 as a package.** The lenient decode needs
  a non-UTF-8 file Haute never writes, but when it meets one the save corrupts
  authored bytes silently, which is the Critical definition.
- **PCR-1 rated Major in the ledger, P1 as a package.** The trigger is a
  hand-written one-line body, but the loss of authored code is silent and the
  next save makes it permanent.
- **EXE-11 kept Minor, reachability unconfirmed.** The positional binding after
  a dropped name is real; whether the canvas or codegen can emit a request-input
  edge with a null handle was not shown.
- **WRK-1 proportion noted.** The 51 MiB gap is a fifth of a 256 MiB budget and
  a small fraction of a multi-GiB one; the package is P2.

## Probes

All probes live outside the repository and wrote nothing into it.

| Probe | What it showed |
|---|---|
| fan-out cache peak | Display walk with the fan-out cache: 610 MB peak; without: 329–365 MB; with Polars CSE off: 318–340 MB; identical results. |
| column-free select | `classify_chunk_local_polars_code` accepts `source.select(pl.lit(1).alias("one"))`; native height 1, input-sliced write under three parts: height 3. |
| estimate widths | Widened graph estimate 48,000 bytes, equal to the plain graph; actual widened input 4,016,000 bytes. |
| watchdog baseline | Parent sample 2,154,496 bytes straight after start; child RSS after importing Polars and two haute modules 55,517,184 bytes. |
| shutdown join | Main thread exits at 2.0 s with a 6 s child in flight; the process exits at 7.57 s (8 s child: 9.48 s). |
| pool replacement | Worker pid changes after a child `ValueError`; after one forced start failure, two further requests on the slot fail with "its replacement did not start". |
| worker `TimeoutError` | A worker raising `TimeoutError` under a 5 s response timeout surfaces as `BlockingWorkTimeoutError` with `outcome=response_timeout`. |
| sidecar self-write | After `save_sidecar(...)` the self-write registry is empty and `is_self_write(sidecar)` is false; `ParseError` is not a `ValueError`. |
| save request order | `save_sidecar` with an `active_source` not in `sources` raises pydantic's `ValidationError` after the other files were staged; two nodes labelled `Rates` and `rates` make `flatten_executable_graph` raise `ParseError`. |
| dropped comments | Five comment kinds (a section, decorator-to-`def`, trailing, between functions, on `connect`) are absent after parse → `graph_to_code`; `unkept_module_statements` reports none. |
| corrupt parquet scan | `pl.scan_parquet` on a corrupt file returns a lazy frame; `collect_schema()` raises `ComputeError` (Polars 2.0.0). |
| evaluation plan size | 2,000,000 rows, five folds: a 71 MB plan; the parent's reload takes 13.3 s with a 0.65 GB peak. |
| trace input sources | A left join of A `(policy, x=10)` with B `(policy, x=1000)` and `y = x * 2`: `calc.x` is 10.0 while `input_sources["x"]` names B with 1000.0; declaring B first names A with 10.0; `derivations[].reads` names A both ways. |
| parser display | `with_columns(rank=pl.col('x').rank().over('g'), y=pl.col('a') * 2)` evaluated for `y`: `expression_type: window`, `substituted_text: rank of x over g`, `result_value: 42.0`; a `replace({'A': 'premium'})` beside a `rename` labels the step a rename of `A`. |
| typed branch names | Seven names git refuses (`my branch`, `feat..x`, `feat.lock`, `.hidden`, `a//b`, `trail/`, `x@{1}`) pass `_validate_ref_name` and fail in `set_working_branch` with a plain `GitError`. |
| argv limit | `git rev-parse` with 800 forty-character SHAs fails on Windows with `WinError 206` at 32,814 characters; 500 succeed. |
| manifest timing | Five warm `capability_manifest()` calls: 47.3, 47.5, 48.6, 47.2, 47.2 ms, the same object each time. |
| root-array tokeniser | 167,103 records (41.9 MB): JSONL 0.18 s, root JSON array 7.60 s, `orjson.loads` of the whole file 0.66 s. |
| date guessing | `['15/01/2024']` builds a `Date` series as 15 January and `['01/02/2024']` as 1 February; an ISO timestamp is refused. |
| tz-aware pivot member | A member `2020-06-01T10:00:00+01:00` restored to an aware `datetime` compares against a `Datetime('μs', 'Europe/London')` column with `SchemaError`; a UTC column and a nanosecond column each match their row. |
| client paths | Every `/api/...` literal in `frontend/src/api/*.ts` has a server route with the same method and body fields; eight server routes have no browser caller. |
| formatter boundaries | `formatNumber(999999)` is "1000.0K", `formatBytes(1048575)` is "1024.0 KB"; `(1234.5).toLocaleString("de-DE")` is "1.234,5"; `Date.UTC(99, 0, 1)` reads as 1999. |
| banding validator versus writer | A breakpoint rule with a blank label beside a valid rule passes `validate_banding_config` and `compact_banding_config_for_sidecar` raises `ConfigSettingError` ("breakpoint rule '40' requires label"), a `HauteValidationError` with no HTTP handler. |

## Mechanical metrics

| Tool | Result |
|---|---|
| radon | 285 functions with cyclomatic complexity ≥ 20; 27 ≥ 50 (the largest in the optimiser service, the save service, the assistant tools and the RAM estimate). |
| vulture (60 %) | 61 candidates; those confirmed unreferenced are in `EXEC-R17`, `ROAD-WORKER-08`, `DEP-R13`, `CLI-R05` and `ENGQ-R01`. |
| jscpd Python | 0.30 % duplicated, 41 clones; the production ones are in `DEP-R13`, `EXEC-R17` and `ROAD-WORKER-08`. |
| jscpd TypeScript | 0.34 % duplicated, 29 clones; the production ones are in `FNED-R03`, `FNED-R05`, `FMOU-R05`, `EDA-R03` and `FGC-R04`. |
| knip | 161 unused exports, 256 unused exported types, `@lezer/highlight` unlisted; every frontend reviewer found the hits used inside their own module, so they are over-exports under `ENGQ-R01`'s gate, not dead code. |
| sweeps | 35 campaign-named test modules (`ENGQ-R05`). |
