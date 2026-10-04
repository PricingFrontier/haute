# t-boost improvements for the Haute integration

A handover specification for the t-boost project, written on 4 October 2026 against
t-boost 0.6.2. It lists the library changes that would let Haute train, validate and
explain t-boost models on exactly the same terms as its other gradient-boosting
families (XGBoost and LightGBM), and remove the workarounds Haute's adapter carries
today. It is a supporting report: it owns no Haute work. The status of each
requirement at t-boost 0.7.0, and how Haute uses it, is recorded below.

Each requirement states the behaviour observed in 0.6.2, the proposed API, its exact
semantics, the tests t-boost should ship with it, and what it unlocks in Haute.
Names are proposals; the semantics are what matter.

## Status at t-boost 0.8.0

t-boost 0.7.0 (4 October 2026) shipped R1–R3 and R5–R12, and 0.8.0 shipped R13, with the
differences below. Most follow from one decision: early stopping fits the model, so nothing
carries a round count or a table set into a refit. Haute requires `t-boost>=0.8.0,<0.9`.

| ID | In t-boost | Haute |
|---|---|---|
| R1 | `eval_set` with `eval_sample_weight`, `eval_exposure` and `eval_offset`. Each bag stops at its own best round on the evaluation rows; ensemble-level stopping was declined (bags fit independently, and truncating after the fit is not exact). `eval_set` overrides `validation_fraction` (`binding_report_` records it as inert) rather than raising, and the evaluation rows always drive early stopping. The holdout slope recalibration does not run with `eval_set`; multiclass rejects it. | A fit with validation rows passes them as `eval_set`, and the early-stopped fit is the model. |
| R2 | `n_trees_per_bag_`, `stopping_reason_per_bag_`, `n_trees_` (the largest bag) and `stopping_reason_` (one summary); no `best_iteration_`, which only served a refit. | Fit evidence records `n_trees_` and the mapped stopping reason. |
| R3 | `callbacks` called with a dict per bag and round on the calling thread (bags interleave); per-bag `evals_result_`, the train curve only when callbacks are given. Pruning folds do not call back. | Progress and cancellation from the callback; the loss curve is the per-round bag mean, a progress curve rather than the deployed ensemble's deviance. |
| R4 | Withdrawn: single-split drop-gains have no standard error and were measured to drop tables on noise, and `keep_tables` only served a refit. | t-boost's own pruning CV runs inside each fit. |
| R5 | A `metadata` property set after fitting, kept by both formats; no `fit(..., metadata=...)`. | The Haute record lives in it. |
| R6 | `offset=` on fit and scoring, optional at scoring (zero when omitted); exact under `squared_error`; under the log and logit links it joins the exposure and scales `support`; multiclass rejects it. | `RMSE` offsets; Haute always adds the offset itself when scoring. |
| R7 | `unknown_category` and `categories_`. | Haute sets `unknown_category="rare"` (R13) and never refuses an unseen value. |
| R8 | Only `squared_error` refuses `exposure`; logistic keeps it as a rare-event logit offset. | Haute refuses classification offsets itself. |
| R9 | `return_format="matrix"` (`ContributionMatrix`) with tuple terms; the records and dataframe formats keep rustystats' shape. | Uses the matrix; feature names may contain `:`. |
| R10 | `cell_indices(X)`, a dict keyed by each table's feature-name tuple (regression and binary; factored effects have no cells). | Not yet used; `TBOOST-02` and `TBOOST-03` in the [t-boost roadmap](t-boost.md) will. |
| R11 | Typed top-level imports; `PrecisionWarning` once per estimator, with no flag to silence it. | Imports from `t_boost`; the warning is silenced inside the adapter. |
| R12 | Envelope `schema_version` 5, refusing newer headers. | A model saved before 0.7.0 is refused with a retrain message. |
| R13 | `unknown_category="rare"`, now the default: an unseen value scores in the pooled `<rare>` cell, or in `default_cell` on an axis that pooled nothing (no synthetic rare level); every categorical axis in `tables()` carries `rare_pooled` and `unseen_cell`. `unseen_values()` returns a frame of `feature`, `value`, `rows` (and `mass` given a weight or exposure). Models saved by 0.7.0 keep their policy. | Haute sets `"rare"`, logs `unseen_values` for the validation rows, and will report them in results (`TBOOST-09`). |

