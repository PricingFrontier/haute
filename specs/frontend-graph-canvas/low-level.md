# Frontend Graph Canvas — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/App.tsx` | `FlowEditor` — the canvas orchestrator: wires `<ReactFlow>` event props to interaction hooks, derives a transient highlighted edge from the active edge-join insertion candidate, renders its accessible status, owns local selection/context-menu/dialog state, picks the active preview pane, handles `onUpdateNode` (including api-input edge reconciliation), and gates Save/Save-&-Commit on git working-branch status. Exports `App`, which mounts `FlowEditor` inside `ReactFlowProvider`. |
| `frontend/src/nodes/PipelineNode.tsx` | Renders every non-submodel node type across three zoom LODs and the edge-join marker variant; computes source/target `Handle` sets, including multi-frame api-input handles (row-mounted through the shared `FramePortRows` component on the full-detail body, evenly spaced at medium/compact) and edge-join geometry-dependent handle placement; owns api-input instance-name suppression and the zero-frame "No emitted frames" state. |
| `frontend/src/nodes/FramePortRows.tsx` | Shared full-detail frame-row primitive used by API Input, the parent Submodel card, and drilled Input/Output boundary cards. It owns the common semibold 13px label typography, truncation/title behavior, and row-relative source/target handle placement. |
| `frontend/src/nodes/SubmodelNode.tsx` | Resolves occurrences through `config.definitionId` and the typed definition registry, then renders labelled `in__<portId>`/`out__<portId>` rows, a registry-derived accessible child count, and a visible invalid-definition alert. Cards have no default target. |
| `frontend/src/nodes/SubmodelPortNode.tsx` | Renders one composite Input or Output boundary card inside a drilled submodel. Both headers use the same right-pointing arrow while their handles retain their graph semantics. Input turns its ordered `ports` into shared source-handle rows; Output renders one shared target handle and never lists exported frames. |
| `frontend/src/panels/useGraph.ts` | Defines `GraphContext` (`React.Context<GraphContextValue \| undefined>`) and the `useGraph()` consumer hook, which throws when called outside a provider. |
| `frontend/src/panels/GraphContext.tsx` | `GraphProvider` component; memoises the context value on `{allNodes, edges, submodels, preamble}` identity. |
| `frontend/src/stores/useGraphStore.ts` | Zustand store owning `nodes`/`edges`/`preamble`/`submodels`, undo/redo history (four-field graph snapshots interleaved with VC entries), and three derived fingerprints (`structuralFingerprint`, `panelContextFingerprint`, `persistedFingerprint`) plus the `dirty` boolean derived from them. |
| `frontend/src/types/node.ts` | Shared node-data and persisted node-type contract owned by [frontend-shared](../frontend-shared/low-level.md) and consumed by the canvas. |
| `frontend/src/hooks/useNodeHandlers.ts` | Node CRUD handlers: ordinary atomic delete, guarded submodel deletion, duplicate, ordinary `instanceOf` creation, reusable-submodel occurrence creation with deterministic fresh id/alias allocation, rename dialog, and in-flight-guarded ELK auto-layout. |
| `frontend/src/hooks/useEdgeHandlers.ts` | Connection/gesture handlers: `onConnectStart` plus pointer movement maintain the transient compatible edge-join candidate; `commitConnection`/`onConnectEnd` interpret React Flow handle-drag endings into a normal edge or a revalidated edge-join insertion and always clear gesture feedback; the hook also owns `onSelectionChange`/`onNodeClick` (panel + debounced preview, gated while a structured API-input has no `tables[]` schema), `handleDeleteEdge`, `onNodeContextMenu`, and `onDragOver`/`onDrop` (palette node creation). |
| `frontend/src/hooks/usePipelineAPI.ts` | Pipeline load-on-mount; request-facing source-revision and preserved-block refs; debounced, cache-first, concurrency-limited-cascade preview fetching (`fetchPreview`/`fetchPreviewImmediate`/`refreshPreview`/`previewNodeFrame`), where every network preview path first awaits `ensureInputSnapshots` for the graph's snapshot-backed Data Inputs; an active-source-change effect (`invalidateStaleColumnStashes`) that strips any node's column stash tagged with a different (or no) `_columnsSource`; and `handleSave` (config-ref/edge-join pre-save validation, snapshot-scoped save-concurrency guard, `markSaved`, committed-revision update). |
| `frontend/src/hooks/ensureInputSnapshots.ts` | Pre-preview snapshot orchestration owned behaviourally by [caching](../caching/high-level.md): derives the graph's snapshot-backed Data Inputs (direct Parquet skipped), checks status, starts or joins builds (lazy-sink first, one admitted-eager retry on `snapshot_build_unsupported`), polls jobs to a terminal state with abort support, and notifies at most once when a build starts. |
| `frontend/src/hooks/useWebSocketSync.ts` | The `/ws/sync` WebSocket client: connect/reconnect with exponential backoff, fingerprint-based resync, applying accepted `graph_update` frames through one atomic clean-snapshot transition (including preserved-block/revision refs and dirty blocking), handling `parse_error`, and session expiry. |
| `frontend/src/hooks/useSubmodelNavigation.ts` | `handleCreateSubmodel`/`handleDrillIntoSubmodel`/`handleBreadcrumbNavigate`/`handleDissolveSubmodel` — definition/occurrence-aware view-stack state machine, local embedded-definition drill/project/layout, revision-preconditioned transform requests, and one atomic dirty history entry per create/dissolve. |
| `frontend/src/utils/submodelViewGraph.ts` | Pure projection from one definition plus one occurrence's parent bindings into collision-safe composite Input/Output nodes and definition-port boundary edges. |
| `frontend/src/utils/submodelDeletionPolicy.ts` | `withNativeDeletePolicy` applies the owner-aware React Flow deletion gate while preserving unchanged node identity. |
| `frontend/src/utils/submodelRuntimeTarget.ts` | `encodeRuntimeIdPart`, `qualifiedRuntimeNodeId`, `resolveDrilledOccurrenceIdentity`, and `runtimeNodeIdForVisibleNode` validate drilled occurrence identity and derive backend runtime targets. |
| `frontend/src/utils/canonicalSubmodelBoundaryEditing.ts` | Pure definition-aware boundary transform. It validates canonical identity, edits structured endpoints and opaque public-port ids, preserves boundary positions, preflights interface changes against every bound occurrence, and returns one coherent child/definition/parent result. |
| `frontend/src/utils/submodelBoundaryEditing.ts` | Canonical boundary-edit orchestration that validates occurrence identity and delegates structured definition transforms to `canonicalSubmodelBoundaryEditing.ts`. |
| `frontend/src/hooks/useSubmodelBoundaryEditing.ts` | Adapts canonical pure boundary transforms to React Flow connection/deletion events, the history-aware atomic graph setter, `parentGraphRef`, error toasts, and undo/redo reconciliation while a drilled view is active. |
| `frontend/src/hooks/useGraphCanvasState.ts` | React Flow adapter over `useGraphStore`: converts `NodeChange[]`/`EdgeChange[]` into raw graph updates, takes one snapshot at a drag's first structural position change, and avoids history churn for per-frame movement and selection-only changes. |
| `frontend/src/hooks/usePanelGraphContext.ts` | Produces the typed, render-stable `PanelGraphContextSnapshot` (`allNodes`, `edges`, `nodeById`, `getNode`) only when the graph store's panel-context version changes, isolating editor consumers from React Flow UI-only updates. |
| `frontend/src/hooks/useKeyboardShortcuts.ts` | App-level canvas keyboard bindings for save, undo/redo, copy/paste, delete, search, and panel dismissal; honours editable controls so keystrokes do not leak from a text field into graph mutation. |
| `frontend/src/utils/buildGraph.ts` | `buildGraph` (backend payload shape), `graphForRequestIdentity` (semantic graph projection consumed by Data Output), and `resolveGraphFromRefs` (parent-graph-takes-priority resolution used by preview/save/submodel calls). |
| `frontend/src/utils/graphDiff.ts` | `diffPipelineNodes` — pure added/removed/changed/moved node diff between two graph versions, backing the comparison view. |
| `frontend/src/utils/graphHelpers.ts` | `computeNextNodeId`, `normalizeEdges`, and `filterIncomingEdges`; validates endpoint/handle existence for layout while preserving the full normalised edge list for graph state and save. |
| `frontend/src/utils/graphPerformance.ts` | `shouldUseLiteGraphEffects`/`GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT` (1000). |
| `frontend/src/utils/makePreviewData.ts` | `makePreviewData` — `PreviewData` constructor with defaults. |
| `frontend/src/utils/columnFingerprint.ts` | `columnFingerprint`/`columnsEqualByFingerprint` — collision-safe column-schema fingerprinting. |
| `frontend/src/utils/activePreview.ts` | `previewForActiveNode` — filters preview data to the currently active node. |
| `frontend/src/utils/validateConfigRefs.ts` | `validateConfigRefs`/`formatConfigRefWarnings` — flags `data_input`/`banding_source`/`instanceOf` config fields pointing at non-existent node ids (graph-level or submodel-internal). |
| `frontend/src/utils/layout.ts` | `getLayoutedElements` plus `nodeIdsNeedingLayout`/`mergeLayoutedNodePositions` for partial imported-graph layout; finite positions including the origin are authoritative. Also owns `clusterSnap`/`alignPositions` coordinate snapping. |
| `frontend/src/utils/connectionValidation.ts` | `isPipelineConnectionValid` — the graph-level `isValidConnection` React Flow validator, including ordinary executable input-name uniqueness, strict public-port validation and one-binding-per-input enforcement, and null-API-handle rejection in every gesture direction. |
| `frontend/src/utils/flowElements.ts` | `appNode`/`appEdge`/`nodeLabel`/`edgeId`/`deselectNodes`/`selectOnlyNode` — node/edge factories and id/selection helpers. |
| `frontend/src/utils/flowHandles.ts` | `DEFAULT_TARGET_HANDLE`/`normalizeDefaultTargetHandle` — collapses React Flow's synthetic default-target-handle id to `null`. |
| `frontend/src/utils/nodeTypes.ts` | Canvas metadata derived from shared `types/node.ts::PIPELINE_NODE_TYPES`: `NODE_TYPE_META` and lookups (`SOURCE_ONLY_TYPES`, `SINK_ONLY_TYPES`, `SINGLETON_TYPES`, `PALETTE_TYPES`, `nodeTypeIcons`/`nodeTypeColors`/`nodeTypeLabels`, `PILL_TYPES`). |
| `frontend/src/utils/apiInputPorts.ts` | Mirrors backend API-input frame identity and resolves executable names across ordinary nodes, public-port ids, and alias-prefixed public-output names; also owns label validation, conservative rename updates, and orphan-handle pruning. |
| `frontend/src/utils/edgeJoinRoles.ts` | Defines edge-join base/join handle roles and maps the rendered bottom join handle onto the canonical join role. |
| `frontend/src/utils/edgeJoinGraph.ts` | Pure edge-join candidate validation and insertion/rewrite helpers; candidate feedback and release-time insertion share the same validator. |
| `frontend/src/utils/edgeJoinInsertionFeedback.ts` | Pure render-only Edge Join candidate decoration that preserves edge-array identity when inactive. |
| `frontend/src/utils/edgeJoinValidation.ts` | Save-time edge-join graph validation and readable warnings. |
| `frontend/src/utils/nodeTypeRegistry.ts` | React Flow node-type registry built from canonical metadata, shared by editable and read-only canvases. |
| `frontend/src/utils/graphSnapshot.ts` | Four-field graph snapshot serialization/cloning and persisted canonicalisation; omits transient node/submodel metadata from fingerprints. |
| `frontend/src/utils/shallowNodeHash.ts` | Stable shallow data hashing used by structural and panel-context fingerprint calculations. |
| `frontend/src/components/ComparisonInspector.tsx` | Read-only comparison-view config panel: renders the real node editor `inert` for the available side(s), with a Historical/Current switcher. |
| `frontend/src/components/ComparisonView.tsx` | The historical-vs-current comparison canvas pair: fetches the historical pipeline, diffs it, and renders two non-interactive `ReactFlow` instances (`ReadonlyCanvas`) with diff-ring highlighting, a draggable split, and orientation toggle. |
| `frontend/src/components/EdgeJoinInsertionFeedback.tsx` | Renders the conditional polite live-region status for a compatible edge-join insertion candidate. |
| `frontend/src/components/PolarsIcon.tsx` | Memoized SVG icon for the Polars node type. |
| `frontend/src/components/RenameDialog.tsx` | Node-rename modal with name-length and unsafe-character validation. |
| `frontend/src/components/SubmodelDialog.tsx` | "Create submodel" name-entry modal. |

