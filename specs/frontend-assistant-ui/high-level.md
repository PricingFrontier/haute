# Frontend Assistant UI — High-Level Specification

## Purpose

The backend [assistant](../assistant/high-level.md) exposes a chat API whose turns stream
assistant text and graph-mutation activity. This component is the browser half: the chat
panel where a pricing analyst converses with the assistant, watches it work, and stops it —
while the canvas itself updates through the ordinary live-sync channel, untouched by this
component. It exists as its own component (rather than growing the node inspector or the
shared chrome) for the same reason the git and trace panels do: it is a self-contained
right-panel feature with its own store, its own API module, and its own failure surface.

## Scope

In scope:

- The assistant panel: transcript (user messages, streamed assistant text, tool-activity
  rows, change cards), the message composer with its context chips, the stop control, the
  new-chat control, the model name in the header, and the new-chat empty state.
- The assistant's chrome outside the panel: the "Assistant is working" pill on the canvas,
  the toolbar button's progress and unseen-outcome states, and the "Ask the assistant to
  fix" action beside a node's run error.
- The assistant Zustand store: session id, transcript, streaming state, and the derived
  can-send gate.
- Consuming the assistant SSE stream (fetch + ReadableStream) and translating typed events
  into store updates.
- Readiness handling: querying assistant status and rendering the disabled-with-reason
  composer states.
- The panel's mount point in the right-panel surface and its lazy loading.

Out of scope:

- Everything server-side — loop, tools, providers, sessions — see
  [assistant](../assistant/high-level.md).
- Canvas updates. Assistant mutations arrive as ordinary `pipeline_document_update` frames
  handled by [frontend-graph-canvas](../frontend-graph-canvas/high-level.md)'s WebSocket
  sync, which rings and centres the nodes a frame's assistant origin names; this component
  never writes to the graph store.
- The shared side-panel shell chrome, owned by
  [frontend-node-editors](../frontend-node-editors/high-level.md), and the shared API-client
  machinery, stores, toasts, and theme tokens owned by
  [frontend-shared](../frontend-shared/high-level.md).

## Behaviour

**Opening the panel.** The assistant surface sits in the right-panel area alongside the
existing inspector surfaces (node config, trace, imports, utility scripts, git), opened from
the same chrome that opens those. Its body is lazy-loaded on first open, like the node
editors, so the chat feature costs the initial bundle nothing. The panel header names the
configured model and provider (for example `databricks-qwen35-122b-a10b · databricks`)
once the status reports them.

**A new chat says what the assistant can do.** A chat with no messages yet shows, for the
pipeline the canvas shows, what the assistant can do (build and edit nodes as steps,
banding, rating, joins, outputs, and modelling setup) and what it cannot (run the
pipeline, train models, deploy, or use Git), with three starter prompts. Choosing a
starter prompt puts its text in the composer for the analyst to edit and send; it never
sends by itself.

**Readiness gates the composer, with the reason visible.** On open, the panel queries the
backend's assistant status. An unconfigured assistant renders the composer disabled with the
backend-supplied reason (no `[assistant]` config, missing API key, unknown provider, missing
`[assistant].egress` policy, or a provider SDK missing from a broken installation) — never a
send that bounces. Mutations-disabled (working branch not ready —
no repository, unset, detached, divergent, or invalid) renders the same way, with the backend's per-state reason: authoring is this panel's whole
purpose, so an assistant that could talk but not edit would only mislead. The status is
re-checked on every panel open, not polled.

The opening chat-list screen states the same readiness before any chat is opened: an
unconfigured assistant or one whose edits are disabled (the egress policy denies them, Git
is unavailable, or the working branch is not ready) shows a readiness card whose heading
names what is unavailable ("Assistant is not set up" or "Assistant cannot edit this
project"), the backend's reason verbatim (each reason names its own fix: the `haute.toml`
table, the environment variable, the Git panel action, or installing Git), and a "Check
again" action that re-reads the status once the analyst has made the fix.

Readiness also shows the effective provider endpoint host, asserted trust
class, and maximum sensitivity without displaying credentials or URL query
material. A missing `[assistant].egress` table renders as an unconfigured
readiness reason in the composer and on the list screen. A malformed `haute.toml` or an
invalid egress policy fails the status fetch with a 400 whose detail is the reason; the
panel's error state shows that detail and says to fix `haute.toml`, then check again,
rather than offering a bare retry that cannot succeed until the file changes.

