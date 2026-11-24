# Analytics

What the Modelling UI can tell an analyst, and how each thing is worked out. Loose, and proposed
unless `overview.md` lists it as settled. The rules come first, then each analysis: its question,
its method and how it misleads. The screen is in `ui.md`, and the steps are in `workflow.md`.

*Today* marks what Haute does now, with a path from the haute repo's root, or from a
sibling repo where the path starts with `t-boost/` or `rustystats/`. A claim marked "not
rechecked" comes from the team's code survey. Sources are named in brackets and listed last.

## The rules

Settled: "Any model type can be trained on the node's data, and the models are compared." These
rules are what make that comparison fair.

**Targets.** Setup names one of five. Each fixes what "exposure" and "claims" mean in this file. For
the last two a "claim" is a yes, or a row.

| Target | Actual | Exposure | Main score |
|---|---|---|---|
| Claim counts | claims | exposure | Poisson deviance |
| Claim sizes | claim amount | number of claims | Gamma deviance |
| Pure premium | claim amount | exposure | Tweedie deviance, one power for every model |
| Yes/no | 0 or 1 | one per row | binomial deviance |
| Another amount per row | the amount | one per row | the deviance of its loss, or squared error |

- **The main score** is the mean deviance of the shared loss, weighted by exposure. It matches a
  model of the mean, which Gini does not (Fissler and others).
- **The floor** is the model with no factors: one rate, taken from the training rows.
- **Gain** is how far a model beats the floor, as a percent of the floor's deviance. It is the one
  unit wherever a score is shown or recorded. It looks small for claim counts, where most of the
  floor's deviance is chance. So a bar from the floor to the strongest model sits beside it, drawn
  live and never recorded: it moves when a stronger model arrives.

**Which rows.**
- **The split is made once per node,** against an id column, so nothing moves a row. Setup asks for
  the id and checks it is unique. Without one the tool says what is lost: the split is redrawn when
  the data changes, earlier runs cannot be compared, and the record of looks restarts. *Today:* the
  id is optional (`src/haute/_node_config_models.py:871`), and without it a random split
  depends on every column (`src/haute/modelling/_row_order.py:9-10`).
- **Test rows** are the latest mature period, and **validation rows** are whole policies, a fifth of
  the rest. *Today:* a split is random, by group or by time, never two at once
  (`src/haute/modelling/_evaluation.py:110-114`).
- **Scores, differences and charts use the validation rows,** predicted by a fit that learned from
  the training rows only: the **reviewed fit**. Before any model is fitted the charts use the
  training rows, so looking at the data spends no validation rows.
- **A model learns only from the training rows.** Early stopping and tuning use rows held back from
  them. Once the round is chosen the model is fitted again on all of them, so every model type
  trains on the same rows. *Today:* LightGBM, XGBoost and CatBoost stop on the validation rows
  (`src/haute/modelling/_lightgbm.py:262-263`, `src/haute/modelling/_xgboost.py:388-390`, `src/haute/modelling/_algorithms.py:557-558`).
- **Test rows are sealed:** no chart, score, check or band uses their actuals. **One in-sample
  mark** goes on anything worked out on rows a model learned from.
- **A run keeps its predictions,** and the shared charts are computed from them on demand. *Today:*
  they are not kept (not rechecked).

**The range belongs on the difference between two runs.** One score's range is mostly noise no model
can remove, so two such ranges overlap even when one model wins on nearly every policy.
- Per row: one run's deviance minus the other's, on the same validation rows. Whole policies are
  resampled, since one policy's rows move together, and the middle 95% is the range (Fissler and
  others test the same paired differences).
- It covers the luck of which rows are validation rows, not of the training rows or the seed. A few
  large claims make it too narrow: for amounts, see the residuals view.
- **Clear:** both runs are scored on the same rows, the range excludes zero, and the difference
  survives the rule for many tests. Otherwise the tool says "no clear difference", with the raw
  number on hover. Models with no clear difference from the best are bracketed in the standings.
- **Many tests, one rule.** A list tests many things at once: factors, levels, segments, a batch of
  what-if runs. In every list the tool controls the share of false flags (Benjamini and Hochberg)
  and calls clear only what survives. The loop is not a list: forty changes tried in turn will show
  a couple of false gains, and only the sealed test rows check that.

