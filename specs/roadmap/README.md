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
| [Caching](caching.md) | Automatic API-input table snapshots, planning and housekeeping cost, the shapes that cannot carry a write recipe, chunked-write bounds, cache usage | `CACHE-S08` |
| [Explore and EDA](explore-eda.md) | Report correctness, scale, UX, pivot tables, PivotCharts, analysis, export | `EDA-E09` |
| [Modelling](modelling.md) | RustyStats 0.9.0 upgrade, GLM terms and interactions; proposed XGBoost, LightGBM, and EBM lifecycle support | `MOD-T00` |
| [Optimiser](optimiser.md) | Apply/save correctness, scaling, lifecycle, workers | `OPT-P11` |
| [Pipeline config](pipeline-config.md) | Repair, save validation, and the error a rejected config reports | `PCFG-R01` |

## PR #227 review — 22 September 2026

The [cache and pipeline review](pr-227-review.md) records reproduced defects,
targeted verification, CI evidence and the merge recommendation for head
`97f3e99`. The [focused implementation plan](pipeline-cache-memory-design.md)
is the current plan for the existing Polars/Parquet pipeline and store, narrowed
at the user's request. Supporting probes, inventories and benchmark results are
linked from the review. The Fable report and its [reconciliation](pr-227-fable-reconciliation.md)
are review history; use the current plan for implementation scope. These are
dated review artifacts; component specifications remain the behavior authority.

The subsequent [independent Fable 5.1 review](pr-227-fable-5.1-review.md)
challenges the proposed architecture and validates the findings against source.
Read the [parent reconciliation](pr-227-fable-reconciliation.md) for accepted
simplifications, corrected assumptions and the refined next steps. Its
[runtime provenance](pr-227-fable-5.1-provenance.json) verifies the exact model;
the Fable report is preserved verbatim.

The [implementation evidence](pr-227-implementation-progress.md) records the
subsequent fixes, reproducible before/after memory and runtime measurements,
targeted tests, and remaining CI verification for the focused plan.

The supporting inventories record [cache lifecycle evidence](pr-227-cache-evidence.md),
[materialisation evidence](pr-227-materialisation-evidence.md), and
[CI implementation evidence](pr-227-ci-implementation-evidence.md). The
[Fable review request](pr-227-fable-review-request.md) preserves the scope and
instructions supplied for that independent review.

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
