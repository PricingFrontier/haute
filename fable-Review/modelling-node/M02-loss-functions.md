# M02 — The loss layer: Tweedie slider crashes at its labelled endpoints, silent RMSE default, no Gamma story

**Severity: HIGH (three findings) + MEDIUM (one finding)**
**Status: verified against installed catboost 1.2.10 by direct probe.**

For an insurance pricing tool, the loss function layer is the heart of the modelling node.
Today it is the weakest part of an otherwise strong training path.

## Probe ground truth (catboost 1.2.10)

```
Gamma                        -> "Gamma loss is not supported"
Tweedie:variance_power=1.0   -> "Tweedie metric is defined for 1 < variance_power < 2, got 1"
Tweedie:variance_power=2.0   -> "... got 2"
Tweedie:variance_power=1.99  -> OK
Quantile:alpha=0.9 / MAPE / Huber:delta=1.0 / Expectile / LogCosh -> OK
RMSEWithUncertainty          -> OK but predict() returns shape (n, 2)
eval_metric can differ from loss (Poisson loss + AUC eval_metric accepted)
```

---

## Finding M02-1 (HIGH): Tweedie variance-power slider endpoints crash training — and the label advertises them

### Evidence
- `frontend/src/panels/modelling/TargetAndTaskConfig.tsx:121-133`:
  `<input type="range" min={1.0} max={2.0} step={0.05}>` labelled
  **"Variance power (1.0=Poisson, 2.0=Gamma)"**.
- `resolve_loss_function` (`src/haute/modelling/_algorithms.py:219-241`) interpolates the
  value verbatim into `Tweedie:variance_power=X` with **no bounds validation**; neither does
  `_train_config.py` nor `TrainingJob`.
- CatBoost requires the **open** interval (1, 2); both endpoints raise `CatBoostError`.

### DS impact
The two values the label explicitly names are the two values that crash. The failure fires
*mid-fit, after the pipeline has already executed* (potentially minutes on big data), with a
raw C++-flavoured `CatBoostError` rather than a config error.

### Fix
1. Backend first (guards UI, API, exported scripts alike): `resolve_loss_function` raises
   `ValueError("Tweedie variance_power must be strictly between 1 and 2 (got 2.0). Use 1.99
   for Gamma-like severity, or the GLM algorithm for a true Gamma.")` — before CatBoost sees it.
   `TrainService._validate_config` should surface the same check pre-pipeline so the user
   fails in <1s, not after the pipeline sink.
2. UI: slider `min={1.05} max={1.95} step={0.05}` (or allow a numeric input clamped to
   [1.01, 1.99]); relabel "≈1 frequency-like … ≈2 severity-like".

### TDD
- `resolve_loss_function("Tweedie", "regression", 1.0)` / `(…, 2.0)` raise with the helpful
  message; `1.99`, `1.5` pass (failing test today: currently returns the string and defers the
  crash to CatBoost).
- Route test: train request with vp=2.0 → HTTP 400 before pipeline execution.
- Frontend test: slider cannot emit 1.0/2.0.

---

## Finding M02-2 (HIGH): regression loss silently defaults to RMSE

### Evidence
- Loss buttons start unselected (`TargetAndTaskConfig.tsx:106-119`); clicking a selected loss
  even deselects it (`onUpdate("loss_function", selected ? null : l)`).
- `resolve_loss_function` returns `None` for a falsy loss (`_algorithms.py:227-229`) →
  `fit_params` has no `loss_function` → CatBoostRegressor defaults to **RMSE**.
- Nothing records the *effective* loss: not the UI, not TrainResult, not MLflow params
  (`_training_job.py:1562-1581` logs `param_*` from `self.params` — no resolved loss).

### DS impact
The classic pricing footgun: claim counts or incurred cost trained under squared error.
The run "succeeds", Gini looks plausible, and nothing anywhere says "RMSE".

### Fix
- Record the effective loss everywhere: put the resolved loss (explicit or CatBoost default)
  into `TrainResult`, the summary tab, and MLflow params.
