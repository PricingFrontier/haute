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
- Pipeline graph compilation and lazy execution — see
  [execution-engine](../execution-engine/high-level.md).
- Background job storage, lifecycle state machine, and cancellation plumbing — see
  [background-jobs](../background-jobs/high-level.md).
- The training/optimiser configuration and results UI — see
  [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).

## Behaviour

- A user configures a "modelling" node: target column, optional weight/offset columns,
  columns to exclude (or an explicit feature list), algorithm (`catboost` or `glm`),
  task, evaluation configuration, requested metrics, and algorithm-specific parameters
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
  estimate without starting a job. Once the relevant modelling and evaluation fields
  are valid, it also returns a bounded preview of the exact evaluation plan: effective
  development/final-test rows, validation-fit count and row bounds, plus group counts
  or date ranges when applicable.
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
  modelling-node configuration. `monotone_constraints` maps configured numeric feature
  names to exactly `-1` (decreasing) or `1` (increasing); zero means absence and is
  omitted by the editor. Entries for features made dormant by `exclude` remain stored:
  the shared config builder omits them from live training and script export, so re-including
  the feature restores its prior direction. The established explicit `feature_columns` contract
  still wins over a stale exclusion. After the final CatBoost feature selection or GLM-term
  narrowing is known, training rejects a non-object mapping, malformed names or
  directions, active constraints on absent/non-selected features, and constraints on
  categorical, Boolean, temporal, or otherwise non-numeric features before splitting
  or fitting.

Invariants that always hold:
- Live training and script export always produce the same model for the same config —
  both go through one shared config→kwargs builder (`_train_config.build_training_job_kwargs`).
- The training objective must be fully specified before a job starts or a script is
  exported: an unset loss/family, Tweedie variance power, Negative Binomial `theta`, GLM
  factor set, or elastic-net L1 ratio is rejected with an actionable message rather than
  silently defaulting.
- A classification task never trains against a continuous target, and classification
  metrics are never computed against one. Once training data is materialised, the
  target column's values are checked against the task and the effective metric set —
  in the train route before the fit worker is dispatched, and again inside
  `TrainingJob` itself so the CLI and exported-script paths share the same gate. A
  float or decimal target with fractional values (or a target whose type cannot serve
  as class labels) under `task="classification"` is rejected with a message naming
  the target column and task and directing the user to choose a discrete target or
  switch the task to regression, instead of failing later inside a metrics library
  with a context-free error. The gate originally keyed on the configured task only —
  a classification-flavoured objective under a regression task (e.g. a binomial GLM
  defaulting to AUC/log-loss metrics) was left to the metric-stage context wrap,
  because a binomial target may legitimately be a continuous proportion. But that run
  still dies once AUC/log loss are computed, only later and with less context, so the
  gate now keys on the effective metric set as well: a fractional target whose
  effective metrics (explicit config metrics, or the objective-implied defaults —
  the same derivation the job builder uses) include AUC/log loss is rejected
  pre-dispatch with the metrics named and the escape hatch stated. The legitimate
  continuous-proportion binomial fit remains reachable by setting the reported
  metrics explicitly to regression metrics, which removes every classification
  metric from the effective set. On this metric-keyed branch, non-float target types
  defer to the fit's own validation. Non-finite float values are deliberately
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
  prerelease canonical-only format policy.
- A final-test source position is never visible to a validation fit or tuning trial.
  Selection fits use only development rows, the selected configuration is refitted
  once on all development rows, and the final test is evaluated once after selection.
- Reported final-model diagnostics come from the final test when one exists and
  otherwise from the complete development/training data. Validation diagnostics are
  not presented as final-model diagnostics.
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
- A model becomes visible at its configured final path only after the parent validates
  staged size/digest evidence and publishes the model plus per-model feature contract.
Every run publishes the model, feature contract, and three evaluation JSON
artifacts as one set; a tuned run adds its three tuning JSON artifacts to the
same transaction. Replacing a tuned model with an ordinary run removes the
prior tuning companions inside that rollback-capable transaction, so stale
selection evidence can never appear to describe the newly deployed model.
Cancellation, crash, malformed result, or pre-commit publication failure preserves the
prior set and removes prepared/staged files. A post-commit backup or staging cleanup
error is logged without relabelling the already durable model as failed. Dispersion
  publishes bounded scalar metadata and no artifact.

## Unified evaluation and bounded tuning

### Canonical evaluation configuration

Every modelling node supplies one strict version-1 `evaluation` object. Random and
group evaluation use the following shape (with `group_column` present only for
`strategy="group"`):

```json
{
  "schema_version": 1,
  "strategy": "group",
  "group_column": "policyholder_id",
  "seed": 42,
  "test": {"size": 0.2},
  "validation": {"method": "cross_validation", "fold_count": 5}
}
```

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

Unknown versions or fields, legacy public `split`/`cross_validation`, malformed
strategy keys, inexact/Boolean fold counts, non-finite fractions, and structurally
invalid validation/test objects fail during cheap config validation before a job is
created.

