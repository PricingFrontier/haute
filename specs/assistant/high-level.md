# Assistant — High-Level Specification

## Purpose

Building a Haute pipeline requires the analyst to know which of the 19 node types to reach
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

- The agent loop: assembling the provider request (system prompt, compact capability index,
  conversation history, tool definitions), streaming the model's response, executing
  requested tools, feeding results back, and terminating on stop/limits.
- The provider abstraction and its three v1 provider modes — Anthropic (Claude models, via
  the `anthropic` SDK), OpenAI (Codex/GPT models, via the `openai` SDK), and Databricks
  Model Serving. Databricks is named explicitly in project configuration, derives its
  OpenAI-compatible serving URL and bearer token from the project's standard
  `DATABRICKS_HOST` / `DATABRICKS_TOKEN` environment variables, and reuses the
  OpenAI-protocol stream normalizer internally.
- The versioned tool/recipe registry and node descriptors the model queries (derived from the same
  `NodeType`/config-`TypedDict` machinery the rest of the product dispatches on, plus
  per-type usage notes owned here).
- The assistant's authoring knowledge, shipped as repo-versioned package assets: a
  concise authoring guide and discoverable executable project bundles served on
  demand through `get_example`. Bundle inventories are content-addressed; every
  bundle parses and validates, and the declared fast subset executes in installed
  distribution smoke checks.
- Chat sessions: process-local per-pipeline conversation state, bounded durable
  restart history, and separate provider-working/redacted persisted representations.
- The assistant HTTP surface: session create, readiness/status, and the message endpoint that
  streams a turn as server-sent events.
- Assistant configuration and readiness: the closed `[assistant]` table in
  `haute.toml` (provider, model, optional base URL, and required nested egress
  policy) and API keys inherited from the server process environment.

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
`[assistant]` table names the `provider` (`"anthropic"`, `"openai"`, or
`"databricks"`), the `model`, optionally a `base_url` (OpenAI only), and a required closed
`[assistant.egress]` table. Credentials come exclusively from the
environment — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `DATABRICKS_TOKEN` —
never from `haute.toml`. Databricks also requires `DATABRICKS_HOST`; Haute
validates it as a credential-free absolute HTTPS workspace-root URL and
derives `<host>/serving-endpoints`. A Databricks `base_url` is rejected so
workspace identity is never duplicated or allowed to drift from `.env`.
The outer table is closed to `provider`, `model`, `base_url`, and `egress`;
the nested table requires the exact trust/sensitivity/`allow_*` fields defined
in the ASSIST-A07 contract below. An older table without `egress` is not ready
and names `[assistant].egress` in its migration reason. An unknown key raises
`ConfigError` naming `[assistant].<key>`. An OpenAI
`base_url`, when present, must be an absolute `http` or `https` URL with a
hostname and no embedded user information; malformed, relative, unsupported-
scheme, whitespace/control-bearing, or invalid-port values raise a
field-specific `ConfigError` without echoing the URL. Anthropic continues to
reject the field entirely, as does Databricks because its endpoint is derived.
During lifespan startup, `haute serve` loads the project `.env` into the process environment
without overriding variables the caller already exported. A status endpoint reports whether the
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
fresh session is created — resume is an offer, never an error. A mismatched
offer is rejected before the candidate is promoted or touched in the live LRU,
so asking to resume the wrong pipeline cannot evict a useful session. A *message* against an
unknown session id still fails with 404 rather than silently creating a fresh one.
Retention is bounded
everywhere: the provider request carries a sliding window of recent turns (the system
prompt carries only the compact manifest identity/index), stored history is capped per session, and
live sessions are LRU-capped — evicting an idle session drops only its in-memory record;
its persisted file revives it on the next lookup, so eviction is invisible to the client.
A session with a running turn is never evicted. Persisted files have their own cap:
beyond it the oldest files by session-file modification time are pruned at session creation, and a pruned or
never-persisted id 404s on message send (and starts fresh on session create). Session
creation also removes abandoned atomic-write `<id>.json.tmp` files, so a process crash
during persistence cannot grow the session directory outside that bound.

