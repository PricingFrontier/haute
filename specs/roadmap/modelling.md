# Modelling roadmap

## Scope

Training, evaluation, scoring, split semantics, artifacts, MLflow handoff,
performance, and modelling workflows remain trustworthy. Current behaviour is
specified in [modelling](../modelling/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| — | — | — | No active modelling roadmap package remains. |

## Planned improvements

There are no active modelling roadmap packages.

## Delivered outcomes

- `MOD-M10` presents supported modelling nodes as Target, Features, Params,
  Split, and Train panes with per-node memory, plain setup-tab labels, an
  accessible active-training indicator, and click-time validation beneath
  Train. CatBoost and GLM share the role-aware feature browser and atomic
  dependency cleanup; CatBoost has a single algorithm-neutral, conflict-safe
  JSON-object hyperparameter editor that preserves access to arbitrary current
  and future parameters; live progress retains the backend loss window and
  derives a bounded browser ETA. The present-tense contract and focused evidence live in
  [the modelling/optimiser UI specification](../frontend-modelling-optimiser-ui/high-level.md#modelling-config-panes).
- `MOD-M04` provides strict version-1 random/group/temporal fold plans, a
  two-to-ten-fold bound, sequential same-child orchestration, whole-run
  cancellation, exact persisted-result aggregation, a final ordinary fit, and
  rollback-capable five-artifact publication. The additive completed response
  and result summary expose aggregate and ordered fold metrics. Contracts and
  evidence live in
  [the modelling specification](../modelling/high-level.md#bounded-cross-validation),
  `tests/test_cross_validation.py`, `tests/test_training_cross_validation.py`,
  and the worker/route/frontend contract suites.
- `MOD-M05` keeps the existing Fortran-contiguous `Float32` CatBoost handoff.
  The recorded 2026-07-27 opt-in 100,000-row × 32-feature run measured direct
  `Pool` construction at 484,300 ns median versus 23,716,000 ns for
  `ascontiguousarray` plus `Pool`; the candidate was slower and added a
  12,800,000-byte full-matrix allocation. Feature/label checks and seeded
  predictions were equivalent (prediction delta zero), so the measured
  no-change decision satisfies the 20%-benefit/no-extra-allocation policy.
  `tests/performance/test_catboost_contiguity_perf.py` and the repository
  performance harness retain the workload and decision gate.
- `MOD-M09` selects monotonicity as the sole additional cross-algorithm lever.
  Both algorithms now reject malformed directions, absent/non-selected names,
  and non-numeric features after final feature/term selection; the editor
  offers only selected numeric inputs. Warm start, class-imbalance controls,
  arbitrary metric/passthrough editors, and feature-weight UI are deliberately
  not productised because they lack a shared supported contract. Backend and
  editor regressions pin the accepted lever and unsupported cases.
- `AUD-C14` maps Date and canonical parameterised Datetime descriptors to
  persisted MLflow datetime signatures; the pyfunc scoring boundary converts
  zoned values to UTC-naive pandas datetimes for MLflow enforcement, and
  Decimal signatures fail before logging with explicit String/Float64 cast
  guidance. Real local log/load/predict coverage lives in
  `tests/test_mlflow_signature.py`.
- Offset lifecycle, loss-function validation, evaluation correctness,
  robustness/lifecycle, and tracking/export parity (`MOD-M01`–`MOD-M03`,
  `MOD-M06`, `MOD-M08`) are present-tense contracts in
  [the modelling specification](../modelling/high-level.md), enforced by
  `tests/test_offset_scoring.py`, `tests/test_modelling.py`,
  `tests/test_train_service_coverage.py`, and
  `tests/test_modelling_export.py`.
- The split-leakage behavior (temporal/group ordering, null-date rejection,
  holdout recency) and the redundant-pass/PDP-bounding half of `MOD-M05` are
  also delivered.
- `MOD-M07` returns a pollable job handle before preparation, uses one
  idempotent cancellation token across preparation and fitting, preserves a
  terminal race winner, and reports GPU-memory rejection as an actionable
  manual CPU retry without claiming or performing an automatic fallback.