**A turn streams into the transcript live.** Sending a message appends the user entry,
disables the composer, and swaps the send button for a stop button. Assistant text renders
incrementally as deltas arrive. While a Claude model thinks, a "Thinking…" status shows
below the transcript until its next text or tool row; what it thinks is never shown or
kept. Tool activity renders as compact rows in-place in the
transcript, each headed by the plain-words title the backend writes ("Reading the
pipeline", "Checking 3 changes", "Applying 3 changes") rather than the tool's name, with
failures marked distinctly — so the analyst can follow what the agent actually did, in
order: text streamed after a tool row renders below it as a new text segment, never back
in an earlier bubble. After each apply a change card shows what was saved: the plan's
summary and the assumptions it made, one chip per node added, changed, removed or renamed
with its palette type, the fields it changed in words and its step kinds, the edges added
and removed, the save's warnings, and the short commit id. The card never shows a
configuration value or step code. The canvas itself updates via live-sync, not via this
panel: when an assistant save or undo lands, the canvas rings the nodes it changed and
centres them, instead of fitting the whole graph.

**Undo and Compare on each change card.** A card saved to Git offers "Undo this change"
and "Compare". Undo is enabled only while the canvas shows the document revision that
change produced, so it is offered on the latest change to the pipeline and on no card
after a later save; while a turn runs or an undo is in flight it is disabled too, and its
title says why. Clicking it asks the backend to save the version before the change; the
transcript then shows a note that the change was undone, the canvas updates through
live-sync, and a refusal (a later save, a running turn) is shown as the panel notice in the
backend's words. Compare opens the read-only comparison view with the version before the
change on the historical side. A card not saved to Git offers neither.
A resumed chat renders the same entries in the same order as the live turn did.

**Every completed turn ends with its outcome.** The completed event's typed outcome
decides how the turn closes. `applied` and `answered` close with the ordinary completed
marker ("Changes applied" or "Turn completed"). `needs_input` renders a question card
holding the model's question, in place of the raw `NEEDS_INPUT:` text, with a one-click
reply "You choose, and tell me what you picked." that sends that message as the next turn
(available on the latest turn only, under the same send gate as the composer). `blocked`
renders a blocked card with the reason, in place of the raw `BLOCKED:` text, stating that
nothing was saved, or how many changes the turn saved before it was blocked, whose cards
are above it; a question card counts the changes saved before the question the same way. `committed_unverified` renders a card stating that the changes were
saved but the post-save check failed, with the check's error, a prompt to review the
pipeline or undo the change, and "Undo this change" as its primary action for the change
whose check failed (the last change the outcome lists), enabled under the change card's
rule; it never says that nothing changed. `incomplete` renders a card titled "Stopped before finishing" that tells the
analyst to ask it to continue, states that nothing was saved (or how many changes were),
and shows the reason the controller gave. A turn may save several changes, each with its
own change card, before it ends.

**Graph authoring applies without a second permission prompt.** The user's
message authorizes graph authoring. A validated plan may therefore apply
directly whether it adds Polars code, configures an output, deletes graph
elements, changes a preamble, or contains a large operation batch. The
frontend renders the ordinary tool activity and change cards; it has
no graph-plan confirmation card or confirmation request. Exact plan hashes,
revision checks, single-use authority, transactional saves and post-save
verification remain server-owned. Actually running the pipeline or performing
an external write remains a separate user-initiated execution action; v1
exposes no assistant execution tool.

**The clean-canvas gate.** The composer refuses to send while the graph has unsaved local
edits, showing why ("save or discard your canvas changes first") — because the assistant
operates on the saved pipeline, and because an incoming live-sync update while dirty would
hit the canvas's reload-or-discard banner instead of applying. The gate derives from the
canvas's existing dirty state; this component adds no dirty tracking of its own. The
read-only canvas during a turn (below) is its complement: the canvas cannot become dirty
while a turn runs, so the turn's own live-sync updates always apply.

**The canvas is read-only while a turn runs.** From the moment a send acquires the turn
until the turn ends, the canvas takes the same editing fence as a read-only document: nodes
cannot be moved, connected, added, deleted, renamed or configured; the palette, Undo, Redo,
Layout, Utility, Imports, Save and Commit are disabled; and the node panel opens read-only.
Selection, pan, zoom, previews and traces stay usable, and the turn's saves still reach the
canvas through live sync, which the fence never blocks. A pill over the canvas reads
"Assistant is working" with a Stop button that stops the turn exactly as the composer's
Stop does. The toolbar's Assistant button stays enabled during a turn, so the panel that
shows and stops it is always reachable.

**The toolbar shows a turn's progress and an unseen outcome.** While a turn runs, the
toolbar's Assistant button shows a spinner in place of its icon and its title says the
assistant is working. A turn that ends while the panel is closed leaves a dot on the
button, titled to say the assistant finished; opening the panel clears it.

**The composer shows what a message carries.** Above the composer, a chip names the
selected canvas nodes the next message will carry as context (the first three labels and a
count of the rest; at most 20 are sent), so the analyst points the assistant at nodes by
selecting them. With nothing selected there is no selection chip.

**Ask the assistant to fix a run error.** When a top-level node's preview fails, the error
offers "Ask the assistant to fix". It selects that node alone on the canvas and opens the
panel: on a new chat when the panel would show the chat list, in the open chat otherwise.
An empty composer is filled with a fix request, and an error chip names the node: the next
message carries it as the preview-error node, whose error the backend reproduces for the
model. The chip has a dismiss control, and the message that carries it clears it. The
action is not offered inside a submodel, because the assistant authors the top-level graph
only.

**The document-readiness gate.** The composer also refuses to send while the current editor
document is degraded, source-only, or has not synchronized its retained canvas with the
authoritative document revision. Assistant authoring always starts from a strict on-disk graph;
it must not turn a recovery document or stale retained canvas into an indirect mutation path.
The app supplies the same central document-read-only gate used by Save and canvas editing, and
the store rechecks it when a message is sent rather than trusting button state alone.

**The top-level-view gate.** The composer likewise refuses to send while the canvas is
drilled into a submodel, showing why: v1 tools author the top-level graph only, and letting
the agent rewire a graph the analyst is not currently looking at invites unseen changes.
The gate derives from the canvas's existing submodel-navigation state (supplied by the app
shell, which owns it), exactly as the dirty gate derives from the canvas's dirty state.

