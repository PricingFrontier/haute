# Frontend shared roadmap

## Scope

The browser's shared hooks, stores, helpers and API client. Current
behaviour is specified in
[the frontend-shared specification](../frontend-shared/high-level.md).
Generating the API contract is planned in the
[server API roadmap](server-api.md) (`API-R03`). `FSH-R03` comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md); `FSH-R05`
to `FSH-R09` from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FSH-R03 | Planned | P3 | The results store holds results; validation and optimiser logic move out. |
| FSH-R05 | Planned | P2 | A save stores the saved document's fingerprint, so a WebSocket reconnect after a save discards no running job and no undo history; a completion callback that throws is reported; a timed-out fetch is aborted; the session bootstrap has a deadline. |
| FSH-R06 | Planned | P3 | The stores carry one copy of each derivation and validator, no test-only action, no fabricated result sentinel, and their specification names the actions that exist. |
| FSH-R07 | Planned | P2 | Ctrl+S saves what is being typed in a code box, node creation is fenced on the graph's structure rather than on object identity, a refused scoped update marks nothing edited, a copied node carries no other node's transient metadata, and a save whose answer was lost is reconciled instead of reported failed. |
| FSH-R08 | Planned | P3 | One helper per concern in the hooks and utilities: unit formatting that carries, one canonical JSON, one date validator, one job-status vocabulary, one locale, one node-creation block, one polling configuration, selector subscriptions. |
| FSH-R09 | Planned | P3 | The frontend-shared specification describes the formatter, the pollers and the store shape that exist, and preview supersession is recognised by a structured code rather than by its message text. |

## Planned improvements

### FSH-R03 — The results store holds results
**Why:** `useNodeResultsStore.ts` combines the store with four recency
caches, module-level derived-preview caches and optimiser logic that
applies the server's frontier-point summaries and converts select
responses into them.

**Plan:** Move the remaining validation to the generated parsers
(`API-R03`), the optimiser logic into an optimiser module and the derived
caches into selectors, leaving the store with state and actions.

**Acceptance:** The store module contains no response validation or domain
derivation; its tests cover state transitions only.

**Dependencies:** `API-R03` (server API).

**Evidence:** `frontend/src/stores/useNodeResultsStore.ts::applyFrontierPointSummary`;
`frontend/src/stores/useNodeResultsStore.ts::hashConfig`.

### FSH-R05 — A save keeps the browser in step
**Why:** After a successful pipeline save nothing refreshes the stored
`documentFingerprint` (`acknowledgeSave` sets only the revision and the save
response carries no fingerprint), so the next WebSocket reconnect resyncs with
the pre-save fingerprint; the server answers a full document for a file that has
not changed, and the client treats it as an external change: the execution
generation is bumped, `useBackgroundJobs` discards every active solve, train and
pivot job and resets the node-data store, and the graph, clean after the save,
is replaced with its undo and redo history wiped and a "Pipeline updated from
file" toast. A save followed by a sleep-wake or any socket drop during a long
training loses the job's tracking and the history. A completion callback that
throws (a frontier-generation contract violation, a storage exception) is
swallowed because the poller is retired before the callback runs, so the job
stays shown as running and every later re-render recreates a poller that
refetches the finished status and throws again. `fetchMlflow` races the
inventory request against a 15 s timer without an abort signal, so the request
runs on after the timeout. The session bootstrap uses a raw `fetch` with no
deadline, so a server that accepts the connection but never answers leaves the
page blank instead of reaching the failure screen.

**Plan:** Return the saved document's fingerprint from the save, scoped-save,
repair-apply and submodel routes and store it in `acknowledgeSave` (the server
half, publishing the saved document to the other clients, is `API-R08`); call
`onComplete` and `onFail` inside a guard that reports the error and leaves no
job record; pass an `AbortController` to the MLflow fetch; give the bootstrap a
deadline and render the failure screen on timeout. Add a test: save, drop and
reopen the socket, assert active jobs and undo history survive.

**Acceptance:** A reconnect after a save produces no document frame and bumps
no generation; a throwing completion callback shows a toast and ends the job
record; a timed-out MLflow fetch is aborted; a silent server reaches the
bootstrap failure screen within the deadline.

**Dependencies:** `API-R08` for the server's fingerprint and broadcast.

**Evidence:** `frontend/src/stores/useDocumentStatusStore.ts::acknowledgeSave`;
`frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/hooks/useWebSocketSync.ts`;
`frontend/src/hooks/useBackgroundJobs.ts`;
`frontend/src/hooks/jobPollingController.ts`;
`frontend/src/stores/useSettingsStore.ts::fetchMlflow`;
`frontend/src/api/client.ts::performSessionBootstrap`.

