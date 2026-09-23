# Modelling — High-Level Specification

## Purpose

The modelling component trains, evaluates, and exports predictive models for insurance
pricing pipelines. It takes a pipeline graph's materialised training data and a
declarative node configuration (target, weight, offset, algorithm, evaluation strategy,
fixed parameters, and optional tuning search space) and produces a fitted model artifact, a train-to-deploy feature
contract, evaluation metrics, and diagnostic chart data. When the result is logged to
MLflow, the logging path also generates and attaches a self-contained HTML model card;
an ordinary training run does not write a model-card file beside the native model. The
component also generates standalone, runnable Python training scripts from the same
configuration, so a pipeline author can hand a data scientist exactly what the "Train"
button ran.

Two algorithm families are supported: CatBoost (gradient-boosted trees) and GLM (via
RustyStats), covering both the "black box, high accuracy" and "interpretable,
regulatory-friendly" ends of the insurance pricing spectrum. Frequency/severity/pure-
premium modelling conventions — exposure weights, offset columns, Tweedie/Poisson/Gamma
losses — are first-class throughout.

## Model validation workspace

Completed training results use a dedicated, readable validation workspace within
the existing preview shell. It opens at a 420px docked height, remembers the user's
resized height during the application session, and offers a labelled Focus view
that fills the viewport. Entering or leaving Focus view preserves the active pane
and its selections; Escape returns to the docked workspace and restores focus.
Other preview types retain their existing sizing and controls.

The navigation uses sentence-case, content-sized tabs. Every pane keeps the
diagnostics partition (final test or development) and its row count visible;
development diagnostics are explicitly described as not held-out performance.
Charts measure their available width instead of forcing horizontal scrolling,
use readable labels, and expose full values without relying on tiny or truncated
axis text. Numeric formatting and actual/expected colours are consistent across
the workspace. Related charts stack when their container is narrow.

- Summary leads with final-test performance when available. Diagnostic metrics
  retain their partition labels. Model/evaluation facts stay visible; fit details,
  candidate-selection evidence and tuning details are disclosed on demand. No
  metric or diagnostic failure is silently removed or relabelled as held-out.
- Coefficients offer searchable terms, keyboard-operable sorting, a sticky header,
  and an optional estimate/95% Wald interval view only when inference is valid.
  Unavailable inference retains its explicit explanation and missing statistics.
- Lift uses a chart-first layout with the raw table under a disclosure. Lift and
  Lorenz may be compared together when the workspace is wide; a narrow workspace
  has a labelled view switch. A result containing only Lorenz data still renders.
  Deciles identify their prediction ordering, and full values/counts are available.
- Residual distribution and actual/predicted scatter use aligned responsive
  layouts, numeric residual ticks and explicit reference lines. Existing weighting,
  sampling disclosure and diagnostic statistics are preserved.
- Feature importance offers search and Top 20/All controls, explains each available
  importance method, and distinguishes signed values spatially around zero.
- AvE and PDP share the selected feature and feature search while switching panes.
  A selection unavailable in a pane shows a clear unavailable message rather than
  silently selecting another feature. A new node or training result resets this
  state. The feature picker moves above the chart in narrow containers.
- AvE displays actual and expected together with a separate, aligned exposure
  strip. Unordered categories use unconnected marks; numeric bins retain their
  ordered lines. Full bin labels, values and exposure are available in a hover/
  focus detail and a disclosed table.
- PDP uses the feature name on its axis, numeric curves and horizontal categorical
  displays with readable category labels. Missing levels and per-feature errors
  retain their explicit existing representations. No uncertainty or exposure is
  invented when the result does not provide it.

## Scope

In scope:
- Algorithm abstraction and implementations (CatBoost, GLM/RustyStats).
- Unified development/validation/final-test evaluation planning (random, temporal,
  and group strategies).
- Bounded deterministic CatBoost hyperparameter tuning over the persisted
  development-only validation plan.
- Metric and diagnostic computation (Gini, deviances, double lift, AvE, residuals,
  Lorenz curve, partial dependence, SHAP, GLM coefficients/relativities/fit statistics).
- The train-to-deploy feature contract (schema pinning + hash verification).
- MLflow experiment logging, including a `ModelSignature` built from the same contract.
- Self-contained HTML model card generation with embedded SVG charts.
- Standalone training-script codegen, guaranteed to train the same model as a live run.
- Profile-likelihood estimation of a GLM dispersion parameter (Negative Binomial
  `theta`, Tweedie `var_power`) as an on-demand background job, so a user can resolve a
  value RustyStats itself does not estimate before starting a real training run.
- HTTP routes for starting/polling/cancelling training, RAM/VRAM estimation, MLflow
  logging of a completed job, script export, model-cache clearing, and GLM dispersion
  estimation.

Out of scope, owned elsewhere:
- Executing a generated script through `haute train` — see
  [cli](../cli/high-level.md); this component owns the generated `TrainingJob` source,
  not the command-line runner.
- Interactive/exploratory GLM model development — **Atelier**, a separate standalone GLM
  workbench, is where a user iteratively builds and curve-fits a GLM's terms/interactions.
  Haute only trains from the finished dict-spec config (the same `terms`/`interactions` JSON
  Atelier exports); it has no interactive curve-fitting or data-exploration tooling of its
  own, by design — see [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).
- Scoring a trained model against new data at serve time — see
  [mlflow-model-registry](../mlflow-model-registry/high-level.md).
- The MLflow destinations/settings/test-connection HTTP surface (including
  persistence of the `[mlflow]` inventory table of `haute.toml`) — see
  [mlflow-model-registry](../mlflow-model-registry/high-level.md); this component
  owns the destination inventory, the per-key resolver, the local-folder default for a
  node that names no destination, and the resolution helpers those endpoints and the
  logging path share.
- Pipeline graph compilation and lazy execution — see
  [execution-engine](../execution-engine/high-level.md).
- Background job storage, lifecycle state machine, and cancellation plumbing — see
  [background-jobs](../background-jobs/high-level.md).
- The training/optimiser configuration and results UI — see
  [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).

The HTTP training implementation is split behind the stable `TrainService` surface:
preparation, evaluation/dispersion rules, spawn-picklable worker protocol, and atomic
artifact publication are stateless/cohesive domain modules, while one lifecycle module owns
the job store, cancellation registry, supervisor, cleanup, and terminal transitions. The
compatibility facade and route own no duplicate state or worker implementation.

## Behaviour

