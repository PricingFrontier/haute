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

| Component | Improvement surface | Start with |
|---|---|---|
| [Assistant](assistant.md) | Blocking data findings, structured step authoring, typed banding and rating, item-level edits and per-model qualification (all deferred) | — |
| [Background jobs and API lifecycle](background-jobs-api.md) | Worker terminal states, artifacts, events, cleanup, one worker primitive | `ROAD-WORKER-05` |
| [Bugs](bugs.md) | Defects found outside component work: a deployed pipeline that skips its submodels, statements a save drops from a submodel file, generated CI/CD files that fail before a first release, help text that no longer matches behaviour, and a deployed Quote Input that hands every port the whole request | `BUG-18` |
| [Caching](caching.md) | Planning and housekeeping cost, the shapes that cannot carry a write recipe, chunked-write bounds, cache identity | `CACHE-S17` |
| [Engineering quality](engineering-quality.md) | Model-training test cost, order- and load-sensitive tests, compatibility shard balance, dead code, test organisation | `ENGQ-CI03` |
| [Explore and EDA](explore-eda.md) | Advanced pivot and PivotChart parity | — |
| [Frontend shared](frontend-shared.md) | Results store | `FSH-R03` |
| [Model scoring](model-scoring.md) | Models trained outside Haute | `MSC-06` |
| [Modelling](modelling.md) | Adaptive low-memory fitting: exact chunked GLM, a low-memory boosted-tree mode (both awaiting a decision) | `MOD-M01` |
| [Name collisions](name-collisions.md) | Input-binding editors showing their name violations inline | `NAME-09` |
| [Optimiser validation](optimiser-validation.md) | Per-point convergence traces from price_contour's frontier sweep; a calibrated solve-memory forecast in the Solve panel; the optimiser result workspace's open robustness and CSV questions | `OPT-PC04` |
| [Pipeline config](pipeline-config.md) | Project context, typed configs, editor state, node specification | `PCFG-R04` |
| [Polars 2](polars-2.md) | Moving to Polars 2.0: caches computed under Polars 1, new dtypes, deploy parity, generated and teaching code, the operation registry and the tests; then the operator memory evidence, worker baselines and the price_contour boundary | `PL2-19` |
| [Polars node clarity](polars-node-clarity.md) | Step card visual baseline, formula comparisons (deferred) | `PNC-13` |
| [Sandbox security](sandbox-security.md) | Every containment comparison through the one check | `SBX-R01` |
| [Server API](server-api.md) | Domain errors, generated browser contract | `API-R02` |
| [Submodels](submodels.md) | Submodels registered by import, one reuse mechanism | `SUB-R02` |
| [t-boost](t-boost.md) | What rating-table models make possible: cell-level holdout A/E, unfolding into rating steps, model comparison with premium attribution, measured analyst adjustments (all awaiting a decision) | `TBOOST-02` |
| [Workbench](workbench.md) | The underwriter app on hosts past Azure Container Apps (a Databricks App, AWS, internal platforms); a workbench project's CI/CD: a merge to `main` takes form, pipeline and app, tested together, to staging and production; the quote store on the server (its engine awaiting IT); managing quotes in the app past the Quotes page: comparing revisions, validity, outcomes, many quotes at once; exports from the app (awaiting a decision on formats); test quotes past what Preview shows kept in full; actions that fill the sheets from databases and APIs (awaiting decisions on auth and sources); and what is left of the review of 15 October 2026: Preview priced in one run, the form's file changed outside the editor, the app's sign-in, and building the sheets without a pointer | `WB-08` |

## Delivery plan — 24 September 2026

The [delivery plan](delivery-plan.md) orders every active package into rounds
delivered one PR at a time from a single checkout, with each round's
dependencies, its size and the user-visible changes it makes. It owns only
the order; the packages above own the work.

## Codebase review — 23 September 2026

The [codebase review](codebase-review-2026-09-23.md) is a whole-repository
review for brittleness, over-engineering, weak areas with stronger standard
solutions, and duplication, made at `main` `9319b11d`. It records its method,
limits and evidence, and maps every finding to the package that tracks it.
It is a dated supporting report and owns no work; the packages in the
component roadmaps above carry the plans.

## Workbench review — 15 October 2026

The [Workbench review](workbench-review-2026-10-15.md) is a review of what pull
requests #303 to #309 built, made at `main` `db7d03a5` by five Codex passes and
six Fable 5.1 reviews, every finding verified at its source lines. It looked for
defects, duplicated mechanisms and drift between the specifications and the code.
It records its method, limits and evidence, and maps every finding to the package
made for it: `WB-14` to `WB-22`, `BUG-32` to `BUG-36` and `FSH-R04`. It is a
dated supporting report and owns no work; the packages in the component
roadmaps above carry the plans.

## Polars 2 review — 17 October 2026

The [Polars 2 review](polars-2-review-2026-10-17.md) records what moving Haute
from Polars 1.44.2 to Polars 2.0 breaks, changes silently, makes redundant and
makes possible, made at `5c184d8d`: its sources, side-by-side probes of both
versions, bounded test runs under Polars 2.0.0, a row-order audit and an
API-surface audit. It is a dated supporting report and owns no work; the
[Polars 2 roadmap](polars-2.md) carries the packages.

## t-boost improvements — 4 October 2026

The [t-boost improvements](t-boost-improvements.md) are a handover
specification for the t-boost project: the library changes (a validation set
with ensemble-level early stopping, reported round counts, a metrics callback,
a metadata slot, offsets, an unknown-category policy and smaller items) that
would let Haute integrate t-boost exactly like its other boosted families, with
their status at t-boost 0.8.0. It is a supporting report and owns no Haute work.

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
