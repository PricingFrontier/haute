# 03 — What the code can support (the engineer's report)

Read-only survey of the working tree, 2026-11-20. "Unverified" = read in the code, not exercised.

**Path shorthand.** `H/` = `/home/ralph/suite/haute/`, `BE/` = `H/src/haute/`, `FE/` =
`H/frontend/src/`, `TB/` = `/home/ralph/suite/t-boost/python/t_boost/`,
`RS/` = `/home/ralph/suite/rustystats/python/rustystats/`, `SP/` =
`H/.venv/lib/python3.11/site-packages/`. t-boost (0.9.1) and rustystats
(0.11.1) are editable installs pointing at those two folders (`SP/t_boost.pth`, `SP/rustystats.pth`).

**The code's words against the owner's.**

| Owner's word | In the code |
|---|---|
| model type | `algorithm`, "family" (`BE/modelling/_descriptors.py:50`) |
| factor | `feature`, `feature_columns` |
| test rows | "holdout" partition, `final_test` (`BE/modelling/_split.py:56`, `_evaluation.py:427`) |
| training + validation rows together | "development" rows |
| which rows the charts used | `diagnostics_set`: `final_test`, `development`, `validation` (`BE/modelling/_training_job.py:2009`) |
| log | "candidate run" (`BE/modelling/_candidate_run.py:1`) |
| out of date | "stale": config hash + source + structural version (`FE/stores/useNodeResultsStore.ts:14-17`) |

---

## 1. Reusable interface parts

Charts are hand-drawn SVG. `echarts` is installed but only the Explore panel uses it
(`FE/panels/explore/chartRuntime.ts`).

| Part | File | What it does | Reusable as it stands? |
|---|---|---|---|
| Chart frame | `FE/panels/modelling/ChartScaffold.tsx` | Primitives only: `ResponsiveChart` (:21, measures width), `ChartSvg` (:108), `ChartLegend` (:237), `ChartValuesTable` (:140, a closed "values" disclosure), `ChartValueGrid` (:346), `ChartYAxisTitle` (:385), `ChartEmptyState` (:181), `TwoChartLayout` (:64). | Partly. There is no shared axes, series or hover component. Every chart computes its own geometry (see `AveChart`, `FE/panels/modelling/AveTab.tsx:67-80`). "One chart frame" is new work on top of these primitives. |
| AvE chart | `FE/panels/modelling/AveTab.tsx:46` `AveChart({ data })` | One factor: actual and expected by bin, exposure bars, hover on a bin. | One model only. It takes one row with exactly `avg_actual` and `avg_predicted`; the legend is fixed to two entries (:58-63). An overlay of several models needs a new chart. |
| Factor browser | `FE/panels/modelling/FeatureBrowser.tsx:40` | Searchable list with a small bar per item. Props: `features[{feature, importance}]`, `selected`, `onSelect`, `itemNoun` (:36), `rankedBy {label, description}` (:37). | Yes. `rankedBy` already lets the bars show any measure ("where it fits worst"). No marks, ticks, filter or arrow keys (the file has no key handler). Note the Modelling view does not use it: it has its own plain `FactorList` (`FE/modelling/ModellingView.tsx:424`). Two lists exist today. |
| Factor diagnostic layout | `FE/panels/modelling/FeatureDiagnosticTab.tsx:13,54`, `useDiagnosticFeature.ts:9` | Browser on the left, one chart on the right, the chosen factor shared across tabs. | Yes, for any per-factor pane. |
| Pane tabs | `FE/panels/PreviewPanelTabs.tsx:26` | Generic tab strip: `tabs[{key,label,disabled,indicator}]`, `activeTab`, `onChange`, `idPrefix`. `indicator` (:12) gives a warning or active mark with a label. | Yes. Already used by the Modelling view (`ModellingView.tsx:375`). The `indicator` could carry "out of date" or "training". |
| Results workspace | `FE/panels/ResultsWorkspace.tsx:63` | The docked results panel: tabs, Focus view, progress bar, `notices`, `provenance` (:58, "what the figures are"), header actions, resizable height. | Yes as a shell for one result. It is a bottom panel of the node editor, not a middle column. The Modelling view does not use it. |
| Result tabs | `FE/panels/ModellingPreview.tsx:25-36` | Chooses tabs by which result fields are filled (`ModellingPreview.tsx:151-179`, e.g. `result.tboost_tables != null`). | The tab bodies take `{ result }` — one run of one model. None overlays two. |
| Values tables | `FE/panels/modelling/TermTableView.tsx:47`, `termCharts.tsx:84-367` | One term as a table or chart: `axes`, nested `values`, `reference`, `mass`. Used by both `EBMTermsTab` and `TBoostTablesTab`. `termCharts` has `LevelBars`, `StepShape`, `LevelTable`, `InteractionLines`, `SurfaceTable`. | Yes for display, for t-boost and EBM alike. Read-only: no cell selection, no edit callback, no second ("fitted behind edited") series. |
| GLM tables | `FE/panels/modelling/GLMCoefficientsTab.tsx`, `GLMRelativitiesTab.tsx` | One row per coefficient, not per factor level. | Display only. Not the same shape as `TermTableView`. |
| Training progress | `FE/panels/modelling/TrainingProgress.tsx:16` | Progress bar, message, round counter, tuning line ("Trial 3 of 20"), time remaining, small live `LossChart` (:77). | Yes, per job. One job at a time (see §3). |
| Learning curve | `FE/panels/modelling/LossTab.tsx:26,50`, `LossChart.tsx:35` | Train and held-back curves, best round. | Yes. Already in the Modelling view (`ModellingView.tsx:534`). |
| Settings editor | `FE/panels/ModellingConfig.tsx:89-97` | One editor; the `panes` prop renders a chosen subset stacked (target, features, params, split, train, export). `onReviewPane` routes "go to" links. | Yes. This is how the view's Factors and Boost steps work today (`ModellingView.tsx:66-69`). It has 7 `algorithm ===` style branches. |
| Factor settings table | `FE/panels/modelling/CommonFeatureConfig.tsx:47` | In/out tick, direction arrows, search, included/excluded filter, Include all / Exclude all. Takes `noun="factor"` (:20). | Yes. This is the All models "factors" section already. |
| GLM terms | `FE/panels/modelling/GLMTermsConfig.tsx:59`, `TermCard.tsx:601`, `GLMInteractionsConfig.tsx` | A card per term: type, spline settings, monotonicity arrows (`TermCard.tsx:366`), levels and reference. | Yes. The GLM workflow's settings exist. |
| Split settings | `FE/panels/modelling/SplitAndMetricsConfig.tsx:63`, `EvaluationAllocation.tsx:90` | Strategy, validation method, test size, a bar of how rows are allocated per fit. | Yes. |
| Summary and score tables | `FE/panels/modelling/SummaryTab.tsx:167,219` | Validation metrics as mean, stddev, min, max across fits. | Partly. It is the nearest thing to a "score cell" but has no difference-from-reference. |
| Capability table | `FE/panels/modelling/algorithmCapabilities.json`, mirrored from `BE/modelling/_descriptors.py:851` `capability_fixture()` | Per model type: tasks, losses, `feature_controls`, `refit_policy`, allowed params, `supports_tuning`, `gpu_device`, `monotone_unsupported_losses`. | Yes. This is what the shell should read. It has no entries yet for own steps, own panes, hand edits or effect shape. |
| View state | `FE/stores/useModellingViewStore.ts:36` | Open model, step, pane, chosen factor. Not saved, not undone. | Yes; needs a chosen model type and a comparison selection added. |

