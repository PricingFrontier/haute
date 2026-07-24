# Background jobs and API lifecycle improvement backlog

## Scope

Owns job state transitions, request supersession, worker supervision,
artifact/event transfer, route timeouts, and cleanup for long-running work.
Current contracts live in the
[background-jobs](../../../specs/background-jobs/high-level.md) and
[server-api](../../../specs/server-api/high-level.md) specifications.

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| ROAD-WORKER-01 | Active | P0 | Make supervisor terminalisation total across success, failure, timeout, cancellation, and process loss. | [Worker isolation milestone 1](../../worker-isolation.md#1-make-supervisor-terminalisation-total) |
| ROAD-WORKER-02 | Active | P0 | Establish typed, versioned artifact and event contracts for spawned work. | [Worker isolation milestone 2](../../worker-isolation.md#2-establish-artifact-and-event-worker-contracts) |
| AUD-C15 | Reverify | P0 | Remove unlocked JobStore reads and make timeout/lifecycle transitions coherent. | [Audit cluster C15](../../../review/REMEDIATION-PLAN.md#c15-jobstore-unlocked-read-concurrency--job-lifecycle-timeout-coherence-routes) |
| ROAD-WORKER-03 | Active | P1 | Migrate model training and dispersion estimation to the canonical worker boundary. | [Worker isolation milestone 3](../../worker-isolation.md#3-migrate-model-training-and-dispersion-estimation) |
| ROAD-WORKER-04 | Active | P1 | Migrate optimiser setup, solve, auto-range, and frontier recomputation without duplicating lifecycle policy. | [Worker isolation milestone 4](../../worker-isolation.md#4-migrate-optimiser-setup-solve-auto-range-and-frontier-recomputation) |
| AUD-C19 | Reverify | P1 | Bound long-lived caches/resources and make server cleanup observable and deterministic. | [Audit cluster C19](../../../review/REMEDIATION-PLAN.md#c19-resource-leak--unbounded-cache-hardening-long-lived-server-hygiene) |
| ROAD-WORKER-05 | Decision | P2 | Decide whether deploy/API execution must enforce the spawned-worker boundary. | [Worker isolation milestone 5](../../worker-isolation.md#5-decide-the-deployment-enforcement-boundary) |

## Dependencies

- [Execution engine](../execution-engine/README.md) owns the canonical
  execution boundary, admission primitives, and fault vocabulary.
- [Modelling](../modelling/README.md) and
  [Optimiser](../optimiser/README.md) own feature semantics and acceptance
  tests for migrated workloads.
- Do not create a second job-state model inside a feature route.

## Evidence and retirement

The [worker-isolation roadmap](../../worker-isolation.md) is the active
acceptance source. Audit clusters are candidates until checked against the
[HEAD re-verification](../../../review/06-reverification/REPORT.md). Retire a
package only when every terminal path is regression-tested and the typed
lifecycle contract is represented in the owning specs.
