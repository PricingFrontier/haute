# Modelling roadmap

## Scope

Training, evaluation, scoring, split semantics, artifacts, MLflow handoff,
performance, and modelling workflows remain trustworthy. Current behaviour is
specified in [modelling](../modelling/high-level.md).

## Priorities

There are no active modelling improvement packages.

## Planned improvements

No further modelling improvement is planned after delivery of the unified
evaluation and bounded-tuning packages. New work should be added only with a
reproduced correctness, performance, or workflow gap.

## Delivered outcomes

- `MOD-M12` adds optional strict version-1 bounded CatBoost tuning on the exact
  persisted development-only evaluation plan. A seeded sequential Optuna 4.x
  TPE sampler evaluates the fixed baseline plus sampled trials under one
  admission/cancellation lifecycle, enforces the 5–50 trial and 200
  trial-fit bounds, selects the metric-directed winner deterministically,
  derives the final tree count from validation evidence, and performs only one
  deployable final fit. Digest-linked plan/trials/report artifacts, the
  completed response, model card, MLflow run, exported script, progress UI,
  Summary surface, and **Use best as fixed parameters** action share the same
  strict contract. Evidence lives in
  [the modelling specification](../modelling/high-level.md#bounded-deterministic-catboost-tuning),
  `tests/test_tuning.py`, `tests/test_training_tuning.py`,
  `tests/test_training_response_evaluation.py`, and the worker/route/frontend
  contract suites.
- `MOD-M11` replaces the public `split` and `cross_validation` configuration
  with one strict version-1 `evaluation` object. It reserves the final test
  before deriving development-only single or cross-validation fits, persists
  exact deterministic random/group/temporal membership, reuses the same plan
  for selection and tuning, and performs one final fit on all development
  rows. The preflight preview, result labels, transactional artifacts, live
  training, export, MLflow, backend schemas, and frontend guards use the same
  development/validation/final-test vocabulary. Evidence lives in
  [the modelling specification](../modelling/high-level.md#unified-evaluation-and-bounded-tuning),
  `tests/test_evaluation.py`, `tests/test_train_evaluation_config.py`,
  `tests/test_training_evaluation.py`, and
  `tests/test_training_response_evaluation.py`.
- `MOD-M10` presents supported modelling nodes as Target, Features, Params,
  Split, and Train panes with per-node memory, plain setup-tab labels, an
  accessible active-training indicator, and click-time validation beneath
  Train. CatBoost and GLM share the role-aware feature browser and atomic
  dependency cleanup. CatBoost begins Params with a Fixed/Tune strategy choice:
  Fixed shows its algorithm-neutral JSON-object parameter editor, while Tune
  shows bounded tuning controls and compact search-space JSON. Both autosave
  top-level objects accepted by their frontend parser and preserve locally
  rejected per-node drafts for click-time Train validation; fixed parameters
  retain access to arbitrary current and future CatBoost keys, while detailed
  tuning semantics remain backend-authoritative.
  Live progress retains the backend loss window and derives a bounded browser
  ETA. The present-tense contract and focused evidence live in
  [the modelling/optimiser UI specification](../frontend-modelling-optimiser-ui/high-level.md#modelling-config-panes).
- `MOD-M04` delivered bounded two-to-ten-fold cross-validation with sequential
  same-child orchestration, whole-run cancellation, exact persisted-result
  aggregation, a final ordinary fit, and rollback-capable publication.
  `MOD-M11` absorbed that capability into the unified `evaluation` contract as
  its `cross_validation` validation method, and the standalone fold-plan
  orchestrator has since been removed outright. The present-tense contract and
  evidence live in
  [the modelling specification](../modelling/high-level.md#unified-evaluation-and-bounded-tuning),
  `tests/test_evaluation.py`, `tests/test_training_evaluation.py`, and the
  worker/route/frontend contract suites.
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
  Both algorithms reject malformed directions, absent/non-selected names, and
  non-numeric features after final feature/term selection; the editor offers
  only selected numeric inputs. Warm start, class-imbalance controls,
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
- The split-leakage behaviour (temporal/group ordering, null-date rejection,
  final-test recency) and the redundant-pass/PDP-bounding half of `MOD-M05`
  are also delivered.
- `MOD-M07` returns a pollable job handle before preparation, uses one
  idempotent cancellation token across preparation and fitting, preserves a
  terminal race winner, and reports GPU-memory rejection as an actionable
  manual CPU retry without claiming or performing an automatic fallback.
