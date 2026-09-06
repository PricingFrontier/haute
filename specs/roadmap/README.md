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
| [Explore and EDA](explore-eda.md) | Report correctness, scale, UX, pivot tables, PivotCharts, analysis, export | `EDA-E09` |
| [Optimiser](optimiser.md) | Apply/save correctness, scaling, lifecycle, workers | `OPT-P11` |
| [Specification accuracy](spec-accuracy.md) | Corpus review findings of 6 September 2026: accuracy, stale symbols, writing rules, ownership ledger, structure | `SPEC-A04` |

## Supporting findings

- [Specification corpus review — 6 September 2026](fable5.1-review/report.md)

The dated reports preserve the reviewed snapshot and its evidence; they are not
additional work queues or claims about current behaviour. Delivery status lives
in the owning component roadmap and, for workflow witnesses, in
`tests/workflow_coverage.toml`. Supporting reports are
explicitly enumerated by the documentation checks, included in the corpus
inventory, and use repository-relative links. When the last package a report
supports is retired, the report is removed in the same change and the
enumeration is updated; durable evidence lives in the owning component Testing
sections. Historical probe programs and raw execution logs are not bundled with
these reports; their recorded results are identified as historical evidence.

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
