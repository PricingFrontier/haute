# Workflow

How an analyst gets from a new node to a logged model, and keeps it good afterwards. Loose, and
proposed unless `overview.md` lists it as settled. Clustering is parked for now.

Steps say **You** (the analyst), **Unasked** (the tool, by itself) or **Suggests** (offered,
never applied). *Today* is what Haute does now, with the file under `src/haute/` or `t-boost/`.

## The journey

Settled: all the modelling for a node happens in the Modelling UI, for every model type. Any
model type can be trained on the node's data, and the models are compared.

1. **Open the node.** *Unasked:* on a new node the All models menu is open, each section marked
   done or missing, and Train names what it still waits for.
2. **Set up once, for all models.** *You:* say what the target is (a count, an amount, a rate or
   yes/no), then exposure or weight, offset and split. *Unasked:* the tool states the handling
   ("claims per vehicle-year, exposure as a log offset") and shows rows, exposure and claims in
   each set of rows. It fixes the split once, so no later change moves a row between sets. A
   role column (exposure, group, date, id) starts left out as a factor. *Suggests:* the loss,
   and a split that keeps policies whole and holds back the latest period as test rows.
3. **Look before fitting.** *You:* arrow through the factors, reading actual and exposure by
   level. Leave out what should not be rated on, set the directions, enter the values the
   business fixes. *Unasked:* the before-fitting checks. Nothing here uses the test rows.
4. **First fits.** *You:* add model types and press Train. *Unasked:* they queue, fastest first,
   and charts fill as each finishes. The tool trains two yardsticks of its own as reference
   models, untouched by tuning: **No factors** (the floor) and **Default boost** (gradient
   boosting on defaults). It picks sample or full rows, and quick or thorough, from how long a
   training takes, and says which. A model type that cannot honour a shared setting says so: a
   substitute you accept, a row greyed as not like for like, or greyed in Add with the reason.
5. **Read the standings.** *You:* choose the model types to carry on with. *Unasked:* each model
   sits between the floor and the strongest model, with a range on each gap. Tied models are
   shown as tied. A row that is not like for like is greyed, with the reason.
6. **Improve, refine, adjust by hand, decide when to stop.** See the sections below.
7. **Stress the favourite.** *You:* train every model on full rows with the thorough evaluation.
   Read it by year, by segment and in calibration, with a double lift against the last logged
   run and the strongest model. It is no second untouched score, as your choices saw those rows.
8. **Log.** See "Before logging". Settled: the node holds no config, and it shows what the model
   output is. The logged run stays as a reference model for the next round.

*Today:* one training runs at a time (`routes/_training_lifecycle.py:1338`), so Train is a
queue. A split is random, by group or by time, not both (`modelling/_evaluation.py:110-114`).

## The loop

    pick a factor -> read its chart -> try one change -> read what changed -> apply or drop

- **Pick.** *Unasked:* the factor list sorts by each factor's share of the chosen model's
  misfit, counting only misfit beyond its range, so a thin level never tops it. When nothing
  clears its range the list says so: a signal to stop.
- **Try one change.** *You:* press `Try` on a shared setting, a model's own setting or a hand
  edit. *Unasked:* a what-if run trains beside the model's run, and nothing goes out of date.
- **Read what changed.** *Unasked:* only the settings that differ, each score's change with the
  range on the difference, the factors whose fit moved, and how far predictions moved.
- **Apply or drop.** Apply makes the what-if run the model's run, with no training. Drop leaves
  everything as it was. Backing out of an idea never costs a training.

Settled: training a model again overwrites its run, unless the run is kept as a reference model.
So the direct route stays: change a setting and press Train. The run is overwritten when the new
training finishes, never when it fails or is cancelled. The tool then adds a line to the model's
history: what changed, the scores, and a way to bring those settings back. Undo takes the
settings back and leaves the model out of date, so first keep a run worth returning to.

## What-if runs

- **Kinds:** without or with a factor; with a direction; with or without a hand edit; one
  changed setting; each factor left out in turn; a factor by year; what is this model missing.
