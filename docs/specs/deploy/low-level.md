# Deploy — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/deploy/__init__.py` | Public API surface (`deploy`, `deploy_resolved`, config/result re-exports); target validation (`_validate_target`) and dispatch (`_dispatch_resolved`) by `config.target`. |
| `src/haute/deploy/_config.py` | `DeployConfig` (user input), target sub-configs (`DatabricksConfig`, `ContainerConfig`, `AzureContainerAppsConfig`, `AwsEcsConfig`, `GcpRunConfig`, `SafetyConfig`, `CIConfig`), `haute.toml` loading + schema validation, base-image pinning validation, `.env` loading, `resolve_config()` producing `ResolvedDeploy`. |
| `src/haute/deploy/_pruner.py` | Graph pruning to the output node's ancestors; `liveSwitch` live-branch collapsing; output/input/source node discovery. |
| `src/haute/deploy/_bundler.py` | Artefact discovery and collection (`collect_artifacts`): external files, file-backed optimiser artefacts, supported MLflow-sourced local models + feature contracts, and static data sources; path resolution and static-source schema drift checks. MLflow-sourced optimiser applies are deliberately not bundled. |
| `src/haute/deploy/_schema.py` | Input schema inference (read source file schema) and output schema inference (dry-run scoring with the bundled artefacts), with a graph-and-artefact-fingerprint-keyed on-disk cache. |
| `src/haute/deploy/_scorer.py` | Runtime scoring engine (`score_graph`, `score_graph_lazy`) shared by every deploy target; `NodeBuildHooks` interception for live-input injection and artefact-path remapping; stat-gated model/contract caches; execution admission. |
| `src/haute/deploy/_validators.py` | Pre-deploy validation (`validate_deploy`): structural checks + test-quote scoring; golden test-quote parsing and expected-output tolerance comparison; `score_test_quotes`. |
| `src/haute/deploy/_utils.py` | Shared helpers: `get_user`, `get_haute_version`, `build_manifest` (the canonical deploy-manifest schema). |
| `src/haute/deploy/_mlflow.py` | Databricks target: `deploy_to_mlflow`, `get_deploy_status`, MLflow signature/conda-env building, Databricks Model Serving endpoint create/update, connectivity pre-check. |
| `src/haute/deploy/_model_code.py` | MLflow models-from-code entry point: `HauteModel` (`mlflow.pyfunc.PythonModel` subclass) wrapping `score_graph`. |
| `src/haute/deploy/_container.py` | Container build/push orchestration, generated FastAPI `/health` and `/quote` runtime, stable JSON/NDJSON response handling, pinned Dockerfile generation, Docker subprocess calls, and the platform service-update stub. |
| `src/haute/deploy/_impact.py` | Impact analysis: batched endpoint scoring (Databricks SDK or HTTP `/quote`), quote-envelope normalisation, percent-change statistics, categorical segment breakdown, terminal/Markdown report formatting. |
| `src/haute/deploy/_request_limits.py` | Deployed-container request-body size limiting: environment resolution, `Content-Length` and streamed-byte enforcement before JSON materialisation, and structured limit/header/parse errors. |

## Key types and data structures

