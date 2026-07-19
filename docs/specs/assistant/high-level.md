# Assistant — High-Level Specification

## Purpose

Building a Haute pipeline requires the analyst to know which of the 21 node types to reach
for, how each is configured, and how the graph must be wired — knowledge that lives in the
product, not in the analyst's head on day one. The assistant is an in-app AI assistant that
authors the pipeline graph on the analyst's behalf: the analyst types an instruction into a
chat panel ("band `vehicle_age` into 5 groups after the data source, then add a rating step
that uses it"), and a backend-owned agent loop calls an LLM equipped with a small set of
graph-authoring tools. Every mutating tool call lands through the exact same transactional
save path a GUI edit uses, and is broadcast over the same live-sync channel an external `.py`
edit uses — so the analyst watches nodes appear and rewire on the canvas in real time as the
agent works.

This component is the backend half of the feature: the agent loop, the LLM provider
adapters, the tool surface, chat-session state, and the streaming chat API. The chat UI is
[frontend-assistant-ui](../frontend-assistant-ui/high-level.md).

## Scope

In scope:

- The agent loop: assembling the provider request (system prompt, node-type catalog,
  conversation history, tool definitions), streaming the model's response, executing
  requested tools, feeding results back, and terminating on stop/limits.
- The provider abstraction and its two v1 adapters — Anthropic (Claude models, via the
  `anthropic` SDK) and OpenAI (Codex/GPT models, via the `openai` SDK). The OpenAI adapter
  accepts a base-URL override; an OpenAI-protocol-compatible endpoint (the intended slot for
  a Databricks model-serving endpoint later) is configuration, not a third adapter.
- The tool registry and the node-type catalog the model reads (derived from the same
  `NodeType`/config-`TypedDict` machinery the rest of the product dispatches on, plus
  per-type usage notes owned here).
- The assistant's authoring knowledge, shipped as repo-versioned package assets: a
  hand-authored authoring guide injected into every system prompt, and a small set of
  exemplar pipelines — real, parseable `.py` pipeline sources — served on demand through
  `get_example`. Both are CI-guarded (the guide must load non-empty; every exemplar must
  parse cleanly through the real engine).
- Chat sessions: in-memory, per-pipeline conversation state and its lifecycle.
- The assistant HTTP surface: session create, readiness/status, and the message endpoint that
  streams a turn as server-sent events.
- Assistant configuration and readiness: the `[assistant]` table in `haute.toml` (provider,
  model, optional base URL) and API keys from the environment (the project `.env` the server
  already loads at startup).

Out of scope:

- The chat panel, transcript rendering, and all browser state — see
  [frontend-assistant-ui](../frontend-assistant-ui/high-level.md).
- The save/parse machinery the tools call — the transactional save service, sidecar layout,
  codegen, and the event-bus/WebSocket broadcast are owned by
  [server-api](../server-api/high-level.md), [pipeline-config](../pipeline-config/high-level.md),
  and [codegen](../codegen/high-level.md); this component is a caller, never a fork of them.
- Running anything: previews, training, optimiser solves, deploys, and git operations are
  deliberately absent from the v1 tool surface (see Design rationale).
- Submodel creation, dissolution, or edits *inside* a submodel's own graph — v1 tools
  operate on the top-level flat graph only and reject submodel-internal targets loudly.
- Provider-side model behaviour, pricing, or availability.

## Behaviour

**Configuration and readiness.** The assistant is configured per project: `haute.toml`'s
`[assistant]` table names the `provider` (`"anthropic"` or `"openai"`), the `model`, and
optionally a `base_url` (OpenAI adapter only). Credentials come exclusively from the
environment — `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, typically via the project `.env` the
server loads at startup — never from `haute.toml`. A status endpoint reports whether the
assistant is ready and, if not, exactly which piece is missing (no `[assistant]` table, unknown
provider, missing model, missing key, a provider SDK missing from the installation, or an
invalid output-token budget), so the
UI can disable the input with a reason instead of letting a send fail. Sending a message while unconfigured is rejected with a 400 naming the missing
piece — there is no default provider and no silent degradation.

**Sessions.** A chat session is created explicitly, bound to one pipeline, and held in
process memory as the runtime authority, with every committed turn written through to
`.haute/assistant/sessions/<id>.json` so history survives the constant server restarts of
a locally-run tool. Session create accepts an optional prior session id: when its
persisted record exists (in memory or on disk) and is bound to the same pipeline, the
session resumes with its transcript returned for the panel to rehydrate; otherwise a
fresh session is created — resume is an offer, never an error. A *message* against an
unknown session id still fails with 404 rather than silently creating a fresh one.
Retention is bounded
everywhere: the provider request carries a sliding window of recent turns (the system
prompt and catalog are always re-sent in full), stored history is capped per session, and
live sessions are LRU-capped — evicting an idle session drops only its in-memory record;
its persisted file revives it on the next lookup, so eviction is invisible to the client.
A session with a running turn is never evicted. Persisted files have their own cap:
beyond it the oldest files by last use are pruned at session creation, and a pruned or
never-persisted id 404s on message send (and starts fresh on session create).

**Authoring knowledge.** Every turn's system prompt carries — alongside the node-type
catalog — the authoring guide (Haute idioms: canonical pipeline shapes, naming
conventions, how the standard stages chain, do/don't guidance) and an index of the
packaged exemplar pipelines (name plus one-line summary each). The full exemplars are
fetched by the model through `get_example` only when relevant, so the always-paid prompt
stays small while deep worked examples remain one tool call away. Both assets are
versioned files in the repository: improving the assistant's taste is a reviewable
documentation edit, not a code change.

**Turns.** Posting a user message starts a turn, streamed back as typed server-sent events:
assistant text deltas, tool-call started/finished activity (name plus a compact argument and
result summary), a graph-updated notification after each successful mutation (carrying the
new graph fingerprint), and exactly one terminal event — completed (with token usage),
failed (with a typed error), or cancelled. One turn may be in flight per session; a second
send while one is running is rejected with 409, not queued. A turn ends when the model stops
on its own, when a per-turn tool-call cap or wall-clock timeout is hit (both surfaced as a
named terminal event, never a silent truncation), or when the client disconnects — on
disconnect the provider stream is aborted and the loop stops *between* tool executions; a
mutation already executing completes its save (a save is never killed mid-transaction), and
everything already applied stays applied.

**The tool surface** (complete in v1):

- `get_pipeline` — the saved graph: nodes (id, type, name, config summary), edges, preamble,
  and which singletons exist.
- `get_node_config` — one node's full config.
- `list_node_types` — the node-type catalog: every `NodeType`, its config keys and shapes,
  its wiring rules (singleton status, sidecar folder convention), and a usage note.
- `list_datasets` / `get_dataset_schema` — the data files visible to the project and a
  file's column names, dtypes, and a small sample, so the agent configures nodes against
  real columns instead of guessing. Listing is per-directory and names visible
  subdirectories, so the agent navigates into nested folders (`data/`, …) instead of
  guessing paths.
- `get_node_schema` — the column names and dtypes at any node's *output*, resolved by the
  same execution engine that runs the pipeline: the lazy plan is built up to that node —
  with exactly the graph preparation a real run performs (submodels flattened, preamble in
  scope, the saved active source selected) — and its schema is read without collecting any
  data. This is what lets the agent wire a mid-graph transform against the columns that
  actually exist *at that point* — post-join, post-derivation — not just the source file's
  columns. A node emitting several frames reports one schema per output port; the submodel
  placeholder itself is not addressable (the v1 submodel boundary, as for edits).
- `get_example` — one packaged exemplar pipeline by name (the index of available exemplars
  lives in the system prompt), returned as its graph rendering — the same shape
  `get_pipeline` uses — plus the exemplar's narrative notes, so few-shot examples arrive in
  exactly the format the model already reads the live pipeline in.
- `apply_graph_edits` — the single mutation tool: an ordered batch of operations
  (`add_node`, `update_node`, `rename_node`, `delete_node`, `add_edge`, `delete_edge`,
  `update_preamble`) applied to the saved graph and committed as **one** transactional save.

**Mutation semantics.** Each `apply_graph_edits` call loads the pipeline's saved graph,
applies every operation in order against the in-memory graph model, and commits through the
same transactional save service the GUI uses — same validation (singleton limits, name
collisions, path allowlists), same codegen, same sidecar writes, same git-ledger capture.
Operations later in a batch may reference nodes created earlier in it by a batch-local ref,
so add-then-connect never depends on predicting server-side name sanitisation. The whole
read-modify-write runs as one critical section under the same process-wide save lock GUI
saves use — nothing interleaves between the assistant's read and its write. The batch is
all-or-nothing: an operation that fails validation aborts the call before the save, and a
save failure rolls back every touched file (the save service's existing guarantee).
Mutations additionally require the project's git working branch to be in its ready state —
the same readiness the GUI's Save gate requires and the state in which saves are
ledger-captured — so capture is attempted for every assistant edit as the expected path;
without it, mutation tools refuse with a named per-state reason and the status endpoint
says so. In the residual case where capture fails *after* a committed save, the save
service's documented degrade-to-warning path applies and the warning surfaces in the chat
activity row — the next successful capture sweeps the delta up. After a successful save the component re-parses the pipeline
and publishes the same `graph.update` event an external `.py` edit produces, so every
connected canvas updates live; its own writes are marked as self-writes so the file watcher
never re-broadcasts them as a phantom external edit. New nodes are placed at deterministic
server-assigned positions (the analyst can rearrange; the assistant never needs
pixel-accurate layout).

**Tools operate on saved state.** Read tools describe the pipeline as saved on disk, and
mutations rebase on the saved graph at call time. The frontend keeps this coherent by
refusing to start a turn while the canvas has unsaved edits (see
[frontend-assistant-ui](../frontend-assistant-ui/high-level.md)); the backend does not — and
cannot — see browser-local dirty state.

**Tool failures feed the model, not the user.** An operation targeting an unknown node, a
config the save layer rejects, a schema read against a missing file — each returns a
structured error as that tool call's result, so the model can correct course within the same
turn. Only failures of the turn itself (provider errors, timeout, cap, internal errors)
terminate the stream.

## Design rationale

- **Haute owns the agent loop.** Three alternatives were considered and rejected. Driving an
  analyst-installed Claude Code / Codex CLI as the agent (subscription auth, mature loops)
  would make every analyst's machine a deployment target — CLI install, login state,
  subprocess lifecycle, per-OS quirks — and offers no path to a Databricks-hosted endpoint.
  The Claude Agent SDK embeds the same CLI behind a Python API and would add a Node.js
  runtime requirement to a `pip install haute` product. A browser-side loop would put API
  keys in the client and split authority over mutations across the network boundary. A
  backend-owned loop keeps keys server-side, makes the tool surface a unit-testable Python
  API, streams through one channel, and makes "add a provider" a config concern.
- **Official SDKs as core dependencies, two adapters not three.** The `anthropic` and
  `openai` SDKs maintain the streaming/tool-use wire formats we would otherwise hand-roll
  over httpx and chase forever. They ship in the core `haute` dependencies — the assistant
  is a first-class product feature, present in every install with no `haute[assistant]`
  extra (a user decision, 2026-07-19: the earlier optional-extra packaging optimised for a
  lean default install, but a feature the product leads with must not require a second
  install step). The SDKs are still imported lazily at the adapter seam, so importing Haute
  never triggers provider-side behaviour, and a broken installation surfaces as a named
  readiness reason rather than an import crash. A Databricks serving endpoint speaks the
  OpenAI protocol, so the third backend is the OpenAI adapter plus a `base_url` —
  configuration, not code.
- **The same save path as the GUI, not a parallel mutation engine.** Haute's philosophy is
  a single execution engine and a single write path. Because every assistant mutation goes
  through the transactional save service, the assistant cannot produce any on-disk state the
  GUI could not; validation, codegen, sidecar layout, rollback, and git-ledger capture are
  inherited rather than re-implemented — and the git ledger is the undo story for
  direct-apply.
- **Deterministic broadcast, not watcher reliance.** Assistant saves mark self-writes (so the
  debounced watcher stays quiet) and then explicitly re-parse and publish `graph.update` on
  the event bus. Relying on the watcher to notice the write would couple canvas liveness to
  watcher availability (it can be paused by git operations, or absent entirely when
  `watchfiles` isn't installed) and add debounce latency; publishing explicitly is
  deterministic and reuses the exact event/broadcast wiring external edits already exercise.
- **Granular operations, not whole-graph replacement.** Having the model emit a full
  replacement graph invites lost updates (the model's copy goes stale mid-turn), wastes
  tokens on untouched nodes, and turns small intents into large diffs. Ordered ops over the
  saved graph keep payloads proportional to the change and make each batch reviewable in the
  chat activity log.
- **Direct apply, not propose-and-approve.** Every mutation is a complete, validated,
  capture-attempted save, visible live on the canvas — ledger capture is the expected
  path, not incidental, because mutation tools require the ready working-branch state under
  which the save service captures; the analyst interrupts by pressing stop, and reverts
  through the existing git workflow. A staged change-set model (preview,
  conflict handling, apply UI) was deliberately deferred — it multiplies v1 surface without
  changing what the analyst can ultimately do.
- **No execution tools in v1.** Preview/train/optimise/deploy/git tools raise the stakes
  (cost, long-running jobs, deployment safety) and none are needed to author a graph. The
  boundary is explicit so the model is told what it cannot do, rather than discovering it by
  erroring.
- **Catalog completeness is guarded like the node registry.** The catalog mirrors
  `validate_registry_complete()`'s pattern: a check at import time fails loudly if any
  `NodeType` lacks a catalog entry, so a new node type cannot ship invisible to the assistant.
- **Idiom ships as versioned assets, not prompt folklore.** The catalog makes the model
  *correct* (it cannot invent node types or config keys); it does not make it *good* —
  knowing the vocabulary is not knowing the idiom. That gap is closed by two assets owned in
  the repository: the authoring guide and the exemplar pipelines. Baking idiom into
  hard-coded prompt strings scattered through the loop was rejected — assets are diffable,
  reviewable, and improvable by anyone who knows Haute, without touching the loop. An
  external skills framework was likewise rejected: the tool registry already provides the
  progressive-disclosure mechanism (small index always in the prompt, full content on
  demand), and it works identically across every provider adapter.
- **Schema comes from the engine, not a parallel inferencer.** `get_node_schema` reuses the
  single execution engine's lazy path — build the plan to the target node, read
  `collect_schema()`, collect nothing — the same no-data schema resolution the product
  already performs internally (explore, optimiser pre-flights, deploy schema inference).
  A assistant-owned schema deriver was rejected outright: it would be a second
  implementation of node semantics that drifts from the engine. This does not breach the
  "no execution tools" boundary — plan construction materialises no data — with two honest
  exceptions that fail loud rather than hide cost: plain-`.json` sources parse eagerly (a
  cost the GUI's existing schema route already pays for the same files), and a
  never-fetched Databricks table is a named error telling the analyst to fetch it first —
  never a silent remote query with their credentials.
- **Exemplars are real pipelines, kept honest by the engine.** Each exemplar is an actual
  `.py` pipeline source packaged with the assistant, and a CI test parses every one through
  the same `parse_pipeline_to_graph` the product uses — an exemplar that drifts from the
  current node types or config shapes fails the build, exactly like a stale catalog entry
  would. Hand-maintained JSON "example graphs" were rejected for precisely that drift risk.
  Serving them rendered as graphs (not raw source) keeps the few-shot format identical to
  the `get_pipeline` format the model works in.
- **Sessions persist per clone, in `.haute/`.** Haute's server is a locally-run
  distribution vehicle, not a hosted service — users restart it constantly, so
  process-local-only chat would lose every conversation at each restart (a user decision,
  2026-07-19, reversing the earlier in-memory-only stance). Committed turns are written as
  one JSON file per session under `<project_root>/.haute/assistant/sessions/`, inside the
  per-clone `.haute/` state directory that is already gitignore-guarded: history is
  user-private, never committed, and shares the established posture of `.haute/` state —
  an unreadable or hand-corrupted file is a logged warning treated as absent
  (reconstructable convenience, not data), never a crash. The in-memory store remains the
  runtime authority (locks, LRU, turn atomicity); disk is a write-through copy revived on
  lookup miss, so a restarted server resumes a session the browser still remembers.
- **SSE within the request, not background jobs, not the sync socket.** The
  [background-jobs](../background-jobs/high-level.md) machinery exists for work the user
  navigates away from and polls; a chat turn is interactive — the analyst is watching it
  stream, and abandoning it should abort it. Nor does the turn ride the existing `/ws/sync`
  WebSocket: that channel broadcasts graph state to every connected canvas, while a chat
  turn is a private, request-scoped stream with its own abort semantics — multiplexing
  per-session chat frames into the broadcast client registry would complicate both. A
  request-scoped SSE response maps one-to-one onto the turn lifecycle (SSE is new transport
  for the authoring backend, introduced deliberately here).

## Interactions

- **[server-api](../server-api/high-level.md)** — the assistant router is included into the
  same FastAPI app (session middleware, sanitized-error conventions apply); request/response
  models live in the shared `schemas.py` contract module; mutations run under the shared
  `save_lock` through the transactional save service; broadcasts publish the existing
  `graph.update` event on the shared event bus; self-write marking prevents watcher
  feedback.
- **[pipeline-config](../pipeline-config/high-level.md)** — the graph model the ops mutate
  and the config-key validity rules the save path enforces; the node-type catalog is derived
  from the same config `TypedDict`s.
- **[codegen](../codegen/high-level.md)** — reached only through the save service; the
  assistant never generates `.py` source itself.
- **[io-layer](../io-layer/high-level.md)** — dataset listing and schema reads back
  `list_datasets` / `get_dataset_schema`.
- **[execution-engine](../execution-engine/high-level.md)** — `get_node_schema` builds the
  lazy plan through the engine's public facade (target-node execution, nothing collected);
  the assistant adds no schema logic of its own.
- **[sandbox-security](../sandbox-security/high-level.md)** — assistant-authored node code
  (e.g. a `polars` body) is validated and sandboxed identically to human-authored code; the
  assistant adds no bypass.
- **[frontend-assistant-ui](../frontend-assistant-ui/high-level.md)** — the sole consumer of the
  assistant HTTP surface; owns the clean-canvas send gate.
- **[frontend-graph-canvas](../frontend-graph-canvas/high-level.md)** — receives assistant
  mutations as ordinary `graph.update` frames over `/ws/sync`; its dirty-state banner and
  apply/rollback behaviour are unchanged.

## Failure model

Loud, typed, and never averaged away:

- **Unconfigured** — message send against a project with no usable `[assistant]` config
  returns 400 with a message naming exactly what is missing; the status endpoint reports the
  same reason machine-readably. No default provider, no fallback model.
- **Provider failures** (bad key, rate limit, overloaded, network, malformed stream) raise a
  assistant-specific `HauteError` subclass whose hand-authored message carries the provider
  name and failure class but never the raw provider response body; mid-stream it becomes the
  terminal `failed` event, before streaming it becomes the HTTP error. The turn dies with
  the error — there is no silent retry cascade and never a fallback to a different
  provider or model.
- **Tool-level failures** (unknown node id, invalid op, save-layer validation rejection,
  missing dataset, a schema the engine cannot resolve — unfetched Databricks cache, missing
  trained artifact, invalid node code) are structured tool results returned to the model —
  visible in the chat activity log — not turn failures.
- **Save failures roll back** via the save service's existing staged-write transaction; a
  failed `apply_graph_edits` never leaves a partially-written pipeline, and the error
  (sanitized) is what the model sees.
- **Limits** — the per-turn tool-call cap and wall-clock timeout each terminate the stream
  with a named terminal event stating which limit was hit. Edits already applied remain (each
  was a complete valid save); nothing is auto-reverted.
- **Client disconnect** aborts the provider stream and stops the loop between tool
  executions; an executing save always completes. No orphaned provider streams outlive the
  request.
- **Working branch not ready** — mutation tools refuse with a named per-state reason
  (read tools still work), and the status endpoint reports mutations disabled with the same
  reason; a rare post-save capture failure degrades to a visible warning in the chat, never
  silently.
- **Unknown session** → 404; **concurrent turn on one session** → 409; both typed, neither
  auto-recovers.
- **Broadcast failures are isolated** — the event bus already isolates subscriber
  exceptions, so a misbehaving WebSocket consumer can never fail a save that has already
  committed.
