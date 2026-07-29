# Haute engineering roadmap

This folder is the single source of truth for planned engineering
improvements. Each component roadmap contains the problem, implementation
direction, acceptance criteria, dependencies, and current code/test evidence
needed to take one package through delivery.

Shipped behaviour remains defined by code, tests, and the component
specifications. `Reverify` packages came from older evidence and must be
reproduced against `HEAD` before implementation. `Decision` packages require
an explicit product or architecture choice. Remove a package when its outcome
is covered by current specifications and ordinary regression tests.
`Start with` names a non-deferred package; it is `—` when a component has no
currently startable package.

| Component | Improvement surface | Start with |
|---|---|---|
| [Assistant](assistant.md) | Session fidelity, authoring feedback, provider/model workflow | — |
| [Background jobs and API lifecycle](background-jobs-api.md) | Worker terminal states, artifacts, events, cleanup | — |
| [Caching](caching.md) | Fingerprint completeness, invalidation, lifetime, concurrency | — |
| [Deploy and platform](deploy-platform.md) | Deployment paths, scaffolding, platform/resource boundaries | — |
| [Engineering quality](engineering-quality.md) | Invariants, oracles, fixtures, CI, types, documentation truth | — |
| [Execution engine](execution-engine.md) | Execution boundary, projection, memory, faults, metrics | — |
| [Explore and EDA](explore-eda.md) | Report correctness, scale, UX, analysis, export | `EDA-E09` |
| [Frontend and canvas](frontend-canvas.md) | Cache/sync correctness, journeys, visibility, accessibility | — |
| [Git integration](git-integration.md) | Mutation safety, history integrity, performance, feedback | — |
| [I/O layer](io-layer.md) | Input/output correctness, formats, caches, editor workflow | — |
| [Modelling](modelling.md) | Training/scoring correctness, lifecycle, performance, capability | — |
| [Optimiser](optimiser.md) | Apply/save correctness, scaling, lifecycle, workers | `OPT-P11` |
| [Pipeline authoring](pipeline-authoring.md) | Parser, code generation, standalone and DSL contracts | — |
| [Rating](rating.md) | Key canonicalisation and persisted table round trips | — |
| [Security and supply chain](security-supply-chain.md) | Trust boundaries and dependency risk | — |
| [Tracing and explainability](tracing-explainability.md) | Evaluation fidelity, row correlation, waterfall honesty | — |

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
