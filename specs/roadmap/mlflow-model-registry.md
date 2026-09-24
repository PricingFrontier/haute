# MLflow model registry roadmap

## Scope

Loading, validating and scoring models, and the MLflow client state used for
logging. Current behaviour is specified in
[the MLflow model-registry specification](../mlflow-model-registry/high-level.md).
These packages come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MLF-R02 | Planned | P3 | The optimiser's MLflow log runs on a destination-bound client, outside the fluent lock. |

## Planned improvements

### MLF-R02 — Explicit MLflow clients for the optimiser log
**Why:** Training (`log_experiment`) and deploy (`deploy_to_mlflow`) log through
an `MlflowClient` bound to the resolved destination; only their model log runs
inside `mlflow_fluent_operation`, as the
[modelling low-level specification](../modelling/low-level.md) states. The
optimiser's MLflow log route still runs its whole log, from
`configure_mlflow_tracking` to the run URL, inside the fluent operation, so it
serialises against every other MLflow log for its full duration and writes the
tracking URI into the environment.

**Decided (24 September 2026):** option (a). Checked against MLflow 3.15.1,
`mlflow.<flavor>.log_model` calls `Model.log`, which resolves the global
tracking URI and the thread's active run; `mlflow.set_tracking_uri` writes
`MLFLOW_TRACKING_URI` into `os.environ`; and uv-project detection can be
switched off only through the `MLFLOW_UV_AUTO_DETECT` and `MLFLOW_LOG_UV_FILES`
environment variables. Model logging therefore keeps the fluent lock and its
environment switches, and every other MLflow call runs on a destination-bound
client.

**Plan:** Move the optimiser log route onto the pattern training uses: resolve
the destination with `resolve_tracking_backend` and `registry_uri_for_tracking`,
create the experiment and run with `_mlflow_utils.ensure_experiment` and
`client.create_run`, log parameters, metrics, tags and artifacts through the
client, terminate the run in a `finally`, and build the URL with
`build_run_url(..., tracking_uri=...)`. The route logs no model, so it needs no
fluent operation at all. `configure_mlflow_tracking` and
`set_experiment_creating_workspace_folder` then have no production caller;
delete them and their tests.

**Acceptance:** Two MLflow logs to different destinations, from any of
training, deploy and the optimiser, run concurrently under test outside their
model logs; the only code holding `mlflow_fluent_operation` is a model log
(with its environment switches) and the pyfunc download's nested model lookup;
the logging and destination-isolation tests pass.

**Dependencies:** None. `src/haute/routes/optimiser.py` belongs to the
optimiser lane.

**Evidence:** `src/haute/routes/optimiser.py`;
`src/haute/modelling/_mlflow_log.py::configure_mlflow_tracking`;
`src/haute/_mlflow_utils.py::set_experiment_creating_workspace_folder`;
`src/haute/_mlflow_utils.py::ensure_experiment`.
