# Modelling roadmap

## Scope

Training, evaluation, scoring, split semantics, artifacts, MLflow handoff,
performance, and modelling workflows remain trustworthy. Current behaviour is
specified in [modelling](../modelling/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-M04 | Planned | P2 | Add reproducible bounded cross-validation as a product-scoped feature. |

## Planned improvements

### MOD-M04 — Splits and cross-validation
**Why:** The split-leakage contract is complete, but multi-fit cross-validation
is a net-new modelling workflow whose fold identity, cancellation, resource
ownership, and result shape must be explicit before fits are multiplied.

**Plan:** Deliver this in two independent phases. First define a versioned,
reproducible fold-plan artifact and a single-fold train/evaluate contract for
random, group, and temporal strategies. Then add bounded sequential
orchestration and aggregate/per-fold reporting after the supervised training
worker can own multiple fits under one admission lease and cancellation token.
Hyperparameter search is not part of this package and requires a separate
product contract.

**Acceptance:** Fold plans are reproducible, reject leakage and invalid folds,
preserve group/temporal ordering, and round-trip through the declared artifact
schema. Orchestration proves a fixed fit bound, deterministic result ordering,
whole-run cancellation, admission release, and aggregate metrics derived from
the exact persisted per-fold results.

**Dependencies:** Delivered training/evaluation and lifecycle contracts
(formerly MOD-M01–MOD-M03, MOD-M06), plus an explicit multi-fit extension of
the supervised child-worker protocol.

**Evidence:** `src/haute/modelling`; `tests/test_modelling.py`; `specs/modelling/high-level.md`.

## Delivered outcomes

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
- The split-leakage half of `MOD-M04` (temporal/group ordering, null-date
  rejection, holdout recency) and the redundant-pass/PDP-bounding half of
  `MOD-M05` are also delivered. The remaining CV work is deliberately
  separated above as a net-new product feature rather than audit debt.
- `MOD-M07` returns a pollable job handle before preparation, uses one
  idempotent cancellation token across preparation and fitting, preserves a
  terminal race winner, and reports GPU-memory rejection as an actionable
  manual CPU retry without claiming or performing an automatic fallback.
