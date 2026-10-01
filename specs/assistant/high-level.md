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
  `NodeType`/config-`TypedDict` machinery, the palette defaults, and the source, sink-only
  and pass-through registries the rest of the product dispatches on, plus the palette
  names, one-line purposes and per-type usage notes owned here).
- The assistant's authoring knowledge, shipped as repo-versioned package assets: a
  concise authoring guide, one validated node card per node type served in its node
  descriptor, and discoverable executable project bundles served on
  demand through `get_example`. Bundle inventories are content-addressed; every
  bundle parses and validates, and the declared fast subset executes in installed
  distribution smoke checks.
- Chat sessions: process-local per-pipeline conversation state, bounded durable
  restart history, and separate provider-working/redacted persisted representations.
- The assistant HTTP surface: session list, session create, readiness/status, and the message endpoint that
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
the nested `[assistant.egress]` table requires the exact fields `trust`,
`max_sensitivity`, `allow_project_knowledge`, `allow_executable_source`, and
`allow_row_samples`. A configuration without `egress` is not ready and names
`[assistant].egress` in its not-ready reason. An unknown key raises
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
UI can disable the input with a reason instead of letting a send fail. It
also reports edits as disabled, with a named reason, when the egress policy
denies every project read (`max_sensitivity = "public"`, which external trust
requires) or when Git is unavailable on the host; the status endpoint never
fails for either. Sending a message while unconfigured is rejected with a 400 naming the missing
piece — there is no default provider and no silent degradation.

**Sessions.** A chat session is created explicitly, bound to one pipeline, and held in
process memory as the runtime authority, with every committed turn written through to
`.haute/assistant/sessions/<id>.json` so history survives the constant server restarts of
a locally-run tool. The pipeline is always the one the canvas shows: session create,
session list and every message carry the loaded document's project-relative source file,
and the server never guesses a default pipeline. The server resolves that path inside the
project, requires it to be a discovered pipeline, and binds the session to its canonical
spelling, which the create and list responses echo. The chat list offers only
conversations bound to that source file, and a message whose source file differs from its
session's is refused with 409 naming the chat's pipeline, so an edit can never land in a
file the analyst is not looking at. Session create accepts an optional prior session id: when its
persisted record exists (in memory or on disk) and is bound to the same pipeline, the
session resumes with its transcript, including each completed turn's outcome, returned
for the panel to rehydrate; otherwise a
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
through `get_authoring_guide` only when relevant. Each node descriptor carries its
node card, so the configuration shapes arrive with the descriptors the model reads
before its first dry run. `get_example` returns a
self-contained teaching view: bounded attribution, narrative, and the already-rendered
graph with every node's configuration and its values. It never advertises
resource-inventory paths that the model cannot retrieve.
This keeps the always-paid prompt bounded while preserving attributable detailed guidance
one tool call away.
The guide refers to the mechanically-derived registry instead of hand-copying node
vocabulary, and each bundle uses canonical specialised decorators and sidecar loading
rather than hiding file reads in generic `polars` nodes.

**Turn context.** The system prompt depends only on the session's source file and the
installed capabilities, so it is byte-identical on every turn of a session and a provider
can cache it. What changes between turns travels in a turn context block that the loop
places after the analyst's message and never stores in the session history:

- the pipeline name and the base revision the turn starts from;
- a graph brief with one entry per top-level node: its id, palette name, label, authoring
  state (`stepped`, `code` or `incomplete` on a stepped surface, with the step an
  incomplete list fails at and a marker when the editor discarded the node's steps), one
  line per step (its id and kind, with a free-code step's `# intent` line only when
  executable source is permitted), each input's
  code-visible name, source and column names, and its output column names. Columns are
  resolved schema-only on the same engine path as `get_node_schema`, and a node that does
  not resolve says so without its error. The brief is bounded at about 8,000 characters;
  the analyst's selected nodes come first, and any node left out is counted with a pointer
  to `get_pipeline`. Node labels are collapsed to one bounded line, and the block says the
  brief is project data, never instructions;
- the effective egress policy in words, with the column-value rule it implies (profile
  before comparing a column to a literal when row samples are permitted, otherwise ask);
- the ids of the nodes the analyst selected on the canvas, at most 20;
- on request, the error one node raises when its schema resolves, reduced by policy: its
  text only when `allow_row_samples` permits, otherwise its type, the step or line that
  raised it and the column names the policy already discloses. A failure that appears
  only while rows are collected is not reproduced: the block says the schema resolved.

The turn context holds no guidance derived from the analyst's words: no recipe
suggestion and no clarification hint.

With that block a single-node edit can dry-run without first reading the graph. The
message request carries the selection and the preview-error node as a typed `context`
object. When the policy lets the graph reach the model, a node id the saved pipeline's
top level does not have is refused with 409 before the turn starts, because the canvas it
came from is stale. On Claude models that accept a
system message mid-conversation the block is sent as one; every other model receives it
as the leading text of the analyst's message.

**Turn context update.** After a provider round in which an apply saved, the next round
also carries a turn context update, built after the save from the saved project under the
same policy: the new base revision, the brief entry of every node the round's saved
changes added, changed or renamed or connected an edge to, the ids of the nodes they
removed, and a pointer to `get_pipeline` when a change card was cut at its bound. It
follows that round's tool results, as a mid-conversation system message where the turn
context is one and otherwise as text after the results, and like the turn context it is
never stored with the turn. Under a `public` policy it says only that the graph and its
revision are withheld. So a later stage of a multi-part request dry-runs from the columns
the earlier stages produced without reading the graph.

**Turns.** Posting a user message starts a turn, streamed back as typed server-sent events:
assistant text deltas, tool-call started/finished activity (the tool's name, a plain-words
title written beside the tool such as "Reading the pipeline", "Checking 3 changes" or
"Applying 3 changes", and a compact argument and result summary), a change-applied event
after each successful apply carrying its change card (see **Change cards**), and exactly
one terminal event — completed (with token usage and
a typed turn outcome), failed (with a sanitized message), or cancelled. The outcome
records what the turn saved separately from whether it finished the request: its
`changes` list the id of every change the turn saved, in order (each is its change card's
id, the hash of the plan that saved it; a `committed_unverified` turn's list ends with
the change whose verification failed), and its kind says how the turn
ended: `applied` (at least one change was saved and the model then ended without a
question, a blocker or an unfinished dry-run), `answered` (nothing was saved and the model
replied with no dry-run unfinished), `needs_input` with the model's question,
`blocked` with the sanitized blocker, `committed_unverified` with
the verification error when a save committed but its post-save verification failed, or
`incomplete` with the controller's reason when the model stopped, after one reminder,
with a validated plan unapplied or a failed dry-run uncorrected. A `needs_input`,
`blocked`, `committed_unverified` or `incomplete` turn may follow changes the same turn
saved, and its `changes` then name them; with an empty list nothing was saved. The
outcome is stored with the turn, so a resumed chat shows the same outcome the live turn
ended with. One turn may be in flight per session; a second
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
system prompt tells the model not to end after merely announcing a future tool call: it
completes the dry-run/apply sequence, asks one focused question prefixed `NEEDS_INPUT:` when
material intent is ambiguous, or reports a concrete tool blocker prefixed `BLOCKED:`. When
the analyst delegates a choice ("pick any", "you choose"), the prompt tells the model to
make a reasonable choice, state it, and proceed; it asks only for choices that change the
result materially and that the analyst has not delegated.

The controller is structural: nothing it does depends on the words of the request. It
tracks one fact across the turn's dry-runs, the state the latest one left: a **validated
plan** (the latest dry-run succeeded) or a **failed dry-run** (the latest dry-run failed);
only an apply that saves clears it. When the model ends a
round with text that does not begin `NEEDS_INPUT:` or `BLOCKED:` while that state is
open, the controller sends one internal, non-transcript reminder naming it (apply the
validated plan with its exact hash, or correct and dry-run the failed plan, or report a
question or blocker) and requests one more provider round. A second such end completes
the turn with the `incomplete` outcome, whose detail names the open state, so the
analyst sees that the assistant stopped before finishing rather than a failure or a
false success. A turn with no open state ends `applied` when it saved a change and
`answered` otherwise, so a question such as “Can you
explain the rating step?” or “Why does the join produce nulls?” ends as an answered turn.
This replaces a controller that, once the model attempted any dry-run or apply, required
a successful apply or an explicit marker and failed the turn on a second unqualified
end, and that also retained a recipe route across a chain of `NEEDS_INPUT:` turns by
matching the analyst's words with regular expressions. Its rationale was that a turn
must never claim completion falsely; the typed `incomplete` outcome keeps that
guarantee, while the word matching misrouted ordinary phrasing and turned delegated
choices into questions, and a failed turn hid a resumable state behind an error.

**Several applies per turn.** A successful `apply_graph_plan` does not end the turn. The
model reads the apply's result and the turn context update, and may dry-run and apply
further plans within the turn's tool-call and time budgets, so a request with several
parts (a source, its features, a banding, a rating and a response) is built in one turn
with a change card per saved plan. The turn says what each apply saved through the
change card streamed with it, and adds no text of its own. This replaces a rule that made
a successful apply the terminal mutation outcome: after consuming that provider round, the
controller completed with `applied` "without exposing another tool round in which the
model could repeat or extend the mutation". Repeating is already impossible, because a
plan is single-use: applying its hash again returns `plan_already_applied` with no write,
and any further change needs a new dry-run against the new revision (see **Undo and
Compare** for the one case where that dry-run issues the same hash). Extending is what an
analyst asks for when a request has several parts; the rule silently dropped every part
after the first and made a pipeline take one message per stage. The guarantee that
mattered, that a turn never claims more than it saved, now rests on the outcome's
`changes`, which list exactly the saved changes whatever the model says afterwards.
The change cards also replaced a fixed confirmation sentence ("Graph changes applied
successfully.") that the controller appended to the transcript and the provider history.
The sentence existed so the turn ended with a deterministic success statement rather than
model prose that could overclaim; each change card is that statement, built from what was
saved, while the sentence told the analyst nothing about what changed.
An apply that commits its save but fails post-save verification (`verification_failed`)
still ends the turn at once: later tool calls in its round are ignored, the controller
emits a deterministic statement that the changes were saved but not verified and
completes with the `committed_unverified` outcome, so the model cannot apply a further
plan over an unverified save and no later text or blocker can report that nothing
changed.
A failed dry-run may be corrected while each correction makes progress. Each plan
allows up to four failed dry-runs across both dry-run tools, counted from the start of
the turn or from the latest apply that saved (a saved plan is progress, and a request
that failed against the earlier revision may succeed against the new one), and the
controller ends the turn earlier when the model makes no progress: it resends a plan
identical to one that already failed since then, or the same diagnostic (its code,
`where` and `fix`) repeats while the operation it points at is unchanged. When either
happens the controller terminates the tool loop itself with a `BLOCKED:` outcome naming
why it stopped and the latest stable error code, repeating that dry-run error's message
in the same bounded form the chat's tool row showed (so it carries nothing the tool
result had not already shown the model), and stating that no graph changes were applied,
or, after earlier saves in the turn, that those changes stay saved and no further change
was applied; the provider cannot continue guessing until the global tool-call limit is
exhausted.
This replaces a rule that allowed "one materially corrected retry" so that the
provider "cannot continue guessing". That rule treated every second failure as
guessing, but a plan often holds several independent, fixable faults that a dry-run
reports one at a time, so a mid-tier model that corrected the first fault correctly
was blocked on the second. Guessing is a plan that stops changing, and that is what
the progress rule measures; the four-attempt ceiling and the tool-call limit still
bound a model that changes its plan without converging.
An apply result that saved, verified or not, retains its change record in neutral
history; resume rebuilds the same change card from that durable record, in its original
position after the apply's tool row.

**Change cards.** After every successful apply the service builds a value-free change
record from what was saved: the actual semantic diff, the graph before the plan and the
graph reparsed after the save. It holds its id, the hash of the plan it saved (a plan
applies once, so the id names one change); the plan's summary and assumptions (below); one
chip per node the plan added, changed, removed or renamed, naming the node, its palette
type name (for example "Polars" or "Banding"), what happened to it, the earlier name of
a renamed node, the configuration fields it changed in plain words derived from their keys
(`outputColumn` reads "output column"), and for a stepped node the kinds of its steps after
the save and how many steps the plan changed; the edges added and removed; whether the
preamble changed; the save's warnings; the Git commit the save made with its parent; and
`revision`, the editor document revision the save produced (the revision the canvas and
every save precondition compare). When a save commits but its post-save verification
fails, the apply's error result still carries a change record, built from the graph
before the plan, the graph the plan computed and the plan's validated diff (the save
could not be reparsed and compared), so the analyst can undo exactly that save; its
change-applied event streams like a verified one and the turn's `changes` name it.
Chips and edges are bounded at 50 each, with a flag when more were cut. The record never
holds a configuration value, a step's free-code text or `# intent` comment, a column
value or an error message: it names what changed, not what it changed to. There is no
second, hand-maintained list of editor labels; a field reads as its key in words.

