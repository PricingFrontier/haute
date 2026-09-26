# Modelling issues roadmap

## Scope

What the Model Training node shows while a model trains and once it has
trained: the Train tab's run summary and memory estimate, the live progress
panel, the results workspace (Summary, Loss, Lift, Features, AvE) and the
node's palette entry. The issues were found on 26 September 2026 while
filming the landing page's training clip, a real training of the motor demo's
`competitor_modelling` node, so every package names what was seen on screen
and the code that produces it.

The run each package refers to:

- **Project:** `C:\Users\ralph\motor-pricing-demo`, editor source `nb_batch`,
  so training reads `outputs/nb_batch.parquet` (100,000 quotes) left-joined on
  `quote_id` with `data/competitor_insight_100k.parquet` (100,000 rows) by the
  `competitor_join` Edge Join, which declares no `validate` contract.
- **Model:** CatBoost, Tweedie (variance power 1.99), target `avg_cheapest_5`,
  1,000 iterations, learning rate 0.05, depth 6, `l2_leaf_reg` 3, early
  stopping after 50, holdout validation of 20% (80,000 training and 20,000
  validation rows), metrics Gini and Tweedie deviance. Nine features:
  `channel` (7 levels), `policy_cover_type` (3), `business_use` (2),
  `ncd_years`, `annual_mileage`, `year_of_manufacture`, `engine_power_bhp`,
  `total_claim`, `total_incurred`. The run summary reads "2 total fits: 1
  validation fit + 1 final fit".
- **Haute:** `rating-code-improvements` at `55362979` (merged as PR #261),
  served in production mode from the built bundle; the packages were rechecked
  against `c1428a1c`.
- **Machine:** Windows 11, 32 logical CPUs, 69 to 95 GB of RAM free, often
  busy with other work; timings below say which state they were taken in.

Two related findings are out of scope: the parser refusing pipeline files
saved before node declarations is handled at `c1428a1c` by node recovery, and
the demo pipeline has been rewritten in the declaration format.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MDL-01 | Planned | P2 | The memory estimate says how many rows training will really use, and never reports a join's worst case as the row count. |
| MDL-02 | Planned | P2 | The live loss curve draws while CatBoost, LightGBM and XGBoost train. |
| MDL-03 | Planned | P2 | The Loss tab shows the whole fit and says which fit it is. |
| MDL-04 | Planned | P2 | Chart value axes label a narrow range with distinct numbers. |
| MDL-05 | Planned | P3 | Training progress refreshes about once a second while it changes. |
| MDL-06 | Decision | P3 | A CatBoost model with small categorical columns does not train many times slower than it needs to. |
| MDL-07 | Decision | P3 | The Summary tab leads with the out-of-sample metrics when a validation fit ran. |
| MDL-08 | Planned | P3 | An open page recovers when the frontend it was served from has been rebuilt. |
| MDL-09 | Decision | P3 | A trained model's results survive a server restart, or the panel says why they are gone. |
| MDL-10 | Planned | P3 | The Model Training palette entry names the model families it trains. |

## Planned improvements

### MDL-01 — The memory estimate reports a join's worst case as the row count
**Why:** For the demo model the Train tab shows a "Will downsample" box
reading Source rows 10,000,000,000, Training rows 149,958,852 (with 69.4 GB
free; 204,238,763 with 94.6 GB free), Est. training RAM 48.6 GB (66.2 GB) and
Available RAM 69.4 GB (94.6 GB), while the run summary above it says 80,000
training and 20,000 validation rows. The server logs the same decision when
training starts: `downsampling safe_rows=149290300 total_rows=10000000000
warning='Dataset downsampled to 149,290,300 of 10,000,000,000 rows to fit in
available RAM (69.1 GB). Estimated peak training memory: 3241.0 GB.'`, and
the warning is stored on the job. Nothing is downsampled: the row limit (149
million) is far above the 100,000 rows that arrive, and the model trains on
all of them.

10,000,000,000 is 100,000 × 100,000. The estimate takes its row count from the
graph's proven cardinality upper bound (`cardinality.output_rows` in
`estimate_safe_training_rows`), and an Edge Join without a `validate`
contract carries the row product, exactly as an undeclared Polars join does.
The bound is a correct worst case for admission, but the Train tab presents it
as the size of the data, says it "Will downsample", sizes the RAM from it, and
the job carries a downsampling warning for a run that downsampled nothing. An
analyst reading it would reasonably think training is about to throw data
away, or that the pipeline has exploded, and nothing on screen says the
number comes from a join that could be declared many-to-one.

**Plan:** Keep the worst-case bound for admission, and stop presenting it as
the row count:

1. When the bound depends on an undeclared join (the cardinality evidence
   already records the join node), the estimate says so: "Up to
   10,000,000,000 rows: `competitor_join` has no key contract", with a link
   that opens the join's settings, where declaring `many-to-one` (`validate=
   "m:1"`) turns the bound into the base frame's row count.
2. Training decides whether to downsample from the rows it actually has
   (it writes its input to a temporary Parquet file before the split, so the
   count is known), and records the warning only when it did. The Train tab's box reads "Will downsample" only when the
   decision rests on a proven count; with an unproven bound it reads
   "Row count not proven" in the neutral style, with the upper bound and the
   RAM it would need.
3. The client-side "Training rows" figure uses the server's `safe_row_limit`
   rather than recomputing it (the two differed: 149,958,852 on screen,
   149,290,300 in the log, for the same run).

**Acceptance:** On the demo pipeline the Train tab no longer says "Will
downsample" or shows a ten-billion row count as fact; with `validate="m:1"`
on `competitor_join` it shows 100,000 rows and "Dataset fits in memory". A
route test estimates an undeclared left join of two 100,000-row inputs and
asserts the response marks the count as an unproven bound naming the join; a
training test on that graph asserts no downsampling warning on the job and
all rows trained; a component test renders both box states.

**Dependencies:** None.

**Evidence:** `src/haute/_ram_estimate.py::estimate_safe_training_rows`;
`src/haute/_cardinality.py::join_cardinality_upper_bound`;
`src/haute/routes/_training_preparation.py::estimate_training_memory`;
`src/haute/routes/modelling.py::estimate_training`;
`src/haute/routes/_training_lifecycle.py`;
`frontend/src/panels/modelling/TrainingActionsAndResults.tsx::TrainingActionsAndResults`.

### MDL-02 — The live loss curve never draws
**Why:** While the demo model trains, the progress panel shows the iteration,
the bar, "Round 699/699 Tweedie:variance_power=1.99: 107.1548" and, once
more than 200 iterations have passed, "Showing latest retained loss-history
window.", with nothing under that line. `TrainingProgress` renders
`LossChart` from `train_loss_history`, and `LossChart` returns `null` unless
the first row has a key starting with `train_`. The live rows never have one:
the progress handler in `_training_lifecycle.py` appends
`{"iteration": ..., **metrics}`, and each engine's callback builds `metrics`
with the training metric's bare name (`Tweedie:variance_power=1.99`) and the
evaluation metric as `validation_<name>`. The same callbacks build a second,
prefixed row (`train_<name>`, `eval_<name>`) for the final loss history,
which is why the Loss tab after training does draw. The mismatch is shared by
`_CatBoostProgressCallback`, the LightGBM `progress` callback and the XGBoost
callback, so no boosted model has shown a live curve; GLM and EBM report no
per-iteration loss.

**Plan:** Send the prefixed row the callbacks already build as the progress
event's history row, and keep the bare-named metrics only for the numeric
readout; `LossChart` then finds `train_` and `eval_` keys as the Loss tab
does. Hide the "latest retained window" note when there is no chart to
qualify.

**Acceptance:** A worker test for each of CatBoost, LightGBM and XGBoost
asserts the live history rows carry `train_` keys, and `eval_` keys during a
fit with an evaluation set; a `TrainingProgress` component test with such a
history renders the loss curve, and with no curve renders no window note.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_algorithms.py::_CatBoostProgressCallback`;
`src/haute/modelling/_lightgbm.py`; `src/haute/modelling/_xgboost.py`;
`src/haute/routes/_training_lifecycle.py`;
`frontend/src/panels/modelling/TrainingProgress.tsx::TrainingProgress`;
`frontend/src/panels/modelling/LossChart.tsx::LossChart`.

### MDL-03 — The Loss tab shows only the last 200 iterations
**Why:** After the demo model trained, the Loss tab plotted one curve over
iterations 512 to 711: a nearly flat tail, with the steep early descent
missing. The results payload runs the loss history through
`_bounded_loss_history`, which keeps the last `HAUTE_TRAIN_LOSS_HISTORY_LIMIT`
rows (default 200), and `LossTab` does not read `loss_history_truncated`, so
nothing says the curve is a window. Only one curve was drawn, although the
validation fit ran with an evaluation set; the history comes from the fit
result the training job keeps, and after a validation fit and a final refit
on all rows that appears to be the refit, which has no evaluation set. The
early stopping point, the reason the refit uses the tree count it does, is
therefore not visible either.

