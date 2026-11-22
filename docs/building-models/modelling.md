# Modelling

The Modelling view is where you build a model, looking at each factor as you go. A model built here is a [Model Training](nodes/model-training.md) node in your
pipeline: the pipeline imports and prepares the data, and the node is where the model is
built. The view builds t-boost models so far.

## Starting a model

1. Add a Model Training node to the pipeline and connect your prepared data to it.
2. Preview the node that feeds it, so its columns are known.
3. Select the Model Training node and choose **Interactive build** under **Select
   algorithm**.
4. Choose **Open in Modelling** in the node's panel.

Choosing **Interactive build** makes the node a t-boost model that is edited in the
Modelling view. Its panel shows the model and its target in place of the usual panes. The
choice is fixed, as a model family is: for another kind of model, add another node.

The model starts with:

- every column arriving at the node ticked as a factor. If the columns were not known when
  you chose **Interactive build**, none is ticked: use **Include all** in the model's own
  tab.
  A column added upstream later arrives unticked;
- no validation set and no test set, so the model trains on every row;
- parameters that only boost: `n_trees` 4000, `max_interaction_order` 3, `n_bags` 8 and
  `interaction_gain_hurdle` 2, with `prune` false, `band_tolerance` null and `graduate`
  false, so t-boost does not prune, band or smooth the tables.

## Switching views

**Modelling** at the bottom of the node palette shows the view over the pipeline editor;
**Pricing** brings the pipeline back. The pipeline editor keeps everything it had while the
view shows, and its keyboard shortcuts are off meanwhile.

The toolbar has:

- **Main Menu**: return to the list of modelling nodes.

- **Model Setup**: the settings every model of the node shares, on the right.
- **Training Split**: how the rows are split for training, on the right.
- Haute's project controls (**Pipeline files** and **Local files**, Assistant, Help, the
  branch, **Save** and **Commit**).

Press Model Setup or Training Split again to go back to the chosen model's settings. A model
trains on the source last chosen in Pricing. The Git, Files and Assistant panels open on the
right too.

A model's settings are part of the pipeline, so **Save**, **Commit** and undo work on them
as on any node: Ctrl+S (Cmd+S on a Mac) saves the project, and Ctrl+Z and Ctrl+Shift+Z undo
and redo the last edit to the pipeline. The toolbar has no Undo or Redo here.

## The list of models

The view opens on the interactive builds in the pipeline, or in the submodel open on the
canvas, each with its target and its last run: **Not trained**, **Training**, **Trained** or
**Failed**. Click a model's name to open it. **Main Menu** in the toolbar returns to the list.

A node set to a model family (CatBoost, a GLM and the others) is edited in its own panel
and is not listed.

## Building a model

An open node has its models and **Add** down the left, the chart pane in the middle, and
**Features**, **Parameters** and **Training** tabs on the right.

Use the collapse button beside **Models** to make more room for the chart. The narrow
sidebar keeps the view switcher available; use its reveal button to restore the models.
Pricing, Modelling and Workbench share this sidebar preference.

### Models

A node can hold several models, for example two t-boost models with different factors. Each
entry in the left column names a model, beside a dot in the colour of its line on the charts. A
bar shows under the name while the model trains, and a failed last run turns the name red.
Click a name to choose that model. Its settings and panes show; the actions in Training run that model.
The sliders settings icon, just left of its checkbox, expands Duplicate, Rename and Remove.
Click the icon again to collapse; only one model’s actions open at a time.
The checkbox beside each model name controls its chart visibility without opening or
selecting the model.

- **Add** opens a model picker. Choose **t-boost**, currently the only available type,
  to add a model starting with the chosen model’s factors, directions and parameters.
- The expanded controls offer **Duplicate**, a copy of that model's settings,
  **Rename** and **Remove**, stacked vertically. Remove asks you to confirm the named model
  before deleting it. Cancel keeps it unchanged. A node keeps at least one model.
- A copy never takes the other model's run: it trains on its own. Only one model trains at
  a time.

The Features pane on the right names every column that is not the target, the weight or the offset.
Search above it by name, and click a name to chart that factor. A factor the chosen model
leaves out is named in muted text.

The factors are the columns reaching the node, which Haute learns by previewing it. Opening
a model previews its node as clicking it in the pipeline does. If the list is empty, it
says whether the columns are loading or why the preview failed, and **Refresh** asks again.
In manual calculation nothing is previewed until you press **Refresh**.

