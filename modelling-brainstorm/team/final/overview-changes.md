# Changes for overview.md

What the team's honing changes in `overview.md`, for the owner to accept, strike or change. The
three final files are `ui.md`, `workflow.md` and `analytics.md`, beside this one. `analytics.md` is
new: it holds every measure, range, check and default number, so the other two can stay short.
Nothing here is settled. The settled list in `overview.md` is untouched. Section h goes beyond the
seven asked for: it is the list of findings resolved, to sit beside the list rebutted.

## a. New proposals

Numbered on from the 19 in `overview.md`. Each names where it lives.

**The foundations**

20. **An id column, asked for at setup and checked unique.** The split is made once per node against
    it. *(analytics.md, The rules; workflow.md, The journey)*
21. **One split by policy and by time:** test rows are the latest mature period, and validation rows
    are whole policies. Setup takes a "claims are mature up to" date. *(analytics.md, The rules)*
22. **Setup states the handling.** The analyst names the kind of target, and the tool states the
    handling in a sentence. *(workflow.md, The journey)*
23. **Rows held back from the training rows** for early stopping and tuning, then a fit on all the
    training rows, so every model type trains on the same rows. *(analytics.md, The rules)*
24. **The look before fitting uses the training rows,** so it spends no validation rows.
    *(analytics.md, The rules; ui.md, The other states)*

**Scores and comparisons**

25. **Gain is the one unit:** how far a model beats the floor, as a percent of the floor's deviance,
    wherever a score is shown or recorded. *(analytics.md, The rules)*
26. **The range goes on the difference between two runs,** paired by row and resampled by whole
    policies. One rule says when a difference is clear. *(analytics.md, The rules)*
27. **One rule for many tests.** Every list controls its share of false flags and calls clear only
    what survives. *(analytics.md, The rules)*
28. **Two marks for runs that are not alike:** "other rows" and "ignores". A run that ignores a
    shared setting is still scored and compared. *(analytics.md, The rules)*
29. **The last logged run is compared as its reviewed fit.** The logged refit is compared only on
    rows it never saw. *(analytics.md, The rules)*
30. **The thorough evaluation buys charts and a worklist on every row, and a spread on each
    effect,** but no narrower range. *(analytics.md, The rules)*
31. **Two yardsticks:** the floor, which is one rate, and Default boost, a reference model that
    tuning never touches and that is retrained only when asked. *(workflow.md, The journey)*

**Factor by factor**

32. **Misfit is the gain a factor still offers,** from corrections learned on other rows. It sorts
    the factor list, left-out factors included. *(analytics.md, By factor; ui.md, The factor list)*
33. **One common effect:** the link scale, one centring, averaged over a partner's levels, exact or
    labelled as estimated. Partial dependence leaves the shared charts. *(analytics.md, By factor)*
34. **The chosen model's effect as a thin third line on the fit view,** as three pricing tools draw
    it. *(analytics.md, By factor; ui.md, Charts)*
35. **A suggestion for the factor in hand,** with its evidence, for every model type, and the
    pattern named in words. *(workflow.md, The loop; ui.md, The settings column)*
36. **Named segments,** kept in the config and scored on every run. A score under a filter made by
    hand is labelled "after looking". *(analytics.md, By model)*
37. **Where and why two runs disagree:** the factors that set the disagreeing rows apart, and for
    table models an exact split of the difference by factor. *(analytics.md, By model)*

**The loop**

38. **Try, then Apply.** A change runs as a what-if beside the model's run. Apply makes it the
    model's run, with no training. *(workflow.md, The loop, What-if runs)*
39. **A history line per training,** with what changed and a way back. A run is overwritten only
    when the new training finishes. *(workflow.md, The loop)*
40. **Five stopping questions** for the chosen model, and a rule for when to stop. *(workflow.md,
    Knowing when to stop; ui.md, The other states)*
41. **Errors are not checks.** A missing column stops Train. Each check has a rule and a status.
    *(analytics.md, Checks; workflow.md, Handover, and things going wrong)*
42. **A new column arrives left out,** with `Try with`. Thin levels have two homes. *(workflow.md,
    The round trip to pre-processing)*

**By hand**

43. **Fixed values:** the business's rule, entered once for all models. A whole factor goes in as an
    offset. *(workflow.md, Hand edits and fixed values; analytics.md, Hand edits and directions)*
