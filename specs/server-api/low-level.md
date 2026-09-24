# Server API — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_editor_identities.py` | Pure derivation of node function names, default/handle input identities, and config references used by editor documents and the bounded prospective-identity endpoint. |
| `src/haute/server.py` | App factory (`app = FastAPI(...)`), lifespan (bytecode clear, logging config, env load, marked optimiser-artifact reaping, pipeline-index priming, watcher task lifecycle), middleware registration, router inclusion, `/api/session` health/bootstrap routes, bounded request-ID selection, the API/WS 404 guard, the `/ws/sync` WebSocket endpoint, the debounced file watcher, and credential-free static SPA serving. |
| `src/haute/_local_security.py` | [sandbox-security](../sandbox-security/low-level.md)-owned per-process local-session token, trusted-Origin/Host parsing (including bracketed IPv6), `LocalSessionMiddleware`, `LocalTrustedHostMiddleware`, and the HTTP/WebSocket token-validation contract. |
| `src/haute/schemas.py` | Stable shared Pydantic request/response import surface used across the app — re-exports canonical graph types from `_types.py`, re-exports execution-strategy DTOs from `_execution_schemas.py`, and defines the remaining per-feature model groups, including the structurally separate pipeline editor-recovery document and diagnostics. The OUTPUT dry-run models are the deliberate route-local exception. |
| `src/haute/errors.py` | The `HauteError` hierarchy, including execution, bounded-memory, schema/contract, deployment, and feature errors. Route-visible public subclasses carry stable `error_code` and `public_fields` metadata consumed by `_contract_errors.py`. |
| `src/haute/_validation_error.py` | `HauteValidationError` — the ValueError-derived marker for haute-authored validation messages (re-exported by `errors.py`); the modelling worker boundary keys its curated-message promotion on it. |
| `src/haute/_logging.py` | `configure_logging()` (structlog + stdlib bridge, dev-console vs. JSON-lines modes) and `get_logger()`. |
| `src/haute/_event_bus.py` | `EventBus` — thread-safe synchronous pub/sub with typed `parse.error` and `pipeline.document.update` overloads; `default_bus` is the module-level singleton the watcher and server wire together. |
| `src/haute/_types.py` | `NodeType` (`StrEnum`), the decorator↔NodeType maps, every per-node-type config `TypedDict`, the `SolveResultLike` Protocol family, and the canonical `NodeData` / `GraphNode` / `GraphEdge` / `PipelineGraph` Pydantic models (with `PipelineGraph`'s cached-property-invalidating `model_copy` override). |
| `src/haute/_pipeline_revision.py` | [submodels](../submodels/low-level.md)-owned canonical parsed-graph revision plus the editor recovery revision over a contained, role-qualified raw-artifact manifest with explicit missing sentinels. |
| `src/haute/_pipeline_recovery.py` | Side-effect-free editor loader: AST skeleton discovery (a syntax-invalid file becomes a source-only document carrying the syntax error), isolated node resolution, availability/diagnostic propagation, typed sidecar merge, raw-artifact revision assembly, and ready/degraded/source-only classification, plus `pipeline_document_fingerprint`, the one digest of a dumped editor document shared by the load routes, resync, and live-sync frames. It never returns a canonical `PipelineGraph`. |
| `src/haute/_pipeline_repair.py` | Unavailable-node removal planner and shared revision/plan verification, conservation and rollback service for explicit removal, current-format update and reset. It never accepts client-authored bytes. |
| `src/haute/_pipeline_repair_actions.py` | Bounded submodel-format update and ordinary-node reset planners; single-node codegen, shared palette defaults, isolated artifact-only preview and strict postconditions. Node-scoped saves and resets write a sidecar for every node that emits one (`node_emits_sidecar`), so a stepped transform's optional polars sidecar is updated alongside its regenerated body. |
| `src/haute/_submodel_recovery.py` | Literal submodel registration identity evidence used only by recovery, raw revision discovery and explicitly requested updates. |
| `src/haute/_sidecar.py` | Core read-side `.haute.json` contract: `SidecarModel`, the typed absent/valid/corrupt/unreadable read state, and the sidecar source/position normalisers. Lives outside the web layer so editor recovery never imports routes. |
| `src/haute/routes/__init__.py` | Package docstring only — no code. |
| `src/haute/routes/_helpers.py` | Re-exports the core sidecar read contract and `load_pipeline_editor_document` for route consumers; path/index/watcher/WebSocket helpers; strict `parse_pipeline_to_graph`; the sidecar write path (`save_sidecar`); historical-commit parsing; and the shared `save_lock`. |
| `src/haute/routes/pipeline.py` | `/api/pipelines`, `/api/pipeline`, `/api/pipeline/{name}`, `/api/pipeline/editor-identities`, `/api/pipeline/polars-steps/render`, `/api/pipeline/save`, `/api/pipeline/repair/remove/dry-run`, `/api/pipeline/repair/remove/apply`, `/api/pipeline/read-json`, `/api/pipeline/trace`, `/api/pipeline/preview`, `/api/pipeline/preview/inputs`, `/api/pipeline/recovery-preview`, `/api/pipeline/write-output`, `/api/pipeline/output-destination` — plus the supersession-key builders, shared output-request preparation, `_prepare_runtime_graph` request containment, runtime-input/output path validators, and memory-limit-to-HTTP-exception translators shared across graph-executing route families. |
| `src/haute/routes/files.py` | `/api/files` (directory browse) and `/api/schema` (flat-file plus XML structured-record schema/preview). |
| `src/haute/routes/io_capabilities.py` | `/api/io-capabilities`, the versioned provider/format/cache capability contract consumed by the input and output editors. |
| `src/haute/routes/input_cache.py` | `/api/input-cache/*`, the shared build/status/cancel/clear lifecycle for snapshot-backed inputs. |
| `src/haute/routes/node_data.py` | `/api/node-data/point`, `/run`, `/status/{job_id}`, `/cancel/{job_id}`, and `/clear` for the data a consumer node reads. |
| `src/haute/routes/cache.py` | `POST /api/cache/nodes`, the per-node inventory of stored datasets plus owners outside the open graph; `POST /api/cache/clear`, which clears the identities a report's row names; and `GET /api/cache/usage`, the store's size for the preview status bar. The inventory answers explicit requests rather than polling. |
| `src/haute/routes/banding.py` | FastAPI router (`/api/banding`): whole-dataset statistics for the banding factor being edited, delegating to `_banding_stats.py`. |
| `src/haute/routes/rating.py` | FastAPI router (`/api/rating`): whole-dataset levels for the raw factor columns a Rating Step rates on, delegating to `_rating_levels.py`. |
| `src/haute/routes/_rating_levels.py` | Reads those levels over the node's shared data point under `run_synchronous_analysis`, keyed by the rating lookup's own key expression. |
| `src/haute/routes/_node_data_service.py` | `NodeDataService`: consumer point responses, delegation, explicit node-output build jobs in isolated workers, the data-profile job, supersession, cancellation, and clear. `points_for_graph` resolves every node of a graph against one resolver for the per-node cache report, returning each node's input-snapshot identity digest alongside its point. |
| `src/haute/routes/_synchronous_analysis.py` | Request-time analyses of a leased point: admitted execution, memoisation by data version, and client-disconnect cancellation. |
| `src/haute/routes/utility.py` | `/api/utility` CRUD (list/read/create/update/delete) for `utility/*.py` helper modules, with AST syntax validation on every write. |
| `src/haute/routes/_save_pipeline.py` | `SavePipelineService` — the transactional save orchestrator: singleton/name-collision/load-error validation, codegen invocation, config-file + sidecar writes, stale-config cleanup, and rollback. |
| `src/haute/routes/_supersession.py` | `SupersessionCoordinator` / `_SupersessionState` — generation-counted "run latest, cancel/skip the rest" concurrency primitive used by preview and trace. |
| `src/haute/routes/output_assemble.py` | `POST /api/output-assemble/dry-run` — validates an unsaved `outputMapping`, swaps it into the target node's in-memory config, executes up to that node, returns the rendered document. |
| `src/haute/routes/_contract_errors.py` | Shared public-contract-error adapter: validates the closed public error set, emits stable payloads, maps synchronous failures to HTTP 422, and supplies the matching contract-error fields for background jobs. Also owns `memory_limit_http_exception`, the one memory-limit → 507 mapping; a job-backed surface passes its operation noun so the detail also carries the curated message. |
| `src/haute/routes/_error_handlers.py` | The application exception handlers, installed by `install_exception_handlers(app)`: public contract errors → `contract_error_http_exception`, `ExecutionAdmissionError` / `ExecutionMemoryLimitExceededError` → `memory_limit_http_exception`, `GitError` → `git_error_http_exception`, `PathOutsideProjectError` / `InvalidPathError` → 403 / 400 with the bare message. A handler reached from a WebSocket re-raises, because an HTTP response cannot answer it. |
| `src/haute/routes/_runtime_path_errors.py` | Closed HTTP mapping for runtime-path failures: malformed path → 400, project-root escape → 403, selected by concrete exception type rather than message text. |
| `src/haute/_node_config_recovery.py` | Current contracts and field reconciliation. |
| `src/haute/_artifact_paths.py` | Contained project-relative artifact paths (traversal/alias/reparse-point rejection) and bounded artifact reads shared by recovery and the mutation lock. |
| `src/haute/_recovery_sources.py` | Raw authored settings/code evidence and scaffold matching for the recover action. For a stepped node type it reconciles the recovered `steps` against the extracted body through the parser's own `_reconcile_steps`, so a hand-edited body is never regenerated from stale steps when the candidate materialises; a discarded list is reported as a `/steps` `removed` change. A step list the parser rejects outright (a malformed container, or a list beside an `inputMapping` an edges surface refuses) is dropped with the same change record rather than propagated, because recovery never raises on a bad field and the route would otherwise answer HTTP 500; the key is removed rather than defaulted to `[]`, which would materialise as empty code and discard the authored body. |
| `src/haute/_recovery_schemas.py` | Engine field-outcome and issue types. |
| `src/haute/_project_mutation_lock.py` | Cross-process project writer lock: an `asyncio.Lock` plus a `FileLock` of its own, polled without blocking so cancellation never strands it. |
| `src/haute/_file_lock.py` | The one cross-process lock helper (`FileLock`) and the OS file-lock primitives beneath it. |
| `src/haute/node_defaults.json` | Shared palette/reset defaults. |

## Key types and data structures

- `SavePipelineService.validate_graph(graph, source_file)` is public and
  side-effect free. It validates Edge Join connected roles, target handles,
  and mutually exclusive/required key forms through the canonical
  `_edge_join.py` validators as well as the other save invariants. `save()`
  calls it before staging, and dry-run calls the same method.
- `AssistantMessageRequest` is a strict request containing only `session_id`
  and `message`; graph-authoring confirmation payloads are rejected as unknown
  fields.
- Assistant plan and application responses carry `base_revision`,
  `result_revision`, `capability_hash`, `plan_hash`, semantic diff,
  verification tier/evidence, warnings and ledger reference as applicable.
  Unknown fields remain rejected at typed HTTP boundaries.
- `ExecutionMetricsPayload` (`_execution_schemas.py`, re-exported by `schemas.py`) carries
  the execution metrics payload across routes and worker processes, including scalar
  single-file training write evidence: `training_write_strategy`, `training_write_input_slices`
  (`ge=1`), `training_write_native_reason`, and `training_write_blocking_operator`; and the same
  for a Data Output's own file, `data_output_write_strategy`, `data_output_write_input_slices`
  (`ge=1`) and `data_output_write_native_reason`. The two are named apart rather than shared
  because this payload is carried by every operation, and a preview should not report four null
  training fields nor a training run three null output ones. Each set's native reason is recorded
  only where the write did come back native: a reason for a strategy that did not happen would be
  a false statement in the evidence. A Data Output that writes eagerly or to a database records no
  strategy at all, because the chunked writer is not on that path.

- A failing build's `worker_evidence` is adopted before its error propagates, and a
  failed job retains its execution metrics. Cache-specific quota refusals no longer exist.

**Exception hierarchy** (`errors.py`, abridged to route-relevant branches) — every subclass
roots at `HauteError`, which renders
`**context` kwargs into `str(err)` (`"message (k=v, k2=v2)"`) so structured fields reach log
lines without manual formatting:
```
HauteError
├── ConfigError
│   └── NodeConfigError (also a HauteValidationError)
├── ParseError
├── ExecutionError
│   ├── PreambleError
│   ├── ContractResolutionError
│   ├── InputPreparationError
│   ├── RatingExtremaUndefinedError
│   ├── LiveSwitchScenarioError
│   ├── TraceCorrelationUnsupportedError
│   └── BoundedMemoryUnsupportedError
│       ├── ChunkPlanUnsupportedError
│       ├── ChunkMemoryRiskError
│       └── GroupByExecutionUnsupportedError
├── DeployError
├── FeatureMismatchError
├── SchemaMismatchError
│   ├── RatingFactorMissingError
│   └── RatingFactorDtypeContractError
└── ContractMismatchError
```
`_api_input_schema.ApiInputSchemaError` and `_output_assembler.OutputMappingSchemaError`
(with `OutputNestingKeyError`) are direct `HauteError` subclasses supplied by
JSON-shredding and consumed by this component's routes, not defined in `errors.py`. Not
every Haute exception is a `HauteError`: resource-exhaustion and deadline errors used
elsewhere in the codebase deliberately extend `MemoryError` / `TimeoutError` /
`FileNotFoundError` instead, so a single `except HauteError` does not catch the whole error
surface (see `errors.py`'s module docstring).

**`NodeType`** (`_types.py`) is a `StrEnum` — `NodeType.API_INPUT == "apiInput"` is `True`,
and it serialises to the plain string for the React Flow frontend. `DECORATOR_TO_NODE_TYPE`
maps pipeline-decorator names (`"data_source"`, `"polars"`, …) to `NodeType`;
`NODE_TYPE_TO_DECORATOR` is its inverse (excluding the `"instance"` alias, which has no
canonical decorator name).

**`PipelineGraph`** (`_types.py`) is the canonical graph type shared by the parser, executor,
codegen, deploy, and this component's `schemas.py` (re-exported as `Graph`). It carries three
`@cached_property` slots (`node_map`, `parents_of`, `_haute_base_fingerprint`) and overrides
`model_copy` to evict all three on every copy — Pydantic's default `model_copy` shallow-copies
`__dict__`, which would otherwise leak a stale `node_map` onto a structurally-changed copy.
`GraphEdge.sourceHandle`/`targetHandle` reject an empty string in a `field_validator`
(`""` is not silently coerced to `None` — a port legitimately named `""` is a different,
separately-invalid case).
`source_revision: str | None` is live API metadata rather than executable
graph state. `parse_pipeline_to_graph` computes it from a versioned canonical
graph/file manifest, and the revision algorithm excludes that field from its
own input. Mutation preconditions and committed response revisions use a
non-empty, whitespace-free `RevisionToken`.
Every persisted-document mutation names the revision it was based on:
submodel create/dissolve send `base_revision`, recovery preview sends
`source_revision`, and `POST /api/pipeline/save` sends `base_revision` (`null`
only when the client believes the target file does not exist). The save
service compares it with the on-disk document's `source_revision` before any
write and rejects a mismatch with a `409` whose flat `detail` string begins
with the stable code `stale_document_revision:` (the expected and provided
revisions go to the server log, keeping the standard error shape); there is
no unconditional overwrite and no server-side retry.

**Pipeline editor recovery models** (`schemas.py`) all use `extra="forbid"` and schema
version 1. `PipelineEditorDocument` carries `document_kind`, `schema_version`,
`load_status`, metadata/source text, `RecoveryPipelineNode[]`, `RecoveryPipelineEdge[]`,
structured diagnostics, capabilities, source-selection trust, submodels, and a raw-artifact
revision. `RecoveryPipelineNode` uses `recovery_id`, `authored_id`, `decorator_name`,
`node_type`, `display_position`, `availability`, optional validated `config`, server-owned
`function_name`, nullable `default_input_name`, exact `source_handle_input_names`, source/config
locations, diagnostic ids, blocker path, and the server-derived `scoped_editable`
eligibility flag; it intentionally has no canonical
`id/type/position/data` tuple. Recovery edges likewise use recovery endpoint identities, not
canonical `source`/`target`, and carry nullable `input_name` (non-null for ready executable
edges). Submodel definitions exactly map every public input port to its executable identity.
`PipelineRecoveryDiagnostic` supplies a stable id/code,
severity, scope, safe message, optional element identity and source span, remediation, and
incident id. `PipelineEditorDocument` also carries a bounded `completeness` list —
field-level required-value gaps for loadable Data Input/Output nodes, recomputed by the
document loader from the strict validators' completeness mode; entries never affect
availability, capabilities, or `diagnostics`. `PipelineDocumentCapabilities` is the server-derived mutation/persistence/
execution/preview/submodel/repair fence and carries a sorted unique
`reserved_api_input_frame_labels` list. The response types do not subclass or relax
`PipelineGraph`.

**Prospective editor identity models** (`schemas.py`) use `extra="forbid"`.
`EditorIdentitiesRequest` accepts at most 10,000 unique nodes; each has bounded
`node_id`, `label`, canonical `node_type`, nullable submodel alias, and at most
1,024 unique bounded source handles. Validators enforce node-type-specific alias
and handle rules; API-input handles must already be non-keyword ASCII identifiers,
while submodel outputs use `out__<port_id>`. `EditorIdentitiesResponse` returns one exact-order identity per
request node with non-empty function/default/handle identities and an optional
config reference. Resolution is pure and performs no project I/O.

**`SidecarModel`** (`haute/_sidecar.py`, re-exported by `routes/_helpers.py`) is the typed `.haute.json` schema: `positions:
dict[str, dict[str, float]]`, `sources: list[str]` (defaults to `["live"]`), `active_source:
str`, and optional `managed_parent: str | None`. `managed_parent` is emitted
only when the existing child sidecar already proves the same canonical
project-relative owner, or when explicit Save derives a new definition from
the persisted/submitted registry diff after source-and-sidecar no-clobber. A
graph request exposes no ownership metadata; only that derived disk state can
establish ownership. A
`model_validator(mode="after")` enforces `active_source in sources`. Written via
`model_dump_json(exclude_defaults=True)` so a pipeline that never touched multi-source state
produces a sidecar with only `positions`.

**`EventBus`** (`_event_bus.py`) keys handlers by event-type string in a
`dict[str, list[HandlerType]]` guarded by an `RLock` (reentrant so a handler that
republishes doesn't deadlock). `subscribe()` returns a zero-arg unsubscribe closure;
`publish()` snapshots the handler list under the lock, then calls each handler *outside* the
lock, catching and logging any exception per-handler so one misbehaving subscriber can't
silence the rest. `PipelineDocumentUpdatePayload` is a closed required-key contract
containing `document`, `document_fingerprint`, and `source_file`; `ParseErrorPayload`
contains `error` and `source_file`. They are the two currently-declared typed events;
`default_bus` is the module-level singleton `server.py`'s watcher and WebSocket
translator share.

**`SupersessionCoordinator._SupersessionState`** (`routes/_supersession.py`) is one
`asyncio.Condition` + `latest_generation: int` + `active: bool` + `references: int` +
`active_cancel: Callable[[], None] | None` per distinct request key. `run_latest()` increments
`latest_generation` on entry, invokes `active_cancel` on whatever is currently running for
that key, waits for the active slot to free, re-checks its own generation is still the
latest (otherwise raises `SupersededRequestError` without ever running the worker), then runs
the worker exclusively for that key.

**`SavePipelineService._TouchedFile`** (`routes/_save_pipeline.py`) is a `NamedTuple` of
`(target: Path, previous_bytes: bytes | None)` — `None` means the file did not exist before
this save (rollback deletes it); otherwise rollback restores the snapshotted bytes.

**HTTP endpoint contracts owned here** (FastAPI adds its standard 422 validation envelope
when a path/query/body fails model validation):

| Method and path | Input contract | Success contract |
|---|---|---|
| `GET /api/session` | No body | `SessionStatusResponse {ok: bool=true}` |
| `POST /api/session/bootstrap` | No body; explicit exact local Origin required | `SessionStatusResponse {ok: bool=true}` plus HttpOnly, SameSite=Strict session cookie and no-store headers |
| `GET /api/pipelines` | No body | `list[PipelineSummary]`; each item carries `{name, description, file, node_count, load_status, diagnostic_count}`. |
| `GET /api/pipeline` | No body | First discovered authored `PipelineEditorDocument`, irrespective of load status; a new empty ready document only when no authored document exists. Readable authored errors remain HTTP 200. The `x-haute-document-fingerprint` header carries `pipeline_document_fingerprint` of the returned document. |
| `GET /api/pipeline/{name}` | Pipeline name path parameter | Named `PipelineEditorDocument`; a readable non-ready document is found by recovered metadata or file stem and remains HTTP 200. Carries the same `x-haute-document-fingerprint` header. |
| `POST /api/pipeline/editor-identities` | `EditorIdentitiesRequest {nodes:[{node_id,label,node_type,source_handles}]}` | `EditorIdentitiesResponse {identities:[{node_id,function_name,config_reference,default_input_name,source_handle_input_names}]}` in exact request order; public handles are sanitised server-side and the operation has no project-state side effects. |
| `POST /api/pipeline/save` | `SavePipelineRequest {name="main", description="", graph={}, preamble=null, preserved_blocks=[], source_file="", sources=["live"], active_source="live", base_revision}`; `base_revision` is a required `RevisionToken | null` | `SavePipelineResponse {status="saved", file, pipeline_name, source_revision, warnings=[], git_sha=null, identity_required=false}`, or `409` with a flat `detail` beginning `stale_document_revision:` when `base_revision` does not equal the on-disk `source_revision` (`null` versus an existing file, or a token versus a missing file, are mismatches) |
| `POST /api/pipeline/read-json` | `ReadJsonRequest {path}` | `ReadJsonResponse`, a root JSON object (arrays/scalars are rejected) |
| `POST /api/pipeline/polars-steps/render` | `PolarsStepsRenderRequest {steps:[object], input_names:[str], start:"input"|"frame"}` (`start` is required: `input` renders a transform's list, `frame` a surface whose `df` is already bound) | `PolarsStepsRenderResponse {ok, code, step_lines:[[start,end]], step_index, message}`; a step validation failure is `ok: false` with HTTP 200, an empty frame-mode list is `ok: true` with empty code, and the call touches no project state |
| `POST /api/pipeline/preview` | `PreviewNodeRequest {graph, node_id, row_limit=100 (1..10000), source="live", requested_preview_columns=null (non-empty when present), port_label=null}`; `node_id` is the visible id for a root node and the occurrence-qualified runtime id for a drilled child | `PreviewNodeResponse`, extending `NodeResult` with `node_id`, timings/memory, per-node schemas/statuses, optional execution metrics, and `seed_plan`: one `PreviewSeedPlanEntry {node_id, port_label=null, node_label, identity_digest, generation_id, columns (null = all), created_at (ISO-8601 UTC), kind: seeded|captured}` per shared-snapshot generation the rows were computed from, in topological order. `seeded` means the plan leased that generation and the response was computed from it; `captured` means this request computed the node and published it. A response served from the preview response cache reports every generation it lists as `seeded`, because the hit leases and verifies each one before serving it and publishes none of them. A partial hit, which extends a cached entry by executing the nodes it lacks, reports the kinds of the plan it ran under. The route executes with `shared_snapshots=True`; in process mode it passes the worker a staging token and discards any capture staging left under it once the worker returns, fails, times out, or is superseded. |
| `POST /api/pipeline/preview/inputs` | `PreviewInputsRequest {graph, node_id, source="live", requested_preview_columns=null (non-empty when present), port_label=null}` | `PreviewInputsResponse {input_node_ids}` — `preview_input_node_ids`: the snapshot-backed Data Inputs and structured API Inputs the preview's first resolution executes, read without preparing or leasing; for a lineage that is not admitted, every one the target reads. Advisory: the preview prepares whatever its own plan then reads. A graph the preview cannot run as authored (a shape, config, or contract error, flattening included) answers an empty list, and the preview reports that error at the node. |
| `POST /api/pipeline/trace` | `TraceRequest {graph, row_index=0 (>=0), target_node_id=null, column=null, row_limit=100 (1..10000), source="live", row_values=null, seed_plan}`; a non-null `target_node_id` is the visible id for a root node and the occurrence-qualified runtime id for a drilled child. `seed_plan` is required and may be empty: the explained preview's entries as `TraceSeedPlanEntry {node_id, port_label=null, identity_digest, generation_id}`; it joins the supersession key, and an expired plan answers 409 `preview_seed_plan_expired` in thread and process mode alike | Explicit JSON `TraceResponse {status, trace}`. `trace` includes successful steps, typed omissions, correlation/waterfall evidence, UTC `generated_at`, source identity, and `execution_origin: fresh_execution|trace_cache`; the payload is serialized and `TraceResponse`-validated in the worker, then the returned `JSONResponse` skips a second event-loop validation pass |
| `POST /api/pipeline/write-output` | `WriteOutputRequest {graph, node_id, source="live", overwrite=false}` | `WriteOutputResponse` with status, row count, destination path/table, format, publication outcome, and execution metrics |
| `GET /api/execution-settings` | No body | `ExecutionSettings {streaming_chunk_size}`: the editor's current streaming chunk size |
| `PUT /api/execution-settings` | `ExecutionSettings {streaming_chunk_size (1..10000000, bool rejected)}` | The applied `ExecutionSettings`. The value is set for the server process at once, so server-thread executions and workers started afterwards, and the next task on each warm interactive worker, run with it |
| `POST /api/pipeline/output-destination` | `OutputDestinationRequest {graph, node_id}` | Safe destination display path, format, and suffix-mismatch flag; performs no graph execution or filesystem write |
| `GET /api/files` | Query `dir="."`, `extensions=null`; omission derives readable extensions from the I/O registry | `BrowseFilesResponse {dir, items:[{name,path,type,size?}]}`; files have numeric byte size, directories serialize `size: null` |
| `GET /api/io-capabilities` | No body | Versioned provider groups, format capabilities, modes, accepted arguments, optional engines, cache modes, and materialisation diagnostics |
| `GET /api/schema` | Required query `path`; XML uses the structured API-input decoder | `SchemaResponse {path, columns, row_count?, row_count_estimated=false, column_count, preview=[]}`; invalid/unsafe XML is 400 |
| `POST /api/input-cache/build` | Canonical `dataInput` config and source identity (no build profile: the server chooses) | Starts or coalesces a cache-generation build and returns its job identity and `build_class` |
| `POST /api/input-cache/status` | Canonical `dataInput` config | Current published-generation readiness, freshness, metadata, and active job |
| `POST /api/input-cache/clear` | Canonical `dataInput` config | Clears published cache generations when no active lease prevents deletion |
| `GET /api/input-cache/jobs/{job_id}` / `DELETE /api/input-cache/jobs/{job_id}` | Job id | Polls or requests cancellation of a cache build |
| `GET /api/utility` | No body | `UtilityListResponse {files:[{name,module}]}` |
| `GET /api/utility/{module}` | Python-identifier module path | `UtilityReadResponse {name,module,content}` |
| `POST /api/utility` | `UtilityCreateRequest {name, content=""}` | `UtilityWriteResponse {status="ok", name, module, import_line, error=null, error_line=null}` |
| `PUT /api/utility/{module}` | `UtilityWriteRequest {content}` | `UtilityWriteResponse` |
| `DELETE /api/utility/{module}` | Module path | `UtilityDeleteResponse {status="ok", module}` |
| `POST /api/output-assemble/dry-run` | Route-local `OutputAssembleDryRunRequest {graph, node_id, output_mapping=[], output_format="json", row_limit=100 (1..10000), source="live"}` | Route-local `OutputAssembleDryRunResponse {status, document=[], row_count=0, error=null}` |

`TraceResultResponse` requires `omissions`, `correlation_diagnostics`, `generated_at`, and
`execution_origin`; these are not compatibility defaults. Each successful step requires a
non-negative `topological_rank` and carries no per-step timing. A successful waterfall entry is
the typed `{label, operation, value, delta, cumulative, default_used}` shape, while a failed
waterfall is the typed `{error, error_type}` shape. This keeps omission links, default evidence,
and reconciliation failures enforceable at the HTTP boundary rather than accepting arbitrary
trace dictionaries.

**WebSocket contract.** `GET /ws/sync` upgrades only after an explicit Origin exactly matches
the loopback Host authority and the HttpOnly session cookie validates. No header or query
token transport exists; the HttpOnly session cookie validates WebSocket and HTTP requests.
The client sends `{"type":"resync","source_file":str,"document_schema_version":1,
"document_fingerprint"?:sha256}`; a resync whose `document_schema_version` is not the
current literal `1` receives a `parse_error` frame naming the unsupported version, and
plain text, malformed JSON, non-object JSON, and unknown message types are keep-alive
no-ops. The server sends exactly two frame types: a complete
`{"type":"pipeline_document_update","schema_version":1,"document":object,
"document_fingerprint":sha256,"source_file":str}` or
`{"type":"parse_error","error":str,"source_file":str}`.
A matching document fingerprint produces no frame. Every fingerprint — in these frames, in a
resync comparison, and in the load routes' `x-haute-document-fingerprint` header — is
`pipeline_document_fingerprint`: SHA-256 over `canonical_json` of the document's JSON-mode,
by-alias dump, so a document loaded over HTTP and the same document recovered for a resync
compare equal. A parse error is emitted only when the
editor document itself cannot be loaded or resynced and carries a fixed safe message;
authored pipeline errors are never parse errors — they arrive as degraded or source-only
documents. The frame builder rejects an event payload that already contains reserved key
`type`.

## Control flow

For an assistant apply, the route/service sequence is: reserve the session;
run the provider loop; acquire `save_lock` only when `apply_graph_plan`
executes; recompute revision/plan/authority under the lock; call the
transactional save once; reparse/verify; publish once; release in `finally`. A
failed precondition never enters the save service, and a completed save is
never automatically replayed after transport failure.

`POST /api/assistant/message` accepts exactly `session_id` and `message`; the
request is closed to unknown fields. There is no graph-plan confirmation
request and no `plan_ready` SSE event. A graph edit only authors project
source/config and does not run the pipeline or materialise outputs, so the
model may apply any valid stored plan directly. Missing, expired, stale or
used plans fail at the tool boundary before save. Runtime execution and
external writes remain separate user-initiated operations; v1 exposes no
assistant execution tool.

**Startup.** `_lifespan()`: `_clear_bytecache()` (rmtree every `__pycache__` under
`src/haute/`) → `configure_logging()` → `_load_env(Path.cwd())` → validate and cache
execution-telemetry and optimiser-housekeeping configuration →
`recover_json_runtime_storage()` (reaps dead-process spills and orphaned runtime storage under `.haute_cache`) →
`_ensure_pipeline_index()` (builds the name→path index once, under a double-checked lock) →
spawn `_watcher_forever()` and a tracked worker-thread optimiser-reaper task. The lifespan yields
without awaiting filesystem housekeeping, so temp-directory population cannot delay server
readiness. Shutdown cancels and awaits the watcher and observes the reaper task; reaper failures
are logged rather than silently discarded. A partial startup failure after the interactive pool
has opened still cancels every task already created, clears the exported task handles, and closes
the pool; no startup exception may strand a worker process or watcher.

**Request middleware chain.** `add_middleware` prepends entries and Starlette later wraps in
reverse, so runtime outer-to-inner order is `LocalTrustedHostMiddleware →
LocalSessionMiddleware → _RequestIdMiddleware → route` in both dev and built-UI modes.
Vite preserves the browser authority while proxying `/api` and `/ws`; no CORS middleware
exposes a second request path around the exact authority checks.
Host/auth failures therefore bypass request-ID binding/logging/header injection. The
registered exception handlers run inside `_RequestIdMiddleware`; an exception none of them
claims reaches it, is logged as `unhandled_exception` with its `error_class` and traceback,
and is answered `{"detail": _INTERNAL_ERROR_DETAIL}` with the selected safe request ID. It is
the application's single handler for unexpected exceptions; routes do not catch `Exception`
only to log it and answer 500. `LocalSessionMiddleware`
checks Origin before its `OPTIONS` exception, so a trusted preflight bypasses the token while
an untrusted preflight still receives 403. `_select_request_id` retains only a 1–64
character ASCII token matching `[A-Za-z0-9][A-Za-z0-9._:-]*`; otherwise it generates a new
ID and logs only the bounded rejection reason and input length.

**Route registration order matters.** The feature routers (`pipeline_router` through
`git_router`, plus the assistant router owned by [assistant](../assistant/low-level.md)) are
included first; then two catch-all Starlette `Route`s (not typed `APIRoute`s — they carry no response
model by design) match any unhandled `/api/{rest:path}` or `/ws/{rest:path}` `GET` and return
a clean JSON 404 — registered *before* the SPA catch-all so an unmatched API/WS path never
falls through to `index.html` (which would otherwise return `200 text/html` and break the
frontend's `res.json()`). The SPA catch-all (`GET /{full_path:path}`) is registered last,
inside the `if static_build_ready(STATIC_DIR)` block, and is absent entirely in dev mode.

**`/ws/sync` connection.** Reject (close code 1008) if `websocket_rejection_reason` finds a
problem with headers/query params (or if that check itself raises `AttributeError` — treated
as "missing metadata", also rejected). Otherwise accept, register in `ws_clients` (lock-
guarded), and loop `receive_text()` → `_handle_ws_sync_message`: a plain non-JSON string is a
no-op keep-alive; a JSON `{"type": "resync", ...}` runs `_prepare_ws_resync` in a thread pool
(discover pipelines, hash-check the fingerprint, parse if changed) and replies only to the
requesting socket — an unchanged fingerprint short-circuits to no reply at all. Disconnect
(`WebSocketDisconnect`) is caught silently; `finally` always discards the client and clears
its send-state.

**File watcher loop.** `_watcher_forever()` wraps `_file_watcher()` in a crash-restart loop
(`_WATCHER_RESTART_DELAY_SECONDS = 0.1`; `CancelledError` propagates, everything else logs
and restarts). `_file_watcher()` watches `cwd`, the pipeline directory, `modules/`, and
`config/` (each only if it exists and differs from `cwd`) via `watchfiles.awatch`
(recursive); if `watchfiles` isn't installed, live sync is disabled with a warning, not a
crash. Every filesystem event batches into `pending_changes`; a 300ms debounce timer
(`asyncio.Task`, cancelled and restarted on each new event) triggers `_flush()`, which:
1. Snapshots and clears `pending_changes` (so new events queue independently of the batch
   being processed).
2. Bails out entirely if `watcher_is_paused()` (a haute-initiated git op holds the pause).
3. Consumes self-write markers (`is_self_write(path, consume=True)`) by content identity:
   a marker matches only while the file's current bytes (or its absence, for a deletion
   marker) equal what the server committed. Matched paths are skipped and never reach the
   parse step; a marker whose content no longer matches is discarded and the event is
   processed as an external change. A consumed marker also clears the path's
   last-broadcast fingerprint, so an external restore of previously broadcast content is
   broadcast rather than deduplicated.
4. Classifies each remaining `.py`/`.json` change: `config/*.json` → re-parse every
   discovered pipeline; `modules/*.py` → re-parse only pipelines importing that module stem
   (case-insensitively, via `_module_dep_key`); any other `.py` (excluding `utility/` and
   dunder-prefixed files) → re-parse that pipeline directly when added/modified. Any direct
   pipeline `.py` addition, modification, or deletion invalidates the pipeline name→path
   index before discovery; a deletion is not reparsed. Module and config changes (including
   deletion) do not discard an otherwise valid pipeline index.
5. For each changed pipeline: hash raw bytes first (cheap) and skip the recovery load
   entirely if the byte hash is unchanged *and* the change wasn't dependency-triggered (a
   module/config change always re-recovers even if this pipeline's own bytes are unchanged,
   since its *effective* document may differ). On a successful document load, publish
   `pipeline.document.update` with the document payload + a content fingerprint + the
   wire-form source path; if the document itself cannot be loaded, publish `parse.error`
   with the fixed safe message and evict the stale fingerprint so the next successful load
   re-broadcasts even if the bytes happen to match a previous good state.
6. If the flush body itself raises, the *entire* processed batch is requeued and `_flush`
   retries it in the same task at most three times with exponential backoff. Cancellation
   after snapshotting also requeues the batch. On exhaustion, `_flush` removes that batch
   from `pending_changes`, tries each change once in isolation so healthy paths still
   broadcast, and logs/drops each still-failing event. A later event for a dropped path is a
   fresh attempt; no retry task is recursively scheduled.

**Pipeline save (`SavePipelineService.save`)**, run inside the process-wide `save_lock` (an
`asyncio.Lock`, so it serialises against concurrent submodel create/dissolve as well as
concurrent plain saves, but does not coordinate another worker process):
1. Flatten submodel occurrences and validate singleton node types (at most one
   `apiInput`/`output`/`liveSwitch`) across the resulting executable pipeline, then validate
   unique sanitized node names (per-graph, then cross-module against every embedded submodel
   graph) and that no node carries a `_load_error` marker. A submodel boundary cannot hide a
   second singleton, and creating another occurrence of a definition that contains one counts
   as another executable singleton.
2. Resolve and validate `source_file` against the active pipeline root.
   Then require `base_revision` to equal the on-disk document's `source_revision`
   (`null` only when the target file does not exist); a mismatch raises
   `StaleDocumentRevisionError`, which the route returns as a `409` whose flat `detail`
   begins with `stale_document_revision:` before any write, cache mirror, or ledger commit.
   The precondition also digests every owned artifact it can observe (the parent source
   and sidecar, everything under `modules/` and `config/` beneath the pipeline root);
   every staged write or delete in steps 5, 7, 9, and 10 first re-checks that the
   artifact's current bytes, or absence, still equal that observation and raises the same
   conflict before the artifact changes. An application lock cannot make the multi-file
   commit atomic against external writers, so this is the guarantee: no owned artifact is
   replaced after it moved, and the window is the single atomic rename of each file.
   Before the remaining preflights, parse the current persisted parent and
   diff its canonical definition registry against the submitted graph. Every
   added definition path becomes both a no-clobber target and a
   transaction-local managed ownership claim. Every removed definition path
   becomes a deletion candidate only when its persisted sidecar names this
   parent and a complete project-wide reference audit finds no other parent.
   When the same canonical child path exists in both registries, its exact
   `definitionId` must also match; identity substitution fails `409`.
3. Resolve every derived no-clobber entry against the same module allowlist and
   reject `409` if either its source filename or sibling `.haute.json` sidecar
   already exists case-insensitively. Treat those derived additions as the only
   transaction-local managed ownership claims. Resolve every child definition
   path and prove ownership from its existing sidecar or one of those derived
   additions. All checks compare fully resolved, casefolded paths and run
   before all writes. No caller-supplied deletion, no-clobber, ownership, or
   `managed` compatibility input exists.
4. Snapshot the *on-disk* graph's config-file set (`_compute_disk_prev_config_files`) — the
   diff baseline for stale-file cleanup, computed **before** any write in this call.
5. Generate code (`graph_to_code` or, if submodels are present, `graph_to_code_multi`),
   validate every output path against the allowlist (main file exact match, or
   `modules/<name>.py` with no traversal/reserved-device-name/case-collision), and stage each
   write.
6. Emit non-blocking warnings for structured `apiInput` nodes with no `tables[]` yet.
7. Write per-node config JSON sidecars (collision-checked against protected load-error paths
   and against each other, casefolded).
8. Write the parent `.haute.json` position sidecar. For each child whose
   ownership passed step 3, write positions plus `managed_parent`. All sidecar
   writes are transactional.
9. Stage deletion of any derived submodel source and
    its sibling `.haute.json` sidecar (skipping any that casefold-collide with a path this
    same save just wrote).
10. Reparse the fully staged document, require its editor recovery state to be
    `ready`, and return that document's raw-artifact `source_revision`. On any propagated exception in steps 5–11, roll back every staged write (restore
   snapshotted bytes, delete newly-created files) and re-raise unchanged. Rollback restores
   an artifact only while it still holds this transaction's bytes (or is still absent, for a
   staged delete); an external edit that landed mid-transaction is left in place and logged
   as `rollback_skipped_external_change`.
11. Only after every write commits: delete stale config files (the diff from step 4, minus
    what this save just wrote or protects), invalidate the pipeline index, and — if the
    project has a recorded git working branch — capture the save in the git ledger
    (`_git.commit_save`); `GitDomainError`/`GitError` become response warnings because the
    on-disk save already succeeded (if version capture was skipped because git lacks a
    commit identity, `identity_required` is set to `true` to prompt the client, while every
    other capture failure leaves it `false`), while an unexpected exception still propagates
    after the filesystem transaction and stale cleanup have committed. Return the
    committed `source_revision` with the normal response fields.

**Executable graph request containment.** Before work starts, every route that
executes or profiles a client-supplied graph confines it to the configured
project root. `_prepare_runtime_graph` flattens embedded submodels, fills a
missing source from `haute.toml`, rejects a supplied `source_file` that resolves
outside the project, and validates every path-bearing node in the flattened
graph. Modelling train/dispersion/estimate and optimiser
solve/estimate/frontier-auto-range use that helper before delegating to their
services. Pipeline preview/trace/write-output, Explore, and OUTPUT dry-run
likewise flatten before validating the submitted source and runtime paths at
their route boundary (and use the configured source when their execution
contract requires it). An HTTP body therefore cannot select the
direct-execution external-pipeline re-rooting behavior. Runtime path adapters
map `MalformedRuntimePathError` to HTTP 400 and
`RuntimePathOutsideProjectError` to HTTP 403 by concrete exception type; error
message wording is not part of the status-selection contract.

**Preview / trace supersession**, both routed through the same `SupersessionCoordinator`
pattern (`_preview_supersession`, `_trace_supersession`, each bounded by its own
`asyncio.Semaphore` sized by `HAUTE_{PREVIEW,TRACE}_MAX_CONCURRENCY`, default 2):
after request containment, build a composite key from
`(operation, source_file, source, graph_fingerprint, ...
operation-specific selectors)` → `run_latest()` → on preview, an
`ExecutionCancellationToken` is threaded through so a superseded preview's in-flight work is
stopped. In production process mode the call is submitted to the warm interactive-worker
pool. The supervisor polls the preview cancellation token (trace receives an equivalent
parent stop signal), kills and joins the process on timeout/supersession, replaces the
slot, and only then resolves the route future. The coordinator can therefore clear
`state.active`, release the semaphore, and release admission immediately after that
terminal result; there is no late computation. Explicit thread mode retains
`run_blocking_with_response_timeout`, `BlockingWorkTimeoutError`, and its
`background_task`, so the existing deferred cleanup path remains correct and visible
rather than becoming a silent fallback.
The worker budget is the parent's actual admitted headroom plus its optional absolute
process cap, not merely the wider profile default. Remote HTTP reconstruction accepts
payloads only from the closed public-contract and memory-error identities; arbitrary
child exceptions remain redacted internal failures even if they implement a method
named `to_payload`. Memory outcomes the child cannot curate are classified from
parent-side evidence and answered with a parent-authored, data-free 507 detail
(`error_code="memory_limit"` plus the operation and a closed reason): a pool worker
crash whose exit code looks memory-limited under a configured growth cap
(`reason="worker_may_have_exceeded_memory_limit"`), a remote exact
`builtins.MemoryError` (`reason="worker_memory_exhausted"`), and a remote exact
`NativeMemoryLimitUnsupportedError` (`reason="native_memory_cap_unavailable"`). Any
other pool-worker crash logs and returns the redacted internal 500.

**OUTPUT dry-run** (`routes/output_assemble.py`): validate the mapping shape
(`validate_v2_output_mapping`, data-independent) → flatten the graph → locate and type-check
the target node → validate every runtime input path stays inside the project root → replace
the node's `config` in-memory with the volatile mapping → extract the parent's admitted budget
→ execute and response-model-validate in the warm process pool. The worker constructs a
child-local context without reserving admission twice. Timeout/cancellation kills and joins the
slot before the route releases admission; no late result exists. The closed remote contract and
memory errors retain their existing 422/507 mappings; arbitrary remote failures remain redacted.
Explicit thread compatibility mode uses the existing deferred-release helper and is never an
automatic fallback.

**API Input table build** (`routes/input_cache.py`, the `_json_shred/` package): an
`apiInput` build job validates the config and path, holds the admitted reservation, and
chooses every table's generation id and staging token and the build's scratch token. A
one-shot hard-capped process (`build_api_input_tables_worker`) shreds the source once and
publishes each planned table through the input-snapshot store, deferring retirement. After a
timeout, crash, or cancellation the parent reconciles each planned table — a generation the
child published stays current, anything unpublished is removed — and removes the scratch
directory; the job completes only when every table is published. On success the parent
retires superseded generations.

**Output write** (`routes/pipeline.py`, `executor.py`): preflight destination checks occur in the
parent. For file sinks, a one-shot process executes the graph and writes a parent-selected sibling
staging file, fsyncs it, and returns a bounded manifest containing the canonical final/staging
paths, byte length, digest, and response counts. The parent validates containment, type, digest,
single-link ownership, overwrite policy, and ownership immediately before atomic publication and
directory fsync. A hard-linked stage is rejected so another pathname cannot mutate the selected
output after publication. For
database/lakehouse sinks, the child performs the existing connector transaction and returns its
bounded result; no non-transactional parent replay is introduced. Timeout/cancellation joins the
worker and removes a known file staging artifact before returning 504. The sole file-publication
and directory-fsync section runs under the same short gate that records route cancellation, so a
result/cancel race has one linearized winner and cannot pass through a check/rename gap.

**Node-data build and profile** (`routes/_node_data_service.py`): the background supervisor
remains a parent thread only to bridge the synchronous one-shot worker into the job lifecycle.
The child performs graph execution and writes only into the parent-named staging directory it is
given, or — for a profile — leases the parent's exact resolution and computes statistics without
writing anything. The parent validates the returned manifest or analysis outcome, rechecks
latest-wins ownership, publishes the generation or the analysis document, and transitions the
job. Cancellation, supersession, timeout, and worker failure terminate/join the process and
discard that exact staging directory *before* the job's terminal status is published, so a client
that reads the outcome never finds staging the build left behind; a last-resort sweep after the
status covers a path that failed while publishing it. Child code cannot write the `JobStore`, the
snapshot store's current pointer, or the analysis-result store.

### Node-data builds

**Node-data builds** (`routes/node_data.py`, `routes/_node_data_service.py`): every request is
flattened, source-file checked, and runtime-path validated like other graph routes before
`NodeDataService` resolves the consumer point with a `DataPointResolver` over
`NodeSnapshotStore(project root)`. The resolver's building probe reports a node-output build
from the service's own identity-digest → running-job map, an input-snapshot build through
`input_cache.input_snapshot_build_running(identity_digest)`, and an API-input table build
through `input_cache.api_input_table_build_running(table_digest)`. `point` returns
`NodeDataPointResponse` (`slot_key` is `producer|port|source`); node-output details come from
the slot's latest generation and the running job; snapshot-backed inputs report their
generation's rows and bytes and name `/api/input-cache/build` and `/api/input-cache/clear`
(an API-input table's point does too, its build and clear acting on the node's tables
together); a direct-Parquet input sets `reads_directly`. A service-wide slot lock makes each `run` decision and each `clear` atomic, so
simultaneous identical requests start one job and join it. `run` pins and completes a node
output current for `all` unless `refresh`, joins the running job for the same identity, and
otherwise creates a `node_data` job with a parent-chosen staging token, registers
it latest for the slot digest (superseding and transitioning the previous job), and starts a
supervisor thread. The thread first joins the superseded job's thread, then creates an admitted
`node_snapshot` context, binds metrics publication, and prepares the node's snapshot-backed
inputs itself (`prepare_graph` then `prepare_input_snapshots` under that context). Preparation
can build or refresh input snapshots whose generations belong to the signature, so the thread
then binds the build to the identity of the prepared inputs, re-keys the running job under that
identity (so `point` and joins still find it), opens a
[seed plan](../caching/low-level.md#seed-plans) for the build (`NODE_SNAPSHOT`, the built node
as `build_node_id` so only strictly upstream points are seeded, the request's `refresh`, which
seeds nothing but still captures) whose captures stage under the build's own token, and — with
that plan leased until the worker has exited — runs `_run_node_snapshot_worker` in an
isolated worker with `HAUTE_NODE_SNAPSHOT_TIMEOUT` (default 1800 s), passing the plan's handoff
in the request. The child sets the project root, confirms the bound identity, and runs its whole
execution and its target write under the streaming chunk size it inherited from the server at spawn, so the
target and every capture the run makes resolve the same size: it adopts the plan (leasing the same generations), executes the node under it
with `enforce_contracts=True` and `prepare_inputs=False` — intermediate capture points are
published as automatic generations —
rejects a multi-frame output (`node_snapshot_multi_frame_unsupported`), sinks the frame into
staging named by the parent's token via `write_parts` (passing the node's join recipe or
write recipe from execution, so a chunk-local filter writes `input_sliced`), confirms the identity again, and publishes with
`explicit=True`, the request's `refresh`, and as `dependencies` the closure the plan recorded
for the node, so replacing any snapshot it seeded or captured makes it stale. A superseded
publication counts as cached only when a current, full-width generation of the node exists;
otherwise something the build read was replaced while it ran and nothing holds the node's
data, so it is `NodeSnapshotInputsChangedError`; an identity that moved at either
check (a source or snapshot changed while the build read it) is
`NodeSnapshotInputsChangedError`, reported as a contract error and never published. It returns
a closed `_NodeSnapshotWorkerOutcome` (generation id and `published`/`superseded` with the
execution's `worker_evidence()`, or a `public_contract`, `memory`, or
`contract` failure). The parent validates the envelope, adopts the child's input preparation, seeds,
captures, and warnings into its own execution context (`adopt_worker_evidence`), and completes the
job under the registry's latest-publication guard with `generation_id`, `outcome`, and
execution metrics, or maps failures to `contract_error`, `memory_limited`,
the cancellation or supersession reason, a public
contract error from input preparation (except that a preparation failure after the job was
cancelled or superseded ends with that reason), or the internal-error envelope. After the worker has terminated, the supervisor discards any
staging directory carrying its token — the build's own and its captures' — which a killed
worker could not remove. `clear` cancels
the slot's running job, waits for that job's supervisor thread to finish so the worker can no
longer publish, and then calls `NodeSnapshotStore.clear_slot`; for other kinds it answers
`delegated`; every kind's `clear` also cancels the point's running profiles and removes its
stored analyses. An invalid API-input port is `node_data_point_invalid` (400) on every route.
`status` serves both job kinds and reports `error`, `error_code`, and `error_detail` from the
job, so a memory limit and a changed-data contract error are distinguishable from the message
alone. Every resource failure carries `error_code` `memory_limit` with the execution payload as
`error_detail`, whether the execution reported the limit, admission refused it, or the parent
killed the worker over its RSS limit.

