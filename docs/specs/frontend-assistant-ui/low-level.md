# Frontend Assistant UI — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `frontend/src/panels/assistant/AssistantPanel.tsx` | The panel body (default export, loaded via `React.lazy`): `PanelShell` + `PanelHeader` chrome, the transcript list (auto-scrolled while streaming), and the composer mount. Renders entirely from `useAssistantStore` selectors. |
| `frontend/src/panels/assistant/TranscriptEntryView.tsx` | Renders one transcript entry by `kind`: user bubble, assistant markdown segment (streamed text), tool-activity row (running/ok/error states), or turn marker (completed/failed/stopped/interrupted). Owns the markdown rendering (see Control flow). |
| `frontend/src/panels/assistant/Composer.tsx` | Message input, send/stop split behaviour, and the disabled-state messaging (streaming lock, unconfigured/mutations-disabled reasons from status, dirty-canvas notice, drilled-into-submodel notice). Receives `isInsideSubmodel` from the panel and forwards it into `sendMessage`. |
| `frontend/src/stores/useAssistantStore.ts` | Zustand store owning session id + pipeline binding, transcript entries, turn status, the in-flight `AbortController`, and the `sendMessage`/`stopTurn`/`newChat`/`refreshStatus` actions. The SSE consumption loop runs inside `sendMessage` (module scope), so a turn survives the panel unmounting. |
| `frontend/src/api/assistant.ts` | Bundle-split endpoint module (module-map row owned by [frontend-shared](../frontend-shared/low-level.md); its behaviour is specced here as its sole consumer): `getAssistantStatus`, `createAssistantSession`, and `streamAssistantMessage` — a `fetch` + `ReadableStream` SSE reader built on the authenticated raw-stream helper `client.ts` exports for split modules (auth headers + `ApiError` mapping without the JSON parse), with local event parsing that throws on an unrecognised event type. |
| `frontend/src/App.tsx` *(modified)* | New branch in the right-panel if/else cascade: `assistantOpen` renders the lazy `AssistantPanel` inside `<ErrorBoundary name="AssistantPanel">` + `Suspense`; sits ahead of the `NodePanel` default alongside the git/utility/imports branches. Passes `isInsideSubmodel` (derived from its submodel-navigation view stack, which is hook-local state a module-scope store cannot read) into the panel as a prop. |
| `frontend/src/stores/useUIStore.ts` *(modified)* | New `assistantOpen` flag + `setAssistantOpen`, mutually exclusive by construction with `gitOpen`/`utilityOpen`/`importsOpen` (each setter clears the others, matching the existing pattern). |
| `frontend/src/components/Toolbar.tsx` *(modified)* | Assistant toggle button next to the existing utility/imports buttons, calling `setAssistantOpen`. |

## Key types and data structures

- **`AssistantStatus`** (`api/assistant.ts`, mirrored from `schemas.py`):
  `{ configured: boolean; reason: string | null; provider: string | null; model: string |
  null; mutations_enabled: boolean; mutations_reason: string | null }`.
  `reason` (unconfigured) and `mutations_reason` (working branch not ready — the backend's
  per-state message for unset/divergent/invalid) are the backend's human-readable
  explanations; the composer renders whichever applies verbatim.
- **`AssistantStreamEvent`** (`api/assistant.ts`, mirrored from the backend SSE contract in
  `schemas.py`) — discriminated union on `type`:
  `text_delta { text }` · `tool_started { id, name, summary }` ·
  `tool_finished { id, name, is_error, summary }` · `graph_updated { fingerprint }` ·
  `completed { usage: { input_tokens, output_tokens } }` · `failed { message }` ·
  `cancelled {}`. The parser throws on any other `type` — contract drift surfaces, never
  skips.
- **`TranscriptEntry`** (`stores/useAssistantStore.ts`) — union on `kind`:
  `{ kind: "user"; text }` · `{ kind: "assistant"; text; streaming: boolean }` ·
  `{ kind: "activity"; id; name; state: "running" | "ok" | "error"; summary }` ·
  `{ kind: "marker"; outcome: "completed" | "failed" | "stopped" | "interrupted"; detail?: string }`.