44. **The hand-edit preview leads with the rows touched and states the cost.** Only main tables are
    edited. *(analytics.md, Hand edits and directions; ui.md, The other states)*

**Per model type**

45. **The GLM's default terms hold the shared direction,** and a term change refits at once only
    when nothing else is training. *(workflow.md, Workflows by model type)*
46. **t-boost's pruning choice is recorded as a rule,** picked on out-of-bag evidence. Graduation
    gets one number on the validation rows. *(analytics.md, Per model type)*

**Logging and after**

47. **Logging says what is logged:** a refit, shown against the reviewed fit before the test rows
    are scored. *(workflow.md, Before logging; analytics.md, At logging; ui.md, The other states)*
48. **An expected range for the test score,** stated first and holding both row sets' noise, with a
    rule for a score below it. *(analytics.md, At logging; workflow.md, Before logging)*
49. **The record of looks at the test rows belongs to the node's rows,** so a changed split cannot
    reset it. *(analytics.md, At logging)*
50. **A refresh workflow:** where things stand, the split moved forward, `Bring in a logged run`,
    the old model judged first. *(workflow.md, Refreshing a logged model)*
51. **Handover:** the config carries settings, edits and segments. Reviews and acknowledged checks
    stay local until logging. *(workflow.md, Handover, and things going wrong)*

**The screen**

52. **Three choices, one home each,** and the two menus as tabs of the settings column. *(ui.md,
    Rules for the screen, The settings column)*
53. **A notice line** for news, errors and "where things stand", and **a rows line** on every pane.
    *(ui.md, The frame)*
54. **The `vs` list** picks the run every difference is taken from. Compare always has a pair, and
    `[B]` picks another. *(ui.md, The frame, The other states)*
55. **One grammar for lines:** colour and shape for the model type, stroke for the run, each with
    one meaning. A line is muted, never hidden. *(ui.md, Charts)*
56. **A small set of keys,** none of which changes a shared setting. *(ui.md, Keys)*
57. **One chart frame, one table editor, one run row, one score cell.** The model card gains tables
    and text, not charts. *(ui.md, Building blocks)*
58. **A shared order to build in,** foundations first. *(workflow.md, The order to build in)*

## b. Changes to Proposed items 1 to 19

- **1.** Kept. The yardsticks are reference models, so "one model per model type" holds.
- **2.** Changed. Early stopping and tuning use rows held back from the training rows (23). The look
  before fitting uses the training rows (24). "Sealed" also covers the checks and the bands of a
  number. The record of looks belongs to the rows (49). Why: the comparison across model types was
  tilted, and a changed split could reset the count.
- **3.** Changed. "A range on every score" becomes a range on every difference between two runs
  (26). The thorough evaluation is described by what it buys: charts and a worklist on every row,
  and spreads. It is not a second untouched score or a narrower range (30). Why: two unpaired ranges
  overlap even when one model wins on nearly every policy.
- **4.** Kept, and made exact: a run keeps predictions for the validation and training rows from the
  reviewed fit, for every row out of fold after a thorough evaluation, and for the test rows at
  logging. It rests on the id column (20).
- **5.** Changed. The common shape is defined (33). "Exact" holds for the main effect, averaged over
  a partner's levels.
- **6.** Changed. The row across the top keeps the model types, Add, `vs`, Train and Log. All models
  becomes a tab of the settings column, and Compare and Checks become panes (52). Train is a queue.
  Why: the menu row mixed three kinds of choice, and one training runs at a time today.
- **7.** Changed. Both views stay, and the fit view gains the chosen model's effect (34). The split
  by year is of the fit. The effect by year is a what-if run, for the GLM and EBM only. The range on
  actual depends on the loss.
- **8.** Changed. "Where a model fits worst" is defined as misfit (32), which does not put thin
  levels first. A reviewed tick records its model and run, and stays local until logging.
- **9.** Replaced by 38 and 39. Why: undo could not bring back an overwritten run. Try and Apply
  never lose one.
- **10.** Changed, and moved. The list of checks now lives in `analytics.md`, each with a rule and a
  status, cut to about a dozen. An error is not a check (41). Why: long lists get ignored.
- **11.** Changed. t-boost does every step in one training and only pruning has a path, so picking a
  point is pruning only, recorded as a rule (46). Curves for banding and graduation wait on t-boost.
  The GLM's default terms hold the shared direction (45), and "bands" need a new term type.
