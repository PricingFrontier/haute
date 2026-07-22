# Modelling — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/modelling/__init__.py` | Public API surface: `FitResult`, `MLflowLogResult`, `TrainingJob`, `TrainResult`, `SplitConfig`, `generate_training_script`, `log_experiment`. |
| `src/haute/modelling/_algorithms.py` | `BaseAlgorithm` ABC, `CatBoostAlgorithm`, `ALGORITHM_REGISTRY`, memory-checkpoint helpers, CatBoost `Pool` construction, GPU fit-thread lifecycle. |
| `src/haute/modelling/_rustystats.py` | `GLMAlgorithm` implementing `BaseAlgorithm` via RustyStats; GLM-only diagnostics (`coefficients_table`, `relativities`, `fit_statistics`, `glm_diagnostics`); `estimate_glm_dispersion()` profile-likelihood estimation; `_resolve_glm_terms()` term resolution. |
| `src/haute/modelling/_training_job.py` | `TrainingJob` orchestrator — prepare data, split, train, compute metrics, save artifacts, and optionally log to MLflow; also defines `TrainResult` and the intermediate stage types. |
| `src/haute/modelling/_train_config.py` | Single source of truth for modelling-node config → `TrainingJob` kwargs (`build_training_job_kwargs`, `build_train_params`, `training_objective_issue`, `default_metrics`). |
| `src/haute/modelling/_split.py` | `SplitConfig`, `split_data`/`split_mask` for random/temporal/group strategies, and partition constants. |
| `src/haute/modelling/_metrics.py` | Primary metric functions and diagnostic data computation (double lift, AvE, residuals, actual-vs-predicted, Lorenz, PDP). |
| `src/haute/modelling/_feature_contract.py` | `FeatureContract` build/save/load/cache, contract comparison, and categorical-level normalisation/validation. |
| `src/haute/modelling/_signature.py` | `build_signature()` — MLflow `ModelSignature` construction with loud dtype/metadata validation. |
| `src/haute/modelling/_charts.py` | Pure-SVG renderers used by model cards. |
| `src/haute/modelling/_model_card.py` | `generate_model_card()` — self-contained HTML assembled for MLflow artifact logging; ordinary training does not persist it beside the model. |
| `src/haute/modelling/_mlflow_log.py` | Tracking-backend resolution, `log_experiment()`, flavor-aware model/signature logging, diagnostics artifacts, and best-effort model-card logging. |
| `src/haute/modelling/_result_types.py` | `ModelDiagnostics` and `ModelCardMetadata` bundles shared by training, MLflow logging, and model-card generation. |
| `src/haute/modelling/_export.py` | `generate_training_script()` code generation for standalone Python training scripts. |
| `src/haute/routes/modelling.py` | FastAPI router for training, status/cancel, estimates, MLflow check/log, export, model-cache clear, and dispersion jobs. |
| `src/haute/routes/_train_service.py` | `TrainService`: validation, RAM/VRAM estimates, pipeline materialisation, background training/dispersion lifecycle, cancellation, and cleanup. |
| `src/haute/schemas.py` | Shared Pydantic request/response contracts used by `/api/modelling/*` routes. |

## Key types and data structures

- **`BaseAlgorithm`** (ABC, `_algorithms.py`) — `fit()`, `predict()`,
  `feature_importance()`, `save()`. `CatBoostAlgorithm` and `GLMAlgorithm` implement it.
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
- **`SplitConfig`** (`_split.py`, dataclass) — `strategy: "random"|"temporal"|"group"`,
  `validation_size`, `holdout_size`, `seed`, `date_column`/`cutoff_date` (temporal),
  `group_column` (group). `__post_init__` validates `0 <= size < 1`,
  `validation_size + holdout_size < 1`, and that strategy-specific fields are present.
- **`FeatureContract`** (`_feature_contract.py`, frozen dataclass) — `features`,
  `feature_types`, `categorical_features`, `categorical_levels`, `target_name`,
  `target_type`, `task`, `contract_hash` (sha256 of canonical compact JSON over every
  other field), `offset_column: str | None`. `CONTRACT_FILENAME = "feature_contract.json"`;
  per-model files are named via `_training_job.model_contract_filename(name)` →
  `"{name}.feature_contract.json"`.
