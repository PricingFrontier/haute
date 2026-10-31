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
| FGC-R01 | Planned | P2 | Every path that makes a node active opens it as a click does, so Automatic calculation computes its preview; the refresh shortcut honours editable controls and open dialogs; the side panes close through one action. |
| FGC-R02 | Planned | P2 | Every node renderer draws every flag the canvas stamps: the comparison diff ring, the trace and change focus rings and the trace value on a submodel occurrence, the selection border and hover dimming on a boundary card; an unknown node type is as loud on the canvas as in the inspector. |
| FGC-R03 | Planned | P3 | The editor's chrome does not re-render on every drag frame, measured by render count before and after. |
| FGC-R04 | Planned | P3 | One copy of each shell mechanism (menu keyboard handling, the radio group, the committed draft), every error shown with the server's detail, the Hotkeys list derived from the bindings, the dead prop gone and the swallowed context fetches logged. |
| FGC-R05 | Planned | P1 | Finishing auto-layout changes positions without restoring older node data or replacing a different graph. |
| FGC-R06 | Planned | P1 | A delayed drill-in cannot replace a newer graph, source path or navigation stack. |
| FGC-R07 | Planned | P3 | Deleting selected nodes and separate selected edges performs one complete, undoable selection change. |
| FGC-R08 | Planned | P2 | A late file read cannot replace a newer selection or discard edits made while the read was pending. |
| FGC-R09 | Planned | P2 | A blocked node downstream of an unavailable source opens its recovery inspector without running healthy-editor input derivation. |
| FGC-R10 | Planned | P1 | Submodel drill-in and breadcrumb exit isolate or reset Undo/Redo history and dirty baselines per navigation level, and close side panels and stale traces on entry and exit. |
| FGC-R11 | Planned | P2 | Global Constants edits participate in Undo/Redo history and are restored by `undo()` and `redo()`, and `nodeConstantReads` scans expression strings inside structured Polars steps. |
| FGC-R12 | Planned | P2 | `previewPortLabel` honours `chosenFrame` on multi-table `apiInput` and `workbenchInput` nodes, `computeStructuralFingerprint` and `computePanelContextFingerprint` include `submodels` and submodel boundary `ports`, `Ctrl+V` refuses `submodel` and `submodelPort` nodes, and `Escape` in a non-empty `SearchField` does not close an enclosing `ModalShell`. |

## Planned improvements

### FGC-R01 — Every selection path opens the node as a click does
**Why:** Node search, the recovery banner's go-to, a name-violation click, a repair
applied and a scoped save applied each set the active node by hand (`setSelectedNode`,
`setLastSelectedId`, the ref) and skip `openNode`, the one place a selection calls
`fetchPreview`; in Automatic calculation they open the inspector without computing the
node's preview and without the trace and preview reset a click performs. The Ctrl/Cmd+Enter
refresh shortcut hand-rolls its typing test (`INPUT`, `TEXTAREA`, `.cm-editor`) instead
of `keyboardTargets.ts`, so it is swallowed from a focused checkbox, fires from a select,
fires inside an open modal dialog and ignores `defaultPrevented`. The "close the side
panes" block is written five times with three store setters each, although any one UI-store
setter already closes every other pane, so the real effect (a node click also closes the
Assistant and Files panels) is invisible at the call sites.

**Plan:** One `activateNode(node)` (beside `openNode`) that closes the side panes through
one `closeSidePanes()` store action, selects the node and, in Automatic calculation,
fetches its preview; the five sites call it, node search keeping its centring step. The
refresh handler returns on `defaultPrevented`, `inModalDialog` or `isFormControl`.

**Acceptance:** App tests: node search in Automatic calculation fetches the selected
node's preview; the recovery go-to does the same; Ctrl+Enter from a focused select or
inside an open dialog refreshes nothing; the UI store has one `closeSidePanes` and
`App.tsx` no three-setter block.

**Dependencies:** None.

**Evidence:** `frontend/src/App.tsx::handleNodeSearchSelect`,
`frontend/src/App.tsx::handleSelectRecoveryElement`,
`frontend/src/App.tsx::handleSelectNameViolation`, `frontend/src/hooks/useEdgeHandlers.ts::openNode`,
`frontend/src/stores/useUIStore.ts`, `frontend/src/hooks/keyboardTargets.ts::isFormControl`.

### FGC-R02 — Every node renderer draws every canvas flag
**Why:** The comparison view stamps `_diffStatus` on every node and `useTracing` stamps
`_hoverDimmed`, `_traceFocused`, `_changeFocused` and `_traceValue` on every node, but
only `PipelineNode` draws from all of them. `SubmodelNode` reads the trace-active,
trace-dimmed, hover-dimmed and motion flags alone, so a submodel occurrence draws no
diff ring, no focus ring and no trace value; `SubmodelPortNode` reads the three trace
flags and never `selected` or `_hoverDimmed`, so a boundary card draws no selection
border and never hover-dims. The specification asks for a consistent highlight on every
node type. Separately, `PipelineNode` renders an unknown `nodeType` as a Transform card
through `||` fallbacks while the parser rejects unknown types and the inspector renders an
explicit diagnostic.

