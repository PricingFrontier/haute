# MLflow Experiment Name Standardisation

## Problem

MLflow experiment logging had three independent code paths with inconsistent behaviour:

1. **Training** (`routes/modelling.py`) resolved experiment names inline with a fallback chain (`body override > node config > hardcoded default`), then delegated to `log_experiment()` which did its own MLflow setup.
2. **Optimiser** (`routes/optimiser.py`) duplicated the entire MLflow setup inline (tracking URI, registry URI, experiment name, run URL construction) with its own copy of the fallback chain.
3. **Optimiser schema** (`schemas.py`) hardcoded `experiment_name = "/optimisation"` as a default, bypassing the fallback chain entirely.

Additionally:
- The default experiment path `/Shared/haute/{node_label}` was Databricks-specific but applied to local MLflow too, where `/Shared/` is meaningless.
- The run URL in `log_experiment()` used `experiment_name` instead of `experiment_id`, producing broken Databricks links.
- The optimiser UI had no way to configure the experiment path (unlike training).

## Approach

Extract shared helpers into `_mlflow_log.py` and have both routes call them.

### New shared helpers

| Function | Purpose |
|---|---|
| `resolve_experiment_name()` | Standard fallback chain: explicit override > node config > backend-aware default. Returns `/Shared/haute/{label}` for Databricks, `{label}` for local. |
| `configure_mlflow_tracking()` | Resolve backend, call `set_tracking_uri`, conditionally `set_registry_uri`. Single place for connection setup. |
| `build_run_url()` | Build a Databricks run URL using `experiment_id` (not name). Warns when experiment lookup fails. Handles host trailing slash. |

### What changed

- **`_mlflow_log.py`**: Added three helpers above. `log_experiment()` now calls `configure_mlflow_tracking()` and `build_run_url()` instead of inlining the logic.
- **`routes/modelling.py`**: Replaced inline fallback with `resolve_experiment_name()`.
- **`routes/optimiser.py`**: Replaced inline MLflow setup with `configure_mlflow_tracking()`, inline URL builder with `build_run_url()`, inline fallback with `resolve_experiment_name()`.
- **`schemas.py`**: Changed `OptimiserMlflowLogRequest.experiment_name` from `str = "/optimisation"` to `str | None = None`.
- **`OptimiserPreview.tsx`**: Removed hardcoded `experiment_name: "/optimisation"`. Post-log message now shows experiment name.
- **`OptimiserConfig.tsx`**: Added collapsible "MLflow Logging" section with "Experiment path" field.
- **`client.ts`**: Fixed type inconsistency (`experiment_name?: string | null`).

### What was intentionally left alone

- **Deploy** (`deploy/_mlflow.py`): Hardcodes `"databricks"` tracking URI. This is correct -- deploy uses different env vars (`DATABRICKS_RATING_HOST/TOKEN`) and only targets Databricks.
- **`_ensure_tracking()`** in `routes/mlflow.py`: Read-only discovery helper, 3 lines, different purpose.
- **`_training_job.py`**: Passes `mlflow_experiment` directly to `log_experiment()`. This is the programmatic API where the user has already chosen the value.

## Alternatives considered

1. **Route the optimiser through `log_experiment()`**: Rejected. The optimiser logs a fundamentally different artifact shape (solver params, frontier CSV, optimiser_result.json) vs training (model diagnostics, SHAP, model card). Forcing them through one function would require fake empty metadata or `if is_optimiser:` branches.

2. **Move `resolve_tracking_backend()` to `_mlflow_utils.py`**: Rejected. Conceptually cleaner, but creates a noisy diff across 5+ files for zero behavioural change. Not worth it standalone.

3. **Add `[mlflow]` section to `haute.toml`**: Deferred. Would unify experiment config across training, optimiser, and deploy. Good future improvement but a larger config schema change.