### FSH-R06 — One copy of each derivation
**Why:** The ISO date and time validators are implemented twice, line for line,
in the response guards and the pivot editor's config. Eleven graph-store actions
each re-implement the fingerprint → dirty → structural version → panel-context
block that `commitGraphState` encapsulates, and the two families already
disagree on when an undo snapshot is pushed. `failTrainJob` fabricates a
complete forty-four-field `TrainResponse` sentinel to record a failure, which
every reader screens by status, where the solve cache models the same case as
`result: null`. `setSourceRevision` has no production caller and four test
files drive a generation bump through it that production never performs.
`runDispersionEstimate` manufactures an `ApiError(..., 500)` for a job that
ended in a non-completed status although no HTTP failure occurred, and the two
specification passages disagree about it. The specification names an `update`
recover action that neither the browser nor the server has, and one test file is
named for a store it does not test.

**Plan:** Export the validators from one module; route the history-aware setters
and undo/redo through `commitGraphState`; give the cached train result a
`result | null` plus `error` shape; delete `setSourceRevision` and drive its
tests through `loadLiveDocumentStatus`; throw a job-failure error from the
dispersion helper and align the two passages; correct the recover actions and
the test file's name.

**Acceptance:** jscpd reports none of the pairs; `useGraphStore` has one
change-commit path; no fabricated response sentinel; the specification names
remove, reset, recover and remove-connection only.

**Dependencies:** `FSH-R03` moves the optimiser logic out of the same store.

**Evidence:** `frontend/src/types/guards.ts::isValidPivotDate`;
`frontend/src/panels/explore/pivotConfig.ts`;
`frontend/src/stores/useGraphStore.ts::commitGraphState`;
`frontend/src/stores/useNodeResultsStore.ts::failTrainJob`;
`frontend/src/stores/useDocumentStatusStore.ts::setSourceRevision`;
`frontend/src/api/dispersion.ts::runDispersionEstimate`;
`frontend/src/types/pipelineRepair.ts`;
`frontend/src/stores/__tests__/useUIStore.dirty.derived.test.ts`.

### FSH-R07 — Saves and creations that include what the analyst did
**Why:** Ctrl+S pressed while the cursor is in a code box saves the pipeline
without the keystrokes of the last 150 ms, reports "Saved" and leaves the canvas
dirty with no indicator: `saveCommittingField` blurs only `<input>` and
`<textarea>` targets while CodeMirror's focused element is a contenteditable
`div`, so the save runs at once; the editor's debounced commit is flushed only
on blur, unmount or an external value, so the snapshot predates the edit and the
debounce re-dirties the graph after the toast; `isTypingTarget` already knows a
`.cm-editor` is a typing target, and a test pins the gap. The five node-creation
paths (duplicate, create instance, palette and connection drop, Edge Join
insertion, paste) decide "the graph changed while identity resolution was
running" by object identity of `graphRef.current`, which the app replaces on
every selection, drag, measurement or preview stash, so an unrelated write
during the round trip refuses the creation with an error toast; the structural
fingerprint built for exactly this is used by the two other identity flows, and a
test pins the coarse behaviour. `useScopedNodeSave` marks a node edited before
the update is accepted, so one refused update locks every other node behind
"Save X or reload" until a document reload. Duplicate and paste copy every
transient field of the source node (`_recoveryId`, `_columns`, ...) onto the
copy, so a copy carries another node's recovery identity and column stash
(latent while the copy is only reachable in a mutable document). A save whose
HTTP response is lost is reported failed although the server wrote the file;
the client keeps the old base revision, the next save is refused as stale and
the banner tells the analyst to reload, discarding the edits in between.

**Plan:** In `saveCommittingField`, treat a typing target inside `.cm-editor`
like a text field (blur or flush, then save after the commit has rendered) and
test a Ctrl+S inside the debounce window; fence the five creation sites on
`structuralFingerprint` plus the ids the creation depends on, in one shared
helper, and turn the pinning test around; add the id to the edited set only on
an accepted update; build a copy's data from the authored fields only; on a
non-HTTP save failure re-read the document and acknowledge the save when its
revision or content matches what was sent.

**Acceptance:** Ctrl+S during typing saves the typed text and leaves the canvas
clean; a selection change during identity resolution does not refuse a
creation while a structural change still does; a refused scoped update leaves
the edited set unchanged; a pasted node carries none of the original's
underscore fields; a save with a dropped response is acknowledged on the next
read.

**Dependencies:** `API-R08` for the server's saved-document fingerprint.

