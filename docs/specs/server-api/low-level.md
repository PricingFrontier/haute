# Server API — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/server.py` | App factory (`app = FastAPI(...)`), lifespan (bytecode clear, logging config, env load, marked optimiser-artifact reaping, pipeline-index priming, watcher task lifecycle), middleware registration, router inclusion, `/api/session` health/bootstrap routes, bounded request-ID selection, the API/WS 404 guard, the `/ws/sync` WebSocket endpoint, the debounced file watcher, and credential-free static SPA serving. |
| `src/haute/_local_security.py` | Per-process local-session token, trusted-Origin/Host parsing (including bracketed IPv6), `LocalSessionMiddleware`, `LocalTrustedHostMiddleware`, and the HTTP/WebSocket token-validation contract. |
| `src/haute/schemas.py` | Shared Pydantic request/response models used across the app — re-exports the canonical graph types from `_types.py` and defines per-feature model groups (pipeline save/preview/trace/sink, Explore, files/schema, Databricks, JSON cache, utility, submodel, modelling, MLflow, optimiser, git, I/O format capabilities). The OUTPUT dry-run models are the deliberate route-local exception. |
| `src/haute/errors.py` | The `HauteError` root and its direct subclasses (`ConfigError`, `ParseError`, `ExecutionError` and its `PreambleError`, `ContractResolutionError`, `BoundedMemoryUnsupportedError`, `ChunkPlanUnsupportedError`, and `ChunkMemoryRiskError` descendants, `DeployError`, `FeatureMismatchError`, `SchemaMismatchError`, `ContractMismatchError`, `ProjectionImpossibleError`). |
| `src/haute/_logging.py` | `configure_logging()` (structlog + stdlib bridge, dev-console vs. JSON-lines modes) and `get_logger()`. |
| `src/haute/_event_bus.py` | `EventBus` — thread-safe synchronous pub/sub with typed `graph.update` / `parse.error` overloads; `default_bus` is the module-level singleton the watcher and server wire together. |
| `src/haute/_types.py` | `NodeType` (`StrEnum`), the decorator↔NodeType maps, every per-node-type config `TypedDict`, the `SolveResultLike` Protocol family, and the canonical `NodeData` / `GraphNode` / `GraphEdge` / `PipelineGraph` Pydantic models (with `PipelineGraph`'s cached-property-invalidating `model_copy` override). |
| `src/haute/routes/__init__.py` | Package docstring only — no code. |
| `src/haute/routes/_helpers.py` | `SidecarModel` (the `.haute.json` on-disk schema); `validate_safe_path`; `pipeline_dir()`; the pipeline-name→path index (`_ensure_pipeline_index`, `invalidate_pipeline_index`, `lookup_pipeline_by_name`) and its module-dependency twin (`_ensure_module_deps`, `pipelines_importing_module`); self-write tracking (`mark_self_write`, `is_self_write`); watcher-pause (`pause_watcher`, `watcher_is_paused`); the WebSocket client registry and `broadcast()`; sidecar load/save (`load_sidecar`, `save_sidecar`); `parse_pipeline_to_graph` (parse + sidecar merge); `commit_pipeline_graph` (read-only historical-commit parse); the shared `save_lock` asyncio.Lock. |
| `src/haute/routes/pipeline.py` | `/api/pipelines`, `/api/pipeline`, `/api/pipeline/{name}`, `/api/pipeline/save`, `/api/pipeline/read-json`, `/api/pipeline/trace`, `/api/pipeline/preview`, `/api/pipeline/write-output` — plus the supersession-key builders, `_prepare_runtime_graph` request-containment helper, runtime-input/output path validators, and memory-limit-to-HTTP-exception translators shared across graph-executing route families. |
| `src/haute/routes/files.py` | `/api/files` (directory browse) and `/api/schema` (flat-file schema+preview). |
| `src/haute/routes/io_capabilities.py` | `/api/io-capabilities`, the versioned provider/format/cache capability contract consumed by the input and output editors. |
| `src/haute/routes/input_cache.py` | `/api/input-cache/*`, the shared build/status/cancel/clear lifecycle for snapshot-backed inputs. |
| `src/haute/routes/utility.py` | `/api/utility` CRUD (list/read/create/update/delete) for `utility/*.py` helper modules, with AST syntax validation on every write. |
| `src/haute/routes/_save_pipeline.py` | `SavePipelineService` — the transactional save orchestrator: singleton/name-collision/load-error validation, codegen invocation, config-file + sidecar writes, stale-config cleanup, and rollback. |
| `src/haute/routes/_supersession.py` | `SupersessionCoordinator` / `_SupersessionState` — generation-counted "run latest, cancel/skip the rest" concurrency primitive used by preview and trace. |
| `src/haute/routes/output_assemble.py` | `POST /api/output-assemble/dry-run` — validates an unsaved `outputMapping`, swaps it into the target node's in-memory config, executes up to that node, returns the rendered document. |
| `src/haute/routes/_contract_errors.py` | Shared public-contract-error adapter: validates the closed public error set, emits stable payloads, maps synchronous failures to HTTP 422, and supplies the matching `contract_error` fields for background jobs. |
| `src/haute/routes/_runtime_path_errors.py` | Closed HTTP mapping for runtime-path failures: malformed path → 400, project-root escape → 403, selected by concrete exception type rather than message text. |

## Key types and data structures

**Exception hierarchy** (`errors.py`) — every subclass roots at `HauteError`, which renders
`**context` kwargs into `str(err)` (`"message (k=v, k2=v2)"`) so structured fields reach log
lines without manual formatting:
```
HauteError
├── ConfigError
├── ParseError
├── ExecutionError
│   └── BoundedMemoryUnsupportedError
│       └── ChunkPlanUnsupportedError
├── DeployError
├── FeatureMismatchError
├── SchemaMismatchError
└── ContractMismatchError
    └── ProjectionImpossibleError (also extends BoundedMemoryUnsupportedError)
```
`_api_input_schema.ApiInputSchemaError` and `_output_assembler.OutputMappingSchemaError` are
direct `HauteError` subclasses supplied by JSON-shredding and consumed by this component's
routes, not defined in `errors.py`. Not
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

**`SidecarModel`** (`routes/_helpers.py`) is the typed `.haute.json` schema: `positions:
dict[str, dict[str, float]]`, `sources: list[str]` (defaults to `["live"]`), `active_source:
str`. A `model_validator(mode="after")` enforces `active_source in sources`. Written via
`model_dump_json(exclude_defaults=True)` so a pipeline that never touched multi-source state
produces a sidecar with only `positions`.

**`EventBus`** (`_event_bus.py`) keys handlers by event-type string in a
`dict[str, list[HandlerType]]` guarded by an `RLock` (reentrant so a handler that
republishes doesn't deadlock). `subscribe()` returns a zero-arg unsubscribe closure;
`publish()` snapshots the handler list under the lock, then calls each handler *outside* the
lock, catching and logging any exception per-handler so one misbehaving subscriber can't
silence the rest. `GraphUpdatePayload` / `ParseErrorPayload` are the two currently-declared
typed events; `default_bus` is the module-level singleton `server.py`'s watcher and WebSocket
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
| `GET /api/pipelines` | No body | `list[PipelineSummary]`; each item is `{name, description, file, node_count, error}` |
| `GET /api/pipeline` | No body | `PipelineGraph` for the first discovered pipeline |
| `GET /api/pipeline/{name}` | Pipeline name path parameter | `PipelineGraph` |
| `POST /api/pipeline/save` | `SavePipelineRequest {name="main", description="", graph={}, preamble=null, preserved_blocks=[], source_file="", sources=["live"], active_source="live"}` | `SavePipelineResponse {status="saved", file, pipeline_name, warnings=[], git_sha=null}` |
| `POST /api/pipeline/read-json` | `ReadJsonRequest {path}` | `ReadJsonResponse`, a root JSON object (arrays/scalars are rejected) |
| `POST /api/pipeline/preview` | `PreviewNodeRequest {graph, node_id, row_limit=100 (1..10000), source="live", requested_preview_columns=null (non-empty when present), streaming_chunk_size=null (1..10000000, bool rejected), port_label=null}` | `PreviewNodeResponse`, extending `NodeResult` with `node_id`, timings/memory, per-node schemas/statuses, and optional execution metrics |
| `POST /api/pipeline/trace` | `TraceRequest {graph, row_index=0 (>=0), target_node_id=null, column=null, row_limit=100 (1..10000), source="live", row_values=null, streaming_chunk_size=null}` | Explicit JSON `TraceResponse {status, trace}`. `trace` includes successful steps, typed omissions, correlation/waterfall evidence, UTC `generated_at`, source identity, and `execution_origin: fresh_execution|preview_cache|trace_cache`; the payload is serialized and `TraceResponse`-validated in the worker, then the returned `JSONResponse` skips a second event-loop validation pass |
| `POST /api/pipeline/write-output` | `WriteOutputRequest {graph, node_id, source="live", streaming_chunk_size=null}` | `WriteOutputResponse` with status, row count, destination path/table, format, publication outcome, and execution metrics |
| `GET /api/files` | Query `dir="."`, `extensions=".parquet,.csv,.json,.xml"` | `BrowseFilesResponse {dir, items:[{name,path,type,size?}]}` |
| `GET /api/io-capabilities` | No body | Versioned provider groups, format capabilities, modes, accepted arguments, optional engines, cache modes, and materialisation diagnostics |
| `GET /api/schema` | Required query `path` | `SchemaResponse {path, columns, row_count?, row_count_estimated=false, column_count, preview=[]}` |
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

**Startup.** `_lifespan()`: `_clear_bytecache()` (rmtree every `__pycache__` under
`src/haute/`) → `configure_logging()` → `_load_env(Path.cwd())` → validate and cache
execution-telemetry configuration → await `reap_stale_optimiser_artifacts()` in a worker thread
(registered roots, ownership markers, and recursive byte sizing never block the event loop) →
`_ensure_pipeline_index()` (builds the name→path index once, under a double-checked lock) →
spawn `_watcher_forever()` as a background task. Shutdown cancels that task and awaits its
completion, suppressing `CancelledError`.

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
an untrusted preflight still receives 403.

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
   dunder-prefixed files) → re-parse that pipeline directly, invalidating the pipeline index
   first.
5. For each changed pipeline: hash raw bytes first (cheap) and skip the parse entirely if the
   byte hash is unchanged *and* the change wasn't dependency-triggered (a module/config
   change always re-parses even if this pipeline's own bytes are unchanged, since its
   *effective* graph may differ). On a successful parse, publish `graph.update` with the
   graph payload + a content fingerprint + the wire-form source path; on failure, publish
   `parse.error` and evict the stale fingerprint so the next successful parse re-broadcasts
   even if the bytes happen to match a previous good state.
