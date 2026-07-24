# Modelling improvement backlog

## Scope

Owns training/evaluation/scoring semantics, offsets and loss functions,
split/CV behaviour, model lifecycle and MLflow handoff, training performance,
and modelling workflow capability. Current contracts span
[modelling](../../../specs/modelling/high-level.md) and
[MLflow model registry](../../../specs/mlflow-model-registry/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| MOD-M01 | Reverify | P0 | Carry offsets through evaluation, scoring, contracts, deployment, and UI guidance. | [Offset lifecycle](../../../fable-Review/modelling-node/M01-offset-lifecycle.md) |
| MOD-M02 | Reverify | P0 | Validate Tweedie bounds, make effective loss honest, and close loss-function gaps. | [Loss functions](../../../fable-Review/modelling-node/M02-loss-functions.md) |
| MOD-M03 | Reverify | P0 | Correct weighted evaluation, deviance, PDP grids, and classification guards. | [Evaluation correctness](../../../fable-Review/modelling-node/M03-evaluation-correctness.md) |
| MOD-M04 | Reverify | P0 | Make temporal/group splits sound and design CV/tuning deliberately. | [Splits and cross-validation](../../../fable-Review/modelling-node/M04-splits-and-cv.md) |
| AUD-C14 | Reverify | P0 | Close train/score and MLflow contract residuals not already owned by a more specific MOD package. | [Audit cluster C14](../../../review/REMEDIATION-PLAN.md#c14-modelling-trainscore-semantics--mlflow-logging-contract-gaps) |
| MOD-M06 | Reverify | P0 | Preserve typed failures/results and make model/contract/temp-file lifecycle robust. | [Robustness and lifecycle](../../../fable-Review/modelling-node/M06-robustness-lifecycle.md) |
| MOD-M05 | Reverify | P1 | Reduce staged I/O/PDP cost and make resource logging/scratch behaviour honest. | [Performance](../../../fable-Review/modelling-node/M05-performance.md) |
| MOD-M07 | Reverify | P1 | Add cancel/export/live-loss/run-history workflow and correct misleading UI. | [UX and workflow](../../../fable-Review/modelling-node/M07-ux-workflow.md) |
| MOD-M08 | Reverify | P1 | Unify tracking parameters and generated export configuration. | [Tracking and export drift](../../../fable-Review/modelling-node/M08-tracking-and-export-drift.md) |
| MOD-M09 | Decision | P2 | Add validated capability levers such as monotonicity, metrics, imbalance, warm start, and parameter passthrough. | [Capability levers](../../../fable-Review/modelling-node/M09-capability-levers.md) |

## Dependencies

- [Background jobs and API lifecycle](../background-jobs-api/README.md) owns
  worker supervision; this component owns training semantics and artifacts.
- [Deploy and platform](../deploy-platform/README.md) owns validate/serve
  equivalence; this component owns the model/feature contract it consumes.
- Complete MOD-M01 and correctness/lifecycle work before multiplying the fit
  path through cross-validation or tuning.

## Evidence and retirement

The [modelling Fable review](../../../fable-Review/modelling-node/README.md) and
its [implementation plan](../../../fable-Review/modelling-node/IMPLEMENTATION-PLAN.md)
provide detailed wave ordering and TDD evidence. Reverify every item, and use
audit IDs where the review marks an overlap. Retire accepted packages only
after model semantics, artifacts, and user-visible lifecycle are durable
spec/test contracts.