### Model setup and training split

**Model Setup** holds the settings every model shares: the target and the objective, the
weight and the offset, and the metrics. **Training Split** holds how the rows are split:
validation rows, and a final test set. Every model uses the same split.

### The chosen model's settings

The right-hand column has **Features**, **Parameters** and **Training** tabs. Features opens first:
click a feature name to chart it. The checkbox beside the name includes or excludes it for
the selected model. The sliders icon, just left of the checkbox, opens its direction
settings without changing the chart. One feature’s settings open at a time; click the icon
again to collapse them. Choosing another model shows that model’s settings in the expanded
row. The options slide open and closed; reduced motion makes the change immediate.

- Untick a factor to leave it out of this model, for example one you may not rate on or
  cannot supply when quoting. **Include all** and **Exclude all** set every factor.
- The arrows beside a numeric factor set its direction: decreasing, no constraint or
  increasing.
- Search by name, or filter to the included or the excluded factors.

### Panes

- **Factor** draws the selected factor: the actual by level, over the exposure in each
  level, and the expected of every ticked model from its latest run, each in its colour, the
  chosen model's line heavier. A model that left the factor out is drawn dashed and marked
  **(not in model)**: its expected is its prediction, which does not use the factor, so the
  chart shows what that model misses. A date column is not charted.
- **Learning curve** draws the deviance by boosting round, on the rows each bag trains on
  and on the rows it holds back, each averaged over the bags. It draws while the model
  trains and stays afterwards.
- **Tables** shows the latest run's t-boost tables, as the node panel's results do.

## Evaluation and saved results

Choose validation in **Training Split**, or explicitly choose development-only exploration
in the GBM card before the first fit. Once a model has a development-only fit, changing
its settings does not require that acknowledgement again; **Rebuild needed** means Train
GBM can rebuild the saved result with the current setup. Development results describe rows used for fitting;
Validation and OOF validation describe held-out predictions. The pruning path separately
uses Training OOB evidence; under CV its tables describe the development reference fit.

Finalised models can be saved to a project file, logged to MLflow and exported as a training
script using **Export final model** in Training.

## While the pipeline cannot be edited

When the pipeline is read-only, or the assistant is working on it, you can still open a
model, change the settings tab and the pane and chart its factors. You cannot change its factors or
settings, or train it, until the pipeline can be edited again.

Readiness warnings appear below the controls in the right-hand settings pane. They never
push the chart tabs down. Train stays disabled until the configuration is ready.

### Building t-boost in stages

Set the GBM, pruning, banding and smoothing options in **Parameters**. Open **Training**
to run each stage; its action becomes **Cancel** while running. Switching tabs preserves
your settings and the running job. The GBM remains the required base; its parameters stay
in the JSON editor. Phase switches change the draft chain, leaving saved results and the
applied recipe intact until you run/apply another stage.

1. **Train GBM** fits the full model, retains its evidence and prepares the next enabled
   phase's path within the same Running/Cancel state. GBM tuning compares full models only
   and keeps the winning candidate's evaluation fits.
2. **Paths** on the canvas has Pruning, Banding and Smoothing selectors. Click a point to
   inspect its predictions and tables without editing parameters or changing the applied
   model. Suggested, Selected and Applied are separate. **Apply selected** commits the
   exact candidate inspected; **Keep unchanged** retains the predecessor and disables
   that phase. The next enabled path is then prepared automatically.
3. In **Parameters**, tick or untick phases. Each uses the nearest earlier enabled phase.
   Banding sweeps bounded fractions of the configured noise tolerance and deviance cap;
   its complexity axis counts interaction grid cells, excluding main effects. Smoothing
   uses multipliers of each table's own Auto proposal, preserving zero proposals. Manual
   alpha is a separate method. Interaction smoothing stays fixed across nonidentity points.
   Rejected and unchanged results are labelled. Use **Build pruning**, **Build banding** or
   **Build smoothing** after changing controls or to retry preparation. These actions reuse
   retained training evidence; they do not fit another GBM.
4. In the model's left-hand settings, tick Full GBM, Pruned, Banded or Smoothed to compare
   their predictions. These checkboxes only change the display. Tables has its own stage
   selector; Learning Curve belongs to the original GBM.