`dry_run_graph_edits` takes a required `summary` of at most 400 characters, saying in
one or two plain sentences what the plan does, and an optional list of up to five
`assumptions` the model made (each at most 400 characters, with no control character other
than whitespace). The bound fits the summaries models write: the summary is presentation,
never authority, and a tighter bound refused ordinary summaries in live runs.
They are stored as the plan's receipt in the
plan store beside the plan, outside the hashed plan authority, so wording never changes a
plan hash; a later identical dry-run replaces the receipt of a plan not yet applied. A
recipe plan's receipt is its recipe's index summary with no assumptions. The change card
shows both as written. The change's headline is derived from the summary: its whitespace
collapsed to one line and, when that is longer than 100 characters, cut at the last word
boundary within them with an ellipsis ending the 100 (a single word longer than that is
cut inside it). The headline is the Git commit message of the save the apply makes, in
place of the default message that names the changed files.

**Undo and Compare.** The analyst can undo an assistant change from its card with one
click, and compare the pipeline before it with the current one. Undo
(`POST /api/assistant/changes/undo` with `session_id`, `change_id` and the canvas
`source_file`, bound like every assistant request) reads the graph at
the change's parent commit (the read-only historical parse the comparison view uses) and
saves it through the same transactional save service as a forward save, whose Git commit
message is `Undo: ` and the change's headline. The save restores the parent's files byte
for byte, removing a configuration file the change added. Undo is allowed only while the
pipeline's current document revision is the change's `revision`, so only the latest
change to the file can be undone, and never over a later save by the analyst or another
change: otherwise it is refused with 409 naming why, as it is while a turn of that chat
is running and when the change has no parent commit (it was not saved to Git). The change
is found by id in the chat's stored history, the latest record when the same plan was
saved more than once. The undo is recorded in the chat as a note after the latest turn;
the transcript shows it, and the next turn's context tells the model that the analyst
undid that change, so the model does not assume its earlier change is still in place.
A plan's single-use record never outlives the revision it applied to: after an undo
restores that revision, dry-running the same change again issues a fresh plan with the
same hash, which applies once more. This amends the rule that a stored plan "is
single-use; a repeated apply returns `plan_already_applied`" even after a fresh
dry-run. That rule exists so a plan cannot be applied twice to the revision it was
validated against; the hash covers the base revision, so a fresh dry-run that produces
an applied plan's hash proves that revision is current again, and refusing it left the
analyst unable to redo a change they had undone. A repeated apply without a fresh
dry-run is still refused. Compare opens the existing read-only comparison view with the
parent commit on the historical side; it needs only that parent.

**Assistant updates carry their origin.** The `pipeline.document.update` an apply or an
undo publishes carries `origin`: `kind` `assistant`, the `session_id`, the `change_id`
and `node_ids`, every node id the change names (its chips, a renamed node's earlier id
and both ends of each added or removed edge). The node ids travel in the origin because
the canvas receives the update before the change card streams. Updates from the file
watcher and resyncs carry no origin.

**The tool surface** (complete in v1):

- `get_capability_manifest` / `get_capability_descriptors` — the compact installed
  manifest and a bounded ordered batch of complete closed node, operation, or recipe
  descriptors. A batch has one kind and one to twelve unique ids, eliminating per-node
  tool-call pressure for complex authoring. Every returned descriptor is materialised as
  ordinary finite JSON containers before it crosses the tool boundary; immutable registry
  wrappers never leak into provider results.
