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
  demand through `read_reference`. Bundle inventories are content-addressed; every
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
  deliberately absent from the v1 tool surface (see Design rationale). The one execution
  the assistant may cause beyond schema resolution is the bounded, value-free data check
  that `[assistant.egress].allow_aggregate_statistics` authorises, specified under
  [Data checks](#data-checks): it runs after a dry-run, and over a saved node's lineage
  when the model asks for `inspect_node`'s data part. It is not a tool of its own.
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
`max_sensitivity`, `allow_project_knowledge`, `allow_executable_source`,
`allow_row_samples`, and `allow_aggregate_statistics`. A configuration without `egress` is not ready and names
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
session resumes with its transcript, including each completed turn's outcome, and its
build plan, returned for the panel to rehydrate; otherwise a
fresh session is created — resume is an offer, never an error. A mismatched
offer is rejected before the candidate is promoted or touched in the live LRU,
so asking to resume the wrong pipeline cannot evict a useful session. A *message* against an
unknown session id still fails with 404 rather than silently creating a fresh one.
Retention is bounded
everywhere: the provider request carries the current turn whole and compact records of
earlier turns within a character budget (the system prompt carries only the compact
manifest identity/index; see Conversation history), stored history is capped per session, and
live sessions are LRU-capped — evicting an idle session drops only its in-memory record;
its persisted file revives it on the next lookup, so eviction is invisible to the client.
A session with a running turn is never evicted. Persisted files have their own cap:
beyond it the oldest files by session-file modification time are pruned at session creation, and a pruned or
never-persisted id 404s on message send (and starts fresh on session create). Session
creation also removes abandoned atomic-write `<id>.json.tmp` files, so a process crash
during persistence cannot grow the session directory outside that bound.

**Authoring knowledge.** Every turn's system prompt carries the compact capability
identity plus node, operation, recipe, and example indexes. Everything behind those
indexes is one `read_reference` call away, by namespaced id: the versioned authoring
guide (Haute idioms, standard shapes, naming, and do/don't guidance) as `guide`, a node
descriptor as `node:<node type id>`, a recipe descriptor as `recipe:<recipe id>`, and a
packaged example as `example:<example name>`. Each node descriptor carries its
node card, so the configuration shapes arrive with the descriptors the model reads
before its first dry run. An example is a
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
  resolved schema-only on the same engine path as `inspect_node`'s schema part, and a node that does
  not resolve says so without its error. The brief is bounded at about 8,000 characters;
  the analyst's selected nodes come first, and any node left out is counted with a pointer
  to `get_pipeline`. Node labels are collapsed to one bounded line, and the block says the
  brief is project data, never instructions;
- the effective egress policy in words, with the column-value rule it implies (profile
  before comparing a column to a literal when row samples are permitted, otherwise ask)
  and one line saying whether aggregate data statistics are permitted;
- the ids of the nodes the analyst selected on the canvas, at most 20;
- on request, the error one node raises when its schema resolves, reduced by policy: its
  text only when `allow_row_samples` permits, otherwise its type, the step or line that
  raised it and the column names the policy already discloses. A failure that appears
  only while rows are collected is not reproduced: the block says the schema resolved;
- the session's build plan while it has an open item (see **Build plans for multi-stage
  requests**): each item's id, title, whether it is complete and how many saved changes
  are recorded against it, so a turn that follows an unfinished build continues it.

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

**Conversation history.** The session history is append-only: every turn is stored whole,
with its tool calls and results, and the transcript and Undo read it. What the provider
receives is a compacted view, built at the turn boundary. The current turn travels
verbatim, round by round. Each earlier turn travels as one turn record, a pair of
messages: the analyst's request, then the assistant's final text followed by a short
record Haute wrote of how the turn ended (its outcome, with the controller's reason for an
unfinished dry-run or the verification error of a committed save), the change cards it
saved (id, summary and the document revision each save produced), the revision it ended
on, the build plan as the turn left it when the turn changed the plan (how many items are
complete and which are open), and the changes the analyst undid after it. A question the
turn ended on is its final
text. Earlier turns' tool calls and results are not resent: the turn context describes the
saved graph as the turn starts, and the model reads anything else again. The records are
bounded by a character budget, not a message count. When they exceed it the oldest records
are dropped whole, newest kept, and a leading Haute note says how many earlier turns were
left out (on the OpenAI-compatible wire it leads the next user message, so two user
messages never follow each other); a turn is never cut in the middle, and the current
turn is never cut at all.
Records are built from what a persisted turn keeps (its text, its outcome, its saved
change records and the build plan it left), so a chat revived after a restart compacts the
same way. Mid-tier models
get a short context that still names every change the conversation saved; thinking from
earlier turns is never replayed, which is the compaction shape the providers' preserved
thinking accepts.

**Prompt caching and thinking on Claude models.** The Anthropic adapter marks the stable
prefix, the tool definitions and the frozen system prompt, as one prompt-cache breakpoint,
so every round of every turn of a session reads it from the cache; the turn context, the
history and the current turn all follow the breakpoint. On the Claude models that support
adaptive thinking it enables it with an explicit `medium` effort. Within a turn the
thinking blocks of each assistant message are sent back unchanged, signatures and order
included, in that message, so each round's request is the previous round's request with
the new messages appended; compaction drops them at the turn boundary. While the model
thinks, the panel shows a "Thinking…" status; the thinking itself never reaches the panel
and is not stored with the turn. A Claude model outside the supported set is refused
before the turn starts, naming the models Haute runs. The OpenAI and Databricks adapters
get the compacted history and no caching or thinking changes.

**Turns.** Posting a user message starts a turn, streamed back as typed server-sent events:
assistant text deltas, a content-free thinking status while a Claude model thinks,
tool-call started/finished activity (the tool's name, a plain-words
title written beside the tool such as "Reading the pipeline", "Checking 3 changes" or
"Applying 3 changes", and a one-line summary in plain words, never JSON, of what the call
asks for while it runs and of what its result holds once done, such as "value_band:
schema, data" or "Plan is valid; data checked: 1 advisory finding"), a change-applied event
after each successful apply carrying its change card (see **Change cards**), a
build-plan event carrying the whole plan after each tool call that changed the session's
build plan (see **Build plans for multi-stage requests**), and exactly
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
completes the dry-run/apply sequence, asks one focused question on a line starting
`NEEDS_INPUT:` when material intent is ambiguous, or reports a concrete tool blocker on a
line starting `BLOCKED:`. When it saved part of a request and another part cannot be done
(a run, a file write, a deployment, training), it names that part on a `BLOCKED:` line, so
the turn ends `blocked` with its saves listed rather than `applied`. It never asks the
analyst to confirm a value, name or threshold the request already states, or anything a
tool can answer: it looks that up first, such as whether a column exists (`find_data` on
the file, or `inspect_node`'s schema part) or which labels a banded column holds (the
banding node's config). The second live evaluation of 2026-10-01 saw a model ask whether a
stated column existed in a file it could list, and what labels a banded column held when
its banding node's configuration was readable. When
the analyst delegates a choice ("pick any", "you choose"), the prompt tells the model to
make a reasonable choice, state it, and proceed; it asks only for choices that change the
result materially and that the analyst has not delegated.

The controller reads an outcome marker anywhere in the final round's text: `NEEDS_INPUT:`
or `BLOCKED:` as a whole, case-sensitive word with its colon, bare or wrapped in backticks
or markdown emphasis (`**NEEDS_INPUT:**`, `**NEEDS_INPUT**:`, `` `BLOCKED:` ``). The last
marker decides, and its detail is the text after the marker and its wrapping. The prompt
still tells the model to start a line with the marker. The live evaluation of 2026-10-01
had nine questions and blockers scored as plain answers because the model wrote them after
a sentence of prose or in bold, and a second run that day ended a reply with "However,
`BLOCKED:` Pipeline execution is not available", mid-line and in backticks. A message
whose text asks or blocks never saves:
an `apply_graph_plan` it carries is refused without running, with a tool error saying
nothing was applied because the same message asked or blocked. The same evaluation saw a
model ask for a list it could not read and, in that same message, apply a list it had
invented.

The controller is structural: nothing it does depends on the words of the request. It
tracks one fact across the turn's dry-runs, the state the latest one left: a **validated
plan** (the latest dry-run succeeded) or a **failed dry-run** (the latest dry-run failed);
only an apply that saves clears it. When the model ends a
round with text that carries no outcome marker while that state is
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

**Build plans for multi-stage requests.** A request with several stages (a source, its
features, a banding, a rating and a response) is built under a build plan the model sets
with `update_build_plan`: its items in order, each a short stable id and a title. The
panel shows the plan as a checklist, and each item carries two separate facts. The first is
the changes saved against it, which Haute records: an `apply_graph_plan` call may name the
item it implements in `item`, and when that save commits, verified or not, Haute records the
change's id against the item; an `item` the plan does not hold is refused before anything
is saved. The second is whether the item is complete, which only the model can claim, by
naming it in `complete`. Haute accepts the claim only for an item with at least one
recorded change the analyst has not undone, and otherwise refuses it with a located,
retryable error, so the checklist never shows a stage complete with nothing saved for it.
An apply that saves part of a stage therefore leaves the item open with its change listed
until the model claims it. Setting `items` again revises an unfinished plan: an item keeps
its recorded changes and its completion under the same id, a new id starts open with no
change, and an id left out is dropped. Once every item is complete the plan is finished,
and the next `items` start a new plan, so a later build that reuses an id never inherits a
finished item. A failed call changes nothing. The model sees the resulting items, each
with whether it is complete and how many saved changes it has. An undo marks its change
undone on every item that lists it, and a complete item left with no change that is not
undone reopens; the change stays listed, marked undone, as its change card stays in the
transcript with the undo note after it. The plan is session state: it is persisted with
the session, so a resumed chat shows the checklist, and the turn record of every turn that
changed it says how that turn left it. While an item is open the turn context lists the
plan, so a turn that ended with items open (stopped, out of budget, or ended by the model)
is continued when the analyst says "continue". A session saved before build plans existed
has no plan and revives with none. The system prompt asks the model to set a plan only for
a request with several stages, to name each apply's item and to claim an item only once
its whole stage is saved; a request of one change needs no plan. The plan changes nothing
in the project and is no authority for a save: the outcome's `changes` still list exactly
what the turn saved.

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
When the plan's dry-run ran a [data check](#data-checks), the record also carries it
as the card shows it: each finding worded from its counts and names, the label that
says whether the findings describe the saved graph, and one line on what was not
checked.

`dry_run_graph_edits` takes a required `summary` of at most 400 characters, saying in
one or two plain sentences what the plan does, and an optional list of up to five
`assumptions` the model made (each at most 400 characters, with no control character other
than whitespace). The bound fits the summaries models write: the summary is presentation,
never authority, and a tighter bound refused ordinary summaries in live runs.
They are stored as the plan's receipt in the
plan store beside the plan, outside the hashed plan authority, so wording never changes a
plan hash; a later identical dry-run replaces the receipt of a plan not yet applied. A
plan with `recipe` operations is described by the model's own summary like any other. The change card
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
undid that change, so the model does not assume its earlier change is still in place. The
undo also updates the session's build plan as **Build plans for multi-stage requests**
describes, and its response carries the resulting plan for the checklist.
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

**The tool surface** is eight task-shaped tools. Each answers one question the model
asks while authoring, or records one fact about its work, whatever storage layer holds
it:

- `get_pipeline` — the saved graph: nodes (id, type, name, config summary, and on a
  stepped type its authoring state and value-free per-step summary, as in the graph
  brief), edges,
  a preamble-presence/digest summary (never executable source), and which
  singletons exist.
- `inspect_node` — one top-level node, in the parts the call names: `schema`, `config`,
  `profile` and `data`, with `schema` alone when the call names none. Each part is checked
  against the egress policy on its own, so a part the policy denies is listed under
  `withheld` with the `[assistant.egress]` setting it needs while the permitted parts
  still answer; a call whose every part is denied is refused with
  `egress_policy_denied`, carrying the same `withheld` list. Each part also answers or
  fails on its own: a part that fails (a schema the engine cannot resolve, say) is
  reported under `part_errors` with its located error while the other parts still
  answer, so a schema failure never hides the data part that diagnoses it. The call
  itself fails, naming the part, only when no requested part answered.
  - `schema` (`internal`) — the column names and dtypes at the node's *output* **and on
    each of its inputs**, resolved by the
    same execution engine that runs the pipeline: the lazy plan is built up to that node —
    with exactly the graph preparation a real run performs (submodels flattened, preamble in
    scope, the saved active source selected) — and its schema is read without collecting any
    data. Inputs are keyed by the name the node's own code binds, because writing a
    transform needs the columns arriving at it, not only the ones leaving it — and a node
    the analyst has asked the assistant to *write* has no output schema to report. Such a
    node answers with those inputs and a stable reason rather than refusing.
    This is what lets the agent wire a mid-graph transform against the columns that
    actually exist *at that point* — post-join, post-derivation — not just the source file's
    columns. A node emitting several frames reports one schema per output port. A
    top-level submodel occurrence answers this part too, resolved on the same flattened
    graph: its output ports' columns and its input ports' columns, named by port, so a
    question about what a submodel adds is answered from metadata. Its configuration,
    its code and the nodes inside it stay behind the v1 submodel boundary, as for edits.
  - `config` (`restricted`) — the node's structured config. Credential-shaped
    fields are always redacted; executable code (`code`, `preamble`, `query`,
    `script`, at any depth, so a free-code step's `code` too) is redacted unless
    the policy's `allow_executable_source` permits it. The whole part is still
    treated as `restricted` and is withheld unless the configured policy permits
    that class. A call whose every part is withheld says which parts the policy
    does permit: the schema part lists every output column, a struct column's
    dtype naming its fields, which is how a model that cannot read an output
    node's rows can still see the response's shape.
  A Source Switch's schema part also lists the scenarios it routes (`scenarios`),
  as the graph brief does: scenario names are pipeline metadata.
  A node whose schema fails to resolve still answers each input's columns, as an
  empty node does, and names the failing step when its line says or the step list
  has one step besides `source` steps; the missing column itself is named only
  when the policy already discloses it.
  - `profile` (`allow_row_samples`) — what the values in a frame actually look like, for
    the one question a schema cannot answer: how a categorical column encodes itself. A
    `fault` column typed `String` may hold `Y`/`N`, `true`/`false`, or
    `at_fault`/`not_at_fault`, and code written against the wrong guess runs, validates,
    and silently matches nothing. It returns distinct levels with counts for
    small-cardinality columns, bounds for numerics and dates, and never a row. A column
    with many distinct values has its values withheld, reducing unnecessary disclosure;
    low-cardinality strings can still be returned, including repeated personal data. This
    is the only part that returns values from project data, and the project's explicit
    `allow_row_samples` policy is therefore the authorization boundary. The call's
    optional `input` profiles one of the node's inputs, named as the schema part reports
    it, instead of the node's own output; `input` without the `profile` part is an
    invalid request. Its bounded collection runs in the editor's interactive preview
    worker under the ordinary preview admission and the worker's memory cap, so frames
    downstream of joins and aggregations profile like any other, and a stopped turn stops
    its profile. The turn context states the project's effective egress policy in words
    and requires a profile before a literal comparison only when `allow_row_samples`
    permits one; otherwise it tells the model to ask the analyst which values to match,
    beginning `NEEDS_INPUT:`.
  - `data` (`allow_aggregate_statistics`) — why the saved node fails or why one of its
    columns is null, answered in one call: the [data check](#data-checks) run over the
    node's lineage in the saved graph under the active scenario, returning each lineage
    node's status with its recorded error and step or line, each failure once at the node
    it is attributed to, rows in and out, join matches and findings, and, with the call's
    optional `column`, that column's null count at each lineage node whose output has it
    and the first node where its nulls appear or grow. Counts only, never a value.
    `column` without the `data` part is an invalid request. See
    [A saved node's data](#a-saved-nodes-data).
- `find_data` — the data files visible to the project, and, when the call names a file
  `path`, that file's column names and dtypes too, with no preview collection or row
  values. Listing names visible subdirectories of one project directory and accepts a
  bounded recursive traversal. Recursive
  results are deterministically ordered and report truncation rather than silently omitting overflow.
  It uses the installed input-format registry; unavailable
  optional engines and unsupported extensions are not advertised. Hidden path components
  and explicitly denylisted credential/state names are rejected for both listing and
  schema inspection, even when the caller supplies the path directly.
- `read_reference` — library content by namespaced id, one to twelve unique ids per call,
  returned in the order asked: `guide` (the complete attributable packaged authoring
  guide with the structured step grammar — each step kind's fields and the closed step
  vocabularies — derived from the step renderer), `node:<node type id>` (the complete
  closed node descriptor with its node card), `recipe:<recipe id>` (the recipe
  descriptor with its closed argument schema) and `example:<example name>` (one
  self-contained packaged teaching view: bounded attribution, narrative, and a graph
  rendered through the same machinery as a live pipeline with each node's configuration
  shown whole, values included, rather than as its key names, without inaccessible
  resource paths). Ids are namespaced because an example and a recipe can share a name.
  A batch is all-or-nothing: an unknown id is refused with `unknown_reference`, naming
  the close valid ids. Every returned item is materialised as ordinary finite JSON
  containers before it crosses the tool boundary; immutable registry wrappers never leak
  into provider results.
- `get_project_knowledge` — a bounded, query-selected view of policy-eligible
  source-linked project facts and untrusted documentation evidence. Each item
  carries source digest, extraction version, sensitivity and evidence class;
  excluded content is counted but its path or value is not disclosed.
- `dry_run_graph_edits` / `apply_graph_plan` — validate an ordered operation batch into an
  exact revision-bound semantic plan, then apply that stored plan once using the exact
  returned plan hash. A batch mixes primitive operations with `recipe` operations, each
  `{"op": "recipe", "recipe": "<recipe id>", "arguments": {...}}` with an optional `ref`
  naming the node it creates; the recipe expands deterministically into primitive
  operations inside the same plan, so a recipe and the primitive edits around it are one
  dry-run, one apply and one change card. These are the only provider-visible mutation
  operations. An apply may name the build-plan item it implements in `item`.
- `update_build_plan` — sets the session's build plan (`items`, each an id and a title, in
  order) and claims an item complete (`complete`), as **Build plans for multi-stage
  requests** describes. It changes only the session's plan, never the project, and reads
  no project material.

**Mutation semantics.** `dry_run_graph_edits` first expands each `recipe` operation in
place into its recipe's primitive operations, then loads a canonical saved-state
snapshot and passes the closed primitive batch through one `build_verified_plan`
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
successful apply proves equal, are not repeated. When the egress policy permits
aggregate statistics, a successful dry-run also carries its plan's
[data check](#data-checks) under `data_check`, or the note that it did not fit under
`data_check_omitted`. In both, a chip field that holds its default (no earlier name, no changed
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
`op_not_applied`. A step list that does not render points at the free-code form only for
what the surface cannot hold (an unknown step kind, a start step where `df` is already
bound, a join where the code sees only `df`); a step that is merely incomplete is named
with its problem, to be completed where it stands. A pivot step on an Explore is instead
pointed at the node's `pivots` config, because an analyst's pivot table is a `pivots`
entry and a pivot step only reshapes the frame. Dry-run then generates the planned source in memory and reparses each
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
index, the node, and the config field or step id involved. At dry-run every operation
index, in `where` and in a message or `fix`, counts the batch the model sent, never the
expanded one: a failure inside a recipe's expansion names the `recipe` operation's index,
and `where` adds its `recipe` id. Every operation validation
failure (`invalid_ops`) and every `schema_unresolvable`, `op_not_applied`,
`node_not_ready`, `rename_has_consumers`, recipe argument (`unknown_recipe`,
`recipe_argument_invalid`, `recipe_plan_invalid`) and `unknown_tool` error carries `fix`,
one concrete correction. When `where`
names a node, the error carries `context.inputs`, each incoming input's name and its
column names; column names are schema metadata and never row values, and the dry-run
and apply tools run only under a policy that permits saved project metadata (the same
permission `inspect_node`'s schema part needs), so they disclose nothing that part would not.
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
  standard Databricks `.env` contract. Each provider lane receives the tool-schema
  projection it handles best, and both projections derive from one canonical input schema
  per tool, which `_wire_ops`' operation models and the operation descriptors generate;
  neither is written by hand. The **canonical projection** is that schema itself:
  `dry_run_graph_edits.ops` is a discriminated union whose branches keep their own
  required fields. Anthropic and OpenAI receive it, and on those two lanes a closed tool
  that does not change the project (the read tools and `update_build_plan`), one whose
  canonical schema reduces to closed objects without composition, is sent
  in strict mode, so the provider decodes its arguments against the schema: in the subset
  of JSON Schema strict decoding accepts, with a removed numeric bound stated in the
  description, and on OpenAI with every property required and each optional one nullable,
  a `null` for which the adapter reads as omitted. A tool that mutates the project, or whose
  schema has open objects or a union, is never strict. Databricks receives the
  **compatible projection** by default, because its live baselines were measured on it;
  the evaluation's `canonical_tools` variant sends that lane the canonical projection,
  never strict, so the two can be compared on the configured model. A probe of the
  configured Databricks models on 2026-10-01 found that they accept the canonical union and
  flat objects of up to twenty-four keys, so Databricks' documented sixteen-key limit does
  not decide the lane; the low-level specification records it. The compatible projection
  is a flat, bounded wire schema. It preserves names, descriptions, required fields, single scalar
  types, enums, and container shapes. A discriminated composition whose branches are closed
  objects is merged into one closed generation object: branch properties are unioned, the
  discriminator constants become one enum, and only requirements common to every branch
  remain required. A property present in only one branch, or declared identically across
  branches, is recursively projected within the remaining budget so its description and
  affordable nested shape survive. When the same property is an array of different closed
  object variants, their item fields are merged by the same rule instead of discarding the
  item contract; shared requirements remain required and the variant descriptions are retained.
  Projection has a forty-property budget per tool, an explicit bound. A composition that
  would exceed it remains a generic typed container. A property declared differently on
  each branch keeps only their common type and joined descriptions: a recipe operation's
  `arguments` reach the provider as an object whose description gives each recipe's
  argument names, and the complete argument schema is the `recipe:<id>` reference.
  Unsupported validation vocabulary is
  omitted, except a number's `minimum` and `maximum`, which the projection states in its
  description, and a rejection by a bound names the bound. The complete canonical operation schema remains the sole execution-time
  authority, so no projection weakens validation. Some Databricks-hosted OpenAI-compatible models encode function
  arguments whose declared type is an array, object, boolean, integer, or number as a JSON
  string, under either projection. The Databricks adapter decodes only valid, correctly typed, schema-declared
  values. A value's declared type comes from the tool's canonical schema at that value's
  position, whichever projection was sent, because both derive from it and only the
  canonical one keeps the union whose branch decides a recipe's argument types: the top-level properties, an object's declared properties and an array's
  declared items, at any depth. Within a closed object union the declared types come from
  the single canonical branch the discriminator values select, narrowed one discriminator
  at a time: an operation in `dry_run_graph_edits` selects its branch by `op`, and a
  `recipe` operation then by `recipe`, so its `arguments` and their `rules`, `tables` and
  `output_columns` arrays decode by that recipe's argument schema. An unknown or
  ambiguous discriminator selects no branch, and nothing below it is decoded. Numeric results must be finite, booleans never satisfy integer or
  number declarations, and string or null declarations are never decoded. The adapter
  does not infer a type from an undeclared or ambiguous schema. Two further spellings,
  seen live on 2026-10-01 in eleven `assumptions` and five `find_data.recursive` calls,
  decode by the declared type alone and are logged by shape: plain text where a list of
  text is declared becomes a one-item list (text that opens like JSON never does), and
  Python's `True` or `False` where a boolean is declared becomes that boolean. The
  `assumptions` list stays a list on the wire, because the change card shows each
  assumption as its own bullet, bounds their number and persists each one. An
  invalid or wrong-type encoding is left unchanged for the canonical tool validator to
  reject as a structured, recoverable tool result; it is never guessed, repaired, or
  executed, and it does not terminate the provider stream.
- **Turn records, not a message-count window.** A window of the newest messages drops
  context without regard to its size: one tool-heavy turn pushes every earlier request out
  while a schema result of thousands of tokens stays in. Compacting at the turn
  boundary keeps what a later turn acts on (what was asked, what was said, what was saved
  and how the turn ended) and drops what the turn context and a fresh read restate (earlier
  tool calls and results). Replaying nothing of an earlier turn but its record is also the
  one client-side compaction shape under which Claude's signed thinking stays valid.
- **A checklist of two facts, one tool.** A long build gave the analyst no view of what
  remained, and a model that lost track of its stages could not be nudged on them. Whether
  a stage is done is a judgment only the model can make, so completion is its claim; what
  was saved for a stage is a fact, so Haute records it from the commits the model
  attributes, and a claim with nothing saved behind it is refused. Keeping both on one
  `update_build_plan` tool plus an `item` on the apply adds one closed tool that every
  provider projection carries unchanged. It builds on several applies per turn, which the
  live portfolio of 2026-10-01 measured at 27 of 44 cases passing against 24 of 44 with one
  apply per turn on `databricks-qwen35-122b-a10b`.
- **One explicit effort for Claude models.** `medium` is valid on every Claude model with
  adaptive thinking, is Anthropic's recommended starting point for multistep tool use on
  the current Opus and Sonnet models (and the current Opus's own default), and keeps
  thinking within the default 8,192-token output budget per provider call, which a
  `max_tokens` stop turns into a failed turn. It is fixed in code rather than configured:
  there is no evaluation evidence yet for choosing another level per project.
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
  erroring. Checking data is the one carve-out, and it is not a tool. A dry-run proves
  schemas only, so a plan that runs but computes the wrong data passes it; the
  [data check](#data-checks) measures the changed nodes' data
  under its own permission, `allow_aggregate_statistics`, and its own contract (what it
  executes, what it returns, what it is bound to), because a data check is an execution
  capability and graph-plan authority does not authorise execution. It runs automatically
  after an eligible dry-run rather than on request, because a model that must choose to
  call a check often will not, and it never replaces the evaluation's independent
  execution goldens. The same check also answers a question about the saved graph, as
  `inspect_node`'s data part: "why is this column null" was otherwise answered by
  guessing from code, and a part of the read tool keeps the tool count at eight.
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
- **Schema comes from the engine, not a parallel inferencer.** `inspect_node`'s schema part reuses the
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
  whole only through `inspect_node`'s config part under the egress policy.
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
operation, recipe, and example index. Full node and recipe descriptors, the guide and
the examples are retrieved through the bounded `read_reference` operation. An unknown
id returns `unknown_reference` with its close valid ids; malformed closed input returns
`invalid_request`. These are tool-level failures and never trigger a
prompt-owned fallback vocabulary. Operation descriptors stay in the manifest for the
registry's own checks; the provider learns an operation from its tool definition.

The manifest is the assistant's only node catalogue. A removed tool is refused with
`tool_removed` and a message naming what replaces it, because a resumed session's
history can still name one: `list_node_types` (replaced by the manifest index and
`read_reference`), and the tools the task-shaped surface consolidated —
`get_node_schema`, `get_node_config` and `get_column_profiles` (`inspect_node`),
`list_datasets` and `get_dataset_schema` (`find_data`), `get_capability_manifest`,
`get_capability_descriptors`, `get_example` and `get_authoring_guide`
(`read_reference`), and `plan_recipe` and `dry_run_recipe_plan` (a `recipe` operation in
`dry_run_graph_edits`).

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

Each session keeps an evidence ledger in its live state: the exact source and
schema facts its tool results returned in any of its turns (a `find_data`
schema or a `get_project_knowledge` item, each a project-relative path and a
digest). It is not read back from the provider history, which compaction no
longer fills with earlier turns' tool results. Every dry-run includes the
ledger's facts in the plan revision, so a plan formed after a fact was
returned binds that fact, and an apply refuses the plan if the fact changed in
between. A fact returned in the current turn that is missing or changed when a
dry-run plans fails it with `project_source_missing` or
`stale_project_evidence`, naming the project-relative file and the call that
refreshes it: the model is reading that result now. A fact carried from an
earlier turn that has since gone missing or changed is released from the
ledger at the dry-run instead, and the dry-run proceeds. The model no longer
sees that earlier tool result, and the dry-run resolves every schema afresh, so
a plan built on outdated knowledge still fails on what the files hold now;
failing every later plan on a fact the conversation has moved past would block
it on a file the model may no longer use. A carried fact that still holds keeps
binding plans. When the model inspects datasets again (any `find_data` call),
dataset-schema evidence whose file no longer exists is dropped, so a dataset
renamed after it was inspected in the current turn blocks planning only until
the model looks again. The ledger is never persisted, because a digest of a
schema payload is enumerable: a chat revived after a restart starts with an
empty ledger, matching the redacted tool payloads it revives with.

`dry_run_graph_edits` accepts the closed operation union (the primitive operations and
one `recipe` branch per installed recipe), explicit
postconditions, and the plan's summary and assumptions. The plan it builds holds the
normalized operations; the base revision; a stable plan hash; semantic node, edge,
configuration, preamble and sidecar changes; validation warnings; resulting graph shape;
affected capabilities; the deterministic egress class; and the strongest bounded
verification tier the affected capabilities declare; the tool returns the compact view of
it described under Mutation semantics. The plan hash is
canonical over all facts that can affect authorization or verification.
Canonical request validation recognizes closed object unions discriminated by fields such
as `op` and `kind`, and narrows by a further discriminator when one value selects several
branches (`recipe` among the `recipe` operations). It selects the declared branch before validation so retry feedback
names the exact safe schema path and a stable value-free reason, rather than collapsing all
branch failures to a generic `oneOf` error. Those fields are retained in redacted history;
submitted values are not. The provider's primitive-operation branches are projected from
the same `_wire_ops` model declarations that parse the canonical operation vocabulary;
field membership, requiredness, discriminator values, and node-type enums are therefore not
maintained in a second hand-written schema.

Graph authoring never runs the graph, collects rows, invokes a sink, or
materialises a configured output. The data check that may follow a dry-run is
not graph authoring: the plan is complete and stored before it starts, and
nothing it collects enters the plan. Dry-run constructs the production lazy plan
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
explicit runtime authorization instead of reusing graph-plan authority. The
data check is such a definition: its authorization is
`[assistant.egress].allow_aggregate_statistics`, never the plan's authority,
and [its contract](#data-checks) bounds it to
measuring the lineage of the plan's changed nodes, or of one saved node the
model inspects, value-free, issuing no sink,
external-write, training, optimisation, deployment or Git operation of its own.
It does not contain the project and model-authored Python that lineage runs,
which has the process's privileges as in any preview. Its findings sit outside
the plan hash and the save lock and never change what apply saves.

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
is written in that text form: a boolean column's values are `"true"` and `"false"`, a
number is written as its digits (`"3"`), and a string column's values are
matched exactly. A boolean or numeric rule value is refused at planning with a message
that states the text form it must take, and two rules with the same value are refused
there too, so a recipe never saves rules that match no row or that collide once saved.
There is no numeric-banding recipe, and the recipe index says so: number and date ranges
are banded with a `banding` node using `banding: breakpoints`. A dry-run refuses a
categorical factor a plan writes (by the recipe or directly) on a decimal, date or time
column, whatever the rules say, with a located fix pointing at breakpoints, because
categorical rules match only the listed values; integer codes such as vehicle groups are
accepted, and an analyst may still band any column categorically in the editor. The reference-join recipe offers the join modes
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
A recipe is invoked as one operation of a `dry_run_graph_edits` batch,
`{"op": "recipe", "recipe": "<recipe id>", "arguments": {...}}`, and the dry-run expands
it in place, deterministically, into its primitive operations and postconditions. The
model never receives, relays, extends or rewrites the expanded operations. Expansion
reads no project state, so the same arguments expand identically on every dry-run.
Each expansion's batch-local refs are its own: the node the recipe creates takes the
operation's optional `ref` (otherwise `recipe_<operation index>`), and a response
output it adds takes that ref with `_output` appended, so two recipes in one batch never
collide and a later primitive operation can address the recipe's node by `$ref` — wire an
edge from it, or update a node beside it, in the same plan. The nodes and edges a recipe
adds are proved after save by the plan's postconditions like any other operation's. A transform, join, or
rating recipe's optional `output_name` and non-empty `output_columns` must be supplied
together; they deterministically add and connect one response `output` node with a
canonical JSON mapping for exactly those columns inside the same plan. The standalone
`response_output` recipe requires `source`, `output_name`, and `output_columns` and
creates that same mapping directly after its source. Each mapping row's `source_port`
names the frame it reads by the input name its edge gives: the created node's id for a
recipe's own output, and, when `source` is a `$ref` to a node the plan adds, the ref,
which the plan resolves to that node's name rather than saving the ref. A dry-run refuses
a response row the plan writes whose `source_port` names no incoming edge, because the
engine lets a one-input response read any name and the wrong name would save silently. A bare output name is a material
mapping ambiguity and requires clarification. A recipe argument failure is a structured
error located at the `recipe` operation, with `fix` naming the correction and the
`recipe:<id>` reference that holds the argument schema.

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
index and served by `read_reference` as `example:<name>`. A bundle with `teaching: false` is a test
fixture (the deployment-safety and invalid/adversarial bundles): it is
validated and materialisable for its specialist checks exactly like a
teaching bundle, but it is absent from the example index, and `read_reference`
refuses its `example:<name>` id as `unknown_reference`.
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
`read_reference`; evaluation fixtures and cases live outside assistant package
resources and cannot be enumerated through those surfaces. Installed
distribution smoke checks enumerate and validate every bundle and execute the
declared fast subset.

The assistant is evaluated offline by replaying reference trajectories through the
real tools in CI and live against a configured provider on demand, with results
reported per authoring area; the fixtures, cases, tiers, scoring layers, execution
boundary and live runner are specified in [the assistant evaluation](evaluation.md).


## Egress and project knowledge

`[assistant.egress]` is required and closed. It contains exactly `trust`
(`local`, `organization`, or `external`), `max_sensitivity` (`public`,
`internal`, or `restricted`), and the required booleans
`allow_project_knowledge`, `allow_executable_source`, `allow_row_samples`, and
`allow_aggregate_statistics`. No key has a default: a missing key is a
configuration error naming its full path, such as
`[assistant].egress.allow_aggregate_statistics`. A configuration without
`egress` is not ready and names `[assistant].egress` in its not-ready reason.
Local endpoints must be loopback; organization and external endpoints must use
HTTPS; external policy is public-only and cannot enable executable source, row
samples or aggregate statistics. Project configuration may narrow but never
widen these class ceilings.

**Aggregate statistics.** `allow_aggregate_statistics` authorises the
[data check](#data-checks): executing the lineage of
the nodes a plan changes, or of the saved node `inspect_node`'s data part names,
over project data in the preview worker, and sending
the model value-free counts and shares derived from those rows (rows in and
out, null shares, per-rule banding counts, rating misses, join matches), never
a row value, a quantile of values or a distinct value. Its minimum sensitivity
is `internal`. The results are value-free metadata of the class `internal`
already permits (`find_data` returns a dataset's schema and estimated row count
at `internal`); they name nodes and columns, which are internal pipeline
metadata, and carry no configuration value, so they do not need `restricted`.
Under `public` no check runs, as no project read does. Value-free is not
anonymous: a count over a predicate the model wrote (a filter literal, a
categorical rule value, a join key) says how many rows satisfy it, including
whether any do, so a check can disclose whether a value occurs in the data. The
explicit flag is the authorization boundary, as `allow_row_samples` is for
profiles; the result's shape limits what is sent, not what can be inferred from
it. The flag is independent of `allow_row_samples` in both directions: row
samples are a disclosure decision, while the data check is an execution
decision with its own cost, because the node code the schema tier already runs
over lazy frames then runs over rows, with its deferred callbacks. The text of an
execution error inside a check result still
follows `allow_row_samples`, as every execution error's does. The turn context's
egress policy states the flag
in one line, when it is true:

`- Aggregate data statistics: permitted (value-free counts and shares, never row values)`

and when it is false:

`- Aggregate data statistics: not permitted; no data check runs and `inspect_node` withholds its data part, so a dry-run proves schemas, never that the data came out right`

The flag, under a ceiling above `public`, is what lets a dry-run run its data
check and `inspect_node` answer its data part; nothing else reads it. A data part
the policy forbids is listed under `withheld` with its `required_policy`
(`allow_aggregate_statistics = true`, or `max_sensitivity = "internal"` under
`public`), as every withheld part is.

Schema inspection is schema-only: assistant schema results never contain
preview rows. Raw rows are unavailable through ordinary read tools, and
executable source is available only through `inspect_node`'s config part when
`allow_executable_source` permits it. Rows are read only behind a flag of their
own: `inspect_node`'s profile part returns a frame's values under
`allow_row_samples`, and the [data check](#data-checks)
returns value-free counts under `allow_aggregate_statistics`. Resolving a schema still runs the
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
and an `internal` policy withholds `inspect_node`'s config part before node
configuration is parsed, while the call's schema part still answers. The
turn context's graph brief, base revision, selection and preview error are
`internal` too: under a `public` policy the block carries only the policy.
Below `restricted` the turn context says plainly that saved node configuration
is withheld (factors, tables, mappings, scenario maps and code) and that a list
or map the model has not read must not be rewritten; when the config part is
readable it says that before replacing a saved list or map the model reads that
node's config in the same turn and keeps its entries, and it mentions the config
part's redaction of code only then.

**Blind rewrites of saved configuration are refused.** `update_node` replaces a
key's whole value, so changing one factor, rating table, response row or
scenario route means restating the whole list or map. A dry-run refuses an
`update_node` on a saved node that replaces a key holding a non-empty list or
map unless the new value keeps every saved entry unchanged (a list entry equal
to it, a map key with an equal value; new entries may be added) or the model has
seen that node's configuration in the running turn: an `inspect_node` config
part returned it, or an apply of the turn added the node. An earlier turn's read
does not count, because compaction drops its result from what the model sees. A
node the plan adds is the model's own and is not guarded. The located,
retryable error names the node, the key and the saved entries it would change
or drop by their metadata identities only (an output column, an output path, a
pivot or step id, an input name), counting them where a list or map has none.
While the policy withholds saved configuration it is `config_withheld`: the
model cannot read that configuration, so it asks the analyst on a
`NEEDS_INPUT:` line rather than retyping it. Where the policy lets the model
read it, it is `config_unread`, and its fix says to read the node with
`inspect_node`'s config part and resend the update keeping the existing
entries. Either way a step list's fix points at `edit_steps`. In the live
evaluation of 2026-10-01 under an `internal` policy, 11 of 37 failing case-runs
came from retyping such lists and six of them saved silent corruptions of a
rating or the response contract; a second run that day, with configuration
readable, replaced a response's mapping without reading it and dropped two of
its rows. Item-level edits (ASSIST-52) remain the way to change one entry
without reading the rest.

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

## Data checks

A dry-run proves schemas only: plan construction, the schema tier and apply's
verification collect no rows, so a plan that runs but computes the wrong data
would validate, apply and report success (every row in a banding default, a
filter that empties its input, an Edge Join that matches nothing, a rating table
most rows miss, a join that multiplies rows on duplicate keys). The data check
closes that gap. After an eligible dry-run, when the egress policy permits it,
it measures the nodes the plan changes in the plan's candidate graph and returns
value-free advisory and informational findings, which the dry-run result
carries to the model and the change card shows the analyst. It runs without
being asked. The same check answers `inspect_node`'s data part for a node of the
saved graph, measuring that node's lineage instead of a plan's changed nodes
([A saved node's data](#a-saved-nodes-data)); every rule below holds for it
except where that section says otherwise.
The [low-level specification](low-level.md#data-checks) names the seams,
constants, the closed result shapes and a worked example.

**What the check executes.** The check executes the lineage of its checked
nodes in the dry-run's candidate graph: the `result_graph` of the
`VerifiedPlan` that `src/haute/assistant/_application.py::build_verified_plan`
returned for the stored plan, never the saved graph. The candidate graph is
flattened and its preamble compiled as for a preview. The lineage is each
checked node and its ancestors up to the sources, under the candidate graph's
active source scenario (its `active_source`, the checked scenario) and pruned at
Source Switches as `src/haute/execution.py::source_lineage_graph` prunes them.
A node downstream of a checked node never runs unless it is checked itself.

**What the check does and does not contain.** The check itself issues no
built-in sink or external-write operation: Quote Response, Data Output,
Explore, Model Training and Optimisation nodes have no output frame, so no
lineage contains one, and the check publishes no output. It issues no
training, optimisation, deployment or Git operation, prepares or refreshes no
input snapshot, captures or publishes no node-output snapshot, fetches no
remote data, and neither downloads an artifact nor calls a model registry (see
**Eligibility**). These are statements about the operations Haute performs,
not a containment of Python. Project Python and model-authored Python (the
preamble, node code and free-code steps) run with the privileges of the process
that runs them, exactly as in a preview: `src/haute/_sandbox.py` is an accident
guard that refuses only calls that would hang or stop the server, not a
sandbox, so that Python can itself write files, reach the network or read
credentials, and an aggregate-only result schema does not contain it. The
dry-run already executes that Python: `build_verified_plan` resolves schemas in
the server process by building each node's lazy frame, which runs every
free-code step and node code body over lazy frames, and it loads the object of
a Load File the plan writes (`src/haute/_builders.py::load_external_file_object`).
What the check adds is collection: rows flow through the plan, so deferred
Python (callbacks such as `map_batches` and `map_elements`, and the row-local
Python scans of Model Scoring and the rating miss guard) runs over rows, in a
worker process rather than the server. The check is therefore not a new class
of execution; it is the existing class over rows, authorised by its own flag.

**Where it runs and how it is bounded.** The check runs only in a killable
interactive worker process. `HAUTE_INTERACTIVE_EXECUTION_MODE` defaults to
`process` on every platform (Windows, Linux and macOS alike, with spawned
workers); under `thread`, an explicit opt-in that the test suite's autouse
fixture also sets, the check does not run and reports `worker_mode_unsupported`,
because a thread cannot be stopped between checkpoints. The check takes one
`PREVIEW_EAGER` admission from
`src/haute/_execution_admission.py::create_admitted_execution_context` under the
operation name `assistant_data_check` (refused at once rather than waiting) and
runs through `src/haute/_interactive_workers.py::run_in_interactive_worker`,
under the isolated budget derived from that admission and the worker's native
memory cap. Its deadline is 30 seconds, fixed rather than configurable and
absolute from the moment the check starts, and that one deadline is carried
through every stage. The server's work before admission reads only
configuration, file metadata and published-generation pointers, never an
input's content and never a snapshot's parts. The check never waits for a worker slot or a starting worker:
it tries once to take the slot its affinity selects, and when that slot is held,
or its worker is not running, it does not run and reports `worker_busy`. Every
read that can hash an input's whole content (an input's source signature, the
binding's input identity and the cached model's identity) runs in the worker,
where the deadline's termination bounds it together with the request's dispatch
and the run. Editor requests have priority: when an editor preview, trace,
output assembly or free-code column resolution, or any other interactive
request that waits for its slot, needs the slot a check holds, the check is
stopped and reports `superseded_by_preview`, and the waiting request runs next,
on the replacement worker, before any other request: while an interactive
request is pending for a slot, a check that asks for it is refused as
`worker_busy` even if the slot is momentarily free. At the deadline, on
supersession by a newer check in the same session (`superseded`), on
supersession by an editor request, and when the turn stops (`cancelled`), the
check's worker process is terminated, and its slot is released only after
termination is confirmed and the pool has started the slot's replacement worker.
A check therefore costs a dry-run at most its 30 seconds plus that termination
and restart, and an editor request that pre-empts a check waits for the same.
At most 8 nodes are checked per check, and one check runs per assistant session
at a time. No frame leaves the worker: it returns only the value-free result.

The check starts after the dry-run has stored its plan, outside the save lock.
Its outcome never changes the plan, its hash, its verification tier, its
evidence or its warnings, and a dry-run that fails runs no check.

**Which data it reads and how rows are bounded.** The lineage runs over the
inputs an editor preview reads, uncapped at the sources, but never prepares one:
it reads only data that already exists locally, in the states below, and the
first input in a node's lineage that is in any other state makes that node
`input_not_prepared`, with the remedy to preview that input in the editor
first.

| Input | Read by the check when | Not checked when |
|---|---|---|
| Data Input, Parquet in scan mode (read directly) | always, from its file | never; a file missing at run time is an execution failure, and the changed input signature makes the check `source_changed` |
| Data Input from a snapshot (every other file format, inline records, database and Databricks inputs) | its published generation is `ready` and `fresh`; `ready` and `unknown`, which is how database and Databricks inputs always read because they have no local source signature; or `ready` while its original file is missing, since a published generation stays authoritative | `ready` and `stale` (its file changed, so a preview would refresh it), `building` (a refresh is in progress; the check never waits for it), `missing`, `corrupt` or `failed` |
| Quote Input from a flat file (any path that is not JSON, JSONL, NDJSON or XML) | always, from its file, as a preview reads it directly | never; as for a direct Data Input, a file missing at run time is an execution failure and `source_changed` |
| Quote Input from a structured file (JSON, JSONL, NDJSON or XML) | each table it reads has a published table snapshot in a readable state as above | a table it reads has no published snapshot or is in a non-readable state |

The published state is read on the server through the read-only status the
editor's input-cache panel shows. It looks for a running refresh first, before
touching the snapshot store, so `building` is reported while the previous
generation is still readable; otherwise, for the check, it reads only the
published generation pointer and that the generation's files exist. Verifying
the generation (its part sizes, Parquet footers and, for a generation this
process has not verified before, every part's content hash) happens in the
worker before the walk, where a generation that fails is `corrupt`. Freshness
(`fresh` or `stale`) needs the source's content signature, so it is decided in
the worker too. A
refresh the server cannot see, such as one a preview's preparation runs in
another worker process, changes a generation pointer the binding signs and so
ends the check as `source_changed`.

Each measured frame, which is a checked node's output port or one of its input
frames, is cut to its first 1,000,000 rows with `head`, the bound
`inspect_node`'s profile part applies (`_MAX_PROFILE_ROWS` in
`src/haute/assistant/_tools.py`). The frames a measured frame is computed from
are not cut, so an Edge Join's join side, a rating step's input and a filter's
input are complete, and Polars pushes the cut upstream only where the result is
unchanged. When it cannot (a filter that keeps nothing scans its whole input),
the deadline bounds the cost. Cutting the sources instead would distort a join
of two large inputs, whose first rows barely overlap, and would report a filter
as emptying its input whenever the matching rows of a sorted file lie beyond
the cut. Nothing is sampled at random. Every row count records `truncated` when
its frame reached the bound. Below the bound every count is exact over the
whole frame, as the Banding editor's whole-dataset statistics are. At the
bound, counts and shares describe the frame's first 1,000,000 rows in the
engine's order, which is not a random sample and, for a frame whose row order
the engine does not fix (after a join or a group-by without maintained order),
not reproducible between runs; every finding computed from a truncated frame
carries `truncated: true`.

**What it returns.** The result is value-free: counts and shares, with the node
ids, port and input names, column names and configuration positions that say
what was counted. It never carries a row value; a quantile, minimum, maximum or
mean of data values; a distinct value or level; a configuration value (a rule
value or assignment, a table entry or default, a filter literal, code); or the
text of an execution error unless `allow_row_samples` permits that text. Its
closed shapes, discriminated by `outcome` and, per node, by `status`, are fixed
in the low-level contract with a worked example. A share is the exact ratio of
two counts the result also carries; it is shown rounded to four decimal places,
and it is `null` when its denominator is 0. For each changed node the result
reports:

- `status`: `checked`; `failed`, when the node's own output raised after its
  inputs collected, with a structured error record (its error class, exception
  type, step or line, the columns the egress policy discloses, and the error's
  own text only under `allow_row_samples`); `upstream_failed`, naming the node
  the failure is attributed to; or `not_checked`, with one reason.
- Rows in and out: the row count of each input frame, by its code-visible input
  name, and of each output port, each with `truncated`. A single-frame node's
  port is `null`; a Quote Input's ports are its table names. A `failed` node
  reports its inputs and no outputs.
- Null counts: for each new or changed column of each output port, its null
  count over the port's rows. A column is new when no input frame has it, and
  changed when an input has it with another dtype or the node's structured
  configuration writes it (a banding factor's output column, a rating table's or
  combined output's column, a `with_column` step's target). At most 20 columns
  per port, in output order, and at most 10 ports per node, each with the number
  omitted.
- Banding, for a Banding node: per factor, identified by its 0-based position in
  `factors` and its output column, the rows the factor read; the rows each rule
  claims, as a list aligned with the factor's `rules` by 0-based position; and
  the defaulted rows. Claims are the claim index of
  `src/haute/_rating.py::banding_rule_claim_expr`, computed on the frame that
  factor reads, so a row counts against exactly the rule whose assignment
  execution writes and an unclaimed row is a defaulted row. A factor with more
  than 100 rules reports its claimed and defaulted totals and the number of
  rules that claim no row instead of the list. A draft factor that execution
  skips is `skipped`, with no counts. At most 20 factors per node, with the
  number omitted.
- Rating, for a Rating Step: per table, identified by its 0-based position in
  `tables` and its output column, the rows the table read; the rows whose factor
  key has no entry, keys canonicalised exactly as the lookup canonicalises them
  and counted alike whether `defaultValue` fills them, `"onMissing": "neutral"`
  leaves them null, or the default `"onMissing": "error"` raises; the number of
  entries; and the number of entries no row's key matched. A table execution
  skips is `skipped`, with no counts. At most 20 tables per node, with the
  number omitted.
- Edge Join: the join type and declared `validate`, the key column names per
  side, the base and join rows, the base rows whose key matches at least one join
  row, and per side the number of key tuples that occur more than once. A key
  tuple with a null part never matches and is never counted as duplicated, as
  the join itself never matches nulls. A cross join reports rows only.

Measurements that read only a node's inputs (its input row counts, banding
claims and rating lookups computed on the frame the factor or table reads, and
an Edge Join's key matches and duplicates computed on its two inputs) are
collected before its output, so they survive a failure of its own output: a
rating step whose miss guard raises still reports its tables' misses. Code-mode
nodes and free-code steps are measured like every node, by rows in and out, null
counts and errors. Banding, rating and join measurements come only from
structured configuration, so a join written in code or in a step list is
measured by its node's rows in and out.

**Failures and their attribution.** A failure raised while a node is built or
run is attributed to that node. A failure raised while collecting a checked
node's input frame is attributed to the node that produces that input, marked
`at_or_upstream`, because the raising operation may lie in an ancestor the
check does not measure. A failure raised while collecting a node's own output,
after its inputs collected, is that node's own. Checked nodes are visited in
the changed nodes' order, and once a failure is attributed to a node, every
later checked node whose lineage contains that node is `upstream_failed` naming
it and is not collected, while checked nodes with no failed ancestor still
measure; so a failing node that two checked nodes both read is reported once.
Two checked nodes that reach one failing ancestor through different unmeasured
producers each attribute the failure to their own producer, both marked
`at_or_upstream`, because the check cannot prove the two failures share a cause
without measuring the ancestors it does not measure. A failure attributed to a
node inside a submodel occurrence, which runs flattened, is reported against the
occurrence, the node the model can name; the nodes inside it stay behind the
submodel boundary. The error record
maps exception classes as follows: a Polars error, an exception raised from node
or step code, or a preamble failure is `authored_code`, reported as every
execution error is (type, step or line, disclosable columns, text only under
`allow_row_samples`); a `ConfigSettingError` is `configuration`, with its
setting and fix and only values the model submitted; every other
`HauteValidationError` subclass, such as the rating miss guard's
`RatingTableMissError`, is `validation`, its type always and its text, which can
quote row values, only under `allow_row_samples`; any other `HauteError` is
`haute`, with its own message; anything else is `internal`, logged and reported
with the sanitized internal detail.

The result also carries the check `version`, the checked `scenario`, `row_bound`
(1,000,000), its `outcome` (`checked`, or `not_run` with one reason), its
findings and its elapsed milliseconds.

**Findings and thresholds.** A finding names its `kind`, its `severity`, its
node, `truncated`, and the fields its kind lists below. A severity is
`advisory`, meaning the data looks wrong and the model should look before it
applies, or `informational`, meaning worth knowing and often intended. No
finding blocks apply. Thresholds compare the exact ratio of the counts, never
the rounded share, and a share whose denominator is 0 is `null` and raises no
share finding. The kinds are a closed set:

| Kind | Severity | Raised when | Fields |
|---|---|---|---|
| `execution_failed` | advisory | A node is attributed a failure; one finding per attributed node. | `error`, `at_or_upstream` |
| `rows_emptied` | advisory | A checked node's output port has 0 rows while at least one of its inputs has at least 1. | `port`, `input_rows` |
| `banding_all_default` | advisory | A banding factor read at least 1 row and no rule claims any. | `factor`, `output_column`, `rows` |
| `banding_mostly_default` | informational | A banding factor's defaulted rows over rows read is at least 0.5 and some rule claims a row. | `factor`, `output_column`, `defaulted`, `rows`, `share` |
| `banding_rules_unclaimed` | informational | Rules of a factor that is not all-default claim no row. | `factor`, `output_column`, `rules` (up to 20 positions), `rules_omitted` |
| `rating_misses` | advisory | A rating table's missed rows over rows read is at least 0.10. | `table`, `output_column`, `missed`, `rows`, `share` |
| `rating_misses` | informational | A rating table's missed rows over rows read is above 0 and below 0.10. | as above |
| `rating_entries_unused` | informational | At least one of a rating table's entries matched no row. | `table`, `output_column`, `unused_entries`, `entries` |
| `join_unmatched` | advisory | A `left`, `inner` or `semi` Edge Join's base has at least 1 row and none matches a join row. | `base_rows`, `join_rows` |
| `join_partial` | informational | A `left`, `inner` or `semi` Edge Join's matched base rows over base rows is above 0 and below 1. | `matched_base_rows`, `base_rows`, `share` |
| `join_validation_failed` | advisory | An Edge Join's `validate` requires unique keys on a side (`m:1` the join side, `1:m` the base side, `1:1` both) and that side has a duplicated key tuple. | `validate`, `side`, `duplicate_key_tuples` |
| `join_fan_out` | advisory | A `left` or `inner` Edge Join whose `validate` is not `1:m` or `m:m` has more output rows than base rows, neither truncated, and a duplicated key tuple on its join side. | `base_rows`, `output_rows`, `duplicate_key_tuples` |
| `column_all_null` | advisory | A new or changed column of an output port is null in every row, with at least 1 row. | `port`, `column`, `rows` |
| `column_mostly_null` | informational | A new or changed column's nulls over the port's rows is at least 0.5 and below 1. | `port`, `column`, `nulls`, `rows`, `share` |

A `join_validation_failed` finding replaces the `execution_failed` finding of the
join whose validation raised. When a rating step's miss guard raises (its
table's `onMissing` is `error` and it has no usable `defaultValue`), that
table's `rating_misses` finding is advisory whatever its share and replaces the
node's `execution_failed` finding. Each replacement states the cause. Findings
are ordered advisory first, then by the changed nodes' order, then by the order
of the kinds in the table above. At most 20 are returned, with the number
omitted.

**How large it may be.** The model-facing check is bounded at 32,000 bytes of
compact UTF-8 JSON, and never more than the room the dry-run's own response
leaves under the 256,000-byte limit above which a tool result becomes
`tool_result_too_large`, measured on the fully attributed result the limiter
measures. Findings take at most 16,000 bytes of it. When the result does not
fit, it is reduced in a fixed order that always ends: the last node record
holding detail loses it (`detail_omitted`), then the last node record of any
status is dropped (`nodes_omitted`), then the last finding is dropped
(`findings_omitted`). When even the empty result does not fit, the dry-run
result carries a fixed one-line note that the check's result was omitted for
size instead of the check, and nothing when even the note does not fit. The
stored check is never reduced, and the dry-run's own fields are never reduced
for the check, so a check never turns a successful dry-run into an error. The
low-level contract states the variants and each step.

**What findings are bound to.** A check result records its binding: the check
`version` (1, incremented whenever a measurement, threshold or shape changes);
the `plan_hash` it was computed for; `graph_digest`,
`src/haute/_cache.py::graph_fingerprint` of the flattened candidate graph, which
covers node configuration, edges, the preamble and imported utility modules but
not the active scenario; the checked `scenario`; `source_generation`, a digest
of `src/haute/execution.py::dataframe_graph_input_identity` over the check's
lineage under that scenario, which covers the input file signatures, published
snapshot generation pointers, a file-sourced Apply Optimisation artifact and the
preamble fingerprint that execution caches already sign, together with the
identity of each local Model Scoring node's cached model file and an EBM's
cached contract, which that identity does not sign; `freshness_tokens`, the
native revision or stat token of every file the source generation signs, which
reading never touches a file's content; and `identity_components`, the parts of
that identity that are not file contents and cost only configuration reads:
the set of signed file paths and, for each run-sourced Model Scoring node, the
resolved MLflow backend identity, which also selects its model cache directory.
All three are read in the worker at the start of the check and again at its
end; when the two source generations differ the check is `not_run` with
`source_changed`, so no finding describes inputs or a model that changed under
it. A later freshness comparison re-derives the identity components from
configuration and re-observes the tokens, so it never hashes a file on the
server.
Findings are not plan facts: they are outside the plan hash, never computed or
awaited under the save lock, and never recomputed or read by apply, so they
never change what apply saves. A consumer shows findings only when both the
graph digest and the scenario they record equal those of the graph it shows. A
scenario mismatch hides them with a note naming the checked scenario, because
they describe another branch; within the same scenario, a changed or missing
freshness token (a refreshed input, a replaced model file or EBM contract) or a
changed identity component (a destination moved to another tracking server or
local folder while the old cached files stay untouched) keeps them visible,
labelled as computed from earlier inputs. A change
card shows the findings of the dry-run whose plan hash it applied under those
rules. The model-facing view states the checked scenario and omits the digests,
as dry-run results omit revisions; the stored result keeps the whole binding.

**Eligibility.** The changed nodes are the nodes the plan's semantic diff adds
or whose configuration it writes, the new ids of nodes it renames, and the
target of every edge it adds or removes, when present in the candidate graph:
the seeds of `src/haute/assistant/_application.py::diff_seed_nodes` without its
widening to every node when the preamble changes, so a preamble-only plan checks
nothing. They are taken in the candidate graph's topological order, ties broken
by node id. Each changed node is given the first of these reasons that applies,
in this order, and is checked when none does; a lineage reason names the first
offending node of the lineage in topological order, ties broken by node id:

1. `submodel`: a submodel occurrence or port, which assistant plans cannot
   write. A submodel in a checked node's lineage runs flattened, as in a preview.
2. `sink_only`: a Quote Response, Data Output, Explore, Model Training or
   Optimisation node, which has no output frame to measure.
3. `artifact_in_lineage`: the node or an ancestor is a Load File whose
   `fileType` deserialises an opaque object: `pickle`, `joblib` or `catboost`
   (the loaders in `src/haute/_io.py`). A `json` Load File is data and stays
   eligible. The check never invokes an excluded node's loader; that the
   dry-run's schema tier may already have loaded it in the server process does
   not make the check load it again.
4. `artifact_not_local`: the node or an ancestor is a Model Scoring node whose
   model is not already in the local model cache that editor previews load from,
   or an Apply Optimisation node whose artifact is not a local file. A Model
   Scoring node is local when it names an MLflow run (`sourceType` `run`) with a
   `run_id` and an `artifact_path` whose flavor loads from one file (not a
   `pyfunc` directory), and that file, with an EBM's contract file, exists in the
   disk model cache for the node's resolved destination: the files the preview's
   fast path in `src/haute/_mlflow_io.py` loads with no tracking-server,
   registry or download call. A registered model (a `version` or `alias` the
   registry resolves on every load) and a `pyfunc` artifact are never local; a
   run artifact not yet cached is not local, with the remedy to preview the
   Model Scoring node in the editor, which fills the cache. The worker loads a
   local model from that cache only: a cache file that disappears after
   eligibility is an execution failure, never a download. An Apply
   Optimisation node is local when its `sourceType` is `file`, a JSON artifact
   inside the project; `run` and `registered` sources download through MLflow
   and are not local.
5. `input_not_prepared`: the lineage reads an input in a state the table above
   does not read, naming that input, with the remedy to preview it first.
6. `node_cap`: the node comes after the first 8 changed nodes that no other
   reason excludes.

Code-mode nodes, nodes with free-code steps, nodes downstream of a submodel, a
`json` Load File and a locally cached Model Scoring node are checkable. Only the
checked scenario is checked: the dry-run proves the schema of every scenario a
Source Switch routes, while the data check measures the one a preview runs.

A check that cannot run returns `not_run` with exactly one reason, never an
error, determined in this order: `worker_mode_unsupported` (thread mode),
`not_schema_tier` (a structural plan has nothing executable to measure),
`no_checkable_nodes` (each changed node reports why), `admission_refused` (with
the admission's reason, as a preview reports it), `worker_busy` (its worker
slot was held or its worker was not running), and then whichever of these ends
the run: `deadline` (30 seconds passed), `memory_limited` (the worker exceeded
the preview memory budget),
`superseded` (a newer check in the session replaced it),
`superseded_by_preview` (an editor request needed its worker), `cancelled` (the
turn stopped), `source_changed`, or `internal_error` (a defect in the check
itself, logged with its detail and reported with the sanitized internal
detail). No partial result returns from a stopped worker. No check is attempted,
and the dry-run result carries none, when `allow_aggregate_statistics` is false
or the policy's ceiling is `public`; the turn context's policy line already
says so.

**The permission.** The check runs only when
`[assistant.egress].allow_aggregate_statistics` is true and `max_sensitivity`
is `internal` or `restricted`; [Egress and project knowledge](#egress-and-project-knowledge)
gives the flag's rules and the turn-context line. Graph-plan authority never
authorises a check.

**Where the findings appear.** The dry-run tool starts the check once its plan is
stored and the save lock released, under the turn's session, and attaches what fits
of the result to the dry-run result under `data_check`, or the omission note under
`data_check_omitted`. The whole result is kept beside the plan in the plan store,
and a later identical dry-run replaces it, so a dry-run whose policy no longer
permits a check leaves none behind. While the check runs, the dry-run's activity
row reads "Checking the data": a `tool_progress` stream event retitles a running
tool's row. Stopping the turn stops the check, which reports `cancelled`, and the
dry-run's result, carrying that check, is still the turn's record of the call. An
apply reads the stored check and labels it against the graph it saves with the
freshness comparison: `current`; `earlier_inputs`, which the card labels;
`other_scenario`, whose card keeps no findings and names the checked scenario;
and for another graph digest the change record carries no check at all. The
change record words each finding for the analyst from its counts and names ("All
1,204 rows fell into the default band of age_band.", "903 of 1,204 base rows
(75%) matched a join row."), never with a row value, a configuration value or an
error's own text (an execution failure is named by its exception type and the
step or line that raised it), at most 20 with the number omitted, and adds one
line on what was not checked: why the whole check did not run, or which changed
nodes it skipped and what would let them be checked (a node that produces no
data, such as a Quote Response, is named only when nothing was checked). The
card shows advisory findings plainly, folds informational ones away, labels
findings measured on inputs that have changed since, and says when a check that
ran found nothing. A persisted change record keeps its check, and a record saved
before data checks existed has none.

**What the model is told.** One paragraph of the system prompt says that a
dry-run can return `data_check`; that when an advisory finding shows a choice of
the model's is wrong (every row in a band's default, a join that matches nothing,
a filter that empties its input) the model corrects the plan and dry-runs again
before applying, while values the analyst stated, such as rating keys that match
no rows, stay as stated and the model tells the analyst what the check found;
that informational findings need no action; that neither a clean check nor one
that did not run proves the plan correct; and that `inspect_node`'s data part,
with `column`, answers why a saved node fails or why a column is null in one
call, before reading code. The dry-run tool's description names `data_check`, and
`inspect_node`'s description states the data part's rules. Mid-tier models follow
a tool result more closely than the system prompt (a live model applied a plan
whose check reported an advisory `join_unmatched`, twice), so a dry-run whose
check holds an advisory finding also carries `next`, one short value-free
instruction beside the check: "This plan has 2 advisory data findings
(join_unmatched, rows_emptied). Correct the plan and dry-run again before
applying, unless the analyst stated the values involved; then apply and tell the
analyst what the check found." It counts the whole check's advisory findings and
names their kinds, is placed before the check is sized, so it survives the
check's reduction and omission, and never reaches the stored check or the change
card.

**What it is not.** The check is not an assistant execution tool: a dry-run's
check runs automatically and the model cannot invoke, widen or target it, and
`inspect_node`'s data part measures counts over one saved node's lineage and
nothing more, so the system prompt's statement that no execution tool is
available stays true and running or materialising a pipeline stays unavailable.
It contains project or model-authored Python no more than a preview does. No
finding blocks an apply, and the assistant checks a saved node only when the
model asks for its data part. The evaluation scores findings in a layer of its
own beside its independent execution goldens, never in their place: what the
checks told the model, and what the harness's own check of the saved graph
still finds ([the assistant evaluation](evaluation.md#data-findings)). It
checks no inactive scenario, no lineage through an opaque Load File or a
non-local model or optimiser artifact, and no join inside code or a step list,
and it has no configurable bound, deadline, cap or threshold. The thresholds
(0.10 for rating misses, 0.5 for mostly-default bands and mostly-null columns)
are starting values for the evaluation to measure. In the evaluation's
`motor_pricing` fixture the harness logs the CatBoost model into the project
copy's local MLflow folder, which fills no disk model cache, so a check of its
scored lineage reports `artifact_not_local`.

**Failures.** A `haute.toml` whose `[assistant.egress]` lacks
`allow_aggregate_statistics` fails with a configuration error naming the key;
there is no default. A check that cannot run, an ineligible node and a failing
node are reasons and statuses in the result, never dry-run errors, and the
dry-run's plan and response are the same whatever the check reports. Findings
are never shown for a graph whose digest or scenario differs from theirs.

### A saved node's data

`inspect_node`'s data part runs the same check over the saved graph, so that
"why is `total_incurred` null for some quotes?" or "why does this node fail?" is
answered from one call rather than by guessing from code. Every rule above holds
for it, with these differences.

**What it measures.** The saved graph is parsed, with the call's project
revision, under the save lock; the check then runs after the lock is released,
as a dry-run's does. It executes the inspected node's lineage under the saved
graph's active scenario, flattened and pruned at Source Switches as above. The
nodes it would check are the lineage's top-level nodes: the inspected node and
its ancestors, never a node inside a submodel occurrence. Each is given the
eligibility reasons above in their precedence, and the node cap keeps the 8
eligible nodes nearest the inspected node (in reverse topological order) rather
than the first 8, because the question is about that node. A failure further
upstream is then reported `at_or_upstream` against the producer the nearest
measured nodes read, and nulls that arrive from beyond them show as their inputs'
nulls. The node records list every top-level node of the lineage in topological
order, ties broken by node id, and findings rank by that order.

**One error for one cause.** Because the measured nodes run in topological
order, a failure is attributed once, to the first measured node that raises it,
which carries the error record (its class, type, step or line, the columns the
policy discloses, and its text only under `allow_row_samples`); every measured
node downstream of it is `upstream_failed` naming it, and the findings hold one
`execution_failed` for it, however many downstream nodes the failure stops. A
preview of each node on its own would report the same failure again at each of
them.

**A column's nulls.** The call's optional `column` names a column to follow.
Each measured input frame and output port that carries it also counts its nulls,
and the result adds `column`: the column's `name`; `lineage`, one entry per
measured output port that carries the column, in node order, each with its
`node`, `port`, `rows`, `nulls`, `share`, `input_nulls` (its nulls summed over
the node's measured inputs that carry the column, or `null` when none does: a
source, or the node that creates the column) and `truncated` (when that port or
an input it is compared with reached the row bound); and `first_null_node`, the
node of the first entry whose nulls exceed its `input_nulls` (a `null` counting
as 0), where the column's nulls appear or grow, or `null` when no measured node
adds any. An Edge Join on the path reports its matched base rows in its node
record, so a join that leaves rows unmatched is visible beside the nulls it
creates. A column no measured frame carries gives an empty `lineage`, never an
error. Counts only: no value of the column is read into the result. Without
`column` the result's `column` is `null`.

**Permission, waiting and stopping.** The part answers only when the policy
permits data checks; otherwise it is withheld with its `required_policy` while
the other parts answer. The call waits for the check, which keeps its 30-second
deadline, admission, worker priority and refusals, and a check that cannot run
is the part's answer, `not_run` with one reason, never a tool error
(`not_schema_tier` never applies). An inspection and a dry-run's check share the
session's one check at a time, so a newer one supersedes an older one. While it
runs the call's activity row reads "Checking the data". Stopping the turn stops
the check, which reports `cancelled`, and that result is the turn's record of the
call.

**Size and keeping.** The part is reduced like a dry-run's check, in the room
the rest of the `inspect_node` result leaves under the tool-result limit; the
`column` object is never reduced. When even the empty part does not fit, the
result carries the note "The data part's result did not fit in this tool result;
ask for the data part on its own." under `data_omitted`. Nothing is kept: the
result is not stored, has no plan hash and reaches no change card.

## Provider qualification

No provider or model is qualified by a gate. Live evaluation reports, attributed
to a configuration of the support matrix, measure each configuration per area,
with its recovered-within-budget rate for data findings;
see [the assistant evaluation](evaluation.md#tiers).

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
  `find_data`.
- **[execution-engine](../execution-engine/high-level.md)** — `inspect_node`'s schema part and
  dry-run schema validation build the lazy plan through the engine's public facade
  (target-node execution, nothing collected); the assistant adds no schema logic of its
  own. Both declare `schema_only`, the engine flag stating that a caller resolves schemas
  and never materialises, so the engine's group-by memory-admission gate — which bounds
  peak memory during materialisation — does not refuse an aggregation neither of them
  runs. The [data check](#data-checks) collects through the walker's measuring purpose
  and runs in an interactive worker through the pool's pre-emptible acquisition, so an
  editor preview always takes the worker back from it.
- **[sandbox-security](../sandbox-security/high-level.md)** — assistant-authored node code
  (e.g. a `polars` body) is validated and sandboxed identically to human-authored code; the
  assistant adds no bypass.
- **[frontend-assistant-ui](../frontend-assistant-ui/high-level.md)** — the sole consumer of the
  assistant HTTP surface; owns the clean-canvas send gate and renders each change card,
  its data check included.
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
- **A Claude model without adaptive thinking** — readiness reports an Anthropic model
  outside the supported set as not ready, with a reason naming the model and the supported
  ones, so the panel's readiness card says so before a message is sent and the message
  route answers 400 with the same reason; constructing the Anthropic adapter for such a
  model raises the same `ConfigError`. The adapter never runs a model with its thinking or
  effort silently dropped.
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
  A node setting a config parser refuses (a rating row without its factor, number
  breakpoints on a Date column) is a typed `ConfigSettingError` naming the setting, and
  reaches the model as a located, retryable error at the node and the operation that wrote
  it, with the parser's fix ("a Date column's boundaries are dates like 2024-12-31"),
  rather than as the internal-failure detail. A schema failure is located at the node that
  raised it, with the operation that touched that node when one did, while its message
  names the terminal whose validation reached it. Only a genuine internal failure keeps
  the sanitized internal detail. A read or edit of a submodel or a node inside one is
  refused as not retryable, saying the assistant cannot read or edit it and should report
  a blocker asking the analyst to edit it in the editor.
- **Every scenario a Source Switch routes is validated** — a dry-run refuses a switch the
  plan touches that routes a scenario to no connected input (`scenario_unrouted`), naming
  the inputs its incoming edges do provide; an input is named by its edge's source node (a
  Quote Input's table label), never by an edge's target handle, so a plan that maps the
  handle it gave a new edge is told the name that edge provides and the mapping to write; and
  resolves every target again under each other scenario a switch maps, so dropping an input
  a batch scenario needs fails loudly instead of passing because only the live scenario
  was checked.
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
