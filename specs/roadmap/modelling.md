# Modelling roadmap

## Scope

Training, evaluation, scoring, split semantics, artifacts, MLflow handoff,
performance, and modelling workflows remain trustworthy.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-M01–MOD-M04, AUD-C14, MOD-M06 | Active | P0 | Correct model semantics and lifecycle contracts. |
| MOD-M05, MOD-M07–MOD-M08 | Active | P1 | Reduce cost and complete the workflow. |
| MOD-M09 | Decision | P2 | Select validated additional modelling levers. |

## Planned improvements

### MOD-M01 — Offset lifecycle
**Why:** Offset semantics can disappear between training, evaluation, scoring, deployment, and user guidance.

**Plan:** Carry validated offsets through every model and feature contract boundary.

**Acceptance:** Train/evaluate/score/deploy fixtures with offsets produce consistent predictions and clear configuration errors.

**Dependencies:** MOD-M03, MOD-M06.

**Evidence:** `src/haute/modelling`; `src/haute/_model_scorer.py`; `tests/test_modelling.py`; `specs/modelling/high-level.md`.

### MOD-M02 — Loss functions
**Why:** Tweedie bounds and effective loss reporting can diverge from fitted behaviour.

**Plan:** Validate supported loss parameters and expose the actual effective loss consistently.

**Acceptance:** Boundary and invalid-loss tests verify fit rejection, metrics, and UI/config reporting.

**Dependencies:** MOD-M03.

**Evidence:** `src/haute/modelling/_algorithms.py`; `tests/test_modelling.py`.

### MOD-M03 — Evaluation correctness
**Why:** Weighted metrics, deviance, PDP grids, and classification guards can produce misleading results.

**Plan:** Centralise evaluation semantics and validate metric/PDP applicability before computation.

**Acceptance:** Weighted, classification, regression, invalid-grid, and known-value fixtures match expected metrics.

**Dependencies:** MOD-M01, MOD-M02.

**Evidence:** `src/haute/modelling`; `tests/test_modelling.py`.

### MOD-M04 — Splits and cross-validation
**Why:** Temporal/group leakage and undeclared CV/tuning semantics undermine model results.

**Plan:** Validate temporal/group split invariants and define bounded CV/tuning orchestration before multiplying fits.

**Acceptance:** Tests reject leakage and invalid folds, preserve group/temporal ordering, and prove reproducible CV results.

**Dependencies:** MOD-M01–MOD-M03, MOD-M06.

**Evidence:** `src/haute/modelling`; `tests/test_modelling.py`; `specs/modelling/high-level.md`.

### AUD-C14 — Train/score and MLflow residuals
**Why:** Classification prediction meaning, pyfunc loading, and temporal/decimal signature mapping are inconsistent.

**Plan:** Choose one prediction contract across metrics and scoring, provide a real MLflow loader for non-native models, and map supported Polars temporal/decimal types.

**Acceptance:** Classification train/score parity, pyfunc load-and-predict, and temporal/decimal MLflow logging tests pass.

**Dependencies:** MOD-M01, MOD-M03, MOD-M06.

**Evidence:** `src/haute/modelling/_algorithms.py`; `src/haute/_model_scorer.py`; `src/haute/_mlflow_io.py`; `src/haute/modelling/_signature.py`; `tests/test_modelling.py`.

### MOD-M06 — Robustness and lifecycle
**Why:** Typed failures, model artifacts, contracts, and temporary files need reliable ownership and cleanup.

**Plan:** Preserve typed results/failures across the job boundary and make artifact and temporary-resource lifecycle explicit.

**Acceptance:** Failure injection tests retain domain errors, clean safe temporaries, and preserve usable artifacts.

**Dependencies:** Background-job lifecycle.

**Evidence:** `src/haute/modelling/_training_job.py`; `src/haute/routes/_train_service.py`; `tests/test_modelling.py`.

### MOD-M05 — Training performance
**Why:** Staged I/O and PDP work can dominate training while resource logging/scratch reporting is misleading.

**Plan:** Remove redundant passes, bound PDP computation, and report actual scratch/resource use. Benchmark the CatBoost array handoff before forcing C-contiguous copies; implement only if the profile shows material benefit without increasing peak memory.

**Acceptance:** Structural performance tests prove fewer passes and bounded PDP work without changing results. The CatBoost contiguity gate records its workload, artifact, memory/result equivalence, and implement/no-change decision.

**Dependencies:** MOD-M03, MOD-M06.

**Evidence:** `src/haute/modelling`; `tests/test_modelling.py`.

### MOD-M07 — Workflow UX
**Why:** Cancellation, export, live loss, run history, and error states are incomplete or misleading.

**Plan:** Add explicit lifecycle-driven controls and truthful progress/history/export presentation.

**Acceptance:** UI tests cover start, cancel, terminal states, live loss, export, and historical runs.

**Dependencies:** MOD-M06.

**Evidence:** `frontend/src`; `src/haute/routes/modelling.py`; `frontend/src/**/*.test.tsx`.

### MOD-M08 — Tracking and export drift
**Why:** Tracking parameters and generated export configuration can represent different models.

**Plan:** Derive tracking and export configuration from one validated model configuration.

**Acceptance:** Generated config, tracking payload, and runtime fit agree for representative configurations.

**Dependencies:** MOD-M01, MOD-M02.

**Evidence:** `src/haute/modelling`; `src/haute/modelling/_export.py`; `tests/test_modelling.py`.

### MOD-M09 — Capability levers
**Why:** Monotonicity, metrics, imbalance, warm start, and passthrough need validated product decisions.

**Plan:** Add selected levers only with algorithm support checks, schema validation, and reproducible semantics.

**Acceptance:** Each accepted lever has unsupported-case errors and train/score regression coverage.

**Dependencies:** MOD-M01–MOD-M06.

**Evidence:** `src/haute/modelling`; `src/haute/schemas.py`; `tests/test_modelling.py`.