**Stop is immediate.** Stop aborts the stream request; the backend halts between tool
executions. The transcript keeps everything already streamed and adds its generic stopped
marker. Edits already applied remain applied (they are real saves); the marker does not imply
an undo, but it also does not spell that consequence out.

**One turn, one selected session.** The store holds one active session, created lazily on
first send or selected explicitly from the backend conversation list. Every chat is bound to
the pipeline document the canvas shows: the panel sends that document's source file with
the chat-list request, session creation and every message, the list shows only that
pipeline's conversations, and a source change while idle returns the panel to the new
pipeline's list. Should the canvas show another pipeline than the open chat's, the composer
refuses to send and names the chat's pipeline instead of starting a conversation silently;
a canvas with no saved source file cannot send at all. While a turn
is in flight the composer is locked from before session creation until the response body has
ended (stop is the only action); the backend's 409 on concurrent
sends therefore has exactly one
normal-operation window — a send in the moments after a stop, while the backend finishes an
in-flight edit — rendered with its own "still finishing" notice rather than a generic
error.
New-chat discards the transcript and session id; the next send creates a fresh session.
Conversations survive both page reloads and server restarts because the backend persists
committed turns per clone in `.haute/`; the panel opens on that list and rehydrates only the
conversation the user selects. The browser remembers no session id. If a listed conversation
disappears before it is opened (pruned or cleaned `.haute/`), the backend returns a fresh blank
session; a 404 on *send* still renders the explicit session-expired notice.

**Failures are inline and loud.** A turn that terminates with the backend's `failed` event
renders the typed error message inline in the transcript at the point of failure (plus an
error toast), and re-enables the composer. A transport drop mid-stream is rendered as an
interrupted turn, distinct from a completed one — never silently truncated prose that reads
as if the model finished. Parser, callback, and transport failures explicitly cancel the
response reader so the backend sees the disconnect and stops mutating. An event after a
terminal frame is a contract violation: it cancels the response and renders the turn
interrupted rather than silently preserving a false completed state.

**Assistant responses are validated at the feature boundary.** Status and
session JSON, every history row, and every field of all seven SSE variants are
checked at runtime before they become typed values or reach a store callback.
Required object/array/primitive shapes are closed while unrelated additional
fields are tolerated for additive compatibility. Contract drift raises a
descriptive ordinary `Error`, not `ApiError`, and a rejected stream frame
cannot partially append text or activity.

## Design rationale

- **A separate panel component, not a node-inspector tab body grown in place.** The chat
  panel has no selected-node dependency, holds long-lived conversational state, and streams;
  the inspector's editors are selection-scoped and request/response. Splitting keeps the
  heavily-tested node panel untouched — the same reasoning that keeps the read-only config
  inspector a parallel component.
- **A dedicated store, per the stores-by-concern rule.** Chat state changes on every streamed
  delta; putting it in an existing store would re-render unrelated consumers on every token.
  The transcript store is subscribed to only by the panel.
