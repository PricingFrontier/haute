# Workflow

How an analyst gets from a new node to a logged model, and keeps it good afterwards. Loose, and
proposed unless `overview.md` lists it as settled. What each analysis works out is in
`analytics.md`. Where each thing sits on screen is in `ui.md`.

A step says **You** (the analyst), **Unasked** (the tool, by itself) or **Suggests** (offered, never
applied). *Today* marks what Haute does now, with a path from the haute repo's root, or from a
sibling repo where the path starts with `t-boost/` or `rustystats/`. A claim marked "not
rechecked" comes from the team's code survey.

## The journey

Settled: "All the modelling for a node happens in the Modelling UI, for every model type, and later
for clustering." Settled: "Clustering is parked for now."

1. **Open the node.** *Unasked:* on a new node the All models menu is open, each section marked done
   or missing, and Train names what it waits for. A node made the old way opens with its one model,
   its factors and its settings carried over.
2. **Set up once, for all models.** *You:* pick the target column and say what it is: claim counts,
   claim sizes, pure premium, yes/no or another amount. Then exposure or weight, offset, the id
   column, the split, and the date up to which claims are mature. *Unasked:* the tool states the
   handling in a sentence ("claims per vehicle-year, exposure as a log offset"), checks the id is
   unique, and shows the rows and exposure in each set of rows. The split is made once. A column
   with a role starts left out of every model. *Suggests:* the loss, and a split that keeps policies
   whole and holds back the latest mature period as test rows.
3. **Look before fitting.** *You:* arrow through the factors, reading actual and exposure by level
   on the training rows. Note what should not be rated on, and enter the values the business
   fixes. *Unasked:* the checks that need only the data.
4. **First fits.** *You:* tick model types in Add and press `Add and train`. Each tick is one
   model, and its factors start as proposal 59 in `overview.md` says. Leave out what should not be
   rated on and set the directions in a model's own tab, before or after its first training.
   *Unasked:* before it starts, the list says what will run and for how long: sample or full rows,
   quick or thorough evaluation, and Default boost, the tool's yardstick, already ticked.
   Trainings queue, and charts fill as each finishes. A model type that cannot honour a setting
   says so first: a substitute you accept, an "ignores" mark, or greyed in Add with the reason.
5. **Read the standings.** *You:* choose the models to carry on with. *Unasked:* each model's
   gain, its difference from the `vs` run (the run you compare with) and its range, and its place
   between the floor and the strongest model. Models with no clear difference from the best are
   bracketed.
6. **Improve, refine, adjust by hand, decide when to stop.** The sections below.
7. **Stress the favourite.** *You:* train every model, Default boost included, on full rows with the
   thorough evaluation. Read it by year, by segment and in calibration, with a double lift against
   the last logged run and the strongest model. Run "each factor left out in turn" on the favourite:
   a factor that carries most of the gain is a likely leak.
8. **Log.** See "Before logging". Settled: "The node is only the connection point. It holds no
   config, nothing flows on from it, and it shows what the model output is." The logged run stays as
   a reference model for the next round.

**The two yardsticks.** The floor is one rate and needs no training. Default boost is a
gradient-boosting model on defaults, kept as a reference model so that tuning never touches it. Its
factors are set as proposal 61 in `overview.md` says. It goes out of date like any model, and is retrained when you ask. *Today:* one training runs at a time
(`src/haute/routes/_training_lifecycle.py:1338-1341`), so Train is a queue.

## The loop

    pick a factor -> read its chart -> try one change -> read what changed -> apply or drop

- **Pick.** *Unasked:* the factor list is sorted by misfit and says how many are clear. When none
  is, it says so: a signal to stop.
- **Read.** *Unasked:* the chart names the pattern in words ("too high at 0 to 2, too low at 10+").
  *Suggests:* one move for this factor and this model, with its evidence. The moves: try with the
  factor, try without its direction, look at it with a partner or by year, change its GLM term,
  change its column in pre-processing.
- **Try one change.** *You:* press `Try` on a shared setting or a model's own setting, such as one
  of its factors. *Unasked:* a
  what-if run trains beside the model's run, and nothing goes out of date.
- **Read what changed.** *Unasked:* only the settings that differ, the difference in gain with its
  range, the factors whose fit moved, and how far predictions moved.