---

## 2. What a run computes today

### 2a. How the rows are used

One run = `TrainingJob._run_evaluation` (`BE/modelling/_training_job.py:1747`). It makes an
evaluation plan, runs one "selection fit" per validation fold, then one final fit.

- **Splits.** Random, group or temporal; validation is `none`, `single` (one holdout) or
  `cross_validation` (2+ folds; temporal uses an expanding window); an optional test set
  (`BE/modelling/_evaluation.py:101-131`). The plan stores one label per row (:448-458).
- **A random split depends on the frame's columns unless `id_columns` is set.** The mask is drawn
  over a canonical row order: sort by the id columns, "then by every other column in the frame"
  (`BE/modelling/_row_order.py:9-10`, :32; `_training_job.py:1092-1101`). That frame holds only
  the chosen factors and roles (§3). So with no id column, dropping a factor can change the
  validation rows for the same seed. Group and temporal splits are not affected. Unverified.
- **Final refit is the default.** `refit_on_development` defaults to true (`_training_job.py:745-746`).
  The final fit uses `final_mask()`, which has only two codes: test rows, and everything else as
  training (`_evaluation.py:427-428`). So the final model never has a validation partition.
- **Selection fits return scores only.** `run_evaluation_fit` predicts the fold's validation rows,
  computes the metrics and drops the predictions and the model (`_training_job.py:1480-1507`).
- **Tuning.** Optuna TPE, by trial count (default 20), no time budget (`BE/modelling/_tuning.py:334,675`).
  Every trial is scored on the validation folds and the winner is chosen on them. It needs
  validation rows (`_tuning.py:275`). The GLM has no tuning (`refit_policy="none"`).

