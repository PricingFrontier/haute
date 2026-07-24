# Execution engine improvement backlog

## Scope

Owns the canonical eager/lazy/chunked execution boundary, projection planning,
admission and memory bounds, failure/fault vocabulary, metrics, and
request-local execution cleanup. Current contracts live in the
[execution-engine specification](../../../specs/execution-engine/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| ROAD-EXEC-01 | Active | P0 | Route execution through one canonical boundary with one admission, cancellation, and metrics contract. | [Backend hardening milestone 1](../../backend-execution-hardening.md#milestone-1---canonical-execution-boundary) |
| AUD-C13 | Reverify | P0 | Bound chunk memory from projected target width instead of source-only estimates. | [Audit cluster C13](../../../review/REMEDIATION-PLAN.md#c13-chunkmemory-budget-under-bounding-oom-hazard) |
| AUD-C09 | Reverify | P0 | Replace heuristic projection ownership and silent seed loss with explicit, tested demand attribution. | [Audit cluster C9](../../../review/REMEDIATION-PLAN.md#c9-projection-demand-mis-attribution--silent-seed-drop-latent-column-pruning-miss) |
| ROAD-EXEC-02 | Active | P1 | Add deterministic fault injection and cleanup service-level evidence. | [Backend hardening milestone 2](../../backend-execution-hardening.md#milestone-2---fault-injection-and-cleanup-slos) |
| ROAD-EXEC-03 | Active | P1 | Produce reproducible scale evidence and complete execution metrics without hardware-fiction gates. | [Backend hardening milestone 3](../../backend-execution-hardening.md#milestone-3---reproducible-scale-evidence-and-metric-completeness) |
| ROAD-EXEC-04 | Active | P1 | Cover compatibility, cross-strategy invariants, and opt-in telemetry. | [Backend hardening milestone 4](../../backend-execution-hardening.md#milestone-4---compatibility-invariant-and-telemetry-testing) |
| ROAD-EXEC-05 | Active | P2 | Harden startup and request-local housekeeping after boundary semantics are stable. | [Backend hardening milestone 5](../../backend-execution-hardening.md#milestone-5---startup-and-request-local-housekeeping) |

## Dependencies

- [Background jobs and API lifecycle](../background-jobs-api/README.md) owns
  process supervision and feature-route migration.
- [Caching](../caching/README.md) owns shared key/lifetime primitives used by
  preview and execution caches.
- [Pipeline authoring](../pipeline-authoring/README.md) owns generated-file
  equivalence; this component owns execution semantics against which it is
  compared.

## Evidence and retirement

The [backend execution hardening roadmap](../../backend-execution-hardening.md)
contains detailed acceptance criteria. Audit clusters remain candidates until
reproduced on current code. Retire this queue only after the canonical boundary
and its fault, scale, compatibility, telemetry, and cleanup evidence are
ordinary tested contracts.
