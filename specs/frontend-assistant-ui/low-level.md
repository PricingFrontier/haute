# Frontend Assistant UI — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `frontend/src/panels/assistant/AssistantPanel.tsx` | The panel body (default export, loaded lazily by React): `PanelShell` chrome and one of two screens — the chat list, or one conversation's transcript (auto-scrolled while streaming) with the composer. Receives `isInsideSubmodel` and the central document `readOnly` fence from the app shell, reads the loaded document's source file from `useDocumentStatusStore` (`sourceFile`, with the empty string of an unsaved canvas meaning no source), reads transcript/turn/status/list actions from `useAssistantStore`, and uses `useUIStore.setAssistantOpen` for close. The header icon becomes a back control inside a chat; the composer mounts only there. The list screen shows the readiness card above the chats. The panel hands the one-click question reply to the latest entry only, gated by `assistantSendDisabledReason` like the composer. |
| `frontend/src/panels/assistant/ReadinessCard.tsx` | The readiness surface: the status-error state (a 400's detail with "Fix haute.toml, then check again", or a plain retry for any other failure) and, on the list screen, the not-set-up and cannot-edit cards that render `reason` or `mutations_reason` verbatim with a "Check again" button calling `refreshStatus`. Renders nothing while the status is unknown or ready. |
| `frontend/src/panels/assistant/SessionList.tsx` | The chat-list screen: one row per saved conversation with its title and relative last-used time, plus distinct loading, empty, and retryable-error states so the opening screen never renders as a blank panel. Rows are disabled while the canvas has no source file. |
| `frontend/src/panels/assistant/relativeTime.ts` | Relative-time rendering for list rows, kept out of the component module so the component file exports only components (React Fast Refresh). |
| `frontend/src/panels/assistant/TranscriptEntryView.tsx` | Memoised renderer for one transcript entry by `kind`: user bubble, assistant markdown segment (streamed text), tool-activity row (running/ok/error states), turn marker (failed/stopped/interrupted), or turn outcome: a completed marker for `applied` ("Changes applied") and `answered` ("Turn completed"), the question card for `needs_input` (question as markdown plus the "You choose, and tell me what you picked." reply button when the panel supplies a reply), the blocked card for `blocked` (reason plus "Nothing was saved."), and the saved-but-unverified card for `committed_unverified` (error plus "Your changes were saved, but the check after saving failed. Review the pipeline, or return to the previous save in the Git panel, before continuing."). Owns the markdown rendering (see Control flow); scoped `.assistant-markdown` rules live in `frontend/src/index.css`. |
| `frontend/src/panels/assistant/Composer.tsx` | Message input, send/stop split behaviour, and disabled-state messaging. Receives `isInsideSubmodel`, `currentSourceFile`, and `readOnly` from the panel and uses the store-exported send-gate reason helper, so the rendered gate and imperative `sendMessage` guard share one implementation and one set of messages. |
| `frontend/src/stores/useAssistantStore.ts` | Zustand store owning session id + source binding, transcript entries, turn status, status and `statusErrorDetail`, notice, the `view`/`sessions`/`sessionsStatus` list state, and the `sendMessage`/`stopTurn`/`newChat`/`refreshStatus`/`loadSessions`/`openSession`/`showSessionList` actions. A module-scope `activeController` owns the in-flight abort handle, and the SSE consumption loop runs inside `sendMessage`, so a turn survives panel unmounting. It exports `settleOutcome` (the one function that closes a turn with its outcome, live and on resume) and `CHOOSE_FOR_ME_REPLY`. |
| `frontend/src/api/assistant.ts` | Assistant-owned bundle-split endpoint module: `getAssistantStatus`, abortable `createAssistantSession(sourceFile, sessionId, signal)`, `listAssistantSessions(sourceFile, signal)` (with per-row summary parsing), and `streamAssistantMessage(sessionId, message, sourceFile, options)`; every call carries the canvas document's `source_file`, and the session and list parsers require the echoed `source_file`. It requests JSON as `unknown`, validates status/session/history locally, and fully parses each SSE variant before invoking the store callback. The stream reader uses the authenticated raw-stream helper from [frontend-shared](../frontend-shared/low-level.md), cancels the reader before propagating parser/callback/transport failures, and keeps contract errors distinct from frontend-shared's ApiError. |
| `frontend/src/App.tsx` *(modified)* | [frontend-graph-canvas](../frontend-graph-canvas/low-level.md)-owned shell with a right-panel branch: `assistantOpen` renders the lazy `AssistantPanel` inside `<ErrorBoundary name="AssistantPanel">` + `Suspense`; sits ahead of the `NodePanel` default alongside the git/utility/imports branches. Passes `isInsideSubmodel` and the central document-read-only fence into the panel because both are app-owned state unavailable to the module-scope assistant store; the panel reads the document's source file from `useDocumentStatusStore` itself, so drilling into a submodel never changes the chat's pipeline. |
| `frontend/src/stores/useUIStore.ts` *(modified)* | [frontend-shared](../frontend-shared/low-level.md)-owned UI state with an `assistantOpen` flag + `setAssistantOpen`, mutually exclusive by construction with `gitOpen`/`utilityOpen`/`importsOpen` (each setter clears the others, matching the existing pattern). |
| `frontend/src/components/Toolbar.tsx` *(modified)* | [frontend-shared](../frontend-shared/low-level.md)-owned toolbar with an Assistant toggle button in its Assistant and Help column, calling `setAssistantOpen`. |

## Key types and data structures

- **`AssistantStatus`** (`api/assistant.ts`, mirrored from `schemas.py`):
  `{ configured: boolean; reason: string | null; provider: string | null; model: string |
  null; endpoint_host: string | null; trust: "local" | "organization" | "external" | null;
  max_sensitivity: "public" | "internal" | "restricted" | null;
  mutations_enabled: boolean; mutations_reason: string | null }`.
  `reason` (unconfigured) and `mutations_reason` (working branch not ready — the backend's
  per-state message for no-repository/unset/detached/divergent/invalid) are the backend's human-readable
  explanations; the composer renders whichever applies verbatim. When `configured` is true,
  the status parser requires `endpoint_host`, `trust`, and `max_sensitivity` to be non-null,
  throwing otherwise.
- **`AssistantSessionSummary`** (`api/assistant.ts`): `{ sessionId: string; title: string;
  createdAt: number; lastUsed: number; messageCount: number }`. Backs each row on the
  chat-list screen.
- **`AssistantStreamEvent`** (`api/assistant.ts`, mirrored from the backend SSE contract in
  `schemas.py`) — discriminated union on `type`:
  `text_delta { text }` · `tool_started { id, name, summary }` ·
  `tool_finished { id, name, is_error, summary }` · `graph_updated { fingerprint }` ·
  `completed { usage: { input_tokens, output_tokens }, outcome }` · `failed { message }` ·
  `cancelled {}`. The parser validates the object, discriminator, every required
  primitive, and the nested usage and outcome objects before returning the union member; an
  unknown type or malformed known variant throws. Unrelated additive fields are
  ignored.
- **`AssistantTurnOutcome`** (`api/assistant.ts`, mirrored from `schemas.py`):
  `{ kind: "applied" | "answered"; detail: null }` or
  `{ kind: "needs_input" | "blocked" | "committed_unverified"; detail: string }` with a
  non-empty detail (the question, the blocker, or the verification error). Any other kind,
  a detail on the first pair, or a missing or empty detail on the second throws.
- **`AssistantHistoryEntry` and session envelope** are locally parsed from
  `unknown`: the envelope requires string `session_id` and an array `history`;
  each row requires `kind` in `user|assistant|tool|outcome`; a text or tool row requires
  string `text`, `name`, and `summary`, and Boolean `is_error`, and an `outcome` row
  requires an `outcome` parsed as above. `AssistantStatus` applies the same boundary
  to its Boolean and nullable-string fields.
- **`TranscriptEntry`** (`stores/useAssistantStore.ts`) — union on `kind`:
  `{ kind: "user"; text }` · `{ kind: "assistant"; text; streaming: boolean }` ·
  `{ kind: "activity"; id; name; state: "running" | "ok" | "error"; summary }` ·
  `{ kind: "marker"; outcome: "failed" | "stopped" | "interrupted"; detail?: string }` ·
  `{ kind: "outcome"; outcome: AssistantTurnOutcome }`.
- **`AssistantStoreState`**: `sessionId: string | null`, `pipelineSource: string | null` (the
  source file the server echoed when the current session was created or opened),
  `entries: TranscriptEntry[]`,
  `turnStatus: "idle" | "streaming"`, `status: AssistantStatus | "unknown" | "error"`,
  `statusErrorDetail: string | null` (the detail of a status fetch refused with 400, which
  names what is wrong in `haute.toml`; `null` for any other failure and on success),
  `notice: string | null`, `view: "list" | "chat"`, `sessions: AssistantSessionSummary[]`,
  `sessionsStatus: "unknown" | "loading" | "ready" | "error"`, `sessionsSource: string | null`
  (the source file the panel last asked to list, so a finished turn refreshes the list the
  canvas shows rather than the one the turn started on), and the actions listed in the module map. The in-flight
  `AbortController` is module state, not a Zustand field.

## Control flow

**Open.** Toolbar button → `setAssistantOpen(true)` (clears the other right-panel flags) →
`App.tsx` cascade renders `Suspense(lazy AssistantPanel)` in its error boundary → mount
effects call `refreshStatus()` unconditionally on every open (the previous status stays
rendered as the loading state; never polled) and `loadSessions(sourceFile)` with the loaded
document's source file (`null` for an unsaved canvas).

**Chat list.** The panel opens on `view: "list"`: `GET /api/assistant/sessions?source_file=`
returns that pipeline's saved conversations, most recently used first, each with a title,
relative last-used time, and message count. Selecting one calls `openSession(sessionId,
sourceFile)`, which resolves its transcript through `POST /api/assistant/session` with that
source file and id, records the echoed source file as `pipelineSource`, and switches to
`view: "chat"`. Hydration rebuilds the live display: adjacent assistant rows (one per
provider round) are joined into one segment, as the live deltas were, and each `outcome`
row goes through `settleOutcome`, so a resumed turn shows the same segments, card, and
marker as the live turn did. `newChat()` clears the transcript and switches to the
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
transcript. A valid send also bumps the open ticket before creating or streaming its
session: the message becomes the active chat, so a slower response from the row that was
opening cannot overwrite the new session and transcript. The tickets sit at module scope
alongside `activeController` because the panel can unmount and remount mid-request.
`loadSessions(sourceFile)` records `sourceFile` as `sessionsSource` and lists exactly that
pipeline's conversations; with no source file there is nothing to list. When called for a
different source while idle, it invalidates the old chat's navigation ticket, clears that
active session/transcript, and returns to the list; a conversation from one pipeline is never shown
under another pipeline's canvas.

**Send.** `Composer` submit → `useAssistantStore.getState().sendMessage(text)`:

1. Guards, in order: `turnStatus === "idle"` (whitespace-only input is also a
   no-op); then `assistantSendDisabledReason`: status not `"unknown"`
   and not `"error"`; `status.configured`; `status.mutations_enabled`; the canvas
   has a source file ("Save this pipeline to a file before using Assistant.");
   an open chat's `pipelineSource` equals that source file (otherwise "This chat
   belongs to <pipelineSource>. Open that pipeline to continue it, or start a new
   chat." — the session and transcript are kept, never silently replaced); not inside
   a submodel; canvas not dirty (`useGraphStore.getState().dirty === false`);
   document not read-only (synchronized and not degraded). `sendMessage(text, {isInsideSubmodel, currentSourceFile, readOnly})` receives these
   values as arguments because the view stack and the document fence are app-owned
   state that a module-scope store action cannot read (App → panel → composer prop
   chain); `currentSourceFile` is the panel's `useDocumentStatusStore` source file. A
   failed gate helper check renders as composer messaging and, from `sendMessage`, as
   the panel notice, not a thrown error.
2. Create the turn's `AbortController`, make it the module-scope owner, and set
   `turnStatus = "streaming"` synchronously before the first await. This is the lock
   acquisition: a second same-tick send cannot pass while session creation is pending.
3. Ensure a session: `createAssistantSession(currentSourceFile, null, signal)` on first
   send — always a null prior id, so this call only ever mints a fresh conversation bound
   to the canvas's pipeline. Resuming here would drop a stored transcript
   into a chat the user opened as new; `openSession` owns resumption. The response is
   requested as `unknown` and parsed locally before its `session_id` and echoed
   `source_file` are stored to state.
4. Append the user entry and open a streaming assistant entry.
5. `streamAssistantMessage(sessionId, text, currentSourceFile, {signal, onEvent})` — the
   server refuses a source file other than the session's with a 409 whose detail names
   the chat's pipeline, rendered as the notice. POST via the exported authenticated
   raw-stream helper, then read the response body: chunks are buffered and split on the
   SSE frame delimiter (frames may span chunk boundaries; one chunk may carry several
   frames), each frame's `data:` payload is JSON-decoded and fully parsed into
   an `AssistantStreamEvent` before the callback runs, and each accepted event
   is applied to the store: `text_delta` appends to the last entry when it is a streaming
   assistant segment and otherwise opens a new streaming segment after it, so text that
   follows a tool row renders below that row; `tool_started` settles the streaming segment
   (an empty one is removed) and appends an activity row, and `tool_finished` settles that
   row; `graph_updated` appends an activity row noting the canvas was updated (the canvas
   itself refreshes via `/ws/sync`, not here). A terminal event is retained locally and
   committed only after the response ends; any later event throws instead. `failed` and
   `cancelled` commit their marker. `completed` commits its outcome through
   `settleOutcome`, the same function hydration uses: for `needs_input` and `blocked` it
   removes the model's marker text from the last assistant segment, which ends with the
   marker followed by the outcome's detail (the segment is dropped when nothing else is
   left, and a segment that does not end that way is a contract violation that throws),
   then appends the `outcome` entry, which renders as the question or blocked card. The owning action alone clears
   its controller and returns `turnStatus` to idle in `finally`, then refreshes the chat
   list for `sessionsSource`: when the canvas changed pipeline mid-turn that refresh
   returns the panel to the new pipeline's list, never the old pipeline's.
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
  can't prevent it. Incoming `pipeline_document_update` frames then hit the canvas's
  existing dirty-guard banner (reload/discard) rather than replacing the graph — the
  transcript still records `graph_updated` activity, and resolution happens in the canvas,
  not this panel. Accepted
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
| `refreshStatus` fetch failure | `status = "error"` → the readiness card renders the error state; composer never enabled on unknown readiness. An `ApiError` 400 stores its detail as `statusErrorDetail`, rendered with "Fix haute.toml, then check again." and a "Check again" button; any other failure renders "Assistant status could not be loaded." with Retry. |
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
`frontend/src/panels/assistant/__tests__/TranscriptEntryView.test.tsx`,
`frontend/src/panels/assistant/__tests__/AssistantPanel.test.tsx`,
and `frontend/src/__tests__/App.assistantLazy.test.ts`. Composer DOM interactions are
covered through the store/API boundaries.