- *You:* start one where the setting lives, for one model or all. *Unasked:* the tool gives the
  time first, and trains on the same rows and depth as the model's run. The result joins the
  standings, marked what-if, with Apply, Keep and Drop, until the settings or data change.
  Applying a shared change tried on one model puts the others out of date.
- **Many at once are one picture:** shown ranked, noting that a few clear their range by chance.
- **What is this model missing** fits a small boosted model on top of the chosen model's
  prediction and lists the factor pairs it leans on.

## Knowing when to stop

*Unasked:* a short readout for the chosen model. It informs and never blocks.

1. **Is there signal left?** The gap to the strongest model, with its range.
2. **Is any factor still wrong?** The factors and named segments with misfit beyond range.
3. **Is the level right?** Total actual against expected: overall, by year, by segment.
4. **Does it hold?** Each effect by year and, after a thorough run, from fold to fold.
5. **Are changes still paying?** Of the last five, and of all, how many cleared their range.

- A gain inside its range is no gain. Between tied models the simpler one wins.
- Stop the loop when 2, 3 and 4 are clean, or when none of the last five changes paid.
- A gap in 1 that remains is closed (see what the model is missing) or accepted with a note,
  such as "0.4% behind t-boost: three-way effects we choose not to rate on".
- A good score is not enough: an open leak check or a level that is off comes first.
- Every change applied fits the validation rows a little, so the sealed test rows matter more.

## Checks

Each check has a threshold and a status, and links to its fix. Checks never block training. Each
can be acknowledged with a note, and reopens if its evidence changes. Silent when all is well.

- **Before fitting:** a likely leak (a factor that predicts the target suspiciously well); an
  identifier; a constant; two factors carrying the same information; a level too thin to trust;
  a group on both sides of the split; new levels or a shift in mix since the last logged run.
- **After fitting:** overfitting, by level; a pattern that flips between years; total expected
  off total actual; a direction set but not held in the expected line; one factor carrying most
  of the gain in every model; a score far above the last logged run.
- **Hand edits:** the data moved away from an edit; it breaks a direction; its levels are gone.
- **Runs:** out of date, or not like for like.
- **Not a check:** a factor that is in but whose column is gone is an error. It stops Train with
  two choices: leave it out, recorded as a change, or open the node that made it.

## The round trip to pre-processing

Settled: feature engineering stays in the pre-processing nodes.

- *You:* from a factor, one click opens the node that makes its column. Change it there and come
  back. Capping large losses in the target is done there too.
- *Unasked:* a banner says what changed in the data: columns, levels, rows. A new column arrives
  marked new and left out, so nothing goes out of date by surprise. A changed column that is in
  puts every model out of date. The split stays put.
- *Suggests:* a banding, a grouping or a combination, each with its evidence. And making the
  change as a new column beside the old, then `Try with` on one model before swapping.
- **Thin levels have two homes.** Grouping levels for every model is feature engineering.
  Grouping them inside one GLM term is that GLM's own setting, and makes no column.

## Hand edits

Settled: an analyst may edit a fitted table or relativity by hand before logging. An edit
carries over when the model is trained again, and the rest of the model is refitted around it
where the model type allows.

- **Fixed values first.** *You:* enter a value the business fixes once, in the factor's settings
  in the All models menu, against a base level you name. *Unasked:* each model type that takes
  hand edits carries it and converts the base. The rest show it as not honoured.
- **Judgement edits last.** Every later fit has to work around the edits already made.
- **One edit, one reason.** *Unasked:* the tool drafts what the edit did. *You:* write why.
- **Read the preview on the rows it touches,** since the overall score barely moves. It leads
  with actual against expected on the edited levels, before and after, then the factors the edit
  will disturb, then the total. An edit can also be tried as a what-if.
- An edit is stored against a level's name or a range of values, never a position in a table.
  Bins move between fits, so this is what lets an edit carry over.
