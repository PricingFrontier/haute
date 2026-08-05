# Frontend Assistant UI — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `frontend/src/panels/assistant/AssistantPanel.tsx` | The panel body (default export, loaded lazily by React): `PanelShell` chrome and one of two screens — the chat list, or one conversation's transcript (auto-scrolled while streaming) with the composer. Receives `isInsideSubmodel` and `currentSourceFile` from the app shell, reads transcript/turn/status/list actions from `useAssistantStore`, and uses `useUIStore.setAssistantOpen` for close. The header icon becomes a back control inside a chat; the composer mounts only there. |
| `frontend/src/panels/assistant/SessionList.tsx` | The chat-list screen: one row per saved conversation with its title and relative last-used time, plus distinct loading, empty, and retryable-error states so the opening screen never renders as a blank panel. Rows are disabled while no pipeline is resolved. |
| `frontend/src/panels/assistant/relativeTime.ts` | Relative-time rendering for list rows, kept out of the component module so the component file exports only components (React Fast Refresh). |
| `frontend/src/panels/assistant/TranscriptEntryView.tsx` | Memoised renderer for one transcript entry by `kind`: user bubble, assistant markdown segment (streamed text), tool-activity row (running/ok/error states), or turn marker (completed/failed/stopped/interrupted). Owns the markdown rendering (see Control flow); scoped `.assistant-markdown` rules live in `frontend/src/index.css`. |
| `frontend/src/panels/assistant/Composer.tsx` | Message input, send/stop split behaviour, and disabled-state messaging. Receives `isInsideSubmodel` and `currentSourceFile` from the panel and uses the store-exported send-gate reason helper, so the rendered gate and imperative `sendMessage` guard share one implementation and one set of messages. |
| `frontend/src/stores/useAssistantStore.ts` | Zustand store owning session id + source binding, transcript entries, turn status, notice, the `view`/`sessions`/`sessionsStatus` list state, and the `sendMessage`/`stopTurn`/`newChat`/`refreshStatus`/`loadSessions`/`openSession`/`showSessionList` actions. A module-scope `activeController` owns the in-flight abort handle, and the SSE consumption loop runs inside `sendMessage`, so a turn survives panel unmounting. |
| `frontend/src/api/assistant.ts` | Assistant-owned bundle-split endpoint module: `getAssistantStatus`, abortable `createAssistantSession`, and `streamAssistantMessage`. It requests JSON as `unknown`, validates status/session/history locally, and fully parses each SSE variant before invoking the store callback. The stream reader uses the authenticated raw-stream helper from [frontend-shared](../frontend-shared/low-level.md), cancels the reader before propagating parser/callback/transport failures, and keeps contract errors distinct from frontend-shared's ApiError. |
| `frontend/src/App.tsx` *(modified)* | [frontend-graph-canvas](../frontend-graph-canvas/low-level.md)-owned shell with a right-panel branch: `assistantOpen` renders the lazy `AssistantPanel` inside `<ErrorBoundary name="AssistantPanel">` + `Suspense`; sits ahead of the `NodePanel` default alongside the git/utility/imports branches. Passes `isInsideSubmodel` (derived from its submodel-navigation view stack, which is hook-local state a module-scope store cannot read) into the panel as a prop. |
| `frontend/src/stores/useUIStore.ts` *(modified)* | [frontend-shared](../frontend-shared/low-level.md)-owned UI state with an `assistantOpen` flag + `setAssistantOpen`, mutually exclusive by construction with `gitOpen`/`utilityOpen`/`importsOpen` (each setter clears the others, matching the existing pattern). |
| `frontend/src/components/Toolbar.tsx` *(modified)* | [frontend-shared](../frontend-shared/low-level.md)-owned toolbar with an Assistant toggle button next to the existing utility/imports buttons, calling `setAssistantOpen`. |

## Key types and data structures

- **`AssistantStatus`** (`api/assistant.ts`, mirrored from `schemas.py`):
  `{ configured: boolean; reason: string | null; provider: string | null; model: string |
  null; endpoint_host: string | null; trust: "local" | "organization" | "external" | null;
  max_sensitivity: "public" | "internal" | "restricted" | null;
  mutations_enabled: boolean; mutations_reason: string | null }`.
  `reason` (unconfigured) and `mutations_reason` (working branch not ready — the backend's
  per-state message for no-repository/unset/detached/divergent/invalid) are the backend's human-readable
  explanations; the composer renders whichever applies verbatim.
