# 01 Analyst walkthrough

Role: the pricing analyst. I read `before/overview.md`, `before/workflow.md` and `before/ui.md`
only, and walked the design as a user.

- I did not read Haute's code. Anything here about what Haute does today is taken from the three
  files and is **unverified**. Methods I rely on are my own practice, not claims about a tool.
- Tags: KEEP, GAP (the files are silent), RISK (a mistake the tool would not catch), SLOW (steps
  counted), MISSING (something I rely on), CONFLICT (the files disagree).

## The walkthroughs

### 1. First model: motor claim frequency, 2 million rows, 60 factors

1. Open the node from its card. I see a menu row with no model types. GAP: the empty state is not
   described. Which menu is open, and what tells me to start with setup?
2. All models menu, sections 1 to 4. Target = claim count. Then one box for "exposure or weight" and
   another for "offset". RISK: a count target needs exposure as a log offset, a rate target needs it
   as a weight. Pick wrong and the model trains, charts and is wrong. Nothing checks it.
3. Split. The tool suggests by time (accident year) and by group (policy id). GAP: overview.md says
   "random, by group or by time". I need both at once: policies kept whole and the latest year held
   out. GAP: claims and exposure per row set are not shown.
4. Factors table. GAP: is a column in or out by default? RISK: the policy id, the accident year and
   the exposure column sit in as factors. The identifier check would help, but ui.md gives the
   checks no place on screen (only a line in the Logging panel).
5. Look before fitting: arrow down the list, 60 key presses. KEEP: one chart serves before and after
   fitting, and leaves the test rows out. Leaving a factor out is 1 click while All models is open.
   SLOW: with a model type's menu open it is 3 clicks (All models, toggle, back). No key is given
   for in, out, direction or the reviewed tick.
6. Add model types: [+] three times, 6 clicks. Train. Proposal 13 picks a sample and quick. GAP: the
   sample's size, and whether it samples training rows only. RISK: a full run overwrites the sample
   run, and the standings can hold a sample run beside a full run with no label.
7. "Two yardsticks come free." GAP: is the gradient-boosting yardstick the node's LightGBM model
   (Proposal 1 allows one per model type)? If I tune LightGBM, is the yardstick gone?
8. Compare. KEEP: standings with ranges, and the floor-to-ceiling bar.
9. The loop. Sort by worst fit. GAP: the measure is not defined. A shared change puts three models
   out of date on 2 million rows. A what-if run avoids that. GAP: ui.md has no control that starts a
   what-if run and no place for its result, or for "what changed".
10. Undo. RISK: undo takes the settings back, but the run was overwritten. The model is out of date
    again and I retrain for minutes to get back to where I was.
11. Thorough evaluation before logging. RISK: one model thorough and the rest quick, in one
    standings table.
12. Log. KEEP: test rows scored at logging, each look recorded. GAP: nothing says what to do if the
    test score is poor. RISK: the logged model is a refit on training and validation rows. Its
    tables are not the ones I reviewed, and nothing shows the difference. GAP: are t-boost's prune,
    band and graduate choices replayed on that refit?

Count: about 12 setup choices, 60 key presses and 15 toggles to review, 6 clicks to add, 1 to train.
That is good. The loop is where it costs.

### 2. Small data: commercial property severity, 15,000 claims, 25 factors

1. Setup. The loss suggestion gives Gamma. overview.md says CatBoost has no Gamma loss and that this
   is "flagged in that model's menu". GAP: does CatBoost then train on another loss, or not train?
   Either way it sits in the standings.
2. Large losses. GAP: where a cap on the target is set is not stated. I assume pre-processing.
   MISSING: nothing shows how much one claim drives a level or a score.
3. Proposal 13 picks thorough. KEEP. GAP: "a range on every score" comes "from the folds, or from
   resampling". Those differ, and neither is paired. With heavy tails every range overlaps, so every
   comparison reads "no difference", even when one model wins in every fold.
4. Factor chart. ui.md: "a range on actual at each level, from its exposure". CONFLICT with
   severity: the range must come from the claim count and the spread of the amounts.
5. Sort by worst fit. RISK: on 15,000 claims the worst fit is the thinnest level. The worklist sends
   me to tune noise, against principle 5.
