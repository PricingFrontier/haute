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
| FSH-R02 | Planned | P3 | One debounce hook, one modal base and one table base. |
| FSH-R03 | Planned | P3 | The results store holds results; validation and optimiser logic move out. |

## Planned improvements

### FSH-R02 — One debounce hook, one modal base and one table base
**Delivered so far:** error text goes through `apiErrorMessage`, byte counts
through `formatBytes`/`formatByteSize`, durations through `formatDuration`,
and object checks through `isPlainObject`/`expectPlainObject` or
`isObjectLiteral`; ESLint rejects new local copies of each. The remaining
repeats are below.

**Why:** Debouncing is hand-rolled eight times (the banding statistics and
rating levels hooks, the rendered-steps hook, the filter member picker, the
code editor, the preview debounce, the utility panel's save timer and the edge
click preview). Two dialogs build their own overlays instead of `ModalShell`
(the node search palette, which is top-aligned, and a toolbar dialog), the
identity, working-branch and divergence modals repeat the same form blocks,
and 22 tables are hand-built while the shared `SimpleTable` goes mostly
unused. `gitErrorMessage` (40 call sites) still unwraps JSON-encoded string
details on its own, and the tracing hook's raw-detail text and the repair
dialog's `code: message` text are separate formatters.

**Plan:** Add one debounce hook (the banding and rating hooks share one
request hook after `FNE-R01`), move the two overlays onto `ModalShell`
(which then needs a top-aligned placement), extract the shared modal form
block, move simple tables onto `SimpleTable`, and decide whether the git,
tracing and repair formatters fold into `apiErrorMessage`.

**Acceptance:** Each concern has one implementation, and lint or a test
rejects a new local copy where that is cheap to express; the existing
component tests pass.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/useRenderedSteps.ts`;
`frontend/src/panels/editors/explorePivots/FilterMemberPicker.tsx`;
`frontend/src/panels/editors/CodeMirrorEditor.tsx`;
`frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/panels/UtilityPanel.tsx`;
`frontend/src/hooks/useEdgeHandlers.ts`; `frontend/src/components/Toolbar.tsx`;
`frontend/src/components/ModalShell.tsx`;
`frontend/src/components/NodeSearch.tsx`;
`frontend/src/components/IdentityPromptModal.tsx`;
`frontend/src/components/WorkingBranchModal.tsx`;
`frontend/src/components/DivergenceModal.tsx`;
`frontend/src/panels/editors/_shared.tsx`; `frontend/src/utils/gitError.ts`;
`frontend/src/hooks/useTracing.ts`;
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