- **`AssistantStreamEvent`** (`api/assistant.ts`, mirrored from the backend SSE contract in
  `schemas.py`) — discriminated union on `type`:
  `text_delta { text }` · `tool_started { id, name, summary }` ·
  `tool_finished { id, name, is_error, summary }` · `graph_updated { fingerprint }` ·
  `completed { usage: { input_tokens, output_tokens } }` · `failed { message }` ·
  `cancelled {}`. The parser validates the object, discriminator, every required
  primitive, and the nested usage object before returning the union member; an
  unknown type or malformed known variant throws. Unrelated additive fields are
  ignored.
- **`AssistantHistoryEntry` and session envelope** are locally parsed from
  `unknown`: the envelope requires string `session_id` and an array `history`;
  each row requires `kind` in `user|assistant|tool`, string `text`, `name`, and
  `summary`, and Boolean `is_error`. `AssistantStatus` applies the same boundary
  to its Boolean and nullable-string fields.
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
`App.tsx` cascade renders `Suspense(lazy AssistantPanel)` in its error boundary → mount
effects call `refreshStatus()` unconditionally on every open (the previous status stays
rendered as the loading state; never polled) and `loadSessions(currentSourceFile)`.

**Chat list.** The panel opens on `view: "list"`: `GET /api/assistant/sessions` returns this
pipeline's saved conversations, most recently used first, each with a title, relative
last-used time, and message count. Selecting one calls `openSession(sessionId,
sourceFile)`, which resolves its transcript through `POST /api/assistant/session` with that
id and switches to `view: "chat"`. `newChat()` clears the transcript and switches to the
chat screen **without** creating a backend session — `create` persists immediately, so an
abandoned new chat would appear in the list as an empty untitled row; the first send mints
it. The back control returns to the list and refreshes it, and a completed turn refreshes
it too, so a conversation appears under the title its opening message gave it. Both
navigation actions are refused mid-turn.

The transcript is resolved when a conversation is **opened**, never lazily on send, and no
session id is remembered client-side. A `localStorage` id that silently resumed inside the
next send is what made the panel open blank and then surface an earlier conversation above
the message just sent; the backend list is now the single record of which chats exist.

`openSession` clears `sessionId`/`pipelineSource` **before** awaiting, not after: the chat
screen mounts the composer immediately, so a message sent while the transcript loads would
otherwise be posted into the conversation just navigated away from, under an empty screen.
Both navigation fetches carry a module-scope monotonic ticket (`openGeneration`,
`listGeneration`) and only the newest may write state. Neither response identifies the
request it answers, and both are re-issued faster than they resolve — a second row click, a
pipeline change, a turn finishing — so a slower earlier response would otherwise land last
and show a chat nobody chose. `newChat` and the back control bump the open ticket too:
any navigation supersedes an open still in flight, whose response would otherwise arrive
afterwards and re-attach the conversation just left — filling a "new chat" with an old
transcript. The tickets sit at module scope alongside `activeController` because the panel
can unmount and remount mid-request. `loadSessions(sourceFile)` treats
its argument as the gate, not the query: like `createAssistantSession`, the request itself
deliberately sends `pipeline: null` so the server resolves the pipeline the same way
session creation does, and with none resolved there is nothing to list.

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
3. Ensure a session: `createAssistantSession(null, null, signal)` on first send — the
   current client deliberately sends `pipeline: null`, and always a null prior id, so this
   call only ever mints a fresh conversation. Resuming here would drop a stored transcript
   into a chat the user opened as new; `openSession` owns resumption. The response is
   requested as `unknown` and parsed locally before its `session_id` is stored to state.
4. Append the user entry and open a streaming assistant entry.
5. `streamAssistantMessage(sessionId, text, signal)` — POST via the exported authenticated
   raw-stream helper, then read the response body: chunks are buffered and split on the
   SSE frame delimiter (frames may span chunk boundaries; one chunk may carry several
   frames), each frame's `data:` payload is JSON-decoded and fully parsed into
   an `AssistantStreamEvent` before the callback runs, and each accepted event
   is applied to the store: `text_delta` appends to the open assistant entry;
   `tool_started`/`tool_finished` append/settle an activity row; `graph_updated` appends an
   activity row noting the canvas was updated (the canvas itself refreshes via `/ws/sync`,
   not here). A terminal event is retained locally and
   committed as the single marker only
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
- **Malformed known events and unrecognised event types throw** before the store
  callback → turn marked `interrupted` + error toast. Contract drift is loud and
  no rejected field can partially mutate the transcript. The API reader is
  cancelled before the error propagates, so the server sees a disconnect.
- **Event after a terminal event throws** from the store callback, cancels the reader, and
  replaces the provisional terminal outcome with one `interrupted` marker plus an error
  toast. It is never discarded.