### Evaluation plan, fits, and results

After null-target filtering, planning writes and strictly reloads one canonical
digest-linked `evaluation_plan.json` for the exact prepared parquet. The artifact
contains the source digest, exact source positions, development/final-test
membership, ordered validation-fit train/validation memberships, canonical strategy
configuration, row counts, and bounded group/date summaries.

Planning rejects group leakage, temporal ties split across partitions, and invalid
temporal ordering before persistence. The strict loader rejects unknown/missing fields,
source mismatches, duplicate/out-of-range or non-canonical positions, overlap, empty
requested partitions, count/summary disagreement, non-partitioning ordinary CV, and a
non-expanding temporal CV sequence; training also compares the reloaded plan with the
generated plan before fitting.

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

The completed response exposes one `evaluation` report containing selection metrics
and ordered validation fits, final-test metrics when present, development/test counts,
the exact fit count, plan digest/path, result/report artifact paths, and group/date
summaries. It never labels a selection metric as final-test performance.

### Bounded deterministic CatBoost tuning

CatBoost nodes may additionally supply one strict version-1 `tuning` object:

```json
{
  "schema_version": 1,
  "trial_count": 20,
  "seed": 42,
  "metric": "gini",
  "search_space": {
    "depth": [4, 6, 8, 10],
    "learning_rate": [0.01, 0.03, 0.05, 0.1, 0.2],
    "grow_policy": ["SymmetricTree", "Depthwise"],
    "min_data_in_leaf": {
      "choices": [10, 25, 50, 100],
      "when": {"grow_policy": ["Depthwise"]}
    }
  }
}
```

Absence preserves ordinary training. GLM tuning and tuning with
`validation.method="none"` are invalid. `trial_count` includes baseline trial zero,
defaults to 20, and is an exact integer from 5 through 50. The search space has one
through thirty-two non-empty names. Each unconditional name maps directly to a list
of two through fifty canonically distinct finite JSON candidate values. Haute passes
the selected value to CatBoost without inferring a numeric range, integer/float
sampling mode, logarithmic scale, or step. A conditional entry instead uses the exact
shape `{"choices": [...], "when": {...}}`; its candidate list obeys the same bounds.
Optional `when` conditions reference sampled or fixed parameters, contain non-empty
canonical choice sets, and form an acyclic, possible dependency graph.

Sampled values override only same-named fixed parameters. Search-space validation
rejects orchestration-owned keys, including objectives/losses, device/resource
selection, callbacks, write directories, random seed, and `iterations`; the fixed
`iterations` value is the upper ceiling. Fixed parameter JSON otherwise remains
unrestricted and unchanged.

The implementation uses the pinned Optuna 4.x seeded TPE sampler through sequential
ask/tell only. Every trial reuses the exact persisted development-only validation
plan. Trial zero is the current fixed configuration and is labelled `baseline`.
Exactly one configured finite metric selects the winner: Gini, AUC and R² maximise;
RMSE, MAE, MSE, log loss, Poisson deviance and Tweedie deviance minimise. Ties select
the lower trial index. Every trial retains every configured metric and the existing
validation-row-weighted aggregate.

The hard preflight bounds are:

```text
trial_fit_count = trial_count * validation_fit_count <= 200
total_fit_count = trial_fit_count + 1
```

All trial fits run sequentially under the run's one admission lease and cancellation
token, and models/pools are released between fits. A candidate-fit error aborts with
the trial index, sampled parameters and original actionable exception; it is never
skipped.

For the winning trial, the final tree count is the deterministic
validation-row-weighted median of `best_iteration + 1`, capped by fixed
`iterations`. The final fit merges the winning sampled values into the untouched
fixed object, uses that explicit tree count, removes validation-only early-stop
controls, trains on all development rows, and evaluates the final test once.

The run persists canonical `tuning_plan.json`, `tuning_trials.json`, and
`tuning_report.json` artifacts recording configs/digests, sampler/version/seed,
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

### Regression evidence

Focused evaluation tests prove strict canonical parsing, deterministic plans, random
stratification/failure counts, final-test exclusion, group row balancing/non-leakage,
temporal boundary/tie/expanding-window ordering, fit counts, summaries, digest
linkage, and artifact round trips. Training tests prove evaluation-only selection
fits, one deployable final fit, exact metric aggregation, final-test-once behaviour,
single-child sequential execution, cancellation checkpoints, and cleanup.

Tuning tests prove static search-space validation, seeded ordered sampling,
conditional resolution, merge preservation, baseline participation, metric direction
and tie-breaking, weighted final tree count, exact fit bounds/invocations, candidate
failure visibility, reused plan digest, progress monotonicity, and strict artifact
round trips. Worker/service/route tests retain one admission lease, terminal-race
ownership, transactional publication/rollback and release on every terminal path.
Backend/frontend runtime guards and export tests prove the same canonical objects and
labels end to end.
