# M03 — Evaluation & diagnostics correctness (beyond the offset)

**Severity: MEDIUM (one) + LOW–MEDIUM (one) + LOW (three)**
**Status: all verified by direct probe / worked examples. The big evaluation defect (offset) is M01 — this doc is the rest.**

What is already *right* here (verified, keep it): tie-corrected Gini equals `2·AUC−1` exactly
for binary targets; Gini and the plotted Lorenz curve share one aggregation
(`_aggregated_lorenz_points`) so they can never disagree and are row-order invariant;
Poisson/Tweedie deviance match sklearn's weighted semantics to 1e-6; non-finite rows are
filtered *and surfaced* (`non_finite_rows_filtered` key) with fail-loud on all-non-finite;
Poisson/Tweedie `predict()` is in target space (Exponent), the correct space for deviance/AvE.

---

## Finding M03-1 (MEDIUM): `compute_double_lift` is unweighted, row-count-binned, and row-order-dependent

### Evidence — `src/haute/modelling/_metrics.py:368-417`
```python
order = np.argsort(y_pred)                    # ties broken by row position
bins = np.array_split(np.arange(n), n_bins)   # equal ROW COUNT, not equal weight
```
Contrast with the Gini/Lorenz path, which was deliberately rebuilt (CODE_REVIEW C6) to be
tie-corrected and weight-aware via `np.lexsort` (`_metrics.py:148-186`). Double-lift is the
odd one out.

### DS impact
- **Row-order dependence:** identical data in a different row order produces different
  deciles whenever predictions tie (common with heavily-binned factors and shallow trees).
  This is exactly the reproducibility defect C6 fixed for Gini, still live here.
- **Equal-row-count deciles on exposure data distort the lift chart:** insurance decile
  analysis is conventionally *exposure-weighted* so every bucket carries equal exposure. With
  row-count buckets, a few high-exposure policies dominate a decile's actual.

### Fix
Bin by cumulative weight (weighted quantiles of `y_pred`) and use the canonical tie-break
(`np.lexsort((w, y_true, y_pred))`). Keep the output shape (`decile/actual/predicted/count`),
add `exposure` per bin.

### TDD
- Permutation invariance: `compute_double_lift(shuffled) == compute_double_lift(original)`
  (fails today with tied predictions).
- Equal-exposure buckets: with weights, per-bin `Σw` within ±1 unit of total/10.
- Hand-computed 20-row example pins actual/predicted values.

---

## Finding M03-2 (LOW–MEDIUM): training-time classification predict assumes binary `[:, 1]`; the score path is guarded but training is not

### Evidence
- `src/haute/modelling/_algorithms.py:608-613`:
  `preds = model.predict_proba(x_data)[:, 1]` — no shape guard.
- A ≥3-class target is reachable: `task="classification"` with loss unset →
  `CatBoostClassifier` auto-selects `MultiClass`; verified fit succeeds and
  `predict_proba` is `(n, 3)`; `[:, 1]` silently returns the probability of whichever class
  sorts second.
- The *scoring* path already handles this loudly: `_positive_class_proba_vector`
  (`src/haute/_mlflow_io.py:1119-1160`) raises for `(n, k≥3)`.
- Downstream mitigation: sklearn `roc_auc_score`/`log_loss` reject 3-class `y_true` vs 1-D
  scores, so the run usually dies at the metrics stage — but *after* a full fit, with a
  confusing sklearn error, and SHAP/PDP would already have consumed the bogus `[:, 1]`.
- Related, already tracked by the June audit: training metrics use `predict_proba[:,1]` while
  the scored `prediction` column is the hard class label
  (audit P1-subsystem, `_algorithms.py:608-615` vs `_model_scorer.py:585-593`) — resolve
  that finding's proba-vs-label decision together with this one.

### Fix
Route training-time classification predictions through the same guard the scorer uses
(extract `_positive_class_proba_vector` to a shared home or mirror its logic in
`CatBoostAlgorithm.predict`): binary → `[:, positive_class_index]`; k≥3 → raise
"multiclass classification is not supported by the modelling node (target has k classes)".
Better: validate target cardinality at `_prepare_data` (cheap `n_unique` on the target
column) so the user fails before the fit, not after.

### TDD
- 3-class target: training fails at data-prep with the cardinality message (failing today:
  fit completes, dies later in sklearn).
- Binary target: unchanged (`[:, 1]` on a 2-column proba).
- String binary target ("Y"/"N"): see M03-5.

---

## Finding M03-3 (LOW): Poisson/Tweedie deviance clamps silently mask out-of-domain predictions

### Evidence — `src/haute/modelling/_metrics.py:326-327, 340-342`
`_poisson_deviance` clamps `y_pred` to ≥1e-10; `_tweedie_deviance` also clamps `y_true`
to ≥0. An RMSE-trained model (negative predictions are normal — probe: min −0.19 on
all-positive targets) evaluated with `poisson_deviance` yields a large-but-plausible finite
number with no signal that the metric was computed outside its domain.

### Fix
Reuse the existing surfacing pattern: when clamping changes any value, add
`"poisson_deviance_clamped_rows": count` alongside the metric (mirror
`non_finite_rows_filtered`) and log a warning. Don't change the values.

### TDD
- Negative predictions → metric dict contains the clamp count key; all-valid inputs → absent.

---

## Finding M03-4 (LOW): PDP evaluates integer features at truncated values but plots the untruncated x

### Evidence — `src/haute/modelling/_metrics.py:916-933`
Numeric grid values are float percentiles; for an integer column,
`pl.lit(float(val)).cast(Int64)` **truncates toward zero** (verified: 42.7 → 42), while the
stored x value is the untruncated 42.7. The curve is horizontally misaligned for integer
predictors (driver age, vehicle age) and near-duplicate grid points collapse to the same
scored value while plotting as distinct x's.

### Fix
For integer-dtype features, round the grid to integers and `np.unique` **before** prediction
so the scored value and the plotted x agree.

### TDD
- Integer feature: every plotted grid `value` is an integer and distinct grid values imply
  distinct scored columns (failing today with a fractional percentile grid).

---

## Finding M03-5 (LOW): string classification targets die with a raw Polars cast error

### Evidence
`_training_job.py:922` / `_algorithms.py:303` cast the target `pl.Float64` →
`InvalidOperationError: conversion from 'str' to 'f64' failed` for a `"Y"/"N"` target.
Loud (good), but nothing tells the DS the actual rule: classification targets must be
numerically encoded 0/1.

### Fix
Pre-check in `_prepare_data`: if `task == "classification"` and the target dtype is
string/categorical, raise
`ValueError("Classification target 'renewed' is text ('Y'/'N'). Encode it 0/1 upstream, e.g.
(pl.col('renewed') == 'Y').cast(pl.Int8)")`. (A full label-encoding feature is a possible
follow-up; the error message is the minimum.)

### TDD
- String target + classification → the friendly error naming the column and the recipe
  (failing today: raw Polars message).

## Acceptance criteria
- Double-lift is permutation-invariant and exposure-weighted.
- No multiclass fit can complete; binary paths are byte-identical.
- Clamped-deviance runs are visibly annotated.
- PDP x-values equal the scored values for integer features.
- Text targets produce an actionable error.