- A user configures a "modelling" node: target column, optional weight/offset columns,
  columns to exclude (or an explicit feature list), algorithm (`catboost` or `glm`),
  task, evaluation configuration, requested metrics, and algorithm-specific parameters
  (CatBoost hyperparameters, or GLM terms/family/link/regularization/solver/interactions).
  CatBoost hyperparameters live in the node's `params` object and its Tweedie power is
  `variance_power`; GLM settings live at the node's top level and its Tweedie power is
  `var_power`. `exclude`, `feature_columns`, `monotone_constraints`, and `feature_weights`
  apply only to CatBoost: a GLM's features are its terms and interaction factors, and GLM
  monotonicity lives on each term.
- Starting training (`POST /api/modelling/train`) performs the cheap graph/config
  validation synchronously, creates and registers the cancellable job, starts an owned
  preparation thread, and returns `status="started"` plus the job ID before RAM
  estimation or upstream materialisation begins. The preparation thread estimates
  memory requirements and then supervises a single hard-capped worker process that
  executes the upstream pipeline, derives the exact feature choice from the
  materialised schema, and writes the training data — so no training frame is ever
  materialised in the server process. Because that worker runs under a real kernel
  memory cap, a boundary Haute cannot size ahead of time runs conservatively there
  instead of being refused. A failed preparation leaves no partial training file. Materialisation
  consumes the execution facade's typed strategy result and carries its deterministic
  inclusion/exclusion provenance into the modelling status/result; modelling does not
  select a competing plan. It then runs fit, evaluation, diagnostics, and model staging
  in a supervised spawn child through a versioned plain-data protocol. Progress writes
  are non-blocking:
  a full queue or the delivered-event budget drops progress rather than stalling fit,
  reports the loss count on the next event/end marker, and retains only bounded history.
  The response includes a bounded, versioned diagnostic describing the
  feature choice and why other columns were retained as metadata or excluded.
  A configuration that leaves no feature columns is rejected with HTTP 422 before a sink
  or trainer runs.
  The training job store has
  one process-wide running slot shared by training and GLM dispersion estimation; a
  second request of either kind is rejected while the first is running.
- The client polls for status (`GET /api/modelling/train/status/{job_id}`), receiving
  preparation and fit progress, an incrementally-growing loss/iteration history, and —
  on completion — the full result: metrics, feature importances, and every diagnostic
  chart's underlying data. Terminal preparation failures retain their public
  `error_code`, `http_status_code`, and structured `error_detail` on this status
  response. Polling also enforces the configured/default training timeout: an overdue
  running job requests preparation/child termination and atomically transitions to
  `timed_out`.
- `POST /api/modelling/estimate` returns a RAM/row-limit and (for GPU CatBoost) VRAM
  estimate without starting a job. Both this estimate and the pre-training RAM check
  use fresh shared node snapshots that cover the training column demand: cached
  outputs supply measured row counts and schema even when their original computation
  cannot be analysed statically. Estimating RAM alone never builds missing snapshots
  or runs upstream code; without usable cache evidence, the existing analytical
  estimate (including its unavailable outcome) applies.
  Once the relevant modelling and evaluation fields
  are valid, it also returns a bounded preview of the exact evaluation plan: effective
  development/final-test rows, validation-fit count and row bounds, plus group counts
  or date ranges when applicable. A failure raised while that bounded preview executes
  is the user's to fix and is answered as HTTP 422 `Evaluation preview failed: <reason>`,
  whether it is data-dependent (an all-null target, an empty partition), a graph-shape
  or schema failure (a broken node contract, a column no source supplies, an invalid
  config or parse, any Polars planning or collection failure raised by the pipeline's own
  code and data), or a bounded-mode refusal; the endpoint never surfaces one as a 500.
- `POST /api/modelling/export` returns a standalone Python script that trains the
  identical model the "Train" button would, using the same config → kwargs builder as
  live training.
- `POST /api/modelling/mlflow/log` logs an already-completed job's results to MLflow
  after the fact (the "Log to MLflow" button), reusing the persisted feature contract
  so the logged model's signature matches what was actually trained. Haute never
  registers or promotes a trained model: a logged run is a candidate, and registering it
  (for example after comparing it with the current champion and moving an alias) is an
  external process. Model Score consumes the result through a registered version or
  alias, or scores a logged run directly.
- `POST /api/modelling/save` writes a copy of an already-completed job's trained model and
  its feature contract to a file in the project (the Export pane's "Save model to file"
  button), so a model can be kept without MLflow. Its destination rules match a file Data
  Output with `models/` in place of `outputs/`, and it refuses to replace an existing file
  unless the request confirms overwrite. `POST /api/modelling/save/destination` resolves the
  same destination for display without writing. Neither retrains, reads node config, or
  touches the training output being copied.
- `POST /api/modelling/train/cancel/{job_id}` is idempotent. If cancellation wins the
  terminal race, it marks the run cancelled and trips the same token used by upstream
  preparation and the spawned fit worker; if another terminal transition won first,
  it returns that existing terminal job unchanged.
- `POST /api/modelling/dispersion/estimate` estimates a GLM node's Negative Binomial
  `theta` or Tweedie `var_power` by profile likelihood over the node's own training
  data, as a background job the client polls
  (`GET /api/modelling/dispersion/status/{job_id}`) and can cancel
  (`POST /api/modelling/dispersion/cancel/{job_id}`); after that explicit estimate
  action resolves, the UI writes the value into the visible editable node-config
  field. The user can inspect or adjust that auto-filled value before their normal
  save/publish action. Its process supervisor enforces the timeout stamped at job
  creation; status polling is not required to trigger that timeout.
- CatBoost monotonicity is the one additional capability lever exposed in
  modelling-node configuration. `monotone_constraints` maps configured numeric feature
  names to exactly `-1` (decreasing) or `1` (increasing); zero means absence and is
  omitted by the editor. Entries for features made dormant by `exclude` remain stored:
  the shared config builder omits them from live training and script export, so re-including
  the feature restores its prior direction. The established explicit `feature_columns` contract
  still wins over a stale exclusion. After the final CatBoost feature selection is known,
  training rejects a non-object mapping, malformed names or
  directions, active constraints on absent/non-selected features, and constraints on
  features whose dtype is not `Int64` or `Float64` before splitting
  or fitting.

Invariants that always hold:
- Live training and script export always produce the same model for the same config —
  both go through one shared config→kwargs builder (`_train_config.build_training_job_kwargs`).
- The training objective must be fully specified before a job starts or a script is
  exported: an unset loss/family, Tweedie variance power, Negative Binomial `theta`, GLM
  terms, elastic-net L1 ratio, or cross-validation folds, selection rule, or seed is
  rejected with an actionable message rather than silently defaulting.
