# Overview

A loose brainstorm. None of this is in the specs, and nothing is settled unless it is listed
as settled below.

The five files:

- `overview.md` (this one): the idea, the decisions and the reasons for them.
- `workflow.md`: how an analyst works through a model.
- `ui.md`: what is on screen.
- `analytics.md`: what the tool can tell the analyst, and how each thing is worked out.
- `plan.md`: the order to build in, one aspect at a time.

On 20 November 2026 a team of nine agents honed `workflow.md` and `ui.md` and added
`analytics.md`. What they changed, and what they cut, is at the end of this file.

## Where things stand

**Settled** (from Ralph):

- For staged training, put a Training pane on the right instead of the Train button at
  the top. Give the different stages separate hyperparameter configuration boxes and
  separate training buttons. Planned in `staged-tboost.md`; not implemented yet.

- Features use the same row controls as models: a settings icon left of an always-visible
  inclusion checkbox. Names chart the feature; the icon opens its fitting options.

- Use the Workbench schema’s settings icon to the left of each model’s checkbox.
  That icon opens Duplicate, Rename and Remove; the name only selects the model.

- + Add opens a model-type picker. Only t-boost is available at present.

- Duplicate, Rename and Remove are stacked vertically. Remove requires confirmation.

- The main chart and right-hand pane selectors match in bar height, spacing, uppercase
  typography and font size; ~~tabs share the width of each bar equally~~ tabs stay
  grouped at the left, sized to their labels.

- Remove the node-name subtitle (modelling) on the left and the model-name heading
  (t-boost) above the right-hand tabs.

- The Models list on the left behaves like Features: names expand on click with the same
  animation and soft hover. ~~Chart visibility and model actions sit in the expanded area.~~ The chart checkbox
  stays beside the name; Duplicate, Rename and Remove stay in the expanded area.

- Readiness warnings must not push the panes down: disable Train and show the warning
  below the right-hand pane’s controls, with no banner above the charts.

- The left sidebar matches Pricing: chrome background, heading typography, collapse
  button and compact view switcher; its open state is shared between views.

- All the modelling for a node happens in the Modelling UI, for every model type, and later
  for clustering.
- The node is only the connection point. It holds no config, nothing flows on from it, and
  it shows what the model output is.
- Any model type can be trained on the node's data, and the models are compared.
- There is one menu for the settings all models share, then a menu for each model type.
- A factor's chart shows for every factor, even the ones a model did not fit.
- Several models of one model type can be trained in a node, for example two totally
  different t-boost models with different factors.
- A model's configuration can be copied.
- Each model has its own factors.
- The actual-against-expected charts show every trained model, so they can be compared, and
  each model can be toggled on and off them with a tick box after its name.
- The top toolbar has Model Setup (the settings all models share: target, objective,
  metrics) and Training Split. ~~The right-hand pane is just the chosen model's settings:
  its features and parameters.~~ The feature location is superseded by the combined list below.
- ~~For the individual model settings in the right hand pane, the features and the params
  should be split into panes as per the modelling node.~~ Superseded 20 Nov by the combined
  feature list below.
- The feature list for each model realistically needs to be combined with that on the left,
  rather than 2 large lists of features. It needs to be condensed: feature names shown, and
  then when you click the name it expands with the fitting options.
- The combined feature list should be more aesthetic and lose the '>' and downward equivalent.
- Move the backward arrow above Factor, Learning curve and Tables into the main toolbar
  as a button called Main Menu. Move the complete feature list to the right into a pane
  called Features. Put the models, including the current t-boost and +Add, in the left pane.
  This supersedes the earlier left-side location of the combined feature list and top-row
  location of model entries; the expandable feature controls remain.
- A little slide out animation as it goes down; the feature name should not be blacked out
  when hovering. This should feel like a premium bit of software.
- ~~A button in the top toolbar trains the model currently selected.~~ Superseded for the
  planned staged workflow by the right-hand Training pane and separate stage buttons above.
- The train button should turn into a cancel button whilst its running.
- A model's entry needs no words for its run's state (trained, not trained): they take space.
- The GLM needs its own workflow.
- Clicking a factor charts actual against expected for every fitted model.
- ~~The factor list on the left is for choosing a factor to look at, not for settings.~~ Superseded 20 Nov by the combined feature list below.
- A model is logged to MLflow. Ensembles are out for now.
- Scheduling may come later. The model type a schedule trains and logs is picked when the
  schedule is set up.
- Runs live locally. Training a model again overwrites its run, unless the run is kept as a
  reference model.
- Training sets vary a lot in size.
- Clustering is parked for now.
- Feature engineering stays in the pre-processing nodes.
- An analyst may edit a fitted table or relativity by hand before logging. An edit carries
  over when the model is trained again, and the rest of the model is refitted around it
  where the model type allows.