**Plan:** Extract the ring and opacity derivation from `PipelineNode` (focus shadow, diff
shadow and outline, dimmed opacity, selection border) into one helper and apply it in all
three renderers. Type `nodeType` as the node-type union and render the unavailable card
for an unknown type as the inspector does.

**Acceptance:** A test per flag per renderer: a submodel occurrence shows the diff ring in
the comparison view and the focus ring from `_changeFocused`; a boundary card shows the
selection border and hover-dims; an unknown type renders the unavailable card.

**Dependencies:** None.

**Evidence:** `frontend/src/nodes/PipelineNode.tsx`, `frontend/src/nodes/SubmodelNode.tsx`,
`frontend/src/nodes/SubmodelPortNode.tsx`, `frontend/src/components/ComparisonView.tsx::prepNodes`,
`frontend/src/hooks/useTracing.ts`, `frontend/src/utils/graphDiff.ts`.

### FGC-R03 — The chrome does not re-render per drag frame
**Why:** `FlowEditor` subscribes to `nodes`, which `useGraphCanvasState` writes for each
mid-drag position change, and passes freshly created inline callbacks to unmemoised
children (`Toolbar`, the inspector with its selectors, memos and lazy editor, the preview
table), so the chrome re-renders at drag-frame rate with unchanged inputs. The
specification's selector-isolation gate exists for exactly this cost. The review did not
measure it; the package starts with the measurement.

**Plan:** A render-count test (as the selector-isolation gate does) for the toolbar, the
inspector and the preview during a drag; then `memo` the chrome children and hoist the
inline closures into the existing `useCallback`s, or host `<ReactFlow>` in a child that
owns the `nodes` subscription.

**Acceptance:** The render-count test shows the toolbar, inspector and preview rendering
at most once per drag; if the measurement shows no cost, the package is retired with the
test kept.

**Dependencies:** None.

**Evidence:** `frontend/src/App.tsx`, `frontend/src/hooks/useGraphCanvasState.ts::setNodesRaw`,
`frontend/src/panels/NodePanel.tsx`, `frontend/src/panels/DataPreview.tsx`.

### FGC-R04 — One copy of each shell mechanism, errors with their detail
**Why:** Six error surfaces in the utility panel and the comparison view build their text
from `err.message`, which for an `ApiError` is only `HTTP <status>`, so the server's
detail is dropped ("Failed to create: HTTP 409"); `apiErrorMessage` exists and the lint
bans two other shapes but not this one. Four menus implement arrow-key roving focus by
hand in four shapes, one of them landing ArrowUp from outside on the second-last item.
`CalculationSettings` re-implements the radio group `ToggleButtonGroup` provides, minus
Home/End. The Hotkeys list is hand-maintained and omits Ctrl+Y, Ctrl+G and Escape, which
the hook binds. `NodePanelProps.onRecoverSettings` is declared and never read or passed.
The utility editor recovers the error line by regex from the route's flat sentence, and a
file whose last save was refused cannot be left with nothing saying why. Two fields
re-implement the committed draft beside the form kit's `useCommittedDraft`. The
breadcrumb-context fetches swallow every failure.

**Plan:** `apiErrorMessage` at the six sites and the lint selector extended to the
`instanceof Error ? err.message` shape. One `useMenuKeyboard(ref)` hook (roving focus,
Home/End, Escape) for the four menus. `CalculationSettings` renders `ToggleButtonGroup`
with a two-line label. The Hotkeys modal and the hook read one bindings table. Delete the
prop. The utility route returns `{line, message}` in its 400 detail; a refused switch
toasts why. `PipelineSettingField` and `ConstantRow` use `CommittedTextField` or
`ValidatedTextField`. The context fetches log the status on failure.

**Acceptance:** Grep finds one roving-focus implementation, one radio group, one bindings
table and no `err.message` toast; the lint fails on the old shape; the Hotkeys modal lists
Ctrl+Y and Ctrl+G; a 409 in the utility panel shows the server's detail.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/UtilityPanel.tsx`, `frontend/src/components/ComparisonView.tsx`,
`frontend/src/api/errors.ts::apiErrorMessage`, `frontend/eslint.config.js`,
`frontend/src/components/ContextMenu.tsx`, `frontend/src/components/ConnectionDropMenu.tsx`,
`frontend/src/components/ProjectControls.tsx`, `frontend/src/app/ui.tsx`,
`frontend/src/components/PipelineSettingsModal.tsx::CalculationSettings`,
`frontend/src/components/ToggleButtonGroup.tsx`, `frontend/src/components/KeyboardShortcuts.tsx`,
`frontend/src/hooks/useKeyboardShortcuts.ts`, `frontend/src/panels/NodePanel.tsx`,
`src/haute/routes/utility.py`, `frontend/src/panels/GlobalConstantsPanel.tsx::ConstantRow`,
`frontend/src/components/form/CommittedTextField.tsx::useCommittedDraft`.

