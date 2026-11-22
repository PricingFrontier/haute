# UI

What is on screen. Loose, and proposed unless `overview.md` lists it as settled. It builds
on the view as it is today: factor names down the left, panes in the middle, settings on
the right.

## Rules for the screen

- **One screen.** The main work needs no wizard and no pop-up.
- **Linked.** A factor chosen anywhere is chosen everywhere, and the same for a model.
- **One colour per model,** on every chart, in the menu row and in the standings.
- **Differences shown.** A number that can be compared shows its difference from the
  reference: the run it replaced, or a reference model.
- **State always visible:** what is training, what is out of date, what is logged.
- **A warning links to its fix.**
- **Quiet by default.** Detail appears for the factor or model in focus.

## Layout

    +--------------------------------------------------------------------------------+
    | <  Frequency   [All models] [t-boost] [GLM] [LightGBM] [+]   Compare Train Log |
    +-----------+---------------------------------------------+----------------------+
    | Search    |  Factor chart | Compare | the model's panes |  Settings for the    |
    |-----------|                                             |  menu chosen above:  |
    | age       |   actual, and each model's expected,        |                      |
    | region    |   by level                                  |  All models,         |
    | ncd_years |                                             |  or one model type   |
    | ...       |   exposure                                  |                      |
    +-----------+---------------------------------------------+----------------------+

- **Left:** the factor names, as now.
- **Top:** the menu row.
- **Middle:** the panes.
- **Right:** the settings of the menu chosen.

## The menu row

It replaces today's Factors and Boost steps.

- **All models** shows the shared settings on the right.
- **One entry per model type** in the node. Each shows the model's colour, its headline
  score and its state: trained, training with its progress, out of date, or failed. A mark
  shows when the model carries hand edits.
  Choosing one shows its settings on the right, brings its own panes into the middle, and
  emphasises its line on the charts. An eye on the entry hides the model from the charts.
- **Add (+)** lists the model types not yet in the node.
- **Compare** opens the comparison in the middle.
- **Train** trains whatever is out of date. Its menu holds sample or full rows, and quick
  or thorough evaluation.
- **Log** starts logging.

The menu row is therefore also the legend and a small league table.

## The factor list

As now: the names and a search. Ideas to add, kept quiet:

- **Sort:** by name, by importance in the chosen model, by where the chosen model fits
  worst, or by the factor's strength on its own.
- **Marks:** left out (the muted name today), and the factor's direction.
- **Reviewed:** a tick once the analyst has reviewed the factor, and a filter for the ones
  still to do. A tick clears when a later run moves that factor by more than its range.
- **Arrow keys** move through the list.

The list stays a way to choose a factor. Settings stay on the right.

## The factor chart

Settled: clicking a factor shows one chart of actual against expected, by the factor's
levels, for every fitted model. The chart holds no settings: a factor's settings are in
the All models menu. The first details of the chart:

- **Actual is drawn once.** Every model is fitted to the same rows, so their actuals agree.
  Each fitted model adds its own expected line.
- **Exposure bars** sit under the lines, by level.
- **Each model keeps one colour.** A model can be shown or hidden from the menu row.

Proposed additions:

- **Every factor charts every model,** including for a factor that is left out. The
  expected line comes from the model's predictions averaged by level, so it shows what the
  models miss without the factor. This relies on runs keeping their predictions.
- **Before any model is trained** the chart shows actual and exposure alone, so the same
  chart serves for looking at the data. It leaves the test rows out.

- **Two views.** *Fit* is actual against each model's expected. *Effect* is how each model
  treats the factor, as relativities, with the other factors' influence taken out. Fit
  shows whether a model is right at each level. Effect shows what the model is doing. A
  factor can fit well only because a correlated factor carries it, and only the effect
  view shows that.
- **A range on actual** at each level, from its exposure, so a thin level reads as thin.
- **Split by** a second factor or by year. The chart repeats for each value, one model at a
  time. This covers consistency over time and interactions.
