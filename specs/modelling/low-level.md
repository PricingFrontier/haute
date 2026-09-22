# Modelling — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/modelling/__init__.py` | Public API surface: `FitResult`, `MLflowLogResult`, `TrainingJob`, `TrainResult`, `generate_training_script`, `log_experiment`. |
| `src/haute/modelling/_algorithms.py` | `BaseAlgorithm` ABC, `CatBoostAlgorithm`, `ALGORITHM_REGISTRY`, memory-checkpoint helpers, CatBoost `Pool` construction, GPU fit-thread lifecycle. |
| `src/haute/modelling/_rustystats.py` | `GLMAlgorithm` implementing `BaseAlgorithm` via RustyStats; `prepare_glm_design()` (frame-dtype validation, reference-level translation, interaction resolution); `glm_fit_kwargs()` (fixed or cross-validated penalty, solver controls, robust standard errors); `GLMAlgorithm.glm_result()` over `glm_inference`, `glm_coefficient_rows`, `glm_relativity_rows`, `glm_fit_statistics`, `glm_smooth_term_rows`, and `glm_regularization_summary`; `estimate_glm_dispersion()` profile-likelihood estimation. |
| `src/haute/modelling/_training_job.py` | `TrainingJob` orchestrator — prepare one eligible source, persist/reload its evaluation plan, run selection or tuning fits, perform one deployable final fit, compute diagnostics, stage artifacts, and optionally log once to MLflow; also defines `TrainResult` and intermediate stage types. |
| `src/haute/modelling/_evaluation.py` | Strict version-1 evaluation config, exact development/final-test and validation-fit plan generation, plan/result/report codecs, digest linkage, strategy summaries, and validation-row-weighted aggregation. |
| `src/haute/modelling/_tuning.py` | Strict bounded CatBoost tuning config/search-space validation, seeded trial resolution, winner/tree-count selection, and tuning plan/trials/report codecs. |
| `src/haute/modelling/_train_config.py` | Single source of truth for modelling-node config → training-job kwargs (`build_training_job_kwargs`, `build_train_params`, `parse_evaluation_config`, `parse_tuning_config`, `training_objective_issue`, `default_metrics`, `effective_metrics`), plus the GLM value contract (`GLM_FAMILY_LINKS`, `GLM_CONFIG_KEYS`, `CATBOOST_ONLY_LEVERS`, `is_glm_config`, `glm_params_issue`, `validate_glm_params`). |
| `src/haute/modelling/_glm_terms.py` | Pure GLM term contract shared by the config builder, routes, job, and adapter: `SUPPORTED_TERM_TYPES` and `TERM_KEYS`, dtype classes (`glm_dtype_class`, `MAIN_FITS_BY_CLASS`, `SLOT_FITS_BY_CLASS`), the parameter contract (`validate_term_spec`, `validate_interaction_entry`), the expression grammar (`expression_identifiers`), the schema-free `glm_model_columns()` used for projection demand, `validate_glm_model_columns()` against a real schema and role columns, `resolve_categorical_levels()`, the order-independent `resolve_glm_design()`, and `penalised_smooth_terms()` / `monotone_constraint_terms()`. Imports no RustyStats, so the schema-free half runs during projection planning before any data exists. |
| `src/haute/modelling/_target_check.py` | `training_target_task_issue()` — data-dependent target-column vs task/metric gate returning an actionable message (or nothing when the pairing is valid), keyed on the effective reported-metric set (explicit config metrics or the objective-implied defaults), shared by the train route's pre-dispatch validation and `TrainingJob._prepare_data`. |
| `src/haute/modelling/_split.py` | Internal partition-mask execution used by a final or selection fit. Its `SplitConfig` is a private test seam for direct callers exercising the shared partition/fit machinery; it is not a public modelling-node config contract and is not exported. |
| `src/haute/modelling/_metrics.py` | Primary metric functions and diagnostic data computation (double lift, AvE, residuals, actual-vs-predicted, Lorenz, PDP). |
| `src/haute/modelling/_feature_contract.py` | `FeatureContract` build/save/load/cache, contract comparison, and categorical-level normalisation/validation. |
| `src/haute/modelling/_signature.py` | `build_signature()` — MLflow `ModelSignature` construction with loud dtype/metadata validation, structural Date/parameterised-Datetime mapping, and the explicit no-lossy-Decimal policy. |
| `src/haute/modelling/_candidate_run.py` | The candidate-run contract builder shared by canvas and scripted logging (`CANDIDATE_RUN_CONTRACT_VERSION`, `training_identity_sha256`, `CandidateProvenance` capture including git state, `CandidateArtifacts.require_files`, `build_candidate_run`); see [mlflow-model-registry](../mlflow-model-registry/low-level.md#candidate-run-contract). |
| `src/haute/modelling/_glm_pyfunc.py` | MLflow pyfunc loader module for logged RustyStats GLMs: `_load_pyfunc(data_path)` returns `GLMPyfuncModel`, which scores through haute's own `load_local_model` + `score_frame` path so a GLM loaded with `mlflow.pyfunc.load_model` predicts exactly what haute scoring does. |
| `src/haute/modelling/_charts.py` | Pure-SVG renderers used by model cards. |
| `src/haute/modelling/_model_card.py` | `generate_model_card()` — self-contained HTML assembled for MLflow artifact logging; ordinary training does not persist it beside the model. |
| `src/haute/modelling/_mlflow_log.py` | Destination-aware tracking-backend resolution wrappers over `_mlflow_settings.py` (every wrapper takes `destination`, `""` = the local folder), `log_experiment()`, flavor-aware model/signature logging, diagnostics artifacts, and best-effort model-card logging. |
| `src/haute/modelling/_mlflow_settings.py` | The `[mlflow]` destination inventory in `haute.toml` (tracking_uri, folder): load/validate/save (tomlkit round-trip), tracking-URI form classification via `classify_tracking_uri()`, `validate_destination_key()`, the per-key resolver `resolve_destination()` (raising the `MlflowDestinationUnconfigured` marker for a merely unconfigured key), the three-entry inventory `list_destinations()`, draft resolution for the connection test (`candidate_tracking_config()`), and `node_destination_key()` — the local folder for a node or request that names no destination, so a configured remote is never chosen for it. |
| `src/haute/modelling/_result_types.py` | `ModelDiagnostics` and `ModelCardMetadata` bundles shared by training, MLflow logging, and model-card generation. |
| `src/haute/modelling/_export.py` | `generate_training_script()` code generation for standalone Python training scripts. |
| `src/haute/modelling/_model_export.py` | `MODEL_FILE_SUFFIXES` and `resolve_model_export_destination()`: the Export pane's model-file destination rules (see Save-model-after-the-fact). |
| `src/haute/routes/_export_receipts.py` | Export receipts on training jobs: `record_receipt` (append an MLflow or model-file receipt, keeping the newest `MAX_RECEIPTS_PER_KIND`), `export_receipts` (the wire shape the status route returns), `mlflow_receipt_for_operation` (idempotent retries) and `single_flight_mlflow_log` (one MLflow log per job at a time, else a `409` in-progress refusal). |
| `src/haute/routes/_mlflow_log_errors.py` | The shared HTTP outcomes of the modelling and optimiser MLflow log routes: `require_mlflow_installed` (the shared `503`) and `mlflow_log_http_exception` (configuration `400`, classified remote `502` with an `mlflow_<category>` code and write-specific copy, unclassified `500`; logs category and error type only). |
| `src/haute/routes/modelling.py` | FastAPI router for training, status/cancel, estimates, MLflow log, export, model-cache clear, and dispersion jobs. |
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
  PDP computation receives the complete feature list in training order for every
  prediction, including models with offsets. Feature-importance ranking controls
  only the order of completed chart entries (including per-feature error entries);
  it must never reorder model inputs. Regression coverage uses real CatBoost
  predictions for both mixed categorical/numeric and numeric-only models: the
  latter can silently produce incorrect curves when columns are swapped.
  `CatBoostAlgorithm` and `GLMAlgorithm` implement it.
  Both also expose algorithm-specific methods that `_training_job._compute_metrics`
  probes with `hasattr()` rather than an interface method — `shap_summary` /
  `feature_importance_typed` (CatBoost only), `glm_result` (GLM only).
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
  via `default_metrics()` when omitted), `mlflow_experiment`,
  `mlflow_destination` (`""` = the local folder, else `"databricks"|"server"|"local"`;
  any other value is rejected when logging resolves it),
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
  (`terms`, `family`, `link`, `interactions`, `regularization`, `alpha`,
  `l1_ratio`, `intercept`, `var_power`, `theta`, `offset`); `build_train_params`
  projects those fields into the `TrainingJob.params` mapping consumed by RustyStats.
  An interaction entry is
  `{"factors": [...], "specs": {factor: override}, "include_main": bool}`;
  `specs` holds only `linear`, `categorical`, `bs`, or `ns` overrides. `exclude` never
  narrows a GLM: a GLM feature is in the model exactly when it has a term or is a filled
  interaction factor, and `build_training_job_kwargs` passes `exclude=[]` for GLM. Explicit
  `feature_columns` retains its established precedence over a stale exclusion.
