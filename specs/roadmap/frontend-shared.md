# Frontend shared roadmap

## Scope

The browser's shared hooks, stores, helpers and API client. Current
behaviour is specified in
[the frontend-shared specification](../frontend-shared/high-level.md).
Generating the API contract is planned in the
[server API roadmap](server-api.md) (`API-R03`). These packages come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FSH-R02 | Planned | P3 | One table base, and the repair dialog's error text through `apiErrorMessage`. |
| FSH-R03 | Planned | P3 | The results store holds results; validation and optimiser logic move out. |

## Planned improvements

### FSH-R02 — One table base and the repair dialog's error text
**Delivered so far:** error text goes through `apiErrorMessage` (the Git UI's
`gitErrorMessage` included), byte counts through `formatBytes`/`formatByteSize`,
durations through `formatDuration`, and object checks through
`isPlainObject`/`expectPlainObject` or `isObjectLiteral`; ESLint rejects new
local copies of each. Scheduled calls debounce through `useDebouncedCallback`
(the code editor, the utility panel's autosave and the canvas preview); a
delayed request inside an effect that clears its timer and aborts its request
on cleanup stays in that effect. The node-search palette sits on a top-placed
`ModalShell`, and the working-branch, divergence and identity modals share
`ModalForm`. The tracing hook's `technicalDetail` is not error text: it keeps
the raw detail whole for the trace panel's Technical details disclosure.

**Why:** Tables are hand-built one by one; the review's premise that a shared
`SimpleTable` already exists is wrong, as none has ever been in the frontend.
The repair dialog's `code: message` text is a separate error formatter.

**Plan:** Decide whether a shared table base is worth adding (the plain value
tables in the modelling and Explore panels are the candidates; their styling
differs today), and fold the repair dialog's text into `apiErrorMessage` if
the dialog survives the recovery-scope work.

**Acceptance:** Each concern has one implementation, and lint or a test
rejects a new local copy where that is cheap to express; the existing
component tests pass.

**Dependencies:** `API-R04` ([server API](server-api.md)) decides the repair
dialog's future.

**Evidence:** `frontend/src/panels/modelling/SummaryTab.tsx`;
`frontend/src/panels/explore/ExploreSummaryCards.tsx`;
`frontend/src/panels/explore/ExploreRelationshipsPane.tsx`;
`frontend/src/components/PipelineRepairDialog.tsx`.

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
