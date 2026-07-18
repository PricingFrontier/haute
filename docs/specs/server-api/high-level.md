# Server API — High-Level Specification

## Purpose

Haute's frontend is a React Flow canvas; this component is the FastAPI application that
backs it. It owns the app shell (lifespan, middleware, static SPA serving), the live
code-to-canvas sync channel (file watcher + WebSocket), the request/response contract every
route in the product speaks (Pydantic schemas, the typed error hierarchy, the sanitized-error
convention), and the two largest route families itself: pipeline CRUD/preview/trace/sink and
file/schema browsing. Every other route module (Databricks, Explore, MLflow, modelling,
optimiser, submodel, git, JSON cache) is included into the same `FastAPI` app but owned by
its own component; this one is the substrate they all sit on.

It exists so that a pricing analyst editing a pipeline on the canvas gets sub-second preview
feedback, sees their `.py` file edits reflected live without a page reload, and never has a
raw Python traceback or an absolute filesystem path land in their browser.

## Scope

In scope:
- The FastAPI app factory and its lifecycle: `haute.server` — lifespan startup/shutdown,
  middleware stack, static SPA serving, the `/ws/sync` live-sync WebSocket, and the
  filesystem watcher that drives it.
- The shared Pydantic contract layer: `haute.schemas` (every request/response model in the
  product) and `haute._api_input_schema` (the v2 `tables[]` schema-mapping codec for API
  Input nodes — the on-the-wire contract the JSON-cache build/status routes validate
  against).
- The typed exception hierarchy (`haute.errors`), structured logging setup
  (`haute._logging`), and the in-process pub/sub event bus (`haute._event_bus`) that
  decouples the file watcher from the WebSocket broadcaster.
- The node-type dispatch registry (`haute._registry`, `haute._contracts`) and the canonical
  graph types (`haute._types`) — foundational, cross-cutting modules that every parser,
  executor, and codegen call site depends on, bundled here because `schemas.py` re-exports
  the graph types directly and there is no dedicated "core types" component.
- Route-layer shared helpers (`haute.routes._helpers`): path-traversal guards, the
  pipeline-name→path index, self-write tracking (watcher feedback-loop prevention), the
  WebSocket client registry and broadcast fan-out, and the on-disk sidecar (`.haute.json`)
  format.
- The pipeline routes (`haute.routes.pipeline`): list/get/save/preview/trace/sink, their
  request-supersession and concurrency-limiting behaviour, and the transactional save
  service (`haute.routes._save_pipeline`).
- The file-browsing and schema-inspection routes (`haute.routes.files`), the utility-script
  CRUD routes (`haute.routes.utility`), and the OUTPUT-node dry-run assembler
  (`haute.routes.output_assemble`, `haute._output_assembler`).

Out of scope (owned by neighbouring components, included as routers but not described here):
- Pipeline execution itself — `execute_graph`, `execute_trace`, `execute_sink`, admission
  control, memory budgets — see [execution-engine](../execution-engine/high-level.md). This
  component only validates the HTTP boundary and translates execution exceptions to status
  codes.
- Long-running job polling (training, optimiser solves, Explore materialisation, frontier
  auto-range) — see [background-jobs](../background-jobs/high-level.md). The response
  *shapes* for those jobs (`TrainResponse`, `OptimiserStatusResponse`, `ExploreStatusResponse`,
  etc.) live in `schemas.py` because every route needs one shared contract module, but the
  job-runner mechanics are a separate component.
- `routes/git.py` and all git domain logic — see
  [git-integration](../git-integration/high-level.md). This component defines the `Git*`
  Pydantic schemas (git-integration returns them directly) and exposes `pause_watcher()` /
  `watcher_is_paused()` so haute-initiated git operations can suspend the file watcher.
- `routes/explore.py` and Explore materialisation — see
  [explore-eda](../explore-eda/high-level.md).
- `routes/databricks.py` — see [databricks-io](../databricks-io/high-level.md).
- `routes/json_cache.py` and the v2→parquet shredding pipeline that consumes
  `_api_input_schema.py`'s codec — see [json-shredding](../json-shredding/high-level.md).
- `routes/mlflow.py`, `routes/modelling.py`, `routes/optimiser.py`, `routes/submodel.py` —
  separate components not covered by this spec pass.
- The preview/execution cache and graph-fingerprint machinery consumed by the pipeline
  routes — see [caching](../caching/high-level.md).
- Everything the browser does with these responses — see
  [frontend-shared](../frontend-shared/high-level.md).
- Generated pipeline `.py` source and the config-file layout `SavePipelineService` writes —
  see [pipeline-config](../pipeline-config/high-level.md) and [codegen](../codegen/high-level.md).