## Key types and data structures

### Reusable submodel instance state (normative)

Frontend graph types mirror the backend contract: `submodels` is a registry of
typed definitions keyed by `definitionId`, while each `SUBMODEL` React Flow
node is an instance whose immutable id and typed config
`{definitionId, alias}` are authoritative. Labels and positions remain on the
node. Hooks and utilities must use `node.data.nodeType === 'submodel'` plus the
typed config; parsing `submodel__*` ids or display labels is forbidden.

For ordinary pipeline nodes, Create Instance retains the established
`config.instanceOf` behavior. For a canonical `SUBMODEL`, the action is a pure
single-snapshot graph mutation that retains `definitionId`, allocates a fresh
collision-free immutable node id and deterministic alias (copy numbering
continues past nine: `scoring_10` clones to `scoring_11`, never
`scoring_10_2`), copies only presentation defaults, and leaves all parent
boundary bindings empty.

Occurrence deletion is owner-aware and shares one predicate,
`isProtectedSubmodelNodeData` in `frontend/src/types/node.ts`: a `submodel`
node is protected when its canonical config is malformed or has no
`instanceOf` (the definition owner). Every delete surface consults it —
`handleDeleteNode`, the window keyboard Delete/Backspace handler (which
partitions a mixed selection, toasts once for spared owners, and deletes the
remainder), the context menu (which shows Delete only when `isSubmodelCopy`),
and React Flow's native delete through
`frontend/src/utils/submodelDeletionPolicy.ts`: `withNativeDeletePolicy`
stamps `deletable` on every canvas node (owners and malformed occurrences
false, copies true, other nodes untouched), and that flag is the sole
native gate — React Flow excludes non-deletable nodes from a deletion set
before acting and preserves the edges of nodes it does not delete, so a
mixed native selection deletes everything except owners. Explicitly
selected boundary edges stay deletable everywhere (removing a binding is a
legitimate edit, not occurrence removal). User-facing feedback for a
blocked owner comes from the window keyboard handler's toast, which fires
on the same Delete/Backspace gesture. Instance copies delete like
ordinary nodes, with incident edges, in one undo step. Duplicate remains
omitted for every submodel occurrence; owner removal uses the
instance-targeted dissolve lifecycle and reusable copying uses Create
Instance.

Selection-based submodel creation obtains `nodes`, `edges`, and `submodels`
from one synchronous `useGraphStore.getState()` read when the dialog is
submitted. Request construction never uses the effect-mirrored `graphRef` or
`submodelsRef`, so a just-loaded graph cannot submit an obsolete hidden node.
Create and dissolve serialise that complete persisted snapshot with
`serializeSnapshot` and capture the source file, pipeline name, source
revision, preserved blocks, and a monotonically increasing transform request
serial. Their responses commit only when that complete request context is
still current and the store serialises identically. This catches position-only
and submodel-only edits that deliberately do not increment
`structuralVersion`, while excluding transient React Flow presentation
fields. A local edit, pipeline/revision change, navigation action, or older
overlapping transform response therefore causes the stale response to be
rejected visibly without touching graph state.
The dissolve response guard rejects the removed child-file lifecycle keys
rather than silently accepting an old response shape.