5. **Finalise applied model** freezes the applied recipe, refits it on development rows and
   evaluates any reserved test rows. **Export final model** opens the usual native save and
   MLflow logging controls. Only this explicit finalisation uses the reserved test set.

Holdout exploration uses its training-partition fit and validation predictions. With cross-
validation, curves pool the matching held-out predictions from every fold. Tables and the
pruning path show a separately labelled development reference fit. Without validation, the
curves are labelled development diagnostics. Repeated exploration is model selection, so
validation performance is not an untouched final-test result.

Completed stages and final export artifacts survive a server restart. Pending settings leave
saved results intact; changed GBM inputs require a new build. Post-fit actions use the frozen
source snapshot. Local input-file and pipeline changes mark a build stale; remote data is
refreshed by a new GBM build. A single bag or too few OOB rows leaves the full model usable
and explains why a pruning path is unavailable. Legacy models need a new staged build before
they can be pruned, banded or smoothed interactively.

### Compare and keep a candidate

The main path chart shows percentage objective-loss change from the unchanged predecessor
on the same evaluation rows. Zero is unchanged and negative is better; a near-zero baseline
uses clearly labelled absolute change instead. The selection ceiling shows the configured
trade-off against both the best point and the unchanged model, not statistical significance.
Banding uses the fraction of the configured budget on the x-axis. Larger budgets permit more
simplification within the noise tolerance and deviance cap; table and cell counts are outcomes
shown alongside the selected point, and need not change at every budget.
Cross-validation transforms each retained training fit and pools its held-out predictions.
Reference-fit table complexity is labelled separately. Training OOB pruning evidence remains
available in its own explicitly labelled chart; it is not relabelled as validation loss.

The suggestion chooses a simpler feasible candidate within the practical loss tolerance of
both the best candidate and the unchanged predecessor. The default fraction is 0.001 (0.1%),
with an absolute tolerance of 1e-10 near zero. Development-only exploration has no validation
suggestion. A rejected point shows the actual retained model, not a claimed smoothing change.
Per-table EDF, where available, describes the solver before constraints and reanchoring.
Equivalent settings share evaluation results, but remain separate selectable policies for
future refits. Pruning budgets accommodate the mandatory tables in every retained fit;
the actual number of retained tables can differ between folds.

Selecting a Banding point also shows a compact before/after histogram of interaction-table
cell counts, total cells and percentage reduction. It describes the reference fit; main
effects and factored effects without a cell grid are excluded. Rebuild older banding paths
to refresh suggestions created before the grid-cell counting correction. Smoothing points show mini
charts of average predictions by fitted factor, with the predecessor and selected candidate
on the same scale. These are grouped predictions over the observed rows, not PDPs. Factors
with the largest changes appear first, six at a time. The summaries read saved diagnostics
and add no fitting or scoring work when you select a point.

Use the separate **Factor** or **Tables** canvas tab to inspect a selected candidate. Earlier paths remain
available while their parent snapshots remain valid. **Clear prepared path** keeps the applied
model and starts no job. Applying an earlier stage removes dependent applied results. Final
refits replay the selected settings: Auto-relative smoothing recomputes each fit's proposals,
rather than transferring an alpha vector or table boundaries from exploration.

**Saved checkpoints** keeps up to ten named applied models with their settings. Save before
rebuilding to retain a result. Restore replaces the applied model and its per-model settings;
it refuses changed upstream data or incompatible shared setup/split settings. Delete removes
that saved checkpoint, without removing the currently applied model.

The Final model card identifies the exact applied recipe, independently of phase toggles,
pending settings and visible curves. Its final-test metrics and export output are separate
from the exploratory comparisons.

Automatic preparation is included in the preceding stage’s Running/Cancel state. You can
retrain the GBM or an available earlier stage even when a candidate is ready. Any genuine
reason an action is unavailable appears at the bottom of Training. Finalise always uses
the named applied output, independently of any unaccepted candidate.

### Learning curves

The learning curve shows each t-boost bag separately. Switch between Training
and Validation, and use the coloured checkboxes to show or hide individual bags.
Validation curves use each bag's own held-back rows. Once fitting completes, the
mean includes all bags, holding stopped bags at their last loss. This mean is a
diagnostic average of bag losses, not the loss of the ensemble's predictions.

The model's progress bar includes fitting and automatic preparation of the next
stage. Its percentage is approximate and does not restart between those jobs.