- ~~A factor's settings sit in the menu, not beside its chart.~~ Superseded 20 Nov by the combined feature list below.
- The code stays clean and modular, with as little duplication as possible.

**Open** (the default the files assume is in brackets). To answer one, say its Q number.

- **Q1.** Is a value the business fixes a shared setting, entered once in the All models
  menu? [Yes.]
- **Q2.** May kept predictions go to MLflow with a logged run? They are row-level data.
  [No. They stay local, and a logged run brought in elsewhere is compared only on rows it
  never saw.]
- **Q3.** Is there an id column on every dataset? [Setup asks for one, and says what is
  lost without it.]
- **Q4.** Should claim counts and claim sizes, modelled in separate nodes, be checked
  together as a pure premium? [Not in this design. Each node is judged alone.]
- **Q5.** Should today's price be drawn as a benchmark beside the models? [No. The last
  logged run answers "better than today?".]
- **Q6.** Do reviews and acknowledged checks travel with work in progress, in git? [No.
  They stay local until logging, then go into the model card.]

**Concerns about settled lines** (from the team). The files stay inside every settled line.
Each concern gives the problem and the smallest change that would fix it. To accept one,
say its letter.

- ~~**A. "A factor's settings sit in the menu, not beside its chart."** A factor's in or out
  and its direction are in the chosen model's tab, and its fixed value is in All models.
  With All models open the row is a click away, and across several models it is one row
  per model, a tab each. Smallest change: let the chosen factor's row be pinned at the top
  of the settings column, one line per model, with keys that act on the chosen model's.~~ Resolved 20 Nov by Ralph's combined feature list decision; fitting controls now expand on the left for the chosen model.
- **B. "Training a model again overwrites its run."** Undo brings the settings back but not
  the run, and "what changed" needs the replaced run's predictions. Smallest change: hold
  the one replaced run until the next training. Four of the team's reports asked for this.
- ~~**C. "Factors and monotonicity are shared by every model", with the GLM's own workflow.**
  A GLM is classically built forward, one factor at a time, but leaving a factor out of the
  GLM takes it from every model. Smallest change: a model type may leave a shared factor
  unused, and the factor list shows it.~~ Resolved 20 Nov by the settled line "Each model
  has its own factors.": a GLM built forward leaves a factor out of itself only.
- **D. "Runs live locally."** At a refresh on another machine the reference is gone.
  Smallest addition: `Bring in a logged run` from MLflow (proposal 50).
- **E. "...refitted around it where the model type allows."** No model type can refit
  around part of a table today. The files refit around a whole table and lay part of a
  table on top. And an edit only carries over if it is stored against a level's name or a
  range of values. Smallest change: say both in the rule.
- **F. "Feature engineering stays in the pre-processing nodes."** Grouping levels inside one
  GLM term stretches it. The reading taken: what only the GLM sees is a term, not a column.
- **G. "There is one menu for the settings all models share, then a menu for each model
  type."** The menus are now per model: two t-boost models each have their own tab, with
  their own factors. Smallest change: "then a menu for each model".
- **H. "The model type a schedule trains and logs is picked when the schedule is set up."**
  A node can now hold two t-boost models, so the model type alone does not say which one a
  schedule trains. Smallest change: "The model a schedule trains and logs".

**Proposed** (say the number to strike or change one). Items 1 to 19 are from before the
team's work and keep their numbers, reworded where the team changed them. Items 20 to 58
are the team's. Items 59 on follow Ralph's decisions of 20 November.

1. ~~One model per model type in a node. The two yardsticks are reference models, so this
   holds.~~ Struck 20 Nov: a node can hold several models of one model type (settled).
   *(Models, runs and logging)*
2. Models learn only from training rows: early stopping and tuning use rows held back from
   them. Validation rows are only looked at, and the look before fitting uses the training
   rows. Test rows stay sealed until logging. *(Honest evaluation; analytics.md, The rules)*
3. The range goes on the difference between two runs, not on each score. The thorough
   evaluation buys charts and a worklist on every row, not a narrower range. *(Honest
   evaluation; analytics.md, The rules)*
4. Each run keeps its predictions, and the shared charts are computed from them on demand.
   This rests on the id column (20). *(One shell, a module per model type)*
5. Each model type supplies its effect for a factor in one common shape, defined in 33.
   *(analytics.md, By factor)*
6. The top row holds an entry per model (with its tick box), Add, `vs` and Log; ~~Train is in
   the top toolbar (settled 20 Nov).~~ The staged workflow moves training into the right-hand
   Training pane with separate stage configuration boxes and buttons. An entry's small menu has Duplicate. The shared
   settings are the toolbar's Model Setup and Training Split, not a tab (settled 20 Nov),
   and Compare and Checks are panes. *(ui.md, The frame)*
