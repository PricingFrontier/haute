# Overview

A loose brainstorm. None of this is in the specs, and nothing is settled unless it is listed
as settled below.

The three files:

- `overview.md` (this one): the idea, the decisions and the reasons for them.
- `workflow.md`: how an analyst works through a model.
- `ui.md`: what is on screen.

## Where things stand

**Settled** (from Ralph):

- All the modelling for a node happens in the Modelling UI, for every model type, and later
  for clustering.
- The node is only the connection point. It holds no config, nothing flows on from it, and
  it shows what the model output is.
- Any model type can be trained on the node's data, and the models are compared.
- There is one menu for the settings all models share, then a menu for each model type.
- Factors and monotonicity are shared by every model.
- The GLM needs its own workflow.
- Clicking a factor charts actual against expected for every fitted model.
- The factor list on the left is for choosing a factor to look at, not for settings.
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
- A factor's settings sit in the menu, not beside its chart.
- The code stays clean and modular, with as little duplication as possible.

**Proposed** (Claude's suggestions: say the number to strike or change one):

1. One model per model type in a node. *(Models, runs and logging)*
2. Models learn only from training rows. Validation rows are only looked at. Test rows stay
   sealed until logging. *(Honest evaluation)*
3. A quick and a thorough depth of evaluation, with a range on every score. *(Honest
   evaluation)*
4. Each run keeps its predictions, and the shared charts are computed from them on demand.
   *(One shell, a module per model type)*
5. Each model type supplies its effect for a factor in one common shape, so one chart can
   overlay them. *(One shell, a module per model type)*
6. A menu row across the top: All models, one entry per model type, Compare, Train, Log.
   *(ui.md)*
7. The factor chart has a fit view and an effect view, and can be split by year or by a
   second factor. *(ui.md)*
8. The factor list can sort by where a model fits worst, and tracks which factors have been
   reviewed. *(ui.md)*
9. A retrain shows what changed from the run it replaced, and a what-if run tests a change
   without committing to it. *(workflow.md)*
10. A list of checks that point at likely problems and never block. *(workflow.md)*
11. A workflow per model type, including a GLM that trains with no setup and borrows
    structure from the other models. *(workflow.md)*
12. Logging scores the test rows at that moment and records a checklist and a note with the
    run. *(workflow.md, ui.md)*
13. Defaults follow the size of the data: thorough evaluation on small data, sample runs on
    large data. *(Small and large data)*
14. Hand edits apply to the model types whose model is tables or relativities: t-boost, the
    GLM and EBM. *(Hand edits)*
15. An edit sits beside the fitted value, never over it. It records the fitted value, the
    new value, when and why. It can be undone, and the model card lists it. *(Hand edits)*
16. An edit shows at once as a preview laid on the current fit. After training, the fit
    around it is set against the run it replaced, with the other tables that moved. *(Hand
    edits)*
17. Edits are made in the model's own table pane and listed in its menu. The shared factor
    chart stays read-only. *(ui.md)*
18. A hand edit is a constraint on the fit, like monotonicity but for one model. It lives
    in the config, puts the model out of date, and is held fixed in every training. *(Hand
    edits)*
19. Until a model type can refit around an edit, its edits are laid on top of the fit, and
    its menu says so. *(Hand edits)*

**Open:** nothing at the moment.

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
3. **Comparison by default.** No result is shown alone. It sits beside the other models, the
   reference models and the run it replaced.
4. **Fast loops.** The more ideas an analyst can test in a day, the better the model.
   Changing something, retraining and reading the difference should take seconds to minutes.
5. **Signal, not noise.** A difference smaller than its uncertainty is shown as no
   difference, so nobody tunes to noise.
6. **Nothing lost by accident.** A run worth returning to is kept as a reference model, and
   a change to the settings can be undone, so trying an idea is never a risk.
7. **Guidance, not automation.** The tool shows where a model is weakest and what might fix
   it. The analyst decides.
8. **Judgement with evidence.** An analyst can override the fit by hand. The tool shows
   what the override costs and keeps a record of it.

## What Haute already has

Listed so that nothing below is mistaken for new:

- an adapter and a capability list per model type: its losses, tasks, monotonicity, tuning
  and GPU
- splits that are random, by group or by time, with a holdout or cross-validation, and an
  optional test set
- a final refit on the training and validation rows together, and test scores on every run
  that has test rows
- parameter tuning
- diagnostics per run: AvE, lift, double lift, Lorenz, residuals, PDP and SHAP, plus each
  model type's own (t-boost tables, EBM terms, GLM coefficients and relativities)
- a model card, and a feature contract tying a trained model to the columns it scores
- logging a run to MLflow, and a Scoring node that scores a logged run or a registered model
- a memory estimate before training, and a row limit

What is new here: several model types in one node, the shared settings, comparison across
models, reference models, and working factor by factor.

Two reversals to note:

- Today a node has one model type and one editor, and the docs say to add another node for
  another kind of model. This idea puts several model types in one node.
- Today a run with test rows scores them every time. Proposal 2 seals them until logging.

## The pipeline and the Modelling UI

- **All config lives in the Modelling UI**, edited in one place: the settings shared by all
  models and each model type's own settings (see Settings below).
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

- **Model:** one per model type in a node. It holds that type's settings and its runs.
- **Run:** one training of a model, or one later step applied to a trained model (such as
  t-boost's pruning). It records the settings used, the results and the predictions.
  Training the model again overwrites its run.
- **Reference model:** a run the analyst chose to keep. It is not overwritten. It stays on
  the charts and in the comparison under its own name, and its settings can be brought
  back. This is also how two variants of one model type are compared.
- **Log:** the end of the decision. One run is sent to MLflow, and the node shows what was
  last logged.

Runs and reference models live locally, in the project's cache. Only a logged run reaches
MLflow.

How this meets what Haute does today: a run logged to MLflow is called a *candidate*, and
registering a model or promoting a champion happens outside Haute. This brainstorm keeps
that, so "log" here means logging a candidate run.

**The config file.** Today it is flat, with one `algorithm`. It would hold the shared
settings once, then a block per model type, with that model's hand edits. On a schedule, the model type to train and log
is picked when the schedule is set up, so the config needs no mark for it.

## Settings: all models, then each model type

Two levels of menu.

**All models: the overall menu.** These are the settings every model shares, so that the
models stay comparable:

- the target, and the exposure or weight
- the offset
- the task and the loss (distribution)
- the split: validation and test rows
- the metrics
- **the factors:** which columns are in, shared by every model
- **monotonicity:** each factor's direction, shared by every model

Changing any of these marks every model's run as out of date.

**Each model type: its own menu.** These are that model type's settings:

- **Parameters and tuning,** and the GPU where the model type has one.
- **Constraints only that model type has,** such as EBM interactions.
- **Its own steps,** such as t-boost's Boost, then Prune, Band and Graduate later.

A shared setting a model type cannot honour is flagged in that model's menu rather than
silently dropped. This will come up in practice. Today:

- every model type but the GLM takes monotonicity, so monotonicity in the GLM is new work
- CatBoost has no Gamma loss, and EBM and t-boost have no MAE
- the GLM has a family and a link in place of a loss, which the shared loss would map onto
- the GLM does regression only

## Honest evaluation

The rule: **anything a model learns from comes out of the training rows.** That covers
fitting, early stopping and tuning.

- **Training rows:** every model fits on these. A model that stops early or tunes holds
  some of them back for that, internally.
- **Validation rows:** only looked at. Every chart and the comparison use these, so no model
  type has a head start on them.
- **Test rows:** sealed. They are scored when a run is logged, and each look is recorded.

Why it matters here:

- Today CatBoost, LightGBM and XGBoost stop early on the validation rows, and tuning
  compares its trials on them. Those scores have already seen the rows they are judged on,
  while a GLM's have not, so a comparison across model types is tilted.
- An interactive build has no validation rows today, so its charts are drawn on training
  rows. A node would hold rows back by default.
- The analyst's own choices also fit the rows they look at, over many rounds. The sealed
  test rows are the check on that.

Two depths:

- **Quick:** one holdout. The default while iterating.
- **Thorough:** cross-validation, where each row is predicted by a model that never saw it.
  It costs several times the training time. Every row then has an honest prediction, so a
  chart of a thin level is far less noisy, and the spread across folds gives the
  uncertainty. Worth running before logging.

**A range on every score.** It comes from the folds, or from resampling the validation rows.
A comparison of two runs says whether the gap is larger than the range.

**What gets logged.** The logged model is the final refit on the training and validation
rows together, as Haute refits today, with the settings of the run chosen and its hand
edits held fixed. The test rows
are scored on that refit, at logging. The fold models of a thorough run exist only to
evaluate and are never logged.

## Small and large data

Training sets vary a lot in size, so nothing here may assume one size.

- **Small data: noise is the risk.** The ranges on scores and on each level matter most.
  The thorough evaluation is cheap enough to be the default.
- **Large data: time is the risk.** Sample runs, training only what is out of date, and
  charts from kept predictions matter most. The thorough evaluation waits until before
  logging.
- **Defaults follow the size.** The tool picks quick or thorough, and sample or full rows,
  from the size of the data, and says which it picked.

## Hand edits

Settled:

- An analyst may edit a fitted table or relativity by hand before logging.
- An edit carries over when the model is trained again.
- The rest of the model is refitted around an edit, where the model type allows it.

The rest of this section is proposed.

**An edit is a constraint on the fit.** It works like monotonicity, but for one model:
monotonicity says "hold this direction", and an edit says "hold this value".

- It lives in that model's block of the node's config, as a fixed value with its note.
- Changing an edit puts the model out of date, like any setting.
- Every training holds the edited values fixed and fits the rest around them: a retrain,
  each fold of a thorough run, the final refit and a scheduled run. So the model that is
  logged was fitted with its edits in place.
- Because the rest is refitted, correlated factors adjust to the edit and the total stays
  in balance. An edit laid on top of a fit does neither.

**Two stages of feedback.**

- *At once:* a preview, with the edit laid on the current fit and marked as a preview.
- *After training:* the fit around the edit, set against the run it replaced. It shows the
  scores with their ranges, and which other tables moved to make room.

A GLM refits fast enough to skip the preview. Slower model types wait for Train, so several
edits can go in first.

**Which model types.** The ones whose model is tables or relativities: t-boost, the GLM
and EBM. A t-boost model is exactly its tables, so an edited table is still a valid model.
The gradient-boosting types have nothing to edit by hand.

**How each can refit around an edit.**

- All three already take an offset, which gives the simple route: a fixed table goes in as
  an offset, and the fit learns the rest.
- Holding only some levels of a table fixed while its other levels are fitted, and keeping
  a fixed factor's interactions, is new work. In a GLM it is standard in principle, but
  whether RustyStats exposes it needs checking. In t-boost and EBM it needs building.
- Until a model type can refit around an edit, its edits are laid on top of the fit, and
  its menu says so.

**The rest of the rules.**

- **An edit sits beside the fitted value, never over it.** Each edit records the value the
  fit had when it was made, the new value, when and why. Each edit can be undone, and the
  model card lists them all.
- **A value is fixed against the table's base:** the base level in a GLM, the table's
  average in t-boost and EBM. A relativity only means something against a base.
- **An edit changes values, not the data.** Merging levels or re-banding a factor is
  feature engineering, which stays in the pre-processing nodes. An edit can still set
  several levels to one value.
- **Kinds of edit:** set one value; set a run of levels to one value, a straight line or a
  smoothed curve; cap or floor a table.
- **Edits also fit the rows the analyst looks at,** like every other choice. The sealed
  test rows are the check on that.

There is precedent for editing by hand with live scores and an edit history: GAM Changer,
a research tool for EBM-style models.

## One shell, a module per model type

The shell is shared by every model type:

- the factor list on the left
- the menu row across the top
- panes in the middle
- the settings on the right
- the run history
- comparison

Each model type's module adds what is specific to it:

- its capabilities: what it supports of the shared settings, and what it has of its own
- its settings menu and its own steps
- training: settings and data in, a model and its predictions out
- its own result panes
- hand edits, where its model can be edited: showing an edit on a fit at once, and
  holding edits fixed while it fits
- **its effect for a factor,** in one common shape: the relativity at each level. This is
  exact for t-boost, the GLM and EBM, and estimated for the gradient-boosting types. One
  chart can then overlay how each model treats a factor.

The shell reads the capabilities to show or hide shared controls, so no `if` statements
about a particular model type spread through the shell.

**Runs keep their predictions.** Each run stores one prediction per row. The shared charts
are computed from those on demand, together with the node's data. That buys a lot:

- any factor can be charted for any model, whether or not the model uses it
- any chart can be split by a second factor or by year, with no retraining
- any two runs can be compared row by row: a double lift, or where they disagree
- the shared charts need no code per model type

Today the diagnostics are computed once, during training, for the factors in the model, and
as far as I can see predictions are not kept. The alternative is to compute AvE for every column during
training. That is cheaper to build, but it fixes the set of charts in advance and gives no
row-by-row comparison. The cost of keeping predictions is one number per row on disk, for
each model's run and each reference model.

There are three kinds of diagnostics:

- **From kept predictions,** with no model needed: AvE, lift, double lift, Gini or Lorenz
  curves, calibration and residuals. They work for any model type, so comparison is free.
- **By scoring the model again,** through the one predict every model type has: PDP.
- **From inside the model,** in its module: t-boost tables, GLM coefficients and
  relativities, EBM terms, tree importances and SHAP.

The rule of thumb: a component that needs to know the model type belongs to that model
type's module. Everything else belongs to the shell.

## Scheduling (later)

- When a schedule is set up, the analyst picks the model type it trains and logs.
- It retrains on that schedule, for example monthly as new data lands, with the node's
  saved settings, and logs the run to MLflow as a new candidate.
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