**Runs that are not alike** carry one of two marks.
- **Other rows:** one run learned from rows the other is judged on. The difference uses only rows
  neither learned from, with the count, or it is not shown.
- **Ignores:** a run could not honour a setting it was given, shared or in its model's own menu, as
  in "ignores: direction on veh_age" for a GLM whose term cannot hold the direction set in its
  factors. It is still scored and compared, on the same rows. Two models with different factors or
  directions carry no mark: that difference is theirs to have.
- A sample run samples the training rows only and scores every validation row, so it compares with a
  full run. *Today:* a sample is a uniform draw (`src/haute/routes/_training_preparation.py:90-106`).
- **The last logged run** is compared as its reviewed fit, from its kept validation predictions. The
  logged model is a refit that learned from those rows, so it is compared only on rows it never saw.

**The thorough evaluation** is five folds over the training and validation rows, by whole policies.
The validation rows are fold 1, so it adds four fits and changes no score.
- It buys a prediction for every row from a fit that never saw it, and the spread of each effect
  across the folds. A model's misfit then uses every such row. So does the fit view, once every
  model drawn has them, so that actual is still drawn once.
- It buys no narrower range: scores and differences stay on the validation rows, for a what-if run
  too. Nor is it a second untouched score: the analyst chose on those rows (Goldburd and others).
- **The spread across folds** is the lowest and highest of the five, labelled as a spread and never
  used as a threshold. Fold models share most of their rows, so it is much narrower than a 95%
  range would be.

## By factor

Settled: "Clicking a factor charts actual against expected for every fitted model." Settled: "A
factor's chart shows for every factor, even the ones a model did not fit."

**Fit: is each model right at each level?** Per level, actual with its range and each model's
expected, on the validation rows, or on every out-of-fold row once every model drawn has them.
- The range on actual comes from the loss: the claim count, and for amounts their spread too. Its
  dispersion is set once per node, so the whiskers do not move with the chosen model. A level is
  marked only where the chosen model's expected is clearly outside the range.
- A number factor is cut into bands once per node, of equal exposure, so every run is charted on the
  same bands. One with up to 20 values gets one bar per value. Bands are closed on the right, as in
  Banding and t-boost. Today's code cuts on row count, per run; the fixes are in
  `factor-axis-2026-11-23.md`, section 7.
