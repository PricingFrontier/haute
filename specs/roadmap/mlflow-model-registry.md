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
| MLF-R02 | Planned | P3 | MLflow calls use explicit clients instead of mutating process-global state under locks. |

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

**Acceptance:** Two logging operations to different destinations can run
concurrently under test; no production path writes `os.environ`; the
logging and destination-isolation tests pass.

**Dependencies:** None.

**Evidence:** `src/haute/_mlflow_utils.py::mlflow_fluent_operation`;
`src/haute/_mlflow_utils.py::runtime_environment_inference`;
`src/haute/modelling/_mlflow_log.py`.
