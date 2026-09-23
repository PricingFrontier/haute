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
| MLF-R01 | Planned | P1 | A CatBoost model whose offset metadata cannot be read is refused, never scored without its offset. |
| MLF-R02 | Planned | P3 | MLflow calls use explicit clients instead of mutating process-global state under locks. |

## Planned improvements

### MLF-R01 — Never score without a model's offset
**Why:** `_catboost_offset_column` reads the offset column stamped into a
CatBoost model's metadata, and returns `None` on any exception from
`get_metadata()`. `None` means "trained without an offset", so a metadata
read failure makes a served model score from baseline zero, which is exactly
the silent mispricing its docstring says the stamp exists to prevent.

**Plan:** Treat only an absent key as "no offset". Let any error reading the
metadata propagate as a typed model-load failure.

**Acceptance:** A test with a model whose `get_metadata` raises fails to load
with a message naming the model; a model without the key still loads as
offset-free; a model with the key applies its offset.

**Dependencies:** None.

**Evidence:** `src/haute/_mlflow_io.py::_catboost_offset_column`;
`src/haute/_mlflow_io.py::_catboost_offset_link`; `tests/test_mlflow_io.py`.

### MLF-R02 — Explicit MLflow clients
**Why:** Logging uses MLflow's fluent API, whose tracking and registry URIs are
process-global, so `mlflow_fluent_operation` serialises every logging
operation under a global lock and restores the URIs and three environment
variables afterwards. `runtime_environment_inference` flips two more
environment variables around each `log_model`. This repeats a pattern the
execution engine also has with Polars configuration (`EXEC-R01`).

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