6. The thin-level check "links to the place that fixes it". CONFLICT: overview.md says merging
   levels is feature engineering, and workflow.md says a GLM term can group levels. Which is it for
   a trade code with 200 levels? SLOW: via pre-processing it is open node, edit, return, Train,
   wait, and every model is out of date each time.
7. What-if "each factor left out in turn": 25 comparisons. RISK: one or two look real by chance.
   They need to be read as one ranked picture, not 25 verdicts.
8. Effect view. MISSING: a range on each model's relativity. The fold models give it for free, and
   on small data it is the first thing I look at.
9. Test rows: about 3,000 claims if a fifth is held back. RISK: one test score on that is noise.
   "Shown once" with no range invites a wrong decision in either direction.

### 3. A refresh: last year's logged model, one more year of data

1. Open the node. workflow.md says the logged run is kept as a reference model. overview.md says
   runs and reference models live in the project's local cache. GAP: a year on, on another machine
   or after a cleared cache, the reference is gone. Nothing brings a logged run back from MLflow.
2. New rows arrive. GAP: a new column puts models out of date, but new rows are not mentioned. GAP:
   no check for new levels, or for a shift in the mix of exposure by year.
3. The reference model's line. Kept predictions are "one prediction per row", for last year's rows.
   GAP: the old model must score the new rows. overview.md lists only PDP under "by scoring the
   model again".
4. RISK: under a random or group split, last year's logged model was refitted on rows that are
   validation rows today. Its score on them is flattered, against principle 1. A time split avoids
   it, but nothing records which rows a run learned from.
5. Rebuild: Train, 1 click. KEEP: hand edits carry over, and a check flags an edit the data has
   moved away from.
6. Decide. KEEP: double lift between any two runs, where they disagree, by segment and by year.
   MISSING while I work: how far predictions move, by segment. ui.md shows it only in the Logging
   panel.

### 4. "Why does the new model charge young drivers less?"

1. Click driver age. Fit view: actual, the new model, and the old model dashed. KEEP: this is the
   first piece of evidence and it is one click.
2. MISSING: a scale that shows new against old directly. The scales are values, relativities, and
   actual to expected.
3. Effect view: old against new relativity for age. GAP: a reference model keeps predictions. Does
   it keep its effect too? GAP: the effect is "estimated" for gradient boosting, and the method is
   not named. Two methods give two answers.
4. Is it age itself, or the factors that travel with age? MISSING: a filter. "Split by" repeats a
   chart per value. I need every chart limited to drivers under 25, then to click through vehicle
   group and no-claims discount.
5. MISSING: the key analysis. For the segment, how much of the difference in prediction comes from
   each factor's change. For a model type whose model is tables this is exact arithmetic.
6. Is it the data, the settings or a hand edit? workflow.md gives the settings difference against
   "the run it replaced". GAP: I need it between any two runs.

### 5. No-claims discount: monotone, top level fixed

1. Monotone: All models, click ncd on the left, set its direction in the highlighted row. 3 clicks,
   then Train. KEEP: one place, every model.
2. overview.md: monotonicity in the GLM is new work, flagged "in that model's menu". RISK: until
   then the GLM sits in the standings unconstrained, and scores better for it.
3. GAP: the direction of an ordered category needs the order of its levels. Where is it set?
4. Fixed top level: a hand edit, so per model, and only for t-boost, the GLM and EBM. SLOW: choose
   the model, open its table pane, find the table, click the level, type, write a note. 6 steps, 3
   times. Gradient boosting cannot take it at all.
5. RISK: the rule says "0.35 against zero years". overview.md fixes a value against the base level
   in a GLM, and against the table's average in t-boost and EBM. I must convert by hand, and the
   average moves on each refit.
6. CONFLICT: workflow.md says edits come "last, not first". A business rule is known on day one.
7. RISK: a fixed top level can break the direction against its fitted neighbour, most of all where
   an edit is laid on top (Proposal 19). No check covers it.
8. What they cost. KEEP: what-if with a direction, what-if with an edit, and "which other tables
   moved". SLOW without what-if: keep 3 models as reference models, set, train, keep again, edit 3
   times, train. About 30 steps for three comparable states.
9. MISSING: the cost where it lands. The score barely moves. I want actual to expected by
   no-claims-discount level before and after, with exposure.

### 6. A GLM built factor by factor, two interactions, against t-boost