No request is open. The requirements below are kept as written, as the record of what was
asked.

## How Haute uses t-boost

Haute trains every model through one evaluation plan. Source rows are split into
development rows and an optional **final-test** partition that no fit or selection ever
sees. Development rows are split again into a **training** partition and a
**validation** partition (or cross-validation folds). A **selection fit** trains on the
training partition and is evaluated, and early-stopped, on the validation partition;
tuning compares candidates on that validation evidence. The **final refit** then trains
on all development rows with the selected settings and, for a boosted family, the
round count the selection fits chose (the validation-weighted mean across folds). The
final-test partition is scored once at the end.

XGBoost and LightGBM fit this directly: they accept a validation set, early-stop on it,
report the best iteration, and refit with that iteration count and early stopping off.

t-boost 0.6.2 has no validation-set argument. Each fit carves its own early-stopping
holdout from the rows it is given (`validation_fraction`, per bag), runs internal K-fold
CV for table pruning (`prune_n_folds`), and the deployed tables-only model records no
round count. Haute therefore integrates it like an EBM: it never passes its validation
partition, lets every fit early-stop internally, and refits with the same parameters
(`n_trees` reused only as a ceiling). The result is correct and leak-free, but:

- the validation partition only scores the selection fit; it never drives stopping;
- each refit early-stops again on a fresh internal carve, so the selected model and the
  deployed model can stop at different points;
- tuning has no final round count, and fit evidence reports no fitted rounds and no
  stopping reason;
- there is no loss curve and no progress during a fit.

Haute's adapter also works around several smaller behaviours, listed as R4 to R12 below.

## Summary

| ID | Requirement | Priority | Unlocks in Haute |
|---|---|---|---|
| R1 | External validation set with ensemble-level early stopping | P1 | Validation-driven stopping and refit, as for XGBoost and LightGBM |
| R2 | Fitted round count, best iteration and stopping reason | P1 | Refit round count, tuning tree count, fit evidence |
| R3 | Per-round metrics history and an iteration callback | P2 | Live loss curve, training progress, cancellation |
| R4 | Pruning against the validation set, and a fixed keep-set for refits | P2 | Pruning decided on Haute's validation rows; refit keeps the selected tables |
| R5 | An official metadata slot in the saved model | P2 | Self-describing `.tboost` files without relying on unknown-key tolerance |
| R6 | Arbitrary link-scale offset (`init_score`) | P2 | Offsets under RMSE and Logloss |
| R7 | Unknown-category policy | P2 | Haute stops pre-checking categorical levels |
| R8 | Refuse `exposure` under `squared_error` | P3 | One less silent case |
| R9 | Contributions keyed by feature tuples, as a matrix | P2 | Faster explanations; feature names may contain `:` |
| R10 | Public cell indices and documented border semantics | P2 | Cell-level validation and unfolding into rating tables |
| R11 | Typed top-level imports and a quieter precision warning | P3 | Cleaner adapter |
| R12 | A documented saved-model compatibility rule | P3 | An exact retrain rule in Haute |
| R13 | Unseen categories score as the rare level, and unseen values are countable | P1 | New levels priced like rare ones, and reported |

## Requirements

### R1. External validation set with ensemble-level early stopping

**Observed in 0.6.2.** `TBoostRegressor.fit` and `TBoostClassifier.fit` take `X, y,
sample_weight, exposure, groups`. Early stopping always uses an internal carve of the
fit rows (`validation_fraction`, default 0.1), drawn per bag; there is no way to supply
the rows to stop on.

**Proposal.**

```python
model.fit(
    X, y, sample_weight=w, exposure=e, groups=g,
    eval_set=(X_val, y_val),
    eval_sample_weight=w_val,     # optional
    eval_exposure=e_val,          # optional; required when exposure is given
)
```

`X_val` is matched to the fit features by name, exactly as `predict` matches a polars
frame. A string column name for `y`, weights or exposure is accepted as it is for `X`.

**Semantics.**

- With `eval_set`, early stopping uses only the evaluation rows: no internal holdout is
  carved, every fit row trains, and `validation_fraction` must be unset. Setting both
  raises.