- **12.** Changed, as 47 to 49.
- **13.** Changed. Defaults follow the time a training takes, and are stated before the first
  training. Thorough stays the default on small data, for its charts by level.
- **14.** Kept, and narrowed: a GLM edit applies to a term with levels, and only main tables are
  edited.
- **15.** Kept, with two additions: an edit is stored against a level's name or a range of values,
  and the tool drafts what it did.
- **16.** Changed, as 44. "Which other tables moved" applies only after a refit around a whole
  table.
- **17.** Kept. A fixed value (43) is entered in the All models menu and shows in the table as
  locked.
- **18.** Changed. Refitting around an edit is in reach for a whole table, through the offset, once
  Haute carries a second offset. Holding part of a table is library work.
- **19.** Changed. Laid on top applies to part of a table only, with `Rescale the base` offered.

## c. Open questions for the owner

Six, each with the default the files assume.

1. **Is a value the business fixes a shared setting,** entered once in the All models menu? Default:
   yes.
2. **May kept predictions go to MLflow with a logged run?** They are row-level data. Default: no.
   They stay local, and a logged run brought in elsewhere is compared only on rows it never saw.
3. **Is there an id column on every dataset?** Default: setup asks for one, and says what is lost
   without it.
4. **Should claim counts and claim sizes, modelled in separate nodes, be checked together as a pure
   premium?** Default: not in this design. Each node is judged alone.
5. **Should today's price be drawn as a benchmark beside the models?** Default: no. The last logged
   run answers "better than today?".
6. **Do reviews and acknowledged checks travel with work in progress, in git?** Default: no. They
   stay local until logging, then go into the model card.

## d. Concerns about settled lines

The files stay inside every settled line, and assume "no" wherever one is at its edge. Each concern
gives the problem and the smallest change that would fix it.

- **"A factor's settings sit in the menu, not beside its chart", with "The factor list on the left
  is for choosing a factor to look at, not for settings."** Problem: with a model type's menu open,
  a factor's setting is two clicks away, across sixty factors. The UI draft pinned the chosen
  factor's row at the top of the settings column and gave keys for in, out and direction. The red
  team read that as an inspector beside the chart, so both are withdrawn. What is drawn instead
  still follows the chosen factor: the Factors table is open by default, and the chosen row is
  highlighted, scrolled into view and opened to show its fixed value, a suggestion and `Try`. That
  goes beyond the before file's "highlighted". The stricter fallback is highlight only. Smallest
  change: allow the pinned row, and keys that act on it.
- **"Training a model again overwrites its run, unless the run is kept as a reference model."**
  Problem: undo brings the settings back but not the run, and "what changed" needs the replaced
  run's predictions. Inside the line, the files overwrite only when the new training finishes, and
  work out what changed at that moment. Smallest change: hold the one replaced run until the next
  training. Four of the team's reports ask for this. Also at this line's edge: a what-if run is a
  third kind of run. It ends at Apply, Keep or Drop.
- **"Factors and monotonicity are shared by every model", with "The GLM needs its own workflow."**
  Problem: a GLM is classically built forward, one factor at a time, and leaving a factor out of the
  GLM takes it from every model. The files have every factor that is in hold a GLM term. Smallest
  change: a model type may leave a shared factor unused, and the factor list shows it.
- **"...the rest of the model is refitted around it where the model type allows."** No model type
  allows it for part of a table today. The wording covers that. The files refit around a whole table
  and lay part of a table on top. And an edit "carries over" only if it is stored against a level's
  name or a range of values. Smallest change: say so in the rule.
- **"Feature engineering stays in the pre-processing nodes."** Stretched by grouping levels inside
  one GLM term, and by a banded term. The reading taken: what only the GLM sees is a term, not a
  column.
- **"Runs live locally."** Problem: at a refresh on another machine the reference is gone. Smallest
  addition: `Bring in a logged run` from MLflow. And nothing lives locally yet, so the run store
  comes first in the build order.

## e. What changed and why

