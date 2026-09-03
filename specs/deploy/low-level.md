# Deploy — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/deploy/__init__.py` | Public API surface (`deploy`, `deploy_resolved`, config/result re-exports); target validation (`_validate_target`) and dispatch (`_dispatch_resolved`) by `config.target`. |
| `src/haute/deploy/_config.py` | `DeployConfig` (user input), target sub-configs (`DatabricksConfig`, `ContainerConfig`, `AzureContainerAppsConfig`, `AwsEcsConfig`, `GcpRunConfig`, `SafetyConfig`, `CIConfig`), `haute.toml` loading + schema validation, base-image pinning validation, `.env` loading, `resolve_config()` producing `ResolvedDeploy`. |
| `src/haute/deploy/_pruner.py` | Graph pruning to the output node's ancestors; `liveSwitch` live-branch collapsing; output/input/source node discovery. |
| `src/haute/deploy/_bundler.py` | Artefact discovery and collection (`collect_artifacts`): external files, file-backed optimiser artefacts, supported MLflow-sourced local models + feature contracts, and retained Data Inputs; path resolution plus canonical provider/schema validation and a bounded one-row readability probe. MLflow-sourced optimiser applies are deliberately not bundled. |
| `src/haute/deploy/_schema.py` | Input schema inference (read source file schema), output schema inference (dry-run scoring with the bundled artefacts) with a graph-and-artefact-fingerprint-keyed on-disk cache, and bundle-time, target-aware batch strategy planning (`infer_deploy_execution_policy`) over the shared one-row sample (`_read_sample_row`), with a hard-capped-worker dry-run fallback (`_capped_worker_output_schema`) for an unprovable group-by. |
| `src/haute/deploy/_batch_scoring.py` | Multi-row `/quote` scoring in a hard-capped spawn worker: the picklable `BatchScoreRequest`/`BatchScoreOutcome` pair, the child entrypoint `score_batch_worker`, and the parent supervisor helpers `prepare_batch_scoring` / `accept_batch_outcome` / `deploy_batch_timeout_seconds`. |
| `src/haute/deploy/_scorer.py` | Runtime scoring engine (`score_graph`, `score_graph_lazy`) shared by every deploy target; `NodeBuildHooks` interception for live-input injection and artefact-path remapping; stat-gated model/contract caches; execution admission. |
| `src/haute/deploy/_validators.py` | Pre-deploy validation (`validate_deploy`): structural checks + exactly one test-quote scoring pass, returning successful per-file results to its caller; golden test-quote parsing and expected-output tolerance comparison; `score_test_quotes`. |
| `src/haute/deploy/_utils.py` | Shared helpers: `get_user`, `get_haute_version`, `build_manifest` (the canonical deploy-manifest schema). |
| `src/haute/deploy/_mlflow.py` | Databricks target: `deploy_to_mlflow`, `get_deploy_status`, MLflow signature/conda-env building, Databricks Model Serving endpoint create/update, connectivity pre-check. |
| `src/haute/deploy/_model_code.py` | MLflow models-from-code entry point: `HauteModel` (`mlflow.pyfunc.PythonModel` subclass) wrapping `score_graph`. |
| `src/haute/deploy/_container.py` | Container build/push orchestration, generated FastAPI `/health` and `/quote` runtime, stable JSON/NDJSON response handling, pinned Dockerfile generation, Docker subprocess calls, and the platform service-update stub. |
| `src/haute/deploy/_impact.py` | Impact analysis: batched endpoint scoring (Databricks SDK or HTTP `/quote`), quote-envelope normalisation, percent-change statistics, categorical segment breakdown, terminal/Markdown report formatting. |
| `src/haute/deploy/_request_limits.py` | Deployed-container request-body size limiting: environment resolution, `Content-Length` and streamed-byte enforcement before JSON materialisation, and structured limit/header/parse errors. |

## Key types and data structures

- **`DeployConfig`** (`src/haute/deploy/_config.py`) — dataclass holding all user-provided settings:
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
- **`ResolvedDeploy`** (`src/haute/deploy/_config.py`) — the target-agnostic handoff object, created only
  by `resolve_config()`: `config`, `full_graph`, `pruned_graph`, `input_node_ids`,
  `output_node_id`, `artifacts` (`dict[str, Path]`), `input_schema`/`output_schema`
  (`dict[str, str]` of column name → Polars dtype string), `execution_policy` (the
  bundle-time batch strategy record from `_schema.py::infer_deploy_execution_policy`),
  `removed_node_ids`.
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
- **`BatchScoreRequest`** (`_batch_scoring.py`, frozen dataclass) — the only evidence that
  crosses the spawn boundary: `graph`, `input_node_ids`, `output_node_id`,
  `artifact_paths`, `output_fields`, `input_path` (parent-written JSON rows),
  `result_path` (child-written parquet), `operation`.
