# MLflow destinations roadmap

## Scope

Owns where each node's MLflow activity goes: how the available tracking
destinations (Local folder, MLflow server, Databricks) are configured and
surfaced, how a modelling or optimiser node chooses where to log, and how a
Model Score or Optimiser Apply node chooses where to browse and load from.
Shipped behaviour is defined by the mlflow-model-registry, modelling,
optimiser, server-api, execution-engine, deploy,
frontend-modelling-optimiser-ui, frontend-node-editors, and frontend-shared
component specifications and their regression tests.

The shipped configuration stores one workspace-wide tracking mode in the
`[mlflow]` table of `haute.toml`, shows it in a toolbar chip, and every node
echoes it. That is the wrong shape for the real decision. Someone developing
a new model, setting up a pipeline, or exploring does not want those runs in
the team's official tracking backend, while a finished model does belong
there. The decision is per node, so the control belongs on the node.

Agreed product decisions for this component:

- **Destination choice is per node.** Modelling and optimiser nodes choose
  where to log; Model Score and Optimiser Apply nodes choose where to browse
  experiments, runs, registered models, and versions, and where to load from.
- **Workspace configuration is an inventory, not a selection.** `haute.toml`
  `[mlflow]` holds the MLflow server URL and the local runs folder. Databricks
  credentials stay in `.env` or the selected Databricks profile. The
  single-mode key goes away; there are no users to migrate. No secret ever
  round-trips through the browser.
- **Auto is the absent value.** A node with no stored choice resolves to the
  configured remote (Databricks first, then MLflow server), else Local. Auto
  follows the environment the pipeline runs in rather than baking a
  destination into the node at first render. An explicit choice is stored and
  fails loudly if its destination is not configured where the pipeline runs.
- **No silent fallback on a broken remote.** If the auto destination is
  configured but the probe fails, auto still points at it, the light turns
  amber with the reason on hover, and the user picks Local. Nothing
  redirects on its own.
- **Connection lights.** Databricks and MLflow server each show green
  (configured and the probe passed), amber (configured, probe failed, reason
  on hover), or greyed (not configured, hover names what to set and links to
  the settings modal). Local has no light: it always works.
- **GUI logging stays manual.** The post-training and post-solve "Log to
  MLflow" actions always send the node's current destination, empty meaning
  Auto. The request is the single source of truth for where a run goes; the
  job's training-time snapshot is never consulted for it. No auto-log
  toggle. Standalone training retains its existing optional
  experiment-triggered logging and honours the exported node destination;
  choosing a destination alone never enables logging.
- **Help lives in tooltips.** Instructions that used to sit as always-visible
  prose in the Train pane move behind Info icons using the existing
  `Tooltip` component and the Info-icon label pattern the offset field uses.
- **The toolbar chip goes.** The settings modal survives as the inventory
  editor and opens from a gear icon in each node's MLflow section and from
  the greyed-light hover.

Decisions to confirm before `MLF-D01` starts:

- The middle option is labelled "MLflow server". "Managed" was suggested;
  Databricks is also a managed service, so the plainer label avoids the
  ambiguity. Either is a one-line change.
- When both remotes are configured, auto prefers Databricks. Rare in
  practice; the per-node control covers the exception.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| MLF-D01 | Planned | P2 | Destination inventory, per-destination resolution, destination-aware settings, probe, discovery, and log APIs, and training-export parity. |
| MLF-D02 | Planned | P2 | Node-level destination selector with connection lights and tooltip help on the modelling and optimiser surfaces; toolbar chip removed. |
| MLF-D03 | Planned | P2 | Destination-aware Model Score and Optimiser Apply: browse, load, generate, and deploy from the chosen destination. |

`MLF-D01` is the startable package. `MLF-D02` builds on its APIs and owns the
shared selector component; `MLF-D03` reuses that component on the read
surfaces and carries the destination through the loader, codegen, and deploy.

## Planned improvements

### MLF-D01 — Destination inventory and destination-aware resolution

**Why:** `resolve_tracking_config()` returns exactly one destination for the
whole workspace, the `[mlflow]` table stores a `mode`, the status endpoint
reports one resolved backend, and neither log endpoint nor any discovery
endpoint accepts a destination. Every node therefore logs to and reads from
the same place, so exploratory runs land in the official backend and nothing
in the API lets a node say otherwise.

