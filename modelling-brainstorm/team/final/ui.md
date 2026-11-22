# UI

What is on screen. Loose, and proposed unless `overview.md` lists it as settled. It builds on the
view as it is today: factor names down the left, panes in the middle, settings on the right. What
each number means is in `analytics.md`. When each thing is used is in `workflow.md`.

Settled: "All the modelling for a node happens in the Modelling UI, for every model type, and later
for clustering." *Today* marks what Haute has now, with a path under
`/home/ralph/suite/haute/frontend/src/`, or under `/home/ralph/suite/` when it starts with `haute/`
or `rustystats/`. "The engineer" or "the red team" beside a claim means that member checked it and
the editor did not: unverified here.

## Rules for the screen

- **One screen.** No wizard and no dialog for the main work.
- **Three choices, one home each.** The factor: the list on the left. The model: its entry in the
  top row. The pane: the pane tabs.
- **Linked.** A factor, a model or a row filter chosen anywhere is chosen everywhere.
- **One symbol per model type:** a colour and a shape, the same on charts, entries and standings.
- **Differences shown.** A score sits with its difference from the `vs` run, the run picked to
  compare with, and a range on that difference.
- **State in words.** Training, out of date, failed, logged: a mark and a word, never colour alone.
- **Honest by default.** Every pane says which rows its figures use, with a mark when in-sample.
- **A warning links to its fix. Quiet by default:** detail appears for the factor or model chosen.

## The frame

