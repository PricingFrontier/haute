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
| [Assistant](assistant.md) | The shipped examples refused on a copy of Haute that pip installed | `AST-01` |
| [Caching](caching.md) | Node outputs stored by one version of Haute's node code and read as current by the next | `CCH-01` |
| [Deploy](deploy.md) | One rule for which file a relative path means in the editor and in the bundle; a non-finite output refused by name; what a dry run checks; date outputs in test quotes | `DPL-01` |
| [Engineering quality](engineering-quality.md) | Promise rules in the frontend lint gate; a test that never runs in CI; dead branches and unused suppressions; the commands run whole on the shipped examples by a test | `EQ-01` |
| [Execution engine](execution-engine.md) | Pipelines with more than one non-live source under the optimiser and a write; the Constant node's number rule; preamble failure attribution; a join option the planner does not read; a Transform that cross-joins a Constant | `EXE-01` |
| [Frontend graph canvas](frontend-graph-canvas.md) | What one edit costs and what the undo history holds when the graph carries large tables | `FEG-01` |
| [Frontend shared](frontend-shared.md) | Conditional writes the API client retries | `FES-01` |
| [Git integration](git-integration.md) | A catch-up that cannot remove an open file and leaves the branch moved | `VCS-01` |
| [IO layer](io-layer.md) | Inputs whose file is wrong and the fixed text the analyst is given; byte-order marks and empty files in JSON inputs (a decision) | `IOL-01` |
| [MLflow model registry](mlflow-model-registry.md) | The model-load retry that treats every failure as a corrupt artifact; eviction of local model copies; the run picker's silent omissions; a missing run reported as a traceback | `MLR-01` |
| [Modelling](modelling.md) | Temporal splits on a date column with gaps; tuning and rerun messages; evaluation planning cost; the constructor-only split seam; how `haute train` loads a script | `MDL-01` |
| [Pipeline config](pipeline-config.md) | One pointer form for a recovery issue on a nested setting | `PCF-01` |
| [Pipeline parsing and code generation](codegen.md) | Constructor forms rewritten on save; two hand-written statements the parser cannot read; code generation's cost per edge; decorator keywords a Transform drops; Polars imported under another name; names bound through `exec` or `globals()` | `CGEN-01` |
| [Polars 2](polars-2.md) | Remaining RAM-estimate calibration, large-scale operator measurements and worker baselines; Map columns and quantile/rank binning (deferred) | `PL2-17` |
| [Polars node clarity](polars-node-clarity.md) | Step card visual baseline, formula comparisons (deferred) | `PNC-13` |
| [Sandbox and local security](sandbox-security.md) | The hosted editor's cross-site checks; NumPy 1 pickles and the refusal's wording; temporary folders shared between users | `SBS-01` |
| [Server API](server-api.md) | Node failures answered "check the server logs" (a decision); live sync on the event loop; a landed save answered as failed | `SRV-02` |
| [Workbench](workbench.md) | The underwriter app on hosts past Azure Container Apps (a Databricks App, AWS, internal platforms); a workbench project's CI/CD: a merge to `main` takes form, pipeline and app, tested together, to staging and production; managing quotes in the app past the Quotes page: outcomes, many quotes at once; exports from the app (awaiting a decision on formats); test quotes analysts keep and price again, built again after the first version was taken out (awaiting decisions); actions that fill the sheets from databases and APIs (awaiting decisions on auth and sources); the app's sign-in (a decision); typed-in allowed values (a decision); `useQuoteListStore` pagination fencing and submodel `WORKBENCH_OUTPUT` resolution in `priceSample`; from the 9 November 2026 review: the quote store's missing timeouts, a release built with outputs nothing fills (a decision), typed numbers with a misplaced comma, the hosted check's single probes, a save priced before it is refused, a form request answered 500, a column name the save accepts and the pipeline refuses, and what a failed pricing tells the underwriter (a decision) | `WB-08` |

## Delivery plan

The [delivery plan](delivery-plan.md) orders scheduled packages into rounds
delivered one PR at a time from a single checkout, with each round's
dependencies, its size and the user-visible changes it makes. It owns only
the order; the packages above own the work. The packages the
[25 October 2026 codebase review](codebase-review-2026-10-25.md) added, and the
documentation packages from that day's check of the deployment guides, are not yet placed
in its rounds.

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
new component packages (`FGC-R10` to `FGC-R12`, `FMOU-R07`, `FNED-R07`,
`GIT-R19`,
`WB-41`) and extensions of `DEP-R12B` and
`DEP-R13` (delivered, with the deploy roadmap retired); findings whose packages were
delivered have been removed.
It is a supporting report and owns no work; the packages in the component roadmaps
above carry the plans.

## Codebase review plan — 10 November 2026

The [codebase review plan](codebase-review-plan.md) is the method and the
order for finding defects in the part of the backend and frontend that the
9 November 2026 review did not reach: wrong inputs made by machine, injected
failures, one answer computed more than one way, the editor in a real
browser, and environments not yet run. It is a supporting report and owns
no work; a reproduced finding becomes a package in its component's roadmap
above, and a phase leaves the plan when it has run.

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
