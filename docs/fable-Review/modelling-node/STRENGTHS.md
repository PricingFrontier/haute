# What the modelling node already does right

Every claim below was verified during this review (code-read plus, where behavioural,
probes against the installed CatBoost 1.2.10). These are properties to **preserve** through
the remediation waves — several are easy to break accidentally while fixing the findings.

## Statistical core
- **Tie-corrected Gini** via `_aggregated_lorenz_points`: equals `2·AUC−1` exactly for
  binary targets, row-order invariant (canonical lexsort tie-break), and shares one
  aggregation with the plotted Lorenz curve so the chart and the scalar can never disagree.
- **Deviance metrics match sklearn** (weighted Poisson/Tweedie to 1e-6); weights applied
  once, no double-counting against CatBoost's internal eval weighting.
- **Prediction space is right**: Poisson/Tweedie `predict()` returns the mean (Exponent),
  the correct space for deviance and AvE. (The offset gap, M01, is about the baseline term —
  not the link.)
- **Non-finite handling is exemplary**: rows filtered *and surfaced*
  (`non_finite_rows_filtered` metric key), all-non-finite raises. This is the pattern
  M03-3's clamp surfacing should copy.
- **Diagnostics suite breadth** (SHAP, LossFunctionChange importance, PDP, double-lift,
  Lorenz, per-feature AvE with weighted bins + Missing/Other buckets, residuals) is a
  genuinely strong actuarial evaluation pack — most commercial tools ship less.

## Training engineering
- **The config→kwargs SSOT** (`_train_config.build_training_job_kwargs`) shared by live
  training and script export, created after two real drift bugs — the right pattern; M08's
  findings are about config assembly *outside* its boundary, not the SSOT itself.
- **Early stopping done right**: eval pool auto-enables `early_stopping_rounds=50`;
  CatBoost's `use_best_model` default shrinks the saved `.cbm` to `best_iteration+1`
  (verified through save/reload), so the artifact matches the reported best iteration.
- **Raw params passthrough** to the CatBoost constructor: a real power-user escape hatch —
  `grow_policy`, `bootstrap_type`, `rsm`, `border_count`, `od_*`, `eval_metric`,
  `auto_class_weights`, `task_type` all work today via the JSON editor.
- **Memory discipline**: y/w/baseline extracted and wide frames freed before Pool build
  (avoids the triple copy); float32 feature casts; the pandas round-trip skipped entirely
  when no categoricals; streaming sinks with lz4 for throwaway temp data; projection
  pushdown into every partition read; the diagnostics partition read once and reused for
  every diagnostic.
- **RAM/VRAM pre-flight is real**: metadata-based row-limit estimation wired into the train
  route with a seeded, order-preserving downsample (fixed the old head() bias) and
  user-visible warnings. The README's "probes a sample" wording is stale, but the feature is
  better than the claim.

## Lifecycle & contracts
- **Abort-safe temp lifecycle**: `owns_tmp` discipline, `_remove_temp_parquet` (loud on
  failure, never masks the in-flight error), `BaseException` guards around every temp write.
- **GPU fit lifecycle**: bounded abort-join, zombie-thread annotation via `add_note`, train
  dir retained rather than half-deleted under a live writer — loud, deliberate degradation.
- **Job lifecycle precedence table** prevents late `error` transitions from clobbering
  `cancelled`/`timed_out` — deliberate terminal-state race handling.
- **Feature contract subsystem**: content-hashed, hash-verified on load, stat-gated cache,
  categorical domain validation with example values in errors, loud
  `assert_contracts_match` naming the exact field. Per-model contract filenames prevent the
  old shared-file overwrite. (M06-8 is about *propagating* it to MLflow/deploy, not about
  its design.)
- **Score-time discipline**: feature order + categorical dtype enforcement raises rather
  than casts; multiclass proba loudly rejected at scoring; deploy modelScore never degrades
  to passthrough.

## UX
- **Degraded-run visibility**: `diagnostics_errors` from optional diagnostics (SHAP/PDP/GLM
  extras) reach the UI as a banner instead of being swallowed — the fail-loud philosophy
  carried into diagnostics.
- **Monotone constraints editor** (per-numeric-feature ±1) — real regulatory value, rare in
  OSS tools. (Needs M09-1's name validation behind it.)
- **The GLM factor builder** (typed terms, interactions, family/link validation with
  actuarial hints) shows the house UX standard the CatBoost params panel should match.
- **Honest labelling** of validation vs holdout metrics and the legacy `test_rows` naming
  pinned by tests.