- **Ensemble-level stopping.** With `n_bags > 1`, all bags advance one round at a time
  together, and the stopping metric is the deviance on the evaluation rows of the
  bagged ensemble as it would be deployed (the bag average). One best round is chosen
  for the whole ensemble and every bag is truncated to it. Per-bag stopping would give
  `n_bags` different rounds, and Haute needs one number to refit with; the ensemble is
  also what is deployed.
- `early_stopping_rounds`, `early_stopping_adaptive` and `early_stopping_min_delta` keep
  their meaning, applied to the evaluation deviance.
- The evaluation rows never enter a bag, a pruning fold or the cell refit, and never
  influence purification or the reference measure.
- Without `eval_set`, behaviour is unchanged.
- With `eval_set` and early stopping disabled (`early_stopping_rounds=0` or equivalent),
  the fit trains `n_trees` rounds and still reports the evaluation history (R3).
- A refit with a fixed round count: `fit(..., n_trees=k)` with early stopping disabled
  trains exactly `k` rounds per bag and is deterministic for a seed.

**Tests t-boost should ship.**

- A fit with `eval_set` stops at the round minimising evaluation deviance (an
  independent re-scoring of the ensemble at each round confirms it).
- Every bag is truncated to the same round.
- Evaluation rows never reach training: moving one evaluation row's target to an
  extreme value changes only the stopping round, never the fitted cell values at a
  fixed round count.
- Refitting on the same rows with `n_trees = best_iteration_ + 1` and early stopping off
  reproduces the early-stopped model's predictions (before pruning, or with the same
  keep-set, R4).
- `eval_set` with `validation_fraction` raises.
- Exposure on the fit rows without `eval_exposure` raises.

**Unlocks in Haute.** The t-boost descriptor moves from the `fixed_budget` refit policy
to `validation_weighted_rounds`: selection fits early-stop on Haute's validation
partition, the final refit uses the validation-weighted round count with early stopping
off, and tuning reports a final tree count, exactly as for XGBoost and LightGBM.

### R2. Fitted round count, best iteration and stopping reason

**Observed in 0.6.2.** The fitted estimator exposes `pruning_report_`,
`graduation_report_`, `binding_report_` and `feature_importances_`, but no round count.
The deployed model is a tables-only model with no `n_trees()`.

**Proposal.** Fitted attributes, kept through `to_json`/`from_json` and
`to_bytes`/`from_bytes`, so a loaded model reports them too:

| Attribute | Meaning |
|---|---|
| `best_iteration_` | The zero-based round early stopping selected (ensemble level), or `None` when no early stopping ran |
| `n_trees_` | Boosting rounds per bag in the deployed model |
| `n_trees_per_bag_` | Per-bag counts, for any configuration where bags may still differ (without `eval_set`) |
| `stopping_reason_` | `"early_stopping"`, `"max_trees"` (reached `n_trees`) or `"no_split"` (no split met the constraints before `n_trees`) |

**Semantics.** With `eval_set`, `n_trees_ == best_iteration_ + 1` for every bag. Without
it, `best_iteration_` is `None` and `n_trees_per_bag_` lists each bag's own stopping
round. Pruning does not change these counts: they describe the boosting that produced
the tables.

**Tests t-boost should ship.** Each stopping reason is produced by a constructed fit; the
attributes survive both serialisation formats; a model fitted without early stopping
reports `max_trees` and `n_trees_ == n_trees`.

**Unlocks in Haute.** Fit evidence records the configured ceiling, the fitted rounds and
the stopping reason (`none`, `validation` or `native_exhaustion` in Haute's terms) for
t-boost, as it already does for every other boosted family.

### R3. Per-round metrics history and an iteration callback

**Observed in 0.6.2.** No per-round output. Haute reports progress only before and
after a fit, and draws no loss curve.

**Proposal.**

```python
model.fit(..., callbacks=[on_round])

def on_round(round: int, total: int, metrics: dict[str, float]) -> bool | None:
    ...  # return True to stop the fit early, as a user cancellation
```

and a fitted `evals_result_`:

```python
{"train": {"deviance": [...]}, "eval": {"deviance": [...]}}   # one value per round
```

**Semantics.** `metrics` holds the ensemble's training deviance and, with `eval_set`, its
evaluation deviance after that round, in the objective's own deviance (weighted by
`sample_weight` times exposure as the fit is). The callback runs on the calling thread
between rounds, so it may raise; an exception aborts the fit and propagates unchanged.
Returning `True` stops after the current round and records `stopping_reason_ =
"callback"`. Callbacks during internal pruning-fold fits are not required; if they run,
the round numbers must not be confused with the main fit's (a `phase` key in `metrics`
would do).