7. The factor chart has a fit view and an effect view. The fit view also carries the chosen
   model's effect, and can be split by a second factor or by period. *(ui.md, Charts)*
8. The factor list sorts by misfit (32) and tracks which factors have been reviewed.
   *(ui.md, The factor list)*
9. Replaced by 38 and 39. Undo could not bring back an overwritten run.
10. A short list of checks, each with a rule and a status, that never block. An error is
    not a check (41). *(analytics.md, Checks)*
11. A workflow per model type. t-boost does every step in one training, and only pruning
    has a path. The GLM trains with no setup and borrows structure from the other models.
    *(workflow.md, Workflows by model type)*
12. Logging scores the test rows at that moment and records a checklist and a note. Now
    spelled out in 47 to 49. *(workflow.md, Before logging)*
13. Defaults follow the time a training takes, and are stated before the first training.
    Thorough stays the default on small data. *(Small and large data)*
14. Hand edits apply to t-boost, EBM and a GLM term with levels. Only main tables are
    edited, not interaction tables. *(Hand edits)*
15. An edit sits beside the fitted value, never over it. It records the fitted value, the
    new value, when and why, and is stored against a level's name or a range of values. It
    can be undone, and the model card lists it. *(Hand edits)*
16. The hand-edit preview leads with the rows the edit touches and states the cost (44).
    "Which other tables moved" applies after a refit around a whole table. *(Hand edits)*
17. Edits are made in the model's own table pane and listed in its menu. The shared factor
    chart stays read-only. *(ui.md, The other states)*
18. A whole table is refitted around, through the offset, once Haute carries a second
    offset. Holding part of a table fixed is work in the libraries. *(Hand edits)*
19. Part of a table is laid on top of the fit, and the pane says so, with `Rescale the
    base` offered. *(Hand edits)*

*The foundations*

20. An id column, asked for at setup and checked unique. The split is made once per node
    against it. *(analytics.md, The rules; workflow.md, The journey)*
21. One split by policy and by time: test rows are the latest mature period, and validation
    rows are whole policies. Setup takes a "claims are mature up to" date. *(analytics.md,
    The rules)*
22. Setup states the handling. The analyst names the kind of target, and the tool states
    the handling in a sentence. *(workflow.md, The journey)*
23. Rows held back from the training rows for early stopping and tuning, then a fit on all
    the training rows, so every model type trains on the same rows. *(analytics.md, The
    rules)*
24. The look before fitting uses the training rows, so it spends no validation rows.
    *(analytics.md, The rules)*

*Scores and comparisons*

25. Gain is the one unit: how far a model beats the floor, as a percent of the floor's
    deviance, wherever a score is shown or recorded. *(analytics.md, The rules)*
26. The range goes on the difference between two runs, paired by row and resampled by whole
    policies. One rule says when a difference is clear. *(analytics.md, The rules)*
27. One rule for many tests. Every list controls its share of false flags and calls clear
    only what survives. *(analytics.md, The rules)*
28. Two marks for runs that are not alike: "other rows" and "ignores". A run that ignores a
    setting it was given, shared or its own, is still scored and compared. *(analytics.md,
    The rules)*
29. The last logged run is compared as its reviewed fit. The logged refit is compared only
    on rows it never saw. *(analytics.md, The rules)*
30. The thorough evaluation buys charts and a worklist on every row, and a spread on each
    effect, but no narrower range. *(analytics.md, The rules)*
31. Two yardsticks: the floor, which is one rate, and Default boost, a reference model that
    tuning never touches and that is retrained only when asked. *(workflow.md, The journey)*

*Factor by factor*

32. Misfit is the gain a factor still offers, from corrections learned on other rows. It
    sorts the factor list, left-out factors included. *(analytics.md, By factor)*
33. One common effect: the link scale, one centring, averaged over a partner's levels, exact
    or labelled as estimated. Partial dependence leaves the shared charts. *(analytics.md, By
    factor)*
34. The chosen model's effect is a thin third line on the fit view, as three pricing tools
    draw it. *(analytics.md, By factor; ui.md, Charts)*
35. A suggestion for the factor in hand, with its evidence, for every model type, and the
    pattern named in words. *(workflow.md, The loop)*
36. Named segments, kept in the config and scored on every run. A score under a filter made
    by hand is labelled "after looking". *(analytics.md, By model)*
37. Where and why two runs disagree: the factors that set the disagreeing rows apart, and
    for table models an exact split of the difference by table. *(analytics.md, By model)*

*The loop*

