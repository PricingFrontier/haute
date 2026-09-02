# Modelling — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/modelling/__init__.py` | Public API surface: `FitResult`, `MLflowLogResult`, `TrainingJob`, `TrainResult`, `generate_training_script`, `log_experiment`. |
| `src/haute/modelling/_algorithms.py` | `BaseAlgorithm` ABC, `CatBoostAlgorithm`, `ALGORITHM_REGISTRY`, memory-checkpoint helpers, CatBoost `Pool` construction, GPU fit-thread lifecycle. |
| `src/haute/modelling/_rustystats.py` | `GLMAlgorithm` implementing `BaseAlgorithm` via RustyStats; GLM-only diagnostics (`coefficients_table`, `relativities`, `fit_statistics`, `glm_diagnostics`); `estimate_glm_dispersion()` profile-likelihood estimation; `_resolve_glm_terms()` term resolution. |
| `src/haute/modelling/_training_job.py` | `TrainingJob` orchestrator — prepare one eligible source, persist/reload its evaluation plan, run selection or tuning fits, perform one deployable final fit, compute diagnostics, stage artifacts, and optionally log once to MLflow; also defines `TrainResult` and intermediate stage types. |
| `src/haute/modelling/_evaluation.py` | Strict version-1 evaluation config, exact development/final-test and validation-fit plan generation, plan/result/report codecs, digest linkage, strategy summaries, and validation-row-weighted aggregation. |
| `src/haute/modelling/_tuning.py` | Strict bounded CatBoost tuning config/search-space validation, seeded trial resolution, winner/tree-count selection, and tuning plan/trials/report codecs. |
| `src/haute/modelling/_train_config.py` | Single source of truth for modelling-node config → training-job kwargs (`build_training_job_kwargs`, `build_train_params`, `parse_evaluation_config`, `parse_tuning_config`, `training_objective_issue`, `default_metrics`, `effective_metrics`). |
| `src/haute/modelling/_target_check.py` | `training_target_task_issue()` — data-dependent target-column vs task/metric gate returning an actionable message (or nothing when the pairing is valid), keyed on the effective reported-metric set (explicit config metrics or the objective-implied defaults), shared by the train route's pre-dispatch validation and `TrainingJob._prepare_data`. |
| `src/haute/modelling/_split.py` | Internal partition-mask execution used by a final or selection fit. Its `SplitConfig` is a private test seam for direct callers exercising the shared partition/fit machinery; it is not a public modelling-node config contract and is not exported. |
| `src/haute/modelling/_metrics.py` | Primary metric functions and diagnostic data computation (double lift, AvE, residuals, actual-vs-predicted, Lorenz, PDP). |
| `src/haute/modelling/_feature_contract.py` | `FeatureContract` build/save/load/cache, contract comparison, and categorical-level normalisation/validation. |
| `src/haute/modelling/_signature.py` | `build_signature()` — MLflow `ModelSignature` construction with loud dtype/metadata validation, structural Date/parameterised-Datetime mapping, and the explicit no-lossy-Decimal policy. |
| `src/haute/modelling/_charts.py` | Pure-SVG renderers used by model cards. |
| `src/haute/modelling/_model_card.py` | `generate_model_card()` — self-contained HTML assembled for MLflow artifact logging; ordinary training does not persist it beside the model. |
| `src/haute/modelling/_mlflow_log.py` | Tracking-backend resolution, `log_experiment()`, flavor-aware model/signature logging, diagnostics artifacts, and best-effort model-card logging. |
| `src/haute/modelling/_result_types.py` | `ModelDiagnostics` and `ModelCardMetadata` bundles shared by training, MLflow logging, and model-card generation. |
| `src/haute/modelling/_export.py` | `generate_training_script()` code generation for standalone Python training scripts. |
| `src/haute/routes/modelling.py` | FastAPI router for training, status/cancel, estimates, MLflow check/log, export, model-cache clear, and dispersion jobs. |
| `src/haute/routes/_train_service.py` | Stable compatibility facade that re-exports `TrainService` and the established helper seams consumed by the router/tests. It owns no job state, worker entrypoint, preparation algorithm, or artifact mutation. |
| `src/haute/routes/_training_preparation.py` | Training input preparation rules plus the hard-capped preparation worker: deterministic sampling, feature/metadata demand and projection, modelling-node lookup, row-limit and RAM/VRAM feasibility helpers, the picklable `TrainingPreparationRequest`/`TrainingPreparationOutcome`/`TrainingPreparationFailure` transport, the in-process core `prepare_training_data`, the spawn entrypoint `prepare_training_data_worker`, and the target/task gate `_validate_target_task_pairing`. |
| `src/haute/routes/_training_evaluation.py` | Route-side evaluation and GLM-dispersion rules: family/link validation, dispersion parameter contracts, and immutable evaluation-preview projection. |
| `src/haute/routes/_training_worker.py` | Spawn-picklable training/dispersion worker protocol: request validation, child execution context, curated failure taxonomy, bounded progress/result payloads, and the two process entrypoints. It publishes only staged manifests. |
| `src/haute/routes/_training_artifacts.py` | Sole parent-side training artifact publication owner: manifest/path/size/digest validation, strict evaluation/tuning reloads, rollback-capable generation replacement, and stale tuning retirement. |
| `src/haute/routes/_training_lifecycle.py` | State-owning `TrainService` implementation. Composes `JobLifecycle`, `CancellableJobRegistry`, and `IsolatedJobSupervisor`; orchestrates preparation and worker launch, cancellation/timeout, parent cleanup, and atomic completion publication through the domain modules above. |
| `src/haute/routes/_memory_messages.py` | Shared curated wording for memory-limit failures (`memory_limit_user_message`, `format_byte_size`) used by training, auto-range, and the input-snapshot build. |
| `src/haute/schemas.py` | Shared Pydantic request/response contracts owned by [server-api](../server-api/low-level.md) and used by `/api/modelling/*` routes. |

## Key types and data structures

- **`BaseAlgorithm`** (ABC, `_algorithms.py`) — `fit()`,
  `predict(model, df, features, offset=...)`, `feature_importance()`, `save()`.
  Training and PDP diagnostics always call the declared `predict` signature, including
  `offset=None`; reduced-arity duck-typed implementations are not another interface.
  `CatBoostAlgorithm` and `GLMAlgorithm` implement it.
  Both also expose algorithm-specific methods that `_training_job._compute_metrics`
  probes with `hasattr()` rather than an interface method — `shap_summary` /
  `feature_importance_typed` (CatBoost only), `coefficients_table` / `relativities` /
  `fit_statistics` (GLM only).
- **`FitResult`** (`_algorithms.py`) — `model`, `best_iteration: int | None`,
  `loss_history: list[dict[str, float]]`. Returned by every algorithm's `fit()`.
- **`ALGORITHM_REGISTRY`** (`_algorithms.py`) — `dict[str, type[BaseAlgorithm]]`,
  `{"catboost": CatBoostAlgorithm}` unconditionally; `"glm": GLMAlgorithm` is added only
  if `import rustystats` succeeds (lazy `try/except ImportError` at module import time),
  so RustyStats stays an optional dependency.
- **`EvaluationConfig` / `EvaluationValidationFit` / `EvaluationPlan` /
  `EvaluationFitResult` / `EvaluationResultsArtifact` /
  `EvaluationAggregateReport`** (`src/haute/modelling/_evaluation.py`, frozen dataclasses) — the required
  version-1 evaluation contract and its persisted evidence. Parsing rejects unknown
  keys and inexact or non-finite values. Planning assigns final-test positions first,
  derives every validation fit only from development positions, and records exact
  source positions plus bounded random/group/temporal summaries. Artifact readers
  validate membership, ordering, counts and SHA-256 links before aggregation.
- **`TuningConfig` / `TuningTrialResult` / `TuningPlanArtifact` /
  `TuningTrialsArtifact` / `TuningReportArtifact`** (`_tuning.py`, frozen dataclasses)
  — the optional version-1 CatBoost tuning contract. It owns the 5–50 trial,
  at-most-200 trial-fit limits, seeded conditional explicit-choice search-space
  semantics, deterministic baseline/winner selection, validation-row-weighted final
  tree count, and strict digest-linked artifacts.
