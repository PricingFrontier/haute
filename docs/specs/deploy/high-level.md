# Deploy — High-Level Specification

## Purpose

A Haute pipeline is authored as a graph that mixes training-time branches (data joins,
exports, model-fitting inputs) with the live scoring path. The deploy component turns
that graph into a **live pricing API**: a deployment artefact that accepts one quote
(or a batch) as JSON and returns a premium, with the same node-execution semantics the
pipeline used during development.

Haute deploys a **pricing API**, not a single ML model in the MLOps sense. Models
(CatBoost, GLMs, optimiser ratebooks) are internal nodes in the graph; the deployed
artefact is the whole scoring path from API input to output node. This distinguishes the
component from a typical "log model to registry" workflow — it prunes an arbitrary
pipeline graph down to only what live scoring needs, bundles its supported local artefacts,
and re-executes the graph at request time with live-injected input instead of
file reads.

The insurance-pricing domain adds a hard constraint that shapes the whole component: a
silently wrong deploy can misprice a large book of business before anyone notices. The
component is built to fail loudly rather than degrade gracefully — an unpinned base
image, a missing model artefact, a schema that drifted since training, or a modelScore
node with no usable source are all deploy-time errors, not warnings.

## Scope

In scope:
- Resolving a `haute.toml` + pipeline file into a fully validated, deployment-ready
  object (`ResolvedDeploy`).
- Pruning the full pipeline graph to the ancestors of the declared output node, and
  collapsing `liveSwitch` nodes onto their live branch.
- Discovering and bundling supported local artefacts (model files, file-backed optimiser
  artefacts, static data sources, feature contracts) the pruned graph needs. MLflow-sourced
  optimiser applies remain runtime MLflow dependencies.
- Inferring the input schema from the configured source and the output schema by an
  at-most-one-row dry-run through the pruned graph.
- Runtime scoring of the pruned graph against live-injected data, shared identically by
  every deploy target.
- Packaging and shipping to two implemented backends: Databricks (MLflow pyfunc + Model
  Serving) and generic Docker containers (FastAPI app); build plus optional registry push for
  three container-platform variants (Azure Container Apps, AWS ECS, GCP Cloud Run).
- Pre-deploy validation, including golden test-quote scoring with tolerance-based
  expected-output checks.
- Post-deploy impact analysis: comparing staging vs production predictions over a sample
  dataset.

Out of scope (owned elsewhere):
- Parsing the pipeline `.py` file into a `PipelineGraph` — see
  [pipeline-config](../pipeline-config/high-level.md).
- The general node-execution engine (`_build_node_fn`, lazy graph execution, dataframe
  caching) that deploy's scorer reuses — see
  [execution-engine](../execution-engine/high-level.md).
- Model training, feature-contract generation, and MLflow artefact logging at training
  time — see [mlflow-model-registry](../mlflow-model-registry/high-level.md) and
  [modelling](../modelling/high-level.md).
- `haute.toml` parsing mechanics beyond deploy's own schema section, and the `deploy`,
  `smoke`, `impact`, `status` CLI commands themselves (`src/haute/cli/_deploy.py`) — see
  [cli](../cli/high-level.md).
- Optimiser ratebook artefact format and application logic — see
  [optimiser](../optimiser/high-level.md).

## Behaviour

**Resolution.** Given a `DeployConfig` (loaded from `haute.toml`, CLI args, or
constructed programmatically), `resolve_config()` parses the pipeline, finds the single
node marked as output, prunes the graph to that node's ancestors (keeping only the live
branch of any `liveSwitch`), identifies the API input node(s), collects supported artefacts
the pruned path references, and dry-runs the graph once to infer input and output
schemas. The result is a `ResolvedDeploy` — the single handoff object every backend
target consumes.

**Validation.** Before anything ships, `validate_deploy()` checks structural invariants
(output/input nodes present in the pruned graph, input nodes are true sources, artefacts
exist on disk, schemas are non-empty, no unimplemented Databricks source stubs survived
pruning) and, when the configured test-quotes path is an existing directory, scores every
JSON file there through the
resolved graph. Test-quote files may be plain input rows or "golden" rows with an
`expected` output and a `tolerance_pct`; any row whose actual output falls outside
tolerance fails the deploy. All failures — structural and test-quote — are collected and
raised together as one `DeployError` so an operator sees the whole picture in one pass,
not a fix-rerun-fix cycle.