- **`AssistantStoreState`**: `sessionId: string | null`, `pipelineSource: string | null` (the
  pipeline the session was opened against), `entries: TranscriptEntry[]`,
  `turnStatus: "idle" | "streaming"`, `status: AssistantStatus | "unknown" | "error"`, the
  private in-flight `AbortController`, and the actions listed in the module map.

## Control flow

**Open.** Toolbar button → `setAssistantOpen(true)` (clears the other right-panel flags) →
`App.tsx` cascade renders `Suspense(lazy AssistantPanel)` in its error boundary → a mount
effect calls `refreshStatus()` unconditionally on every open (the previous status stays
rendered as the loading state; never polled).

**Send.** `Composer` submit → `useAssistantStore.getState().sendMessage(text)`:

1. Guards, in order: `turnStatus === "idle"`; `status.configured` and
   `status.mutations_enabled`; not inside a submodel — `sendMessage(text,
   {isInsideSubmodel, currentSourceFile})` receives both flags as arguments because the
   view stack AND the loaded pipeline's source are hook-local state in `App`
   (`usePipelineAPI`) that a module-scope store action cannot read (App → panel →
   composer prop chain); `currentSourceFile` equals `pipelineSource` (mismatch
   resets session + transcript first); `useGraphStore.getState().dirty === false`. A failed
   guard renders as composer messaging, not a thrown error; whitespace-only input is a
   no-op.
2. Ensure a session: `createAssistantSession(pipeline, rememberedSessionId)` on first
   send — the remembered id comes from `localStorage`
   (`haute.assistant.session:<sourceFile>`); the response's `session_id` is stored to both
   state and `localStorage`, and a non-empty `history` (the backend's transcript mapping
   of a resumed session) hydrates `entries` before the new turn's entries append.
3. Append the user entry, open a streaming assistant entry, set `turnStatus = "streaming"`,
   stash a fresh `AbortController`.
4. `streamAssistantMessage(sessionId, text, signal)` — POST via the exported authenticated
   raw-stream helper, then read the response body: chunks are buffered and split on the
   SSE frame delimiter (frames may span chunk boundaries; one chunk may carry several
   frames), each frame's `data:` payload is `JSON.parse`d into a `AssistantStreamEvent`, and
   each event is applied to the store: `text_delta` appends to the open assistant entry;
   `tool_started`/`tool_finished` append/settle an activity row; `graph_updated` appends an
   activity row noting the canvas was updated (the canvas itself refreshes via `/ws/sync`,
   not here); a terminal event closes the assistant entry, appends the matching marker, and
   sets `turnStatus = "idle"`.