Navigation resolves canonical identity from the selected `SUBMODEL` node, not
from an id prefix or label. Loading, response-identity validation, projection,
and layout complete before any graph, ref, selection, or view-stack mutation.
A successful frame stores both `instanceId` and `definitionId`; failure leaves
the current view unchanged. Synthetic canonical Input/Output nodes retain that
`definitionId` marker. Drilled Input edges contribute the sanitised public
`portId` to child configs/codegen, while a parent edge sourced from an
occurrence contributes sanitised `<alias>__<portId>`.

Shared-definition save performs an interface diff by immutable port id and
checks all parent placeholder edges. Any removed or direction-changed bound
port yields a blocking dialog containing every
affected instance label/id and port label/id. No setter, file request, dirty
baseline, or undo history is updated on rejection. A compatible edit submits
one definition update and refreshes every occurrence without changing its
position or bindings. Node deletion from React Flow, the context menu, and the
keyboard is staged with its incident edges and passed through this same
compatibility check before the single graph setter is called. A mixed React
Flow change batch applies its non-removal changes inside that same staged
reconciliation rather than dropping them or committing a second mutation.

Tests must precede implementation and cover: two independent occurrences of
one definition; fresh identity/alias allocation; one-snapshot undo; navigation
by instance plus shared definition; public-handle rendering and direction
checks; persistence through graph replacement; selection-based creation from
an atomic store snapshot even when effect-mirrored refs are stale; grouping
two disconnected input nodes; shared-edit messaging; and
atomic interface-break rejection with all affected occurrences reported.
Coverage includes every deletion entry point and stale create/dissolve
responses after an intervening local mutation, request-context change, or
newer overlapping transform.

- **`GraphSnapshot`** (`{ nodes: Node[]; edges: PipelineEdge[]; preamble: string;
  submodels: Record<string, unknown> }`,
  `useGraphStore.ts`) — the unit of undo/redo for a graph edit.
- **`PIPELINE_NODE_TYPES` / `NodeTypeValue`** (`types/node.ts`, owned by
  frontend-shared) — the 19 persisted node strings accepted by guards and
  consumed by canvas metadata. It includes `dataInput`/`dataOutput` and no
  historical Data Source/Data Sink aliases.
- **`PipelineEdge`** (`types/node.ts`) — React Flow's `Edge` plus optional
  `sourcePort`/`targetPort`. Those fields retain an authored connect port while a submodel
  boundary temporarily consumes `sourceHandle`/`targetHandle`; API response types and outgoing
  `GraphPayload` use this shape explicitly.
- **Request-facing document refs** — `sourceFileRef`, `sourceRevisionRef`, and
  `preservedBlocksRef` mirror non-editable persisted metadata outside the
  four-field undoable graph store. Load replaces all three; an accepted
  WebSocket refresh replaces revision and preserved blocks for the same source
  identity. Save/create/dissolve replace the revision only after success.
- **`VcHistoryEntry`** (`{ kind: "vc"; label: string; undo: () => Promise<void>; redo: () => Promise<void> }`)
  — a version-control operation riding the same history stacks; carries its
  own async inverse. `HistoryEntry = GraphSnapshot | VcHistoryEntry`;
  `isVcEntry()` is the discriminant type guard.
- **`GraphStore`** — the full state/action surface: state
  (`nodes`, `edges`, `preamble`, `submodels`, `lastSavedSnapshot`, `undoStack`,
  `redoStack`, `vcBusy`, `structuralVersion`/`structuralFingerprint`,
  `panelContextVersion`/`panelContextFingerprint`, `persistedFingerprint`,
  `savedPersistedFingerprint`, `dirty`); history-aware actions (`setNodes`,
  `setEdges`, `setNodesAndEdges`, `setNodesAndEdgesAndSubmodels`,
  `setPreamble`); raw actions
  (`setNodesRaw`, `setEdgesRaw`, `setSubmodelsRaw`, `setPreambleRaw`); explicit history ops
  (`pushSnapshot`, `pushVcEntry`, `undo`, `redo`); `markSaved`; and pure
  selectors (`isDirty`, `canUndo`, `canRedo`). `loadGraphSnapshot` is the
  separate whole-document boundary: it deep-clones and installs all four
  persisted fields, sets the saved baseline and both persisted
  fingerprints to that snapshot, clears both history stacks, recomputes the
  structural and panel-context fingerprints, and increments both versions
  exactly once in one Zustand transition.
- **`HauteNodeData`** (`types/node.ts`) — base node data shape:
  `label`, `nodeType`, `description?`, `config?`, `code?`, `func_name?`, plus
  underscore-prefixed *transient* fields that are runtime-only and never
  persisted: `_columns`, `_availableColumns`, `_schemaWarnings`,
  `_columnsSource` (the active source the column stash was captured
  under — set alongside `_columns`/`_availableColumns`/`_schemaWarnings`
  by `usePipelineAPI`, compared against the live active source to decide
  staleness), `_status`, `_traceActive`, `_traceDimmed`, `_hoverDimmed`,
  `_traceValue`, `_traceMotionDisabled`, `_diffStatus`.
- **`SubmodelDefinition`** — `{ definitionId, file, graph, inputPorts,
  outputPorts }`, where every input port owns ordered internal targets and every
  output port owns exactly one internal source. Public `portId` values are
  non-blank, unpadded, unique across both directions, and independent of child
  ids and display labels.
- **`SubmodelInstanceConfig`** — canonical per-occurrence
  `{ definitionId, alias, instanceOf? }`; every present field is non-blank and
  unpadded. The node id is the immutable occurrence id and is not inferred from
  the alias. Exactly one occurrence per definition omits `instanceOf` and owns
  editing; every created instance points directly to that owner and is
  read-only when drilled into.
- **`SubmodelNodeData`** extends `HauteNodeData` with a required canonical
  `SubmodelInstanceConfig`. A submodel occurrence without that exact identity
  shape is invalid rather than renderable through a fallback.
- **`SubmodelBoundaryPort`** — `{ id, label }`, separating the React Flow
  handle id from the user-facing frame name.
- **`SubmodelPortData`** — `{ label, portDirection: "input" | "output",
  ports: SubmodelBoundaryPort[], externalNodeIds: string[], _traceActive?, _traceDimmed?,
  _traceMotionDisabled? }`. The synthetic drill view contains one data object
  per direction, not one per frame. `externalNodeIds` is the ordered, distinct
  set of flat parent node ids whose trace steps collapse onto that card.
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
   A `seededRef`-guarded effect calls `loadGraphSnapshot` once with the
   caller's nodes/edges and canonical empty preamble/submodels. The real
   pipeline later arrives through that same action. A remount therefore
   cannot retain a prior document's persisted fields, saved baseline, or
   history.
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
   `utils/apiInputPorts.ts`). For a frame rename, occurrence edges only rebind
   their external `sourceHandle` because the shared definition is already keyed
   by public port id. Ordinary targets additionally migrate
   `input_scenario_map` keys and instance `inputMapping` entries. The
   preflight then checks each affected executable target's post-commit
   input-name set for duplicates. On a collision the commit
   returns `{ ok: false, error }` and **nothing mutates** — no snapshot, no
   config, no edges, no mappings; `NodePanel` passes the result through
   `OnUpdateConfig` so the ApiInputEditor surfaces `error` inline at the
   label field (see
   [frontend-node-editors](../frontend-node-editors/low-level.md)), clearing
   it on the next successful commit. On success the whole tentative result —
   the migrated root nodes, edges, and canonical definition registry — lands
   through the single
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
   `nodes`/`edges`/`preamble`/`submodels` to the target snapshot,
   recompute all three fingerprints and `dirty` from it, and push the
   *current* (pre-undo) state onto the opposite stack via
   `captureGraphSnapshot`. Every push to either stack goes through the same
   `MAX_HISTORY` cap.
