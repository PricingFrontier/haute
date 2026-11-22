# UI

The screen an analyst works in all day. Settled: "All the modelling for a node happens in the
Modelling UI, for every model type, and later for clustering." Everything else here is proposed,
except lines marked "Settled:", which quote the owner word for word. It builds on today's view
(`modelling/ModellingView.tsx`): factor names down the left, panes in the middle, settings on the
right. Paths are under `/home/ralph/suite/haute/frontend/src/`. "03" is the engineer's report.
What it fixes in the earlier `ui.md`:

- **The menu row carried too much, and overlapped the pane tabs.** It keeps the model types, Add,
  the reference, Train and Log. `All models` moves to the settings column. `Compare` and `Checks`
  become panes. Three choices now have one home each.
- **Checks, reference models, "what changed", what-if runs and a run's state had no place.** Each
  has one now. So do empty states and failures. See the last three sections.
- **The All models menu was long.** Its sections fold to a line each, under the chosen factor.

## Rules for the screen

- **One screen.** No wizard and no dialog for the main work. Three small lists drop from the
  header: Add, the reference, the queue.
- **Three choices, one home each.** The factor: the list on the left. The model: the model strip.
  The pane: the pane tabs. The settings column follows the model.
- **Linked.** A factor, a model or a row filter chosen anywhere is chosen everywhere.
- **One mark per model type:** a colour and a shape, the same on charts, chips and standings.
- **Differences shown.** A score sits with its difference from the reference, and the range of
  that difference.
- **State in words.** Training, out of date, failed, logged: a mark and a word, never colour alone.
- **Honest by default.** Every pane says which rows its figures use. In-sample figures are tagged.
- **A warning links to its fix. Quiet by default:** detail appears for the factor or model chosen.

## The frame, in the main working state (state 3)