- **`TrainingJob`** (`_training_job.py`) — the orchestrator. Constructor stores
  `name`, `data` (path/DataFrame/LazyFrame), `target`, `weight`, `exclude`,
  `feature_columns`, `fold_column`, `id_columns`, `algorithm`, `task`, `params`,
  `split_config` (parsed from dict/`SplitConfig`/default), `metrics` (defaulted via
  `default_metrics()` when omitted), `mlflow_experiment`, `model_name`, `output_dir`,
  `loss_function`, `variance_power`, `offset`, `monotone_constraints`,
  `feature_weights`, and a normalised `_declared_categorical_levels`. A contract-dtype
  snapshot (`_contract_feature_dtypes`, `_contract_categorical_levels`,
  `_contract_target_dtype`, `_contract_offset_dtype`) is populated inside `_prepare_data`
  and consumed by `_save_artifacts` and `_log_to_mlflow`.
- **`TrainResult`** (`_training_job.py`, dataclass) — the full public result: `metrics`,
  `feature_importance`, `model_path`, `train_rows`, `test_rows` (legacy name for the
  *validation*-set count — kept for API/frontend back-compat), `features`,
  `cat_features`, `holdout_rows`, `holdout_metrics`, `diagnostics_set`
  (`"train"|"validation"|"holdout"`), `best_iteration`, `loss_history`, every chart's
  underlying data (`double_lift`, `shap_summary`, `feature_importance_loss`,
  `ave_per_feature`, `residuals_histogram`/`residuals_stats`, `actual_vs_predicted`,
  `lorenz_curve`/`lorenz_curve_perfect`, `pdp_data`), GLM-only fields
  (`glm_coefficients`, `glm_relativities`, `glm_fit_statistics`,
  `glm_regularization_path`), and `diagnostics_errors: list[dict[str, str]]`.
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
- **`TrainService`** (`routes/_train_service.py`) — wraps a `JobStore`, `JobLifecycle`,
  and `CancellableJobRegistry`; owns the HTTP-facing training lifecycle
  (`start`/`cancel`/`timeout`) and the dispersion-estimation lifecycle
  (`start_dispersion_estimate`/`dispersion_job`/`cancel_dispersion`), distinguished in
  the shared job store by a `job_type` field (`"training"` vs. `"dispersion_estimate"`)
  so `dispersion_job()` 404s if asked for a job of the other type.

## Control flow

### Live training (HTTP)

1. `POST /api/modelling/train` → `routes/modelling.py:train_model` →
   `TrainService.start(body)`.
2. `TrainService.start`: locate the modelling node in the graph; merge declared
   `categorical_levels` from the node and its upstream ancestors
   (`_declared_categorical_levels_for_training`); `_validate_config` (target set,
   algorithm registered, GLM family/link validity or CatBoost loss validity via
   `resolve_loss_function`, then `training_objective_issue` for completeness); under
   `_start_lock`, reject if another job is already `"running"`
   (`_check_no_concurrent_jobs`) and create the job record; `_compile_preamble`;
   `_estimate_ram` (raises HTTP 422 on estimate failure); clamp the estimated row
   limit against any user-supplied `row_limit`; `build_train_params` (the same builder
   export uses); `_check_gpu_fallback` (VRAM feasibility check — see Edge cases);
   compute required-column demand per node (`_training_required_columns_by_node`);
   create an admitted `ExecutionContext` (RAM ceiling + cancellation token) via
   `create_admitted_execution_context`; `_execute_and_sink` runs the upstream pipeline
   lazily, validates the required columns actually arrived, projects away excluded
   columns, and streams the result to a temp parquet with `bounded_sink`;
   `_launch_background` builds the `TrainingJob` via `build_training_job_kwargs`
   (`params` overridden by the GPU-adjusted `train_params`) and starts a daemon thread
   running `TrainingJob.run()`.