38. Try, then Apply. A change runs as a what-if beside the model's run. Apply makes it the
    model's run, with no training. *(workflow.md, The loop, What-if runs)*
39. A history line per training, with what changed and a way to bring the settings back. A
    run is overwritten only when the new training finishes. *(workflow.md, The loop)*
40. Five stopping questions for the chosen model, and a rule for when to stop.
    *(workflow.md, Knowing when to stop)*
41. Errors are not checks. A missing column stops Train. Each check has a rule and a
    status. *(analytics.md, Checks)*
42. A new column arrives left out, with `Try with`. Thin levels have two homes.
    *(workflow.md, The round trip to pre-processing)*

*By hand*

43. Fixed values: the business's rule, entered once for all models. A whole factor goes in
    as an offset. *(workflow.md, Hand edits and fixed values)*
44. The hand-edit preview leads with the rows touched and states the cost. Only main tables
    are edited. *(analytics.md, Hand edits and directions)*

*Per model type*

45. The GLM's default terms hold the directions set in its own factors, and a term change
    refits at once only when nothing else is training. *(workflow.md, Workflows by model type)*
46. t-boost's pruning choice is recorded as a rule, picked on out-of-bag evidence.
    Graduation gets one number on the validation rows. *(analytics.md, Per model type)*

*Logging and after*

47. Logging says what is logged: a refit, shown against the reviewed fit before the test
    rows are scored. *(workflow.md, Before logging; analytics.md, At logging)*
48. An expected range for the test score, stated first and holding both row sets' noise,
    with a rule for a score below it. *(analytics.md, At logging)*
49. The record of looks at the test rows belongs to the node's rows, so a changed split
    cannot reset it. *(analytics.md, At logging)*
50. A refresh workflow: where things stand, the split moved forward, `Bring in a logged
    run`, the old model judged first. *(workflow.md, Refreshing a logged model)*
51. Handover: the config carries settings, edits and segments. Reviews and acknowledged
    checks stay local until logging. *(workflow.md, Handover, and things going wrong)*

*The screen*

52. Three choices, one home each: the factor in the list, the model in the top row, the
    pane in the pane tabs. ~~The two menus are tabs of the settings column.~~ Overtaken 20
    Nov: the shared menu opens from the toolbar (Model Setup, Training Split) in the
    settings column's place. *(ui.md, Rules for the screen)*
53. A notice line for news, errors and "where things stand", and a rows line on every pane.
    *(ui.md, The frame)*
54. The `vs` list picks the run every difference is taken from. Compare always has a pair.
    *(ui.md, The frame)*
55. One grammar for lines: colour and shape for the model type, stroke for the run, each
    with one meaning. Two models of one type are told apart by name. A line is muted, never
    hidden. *(ui.md, Charts)*
56. A small set of keys, none of which changes a setting. *(ui.md, Keys)*
57. One chart frame, one table editor, one run row, one score cell. The model card gains
    tables and text, not charts. *(ui.md, Building blocks)*
58. A shared order to build in. Now `plan.md`, which builds one aspect at a time.

*Several models*

59. A new model's factors and directions start as the chosen model's, or on a new node as
    every column with no role, and no directions. *(Models, runs and logging)*
60. Duplicate copies a model's settings: its factors, directions and own settings. It
    leaves the run and the hand edits behind, and the copy needs its own name. *(Models,
    runs and logging; ui.md, The frame)*
61. Default boost takes its factors like any new model, and keeps them until changed in its
    tab. *(Models, runs and logging)*

## The idea

The pricing pipeline holds the **pre-processing** and the **modelling nodes**. A modelling
node sits where its data arrives, and all the modelling for it happens in the **Modelling
UI**. That covers every model type: t-boost, EBM, LightGBM, XGBoost, CatBoost and the GLM,
and later **clustering** too.

In the Modelling UI you can train any model type on the node's dataset and compare the
results. Everything needed to make the modelling decisions is there. The decision ends in
**logging** the model to MLflow.

Model types have their own components, but the UI stays clean and modular, held to a high
standard, with as little duplication as possible.

## What building the best models needs

Every proposal in these files is judged against this list.

1. **Honest numbers.** A chart or score that includes rows a model learned from flatters the
   most overfitted model. Everything the analyst looks at uses rows no model learned from.
2. **The factor is the unit of work.** Analysts improve a model factor by factor, so every
   factor is one click from its evidence, for every model.
3. **Comparison by default.** No result is shown alone. It sits beside the other models and
   the reference models.
4. **Fast loops.** The more ideas an analyst can test in a day, the better the model.
   Changing something, retraining and reading the difference should take seconds to minutes.
5. **Signal, not noise.** A difference smaller than its uncertainty is shown as no clear
   difference, so nobody tunes to noise.
