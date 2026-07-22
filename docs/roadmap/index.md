# Engineering roadmap

**Current as of:** 2026-07-20

This is Haute's active engineering delivery roadmap. It records the remaining
outcomes after the implementation and specification audit; it is intentionally
shorter-lived than a product specification.

Roadmaps describe intended delivery, sequencing, and retirement criteria.
They are **not** the source of truth for shipped behaviour. The code, its
tests, and the public behaviour documentation remain authoritative until a
roadmap item is implemented and verified.

## Active tracks

| Track | Delivered baseline | Remaining outcome |
|---|---|---|
| [Test-suite hardening](test-suite-hardening.md) | The optimiser contiguity incident, contract/parity/property tests, and mutation foundations are covered. | Ratchet high-risk boundary evidence; complete optimiser/ratebook oracle matrices; add seeded parser differential coverage; and publish fixture, regression, and test-health evidence. |
| [Frontend UI quality](frontend-ui-quality.md) | The Banding/Rating Step incident is fixed; Vitest, isolated Playwright, save/reload, and CI foundations exist. | Maintain a configuration-shape and user-journey matrix, prove high-risk browser workflows, make missing choices visible, and add targeted visual, keyboard, accessibility, and CI policy evidence. |
| [Edge Join completion](edge-join-completion.md) | The `edgeJoin` graph node, lazy join execution, configuration, persistence, trace, and deploy paths are implemented. | Make insertion feedback discoverable and accessible, add a deterministic browser workflow, and align user-facing documentation with the interaction and supported join semantics. |
| [Worker isolation](worker-isolation.md) | Spawn-worker and typed supervisor foundations, bounded execution, admission, and lifecycle primitives exist. | Make terminalisation total, establish artifact/event contracts, migrate training and optimiser/auto-range work, and decide the deploy/API enforcement boundary. |
| [Polars execution strategy](polars-execution-strategy.md) ([implementation plan](../trip/plans/F_0.6.0_polars-backend-remediation.plan.md)) | Shared projection planning, bounded execution/chunking, and structured diagnostics exist. | Establish one explicit planning contract across surfaces, explain boundary and feature choices, decide group-by semantics, expose diagnostics, and prove scale behaviour. |
| [Backend execution hardening](backend-execution-hardening.md) | Core admission, bounded helpers, metrics, lifecycle, and cleanup foundations are implemented. | Unify execution boundaries, inject faults, add reproducible scale and compatibility evidence, introduce opt-in telemetry, and harden startup/request-local cleanup. |

The Price Contour ratebook factor-context work is implemented and retired. It
has no active roadmap because it no longer has remaining delivery work.

## Working issue notes

- [API Input UI issue notes](api-input-ui-issues.md) — the four captured issues
  were designed and implemented as v0.4.1 (frame-row node body, frame-named
  downstream inputs); see
  `docs/trip/plans/F_0.4.1_api-input-frame-identity.plan.md`. The note stays as
  the collection point for any further API Input observations.

## Sequencing and ownership

Test-suite hardening and Frontend UI quality are cross-cutting evidence tracks;
they establish shared conventions without taking ownership of feature delivery.
The execution tracks must preserve their boundary of ownership:

1. Test-suite hardening owns backend and cross-boundary invariant/oracle,
   production-shape fixture, and test-health evidence. Feature roadmaps consume
   those conventions but retain their feature-specific acceptance tests.
2. Frontend UI quality owns the shared frontend workflow, visual, accessibility,
   fixture, and CI-tier policy. Edge Join consumes its fixture and browser-harness
   conventions while retaining its node-specific insertion and end-to-end
   acceptance criteria.
3. The Polars track owns planning semantics, Polars capability decisions, and
   user-facing strategy diagnostics. Backend hardening owns the shared error,
   lifecycle, fault-injection, and observability infrastructure those plans
   use.
4. Establish the canonical execution boundary and the explicit strategy
   contract before relying on their diagnostics for broad scale or worker
   migrations.
5. Worker isolation owns spawn protocols, artifact boundaries, and migration
   of heavy routes. It consumes the execution primitives but must not duplicate
   planner or lifecycle policy.
6. Add scale gates after the strategy and metrics contracts are stable; then
   use those gates to protect the completed route migrations and hardening.

When a change spans tracks, its tests must make the ownership split explicit
rather than quietly adding a second planner, lifecycle path, or worker
protocol.

## Retirement rules

A track is retired only when all of its stated completion criteria are met,
the relevant code and tests prove the result, and the public behaviour
documentation has been updated. Move no implementation history into this
directory: durable current behaviour belongs in its product documentation and
tests, while review and reproduction archives remain point-in-time evidence.
