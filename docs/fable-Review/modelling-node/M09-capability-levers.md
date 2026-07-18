# M09 — CatBoost levers a pricing DS needs: invisible, blocked, or unguarded

**Severity: MEDIUM (four findings) + LOW (two findings) + one important POSITIVE**
**Reachability legend: (a) fully blocked · (b) reachable via raw params JSON but invisible in the UI · (c) supported.**

## The positive that frames everything: raw params passthrough works — (b) is real

`config["params"]` flows **verbatim** into `CatBoostRegressor/Classifier(**model_params)`
(`src/haute/modelling/_algorithms.py:493, 530-533`). Verified working through the JSON
editor: `grow_policy`, `bootstrap_type`, `subsample`, `rsm`, `border_count`, `od_type`,
`od_wait`, `random_seed`, `one_hot_max_size`, `nan_mode`, `thread_count`, `eval_metric`,
`custom_metric`, `auto_class_weights`, `task_type` (GPU also has a checkbox). The JSON editor
(`FeatureAndAlgorithmConfig.tsx:7-13`, defaults `iterations/learning_rate/depth/l2_leaf_reg/
early_stopping_rounds`) is a genuine power-user escape hatch — the gaps below are mostly
**discoverability and validation**, not access. Any fix should preserve this passthrough.

---

## M09-1 (MEDIUM, silent-wrongness risk): monotone-constraint names are silently dropped when they don't match a feature

### Evidence
`_algorithms.py:519-521` — `mc_list = [monotone_constraints.get(f, 0) for f in features]`:
a constraint keyed on a typo'd, excluded, or upstream-**renamed** column vanishes with no
error. The UI lists live columns (mitigation), but config files, the API, and exported
scripts are unguarded — and a rename upstream after configuring the constraint defeats even
the UI.

### DS impact
A regulatory constraint ("premium increases with sum insured") can silently stop applying.
The model ships unconstrained; nothing in the run output says so.

### Fix
In `TrainingJob._train_model` (or `_validate_columns`), require every
`monotone_constraints`/`feature_weights` key ∈ `features`; raise naming the offending keys
and the nearest matches. Also reject constraints on categorical features (CatBoost only
supports numeric).

### TDD
- Constraint on unknown column → error naming it (fails today: silently ignored).
- Constraint on categorical → error. Valid constraint → identical index list as today.

## M09-2 (MEDIUM, (b)): `eval_metric` — early-stop on the metric you care about — has no UI

Verified: `loss_function="Poisson"` + `eval_metric="AUC"` is accepted by CatBoost. The UI
"Metrics" buttons drive only post-hoc `compute_metrics`, not CatBoost's early stopping. A
DS wanting "train Tweedie, stop on validation Gini (NormalizedGini)" must know to hand-type
`eval_metric` into raw JSON.
**Fix:** an "Early-stopping metric" select in the UI (default: same as loss), written into
`params.eval_metric`; document the distinction from the post-hoc metrics row.
**TDD:** setting it changes `best_iteration_` on a crafted dataset; MLflow records it.

## M09-3 (MEDIUM, (b)): class-imbalance controls invisible for classification

`auto_class_weights` / `scale_pos_weight` / `class_weights` appear nowhere in UI or docs
(grep: zero matches outside this review). Retention/conversion targets are routinely 90:10+.
**Fix:** classification-only "Imbalance handling" control (None / Balanced / SqrtBalanced →
`params.auto_class_weights`).
**TDD:** UI writes the param; a skewed synthetic target shows changed class weighting in fit.

## M09-4 (MEDIUM, (a) — genuinely blocked): warm start / continued training impossible

`init_model` is a **fit()-only** CatBoost argument, but `_algorithms.fit` passes only
`callbacks`/`eval_set` to `model.fit` (`_algorithms.py:538-544`); putting `init_model` in
params sends it to the **constructor**, which rejects it. Snapshot/resume
(`save_snapshot`/`snapshot_file`) is likewise unusable because `allow_writing_files=False`
is forced by default (`:499-500`).
**DS impact:** no periodic-refresh workflow (continue last quarter's model on new data), no
resume of a long GPU fit after cancellation.
**Fix:** optional `init_model` config (path to a prior `.cbm`, validated to exist and match
the feature contract) threaded into `fit_kwargs`. Snapshotting is a separate, lower-value
follow-up.
**TDD:** fit with init_model → `tree_count_ = prior + new`; contract mismatch between prior
model and current features → loud error.

## M09-5 (LOW, (a)): `feature_weights` is plumbed but unreachable

Top-level config key consumed by fit (`_algorithms.py:524-526`) and exported by `_export.py`,
but no UI writes it (grep across `frontend/src` → nothing) and it isn't in the params JSON
(different config level). Effectively hand-edit-the-.py-file only.
**Fix:** either a small per-feature weight editor next to the monotonic panel, or document
the config key in the modelling docs. Validate names like M09-1.

## M09-6 (LOW, (a)): text and embedding features unsupported

`_build_pool` derives `cat_features` purely from string dtype (`_algorithms.py:276-315`);
there is no `text_features`/`embedding_features` path, so free-text columns (occupation,
vehicle description) become high-cardinality categoricals rather than using CatBoost's text
processing. Acceptable for structured pricing data; note it in docs, implement only on
demand.

## M09-7 (context): `use_best_model` semantics are correct today; record it anyway

Verified: with an eval set, CatBoost defaults `use_best_model=True` and the saved `.cbm` is
shrunk to `best_iteration+1` trees (persists through save/load) — so `TrainResult
.best_iteration` agrees with the artifact. One caveat: a raw-params `use_best_model: false`
keeps all trees while `best_iteration_` still reports the best index; log the effective
`use_best_model` alongside M08-1's params so the run record is unambiguous.

## Acceptance criteria
- Misnamed monotone/feature-weight keys can no longer vanish silently.
- Early-stopping metric and imbalance handling are first-class UI controls backed by params.
- `init_model` warm start works with contract validation, or is explicitly documented as
  unsupported.