1. Add the GLM. It "trains with no setup" with all 60 shared factors in. CONFLICT: I build forward
   from nothing, and factors are shared by every model (settled). Leaving a factor out of the GLM
   takes it from t-boost too.
2. GAP: 60 default terms with wide categories. Aliasing and failed fits are not mentioned.
3. Choose a factor. KEEP: the chart shows the GLM's expected against actual for a factor that is not
   yet in the model. That residual view is how I pick the next term.
4. Set the term in the GLM menu. GAP: with 60 terms, does the menu show the chosen factor's term at
   the top, as the All models table highlights it?
5. Refit. CONFLICT: "refitting should feel instant" and "skip the preview" suggest it refits on a
   change. ui.md says Train trains what is out of date. Which? SLOW if it is Train: choose, set the
   term, Train, read is about 5 steps a term, 300 for 60 factors.
6. Tests for adding or dropping a term, AIC, BIC. CONFLICT with principle 1: these use the training
   rows. They are right to have, but must be labelled.
7. Forty refits later. CONFLICT: overview.md lists "the run history" in the shell. ui.md has only
   the last run and the reference models. MISSING: the list of steps with the score at each, and a
   way back to step 23.
8. Interactions. KEEP: borrowing the ones t-boost kept, with evidence. KEEP: split by a second
   factor to see it. GAP: how an interaction's relativities are shown, and what the effect view
   draws for a factor that sits in an interaction.
9. Against t-boost. KEEP: one chart, both models. RISK: the GLM's relativities are against a base
   level and t-boost's against the table's average. Overlaid, they sit apart for no real reason.

### 7. Things going wrong

1. Out of memory. overview.md says Haute estimates memory before training (unverified). GAP: several
   models at once, times the folds of a thorough run. GAP: when is the old run overwritten, at the
   start or on success? RISK: a failed training costs me the good run. GAP: the "failed" state names
   no reason and no next step.
2. A column disappears upstream. GAP: no check, no state. CONFLICT: checks "never block training",
   yet a model cannot train on a missing factor. RISK: it is dropped silently, the model trains on
   59 factors, and the score dips a little.
3. A leak. KEEP: the pre-fit check for a factor that predicts suspiciously well. RISK: if I miss it,
   every model inflates together. Standings, floor to ceiling and the test rows all look fine,
   because the leak is in every row. MISSING: a check after fitting.

### 8. A conversion model: yes/no target, price as a factor

1. Setup. A yes/no target. overview.md: "the GLM does regression only". CONFLICT with my daily work:
   the model type I would start with cannot train here. GAP: which other model types take one?
2. Split: quotes from one customer must stay together, and the latest months held out. The same
   group-and-time gap as scenario 1.
3. KEEP: price monotone, shared by every model. Without it a boosted price curve wobbles.
4. Fit view by price. RISK: price is set from the other factors, so actual by price is confounded.
   The chart gives no warning.
5. Effect view "as relativities". RISK: with a logit link the effect multiplies the odds, not the
   conversion rate. The word misleads.
6. MISSING: the output a conversion model exists for. The demand curve: conversion against a price
   change, by segment, with a range.
7. MISSING: a check that price is not almost fully determined by the other factors. If it is, the
   price effect cannot be told apart from them.
8. Verdict: the shell holds. The gaps are the GLM, the scale of the effect and the demand curve.

## The 15 problems that matter most

Ranked by harm times how often. Brackets: the scenarios; the Proposed items changed (P); the tag.

**1. Runs are compared on rows that are not like for like.** (1, 3, 5; P3, P4, P13; analytics)
- Wrong: a sample run beside a full run, quick beside thorough, a reference model that learned from
  today's validation rows (under a random or group split), a model that ignored a shared setting.
- Fix: every run records the rows it learned from, sample or full, and its depth. Any comparison
  uses only rows neither run learned from and says how many. A row in the standings that is not like
  for like is greyed, with the reason.

**2. The range is not defined, and not paired.** (2, all; P3, P8; analytics)
- Wrong: unpaired ranges make every comparison "no difference" on small data. A range from exposure
  is wrong for severity. The worst-fit sort ranks noise.
