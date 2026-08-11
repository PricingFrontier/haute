# Server API — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/server.py` | App factory (`app = FastAPI(...)`), lifespan (bytecode clear, logging config, env load, marked optimiser-artifact reaping, pipeline-index priming, watcher task lifecycle), middleware registration, router inclusion, `/api/session` health/bootstrap routes, bounded request-ID selection, the API/WS 404 guard, the `/ws/sync` WebSocket endpoint, the debounced file watcher, and credential-free static SPA serving. |
| `src/haute/_local_security.py` | [sandbox-security](../sandbox-security/low-level.md)-owned per-process local-session token, trusted-Origin/Host parsing (including bracketed IPv6), `LocalSessionMiddleware`, `LocalTrustedHostMiddleware`, and the HTTP/WebSocket token-validation contract. |
| `src/haute/schemas.py` | Shared Pydantic request/response models used across the app — re-exports the canonical graph types from `_types.py` and defines per-feature model groups (pipeline save/preview/trace/output write and destination, Explore materialisation and typed pivot matrices/jobs/members, files/schema, Databricks, JSON cache, utility, submodel, modelling, MLflow, optimiser, git, I/O capabilities and execution diagnostics). The OUTPUT dry-run models are the deliberate route-local exception. |
| `src/haute/errors.py` | The `HauteError` hierarchy, including execution, bounded-memory, schema/contract, deployment, and feature errors. Route-visible public subclasses carry stable `error_code` and `public_fields` metadata consumed by `_contract_errors.py`. |
| `src/haute/_validation_error.py` | `HauteValidationError` — the ValueError-derived marker for haute-authored validation messages (re-exported by `errors.py`); the modelling worker boundary keys its curated-message promotion on it. |
| `src/haute/_logging.py` | `configure_logging()` (structlog + stdlib bridge, dev-console vs. JSON-lines modes) and `get_logger()`. |
| `src/haute/_event_bus.py` | `EventBus` — thread-safe synchronous pub/sub with typed `graph.update` / `parse.error` overloads; `default_bus` is the module-level singleton the watcher and server wire together. |
| `src/haute/_types.py` | `NodeType` (`StrEnum`), the decorator↔NodeType maps, every per-node-type config `TypedDict`, the `SolveResultLike` Protocol family, and the canonical `NodeData` / `GraphNode` / `GraphEdge` / `PipelineGraph` Pydantic models (with `PipelineGraph`'s cached-property-invalidating `model_copy` override). |
| `src/haute/_pipeline_revision.py` | [submodels](../submodels/low-level.md)-owned canonical pipeline-document revision over parsed graph state plus parent/referenced-child source and sidecar hashes. |
| `src/haute/routes/__init__.py` | Package docstring only — no code. |
| `src/haute/routes/_helpers.py` | `SidecarModel` (the `.haute.json` on-disk schema); `validate_safe_path`; `pipeline_dir()`; the pipeline-name→path index (`_ensure_pipeline_index`, `invalidate_pipeline_index`, `lookup_pipeline_by_name`) and its module-dependency twin (`_ensure_module_deps`, `pipelines_importing_module`); self-write tracking (`mark_self_write`, `is_self_write`); watcher-pause (`pause_watcher`, `watcher_is_paused`); the WebSocket client registry and `broadcast()`; sidecar load/save (`load_sidecar`, `save_sidecar`); `parse_pipeline_to_graph` (parse + sidecar merge); `commit_pipeline_graph` (read-only historical-commit parse); the shared `save_lock` asyncio.Lock. |
| `src/haute/routes/pipeline.py` | `/api/pipelines`, `/api/pipeline`, `/api/pipeline/{name}`, `/api/pipeline/save`, `/api/pipeline/read-json`, `/api/pipeline/trace`, `/api/pipeline/preview`, `/api/pipeline/write-output`, `/api/pipeline/output-destination` — plus the supersession-key builders, shared output-request preparation, `_prepare_runtime_graph` request containment, runtime-input/output path validators, and memory-limit-to-HTTP-exception translators shared across graph-executing route families. |
| `src/haute/routes/files.py` | `/api/files` (directory browse) and `/api/schema` (flat-file plus XML structured-record schema/preview). |
| `src/haute/routes/io_capabilities.py` | `/api/io-capabilities`, the versioned provider/format/cache capability contract consumed by the input and output editors. |
| `src/haute/routes/input_cache.py` | `/api/input-cache/*`, the shared build/status/cancel/clear lifecycle for snapshot-backed inputs. |
| `src/haute/routes/utility.py` | `/api/utility` CRUD (list/read/create/update/delete) for `utility/*.py` helper modules, with AST syntax validation on every write. |
| `src/haute/routes/_save_pipeline.py` | `SavePipelineService` — the transactional save orchestrator: singleton/name-collision/load-error validation, codegen invocation, config-file + sidecar writes, stale-config cleanup, and rollback. |
| `src/haute/routes/_supersession.py` | `SupersessionCoordinator` / `_SupersessionState` — generation-counted "run latest, cancel/skip the rest" concurrency primitive used by preview and trace. |
| `src/haute/routes/output_assemble.py` | `POST /api/output-assemble/dry-run` — validates an unsaved `outputMapping`, swaps it into the target node's in-memory config, executes up to that node, returns the rendered document. |
| `src/haute/routes/_contract_errors.py` | Shared public-contract-error adapter: validates the closed public error set, emits stable payloads, maps synchronous failures to HTTP 422, and supplies the matching contract-error fields for background jobs. |
| `src/haute/routes/_runtime_path_errors.py` | Closed HTTP mapping for runtime-path failures: malformed path → 400, project-root escape → 403, selected by concrete exception type rather than message text. |

## Key types and data structures

ASSIST-A05 adds the following contracts:

- `SavePipelineService.validate_graph(graph, source_file)` is public and
  side-effect free. It validates Edge Join connected roles, target handles,
  and mutually exclusive/required key forms through the canonical
  `_edge_join.py` validators as well as the other save invariants. `save()`
  calls it before staging, and dry-run calls the same method.
- `AssistantMessageRequest` is a strict request containing only `session_id`
  and `message`; graph-authoring confirmation payloads are rejected as unknown
  fields.
- assistant plan/application responses carry `base_revision`,
  `result_revision`, `capability_hash`, `plan_hash`, semantic diff,
  verification tier/evidence, warnings and ledger reference as applicable.
  Unknown fields remain rejected at typed HTTP boundaries.

**Exception hierarchy** (`errors.py`, abridged to route-relevant branches) — every subclass
roots at `HauteError`, which renders
`**context` kwargs into `str(err)` (`"message (k=v, k2=v2)"`) so structured fields reach log
lines without manual formatting:
```
HauteError
├── ConfigError
├── ParseError
├── ExecutionError
│   ├── PreambleError
│   ├── ContractResolutionError
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
    └── ProjectionImpossibleError (also extends BoundedMemoryUnsupportedError)
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

**`SidecarModel`** (`routes/_helpers.py`) is the typed `.haute.json` schema: `positions:
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
silence the rest. `GraphUpdatePayload` is a closed required-key contract containing
`graph`, `graph_fingerprint`, and `source_file`; `ParseErrorPayload` contains `error` and
`source_file`. They are the two currently-declared typed events; `default_bus` is the
module-level singleton `server.py`'s watcher and WebSocket translator share.

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
| `GET /api/pipelines` | No body | `list[PipelineSummary]`; each item is `{name, description, file, node_count, error}` |
| `GET /api/pipeline` | No body | First parseable `PipelineGraph` with `source_revision`; an empty graph only when no pipeline file exists. If files exist but none parses, the first parse diagnostic is returned as 422. |
| `GET /api/pipeline/{name}` | Pipeline name path parameter | `PipelineGraph` with `source_revision` |
| `POST /api/pipeline/save` | `SavePipelineRequest {name="main", description="", graph={}, preamble=null, preserved_blocks=[], source_file="", sources=["live"], active_source="live"}` | `SavePipelineResponse {status="saved", file, pipeline_name, source_revision, warnings=[], git_sha=null}` |
| `POST /api/pipeline/read-json` | `ReadJsonRequest {path}` | `ReadJsonResponse`, a root JSON object (arrays/scalars are rejected) |
| `POST /api/pipeline/preview` | `PreviewNodeRequest {graph, node_id, row_limit=100 (1..10000), source="live", requested_preview_columns=null (non-empty when present), streaming_chunk_size=null (1..10000000, bool rejected), port_label=null}`; `node_id` is the visible id for a root node and the occurrence-qualified runtime id for a drilled child | `PreviewNodeResponse`, extending `NodeResult` with `node_id`, timings/memory, per-node schemas/statuses, and optional execution metrics |
| `POST /api/pipeline/trace` | `TraceRequest {graph, row_index=0 (>=0), target_node_id=null, column=null, row_limit=100 (1..10000), source="live", row_values=null, streaming_chunk_size=null}`; a non-null `target_node_id` is the visible id for a root node and the occurrence-qualified runtime id for a drilled child | Explicit JSON `TraceResponse {status, trace}`. `trace` includes successful steps, typed omissions, correlation/waterfall evidence, UTC `generated_at`, source identity, and `execution_origin: fresh_execution|preview_cache|trace_cache`; the payload is serialized and `TraceResponse`-validated in the worker, then the returned `JSONResponse` skips a second event-loop validation pass |
| `POST /api/pipeline/write-output` | `WriteOutputRequest {graph, node_id, source="live", streaming_chunk_size=null, overwrite=false}` | `WriteOutputResponse` with status, row count, destination path/table, format, publication outcome, and execution metrics |
| `POST /api/pipeline/output-destination` | `OutputDestinationRequest {graph, node_id}` | Safe destination display path, format, and suffix-mismatch flag; performs no graph execution or filesystem write |
| `GET /api/files` | Query `dir="."`, `extensions=null`; omission derives readable extensions from the I/O registry | `BrowseFilesResponse {dir, items:[{name,path,type,size?}]}`; files have numeric byte size, directories serialize `size: null` |
| `GET /api/io-capabilities` | No body | Versioned provider groups, format capabilities, modes, accepted arguments, optional engines, cache modes, and materialisation diagnostics |
| `GET /api/schema` | Required query `path`; XML uses the structured API-input decoder | `SchemaResponse {path, columns, row_count?, row_count_estimated=false, column_count, preview=[]}`; invalid/unsafe XML is 400 |
| `POST /api/input-cache/build` | Canonical `dataInput` config and source identity | Starts or coalesces a cache-generation build and returns its job identity |
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
the loopback Host authority and the HttpOnly cookie or non-browser token header validates.
Query parameters are not an authentication transport.
The client may send `{"type":"resync","source_file":str,"graph_fingerprint":str|null}`;
plain text, malformed JSON, non-object JSON, and unknown message types are keep-alive no-ops.
The server sends either `{"type":"graph_update","graph":object,
"graph_fingerprint":sha256,"source_file":str}` or
`{"type":"parse_error","error":str,"source_file":str}`. A matching fingerprint produces
no frame. The frame builder rejects an event payload that already contains reserved key
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
`_ensure_pipeline_index()` (builds the name→path index once, under a double-checked lock) →
spawn `_watcher_forever()` and a tracked worker-thread optimiser-reaper task. The lifespan yields
without awaiting filesystem housekeeping, so temp-directory population cannot delay server
readiness. Shutdown cancels and awaits the watcher and observes the reaper task; reaper failures
are logged rather than silently discarded.

**Request middleware chain.** `add_middleware` prepends entries and Starlette later wraps in
reverse, so runtime outer-to-inner order is `LocalTrustedHostMiddleware →
LocalSessionMiddleware → _RequestIdMiddleware → route` in both dev and built-UI modes.
Vite preserves the browser authority while proxying `/api` and `/ws`; no CORS middleware
exposes a second request path around the exact authority checks.
Host/auth failures therefore bypass request-ID binding/logging/header injection. The request-
ID backstop returns `{"detail":"Internal server error"}` with the selected safe request ID on
an escaped exception; route
catch-alls use the different `_INTERNAL_ERROR_DETAIL` string. `LocalSessionMiddleware`
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
3. Consumes self-write markers (`is_self_write(path, consume=True)`) — matched paths are
   skipped and never reach the parse step.
4. Classifies each remaining `.py`/`.json` change: `config/*.json` → re-parse every
   discovered pipeline; `modules/*.py` → re-parse only pipelines importing that module stem
   (case-insensitively, via `_module_dep_key`); any other `.py` (excluding `utility/` and
   dunder-prefixed files) → re-parse that pipeline directly when added/modified. Any direct
   pipeline `.py` addition, modification, or deletion invalidates the pipeline name→path
   index before discovery; a deletion is not reparsed. Module and config changes (including
   deletion) do not discard an otherwise valid pipeline index.
5. For each changed pipeline: hash raw bytes first (cheap) and skip the parse entirely if the
   byte hash is unchanged *and* the change wasn't dependency-triggered (a module/config
   change always re-parses even if this pipeline's own bytes are unchanged, since its
   *effective* graph may differ). On a successful parse, publish `graph.update` with the
   graph payload + a content fingerprint + the wire-form source path; on failure, publish
   `parse.error` and evict the stale fingerprint so the next successful parse re-broadcasts
   even if the bytes happen to match a previous good state.
