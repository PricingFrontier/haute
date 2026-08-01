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
  rows), the message composer, the stop control, and the new-chat control.
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
- Canvas updates. Assistant mutations arrive as ordinary `graph.update` frames handled by
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md)'s WebSocket sync; this
  component never writes to the graph store.
- The shared side-panel shell chrome, owned by
  [frontend-node-editors](../frontend-node-editors/high-level.md), and the shared API-client
  machinery, stores, toasts, and theme tokens owned by
  [frontend-shared](../frontend-shared/high-level.md).

## Behaviour

**Opening the panel.** The assistant surface sits in the right-panel area alongside the
existing inspector surfaces (node config, trace, imports, utility scripts, git), opened from
the same chrome that opens those. Its body is lazy-loaded on first open, like the node
editors, so the chat feature costs the initial bundle nothing.

**Readiness gates the composer, with the reason visible.** On open, the panel queries the
backend's assistant status. An unconfigured assistant renders the composer disabled with the
backend-supplied reason (no `[assistant]` config, missing API key, unknown provider, extra
not installed) — never a send that bounces. Mutations-disabled (working branch not ready —
no repository, unset, detached, divergent, or invalid) renders the same way, with the backend's per-state reason: authoring is this panel's whole
purpose, so an assistant that could talk but not edit would only mislead. The status is
re-checked on every panel open, not polled.

Readiness also shows the effective provider endpoint host, asserted trust
class, and maximum sensitivity without displaying credentials or URL query
material. Invalid or missing egress policy disables sending with the
field-specific backend migration reason.

**A turn streams into the transcript live.** Sending a message appends the user entry,
disables the composer, and swaps the send button for a stop button. Assistant text renders
incrementally as deltas arrive. Tool activity renders as compact rows in-place in the
transcript — "reading pipeline", "applied 3 edits", with failures marked distinctly — so the
analyst can follow what the agent actually did, in order. A graph-updated event annotates
the transcript; the canvas itself updates via live-sync, not via this panel.

**Graph authoring applies without a second permission prompt.** The user's
message authorizes graph authoring. A validated plan may therefore apply
directly whether it adds Polars code, configures an output, deletes graph
elements, changes a preamble, or contains a large operation batch. The
frontend renders the ordinary tool activity and graph-updated events; it has
no graph-plan confirmation card or confirmation request. Exact plan hashes,
revision checks, single-use authority, transactional saves and post-save
verification remain server-owned. Actually running the pipeline or performing
an external write remains a separate user-initiated execution action; v1
exposes no assistant execution tool.

**The clean-canvas gate.** The composer refuses to send while the graph has unsaved local
edits, showing why ("save or discard your canvas changes first") — because the assistant
operates on the saved pipeline, and because an incoming live-sync update while dirty would
hit the canvas's reload-or-discard banner instead of applying. The gate derives from the
canvas's existing dirty state; this component adds no dirty tracking of its own.

**The top-level-view gate.** The composer likewise refuses to send while the canvas is
drilled into a submodel, showing why: v1 tools author the top-level graph only, and letting
the agent rewire a graph the analyst is not currently looking at invites unseen changes.
The gate derives from the canvas's existing submodel-navigation state (supplied by the app
shell, which owns it), exactly as the dirty gate derives from the canvas's dirty state.

**Stop is immediate.** Stop aborts the stream request; the backend halts between tool
executions. The transcript keeps everything already streamed and adds its generic stopped
marker. Edits already applied remain applied (they are real saves); the marker does not imply
an undo, but it also does not spell that consequence out.

**One turn, one locally keyed session.** The store holds one active session, created lazily on
first send. The current source-file string scopes the browser's remembered-session key and
causes a source change to clear the in-memory transcript/session; it is not passed as the
`pipeline` value in the session-create request (the current client sends `null`). While a turn
is in flight the composer is locked from before session creation until the response body has
ended (stop is the only action); the backend's 409 on concurrent
sends therefore has exactly one
normal-operation window — a send in the moments after a stop, while the backend finishes an
in-flight edit — rendered with its own "still finishing" notice rather than a generic
error.
New-chat discards the transcript and session id; the next send creates a fresh session.
Conversations survive both page reloads and server restarts: the store remembers the
session id in `localStorage` (keyed per pipeline source), offers it back on session
create, and rehydrates the transcript from the history the backend returns — the backend
persists committed turns per clone in `.haute/`, so the restart-everything workflow of a
locally-run tool keeps its chat. When a remembered session is truly gone (pruned, cleaned
`.haute/`, different pipeline) the backend hands out a fresh session and the panel simply
starts blank; a 404 on *send* still renders the explicit session-expired notice.

**Failures are inline and loud.** A turn that terminates with the backend's `failed` event
renders the typed error message inline in the transcript at the point of failure (plus an
error toast), and re-enables the composer. A transport drop mid-stream is rendered as an
interrupted turn, distinct from a completed one — never silently truncated prose that reads
as if the model finished. Parser, callback, and transport failures explicitly cancel the
response reader so the backend sees the disconnect and stops mutating. An event after a
terminal frame is a contract violation: it cancels the response and renders the turn
interrupted rather than silently preserving a false completed state.

**Assistant responses are validated at the feature boundary.** Status and
session JSON, every history row, and every field of all eight SSE variants are
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
- **No optimistic transcript persistence.** The browser persists only a per-source session id,
  not transcript entries. On resume it replaces its empty/in-memory transcript with the
  server-returned history before appending the new turn; this avoids reconciling speculative
  client transcript state after reload.

## Interactions

- **[assistant](../assistant/high-level.md)** — the backend surface this component consumes:
  status, session create, and the streamed message endpoint; the typed SSE event contract is
  owned there (in the shared schemas module) and consumed here.
- **[frontend-shared](../frontend-shared/high-level.md)** — the split API-module pattern and
  `request`/`post` machinery, the toast store for failure surfacing, the UI store for
  panel-visibility chrome, theme tokens, and the error boundary the panel mounts inside.
- **[frontend-graph-canvas](../frontend-graph-canvas/high-level.md)** — supplies the derived
  dirty state that drives the clean-canvas gate, and applies assistant mutations via its
  existing WebSocket sync; this component reads canvas state, never writes it.
- **[frontend-node-editors](../frontend-node-editors/high-level.md)** — the shared
  side-panel shell chrome the panel renders inside, and the lazy-loading convention it
  follows.

## Failure model

- **Status fetch failure** renders the panel's error state with retry — an assistant of unknown
  readiness never presents an enabled composer.
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