3. In the background thread: on success, build a `TrainResponse`, run
   `_assert_json_finite` over it, and transition the job to `"completed"`. Exceptions
   are mapped to terminal states: `BackgroundJobStoppedError` → silent (already logged
   at cancellation/timeout time), `ExecutionCancelledError` → `cancelled`,
   `ExecutionMemoryLimitExceededError` → `memory_limited`,
   `BoundedMemoryUnsupportedError` → `contract_error`, bare `ValueError` →
   `contract_error`, everything else → `error` with `_friendly_error(exc)`. The
   `finally` block always republishes `execution_metrics`, releases the cancellation
   registry entry and RAM admission, and deletes the temp parquet.
4. `GET /train/status/{job_id}` first compares a running job's `start_time` with its
   configured/default timeout; an overdue job is cooperatively cancelled and atomically
   transitioned to `timed_out` before the response is assembled. It then returns
   progress/loss history/result. On the first read of a completed result,
   `_assert_json_finite` re-validates it and the outcome
   is cached on the job (`_result_finite_validated`) via `atomic_update` so later polls
   skip the recursive walk; a validation failure instead flips the job to `"error"`
   with `result: None`.

### `TrainingJob.run()` pipeline (used by live training and direct/test callers)

1. **`_prepare_data`** — reuse an already-sunk parquet path directly, or collect a
   supplied DataFrame/LazyFrame and write it to a temp parquet; validate required
   columns; count and, when the run owns the input, filter out null-target rows into a
   second "clean" temp parquet; `_derive_features` (explicit `feature_columns`, or all
   columns minus target/weight/offset/fold/id/split-key columns/exclude, with
   categorical features detected from Polars dtype); snapshot feature dtypes,
   categorical levels, target dtype, and offset dtype for the contract.
2. **GLM term narrowing** (`TrainingJob.run`, algorithm == `"glm"` only) — if `terms`
   were configured, narrow `features`/`cat_features` to just the term columns (raising
   if a term names a column absent from the data, or if narrowing leaves zero
   features).
3. **`_split_data`** — compute an `Int8` partition mask via `split_mask` for the
   configured strategy; sink the original data plus a `_partition` column to a new
   split parquet via `bounded_sink`; frees the prepared-data temp file once consumed.
4. **`_train_model`** — resolve the algorithm class from `ALGORITHM_REGISTRY`; resolve
   CatBoost's `loss_function` (GLM's equivalent config already lives in `params`); read
   the train (and validation, if present) partitions with an algorithm-appropriate
   column projection (`_glm_select_columns` / `_catboost_select_columns` via
   `_scan_with_columns`); GLM calls `algo.fit(train_df, ...)` on DataFrames directly;
   CatBoost extracts label/weight/offset arrays, builds `Pool`s via `_build_pool`
   (float32 downcast, categorical-index mapping, explicit feature-name pinning so a
   reloaded `.cbm` scores by name, not position), then fits.
5. **`_compute_metrics`** — mandatory `algo.feature_importance(model)`; select the
   diagnostics partition by precedence holdout > validation > train; read it once;
   compute primary metrics with offset-inclusive predictions; if the diagnostics set is
   holdout, separately re-read validation to report both sets' metrics; then double
   lift, AvE-per-feature, optional SHAP / `LossFunctionChange` importance (each
   individually try/excepted into `diagnostics_errors`), residuals
   histogram/scatter/Lorenz curve, optional PDP (raises only if *every* feature's PDP
   fails), optional GLM-specific diagnostics probed via `hasattr`; frees the
   diagnostics DataFrame and unlinks the split parquet.
6. **`_save_artifacts`** — write the native model file
   (`.cbm`/`.rsglm`/`.model` from `_MODEL_EXT_MAP`); when features are supplied, build
   and save the per-model `FeatureContract`, warning (never overwriting or deleting) if
   a legacy shared `feature_contract.json` is present in the same output directory.