- *Today:* no model type can hold part of a table fixed while it fits (engineer's report, 5.2).
  So edits start laid on top of the fit, apply at once with no training, and the menu says so.

## Workflows by model type

**t-boost.** One training boosts, then prunes, bands and graduates. *You:* train on the defaults
and read the tables by importance. *Unasked:* the learning curve, the pruning path (fit against
the number of tables), and what banding and graduation did to each table. *You:* `Try` another
point on the path, and stop at the simplest tables that tie with the unpruned model. *Today:*
t-boost keeps only the finished tables, so another point means boosting again, and only pruning
has a path (`t-boost/python/t_boost/sklearn.py:4108`, `:4283`). Other curves need t-boost work.

**Gradient boosting (LightGBM, XGBoost, CatBoost).** *You:* train on defaults, then tune with a
number of trials. *Unasked:* the time expected. Tuning and early stopping use rows held back
from training. It is usually the yardstick. Stop tuning when its gain is inside its range.

**EBM.** *You:* train the main effects, read the term shapes by importance, then choose the
interactions. *Suggests:* pairs, with their evidence. *Unasked:* a flag where a factor with a
direction cannot join an interaction (`modelling/_descriptors.py:598`).

**GLM.** Settled: the GLM needs its own workflow. Factors and monotonicity are shared by every
model. A tree model takes a factor as it comes. A GLM must be told how each enters.

- **It trains with no setup.** *Unasked:* each factor that is in gets a default term: its levels
  for a category, and a smooth curve for a number, or its levels when it has few.
- **Then term by term.** *You:* take the top term of the worklist and set its shape: grouped
  levels, bands, a spline or a polynomial. *Suggests:* a shape, from its partial residuals.
- **Refit on change.** *Unasked:* a term change refits the GLM at once when its last fit took
  under about ten seconds. Above that it waits for Train, or you work on sample rows. Each refit
  adds a line to the GLM's history, and any line brings its terms back.
- **Its own diagnostics:** relativities with ranges, aliased terms, AIC, BIC, and tests for
  adding or dropping a term. The tests use the training rows and say so.
- **Interactions are added by hand.** *Suggests:* the ones t-boost kept, the pairs from "what is
  this model missing", and bands from where t-boost banded a factor.
- **Every factor that is in has a term.** A factor the GLM does not need is left out for all
  models, once `Try without` shows none needs it, or kept as its simplest term.
- **Direction.** *Unasked:* the shared direction sets the term's direction, and a term that
  cannot hold one is flagged. *Today:* a term can hold a direction, but nothing links it to the
  shared one, and there is no banded term (`modelling/_glm_terms.py:50-61`).

## Before logging

Settled: a model is logged to MLflow. Nothing here blocks.

1. **Pick the run.** *Unasked:* the final refit starts in the background, on the training and
   validation rows together, with the same settings and the hand edits.
2. **Go through the list while it trains.** Each factor reviewed, each check fixed or
   acknowledged, each hand edit confirmed with its note, the thorough evaluation run.
3. **Refit against reviewed.** The model logged is the refit, not the model reviewed. *Unasked:*
   the largest move in each factor's effect, flagged when beyond its range.
4. **Unseal the test rows.** *Unasked:* first the band the score should fall in, from the
   validation score and its range. Then the score with its range, the paired difference from the
   last logged run, and how far predictions move. The look is recorded for good.
5. **If the score is below the band:** log anyway with a note, or look for a cause that is not
   tuning: drift, a leak, one segment. Never change the model to chase the test score. Later
   scores on these rows are marked as seen, and fresh test rows come with the next period.
6. **Note and log.** *Unasked:* a drafted note of what changed since the last logged run. *You:*
   add why. The model card takes the notes, reviews, checks, hand edits and history.

## Refreshing a logged model

1. **Open the node.** *Unasked:* a banner says what changed in the data since the last logged
   run: rows, periods, columns, new levels. Every model is out of date.
2. **Move the split forward.** *Unasked:* new rows stay out of every chart until the split
   places them. *Suggests:* the newest period as the new test rows.
3. **Get the reference.** *You:* `Bring in a logged run` if it is not here. *Unasked:* the
   logged model scores the node's current rows, and the rows it learned from are marked.
4. **Judge the old model first,** on the period just released from the test rows: actual against
   expected by year and by factor, before any training.
5. **Train all** on the saved settings. Hand edits carry over. Read what changed against the
   reference: scores, how far predictions move by segment, which effects moved.
6. **Refresh or rebuild?** Log the refresh when no factor's misfit clears its range and the data
   explains the moves. Otherwise enter the loop at the factors that moved. A comparison with the
   old model uses only rows neither model learned from, and says how many.

Settled: scheduling may come later. The model type a schedule trains and logs is picked when the
schedule is set up. A scheduled run is step 5 and the logging. An error stops it.

## Review and handover

Settled: runs live locally. So only the config and what is logged travel.

- **Handing over work in progress.** *You:* commit the config. It holds the settings, the hand
  edits, the named segments, and each reference model's name and settings. A small file beside
  it holds the reviews, the acknowledged checks and their notes.
- **Picking it up.** *Unasked:* each model shows "not trained here" with its settings. Train
  rebuilds every run and reference model, on the same split and seeds.
- **Reviewing a logged run.** The model card is complete without the tool. Or `Bring in a logged
  run`: it arrives with its kept predictions, so every chart works with no training.

## Failure and recovery

- **A training fails or is cancelled.** The old run stays. The entry names the reason and the
  next step: for memory, sample rows or one model at a time.
- **A restart.** Runs, reference models, the history and the queue are on disk. A training cut
  off shows as interrupted. *Today:* a restart loses every run (`routes/_job_store.py`).
- **A GLM will not fit.** It names the aliased or failing term and offers a simpler one.
- **The pipeline upstream fails.** The error links to its node. The last runs stay viewable.
- **Logging fails.** The refit is kept, so retry. A look at the test rows stays recorded.
- **Setup was wrong.** Change it: all models go out of date, old reference models are greyed.

## Working fast

Settled: training sets vary a lot in size.

- **Small data: noise is the risk.** Thorough by default, each change read against its range.
- **Large data: time is the risk.** Work on sample rows, confirm on full rows, then stress with
  the thorough evaluation, unattended. A sample run samples the training rows only and is
  labelled. Validation rows stay whole, so runs compare on the same rows.
- **The queue** trains only what is out of date, fastest first. A what-if run can jump a long
  training. Charts come from kept predictions, so looking never waits.
- **Keys** for the next factor, in or out, direction, reviewed and `Try without`.
- **The Assistant** panel works through the same settings and runs, so its work can be undone.

## Two worked examples

**Large: motor frequency, 2 million rows, 60 factors.** Timings are assumed, one training at a
time: on full rows t-boost 4 minutes, the GLM 30 seconds, Default boost 90; a tenth on a sample.

    0:00  set up, then look: 60 factors by arrow key, six left out, four directions set
    0:25  train t-boost and the GLM on the sample: standings in a minute
    0:30  the loop on the sample: twelve Try runs at three minutes each, seven applied
    1:10  GLM terms for the ten worst factors, each refit in seconds
    1:30  confirm on full rows, six minutes; two more points on t-boost's pruning path
    2:00  two hand edits; the readout: tied with Default boost, one factor noted; stop
    2:05  stress: five folds of everything, 30 minutes unattended, then 20 to read
    3:00  log, the refit running behind the list; 3 hours 15 in all, an hour of it waiting

**Small: property severity, 15,000 claims, 25 factors.** Assumed: five folds of any model in
under 10 seconds, so full rows and thorough from the start.

    0:00  set up, then look: wide ranges, a thin-level check on a 200-level trade code
    0:25  train the GLM and t-boost: all tied with Default boost, so work on the GLM
    0:30  four factors are beyond their range: leave the other 21 alone
    0:40  group the trade code inside its GLM term
    0:50  each factor left out in turn, four minutes: six add nothing, so Try without all six
    1:10  none of the last five changes paid: stop; one hand edit, a straight tail
    1:20  read each effect from fold to fold, and what the five largest claims drive
    1:40  log: the test score is in its band, with a wide range; 1 hour 50, little waiting

---

All three first-phase reports were present and used: 01 (walkthrough), 02 (state of the art), 03
(codebase). Sources in brackets are cards in 02 or sections in 03. Paths are under
`/home/ralph/suite/haute/src/haute/`, or under `/home/ralph/suite/` when they start with
`t-boost/`.

## Needs from the UI

Each is a place the workflow needs on screen. Where it goes is the UI designer's call.

1. **The empty state.** On a new node the All models menu is open, each section marked done or
   missing, and Train names what it waits for.
2. **Setup.** The "what is the target" choice (count, amount, rate, yes/no) with the
   one-sentence statement of the handling. Rows, exposure and claims in the training, validation
   and test rows. A split control that keeps a group whole and holds back the latest period as
   test rows, both at once.
3. **Run labels, everywhere a run shows.** Sample or full, quick or thorough, what-if,
   yardstick, not trained here, interrupted, failed with its reason. A row that is not like for
   like is greyed with the reason.
4. **The yardsticks.** No factors and Default boost in the standings and on the
   floor-to-strongest bar, marked as the tool's own.
5. **The queue.** What is training, what waits, the time and memory expected, cancel, and a
   what-if moved ahead.
6. **`Try`** beside every setting that can change a fit: a factor in or out, a direction, a
   fixed value, a model's own setting, a hand edit in its table pane, a point on t-boost's
   pruning path, and `Try with` on a new column. It asks one model or all, and shows the time
   first.
7. **What-if results** as rows in the standings with Apply, Keep and Drop. Many at once shown as
   one ranked picture.
8. **What changed.** One view for a what-if against the model's run, a new run against the one
   it replaced, and any two runs: only the settings that differ, score changes with the range on
   the difference, factors whose fit moved, how far predictions moved.
9. **The history** of a model: one line per training, with bring back. The GLM's step list is
   the same part.
10. **The readout** of five questions for the chosen model, always reachable, never a pop-up.
11. **The factor list:** a sort by share of misfit beyond its range, a "nothing clears its
    range" state, and marks for new, left out, direction, fixed value and reviewed.
12. **The chosen factor's settings in one step from any menu,** with keys for in or out,
    direction, reviewed and `Try without`. This must stay inside the settled lines: the settings
    sit in the menu, not in the list and not beside the chart.
13. **Checks:** a count in the menu row and a pane with each check's status, threshold, link to
    its fix and acknowledge-with-note. An error (a missing column) looks different and offers
    its two choices.
14. **Cannot honour.** On the model's entry and its standings row: a substitute (named,
    accepted), not honoured and so greyed as not like for like, or greyed in Add with the
    reason.