6. **Nothing lost by accident.** A run worth returning to is kept as a reference model, and
   a change to the settings can be undone, so trying an idea is never a risk.
7. **Guidance, not automation.** The tool shows where a model is weakest and what might fix
   it. The analyst decides.
8. **Judgement with evidence.** An analyst can override the fit by hand. The tool shows
   what the override costs and keeps a record of it.

## Words used in these files

- **Floor:** the model with no factors: one rate.
- **Model:** a named model of one model type, with its own factors, directions and
  settings. A node can hold several of one model type.
- **Gain:** how far a model beats the floor, as a percent of the floor's deviance. It is the
  one unit for a score.
- **Range, spread, band:** a range is a 95% range. It sits on the difference between two
  runs, on actual at each level of a factor, and on the test score expected at logging. A
  spread is the lowest and highest across the five folds, and is never a threshold. A band
  is a slice of a number factor.
- **Clear:** a difference whose range excludes zero and that survives the rule for many
  tests. Anything else reads "no clear difference".
- **Reviewed fit and refit:** the reviewed fit learned from the training rows only, and
  every chart and score uses it. The refit learned from the training and validation rows
  together, and is what gets logged.
- **The `vs` run:** the run every difference is taken from.
- **Yardsticks:** the floor, and Default boost, a gradient-boosting model on defaults kept
  as a reference model.
- **Misfit:** the gain a factor still offers a model. It sorts the factor list.
- **Strength on its own:** what a column predicts by itself, before any model.
- **Fit and effect:** fit is actual against a model's expected. Effect is what the model
  does with the factor.
- **What-if run:** a trial of one change, beside the model's run. `Try` starts one, and
  `Apply` makes it the model's run.
- **Fixed value and hand edit:** a fixed value is the business's rule, entered once for all
  models. A hand edit is the analyst's judgement on one model's table.
- **Marks on a run:** "other rows" (it learned from rows the other run is judged on),
  "ignores" (it could not honour a setting it was given), and in-sample (worked out on rows a
  model learned from).
- **Named segment:** a slice of the rows, named in advance and scored on every run.

## What Haute already has

Listed so that nothing below is mistaken for new:

- an adapter and a capability list per model type: its losses, tasks, monotonicity, tuning
  and GPU
- splits that are random, by group or by time (never two at once), with a holdout or
  cross-validation, and an optional test set
- a final refit on the training and validation rows together, and test scores on every run
  that has test rows
- parameter tuning
- diagnostics per run: AvE, lift, Lorenz, residuals, PDP and SHAP, plus each model type's
  own (t-boost tables, EBM terms, GLM coefficients and relativities)
- a model card, and a feature contract tying a trained model to the columns it scores
- logging a run to MLflow, and a Scoring node that scores a logged run or a registered model
- a memory estimate before training, and a row limit

What is new here: several models in one node, of one model type or more, the shared
settings, comparison across models, reference models, and working factor by factor.

What the design has to change in Haute, checked in the code:

- **One model type per node.** Today a node has one model type and one editor, and the docs
  say to add another node for another kind of model.
- **Which rows the charts use.** Today a run's charts are drawn on the test rows when there
  are any, and otherwise on the rows the model was fitted on
  (`src/haute/modelling/_training_job.py:2009-2015`). They are never on untouched validation
  rows by default.
- **Predictions are not written anywhere I could find,** and an id column is optional
  (`src/haute/_node_config_models.py:871`).
- **Nothing from a run is stored in the project.** Results sit in the server's memory, so a
  restart loses them (`src/haute/routes/_job_store.py:1-4`).
- **One training runs at a time** (`src/haute/routes/_training_lifecycle.py:1338-1341`), so
  Train is a queue.

## The pipeline and the Modelling UI

- **All config lives in the Modelling UI**, edited in one place: the settings shared by all
  models and each model's own settings (see Settings below).
- **The node is just the connection point.** Data goes in, and nothing flows on from it: a
  modelling node is the end of its branch. Its output is the trained model itself (rating
  tables, a scored model or a clustering), and the node shows what was last logged: the
  model type, its headline metrics, when it was trained, and its status. It links to the
  Modelling UI and has nothing to configure.
- **A logged model is used by a Scoring node.** That node already scores a model from an
  MLflow run or a registered model, which is why nothing needs to flow out of a modelling
  node.
- **The config is still a project file.** The pipeline already declares the node as
  `@pipeline.modelling(config="config/model_training/modelling.json")`, with its settings in
  that JSON file. The Modelling UI becomes the only editor of that file. It stays in git,
  reviewable, and runnable without the GUI.