**Tests t-boost should ship.** The callback sees rounds `1..n_trees_` in order, the last
reported evaluation deviance equals an independent scoring of the returned model, and a
callback returning `True` at round `k` leaves `n_trees_ == k`.

**Unlocks in Haute.** The live loss chart during training, accurate progress for long
fits, and prompt cancellation of a training job.

### R4. Pruning against the validation set, and a fixed keep-set for refits

**Observed in 0.6.2.** With `prune=True` the single-output path selects tables by
internal K-fold CV over the fit rows (`prune_n_folds`, default 5), refitting a model per
fold. On a refit Haute cannot ask for the tables the selection fit kept.

**Proposal.**

- `prune_on="cv"` (the current behaviour, default) or `prune_on="eval_set"`, which
  scores each candidate table's drop-gain on the `eval_set` rows of the one fitted
  model, without fold refits.
- `fit(..., keep_tables=[("age",), ("age", "veh"), ...])`: prune to exactly these
  effects (by feature-name sets; heredity closure applied) instead of selecting.
- A fitted `kept_tables_` listing the deployed effects in the same form.

**Semantics.** `prune_on="eval_set"` requires `eval_set`. `keep_tables` and selection are
mutually exclusive. An effect named in `keep_tables` that the refit never grew is
reported (in `pruning_report_`) rather than silently ignored; whether that warns or
raises is the library's choice, but it must be visible.

**Tests t-boost should ship.** `prune_on="eval_set"` never fits on evaluation rows; a
refit with `keep_tables=selection.kept_tables_` deploys exactly that set when the refit
grew every effect; a missing effect appears in the report.

**Unlocks in Haute.** Pruning decided on Haute's validation partition, a cheaper
selection fit, and a final refit whose tables are the ones validated.

### R5. An official metadata slot in the saved model

**Observed in 0.6.2.** Haute's `.tboost` file is `to_json()` with an added top-level
`"haute"` key. This works only because `from_json` ignores unknown keys, which is not a
documented guarantee. `to_bytes()` cannot carry anything extra: `from_bytes` refuses
trailing bytes.

**Proposal.** `model.metadata: dict[str, JSON]`, set by the caller after fitting (or
through `fit(..., metadata=...)`), stored by both serialisation formats and returned
unchanged by `from_json`/`from_bytes`. t-boost never reads it.

**Semantics.** Values must be JSON-serialisable; anything else raises at save time. The
slot does not affect predictions, `tables()`, or equality of fitted models.

**Tests t-boost should ship.** Round trip through both formats; a non-JSON value raises;
a model saved with metadata predicts identically to one saved without.

**Unlocks in Haute.** The Haute record (feature order, categorical levels, task, link,
offset, class labels) moves into the documented slot, and Haute can choose the compact
binary format.

### R6. Arbitrary link-scale offset

**Observed in 0.6.2.** `exposure` is the only offset: it enters as `log(exposure)` and is
meaningful only under the log-link objectives. Haute therefore refuses an offset for a
t-boost model under RMSE or Logloss, which its other families accept.

**Proposal.** `fit(..., init_score=s)` and the same argument on `predict`,
`predict_raw`, `predict_proba`, `decision_function` and `predict_contributions`: a
link-scale baseline added to every row's raw score, under any objective. `exposure`
keeps its meaning; supplying both adds both.

**Semantics.** The baseline is never learned or purified into the tables;
`predict_contributions` reports it as its own term (or in `base_value`, documented
either way), so `base_value + sum(contributions)` still equals the raw score including
the baseline. A model fitted with `init_score` must be scored with one; omitting it
raises rather than scoring on a zero baseline.

**Tests t-boost should ship.** A constant `init_score` equals a shifted intercept; a
varying one is recovered exactly in raw predictions; omitting it at predict time raises.

**Unlocks in Haute.** Offsets under every loss, consistent with XGBoost (`base_margin`)
and LightGBM (`init_score`).

### R7. Unknown-category policy

