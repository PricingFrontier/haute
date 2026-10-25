# Frontend shared roadmap

## Scope

The browser's shared hooks, stores, helpers and API client. Current
behaviour is specified in
[the frontend-shared specification](../frontend-shared/high-level.md).
Generating the API contract is planned in the
[server API roadmap](server-api.md) (`API-R03`). `FSH-R03` comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md); `FSH-R05`
and `FSH-R06` from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FSH-R03 | Planned | P3 | The results store holds results; validation and optimiser logic move out. |
| FSH-R05 | Planned | P2 | A save stores the saved document's fingerprint, so a WebSocket reconnect after a save discards no running job and no undo history; a completion callback that throws is reported; a timed-out fetch is aborted; the session bootstrap has a deadline. |
| FSH-R06 | Planned | P3 | The stores carry one copy of each derivation and validator, no test-only action, no fabricated result sentinel, and their specification names the actions that exist. |

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

