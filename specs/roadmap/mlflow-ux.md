# MLflow configuration and UX roadmap

## Scope

Owns how the MLflow tracking backend is chosen, surfaced, and consumed by the
modelling, optimiser, Model Score, and Optimiser Apply surfaces: backend
selection and resolution, connection status and settings endpoints, the
frontend settings surface, and the per-node logging/browsing UX.

Shipped behaviour is defined by the mlflow-model-registry, modelling,
optimiser, server-api, frontend-modelling-optimiser-ui, frontend-node-editors,
and frontend-shared component specifications and their regression tests.

Agreed product decisions for this component:

- Connection configuration is hybrid: the UI selects the backend mode and the
  server persists it to a `[mlflow]` section in `haute.toml`; credentials stay
  in `.env` and never round-trip through the browser.
- Experiment logging stays manual — the post-training and post-solve "Log to
  MLflow" actions. No auto-log toggle.
- Local-folder mode gets no managed viewer process and no viewer links. Its
  success surface is the run ID, the resolved `mlruns` folder path, and a
  copyable `mlflow ui` command. A user-configured `server` backend keeps real
  run URLs.
- Polish is weighted toward the Databricks path; local support must be correct
  and honestly reported, not feature-matched.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| MLF-U01 | Planned | P2 | Explicit three-mode tracking-backend configuration, status, and settings API. |
| MLF-U02 | Planned | P2 | Toolbar MLflow status chip and settings modal. |
| MLF-U03 | Planned | P2 | Plain-language, truthful MLflow UX across the four node surfaces. |
| MLF-U04 | Planned | P3 | Registration follows backend capability; registry parity for non-Databricks modes. |

MLF-U01 is the startable package; MLF-U02 and MLF-U03 build on its API, and
MLF-U04 opens with its own verification spike.

## Planned improvements

### MLF-U01 — Explicit tracking-backend configuration and resolution

**Why:** `resolve_tracking_backend()` is implicit and env-driven: Databricks
credentials force Databricks tracking (coupling MLflow to Databricks data
access), the standard `MLFLOW_TRACKING_URI` variable is ignored, and the
fallback silently writes to `./mlruns` without the user ever choosing or
seeing it. There is no way to point haute at a local or team MLflow server,
and no API to read or change any of this.

**Plan:** Extend `resolve_tracking_backend()` to three explicit modes —
`databricks`, `server`, `local` — resolved as: `[mlflow]` section in
`haute.toml` (`mode`, plus `tracking_uri` for server mode and an optional
`folder` for local mode) first; env fallback second; default local file
store at `./mlruns` last. The env
fallback classifies `MLFLOW_TRACKING_URI` by form rather than assuming a
server: `databricks` or a `databricks://` profile URI → databricks mode;
`http(s)://` → server mode; a `file:` URI or plain filesystem path → local
mode at that path; any other scheme (for example `sqlite:`) is a loud
unsupported-URI failure, never a guess. Without that variable,
`DATABRICKS_HOST`+`DATABRICKS_TOKEN` → databricks. Server mode requires an
`http(s)` `tracking_uri`; both the settings PUT and resolution reject other
forms. A selected mode with missing prerequisites (no token, no URI) fails
loudly with a reason naming the missing variable or field — never a silent
fallback. Settings GET reports the resolved destination (including an
env-derived custom local folder), and saving persists that resolved folder
for local mode, so a save never silently redirects logging or discovery to
`./mlruns` from a folder the environment had selected.
`resolve_experiment_name()` keeps the `/Shared/haute/`
default for databricks only; server mode uses the plain node label like
local. `build_run_url()` gains server-mode run URLs. Add
`GET /api/mlflow/status` (installed/importable, resolved mode, human-readable
destination, config source toml/env/default, actionable detail),
`GET`/`PUT /api/mlflow/settings` (round-trips the `[mlflow]` section via
tomlkit, preserving file comments and layout), and
`POST /api/mlflow/test-connection` (short-timeout `search_experiments()`
probe with categorised, non-secret-leaking errors). Migrate the frontend from
`/api/modelling/mlflow/check` to the new status endpoint and delete the old
route and schema. Teach `deploy/_config.py` unknown-key validation the
`[mlflow]` section. Update the mlflow-model-registry, modelling, and
server-api specs before the code change.