| Model type | Fit sees validation rows? | Early stopping today | Final refit |
|---|---|---|---|
| CatBoost | Yes, as `eval_set` (`BE/modelling/_algorithms.py:588`) | On the validation rows, 50 rounds by default (:557-558) | Round count = validation-weighted median of the folds' best round (`_tuning.py:594-600`, `_training_job.py:1934-1941`); early-stopping keys dropped (`_descriptors.py:793-805`) |
| LightGBM | Yes, in `valid_sets` (`BE/modelling/_lightgbm.py:262-263`) | `lgb.early_stopping` on them, default 50 (:43, :306-307) | Same as CatBoost |
| XGBoost | Yes, in `evals` (`BE/modelling/_xgboost.py:389-390`) | On them, default 50 (:46, :429); booster cut to the best round (:448) | Same as CatBoost |
| t-boost | No. `eval_df` is deleted unused (`BE/modelling/_tboost.py:596-598`) | Inside the fit: each bag holds back `validation_fraction` of its training rows (t-boost default 0.1, `/home/ralph/suite/t-boost/docs/features/early-stopping.md`) | `fixed_budget`: same `n_trees` ceiling, stops early inside again (`_descriptors.py:764-767`) |
| EBM | No. `eval_df` deleted (`BE/modelling/_ebm.py:353-354`) | None. `outer_bags=1`, `validation_size=0`, `early_stopping_rounds=0` (:418-421) | `fixed_budget`: same `max_rounds` |
| GLM | No (`_training_job.py:2513-2516`: "A GLM has no early stopping, so it never reads the validation rows at fit time") | None | Plain refit on development rows |

So t-boost, EBM and the GLM already follow proposed rule 2. The three gradient-boosting types do
not, and neither does tuning.

### 2b. Which rows the charts and scores use

`_compute_metrics` picks one partition for every diagnostic: test rows if there are any, else
validation rows, else training rows (`_training_job.py:3001-3009`). Because the final refit has no
validation partition, the result is:

| Config | Charts (AvE, lift, PDP, SHAP, residuals) drawn on | Headline scores |
|---|---|---|
| Refit (default), test rows set | **Test rows**, every run | Test scores from the final model; validation mean/stddev/min/max from the selection fits |
| Refit (default), no test rows | **Rows the model was fitted on** (`development`). The frontend says so: "the diagnostics are in-sample" (`FE/panels/modelling/diagnosticsSet.ts:8-12`) | Validation scores from the selection fits |
| `refit_on_development: false` (single holdout only), no test rows | Validation rows — the same rows a gradient-boosting model stopped on | Same |
| Validation `none` (an interactive build today) | Training rows (`H/docs/building-models/modelling.md`: "no validation set and no test set") | In-sample |

No config today draws charts on validation rows from a model that did not see them, except
t-boost, EBM or GLM with the refit switched off.

### 2c. Each diagnostic

| Diagnostic | How | Evidence |
|---|---|---|
| Scores | `compute_metrics` on the chosen partition. Per-fold validation scores aggregated to mean, stddev, min, max, weighted by fold size. A single holdout has one fit, so stddev is 0. | `_metrics.py:112`; `_evaluation.py:1305-1329` |
| AvE per factor | **Only the model's factors.** Numbers in 10 quantile bins; categories as the top 15 by mass plus "Other". Bin mass = weight × exposure, where exposure is a log-link offset. No range per level, no second-factor split. | `_training_job.py:3087-3095`; `_metrics.py:459-481` |
| "Double lift" | Not a two-model double lift. It is one model's actual and predicted by decile of its own prediction, 10 bins of equal row count. | `_metrics.py:406-414` |
| Lorenz, residuals, actual-vs-predicted | From `y_true`, `y_pred`, weight. Need only predictions. | `_training_job.py:3154-3156` |
| PDP | 500-row sample, 50 grid points, through each adapter's `predict`. Works for every model type. | `_metrics.py:912-920` |
| SHAP | 5,000-row sample. CatBoost, LightGBM, XGBoost, t-boost only (an adapter with `shap_values`). Not GLM, not EBM. | `_shap.py:21`; `_training_job.py:3108` |
| Importance | From the model, no rows. Method differs by type (`importance_method`, e.g. t-boost "Variance share", EBM "Mean absolute term score"). CatBoost adds a loss-change importance. | `_descriptors.py:75,670,771`; `_training_job.py:3129` |
| Learning curve | From the fit. For t-boost: mean over bags of training deviance and held-back deviance. A refit keeps the single holdout fit's curve too. | `_tboost.py:504-521`; `_training_job.py:2000-2002` |
| t-boost tables | `estimator.tables()` on the fit's own training rows; `support` is training mass; `relativities` only under a log link. | `_tboost.py:609`, :143, :251-273 |
| EBM terms | `term_scores_` and bins from the estimator. Same shape as t-boost tables: `term, features, importance, axes, scores`. | `_ebm.py:275-305` |
| GLM | Coefficients, relativities (log link only, one per coefficient with CI), fit statistics, inference status, smooth terms, regularisation. | `_rustystats.py:548-611`, :846 |

Predictions are computed in 65,536-row batches (`_training_job.py:2933`, :3051) and never stored.

---

## 3. Where results live, and for how long