- Today only an *interactive build* is edited in the view, and a node set to a model type
  keeps its own panel. In this vision every modelling node is edited in the view.

## Models, runs and logging

- **Model:** a named model of one model type. A node can hold several of one model type,
  for example two t-boost models with different factors. A model holds its factors and
  directions, its own settings and its run. Add makes a new one, and Duplicate copies
  another model's settings (proposals 59 and 60).
- **Run:** one training of a model. It records the settings used, the results and the
  predictions. Training the model again overwrites its run, once the new training has
  finished. A training that fails or is cancelled leaves the old run in place.
- **Reference model:** a run the analyst chose to keep. It is not overwritten. It stays on
  the charts and in the comparison under its own name, and its settings can be brought
  back. It keeps an earlier run of one model. Two variants worth carrying on with are two
  models, each with its own run.
- **What-if run:** a trial of one change, trained beside the model's run. It ends when the
  analyst applies it, keeps it as a reference model or drops it.
- **Yardsticks:** the floor, which needs no training, and Default boost, which is kept as a
  reference model. Its factors are set as proposal 61 says.
- **History:** each training adds a line to the model's history: what changed, the scores,
  and a way to bring the settings back. The history holds no runs.
- **Log:** the end of the decision. One run is sent to MLflow, and the node shows what was
  last logged.

Runs and reference models live locally. Only a logged run reaches MLflow.

How this meets what Haute does today: a run logged to MLflow is called a *candidate*, and
registering a model or promoting a champion happens outside Haute. This brainstorm keeps
that, so "log" here means logging a candidate run.

**The config file.** Today it is flat, with one `algorithm`. It would hold the shared
settings once, the fixed values, the named segments, each reference model's name and
settings, and a block per model. Each block holds its model type, its name, its factors and
directions, its own settings and its hand edits. On a schedule, the model type to train and
log is picked when the schedule is set up, so the config needs no mark for it (concern H).

## Settings: all models, then each model

Two levels of menu. On 20 November Ralph moved the factors and directions out of All models
and into each model's own menu (concern G is on the settled line about menus).

**All models: the overall menu.** These are the settings every model shares, so that the
models stay comparable:

- the target and what kind it is, and the exposure or weight
- the offset
- the loss (distribution)
- the id column, and the date up to which claims are mature
- the split: validation and test rows
- the metrics
- named segments
- fixed values: the values the business fixes (question Q1)

Changing any of these marks every model's run as out of date.

**Each model: its own menu.** These are that model's settings, laid out by its model type:

- **Its name and model type.**
- **Its factors:** which columns are in, and each factor's direction (monotonicity), now
  edited in the expanded row of the left feature list. Changing them marks only this model's run as out of date.
- **Parameters and tuning,** and the GPU where the model type has one.
- **Constraints only that model type has,** such as EBM interactions.
- **Its own steps,** where it has them.
- **Its run and its hand edits.**

A model type that cannot honour a setting, shared or in its own menu, is never silently let
off. It offers a substitute the analyst accepts, or its run carries an "ignores" mark and is
still scored and compared. Today:

- every model type but the GLM takes a direction. The GLM takes a direction on four of its
  eight term types, but nothing links a term to the direction in the factors table
  (`src/haute/modelling/_glm_terms.py:50-61`)
- EBM cannot put a factor that has a direction into an interaction
  (`src/haute/modelling/_descriptors.py:596-603`)
- CatBoost has no Gamma loss, and EBM and t-boost have no MAE
- the GLM has a family and a link in place of a loss, which the shared loss would map onto
- the GLM does regression only

## Honest evaluation

The rule: **anything a model learns from comes out of the training rows.** That covers
fitting, early stopping and tuning. The mechanics are in `analytics.md`, under "The rules".

- **Training rows:** every model fits on these. Early stopping and tuning use rows held
  back from them, and the model is then fitted again on all of them.
- **Validation rows:** only looked at. Every score, difference and chart uses these,
  predicted by the fit that learned from the training rows only: the reviewed fit.
- **Test rows:** sealed until logging. No chart, score, check or band uses them, and each
  look is recorded against the rows.
- **Before any model is fitted** the charts use the training rows, so looking at the data
  spends no validation rows.

Why it matters here:

- Today CatBoost, LightGBM and XGBoost stop early on the validation rows, and tuning
  compares its trials on them. Those scores have already seen the rows they are judged on,
  while a GLM's have not, so a comparison across model types is tilted.
- An interactive build has no validation rows today, so its charts are drawn on the rows it
  was fitted on. A node would hold rows back by default.
- The analyst's own choices also fit the rows they look at, over many rounds. The sealed
  test rows are the check on that.

Two depths:

- **Quick:** the validation rows alone. The default while iterating on large data.
- **Thorough:** five folds by whole policies, with the validation rows as the first fold.
  Every row then has a prediction from a fit that never saw it, so the charts by level and
  the worklist use every row, and each effect gets a spread across the folds. It does not
  buy a narrower range, and it is not a second untouched score: the analyst chose on those
  rows.

**The range belongs on the difference between two runs.** One score's range is mostly noise
that no model can remove, so two such ranges overlap even when one model wins on nearly
every policy. The difference is taken row by row and whole policies are resampled. One rule
says when a difference is clear, and it allows for many things being tested at once.

**What gets logged.** The logged model is the final refit on the training and validation
rows together, as Haute refits today, with the settings of the run chosen and its hand
edits applied. So the model logged is not quite the fit that was reviewed. Logging shows how
far the refit moved from the reviewed fit, states an expected range for the test score, and
only then scores the test rows. The fold models of a thorough run are never logged.

## Small and large data

Training sets vary a lot in size, so nothing here may assume one size.

- **Small data: noise is the risk.** The thorough evaluation is the default, because the
  charts by level and the worklist then use every row. Most differences between runs will
  read "no clear difference", so the work is done from the factor charts.
- **Large data: time is the risk.** Sample runs, training only what is out of date, and
  charts from kept predictions matter most. The thorough evaluation waits until before
  logging.
- **Defaults follow the time a training takes,** not the row count alone. The tool says
  which it picked before the first training starts.

## Hand edits

Settled:

- An analyst may edit a fitted table or relativity by hand before logging.
- An edit carries over when the model is trained again.
- The rest of the model is refitted around an edit, where the model type allows it.

The rest of this section is proposed. When to edit is in `workflow.md`, how the cost is
worked out is in `analytics.md`, and where it happens on screen is in `ui.md`.

**Two things are typed by hand,** and the screen names them apart.

- **A fixed value** is the business's rule. It is entered once in the All models menu,
  against a base level the analyst names. A whole factor fixed this way goes in as an
  offset, so every model type honours it and refits around it. A single level is carried
  only by the model types whose model is tables, and the rest show "ignores".
- **A hand edit** is the analyst's judgement on one model: t-boost, EBM, or a GLM term with
  levels. It is made in that model's table pane. The gradient-boosting types have nothing
  to edit by hand.

**Two routes for a hand edit,** and the tool says which applies.

- **A whole table is refitted around.** The table goes in through the offset, the factor
  leaves the fit, and the rest is fitted around it. All six model types take an offset.
  Haute carries one offset per model today, usually the exposure, so this needs a second.
- **Part of a table is laid on top of the fit.** As far as the team's code survey found, no
  library can hold part of a table fixed today. The edit applies at once, with no training.
  Nothing else moves and the total goes off by the edit, so the tool offers `Rescale the
  base`.

**How an edit carries over.** It lives in that model's block of the config, stored against
a level's name or a range of values, never a position in a table. It is applied again in
every training: a retrain, each fold of a thorough run, the final refit and a scheduled run.

**The rest of the rules.**

- **An edit sits beside the fitted value, never over it.** Each edit records the value the
  fit had when it was made, the new value, when and why. The tool drafts what the edit did
  and the analyst writes why. Each edit can be undone, and the model card lists them all.
- **The preview leads with the rows the edit touches,** since the overall score barely
  moves for a local edit. It states the cost there and overall. "No clear difference" is
  the good answer.
- **Only main tables are edited,** not interaction tables, for now. A GLM curve has no
  level to edit, so the whole factor is fixed instead.
- **An edit changes values, not the data.** Merging levels or re-banding a factor is
  feature engineering, which stays in the pre-processing nodes.
- **Kinds of edit:** set one value; set a run of levels to a straight line or a smoothed
  curve; make a run of levels only rise or only fall; cap or floor a table.
- **Fixed values first, hand edits last.** Every later fit has to work around the edits
  already made.
- **Edits also fit the rows the analyst looks at,** like every other choice. A gain from an
  edit is labelled "on rows you have seen", and the sealed test rows are the check.

There is precedent for editing by hand with live scores and an edit history: GAM Changer,
a research tool for EBM-style models.

## One shell, a module per model type

The shell is shared by every model type:

- the factor list on the left
- the top row, with an entry per model
- the shared panes in the middle: Factor, Compare and Checks
- the settings column on the right for parameters and toolbar setup; feature fitting controls expand in the left list
- the notice line, the queue and the standings

Each model type's module adds what is specific to it:

- its capabilities: what it supports of the shared settings, and what it has of its own
- its symbol: a colour and a shape
- its settings menu and its own steps
- training: settings and data in, a model and its predictions out
- its own panes
- hand edits, where its model can be edited: the rules of its table editor
- **its effect for a factor,** in one common shape. This is exact for t-boost, the GLM and
  EBM, and estimated for the gradient-boosting types. One chart can then overlay how each
  model treats a factor.