- Fix: three stated ranges. A score: resampled validation rows. A difference between two runs: its
  own range from the same resamples or folds for both. A level: from the loss (claim count, and
  spread of amounts for severity). Folds and seeds are fixed across models. Worst fit is the gap
  divided by its range, weighted by exposure.

**3. What-if, checks and "what changed" have no place in ui.md.** (1, 5, 7; P9, P10; UI)
- Wrong: three of workflow.md's best ideas cannot be found or started on screen.
- Fix: `Checks 3` in the menu row opens a checks pane. `Try without` and `Try direction` sit in the
  chosen factor's row of the All models menu, and `Try this edit` in the table pane. Results land as
  rows in the standings marked "what-if", with Apply, Keep, Drop. "What changed" is a pane that
  opens after each training.

**4. Setup lets silent mistakes through.** (1, 8; workflow)
- Wrong: exposure as weight or offset, identifiers in as factors, group or time but not both.
- Fix: setup asks what the target is: a count, an amount, a rate or yes/no. The tool then states the
  handling in a sentence ("claims per vehicle-year, exposure as a log offset"). Columns picked as
  exposure, group or date start out. The split takes a group and a time holdout together. Section 1
  shows rows, exposure and claims for each row set.

**5. A factor's setting costs a menu switch.** (1, 5; UI)
- Wrong: 3 clicks per change whenever a model type's menu is open, across 60 factors.
- Fix: the chosen factor's row is pinned at the top of the settings column in every menu, with keys
  for in or out, direction and reviewed. See Concerns, B.

        | ncd_years   in [x]   direction [down]   [Try without]  [Try direction]  |
        |--------------------------------------------------------------------------|
        | (the settings of the menu chosen, below)                                 |

**6. Undo does not bring the run back, and there is no run history.** (1, 6; P9; workflow)
- Wrong: undoing a change leaves the model out of date. A GLM built in 40 steps has no path back.
- Fix: the run that was replaced is held as "previous" until the next training, so undo restores run
  and settings together. Each model keeps a run history: for each training, the settings change and
  the scores, no predictions, with "bring back" on any entry. The run history alone fits the settled
  line. The held run needs Concerns, C.

**7. The logged model is not the model that was reviewed.** (1; P12; analytics)
- Wrong: the refit on training and validation rows moves the tables, and nobody sees it.
- Fix: the Logging panel adds a step before the test rows: "refit against reviewed". It shows the
  largest move in each factor's effect and the spread of the ratio of predictions, and flags a move
  larger than its range. t-boost's steps are replayed with their recorded choices.

**8. A GLM cannot be built factor by factor while factors are shared.** (6; P11; workflow)
- Wrong: leaving a factor out of the GLM takes it from every model, so I cannot build forward.
- Fix: inside the settled line, the GLM starts from default terms and is refined term by term, with
  a what-if run "without this factor" for the GLM alone. The forward build needs Concerns, A: the
  GLM's term for a factor can be "not used", and the factor list marks it.

**9. The effect view is not defined well enough to overlay.** (4, 6, 8; P5, P7; analytics)
- Wrong: different bases, an unnamed estimate for gradient boosting, no rule for interactions, and
  "relativity" under a logit link.
- Fix: the common shape is computed on the link scale and shown as a multiplier whose
  exposure-weighted average on the validation rows is 1, or against a base level the analyst names.
  The legend names the method (exact, or estimated and how). A factor in an interaction is drawn per
  level of its partner through Split by. Fold models give the range. The link labels the axis.

**10. A refresh has no reference to work against.** (3; P4; workflow)
- Wrong: the reference model is local, and its predictions are for old rows.
- Fix: `Bring in a logged run` in the run list loads a run from MLflow as a reference model, scores
  the node's current rows, and marks the rows it learned from. A banner reports what changed in the
  data since each run: rows, columns, new levels.

**11. No way to explain why a segment moved.** (4; P7; analytics)
- Wrong: the most common question from the business has no answer in the tool.
- Fix: a segment filter in the chart header, linked across every pane. In Compare, `Explain
  difference` between two runs for the filtered rows: a bar per factor of its share of the change in
  prediction (exact where the model is tables), plus a scale "ratio of two runs" on the factor
  chart.