- Config-validation warning (not a block) when `task="regression"`, loss unset, and the
  target is integer-dtype or non-negative-skewed: "No loss selected — defaulting to RMSE.
  For claim counts choose Poisson; for cost with mass at zero choose Tweedie."
- UI: render the default state as an explicit selected "RMSE (default)" chip instead of
  nothing-selected, so the choice is visible.

### TDD
- TrainResult/MLflow expose `effective_loss == "RMSE"` when unset (failing today: absent).
- Warning emitted for integer regression target + no loss; absent when Poisson chosen.

---

## Finding M02-3 (HIGH): no Gamma severity objective, and no documented recipe

### Evidence
- CatBoost has **no Gamma loss** (probe) and Tweedie cannot express p=2 (open interval).
- Haute's allowlist `REGRESSION_LOSSES = {"RMSE","MAE","Poisson","Tweedie"}`
  (`_algorithms.py:215`).
- Mitigating fact: the GLM path supports `family="gamma"`
  (`src/haute/routes/_train_service.py:118-125`) — a true Gamma severity model exists, just
  not gradient-boosted.

### DS impact
Severity is half of frequency×severity pricing. A DS looking for "Gamma" finds nothing, and
the Tweedie slider label ("2.0=Gamma") points at a crash (M02-1).

### Fix (documentation + one hint; no engine change required)
- UI hint next to Tweedie: "Gamma-like severity: variance power ≈ 1.9–1.99. For a true Gamma
  model use the GLM algorithm."
- Modelling docs section "Frequency / severity / burning-cost recipes": Poisson + log-exposure
  offset for frequency; Tweedie vp≈1.9 (or GLM-Gamma) for severity; Tweedie vp≈1.5 + exposure
  weight for burning cost.

---

## Finding M02-4 (MEDIUM): the loss allowlist blocks useful, safe losses — extend it deliberately

### Evidence
- Quantile / MAPE / Huber / Expectile / LogCosh all fit and predict shape `(n,)` on 1.2.10
  (probe). All are blocked by `resolve_loss_function`'s allowlist — reachable only by NOT
  selecting a loss in the UI and typing `loss_function` into the raw params JSON (which
  bypasses `resolve_loss_function` entirely, since that only runs on the top-level
  `loss_function` config key; params flow verbatim to the constructor).
- `RMSEWithUncertainty` predicts shape `(n, 2)` — it would break
  `CatBoostAlgorithm.predict`'s `.flatten()` (`_algorithms.py:613`) by silently returning
  2n interleaved values. The allowlist is *right* to exclude it; this is why extension must be
  deliberate, with a shape guard.

### DS impact
Quantile loss matters in pricing (large-loss loadings, capped severity, P90 cost). Today the
only route is the raw-params side door, which also skips Haute's task/loss validation.

### Fix
- Add `Quantile` (with an `alpha` sub-parameter UI, à la Tweedie's variance power), `MAPE`,
  `Huber` to `REGRESSION_LOSSES` and `resolve_loss_function` (parametrised losses render as
  `Name:param=value`).
- Add a predict-shape guard in `CatBoostAlgorithm.predict`: raise if `preds.ndim > 1 and
  preds.shape[1] > 1` for regression ("multi-output losses such as RMSEWithUncertainty are
  not supported"), so raw-params users fail loud instead of getting interleaved garbage.
- Optional: warn when params JSON contains `loss_function` (it bypasses task validation and
  the UI display; either forbid it or reconcile it into the config field).

### TDD
- `resolve_loss_function("Quantile", "regression", quantile_alpha=0.9)` →
  `"Quantile:alpha=0.9"`; task mismatch still raises.
- Regression predict on an RMSEWithUncertainty model raises the shape error (failing today:
  silently returns 2n values).
- Params-JSON `loss_function` triggers the reconcile/warn path.

## Acceptance criteria
- Invalid variance powers fail in <1s with an actionable message on every path (UI, API,
  export).
- Every completed run displays and logs its effective loss.
- Quantile is a first-class loss with its alpha exposed.
- Docs contain the frequency/severity/burning-cost recipes.