6. If the flush body itself raises, the *entire* processed batch is requeued and `_flush`
   retries it in the same task at most three times with exponential backoff. Cancellation
   after snapshotting also requeues the batch. On exhaustion, `_flush` removes that batch
   from `pending_changes`, tries each change once in isolation so healthy paths still
   broadcast, and logs/drops each still-failing event. A later event for a dropped path is a
   fresh attempt; no retry task is recursively scheduled.

**Pipeline save (`SavePipelineService.save`)**, run inside the process-wide `save_lock` (an
`asyncio.Lock`, so it serialises against concurrent submodel create/dissolve as well as
concurrent plain saves, but does not coordinate another worker process):
1. Validate singleton node types (at most one `apiInput`/`output`/`liveSwitch`), unique
   sanitized node names (per-graph, then cross-module against every embedded submodel graph),
   and that no node carries a `_load_error` marker.
2. Resolve and validate `source_file` against the active pipeline root.
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
8. Best-effort mirror each JSON/JSONL/NDJSON/XML `apiInput`'s volatile cache to its committed layer.
   Mirror errors are logged and swallowed; mirrors are idempotent and are not recorded in
   `_TouchedFile`, so partial cache state is outside rollback and repaired by a later save.
9. Write the parent `.haute.json` position sidecar. For each child whose
   ownership passed step 3, write positions plus `managed_parent`. All sidecar
   writes are transactional.
