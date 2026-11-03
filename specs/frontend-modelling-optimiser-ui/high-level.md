# Frontend Modelling & Optimiser UI — High-Level Specification

## Purpose

This component configures, submits and explains modelling training and optimiser solves. It
combines domain-specific configuration controls with result panels for diagnostics, charts,
frontiers, ratebooks, exports and previewed optimiser scenarios.

## Scope

It owns the modelling and optimiser configuration/preview panels and their local chart, summary,
factor/ratebook and helper modules. Node-editor dispatch is documented by
[frontend-node-editors](../frontend-node-editors/high-level.md); job execution and persisted
results are supplied by API and result-store layers.

## Behaviour

- Modelling config selects an algorithm, feature/target/evaluation/metric options and
  algorithm-specific controls, including GLM terms, regularisation and dispersion
  estimation, and optional tuning for every other family, across the panes defined in
  [Modelling config panes](#modelling-config-panes). Training submission
  records the returned job handle immediately, keeps an explicit Cancel control visible through
  preparation and fitting, and shows progress, estimates, failures and result actions. An
  insufficient-GPU estimate or terminal 507 asks the user to select CPU and retry (or reduce the
  workload); it never claims that CPU fallback happened automatically.
- The modelling editor exposes monotonicity as the sole additional cross-algorithm
  capability lever. A tree family's selector is enabled only for included numeric input
  features and writes `-1` or `1` per feature, removing the key when the user selects the
  dash. String/categorical, Boolean, date/datetime and excluded features never take a
  direction, and target, weight and other role columns are not listed as features.
- Modelling preview exposes summary unconditionally as the fallback tab whenever a result
  exists, presents remaining tabs (coefficients/relativities, loss, lift, residuals,
  feature importance, AVE, PDP and SHAP curves) only when backed by non-empty result data, and resets
  selection to summary when the result changes. When a node has no result because the one the
  browser remembered is gone from the server (it restarted, or the job expired), its results
  panel shows the node's data preview, as for a node that was never trained; the Export pane
  says the last result is no longer available and to train the model again to export it.
- The Features tab switches between importance measures with one button each: Prediction,
  then Loss and SHAP (mean absolute SHAP) when the result has them, then SHAP beeswarm when
  it has beeswarm rows (CatBoost, XGBoost, LightGBM, t-boost). The beeswarm draws one row per feature,
  in mean-absolute-SHAP order, and one dot per sampled row (up to 2,000) placed by its SHAP
  value on the link scale around a zero line; a numeric feature's dots run from the low (blue)
  to the high (red) colour by the value's rank, and a categorical feature's or a missing
  value's dots are grey, which no low-to-high mix resembles, with a legend entry saying they
  have no value order. It draws on the panel surface with the other modelling charts' axis
  text, grid lines and label colours, not a plot of its own. The dots are painted on a canvas
  layer under the chart's labels so thousands of them stay responsive. It replaces the search and
  Features shown controls with one line saying it shows the top 20 of N features by mean
  |SHAP| over the sampled rows. Dots stack within their row so dense regions show as height,
  scaled so the densest stack fits the row. Pointing at a dot names its feature, value (the
  level for a categorical feature) and SHAP value; each feature row is a single tab stop
  whose focus states that feature's rows and SHAP range. A closed values table lists, per
  feature, the rows and the minimum, median and maximum SHAP value.
- A SHAP curves tab follows PDP when the result has curves (CatBoost, XGBoost, LightGBM,
  t-boost). It
  uses the AvE/PDP per-feature layout and shares their selected feature. Under a log link
  (`shap_link` `log`) it shows relativities, exp of the mean SHAP value, around a 1.0
  baseline; otherwise mean SHAP values around 0. A numeric feature draws a line through each
  band's mean value with a shaded 10th to 90th percentile range and a separate "(missing)"
  point when missing rows exist; a categorical feature draws one bar per level in the
  result's order (most rows first) with 10th to 90th percentile whiskers and a row-count
  strip, and says how many less frequent levels are not shown. Points and bars are
  focusable and name their band or level, rows, mean SHAP, relativity under a log link and
  percentile range; a closed values table lists the same per point.
- Completed training accepts missing categorical PDP levels as JSON `null` and labels
  them `(missing)` in the chart. Numeric PDP levels remain non-null. Invalid status
  responses stop polling with a visible response error for every background job type
  (training, optimiser, Explore and pivot); network failures remain retryable, so a
  rejected result cannot leave the last reported stage running forever.
- CatBoost and RustyStats/GLM results use Explore's full-width, equal-width preview
  buttons, with keyboard navigation and an explicitly labelled active pane. Narrow
  panels scroll the button strip horizontally rather than clipping view names.
  Diagnostic views introduce their chart or table with a plain-language title; what
  each view shows, and how to read a chart's values, is explained in the docs rather
  than in the pane.
- The completed summary uses responsive, themed cards with final-test performance
  first (without a test set, when a validation fit ran and the reported diagnostics are
  in-sample, the validation fit's selection metrics lead instead, labelled with their row
  count), separately labelled diagnostics, model information, and optional GLM fit
  statistics, the regularisation actually applied, and smooth terms. When RustyStats marks a
  GLM's inference invalid (penalties, monotonicity, smoothing), coefficients show dashes and the
  reason instead of statistics. Metric values are prominent. Without a reserved final test, no
  card presents development diagnostics as held-out performance. Candidate selection and tuning
  retain their complete evidence in separately headed cards; warnings remain visible
  above the summary. The summary carries no export action or model path: saving the
  trained model to a file and logging it to MLflow belong to the modelling editor's
  **Export** pane.
- The MLflow log action ("Log run to MLflow") stays
  visible in every backend state: enabled with the resolved destination
  named beneath it when tracking is connected, and disabled with the
  actionable reason plus a "Configure MLflow" link (opening the shared
  MLflow settings dialog) when it is not. Success shows just the run ID,
  with an "Open in Databricks" / "Open run" link underneath when a
  databricks/server backend returns a run link.
  The local command includes `MLFLOW_ALLOW_FILE_STORE=true`, required by the
  supported MLflow file-store backend. A labelled terminal choice offers
  PowerShell and bash/zsh commands with appropriate quoting; copying includes
  both the opt-in and launch command. Remote successes never offer either.
- Optimiser config selects input/objective/mode, banding/ratebook factors, constraints, solver
  options and frontier ranges across the panes defined in
  [Optimiser config panes](#optimiser-config-panes); it can auto-range constraints and submit
  solves. Starting another
  auto-range request or unmounting best-effort cancels that auto-range job; a running solve is
  stopped from the Solve pane.
- Ratebook source and inferred factor columns change in one atomic config update. Constraint
  renames/removals migrate or remove their matching canonical `frontier_ranges` entry in the
  same update; range fields never inherit the removed global frontier bounds.
- Optimiser preview renders summary, convergence, detail, frontier, ratebook/rates and quotes
  views (see [Optimiser config panes](#optimiser-config-panes)); structured API details are
  preferred on failures. The Export pane's "Log to MLflow" action stays visible in every backend
  state — disabled with the off-reason and a "Configure MLflow" link when tracking is unavailable.
  The optimiser Export pane opens its MLflow section with the same manual-logging
  explainer the modelling Export pane uses. The data preview
  groups and charts bounded scenario samples and can calculate statistics.
- Modelling and optimiser action areas render actionable memory-pressure and
  rejected-strategy diagnostics with profile, blocking node/operator, cost,
  reason, and remediation when the guarded payload supplies them. Other,
  missing, or unsupported planner detail never becomes an invented success or
  pre-emptive submit gate; the primary request/status error remains visible.
- Optimiser input selection stays scoped to connected incoming edges and can
  explicitly select a `data_input`. Each option's displayed and persisted value is the edge's
  exact executable input name, so separate frames from one API Input remain separate choices and
  source node ids never leak into the user contract. `banding_source` and Optimiser Apply's
  `ratebook_input` use the same identity rule. Its columns come from the guarded
  post-provider/post-Polars schema or preview contract; the panel does not
  inspect provider config or trigger a snapshot build merely to discover them.
  Selector matching is byte-for-byte; the UI does not trim or otherwise normalise
  a stale persisted value into a valid input name. A present non-string selector is
  malformed, remains visibly invalid, and blocks solve; it is not treated as an absent
  selector eligible for single-input inference.
  Ratebook factor-level ordering resolves the configured `banding_source` through
  that exact incoming edge name too; it neither interprets the value as a node id
  nor infers a sole Banding parent when the selector is absent or stale.
  Optimiser Apply always shows an explicit input selector for a loaded ratebook
  artifact, including with one connected input; its empty option is an incomplete
  configuration, never a first-edge default.
  Snapshot/capability failures remain visible in estimate, solve, and
  auto-range states.
- An explicit Banding source that is no longer directly connected and
  recognised configured outputs with zero valid levels are combined into one
  accessible warning, while healthy factors remain selectable. An
  unconfigured source or ordinary empty graph does not warn, and the
  exactly-one-direct-Banding fallback is unchanged.

## Design rationale

Large config forms are split into focused subcomponents so algorithm/mode gates preserve hidden
configuration instead of destructively rewriting it. Multi-field invariants use one object
update because the production callback spreads the last committed config. Asynchronous actions
are sequence/abort aware where a newer request can supersede an older request. Result tabs defer
expensive work until opened and use structured backend diagnostics when available.

## Interactions

This component consumes graph/node-result stores, API clients, background-job state and
`frontend/src/utils/banding.ts` for ratebook factor levels. It is routed from the node editor and
shares the preview frame/tab controls with
[frontend-preview-explore](../frontend-preview-explore/high-level.md).

## Failure model

Training, solve, MLflow, export, auto-range and materialisation failures are displayed in their
local action/result area. A GLM dispersion estimate (theta or variance power) belongs to the
training draft it was asked for: changing the node's training settings (its target, terms,
weight, offset or family, or typing the value it estimates), the graph feeding it, the data
source or the document stops it, its value is never filled in, and a neutral note says it
stopped; an unchanged estimate fills its field, and closing the panel stops it silently. Training cancellation posts the active job ID, records the returned
terminal state immediately, and leaves a terminal race winner intact. Structured training status
fields (`error_code`, `http_status_code`, `error_detail`) survive runtime parsing so an asynchronous
GPU-VRAM 507 retains its actionable server message. A failure raised inside the training worker
also keeps its `worker_remote_traceback`, shown collapsed under **Error details** beneath
"Training failed", since the failure message points the user at the job's error details. The training
memory estimate waits out the pipeline's own input-snapshot build rather than failing while it
runs. A refusal naming only other evaluation previews keeps its short, silent backoff (a few
seconds, under the usual estimating indicator); a refusal naming an input-snapshot build, alone
or beside evaluation previews, is retried for up to about a minute, with a neutral "Waiting for
the input snapshot to finish" notice and Train disabled meanwhile. Any other refusal fails at
once, as before. Train pressed while a snapshot
build runs is still refused by the server, since training's admission waits out only
evaluation previews; making it wait for the build too is a follow-up on the server side. A locally aborted/superseded auto-range request
suppresses an error, while a terminal cancelled/superseded status returned by the server is shown
in the auto-range error area. Deliberately strict helper parsers throw for malformed numerical
result contracts rather than silently charting incorrect values.

## Modelling config panes

With a known algorithm selected, the modelling node panel presents the same six panes for every
family: **Target**, **Features**, **Parameters**, **Split**, **Train** and **Export**, in the same
shared equal-width pane-tab strip the Explore editor uses, hosted by the node panel
([frontend-node-editors](../frontend-node-editors/low-level.md#modelling-config-panes)) and
carrying the accessible tab indicators of
[frontend-preview-explore](../frontend-preview-explore/low-level.md#modelling-config-panes).
One `modellingPanesFor(algorithm)` list drives both the tabs and the pane bodies. Every family
but the GLM shares one Target, Features and Parameters body (`usesSharedPanes`); the GLM has its
own three. The active pane is remembered per node in the UI store, and Target opens when none is
remembered. Without an algorithm, the existing gateway renders alone. A non-empty unsupported
algorithm renders an explicit inline diagnostic and no pane strip; it never falls through to
CatBoost. Pane ownership:

- **Target** — the selected algorithm as read-only context ("Algorithm: CatBoost"; the GLM's
  reads **Algorithm Rustystats** while the stored algorithm remains `glm`), then **Target and
  objective**, **Weight and offset** and **Metrics**. The gateway may set the algorithm only
  while it is unset; after selection, neither this pane nor any other supported-node editor
  action changes it. To configure another family, the user creates a separate modelling node,
  preserving the original node and all of its settings.
  A tree family has a searchable target picker and one **Objective** button per loss the family
  supports, and no separate task selector: choosing a loss stores its regression or
  classification task and the metrics the backend defaults to for that objective
  (`effectiveMetrics`) in one update, and pressing the selected loss clears it. Tweedie reveals
  a variance-power slider and number field bounded to the open interval 1 to 2; choosing it
  with no stored power writes the 1.5 midpoint in the same update, and a saved power outside
  the interval is flagged. A classification loss on a target that is not Boolean shows the
  positive-class field (see [Model family capabilities](#model-family-capabilities)).
  The GLM chooses a family, which resets the link to the family's canonical one and writes its
  default metrics (so a Gamma GLM headlines Gamma deviance), then a link; families and links
  come from the table the backend validates against (RustyStats supports only identity, log,
  and logit links), and a saved family or link outside it is flagged. A Tweedie family needs a
  variance power (1 to 2) and a Negative Binomial family a theta, each set explicitly or
  estimated from the data; the GLM also has an **Intercept** checkbox.
  The info icon beside **Offset column (optional)** states the families' offset rules: a
  positive exposure multiplier under a log link (a log-link GLM, or a Poisson, Gamma or Tweedie
  loss), read as a full policy term when missing at scoring; added under any other link and
  required when scoring; and no offset with the Logloss loss on XGBoost, LightGBM, t-boost
  and EBM. Both metric pickers read one table
  (`frontend/src/panels/modelling/metricOptions.ts`): each metric measures a regression or a
  classification, and Tweedie deviance, measured at the model's own variance power, is offered
  only for a Tweedie loss or family. Every metric stays visible; one the selected objective
  cannot report is disabled with the reason.
- **Features** — a tree family's feature browser: the included and excluded counts, a
  case-insensitive name search, **All**, **Included** and **Excluded** filters with their
  counts, and **Include all** and **Exclude all**, which set every eligible feature, including
  features the search or filter hides. Each eligible feature is one row: an inclusion checkbox,
  the name, the upstream dtype label coloured by type, and a three-option monotonicity
  selector without a repeated visible label: a red downward arrow (`-1`), a yellow dash (no
  stored constraint) and a green upward arrow (`1`), each named for assistive technology and
  pressed when selected. Features are opt-in: every checkbox starts unticked on a new node,
  including for a column that appears upstream later, and ticking one writes it to
  `feature_columns`. Columns consumed as target, weight, offset, or active evaluation/metadata
  roles are not presented as trainable features. The selector is enabled only for included
  numeric features. Unticking a feature is immediate and reversible: it asks for no
  confirmation and keeps the feature's monotonic direction, which stays visibly selected in the
  greyed, disabled control, does not apply while the feature is excluded, and becomes active
  again when the feature is re-included. A stored feature that is no longer an upstream column
  is listed as not found with a remove action, only once the upstream columns are known; before
  then the saved feature count is stated instead. An EBM adds its pairwise-interaction control
  (see [Model family capabilities](#model-family-capabilities)). GLM's Features
  pane is its term editor instead: a feature is in the model when it has a term, is read by an
  expression, or is an interaction factor. Fit menus follow the column's dtype class (continuous,
  integer, boolean, categorical; unsupported dtypes are hidden and counted), GLM monotonicity
  lives on each term, and terms that cannot be fitted (role, missing, or unsupported columns, and
  malformed entries) are listed with their reason and removal. Interaction cards show which main
  effects Include main effects adds, which fits each slot may use, and any conflict the backend
  would refuse, on every card involved. The GLM pane never writes the tree families'
  `feature_columns`, `monotone_constraints`, or `feature_weights`.
- **Parameters** — a tree family's pane starts with a **Parameter strategy** radio group,
  **Fixed parameters** or **Tune parameters**, and shows exactly one strategy body. Fixed
  parameters shows only the algorithm-neutral **Parameters JSON** object editor. Tune parameters
  shows a note, Trial count (5 to 50), Seed, Selection metric (one of the reported metrics) and
  the search space JSON, labelled with the selected family's capability label (for example
  "XGBoost search space JSON"). The note says whether a test set is held out during tuning ("No
  test set is reserved. Reserve one in Split for an independent evaluation." otherwise), offers
  **Review split →**, which opens the Split pane, and states the fit budget in the Train pane's
  run-summary words, so a t-boost study ends with its winning validation fit rather than a
  final fit. Neither JSON editor has Apply/Revert controls. Each family's gateway starter
  parameters are visible and editable in that JSON: CatBoost's include
  `one_hot_max_size: 10`, and t-boost's are under
  [Model family capabilities](#model-family-capabilities). Haute adds no hidden default, nodes
  created earlier keep their parameters, and an empty stored object shows as `{}`. A
  syntactically valid top-level object updates its corresponding config automatically, except
  that a fixed-parameter object holding a key the family's descriptor reserves (its loss,
  metric, seed and training-file keys, read from `algorithmCapabilities.json`), or CatBoost's
  Train-owned `task_type`, is refused with the reason. An invalid, non-object, or reserved-key
  draft stays local to that node, is named in an inline alert beneath its editor, and is a
  Train-guard issue while its strategy is selected. This preserves each library's complete
  parameter surface without duplicating its evolving catalogue: arbitrary current or future
  keys and nested JSON values round-trip unchanged, and the Train-pane GPU `task_type` remains
  merged from the latest stored object. Search-space candidate arrays render on one line per
  parameter while nested conditional objects remain indented. Selecting Tune parameters seeds
  the family's own starter search space (CatBoost's candidate lists for `depth`,
  `learning_rate`, and `l2_leaf_reg`), 20 trials, seed 42 and the first reported metric, and in
  the same update turns on `refit_on_development`, except for t-boost, which is never refit; it
  never changes the evaluation. Selecting Fixed parameters removes tuning without changing the
  last valid fixed Parameters JSON. The editor component accepts the family's label, starter
  search space and reserved keys, so every tree family uses it without bespoke controls.
  The GLM's Parameters pane holds its regularization and solver settings, stored at the node
  top level rather than in `config.params`. Its regularization type (None, Ridge, Lasso, Elastic
  Net) is indicated by its pressed button without a duplicate heading badge. Choosing a type
  writes cross-validation defaults (5 folds, minimum deviance, seed 42) when absent, while the
  seed is hidden; penalty mode is Cross-validated, with folds and a selection rule, or a Fixed
  alpha; choosing Elastic Net stores an L1 ratio of 0.5 when absent and shows its slider
  immediately, without explanatory copy or setup controls. Older Elastic Net configurations
  with no ratio remain invalid until the slider is changed; the Train list names that issue and
  the pane does not repeat it.
  Inline alerts, beside the type buttons and under the Solver heading even while it is closed,
  explain why regularization cannot combine with automatically smoothed splines and why robust
  standard errors cannot combine with regularization, monotonicity or those splines; they are
  the Train guard's own issues. A Solver disclosure holds maximum iterations, tolerance, and
  robust standard errors.
- **Split** — a **Row limit** first (empty for all rows), then the allocation, then one
  canonical version-1 evaluation in three sections. **Split strategy** is Random split, Group
  split with its group column, or Time-based split with its date column. **Validation
  strategy** is Holdout validation with a Validation set (%) or Validation starts date,
  Cross-validation with a fold count (expanding windows when time-based), or No validation; a
  holdout adds **Refit on training + validation**, locked on while tuning (t-boost has neither
  the box nor Cross-validation; see [Model family capabilities](#model-family-capabilities)).
  **Test set** is a Test set (%) for random and group splits, where 0 means none, or a Test
  starts date.
  Percentages are source-relative and stored as fractions, and the evaluation seed stays
  internal. Once the relevant fields are valid, the allocation shows exact training,
  validation and test rows, per-fit row ranges and group counts or date ranges from the
  estimate's evaluation preview when its strategy and validation method match the editor, and
  target proportions otherwise. The pane shows its own evaluation issues: a validation and test
  total of 100% or more (`evaluation-fraction-sum`) beneath the validation settings, and the
  others above the allocation. A failed evaluation preview, which the server codes
  `evaluation_preview_failed`, is shown at the foot of the pane in the server's words. The pane
  uses the Training, Validation and Test vocabulary of the
  [modelling specification](../modelling/high-level.md#training-configuration-experience).
- **Train** — the read-only run summary; the GPU toggle (CatBoost: stored as the GPU task-type
  parameter; XGBoost: stored as the node's `device: "gpu"`, enabled only when
  `GET /api/modelling/gpu` reports the server can train on a CUDA GPU, otherwise disabled
  beside the server's reason, and always switchable back to CPU); the staleness banner ("Config
  changed since last training", with **Re-train**); the RAM/VRAM estimate at the Split pane's
  row limit (an estimate the server cannot size shows its reason in place of the missing
  figures, never a memory verdict; the VRAM figure is rescaled from the server's
  `gpu_vram_rows` to the rows training will use); the Train/Cancel actions with the **Complete
  before training** list; live progress, the completion badge and the error card. An estimate
  the server refuses shows in the estimate's place: one coded `evaluation_preview_failed` in the
  server's own words, which name the failed preview, and any other under "Memory estimate
  failed". The run summary names the model (the family's label with its loss, or the GLM
  family), target, feature count, evaluation method and allocation, fit budget (with a
  cross-validated GLM penalty's internal folds) and compute. For a CatBoost model whose
  selected features include String or Categorical columns it also names their encoding:
  "One-hot up to N levels, target statistics above" when `one_hot_max_size` is set, "Tuned
  (one_hot_max_size is in the search space)" when tuning varies it, and otherwise "CatBoost's
  default (target statistics)", without claiming a level count that differs between CPU and GPU
  training. Its checkbox and text/number controls use the same visible themed borders,
  backgrounds, typography and spacing as the rest of the modelling editor; labels never collapse
  into input placeholders.
  Train stays enabled while idle; with tuning enabled the action reads **Tune & Train**. While
  the configuration has any frontend Train-guard issue, including an invalid JSON draft for the
  selected parameter strategy, and no job is starting or running, the **Complete before
  training** alert directly beneath the main Train button lists every issue with a **Go to**
  button naming the pane that fixes it (Target, Features, Parameters or Split). A Train or
  Re-train press sends no request until that list is empty. Live progress renders the
  backend's existing bounded `train_loss_history` snapshot and
  `train_loss_history_truncated` state rather than reconstructing iterations from polls; a
  truncated chart is labelled as the latest retained window. A browser-derived estimated time
  remaining uses successive distinct increasing `(iteration, elapsed_seconds)` samples for the
  current job. It is hidden until two valid samples exist, on a duplicate/stalled or
  non-monotonic update, when iteration/total/rate is non-finite or non-positive, and after
  completion; a new job clears the estimator. Tuning progress instead displays phase,
  trial/fold indices, completed/total fits and best objective, and derives ETA only
  from enough completed increasing fit durations.
- **Export** — everything that publishes the last trained model, in two sections. **MLflow
  logging** holds the destination selector and experiment path fields (headed by an
  explainer that logging is manual — nothing is logged automatically; the experiment field's
  placeholder is the real computed default — `/Shared/haute/{node label}` for Databricks, the
  bare node label otherwise — and offers existing experiment names through a lazily fetched
  datalist while connected; the suggestions are keyed to the resolved tracking destination, so a
  destination switch clears them, refetches on the next focus, and discards any in-flight
  response from the previous destination), followed by the "Log run to MLflow" action.
  When unavailable, it shows the reason and a "Configure MLflow" link. The selected destination is
  already visible in the selector; Export omits the extra location text, including `mlruns`. There is
  no registry field: haute logs candidate runs, and registering or promoting a model happens
  outside haute.
  **Model file** writes a copy of the trained model and its feature contract into the project
  and behaves like a file Data Output with a `models/` folder in place of `outputs/`. A
  required "Filename or path" picker (typed or browsed, persisted as `model_export_path`, no
  default) retains the folder browser and selected-path display without instructional prose.
  The resolved destination is shown when it differs from the selected path, so a selected
  full filepath is not repeated. An alert appears when the path's extension does not match
  the model format (which keeps **Save model to file** disabled) or when the destination
  cannot be resolved. Saving
  never silently replaces a file: an existing destination shows the server's message with a
  **Replace existing file** confirmation that retries with overwrite. Success names both
  written project-relative paths; a failure shows the server's detail. Both actions act on the node's last completed training result. Without one they stay visible but
  disabled without a train-first note, except that a remembered result gone from the server is
  reported with a note to train the model again; while a training job for the node runs they
  are disabled beneath a note that export resumes when it completes; and when the training
  configuration has changed since that result, a warning says exports use the last trained
  model. The export fields (`mlflow_destination`, `mlflow_experiment`,
  `model_export_path`) are not part of the training identity: editing them neither marks the
  trained result stale nor requests a new RAM estimate.

Cross-pane signalling marks where work is needed and where work is running. `ModellingConfig`
reports the panes that hold a current Train-guard issue to `NodePanel`, which marks each such tab
with a compact warning indicator whose accessible description is "<Pane> needs attention"; a
report left by another node's editor never marks this node's tabs. While a job runs, the Train
tab shows the active-training indicator instead, so progress is visible from any pane. Target,
Features and Parameters list their own issues in an alert above the pane body, except those the
body shows beside the setting involved (an invalid JSON draft, the GLM conflicts) and the GLM's
missing L1 ratio, which only the Train list names; Split shows its evaluation issues as above,
and Train lists every issue.

**Completed results.** Summary shows final-test metrics first when present. Without a
test set, when a validation fit ran and the final model was refit on all development rows,
so the diagnostics are in-sample, the validation fit's selection metrics (the mean of each
metric) lead instead, labelled "Validation, N rows" for holdout or "Validation (K-fold mean),
N rows" for cross-validation, with the in-sample diagnostics beneath; a run without validation
is unchanged. Then come the selection score and variability, baseline improvement, winning/final parameters,
fit count/elapsed time (the completed job's own, from its terminal status, never a later
run's clock) and a bounded top-trial table. The fit line ends with what the published model
is, by the tuned family's refit policy (`tuningFamilyCapability`, which finds the family as
the backend's `tuning_family` does, from the one round key the final parameters carry): a
round-refitting family's final tree count, "refit with the winning round budget" for a
fixed-budget family (EBM), and "published the winning trial's fit" for a validation-fit
family (t-boost), which is never refit. **Use best as fixed
parameters** asks for confirmation, atomically writes the reported fixed-parameter
projection and disables tuning; an asynchronous result never mutates node config
automatically. In a read-only document it asks nothing and says why, and an answer given after
the document changed or became read-only writes nothing.

**Non-goals.** No GLM structural-model tuning; no multi-objective, parallel,
distributed or nested-CV tuning; no parameter-specific form builder; no run
history/comparison (MLflow is the system
of record); no EDA readouts in the editor (the explore
node owns those); no collapsed-header summaries; no importance-threshold exclusion actions; and no
algorithm switching or in-place algorithm migration.

**Failure and compatibility semantics.** The frontend continuously derives its training-guard
target, objective, evaluation and bounded-tuning issues, plus any invalid draft for the selected
parameter strategy, and shows them as above; a press with any issue sends no request. The backend
remains authoritative for the full configuration and candidate/conditional search-space contract
and still rejects an invalid request if reached. An issue the editor places by kind, such as the
validation and test total or a GLM conflict, is chosen by its issue code, and an estimate failure
by the server's `error_code`; neither is matched by its words.
Cancellation, terminal-race and GPU-VRAM 507 behaviour are unchanged. A
malformed present live-history entry fails at the runtime response boundary exactly like malformed
completed-result history; absent history produces no chart and is never synthesized from
latest-loss polls. Under the
[canonical-only format policy](../README.md#canonical-only-format-policy),
the `modelling.features`/`modelling.mlflow`/`modelling.monotonic` section keys cease to be read or
written; the generic section store and any inert in-memory entries need no migration.

**Regression evidence.** Suites in
`frontend/src/panels/__tests__/ModellingConfig.test.tsx` and under
`frontend/src/panels/modelling/__tests__/` prove: the panes' ownership for the tree families and
the GLM (GLM regularization in Parameters), the panes reported for the tab indicators, the GLM
conflicts shown once beside their settings and the missing L1 ratio kept out of Parameters, and
the unsupported-algorithm diagnostic; the tree families' all-loss picker, loss-derived
task/default metrics, and visible disabled incompatible metrics; the searched, filtered,
dtype-labelled feature rows with per-row checkboxes and search-independent bulk actions;
confirmation-free reversible exclusion with dormant monotonic settings; role/final-selection-aware
monotonic controls; the GLM term and interaction editors against the shared dtype-class and
expression-grammar fixtures; unset-only algorithm selection, read-only selected-algorithm
context, and the absence of an in-place change action; mutually exclusive fixed/tuned Parameters
bodies, each family's starter search space, arbitrary params JSON round trips, valid
fixed/search-space autosave, CatBoost's starter `one_hot_max_size`, t-boost's eight starter
parameters, the tuning note's family-labelled search space and run-summary fit budget, the run
summary's fit budget and categorical encoding, invalid/non-object/reserved-key draft persistence
with an inline alert and no Apply/Revert, and GPU task-type merge; the Complete before training
list with its Go to links and no request from an invalid press; a coded evaluation-preview
failure shown in Split and Train in the server's words and an uncoded one kept out of Split;
the fraction-sum issue picked by its code; authoritative bounded live-history
rendering including truncation; Export-pane ownership of the MLflow fields and both export
actions, their no-model/training/stale notes, the save-model request path default and result
display, export-field edits leaving the trained result current, and a Summary with no export
card; and time-remaining
show/hide/reset behaviour for valid, insufficient, duplicate/stalled, non-monotonic, terminal, and
new-job samples. `frontend/src/panels/__tests__/NodePanel.test.tsx`,
`frontend/src/stores/__tests__/useUIStore.test.ts`, and
`frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx` prove strip gating, per-node memory,
the needs-attention and active indicators' accessibility, and unchanged roving-keyboard
behaviour. Runtime/store suites prove strict live-history parsing, latest-status retention, and
per-job estimator reset.

## Optimiser config panes

The optimiser node panel presents **Data**, **Factors**, **Constraints**, **Solve**, and
**Export** panes in the same shared equal-width pane-tab strip the modelling and Explore editors
use, hosted by the node panel
([frontend-node-editors](../frontend-node-editors/low-level.md#optimiser-config-panes)).
Both modes show every pane. The optimiser is sink-only and emits no output frame,
so like modelling it has no Columns tab: the node panel shows no Config/Columns strip and the pane
strip sits directly beneath the header. One `OPTIMISER_PANES` list drives both the tabs
and the pane bodies. The active pane is remembered per node in the UI store. Switching to online
mode never discards the stored ratebook source or factor columns, which apply again when ratebook
mode is chosen again.
Pane ownership:

- **Data** — the Online/Ratebook mode choice, the Objectives & Constraints input selector with
  its missing/malformed-input alert, the objective ("Column to maximise"), and the Row ID,
  Scenario Index and Scenario Value column mappings. The objective is chosen here because it
  is a column of the input selected directly above it. The input's columns are fetched only
  after the node's preview settles, so every column field shows its saved value from the config
  immediately: a saved column absent from the (not yet known) column list is still offered as
  the selected option, and marked "(not in input)" once the columns are known and lack it.
- **Factors** — in ratebook mode, the Rating Factor Source selector and its combined
  disconnected-source and zero-level warning. Then, in both modes, one **Factors** table styled
  like the modelling Features list: a count (`N ratebook · M of 12 validation`, the ratebook part
  in ratebook mode only), help text saying validation factors only break results down by segment
  and never reach the solver, a **Validation input** select listing only the optimiser's connected
  inputs, whose first option is the Objectives & Constraints input (stored as no
  `analysis_input`), a factor search, and one row per factor with its name, level count (rating
  factors), dtype chip, and two checkboxes: **Ratebook** (writes `factor_columns`) and
  **Validation** (writes `analysis_columns`). In ratebook mode the Rating Factor Source's factors
  lead, followed by the validation frame's other columns (the quote-id column excluded); online
  mode lists the frame's columns. The Ratebook box is enabled only in ratebook mode and only for
  the source's factors; the Validation box only for a column of the validation frame, and not
  for a new choice once twelve are chosen. Switching the validation input removes the chosen
  validation factors the newly selected frame does not have, once its columns are known (a
  frame with the same columns as the last settles the switch, so a later schema change never
  prunes); a
  configured validation factor the frame does not have, or a configured validation input that is
  no longer connected, is flagged in the pane and blocks the solve. Until the validation
  frame's columns are known, the saved validation factors are still listed, ticked, and can be
  unticked.
- **Constraints** — the constraint count heading with **Add**, then a **Result** line ("single
  point", or "frontier over <swept constraints> · N solves", N counting the swept constraints
  only), then one bordered card per constraint, all laid out the same way so each reads as a
  sentence. There is no result-type choice: the solve is a single point unless a constraint is
  swept. A card's first row holds the constraint column selector and the remove action; its
  second row holds the bound type (**at least** / **at most**, stored as `min` / `max`, an
  absolute total; or **at least % of baseline** / **at most % of baseline**, stored as
  `min_pct` / `max_pct`, a fraction of the constraint's baseline total), a
  **Fixed | Sweep** switch, and the value area: one value when Fixed, or the **from** and **to**
  ends of that constraint's frontier range when swept. A percentage bound's value and range are
  shown and typed in percent beside a % sign (`{min_pct: 0.95}` reads 95) and stored as the
  fraction. Switching between at least and at most keeps the value; switching between an
  absolute and a percentage bound starts it afresh, at 100% of baseline or at 0 as a new
  constraint starts, and a swept constraint's range restarts from that value in the same
  update. A constraint that holds none of the four bounds, or several, shows no bound type
  until one is chosen. A swept card shows no fixed value; the
  solve runs a swept constraint at its range's start, and a fixed constraint is held at its value
  at every frontier point. Switching to Sweep writes a `frontier_ranges` entry starting at the
  fixed value; switching back to Fixed removes it, and the saved fixed value applies again. A
  swept card carries its own **Auto range** button beneath its from/to, which fills that
  constraint's range only, merged over the other constraints' current ranges; the running card's
  button reads **Restart auto range**, and a failure shows on the card that asked. One auto-range
  run exists at a time, so starting another card's supersedes it; saving the pipeline while it
  runs leaves it running. Auto range fills absolute totals, so on a percentage card it is
  disabled beside "Auto range sets absolute totals" and the range is typed;
  clearing both ends keeps the constraint swept. With no constraints the pane says the objective
  is maximised alone. When any constraint is swept, a **Frontier** section follows the cards with
  **Points per swept constraint** (`frontier_steps`). The frontier is a sweep over the
  constraint bounds, so its settings live with the constraints rather than in a pane of their
  own. Bound values and sweep range fields commit on blur or Enter, not per keystroke. Clearing a
  bound value restores its stored value on commit, so a constraint is never silently relaxed to
  0; clearing a sweep range field removes that end of the range, which the field and the Solve
  issue list then flag as missing.
- **Solve** — a **Solver settings** section first, holding maximum iterations and tolerance and,
  in ratebook mode, the coordinate-descent iterations and tolerance; then the stale-result banner,
  source-size estimate, **Optimise** action, progress with a **Stop** action, failure card and
  convergence result, so the action follows the settings it runs with. There is no chunk size:
  the solve reads its inputs in chunks of the Pipeline Settings streaming chunk size. There is no
  history toggle: every solve records its convergence history. **Stop** cancels the running solve job (including
  its efficient-frontier phase) through the existing cancel route and records the returned
  terminal state the way modelling's Cancel does. The size estimate is requested only when an
  input that changes it changes (the Objectives & Constraints input, mode, the Row ID / Scenario
  Index / Scenario Value mappings, the Rating Factor Source and factors, the number of
  constraints, the saved **Chunk rows** or **Memory kept free** pipeline setting, the active
  source or the graph structure), never on objective, constraint-bound,
  solver or export edits; an estimate the server cannot size reads "Size unknown". Beneath the
  sizes, "Estimated solve memory X of Y" sets the solve's memory forecast against what a solve
  started now would be given (OPT-W02); when X exceeds Y the line takes the warning colour and an
  alert icon labelled "More than this machine can give", and Optimise stays enabled: a solve is
  never refused on an estimate. While the configuration cannot be solved the Optimise action
  stays disabled and one alert beneath it lists every blocking issue, each with a **Go to** link
  that opens the pane that fixes it: no resolvable Objectives & Constraints input; no objective,
  or an objective or constraint column the input does not have (checked once the input's columns
  are known); a
  mapped Row ID, Scenario Index or Scenario Value column that the input does not have (checked
  once the input's columns are known); a Validation input that is not connected, more than twelve
  validation factors, or a validation factor the validation input does not have (checked once its
  columns are known); in ratebook mode a Rating Factor Source that is not an input name, one no
  longer connected, none connected or chosen (each named as such), or no
  selected factor; a constraint without exactly one bound, or a fixed one without a value; and a
  swept constraint whose range is missing an end or whose minimum is not
  below its maximum, or more than 10,000 frontier solves in total over the swept constraints. Warnings
  that do not block — one scenario per quote, or quotes with differing scenario counts — appear
  under the estimate. Ctrl+Enter inside the optimiser editor starts the solve, or re-runs it when
  the result is stale, whenever the configuration can be solved.
- **Export** — two sections. **Publish** acts on the node's last solve: a target choice between
  the solved result and each frontier point (the same selection the result preview's frontier
  chart shows, so choosing a point on either surface changes both), an output path field
  persisted as `result_export_path` whose placeholder is the derived default
  (`optimiser_<label>_<id>.json`, a bare filename the server places in the pipeline folder's
  `files/models/`), an optional version label, **Save to file** and **Log
  to MLflow**. Saving never silently replaces a file: an existing destination shows the server's
  message with a **Replace existing file** confirmation that retries exactly the refused request with overwrite; the confirmation is withdrawn while the path, target, version label or staleness no longer match it. A successful
  save shows the written project-relative path and a **Use in Apply node** choice listing the
  graph's Apply Optimisation nodes, which points the chosen node's file source at that path. A
  successful log shows the run id and link, as modelling's Export pane does. A ratebook result
  also offers **Download factor tables (CSV)** and states its combined-factor collar,
  "Combined factor collar [min, max] — apply it in your rating engine", from the solve's
  `combined_factor_bounds` (every frontier point shares its solve's collar), printed as the grid
  values the solve scored (`0.8`, never the widened `0.800000011920929`). The CSV repeats the
  exact collar on every row as `combined_factor_min` and `combined_factor_max`, so the file stays one
  rectangular table. A ratebook frontier point whose tables are not loaded yet offers **Load
  factor tables for CSV** instead. A ratebook result without a collar shows an alert asking for a re-run and
  offers no CSV. Before any solve the section explains that there
  is nothing to publish yet; while a solve runs the actions are disabled. When the configuration
  has changed since the solve, a warning says so, the actions read **Save outdated result** and
  **Log outdated result**, and the request records that the result was stale when published.
  Publishing never consumes the result: saving, logging and viewing Quotes can be repeated in
  any order. **MLflow logging** holds the manual-logging explainer, the shared destination
  selector and the experiment path field. The export fields (`mlflow_destination`,
  `mlflow_experiment`, `result_export_path`) are not part of the solve identity: editing them
  never marks the solve stale, re-requests the size estimate, or changes the graph's structural
  fingerprint.

The optimiser config has no collapsible sections: the panes replace the former Advanced and MLflow
disclosures. The Solve tab shows the accessible active indicator while a solve job runs. Unlike
modelling, a pane with a blocking Solve issue shows a compact warning indicator on its tab ("Data
needs attention"), so the reason Optimise is disabled is visible from every pane.

**Result preview.** The optimiser result preview uses the same results workspace as model
validation: a Focus view that fills the viewport and keeps the active tab (Escape returns), a
docked height remembered for the session and results-style tabs. No tab has an introduction:
each opens on its values, and the reading of them is documented. It has no provenance strip: the header already states convergence, iterations and the quote
count, and the point stepper which frontier point is shown. When a diagnostic could not be produced (the adjustment report,
or the efficient frontier), the summary says so in a "Diagnostics Issues" alert naming
each one and why, in the same form as model validation. A failed solve with no earlier result
opens no result preview: there is nothing to show, and its error stays on the Solve pane. Its accent is its own colour,
never the warning colour the stale strip uses. The Frontier pane's chart and the slice's points
table (never wrapping a cell; it scrolls sideways instead) sit side by side at equal widths,
stacking when the workspace is narrow, and the Summary numbers follow them in the same pane. It
offers Frontier when the solve produced one, else Summary, then Rates (ratebook), Adjustments, Segments and Quotes (both modes) and Convergence, then the
pre-solve input's Curves and Statistics (the data preview's per-quote chart and per-scenario
statistics, from the node's preview rows, while they hold an objective and scenario rows) so a
solve never hides them; on Curves the header's point stepper gives way to the quote navigation. It has no
Export tab and no publish actions — publishing belongs only to the Export pane. Clicking a frontier point selects it
as the publish target; clicking the selected point again keeps it selected (the Export pane's
target choice returns to the solved result). Summary shows the
constraint-attainment table for the displayed result (the selected point, else the solve): every
constraint, swept or not, as Constraint | Kind | Bound | Achieved | Slack | Status | λ, e.g.
"min 1,000,000 · achieved 1,012,400 · slack +12,400 (+1.24%) · Met · λ 0.0031". The bound is
the backend's `effective_bounds` for that result, the bound it was actually solved at, so the
two panes cannot disagree. Status is text ("Met" or "Breached") with an icon, colour only a
secondary cue, judged by a strict comparison with no tolerance; the signed slack percentage is
always shown. There is no "binding" judgement. Quotes explores the per-quote chosen
scenarios of the publish target on demand, a page at a time sorted, searched and filtered on the
server over every quote: sortable headers, presets ("Highest adjustment", "Lowest adjustment",
"At range edge", and for ratebook "Deployed ≠ evaluated"), a quote-id search, a filter per
analysis value, the scenario value marked up or down against 1.0 by a glyph and words as well
as colour, and a pager stating "Showing a–b of M matching (of N)"; columns are named in
neutral scenario terms; reopening Quotes for the same target shows the loaded detail again
without a new request, and a new solve loads it afresh. Rates, Adjustments, Segments, Quotes
and the Export pane's factor-table load treat a frontier-point request alike: one the view has
moved past (another point, key, weighting or query, or the view closed) is abandoned silently;
one the server dropped because another point was requested is sent again, never shown as a
failure; a point or result that is no longer available says so with no **Retry**, since only a
new solve helps; any other failure states the server's message with **Retry**. Rates lays out like AvE: factors ranked by rate spread (how far their rates move from 1.0, weighted by quotes) with search, beside the selected factor's rate bars in banding order with an aligned quote-count strip, a focusable detail line per level, and a values table of Level | Rate | vs neutral 1.0 (%) | Quotes | Share; the chosen factor survives tab switches. Summary's ratebook beeswarm fits the pane's width, says when it shows only the top 8 of N factors (with a Top 8 / All toggle), names categorical levels in words as well as colour, and has a values table. Adjustments shows how the optimiser adjusted the book relative to the base price, for the
solved result or the selected frontier point: one bar per scenario value of the solve's grid
(values nobody chose included, as empty bars), labelled by the value, on an axis named
"Scenario value (1.0 = base price)", with a dashed line at 1.0 = base price (no adjustment).
A **Weight by** switch weighs the bars by quote count or by the objective or a constraint at the
chosen scenario (a weighting with a negative value or a zero total is not offered, and the view
says why). Below it: the 5th, 25th, 50th, 75th and 95th percentiles and the mean, the shares
adjusted up, down and unadjusted, and the shares at the scenario range's minimum and maximum; a
grid without 1.0 has no unadjusted share and a note says so. Each bar can be hovered or focused
to read its exact values, and a values table lists them. A selected point's adjustments load
only while the view is open, with a loading state; a failure offers **Retry**, and a point whose
choices are no longer available says so. It describes the solution, not an impact: nothing is
compared with current pricing. For a ratebook result it describes the grid step the solver
evaluated for each quote, and states how many quotes' deployed factor differs from that step
(the within-range rounding the deployed, unsnapped factor keeps). Summary shows the solved
result's shares compactly, with that count for a ratebook result, and a link to the view; with a
frontier point selected Summary shows no adjustments (the point's summary carries no report),
and the point's adjustments are in the Adjustments view.
Segments shows where the optimiser adjusted, for the solved result or the selected point: its
keys (the analysis columns, and a ratebook result's rating factors) in a searchable list ranked by
how differently the optimiser adjusted each key's levels (named above the list, and listed unranked
until the ranking arrives), beside the selected key's mean chosen scenario value per level against
the 1.0 base line, with an aligned quote strip, a detail line for the hovered or focused level and a
values table. A **Weight by** switch weighs the means and shares as Adjustments does; a level
whose weight totals 0 shows "—" for its weighted figures and says why. The key chosen here and
on Rates is the same choice. A key with too many levels says why it cannot be broken down; a
result with no keys says "Tick validation factors in the optimiser's Factors pane".
Selecting a frontier point never removes a view: Convergence stays available and, for a selected point, says "History is recorded for the solved result; frontier point N: converged
(or not converged), K iterations", and the frontier chart's as-solved marker stays at the solve's
position. The Frontier chart fits the pane, names its axes (the objective
column and the x constraint) at 12 px, and has a legend. A multi-constraint sweep is shown one
slice at a time ("Holding <other> at", a percentage constraint's held value reading "95% of
baseline"), drawn as a line in bound order, so it reads as a frontier
rather than a projected cloud; every point keeps its global number wherever it is selected,
stepped or published, and selecting a point elsewhere brings its slice into view. Points that
land on one pixel share one marker, which shows the selected point when it is one of them, so
the marker drawn as selected is the one a click selects. The line joins
only feasible points — converged and meeting every bound, judged by haute in both modes because
a converged ratebook point can still breach a bound; a non-converged point is hollow and a
converged-but-breached one a cross labelled "breached", both still selectable, with its status in
the points table. The as-solved marker is hollow, labelled "As solved (different slice)", when the
solve lies off the displayed slice. There is no point details card: the points table beside the
chart (Point | <x> bound | <x> achieved | objective | Converged | Iterations | Status) highlights
the selected point's row and scrolls it into view, and clicking a row selects its point as a chart
click does; Summary carries the selected point's attainment. While a point is selected the chart
drops its hover detail, and it carries no caption beyond the slice and response-cap facts. Convergence draws the solve's history as small multiples, each on its own real
axis with tick values: the objective; the largest λ change on a log axis (an iteration with no
change, 0, is drawn at the axis floor and a note says how many); each constraint's total with
its bound (the solve's `effective_bounds`) as a dashed line and a marker at the first iteration
that met every constraint; and λ per constraint. A ratebook solve instead shows its
coordinate-descent trace by CD pass, one line per factor: the objective and each constraint
total against its dashed bound (λ per record is in the values table). A ratebook result without a trace says the trace is recorded
by live solves only. The values behind the charts sit in a closed values table ("View iteration
values", or "View coordinate-descent values" for a trace), and a truncated trace says so. Stepping through points keeps the current tab; a new solve job, or switching to another
optimiser node, returns to the default tab. A displayed result's warning (such as the
non-convergence reason) shows as an amber strip. When the node configuration has changed since the result was produced,
the preview shows a strip saying so with a **Re-run** action that starts the solve directly. Re-run applies the Solve pane's blocking rules: while any issue blocks the solve it is disabled and the strip names the first issue. λ is shown
for online and ratebook results alike, labelled "λ (multiplier)" and explained as the solver's
Lagrange multiplier on the constraint's term; it is never read as a tightness claim, because in
this discrete solve a positive λ can sit beside positive slack.

## Model family capabilities

- Algorithm names, tasks, losses, feature controls, refit policy, suffixes and tuning support
  come from `frontend/src/panels/modelling/algorithmCapabilities.json`, which the backend
  generates from its descriptors and checks for drift. The gateway lists exactly the families
  in it, the target pane offers only the selected family's losses, and tuning is offered only
  for families that support it.
- The model type is chosen once, when the node is created, and cannot be changed afterwards;
  a different family is a new node.
- The palette describes the Model Training node by what it trains rather than by engine: "Train
  a model: gradient boosting, t-boost, EBM or GLM", where CatBoost, XGBoost and LightGBM are
  its gradient boosting; the engines are named where the family is chosen. A test maps every family in the
  capability table to its word in that description, so a new family fails it until the
  description names it.
- A classification objective on a target that is not Boolean shows a positive-class field. It is
  required for a text target, and the Train guard refuses a text target without it; it is
  optional for a numeric target ("only if the labels are not 0/1"), whose whole-number value is
  saved as a number. Predictions above 0.5 are labelled with the positive class.
- Every family but the GLM shares the Target, Features and Parameters pane bodies and the JSON
  parameter editor (`usesSharedPanes`). An EBM's Features pane adds a pairwise-interaction
  control: "Let EBM choose" writes a count to `params.interactions`, "Choose pairs" a list of
  feature pairs picked from the included features, with monotone-constrained features
  disabled and a saved pair naming a missing column kept visible. The readiness issues mirror
  the backend's EBM rules (`ebm-max-rounds` on Parameters, `ebm-interactions` on Features).
- An EBM result adds a Terms tab: terms ranked by importance, each shown as a table by default
  with the same Table/Chart switch, axis selectors and one-line labels as the t-boost Tables
  tab. A main effect's table lists its bins (the missing bin first, numeric bins as
  left-closed intervals such as `[25, 40)`) with their scores, and its chart is its shape
  (bars per category, a step line over value bins, the missing-value score stated); an
  interaction is a score table over its two axes, or one line per cell of its column axis.
  All are additive link-scale term scores, never labelled as SHAP. A traced EBM prediction lists one contribution per term, an interaction
  as one row.
- A t-boost node starts with eight parameters: `n_trees` 4000, `max_interaction_order` 3,
  `n_bags` 8, `interaction_gain_hurdle` 2, `prune` true, `prune_main_effects` true,
  `band_tolerance` 0.75 and `graduate` true. All but `prune_main_effects` are t-boost's own
  defaults (its unset `graduate` graduates too), written out so the node shows that bagging,
  pruning, banding and graduation are on; `prune_main_effects` is the one starter value that
  differs from t-boost's default, so pruning may drop main effects as well as interactions.
  Every other setting is t-boost's own recommended recipe. Its starter search space varies
  `learning_rate`, `max_interaction_order` and `lambda_`. Two readiness issues mirror the
  backend's rules on Parameters: `tboost-n-trees` when `n_trees` is not a positive whole
  number, and `tboost-prune-main-effects` when `prune_main_effects` is true with `prune` set
  to false or with a monotonicity constraint in the Features pane.
- A t-boost result adds a Tables tab: the model's rating tables ranked by importance, with the
  base value. Every table shows as a table by default, with a Table/Chart switch. A main
  effect's table lists its cells (level groups, or numeric intervals such as `(25, 40]` after
  the missing cell) with each cell's value and training mass, labelled as weight × exposure;
  its chart is bars per level group or a step line over the numeric intervals with the
  missing cell stated. A table of two or more factors is a heatmap of two chosen axes, the
  longest axis on the rows and the shortest other axis on the columns by default, with a
  selector for each remaining axis; its chart draws the row axis along x and one line per
  column cell. Labels stay on one line and a narrow table scrolls sideways. Under a log link the tab shows
  relativities by default and can switch to link-scale values; other links show link-scale
  values only. Factored effects, which have no dense table, are listed by name and importance.
  A traced t-boost prediction lists one contribution per table, an interaction as one row.
- The response guard treats fit-evidence fields and a tuning report's `final_tree_count` as
  optional, because the backend drops nulls: a GLM's evidence is its threads alone, and a
  fixed-budget (EBM) study refits with the winner's parameters and a validation-fit (t-boost)
  study publishes the winning trial's fit; neither has a tree count.
- A family whose capability `refit_policy` is `validation_fit` (t-boost,
  `publishesValidationFit`) is never refit: its Split pane has no **Refit on training +
  validation** box and no Cross-validation option, choosing a validation method or turning
  on tuning never writes `refit_on_development`, and the readiness issue `validation-fit` (on
  Split) mirrors the backend's refusals. The Train summary's fit budget, and the Parameters
  pane's tuning note in the same words, count its one validation fit, or a study's trials plus
  the winning validation fit, never a final fit.

