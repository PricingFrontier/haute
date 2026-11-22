# Analytics

Everything the Modelling UI can tell the analyst, and exactly how each thing is worked out. All of it is proposed.

- Claims about Haute today rest on the engineer's report, cited as 03 §n, or on a path given here. `BE/` = `/home/ralph/suite/haute/src/haute/`, `RS/` = `/home/ralph/suite/rustystats/python/rustystats/`, `TB/` = `/home/ralph/suite/t-boost/docs/`.
- Sources are the researcher's cards, cited as 02-E6 and so on. Full references are in `02-state-of-the-art.md`. A method with no card is my own practice. Every threshold is a default, to be tuned on real data.
- Each analysis gives the question, then a bracket: what it **needs** (D = the node's data, P = kept predictions, S = scoring the model again, M = the inside of the model), its **cost** on large data, and the **targets** it applies to. Then the method with its range, and how it **misleads**, with the guard.

## 0. Conventions

**0.1 Targets, mass and the main score.**

| Target | y, the row's actual | m, the row's mass | Unit deviance d(a, e), on the rates a = y/m and e = μ/m |
|---|---|---|---|
| Claim counts | claims | exposure | Poisson: 2[a·ln(a/e) − (a − e)] |
| Claim sizes | claim amount | number of claims | Gamma: 2[(a − e)/e − ln(a/e)] |
| Pure premium | claim amount | exposure | Tweedie, power p: 2[a^(2−p)/((1−p)(2−p)) − a·e^(1−p)/(1−p) + e^(2−p)/(2−p)] |
| Yes/no | 0 or 1 | 1 | −2[a·ln(e) + (1 − a)·ln(1 − e)] |

- μ is the model's expected total for the row. A weight column multiplies m.
- For any set of rows: actual A = Σy / Σm, expected E = Σμ / Σm. An "event" is a claim, a non-zero row or a yes.
- **The main score** is the mean deviance of the shared loss: D = Σ m·d / Σm. Lower is better. It is the score that matches a model of the mean (02-E2). Haute has all four today (`BE/modelling/_metrics.py:321-370`).
- **One Tweedie power for scoring,** set in the All models menu, whatever power a model trained with. Mixed powers do not compare. Today the score takes the power the model trained with (`BE/modelling/_metrics.py:136`).
- **The floor** is the model with no factors: one rate, Σy / Σm on the training rows. **Gain** = D of the floor − D of the model.

**0.2 Which model predicts which row.** A run keeps up to four columns of predictions.

| Rows | Predicted by | Honest? | Used for |
|---|---|---|---|
| Validation | the reviewed fit: fitted on training rows only | yes | every score and every default chart |
| Training and validation, after a thorough run | the fold model that did not see the row | for the fit, not for the analyst's choices | less noisy level charts, stability |
| Training | the reviewed fit | no: marked in-sample (02-B2) | the overfitting check only |
| Test | the final refit, at logging | yes, once | the logged result |

**0.3 Bands, groups, buckets and thin levels.**
- **Bands.** A number factor is cut once per node into bands of equal mass, on training and validation rows: 20 bands, or events / 50 if that is fewer, and at least 5. Every model and run is charted on the same bands. Today each run cuts its own 10 bands and keeps the top 15 categories (03 §2c).
- **Group.** The rows that share the split's group value, such as one policy. With no group column, each row is its own group.
- **Buckets.** Each group is hashed into one of H buckets: H = 2,000, or the number of groups if smaller, fixed per node. Sums are kept per bucket. Resampling draws buckets, so it costs the same on 15,000 rows as on 20 million.
- **Thin-level rule.** A level mean or correction learned from data is pulled towards the overall value with the weight of 20 events: (level total + k × overall rate) / (level mass + k), where k is the mass that holds 20 events on average. For a correction to a model, the overall value is 1 and the mass is the expected total.

**0.4 The three ranges.** All at 95%.
- **On a difference between two runs (R-diff).** Per row, δ = m·[d under run A − d under run B], on the same rows. Sum δ and m per bucket. Draw 2,000 resamples of the buckets with replacement; each gives Σδ / Σm. The range is the 2.5th to 97.5th percentile. Any ratio of sums (balance, a share of the gain) is resampled the same way.
- **On actual at a level (R-level).** The wider of two: the loss-based range in the table below, and the range from resampling the level's buckets (used when the level has rows in 50 buckets or more). The first keeps a level with no claims honest. The second catches heavy tails.
- **On an effect (R-spread).** The lowest and highest value across the fold models of a thorough run. It is a spread, not a confidence interval, and is labelled so.

| Target | Loss-based range on A, for a level with N events |
|---|---|
| Claim counts | the exact Poisson interval on N/φ claims, scaled by φ / Σm: from ½·χ²(0.025; 2N/φ) to ½·χ²(0.975; 2N/φ + 2) |
| Claim sizes | A × the 2.5% and 97.5% points of a Gamma with mean 1 and shape N/φ |
| Pure premium | A ± 2·√(φ·Σ m·e^p) / Σm, floored at zero |
| Yes/no | the Wilson interval on (rows / φ) rows |

- φ is the dispersion: the Pearson statistic on group totals over the number of groups, from the chosen model on validation rows (from the floor before any model exists), never below 1. One number per run.

## 1. Scores and their ranges

**1.1 The scores.** Which model is better, and in what way? *[P · one pass · all four]*
- Main: D, shown as the gain over the floor. Then, in this order: balance (ΣE / ΣA), "off level" and "ranks risks" (4.2), Gini (4.5).
- Yes/no: D is twice the log loss. AUC = (Gini + 1) / 2 sits where Gini sits.
- MAE is not offered for choosing a model. The median minimises it, and the median claim count is zero, so it rewards predicting nothing. RMSE is fair for a mean, but a few large claims decide it. Both stay as extra columns.
- Misleads: Gini only ranks, so a model can top it while charging everyone 10% too little (02-E2). Guard: Gini is never the sort column and never shown without balance.

**1.2 The range belongs on the difference.** Is the gap between two runs real? *[P of both runs · one pass, then sums over 2,000 × H numbers · all four]*
- Method: R-diff (0.4) on the per-row score differences, on the same validation rows, resampled by whole groups. A t-test on paired differences is the textbook form (02-E6). The bucket resample is the same idea, and it survives heavy tails.
- A range on one score alone is mostly the noise no model can remove (02-E5). Two such ranges overlap even when one model wins on nearly every policy (01 scenario 2). So the score cell shows the score, its difference from the reference, and the range of that difference.
- Misleads: the range covers the luck of which rows are validation rows. It does not cover the luck of the training rows or of the seed (02-E6). Guard: the tooltip says so, and a thorough run adds the fold vote in 1.3.

**1.3 The rule for "no clear difference".** Two runs differ clearly only when all three hold. Otherwise the tool says "no clear difference", with the raw number on hover (02-E7).
1. They are like for like (1.4).
2. The R-diff range excludes zero.
3. Where both have a thorough run: the difference has the same sign in at least K − 1 of the K folds.
- The size is always given as a share of the span from the floor to the strongest model. On large data a tiny gain is clear, and must still read as tiny.
- Standings: models with no clear difference from the best are bracketed as tied. Inside a tie the rows keep the menu row's order, not the score's, because size does not compare across model types.
- Fold spread is not the range. Folds share training rows, so it is too narrow (02-E6), and five numbers give an unstable spread. The folds vote; they do not measure.
- Misleads: forty comparisons at 95% will show about two clear gains that are not real. The rule slows tuning to noise and does not stop it. The sealed test rows are the check.

**1.4 Like for like.** Each run records the split it used, the rows it learned from, sample or full, quick or thorough, and any shared setting it could not honour.
- Two runs are compared only on rows neither learned from, and the comparison says how many.
- A run that ignored a shared setting (a direction, the loss) is marked in the standings and never bracketed as tied with the rest.
- A reference model logged earlier was refitted on rows that may be validation rows today (01 scenario 3). It is compared only on rows after its own cut-off, or shown with the in-sample mark.

## 2. Honest evaluation, mechanically

**2.1 The split is made once per node.** Each row gets one label (training, validation or test), stored against a row key with the data version. Training never draws it again.
- Today a random split can move when the factors change (03 fact 2, unverified there). Then "actual is drawn once" is false, and paired differences are impossible.
- **Group and time together.** Test rows are the latest period, since that mimics using the model on next year's business. Inside the earlier rows, validation rows are whole groups, so one policy never sits on both sides. Today a split is random, by group or by time, never two at once (`BE/modelling/_evaluation.py:194`).
- Validation is one fifth of the rows that are not test rows. It doubles as fold 1 of the thorough run (2.3). The All models menu shows rows, mass and events for each row set.

**2.2 Rows for early stopping and tuning.** Everything a model learns from comes out of the training rows. It adds no cost: the same fits run on slightly fewer rows.

| Model type | Today (03 §2a) | Rule |
|---|---|---|
| LightGBM, XGBoost, CatBoost | stop early on the validation rows | stop on an inner slice: 15% of the training rows, drawn as the validation rows were (whole groups, or the latest period) |
| t-boost | stops and prunes on rows held back inside its fit, by whole groups when `groups` is passed (`TB/features/panel-data.md`; Haute can pass it, `BE/modelling/_tboost.py:600`) | no change |
| EBM, GLM | no early stopping | no change |
| Tuning | every trial is scored on the validation rows | trials are scored on the inner slice. The winner's inner score is flattered, being the best of many (02-B3), and is never shown as the model's score |

**2.3 Cross-validated predictions (the thorough run).** K = 5 folds over the training and validation rows, by whole groups. Fold 1 is the validation rows, so a thorough run adds four fits and leaves every validation score unchanged.
- Under a time split, fold k is trained on the periods before it. The earliest period gets no prediction.
- Repeated in each fold: the fit, early stopping on that fold's inner slice, t-boost's prune, band and graduate, and the hand edits. Not repeated: tuning (the tuned settings are reused), and every choice the analyst made.
- **It is not a second honest score** (02-E8). The analyst chose factors, terms and edits with sight of all those rows. It gives three things: a prediction for every row, so thin levels chart with less noise; the spread of each effect across folds (R-spread); and the fold vote in 1.3.
- The headline score stays on the validation rows for every run. So a quick run and a thorough run sit in one standings table with no caveat.
- Cost: K − 1 more fits. A fold model is kept only as its effects and its predictions.

**2.4 The sealed test rows.** No chart, score, check or band edge uses them, before or after fitting.
- At logging the final refit scores them once. Shown: D, balance, and the R-diff ranges of its gain over the floor and of its difference from the last logged run, where that run did not learn from these rows.
- Read against validation: the tool says whether the test gain sits inside the range of the validation gain. If it is clearly worse, either the rounds of tuning fitted the validation rows, or the data moved. The validation score by period (4.7) tells which: a steady fall by period is the data moving.
- The count of looks is permanent in the model card (02-B1). After one look the test rows are spent for choosing between models.
- Misleads: one number on a small test set (01 scenario 2). Guard: it is never shown without its range.

**2.5 The final refit, and "refit against reviewed".** Has the model I am logging moved from the one I reviewed? *[M and S · one scoring pass · all four]*
- The logged model is fitted on training and validation rows with the reviewed settings. Boosting rounds are fixed at the reviewed fit's best round. Hand edits are applied. It has no honest prediction for any row it was fitted on (03 fact 3), so everything the analyst reviewed came from the reviewed fit.
- Method: per factor, the mass-weighted mean and the largest absolute change in the effect (3.4), on the log scale. Over the validation rows, the ratio of refit to reviewed prediction: its 5th, 50th and 95th percentiles, and the share of mass that moves by more than 5%. This measures movement only. It is never a score.
- Flag: a factor whose effect moves by more than its R-spread, or by more than 2% where there is no thorough run.

## 3. By factor

**3.1 Fit: actual against expected, with a range.** Is each model right at each level? *[P · one pass per factor, cached per run · all four]*
- Method: per level, A and each model's E (0.1), on validation rows, or on every out-of-fold row after a thorough run. The range is R-level, on A. A level is marked only where a model's E falls outside it.
- Misleads, with guards. A range from exposure alone is wrong for claim sizes: R-level uses the claim count and the spread of the amounts. With 24 levels one sits outside by chance: the header reads "3 of 24 outside, about 1 expected by chance". One large claim can make a level: hover gives the largest claim's share of the level's actual, and marks it above 25%. A factor can fit well because a correlated factor carries it: the effect (3.4) shows that. E has its own error, which the range leaves out: the tooltip says so.

**3.2 "Fits worst": the gain a factor still offers.** Which factor should I work on next? *[P · one pass per factor · all four]*
- Method: split the buckets into two halves. In each half, per level, learn the correction c = Σy / Σμ, pulled towards 1 by the thin-level rule. Apply each half's corrections to the other half, so μ becomes c·μ (for yes/no, capped below 1). The measure is the fall in D, as a share of the model's gain over the floor. Its range is R-diff, because it is a difference between the model and the corrected model.
- Why this one: under a perfect fit it is zero or just below, whatever the number of levels, because a correction learned on other rows cannot help. A sum of gaps grows with the number of levels, and a "worst level" measure picks the thinnest level (01 scenario 2; 02-D2).
- The list sorts by the measure. A factor whose range includes zero is greyed as "no clear misfit" and sorted last. It works for a factor that is left out, which is how a missed factor reaches the top.
- Misleads: the gains do not add up across correlated factors, and a model right at every level can still be wrong in the cells (3.6). Guard: the list is a worklist, re-sorted after each training, and is labelled so. Sixty factors tested at 95% give about three clear misfits by chance: the list's header states that count, as it does for 3.5.

**3.3 A factor's strength on its own.** How much signal does this column hold, before any model? *[D · one pass per factor · all four]*
- Method: on the training rows, the mean rate per level or band, with the thin-level rule. Score those means on the validation rows. The strength is the gain over the floor, with R-diff. A level unseen in training gets the overall rate.
- Fitted and scored on the same rows, an identifier would be the strongest factor. Scored on other rows, it scores nothing.
- Misleads: it includes whatever correlated factors carry, so strengths do not add up, and a strong factor may add nothing to a model. Guard: the label reads "on its own". It is never called importance.

**3.4 Each model type's effect for a factor.** What is the model doing with this factor? *[M when exact; S when estimated, two predictions per sampled row per factor · all four]*
- Common shape: the factor's contribution on the link scale, centred so that its mass-weighted mean over the validation rows is zero, drawn at the model's own cuts. Log link: a multiplier of the rate. Logit link: a multiplier of the odds, labelled so (01 scenario 8).
- One centring for every model type removes the false gap between a GLM on a base level and a t-boost table on its average (01 scenario 6). A named base level is an option.

| Model type | Effect | Method | Range |
|---|---|---|---|
| t-boost | exact | the factor's main-effect table. The docs call every table purified (`TB/model-analysis/contributions.md`), which I read as: interaction tables average to zero along each factor | R-spread |
| GLM | exact | the term's columns times its coefficients, at each level or band | from the coefficient covariance, for the centred contrast; ready with the fit, but too narrow where rows are related (6.2) |
| EBM | exact | the factor's term scores (whether pair terms are purified is unverified) | R-spread. No bag bands today: `outer_bags=1` (03 §2a) |
| LightGBM, XGBoost, CatBoost | estimated | accumulated local effects on the raw score (02-E9), over the shared bands, on up to 50,000 rows. An unordered category has no order to accumulate along: use the mean SHAP value per level (my judgement) | R-spread |

- Position: drop partial dependence from the shared charts. It predicts at combinations that do not occur, and rating factors are correlated (02-E9). Today's PDP also rests on 500 rows (03 §2c).
- A factor with interactions: the chart draws the main effect and names its partners. Split by a partner draws the effect at each of the partner's levels.
- Misleads when factors are correlated. An effect is conditional on the other factors in that model, so two models with different terms can both be right and still differ: the legend says which factors each model uses. Two factors that carry the same information split one effect between them arbitrarily, and differently in each fold: a wide R-spread and the same-information check (5) show it.
- Misleads as a price. The effect is not the price gap between two real groups of customers, because the other factors move with it. Guard: the one-way actual relativity is drawn faint behind the effect, and the gap between the two is what the correlated factors carry (02-A7). An estimated effect is drawn dashed and labelled "estimated".

**3.5 Consistency over time.** Is this pattern lasting, or one year's accident? *[P, and the period column, which may not be a factor · one pass · all four]*
- To look: the fit view split by period (the year by default), as the ratio A / E with R-level, one small chart per period on one axis. A / E is used because raw actual by year also moves with the mix of the other factors.
- To measure: the interaction evidence of 3.6, with the period as the partner. A clear gain means the model's error at this factor's levels changes with the period. This drives the "pattern changes between years" check.
- To see the effect itself by period: a what-if run with the factor interacted with the period. Do not fit a model per year, because correlated factors trade off between the yearly fits (02-A3).
- Misleads: a thin year crosses lines from noise. The range guards. The latest period often has claims still to be reported, which shifts its level and not its shape, so each period is centred on its own total.

**3.6 Evidence for an interaction.** Is the model wrong in the cells of two factors while right on each alone? *[P · one pass per pair; the search runs on a sample · all four]*
- Method: on one half of the buckets learn, in order, a correction for each margin and then one per cell, each with the thin-level rule. Apply them to the other half, and the reverse. The evidence is the fall in D from the cell corrections beyond the margin corrections, with R-diff. A number factor uses 5 bands.
- Search on the training rows, confirm on the validation rows. The shortlist comes from the interactions t-boost kept, EBM's chosen pairs, and the pairs a small boosted model leans on when it starts from the chosen model's prediction as its offset (02-E10). Only the top ten are scored on validation rows, so nothing is found by search on the rows it is judged on.
- A pair is shown when its cells hold enough mass, its gain is above 0.5% of the model's gain, and its range excludes zero (02-D1). Otherwise the tool says "no pair stands out". The picture is the two-way heatmap of A / E, with thin cells greyed (02-D2).
- Misleads: a pair with a factor the model lacks shows a missing main effect, not an interaction. The margin corrections remove that first.

## 4. By model

**4.1 Between the floor and the strongest model.** How much of the signal does this model capture? *[P · one pass · all four]*
- Method: position = the model's gain / the strongest model's gain, on validation rows. The floor's rate comes from the training rows. The strongest is the run with the lowest D, labelled "strongest so far". It is a yardstick, not a ceiling.
- Range: the position is a ratio of two noisy differences, so the two are resampled together (0.4).
- Misleads, with guards. The gain as a share of the floor's D looks tiny for claim counts, because most of that deviance is chance (02-E5): the bar is the headline, not that percentage. When no model clearly beats the floor a bar would be noise: that statement replaces it. A leak lifts every model and the bar still looks healthy (01 problem 15): the leak checks (5) are the guard.

**4.2 Balance and calibration.** Is the model right in level: in total, by factor, and across its own predictions? *[P · one sort · all four]*
- Balance: ΣE / ΣA on validation rows, with its range from the buckets, in total and by period. Flag when 1 is outside the range. By factor: the A / E scale of 3.1.
- Across predictions: an isotonic curve (a monotone step curve) of actual on predicted, weighted by mass. No bins to choose (02-E3). The band comes from 200 bucket resamples on up to 200,000 rows.
- Two columns for the standings (02-E5). **Off level** = D of the model − D of the recalibrated model. **Ranks risks** = D of the validation rows' own overall rate − D of the recalibrated model. The first says fix the level. The second says how good the ranking is. The recalibration is learned on one half of the buckets and applied to the other, as in 3.2, so it cannot flatter itself.
- Boosted model types can lose balance when trained on deviance (02-E4). The fix offered in their menu is one scale factor, learned on the training rows. Isotonic recalibration can wait.
- Misleads: under a time split the validation level also moves with claims trend, in every model alike. Guard: the floor's balance is shown beside each model's. A total in balance can hide local imbalance, which the other two levels show.

**4.3 Lift.** Does the model separate low risks from high, and is it right at both ends? *[P · one sort · all four]*
- Method: sort validation rows by predicted rate. Cut into 10 bands of equal mass (5 when events are scarce). Plot A and E per band, with R-level on A.
- Today the bands hold equal row counts, and this one-model chart is named "double lift" (`BE/modelling/_metrics.py:406-437`). Equal rows put unequal exposure in each band.
- Misleads: two models' lift charts cannot be compared band for band, since each sorts the rows its own way. Guard: for two models the tool offers the double lift.

**4.4 Double lift.** Where two models disagree, which is closer to actual? *[P of both runs · one sort · all four]*
- Method, as the monograph defines it (02-E1): sort rows by the ratio of run A's prediction to run B's. Cut into 10 bands of equal mass. Per band plot A's average, B's average and the actual average, each over its overall average, with R-level on actual.
- Summary line: the R-diff of the two runs inside the lowest and the highest band, where they disagree most.
- First show how far apart they are: the 5th and 95th percentiles of the ratio. Within ±2%, say "these two runs price alike" and draw nothing.
- Misleads: end bands are thin on small data. The range guards. The chart says which run is closer, not why (4.8).

**4.5 Lorenz and Gini.** How well does the model rank? *[P · one sort · all four]*
- Method: sort by predicted rate, rising. x = cumulative share of mass, y = cumulative share of actual. Gini = 1 − 2 × the area under the curve, normalised as Haute does today (`BE/modelling/_metrics.py:203`). Range: 200 bucket resamples on up to 200,000 rows.
- Misleads: it ignores level entirely, and is a sound way to choose only between models that are right in level (02-E2). Guard: shown second, always beside balance and "off level".

**4.6 Residuals.** Is the shape of the error what the loss assumes? *[P · one sort · counts, sizes and pure premium; for yes/no the calibration curve does this job]*
- Method: always binned (02-A6). Group rows into 50 bins of equal mass by prediction. Per bin, the deviance residual of the totals: sign(A − E)·√(Σm·d(A, E) / φ). Under a sound model the bins scatter around zero with a spread near 1.
- For claim sizes and pure premium, list the ten rows that add most to D. This answers how much one claim drives a score (01 scenario 2).
- Misleads: a row-level histogram of claim counts shows stripes, not misfit. It is dropped.

**4.7 Scores by segment and by period.** Does a model that wins overall lose somewhere that matters? *[P · one pass · all four]*
- Method: per segment, three things. Its share of mass. Each model's A / E with its range. The R-diff between the model and the reference inside the segment.
- Raw D per segment is not shown. It rises with the claim rate of the segment whatever the model, so segments cannot be compared on it.
- Segments are named in advance and kept in the node's config (02-D5), plus the period. A search over all segments must pass the three tests of 3.6 first.
- Also here, from the same predictions: how far predictions move between two runs. The percentiles of the ratio, and the share of mass moving by more than 5% and 10%, by segment (01 scenario 3).
- Misleads: twenty segments give one clear loss by chance. Guard: the count of segments tested is shown with the result.

**4.8 Where two models disagree, and why.** What drives the difference, for these rows? *[P for where, M for why · one pass · all four]*
- Where (02-D3): take the rows in the end bands of the double lift. For each factor, measure how far its mix in those rows is from its mix in all rows: half the sum over levels of |share there − share overall|. Factors are listed by that distance. Each opens the factor chart filtered to those rows.
- Why, for the model types whose model is tables or terms: per row, the log of the ratio of the two predictions is exactly the sum over factors of the difference in contributions. Average it over the chosen rows for one bar per factor. An interaction is shared equally between its factors, as t-boost does (`TB/model-analysis/contributions.md`). For gradient boosting the same bar uses SHAP values and is labelled estimated. This answers "why does the new model charge this segment less" (01 scenario 4).
- Misleads: the "where" list shows association only. A factor that travels with the true cause ranks just as high.

## 5. Checks on the data

Each check has a rule, a status (clear, look, or not run) and a link to its fix. The list stays silent when all is clear (02-B5). None uses the test rows.

| Check | Rule (default) | Needs |
|---|---|---|
| A factor that leaks the target, before fitting | its strength on its own (3.3) is more than 3 times the median of the five next strongest factors. The median, so that two columns cut from one claim field do not hide each other | D |
| The same, after fitting | one factor holds over half of the importance in every model. The check offers the "without this factor" what-if | M |
| Identifier or constant | a category with over 90% of its mass in levels of fewer than 20 events (this catches a policy number with five renewals); the column is the split's group, the exposure or the date; or one level holds over 99.5% of the mass | D |
| Thin levels | levels with fewer than 20 events, and the share of the factor's mass they hold; levels in validation rows never seen in training | D |
| Same information | every factor banded, then the bias-corrected Cramér's V for each pair, on up to 200,000 rows: look above 0.8. Also where one factor fixes the other (each level of one maps to one level of the other) | D |
| Change over time | per factor, the population stability index between the earliest and the latest period, Σ (p − q)·ln(p / q) over levels: look above 0.25. New and vanished levels. A, and A / E, by period | D, P |
| Setup and row sets | a count target with no exposure; mass at or below zero; a claim-size row with no claim; a negative target; the overall rate on training and validation rows differing by more than its range under a random or group split | D |
| Direction not kept | on a factor with a direction: the model's effect is not monotone, or its expected line on validation rows is not (02-C5) | P, M |
| Overfitting | the in-sample gain over the validation gain, shown for every model: look above 3, since boosted models sit well above 1 as a matter of course. Listed by the levels where the gap is widest (02-D4) | P |

- Fixes for thin levels and for factors that carry the same information are made in pre-processing, or in a GLM term's grouping. The check only points.
- Why same-information factors matter: their effects are unstable, importance is split between them, a direction on one can be routed round through the other (02-C5), and a hand edit on one is quietly undone by the other.
- Misleads: a leak in every row lifts every model, the standings and the test rows alike. Only the two leak checks, and the analyst's knowledge of when a column is known, can catch it. The 0.25 and 0.8 are conventions, not theory (no source checked).
- Cost: single passes on a sample of at most 200,000 rows, except the thin and unseen level counts, which use every row.

## 6. Per model type

**6.1 t-boost: fit against simplicity.** How simple can the tables get before the fit suffers? *[M · reading a report is free; each extra curve point is a fit until t-boost can apply a step again · all four]*
- One chart form for all three steps: x is a count the analyst understands, y is D, and the chosen point is marked. The default is the simplest point that loses nothing clear against the best. For pruning that is t-boost's own rule: the smallest prefix within its tolerance of the best.

| Step | x | y, and where it comes from | Today |
|---|---|---|---|
| Prune | tables kept, in rank order, each point named by the table it adds | the out-of-bag deviance of each prefix, on training rows (`TB/training-parameters/pruning.md`) | the path exists for pruning, with two bags or more. Haute does not read it and cannot pass a chosen point (03 §5.1) |
| Band | cells in the tables | the change in predictions, in units of the spread between bags, and the estimated cost in D (`TB/training-parameters/banding.md`) | one chosen point only. A curve needs several fits, or t-boost banding again without boosting (03 §5.1) |
| Graduate | turning points across the tables | the rise in training deviance, and the R-diff of graduated against as fitted on validation rows | one strength per table. Graduation needs only the tables and the training rows, so t-boost could apply it again without boosting. Its public API cannot today, so on against off costs a second fit. Whether it runs under a monotone fit is unknown (03 §5.1) |

- Position: pick the prune and band points on the out-of-bag evidence, which lives in the training rows. The validation rows then score the chosen model once. Walking a curve on the validation rows spends them.
- Position: graduation is never tested on rows held back. t-boost's own check is a 1% brake on training deviance, "not a test of accuracy" (`TB/training-parameters/graduation.md`). So the validation R-diff of graduated against as fitted is the one number the tool must add.
- Misleads: the chosen prefix is picked on its out-of-bag score, so that score is slightly flattered. Points closer than the pruning tolerance are drawn as one flat stretch.

**6.2 GLM: inference, and its limits.** Which terms earn their place? *[M · comes with the fit · counts, sizes and pure premium: the GLM does regression only today, 03 fact 14]*
- What it gives: coefficients with standard errors; relativities with intervals (3.4); a test for dropping a term (the rise in deviance, as an F test where the dispersion is estimated); a score test for adding a factor not in the model; AIC and BIC; variance inflation; the dispersion; and binned partial residuals to choose a term's shape (02-E11). rustystats has switches for variance inflation, score tests and robust errors (`RS/diagnostics/api.py:2134-2166`). Haute does not use the score tests today (03 §4, item 10).
- Limit 1: all of it is in-sample. It carries the in-sample mark, and each add or drop test sits beside the validation R-diff of the same change (01 scenario 6).
- Limit 2: the standard errors assume independent rows. Renewals of one policy are not, so the intervals are too narrow. Robust errors do not cure this: rustystats has HC0 to HC3 (`RS/formula.py:826`), which repair a wrong variance assumption, not related rows. Errors grouped by policy would; I found none in that file. Until then, read R-spread from folds of whole groups, or refit on resampled groups.
- Limit 3: tests on terms chosen by looking are flattered, and no setting repairs that. The validation rows judge. The tests guide.
- Limit 4: on large data every term is significant. Rank terms by the validation gain they bring, not by p-value. On small data the interval is the message.
- Limits of form: a spline has no single coefficient to read, so test the term as a block and show its curve with a band. A penalised fit has no valid standard errors, so show none and say why. A level with no claims has no finite estimate, so show it as a failed level, not as a huge interval.

**6.3 EBM.** What shapes did it learn, and can they be trusted? *[M · comes with the fit · those its losses allow]*
- Its panes: each term's shape with mass beneath, and the terms ranked by mean absolute score (03 §2c).
- Position: an EBM shape with no band invites reading noise as pattern, because the steps are jagged where rows are few. Either the thorough run supplies R-spread, or Haute raises `outer_bags` above 1 for bag bands, at that multiple of the cost.
- Apply a direction after the fit, not during it: the library's own docs warn that a direction set in training can be routed round through a correlated factor (02-C5). EBM has no early stopping in Haute today (03 §2a), so the overfitting check matters most here.

**6.4 Gradient boosting: tuning diagnostics.** Is the tuning finished, and was it worth it? *[M and P · comes with the tuning, plus one fit for the seed check · all four]*
- Learning curve: D on the training rows and on the inner slice, by round, with the best round marked. Flag a best round at the limit (not converged), or under 20 (learning rate too high).
- Trials: the inner score by trial with the running best; and each setting against the score, with the winner marked. Flag a winner at the edge of its search range.
- Worth it: the R-diff of tuned against defaults on the validation rows. "No clear difference" means keep the defaults. Report this, not the winner's inner score, which is the best of many (02-B3).
- Seed noise: train the winner once more with another seed and take the R-diff of the two. A gain smaller than that is not a gain. My own practice: cheap on small data, optional on large.
- Misleads: parallel coordinates suit hundreds of trials, not twenty (02-B4). Leave them out.

**6.5 Cautions on SHAP.** My own judgement: the researcher's list has no source for these.
- It describes the model, not the data. A large value says the model leans on the factor, not that the factor matters.
- Between correlated factors the credit is shared by a rule of the method, and the methods differ. Mean absolute SHAP does not rank factors that carry the same information.
- Values are on the raw score. Under a log link the average of exp(SHAP) is not exp of the average, so a level's mean is not its relativity. A dependence plot mixes the main effect with the interactions: the vertical scatter is the interactions.
- Haute computes it on 5,000 rows, for the boosted types and t-boost only (03 §2c). Thin levels are noise at that size.
- Position: SHAP stays inside the gradient-boosting panes, as a way to look inside. Kept predictions are the common currency between model types, never SHAP. For t-boost, the GLM and EBM the contributions are exact, so SHAP adds nothing.

## 7. Hand edits and constraints

No library holds part of a table fixed in a fit today, and Haute carries one offset column (03 facts 10 and 11). So assume an edit is laid on top of the fit at first. The analytics below serve both routes and say which applies.

**7.1 The preview.** What does this edit do, before I train? *[P, plus each row's cell in the edited table · one pass over the rows touched · all four]*
- Method: for each row in an edited cell, the new prediction is μ × exp(new value − fitted value), on the link scale. No training.
- It leads with the rows touched, not the overall score, which barely moves for any local edit (02-C2). A against E before and after, with R-level. The R-diff of edited against fitted on those rows, then overall. The shift in total balance.
- It lists the factors whose mix in the touched rows differs most from all rows, by the distance of 4.8. Those are the tables that would move in a refit (02-C3).
- It places the edit against the data: the new value in units of the fitted value's range (R-spread, or the GLM's interval). "Inside the range" means the data cannot tell the two apart.
- Misleads: an edit drawn towards the validation actuals scores better on the validation rows by construction. Guard: a preview gain is labelled "on the rows you were looking at" and never counts as an improvement in the standings. Out-of-fold rows are no cure, since the fit view shows those too. The test rows are the only real check.

**7.2 The fit around an edit, and what else moved.** How did the rest of the model respond? *[M, and P of both runs · one pass · all four]*
- Where the model type refits around the edit (a whole table held through the offset, 02-A4): against the run it replaced, per other table, the mass-weighted mean and the largest absolute change in log effect. List the tables that moved by more than their R-spread, or by 1%. Then the ratio of edited to free predictions by level of those tables. This is "who compensated" (02-A4).
- Where the edit is laid on top: nothing else moves, and the pane says so. The total goes out of balance by the edit. Position: offer to rescale the model's base so the total balances on the training rows, as a recorded step. Without it, one edited table shifts the level of the whole book.
- Misleads: an edit to a claim-count model is compensated only inside that model (02-A4). The pure premium may still be off.

**7.3 What an edit costs.** Is the judgement cheap or dear? *[P · free for an edit laid on top; a refit needs the run before the edit, or a what-if run without it · all four]*
- Method: the R-diff of the model with the edit against the model without it, on the rows touched and overall, on validation rows. Plus the share of the expected total that moves: Σ|μ with − μ without| / Σμ.
- The check "the data has moved away from an edit": the fitted value differs from the edited one by more than twice its range, on a level that is not thin.
- Read it as a cost. "No clear difference" is the good answer for an edit made on judgement.

**7.4 What a monotone direction costs.** Does the constraint fight the data? *[M without training; P of both runs with a what-if run · all four]*
- Without training: take the unconstrained effect, fit the nearest monotone curve by isotonic regression weighted by mass, and report the mass-weighted mean gap between the two. Near zero means the direction is free.
- With a what-if run: the R-diff of with against without, overall and by level of the factor, and A / E by level before and after. The score barely moves. The levels show where the cost lands (01 scenario 5).
- A model type that cannot honour the direction is marked in the standings (1.4). It is not like for like with the rest.
- Misleads: a non-monotone expected line can be a true effect of the mix. The check informs and does not judge (02-C5).

## 8. Small and large data

| | Small data: noise is the risk | Large data: time is the risk |
|---|---|---|
| Ranges | wide. Most comparisons read "no clear difference", and that is the truth | narrow. Everything is clear, so read the size as a share of the span |
| Bands | fewer, by the events rule (0.3); lift and double lift in 5 | 20 |
| Thorough run | the default | before logging |
| Resampling | the buckets are the groups | 2,000 buckets, at the same cost |
| Charts | computed on demand | one table of sums per run, factor and band (y, μ and m, by bucket half), built after training. A chart then costs the number of levels |
| Sampled analyses | none needed | estimated effects, SHAP, isotonic bands, the pair search and Cramér's V use up to 200,000 rows, in whole groups |
| Claim sizes | the largest claims decide scores: show their influence (3.1, 4.6) | as usual |

- **Sample runs sample the training rows only,** by whole groups, and score every validation row. A sample run and a full run are then judged on the same rows, and their R-diff says what the rest of the data buys. Today a sample is a uniform draw of rows with one fixed seed (`BE/routes/_training_preparation.py:93-106`).
- **Say what the data can show.** In the standings: the half-width of the R-diff between the two closest runs, as a share of the span. On 15,000 claims the analyst should know which gaps cannot be seen, before tuning for them.

## 9. An outside benchmark

Position: yes. Draw today's price as a reference line, and never treat it as a model.
- Why: the real question at logging is whether the new model beats what is charged today. With kept predictions a benchmark is one more column of predictions, so the fit view, lift, double lift, Lorenz and "where they disagree" all take it at no extra cost.
- It must be declared as one of two things. **A prediction of the target** (last year's model scored on these rows): used as it is, level included. **A price** (with expenses, discounts and caps in it): rescaled to balance on the validation rows, and judged on ranking and shape only. Its D and its balance are not shown.
- For a price, the telling chart is the Lorenz curve ordered by model over price: x = cumulative share of price, y = cumulative share of actual (Frees, Meyers and Cummings 2011, in 02's list; the construction is from my memory of the paper, not rechecked).
- It is drawn grey and dashed, has a marked row in the standings, and is never "the strongest". It has no effect view.
- Misleads, with guards. The benchmark was probably fitted on today's validation rows, so it is flattered: it carries the in-sample mark unless its cut-off date is given. A price is the wrong yardstick for a claim-count or claim-size node: the tool refuses a price there and takes only a prediction of that target.
- Today: rustystats takes `base_predictions` in its diagnostics (`RS/diagnostics/api.py:2134-2166`). Haute does not use it: I found no match for that name under Haute's `src/` or `frontend/src/`.

## 10. Priorities

**A first version must have.** Without these the numbers are not honest, or the loop has no direction.
1. The split fixed per node by row key, group and time together, and the inner slice for early stopping and tuning (2.1, 2.2).
2. Kept predictions for validation and training rows, with buckets and shared bands (0.2, 0.3).
3. D as the main score, the floor, R-diff, the "no clear difference" rule and the like-for-like marks (1).
4. The fit view with R-level, the "fits worst" sort and strength on its own (3.1 to 3.3), and exact effects for t-boost, the GLM and EBM on one centring (3.4).
5. Balance, lift on equal mass, the true double lift, and the floor-to-strongest bar (4.1 to 4.4).
6. The data checks that need only the data, and the in-sample mark (5).
7. Sealed test rows with the count of looks, and refit against reviewed (2.4, 2.5).
8. The hand-edit preview on the rows touched, with the balance shift and the cost (7.1, 7.3).

**Next.** The thorough run with R-spread and the fold vote (2.3). Estimated effects for gradient boosting (3.4). Scores by segment and period, and how far predictions move (4.7). Where two models disagree, and why (4.8). Interaction and time evidence (3.5, 3.6). The calibration curve with "off level" and "ranks risks" (4.2). The GLM panel with robust errors beside validation (6.2). The prune curve and the graduation check (6.1). Tuning diagnostics (6.4). The benchmark (9). The cost of a direction (7.4).

**Can wait.** Band and graduate curves, which need t-boost to apply a step again. Refitting around part of a table, which is library work (03 fact 10). Isotonic recalibration. A range on Gini. A second seed by default. The demand curve for a yes/no model with price as a factor, and the check that price can be told apart from the other factors (01 scenario 8).

---

## Needs from the UI

1. **One score cell, three parts:** the score, its difference from the reference, and the range of that difference. It has a fourth state, "no clear difference", with the raw number on hover.
2. **A reference picker** for the score cell: the run it replaced, a reference model, the floor or the benchmark.
3. **Tied models bracketed** in the standings, and a mark on any row that is not like for like, with the reason (sample, another split, a shared setting it ignored).
4. **One in-sample mark,** used on training-row views, the refit, GLM tests and a benchmark with no cut-off date.
5. **Whiskers on actual** at each level, a mark on a level only where expected is outside them, and the header line "3 of 24 outside, about 1 expected by chance".
6. **A method label on every effect line:** exact, or estimated and how. An axis label for the scale: × rate, or × odds. A faint one-way line behind the effect.
7. **A label for the range kind** wherever a band is drawn: "95% range" or "spread across folds".
8. **A row filter shown as a removable chip,** set by a double-lift band, a segment, or the rows an edit touches, and applied to every pane.
9. **The factor list's "fits worst" bars,** with "no clear misfit" factors greyed and last.
10. **A place for the row sets:** rows, mass and events for training, validation and test, plus the count of test looks.
11. **The fit-against-simplicity chart:** discrete points, each named, one marked as chosen, and flat stretches drawn flat.
12. **The edit preview led by the rows touched,** with the label "on the rows you were looking at" on any gain.
13. **The benchmark's line:** grey, dashed, with a legend entry that says "price, rescaled" or "prediction".
14. **A two-way heatmap** of A / E with thin cells greyed, opened from an interaction pair.
15. **Small charts per period on one shared axis** for the split by period.
16. **A "refit against reviewed" step** in the Logging panel, before the test rows.
17. **A line in the standings** saying what the data can show.

## Proposals and questions

**New proposals.**
- **A1.** The range goes on the difference between two runs, paired by row and resampled by whole groups through fixed buckets (0.4, 1.2).
- **A2.** "No clear difference" is a three-part rule: like for like, the range excludes zero, and the folds agree. Tied models are bracketed and not ranked (1.3).
- **A3.** The split is made once per node and stored by row key. Group and time can be used together. The validation rows are fold 1 of the thorough run (2.1, 2.3).
- **A4.** Early stopping and tuning use an inner slice of the training rows (2.2).
- **A5.** The headline score is always on validation rows. The thorough run adds stability and less noisy charts, and is never called a second honest score (2.3).
- **A6.** A run keeps up to four columns of predictions, and on large data one table of sums per factor and band (0.2, 8).
- **A7.** The range on actual at a level depends on the target (0.4).
- **A8.** "Fits worst" is the gain from correcting the model by level, learned on other rows (3.2).
- **A9.** One common effect: link scale, centred on the mass-weighted mean, exact or labelled as estimated. Accumulated local effects for gradient boosting. Partial dependence leaves the shared charts (3.4).
- **A10.** Interaction and time evidence: search on training rows, confirm on validation rows (3.5, 3.6).
- **A11.** Standings columns: gain over the floor, balance, off level, ranks risks, Gini. One Tweedie power for scoring. MAE is not offered for choosing a model (1.1, 4.2).
- **A12.** "Refit against reviewed" at logging, and the test result read against the range of the validation result (2.4, 2.5).
- **A13.** Sample runs sample the training rows only and score every validation row (8).
- **A14.** A hand edit laid on top is offered a rebalance of the model's base. A preview gain on validation rows is labelled and never counts in the standings (7.1, 7.2).
- **A15.** "Explain the difference" between two runs for chosen rows, by factor, exact for table models (4.8).
- **A16.** A benchmark column, declared as a prediction or as a price (9).
- **A17.** The standings say what the data can show (8).

**Changes to the Proposed items.**
- **2:** add A3 and A4. "Sealed" also covers band edges and data checks. The test result is shown with a range and a permanent count of looks.
- **3:** replace "a range on every score" with A1 and A2. Reword "thorough" by A5. Fold spread is not the range.
- **4:** say which predictions are kept (A6), and keep buckets and shared bands with them. "What changed" needs the replaced run's predictions for its paired difference: see the concern at the end.
- **5:** define the common shape by A9. "Exact" holds for the main effect only; a factor with interactions names its partners.
- **7:** the fit view carries R-level. The split by year uses A / E. The effect by year is a what-if run, not a split of the chart (3.5).
- **8:** define "fits worst" by A8. The reviewed tick clears when a later run changes that factor's measure clearly, not on any movement.
- **9:** "what changed" is a set of R-diffs. "Each factor left out in turn" is read as one ranked list with the false discoveries controlled (02-D1), not as separate verdicts.
- **10:** give each check a rule and a status, as in section 5. Add the leak check after fitting, the direction check, levels unseen in training and change over time.
- **11:** every GLM test carries the in-sample mark and sits beside the validation R-diff of the same change (6.2).
- **12:** add "refit against reviewed" before the test rows, and read the test result against validation (A12).
- **13:** defaults also set the number of bands and the lift bands. Sample runs follow A13.
- **16:** the preview leads with the rows touched and follows A14.
- **19:** for an edit laid on top, "which other tables moved" is empty by construction, and the pane must say so (7.2).
- No change proposed to 1, 6, 14, 15, 17 or 18.

**Open questions for the owner,** each with the default I assumed.
- **Q1.** Should a benchmark such as today's price be drawn beside the models? Default: yes, as a reference line, judged on ranking and shape only unless it is declared a prediction of the target.
- **Q2.** Which level for the ranges? Default: 95% everywhere.
- **Q3.** Is there a smallest gain that matters commercially? Default: none is set. Sizes are shown as a share of the floor-to-strongest span and the analyst judges.
- **Q4.** Is there a policy identifier on every dataset, to form groups? Default: the split's group column, and single rows when there is none.
- **Q5.** Should the test rows be the latest period whenever a date exists? Default: yes, suggested and not applied without the analyst.
- **Q6.** Are claim counts and claim sizes modelled in separate nodes, with the pure premium checked across them? Default: each node is judged alone in the first version.
- **Q7.** Are large claims capped before the data reaches the node? Default: yes, in pre-processing. The tool only shows their influence.
- **Q8.** Should a laid-on hand edit be rebalanced automatically? Default: offered, never automatic.
- **Q9.** Is a yes/no model with price as a factor in scope for the first version? Default: the shared analytics apply, and the demand curve waits.

## Concerns about settled items

- **"Training a model again overwrites its run."** The paired "what changed" needs the replaced run's predictions until the new training finishes, and a failed training must not lose them. This supports the analyst's concern C in `01-analyst-walkthrough.md`. Smallest change: the overwrite happens when the new run completes, and one replaced run is held until the next. No other settled line is touched by this draft.

**Housekeeping.** I ran one throwaway edit script from the session scratch folder, one level above the team folder, and deleted it straight after. Nothing under `/home/ralph/suite/` was touched.