- **`offset`** — maps to RustyStats `exposure=` when the effective link (explicit
  `link`, else `rustystats.formula.get_default_link(family)`) is `log`, and to `offset=`
  otherwise, so the column stays a multiplier under a log link and additive elsewhere;
  RustyStats re-reads the stored column by name at prediction.
- **`monotone_constraints`** — the selected MOD-M09 product lever is a mapping from
  configured feature name to the exact integer `-1` or `1` (Boolean and zero are invalid).
  CatBoost only. `build_training_job_kwargs` passes `None` for GLM, and `TrainingJob`
  rejects a GLM job constructed with the argument; GLM monotonicity lives on each term's
  `monotonicity` key. For CatBoost, `build_training_job_kwargs` removes entries named by
  `exclude` from the effective job mapping without mutating stored config; an empty
  effective mapping becomes `None`. `_validate_monotone_constraints` runs before
  `_split_data`; it requires a mapping with non-empty string keys, rejects names not in
  the final feature list, and accepts only canonical numeric contract dtypes
  (`Int64`/`Float64`). The resulting validated mapping is passed unchanged to
  CatBoost's feature-index translation.
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
- **`MLflowLogResult`** (`_mlflow_log.py`) — `backend` (`"databricks"|"server"|"local"`),
  `experiment_name`, `run_id`, `tracking_uri`, `run_url: str | None`.
- **`GLMInference`** (`_rustystats.py`, frozen dataclass) — `status` (RustyStats'
  `inference_status`, or `singular_design` when a valid status carries a non-finite
  statistic), `valid`, `standard_errors` (`model` or the robust type), `reason`, and the
  statistic arrays when valid. `to_plain_data()` is the `glm_inference` response field.
- **`GLMResultReport`** (`_rustystats.py`, dataclass) — `inference`, `coefficients`,
  `relativities`, `fit_statistics`, `smooth_terms`, `regularization`, and `errors`, the
  failed diagnostics by name.
- **`DispersionEstimate`** (`_rustystats.py`, frozen dataclass) — result of
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
- **`TrainService`** (`src/haute/routes/_training_lifecycle.py`, re-exported by
  `src/haute/routes/_train_service.py`) — wraps a `JobStore`, `JobLifecycle`,
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

1. `POST /api/modelling/train` → `src/haute/routes/modelling.py::train_model` →
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
   `create_admitted_execution_context`. `src/haute/routes/_training_lifecycle.py::TrainService._execute_and_sink`
   is then a **parent supervisor only** — no training data is materialised in the server
   process:
   - it takes the budget envelope with `src/haute/_execution_admission.py::isolated_execution_budget`
     (an admitted `execution_context` is required — `ValueError` otherwise), computes the
     remaining job timeout from `start_time`/`timeout` via `_worker_timing` (a non-positive
     remainder records `timeout()` and raises `ExecutionCancelledError` without launching), and
     builds the worker config — every fallible setup step runs **before** the temp parquet
     exists, so a setup failure cannot orphan one. Only then does it create the parent-owned
     temp parquet (`src/haute/routes/_training_preparation.py::create_training_parquet_path`); from that point
     supervision runs inside a `try` whose `except BaseException` backstop discards the path,
     so the only exit that keeps it is a successful hand-off;
   - it prepares the lineage's inputs and opens the run's
     [seed plan](../caching/low-level.md#seed-plans) itself (`open_seed_plan` with
     `training_seed_plan_request`: `TRAINING_PREP`, the modelling node, its demand), because a
     node's signature signs its prepared inputs; a failure there — input preparation, a corrupt
     snapshot, admission — becomes the same job outcome the child would have reported
     (`preparation_failure_outcome`, shared with the child), and a cancellation propagates.
     The job's deadline (`start_time + timeout`) bounds that preparation (`open_seed_plan(...,
     deadline=)`, which no automatic build or wait outlasts); a failure once the deadline has
     passed, or a plan that opens with no budget left, is the job's `timed_out` and launches no
     child, and the child is given only what preparation left of the budget. The plan's seed
     leases are held until the child has exited, and closing it removes any capture staging a
     killed child left under its token;
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
     `exclude`, `keep_columns`, `required_columns_by_node`, `preamble_supplied`, and the
     plan's `seed_plan` handoff); the child never touches the `JobStore`.
   - the job's `execution_metrics` are the reporting process's metrics carrying the whole
     job's evidence (`ExecutionContext.metrics_with_worker_evidence`): the parent adopts the
     preparation child's input preparation, seeds, captures, and warnings behind its own, and
     the training and dispersion workers' metrics — completed, or reported with a failure
     (the supervisor's `failure_metrics`) — carry that evidence ahead of theirs, so nothing
     preparation read, wrote, or warned about is lost when a later process reports.
   `_training_preparation.py::prepare_training_data` is the child core: it recompiles the
   preamble when supplied, adopts the parent's seed plan (or, called without one, prepares
   inputs and opens its own), runs the upstream pipeline lazily under it with
   `prepare_inputs=False` — seeds read, the modelling node's producer and every join, fan-out,
   and materialisation captured into shared snapshots, no checkpoint directory and no private
   dataframe-cache namespace — holding the plan until the sink completes, derives the version-1
   feature-selection diagnostic from the materialised schema, rejects HTTP
   422/`contract_error` if target/metadata/exclusion rules leave no feature columns, validates
   the required columns actually arrived, projects away excluded columns while retaining
   explicit `feature_columns` even when a stale `exclude` entry also names them (composing
   column drops into an admitted recipe), writes the prepared frame to the parent's temp parquet
   with a single `write_file` call under the context's `training_sink_write` stage — which
   slices the frame directly when sliceable (`strategy="sliced"`), or slices the recipe's input
   when the node carries one (`strategy="input_sliced"`), or sinks natively and records why
   (`strategy="native"`, `native_reason` naming the recipe's reason or `not_sliceable`);
   a row-limit sample discards the recipe and takes the native path with
   `native_reason="row_limit_sample"` unless the sampled frame is itself sliceable;
   a `RecipeEquivalenceError` writes again through the same `write_file` with no recipe, which
   always lands on `native` with `native_reason="recipe_mismatch"` and warning code
   `recipe_mismatch`: the equivalence check runs only on the branch a non-sliceable frame reaches,
   so the second write finds that same frame unsliceable too; and execution metrics record the
   write outcome across four fields (`training_write_strategy`, `training_write_input_slices`,
   `training_write_native_reason`, `training_write_blocking_operator`) — and then runs the
   target/task gate;
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
   (`contract_error`|`memory_limited`|`cancelled`|`error`), the job `message`/`fields`, and the
   `http_status_code`/`http_detail`, computed in the child with the same
   `_http_failure_job_parts`/`contract_error_job_fields`/`_memory_limit_http_exception`
   helpers the in-thread path used, so job records and HTTP payloads are unchanged. A cancelled
   run maps to `cancelled` with `CLIENT_CLOSED_REQUEST_STATUS`, both in the mapper and ahead of
   the worker's own catch-all, so a cancellation is never reported as a pipeline failure. Every
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
   `src/haute/_worker_isolation.py::isolated_worker_memory_detail` labelled `budget.operation` — the
   admitted context's own operation, so the dispersion flow (which admits
   `operation="dispersion_estimate"` and reuses this supervisor) is never mislabelled as
   `training_pipeline`; the child's own memory payload already carries it, since its context is
   built from the same budget; any
   other worker failure logs `training_preparation_worker_failed` and becomes a 500 `error`.
   The child's `execution_metrics` payload is the one persisted on the job. Admission is
   released exactly once — by `_parent_worker_cleanup` after the fit worker, or by the
   `finally` in `_prepare_and_launch_training` on failure — never inside `_execute_and_sink`.
   `_launch_background` builds the `TrainingJob` via `build_training_job_kwargs`
   (`params` overridden by the GPU-adjusted `train_params`), creates the job's owned
   artifact directory with `create_training_artifact_directory()` (a marked `train_*` child of
   `training_artifact_root()`, `<system temp>/haute/artifacts/v1/modelling_training`), and
   starts a daemon supervisor thread around a spawn child. The node's `output_dir` applies only
   to scripted `TrainingJob` runs; canvas training artifacts are server-owned job state that
   users take away with Save model to file or Log run to MLflow.
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
   parent, the supervisor strictly reloads and cross-checks the complete staged set in
   place (`_validate_training_artifacts`), rewrites all artifact paths to their absolute
   locations inside the job's artifact directory, and transitions the job to `"completed"`
   with final `elapsed_seconds` and `artifact_handles["training_artifacts"]` (kind
   `modelling_training_artifacts`, version 1, the directory and each artifact kind's file
   name). No file is moved or replaced: the validated directory is the published,
   immutable artifact set, and ownership passes from parent cleanup to the job record in the
   same job-store critical section as the transition. Cancellation that wins first prevents
   publication, and parent cleanup removes the directory; publication that wins first
   prevents a late cancellation from relabelling the model. Once that job completes, the
   store detaches and cleans the artifact handles of earlier completed training jobs for the
   same pipeline source and node (`node_id` and `pipeline_source` are recorded when the job
   is created), skipping any job whose artifacts are currently held by an export; a skipped
   job's directory is removed when the job is evicted. The registered cleaner removes only a
   validated `train_*` directory directly under the training artifact root, and the server
   lifespan reaps stale marked directories there with the same
   `HAUTE_ARTIFACT_STALE_SECONDS` interval the optimiser uses.
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
omit it retain the constructor-only internal/test-seam split pipeline described above.

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
   Ordinary validation fits forward preparation, iteration and metric-stage progress
   to the outer job. Messages identify the current fit, total fits (including the
   final model), and current iteration/total when the algorithm supplies them.
   Fit-local fractions map into monotonically increasing overall progress. Selection
   metrics never enter the final model's iteration callback or loss chart. Progress
   callbacks retain cancellation and memory checkpoints; a progress-only caller
   receives iteration updates even without an execution context.
   CatBoost callback iteration numbers are already one-based; the displayed count
   and loss-history iteration retain that value, ending at the configured limit.
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
`family == "tweedie"`). `mlflow_destination` is rendered only when the node stores an
explicit key; an Auto node leaves it unrendered so the exported script resolves the
destination in the environment it runs in. The exported script keeps standalone
training's optional logging (a run is logged only when `mlflow_experiment` is set) and
honours the rendered destination: an explicit Local logs locally even when a remote is
configured where the script runs, an explicit destination that is not configured there
fails with `MlflowConfigError` without a remote write, and a destination without an
experiment logs nothing.

### Dispersion estimation (HTTP)

1. `POST /api/modelling/dispersion/estimate` → `src/haute/routes/modelling.py::estimate_dispersion`
   → `TrainService.start_dispersion_estimate(body)`.
2. `_validate_dispersion_config`: reject an unknown `param`, a non-GLM node, an
   invalid family/link combination, a `param` that doesn't belong to the request's GLM
   family (`theta` ⇒ `negbinomial`, `var_power` ⇒ `tweedie`), a missing target column,
   or (via `training_objective_issue`, called with the parameter being estimated
   stubbed to a placeholder value so its own gate doesn't fire) any other
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
   `build_training_job_kwargs` with the parameter being estimated set to a placeholder
   (`_DISPERSION_PARAM_STUBS`: `theta=1.0`, `var_power=1.5`) so the shared config
   machinery can run; the stub value never reaches a fit — the search
   overrides it at every candidate. A spawn child then runs `job._prepare_data`
   (identical to training), validates the model columns against the prepared frame's
   dtypes and role columns, collects only the columns the design needs (including an
   interaction factor whose main effect Include main effects materialises), and calls
   `estimate_glm_dispersion`, which resolves the design with `prepare_glm_design`
   (shared with `GLMAlgorithm.fit`, so the profiled design can never drift from what
   training would actually fit).
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

Destination changes are operation-scoped. Discovery and scoring use clients and
artifact downloads explicitly pinned to the resolved tracking and registry URIs;
discovery and native downloads never change MLflow's process-global destination.
Pyfunc downloads share the fluent-operation lock because MLflow's nested
logged-model lookup still reads global state; they temporarily select the resolved
destination and restore it on every exit. Fluent logging operations
are serialized for their complete lifetime (including run termination and URL
construction), restore their previous tracking/registry state on success or
failure, and preserve the configured environment URI. Saving settings may proceed
while an existing log finishes at its original destination; subsequent operations
resolve the new destination.

The Databricks destination is configured by an environment
`databricks://<profile>` URI (which counts as configured even if the profile's
credentials cannot be loaded or its probe fails — the failure is reported, never
worked around by falling back to host/token variables or another destination),
or, without a profile reference, by the dedicated MLflow pair
`DATABRICKS_MLFLOW_HOST`/`DATABRICKS_MLFLOW_TOKEN`; a profile takes precedence
over the pair and is preserved through settings saves and probes. The pair is
separate from the general `DATABRICKS_HOST`/`DATABRICKS_TOKEN` (and
service-principal) credentials data access uses
([databricks-io](../databricks-io/low-level.md)), because Databricks token
scopes may not let one token cover both: MLflow **never** reads the general
pair, and when only the general pair is set the Databricks entry is unconfigured
with a detail naming the MLflow pair and saying the general pair is not used for
MLflow (copy the values when one token covers both). The pair carries a
personal access token only; a service principal is selected through a profile.