5. The loop runs in the store action, not a component effect — closing the panel (or another
   panel's setter forcing `assistantOpen` false) does not stop a running turn; reopening shows
   the live state.

**Stop.** `stopTurn()` aborts the controller; the reader's `AbortError` is caught as the
expected stop path → marker `stopped`, `turnStatus = "idle"`. The backend notices the
disconnect and halts between tool executions; edits already applied stay (the marker text
says so). One deliberate consequence: the backend keeps the session lock until an in-flight
shielded mutation completes, so a send immediately after stop can hit a **transient 409**
— rendered as its own inline notice ("the assistant is still finishing its last edit — try
again in a moment"), never auto-retried. A stopping/acknowledgement handshake was
considered and rejected for v1: it would need a second channel purely to shave a rare,
self-resolving retry.

**New chat.** Enabled only while idle: clears `entries`, `sessionId`, `pipelineSource`.

**Markdown.** Assistant text renders through a markdown renderer (GFM, raw HTML disabled)
imported inside the lazy panel chunk so it never reaches the initial bundle; fenced code
renders as styled `<pre>` blocks using the shared theme tokens — no syntax highlighter in
v1 (a per-message CodeMirror instance is deliberately avoided; see high-level rationale on
bundle/perf).

## Edge cases and invariants

- **SSE framing**: partial frames across reads are buffered; multiple frames in one read are
  all applied in order. An empty keep-alive frame is ignored without error.
- **Stream ends without a terminal event** (network drop, server crash): marker
  `interrupted` — never rendered as a completed turn.
- **Unrecognised event type throws** in the parser → turn marked `interrupted` + error
  toast. Contract drift is loud.
- **Canvas dirtied mid-turn** (the analyst edits while the agent works): the send-time gate
  can't prevent it. Incoming `graph.update` frames then hit the canvas's existing
  dirty-guard banner (reload/discard) rather than applying — the transcript still records
  `graph_updated` activity, and resolution happens in the canvas, not this panel. Accepted
  v1 behaviour, documented rather than special-cased.
- **Drilled into a submodel mid-turn**: likewise send-time-gated only. A running turn keeps
  editing the top-level graph; the drilled-in view is rebuilt from the parent graph on
  navigation, so the analyst sees the result on drill-out. Accepted v1 behaviour.
- **One turn per session is client-enforced** (composer locked while streaming), so the
  backend's 409 has exactly one normal-operation window: the moments after a stop while the
  backend finishes a shielded mutation and releases the session lock. That case renders the
  specific still-finishing notice (see Stop); any other 409 renders inline like any
  turn-start failure.
- **`turnStatus` is the single lock**: send, new-chat, and session reset all check it; no
  boolean flags are duplicated elsewhere.
- **Store survives panel unmount by design** — the invariant is that `turnStatus`
  transitions only via `sendMessage`'s terminal handling or `stopTurn`, never by unmount.

## Error handling

| Failure | Surfaced as |
|---|---|
| `refreshStatus` fetch failure | `status = "error"` → panel body renders an error state with a retry button; composer never enabled on unknown readiness. |
| Send-time `ApiError` 400 (unconfigured) | Inline notice with the backend detail; `refreshStatus()` re-run so the composer gate shows the current reason. |
| Send-time `ApiError` 404 (stale session) | Inline "session expired (server restarted)" notice offering New chat; no silent re-create. |
| Send-time `ApiError` 409 right after a stop | The still-finishing inline notice; composer stays enabled; no auto-retry. |
| Send-time `ApiError` 409 otherwise | Inline notice; composer state re-derived from the store. |
| Terminal `failed` event | Marker `failed` with the backend's sanitized message inline + error toast (`useToastStore`). |
| Parser throw / transport drop mid-stream | Marker `interrupted` + error toast; composer re-enabled. |
| Caller-initiated `AbortError` (stop) | Marker `stopped`; not an error, no toast. |
| Render crash anywhere in the panel | Contained by `<ErrorBoundary name="AssistantPanel">`; canvas/toolbar/inspector unaffected. |

## Testing

Frontend tests follow the existing colocated component/store test conventions. Scenarios the
TRIP-2 gate authors (named here, not written):

- **Store transitions**: delta append into the open assistant entry; activity row
  started→ok/error settlement; each terminal event's marker + `turnStatus` reset; `newChat`
  refused while streaming.
- **SSE parsing**: frames split across chunk boundaries; multiple frames per chunk;
  keep-alive frames ignored; unknown event type throws; stream end without terminal event →
  `interrupted`.
- **Send gates**: dirty canvas blocks with notice; unconfigured blocks and renders the
  backend reason; mutations-disabled blocks and renders `mutations_reason`; drilled into a
  submodel blocks with notice (depth > 1); streaming locks the composer; pipeline-source
  mismatch resets the session before sending; whitespace no-op.
- **Status refresh**: `refreshStatus()` fires on every panel open, keeping the prior status
  rendered while loading.
- **Stop semantics**: abort marks `stopped`, keeps prior entries, re-enables the composer;
  a 409 on the immediately-following send renders the still-finishing notice without
  auto-retry.
- **Session recovery**: 404 on send renders the stale-session notice and New chat clears to
  a working state; a remembered `localStorage` session id is offered on session create and
  a returned `history` hydrates the transcript (reload/restart resume); a fresh id in the
  response replaces the remembered one; New chat clears the remembered id.
- **Lazy-loading enforcement**: `App.tsx` never statically imports `AssistantPanel` (mirror of
  the `NodePanel.lazyEditors` test pattern), keeping the panel + markdown renderer out of
  the initial bundle within the bundle-size gate.
- **`useUIStore` exclusivity**: `setAssistantOpen(true)` clears git/utility/imports flags and
  vice versa.