| Thing | Where | Lifetime | Evidence |
|---|---|---|---|
| The node's settings | The project's JSON file, through the pipeline graph; saved, committed and undone like any node edit | Durable, in git | `ModellingView.tsx:321-327`; `H/docs/building-models/modelling.md` "Switching views" |
| A run's result (all diagnostics as JSON) | The server's job record, in memory | 24 hours, or until the server restarts | `BE/routes/modelling.py:84-88`; `BE/routes/_job_store.py:1-4,33` |
| The same result in the browser | Zustand store, keyed by node, **8 results at most**, least recently used dropped | Until reload | `FE/stores/useNodeResultsStore.ts:61,538` |
| A pointer to the last completed job per node | Browser `localStorage` (`haute.modelling.trainedJobs.v3`): job id, config hash, lineage. After a reload the result is fetched again from the server if it still has it | Survives a reload, not a server restart | `FE/utils/trainedJobHandles.ts:24`; `FE/panels/modelling/useTrainedJobRestore.ts:14-27` |
| Model file, feature contract, evaluation plan/results/report, tuning files | One directory per job under the **OS temp dir** (`haute/artifacts/v1/modelling_training/train_*`), 4 GB cap | Released when a newer run of the **same node** completes, or when the job record expires; stale ones reaped | `BE/routes/_training_artifacts.py:41,75,338-340`; `BE/routes/_training_lifecycle.py:1116-1149` |
| Predictions | Nowhere | — | §2c |
| A saved model | Copied into the project on **Save** (the Export pane) | Durable | `BE/routes/modelling.py:510` |
| A logged run | MLflow | Durable | `BE/modelling/_mlflow_log.py:175` |
| "What was logged" for a node | An export receipt on the in-memory job record only | Lost with the job | `BE/routes/modelling.py:449-461` |

