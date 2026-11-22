# Team brief: honing the Modelling UI design for Haute

## What this is

Haute is a pricing tool for insurance: a pipeline editor with pre-processing, model
training, scoring, rating and optimisation nodes. Its owner is designing a new **Modelling
UI**, the place where a pricing analyst builds the models for a modelling node. The design
is a loose brainstorm in three markdown files.

Your team's job is to hone it. The owner's words: "brainstorm and really hone the UI
layout, and workflows, and analytical capability to make this as intuitive, and effective as
possible." The goal behind that: "the best possible workflow and interface for allowing
analysts to build the best possible models."

"Loose" means a design brainstorm. No code is being written now, no tests, nothing goes in
`specs/`. But "hone" means concrete: real layouts, real steps, real methods.

## The team folder

`/tmp/claude-1000/-home-ralph-suite-haute-modelling-demo/2e3aa27c-9b97-4db9-9219-db47210d5c8a/scratchpad/modelling-team/`

It is the only place you may write. Its `before/` folder is a fixed snapshot of the current
design. Never edit `before/`.

- `before/overview.md`: the idea, the decisions and the reasons. Its "Where things stand"
  section has three lists: Settled, Proposed, Open.
- `before/workflow.md`: how an analyst works.
- `before/ui.md`: what is on screen.

Your task says which of these to read. Work from the snapshot, not from any other copy.

## Settled: the owner's decisions, word for word

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

Only these lines are settled. Everything else in the three files is a proposal, including
what else the shared menu holds beyond factors and monotonicity (target, exposure or
weight, offset, loss, split, metrics), the menu row, the evaluation rules and the panes.

## Rules that bind every member

1. **Settled is settled.** Do not contradict, weaken or re-argue a settled line, and do not
   design around one silently. If you believe a settled line causes a real problem, put it
   in a separate "Concerns about settled items" note: the problem, and the smallest change
   that would fix it. Clustering is parked and ensembles are out: do not design either.
2. **Proposed is fair game.** The numbered Proposed items (1 to 19 in `before/overview.md`)
   are suggestions. Improve, replace or cut them, naming the number and the reason.
3. **Judge everything against the eight principles** in `before/overview.md` ("What
   building the best models needs"), and against the owner's two tests. *Intuitive*: an
   analyst can tell what to do next without being taught. *Effective*: it makes the model
   better, or stops a mistake.
4. **Honing means sharper, not longer.** Cut what does not earn its place. Every addition
   names the analyst's problem it solves. One strong idea beats five weak ones.
5. **Use the owner's words:** model type (not family or algorithm), factor (not feature),
   run, reference model, hand edit, the All models menu, training / validation / test rows,
   log (to MLflow), out of date.
6. **Claims about Haute must be checked in the code** before you make them. Write "Haute
   has X today" only with a file path as evidence. Mark anything you could not check as
   unverified.
7. **Claims about other tools or papers need a source** (a URL or a full reference) and
   must not go beyond what the source shows. Mark uncertainty plainly. Never invent a
   source.
8. **Read-only outside the team folder.** Do not modify anything under
   `/home/ralph/suite/` (haute, the demo project, t-boost or any other repository). Do not
   run tests, builds, the app, installs, or any git command that changes state. Reading
   files, `grep`, `ls` and a one-line `python -c` to inspect an installed package are fine.
9. **Web searches stay generic.** Never put the owner's name, company, project names, file
   paths or text from these files into a search query or any request to an outside
   service.
10. **Plain writing.** Short sentences, plain words, one idea per bullet. No hype and no
    filler. Draw a wireframe as an indented text block (4 spaces), never as an image.
11. **Do not start other agents.** Do the work yourself.

## Where things are in Haute

Everything is under `/home/ralph/suite/haute/`. The working tree has uncommitted work in
progress, which is expected: read it as the current state.

The Modelling view as it is today:

- `frontend/src/modelling/ModellingView.tsx`, `frontend/src/modelling/ModellingToolbar.tsx`
- `frontend/src/stores/useModellingViewStore.ts`, `frontend/src/stores/useNodeResultsStore.ts`
- `docs/building-models/modelling.md` (the user guide for the view)
- `specs/frontend-modelling-optimiser-ui/high-level.md` and `low-level.md` (search for
  "Modelling view" and "Modelling config panes")

The node editor, its panes and the result tabs:

- `frontend/src/panels/ModellingConfig.tsx`, `frontend/src/panels/ModellingPreview.tsx`,
  `frontend/src/panels/ResultsWorkspace.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx`
- `frontend/src/panels/modelling/`: settings (`TargetAndTaskConfig`, `CommonFeatureConfig`,
  `SplitAndMetricsConfig`, `HyperparametersConfig`, `GLMTermsConfig`, `TermCard`,
  `GLMInteractionsConfig`, `EBMInteractionsConfig`, `TrainingActionsAndResults`,
  `TrainingProgress`), result tabs (`SummaryTab`, `FeaturesTab`, `AveTab`, `PdpTab`,
  `LiftTab`, `ResidualsTab`, `ShapCurvesTab`, `LossTab`, `TBoostTablesTab`,
  `TermTableView`, `EBMTermsTab`, `GLMCoefficientsTab`, `GLMRelativitiesTab`), shared
  parts (`ChartScaffold`, `FeatureBrowser`, `termCharts`), and
  `algorithmCapabilities.json` (what each model type supports)
- `docs/building-models/nodes/model-training.md`

The backend:

- `src/haute/modelling/`: `_algorithm_base.py` and `_descriptors.py` (the adapter and the
  capability list per model type), `_tboost.py`, `_ebm.py`, `_rustystats.py` (the GLM),
  `_lightgbm.py`, `_xgboost.py`, `_evaluation.py` and `_split.py` (splits),
  `_training_job.py`, `_result_types.py` (what a run returns), `_metrics.py`,
  `_tuning.py`, `_model_card.py`, `_candidate_run.py` and `_mlflow_log.py` (MLflow),
  `_feature_contract.py`, `_shap.py`
- `specs/modelling/high-level.md` and `low-level.md`, `specs/mlflow-model-registry/`
- Installed packages, readable at
  `/home/ralph/suite/haute/.venv/lib/python3.11/site-packages/`: `t_boost`, `interpret`
  (EBM), `rustystats`, `lightgbm`, `xgboost`, `catboost`, `optuna`

t-boost, the owner's own model type (gradient boosting whose model is exactly a set of
rating tables):

- `/home/ralph/suite/t-boost/README.md` and `/home/ralph/suite/t-boost/docs/`

An example project: `/home/ralph/suite/haute-modelling-demo/` (see `rating/main.py` and
`rating/config/model_training/modelling.json`).

## Your output

Try to write your full report to the path your task names, inside the team folder. Whether
or not the write works, also return the full report text in your structured output, with
`file_written` saying whether the file was written. Later members rely on your report, so
make it complete and self-contained.