- **`DeployConfig`** (`_config.py`) — dataclass holding all user-provided settings:
  `pipeline_file`, `model_name`, `project_dir`, `target` (default `"databricks"`),
  `endpoint_name`/`endpoint_suffix`, `output_fields`, `test_quotes_dir`, and one nested
  config per target family (`databricks`, `container`, `azure_container_apps`,
  `aws_ecs`, `gcp_run`) plus `safety` and `ci`. `__post_init__` re-validates base-image
  pinning whenever `target` is one of the container-based targets. `effective_endpoint_name`
  is a computed property: `None` when no endpoint name or suffix was set (meaning "create
  no serving endpoint"), otherwise `(endpoint_name or model_name) + endpoint_suffix`.
  Constructed via `from_toml()` (parses + validates `haute.toml`, applies `.env` and
  `HAUTE_*` env-var overrides), `from_cli_args()` (no-TOML path, requires `pipeline_file`
  + `model_name`), or `override()` (returns a deep copy with non-`None` CLI kwargs
  applied — highest-priority layer).
- **`ResolvedDeploy`** (`_config.py`) — the target-agnostic handoff object, created only
  by `resolve_config()`: `config`, `full_graph`, `pruned_graph`, `input_node_ids`,
  `output_node_id`, `artifacts` (`dict[str, Path]`), `input_schema`/`output_schema`
  (`dict[str, str]` of column name → Polars dtype string), `removed_node_ids`.
- **`DeployResult`** (`_mlflow.py`) — returned by every backend: `model_name`,
  `model_version`, `model_uri`, `endpoint_url` (`None` if no endpoint configured/created),
  `manifest_path`.
- **`_TestQuoteCase`** (`_validators.py`, frozen dataclass) — `input` (dict),
  `expected` (dict or `None`), `tolerance_pct` (float fraction, e.g. `0.01` == 1%).
  Distinguishes plain quote rows from "golden" rows (any row containing `expected` or
  `tolerance_pct` keys).
- **`DeployScorePlan`** (`_scorer.py`, slotted dataclass) — lazy scoring result plus
  resources that must outlive collection: `lazy_frame`, `execution_context`,
  `temporary_paths` (registered-model temp files), `retained_lazy_frames` (kept alive
  when `output_fields` narrows the final select), `_cleaned_up` guard. `cleanup()` is
  idempotent and always releases execution admission.
- **`ContainerBuildResult`** (`_container.py`) — intermediate result of
  `build_and_push_image`: `image_tag`, `manifest_path`, `build_dir`, `model_name`,
  `model_version`.
- **Impact dataclasses** (`_impact.py`) — `ColumnStats` (per-output-column change
  statistics: mean/median/p5/p25/p75/p95 percent change, staging/prod means, total
  premium change), `SegmentRow` (per-categorical-value breakdown), `ImpactReport`
  (top-level result: row counts, `column_stats`, `segments`, `is_first_deploy` flag).
- **Deploy manifest** (`_utils.py::build_manifest`) — the canonical JSON schema shared by
  every target and the generated runtime code (`_container.py`'s `app.py` template,
  `_model_code.py`'s `HauteModel.load_context`): `haute_version`, `pipeline_name`,
  `pipeline_file`, `target`, `created_at`, `created_by`, `input_node_ids`,
  `output_node_id`, `output_fields`, `input_schema`, `output_schema`, `artifacts`
  (name → posix path), `pruned_graph` (full `model_dump()`), `nodes_deployed`,
  `nodes_skipped`, `nodes_skipped_names`.

## Control flow

**Resolution (`_config.py::resolve_config`)**
1. Re-validate base-image pinning (config may have been mutated after `__post_init__` via
   `.override()` or env overrides).
2. Load `.env` (idempotent).
3. `parse_pipeline_file(config.pipeline_file)` → `full_graph`; error if empty.
4. `find_output_node(full_graph)` — exactly one node with `nodeType="output"` or
   `config.output=True`, else `ValueError`.
5. `prune_for_deploy(full_graph, output_node_id)` → `pruned_graph`, kept ids, removed ids.
   Internally: `_live_only_edges()` first drops every non-live input edge into each
   `liveSwitch` node (matched by `input_scenario_map` or, as a fallback, by matching
   `inputs[0]` against an edge's source label), then `ancestors()` walks backward from the
   output node over the filtered edge set.
6. `find_deploy_input_nodes(pruned_graph)` — nodes with `nodeType="apiInput"`. If none,
   fall back to the single source node in the pruned graph (`ValueError` if zero or
   multiple non-apiInput sources exist).
7. `collect_artifacts(pruned_graph, deploy_inputs, pipeline_dir)` → `artifacts` dict.
8. `infer_input_schema()` (call `collect_schema()` on the first input node's source;
   lazy readers avoid row collection, while the existing plain-JSON reader may parse
   eagerly) and
   `infer_output_schema()` (dry-run score up to one sample row through the pruned graph using
   the just-collected artefact paths, cached by graph+artefact-identity fingerprint).
9. Assemble and return `ResolvedDeploy`.

`output_fields` is not passed into either schema inference or quote validation. It is
applied only by the deployed runtime, so those pre-deploy checks describe the unprojected
output and do not catch missing selected columns.

**Validation (`_validators.py::validate_deploy`)** — called by `deploy()` after
`resolve_config()`, before dispatch. Runs seven structural checks (output, inputs,
source-ness, artefact existence, unresolved Databricks sources, and non-empty input/output
schemas), then — if `config.test_quotes_dir` is a directory — pre-checks every `*.json` file's rows
against the required input-schema columns (catching a missing column before scoring even
starts, since a passthrough graph wouldn't otherwise surface it), then calls
`score_test_quotes()` to actually score every file and collect per-file errors. All
structural + test-quote errors are combined into one `DeployError` if any exist.
A missing/non-directory quote path and a directory containing no `*.json` files both
produce an empty quote result rather than a validation error.

**Dispatch (`__init__.py`)**
1. `deploy(config)`: `_validate_target(config.target)` (checked before resolution, so a
   bad target fails fast rather than surfacing as an unrelated "no output node" error) →
   `resolve_config()` → `validate_deploy()` → `_dispatch_resolved()`.
2. `deploy_resolved(resolved)`: the CLI's actual path — resolution and validation already
   happened, including validation's quote-scoring gate; the CLI then ran a second
   `score_test_quotes()` pass to print per-file timings. This function re-validates only
   the target and dispatches the *same* resolved object, so the backend receives exactly
   what was validated.
3. `_dispatch_resolved()`: `"databricks"` → `deploy_to_mlflow`; `"container"` →
   `deploy_to_container`; any other `_CONTAINER_BASED_TARGETS` member (`azure-container-apps`,
   `aws-ecs`, `gcp-run`) → `deploy_to_platform_container`.

**Container build (`_container.py::build_and_push_image`)** — shared by all
container-based targets. Creates `.haute_build/` under CWD; on any exception the whole
directory is removed (`except BaseException: shutil.rmtree(...); raise`). Steps: build
manifest via `_utils.build_manifest`, remap artefact paths to `artifacts/<name>`
container-relative paths, write `deploy_manifest.json`, copy every artefact file into
`artifacts/`, generate `app.py` from an f-string template, generate `Dockerfile` (base
image + pinned core deps + auto-detected extra deps from artefact file extensions), pick
an image tag (`<registry>/<model_name>:<git_sha>` or `<model_name>:<git_sha>`, falling
back to `"local"` if not in a git repo), `docker build`, then `docker push` only if a
registry is configured.

**Generated container HTTP runtime (`_container.py::_generate_app_source`)**
1. Startup loads `deploy_manifest.json`, reconstructs `PipelineGraph`, and resolves the
   request-body limit. `GET /health` returns status, model/version, deployed-node count,
   and the manifest input/output schemas.
2. `POST /quote` reads JSON through `_request_limits.read_limited_json_body` before
   constructing a `DataFrame`. A JSON object becomes a one-row request; a JSON array is
   used as the batch; any other JSON top-level value returns HTTP 400. Array element
   shapes are not pre-validated, and invalid UTF-8 is not converted to the structured
   parse-error envelope.
3. Admission occurs before Polars materialisation. Normal JSON requests call
   `score_graph()` and return an object containing rendered `rows`, the full
   `row_count`, `returned_rows`, `truncated`, `limit`, and execution metrics. At most
   1,000 rows are returned in that envelope even though `row_count` records the full
   result.
4. An `Accept` header containing `application/x-ndjson` or `application/ndjson` selects
   `score_graph_lazy()` and ordered, bounded collection in 50,000-row chunks. This path
   streams every result row as NDJSON rather than applying the 1,000-row JSON-envelope
   response cap; plan cleanup runs in the generator's `finally`.

**Databricks deploy (`_mlflow.py::deploy_to_mlflow`)** — checks Databricks connectivity
(HTTP GET with a short timeout, distinguishing 403 from unreachable), sets MLflow tracking
+ registry URI to Databricks/Unity-Catalog, builds the manifest, writes it under
`<pipeline_dir>/.haute_build/`, builds an MLflow `ModelSignature` from the resolved
schemas, sets/creates the experiment (suffix-isolated for staging), and inside one
`mlflow.start_run()` logs `HauteModel` as a `pyfunc` model-from-code with the manifest +
every bundled artefact attached, a `conda_env` with Python 3.11.11 and Haute exactly
pinned but `polars>=1.39.2` and optional `catboost>=1.2.8` as lower bounds, and
`registered_model_name` set to the UC
three-level name. Fetches the newly registered version, then creates or updates the
Databricks Model Serving endpoint (`_create_or_update_serving_endpoint`) if
`effective_endpoint_name` is set. Any exception during this whole block removes the build
directory before re-raising.

**Runtime scoring (`_scorer.py::score_graph_lazy` → `score_graph`)**
1. Resolve the graph's relative path configs against `graph.source_file`
   (`_resolve_runtime_graph_paths`) and attach bundled feature-contract paths to
   `modelScore` node configs (`_attach_bundled_feature_contracts`).
2. Build a `NodeBuildHooks(before_build=_intercept)` wrapper around the shared
   `_build_node_fn` builder. `_intercept` returns a replacement `(func_name, fn,
   returns_frame)` tuple — or `None` to fall through to the base builder — for six node
   situations: apiInput source in the live input set (inject the live `DataFrame`
   directly); `externalFile` with a remapped bundled path (run its user code against the
   loaded object, or passthrough if no code); `optimiserApply` either file-based-remapped
   or MLflow-sourced (`run`/`registered`, downloaded at request time); `modelScore` in three sub-cases (remapped
   model artefact present → score; contract bundled but no model artefact → validate
   contract then raise `RuntimeError`; neither present and no usable model source
   configured → raise `DeployError` immediately, never a silent passthrough); static
   `dataSource` with a remapped bundled path.
3. Compile the graph's preamble once so transform-node user code has access to the same
   namespace as at dev time.
4. For non-`DEPLOY_LIVE` profiles, build a `dataframe_cache_request` — the deployed
   scorer opts into the same dataframe execution cache the dev executor uses, fingerprinted
   on the live input `DataFrame`, the input node ids, and the resolved artefact-path
   identities so a cache hit requires byte-identical served artefacts.
5. `execute_lazy_graph()` runs the pruned graph to `output_node_id`; if `output_fields`
   was requested, the output lazy frame is retained (kept alive against GC) and narrowed
   with `.select(output_fields)`.
6. Returns a `DeployScorePlan`; `score_graph()` additionally collects it via
   `streaming_collect()` under the plan's execution profile, checkpointing before/after,
   and always calls `plan.cleanup()` in a `finally` — passing `preserve_primary_error=True`
   if collection itself raised, so cleanup failures during error unwinding are logged
   rather than masking the original exception.

**Impact analysis (`_impact.py::build_report`)** — takes two prediction lists (Databricks
SDK responses or HTTP `/quote` JSON, both normalised through
`_normalise_http_prediction_payload` / `_unwrap_prediction_envelopes` to handle the
`{rows, row_count, ...}` quote-envelope shape transparently), truncates both lists to the
shorter length *before* building DataFrames (`failed_rows = len(input_df) - scored`),
computes per-numeric-column `ColumnStats`, and — for the first numeric column only —
a `_segment_breakdown` over every categorical (`Utf8`, 2–50 unique values) input column
with at least 10 rows per segment value, keeping the top 10 by absolute mean change.

**Wire-size limits (`_request_limits.py`)** — the default `/quote` JSON body limit is
8 MiB. `HAUTE_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES` has first precedence;
`HAUTE_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_MB` is used only when the bytes variable is absent.
Configured values must be positive base-10 integers. A declared `Content-Length` above
the limit is rejected before the body stream is consumed; absent or in-range headers do
not bypass the cumulative streamed-byte check. Malformed/negative headers and invalid
JSON have separate structured payloads. A body exactly at the configured limit is valid.

## Edge cases and invariants

- **Pipeline-relative path resolution wins over CWD** (`_bundler.py::_resolve_path`):
  absolute paths are `.resolve()`d as-is; relative paths are resolved against
  `pipeline_dir` and that resolution is always used (even if the file doesn't exist there
  — existence is checked separately by `_check_exists`), specifically so a file that
  exists under the pipeline directory always wins over a same-named file elsewhere. Every
  returned path is absolute, so the manifest never bakes in a re-resolution-dependent
  pointer.
- **Base image pinning is validated twice**: once in `DeployConfig.__post_init__` (catches
  the common case at construction time) and again in `resolve_config()` (catches
  mutation via `.override()`, env overrides, or direct attribute writes after
  construction — the actual last chokepoint before a build is committed).
- **`liveSwitch` live-branch resolution has two paths**: primary is matching
  `input_scenario_map`'s `"live"`-valued key against a connected edge's source label; if
  no `input_scenario_map` exists, fallback matches `config["inputs"][0]` (positional) the
  same way. If an explicit `input_scenario_map` names a live input that doesn't match any
  connected edge, `ValueError`. If the legacy `inputs[0]` fallback matches nothing, the
  current implementation records no live source and silently drops all incoming switch
  edges.
- **Feature-contract bundling filename convention**: the bundler only looks for a bare
  `feature_contract.json` sitting next to a downloaded model (the MLflow-download-cache
  convention); training itself writes per-model `{name}.feature_contract.json` and never
  populates the bare name — this asymmetry is intentional and documented inline
  (`_bundler.py::_bundle_feature_contract`).
- **`tolerance_pct` is a raw fraction, not a percentage.** `_validators.py::_parse_tolerance_pct`
  rejects any value `> 1` with a message suggesting the likely intended value divided by
  100 — guards against an operator writing `tolerance_pct: 5` meaning "5%" and
  accidentally accepting a 500%-tolerant (i.e. any-value-passes) golden quote.
- **Golden vs plain test-quote rows** are distinguished purely by key presence (`expected`
  or `tolerance_pct` present ⇒ golden; row must then be `{"input": {...}, "expected":
  {...}?, "tolerance_pct": ...?}` with only those keys plus `_`-prefixed metadata keys).
  A `tolerance_pct` without `expected` is an error (nothing to compare against tolerance).
- **Zero-baseline percent-change is defined, not undefined**: per-row zero production
  values are accepted only when staging equals production exactly; otherwise the change
  raises `ValueError`. `_CHANGE_EPSILON` applies to changed-row counting and the aggregate
  total-zero comparison, not this row-level equality test.
- **Prediction/input length mismatch in impact reports** is truncated (not padded or
  errored): `scored = min(len(staging_preds), len(prod_preds))`, with the shortfall
  recorded as `failed_rows`, before DataFrames are built — avoiding materialising rows
  that will be discarded.
- **Model/contract artefact caches are stat-gated and per-key-locked**
  (`StatGatedCache` in `_scorer.py`), so concurrent `/quote` requests on container start
  perform exactly one disk load per artefact and later requests short-circuit on a cheap
  `(mtime_ns, size)` stat check; failed loads are never cached.
- **Empty artefact set produces an empty fingerprint string** (`artifact_identity_fingerprint`
  returns `""` when `artifact_paths` is empty/`None`) specifically so graphs bundling no
  artefacts keep byte-identical cache keys across runs.
- **`_next_version()` is a stub** (`_container.py`) — always returns `1`; the design doc
  notes the registry or git tags are the real version source in production. Callers must
  not treat the container-target `model_version` as authoritative.
- **Container `output_fields` type check**: `score_graph_lazy` explicitly rejects
  `output_fields` passed as a bare `str`/`bytes` (which would otherwise silently iterate
  per-character) with `ValueError`.
- **Static sources must support bounded batch reads.** Schema-declared static plain JSON
  is rejected during bundle verification under `DEPLOY_BATCH`; undeclared JSON can pass an
  at-most-one-row `DEPLOY_LIVE` schema dry-run but fail when a multi-row request selects
  `DEPLOY_BATCH`. Parquet/NDJSON and bounded-compatible declared CSV are the supported path.
- **Local model format boundary.** Registry resolution can discover an MLflow pyfunc
  directory, but deploy's download/bundle path requires a file and local scoring supports
  `.cbm` and `.rsglm`; such pyfunc directories are not deployable through this path.
- **Project-local preamble imports are not bundled.** The preamble embedded in graph JSON
  still executes, but the bundler does not walk/copy imported Python modules.
- **Request and response bounds are independent.** The request byte limit controls JSON
  materialisation and defaults to 8 MiB; the ordinary JSON response envelope independently
  returns at most 1,000 rows. NDJSON is the explicit all-rows streaming response.

> NOTE: `_pruner.py::find_deploy_input_nodes` only returns `apiInput` nodes even though
> `find_source_nodes` also recognises `dataSource` and `constant` node types as sources;
> `resolve_config`'s fallback-to-single-source-node path is the only way a non-`apiInput`
> source becomes a deploy input, and it only fires when there is exactly one such source.

## Error handling

| Exception | Raised where | Propagates to |
|---|---|---|
| `haute.errors.DeployError` | `_config.py` (unpinned base image), `_bundler.py` (static-source schema drift), `_scorer.py` (unscoreable `modelScore`), `_validators.py` (aggregated validation failure) | `deploy()` / `resolve_config()` callers; carries structured `context` kwargs (e.g. `node_id`, `expected_columns`) rendered into `str()`. |
| `ValueError` | `_pruner.py` (missing/multiple output nodes, bad explicit `liveSwitch` config), `_config.py` (zero/ambiguous fallback source nodes, unknown TOML keys, missing `from_cli_args` required fields), `__init__.py` (unknown target), `_scorer.py` (bad `output_fields` type, negative `row_count`), `_impact.py` (non-finite predictions, zero-baseline change) | Caller of `resolve_config`/`deploy`/scoring functions; container `/quote` endpoint catches the `BoundedMemoryUnsupportedError` subclass specially (422) but a bare `ValueError` from scoring falls into the generic 500 handler. |
| `FileNotFoundError` | `_bundler.py::_check_exists` (missing artefact on disk), `_bundler.py::_download_model_artifact` (MLflow download landed but file missing) | Propagates uncaught through `resolve_config()`. |
| `RuntimeError` | `_container.py` (Docker unavailable/build/push failure, unpinned Dockerfile dependency), `_scorer.py` (`modelScore` contract matched but no model artefact — deliberately after the contract check), `_mlflow.py` (Databricks host/token unset, unreachable, `run_id`-less registered model version) | Uncaught to caller; `_check_docker_available`'s message specifically redirects the operator to CI. |
| `FeatureMismatchError` | `_scorer.py::_assert_runtime_contract_matches` (live schema disagrees with bundled training contract on feature set, dtype, or categorical levels) | Uncaught through scoring; surfaces in the container's generic 500 handler or the MLflow `pyfunc` boundary. |
| `RequestBodyLimitError` / `RequestBodyHeaderError` / `RequestBodyParseError` | `_request_limits.py::read_limited_json_body` | Caught explicitly in the generated container `app.py`'s `/quote` handler → HTTP 413 / 400 / 422 with a structured `to_payload()` body. |
| `ExecutionAdmissionError` / `ExecutionMemoryLimitExceededError` | Raised by the execution-engine's admission layer, invoked via `admit_deploy_execution` | Caught in `/quote` → HTTP 507. |
| `ExecutionCancelledError` | Execution engine | Caught in `/quote` → HTTP 499 with `job_id`/`operation` context. |
| `NotImplementedError` | `__init__.py::_validate_target` (planned targets: `sagemaker`, `azure-ml`), `_container.py::_update_service` (platform-container service update not yet built) | Uncaught to caller; the `_update_service` message names the built image tag (which is pushed only when a registry was configured). |
| Any other `Exception` | Runtime scoring inside `/quote` | Caught by the container's catch-all, logged via `logger.exception("deploy_quote_failed")`, returned as HTTP 500 with `error_code: "deploy_internal_error"`. The MLflow `pyfunc` predict path has no equivalent catch-all. |

`build_and_push_image` and `deploy_to_mlflow` both wrap their build-directory-writing
steps in `try/except BaseException: shutil.rmtree(...); raise` — cleanup happens on
*every* exception type (including `KeyboardInterrupt`/`SystemExit`), not just
`Exception` subclasses, and the original exception always re-propagates unchanged.

## Testing

Tests live in `tests/`, one or more files per concern, all using `pytest` with plain
function/class-based tests (no property-based testing in this component). Key files and
what they cover:

- **`test_deploy.py`** — broad unit coverage
  across nearly every module: `TestPruner` (ancestor walking, `liveSwitch` collapsing),
  `TestBundler` (artefact discovery per node type, path resolution precedence),
  `TestResolveRegisteredModel`, `TestScorer`, `TestSchema`, `TestValidators`,
  `TestConfig`, MLflow helpers (`TestBuildUcModelName`, `TestBuildExperimentName`,
  `TestDatabricksTracking`, `TestServingEndpoint`, `TestModelsFromCode`,
  `TestHauteModel`).
- **`test_deploy_config.py`** — `DeployConfig.from_toml` behaviour, `effective_endpoint_name`,
  env-var override precedence, TOML-schema-vs-dataclass-field sync (guards against a
  dataclass field silently missing from the TOML allowlist or vice versa), unknown-key
  rejection, `validate_deploy` structural checks, `override()` semantics.
- **`test_deploy_config_and_bundle.py`** — `TestBaseImageMustBePinned` (every accepted/
  rejected base-image tag form) and `TestBundledPathsAreAbsolute` (the pipeline-dir-wins
  path resolution invariant — the test this behaviour's docstring explicitly names).
- **`test_deploy_container.py`** — Docker availability check, `docker build`/`push`
  subprocess handling, git-SHA detection, platform-container service-update stub.
- **`test_container.py`** — base-image/model-name hardening; generation and import of the
  actual generated `app.py`; `/health` and `/quote` requests through FastAPI `TestClient`;
  admission, cancellation, memory and bounded-streaming error mappings; body-limit env
  precedence and streamed-byte enforcement; JSON response truncation/envelope boundaries;
  NDJSON streaming; Dockerfile dependency pins and build-directory cleanup.
- **`test_deploy_contract_integrity.py`** — static-data-source schema drift detection,
  `validate_deploy` failing on bad test quotes, feature-contract bundling end-to-end.
- **`test_deploy_dispatch.py`** — `_dispatch_resolved` routing for every target family
  (Databricks, container, platform-container, planned/`NotImplementedError`,
  unknown/`ValueError`) and `DeployResult` field population.
- **`test_deploy_expected_output_validation.py`** — golden test-quote parsing (legacy flat
  vs. wrapped `input`/`expected` forms), tolerance comparison (numeric, boolean, Decimal,
  large-integer zero-tolerance), missing-expected-column and row-count-mismatch failure
  modes, malformed golden-row rejection, end-to-end `validate_deploy` blocking on drift.
- **`test_deploy_identity_parity.py`** — `artifact_identity_fingerprint` determinism, the
  output-schema cache folding artefact identity into its key, the deliberate
  `modelScore`-passthrough-rejection behaviour, artefact threading through
  `score_test_quotes`.
- **`test_deploy_internals.py`** (also large) — `HauteModel.load_context`/`predict`,
  `infer_input_schema`/`infer_output_schema` (including cache hit/miss), `score_graph`
  live-input injection, `output_fields` narrowing, static-data-source/external-file/
  optimiser-apply/model-score artefact remapping (each as its own large test class,
  `TestScoreGraphModelScoreRemap` being the biggest), missing-output and bad-input
  error paths, temp-file cleanup, `.env` loading, `resolve_config` edge cases,
  `get_deploy_status`, MLflow signature/conda-env building, Databricks connectivity
  checks, and (also in this file) the full `_impact.py` surface — batched scoring,
  `ColumnStats`/`SegmentRow`/report building, terminal/Markdown formatting — plus the
  `TestBugB4PrunerUsesOriginalEdges` and `TestBugB10LexicographicVersionComparison`
  regression classes.
- **`test_deploy_mlflow_gaps.py`**, **`test_deploy_validators_gaps.py`** — targeted
  gap-filling for branches not hit by the main suites (progress callbacks, artefact copy,
  version selection, build-dir cleanup-on-error for MLflow; unparseable test-quote files,
  guard clauses in `score_test_quotes`).
- **`test_deploy_scorer_artifact_cache.py`** — `StatGatedCache` behaviour specific to
  deploy: model loaded once per artefact across repeated scoring calls, cache
  invalidation on stat-gate (mtime/size) change, deploy artefact-path fingerprint
  determinism, byte-for-byte response equality across cache hits.
- **`test_deploy_scorer_coverage.py`** — canonical dtype mapping, runtime-vs-training
  contract matching (`_assert_runtime_contract_matches`), contract-only (no model
  artefact) scoring rejection, static-data-source remap edge cases.
- **`test_deploy_utils.py`** — `get_user`/`get_haute_version` fallback behaviour,
  `build_manifest` field population.
- **`test_cli_deploy.py`** — thin CLI-boundary tests (`TestDeploy`) — the deploy command
  wiring itself is specced in [cli](../cli/high-level.md), but this file exercises the
  handoff into `haute.deploy`'s public functions.
- **`_deploy_helpers.py`** — shared test fixtures/builders (not itself a test file) used
  across the above.

**Strategy**: overwhelmingly unit tests against the deploy module functions directly, with
real (small, synthetic) `PipelineGraph` objects and temp-directory artefacts rather than
mocked graph structures — `_scorer.py`'s tests in particular build real pruned graphs and
assert on actual scored output, since the scoring correctness is the component's core
risk surface. Docker subprocess/helpers and Databricks SDK calls are patched or faked
rather than requiring a live daemon or workspace. No dedicated integration test spins up a real container or a real
Databricks endpoint — the seam between "manifest + artefacts are correct" and "the
generated container actually serves them correctly" is not exercised end-to-end in this
suite.

**Known gaps**: no test exercises the platform-container (`azure-container-apps`,
`aws-ecs`, `gcp-run`) service-update path beyond confirming it raises
`NotImplementedError`, since the implementations don't exist yet. Generated `app.py` is
imported and exercised in-process through `TestClient`, but no test boots a built Docker
image, contacts a real registry/Databricks workspace, or verifies a cloud service update.