15. **Fixed values** in the factor's settings in the All models menu: the value, the base level
    named, and which model types carry it.
16. **The data-change banner** (after pre-processing, and at a refresh): columns, levels, rows,
    periods. And "open the node that makes this column" from a factor.
17. **The hand-edit preview:** actual against expected on the edited levels before and after,
    the factors disturbed, the total, the drafted note, and whether this model type lays the
    edit on top.
18. **t-boost's pane:** the pruning path with the chosen point, and what banding and graduation
    did to each table.
19. **The GLM's panes:** the chosen factor's term at the top of the GLM menu, its partial
    residual chart beside it, the refit-on-change state, the tests labelled "training rows"
    beside the difference on validation rows, and the aliased-terms report.
20. **The logging panel, in order:** the run; the list with the refit's progress; refit against
    reviewed; the expected band; unseal; the score, its range, the paired difference and the
    count of looks; the below-band choices; the note; Log.
21. **`Bring in a logged run`** in the run list, a "results are local" label on a reference
    model, and a banner when the data here differs from a run's data.
22. **Named segments:** where they are defined and listed.

## Needs from analytics

Each is a calculation the workflow relies on.

1. **A range on the difference between two runs,** paired on the same validation rows and
   resampled by policy, with a "tied" verdict when the gap is inside it (E6, E7). Every stopping
   rule rests on this.