The shell reads the capabilities to show or hide shared controls, so no `if` statements
about a particular model type spread through the shell.

**Runs keep their predictions.** Each run stores one prediction per row, against the id
column. The shared charts are computed from those on demand, together with the node's
data. That buys a lot:

- any chart can be split by a second factor or by period, with no retraining
- any two runs can be compared row by row: a double lift, or where they disagree
- the shared charts need no code per model type

Today the diagnostics are computed once, during training, for the factors in the model, and
as far as I can see predictions are not kept. The cost of keeping them is one number per row
on disk, for each model's run and each reference model.

Charts for left-out factors do not wait for kept predictions. On 20 November Ralph chose to
work them out at training time: each run also bins actual against its prediction for every
column it did not use (`analytics.md`, By factor).

There are three kinds of diagnostics:

- **From kept predictions,** with no model needed: AvE, misfit, lift, double lift,
  calibration, residuals, and where two runs disagree. They work for any model type, so
  comparison is free.
- **By scoring the model again:** the estimated effect for the gradient-boosting types.
- **From inside the model,** in its module: t-boost tables, GLM coefficients and
  relativities, EBM terms, tree importances and SHAP.

The rule of thumb: a component that needs to know the model type belongs to that model
type's module. Everything else belongs to the shell.

## Scheduling (later)

- When a schedule is set up, the analyst picks the model type it trains and logs. With
  several models of that type, which one is concern H.
- It retrains on that schedule, for example monthly as new data lands, with the node's
  saved settings, and logs the run to MLflow as a new candidate. An error stops it.
- The node shows the schedule, the last scheduled run and how it compared with the one
  before.

## Clustering (parked)

Parked for now. The notes so far:

Clustering has no target, so the shell has to allow a *task* without one. Its panes differ:

- the number of clusters (elbow, silhouette)
- cluster profiles
- a map for territories

Its output is a grouping, for example of vehicles or territories. Nothing flows on from a
modelling node, so the grouping would be logged like any model. A Scoring node could then
assign the group, as it assigns a prediction today, but that is not built. What it would
group is undecided.

## The team's honing, 20 November 2026

Nine agents worked in four phases. A pricing analyst walked through the design, a
researcher looked at other tools and the literature, and an engineer checked Haute's code.
Three designers then drafted the workflow, the analytics and the screen. A first-time
analyst and a red team attacked the drafts, and an editor produced the final files. Their
working reports are in `team/`, with an index in `team/README.md`.

**What changed, and why**

1. A fourth file, `analytics.md`, owns every measure, range, check and default number,
   defined once.
2. Scores are gains over the floor, and the range sits on the difference between two runs.
3. One rule says when a difference is clear. Many tests at once used to flag noise.
4. Foundations come first: an id column, a split made once, kept predictions, and rows held
   back for early stopping.
5. The top row is now a row of model entries. The two menus are tabs of the settings
   column.
6. Every state has a drawn place, from a new node to the node's card.
7. Try and Apply replace "what changed on retrain": an idea is backed out without losing a
   run.
8. Values typed by hand split in two: a fixed value for all models, and a hand edit for
   one.
9. A whole table refits through the offset. Part of a table is laid on top of the fit.
10. Logging says that a refit is logged, shows how far it moved, and states an expected
    range first.
11. A stopping rule, a refresh workflow, a handover and a list of failures were added.

**Considered and cut**

- A vote across the folds, and a narrower range from the thorough evaluation. The first
  passes by chance too often, and the second needs a correction the team had not read in
  full.
- An outside benchmark such as today's price (question Q5), and columns for "off level"
  and "ranks risks". Balance, the calibration curve and the last logged run already say it.
- "What is this model missing", and a search over all pairs of factors. Each costs a
  training. The pairs t-boost and EBM kept answer the same question.
- A pinned block for the chosen factor, and keys for in, out and direction: at the edge of
  a settled line (concern A).
- Half the checks, because long lists get ignored.
- Unasked retraining of Default boost after every shared change.
- Two worked examples with timings. The timings were assumed, not measured.
- A command palette, isotonic recalibration, a range on Gini, and the demand curve for a
  conversion model.

**What has not been tested**

- The first-time analyst tried the drafts, not the final files. On the drafts, seven tasks
  scored between 2 and 4 out of 5 for how intuitive they were. The hand-edit cost and
  coming back after a month scored lowest, and the editor reworked both.
- No timing in these files has been measured.
- Some papers were read in full and some only as search summaries. `analytics.md` lists
  which under "Sources".