- Path sandboxing primitives (`_get_project_root`) — see
  [sandbox-security](../sandbox-security/high-level.md).

## Behaviour

**App lifecycle.** On startup the app clears stale `.pyc` bytecode, configures structured
logging, loads the project's `.env`, primes the pipeline-name→path index (so the first HTTP
request doesn't pay for a full filesystem discovery + parse), and starts the background file
watcher as an `asyncio.Task`. Shutdown cancels the watcher task and awaits it. If the built
frontend (`src/haute/static/`) is present and complete (`index.html` **and** `assets/` both
exist — a partial build is treated as absent, not served broken), every unmatched `GET` falls
through to the SPA `index.html`; otherwise CORS is opened for the Vite dev server on
`:5173`.

**Middleware stack** (applied in this order): a request-ID/timing/exception-logging
middleware that also catches any unhandled exception and returns a sanitized `500`, then
local-session-token auth, then a trusted-host check, then (dev mode only) CORS.

**Live sync.** A pricing analyst can edit a pipeline's `.py` file directly in an IDE while
the canvas is open. A background watcher (debounced 300ms) detects the change, re-parses
only the affected pipeline(s), and publishes a `graph.update` (or `parse.error`) event on an
in-process event bus; a subscriber wired at import time turns that into a WebSocket frame
broadcast to every connected canvas. The canvas can also request a targeted resync over the
same socket by sending `{"type": "resync", "source_file": ..., "graph_fingerprint": ...}`;
the server replies only to that client, and skips the reply entirely if the client's
fingerprint already matches (no redundant payload). Edits the server itself makes (via
`/pipeline/save`) are tagged as self-writes so they never round-trip back through the watcher
as a phantom external edit. A change to a `modules/*.py` file re-parses only the pipelines
that import it; a change to a `config/*.json` file re-parses every discovered pipeline (a
config change can affect any pipeline that reads it).

**Pipeline CRUD, preview, trace, sink.** `GET /api/pipelines` lists every discovered pipeline
with parse status; `GET /api/pipeline` / `GET /api/pipeline/{name}` return one graph.
`POST /api/pipeline/save` is the single write path for a pipeline's `.py` source, its
per-node config JSON sidecars, and its `.haute.json` position sidecar — described in detail
below. `POST /api/pipeline/preview` runs the graph up to one node and returns its schema,
sample rows, and per-node timing/memory; `POST /api/pipeline/trace` follows one row's values
through every node it passed through; `POST /api/pipeline/sink` materialises a `dataSink` /
`dataOutput` node to disk. Preview and trace are keyed on (graph fingerprint, source, node,
row/column selectors): a newer request for the *same* key cancels the in-flight one
(supersession) rather than queuing behind it, so a user editing the canvas quickly never
waits on stale work; a *different* key runs independently, bounded by a small
per-operation concurrency semaphore. All three long-running endpoints enforce a response
timeout (`HAUTE_{PREVIEW,TRACE,SINK}_TIMEOUT`, default 120s/120s/300s) and go through memory
admission control before execution begins.

**File browsing and schema inspection.** `GET /api/files` lists a directory (extension-
filtered) for the file picker. `GET /api/schema` reads a data file's column schema, a 5-row
preview, and (for parquet) an exact row count or (for JSONL) an estimated one, without
loading the whole file. `GET /api/schema/databricks` does the same against a table's local
parquet cache. `GET /api/formats` exposes the polars I/O format registry (read/write
capability, accepted arguments, missing optional engines) so the dataInput/dataOutput node
editors never hard-code format knowledge.

**Utility scripts.** `GET/POST/PUT/DELETE /api/utility[/{module}]` manage Python files under
the project's `utility/` directory — reusable helpers a pipeline's preamble imports via
`from utility.<module> import *`. Every write is AST-syntax-checked before landing on disk;
a syntax error is rejected with a line-numbered message, never written half-valid.

**OUTPUT assembly dry-run.** `POST /api/output-assemble/dry-run` lets the OUTPUT node editor
preview the assembled JSON response from an *in-progress, unsaved* field→path mapping: it
validates the mapping shape independently of any data, swaps it into the target node's
in-memory config (never touching disk), runs the graph up to that node, and returns the
rendered document. The assembly algorithm itself (schema-determined cyclic-core detection,
surgical cut, prefix-nested serialisation) lives in `_output_assembler.py` and is shared with
the deploy-time render path.

## Design rationale

- **One shared schema module, one shared error hierarchy.** Every route in the product —
  including the ones owned by other components — imports its Pydantic models from
  `schemas.py` and raises through `errors.py`'s `HauteError` family. A single contract module
  means the frontend's TypeScript types (generated from these schemas) can never drift
  between route families, and a single `except HauteError` at any boundary catches the
  entire product's domain-error surface (with the documented exceptions — resource-exhaustion
  and deadline errors deliberately extend stdlib bases instead, so existing `except
  MemoryError` / `except TimeoutError` handlers keep working).
