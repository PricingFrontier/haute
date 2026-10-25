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