- **`BatchScoreOutcome`** (`_batch_scoring.py`, frozen dataclass) — the child's picklable
  return: `row_count`, `execution_metrics` (the child context's payload), and on failure
  `failure_kind` (`contract` | `bounded` | `memory` | `cancelled` | `error`), `detail`,
  `payload`.
- **`BatchScorePlan`** (`_batch_scoring.py`, slotted dataclass) — parent-owned resources
  for one supervised worker: `request`, `budget` (`IsolatedExecutionBudget`),
  `execution_context` (the admitted `DEPLOY_BATCH` parent context), `worker_config`,
  `temp_dir`. `cleanup(preserve_primary_error=...)` removes the temp directory and
  releases the parent admission exactly once (idempotent).
- **`BatchScoreResult`** / **`BatchScoreError`** (`_batch_scoring.py`) — the accepted
  result (`result_path`, `row_count`, `execution_metrics`) and the typed parent-side
  failure carrying `kind`, `detail`, `payload`.
- **`BatchScoreCleanupError`** (`_batch_scoring.py`) — raised when the batch temp
  directory (request rows plus scored parquet) could not be removed and no primary error
  is in flight; a data-retention defect is never swallowed.
- **Deploy execution policy** (`_schema.py::infer_deploy_execution_policy`) — the
  bundle-time record on `ResolvedDeploy.execution_policy`, in the manifest, and on
  `/health`: `schema_version`, `profile` (`"deploy_batch"`), `runtime`
  (`hard_capped_worker` | `in_process`), `status`, `strategy`, `reason_code`,
  `blocking_node_id`, `blocking_operator`, `remediation`.
- **`ContainerBuildResult`** (`_container.py`) — intermediate result of
  `build_and_push_image`: `image_tag`, `manifest_path`, `build_dir`, `model_name`,
  `model_version`.
- **Impact dataclasses** (`src/haute/deploy/_impact.py`) — `ColumnStats` (per-output-column change
  statistics: mean/median/p5/p25/p75/p95 percent change, staging/prod means, total
  premium change), `SegmentRow` (per-categorical-value breakdown), `ImpactReport`
  (top-level result: row counts, `column_stats`, `segments`, `is_first_deploy` flag).
- **Deploy manifest** (`_utils.py::build_manifest`) — the canonical JSON schema shared by
  every target and the generated runtime code (`_container.py`'s `app.py` template,
  `_model_code.py`'s `HauteModel.load_context`): `haute_version`, `pipeline_name`,
  `pipeline_file`, `target`, `created_at`, `created_by`, `input_node_ids`,
  `output_node_id`, `output_fields`, `input_schema`, `output_schema`, `execution_policy`,
  `artifacts`
  (name → posix path), `pruned_graph` (full `model_dump()`), `nodes_deployed`,
  `nodes_skipped`, `nodes_skipped_names`.

## Control flow

**Resolution (`src/haute/deploy/_config.py::resolve_config`)**
1. Re-validate base-image pinning (config may have been mutated after `__post_init__` via
   `.override()` or env overrides).
2. Load `.env` (idempotent).
3. `parse_pipeline_file(config.pipeline_file)` → `full_graph`; error if empty.
4. `find_output_node(full_graph)` — exactly one node with `nodeType="output"` or
   `config.output=True`, else `ValueError`.
5. `prune_for_deploy(full_graph, output_node_id)` → `pruned_graph`, kept ids, removed ids.
   Internally: `_live_only_edges()` first drops every non-live input edge into each
   `liveSwitch` node — selection is **per edge**, matching `input_scenario_map`'s
   `"live"`-valued key against each edge's input name
   (`haute._graph_utils.edge_input_name`), never per source node, so two frames from
   one apiInput mapped `quotes=live, drivers=batch` keep exactly the `quotes` edge —
   then `ancestors()` walks backward from the output node over the filtered edge set.
6. `find_deploy_input_nodes(pruned_graph)` — nodes with `nodeType="apiInput"`. If none,
   accept a single `dataInput` source as the deliberate legacy live-input form.
   Zero/multiple sources, or a sole `constant`/other unsupported source, fail with a
   correction that names the node/type and asks for an API Input.
7. `collect_artifacts(pruned_graph, deploy_inputs, pipeline_dir, project_root=...)` →
   `artifacts` dict. The pipeline and every local runtime input are already canonicalised
   and checked for project-root containment. Bundling repeats that check at the copy
   boundary. Explicit `modelScore.feature_contract_path` files are copied under the
   canonical `<node>__feature_contract.json` key and override an adjacent downloaded
   contract. MLflow artifact identifiers reject absolute and `..`-containing forms before
   download.
   A retained file-backed Parquet input is derived direct (`data_input_is_direct`) and
   bundles its validated source file. Every other retained Data Input is snapshot-backed;
   its ready snapshot acquires a `SourceCacheStore.lease()` that
   is retained by `ResolvedDeploy` until dispatch completes, and records provider,
   identity, generation, signature, checksum, row/column counts, and creation time as
   manifest provenance.
8. `infer_input_schema()` (call `collect_schema()` on the first input node's source;
   lazy readers avoid row collection, while the existing plain-JSON reader may parse
   eagerly).
   Then `infer_deploy_execution_policy()` plans the served `DEPLOY_BATCH` strategy once,
   over the same one-row sample (`_read_sample_row`) and the same bundled-contract graph
   preparation `_scorer._score_graph_lazy` performs, under a short-lived
   `deploy_bundle_policy` admission released in `finally`. The record is target-aware:
   `resolve_config` passes `batch_runtime="hard_capped_worker"` for a target in
   `_CONTAINER_BASED_TARGETS` and `"in_process"` otherwise (Databricks pyfunc, which
   scores multi-row inputs in the serving process via
   `_model_code.py::HauteModel.predict`), and the value is recorded as the policy's
   `runtime`. Bundle time has no native cap, so a `GroupByExecutionUnsupportedError` with
   `reason_code == "materialisation_estimate_unavailable"` is translated — for
   `hard_capped_worker` only — into the policy that worker will actually apply
   (`status="warned"`, `strategy="full-width-conservative"`, the runtime
   `reason_code="materialisation_estimate_unavailable_conservative"`, a remediation naming
   the hard-capped envelope); for `in_process` it raises `DeployError`, because that
   runtime has no cap and would reject the same group-by on every request. Every other
   planning rejection, and an `unsupported` strategy, raises `DeployError` naming the
   blocking node and operator for both runtimes. The record is logged once
   (`deploy_execution_policy`, warning when warned).
   Only then `infer_output_schema()` (dry-run score up to one sample row through the
   pruned graph using the just-collected artefact paths, cached by
   graph+artefact-identity fingerprint).
   The dry-run scores uncapped under `DEPLOY_LIVE`, so a group-by whose materialisation
   estimate is unavailable is rejected there even though the served batch would run it
   conservatively. That one rejection
   (`GroupByExecutionUnsupportedError(reason_code="materialisation_estimate_unavailable")`)
   falls back to `_capped_worker_output_schema`: the same one-row dry-run re-run inside
   the *served* batch worker — `prepare_batch_scoring(...,
   operation="deploy_bundle_schema")` + `run_isolated_worker(score_batch_worker, ...)` +
   `accept_batch_outcome`, then `pl.read_parquet_schema()` over the parquet the worker
   wrote, with `plan.cleanup(primary_error=...)` on every path. The group-by therefore
   runs once under its full hard-capped envelope, exactly the policy the manifest records,
   and the schema is read from what the worker actually produced. The one-row sample
   bounds only the request-derived side of the graph — a group-by over a bundled static
   source still materialises that source in full — so the admission gate is never simply
   relaxed here; the cap is what keeps the bundle build bounded. A `BatchScoreError` from
   the child (the group-by exceeded the cap, say) or any `IsolatedWorker*Error`
   (including `IsolatedWorkerMemoryLimitUnsupportedError` on a host that cannot install a
   cap) raises `DeployError` naming the node and operator from the original rejection: the
   bundle could not prove the served batch path can produce the schema, and the deployed
   endpoint would fail the same way on every batch request. Every other rejection
   propagates.
   This ordering is deliberate: policy inference needs only the sample, and an
   `in_process` target must refuse an unprovable group-by before any capped dry-run is
   attempted.
   Validate `output_fields` as a non-empty, duplicate-free list of non-empty strings
   present in that full schema, then retain the projected schema in configured order.
9. Assemble and return `ResolvedDeploy`, carrying the policy on `execution_policy`.

**Validation (`_validators.py::validate_deploy`)** — called by `deploy()` after
`resolve_config()`, before dispatch. Runs seven structural checks (output, inputs,
source-ness, artefact existence, canonical Data Input direct readability or snapshot readiness, and non-empty input/output
schemas), rechecks the projected output-field invariant, then — if
`config.test_quotes_dir` is configured — requires an existing directory containing at
least one `*.json` file and pre-checks every quote's rows
against the required input-schema columns (catching a missing column before scoring even
starts, since a passthrough graph wouldn't otherwise surface it), then calls
`score_test_quotes()` with `config.output_fields` to score the same projection served at
runtime and collect per-file errors. All
structural + test-quote errors are combined into one `DeployError` if any exist.
On success, `validate_deploy()` returns that exact per-file result list so a
presentation caller can render timings/status without executing the scorer again.
`score_test_quotes()` remains a result-producing helper and returns an empty list when
called directly without a usable directory; `validate_deploy()` owns enforcement of the
configured gate.

**Dispatch (`src/haute/deploy/__init__.py`)**
1. `deploy(config)`: `_validate_target(config.target)` (checked before resolution, so a
   bad target fails fast rather than surfacing as an unrelated "no output node" error) →
   `resolve_config()` → `validate_deploy()` → `_dispatch_resolved()`.
2. `deploy_resolved(resolved)`: the CLI's actual path — resolution and validation already
   happened, including validation's quote-scoring gate; the CLI renders the result list
   returned by that gate. This function re-validates only
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
image + pinned core deps + auto-detected extra deps from artefact file extensions, with
`HAUTE_EXECUTION_MEMORY_POLICY=strict_server`), pick
an image tag (`<registry>/<model_name>:<git_sha>` or `<model_name>:<git_sha>`, falling
back to `"local"` if not in a git repo), `docker build`, then `docker push` only if a
registry is configured.

The manifest paths are resolved by the generated runtime against the image's
`WORKDIR /app`. `_container.py`'s `artifacts/<name>` remapping and the Dockerfile's
`WORKDIR /app` plus `COPY artifacts/ artifacts/` must change together; neither side is
an independently relocatable contract.

**Generated container HTTP runtime (`_container.py::_generate_app_source`)**
1. Startup loads `deploy_manifest.json`, reconstructs `PipelineGraph`, and resolves the
   request-body limit. `GET /health` returns status, model/version, deployed-node count,
   the manifest input/output schemas,
   `memory_enforcement="admission_rss_best_effort"` (this describes application
   admission/RSS checkpoints for single-row live scoring, not an OS or container hard
   memory limit), `batch_memory_enforcement`
   (`_worker_isolation.resolve_worker_memory_enforcement()` — `"required"` or
   `"best_effort"`, the hard-cap policy multi-row batches run under), and
   `execution_policy` (the manifest's bundle-time strategy record).
   Startup is fail-closed on that record: `_require_fail_closed_batch_enforcement`
   raises `RuntimeError` at module load when the policy's `status` is `"warned"` — a
   promise that only holds while the batch worker runs under an enforced hard cap — and
   `resolve_worker_memory_enforcement()` is not `"required"`. The message names the
   policy, the blocking node/operator, and `HAUTE_WORKER_MEMORY_ENFORCEMENT=required`.
   Without the gate, a `best_effort` host whose cap installation fails would start a
   child with no native backend, and the planner would reject the unavailable estimate on
   every batch request while `/health` still advertised conservative execution. Under
   `required` enforcement a host that cannot install a cap instead answers each batch
   with the typed 507 `native_memory_cap_unavailable` (`run_isolated_worker` raises
   `IsolatedWorkerMemoryLimitUnsupportedError`, mapped through
   `isolated_worker_memory_detail`).
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
4. A request with more than one row never scores in the service process. `_quote_batch`
   calls `_batch_scoring.prepare_batch_scoring` in the threadpool (admits one
   `DEPLOY_BATCH` context — the batch path always admits that profile whatever the
   row count, so the bundle's one-row schema dry-run runs under the served envelope
   and the batch `modelScore` contract —, derives `isolated_execution_budget`, creates a private
   `haute_deploy_batch_*` temp directory, writes `input.json`), then awaits
   `routes._isolated_worker_async.run_isolated_worker_async(score_batch_worker,
   request, budget, config=...)` — one spawn per batch, `process_name="haute-deploy-batch"`,
   `memory_limit_bytes` equal to the admitted headroom, `timeout_seconds` from
   `deploy_batch_timeout_seconds()` (`HAUTE_DEPLOY_BATCH_TIMEOUT`, default 300s). The
   child creates a worker-local context (`create_isolated_execution_context`), builds the
   request `DataFrame` and scores it through `score_graph_lazy`, sinks the result to
   `result.parquet` under a `deploy_batch_sink` stage, and returns row count plus its own
   `metrics_payload`. Because the child runs under a native cap, an unavailable
   materialisation estimate is warned and run conservatively there instead of rejected.
   `accept_batch_outcome` validates the outcome type, re-reads the parquet's row count,
   and rejects a missing/unreadable/mismatched file. The parent renders the same JSON
   envelope from that parquet (`head(limit)`) with the child's `execution_metrics`, or
   streams every row as NDJSON through `bounded_collect_batches` into the same spool. The
   plan's `cleanup()` runs on every path.
5. Batch error mapping: `BatchScoreError` kinds `contract`/`bounded` → 422 (the child's
   payload, else the typed bounded envelope), `memory` → 507, `cancelled` → 499,
   `error` → 500 `deploy_internal_error`; `IsolatedWorkerMemoryLimitExceededError`,
   `IsolatedWorkerMemoryLimitUnsupportedError`, a crash whose exit code looks
   memory-bound, and a remote memory type → 507 carrying
   `_worker_isolation.isolated_worker_memory_detail(exc, operation="deploy_quote",
   memory_limit_bytes=plan.budget.memory_limit_bytes)`; `IsolatedWorkerTimeoutError` →
   504 `{"error_code": "deploy_batch_timeout", "operation": "deploy_quote",
   "timeout_seconds": ...}`; every other worker death → logged 500.
6. An `Accept` header containing `application/x-ndjson` or `application/ndjson` selects
   `score_graph_lazy()` and ordered, bounded collection in 50,000-row chunks. Rows are
   encoded into a `SpooledTemporaryFile` from Starlette's worker threadpool before
   response headers are committed; the spool spills to disk above its memory threshold
   and is then streamed to the client. A late scoring error is therefore logged and
   returned as HTTP 500, never HTTP 200 with a truncated NDJSON body, while `/health`
   and other requests can continue to use the event loop. Plan and spool cleanup run on
   every path.

**Databricks deploy (`_mlflow.py::deploy_to_mlflow`)** — checks Databricks connectivity
(HTTP GET with a short timeout, distinguishing 403 from unreachable), sets MLflow tracking
+ registry URI to Databricks/Unity-Catalog, builds the manifest, writes it under
`<pipeline_dir>/.haute_build/`, builds an MLflow `ModelSignature` from the resolved
schemas (`Categorical` and parameterised `Enum` map to MLflow string; genuinely
unrepresentable Polars types fail loudly), sets/creates the experiment
(suffix-isolated for staging), and inside one
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
   `modelScore` node configs (`_attach_bundled_feature_contracts`). When a remapped
   native model has no bundled feature-contract sidecar, load that local model through
   the stat-gated deploy cache and attach its feature names plus any offset column as
   the node's internal deploy-contract inputs before strategy planning. Projection and
   boundary checks therefore describe the artifact actually served and never contact
   the original MLflow run or registry merely to resolve a remapped model's columns.
   A bundled feature contract remains authoritative when present. Before live-input
   interception, every relevant `apiInput` edge is also validated through the shared
   edge-name resolver, so direct `DataFrame` injection cannot bypass the required
   frame `sourceHandle`.
2. Build a `NodeBuildHooks(before_build=_intercept)` wrapper around the shared
   `_build_node_fn` builder. `_intercept` returns a replacement `(func_name, fn,
   returns_frame)` tuple — or `None` to fall through to the base builder — for four node
   categories: `apiInput` or `dataInput` source in the live input set (inject the live `DataFrame`
   directly); retained direct-Parquet `dataInput` nodes (remap their configured path to the
   bundled source); retained snapshot-backed `dataInput` nodes with a bundled
   `node_id__snapshot.parquet` (scan the leased parquet through a deploy-only interception
   path while retaining the canonical config unchanged, user code,
   preamble namespace, and executor post-processing);
   `externalFile` with a remapped bundled path (run its user code against the
   loaded object, or passthrough if no code); `optimiserApply` either file-based-remapped
   or MLflow-sourced (`run`/`registered`, downloaded at request time); `modelScore` in three sub-cases (remapped
   model artefact present → score; contract bundled but no model artefact → validate
   contract then raise `RuntimeError`; neither present and no usable model source
   configured → raise `DeployError` immediately, never a silent passthrough).
3. Compile the graph's preamble once so transform-node user code has access to the same
   namespace as at dev time.
4. For non-`DEPLOY_LIVE` profiles, build a `dataframe_cache_request` — the deployed
   scorer opts into the same dataframe execution cache the dev executor uses, fingerprinted
   on the live input `DataFrame`, the input node ids, and the resolved artefact-path
   identities so a cache hit requires byte-identical served artefacts.
5. `execute_lazy_graph()` runs the pruned graph to `output_node_id` with
   `prepare_inputs=False`: a deployed scorer serves a request against artefacts that were
   resolved and validated at deploy time, so it never builds or refreshes a source snapshot
   on the serving path — a missing generation is a deploy-time packaging failure to be
   raised, not a per-request build. If `output_fields`
   was requested, the output lazy frame is retained (kept alive against GC) and narrowed
   with `.select(output_fields)`.
6. Returns a `DeployScorePlan`; `score_graph()` additionally collects it via
   `streaming_collect()` under the plan's execution profile, checkpointing before/after,
   and always calls `plan.cleanup()` in a `finally` — passing `preserve_primary_error=True`
   if collection itself raised, so cleanup failures during error unwinding are logged
   rather than masking the original exception.

**Impact analysis (`src/haute/deploy/_impact.py::build_report`)** — takes two prediction lists (Databricks
SDK responses or HTTP `/quote` JSON, both normalised through
`_normalise_http_prediction_payload` / `_unwrap_prediction_envelopes` to handle the
`{rows, row_count, ...}` quote-envelope shape transparently), aligns both lists and the
sampled input to their common length *before* building DataFrames while preserving the
original sampled-row count (`failed_rows = sampled_rows - scored`),
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

- **Live scoring adds no per-request process spawn.** A one-row `/quote` (a JSON object,
  or a one-element array) scores in the service process exactly as before; only
  `len(rows) > 1` reaches `_batch_scoring`. A warm worker pool is deliberately out of
  scope.
- **Exactly one worker per batch request**, launched with the parent's admitted headroom
  as its hard RSS cap. The child never re-admits: `create_isolated_execution_context`
  rebuilds the budget locally, and the parent context is released exactly once by
  `BatchScorePlan.cleanup`, on every success and every failure path.
- **The batch temp directory is private and always removed — loudly.** The parent owns
  `haute_deploy_batch_*/input.json` and `result.parquet`; the child removes a partial
  `result.parquet` on every classified failure, and `cleanup(primary_error=...)` removes
  the directory after the JSON envelope or the NDJSON spool has been materialised.
  Removal never uses `ignore_errors`: with no primary error in flight a failure raises
  `BatchScoreCleanupError` and the request is answered with a 500 instead of the computed
  response (a leftover copy of the request rows is a data-retention defect even after a
  good score); with a primary error in flight the failure is attached to it as a note and
  logged `deploy_batch_cleanup_failed`, so the original failure still reaches the client.
  `prepare_batch_scoring` cleans up a setup failure the same way. The parent admission is
  released exactly once either way.
- **The bundle's policy is only as strong as the target's runtime.** A `warned` /
  `full-width-conservative` record is issued only for `runtime == "hard_capped_worker"`;
  the in-process (Databricks) runtime fails the bundle instead of promising a cap it does
  not have. The serving host must honour the same promise: a container carrying a
  `warned` policy refuses to start unless `HAUTE_WORKER_MEMORY_ENFORCEMENT=required`, so
  the manifest can never advertise conservative execution that the running service would
  not actually perform.

- **Pipeline-relative path resolution wins within the project**
  (`_bundler.py::_resolve_path`): local paths use
  `resolve_runtime_file_path(..., prefer="pipeline", enforce_project_root=True)`.
  A pipeline-relative existing file wins over a project-root peer, while an absolute,
  traversal, or symlink-resolved path outside the project raises `DeployError` before
  copy/load. Every accepted path is absolute, so the manifest never bakes in a
  re-resolution-dependent pointer.
- **Container manifest artefact paths are image-relative, not pipeline-relative**:
  every bundled path is rewritten to `artifacts/<name>` and resolves against
  `WORKDIR /app`; the generated Dockerfile copies the same build-context directory to
  `/app/artifacts`.
- **Base image pinning is validated twice**: once in `DeployConfig.__post_init__` (catches
  the common case at construction time) and again in `resolve_config()` (catches
  mutation via `.override()`, env overrides, or direct attribute writes after
  construction — the actual last chokepoint before a build is committed).
- **`liveSwitch` live-branch resolution** matches `input_scenario_map`'s
  `"live"`-valued key against a connected edge's **input name**
  (`haute._graph_utils.edge_input_name` — the frame label for an apiInput-frame edge,
  the sanitised source label otherwise; the same derivation the executor, projection,
  and codegen use, so two frames from one apiInput are individually routable). The
  configured live input must match a connected edge.
- **Feature-contract bundling filename convention**: the bundler only looks for a bare
  `feature_contract.json` sitting next to a downloaded model (the MLflow-download-cache
  convention); training itself writes per-model `{name}.feature_contract.json` and never
  populates the bare name — this asymmetry is intentional and documented inline
  (`_bundler.py::_bundle_feature_contract`).
- **`tolerance_pct` is a raw fraction, not a percentage.** `_validators.py::_parse_tolerance_pct`
  rejects any value `> 1` with a message suggesting the likely intended value divided by
  100 — guards against an operator writing `tolerance_pct: 5` meaning "5%" and
  accidentally accepting a 500%-tolerant (i.e. any-value-passes) golden quote.
- **Test-quote rows** use one envelope:
  `{"input": {...}, "expected": {...}?, "tolerance_pct": ...?}`, with only those keys
  plus `_`-prefixed metadata keys. A `tolerance_pct` without `expected` is an error
  because there is nothing to compare against tolerance.
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
- **Deploy contract validity is row-count invariant.** `DEPLOY_LIVE` and `DEPLOY_BATCH` retain
  different admission, cache, and bounded-I/O policies, but both use strict contract resolution.
  A known builder-contract resolution failure therefore returns the same typed failure for a
  single quote and a multi-row request. `score_graph_lazy` releases its owned execution context
  when plan construction fails before a `DeployScorePlan` can be returned.
- **Pyfunc conversion is inside the admitted lifetime.** `HauteModel.predict()` admits before
  Pandas→Polars conversion and retains that same reservation through scoring and the final
  Polars→Pandas conversion. Both copies run in execution stages, and every failure path releases
  the admission exactly once (including a failure before `score_graph` is entered). The scorer's
  opt-in retain-on-success mode still cleans its materialised-plan resources; the caller owns the
  final admission release and failures inside scoring continue to release immediately.
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
- **Deploy scoring never performs persistence writes.** `dataOutput` is a
  pass-through in the served graph, its configured writer is never invoked, and
  persistence-only branches outside the output ancestry are removed by pruning.
- **Snapshot leases are process-local.** `ResolvedDeploy` holds the selected generation's
  `SourceCacheStore.lease()` through shipment, which prevents same-process refresh,
  clear, and eviction from deleting it. The source-cache layer does not yet coordinate
  leases or retirement across OS processes, so a refresh from another process remains a
  known limitation rather than a guarantee made by deploy.

## Error handling

| Exception | Raised where | Propagates to |
|---|---|---|
| `haute.errors.DeployError` | `src/haute/deploy/_config.py` (unpinned base image, escaped pipeline, unsupported live source, invalid output projection), `_bundler.py` (escaped/malformed artefact path, invalid or unreadable retained Data Input), `_scorer.py` (unscoreable `modelScore`), `_validators.py` (aggregated validation failure), `_container.py` / `_mlflow.py` (expected target operational failure) | `deploy()` / `resolve_config()` callers; carries structured `context` kwargs (for example `node_id`, `field`, and the underlying provider/schema `error`) rendered into `str()`. The CLI formats this expected domain family concisely. |
| `ValueError` | `_pruner.py` (missing/multiple output nodes or a configured `liveSwitch` input not connected to the graph), `src/haute/deploy/_config.py` (zero/ambiguous fallback source nodes, unknown TOML keys, missing `from_cli_args` required fields), `src/haute/deploy/__init__.py` (unknown target), `_scorer.py` (bad `output_fields` type, negative `row_count`), `src/haute/deploy/_impact.py` (non-finite predictions, zero-baseline change) | Caller of `resolve_config`/`deploy`/scoring functions; container `/quote` endpoint catches the `BoundedMemoryUnsupportedError` subclass specially (422) but a bare `ValueError` from scoring falls into the generic 500 handler. |
| `FileNotFoundError` | `_bundler.py::_check_exists` (missing artefact on disk), `_bundler.py::_download_model_artifact` (MLflow download landed but file missing) | Propagates uncaught through `resolve_config()`. |
| `RuntimeError` | `_scorer.py` (`modelScore` contract matched but no model artefact — deliberately after the contract check), or an unexpected backend implementation defect | Uncaught to caller with its original type/traceback. Expected Docker, dependency-pinning, credential, connectivity, and backend-response failures use `DeployError` instead. |
| `FeatureMismatchError` | `_scorer.py::_assert_runtime_contract_matches` (live schema disagrees with bundled training contract on feature set, dtype, or categorical levels) | Uncaught through scoring; surfaces in the container's generic 500 handler or the MLflow `pyfunc` boundary. |
| `RequestBodyLimitError` / `RequestBodyHeaderError` / `RequestBodyParseError` | `_request_limits.py::read_limited_json_body` | Caught explicitly in the generated container `app.py`'s `/quote` handler → HTTP 413 / 400 / 422 with a structured `to_payload()` body. |
| `ExecutionAdmissionError` / `ExecutionMemoryLimitExceededError` | Raised by the execution-engine's admission layer, invoked via `admit_deploy_execution` | Caught in `/quote` → HTTP 507. |
| `ExecutionCancelledError` | Execution engine | Caught in `/quote` → HTTP 499 with `job_id`/`operation` context. |
| Public `HauteError` (`ContractResolutionError`, `PreambleError`, and other errors with a stable `error_code`) | Execution engine or preamble compilation during scoring | Caught in `/quote` → HTTP 422 with `to_payload()`; server routes use the same stable public payload contract. |
| `NotImplementedError` | `src/haute/deploy/__init__.py::_validate_target` (planned targets: `sagemaker`, `azure-ml`), `_container.py::_update_service` (platform-container service update not yet built) | Uncaught to caller; the `_update_service` message names the built image tag (which is pushed only when a registry was configured). |
| `DeployError` (capped schema dry-run) | `_schema.py::infer_output_schema` when the fallback's worker fails: a `BatchScoreError` from the child, or any `IsolatedWorker*Error` (`IsolatedWorkerMemoryLimitUnsupportedError` on a host without native caps included) | Propagates from `resolve_config()`; names the blocking node/operator from the original group-by rejection and states that the served batch path could not be proven. |
| `RuntimeError` (startup) | Generated `app.py`'s `_require_fail_closed_batch_enforcement` at module load: a `warned` execution policy with `HAUTE_WORKER_MEMORY_ENFORCEMENT` other than `required` | Uncaught — the service refuses to start rather than failing every batch request. |
| `IsolatedWorkerMemoryLimitUnsupportedError` | `run_isolated_worker` under `required` enforcement on a host that cannot install a native cap | Caught in `_quote_batch` → HTTP 507 with `reason: "native_memory_cap_unavailable"`. |
| `BatchScoreCleanupError` | `_batch_scoring.py::_remove_batch_temp_dir` (temp directory removal failed with no primary error in flight) | Caught in `_quote_batch`'s `finally`, logged `deploy_quote_batch_cleanup_failed`, replaces the computed response with HTTP 500 `deploy_internal_error`. With a primary error in flight it is attached to that error as a note instead. |
| `BatchScoreError` | `_batch_scoring.py::accept_batch_outcome` (classified child failure, wrong outcome type, missing/unreadable/row-count-mismatched result parquet) | Caught in `_quote_batch` → 422 (`contract`/`bounded`), 507 (`memory`), 499 (`cancelled`), 500 (`error`). |
| `IsolatedWorkerMemoryLimitExceededError` / `IsolatedWorkerMemoryLimitUnsupportedError` / memory-bound `IsolatedWorkerCrashedError` / memory-typed `IsolatedWorkerRemoteError` | `_worker_isolation.run_isolated_worker` supervising the batch child | Caught in `_quote_batch` → HTTP 507 with `isolated_worker_memory_detail(...)`. |
| `IsolatedWorkerTimeoutError` | Batch worker exceeded `deploy_batch_timeout_seconds()` | Caught in `_quote_batch` → HTTP 504 `deploy_batch_timeout`. |
| Any other `IsolatedWorkerError` | Batch worker supervision | Caught in `_quote_batch`, logged `deploy_quote_batch_failed`, HTTP 500 `deploy_internal_error`. |
| Any other `Exception` | Runtime scoring inside `/quote` (live path) or parent-side batch supervision in `_quote_batch` | Caught by the container's catch-all, logged via `logger.exception("deploy_quote_failed")` (live) or `logger.exception("deploy_quote_batch_failed")` (batch, after `BatchScorePlan.cleanup`), returned as HTTP 500 with `error_code: "deploy_internal_error"`. The MLflow `pyfunc` predict path has no equivalent catch-all. |

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
- **`test_deploy_batch_scoring.py`** — the multi-row path end to end: a one-row request
  launching no worker; a two-row request launching exactly one
  `haute-deploy-batch` worker with the budget's memory limit and
  `deploy_batch_timeout_seconds()`; the unchanged JSON envelope carrying the child's
  metrics and the NDJSON stream over every sunk row; every `failure_kind`, every worker
  memory/timeout/crash mapping, and every unpublishable success outcome — each asserting
  one parent admission release and a removed temp directory; the child in process
  (parquet written, row count, `admission.profile == "deploy_batch"`, result file removed
  and child admission released on each failure kind); two real spawns (a scored batch,
  and an unprovable group-by completing as `warned` /
  `full-width-conservative` under the worker's cap); and
  `infer_deploy_execution_policy` (ok record, translated warning for the capped worker,
  `DeployError` for the in-process runtime and for other rejections, one bundle-time
  release) plus the manifest and `/health` fields. `TestOutputSchemaConservativeFallback`
  covers the capped-worker fallback on a cache miss (a real spawn admitted as
  `DEPLOY_BATCH`, the schema read from the worker's parquet) and proves a provable graph still scores
  its dry-run row; `TestBatchCleanupFailsLoud` injects an `rmtree` failure for a
  successful batch, a handled child failure, a setup failure, and a direct
  `plan.cleanup()`; `TestFailClosedBatchEnforcement` covers the startup gate (warned
  policy refused under `best_effort`, accepted under `required`, provable policy always
  accepted) and the typed 507 `native_memory_cap_unavailable` a cap-less `required` host
  returns. `TestOutputSchemaConservativeFallback` covers the capped-worker schema
  fallback: a real spawn (nothing patched across the boundary) asserting exactly one
  `haute-deploy-batch` worker ran and the aggregated column's dtype came back, a provable
  graph spawning nothing, and both failure mappings (`IsolatedWorkerMemoryLimitUnsupportedError`
  and a child `BatchScoreError`) surfacing as `DeployError`. `tests/test_deploy_internals.py` adds the two `resolve_config`
  cache-miss regressions (container target bundles the warning, Databricks refuses).
- **`test_deploy_contract_integrity.py`** — static-data-source schema drift detection,
  `validate_deploy` failing on bad test quotes, feature-contract bundling end-to-end.
- **`test_deploy_dispatch.py`** — `_dispatch_resolved` routing for every target family
  (Databricks, container, platform-container, planned/`NotImplementedError`,
  unknown/`ValueError`) and `DeployResult` field population.
- **`test_deploy_expected_output_validation.py`** — canonical test-quote envelope parsing,
  tolerance comparison (numeric, boolean, Decimal,
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
  checks, and (also in this file) the full `src/haute/deploy/_impact.py` surface — batched scoring,
  `ColumnStats`/`SegmentRow`/report building, terminal/Markdown formatting — plus the
  `TestBugB4PrunerUsesOriginalEdges` and `TestBugB10LexicographicVersionComparison`
  regression classes.
- **`test_impact.py`** — dedicated impact arithmetic, prediction-envelope
  normalization, row-alignment/shortfall accounting, segment selection, and
  terminal/Markdown report formatting.
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

## Canonical-only scoring inputs

Under the [canonical-only format policy](../README.md#canonical-only-format-policy),
generated deployment pruning and scoring bind inputs exclusively through the current named input
handle contract. There is no positional-first-input or bare-frame fallback. Deployment tests use
the same canonical handles produced by current graph/code generation.
