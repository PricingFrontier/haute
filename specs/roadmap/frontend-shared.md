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
| FSH-R01 | Planned | P2 | Every job wait uses the shared poller, which stops on unmount and cancellation. |
| FSH-R02 | Planned | P3 | One helper each for error messages, number and byte formatting, and debouncing; one modal and table base. |
| FSH-R03 | Planned | P3 | The results store holds results; validation and optimiser logic move out. |

## Planned improvements

`FSH-R01` fixes a leak and goes first.

### FSH-R01 — Every job wait uses the shared poller
**Why:** A shared `jobPollingController` and `useJobPolling` exist, but there
are sixteen polling implementations. Ad hoc loops live in the cache fetch
button (`setInterval`), the input-snapshot helpers, the input-snapshot cache
button, the optimiser auto-range hook and the dispersion client. The
snapshot cache button's `pollJobToTerminal` is an unbounded `for (;;)` loop
that polls every 800 ms with no abort signal or timeout, so it keeps polling
after its component unmounts.

**Plan:** Route every job wait through the shared controller, with an abort
signal tied to the component or request lifetime and a bounded wait where the
backend has a timeout.

**Acceptance:** No `for (;;)`, `while (true)` or `setInterval` job loop
remains outside the shared controller; a test unmounts the snapshot cache
button mid-build and observes no further status requests.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/jobPollingController.ts`;
`frontend/src/hooks/useJobPolling.ts`;
`frontend/src/panels/editors/_InputSnapshotCacheButton.tsx::pollJobToTerminal`;
`frontend/src/hooks/ensureInputSnapshots.ts`;
`frontend/src/components/CacheFetchButton.tsx`;
`frontend/src/panels/optimiser/useOptimiserAutoRange.ts`;
`frontend/src/api/dispersion.ts::runDispersionEstimate`.

### FSH-R02 — One helper per repeated concern
**Why:** Error messages are extracted from `ApiError` at 27 sites, with
character-identical helpers in the MLflow browser hook and the Databricks
selector, in the preview and tracing hooks, and in the optimiser config and
auto-range hook, plus a separate git formatter. There are 37 formatting
helpers; byte formatting alone exists four times and duration formatting
twice. Debouncing is hand-rolled eight times. Two dialogs build their own
overlays instead of `ModalShell`, the identity, working-branch and divergence
modals repeat the same form blocks, and 22 tables are hand-built while the
shared `SimpleTable` goes mostly unused. Guard helpers `asRecord` and
`isRecord` re-implement `expectObject`.

**Plan:** Add one `errorMessage` helper, consolidate formatting into
`utils/formatValue.ts` and `utils/formatBytes.ts`, add one debounce hook, and
move the modal overlays and simple tables onto the shared components. Replace
the local guard helpers with the shared ones.

**Acceptance:** Each concern has one implementation, and lint or a test
rejects a new local copy where that is cheap to express; the existing
component tests pass.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useMlflowBrowser.ts`;
`frontend/src/panels/editors/_DatabricksSelector.tsx`;
`frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/hooks/useTracing.ts`;
`frontend/src/panels/OptimiserConfig.tsx`; `frontend/src/utils/gitError.ts`;
`frontend/src/utils/formatValue.ts`; `frontend/src/utils/formatBytes.ts`;
`frontend/src/components/Toolbar.tsx`; `frontend/src/components/ModalShell.tsx`;
`frontend/src/components/NodeSearch.tsx`;
`frontend/src/components/IdentityPromptModal.tsx`;
`frontend/src/components/WorkingBranchModal.tsx`;
`frontend/src/components/DivergenceModal.tsx`;
`frontend/src/panels/editors/_shared.tsx`; `frontend/src/api/assistant.ts`.

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