The main working state: several models trained, a factor chosen. In the wireframes: `o # ^` a model
type's shape, `*` chosen, `~` out of date, `e2` two hand edits, `[` no clear difference from the
best. In the factor list: `###` the misfit bar, `r` reviewed, `-` left out, `+` new, `!` a check. In
the standings: `-` a reference model, and `L` the last logged run on the bar.

    +----------------------------------------------------------------------------------------------+
    | < Freq [o t-boost 3.4 e2]* [# GLM 3.2 ~] [^ LightGBM 3.5] [+]  vs Last logged v Train 1 v Log|
    +-------------+------------------------------------------------------+-------------------------+
    | / search    | t-boost trained: +0.1, no clear difference.       [x]| [All models]* [t-boost] |
    | By misfit v |------------------------------------------------------|-------------------------|
    | 3 clear     | Factor*  Compare  Checks 3  |  o t-boost: Tables Path| > Target  claim counts  |
    |-------------| veh_age   (Fit|Effect)   Rows: validation v   More v | > Rows    412k 103k 98k |
    |>veh_age ### | validation rows . 31,200 claims . 2 of 14 levels are | > Split   policy + 2025 |
    | region  ##  |   clearly off: too high at 0-2, too low at 10+       | > Metrics   > Segments  |
    | telemat ## -|   | I                                                | v Factors  46 of 60 in  |
    | mileage    !|   | o#^ I           I   actual, with its 95% range   |    region    in   .     |
    | ncd_yrs    r|   |  - - o#^  I     o   t-boost (chosen, heavy line) |   >veh_age   in   down  |
    | pol_type   r|   |  . . . - -o#^ I .   its effect (thin line)       |     fixed value: none   |
    | colour     -|   |           - - o#^   # GLM, from earlier settings |     made by: Clean veh  |
    | new_col    +|   +------------------   ^ LightGBM   - - Last logged |     Suggests: try it    |
    | ...         |   ###  ###  ##  ..  #   exposure, in its own strip   |      without the        |
    |             |                                                values|      direction   [Try v]|
    +-------------+------------------------------------------------------+-------------------------+

- **Top row.** Back, the node's name, an entry per model type, Add, `vs`, Train, Log. An entry shows
  the model type's symbol and name, its gain and its state, with training as a bar, never a bare
  percentage. A click chooses the model: its line is drawn heavier, and its panes and menu follow.
  Its small menu: train it, mute its line, remove it.
- **`vs`** picks the run every difference is taken from, drawn dashed. The default is the last
  logged run, else Default boost, else the floor. Its list holds the reference models, each with
  `Bring back` and `Remove`, the node's other models, and `Bring in a logged run`.
- **Train** trains what is out of date, and counts it. Its list is the queue. **Log** opens logging.
- **Notice line.** One line, only when there is news: a training finished, a what-if result, the
  data changed, what the tool picked, an error. An error is red and stays until fixed. On opening a
  node with a logged run it says where things stand: "Logged 12 Nov: t-boost refit, test rows gain
  3.3. Since: 3 settings changed, 41k new rows. [Details]". Closed notices stay in a list under it.
- **Pane tabs.** The shared panes first: Factor, Compare, Checks with its count. Then the chosen
  model's own panes, under its symbol and name. t-boost: Tables, Path, Curve, History. GLM: Terms,
  Tests, History. EBM: Terms, History. Gradient boosting: Curve, Importance, SHAP, Tuning, History.
- **Rows line.** Under a pane's controls: which rows, how many, the in-sample mark, any row filter
  as a tag that can be removed, and on the Factor pane the pattern in words. Under a filter made by
  hand, scores are labelled "after looking".

## The settings column

Settled: "There is one menu for the settings all models share, then a menu for each model type."
They are two tabs: `All models`, and the chosen model type. Settled: "A factor's settings sit in the
menu, not beside its chart."

- **All models,** top to bottom: Target (the column, what it is, exposure or weight, offset, loss,
  and the sentence stating the handling). Rows (the id column, rows and exposure per set of rows,
  the date up to which claims are mature, the record of looks at the test rows). Split. Metrics,
  with the Tweedie power for scoring. Segments. Factors.
- **Sections fold to one line** that states the setting. Factors is open by default. A suggestion
  sits beside its setting, with `Use`.
- **Factors is the table as today:** in or out, and the direction
  (`panels/modelling/CommonFeatureConfig.tsx`). The factor chosen on the left is highlighted in it
  and scrolled into view. Its row opens to show its fixed value, the node that makes its column, one
  suggestion with its evidence, and `Try`: with or without it, without its direction, by year. The
  table's header has `Try each factor left out`.
- **A model type's menu:** what it ignores, first. Then its steps or interactions, its parameters
  and tuning, its run (its labels, and `Keep as a reference model`), and its hand edits with notes
  and undo. `Try` sits beside each setting that changes a fit. Where the menu lists factors, as the
  GLM's terms do, the chosen factor's row is highlighted the same way.
- **What this costs.** With a model type's menu open, a factor's setting is two clicks away: the All
  models tab, then the row. A row pinned at the top of the column, with keys for in, out and
  direction, would make it one. That is at the edge of two settled lines: a concern, not drawn. Even
  the opened row goes beyond "highlighted". The stricter reading is highlight only.

## The factor list

Settled: "The factor list on the left is for choosing a factor to look at, not for settings."

- A search, a sort and a filter: all, to review, in, left out. The first factor starts chosen.
- Sort: strength on its own before any model is fitted, then the chosen model's misfit, with a bar.
  Left-out factors sort with the rest. Factors with no clear misfit are greyed and last.
- The line above the names says how many misfits are clear, or "nothing clear".
- Marks only: left out, direction, fixed value, new, reviewed, a check points at it. A reviewed tick
  records the model and run it was made on. It clears when that factor's misfit becomes clear again.

## The other states

**A new node.** The first missing section is open, and Train says what it waits for.

    | < Freq   no model types yet [+]                   Train: needs a target, an id, a split   Log|
    +-------------+------------------------------------------------------+-------------------------+
    | / search    | Factor*  Compare  Checks                             | [All models]*           |
    | age         |                                                      | ! Target      missing   |
    | region      |   Set the target to see actual by level.             |   column [claims v]     |
    | ncd_yrs     |   60 columns reach this node.                        |   it is (claim counts)  |
    | ...         |                                                      |    (claim sizes) (more) |
    |             |                                                      | ! Rows     id missing   |
    |             |                                                      | ! Split       missing   |
    |             |                                                      | . Factors     60 in     |

**Set up, no model yet.** The frame as above, with actual and exposure alone, on the training rows.
The list is sorted by strength on its own, and the pane says "Add a model type".

**Add, and the first training.** One list with tick boxes. The tool's pick of rows and evaluation is
marked, with the time, before anything starts. A model type that cannot train is greyed.

    | Add model types            [x] o t-boost   [x] # GLM   [ ] EBM   [ ] ^ LightGBM   [ ] XGBoost|
    |                           [x] Default boost, the tool's yardstick      [-] CatBoost: why     |
    | rows (Sample 10%) (Full)*       evaluation (Quick: validation rows)* (Thorough: 5 folds)     |
    | Picked full rows and quick: about 6 min for three trainings.                [Add and train]  |

**Compare.** Settled: "Any model type can be trained on the node's data, and the models are
compared." The standings stay on top, in the top row's order, with one view below. A is the chosen
model and B the `vs` run, so a pair is always ready. `[B]` on a row picks another B for this pane
only. A what-if row sits under its model, and its difference is against that model's run.

    | Factor  Compare*  Checks 3  |  o t-boost: Tables  Path  Curve  History                       |
    | validation rows . 103,100 rows . gain: % of the floor's deviance . differences vs Last logged|
    |    model             run             gain  vs Last logged        balance  size       time    |
    |  [ o t-boost  e2     full, thorough  3.4   +0.3 (+0.1 to +0.5)   1.00     38 tables  4 min   |
    |      : without ncd_yrs    what-if v  2.9   vs its own run: -0.5 (-0.7 to -0.3), clearly worse|
    |    # GLM ~ ignores 1 full, quick     3.2   no clear difference   1.00     46 terms   30 s    |
    |  [ ^ LightGBM        full, quick     3.5   +0.4 (+0.2 to +0.6)   0.98     600 trees  90 s    |
    |  [ - Default boost   yardstick       3.5   +0.4 (+0.2 to +0.6)   0.97                        |
    |    - Last logged     reference       3.1   (the vs run)          1.01     [Card]             |
    |  floor |--------------------------L---#------o-^| strongest so far                           |
    |  A = t-boost (click a row)      B = Last logged ([B] on any row, for this pane only)         |
    |  (Lift) (Calibration) (By segment)  A against B: (What changed) (Double lift) (Where and why)|

**Checks.** The five stopping questions on top, then the checks to look at. Each check has `Open`,
which goes to its fix, and `Note`, which acknowledges it. Clear and noted checks are folded away.

    | Factor  Compare  Checks 3*  |  o t-boost: Tables  Path  Curve  History                       |
    | Where t-boost stands . validation rows                                                       |
    |  1 signal left?    0.1 behind LightGBM: no clear difference                                  |
    |  2 factors wrong?  3 with a clear misfit: veh_age, region, telemat (left out)                |
    |  3 level right?    balance 1.00 overall . 2023 is 0.96, clearly off                          |
    |  4 does it hold?   ncd_yrs changes between years . spreads: run the thorough evaluation      |
    |  5 still paying?   2 of the last 5 changes applied were clear                                |
    | To look at (3)                                                          14 clear . 2 noted   |
    |  ! pol_id looks like an identifier: 98% of exposure in tiny levels           [Open] [Note]   |
    |  ! region and postcode_area carry the same information                       [Open] [Note]   |
    |  ! GLM: direction on ncd_yrs not kept on the validation rows                 [Open] [Note]   |

**A model's table, with a hand edit under way.** Settled: "An analyst may edit a fitted table or
relativity by hand before logging. An edit carries over when the model is trained again, and the
rest of the model is refitted around it where the model type allows." The pane opens on the chosen
factor's main table. `O` is a level set by hand, and `.` the fitted value behind it.

    | Factor  Compare  Checks 3  |  o t-boost: Tables*  Path  Curve  History                       |
    | ncd_yrs: main table . also in ncd_yrs x veh_age, not edited here   x rate, against (average)*|
    |                                    level  exposure  fitted  edited  spread across folds      |
    |  1.4 | o                           0      18%       1.38            1.31 to 1.45             |
    |  1.0 |---o--o--------------        ...                                                       |
    |      |        o   .  .  .        [ 7       3%       0.71    0.66    0.60 to 0.84 ]           |
    |  0.6 |            O--O--O        [ 8       2%       0.74    0.66    0.58 to 0.93 ]           |
    |      +-0--1--2- ... 7--8--9+     [ 9+      4%       0.63    0.66    0.57 to 0.70 ]           |
    | Hand edit, this model only. 7 to 9+:  Set [0.66]  Line  Smooth  Monotone  Cap  Floor         |
    | Preview. Laid on top of the fit: no other table moves now.               [Cancel] [Apply]    |
    |   rows this edit touches, 9% of exposure (validation rows, which you have seen):             |
    |     actual 0.67 . expected 0.69 -> 0.66                                                      |
    |   cost: on these rows, no clear difference (-0.02, -0.09 to +0.04) . overall, none           |
    |   would move in a refit: veh_age, region . total 1.000 -> 0.998       [Rescale the base]     |
    |   What: "Set 7 to 9+ to 0.66" (drafted)    Why: [                                  ]         |

- The tools: `Set` one value. `Line`: a straight line between the ends of the selection. `Smooth`: a
  smoothed curve through it. `Monotone`: the least change that makes it only rise or only fall.
  `Cap` and `Floor` limit it. The scale switches between the table's average and a named base level.
- No `Try` here: an edit laid on top applies with no training, and the model's menu lists it with an
  undo. A whole-table fix (`Fix the table`) does train, and gets `Try`, then a "what moved" list.
- A fixed value from the All models menu shows in the table as a locked level, changed where it was
  entered. Under the table: "Make hand edits last."

**The GLM's terms.** The term's settings are in the GLM's menu, and the pane shows the evidence.

    | Factor  Compare  Checks 3  |  # GLM: Terms*  Tests  History        | [All models]  [GLM]*    |
    | veh_age: spline, 4 knots . refit waits: t-boost is training        | > Family  Poisson, log  |
    |  partial residuals, binned      level  relativity  interval        | > Interactions  2       |
    |   | .  .                        0-2    1.00  base                  | v Terms  46             |
    |   |  \__  .                     3-5    0.91        0.88-0.95       |    region   levels      |
    |   |     \____ . .               6-9    0.84        0.80-0.88       |   >veh_age  (spline v)  |
    |   +---------------              10+    0.80        0.72-0.89       |     knots [4]   down,   |
    | in-sample: AIC 31,466 . this change on validation rows: +0.1       |     from All models     |
    | Suggests: bands at 3 and 8, from t-boost          [Try] [Use]      |    mileage  spline      |

**Training under way.** Settled: "Runs live locally. Training a model again overwrites its run,
unless the run is kept as a reference model." The old run stays on screen until the new one
finishes. Settled: "Training sets vary a lot in size." So the Train list offers sample or full rows
and quick or thorough evaluation.

    | < Freq [o t-boost |||..]* [# GLM 3.2 ~] [^ LightGBM waits] [+]   vs Last logged v  Training v|
    |                     +------------------------------------------------------------------------|
    |                     | o t-boost        boosting, round 212, about 2 min             [Cancel] |
    |                     | # GLM            waits, about 30 s                  [Move up] [Cancel] |
    |                     | ^ LightGBM       waits, about 90 s                  [Move up] [Cancel] |
    |                     | - Default boost  out of date, not queued                         [Add] |
    |                     | rows (Sample 10%) (Full)*    evaluation (Quick)* (Thorough: 5 folds)   |

**A what-if result.** It lands in the notice line and as a row in the standings. `T`, or `Try`,
first shows a toast: "Trying t-boost without ncd_yrs, about 4 min [Cancel]". `What changed` is one
view, used for any two runs.

    | What-if: t-boost without ncd_yrs. Clearly worse: -0.5 (-0.7 to -0.3).                     [x]|
    |   [Apply: leaves ncd_yrs out for all models. GLM and LightGBM go out of date]                |
    |   [Keep as a reference model]  [Drop]  [What changed]                                        |
    | Factor  Compare*  Checks 3  |  o t-boost: ...                  A against B: (What changed)*  |
    |  A = t-boost, what-if         B = t-boost's run           both full, quick, on the same rows |
    |  settings     ncd_yrs: in -> left out                     (only what differs)                |
    |  scores       gain 3.4 -> 2.9: -0.5 (-0.7 to -0.3), clearly worse . balance 1.00 -> 1.00     |
    |  fit moved    ncd_yrs, veh_age, region                    (each opens its factor chart)      |
    |  predictions  5% to 95% of the ratio: 0.93 to 1.09 . 22% of exposure moves by more than 5%   |

**Logging.** Settled: "A model is logged to MLflow. Ensembles are out for now." The panel takes the
settings column's place, drawn wide here for room. A Logging pane shows the evidence for the step in
hand: for step 3, each factor's largest move and how far predictions move. Nothing blocks: what is
open is named and recorded.

    | Log t-boost to MLflow                                                                    [x] |
    | 1 What is logged   a refit of t-boost on the training and validation rows (515k), with the   |
    |                    same settings and 2 hand edits. Refitting, about 4 min.        [Cancel]   |
    | 2 The list         factors reviewed 44 of 46 . checks 2 open, 3 noted . hand edits 2 of 2    |
    |                    confirmed . thorough evaluation run                                       |
    | 3 Refit against reviewed   1 flag: region's effect moved 2.6%          (shown in the pane)   |
    | 4 Test rows        98k, sealed, none seen before . expect a gain of 2.9 to 3.9               |
    |                    [Score the test rows]   waits for the refit                               |
    | 5 Note             drafted: what changed since Last logged.    Why: [                    ]   |
    | [Log to MLflow]    open: 2 factors to review                                                 |

- Once scored, step 4 reads: "gain 3.3 (2.9 to 3.7), inside the expected range . balance 0.99 . vs
  Last logged +0.2 (0.0 to +0.4) . look 1, recorded". Below the range, it offers the two choices in
  `workflow.md`. Logging scores the test rows if not yet done. Closing the panel stops the refit.

**The node's card.** Settled: "The node is only the connection point. It holds no config, nothing
flows on from it, and it shows what the model output is." A double-click opens the Modelling UI.
States: nothing logged, logged, settings or data changed since logged, training, error.

    +---------------------------------------------+     +--------------------------------------+
    | Frequency                             logged|     | Frequency                    training|
    | t-boost: claims per vehicle-year            |     | nothing logged yet                   |
    | logged 12 Nov: a refit on 515k rows         |     | t-boost [|||..]   2 waiting          |
    | judged once, on 98k test rows: gain 3.3     |     | [Open in Modelling]                  |
    | since: 3 settings changed, 41k new rows     |     +--------------------------------------+
    | [Open in Modelling]                         |
    +---------------------------------------------+

## Charts

Settled: "Clicking a factor charts actual against expected for every fitted model." So every fitted
model is drawn. A line can be muted to a hairline, never removed.

**Which model type: colour and shape,** fixed for every node. t-boost: a blue circle. GLM: an orange
square. EBM: a green triangle. LightGBM: a violet diamond. XGBoost: a gold down-triangle. CatBoost:
a cross. The UI designer tested these on Haute's dark surface for red-green colour blindness: the
first four pass together, gold is borderline, and no safe sixth colour was found. So colour is never
the only cue. Every line has its shape on its markers and its name at its end.

**Which run: stroke.** Each has one meaning.

| Stroke | Meaning |
|---|---|
| Solid, in the model's colour | a model's own run. The chosen model's is heavier |
| Thin solid, same colour | the chosen model's effect, on the fit view |
| Dotted, same colour | a what-if run of that model, drawn only while its row is chosen |
| Dashed, grey | a reference model. The `vs` run is brighter |
| Near-white, with whiskers | actual, and its range |

- Half strength means out of date and nothing else. The legend says "from earlier settings".
- A ring marks a level set by hand. An estimated effect has `~` before its name in the legend. On
  the effect view the legend also names the interactions each model holds.
- Shading is drawn for the chosen model only, labelled "95% range" or "spread across folds".
- **One frame, one y axis.** Exposure has its own strip under the plot, on the same x axis, as today
  (`panels/modelling/AveTab.tsx`).
- **Hover** lists every line at the nearest level: exposure and claims, actual with its range, each
  model's expected, and the largest claim's share. `values` opens a table that can be copied.
- **Rows:** validation by default, or every out-of-fold row once every model drawn has them.
  Training rows, with the in-sample mark. The rows the `vs` run never saw. Never test rows.
- **More:** the scale (rate, relativity, actual over expected, against the `vs` run). A split by a
  second factor or by period, as small charts on one shared axis. A grid for a pair of factors. The
  order of levels: natural, by exposure or by actual.
- The axis moves only with the factor or the scale, never with the rows or the models.
- Missing values, and levels not seen in training, are drawn as their own levels at the end. A
  factor with thousands of levels shows its top levels by exposure, with a search.

## Keys

Bare letter keys are new: almost every binding needs Ctrl or Cmd (`hooks/useKeyboardShortcuts.ts`,
the red team's check). So the set is small, with each key in a tooltip.

- Up and Down: the previous and next factor. `/`: search the factors.
- `R`: tick reviewed, and go to the next factor to review. `E`: Fit or Effect. `V`: validation or
  training rows, with the axis held, so overfitting shows as movement.
- `T`: try the chosen model without the chosen factor. No key changes a shared setting.
- Train gets a key of its own. Ctrl+Enter is taken: it refreshes the preview in the editor
  (`hooks/useKeyboardShortcuts.ts:92-94`). Undo, redo and save are as today
  (`modelling/ModellingView.tsx:187-198`).

## Where each thing lives

| Thing | Place |
|---|---|
| Setup, the id column, the mature-claims date, the record of looks | All models: Target, Rows |
| Named segments | All models: Segments. `Save as a segment` on a row filter |
| In or out, direction, fixed value, suggestions, `Try` | All models: Factors, the chosen row |
| A run's labels and marks (sample, other rows, ignores) | Entry, standings row, rows line |
| Cannot honour a shared setting | A substitute, accepted in the model's menu. Else "ignores" |
| Reference models, the yardsticks, `Bring in a logged run` | The `vs` list, and the standings |
| The queue, sample or full, quick or thorough, disk used | The Train list, and the Add list |
| What-if results | Notice line, standings row. A batch: one ranked list in Compare |
| What changed, history, bring back | Compare: A against B. Each model's History pane |
| The five stopping questions, the checks | Checks pane |
| Errors, changed data, where things stand | Notice line |
| Misfit, strength on its own, marks | Factor list |
| Fit, effect, ranges, over time, pairs | Factor pane: its views, Rows and More |
| Standings, the bar, balance, lift, calibration, Gini, residuals, segments | Compare |
| Double lift, how far predictions move, where and why | Compare: A against B |
| Hand edits: preview, cost, what moved | Tables or Terms pane. Listed in the model's menu |
| The pruning path, GLM tests, EBM shapes, tuning, SHAP, `Rebalance` | Each model's panes and menu |
| What is logged, refit against reviewed, test rows | Logging panel and pane |
| What was logged, and the model card | The node, the notice line, `Card` on the Last logged row |
| The Assistant, Git, Files | The settings column's place (`modelling/ModellingView.tsx:125`) |

## Nothing to show, and things going wrong

| When | The screen shows | It offers |
|---|---|---|
| A model has no run here | A hollow entry, "not trained here", and no line | Train |
| Nothing is clear | The factor list and the Checks pane say so | A signal to stop |
| A training fails or is cancelled | The entry says so. The old run stays | The reason, and a fix |
| A factor's column is gone | A red notice. Train stops | Leave it out, or open its node |
| The pipeline upstream fails | A red notice. The last runs stay viewable | A link to the node |
| A GLM will not fit | A notice naming the term | A simpler term |
| Logging fails | The panel keeps the refit and the recorded look | Retry |

## Building blocks

Settled: "The code stays clean and modular, with as little duplication as possible."

- **One chart frame** for fit, effect, lift, calibration, the learning curve and the pruning path:
  many lines, whiskers, the exposure strip, hover, small charts. *Today:* there are chart primitives
  and no shared frame (the engineer), and the fit chart draws one model
  (`panels/modelling/AveTab.tsx:46-63`).
- **One factor list,** from today's two (`modelling/ModellingView.tsx:424`,
  `panels/modelling/FeatureBrowser.tsx`).
- **One table editor** for t-boost, EBM and a GLM term with levels. t-boost and EBM share a table
  view today (`panels/modelling/TermTableView.tsx`). The GLM's tabs are per coefficient (the
  engineer), so its table per factor is new work, from `to_rate_tables`
  (`rustystats/python/rustystats/rate_tables.py:20`), which Haute does not call.
- **One run row,** for the standings, the `vs` list, the queue and History. **One score cell:** the
  gain, its difference from the `vs` run and its range, or "no clear difference". One notice line,
  one rows line, one folding section.
- **The model card gains tables and text, not charts.** It draws its own charts in Python today
  (`haute/src/haute/modelling/_charts.py`), so a new chart in the card would be built twice.
- A model type's module supplies its symbol, its menu, its own panes and its table editor's rules.
  The shell never asks which model type it is.

## Left out, and why

- **Clustering.** Settled: "Clustering is parked for now." Ensembles are out. Scheduling on the
  node's card comes later.
- **A pinned block for the chosen factor, and keys for in, out and direction:** at the edge of two
  settled lines. **A layout for narrow windows:** for now, the settings column folds to a drawer.
- **A benchmark line, "what the data can show", "off level" and "ranks risks":** cut in
  `analytics.md`. Also partial dependence as a shared chart, and a command palette.
- **Dragging a point on the factor chart to edit it.** That chart stays read-only. **Editing an
  interaction table:** main tables only, for now.
- **"The run it replaced" as a live line.** The settled overwrite rule means that run is gone. What
  changed is stored on its History line.
- **A light theme:** Haute's stylesheet is dark only (`index.css:10`). **A second y axis.**