2. **A range on actual at each level** that is right for the loss (claim counts; a bootstrap for
   amounts), and a range on each model's effect from the fold models (A7).
3. **Misfit by factor:** each factor's share of the chosen model's total misfit, counting only
   misfit beyond its range, weighted by exposure. The same for named segments (D2, D5).
4. **Like for like.** Each run records the rows it learned from, sample or full, and its depth.
   A comparison uses only rows neither run learned from, and reports how many.
5. **One fixed split per node:** a stable row key, a group kept whole with the latest period
   held back as test rows, and rows held back from the training rows for early stopping and
   tuning. Today the three gradient-boosting types stop on the validation rows
   (`modelling/_lightgbm.py:262`; 03 section 2a).
6. **Kept predictions** per run: validation rows for quick, every row out of fold for thorough,
   test rows at logging. Charts from them on demand, by any factor, a second factor or year.
7. **The yardsticks:** a no-factor model and a gradient-boosting model on defaults, and each
   model's place between the floor and the strongest (E5).
8. **What changed between two runs:** the settings that differ, score differences with their
   paired range, the fit movement by factor, and how far predictions move (the spread of the
   ratio, and the exposure moving more than a set amount), overall and by segment.
9. **What-if runs:** training from a changed config that is not saved; the one-per-factor batch;
   a ranked reading that says how many would clear their range by chance (D1).
