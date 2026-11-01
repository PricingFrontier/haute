# Frontend graph canvas roadmap

## Scope

The pipeline editor's shell and graph canvas: `App.tsx`, the node renderers, the
shared components (menus, dialogs, the toolbar, settings, the form kit), the
inspector shell and the preview frame, and the underwriter app's shell where it
shares those components. Current behaviour is specified in
[the frontend graph canvas specification](../frontend-graph-canvas/high-level.md)
and [the frontend shared specification](../frontend-shared/high-level.md). These
packages come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FGC-R09 | Planned | P2 | A blocked node downstream of an unavailable source opens its recovery inspector without running healthy-editor input derivation. |
| FGC-R10 | Planned | P1 | Submodel drill-in and breadcrumb exit isolate or reset Undo/Redo history and dirty baselines per navigation level, and close side panels and stale traces on entry and exit. |
| FGC-R11 | Planned | P2 | Global Constants edits participate in Undo/Redo history and are restored by `undo()` and `redo()`, and `nodeConstantReads` scans expression strings inside structured Polars steps. |
| FGC-R12 | Planned | P2 | `previewPortLabel` honours `chosenFrame` on multi-table `apiInput` and `workbenchInput` nodes, `computeStructuralFingerprint` and `computePanelContextFingerprint` include `submodels` and submodel boundary `ports`, `Ctrl+V` refuses `submodel` and `submodelPort` nodes, and `Escape` in a non-empty `SearchField` does not close an enclosing `ModalShell`. |

## Planned improvements

### FGC-R09 — Recovery inspection does not require executable input identities

**Why:** NodePanel computes input-source signatures before its recovery early return.
A real backend editor document for an unknown decorator retains the source as unavailable
with no default input identity and its downstream node as blocked. Parsing/adapting that
exact DTO and rendering the real NodePanel throws from edgeInputName before RecoveryNodePanel
can appear. This defeats the degraded-document inspection workflow.

**Plan:** Separate the recovery/diagnostic panel boundary from healthy editor derivation,
preserving React hook ordering without requiring unavailable sources to supply executable
identities. Use the server recovery metadata for diagnostics and keep strict identity
refusal in the actual executable path; do not invent a fallback input name.

**Acceptance:** Load a real degraded document with an unknown source decorator and blocked
downstream edge, adapt it through the browser parser and open the downstream inspector
without an exception. It shows the attributed diagnostic/blocking path and does not mount
a normal editor or trigger execution. Cover unavailable and scoped-editable cases according
to their capability contract; healthy nodes keep strict input identity validation.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/NodePanel.tsx::NodePanelContent` (input-identity resolution); `frontend/src/panels/NodePanel.tsx::NodePanelContent` (recovery availability guard);
`frontend/src/utils/apiInputPorts.ts::edgeInputName`; real load_pipeline_editor_document -> browser
adapter -> NodePanel witness with no frontend mocks.

### FGC-R10 — Submodel navigation isolates Undo/Redo history, dirty baselines, side panels and trace state
**Why:** `handleDrillIntoSubmodel` and `handleBreadcrumbNavigate` in
`useSubmodelNavigation` swap `nodes` and `edges` via `setNodesRaw` / `setEdgesRaw`
while leaving `undoStack`, `redoStack`, `lastSavedSnapshot` and
`savedPersistedFingerprint` in `useGraphStore` untouched:
1. Pressing Undo while drilled into a submodel pops a root-graph `HistoryEntry`
   onto the child canvas while `activeSubmodelIdentity` and `parentGraphRef` remain
   set, and breadcrumb exit then reconciles those root nodes into the child
   submodel definition; conversely, editing inside a submodel, returning to the
   root and pressing Undo replaces the root canvas with the child submodel's
   projected nodes and `submodel_port` cards.
2. `setNodesRaw` and `setEdgesRaw` during drill-in recompute `persistedFingerprint`
   from the child submodel's projected nodes against `savedPersistedFingerprint`
   (captured from the root graph), flipping `dirty` to `true` on an unmodified
   pipeline as soon as the user drills into a submodel.
3. Double-clicking an original submodel (`instanceOf === undefined`) in `App.tsx`
   calls `handleDrillIntoSubmodel` without closing Utility, Constants or Assistant
   side panels, and neither drill-in nor breadcrumb exit clears active trace state
   (`clearTrace`) or invalidates in-flight preview requests.

**Plan:** Scope Undo/Redo history to the root document (reconciling submodel edits
into `submodels` on the root snapshot or maintaining per-level history stacks that
cannot pop across navigation boundaries), compute `dirty` against the root
document's persisted snapshot even while viewing a child submodel, and close side
panels and clear trace/preview state on both submodel drill-in and breadcrumb
navigation.

**Acceptance:** Editing the root graph, drilling into a submodel and pressing Undo
does not inject root nodes into the submodel; editing inside a submodel, returning
to the root and pressing Undo reverts the submodel edit on the root graph without
replacing the root canvas with `submodel_port` nodes; drilling into a clean
submodel leaves `dirty === false`; and drilling into an original submodel closes
open side panels and clears any active trace.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useSubmodelNavigation.ts::useSubmodelNavigation`;
`frontend/src/stores/useGraphStore.ts::useGraphStore`;
`frontend/src/utils/submodelViewGraph.ts::buildSubmodelViewGraph`;
`frontend/src/App.tsx`.

