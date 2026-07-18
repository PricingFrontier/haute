# M04 — Splits, dead `fold_column`, and the missing CV/tuning layer

**Severity: HIGH (one) + MEDIUM (two) + LOW–MEDIUM (one)**
**Two split-mask defects in this area are ALREADY TRACKED by the June 2026 audit (marked below) — include them in this wave rather than re-verifying separately.**

What's already right: random masks are seeded and reproducible (`SplitConfig.seed`, default
42, exposed in the UI); temporal splits fail loud on null dates; group assignment is a
deterministic md5 hash so group membership is stable across runs and machines;
`SplitConfig.__post_init__` validates sizes and required columns.

---

## Finding M04-1 (HIGH): no cross-validation or hyperparameter tuning exists anywhere

### Evidence
- Grep across `src/haute` for `catboost.cv`, `grid_search`, `randomized_search`, `optuna` →
  zero matches. Training is one fit on one split; the only "tuning" loop is a human editing
  the raw params JSON and re-clicking Train (which also overwrites the previous result —
  see M07).

### DS impact
"Train the best model they can" is the request; a single 80/20 fit with hand-edited JSON is
the weakest link in that story. Validation metrics on one split are noisy exactly where
pricing needs confidence (rare perils, small segments), and there is no systematic search.

### Proposed design (incremental, all CatBoost-native)
1. **Phase 1 — k-fold CV as an evaluation option.** `split.strategy: "cv"` with `n_folds`
   (and `stratified` for classification). Implementation: reuse the existing partition-mask
   machinery with fold ids 0..k−1 in `_partition`; loop fits per fold (reusing the existing
   fit path and cancellation checkpoints); report per-fold + mean±std metrics in
   TrainResult/UI/MLflow. Final artifact = refit on all data (standard practice; document it).
2. **Phase 2 — wire `fold_column` (M04-2) as user-defined folds** (accident-year folds,
   region folds) — the group-CV analogue of the existing group split.
3. **Phase 3 — a small opinionated sweep** (optional): grid over
   `depth × learning_rate × l2_leaf_reg` with early stopping per candidate, results table to
   the UI and one MLflow run per candidate. Keep it bounded (e.g. ≤ 24 candidates) — this is
   a laptop tool.

### TDD
- CV masks: every row in exactly one fold; folds within ±1 row (or group-exact); seed-stable.
- Per-fold metrics deterministic under fixed seed; mean/std math pinned on a toy example.
- Cancellation between folds honoured (existing `check_cancelled` contract).

---

## Finding M04-2 (MEDIUM): `fold_column` is dead config

### Evidence
Accepted and typed everywhere — `_types.py:343`, `_train_config.py:133`,
`_training_job.py:317,340`, exported by `_export.py:74-75`, kept through projection
(`_train_service.py:213`), excluded from features (`_training_job.py:1443-1444`) — and then
**never used by anything**. No split strategy, no CV, no CatBoost argument reads it. The UI
cannot even set it; only hand-edited pipeline files can.

### DS impact
A config key that promises fold-aware behaviour and delivers "your column was quietly removed
from the features". Misleading dead surface.

### Fix
Wire it into M04-1 Phase 2 (preferred), or delete the knob from `_types.py`/
`_train_config.py`/`_export.py` and the docstrings. Do not leave it dead.

### TDD
- If wired: rows sharing a `fold_column` value never straddle folds.
- If removed: schema/type tests assert the key is rejected with a clear message.

---

## Finding M04-3 (LOW–MEDIUM): temporal split sizes are ratios, not the fractions the UI implies

### Evidence — `src/haute/modelling/_split.py:265-320`
The train boundary is set **only** by `cutoff_date`. `validation_size` never sizes anything:
post-cutoff rows are all validation unless `holdout_size>0`, in which case
`holdout_frac = holdout/(validation+holdout)` splits post-cutoff **by date order**. So
`validation_size=0.2` can produce a 45% validation set.
**Already tracked (June audit, P3-exhaustive, `_split.py:300-314`):** with
`validation_size == 0` and `holdout_size > 0`, `holdout_frac = 1.0` sweeps **all**
post-cutoff rows into holdout — the user asked for (say) 10% holdout and got everything after
the cutoff.
Also note the UI never shows validation/holdout size inputs for the temporal strategy
(`SplitAndMetricsConfig.tsx:106-131` renders only date column + cutoff), so whatever sizes
were last set for random silently apply as ratios.

### Fix
Define the semantics, then enforce + document: recommended — cutoff separates train from
eval as today; `validation_size`/`holdout_size` act as an explicit **ratio** of post-cutoff
data (validated to sum to 1.0 when both set, defaulting holdout to 0). Surface the actual
partition percentages in the UI estimate ("cutoff 2024-01-01 → 71% train / 23% val / 6%
holdout") so nothing is a surprise. Fix the val=0 sweep case per the audit finding.

### TDD
- val=0 + holdout=0.1 temporal: holdout gets the most-recent share consistent with the
  documented ratio semantics, not 100% of post-cutoff (fails today).
- Partition percentages surfaced in the estimate response.

---

## Finding M04-4 (MEDIUM): group-mask fallback can leave train empty — ALREADY TRACKED

**June audit, P3-exhaustive, `_split.py:351-358`:** when hashing assigns no group to
validation, the fallback promotes "the first TRAIN group" — a no-op if no train group exists
(e.g. tiny validation+holdout fractions with few groups can hash everything to
validation/holdout), leaving train empty and the fit to fail downstream. Fix alongside this
wave: after mask construction assert `n_train > 0` (and `n_train + n_validation + n_holdout
== n_rows`) with an actionable error naming the group distribution.

### TDD
- 2-group dataset with sizes that hash both groups out of train: today trains on 0 rows /
  fails obscurely; after fix, raises "group split produced an empty training set (2 groups,
  sizes 0.5/0.5) — reduce validation/holdout or use random split".

## Acceptance criteria
- `fold_column` either powers group-CV or no longer exists.
- Temporal and group splits can never silently produce empty/degenerate partitions; the UI
  shows actual partition sizes before training.
- k-fold CV metrics (mean ± std) available per run and logged to MLflow.