10. **What is this model missing:** a small boosted model with the chosen model's prediction as
    the offset, ranking factor pairs on validation rows with size and effect thresholds (E10,
    D1).
11. **A factor by year:** the fit by year from kept predictions, and the effect by year as an
    interaction what-if (A3).
12. **The readout:** the gap to the strongest model with its range; counts beyond range; total
    actual against expected overall, by year and by segment; each effect's stability across
    years and folds; the tally of changes applied and how many cleared their range.
13. **Each check with a threshold:** one-factor strength against the target; identifier,
    constant, near-duplicate; thin level; a group on both sides of the split; new levels and mix
    shift against the last logged run; the training-against-validation gap by level (D4); flips
    between years; total off; a direction not held in the expected line (C5); one factor's share
    of the gain over the floor across models; a score jump against the last logged run; an edit
    against the free fit; an edit that breaks a direction.
14. **Hand edits:** a base conversion between a named base level and the table's average; edits
    keyed by level name or value range; the preview on the rows touched, the factors disturbed
    and the total (C2, C3); after a refit, which other tables moved (A4).
15. **t-boost:** reading its pruning report (sizes against out-of-bag deviance, the chosen
    point), passing a chosen point, and a per-table summary of banding and graduation. Haute
    reads none of this today (03 section 5.1).
16. **GLM:** a default term from a factor's type and number of values, with the level of most
    exposure as the base; binned partial residuals (E11); add and drop tests, AIC, BIC; an
    aliasing report; the link from the shared direction to a term's direction; a banded term
    (new).
17. **Time and memory estimates** per training, for the queue order, the defaults by size and
    the time a what-if will take. Haute has a memory estimate today (`routes/modelling.py:251`).
18. **Logging:** the final refit with the hand edits applied and t-boost's recorded choices
    replayed; refit against reviewed (largest move in each factor's effect, spread of the ratio
    of predictions); the band the test score should fall in; the test score with its range; the
    paired difference from the last logged model on the test rows; the count of looks (B1).
19. **Refresh:** what changed in the data since a run (rows, periods, columns, levels); scoring
    a logged model on the node's current rows; which rows a logged run learned from, read from
    what it logged.
20. **Severity:** how much the largest claims drive a level and a score.
21. **Reproducible runs:** the same config, data, split and seeds give the same run on another
    machine.

## Proposals and questions

**New proposals.** Each names the problem it answers (01 problem number, or the scenario).

- **W1. Setup that states the handling.** The analyst says what the target is; the tool states
  the handling in a sentence and shows rows, exposure and claims per set of rows. Role columns
  start left out. (Problem 4.)
- **W2. One fixed split per node,** keyed by an id column, keeping a group whole and holding
  back the latest period as test rows. No change of factors moves a row. (Problems 1 and 4; 03
  fact 2, which the engineer marks unverified.)
- **W3. Two yardsticks owned by the tool,** kept as reference models: No factors and Default
  boost. They are not the node's own models, so tuning never touches them. (Scenario 1, step 7.)
- **W4. Try, then Apply.** Any change can run as a what-if beside the model's run, and Apply
  makes that run the model's run with no training. A run is overwritten only when a training
  finishes. (Problems 6 and 14, inside the settled overwrite rule.)