**Plan:** In `_mlflow_settings.py`, redefine the `[mlflow]` table as an
inventory: `tracking_uri` (MLflow server URL, `http(s)` only, no embedded
credentials) and `folder` (local runs folder, default `mlruns`); `mode` is
removed and rejected as an unknown key. Introduce a destination domain with
keys `databricks`, `server`, `local`. `resolve_destination(key, project_root)`
returns a `TrackingConfig` for that key or raises `MlflowConfigError` naming
the missing prerequisite. Databricks is configured by a syntactically valid
`MLFLOW_TRACKING_URI=databricks://<profile>` reference, or, without a profile
reference, both `DATABRICKS_HOST` and `DATABRICKS_TOKEN` in the environment
(`.env`). A selected profile takes precedence over the host/token pair and
does not require those environment variables. The profile reference counts
as configured even if its credentials cannot be loaded or its probe fails:
report the failure without falling back to environment credentials or another
destination. Server requires `[mlflow] tracking_uri` or an `http(s)`
`MLFLOW_TRACKING_URI`; local always resolves. `list_destinations(project_root)`
returns the three entries with `configured`, secret-free `destination`,
`config_source`, and `detail`; a profile-backed entry identifies the selected
profile. `resolve_tracking_config()`
becomes the auto rule: databricks if configured, else server if configured,
else local.

`MLFLOW_TRACKING_URI` still seeds the matching inventory entry by form
(`databricks://profile` seeds databricks; `http(s)` seeds server when the toml
has no `tracking_uri`; `file:` or a path seeds the local folder when the toml
has no `folder`); `haute.toml` wins where both are present, and credential
re-attachment for a redacted server URI is unchanged.

In `routes/mlflow.py`, replace `GET /api/mlflow/status` with
`GET /api/mlflow/destinations?probe=<bool>`: the inventory plus, when
probing, `ok`, `category`, and `detail` for each configured remote. Probes
run concurrently under the existing bounded-probe helper; an unconfigured
remote reports `configured=false` with its detail and is never probed; local
is never probed. `GET`/`PUT /api/mlflow/settings` drop `mode`; PUT accepts
`tracking_uri` and `folder` independently, empty clearing the key, and still
persists the currently resolved local folder for a bare save.
`POST /api/mlflow/test-connection` takes `destination` plus optional draft
`tracking_uri`/`folder` and probes that candidate as a save would make it.
The four discovery endpoints (`/experiments`, `/runs`, `/models`,
`/model-versions`) accept `destination` (empty = auto) and resolve their
client through `resolve_destination`.

On the write path, `LogExperimentRequest` and `OptimiserMlflowLogRequest`
gain `destination: Literal["", "databricks", "server", "local"] = ""`, where
`""` is auto. The request is authoritative: the log routes never consult the
job's training-time `mlflow_destination` snapshot, because the node's choice
can change after training and the UI must never show one destination while
the run goes to another. The frontend always sends the node's current value
(MLF-D02); an API caller that omits the field gets auto and can pass a key.
An unknown value is rejected by validation before any write. This
deliberately differs from `experiment_name`, whose snapshot fallback is an
existing contract left unchanged. `configure_mlflow_tracking(destination)`
resolves the effective choice through `resolve_destination`; experiment-name
defaults use that same resolved backend, keeping `/Shared/haute/` for
databricks only. MODEL and OPTIMISER node configs gain the optional
`mlflow_destination` field (absent = auto) in the schema, node validation,
and the UI contract fixtures; its backend consumer is the standalone
training path below, not the log routes.

Carry `mlflow_destination` through `_train_config.py`, `TrainingJob`, and
`_export.py` into `log_experiment(destination=...)`. Generated training scripts
preserve explicit choices and leave Auto unresolved until execution. Retain
standalone training's existing optional logging when `mlflow_experiment` is
set, while GUI training stays manual; destination selection alone must not
enable logging. Teach `deploy/_config.py` unknown-key validation the new
`[mlflow]` shape. Update the mlflow-model-registry, modelling, optimiser, and
server-api specs before the code change.

