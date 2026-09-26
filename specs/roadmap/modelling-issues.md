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
| MDL-06 | Decision | P3 | A CatBoost model with small categorical columns does not train many times slower than it needs to. |
| MDL-07 | Decision | P3 | The Summary tab leads with the out-of-sample metrics when a validation fit ran. |
| MDL-08 | Planned | P3 | An open page recovers when the frontend it was served from has been rebuilt. |
| MDL-09 | Decision | P3 | A trained model's results survive a server restart, or the panel says why they are gone. |

## Planned improvements

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