### Cache usage

**Inventory, size and clearing.** `GET /api/cache/usage` returns
`CacheUsageResponse`:

- `total_bytes`: every generation and in-flight staging directory in the store;
- `automatic_bytes`: node-output captures no pin protects;
- `automatic_budget_bytes`: the
  [automatic-capture budget](../io-layer/low-level.md#node-output-snapshots).

It costs one pass over the store with a small metadata read per generation and no
point resolution, so the preview status bar reads it each time a preview settles.
`POST /api/cache/nodes` lists data owned by the graph's nodes plus every remaining
stored dataset; `/api/cache/clear` clears the identities named by the selected row.
No request is refused for a quota, and eviction sends the browser no diagnostic.
Active-reader leases, replacement, disk-headroom checks and real write failures keep
their existing behaviour.

**Per-node cache report** (`routes/cache.py`): `POST /api/cache/nodes` takes a graph and a
source and returns `CacheNodesResponse`. The graph is flattened first, so a submodel's nodes
are reported the way they are cached — individually. `NodeDataService.points_for_graph`
resolves every node's point against one `DataPointResolver` and the caller's store — the whole
report builds one `NodeSnapshotStore` — because the per-node cost is the resolution itself.
That cost is `CACHE-S17`'s, multiplied by the node count: each signature re-walks the
canonical graph, so the batch is quadratic in node copies. It is milliseconds per node at the
sizes measured and is why this endpoint is asked for explicitly rather than polled; a node whose point cannot be resolved becomes a row
rather than failing the request, because a report about every node is worth least precisely
when one node is half-configured. An unwired Banding (`NodeDataPointInvalidError`) and a Data
Input with no path (`PolarsIoConfigError`, raised resolving its own identity) carry
`unavailable_reason`; both are named rather than caught as the `ValueError` they derive from,
so a programming error still surfaces as a 500. A `SourceCacheCorruptError` is different: the
node's data exists and is damaged, so `_corrupt_response` synthesises the row from the
consumer point — the kind is a graph lookup and the producer is the point's — with
`state="corrupt"` and no reason, and the row carries the bytes like any other, because the
corrupt generation still occupies disk space. A `PipelineGraph` carries no id-uniqueness
validator, so a duplicated node id is resolved once; two rows would otherwise claim one
node's bytes. Each
row's `state` describes the generation that node would read for its own column demand, while
`generations`/`size_bytes` are only ever data that row is the one to carry: every signature
the store holds for it as a node output, or the whole identity behind it when it is a
snapshot-backed input.

**Every byte is reported exactly once**, which three rules together secure. A node reading an
upstream point does not carry that point's bytes and names it in `reads_from`, though it still
carries its own captured output. A shared input snapshot — two Data Inputs with one
configuration, or one submodel instantiated twice, resolving to a single identity — is charged
to exactly one reader and named on all of them in `shares_snapshot_with`; the carrier is the
smallest node id among the readers, never iteration order, so an unrelated edit never moves
bytes from one row to another. And a row's figures
come from the inventory's owner for the identity, never from the single generation the point
resolved, so a non-current generation or an in-flight staging directory under that identity is
reported rather than dropped. Matching is by identity digest, which `points_for_graph` returns
for exactly that purpose, because a descriptor's label is not an identity: the same file read
with different arguments is a different identity with the same path.

The route tracks the identities its rows carry and lists every other owner in `other`, so the
two halves are exhaustive by construction — a node no longer in the graph, the same node's
data under another source, an input snapshot nothing reads. `unattributed_*` is what no
metadata could name. Every row and owner also carries `newest_created_at` and `build_seconds`
from its newest generation's metadata, so the report says when a thing was cached and how long
that took; a generation published before durations were recorded reports `null`, which the
surfaces must show as unknown rather than as an instant build. Rows plus `other` plus
`unattributed_*` account for stored dataset bytes, which
`tests/test_cache_nodes_routes.py` checks independently against files on disk.
`unmarked_identities` counts identities whose provider marker does not classify.


**Clearing a row** (`routes/cache.py`): `POST /api/cache/clear` takes the `identity_digests`
a report's row carried and clears exactly those, through
`NodeSnapshotStore.clear_identity`. A row names identities only when it carries their bytes,
so clearing a row removes what that row reported and nothing else — not the node's data under
another source, which is a different row, and not a shared snapshot charged to a different
reader. The identity is reconstructed from a generation's own metadata and **only cleared when
the reconstruction reproduces the digest that was asked for**, so a hash the caller supplies
can never name a different identity than the one the store verifies. A digest the store no
longer holds is reported as not cleared rather than failing the request: acting on a report a
moment out of date is an ordinary race. The response reports what was cleared and the bytes
freed, which the caller uses rather than assuming its request succeeded.

This is the only way to reclaim a node the graph no longer has: `/api/node-data/clear`
resolves a node of the posted graph. Cache data is regenerable, so the endpoint needs no
confirmation of its own; what it must not do is surprise a reader mid-scan, and it does not,
because every clear retires a held generation on release rather than deleting it underneath
the scan.

### The data profile

`POST /api/node-data/profile` answers with the point's profile for its current data version.
Under a service-wide profile lock, the point is resolved for every column: a point that is not
`current` is `cache_required` (the profile describes the whole dataset, so it is never computed
from partial data), an `AnalysisResultStore` document for `(point digest, data version,
profile, 1)` is `completed` with the result, a running profile of the same point and data
version is `joined`, and otherwise a `node_profile` job is created and `started`. The job
thread creates an admitted `explore_analysis` context, binds metrics publication, and holds
`lease_resolved(resolution, exact=True)` for the whole job, so the worker reads exactly the
data the parent resolved even if the point is refreshed or cleared meanwhile. It runs
`_run_profile_worker` in an isolated worker (`HAUTE_NODE_DATA_PROFILE_TIMEOUT`, default
1800 s), which sets the project root, leases the same resolution exactly, computes
`_build_frame_stats` from `src/haute/_frame_profile.py` (owned by
[explore-eda](../explore-eda/low-level.md)) in a `node_data_profile` stage, and for a direct
file re-resolves afterwards so a rewrite during the read is reported rather than profiled. It
returns a closed outcome carrying either the profile or a `public_contract`, `memory`,
`contract`, or `changed` failure, validated by the parent through the same envelope as a
build; the parent then writes the store and completes the job with the profile under the
profile lock and the registry's latest-publication guard, so publication is indivisible against
both a cancellation and a `clear` of the point: whichever of the two serialises first, a
cleared point never keeps an analysis of the data that was removed, and a terminal admission,
memory, cancellation, or changed-data outcome leaves the analysis store unchanged.

### Banding statistics

`POST /api/banding/stats` (`routes/banding.py`, `routes/_banding_stats.py`) answers what one
banding factor's data looks like over the whole point its node reads. The factor comes from the
editor rather than the saved graph, so the numbers follow what the user is editing; the node only
says which point to read. Because the edited factor may name a column the saved node does not, the
request resolves the point for the node's demand *widened by that column*, so a snapshot without it
reads as not current for this request instead of answering from data that lacks it — and the
`point` in the response is that same widened reading.

`status: "cache_required"` with the point is the whole answer when the point is not current or its
data changed underneath the request. Otherwise the statistics are computed through
`run_synchronous_analysis`, so they run under an admitted `explore_analysis` context, hold the
lease for the collection, and are memoised per data version and request — the request digest
covers the column, mode, rules, closure, bin count and value limit, so an edit that cannot change
the numbers does not recompute them. The route runs the analysis through
`run_until_disconnected`, so the editor superseding its own request on the next keystroke stops
that scan instead of leaving a whole-dataset collection holding its admission and lease for an
answer nobody will read; `/api/explore/pivots/members` answers the same way.

`data_version` is the version the *lease served*, not the one the point reported when the request
resolved: a refresh between the two makes those different, and the editor decides whether it may
show whole-dataset counts by comparing this version with the one its point currently holds, so a
result labelled with a version it was not computed from would be shown as current.

For a numeric mode (`continuous`, `breakpoints`) the response carries `non_finite_count`, `minimum`
and `maximum` over the finite values, and `bins` from the shared `_binning.equal_width_bins`:
equal-width `[lower, upper)` intervals with the last closed at the maximum, one bin for a constant
column, and no bins at all for a column with no finite value. Every numeric dtype is measured as a
float, including `Decimal`, which has no `is_finite` of its own and raises on the check. A value is
counted in the bin whose *published* edges contain it — `bin_edges` derives the edges and the count
is placed against those numbers — because deriving an index by arithmetic instead is a second
calculation that can round differently from the edge it should agree with: over 40 bins of `[0, 1]`
it put `0.3` in the bin whose lower edge is `0.30000000000000004`, above the value itself. For `categorical` it carries `values`
as `{value, count}` on the column cast to the text execution matches on — sorted by count
descending then value ascending and capped at `value_limit` — with `distinct_count` over non-null
values and `other_count` for the non-null rows outside the returned ones; nulls count only in
`null_count`, and `"NaN"` and `"inf"` are ordinary values. When the factor has rules, `rule_counts`
is aligned to the user's rules and `unmatched_count` is the rest, both from
[`banding_rule_claim_expr`](../rating/low-level.md#which-rule-claimed-a-row--bandingruleclaimexpr-ratingpy),
so the editor shows the counts a run would produce.

Three conditions are HTTP 422: a column the data does not have (whether the projection or the
schema finds it), a numeric mode on a column it cannot compare, and rules execution itself would
refuse — the last carrying execution's own message. The repository keeps `HTTPException.detail` a
plain string, so these are told apart by their messages rather than by a code in the body. Invalid
consumer wiring is HTTP 400, and admission or memory-limit failure is HTTP 507 through the shared
analysis helper.

Request-time analyses (`routes/_synchronous_analysis.py`) answer inside the request instead:
`run_synchronous_analysis` serves the `SynchronousAnalysisCache` entry for the point's current
data version, or admits an `explore_analysis` context, leases the point, computes under it,
and memoises the result under the version the lease actually served; a point that is not
`current` raises `cache_required`, a direct file rewritten while the analysis read it raises
`node_data_changed` and is neither returned nor memoised, and an admission or memory-limit
failure is HTTP 507 with the execution error payload.
`run_until_disconnected` runs one of these off the event loop and cancels its context when the
client disconnects or the request task itself is cancelled; either way it waits for the
abandoned analysis to stop, so its admission and lease are always released, discarding whatever
that analysis reports, before answering 499 or propagating the cancellation.

### Rating factor levels

`POST /api/rating/levels` (`routes/rating.py`, `routes/_rating_levels.py`) answers which levels the
raw factor columns of a Rating Step actually hold, over the whole point its node reads. The editor
listed the levels of preview rows, so a level appearing only outside them could not be given a rate
and its rows silently took the table's default.

The request names `columns` (1–100, read once each in the order first asked) and a `value_limit`
(1–10000, default 1000). The point is resolved for the node's demand *widened by those columns*, on
the same rule as the banding statistics above, and the response carries that same widened reading.
`status: "cache_required"` with the point is the whole answer when the point is not current or its
data changed underneath the request; otherwise the levels are read through `run_synchronous_analysis`
— admitted, leased, cancellable, and memoised per data version and request — and the route runs it
through `run_until_disconnected`, so a superseded request stops its scan. `data_version` is the
version the lease served, for the same reason it is on the banding response.

Each column's `values` are `{value, count}` pairs keyed by the rating lookup's own
`_rating_key_expr`, so a level chosen in the editor is one the lookup joins on rather than a
rendering of the value that merely looks like it. They are sorted by count descending then value
ascending and capped at `value_limit`, so what the cap keeps is what the data is mostly made of.
`distinct_count` counts the levels that could be chosen — the number `values` would hold without
the cap — and `null_count` the missing rows. A missing value and a blank string are neither of them
something to rate on, so neither is a level: only the missing ones are counted. `total_rows` is the
rows those levels were read from, which the editor reports.

Two conditions are HTTP 422: a column the data does not have (whether the projection or the schema
finds it), and a column that is not text — `String`, `Categorical` or `Enum`, the kinds the preview
path has always offered, because a number's levels are banding's job. As with the banding
statistics, `HTTPException.detail` stays a plain string and the two are told apart by their
messages. Invalid consumer wiring is HTTP 400, and admission or memory-limit failure is HTTP 507
through the shared analysis helper.

## Edge cases and invariants

Save preconditions capture artifact identities **before** validating the client's
document revision. Revision validation must not bless bytes captured after an
external edit. The same ordering covers a new destination appearing during save;
later write and cleanup checks still compare against the captured identities.

- **Partial frontend build never serves.** `static_build_ready()` requires both
  `index.html` *and* `assets/` to exist — an interrupted `npm run build` or a hand-created
  directory with only one of the two would otherwise pass a bare `.exists()` check, mount
  `assets/` (raising `RuntimeError` at import if the dir is genuinely missing), or 500 at
  request time reading a missing `index.html`.
- **Windows `.js` MIME type.** The Windows registry commonly maps `.js` to `text/plain`,
  which browsers reject as a script; `mimetypes.add_type` is patched at module import, before
  any `StaticFiles`/`FileResponse` construction.
- **Self-write cooldown vs. per-path content tracking.** `is_self_write()` supports two modes: a
  bare cooldown check (`now - _last_self_write < 2.0s`) for callers with no specific path, and
  a per-path content match for the file watcher's per-event check. A path marker records the
  SHA-256 of the bytes the server committed, or a deletion flag; it matches only while the
  current file bytes (or absence) still equal that identity, and `consume=True` removes the
  entry on match. A marker whose identity no longer matches is discarded so a later external
  write to the same path is broadcast. Path-only marking is not supported: every `Writer`
  callback, staged delete, and rollback write supplies the committed content. Per-path entries
  are pruned after 60 seconds of retention so a crashed or never-consumed marker cannot leak
  memory indefinitely.
- **Watcher pause is reentrant and watchdog-bounded.** `pause_watcher()` depth-counts nested
  git operations sharing one pause; the outermost call sets a hard deadline (default 60s) that
  only extends, never shrinks, on a nested call. `watcher_is_paused()` force-resumes (returns
  `False`) once that deadline is exceeded, logging once per overrun — a hung or
  non-unwinding git op can never freeze live-sync permanently. A 1-second "settle window"
  after release absorbs the checkout's own debounced trailing filesystem events so they are
  not mistaken for user edits.
- **`ws_clients` mutation is lock-guarded** even though CPython's GIL would normally make
  `set.add`/`set.discard` atomic — the comment notes this is deliberate for multi-worker
  deployments and free-threaded (`--disable-gil`) CPython, where that guarantee no longer
  holds.
- **Per-client WebSocket sends are serialized, not concurrent.** `broadcast()` tracks
  in-flight sends per client (`_ws_send_inflight`) and queues (`_ws_send_pending`) any
  message that arrives while a send to that client is still outstanding, replaying the queue
  after the current send completes — so two rapid broadcasts to a slow client never race each
  other out of order. Each send (and each stalled-client close) has a hard 1-second timeout;
  a timeout marks the client dead and discards it rather than blocking the whole fan-out.
- **The pipeline index has three lifecycle mutation points**: startup priming, watcher
  invalidation for a direct pipeline `.py` add/modify/delete, and invalidation after a
  successful save.
  `_ensure_pipeline_index()` uses double-checked locking so concurrent cold-cache readers
  never scan twice. The module-dependency twin uses a dedicated single-builder lock and
  snapshots the pipeline-index generation; it publishes only if that generation is still
  current, otherwise it rescans. Invalidators therefore remain short and a stale in-flight
  dependency scan cannot overwrite them.
- **Module-dependency keys are casefolded** (`_module_dep_key`) because the build side
  derives a module stem from a `pipeline.submodel("modules/<name>.py")` *source literal*
  while the watcher derives it from an *on-disk filename* — on case-insensitive filesystems
  (macOS, Windows) those can differ in case and must still match, or live-sync silently goes
  stale for that module.
- **Sidecar position keys use persisted node identity.** Submodel occurrences
  use their occurrence name (node.id == label == alias == name) and ordinary nodes use their
  parser ids; load and save use those exact keys without deriving identities
  from names or filenames.
- **Every casefold-collision guard in `_save_pipeline.py`** (config sidecar paths, module
  output paths, save-vs-delete-target overlap) treats names differing only in case as the
  *same file*, even on case-sensitive Linux — the guard runs on every platform so a pipeline
  saved on Linux stays loadable on a macOS/Windows checkout, at the cost of leaving harmless
  same-case residue behind on Linux in the delete-skip case.
- **Windows-reserved device names** (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`,
  any casing, any extension) are rejected for both codegen output filenames and config
  sidecar filenames, on every platform, for the same cross-platform-loadability reason.
- **`apiInput` validation guardrails**: An unknown column `type` string is rejected at
  validate time; two table labels whose filesystem-safe sanitisation collides *casefolded*
  are rejected (case-insensitive filesystems would otherwise let the second table's parquet
  clobber the first); and every validation failure raises `ApiInputSchemaError` specifically,
  so the JSON-cache route can catch it and return a structured 422 rather than a generic 500.
- **OUTPUT assembly's cut is schema-determined, never data-dependent** (axiom A4 in the
  algorithm's design doc): `_plan_cut` operates purely on which table carries which field, so
  the plan can be computed once at save time and reused at every run without re-deriving it
  from row values. `_prune` treats an empty array/object as "carries no data" and omits it —
  the documented round-trip invariant is equality *up to empty collections*, not bit-exact.
- **`is_active_mapping_entry`** skips an OUTPUT mapping row with a blank `source_column` or
  `output_path` in the shared OUTPUT contract, assembler, and execution consumers — a
  half-finished editor row never demands `pl.col("")` or produces a confusing
  `missing=['']` failure. Projection planner parity is owned by the execution-engine
  workstream.
- **`GraphEdge` handle validation rejects `""` but accepts `None`** for `sourceHandle` /
  `targetHandle` — the two are semantically distinct (an unnamed port vs. no port specified)
  and coercing one into the other would mask a genuinely invalid empty-string port name.

## Error handling

| Raised as | Route(s) | HTTP status | Notes |
|---|---|---|---|
| `ConfigError` | save, preview, output-assemble dry-run | 400 / embedded `NodeResult.error` / 422 | Save: bad `haute.toml`. Preview: swallowed into the node result so the canvas shows it in-situ. A public contract error that is also a `ConfigError` or `SchemaMismatchError` (`NodeConfigError`, `RatingFactorMissingError`) is not swallowed: in thread and process mode alike the preview answers its public 422, and the preview worker re-raises it with its payload. |
| `ContractMismatchError` | trace, preview, output-assemble dry-run | 422 / embedded `NodeResult.error` / 422 | Message already names the node + symmetric column diff. |
| `SchemaMismatchError` | preview | embedded `NodeResult.error` | Adapted identically to `ContractMismatchError`, so a propagated join-key dtype mismatch never becomes a generic 500. |
| `ParseError` | preview | embedded `NodeResult.error` | Preview surfaces graph-shape issues per node. An unreadable document on the editor-document routes propagates to the request-ID backstop as a sanitized 500 (authored failures arrive as 200 degraded/source-only documents). |
| `ApiInputSchemaError` | json-cache; preview/write execution | 422 | JSON cache retains its `type` discriminator envelope; execution routes use the public-contract adapter (`api_input_schema_invalid`). |
| `OutputMappingSchemaError` | output-assemble dry-run | 422 | Raised both by the schema-only pre-check and if execution surfaces it deeper (an unmapped port). |
| `ExecutionAdmissionError`, `ExecutionMemoryLimitExceededError` | any synchronous route (application handler) | 507 | Payload is `exc.to_payload()`, nested under `detail`, through `memory_limit_http_exception`; training and the optimiser pass an operation noun that adds the curated `message`. |
| Public contract errors (closed set below) | any synchronous route (application handler) | 422 / 507 / 409 | Routes that also log or order them against a broader clause keep an explicit clause with the same mapping. |
| `GitError` family | any route (application handler) | 403 / 400 | `git_error_http_exception`: guardrail → 403 verbatim, domain → 400 verbatim, plain `GitError` → 400 sanitized. |
| `PathOutsideProjectError`, `InvalidPathError` | any route (application handler) | 403 / 400 | Raised by the sandbox's `contained_path` for a request path; detail is the bare message ("Cannot access paths outside the project root" / "Invalid path"), never the refused path. |
| `InteractiveWorkerCrashedError` (memory-classified), remote `builtins.MemoryError`, remote `NativeMemoryLimitUnsupportedError` | preview, trace, output-assemble dry-run | 507 | Parent-authored data-free detail with `error_code="memory_limit"`, the operation, and a closed reason; a non-memory pool-worker crash stays a redacted 500. Mirrors the write-output worker classification. |
| `BoundedMemoryUnsupportedError` | output write | 422 | Distinguishes "cannot stream safely" from a hard resource limit. |
| `DataOutputDestinationExistsError` | `POST /api/pipeline/write-output` | 409 | `overwrite=false` refuses an existing file/table before publication and returns the destination in the detail. |
| `SupersededRequestError` | preview, trace | 409 | Raised by `SupersessionCoordinator`; the worker never runs for a superseded generation. |
| `IsolatedWorkerTimeoutError`, `BlockingWorkTimeoutError`, `TimeoutError` | preview, trace, input-snapshot and API Input table builds, output write, output-assemble, Explore | 504 / timed-out job | Production process mode kills and joins the exact worker before returning or transitioning the job. Explicit thread compatibility mode is opt-in and retains cooperative/deferred cleanup; it is never selected after a process-start failure. |
| `HTTPException` (raised directly) | path validation, node lookup, syntax checks | 400 / 403 / 404 / 409 | `raise_node_not_found`, `raise_node_type_error`, `raise_pipeline_not_found`, `raise_validation_error` centralise the structured-log + raise pattern. |
| Any other `Exception` | `_RequestIdMiddleware` | 500 | Logged as `unhandled_exception` with `error_class` and traceback; detail `_INTERNAL_ERROR_DETAIL`. |

The synchronous public-contract adapter maps this closed set to HTTP 422 (except `InputPreparationError` with `reason_code == "memory_limited"`, which maps to 507, and `SeedPlanExpiredError`, which maps to 409 because the preview a trace explains must be refreshed); background jobs
use the same stable codes and named fields under terminal `contract_error` (or `memory_limited` for that memory case):

| Exception | Stable code | Named fields |
|---|---|---|
| `ApiInputSchemaError` | `api_input_schema_invalid` | — |
| `PreambleError` | `preamble_failed` | `source_line` |
| `ContractResolutionError` | `contract_resolution_failed` | `node_id`, `node_type`, `failure_kind` |
| `InputPreparationError` | `input_preparation_failed` | `node_id`, `identity_digest`, `build_class`, `reason_code`, `remediation` |
| `ChunkMemoryRiskError` | `chunk_memory_risk` | `target_node_id`, `reason_code`, `estimated_target_row_bytes`, `estimated_minimum_chunk_bytes`, `row_expansion_factor`, `target_chunk_bytes` |
| `GroupByExecutionUnsupportedError` | `group_by_execution_unsupported` | `node_id`, `operator`, `profile`, `reason_code`, `remediation`, `estimated_peak_bytes`, `headroom_bytes` |
| `TraceCorrelationUnsupportedError` | `trace_correlation_unsupported` | `node_id`, `key_columns`, `dtypes`, `reason_code` |
| `RatingExtremaUndefinedError` | `rating_extrema_undefined` | `output_column`, `operation` |
| `RatingFactorMissingError` | `rating_factor_missing` | `table`, `factor` |
| `RatingFactorDtypeContractError` | `rating_factor_dtype_contract` | `table`, `factor`, `saved_dtype`, `input_dtype` |
| `LiveSwitchScenarioError` | `live_switch_scenario_missing` | `switch`, `scenario`, `available_mappings` |
| `NodeConfigError` | `node_config_invalid` | `setting` |
| `OutputNestingKeyError` | `output_nesting_key_null` | `frame`, `output_path`, `key` |
| `SnapshotPlanInputsChangedError` | `snapshot_plan_inputs_changed` | `target_node_id` |
| `SnapshotCorruptError` | `snapshot_corrupt` | `node_id`, `node_label` |
| `SeedPlanExpiredError` | `preview_seed_plan_expired` | `node_id` |

A preview answers `snapshot_plan_inputs_changed` for inputs that moved before it collected
anything and, since the mid-run case now stops too, for inputs that move while it runs: it
would otherwise render a seed signed for the old inputs beside a branch recomputed from the
new ones. Both are the same 422 the frontend already handles.

Except for handlers that return a `JSONResponse` directly, `HTTPException` responses use
FastAPI's `{"detail": <string-or-object>}` envelope; this includes structured 507 memory
payloads nested under `detail`. Pydantic request/query validation uses
`{"detail": [validation-error...]}`. `_RequestIdMiddleware` constructs its 500 JSON directly.
The JSON-cache router's `ApiInputSchemaError` is another deliberate direct-response exception:
`{"detail": str, "type": "ApiInputSchemaError"}`. Any load failure for a discovered file
in pipeline listing yields a `source_only` summary with `diagnostic_count=1` and the failure
is logged server-side; there is no per-item error string on the wire, and the `file` field
is always relative to the working directory (falling back to the file name). Live-sync
parse failures surface `str(exc)` as `parse_error.error`. An unreadable document on the editor-document
routes propagates to the request-ID backstop as a sanitized 500; authored failures arrive as
200 degraded/source-only documents. Live-sync diagnostics deliberately bypass the internal-error sanitizer.

Two safety nets exist above individual route handlers: `_RequestIdMiddleware` catches any
exception a route handler failed to catch and returns its separately pinned sanitized 500 shape (with a
structured log including the traceback), and the file watcher's `_watcher_forever` /
`_flush` layers ensure an internal watcher failure never crashes the background task or
silently drops a pending filesystem change. `EventBus.publish` isolates each subscriber's
exception individually (logged at `warning`, handler qualname included) so a broken
subscriber cannot suppress the event for any other subscriber.

`SavePipelineService._rollback` is deliberately best-effort: if restoring one file's
snapshotted bytes raises `OSError`, that failure is logged and rollback continues with the
remaining touched files — "recover most of the save" is preferred over "abort rollback
entirely and leave every touched file in whatever state it happened to be in."

## Testing

- `tests/test_cache_nodes_routes.py` covers `POST /api/cache/nodes`: every node of the graph
  reported including one with nothing cached, a re-cache replacing rather than adding to a
  node's dataset, two nodes resolving to one input snapshot reporting its bytes once with the
  second naming the first, a second identity under one path label still accounted for, a
  misconfigured Data Input leaving the rest of the report intact, the accounting invariant
  that rows plus `other` plus `unattributed_*` equal stored dataset bytes against a stray
  generation and a staging directory on disk, a generation whose metadata is unreadable
  reported as unattributed, a corrupt point reported as a row with `state="corrupt"` and its
  bytes rather than as an absence, clearing a row removing exactly its own identities while
  another source's row and an unrelated orphan are untouched, clearing a digest the store no
  longer holds reported rather than failing, clearing an orphaned row reclaiming it, another pipeline's node of the same name kept off this
  one's row, a shared snapshot's carrier not depending on node order, a
  node no longer in the graph and a node's data under another source both reported as `other`,
  an input snapshot reported on its reader's row and nowhere else, a node reading an upstream
  point carrying no size and naming that node, an unwired Banding reported as a row with a
  reason rather than failing the request, and the unmarked-identity diagnostic count.

- `tests/test_node_data_routes.py` covers a missing point for Banding, one build shared by
  Explore and Banding on one parent (`building` with the job, `joined`, then cached), an
  upstream edit making the point stale and a run publishing the new signature, a different
  signature superseding a running build, refresh recomputing a randomly sampled producer, a
  partial automatic generation becoming current and pinned, clear removing every signature,
  contract and admission failures through the job envelope, cancellation and clear
  terminating a real sleeping worker without publication or staging, clear never letting a
  build paused before publication repopulate the slot, simultaneous identical runs sharing one
  job, a killed worker's staging discarded by the parent, a build that prepares or refreshes
  its CSV input snapshot completing current, a build paused after preparation found by `point`
  and joined, a source replaced after execution read its rows never being published,
  cancellation during input preparation ending `cancelled`, delegation and direct reads for source kinds,
  invalid wiring and an invalid API-input port as 400, and a real isolated-worker build. Seed
  plans in builds: caching `A` then `B` seeds `A`, records it in `B`'s dependencies and in the
  job's metrics, and refreshing `A` stales `B`; clearing `A` leaves `B` current; caching `B`
  before `A` leaves `B` current; refreshing the root of `A → B → C` stales both descendants; a
  refresh build seeds nothing yet still captures its fan-out and join feeder; a build whose seed
  is refreshed before it publishes ends `contract_error` rather than reporting the node cached;
  and a build worker stopped, timed out, or killed at its memory cap ends with that status and
  removes the capture it had staged. `test_an_explicit_build_and_its_captures_share_the_editor_chunk_size`
  verifies that an explicit build and its captures run at the editor's chunk size and that the
  capture evidence records it. An explicit build of a chunk-local filter node writes `input_sliced` across several parts and equals the native result.
- `tests/test_analysis_results.py` covers the profile route: an uncached point asking to be
  cached, a profile computed once and then served from the store, a second request joining the
  running profile, a refreshed point never returning the previous profile, admission failure,
  a memory limit, and cancellation each ending in the typed terminal state with nothing
  stored, clear removing a stored profile, a direct file becoming unavailable after a rewrite
  or a Data Input rename, a file rewritten mid-profile reported as `node_data_changed`, and
  the synchronous helper running once per data version, requiring a cached point, reporting
  memory failures as 507, and stopping its analysis both on client disconnect and on
  cancellation of the request task.
- `tests/test_contract_error_adapter.py` verifies sync/background contract-error payload parity and rejects unversioned errors.
- `tests/test_error_detail_sanitization.py` verifies safe public error details, logging, domain-error exposure, route-specific sanitization, and sensitive-information leak prevention.
- `tests/test_error_response_shape.py` verifies standard error envelopes, flat syntax details, sanitized internal errors, and prohibition of dict route details.
- `tests/test_pipeline_index_cache.py` verifies startup population, cache hits, watcher rebuilds, no manual invalidation, and race-free concurrent reads.
- `tests/test_pipeline_read_json_route.py` verifies object JSON reads plus missing/non-JSON/invalid/non-object/traversal rejection.
- `tests/test_polars_steps.py::test_render_endpoint_reports_invalid_step_as_data` verifies the step render endpoint's rendered, invalid-step (data, not 4xx) and malformed-request (422) outcomes.
- `tests/test_serialization_invariants.py` verifies non-finite values serialize as sentinels in schema-preview and preview responses.
- `tests/test_preview_snapshot_seeding.py` covers preview seed-plan responses: a repeat preview
  reporting its generations as seeded (`test_a_repeat_preview_announces_its_generations_as_seeded`),
  and an extended cache hit reporting the plan it ran under
  (`test_an_extended_cache_hit_reports_the_current_plans_generations`).
- `tests/test_training_seeding.py` covers training preparation: a modelling node over a chunk-local
  filter parent writes its prepared parquet `input_sliced` across several slices, with rows,
  order and schema equal to the native result, and its metrics report `training_write_strategy`
  and `training_write_input_slices` through the worker path; a run with column exclusions
  composes the drop into the recipe; a run with a row-limit sample takes the native path with
  `training_write_native_reason="row_limit_sample"`; a mismatched recipe degrades to native with
  `training_write_native_reason="recipe_mismatch"` and a recorded warning; and cancellation
  mid-write leaves no prepared parquet.

Tests live under `tests/`, one file per module or per feature slice, using FastAPI's
`TestClient` against a temporary project directory (a `haute.toml` + pipeline `.py` fixture)
for route-level tests, and direct unit tests for the pure-function modules.

- **`test_server.py`** — the broadest integration suite: app lifecycle,
  middleware behaviour, static SPA serving (including the partial-build fail-fast case), the
  `/ws/sync` protocol (resync requests, fingerprint short-circuit, rejection reasons), and
  the file watcher end-to-end (debounce, module-dependency re-parse, config-triggered
  full-reparse, content-identity self-write suppression: an external write to a
  server-written path before the coalesced flush is broadcast, an unchanged self-write
  and a self-deletion are suppressed, an external recreate and an unrelated same-batch
  edit are broadcast with their actual document content).
- **`test_pipeline_recovery.py`** — editor-document DTO separation, degraded/source-only
  recovery, authored-structure conservation, bounded diagnostics, raw-artifact revision safety,
  mutation fences, and server-owned ready-closure preview admission.
- **`test_security_gaps.py`** — local-session token generation/override/disable behaviour,
  HTTP and WebSocket Origin/token rejection, trusted hosts (including bracketed IPv6), and
  malformed authority cases.
- **`test_local_security.py`** — exact bootstrap authority checks, cookie/API/WebSocket success,
  forwarded/absent/mismatched rejection, query-token rejection, and secret-corpus coverage proving
  the SPA, URLs, errors, and rejection surfaces contain no credential.
- **`test_server_concurrency.py`**, **`test_save_lock_contract.py`** — concurrency
  correctness: the shared `save_lock` serialises concurrent saves/submodel operations; the
  WebSocket broadcaster's per-client serialization under concurrent rapid sends.
- **`test_route_helpers.py`** / **`test_route_helpers_contracts.py`** — `SidecarModel`
  defaults, `contained_path` traversal/absolute-path rejection, the pipeline index's
  double-checked-locking and invalidation contract, module-dependency casefold matching.
- **`test_save_precondition_properties.py`** — the generated editing/version-state family
  (ENG-T11): 1..6 generated load/edit/save/external-write operations for two clients are
  driven through the real save and editor-document routes in lockstep with a plain-Python
  model of each client's base revision, the on-disk generation and the accepted saves. A
  save is accepted exactly when the client's base equals the on-disk revision (None only
  while the file does not exist), a rejected save leaves every artifact byte-identical with
  the `409` `stale_document_revision` detail, an accepted save lands its content and reports
  the byte-true token (a return to earlier bytes reports that generation's token; new bytes
  a token never seen), an external edit invalidates every client until it reloads, and the
  last accepted generation is what survives. The `hypothesis.find` negative control patches
  `SavePipelineService._require_base_revision` to a no-op and finds a sequence in which a
  stale save overwrites a newer accepted generation: the regression-sensitivity control for
  the ENG-T02 fix. A creation with a non-null base and a null base against an existing file
  are both rejected without touching disk.
- **`test_route_save_pipeline.py`** / **`test_save_pipeline_integrity.py`** — `SavePipelineService`
  unit and integration tests: singleton/name-collision validation, transactional rollback on
  a mid-save failure, stale-config diff-based cleanup, casefold-collision guards, reserved
  Windows filenames. `TestStaleSavePrecondition` drives the `base_revision` precondition
  against real disk state through the route: stale, fresh, two clients from one base in
  both arrival orders, config-only external edit, creation with and without a token, and a
  vanished target, asserting the `409` detail and byte-exact preservation of the newer
  artifacts. `test_partial_failure.py::TestStaleSaveUnderLock` races two edits from one
  base through the real `save_lock` and proves exactly one wins on disk.
- **`test_route_helpers.py::TestSelfWriteTracking`** — content-identity markers: match,
  mismatch discards the marker, deletion markers, a failed write that never landed, and
  the exactly-one-of-content-or-deleted argument contract.
- **`test_pipeline_route_supersession.py`** / **`test_request_supersession.py`** — supersession
  correctness under rapid repeated requests, including that a superseded waiter never runs
  its worker, cancellation propagates to the execution token, and a timeout carrying a
  background worker remains 504 while retaining its key and execution context to completion.
- **`test_pipeline_route_parity.py`** — shared guard behaviour (runtime input path
  validation, printable-id checks) applied consistently across preview/trace/output-write.
- **`test_trace_api.py`**, **`test_trace.py`**, **`test_trace_multi_frame.py`** —
  `/api/pipeline/trace` route and `execute_trace` coverage, including two fail-loud
  cases `trace_row` translates to HTTP errors rather than silently guessing: a
  duplicate-row relocation match (`_find_target_row_index` raising on an
  ambiguous match → 409 `"Trace row match is ambiguous"`) and a multi-frame
  apiInput correlation walk that must select the same `sourceHandle`-named frame
  per edge the target actually consumes, not the last edge's frame for a
  (source, target) pair (→ 400 `"Target node ... multiple frames"` when it can't
  be resolved).
- **`test_files_routes.py`**, **`test_formats_route.py`**, **`test_utility_routes.py`** —
  route-level coverage of file browsing (including nullable directory size), schema previews
  including XML, the I/O format registry endpoint, and utility-script CRUD (including
  AST-syntax-error rejection with line numbers).
- **`test_output_assemble_routes.py`** — the dry-run route: schema-pre-check ordering,
  volatile-config swap-in behaviour, structured 507 admission mapping, timeout/error-status
  mapping, and admission-context release on success.
- The shared assembler and codec suites (`test_output_assembler.py` and
  `test_v2_codec_and_shred.py`) belong to
  [json-shredding](../json-shredding/low-level.md); this component's route tests verify
  only their HTTP consumption and error mapping.
- **`test_errors.py`** — the `HauteError` hierarchy's `**context` rendering and `repr`/`str`
  behaviour.
- **`test_event_bus_gaps.py`** — targeted edge-branch coverage for `EventBus` (idempotent
  unsubscribe, handler-exception isolation, empty-registry publish no-op).
- **`test_logging.py`** — `configure_logging()` preserves processor-list identity once
  Haute's stdlib bridge is installed (so `capture_logs` and cached bound loggers keep
  working across reconfiguration), but never mutates a pre-existing structlog default or
  third-party processor list. Restoring a prior default configuration must therefore not
  combine `PrintLogger` with Haute's stdlib-only processors.
- **`test_routes_hygiene.py`**, **`test_routes_error_handling.py`** — cross-route regression
  checks (consistent sanitized-error usage, no leaked internal exception text) and explicit
  error-path tests per route.
- **`test_schemas.py`**, **`test_api_contracts.py`**, **`test_backend_frontend_contracts.py`**,
  **`test_ui_contract_golden.py`** — Pydantic model validation edge cases plus
  fingerprint/golden tests that fail loudly if a response schema's shape drifts from what the
  frontend expects.
- **`test_types.py`** — `_types.py` model construction, defaults, validation, and
  cached-property behaviour.

## Canonical API payloads

Under the [canonical-only format policy](../README.md#canonical-only-format-policy),
server routes return and consume only current versioned payload fields. They do not append
temporary historical detail keys, classify earlier config generations, strip old fields, or try
alternate sidecar identifiers. Ordinary current-schema validation and safe error translation remain.

## Pipeline editor recovery implementation contract

- `_pipeline_recovery.py` owns phased discovery, independent node/submodel resolution, topology
  attribution, deterministic blocker propagation, diagnostic caps, and raw-artifact revisions.
- `PipelineEditorDocument` and all recovery element DTOs use exact forbidden-extra schemas that are
  structurally incompatible with `PipelineGraph`/`GraphNode`/`GraphEdge`.
- `RecoveryPreviewRequest` carries `source_file`, `source_revision`, `target_recovery_id`, source and
  selector limits. The route reloads recovery state, compares revisions before planning, rejects an
  untrusted source selection or a non-ready closure with stable structured detail, and sends only a
  freshly built canonical closure into the shared preview execution helper.
- `pipeline_document_update` WebSocket frames have schema version 1 and carry the complete validated
  editor document plus its document fingerprint. Resync compares that fingerprint; watcher ownership
  includes parent/child Python, config JSON, and `.haute.json` sidecars.
- A document-load exception is logged with its stack and becomes a sanitized `parse_error`; that
  frame is reserved for document transport failure, so clients activate the dedicated
  system-failure surface without ever treating ordinary authored recovery as a transport error.
- Diagnostic ordering follows authored discovery order, connection order, submodel registration order,
  then deterministic propagation. At most 200 diagnostics are serialized and `diagnostics_omitted`
  reports the exact remainder.

## Minimal pipeline repair implementation contract

`PipelineRepairRemoveRequest` is a forbidden-extra model carrying the root
document source, raw-artifact revision, target source/recovery identity, and
explicit `delete_config` choice. There is no plan hash: the request applies
against the revision it names. The response contains no executable graph and
no replacement bytes.

`_pipeline_repair.py` locates one unavailable target by server-produced source
identity and recovery id. It rejects blocked/ready nodes, duplicate authored
identity, absent or ambiguous spans, downstream signature consumers, shared
or managed-artifact config deletion, a connection line containing other
authored content, and a chain whose unrelated link would otherwise be removed.
Source edits operate on exact line-bounded bytes and are applied in descending
offsets. AST skeleton spans are decorator-inclusive (from the first matched
decorator line through the last body line), so removing a node never strands
decorator text; syntax-broken source is refused with `repair_syntax_unsupported`.
Position JSON editing preserves unrelated bytes and rejects duplicate
positions/target keys as ambiguous. The display patch in the apply response is
bounded; the server writes the complete untruncated edits. The post-apply
document is authoritative.

Each apply route acquires `save_lock`, reloads the document and computes the
complete plan under the lock; a stale revision is HTTP 409 before staging. Staged writes/deletes and
rollback reuse `_save_pipeline.py`'s touched-file and `Writer` primitives so
each forward and rollback write participates in self-write suppression. After
staging, recovery parsing must conserve the remaining source and the selected
identity must be absent. Strict parse success is recorded by the returned
editor document; an independent authored error may validly remain degraded.
Any verification or write failure rolls back all touched artifacts.

The additional `/api/pipeline/repair/recover/apply` route accepts
`PipelineRepairRecoverRequest`, replacing the removal-only `delete_config` option with
`action: update | reset | recover`. Its responses use `update_node` / `reset_node` /
`recover_node` and otherwise share the bounded repair transport; recover responses add the engine's
`field_changes` outcome report, the target's completeness entries, and `previous_config`.
The full scope and acceptance criteria are defined in
[node recovery actions](node-recovery-actions.md). Updates, resets, and recovers retain
the target; application checks its recovered availability and compares the complete
staged structure against the plan's isolated single-node preview. Python replacements use the shared LibCST
boundary. Ordinary saves validate Data Input/Output structure strictly but tolerate
declared-incomplete locators (`require_complete=False`), so a loadable incomplete node
round-trips through save. They refuse a Scenario Expander without a valid `stepCount` with a
400 naming the node and the setting: the grid size has no incomplete form. The per-node-type
rule is in the [pipeline-config specification](../pipeline-config/low-level.md). `POST /api/pipeline/node/save` provides the node-scoped save
for `scoped_editable` nodes in degraded documents, per the same specification.

## No persistent recovery state

Recovery has no server-side draft store, journal, restore records, or history:
the repair actions and the node-scoped save are the entire mutation surface,
each computed fresh from the authoritative artifacts under the shared save and
project mutation locks. Any `.haute/recovery/` contents left by earlier
releases are ignored without migration; git history is the durability and undo
layer. The cross-process project mutation lock remains shared by every project
writer, and its rendezvous uses a resolved-project-path hash in the user's
temporary directory, so read-only previews and rejected mutations do not create
project files.