**12. A fixed value from the business is three edits on three bases.** (5; P14, P18; workflow)
- Wrong: 18 steps, a conversion by hand, and gradient boosting left out without a mark.
- Fix: a fixed value known up front is set once, in the factor's row of the All models menu, against
  a base level the analyst names. Model types whose model is tables honour it and convert the base.
  The rest are flagged. Judgement edits stay per model. Add a check: an edit that breaks a
  direction.

**13. "Cannot honour" has no defined outcome.** (2, 5, 8; workflow)
- Wrong: a flag inside one menu, while the model sits in the standings as an equal.
- Fix: three outcomes, shown on the menu row entry and the standings row. Honours. Substitutes,
  named and accepted by the analyst (for example another loss). Cannot train, greyed in [+] with the
  reason.

**14. Failures lose work or pass silently.** (7; P10; workflow)
- Wrong: a failed training may overwrite a good run. A missing column may be dropped.
- Fix: a run is replaced only when the new training finishes. A failed entry shows the reason and
  its fixes (sample rows, one model at a time). A missing column that is in stops Train with two
  choices: leave it out, as a recorded change, or open its node. That is an error, not a check, so
  checks still "never block training".

**15. A leak that passes the first check inflates everything.** (7; P10; analytics)
- Wrong: every model, every score and the test rows agree, and all are wrong.
- Fix: a check after fitting: one factor carries most of the gain over the floor, in every model.
  And a check when the score jumps far beyond the last logged run. Both link to the factor's chart
  and to `Try without`.

## Analyses I wanted and could not get

- The share of a difference between two runs that each factor carries, for a segment.
- A filter to a segment that applies to every chart.
- A paired range on the difference between two runs, and a range on each model's effect by level.
- The influence of the largest claims on a level and on a score (severity).
- How far predictions move between two runs, by segment, while working and not only at logging: the
  spread of the ratio, and the exposure that moves more than a set amount.
- The mix of exposure by year for a factor, and new levels since the last run.
- Factor pairs ranked by the two-way gap the chosen model leaves.
- For a GLM: an aliasing report, and each add-or-drop test beside the validation score.
- For conversion: the demand curve by segment, and the check that price can be told apart from the
  other factors.
- Total actual to expected by year. Copying a chart with its table.

## What is right and must not be lost

- Validation rows are only looked at, test rows are sealed, and each look is recorded.
- One chart per factor with every model on it, actual drawn once, exposure beneath.
- Every factor charts every model, even a factor left out. That is how I find what a model misses.
  The same chart works before any model exists.
- The fit view and the effect view as two questions. Kept predictions, so looking needs no training.
- The floor-to-ceiling bar and the gradient-boosting yardstick.
- A range on every score, and a gap smaller than its range shown as no difference.
- What-if runs, including each factor left out in turn.
- Checks that never block, link to the fix and take a note. Suggestions never self-apply.
- The GLM in the comparison from the first training, borrowing bands and interactions from t-boost
  with the evidence shown.
- t-boost's fit-against-simplicity curve, with the point picked on the curve.
- Hand edits: beside the fitted value, with a note, refitted around, "which other tables moved", and
  the check when the data moves away.
- The menu row as legend and league table, one colour per model, linked choices.
- Arrow keys through the factors, and a reviewed tick that clears when the factor moves.
- "Where they disagree", with which model is closer to actual.
- Defaults that follow the size of the data and say so. The config as a project file in git.

## Concerns about settled items

- **A. "Factors and monotonicity are shared by every model", with "The GLM needs its own
  workflow".** Problem: a GLM is built by adding factors one at a time, and removing a shared factor
  removes it from every model. Smallest change: read "shared" as "available to every model". A model
  type's menu may leave an available factor unused, and the factor list shows it.
- **B. "The factor list on the left is for choosing a factor to look at", with "A factor's settings
  sit in the menu".** Problem: the review of 60 factors needs the in, out and direction controls at
  hand while a model type's menu is open. Smallest change: the chosen factor's row from the All
  models menu stays pinned at the top of the settings column, and keys act on it. The list and the
  chart still hold no settings.
- **C. "Training a model again overwrites its run".** Problem: undo and "what changed" both need the
  run that was replaced, and a failed training must not cost the good run. Smallest change: each
  model holds one replaced run, as "previous". A finished training drops the one before it.
- **D. "Runs live locally."** Problem: at a refresh a year later the reference model is often gone.
  Smallest change: a logged run can be brought back from MLflow as a reference model.