- **Apply or drop.** Apply makes the what-if run the model's run, with no training. Drop leaves
  everything as it was. Backing out of an idea never costs a training.

Settled: "Runs live locally. Training a model again overwrites its run, unless the run is kept as a
reference model." So the direct route stays: change a setting and press Train. The run is
overwritten when the new training finishes, never when it fails or is cancelled. At that moment the
tool works out what changed and adds a line to the model's history: the change, the scores, and a
way to bring the settings back. Undo takes the settings back and leaves the model out of date, so
first keep a run worth returning to.

## What-if runs

- **Kinds:** with or without a factor; with or without a direction; one changed setting; each factor
  left out in turn; a factor by year, for the GLM and EBM.
- *You:* start one where the setting lives. *Unasked:* the tool names the model and the time first
  ("Trying t-boost without ncd_yrs, about 4 min"). It uses the same rows and evaluation as its
  model's run, so Apply loses nothing. It is judged on the validation rows, against that run.
- The result is a row in the standings marked what-if, with `Apply`, `Keep as a reference model` and
  `Drop`. It goes out of date like any run, and is never dropped unasked.
- **Apply says what it does to the others.** Factors and directions belong to each model, so
  applying one changes that model only. A shared setting tried on one model puts the other models
  out of date when applied. The button says which.
- **A batch is one list,** ranked, with only the clear results called clear.

## Several models of one type

Settled: "Several models of one model type can be trained in a node, for example two totally
different t-boost models with different factors." Settled: "A model's configuration can be copied."

- *You:* `Duplicate` a model from its entry's small menu, name the copy, and change its factors or
  directions in its own tab. For example a t-boost model on every factor beside one on a short list.
- *Unasked:* the copy has no run until it trains, and Train counts it. The two are drawn and
  compared like any two models, told apart by name.
- A what-if run tries one change on its model. A second model is for a different model kept side
  by side, with its own history.

## Knowing when to stop

*Unasked:* five questions about the chosen model, at the top of the Checks pane. They inform and
never block. The checks under them, with their rules, are listed in `analytics.md`.

1. **Is there signal left?** The difference from the strongest model, with its range.
2. **Is any factor still wrong?** The factors and named segments with a clear misfit.
3. **Is the level right?** Balance: overall, by year, by segment.
4. **Does it hold?** Patterns that change between years, and each effect's spread across folds.
5. **Are changes still paying?** Of the last five changes applied, how many were clear.

- Stop the loop when 2, 3 and 4 are clean, or when none of the last five changes was clear.
- A gap in 1 that remains is accepted with a note: "three-way effects we choose not to rate on".
- Between models with no clear difference, the analyst picks. The standings show size and time to
  train, and size compares only within a model type.
- A good score is not enough: an open leak check or a level that is off comes first.

## The round trip to pre-processing

Settled: "Feature engineering stays in the pre-processing nodes."

- *You:* from a factor, one click opens the node that makes its column. Change it there and come
  back. Capping large claims is done there too.
- *Unasked:* a notice says what changed in the data: columns, levels, rows. A new column arrives
  marked new and left out of every model, so nothing goes out of date by surprise. A changed column
  puts every model it is in out of date. The split stays put.
- *Suggests:* a banding, a grouping or a combination, each with its evidence. And making the change
  as a new column beside the old, then `Try with` on one model before swapping.
- **Thin levels have two homes.** Grouping levels for every model is feature engineering. Grouping
  them inside one GLM term is how that factor enters that one model, and makes no column.

## Hand edits and fixed values

Settled: "An analyst may edit a fitted table or relativity by hand before logging. An edit carries
over when the model is trained again, and the rest of the model is refitted around it where the
model type allows."

Two things are typed by hand, and the screen names them apart.
- **A fixed value** is the business's rule, entered once in the All models menu against a base level
  you name. A whole factor fixed this way goes in as an offset, so all six model types honour it and
  refit around it. A single level, such as the top no-claims level, is carried only by the model
  types whose model is tables. The rest show "ignores".
- **A hand edit** is your judgement on one model: t-boost, EBM, or a GLM term with levels. It is
  made in that model's table pane.
- **Fixed values first, hand edits last.** Every later fit has to work around the edits already
  made, so improve the model through its settings while that works.
