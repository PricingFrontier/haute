# Server API — High-Level Specification

## Purpose

Haute's frontend is a React Flow canvas; this component is the FastAPI application that
backs it. It owns the app shell (lifespan, middleware, static SPA serving), the live
code-to-canvas sync channel (file watcher + WebSocket), the request/response contract every
route in the product speaks (Pydantic schemas, the typed error hierarchy, the sanitized-error
convention), and the two largest route families itself: pipeline CRUD/preview/trace/output and
file/schema browsing. Every other route module (Databricks, Explore, MLflow, modelling,
optimiser, submodel, git, JSON cache) is included into the same `FastAPI` app but owned by
its own component; this one is the substrate they all sit on.

It exists so that a pricing analyst editing a pipeline on the canvas gets sub-second preview
feedback, sees their `.py` file edits reflected live without a page reload, and never has a
raw Python traceback land in their browser. Most internal failures are sanitized; parse
diagnostics are deliberately returned/broadcast verbatim and may contain parser-authored
file context.

## Scope

In scope:
- The FastAPI app factory and its lifecycle: `haute.server` — lifespan startup/shutdown,
  middleware stack, static SPA serving, the `/ws/sync` live-sync WebSocket, and the
  filesystem watcher that drives it.
- Local HTTP/WebSocket protection (`haute._local_security`): trusted Host parsing,
  exact local-Origin checks, the HttpOnly-cookie bootstrap, and the per-process
  session token accepted through that cookie. URL/header token transport is unsupported.
- The shared Pydantic contract layer: `haute.schemas` (the cross-route request/response models;
  OUTPUT dry-run keeps two route-local models). JSON-cache and output routes consume the
  v2 input/output schema modules owned by [json-shredding](../json-shredding/high-level.md).
  Explore's shared models include dedicated pivot run/status/cancel and exact-member contracts.
  They distinguish a typed `cache_required` response from started/completed work and expose only
  a closed, versioned typed matrix rather than unvalidated result dictionaries.
- The typed exception hierarchy (`haute.errors`), structured logging setup
  (`haute._logging`), and the in-process pub/sub event bus (`haute._event_bus`) that
  decouples the file watcher from the WebSocket broadcaster.
- The canonical graph types (`haute._types`) that `schemas.py` re-exports at the API boundary.
- Route-layer shared helpers (`haute.routes._helpers`): path-traversal guards, the
  pipeline-name→path index, self-write tracking (watcher feedback-loop prevention), the
  WebSocket client registry and broadcast fan-out, and the on-disk sidecar (`.haute.json`)
  format.
- The pipeline routes (`haute.routes.pipeline`): list/get/save/preview/trace/output-write
  and output-destination preview, their
  request-supersession and concurrency-limiting behaviour, and the transactional save
  service (`haute.routes._save_pipeline`).
- The file-browsing and schema-inspection routes (`haute.routes.files`), the utility-script
  CRUD routes (`haute.routes.utility`), and the OUTPUT-node dry-run HTTP workflow
  (`haute.routes.output_assemble`), which consumes the shared assembler.

Out of scope (owned by neighbouring components, included as routers but not described here):
- Pipeline execution itself — `execute_graph`, `execute_trace`, `write_data_output`, admission
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
- `routes/json_cache.py` — see [caching](../caching/high-level.md); its schema and
  shredding dependencies are owned by [json-shredding](../json-shredding/high-level.md).
- `routes/mlflow.py`, `routes/modelling.py`, `routes/optimiser.py`, and
  `routes/submodel.py` — see
  [mlflow-model-registry](../mlflow-model-registry/high-level.md),
  [modelling](../modelling/high-level.md), [optimiser](../optimiser/high-level.md), and
  [submodels](../submodels/high-level.md), respectively.
- `routes/assistant.py` and the assistant agent loop/providers/tools — see
  [assistant](../assistant/high-level.md). This component defines the `Assistant*` schemas in
  `schemas.py` and provides the save service, event bus, self-write tracking, and
  `save_lock` that component's mutation tools call.