- **Store transitions and gates** (`frontend/src/stores/__tests__/useAssistantStore.test.ts`): status success/failure; streaming delta aggregation; tool start/finish settlement; graph-update, completed, failed, cancelled, parser-error, and unterminated-stream terminals; dirty/readiness/submodel/whitespace/streaming gates; the no-source and mismatched-source refusals (a send whose canvas shows another pipeline than the open chat's is refused with a notice naming the chat's pipeline, keeps the session and makes no request) versus same-source session reuse; every request carrying the canvas source file; 400/404/409 notices; abort-stop; and idle-only New chat. Chat-list coverage pins that the panel opens on the list, that a send never resumes a conversation, that opening one hydrates its transcript at that moment, list load success/failure, returning to the list, a pipeline change clearing the prior chat, a turn that finishes after a pipeline change refreshing the new pipeline's list, refusal to navigate mid-turn, that no session stays addressable while its replacement loads, and that a superseded open or list load cannot overwrite the newer one — whether superseded by another open, by New chat, by a send, or by the back control.
- **Transcript order and outcomes in the store** (same file): text, tool and text keep stream order with the send-time placeholder dropped when a tool row comes first; `needs_input` and `blocked` replace the model's marker text with the outcome entry, keeping earlier prose; `committed_unverified` keeps the saved statement beside its outcome; an outcome whose detail does not end the reply interrupts the turn; a resumed history (one assistant row per provider round plus its outcome row) hydrates to exactly the live entries; and a status 400 keeps its detail while any other failure does not.
- **Outcome cards** (`frontend/src/panels/assistant/__tests__/TranscriptEntryView.test.tsx`): `applied` and `answered` render the completed marker; the question card shows the question without its marker and its one-click reply sends, or is disabled with the gate's reason, or is absent when the panel supplies none; the blocked card says nothing was saved; the saved-but-unverified card says the changes were saved and never that nothing changed.
- **Panel** (`frontend/src/panels/assistant/__tests__/AssistantPanel.test.tsx`): transcript rows render text, tool and text in stream order; only the latest question offers the one-click reply, which sends `CHOOSE_FOR_ME_REPLY` with the panel's gate inputs and is disabled on a dirty canvas; the list screen shows the not-set-up and cannot-edit readiness cards with the backend reason and a "Check again" that re-reads the status, none when ready, a 400 status failure with its detail and the `haute.toml` fix, and a plain retry for any other failure.
- **Chat list rendering** (`frontend/src/panels/assistant/__tests__/SessionList.test.tsx`): loading, empty, and retryable-error states are distinguishable rather than blank; rows render their title (with a fallback label for an untitled conversation) and open the one clicked; rows are inert while the canvas has no source file; and relative-time rendering across its boundaries.
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

