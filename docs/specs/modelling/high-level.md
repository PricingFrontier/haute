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
- Starting training (`POST /api/modelling/train`) validates the config, estimates
  memory requirements, executes the upstream pipeline to materialise training data,
  and derives the exact feature choice from the materialised schema in the request
  process. It then runs fit, evaluation, diagnostics, and model staging in a supervised
  spawn child. The response includes a bounded, versioned diagnostic describing the
  feature choice and why other columns were retained as metadata or excluded.
  A configuration that leaves no feature columns is rejected with HTTP 422 before a sink
  or trainer runs.
  The training job store has
  one process-wide running slot shared by training and GLM dispersion estimation; a
  second request of either kind is rejected while the first is running.
- The client polls for status (`GET /api/modelling/train/status/{job_id}`), receiving
  progress, an incrementally-growing loss/iteration history, and — on completion — the
  full result: metrics, feature importances, and every diagnostic chart's underlying
  data. Polling also enforces the configured/default training timeout: an overdue
  running job requests child termination and atomically transitions to `timed_out`.
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
- `POST /api/modelling/train/cancel/{job_id}` marks an in-flight run cancelled and asks
  its supervisor to terminate and join the child process.
- `POST /api/modelling/dispersion/estimate` estimates a GLM node's Negative Binomial
  `theta` or Tweedie `var_power` by profile likelihood over the node's own training
  data, as a background job the client polls
  (`GET /api/modelling/dispersion/status/{job_id}`) and can cancel
  (`POST /api/modelling/dispersion/cancel/{job_id}`); the resolved value is returned
  for the user to accept into the node config, never written there automatically. Its
  process supervisor enforces the timeout stamped at job creation; status polling is
  not required to trigger that timeout.

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
since it is scale-like). The resulting value is returned to the client to review and
accept — it is never written into the node config automatically, preserving the
"no hidden defaults" invariant the gate itself enforces.

The feature contract is a separate artifact (rather than relying on the model file's
own metadata) because CatBoost and RustyStats models predict correctly only when fed
features in the exact trained order/dtype/categorical domain, and a library-level
mismatch surfaces as a confusing internal error. The contract is content-hashed so a
hand-edited or corrupted file is caught, and it is written per-model
(`{model_name}.feature_contract.json`) after a prior shared-file design let two models
trained into the same output directory silently overwrite each other's contract.

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
- Insufficient RAM or GPU VRAM surfaces as HTTP 507 with a structured payload; a GPU
  job that would not fit is refused outright, not silently retried on CPU.
- Pipeline-execution failures while materialising training data surface as HTTP 422
  (missing required columns, bounded-streaming unsupported) or HTTP 500 (generic
  failure), and the job record transitions to `contract_error`/`error` accordingly.
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

## Polars backend contracts (0.6.0)

Every pipeline materialisation, including initial eager previews, will use the universal
execution-plan facade. Remaining modelling improvement work is tracked in the
[modelling roadmap](../../roadmap/modelling.md).
Its final feature include/exclude decision and deterministic provenance diagnostics
accompany modelling validation and execution. Modelling does not reimplement planning
or infer a competing feature set.

## Approved change contract — 0.8.0 isolated fit and dispersion

Training request validation, RAM estimation, graph execution, projection, and bounded Parquet
materialisation keep their current synchronous HTTP behavior. Once that prepared artifact
exists, fit/evaluation/diagnostics/model staging and GLM dispersion profiling execute in a
spawn child through the shared worker protocol.

The child receives plain configuration and paths, never the route's `JobStore`,
`ExecutionContext`, callbacks, dataframes, or cancellation registry. Progress and iteration
events reconstruct the existing status response in the parent; they are non-blocking,
loss-accounted telemetry, so a slow observer or a training run beyond the delivered-event budget
cannot stall or fail fitting. History remains capped. A model is visible at its configured final
path only after the parent verifies its staged size/digest and publishes the model and per-model
feature contract. Cancellation, timeout, memory-limit, child crash, malformed result, and
pre-commit publication failure preserve truthful terminal state and remove prepared/staged
files. A post-commit backup or staging cleanup error is logged without misreporting the already
published model as failed. The existing response DTOs and immediate pre-launch validation errors
do not change.