### FGC-R11 — Global Constants edits participate in Undo/Redo history and `nodeConstantReads` scans structured step expressions
**Why:** `GlobalConstantsPanel` commits every constant add, rename, value change and
delete through `setGlobalConstantsRaw(next)`, which bypasses `pushHistory`. Even
though `cloneGraphSnapshot` captures `globalConstants` in every `HistoryEntry`,
`undo()` and `redo()` in `useGraphStore` read `get().globalConstants` and never
restore `prev.globalConstants` or `next.globalConstants`. Consequently, Global
Constants edits cannot be undone, and undoing or redoing any node or edge edit
leaves `globalConstants` out of step with the restored snapshot. In addition,
`stepConstantReads` and `nodeConstantReads` in `globalConstantsEditing.ts` collect
only `{ kind: "constant", name }` operands and `codeConstantReads` on `config.code`
and `step.kind === "free_code"`; after `authoredPolarsConfig` strips derived
`config.code` on stepped Polars nodes, `nodeConstantReads` misses `global_constants`
references inside `step.expression` (filter step), `value.expression` (assign and
`case_when` steps), and `branch.when` (`case_when` branch), so the Global Constants
panel under-counts references and skips delete confirmations for constants used in
step expressions.

**Plan:** Add a history-recording `setGlobalConstants` action to `useGraphStore`
(used by `GlobalConstantsPanel` for user edits while keeping `setGlobalConstantsRaw`
for document loads and resets), restore `prev.globalConstants` /
`next.globalConstants` in `undo()` and `redo()`, and pass `step.expression`,
`value.expression` and `branch.when` through `codeConstantReads` inside
`stepConstantReads`.

**Acceptance:** Adding, editing or deleting a Global Constant pushes an undo entry;
pressing Undo restores the previous `globalConstants` list and updates `dirty`
accurately; pressing Redo re-applies the constant change; and a constant referenced in
a filter, assign or `case_when` expression is counted as used by `nodeConstantReads`.

**Dependencies:** Coordinates with `FSH-R06` (`commitGraphState`).

**Evidence:** `frontend/src/panels/GlobalConstantsPanel.tsx::GlobalConstantsPanel`;
`frontend/src/stores/useGraphStore.ts::useGraphStore`;
`frontend/src/utils/graphSnapshot.ts::cloneGraphSnapshot`;
`frontend/src/utils/globalConstantsEditing.ts::stepConstantReads`;
`frontend/src/utils/globalConstantsEditing.ts::nodeConstantReads`.

### FGC-R12 — Multi-frame `previewPortLabel`, submodel structural/panel fingerprints, `Ctrl+V` submodel paste guard, and `SearchField` `Escape` isolation
**Why:** Four canvas identity, preview-routing and keyboard interaction helpers have
gaps:
1. `previewPortLabel` in `usePipelineAPI.ts` returns `chosenFrame` only when
   `node.data.nodeType === NODE_TYPES.WORKBENCH_OUTPUT`. For multi-table `apiInput`
   and `workbenchInput` nodes, `portLabelFor(node)` ignores `chosenFrame` and always
   returns the first handle (`ports[0].handle`), so selecting a non-default table tab
   in `DataPreview` (`previewNodeFrame`) stashes the fetched table under the first
   handle's key and a subsequent node click or manual refresh resets the preview to
   the first table instead of refreshing `chosenFrame`.