Several models trained, a factor chosen. The factor list is 180px wide (`haute-ui/palette.css:12`)
and the settings column 400px (`ModellingView.tsx:397`). Each strip in the middle is one row, and
the chart takes the rest. Marks in every wireframe: `o # ^ <> \/ x` the model types' shapes, `*`
chosen, `~` out of date, `e2` two hand edits, `=` tied with the strongest, `v` a list. `[views]`
is today's switch between Haute's views.

    +------------------------------------------------------------------------------------------------+
    | < Freq  [o t-boost 96% = e2]* [# GLM 88% ~] [^ LightGBM 100%] [+]  vs Logged v  Train 1 v  Log |
    +-------------+--------------------------------------------------------+-------------------------+
    | / search    | t-boost trained: no clear difference. [What changed] x | [All models]* [t-boost] |
    | By misfit   |--------------------------------------------------------|-------------------------|
    | 3 clear,    | Factor*  Compare  Checks 3  |  o t-boost: Tables Curve | veh_age                 |
    |  3 by chance| veh_age  (Fit|Effect) Rows(Val|Train) Scale v Split v  |  [x] in   direction (-) |
    |-------------| validation rows . 31,200 claims . 2 of 14 levels are   |  fixed value: none      |
    |>veh_age ### |   outside the range, about 1 expected by chance        |  [Try without] [Try v]  |
    | region  ##  |   | I                                                  |  made by: Clean vehicle |
    | ncd_yrs #  r|   | o#^ I            I   actual, with its 95% range    |  [ ] reviewed  note...  |
    | mileage .  r|   |  - - o#^  I      o   t-boost (chosen, heavy line)  |-------------------------|
    | pol_type   r|   |       - - o#^ I  #   GLM, from earlier settings    | > Target  claims, count |
    | colour  -   |   |            - - -o#^  ^   LightGBM                  | > Rows    412k 103k 98k |
    | new_col +   |   +--------------------  - - Last logged (reference)   | > Split   policy + 2025 |
    | ...         |   ###  ###  ##  ...  #   exposure, in its own strip    | > Metrics   > Segments  |
    | [views]     | 4-6: actual 0.081 (0.076-0.087), t-boost 0.083  values | v Factors 46 of 60 in   |
    +-------------+--------------------------------------------------------+-------------------------+

Settled: "Any model type can be trained on the node's data, and the models are compared."

| Part | What it holds |
|---|---|
| Model strip | A chip per model type in the node, in a fixed order: mark, name, place on the span from the floor to the strongest model, state. A click chooses the model. Its menu: train it, mute its line, remove it |
| Add (+) | The other model types, with tick boxes and `Add and train`. One that cannot train here is greyed, with the reason |
| vs | The reference. Every difference is taken from it, and it is drawn dashed. Default: the last logged run, else Default boost |
| The vs list | The reference models, the yardsticks, the benchmark and the node's other models, each with an eye. A reference model's row has `Bring back` and `Remove`. Also `Bring in a logged run` |
| Train, Log | Train trains what is out of date, and counts it. Its list is the queue (state 6). Log opens logging (state 8) |
| Notice line | One line, only when there is news: a training finished, a what-if result, the data changed, the tool picked sample rows, or an error. An error is red and stays until fixed |
| Pane tabs | Shared panes first: Factor, Compare, Checks with its count. Then the chosen model's own panes, headed by its mark and name. Choosing another model swaps that group only |
| Own panes | t-boost: Tables, Curve, Simplify, History. GLM: Terms, Tests, History. EBM: Terms, History. LightGBM, XGBoost, CatBoost: Curve, Importance, SHAP, Tuning, History |
| Figures line | Which rows, how many, which runs, the in-sample tag, and any row filter as a chip that can be removed |

**Settings column.** Settled: "There is one menu for the settings all models share, then a menu
for each model type." Two tabs: `All models`, and the chosen model type.

- **Each menu opens on the chosen factor.** Settled: "A factor's settings sit in the menu, not
  beside its chart." Settled: "Factors and monotonicity are shared by every model." So in, out,
  direction and a fixed value are in the All models block only. A model type's block holds what
  that model does with the factor: the GLM's term, a table's hand edits.
- **Sections fold to one line** that states the setting. One is open at a time.
- **All models:** Target (what it is, exposure or weight, offset, loss, and the sentence stating
  the handling), Rows (rows, exposure and claims per set, the count of looks at the test rows),
  Split, Metrics, Segments, Factors (the table as today).
- **A model type:** what it cannot honour, first. Then steps or interactions, parameters and
  tuning, its run (labels, `Keep as a reference model`), its hand edits with notes and undo.
- **`Try`** sits beside every setting that changes a fit. It starts a what-if run.
- **`made by`** opens the node that makes the factor's column. Settled: "Feature engineering
  stays in the pre-processing nodes."

**Factor list.** Settled: "The factor list on the left is for choosing a factor to look at, not
for settings." It has a search, a sort and a filter (all, to review, in, left out).

- Sort: strength on its own before any model is trained, then the chosen model's misfit. A bar
  shows the measure. A factor with no clear misfit is greyed and last.
- Marks: left out (muted), direction, fixed value, new, reviewed, a dot when a check points at it.
- The line above the names counts the clear misfits, and how many chance alone would give.

## The other nine states

**State 1. A new node, nothing set up.** The first missing section is open. Train says what it
waits for. The pane names the one next step.

    | < Freq   no model types yet [+]                              Train: needs a target         Log |
    +-------------+--------------------------------------------------------+-------------------------+
    | / search    | Factor*  Compare  Checks                               | [All models]*           |
    | age         |                                                        | ! Target      missing   |
    | region      |   Set the target to see actual by level.               |   What is the target?   |
    | ncd_years   |   60 columns reach this node.                          |   (count) (amount)      |
    | ...         |                                                        |   (rate) (yes/no)       |
    |             |                                                        | ! Split       missing   |
    |             |                                                        | . Factors     60 in     |

**State 2. Set up, no model trained.** The same chart serves for looking at the data: actual and
exposure, on validation rows. The test rows are never used.

    | < Freq   [+ Add a model type]               vs No factors v   Train: needs a model type    Log |
    +-------------+--------------------------------------------------------+-------------------------+
    | By strength | Factor*  Compare  Checks 2                             | [All models]*           |
    |>veh_age ### | veh_age  (Fit|Effect) Rows(Val|Train) Scale v Split v  | veh_age                 |
    | region  ##  | validation rows . 103,100 rows . 7,800 claims          |  [x] in   direction (-) |
    | pol_id  -   |   |    I   I                                           |  made by: Clean vehicle |
    | ...         |   |          I   I   actual, with its 95% range        |-------------------------|
    |             |   ###  ###  ##   #   exposure                          | > Target  claims, count |
    |             | Add a model type to draw its expected line.            | > Rows  > Split  ...    |

**State 4. The comparison.** The standings stay on top, and one view shows below them. A is the
chosen model and B the reference, so a pair is always ready. A click on a row makes it A or B,
and a what-if run opens against its own model's run. `[` joins tied models.

    | Factor  Compare*  Checks 3  |  o t-boost: Tables  Curve  Simplify  History                     |
    | validation rows . 103,100 rows . gain as a share of the span . differences are vs Last logged  |
    |    model                    run             gain  vs reference          balance  off level     |
    |  [ o t-boost   e2           full, thorough   96%  +3.1 (+1.9 to +4.4)    1.00     0.1          |
    |  [ ^ LightGBM               full, quick     100%  +4.2 (+2.8 to +5.5)    0.98     0.4          |
    |    # GLM       ~            full, quick      88%  no clear difference    1.00     0.1          |
    |      : without ncd_years    what-if          85%  clearly worse     [Apply] [Keep] [Drop]      |
    |    - Last logged            reference        93%  the reference          1.01                  |
    |    - Default boost          yardstick        97%  +3.4 (+2.2 to +4.7)    0.97                  |
    |    ! CatBoost               not like for like: trained on Tweedie, not Gamma                   |
    |  floor |------------------------#----L--o-D^| strongest so far.  Gaps under 1.5 cannot be seen |
    |  (Lift) (Calibration) (By segment)   A against B: (What changed) (Double lift) (Where and why) |

**State 5a. t-boost's tables, with a hand edit under way.** Settled: "An analyst may edit a fitted
table or relativity by hand before logging. An edit carries over when the model is trained again,
and the rest of the model is refitted around it where the model type allows." The pane opens on
the chosen factor's table. `O` is a level set by hand, and `.` the fitted value behind it.

    | Factor  Compare  Checks 3  |  o t-boost: Tables*  Curve  Simplify  History                     |
    | ncd_years:  (main table)*  (ncd_years x veh_age)          (This factor | All tables)           |
    |  x rate                              level   mass   fitted  edited   spread across folds       |
    |  1.4 | o                             0       18%    1.38             1.31 to 1.45              |
    |  1.0 |---o--o--------------          ...                                                       |
    |      |        o   .  .  .          [ 7        3%    0.71    0.66     0.60 to 0.84 ]            |
    |  0.6 |            O--O--O          [ 8        2%    0.74    0.66     0.58 to 0.93 ]            |
    |      +-0--1--2- ... 7--8--9+       [ 9+       4%    0.63    0.66     0.57 to 0.70 ]            |
    | 7 to 9+:  Set [0.66]  Line  Smooth  Monotone  Cap  Floor     [Try this edit] [Cancel] [Apply]  |
    | Preview. This model type lays an edit on top of its fit: nothing else moves.                   |
    |   on the rows you were looking at, 9% of exposure: actual 0.67 . expected 0.69 -> 0.66         |
    |   will disturb: veh_age, region .  total 1.000 -> 0.998 [Rebalance]                            |
    |   Note: "Set 7 to 9+ to 0.66" (drafted)    Why: [                                    ]         |

**State 5b. The GLM's terms.** Settled: "The GLM needs its own workflow." The term's settings are
in the GLM's menu, on the right. The pane shows the evidence for them.

    | Factor  Compare  Checks 3  |  # GLM: Terms*  Tests  History  | [All models]  [GLM]*            |
    | veh_age: spline, 4 knots.  Refits on change, last fit 3 s    | veh_age                         |
    |  partial residuals, binned     level  relativity  95% range  |  term (spline v)  knots [4]     |
    |   | .  .                       0-2    1.00  base             |  direction: up, from All models |
    |   |  \__  .                    3-5    0.91        0.88-0.95  |  Suggests: bands at 3 and 8,    |
    |   |     \____ . .              6-9    0.84        0.80-0.88  |   from t-boost [Try] [Use]      |
    |   +---------------             10+    0.80        0.72-0.89  |---------------------------------|
    | in-sample: deviance 31,402 . AIC 31,466 . BIC 31,712         | > Interactions 2   > Family     |

**State 6. Training under way, and a model out of date.** Settled: "Runs live locally. Training a
model again overwrites its run, unless the run is kept as a reference model." The old run stays on
screen until the new one finishes. An out-of-date line is drawn at half strength, and the legend
says "from earlier settings". Settled: "Training sets vary a lot in size." So the Train list
offers sample or full rows and quick or thorough, with the tool's pick marked.

    | < Freq  [o t-boost 40%]* [# GLM 88% ~] [^ LightGBM waits] [+]   vs Logged v  Training 1 of 3 v |
    |                               +----------------------------------------------------------------|
    |                               | o t-boost    boosting, round 212, about 2 min         [Cancel] |
    |                               | # GLM        waits, about 30 s              [Move up] [Cancel] |
    |                               | ^ LightGBM   waits, about 90 s              [Move up] [Cancel] |
    |                               | Next: rows (Sample 10%) (Full)*  depth (Quick)* (Thorough)     |
    |                               +----------------------------------------------------------------|

**State 7. The result of a what-if run.** It lands in the notice line, as a dotted line in its
model's colour, and as a row in the standings. `What changed` is one view, used for any two runs.

    | What-if: t-boost without ncd_years. Clearly worse: -2.1 (-2.9 to -1.4).                        |
    |   [Apply] [Keep as a reference model] [Drop] [What changed]                                    |
    | Factor  Compare*  Checks 3  |  o t-boost: ...            A against B: (What changed)*          |
    |  A = t-boost, what-if       B = t-boost's run            both full, quick, on the same rows    |
    |  settings     ncd_years: in -> left out                  (only what differs)                   |
    |  scores       gain -2.1 (-2.9 to -1.4), clearly worse .  balance 1.00 -> 1.00                  |
    |  fit moved    ncd_years, veh_age, region                 (each opens its factor chart)         |
    |  predictions  5% to 95% of the ratio: 0.93 to 1.09 .  22% of exposure moves by more than 5%    |

**State 8. Logging.** Settled: "A model is logged to MLflow. Ensembles are out for now." The panel
takes the settings column's place, and a `Logging` pane shows the evidence for the step in hand.
Nothing blocks: what is outstanding is counted and recorded. Once unsealed, step 4 gives the gain
with its range, the band, the difference from the last logged run and the count of looks.

    | Factor  Compare  Checks 2  | o t-boost: ...  | Logging*      | Log t-boost to MLflow       [x] |
    | Refit against reviewed . validation rows . in-sample         | 1 The run   full, thorough      |
    |   factor     largest move   mean move                        | 2 The list                      |
    |   region     2.6%           0.4%    beyond its spread        |   factors reviewed   44 of 46   |
    |   veh_age    0.8%           0.1%                             |   checks   2 to look, 3 noted   |
    |   ratio of refit to reviewed, 5% 50% 95%:  0.98  1.00  1.03  |   hand edits   2 of 2 confirmed |
    |   3% of exposure moves by more than 5%                       | 3 Refit against reviewed  1 flag|
    |                                                              | 4 Test rows   sealed, looks: 0  |
    |                                                              |   expect a gain of 3.0 to 4.6   |
    |                                                              |   [Unseal and score]            |
    |                                                              | 5 Note   drafted.  Why: [     ] |
    |                                                              | [Log to MLflow]   2 outstanding |

**State 9. The node's card.** Settled: "The node is only the connection point. It holds no
config, nothing flows on from it, and it shows what the model output is." A double-click opens the
Modelling UI. States: nothing logged, logged, changed since logged, training, error.

    +----------------------------------+     +----------------------------------+
    | Frequency               logged   |     | Frequency             training   |
    | t-boost: claims per vehicle-year |     | nothing logged yet               |
    | logged 12 Nov . gain 3.4         |     | t-boost 40%, 2 waiting           |
    | 3 settings changed since         |     | [Open in Modelling]              |
    | [Open in Modelling]              |     +----------------------------------+
    +----------------------------------+

**State 10. A narrow window, with six model types.** The chart is never under 560px wide. Under
1440px the chips drop the score, then the name. Under 1280px the settings column becomes a drawer
over the pane, with its two tabs left as a rail. Under 1100px the factor list folds to a strip,
the chosen factor becomes a list in the chart's controls, and the own panes fold into one list.

    +-----------------------------------------------------------------------+
    | < Freq  [o]* [# ~] [^] [<>] [\/ 40%] [x !] [+]  vs v   Train 2 v  Log |
    +---+-------------------------------------------------------------+-----+
    | > | Factor*  Compare  Checks 3  |  o t-boost v                  | All |
    |   | veh_age v  (Fit|Effect) Rows v  Scale v  Split v            | --- |
    |   | (the chart, at least 560px)                                 | t-b |
    +---+-------------------------------------------------------------+-----+

## Charts

Settled: "Clicking a factor charts actual against expected for every fitted model." So every fitted
model is drawn. A line can be muted to a hairline, never removed.

**Colour and shape say which model type,** fixed for every node. t-boost: circle, blue `#0072B2`.
GLM: square, orange `#d95926`. EBM: triangle, green `#199e70`. LightGBM: diamond, violet
`#9085e9`. XGBoost: down triangle, gold `#d4a017`. CatBoost: cross, magenta.

- I tested these on Haute's dark surface (`index.css:35`) for red-green colour blindness, any
  pair against any pair. The first four pass. Gold clears the pair test but is brighter than the
  checker allows. I found no safe sixth: magenta and green look alike. A small search, no proof.
- So colour is never the only cue. Every line has its shape on its markers and its name at its
  end, and the chips are the legend. Text is never drawn in a model's colour.
- Models need their own colour tokens. Today's series colours reuse the warning, success and
  danger colours (`index.css:327-334`), and actual is the success green (`index.css:286`).

**Stroke says which run.**

| Stroke | Meaning |
|---|---|
| Solid, in the model's colour | the model's own run. The chosen model's line is heavier |
| Dotted, same colour | its counterpart: a what-if run, the fit under a hand edit, training rows |
| Dashed, grey | a reference model. The picked reference is brighter |
| Dash-dot, grey | the benchmark, named "price, rescaled" or "prediction" |
| Near-white, round markers, whiskers | actual, and its range |

- A ring marks a level set by hand. Half strength marks an out-of-date run, named "from earlier
  settings". With more than four model lines, those not chosen drop to half strength.
- An estimated effect has `~` before its name, and the legend names the method. It is not dashed.
- A band is drawn for the chosen model only and names its kind: "95% range" or "spread across
  folds". A level is marked only where the chosen model's expected is outside actual's range.

**The other rules.**

- **One frame, one y axis.** Exposure has its own strip under the plot, on the same x axis, as
  today (`panels/modelling/AveTab.tsx:186`).
- **Hover.** A hairline snaps to the nearest level. One readout lists every line there: exposure
  and claims, actual with its range, each model's expected, and for claim sizes the largest
  claim's share. A click pins it. `values` opens a table that can be copied
  (`panels/modelling/ChartScaffold.tsx:140`).
- **Split by a second factor, or by period.** Small charts on one shared y axis, at most six, each
  with actual, the chosen model and the reference. By period the scale is actual over expected,
  each period centred on its own total. `Grid` shows a pair of factors as a heatmap instead.
- **Scales.** Rate, relativity, actual over expected, against the reference. Ratios use a log
  axis centred on 1. The axis moves only with the factor or the scale, never with the rows or
  the models. Every model is charted on the same bands. Thin levels can be grouped for display.
- **Training and validation rows.** A switch: Validation (the default), Training (tagged
  in-sample), Out of fold (after a thorough run). `V` flips validation and training in place
  with the axis held, so overfitting shows as movement. Test rows are never offered.
- **Fit and effect.** Fit: actual against each model's expected. Effect: each model's multiplier
  for the factor, on an axis labelled "x rate" or "x odds", with the one-way actual dotted behind.

## By default, and on demand

| Shows by default | On demand |
|---|---|
| Fit, validation rows, rate scale, every model, one reference | Effect, training rows, other scales, a split, other references |
| The range on actual, and marks where the chosen model is outside it | A band on the chosen model, the values table, a pinned readout |
| Chips with place and state. The count on `Checks` | The queue, a chip's menu, clear and acknowledged checks |
| The chosen factor's block, and one line per section | A section's settings, one open at a time |
| The notice line, when there is news. The standings and the span bar | `What changed` in full. The six views under the standings |
| The model's first own pane | History, Tests, SHAP, Tuning, All tables |

## Keys

Letter keys act when focus is not in a text field (`hooks/keyboardTargets.ts`). A control's tooltip
shows its key, and `?` lists them, as in the pipeline editor (`components/KeyboardShortcuts.tsx`).

| Key | Does | Key | Does |
|---|---|---|---|
| Up, Down | previous, next factor | `1` to `6` | choose a model, by its place in the strip |
| `/` | search the factors | `M` | flip the menu: All models, the chosen model |
| `R` | tick reviewed, go to the next to review | `H` | mute the chosen model's line |
| `X` | chosen factor in or out | `F` `C` `K` | Factor, Compare, Checks pane |
| `D` | its direction: none, up, down | `O` | the chosen model's table or term for this factor |
| `T` | Try without it (Shift: for all models) | `[` `]` | previous, next pane |
| `E` | Fit or Effect | Ctrl+Enter | Train what is out of date |
| `V` | validation or training rows | Esc | close a list, cancel an edit, clear the row filter |
| `S` | Split by | Left, Right | move the readout along the levels |
| Ctrl+Z, Ctrl+Shift+Z | undo, redo, as today (`ModellingView.tsx:187`) | Ctrl+S | save, as today |

- **`X`, `D`, `R` and `T` act on the All models menu from anywhere,** with a toast that names the
  change and offers Undo. The settings column flips to All models if it is on screen. Otherwise
  the toast is the only sign. They are off while logging is open or the node is read-only.
- Up and Down keep a pane that shows one factor (Factor, Tables, Terms). From any other pane they
  open the Factor pane, as a click on a factor does.
- In a table, arrows move the cell, Shift+arrows select a run of levels and a number starts an
  edit. Alt+Up and Alt+Down change the factor.

## Clicks and key presses, for the six most common tasks

"Before" is the analyst's count in `01-analyst-walkthrough.md` (scenario.step in brackets).

| Task | Before | Now |
|---|---|---|
| Look at the next factor and tick it reviewed | 2: Down, then a click, as the tick had no key (1.5) | 1: `R` |
| Leave a factor out, or set its direction, with a model's menu open | 3 clicks (1.5) | 1: `X` or `D`. By mouse, 2 |
| Test a model without a factor | no control; about 30 steps by hand for three states (5.8) | 2: `T`, then Apply or Drop |
| Retrain, and read what changed | 1 click, and no place to read it (problem 3) | 1: Ctrl+Enter. The notice line says it |
| Set a GLM term and refit | about 5 a term (6.5) | 2: pick the shape. It refits by itself |
| Set a level by hand | 6 steps (5.4) | 4: `O`, click the level, type the value, type why |
| Also: add three model types and train them | 7 clicks (1.6) | 5, in one list |

## Where everything lives

"04.n" is need n in the workflow draft's "Needs from the UI". "05.n" is the same in the analytics
draft, and "§" is a section of that draft.

| Thing | Place |
|---|---|
| Setup, the handling sentence, group and period in one split, moving the split at a refresh (04.1, 04.2) | All models: Target, Split. A suggestion has `Use` |
| Row sets, count of test looks, scoring power (05.10, §0.1, §2.1) | All models: Rows, Metrics |
| Named segments (04.22, §4.7) | All models: Segments. `Save as a segment` on a row filter |
| In or out, direction and its cost, fixed value, suggestions, the node that makes a column (04.12, 04.15, 04.16, §7.4) | All models: the chosen factor's block |
| Run labels, the rows a run learned from and stopped on (04.3, §1.4, §2.2) | Chip, standings row, figures line. In full in the model's menu: its run |
| Cannot honour (04.14) | A substitute is accepted at the top of the model's menu. Not honoured: a mark on the chip, a greyed standings row. Cannot train: greyed in Add |
| Yardsticks, benchmark, `Bring in a logged run` (04.4, 04.21, 05.2, 05.13, §9) | The `vs` list, which marks a reference whose results are local. Rows in the standings |
| The queue, sample or full, quick or thorough (04.5, §2.3, §8) | The Train list |
| `Try`, and what-if results (04.6, 04.7, §3.5) | Beside each setting, in a table pane, on the pruning path. Results: notice line, standings. Many at once: one ranked list in Compare |
| What changed, history, bring back (04.8, 04.9) | Compare: A against B. A retrain's is stored on its line in History |
| The readout, "what is this model missing" (04.10, §3.6) | Top of Checks. A pair opens the Grid split |
| Sort, marks, misfit bars (04.11, 05.9, §3.2, §3.3) | Factor list |
| Checks, errors, data changed (04.13, 04.16, §5) | Checks pane. Each check shows its status, its threshold, a link to its fix, and Acknowledge with a note. An error is a red notice with its two choices. Changed data is a notice |
| Hand-edit preview, what moved, cost (04.17, 05.12, §7.1 to §7.3) | Tables or Terms pane. Edits are listed in the model's menu |
| Pruning path, banding and graduation (04.18, 05.11, §6.1) | t-boost: Simplify |
| Term, partial residuals, tests, aliasing (04.19, §6.2) | The GLM menu's factor block, Terms, Tests. A test is tagged in-sample, beside the validation difference |
| EBM shapes. Learning curve, tuning, SHAP (§6.3 to §6.5) | Terms. Curve, Tuning, SHAP |
| Logging steps, refit against reviewed, test rows (04.20, 05.16, §2.4, §2.5) | Logging panel and pane |
| Score cell, tied, in-sample (05.1, 05.3, 05.4, §1) | Standings, chips, figures line |
| Fit, ranges, effect and its labels (05.5 to 05.7, §3.1, §3.4) | Factor pane |
| Row filter, heatmap, small charts by period (05.8, 05.14, 05.15) | A chip in the figures line. Factor pane: Split |
| Span bar, what the data can show (05.17, §4.1) | Compare, under the standings |
| Balance, calibration, residuals, largest claims (§4.2, §4.6) | Standings columns. Compare: Calibration. A boosted model's menu offers `Rebalance` |
| Lift, Lorenz, Gini. By segment and period (§4.3, §4.5, §4.7) | Compare: Lift, By segment |
| Double lift, where and why, how far predictions move (§4.4, §4.7, §4.8) | Compare: A against B |
| The Assistant, Git, Files | The settings column's place, as today (`ModellingView.tsx:125-145`) |

## Nothing to show, and things going wrong

| When | The screen shows | It offers |
|---|---|---|
| The columns are not known | "Columns not known yet", as today (`ModellingView.tsx:498`) | Refresh |
| No target. No model types | State 1. State 2 | The Target section. Add |
| A model has no run here | A hollow chip, "not trained here", and no line | Train |
| No factor clearly misfits. No checks to look at | The list says so. "Nothing to look at. 14 clear." | A signal to stop |
| A row filter leaves no rows | "No rows in this filter" | Clear the filter |
| A training fails, is cancelled, or is cut off by a restart | The chip says which. The old run and its line stay | The reason, and the next step |
| A factor is in but its column is gone | A red notice. Train stops | Leave it out, or open its node |
| The pipeline upstream fails | A red notice. The last runs stay viewable | A link to the node |
| A GLM will not fit | A notice naming the term | A simpler term |
| Logging fails | The panel keeps the refit and the recorded look | Retry |
| The node cannot be edited | The settings are inert, as today (`ModellingView.tsx:399`) | Nothing |

## Building blocks

Settled: "The code stays clean and modular, with as little duplication as possible."

| Part | Built from | Change |
|---|---|---|
| The frame, with side panels in the settings' place | `ModellingView.tsx` | none |
| Pane tabs, with a mark and a label per tab | `panels/PreviewPanelTabs.tsx:12` | a divider between shared and own tabs |
| Factor list, one from today's two | the view's own (`ModellingView.tsx:424`), and `panels/modelling/FeatureBrowser.tsx:40`, which draws a bar per name | sort, marks, filter, keys |
| Factors table, settings sections | `panels/modelling/CommonFeatureConfig.tsx`, and the editor's `panes` prop (`ModellingView.tsx:401-410`; 03 §1) | folding sections, the factor block |
| Training progress, learning curve, polling with a toast | `panels/modelling/TrainingProgress.tsx`, `LossTab.tsx` (03 §1), `hooks/useBackgroundJobs.ts` | a queue. One job runs at a time today (03 §3) |
| Switches, menu keys, toasts | `components/ToggleButtonGroup.tsx`, `hooks/useMenuKeyboard.ts`, `stores/useToastStore.ts` | none |
| Out-of-date mark, node card, `Open in Modelling` | `components/PreviewOutOfDateBadge.tsx`, `nodes/PipelineNode.tsx:17`, `panels/ModellingConfig.tsx:357` | the card's lines |
| Narrow window | the collapsed-column strip (`haute-ui/PaletteShell.tsx:54`), the side panel frame (`haute-ui/SidePanel.tsx`) | the drawer |
| View state and keys | `stores/useModellingViewStore.ts`, `ModellingView.tsx:187` | chosen model, menu tab, reference, row filter. The key map |
| Table editor, one for t-boost, EBM and the GLM | `TermTableView`, `termCharts` (03 §1) | selection, editing, the dotted fitted line. The GLM's table takes the same shape |
| Chart frame (new) | the primitives in `panels/modelling/ChartScaffold.tsx`. Today's chart draws one model (03 §1) | many lines, whiskers, exposure strip, hover, small charts |
| Score cell, model chip, notice line, figures line, folding section, step list (new) | nothing today | each built once |
| Run row (new) | nothing today | one row for the standings, the `vs` list, the queue and History |

A model type's module supplies its mark, its menu's sections and factor block, its own panes and
its table editor's rules. The shell never asks which model type it is.

---

## Left out, and why

- **Clustering.** Settled: "Clustering is parked for now."
- **Scheduling on the node's card.** Settled: "Scheduling may come later. The model type a schedule
  trains and logs is picked when the schedule is set up." The card has room for a line later.
- **Ensembles.** Settled as out.
- **A command palette.** The researcher found only design blogs for it (02-F4). The keys and the
  search cover the loop.
- **Partial dependence as a shared chart.** The analytics draft drops it (§3.4). It has no pane.
- **The demand curve for a yes/no model with price as a factor, and the check that price can be
  told apart.** The analytics draft says they can wait (§10). No pane is drawn for them.
- **Curves for banding and graduation.** They wait on t-boost work (03 §5.1). Simplify shows the
  pruning path, and one chosen point for each of the other two.
- **Parallel coordinates for tuning trials.** Avoided by 02-B4 and §6.4.
- **A light theme.** Haute has none today. The colours were tested on the dark surface only.
- **"The run it replaced" as a live reference.** The settled overwrite rule means that run is
  gone. What changed is worked out at the moment of replacement and stored on the History line.
- **Settings beside the chart, or in the factor list.** Both settled against.
- **A second y axis for exposure.** One axis per chart, so exposure has its own strip.
- **Dragging a point on the shared factor chart to edit it.** Proposed item 17 keeps that chart
  read-only. Edits are made in the table pane.
- **A number on every point.** The readout and the values table carry the numbers.
- **Sharing work in progress without logging.** The workflow draft's default is no (its question 7).

## Proposals and questions

**New proposals.** Each names the problem it answers.

- **U1. Three choices, one home each.** The factor list, the model strip, the pane tabs. Each
  choice is made in one place. (The menu row and the pane tabs overlapped.)
- **U2. The menus live in the settings column,** as two tabs: All models, and the chosen model
  type. Compare and Checks are panes. (The menu row carried too much.)
- **U3. Every menu opens on the chosen factor's block,** and its sections fold to one line. (The
  All models menu was long, and a factor's setting cost a menu switch: 01 problem 5.)
- **U4. Keys for the loop.** `X`, `D`, `R` and `T` act on the All models menu from anywhere, with
  a toast and Undo. (Sixty factors by mouse: 01 scenario 1.)
- **U5. A notice line** for a finished training, a what-if result, changed data and errors. (None
  of them had a place: 01 problem 3.)
- **U6. A figures line on every pane:** which rows, how many, which runs, the row filter.
  (Principle 1: the analyst should never have to ask what a chart is drawn on.)
- **U7. The `vs` list** is the reference picker, and the home of reference models, yardsticks and
  the benchmark. (Reference models had no place, and up to six model types fill the strip.)
- **U8. Train carries the queue,** with sample or full and quick or thorough. (A run's state had
  no place. One training runs at a time today: 03 §3.)
- **U9. One grammar for lines.** Colour and shape: the model type. Stroke: which run. A ring: set
  by hand. Half strength: out of date. (Six lines, reference models and edits on one chart.)
- **U10. Four tested colours, and shape and name always.** (Colour-blind readers.)
- **U11. Mute, never hide.** (Keeps the settled line true whatever the analyst clicks.)
- **U12. Compare always has a pair:** A is the chosen model, B is the reference. (Fewer clicks to
  a double lift.)
- **U13. History is a pane of each model,** and "what changed" is one view for any two runs. (01
  problem 6.)
- **U14. The table pane follows the chosen factor,** and one editor serves t-boost, EBM and the
  GLM. (Six steps to an edit: 01 scenario 5. Three editors: 03 §6.)
- **U15. Add is one list with tick boxes.** (Six clicks to add three model types: 01 scenario 1.)
- **U16. A folding order for narrow windows.** (Not designed before.)
- **U17. A Logging pane beside the Logging panel,** for the evidence of the step in hand. (The
  panel alone has no room for refit against reviewed.)
- **U18. The first factor is chosen on opening.** (Removes the "no factor selected" state.)

**Changes to the Proposed items.**

- **1, 2, 4, 5, 11, 14, 18.** No change from the screen's side.
- **3.** Change, on screen: the score cell shows the difference and the range of the difference.
- **6.** Change. The row keeps the model types, Add, the reference, Train and Log. All models
  becomes a tab of the settings column, and Compare becomes a pane. The settled menu line holds:
  one menu for the shared settings, then one for each model type.
- **7.** Keep both views. Add: the stroke grammar, the split as small charts or a grid, the held
  axis, the rows switch. The effect by year is a `Try`, as the analytics draft says.
- **8.** Keep. Add the default sort, the marks, the count of chance misfits, and the `R` key.
- **9.** Change. A what-if result has three homes: the notice line, a dotted line, a standings
  row. "What changed" is one view in Compare, and a stored summary on each History line.
- **10.** Change. Checks is a pane with its count on the tab, and the readout on top. An error is
  a red notice, not a check.
- **12.** Change. Logging is a step list in the settings column's place, with its own pane.
- **13.** Add: the tool's pick is marked in the Train list, and announced once in the notice line.
- **15, 16, 17.** Add: a ring for a level set by hand, the fitted value dotted behind it, the
  preview under the table. A fixed value entered in All models shows in the table as set, and is
  changed where it was entered.
- **19.** Add: the table pane and the model's menu both say "laid on top of its fit".

**Changes to the other drafts.**

- The analytics draft draws an estimated effect dashed. Here dashes mean a reference, so an
  estimated effect is marked `~` and named in the legend.
- The analytics draft draws the benchmark grey and dashed. Here it is grey and dash-dot, so it
  differs from a reference model.
- Today's chart draws expected dashed (`panels/modelling/AveTab.tsx:58-63`). Here expected is
  solid, in its model's colour.
- The workflow draft asks for the readout to be "always reachable". Here it is the top of Checks,
  one key away (`K`).

**Questions only the owner can answer,** each with the default assumed.

1. May the chosen factor's block (in, out, direction) stay in view while a model type's menu is
   open? Default: no. It is the first block of the All models menu, one key or one click away.
2. May one key leave a factor out for every model? Default: yes, with a toast and Undo.
3. Is a model type's colour and shape fixed across all nodes and projects? Default: yes.
4. Is muting enough, or must a line be removable from a chart? Default: mute only.
5. Does a double-click on the node open the Modelling UI, with no node panel? Default: yes.
6. What is the narrowest window to support? Default: 1024px.
7. Where does Back go? Default: a list of the pipeline's modelling nodes. Today Back goes to a
   list that holds interactive builds only (`ModellingView.tsx:91-93`, `:346`).
8. Is a light theme planned? Default: no.
9. How many model types will one node really hold? Default: up to six, with three or four usual.
   If six is common, the sixth colour needs a decision.

## Concerns about settled items

No settled line needs changing. Two are near the edge of this draft.

- **"A factor's settings sit in the menu, not beside its chart", with "The factor list on the left
  is for choosing a factor to look at, not for settings."** Keys change a factor's settings while
  the menu may be out of view. I read a key as a way to reach the menu's setting, not as a second
  home for it. Smallest change if the owner disagrees: the keys only open the All models menu on
  the factor's block, and a second press changes the setting.
- **"Clicking a factor charts actual against expected for every fitted model."** With six model
  types, colour cannot tell every pair of lines apart for a colour-blind reader. Shape, name and
  emphasis carry it. No change is needed.

## Housekeeping

- All five inputs were present and used: 01, 02, 03, 04 and 05.
- I checked every Haute path cited with a line number by opening the file. Items cited as
  "03 §n" rest on the engineer's report and I did not open them.
- The colour test used a palette checker: distance in OKLab under simulated protan and deutan
  vision, any pair against any pair. It searched about twenty candidate colours.
  - The four colours' worst pair scores 9.4 for colour-blind vision (target 8) and 17.1 for full
    vision (floor 15). Each has a contrast of 3.6 or more on `#0f1117`.
  - Gold scores 10.5 and 16.6 against them, but is lighter than the checker's band.
  - Magenta against green scores 1.6 for colour-blind vision. That is why there is no safe sixth.
  - Violet against the reference grey scores 12.6 for full vision, under the floor. The dashes
    carry that pair.
  - Not tested: the model colours against Haute's status colours and its purple accent. The rule
    "state is a mark and a word" covers that.
- I ran the test from throwaway scripts in the session scratch folder, one level above the team
  folder, with the demo project's Python. They are deleted. Two builder files I used inside the
  team folder to keep the wireframes aligned are deleted too.
- Nothing under `/home/ralph/suite/` was changed.