- **`FeatureContract`** (`_feature_contract.py`, frozen dataclass) — `features`,
  `feature_types`, `categorical_features`, `categorical_levels`, `target_name`,
  `target_type`, `task`, `contract_hash` (sha256 of canonical compact JSON over every
  other field), `offset_column: str | None`. Serialized contracts always contain every
  one of those fields, including an empty `categorical_levels` object and a nullable
  `offset_column`. `CONTRACT_FILENAME = "feature_contract.json"`;
  per-model files are named via `_training_job.model_contract_filename(name)` →
  `"{name}.feature_contract.json"`.
  `_training_job._polars_dtype_name` preserves `Date` and full
  `Datetime(time_unit=..., time_zone=...)` descriptors. `_signature._map_dtype`
  maps both temporal families to MLflow `DataType.datetime`; it recognises
  `Decimal(...)` separately and raises the actionable unsupported-type error
  rather than falling through to an unknown type or `double`.
- **`TrainingJob`** (`_training_job.py`) — the orchestrator. Public-node construction
  stores
  `name`, `data` (path/DataFrame/LazyFrame), `target`, `weight`, `exclude`,
  `feature_columns`, `fold_column`, `id_columns`, `algorithm`, `task`, `params`,
  required canonical `evaluation`, optional canonical `tuning`, `metrics` (defaulted
  via `default_metrics()` when omitted), `mlflow_experiment`, `model_name`,
  `output_dir`, `loss_function`, `variance_power`, `offset`,
  `monotone_constraints`, `feature_weights`, and a normalised
  `_declared_categorical_levels`. Internal clones additionally receive an
  `evaluation_plan`, optional validation `fit_index`, and the orchestrator's
  precomputed `plan_source_sha256`; those arguments are never accepted from node
  JSON. The `split` argument is a private test seam rejected by the public config
  builder; a direct caller that omits `evaluation` stays on that internal path, and
  supplying `evaluation` together with `split` fails instead of silently
  ignoring either contract. Canonical `tuning` and internal `evaluation_plan` inputs
  require an explicit canonical `evaluation`. A contract-dtype
  snapshot (`_contract_feature_dtypes`, `_contract_categorical_levels`,
  `_contract_target_dtype`, `_contract_offset_dtype`) is populated inside `_prepare_data`
  and consumed by `_save_artifacts` and `_log_to_mlflow`.
- **Modelling-node algorithm config** — CatBoost constructor hyperparameters are the
  contents of top-level `params`, with CatBoost Tweedie power in top-level
  `variance_power`. GLM configuration is exclusively top-level
  (`terms`, `all_factors`, `family`, `link`, `interactions`, `regularization`, `alpha`,
  `l1_ratio`, `intercept`, `var_power`, `theta`, `offset`); `build_train_params`
  projects those fields into the `TrainingJob.params` mapping consumed by RustyStats. Terms and
  interactions that reference a feature made dormant by `exclude` remain stored in node config
  but are omitted from this effective mapping until that feature is re-included; explicit
  `feature_columns` retains its established precedence over a stale exclusion.
- **`monotone_constraints`** — the selected MOD-M09 product lever is a mapping from
  configured feature name to the exact integer `-1` or `1` (Boolean and zero are invalid).
  `build_training_job_kwargs` removes entries named by `exclude` from the effective job mapping
  without mutating stored config; an empty effective mapping becomes `None`.
  `_validate_monotone_constraints` runs after GLM term narrowing and before
  `_split_data`; it requires a mapping with non-empty string keys, rejects names not in
  the final feature list, and accepts only canonical numeric contract dtypes
  (`Int64`/`Float64`). The resulting validated mapping is passed unchanged to
  CatBoost's feature-index translation or RustyStats term monotonicity.
- **CatBoost numeric array handoff** — `_build_pool` calls
  `_prepare_predict_frame(..., flavor="catboost")`, which returns a multi-column
  numeric Polars frame as a Fortran-contiguous `Float32` NumPy matrix and passes it
  directly to `catboost.Pool`. The opt-in MOD-M05 benchmark compares that path with
  the full candidate operation `Pool(numpy.ascontiguousarray(matrix))` over a
  deterministic 100,000-row by 32-feature workload. It records median end-to-end
  handoff time, matrix layout, source/copy bytes, exact feature equality, label
  equivalence within `Float32` ingestion precision, and seeded prediction equivalence
  at `rtol=atol=1e-12` with matching dtype. A C-layout conversion may enter
  production only when it is at least 20% faster and does not introduce a
  full-matrix peak allocation.
- **`TrainResult`** (`_training_job.py`, dataclass) — the child-internal result bundle.
  It carries model/feature/diagnostic data plus canonical `development_rows`,
  `final_test_rows`, `final_test_metrics`, `evaluation`, and optional `tuning`.
  Primitive `train_rows`/`validation_rows`/`holdout_*` fields remain internal
  final-fit plumbing. `_run_training_process_job` projects the bundle into the strict
  public `TrainResponse`, whose result terminology is exclusively development,
  validation/selection, and final test.
- **Intermediate stage types** (`_training_job.py`, all dataclasses): `_PreparedData`
  (data path, feature/categorical schema snapshot), `_SplitResult` (split parquet path,
  per-partition row counts), `_TrainModelResult` (fitted model, algo instance,
  `FitResult`, resolved fit params), `_MetricsResult` (everything that becomes the
  diagnostic portion of `TrainResult`). Each is produced by one pipeline stage and
  consumed by the next inside `TrainingJob.run()`.
- **`ModelDiagnostics` / `ModelCardMetadata`** (`_result_types.py`) — shared bundling
  dataclasses consumed by both `_model_card.generate_model_card` and
  `_mlflow_log.log_experiment`, avoiding 25+ positional parameters at either call site.
- **`MLflowLogResult`** (`_mlflow_log.py`) — `backend` (`"databricks"|"local"`),
  `experiment_name`, `run_id`, `tracking_uri`, `run_url: str | None`.
- **`GLMInferenceUnavailableError`** (`_rustystats.py`, `RuntimeError` subclass) —
  raised by `coefficients_table()` when real SE/z/p-value statistics cannot be
  obtained; never fabricated.