**Acceptance:** A precedence matrix covers toml × env × credential
combinations per destination key: each explicit key resolves to its own
destination or fails with an actionable, secret-free reason; auto prefers
databricks, then server, then local; a `mode` key is rejected loudly; an
`http(s)` `MLFLOW_TRACKING_URI` seeds server only when the toml has no
`tracking_uri`. A profile reference configures Databricks without host/token
variables, retains precedence when a server and host/token pair are also
configured, and preserves its profile through settings save and probe.
A broken selected profile stays configured with a failed probe and a
secret-free reason; auto and explicit Databricks operations fail without
switching credentials or destinations. The destinations endpoint truthfully
reports all three entries, probes only configured remotes, and never blocks
longer than the bounded probe budget. Settings PUT→GET round-trips through a real
`haute.toml` preserving unrelated sections and comments, and a bare local
save keeps an env-derived folder. Each log route logs the same completed job
to local and to a second destination and the resulting `backend` and
`tracking_uri` name the requested one; a request for an unconfigured
destination fails with the prerequisite in the detail and writes nothing.
For both log routes, a job trained with an explicit `mlflow_destination` in
its config is logged with an omitted field, an empty string, and a different
explicit key: omitted and empty both go to the current auto destination, the
key goes to its own, and the job's snapshot never decides. Unknown
destination values are rejected before any write.
An executed training export with Local selected and a remote configured logs
to Local; an Auto export follows the execution environment, an unavailable
explicit destination fails without a remote write, and a destination without
an experiment does not enable logging. Discovery endpoints honour
`destination`. No frontend caller references `/api/mlflow/status`, and its
fixture is removed.

**Dependencies:** None beyond the shipped tomlkit runtime dependency.

**Evidence:** `src/haute/modelling/_mlflow_settings.py`;
`src/haute/modelling/_mlflow_log.py`; `src/haute/routes/mlflow.py`;
`src/haute/modelling/_train_config.py`; `src/haute/modelling/_training_job.py`;
`src/haute/modelling/_export.py`;
`src/haute/routes/modelling.py`; `src/haute/routes/optimiser.py`;
`src/haute/schemas.py`; `src/haute/deploy/_config.py`;
`tests/test_mlflow_settings.py`; `tests/test_mlflow_connection_routes.py`;
`tests/test_mlflow_routes.py`; `tests/test_mlflow_log.py`;
`tests/test_mlflow_log_button_roundtrip.py`;
`tests/test_train_config_builder.py`; `tests/test_modelling_export.py`;
`tests/test_training_evaluation.py`;
`tests/fixtures/ui_contracts/mlflow_destinations_response.json`;
`tests/fixtures/ui_contracts/mlflow_settings_response.json`;
`tests/fixtures/ui_contracts/mlflow_test_connection_response.json`.

### MLF-D02 — Node-level destination selector, lights, and tooltip help

**Why:** The toolbar chip presents the destination as a workspace concern
and the node sections only echo it, so the decision a user actually makes
("where should this run go?") has no control. The Train pane's MLflow
section carries two always-visible explanatory paragraphs that crowd a small
panel, and the destination line is informational only. The post-training and
post-solve log buttons cannot send a destination.

**Plan:** Remove the toolbar chip and its tests. `useSettingsStore` replaces
its single-status cache with the destinations inventory, fetched once with
probing on the first render of any MLflow section, re-fetched by
`invalidateMlflow()` after a settings save, and re-probed by a small re-check
icon beside the selector. Build one shared `MlflowDestinationSelector`
component: a three-option segmented radio (Databricks, MLflow server, Local)
where each remote carries a light dot with a hover tooltip (green shows the
secret-free destination; amber shows the probe detail; greyed is disabled and
names what to set with a "Configure" link that opens the settings modal), the
resolved destination string sits under the row, and a gear icon opens the
modal. Auto is the absent config value: the radio shows the auto-resolved
option as selected with an "auto" suffix until the user clicks; a click
stores the key in node config and a "Use auto" affordance clears it.

