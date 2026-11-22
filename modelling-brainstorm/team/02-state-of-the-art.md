# 02. State of the art: what existing tools and the literature say

Researcher's report. Web search and fetch were available and used.

**How to read the evidence.**

- **Read directly** (page fetched, or PDF text extracted): the WTW Emblem deck, Dataiku's Visual GLM docs, Anderson et al., Goldburd et al., the Earnix blog, DataRobot's unlock-holdout and leaderboard pages, both GAM Changer papers, the `interpret` API docs, Slice Finder, Microsoft's error-analysis page, the Manifold blog, Fissler et al. (all but the last pages), the `model-diagnostics` API docs, Blum and Hardt, and the abstracts of Bates et al., Apley and Zhu, Delong and Wüthrich, and Wüthrich (2026).
- **Every other source was seen only as a search summary** of its abstract or docs page. For those I give the reference and no more than the summary showed. A quote from one of them is a quote from the summary.
- *(snippet only)* marks a page that refused the fetch and matters to the card. Treat it as a lead.
- **Not covered:** vendor interfaces are mostly not public. Akur8's product page names capabilities and shows no interface, and Emblem is known here from one slide deck. I did not look at EBM dashboards beyond the API docs, AutoGluon, H2O, or other pricing vendors. Experiment trackers got one search each.
- "Bears on" names the Proposed item in `before/overview.md` a card confirms or challenges.
- Card verdicts: **Adopt** (take as is), **Adapt** (take with a change), **Avoid**.
- One claim about Haute's environment is made, in card C5, with its evidence.
- Housekeeping: I extracted PDF text with a throwaway script in a scratch folder outside the team folder, and deleted that folder afterwards.

## A. Insurance pricing modelling tools

### A1. Three lines per factor: observed, fitted, and the model's effect at base levels
- **What:** One chart per factor showing the observed average, the fitted average, and the prediction with all other factors held at base level, over exposure bars. Emblem calls the third line "Model Prediction at Base Levels" and says it "represents the effect in the model of this variable, holding all other variables constant". Dataiku's Visual GLM draws the same three lines plus grey exposure bars. The `model-diagnostics` package's `plot_marginal` draws observed, predicted and partial dependence with a histogram.
- **Problem:** Fit alone hides a factor that fits well only because a correlated factor carries it.
- **Source:** WTW, "Evaluating models built in Emblem" (L. McGrath, 22 Feb 2022), posted publicly by the NAIC although its slides carry a WTW confidentiality footer, https://content.naic.org/sites/default/files/inline-files/Evaluating%20Emblem%20Models.pdf ; Dataiku docs, https://doc.dataiku.com/dss/14/machine-learning/generalized-linear-models/visual-glm.html ; https://lorentzenchr.github.io/model-diagnostics/api-reference/calibration/
- **Verdict:** Adapt. UI. Bears on 7. Three independent tools put fit and effect on **one** chart, not two views. Draw all three lines for the chosen model and only the expected line for the others. Keep the separate effect view for overlaying every model's effect.
- **Risk:** Three lines for each of six models is unreadable. Limit the third line to the chosen model.

### A2. Chart controls that pricing tools already agree on
- **What:** Dataiku's chart offers rescaling (none, base level, ratio), level order (natural, or by predicted or observed), top levels by exposure with the rest grouped as "other", training or test rows, and a second model drawn dashed with its relativities in a second table column.
- **Problem:** The same chart must answer "is it right", "what is it doing" and "what changed".
- **Source:** Dataiku docs (as A1).
- **Verdict:** Adopt. UI. Confirms the scale, rows and dashed reference ideas in `before/ui.md`. Add two things the design lacks: sorting levels by value, and grouping thin levels as "other" for display.
- **Risk:** Grouping for display must not be mistaken for a change to the factor.

### A3. Consistency over time by interacting one factor with year
- **What:** Fit a series of models, each interacting a single factor with year, and look for parallel lines. The guide warns against fitting separate models per year, because "a movement in one factor in one year may to a large extent be compensated for by a movement in another correlated factor".
- **Problem:** Tells a lasting effect from a one-year accident.
- **Source:** Anderson et al. (2007), paras 2.39 to 2.40, https://www.casact.org/sites/default/files/database/studynotes_anderson9.pdf
- **Verdict:** Adapt. Analytics. Bears on 7 and the "flips between years" check. Splitting the **fit** view by year from kept predictions is cheap and right. Splitting the **effect** by year needs the interaction fit: offer it as a what-if run ("this factor by year").
- **Risk:** A thin year gives lines that cross from noise alone. Show a range on each year's line.

### A4. Fix a value through the offset, then show who compensated
- **What:** A restricted relativity goes into the offset and the factor leaves the fit, so "Factors 2, 3, 4 have been allowed to compensate". The guide then compares restricted with unrestricted fitted values to "demonstrate the degree to which other factors have compensated". It also says to "assess the true effect of all factors" first, as ordinary factors, before restricting.
- **Problem:** A value laid on top of a fit leaves correlated factors wrong and the total out of balance.
- **Source:** Anderson et al. (2007), paras 2.110 to 2.116. Goldburd et al. (2020), section on offsets: "An offset is formally defined as a predictor whose coefficient is constrained to be 1."
- **Verdict:** Adopt. Analytics and workflow. Bears on 15, 16, 18, 19. This is long-standing GLM practice (the guide dates from 2004) and matches the design. It also backs "last, not first": fit free, read, then fix.
- **Risk:** Anderson notes a restriction is best imposed at the risk premium stage for "more complete and balanced compensation". An edit on a frequency model alone compensates only within frequency.