> NOTE: quote validation currently runs only when `test_quotes_dir` exists and is a
> directory. A missing, non-directory, or empty configured location produces no quote
> validation error and therefore does not act as a gate.

**Scoring.** The runtime scorer (`score_graph` / `score_graph_lazy`) executes the pruned
graph with a live-injected input `DataFrame` in place of the API input node's file read.
File-backed external/static/optimiser/model artefacts are redirected to bundled local
copies; `optimiserApply` nodes configured from an MLflow run or registered model resolve
that artefact at request time. This is the same
scoring path used by pre-deploy dry-runs, golden test-quote validation, the generated
FastAPI container, and the MLflow `pyfunc` model — deploy has exactly one scoring engine,
not one per target.

> NOTE: `output_fields` is applied only by the deployed container/pyfunc calls. Output
> schema inference and test-quote validation currently score the unprojected output, so a
> missing selected field can pass deployment validation and fail at runtime, and the
> MLflow signature can advertise columns the served projection omits.

**Packaging and shipping.** Two backends are implemented:
- **Databricks**: logs the pipeline as an `mlflow.pyfunc.PythonModel` (models-from-code),
  registers it in Unity Catalog, and creates/updates a Databricks Model Serving endpoint.
- **Container**: generates a FastAPI app (`POST /quote`, `GET /health`) and a Dockerfile
  pinned to dependency versions actually installed in the build environment, builds the
  image, and pushes it to a registry if one is configured. `/quote` accepts a single JSON
  object or an array, enforces a byte limit before materialisation, and either returns a
  stable JSON envelope capped at 1,000 returned rows or streams all rows as ordered NDJSON
  when requested through `Accept`.

Three further container-platform targets (Azure Container Apps, AWS ECS, GCP Cloud Run)
share the container build step (and push only when `container.registry` is configured),
but their service-update step is not yet implemented — `deploy()` then raises
`NotImplementedError` naming the image tag.

**Impact analysis.** `haute impact` (via `_impact.py`) scores a shared dataset through
both a staging and a production endpoint (Databricks serving or a container's `/quote`
HTTP endpoint), computes per-column percent-change statistics and categorical-segment
breakdowns, and renders a terminal or Markdown report. This is advisory, not a deploy
gate — it exists so a human reviewer can see exactly what a release changes before
approving it.

## Design rationale

- **Loud failure over silent fallback**, consistent with the rest of the codebase. A
  `modelScore` node with neither a bundled model artefact nor a bundled feature contract
  is rejected at scoring-plan build time rather than falling through to an identity
  passthrough that would silently omit the model from every quote
  (`_scorer.py::_intercept`, the `not _model_score_has_configured_source` branch). A
  bundled feature contract with a missing model artefact still validates the contract and
  then raises — it never manufactures a null prediction.
- **One scoring engine, not one per target.** `_scorer.py` reuses the same
  `_build_node_fn` / lazy-graph-execution infrastructure as the development executor,
  intercepting only the handful of node types that need live-input injection or
  artefact-path remapping (`NodeBuildHooks(before_build=_intercept)`). This keeps
  dev/deploy behavioural drift structurally impossible for every other node type.
- **The pruned graph JSON, not the `.py` file, is the deployment unit.** This was a
  deliberate design decision: `_utils.py::build_manifest` embeds `resolved.pruned_graph.model_dump()` verbatim into
  `deploy_manifest.json`, and at runtime `HauteModel.load_context`
  (`_model_code.py`) reconstructs the graph via `PipelineGraph.model_validate(manifest["pruned_graph"])`
  rather than re-parsing any source file. The graph JSON is inspectable without the
  original source and cannot drift from what was validated. Its embedded preamble still
  executes at runtime, however: project-local modules imported by that preamble are not
  collected by the bundler and must be installed/provided separately.
- **Reproducible container builds.** `container.base_image` must be pinned to an explicit
  patch version or a digest (`_config.py::_validate_base_image_pinning`) — floating tags
  like `python:3.11-slim` are rejected outright, because the image bytes tested today
  must be the image bytes served tomorrow. Dockerfile dependency versions
  (`haute`, `polars`, `fastapi`, `uvicorn`) are pinned to whatever is actually installed
  in the build environment rather than left unpinned or hardcoded, so a build environment
  drift is caught rather than silently propagated to a fresh, possibly incompatible pull.