- The preview/execution cache and graph-fingerprint machinery consumed by the pipeline
  routes — see [caching](../caching/high-level.md).
- Everything the browser does with these responses — see
  [frontend-shared](../frontend-shared/high-level.md).
- Generated pipeline `.py` source and the config-file layout `SavePipelineService` writes —
  see [pipeline-config](../pipeline-config/high-level.md) and [codegen](../codegen/high-level.md).
- Path sandboxing primitives (`_get_project_root`) — see
  [sandbox-security](../sandbox-security/high-level.md).
- The shared node-type dispatch registry and column-contract lookup
  (`haute._registry`, `haute._contracts`) — see
  [pipeline-config](../pipeline-config/high-level.md),
  [execution-engine](../execution-engine/high-level.md), and [codegen](../codegen/high-level.md).

## Behaviour

**App lifecycle.** On startup the app clears stale `.pyc` bytecode, configures structured
logging, loads the project's `.env`, reaps only stale ownership-marked children of the two
registered optimiser artifact roots, primes the pipeline-name→path index (so the first HTTP
request doesn't pay for a full filesystem discovery + parse), and starts the background file
watcher as an `asyncio.Task`. Shutdown cancels the watcher task and awaits it. If the built
frontend (`src/haute/static/`) is present and complete (`index.html` **and** `assets/` both
exist — a partial build is treated as absent, not served broken), every unmatched `GET` falls
through to the SPA `index.html`. In dev mode Vite proxies `/api` and `/ws` while preserving
the browser's Host authority, so the backend never opens a separate cross-origin API surface.

**Middleware stack.** Starlette prepends each middleware registration, so runtime
outer-to-inner order is trusted-host validation → local-session-token and Origin validation →
request-ID/timing/exception logging → route in both dev and built-SPA modes. Host/auth
rejections therefore occur before request-ID middleware and have no
`x-request-id` header. `POST /api/session/bootstrap` requires an explicit Origin whose
scheme/loopback authority/effective port exactly match Host, and returns only an HttpOnly,
SameSite=Strict cookie plus `{ok:true}` under no-store headers. Other `/api/*` requests require
that cookie; an absent Origin is allowed only with a valid existing cookie.
`OPTIONS` still requires a trusted Origin but bypasses the token check. WebSocket
handshakes always require an explicit matching Origin plus cookie credential and close
with code 1008 on rejection. `HAUTE_DISABLE_LOCAL_SESSION_AUTH` bypasses only session auth.
An inbound `x-request-id` is retained only when it is a 1–64 character ASCII token matching
`[A-Za-z0-9][A-Za-z0-9._:-]*`; missing or invalid values are replaced with a generated ID.
Rejected values are logged only by bounded reason and numeric length, never by their bytes.

**Live sync.** A pricing analyst can edit a pipeline's `.py` file directly in an IDE while
the canvas is open. A background watcher (debounced 300ms) detects the change, re-parses
only the affected pipeline(s), and publishes a `graph.update` (or `parse.error`) event on an
in-process event bus; a subscriber wired at import time turns that into a WebSocket frame
broadcast to every connected canvas. The canvas can also request a targeted resync over the
same socket by sending `{"type": "resync", "source_file": ..., "graph_fingerprint": ...}`;
the server replies only to that client, and skips the reply entirely if the client's
fingerprint already matches (no redundant payload). Edits the server itself makes (via
`/api/pipeline/save`) are tagged as self-writes so they never round-trip back through the watcher
as a phantom external edit. A change to a `modules/*.py` file re-parses only the pipelines
that import it; a change to a `config/*.json` file re-parses every discovered pipeline (a
config change can affect any pipeline that reads it). Direct pipeline `.py` additions,
modifications, and deletions invalidate the name→path index; module/config-only batches do
not. Startup priming, watcher invalidation, and successful save invalidation are the index's
lifecycle mutation points. The module-dependency index scans under its own build lock and
publishes only when the pipeline-index generation it observed is still current, so
invalidation does not block on the AST scan and an in-flight rebuild cannot overwrite it. A
failed debounced batch is retried at most three times with exponential backoff; after that
the watcher isolates each change once, processes healthy entries, and logs and drops only
the still-failing entries instead of poisoning future batches or creating an unbounded retry
chain.

**Pipeline CRUD, preview, trace, and output publication.** `GET /api/pipelines` lists every
discovered pipeline with `ready`, `degraded`, or `source_only` load status. The editor-facing
`GET /api/pipeline` and `GET /api/pipeline/{name}` routes return a versioned editor document,
not the canonical runtime graph: readable authored failures are HTTP 200 responses carrying
structured diagnostics, element availability, explicit capabilities, and a raw-artifact
revision. The unnamed route selects the first discovered document with authored content
without skipping a broken document in favour of a later healthy one. It returns an empty
ready document only when the project genuinely has no authored pipeline content. Discovery,
permission, transport, and unreadable-source failures remain ordinary HTTP failures.
Recovery nodes deliberately do not have the canonical `GraphNode` wire shape, and recovery
documents are rejected by graph-consuming request models.
`POST /api/pipeline/save` is the single write path for a pipeline's `.py` source, its
per-node config JSON sidecars, and its `.haute.json` position sidecar — described in detail
below. Before changing an existing named document, Save and submodel create/dissolve reread
its current on-disk editor state under the shared save lock and reject a non-ready document
before staging bytes, regardless of the client-posted graph. `POST /api/pipeline/preview`
runs the graph up to one node and returns its schema,
sample rows, and per-node timing/memory; `POST /api/pipeline/trace` follows one row's values
through every node it passed through and returns typed correlation omissions plus generation
provenance; `POST /api/pipeline/write-output` explicitly materialises a
`dataOutput` node, with `overwrite=false` by default. A pre-existing destination returns
409 before graph execution; `POST /api/pipeline/output-destination` runs the same safe
destination resolution without executing the graph or touching the target. Preview and
trace are keyed on (graph fingerprint, source, node,
row/column selectors): a newer request for the *same* key supersedes the older request's
response and waits for its active slot to clear, so same-key workers never overlap. Preview
also requests cooperative cancellation of the active worker; trace has no route-level
cancellation token, so a newer trace must wait for the older trace thread to finish before it
can start. A *different* key runs independently, bounded by a small per-operation concurrency
semaphore. All three long-running endpoints enforce a response timeout
(`HAUTE_{PREVIEW,TRACE,SINK}_TIMEOUT`, default 120s/120s/300s; the historical internal
`SINK` setting governs output-write). Preview and output-write
cooperatively cancel their execution token/context on timeout; trace's already-started thread
finishes in the background. Preview/trace retain their supersession key and concurrency
permit until that thread really finishes, despite having returned 504. Preview and output-write use
memory admission control at this route boundary; trace does not create an admission context
here. A timeout that carries a still-running background task takes precedence over a newer
supersession generation, so it remains a 504 and retains both the key and execution context
until the worker exits.

Editor documents carry `source_revision`, a deterministic digest over raw parent/child
source bytes, every referenced node-config file, and every participating `.haute.json`
sidecar, including role-qualified missing-file sentinels. The digest therefore remains
available when canonical parsing or JSON decoding fails. Explicit Save returns the newly
committed revision. Submodel create and dissolve use the current revision as an optimistic
precondition, return it unchanged, and perform no persistence, so a stale transform cannot
be applied over a newer document.

**File browsing and schema inspection.** `GET /api/files` lists a directory for the file
picker; when its `extensions` query is omitted, the effective readable extensions come from
the installed I/O registry. Directory items omit file size in the backend model and therefore
serialize it as `null`; file items carry their byte size. `GET /api/schema` reads a data file's
column schema, a 5-row preview, and (for parquet) an exact row count or (for JSONL) an estimated
one, without loading the whole file. XML is decoded through the API-input structured-record
normaliser and returns an exact row count; invalid or unsafe XML returns 400.
`GET /api/io-capabilities` exposes provider groups, the Polars I/O
format registry (read/write capability, modes, accepted arguments, missing optional engines),
and cache/materialisation capabilities so the dataInput/dataOutput node editors never
hard-code format knowledge. `/api/input-cache/*` owns shared snapshot build, progress,
cancellation, status, and clear operations for file, database, lakehouse, and Databricks
inputs.

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
the deploy-time render path. Admission refusal uses the same structured 507 payload as the
other execution routes. Ordinary success and failure release the admission context in
`finally`; a 504 defers release to the still-running background task.

**Execution diagnostics and public contract errors.** Execution-consuming endpoints expose
the bounded version-1 strategy diagnostic DTO: canonical, capped collections describe
boundaries, reasons, and provenance without frames, plans, or user data. The shared
public-error adapter is a closed set mapped to synchronous HTTP 422 and background-job
`contract_error`: `ApiInputSchemaError`, `PreambleError`, `ContractResolutionError`,
`ChunkMemoryRiskError`, `GroupByExecutionUnsupportedError`,
`TraceCorrelationUnsupportedError`, `RatingExtremaUndefinedError`,
`RatingFactorMissingError`, `RatingFactorDtypeContractError`,
`LiveSwitchScenarioError`, and `OutputNestingKeyError`. Every payload preserves the
exception's stable code and named safe fields; malformed or unsupported diagnostic versions
become diagnostic-unavailable rather than a fabricated success.

## Design rationale

- **One shared schema module, one shared error hierarchy.** Nearly every route in the product —
  including the ones owned by other components — imports its Pydantic models from
  `schemas.py` and raises through `errors.py`'s `HauteError` family. A single contract module
  means the frontend's TypeScript types (generated from these schemas) can never drift
  between route families, and a single `except HauteError` at any boundary catches the
  entire product's domain-error surface (with the documented exceptions — the OUTPUT
  dry-run request/response models are route-local, and resource-exhaustion
  and deadline errors deliberately extend stdlib bases instead, so existing `except
  MemoryError` / `except TimeoutError` handlers keep working).