### A5. The machine proposes structure and the analyst owns the GLM
- **What:** Earnix AutoGLM starts "with granular bins and then merging where the data supports it", proposes "a short list of high-impact interactions", and returns a GLM that "remains fully editable". The maidrr method groups each factor's values using insights from a black-box model (a boosted model in the paper), then fits a GLM on the grouped factors and selected interactions. A 2026 WTW webcast describes a layered boosting model that guides factor selection for a GLM *(snippet only)*.
- **Problem:** Hand-building bands and hunting interactions is the slow part of a GLM.
- **Source:** Earnix blog, 29 Jan 2026, https://earnix.com/blog/automatic-generalized-linear-models-turbo-charging-interpretable-models-for-financial-services/ ; Henckaerts, Antonio and Côté (2022), https://arxiv.org/abs/2007.06894 ; https://www.wtwco.com/en-in/insights/2026/04/reinventing-insurance-modeling-machine-led-glm-in-action
- **Verdict:** Adopt. Workflow. Bears on 11. "The GLM borrows from the other models" is where the market and the literature have both gone.
- **Risk:** Bands copied from a boosted model copy its mistakes. Each borrowed band should show its own evidence on validation rows, as the design says.

### A6. Aggregate residuals before plotting them
- **What:** Emblem's "crunched residuals" group rows with similar predicted values and compute the residual per group, because row-level residuals on claim counts are too discrete to read. Goldburd et al. bin working residuals before plotting, because tens of thousands of points make "a very messy (and uninformative) scatterplot".
- **Problem:** A raw residual plot of claim counts shows stripes, not misfit.
- **Source:** WTW slide deck (as A1); Goldburd et al. (2020), section 6.3.2.
- **Verdict:** Adopt. Analytics. The residual chart should always be binned, with exposure-weighted bins.
- **Risk:** Low.

### A7. A band on every relativity and on actual at every level
- **What:** Anderson et al. chart each factor's fitted relativities with thin lines "two standard errors either side", read as roughly 95%, over exposure bars. The bands widen "where there is low exposure volume, where other correlated factors also explain the risk, or where the underlying experience is very variable". The same chart adds the one-way relativities as a dotted line, which part from the fitted line when factors are correlated. `model-diagnostics` gives a standard error for the average observed and the average bias in each bin (σ/√n) and draws error bars, 90% by default. Dataiku flags a level in yellow when its p-value is above 0.05 or its standard error exceeds its coefficient.
- **Problem:** Without a band, a thin level's wobble is read as a pattern, and a misfit smaller than its noise gets tuned.
- **Source:** Anderson et al. (2007), paras 2.27 to 2.33; `model-diagnostics` and Dataiku docs (as A1). Goldburd et al. (2020), section 6.4: "Many modelers prefer bootstrapped confidence intervals to the estimated confidence intervals produced by statistical software."
- **Verdict:** Adopt. Analytics and UI. Bears on 3 and 7. Put the band on **actual** in the fit view (it holds for every model type) and on the **relativity** in the effect view where the model type gives one (the GLM directly, others by bootstrap or folds). Colour a level only when expected falls outside actual's band.
- **Risk:** Anderson notes the bands are relative to the base level, so a thin base level widens every band. Claim sizes are skewed, so in my judgement σ/√n is unreliable for severity. Use a bootstrap there.

## B. General machine-learning platforms

### B1. A locked holdout with a one-way unlock
- **What:** DataRobot hides the holdout score behind a lock. Its docs say "You should only unlock your holdout data after having made all your model-related decisions" and "Once your project's holdout has been unlocked, it cannot be re-locked."
- **Problem:** A holdout that is looked at during selection stops being a holdout.
- **Source:** https://docs.datarobot.com/en/docs/classic-ui/modeling/build-models/build-basic/unlocking-holdout.html.md
- **Verdict:** Adopt. Workflow. Bears on 2 and 12. The design's "each look is recorded" is softer than this precedent. Show the count of looks in the logging panel and in the model card, for good.
- **Risk:** After one look the test rows are spent for the next round of work. Plan how they are refreshed, for example a newer period.

### B2. Mark every score that includes rows the model learned from
- **What:** DataRobot puts an asterisk on a validation or holdout score when the model was trained on some of those rows. Hover gives the reason.
- **Problem:** In-sample numbers sit beside honest ones and are read as equal.
- **Source:** https://docs.datarobot.com/en/docs/reference/pred-ai-ref/leaderboard-ref.html.md
- **Verdict:** Adopt. UI. Supports principle 1. One mark, used everywhere: the training-rows view, any chart of the final refit, and any score from a model that stopped early on the rows shown.
- **Risk:** The mark is ignored if it appears on most numbers. Keep in-sample views rare.

### B3. A leaderboard with one sort metric, and its trap
- **What:** DataRobot's leaderboard orders models by one chosen metric, with validation, cross-validation and holdout columns, tags, a star, and cross-validation run on demand for large data.
- **Problem:** Gives the standing of many models at a glance.
- **Source:** DataRobot leaderboard reference (as B2). Cawley and Talbot (2010).
- **Verdict:** Adapt. UI. Bears on 3 and the standings. Cawley and Talbot show that over-fitting the selection criterion gives effects "of comparable magnitude to differences in performance between learning algorithms". So do not rank models whose gap is inside the range: show them as tied.
- **Risk:** A sorted table invites picking row one. See trap 1.