- **A factor the model left out charts too.** At training time each run also bins actual and its
  prediction by every column it did not use, on the same rows and bands as the other factor charts.
  It needs no kept predictions (Ralph's choice, 20 November). The chart says the factor is not in
  the model, and shows what the model misses on that column. It has no effect line.
- Misleads: one large claim can make a level, so hover gives its share.

**Misfit: which factor next?** It is the gain a factor still offers.
- Split the policies into two halves. In each, learn per level the correction actual over expected,
  pulled towards 1 where the level is thin. Apply each half's corrections to the other half. The
  misfit is the gain this adds, with the range on a difference.
- Under a perfect fit it is zero whatever the number of levels, since a correction learned on other
  rows cannot help. A "worst level" measure picks the thinnest level instead.
- The factor list sorts by it, left-out factors included, which is how a missed factor reaches the
  top. Factors with no clear misfit are greyed and last. Misfits of correlated factors overlap.

**Strength on its own: what does a column hold, before any model?** The rate per level on the
training rows, pulled towards the overall rate where thin, is scored on the validation rows. Its
gain is the strength. An identifier scores nothing, because it is scored on other rows. It sorts the
list before any fit and drives the leak check.

**Effect: what is the model doing with this factor?**
- One shape for every model type: the factor's contribution on the link scale, centred on its
  exposure-weighted average, so that an average level reads 1. It multiplies the rate under a log link and the odds under a logit
  link, and the axis says which. One centring removes the false gap between a GLM on a base level
  and a t-boost table on its average.
- **In an interaction** it is the average over the partner's levels, weighted by exposure. t-boost's
  main tables are that already (`t-boost/docs/model-analysis/contributions.md:30`). A GLM's main
  coefficients are the effect at the partner's base level, so they are averaged first. Whether EBM's
  pair terms average out the same way is unverified.
- Exact for t-boost, the GLM and EBM. Estimated for the gradient-boosting types, by accumulated
  local effects (Apley and Zhu), and labelled so. Partial dependence leaves the shared charts: it
  predicts at combinations that do not occur.
- Around it is drawn the spread across folds, for every model type, so the lines compare. The GLM's
  own interval stays in its Terms pane.
- **On the fit view the chosen model's effect is a thin third line,** as Emblem, Dataiku and
  `model-diagnostics` draw it. Where expected follows actual and the effect is flat, a correlated
  factor carries the fit. The effect view overlays the effect of every model that uses the factor.
- Misleads: two factors with the same information split one effect between them arbitrarily.

**Pairs, and over time: is the model wrong in the cells of two factors?** The pairs are not searched
for: they are the interactions t-boost kept and the pairs EBM chose. On half the policies learn a
correction per margin, then per cell, and apply them to the other half. The evidence is the gain
from the cells beyond the margins, and the picture is a two-way grid of actual over expected. With
the period as the partner it says whether a pattern lasts. Its picture is the fit view split by
period, each period centred on its own total, as the latest claims are still developing. The effect
by period is a what-if run with the factor interacted with the period, for the GLM and EBM only. A
model per year is not offered: correlated factors trade off between the fits (Anderson and others).

## By model

**The standings.** One row per model and reference model: gain, its difference from the `vs` run
(the run picked to compare with) and the range, balance, size, time to train and the run's labels.
The bar runs to the strongest model so far, which is no ceiling. Size (tables, terms or trees)
compares only within a model type. Across types the tool states "no clear difference" and the
analyst picks. A leak lifts every model, and the bar still looks healthy.

**Balance and calibration: is the level right?** Balance is total expected over total actual with
its range, overall and by period. Across a model's own predictions it is an isotonic curve of actual
on predicted, which needs no bins (Dimitriadis and others). Boosted model types can lose balance
(Denuit and others), so their menu offers `Rebalance`: one scale factor from the training rows.

**Lift.** Validation rows sorted by predicted rate, in 10 groups of equal exposure: actual with its
range, and expected. *Today:* the groups hold equal row counts and this one-model chart is named
double lift (`src/haute/modelling/_metrics.py:406-437`).

**Double lift: where two runs disagree, which is closer to actual?** Rows sorted by the ratio of run
A's prediction to run B's, in 10 groups of equal exposure: A, B and actual, each over its overall
average (Goldburd and others). Beside it, **how far predictions move**: the percentiles of the
ratio, and the share of exposure moving by more than 5%, overall and by segment. That measures
movement, never fit. When nearly every row is within 2%, the tool says "these two runs price alike".

**Where and why two runs disagree.** *Where:* the factors whose mix in the end groups of the double
lift is furthest from their mix in all rows (the pattern is Uber's Manifold). *Why:* for t-boost,
the GLM and EBM, the log of the ratio of two predictions is exactly the sum over factors of the
difference in their contributions. Averaged over the rows in view it is one bar per table (a factor, or a pair), and
answers "why does the new model charge this segment less". For gradient boosting it uses SHAP and is
labelled estimated. Neither costs a training.

**By segment.** Per segment: its share of exposure, each model's actual over expected with its
range, and the difference from the `vs` run inside it. Segments are named in advance and kept in the
config, so they are not a search (Cabrera and others). A score under a row filter made by hand is
labelled "after looking". A segment saved from a filter is trusted from the next refresh.

**Gini, MAE, residuals.** Gini only ranks, and is sound only between models that are right in level
(Wüthrich). It is shown second and never alone. MAE is not offered for choosing a model: the median
claim count is zero. Residuals are always binned by prediction (Goldburd and others). For amounts,
the ten rows that add most to the deviance are listed.

## Checks

Each check has a rule, a status (clear, to look at, not run) and a link to its fix. Checks never
block training. One can be acknowledged with a note, and reopens when its evidence changes. The list
is short, and silent when all is clear, because long lists get ignored.

| When | Check | Rule |
|---|---|---|
| Before fitting | A likely leak | a strength on its own several times the next strongest |
| | An identifier or a constant | most exposure in tiny levels, or nearly all in one level |
| | Thin and unseen levels | levels under 20 claims; validation levels not seen in training |
| | The same information | two factors very strongly associated, or one fixes the other |
| | Setup | a count with no exposure; exposure at or below zero; an id that repeats |
| | Change over time | new and vanished levels; the factors whose mix moved most |
| After fitting | Direction not kept | the effect or the expected line breaks a set direction |
| | Overfitting | gain on training rows over gain on validation rows, by level |
| | Level off, or pattern changes | balance clearly off 1; clear pair evidence with the period |
| Hand edits | The data moved away | expected on the edited levels clearly outside actual's range |
| | Against a direction, or levels gone | the edit breaks the direction; or it is not applied |

- **Not checks.** A factor that is in but whose column is gone is an error, and stops Train. Out of
  date and the two marks are states of a run.
- Thin levels and same-information factors are fixed in pre-processing, or inside a GLM term.
  Settled: "Feature engineering stays in the pre-processing nodes."
- **The limit.** A leak in every row lifts every model, the standings and the test rows alike. Three
  things can catch it: the leak check, "each factor left out in turn" on the favourite, and the
  analyst's knowledge of when a column is known.

## Per model type

**t-boost: how simple can the tables be?** Tables kept across, deviance up, the chosen point marked.
- *Today:* only pruning has a path of scores, out of bag (`t-boost/python/t_boost/sklearn.py:4108`).
  Banding and graduation give one point each (not rechecked). t-boost keeps only the finished tables
  (`t-boost/python/t_boost/sklearn.py:4281-4284`), so another point means boosting again.
- The choice is recorded as the rule t-boost takes, a tolerance or a fraction
  (`t-boost/python/t_boost/sklearn.py:1888-1889`), not a point on one path. So it replays in every fold and the refit.
- Pick on the out-of-bag evidence, from the training rows: walking the path on the validation rows
  spends them. Graduation gets one added number: graduated against as fitted, on validation rows.

**GLM: which terms earn their place?** Settled: "The GLM needs its own workflow."
- With the fit: relativities with intervals, a Wald test per term, AIC, BIC, and binned partial
  residuals to choose a term's shape (Goldburd and others). rustystats also has score tests for
  factors not in the model, unused by Haute
  (`rustystats/python/rustystats/diagnostics/api.py:2161-2162`).
- All of it is in-sample and carries the mark. Each test sits beside the validation difference of
  the same change. The validation rows judge, and the tests guide.
- The intervals are too narrow: they assume independent rows, and renewals of one policy are not. On
  large data every term is significant, so rank terms by the validation gain they bring.

**EBM.** A shape is jagged where rows are few, so it needs the spread across folds. The `interpret`
docs warn that a direction set in training can be routed round through a correlated factor, which is
why "direction not kept" reads the expected line too.

**Gradient boosting: was the tuning worth it?** Tuned against defaults, on the validation rows: "no
clear difference" means keep the defaults. SHAP stays in these panes. It describes the model, not
the data, and its values are on the raw score, so a level's mean is not its relativity. Kept
predictions are the common currency between model types, never SHAP.

## Hand edits and directions

Settled: "An analyst may edit a fitted table or relativity by hand before logging. An edit carries
over when the model is trained again, and the rest of the model is refitted around it where the
model type allows."

There are two routes, and the tool says which applies.
- **A whole table is fixed through the offset.** The factor leaves the fit and the rest is refitted
  around it (Anderson and others). All six model types take an offset. *Today:* Haute carries one
  offset per model (`src/haute/modelling/_algorithm_base.py:71`), usually the exposure, so this needs a
  second. t-boost and rustystats already take both (`t-boost/python/t_boost/sklearn.py:3606-3614`,
  `rustystats/python/rustystats/formula.py:5582-5583`).
- **Part of a table is laid on top of the fit.** No library holds part of a table fixed today (not
  rechecked). Nothing else moves and the total goes off by the edit, so the tool offers `Rescale the
  base`: a recorded step that balances the total on the training rows.

**The preview: what does this edit do, before anything trains?**
- For each row in an edited cell the new prediction is the old one times new value over fitted
  value. It leads with the rows the edit touches, since the overall score barely moves for a local
  edit (Wang and others): actual against expected there, before and after.
- **The cost:** the model with the edit against the model without it, on those rows and overall, and
  the share of the expected total that moves. "No clear difference" is the good answer here.
- It lists the factors whose mix in those rows differs most from all rows: the tables that would
  move in a refit. It shows the fitted value's spread across folds, with no verdict.
- Misleads: an edit drawn towards the validation actuals scores better on them by construction. A
  gain is labelled "on rows you have seen" and never counts in the standings.

**After a refit around a whole table:** how far each other table's effect moved: who compensated. An
edit to a claim-count model is compensated only inside that model (Anderson and others).

**What a direction costs.** Settled: "Each model has its own factors." So a direction is set per
model, and its cost is that model's. It takes a what-if run of the model without the direction: the
difference overall, and by level of the factor. The score barely moves, and the levels show where
the cost lands. A duplicate of the model without the direction shows the same on every chart, for as
long as both are kept.

## At logging

Settled: "A model is logged to MLflow. Ensembles are out for now."

- **Refit against reviewed.** The logged model is a refit on the training and validation rows
  together: the reviewed settings, the boosting rounds fixed at the reviewed fit's best round,
  t-boost's rules replayed, the hand edits applied. It has no honest prediction for any row it
  learned from. So the tool shows how far it moved from the reviewed fit: per factor, the largest
  move in its effect, and how far predictions move. A move above 2% is flagged.
- **The expected range for the test score** is stated before the test rows are scored. It holds both
  sets of noise: the validation rows', and the test rows' own, sized from their exposure, not their
  actuals. The validation range alone would put an unchanged model outside it about one time in six,
  when the two sets of rows are of similar size. It leaves out drift and the fact that the model was picked.
- **The test score** is shown with its range, with balance, and with its difference from the last
  logged model where that model never learned from these rows. A score below the expected range
  means the rounds of tuning fitted the validation rows, or the data moved. A steady fall in the
  validation score by period is the data moving.
- **The record of looks** belongs to the node's rows, by id, not to a split or a run. Changing the
  split after a look does not reset it: the model card says how many of today's test rows were seen
  before, and as what. Where the latest claims are still developing, the test rows end at the
  "claims are mature up to" date from setup. Without that date the score carries a warning.

## Default numbers

Each is a default to tune on real data. How they change with the size of the data is in
`workflow.md`. Ranges: 95%. Folds: 5. Validation rows: a fifth of the rows that are not test rows.
Rows held back for early stopping and tuning: 15% of the training rows. Bands of a number factor:
20, or claims / 50 if fewer, and at least 5. A thin level: under 20 claims. Resampling: policies are
hashed into 2,000 groups, drawn 2,000 times, so the cost is the same at any size. On large data the
charts come from one table of sums per run, factor and band, and the sampled analyses (estimated
effects, SHAP, the same-information check) use up to 200,000 rows. A likely leak: a strength above 3
times the median of the next five. The same information: Cramér's V above 0.8, a convention.

## Left out, and why

- **The fold vote** (a difference had to keep its sign in four folds of five). It passes by chance
  too often. **A narrower range from the thorough evaluation:** folds share training rows, so it
  needs a correction. Nadeau and Bengio give one, but nobody on the team read it in full.
- **An outside benchmark,** such as today's price. The last logged run answers "better than today?".
- **"What is this model missing", and a search over all pairs.** Each costs a training, and the kept
  pairs and the grid answer the same question.
- **"What the data can show".** A range's width depends on the two runs compared, not on the data.
  **"Off level" and "ranks risks"** as standings columns: balance and the calibration curve say it.
- **Claim counts and claim sizes checked together** as a pure premium. Each node is judged alone. It
  is the largest gap: an edit in one node is not offset in the other.
- **Smaller, or waiting on t-boost:** curves for banding and graduation, the demand curve for a
  conversion model, isotonic recalibration, a range on Gini.

## Sources

Read by the team: Anderson and others (2007), CAS Study Note on GLMs; Goldburd and others (2020),
CAS Monograph No. 5; Fissler, Lorentzen and Mayer (2022), arXiv:2202.12780; Wang and others (2021,
2022), arXiv:2112.03245 and arXiv:2206.15465; the abstract of Apley and Zhu (2020), JRSS B 82(4); a
WTW deck on Emblem, Dataiku's Visual GLM docs, the `model-diagnostics` and `interpret` docs, Uber's
Manifold blog. Seen only as a search summary: Wüthrich (2023), arXiv:2207.14372; Denuit, Charpentier
and Trufin (2021), Insurance: Mathematics and Economics 101; Dimitriadis, Gneiting and Jordan
(2021), PNAS 118(8); Cabrera and others (2023), arXiv:2302.04732; Nadeau and Bengio (2003), Machine
Learning 52. Not opened by the team: Benjamini and Hochberg (1995), JRSS B 57.