6. If the flush body itself raises, the *entire* processed batch is requeued and a fresh
   flush is scheduled — no event is silently dropped by an internal failure.

**Pipeline save (`SavePipelineService.save`)**, run inside the process-wide `save_lock` (an
`asyncio.Lock`, so it serialises against concurrent submodel create/dissolve as well as
concurrent plain saves):
1. Validate singleton node types (at most one `apiInput`/`output`/`liveSwitch`), unique
   sanitized node names (per-graph, then cross-module against every embedded submodel graph),
   and that no node carries a `_load_error` marker.
2. Resolve and validate `source_file` against the active pipeline root.
3. Snapshot the *on-disk* graph's config-file set (`_compute_disk_prev_config_files`) — the
   diff baseline for stale-file cleanup, computed **before** any write in this call.
4. Generate code (`graph_to_code` or, if submodels are present, `graph_to_code_multi`),
   validate every output path against the allowlist (main file exact match, or
   `modules/<name>.py` with no traversal/reserved-device-name/case-collision), and stage each
   write.
5. Emit non-blocking warnings for JSON `apiInput` nodes with no `tables[]` yet.
6. Write per-node config JSON sidecars (collision-checked against protected load-error paths
   and against each other, casefolded).
7. Best-effort mirror each JSON/JSONL `apiInput`'s volatile cache to its committed layer.
   Mirror errors are logged and swallowed; mirrors are idempotent and are not recorded in
   `_TouchedFile`, so partial cache state is outside rollback and repaired by a later save.
