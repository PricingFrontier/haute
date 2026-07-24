# Frontend Graph Canvas — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/App.tsx` | `FlowEditor` — the canvas orchestrator: wires `<ReactFlow>` event props to interaction hooks, owns local selection/context-menu/dialog state, picks the active preview pane, handles `onUpdateNode` (including api-input edge reconciliation), and gates Save/Save-&-Commit on git working-branch status. Exports `App`, which mounts `FlowEditor` inside `ReactFlowProvider`. |
| `frontend/src/nodes/PipelineNode.tsx` | Renders every non-submodel node type across three zoom LODs and the edge-join marker variant; computes source/target `Handle` sets, including multi-frame api-input handles (row-mounted on the full-detail frame-row body, evenly spaced at medium/compact) and edge-join geometry-dependent handle placement; owns the api-input frame-row body with instance-name suppression and the zero-frame "No emitted frames" state. |
| `frontend/src/nodes/SubmodelNode.tsx` | Renders a submodel boundary card: label, child count, file path, and hidden per-port target/source handles mirroring `config.inputPorts`/`config.outputPorts`. |
| `frontend/src/nodes/SubmodelPortNode.tsx` | Renders the input/output port marker shown when a submodel is drilled into; input ports get a source handle, output ports get a target handle. |
| `frontend/src/panels/useGraph.ts` | Defines `GraphContext` (`React.Context<GraphContextValue \| undefined>`) and the `useGraph()` consumer hook, which throws when called outside a provider. |
| `frontend/src/panels/GraphContext.tsx` | `GraphProvider` component; memoises the context value on `{allNodes, edges, submodels, preamble}` identity. |
| `frontend/src/stores/useGraphStore.ts` | Zustand store owning `nodes`/`edges`/`preamble`/`submodels`, undo/redo history (four-field graph snapshots interleaved with VC entries), and three derived fingerprints (`structuralFingerprint`, `panelContextFingerprint`, `persistedFingerprint`) plus the `dirty` boolean derived from them. |
| `frontend/src/hooks/useNodeHandlers.ts` | Node CRUD handlers: `handleDeleteNode` (atomic node+edges delete, deferred cache cleanup), `handleDuplicateNode`, `handleCreateInstance`, `handleRenameNode` (opens the rename dialog), `handleAutoLayout` (ELK, in-flight guarded). |
| `frontend/src/hooks/useEdgeHandlers.ts` | Connection/gesture handlers: `commitConnection`/`onConnectEnd` (interprets React Flow handle-drag endings into a normal edge or an edge-join insertion), `onSelectionChange`/`onNodeClick` (panel + debounced preview), `handleDeleteEdge`, `onNodeContextMenu`, `onDragOver`/`onDrop` (palette node creation). |
| `frontend/src/hooks/usePipelineAPI.ts` | Pipeline load-on-mount; debounced, cache-first, concurrency-limited-cascade preview fetching (`fetchPreview`/`fetchPreviewImmediate`/`refreshPreview`/`previewNodeFrame`); an active-source-change effect (`invalidateStaleColumnStashes`) that strips any node's column stash tagged with a different (or no) `_columnsSource`; and `handleSave` (config-ref/edge-join pre-save validation, snapshot-scoped save-concurrency guard, `markSaved`). |
| `frontend/src/hooks/useWebSocketSync.ts` | The `/ws/sync` WebSocket client: connect/reconnect with exponential backoff, fingerprint-based resync, applying `graph_update`/`parse_error` frames (with dirty-blocking and rollback-on-failure), and session-expiry handling. |
| `frontend/src/hooks/useSubmodelNavigation.ts` | `handleCreateSubmodel`/`handleDrillIntoSubmodel`/`handleBreadcrumbNavigate`/`handleDissolveSubmodel` — the view-stack state machine, cross-boundary port-node/edge synthesis on drill-in, and the three submodel API calls. |
| `frontend/src/utils/buildGraph.ts` | `buildGraph` (backend payload shape) and `resolveGraphFromRefs` (parent-graph-takes-priority resolution used by preview/save/submodel calls). |
| `frontend/src/utils/graphDiff.ts` | `diffPipelineNodes` — pure added/removed/changed/moved node diff between two graph versions, backing the comparison view. |
| `frontend/src/utils/graphHelpers.ts` | `computeNextNodeId` and `normalizeEdges`. |
| `frontend/src/utils/graphPerformance.ts` | `shouldUseLiteGraphEffects`/`GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT` (1000). |
| `frontend/src/utils/makePreviewData.ts` | `makePreviewData` — `PreviewData` constructor with defaults. |
| `frontend/src/utils/columnFingerprint.ts` | `columnFingerprint`/`columnsEqualByFingerprint` — collision-safe column-schema fingerprinting. |
| `frontend/src/utils/activePreview.ts` | `previewForActiveNode` — filters preview data to the currently active node. |
| `frontend/src/utils/validateConfigRefs.ts` | `validateConfigRefs`/`formatConfigRefWarnings` — flags `data_input`/`banding_source`/`instanceOf` config fields pointing at non-existent node ids (graph-level or submodel-internal). |
| `frontend/src/utils/layout.ts` | `getLayoutedElements` — lazily-imported ELK layered layout plus `clusterSnap`/`alignPositions` coordinate snapping. |
| `frontend/src/utils/connectionValidation.ts` | `isPipelineConnectionValid` — the graph-level `isValidConnection` React Flow validator, including the input-name uniqueness rule (a candidate connection whose `edgeInputName` duplicates an existing input name on the target node is invalid, surfaced as a named toast at commit time) and the null-API-handle rejection (an apiInput connection with no source handle is invalid in every gesture direction, the validator-level twin of the zero-frame handle's `isConnectable` false under `ConnectionMode.Loose`). |
| `frontend/src/utils/flowElements.ts` | `appNode`/`appEdge`/`nodeLabel`/`edgeId`/`deselectNodes`/`selectOnlyNode` — node/edge factories and id/selection helpers. |
| `frontend/src/utils/flowHandles.ts` | `DEFAULT_TARGET_HANDLE`/`normalizeDefaultTargetHandle` — collapses React Flow's synthetic default-target-handle id to `null`. |
| `frontend/src/utils/nodeTypes.ts` | `NODE_TYPES`/`NODE_TYPE_META` and derived lookups (`SOURCE_ONLY_TYPES`, `SINK_ONLY_TYPES`, `SINGLETON_TYPES`, `PALETTE_TYPES`, `nodeTypeIcons`/`Colors`/`Labels`, `PILL_TYPES`) — the single source of truth for node-type metadata. |
| `frontend/src/components/ComparisonInspector.tsx` | Read-only comparison-view config panel: renders the real node editor `inert` for the available side(s), with a Historical/Current switcher. |
| `frontend/src/components/ComparisonView.tsx` | The historical-vs-current comparison canvas pair: fetches the historical pipeline, diffs it, and renders two non-interactive `ReactFlow` instances (`ReadonlyCanvas`) with diff-ring highlighting, a draggable split, and orientation toggle. |
| `frontend/src/components/PolarsIcon.tsx` | Memoized SVG icon for the Polars node type. |
| `frontend/src/components/RenameDialog.tsx` | Node-rename modal with name-length and unsafe-character validation. |
| `frontend/src/components/SubmodelDialog.tsx` | "Create submodel" name-entry modal. |

Additional graph modules assigned to this component; the table which follows
is the authoritative module map for their responsibilities:

- `frontend/src/hooks/useGraphCanvasState.ts` — the React Flow adapter over
  the store; translates `NodeChange[]`/`EdgeChange[]` deltas into
  `setNodesRaw`/`pushSnapshot` calls.
- `frontend/src/hooks/usePanelGraphContext.ts` — builds the
  `PanelGraphContextSnapshot` (`{allNodes, edges, nodeById, getNode}`) that
  `App.tsx` passes into `<GraphProvider>`, recomputed only when
  `panelContextVersion` changes.
- `frontend/src/utils/nodeTypeRegistry.ts` — the `NODE_TYPES` → component
  registry shared with the read-only comparison canvas.
- `frontend/src/types/node.ts` — `HauteNodeData`, `SubmodelNodeData`,
  `SubmodelPortData`, and the `nodeData()`/`effectiveNodeType()` accessors
  used throughout.
- `frontend/src/utils/graphSnapshot.ts`, `frontend/src/utils/shallowNodeHash.ts`
  — serialization and shallow-hashing helpers behind the store's
  fingerprints.

| File | Responsibility |
| --- | --- |
| `frontend/src/hooks/useGraphCanvasState.ts` | React Flow adapter over `useGraphStore`: converts `NodeChange[]`/`EdgeChange[]` into raw graph updates, takes one snapshot at a drag's first structural position change, and avoids history churn for per-frame movement and selection-only changes. |
| `frontend/src/hooks/usePanelGraphContext.ts` | Produces the typed, render-stable `PanelGraphContextSnapshot` (`allNodes`, `edges`, `nodeById`, `getNode`) only when the graph store's panel-context version changes, isolating editor consumers from React Flow UI-only updates. |
| `frontend/src/hooks/useKeyboardShortcuts.ts` | App-level canvas keyboard bindings for save, undo/redo, copy/paste, delete, search, and panel dismissal; honours editable controls so keystrokes do not leak from a text field into graph mutation. |
| `frontend/src/utils/apiInputPorts.ts` | Mirrors backend api-input frame identity: `apiInputFrameLabels` is the single ordered eligible-frame-label list (no minimum count) that drives handles, body rows, and downstream input names alike; `edgeInputName` derives an edge's input/argument name (frame label verbatim for api-input edges, sanitised source label otherwise, submodel `out__` edges resolved to the child's sanitised label) in lockstep with the backend's `edge_input_name`; validates blank/duplicate/non-identifier/keyword labels (`apiInputLabelIssue`, mirroring backend invariant B4 exactly — ASCII identifier `/^[A-Za-z_][A-Za-z0-9_]*$/` plus the Python hard-keyword list — with duplicates compared case-insensitively to match backend B2's casefolded parquet-stem rule); migrates edges on a conservative in-place table rename, then prunes only genuinely orphaned handles while preserving input array identity on no-op. |
| `frontend/src/utils/edgeJoinRoles.ts` | Defines edge-join base/join handle roles and canonical role resolution, including compatibility handling for legacy/default handle ids. |
| `frontend/src/utils/edgeJoinGraph.ts` | Pure edge-join insertion/rewrite helpers: split an existing edge or combine source gestures into a correctly configured join node and its role-bound edges. |
| `frontend/src/utils/edgeJoinValidation.ts` | Save-time edge-join graph validation and readable warnings; rejects incomplete, duplicate, or otherwise inconsistent role/edge representations before the backend receives them. |
| `frontend/src/utils/nodeTypeRegistry.ts` | React Flow node-type registry built from the canonical metadata, shared by the editable and read-only comparison canvases. |
| `frontend/src/utils/graphSnapshot.ts` | Snapshot serialization/cloning helpers which omit transient node data so undo/redo and persisted fingerprints describe graph state rather than preview/UI residue. |
| `frontend/src/utils/shallowNodeHash.ts` | Stable shallow data hashing used by the graph store's structural and persisted fingerprint calculations. |
| `frontend/src/types/node.ts` | Canonical React Flow node, edge, submodel-port, column, and status shapes plus `nodeData()`/`effectiveNodeType()` accessors used at the untyped React Flow boundary. |