- A classification task never trains against a continuous target, and classification
  metrics are never computed against one. Once training data is materialised, the
  target column's values are checked against the task and the effective metric set —
  in the train route before the fit worker is dispatched, and again inside
  `TrainingJob` itself so the CLI and exported-script paths share the same gate. A
  float or decimal target with fractional values (or a target whose type cannot serve
  as class labels) under `task="classification"` is rejected with a message naming
  the target column and task and directing the user to choose a discrete target or
  switch the task to regression, instead of failing later inside a metrics library
  with a context-free error. The gate keys on the effective metric set as well
  (explicit config metrics, or the objective-implied defaults — the same derivation
  the job builder uses): a fractional target whose effective metrics include AUC or
  log loss is rejected pre-dispatch with the metrics named and the escape hatch stated.
  The legitimate continuous-proportion binomial fit remains reachable by setting
  the reported metrics explicitly to regression metrics, which removes every
  classification metric from the effective set. On this metric-keyed branch, non-float
  target types defer to the fit's own validation. Non-finite float values are deliberately
  excluded from the fractional scan: NaN is treated as missing (null-target handling
  and the metric stage's non-finite filtering own it), so an all-NaN or
  infinite-valued target passes the gate and fails downstream inside the wrapped
  metric stage with its own bounded message.
- Every trained model is saved together with a feature contract pinning its exact
  feature order, dtypes, categorical domains, target, and offset column. Any drift
  detected later (train vs. score) raises rather than producing a plausible-looking
  wrong prediction.
- MLflow signatures preserve temporal inputs deliberately: Polars `Date` and
  every supported parameterised `Datetime` unit/time-zone form map to MLflow
  `datetime`, survive signature persistence, and accept the corresponding
  pandas frame produced by the scoring path after log/load. Polars `Decimal`
  has no exact MLflow 3.x scalar type and is therefore rejected at signature
  construction with an actionable instruction to cast upstream to `String`
  (precision-preserving text) or explicitly to `Float64` (accepting precision
  loss). It is never silently mapped to `double`.
- The public modelling config has exactly one versioned `evaluation` object.
  Legacy public `split` and `cross_validation` objects are rejected under the
  [canonical-only format policy](../README.md#canonical-only-format-policy).
- A final-test source position is never visible to a validation fit or tuning trial.
  Selection fits use only development rows, the selected configuration is refitted
  once on all development rows, and the final test is evaluated once after selection.
- Reported final-model diagnostics come from the final test when one exists and
  otherwise from the complete development/training data. Validation diagnostics are
  not presented as final-model diagnostics.
- A model trained with an offset column always has its offset effect included in
  reported predictions and diagnostics — an offset-absent prediction path is refused,
  never silently computed at baseline zero. The offset is a strictly positive exposure
  multiplier under a log link (a log-link GLM, CatBoost `Poisson` or `Tweedie`) and an
  additive term otherwise, for both algorithms.
- Optional diagnostics (SHAP, partial dependence, GLM inference statistics) can fail
  independently without aborting the run; failures are recorded and surfaced, not
  swallowed.
- Training never silently proceeds with an empty feature set. Explicit features,
  all-except selection, and GLM terms produce the same version-1 feature-selection
  diagnostic shape in start/status results, including deterministic capped lists of
  selected features, retained metadata, and exclusions.
- Numeric-only CatBoost input keeps Polars' native Fortran-contiguous `Float32`
  matrix unless a repeatable handoff benchmark shows at least a 20% median
  end-to-end `Pool` construction improvement without adding a full-matrix peak
  allocation. The benchmark also has to prove identical feature order, values,
  equivalent labels within CatBoost's `Float32` ingestion precision, seeded
  predictions within `1e-12` absolute/relative tolerance, and the same prediction
  dtype. A timing-only win cannot justify doubling the live feature-matrix
  allocation at the training boundary.

### Unified evaluation and bounded tuning

Every modelling node supplies one strict version-1 `evaluation` object. Random and
group evaluation specify `schema_version` (1), `strategy` (`random` or `group`), optional
`group_column` (for `strategy="group"`), `seed`, optional `test` object (e.g. `size`), and a
`validation` object (e.g. `{"method": "cross_validation", "fold_count": 5}`).
Random/group single validation uses
`{"method": "single", "size": <source-relative fraction>}` and no validation uses
`{"method": "none"}`. `test` is optional. Fractions are finite numbers in `[0, 1)`;
Boolean numbers are invalid, and integer allocation must leave every requested
partition and every final development-training set non-empty.

Temporal evaluation uses a required `date_column`, an optional
`test={"start": <ISO date/datetime>}`, and exactly one of:

- `validation={"method": "single", "start": <ISO date/datetime>}`;
- `validation={"method": "cross_validation", "fold_count": 2..10,
  "window": "expanding"}`;
- `validation={"method": "none"}`.

Temporal boundaries retain equal dates as one unit. A single-validation boundary
precedes the final-test boundary and all resulting intervals are non-empty.
Expanding-window CV divides the ordered distinct development dates into an initial
training block and the requested validation blocks; every training date is strictly
earlier than its validation dates. Rolling windows, embargoes and relative period
boundaries are not accepted.

Random classification evaluation is stratified by target. Preflight rejects a plan
when any requested test/validation partition or fold cannot contain every class and
reports the class counts and required minimum. Regression remains unstratified. Group
evaluation canonicalises group keys, keeps each group in exactly one partition, and
uses a deterministic seeded row-count-balancing assignment. It fails when any
requested partition/fold would be empty.

After null-target filtering, planning writes and strictly reloads one canonical
digest-linked `{model}.evaluation-plan.json` for the exact prepared parquet. The artifact
contains the source digest, exact source positions, development/final-test
membership, ordered validation-fit train/validation memberships, canonical strategy
configuration, row counts, and bounded group/date summaries.

Planning assigns the final test first. Every validation fit is then derived solely
from development positions. Single validation has one selection fit; K-fold
validation has K; no validation has zero. An ordinary run performs those selection
fits followed by exactly one deployable final fit on all development rows.
Selection fits use an evaluation-only execution path: they materialise their
partition, fit the algorithm, compute every configured metric and retain row counts
and best iteration, but do not save a model or feature contract, run
SHAP/PDP/full diagnostics, write MLflow, or publish per-fit artifacts.

Validation-fit results are persisted in canonical order and aggregated from the
reloaded artifact using validation-row-weighted metric means plus population standard
deviation, minimum, maximum, fit count and total validation rows. Only the final fit
emits deployable-model loss history and expensive diagnostics. The final fit evaluates
the final test once when present; otherwise diagnostics are explicitly labelled as
development/training diagnostics.

For fixed CatBoost parameters with validation, the final fit uses the validation-row-
weighted median of each fit's best iteration plus one (one fit simply uses its best
iteration plus one), capped by the configured iteration ceiling. Validation-only
early-stopping controls are removed from the final fit, which trains on all development
rows without an evaluation set. Supported CatBoost iteration aliases are replaced by
the selected `iterations` value. With no validation, it retains the configured iteration
count. The selected final tree count is carried in the completed training result so
later MLflow export logs the parameters used for that model; older results without the
field retain their existing logging behavior.

Holdout validation offers a checked-by-default `Refit on training + validation`
option. Unchecking it publishes the one model trained on the training partition
with validation used for selection and, for CatBoost, early stopping. The
validation model is saved with its diagnostics and optional final-test metrics;
there is no second fit. The run reports one total fit and labels diagnostics as
validation when there is no final test. This option is unavailable for
cross-validation, no-validation, and parameter tuning, which require their
existing final fit. Older configurations default to refitting.

The completed response exposes one `evaluation` report containing selection metrics
and ordered validation fits, final-test metrics when present, development/test counts,
the exact fit count, plan digest/path, result/report artifact paths, and group/date
summaries. It never labels a selection metric as final-test performance.

CatBoost nodes may additionally supply one strict version-1 `tuning` object specifying
`schema_version` (1), `trial_count`, `seed`, `metric`, and a `search_space` mapping
parameter names to candidate lists or conditional choices. Absence preserves ordinary
training. GLM tuning and tuning with `validation.method="none"` are invalid. `trial_count`
includes baseline trial zero, defaults to 20, and is an exact integer from 5 through 50.
The search space has one through thirty-two non-empty names. Each unconditional name maps
directly to a list of two through fifty canonically distinct finite JSON candidate values.
Haute passes the selected value to CatBoost without inferring a numeric range, integer/float
sampling mode, logarithmic scale, or step. A conditional entry instead uses the exact
shape `{"choices": [...], "when": {...}}`; its candidate list obeys the same bounds.
Optional `when` conditions reference sampled or fixed parameters, contain non-empty
canonical choice sets, and form an acyclic, possible dependency graph.

Sampled values override only same-named fixed parameters. Fixed parameter JSON otherwise
remains unrestricted and unchanged. The implementation uses the pinned Optuna 4.x seeded
TPE sampler through sequential ask/tell only. Every trial reuses the exact persisted
development-only validation plan. Trial zero is the current fixed configuration and is
labelled `baseline`. Exactly one configured finite metric selects the winner: Gini, AUC
and R² maximise; RMSE, MAE, MSE, log loss, Poisson deviance and Tweedie deviance minimise.
Ties select the lower trial index. Every trial retains every configured metric and the
existing validation-row-weighted aggregate. All trial fits run sequentially under the
run's one admission lease and cancellation token, and models/pools are released between fits.

For the winning trial, the final tree count is the deterministic
validation-row-weighted median of `best_iteration + 1`, capped by fixed
`iterations`. The final fit merges the winning sampled values into the untouched
fixed object, uses that explicit tree count, removes validation-only early-stop
controls, trains on all development rows, and evaluates the final test once.

The run persists canonical `{model}.tuning-plan.json`, `{model}.tuning-trials.json`, and
`{model}.tuning-report.json` artifacts recording configs/digests, sampler/version/seed,
ordered trials and fits, objectives, winner/baseline comparison, exact final
parameters/tree count and fit bounds. Evaluation, tuning, model and feature-contract
artifacts are one staged transactional publication set. Failure, cancellation, a
lost terminal race, malformed content or response/artifact mismatch publishes none.
Trial evidence stores `elapsed_seconds=0.0` deliberately so canonical artifact bytes
do not depend on machine timing; the completed job owns the real total elapsed time,
which the Summary surface displays.
MLflow receives one final run with the selected final parameters, final-test metrics
when present, selection and baseline/winner tuning summaries, and all
evaluation/tuning artifacts.

Live tuning progress is monotonic over planning, trial/fold fits, final fit and
publication and exposes phase, one-based trial/fold indices and counts,
completed/total fits, and best objective so far. Only the final fit contributes model
loss history. Live training and exported scripts use the same config builder and
produce equivalent evaluation plans, fit bounds and result artifacts.

## Design rationale

The single config→kwargs builder (`_train_config.py`) exists because live training and
script export used to build `TrainingJob` arguments independently, which produced two
concrete silent-wrongness bugs in the codebase's history: GLM-only keys (including
`offset`) leaking into CatBoost's constructor params (which has no `**kwargs`, so it
crashed at fit time), and an exported GLM script silently dropping the top-level
terms/family/link/regularization config and training a plain Gaussian all-features
model instead. Both are permanently closed off by making the builder the only path.