- **W5. A history line per training,** with "what changed" worked out at the moment of
  replacement. The GLM's step list is the same part. (Problem 6.)
- **W6. Like for like.** Runs record what they learned from. A sample run samples the training
  rows only. Comparisons use rows neither run learned from. (Problem 1.)
- **W7. The readout and the stopping rules** in "Knowing when to stop". (New: the before files
  had no stopping rule.)
- **W8. Three outcomes when a model type cannot honour a shared setting.** (Problem 13.)
- **W9. Fixed values entered once,** in the factor's settings in the All models menu, against a
  named base level, carried by each model type that takes hand edits. Judgement edits stay per
  model and come last. (Problem 12; A4 backs fitting free first and showing who compensated.)
- **W10. Errors are not checks,** and checks gain thresholds, statuses, two leak checks after
  fitting, "direction not held" and "edit breaks a direction". (Problems 14 and 15; B5, C5.)
- **W11. A new column arrives left out,** with `Try with`. Thin levels have two homes. (Scenario
  2, step 6.)
- **W12. Before logging:** the refit runs behind the list; refit against reviewed; the band
  stated before unsealing; a rule for a score below the band; a permanent count of looks.
  (Problem 7; scenario 1, step 12; B1.)
- **W13. The refresh workflow,** with the data-change banner, `Bring in a logged run`, the split
  moved forward before anything is charted, the old model judged first on the period just
  released from the test rows, and a refresh-or-rebuild rule. (Problem 10.)
- **W14. Handover.** Reference models' names and settings go in the config. Reviews,
  acknowledged checks and notes go in a small file beside it. A logged run carries its kept
  predictions. A model with no local run shows "not trained here".
- **W15. The GLM refits on a term change** when its last fit took under about ten seconds,
  starts from default terms, and labels its tests as on training rows. (Scenario 6, steps 2, 5
  and 6.)
- **W16. Large data in three phases:** sample, confirm, stress. The queue runs fastest first and
  a what-if can jump it.
- **W17. Named segments** kept in the config and scored on every run (D5). The readout, the
  stress step and the refresh use them.

**Changes to the Proposed items.**

- **1.** Keep. The yardsticks are reference models, so "one model per model type" holds.
- **2.** Keep, and add: early stopping and tuning use rows held back from the training rows; a
  permanent count of looks at the test rows; the below-band rule.
- **3.** Change. The main device is a range on the difference between two runs, paired. Describe
  the thorough depth as quieter charts and a view of stability, not as a second untouched score
  (E6, E8).
- **4.** Keep, and add: a run records the rows it learned from and whether it is a sample; a
  logged run carries its kept predictions; a logged run brought in scores the current rows.
- **6.** Change. Train is a queue: one training runs at a time today
  (`routes/_training_lifecycle.py:1338`).
- **8.** Change. Sort by share of misfit beyond its range, not by "where a model fits worst",
  which ranks thin levels first. Review ticks and notes go in a file beside the config, not in
  it.
- **9.** Replace with W4 and W5.
- **10.** Change, as W10.
- **11.** Change. t-boost does every step in one training, and only pruning has a path of
  scores, with two or more bags (`t-boost/python/t_boost/sklearn.py:4108`). Picking a point on a
  curve is pruning only for now, and another point means boosting again. Curves for banding and
  graduation, re-applying a step in seconds, and keeping each stage as a reference model wait on
  t-boost work. For the GLM, "bands" need a new term type (`modelling/_glm_terms.py:50-61`).
- **12.** Change, as W12.
- **13.** Change. Defaults follow how long a training takes, not the row count alone.
- **14 and 18.** Add fixed values (W9).
- **15.** Add: an edit is keyed by a level's name or a range of values, and the tool drafts the
  "what" of its note (C4).
- **16.** Change. The preview leads with the rows touched, then the factors disturbed, then the
  total (C2, C3).
- **17.** Change. A fixed value is entered in the All models menu (W9) and then shows in each
  model's table pane as a hand edit. Judgement edits are still made in the table pane.
- **18 and 19.** Swap their order of arrival. Laid on top (19) is where every model type starts,
  because none can hold part of a table fixed today (03 section 5.2; I checked t-boost only, and
  the GLM and EBM are as the engineer reports). Refit around (18) is the goal.
