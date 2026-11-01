# Haute engineering roadmap

This folder is the single source of truth for planned engineering
improvements. Each active component roadmap contains the problem,
implementation direction, acceptance criteria, dependencies, and current
code/test evidence needed to take one package through delivery.

Shipped behaviour remains defined by code, tests, and the component
specifications. `Reverify` packages came from older evidence and must be
reproduced against `HEAD` before implementation. `Decision` packages require
an explicit product or architecture choice. Remove a package when its outcome
is covered by current specifications and ordinary regression tests.
`Start with` names a non-deferred package; it is `—` when a component has no
currently startable package. Priority `P1` marks a package that threatens
persisted work, computed results, or security; `P2` a material correctness, UX,
or maintenance issue; `P3` opportunistic work.

Audit finding counts describe the original review inventory, not a count of release
blockers or independently reproduced defects. Keep measured defects, source-supported
risks, unconfirmed reachability, product decisions and maintenance work distinct.
The [25 October validation](codebase-review-2026-10-25.md#validation--25-october-2026)
records corrections to that audit's claims and proposed remedies.

| Component | Improvement surface | Start with |
|---|---|---|
| [Assistant](assistant.md) | The provider client as the policy describes it, bounded requests with a terminal event on every stream, tools that never stall the server, facts the model can trust, the core's and the tools' hygiene, the chat panel's failures and clicks; blocking data findings, structured step authoring, typed banding and rating, item-level edits and per-model qualification (all deferred); assistant apply retaining the authenticated revision through commit, and workbench form tables resolved from the configured project root | `ASSIST-54` |
| [Background jobs and API lifecycle](background-jobs-api.md) | A shutdown that cancels every job, one failure classification with persistence failures surfaced, one worker envelope and one cgroup resolver, one worker primitive; operation-raised timeouts propagated by shared flights and the latest-wins queue | `ROAD-WORKER-06` |
| [Caching](caching.md) | Planning and housekeeping cost, the shapes that cannot carry a write recipe, chunked-write bounds, cache identity; `LRUCache` evicting oversized replacements and `buildNodeDataCacheIdentity` including `globalConstants` | `CACHE-S26` |
| [CLI](cli.md) | `haute init --force` keeping the analyst's files, `haute run` as a lazy walk, `haute lint` running the editor's validators, `pyproject.toml` through `tomlkit`, error reporting, generated CI hygiene; `haute impact` dataset confinement and `lint`/`run` pipeline resolution error handling | `CLI-R01` |
| [Deploy](deploy.md) | Every target's dependency closure locked, typed `haute.toml`, impact alignment, build directories under the project, one copy of each deploy mechanism; matching JSON and NDJSON output documents | `DEP-R09` |
| [Documentation](docs.md) | The Databricks guide as the deploy runs, the CI pages as the generated workflows are, a new project's path from an empty folder, upgrade notes for a project made by 0.6.0, the site publishing `main` again | `DOC-01` |
| [Engineering quality](engineering-quality.md) | Dead code, test organisation; mutation witnesses for the price-changing modules (a decision) | `ENGQ-R01` |
| [Execution engine](execution-engine.md) | Column-free selects in chunked writes, estimate widths from the lineage, fan-out caching, warm pool resilience, watchdog baselines, quiet failures made loud, static-analysis coverage, dead evidence and clones; request-order proof rejecting column-free selects in deployed scoring; eager preamble failures, multi-frame `port_label` validation, schema-only online Optimiser Apply, and protocol worker environment | `EXEC-R09` |
| [Explore and EDA](explore-eda.md) | Pivot requests answered right (invalid cards, time-zone-aware members, formula causes, one scan per feature), card fields and the specification's stale statements, the chart panels' empty state and glyphs; advanced pivot and PivotChart parity (deferred); noncolliding relationship aliases and cached responses naming their consumer; pivot header format lookup by field name, fresh config reads after confirmation dialogs, and Explore error assistant action | `EDA-R01` |
| [Frontend graph canvas](frontend-graph-canvas.md) | Every selection path opening the node as a click does, every node renderer drawing every canvas flag, chrome that does not re-render per drag frame, one copy of each shell mechanism with errors shown in full; auto-layout preserving edits, submodel navigation bound to its document and isolating Undo/Redo history and dirty baselines, Global Constants in Undo/Redo history, multi-frame `previewPortLabel` and submodel fingerprints, complete mixed-selection deletion, utility drafts surviving pending reads, and degraded-document inspection | `FGC-R01` |
| [Frontend modelling and optimiser UI](frontend-modelling-optimiser-ui.md) | The specification as the editor is; an auto-range run that survives a save, percentage constraints kept as such, one point-request lifecycle; dispersion estimates bound to the initiating training draft; retained model handles on pre-job errors, synchronous training hash, fenced optimiser publish and factor pruning, and frontier axis clamping | `FMOU-R04` |
| [Frontend node editors](frontend-node-editors.md) | Editors that write what the engine reads, drafts in step with the document and errors with their detail, one copy of each picker and helper; grid rules the save refuses with a reason, positions and passthroughs in the subdirectories; cached-directory recovery, source-bound API Input inference and Data Input schemas, Databricks catalog/schema picker state, `OptimiserApplyEditor` artifact read cleanup, and `OutputEditor` preview invalidation | `FNED-R01` |
| [Frontend shared](frontend-shared.md) | A save that keeps the browser in step after a reconnect, surfaced completion errors, aborted fetches, a bootstrap deadline; Ctrl+S saving what is being typed and creation fenced on structure; one copy of each store derivation and helper; the specification as the code is; the results store; independent edits during input resolution, job-bound cancellation replies, failed solves retaining the identity of prior successful results, and store-backed node-data job ownership | `FSH-R05` |
| [Git integration](git-integration.md) | Dead symbols and one compensation helper, hooks on automatic saves (a decision); push recovery retaining its remote, and `file://` remote host/hosted-mode validation with unborn-repository file-size guard | `GIT-R18` |
| [IO layer](io-layer.md) | Lock holds and usage cost; memory admission and isolated worker execution for explicit Data Input cache builds | `IO-R07` |
| [Model scoring](model-scoring.md) | Generic model-file suffixes, pyfunc files, loader errors that keep their meaning; pyfunc classifiers preserving separate labels and positive-class probabilities, and native metadata record validation in `EBMModel.load`, `XGBoostModel.load` and `LightGBMModel.load` | `MSC-06` |
| [Modelling](modelling.md) | Publication outside the lock, diagnostics measured as the fit was; adaptive low-memory fitting: exact chunked GLM, a low-memory boosted-tree mode (both awaiting a decision); feasible stratified allocation, recorded tuning progress reaching the browser, prediction-based MLflow signatures, accurate model-card and PDP labels, and training identity agreement with submodel script export and MLflow/contract persistence safety | `MOD-R03` |
| [Name collisions](name-collisions.md) | Input-binding editors showing their name violations inline; distinct parsed, recovered and pasted connections retaining distinct identities | `NAME-09` |
| [Optimiser validation](optimiser-validation.md) | Per-point convergence traces from price_contour's frontier sweep; a calibrated solve-memory forecast in the Solve panel; the optimiser result workspace's open robustness and CSV questions; one gate for every persisted optimiser artifact, setup files under a reaped root, failures classified once, the frontier recompute sweep as a decision, `select_point` serialized under `parent_lock`, and sandboxed `artifact_path` resolution in Optimiser Apply | `OPT-R06` |
| [Pipeline config](pipeline-config.md) | Comments kept across a save, one recovery pass per file; project context, typed configs, editor state, node specification; keyword-only configuration defaults preserved; orphaned Polars sidecar removal | `PCFG-R21` |
| [Polars 2](polars-2.md) | Remaining RAM-estimate calibration, large-scale operator measurements and worker baselines; Map columns and quantile/rank binning (deferred) | `PL2-17` |
| [Polars node clarity](polars-node-clarity.md) | Step card visual baseline, formula comparisons (deferred) | `PNC-13` |
| [Server API](server-api.md) | Generated browser contract; process-mode 422 domain error mapping in `output_assemble_dry_run` and format-keyed Parquet metadata reads in `get_schema` | `API-R03` |
| [t-boost](t-boost.md) | What rating-table models make possible: cell-level holdout A/E, unfolding into rating steps, model comparison with premium attribution, measured analyst adjustments (all awaiting a decision); `_report_table` relativity overflow guard and `class_labels` validation | `TBOOST-11` |
| [Tracing](tracing.md) | Proven matches kept strict, and `carried_column_proof` restricted to columns retained by `select(...)` | `TRACE-R04` |
| [Workbench](workbench.md) | The underwriter app on hosts past Azure Container Apps (a Databricks App, AWS, internal platforms); a workbench project's CI/CD: a merge to `main` takes form, pipeline and app, tested together, to staging and production; managing quotes in the app past the Quotes page: outcomes, many quotes at once; exports from the app (awaiting a decision on formats); test quotes analysts keep and price again, built again after the first version was taken out (awaiting decisions); actions that fill the sheets from databases and APIs (awaiting decisions on auth and sources); the app's sign-in (a decision); typed-in allowed values (a decision); `useQuoteListStore` pagination fencing and submodel `WORKBENCH_OUTPUT` resolution in `priceSample` | `WB-08` |

## Delivery plan

The [delivery plan](delivery-plan.md) orders scheduled packages into rounds
delivered one PR at a time from a single checkout, with each round's
dependencies, its size and the user-visible changes it makes. It owns only
the order; the packages above own the work. The packages the
[25 October 2026 codebase review](codebase-review-2026-10-25.md) added, and the
documentation packages from that day's check of the deployment guides, are not
yet placed in its rounds.

## Codebase review — 23 September 2026

The [codebase review](codebase-review-2026-09-23.md) is a whole-repository
review for brittleness, over-engineering, weak areas with stronger standard
solutions, and duplication, made at `main` `9319b11d`. It keeps its method and
limits and the findings still open, each mapped to the package that tracks it;
findings whose packages were delivered have been removed. It is a dated
supporting report and owns no work; the packages in the component roadmaps
above carry the plans.

## Codebase review — 25 October 2026

The [codebase review](codebase-review-2026-10-25.md) is a whole-repository
review for defects, inconsistencies, duplicated and dead code, over-complicated
and fragile solutions, made at `main` `859548a4` before the first production
use, with particular attention to the deployment and serving code. Every area
was read in full by the main session or by a reviewer whose findings the main
session verified at their source lines, with probes where a finding turned on
behaviour. It records its method, limits, probes and evidence, and maps every
open finding to the package that tracks it; findings whose packages were
delivered have been removed. It is a dated supporting report and owns no work;
the packages in the component roadmaps above carry the plans.

## price-contour on Polars 2 — 18 October 2026

The [price-contour on Polars 2](price-contour-polars-2.md) plan is a handover
specification for the price-contour project: what is left of it is stage 3,
moving price-contour to Polars' own 2.0 Rust crates once Polars publishes them,
and what Haute checks when it takes that release. It is a supporting report and
owns no Haute work.

## Codebase review — 28 October 2026

The [review](codebase-review-2026-10-28.md) records the initial functional pass
and the inventory-wide source follow-up at `2c3151b0`. The scratch ledger marks
1,081 authored source files and 40 runtime/configuration files as reviewed, with
38 generated modules/assets assessed through contracts and 1,218 test/support files
inventoried for targeted evidence. Its batch-level notes do not prove that every
statement was individually inspected. The 2,377-record ledger is kept outside the
repository; its hashes refer to Windows working-tree bytes, not Git blobs.
Forty-two finding groups were assigned to 30 new component packages and extensions
to 9 existing owners. They cover saved work, calculation and request correctness,
containment, editor state, model/deploy contracts and assurance gates. The report distinguishes
executed witnesses from source-supported variants and states the review limits;
findings whose packages were delivered have been removed.
These additions are not yet assigned delivery rounds. Component packages own
implementation; no production fix is claimed by this review.

## Codebase review — 31 October 2026

The [codebase review](codebase-review-2026-10-31.md) is a whole-codebase
functional and full-inventory source review at `main` `edde9e169` (`argon-review`).
It combines an initial subsystem-wide pass and executed Python behavioural probes
with a systematic batch-by-batch pass over all 961 authored production source files
(`376,420` lines across `src/haute/` and `frontend/src/`, tracked in a scratch
inventory ledger with `0` unread files remaining). It maps the verified findings to
new component packages (`ASSIST-62`, `CACHE-S26`, `CLI-R07`, `EXEC-R19`,
`EDA-R04`, `FGC-R10` to `FGC-R12`, `FMOU-R07`, `FNED-R07`, `FSH-R13`,
`GIT-R19`, `IO-R07`, `MSC-12`, `MOD-R10`, `OPT-R06`, `PCFG-R23`,
`API-R04`, `TBOOST-11`, `TRACE-R04`, `WB-41`) and extensions of `DEP-R12B`,
`DEP-R13` and `MOD-R05`; findings whose packages were delivered have been removed.
It is a supporting report and owns no work; the packages in the component roadmaps
above carry the plans.

## Working protocol

1. Pick one package from its owning component, in the order the
   [delivery plan](delivery-plan.md) gives.
2. For `Reverify`, reproduce the stated failure against `HEAD`; retire the
   package if current code and tests already prove the outcome.
3. Update the owning component specification before changing behaviour.
4. Add the smallest failing regression, implement the change, and run the
   affected verification ladder.
5. Update or remove the package so this folder remains current.

Package IDs are stable and component-owned. Cross-component consumers name a
dependency instead of copying the package.