9. **Whole-document load and `markSaved(snapshot?)`.**
   `loadGraphSnapshot(snapshot)` deep-clones the input, computes its
   structural, panel-context, and persisted fingerprints, installs all four
   graph fields, records a separate clone as `lastSavedSnapshot`, copies the
   persisted fingerprint to `savedPersistedFingerprint`, sets `dirty=false`,
   clears `undoStack`/`redoStack`, and increments
   `structuralVersion`/`panelContextVersion` once regardless of fingerprint
   equality. Versions are never reset: a document switch that changes only
   submodels must still receive a fresh preview/result-cache identity.
   `markSaved(snapshot?)` captures `lastSavedSnapshot` (defaults to the
   current state) and `savedPersistedFingerprint`, then recomputes `dirty`.
   Loads use the atomic load action directly; `markSaved` remains the
   successful-save boundary.
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
    truncating name in the same 13px semibold primary-text typography as
    node names (full name as `title` tooltip) and that row's labelled
    source `Handle` (id = the frame's raw label, a sole frame included),
    absolutely positioned at the row's vertical midline with its dot
    centred on the node's right border. The instance name is
    suppressed in that body; the trace-value pill, when active, renders
    above the rows. Zero eligible frames keeps the instance name, adds a
    muted "No emitted frames" line, and renders no source handle. At
    medium/compact zoom no frame rows render and `_SourceHandles` supplies
    the same handle id set — labelled handles evenly spaced down the right
    edge, or no handles for zero eligible frames. Positional
    `output-connector[<idx>]:<node label>` test
    ids follow the visual top-to-bottom order in both modes, and the name
    span keeps its `api-input-body-label-<label>` test id. Edge-join nodes
    short-circuit to an entirely separate marker/pill render before the
    LOD branches run; their status and warning dots sit inside the visible
    marker ellipse rather than outside its right edge.
12. **Node delete (`useNodeHandlers.handleDeleteNode`).** Calls
    `setNodesAndEdges` once (node filter + edge filter closed over the same
    call, one undo entry), nulls `selectedNode`/`previewData` if they
    referenced the deleted node, and defers `clearNode(id)` via
    `setTimeout(..., 0)` so the cache eviction lands only after the
    node-removal render has committed. Also clears `renameDialog`/
    `submodelDialog` if either referenced the deleted node.
13. **Connection candidate and commit (`useEdgeHandlers`).**
    `onConnectStart` records an active endpoint only when the gesture begins
    at a source handle. Pointer movement asks `findEdgeIdAtPoint` for the
    uppermost edge under the pointer and runs
    `validateEdgeJoinInsertionCandidate` against `graphRef.current`. The hook
    exposes only a compatible candidate edge id; repeated movement over the
    same edge is an identity-preserving state no-op, moving between valid
    edges replaces the id, and a node/handle, invalid edge, canvas exit, or
    non-source gesture clears it. `FlowEditor` decorates that edge in the
    derived render-only edge list and conditionally mounts a polite
    `role="status"` message whose text names the Edge Join insertion action.
    Neither representation is written to `useGraphStore`.

    `onConnectEnd` first clears the active source/candidate state, then reads
    `fromHandle`/`toHandle` types off the connection-end event to decide the
    shape of the gesture: source→source with a resolved target node inserts
    an edge-join via `insertEdgeJoinNodeFromSources`; source→target or
    target→source with a resolved target node calls `commitConnection`;
    source-with-no-target-node probes `findEdgeIdAtPoint` (a DOM hit-test
    via `document.elementsFromPoint`) and, if the drop landed on an edge,
    re-runs `validateEdgeJoinInsertionCandidate` through
    `insertEdgeJoinNode` before committing the rewrite. `commitConnection`
    special-cases an edge-join *target*: it resolves the canonical
    base/join role for the target handle, rejects a second input to an
    already-filled role or a third input overall, stores the source node id
    into the edge-join's `config` under that role's key, and pushes one
    snapshot before applying nodes/edges via the raw setters (so the
    role-config write and the new edge land in the same undo entry as the
    edge itself). A successful edge-join insertion (either path) also
    selects the new node, clears trace, and cancels any in-flight preview.
    Splitting preserves the original edge's `sourceHandle` on the new base
    edge and `targetHandle` on the new downstream edge, and preserves the
    dragged source's handle on the join-role edge. Failure leaves graph,
    selection, and history untouched; an edge-targeted failure uses the
    exhaustive reason-to-toast map, while a non-edge cancellation is silent.
14. **Palette drop (`useEdgeHandlers.onDrop`).** Parses the drag event's
    `application/reactflow-type` and `application/reactflow-config` payloads;
    a config JSON parse failure or a non-object payload toasts an error and
    creates nothing. On success, builds the node via `appNode`, selects it
    exclusively (`selectOnlyNode`), and sets it as the panel's selected
    node.
15. **Pipeline load (`usePipelineAPI`, mount effect).** Calls `loadPipeline`
    with a cold-start retry policy (`INITIAL_PIPELINE_RETRY_POLICY`, 6
    retries at 250ms base delay); the response is validated through
    `parsePipelineResponse` before touching the graph. On success, the hook
    canonicalises an omitted/null preamble to `""` and submodels to `{}`,
    requires a non-empty, non-whitespace `source_revision` for a live document, copies
    `preserved_blocks`/`source_revision` into their request-facing refs,
    updates the matching refs, then calls `loadGraphSnapshot` once with
    normalised edges and all four persisted fields. That one transition
    makes the response the clean saved baseline and clears history; no
    sequence of raw setters plus `markSaved` is permitted for a document
    load. It then seeds `nodeIdCounter` from `computeNextNodeId`. Aborted via
    `AbortController` on unmount.
16. **Preview fetch (`usePipelineAPI.fetchPreview` →
    `fetchPreviewImmediate`).** `fetchPreview` cancels any in-flight
    request/debounce, paints cached data (or a `"loading"` placeholder)
    immediately, then debounces (`options.debounceMs ?? 200`) before calling
    `fetchPreviewImmediate`. That function snapshots `rowLimit`/
    `activeSource`/`streamingChunkSize` once, checks the node-results cache
    for a hit matching source+rowLimit; if the cached entry also matches the
    current `structuralVersion` it short-circuits with no network call,
    otherwise it shows the cached data while re-fetching in the background.
    Before any network preview is sent, the request awaits
    `ensureInputSnapshots` on the resolved graph — missing snapshot-backed
    inputs are built or joined first (see the caching spec) — and an ensure
    failure surfaces as that node's preview error; `refreshPreview` and
    `previewNodeFrame` gate the same way.
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
    (`captureGraphSnapshot`, `structuredClone`); snapshot cloning strips only
    React Flow UI fields and therefore retains `PipelineEdge.sourcePort`/
    `targetPort`. The request also sends `preservedBlocksRef.current` unchanged.
    It then stamps the attempt with
    `++saveRequestSeq.current`. On a successful `savePipeline()` response,
    calls `markSaved(savedSnapshot)` only if this request's id is still the
    newest applied (`saveRequestId > appliedSaveSeq.current`), then updates
    `useGitStore`'s last-save SHA and notifies its history-changed
    subscribers and replaces `sourceRevisionRef.current` with the committed
    `source_revision`. Never throws; resolves `false` on any failure after
    toasting the detail.
19. **WebSocket sync (`useWebSocketSync`).** Connects to the credential-free
    `/ws/sync` URL; the browser supplies its HttpOnly same-origin cookie during
    the handshake. On open, sends a `resync` message
    carrying the last-applied graph fingerprint for the current source file
    (server skips replying if it already matches). Every accepted
    `graph_update` or `parse_error` synchronously advances a generation; an
    older update may not mutate graph/banner/dialog state after awaiting
    layout. Source matching tolerates absolute-vs-relative spelling, but if
    either side has an identity then both must resolve to the same file. If
    the store is `dirty`, the update is blocked and a sync banner shown
    instead of applied. Otherwise the incoming `submodels` value must be a
    plain map, except that the backend's explicit `null` empty-collection
    representation is normalised to `{}`; an omitted field still fails
    loudly. Edges are normalised and partitioned by live endpoint/handle
    validity; all remain in graph state/save, one bounded warning describes
    rejected layout edges, and only the valid partition is given to ELK.
    `nodeIdsNeedingLayout` selects missing/non-finite positions and
    `mergeLayoutedNodePositions` copies ELK positions only to those ids, so
    every finite coordinate including `{x: 0, y: 0}` is retained. A
    `graphRefreshingRef` guard prevents React Flow's spurious
    `onSelectionChange` during the swap from clearing the open panel.
    Nodes, edges, submodels, and preamble are installed together through one
    `loadGraphSnapshot` transition, which also establishes the clean baseline
    and clears local history; preserved blocks and the required live
    `source_revision` refs advance with that same accepted update. A thrown
    error restores the request-facing refs before re-throwing into the outer
    catch, which toasts, while the atomic store transition leaves no partial
    graph to roll back.
    Reconnection backs off exponentially (`INITIAL_BACKOFF_MS` doubling to
    `MAX_BACKOFF_MS`, capped at `MAX_RETRIES` = 50). A `1008` close with a
    session-expired reason force-refreshes the HttpOnly cookie, then reconnects;
    only a failed refresh emits the session-expired event. An abnormal (`1006`)
    pre-open close also attempts bootstrap before continuing the bounded retry
    loop, covering local backend restarts without putting a secret in a URL.
20. **Submodel drill-in and boundary editing.**
    `useSubmodelNavigation.handleDrillIntoSubmodel` resolves a canonical
    occurrence by node id, loads by `definitionId`, verifies the returned
    definition identity, overlays the authoritative child graph onto the typed
    interface, and asks `buildSubmodelViewGraph` for one collision-safe Input
    and Output card keyed by the immutable instance id. Each declared input port
    becomes one labelled Input row and one edge per ordered target; each output
    port becomes one source-to-Output edge. Parent bindings are validated against
    `in__<portId>`/`out__<portId>` before projection. The child graph, synthetic
    edges, and ELK positions are all computed before any view stack, graph,
    source-file ref, or selection mutation, so load/projection/layout failure is
    atomic. A shared-definition toast names the number of affected occurrences.

    `useSubmodelBoundaryEditing` intercepts boundary connects/deletes before the
    generic edge handler and dispatches canonical state to
    `canonicalSubmodelBoundaryEditing.ts`. Canonical reconciliation rebuilds the
    definition graph and structured endpoints, allocates opaque `output_N` ids
    for new exports, preserves both boundary-card positions, and leaves every
    parent occurrence's id, position, alias, and edges untouched. Before an
    endpoint is removed or redirected, it scans all occurrences and rejects one
    atomic edit with a visible error if any changed public port is bound,
    reporting every affected occurrence and port. A successful result commits
    the view, definition registry, and parent refs through one history-aware
    setter; missing/malformed identity or topology fails loudly. The projection
    retains the two empty boundary cards and refuses to reconcile a graph that
    no longer contains both cards.
21. **Submodel create/dissolve.** Both handlers refuse to run while a drilled
    view is active (`parentGraphRef.current` set), toasting an instruction to
    return to the main pipeline — the same client-side gate as save — so the
    backend revision precondition is never triggered by a mis-scoped request.
    Both requests send `base_revision=sourceRevisionRef.current` and
    `preserved_blocks=preservedBlocksRef.current`. Create changes no local state
    until the response succeeds. Dissolve resolves the selected node as a typed
    occurrence and sends only `instance_id`. Each successful response replaces
    nodes, edges, definitions, and preamble through one history-aware store
    action, updates the in-memory preserved-block ref, creates one undo entry,
    and leaves the persisted revision unchanged. The resulting dirty graph is
    written only by explicit Save. Other occurrences of the same definition
    remain collapsed and keep the registry entry; dissolving the final
    occurrence removes the definition only from the submitted graph, while
    Save later decides whether its managed child files are safe to delete. A
    `409` leaves graph and refs untouched and surfaces the backend reload
    instruction.
22. **Breadcrumb navigate (`handleBreadcrumbNavigate`).** Reconciles the
    active drilled projection, then restores the synchronized parent graph
    (not the stale entry snapshot and not a re-fetch). It clears
    `parentGraphRef` only after returning all the way to depth 0, so declared
    exports, removed consumers, and explicit input mappings appear on main.
23. **Comparison view mount (`ComparisonView`).** Freezes the current
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

- **API-input handle ids never synthesize.** Zero eligible frames render no
  source handle; one eligible frame or more renders one labelled handle per
  frame, ids = the raw labels. A blank, duplicate,
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
  or every label invalid): no source handle renders and the body shows the
  zero-frame state — no bindable id is invented for an invalid config.
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
- **Whole-graph editor transforms are one atomic history step.**
  `setNodesAndEdgesAndSubmodels` may also replace the preamble; it publishes
  nodes, edges, definitions, and support code together, captures one undo
  snapshot, and recomputes dirty state once. Submodel create and dissolve use
  this action. They never update the persisted revision; only Save does.
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
- **`handleDuplicateNode`, singleton types, and reusable submodels.**
  Duplicating a node whose type is in `SINGLETON_TYPES` (`apiInput`, `output`,
  or `liveSwitch`) is a silent no-op because the palette already prevents a
  second one. Generic duplication of a `SUBMODEL` is absent from its context
  menu and rejected visibly by the handler with direction to use Create
  Instance; that path allocates a fresh occurrence id and alias. Duplication,
  paste, and context-menu creation consume the same singleton metadata that
  mirrors the backend save invariant.