Modelling Train pane: retitle the section "MLflow logging" with an Info icon
whose tooltip carries the manual-only note; mount the selector; give the
Experiment path and Model name labels Info icons (the experiment tooltip
names the computed default, which follows the selected destination:
`/Shared/haute/<label>` for databricks, else `<label>`); delete the standalone
prose paragraphs and the "Logging destination" line. The experiment datalist
loads from the selected destination via the discovery `destination`
parameter. Optimiser config gets the same selector and tooltip treatment on
its experiment field. The post-training `MlflowExportSection` and the
optimiser `ExportMlflowSection` show one line naming the node's destination
under the button, render the button disabled with the reason when that
destination is not configured, and always include `destination` in the log
request: the explicit node key or `""` for Auto. The optimiser `DetailCard`
log button gets the same disabled-with-reason treatment and a "Configure"
link that opens the settings modal, replacing its tooltip that points at the
toolbar chip. The local success surface (run ID,
folder, copyable `mlflow ui` command) is unchanged. The settings modal becomes
the inventory editor: an MLflow server URL field, a Local folder field showing
the resolved folder, a read-only Databricks block naming the selected profile,
the detected host when no profile is selected, or the missing configuration
(a profile reference or host/token variables), one Test action per remote
rendering the categorised probe result inline, and Save persisting
`tracking_uri` and `folder` only. Update the
frontend-shared and frontend-modelling-optimiser-ui specs before the code
change.

**Acceptance:** No toolbar MLflow chip renders. The selector shows the three
light states per remote from a mocked inventory, including hover text and
the disabled greyed state. With no stored choice, the same node config
selects different options under different mocked inventories (auto follows
the environment); an explicit choice persists in node config, survives
pipeline save and load, and is what the log request sends. After a job
completes with an explicit choice, "Use auto" removes the node field and the
next log request contains `destination=""`; both training and optimiser
results name the current auto backend rather than the job's saved choice.
The optimiser detail card's disabled log button names the reason and opens
the settings modal, and no surface's copy references a toolbar chip.
A profile-backed Databricks entry is selectable without environment
host/token variables and the modal identifies that profile. No always-visible
instruction prose remains in the section; the tooltip text is asserted on
hover. The log buttons are disabled with a reason only when the node's own
destination is unconfigured, not when some other remote is. The settings
modal round-trips server URL and folder and never renders or submits a
secret. Optimiser config and preview receive the same behaviour under the
same tests.

**Dependencies:** MLF-D01 for the destinations, settings, probe, discovery,
and log APIs and the `mlflow_destination` node config field.

**Evidence:** `frontend/src/components/Toolbar.tsx`;
`frontend/src/components/MlflowSettingsModal.tsx`;
`frontend/src/stores/useSettingsStore.ts`; `frontend/src/components/Tooltip.tsx`;
`frontend/src/panels/modelling/OffsetFieldLabel.tsx`;
`frontend/src/panels/ModellingConfig.tsx`;
`frontend/src/panels/modelling/MlflowExportSection.tsx`;
`frontend/src/panels/OptimiserConfig.tsx`; `frontend/src/panels/OptimiserPreview.tsx`;
`frontend/src/panels/optimiser/DetailCard.tsx`;
`frontend/src/hooks/useMlflowBrowser.ts`; `frontend/src/api/client.ts`;
`frontend/src/api/types.ts`; `frontend/src/components/__tests__/Toolbar.test.tsx`;
`frontend/src/components/__tests__/MlflowSettingsModal.test.tsx`;
`frontend/src/panels/__tests__/ModellingConfig.test.tsx`;
`frontend/src/panels/modelling/__tests__/MlflowExportSection.test.tsx`;
`frontend/src/__tests__/stores/useSettingsStore.test.ts`.

### MLF-D03 — Destination-aware Model Score and Optimiser Apply

**Why:** The read nodes resolve the single workspace backend, so a model
logged to Local is invisible to a Model Score node whose auto destination is
Databricks and the exploration loop never closes inside one pipeline. The
loader, the deploy bundler, and generated scripts all resolve tracking at
runtime from the environment, ignoring where the pipeline author browsed,
and the model cache is keyed without the backend, so the same run ID on two
backends would alias.

**Plan:** MODEL_SCORE and OPTIMISER_APPLY node configs (MLflow source types
only) gain the optional `mlflow_destination` field (absent = auto). Their
editors mount the shared selector above the source picker in place of the
status badge; `useMlflowBrowser` and `MlflowModelPicker` pass the selected
destination to the discovery endpoints so experiments, runs, registered
models, and versions come from that destination; switching destination
clears the picked run or model with an inline note, since identifiers are
not portable across backends. `resolve_mlflow_source`, `load_mlflow_model`,
and the `_mlflow_utils` client factory take a destination; `score_from_config`
reads it from node config, including the config sidecar used by generated
scripts. Preserve it through pipeline save, parse, and standalone execution.
`deploy/_bundler.py` resolves the bundled model through the node's destination.