8. Write the `.haute.json` position sidecar (collision warnings for label-sanitisation
   clashes, non-fatal).
9. Stage deletion of any explicitly-requested submodel module files (skipping any that
   casefold-collide with a path this same save just wrote).
10. On any propagated exception in steps 4–9, roll back every staged write (restore
    snapshotted bytes, delete newly-created files) and re-raise unchanged.
11. Only after every write commits: delete stale config files (the diff from step 3, minus
    what this save just wrote or protects), invalidate the pipeline index, and — if the
    project has a recorded git working branch — capture the save in the git ledger
    (`_git.commit_save`); `GitDomainError`/`GitError` become response warnings because the
    on-disk save already succeeded, while an unexpected exception still propagates after
    the filesystem transaction and stale cleanup have committed.

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
Preview also cancels its token and defers admission release; trace has no cancellation token,
so its thread runs to completion while retaining the key/permit.

**OUTPUT dry-run** (`routes/output_assemble.py`): validate the mapping shape
(`validate_v2_output_mapping`, data-independent) → flatten the graph → locate and type-check
the target node → validate every runtime input path stays inside the project root → replace
the node's `config` in-memory with the volatile mapping → `execute_graph(...,
target_preview_only=True)` under a timeout → map the result's `status`/`error` to the
response, or return the rendered `document` on success.

> NOTE: unlike preview and sink, the implemented OUTPUT dry-run route never calls
> `context.release_admission()` on success or failure, and its timeout handler also does not
> call `context.cancel()`. It has no supersession coordinator retaining a concurrency permit.
> `run_blocking_with_response_timeout` drains a late future (discarding a successful value and
> logging an ordinary exception), but the route returns 504 while the thread continues. This
> reservation-lifecycle gap is current behaviour, not an intended release/cancellation
> guarantee.

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
- **The pipeline index has exactly two legitimate writers**: server startup and the file
  watcher's `invalidate_pipeline_index()`. `_ensure_pipeline_index()` uses double-checked
  locking so concurrent cold-cache readers never scan the filesystem twice, and the final
  publish is a single dict-reference assignment (atomic in CPython) so no reader ever
  observes a partially-built index.
- **Module-dependency keys are casefolded** (`_module_dep_key`) because the build side
  derives a module stem from a `pipeline.submodel("modules/<name>.py")` *source literal*
  while the watcher derives it from an *on-disk filename* — on case-insensitive filesystems
  (macOS, Windows) those can differ in case and must still match, or live-sync silently goes
  stale for that module.
- **Sidecar position keys reconstruct the parser's node-id scheme**, including a
  backward-compat fallback: legacy sidecars keyed submodel positions by the bare sanitized
  label instead of the parser's `submodel__<name>` id; `parse_pipeline_to_graph` falls back to
  the legacy key on a miss so old sidecars don't snap submodel nodes back to `(0, 0)` on the
  first reload after the id-scheme fix (the next save rewrites the sidecar correctly).
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
  `output_path` everywhere it's consumed (contract, assembly, validation) — a half-finished
  editor row (added but not yet wired to a column) never demands a `pl.col("")` or produces a
  confusing `missing=['']` contract failure.
- **`GraphEdge` handle validation rejects `""` but accepts `None`** for `sourceHandle` /
  `targetHandle` — the two are semantically distinct (an unnamed port vs. no port specified)
  and coercing one into the other would mask a genuinely invalid empty-string port name.

## Error handling

| Raised as | Route(s) | HTTP status | Notes |
|---|---|---|---|
| `ConfigError` | save, preview, output-assemble dry-run | 400 / embedded `NodeResult.error` / 422 | Save: bad `haute.toml`. Preview: swallowed into the node result so the canvas shows it in-situ. |
| `ContractMismatchError` | trace, preview, output-assemble dry-run | 422 / embedded `NodeResult.error` / 422 | Message already names the node + symmetric column diff. |
| `ParseError` | preview | embedded `NodeResult.error` | Graph-shape issue surfaced per-node, not as a request failure. |
| `ApiInputSchemaError` | (json-cache route, owned by caching) | 422 with `type` discriminator | Raised by JSON-shredding's schema codec and mapped by the consuming cache route. |
| `OutputMappingSchemaError` | output-assemble dry-run | 422 | Raised both by the schema-only pre-check and if execution surfaces it deeper (an unmapped port). |
| `ExecutionAdmissionError`, `ExecutionMemoryLimitExceededError` | preview, sink | 507 | Payload is `exc.to_payload()`, a structured body, not a plain string. OUTPUT dry-run maps `ExecutionAdmissionError` to 503 with `str(exc)`. |
| `BoundedMemoryUnsupportedError` | sink | 422 | Distinguishes "cannot stream safely" from a hard resource limit. |
| `SupersededRequestError` | preview, trace | 409 | Raised by `SupersessionCoordinator`; the worker never runs for a superseded generation. |
| `BlockingWorkTimeoutError`, `TimeoutError` | preview, trace, sink, output-assemble | 504 | Worker threads are never killed. Preview/sink request cooperative cancellation; trace retains its supersession key/permit until completion; OUTPUT dry-run only drains the late future. |
| `HTTPException` (raised directly) | path validation, node lookup, syntax checks | 400 / 403 / 404 / 409 | `raise_node_not_found`, `raise_node_type_error`, `raise_pipeline_not_found`, `raise_validation_error` centralise the structured-log + raise pattern. |
| Any other `Exception` | route catch-alls | 500 | Route handlers generally log and return `_INTERNAL_ERROR_DETAIL`; `_RequestIdMiddleware` is a separate backstop whose fixed detail is `Internal server error`. |

Except for handlers that return a `JSONResponse` directly, `HTTPException` responses use
FastAPI's `{"detail": <string-or-object>}` envelope; this includes structured 507 memory
payloads nested under `detail`. Pydantic request/query validation uses
`{"detail": [validation-error...]}`. `_RequestIdMiddleware` constructs its 500 JSON directly.
The JSON-cache router's `ApiInputSchemaError` is another deliberate direct-response exception:
`{"detail": str, "type": "ApiInputSchemaError"}`. Pipeline-list and live-sync parse failures
surface `str(exc)` as `PipelineSummary.error` / `parse_error.error`, rather than passing
through the internal-error sanitizer.

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
  its worker and that cancellation actually propagates to the execution token.
- **`test_pipeline_route_parity.py`** — shared guard behaviour (runtime input path
  validation, printable-id checks) applied consistently across preview/trace/sink.
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
  route-level coverage of file browsing, the I/O format registry endpoint, and utility-script
  CRUD (including AST-syntax-error rejection with line numbers).
- **`test_output_assemble_routes.py`** — the dry-run route: schema-pre-check ordering,
  volatile-config swap-in behaviour, timeout/error-status mapping.
- The shared assembler and v2 codec suites (`test_output_assembler.py`,
  `test_v2_codec_and_shred.py`, and `test_v1_removal_contract.py`) belong to
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

Known gap: `test_output_assemble_routes.py` does not pin admission-context release; the
implemented route currently omits release on every outcome, as noted above.

## Polars backend contracts (0.6.0)

Add one Pydantic execution-strategy diagnostic DTO at the shared API schema boundary. Its
required fields are integer `schema_version` (the producer emits exactly `1`), `status`,
`strategy`, `profile`, `boundedness` (`bounded|unbounded|unknown`), `reason_code`,
`detail_state` (`available|unavailable|truncated`), and `boundaries`, `reasons`, and
`provenance`. Blocking/remediation, cost, metric, and provenance item objects are otherwise
optional; human messages/remediation have at most 512 characters. The DTO rejects plans,
frames, source values, and other user data.

`boundaries`, `reasons`, and `provenance` each use exactly
`{state: available|unavailable|truncated, total_count: int|null, items: [...]}`. Validators
enforce non-negative counts and these invariants:

| `state` | `total_count` | `items` |
|---|---|---|
| `available` | `len(items)` | Complete collection |
| `truncated` | Greater than `len(items)` | Deterministic prefix of the complete collection |
| `unavailable` | `null` | Empty |

Boundary and reason `items` have at most 32 entries; provenance `items` have at most 128. The
producer canonical-sorts the complete collection and only then takes the capped prefix. An
over-cap or internally inconsistent wrapper is invalid input to every consumer and maps to the
typed diagnostic-unavailable result; consumers must not silently re-sort or truncate it.
Top-level `detail_state` is derived as the worst collection state using
`truncated` > `unavailable` > `available`, and a supplied inconsistent value is invalid.

Boundary items require integer `topological_rank` plus string `node_id`, `operator`, and
`boundary_kind`; their primary sort key is
`(topological_rank, node_id, operator, boundary_kind)`. The rank is assigned by a canonical
topological sort whose ready-node queue uses ascending lexical `node_id`. Reason items require
`reason_code` and sort by
`(topological_rank or max, node_id or '', reason_code, operator or '')`. Provenance items
require `column` and `origin_kind` and sort by
`(column, origin_kind, source_node_id or '', source_column or '')`. All string comparison is
ascending Unicode code-point order. For all three collections, the producer appends its Python
canonical-JSON serialization of the item as an internal final sort component so capped prefixes
are deterministic; duplicate items, including identical canonical JSON, are retained. This
server-only component is not a wire-order rule. Consumers validate nondecreasing primary tuples
only and accept any relative order within an equal-primary group; in particular, browser code
must not attempt to reproduce the Python serializer.

Version 1 maps internal strategies to `status` exactly as follows:

| `strategy` | `status` |
|---|---|
| `projected`, `schema-all-except` | `projected` |
| `full-width-admitted-eager` | `admitted_eager` |
| `unprojected-streaming-boundary`, `materialisation-boundary` | `boundary` |
| `unsupported` | `rejected` |
| `not-planned` | `not_planned` |

Readers accept version 1 and ignore unknown additive fields only within version 1. A missing
or malformed required field, unknown version-1 enum value, or unsupported higher schema
version produces the typed diagnostic-unavailable result; it must not be coerced to a known
status. All route adapters consume this DTO and never expose planner internals.

Group-by route execution has only two version-1 outcomes: an admitted
`materialisation-boundary` after both profile permission and RAM admission, or
`GroupByExecutionUnsupportedError(BoundedMemoryUnsupportedError)` before execution. It must
not be labelled as ordinary checked execution or `unprojected-streaming-boundary`. Its public
fields are `node_id`, `operator`, `profile`, `reason_code`, `remediation`,
`estimated_peak_bytes`, and `headroom_bytes`; the two byte estimates are nullable when the
corresponding measurement could not be established.

One shared exception adapter maps each of the following to HTTP 422 for synchronous routes
and `contract_error` for background jobs, preserving its stable code and named fields:

| Exception | Stable code | Named fields |
|---|---|---|
| `PreambleError` | `preamble_failed` | `source_line` |
| `ContractResolutionError` | `contract_resolution_failed` | `node_id`, `node_type`, `failure_kind` |
| `ChunkMemoryRiskError` | `chunk_memory_risk` | `target_node_id`, `reason_code`, `estimated_target_row_bytes`, `target_chunk_bytes` |
| `GroupByExecutionUnsupportedError` | `group_by_execution_unsupported` | `node_id`, `operator`, `profile`, `reason_code`, `remediation`, `estimated_peak_bytes`, `headroom_bytes` |
| `TraceCorrelationUnsupportedError` | `trace_correlation_unsupported` | `node_id`, `key_columns`, `dtypes`, `reason_code` |
| `RatingExtremaUndefinedError` | `rating_extrema_undefined` | `output_column`, `operation` |
| `RatingFactorMissingError` | `rating_factor_missing` | `table`, `factor` |
| `LiveSwitchScenarioError` | `live_switch_scenario_missing` | `switch`, `scenario`, `available_mappings` |
| `OutputNestingKeyError` | `output_nesting_key_null` | `frame`, `output_path`, `key` |

The declared inheritance is
`GroupByExecutionUnsupportedError(BoundedMemoryUnsupportedError)`,
`TraceCorrelationUnsupportedError(ExecutionError)`,
`RatingExtremaUndefinedError(ExecutionError)`,
`RatingFactorMissingError(SchemaMismatchError)`, `LiveSwitchScenarioError(ExecutionError)`,
and `OutputNestingKeyError(OutputMappingSchemaError)`. `TraceCorrelationUnsupportedError`'s
`key_columns` and `dtypes` arrays preserve correlation-key order, correspond positionally, and
are each capped at 16 items. Error-response and background-job tests pin status/reason, code,
and fields for every class. DTO tests pin every schema-version path, wrapper invariant,
producer primary ordering and internal tie-break, consumer acceptance of equal-primary
permutations, cap boundary, over-cap rejection, aggregate `detail_state`, and duplicate
retention. Release 0.6 is an intentional
pre-1.0 fail-loud compatibility change: release and migration notes are required, and no
shim may preserve unsafe silent fallback. The execution-engine spec owns production of the
strategy data. Remaining server API improvement work is tracked in the
[background jobs and API roadmap](../../roadmap/background-jobs-api.md).

## Approved change contract — 0.7.0 data I/O API

Remaining server API improvement work is tracked in the
[background jobs and API roadmap](../../roadmap/background-jobs-api.md).

- `src/haute/routes/files.py` stops serving `/api/formats`; registry capability serving moves to
  a focused `src/haute/routes/io_capabilities.py`. Add the exact versioned Pydantic response and
  corresponding frontend guard.
- Add `src/haute/routes/input_cache.py`, backed by the source-cache store and the existing
  background-job/concurrency primitives. Build requests validate a retained `DataInputConfig`,
  compute its redacted identity, enforce provider/build capability, then return `202` with job
  id. Status/cancel are job-id addressed; snapshot status/clear are identity addressed through a
  validated source descriptor rather than an arbitrary filesystem path.
- In `src/haute/routes/databricks.py`, retain workspace browse endpoints and delete fetch,
  progress, cache-status, and cache-delete endpoints after callers migrate.
- Rename the explicit sink handler in `src/haute/routes/pipeline.py` to
  `/api/pipeline/write-output`; validate exactly `NodeType.DATA_OUTPUT` and dispatch the unified
  executor. Delete the legacy route and dual-type condition.
- `src/haute/schemas.py` adds `IoCapabilitiesResponse`, input-cache job/request/status models, and
  unified output-write request/response models. Stable error codes cover unsupported capability,
  snapshot required/missing/stale/corrupt, build rejected/cancelled, secret-bearing config, and
  unsupported publication. Guards reject unknown schema versions and malformed discriminants.
- Route tests cover response-model exactness, same-key single flight, independent-key
  concurrency, cancel/finish races, timeout ownership, safe errors/redaction, path containment,
  unsaved graph execution, and removed route 404s. OpenAPI/frontend contract fixtures update in
  the same batch.