**Observed in 0.6.2.** A categorical value not seen in training is scored in the axis's
`default_cell` without any signal. Haute refuses such values before calling t-boost, so
it must keep its own copy of every fitted level list.

**Proposal.** A constructor parameter `unknown_category="default_cell" | "error"`
(default unchanged), and a public fitted `categories_` mapping each categorical feature
to its fitted levels, including the members pooled into a rare level and the missing
level.

**Semantics.** With `"error"`, any scoring call (`predict*`, `predict_contributions`,
`tables` on new rows) raises a `ValueError` naming the feature and up to a few of the
unseen values. Nulls are never unknown: they score in the missing level.

**Tests t-boost should ship.** Each scoring entry point raises under `"error"` and
scores the default cell under `"default_cell"`; a null never raises.

**Unlocks in Haute.** Haute sets `"error"` and drops its own level check.

### R8. Refuse `exposure` under `squared_error`

**Observed in 0.6.2.** `TBoostRegressor(objective="squared_error").fit(..., exposure=e)`
fits without complaint, although an exposure offset has no meaning under the identity
link.

**Proposal.** Raise a `ValueError` for `exposure` with `squared_error` and the logistic
objective, naming the objectives that accept it. (With R6, `init_score` is the offset
for those objectives.)

**Unlocks in Haute.** Consistency; Haute already refuses the combination itself.

### R9. Contributions keyed by feature tuples, as a matrix

**Observed in 0.6.2.** `predict_contributions` returns records or a long polars frame
keyed by a term string that joins feature names with `:` (`"age:veh"`). Haute rebuilds a
`rows × terms` matrix from the long frame and splits names on `:`, so it refuses feature
names containing `:`. A model with no tables returns one `intercept` placeholder row per
input row with `term_type == "intercept"`, which callers must filter out. The fast exact
path (`_TableModel.effect_contributions`) is private.

**Proposal.** `predict_contributions(X, return_format="matrix", split_interactions=...)`
returning a small result object:

```python
result.base_value   # float64 array, one per row (link scale)
result.values       # float64 array, rows x terms
result.terms        # list[tuple[str, ...]]: each term's feature names
```

and `term` keys in every format given as the tuple of feature names (the joined string
may remain as a display label).

**Semantics.** `base_value + values.sum(axis=1)` equals `predict_raw` (plus any offset,
R6); an intercept-only model returns zero columns, never a placeholder term; with
`split_interactions=True` the columns are exactly the fitted features in
`feature_names_in_` order, a feature no table uses being a zero column.

**Tests t-boost should ship.** Exactness against `predict_raw`; a feature named `a:b`
round-trips; zero columns for an intercept-only model; column order under
`split_interactions=True`.

**Unlocks in Haute.** Explanations and SHAP views without reshaping, and no restriction
on feature names.

### R10. Public cell indices and documented border semantics

**Observed in 0.6.2.** `tables()` exports each axis's `borders` and categorical
`levels`. Probes show that a value equal to a border scores in the lower cell (cells are
right-closed, `(b[i-1], b[i]]`), that comparisons happen after conversion to float32,
that cell 0 of a numeric axis is the missing cell, and that a categorical axis may have
cells holding no level. None of this is documented. The per-row cell lookup
(`_TableModel.cell_indices`) is private.

**Proposal.**

- A public `cell_indices(X)` returning, for each deployed table, the cell index of every
  row on every axis (or the flat cell index), in the same order as `tables()`.
- Documentation, in the `tables()` docstring and the export schema, of: border closure,
  the float32 comparison, the missing cell, empty categorical cells, the
  `default_cell`, and the row-major order of `values` and `support`.
- Borders exported exactly as the float32 values compared against (they are today; this
  makes it a guarantee).

**Tests t-boost should ship.** For random rows, the table value at `cell_indices` equals
that row's contribution for every table; a value equal to a border lands in the lower
cell; the float32 rounding case (a float64 value just above a border that rounds onto
it) lands in the lower cell.

**Unlocks in Haute.** Cell-level actual-versus-expected on held-out rows (`TBOOST-02`)
and exact unfolding of a model into Haute's Banding and Rating Step nodes
(`TBOOST-03`), both of which need the model's own cell for every row.

### R11. Typed top-level imports and a quieter precision warning