- **One edit, one reason.** *Unasked:* the tool drafts what the edit did. *You:* write why.
- **Read the cost on the rows the edit touches,** since the overall score barely moves. "No clear
  difference" is the good answer.
- **Part of a table is laid on top of the fit,** and applies at once with no training. The pane says
  so, and offers to rescale the base so the total stays in balance. **A whole table is refitted
  around:** the model goes out of date, and after training the pane shows which other tables moved.
- An edit is stored against a level's name or a range of values, never a position in a table. Bands
  move between fits, so this is what lets an edit carry over.
- **Only main tables are edited.** A main table is the factor's average effect, and its interaction
  tables still vary around it. A GLM curve has no level to edit, so fix the whole factor.

What Haute and its libraries can do of this today is in `analytics.md`.

## Workflows by model type

**t-boost.** One training boosts, then prunes, bands and graduates. *You:* train on the defaults and
read the tables by importance. *Unasked:* the learning curve, the pruning path (fit against the
number of tables), and what banding and graduation did to each table. *You:* `Try` a looser or
tighter pruning rule, and stop at the simplest tables with no clear difference from the unpruned
model. *Today:* only pruning has a path, and another point on it means boosting again
(`t-boost/python/t_boost/sklearn.py:4108`, `:4281-4284`).

The proposed [staged workflow](staged-tboost.md) changes this: build the GBM and its path
first, then prune, band and smooth without reboosting. Ralph's follow-up puts separate
hyperparameter boxes and action buttons in Training on the right, replacing toolbar Train;
stage prediction visibility stays in the model controls on the left. This is not built yet.

**Gradient boosting (LightGBM, XGBoost, CatBoost).** *You:* train on defaults, then tune with a
number of trials. *Unasked:* the time expected. Tuning and early stopping use rows held back from
the training rows. Stop when tuned against defaults shows no clear difference.

**EBM.** *You:* train the main effects, read the term shapes by importance, then choose the
interactions. *Suggests:* the pairs t-boost kept. *Unasked:* a flag where a factor with a direction
cannot join an interaction (`src/haute/modelling/_descriptors.py:596-603`).

**GLM.** Settled: "The GLM needs its own workflow." Settled: "Each model has its own factors." A
tree model takes a factor as it comes. A GLM must be told how each enters.

- **It trains with no setup.** *Unasked:* each factor that is in the GLM gets a default term: its
  levels for a category, a smooth curve for a number, and a monotone curve for a factor with a
  direction in its factors. Where no term can hold the direction, the GLM carries the "ignores"
  mark.
- **Or it is built forward.** *You:* start the GLM on a short list of its own factors, then add one
  at a time with `Try with`. The other models keep their factors.
- **Then term by term.** *You:* take the top factor of the worklist and set its shape: grouped
  levels, bands, a spline or a polynomial. *Suggests:* a shape from its partial residuals, and bands
  from where t-boost banded the factor.
- **Refit on change.** *Unasked:* a term change refits the GLM at once when its last fit was short
  and nothing else is training. Otherwise it joins the queue, and the screen says it waits. Each
  refit adds a line to the GLM's history, and any line brings its terms back.
- **Its own diagnostics:** relativities with intervals, a test per term, AIC, BIC and a report of
  aliased terms. They use the training rows, and say so.
- **Interactions are added by hand.** *Suggests:* the ones t-boost kept.
- **Every factor that is in the GLM has a term.** A factor the GLM does not need is left out of the
  GLM, once `Try without` shows it adds nothing clear, or kept as its simplest term.

*Today:* four of the eight term types take a direction, and levels do not
(`src/haute/modelling/_glm_terms.py:50-61`). No term is linked to the direction in the factors table, and none is banded.

## Before logging

Settled: "A model is logged to MLflow. Ensembles are out for now." Nothing here blocks.

1. **Press Log on a run.** *Unasked:* the panel says what will be logged: a refit of this model on
   the training and validation rows together, with the same settings and its hand edits. The refit
   joins the queue, with its progress and a Cancel.
2. **Go through the list while it trains.** Each factor reviewed, each check fixed or acknowledged,
   each hand edit confirmed with its note, the thorough evaluation run.
3. **Refit against reviewed.** The model logged is the refit, not the fit you reviewed. *Unasked:*
   the largest move in each factor's effect, flagged when large.