- **Sanitized error detail, always.** `_INTERNAL_ERROR_DETAIL` ("Operation failed. Check the
  server logs for details.") is the only text most `except Exception` handlers return to the
  client; the real exception — which can embed absolute filesystem paths, OS error strings,
  or git stderr — is logged server-side with `exc_info=True`. This is deliberate defence
  against information disclosure, not an oversight; contrast with `HauteError` subclasses
  (`ConfigError`, `ContractMismatchError`, etc.), whose hand-authored messages are safe to
  surface verbatim because their text never embeds raw system output.
- **Event bus, not a direct watcher→WebSocket call.** The file watcher publishes typed
  events; it has no reference to the WebSocket client set. This keeps the watcher unit-
  testable in isolation and lets other subscribers (metrics, audit, future features) hang off
  the same stream without editing the watcher.
- **Supersession over raw cancellation.** A naive "cancel the previous preview when a new one
  arrives" would race: the previous request's cleanup and the new request's start can
  interleave. `SupersessionCoordinator` serialises this with a generation counter and an
  `asyncio.Condition`, guaranteeing at most one active worker per key and that a superseded
  waiter never starts running — the alternative (semaphore-only limiting) does not, by
  itself, prevent stale results winning a race with fresh ones.
- **Save as an all-or-nothing transaction.** A pipeline save touches multiple files (the
  `.py`, N config JSON sidecars, the position sidecar) plus a git ledger commit. Every write
  is staged (previous bytes snapshotted, or recorded as new-file) before any lands; any
  failure mid-save rolls every touched file back to its prior state and re-raises the
  original error. The alternative — writing files as generated and hoping nothing fails
  partway — would leave a pipeline in a state where the `.py` disagrees with its own config
  sidecars, silently corrupting the next load.
- **Self-write tracking instead of debounce-only.** The file watcher's 300ms debounce alone
  cannot distinguish a server-originated write from a user's IDE edit that happens to land in
  the same window. Every write the server makes is registered by absolute path just before
  the rename; the watcher consumes (and clears) that registration on the matching event. This
  closes the feedback loop precisely, rather than by a timing heuristic that could either miss
  a genuine external edit (window too wide) or re-broadcast the server's own write (window too
  narrow).
- **Path allowlisting at multiple layers.** `validate_safe_path` guards ad-hoc file/schema
  reads; `SavePipelineService._validate_output_rel_path` separately allowlists *codegen
  output* paths (only the declared main file or `modules/<name>.py`, no traversal, no
  Windows-reserved device names, casefold-collision-checked) because codegen output paths
  come from a different trust boundary (generated strings, not direct user path input) and
  need a narrower allowlist than general file browsing.
- **The registry pattern for `NodeType` dispatch.** `_registry.py` centralises the
  `NodeType → (exec builder, codegen builder, column contract)` mapping so the executor and
  codegen can never silently disagree about a node type — a gap here previously fell through
  to a generic passthrough builder undetected. `validate_registry_complete()` runs at import
  time and fails loudly (not lazily, at first dispatch) if any `NodeType` is missing a piece.

## Interactions

- **[execution-engine](../execution-engine/high-level.md)** — `routes/pipeline.py` and
  `routes/output_assemble.py` call `execute_graph`, `execute_trace`, `execute_sink`, and the
  execution-admission/context APIs directly; `_registry.py`/`_contracts.py`/`_types.py`
  define the `NodeType` dispatch table and column contracts the executor consumes.
- **[background-jobs](../background-jobs/high-level.md)** — job-status response shapes
  (`TrainStatusResponse`, `OptimiserStatusResponse`, `ExploreStatusResponse`,
  `OptimiserFrontierAutoRangeStatusResponse`, `OptimiserFrontierStatusResponse`,
  `DispersionEstimateStatusResponse`) are defined in `schemas.py`; the polling/job-
  runner mechanics live in that component's own routers.
- **[git-integration](../git-integration/high-level.md)** — every `Git*` schema
  (`GitStatusResponse`, `GitCommitResponse`, `GitGraphResponse`, etc.) is defined in
  `schemas.py` and returned directly by that component's routes; `_save_pipeline.py` calls
  `haute._git.commit_save` to capture each successful save in the clone's ledger; the file
  watcher's `pause_watcher()` / `watcher_is_paused()` contract (`routes/_helpers.py`) lets
  haute-initiated git operations suspend live-sync for their duration.