### FGC-R05 — Auto-layout cannot overwrite edits
**Why:** The [28 October review](codebase-review-2026-10-28.md) delayed a layout,
edited a node's code, and completed the layout: the code reverted to its old
value. `handleAutoLayout` captures full nodes before awaiting ELK, then replaces
the live nodes with that returned snapshot. Only another layout is blocked;
ordinary edits remain available. The same write can restore deleted nodes,
remove added nodes or overwrite a newly loaded document while leaving its edges.
Those structural variants are source-supported; the executed probe changes code.

**Plan:** Apply returned geometry to current nodes by ID, preserving live data
and other state, only while the initiating document and required topology remain
current. Reject or explicitly recompute an outdated structural layout. Keep
selection, undo and fit-view behaviour scoped to the accepted layout; do not
apply an old graph merely because ELK completed successfully. Define the
document/topology and concurrent-position policy in the canvas specification.

**Acceptance:** An edit during a delayed layout survives with the new positions.
Adding/deleting nodes or switching documents cannot resurrect/drop nodes or
create mismatched edges; an obsolete layout reports its disposition without
changing the new document. A normal layout and undo still work. Use a controlled
layout promise against the real node-handler hook, asserting final graph data.

**Dependencies:** None; reuse existing document/structural identity helpers.

**Evidence:** `frontend/src/hooks/useNodeHandlers.ts`;
`frontend/src/utils/layout.ts`; `frontend/src/components/Toolbar.tsx`.

### FGC-R06 — Pending submodel navigation belongs to its document

**Why:** `drillInto` awaits identity resolution and layout, then installs captured child nodes
and parent context without authenticating the document it started from. The real hook/store
witness loads a different root while drill-in waits; completion replaces it with the old child,
sets `modules/pricing.py`, and creates a depth-two stack mixing old nodes with the new
document's submodels.

**Plan:** Bind navigation work to the initiating document, source, occurrence and graph
revision. Invalidate it on reload, another navigation, graph changes or lost capability;
recheck immediately before committing nodes, path and stack together. Retain the user's current
document when an old request finishes.

**Acceptance:** Delay each await, then load another document or edit/remove the occurrence:
resolving the old request changes no current graph/path/stack. An unchanged drill succeeds with
one coherent parent snapshot; navigating back restores that same parent. Test the actual store
and hook.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useSubmodelNavigation.ts::useSubmodelNavigation.handleDrillIntoSubmodel`; its existing tests cover reload after completed navigation but not during it.

### FGC-R07 — Delete removes the complete eligible selection

**Why:** The selected-node branch removes incident edges but ignores separately selected edges.
A real keyboard/store test selects node A and the unrelated B-to-C edge; Delete removes A while
leaving the selected edge. The edge-only branch behaves differently.

**Plan:** Compute the eligible node and edge deletion set together, including incident edges
and selected unrelated edges. Apply existing submodel-owner/boundary protections consistently
and commit one graph transaction with its normal result cleanup.

**Acceptance:** A mixed selection removes the selected node, its incident edges and the
selected unrelated edge, preserving unrelated unselected edges. One undo restores the whole
change. Protected submodel selections remain protected and the allowed part of a mixed
selection follows the documented policy.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useKeyboardShortcuts.ts::useKeyboardShortcuts.handler` (Delete/Backspace branch); `frontend/src/hooks/useKeyboardShortcuts.ts::useKeyboardShortcuts.handler` (Delete/Backspace branch).

### FGC-R08 — Utility navigation preserves drafts across pending reads

**Why:** UtilityPanel flushes the old draft before awaiting the next file read but leaves
the old editor enabled. New typing during that await is not part of the flush. When B
arrives it replaces A; A's later rejected autosave is ignored because A is no longer
active, and returning to A reloads the old disk contents. The real component/debounce
witness loses `a = (` and restores `a = 1`. A second witness selects Imports while B
waits and B's late response steals the selection.

**Plan:** Give utility reads a selection generation and preserve drafts/save outcomes
per module, or prevent an accepted switch until every edit made during the pending
transition is safely retained. Authenticate both source draft and destination selection
immediately before adoption. A failed autosave must retain the unsaved text and expose
its error even if another module is shown.

**Acceptance:** Delay loading B, type in A, and reject A's eventual save: A's draft and
error remain recoverable, never replaced by disk bytes. Delay B then select Imports/C:
B cannot steal the newer selection. Successful ordinary navigation still flushes edits
once and loads the requested module. Cover edit-after-flush as well as edit-before-switch.

**Dependencies:** None. The utility routes refuse a write made against a changed file
(412, specs/server-api); keeping the draft any failed save leaves is this package's.

**Evidence:** `frontend/src/panels/UtilityPanel.tsx::UtilityPanel.persistSave`; `frontend/src/panels/UtilityPanel.tsx::UtilityPanel.loadFile`; `frontend/src/panels/UtilityPanel.tsx::UtilityPanel.selectImports`; actual
component/debounce with CodeEditor textarea and API transport seams.

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