1. A third file, `analytics.md`, owns every measure, range, check and number, defined once.
2. Scores are gains over the floor, and the range sits on the difference between two runs.
3. One rule says when a difference is clear. Many tests at once used to flag noise.
4. Foundations come first: an id column, a split made once, kept predictions, held-back rows.
5. The menu row is now a row of model entries. The two menus are tabs of the settings column.
6. Every state has a drawn place, from a new node to the node's card.
7. Try and Apply replace "what changed on retrain": an idea is backed out without losing a run.
8. Typed values split in two: a fixed value for all models, and a hand edit for one.
9. A whole table refits through the offset. Part of a table is laid on top of the fit.
10. Logging says a refit is logged, shows how far it moved, and states an expected range first.
11. A stopping rule, a refresh workflow, a handover and a list of failures were added.
12. About a third of the drafts was cut: see f.
13. Wherever a draft sat at the edge of a settled line, the files assume no: see d.

## f. Considered and cut

- **The fold vote,** and **a narrower range from the thorough evaluation.** The first passes by
  chance too often. The second needs a correction nobody on the team read in full.
- **"Off level" and "ranks risks" columns, an outside benchmark, "what the data can show".** New
  words or new strokes for what balance, the calibration curve and the last logged run already say.
- **"What is this model missing", and a search over all pairs.** Each costs a training. The pairs
  t-boost and EBM kept, and the grid, answer the same question.
- **The pinned factor block, and most keys:** in or out, direction, a key per model, a key per pane.
  Either at the edge of a settled line, or not worth a new idiom.
- **Half the checks:** importance held by one factor (importance differs by model type), a score
  jump against the last logged run, a group on both sides of the split, fixed thresholds for a shift
  in mix, and run states listed as checks.
- **A file beside the config for reviews and acknowledged checks.** A tick records a run, and runs
  are local.
- **`Try this edit` for an edit laid on top.** Apply needs no training and has an undo.
- **Unasked retraining of Default boost** after every shared change.
- **Folds cut by period** for the thorough evaluation. Folds are by whole policies.
- **The two worked examples.** Their timings were assumed, and the small one leaned on ranges it
  could not have had.
- **Pixel breakpoints, hex colours, a 22-key table, and the drafts' lists of needs and
  cross-references.** Specification, not brainstorm.
- **A dash-dot stroke, parallel coordinates, a command palette, isotonic recalibration, a range on
  Gini, the demand curve for a conversion model.**

## g. Findings rebutted

- **Red team, section 8: "Analytics has no home among the three files."** Rebutted in part. The
  owner asked for the analytical capability to be honed, and the team's task names `analytics.md`.
  Taken from the finding: the rules come first in plain words, and the numbers sit in one short
  reference section.
- **Red team, section 6 item 7: cut "where and why".** Rebutted. It costs no training, unlike the
  two devices cut with it, and it answers the question analysts are asked most: why does the new
  model charge this segment less.
- **Red team, section 6 item 4 and S1, for the `R` and `T` keys.** Kept. `R` ticks a review mark in
  the list, which the before files already had. `T` starts a run and changes no setting. The keys
  that change a shared setting are cut.
- **Red team, F4, for the refit that starts when a run is picked for logging.** Kept. Pressing Log
  asks for it. It now shows its progress, can be stopped, waits in the queue, and scoring waits for
  it. The rest of F4 is taken.
- **Red team, T2, the option to scale the spread into an approximate range.** Not taken. The scaling
  is the red team's own arithmetic, cited from memory. The other option is taken: the spread is
  never a threshold.
- **Red team, X2, option (b): judge thorough pairs on every out-of-fold row.** Not taken. Option (a)
  is, in substance: a difference is always read on the validation rows. One departure: a what-if run
  matches its model's evaluation, so that Apply loses nothing. On small data that costs seconds.
- **Red team, S4, the option to build the card from the same chart code.** Not taken. The cheaper
  option is: the card gains tables and text only.
- **First-time analyst, fix 3: print "tied: prefer the simpler" beside the bracket.** Not taken.
  Size does not compare across model types (red team X5). The standings show size and time, and the
  analyst picks. The rest of fix 3 is taken.
- **First-time analyst, fix 7: the line "inside the spread across folds: the data cannot tell the
  two apart".** Not taken, because the spread is not a range (red team T2). The preview shows the
  spread with no verdict. The rest of fix 7 is taken.
- **First-time analyst, fix 9: report "1 what-if dropped" on opening.** Not needed. A what-if run is
  never dropped unasked. The rest of fix 9 is taken.

## h. Findings resolved, and where