"Loud, actionable failure over silent fallback" is applied deliberately to the training
objective: an unset Tweedie variance power, Negative Binomial `theta`, GLM terms,
or elastic-net L1 ratio would otherwise fall through to a library default (variance power
1.5, theta 1.0, an intercept-only design, pure ridge) that produces a real, trainable,
plausible-looking model — just not the one the user intended. Cross-validation uses a
fixed seed of 42 when older configurations have none, avoiding unseeded folds without
requiring a user-facing seed control.
`training_objective_issue` gates this identically at config-build time and at the
route's upfront validation, so the two paths cannot drift apart on what counts as
"complete."

The same posture extends to the target/task/metric pairing and to the worker error
boundary. A continuous target under a classification task used to train all the way
to the metric stage and surface sklearn's bare "continuous format is not supported" —
no target column, no task, no fix — so `_target_check.training_target_task_issue`
gates the pairing with the objects the user can act on (target column, task,
metrics), at both the route and `TrainingJob` layers. The gate keys on the EFFECTIVE
metric set (explicit config metrics or the objective-implied defaults from
`effective_metrics`), not the declared task alone: a binomial family or
Logloss/CrossEntropy loss under `task="regression"` implies AUC/log loss by default,
and those metrics are undefined on a continuous target, so that run is rejected
pre-dispatch too rather than dying later at the metric stage. A binomial fit on a
continuous proportion target stays legitimate and reachable — setting the reported
metrics explicitly to regression metrics empties the effective set of classification
metrics and the gate stands aside, which the rejection message itself points out —
qualified to objectives that accept a continuous target (a binomial GLM family; a
CatBoost Logloss/CrossEntropy loss never reaches this branch, since
`resolve_loss_function` rejects it under a regression task at config time). And
because the fit runs in a spawn child, message
quality has to survive the process boundary: the child stamps every curated failure
message on the failure payload's `user_message` field, and the parent supervisor
surfaces that wording verbatim instead of re-wrapping it in worker jargon. This is the
inverse twin of the sanitise-by-default posture: sanitisation strips detail that would
leak (paths, secrets, raw stderr); the user-message contract adds detail that informs
(the user-model objects involved and a call to action). Both are properties of the
same error-surface chokepoints.