**Plan:** Bound the history by thinning, not by keeping the tail: keep the
first and last iterations, the best iteration and an even stride between
them, up to the limit. Show the validation fit's train and eval curves, with
the best iteration marked, when a validation fit ran, and say so in the tab's
intro ("Validation fit: 80,000 training, 20,000 validation rows"); a refit's
training curve can follow as its own series or a note. When the history was
thinned, the tab says how many iterations it spans.

**Acceptance:** A unit test thins a 1,000-row history to 200 rows that
include iteration 1, the last iteration and the best iteration; a training
test with holdout validation asserts the results carry the validation fit's
train and eval history; a `LossTab` component test renders both curves, the
best-iteration marker and the span note.

**Dependencies:** MDL-02 (the same prefixed rows).

**Evidence:** `src/haute/routes/_training_worker.py::_bounded_loss_history`;
`src/haute/modelling/_training_job.py`;
`frontend/src/panels/modelling/LossTab.tsx::LossTab`.

### MDL-04 — Value axes label a narrow range with the same number
**Why:** On that Loss tab every value-axis label read "107": the plotted range
was about 107.0 to 107.3, the axis has five linear ticks, and
`ChartValueGrid` labels every tick with `formatChartNumber`, which formats to
three significant figures whatever the spacing between ticks. Any chart built
on `ChartValueGrid` does the same whenever its range is narrow relative to
its magnitude: loss curves near convergence, relativities near 1.0, premiums
in the thousands with a small spread.

**Plan:** Format an axis's ticks together: choose the number of decimals from
the tick step (enough that neighbouring ticks differ), keep the compact
notation for large magnitudes, and use one precision for every label on the
axis. Keep `formatChartNumber` for single values.

**Acceptance:** A unit test formats the ticks of 107.0 to 107.3 as five
distinct labels, and of 0 to 1,250 and of 0.0001 to 0.0005 as they are
today; the Loss tab and one other `ChartValueGrid` chart get a component
test for distinct labels on a narrow range.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/chartHelpers.ts::formatChartNumber`;
`frontend/src/utils/chartHelpers.ts::chartTicks`;
`frontend/src/panels/modelling/ChartScaffold.tsx::ChartValueGrid`;
`frontend/src/panels/IterationLinesChart.tsx::IterationLinesChart`.

### MDL-05 — Training progress refreshes only every five seconds
**Why:** The status poller starts at 500 ms and doubles its interval after
every successful poll, not only after failures, up to 5 s. After four polls a
running training is checked every five seconds (the server log shows
`/api/modelling/train/status` every 5.0 s), so the bar and iteration count
jump: on film the count went from 9 to 63 in one step, and the estimated time
remaining moves in the same steps. The backoff suits a job that is quietly
running; it throws away progress that has changed.

**Plan:** Back off only while a job's status is unchanged, or after errors,
and return to the base interval when progress moves; cap a running job's
interval at about one second while its iteration count is advancing. The
optimiser and other callers of `JobPollingController` keep their behaviour
unless they opt in.

**Acceptance:** A controller test with a status whose iteration advances on
every poll asserts the interval stays at or below one second, and with an
unchanged status asserts it backs off to 5 s as today.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/jobPollingController.ts::JobPollingController`.

### MDL-06 — CatBoost's default encoding of small categorical columns is slow
**Why:** The demo model takes minutes to train: 134 s from Train Model to
results on a quiet machine (validation fit about 55 s, final fit about 45 s,
diagnostics about 30 s), and over five minutes when the machine was busy (the
validation fit reached iteration 700 after about 2.5 minutes). CatBoost itself
is the cost, not Haute: outside Haute, on the same data, the default model
fitted 759 trees in 207 s. Timing 100 iterations back to back on the same
data (80,000 training and 20,000 evaluation rows, depth 6, learning rate
0.05), with the machine busy so only the ratios matter:

| Variant | Seconds per 100 iterations |
|---|---:|
| Tweedie, defaults | 12.7 |
| RMSE, defaults | 8.9 |
| Tweedie, `border_count=32` | 9.4 |
| Tweedie, `one_hot_max_size=10` | 0.9 |
| Tweedie, categorical columns dropped | 0.8 |

Almost all of the time goes on CatBoost's target statistics for the three
small categorical columns (2, 3 and 7 levels); one-hot encoding them is about
14 times faster. Haute neither sets nor names `one_hot_max_size`, so an
analyst has no hint of the cause.

**Plan:** Decide between three options. (a) Default `one_hot_max_size` to a
small number (for example 10) for CatBoost when the user has not set it,
recorded in the run summary, knowing it changes the model for columns with
more levels than CatBoost's own default. (b) Name `one_hot_max_size` in the
Parameters tab with a short explanation, and leave the default alone. (c)
Show a hint in the Train tab when a categorical feature has few levels and the
fit is slow. Whichever is chosen, the run summary names the encoding in use.

