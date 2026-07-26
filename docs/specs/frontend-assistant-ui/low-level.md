# Frontend Assistant UI — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `frontend/src/panels/assistant/AssistantPanel.tsx` | The panel body (default export, loaded via `React.lazy`): `PanelShell` chrome, the transcript list (auto-scrolled while streaming), and the composer mount. Receives `isInsideSubmodel` and `currentSourceFile` from the app shell, reads transcript/turn/status actions from `useAssistantStore`, and uses `useUIStore.setAssistantOpen` for close. |
| `frontend/src/panels/assistant/TranscriptEntryView.tsx` | Memoised renderer for one transcript entry by `kind`: user bubble, assistant markdown segment (streamed text), tool-activity row (running/ok/error states), or turn marker (completed/failed/stopped/interrupted). Owns the markdown rendering (see Control flow); scoped `.assistant-markdown` rules live in `frontend/src/index.css`. |
| `frontend/src/panels/assistant/Composer.tsx` | Message input, send/stop split behaviour, and disabled-state messaging. Receives `isInsideSubmodel` and `currentSourceFile` from the panel and uses the store-exported send-gate reason helper, so the rendered gate and imperative `sendMessage` guard share one implementation and one set of messages. |
| `frontend/src/stores/useAssistantStore.ts` | Zustand store owning session id + source binding, transcript entries, turn status, notice, and the `sendMessage`/`stopTurn`/`newChat`/`refreshStatus` actions. A module-scope `activeController` owns the in-flight abort handle, and the SSE consumption loop runs inside `sendMessage`, so a turn survives panel unmounting. |
| `frontend/src/api/assistant.ts` | Assistant-owned bundle-split endpoint module: `getAssistantStatus`, abortable `createAssistantSession`, and `streamAssistantMessage` — a `fetch` + `ReadableStream` SSE reader built on the authenticated raw-stream helper `frontend/src/api/client.ts` exports for split modules (auth headers + `ApiError` mapping without the JSON parse), with local event parsing that throws on an unrecognised event type and cancels the reader before propagating any parser/callback/transport failure. |
| `frontend/src/App.tsx` *(modified)* | New branch in the right-panel if/else cascade: `assistantOpen` renders the lazy `AssistantPanel` inside `<ErrorBoundary name="AssistantPanel">` + `Suspense`; sits ahead of the `NodePanel` default alongside the git/utility/imports branches. Passes `isInsideSubmodel` (derived from its submodel-navigation view stack, which is hook-local state a module-scope store cannot read) into the panel as a prop. |
| `frontend/src/stores/useUIStore.ts` *(modified)* | New `assistantOpen` flag + `setAssistantOpen`, mutually exclusive by construction with `gitOpen`/`utilityOpen`/`importsOpen` (each setter clears the others, matching the existing pattern). |
| `frontend/src/components/Toolbar.tsx` *(modified)* | Assistant toggle button next to the existing utility/imports buttons, calling `setAssistantOpen`. |

## Key types and data structures

- **`AssistantStatus`** (`api/assistant.ts`, mirrored from `schemas.py`):
  `{ configured: boolean; reason: string | null; provider: string | null; model: string |
  null; mutations_enabled: boolean; mutations_reason: string | null }`.
  `reason` (unconfigured) and `mutations_reason` (working branch not ready — the backend's
  per-state message for no-repository/unset/detached/divergent/invalid) are the backend's human-readable
  explanations; the composer renders whichever applies verbatim.
- **`AssistantStreamEvent`** (`api/assistant.ts`, mirrored from the backend SSE contract in
  `schemas.py`) — discriminated union on `type`:
  `text_delta { text }` · `tool_started { id, name, summary }` ·
  `tool_finished { id, name, is_error, summary }` · `graph_updated { fingerprint }` ·
  `completed { usage: { input_tokens, output_tokens } }` · `failed { message }` ·
  `cancelled {}`. The parser throws on any other `type` — contract drift surfaces, never
  skips. It validates the discriminator only; event-specific fields are trusted through the
  TypeScript cast rather than runtime-validated.
- **`TranscriptEntry`** (`stores/useAssistantStore.ts`) — union on `kind`:
  `{ kind: "user"; text }` · `{ kind: "assistant"; text; streaming: boolean }` ·
  `{ kind: "activity"; id; name; state: "running" | "ok" | "error"; summary }` ·
  `{ kind: "marker"; outcome: "completed" | "failed" | "stopped" | "interrupted"; detail?: string }`.
- **`AssistantStoreState`**: `sessionId: string | null`, `pipelineSource: string | null` (the
  source-file key associated with the current in-memory session), `entries: TranscriptEntry[]`,
  `turnStatus: "idle" | "streaming"`, `status: AssistantStatus | "unknown" | "error"`,
  `notice: string | null`, and the actions listed in the module map. The in-flight
  `AbortController` is module state, not a Zustand field.

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
2. Create the turn's `AbortController`, make it the module-scope owner, and set
   `turnStatus = "streaming"` synchronously before the first await. This is the lock
   acquisition: a second same-tick send cannot pass while session creation is pending.
3. Ensure a session: `createAssistantSession(null, rememberedSessionId, signal)` on first
   send — the current client deliberately sends `pipeline: null`; the remembered id comes from `localStorage`
   (`haute.assistant.session:<sourceFile>`); the response's `session_id` is stored to both
   state and `localStorage`, and a non-empty `history` (the backend's transcript mapping
   of a resumed session) hydrates `entries` before the new turn's entries append.