- **Schema-cache identity tracks served bytes, not just graph shape.** The output-schema
  dry-run cache key folds in `artifact_identity_fingerprint()` — a stat-gated fingerprint
  of every bundled artefact's resolved path, `(mtime_ns, size)` gate, and content hash — so retraining a model in
  place under an unchanged `run_id`/`version="latest"` config still busts the cache
  instead of baking a stale `ModelSignature` into the manifest.
- **Pipeline-relative paths win over CWD-relative.** `_bundler.py::_resolve_path`
  deliberately prefers a path resolved against the pipeline file's directory over one
  resolved against the process's working directory, because the deployed container's CWD
  (`/`) has no relation to the developer's CWD at build time; baking in a CWD-relative
  path silently breaks the container.
- **Container-first, ML-platform-second.** This was a deliberate design decision: a
  container is just an API surface that any team already knows how to operate, needs
  no ML-platform lock-in or MLOps-specific knowledge, and runs anywhere (ECS,
  Container Apps, Cloud Run, Kubernetes, a VM, a laptop) instead of being tied to one
  platform's compute pricing — and it lets the implemented container-platform targets
  share one build instead of requiring a bespoke packaging pipeline per platform.
  SageMaker and Azure ML (`sagemaker`, `azure-ml`) are currently only planned target
  names: `_validate_target` raises `NotImplementedError` before resolution and makes no
  promise about their eventual packaging design. Databricks remains
  first-class for teams already on that platform, with a documented pandas bridge
  (`_model_code.py::HauteModel.predict`) as the one place the "Polars-native" rule is
  deliberately broken, because MLflow's `pyfunc` protocol requires it.
- **No target abstraction until three targets exist.** This was a deliberate design
  decision to avoid premature abstraction: with only two implemented targets
  (Databricks, container) plus scaffolded container-platform variants, any `Protocol`
  or base class would be guessing at a shape the codebase doesn't yet have enough
  concrete implementations to justify. Dispatch in `__init__.py::_dispatch_resolved`
  is a plain if-chain; container-platform targets share `deploy_to_platform_container()`
  but there is no `Protocol` or base class because the concrete shape of a third
  genuinely different target isn't known yet.

## Interactions

- **[pipeline-config](../pipeline-config/high-level.md)** — supplies
  `haute.parser.parse_pipeline_file()` and the `PipelineGraph` model that
  `resolve_config()` consumes; deploy never re-implements pipeline parsing.
- **[execution-engine](../execution-engine/high-level.md)** — deploy's scorer
  (`_scorer.py`) is a thin `NodeBuildHooks` wrapper around `_build_node_fn` and
  `execute_lazy_graph()`; it depends on the engine's lazy-graph execution, dataframe
  execution cache, and execution-context/admission machinery
  (`haute._execution_admission`, `haute._execution_context`).
- **[mlflow-model-registry](../mlflow-model-registry/high-level.md)** — `_bundler.py`
  downloads `modelScore` model artefacts and feature contracts from MLflow at bundle
  time (`_mlflow_io._resolve_artifact_local`, `_find_model_artifact`); `_mlflow.py`
  registers the deployed pipeline itself as a new MLflow model version. `_scorer.py`
  loads bundled models via `haute._mlflow_io.load_local_model`.
- **[modelling](../modelling/high-level.md)** — `_scorer.py` and `_bundler.py` both
  depend on `haute.modelling._feature_contract` (contract loading, matching, and
  categorical-level declarations) to detect train-vs-score drift.
- **[optimiser](../optimiser/high-level.md)** (spec pending) — `_scorer.py` intercepts
  `optimiserApply` nodes and dispatches to `haute._optimiser_io` /
  `haute.executor._dispatch_apply` for both file-based and MLflow-sourced ratebook
  artefacts.
- **[io-layer](../io-layer/high-level.md)** — `_bundler.py` and `_schema.py` read static
  data sources and infer schemas via `haute._io.read_data_source` /
  `haute.graph_utils.read_data_source`, respecting `ExecutionProfile.DEPLOY_BATCH` /
  `DEPLOY_LIVE` bounded-execution semantics.
- **[cli](../cli/high-level.md)** — `src/haute/cli/_deploy.py` is the production CLI
  consumer of this component's callable surface (`deploy_resolved()`, `resolve_config()`,
  `validate_deploy()`, `score_test_quotes()`, the `_impact` formatters). Deploy itself has
  no CLI concerns; it exposes plain functions and dataclasses.