10. Stage deletion of any derived submodel source and
    its sibling `.haute.json` sidecar (skipping any that casefold-collide with a path this
    same save just wrote).
11. Reparse the fully staged document and compute the new
    `source_revision`. On any propagated exception in steps 5–11, roll back every staged write (restore
   snapshotted bytes, delete newly-created files) and re-raise unchanged.
12. Only after every write commits: delete stale config files (the diff from step 4, minus
    what this save just wrote or protects), invalidate the pipeline index, and — if the
    project has a recorded git working branch — capture the save in the git ledger
    (`_git.commit_save`); `GitDomainError`/`GitError` become response warnings because the
    on-disk save already succeeded, while an unexpected exception still propagates after
    the filesystem transaction and stale cleanup have committed. Return the
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
asked to cancel cooperatively, not just abandoned. Trace has no corresponding token: its old
route response is superseded, but the newer same-key trace waits on the condition until the
old thread finishes. Both wrap execution in `run_blocking_with_response_timeout`. If that
helper raises a
`BlockingWorkTimeoutError`, `SupersessionCoordinator` extracts its `background_task` and
defers clearing `state.active` and releasing the semaphore until the task finishes. Same-key
work therefore cannot overlap after a 504 and timed-out calls cannot create a worker storm.
An error carrying that background task is re-raised before the post-worker generation check,
so a newer request cannot mask the timeout as 409 or trigger early cleanup.
Preview also cancels its token and defers admission release; trace has no cancellation token,
so its thread runs to completion while retaining the key/permit.