Credentials are **bound**, not assumed. Left alone, MLflow 3.15 would
authenticate a bare `databricks` URI from the general pair by several routes: its
per-request provider chain starts with an environment provider reading
`DATABRICKS_HOST`/`DATABRICKS_TOKEN`; a `DATABRICKS_CONFIG_PROFILE` variable
replaces that chain with the named profile; its default Databricks-SDK path
resolves credentials environment-first and caches its client; and its run and
logged-model artifact repositories first try a bare SDK client built from the
environment before falling back to MLflow's REST artifact repository; and when the
workspace (or `MLFLOW_USE_DATABRICKS_SDK_MODEL_ARTIFACTS_REPO_FOR_UC`) selects the
SDK path for Unity Catalog model artifacts — required on Secure Egress Gateway
workspaces — MLflow builds that client from the resolved host and token but lets
the SDK read every other field from the environment, so a data-access service
principal (`DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET`,
`DATABRICKS_AUTH_TYPE`) would conflict with the token or replace it.
`haute._mlflow_utils.bind_mlflow_databricks_credentials()` therefore,
idempotently and under a lock: pins `MLFLOW_ENABLE_DB_SDK` to `false`
(`os.environ.setdefault`); replaces MLflow's environment credential provider
with one that reads the MLflow pair on every call and **raises** naming both
variables when either is unset, so a bare request never falls through to the
`DEFAULT` profile or any later provider; and replaces the SDK artifact repository
those run and logged-model repositories construct with a shim whose operations
raise, so every upload, listing and download takes MLflow's REST fallback, which
the provider (or the profile) binds; and replaces MLflow's Unity Catalog
model-artifact SDK client factory with one that builds the client from the bound
host and token with `auth_type="pat"`, so that token is its only credential (a
missing token raises rather than falling back to the SDK's default
authentication). The SDK path stays available for workspaces that require it. No
Databricks SDK client ever authenticates with the general pair or an ambient
service principal, and a REST failure propagates to the caller. Providers resolve
credentials on every request, so a repointed `DATABRICKS_MLFLOW_HOST` or a
rewritten profile is followed by the very next request on an existing client.
The binder runs whenever this module's Databricks resolver mints a Databricks
`TrackingConfig`, just before returning it — the one place such a config is
created, which the inventory, the per-key resolver, candidate resolution and
deploy all pass through — so every consumer is bound before its
first Databricks credential lookup, cold process included, while resolving a
server or local destination (or an unconfigured or rejected Databricks entry)
never imports MLflow. It returns immediately when mlflow is not installed and skips
binding when importing MLflow fails (such a process cannot make MLflow
requests), so destination resolution never requires the optional package. The
binding is process-global; `_restore_mlflow_databricks_credentials()` undoes it
so tests stay independent of execution order.

haute supports exactly these two credential forms and rejects configurations
that would silently select different credentials: when Databricks is configured
and the environment explicitly sets `MLFLOW_ENABLE_DB_SDK=true`, or when the pair
form is selected while `DATABRICKS_CONFIG_PROFILE` is set, the Databricks
resolver raises `MlflowConfigError` naming the variable (a profile is selected
with `MLFLOW_TRACKING_URI=databricks://<profile>` instead). The inventory reports
that entry unconfigured with the reason while server and local keep their own
verdicts, a node that chose Databricks fails loudly with that reason, and neither
value is ever silently overridden. Both checks read the environment directly
(no mlflow import). With no Databricks configuration at all these variables are
irrelevant and the entry is simply unconfigured. Its Unity Catalog registry URI
retains the same profile as `databricks-uc://<profile>`. All tracking consumers
share this registry mapping.
Credential-bearing tracking URIs are internal connection values only: training
and optimiser logging responses redact userinfo from both tracking URI and run
URL before returning them to the browser.