4. Append the user entry and open a streaming assistant entry.
5. `streamAssistantMessage(sessionId, text, signal)` — POST via the exported authenticated
   raw-stream helper, then read the response body: chunks are buffered and split on the
   SSE frame delimiter (frames may span chunk boundaries; one chunk may carry several
   frames), each frame's `data:` payload is `JSON.parse`d into a `AssistantStreamEvent`, and
   each event is applied to the store: `text_delta` appends to the open assistant entry;
   `tool_started`/`tool_finished` append/settle an activity row; `graph_updated` appends an
   activity row noting the canvas was updated (the canvas itself refreshes via `/ws/sync`,
   not here). A terminal event is retained locally and committed as the single marker only
   after the response ends; any later event throws instead. The owning action alone clears
   its controller and returns `turnStatus` to idle in `finally`.
6. The loop runs in the store action, not a component effect — closing the panel (or another
   panel's setter forcing `assistantOpen` false) does not stop a running turn; reopening shows
   the live state.

**Stop.** `stopTurn()` aborts the controller; the reader's `AbortError` is caught as the
expected stop path → marker `stopped`, `turnStatus = "idle"`. The backend notices the
disconnect and halts between tool executions; edits already applied stay, although the generic
marker text does not describe that consequence. One deliberate consequence: the backend keeps the session lock until an in-flight
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
bundle/perf). Scoped typography styles distinguish paragraphs, headings, lists,
blockquotes, links, rules, and GFM tables. `TranscriptEntryView` is memoised: a token update
re-renders and re-parses only the open assistant entry whose object changed, not every
settled transcript row.

## Edge cases and invariants

- **SSE framing**: partial frames across reads are buffered; multiple frames in one read are
  all applied in order. An empty keep-alive frame is ignored without error.
- **Stream ends without a terminal event** (network drop, server crash): marker
  `interrupted` — never rendered as a completed turn.
- **Unrecognised event type throws** in the parser → turn marked `interrupted` + error
  toast. Contract drift is loud. The API reader is cancelled before the error propagates,
  so the server sees a disconnect.
- **Event after a terminal event throws** from the store callback, cancels the reader, and
  replaces the provisional terminal outcome with one `interrupted` marker plus an error
  toast. It is never discarded.
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
- **`turnStatus` is the single lock**: send, new-chat, and session reset all check it; it is
  acquired before session creation and released only by the owning controller after the
  response ends. Controller identity guards both cleanup fields, so stale cleanup can
  never unlock a newer turn.
- **Store survives panel unmount by design** — the invariant is that `turnStatus`
  transitions only when the owning `sendMessage` action acquires or releases the turn,
  never from a component lifecycle or a stale action's cleanup.

## Error handling

| Failure | Surfaced as |
|---|---|
| `refreshStatus` fetch failure | `status = "error"` → panel body renders an error state with a retry button; composer never enabled on unknown readiness. |
| Send-time `ApiError` 400 (unconfigured) | Empty speculative assistant bubble removed; user entry followed by one `failed` marker; inline notice with the backend detail; `refreshStatus()` re-run so the composer gate shows the current reason. |
| Send-time `ApiError` 404 (stale session) | Empty speculative assistant bubble removed; user entry followed by one `failed` marker; inline "session expired (server restarted)" notice offering New chat; no silent re-create. |
| Send-time `ApiError` 409 | Empty speculative assistant bubble removed; user entry followed by one `failed` marker; the still-finishing inline notice; composer stays enabled; no auto-retry. The client does not distinguish a post-stop 409 from another 409. |
| Terminal `failed` event | Marker `failed` with the backend-provided message inline + error toast (`useToastStore`). |
| Parser throw / callback throw / transport drop mid-stream | Response reader cancelled, marker `interrupted` + error toast; composer re-enabled. |
| Caller-initiated `AbortError` (stop) | Marker `stopped`; not an error, no toast. |
| Render crash anywhere in the panel | Contained by `<ErrorBoundary name="AssistantPanel">`; canvas/toolbar/inspector unaffected. |

## Testing

Implemented Vitest coverage is split between `frontend/src/stores/__tests__/useAssistantStore.test.ts`, `frontend/src/api/__tests__/assistant.test.ts`, and `frontend/src/__tests__/App.assistantLazy.test.ts`. Component-level transcript/composer DOM interactions are not currently covered directly.

- **Store transitions and gates** (`stores/__tests__/useAssistantStore.test.ts`): status success/failure; streaming delta aggregation; tool start/finish settlement; graph-update, completed, failed, cancelled, parser-error, and unterminated-stream terminals; dirty/readiness/submodel/whitespace/streaming gates; source-change reset versus same-source session reuse; session persistence/hydration; 400/404/409 notices; abort-stop; and idle-only New chat.
- **SSE API reader** (`api/__tests__/assistant.test.ts`): endpoint payloads and abort signal, chunk-split and multi-frame ordering, keep-alive handling, unknown-event failure, non-OK `ApiError` mapping, and a stream ending without a terminal event for the store to classify.
- **Bundle boundary** (`__tests__/App.assistantLazy.test.ts`): `App.tsx` uses only `React.lazy(import())` for the panel and neither it nor non-assistant production modules import the markdown renderer.

The following matrix records the full regression contract; where the scenario is already unit-covered above, it remains a useful component/integration target:

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
