# 08. Red team: what is wrong with the three drafts

All inputs were present and read: the brief, `before/overview.md`, drafts 04 (workflow), 05 (analytics) and
06 (UI), reports 02 and 03, and `before/ui.md` for the owner's wording. Paths: `BE/` =
`/home/ralph/suite/haute/src/haute/`, `FE/` = `/home/ralph/suite/haute/frontend/src/`, `TB/` =
`/home/ralph/suite/t-boost/`, `RS/` = `/home/ralph/suite/rustystats/python/rustystats/`. "Checked" means I
opened the file. "My sum" is my own arithmetic, with its assumption. Severity: **blocker** (the design cannot
work until fixed), **major** (misleads the analyst or the owner), **minor**. I changed nothing outside the
team folder.

## The ten points that matter most

1. Nothing requires a stable row key. The fixed split, paired differences, kept predictions and the count of
   looks all need one (F1).
2. The spread across folds is used as a threshold, but is about a third of a 95% range (T2).
3. The "band" for the test score leaves out the test rows' own noise. It fires one time in six by chance (T1).
4. "Last logged" is the default reference, but the logged refit learned from today's validation rows (X3).
5. A thorough run buys no narrower range on a difference, yet the workflow leans on it for small data (X2).
6. Many things tested, three different guards. The UI's own header reads "3 clear, 3 by chance" (T3).
7. Two settled lines are at the edge and the UI assumes yes (S1). The GLM's default term drops the shared
   direction (S2).
8. A share of the span changes meaning when a stronger model arrives, and does not exist on test rows (X4).
9. "Like for like" mixes other rows with other settings, and greys out scores that are valid (X1).
10. Three drafts hold 1,532 lines, 31 questions and one priority list. The before files held 802 lines (§8).

## 1. Conflicts with a settled line