- `get_authoring_guide` — the complete attributable packaged guide, retrieved on
  demand rather than embedded in every request, with the structured step grammar
  (each step kind's fields and the closed step vocabularies) derived from the step
  renderer.
- `get_pipeline` — the saved graph: nodes (id, type, name, config summary, and on a
  stepped type its authoring state and value-free per-step summary, as in the graph
  brief), edges,
  a preamble-presence/digest summary (never executable source), and which
  singletons exist.
- `get_node_config` — one node's restricted structured config. Credential-shaped
  fields are always redacted; executable code (`code`, `preamble`, `query`,
  `script`, at any depth, so a free-code step's `code` too) is redacted unless
  the policy's `allow_executable_source` permits it. The whole result is still
  treated as `restricted` and is refused unless the configured policy permits
  that class.
- `list_datasets` / `get_dataset_schema` — the data files visible to the project
  and a file's column names and dtypes, with no preview collection or row values.
  Listing names visible subdirectories and accepts a bounded recursive traversal. Recursive
  results are deterministically ordered and report truncation rather than silently omitting overflow.
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
  Its bounded collection runs in the editor's interactive preview worker under the
  ordinary preview admission and the worker's memory cap, so frames downstream of joins
  and aggregations profile like any other, and a stopped turn stops its profile.
  The turn context states the project's effective egress policy in words and requires a
  profile before a literal comparison only when `allow_row_samples` permits one; otherwise
  it tells the model to ask the analyst which values to match, beginning `NEEDS_INPUT:`.
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
  pipeline with each node's configuration shown whole, values included, rather than as
  its key names, without inaccessible resource paths.
- `plan_recipe` — accept one flat, recipe-discriminated invocation, including an optional
  downstream response-output name plus explicit selected columns, expand it deterministically, and return
  only an opaque content-addressed recipe-plan receipt; it never writes. Every turn receives
  the same complete closed discriminated union. The explicit structured `recipe_id`, not a
  natural-language classifier, selects the branch that the executor validates and plans.
- `dry_run_recipe_plan` — consume only that hash server-side and produce the same exact
  revision-bound plan as the primitive dry-run without asking the model to copy, extend, or
  reconstruct nested recipe JSON.
- `dry_run_graph_edits` / `apply_graph_plan` — validate an ordered primitive operation
  batch into an exact revision-bound semantic plan, then apply either kind of stored plan
  once using the exact returned plan hash. These are the only provider-visible mutation
  operations.

**Mutation semantics.** `dry_run_graph_edits` loads a canonical saved-state
snapshot and passes the closed operation batch through one `build_verified_plan`
pipeline. That pipeline parses and normalizes the operations once, applies them
to a deep copy, invokes the save service's public no-write validation, evaluates
the closed structural postconditions, and resolves affected terminal lazy
schemas without collecting rows or invoking sinks. It stores an immutable plan containing the base
revision, semantic diff, verification tier, schema evidence, and plan hash, with the
plan's summary and assumptions as its receipt beside it. The stored diff is bounded per
category but carries complete counts, an explicit truncation flag, and a digest over the
complete diff. Operations later in a batch may reference nodes created earlier by a
batch-local ref.

Dry-run and apply results are compact, because the model reads them in every later round
of the turn. A dry-run returns the plan hash, the number of operations, the verification
tier, an evidence summary (how many schemas resolved, and which inputs were resolved from
an inferred or declared schema), the validation warnings, and the plan's change chips and
edges built by the same builder as the change card. It does not echo the normalized
operations, which the model wrote, or the revision, digests and postconditions, which only
the server reads. An apply returns the number of operations applied, the
verification tier, the evidence summary of the post-save verification and the change
record, whose id is the plan hash, stated once; the expected and actual diffs, which a
successful apply proves equal, are not repeated. In both, a chip field that holds its default (no earlier name, no changed
fields, no step list, no changed steps), an empty edge or warning list and a false flag
are left out, and the evidence summary names inferred and declared inputs only when
there are any. A four-node batch's dry-run and apply results each stay under one kilobyte.

`apply_graph_plan` accepts only that stored plan hash. Under the shared save
lock it reloads every revision source, rejects stale evidence, and passes the
stored normalized operations through the same `build_verified_plan` pipeline.
It requires byte-for-byte-equivalent canonical plan evidence and the same hash,
checks the plan's one-use authority, then commits once
through the transactional save
service. The service reparses, compares the actual and expected visible diff
and complete-diff digest, evaluates postconditions, re-proves the bound schema
evidence, reports the combined evidence and result revision, and publishes the ordinary
`pipeline.document.update` event. Errors before
save write nothing; a failed pre-commit application marks that plan attempt
aborted, so it cannot be applied directly again. A fresh identical dry-run may
revalidate and reissue the same deterministic hash. A failure discovered after
commit reports the committed state and ledger evidence without replaying the
mutation.

Assistant-authored batches must leave every newly added node connected in the
resulting graph. New Polars logic is written as steps with a free-code card, and the
system prompt, the node descriptors and the authoring guide teach the same form: on a
Transform `[source, free_code]`, whose source step names the incoming edge that becomes
`df`, and on every other stepped surface (Data Input, Load File, Rating Step, Model
Scoring, Expander, Explore) `[free_code]`, because the surface binds `df` itself. Every
step carries a non-empty `id`. The code transforms `df` and assigns the result to `df`;
it reads other inputs by their edge names only on a Transform and a Load File (where
`df` is the first input and the loaded object is `obj`), and elsewhere sees only `df`,
the frame the node produced. It starts with a one-line `# intent` comment, which the
step builder shows as the card's title. A hook that needs no post-processing keeps
`steps: []`. Existing structured steps keep their ids and order, and code-mode nodes
keep code editing: code on a code-mode Transform starts from a named input (each input
is named by its upstream node, and `df` is only the output variable) and must assign
the transformed frame to `df` or return it; immutable
expressions whose results would be discarded, and code that reads `df` before
an assignment that definitely dominates that read across control flow, are
rejected during dry-run. The derived input name `df` is reserved and rejected
rather than silently weakening the output-only contract. A step list the batch
authors is rendered by the product's step renderer, and each free-code step in it is
checked against its surface: a bare frame method call whose result is discarded is
refused with the step, and on a surface whose code sees only `df` a step that reads an
input or upstream node by name is refused with "<Surface> code sees only df".

**Writes to stepped nodes land in the saved config or fail naming the fix.** A node
the assistant adds starts from the palette's config for its type (`node_defaults.json`),
so it matches a node the analyst drops from the palette: every stepped type opens in the
step builder with `steps`, and a Data Input carries its `inputType`. The assistant
writes new Polars logic as steps and never changes how an existing node is authored: on
a node that holds steps it refuses a `code` write or the removal of `steps` (which would
switch the node to code mode, an analyst action in the editor), on a code-mode node with
code it refuses `steps` (which would discard that code), and `add_node` of a stepped type
refuses `code`. Code-mode nodes keep code editing. Each refusal names the free-code form
for the node's surface: `[{"id": "start", "kind": "source", "input": "<edge name>"},
{"id": "logic", "kind": "free_code", "code": "..."}]` on a Transform and
`[{"id": "logic", "kind": "free_code", "code": "..."}]` on every other stepped surface.
An existing stepped node is changed step by step with `edit_steps`: insert a step
after a step id (or at the start), replace a step by id, or remove one, with ids
assigned to new steps that omit one. Only the steps it names are sent, so a free-code
step whose code the policy masks stays as saved, and such a step can be replaced or
removed as a whole but never edited in place. Its errors name the step id; it is
refused on a code-mode node, whose `code` is edited instead.
Every key an operation writes must hold the written value once the node's config is
materialised, and a written step list must render; otherwise the plan fails with
`op_not_applied`. Dry-run then generates the planned source in memory and reparses each
stepped node the plan touches through the parser, so steps a save would discard fail the
plan before apply. After save, a per-node `node_config` postcondition checks each added
or updated code-carrying node's authored config against the reparse: its `steps`, or its
normalised `code` on a code-mode node.

**Modelling and Load File nodes the assistant writes are ready to use.** Save validation
refuses a malformed modelling value for every author (see
[modelling](../modelling/high-level.md)), so the dry-run refuses it too. The editor lets an
analyst save an unfinished node; the assistant may not leave one. For each Modelling or
Load File node a plan adds or updates (instances excepted), the dry-run also checks what
save leaves to training or execution, after the plan's schemas resolve. A Modelling node
needs a target, a complete training objective (`training_objective_issue`: a loss, or a
GLM family and terms) and a valid evaluation object (`parse_evaluation_config`, which
training also requires), and its target, weight, offset, identifier, evaluation and feature
columns (a GLM's term columns included) must exist in the input schema the dry-run
resolves, checked by the function training preparation runs on the materialised schema. A
Load File must load its `path` as its declared `fileType` exactly as execution loads it:
with empty steps the node passes its input through and never loads the file, so schema
resolution alone does not prove it. A failure is a structured `node_not_ready` error naming
the node and the product's message, and no plan is stored. A node the plan does not add or
update is never checked, so an analyst's unfinished node does not block an edit beside it.

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

**A tool error says how to fix it.** Every tool error carries a stable `code`, a
`message` and `retryable`, which is false only when no corrected call can succeed in
the turn (an internal failure, a policy refusal, an interrupted call, a spent dry-run
budget, a save that committed unverified, or a result too large for the model's
context). A failure located in the submitted plan carries `where`: the operation's
index, the node, and the config field or step id involved. Every operation validation
failure (`invalid_ops`) and every `schema_unresolvable`, `op_not_applied`,
`node_not_ready`, `rename_has_consumers` and `unknown_tool` error carries `fix`, one
concrete correction. When `where`
names a node, the error carries `context.inputs`, each incoming input's name and its
column names; column names are schema metadata and never row values, and the dry-run
and apply tools run only under a policy that permits saved project metadata (the same
permission `get_node_schema` needs), so they disclose nothing that tool would not.
`did_you_mean` lists close matches to a misspelt name, drawn only from names the error
may already disclose: those input and column names, the node ids of the pipeline and of
the plan for an unknown node reference, and the tool names for an unknown tool. An
operation naming a node that does not exist says why and how to correct it: a ref
written without its `$`, a node the plan adds only later (move that `add_node` earlier),
an undeclared ref, or a node no operation adds, since each dry-run is a whole plan and
a node a failed dry-run proposed was never kept. The reference is not guessed for the
model; the error names the nodes the plan adds, by id and ref.

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
  extra, because a feature the product leads with must not require a second
  install step. The SDKs are still imported lazily at the adapter seam, so importing Haute
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
  affordable nested shape survive. When the same property is an array of different closed
  object variants, their item fields are merged by the same rule instead of discarding the
  item contract; shared requirements remain required and the variant descriptions are retained.
  Projection has a forty-property budget per tool; this is
  large enough for the complete structured recipe union without request-dependent schema
  narrowing, while remaining an explicit bound. A composition that
  would exceed it remains a generic typed container. Unsupported validation vocabulary is
  omitted. The complete operation schema remains available through batched capability
  descriptors and remains the sole execution-time authority, so portability never weakens
  validation. Some Databricks-hosted OpenAI-compatible models encode function
  arguments whose declared type is an array, object, boolean, integer, or number as a JSON
  string. The Databricks adapter decodes only valid, correctly typed, schema-declared
  top-level values. A field's declared type comes from the tool's canonical top-level
  properties or, for a tool whose input is a closed object union such as `plan_recipe`
  (shown to the provider as one merged object whose `rules`, `tables` and
  `output_columns` are arrays), from the single canonical branch its discriminator value
  (`recipe_id`) selects. An unknown or ambiguous discriminator selects no branch, and
  nothing is decoded. Numeric results must be finite, booleans never satisfy integer or
  number declarations, and string or null declarations are never decoded. The adapter
  does not infer a type from an undeclared or ambiguous schema. An
  invalid or wrong-type encoding is left unchanged for the canonical tool validator to
  reject as a structured, recoverable tool result; it is never guessed, repaired, or
  executed, and it does not terminate the provider stream.
- **The same save path as the GUI, not a parallel mutation engine.** Haute's philosophy is
  a single execution engine and a single write path. Because every assistant mutation goes
  through the transactional save service, the assistant cannot produce any on-disk state the
  GUI could not; validation, codegen, sidecar layout, rollback, and git-ledger capture are
  inherited rather than re-implemented — and the git ledger is the undo story for
  direct-apply: a change card's Undo saves the change's parent commit forward through
  that same save service.
- **Deterministic broadcast, not watcher reliance.** Assistant saves mark self-writes (so the
  debounced watcher stays quiet) and then explicitly recover and publish
  `pipeline.document.update` on the event bus. Relying on the watcher to notice the write would couple canvas liveness to
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
  Serving them rendered as graphs (not raw source) keeps the few-shot graph shape identical
  to the `get_pipeline` format the model works in. An example's node configurations carry
  their values because they are library content; a live pipeline's are project data, read
  whole only through `get_node_config` under the egress policy.
- **Node cards teach values, and CI executes them.** Key names and a closed schema do not
  say how a breakpoint boundary, an output path or a GLM offset is written, and those are
  where models guess. One hand-authored card per node type holds a minimal and a realistic
  configuration with the meaning of each field, and CI applies and executes every one
  through the real application service and engine, so a card cannot drift from its
  validator.
- **Sessions persist per clone, in `.haute/`.** Haute's server is a locally-run
  distribution vehicle, not a hosted service — users restart it constantly, so
  process-local-only chat would lose every conversation at each restart. Committed turns are written as
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

## Capability registry

The node catalogue is one view of a versioned, library-owned capability
manifest. The manifest is the assistant's source of truth and contains:

- the installed Haute version, manifest schema version, deterministic
  capability hash, installed I/O formats and optional engines, and enabled
  feature flags;
- one closed descriptor for every `NodeType`, including its resolved config
  JSON Schema, required/optional fields, defaults/enums, nested and
  discriminated branches, runtime-derived decorator/config/sidecar/singleton
  facts, ports/cardinality/schema effects, execution and side-effect classes,
  completeness-checked semantic guidance, and, for a stepped type, how its
  steps start, which inputs they see and the step list that writes new logic,
  derived from the step builder's surface table, and its node card (below);
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

**Node cards.** Every `NodeType` has exactly one packaged node card, and its
descriptor serves it as `card`. A card for a type the assistant can author holds the
meaning of each configuration field, the input edges and column dtypes its
configurations assume, and two configurations, `minimal` and `realistic`, each with its
intent, its complete config and the columns its node produces. Together the cards teach
breakpoint banding on numbers and on dates with an open-ended last band, categorical
banding with string `{value, assignment}` rows, Quote Response output paths rooted at
`$[:]`, a Load File whose free code reads the loaded `obj`, a Transform written as
`[source, free_code]` reading a second input by its edge name, a Rating Step with
several tables and a combined output, a nested API Input, a Source Switch, a Model
Training node for a tree family (loss and params) and a Poisson GLM with an exposure
offset under a log link, Model Scoring from a training run, an Expander with its
`stepCount`, an online and a ratebook Optimisation (the ratebook with a Banding source),
online and ratebook Apply Optimisation, and Data Input, Data Output, Constant, Edge Join
and Explore configurations. A field meaning also carries a material choice its node
needs: the Rating Step card says its factor values, relativities and missing-factor
value come from the analyst, are never invented, and are asked for with `NEEDS_INPUT:`
unless the analyst delegated them. A card for Submodel or Port states that the assistant
cannot author it, in the words of the operation layer's refusal. Cards are library
content, never project data. A card file also carries a synthetic fixture (tiny rows,
files, surrounding operations) that is test evidence and never reaches the model: CI
writes each configuration into a fresh project with that fixture, dry-runs and applies
it through the application service as an assistant plan, executes the node through the
execution engine, trains a Model Training card and solves an Optimisation card through
their job routes, and checks the produced columns and, where the card declares them,
the produced values. At import, every card configuration's keys must lie in its node
type's closed config schema.

The permanent prompt contains only manifest identity and a compact node,
operation, recipe, and example index. Full descriptors are retrieved through a
bounded capability-query operation. An unknown descriptor kind or identifier
returns `unsupported_capability`; malformed closed input returns
`invalid_capability_query`. These are tool-level failures and never trigger a
prompt-owned fallback vocabulary.

The manifest is the assistant's only node catalogue. The former `list_node_types`
tool is removed; a call to it is refused with `tool_removed`, naming the manifest
operations that replace it.

## Application services and mutation authority

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
When the model inspects datasets again (`list_datasets` or `get_dataset_schema`),
dataset-schema evidence whose file no longer exists is dropped, so a dataset renamed
after it was inspected blocks planning only until the model looks again. A missing or
changed evidence file fails planning with `project_source_missing` or
`stale_project_evidence` naming the project-relative file and the call that refreshes it.

`dry_run_graph_edits` accepts the closed primitive operation union, explicit
postconditions, and the plan's summary and assumptions. The plan it builds holds the
normalized operations; the base revision; a stable plan hash; semantic node, edge,
configuration, preamble and sidecar changes; validation warnings; resulting graph shape;
affected capabilities; the deterministic egress class; and the strongest bounded
verification tier the affected capabilities declare; the tool returns the compact view of
it described under Mutation semantics. The plan hash is
canonical over all facts that can affect authorization or verification.
Canonical request validation recognizes closed object unions discriminated by fields such
as `op` and `kind`. It selects the declared branch before validation so retry feedback
names the exact safe schema path and a stable value-free reason, rather than collapsing all
branch failures to a generic `oneOf` error. Those fields are retained in redacted history;
submitted values are not. The provider's primitive-operation branches are projected from
the same `_wire_ops` model declarations that parse the canonical operation vocabulary;
field membership, requiredness, discriminator values, and node-type enums are therefore not
maintained in a second hand-written schema.

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
recomputes the normalized plan, warnings, schema evidence, and hash through the
same `build_verified_plan` path used by dry-run, checks exact-plan authority, and
commits once through `SavePipelineService`, with the change headline as the Git
commit message. A plan is single-use; a repeated
apply returns `plan_already_applied` with no write, until a fresh dry-run of the
same plan succeeds against its base revision again (after an undo; see **Undo and
Compare**). Any changed authority fact invalidates the plan.

After save, the service reparses and computes the actual semantic diff, checks
the declared postconditions, runs the strongest permitted local verification,
and compares the result to the dry-run. V1 plans that affect executable flow
declare `schema`, which combines reparse/save validation, exact diff,
structural-postcondition evidence, and exact lazy-schema evidence. A mutation
with no executable target to resolve (for example deleting the only node) may
declare `structural`. Neither tier's verifier collects rows or invokes
external writes, and no row tier is selected implicitly; the schema tier does
run the node code it resolves, which is why such a plan's egress is
`schema-resolution` rather than `none`. A local file Data Input with no snapshot yet (for
example one the plan adds) is resolved at the IO layer's inferred schema tier: its schema
comes from the format's lazy scanner with the node's own settings and bounded type
inference, nothing is collected and no snapshot is written. The plan keeps the `schema`
tier, and its evidence carries one `input_schema_inferred` record per such input naming
the node, the tier `inferred`, the format, and the inference row bound (`null` for file
metadata or a declared schema), so the plan says which schemas were inferred rather than
read from a snapshot. An input that cannot be scanned this way (an eager-only format, a
database or remote source, or a configuration only the eager reader accepts) fails the
dry-run with the remedy to preview the input first. A structured Quote Input table with
no snapshot yet (for example in a Quote Input the plan adds, alone or behind a Source
Switch) resolves at the IO layer's declared schema tier: its schema is the node's own
request contract, each selected column's declared type, so no request data is read,
nothing is collected and no snapshot is written. The evidence then carries one
`input_schema_declared` record per such table naming the node, the table, the tier
`declared` and the table's declared column count. A contract with a column that declares
no type is refused by the contract validator naming the column, as a preview would be. Results name the tier
that actually ran and include an evidence summary and the change record with its ledger
commit, the commit's parent and warnings. Structural or plan verification is
never described as row-level, model-quality, pricing, or commercial proof.

Stable application errors include `invalid_plan`, `op_not_applied`, `stale_revision`,
`stale_project_evidence`, `plan_not_found`, `plan_expired`,
`plan_store_busy`, `plan_aborted`, `plan_already_applied`,
`authority_denied`, `postcondition_failed`, and `verification_failed`.
All are returned before a write except verification
failure, which reports that the transactional save committed, carries that save's
change record, and preserves the ordinary ledger/undo path.

## Recipes and executable bundles

Recipes are versioned deterministic planners over the canonical primitive graph
operations. Their descriptors declare closed argument schemas, unresolved user
decisions, preconditions, allowed operation kinds, postconditions, linked
examples, and stable failures. Planning never writes, and every planner output
is parsed by the same primitive validator before it can enter dry-run or apply.
A recipe cannot grant authority, choose an omitted pricing assumption, or
bypass revision, egress, save, or verification policy. The categorical-banding recipe
uses closed rules containing exactly a non-empty string `value` and non-empty
`assignment`. Execution casts the banded column to text before matching, so a rule value
is written in that text form: a boolean column's values are `"true"` and `"false"`, an
integer column's values are their digits (`"3"`), and a string column's values are
matched exactly. A boolean or numeric rule value is refused at planning with a message
that states the text form it must take, and two rules with the same value are refused
there too, so a recipe never saves rules that match no row or that collide once saved.
There is no numeric-banding recipe. The reference-join recipe offers the join modes
`inner`, `left`, `right`, `full`, `semi`, and `anti`; it always joins on explicit key
lists, so it never offers `cross`. Recipe argument
descriptions distinguish graph node names from output column names. The rating-step recipe
uses a provider-facing positional contract instead of canonical dynamic row keys: each
table declares one to three ordered `factors`, an `output_column`, a finite
`default_value`, and closed entries containing aligned `factor_values` plus a finite
numeric `value`. Optional combined outputs use closed `output_column`, `operation`,
and finite `base_value` fields. The planner validates alignment, scalar values,
uniqueness, supported operations, and canonical rating normalisation before emitting
Haute's dynamic-key sidecar form.
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

No recognizer reads the analyst's words: the model selects a recipe or primitive
operations from the recipe index, the node cards and the descriptors. Every request
receives the same provider-visible tools and schemas, and the source-bound executor
constructor receives no user request text at all, so equivalent phrasing, another
locale, or a composed request remains free to submit any valid structured recipe or
primitive plan.

The fail-closed authority is the provider operation descriptor, the wire model's closed
discriminated union, recipe argument validation, graph-semantic replay, exact plan hash,
stale-snapshot check, and single-use receipt. Explicit names and apparently missing material
choices remain prompt and node-card guidance: the model must not invent them, while a
complete structured call is accepted on its own terms. Invalid or
incomplete calls fail the canonical schema or recipe validator. The lexical-only
`recipe_route_required`, `recipe_route_mismatch`, `recipe_name_mismatch`, and
`material_input_required` executor verdicts do not exist.

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
valid projects with machine-readable invalid/adversarial cases; their
rejection checks run in the ordinary suite, so validation never requires
importing malformed or executable hostile source.
Every manifest also declares a required boolean `teaching`. A teaching bundle
is one the model learns from: it is listed in the system prompt's example
index and served by `get_example`. A bundle with `teaching: false` is a test
fixture (the deployment-safety and invalid/adversarial bundles): it is
validated and materialisable for its specialist checks exactly like a
teaching bundle, but it is absent from the example index, and `get_example`
refuses its name as `unknown_example`.
Every bundle is a project the editor accepts: it parses, regenerates through
the save path's codegen, and accepts a no-op edit through the application
service's dry-run. Bundles wire nothing out of a node type that has no output
(Model Training and Optimisation are terminal branches), name no submodel
occurrence like a node inside its definition, and give a ratebook optimiser a
Banding node as its rating factor source.
Golden output arrays are positional contracts. A bundle whose operators do not
guarantee row order must impose an explicit stable order in its production
pipeline before asserting those arrays; packaging checks never sort observed
results to make a nondeterministic fixture pass.
Teaching bundles are indexed by the system prompt and
`get_example`; held-out evaluation fixtures live outside assistant package
resources and cannot be enumerated through those surfaces. Installed
distribution smoke checks enumerate and validate every bundle and execute the
declared fast subset.

The assistant is evaluated offline by replaying reference trajectories through the
real tools in CI, live against a configured provider on demand, and by repeated
qualification trials; the cases, tiers, scoring layers and execution boundary are
specified in [the assistant evaluation](evaluation.md).


## Egress and project knowledge

`[assistant.egress]` is required and closed. It contains exactly `trust`
(`local`, `organization`, or `external`), `max_sensitivity` (`public`,
`internal`, or `restricted`), and the required booleans
`allow_project_knowledge`, `allow_executable_source`, and
`allow_row_samples`. A configuration without `egress` is not ready and
names `[assistant].egress` in its not-ready reason. Local endpoints must be
loopback; organization and external endpoints must use HTTPS; external policy
is public-only and cannot enable executable source or row samples. Project
configuration may narrow but never widen these class ceilings.

Schema inspection is schema-only: assistant schema results never contain
preview rows. Raw rows are unavailable through ordinary read tools, and
executable source is available only through `get_node_config` when
`allow_executable_source` permits it. Resolving a schema still runs the
preamble and node code over the project's inputs, and that code, or Polars
itself when a cast or computation meets a bad value, can put row values in an
exception. Unless `allow_row_samples` permits row samples, such a failure
reaches the model only as its exception type, the line or step that raised it,
and those column names it names that the egress policy already discloses: the
failing node's schema metadata, text the model itself submitted in the plan,
and saved preamble or node code only when `allow_executable_source` permits
it. A plan says truthfully whether building it ran node code over data. Any future sensitive read must first produce a closed disclosure
bound to endpoint identity, policy hash, project revision, category, resource,
fields, sensitivity, and row limit, then consume same-session confirmation
exactly once. Credentials, credential references, hidden paths, and restricted
values never enter a provider-visible or persisted tool payload.

The tool boundary assigns minimum sensitivity before performing a project
read: saved graph topology, dataset listings, dataset schemas, node schemas,
and mutation plans are `internal`; complete node configuration is
`restricted` even after executable and credential-shaped fields are redacted.
A `public` policy is therefore denied before any of those resources is read,
and an `internal` policy is denied before node configuration is parsed. The
turn context's graph brief, base revision, selection and preview error are
`internal` too: under a `public` policy the block carries only the policy.

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

## Provider qualification gate

A provider and model are qualified only by attributable live trials that meet
every threshold of the closed support matrix; the lane and its scoring are
specified in [the assistant evaluation](evaluation.md#tiers).

## Interactions

- **[server-api](../server-api/high-level.md)** — the assistant router is included into the
  same FastAPI app (session middleware, sanitized-error conventions apply); request/response
  models live in the shared `schemas.py` contract module; mutations run under the shared
  `save_lock` through the transactional save service; broadcasts publish the existing
  `pipeline.document.update` event on the shared event bus; self-write marking prevents
  watcher feedback.
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
  mutations as ordinary `pipeline_document_update` frames over `/ws/sync`; its dirty-state
  banner and apply/rollback behaviour are unchanged. A frame's assistant `origin` makes
  the canvas ring and centre the changed nodes instead of fitting the whole graph.
- **[git-integration](../git-integration/high-level.md)** — Undo reads the change's parent
  commit through the read-only historical parse, and assistant saves pass their headline
  to `commit_save` as the commit message.

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
  name and failure class but never the raw provider response body. Databricks owns a
  documented, bounded retry for a rate-limit, connection, or timeout exception raised before
  a response stream exists: such a failure produced no partial output, so it is safe to send
  again. It makes two retries on the same model and endpoint, after one and three seconds.
  Its SDK-level retries are disabled so this is one observable bound, not a nested retry
  cascade. The direct OpenAI and Anthropic adapters keep their SDKs' own bounded request
  retries (two), which already cover the same pre-stream connection and timeout failures,
  and have no adapter retry on top. Every OpenAI-compatible client is built with explicit
  timeouts: a thirty-second connect timeout, long enough for a serving endpoint's cold
  connection, and a read timeout equal to the turn timeout. Once any stream exists,
  failures are never retried because replay could duplicate partial text or tool calls.
  Exhausted request failures and stream failures become the terminal `failed` event. There
  is never an unbounded retry or fallback to a different provider or model.
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
- **Unknown session** → 404; **concurrent turn on one session** → 409; **a message for
  another pipeline than its session's** → 409 naming the chat's pipeline; **a source file
  that is not a discovered pipeline** → 404; all typed, none auto-recovers.
- **Broadcast failures are isolated** — the event bus already isolates subscriber
  exceptions, so a misbehaving WebSocket consumer can never fail a save that has already
  committed.