- **Sanitized error detail, always.** `_INTERNAL_ERROR_DETAIL` ("Operation failed. Check the
  server logs for details.") is the only text most `except Exception` handlers return to the
  client; the real exception — which can embed absolute filesystem paths, OS error strings,
  or git stderr — is logged server-side with `exc_info=True`. This is deliberate defence
  against information disclosure, not an oversight; contrast with explicitly surfaced
  domain subclasses such as `ConfigError`, `ContractMismatchError`, and
  `SchemaMismatchError`, whose hand-authored messages are safe to return.
  `HauteError` ancestry alone is not a safety marker: plain
  `GitError` can wrap raw stderr and is deliberately sanitized.
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
- **Transactional core, post-commit cleanup.** Generated code, config JSON sidecars, the
  parent position sidecar, managed child position/ownership sidecars, and
  requested submodel source-plus-sidecar deletions form the rollback-covered
  transaction: previous bytes are snapshotted (or a file is recorded as new) before mutation,
  and a failure restores/deletes every touched path best-effort before re-raising. Stale-config
  deletion and git-ledger capture occur only after that transaction succeeds and are not
  rolled back. Typed git failures become response warnings because the filesystem save is
  already durable; an unexpected non-`GitError` still propagates. API-input cache mirroring is
  independently idempotent/best-effort: failures are logged and partial cache state is left
  for a later save to repair, outside `_TouchedFile` rollback.
- **Generated child ownership is durable but never inferred.** A GUI-created
  submodel sidecar records its canonical `managed_parent`. Save preserves that
  marker and child positions only when the existing sidecar already names the
  same parent, or when explicit Save derives a brand-new definition from the
  difference between the persisted and submitted registries and its source and
  sidecar both pass no-clobber. The graph schema contains no request-controlled
  ownership field, and parsing or saving a hand-authored child never creates
  the marker. Creation requires the derived module output to be absent, with a
  case-insensitive preflight returning `409` before any write.
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

## Approved change contract — assistant plan authority

The server exposes the assistant application service inside the running
process; it adds no headless or serverless mutation API. GUI saves and
assistant applies share the same process-wide save lock, transactional
`SavePipelineService`, self-write marking, graph-update broadcast and Git
ledger capture.

`SavePipelineService.validate_graph(...)` is the public, no-write validation
entry point used by both `save(...)` and assistant dry-run. It performs the
same singleton, data-I/O, Edge Join role/key/topology, sanitized-name,
load-error, API-input and path validation that can be decided without staging
files. Edge Join validation uses the canonical backend join validators, not a
save- or assistant-specific approximation. Save invokes it before any write,
so the validation paths cannot drift.

Assistant message requests carry only the session id and user message. Graph
authoring has no browser confirmation object or plan-ready stream event:
application does not execute the graph or materialise a configured output.
The provider invokes a server-owned exact plan hash after dry-run. Plan records
are bounded process state; restart invalidates them safely and the assistant
must dry-run again.

The bounded semantic diff carries complete category counts, an explicit
truncation flag, and a complete semantic-diff digest. Exact post-save
verification compares that digest, so the wire-size bound cannot hide an
unrelated committed change.

Every assistant saved-state response identifies the project revision it
describes. Stale, changed or already-applied plans fail before
`SavePipelineService.save_graph_transactionally(...)` is invoked.

## Interactions

- **[execution-engine](../execution-engine/high-level.md)** — `routes/pipeline.py` and
  `routes/output_assemble.py` call `execute_graph`, `execute_trace`, `write_data_output`, and the
  execution-admission/context APIs directly; the routes consume the shared registry/contract
  dispatch and canonical `_types.py` graph models used by the executor.
- **[background-jobs](../background-jobs/high-level.md)** — job-status response shapes
  (`TrainStatusResponse`, `OptimiserStatusResponse`, `ExploreStatusResponse`,
  `OptimiserFrontierAutoRangeStatusResponse`, `OptimiserFrontierStatusResponse`,
  `DispersionEstimateStatusResponse`) are defined in `schemas.py`; the polling/job-
  runner mechanics live in that component's own routers.
- **[git-integration](../git-integration/high-level.md)** — every `Git*` schema
  (`GitWorkingBranchResponse`, `GitCommitResponse`, `GitGraphResponse`, etc.) is defined in
  `schemas.py` and returned directly by that component's routes; `_save_pipeline.py` calls
  `haute._git.commit_save` to capture each successful save in the clone's ledger; the file
  watcher's `pause_watcher()` / `watcher_is_paused()` contract (`routes/_helpers.py`) lets
  haute-initiated git operations suspend live-sync for their duration.
- **[explore-eda](../explore-eda/high-level.md)** and
  **[databricks-io](../databricks-io/high-level.md)** own routers included into the same app;
  [caching](../caching/high-level.md) owns the included `routes/json_cache.py` router, which
  consumes [json-shredding](../json-shredding/high-level.md)'s schema/shred modules.
- **[caching](../caching/high-level.md)** — `routes/pipeline.py` reads `_preview_cache` and
  `graph_fingerprint` to key supersession and to inject the preview reader `execute_trace`
  needs.
- **[assistant](../assistant/high-level.md)** — owns `routes/assistant.py`, included into the same
  app; its `Assistant*` request/response/SSE-event models live in `schemas.py`; its mutation
  tools run `SavePipelineService` under the shared `save_lock`, mark self-writes, and publish
  `graph.update` on the shared event bus so assistant edits broadcast over `/ws/sync` exactly
  like external edits.
- **[codegen](../codegen/high-level.md)** — `SavePipelineService._write_code` calls
  `graph_to_code` / `graph_to_code_multi` and therefore depends on the shared registry
  between codegen and the executor.
- **[pipeline-config](../pipeline-config/high-level.md)** — `_save_pipeline.py` calls
  `collect_node_configs` / `config_path_for_node` to decide which config JSON sidecars a
  save writes, and owns the on-disk config layout under `<pipeline>/config/`.
- **[sandbox-security](../sandbox-security/high-level.md)** — `_get_project_root()` anchors
  `validate_safe_path` and the `/pipeline/read-json` route.
- **[frontend-shared](../frontend-shared/high-level.md)** — the sole consumer of every
  schema and route this component (and the routers it hosts) exposes; the WebSocket resync
  protocol and browser call to `/api/session/bootstrap` are frontend-facing contracts owned
  here. `_serve_index_html` serves the static shell verbatim and never injects a credential.

## Failure model

This codebase prefers loud failure over silent fallbacks, and the server layer's job is to
turn that loudness into a well-typed HTTP response rather than a raw traceback.

- **Explicitly mapped domain errors** carry a hand-authored message (and, for
  `HauteError`, optional structured `**context`) that the relevant route treats as safe to
  surface: save-time `ConfigError` → 400; output dry-run `ConfigError` /
  `ContractMismatchError` → 422; trace `ContractMismatchError` → 422; and preview
  `ContractMismatchError` / `SchemaMismatchError` failures are embedded in
  `NodeResult.error` so the canvas can show either mismatch in-situ rather than
  as a banner. JSON-cache `ApiInputSchemaError` uses a direct 422 body
  with a `type` discriminator, while preview/write execution uses the stable public-contract
  payload under `detail`; `OutputMappingSchemaError` uses FastAPI's
  `{"detail": <message>}` 422 envelope.
- **Everything else** — any exception not explicitly mapped — is normally caught at the
  route level, logged server-side, and returned as `{"detail": "Operation failed. Check the
  server logs for details."}`. If a route-level handler is bypassed,
  `_RequestIdMiddleware` returns the separately pinned sanitized envelope
  `{"detail": "Internal server error"}`. Neither exposes a traceback; outer trusted-host
  and session middleware rejections bypass request-ID middleware entirely.
- **Resource limits** surface as their own status codes rather than a generic 500:
  `ExecutionAdmissionError` / `ExecutionMemoryLimitExceededError` → 507 for preview,
  output-write, and OUTPUT dry-run; a superseded request →
  `SupersededRequestError` → 409; a timed-out blocking operation → 504. Worker threads are
  never forcibly killed: preview/output-write request cooperative cancellation, while trace and
  OUTPUT dry-run drain the late result/error after returning. A timeout carrying a background
  task is never masked by supersession, and OUTPUT dry-run releases its execution context
  only after that task completes.
- **Path-safety violations** (`validate_safe_path`, the save-time output-path allowlist,
  runtime-input-path validation) return 400 for malformed input (null bytes, empty codegen
  paths, traversal segments) and 403 for a resolved path that escapes its allowed root —
  never a 500, since these are user-input-shaped failures, not internal ones.
- **Save has a transactional write stage**: a propagated failure during code/config/sidecar
  writes or requested module deletion rolls back every tracked target before the exception
  propagates. Best-effort API cache mirroring is deliberately excluded. Stale-config cleanup
  (deleting config files
  the new graph no longer references) is deliberately *not* part of the transaction — it only
  runs after every write has succeeded, because those deletions are non-recoverable; a
  cleanup that never got the chance to run is simply retried on the next successful save.
- **Write serialization is process-local, not optimistic concurrency.** The
  shared `asyncio.Lock` prevents interleaving only inside one server process.
  Routes that accept `base_revision` must compare it under that lock before
  mutation; a mismatch returns `409`. Multiple Uvicorn worker processes remain
  outside the lock contract.
- **The event bus isolates handler failures**: a raising subscriber is logged
  (`event_bus_handler_failed`, with the handler's qualname) and the remaining subscribers
  still receive the event — the file watcher's own operation is never blocked by a
  misbehaving downstream consumer.
- **The file watcher is crash-resilient**: an unexpected exception in the watch loop is
  logged and the loop restarts after a short delay (`_watcher_forever`); a failure inside one
  debounced flush retries the same batch at most three times with exponential backoff. After
  the retry cap it isolates the batch into single-change attempts, processes healthy changes,
  and logs and drops each still-failing event. A later event for that path is eligible again;
  no poisoned batch is retained and no unbounded retry chain is scheduled.
- **WebSocket sends never block the broadcaster indefinitely**: each client send has a hard
  1-second timeout; a stalled client is force-closed and dropped from the client set rather
  than stalling the fan-out to every other connected canvas.

**Missing-key configuration policy.** `routes/_helpers.py::pipeline_dir()`
treats a missing `[project].pipeline` key in `haute.toml` as a soft omission
(warns and falls back to `Path.cwd()`), while malformed or unreadable
configuration raises `ConfigError`. The asymmetry is deliberate: a missing key
can be a fresh-project state, whereas swallowing a decode failure could
silently misroute subsequent saves and loads.

## Pipeline recovery, preview, and live-sync contract

Editor loads are conservation-oriented. Every top-level authored node decorator is discovered before
support is checked; unknown types and duplicate identities remain editor-only recovery elements.
Connection declarations that do not resolve to one unique pair remain typed unresolved structures,
and invalid submodel definitions make only their occurrences unavailable. Diagnostics and downstream
blocking paths are deterministic and bounded. Strict parser, execution, deploy, save, trace, and job
boundaries continue to accept canonical graphs only.

An eligible node in a degraded document is previewed through a server-owned recovery-preview request.
The client supplies source identity, raw-artifact revision, target recovery id, source selection, and
preview limits—not a graph. The server rereads the document, rejects revision drift, proves the full
ancestor closure ready, rebuilds and validates a canonical closure, then delegates to the ordinary
preview execution service. Other execution and persistence capabilities remain fenced.

WebSocket sync publishes versioned `pipeline_document_update` frames for ready, degraded, and
source-only states. Status, capabilities, diagnostics, source identity, and revision are authoritative
even when a dirty client retains its local graph. Sidecar changes are dependency events. A source-only
update may leave a prior canvas visible only as an explicitly stale read-only reference; it is never
treated as the current graph or accepted by save/execution routes. If the editor document itself cannot
be read or built, the server logs the underlying exception and sends a sanitized version-1
`parse_error`; this is distinct from the unversioned strict-parser frame retained for legacy clients.

## Approved change contract — minimal transactional pipeline repair

The only structured repair action is `Remove unavailable node`. It is not a
recovery-graph Save and does not accept source bytes, source spans, replacement
graphs, or migration instructions from the client. Dry-run identifies the
current document by source file and raw-artifact revision, resolves the target
recovery node on the server, and returns a deterministic plan hash, bounded
human-readable patches, the exact touched-artifact manifest, retained config
artifacts, warnings, and predicted recovery state without writing.

Apply takes the same identities, revision, explicit config-deletion choice,
and confirmed plan hash. Under the shared save lock it reloads recovery state,
recomputes the plan, rejects revision or plan drift, then uses the existing
atomic staged-write/rollback machinery. It removes only the selected
decorator/function block, standalone explicit connection declarations that
reference it, and its position entry. A referenced config JSON file is
retained unless it is separately enumerated and explicitly approved. A
shared config, config path overlapping a pipeline source/position artifact,
duplicate authored identity, ambiguous span, mixed connection chain, authored
content sharing a connection's removal line, or downstream function parameter
naming the node rejects the repair without a write. Post-write
recovery/conservation verification must succeed;
strict parsing transitions the document to ready when no independent problem
remains, while an unrelated diagnosed failure may leave it degraded.

No migration registry, `Upgrade node` action, automatic load-time rewrite, or
guessed legacy version exists in this scope. Problems without a safe
remove-only plan continue through raw source/config inspection.