- **S1 (major). The chosen factor's block sits beside the chart.** Settled: "A factor's settings sit in the
  menu, not beside its chart." UI: "Each menu opens on the chosen factor", drawn as `veh_age  [x] in
  direction (-)  fixed value: none  [Try without]` at the top of the column next to the chart. It follows the
  chosen factor, so it works as an inspector beside the chart. Also settled: "The factor list on the left is
  for choosing a factor to look at, not for settings." UI: "`X`, `D`, `R` and `T` act on the All models menu
  from anywhere ... Otherwise the toast is the only sign." The draft knows the line: it lists the keys as a
  concern, and its question 1 asks about the block under a model type's menu. It does not ask about the block
  under All models, and it assumes yes for the keys. *Fix:* list both in Concerns and assume no until the
  owner rules. Fall back to the snapshot: "The factor chosen on the left is highlighted in [the table]".
- **S2 (major). The GLM's default term cannot hold the shared direction.** Settled: "Factors and monotonicity
  are shared by every model." Workflow: "each factor that is in gets a default term: its levels for a
  category, and a smooth curve for a number, or its levels when it has few." Checked: only `linear`, `bs`,
  `ms` and `expression` take `monotonicity`; `categorical` and `ns` do not
  (`BE/modelling/_glm_terms.py:52-59`). So the workflow's "a term can hold a direction" is true of four term
  types in eight. NCD years and vehicle age have few values, so they default to levels and lose their
  direction on the first fit. *Fix:* a factor with a direction defaults to `ms`.
- **S3 (major). "Laid on top" for everything goes below the settled line.** Settled: "the rest of the model
  is refitted around it where the model type allows." Workflow: "edits start laid on top of the fit, apply at
  once with no training", for every model type. But a whole table can be fixed through the offset today:
  t-boost joins an offset to the exposure (`TB/python/t_boost/sklearn.py:3606-3614`, checked) and the GLM
  takes both (`RS/formula.py:5582-5583`, checked). What is missing is a second offset in Haute
  (`BE/modelling/_algorithm_base.py:71`), which is Haute work, not library work. *Fix:* laid on top for part
  of a table; refit through the offset for a whole table, as the first target. A value the business fixes for
  a whole factor can then be honoured by all six model types (03 §5.2), so W9 need not grey any row.
- **S4 (major). Every new chart would be built twice.** Settled: "The code stays clean and modular, with as
  little duplication as possible." Workflow: "The model card is complete without the tool." The card draws
  its own charts in Python (`BE/modelling/_charts.py:260-828`, checked). Whiskers, many models, effects and
  "refit against reviewed" would each be drawn in two languages. No draft says so. *Fix:* decide once: the
  card is built from the same chart code, or its new content is tables and text.
- **S5 (minor). Two lines stretched without a note.** Settled: "Training a model again overwrites its run,
  unless the run is kept as a reference model." Workflow: "a what-if run trains beside the model's run, and
  nothing goes out of date": a third kind of run. Settled: "Feature engineering stays in the pre-processing
  nodes." Workflow: "Grouping them inside one GLM term is that GLM's own setting", plus a new banded term.
  *Fix:* list both in Concerns. A what-if ends at Apply, Keep or Drop. What only the GLM sees is a term.
- No draft designs clustering or ensembles. Keep "what is this model missing" a diagnostic, never logged.

## 2. Conflicts between the drafts

- **X1 (major). "Like for like" means two things.** Workflow W6: compare "only on rows neither run learned
  from". Workflow W8 and the UI: "not honoured and so greyed as not like for like". Other rows make a
  difference meaningless. A substitute loss or an ignored direction does not: the run is still scored on the
  same rows with the same deviance (analytics 0.1 says this of the Tweedie power). The UI row "! CatBoost  not
  like for like: trained on Tweedie, not Gamma" shows no score, which weakens the settled "the models are
  compared". *Fix:* two marks. "Other rows": no difference shown. "Ignores: direction on veh_age": scored,
  compared and marked.
- **X2 (major). What a thorough run buys.** Workflow: small data is "thorough by default, each change read
  against its range", and a what-if "trains on the same rows and depth". Analytics 2.3: "The headline score
  stays on the validation rows for every run"; thorough adds only a fold vote. So on small data each what-if
  costs five fits and its range is no narrower. In the small example, 15,000 claims leave about 2,400 on
  validation rows if the test rows take a fifth (my sum). "Four factors are beyond their range" is unlikely
  there. *Fix:* pick one. (a) A what-if is always one fit, and thorough is for the stress step. (b) At
  thorough depth the paired difference uses every out-of-fold row, labelled, with the Nadeau and Bengio
  correction (in 02's list), because folds share rows.
- **X3 (major). Which predictions stand for "Last logged"?** UI: the default reference is "the last logged
  run", on "validation rows . 103,100 rows". Analytics 1.4: a logged run "was refitted on rows that may be
  validation rows today". Workflow refresh 3: "the logged model scores the node's current rows". So the
  default comparison is against a flattered reference, or on almost no rows. *Fix:* the reference is the
  logged run's reviewed fit, through its kept validation predictions. The refit is used only on rows it never
  saw.
- **X4 (major). The unit of a score.** Analytics 1.3: "a share of the span from the floor to the strongest
  model". UI: chips "96%", the card "gain 3.4", logging "expect a gain of 3.0 to 4.6". The span moves when a
  stronger model arrives, so a number kept in History, on the card or in MLflow changes meaning. The test rows
  have no span: the strongest model is never scored there. *Fix:* anything recorded uses the gain as a percent
  of the floor's deviance. The span is for the live bar only.
- **X5 (major). "Simpler wins" has nothing behind it.** Workflow: "Between tied models the simpler one wins."
  Analytics: "size does not compare across model types." The UI's standings dropped the size and time columns
  the snapshot had. *Fix:* show size within a model type. Across types the tool states the tie and the analyst
  picks.
- **X6 (major). "Refits at once" against one job at a time.** Workflow W15 and the UI ("It refits by itself")
  assume a free worker. One training runs at a time, server-wide (`BE/routes/_training_lifecycle.py:1338-1341`,
  checked). A three-second GLM refit waits behind a four-minute t-boost. "Jump" moves a what-if up the queue;
  it does not stop the running job. *Fix:* a second light worker for short fits, or the screen says "waits".
- **X7 (minor). Three words mean several things.** Range: on a difference, on actual, a spread across folds,
  a tuning range, "a range of values" in an edit. Band: a cut of a number, t-boost's step, a lift group, a
  ribbon, "the band the score should fall in". Reference: settled as a kept run, but in the UI whatever `vs`
  picks. *Fix:* range = a 95% range. Spread = across folds. Band = a cut of a number. The `vs` pick is
  "compared with". Yardsticks are reference models in every draft, or in none.
- **X8 (minor). Two misfit measures, two check lists.** Workflow: "share of misfit beyond its range", and
  about twenty checks. Analytics 3.2: the gain from corrections learned on other rows, and nine checks. "A
  group on both sides of the split" cannot happen under the drafts' own split. *Fix:* analytics owns both.
- **X9 (minor). Three loose ends.** The effect by year as a what-if works for the GLM and for EBM, not for
  t-boost or the gradient-boosting types, which cannot be told to interact one factor with year. The reviewed
  tick is per factor, with no model or run. A what-if result has "three homes" (the UI's words), though U1
  says one home each. *Fix:* say which model types take the what-if. The tick records its model and run. A
  what-if lives in the standings; its line is drawn only while it is chosen.

## 3. Statistics

- **T1 (major). The test "band" is not a valid range.** Workflow: "the band the score should fall in, from
  the validation score and its range". Analytics 2.4: "whether the test gain sits inside the range of the
  validation gain". That range holds only the validation rows' noise. The test rows have their own. With
  equal row sets and nothing changed, the test gain falls outside about one time in six (my sum: two
  independent estimates differ by more than 1.96 standard errors of one with probability 17%). Fewer test rows
  make it worse. The refit, a later period and a picked model each push the score lower. The analyst is then
  told to hunt for a cause. *Fix:* take the width from both row sets. The test rows' noise can be sized from
  their exposure, without their actuals. Say it leaves out drift and selection.
- **T2 (major). The spread across folds is about a third of a 95% range.** R-spread is the lowest and highest
  of five fold models. They share three quarters of their rows, so they move together. By the grouped
  jackknife, variance = (K − 1)/K × the sum of squared deviations, so the standard error is about 1.8 times
  the standard deviation across five folds (my sum, checked by hand for a mean; Wolter 2007, Introduction to
  Variance Estimation, ch. 4, cited from memory). A 95% range is then about ±3.5 of those. Lowest to highest
  spans about ±1.2. The label "spread" is honest. Its uses are not: "moves by more than its R-spread" (2.5,
  7.2), "'Inside the range' means the data cannot tell the two apart" (7.1), and the workflow's "flagged when
  beyond its range". Those flags fire about three times too easily. Beside a GLM's 95% interval, a t-boost
  line looks three times surer than it is. *Fix:* scale the spread into an approximate range, or never use it
  as a threshold. Under a time split the folds are not alike, and no scaling applies.
- **T3 (major). Many tests, three guards.** Lists state a chance count (3.1, 3.2, 4.7). The loop only warns
  ("forty comparisons at 95% will show about two"). "Each factor left out in turn" is read "with the false
  discoveries controlled". The UI's own header reads "3 clear, 3 by chance", which tells the analyst the
  worklist is noise. The count is also too high: only the misfit side counts, so sixty factors give 1.5, not
  three (my sum). *Fix:* one rule for every list: control the share of false flags (Benjamini and Hochberg
  1995, JRSS B 57, 289-300), and call "clear" only what survives.
- **T4 (major). Test rows can leak through a change of split.** "Setup was wrong. Change it: all models go
  out of date." Nothing says what happens to the count of looks. Unseal, dislike the score, change the split:
  the new test rows were training or validation rows for every choice so far, and the count reads zero.
  *Fix:* the record of looks belongs to the node's rows, not to a split or a run. The card says how many of
  today's test rows were seen before, and as what.
- **T5 (minor). Three smaller looks at the test rows.** "Rows (rows, exposure and claims per set" shows the
  test rows' claims, so their level: show rows and exposure only. `[Unseal and score]` comes before `[Log to
  MLflow]`, so the score is a gate: say so. "Change over time" compares the earliest and latest periods, yet
  "none uses the test rows": the mix of factors there spends nothing, so allow it.
- **T6 (major). The look before fitting spends the validation rows.** UI state 2: "actual and exposure, on
  validation rows". The workflow has the analyst leave factors out and set directions from it. That is a
  fifth of the data, and choices made on the rows that judge. *Fix:* chart the training rows until a model is
  fitted. "Strength on its own" (3.3) already does this properly.
- **T7 (major). "What the data can show" is not a property of the data.** UI: "Gaps under 1.5 cannot be
  seen". The width of a paired range depends on how differently two runs predict. Two model types need a wide
  gap. A what-if that changes one factor is nearly the same model, so a gap of 0.2 can be clear, in the same
  table. *Fix:* say "between two model types", or drop the line.
- **T8 (major). A "main effect" is not one thing across model types.** t-boost's tables are purified
  (`TB/docs/model-analysis/contributions.md:30`, checked), so its main table is an average. A GLM's main
  coefficients, with an interaction in the model, are the effect at the partner's base level. Overlaid, they
  show a gap that is not real. The guard "the legend says which factors each model uses" is empty, because
  factors are shared. *Fix:* for every exact model type the effect is the exposure-weighted average over the
  partner's levels. The legend says which interactions each model holds.
- **T9 (major). A saved filter becomes a segment "named in advance".** The row filter applies "to every
  pane", standings included, and the UI offers `Save as a segment`. Analytics 4.7 trusts named segments
  because they are not a search. *Fix:* a score under a hand filter is labelled "after looking". A segment
  saved from a filter is trusted from the next data refresh.
- **T10 (minor). "Off level" is the wrong name.** It is the model against its own recalibration, and can be
  large with balance at 1.00: too high at the top, too low at the bottom. "The first says fix the level" is
  then wrong. *Fix:* "off across predictions".
- **T11 (minor). The range on actual changes with the chosen model.** The dispersion comes "from the chosen
  model", so whiskers and marked levels move when another chip is clicked. *Fix:* one per node and data version.
- **T12 (minor). "It survives heavy tails" overstates.** A percentile resample is too narrow when a few
  claims carry the sum: the small severity case. *Fix:* say so, and show the largest claim's share beside it.
- **T13 (minor). The inner slice tilts the comparison.** Gradient-boosting types fit on 85% of the training
  rows, the GLM and EBM on all. *Fix:* refit on all training rows at the stopped round, or mark the row.
- **T14 (minor). Two labels are wrong.** 7.4 "without training" needs an effect fitted without the direction.
  "What is this model missing" searches where predictions are in-sample, so it under-finds for boosted types.
  *Fix:* label 7.4 as needing a what-if run. After a thorough run, search on the out-of-fold predictions.

## 4. Claims about Haute

**Borne out** (checked; those cited in a finding are not repeated): a split is random, group or temporal, with
a test fraction or a test date (`BE/modelling/_evaluation.py:110-114`, `:166-174`); LightGBM stops on the
validation rows (`BE/modelling/_lightgbm.py:262-263`, `:306-307`); t-boost ignores them and takes groups
(`BE/modelling/_tboost.py:596-602`); today's "double lift" is one model in equal row counts
(`BE/modelling/_metrics.py:406-437`); a sample uses seed 42 (`BE/routes/_training_preparation.py:90-106`);
runs are in memory (`BE/routes/_job_store.py:1-4`); the pruning path (`TB/python/t_boost/sklearn.py:4108`);
rustystats' switches, HC0 to HC3 and no errors grouped by policy (`RS/diagnostics/api.py:2154-2165`,
`RS/formula.py:826`); 400px and 180px (`FE/modelling/ModellingView.tsx:397`, `FE/haute-ui/palette.css:12`).

**Not borne out, or stretched** (with S2 above):
- **H1 (minor). Bare letter keys are new.** UI: "Letter keys act when focus is not in a text field
  (`hooks/keyboardTargets.ts`)." The helper exists. But every binding today needs Ctrl or Cmd, bar Delete,
  Backspace, Escape and `?` (`FE/hooks/useKeyboardShortcuts.ts:36-112`), and the view's handler returns unless
  a modifier is held (`FE/modelling/ModellingView.tsx:195`). Ctrl+Enter means "Refresh the open preview" in
  the editor (`useKeyboardShortcuts.ts:92-94`). *Fix:* say the keys are a new idiom, and give Train its own.
- **H2 (minor). The GLM's drop test.** Analytics 6.2 lists "a test for dropping a term (the rise in deviance,
  as an F test...)". I found Wald tests by factor and by smooth term (`RS/diagnostics/factors.py:7`,
  `RS/diagnostics/api.py:83`) and no deviance drop test (by grep, so unverified). *Fix:* use the Wald test.
- **H3 (minor). Two claims with no path.** Workflow: "The Assistant panel works through the same settings and
  runs." The view mounts it (`FE/modelling/ModellingView.tsx:137-143`); the rest is unverified. UI: "The GLM's
  table takes the same shape": today its tabs are per coefficient (03 §1), and Haute does not call rustystats'
  `to_rate_tables` (`RS/rate_tables.py:20`; grep of `BE/`). *Fix:* mark the first unverified. Build the GLM's
  table from `to_rate_tables`, and size it as new work.

## 5. Infeasible, or dearer than implied

- **F1 (blocker). Nothing requires a row key.** Haute has an optional `id_columns` setting: "Columns
  identifying rows, kept with the predictions but never trained on" (`BE/_node_config_models.py:871`,
  checked). The canonical order sorts by it first, then "by every other column in the frame"
  (`BE/modelling/_row_order.py:9-10`, checked). With none set, the order moves when a column changes. I found
  no check in that file that an id is unique. Workflow: "The split stays put" after a pre-processing change.
  Analytics leaves the id as Q4, with "single rows when there is none". *Fix:* Setup asks for `id_columns` and
  checks it is unique. Without one the tool says what is lost: the split is redrawn when the data changes,
  earlier runs cannot be compared, and the count of looks restarts and says so.
- **F2 (major). The seconds loop.** "Refits the GLM at once", "five folds of any model in under 10 seconds".
  Today each training builds its frame from the pipeline, writes a temporary file and starts a worker process
  (03 §3). No timing here was measured. *Fix:* name what must exist first: the node's frame cached per data
  version, and a warm worker. Time one small fit before promising seconds.
- **F3 (major). The sum is a programme, and only one draft ranks it.** 03 sizes items 1, 4, 7, 9 and 16 at
  several weeks each, and 18 as a project. Analytics' "first version" needs most of them. Workflow and UI give
  no order. *Fix:* one shared order. Suggested: (1) the run store, the row key, the fixed split and kept
  predictions; (2) several model types, the factor chart with ranges, standings with paired differences; (3)
  what-if runs and history; (4) hand edits; (5) the rest.
- **F4 (major). Unasked work on one worker.** Yardsticks retrain on each shared change, the final refit
  starts on "Pick the run", the GLM refits by itself, and a batch is one training per factor. In the large
  example one shared change is about six minutes before the standings agree again (my sum from the draft's
  timings). *Fix:* yardsticks retrain when asked or at the stress step, and show their state.
- **F5 (major). Hand edits to a GLM curve.** UI: "one editor serves t-boost, EBM and the GLM", with a
  relativity by band for a spline. A spline is several coefficients and has no cell to edit (03 §5.3). *Fix:*
  a GLM edit applies to a term with levels. To edit a curve, fix the whole factor through the offset.
- **F6 (minor). Replaying t-boost's choices.** t-boost takes a tolerance or a fraction, not a point
  (`TB/python/t_boost/sklearn.py:1888-1889`, checked), and Haute passes neither
  (`BE/modelling/_descriptors.py:736-742`). *Fix:* record the rule, not the point on one fit's path.
- **F7 (minor). Three smaller costs.** "Train rebuilds every run and reference model" is not exact across
  machines, and not possible after a data change. A logged run that "carries its kept predictions" holds 8
  bytes per row per column (03 §5.4) of row-level data. Scoring a logged model fails once its columns change.
  *Fix:* say "close, not identical". Ask the owner before predictions go to MLflow. Say what a refresh does
  when the logged model cannot score today's columns.

## 6. Complexity that does not pay its way: cut in this order

1. **The fold vote** (1.3). It passes by chance three times in eight (my sum, folds taken as independent),
   recounts fold 1, and makes thorough pairs stricter than quick ones.
2. **"Off level" and "ranks risks" as standings columns.** Two new terms and a recalibration per run.
   Balance, and the calibration curve on demand, say it. Analytics itself lists them under "Next".
3. **The benchmark.** Two kinds, a rescale, a refusal, a new stroke, and a chart "from my memory of the
   paper". The last logged run already answers "better than today?".
4. **Most keys.** Keep Up, Down, `/`, `R`, `E`, `V` and one for Train. `X` and `D` put every model out of
   date on one stray press, and sit at the settled edge (S1).
5. **Half the checks.** Drop "over half of the importance in every model" (importance is a different measure
   per model type, 03 §2c), the 0.25 threshold ("conventions, not theory"), "a score far above the last logged
   run", and run states. 02-B5 warns that long lists get ignored.
6. **The overloaded strokes.** Dotted means four things. Half strength means out of date, and also "not
   chosen". An analyst cannot read a line that has two meanings.
7. **Three of the four interaction devices.** Keep the pairs t-boost kept and the two-way grid. "What is this
   model missing", "where and why" and the pair search answer the same question at a training each.
8. **State 10's three breakpoints.** Pixel rules for a screen not yet built. One rule is enough: the settings
   column folds.
9. **"What the data can show"** (wrong as stated, T7) and **"No factors" as a trained run** (it is one rate).
10. **The file beside the config** for ticks and acknowledged checks. A second file to keep in step with the
    config. Keep them local until logging, then in the card.

## 7. Missing

- **M1 (major). Claims still developing.** The latest period is the least developed, and the drafts make it
  the test rows. Only 3.5 mentions it. *Fix:* Setup takes a "claims are mature up to" date, and the test
  period ends there. Failing that, a plain warning beside the test score.
- **M2 (major). Frequency and severity together.** Each node is "judged alone" (analytics Q6). An analyst
  prices the product, and an edit in one node is not offset in the other (02-A4). *Fix:* a later view that
  multiplies two nodes' kept predictions and checks the product against the pure premium.
- **M3 (minor). Level order, and factors with thousands of levels.** No draft orders an unordered category by
  exposure or by actual, though 02-A2 found that pricing tools do. A postcode in a table editor is not drawn.
  *Fix:* an order control on the chart, and a rule for the table: the top levels by exposure, then a search.
- **M4 (major). Editing an interaction table.** The demo uses `max_interaction_order: 3`
  (`/home/ralph/suite/haute-modelling-demo/rating/config/model_training/modelling.json`, checked). State 5a
  shows a tab for a pair table and stops. *Fix:* say whether a pair table's cell can be edited, and what a
  main-table edit means while the pair tables stay.
- **M5 (minor). Targets outside the four.** The demo's target is `avg_cheapest_5` on Gamma, with no claim
  count (same file). By its name it is a price: I infer that. RMSE and MAE are offered losses
  (`FE/panels/modelling/algorithmCapabilities.json`, checked). Analytics 0.1 has no row for an amount per row,
  or for squared error. *Fix:* add both rows, with an event counted as a row.
- **M6 (minor). Three gaps in the journey.** Opening a node made the old way: one `algorithm` and validation
  "none", as the demo is. What each model type predicts for an unseen or missing value. The run store's
  housekeeping: disk use, and when what-if runs and fold predictions go. *Fix:* a short paragraph for each.
- **M7 (minor). No decision on 02-A1,** the researcher's first pattern: actual, expected and effect on one
  chart for the chosen model. The UI keeps two views and does not say why. *Fix:* adopt it or say why not.

## 8. Loose and readable, or a specification?

- **Length.** Before: 802 lines in three files. Now: 1,532 in three drafts, with no overview yet. The brief
  says "sharper, not longer".
- **Workflow.** The first 277 lines are loose, and read in the owner's voice. The next 280 are lists for the
  team: 43 needs, 17 proposals, 13 questions, a cross-reference. Keep those out of the final.
- **Analytics.** A specification: formulas, 2,000 buckets, 2,000 resamples, 15%, 0.8, 0.25, 0.5%, 20 events.
  It has no home among the three files. *Fix:* one page of rules in the owner's words, in the overview's
  "Honest evaluation". The methods go in an appendix marked as reference.
- **UI.** The wireframes are what "hone" asked for. The specification parts are the 26-row "Where everything
  lives" table with its codes, the file paths, 22 key bindings, hex colours and pixel breakpoints.
- **Words.** About twenty new ones beside the owner's list, among them: span, floor, yardstick, standings,
  readout, notice line, figures line, chip, mass, bucket, inner slice, reviewed fit, R-diff, R-level,
  R-spread, tied. "Mass" stands where analysts say exposure. The inner slice is a fourth row set: say "rows
  held back from the training rows", as the workflow does.
- **Questions.** 31 across the three. Merge them, and keep the ten that change the design.

## Concerns about settled items

I raise none of my own. I back two already raised. **"Training a model again overwrites its run"**: holding
the one replaced run until the next training (04, 05) is the smallest fix for "what changed" and for undo.
**"Factors and monotonicity are shared by every model"**: letting a model type leave a shared factor unused
(04) is the smallest fix for a GLM built forward. S1 and S3 are for the owner to rule on.

## Verdicts

**Workflow (04).**
- Strengths: Try then Apply, with the run overwritten only when a training finishes. The logging and refresh
  sections: refit against reviewed, the expected result stated before unsealing, the old model judged first.
  The You / Unasked / Suggests voice, with honest "Today" lines.
- Weaknesses: every stopping rule rests on "its range", which it never pins down (X2, X7, T2). Half the file
  is appendix and nothing is ranked. It piles unasked work on one worker (F4, X6).
- The one change: rewrite "Knowing when to stop" and the small example against what the analytics can deliver
  on a fifth of the rows.

**Analytics (05).**
- Strengths: the range on the paired difference, by whole groups. Honesty about what a thorough run is not,
  with the inner slice and the sealed test rows. Each analysis says how it misleads, and it is the only draft
  with priorities.
- Weaknesses: the fold spread as a threshold, and the test band (T1, T2). Many tests under three guards, and
  a unit that moves (T3, X4). It reads as a specification, in new words.
- The one change: two ranges only, a 95% range and a fold spread scaled to match, with one rule for many
  tests, all on one page in the owner's words.

**UI (06).**
- Strengths: three choices with one home each, and the menus as two tabs, which keeps the settled menu line.
  Ten states, with empty and failed ones. Every path I opened checks out, and the colour test admits it has
  no safe sixth colour.
- Weaknesses: it placed everything the other drafts asked for and cut nothing, so the main state is not
  "quiet by default". It sits at the edge of two settled lines and assumes yes for the keys (S1). Its
  standings show units no one has defined, and no size (X4, X5).
- The one change: redraw state 3 with first-version items only, and move the rest behind "on demand".