**Acceptance:** A precedence matrix test covers toml × env × missing-credential
combinations, including: toml `local` with Databricks credentials present
stays local; `MLFLOW_TRACKING_URI=databricks` resolves to databricks mode,
an `http(s)` value to server mode, and a `file:` URI to local mode at that
path; an unsupported scheme is a loud failure; toml `databricks` without a
token is a loud, actionable failure, not a fallback; a server-mode
`tracking_uri` that is not `http(s)` is rejected at both PUT and resolution.
Settings PUT→GET round-trips through a real `haute.toml` preserving unrelated
sections and comments. A regression scenario proves that saving an unchanged
env-derived local configuration (a `file:` `MLFLOW_TRACKING_URI`) preserves
its custom folder and existing-run discovery. Status truthfully names the
destination in all three modes. No frontend caller references the old check
endpoint.

**Dependencies:** tomlkit as a runtime dependency.

**Evidence:** `src/haute/modelling/_mlflow_log.py`; `src/haute/routes/mlflow.py`;
`src/haute/routes/modelling.py`; `src/haute/_mlflow_utils.py`;
`src/haute/deploy/_config.py`; `tests/test_mlflow_routes.py`;
`tests/test_mlflow_log.py`; `tests/test_env_lazy_accessors.py`;
`frontend/src/api/client.ts`.

### MLF-U02 — Toolbar MLflow status chip and settings modal

**Why:** Connection state is invisible outside the Model Score and Optimiser
Apply editors, there is no UI to choose a backend, and the fetch-once settings
store cannot refresh after configuration changes, so a fixed connection stays
red until reload.

**Plan:** Add a toolbar status chip (coloured dot plus backend name, muted
"MLflow off" when unconfigured or broken) opening a `ModalShell` settings
modal with three plain-language mode cards — Databricks ("uses workspace
credentials from `.env`", showing the detected host or naming the missing
variable), MLflow server (URL field, prefilled from `MLFLOW_TRACKING_URI`
when present), Local folder ("zero setup", always displaying the *resolved*
runs folder — `mlruns/` in the project by default, or the env-derived or
configured custom folder) — plus a Test connection action rendering the
probe result inline. Save calls `PUT /api/mlflow/settings` and re-fetches
status; `useSettingsStore` gains an explicit invalidate/refetch path. Update
the frontend-shared spec (settings store, toolbar) before the code change.

**Acceptance:** The chip reflects each backend state including broken
configurations; the modal round-trips mode and URI through the settings API;
the local card displays the resolved folder — including an env-derived
custom folder — and saving leaves that folder unchanged; saving refreshes
status without a page reload; no secret value is ever rendered or submitted
by the browser.

**Dependencies:** MLF-U01.

**Evidence:** `frontend/src/components/Toolbar.tsx`;
`frontend/src/components/ModalShell.tsx`;
`frontend/src/stores/useSettingsStore.ts`;
`frontend/src/__tests__/stores/useSettingsStore.test.ts`;
`frontend/src/components/__tests__/Toolbar.test.tsx`.

### MLF-U03 — Plain-language, truthful MLflow UX across the node surfaces

**Why:** The Train pane's "MLflow Logging" fields suggest training logs runs
(it does not — logging is a separate manual button the user must find in the
Summary tab), the computed default experiment name is invisible, the log
button vanishes without explanation when MLflow is unavailable, discovery
errors collapse to one generic message, and the Model Score / Optimiser Apply
pickers assume the user already knows MLflow's registered-model, run, and
artifact concepts.