- **`onDrop`'s config JSON never falls back to `{}` on a parse failure** — a
  malformed or non-object payload aborts node creation entirely (toast,
  return) rather than creating a node with an empty config that would then
  violate that node type's downstream invariants (Issue #35). An *absent*
  config payload (the empty-string default) legitimately becomes `{}`.
- **The preview cache key is (node id, structural version, source, row
  limit).** The store lookup supplies node identity; freshness additionally
  requires exact structural version, active source, and row limit. A cached
  entry from the same source/row limit paints immediately, but any freshness
  mismatch re-fetches in the background — never blocking the UI on a
  settings change.
- **Data Output is a non-previewable sink.** Node selection opens its editor
  but `previewOptionsForClick` never sends it through the generic preview
  path, avoiding an ambiguous promise that a persistence boundary is always
  side-effect-free.
- **A structured API-input without a `tables` array is not automatically
  previewed when selected.** `previewOptionsForClick` classifies that
  incomplete authoring state as unavailable for click-to-preview,
  `onNodeClick` clears any prior preview, and no request is sent. The editor
  remains open for Infer Tables, and explicit Refresh is not suppressed:
  the backend's typed schema error remains authoritative if the user asks
  to execute before inference.
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
- Edge-join candidate movement never mutates the graph and never toasts.
  `validateEdgeJoinInsertionCandidate` returns the same exhaustive
  `EdgeJoinFailureReason` set used by insertion; only `ok` candidates are
  exposed. `onConnectEnd` clears candidate state before all early returns and
  before coordinate parsing, so cancellation and malformed touch endings
  cannot strand visual or accessible feedback. Release-time validation
  remains authoritative if the graph changed after the last pointer move.
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
  message JSON, an omitted or invalid non-object
  `graph_update.graph.submodels` (explicit `null` means an empty map), and any
  exception raised while applying a `graph_update` — the last case also
  attempts a best-effort rollback of nodes, edges, submodels/ref, and
  preamble to the pre-update snapshot (itself wrapped in a try/catch that
  swallows a rollback failure so it never masks the original error in the
  toast). A `1008` close code
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

