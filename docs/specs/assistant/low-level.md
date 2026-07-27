# Assistant — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/assistant/__init__.py` | Public package seam; re-exports only `assistant_readiness`. The FastAPI router remains in the routes package and is not re-exported here. |
| `src/haute/assistant/_config.py` | Resolves assistant configuration: the `[assistant]` table from `haute.toml` (tomllib, same parsing discipline as `routes/_helpers.pipeline_dir()` — malformed TOML raises `ConfigError`, an absent table is a legitimate not-configured state), API keys from `os.getenv` (the server process must inherit them; `haute serve` does not load project `.env`), and an SDK-import probe. Produces `AssistantConfig` (ready) or `AssistantReadiness` with a reason (not ready). It follows the same fail-loud credential posture as Databricks I/O. |
| `src/haute/assistant/_catalog.py` | The node-type catalog the model reads: mechanical facts derived from `haute._types` (`NodeType`, `NODE_TYPE_TO_DECORATOR`), `haute._config_validation` (`VALID_KEYS`, config TypedDict shapes), `haute._config_io` (sidecar folders), and the save service (singleton policy), plus hand-authored per-type usage notes. `validate_catalog_complete()` runs at import time and raises if any canonical fact or node entry drifts; `render_catalog()` is the sole static prompt renderer. |
| `src/haute/assistant/_assets.py` | Loader for the assistant's packaged knowledge assets (read via `importlib.resources`): resource enumeration, `authoring_guide()`, and `example_index()` are cached; `load_example(name)` materialises the complete example tree (source plus parser-relative sidecars), then reparses and renders the exemplar on demand through `routes/_helpers.parse_pipeline_to_graph` and the same formatter used by the get-pipeline tool. The guide fails loudly if missing/empty, index summaries come from the first module-docstring line, and an unknown name is a structured tool error listing the valid names. |
| `src/haute/assistant/assets/authoring_guide.md` | Packaged, hand-authored Haute idiom: canonical pipeline shapes, naming and stage-chaining conventions, and do/don't guidance injected into every system prompt. |
| `src/haute/assistant/assets/examples/branched_features.py` | Packaged exemplar with parallel feature branches joined before the output stage; its module docstring supplies narrative notes and the index summary. |
| `src/haute/assistant/assets/examples/joined_reference.py` | Packaged exemplar showing a reference-data join; parsed as data by `_assets.py`, never imported as a module. |
| `src/haute/assistant/assets/examples/linear_pricing.py` | Packaged minimal linear-pricing exemplar; parsed through the real pipeline parser and rendered in the same compact graph shape as the get-pipeline tool. |
| `src/haute/assistant/assets/examples/config/data_input/quotes.json`, `src/haute/assistant/assets/examples/config/data_input/regions.json` | Packaged parser-relative file-input sidecars used by the linear and joined exemplars; source decorators load them through the same generated-code helper as user pipelines. |
| `src/haute/assistant/assets/examples/config/quote_input/quote.json` | Packaged API-input schema for the branched exemplar, including its emitted `quote` port. |
| `src/haute/assistant/assets/examples/config/quote_response/joined_priced.json`, `src/haute/assistant/assets/examples/config/quote_response/linear_priced.json`, `src/haute/assistant/assets/examples/config/quote_response/response.json` | Packaged response-output sidecars; each carries a concrete non-empty `outputMapping`. |
| `src/haute/assistant/_ops.py` | The graph-edit operation model and its pure application engine: add-node, update-node, rename-node, delete-node, add-edge, delete-edge, and update-preamble operations; validation (unknown targets, unknown config keys, submodel-internal targets, ambiguous edge matches); ordered application over a `PipelineGraph` copy; and deterministic position assignment for new nodes. No I/O — unit-testable graph→graph functions. |
| `src/haute/assistant/_tools.py` | The tool registry: JSON-schema definitions and dispatch for every read and mutation tool. Dataset listing and schema preview share the files route's installed-input-extension registry and reject hidden components plus denylisted state/credential names before any directory enumeration or read. Other read tools wrap saved-graph parsing, `_assets.load_example`, and production lazy execution for `get_node_schema`. `apply_graph_edits` itself owns mutation readiness, parse → ordered ops → transactional save under `save_lock` → re-parse → `graph.update` publish. Every tool returns a structured result-or-error payload instead of raising into the loop. |
| `src/haute/assistant/_session.py` | Session store: `AssistantSession` records (id, bound pipeline `source_file`, provider-neutral message history including tool-result `is_error`, per-session `asyncio.Lock`, timestamps), create/lookup, the provider-request history window, and the bounded-retention rules — an LRU cap on live sessions and a per-session stored-history cap (constants below). Memory is the runtime authority; when a `storage_dir` factory is supplied (the route wires `.haute/assistant/sessions/` under the project cwd), sessions write through to one JSON file per session (atomic tmp+`os.replace`) on create and on every committed turn, and `lookup` revives an unknown id from its file (fresh lock; history revalidated through the same JSON-boundary validators). Unreadable/corrupt/invalid files emit the assistant-session-unreadable warning and are treated as absent, mirroring the Git-state posture for `.haute/` files. Persist failures emit the assistant-session-persist-failed warning without failing the committed turn — the in-memory session stays intact and the reply was already delivered. Creation prunes abandoned UUID-shaped `.json.tmp` artifacts before applying the persisted-session cap. |
| `src/haute/assistant/_providers.py` | The `AssistantProvider` protocol and its two adapters: `AnthropicProvider` (`anthropic` SDK, Messages streaming API) and `OpenAIProvider` (`openai` SDK, Chat Completions streaming — the OpenAI-compatible protocol Databricks serving endpoints implement — honouring the configured base URL). SDKs are core dependencies but imported lazily inside the adapters (importing Haute never triggers provider-side behaviour; a broken install surfaces as a readiness reason); each adapter normalises its SDK's stream into the internal `ProviderEvent`s (see Control flow § Provider adapters for the exact call and event mappings) and maps SDK failures to `AssistantProviderError`. |
| `src/haute/assistant/_loop.py` | Provider-neutral agent loop as an async generator of typed stream events: assembles prompt/history/tool inputs, forwards text deltas, invokes the injected tool executor, feeds structured results into later provider rounds, shields an in-flight tool from cancellation, enforces tool/time limits, commits turn history, and closes every provider stream. It does not implement graph edits itself. |
| `src/haute/routes/assistant.py` | The FastAPI router: `GET /api/assistant/status`, `POST /api/assistant/session`, `POST /api/assistant/message` (an SSE `StreamingResponse` wrapping `_loop`'s generator). Route-level exception translation follows the product conventions (typed `HauteError`s surfaced, everything else sanitized). Swept by the existing `tests/test_routes_hygiene.py` contracts like every `routes/` module. |
| `src/haute/schemas.py` | The assistant slice of the server-api-owned shared HTTP/SSE contracts: status, session request/response and transcript entries, message request, usage, and the text-delta, tool-started, tool-finished, graph-updated, completed, failed, and cancelled event union mirrored by `frontend/src/api/assistant.ts`. |
| `src/haute/server.py` | Includes the assistant router with the other feature routers ahead of the API/WebSocket 404 catch-alls and supplies graph-update fingerprint/wire-path helpers used by mutation publishing. |
| `src/haute/routes/_save_pipeline.py` | Transactional save service used by assistant mutations; its `save_graph_transactionally` wrapper explicitly forwards the parsed graph's preserved blocks into `SavePipelineRequest` and owns rollback, self-write marking, and ledger-capture warnings. |
| `pyproject.toml` | Declares `anthropic>=0.40` and `openai>=1.55` as core dependencies and omits `src/haute/assistant/assets/*` from import-coverage measurement because exemplar `.py` files are parsed package data, while ruff and parser tests still check them. |

Environment knobs: `HAUTE_ASSISTANT_TURN_TIMEOUT` (seconds, default 300) and
`HAUTE_ASSISTANT_MAX_TOOL_CALLS` (default 20) read lazily via `haute._env`, matching the
existing pattern. `HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS` (the per-provider-call output budget
both adapters pass through, threaded via `AssistantConfig`) is deliberately stricter than
`haute._env`'s lenient semantics: unset → 8192; a set-but-malformed or non-positive value
is a **readiness error**, not a warn-and-default — a silently substituted cost ceiling is
precisely the wrong-fallback class the project forbids.
Retention constants in `_session.py`, not env knobs: the provider request carries the most
recent **complete turns** fitting a 40-message budget; stored history is capped at 200
messages by evicting whole oldest turns; live-session LRU cap 32 (least-recently-used
*idle* session evicted on create beyond the cap — dropping only the in-memory record, the
persisted file revives it on next lookup; a session with a running turn is never
evicted); persisted session files cap at 100 (`MAX_PERSISTED_SESSIONS`), pruning the
oldest by session-file modification time at session creation after removing abandoned
atomic-write temp files. Pruning always cuts at turn boundaries — an
assistant tool call and its result are never separated (both provider APIs reject
orphaned halves).

## Key types and data structures

- **`AssistantConfig`** (frozen dataclass, `src/haute/assistant/_config.py`): `provider: Literal["anthropic", "openai"]`,
  `model: str`, `base_url: str | None` (OpenAI adapter only; rejected for anthropic),
  `api_key: str`, `max_output_tokens: int` (from `HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS`,
  default 8192 when unset; a set-but-malformed or non-positive value fails readiness with a
  named reason rather than silently substituting the default). Only ever constructed fully
  valid.
- **`AssistantReadiness`** (`src/haute/assistant/_config.py` → `AssistantStatusResponse`): `configured: bool`,
  `reason: str | None` (exactly one of: no `[assistant]` table, unknown provider, missing
  model, missing API key env var — named — the provider SDK missing from the installation
  (a broken install: the SDKs are core dependencies), or an invalid
  `HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS` value — malformed or non-positive, named),
  `provider`/`model` echoes, plus
  `mutations_enabled: bool` / `mutations_reason: str | None` — driven by
  `haute._git.working_branch_status(...)`, the same readiness the GUI's Save gate requires.
  The state→reason mapping is owned in `src/haute/assistant/_config.py` because not every non-ready state
  carries its own message: `"ready"` → enabled, reason `None`; `"no-repository"` → fixed
  message directing the analyst to initialise Git; `"unset"` → fixed message directing the
  analyst to create/select a working branch in the Git panel; `"detached"` → fixed message
  directing them to attach HEAD in the Git panel; `"divergent"` → fixed message directing
  them to resolve divergence in the Git panel; `"invalid"` → the response's `errors` list
  joined verbatim (the one state that carries git-layer text). `working_branch_status` is
  total for those six repository/readiness states. An unexpected git-domain `HauteError`
  raised while computing readiness likewise maps to disabled with that error's message as
  the reason — the assistant status endpoint always renders readiness; an infrastructure
  failure is a reason, never an HTTP error.
- **`ProviderEvent`** (internal union, `_providers.py`): `TextDelta(text)`,
  `ToolCallRequest(id, name, arguments)` — emitted only once a call's streamed argument
  fragments have been fully accumulated and JSON-parsed; several calls in one provider turn
  are emitted in stream order — and `TurnStop(reason: "end" | "tool_use", usage)`.
  Adapters translate SDK streams into exactly these; the loop never sees SDK types.
- **`AssistantProviderError(HauteError)`** (`_providers.py`): hand-authored message carrying
  provider name and a classified failure such as `authentication`, `rate_limit`,
  `connection`, `status`, `stream`, `dependency`, `malformed_stream`, `truncated`, or
  `filtered` — never the raw provider response body.
- **`GraphEditOp`** (discriminated union, `_ops.py`), addressing nodes by id (the function
  name shown by `get_pipeline`) or — within one batch — by `$<ref>`, the batch-local handle
  a preceding `add_node` declared. Refs are resolved server-side to the real sanitised node
  ids as each `add_node` applies, so the model never has to predict name sanitisation or
  collision outcomes:
  - `add_node {node_type, name, config?, ref?}` — `submodel`/`submodelPort` types rejected;
    `ref` (optional) names the batch-local handle later ops may use wherever a node id is
    accepted; positions are assigned by the deterministic rule below *after* the whole batch
    has applied, so parent-based placement sees the batch's final wiring.
  - `update_node {node, config}` — shallow key merge into the existing config; an explicit
    JSON `null` value removes that key. Unknown keys for the node's type are rejected using
    the same `TypedDict`-derived allowlist machinery the sidecar writer uses (see Edge
    cases for why this is deliberately stricter than save's warn-and-drop).
  - `rename_node {node, new_name}` · `delete_node {node}` (drops every touching edge,
    mirroring the GUI's atomic delete) · `add_edge {source, target, source_handle?,
    target_handle?}` · `delete_edge {source, target, source_handle?, target_handle?}`
    (matched on endpoints + handles; an ambiguous match is an error, never a guess) ·
    `update_preamble {preamble}` (full replacement).
- **`AssistantSession`** (`_session.py`): `id` (uuid4 hex), `source_file`, `history` — a list
  of **turn records**, each grouping one user message with every assistant message, tool
  call, and tool result it produced (the atomic unit all pruning operates on) — one
  `asyncio.Lock` (the one-turn-at-a-time guard), `created_at`/`last_used`.
- **SSE wire events** (`schemas.py`): the `AssistantStreamEvent` union listed in the module
  map — field-for-field the contract documented in
  [frontend-assistant-ui](../frontend-assistant-ui/low-level.md) Key types.

## Control flow

**Status** (`GET /api/assistant/status`): `_config.assistant_readiness()` — read `haute.toml`
(malformed → `ConfigError` → 400), check provider/model fields, probe the SDK import for
the configured provider, check the key env var. Pure inspection, no provider network call.

**Session create** (`POST /api/assistant/session`): resolve the pipeline (explicit name via
`lookup_pipeline_by_name`, else the same first-pipeline default `GET /api/pipeline` uses);
unknown name → 404. When the request carries a prior `session_id` and that session revives
(memory or disk) **and** is bound to the same resolved source file, return it unchanged
with `history`: the stored turns mapped to transcript entries (`user`/`assistant` text
entries, and `tool` entries carrying the tool name, the same compact result summary the
live stream uses, and the error flag) so the panel rehydrates the conversation. Any other
case — no `session_id`, unknown/pruned/corrupt, or a different pipeline — creates and
returns a fresh session with empty `history`; resume is an offer, never an error.

**Message turn** (`POST /api/assistant/message` → SSE stream from `_loop.run_turn`):

1. Readiness is checked before session lookup (400 before the stream opens if
   unconfigured). This ordering means an unconfigured request reports the configuration
   problem even when its session id is also unknown.
2. Look up the session (404) and atomically acquire its lock without waiting — held →
   409. The reservation happens before provider construction or pipeline parsing; either
   pre-stream failure releases it immediately, while a started turn releases it from the
   loop/response lifecycle and appends the turn to history.
3. Resolve the provider configuration, construct the adapter, parse the session's saved
   pipeline, and build the provider request: system prompt (static role instructions + the `_catalog`
   rendering + the `_assets` authoring guide + the exemplar index (name and one-line
   summary per packaged exemplar) + project facts: pipeline name, source file,
   node-count/type summary) + windowed history + the new user message + `_tools` JSON
   schemas. Fresh graph detail is deliberately *not* embedded in the system prompt — the
   model fetches it via tools, so it is never stale mid-turn. Full exemplar bodies are
   likewise prompt-excluded: the model pulls them through `get_example` only when relevant.
4. Stream provider events. `TextDelta` → emit `text_delta`. `ToolCallRequest` → emit
   `tool_started`; execute; append the result to the pending provider messages before
   emitting `tool_finished` (+`graph_updated` for successful mutations); on `TurnStop("tool_use")`
   re-invoke the provider with the accumulated results; on `TurnStop("end")` emit
   `completed` with usage and finish. If the response closes while suspended at
   `tool_started`, the round commit filters the unmatched call; closing at either later
   event retains the already-recorded result. Thus every persisted call id has exactly one
   matching result id on every generator-close boundary.
5. Tool execution: read tools run via `asyncio.to_thread`. `get_node_schema` parses the
   saved pipeline, then proceeds in this order:
   1. **Validate the target id against the original hierarchical graph** — a submodel
      placeholder, or an id found only inside a submodel's nested graph → structured error
      naming the v1 submodel boundary (the same classification the ops engine applies to
      submodel-internal targets); an id found nowhere → unknown-node error; only an
      original top-level executable node proceeds. Validating after flattening would be
      wrong twice over: a submodel-internal child id becomes executable once inlined (a
      boundary bypass), and an unknown id would be indistinguishable from a dissolved
      placeholder.
   2. **Reproduce the production execution callers' graph preparation** — the
      `_explore_service._materialise_and_summarise` sequence, never a assistant-local
      variant: `flat = flatten_graph(graph)` (submodels inlined, as every
      run/preview/optimise caller does first); `preamble_ns = _compile_preamble(
      graph.preamble or "", pipeline_dir=_pipeline_dir(graph))`; then
      `lazy_outputs, *_ = execute_lazy_graph(flat, _build_node_fn, target_node_id=node,
      preserve_node_ids={node}, preamble_ns=preamble_ns or None,
      source=graph.active_source, enforce_contracts=True)`
      (`_build_node_fn`/`_compile_preamble`/`_pipeline_dir` from
      `haute.executor`; `preserve_node_ids` keeps the target frame alive through the
      engine's buffer-release; `source=graph.active_source` — the facade's `"live"`
      default would silently pick the wrong live-switch branch for a pipeline whose saved
      active source differs).
   3. **Read the result** from `lazy_outputs[node]`: a single frame → `collect_schema()`
      rendered as `{name, dtype}` pairs; a multi-frame source (a
      `dict[port_name, LazyFrame]` — e.g. an `apiInput` with several emitted tables) → the
      same rendering per port, keyed by port name, never an unconditional
      `.collect_schema()` on the dict. Nothing is collected, and no result is persisted
      (the call is cheap and always reflects saved state; the engine's own in-request
      schema caches apply). Any engine raise — unfetched Databricks cache
      (`CacheNotFoundError`, whose message already tells the analyst to fetch), a missing
      trained artifact, invalid node code — becomes a structured tool error, sanitized
      like every other tool failure. `apply_graph_edits` first checks
   the mutation precondition — `working_branch_status(...)` reports `state == "ready"` (the
   same readiness the GUI's Save gate requires), else a structured tool error carrying the
   mapped per-state reason (the `src/haute/assistant/_config.py` table) and nothing is read or written — then
   runs the **entire** mutation flow while holding the shared
   `save_lock`, so no GUI save or submodel operation can interleave between its read and its
   write: `parse_pipeline_to_graph` (thread) → `_ops.apply(graph, ops)` (pure; `$ref`s
   resolve as their `add_node` applies; positions assigned after the full batch; any
   validation failure returns a structured tool error before anything touches disk) → build
   the save request **from the parsed graph** so every untargeted field (`sources`,
   `active_source`, `preserved_blocks` — explicitly forwarded per the `_save_pipeline.py`
   row above — name, description, and the preamble unless an op replaced it) round-trips →
   `SavePipelineService.save_graph_transactionally` (thread) → re-parse →
   `default_bus.publish("graph.update", GraphUpdatePayload(graph, graph_fingerprint,
   source_file))` — the exact payload the file watcher publishes, so `/ws/sync` clients
   receive an ordinary `graph_update` frame. The lock spans parse→apply→save→re-parse→
   publish. The save's own writes are self-write-marked by the `Writer` callback (existing
   service behaviour), which is precisely why the explicit publish exists: the watcher will
   deliberately suppress the filesystem echo.
6. Limits: a wall-clock deadline (`HAUTE_ASSISTANT_TURN_TIMEOUT`) checked around provider
   streaming and before each tool dispatch, and a per-turn tool-call cap
   (`HAUTE_ASSISTANT_MAX_TOOL_CALLS`). Hitting either aborts the provider stream and emits
   `failed` naming the limit.
7. Cancellation: the client dropping the SSE connection cancels the generator. The provider
   stream is closed immediately; no further tool is dispatched; a save/publish already in
   flight is wrapped in `asyncio.shield` so the transactional write and its broadcast always
   complete as a pair — and, critically, on cancellation the loop **awaits the shielded
   task to completion while still holding `save_lock`** before re-raising (a bare
   `await shield(...)` re-raises immediately, which would release the lock mid-save and let
   another writer interleave; the implementation therefore uses the await-then-reraise
   form). The `finally` then releases the
   session lock. A `cancelled` terminal event
   is emitted if the transport is still writable, otherwise the turn simply ends (the
   invariant "exactly one terminal event" holds for every stream the client can still read).

**Provider adapters** (the exact SDK surfaces and event mappings):

- **Anthropic** — `client.messages.stream(model=…, system=…, messages=…, tools=…,
  max_tokens=<HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS>)`. Text deltas → `TextDelta`; a `tool_use` content block's
  `input_json_delta` fragments accumulate per block and emit one `ToolCallRequest` at the
  block's stop; the message stop reason (`end_turn` vs `tool_use`) → `TurnStop`; usage
  from the message-level usage events.
- **OpenAI** — `client.chat.completions.create(model=…, messages=…, tools=…, stream=True,
  stream_options={"include_usage": True})` plus the output budget, whose parameter name is
  target-mapped: `max_completion_tokens` against api.openai.com (required by current OpenAI
  models), but `max_tokens` whenever `base_url` is set — the parameter Databricks' Chat
  Completions contract documents. Chat Completions, not the Responses API, deliberately: it
  is the OpenAI-compatible protocol Databricks model-serving endpoints implement, so the
  future third backend reuses this adapter via configuration, with the parameter mapping
  asserted in adapter tests on the emitted request for both shapes. `delta.content` →
  `TextDelta`, accepting both the api.openai.com dialect (a plain string) and the
  OpenAI-compatible-gateway dialect for Anthropic models (a list of typed content parts,
  as Databricks Foundation Model APIs stream for Claude): `text` parts yield `TextDelta`s,
  `reasoning` parts (thinking summaries) are deliberately not surfaced — the chat has no
  thinking channel — and any other part type or shape raises a typed `malformed_stream`
  failure. Each raw chunk's structure (content kinds, tool-call counts, finish reasons,
  usage placement — never text values or tool arguments) is logged at debug level as
  `assistant_openai_chunk_shape`, so an operator can capture a gateway's wire dialect from a
  live stream with `HAUTE_LOG_LEVEL=DEBUG`. End-of-stream contract: a `finish_reason` is
  normally required, but Databricks intermittently omits it from a complete reply's final
  text chunk (captured live 2026-07-19), so a clean stream end without one is accepted as a
  natural stop **only** when no half-delivered tool call is pending, text was actually
  streamed, per-chunk usage was observed, and the output stayed under the token budget —
  logged as a `assistant_openai_stream_missing_finish` warning; a pending tool fragment or a
  missing-usage/empty stream raises `malformed_stream`, and an at-budget end raises the
  typed `truncated` failure; `delta.tool_calls[*]` argument fragments accumulate per call index/id and
  emit `ToolCallRequest`s when `finish_reason == "tool_calls"`; `finish_reason == "stop"` →
  `TurnStop`; usage from the final chunk.
- Usage is **summed across the provider round-trips within one turn**; the `completed`
  event reports the aggregate.
- SDK floors are core project dependencies, not an optional extra:
  `anthropic>=0.40` and `openai>=1.55`. The adapters still import the SDKs lazily and
  readiness reports a missing SDK as a broken installation.

## Edge cases and invariants

- **Ops apply in order against the evolving graph** — `add_node` followed by `add_edge`
  addressing the new node via `$ref` within one batch is valid and covered by tests.
- **The mutation is one critical section.** Parse→apply→save→re-parse→publish happens under
  the process-wide `save_lock`; a GUI save or submodel operation serialises entirely before
  or entirely after, never between the assistant's read and write. A GUI save landing *after*
  a assistant save supersedes it — the same last-write-wins the product has for external
  edits, mitigated by the frontend's clean-canvas gate and the sync banner. Base-revision
  conflict detection on ordinary saves is deliberately out of this feature's scope (noted
  as a candidate follow-up hardening).
- **A batch is all-or-nothing**: op validation failures abort before the save; a mid-save
  failure rolls back every staged file (the save service's existing `_TouchedFile`
  snapshot/rollback); in both cases the pipeline on disk is exactly what it was.
- **Unknown config keys are op errors, not warn-and-drop.** The sidecar writer's
  warn-and-drop exists to tolerate stale keys already on disk; an authoring-time unknown key
  is an LLM mistake that must bounce back as a tool error so the model corrects it. Same
  allowlist source, different strictness, both deliberate.
- **The `[assistant]` table itself is not allowlist-validated.** `provider`, `model`, and
  `base_url` are read; additional keys are currently ignored. A non-string `base_url`
  raises `ConfigError`, any `base_url` on Anthropic is a not-ready reason, and OpenAI
  accepts a string (including an empty string) without URL syntax validation.
- **Submodel boundaries**: ops may only target top-level nodes; `add_node` of
  `submodel`/`submodelPort` types and any op addressing a node inside a submodel graph
  return named tool errors (v1 limitation, stated in the error text).
- **Singletons, name collisions, reserved filenames** are enforced by the save service's
  existing validation — the assistant adds no duplicate checks and inherits any future ones.
- **Position rule** (deterministic, no randomness, evaluated after the whole batch has
  applied so parent-based placement sees final wiring): a new node lands one horizontal step
  right of its rightmost parent (fallback: right of the graph's rightmost node; empty graph:
  origin), vertically staggered by sibling index. Nothing else moves; analysts rearrange
  freely afterwards.
- **Mutation precondition**: `apply_graph_edits` requires `working_branch_status(...)` to
  report `"ready"` — the state in which the save service ledger-captures — so every assistant
  edit is *expected* to be captured. If capture still fails after a successful save (the
  service's documented degrade-to-warning path), the warning propagates into the tool
  result and the chat activity row — never swallowed — and, per the service's own design,
  the next successful capture sweeps the orphaned delta up from working-tree state. Read
  tools carry no precondition.
- **The `graph_updated` fingerprint is the post-save re-parse fingerprint** — the same value
  `/ws/sync` clients receive, so the frontend can correlate the chat event with the canvas
  update.
- **One `GraphUpdatePayload` contract**: the assistant publishes the identical payload shape
  the watcher publishes; no assistant-specific frame type exists on `/ws/sync`.
- **Bounded retention, turn-atomic**: the provider request carries the most recent
  complete turns within a 40-message budget plus the always-complete system prompt; stored
  history caps at 200 messages by evicting whole oldest turns. No pruning boundary ever
  separates an assistant tool call from its result (an orphaned half is an invalid provider
  conversation). Live sessions are LRU-capped at 32 with least-recently-used *idle*
  eviction — a session holding a running turn is never evicted, and eviction drops only
  the in-memory record: the persisted file revives the id transparently on next lookup.
- **Dataset discovery and preview share one safety contract**: installed readable path
  extensions come from `routes.files._installed_input_extensions()` and are matched by
  case-folded filename suffix (including compound extensions). The resolved project-relative
  path must contain no hidden component; exact state/credential names in the assistant
  denylist are rejected before listing or reading. Direct calls cannot bypass the filter
  that navigation applies.
- **`get_node_schema` collects nothing** — the invariant is testable: the tool's plan
  construction plus `collect_schema()` must never invoke `LazyFrame.collect` (asserted by
  poisoning `collect` in tests). The two honest cost exceptions are inherited, not assistant
  behaviour: plain-`.json` sources parse eagerly inside `read_source` (the GUI's
  `/api/schema` pays the same), and a never-fetched Databricks table raises
  `CacheNotFoundError` instead of ever reaching for credentials or the network.
- **`get_node_schema` sees the graph the engine runs, not the graph the editor draws** —
  flattened, preamble-compiled, active-source-selected (the step-5 sequence). A node
  downstream of a submodel therefore resolves through the submodel's real internals, a
  live-switch resolves to the saved active source, and preamble-defined helpers are in
  scope; the submodel placeholder itself is not addressable (structured error, v1
  boundary). Multi-frame sources report per-port schemas keyed by port name.
- **Session invariants**: an id unknown to both memory and disk → 404 on message send,
  never auto-created; one turn per session via the per-session lock; committed turns
  persist to `.haute/assistant/sessions/` and survive restarts, while a truly lost id
  (pruned, corrupt file, cleaned `.haute/`) still 404s and the frontend renders that
  explicitly by starting a fresh session. Tool-role messages retain `is_error` through
  validation, JSON persistence, revival, history-window rendering, and both provider
  adapters. Turn and response cleanup release their idempotent reservation in nested
  `finally` blocks even if history append or iterator close raises.
- **No `print`, structlog only** — the assistant package and router are swept by the existing
  decoupling and routes-hygiene contract tests.

## Error handling

| Failure | Where raised | Surfaced as |
|---|---|---|
| Malformed `haute.toml` `[assistant]` | `_config` | `ConfigError` → 400 (existing convention) |
| Not configured / provider SDK missing | `_config` via route pre-check | 400 with the readiness reason verbatim |
| Unknown session | route | 404 |
| Turn already running on session | route (lock try-acquire) | 409 |
| Working branch not `"ready"` (`working_branch_status`) | `_tools` mutation pre-check | Structured tool error carrying the mapped per-state reason; status reports `mutations_enabled: false` with the same reason |
| Ledger capture fails after a committed save | save service (degrade-to-warning path) | Warning propagated into the tool result and activity row; next successful capture sweeps the delta |
| Provider adapter construction/dependency failure | route provider factory | HTTP 502 before the stream opens |
| Provider request/stream failures (authentication, rate limit, connection, malformed/truncated/filtered output) | `_providers` | `AssistantProviderError` → terminal `failed` SSE event after the response has started |
| Op validation, save validation, missing dataset, unknown node, unknown example name, unresolvable node schema (unfetched Databricks cache, missing artifact, invalid node code) | `_tools`/`_ops`/`_assets`/engine/save service | Structured tool error returned to the model (visible as a failed activity row); never terminates the turn |
| Turn timeout / tool-call cap | `_loop` | Terminal `failed` event naming the limit |
| Any unexpected exception in the loop | `_loop` outermost handler | Logged server-side with `exc_info=True`; terminal `failed` event carrying the sanitized `_INTERNAL_ERROR_DETAIL` text only |
| Broadcast subscriber failures | event bus | Isolated and logged by `EventBus.publish` (existing behaviour); never fails the committed save |

The stream invariant: every response the client can still read ends with exactly one
terminal event (`completed`, `failed`, or `cancelled`); a save/publish pair is never
abandoned half-done (cancellation-shielded); persisted tool calls are always paired with
results; the session lock is always released even when history append or response close
raises. The
release is owned by an idempotent turn reservation with two independent paths — the
loop's `finally` and the streaming response's own lifecycle — so even a client that
disconnects before the body iterator ever starts cannot leave the session locked
(the route reserves atomically before its awaited pre-work, which is also what makes
the concurrent-send 409 a pre-stream decision).

## Testing

Flat files under `tests/` per repo convention (`asyncio_mode = "auto"`; shared `client`
fixture for route tests). The implemented coverage is:

- **`tests/test_assistant_ops.py`** — every op's happy path and rejection paths; op ordering
  within a batch (add then connect via `$ref`); ref resolution (unknown ref, duplicate ref,
  ref shadowing an existing id); all-or-nothing on mid-batch validation failure;
  shallow-merge/null-removes semantics; unknown-config-key rejection; submodel-target and
  submodel-type rejection; ambiguous edge match; deterministic positions evaluated
  post-batch (property: same batch, same graph → same positions).
- **`tests/test_assistant_catalog.py`** — completeness against `NodeType` (mirror of the
  registry-completeness test); folder/decorator facts agree with `_types`/`_config_io`.
- **`tests/test_assistant_tools.py`** — real tmp-project coverage for source/downstream
  schemas, preamble-dependent transforms, and the collect-poisoning invariant
  (`LazyFrame.collect` must not run). Contract tests assert that the saved `active_source`
  is passed to the engine; crafted/mocked execution results cover submodel-boundary
  rejection, multi-frame per-port shaping, unknown-node errors, and propagation of an
  unfetched-Databricks `CacheNotFoundError` message as a structured tool error. Dataset
  coverage pins installed-registry extension parity and rejects direct hidden,
  state-directory, and credential-file listing/preview.
- **`tests/test_assistant_assets.py`** — the authoring guide loads non-empty via
  `importlib.resources` (missing/empty asset raises loudly); every packaged exemplar parses
  cleanly through `parse_pipeline_to_graph` (the drift guard — a stale exemplar fails CI);
  every exemplar has a module docstring and `example_index()` summaries derive from its
  first line; `load_example` renders the same shape `get_pipeline` renders; unknown example
  name → structured error listing valid names.
- **`tests/test_assistant_config.py`** — readiness matrix (absent table, unknown provider,
  missing model, missing key, missing SDK, fully configured); malformed TOML raises;
  `base_url` rejected for anthropic; `max_output_tokens` unset-defaults-to-8192 and
  malformed/non-positive-fails-readiness behaviour (named reason, no silent default);
  `mutations_enabled`/`mutations_reason` across all six `working_branch_status` states
  (ready, no-repository, unset, detached, divergent, invalid — asserting each state's
  mapped reason, including invalid's joined `errors`).
- **`tests/test_assistant_providers.py`** — adapters normalise scripted fake SDK streams to
  `ProviderEvent`s; SDK exception classes map to `AssistantProviderError` variants; lazy
  import failure produces the readiness reason, not an ImportError at server start; the
  OpenAI content-delta dialects (plain string, and gateway content-part lists where `text`
  parts stream, `reasoning` parts stay unsurfaced, and unknown part types or non-text
  shapes raise `malformed_stream`).
- **`tests/test_assistant_loop.py`** — against a scripted fake provider: text-only turn;
  tool round-trip; tool error fed back; cap and timeout terminal events; completed/failed
  exactly-one-terminal checks; cancellation drains an in-flight tool, closes the provider
  stream, and releases the session lock; closing at each tool lifecycle yield never
  persists an unmatched call; a raising history append still releases the lock;
  turn-atomic history windowing, including a
  tool-heavy turn crossing both caps without splitting a call/result group.
- **`tests/test_assistant_routes.py`** — status/session/message endpoints: SSE framing,
  400/404/409 mapping, sanitized unexpected-error paths, readiness reasons on status,
  transcript rehydration, adapter construction, atomic concurrent-send reservation, and
  lock release on pre-stream failure, disconnect, and mid-stream send failure.
- **`tests/test_assistant_session_persistence.py`** — atomic write-through persistence,
  restart revival, invisible LRU eviction, corrupt/invalid-file logged misses, session-id
  path hardening, oldest-first persisted-file pruning, abandoned temp-file cleanup,
  tool-error round-trip, and non-fatal persist failures.
- **`tests/test_assistant_integration.py`** — fake-provider end-to-end on a tmp project:
  instruction → ops → real transactional save (files on disk assert codegen/sidecars) →
  `graph.update` published with the post-save fingerprint (asserted via a test subscriber)
  → a second turn reads its own edit back; a pipeline containing preserve markers survives
  a assistant edit byte-identically outside the edited region; the mutation precondition
  (non-ready working-branch state → tool error carrying the git reason, nothing written);
  `save_lock` exclusivity — a concurrent GUI-style save cannot interleave inside a assistant
  mutation's critical section; cancellation during a slow save leaves the lock held until
  the shielded save has landed and then releases it;
  a degraded ledger capture surfaces its warning in the tool result.
- **`tests/test_save_pipeline_integrity.py`** includes a regression pinning the
  preserve-marker round-trip through
  `save_graph_transactionally` (parse a marker-bearing pipeline → transactional save →
  markers and content survive on disk), independent of which layer supplies the blocks.
No test calls a live Anthropic, OpenAI, or Databricks-compatible endpoint; provider wire
behaviour is exercised with scripted SDK streams. The package is covered by the repository's
global branch gate, while exemplar `.py` assets are omitted from coverage because they are
parsed package data rather than importable modules (they remain parser- and lint-checked).
