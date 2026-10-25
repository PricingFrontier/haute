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
- The pipeline-config reviewer left `_polars_steps.py` (about 1,500 lines),
  `_pipeline_recovery.py` (about 1,100 lines) and parts of six smaller modules
  unread; a follow-up reviewer covers them.
- Areas whose findings are not yet in this report are in review: modelling, the
  MLflow registry and training; the optimiser; tracing and expression parsing;
  git integration and Databricks IO; the assistant; the JSON shredder's
  remaining modules; submodels and Explore; the frontend; the tests, scripts
  and CI as code.

## Coverage and findings so far

| Area | Read by | Coverage | Findings (Critical / Major / Minor / Suggestion) |
|---|---|---|---|
| Deploy, CLI, scaffold, server shell, hosted storage, Databricks app, sandbox, underwriter app and quote store, rating, Workbench modules, workflows, build and release | main session | every file in full except `scripts/container_smoke.py` (first 120 lines) and the seven secondary workflows (sweeps only); `_global_constants.py`, `_graph_utils.py`, `_execution_admission.py`, `_mlflow_io.py` and `_model_flavors.py` by targeted greps (the last two are the modelling reviewer's) | 0 / 4 / 38 / 8 |
| Execution core (walker, executor, lazy execution, pipeline, contexts) | reviewer, verified | full | 0 / 2 / 10 / 3 |
| Caching and IO (source cache, node snapshots, providers, data points, input preparation, registry) | reviewer, verified; the Critical reproduced by the main session | full | 1 / 3 / 4 / 3 |
| Static analysis (projection, lineage, RAM estimate, chunk classifier, cardinality) | reviewer, verified; two probes rerun | full | 0 / 3 / 5 / 0 |
| Workers and jobs (isolation, protocol, pool, dedicated worker, caps, job store, timeouts) | reviewer, verified; one probe rerun | full | 0 / 4 / 10 / 0 |
| Server routes and the save service (pipeline, save, helpers, node data, project files, utility, banding and rating, submodel, error handlers, supersession) | reviewer, verified; two probes | every owned file in full; `schemas.py` the models those routes use | 0 / 4 / 10 / 3 |
| Pipeline config, codegen, parsing and recovery | reviewer, verified; two probes rerun | core files in full; `_polars_steps.py` and `_pipeline_recovery.py` in part (follow-up pending) | 0 / 3 / 10 / 2 |

One hundred and thirty findings so far: one Critical, twenty-three Major,
eighty-seven Minor and nineteen Suggestions. The Critical and every Major is confirmed at source by the
main session; the Minors and Suggestions were read at source or accepted on
quoted evidence as the verification table records.

## The Critical and the Majors

- **A glob or folder Data Input is invisible to the freshness proofs** (Critical,
  `IO-R03`). `source_signature` answers `"missing"` for a path that is not one
  file and the runtime fingerprint answers `{"exists": False}`, so the snapshot's
  signature and the live one are always equal and the snapshot is fresh forever.
  Reproduced: three rows summing to 60, one file rewritten, the live scan gives
  2028 while the snapshot stays fresh. A parquet glob also cannot be deployed
  (it is "direct", then refused as absent) while a CSV glob's stale snapshot is
  bundled.
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
files (`BUG-19`); the test-only config helpers and event-bus hooks (`ENGQ-R01`).

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

## Mechanical metrics

| Tool | Result |
|---|---|
| radon | 285 functions with cyclomatic complexity ≥ 20; 27 ≥ 50 (the largest in the optimiser service, the save service, the assistant tools and the RAM estimate). |
| vulture (60 %) | 61 candidates; those confirmed unreferenced are in `EXEC-R17`, `IO-R08`, `ROAD-WORKER-08`, `DEP-R13`, `CLI-R05`, `API-R10`, `PCFG-R15` and `ENGQ-R01`. |
| jscpd Python | 0.30 % duplicated, 41 clones; the production ones are in `DEP-R13`, `EXEC-R17`, `IO-R08`, `ROAD-WORKER-08`, `API-R09` and `PCFG-R15`. |
| jscpd TypeScript | 0.34 % duplicated, 29 clones (frontend areas pending). |
| knip | 161 unused exports, 256 unused exported types, `@lezer/highlight` unlisted (frontend areas pending). |
| sweeps | 100 floating action tags in `.github/workflows` (`ENGQ-CI06`, `CLI-R06`); 35 campaign-named test modules (`ENGQ-R05`). |
