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
| MLF-R02 | Decision | P3 | MLflow calls use explicit clients instead of mutating process-global state under locks. |

## Planned improvements

### MLF-R02 — Explicit MLflow clients
**Why:** Logging uses MLflow's fluent API, whose tracking and registry URIs are
process-global, so `mlflow_fluent_operation` serialises every logging
operation under a global lock and restores the URIs and three environment
variables afterwards. `runtime_environment_inference` flips two more
environment variables around each `log_model`. The execution engine avoids
the same pattern for Polars configuration by holding its streaming chunk size
as one process setting instead of scoping it per request.

**Plan:** Use `MlflowClient` instances bound to the resolved tracking and
registry URIs for logging, as discovery and downloads already do, and pass
environment inference options explicitly where MLflow allows. Keep a narrow
lock only for any MLflow call that still requires fluent state, and state
which calls those are.

**Decision needed:** Checked against MLflow 3.15.1 on 24 September 2026,
model logging cannot run on an explicit client. `mlflow.<flavor>.log_model`
calls `Model.log`, which resolves the global tracking URI and the thread's
active run; `mlflow.set_tracking_uri` itself writes `MLFLOW_TRACKING_URI`
into `os.environ`; and MLflow's uv-project detection can be switched off
only through the `MLFLOW_UV_AUTO_DETECT` and `MLFLOW_LOG_UV_FILES`
environment variables, not a `log_model` argument. While models are logged
through MLflow's flavour API, two logs to different destinations cannot run
concurrently and some production path must write `os.environ`. Choose one:
(a) narrow the lock: run, parameter, metric, tag and artifact logging on
destination-bound clients, and only `log_model` with its environment
switches under the fluent lock, restating the acceptance accordingly;
(b) log each model in a short-lived worker process, so fluent state is
per-process and logs run concurrently; or (c) keep the current lock and
retire the package. Recommended: (a).

**Acceptance:** Two logging operations to different destinations can run
concurrently under test; no production path writes `os.environ`; the
logging and destination-isolation tests pass.

**Dependencies:** None.

**Evidence:** `src/haute/_mlflow_utils.py::mlflow_fluent_operation`;
`src/haute/_mlflow_utils.py::runtime_environment_inference`;
`src/haute/modelling/_mlflow_log.py`.