## Key types and data structures

- **`GraphSnapshot`** (`{ nodes: Node[]; edges: Edge[]; preamble: string }`,
  `useGraphStore.ts`) — the unit of undo/redo for a graph edit.
- **`VcHistoryEntry`** (`{ kind: "vc"; label: string; undo: () => Promise<void>; redo: () => Promise<void> }`)
  — a version-control operation riding the same history stacks; carries its
  own async inverse. `HistoryEntry = GraphSnapshot | VcHistoryEntry`;
  `isVcEntry()` is the discriminant type guard.
- **`GraphStore`** — the full state/action surface: state
  (`nodes`, `edges`, `preamble`, `lastSavedSnapshot`, `undoStack`,
  `redoStack`, `vcBusy`, `structuralVersion`/`structuralFingerprint`,
  `panelContextVersion`/`panelContextFingerprint`, `persistedFingerprint`,
  `savedPersistedFingerprint`, `dirty`); history-aware actions (`setNodes`,
  `setEdges`, `setNodesAndEdges`, `setPreamble`); raw actions
  (`setNodesRaw`, `setEdgesRaw`, `setPreambleRaw`); explicit history ops
  (`pushSnapshot`, `pushVcEntry`, `undo`, `redo`); `markSaved`; and pure
  selectors (`isDirty`, `canUndo`, `canRedo`).
- **`HauteNodeData`** (`types/node.ts`) — base node data shape:
  `label`, `nodeType`, `description?`, `config?`, `code?`, `func_name?`, plus
  underscore-prefixed *transient* fields that are runtime-only and never
  persisted: `_columns`, `_availableColumns`, `_schemaWarnings`,
  `_columnsSource` (the active source the column stash was captured
  under — set alongside `_columns`/`_availableColumns`/`_schemaWarnings`
  by `usePipelineAPI`, compared against the live active source to decide
  staleness), `_status`, `_traceActive`, `_traceDimmed`, `_hoverDimmed`,
  `_traceValue`, `_traceMotionDisabled`, `_diffStatus`.
- **`SubmodelNodeData`** extends `HauteNodeData` with
  `config: { file?, childNodeIds?, inputPorts?, outputPorts? }`.
- **`SubmodelPortData`** — `{ label, portDirection: "input" | "output", portName, _traceActive?, _traceDimmed?, _traceMotionDisabled? }`.
- **`GraphContextValue`** (`useGraph.ts`) —
  `{ allNodes: SimpleNode[]; edges: SimpleEdge[]; submodels?; preamble? }`.
- **`PanelGraphContextSnapshot`** (`usePanelGraphContext.ts`) —
  `{ allNodes, edges, nodeById: Map<string, SimpleNode>, getNode }`, built by
  `toSimpleNode`/`toSimpleEdge`, which strip React-Flow-only fields and
  apply `label`/`nodeType` fallbacks (`data.label || node.id`,
  `data.nodeType || node.type || ""`).
- **`PipelineAPIReturn`** (`usePipelineAPI.ts`) — the hook's full surface:
  `loading`, `previewData`/`setPreviewData`, `previewBusy`, `nodeStatuses`,
  `fetchPreview`/`cancelPreview`/`refreshPreview`/`previewNodeFrame`, and
  `handleSave: () => Promise<boolean>` (resolves `true`/`false`, never
  rejects).
- **`FetchPreviewOptions`** — `{ debounceMs? }`, the per-call override for
  the preview debounce (Optimiser click previews use a longer one).
- **`GraphDiff`** (`graphDiff.ts`) —
  `{ added, removed, changed, moved: Set<string> }` node ids, keyed by the
  comparison view's two graph versions.
- **`ComparisonNodeFacet`** (`ComparisonView.tsx`) —
  `{ label, nodeType, config }`, one side's editable-surface view of a node.
- **`ComparisonInspect`** (`ComparisonView.tsx`) —
  `{ id, status: "added"|"removed"|"changed"|"unchanged", current: ComparisonNodeFacet | null, historical: ComparisonNodeFacet | null }`,
  the resolved-on-both-sides payload handed to `ComparisonInspector`.
  `current`/`historical` is `null` exactly when the node doesn't exist on
  that side (added/removed respectively).
- **`ConfigRefWarning`** (`validateConfigRefs.ts`) —
  `{ nodeId, nodeLabel, field, referencedId }`, one broken config
  reference.
- **`ColumnFingerprintInput`** (`columnFingerprint.ts`) —
  `readonly { name: string; dtype: string }[]`, the shape fingerprinted for
  cheap schema-equality checks.

## Control flow

1. **Mount.** `FlowEditor` calls `useGraphCanvasState([], [], graphRefreshingRef)`.
   A `seededRef`-guarded effect seeds the store once with the caller's
   initial (empty in production) nodes/edges and resets `undoStack`/
   `redoStack`/all three fingerprints. The real pipeline arrives later via
   `usePipelineAPI` calling `setNodesRaw`/`setEdgesRaw` (out of scope here).
2. **Render subscriptions.** `useGraphCanvasState` reads `nodes`/`edges`/
   `undoStack.length`/`redoStack.length` via selector-isolated
   `useGraphStore((s) => …)` calls. `App.tsx` separately subscribes to
   `s.preamble` and `s.dirty` the same way, so preamble edits or dirty flips
   don't re-render siblings subscribed to other slices.
3. **Drag.** React Flow emits a `NodeChange[]` per frame to `onNodesChange`
   (`useGraphCanvasState.ts`). A `position` change with `dragging: true` on a
   node not already marked dragging triggers exactly one `pushSnapshot()`
   (drag start); every subsequent position frame in that drag — and
   selection-only changes — go through `setNodesRaw(applyNodeChanges(...))`.
   `add`/`remove`/`replace` changes always push a snapshot first.
4. **Structural edits** (connect, delete, paste, edge-join swap) are driven
   by `hooks/useNodeHandlers.ts` and `hooks/useEdgeHandlers.ts` (owned
   elsewhere) calling the history-aware setters exposed here. `App.tsx`'s
   own `handleSwapEdgeJoinInputs` is a concrete example: it calls
   `pushSnapshot()` once, then applies the swap's resulting nodes/edges via
   the *raw* setters — deliberately, so the whole swap is one undo entry
   despite not using `setNodesAndEdges`.