RustyStats does not estimate either GLM dispersion parameter it accepts as a fit
argument — an unset Negative Binomial `theta` makes it refuse to fit, an unset Tweedie
`var_power` silently fits at 1.5 — so neither can be safely defaulted and both are
gated by `training_objective_issue`. Because a user still needs *some* principled way to
choose a value, `estimate_glm_dispersion` (`_rustystats.py`) offers a profile-likelihood
search as an explicit, on-demand action: it holds every other part of the design fixed
(the same terms/interactions/weight/offset the config already specifies, resolved via
the same `prepare_glm_design` helper `GLMAlgorithm.fit` uses, so the profiled design is
never allowed to drift from what training would actually fit) and maximises the fitted
model's log-likelihood over the single dispersion parameter with a bounded 1-D search
(`scipy.optimize.minimize_scalar`, ~20-30 IRLS fits; `theta` is searched in log-space
since it is scale-like). The estimate is an explicit user action; when it resolves, the
client auto-fills the visible editable config field so the user can inspect or adjust it
before their normal save/publish action. This preserves the "no hidden defaults"
invariant while avoiding a second accept control for a value the user just requested.

The feature contract is a separate artifact (rather than relying on the model file's
own metadata) because CatBoost and RustyStats models predict correctly only when fed
features in the exact trained order/dtype/categorical domain, and a library-level
mismatch surfaces as a confusing internal error. The contract is content-hashed so a
hand-edited or corrupted file is caught, and it is written per-model
(`{model_name}.feature_contract.json`) after a prior shared-file design let two models
trained into the same output directory silently overwrite each other's contract.
The contract retains Polars' full parameterised `Datetime(...)` descriptor; the
MLflow signature boundary classifies that descriptor structurally rather than
requiring one spelling per unit/time zone. Decimal remains representable in a
local feature contract, but attempting to publish that contract as an MLflow
signature fails before model logging because MLflow cannot express it exactly.

The CatBoost numeric handoff is benchmark-gated because Polars currently exposes a
Fortran-contiguous `Float32` NumPy matrix for a multi-column numeric frame while
CatBoost accepts both Fortran- and C-contiguous matrices. Normalising that matrix
unconditionally with `numpy.ascontiguousarray` is not a free layout hint: it creates
another rows-by-features allocation at the point where training memory is already
highest. The opt-in performance workload therefore measures the complete alternative
(`ascontiguousarray` plus `Pool`) against the production handoff (`Pool` directly),
records the source matrix and copy byte counts, and trains the same seeded model from
both pools to establish result equivalence. The durable decision follows the
20%-and-no-extra-allocation gate above; local timing evidence is diagnostic rather
than a machine-specific production switch.

### Training configuration experience

Training configuration uses Target, Features, Parameters, Split, Train and Export
panes for both algorithms. GLM regularization and solver controls belong in
Parameters; distribution, link and dispersion remain in Target. Configuration pane
tabs use the shared node-tab typography, spacing, equal-width layout and node accent.
Target and Split content uses open sections with consistent headings and field
spacing. Split settings and allocation sections do not use enclosing
cards; separation comes from whitespace. Field borders and validation feedback retain
their normal styling.
Field labels use sentence
case, readable field/help text and the modelling accent consistently. Target, weight
and offset selectors are searchable and show column types. Feature selection uses
compact table rows with inclusion checkboxes, coloured dtype labels using the shared
type palette, All/Included/Excluded filters, and numeric-only ↓ / − / ↑ monotonicity
buttons. The buttons retain their decreasing/neutral/increasing colours and selected
states; target/weight/offset roles remain excluded from predictors.

The row-limit control appears first in Split for both algorithms, above allocation.
An empty value uses all rows; editing it preserves the existing `row_limit` setting
and training-memory estimate behavior. Train no longer contains the control.
The Split pane uses declarative section headings: Split strategy, Validation strategy
and Test set. User-facing split names are Training set, Validation set and Test set;
the single-split method is Holdout validation. Strategy choices are Random split,
Group split and Time-based split. Its reproducibility seed is an internal setting,
not an editable control. Random and group splits always show Test set (%), defaulting
to 0 when no test set is configured; 0 removes the test set. Existing configured
percentages are preserved. Time-based splits use an optional Test starts date, with
an empty date meaning no test set. There is no test-set checkbox.
The pane displays source-relative percentages. Holdout
validation at 20% with a 20% test set shows 60% training, 20% validation and 20% test.
Allocation appears once, at the top, without a separate exact-evaluation section or
instructional paragraphs about model selection and refitting. Exact row counts
supersede target proportions when available. Cross-validation depicts rotating
training folds, or expanding temporal windows, rather than a permanent validation
holdout; concise per-fit row ranges appear alongside the allocation. Temporal
allocation uses exact counts once available. No-validation runs do
not suggest that a selection fit occurs. Combined validation/test fractions must leave
positive training data and are rejected before training in both frontend and backend.
Train, tuning notices and result displays use the same vocabulary. Results distinguish
held-out test metrics from training diagnostics on the data used for the final refit.
API fields, stored split configuration, allocation logic and evaluation behavior keep
their existing contracts; this vocabulary change is presentational.

Displayed CatBoost parameters represent persisted configuration. New CatBoost nodes
explicitly store the recommended defaults when the algorithm is selected; existing
empty or partial parameter maps keep omitted values library-managed. Fixed parameters
use one always-visible JSON editor, preserving arbitrary parameter keys and the Train
pane's GPU setting. Invalid JSON remains editable and is reported in
Parameters as well as Train. CatBoost Tweedie power must be finite and strictly between
1 and 2; its controls and backend validation enforce those bounds.

