# M01 — Offset/exposure is train-only: evaluation, scoring, and the contract all drop it

**Severity: CRITICAL (two findings) + MEDIUM (one finding)**
**Status: verified empirically against installed catboost 1.2.10 (predictions with vs without baseline differ by exactly the exposure factor).**
**Not tracked by the June 2026 audit — new findings.**

This is the single most important cluster in the modelling-node review. The offset column
(the canonical way to train a Poisson/Tweedie frequency model on claim *counts* with
`offset = log(exposure)`) is honoured only while fitting. Everything downstream of the fit —
reported metrics, every diagnostic chart, the score node, deployment, and the feature
contract — silently ignores it.

---

## Background: what CatBoost `baseline` means (verified)

- `Pool(..., baseline=b)` adds `b` per-row in **raw (log-link) score space** during fit; the
  model learns `f(x)` such that `raw = b + f(x)`.
- For Poisson/Tweedie, `model.predict(X)` returns the **mean** `exp(raw)` (prediction type
  `Exponent` is the default for these losses — verified, `exp(RawFormulaVal) == predict`).
- The baseline is applied at predict time **only if the scoring `Pool` carries one**.
  `model.predict(X)` on a plain matrix/frame uses baseline = 0. Verified:
  `predict(Pool(X, baseline=b))_raw − predict(X)_raw == b` exactly.
- The `.cbm` file stores **no memory of the training baseline**; `feature_names_` contains
  only the feature columns. There is no way to recover the offset from the saved model.

So for an offset-trained frequency model, `predict(X)` returns the **per-unit-exposure
rate**, not the expected count.

---

## Finding M01-1 (CRITICAL): all reported metrics and diagnostics drop the offset

### Evidence
- Fit passes the offset as baseline for both pools:
  `src/haute/modelling/_algorithms.py:306-315` (`_build_pool`), wired from
  `src/haute/modelling/_training_job.py:924-926,940` (train) and `:950-968` (eval).
- Evaluation predicts **without** it:
  `src/haute/modelling/_training_job.py:1139` — `y_pred = algo.predict(model, diag_df, features)`;
  `src/haute/modelling/_algorithms.py:589-615` — `CatBoostAlgorithm.predict` never builds a
  Pool and has no baseline parameter.
- That offset-free `y_pred` feeds **everything**: `compute_metrics` (`_training_job.py:1144`),
  double-lift (`:1179`), AvE per feature (`:1182`), SHAP sample + LossFunctionChange pool
  (`:1198,1203` — pools built without `offset=`), residuals/scatter/Lorenz (`:1220-1222`),
  PDP (`:1230`), and the second validation-metrics read (`:1159-1175`).

### Worked example (synthetic Poisson frequency, target = claim count, offset = log(exposure), weight = exposure, 6 000 rows, 300 trees)

| Quantity | Reported today (offset dropped) | Correct (offset included) |
|---|---|---|
| Global AvE Σpred/Σactual | **0.601** | 1.002 |
| Weighted Gini | **0.822** | 0.893 |
| Poisson deviance | **2.645** | 0.845 |

AvE actual/predicted by exposure tercile (reported path): **0.77 / 1.63 / 2.58** — a smooth
monotone "bias" that is purely an artefact of the dropped offset.

### DS impact
1. **AvE plots lie smoothly.** Any feature correlated with exposure shows a clean monotone
   actual-vs-expected trend. The DS "fixes" a bias that does not exist — adds spurious terms,
   distrusts good factors, chases a ghost.
2. **Understated Gini** → good models get rejected or over-tuned.
3. **Deviance inflated ~3×** → comparing an offset model against a no-offset model on the
   reported deviance systematically favours the wrong model.
4. Early stopping is **not** affected (the eval pool *does* carry the baseline), which makes
   it worse: the loss curve looks healthy while reported metrics look broken — maximally
   confusing.
5. `tests/test_modelling.py:1111-1129` (`test_offset_column`) only asserts metrics are finite
   and the offset isn't a feature — it passes today and would pass with any offset handling
   whatsoever. No regression net exists.

## Finding M01-2 (CRITICAL): scoring and deployment drop the offset; the contract doesn't record it

### Evidence
- `grep baseline|offset src/haute/_model_scorer.py` → **zero matches**. Same for
  `_feature_contract.py`. `src/haute/_mlflow_io.py` has no baseline concept in scoring
  (`_prepare_predict_frame` `_mlflow_io.py:1047-1116`, `_wrap_catboost` `:384-396` derives
  `feature_names` from `model.feature_names_` — offset absent by construction).
- Deploy scorer (`src/haute/deploy/_scorer.py:656-692`) loads the `.cbm` and scores with a
  features-only frame.
- `build_contract` (`src/haute/modelling/_feature_contract.py:96-134`) records features,
  dtypes, categoricals, target, task — **no offset column, no offset semantics**.