7. Assemble `TrainResult`; if `mlflow_experiment` is set, `_log_to_mlflow` delegates to
   `_mlflow_log.log_experiment`, reusing the same contract-dtype snapshot for the
   `ModelSignature`.
8. `finally` — `_cleanup_owned_temp_parquets` removes any run-owned temp file not
   already consumed by the normal flow, so an abort or cancellation anywhere in the
   pipeline cannot leak multi-GB files into the OS temp directory.

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
   seeded row sampling — so the profiled data matches what a real training run would
   see. The row limit is additionally clamped to `_DISPERSION_ESTIMATE_ROW_CAP`
   (200,000): the profile search runs ~10-30 IRLS fits, so it samples rather than
   paying full-data cost per candidate — 200k rows pins a single dispersion scalar far
   tighter than the search's own tolerance.
5. `_launch_dispersion_background` builds a stub `TrainingJob` via
   `build_training_job_kwargs` with the parameter being estimated set to its RustyStats
   silent default (`_DISPERSION_PARAM_STUBS`: `theta=1.0`, `var_power=1.5`) so the
   shared config machinery can run; the stub value never reaches a fit — the search
   overrides it at every candidate. A daemon thread then: runs `job._prepare_data`
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
   fires before each candidate fit, used here as both a progress signal
   (`job_id`'s `progress`/`message` updated per fit, capped visually at fit 30) and a
   cancellation checkpoint (`_raise_if_training_stopped`).
7. On success, the job transitions to `"completed"` with `param`/`value`/`llf`/`n_fits`
   fields. `ValueError` (including "no candidate converged") maps to `contract_error`;
   `ExecutionCancelledError`/`BackgroundJobStoppedError` map identically to the training
   background thread's handling; anything else maps to `error` via `_friendly_error`.
   The `finally` block always releases the job registry entry and the RAM admission,
   and deletes the temp parquet the estimation frame was sunk to.
8. `GET /dispersion/status/{job_id}` and `POST /dispersion/cancel/{job_id}` mirror the
   training job's status/cancel routes, scoped to `job_type="dispersion_estimate"` via
   `TrainService.dispersion_job`. The status route does not call `TrainService.timeout` and the
   dispersion worker stores no timeout field, so this job type has no automatic/poll-driven
   timeout; cancellation is explicit.

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

### Concurrency / ordering guarantees

- Only one job may be `"running"` in the process-wide training namespace; `_start_lock`
  serialises the check-then-create so concurrent training/dispersion submissions cannot
  both pass `_check_no_concurrent_jobs`.
- Training timeouts are poll-driven: no watcher thread changes an overdue job until a
  status request observes the elapsed time. The timeout transition and late worker
  progress/completion writes are CAS-guarded, so a timed-out job cannot become completed.
- Dispersion estimates share the single running slot and cancellation registry with training but
  not its timeout policy: they record `start_time` only and run until completion, failure, or
  explicit cancellation.
- Each job's pipeline runs on a single daemon background thread; progress/iteration
  callbacks and cancellation checks go through the job store's `atomic_update`, so
  status polling from other threads/requests is race-free.
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

- Splitting an empty DataFrame raises `ValueError` (`split_data`/`split_mask`).
- A temporal split with any null dates in the date column raises `ValueError` naming
  the null count (`_require_no_null_dates`) — the previous behaviour silently routed
  null-date rows into validation (mask path) or dropped them (split path), both biased
  because nulls cluster; the current policy requires the caller to filter/impute first.
- A group split forces at least one group into validation/test when the hash
  assignment happened to place zero groups there and more than one group exists.
- `SplitConfig.__post_init__` rejects `validation_size + holdout_size >= 1` at
  construction time, before any data is touched.
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
- Per-model contract files exist because a prior shared `feature_contract.json` design
  let two models trained into one `output_dir` silently overwrite each other's
  contract; a detected legacy shared file is warned about and left untouched, never
  trusted or deleted.
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

> NOTE: `TrainService._check_gpu_fallback` is misleadingly named — despite
> "fallback," it does **not** fall back to CPU automatically. It only checks VRAM
> feasibility and raises HTTP 507 when insufficient; the user must manually switch
> `task_type` to CPU (or reduce rows/features) and retry.

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
- **`ValueError`** — the dominant validation error across the package: bad split
  config, missing required columns, an empty training DataFrame, all-non-finite metric
  inputs, a missing offset column at predict time, GLM terms referencing absent
  columns. `TrainService._train_background` specifically maps a bare `ValueError` from
  `TrainingJob.run()` to the job terminal state `contract_error` (distinct from the
  catch-all `error`).
- **Execution-engine exceptions** (`ExecutionCancelledError`,
  `BackgroundJobStoppedError`, `ExecutionMemoryLimitExceededError`,
  `BoundedMemoryUnsupportedError`) — each mapped to a distinct job terminal state
  (`cancelled`, silent stop, `memory_limited`, `contract_error` respectively) inside
  `_train_background`'s exception handling.
- **`HTTPException`** — raised directly by route handlers and by `TrainService.start`
  for 400/409/422/500/507. `TrainService.start`'s `except HTTPException` block
  additionally transitions the job record to the matching terminal state
  (`memory_limited` for 507, `contract_error` for other 4xx, `error` otherwise) before
  re-raising, so the job store and the HTTP response can never disagree about outcome.
- **Generic `Exception`** catch-all in `_train_background` maps to job state `error`
  with the message produced by `_friendly_error(exc)` — a heuristic translator that
  returns `ValueError` messages verbatim, prefixes `FileNotFoundError`, special-cases
  CatBoost-flavoured exceptions (NaN/Inf hint, feature-count mismatch), prefixes
  `OSError` as a model-save failure, and otherwise falls back to
  `f"Training failed ({exc_type}): {msg}"`.
- **`_record_diag_error`** is the single call site that converts an optional-diagnostic
  exception into a structured `diagnostics_errors` entry (`diagnostic`, `error`,
  `error_type`) plus a `logger.warning` — used identically for SHAP,
  `LossFunctionChange` importance, PDP, and every GLM-specific diagnostic
  (`coefficients_table`, `relativities`, `fit_statistics`, `regularization_path`).
- **MLflow logging errors** — `_log_model_card` inside `log_experiment` is wrapped in
  `try/except Exception: logger.warning(...)`, so a model-card bug never fails an
  otherwise-successful experiment log; `build_run_url` similarly catches and returns
  `None` with a debug log rather than failing the whole call.
- **Dispersion estimation** — `_validate_dispersion_config` raises `HTTPException(400)`
  for an unknown parameter, a non-GLM node, a family/link mismatch, a parameter/family
  pairing mismatch, a missing target, or any other incomplete training objective;
  raised before any job record is created. Inside `estimate_glm_dispersion`, `ValueError`
  covers an unrecognised or mismatched `param` and "every candidate fit failed"; inside
  the background worker, `ValueError` maps the job to `contract_error`,
  `ExecutionCancelledError`/`BackgroundJobStoppedError` map identically to training's
  handling, and any other exception maps to `error` via `_friendly_error` — the same
  taxonomy `_train_background` uses.

## Testing

Tests live in the flat `tests/` directory (not mirroring the package layout) and total
roughly 700+ test functions across about 30 files that exercise this component:

- `test_modelling.py` (105 tests) — the broad unit-test base for `TrainingJob`,
  algorithms, metrics, and splits.
- `test_modelling_routes.py` (123 tests) — HTTP-level integration tests for every route
  in `routes/modelling.py`, including `TestDispersionEstimateEndpoint` (happy path,
  status polling, completion payload) and `TestDispersionErrorPaths` (every 400
  validation branch, worker-side failure mapping, cancellation).
- `test_modelling_export.py` (74 tests) — exhaustive coverage of
  `generate_training_script` and its kwarg-rendering rules.
- `test_train_config_builder.py` (48 tests) — unit tests for the config→kwargs builder,
  including regression coverage for the GLM-vs-CatBoost key-routing bugs it was written
  to prevent, and the Negative Binomial `theta` gate (unset fails loud, set via `params`
  or top-level config passes, non-negbinomial families are unaffected, `theta` survives
  script export).
- `test_metrics.py` (96 tests) and `test_metrics_gini_ties.py` (27 tests, labelled the
  "C6 regression suite") — metric correctness and the tie-corrected Gini/Lorenz
  row-order-independence guarantee.
- `test_charts.py` (64 tests) and `test_model_card.py` (33 tests) — SVG chart and HTML
  model-card generation.
- `test_feature_contract.py` (44 tests) and `test_mlflow_signature.py` (32 tests) —
  contract build/save/load/hash-verification and MLflow signature construction.
- `test_modelling_train_score_contract.py` (14 tests) — explicit train↔score contract
  regressions: feature/categorical order mismatch, MLflow signature round-trip,
  categorical type mismatch, GLM column selection preserving categorical metadata
  across save/load.
- `test_rustystats_algorithm.py` (37 tests, skipped when RustyStats isn't installed)
  and `test_glm_integration.py` (24 tests) — GLM fit/predict/save/diagnostics and
  integration-gap regressions. `TestNegBinomialThetaThreading` pins that an unset
  `theta` really is RustyStats' silent-1.0 default and that a set `theta` reaches the
  fit; `TestEstimateGlmDispersion` validates the profile-likelihood search itself — the
  NB `theta` MLE against a statsmodels NB2 reference (to 4 s.f.) with coefficient parity
  (to 4 d.p.) on synthetic data, determinism, an interior Tweedie `var_power` maximum,
  the `on_fit` cancellation hook, and the unknown-parameter/family-mismatch rejections;
  `TestBuildInteractionsEmptySlots` pins the unfilled-interaction-row fix described
  above.
- `test_train_service_coverage.py` (40 tests) and
  `test_train_service_helpers_coverage.py` (13 tests) — `TrainService` error/cleanup
  branches and its pure column-demand helper functions.
- `test_algorithms_coverage.py` (103 tests) — targeted coverage of `_algorithms.py` /
  `_training_job.py` paths not hit elsewhere (platform-specific RSS reads, CatBoost and
  MLflow mocked out via `unittest.mock`).
- Narrow, remediation-pinned regression suites: `test_training_memory_safety.py`,
  `test_training_temp_cleanup.py`, `test_training_split_streaming.py`,
  `test_training_null_target_fused_split.py`, `test_training_catboost_projection.py`,
  `test_training_contract_per_model.py`, `test_training_job_no_glm_cv.py` (a deleted
  GLM cross-validation path), `test_training_lorenz_nonfinite.py`.
- Additional targeted coverage: `test_modelling_loud_errors.py`,
  `test_modelling_golden.py` (golden-snapshot pins for route response shapes),
  `test_bundle6_trust_model_cleanup.py`, `test_catboost_training_demand.py`,
  `test_cli_train.py`, `test_model_explainability.py`, `test_train_param_routing.py`,
  `test_codegen_split.py`.

Strategy is overwhelmingly unit/regression: fast, isolated tests per module, heavy use
of `unittest.mock` to avoid exercising real CatBoost/RustyStats/MLflow where feasible,
with a small number of golden-snapshot tests pinning route response shapes. GLM tests
skip cleanly when RustyStats is not installed, matching the production lazy-registration
behaviour in `ALGORITHM_REGISTRY`.

Known coverage gap: there is no single dedicated test file for `_split.py` in
isolation — split logic (random/temporal/group strategies, the mask functions) is
exercised indirectly through `test_modelling.py`,
`test_training_null_target_fused_split.py`, `test_training_split_streaming.py`, and
`test_codegen_split.py` rather than one focused suite.

## Polars backend contracts (0.6.0)

`TrainService` and estimate paths consume the execution facade's typed result rather
than selecting collection strategy themselves. Their response and job diagnostics retain
the final feature inclusion/exclusion and provenance supplied by that result; execution
engine remains the sole owner of planning mechanics. See the
[remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md).