The red team's one blocker and 25 majors. The first-time analyst gave no severities, so its ten
fixes are treated as its majors.

- **F1, no row key (blocker).** An id column at setup, checked unique. *analytics.md, The rules;
  workflow.md, The journey*
- **S1, the factor block and keys at a settled line.** Withdrawn, and raised in d. *ui.md, The
  settings column, Keys*
- **S2, the GLM's default term drops the direction.** A factor with a direction gets a monotone
  curve, or the "ignores" mark. *workflow.md, Workflows by model type*
- **S3, "laid on top" for everything.** A whole table refits through the offset. *analytics.md, Hand
  edits and directions*
- **S4, charts built twice.** The card gains tables and text. *ui.md, Building blocks*
- **X1, "like for like" meant two things.** Two marks. *analytics.md, The rules*
- **X2, what a thorough evaluation buys.** Charts by level, the worklist and spreads. Differences
  stay on the validation rows. *analytics.md, The rules; workflow.md, What-if runs, Working fast*
- **X3, a flattered "Last logged".** Compared as its reviewed fit. *analytics.md, The rules*
- **X4, the unit of a score.** Gain, everywhere. *analytics.md, The rules; ui.md, The other states*
- **X5, "simpler wins".** Size and time are shown, and the analyst picks. *workflow.md, Knowing when
  to stop; analytics.md, By model*
- **X6, "refits at once" on one worker.** The screen says it waits. *workflow.md, Workflows by model
  type; ui.md, The other states*
- **T1, the test "band".** An expected range holding both row sets' noise. *analytics.md, At
  logging*
- **T2, the spread as a threshold.** Never a threshold. *analytics.md, The rules*
- **T3, many tests.** One rule. *analytics.md, The rules*
- **T4, a changed split resets the looks.** The record belongs to the rows. *analytics.md, At
  logging*
- **T6, the look before fitting.** On the training rows. *analytics.md, The rules*
- **T7, "what the data can show".** Cut.
- **T8, a main effect across model types.** Averaged over the partner's levels. *analytics.md, By
  factor*
- **T9, a saved filter as a segment.** "After looking", and trusted from the next refresh.
  *analytics.md, By model*
- **F2, the loop of seconds.** What it needs first is named, and no timing is promised.
  *workflow.md, Working fast*
- **F3, no shared order.** One order. *workflow.md, The order to build in*
- **F4, unasked work.** Default boost retrains when asked, and the GLM refits only when the worker
  is free. *workflow.md, The journey*
- **F5, hand edits to a GLM curve.** A term with levels only. *workflow.md, Hand edits and fixed
  values*
- **M1, claims still developing.** A "mature up to" date. *workflow.md, The journey; analytics.md,
  At logging*
- **M2, claim counts and claim sizes together.** Not resolved: left out, and asked in c.
- **M4, editing an interaction table.** Main tables only, and the pane says so. *workflow.md, Hand
  edits and fixed values; ui.md, The other states*
- **First-time fixes 1 to 10.** 1: logging says what is logged. 2: one unit, and an example that
  adds up. 3: `[B]` in Compare. 4: one misfit measure, and "3 clear". 5: a suggestion per factor,
  and the `Try` list spelled out. 6: Apply says what it does, and the time is shown first. 7: the
  cost is in the preview. 8: "fixed value" and "hand edit" named apart. 9: "where things stand" on
  opening, and a `Card` link. 10: one set of words at setup, and the pick shown before the first
  training. *ui.md, The frame, The other states; workflow.md, The loop, What-if runs*
- **Also drawn, as the first-time analyst asked:** the Checks pane, the marks in the factor list,
  training as a bar, "exposure and claims" on screen.
- **Minor findings taken:** one meaning each for range, spread and band (X7). Analytics owns the
  misfit measure and the checks (X8). The reviewed tick records its run (X9). Test rows show rows
  and exposure, not claims (T5). The dispersion is set once per node (T11). A fit on all the
  training rows after early stopping (T13). Bare keys named as a new idiom (H1). A Wald test (H2).
  The GLM's table sized as new work (H3). t-boost's rule recorded, not a point (F6). Rebuilt runs
  are near the originals, not identical (F7). An order for levels, and a rule for thousands of them
  (M3). A fifth target (M5). Old-style nodes, unseen values, disk use (M6). The three-line chart
  adopted (M7).