4. **Score the test rows,** once the refit is done. *Unasked:* first the expected range for the
   score. Then the gain with its range, balance, the difference from the last logged model, and how
   far predictions move. The look is recorded against the rows for good, whether or not you log.
5. **If the score is below the expected range:** log anyway with a note, or look for a cause that is
   not tuning: drift, a leak, one segment. Never change the model to chase the test score. Later
   scores on these rows are marked as seen, and fresh test rows come with the next period.
6. **Note and log.** *Unasked:* a drafted note of what changed since the last logged run. *You:* add
   why. Afterwards the node and the notice line say what was logged and on which rows it was judged.
   The model card takes the notes, reviews, checks, hand edits and history, as tables and text.

## Refreshing a logged model

1. **Open the node.** *Unasked:* a line says where things stand: what was logged and when, and what
   has changed since in the settings and the data. Every model is out of date.
2. **Move the split forward.** *Unasked:* new rows stay out of every chart until the split places
   them. *Suggests:* the newest mature period as the new test rows. Other rows keep their place.
3. **Get the reference.** The last logged run is here as a reference model if it was logged on this
   machine. If not, `Bring in a logged run`: the logged model then scores today's rows.
4. **Judge the old model first,** on rows it never learned from: the period just released from the
   test rows. Actual against expected by year and by factor, before any training.
5. **Train all** on the saved settings. Hand edits carry over. Read what changed against the logged
   run: gain, how far predictions move by segment, which effects moved.
6. **Refresh or rebuild?** Log the refresh when no factor has a clear misfit and the data explains
   the moves. Otherwise enter the loop at the factors that moved.

Settled: "Scheduling may come later. The model type a schedule trains and logs is picked when the
schedule is set up." With two models of that type, which one it trains is concern H. A scheduled
run is step 5 and the logging. An error stops it.

## Handover, and things going wrong

Runs live locally, so only the config and what is logged travel.
- **Handing over.** *You:* commit the config. It holds the shared settings, a block per model with
  its factors and its own settings, the fixed values, the hand edits with their notes, the named segments, and each reference model's name and settings. Reviews
  and acknowledged checks stay local until logging, when they go into the model card, so they do not
  travel with work in progress.
- **Picking it up.** Each model shows "not trained here". Train rebuilds runs near the originals.
- **A training fails or is cancelled.** The old run stays. The entry names the reason and a fix.
- **A restart.** Runs, reference models, the history and the queue are on disk. *Today:* a restart
  loses every run (`src/haute/routes/_job_store.py:1-4`).
- **A factor's column is gone.** An error, not a check. Train stops with two choices: leave the
  factor out, recorded as a change, or open the node that made it.
- **Setup was wrong.** Change it: every model goes out of date, and the record of looks is kept.
- **Logging fails.** The refit is kept, so retry. **The logged model cannot score today's columns:**
  it is compared through its kept predictions only, and the tool says so.
- **Disk.** The tool shows what the runs take. What-if runs and fold predictions go first.

## Working fast

Settled: "Training sets vary a lot in size."

- **Small data: noise is the risk.** The thorough evaluation is the default, because the charts by
  level and the worklist then use every row. Most differences between runs read "no clear
  difference", so work from the factor charts and stop early.
- **Large data: time is the risk.** Work on sample rows, confirm on full rows, then stress with the
  thorough evaluation, unattended. A sample run samples the training rows only, and is labelled.
- **Defaults follow the time a training takes,** not the row count alone. The tool says what it
  picked before the first training starts.
- **The queue** trains only what is out of date, fastest first. A what-if run can move ahead of a
  long training that has not started. Charts come from kept predictions, so looking never waits.
- **What a loop of seconds needs first:** the node's data cached per data version, and a worker that
  stays warm. No timing here has been measured. *Today:* each training builds its data from the
  pipeline and starts a new process (not rechecked).
- **The Assistant** is mounted in the view today
  (`frontend/src/modelling/ModellingView.tsx:137-143`). What it would do here is not designed.

## The order to build in

The order is in `plan.md`: one aspect at a time, each one something to play with before the
next is planned. The team's own order put the foundations first: a run store, the id column,
the split made once, kept predictions and rows held back for early stopping. `plan.md` pulls
each of those in at the phase that first needs it.

**Left out of this design:** ensembles (out for now), sharing work in progress without logging, a
view that checks claim counts and claim sizes together, and more than one training at a time.