**Acceptance:** Depends on the decision; at least, a test that the chosen
default or hint appears for a CatBoost model with a three-level categorical
feature, and a timing note in the modelling specification.

**Dependencies:** A decision on whether Haute may change CatBoost's default
encoding.

**Evidence:** `src/haute/modelling/_algorithms.py::_CatBoostProgressCallback`;
`src/haute/modelling/_descriptors.py`.

### MDL-07 — Summary leads with in-sample metrics after a refit
**Why:** After the validation fit and the refit on all 100,000 rows, the
Summary tab opens on "Diagnostics: Training · 100,000 rows · Training
diagnostics are in-sample performance.", "No test set was reserved for this
run." and a headline Gini of 0.8963 and Tweedie deviance of 0.0586, both
measured on the rows the model was fitted to. The out-of-sample numbers from
the validation fit are in the Candidate selection section further down the
tab. The labelling is honest, but the most prominent number on the screen,
and the one a reader would quote, is the in-sample one.

**Plan:** Decide whether, when a validation fit ran and no test set was
reserved, the Summary's headline cards show the validation fit's selection
metrics (labelled "Validation, 20,000 rows"), with the in-sample diagnostics
beneath them; or whether the headline keeps the diagnostics and the
selection metrics move up beside them.

**Acceptance:** A `SummaryTab` component test for a holdout-validated run
with a refit asserts the validation metrics are the first metric cards and
are labelled with their row count.

**Dependencies:** A product decision on which metrics lead.

**Evidence:** `frontend/src/panels/modelling/SummaryTab.tsx::SummaryTab`;
`src/haute/routes/_training_worker.py::_training_response_payload`.

### MDL-08 — A rebuilt frontend breaks panels in open pages
**Why:** `haute serve` serves the built bundle straight from
`src/haute/static`, and each build replaces the hashed chunk files. A page
opened before a rebuild still names the old chunks, so the first panel it
loads lazily afterwards fails: when the bundle was rebuilt at 17:54 during a
training run, the results workspace showed "Something went wrong. Failed to
fetch dynamically imported module:
http://127.0.0.1:8200/assets/ModellingPreview-DMczYKmb.js" (the new build's
chunk is `ModellingPreview-Dl1xg9Jf.js`), and "Try again" requests the same
missing file. Only a reload recovers, which loses nothing here but is not
offered. This mainly affects development and upgrading Haute in place with a
session open.

**Plan:** Catch a failed dynamic import (Vite's `vite:preloadError` event, or
the error boundary seeing a chunk-load failure) and replace "Try again" with
"Haute has been updated: reload", which reloads the page once state that
would be lost has been saved or confirmed.

**Acceptance:** A component test raises a chunk-load error inside the results
workspace and asserts the reload message and action; an end-to-end test
removes a lazily loaded chunk after page load and asserts the page offers a
reload rather than a dead retry.

**Dependencies:** None.

**Evidence:** `src/haute/server.py`; `frontend/src/panels/ModellingPreview.tsx`;
`frontend/vite.config.ts`.

### MDL-09 — Training results are lost when the server restarts
**Why:** Training results live in the server's in-memory `JobStore` (a
24-hour time to live). Restarting `haute serve` empties the Modelling results
panel until the model is retrained, which for the demo model is two to five
minutes, even though the trained model file and, if logged, its MLflow run
still exist. Nothing in the panel says the results existed and were lost.

**Plan:** Decide whether completed training results are persisted beside the
model artifact in the project cache and restored on load, or whether the
panel says "Results from the last run are not kept across restarts; retrain,
or open the logged MLflow run" when a node has a model file but no results.

**Acceptance:** Depends on the decision; either a restart test that restores
the Summary and Lift results, or a component test for the message.

**Dependencies:** A decision on persisting results.

**Evidence:** `src/haute/routes/_job_store.py::JobStore`;
`frontend/src/panels/modelling/useTrainedJobRestore.ts`.

### MDL-10 — The palette names two of the five model families
**Why:** The Model Training palette entry reads "Train a CatBoost or GLM
model". The node trains CatBoost, XGBoost, LightGBM, EBM, and GLMs through
RustyStats when it is installed, and tunes all but the GLM with Optuna.

**Plan:** Describe the node by what it does rather than listing engines, for
example "Train a model: gradient boosting, EBM or GLM", and keep the engine
list in the node's own Target tab where the algorithm is chosen.

**Acceptance:** The palette description no longer names only CatBoost and
GLM; a test holds the description to the families in the algorithm registry.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/nodeTypes.ts::NODE_TYPE_META`;
`src/haute/modelling/_algorithms.py::ALGORITHM_REGISTRY`.