- **`DispersionEstimate`** (`_rustystats.py`, `__slots__`-based) — result of
  `estimate_glm_dispersion()`: `param` (`"theta"|"var_power"`), `value` (the resolved
  parameter), `llf` (the profile-maximised log-likelihood), `n_fits` (candidate fits the
  search performed). `_DISPERSION_BOUNDS` fixes the search interval per parameter:
  `theta` in `(0.01, 1000.0)` (profiled in log-space), `var_power` in `(1.01, 1.99)`
  (the open interval the compound Poisson-gamma family is defined on, matching the
  config panel's slider range).
- **`DispersionEstimateRequest`/`DispersionEstimateResponse`/`DispersionEstimateStatusResponse`**
  (`schemas.py`) — request carries `graph`, `node_id`, `source`, and `param:
  Literal["theta", "var_power"]`; the start response carries only `status`/`job_id`
  (job-based, like `/train`); the status response mirrors `TrainStatusResponse`'s
  progress/message/elapsed shape plus the resolved `param`/`value`/`llf`/`n_fits` once
  complete.
- **`TrainService`** (`routes/_training_lifecycle.py`, re-exported by
  `routes/_train_service.py`) — wraps a `JobStore`, `JobLifecycle`,
  and `CancellableJobRegistry`; owns the HTTP-facing training lifecycle
  (`start`/`cancel`/`timeout`) and the dispersion-estimation lifecycle
  (`start_dispersion_estimate`/`dispersion_job`/`cancel_dispersion`), distinguished in
  the shared job store by a `job_type` field (`"training"` vs. `"dispersion_estimate"`)
  so `dispersion_job()` 404s if asked for a job of the other type.

- **Training service import direction** — `_training_preparation`,
  `_training_evaluation`, `_training_worker`, and `_training_artifacts` are cohesive
  leaves and never import `_training_lifecycle` or `_train_service`.
  `_training_lifecycle` is the only job-state owner and composes those leaves;
  `_train_service` imports only to re-export the stable surface. The modelling package
  remains below the route layer, and worker entrypoints retain their lazy `TrainingJob`
  import so no route/modelling cycle is introduced.

- **`TrainingFeatureSelectionDiagnosticPayload`** (`schemas.py`) — the version-1
  explanation of the pre-training feature choice. `mode` is `explicit`, `all_except`,
  or `glm_terms`; `feature_count` must equal the selected-feature collection's total;
  selected features, retained metadata, and excluded columns are deterministically
  ordered and capped at 128 entries with `available|truncated` state. `TrainResponse`
  and `TrainStatusResponse` carry the payload additively as `feature_selection`, or
  `null` before a selection is available.

## Control flow

### Live training (HTTP)

1. `POST /api/modelling/train` → `routes/modelling.py:train_model` →
   `TrainService.start(body)`.
2. `TrainService.start`: locate the modelling node in the graph; merge declared
   `categorical_levels` from the node and its upstream ancestors
   (`_declared_categorical_levels_for_training`); `_validate_config` (target set,
   algorithm registered, canonical `evaluation` and optional `tuning` parsed,
   legacy `split`/`cross_validation` rejected, GLM family/link validity or CatBoost
   loss validity via `resolve_loss_function`, then `training_objective_issue` for
   completeness); under
   `_start_lock`, reject if another job is already `"running"`
   (`_check_no_concurrent_jobs`), create the job record, and register its cancellation
   token; start the owned preparation thread and return `TrainResponse(status="started",
   job_id=...)` before long-running work begins.
3. The preparation thread first verifies that the job is still running, then
   `_compile_preamble`; `_estimate_ram` (records the equivalent HTTP 422 detail on
   failure); clamp the estimated row limit against any user-supplied `row_limit`;
   `build_train_params` (the same builder export uses); `_check_gpu_vram_before_launch` (VRAM
   feasibility check — see Edge cases);
   compute required-column demand per node (`_training_required_columns_by_node`);
   create an admitted `ExecutionContext` with the already-registered cancellation token
   (RAM ceiling + cancellation) via
   `create_admitted_execution_context`. `_training_lifecycle.py::TrainService._execute_and_sink`
   is then a **parent supervisor only** — no training data is materialised in the server
   process:
   - it takes the budget envelope with `_execution_admission.py::isolated_execution_budget`
     (an admitted `execution_context` is required — `ValueError` otherwise), computes the
     remaining job timeout from `start_time`/`timeout` via `_worker_timing` (a non-positive
     remainder records `timeout()` and raises `ExecutionCancelledError` without launching), and
     builds the worker config — every fallible setup step runs **before** the temp parquet
     exists, so a setup failure cannot orphan one. Only then does it create the parent-owned
     temp parquet (`_training_preparation.py::create_training_parquet_path`); from that point
     supervision runs inside a `try` whose `except BaseException` backstop discards the path,
     so the only exit that keeps it is a successful hand-off;
   - it launches exactly one spawn worker,
     `run_isolated_worker(prepare_training_data_worker, request, budget, config=...)`, with
     `worker_config_for_memory_policy(memory_limit_bytes=budget.memory_limit_bytes,
     timeout_seconds=<remaining>, stop_reason=lambda: cancellation_reason(job_id),
     process_name="haute-training-prep")`. The child installs the matching native cap for
     exactly the parent's admitted headroom, so an unavailable materialisation estimate ahead
     of a group-by plans `full-width-conservative`/`warned` inside the worker instead of the
     `materialisation_estimate_unavailable` rejection an uncapped surface must raise;
   - the request is plain picklable data (`graph`, `node_id`, `job_id`, `source`,
     `parquet_path`, modelling `config`, `project_root`, `streaming_chunk_size`, `row_limit`,
     `exclude`, `keep_columns`, `required_columns_by_node`, `preamble_supplied`); the child
     never touches the `JobStore`.
   `_training_preparation.py::prepare_training_data` is the child core: it recompiles the
   preamble when supplied, runs the upstream pipeline lazily, derives the version-1
   feature-selection diagnostic from the materialised schema, rejects HTTP
   422/`contract_error` if target/metadata/exclusion rules leave no feature columns, validates
   the required columns actually arrived, projects away excluded columns while retaining
   explicit `feature_columns` even when a stale `exclude` entry also names them, streams the
   result to the parent's temp parquet with `bounded_sink` under the context's
   `training_sink_write` stage, and then runs the target/task gate;
   `training_target_task_issue` (`_target_check.py`) validates the sunk parquet's
   target column against the configured task and the effective metric set
   (`effective_metrics` — explicit config metrics or the objective-implied defaults,
   the same derivation `build_training_job_kwargs` uses; a malformed metrics config
   maps to the same 422/`contract_error` with the parquet removed) — a classification
   task pointed at a continuous (or otherwise non-classifiable) target gates
   regardless of the metric set (the fit itself is undefined on it), and a continuous
   target whose effective metrics include AUC/log loss (implied by a binomial family
   even under `task="regression"`, or set explicitly) gates on the metric-keyed
   branch; either removes the temp parquet and rejects HTTP 422/`contract_error`
   with a message naming the target column, task, and offending metrics, before any
   fit worker is spawned.
   Cleanup is fail-loud on both sides. `_training_preparation.py::_remove_prepared_parquet`
   raises rather than logging an `OSError`: a swallowed removal failure would leave real
   training data on disk while the job records a failure claiming no artifact exists. In the
   child, `_finalise_preparation_failure` removes the parquet for every failure arm and, when
   removal fails, degrades the outcome to a 500 `error` whose message names the surviving file
   while `fields` keeps the original `error_detail` and adds `cleanup_error` — the first cause
   is never hidden. In the parent, `TrainService._discard_prepared_parquet` maps a removal
   failure on any path to `_fail_preparation_worker(message="Training preparation cleanup
   failed: <exc>")` (500/`error`), never a bare exception.
   Expected child failures are returned, never raised across the boundary: a
   `TrainingPreparationFailure` carries `terminal_reason`
   (`contract_error`|`memory_limited`|`error`), the job `message`/`fields`, and the
   `http_status_code`/`http_detail`, computed in the child with the same
   `_http_failure_job_parts`/`contract_error_job_fields`/`_memory_limit_http_exception`
   helpers the in-thread path used, so job records and HTTP payloads are unchanged. Every
   child failure removes the parquet first — no partial training artifact ever exists.
   The parent maps the outcome: a `failure` transitions to its `terminal_reason` and raises
   the paired `HTTPException`; a success whose `parquet_path` differs from the parent's, or
   whose file is missing or empty, is a 500 `error` ("Training preparation worker did not
   produce its prepared data."); `IsolatedWorkerStoppedError` raises `ExecutionCancelledError`
   (the outer branch reads the registry for cancelled vs. timed_out);
   `IsolatedWorkerTimeoutError` records `timeout()` then raises it;
   `IsolatedWorkerMemoryLimitExceededError`, `IsolatedWorkerMemoryLimitUnsupportedError`,
   a crash whose exit code reads memory-limited, and an `IsolatedWorkerRemoteError` whose
   `remote_type` is a memory type become `memory_limited`/507 with
   `_worker_isolation.py::isolated_worker_memory_detail` labelled `budget.operation` — the
   admitted context's own operation, so the dispersion flow (which admits
   `operation="dispersion_estimate"` and reuses this supervisor) is never mislabelled as
   `training_pipeline`; the child's own memory payload already carries it, since its context is
   built from the same budget; any
   other worker failure logs `training_preparation_worker_failed` and becomes a 500 `error`.
   The child's `execution_metrics` payload is the one persisted on the job. Admission is
   released exactly once — by `_parent_worker_cleanup` after the fit worker, or by the
   `finally` in `_prepare_and_launch_training` on failure — never inside `_execute_and_sink`.
   `_launch_background` builds the `TrainingJob` via `build_training_job_kwargs`
   (`params` overridden by the GPU-adjusted `train_params`), creates a same-filesystem
   staging root, and starts a daemon supervisor thread around a spawn child.
   `TrainService` consumes the execution facade's typed projection result throughout
   materialisation; its final feature inclusion/exclusion provenance is retained in the
   job response rather than re-derived by a modelling-owned planner. Preparation owns
   registry/admission cleanup until child launch; after launch the child supervisor owns
   it. Every preparation exception is consumed by the thread and persisted as a typed
   terminal job rather than escaping as an unobserved thread failure.
4. In the child, `_run_training_process_job` reconstructs `TrainingJob` and a fresh
   bounded `ExecutionContext`, then runs planning, selection/tuning, final fit and
   diagnostics. It stages the model, per-model feature contract, three evaluation
   artifacts, and—when enabled—three tuning artifacts. It returns progress events and
   a validated result manifest containing a bounded `TrainResponse` payload. In the
   parent, the supervisor strictly reloads and cross-checks the complete staged set,
   publishes it with rollback, rewrites all artifact paths to durable destinations,
   and transitions the job to `"completed"` with final
   `elapsed_seconds`. Publication and the transition share the job-store critical
   section: cancellation that wins first prevents publication, while publication
   that wins first prevents a late cancellation from relabelling the durable model.
   On Windows only, access-denied/sharing-violation failures from `os.replace`
   receive a short bounded retry. Exhaustion raises
   `TrainingArtifactPublicationError` with the source, destination, and attempt
   count; rollback restores the previous durable generation before that typed failure
   escapes. Non-contention filesystem errors are never retried or reclassified.
   Typed child,
   protocol, crash, cancellation, timeout, cleanup, and unexpected supervisor failures
   map through `JobLifecycle`; parent cleanup always releases the cancellation registry
   and RAM admission and removes the prepared/staged temporary data.
5. `GET /train/status/{job_id}` first compares a running job's `start_time` with its
   configured/default timeout; an overdue job requests child termination and atomically
   transitioned to `timed_out` before the response is assembled. It then returns
   progress/loss history/result. On the first read of a completed result,
   `_assert_json_finite` re-validates it and the outcome
   is cached on the job (`_result_finite_validated`) via `atomic_update` so later polls
   skip the recursive walk; a validation failure instead flips the job to `"error"`
   with `result: None`. The response also carries `error_code`, `http_status_code`, and
   structured `error_detail` for terminal preparation failures, including the actionable
   GPU-VRAM 507 payload.

### Canonical `TrainingJob.run()` pipeline

This pipeline is selected by the explicit canonical `evaluation` supplied by every
live modelling-node and exported-script call. Direct/test callers that deliberately
omit it retain the constructor-only legacy split/CV pipeline described above.

1. **Prepare one eligible source** — `_prepare_data` reuses an already-sunk parquet or
   materialises a supplied frame, validates required columns and the
   target/task/metric pairing (`training_target_task_issue` over the job's effective
   metrics — a continuous or non-classifiable target under `task="classification"`,
   or a continuous target with AUC/log loss in the effective metric set, raises the
   same actionable message the route gate surfaces, here as a `ValueError`, covering
   the CLI and exported-script paths; internal evaluation clones skip the re-scan),
   removes null-target rows,
   derives the final feature set and schema snapshot, and applies GLM term narrowing
   and monotonicity validation once before planning.
2. **Plan once** — `_build_evaluation_plan` reads only the target or strategy key
   columns needed for planning, computes the prepared-source digest, and calls
   `generate_evaluation_plan`. `_run_evaluation` saves then strictly reloads
   `{model}.evaluation-plan.json` against that digest before any fit begins.
   Internal clones reuse that once-computed digest instead of re-hashing the
   source for every selection or trial fit; the orchestrator re-hashes the
   prepared source one more time immediately before the deployable final fit.
3. **Run selection evidence** — without tuning, each validation fit is an internal
   clone carrying the same plan and `fit_index`. `run_evaluation_fit` uses
   `EvaluationPlan.selection_mask`, trains only that partition, computes configured
   metrics (a metric failure here — `ValueError`, `TypeError`, or an arithmetic error — is
   re-raised via `_metric_stage_error` naming
   the validation fit, target column, task, and requested metrics — these are the
   first metrics computed on the live route, so the wrap must fire here too), and
   returns `EvaluationFitResult`; it never saves a deployable model,
   feature contract, MLflow run, SHAP/PDP, or full diagnostics. No-validation performs
   zero selection fits. Internal clones skip `_prepare_data`'s target/task/metric re-scan —
   the outer job already gated the shared prepared source.
4. **Run bounded tuning when configured** — `_run_tuning_trials` writes/reloads the
   tuning plan, uses one seeded Optuna `TPESampler` through sequential ask/tell, runs
   every baseline/sampled candidate on the exact same validation fits, persists every
   trial, selects the deterministic winner, derives its validation-row-weighted tree
   count, and produces final parameters with validation-only early-stop controls
   removed.
5. **Persist selection results** — `_run_evaluation` writes and strictly reloads
   `{model}.evaluation-results.json`, aggregates only from that reloaded evidence, and
   writes/reloads `{model}.evaluation-report.json`. Every summary metric is weighted by
   validation rows and linked to the exact plan/results digests.
6. **Perform one final fit** — an internal clone uses
   `EvaluationPlan.final_mask`: every development row is training data and final-test
   rows, if any, occupy the internal holdout partition. `_train_model` resolves the
   algorithm and projections; `_compute_metrics` reads the chosen diagnostics
   partition once and computes primary metrics plus optional diagnostics (a
   metric failure (`ValueError`, `TypeError`, or an arithmetic error) from mandatory
   metric computation is re-raised with the evaluation
   set, target column, task, and requested metric names wrapped around the library
   error, so a bare sklearn message never crosses the worker boundary). The outer
   orchestrator maps internal partition names to public `development`/`final_test`
   labels, attaches the evaluation/tuning reports, and saves the native model plus
   feature contract.
7. **Log once, after evidence is attached** — when an MLflow experiment is configured,
   the outer orchestration calls `_log_to_mlflow` once with selected final parameters,
   canonical result labels, evaluation/tuning summaries and artifact paths, reusing the
   same feature-contract dtype snapshot for the `ModelSignature`.
8. **Clean up on every path** — cancellation checkpoints surround planning, each fit,
   persistence, final fit and publication progress. `finally` removes all run-owned
   parquets and any staged evaluation/tuning artifacts from a failed child run.

### Script export

`POST /api/modelling/export` → `_export.generate_training_script(config, data_path)` →
`build_training_job_kwargs` (identical builder to live training) → renders a
`TrainingJob(...)` constructor call as source text, omitting any kwarg equal to
`TrainingJob`'s own default (so the script stays readable), plus a `__main__` block
that runs the job and prints its metrics. `_training_job_uses_tweedie_variance_power`
decides whether `variance_power` needs to be rendered (CatBoost `Tweedie` loss, or GLM
`family == "tweedie"`).

### Dispersion estimation (HTTP)

1. `POST /api/modelling/dispersion/estimate` → `routes/modelling.py:estimate_dispersion`
   → `TrainService.start_dispersion_estimate(body)`.
2. `_validate_dispersion_config`: reject an unknown `param`, a non-GLM node, an
   invalid family/link combination, a `param` that doesn't belong to the request's GLM
   family (`theta` ⇒ `negbinomial`, `var_power` ⇒ `tweedie`), a missing target column,
   or (via `training_objective_issue`, called with the parameter being estimated
   stubbed to its RustyStats silent default so its own gate doesn't fire) any other
   incomplete part of the training objective.
3. Under `_start_lock`, reject if a job is already running (shared with training —
   `_check_no_concurrent_jobs` does not distinguish job type) and create the job
   record (`job_type="dispersion_estimate"`).
4. `_estimate_ram` and `_execute_and_sink` reuse the exact same helpers `start()` uses
   to materialise the node's training frame — same pipeline execution, projection, and
   seeded row sampling, including preservation of explicit features that also appear
   in `exclude` — so the profiled data matches what a real training run would
   see. The row limit is additionally clamped to `_DISPERSION_ESTIMATE_ROW_CAP`
   (200,000): the profile search runs ~10-30 IRLS fits, so it samples rather than
   paying full-data cost per candidate — 200k rows pins a single dispersion scalar far
   tighter than the search's own tolerance.
5. `_launch_dispersion_background` builds a plain request for a stub `TrainingJob` via
   `build_training_job_kwargs` with the parameter being estimated set to its RustyStats
   silent default (`_DISPERSION_PARAM_STUBS`: `theta=1.0`, `var_power=1.5`) so the
   shared config machinery can run; the stub value never reaches a fit — the search
   overrides it at every candidate. A spawn child then runs `job._prepare_data`
   (identical to training), narrows `features`/`cat_features` to the GLM terms exactly
   as `TrainingJob.run` does, resolves the effective terms via `_resolve_glm_terms`
   (shared with `GLMAlgorithm.fit`, so the profiled design can never drift from what
   training would actually fit), collects only the columns the design needs, and calls
   `estimate_glm_dispersion`.
6. `estimate_glm_dispersion` (`_rustystats.py`) validates `param` is estimable and
   matches `family`, then runs a bounded 1-D `scipy.optimize.minimize_scalar` search
   (`method="bounded"`) maximising `rs.glm_dict(**builder_kwargs).fit().llf()` over the
   parameter — `theta` searched in log-space over `_DISPERSION_BOUNDS`. Each candidate
   fit that raises is treated as `-inf` log-likelihood rather than aborting the search;
   only a search where every candidate fails raises `ValueError`. An `on_fit` callback
   fires before each candidate fit, checks the child execution budget, and emits a
   bounded progress event (capped visually at fit 30); the parent event handler updates
   `job_id`'s `progress`/`message`.
7. On success, the job transitions to `"completed"` with `param`/`value`/`llf`/`n_fits`
   fields. `ValueError` (including "no candidate converged") maps to `contract_error`;
   execution cancellation maps to `cancelled`, memory exhaustion maps to
   `memory_limited`, and anything else maps to `error` via `_friendly_error`. The
   parent supervisor always releases the job registry entry and RAM admission and
   deletes the temp parquet and staging root.
8. `GET /dispersion/status/{job_id}` and `POST /dispersion/cancel/{job_id}` mirror the
   training job's status/cancel routes, scoped to `job_type="dispersion_estimate"` via
   `TrainService.dispersion_job`. The original job record carries `start_time` and
   `timeout`; the process supervisor enforces the remaining duration without depending
   on status polling.

### Shared MLflow tracking/experiment-name resolution

`_mlflow_log.py` exposes three helpers that both this component's routes and the optimiser
component's routes call, so experiment-naming and tracking-setup logic exists in exactly one
place rather than being duplicated per route:

- `resolve_experiment_name(*, explicit, config_value, node_label, backend)` — standard
  fallback chain (highest wins): an explicit override from the request body, then the node
  config's `mlflow_experiment` value, then a backend-aware default (`/Shared/haute/{label}`
  for Databricks, bare `{label}` for local — the `/Shared/` prefix is Databricks-specific and
  was previously applied to local MLflow too, where it is meaningless).
- `configure_mlflow_tracking()` — resolves the tracking backend, calls
  `mlflow.set_tracking_uri`, and conditionally `set_registry_uri("databricks-uc")` for
  Databricks; the single place connection setup happens.
- `build_run_url(backend, experiment_name, run_id)` — builds a Databricks run URL via
  `mlflow.get_experiment_by_name` to resolve the numeric `experiment_id` (Databricks URLs
  require the ID, not the name — an earlier version built the URL from `experiment_name`
  directly and produced broken links); returns `None` for local backends or on a failed
  experiment lookup (logged, not raised).

`routes/modelling.py` and `routes/optimiser.py` both call all three; `TrainingJob._log_to_mlflow`
itself does not — it passes `mlflow_experiment` straight through to `log_experiment()`, since
that is the programmatic-API path where the caller has already chosen the value. Rejected
alternatives: routing the optimiser's MLflow logging through this component's `log_experiment()`
(rejected — the optimiser logs a different artifact shape, solver params/frontier CSV/
`optimiser_result.json`, vs. training's model diagnostics/SHAP/model card, and forcing both
through one function would need fake empty metadata or `if is_optimiser:` branches); moving
`resolve_tracking_backend()` itself into `_mlflow_utils.py` (rejected as a large-diff,
zero-behaviour-change move not worth it standalone); a `[mlflow]` section in `haute.toml` to
unify experiment config across training/optimiser/deploy (deferred as a larger config-schema
change).

### MLflow-log-after-the-fact

`GET /mlflow/check` always returns the complete availability tuple:
`mlflow_installed`, `mlflow_importable`, and `tracking_configured`. Package
discovery, importability, and tracking resolution are distinct states; none of
the three response fields is inferred from a missing value.

`POST /mlflow/log` looks up a completed job's cached `TrainResponse`; if the saved
model file exists, reloads its persisted feature contract from disk (via
`load_contract_cached`, next to the model at `model_contract_filename(model.stem)`) —
never re-derives feature metadata from the job payload, because a model file without a
contract is treated as an error condition rather than a reason to guess Float64 for
everything; builds `ModelDiagnostics`/`ModelCardMetadata` (including the GLM fields);
calls `log_experiment` via `run_in_threadpool` to keep the event loop responsive.

`log_experiment` passes the persisted contract metadata to `_log_model_with_signature`. A `.cbm`
artifact is loaded and logged through `mlflow.catboost.log_model` at artifact path `model`; a
`.rsglm` (or other non-CatBoost native file) is represented by an MLflow pyfunc model with the
same signature and the native file is also logged at the run root for Haute's native-artifact
discovery path. Thus both families carry a `ModelSignature`, but only CatBoost uses MLflow's
native CatBoost flavor.

`build_signature` classifies canonical Polars dtype descriptors structurally.
`Date`, bare `Datetime`, and parameterised `Datetime` descriptors for every
Polars-supported unit/time zone become MLflow `datetime`; unsupported or
malformed lookalikes still raise. `Decimal` and `Decimal(...)` always raise
before `mlflow.*.log_model` is called, naming the column dtype policy and the
two explicit upstream cast choices. A real local-file-store pyfunc regression
logs, reloads, and predicts with Date and parameterised Datetime inputs so
MLflow schema enforcement—not only `_map_dtype`—is the compatibility oracle.
Because MLflow's scalar signature does not retain a time zone and rejects
timezone-aware pandas dtypes, the production pyfunc scoring boundary converts
zoned temporal columns to UTC and then removes the zone before prediction.
Naive temporal values are left unchanged; other flavors retain their existing
input preparation.

### Concurrency / ordering guarantees

- Only one job may be `"running"` in the process-wide training namespace; `_start_lock`
  serialises the check-then-create so concurrent training/dispersion submissions cannot
  both pass `_check_no_concurrent_jobs`.
- Training's process supervisor enforces the create-time deadline; status polling can
  independently observe and win the same timeout transition. The timeout transition and
  late worker progress/completion writes are CAS-guarded, so a timed-out job cannot
  become completed.
- Dispersion estimates share the single running slot and cancellation registry with training;
  their supervisor enforces the create-time timeout even without a status poll.
- Training pipeline preparation/materialisation runs in its owned preparation
  thread after the route has returned a job handle. The heavy training or
  dispersion phase runs in one spawn child supervised by one daemon parent thread;
  validated progress/iteration callbacks use the job store's `atomic_update`, so status
  polling from other threads/requests is race-free.
- Child progress transport is non-blocking and capped at 10,000 delivered events.
  Full-queue or over-budget updates are dropped, the loss count is reported on the next
  delivered event/end marker, and parent drains are batch-bounded so timeout and
  cancellation checks cannot be starved by a fast producer.
- GPU CatBoost fits run on a nested worker thread
  (`_run_gpu_fit_with_metric_polling`, `_algorithms.py`) because CatBoost has no
  progress-callback support under GPU; the caller thread polls
  `learn_error.tsv` every `_GPU_FIT_POLL_INTERVAL_SECONDS` (2s). If the polling loop's
  own `on_iteration` raises (cancellation), the worker is given
  `_GPU_FIT_ABORT_JOIN_TIMEOUT_SECONDS` (30s) to finish before the exception propagates.
- `load_contract_cached` is a process-wide, stat-gated (`st_mtime_ns`, `st_size`),
  single-flight cache — contract reads sit on the scoring hot path, so an unchanged
  file is served from cache while a changed file (retrain/redeploy) reloads and
  re-verifies its hash.

## Edge cases and invariants

- Planning an empty eligible source, or any requested empty development,
  validation, final-test, group, class or temporal partition, raises before fitting.
- Temporal evaluation rejects null/unparseable dates with the invalid-row count,
  keeps equal dates together, requires a single-validation boundary before the
  final-test boundary, and gives every expanding-window fit strictly earlier training
  dates than validation dates.
- Random classification planning is target-stratified. It reports class counts and
  the minimum required rows when a requested partition/fold cannot contain every
  class; random regression remains seeded but unstratified.
- Group planning canonicalises keys, never divides a group between memberships, and
  greedily balances seeded groups toward requested row counts. Too few groups for the
  requested test/validation structure fails instead of leaking or creating an empty
  fit.
- `EvaluationConfig.from_plain_data` rejects unknown versions/fields, Boolean numeric
  values, non-finite/out-of-range fractions, invalid strategy-specific keys, temporal
  relative fractions, and cross-validation counts outside 2–10 before data is touched.
- Tuning is CatBoost-only, requires validation, includes its baseline in 5–50 trials,
  and must satisfy `trial_count * validation_fit_count <= 200`. Invalid search shapes,
  empty/duplicate/oversized or non-finite candidate lists, reserved orchestration keys,
  impossible/cyclic conditions, or a selection metric outside the configured metrics
  fail before Optuna is created.
- GLM `terms` naming a column absent from the training data raise before fitting,
  listing the missing names and a truncated sample of what is available.
- `_build_interactions` (`_rustystats.py`) filters unset factor slots (`""`) out of an
  interaction's `factors` list before checking the two-factor minimum. The config
  panel's "+ Add" control creates `{"factors": ["", ""]}` before the user has picked
  both columns; without the filter, that phantom two-empty-string interaction passed
  the length check and crashed the fit rather than being silently skipped as intended.
- A Negative Binomial GLM (`family="negbinomial"`) requires an explicit `theta` before
  training or export can proceed — `training_objective_issue` gates it identically to
  Tweedie's variance power, since RustyStats fits silently at `theta=1.0` if unset.
  `estimate_glm_dispersion` exists specifically to give the user a principled value to
  set rather than guessing.
- GLM interaction terms whose factors are all already present as main terms force
  `include_main=False` — RustyStats' `include_main=True` would otherwise duplicate the
  main effect in the design matrix and produce a singular matrix.
- GLM regularization's internal alpha-search cross-validation fold count is hardcoded to 5
  (`fit_kwargs["cv"] = 5` in `GLMAlgorithm.fit`), matching sklearn's `LassoCV`/`RidgeCV`/
  `ElasticNetCV` default — treated as a numerical implementation detail, not a user-exposed
  config knob.
- CatBoost's offset baseline is only honoured when supplied through a `Pool`; a
  bare-matrix `predict()` call silently scores from baseline 0 in CatBoost itself, so
  `CatBoostAlgorithm.predict()` always wraps in a `Pool` whenever an offset is
  configured (`_extract_offset_baseline` raises if the column is missing).
- GLM prediction keeps the offset column inside the frame handed to RustyStats rather
  than transforming it in Python — RustyStats owns the fit-time offset transform (e.g.
  log for an exposure column under a log-link family) and reapplies it identically at
  predict time.
- Metric/diagnostic functions filter non-finite rows before computing (a warning is
  logged and the dropped count is surfaced via `metrics[NON_FINITE_FILTERED_KEY]`), but
  raise `ValueError` outright if *every* row is non-finite.
- Gini/Lorenz computation is tie-corrected: `_aggregated_lorenz_points` groups rows
  sharing an exact sort-key value into one Lorenz segment with a canonical
  `(y_true, weight)` tie-break, so both the Gini scalar and the plotted Lorenz curve
  are exactly independent of input row order (`test_metrics_gini_ties.py` is a
  dedicated regression suite for this).
- The feature contract is hashed over sorted, separator-compact canonical JSON but
  written to disk pretty-printed — human-reviewable on disk, byte-deterministic for
  hashing.
- `categorical_levels` domains are explicit metadata and are never inferred from
  observed row values; `validate_categorical_value_domains` only checks observed rows
  against a declared domain and raises `FeatureMismatchError` with example offending
  values when violated.
- Feature contracts are written only to each model's canonical companion path.
- Column-projection pushdown (`_glm_select_columns` / `_catboost_select_columns` /
  `_training_required_columns_by_node`) bounds parquet-read memory: GLM reads only its
  term + target + weight + offset columns; CatBoost's required-columns demand is an
  "all except" projection (everything but excluded/target/weight/offset) rather than
  an unbounded "unknown" demand.
- Every run-owned temp parquet is tracked with an `owns_tmp` flag at each stage; the
  happy path frees each file as soon as the next stage no longer needs it, and a
  `finally`-block abort-safety net (`_cleanup_owned_temp_parquets`) removes anything
  that survived an aborted/cancelled run. Caller-supplied parquet paths (`owns_tmp =
  False`) are never touched by cleanup.
- `_assert_json_finite` recursively walks Pydantic models, dicts, lists/tuples, and any
  `numbers.Real` value (excluding `bool`, since `bool` is a `Real` subclass in Python)
  — this is the mechanism that catches a NaN/Inf anywhere inside a large nested
  diagnostics payload before it reaches the wire.

`TrainService._check_gpu_vram_before_launch` only checks VRAM feasibility; it
never falls back to CPU automatically. Insufficient VRAM becomes a terminal
HTTP 507 job result instructing the user to select CPU (or reduce
rows/features) and retry.

## Error handling

- **`TrainingConfigError`** (`ValueError` subclass, `_train_config.py`) — an
  incomplete or invalid modelling-node config. Raised by `build_training_job_kwargs`
  and `training_objective_issue`; translated to HTTP 400 by
  `routes/modelling.py:export_script` and by `TrainService._validate_config`.
- **`FeatureMismatchError`** (`haute.errors.HauteError` subclass) — feature-contract
  structural problems: missing/unknown top-level fields, wrong field types, hash
  mismatch (edited/corrupted file), invalid `categorical_levels` declarations, and
  train-vs-score disagreement via `assert_contracts_match` (names the offending field
  and shows expected vs. actual).
- **`GLMInferenceUnavailableError`** (`RuntimeError` subclass, `_rustystats.py`) —
  caught by `TrainingJob._compute_metrics`'s `hasattr(algo, "coefficients_table")`
  block via `_record_diag_error`; recorded in `diagnostics_errors`, never propagates to
  fail the whole run.
- **`ValueError`** — the dominant validation error across the package: invalid
  evaluation/tuning evidence, missing required columns, a target/task/metric mismatch
  (`training_target_task_issue`), an empty training DataFrame, all-non-finite metric
  inputs, a missing offset column at predict time, or GLM terms referencing absent
  columns. A metric failure (`ValueError`, `TypeError`, or an arithmetic error —
  never `MemoryError`, which keeps its memory taxonomy) in
  `TrainingJob._compute_metrics` is re-raised as a `ValueError` naming the evaluation
  set, target column, task, and requested metrics, chaining the original.
  `_run_training_process_job` maps a bare `ValueError` from
  `TrainingJob.run()` to a `contract_error` failure payload (distinct from the
  catch-all `error`), and the parent supervisor persists that terminal reason.
- **Execution-engine exceptions** (`ExecutionCancelledError`,
  `ExecutionMemoryLimitExceededError`, `BoundedMemoryUnsupportedError`) — each maps to
  a distinct failure payload (`cancelled`, `memory_limited`, `contract_error`
  respectively); parent-side cancellation/timeout additionally terminates the child
  through the supervisor stop callback.
- **`HTTPException`** — raised directly by route handlers and by `TrainService.start`
  for 400/409/422/500/507. `TrainService.start`'s `except HTTPException` block
  additionally transitions the job record to the matching terminal state
  (`memory_limited` for 507, `contract_error` for other 4xx, `error` otherwise) before
  re-raising, so the job store and the HTTP response can never disagree about outcome.
- **Generic `Exception`** catch-all in each process entrypoint maps to an `error`
  failure payload with the message produced by
  `_friendly_error(exc, operation_noun=..., context=...)` — a heuristic
  translator whose every shape is haute-authored, and which — apart from the
  validation channel — never interpolates a third-party message body:
  `HauteValidationError` messages verbatim (the validation channel; provenance
  is enforced by the marker type, defined in `_validation_error.py` as a
  `ValueError` subclass, re-exported by `errors.py`, and raised at haute's own
  validation sites — gates, column checks,
  the metric wrap, config/protocol validation; `TrainingConfigError` extends
  it. A dependency's plain `ValueError` — including a pydantic
  `ValidationError` — does not ride the channel: it takes the type-only
  unexpected-error fallback, closing the #159 design's dependency-`ValueError`
  residual), `FileNotFoundError` as a
  path-free could-not-find-a-file shape (a fit-stage missing file is typically
  an internal staged asset, so this deliberately narrows #159's
  path-as-actionable ruling — the path stays in the diagnostic fields and
  traceback), CatBoost failures keyed on the exception TYPE name (never a
  message substring, so a non-CatBoost error that mentions catboost in its text
  cannot take these shapes) with a NaN/Inf hint, a body-free feature-mismatch
  shape, and a body-free generic shape naming the type and training context,
  `OSError` rendered as a save failure whose reason is re-derived from the
  numeric errno via `os.strerror` (`exc.strerror` is constructor-supplied and
  untrusted; no errno → a generic file-system-error wording) — never the
  internal temp/staging path embedded in `str(exc)` — and an unexpected-error
  fallback naming the operation, the target/objective context, and the exception
  type. `context` is built by
  `_training_context_phrase(job_kwargs)` (`target 'x' (objective 'y')`, falling
  back to `"the model"`); the entrypoints capture it right after parsing
  `job_kwargs` so a fit-stage failure names what was being trained. The fallback
  stays a plain system `error` — a system fault is never relabelled as
  `contract_error`.
- **Curated failure surfacing** — `_worker_failure_payload` takes an explicit
  `user_facing` decision from every call site and stamps the payload's
  `user_message` field (`_worker_protocol.WORKER_USER_MESSAGE_FIELD`) only for
  deliberately curated, haute-authored wording: the cancelled/memory-limit/
  contract-error/bounded-memory branches of `_known_training_worker_failure`
  (the memory-limit branch authors its message from the exception's structured
  attributes via the shared `routes/_memory_messages.memory_limit_user_message`
  — human-readable used/allowed sizes plus a call to action, never the internal
  operation name, which stays in the diagnostic `error` field; the same shape
  serves the training 507 response, the auto-range job, and the input-snapshot
  build, so the wording cannot drift between surfaces), the
  `HauteValidationError` validation channel (which carries the gate and
  metric-wrap messages), and every `_friendly_error` shape
  (all curated as above, so the entrypoints pass `user_facing=True` and thread
  the raw `str(exc)` into `fields["error"]` explicitly). Raw `MemoryError` text
  is NOT stamped and keeps the typed "Isolated worker raised {type}: {message}"
  wrapper surface. For stamped failures the supervisor surfaces the field
  verbatim as the job's terminal message; the wrapper text is retained in the
  diagnostic `error` field. See
  [background-jobs](../background-jobs/low-level.md) for the supervisor side of
  the contract.
- **`_record_diag_error`** is the single call site that converts an optional-diagnostic
  exception into a structured `diagnostics_errors` entry (`diagnostic`, `error`,
  `error_type`) plus a `logger.warning` — used identically for SHAP,
  `LossFunctionChange` importance, PDP, and every GLM-specific diagnostic
  (`coefficients_table`, `relativities`, `fit_statistics`, `regularization_path`).
- **MLflow logging errors** — `_log_model_card` inside `log_experiment` is wrapped in
  `try/except Exception: logger.warning(...)`, so a model-card bug never fails an
  otherwise-successful experiment log; `build_run_url` similarly catches and returns
  `None` with a debug log rather than failing the whole call.
- **Unsupported MLflow Decimal signature** — `_signature._map_dtype` raises
  `ValueError` before model logging, explains that MLflow 3.x has no exact
  Decimal scalar, and directs the author to an explicit upstream `String` or
  `Float64` cast. The original Decimal descriptor is named; no value is
  inspected or coerced.
- **Dispersion estimation** — `_validate_dispersion_config` raises `HTTPException(400)`
  for an unknown parameter, a non-GLM node, a family/link mismatch, a parameter/family
  pairing mismatch, a missing target, or any other incomplete training objective;
  raised before any job record is created. Inside `estimate_glm_dispersion`, `ValueError`
  covers an unrecognised or mismatched `param` and "every candidate fit failed"; inside
  the process worker, `ValueError` maps the job to `contract_error`,
  `ExecutionCancelledError` maps to `cancelled`, and any other exception maps to
  `error` via `_friendly_error` — the same taxonomy the training entrypoint uses.

## Testing

- `tests/test_service_domain_boundaries.py` keeps the training facade explicit,
  the extracted service-module graph acyclic, and lifecycle state ownership out
  of preparation, evaluation, worker-protocol, and artifact leaves.
- `tests/test_training_preparation_worker.py` pins the hard-capped preparation
  worker: exactly one `haute-training-prep` launch per preparation with the budget's
  `memory_limit_bytes`, the remaining job timeout, and a `stop_reason` that reads the
  live cancellation registry; every outcome in the mapping table (each in-child
  `failure` kind, a success whose file is missing, stopped/timed-out/crashed
  with-and-without a memory guess, RSS-breach, unsupported cap, and remote memory
  vs. non-memory errors) with its terminal job state, `error_code`/`http_status_code`/
  `error_detail`, exactly one parent admission release, and no parquet left behind;
  a real spawn proving the parquet hand-off and `execution_metrics.admission`
  (`profile="training_prep"`, the budget's limit); a real spawn whose unavailable
  materialisation estimate ahead of a group-by reports
  `execution_strategy.status="warned"` / `strategy="full-width-conservative"`; and a
  hand-off parity check that the worker's parquet matches the in-process core's schema
  and rows. `TestPreparationCleanupFailsLoud` proves the fail-loud cleanup contract (an
  unremovable parquet degrades a child 422 to a 500 `error` that still carries the original
  `error_detail` plus `cleanup_error`; a parent-side removal failure ends the job 500 `error`;
  the success path is untouched), `TestDispersionWorkerMemoryOperation` proves a dispersion
  worker-memory failure is labelled `operation="dispersion_estimate"` on both the 507 and the
  job's `error_detail`, and `TestPreparationTempPathOwnership` proves a setup failure before
  launch (an invalid `HAUTE_WORKER_MEMORY_ENFORCEMENT`) ends the job `error` with no
  `haute_train_*.parquet` left behind. `tests/test_modelling_routes.py::TestTrainingProjection` covers the child
  core directly (projection forwarding, bounded-sink and target/task-gate contract
  failures, memory failures) with `execute_lazy_graph` patched at
  `haute.routes._training_preparation.execute_lazy_graph`.
- `tests/performance/test_catboost_contiguity_perf.py` records the MOD-M05
  Fortran-versus-C CatBoost handoff evidence and enforces layout, allocation, and
  result-equivalence facts; it is opt-in under the `perf` marker.
- `tests/performance/test_training_scoring_wide_perf.py` covers wide training/scoring performance.
- `tests/test_ave.py` verifies AVE numeric/categorical binning, weights, NaN/null/constant/missing/empty inputs, category limits, and feature limits.
- `tests/test_gpu_fit_cancel.py` verifies algorithm-level cancellation and metric-polling cancellation behavior.
- `tests/test_mem_helpers.py` verifies RSS/available-memory helpers and checkpoint behavior.
- `tests/test_mlflow_log.py` verifies tracking backend/experiment resolution, run URL construction, experiment/model-card/JSON logging, and tracking configuration.
- `tests/test_mlflow_signature.py` verifies structural Date/Datetime mapping,
  parameterised unit/time-zone coverage, Decimal rejection, signature
  persistence, and a real local MLflow log/load/predict round trip.
- `tests/test_mlflow_log_button_roundtrip.py` verifies CatBoost/GLM log-button round-trip construction and button payloads.

Tests live in the flat `tests/` directory rather than mirroring the package layout:

- `test_modelling.py` — the broad unit-test base for `TrainingJob`, algorithms,
  metrics, and internal partition execution.
- `test_modelling_routes.py` — HTTP-level integration tests for every route
  in `routes/modelling.py`, including `TestDispersionEstimateEndpoint` (happy path,
  status polling, completion payload) and `TestDispersionErrorPaths` (every 400
  validation branch, worker-side failure mapping, cancellation).
- `test_modelling_export.py` — exhaustive coverage of
  `generate_training_script` and its kwarg-rendering rules.
- `tests/test_evaluation.py` and `tests/test_train_evaluation_config.py` — strict
  canonical config parsing, deterministic random/group/temporal plans,
  stratification and failure counts, membership/order invariants, digest-linked
  artifact round trips, and validation-row-weighted aggregation.
- `tests/test_training_evaluation.py` and
  `tests/test_training_response_evaluation.py` — selection-only execution, one final
  deployable fit, final-test exclusion/evaluation, public response invariants,
  cancellation checkpoints and cleanup.
- `tests/test_tuning.py` and `tests/test_training_tuning.py` — static search-space
  validation, seeded conditional sampling, deterministic baseline/winner/tree-count
  selection, fit bounds, exact plan reuse, progress, artifact trust, and candidate
  failure visibility.
- `test_train_config_builder.py` — unit tests for the config→kwargs builder,
  including regression coverage for the GLM-vs-CatBoost key-routing bugs it was written
  to prevent, and the Negative Binomial `theta` gate (unset fails loud, top-level
  `theta` passes, non-negbinomial families are unaffected, `theta` survives script
  export).
- `test_metrics.py` and `test_metrics_gini_ties.py` (the "C6 regression suite") —
  metric correctness and the tie-corrected Gini/Lorenz
  row-order-independence guarantee.
- `test_charts.py` and `test_model_card.py` — SVG chart and HTML
  model-card generation.
- `test_feature_contract.py` and `test_mlflow_signature.py` —
  contract build/save/load/hash-verification and MLflow signature construction.
- `test_modelling_train_score_contract.py` — explicit train↔score contract
  regressions: feature/categorical order mismatch, MLflow signature round-trip,
  categorical type mismatch, GLM column selection preserving categorical metadata
  across save/load.
- `test_rustystats_algorithm.py` (skipped when RustyStats isn't installed)
  and `test_glm_integration.py` — GLM fit/predict/save/diagnostics and
  integration-gap regressions. `TestNegBinomialThetaThreading` pins that an unset
  `theta` really is RustyStats' silent-1.0 default and that a set `theta` reaches the
  fit; `TestEstimateGlmDispersion` validates the profile-likelihood search itself — the
  NB `theta` MLE against a statsmodels NB2 reference (to 4 s.f.) with coefficient parity
  (to 4 d.p.) on synthetic data, determinism, an interior Tweedie `var_power` maximum,
  the `on_fit` cancellation hook, and the unknown-parameter/family-mismatch rejections;
  `TestBuildInteractionsEmptySlots` pins the unfilled-interaction-row fix described
  above.
- `test_train_service_coverage.py` and
  `test_train_service_helpers_coverage.py` — `TrainService` error/cleanup
  branches and its pure column-demand helper functions.
- `test_algorithms_coverage.py` — targeted coverage of `_algorithms.py` /
  `_training_job.py` paths not hit elsewhere (platform-specific RSS reads, CatBoost and
  MLflow mocked out via `unittest.mock`).
- `test_target_task_gate.py` — `training_target_task_issue` unit coverage (discrete
  dtypes pass, integral floats pass, fractional floats and non-classifiable dtypes
  gate with messages naming the target column, task, and call to action; regression
  task with regression metrics untouched; AUC/log loss in the effective metric set
  gates a fractional target under any task, with explicit regression metrics as the
  escape hatch and non-float targets deferred to the fit's own validation on that
  branch), the `TrainingJob._prepare_data` gate on both the legacy and
  evaluation-plan pipelines (including the objective-implied Logloss-under-regression
  case), the metric-stage `ValueError` context wrap on both the final-fit and
  validation-fit sites (via a multi-class integer target, which passes the gate but
  breaks binary AUC), and the route-side pre-dispatch gate
  (`TestPreDispatchServiceGate`: 422 → `contract_error`, temp-parquet removal on the
  issue and scan-failure paths, the binomial-family-under-regression rejection with
  its explicit-regression-metrics pass, and the wiring test proving the gate precedes
  `_launch_background`). Its `TestWorkerBoundaryUserMessage` covers both supervisor
  sides of the curated-message contract.
- Narrow, remediation-pinned regression suites: `test_training_memory_safety.py`,
  `test_training_temp_cleanup.py`, `test_training_split_streaming.py`,
  `test_training_null_target_fused_split.py`, `test_training_catboost_projection.py`,
  `test_training_contract_per_model.py`, `test_training_lorenz_nonfinite.py`.
- Additional targeted coverage: `test_modelling_loud_errors.py`,
  `test_modelling_golden.py` (golden-snapshot pins for route response shapes),
  `test_bundle6_trust_model_cleanup.py`, `test_catboost_training_demand.py`,
  `test_cli_train.py`, `test_model_explainability.py`, `test_train_param_routing.py`,
  and `test_modelling_export.py`.
- `tests/test_training_worker_protocol.py` exercises the spawn-picklable training and
  dispersion entrypoints, typed progress/results, manifest validation, and stable
  terminal-reason mapping. Service tests inject a deterministic protocol runner rather
  than relying on fork inheritance or patching child-process objects.

Strategy is overwhelmingly unit/regression: fast, isolated tests per module, heavy use
of `unittest.mock` to avoid exercising real CatBoost/RustyStats/MLflow where feasible,
with a small number of golden-snapshot tests pinning route response shapes. GLM tests
skip cleanly when RustyStats is not installed, matching the production lazy-registration
behaviour in `ALGORITHM_REGISTRY`.

The internal `_split.py` primitives remain covered through direct training tests, while
the public evaluation contract has dedicated plan/config/orchestration suites above.
No public node-config test treats `SplitConfig` as an accepted alternative.

## Canonical modelling artifacts

Under the [canonical-only format policy](../README.md#canonical-only-format-policy),
training reads and writes only the current run-scoped feature contract and artifact layout. It
does not probe for, warn about, or interpret a historical shared contract path. Result and CLI
field names describe their current meaning rather than retaining an obsolete name.

## Unified evaluation and bounded tuning

The product contract and limits are defined in
[the high-level specification](high-level.md#unified-evaluation-and-bounded-tuning).
The implementation seams are:

- `_train_config.py` is the only public node-config parser used by live training and
  script export. It requires `evaluation`, canonicalises optional `tuning`, and rejects
  the retired top-level `split` and `cross_validation` keys before job construction.
- `src/haute/modelling/_evaluation.py` owns exact version-1 strategy/config parsing and immutable plan,
  results and report evidence. Writers use canonical finite JSON and atomic
  same-directory replacement. Planning enforces group/date semantics; readers reject
  unknown keys/versions and validate canonical source membership, ordinary-CV
  partitioning, temporal expanding membership, counts, digests and aggregate values.
- `_tuning.py` owns exact explicit-choice search-space validation, metric direction,
  conditional categorical sampling, fixed/sample merge, deterministic winner/tree-count
  rules, fit limits and the tuning plan/trials/report evidence. Trial artifacts revalidate fit metric names
  and validation-row-weighted aggregates rather than trusting submitted summaries.
  Their `elapsed_seconds` field is the canonical zero marker; wall-clock elapsed is
  job metadata so repeated evidence remains byte-identical.
- `_training_job.py` prepares one source, persists/reloads one evaluation plan, and
  drives all selection/trial fits through `run_evaluation_fit`. The final fit alone
  writes the model/contract, emits model loss history, and computes expensive
  diagnostics. Tuning uses pinned Optuna 4.x with one seeded sequential TPE sampler;
  no candidate is skipped after a fit failure.
- `routes/_training_lifecycle.py` sends one versioned request to one supervised child for
  the complete run. Progress carries planning, trial-fit, trial-complete, final-fit,
  publication and completed phases with bounded one-based trial/fold indices and exact
  fit counts. The parent remains authoritative for cancellation, timeout, admission,
  terminal state and publication.
- The training manifest always contains the model, feature contract and three
  evaluation companions; tuned runs add three tuning companions. Publication verifies
  canonical names, path containment, declared size/digest, strict artifact reloads,
  digest links and response/artifact agreement before atomically replacing the whole
  generation. A non-tuned replacement retires stale tuning companions in that same
  rollback-capable transaction.
- `schemas.py` and `frontend/src/types/trainGuards.ts` independently enforce the strict
  terminal response: completed runs require evaluation, row/fit counts and artifact
  digests must agree, selection/trial aggregates must recompute from persisted fits,
  and the deterministic tuning winner/improvement must be correct. The frontend store
  retains the canonical objects, while `SummaryTab` labels selection estimates,
  final-test metrics, baseline/winner comparison, exact fit counts and the completed
  job's total elapsed time separately.
- `POST /api/modelling/estimate` calls the same planner over the same eligible rows and
  returns only bounded counts/ranges. The editor shows this neutral exact preview once
  enough fields are valid; malformed or incomplete configuration remains a click-time
  validation issue rather than an estimate-warning state.

Focused evidence lives in `tests/test_evaluation.py`,
`tests/test_train_evaluation_config.py`, `tests/test_training_evaluation.py`,
`tests/test_training_response_evaluation.py`, `tests/test_tuning.py`, and
`tests/test_training_tuning.py`, with worker/route/export/publication integration in
`tests/test_training_worker_protocol.py`, `tests/test_modelling_routes.py`, and
`tests/test_modelling_export.py`. Frontend guard, config, preview, summary and progress
suites prove the same canonical vocabulary and bounded lifecycle end to end.
