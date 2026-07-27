# Modelling roadmap

## Scope

Training, evaluation, scoring, split semantics, artifacts, MLflow handoff,
performance, and modelling workflows remain trustworthy. Current behaviour is
specified in [modelling](../modelling/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-M04, AUD-C14 | Active | P0 | Complete CV orchestration and MLflow temporal/decimal signatures. |
| MOD-M05, MOD-M07 | Active | P1 | Record the CatBoost handoff decision and complete workflow UX. |
| MOD-M09 | Decision | P2 | Select validated additional modelling levers. |

## Planned improvements

### MOD-M04 — Splits and cross-validation
**Why:** Temporal/group leakage and undeclared CV/tuning semantics undermine model results.

**Plan:** Validate temporal/group split invariants and define bounded CV/tuning orchestration before multiplying fits.

**Acceptance:** Tests reject leakage and invalid folds, preserve group/temporal ordering, and prove reproducible CV results.

**Dependencies:** Delivered training/evaluation and lifecycle contracts (formerly MOD-M01–MOD-M03, MOD-M06).

**Evidence:** `src/haute/modelling`; `tests/test_modelling.py`; `specs/modelling/high-level.md`.

### AUD-C14 — Train/score and MLflow residuals
**Why:** Classification prediction meaning, pyfunc loading, and temporal/decimal signature mapping are inconsistent.

**Plan:** Choose one prediction contract across metrics and scoring, provide a real MLflow loader for non-native models, and map supported Polars temporal/decimal types.

**Acceptance:** Classification train/score parity, pyfunc load-and-predict, and temporal/decimal MLflow logging tests pass.

**Dependencies:** Delivered offset, evaluation, and lifecycle contracts (formerly MOD-M01, MOD-M03, MOD-M06).

**Evidence:** `src/haute/modelling/_algorithms.py`; `src/haute/_model_scorer.py`; `src/haute/_mlflow_io.py`; `src/haute/modelling/_signature.py`; `tests/test_modelling.py`.

### MOD-M05 — Training performance
**Why:** Staged I/O and PDP work can dominate training while resource logging/scratch reporting is misleading.

**Plan:** Remove redundant passes, bound PDP computation, and report actual scratch/resource use. Benchmark the CatBoost array handoff before forcing C-contiguous copies; implement only if the profile shows material benefit without increasing peak memory.

**Acceptance:** Structural performance tests prove fewer passes and bounded PDP work without changing results. The CatBoost contiguity gate records its workload, artifact, memory/result equivalence, and implement/no-change decision.

**Dependencies:** Delivered evaluation and lifecycle contracts (formerly MOD-M03, MOD-M06).

**Evidence:** `src/haute/modelling`; `tests/test_modelling.py`.

### MOD-M07 — Workflow UX
**Why:** Cancellation, export, live loss, run history, and error states are incomplete or misleading.

**Plan:** Add explicit lifecycle-driven controls and truthful
progress/history/export presentation. Rename or reshape the GPU feasibility
check so a 507 clearly requests a user-selected CPU retry and never implies
that an automatic fallback occurred.

**Acceptance:** UI tests cover start, cancel, terminal states, live loss,
export, historical runs, and the insufficient-VRAM path with an actionable
manual CPU-retry message.

**Dependencies:** Delivered robustness and lifecycle contracts (formerly MOD-M06).

**Evidence:** `frontend/src`; `src/haute/routes/modelling.py`;
`src/haute/routes/_train_service.py`; `tests/test_train_service_coverage.py`;
`frontend/src/**/*.test.tsx`.

### MOD-M09 — Capability levers
**Why:** Monotonicity, metrics, imbalance, warm start, and passthrough need validated product decisions.

**Plan:** Add selected levers only with algorithm support checks, schema validation, and reproducible semantics.

**Acceptance:** Each accepted lever has unsupported-case errors and train/score regression coverage.

**Dependencies:** MOD-M04 and MOD-M05; otherwise delivered training contracts (formerly MOD-M01–MOD-M03, MOD-M06).

**Evidence:** `src/haute/modelling`; `src/haute/schemas.py`; `tests/test_modelling.py`.

## Delivered outcomes

- Offset lifecycle, loss-function validation, evaluation correctness,
  robustness/lifecycle, and tracking/export parity (`MOD-M01`–`MOD-M03`,
  `MOD-M06`, `MOD-M08`) are present-tense contracts in
  [the modelling specification](../modelling/high-level.md), enforced by
  `tests/test_offset_scoring.py`, `tests/test_modelling.py`,
  `tests/test_train_service_coverage.py`, and
  `tests/test_modelling_export.py`.
- The split-leakage half of `MOD-M04` (temporal/group ordering, null-date
  rejection, holdout recency) and the redundant-pass/PDP-bounding half of
  `MOD-M05` are also delivered; only the CV/tuning orchestration and the
  CatBoost contiguity decision remain active above. Monotone constraints from
  `MOD-M09`'s candidate list are implemented for both algorithms but await the
  package's product decision on schema exposure.