- **Downstream consumers of a deployed pipeline** — the generated container's `app.py`
  and the MLflow `HauteModel` (`_model_code.py`) are themselves the runtime surface that
  policy-admin systems call; they are generated/packaged by this component but execute
  independently once shipped.

## Failure model

This codebase prefers loud failure over silent fallback, and deploy is where that matters
most: a silent wrong answer here mis-prices real policies.

- **Configuration errors** (unknown `haute.toml` keys, missing required CLI fields, an
  unpinned `container.base_image`, an unknown or not-yet-implemented deploy target) raise
  `ValueError`, `TypeError`, or `haute.errors.DeployError` at config-construction or
  resolve time — before any expensive work (pipeline parsing, artefact download, Docker
  build) starts. `deploy()` explicitly validates the target *before* resolving the config,
  so an unknown target never gets misreported as "no output node found".
- **Missing or drifted artefacts** raise `FileNotFoundError` (bundler: artefact file
  absent on disk) or `haute.errors.DeployError` (bundler: static data source's actual
  columns disagree with its declared `expected_columns`; scorer:
  `FeatureMismatchError` when a live request schema disagrees with a bundled training-time
  feature contract).
- **Structural graph errors** split across the two stages: no/multiple output nodes and an
  explicitly mapped `liveSwitch` whose live input matches no connected node raise
  `ValueError` from `_pruner.py`; zero or ambiguous fallback source nodes raise
  `ValueError` from `_config.resolve_config()`. An input node with incoming edges is a
  validation finding aggregated into `DeployError`, not a resolution-time `ValueError`.
- **Unscoreable `modelScore` nodes** — a node with no bundled model artefact and no
  usable model source configured — raise `DeployError` at scoring-plan build time, never
  a silent identity passthrough. A node with a bundled contract but no bundled model
  artefact validates the contract, then raises `RuntimeError` naming the node: the
  contract check happens first so drift is reported precisely even though scoring is
  impossible either way.
- **Pre-deploy validation failures** (structural checks and test-quote scoring/expected-
  output mismatches) are all collected and raised together as a single `DeployError`
  listing every failure, rather than surfaced one at a time across repeated deploy
  attempts.
- **Runtime request errors** in the generated container usually surface as structured JSON
  error envelopes: oversized bodies → 413, malformed `Content-Length` → 400, JSON syntax
  errors → 422, admission/memory-limit rejection → 507, cancelled execution →
  499, bounded-streaming-unsupported → 422, and other caught runtime failures → 500 with
  `error_code: "deploy_internal_error"` for anything else, logged via
  `logger.exception`. The MLflow `pyfunc` path (`_model_code.py`) has no equivalent
  envelope — exceptions propagate to whatever the Databricks serving runtime does with
  an unhandled `predict()` exception. Invalid UTF-8 currently escapes the structured body
  parser, and invalid array element shapes can fail later during Polars materialisation.
- **Docker/subprocess failures** (`docker info`, `docker build`, `docker push`) raise
  `RuntimeError` with the captured stderr; a `RuntimeError` telling the caller to run in
  CI is raised specifically when Docker itself isn't available. The `haute deploy` CLI
  separately blocks every non-dry-run target outside a recognised CI environment;
  `smoke` and `impact` do not apply that CI guard and can be run wherever their endpoint
  credentials and configured datasets are available.
- **Platform-container service update** (Azure Container Apps / AWS ECS / GCP Cloud Run)
  always raises `NotImplementedError` after a successful build and optional configured
  registry push, naming the image tag so the operator can update the service manually.
- **Known unsupported deploy inputs** fail rather than being made self-contained: plain
  JSON static sources are not batch-deployable; project-local preamble imports are not
  bundled; bundled local `modelScore` serving supports CatBoost `.cbm` and RustyStats
  `.rsglm`, while a discovered MLflow pyfunc directory cannot currently be bundled and
  served by this path.
- **Impact-analysis arithmetic** raises `ValueError` rather than producing a misleading
  percentage when predictions contain non-finite values, or when a percent-change or
  total-percent-change calculation would divide by a zero production baseline against a
  non-zero staging value (`_impact.py::_raise_for_non_finite_predictions`,
  `_zero_baseline_change_count`, `_total_percent_change`).

## Polars backend contracts (0.6.0)

Batch deployment and seedless live scoring will use the universal execution-plan facade.
This adds strategy/provenance diagnostics and consistent bounded execution decisions
without changing established input-to-output scoring semantics. Execution-engine owns
plan selection; see the [remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md).