`_mlflow_settings.py` owns the tracking-destination domain; `_mlflow_log.py`
exposes the wrappers this component's routes, the optimiser's routes, and the
mlflow-model-registry component all call, so experiment-naming and
tracking-setup logic exists in exactly one place rather than being duplicated
per consumer.

**`_mlflow_settings.py` contract.** The workspace configuration is an
*inventory*, not a selection: three destinations, keyed `"databricks"`,
`"server"`, `"local"` (`DESTINATION_KEYS`, in display order), each independently
configured or not. `resolve_destination(key, project_root)` returns a
`TrackingConfig` (`mode` — the key —, `tracking_uri`, human-readable
secret-free `destination`, `config_source: "toml"|"env"|"default"`) for that
key or raises `MlflowConfigError` naming the missing prerequisite;
`validate_destination_key(value)` accepts the three keys and `""` and
rejects anything else naming the choices. The empty string is never a
destination for `resolve_destination`; `node_destination_key(value)` validates
a node's or request's value and maps `""` to `"local"`. **There is no automatic
choice:** a node that names no destination uses the local folder whatever the
environment configures, and Databricks or an MLflow server is used only when
the node names it, so adding credentials never retargets an existing node and a
remote that is not working is reported, never replaced by another destination.
`list_destinations(project_root)` returns the three
`DestinationEntry` rows (`key`, `configured`, `destination`, `config_source`,
`detail`) in display order; an unconfigured entry carries its prerequisite in
`detail`. Per key:

1. **`databricks`** — configured by `MLFLOW_TRACKING_URI=databricks://<profile>`
   (a syntactically valid profile reference; `destination` is that reference;
   it counts as configured even when the profile's credentials cannot be
   loaded — the failure surfaces from the probe or the load, never as a
   fallback to environment credentials or another destination), or, without a
   profile reference, by both `DATABRICKS_MLFLOW_HOST` and
   `DATABRICKS_MLFLOW_TOKEN` (`destination` is the host). A profile takes
   precedence over the pair. A partially set pair names the missing variable;
   nothing set names both options and, when the general
   `DATABRICKS_HOST`/`DATABRICKS_TOKEN` pair is set, says it is not used for
   MLflow. The pair form with `DATABRICKS_CONFIG_PROFILE` set, and a configured
   Databricks with `MLFLOW_ENABLE_DB_SDK=true`, raise `MlflowConfigError`. This
   key never reads `haute.toml`.
2. **`server`** — configured by `[mlflow] tracking_uri` (`http(s)://` with a
   non-empty host and **no embedded credentials** — secrets belong in `.env`,
   never in the checked-in toml; `config_source="toml"`), else by an `http(s)`
   `MLFLOW_TRACKING_URI` (`config_source="env"`); `haute.toml` wins where both
   are present. Otherwise the error names both prerequisites.
3. **`local`** — always resolves: `[mlflow] folder` (relative to the project
   root, `config_source="toml"`), else a `file:` URI or plain filesystem path
   (including a Windows drive path) in `MLFLOW_TRACKING_URI`
   (`config_source="env"`), else `<project>/mlruns` (`config_source="default"`).

`MLFLOW_TRACKING_URI` therefore seeds the inventory entry matching its form
(`classify_tracking_uri()`): a `databricks://` profile seeds databricks; an
`http(s)` URL seeds server only when the toml has no `tracking_uri`; a `file:`
URI or path seeds the local folder only when the toml has no `folder`. Any
other scheme (for example `sqlite:`) raises `MlflowConfigError` naming the
unsupported scheme from the server and local resolvers (databricks checks the
`databricks` prefix textually and is unaffected), so resolving the server or the
local folder — including a node that names no destination — fails loudly with that
reason. The "not configured" marker subclass `MlflowDestinationUnconfigured` lets the
inventory tell a merely unconfigured entry from a broken one. The retired
single-mode `mode` key, like any other unknown key, is rejected by
`load_mlflow_settings()` naming the key and the allowed keys
(`tracking_uri`, `folder`); there are no users to migrate. Validation errors
never echo a rejected URI — a malformed or credential-bearing value must not
leak through its own error message. An unparseable URI or an invalid port
(from toml or env) is an `MlflowConfigError`, never an uncaught `ValueError`.

Server resolution re-attaches matching environment credentials:
`haute.toml` persists the credential-free destination, and when
`MLFLOW_TRACKING_URI` is a credentialed server URI whose redaction equals
the stored URI, the env value wins for connecting (destination stays
redacted). Saving the displayed configuration therefore never silently
drops working authentication — the toml carries the non-secret selection,
`.env` carries the secret. `candidate_tracking_config(key, settings, root)`
resolves an *unsaved* draft for one key exactly as a save would make it:
`server` uses the draft `tracking_uri` (validated, env re-attachment
included; an empty draft names the field), `local` uses the draft `folder`
or, when blank, the folder a bare save would keep (via the shared
`_effective_settings()`), and `databricks` ignores the drafts and resolves
the environment — all without writing `haute.toml`; the connection-test
endpoint uses it so a draft is probed as exactly the configuration a save
would produce.

`load_mlflow_settings()` reads the stored section (tomllib);
`save_mlflow_settings()` validates the same rules and rewrites only the
`[mlflow]` table via tomlkit, preserving every other section, comment, and
layout byte-for-byte. `tracking_uri` and `folder` are saved independently:
an empty `tracking_uri` clears the server key, and an empty `folder`
persists the currently *resolved* local folder (a stored folder keeps its
spelling, an env-derived folder is persisted as its absolute path, else the
default `mlruns`), so saving an unchanged env-derived local configuration (a
`file:` `MLFLOW_TRACKING_URI`) can never silently redirect logging or
discovery to `./mlruns` — and the runs already logged in that folder stay
discoverable through `/api/mlflow/experiments` after the save.
Before writing, the resolved target path must remain inside the project
root (case-folded, fully resolved containment, mirroring
`haute._sandbox.validate_project_path`): a `haute.toml` that is a symlink
escaping the project is refused with `MlflowConfigError`, and the external
target is left untouched. When no explicit `project_root` is passed, every
entry point (load/save/resolve) uses `haute._sandbox._get_project_root()`,
so the settings surface, the discovery routes, and the logging paths all
agree on which `haute.toml` and default runs folder they mean. A
credential-bearing env `MLFLOW_TRACKING_URI` (userinfo in an `http(s)` URL)
still *functions* — the full URI is passed to the MLflow client — but every
displayed field derives from `redact_uri()`, which strips userinfo, so
`TrackingConfig.destination` and everything built from it never contain a
secret.

**`_mlflow_log.py` wrappers** (every wrapper takes `destination: str = ""`,
where `""` is the local folder and any value outside the three keys is rejected via
`validate_destination_key` before resolution):

- `resolve_tracking_backend(destination="")` — returns `(tracking_uri, mode)`
  from `resolve_destination(node_destination_key(destination))`; propagates
  `MlflowConfigError`.