**Nothing a run produces is in the project today.** "Runs live locally" is new. Durable stores to
copy the pattern from exist: `.haute_cache/analyses` (atomic JSON keyed by data point and data
version, `BE/_analysis_results.py:1-10,61-65`) and node-output snapshots as immutable generations
with locks (`BE/_node_snapshots.py:1-24`). `.haute_cache/` is git-ignored (the demo's `.gitignore`).

**Jobs, progress, cancel.**

- `POST /api/modelling/train` returns a job id at once. Preparation runs in a thread, the fit in a
  supervised worker process (`BE/routes/modelling.py:91-99`; `_training_lifecycle.py:318-362`).
- **One training job at a time, server-wide.** A second start is refused: "A training job is
  already running" (`_training_lifecycle.py:1338-1341`).
- Each training builds its frame from the pipeline through the shared snapshot store
  (`BE/routes/_training_preparation.py:769`; how much is reused is unverified) and writes a
  temporary parquet holding **only the chosen factors and the role columns**, or a GLM's term
  columns (:281-288). A sample run is `row_limit`: a uniform sample with a fixed seed of 42
  (:93-106; `_training_artifacts.py:81`).
- Progress is polled (`GET /train/status/{id}`): fraction, message, round, live loss history, tuning
  phase and trial (`BE/routes/modelling.py:111-143`). EBM reports only start and end (`_ebm.py:435-444`).
- Cancel is `POST /train/cancel/{id}` (`_training_lifecycle.py:717-731`), checked at stage boundaries
  and on each reported round. A memory estimate exists (`POST /estimate`, `BE/routes/modelling.py:251`).

---

## 4. The 19 proposed items

Size: S = days, M = a week or two, L = several weeks, XL = a project. Sizes are rough.

| # | Exists already | Missing | Size | Main risk |
|---|---|---|---|---|
| 1 One model per model type in a node | Adapters and capability table for six types. One flat config with one `algorithm` (`BE/_node_config_models.py:802`). | Config with shared block + a block per type; results, staleness and artifacts keyed by (node, model type) — today all three are keyed by node alone (`useNodeResultsStore.ts:538`; `_training_lifecycle.py:1123-1135`). A migration for existing configs. | L | Artifact release by `node_id` would delete a second model type's files. Every consumer of the flat config (export script, scripted `TrainingJob`, scoring, docs) changes. |
| 2 Learn only from training rows; test sealed until logging | t-boost, EBM, GLM already comply. The plan has an "unused" row code (`_evaluation.py:306`). | An inner early-stopping slice for the three gradient-boosting types and for tuning; a mode that skips scoring the test rows; a place to record each look. | M | The refit round count comes from the best round on validation rows today; it must come from the inner slice. Group and temporal splits need a matching rule for the inner slice. |
| 3 Quick and thorough depth, a range on every score | Holdout and cross-validation both exist. Fold mean/stddev/min/max exist (`_evaluation.py:1305-1329`). | A range for a single holdout (needs kept predictions to resample). Out-of-fold predictions are computed and dropped. A test for "gap larger than the range". | M | Thorough costs folds + 1 fits. With one job at a time, thorough on four model types is a long queue. |
| 4 Runs keep predictions; shared charts on demand | Predictions are computed at three points (`_training_job.py:1480`, :3025, :3051). | Storing them; a stable row key; a store that survives a restart; an endpoint that groups predictions by any factor. See §5.4. | L | Row identity. Honest validation predictions come from the selection-fit model, not the logged refit. |
| 5 One common effect shape | t-boost and EBM already share one (`axes`, `scores`). PDP exists for every type. | A per-factor table for the GLM (rustystats has `to_rate_tables`, `RS/rate_tables.py:20`, unused by Haute). A marginal-effect rule for tables with interactions. | M | "Exact" is only true for a main effect with no interaction on that factor. Bins differ per model, so overlaying needs a common grid. Relativities exist only under a log link. |
| 6 Menu row | `PreviewPanelTabs` with indicators; the view's step buttons (`ModellingView.tsx:350-371`). | Per-model-type state, colour, eye toggle, Add. | M | Depends on #1. |
| 7 Fit view, effect view, split by year or factor | `AveChart` for one model, fit view only. | Multi-series chart; effect view (#5); any split (needs #4, plus columns that are not in the training parquet). | L | Without #4 a split or a left-out factor cannot be charted at all. |
| 8 Sort by worst fit; reviewed ticks | `FeatureBrowser` `rankedBy` bars. | A worst-fit measure; where review ticks are stored (config? cache?). | S–M | The measure is noisy on thin levels. Ticks in the config file make git diffs noisy. |
| 9 What changed; what-if runs | Config hash and lineage per run (`trainedJobHandles.ts:27-33`). Undo for settings. | Keeping the replaced run (today its artifacts are released, `_training_lifecycle.py:1116`); a diff of settings and scores; a job that trains from a config that is not the saved one; a queue. | L | One job at a time. "Each factor left out in turn" is N full trainings. |
| 10 Checks | GLM diagnostics; `diagnostics_errors` list. rustystats has score tests for factors not in the model (`RS/diagnostics/api.py:2161`), unused. | Every check listed is new. Leak, flip-by-year, thin-level and duplicate checks need the node's data and kept predictions. | M–L | Checks that need a second fit (a hand edit "the data has moved away from") double the cost. |
| 11 Workflow per model type; GLM with no setup, borrowing | GLM terms UI and backend (`BE/modelling/_glm_terms.py:50-61`: linear, categorical, bs, ns, ms, target/frequency encoding, expression). t-boost tables carry `cuts` per axis (`_tboost.py:106`). | Default terms from dtypes; "borrow bands / curve / interactions" actions. | M | A spline default on a low-cardinality number can fail to fit. No banded term exists: `grep -i band` finds nothing in `_glm_terms.py` or `FE/panels/modelling/glmTerms.ts`, though a config docstring mentions one (`BE/_node_config_models.py:827`). Borrowed bands need a new term type or a pre-processing column. |
| 12 Logging scores test rows then, with checklist and note | Logging a completed job (`BE/routes/modelling.py:350`); HTML model card. | A deferred test-scoring step; note, checklist, reviews and edits in the card and run. | M | Logging today needs the job's files still on the server (`require_training_artifacts`, `BE/routes/modelling.py:386`). A durable run store is a precondition. |
| 13 Defaults by data size | Memory estimate, row limit, row counts from the plan. | The rule itself and a timing estimate. | S | A sample run's charts are on fewer rows; small levels vanish. |
| 14 Hand edits for t-boost, GLM, EBM | All three models are plain numbers that can be changed and scored (§5.3). | Everything else. | S alone; the cost is in 15–19 | See 15–19. |
| 15 Edit beside the fitted value, with record, undo, model card | Undo for config edits. | Edit records in the config; card section. | M | An edit must be keyed by level or value range, not cell index (§7). |
| 16 Preview at once; refit set against the replaced run | t-boost and EBM contributions per table (`_tboost.py:228`, `_ebm.py:96`). | Preview scoring needs kept predictions plus each row's cell for the edited table; "which tables moved" needs the replaced run kept. | L | Without #4 a preview means rescoring the whole dataset in a job. |
| 17 Edits in the model's table pane | `TermTableView`, `termCharts`. | Selection, editing, the faint fitted series. | M | Interaction tables are 2-D or more; editing a main effect leaves them untouched. |
| 18 An edit is a constraint held in every training | Offsets in every adapter. rustystats `complement` (§5.2). | No library can hold part of a table fixed (§5.2). The offset route fixes a **whole** table and needs a second offset beside the exposure. | XL | This is library work in t-boost, rustystats and EBM, not Haute work. |
| 19 Until then, edits laid on top | Models can be edited and saved (§5.3). | The apply step after each fit, in every path (retrain, folds, final refit, scheduled). | M | A laid-on edit breaks the balance of the total and, for t-boost, the centring of tables that importance and contributions assume. |

---

## 5. The questions

### 5.1 Can t-boost prune, band and graduate again without boosting again? Path scores?

**Not through its public API today.**

- A fit boosts, then prunes, bands and graduates inside one `fit` call. The boosted model is a
  local variable (`full_model = booster.fit(...)`, `TB/sklearn.py:4267`). The estimator keeps only
  the deployed tables: "pruning replaces the `_Model` with a `_TableModel`, which carries no
  fit-time diagnostics" (`TB/sklearn.py:4283`).
- **Pruning and banding need the boosted model.** Pruning scores prefixes on each bag's out-of-bag
  rows (`full_model.bag_oob_group_raw`, :4050); banding measures noise as the spread between bags
  (`full_model.bag_score_variance`, :4148). **Graduation needs only the tables and the training
  rows** (`_graduate(model, x32, y32, ...)`, :3919), so it could be re-applied to a table model.
- The native binding has the pieces as separate calls: `apply_keepset`, `band`, `apply_graduation`
  (`TB/_t_boost.pyi:400,490,535`). "Apply a step again" is a modest t-boost change (keep or save the
  boosted model, expose the steps). Unverified: whether the boosted model serialises compactly.
- **Path scores are exposed, for pruning only.** `pruning_report_["path"]` holds `sizes`,
  `oob_deviance` per prefix, the chosen `best` and the ranked `sequence` (`TB/sklearn.py:4108-4118`),
  32 points by default. It needs `n_bags >= 2`; one bag falls back to a fold vote with no path.
- **Banding and graduation give one chosen point, not a curve** (`pruning_report_["banding"]`; one
  smoothing strength per table in `graduation_report_`). A curve means several runs or t-boost work.
- **Unknown: graduation under monotonicity.** The README says graduation "is skipped for
  monotone-constrained fits"; the code says a monotone fit "runs every stage like any other fit
  and is then projected back onto its signs" (`TB/sklearn.py:3895-3896`).
- **Haute reads none of these reports** (no use of `pruning_report_` in `BE/` or `FE/`). Its allowed
  t-boost params have `prune`, `band_tolerance`, `graduate` but not `prune_path_fraction` or
  `prune_path_tolerance` (`BE/modelling/_descriptors.py:736-742`): a point on the curve cannot be passed.

### 5.2 Fixed values, offsets, continuing a fit

| | Hold some values fixed in a fit | Offset | Continue from an earlier fit |
|---|---|---|---|
| t-boost | No such parameter (no `fixed`, `init_model` or `warm_start` in `TB/sklearn.py`). | Yes. `fit(..., exposure=, offset=)`, and both together: "the offset joins it as `exposure * exp(offset)`" (`TB/sklearn.py:3606-3625`). Haute passes one of them (`_tboost.py:440-443`). Classification: Haute refuses an offset (:558-561). | No. |
| GLM (rustystats) | No option to fix a coefficient in `fit` (`RS/formula.py:5258-5281`). It has sign constraints and monotone splines only. | Yes, `exposure` and `offset` both (`RS/formula.py:5582-5583`). | No start values in `fit`. But `complement` takes an earlier `GLMModel`: its predictions are "added to the existing offset, so that regularization shrinks coefficients toward the complement" (`RS/formula.py:1255-1265`). Whole model only. Haute does not use it. |
| EBM (interpret 0.7.8) | No, but `exclude` drops a term from a fit, and `init_score` accepts "a model that can generate scores or per-sample initialization score" (`SP/interpret/glassbox/_ebm/_ebm.py:498-509`). A fixed term = excluded from the fit + supplied as `init_score`, then merged back. Not built; unverified. | Yes, `init_score`. Haute passes it for regression only (`_ebm.py:431-434`). | Only by stacking on `init_score`. No true continuation. |
| LightGBM / XGBoost / CatBoost | Not applicable | Yes (`init_score`, `base_margin`, pool baseline) | The libraries can (`init_model`, `xgb_model`, `init_model`); Haute's adapters do not pass them. |

**Haute carries one offset column per model** (`BE/modelling/_algorithm_base.py:71`). The "fixed
table goes in as an offset" route needs a second, computed offset beside the exposure, in fit,
predict, scoring and the feature contract. It fixes a whole table, not some levels.

### 5.3 Can an edited model be saved back and scored?

- **t-boost: yes in principle, unverified.** The saved file is t-boost's JSON document of tables
  (`_tboost.py:280-284`). I found no checksum. There is **no API to set a cell**: an edit means
  changing `values` in the JSON and loading it. Risk: tables are centred on the training data, and
  importance and contributions assume that.
- **GLM: yes in principle, unverified.** `to_bytes` pickles a state dict whose `params` is the
  coefficient array (`RS/formula.py:4025-4060`); `from_bytes` rebuilds a predictor from it. Standard
  errors would be stale. A spline has no single relativity to edit: it is several coefficients.
- **EBM: yes.** `term_scores_` are plain arrays on the estimator; Haute saves the estimator with
  joblib (`_ebm.py:132-139`). interpret also ships `monotonize`, `remove_terms`, `scale`, `sweep`.
- All three then score through the unchanged `predict`. None is built in Haute.

### 5.4 How predictions are produced, and what keeping them involves

- Produced by `algo.predict(model, frame, features, offset=...)` on a partition read from the
  temporary split parquet (`_training_job.py:1480`, :3051), response scale, then discarded.
- What keeping one per row per run involves:
  1. **Decide which model predicts which row.** Validation rows: the selection-fit model (honest).
     Test rows: the final model. Training rows: in-sample only. Under cross-validation every
     development row already gets an out-of-fold prediction. So a run holds up to two columns.
  2. **A row key.** The plan's labels are positions in a temporary parquet that is deleted
     (`_training_job.py:2401`), with null targets removed and perhaps sampled. `id_columns` exist
     in the config (`BE/modelling/_row_order.py:32`). Without one, store a snapshot to align to.
  3. **All the node's columns.** The training parquet keeps only chosen factors and roles
     (`_training_preparation.py:281-288`). Charting a left-out factor needs the full node output.
  4. **A new artifact kind** through the worker protocol; the allowed kinds are a fixed set
     (`_training_artifacts.py:55-72`).
  5. **A durable store** keyed by node, model type, run and data version, with cleanup.
  6. **An aggregation endpoint**: predictions and actuals by a factor, with an optional second
     factor. Disk cost: 8 bytes per row per column before compression.

### 5.5 Early stopping, and stopping on rows held back from the training rows

- **How each stops: the table in §2a. Could they stop on held-back training rows instead? Yes,
  without touching the adapters.** Each takes `eval_df` (CatBoost `eval_pool`) as an argument. The
  one place that chooses is `_train_model`, which loads the validation partition
  (`_training_job.py:2500-2516`, :2579-2610). Carve an inner slice from the training partition
  there (or as another group in the plan) and pass that instead.
- Knock-on changes: the refit's round count must come from the inner slice
  (`_training_job.py:1934-1941`), and the slice must respect a group or temporal split.
- EBM would need its own switch turned back on (`validation_size`, `early_stopping_rounds` are
  reserved, `_descriptors.py:643-650`). Its adapter warns that rows passed through `bags` leak
  "their targets into the intercept" (`_ebm.py:3-5`).

### 5.6 What logging records, and the model card

- **Trigger.** `POST /api/modelling/mlflow/log` with a completed job id (`BE/routes/modelling.py:350`).
  It refuses if the job has no evaluation report (:380-385) or its files are gone (:386). The run
  is named "`<node label> · <date time> UTC`" (`BE/modelling/_candidate_run.py:386`).
- **Tags.** Contract version, job id, node label and id, pipeline, trained-at, Haute version, model
  type, task, target, plan hash, training-identity hash, git commit and dirty flag (:364-382).
- **Params.** Model type, task, target, weight, split strategy and method, every final parameter,
  row counts, factor count, best round, fit evidence, tuning summary (:338-360).
- **Metrics.** Test scores; validation mean, stddev, min, max; tuning baseline, winner and
  improvement (:47-52). A run with test rows may not publish development scores (:316-319).
- **Artifacts.** The feature contract; each diagnostic as JSON, the own tables included
  (`BE/modelling/_mlflow_log.py:151-172`); the evaluation plan, results and report; tuning files;
  the model card; the model as an MLflow pyfunc with a signature, plus the native file (:275-283).
- **Model card.** One HTML page with fixed sections: training summary, test metrics, validation
  metrics, tuning, loss curve, double lift, Lorenz, actual vs predicted, residuals, importance,
  SHAP, AvE per factor, PDP, parameters (`BE/modelling/_model_card.py:104-300`). No notes,
  checklist, reviews, hand edits or comparison with another run.
- Registration and promotion happen outside Haute (`_candidate_run.py:3-4`). The `haute.node_id`
  tag means "last logged run for this node" could be read back from MLflow; nothing does so today
  (unverified).

---

## 6. Duplication, and the cleanest boundaries

**Where duplication would creep in.**

- **Two factor lists** already: `FactorList` in the view and `FeatureBrowser` in the result tabs.
  Fold them into one before adding sort, marks and ticks.
- **AvE computed twice** if the factor chart is built on kept predictions while
  `compute_ave_per_feature` still runs in training. Keep one binning rule in one function.
- **A chart per pane with its own geometry.** Nine component files in `FE/panels/modelling/` draw
  their own SVG. A multi-model chart copied per pane would multiply that.
- **Charts drawn twice.** The model card draws its own SVG charts in Python
  (`BE/modelling/_charts.py:260-828`). A new chart that must appear in the card is built twice.
- **Term tables.** t-boost and EBM share `TermTableView`; the GLM has its own two tabs. A hand-edit
  UI gets built three times unless the GLM's per-factor table takes the same shape.
- **Model-type branches.** Frontend: `ModellingConfig.tsx` 7, `trainingObjective.ts` 4,
  `GLMTargetConfig.tsx` 3. Backend: `_training_job.py` (3,607 lines) about 25, plus
  `hasattr(algo, ...)` probes (:3108, :3129, :3181, :3188, :3196). Each new feature adds more.
- **The result type.** `TrainResult`, `_MetricsResult`, `ModelDiagnostics` and `_RESULT_FIELDS` each
  list every model type's fields by name (`_training_job.py:409`, :514; `_result_types.py:15-38,88`).

**Backend boundary.**

- Already there: `BaseAlgorithm` (`fit`, `predict`, `feature_importance`, `save`), `NativeModel`
  (`BE/modelling/_algorithm_base.py:56-106,143`) and one `AlgorithmDescriptor` per model type.
- Add to the adapter, as declared optional methods instead of `hasattr` probes:
  `own_diagnostics(model)` (replacing `glm_result`, `ebm_terms`, `tboost_tables`);
  `effect(model, factor) -> {axis, values, exact}`; `apply_edits(model, edits)`; for t-boost,
  `steps` and `apply_step(model, step, params)`.
- Add to the descriptor: own steps, own pane ids, whether edits are supported and how (refit or
  laid on), whether the fit uses evaluation rows (so the shell knows to carve an inner slice).
- The shell owns: the plan, the inner slice, kept predictions, every chart computed from
  predictions, comparison, the run store, logging. None needs the model type.
- The rule of thumb in `overview.md` holds against the code: AvE, lift, Lorenz and residuals need
  only predictions; PDP needs only `predict`; importance, SHAP and the tables need the model.

**Frontend boundary.**

- A registry keyed by model type: `{ settingsSections, ownPanes, tableEditor?, colour }`, read
  beside `algorithmCapabilities.json`. Panes are decided in two places today
  (`ModellingPreview.tsx:151-179`, `modellingPanes.ts:16-20`); both become lookups.
- Shell parts with no model-type knowledge: factor list, pane tabs, chart frame, factor chart,
  comparison, run list, training progress, and the All models settings (`TargetAndTaskConfig`,
  `CommonFeatureConfig`, `SplitAndMetricsConfig` already fit).
- The GLM's settings are already separate components behind one switch, `usesSharedPanes`
  (`algorithmCapabilities.ts:60-62`).
- Results must be re-keyed from node to (node, model type, run): `useNodeResultsStore` holds one
  result per node.

---

## 7. Facts the designers will get wrong unless told

1. **Charts are never on validation rows in the default setup.** Test rows when a test set exists,
   otherwise the rows the model was fitted on (§2b). Sealing the test rows removes today's charts.
2. **A random split can move when the factors change.** With no `id_columns`, the validation rows
   depend on the columns in the training frame (§2a, unverified). Two model types, or one model
   before and after dropping a factor, may be judged on different rows. "Actual is drawn once"
   needs an id column, a group or temporal split, or a split fixed once per node.
3. **The honest prediction and the logged model are different models.** The logged refit has no
   honest prediction for any training or validation row (§5.4).
4. **Predictions are not kept and rows have no stable key** (§5.4).
5. **The training data holds only the chosen factors and roles** (§3). A left-out factor, a year or
   a second factor for a split is not there.
6. **Nothing from a run is stored in the project.** A server restart loses every run (§3).
7. **One training job at a time, server-wide** (§3). Train-all and background what-ifs need a queue.
8. **A newer run releases the older run's files for that node** (§3). A second model type or a
   reference model would lose its model file under today's rule.
9. **t-boost cannot re-prune or re-band a fitted model.** Only pruning has a path of scores, with
   two or more bags. Haute shows no t-boost report and cannot pass the path settings (§5.1).
10. **No library holds part of a table fixed in a fit** (§5.2). "Refit around an edit" is library
    work; assume "laid on top" for every model type at first.
11. **Haute has one offset column per model**, usually taken by the exposure (§5.2).
12. **Bins move between runs.** t-boost borders and EBM bins come from each fit (`_tboost.py:99`,
    `_ebm.py:293`). Key an edit by level name or value range, never by cell number.
13. **GLM monotonicity already exists, per term** (`_glm_terms.py:52-59`; arrows in
    `TermCard.tsx:366`). Missing: the mapping from the shared direction. The GLM adapter raises if
    shared `monotone_constraints` reach it (`_rustystats.py:737-740`).
14. **Shared settings have other gaps.** LightGBM and XGBoost refuse monotonicity with MAE; EBM
    cannot interact a monotone factor (`_descriptors.py:598-603`); arrows are for numeric factors
    only. CatBoost has no Gamma, EBM and t-boost no MAE, the GLM is regression only.
15. **Smaller ones** (§2c, §2a, §3): today's "double lift" is a one-model decile chart; AvE covers
    only the model's factors with fixed bins, no range and no split; scores have a spread only
    under cross-validation; tuning has a trial count, not a time budget; GLM relativities are per
    coefficient and log-link only; no SHAP for the GLM or EBM; EBM has no early stopping and no
    live progress; a sample run always uses seed 42.
16. **An interactive build today is t-boost only**, with no split, tuning, save, log or export
    (`H/docs/building-models/modelling.md`). Other model types are edited in the node panel.
17. **Logging needs the run's files still on the server** (§5.6). The model card has fixed sections.
18. **Undo restores settings, not the previous run's result.**

## Concerns about settled items

No settled line is blocked by the code. Three need care:

- **"An edit carries over when the model is trained again."** Only if an edit is stored against a
  level name or a value range (fact 12). Smallest change: say so in the rule.
- **"Refitted around it where the model type allows."** None allows it for part of a table today
  (fact 10). The wording covers this; design for "laid on top" first.
- **"Runs live locally."** Nothing does yet (fact 6). The durable run store comes first.