- **Scale:** values, relativities, or the ratio of actual to expected.
- **Rows:** validation rows by default. Training rows, to see overfitting. All rows, after
  a thorough run.
- **Reference models** are drawn dashed. The last logged run is one of them.
- **Bands for a number:** equal exposure by default, with a choice of how many.
- **Hover** reads every model's value at a level. The values are also available as a table.

## The comparison

- **Standings:** one row per model and per reference model. Each score on validation rows with
  its range, its difference from the reference, a measure of size (tables, terms or trees),
  the time to train, and the state.
- **Floor to ceiling:** a bar from the model with no factors to the strongest model, with
  every model placed on it.
- **Lift and calibration,** with every model overlaid.
- **Double lift** between any two runs.
- **Where they disagree:** the segments where two models differ most, and which of them is
  closer to actual there.
- **By segment and by year:** the same scores broken down, to find a model that wins
  overall and loses somewhere that matters.

## A model's own panes

Supplied by its module, and shown when the model is chosen:

- **t-boost:** the learning curve, the tables, and the fit-against-simplicity curves for
  prune, band and graduate.
- **GLM:** terms and relativities with their ranges, coefficients, and the tests for
  adding or dropping a term.
- **EBM:** the term shapes.
- **Gradient boosting:** the learning curve, importance, SHAP and the tuning trials.

**Editing by hand.** For t-boost, the GLM and EBM, the table, relativity or term pane is
also where a value is edited:

- Click a level to set its value. Select a run of levels to set them to one value, a
  straight line or a smoothed curve, or to cap or floor them.
- The fitted value stays visible behind the edited one, and an edited level is marked.
- A preview shows at once, with the edit laid on the current fit and marked as a preview:
  the factor's fit, the total and the score.
- The model is then out of date. After training, the pane shows the fit around the edit,
  and which other tables moved to make room.
- The shared factor chart stays read-only. In its effect view, an edited model shows its
  edited line, with the fitted line faint behind it.

## The settings column

**All models,** top to bottom:

1. the data: rows, exposure, and a summary of the target
2. target, exposure or weight, offset, loss
3. split
4. metrics
5. factors: the table as now, with in or out and the direction. The factor chosen on the
   left is highlighted in it.

**A model type:**

1. anything it cannot honour from the shared settings, flagged at the top
2. its steps, where it has them
3. its parameters and tuning
4. its run, and its reference models
5. its hand edits, each with its note and an undo, and a line saying so if this model type
   lays edits on top of the fit rather than refitting around them

## Runs and reference models

- **A model's run:** the time, the settings and the scores of its last training. Training
  again overwrites it and shows the difference from the run it replaced.
- **Keep as a reference model** names the run and keeps it. It stays on the charts and in
  the standings as its own line.
- **Bring back** puts a reference model's settings back. For shared settings it warns that
  every model goes out of date.
- **Remove** a reference model once it is no longer needed.
- **Out of date:** a model whose settings have changed since its last run is marked. Its
  last run still shows, labelled as from the earlier settings.

## Logging

One panel, in the settings column's place:

1. the run to log
2. the checks and the factor reviews: each fixed, acknowledged or outstanding
3. the hand edits, each with its note
4. the test rows: scored now, shown once, and recorded
5. against the last logged run: the scores, and how far predictions move
6. a note
7. Log to MLflow

## The node in the pipeline

A card, with nothing to configure:

- the last logged run: its model type, headline score and date
- its state: nothing logged, logged, changed since logged, or training
- **Open in Modelling**

## Building blocks

To keep duplication down, the screen is made from a few parts used everywhere:

- **one chart frame** (axes, hover, a legend in the models' colours, a values table), used
  by fit, effect, lift, calibration, the learning curve and the fit-against-simplicity
  curves
- **one score cell:** the value, its range and its difference from the reference
- **one run list**
- **one settings section**

A model type's module supplies only its settings menu, its own panes and its effect.
