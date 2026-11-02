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
| FGC-R12 | Planned | P2 | `previewPortLabel` honours `chosenFrame` on multi-table `apiInput` and `workbenchInput` nodes, `computeStructuralFingerprint` and `computePanelContextFingerprint` include `submodels` and submodel boundary `ports`, `Ctrl+V` refuses `submodel` and `submodelPort` nodes, and `Escape` in a non-empty `SearchField` does not close an enclosing `ModalShell`. |

## Planned improvements

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
