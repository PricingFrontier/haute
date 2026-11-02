# Frontend shared roadmap

## Scope

The browser's shared hooks, stores, helpers and API client. Current
behaviour is specified in
[the frontend-shared specification](../frontend-shared/high-level.md).
Generating the API contract is planned in the
[server API roadmap](server-api.md) (`API-R03`). `FSH-R03` comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md); `FSH-R06`
to `FSH-R09` from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FSH-R03 | Planned | P3 | The results store holds results; validation and optimiser logic move out. |
| FSH-R06 | Planned | P3 | The stores carry one copy of each derivation and validator, no test-only action, no fabricated result sentinel, and their specification names the actions that exist. |
| FSH-R09 | Planned | P3 | The frontend-shared specification describes the formatter, the pollers and the store shape that exist, and preview supersession is recognised by a structured code rather than by its message text. |
| FSH-R10 | Planned | P2 | Independent request-input edits made while identity resolution is pending are retained together. |

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

### FSH-R06 — One copy of each derivation
**Why:** Eleven graph-store actions
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

**Plan:** Route the history-aware setters
and undo/redo through `commitGraphState`; give the cached train result a
`result | null` plus `error` shape; delete `setSourceRevision` and drive its
tests through `loadLiveDocumentStatus`; throw a job-failure error from the
dispersion helper and align the two passages; correct the recover actions and
the test file's name.

**Acceptance:** `useGraphStore` has one
change-commit path; no fabricated response sentinel; the specification names
remove, reset, recover and remove-connection only.

**Dependencies:** `FSH-R03` moves the optimiser logic out of the same store.

**Evidence:** `frontend/src/stores/useGraphStore.ts::commitGraphState`;
`frontend/src/stores/useNodeResultsStore.ts::failTrainJob`;
`frontend/src/stores/useDocumentStatusStore.ts::setSourceRevision`;
`frontend/src/api/dispersion.ts::runDispersionEstimate`;
`frontend/src/types/pipelineRepair.ts`;
`frontend/src/stores/__tests__/useUIStore.dirty.derived.test.ts`.

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

### FSH-R10 — Retain edits during input identity resolution
**Why:** The [28 October review](codebase-review-2026-10-28.md) reproduced two
API Input column deselections yielding `[true, false]` instead of
`[false, false]` when both occur before identity resolution returns.
`onUpdateNode` asynchronously resolves every request-input edit, without
publishing a pending config. The editor builds each full update from unchanged
props, and the controller invalidates the earlier request when the next starts.
Its later whole-config result therefore loses an independent earlier edit.
The superseded edit is announced by an error toast from `onUpdateNode` in
`useGraphCommitController.ts`, so this is a P2 interaction failure rather than silent loss.
A superseded-update toast does not preserve the user's intended configuration.

**Plan:** Give request-input editing one authoritative pending draft or ordered
edit stream so each edit builds on the last accepted intent while server-owned
identities are resolved. Commit identities and graph references consistently;
retain the existing document, capability and stale-response guards. Avoid
resolving identities for changes that cannot change them only if the identity
contract proves that distinction. Define pending, failed and superseded edit
behaviour in frontend-shared and node-editor specifications first.

**Acceptance:** Delay identity responses and edit two different columns and two
different top-level config fields: both edits survive, including reversed reply
order. Repeated edits of the same field keep the latest value. A failed resolution
is visible and does not silently discard unrelated accepted edits. Switching
documents or becoming read-only still prevents stale commits. Save waits for
the final accepted draft and persists it. Extend the controller/editor tests.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useGraphCommitController.ts`;
`frontend/src/panels/NodePanel.tsx`;
`frontend/src/panels/editors/ApiInputEditor.tsx`.