### DS impact
A model trained with `offset=log(exposure)` serves `exp(f(x))` (a per-unit-exposure rate)
with no record anywhere that an offset ever existed. If the pipeline/consumer expects counts,
aggregate predictions are wrong by the exposure distribution (~40% low in the worked
example). Even if a rate is what's wanted, nothing enforces or documents that the consumer
must multiply by exposure — and impact analysis / trace views present the raw score node
output as "the prediction". This reaches production quotes; it is arguably worse than M01-1.

## Finding M01-3 (MEDIUM): nothing validates that the offset column is in log space

- UI label is the only guidance: "Offset column (optional, e.g. log-exposure)"
  (`frontend/src/panels/modelling/TargetAndTaskConfig.tsx:66`).
- `_build_pool` uses the column verbatim; `_validate_columns`
  (`_training_job.py:1396-1403`) checks existence only.
- An actuary who selects raw `exposure` (all-positive, un-logged) gets a silently
  mis-specified model that trains and "works".

---

## Proposed fix (one coherent design, in dependency order)

**Decision to make first:** the prediction contract for offset models. Recommended:
*predictions are expected values including the offset wherever an offset value is available;
the offset column becomes part of the model's input contract.* This is the GLM convention
actuaries already know, and it makes AvE/deviance/scoring all coherent.

1. **Make prediction offset-aware at the algorithm boundary.**
   `BaseAlgorithm.predict(model, df, features, *, baseline: np.ndarray | None = None)`.
   CatBoost implementation: when `baseline` is given, build `Pool(x_data, baseline=baseline)`
   and predict on the Pool. (Raw-score-space addition works for every loss; do NOT
   post-multiply by `exp(offset)` — that is only correct for log-link losses.)
2. **Thread the offset through `_compute_metrics`.** `diag_df` already contains the offset
   column when configured (`_catboost_select_columns` includes it,
   `_training_job.py:1020-1033`). Pass `baseline=diag_df[self.offset].cast(Float64).to_numpy()`
   into the diagnostics predict and the separate validation-metrics predict. PDP and SHAP must
   use the same offset-aware predictions (PDP: hold each row's own offset fixed while varying
   the feature).
3. **Record offset in the feature contract.** Add an optional `offset` block to
   `FeatureContract` (`{column: str, space: "raw_score"}`), bump/branch the contract-hash
   canonical payload carefully (new optional key, absent = legacy contract, keeps old
   contracts loading). Write it in `_save_artifacts`; log it in the MLflow signature params
   and model card.
4. **Score-time enforcement (fail loud).** When a loaded model's contract declares an offset:
   the score node must either (a) have an offset-column mapping configured (new score-node
   config field validated against the input schema; scorer builds the Pool with baseline), or
   (b) the user explicitly opts into "score without offset (per-unit-exposure rate)" — an
   explicit config flag, never a silent default. A contract-declared offset with neither
   configured raises `FeatureMismatchError` naming the offset column. Same rule in the deploy
   bundler/validator so a deployment cannot ship ambiguous offset semantics.
5. **UI guidance + log-space heuristic warning.** In `TargetAndTaskConfig`, extend the offset
   help text ("must be in log space for Poisson/Tweedie, e.g. `ln(exposure)`; add a Polars
   node upstream: `pl.col("exposure").log()`"). At data-prep time, warn (training-job warning
   surface already exists) when the offset column is strictly positive with min ≥ 0 — the
   signature of un-logged exposure. Heuristic, so warn, don't block.

## TDD plan (write these failing tests first)

1. **Balance test (fails today, ratio ≈ 0.60):** train Poisson, target = counts,
   `offset=log(exposure)`, exposure ∈ [0.1, 3]; assert
   `abs(Σy_pred/Σy_true − 1) < 0.05` on the diagnostics set.
2. **Gini consistency:** reported Gini == Gini of offset-included predictions (± 1e-9).
3. **AvE artefact regression:** a feature uncorrelated with target but correlated with
   exposure must show a flat AvE actual/predicted ratio (± tolerance) — fails today.
4. **Round-trip scoring:** train-with-offset → save → load via `_model_scorer` with offset
   mapping → Σpred ≈ Σactual on the training frame.
5. **Contract enforcement:** contract-with-offset + scoring input missing the offset mapping
   raises `FeatureMismatchError`; explicit rate-mode flag scores and labels output as a rate.
6. **Legacy contract:** pre-offset contract JSON still loads (hash check passes), scores as
   before.
7. **Log-space heuristic:** all-positive offset column produces the warning; a column with
   negatives does not.
8. Strengthen `test_offset_column` to pin the new semantics (it currently only checks
   finiteness).

## Acceptance criteria
- All eight tests green; existing 12k-test suite green.
- An offset Poisson model's AvE table balances (~1.00 overall) and deviance matches a
  hand-computed offset-included deviance.
- A deployed offset model either receives an offset input or was explicitly configured as
  rate-output; no third path exists.
