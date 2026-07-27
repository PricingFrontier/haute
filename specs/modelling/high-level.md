# Modelling — High-Level Specification

## Purpose

The modelling component trains, evaluates, and exports predictive models for insurance
pricing pipelines. It takes a pipeline graph's materialised training data and a
declarative node configuration (target, weight, offset, algorithm, split strategy,
hyperparameters) and produces a fitted model artifact, a train-to-deploy feature
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

## Scope

In scope:
- Algorithm abstraction and implementations (CatBoost, GLM/RustyStats).
- Train/validation/holdout splitting (random, temporal, group strategies).
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
- Pipeline graph compilation and lazy execution — see
  [execution-engine](../execution-engine/high-level.md).
- Background job storage, lifecycle state machine, and cancellation plumbing — see
  [background-jobs](../background-jobs/high-level.md).
- The training/optimiser configuration and results UI — see
  [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).

## Behaviour

- A user configures a "modelling" node: target column, optional weight/offset columns,
  columns to exclude (or an explicit feature list), algorithm (`catboost` or `glm`),
  task, split configuration, requested metrics, and algorithm-specific parameters
  (CatBoost hyperparameters, or GLM terms/family/link/regularization/interactions).
  CatBoost hyperparameters live in the node's `params` object and its Tweedie power is
  `variance_power`; GLM settings live at the node's top level and its Tweedie power is
  `var_power`.
- Starting training (`POST /api/modelling/train`) performs the cheap graph/config
  validation synchronously, creates and registers the cancellable job, starts an owned
  preparation thread, and returns `status="started"` plus the job ID before RAM
  estimation or upstream materialisation begins. The preparation thread estimates
  memory requirements, executes the upstream pipeline to materialise training data,
  and derives the exact feature choice from the materialised schema. Materialisation
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
  estimate without starting a job, so the UI can warn the user before they commit.
- `POST /api/modelling/export` returns a standalone Python script that trains the
  identical model the "Train" button would, using the same config → kwargs builder as
  live training.
- `POST /api/modelling/mlflow/log` logs an already-completed job's results to MLflow
  after the fact (the "Log to MLflow" button), reusing the persisted feature contract
  so the logged model's signature matches what was actually trained. Databricks
  registry publication uses the logged `runs:/…/model` URI and is best-effort:
  a registry error is logged without discarding the successful run.
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
- Monotonicity is the one additional cross-algorithm capability lever exposed in
  modelling-node configuration. `monotone_constraints` maps selected numeric feature
  names to exactly `-1` (decreasing) or `1` (increasing); zero means absence and is
  omitted by the editor. After the final CatBoost feature selection or GLM-term
  narrowing is known, training rejects a non-object mapping, malformed names or
  directions, constraints on absent/non-selected features, and constraints on
  categorical, Boolean, temporal, or otherwise non-numeric features before splitting
  or fitting.

Invariants that always hold:
- Live training and script export always produce the same model for the same config —
  both go through one shared config→kwargs builder (`_train_config.build_training_job_kwargs`).
- The training objective must be fully specified before a job starts or a script is
  exported: an unset loss/family, Tweedie variance power, Negative Binomial `theta`, GLM
  factor set, or elastic-net L1 ratio is rejected with an actionable message rather than
  silently defaulting.
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
- Reported diagnostics always come from the most held-out partition available: holdout
  if present, else validation, else train.
- A model trained with an offset column always has its offset effect included in
  reported predictions and diagnostics — an offset-absent prediction path is refused,
  never silently computed at baseline zero.
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

## Design rationale

The single config→kwargs builder (`_train_config.py`) exists because live training and
script export used to build `TrainingJob` arguments independently, which produced two
concrete silent-wrongness bugs in the codebase's history: GLM-only keys (including
`offset`) leaking into CatBoost's constructor params (which has no `**kwargs`, so it
crashed at fit time), and an exported GLM script silently dropping the top-level
terms/family/link/regularization config and training a plain Gaussian all-features
model instead. Both are permanently closed off by making the builder the only path.

"Loud, actionable failure over silent fallback" is applied deliberately to the training
objective: an unset Tweedie variance power, Negative Binomial `theta`, GLM factor set,
or elastic-net L1 ratio would otherwise fall through to a library default (variance
power 1.5, theta 1.0, auto-terms over every column, pure ridge) that produces a real,
trainable, plausible-looking model — just not the one the user intended.
`training_objective_issue` gates this identically at config-build time and at the
route's upfront validation, so the two paths cannot drift apart on what counts as
"complete."

RustyStats does not estimate either GLM dispersion parameter it accepts as a fit
argument — an unset Negative Binomial `theta` silently fits at 1.0, an unset Tweedie
`var_power` silently fits at 1.5 — so neither can be safely defaulted and both are
gated by `training_objective_issue`. Because a user still needs *some* principled way to
choose a value, `estimate_glm_dispersion` (`_rustystats.py`) offers a profile-likelihood
search as an explicit, on-demand action: it holds every other part of the design fixed
(the same terms/interactions/weight/offset the config already specifies, resolved via
the same `_resolve_glm_terms` helper `GLMAlgorithm.fit` uses, so the profiled design is
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

Optional diagnostics occupy a deliberate middle ground: neither "abort the whole run if
SHAP fails" nor "silently drop it and say nothing." Each optional block is wrapped so a
failure is recorded in `TrainResult.diagnostics_errors` with the failing diagnostic
name and exception type, and training still completes. GLM inference statistics
(coefficient standard errors, z-values, p-values) are the one exception treated as
harder-fail-loud than most: a past bug rendered fabricated placeholder statistics
(SE=0.0, p=1.0) as if real, inventing statistical significance, so the current code
raises `GLMInferenceUnavailableError` and omits the coefficient table entirely rather
than emit partial or fabricated rows.

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
- A model becomes visible at its configured final path only after the parent validates
  staged size/digest evidence and publishes the model plus per-model feature contract.
  Cancellation, crash, malformed result, or pre-commit publication failure preserves the
  prior pair and removes prepared/staged files. A post-commit backup or staging cleanup
  error is logged without relabelling the already durable model as failed. Dispersion
  publishes bounded scalar metadata and no artifact.