Tuning must not silently add or alter a final-test partition. The Parameters pane
explains whether one is reserved and links to Split, and displays the number of
selection fits plus the final fit. Config issues appear in the relevant pane and on
its tab; Train provides links to resolve them and a summary of target, feature count,
evaluation, compute settings and fit budget before submission. With fixed parameters, the
fit budget labels holdout/CV runs as validation fits and the development refit as the final
fit; tuning labels its trial runs as tuning fits. With no validation, it shows only the
final fit. Evaluation preview
failures retain their actionable detail and are distinguished from unavailable memory
estimates; neither state promises that training will succeed.

The MOD-M09 product decision keeps monotonicity because both supported algorithms
already have deterministic named-feature semantics and it is meaningful in pricing
review. It does not add warm start (incompatible with isolated-child and atomic
artifact ownership), class-imbalance controls (classification-only with no shared GLM
meaning), arbitrary extra metric/passthrough editors (algorithm-specific validation
would be bypassed), or feature-weight UI (RustyStats does not support it). Those are
not hidden defaults or dormant controls; each would need a separate product contract
and evidence before it can be exposed. Existing CatBoost `params` and the declared
metric list remain their current advanced/configuration contracts.

Diagnostics are computed by reading the chosen evaluation partition exactly once and
reusing it for every chart — training data is often multi-GB, so re-reading per
diagnostic was a real memory cost, not a theoretical one. The same memory discipline
(`gc.collect()`, `_malloc_trim()`, temp-parquet ownership tracking with an abort-safety
cleanup net) runs throughout the pipeline, and an admission/RAM-estimation system gates
whether a job is even allowed to start.

Post-fit progress names the diagnostic currently running. SHAP summary and
loss-based feature importance are separate stages: the SHAP message must end
before the full-partition loss-importance calculation starts. Algorithms without
SHAP never announce that stage. This presentation change preserves diagnostic
values, sampling, and training parameters.

Optional diagnostics occupy a deliberate middle ground: neither "abort the whole run if
SHAP fails" nor "silently drop it and say nothing." Each optional block is wrapped so a
failure is recorded in `TrainResult.diagnostics_errors` with the failing diagnostic
name and exception type, and training still completes. GLM inference statistics
(coefficient standard errors, z-values, p-values) are never fabricated: a past bug
rendered placeholder statistics (SE=0.0, p=1.0) as if real. RustyStats 0.9 marks
inference invalid after penalties, selection, monotonicity constraints, and smoothing,
so those fits publish their coefficients with null statistics and a one-sentence
`glm_inference.reason`; a non-finite statistic under valid inference is reported as a
near-singular design, and a non-finite coefficient or relativity fails that diagnostic
by name rather than reaching the finite-JSON guard.

The HTML model card renders charts as inline SVG with zero external dependencies,
specifically so the artifact is a single file a pricing reviewer can open in any
browser without a server or JS bundle.

## Interactions

- Depends on the [execution-engine](../execution-engine/high-level.md) to compile and
  lazily execute the upstream pipeline graph into the training DataFrame/parquet
  (`execute_lazy_graph`, `ExecutionContext`, RAM admission and cancellation tokens).
- Depends on [background-jobs](../background-jobs/high-level.md) for the job store,
  lifecycle state machine, and cancellable-job registry that `TrainService` wraps.
- Produces the artifacts that [mlflow-model-registry](../mlflow-model-registry/high-level.md)
  / the scoring path consume at deploy/score time: the native model file, the feature
  contract, and (optionally) the MLflow-logged model with its attached `ModelSignature`.
- Serves [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md),
  which calls every route in this component and renders the `TrainResult`/diagnostics
  payload, including the chart data the model card also renders server-side.

## Failure model

- Configuration errors (no target, unknown algorithm, incomplete training objective,
  invalid GLM family/link combination) are rejected before any pipeline execution or
  job record is created, as HTTP 400 with a message naming the exact missing/invalid
  setting.
- An admission failure discovered before a job handle can be returned surfaces as HTTP
  507. RAM or GPU-VRAM failure discovered during background preparation transitions the
  pollable job to `memory_limited` and preserves the equivalent structured 507 detail
  on its status response. A GPU job that would not fit is refused outright: the message
  asks the user to select CPU (or reduce the workload) and retry, and the server never
  silently changes `task_type` or retries on CPU.
- Pipeline-execution failures while materialising training data preserve the equivalent
  HTTP classification (`http_status_code` 422 for missing required columns or
  bounded-streaming unsupported; 500 for a generic failure) on the terminal status,
  and the job transitions to `contract_error`/`error` accordingly.
- A target column whose values cannot serve the configured task or effective metric
  set (a continuous target under classification, or under AUC/log-loss metrics
  implied by a classification-flavoured objective with `task="regression"`) is
  rejected after materialisation but before the fit worker is dispatched: the job
  transitions to `contract_error` with a message naming the target column, task, and
  (on the metric-keyed branch) the metrics, directing the user to choose a discrete
  target, switch the task, or set regression metrics explicitly.
- Failures that cross the training/dispersion worker boundary surface the child's
  wording, not worker jargon — but only when that wording is deliberately curated.
  The child's failure mapper marks haute-authored messages (the gates, the metric
  wrap, `HauteValidationError` validation messages, and the friendly-error shapes
  — including an
  unexpected-error fallback that names the target/objective and the exception
  type but not the third-party message body, and a memory-limit message giving
  used/allowed sizes with a call to action) on the payload's `user_message`
  field, and the parent supervisor surfaces that message verbatim as the job's
  terminal message — the internal "Isolated worker raised {type}: …" wrapper
  text is never shown for those failures. An arbitrary third-party exception's
  text is never vouched for as a terminal message: the fallback wraps it in a
  haute-authored system-error shape (still a plain `error`, never relabelled as
  `contract_error`) and keeps the raw text in diagnostic fields. Failures that
  never produce a payload (a child crash, a parent-side timeout) keep the
  parent-authored wrapper surface, whose crash and timeout wordings are
  themselves written for the user (a hedged may-have-run-out-of-memory phrasing
  when the exit code looks memory-limited — the heuristic is indicative, not
  proof — vs. an unexpected-stop phrasing, with the exit code when available;
  a stopped-after-its-time-limit phrasing naming the limit for timeouts). The
  field is a routing contract, not a per-message content guarantee: message
  quality is enforced at the producing sites (the gates and wraps in this
  component, pinned by their tests). Error types and bounded tracebacks stay in
  diagnostic fields; the curated messages carry domain context (target column,
  task, metrics) and never secrets, raw tracebacks, or filesystem paths — a
  missing-file or save failure names the failure class and the errno-derived
  OS reason, keeping the path itself diagnostic. The validation channel's
  provenance is enforced by a marker type: haute's own validation sites raise
  `HauteValidationError` (a `ValueError` subclass, so every existing handler
  still catches it), and only that type is promoted verbatim — a dependency's
  plain `ValueError` (including a pydantic `ValidationError`) takes the
  type-only fallback as a plain `error`, closing the #159 design's
  dependency-`ValueError` residual.