**OUTPUT dry-run** (`routes/output_assemble.py`): validate the mapping shape
(`validate_v2_output_mapping`, data-independent) → flatten the graph → locate and type-check
the target node → validate every runtime input path stays inside the project root → replace
the node's `config` in-memory with the volatile mapping → `execute_graph(...,
target_preview_only=True)` inside an admitted execution context and under a timeout → map the
result's `status`/`error` to the response, or return the rendered `document` on success.
Admission refusal is translated through the shared structured-memory adapter to 507. Normal
success and failure release admission in `finally`; a `BlockingWorkTimeoutError` registers a
background-task callback and transfers release ownership to it, so the context stays live
until the worker actually exits.

## Edge cases and invariants

- **Partial frontend build never serves.** `static_build_ready()` requires both
  `index.html` *and* `assets/` to exist — an interrupted `npm run build` or a hand-created
  directory with only one of the two would otherwise pass a bare `.exists()` check, mount
  `assets/` (raising `RuntimeError` at import if the dir is genuinely missing), or 500 at
  request time reading a missing `index.html`.
- **Windows `.js` MIME type.** The Windows registry commonly maps `.js` to `text/plain`,
  which browsers reject as a script; `mimetypes.add_type` is patched at module import, before
  any `StaticFiles`/`FileResponse` construction.
- **Self-write cooldown vs. per-path tracking.** `is_self_write()` supports two modes: a
  bare cooldown check (`now - _last_self_write < 2.0s`) for callers with no specific path, and
  an exact per-path match (with `consume=True` removing the entry on match) for the file
  watcher's per-event check. Per-path entries are pruned after 60 seconds of retention so a
  crashed or never-consumed marker cannot leak memory indefinitely.
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
  use their explicit immutable `instance_id` and ordinary nodes use their
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
- **v2 apiInput validation guardrails (B1/B2/B3)**: B1 rejects an unknown column `type`
  string at validate time instead of the historical silent downgrade to `str`; B2 rejects two
  table labels whose filesystem-safe sanitisation collides *casefolded* (case-insensitive
  filesystems would otherwise let the second table's parquet clobber the first); B3 is
  structural — every validation failure raises `ApiInputSchemaError` specifically, so the
  JSON-cache route can catch it and return a structured 422 rather than a generic 500.
- **OUTPUT assembly's cut is schema-determined, never data-dependent** (axiom A4 in the
  algorithm's design doc): `_plan_cut` operates purely on which table carries which field, so
  the plan can be computed once at save time and reused at every run without re-deriving it
  from row values. `_prune` treats an empty array/object as "carries no data" and omits it —
  the documented round-trip invariant is equality *up to empty collections*, not bit-exact.
- **`is_active_mapping_entry`** skips an OUTPUT mapping row with a blank `source_column` or
  `output_path` in the shared OUTPUT contract, assembler, and WS-04-owned execution
  consumers — a half-finished editor row never demands `pl.col("")` or produces a confusing
  `missing=['']` failure. Projection planner parity is owned by the execution-engine
  workstream.
- **`GraphEdge` handle validation rejects `""` but accepts `None`** for `sourceHandle` /
  `targetHandle` — the two are semantically distinct (an unnamed port vs. no port specified)
  and coercing one into the other would mask a genuinely invalid empty-string port name.

## Error handling

| Raised as | Route(s) | HTTP status | Notes |
|---|---|---|---|
| `ConfigError` | save, preview, output-assemble dry-run | 400 / embedded `NodeResult.error` / 422 | Save: bad `haute.toml`. Preview: swallowed into the node result so the canvas shows it in-situ. |
| `ContractMismatchError` | trace, preview, output-assemble dry-run | 422 / embedded `NodeResult.error` / 422 | Message already names the node + symmetric column diff. |
| `SchemaMismatchError` | preview | embedded `NodeResult.error` | Adapted identically to `ContractMismatchError`, so a propagated join-key dtype mismatch never becomes a generic 500. |
| `ParseError` | primary pipeline load, preview | 422 / embedded `NodeResult.error` | Primary load returns the first parse diagnostic when files exist but none parses; preview surfaces graph-shape issues per node. |
| `ApiInputSchemaError` | json-cache; preview/write execution | 422 | JSON cache retains its `type` discriminator envelope; execution routes use the public-contract adapter (`api_input_schema_invalid`). |
| `OutputMappingSchemaError` | output-assemble dry-run | 422 | Raised both by the schema-only pre-check and if execution surfaces it deeper (an unmapped port). |
| `ExecutionAdmissionError`, `ExecutionMemoryLimitExceededError` | preview, output write, output-assemble dry-run | 507 | Payload is `exc.to_payload()`, nested under `detail`, for every route. |
| `BoundedMemoryUnsupportedError` | output write | 422 | Distinguishes "cannot stream safely" from a hard resource limit. |
| `DataOutputDestinationExistsError` | `POST /api/pipeline/write-output` | 409 | `overwrite=false` refuses an existing file/table before publication and returns the destination in the detail. |
| `SupersededRequestError` | preview, trace | 409 | Raised by `SupersessionCoordinator`; the worker never runs for a superseded generation. |
| `BlockingWorkTimeoutError`, `TimeoutError` | preview, trace, output write, output-assemble | 504 | Worker threads are never killed. Preview/output write request cooperative cancellation; trace retains its supersession key/permit; OUTPUT dry-run defers context release until its late worker finishes. A background timeout is not masked by supersession. |
| `HTTPException` (raised directly) | path validation, node lookup, syntax checks | 400 / 403 / 404 / 409 | `raise_node_not_found`, `raise_node_type_error`, `raise_pipeline_not_found`, `raise_validation_error` centralise the structured-log + raise pattern. |
| Any other `Exception` | route catch-alls | 500 | Route handlers generally log and return `_INTERNAL_ERROR_DETAIL`; `_RequestIdMiddleware` is a separate backstop whose fixed detail is `Internal server error`. |

The synchronous public-contract adapter maps this closed set to HTTP 422; background jobs
use the same stable codes and named fields under terminal `contract_error`:

| Exception | Stable code | Named fields |
|---|---|---|
| `ApiInputSchemaError` | `api_input_schema_invalid` | — |
| `PreambleError` | `preamble_failed` | `source_line` |
| `ContractResolutionError` | `contract_resolution_failed` | `node_id`, `node_type`, `failure_kind` |
| `ChunkMemoryRiskError` | `chunk_memory_risk` | `target_node_id`, `reason_code`, `estimated_target_row_bytes`, `estimated_minimum_chunk_bytes`, `row_expansion_factor`, `target_chunk_bytes` |
| `GroupByExecutionUnsupportedError` | `group_by_execution_unsupported` | `node_id`, `operator`, `profile`, `reason_code`, `remediation`, `estimated_peak_bytes`, `headroom_bytes` |
| `TraceCorrelationUnsupportedError` | `trace_correlation_unsupported` | `node_id`, `key_columns`, `dtypes`, `reason_code` |
| `RatingExtremaUndefinedError` | `rating_extrema_undefined` | `output_column`, `operation` |
| `RatingFactorMissingError` | `rating_factor_missing` | `table`, `factor` |
| `RatingFactorDtypeContractError` | `rating_factor_dtype_contract` | `table`, `factor`, `saved_dtype`, `input_dtype` |
| `LiveSwitchScenarioError` | `live_switch_scenario_missing` | `switch`, `scenario`, `available_mappings` |
| `OutputNestingKeyError` | `output_nesting_key_null` | `frame`, `output_path`, `key` |

Except for handlers that return a `JSONResponse` directly, `HTTPException` responses use
FastAPI's `{"detail": <string-or-object>}` envelope; this includes structured 507 memory
payloads nested under `detail`. Pydantic request/query validation uses
`{"detail": [validation-error...]}`. `_RequestIdMiddleware` constructs its 500 JSON directly.
The JSON-cache router's `ApiInputSchemaError` is another deliberate direct-response exception:
`{"detail": str, "type": "ApiInputSchemaError"}`. Pipeline-list parse failures surface `str(exc)` as `PipelineSummary.error` only for
`ParseError` (hand-authored, path-safe messages); any other exception is logged
server-side and surfaced as a fixed "Failed to parse pipeline. Check the server logs for
details." message, and the `file` field is always relative to the working directory
(falling back to the file name). Live-sync parse failures surface `str(exc)` as
`parse_error.error`; primary pipeline load uses FastAPI's `{"detail": <parse diagnostic>}`
422 envelope when no discovered file parses. The `ParseError` and live-sync diagnostics
deliberately bypass the internal-error sanitizer.

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

- `tests/test_contract_error_adapter.py` verifies sync/background contract-error payload parity and rejects unversioned errors.
- `tests/test_error_detail_sanitization.py` verifies safe public error details, logging, domain-error exposure, route-specific sanitization, and sensitive-information leak prevention.
- `tests/test_error_response_shape.py` verifies standard error envelopes, flat syntax details, sanitized internal errors, and prohibition of dict route details.
- `tests/test_pipeline_index_cache.py` verifies startup population, cache hits, watcher rebuilds, no manual invalidation, and race-free concurrent reads.
- `tests/test_pipeline_read_json_route.py` verifies object JSON reads plus missing/non-JSON/invalid/non-object/traversal rejection.
- `tests/test_serialization_invariants.py` verifies non-finite values serialize as sentinels in schema-preview and preview responses.

Tests live under `tests/`, one file per module or per feature slice, using FastAPI's
`TestClient` against a temporary project directory (a `haute.toml` + pipeline `.py` fixture)
for route-level tests, and direct unit tests for the pure-function modules.

- **`test_server.py`** (44 test classes) — the broadest integration suite: app lifecycle,
  middleware behaviour, static SPA serving (including the partial-build fail-fast case), the
  `/ws/sync` protocol (resync requests, fingerprint short-circuit, rejection reasons), and
  the file watcher end-to-end (debounce, module-dependency re-parse, config-triggered
  full-reparse, self-write suppression).
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
  defaults, `validate_safe_path` traversal/absolute-path rejection, the pipeline index's
  double-checked-locking and invalidation contract, module-dependency casefold matching.
- **`test_route_save_pipeline.py`** / **`test_save_pipeline_integrity.py`** — `SavePipelineService`
  unit and integration tests: singleton/name-collision validation, transactional rollback on
  a mid-save failure, stale-config diff-based cleanup, casefold-collision guards, reserved
  Windows filenames.
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

## Approved change contract — canonical-only API payloads

Under the [prerelease canonical-only format contract](../README.md#approved-change-contract--prerelease-canonical-only-formats),
server routes return and consume only current versioned payload fields. They do not append
temporary historical detail keys, classify earlier config generations, strip old fields, or try
alternate sidecar identifiers. Ordinary current-schema validation and safe error translation remain.