### B4. Show only what differs between two runs
- **What:** Weights & Biases' run comparer has a "Diff only" toggle that hides identical values. MLflow's chart view offers parallel coordinates for many runs.
- **Problem:** A full settings list hides the one line that changed.
- **Source:** https://docs.wandb.ai/models/app/features/panels/run-comparer ; https://docs.databricks.com/aws/en/mlflow/visualize-runs
- **Verdict:** Adopt diff-only for 9 ("what changed from the run it replaced"). Avoid parallel coordinates: they suit hundreds of tuning trials, and a node holds a handful of runs. UI.
- **Risk:** Low.

### B5. Checks as named conditions with a status, run before modelling starts
- **What:** Deepchecks attaches conditions to checks, each giving pass, fail or warning in one summary table. Its leakage checks include a single factor's predictive power. DataRobot's standing data quality checks include target leakage, scored from each factor's own Gini against the target.
- **Problem:** Leaks and broken columns are cheapest to catch before the first fit.
- **Source:** Deepchecks docs *(snippet only)*, https://deepchecks.readthedocs.io/en/stable/examples/guides/create_a_custom_suite.html ; https://docs.datarobot.com/en/docs/reference/data-ref/data-quality-ref.html
- **Verdict:** Adopt. Workflow. Bears on 10. Confirms "look before fitting" with a leak check from one-factor strength. Give each check a threshold and a status, not only a message.
- **Risk:** Too many warnings get ignored. A review of 23 clinical alert studies found average override rates from 46.2% to 96.2% (Poly et al. 2020, https://medinform.jmir.org/2020/7/e15653/). That is another field, so read it as a caution, not a number for this tool. Keep the list short and silent when all is well.

## C. Workbenches for interpretable models

### C1. A small kit of edit tools on a selected run of levels
- **What:** GAM Changer offers move, interpolate (a straight line across the selection), make monotone (isotonic regression weighted by bin counts, for least change), align to a neighbour, and delete (set to zero). Each edit is committed or discarded.
- **Problem:** Typing values level by level is slow and gives ragged curves.
- **Source:** Wang et al. (2021), https://ar5iv.arxiv.org/html/2112.03245
- **Verdict:** Adopt. UI. Bears on 14 and 17. It matches the design's kinds of edit. Add "make monotone over this range" as an edit kind, since weighting by exposure gives the smallest change.
- **Risk:** Easy tools make edits cheap. See C4.

### C2. Score an edit on the rows it touches, not only overall
- **What:** GAM Changer's metric panel has three scopes: all rows, the rows in the selected region, and a chosen slice. It colours the original, the last edit and the current model. The follow-up paper found global metrics "not very sensitive to edits that slightly change a few bins".
- **Problem:** The overall score barely moves for any local edit, so it says nothing about whether the edit is good.
- **Source:** Wang et al. (2021); Wang et al. (2022), https://ar5iv.labs.arxiv.org/html/2206.15465
- **Verdict:** Adopt. Analytics and UI. Bears on 16. The preview should lead with actual against expected on the edited levels, before and after, and give the overall score second.
- **Risk:** Few rows in the edited levels means a wide range. Show it.

### C3. Show which other factors an edit will disturb, before retraining
- **What:** GAM Changer finds the rows an edit touches, compares each other factor's mix in those rows with its mix in all rows, and lists the factors by that distance.
- **Problem:** An edit to one factor moves predictions for a group that is skewed on other factors, and the analyst cannot see which.
- **Source:** Wang et al. (2021), "linking+reordering" in the Feature Panel.
- **Verdict:** Adopt. Analytics. Bears on 16. It forecasts "which other tables moved" at preview time, from data alone, with no training.
- **Risk:** It shows association, not the size of the later compensation.

### C4. Edits as commits, a drafted note, and confirm-all before saving
- **What:** Each edit is a commit with a time, an id and a message. The tool drafts the message and the user adds the reason. Earlier versions can be checked out. "GAM Changer requires users to examine and confirm all edits before saving the model."
- **Problem:** Unrecorded edits cannot be defended or undone.
- **Source:** Wang et al. (2022). A study participant warned: "Anyone behind the model can just add whatever relationship they want." The authors state the tool does "not guarantee to prevent users from overfitting the model".
- **Verdict:** Adopt. Workflow. Bears on 15 and 12. Draft the "what" of each note from the edit itself and leave the analyst the "why". Make the logging panel's edit list a confirm-each step.
- **Risk:** The study had seven participants and one task. It shows the pattern is usable, not that it prevents bad edits.

### C5. EBM edits that already exist, and a warning on monotonicity
- **What:** The EBM library documents post-fit `monotonize`, `remove_terms`, `remove_features`, `scale` and `sweep`. Its docs recommend applying monotonicity after the fit, because during fitting "the boosting algorithm may compensate for a monotone constraint on one feature by utilizing another correlated feature".
- **Problem:** A direction set in training can be honoured in the model's table and still broken in what the customer pays.
- **Source:** https://interpret.ml/docs/python/api/ExplainableBoostingRegressor.html . The `interpret` 0.7.8 installed in Haute's environment at `/home/ralph/suite/haute/.venv/lib/python3.11/site-packages/interpret/` has all five on `ExplainableBoostingRegressor` (checked with `hasattr`). Whether Haute calls any of them is unverified.
- **Verdict:** Adopt. Analytics. Add one check for every model type: on a factor with a direction, is the expected line on validation rows monotone too? XGBoost's docs add that a strong constraint may need more split candidates (`max_bin`), https://xgboost.readthedocs.io/en/stable/tutorials/monotonic.html
- **Risk:** A non-monotone expected line can also be a true mix effect. The check informs and does not judge.

## D. Error analysis and slice discovery

### D1. Three tests before a segment is called a problem
- **What:** Slice Finder defines a slice as a conjunction of a few factor-value pairs and keeps it only if it is large, has an effect size above a threshold, and passes a significance test. It controls false discoveries across the many slices tested with alpha-investing, which it says is "the only technique that works in an interactive setting". Its screen is a scatter of slice size against effect size, a linked table, and sliders for the threshold and the number shown.
- **Problem:** Search enough segments and some will look bad by chance.
- **Source:** Chung et al., https://ar5iv.arxiv.org/html/1807.06068 . SliceLine (Sagadeeva and Boehm, SIGMOD 2021) makes the exact search fast, https://sliceline.readthedocs.io/en/latest/Slicefinder.html
- **Verdict:** Adopt. Analytics. Bears on 8 and "where they disagree". Rank by effect size and exposure together, limit a segment to one or two factors, and state the threshold.
- **Risk:** On large data everything is significant, so the effect-size threshold does the work. On small data nothing is, so say "no segment stands out".

### D2. Share of total misfit, and a two-factor heatmap
- **What:** Microsoft's Error Analysis describes each cohort by its error rate, its **error coverage** ("the portion of all errors that fall into the node") and its size. A heatmap slices error by one or two chosen factors.
- **Problem:** The worst level is often a tiny one. What matters is how much of the total misfit a factor holds.
- **Source:** https://learn.microsoft.com/en-us/azure/machine-learning/concept-error-analysis
- **Verdict:** Adapt. Analytics and UI. Bears on 8. Sort the factor list by each factor's share of the model's total misfit, not by its worst level. Use the two-factor heatmap of actual over expected as the interaction view.
- **Risk:** A heatmap of thin cells is mostly noise. Grey out cells below an exposure floor.

### D3. From "where two models disagree" to "which factors explain it"
- **What:** Manifold is model-agnostic, so it is the same for any model type. It needs only each model's output, the actual outcome and the data. Its steps are Compare, Slice, Attribute: plot the metric by segment with one colour per model, pick two segments, then rank factors by how differently they are distributed in the two. Segments are formed by clustering rows on how well each model does on them.
- **Problem:** A double lift shows that two models disagree, and not where to look next.
- **Source:** Uber Engineering, 14 Jan 2019, https://www.uber.com/gb/en/blog/manifold
- **Verdict:** Adopt. Analytics. Bears on 4 and the comparison pane. Take the rows at the two ends of the double lift and list the factors that most separate them from the rest. Each row of that list opens the factor chart. This joins the comparison to the loop.
- **Risk:** Same as C3: association only.

### D4. Overfitting by region, not as one number
- **What:** PiML's diagnostics include weak regions found by one- and two-factor slicing of residuals, and regions where the gap between training and test performance is widest. It compares two or three models side by side.
- **Problem:** One overall gap between training and validation hides where a model memorised.
- **Source:** PiML Toolbox, arXiv:2305.04214, https://arxiv.org/pdf/2305.04214
- **Verdict:** Adapt. Analytics. Bears on 10. Report the overfitting check by factor level, linked to that factor's chart.
- **Risk:** More numbers to scan. Show only the worst few.

### D5. Named segments that every run is scored on
- **What:** Zeno lets a team define named slices and keeps them as a test suite that each new model is scored against.
- **Problem:** Segments the business cares about get rechecked by hand, or forgotten.
- **Source:** Cabrera et al. (CHI 2023), https://arxiv.org/pdf/2302.04732
- **Verdict:** Adopt. Workflow. Bears on "by segment and by year" in the comparison. Segments named in advance are not a search, so in my view the false-discovery problem of D1 is much smaller for them. Store them in the node's config.
- **Risk:** The list grows. Cap it or it becomes another ignored table.

## E. Literature on judging pricing models

### E1. The double lift, as the monograph defines it
- **What:** Sort rows by the ratio of model A's prediction to model B's, cut into quantiles, and plot A's average, B's average and the actual average, each divided by its overall average. "The winning model is the one that more closely matches the actual pure premium in each quantile." The end quantiles hold the rows "on which Models A and B disagree the most".
- **Problem:** Two models with the same overall score can price individual risks very differently.
- **Source:** Goldburd et al. (2020), section 7.2.2. DataRobot ships a similar chart, "Dual Lift", binned on the difference between two models' predictions, https://docs.datarobot.com/en/modeling/analyze-models/other/model-compare.html
- **Verdict:** Adopt as defined. Analytics. Use equal-exposure quantiles and validation rows. DataRobot recommends the chart for choosing models to blend: that use is out of scope, since ensembles are out.
- **Risk:** With few rows the end quantiles are noisy. Put a range on actual.

### E2. Gini ranks and says nothing about level
- **What:** The Gini index has a standard error (Frees et al. prove asymptotic normality). But it only ranks. Wüthrich shows it is a sound selection rule only among auto-calibrated models. Delong and Wüthrich conclude that Gini and related Lorenz-curve scores "may lead to misleading evaluation of point predictions when used for model selection". Fissler et al. advise choosing "the scoring function in model comparison in line with this target functional": for a mean, the Poisson, Gamma or Tweedie deviance.
- **Problem:** A model can top a Gini table while charging everyone 10% too little.
- **Source:** Frees, Meyers and Cummings (2011); Wüthrich (2023), https://arxiv.org/pdf/2207.14372 ; Delong and Wüthrich (2026), https://arxiv.org/abs/2605.12679 ; Fissler, Lorentzen and Mayer (2022), https://arxiv.org/abs/2202.12780v3
- **Verdict:** Adopt. Analytics. Bears on the standings and the shared metrics. The headline score is the deviance that matches the shared loss. Gini is shown only beside a calibration measure.
- **Risk:** Analysts and managers expect Gini. Keep it visible and put it second.

### E3. Calibration at three levels, without choosing bins
- **What:** Fissler et al. separate overall calibration (average bias near zero), calibration by factor (average bias "around 0 for all values"), and auto-calibration (a reliability diagram where "the graph is the diagonal line"). The CORP method draws the reliability diagram by isotonic regression, with no bin choice and bootstrap bands. `model-diagnostics` implements bias plots with error bars (default level 0.9) and CORP diagrams for several models.
- **Problem:** Binned calibration charts change shape with the bin count, and analysts read the changes as signal.
- **Source:** Fissler et al. (2022); Dimitriadis, Gneiting and Jordan (2021), https://pmc.ncbi.nlm.nih.gov/articles/PMC7923594 ; `model-diagnostics` docs (as A1). Wüthrich (2026), https://arxiv.org/abs/2609.26445, adds that policy-weighted and exposure-weighted measurement differ, so each chart must state its weight.
- **Verdict:** Adopt. Analytics. The factor chart's ratio scale is calibration by factor. Make the comparison's calibration chart a CORP diagram with a band.
- **Risk:** Isotonic curves look stepped and unfamiliar. A one-line caption fixes it.

### E4. Boosted models can lose balance, and there is a standard repair
- **What:** Denuit et al. show boosting trained on deviance can leave total fitted off total observed, and that balance should hold locally, not only in total. Wüthrich and Ziegel restore auto-calibration by isotonic recalibration, which assumes only that the model's ranking is roughly right and has no settings. Lindholm et al. build a locally balanced, piecewise-constant premium from any model.
- **Problem:** The check "total expected is off total actual" has a known cause and a known cure for the gradient-boosting model types.
- **Source:** Denuit, Charpentier and Trufin (2021), https://arxiv.org/pdf/2103.03635 ; Wüthrich and Ziegel (2024), https://arxiv.org/pdf/2301.02692 ; Lindholm, Lindskog and Palmquist (2023).
- **Verdict:** Adapt. Analytics and workflow. Bears on 10. Give the balance check a fix: an optional "rebalance" step in the menu of the model types that need it.
- **Risk:** A recalibration fitted on validation rows spends them. Fit it on out-of-fold predictions from training rows.

### E5. Split the score into miscalibration and discrimination
- **What:** A consistent score splits into miscalibration, discrimination and uncertainty. Uncertainty is the largest part and no model can remove it. Fissler et al. recommend reporting "skill scores", meaning the score relative to a baseline.
- **Problem:** A raw deviance says which model is better, and not why or by how much of what is achievable.
- **Source:** Fissler et al. (2022); Delong and Wüthrich (2026); `model-diagnostics` (`decompose`).
- **Verdict:** Adopt. Analytics. Bears on the floor-to-ceiling bar. The no-factor model is the skill baseline. Two extra columns in the standings tell an analyst whether to fix the level or the ranking.
- **Risk:** Three new terms. Label the columns "off level" and "ranks risks".

### E6. Put the range on the difference between two runs
- **What:** To test whether one model beats another, Fissler et al. use a t-test on the per-row score **differences** on the same rows (a Diebold-Mariano test). Bates et al. show the usual cross-validation interval is too narrow because folds share data: an interval built for 10% miscoverage missed 31% of the time in one of their examples (figure from a search summary, not the abstract). Nadeau and Bengio give a variance correction for resampled comparisons.
- **Problem:** Two overlapping ranges do not mean "no difference", and a fold spread overstates certainty.
- **Source:** Fissler et al. (2022); Bates, Hastie and Tibshirani (2024), https://arxiv.org/abs/2104.00673 ; Nadeau and Bengio (2003).
- **Verdict:** Adapt. Analytics. Bears on 3. Replace "a range on every score" as the main device with "a range on every difference", computed by resampling the paired per-row differences on validation rows. Resample whole policies: Fissler et al. note rows sharing a policy are "likely correlated".
- **Risk:** A holdout range covers the luck of the validation rows, and not the luck of the training rows. Say so in the tooltip.

### E7. Looking spends the validation rows too
- **What:** Goldburd et al.: the test set must "be used sparingly", and "the validation set will also diminish in usefulness if it is overused". Blum and Hardt's Ladder releases a new leaderboard score only if it is "below the previous best by some margin"; otherwise it repeats the previous best. That stops competitors fitting the holdout. Dwork et al. formalise reuse of a holdout across adaptive analyses.
- **Problem:** Forty rounds of "change one thing, read the validation chart" fit the validation rows.
- **Source:** Goldburd et al. (2020), section 4.3.3; Blum and Hardt (2015), https://proceedings.mlr.press/v37/blum15.html ; Dwork et al. (2015); the link is to the authors' longer companion preprint, https://arxiv.org/pdf/1506.02629
- **Verdict:** Adopt the Ladder idea, which is principle 5 with a guarantee behind it: the standings move only when a gain exceeds the range. Avoid the noise-adding mechanism of Dwork et al.: too opaque for analysts. Workflow.
- **Risk:** Analysts may read "no change shown" as a bug. Show the raw number on hover.

### E8. Cross-validation is not honest about choices made by hand
- **What:** Goldburd et al.: cross-validation gives a good estimate only when every model-building step is repeated in each fold. With hand-selected variables "proper cross validation is nearly impossible", so "splitting the data at the outset and retaining that split throughout ... is the preferred approach". They prefer a test set from "different time periods than the training set". They also use fold-to-fold and bootstrap spread of parameter estimates, and influential records, to judge stability. Akur8's help notes that selection can differ by fold in its grid search *(snippet only)*.
- **Problem:** The design's thorough evaluation refits fixed settings in each fold. The settings were chosen by looking at all those rows.
- **Source:** Goldburd et al. (2020), sections 4.3 and 6.4; https://doc.akur8-tech.com/en/articles/430426-exploring-and-comparing-multiple-modeling-strategies
- **Verdict:** Adapt. Workflow and analytics. Bears on 2, 3, 13. Present the thorough evaluation as what it is: less noisy charts and a view of **stability** (does each relativity hold across folds). It is not a second honest score. The sealed test rows, ideally a later period, stay the only honest one.
- **Risk:** Calling the thorough depth "honest" would undo principle 1.

### E9. The effect of a factor in a boosted model: partial dependence misleads
- **What:** Apley and Zhu show partial dependence plots "can produce erroneous results if the predictors are strongly correlated", because they predict at combinations of values that do not occur. Accumulated local effects avoid this and are cheaper.
- **Problem:** Rating factors are strongly correlated (age and no-claims years). The effect view would compare an exact GLM relativity with a distorted boosted one.
- **Source:** Apley and Zhu (2020).
- **Verdict:** Adapt. Analytics. Bears on 5. Compute the estimated effect for gradient-boosting model types by accumulated local effects, and label every estimated effect as estimated.
- **Risk:** An accumulated effect has no natural base level. Centre it the same way as the tables (exposure-weighted average).

### E10. Finding the interactions a simple model is missing
- **What:** Friedman and Popescu give a statistic for the strength of an interaction and a reference distribution for it. Lou et al. rank all pairs quickly (FAST) to pick the few worth adding. Schelldorfer and Wüthrich nest a GLM in a flexible model that starts from the GLM fit, to "explore model structure not captured" by it.
- **Problem:** A GLM only has the interactions someone thought to add.
- **Source:** Friedman and Popescu (2008), https://arxiv.org/pdf/0811.1679 ; Lou et al. (2013); Schelldorfer and Wüthrich (2019).
- **Verdict:** Adopt. Workflow. Bears on 11. Offer "what is this model missing": fit a small boosted model with the chosen model's prediction as the offset, and list the factor pairs it leans on, each opening the two-factor heatmap of D2.
- **Risk:** A boosted fit on residuals finds noise happily. Rank pairs on validation rows and apply D1's three tests.

### E11. Choosing a GLM term's shape from binned partial residuals
- **What:** The partial residual plot adds a factor's own term back to the residual, bins it, and shows the shape the term should take. Goldburd et al. use it to choose between bands, a log, a polynomial and a hinge.
- **Problem:** Choosing a curve by trial refits is slow.
- **Source:** Goldburd et al. (2020), sections 5.4.1 and 6.3.2.
- **Verdict:** Adopt. Analytics. Belongs in the GLM's own panes, beside the term settings.
- **Risk:** Low.

## F. Interface patterns for dense analytical tools

### F1. Three ways to compare: side by side, overlaid, or as a difference
- **What:** Gleicher et al. group all comparison designs into juxtaposition, superposition and explicit encoding of the difference. Small multiples are the juxtaposed form and depend on a shared scale.
- **Problem:** Each form fails differently: overlays clutter, side-by-side strains memory, differences hide the level.
- **Source:** Gleicher et al. (2011). Javed, McDonnel and Elmqvist (2010) tested overlaid against split line charts; I did not check their results, https://cs.au.dk/~elm/pdf/multilinevis.pdf
- **Verdict:** Adopt as a rule. UI. Overlay models on one factor chart. Use small multiples with one shared axis for the split by year. Use the ratio of actual to expected as the explicit difference. With more than about four lines, mute all but the chosen model and one reference.
- **Risk:** The "about four" is my judgement, not a sourced number.

### F2. Overview first, then filter, then detail on demand
- **What:** Shneiderman's seven tasks are overview, zoom, filter, details-on-demand, relate, history and extract. Progressive disclosure defers rarely used controls to a second step.
- **Problem:** A screen that shows everything shows nothing first.
- **Source:** Shneiderman (1996), https://drum.lib.umd.edu/handle/1903/466 ; Nielsen's definition *(snippet only, via a secondary source)*.
- **Verdict:** Adopt. UI. Supports "quiet by default". The design lacks an overview: nothing shows all factors' fit at once. A small misfit mark beside each factor name would give one. "History" and "extract" back the run list and the values table.
- **Risk:** Marks in the factor list must stay marks, since the list is for choosing a factor.

### F3. Linked selection across every view
- **What:** Selecting in one view highlights or filters in the others. Gamut links a model's shapes, its importances and single-row explanations. Slice Finder links its scatter and table.
- **Problem:** Unlinked views make the analyst carry the selection in their head.
- **Source:** Roberts (2007), https://kar.kent.ac.uk/14569 ; Hohman et al. (2019).
- **Verdict:** Adopt. UI. The design links factor and model. Extend it to **rows**: choosing a double-lift bucket, a segment or the levels of an edit filters the factor chart to those rows. Kept predictions make this cheap.
- **Risk:** A hidden row filter misleads. Show it as a removable chip above the chart.

### F4. Keyboard and command palette: thin evidence
- **What:** Pattern guides agree on arrow keys to move, a palette on Ctrl or Cmd+K, and showing each shortcut beside its control. I found no primary research.
- **Problem:** Reviewing forty factors by mouse is slow.
- **Source:** Design blogs only *(snippet only)*: https://knock.app/blog/how-to-design-great-keyboard-shortcuts ; https://command.ai/blog/command-palette-past-present-and-future
- **Verdict:** Adopt arrow keys through factors and a key to tick "reviewed". Treat a command palette as optional. UI.
- **Risk:** Shortcuts nobody discovers. Show them in tooltips.

## Concerns about settled items

None needs a settled line changed. Two watch-points:

- **"Factors and monotonicity are shared by every model."** Model types honour a direction differently, and a boosted model can route around it through a correlated factor (C5). Smallest addition: the per-model monotone check in C5. The settled line stands.
- **"Clicking a factor charts actual against expected for every fitted model."** One overlaid chart clutters once reference models are added (F1). Smallest addition: default emphasis on the chosen model and one reference. The settled line stands.
- Goldburd et al. and DataRobot both recommend blending close models. Ensembles are out, so this is not pursued.

## The ten patterns most worth adopting

1. **Observed, expected and effect-at-base on one chart** for the chosen model, with a band on actual at every level (A1, A2, A7).
2. **Deviance that matches the loss as the headline score.** Gini second, never without calibration (E2).
3. **A range on the difference between two runs,** paired by row and resampled by policy (E6). Standings move only when the gain clears it (E7).
4. **Sealed test rows with a permanent count of looks,** and one mark on every in-sample number (B1, B2).
5. **Calibration at three levels,** with a balance check that offers a rebalance step for boosted model types (E3, E4).
6. **The hand-edit kit:** range tools, scores on the rows touched, the factors disturbed, a drafted note, confirm-each at logging (C1 to C4), and restricted against unrestricted (A4).
7. **From disagreement to factors:** take the ends of the double lift and rank the factors that set those rows apart (D3, E1).
8. **Segment rules:** size, effect size and false-discovery control before anything is flagged, and sort factors by share of total misfit (D1, D2).
9. **Consistency over time** through a factor-by-year what-if, read as parallel lines (A3).
10. **The machine proposes and the analyst decides:** borrowed bands and a "what is this model missing" run for interactions (A5, E10).

## The five traps to avoid

1. **Ranking on Gini, or picking the top row on a gap inside the range.** Gini ignores level, and selection on noisy scores overfits (E2, B3).
2. **Calling cross-validation honest, or using fold spread as the range.** Hand choices saw every fold, and fold intervals are too narrow (E6, E8).
3. **Trusting partial dependence, or a trained-in direction, when factors are correlated.** Both mislead in exactly the pricing case (E9, C5).
4. **Unfiltered segment search and long check lists.** Both produce noise that analysts learn to ignore (D1, B5).
5. **Judging a hand edit by the overall score.** It will not move. The cost shows only on the rows touched and in the factors that compensate (C2, C3, A4).

## Reference list of methods for the analytics designer

- **Deviance as a consistent score; calibration tests; skill scores; score decomposition; Murphy diagrams; paired tests of score differences.** Fissler, T., Lorentzen, C. and Mayer, M. (2022). Model Comparison and Calibration Assessment: User Guide for Consistent Scoring Functions in Machine Learning and Actuarial Practice. arXiv:2202.12780.
- **Lift, double lift, loss ratio charts, Gini, actual against predicted, offsets, splits, binned working and partial residuals, stability, bootstrap bands.** Goldburd, M., Khare, A., Tevet, D. and Guller, D. (2020). Generalized Linear Models for Insurance Rating, 2nd edition. CAS Monograph No. 5. Casualty Actuarial Society. https://www.casact.org/monograph/cas-monograph-no-5
- **Standard-error bands on relativities, factor by time, restrictions through the offset.** Anderson, D., Feldblum, S., Modlin, C., Schirmacher, D., Schirmacher, E. and Thandi, N. (2007). A Practitioner's Guide to Generalized Linear Models, 3rd edition. CAS Study Note.
- **Gini with a standard error.** Frees, E. W., Meyers, G. and Cummings, A. D. (2011). Summarizing Insurance Scores Using a Gini Index. Journal of the American Statistical Association 106(495), 1085-1098.
- **Gini is consistent only under auto-calibration.** Wüthrich, M. V. (2023). Model selection with Gini indices under auto-calibration. European Actuarial Journal. arXiv:2207.14372.
- **Lorenz-curve scores against consistent scores.** Delong, Ł. and Wüthrich, M. V. (2026). Measures of predictive accuracy, miscalibration and discrimination. arXiv:2605.12679.
- **Lorenz and concentration curves for model choice.** Denuit, M., Sznajder, D. and Trufin, J. (2019). Model selection based on Lorenz and concentration curves, Gini indices and convex order. Insurance: Mathematics and Economics 89, 128-139.
- **Weighting in validation charts.** Wüthrich, M. V. (2026). A Practical Guide on Graphical Model Validation. arXiv:2609.26445.
- **Balance and auto-calibration for boosted models.** Denuit, M., Charpentier, A. and Trufin, J. (2021). Autocalibration and Tweedie-dominance for insurance pricing with machine learning. Insurance: Mathematics and Economics 101, 485-497.
- **Isotonic recalibration.** Wüthrich, M. V. and Ziegel, J. (2024). Isotonic recalibration under a low signal-to-noise ratio. Scandinavian Actuarial Journal 2024(3), 279-299.
- **Local balance from any model.** Lindholm, M., Lindskog, F. and Palmquist, J. (2023). Local bias adjustment, duration-weighted probabilities, and automatic construction of tariff cells. Scandinavian Actuarial Journal 2023(10), 946-973.
- **Reliability diagrams without bins (CORP).** Dimitriadis, T., Gneiting, T. and Jordan, A. I. (2021). Stable reliability diagrams for probabilistic classifiers. PNAS 118(8).
- **Intervals from cross-validation.** Bates, S., Hastie, T. and Tibshirani, R. (2024). Cross-validation: what does it estimate and how well does it do it? Journal of the American Statistical Association 119, 1434-1445.
- **Corrected variance for resampled comparisons.** Nadeau, C. and Bengio, Y. (2003). Inference for the Generalization Error. Machine Learning 52, 239-281.
- **Over-fitting in selection.** Cawley, G. C. and Talbot, N. L. C. (2010). On Over-fitting in Model Selection and Subsequent Selection Bias in Performance Evaluation. Journal of Machine Learning Research 11, 2079-2107.
- **Leaderboards that resist overfitting.** Blum, A. and Hardt, M. (2015). The Ladder: A Reliable Leaderboard for Machine Learning Competitions. ICML, PMLR 37, 1006-1014.
- **Holdout reuse.** Dwork, C., Feldman, V., Hardt, M., Pitassi, T., Reingold, O. and Roth, A. (2015). The reusable holdout: Preserving validity in adaptive data analysis. Science 349(6248), 636.
- **Accumulated local effects.** Apley, D. W. and Zhu, J. (2020). Visualizing the effects of predictor variables in black box supervised learning models. Journal of the Royal Statistical Society Series B 82(4), 1059-1086.
- **Interaction strength.** Friedman, J. H. and Popescu, B. E. (2008). Predictive learning via rule ensembles. Annals of Applied Statistics 2(3), 916-954.
- **Ranking pairwise interactions (FAST).** Lou, Y., Caruana, R., Gehrke, J. and Hooker, G. (2013). Accurate Intelligible Models with Pairwise Interactions. KDD '13, 623-631.
- **What a GLM misses.** Schelldorfer, J. and Wüthrich, M. V. (2019). Nesting Classical Actuarial Models into Neural Networks. SSRN 3320525.
- **A GLM built from a boosted model.** Henckaerts, R., Antonio, K. and Côté, M.-P. (2022). When stakes are high: Balancing accuracy and transparency with Model-Agnostic Interpretable Data-driven suRRogates. Expert Systems with Applications 202, 117230.
- **Boosted trees against GLMs for tariffs.** Henckaerts, R., Côté, M.-P., Antonio, K. and Verbelen, R. (2021). Boosting insights in insurance tariff plans with tree-based machine learning methods. North American Actuarial Journal 25(2), 255-285.
- **Slice search with false-discovery control (Slice Finder).** Chung, Y., Kraska, T., Polyzotis, N., Tae, K. and Whang, S. E. (2019). Automated Data Slicing for Model Validation: A Big data - AI Integration Approach. IEEE Transactions on Knowledge and Data Engineering. DOI 10.1109/TKDE.2019.2916074. arXiv:1807.06068. *(Year taken from the DOI.)*
- **Editing additive models.** Wang, Z. J., Kale, A., Nori, H., Stella, P., Nunnally, M. E., Chau, D. H., Vorvoreanu, M., Wortman Vaughan, J. and Caruana, R. (2022). Interpretability, Then What? Editing Machine Learning Models to Reflect Human Knowledge and Values. KDD '22. arXiv:2206.15465. Earlier version by the same authors: GAM Changer: Editing Generalized Additive Models with Interactive Visualization, arXiv:2112.03245 (2021).
- **Visual comparison.** Gleicher, M., Albers, D., Walker, R., Jusufi, I., Hansen, C. D. and Roberts, J. C. (2011). Visual comparison for information visualization. Information Visualization 10(4), 289-309.
- **Tasks of a visual tool.** Shneiderman, B. (1996). The Eyes Have It: A Task by Data Type Taxonomy for Information Visualizations. IEEE Symposium on Visual Languages, 336-343.
- **Linked views.** Roberts, J. C. (2007). State of the Art: Coordinated & Multiple Views in Exploratory Visualization. CMV 2007, 61-71.
- **Software to read, not papers:** `model-diagnostics` (bias plots, CORP diagrams, score decomposition), https://lorentzenchr.github.io/model-diagnostics/api-reference/calibration/ ; `interpret` (EBM editing), https://interpret.ml/docs/python/api/ExplainableBoostingRegressor.html