**Authoring knowledge.** Every turn's system prompt carries the compact capability
identity plus node, operation, recipe, and example indexes. The versioned authoring
guide (Haute idioms, standard shapes, naming, and do/don't guidance) is retrieved
through `get_authoring_guide` only when relevant. `get_example` returns a
self-contained teaching view: bounded attribution, narrative, and the already-rendered
live graph. It never advertises resource-inventory paths that the model cannot retrieve.
This keeps the always-paid prompt bounded while preserving attributable detailed guidance
one tool call away.
The guide refers to the mechanically-derived registry instead of hand-copying node
vocabulary, and each bundle uses canonical specialised decorators and sidecar loading
rather than hiding file reads in generic `polars` nodes.

**Turns.** Posting a user message starts a turn, streamed back as typed server-sent events:
assistant text deltas, tool-call started/finished activity (name plus a compact argument and
result summary), a graph-updated notification after each successful mutation (carrying the
new graph fingerprint), and exactly one terminal event — completed (with token usage),
failed (with a sanitized message), or cancelled. One turn may be in flight per session; a second
send while one is running is rejected with 409, not queued. A turn ends when the model stops
on its own, when a per-turn tool-call cap or wall-clock timeout is hit (both surfaced as a
named terminal event, never a silent truncation), or when the client disconnects — on
disconnect the provider stream is aborted and the loop stops *between* tool executions; a
mutation already executing completes its save (a save is never killed mid-transaction), and
everything already applied stays applied. Turn history is committed only with matched
tool-call/result pairs: a disconnect before execution drops the unmatched call, while a
completed tool result is recorded before any result/update event is emitted. Cleanup
always releases the session reservation even if history persistence or response teardown
raises, so a failed cleanup cannot turn into a permanent 409 for that session.
When the user's request authorizes a mutation and the required intent is known, the
assistant must not end after merely announcing a future tool call. It completes the
dry-run/apply sequence, asks one focused question prefixed `NEEDS_INPUT:` when
material intent is ambiguous, or reports a concrete tool blocker prefixed `BLOCKED:`.
After any dry-run attempt, a provider `end` without a successful apply or one of those
explicit outcomes is not accepted as completion. The controller records one internal, non-transcript continuation instruction which says
that a successful dry-run must be followed immediately by an `apply_graph_plan` tool call
using the exact returned hash, then requests one more provider round; a second unqualified
end fails the turn as incomplete instead of falsely completing it. A successful
`apply_graph_plan` is itself the terminal mutation outcome: after consuming that provider
round, the controller emits a concise deterministic success confirmation and completes
without exposing another tool round in which the model could repeat or extend the mutation.
A failed dry-run permits one materially corrected retry. If that retry also fails, the
controller terminates the tool loop itself with a value-free `BLOCKED:` outcome naming the
latest stable error code and stating that no graph changes were applied; the provider cannot
continue guessing until the global tool-call limit is exhausted.
The lexical completion check evaluates the user's requested action, not action words the user
explicitly frames as untrusted reported content inside a read-only inspect-and-explain request.
The successful mutation result retains its graph fingerprint in neutral
history; resume derives the same settled “Canvas updated” activity row from
that durable fact, in its original position after the mutation tool row.

**The tool surface** (complete in v1):

- `get_capability_manifest` / `get_capability_descriptors` — the compact installed
  manifest and a bounded ordered batch of complete closed node, operation, or recipe
  descriptors. A batch has one kind and one to twelve unique ids, eliminating per-node
  tool-call pressure for complex authoring. Every returned descriptor is materialised as
  ordinary finite JSON containers before it crosses the tool boundary; immutable registry
  wrappers never leak into provider results.
- `get_authoring_guide` — the complete attributable packaged guide, retrieved on
  demand rather than embedded in every request.
- `get_pipeline` — the saved graph: nodes (id, type, name, config summary), edges,
  a preamble-presence/digest summary (never executable source), and which
  singletons exist.
- `get_node_config` — one node's restricted structured config. Executable code
  and credential-shaped fields are always redacted, but the remaining shape is
  still treated as `restricted` and is refused unless the configured policy
  permits that class.
- `list_node_types` — the node-type catalog: every `NodeType`, its config keys and shapes,
  its wiring rules (singleton status, sidecar folder convention), and a usage note.
- `list_datasets` / `get_dataset_schema` — the data files visible to the project
  and a file's column names and dtypes, with no preview collection or row values.
  Listing names visible subdirectories and accepts a bounded recursive traversal. For a
  routed Parquet showcase whose request explicitly names a safe relative folder, an omitted
  listing root is bound to that folder and recursive discovery is enabled, so datasets below
  folders such as `data/claims/` are not falsely reported as absent. Recursive results are
  deterministically ordered and report truncation rather than silently omitting overflow.
  Both operations use the installed input-format registry; unavailable
  optional engines and unsupported extensions are not advertised. Hidden path components
  and explicitly denylisted credential/state names are rejected for both listing and
  schema inspection, even when the caller supplies the path directly.
- `get_project_knowledge` — a bounded, query-selected view of policy-eligible
  source-linked project facts and untrusted documentation evidence. Each item
  carries source digest, extraction version, sensitivity and evidence class;
  excluded content is counted but its path or value is not disclosed.
- `get_column_profiles` — what the values in a frame actually look like, for the one
  question a schema cannot answer: how a categorical column encodes itself. A `fault`
  column typed `String` may hold `Y`/`N`, `true`/`false`, or `at_fault`/`not_at_fault`,
  and code written against the wrong guess runs, validates, and silently matches nothing.
  It returns distinct levels with counts for small-cardinality columns, bounds for
  numerics and dates, and never a row. A column with many distinct values has its values
  withheld, reducing unnecessary disclosure; low-cardinality strings can still be returned,
  including repeated personal data. This is the only tool that reads project data, and the
  project's explicit `allow_row_samples` policy is therefore the authorization boundary.
- `get_node_schema` — the column names and dtypes at any node's *output* **and on each of
  its inputs**, resolved by the
  same execution engine that runs the pipeline: the lazy plan is built up to that node —
  with exactly the graph preparation a real run performs (submodels flattened, preamble in
  scope, the saved active source selected) — and its schema is read without collecting any
  data. Inputs are keyed by the name the node's own code binds, because writing a
  transform needs the columns arriving at it, not only the ones leaving it — and a node
  the analyst has asked the assistant to *write* has no output schema to report. Such a
  node answers with those inputs and a stable reason rather than refusing.
  This is what lets the agent wire a mid-graph transform against the columns that
  actually exist *at that point* — post-join, post-derivation — not just the source file's
  columns. A node emitting several frames reports one schema per output port; the submodel
  placeholder itself is not addressable (the v1 submodel boundary, as for edits).
- `get_example` — one self-contained packaged teaching view by name: bounded
  attribution, narrative, and a graph rendered through the same machinery as a live
  pipeline, without inaccessible resource paths.
- `plan_recipe` — accept one flat, recipe-discriminated invocation, including an optional
  downstream response-output name plus explicit selected columns, expand it deterministically, and return
  only an opaque content-addressed recipe-plan receipt; it never writes. On an explicitly
  routed turn the provider sees only that recipe's exact argument branch, so nested fields
  are not weakened by a cross-recipe portability projection.
- `dry_run_recipe_plan` — consume only that hash server-side and produce the same exact
  revision-bound plan as the primitive dry-run without asking the model to copy, extend, or
  reconstruct nested recipe JSON.
- `dry_run_graph_edits` / `apply_graph_plan` — validate an ordered primitive operation
  batch into an exact revision-bound semantic plan, then apply either kind of stored plan
  once using the exact returned plan hash. These are the only provider-visible mutation
  operations.

**Mutation semantics.** `dry_run_graph_edits` loads a canonical saved-state
snapshot, applies the ordered primitive operations to a deep copy, invokes the
save service's public no-write validation, evaluates the closed structural
postconditions, and resolves affected terminal lazy schemas without collecting
rows or invoking sinks. It stores an immutable plan containing the base
revision, semantic diff, verification tier, schema evidence, and plan hash. The provider-visible
diff is bounded per category but carries complete counts, an explicit
truncation flag, and a digest over the complete diff. Operations later in a
batch may reference nodes created earlier by a batch-local ref.

`apply_graph_plan` accepts only that stored plan hash. Under the shared save
lock it reloads every revision source, rejects stale evidence, replays and
revalidates the exact plan, checks its one-use authority, then commits once
through the transactional save
service. The service reparses, compares the actual and expected visible diff
and complete-diff digest, evaluates postconditions, re-proves the bound schema
evidence, reports the combined evidence and result revision, and publishes the ordinary
`graph.update` event. Errors before
save write nothing; a failed pre-commit application marks that plan attempt
aborted, so it cannot be applied directly again. A fresh identical dry-run may
revalidate and reissue the same deterministic hash. A failure discovered after
commit reports the committed state and ledger evidence without replaying the
mutation.

Assistant-authored batches must leave every newly added node connected in the
resulting graph. Explicit Polars code must start from the node's named input
parameters (`df` is only the output variable, never pre-bound to an input) and
assign the transformed frame to `df` or return a transformed frame; immutable
expressions whose results would be discarded, and code that reads `df` before
an assignment that definitely dominates that read across control flow, are
rejected during dry-run. The derived input name `df` is reserved and rejected
rather than silently weakening the output-only contract.

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
- **Official SDKs as core dependencies; one OpenAI-compatible wire implementation.** The `anthropic` and
  `openai` SDKs maintain the streaming/tool-use wire formats we would otherwise hand-roll
  over httpx and chase forever. They ship in the core `haute` dependencies — the assistant
  is a first-class product feature, present in every install with no `haute[assistant]`
  extra (a user decision, 2026-07-19: the earlier optional-extra packaging optimised for a
  lean default install, but a feature the product leads with must not require a second
  install step). The SDKs are still imported lazily at the adapter seam, so importing Haute
  never triggers provider-side behaviour, and a broken installation surfaces as a named
  readiness reason rather than an import crash. A Databricks serving endpoint speaks the
  OpenAI protocol, so the public `DatabricksProvider` reuses that wire implementation while
  retaining a truthful provider identity, Databricks-specific error attribution, and the
  standard Databricks `.env` contract. All provider adapters advertise the same conservative
  wire-schema projection. It preserves names, descriptions, required fields, single scalar
  types, enums, and container shapes. A discriminated composition whose branches are closed
  objects is merged into one closed generation object: branch properties are unioned, the
  discriminator constants become one enum, and only requirements common to every branch
  remain required. A property present in only one branch, or declared identically across
  branches, is recursively projected within the remaining budget so its description and
  affordable nested shape survive. Projection has a sixteen-property budget per tool; a composition that
  would exceed it remains a generic typed container. Unsupported validation vocabulary is
  omitted. The complete operation schema remains available through batched capability
  descriptors and remains the sole execution-time authority, so portability never weakens
  validation. Some Databricks-hosted OpenAI-compatible models encode function
  arguments whose declared type is an array, object, boolean, integer, or number as a JSON
  string. The Databricks adapter decodes only valid, correctly typed, schema-declared
  top-level values. Numeric results must be finite, booleans never satisfy integer or number
  declarations, and string or null declarations are never decoded. The adapter does not
  infer a type from an undeclared or ambiguous schema. An
  invalid or wrong-type encoding is left unchanged for the canonical tool validator to
  reject as a structured, recoverable tool result; it is never guessed, repaired, or
  executed, and it does not terminate the provider stream.
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
- **A mutation plan that changes executable flow must prove its schema before it can be
  applied.** Dry-run resolves the terminal schemas reachable from every added, configured,
  or rewired node through that same lazy engine path. The closed evidence records each
  target and a digest of its resolved output schema, is authority-bound into the plan hash,
  and is recomputed both before and after the transactional save. Invalid Polars plans,
  unusable banding rules, missing local inputs, and incompatible downstream contracts
  therefore fail during dry-run rather than becoming a structurally valid but unusable
  saved graph. This remains graph authoring rather than pipeline execution: it collects no
  rows and never invokes Data Output publication or another external-write surface.
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

## Approved change contract — ASSIST-A04 capability registry

The node catalogue becomes one view of a versioned, library-owned capability
manifest. The manifest is the assistant's source of truth and contains:

- the installed Haute version, manifest schema version, deterministic
  capability hash, installed I/O formats and optional engines, and enabled
  feature flags;
- one closed descriptor for every `NodeType`, including its resolved config
  JSON Schema, required/optional fields, defaults/enums, nested and
  discriminated branches, runtime-derived decorator/config/sidecar/singleton
  facts, ports/cardinality/schema effects, execution and side-effect classes,
  and completeness-checked semantic guidance;
- one closed descriptor for every callable assistant operation, including
  versioned input/output schemas, read/mutation and revision semantics,
  deterministic risk/egress/side-effect/cost classes, retry/idempotency,
  concurrency/cancellation/cache behaviour, limits, stable errors, and
  recovery guidance.

The capability hash is SHA-256 over canonical JSON containing every immutable
derived and hand-authored manifest fact except the hash field itself. Dict keys
are sorted, arrays retain declared semantic order, and no project state,
timestamps, paths, or process identity enter the material. The immutable
manifest object is cached by `(installed Haute version, capability hash)`;
installed capability discovery is refreshed before choosing that cache key.
Changing any descriptor or installed capability therefore selects a new cache
entry without an external prompt or documentation update.

The permanent prompt contains only manifest identity and a compact node,
operation, recipe, and example index. Full descriptors are retrieved through a
bounded capability-query operation. An unknown descriptor kind or identifier
returns `unsupported_capability`; malformed closed input returns
`invalid_capability_query`. These are tool-level failures and never trigger a
prompt-owned fallback vocabulary.

The legacy `list_node_types` response remains a compatibility view generated
from the manifest during the transition. It does not own facts independently.

## Approved change contract — ASSIST-A05 application services and authority

Assistant reads and mutations are adapters over one typed Python application
service. The service owns project snapshots, planning, dry-run, apply, and
verification; the model loop and HTTP route do not reimplement those rules.

A project revision is SHA-256 over a canonical snapshot manifest containing
the saved parsed graph, pipeline source identity and content digest, relevant
project configuration, exact source/schema evidence digests used by planning,
artifact and model identities already represented in the saved graph, and the
capability manifest hash. V1 planning does not read artifact
contents, so mutable artifact bytes are not represented as evidence unless a
future operation actually inspects them. The cache index itself and live
browser state are excluded. Every saved-state read returns the revision it
describes.

Each turn's tool executor starts with the source/schema evidence present in
the exact bounded history window sent to the provider, then adds evidence
returned during the current turn. A follow-up turn therefore cannot form a
replacement plan from a previously returned dataset schema or project fact
while silently dropping that evidence from the plan revision.
Restart-redacted tool payloads contain no reusable source detail and seed no
evidence, matching what the provider can actually observe after restart.

`dry_run_graph_edits` accepts the closed primitive operation union and explicit
postconditions. It returns normalized operations; the base revision; a stable
plan hash; semantic node, edge, configuration, preamble and sidecar changes;
validation errors/warnings; resulting graph shape; affected capabilities; the
deterministic egress class; and the strongest bounded verification tier the
affected capabilities declare. The plan hash is
canonical over all facts that can affect authorization or verification.
Canonical request validation recognizes closed object unions discriminated by fields such
as `op` and `kind`. It selects the declared branch before validation so retry feedback
names the exact safe schema path and a stable value-free reason, rather than collapsing all
branch failures to a generic `oneOf` error. Those fields are retained in redacted history;
submitted values are not.

Graph authoring never runs the graph, collects rows, invokes a sink, or
materialises a configured output. Dry-run constructs the production lazy plan
far enough to resolve affected schemas only, so valid graph edits do not
require a second user confirmation after the user
has asked the assistant to author the pipeline. This includes Polars code,
data-output definitions, deletions, preamble changes, submodel changes and
large but valid operation batches. Dry-run validation, exact revision
authority, single use, transactional save, postconditions and verification
remain mandatory. Actually running a pipeline or performing an external write
is protected at execution time, not graph-authoring time. V1 exposes no
assistant execution tool; any future execution, training, optimisation,
deployment, Git or other external-side-effect tool must define its own
explicit runtime authorization instead of reusing graph-plan authority.

`apply_graph_plan` accepts a plan hash, not a replacement operation payload.
Under the shared save lock it reloads the snapshot, rejects a stale revision,
recomputes the normalized plan and hash, checks exact-plan authority, and
commits once through `SavePipelineService`. A plan is single-use; a repeated
apply returns `plan_already_applied` with no write. Any changed authority fact
invalidates the plan.

After save, the service reparses and computes the actual semantic diff, checks
the declared postconditions, runs the strongest permitted local verification,
and compares the result to the dry-run. V1 plans that affect executable flow
declare `schema`, which combines reparse/save validation, exact diff,
structural-postcondition evidence, and exact lazy-schema evidence. A mutation
with no executable target to resolve (for example deleting the only node) may
declare `structural`. Neither tier collects rows or invokes external writes,
and no row tier is selected implicitly. Results name the tier
that actually ran and include bounded evidence, the resulting revision, graph
fingerprint, ledger reference and warnings. Structural or plan verification is
never described as row-level, model-quality, pricing, or commercial proof.

Stable application errors include `invalid_plan`, `stale_revision`,
`stale_project_evidence`, `plan_not_found`, `plan_expired`,
`plan_store_busy`, `plan_aborted`, `plan_already_applied`,
`authority_denied`, `postcondition_failed`, and `verification_failed`.
All are returned before a write except verification
failure, which reports that the transactional save committed and preserves the
ordinary ledger/undo path.

## Approved change contract — ASSIST-A06 recipes and executable bundles

Recipes are versioned deterministic planners over the canonical primitive graph
operations. Their descriptors declare closed argument schemas, unresolved user
decisions, preconditions, allowed operation kinds, postconditions, linked
examples, and stable failures. Planning never writes, and every planner output
is parsed by the same primitive validator before it can enter dry-run or apply.
A recipe cannot grant authority, choose an omitted pricing assumption, or
bypass revision, egress, save, or verification policy. Continuous-banding recipe rules
use a closed nested contract: each rule requires a supported `op1`, finite numeric
`val1`, and non-empty `assignment`; an optional second bound requires both `op2`
and `val2`. The categorical-banding recipe instead uses closed rules containing exactly
a non-null finite JSON scalar `value` and non-empty `assignment`. Recipe argument
descriptions distinguish graph node names from output column names. The rating-step recipe
uses a provider-facing positional contract instead of canonical dynamic row keys: each
table declares one to three ordered `factors`, an `output_column`, a finite
`default_value`, and closed entries containing aligned `factor_values` plus a finite
numeric `value`. Optional combined outputs use closed `output_column`, `operation`,
and finite `base_value` fields. The planner validates alignment, scalar values,
uniqueness, supported operations, and canonical rating normalisation before emitting
Haute's dynamic-key sidecar form.
The `parquet_showcase` recipe accepts two closed `{path, name}` file sources, an explicit
join name/key, and Polars-transform and response-output node names. The provider does not
author code or output mappings for this open-ended demonstration. From the validated shared
join key, the planner generates a fixed Polars transform adding `<join_key>_text` and
`showcase_stage`, then maps the join key and those two derived columns. It deterministically
builds two scanned Parquet inputs, an exact-role left join, one connected transform, and one
connected response output; it never runs the graph or materialises a sink.
Within one tool executor, a successful recipe call retains its canonical operations and
postconditions behind the returned recipe-plan hash while returning only recipe identity,
version, and hash to the provider. Calling the same recipe again replaces the prior pending
handle so corrected arguments do not leave an ambiguous stale plan. A transform, join, or
rating recipe's optional `output_name` and non-empty `output_columns` must be supplied
together; they deterministically add and connect one response `output` node with a
canonical JSON mapping for exactly those columns inside the same stored plan. The standalone
`response_output` recipe requires `source`, `output_name`, and `output_columns` and
creates that same mapping directly after the saved source. A bare output name is a material
mapping ambiguity and requires clarification. `dry_run_recipe_plan` resolves only a live
handle from that executor and
accepts no model-authored operations or postconditions; the model never receives, relays,
extends, or rewrites canonical recipe JSON. Primitive `dry_run_graph_edits` is rejected while
a recipe handle is pending, and the handle clears only after its successful dedicated
dry-run. A model therefore cannot discover the specialist contract and then silently
substitute a generic node.

For each turn, a conservative deterministic router recognizes only a single unambiguous
explicit recipe pattern: a band/banding term plus a continuous, range, breakpoint, bucket,
or comparison-operator cue for continuous banding; a band/banding term plus categorical or
discrete for categorical banding; join for a reference join; or the phrase rating step.
An explicit request to build, create, author, or make a Parquet pipeline as a showcase of
multiple node types routes to `parquet_showcase`. The showcase cue accepts `showcase`, the
closed phrase `node types`, or `many … types`, so a harmless typo in the intervening noun
does not discard an otherwise explicit authoring request. With two to eight discovered Parquet
datasets, that showcase route inspects every schema and ranks coherent pairs deterministically:
a shared `quote_id` outranks a pair with exactly one other shared column, then larger combined
distinct schema width wins, then project-relative path order breaks a tie. Within the selected
pair, the wider schema is the base with path order breaking equal widths. The deterministic recipe
owns the safe connected transform and mapped output. It asks only when fewer than two or more than
eight datasets are discovered, or when the bounded set has no coherent pair. When a routed turn ends with
`NEEDS_INPUT:`, immediately following clarification turns retain that route while each
intervening turn also ends with `NEEDS_INPUT:`. A normal answer, completed mutation, or
unqualified assistant response closes the chain, so old authoring authority is never revived.
A standalone `response output` request routes to `response_output`;
when a specialist recipe request also asks for a response output, that specialist route owns
the downstream output instead. Categorical or discrete banding never routes to the
continuous recipe. A unique match is appended to the provider system contract and bound
independently to the tool executor. Without a unique route the provider catalog omits both
recipe-planning tools, while the canonical internal registry retains the complete union.
Primitive dry-run before the required recipe returns `recipe_route_required`;
attempting a different recipe returns `recipe_route_mismatch`. No match or a
request matching more than one recipe leaves routing unforced. Routing never
supplies or infers recipe arguments. It preserves explicit primary recipe node names from
closed `named NAME` and `add NAME:` forms: a mismatching provider argument is rejected with
`recipe_name_mismatch` rather than silently changing user intent. Missing material choices require
focused clarification. A rating-factor request which explicitly withholds factor values or
missing-factor policy is deterministically clarification-only: the current-turn contract requires
`NEEDS_INPUT:`, mutation tools are omitted from the provider catalog, and the source-bound
executor rejects any bypass attempt with `material_input_required`. When a route is unique, the provider tool definition narrows
`plan_recipe` to the matching closed branch while the executor still validates against the
canonical discriminated union and independently enforces the route.

Packaged examples are versioned resource bundles with a closed manifest,
pipeline source, sidecars, tiny synthetic data, expected graph/schema material,
golden request/output material, boundary cases, paired prompts, and semantic
assertions. Every bundle declares its assertion tier and review class.
The review class identifies the required review discipline, not an approval
attestation: model-validation and optimisation fixtures declare `pricing`,
while purely mechanical fixtures declare `engineering`.
The closed assertion tiers are `fast`, `ordinary`, and `negative`. Fast
bundles execute through the production graph executor in installed wheel and
source-distribution smoke checks. Ordinary bundles parse there and execute
their declared production training, scoring, optimisation, apply, trace, or
deployment-preflight checks in the ordinary test suite. Negative bundles are
valid teaching projects with machine-readable invalid/adversarial cases; their
rejection checks run in the ordinary suite, so discoverability never requires
importing malformed or executable hostile source.
Golden output arrays are positional contracts. A bundle whose operators do not
guarantee row order must impose an explicit stable order in its production
pipeline before asserting those arrays; packaging checks never sort observed
results to make a nondeterministic fixture pass.
Discoverable teaching bundles are indexed by the capability registry and
`get_example`; held-out evaluation fixtures live outside assistant package
resources and cannot be enumerated through those surfaces. Installed
distribution smoke checks enumerate and validate every bundle and execute the
declared fast subset.

The explicit credentialed assistant self-test lane uses disposable held-out synthetic
projects and the configured provider. Its checked-in prompt portfolio covers specialist
recipes, primitive graph edits, mapped outputs, join-port semantics, graph authoring for
file sources and sinks, focused clarification, prompt injection, and blocked requests to
execute pipelines or perform external writes. Graph-authoring cases may save a sink node but
the lane never runs the resulting pipeline or materialises the sink. Reports remain
content-redacted and retain only semantic graph structure, value-free diagnostics, outcomes,
and aggregate metrics. For a multi-round turn, the last explicit `NEEDS_INPUT:` or `BLOCKED:`
marker in accumulated assistant text determines its non-mutation outcome even when earlier
rounds streamed preparatory prose.


## Approved change contract — ASSIST-A07 egress and project knowledge

`[assistant.egress]` is required and closed. It contains exactly `trust`
(`local`, `organization`, or `external`), `max_sensitivity` (`public`,
`internal`, or `restricted`), and the required booleans
`allow_project_knowledge`, `allow_executable_source`, and
`allow_row_samples`. A legacy `[assistant]` table without it is not ready and
names `[assistant].egress` in its migration error. Local endpoints must be
loopback; organization and external endpoints must use HTTPS; external policy
is public-only and cannot enable executable source or row samples. Project
configuration may narrow but never widen these class ceilings.

Schema inspection is schema-only: assistant schema results never contain
preview rows. Raw rows and executable source are unavailable through ordinary
read tools. Any future sensitive read must first produce a closed disclosure
bound to endpoint identity, policy hash, project revision, category, resource,
fields, sensitivity, and row limit, then consume same-session confirmation
exactly once. Credentials, credential references, hidden paths, and restricted
values never enter a provider-visible or persisted tool payload.

The tool boundary assigns minimum sensitivity before performing a project
read: saved graph topology, dataset listings, dataset schemas, node schemas,
and mutation plans are `internal`; complete node configuration is
`restricted` even after executable and credential-shaped fields are redacted.
A `public` policy is therefore denied before any of those resources is read,
and an `internal` policy is denied before node configuration is parsed.

Project knowledge is derived from a bounded saved-graph fact, a value-free
`haute.toml` digest fact, and allowlisted ordinary documentation, never from an
assistant-specific context file. Dataset schema facts are retrieved separately
through the schema-only operation, whose exact schema digest participates in
the revision of a plan that uses it.
Every item carries source identity and digest, extraction version, sensitivity,
and evidence class. Unknown sensitivity is `restricted`; natural-language
content remains untrusted evidence. The private `.haute/assistant/knowledge`
cache contains only content-addressed derived index metadata, is excluded from
the project revision, invalidates changed or removed sources, and is safe to
delete and rebuild. Retrieved source digests that affect a plan are included
in that plan's revision inputs.

Provider working tool results and durable session/audit representations are
separate. Persistence retains bounded redacted summaries, stable identifiers,
revisions, decisions, graph-update evidence, and value-free validation path/reason
metadata; it does not copy raw row, source, document, configuration payloads, or
deterministic payload digests into restartable history.

## Approved change contract — ASSIST-A08 qualification gate

Model qualification is a versioned, repeatable evaluation lane, never part of
deterministic unit tests. Held-out scenarios live outside package resources and
contain ordinary project artifacts, requests, semantic assertions, and
adversarial perturbations; their requests and expected operations are not
discoverable through assistant tools, examples, recipes, or the permanent
prompt.

Each trial records Haute version, capability hash, system-prompt hash,
provider, pinned model/version, provider parameters, fixture version, run ID,
cold/warm state, semantic and safety outcomes, provider/tool round trips,
input/output tokens, estimated cost, time to first token, time to validated
plan, and end-to-end latency. Scoring compares graph semantics,
postconditions, unrelated diffs, clarification/recovery decisions, authority,
and leakage outcomes; it does not require exact prose or tool order.

A closed support matrix defines repeated-trial counts plus per-task semantic,
tool-call, token/cost, and cold/warm p50/p95 limits. Unauthorized mutation or
sensitive/secret leakage is zero tolerance and can never be averaged into an
overall score. A provider/model is `qualified` only when attributable live
results meet every threshold; absent credentials or candidate-only evidence
leaves it unqualified rather than silently skipping the gate.

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
- **[execution-engine](../execution-engine/high-level.md)** — `get_node_schema` and
  dry-run schema validation build the lazy plan through the engine's public facade
  (target-node execution, nothing collected); the assistant adds no schema logic of its
  own. Both declare `schema_only`, the engine flag stating that a caller resolves schemas
  and never materialises, so the engine's group-by memory-admission gate — which bounds
  peak memory during materialisation — does not refuse an aggregation neither of them
  runs.
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
- **Malformed assistant configuration** — malformed TOML, an unknown
  `[assistant]` key, an invalid OpenAI `base_url`, or an invalid/missing
  Databricks workspace host raises `ConfigError` or a not-ready reason before
  SDK probing/client construction. Error text names the configuration/environment
  field but never repeats a URL or credential-bearing value.
- **Provider failures** (bad key, rate limit, overloaded, network, malformed stream) raise an
  assistant-specific `HauteError` subclass whose hand-authored message carries the provider
  name and failure class but never the raw provider response body. Databricks alone owns a
  documented, bounded retry for a rate-limit exception raised before a response stream exists:
  two retries on the same model and endpoint, after one and three seconds. Its SDK-level
  retries are disabled so this is one observable bound, not a nested retry cascade. Once any
  stream exists, failures are never retried because replay could duplicate partial text or
  tool calls. Exhausted request failures and stream failures become the terminal `failed`
  event. There is never an unbounded retry or fallback to a different provider or model.
- **Tool-level failures** (unknown node id, invalid op, save-layer validation rejection,
  missing dataset, a schema the engine cannot resolve — unfetched Databricks cache, missing
  trained artifact, invalid node code) are structured tool results returned to the model —
  visible in the chat activity log — not turn failures.
- **Save failures roll back** via the save service's existing staged-write transaction; a
  failed `apply_graph_plan` never leaves a partially-written pipeline, and the error
  (sanitized) is what the model sees.
- **Limits** — the per-turn tool-call cap and wall-clock timeout each terminate the stream
  with a named terminal event stating which limit was hit. Edits already applied remain (each
  was a complete valid save); nothing is auto-reverted.
- **Client disconnect** aborts the provider stream and stops the loop between tool
  executions; an executing save always completes. No orphaned provider streams or
  unmatched persisted tool calls outlive the request, and cleanup failure cannot retain
  the session lock.
- **Working branch not ready** — mutation tools refuse with a named per-state reason
  (read tools still work), and the status endpoint reports mutations disabled with the same
  reason; a rare post-save capture failure degrades to a visible warning in the chat, never
  silently.
- **Unknown session** → 404; **concurrent turn on one session** → 409; both typed, neither
  auto-recovers.
- **Broadcast failures are isolated** — the event bus already isolates subscriber
  exceptions, so a misbehaving WebSocket consumer can never fail a save that has already
  committed.