**Observed in 0.6.2.** `t_boost.TBoostRegressor`, `TBoostClassifier` and
`PrecisionWarning` resolve through a module `__getattr__`, so type checkers see them as
`object`; Haute imports them from `t_boost.sklearn` instead. `PrecisionWarning` ("t-boost
converts input features to float32") is emitted on every fit and every scoring call.

**Proposal.** Import the names under `if TYPE_CHECKING:` in `t_boost/__init__.py` (or
eagerly), and emit `PrecisionWarning` once per estimator instance, or offer a
constructor flag to silence it.

**Unlocks in Haute.** Haute drops its warning filter around every call.

### R12. A documented saved-model compatibility rule

**Observed in 0.6.2.** A saved JSON document carries `schema_version`,
`t_boost_version` and a nested model `schema_version`. A document whose
`t_boost_version` was edited to `0.5.0` still loads, so the version field is
informational, and which schema versions a release can read is not stated.

**Proposal.** Document the rule: which `schema_version` values each release reads, that
any readable document predicts identically under the reading release, and that an
unreadable one raises `SerializationError` naming both versions.

**Unlocks in Haute.** Haute states its own rule exactly ("a `.tboost` file loads under
any t-boost that reads its schema") and tests it.

### R13. Unseen categories score as the rare level

**Observed in 0.7.0.** `unknown_category="default_cell"` scores a categorical value the
fit never saw in the axis's `default_cell`, documented as "the encoder's base level";
`"error"` refuses it. Haute must not refuse: a high-cardinality feature (a vehicle make)
routinely has levels that only the validation rows hold under a random split, and new
levels arrive in production. But the base level is the wrong price for them. A make the
book has never seen most resembles the makes it has barely seen, and t-boost already
pools those into the `<rare>` level, whose relativity is learned from data.

**Proposal.** `unknown_category="rare"`: an unseen value scores in the axis's rare cell,
the cell of the pooled `<rare>` level. Recommended as the default.

**Semantics.**

- Every categorical axis of every table has a rare cell, in main effects and in
  interactions alike, so an unseen value is placed consistently in every table that uses
  the feature.
- When the fit pooled no level (every level had enough data), the rare cell still exists.
  Its value must be defined and documented; scoring it as `default_cell` is acceptable,
  and `tables()` should mark that cell (`synthetic: true`) so a reader knows no level was
  pooled.
- `predict*`, `predict_contributions`, `tables`, `cell_indices` and
  `actual_vs_expected` all place an unseen value in that same cell; `tables()` lists the
  rare cell's members as the pooled training levels.
- A null is never unknown: it stays in the missing level.
- `unseen_values(X)` returns, per categorical feature, each value outside the fitted
  levels with its row count (and, given `sample_weight`/`exposure`, its mass), so callers
  report coverage without re-deriving the levels.

**Tests t-boost should ship.** An unseen value scores exactly as a level pooled into
`<rare>` does, in a main effect and in an interaction; with no pooled level it scores as
the documented fallback; every scoring entry point agrees; a null scores in the missing
level; `unseen_values` counts match a direct count.

**Unlocks in Haute.** Haute sets `unknown_category="rare"`, so a new make is priced as a
rare make in validation, final test and production, and reports the unseen counts from
`unseen_values` in the training result and in Model Scoring (`TBOOST-09` in the
[t-boost roadmap](t-boost.md)).

## Behaviour Haute relies on today

These hold in 0.6.2, Haute's integration depends on them, and they should be kept
(and ideally tested in t-boost):

- A fit is identical across `n_jobs` values for a fixed seed.
- `to_json`/`from_json` and `to_bytes`/`from_bytes` round-trip predictions exactly, and
  loading a classifier document as a regressor raises `SerializationError`.
- `tables(X)` on the fit rows reproduces every row's contribution, including cells with
  no training support, whose `support` is 0 and whose `values` are the scored values.
- `support` is the training mass per cell: the sum of `sample_weight` times `exposure`.
- `groups` keeps every internal carve and bag group-honest.
- String, Categorical and Enum columns are encoded natively; a null categorical value
  scores in the missing level.

## Not requested

A multiclass path (Haute trains binary classifiers only), GPU training, and changes to
the fitting algorithm, pruning heuristics or defaults are out of scope. Every
requirement above is opt-in or a stricter error; none should change the predictions of
an existing fit.