- **[explore-eda](../explore-eda/high-level.md)**, **[databricks-io](../databricks-io/high-level.md)**,
  **[json-shredding](../json-shredding/high-level.md)** — each owns a router included into
  the same app (`routes/explore.py`, `routes/databricks.py`, `routes/json_cache.py`);
  json-shredding's build/status routes are the runtime consumer of
  `_api_input_schema.py`'s `validate_v2_schema` / `parse_table_path` / `parse_column_path`.
- **[caching](../caching/high-level.md)** — `routes/pipeline.py` reads `_preview_cache` and
  `graph_fingerprint` to key supersession and to inject the preview reader `execute_trace`
  needs.
- **[codegen](../codegen/high-level.md)** — `SavePipelineService._write_code` calls
  `graph_to_code` / `graph_to_code_multi`; `_registry.py` is the shared dispatch table
  between codegen and the executor.
- **[pipeline-config](../pipeline-config/high-level.md)** — `_save_pipeline.py` calls
  `collect_node_configs` / `config_path_for_node` to decide which config JSON sidecars a
  save writes, and owns the on-disk config layout under `<pipeline>/config/`.
- **[sandbox-security](../sandbox-security/high-level.md)** — `_get_project_root()` anchors
  `validate_safe_path` and the `/pipeline/read-json` route.
- **[frontend-shared](../frontend-shared/high-level.md)** — the sole consumer of every
  schema and route this component (and the routers it hosts) exposes; the WebSocket resync
  protocol and the SPA session-token injection in `_serve_index_html` are frontend-facing
  contracts owned here.

## Failure model

This codebase prefers loud failure over silent fallbacks, and the server layer's job is to
turn that loudness into a well-typed HTTP response rather than a raw traceback.

- **Domain errors** (`HauteError` subclasses) carry a hand-authored message plus structured
  `**context` and are safe to surface verbatim: `ConfigError` → 400, `ContractMismatchError`
  → 422 (trace) or embedded in a `NodeResult.error` (preview, so the canvas can show it
  in-situ rather than as a banner), `ApiInputSchemaError` / `OutputMappingSchemaError` → 422
  with a `type` discriminator in the body so the frontend never string-matches the message.
- **Everything else** — any exception not explicitly mapped — is caught at the route level,
  logged server-side with `exc_info=True`, and returned as a sanitized `500`
  (`_INTERNAL_ERROR_DETAIL`). If a route-level handler is somehow bypassed, the top-level
  `_RequestIdMiddleware` still catches it and returns the same shape, so no request can ever
  return a raw Python traceback to the browser.
- **Resource limits** surface as their own status codes rather than a generic 500:
  `ExecutionAdmissionError` / `ExecutionMemoryLimitExceededError` → 507 (Insufficient
  Storage, repurposed for "would exceed the memory budget"); a superseded request →
  `SupersededRequestError` → 409; a timed-out blocking operation → 504, with the underlying
  execution explicitly cancelled so it does not keep running after the client has given up.
- **Path-safety violations** (`validate_safe_path`, the save-time output-path allowlist,
  runtime-input-path validation) return 400 for malformed input (null bytes, empty codegen
  paths, traversal segments) and 403 for a resolved path that escapes its allowed root —
  never a 500, since these are user-input-shaped failures, not internal ones.
- **Save is transactional**: any failure during `SavePipelineService.save` rolls back every
  file already written in that call before the exception propagates, so a failed save never
  leaves a partially-updated pipeline on disk. Stale-config cleanup (deleting config files
  the new graph no longer references) is deliberately *not* part of the transaction — it only
  runs after every write has succeeded, because those deletions are non-recoverable; a
  cleanup that never got the chance to run is simply retried on the next successful save.
- **The event bus isolates handler failures**: a raising subscriber is logged
  (`event_bus_handler_failed`, with the handler's qualname) and the remaining subscribers
  still receive the event — the file watcher's own operation is never blocked by a
  misbehaving downstream consumer.
- **The file watcher is crash-resilient**: an unexpected exception in the watch loop is
  logged and the loop restarts after a short delay (`_watcher_forever`); a failure inside one
  debounced flush requeues that batch and reschedules rather than dropping the pending
  changes.
- **WebSocket sends never block the broadcaster indefinitely**: each client send has a hard
  1-second timeout; a stalled client is force-closed and dropped from the client set rather
  than stalling the fan-out to every other connected canvas.

> NOTE: `routes/_helpers.py::pipeline_dir()` treats a missing `[project].pipeline` key in
> `haute.toml` as a soft omission (warns, falls back to `Path.cwd()`), but a malformed
> `haute.toml` or an unreadable one raises `ConfigError` rather than falling back — the
> asymmetry is deliberate (a missing key is a fresh-project state; a decode failure would
> silently misroute every subsequent save/load to the wrong directory if swallowed).
