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
| [Background jobs and API lifecycle](background-jobs-api.md) | Worker terminal states, artifacts, events, cleanup | — |
| [Caching](caching.md) | Snapshot jobs, analysis results, execution and preview reuse | `CACHE-S03` |
| [Explore and EDA](explore-eda.md) | Report correctness, scale, UX, pivot tables, PivotCharts, analysis, export | `EDA-E09` |
| [Modelling](modelling.md) | RustyStats 0.9.0 upgrade, GLM terms pane, per-feature fits, interaction fits | `MOD-T00` |
| [Optimiser](optimiser.md) | Apply/save correctness, scaling, lifecycle, workers | `OPT-P11` |
| [Polars step builder](polars-steps.md) | Low-code step authoring on the remaining Polars code surfaces | — |
| [Rating](rating.md) | Banding rule claims, whole-dataset banding statistics and rating levels | `RAT-B01` |

## Working protocol

1. Pick one package from its owning component.
2. For `Reverify`, reproduce the stated failure against `HEAD`; retire the
   package if current code and tests already prove the outcome.
3. Update the owning component specification before changing behaviour.
4. Add the smallest failing regression, implement the change, and run the
   affected verification ladder.
5. Update or remove the package so this folder remains current.

Package IDs are stable and component-owned. Cross-component consumers name a
dependency instead of copying the package.