`frontend/src/stores/__tests__/useGraphStore.loadSnapshot.test.ts` pins atomic
whole-document graph loads, saved-baseline installation, version advancement,
and undo/redo history clearing across pipeline switches.

The pure connection/frame helpers are defended by
`frontend/src/utils/__tests__/apiInputPorts.test.ts`,
`frontend/src/utils/__tests__/edgeJoinGraph.test.ts`, and
`frontend/src/utils/__tests__/edgeJoinValidation.test.ts`: they cover raw-label
frame eligibility, blank/duplicate/non-identifier/keyword label rejection
(mirroring backend invariant B4's ASCII rule — valid Unicode identifiers like
`café` are rejected too), frame-label derivation (`apiInputFrameLabels` across
zero/one/many eligible frames, invalid labels, and unselected-column tables),
edge input-name derivation (`edgeInputName` for api-input frame edges verbatim,
sanitised source labels for ordinary nodes, submodel `out__` edges resolving to
the child's sanitised label, and per-target duplicate detection),
rename-before-prune migration, identity-preserving no-ops, edge-join
insertion/role normalisation, and invalid saved graph diagnostics. These sit
alongside the hook/store suites below because their contracts are exercised
again through the editor and save paths.

- **Store — `frontend/src/stores/__tests__/`:**
  - `frontend/src/stores/__tests__/useGraphStore.consolidation.test.ts` — store shape and required
    action surface; a "selector isolation (reviewer gate)" block asserting
    by render count that subscribing to one slice does not re-render on
    unrelated slice changes; undo/redo push/pop/no-op semantics for
    history-aware vs. raw actions; `MAX_HISTORY` eviction; `isDirty()` as a
    pure, render-stable selector across save/edit/undo cycles, including
    submodel-only edits; regressions cap both undo and redo stacks; a regression
    block confirming `useUIStore` no longer exposes `dirty`/`setDirty` and
    doesn't duplicate graph-shaped state.
  - `frontend/src/stores/__tests__/useGraphStore.fieldEditUndo.test.tsx` — a whole inline field edit is
    one undo step regardless of edit size; a no-op edit pushes nothing; a
    guard test documents what the pre-fix per-keystroke wiring would have
    done.
  - `frontend/src/stores/__tests__/useGraphStore.structuralVersion.test.ts` — the full bump/no-bump
    matrix: position/selection/preview-only node changes never bump
    `structuralVersion`; preview-only changes bump `panelContextVersion`
    but not `structuralVersion`; underscore-prefixed metadata stays out of
    persisted fingerprints; visual-only raw changes reuse cached node
    hashes; Explore overview-card config is ignored while Explore
    data-prep code still flips the hash; add/remove/rewire/reconfigure and
    preamble edits do bump `structuralVersion`.
  - `frontend/src/stores/__tests__/useGraphStore.undoAtomicity.test.ts` — single- and multi-node delete
    (mixing connected/unconnected nodes), pure-edge delete, and paste are
    each exactly one undo entry regardless of selection size; redo replays
    the combined gesture in one step; a store-level contract test asserts
    `setNodesAndEdges` pushes exactly one snapshot per call.
  - `frontend/src/stores/__tests__/useGraphStore.vcHistory.test.ts` — `pushVcEntry` appends and clears
    redo; `MAX_HISTORY` eviction applies to VC entries too; VC undo/redo
    lock history (`vcBusy`) while the async leg runs; a failed leg restores
    the entry to its original stack for retry; VC entries interleaved with
    graph snapshots undo/redo in the correct order.
- **Context — `frontend/src/panels/__tests__/`:**
  - `frontend/src/panels/__tests__/useGraph.gaps.test.ts` — throws a clear `GraphProvider`-naming error
    outside a provider; returns the exact supplied value inside one; an
    empty-graph provider is distinct from no provider; the context sentinel
    is a real, usable `React.Context`.
  - `frontend/src/panels/__tests__/NodePanel.graphContext.test.tsx` — DOM-level regression that
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
  - `frontend/src/__tests__/nodes/SubmodelNode.test.tsx` — canonical registry
    resolution, structured input/output port ids and labels, absence of a
    default target, registry-derived child count, visible invalid-definition
    state, zero-output behavior, border, and trace cases.
  - `frontend/src/__tests__/nodes/SubmodelPortNode.test.tsx` — the single
    composite Input/Output cards; their shared right-pointing header icon;
    ordered multi-frame labels; per-row
    source-right vs target-left handle ids and nesting; shared typography;
    empty states; and trace border/glow/dim/motion states.
  - `frontend/src/__tests__/nodes/ApiInputHandles.test.tsx` — one labelled
    source handle per eligible frame from one frame up, ids matching the
    raw labels (the sole-frame labelled handle explicitly pinned);
    no source handle for zero eligible frames (no eligible tables or an
    all-invalid label set); blank/duplicate/non-identifier labels render no
    handle; row-mounted full-detail handles carry the same ids as the
    medium/compact rendering (zoom-invariant id set).
- **App / integration — `frontend/src/__tests__/`:**
  - `frontend/src/__tests__/App.integration.test.tsx` — mount and initial load sequencing (no
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
  - `frontend/src/__tests__/App.connectionMode.test.ts` — `ConnectionMode.Loose` is enabled and a
    graph-level `isValidConnection` validator is wired to `<ReactFlow>`.
  - `frontend/src/__tests__/App.findCast.test.tsx` — regression #38 (a `lastSelectedId` pointing
    at a deleted node resolves to `null`, never `undefined`, and the panel
    shows its empty state rather than a broken reference); `GraphProvider`
    array identity stability across position-only re-renders vs. refresh on
    a `structuralVersion` bump; the `graph-effects-lite` canvas class
    activates only once node/edge count crosses the shared threshold.
  - `frontend/src/__tests__/App.shallowHash.test.ts` — unit and benchmark coverage for
    `shallowNodeDataHash`/`graphFingerprintShallow`, the primitives behind
    `structuralFingerprint`: input-identity invariants (result-only fields
    don't flip the hash), input-key sensitivity (every structural field
    does), and benchmark assertions that the shallow hash stays bounded
    relative to a full-stringify baseline.
  - `frontend/src/__tests__/App.backgroundJobsIsolation.test.tsx` — the background-job store
    subscription that feeds toolbar counts does not re-render the editor
    toolbar on solve/train progress ticks.
- **Node/edge handlers — `frontend/src/hooks/__tests__/`:**
  - `frontend/src/hooks/__tests__/useGraphCanvasState.test.ts` and
    `frontend/src/hooks/__tests__/useGraphCanvasState.dragSimplification.test.ts`
    — conversion of
    React Flow node/edge changes into store mutations, exactly one history
    snapshot at drag start, no mid-drag/selection-only history churn, and
    structural/non-structural change separation.
  - `frontend/src/hooks/__tests__/useNodeHandlers.test.ts` — ordinary
    delete-as-one-atomic-step and `config.instanceOf` creation; owner and
    malformed-identity deletion refusal with direct instance-copy deletion;
    reusable occurrence creation with retained definition id, collision-free
    immutable id, normalized deterministic alias suffix (including past-nine
    numbering), empty bindings, and one undo snapshot; duplicate and
    auto-layout behavior.
  - `frontend/src/hooks/__tests__/useNodeHandlers.gaps.test.ts` — `handleRenameNode` (opens dialog with
    correct id/label, no-ops for a missing node, coerces a non-string
    label); `lastSelectedNodeRef` clearing scoped to the deleted node only;
    `renameDialog`/`submodelDialog` cleared only when they reference the
    deleted node, including a null-dialog no-op case.
  - `frontend/src/hooks/__tests__/useNodeHandlers.deferCleanup.test.ts` (#32) — `setNodes` commits
    synchronously before `clearNode`; `clearNode` is deferred past the
    current microtask; multiple rapid deletes each defer their own
    `clearNode` independently (no missed nodes).
  - `frontend/src/hooks/__tests__/useEdgeHandlers.test.ts` (largest suite) — `onConnect` is a no-op
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
    preview, API-input-without-`tables[]` automatic-preview skip,
    Data Output/output/submodel/submodel-port preview skip, same-node
    re-click skip, rapid distinct
    selections); edge deletion; context menu singleton/submodel flags;
    drag-over/drop node creation from palette metadata.
  - `useEdgeHandlers` (same file) "edge-join failures and multi-port
    handles" block — connection endings with no source node; a touch
    ending with no pointer coordinates fails loudly; third-input rejection
    against existing role-bound edges; role-config seeding on a config-less
    edge-join node; self-join rejection (dropping a node's own output onto
    its own outgoing edge, and joining a node's two outputs together);
    cycle rejection; a source-to-source join from a node no longer in the
    graph; null-source-handle storage when joining two default outputs;
    reverse-drag normalisation between default handles; edge-drop
    edge-join insertion from a default source handle; edge hit-testing
    consultation and its "missing hit-tester" fallback.
  - `useEdgeHandlers` candidate-state cases cover source-gesture entry,
    movement between compatible edges, invalid/stale/self/cycle edges,
    node/non-edge exit, canvas leave, cancellation, and connection-end
    cleanup. They assert state identity over repeated movement and prove that
    candidate churn never calls graph setters, selection setters, or
    `pushSnapshot`; existing normal connection and insertion cases retain the
    handle-preservation, selection, and single-undo assertions.
  - The Edge Join insertion feedback component is covered with React Testing
    Library: a compatible candidate exposes one named polite status and the
    derived edge receives the candidate class; clearing or invalidating the
    candidate removes both semantic and visual feedback.
  - `frontend/src/hooks/__tests__/useEdgeHandlers.dragJson.test.ts` (#35) — malformed drag-config JSON
    produces a visible error and never silently creates an empty-config
    node; well-formed JSON, the empty-string default, and non-object JSON
    (bare array/string) are each exercised explicitly.
- **Pipeline API — `frontend/src/hooks/__tests__/`:**
  - `frontend/src/hooks/__tests__/usePipelineAPI.test.ts` — initial load (cold-start retry policy,
    unmount abort, AbortError suppression, nullable-metadata tolerance,
    load-failure toast); save (success toast, dirty-during-in-flight-save
    survives, stale-response never overwrites a newer saved baseline,
    blocked while drilled into a submodel, error toast including
    `ApiError` detail); sources loading; preview fetch/status/schema
    propagation into nodes via the raw setter; requested-preview-column
    capping for wide cached schemas; client-side preview timeout surfaced
    in panel and toast; `nodeIdCounter` seeded from max numeric suffix
    (not `nodes.length`).
  - `frontend/src/hooks/__tests__/usePipelineAPI.gaps.test.ts` — preview error/abort/supersession
    distinctions; concurrent saves complete independently (no dedup) and a
    second save's failure doesn't corrupt the first's saved snapshot;
    preview caching (fresh-cache skip, custom debounce, structuralVersion/
    source/rowLimit staleness triggers a refetch); terminal-state
    guarantee after a mid-flight structuralVersion bump; debounce/abort
    interplay between `cancelPreview` and a new `fetchPreview`.
  - `frontend/src/hooks/__tests__/usePipelineAPI.abortStale.test.ts` (#31) — switching the selected
    node while a preview is aborted clears the prior node's preview data
    and the aborted fetch never re-paints onto the new node's panel.
  - `frontend/src/hooks/__tests__/usePipelineAPI.propagation.test.ts` (Phase 2D-5, largest cascade
    suite) — linear-order cascading; source/rowLimit captured at cascade
    start surviving a mid-flight store flip; halting at an unchanged
    downstream node; no duplicate downstream work from an overlapping
    second `fetchPreview`; direct-children fan-out; concurrency cap under
    wide fan-out; stale-supersession toast suppression vs genuine-conflict
    warnings; diamond-shaped dedup (shared child previews once, waits for
    the slower branch); no-op when the previewed node has no downstream
    edges; one downstream rejection does not abort sibling previews.
  - `frontend/src/hooks/__tests__/usePipelineAPI.previewLifecycle.test.ts` (W0) — a preview response or
    failure arriving after a mid-flight structuralVersion bump still
    reaches a terminal panel state; a node deleted mid-flight is never
    resurrected into the panel or cache.
  - `frontend/src/hooks/__tests__/usePipelineAPI.refPattern.test.ts` (#33/#34) — a single
    `activeSource` snapshot spans a fetch and its downstream cascade;
    `handleSave` reads `activeSource` at invocation time, not a stale
    closure; a `rowLimit` change mid-fetch does not affect the
    already-running preview.
  - `frontend/src/hooks/__tests__/columnStashSourceIdentity.test.ts` (key-contract pin) — a captured
    stash is stamped with the source the preview ran under; switching the
    active source invalidates a stash tagged with a different source
    (clearing `_columns`/`_availableColumns`/`_schemaWarnings`/
    `_columnsSource` together) and treats an untagged stash the same way,
    but a stash already tagged with the current source survives a
    same-source re-set (no gratuitous invalidation); `refreshPreview`
    re-previews an upstream node whose stash was captured under a
    different source rather than treating it as already fresh.
- **WebSocket sync — both `frontend/src/__tests__/hooks/` and
  `frontend/src/hooks/__tests__/`:**
  - `frontend/src/__tests__/hooks/useWebSocketSync.test.ts` and
    `frontend/src/__tests__/hooks/useWebSocketSync.gaps.test.ts` cover
    connection, reconnect/resync, source identity, message generations,
    partial layout, bounded invalid-edge warnings, binary/bad messages,
    delayed fit, and the required submodels apply/ref-before-save contract.
  - `frontend/src/hooks/__tests__/useWebSocketSync.panelState.test.ts` (#39) — `renameDialog`/
    `submodelDialog` referencing a node removed by a WS sync are cleared;
    left alone when the referenced node(s) survive; null dialogs stay
    null.
  - `frontend/src/hooks/__tests__/useWebSocketSync.failLoudly.test.ts` (#37) — a `getLayoutedElements`
    throw restores `graphRefreshingRef` and toasts an error without
    calling all raw setters with partial data; a setter failure rolls back
    nodes/edges/submodels/ref/preamble to the previous snapshot without
    marking the failed graph saved; missing submodels fails loudly; a
    subsequent `graph_update` after a failed one is still handled cleanly.
  - `frontend/src/hooks/__tests__/useWebSocketSync.undoHistory.test.ts` (#8) — `graph_update` only ever
    installs one complete saved snapshot, never a history-aware editor action,
    so external sync never pollutes undo/redo or publishes an occurrence
    before its definition registry.
- **Submodel navigation — `frontend/src/hooks/__tests__/`:**
  - `frontend/src/hooks/__tests__/useSubmodelNavigation.test.ts` covers
    transform-only create/dissolve as dirty single-undo edits with unchanged
    persisted revision; local canonical drill (including an unsaved new
    definition); shared-definition messaging; projection/layout failure;
    breadcrumb restore; and graph/ref error no-ops.
  - `frontend/src/hooks/__tests__/useSubmodelNavigation.gaps.test.ts`,
    `frontend/src/utils/__tests__/submodelViewGraph.test.ts`, and
    `frontend/src/utils/__tests__/submodelBoundaryEditing.test.ts` — canonical
    structured-port projection; per-occurrence collision-safe boundary ids;
    shared-interface compatibility preflight and atomic rejection; public-port
    creation/removal; definition-only reconciliation; boundary-position
    preservation; and invalid identity/topology rejection.
  - `frontend/src/hooks/__tests__/useTracing.test.ts` — flat external node ids
    represented by a composite boundary resolve to that Input/Output card for
    active/dim trace projection.
- **Utils — `frontend/src/utils/__tests__/`:**
  - `frontend/src/utils/__tests__/buildGraph.test.ts` — payload serialisation (zeroed position,
    `type`/`data.nodeType` fallback precedence, submodels/preamble
    pass-through and their `undefined` default); `resolveGraphFromRefs`
    (parent-graph priority, fallback to `graphRef`, `preambleRef` always
    wins regardless of which graph is active).
  - `frontend/src/utils/__tests__/graphDiff.test.ts` — added/removed/changed classification; moved-only
    vs changed mutual exclusivity; derived `contract` key ignored (and
    still detects a genuine change alongside an added `contract`); config
    key-order canonicalisation; a combined add+remove+change scenario.
  - `frontend/src/utils/__tests__/graphHelpers.test.ts` — `computeNextNodeId` (empty array, single/
    multiple suffixes, non-numeric-suffix nodes ignored, single- and
    multi-digit and zero suffixes); `normalizeEdges` (empty input, type/
    animated normalisation, authored `sourcePort`/`targetPort` and other
    fields preserved, no source-array mutation, multiple edges).
  - `frontend/src/utils/__tests__/graphPerformance.test.ts` — below/at-threshold behaviour for the
    shared lite-effects size limit.
  - `frontend/src/utils/__tests__/makePreviewData.test.ts` — defaults, overrides, nodeId/nodeLabel
    preserved under overrides, loading status.
  - `frontend/src/utils/__tests__/columnFingerprint.test.ts` — equivalent-schema equality; sensitivity
    to order/name/dtype changes; undefined/empty/non-empty distinctness;
    collision-safety against separator-like characters embedded in names
    or dtypes.
  - `frontend/src/utils/__tests__/activePreview.test.ts` — matching-node passthrough; stale
    previously-selected-node data hidden; null when there's no active node
    or preview.
  - `frontend/src/utils/__tests__/validateConfigRefs.test.ts` — clean graphs; valid `data_input`
    reference; stale `data_input`/`banding_source`/`instanceOf` detection;
    multi-node broken-ref aggregation; empty-string and non-string refs
    ignored; submodel-exported target resolution from canonical nested
    `{graph:{nodes}}` metadata; a target absent from
    both the graph and submodels still warns, including with other
    submodels present; no-submodels calls;
    malformed submodel metadata tolerated without crashing;
    `formatConfigRefWarnings` singular/plural/empty formatting.
  - `frontend/src/utils/__tests__/layout.test.ts` and
    `frontend/src/__tests__/utils/layout.test.ts` in the colocated and root
    utility-test trees
    — empty input; finite-position detection (including origin),
    partial-layout merge that leaves established nodes fixed, overlap
    avoidance, single-node non-zero position;
    distinct positions for connected nodes; data preserved through
    layout; a 3-node linear chain; cluster-snapping near-equal
    y-coordinates; disconnected nodes still positioned; zero-default when
    ELK omits coordinates; fan-out nodes sharing a snapped x coordinate.
  - `frontend/src/utils/__tests__/connectionValidation.test.ts` — edge-join output-to-default-input
    allowed; self-loops rejected; incomplete connections rejected.
  - `frontend/src/utils/__tests__/flowElements.test.ts` — node creation from type metadata defaults;
    metadata-name-based label generation; deterministic edge ids and
    normalised handle fields; non-mutating deselect/select-only-node
    helpers.
  - `frontend/src/utils/__tests__/nodeTypes.test.ts` — every `NODE_TYPES` value present (exact count);
    `NODE_TYPE_META` completeness and 1:1 coverage; Explore's one-input
    sink shape; Data Input/Data Output source/sink and non-singleton
    membership with strict branch-shaped defaults; Edge Join's compact
    centre-origin shape; label/name casing
    convention; exact `SINGLETON_TYPES` membership including `liveSwitch`,
    plus `SOURCE_ONLY_TYPES`/`SINK_ONLY_TYPES` membership and counts;
    `isSingletonType` true/false/undefined
    cases; `PALETTE_TYPES` validity, submodel/edgeJoin exclusion, explore
    inclusion and ordering, no duplicates; every derived lookup
    (`nodeTypeIcons`/`nodeTypeColors`/`nodeTypeLabels`) covers every node type.
  - **Gap:** `flowHandles.ts` has no dedicated test file — its one
    function, `normalizeDefaultTargetHandle`, is exercised only
    indirectly through `frontend/src/hooks/__tests__/useEdgeHandlers.test.ts`'s
    handle-normalisation cases and
    `frontend/src/nodes/__tests__/PipelineNode.test.tsx`'s handle-rendering
    cases, so a regression isolated to the sentinel-collapsing logic
    itself would surface only as a broader failure in one of those suites.
- **Components — `frontend/src/components/__tests__/`:**
  - `frontend/src/components/__tests__/ComparisonView.test.tsx` — loading-then-both-canvases sequencing;
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
  - `frontend/src/components/__tests__/ComparisonInspector.test.tsx` — label + status badge; the real editor
    rendered `inert` with the current-side config by default; the
    Historical/Current switcher for a node present on both sides; the
    absent side greyed out (not hidden) for an added or a removed node,
    each falling back to showing the side that does exist; `onClose` wired
    to the panel header.
  - `frontend/src/components/__tests__/RenameDialog.test.tsx` — title; accessible dialog role/label;
    default-value population; auto-focus on mount; cancel via button,
    backdrop click, and Escape (with in-dialog clicks NOT closing it);
    trimmed-value submission on both button and Enter; empty and
    whitespace-only submissions rejected.
  - `frontend/src/components/__tests__/RenameDialog.validation.test.tsx` (#36) — empty/whitespace/
    over-200-char rejection and exactly-200-char acceptance; newline and
    backtick rejection (would corrupt code-gen/markdown); control-character
    rejection; spaces/dashes/underscores/digits/mixed-case and non-ASCII
    unicode letters accepted; the input is a controlled value reflecting
    every keystroke; submission trims leading/trailing whitespace.
  - `frontend/src/components/__tests__/SubmodelDialog.test.tsx` — node-count text; cancel via button and
    backdrop click; empty-name submission rejected; trimmed-name
    submission calls `onSubmit`; Escape closes and is cleaned up on
    unmount; non-Escape keys are inert.
  - `frontend/src/components/__tests__/PolarsIcon.test.tsx` — default-prop SVG rendering; custom size/color.
- **Strategy.** Predominantly unit and React Testing Library component
  tests, with a deliberate render-count "reviewer gate" for the store's
  selector-isolation contract, several regression tests named after
  historical issue/work-item numbers (#8, #31, #32, #33/#34, #35, #36,
  #37, #38, #39, #84, W0, W1.3, W1.4, Phase 2D-5), and benchmark-style
  assertions bounding the shallow-hash cost.
- **Known gaps.** `utils/flowHandles.ts` has no dedicated test file; its
  sentinel-collapsing contract is exercised indirectly by
  `frontend/src/hooks/__tests__/useEdgeHandlers.test.ts` and
  `frontend/src/nodes/__tests__/PipelineNode.test.tsx`.
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
- **`frontend/e2e/edge-join.spec.ts`** — the normal Playwright lane's
  deterministic Edge Join workflow. It proves pre-release compatible-edge
  feedback and real gesture insertion; same-name-key configuration and
  joined preview rows/columns; save/reload preservation of the compact node,
  role handles, config, and split topology; a second insertion on the same
  branch; exact preservation of a named API-input `sourceHandle`; and a
  downstream trace retaining both Edge Join ancestors, leaving them undimmed,
  and highlighting their connecting path while reserving node-active styling
  for column-relevant steps. All
  drag points are derived from live locator geometry and every assertion is
  an observable DOM, preview, trace, or persisted-pipeline outcome.