- A mandatory metric-evaluation failure names the evaluation set (using the
  evaluation plan's public `development`/`final test` labels on that pipeline),
  target column, task, and requested metrics around the underlying library error,
  with the instruction to fix the target/task/metric pairing — a bare library
  message cannot reach the UI from the metric stage. With the pre-dispatch gate
  keyed on the effective metric set, this wrap is the residual net for the
  target/metric mismatches the gate's one fractional-values scan cannot see (e.g.
  binary AUC over a multi-class integer target) and for every other metric-stage
  failure. It covers every mandatory metric site: the evaluation plan's validation fits (the first metrics
  computed on the live route), the final fit's diagnostics-partition metrics, and
  the separate validation re-read. It catches the failure classes pure metric
  computation produces (`ValueError`, `TypeError`, arithmetic errors — never
  `MemoryError`, which keeps its memory taxonomy) and deliberately trades
  terminal-reason precision for context: a wrapped failure surfaces as
  `contract_error` with the domain objects named and the original chained, even
  when the underlying cause was an internal bug.
- Once a background training run has started, every terminal outcome (`completed`,
  `cancelled`, `timed_out`, `memory_limited`, `contract_error`, `error`) is reflected
  both in the job's status and, for HTTP-raised failures, in the response — the two are
  kept in sync by construction rather than by convention.
- A feature-contract mismatch (train vs. score, or a hand-edited/corrupted contract
  file) raises `FeatureMismatchError` naming the specific field and its expected vs.
  actual value.
- A prediction request missing a required offset column raises rather than silently
  scoring without the offset's effect.
- If every row supplied to a metric or diagnostic is non-finite, computation raises
  rather than returning an empty or NaN result that could be mistaken for a valid
  (if poor) evaluation.
- A non-finite value (NaN/Inf) surviving anywhere in a training result is caught before
  the result is published to the job store or returned over HTTP, turning what would be
  an invalid JSON response into an explicit `error` job.
- MLflow logging is best-effort where it can be: a model-card generation failure inside
  `log_experiment` is caught and warned, never failing an otherwise-successful
  experiment log.
- Dispersion-estimation requests are validated up front (unknown parameter, non-GLM
  node, wrong family for the requested parameter, invalid family/link combination, no
  target column, or an otherwise-incomplete training objective) as HTTP 400 before any
  pipeline execution starts. Once running, a failed candidate fit is absorbed inside the
  search (treated as `-inf` log-likelihood, not a hard error); only a search where *no*
  candidate converges raises, surfacing as the job's `contract_error`/`error` terminal
  state exactly like a training job's equivalent failure classes.

- A child receives only plain configuration and paths, never the route's job store,
  execution context, callbacks, dataframes, or cancellation registry. The parent remains
  authoritative for cancellation, timeout, admission ownership, status, and public
  error mapping.
- A canvas training run's model becomes available only after the parent validates staged
  size/digest evidence for the complete set in the job's own server-owned artifact directory.
Every run publishes the model, feature contract, and three evaluation JSON
artifacts as one set; a tuned run adds its three tuning JSON artifacts. Each job owns its
directory, so a later run never replaces an earlier job's files: exports read exactly the
bytes their job trained. Completing a newer run for the same node releases the older job's
directory (unless an export holds it), job eviction and restart reaping remove the rest, and an
export of a job whose artifacts are gone fails with `410` rather than reading other bytes.
Cancellation, crash, malformed result, or validation failure removes the directory. Dispersion
  publishes bounded scalar metadata and no artifact.
- Unknown evaluation versions or fields, legacy public `split`/`cross_validation`,
  malformed strategy keys, inexact/Boolean fold counts, non-finite fractions, and structurally
  invalid validation/test objects fail during cheap config validation before a job is created.
- Evaluation planning rejects group leakage, temporal ties split across partitions, and invalid
  temporal ordering before persistence. The strict plan loader rejects unknown/missing fields,
  source mismatches, duplicate/out-of-range or non-canonical positions, overlap, empty
  requested partitions, count/summary disagreement, non-partitioning ordinary CV, and a
  non-expanding temporal CV sequence; training also compares the reloaded plan with the
  generated plan before fitting.
- Search-space validation for tuning rejects orchestration-owned keys, including objectives/losses,
  device/resource selection, callbacks, write directories, random seed, and `iterations`; the fixed
  `iterations` value is the upper ceiling.
- Preflight enforces hard bounds on tuning fit counts: `trial_fit_count = trial_count * validation_fit_count <= 200`
  (with `total_fit_count = trial_fit_count + 1`). A candidate-fit error aborts with the trial index,
  sampled parameters and original actionable exception; it is never skipped.

## Model families

- **Descriptors.** Each algorithm (`catboost`, `glm`) has a capability descriptor in
  `src/haute/modelling/_descriptors.py`: its tasks and Haute losses with the native objective
  and link each translates to, its raw-`params` policy, its refit round key, its feature
  controls, and its artifact suffix. Configuration building, `TrainingJob`, tuning, the refit,
  model-file suffixes, and the modelling UI read the descriptors. An unknown algorithm, a task
  the family does not support, or a loss outside its list fails before data is materialised.
- **Losses.** `loss_function` with `variance_power` is the loss setting for every tree family;
  the Haute vocabulary is `RMSE`, `MAE`, `Poisson`, `Gamma`, `Tweedie`, `Logloss`, and
  `CrossEntropy`. CatBoost supports all of them except `Gamma` (CatBoost 1.2.10 has no Gamma
  loss). The GLM configures `family`/`link` instead and trains regression only: a binomial GLM
  predicts a probability, not a class label.
- **Parameters.** CatBoost raw `params` are forwarded unchanged apart from `thread_count`,
  which the thread allotment owns, and `class_names`, which would reorder the probability
  columns away from the positive = 1 encoding; tuning search spaces still exclude CatBoost's
  orchestration-owned keys, and a CatBoost fit whose classes are not the encoded `[0, 1]`
  fails. The GLM keeps its own configuration-key validation. A family with
  an allowlist rejects reserved keys, aliases, duplicate spellings, and unknown keys.