2. `computeStructuralFingerprint` and `computePanelContextFingerprint` in
   `useGraphStore.ts` (and `INPUT_KEYS` in `shallowNodeHash.ts`) omit `submodels`
   and submodel placeholder `ports` (`inputPorts` / `outputPorts`), so edits inside
   a submodel or to a submodel's boundary ports do not advance `structuralVersion`
   or `panelContextKey`.
3. In `useKeyboardShortcuts.ts`, the `Ctrl+V` paste filter checks only
   `!isSingletonType(nt)` without excluding `NODE_TYPES.SUBMODEL` or
   `NODE_TYPES.SUBMODEL_PORT` (unlike `handleDuplicateNode` in `useNodeHandlers.ts`,
   which blocks `NODE_TYPES.SUBMODEL`). Pasting a `submodel` node creates a new node
   with `id: "submodel_<n>"` while cloning `data.config.alias` from the copied
   submodel, which immediately violates `canonicalSubmodelBoundary` (`Submodel "<id>" has mismatched alias "<alias>"`)
   and throws in `SubmodelNode` and `resolveNodeConfigsToSave`. Pasting a
   `submodelPort` boundary card inside a drilled submodel view similarly creates an
   invalid `submodelPort_<n>` node.
4. `SearchField` (`frontend/src/components/form/SearchField.tsx`) states in its
   docstring that pressing `Escape` when non-empty clears the field `"its default prevented so nothing else hears it"`,
   but its `onKeyDown` handler calls only `event.preventDefault()` without
   `event.stopPropagation()`, and `ModalShell` (`frontend/src/components/ModalShell.tsx`)
   listens on `document.addEventListener("keydown", handler)` without checking
   `e.defaultPrevented`, so pressing `Escape` to clear a `SearchField` inside any
   `ModalShell` also closes the modal.

**Plan:** Extend `previewPortLabel` to return `chosenFrame` for multi-frame
`apiInput` and `workbenchInput` nodes as well as `workbenchOutput`; include
`submodels` and submodel `ports` (`inputPorts` / `outputPorts`) in
`computeStructuralFingerprint`, `computePanelContextFingerprint` and `INPUT_KEYS`;
exclude `NODE_TYPES.SUBMODEL` and `NODE_TYPES.SUBMODEL_PORT` from `Ctrl+V` paste in
`useKeyboardShortcuts.ts`; and check `e.defaultPrevented` in `ModalShell`'s
`Escape` listener (as well as calling `event.stopPropagation()` in `SearchField`).

**Acceptance:** Selecting a non-default table tab on a multi-table `apiInput` or
`workbenchInput` node and refreshing the preview re-fetches and retains that
`chosenFrame`; editing a submodel definition or boundary port increments
`structuralVersion` and updates `panelContextKey`; pressing `Ctrl+V` after copying a
`submodel` or `submodelPort` node does not insert an invalid node; and pressing
`Escape` in a non-empty `SearchField` inside a `ModalShell` clears the search text
without closing the modal.

**Dependencies:** Coordinates with `NAME-10` (paste edge IDs).

**Evidence:** `frontend/src/hooks/usePipelineAPI.ts::previewPortLabel`;
`frontend/src/hooks/usePipelineAPI.ts::usePipelineAPI`;
`frontend/src/stores/useGraphStore.ts::computeStructuralFingerprint`;
`frontend/src/stores/useGraphStore.ts::computePanelContextFingerprint`;
`frontend/src/utils/shallowNodeHash.ts::INPUT_KEYS`;
`frontend/src/hooks/useKeyboardShortcuts.ts::useKeyboardShortcuts`;
`frontend/src/components/form/SearchField.tsx::SearchField`;
`frontend/src/components/ModalShell.tsx::ModalShell`.