**Plan:** Train pane: retitle and reword the section so the fields read as
"used when you log this model after training", show the real computed default
experiment name as the placeholder, offer existing experiments through a
datalist backed by `/api/mlflow/experiments`, and add a one-line status with
a link that opens the settings modal. Summary tab: rename the action to "Log
run to MLflow" with the destination shown beneath it; when MLflow is
unavailable render the button disabled with the reason and a "Configure
MLflow" link instead of hiding it; success shows an "Open in Databricks" /
"Open run" link for databricks/server, and run ID plus folder path plus a
copyable `mlflow ui` command for local. Apply the same treatment to the
optimiser config section and preview log button. Model Score and Optimiser
Apply: keep the picker structure, add one-line plain-language help under the
source toggle, real empty states ("No registered models yet — train a model
and log it with a model name"), and categorised discovery errors mapped
server-side from MLflow's structured `RestException` `error_code` values
(authentication, permission denial, missing resource) plus transport-level
exception types for connectivity — never from exception class alone, and
without leaking internals; the status badge links to the settings modal.
Update the frontend-modelling-optimiser-ui, frontend-node-editors, and
mlflow-model-registry specs before the code change.

**Acceptance:** Every MLflow control states or links to where runs will go;
the log action is visible-with-reason in all backend states; local success
output includes the folder path and copyable command; discovery failures
surface a categorised, actionable message with named test cases for
authentication failure, permission denial, missing resource, and
connectivity failure; no UI text implies training or solving logs
automatically.

**Dependencies:** MLF-U01 for status/error categories; MLF-U02 for the
settings-modal link targets.

**Evidence:** `frontend/src/panels/ModellingConfig.tsx`;
`frontend/src/panels/modelling/MlflowExportSection.tsx`;
`frontend/src/panels/modelling/SummaryTab.tsx`;
`frontend/src/panels/OptimiserConfig.tsx`;
`frontend/src/panels/OptimiserPreview.tsx`;
`frontend/src/panels/editors/ModelScoreEditor.tsx`;
`frontend/src/panels/editors/OptimiserApplyEditor.tsx`;
`frontend/src/panels/editors/MlflowModelPicker.tsx`;
`frontend/src/panels/editors/_shared.tsx`;
`frontend/src/hooks/useMlflowBrowser.ts`;
`frontend/src/panels/__tests__/ModellingConfig.test.tsx`;
`frontend/src/panels/modelling/__tests__/MlflowExportSection.test.tsx`;
`frontend/src/panels/editors/__tests__/ModelScoreEditor.test.tsx`;
`tests/test_mlflow_log_button_roundtrip.py`.

### MLF-U04 — Registration follows backend capability

**Why:** `log_experiment()` registers models only when the backend is
Databricks, so server-mode users with a registry-capable tracking server
never get registered models, and local users see a permanently empty
Registered Model dropdown in Model Score with no explanation.

**Plan:** Open with a verification spike against the pinned mlflow: confirm
whether the 3.x file store supports model-registry operations
(`register_model`, `search_registered_models`) under
`MLFLOW_ALLOW_FILE_STORE`. If it does, replace the Databricks-only
registration gate so a provided model name registers on every backend, and
prove the Model Score registered-model flow end-to-end on a local store. If
it does not, keep local mode run-based only and make the Model Score editor
say so explicitly for the active backend instead of showing an empty
dropdown. Server mode registers regardless of the spike outcome, with
registration failures reported per the existing warning path. Update the
modelling and mlflow-model-registry specs with the chosen outcome before the
code change.

**Acceptance:** With a model name set, logging registers the model on every
registry-capable backend and the new version appears in the Model Score
picker; on a registry-incapable backend the UI states that registered models
are unavailable for the active backend rather than presenting an empty list.

**Dependencies:** MLF-U01 for backend modes; the spike decides the local-mode
branch.

**Evidence:** `src/haute/modelling/_mlflow_log.py`; `src/haute/routes/mlflow.py`;
`tests/test_mlflow_log.py`; `tests/test_mlflow_io.py`;
`frontend/src/panels/editors/MlflowModelPicker.tsx`.