- **Fetch-stream SSE consumption in a split API module.** The assistant endpoints live in
  their own `api/` module (the established bundle-split pattern for lazy-panel-only
  endpoints), reusing the shared client's machinery for the non-streaming calls and owning
  the SSE reader for the message stream — POSTs are never auto-retried by the shared client,
  which is exactly right for a mutating chat turn. Concrete JSON and stream-event parsing
  stays local to the module so typed transport assertions cannot bypass the runtime boundary;
  malformed known variants and unrecognised discriminators both fail loudly.
- **The canvas stays the single writer of graph state.** This panel deliberately has no path
  to mutate the graph store. Assistant edits reach the canvas exactly the way IDE edits do —
  one channel, one apply/rollback/dirty-gating behaviour, zero new reconciliation logic. The
  clean-canvas send gate is the complement: it prevents the one situation (dirty canvas)
  where that single channel would park an update behind a banner.
- **Markdown rendering for assistant text.** Model output is markdown-shaped (code fences,
  lists); rendering it as such is table stakes for a chat product surface. The renderer is
  loaded with the lazy panel body so its cost never lands in the initial bundle (the
  bundle-size gate stays authoritative).
- **The running turn is mirrored into the UI store.** The canvas pill, the toolbar button
  and the fix action are in the initial bundle; the panel, its store and its API module are
  not. The assistant store therefore mirrors the running turn (with its Stop) and the
  unseen outcome into the shared UI store at exactly the points where it acquires and
  releases the turn, and the fix action writes its preview-error node there. The eager
  chrome reads only the UI store, the chat stays a lazy chunk, and the assistant store
  remains the single owner of the turn lock.
- **A read-only canvas, not a dirty-canvas banner, during a turn.** The analyst's edits and
  the turn's saves both change the pipeline file. Fencing the canvas for the turn's
  duration keeps one writer at a time, with Stop always one click away, instead of letting
  a mid-turn edit park the turn's update behind the reload-or-discard banner.
- **No browser-side conversation persistence.** The browser persists neither ids nor transcript
  entries. The backend list is authoritative, and opening a selected conversation replaces the
  empty/in-memory transcript with the server-returned history before another turn can use it;
  this avoids reconciling speculative client state after reload.

## Interactions

- **[assistant](../assistant/high-level.md)** — the backend surface this component consumes:
  status, session list/create, the streamed message endpoint and the undo endpoint; the typed SSE
  event contract is owned there (in the shared schemas module) and consumed here.
- **[git-integration](../git-integration/high-level.md)** — Compare opens the git store's
  read-only comparison on the change's parent commit.
- **[frontend-shared](../frontend-shared/high-level.md)** — the split API-module pattern and
  `request`/`post` machinery, the toast store for failure surfacing, the UI store for
  panel-visibility chrome, theme tokens, and the error boundary the panel mounts inside.
- **[frontend-graph-canvas](../frontend-graph-canvas/high-level.md)** — supplies the derived
  dirty state that drives the clean-canvas gate and the document revision that enables Undo,
  applies assistant mutations via its existing WebSocket sync, and folds a running turn into
  its editing fence; this component reads canvas state and selection, never writes the
  graph. The fix action changes only the canvas selection, through the canvas's own
  selection changes.
- **[frontend-preview-explore](../frontend-preview-explore/high-level.md)** — the data
  preview's error state hosts the "Ask the assistant to fix" action the app shell supplies.
- **[frontend-node-editors](../frontend-node-editors/high-level.md)** — the shared
  side-panel shell chrome the panel renders inside, and the lazy-loading convention it
  follows.

## Failure model

- **Status fetch failure** renders the panel's error state — an assistant of unknown
  readiness never presents an enabled composer. A 400 names its reason and the fix
  (`haute.toml`) beside "Check again"; any other failure offers a retry.
- **Session-list fetch failure** leaves the transcript untouched and renders a retryable error state on
  the list screen; it never blanks the panel.
- **Send-time rejections** (400 unconfigured, 404 stale session, 409 concurrent turn) map to
  distinct inline messages; the stale-session case offers starting a new chat, and none of
  them silently retry.
- **A `failed` terminal event** renders the backend-provided error message inline at the
  failure point plus an error toast; the composer re-enables.
- **A transport drop mid-stream** (network error, aborted reader without a terminal event)
  marks the turn interrupted — visually distinct from completed — and re-enables the
  composer.
- **Malformed assistant payloads throw** in the API module's parser. Status/session
  failures stop before typed state is returned; malformed known SSE variants and
  unrecognised event types surface as an interrupted turn with an error toast after
  cancelling the reader. Contract drift is a bug to surface, not data to coerce or skip.
- **A crash anywhere in the panel** is contained by the error boundary it mounts inside; the
  canvas, inspector, and toolbar are unaffected.