5. **Config commit (`App.tsx`'s `onUpdateNode`) — compute, preflight, then
   commit.** Captures `prevNode` from `graphRef.current`, then **before any
   store mutation** computes the complete tentative result: the new config,
   `applyApiInputConfigChange`'s edge rebind/prune outcome (owned by
   `utils/apiInputPorts.ts`), and — for a frame rename — the migrated
   `input_scenario_map` keys and instance `inputMapping` entries on every
   affected node. The preflight then checks each affected target's
   post-commit input-name set for duplicates. On a collision the commit
   returns `{ ok: false, error }` and **nothing mutates** — no snapshot, no
   config, no edges, no mappings; `NodePanel` passes the result through
   `OnUpdateConfig` so the ApiInputEditor surfaces `error` inline at the
   label field (see
   [frontend-node-editors](../frontend-node-editors/low-level.md)), clearing
   it on the next successful commit. On success the whole tentative result —
   the migrated root nodes (config, ISM keys, instance mappings), edges, AND
   the migrated nested submodel metadata — lands through the single
   history-aware combined setter (`setNodesAndEdgesAndSubmodels`, one
   snapshot) plus the `selectedNode` update, as a single undo entry; undo
   restores root and nested state coherently, never a root-only revert.
6. **Store internals, history-aware path.** Every history-aware setter calls
   `pushSnapshotInternal()` (deep-clones the pre-mutation state — nodes,
   edges, preamble, and submodels — via `captureGraphSnapshot`, which
   delegates to the shared transient-stripping `cloneGraphSnapshot` in
   `graphSnapshot.ts` so React Flow presentation fields never enter
   history, evicting the oldest entry once
   `undoStack.length >= MAX_HISTORY`), then always recomputes
   `persistedFingerprint` and `dirty` (via `computeDirty` against
   `lastSavedSnapshot`/`savedPersistedFingerprint`), recomputes
   `panelContextFingerprint`/`Version` only if `computePanelContextFingerprint`
   (hashed over `PANEL_CONTEXT_NODE_DATA_KEYS`: label, description,
   nodeType, config, code, func_name, `_columns`, `_availableColumns`,
   `_schemaWarnings`) changed, and recomputes `structuralFingerprint`/
   `Version` only if `computeStructuralFingerprint` (sorted `id:hash` pairs
   via `shallowNodeDataHash`, plus sorted edge endpoint/handle keys, plus
   preamble) changed.
7. **Store internals, raw path (`setNodesRaw`/`setEdgesRaw`).** Three
   fast-path tiers, cheapest first: (a) `hasOnlyNodeUiFieldChanges` /
   `hasOnlyEdgeUiFieldChanges` (selection-only, comparing a fixed
   `REACT_FLOW_NODE_UI_FIELDS`/`REACT_FLOW_EDGE_UI_FIELDS` set) — returns
   just the updated array, nothing else recomputed; (b)
   `hasOnlyNodePositionChanges` — recomputes only `dirty` via
   `computeDirtyForPositionOnlyNodes` (position-vs-saved-position
   comparison), skipping fingerprint/panel-context work entirely; (c)
   otherwise, the full recompute path identical to step 6 minus the
   snapshot push.
8. **`undo()`/`redo()`.** Pop the relevant stack. If the popped entry
   `isVcEntry`, set `vcBusy: true`, optimistically move it to the opposite
   stack, run its async `undo()`/`redo()` closure, and on rejection restore
   it to its original stack (retry path) before clearing `vcBusy` in
   `.finally`. If it's a `GraphSnapshot`, synchronously swap
   `nodes`/`edges`/`preamble`/`submodels` to the target snapshot (a legacy
   snapshot without the `submodels` field restores an empty metadata map),
   recompute all three fingerprints and `dirty` from it, and push the
   *current* (pre-undo) state onto the opposite stack via
   `captureGraphSnapshot`.
9. **`markSaved(snapshot?)`.** Captures `lastSavedSnapshot` (defaults to the
   current state) and `savedPersistedFingerprint`, then recomputes `dirty`.
   This is the only place `savedPersistedFingerprint` is updated; it's
   called by the save flow (`usePipelineAPI`, out of scope) after a
   successful save.
10. **Panel context refresh.** `usePanelGraphContext` rebuilds its snapshot
    only when `panelContextVersion` changes (a `useMemo` keyed on it,
    reading fresh `nodes`/`edges` from `useGraphStore.getState()` at that
    moment). `App.tsx` passes this snapshot's `allNodes`/`edges`, plus the
    store-subscribed `submodels` and `preamble` values, into
    `<GraphProvider>` wrapping `NodePanel`.