- **Plan authority is server-only**: the browser never sends operations,
  revisions, plan hashes, or consent metadata. Staleness, expiry and prior use
  are enforced when the model invokes the exact stored plan.
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
| Session-create `ApiError` 400 (unconfigured) | No transcript entries have been appended yet, so the transcript stays unchanged; inline notice with the backend detail; `refreshStatus()` re-run so the composer gate shows the current reason. |
| Message-send `ApiError` 400 (unconfigured) | Empty speculative assistant bubble removed; user entry followed by one `failed` marker; inline notice with the backend detail; `refreshStatus()` re-run so the composer gate shows the current reason. |
| Send-time `ApiError` 404 (stale session) | Empty speculative assistant bubble removed; user entry followed by one `failed` marker; inline "session expired (server restarted)" notice offering New chat; no silent re-create. |
| Send-time concurrent-turn `ApiError` 409 | Empty speculative assistant bubble removed; user entry followed by one `failed` marker; the still-finishing inline notice; composer stays enabled; no auto-retry. |
| Other send-time `ApiError` 409 | Empty speculative assistant bubble removed; user entry followed by one `failed` marker; the backend detail (or a generic conflict notice); composer stays enabled; no auto-retry. |
| Terminal `failed` event | Marker `failed` with the backend-provided message inline + error toast (`useToastStore`). |
| Status/session parser throw | Descriptive ordinary `Error`; no typed value or partial history is returned, and the existing status/session failure path handles it. |
| SSE parser throw / callback throw / transport drop mid-stream | Response reader cancelled, marker `interrupted` + error toast; composer re-enabled. Contract failures are ordinary `Error` values, not `ApiError`. |
| Caller-initiated `AbortError` (stop) | Marker `stopped`; not an error, no toast. |
| Render crash anywhere in the panel | Contained by `<ErrorBoundary name="AssistantPanel">`; canvas/toolbar/inspector unaffected. |

## Testing

Implemented Vitest coverage is split between
`frontend/src/stores/__tests__/useAssistantStore.test.ts`,
`frontend/src/api/__tests__/assistant.test.ts`,
`frontend/src/panels/assistant/__tests__/SessionList.test.tsx`,
and `frontend/src/__tests__/App.assistantLazy.test.ts`. Transcript/composer DOM
interactions are covered through the store/API boundaries.

- **Store transitions and gates** (`frontend/src/stores/__tests__/useAssistantStore.test.ts`): status success/failure; streaming delta aggregation; tool start/finish settlement; graph-update, completed, failed, cancelled, parser-error, and unterminated-stream terminals; dirty/readiness/submodel/whitespace/streaming gates; source-change reset versus same-source session reuse; 400/404/409 notices; abort-stop; and idle-only New chat. Chat-list coverage pins that the panel opens on the list, that a send never resumes a conversation, that opening one hydrates its transcript at that moment, list load success/failure, returning to the list, refusal to navigate mid-turn, that no session stays addressable while its replacement loads, and that a superseded open or list load cannot overwrite the newer one — whether superseded by another open, by New chat, or by the back control.
- **Chat list rendering** (`frontend/src/panels/assistant/__tests__/SessionList.test.tsx`): loading, empty, and retryable-error states are distinguishable rather than blank; rows render their title (with a fallback label for an untitled conversation) and open the one clicked; rows are inert with no pipeline resolved; and relative-time rendering across its boundaries.
- **Assistant API boundary** (`frontend/src/api/__tests__/assistant.test.ts`):
  endpoint payloads and abort signal; valid and malformed status/session/history
  shapes; chunk-split and multi-frame ordering; keep-alive handling; missing or
  wrong fields for every known SSE variant; unknown-discriminator failure;
  callback-not-invoked proof for rejected frames; non-OK `ApiError` mapping;
  reader cancellation after parser/callback failure; and a stream ending without
  a terminal event for the store to classify. The suite also covers the closed
  message request and every remaining SSE variant.
- **Bundle boundary** (`frontend/src/__tests__/App.assistantLazy.test.ts`): `App.tsx` uses only `React.lazy(import())` for the panel and neither it nor non-assistant production modules import the markdown renderer.

The following matrix records the full regression contract; where the scenario is already unit-covered above, it remains a useful component/integration target:

- **Store transitions**: delta append into the open assistant entry; activity row
  started→ok/error settlement; each terminal event's marker + `turnStatus` reset; `newChat`
  refused while streaming.
- **SSE parsing**: frames split across chunk boundaries; multiple frames per chunk;
  keep-alive frames ignored; all required fields of every known variant are
  validated; unknown event type throws; stream end without terminal event →
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