- **5 and 7.** No change from the workflow side.

**Questions only the owner can answer,** each with the default this draft assumes.

1. May a model type leave a shared factor unused, so a GLM can be built forward? Default: no.
   Every factor that is in has a GLM term.
2. May the tool hold the replaced run until the next training? Default: no. W4 and W5 cover it.
3. Is a value the business fixes a shared setting? Default: yes, for the model types that take
   hand edits.
4. Where do reviews, acknowledged checks and their notes live? Default: a small file beside the
   config, in git.
5. Do reference models' names and settings go in the config? Default: yes. Their results stay
   local.
6. May a logged run carry its kept predictions, which are row-level data, to MLflow? Default:
   yes.
7. Can work in progress be shared without logging it? Default: no. A reviewer retrains from the
   committed config, or reviews a logged run.
8. Should the GLM refit by itself on a term change? Default: yes, under about ten seconds.
9. Which model type is Default boost? Default: LightGBM, or the first gradient-boosting type
   that supports the loss.
10. After a test score below the band, are the same test rows used again? Default: yes, marked
    as seen with the count. Fresh test rows come only with a new period.
11. Is a column in or out when it first appears? Default: at first setup every column without a
    role is in. A column that arrives later is left out.
12. Does a scheduled run log with checks open? Default: yes, with the checks recorded. Only an
    error stops it.
13. More than one training at a time? Default: no. A queue, as today.

## Concerns about settled items

The draft stays inside every settled line. Two lines cost the analyst something, and one needs
care.

- **"Factors and monotonicity are shared by every model", with "The GLM needs its own
  workflow".** Problem: a GLM is classically built forward, one factor at a time, and leaving a
  factor out of the GLM takes it from every model. The draft has the GLM start from every factor
  and simplify. Smallest change: a model type may leave a shared factor unused, and the factor
  list shows it.
- **"Training a model again overwrites its run".** Problem: after a direct Train, undo brings
  the settings back but not the run. The draft answers with Try and Apply, the overwrite only
  when a training finishes, and the history. Smallest change: hold the one replaced run until
  the next training.
- **"The rest of the model is refitted around it where the model type allows".** No model type
  allows it for part of a table today (03 section 5.2). The wording covers this. The draft
  starts every model type on "laid on top".

## Where each walkthrough problem is answered

1. Not like for like: fixed. Journey 4 and 5, What-if runs, Working fast, Refresh 6 (W6).
2. The range: the workflow now relies on the range on a difference. Its definition is passed to
   analytics (needs 1 and 2).
3. No place for what-if, checks, what changed: the workflow names each. The places are passed to
   the UI (needs 6, 7, 8, 13).
4. Setup mistakes: fixed. Journey 2 (W1, W2).
5. A factor's setting costs a menu switch: passed to the UI (need 12), with the keys in "Working
   fast".
6. Undo and run history: fixed inside the settled line. The loop (W4, W5). See the second
   concern.
7. The logged model is not the reviewed one: fixed. Before logging 3 (W12).
8. A GLM built forward: not fixed. It needs the first concern. Inside the line, the GLM
   simplifies from every factor.
9. The effect view: passed to analytics (need 14 covers the base conversion only).
10. No reference at a refresh: fixed. Refresh 1 to 4, and Review and handover (W13, W14).
11. Why a segment moved: partly. Refresh 5 reads movement by segment. The explain analysis and a
    segment filter are passed to analytics and the UI.
12. A fixed value as three edits: fixed. Hand edits (W9).
13. Cannot honour: fixed. Journey 4 (W8).
14. Failures: fixed. The loop, Checks, Failure and recovery (W4, W10).
15. A leak that inflates everything: fixed. Checks after fitting (W10).

Also answered from the scenarios: the empty state (Journey 1), which run is the yardstick
(Journey 4), a poor test score (Before logging 5), new rows (Refresh 1), where thin levels are
grouped (the round trip), and when the GLM refits. Not answered here: the demand curve for a
conversion model, which is an analytic the workflow does not depend on.
