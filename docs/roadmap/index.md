# Component improvement catalogue

**Current as of:** 2026-07-24

This is the working view of Haute's remaining engineering opportunities. It
groups the surviving Fable reviews, audit clusters, and delivery roadmaps by
the component that owns the outcome. The source reports stay where they were
written for provenance; these component queues decide what is actually picked
up next.

The catalogue is non-normative. Shipped behaviour remains defined by code,
tests, component specifications, and public documentation.

## How to work the catalogue

1. Pick one component, then one package from its ordered queue.
2. Re-verify the cited evidence against `HEAD`. `Reverify` means the source is
   point-in-time evidence, not a confirmed current defect.
3. Write or update the component specification before changing behaviour.
4. Add the smallest failing regression test, implement the package, and run
   the affected verification ladder.
5. Update the owning component queue. Remove a package only when its acceptance
   criteria are proved and durable rationale lives in specs/tests; record a
   blocked or declined decision explicitly rather than silently dropping it.

Package IDs are source-qualified (`AUD-*`, `EDA-*`, `GIT-*`, `IO-*`, `MOD-*`,
`OPT-*`, or `ROAD-*`). An ID has exactly one owning queue. Other components
link to that owner when they consume the result.

`Active` packages have a current delivery roadmap; `Reverify` packages come
from older review evidence; `Decision` packages need an explicit product or
architecture choice before implementation.

## Components

| Component | Primary improvement surface | Suggested starting package |
|---|---|---|
| [Background jobs & API lifecycle](components/background-jobs-api/README.md) | Terminal states, worker artifacts/events, route lifecycle, cleanup | `ROAD-WORKER-01` |
| [Caching](components/caching/README.md) | Key completeness, cache lifecycle, concurrency, invalidation | `AUD-C03` |
| [Deploy & platform](components/deploy-platform/README.md) | Validate/serve parity, packaging paths, platform boundaries | `AUD-C04` |
| [Edge Join](components/edge-join/README.md) | Discoverable insertion, browser workflow, documentation | `ROAD-EDGE-01` |
| [Engineering quality](components/engineering-quality/README.md) | Invariants, oracles, fixtures, CI, types, documentation truth | `ROAD-TEST-01` |
| [Execution engine](components/execution-engine/README.md) | Execution boundary, projection, memory bounds, faults, metrics | `ROAD-EXEC-01` |
| [Explore / EDA](components/explore-eda/README.md) | Report correctness, scale, UX, charts, relationships, export | `EDA-E01` |
| [Frontend & canvas](components/frontend-canvas/README.md) | Cache/sync correctness, journeys, visibility, visual/a11y evidence | `AUD-C16` |
| [Git integration](components/git-integration/README.md) | Mutation safety, history integrity, performance, user feedback | `GIT-G01` |
| [I/O layer](components/io-layer/README.md) | Input/output correctness, JSON shred, formats, editor workflow | `IO-IO03` |
| [Modelling](components/modelling/README.md) | Offset/loss correctness, lifecycle, performance, capability | `MOD-M01` |
| [Optimiser](components/optimiser/README.md) | Apply/save correctness, scaling, lifecycle, worker migration | `OPT-P01` |
| [Pipeline authoring](components/pipeline-authoring/README.md) | Parser/codegen/standalone equivalence and public DSL contracts | `AUD-C05` |
| [Rating](components/rating/README.md) | Key canonicalisation and save/apply dtype agreement | `AUD-C06` |
| [Security & supply chain](components/security-supply-chain/README.md) | Deserialisation, path/session boundaries, dependency advisories | `AUD-C18` |
| [Tracing & explainability](components/tracing-explainability/README.md) | Expression fidelity, row correlation, waterfall honesty | `AUD-C07` |

## Source hierarchy

- The [HEAD re-verification report](../review/06-reverification/REPORT.md) is the
  most recent audit status source. Its 17 `FIXED`/`OBSOLETE` findings are not
  queued.
- [Fable Review](../fable-Review/README.md) supplies component-sized packages
  with detailed evidence and TDD plans. Every retained package still requires
  a current closure pass.
- The five active delivery roadmaps remain the detailed acceptance source for
  execution, worker isolation, frontend quality, Edge Join, and test-suite
  hardening.
- Phase reports, the master finding index, cleared lists, and the
  [tracing performance baseline](tracing-performance-baseline-2026-07-23.md)
  are evidence, not additional queues.

## Cross-component sequencing

1. Security and silent-wrongness packages take precedence over performance,
   cleanup, and new capability.
2. Establish the canonical execution and worker lifecycle contracts before
   migrating modelling or optimiser work.
3. Engineering-quality and frontend-quality packages define shared evidence
   conventions; feature components still own their feature-specific tests.
4. Cache identity, parser/codegen equivalence, and frontend sync work must name
   one owner even when several components consume the invariant.

## Retirement rules

A package leaves its component page only after the relevant code and tests
prove the outcome and current specifications/documentation carry the lasting
contract. A component folder can be removed when its queue is empty and no
active roadmap remains. Historical review material may then be deleted only
when it has no unique evidence value and Git retains the recovery path.