- **Store transitions**: delta append into the streaming segment, or a new segment after a tool row; activity row
  started→ok/error settlement; each terminal event's marker + `turnStatus` reset; `newChat`
  refused while streaming.
- **SSE parsing**: frames split across chunk boundaries; multiple frames per chunk;
  keep-alive frames ignored; all required fields of every known variant are
  validated; unknown event type throws; stream end without terminal event →
  `interrupted`.
- **Send gates**: dirty canvas blocks with notice; a degraded, source-only, or unsynchronized
  document blocks with a recovery notice; unconfigured blocks and renders the
  backend reason; mutations-disabled blocks and renders `mutations_reason`; drilled into a
  submodel blocks with notice (depth > 1); streaming locks the composer; a canvas without a source file,
  or one showing another pipeline than the open chat's, refuses to send and names why; whitespace no-op.
- **Status refresh**: `refreshStatus()` fires on every panel open, keeping the prior status
  rendered while loading.
- **Stop semantics**: abort marks `stopped`, keeps prior entries, re-enables the composer;
  a 409 on the immediately-following send renders the still-finishing notice without
  auto-retry.
- **Session lifecycle**: 404 on send renders the stale-session notice and New chat clears to
  a working state; the backend list, not browser storage, supplies resumable ids; selecting a
  row passes that id to session create and the returned `history` hydrates the transcript;
  New chat clears the active id and the first send always requests a fresh one.
- **Lazy-loading enforcement**: `App.tsx` never statically imports `AssistantPanel` (mirror of
  the `NodePanel.lazyEditors` test pattern), keeping the panel + markdown renderer out of
  the initial bundle within the bundle-size gate.
- **`useUIStore` exclusivity**: `setAssistantOpen(true)` clears git/utility/imports flags and
  vice versa.