- `resolve_experiment_name(*, explicit, node_label, backend, destination="")` —
  the experiment the request names (the node's current setting), else a
  backend-aware default (`/Shared/haute/{label}` for Databricks — the
  `/Shared/` prefix is Databricks-specific — and the bare `{label}` for server
  and local alike). There is no job-config tier: a training- or solve-time
  snapshot of `mlflow_experiment` is never consulted, so clearing the field
  logs to the default the field shows. When `backend` is not supplied it is
  resolved from `destination`, so the default follows the node's effective
  destination.
- Training `log_experiment`, the optimiser MLflow log route and deploy select
  their experiment through `_mlflow_utils.set_experiment_creating_workspace_folder`
  rather than calling `mlflow.set_experiment` directly. A Databricks experiment is a
  workspace object whose folder must already exist, which `/Shared/haute` does not in
  a fresh workspace. When the fluent tracking URI is Databricks (`databricks` or
  `databricks://<profile>`), the name is an absolute workspace path with a parent
  folder, and `mlflow.get_experiment_by_name` finds no experiment, the helper first
  sends `POST /api/2.0/workspace/mkdirs {"path": <parent>}` through MLflow's
  `http_request` with `get_databricks_host_creds(tracking_uri)` — the bound MLflow
  credentials, never the general pair — then calls `mlflow.set_experiment`. An existing
  experiment and every non-Databricks backend skip the folder request. A failed folder
  request is classified with `classify_mlflow_error` and logs
  `databricks_experiment_folder_create_failed` with the folder, experiment, category and
  error type (never the exception text). Authentication and connectivity failures
  propagate unchanged for the caller's own classification; a permission refusal, a
  missing resource or an unclassified refusal raises `MlflowRemoteError` with that
  category and a message naming the folder and experiment and telling the user to create
  the folder or choose a writable path, which the log routes return as a `502`
  (`mlflow_<category>`). No experiment is created.
- `configure_mlflow_tracking(destination="")` — resolves the requested
  destination, calls `mlflow.set_tracking_uri`, sets
  `MLFLOW_ALLOW_FILE_STORE` (setdefault) for local, and sets the registry URI
  **explicitly for every backend** —
  `databricks-uc` (retaining any `://profile`) for Databricks, the tracking URI
  itself for server/local. The SDK's tracking setter must preserve the original
  `MLFLOW_TRACKING_URI` environment value so it cannot overwrite configuration.
  The registry URI is process-global, so a leftover `databricks-uc` from an
  earlier destination must never capture local/server registrations. The
  fluent setup runs inside `mlflow_fluent_operation()` for the entire logging
  lifecycle. Discovery and native artifact downloads use explicitly pinned clients
  and tracking URIs without changing fluent state; pyfunc downloads use the same
  operation scope as logging for the SDK's nested model lookup.
- `build_run_url(backend, experiment_name, run_id)` — resolves the numeric
  `experiment_id` via `mlflow.get_experiment_by_name` (run URLs require the
  numeric id, so the name is resolved first) and returns the Databricks
  workspace URL for databricks mode, `{tracking_uri}/#/experiments/{id}/runs/{run_id}`
  for server mode — with the tracking URI passed through `redact_uri()`, so
  a credential-bearing env URI never reappears in a displayed run link —
  and `None` for local mode or on a failed experiment lookup (logged, not
  raised).

`log_experiment(..., destination="")` resolves its backend through
`configure_mlflow_tracking(destination)` inside the fluent operation.
`src/haute/routes/optimiser.py` calls the naming/setup/url helpers with the
request's destination; `src/haute/routes/mlflow.py` (mlflow-model-registry)
consumes the inventory, per-key, and candidate resolvers and `node_destination_key` plus the
settings load/save surface for its destinations/settings/test-connection
endpoints. `TrainingJob._log_to_mlflow` passes `mlflow_experiment` and
`mlflow_destination` straight through to `log_experiment()`, since that is
the programmatic-API path where the caller has already chosen both values;
`TrainingJob` rejects `mlflow_experiment` without an explicit evaluation
contract at construction, because a candidate run publishes the evaluation
plan and results. Rejected
alternatives: routing the optimiser's MLflow logging through this component's `log_experiment()`
(rejected — the optimiser logs a different artifact shape, solver params/frontier CSV/
`optimiser_result.json`, vs. training's model diagnostics/SHAP/model card, and forcing both
through one function would need fake empty metadata or `if is_optimiser:` branches); moving
`resolve_tracking_backend()` itself into `_mlflow_utils.py` (rejected as a large-diff,
zero-behaviour-change move not worth it standalone); auto-detecting a running local MLflow
server or spawning one as a managed viewer process (rejected as a product decision — local
mode's success surface is deliberately just the run ID; no subprocess lifecycle is owned by
haute).

### MLflow-log-after-the-fact

Connection availability reporting is not this router's concern: the
former `GET /api/modelling/mlflow/check` route and its `MlflowCheckResponse`
schema are deleted, and the UI reads `GET /api/mlflow/destinations` (owned by
[mlflow-model-registry](../mlflow-model-registry/low-level.md)) instead.

GUI logging is manual. Live training (`_training_lifecycle.py`) builds its
job kwargs through `build_training_job_kwargs` and then sets
`mlflow_experiment=None`, so a run started from the canvas is logged only by
the post-training "Log run to MLflow" action; the node's `mlflow_experiment`
still seeds that action's experiment name and the standalone export. The
MODEL node config gains the optional `mlflow_destination` field (absent or
`""` = the local folder; `"databricks"|"server"` when the node chooses a
remote) — declared on the `ModellingConfig` TypedDict and
`MODELLING_CONFIG_KEYS` (so it round-trips through save, parse, and codegen),
classified as node config for the execution cache, and rejected by
`validate_node_config` and `build_training_job_kwargs` for any other value,
including `"local"`: the local folder has one stored representation, no key.
`build_training_job_kwargs` emits `mlflow_destination` (`""` when absent) and `TrainingJob` carries it
into `log_experiment(destination=...)`; choosing a destination alone never
enables logging.

`POST /mlflow/log` (`LogExperimentRequest`: `job_id`, `experiment_name`, and
`destination: Literal["", "databricks", "server", "local"] = ""`)
looks up a completed job's cached `TrainResponse`. The request's `destination`
is the single source of truth for where the run goes: `""` (or an omitted
field) means the local folder, a key means that destination, and
the job's training-time `mlflow_destination` snapshot is **never** consulted —
the node's choice can change after training, and the UI must never show one
destination while the run goes to another (the frontend always sends the
node's current value). `experiment_name` follows the same rule: the request's
value is the node's current setting, and a blank one uses the backend default
(`resolve_experiment_name`), never the job's `mlflow_experiment` snapshot. An
unknown value is a `422` from request validation before any work; a destination that is not configured resolves first
(`resolve_tracking_backend(body.destination)`) and fails with `400` carrying
the prerequisite-naming `MlflowConfigError` detail, writing nothing. The
experiment-name default follows that resolved backend. If the saved
model file exists, the route reloads its persisted feature contract from disk (via
`load_contract_cached`, next to the model at `model_contract_filename(model.stem)`) —
never re-derives feature metadata from the job payload, because a model file without a
contract is treated as an error condition rather than a reason to guess Float64 for
everything; builds `ModelDiagnostics`/`ModelCardMetadata` (including the GLM fields);
calls `log_experiment(destination=body.destination)` via `run_in_threadpool` to keep the
event loop responsive. The model, feature contract, and evaluation/tuning artifacts all come
from `require_training_artifacts(job)`; the feature contract is mandatory and its metadata is
the only source of the signature — the route never substitutes job-payload features. Nothing on this path registers a model: `log_experiment` has no
registry parameter and never calls `mlflow.register_model`, and a modelling or optimiser node
config carrying the removed `model_name` key is rejected by `reject_removed_config_keys` with
guidance that trained-model registration happens outside haute. The response's `backend` and `tracking_uri` name the
destination the run actually went to.

Failures map through `routes/_mlflow_log_errors.py`, shared with the optimiser log route.
`require_mlflow_installed()` runs first, so a missing MLflow package is the shared `503` with
`MLFLOW_NOT_INSTALLED_DETAIL` on every MLflow route. A `HauteValidationError` from haute's own
artifact checks (an unloadable model, an incomplete contract, a missing artifact) is a `400`
with its message, raised before any run exists. `mlflow_log_http_exception(exc, job_id=...)`
maps an `MlflowConfigError` to `400` with its message; classifies anything else with
`haute._mlflow_errors.classify_mlflow_error`, logging `mlflow_log_failed` with only the
category, error type and job id; returns a classified remote failure as `502` with detail
`{"error_code": "mlflow_<category>", "message": ...}` — an `MlflowRemoteError`'s own message, or
the write-specific copy in `MLFLOW_LOG_FAILURE_MESSAGES` for authentication, permission,
missing_resource and connectivity; and returns an unclassified failure (including a local disk
error, which is never connectivity) as the generic `500`.

**Export receipts.** Every successful export is recorded on the training job so the Export
pane can say where a result went after a pane switch or a browser reload.
`LogExperimentRequest.operation_id` (optional; 1–128 characters of letters, digits, `_` and
`-`) identifies one user action. Before any work the route returns the recorded outcome when a
receipt with that operation ID exists, so a retry after a lost response never creates a second
run; the check repeats inside `single_flight_mlflow_log(job_id)`, which admits one log of a job at
a time and refuses a concurrent one with `409` detail `{"error_code": "mlflow_log_in_progress",
...}`. A successful log records an `MlflowExportReceipt` (`operation_id` — the request's, or a
generated one —, request `destination`, `backend`, `experiment_name`, `run_id`, credential-redacted
`run_url` and `tracking_uri`, UTC `logged_at`) and the response repeats `operation_id` and
`logged_at`. A successful save records a `ModelFileExportReceipt` (`path`,
`feature_contract_path`, `saved_at`). A failed export records nothing. Receipts keep the newest
`MAX_RECEIPTS_PER_KIND` (20) per kind, are written under one lock, and `GET /train/status/{job_id}`
returns them as `export_receipts: {mlflow, model_files}`, oldest first.

### Candidate runs (`_candidate_run.py`)

Every training run haute logs is a *candidate run* satisfying the versioned contract in
[mlflow-model-registry](../mlflow-model-registry/low-level.md#candidate-run-contract). Both
entry points build it with `build_candidate_run`: the canvas log route from the completed job
record and `TrainResponse`, and scripted `TrainingJob._log_to_mlflow` from its `TrainResult`.
`CandidateProvenance` (job id, node label, UTC `trained_at`, `training_identity_sha256`, and
optional node id, project-relative pipeline, git commit and dirty flag) is captured when training
completes: the canvas lifecycle captures it outside the store lock just before publishing
completion and stores its plain data on the job record as `provenance`; `TrainingJob` captures it
when it logs. `training_identity_sha256(kwargs)` is the SHA-256 of the canonical JSON of the
TrainingJob training inputs (target, weight, exclusions and feature selection, algorithm, task,
params, evaluation, tuning, metrics, loss, variance power, offset, monotone constraints, feature
weights, categorical levels) — the same kwargs the canvas worker and a script pass to
`TrainingJob` — and raises on values with no canonical JSON form. Git state comes from the project
root and is omitted (never guessed) outside a repository. `build_candidate_run` loads the feature
contract from the artifact set; it is the only source of the signature metadata (features, types,
categorical features, target, task, offset column) and a contract without complete feature types
raises `HauteValidationError`. It also builds the run name, the `haute.*` tags, the params, and the
evaluation-set-namespaced metrics, all as pure data.

`log_experiment(*, experiment_name, candidate, destination="", check_cancelled=None)` first
requires every candidate artifact file to exist, then configures tracking, sets the experiment,
and starts the run with the candidate's name and tags. It logs params (truncated to MLflow's 500
characters, batched by 100) and metrics, the model with its signature, the feature contract at
the run root, the diagnostics JSON, and the evaluation/tuning evidence. A `.cbm` model must load
as the contract's CatBoost task — a load failure raises before `mlflow.catboost.log_model` — and is
logged through the native CatBoost flavor as the LoggedModel named `model` (MLflow 3's `name=`,
never the deprecated `artifact_path=`; `runs:/<run>/model` still resolves it). A `.rsglm` model is
logged as a pyfunc with `loader_module="haute.modelling._glm_pyfunc"` and the model file as
`data_path`; its `_load_pyfunc` returns a model whose `predict` scores through haute's own
`score_frame` RustyStats path, so `mlflow.pyfunc.load_model` and a Model Score node predict the
same values. Any other suffix raises. **Every** flavor also logs the native file at the run root:
mlflow 3.x stores logged models as LoggedModel entities outside the run's artifact listing, so
Haute's run-artifact discovery (`_find_cbm_artifact` / `_find_rsglm_artifact`) would otherwise
never see a freshly logged model. Model-card generation failure does not fail the log: it logs
`model_card_generation_failed` with the error type and sets the tag `haute.model_card=unavailable`.
Scripted runs log only when the script passes `mlflow_experiment` to `TrainingJob` (a visible
argument in the script's own code); `haute train` runs the script's `job` and so honours it too,
while canvas training logs only through the Export pane. **Decision (MLF-E09):** exported scripts
keep this implicit logging rather than emitting an explicit logging call after `job.run()`.
`haute train` imports the script as a module (so its `__main__` block never runs) and calls
`job.run()` itself, so an explicit call in `__main__` would silently stop every `haute train` run
from logging, while the `mlflow_experiment` argument is already an explicit, visible opt-in in the
generated code. `tests/test_cli_train.py` proves `haute train` logs a contracted candidate run for
an exported script that names an experiment. Both log calls run inside
`runtime_environment_inference()` from `haute._mlflow_utils`, which disables MLflow's
uv-project auto-detection and uv-file logging for their duration: the model's recorded
`requirements.txt` therefore describes the interpreter that trained it, never a `uv.lock` that
happens to sit in the working directory (MLflow 3.15 would otherwise `uv export` that lock and
report spurious dependency mismatches). Previous values of the two MLflow variables are
restored afterwards.

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

### Save-model-after-the-fact

The MODEL node config gains the optional `model_export_path` string — the Export pane's
file path, absent or `""` meaning the frontend's computed default — declared on the
`ModellingConfig` TypedDict and `MODELLING_CONFIG_KEYS` so it round-trips through save, parse
and codegen, and classified as node config for the execution cache like its MLflow siblings.
Training never reads it; `build_training_job_kwargs` does not emit it.

`haute.modelling._model_export` owns the destination rules, mirroring
`resolve_data_output_path` for a file Data Output with `models/` in place of `outputs/`.
`MODEL_FILE_SUFFIXES` (`catboost` → `.cbm`, `glm` → `.rsglm`) is the single extension map;
`TrainingJob` names its model file from it. `resolve_model_export_destination(raw_path, *,
model_suffix, project_root)` normalises separators (rejecting NUL bytes), places a path with no
separator under `models/`, and appends `model_suffix` when the path has no suffix; that
forward-slash spelling is the display path. It rejects reserved device names
(`MalformedRuntimePathError`) and drive-relative Windows paths, resolves the path against the
project root (an absolute path stands alone), and raises `RuntimePathOutsideProjectError` when
the result leaves the root. `suffix_mismatch` is true when an explicit suffix differs from
`model_suffix` ignoring case. Routes map these errors through `runtime_path_http_exception`
(`400` malformed, `403` outside) and resolve against `_get_project_root()`.

`POST /save/destination` (`ModelSaveDestinationRequest`: non-empty `output_path`, `algorithm:
Literal["catboost", "glm"]`) returns `ModelSaveDestinationResponse` (`path` display spelling,
`suffix_mismatch`) and writes nothing.

`POST /save` (`SaveModelRequest`: `job_id`, non-empty `output_path`, `overwrite: bool = False`)
looks up a completed job (`require_completed_job`, so an unknown job is `404` and a job that has
not completed is `400`). Both export routes read a job's artifacts through `require_training_artifacts(job)`, which
validates the job's `training_artifacts` handle and holds the set for the duration of the
export (`hold_training_artifacts`), so supersede pruning cannot remove files mid-export. A
missing handle, an invalid handle, or any missing file in the set fails with `410` and
detail `{"error_code": "training_artifacts_unavailable", "message": ...}` telling the user to
retrain; nothing is written or logged. The destination resolves with
the source model's suffix. A suffix mismatch is a `400` naming the required extension — the
path is never silently rewritten. The source is the job-owned copy under the server's temporary
artifact root, so a project destination can never be the source file. The contract destination
is `model_contract_filename(destination.stem)` beside the model. The existence check and the
publication run under `_model_destination_lock(contract path)`, a process-wide lock per
normalised contract path (every save that could write either file of a pair shares that path),
so two concurrent saves can neither both pass the check nor publish one job's model beside
another job's contract: the second waits and then sees the first's files. When `overwrite` is
false and either destination file exists, the route fails with `409` and detail
`{"error_code": "model_file_exists", "message": "Model file already exists: {display path}"}`
and writes nothing; the Export pane's confirmation, dispatched on that `error_code` rather than
the HTTP status, resends with `overwrite: true`. The route creates the parent
directory and calls `atomic_copy_files` from `haute._file_ops` with the contract pair then the
model pair. It is transactional across the pair: every copy is staged as a unique sibling temp
file before any target changes (each temp path is registered for cleanup before its copy starts,
so a copy that fails part-way leaves nothing behind); each existing target is moved to a unique sibling backup before
its staged copy replaces it (with the same Windows sharing-violation retry as
`atomic_write_bytes`); a failure at any point restores every backup and removes every staged
copy and newly published target, so the destinations are either all new or all unchanged.
Backups are deleted only after every replacement succeeds; a failed post-commit backup deletion
is logged and does not fail the save. `OSError` is a `500`
with a filesystem-error detail that names no path; anything else is the sanitized internal
`500`. The response is `SaveModelResponse`: `status="ok"`, the display `path`, and the display
`feature_contract_path` (the display path's sibling contract name). The route is a synchronous handler, so FastAPI runs the
potentially large copy on its threadpool.

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
- GLM model columns (native keys, expression identifiers, interaction factors) are
  resolved by `glm_model_columns` in `src/haute/modelling/_glm_terms.py` for projection
  demand and validated by `validate_glm_model_columns` against the exact unprojected input schema
  (`resolve_training_input_schema`, schema-only lazy build) before a training or
  dispersion job is created (422), and again against the loaded frame in `TrainingJob`
  and the dispersion worker.
- `resolve_glm_design` (`_glm_terms.py`) resolves each product factor as override →
  the column's single inheritable main effect → dtype-class default, independent of
  card order. Monotone splines, monotone linear or `bs` mains, level-restricted
  categoricals, frequency encodings, and columns with several main effects need an
  explicit slot fit. Product target encoding registers one main effect whose settings
  every card agrees on; Include main effects materialises the single spec its cards
  agree on; slots are then checked against the effective native main effects
  (categorical only over categorical, never linear, splines, or target encoding over
  categorical); duplicates are checked per encoding mode and factor set. RustyStats
  always receives `include_main=False`, because its own flag duplicates existing main
  effects. Unset factor slots (`""`) are
  filtered out before the two-factor minimum, so the config panel's freshly added
  `{"factors": ["", ""]}` row is a skip rather than a fit input. Tests assert design
  columns through `dict_to_parsed_formula` and `InteractionBuilder`.
- A Negative Binomial GLM (`family="negbinomial"`) requires an explicit `theta` before
  training or export can proceed — `training_objective_issue` gates it identically to
  Tweedie's variance power, and RustyStats 0.9 itself refuses to fit without one.
  `estimate_glm_dispersion` exists specifically to give the user a principled value to
  set rather than guessing.
- `glm_fit_kwargs` passes a positive `alpha` as a fixed penalty with `l1_ratio` and never
  `regularization` (RustyStats ignores `alpha` whenever `regularization` is set). An absent
  or zero `alpha` cross-validates with the configured `cv_folds`, `cv_selection`, and
  `cv_seed`, which the objective gate requires, so the selected penalty is reproducible.
  Regularisation is refused with penalised smooth splines, and robust standard errors with
  regularisation, monotonicity, or penalised smooth splines.
- CatBoost's offset baseline is only honoured when supplied through a `Pool`; a
  bare-matrix `predict()` call silently scores from baseline 0 in CatBoost itself, so
  `CatBoostAlgorithm.predict()` always wraps in a `Pool` whenever an offset is
  configured (`_extract_offset_baseline` raises if the column is missing). The baseline
  is `log(offset)` for `Poisson` and `Tweedie` losses and the offset verbatim otherwise;
  `haute_offset_link` records the transform beside `haute_offset_column`, and a model
  recording a column without a link is refused. The job's offset link and the link
  stamped at fit both follow the effective loss (`TrainingJob._catboost_loss_function`).
- GLM prediction keeps the offset column inside the frame handed to RustyStats rather
  than transforming it in Python — RustyStats owns the fit-time offset transform (e.g.
  log for an exposure column under a log-link family) and reapplies it identically at
  predict time. A log link passes the column as `exposure=`, stored on `_exposure_spec`;
  other links pass `offset=`. `rustystats_offset_column` reads either spec for the loader
  and scorer. Training and log-link scoring refuse null, zero, or negative exposure.
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
  `src/haute/routes/modelling.py::export_script` and by `TrainService._validate_config`.
- **`FeatureMismatchError`** (`haute.errors.HauteError` subclass) — feature-contract
  structural problems: missing/unknown top-level fields, wrong field types, hash
  mismatch (edited/corrupted file), invalid `categorical_levels` declarations, and
  train-vs-score disagreement via `assert_contracts_match` (names the offending field
  and shows expected vs. actual).
- **GLM result diagnostics** — `GLMAlgorithm.glm_result` catches each failing GLM
  diagnostic (unknown inference status, non-finite coefficient, relativity or bound
  overflow, non-finite fit statistic or smoothing result) by name; `TrainingJob` records
  each in `diagnostics_errors`, never failing the whole run. Invalid inference is not a
  failure: rows carry null statistics and `glm_inference.reason` says why.
- **`ValueError`** — the dominant validation error across the package: invalid
  evaluation/tuning evidence, missing required columns, a target/task/metric mismatch
  (`training_target_task_issue`), an empty training DataFrame, all-non-finite metric
  inputs, a missing offset column at predict time, or GLM terms referencing absent
  columns. A metric failure (`ValueError`, `TypeError`, or an arithmetic error —
  never `MemoryError`, which keeps its memory taxonomy) in
  `TrainingJob._compute_metrics` is re-raised as a `HauteValidationError` naming the
  evaluation set, target column, task, and requested metrics, chaining the original.
  `_run_training_process_job` maps a `HauteValidationError` from `TrainingJob.run()`
  to a `contract_error` failure payload carrying its message verbatim (distinct from
  the catch-all `error`); a bare `ValueError` — a dependency's, or a deliberately
  plain result-shape check — is a system fault that takes `error` with
  `_friendly_error`'s type-only public message, the raw text surviving only in the
  diagnostic `error` field. The parent supervisor persists that terminal reason.
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
  `user_message` field (`src/haute/_worker_protocol.py::WORKER_USER_MESSAGE_FIELD`) only for
  deliberately curated, haute-authored wording: the cancelled/memory-limit/
  contract-error/bounded-memory branches of `_known_training_worker_failure`
  (the memory-limit branch authors its message from the exception's structured
  attributes via the shared `src/haute/routes/_memory_messages.py::memory_limit_user_message`
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
  (`glm_inference`, `glm_coefficients`, `glm_relativities`, `glm_fit_statistics`,
  `glm_smooth_terms`, `glm_regularization`).
- Categorical PDP grids retain missing source levels as JSON `null`, with the prediction
  computed for that missing level. The frontend response contract accepts these levels
  and labels them `(missing)`; numeric grid values remain non-null.
- `_compute_metrics` reports `Computing SHAP values` only immediately before an
  available `shap_summary` call, then reports `Computing loss-based feature
  importance` before constructing its pool/calling `feature_importance_typed`.
  Both progress callbacks run outside the optional-diagnostic exception guards,
  so cancellation between stages propagates rather than being recorded as an
  optional diagnostic failure. Existing 1,000-row SHAP sampling and full-partition
  loss importance are unchanged. Targeted tests pin stage order, the absence of
  a SHAP stage for algorithms without it, and cancellation before loss importance.
- **MLflow logging errors** — `_log_model_card` inside `log_experiment` is wrapped in
  `try/except Exception: logger.warning(...)`, so a model-card bug never fails an
  otherwise-successful experiment log; `build_run_url` similarly catches and returns
  `None` with a debug log rather than failing the whole call.
- **Misconfigured tracking destination** — `resolve_tracking_config()` raises
  `MlflowConfigError` (an explicitly selected mode with a missing
  prerequisite, an invalid or unsupported tracking URI form, or a malformed
  `[mlflow]` section). Logging and discovery consumers propagate it loudly;
  only the status/settings endpoints (mlflow-model-registry) catch it to
  report the reason. It never downgrades to a different mode.
- **Unsupported MLflow Decimal signature** — `_signature._map_dtype` raises
  `ValueError` before model logging, explains that MLflow 3.x has no exact
  Decimal scalar, and directs the author to an explicit upstream `String` or
  `Float64` cast. The original Decimal descriptor is named; no value is
  inspected or coerced.
- **Dispersion estimation** — `_validate_dispersion_config` raises `HTTPException(400)`
  for an unknown parameter, a non-GLM node, a family/link mismatch, a parameter/family
  pairing mismatch, a missing target, or any other incomplete training objective;
  raised before any job record is created. Inside `estimate_glm_dispersion`,
  `HauteValidationError` covers an unrecognised or mismatched `param` and "every
  candidate fit failed"; inside the process worker, `HauteValidationError` maps the job
  to `contract_error` with its message verbatim, `ExecutionCancelledError` maps to
  `cancelled`, `MemoryError` to `memory_limited`, and any other exception — a
  dependency's plain `ValueError` included — maps to `error` via `_friendly_error`'s
  type-only public message, the same taxonomy the training entrypoint uses.

## Testing

- `tests/test_service_domain_boundaries.py` keeps the training facade explicit,
  the extracted service-module graph acyclic, and lifecycle state ownership out
  of preparation, evaluation, worker-protocol, and artifact leaves.
- `tests/test_target_task_gate.py::TestWorkerBoundaryUserMessage` pins the provenance
  rule at the worker boundary: a `HauteValidationError` — the metric-stage wrapper
  included — is promoted verbatim as a `contract_error`, while a dependency's plain
  `ValueError` takes the type-only `error` fallback;
  `tests/test_training_worker_protocol.py::test_dispersion_worker_maps_estimator_failures`
  proves the dispersion worker applies the same taxonomy, keeping the dependency text
  out of the public message and in the diagnostic `error` field.
- `tests/test_training_seeding.py` drives preparation through the supervising parent with the
  child in process: a second run seeds the first run's capture with an equal frame and builds
  nothing; a batch Model Score is scored once; a stale snapshot is never seeded; an `A → B`
  chain seeds only `B` and never reads `A`; a refreshed root is seeded below a stale chain;
  branches recording different generations recompute from sources; a single cached branch seeds
  its recorded ancestor, and recomputes both branches once that ancestor is cleared; a child
  whose seed is refreshed and cleared before it starts reads the leased rows; the evaluation
  preview seeds a training capture and a training run widens the preview's; no checkpoint
  directory or dataframe-cache entry is written; a child stopped, timed out, or killed at
  its memory cap leaves no capture staging; preparation time comes out of the child's budget,
  and preparation that ends past the job deadline — or fails after it — is the job's
  `timed_out`; a plan-opening failure in the parent (cancellation, input-preparation contract
  error, corrupt cache, admission refusal) is classified without launching the child, removes
  the parquet, and releases admission once; the job's metrics keep the parent's input
  preparation alongside the child's seeds and captures; and a training job run to completion
  through the routes keeps preparation's captures and seeds. A failed fit
  (`tests/test_training_worker_protocol.py::test_failed_fit_keeps_the_jobs_preparation_evidence`)
  and a failed or completed dispersion estimate (`tests/test_modelling_routes.py`) keep them
  too. The single `write_file` training write is covered across its strategies: a modelling node
  directly off a data input writes `sliced` with rows, order and schema equal to the native
  result; a modelling node over a chunk-local filter parent writes its prepared parquet
  `input_sliced` across several slices, reporting `training_write_strategy` and
  `training_write_input_slices` through the worker path; column exclusions compose into the
  write recipe; a row-limit sample takes the native path with
  `training_write_native_reason="row_limit_sample"` while a sliceable sample records no native
  reason; a mismatched recipe degrades to native with
  `training_write_native_reason="recipe_mismatch"` and records a `recipe_mismatch` warning;
  mid-write failure fails the job `error` leaving no prepared parquet; and cancellation
  mid-write leaves no prepared parquet.
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
- `tests/test_mlflow_log.py` verifies the destination-aware tracking backend/experiment resolution wrappers (no destination resolving the local folder even with Databricks credentials or an env server configured, a named key resolving that destination, an unconfigured explicit key raising, an unknown key rejected before resolution, the experiment default following the destination), databricks/server/local run URL construction, experiment/model-card/JSON logging, and tracking configuration.
- `tests/test_mlflow_settings.py` verifies the `[mlflow]` inventory load/validate/save round trip (tomlkit layout preservation, the retired `mode` key and other unknown keys rejected, empty `tracking_uri` clearing the key, resolved-folder persistence for a bare save, credential-bearing and non-`http(s)` URIs rejected without echoing them, symlink containment), `validate_destination_key`, the per-key `resolve_destination` precedence matrix (toml × env × credentials for each key, profile precedence over the MLflow pair, the general `DATABRICKS_HOST`/`DATABRICKS_TOKEN` pair alone leaving Databricks unconfigured with the not-used-for-MLflow hint, `DATABRICKS_CONFIG_PROFILE` with the pair form rejected, an unsupported env scheme failing server and local but not databricks), `node_destination_key` (no destination is the local folder, configured remotes are never chosen for it), `list_destinations` (all three entries, secret-free profile and redacted server destinations), and `candidate_tracking_config` for every key.
- `tests/test_mlflow_signature.py` verifies structural Date/Datetime mapping,
  parameterised unit/time-zone coverage, Decimal rejection, signature
  persistence, and a real local MLflow log/load/predict round trip.
- `tests/test_mlflow_log_button_roundtrip.py` verifies CatBoost/GLM log-button round-trip construction and button payloads, and that the request's `destination` is authoritative: a job whose config snapshot names one destination is logged to the local folder when the field is omitted or empty and to the named destination when a key is sent, a chosen Databricks rejected by its configuration fails with `400` while an unchosen request still logs locally, an unconfigured key fails with `400` naming the prerequisite and writes nothing, and an unknown value is a `422` before any write.

Tests live in the flat `tests/` directory rather than mirroring the package layout:

- `tests/test_modelling.py` — the broad unit-test base for `TrainingJob`, algorithms,
  metrics, and internal partition execution.
- `tests/test_modelling_routes.py` — HTTP-level integration tests for every route
  in `src/haute/routes/modelling.py`, including `TestDispersionEstimateEndpoint` (happy path,
  status polling, completion payload) and `TestDispersionErrorPaths` (every 400
  validation branch, worker-side failure mapping, cancellation).
- `tests/test_modelling_export.py` — exhaustive coverage of
  `generate_training_script` and its kwarg-rendering rules, including
  `mlflow_destination` rendered only for an explicit key, and executed exports
  proving an explicit Local logs locally while a remote is configured, an Auto
  export follows the execution environment, an unavailable explicit destination
  fails without a remote write, and a destination without an experiment logs
  nothing.
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

## Canonical modelling artifacts

Under the [canonical-only format policy](../README.md#canonical-only-format-policy),
training reads and writes only the current run-scoped feature contract and artifact layout. It
does not probe for, warn about, or interpret a historical shared contract path. Result and CLI
field names describe their current meaning rather than retaining an obsolete name.

## Unified evaluation and bounded tuning

The product contract and limits are defined in
[the high-level specification](high-level.md#unified-evaluation-and-bounded-tuning).
The implementation seams are:

- `src/haute/modelling/_train_config.py` is the only public node-config parser used by live training and
  script export. It requires `evaluation`, canonicalises optional `tuning`, and rejects
  the retired top-level `split` and `cross_validation` keys before job construction.
- `src/haute/modelling/_evaluation.py` owns exact version-1 strategy/config parsing and immutable plan,
  results and report evidence. Writers use canonical finite JSON and atomic
  same-directory replacement. Planning enforces group/date semantics; readers reject
  unknown keys/versions and validate canonical source membership, ordinary-CV
  partitioning, temporal expanding membership, counts, digests and aggregate values.
- `src/haute/modelling/_tuning.py` owns exact explicit-choice search-space validation, metric direction,
  conditional categorical sampling, fixed/sample merge, deterministic winner/tree-count
  rules, fit limits and the tuning plan/trials/report evidence. Trial artifacts revalidate fit metric names
  and validation-row-weighted aggregates rather than trusting submitted summaries.
  Their `elapsed_seconds` field is the canonical zero marker; wall-clock elapsed is
  job metadata so repeated evidence remains byte-identical.
- `src/haute/modelling/_training_job.py` prepares one source, persists/reloads one evaluation plan, and
  drives all selection/trial fits through `run_evaluation_fit`. The final fit alone
  writes the model/contract, emits model loss history, and computes expensive
  diagnostics. Tuning uses pinned Optuna 4.x with one seeded sequential TPE sampler;
  no candidate is skipped after a fit failure.
- `src/haute/routes/_training_lifecycle.py` sends one versioned request to one supervised child for
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
  validation issue rather than an estimate-warning state. `TrainService.evaluation_preview`
  materialises only the target and evaluation key in process, under its own seed plan
  (`open_seed_plan` with `training_seed_plan_request`) held through collection: it reads a
  training run's capture of the modelling node's producer when one covers its demand, and
  otherwise captures that producer with its narrow demand, which the next training run
  widens.

Focused evidence lives in `tests/test_evaluation.py`,
`tests/test_train_evaluation_config.py`, `tests/test_training_evaluation.py`,
`tests/test_training_response_evaluation.py`, `tests/test_tuning.py`, and
`tests/test_training_tuning.py`, with worker/route/export/publication integration in
`tests/test_training_worker_protocol.py`, `tests/test_modelling_routes.py`, and
`tests/test_modelling_export.py`. Frontend guard, config, preview, summary and progress
suites prove the same canonical vocabulary and bounded lifecycle end to end.
### Training allocation ordering (cache implementation correction)

CatBoost constructs its training Pool and releases the raw training frame,
feature frame, labels, weights and baseline conversion references before loading
the validation partition. Validation loading retains the existing projection,
partition selection, feature names/dtypes and offset semantics; it remains
cancellable and measured through the existing execution context. No-validation
training performs no validation read. The GLM adapter continues to receive its
training and validation frames together, as required by its current interface.
Test the lifetime boundary with weak references rather than relying on garbage
collector or allocator RSS behavior in a unit test. Record real allocation peaks
separately in a fresh-process measurement.

When both validation and final-test partitions exist, validation metrics are
computed and their frame/prediction allocations released before final-test
diagnostics are materialised; returned primary metrics remain validation metrics.
Diagnostic prediction uses the existing bounded batch reader with a 65,536-row
ceiling and fills one output array in order. Feature/offset semantics, prediction
dtype/trailing dimensions and exact metrics are preserved. Invalid output row
counts or changing shapes/dtypes fail clearly; cancellation closes the reader.

### Batch scoring from existing snapshot scans

When the scoring input is proven sliceable by the existing Polars classifier,
batch scoring consumes projected slices of that LazyFrame directly. It keeps
the caller's frame and lease ownership alive until scoring finishes and never
unlinks source parts. This includes a leased multipart Parquet scan. Input
projection retains model features, offsets and requested passthrough columns
in their original order; empty input retains the same output schema.

The existing `_batch_score_to_parquet` adapter accepts a LazyFrame as well as
its current single-file input. Complex inputs retain the existing owned
temporary-file path so an upstream computation is executed only once. Both
paths write through the current output destination, cancellation and cleanup
contracts. This removes a complete input rewrite for reusable scans without
introducing a dataset interface or changing scoring semantics.

Both direct scans and owned adapter-file scoring choose batch rows from decoded
input width and the current execution allowance, retaining the scoring row
ceiling. Dictionary compression must not bypass this rule on the Arrow reader
used for staged input.
