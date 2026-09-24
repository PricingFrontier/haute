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
| [Background jobs and API lifecycle](background-jobs-api.md) | Worker terminal states, artifacts, events, cleanup, one worker primitive | `ROAD-WORKER-05` |
| [Caching](caching.md) | Planning and housekeeping cost, the shapes that cannot carry a write recipe, chunked-write bounds, cache identity | `CACHE-S17` |
| [Engineering quality](engineering-quality.md) | Test hygiene, dead code, coverage gates, test organisation | `ENGQ-R06` |
| [Explore and EDA](explore-eda.md) | Advanced pivot and PivotChart parity | — |
| [Frontend shared](frontend-shared.md) | Results store | `FSH-R03` |
| [MLflow model registry](mlflow-model-registry.md) | Explicit MLflow clients for the optimiser log | `MLF-R02` |
| [Optimiser](optimiser.md) | Service extraction, scaling, input isolation | `OPT-P13` |
| [Pipeline config](pipeline-config.md) | Project context, typed configs, editor state, node specification | `PCFG-R04` |
| [Sandbox security](sandbox-security.md) | Every containment comparison through the one check | `SBX-R01` |
| [Server API](server-api.md) | WebSocket broadcast, error translation, generated browser contract | `API-R05` |
| [Submodels](submodels.md) | One reuse mechanism | `SUB-R01` |

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