Carry the same choice through `load_mlflow_optimiser_artifact` and every
config-driven caller: `_node_apply.py` for pipeline execution,
`deploy/_scorer.py` for deployed request-time loading, and
`_optimiser_apply_explainability.py` for explanations. MLflow optimiser
artifacts retain their existing request-time loading behaviour in deployments;
updating the model bundler alone does not cover them. Generated optimiser
scripts preserve the destination in their config sidecar too. Explicit
destinations must be configured in the environment that loads the artifact,
including deployed scoring and explanations.

Before any in-memory or disk-cache lookup, resolve and validate the effective
backend and derive a secret-free identity from its server endpoint, canonical
absolute local folder, or effective Databricks workspace host and selected
profile, together with its registry target. Neither an absent Auto value nor
the category key (`local`, `server`, `databricks`) identifies a backend. A
profile's effective host must participate so repointing the same profile
cannot reuse the previous workspace's artifacts; unresolved configuration
fails before a cached artifact can be served. Use a filesystem-safe digest
of that identity in disk-cache paths and include the identity in model and
optimiser cache keys and artifact I/O locks. Use the same resolved backend
throughout each load; credentials never enter cache paths or diagnostic keys.
Update the mlflow-model-registry, optimiser, frontend-node-editors,
execution-engine, and deploy specs before the code change.

**Acceptance:** An end-to-end test logs a training job to Local while auto
resolves to a second destination, then scores it through a MODEL_SCORE node
pointed at Local, and the same shape passes for an optimiser artifact through
OPTIMISER_APPLY. Discovery calls from both editors carry the destination and
the picker lists only that backend's content. Switching destination clears
the selection and shows the note. Save/parse round-trips and generated config
sidecars retain explicit destinations and leave Auto absent; executed scripts
honour the selected destination for both read-node types. A model deploy
bundle resolves the same way. Deployed optimiser scoring and explanation
loading use Local when auto points remotely and return results from that
Local artifact; unavailable explicit destinations fail without consulting
another backend.

Cache regressions use identical run IDs and artifact paths with different
contents across destinations and across two endpoints of the same category.
Warm-cache tests cover changing the server URL, local folder, Databricks host
or a selected profile's host, and Auto resolving to a different backend;
each load returns the newly selected backend's artifact. Removing an explicit
destination's prerequisites after warming its cache raises the configuration
error rather than serving the cached artifact. Model memory/disk caches,
optimiser caches, and deploy model downloads share the identity rule;
concurrent loads on different backends remain isolated, and cache clear and
eviction still work with the destination-aware layout.

**Dependencies:** MLF-D01 for destination-aware discovery and resolution;
MLF-D02 for the shared selector component and store shape.

**Evidence:** `src/haute/_mlflow_io.py`; `src/haute/_mlflow_utils.py`;
`src/haute/_model_scorer.py`; `src/haute/_codegen_builders.py`;
`src/haute/codegen.py`; `src/haute/deploy/_bundler.py`; `src/haute/schemas.py`;
`src/haute/_optimiser_io.py`; `src/haute/_node_apply.py`;
`src/haute/deploy/_scorer.py`; `src/haute/_optimiser_apply_explainability.py`;
`frontend/src/panels/editors/ModelScoreEditor.tsx`;
`frontend/src/panels/editors/OptimiserApplyEditor.tsx`;
`frontend/src/panels/editors/MlflowModelPicker.tsx`;
`frontend/src/panels/editors/_shared.tsx`; `frontend/src/hooks/useMlflowBrowser.ts`;
`tests/test_mlflow_io.py`; `tests/test_mlflow_io_concurrency.py`;
`tests/test_mlflow_model_cache_key_contract.py`; `tests/test_optimiser_io.py`;
`tests/test_model_score_codegen.py`; `tests/test_parser_roundtrip.py`;
`tests/test_deploy.py`; `tests/test_optimiser_apply.py`;
`tests/test_optimiser_apply_trace_enrichment.py`;
`tests/test_mlflow_routes.py`; `tests/test_mlflow_log_button_roundtrip.py`;
`frontend/src/panels/editors/__tests__/ModelScoreEditor.test.tsx`;
`frontend/src/hooks/__tests__/useMlflowBrowser.test.ts`.
