# Optimiser improvement backlog

## Scope

Owns optimiser configuration, solve/frontier semantics, apply/save artifacts,
ratebook behaviour, optimiser-specific performance, interruptibility, and
optimiser workflow. Current contracts live in the
[optimiser specification](../../../specs/optimiser/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| OPT-P01 | Reverify | P0 | Preserve heavy state across multiple frontier-point applies. | [Frontier multi-point apply](../../../fable-Review/optimisation/P01-frontier-multipoint-apply.md) |
| OPT-P02 | Reverify | P0 | Make saved optimiser artifacts atomic, finite, versioned, and apply-compatible. | [Save artifact contract](../../../fable-Review/optimisation/P02-save-artifact-contract.md) |
| OPT-P03 | Reverify | P0 | Reject null/non-finite constraint values before they can bias a solve. | [Null constraint validation](../../../fable-Review/optimisation/P03-null-constraint-validation.md) |
| AUD-C10 | Reverify | P0 | Close numerical/silent-failure residuals not already owned by a more specific OPT package, then decompose duplicated orchestration carefully. | [Audit cluster C10](../../../review/REMEDIATION-PLAN.md#c10-optimiser-numerical--silent-failure-cluster-5046-loc-god-file) |
| OPT-P06 | Reverify | P1 | Remove frontier scaling multipliers and introduce bounded parallel work where safe. | [Frontier compute scaling](../../../fable-Review/optimisation/P06-frontier-compute-scaling.md) |
| OPT-P07 | Reverify | P1 | Remove redundant setup, auto-range, count, preview, and statistics I/O passes. | [Setup I/O passes](../../../fable-Review/optimisation/P07-setup-io-passes.md) |
| OPT-P09 | Reverify | P1 | Avoid re-applying a whole portfolio for each trace click. | [Trace apply recomputation](../../../fable-Review/optimisation/P09-trace-apply-recompute.md) |
| OPT-P08 | Reverify | P1 | Bound optimiser memory/disk retention and sweep orphaned artifacts. | [Memory lifecycle](../../../fable-Review/optimisation/P08-memory-lifecycle.md) |
| OPT-P05 | Reverify | P1 | Make solve cancellation, timeout reporting, and admission release truthful. | [Solve interruptibility](../../../fable-Review/optimisation/P05-solve-interruptibility.md) |
| OPT-P04 | Reverify | P1 | Move synchronous heavy endpoints behind the established job/worker boundary. | [Synchronous endpoints to jobs](../../../fable-Review/optimisation/P04-sync-endpoints-jobs.md) |
| OPT-P10 | Reverify | P2 | Batch verified dead-code, duplication, and elegance improvements. | [Elegance and dead code](../../../fable-Review/optimisation/P10-elegance-dead-code.md) |

## Dependencies

- [Rating](../rating/README.md) owns shared key canonicalisation used by
  ratebook save/apply.
- [Background jobs and API lifecycle](../background-jobs-api/README.md) owns
  the worker/job protocol used by OPT-P04/P05.
- [Execution engine](../execution-engine/README.md) owns admission, metrics,
  and fault primitives; optimiser owns feature-specific numerical acceptance.

## Evidence and retirement

The [optimisation Fable review](../../../fable-Review/optimisation/README.md)
contains detailed ordering, measurements, and TDD plans. Audit C10 is folded
into this owner rather than kept as a second optimiser queue. Reverify each
package; close only with exact numerical/artifact tests and current optimiser
specification updates.