**Evidence:** `frontend/src/hooks/keyboardTargets.ts::saveCommittingField`;
`frontend/src/hooks/keyboardTargets.ts::isTypingTarget`;
`frontend/src/panels/editors/CodeMirrorEditor.tsx`;
`frontend/src/hooks/useNodeHandlers.ts`; `frontend/src/hooks/useEdgeHandlers.ts`;
`frontend/src/hooks/useKeyboardShortcuts.ts`;
`frontend/src/utils/structuralFingerprint.ts::structuralFingerprint`;
`frontend/src/hooks/useScopedNodeSave.ts::useScopedNodeSave`;
`frontend/src/hooks/usePipelineAPI.ts`.

### FSH-R08 — One helper per concern
**Why:** `formatNumber` and `formatBytes` round a value up to its unit boundary
without carrying into the next unit ("1000.0K" for 999,999; "1024.0 KB" for
1,048,575) while the sibling `formatByteSize` carries. Sorted-key canonical JSON
is implemented five times in five shapes, so two values can canonicalise
differently depending on the helper a surface reached. Two YYYY-MM-DD
validators exist, and the global-constants one goes through `Date.UTC`, which
the workbench one's own comment says reads years before 100 as the 1900s. The
job-status vocabulary is hand-copied into `executionDiagnostics.ts` although the
specification names `JOB_STATUS_VALUES` as the single vocabulary. Preview cells
follow the browser locale while the trace panel and chart axes pin English, so
one number renders with different separators across surfaces in a non-English
browser. The "resolve identities, check the serial and the graph, apply or
toast" block is written five times; the modelling Summary tab re-implements
`formatFixed`; the five `useJobPolling` configurations repeat the same terminal
rules; two hooks subscribe whole stores against the selector contract; the
context-menu delete defers `clearNode` by a timer while the keyboard delete
clears synchronously, and a test pins the timer.

**Plan:** Carry at the unit boundary in both formatters (or build `formatBytes`
on `formatByteSize`); keep `utils/canonicalJson.ts` as the one helper; replace
`isRealDate` with `isDay`; import `JOB_STATUS_VALUES`; pin one locale constant
or decide every surface follows the browser; one `createAfterIdentity` helper;
`formatFixed` in the Summary tab; a `jobPollingDefaults` spread; selector
subscriptions; one deletion order.

**Acceptance:** `formatNumber(999999)` is "1.0M" and `formatBytes(1048575)` is
"1.0 MB"; one canonical-JSON implementation with the `undefined`, `bigint` and
nested-array cases tested once; jscpd reports none of the pairs; the lint that
rejects local copies of a helper covers the cases above.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/formatValue.ts::formatNumber`;
`frontend/src/utils/formatBytes.ts::formatBytes`;
`frontend/src/utils/formatBytes.ts::formatByteSize`;
`frontend/src/utils/canonicalJson.ts::canonicalJson`;
`frontend/src/utils/graphSnapshot.ts`; `frontend/src/hooks/useTracing.ts`;
`frontend/src/utils/trainedJobHandles.ts`; `frontend/src/utils/globalConstants.ts`;
`frontend/src/utils/sheetValues.ts::isDay`;
`frontend/src/utils/executionDiagnostics.ts`; `frontend/src/utils/formatTrace.ts`;
`frontend/src/utils/chartHelpers.ts`; `frontend/src/hooks/useBackgroundJobs.ts`;
`frontend/src/panels/modelling/SummaryTab.tsx`.

### FSH-R09 — The specification and the supersession signal
**Why:** The specification says the trace formatter quotes ordinary strings and
retains date-shaped ones; the code returns every string unchanged and the regex
is dead. It says `useBackgroundJobs` mounts four pollers (solve, train, explore,
pivot) and the results store holds `exploreResults` and `exploreJobs`; the hook
mounts five (solve, train, pivot, node-data build, data profile), polls no
Explore job, and the store has no such maps. Preview supersession is recognised
by searching the 409's text for "superseded", so a reworded server message would
surface every superseded preview as a preview error; the server's 409 carries no
`error_code`.

**Plan:** Correct the three specification passages and delete the dead regex;
give the supersession 409 a structured `error_code` (one line in the preview
route) and match on it.

**Acceptance:** The specification matches the formatter, the pollers and the
store; the supersession test passes with the server message reworded.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/formatTrace.ts`;
`frontend/src/hooks/useBackgroundJobs.ts`;
`frontend/src/stores/useNodeResultsStore.ts`;
`frontend/src/hooks/usePipelineAPI.ts::isPreviewSupersededError`;
`src/haute/routes/pipeline.py`; `specs/frontend-shared/low-level.md`.