11. **`PipelineNode` render.** Derives `nodeType`/`accent`/`Icon`/`typeLabel`
    from the shared `NODE_TYPES`/`nodeTypeColors`/`nodeTypeIcons`/
    `nodeTypeLabels` tables. One api-input frame list is computed, memoized
    on `config` identity: `frameLabels` via `apiInputFrameLabels(config)`
    (eligible frames, no minimum count) — it drives the handles AND the
    body rows, so they cannot diverge. Its collision-safe serialised
    signature (`JSON.stringify` — labels are validated identifiers now,
    but the serialisation stays collision-safe by construction, same
    rationale as `columnFingerprint`'s length-prefixing), the bucketed
    `zoomLevel`, and the live `edgeJoinJoinHandlePosition` drive a
    `useUpdateNodeInternals(id)` effect so React Flow re-measures handle
    positions whenever port topology changes or a zoom-threshold crossing
    relocates the handles between the container and the frame rows.
    `zoomLevel` comes from a `useStore` selector on the pane's zoom
    transform, bucketed into `full`/`medium`/`compact` so a zoom-threshold
    crossing — not every pixel of zoom — triggers a re-render. At full
    detail an api-input with ≥1 eligible frame renders the frame-row body:
    one relatively-positioned row per frame carrying a right-aligned
    truncating mono name (full name as `title` tooltip) and that row's
    labelled source `Handle` (id = the frame's raw label, a sole frame
    included), absolutely positioned at the row's vertical midline with
    its dot centred on the node's right border. The instance name is
    suppressed in that body; the trace-value pill, when active, renders
    above the rows. Zero eligible frames keeps the instance name, adds a
    muted "No emitted frames" line, and renders the legacy default null-id
    handle vertically centred. At medium/compact zoom no frame rows render
    and `_SourceHandles` supplies the same handle id set — labelled
    handles evenly spaced down the right edge, or the zero-frame default
    handle. Positional `output-connector[<idx>]:<node label>` test
    ids follow the visual top-to-bottom order in both modes, and the name
    span keeps its `api-input-body-label-<label>` test id. Edge-join nodes
    short-circuit to an entirely separate marker/pill render before the
    LOD branches run.
12. **Node delete (`useNodeHandlers.handleDeleteNode`).** Calls
    `setNodesAndEdges` once (node filter + edge filter closed over the same
    call, one undo entry), nulls `selectedNode`/`previewData` if they
    referenced the deleted node, and defers `clearNode(id)` via
    `setTimeout(..., 0)` so the cache eviction lands only after the
    node-removal render has committed. Also clears `renameDialog`/
    `submodelDialog` if either referenced the deleted node.
13. **Connection commit (`useEdgeHandlers.onConnectEnd`).** Reads
    `fromHandle`/`toHandle` types off the connection-end event to decide the
    shape of the gesture: source→source with a resolved target node inserts
    an edge-join via `insertEdgeJoinNodeFromSources`; source→target or
    target→source with a resolved target node calls `commitConnection`;
    source-with-no-target-node probes `findEdgeIdAtPoint` (a DOM hit-test
    via `document.elementsFromPoint`) and, if the drop landed on an edge,
    inserts an edge-join via `insertEdgeJoinNode`. `commitConnection`
    special-cases an edge-join *target*: it resolves the canonical
    base/join role for the target handle, rejects a second input to an
    already-filled role or a third input overall, stores the source node id
    into the edge-join's `config` under that role's key, and pushes one
    snapshot before applying nodes/edges via the raw setters (so the
    role-config write and the new edge land in the same undo entry as the
    edge itself). A successful edge-join insertion (either path) also
    selects the new node, clears trace, and cancels any in-flight preview.
14. **Palette drop (`useEdgeHandlers.onDrop`).** Parses the drag event's
    `application/reactflow-type` and `application/reactflow-config` payloads;
    a config JSON parse failure or a non-object payload toasts an error and
    creates nothing. On success, builds the node via `appNode`, selects it
    exclusively (`selectOnlyNode`), and sets it as the panel's selected
    node.
15. **Pipeline load (`usePipelineAPI`, mount effect).** Calls `loadPipeline`
    with a cold-start retry policy (`INITIAL_PIPELINE_RETRY_POLICY`, 6
    retries at 250ms base delay); the response is validated through
    `parsePipelineResponse` before touching the graph. On success, applies
    nodes/edges/preamble/submodels via the raw setters (`setSubmodelsRaw`
    hydrates the store-owned submodel metadata alongside the legacy
    `submodelsRef`), seeds `nodeIdCounter` from
    `computeNextNodeId`, and calls `useGraphStore.getState().markSaved()` —
    the just-loaded state IS the on-disk state, so it starts clean. Aborted
    via `AbortController` on unmount.
16. **Preview fetch (`usePipelineAPI.fetchPreview` →
    `fetchPreviewImmediate`).** `fetchPreview` cancels any in-flight
    request/debounce, paints cached data (or a `"loading"` placeholder)
    immediately, then debounces (`options.debounceMs ?? 200`) before calling
    `fetchPreviewImmediate`. That function snapshots `rowLimit`/
    `activeSource`/`streamingChunkSize` once, checks the node-results cache
    for a hit matching source+rowLimit; if the cached entry also matches the
    current `structuralVersion` it short-circuits with no network call,
    otherwise it shows the cached data while re-fetching in the background.
    `previewNode()` resolves into `resultToPreview`; if the response's
    columns differ from the node's previous columns
    (`columnsEqualByFingerprint`), `propagate(nodeId)` kicks off the
    downstream cascade. Every write of `_columns`/`_availableColumns`/
    `_schemaWarnings` onto node data — the direct-fetch path, the
    schema-map path (`applyPreviewSchemaMapsToNodes`), and the
    stale-upstream gap-fill in `refreshPreview` — also stamps
    `_columnsSource` with the `activeSource` snapshotted at that fetch's
    start, so the stash records which source it's actually valid for. A
    separate effect (`useEffect(() => setNodesRaw((nds) =>
    invalidateStaleColumnStashes(nds, activeSource)), [activeSource,
    setNodesRaw])`) runs on mount and on every `activeSource` change:
    `invalidateStaleColumnStashes` scans for any node with a column stash
    (`_columns`/`_availableColumns` defined) whose `_columnsSource` doesn't
    match the current `activeSource` — including a stash with no
    `_columnsSource` at all, treated as unknown provenance — and only calls
    `setNodesRaw` (a new array) if at least one node qualifies; a no-op
    scan returns the input array unchanged. A qualifying node has
    `_columns`/`_availableColumns`/`_schemaWarnings`/`_columnsSource`
    deleted from its data via destructuring, returning it to the
    pre-preview state.
17. **Downstream cascade (`propagate`, inside
    `fetchPreviewImmediate`).** BFS-reaches every node downstream of the
    changed node, tracks per-node pending-parent counts, and only enqueues a
    node once every parent that could change its columns has settled;
    `settleNode` recurses through unchanged nodes without previewing them.
    A bounded ready-queue (`drainReadyQueue`) runs at most
    `DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT` (4) previews concurrently. The
    whole cascade resolves once every reachable node has settled, the
    request is still current, or the shared `AbortController` fires.
18. **Save (`usePipelineAPI.handleSave`).** Refuses to run while drilled
    into a submodel. Runs `validateConfigRefs` (warns, does not block) and
    `findFirstInvalidEdgeJoin` (blocks with an error toast if invalid).
    Snapshots the exact graph/preamble/submodels that will be sent
    (`captureGraphSnapshot`, `structuredClone`) and stamps the attempt with
    `++saveRequestSeq.current`. On a successful `savePipeline()` response,
    calls `markSaved(savedSnapshot)` only if this request's id is still the
    newest applied (`saveRequestId > appliedSaveSeq.current`), then updates
    `useGitStore`'s last-save SHA and notifies its history-changed
    subscribers. Never throws; resolves `false` on any failure after
    toasting the detail.
19. **WebSocket sync (`useWebSocketSync`).** Connects to `/ws/sync` with the
    session token in the query string; on open, sends a `resync` message
    carrying the last-applied graph fingerprint for the current source file
    (server skips replying if it already matches). On `graph_update`: source
    file must match the currently open one (`isCurrentSourceFile`, tolerant
    of absolute-vs-relative paths); if the store is `dirty`, the update is
    blocked and a sync banner shown instead of applied. Otherwise it
    computes a layout (only if the incoming nodes carry no non-zero
    positions), sets a `graphRefreshingRef` guard (so React Flow's spurious
    `onSelectionChange` during the swap doesn't clear the open panel),
    applies via the raw setters, and calls `markSaved()`. A thrown error
    during apply rolls back to the pre-update `nodes`/`edges`/`preamble`
    (best-effort) before re-throwing into the outer catch, which toasts.
    Reconnection backs off exponentially (`INITIAL_BACKOFF_MS` doubling to
    `MAX_BACKOFF_MS`, capped at `MAX_RETRIES` = 50); a `1008` close with a
    session-expired reason stops retrying and calls
    `notifyHauteSessionExpired`; an abnormal (`1006`) close before the
    socket ever opened probes session validity before deciding whether to
    reconnect.
20. **Submodel drill-in (`useSubmodelNavigation.handleDrillIntoSubmodel`).**
    Loads the submodel's graph, snapshots the parent graph into
    `parentGraphRef` and the outgoing view-stack entry (`_savedNodes`/
    `_savedEdges`), then builds `SUBMODEL_PORT`-typed nodes: one input port
    per distinct parent source feeding the submodel placeholder (grouped by
    source, deduped by target child id via a `Set`, `in__`-prefixed handle
    stripped for the child id), and one output port per distinct parent
    target the placeholder feeds (grouped by target, `out__`-prefixed handle
    stripped, edges lacking a `sourceHandle` are skipped since they carry no
    child-id information). Lays the result out via ELK and re-fits after a
    short delay.
21. **Breadcrumb navigate (`handleBreadcrumbNavigate`).** Restores the
    exact `_savedNodes`/`_savedEdges` captured on the way in for the target
    depth (not a re-fetch); clears `parentGraphRef` only when returning all
    the way to depth 0.
22. **Comparison view mount (`ComparisonView`).** Freezes the current
    nodes/edges into local state once on mount (so the right canvas stays
    stable even if the live pipeline changes underneath). Fetches the
    historical pipeline via `getCommitPipeline(sha)`; once both sides are
    available, `diffPipelineNodes` runs once (`useMemo`) and `prepNodes`
    stamps each side's nodes with `data._diffStatus` for `PipelineNode` to
    render. Each `ReadonlyCanvas` seeds its own local React Flow state once
    from `initialNodes`/`initialEdges` (the parent remounts it via `key`
    when the inspected version changes, not an in-place reset) and mirrors
    the shared `selectedId` onto its own `selected` flags so both canvases
    highlight a clicked node's counterpart.

## Edge cases and invariants

- **API-input handle ids never synthesize.** Zero eligible frames → the
  legacy single unlabeled handle at index 0; one eligible frame or more →
  one labelled handle per frame, ids = the raw labels. A blank, duplicate,
  non-identifier, or keyword label renders **no handle** (not `port_<idx>`).
  Duplicate labels render **one** handle — the first occurrence only, never
  a disambiguated `label__<idx>`. (See `_SourceHandles` in `PipelineNode.tsx`
  and `frontend/src/__tests__/nodes/ApiInputHandles.test.tsx`.)
- **The api-input handle id set is zoom-invariant.** Full detail mounts the
  source handles on the body's frame rows; medium/compact space the same
  handles down the right edge. Only geometry moves across a zoom threshold
  (node height already changes per LOD today); ids never do, so edges stay
  bound.
- **The visible rows and the labelled handles are the same list.** Both
  read `apiInputFrameLabels`, so a config with two emit tables of which one
  has an invalid label renders exactly one row and one labelled handle (the
  valid one); a null-handle edge is orphaned under any config with ≥1
  eligible frame, exactly as `validSourceHandleKeys` rules. A row can never
  exist without its handle, nor a labelled handle without its row. The only
  configs with NO labelled handles are zero-eligible ones (nothing emitted,
  or every label invalid): the legacy default null-id handle renders and
  the body shows the zero-frame state — no bindable id is invented for a
  backend-invalid config.
- **A sole eligible frame is a labelled frame.** Its row carries a handle
  whose id is the frame label, its edges persist that label as
  `sourceHandle`/`source_port`, and its downstream argument is that label —
  identical to the multi-frame case. Transitions between zero and some
  eligible frames reconcile edges the same way emit-off/table-delete
  already do (migrate renames first, then prune with a warning toast).
- **`validSourceHandleKeys` for an apiInput never contains the empty key.**
  Null-handle API-input edges are always orphaned by reconciliation
  regardless of frame count — the zero-frame default handle is rendered
  non-connectable, so such an edge can only enter via a hand-edited file
  and is pruned (with the warning toast) on the next config commit; the
  backend's codegen `ParseError` for a port-less apiInput edge is the
  authoritative backstop at save.
- **An emit-true table with no selected column is not a frame**: no row, no
  handle, no display name — mirroring runtime eligibility
  (`_json_shred.load_v2_api_source`), so the body can never advertise a
  frame the executor would not emit.
- **Edge-join join-handle position is computed live**, not stored: it
  compares the connected join-source node's vertical centre against the
  marker's own centre (`state.nodeLookup`) and flips `Position.Top`/
  `Position.Bottom` as the user drags the source across the marker;
  `"both"` renders both handles before any join edge is connected.
- **Selection-only and position-only React Flow deltas skip
  fingerprint/dirty/panel-context recomputation** via the fast paths in step
  7 above — this is what keeps a 60fps drag cheap; a test in
  `useGraphStore.structuralVersion.test.ts` asserts hash-cache reuse for
  purely visual node-field churn.
- **Position-only churn after a save is still dirty** even though it never
  touches `structuralFingerprint` — layout position is part of the
  persisted document (`computeDirtyForPositionOnlyNodes` compares saved vs.
  current node positions independently of the structural hash).
- **Transient (`_`-prefixed) node fields never affect dirty, structural, or
  panel-context fingerprints** — preview/trace/status churn cannot mark the
  graph dirty or create undo noise (asserted in both
  `useGraphStore.structuralVersion.test.ts` and the "raw preview metadata…
  stay out of dirty state and undo history" case in
  `useGraphStore.consolidation.test.ts`).
- **`MAX_HISTORY = 100`.** The 101st push to either stack evicts the oldest
  entry; graph snapshots and VC entries share the same cap and eviction
  logic.
- **`undo()`/`redo()` are no-ops** on an empty stack, and also no-ops while
  `vcBusy` is true — this prevents overlapping VC operations from
  corrupting history ordering.
- **`setNodesAndEdges` captures exactly one pre-mutation snapshot** for a
  combined node+edge gesture. Calling `setNodes` then `setEdges` separately
  for one logical gesture pushes two snapshots and requires two undos to
  reverse — this is the "undo atomicity" bug class the combined setter
  exists to prevent, and it's guarded by
  `useGraphStore.undoAtomicity.test.ts`.
- **Deep-cloning on snapshot capture** (`cloneGraphValue`, WeakMap-tracked
  for cycles) protects history entries and `lastSavedSnapshot` from later
  in-place mutation by React Flow or editor code.
- **`useGraph()` distinguishes "no provider" (throws) from "provider with an
  empty graph" (returns cleanly)** — both are explicit test cases in
  `panels/__tests__/useGraph.gaps.test.ts`.
- **`GraphProvider` memoises on input identity**, so a parent re-render that
  doesn't change `{allNodes, edges, submodels, preamble}` by reference does
  not cascade into every descendant `useGraph()` consumer.
- **`App.tsx`'s `onUpdateNode` reads `prevNode` before calling `setNodes`**,
  so the api-input edge-reconciliation diff always compares against the true
  pre-commit config even though Zustand's `set()` is not synchronous from
  the caller's perspective in every batching scenario.
- **`lastSelectedId` can reference a deleted node.** `App.tsx` resolves the
  active panel node via `panelGraph.getNode(activePanelNodeId)`, which
  returns `null` — not `undefined` — for a stale id, and a dedicated effect
  clears `selectedNode`/`lastSelectedId`/preview state when the referenced
  node disappears from the graph (regression #38, covered in
  `App.findCast.test.tsx`).
- **`normalizeDefaultTargetHandle` collapses React Flow's synthetic
  `__default_target` handle id to `null`** before it reaches an `Edge` or a
  `maxInputs`/edge-join role check — a raw comparison against the sentinel
  string would treat "no explicit handle" and "explicitly this handle" as
  different, silently under- or over-counting inputs.
- **Edge-join role assignment allows exactly one base and one join input**,
  enforced in `commitConnection`: a target handle that doesn't resolve to a
  role is rejected outright, a role that already has an incoming edge is
  rejected, and a third incoming edge of any role is rejected — independent
  of the generic `maxInputs` check, which edge-join nodes bypass entirely.
- **`handleDuplicateNode` and singleton types.** Duplicating a node whose
  type is in `SINGLETON_TYPES` (API-input, output) is a silent no-op —
  there is no error path because the palette already prevents adding a
  second one; duplication is just another way to reach the same invariant.
- **`onDrop`'s config JSON never falls back to `{}` on a parse failure** — a
  malformed or non-object payload aborts node creation entirely (toast,
  return) rather than creating a node with an empty config that would then
  violate that node type's downstream invariants (Issue #35). An *absent*
  config payload (the empty-string default) legitimately becomes `{}`.
- **The preview cache key is (node id, structural version, source, row
  limit).** A hit on the first two but a mismatch on the join, but a
  mismatch on source or row limit alone re-fetches even though the cached
  data paints immediately in the meantime — never blocking the UI on a
  settings change.
- **`refreshPreview`'s stale-upstream detection only looks at direct
  parents lacking `_columns`, or whose `_columnsSource` no longer matches
  the active source**, not the full upstream closure — it lazily fills
  exactly the one hop needed to preview the target node, not a full
  re-preview of the pipeline. The source check exists because the
  invalidation effect (step 16) and this filter can observe the graph at
  slightly different times — the effect's `setNodesRaw` may not have
  flushed to the `nodeMap` this filter reads yet — so the filter re-checks
  `_columnsSource` directly rather than assuming the effect has already
  stripped every stale stash.
- **`clusterSnap`/`alignPositions` (layout.ts) snap coordinates within a
  20px threshold to their cluster median**, so ELK's near-but-not-exact
  layer alignment renders as visually exact rows/columns; the ELK engine
  itself is lazily imported once and cached in a module-level promise
  (`elkPromise`), never re-imported across calls.
- **`ComparisonView`'s diff `moved` status is mutually exclusive with
  `changed`** — `diffPipelineNodes` only checks position when content is
  unchanged, so a node that both moved and changed content is reported only
  as `changed` (the content change is the headline).
- **`ComparisonView` freezes the current graph exactly once, on mount**
  (`useState(() => ({...}))` initializer, not a prop mirror) — a live
  pipeline edit or WebSocket sync while comparing does not perturb the
  right canvas, the diff, or the legend; a fresh comparison is a remount
  (keyed by `comparison.sha` at the call site), not an in-place update.
- **`RenameDialog` renders a single-line `<textarea>`, not
  `<input type="text">`**, specifically so a pasted or injected newline is
  visible to validation — `HTMLInputElement` silently strips newlines
  before JavaScript ever sees them, which would let one slip past the
  unsafe-character check.

## Error handling

- `useGraph()` throws a plain `Error` (`"useGraph() was called outside of a <GraphProvider>. …"`)
  when `useContext(GraphContext)` is `undefined`. It is not caught locally;
  it propagates to the nearest `ErrorBoundary` (`name="NodePanel"` in
  `App.tsx`).
- A rejected VC entry `undo()`/`redo()` promise is caught inside the store's
  `undo()`/`redo()` implementation: the entry is pushed back onto its
  original stack (removed from the opposite one) so the user can retry, and
  `vcBusy` is cleared in a `.finally` regardless of outcome. The store does
  not itself raise a toast — callers of `pushVcEntry` own user-facing error
  messaging.
- `App.tsx`'s `handleSwapEdgeJoinInputs` treats a failed swap
  (`swapEdgeJoinInputs` returning `{ ok: false, reason }`) as an expected
  outcome, not an exception: it looks up a static
  `edgeJoinSwapFailureMessages` map by `reason` and raises an `error` toast,
  making no store mutation.
- `App.tsx`'s `onUpdateNode` surfaces api-input edge pruning via a `warning`
  toast naming the disconnected frame(s) and edge count — never a silent
  drop.
- No explicit try/catch wraps deep-cloning (`cloneGraphValue`) or
  fingerprint computation; an exception there propagates out of the
  triggering `set()` call and is caught only by whichever `ErrorBoundary`
  wraps the component that triggered the mutation (typically `"Canvas"` or
  `"NodePanel"` in `App.tsx`).
- `useNodeHandlers`/`useEdgeHandlers` surface user-actionable failures as
  named toasts and otherwise no-op: edge-join role/max-input/self-loop/
  duplicate rejections, malformed drag JSON (Issue #35), and connection-end
  events missing the coordinates needed to resolve a drop point (touch
  events with neither `touches` nor `changedTouches`) throw a plain `Error`
  from `connectionEndPoint` rather than silently treating the drop as a
  no-op — a genuinely impossible browser event, not a user-input case.
- `usePipelineAPI`'s initial load `.catch` distinguishes an
  unmount-triggered `AbortError` (silently ignored) from every other
  failure (including a `parsePipelineResponse` contract violation), which
  toasts `Failed to load pipeline: …` and clears `loading`.
- `usePipelineAPI.fetchPreviewImmediate`/`refreshPreview`/`previewNodeFrame`
  each distinguish three outcomes on preview failure: an abort or
  supersession (`isAbortError`/`isPreviewSupersededError`) is silent
  cancellation; an `ApiTimeoutError` additionally toasts `error`; anything
  else paints an error `PreviewData` and — for cascade/upstream members —
  toasts a `warning` naming the failing node, without aborting siblings.
- `usePipelineAPI.handleSave` never throws out of the hook: `ApiError`
  detail is preferred when present, else the exception's `message`, else a
  literal `"unknown error"`; every branch resolves `false` after toasting.
- `useWebSocketSync` toasts on: WebSocket construction failure, unparsable
  message JSON, and any exception raised while applying a `graph_update` —
  the last case also attempts a best-effort rollback to the pre-update
  snapshot (itself wrapped in a try/catch that swallows a rollback failure
  so it never masks the original error in the toast). A `1008` close code
  carrying a recognised session-expiry reason short-circuits reconnection
  and calls `notifyHauteSessionExpired` instead of toasting.
- `useSubmodelNavigation`'s three async handlers each wrap their API call
  in try/catch, toasting `error` with the caught message (or `String(err)`
  for a non-`Error` throw) and leaving the graph untouched on failure —
  mutation only happens inside the success branch.
- `ComparisonView`'s historical-pipeline fetch failure is caught at the
  effect level and stored in `error` state, rendering a dedicated error
  screen instead of an uncaught rejection; the breadcrumb-context fetches
  (`getCommitContext`) are explicitly best-effort — a rejection there is
  swallowed (`.catch(() => {})`) and just leaves the fallback label, since
  losing the breadcrumb is cosmetic, not blocking.

## Testing

The pure connection/frame helpers are defended by `frontend/src/utils/__tests__/apiInputPorts.test.ts`, `edgeJoinGraph.test.ts`, and `edgeJoinValidation.test.ts`: they cover raw-label frame eligibility, blank/duplicate/non-identifier/keyword label rejection (mirroring backend invariant B4's ASCII rule — valid Unicode identifiers like `café` are rejected too), frame-label derivation (`apiInputFrameLabels` across zero/one/many eligible frames, invalid labels, and unselected-column tables), edge input-name derivation (`edgeInputName` for api-input frame edges verbatim, sanitised source labels for ordinary nodes, submodel `out__` edges resolving to the child's sanitised label, and per-target duplicate detection), rename-before-prune migration, identity-preserving no-ops, edge-join insertion/role normalisation, and invalid saved graph diagnostics. These sit alongside the hook/store suites below because their contracts are exercised again through the editor and save paths.

- **Store — `frontend/src/stores/__tests__/`:**
  - `useGraphStore.consolidation.test.ts` — store shape and required
    action surface; a "selector isolation (reviewer gate)" block asserting
    by render count that subscribing to one slice does not re-render on
    unrelated slice changes; undo/redo push/pop/no-op semantics for
    history-aware vs. raw actions; `MAX_HISTORY` eviction; `isDirty()` as a
    pure, render-stable selector across save/edit/undo cycles; a regression
    block confirming `useUIStore` no longer exposes `dirty`/`setDirty` and
    doesn't duplicate graph-shaped state.
  - `useGraphStore.fieldEditUndo.test.tsx` — a whole inline field edit is
    one undo step regardless of edit size; a no-op edit pushes nothing; a
    guard test documents what the pre-fix per-keystroke wiring would have
    done.
  - `useGraphStore.structuralVersion.test.ts` — the full bump/no-bump
    matrix: position/selection/preview-only node changes never bump
    `structuralVersion`; preview-only changes bump `panelContextVersion`
    but not `structuralVersion`; underscore-prefixed metadata stays out of
    persisted fingerprints; visual-only raw changes reuse cached node
    hashes; Explore overview-card config is ignored while Explore
    data-prep code still flips the hash; add/remove/rewire/reconfigure and
    preamble edits do bump `structuralVersion`.
  - `useGraphStore.undoAtomicity.test.ts` — single- and multi-node delete
    (mixing connected/unconnected nodes), pure-edge delete, and paste are
    each exactly one undo entry regardless of selection size; redo replays
    the combined gesture in one step; a store-level contract test asserts
    `setNodesAndEdges` pushes exactly one snapshot per call.
  - `useGraphStore.vcHistory.test.ts` — `pushVcEntry` appends and clears
    redo; `MAX_HISTORY` eviction applies to VC entries too; VC undo/redo
    lock history (`vcBusy`) while the async leg runs; a failed leg restores
    the entry to its original stack for retry; VC entries interleaved with
    graph snapshots undo/redo in the correct order.
- **Context — `frontend/src/panels/__tests__/`:**
  - `useGraph.gaps.test.ts` — throws a clear `GraphProvider`-naming error
    outside a provider; returns the exact supplied value inside one; an
    empty-graph provider is distinct from no provider; the context sentinel
    is a real, usable `React.Context`.
  - `NodePanel.graphContext.test.tsx` — DOM-level regression that
    `NodePanel` and nested editors (`DataOutputEditor`, `ModellingConfig`,
    `OptimiserConfig`) consume the graph purely via `useGraph()`, including
    a structural assertion that `DataOutputEditor`'s prop type no longer declares
    `allNodes`/`edges`/`submodels`/`preamble`; and the #84 fail-loud
    behaviour for a missing `instanceOf` reference (diagnostic naming the
    missing id, never a silently stringified fallback label).
- **Nodes:**
  - `frontend/src/nodes/__tests__/PipelineNode.test.tsx` (largest suite) —
    per-node-type rendering; source/target handle presence for source-only
    and sink-only types; edge-join marker geometry including join-handle
    top/bottom/both flipping as the join source moves across the marker;
    diff ring and moved-outline rendering; instance/LIVE/API badges;
    status dot (ok/error/running-pulse) and warning-dot behaviour
    (including error-suppresses-warning); trace dim/hover-dim/motion-disabled
    states; aria-label composition (type, label, status, instance, trace);
    the api-input frame-row body (instance-name suppression with ≥1
    visible frame, the single-frame name row, the zero-frame name +
    "No emitted frames" state, row/handle pairing and ordering, status/
    warning/trace adornments coexisting with rows, and medium/compact
    bodies unchanged).
  - `frontend/src/__tests__/nodes/SubmodelNode.test.tsx` — label/badge/
    child-count rendering; per-port input and output handle rendering and
    vertical positioning; file-path display and truncation; dashed-vs-solid
    border by selected/`_traceActive`; dim and motion-disabled states.
  - `frontend/src/__tests__/nodes/SubmodelPortNode.test.tsx` — input-vs-
    output handle placement (source-right for input, target-left for
    output); label fallback to `label` when `portName` is empty; trace
    border/glow/dim/motion states; graceful rendering with a missing
    `portDirection`.
  - `frontend/src/__tests__/nodes/ApiInputHandles.test.tsx` — one labelled
    source handle per eligible frame from one frame up, ids matching the
    raw labels (the sole-frame labelled handle explicitly pinned);
    default-handle fallback only for zero eligible frames (no eligible
    tables, a missing `tables` key, or an all-invalid label set); the W1.4
    guarantees that blank/duplicate/non-identifier labels render no
    handle; row-mounted full-detail handles carrying the same ids as the
    medium/compact fallback (zoom-invariant id set).
- **App / integration — `frontend/src/__tests__/`:**
  - `App.integration.test.tsx` — mount and initial load sequencing (no
    WebSocket sync while the initial load is pending); empty-pipeline
    rendering; loading a 3-node graph and selecting an Explore node to
    preview its post-code dataframe; drag-and-drop node creation from the
    palette; Save and Save-&-Commit, including the working-branch/
    divergence modal gate; error-toast handling for failed load/save; the
    api-input emit-port edge reconciliation suite (Defect 1: emit-off
    prunes with a warning; W1.3: renaming a connected port rebinds its edge
    in one undo entry; W1.4: a blanked port label never reaches the graph;
    editing a non-port field never prunes a valid edge); panel
    open/close mutual exclusivity (Utility/Imports/Git/branch indicator).
  - `App.connectionMode.test.ts` — `ConnectionMode.Loose` is enabled and a
    graph-level `isValidConnection` validator is wired to `<ReactFlow>`.
  - `App.findCast.test.tsx` — regression #38 (a `lastSelectedId` pointing
    at a deleted node resolves to `null`, never `undefined`, and the panel
    shows its empty state rather than a broken reference); `GraphProvider`
    array identity stability across position-only re-renders vs. refresh on
    a `structuralVersion` bump; the `graph-effects-lite` canvas class
    activates only once node/edge count crosses the shared threshold.
  - `App.shallowHash.test.ts` — unit and benchmark coverage for
    `shallowNodeDataHash`/`graphFingerprintShallow`, the primitives behind
    `structuralFingerprint`: input-identity invariants (result-only fields
    don't flip the hash), input-key sensitivity (every structural field
    does), and benchmark assertions that the shallow hash stays bounded
    relative to a full-stringify baseline.
  - `App.backgroundJobsIsolation.test.tsx` — the background-job store
    subscription that feeds toolbar counts does not re-render the editor
    toolbar on solve/train progress ticks.
- **Node/edge handlers — `frontend/src/hooks/__tests__/`:**
  - `useNodeHandlers.test.ts` — delete-as-one-atomic-step (node + connected
    edges); selected-node preservation vs clearing on delete; duplicate
    (offset position, no-op for singleton/output types, no-op for a
    missing node); create-instance (`config.instanceOf`, toast);
    auto-layout (applies + toasts, exposes pending state, ignores
    overlapping clicks, resets pending state after a layout failure,
    no-ops on an empty graph).
  - `useNodeHandlers.gaps.test.ts` — `handleRenameNode` (opens dialog with
    correct id/label, no-ops for a missing node, coerces a non-string
    label); `lastSelectedNodeRef` clearing scoped to the deleted node only;
    `renameDialog`/`submodelDialog` cleared only when they reference the
    deleted node, including a null-dialog no-op case.
  - `useNodeHandlers.deferCleanup.test.ts` (#32) — `setNodes` commits
    synchronously before `clearNode`; `clearNode` is deferred past the
    current microtask; multiple rapid deletes each defer their own
    `clearNode` independently (no missed nodes).
  - `useEdgeHandlers.test.ts` (largest suite) — `onConnect` is a no-op
    (commit happens in `onConnectEnd`); source→target and target→source
    edge creation; self-loop and duplicate-edge rejection; submodel
    targetHandle preservation; `maxInputs` blocking (including a
    second Explore input) and non-blocking when unset; default-handle
    normalisation; source-to-source edge-join creation and its rejection
    when invalid; edge-join base/join role assignment, role-occupied and
    third-input rejection; edge-drop edge-join insertion and its
    ignore-if-no-edge-under-pointer case; touch-event coordinate
    resolution via `changedTouches`; selection-change drag-safety and
    `graphRefreshingRef`-guarded deselection skip; node-click panel-open +
    preview fetch (including Optimiser debounce, modelling/explore
    preview, submodel-port preview skip, same-node re-click skip, rapid
    distinct selections); edge deletion; context menu singleton/submodel
    flags; drag-over/drop node creation from palette metadata.
  - `useEdgeHandlers` (same file) "edge-join failures and multi-port
    handles" block — connection endings with no source node; a touch
    ending with no pointer coordinates fails loudly; third-input rejection
    against legacy (non-role) edges; role-config seeding on a config-less
    edge-join node; self-join rejection (dropping a node's own output onto
    its own outgoing edge, and joining a node's two outputs together);
    cycle rejection; a source-to-source join from a node no longer in the
    graph; null-source-handle storage when joining two default outputs;
    reverse-drag normalisation between default handles; edge-drop
    edge-join insertion from a default source handle; edge hit-testing
    consultation and its "missing hit-tester" fallback.
  - `useEdgeHandlers.dragJson.test.ts` (#35) — malformed drag-config JSON
    produces a visible error and never silently creates an empty-config
    node; well-formed JSON, the empty-string default, and non-object JSON
    (bare array/string) are each exercised explicitly.
- **Pipeline API — `frontend/src/hooks/__tests__/`:**
  - `usePipelineAPI.test.ts` — initial load (cold-start retry policy,
    unmount abort, AbortError suppression, nullable-metadata tolerance,
    load-failure toast); save (success toast, dirty-during-in-flight-save
    survives, stale-response never overwrites a newer saved baseline,
    blocked while drilled into a submodel, error toast including
    `ApiError` detail); sources loading; preview fetch/status/schema
    propagation into nodes via the raw setter; requested-preview-column
    capping for wide cached schemas; client-side preview timeout surfaced
    in panel and toast; `nodeIdCounter` seeded from max numeric suffix
    (not `nodes.length`).
  - `usePipelineAPI.gaps.test.ts` — preview error/abort/supersession
    distinctions; concurrent saves complete independently (no dedup) and a
    second save's failure doesn't corrupt the first's saved snapshot;
    preview caching (fresh-cache skip, custom debounce, structuralVersion/
    source/rowLimit staleness triggers a refetch); terminal-state
    guarantee after a mid-flight structuralVersion bump; debounce/abort
    interplay between `cancelPreview` and a new `fetchPreview`.
  - `usePipelineAPI.abortStale.test.ts` (#31) — switching the selected
    node while a preview is aborted clears the prior node's preview data
    and the aborted fetch never re-paints onto the new node's panel.
  - `usePipelineAPI.propagation.test.ts` (Phase 2D-5, largest cascade
    suite) — linear-order cascading; source/rowLimit captured at cascade
    start surviving a mid-flight store flip; halting at an unchanged
    downstream node; no duplicate downstream work from an overlapping
    second `fetchPreview`; direct-children fan-out; concurrency cap under
    wide fan-out; stale-supersession toast suppression vs genuine-conflict
    warnings; diamond-shaped dedup (shared child previews once, waits for
    the slower branch); no-op when the previewed node has no downstream
    edges; one downstream rejection does not abort sibling previews.
  - `usePipelineAPI.previewLifecycle.test.ts` (W0) — a preview response or
    failure arriving after a mid-flight structuralVersion bump still
    reaches a terminal panel state; a node deleted mid-flight is never
    resurrected into the panel or cache.
  - `usePipelineAPI.refPattern.test.ts` (#33/#34) — a single
    `activeSource` snapshot spans a fetch and its downstream cascade;
    `handleSave` reads `activeSource` at invocation time, not a stale
    closure; a `rowLimit` change mid-fetch does not affect the
    already-running preview.
  - `columnStashSourceIdentity.test.ts` (key-contract pin) — a captured
    stash is stamped with the source the preview ran under; switching the
    active source invalidates a stash tagged with a different source
    (clearing `_columns`/`_availableColumns`/`_schemaWarnings`/
    `_columnsSource` together) and treats an untagged stash the same way,
    but a stash already tagged with the current source survives a
    same-source re-set (no gratuitous invalidation); `refreshPreview`
    re-previews an upstream node whose stash was captured under a
    different source rather than treating it as already fresh.
- **WebSocket sync — `frontend/src/hooks/__tests__/`:**
  - `useWebSocketSync.panelState.test.ts` (#39) — `renameDialog`/
    `submodelDialog` referencing a node removed by a WS sync are cleared;
    left alone when the referenced node(s) survive; null dialogs stay
    null.
  - `useWebSocketSync.failLoudly.test.ts` (#37) — a `getLayoutedElements`
    throw restores `graphRefreshingRef` and toasts an error without
    calling both raw setters with partial data; a setter failure rolls
    back nodes/edges/preamble to the previous snapshot without marking the
    failed graph saved; a subsequent `graph_update` after a failed one is
    still handled cleanly.
  - `useWebSocketSync.undoHistory.test.ts` (#8) — `graph_update` only ever
    calls the history-bypassing raw setters, never the history-aware ones,
    including under concurrent messages, so external sync never pollutes
    undo/redo.
- **Submodel navigation — `frontend/src/hooks/__tests__/`:**
  - `useSubmodelNavigation.test.ts` — initial pipeline-level view stack;
    create (API call + node update, error toast on failure); drill-in
    (loads submodel, pushes view stack, updates the source-file ref, error
    toast on failure); breadcrumb navigate (restores saved nodes at the
    target depth, restores/clears the parent source file, no-ops when the
    target depth isn't strictly above the top); dissolve (API call + node
    update, error toast on failure).
  - `useSubmodelNavigation.gaps.test.ts` — input-port node+edge synthesis
    from a parent cross-boundary edge; label fallback to source id when
    `targetHandle` is missing and the child is absent; output-port
    synthesis; output edges skipped when their child isn't part of the
    submodel graph or lack a `sourceHandle`; `parentGraphRef` cleared only
    when breadcrumb-navigating to depth 0; drill-in no-ops when the API
    returns no graph.
- **Utils — `frontend/src/utils/__tests__/`:**
  - `buildGraph.test.ts` — payload serialisation (zeroed position,
    `type`/`data.nodeType` fallback precedence, submodels/preamble
    pass-through and their `undefined` default); `resolveGraphFromRefs`
    (parent-graph priority, fallback to `graphRef`, `preambleRef` always
    wins regardless of which graph is active).
  - `graphDiff.test.ts` — added/removed/changed classification; moved-only
    vs changed mutual exclusivity; derived `contract` key ignored (and
    still detects a genuine change alongside an added `contract`); config
    key-order canonicalisation; a combined add+remove+change scenario.
  - `graphHelpers.test.ts` — `computeNextNodeId` (empty array, single/
    multiple suffixes, non-numeric-suffix nodes ignored, single- and
    multi-digit and zero suffixes); `normalizeEdges` (empty input, type/
    animated normalisation, other fields preserved, no source-array
    mutation, multiple edges).
  - `graphPerformance.test.ts` — below/at-threshold behaviour for the
    shared lite-effects size limit.
  - `makePreviewData.test.ts` — defaults, overrides, nodeId/nodeLabel
    preserved under overrides, loading status.
  - `columnFingerprint.test.ts` — equivalent-schema equality; sensitivity
    to order/name/dtype changes; undefined/empty/non-empty distinctness;
    collision-safety against separator-like characters embedded in names
    or dtypes.
  - `activePreview.test.ts` — matching-node passthrough; stale
    previously-selected-node data hidden; null when there's no active node
    or preview.
  - `validateConfigRefs.test.ts` — clean graphs; valid `data_input`
    reference; stale `data_input`/`banding_source`/`instanceOf` detection;
    multi-node broken-ref aggregation; empty-string and non-string refs
    ignored; submodel-exported target resolution in both metadata shapes
    (nested `{graph:{nodes}}` and direct `{nodes}`); a target absent from
    both the graph and submodels still warns, including with other
    submodels present; backward-compatible no-submodels-argument call;
    malformed submodel metadata tolerated without crashing;
    `formatConfigRefWarnings` singular/plural/empty formatting.
  - `layout.test.ts` — empty input; single-node non-zero position;
    distinct positions for connected nodes; data preserved through
    layout; a 3-node linear chain; cluster-snapping near-equal
    y-coordinates; disconnected nodes still positioned; zero-default when
    ELK omits coordinates; fan-out nodes sharing a snapped x coordinate.
  - `connectionValidation.test.ts` — edge-join output-to-default-input
    allowed; self-loops rejected; incomplete connections rejected.
  - `flowElements.test.ts` — node creation from type metadata defaults;
    metadata-name-based label generation; deterministic edge ids and
    normalised handle fields; non-mutating deselect/select-only-node
    helpers.
  - `nodeTypes.test.ts` — every `NODE_TYPES` value present (exact count);
    `NODE_TYPE_META` completeness and 1:1 coverage; Explore's one-input
    sink shape; Edge Join's compact centre-origin shape; label/name casing
    convention; `SINGLETON_TYPES`/`SOURCE_ONLY_TYPES`/`SINK_ONLY_TYPES`
    membership and exact counts; `isSingletonType` true/false/undefined
    cases; `PALETTE_TYPES` validity, submodel/edgeJoin exclusion, explore
    inclusion and ordering, no duplicates; every derived lookup
    (`nodeTypeIcons`/`Colors`/`Labels`) covers every node type.
  - **Gap:** `flowHandles.ts` has no dedicated test file — its one
    function, `normalizeDefaultTargetHandle`, is exercised only
    indirectly through `useEdgeHandlers.test.ts`'s handle-normalisation
    cases and `nodes/__tests__/PipelineNode.test.tsx`'s handle-rendering
    cases, so a regression isolated to the sentinel-collapsing logic
    itself would surface only as a broader failure in one of those suites.
- **Components — `frontend/src/components/__tests__/`:**
  - `ComparisonView.test.tsx` — loading-then-both-canvases sequencing;
    floating chip label/sha and bail-out; removed/changed ringed on the
    left, added/changed on the right; moved-only nodes marked on both
    canvases; right-button pan enabled (mirrors the main canvas); pane
    re-fit once it reaches real size (and not before, avoiding the
    vertical-split fit race); orientation toggle both directions; the
    historic↔current delta's placement differs by orientation
    (side-vertical vs bottom-left-stacked); divider drag-resize and
    double-click reset; blank-canvas deselection with notification;
    no-differences legend text; a clicked node's resolved-both-sides
    inspect payload; counterpart highlighting across both canvases; the
    live editor's `selected`/`dragging` UI state never leaks onto a
    comparison canvas.
  - `ComparisonInspector.test.tsx` — label + status badge; the real editor
    rendered `inert` with the current-side config by default; the
    Historical/Current switcher for a node present on both sides; the
    absent side greyed out (not hidden) for an added or a removed node,
    each falling back to showing the side that does exist; `onClose` wired
    to the panel header.
  - `RenameDialog.test.tsx` — title; accessible dialog role/label;
    default-value population; auto-focus on mount; cancel via button,
    backdrop click, and Escape (with in-dialog clicks NOT closing it);
    trimmed-value submission on both button and Enter; empty and
    whitespace-only submissions rejected.
  - `RenameDialog.validation.test.tsx` (#36) — empty/whitespace/
    over-200-char rejection and exactly-200-char acceptance; newline and
    backtick rejection (would corrupt code-gen/markdown); control-character
    rejection; spaces/dashes/underscores/digits/mixed-case and non-ASCII
    unicode letters accepted; the input is a controlled value reflecting
    every keystroke; submission trims leading/trailing whitespace.
  - `SubmodelDialog.test.tsx` — node-count text; cancel via button and
    backdrop click; empty-name submission rejected; trimmed-name
    submission calls `onSubmit`; Escape closes and is cleaned up on
    unmount; non-Escape keys are inert.
  - `PolarsIcon.test.tsx` — default-prop SVG rendering; custom size/color.
- **Strategy.** Predominantly unit and React Testing Library component
  tests, with a deliberate render-count "reviewer gate" for the store's
  selector-isolation contract, several regression tests named after
  historical issue/work-item numbers (#8, #31, #32, #33/#34, #35, #36,
  #37, #38, #39, #84, W0, W1.3, W1.4, Phase 2D-5), and benchmark-style
  assertions bounding the shallow-hash cost.
- **Known gaps.** `hooks/useGraphCanvasState.ts`'s `onNodesChange`/
  `onEdgesChange` drag-start-vs-mid-drag snapshot logic has no dedicated
  unit test file; it is exercised only indirectly through
  `App.integration.test.tsx` and the store-level tests it delegates to, so
  a regression isolated to that adapter's push/no-push branching would
  surface only as a broader integration-test failure, not a targeted one.
  `utils/flowHandles.ts` has the same shape of gap — see above.
- **`frontend/e2e/persistence/api-input-frame-alignment.spec.ts`** — the
  Playwright geometry evidence for the frame-row body: for one, two,
  three, and eight emitted frames, each frame row's bounding-box vertical
  centre coincides with its output handle's centre within ≤3 CSS px —
  asserted plain, with status and warning dots present, with a
  trace-active value pill, and with a ≥40-character truncating label in
  the row set; edges carry
  the correct labelled `sourceHandle` after render, save/reload, and an
  in-place frame rename — including a sole-frame source, whose edge
  persists the frame label and whose generated `main.py` names it in both
  `source_port` and the downstream signature; and a downstream node's
  input chips name the connected frames with the exact argument names.
  The DOM-only component suites above assert handle count/ids/order; only
  this suite proves the rendered geometry aligns.
- **`frontend/e2e/large-graph-drag.benchmark.spec.ts`** — a Playwright
  benchmark (tagged `@benchmark`, run via `npm run test:e2e:benchmark`, not
  the default e2e lane) that builds a 1000-node graph and drags a node across
  80 pointer-move steps, asserting frame-time and input-latency p95 budgets.
  This is the only real-browser performance coverage of canvas drag
  responsiveness; the unit/component tests above cover correctness, not
  frame timing under load.

## Approved change contract — 0.7.0 canonical data-I/O canvas nodes

The implementation plan is
[`F_0.7.0_data-io-convergence.plan.md`](../../trip/plans/F_0.7.0_data-io-convergence.plan.md).

- Remove `DATA_SOURCE`/`DATA_SINK` from `utils/nodeTypes.ts`,
  `utils/nodeTypeRegistry.ts`, `SOURCE_ONLY_TYPES`, `SINK_ONLY_TYPES`, `PALETTE_TYPES`, icons,
  labels, search metadata, `ReadOnlyNodeConfig.tsx`, and click/preview exclusions. Retain
  `DATA_INPUT` in the source-only set and `DATA_OUTPUT` in the sink-only set; do not add either to
  `SINGLETON_TYPES`.
- Make the retained metadata defaults branch-shaped and consistent with the shared frontend
  config types. `flowElements` copies that shape without adding inactive keys. A Data Output
  click may request a safe pass-through preview; the explicit writer remains editor/API-owned.
- Strict graph response guards reject removed node strings before `buildGraph`, WebSocket apply,
  comparison construction, or store mutation. No fallback maps them to retained types.
- Update exact-count/completeness, factory, handles, palette/search, click preview, graph
  fingerprint, load/save, comparison, and WebSocket tests. Browser coverage creates multiple
  retained I/O nodes and proves a rejected legacy payload leaves the prior/blank graph intact.
