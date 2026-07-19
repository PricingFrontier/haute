# Server API — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/server.py` | App factory (`app = FastAPI(...)`), lifespan (bytecode clear, logging config, env load, pipeline-index priming, watcher task lifecycle), middleware registration, router inclusion, the `/api/session` health probe, the API/WS 404 guard, the `/ws/sync` WebSocket endpoint, the debounced file watcher, and static SPA serving. |
| `src/haute/schemas.py` | Every Pydantic request/response model used by any route in the app (~1750 lines) — re-exports the canonical graph types from `_types.py` and defines per-feature model groups (pipeline save/preview/trace/sink, Explore, files/schema, Databricks, JSON cache, utility, submodel, modelling, MLflow, optimiser, git, I/O format capabilities). |
| `src/haute/_api_input_schema.py` | The v2 `tables[]` schema-mapping codec for API Input nodes: path parsing (`parse_table_path`, `parse_column_path_full`, `parse_column_path`), shape detection (`is_v2_shape`), and full-config validation (`validate_v2_schema`) with the B1/B2/B3 guardrails. |
| `src/haute/errors.py` | The `HauteError` root and its direct subclasses (`ConfigError`, `ParseError`, `ExecutionError` and its `BoundedMemoryUnsupportedError`/`ChunkPlanUnsupportedError` descendants, `DeployError`, `FeatureMismatchError`, `SchemaMismatchError`, `ContractMismatchError`, `ProjectionImpossibleError`). |
| `src/haute/_logging.py` | `configure_logging()` (structlog + stdlib bridge, dev-console vs. JSON-lines modes) and `get_logger()`. |
| `src/haute/_event_bus.py` | `EventBus` — thread-safe synchronous pub/sub with typed `graph.update` / `parse.error` overloads; `default_bus` is the module-level singleton the watcher and server wire together. |
| `src/haute/_registry.py` | `NODE_REGISTRY: dict[NodeType, NodeRegistryEntry]` — the single-source-of-truth exec/codegen/column-contract dispatch table, plus `validate_registry_complete()` and the behavioural-passthrough guard. Foundational for [execution-engine](../execution-engine/high-level.md) and [codegen](../codegen/high-level.md); bundled here for lack of a dedicated core-types component. |
| `src/haute/_contracts.py` | `Contract` dataclass (typed mirror of the `(produced, referenced)` `ColumnContract` tuple) and `get_column_contract()`, the registry-backed lookup executors use for checkpoint projection. |
| `src/haute/_types.py` | `NodeType` (`StrEnum`), the decorator↔NodeType maps, every per-node-type config `TypedDict`, the `SolveResultLike` Protocol family, and the canonical `NodeData` / `GraphNode` / `GraphEdge` / `PipelineGraph` Pydantic models (with `PipelineGraph`'s cached-property-invalidating `model_copy` override). |
| `src/haute/routes/__init__.py` | Package docstring only — no code. |
| `src/haute/routes/_helpers.py` | `SidecarModel` (the `.haute.json` on-disk schema); `validate_safe_path`; `pipeline_dir()`; the pipeline-name→path index (`_ensure_pipeline_index`, `invalidate_pipeline_index`, `lookup_pipeline_by_name`) and its module-dependency twin (`_ensure_module_deps`, `pipelines_importing_module`); self-write tracking (`mark_self_write`, `is_self_write`); watcher-pause (`pause_watcher`, `watcher_is_paused`); the WebSocket client registry and `broadcast()`; sidecar load/save (`load_sidecar`, `save_sidecar`); `parse_pipeline_to_graph` (parse + sidecar merge); `commit_pipeline_graph` (read-only historical-commit parse); the shared `save_lock` asyncio.Lock. |
| `src/haute/routes/pipeline.py` | `/api/pipelines`, `/api/pipeline`, `/api/pipeline/{name}`, `/api/pipeline/save`, `/api/pipeline/read-json`, `/api/pipeline/trace`, `/api/pipeline/preview`, `/api/pipeline/sink` — plus the supersession-key builders, runtime-input/sink-output path validators, and memory-limit-to-HTTP-exception translators shared across those handlers. |
| `src/haute/routes/files.py` | `/api/files` (directory browse), `/api/formats` (polars I/O registry), `/api/schema` (flat-file schema+preview), `/api/schema/databricks` (cached-parquet schema+preview). |
| `src/haute/routes/utility.py` | `/api/utility` CRUD (list/read/create/update/delete) for `utility/*.py` helper modules, with AST syntax validation on every write. |
| `src/haute/routes/_save_pipeline.py` | `SavePipelineService` — the transactional save orchestrator: singleton/name-collision/load-error validation, codegen invocation, config-file + sidecar writes, stale-config cleanup, and rollback. |
| `src/haute/routes/_supersession.py` | `SupersessionCoordinator` / `_SupersessionState` — generation-counted "run latest, cancel/skip the rest" concurrency primitive used by preview and trace. |
| `src/haute/routes/output_assemble.py` | `POST /api/output-assemble/dry-run` — validates an unsaved `outputMapping`, swaps it into the target node's in-memory config, executes up to that node, returns the rendered document. |
| `src/haute/_output_assembler.py` | The OUTPUT assembly algorithm: GYO-reduction cyclic-core detection (`_gyo_residue`), the surgical cut planner (`_plan_cut`, `_Core`, `_CutPlan`), the honoured bag-natural-join executor (`_execute_plan`, `_merge_groups`), prefix-nested serialisation (`_assemble_document`, `_prune`), and the public boundary (`validate_v2_output_mapping`, `assemble_output_from_mapping`, `render_output_document`). |

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
direct `HauteError` subclasses defined next to their raising code, not in `errors.py`. Not
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

**`NodeRegistryEntry`** (`_registry.py`) bundles, per `NodeType`: `exec` (the executor
builder), `codegen` (the codegen builder), `column_contract`, and `is_behavioural` (marks a
stateful-apply type whose codegen body must route through a shared `apply_*_from_config`
helper, never a bare passthrough). `register_exec` / `register_codegen` raise `RuntimeError`
on a duplicate registration for the same `NodeType`; `validate_registry_complete()` — called
once at import time via `ensure_registry_ready()` — asserts every `NodeType` has all three
pieces and that no behavioural type's codegen probe emits a bare `return {param}`.

**`Contract`** (`_contracts.py`) is a frozen dataclass mirror of the tuple-based
`ColumnContract = tuple[set[str] | None, set[str] | None]` (produced, referenced columns;
`None` = opaque). `Contract.from_user_declared` normalises five accepted input shapes
(`Contract`, an object with `.inputs`/`.outputs`, the string `"opaque"`, a
`{"inputs": ..., "outputs": ...}` dict, or a 2-tuple) into one canonical form.
`OPAQUE_CONTRACT = (None, None)` is the explicit "declared opaque" sentinel, distinct from
"forgot to declare" (a `KeyError` from `get_column_contract`).

**`ApiInputV2Config` / `TableV2` / `ColumnV2`** (`_api_input_schema.py`) are `TypedDict`s for
the on-disk `rating/config/<...>.json` shape. A table `path` is a `$[:]`-rooted JSONPath
identifying the array-iteration depth (`PathSeg = tuple[key: str, is_array: bool]`);
`array_depth()` counts the `[:]` hops. Object (1-1) nesting is relationally transparent —
`$[:].a.b.c` and `$[:].p.q` are siblings — only an array-of-objects hop descends a level (the
2026-06-17 inference ruling). `_RESERVED_LEAF = "$value"` addresses a scalar array element as
itself; it is deliberately not a valid identifier so no real JSON key can collide with it.

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

**`_CutPlan` / `_Core`** (`_output_assembler.py`) are frozen dataclasses: `_Core` is one
uncovered cyclic core (`tables`, `parent_keys` — carried by every core table, nest but don't
merge — and `carriers` — carried by some, the genuine cycle obstruction that gets cut).
`_CutPlan` is the full data-independent plan: `cores` in discovery order, `cuts` (the severed
`(table, field)` incidences), and `merge_residue` (the post-cut incidence the honoured bag
join runs over).

## Control flow

**Startup.** `_lifespan()`: `_clear_bytecache()` (rmtree every `__pycache__` under
`src/haute/`) → `configure_logging()` → `_load_env(Path.cwd())` → `_ensure_pipeline_index()`
(builds the name→path index once, under a double-checked lock) → spawn `_watcher_forever()`
as a background task. Shutdown cancels that task and awaits its completion, suppressing
`CancelledError`.

**Request middleware chain** (registration order = outer-to-inner around the handler):
`_RequestIdMiddleware` (binds `request_id` via `structlog.contextvars`, times the request,
catches any unhandled exception and returns a sanitized 500, logs at `error`/`warning`/`info`
by status band) → `LocalSessionMiddleware` → `LocalTrustedHostMiddleware` → (dev mode only)
`CORSMiddleware`.

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
7. Mirror each JSON/JSONL `apiInput`'s volatile cache to its committed layer.
8. Write the `.haute.json` position sidecar (collision warnings for label-sanitisation
   clashes, non-fatal).
9. Stage deletion of any explicitly-requested submodel module files (skipping any that
   casefold-collide with a path this same save just wrote).
10. On any exception in steps 4–9, roll back every staged write (restore snapshotted bytes,
    delete newly-created files) and re-raise unchanged.
11. Only after every write commits: delete stale config files (the diff from step 3, minus
    what this save just wrote or protects), invalidate the pipeline index, and — if the
    project has a recorded git working branch — capture the save in the git ledger
    (`_git.commit_save`); a ledger-capture failure degrades to a response warning, not a
    save failure, since the on-disk save already succeeded.

**Preview / trace supersession**, both routed through the same `SupersessionCoordinator`
pattern (`_preview_supersession`, `_trace_supersession`, each bounded by its own
`asyncio.Semaphore` sized by `HAUTE_{PREVIEW,TRACE}_MAX_CONCURRENCY`, default 2):
build a composite key from `(operation, source_file, source, graph_fingerprint, ...
operation-specific selectors)` → `run_latest()` → on preview, an
`ExecutionCancellationToken` is threaded through so a superseded preview's in-flight work is
actually cancelled, not just abandoned → both wrap the execution call in
`run_blocking_with_response_timeout` (thread-pool execution + a response-level timeout that
cancels the underlying token/context on expiry).

**OUTPUT dry-run** (`routes/output_assemble.py`): validate the mapping shape
(`validate_v2_output_mapping`, data-independent) → flatten the graph → locate and type-check
the target node → validate every runtime input path stays inside the project root → replace
the node's `config` in-memory with the volatile mapping → `execute_graph(...,
target_preview_only=True)` under a timeout → map the result's `status`/`error` to the
response, or return the rendered `document` on success.

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
| `ConfigError` | save, preview | 400 / embedded `NodeResult.error` | Save: bad `haute.toml`. Preview: swallowed into the node result so the canvas shows it in-situ. |
| `ContractMismatchError` | trace, preview | 422 / embedded `NodeResult.error` | Message already names the node + symmetric column diff. |
| `ParseError` | preview | embedded `NodeResult.error` | Graph-shape issue surfaced per-node, not as a request failure. |
| `ApiInputSchemaError` | (json-cache route, not in this component) | 422 with `type` discriminator | Raised by `_api_input_schema.validate_v2_schema`; this component only defines the exception and codec. |
| `OutputMappingSchemaError` | output-assemble dry-run | 422 | Raised both by the schema-only pre-check and if execution surfaces it deeper (an unmapped port). |
| `ExecutionAdmissionError`, `ExecutionMemoryLimitExceededError` | preview, sink | 507 | Payload is `exc.to_payload()`, a structured body, not a plain string. |
| `BoundedMemoryUnsupportedError` | sink | 422 | Distinguishes "cannot stream safely" from a hard resource limit. |
| `SupersededRequestError` | preview, trace | 409 | Raised by `SupersessionCoordinator`; the worker never runs for a superseded generation. |
| `BlockingWorkTimeoutError`, `TimeoutError` | preview, trace, sink, output-assemble | 504 | The associated cancellation token/context is cancelled before the exception is re-raised as HTTP. |
| `HTTPException` (raised directly) | path validation, node lookup, syntax checks | 400 / 403 / 404 / 409 | `raise_node_not_found`, `raise_node_type_error`, `raise_pipeline_not_found`, `raise_validation_error` centralise the structured-log + raise pattern. |
| Any other `Exception` | every route | 500 | Logged with `exc_info=True` / full traceback; response body is always `_INTERNAL_ERROR_DETAIL`. |

Two safety nets exist above individual route handlers: `_RequestIdMiddleware` catches any
exception a route handler failed to catch and returns the same sanitized 500 shape (with a
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
- **`test_output_assembler.py`** (60 tests) — the assembly algorithm itself: GYO-reduction
  correctness, cut-planning on constructed cyclic/acyclic table sets, prefix-nested
  serialisation, the empty-collection prune rule, mapping-schema validation (injectivity,
  prefix-incomparability).
- **`test_output_assemble_routes.py`** — the dry-run route: schema-pre-check ordering,
  volatile-config swap-in behaviour, timeout/error-status mapping.
- **`test_v2_codec_and_shred.py`** (53 tests) / **`test_v1_removal_contract.py`** — exhaustive
  `_api_input_schema.py` coverage: path parsing edge cases (root, nested arrays, object
  transparency, ancestor columns), every B1/B2/B3 guardrail, and a positive assertion that
  the deleted v1 legacy-migration codec stays absent (importing its old symbols raises).
- **`test_errors.py`** — the `HauteError` hierarchy's `**context` rendering and `repr`/`str`
  behaviour.
- **`test_event_bus_gaps.py`** — targeted edge-branch coverage for `EventBus` (idempotent
  unsubscribe, handler-exception isolation, empty-registry publish no-op).
- **`test_registry_contracts.py`** — `NODE_REGISTRY` completeness, duplicate-registration
  rejection, and the behavioural-passthrough-body guard.
- **`test_logging.py`** — `configure_logging()`'s processors-list-identity invariant (so
  `capture_logs` and cached bound loggers keep working across reconfiguration).
- **`test_routes_hygiene.py`**, **`test_routes_error_handling.py`** — cross-route regression
  checks (consistent sanitized-error usage, no leaked internal exception text) and explicit
  error-path tests per route.
- **`test_schemas.py`**, **`test_api_contracts.py`**, **`test_backend_frontend_contracts.py`**,
  **`test_ui_contract_golden.py`** — Pydantic model validation edge cases plus
  fingerprint/golden tests that fail loudly if a response schema's shape drifts from what the
  frontend expects.
- **`test_types.py`**, **`test_column_contracts_adoption.py`** — `_types.py` model
  construction/defaults/cached-property behaviour, and the column-contract adoption
  specification (this suite is explicitly documented as failing until the wider contract
  system is complete — see the file's own docstring).

Known gap: this pass did not locate a dedicated unit-test file for `_registry.py`'s
`_validate_behavioural_bodies_not_passthrough` beyond what `test_registry_contracts.py`
covers at the contract level, nor a file exercising `_contracts.py`'s `Contract.from_user_declared`
normalisation branches in isolation — that coverage may live inside the broader
`test_column_contracts_adoption.py` / codegen test suites rather than a `_contracts`-named
file.