- **Refits.** A round-refitting family feeds zero-based best iterations to
  `validation_weighted_tree_count`, writes the count to its descriptor's round key, and drops
  every other spelling of the round count and every validation-only early-stopping key.
- **Threads.** Each job resolves one thread allotment from `HAUTE_TRAINING_THREADS` (default:
  the logical CPU count, matching CatBoost's own default) and passes it to CatBoost as
  `thread_count`. RustyStats exposes no thread setting.
- **Fit evidence.** The training response and the MLflow candidate record the final fit's
  thread allotment, round ceiling, fitted rounds read from the model, and stopping reason
  (`none`, `validation`, or `native_exhaustion`).
- **Binary classification.** A classification job trains only on exactly two target classes.
  Boolean and 0/1 targets make `True`/`1` positive; any other pair of labels needs an explicit
  `positive_class`. The job trains on the target encoded as positive = 1, records the
  `(negative, positive)` labels in the feature contract and in the CatBoost model metadata, and
  every scoring path derives the label from the positive-class probability: positive exactly
  when it is greater than 0.5, so 0.5 is the negative class. A CatBoost classifier trained
  outside Haute uses its own class order. Single-class, multiclass, and non-integer numeric
  targets fail before fitting.
- **Model identity.** A version-2 feature contract carries an optional model identity:
  algorithm, Haute loss or GLM family, link, variance power, class labels, native feature names,
  and exact engine and Haute versions, all inside the hashed payload. Training always writes
  it; a contract supplied for a generic MLflow model may omit it. A version-1 contract fails to
  load with a retrain message. The shared MLflow pyfunc and Model Score check a loaded model
  against the identity (its model type, and CatBoost's recorded loss) and fail on a mismatch.
  Only the schema fields are compared against live data.

## Approved change contract — XGBoost family

- **Current limitation.** XGBoost cannot be selected for training; the sandbox allowlist names its
  scikit-learn wrapper classes only for externally supplied pickles.
- **Unresolved target.** An `xgboost` algorithm trains a native CPU `hist` booster from a
  `DMatrix` built with contract-derived categorical codes, sample weights and, for regression
  offsets, a `base_margin` of the transformed offset at both fit and predict. Early stopping on
  Haute's validation partition selects `best_iteration` (zero-based) and the saved `.ubj` model is
  trimmed to `best_iteration + 1` rounds. Contributions come from native `pred_contribs`, whose
  bias column carries the offset. The [MOD-F00 engine probes](../roadmap/mod-f00-engine-probes.md) record the native behaviour this relies on: a model
  trained with `base_margin` ignores its fitted `base_score`, category codes are positional, and
  unseen categories and unknown parameters do not fail natively.
- **Non-goals.** GPU training, DART, custom objectives, and the scikit-learn wrapper are out of
  scope.
- **Failure and compatibility semantics.** A model trained with an offset and scored without one,
  an unseen category, or an unknown or reserved parameter fails in Haute before the native call.
- **Acceptance evidence.** Real tiny weighted Poisson, Gamma, Tweedie, squared-error,
  absolute-error and binary fits; scoring parity across evaluation, save/reload (bit-identical),
  Model Score, script, MLflow and deployment; contribution sums reproducing the margin within the
  named bound; a reordered-category scoring frame giving identical predictions through the
  contract.
- **Roadmap package.** [MOD-F02](../roadmap/modelling.md#mod-f02--deliver-the-complete-xgboost-slice).

## Approved change contract — LightGBM family

- **Current limitation.** LightGBM cannot be selected for training.
- **Unresolved target.** A `lightgbm` algorithm trains a native CPU booster from a `Dataset` with
  contract-derived categories, weights and an `init_score` of the transformed offset. Early
  stopping selects `best_iteration` (a one-based count) and the saved `.lgbm` model text holds
  exactly that many trees. Because native prediction ignores `init_score`, the scoring adapter
  adds the offset to the raw score before the inverse link, exactly once. Contributions come from
  native `pred_contrib`, which excludes the offset. A fit that stops because no split satisfies
  its constraints records `native_exhaustion`. The `lightgbm` dependency is added with this slice.
- **Non-goals.** GPU backends, native leaf refitting, and model continuation are out of scope.
- **Failure and compatibility semantics.** Conflicting parameter aliases, which LightGBM accepts
  silently, fail in Haute; unseen categories, which LightGBM scores silently, fail in Haute.
- **Acceptance evidence.** Real tiny fits for every supported loss; offset included exactly once
  through a reloaded model; a constant-feature fixture recording `native_exhaustion`; an alias
  conflict failure; scoring parity across every path.
- **Roadmap package.** [MOD-F03](../roadmap/modelling.md#mod-f03--decide-on-and-deliver-the-complete-lightgbm-slice).

## Approved change contract — EBM family

- **Current limitation.** InterpretML Explainable Boosting Machines cannot be selected for
  training, and the restricted loader blocks their classes.
- **Unresolved target.** An `ebm` algorithm trains an InterpretML regressor or classifier, with
  selection fits on Haute's training partition only and the final development refit on the
  development rows (never final-test rows), with explicit nominal/continuous feature types, sample weights,
  a regression `init_score` of the transformed offset at fit and predict, `outer_bags=1`,
  `n_jobs=1`, early stopping disabled, and an explicit `max_rounds` that tuning may search and the
  refit reuses. Native `best_iteration_` is recorded as term-update steps and never converted into
  a budget. Explanations are the native term scores plus intercept, with pairwise interactions
  kept as one term. The model is saved as a `.ebm` joblib file. The `interpret-core` dependency is
  added with this slice.
- **Non-goals.** EBM early stopping is not offered: the [MOD-F00 engine probes](../roadmap/mod-f00-engine-probes.md) show that passing validation rows
  through `bags` changes the intercept and every prediction for all five objectives even with
  stopping disabled, so validation targets would leak into the model. EBM editing, differential
  privacy, and bag-to-bag variation displays are out of scope.
- **Failure and compatibility semantics.** A configuration without an explicit `max_rounds`, or
  requesting EBM early stopping, internal validation or more than one outer bag, fails before
  fitting.
- **Acceptance evidence.** Numeric/mixed regression and binary fits round-tripping through the
  restricted loader; an `interpret-core` version mismatch failing at load; intercept, terms and
  offset reconstructing served predictions; selection fits receiving only training-partition
  rows; the final development refit receiving development rows, reusing the winning
  `max_rounds`, and never receiving final-test rows.
- **Roadmap package.** [MOD-F04](../roadmap/modelling.md#mod-f04--deliver-the-complete-ebm-slice-and-its-term-representation).
