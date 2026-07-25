# Frontend Graph Canvas — High-Level Specification

## Purpose

The graph canvas is the visual editor at the centre of Haute: a React Flow-based
DAG surface where a pipeline (data sources, transforms, sinks, submodels, and
special nodes like edge-joins and live switches) is built by placing nodes and
wiring edges between them. This component owns three things: (1) how a node
renders on the canvas across zoom levels and states, (2) the single
in-memory source of truth for the graph (nodes, edges, preamble text, and
the nested submodel metadata) with undo/redo history and derived dirty
tracking, and (3) a read-only context
bridge that hands a graph snapshot to the node-inspector panel without prop
drilling. Every other panel, preview pane, and editor in the app reads the
graph through one of these two channels (the ReactFlow canvas or
`useGraph()`), so their correctness depends on this component behaving
consistently.

## Scope

In scope:
- Node rendering and geometry across zoom levels, node-type badges, status/
  warning indicators, trace and comparison-diff visual states, and connection
  handle placement — [`nodes/PipelineNode.tsx`](../../../frontend/src/nodes/PipelineNode.tsx).
- Submodel boundary rendering with per-port handles —
  [`nodes/SubmodelNode.tsx`](../../../frontend/src/nodes/SubmodelNode.tsx).
- Submodel port markers shown when a submodel is drilled into —
  [`nodes/SubmodelPortNode.tsx`](../../../frontend/src/nodes/SubmodelPortNode.tsx).
- The `<ReactFlow>` wiring in the top-level editor: node/edge change
  handlers, selection, connect/drag/drop entry points, context menu and
  keyboard-shortcut wiring, active-node/preview-pane selection, and the
  save/commit gate — [`App.tsx`](../../../frontend/src/App.tsx).
- The graph-shaped Zustand store: nodes, edges, preamble, submodel
  metadata, undo/redo history (including interleaved version-control
  entries), and derived dirty state —
  [`stores/useGraphStore.ts`](../../../frontend/src/stores/useGraphStore.ts).
- The read-only graph context exposed to the node inspector —
  [`panels/GraphContext.tsx`](../../../frontend/src/panels/GraphContext.tsx) and
  [`panels/useGraph.ts`](../../../frontend/src/panels/useGraph.ts).
- Node CRUD and canvas-gesture handlers extracted from `FlowEditor`: add
  (drag-drop from the palette), delete, duplicate, create-instance, rename,
  and ELK auto-layout —
  [`hooks/useNodeHandlers.ts`](../../../frontend/src/hooks/useNodeHandlers.ts)
  and
  [`hooks/useEdgeHandlers.ts`](../../../frontend/src/hooks/useEdgeHandlers.ts)
  (connect, edge-join insertion, selection, click-to-preview, context menu,
  drag-and-drop).
- Loading and saving a pipeline over the network, and the debounced,
  cache-first, cascade-propagating preview fetch that paints the canvas
  preview pane —
  [`hooks/usePipelineAPI.ts`](../../../frontend/src/hooks/usePipelineAPI.ts).
- The live code-to-canvas sync channel: reconciling `.py` file edits made
  outside the browser into the in-memory graph over the `/ws/sync` WebSocket
  —
  [`hooks/useWebSocketSync.ts`](../../../frontend/src/hooks/useWebSocketSync.ts).
- Submodel drill-in/drill-out navigation from the canvas side — the view
  stack, port-node synthesis from cross-boundary edges, and the
  create/dissolve API calls —
  [`hooks/useSubmodelNavigation.ts`](../../../frontend/src/hooks/useSubmodelNavigation.ts)
  and its
  [`components/SubmodelDialog.tsx`](../../../frontend/src/components/SubmodelDialog.tsx)
  name-entry modal.
- The node-rename modal, including its validation of length and unsafe
  characters —
  [`components/RenameDialog.tsx`](../../../frontend/src/components/RenameDialog.tsx).
- The read-only, side-by-side (or stacked) version comparison canvas pair
  and the diff highlighting it draws, plus the read-only config inspector a
  clicked node opens in it —
  [`components/ComparisonView.tsx`](../../../frontend/src/components/ComparisonView.tsx)
  and
  [`components/ComparisonInspector.tsx`](../../../frontend/src/components/ComparisonInspector.tsx).
- Graph construction, normalisation, diffing, layout, and validation
  utilities that back the above: the backend graph payload shape
  (`utils/buildGraph.ts`), node-level diffing for the comparison view
  (`utils/graphDiff.ts`), id/edge normalisation (`utils/graphHelpers.ts`),
  the large-graph performance threshold (`utils/graphPerformance.ts`),
  preview-data shaping (`utils/makePreviewData.ts`), column-schema
  fingerprinting (`utils/columnFingerprint.ts`), active-preview resolution
  (`utils/activePreview.ts`), broken-config-reference validation
  (`utils/validateConfigRefs.ts`), ELK auto-layout
  (`utils/layout.ts`), pipeline-wide connection validity
  (`utils/connectionValidation.ts`), the node/edge factory and id helpers
  (`utils/flowElements.ts`), default-target-handle normalisation
  (`utils/flowHandles.ts`), and the node-type metadata table
  (`utils/nodeTypes.ts`, including its `PolarsIcon` entry at
  `components/PolarsIcon.tsx`).

Out of scope (owned by neighbouring components, linked where they exist):
- Individual node configuration editors and the node inspector panel body —
  `frontend-node-editors`. (`ComparisonInspector` reuses those same editors
  read-only; it does not reimplement them.)
- Toolbar, node palette, and other editor chrome components rendered by
  `App.tsx` but not part of the canvas itself. (`SubmodelDialog` and
  `RenameDialog` are the two canvas-gesture modals that *are* in scope,
  above.)
- Version-control UI and the async operations that populate VC history
  entries — [git-integration](../git-integration/high-level.md).
  `usePipelineAPI`'s `handleSave` reads/writes `useGitStore` (last-save SHA,
  history-changed notification) but does not implement git operations
  itself.
- Trace overlay computation (`useTracing`) — the canvas only *renders* the
  `_trace*` fields these hooks set on node data.
- Background job polling.
- Submodel domain logic on the backend — placeholder construction, port
  classification from parsed AST, and the `/api/submodel/*` endpoints
  themselves — [submodels](../submodels/high-level.md). This component owns
  only the frontend side: navigating into/out of a submodel view and calling
  those endpoints.

## Behaviour

- **Node rendering.** All non-submodel pipeline node types render through one
  component, `PipelineNode`, dispatched via a shared `nodeTypes` registry
  (`utils/nodeTypeRegistry.ts`) so the live editor canvas and the read-only
  comparison canvases never disagree on which component renders a given node
  type. Rendering has three zoom-dependent levels of detail — compact,
  medium, full — chosen by a canvas zoom threshold, plus a distinct
  marker/pill render path for edge-join nodes that never uses the LOD levels.
- **Handles.** A standard node gets one target handle (left) and one source
  handle (right). Source-only and sink-only node types suppress the handle
  they don't have. Edge-join nodes render a base target handle, a join
  target handle that repositions (top/bottom/both) based on the live
  geometry of whatever is connected to it, and one output handle. API-input
  nodes render one labelled source handle per eligible frame — every table
  marked `emit: true` with at least one selected column and a valid
  identifier label — **from one frame up**: the handle id is always the
  frame's raw label, a sole frame included, so every edge an API-input
  creates names the frame it delivers and there is no null-id single-frame
  mode. Only a node with zero eligible frames (nothing emitted yet, or a
  backend-invalid config whose labels are all invalid) renders the legacy
  default null-id handle alongside the zero-frame body — and that handle is
  **not connectable in either direction** (`isConnectable` false — start
  AND end, since `ConnectionMode.Loose` normalises reverse target→source
  drags into ordinary edges), and the graph-level `isValidConnection`
  validator independently rejects any API-input connection with a null
  handle, covering every gesture path: a source that emits nothing cannot
  be wired, so a persisted API-input edge always names a frame.
  Reconciliation enforces the same rule from the other side: a null-handle
  API-input edge (only reachable through a hand-edited file) is pruned with
  the standard warning toast, never kept. At the full zoom
  level the labelled handles are mounted on the body's frame rows, so each
  dot sits at the vertical centre of the row naming its frame; at
  medium/compact zoom, where no frame rows render, the same handles fall
  back to even spacing down the right edge. The handle id set is identical
  at every zoom level, so edges stay bound across zoom changes.
- **API-input body.** At full detail, an API-input node with at least one
  eligible emitted frame uses its body as the frame list: each visible
  frame name is a full-width row paired with its output handle, and the
  generic instance name is suppressed (presentation only — the persisted
  label still drives the accessible name, `data-testid`, editor,
  selection, codegen, and tracing identities). Exactly one visible frame
  shows that frame's name the same way, on its own labelled handle. Zero
  eligible frames keeps the
  instance name and adds a muted "No emitted frames" hint. Visibility
  mirrors runtime eligibility — `emit: true`, at least one selected
  column, and a valid (non-blank, non-duplicate) raw label — and a long
  name truncates with the full name available as a tooltip.
- **Visual state.** Nodes show: a selection border; a dashed border for
  submodel instances; a "LIVE" badge on live-switch nodes when the active
  data source is live; a status dot for ok/error/running; a warning dot for
  schema warnings (suppressed when status is error); trace-active/dimmed/
  hover-dimmed opacity and glow; and, in the read-only comparison view, a
  diff ring (solid glow for added/removed/changed, dashed outline for
  moved-only).
- **Submodel nodes** show a package icon, a live child-node count, the
  backing file path (when set), and hidden per-port handles that mirror the
  submodel's stored `inputPorts`/`outputPorts` so edges into/out of the
  submodel resolve correctly even without opening it.
- **Submodel port nodes** are the boundary markers shown *inside* a
  drilled-into submodel view: an input port shows a source handle (data
  flows out of it into the submodel body); an output port shows a target
  handle.
- **Graph state.** `useGraphStore` is the sole owner of `nodes`, `edges`,
  `preamble`, and the `submodels` metadata (nested submodel graphs — store
  state so a root-frame rename that migrates nested mappings is one
  coherent, undoable transaction). All mutation goes through one of two
  tiers: history-aware actions (`setNodes`, `setEdges`, `setNodesAndEdges`,
  `setNodesAndEdgesAndSubmodels`, `setPreamble`) that push one undo snapshot
  per call, or raw actions (`setNodesRaw`, `setEdgesRaw`, `setSubmodelsRaw`,
  `setPreambleRaw`) that skip history — used for mid-drag position churn,
  WebSocket sync, pipeline load, and the continuously edited imports
  preamble. History snapshots carry `submodels` alongside nodes/edges/
  preamble, and undo/redo restores all four together (older snapshots
  without the field restore an empty metadata map).
- **Undo/redo** operates over a single stack that can hold either a graph
  snapshot or a version-control history entry, so a branch switch and a
  graph edit interleave and reverse in the order they actually happened.
- **Dirty state** is derived, not imperatively toggled: it's a fingerprint
  comparison against the last-saved snapshot, recomputed on every mutation.
  Undoing back to exactly the saved state always reports clean.
- **Graph context.** `GraphProvider`/`useGraph()` exposes a render-stable
  `{allNodes, edges, submodels, preamble}` snapshot to the node inspector's
  subtree. Calling `useGraph()` outside a provider throws immediately.
- **`App.tsx`'s `FlowEditor`** is the orchestrator: it wires ReactFlow's
  event props to interaction hooks, decides which preview pane to show for
  the currently active node, owns local UI state (selected node, context
  menu, dialogs), and gates Save/Save-&-Commit behind the current
  version-control working-branch state before delegating to the save API.
- **Node CRUD.** Deleting a node removes it and every edge touching it as
  one atomic undo step. Duplicating offsets the copy's position and is a
  no-op for singleton node types (API-input, output). Creating an instance
  stamps `config.instanceOf` at the original's id and toasts confirmation.
  Auto-layout runs ELK asynchronously, guards against overlapping runs from
  repeated clicks, and re-fits the view once positions land.
  Node-cache cleanup for a deleted node is deferred one task tick past the
  graph mutation so no component reads a torn state (node gone, cache
  already cleared) in the same render.
- **Connecting nodes.** Dragging from a handle and releasing on a compatible
  handle commits a normal edge; releasing on an *existing edge* inserts an
  edge-join node at the drop point and rewires that edge through it;
  releasing one source handle on another source handle also inserts an
  edge-join (base + join inputs), including via a synthesized touch-event
  drop point on touch devices. While a source-handle gesture is over an
  existing edge, the editor hit-tests that edge against the current graph
  before release. A compatible edge is rendered with a distinct insertion
  highlight and accompanied by a named live status message; moving to
  another edge transfers the feedback, while leaving the edge, leaving the
  canvas, cancelling, or ending the gesture removes it immediately.
  Target-handle gestures and incompatible, stale, self-join, or
  cycle-forming edges never receive valid-target feedback. Release
  revalidates the current graph rather than trusting the earlier hover
  result. A valid release preserves both source and target handles while
  splitting the edge, selects the new edge-join, and records the complete
  rewrite as one undoable action. A rejected edge release reports its
  actionable edge-join reason without changing nodes, edges, selection,
  dirty state, or history; an ordinary blank-canvas cancellation remains a
  no-op. Self-loops, duplicate edges, a third input to an edge-join, a role
  (base/join) that already has an input, and a connection that would exceed
  a node type's `maxInputs` are all rejected silently or with a named toast.
  Dropping a palette item parses its
  drag-carried JSON config and creates a node at the drop position; a
  malformed payload never creates a node with an empty config.
  Clicking a node opens/updates the inspector panel and, unless the node
  type is non-previewable or a preview pane is about to render instead,
  triggers a debounced preview fetch (a longer debounce for Optimiser
  nodes).
- **Pipeline load and save.** The pipeline loads once on mount with a
  cold-start retry policy; a backend-contract violation in the response
  throws before it reaches the graph, surfacing as a load-failure toast
  rather than a downstream crash. Save validates config references and
  edge-join wiring first — a broken edge-join blocks the save outright with
  an error toast, while broken config references only warn. A concurrent
  second save can never let an older response's `markSaved` clobber a newer
  one's.
- **Preview fetching.** Selecting or refreshing a node debounces, then
  fetches its preview; a cache hit for the same structural version, source,
  and row limit paints instantly and skips the network call, otherwise
  cached data paints immediately while a fresh fetch runs in the
  background. A schema change cascades to every reachable downstream node
  (bounded concurrency, diamond-shaped fan-in deduplicated so a shared
  child previews once), each terminating in a definite ok/error state even
  if the graph structure changes mid-flight.
- **Column stash source identity.** Every node's `_columns`/
  `_availableColumns`/`_schemaWarnings` stash is tagged with the active
  source (`_columnsSource`) it was captured under. A dedicated effect runs
  on mount and on every active-source change, stripping the stash from any
  node whose `_columnsSource` disagrees with (or is missing relative to)
  the now-active source; a stripped node returns to the same
  never-previewed state a first load leaves it in, and the existing
  lazy stale-upstream gap-fill in `refreshPreview` repopulates it on next
  preview. `refreshPreview`'s upstream-staleness filter also checks
  `_columnsSource` directly (not just presence of `_columns`), covering the
  window before the invalidation effect's `setNodesRaw` has flushed.
- **Live code sync.** External edits to a pipeline's `.py` file arrive over
  WebSocket and replace the in-memory graph — but never while the user has
  unsaved local edits, where a banner asks them to reload or discard first.
  A resync on reconnect sends the last-applied graph fingerprint so the
  server can skip re-sending an unchanged graph.
- **Submodel navigation.** Drilling into a submodel builds boundary port
  nodes from the parent graph's cross-boundary edges (matching on the
  `in__`/`out__` handle convention) and lays out the drilled-in view via
  ELK; breadcrumb navigation restores the exact saved node/edge state for
  any ancestor level, not a re-fetch.
- **Submodel creation is keyboard-triggered, not a context-menu item.**
  Selecting two or more nodes and pressing Ctrl+G opens `SubmodelDialog`
  for the name; there is no "Group as Submodel" right-click entry (a
  context-menu item was considered but never built — Ctrl+G is the only
  creation trigger). The context menu's entry for an existing submodel node
  reads "Dissolve Submodel", not "Ungroup Submodel". Clicking a submodel node
  opens the standard node inspector but fetches no preview — submodel is a
  non-previewable node type — rather than the output-port summary table that
  was once proposed and never built.
- **Version comparison.** A side-by-side (or, toggled, stacked) pair of
  read-only canvases shows a historical pipeline version against a frozen
  snapshot of the current one, with added/removed/changed/moved nodes ring-
  highlighted on the relevant side(s). Clicking a node on either canvas
  highlights its counterpart on the other and opens a read-only inspector
  showing that node's real config editor, `inert`.

## Design rationale

- **Selector isolation is a hard contract**, not a convention: `useGraphStore`
  consumers must subscribe via selectors (`useGraphStore((s) => s.nodes)`),
  never the whole store. This exists because React Flow drag events replay
  many `position` changes per animation frame; a whole-store subscription
  would re-render every consumer on every frame of every drag. A dedicated
  "selector isolation (reviewer gate)" test block enforces this by render
  count.
- **History-aware vs raw actions** exist to keep undo granularity at the
  gesture level. A raw path lets a 60fps drag update node positions without
  ballooning undo to one entry per pixel; a history-aware path snapshots
  once per user-meaningful action.
- **`setNodesAndEdges` exists to close an "undo atomicity" bug class.**
  Calling `setNodes` then `setEdges` separately for one gesture (delete,
  paste, cut) pushes two snapshots, so a single delete would need two undos
  to fully reverse. `setNodesAndEdges` captures one snapshot for the whole
  gesture.
- **Dirty is fully derived**, replacing an earlier imperative
  `setDirty(true)` pattern that had a specific bug: undoing back to the
  saved state left `dirty=true` because the boolean and the saved reference
  could drift out of sync. Deriving it from a fingerprint comparison
  eliminates that class of bug entirely.
- **Three separate fingerprints at three granularities** — structural,
  panel-context, and persisted — exist so that expensive recomputation only
  happens at the granularity that actually changed. A position drag never
  rehashes node config; a preview-only field update bumps the panel-context
  version (so the inspector panel refreshes) without bumping the structural
  version (so the graph isn't marked "changed" for undo-history purposes
  beyond what's needed).
- **API-input handle ids are the raw configured table labels, never
  synthesized ids.** The backend's codegen round-trips through those exact
  labels, so a synthesized `port_<idx>` id would silently fail to resolve at
  execution time. A blank, duplicate, or non-identifier label therefore gets
  *no* handle at all rather than a fabricated one — consistent with the
  codebase-wide preference for loud failure over a fallback that's wrong and
  hard to notice. The editor mirrors the backend's identifier rule exactly
  (ASCII identifier `/^[A-Za-z_][A-Za-z0-9_]*$/`, no Python hard keyword)
  before commit, because the label is also the downstream code argument
  name; the backend rule is ASCII-only precisely so this mirror can be
  exact rather than an approximation of Unicode `str.isidentifier()`.
- **Connections that would duplicate an input name are rejected at drag
  time.** Every incoming edge contributes exactly one input name (its frame
  label for API frames, the sanitised source label otherwise); a connection
  whose derived name duplicates an existing input on the target is refused
  with a named toast, mirroring the backend's save-time `ParseError`. The
  alternative — accepting the edge and letting codegen suffix a parameter —
  is the hidden-rename behaviour this design exists to eliminate.
- **API-input frame rows own both the name and the handle.** The earlier
  layout computed the body's label column and the handle positions in two
  unrelated coordinate systems — a stacked list inside the body vs.
  percentages of the full node height including the header — which kept
  the same order but drifted vertically as status/trace/label content
  changed the body height, so a user could not tell which line left which
  frame. Mounting each handle inside the row that names it makes the
  alignment structural: it holds for one, two, or any number of frames
  because there are no longer two layouts to keep in sync, and no
  per-frame-count constants exist to go stale.
- **One frame-label derivation, one identity.** `apiInputFrameLabels` is
  the single ordered list of eligible frame labels (no minimum count); the
  rendered handles, the body rows, downstream input chips, and the
  generated function parameters all read from it, so none of them can
  disagree. The earlier design split "visible names" from "multi-port
  handle mode" (labelled handles only from two frames up, a null-id
  default handle for one) to avoid touching persisted edge identity in a
  presentation-only release; the convergence release deliberately retired
  that split — a sole frame's edge now carries the frame label like any
  other, because a name that exists on screen but not in the persisted
  edge or the code argument is exactly the hidden-mapping class this
  design removes.
- **The zero-frame body states the absence explicitly** — "No emitted
  frames" beside the retained instance name — rather than rendering an
  empty body or keeping the old name-only layout: an unconfigured
  API-input should say what is missing.
- **Comparison-view diff, trace, and hover visuals reuse the same
  border/ring element used for selection**, rather than each state owning
  its own overlay shape, so every node type — including pill-shaped
  edge-join markers — gets a visually consistent highlight without special
  casing per shape.
- **Edge-join insertion candidacy is transient UI state, not graph state.**
  The active source endpoint and compatible edge id exist only for the
  connection gesture. The candidate edge is decorated in the derived render
  list, so entering or leaving it cannot affect persistence, dirty tracking,
  selection, or undo history. Candidate detection and release share the same
  pure compatibility check, while release still re-runs that check against
  the latest graph to prevent a stale hover result from authorising a
  rewrite. A conditional live-region status mirrors the visual highlight so
  the affordance is not pointer-only.
- **`useGraph()` throws instead of defaulting to an empty graph** when no
  provider is mounted, so a misconfigured mount surfaces immediately through
  the enclosing `ErrorBoundary` instead of silently rendering editors against
  no data (which would hide broken references and stale column sets).
- **Cache cleanup on delete is deferred by one task tick, not run inline.**
  `handleDeleteNode` calls `clearNode(id)` via `setTimeout(..., 0)` rather
  than synchronously — a synchronous clear can run before React commits the
  node-removal render, letting some other subscriber read "node gone, cache
  already wiped" in the same cycle and flicker-crash (Issue #32).
- **The preview cascade snapshots row limit, active source, and chunk size
  once at fetch time**, then closes over those values for every node it
  previews in that cascade — reading the live settings refs again partway
  through would let a user flipping the active data source mid-cascade split
  one logical preview across two sources (Issues #33/#34).
- **Downstream propagation is a bounded-concurrency BFS with per-node
  dedup**, not a naive "preview every descendant": a diamond-shaped fan-in
  previews its shared child exactly once (waiting for every changed parent
  first), and `DOWNSTREAM_PREVIEW_CONCURRENCY_LIMIT` caps how many preview
  requests are in flight at once so a wide fan-out doesn't saturate the
  backend.
- **Save concurrency is guarded by a monotonic request id
  (`saveRequestSeq`/`appliedSaveSeq`), not a boolean "save in flight" lock.**
  The user can start a second save (or keep editing) while the first is
  still in flight; only marking a save's *own* captured snapshot as saved,
  and only if no newer save has already landed, prevents a slow older
  response from stamping a stale `lastSavedSnapshot` over a newer one.
- **WebSocket sync blocks an incoming external update while the graph is
  dirty**, rather than silently overwriting local edits or silently
  discarding the server's update — either alternative loses work invisibly.
  A banner asks the user to reload or discard explicitly.
- **A failed WebSocket graph apply rolls back to the pre-update snapshot**
  instead of leaving the graph half-replaced (e.g. new nodes applied but the
  preamble update thrown partway through) — the rollback attempt itself is
  best-effort (wrapped so a rollback failure doesn't mask the original
  error), but the common case restores a consistent prior state.
- **`graphDiff` strips codegen-derived config keys (`contract`) before
  comparing node content.** The I/O contract shifts whenever an *unrelated*
  node is added elsewhere in the graph; without stripping it, every node
  would read as "changed" whenever any other node's shape changed,
  defeating the point of the diff.
- **`columnFingerprint` length-prefixes each field before joining** so a
  column name or dtype that happens to contain the separator character
  cannot collide with a different schema — correctness over a simpler but
  collision-prone plain string join.
- **Column stashes are tagged with the source they were captured under,
  not just left to go stale silently.** Before `_columnsSource` existed, a
  node previewed under source A kept its `_columns` on the node data
  indefinitely; switching to source B left editors reading source A's
  columns as if they were current, because `_columns`'s mere *presence*
  was the only signal `refreshPreview`'s stale-upstream check looked at.
  Tagging the stash with its capture source and invalidating on mismatch
  closes the same class of "cached result silently outlives the source it
  was computed for" bug that motivated widening `useNodeResultsStore`'s
  solve/train staleness key (see
  [frontend-shared](../frontend-shared/high-level.md)) — stripping the
  stash and re-triggering the existing lazy gap-fill was preferred over
  keying the cache by `(nodeId, source)`, since editors already tolerate
  "columns not loaded yet" as a normal transient state.

## Interactions

- [frontend-node-editors](../frontend-node-editors/high-level.md) — the node
  inspector panel and its per-type editors consume the graph exclusively
  through `useGraph()`/`GraphProvider`, never via props, and call back into
  `App.tsx`'s `onUpdateNode` to commit config changes. They also consume
  this component's frame display resolution (`utils/apiInputPorts.ts`) to
  label downstream inputs and output frames by the dataframe an edge
  delivers.
- [server-api](../server-api/high-level.md) — the backend counterpart to
  `usePipelineAPI` (`/api/pipeline/*` load/save/preview) and
  `useWebSocketSync` (`/ws/sync`); both hooks feed the store via
  `setNodesRaw`/`setEdgesRaw`/`setPreamble`, and `usePipelineAPI` calls
  `markSaved()` on a successful save.
- `frontend-shared` — `api/client.ts`'s typed HTTP/WebSocket functions
  (`loadPipeline`, `savePipeline`, `previewNode`, `createSubmodel`,
  `loadSubmodel`, `dissolveSubmodel`, session-bootstrap helper) are the
  transport this component's hooks call directly; this component owns the
  orchestration (debounce, cache, cascade, retry-gating, reconnect/backoff)
  around those calls, not the transport itself.
- [tracing](../tracing/high-level.md) — `useTracing` sets the `_traceActive`/
  `_traceDimmed`/`_hoverDimmed`/`_traceValue`/`_traceMotionDisabled` fields
  these node components render; the canvas itself does not compute trace
  state.
- [submodels](../submodels/high-level.md) — `useSubmodelNavigation` calls
  that component's `/api/submodel/create`, `/api/submodel/{name}`, and
  `/api/submodel/dissolve` endpoints and owns the frontend-side drill-in/
  out navigation (view stack, port-node synthesis); the backend placeholder
  construction and port classification those endpoints perform is owned by
  `submodels`, not here.
- [git-integration](../git-integration/high-level.md) — version-control
  operations (branch switch, archive, delete) are recorded as
  `VcHistoryEntry` items on the same undo/redo stacks as graph snapshots via
  `pushVcEntry`, so canvas undo/redo and VC undo/redo share one linear
  history.
- [frontend-assistant-ui](../frontend-assistant-ui/high-level.md) — depends on
  this component in two read-only ways: assistant backend mutations arrive as
  ordinary `graph.update` frames through `useWebSocketSync` (same apply,
  rollback, and dirty-gating behaviour as any external edit — nothing here
  special-cases them), and the assistant panel reads the derived dirty state
  to gate sending while local edits are unsaved.
- `frontend-shared` — the shared node-data types (`types/node.ts`) and the
  edge-join role/api-input-port handle-id conventions
  (`utils/edgeJoinRoles.ts`, `utils/apiInputPorts.ts`) that this component
  and the node editors both depend on. The node-type metadata table
  (`utils/nodeTypes.ts`) and the default-target-handle sentinel
  (`utils/flowHandles.ts`) are owned by *this* component (above), not
  `frontend-shared` — both `frontend-node-editors` and other components
  import them from here.

## Failure model

- `useGraph()` called outside a `<GraphProvider>` throws a plain `Error`
  naming `GraphProvider` in its message. `App.tsx` wraps the node panel in
  an `ErrorBoundary` (`name="NodePanel"`), so this surfaces as a caught
  render error, not a silent empty-graph render.
- API-input handle-id resolution never fabricates an id for a blank,
  duplicate, or non-identifier table label — such a table renders no handle,
  so no edge can be created against a non-existent id. The visible frame
  list obeys the same rule — an invalid label gets no row and no fabricated
  display name — so the body can never advertise a frame that does not
  exist. When a config edit orphans an *existing* edge (a table's `emit`
  flag is turned off, a table is deleted, or the last eligible frame
  disappears and a labelled edge no longer resolves),
  `App.tsx`'s `onUpdateNode` prunes the edge and raises a named `warning`
  toast rather than persisting a broken edge to disk.
- A version-control history entry whose async `undo()`/`redo()` leg rejects
  is restored to its original stack (so the user can retry) rather than
  being silently dropped or left in an ambiguous state; `vcBusy` locks
  further undo/redo motion until the in-flight operation settles.
- The canvas render tree is wrapped in `<ErrorBoundary name="Canvas">` in
  `App.tsx`, isolating a rendering crash there from the palette, toolbar,
  and side panels.
- Edge-join input-swap failures (`handleSwapEdgeJoinInputs` in `App.tsx`)
  surface as a named `error` toast rather than throwing — this is a
  best-effort canvas convenience action, not a data-integrity operation.
- Edge-join *insertion* failures (self-join, cycle, missing source/target
  node, drop point not over an edge) are looked up in a static
  `edgeJoinFailureMessages` map and surfaced as a named `error` toast; no
  partial node/edge mutation is applied. Candidate calculation uses those
  same compatibility reasons but does not toast while the pointer merely
  moves: invalid edges remain visually and programmatically unmarked. The
  release path revalidates and toasts an actionable rejection where an edge
  was actually targeted. Every connection-end path clears the active
  candidate before it can return or throw, and pointer exit clears the
  feedback without ending the underlying connection gesture.
- `useEdgeHandlers.onDrop`'s drag-carried config JSON is parsed
  defensively: a malformed payload, or one that isn't a plain JSON object,
  produces a named `error` toast and creates no node — it never falls back
  to an empty-config node (Issue #35).
- The initial pipeline load throws inside `parsePipelineResponse` on any
  drift from the expected backend contract; the `.catch` in
  `usePipelineAPI`'s load effect turns that into a load-failure toast
  rather than letting a malformed response reach the graph as
  `undefined`-shaped nodes.
- `usePipelineAPI.handleSave` never rejects — every failure path (API
  error, missing detail) is caught and surfaced as an `error` toast, and the
  function resolves `false` so callers that chain follow-on work (Save &
  Commit) can `await` it safely.
- A preview request or its downstream cascade member that fails with an
  aborted/superseded error is treated as expected cancellation (no toast);
  any other failure shows a `warning` toast naming the failing node, and a
  client-side preview *timeout* additionally shows an `error` toast.
- `useWebSocketSync` toasts on WebSocket construction errors, on
  unparsable message JSON, and on any error raised while applying an
  incoming `graph_update`; a failed apply attempts to roll the graph back
  to its pre-update snapshot (best-effort — a rollback failure is swallowed
  so it doesn't mask the original error in the toast). A session-expiry
  close code stops reconnect attempts and calls
  `notifyHauteSessionExpired` instead.
- `useSubmodelNavigation`'s create/drill-in/dissolve calls each catch their
  own failure and surface a named `error` toast; the graph is only mutated
  inside the success branch (`if (newGraph)` / `if (smGraph)` /
  `if (flat)`), so a failed call never leaves a partially-applied graph.
- `ComparisonView` catches a failed historical-pipeline fetch and renders a
  dedicated error state (message plus a "Back to editor" button) in place
  of the canvases, rather than crashing the comparison view.

> NOTE: `useGraphCanvasState`'s seeding effect (`hooks/useGraphCanvasState.ts`,
> out of scope for this spec but load-bearing for it) resets the store's
> `undoStack`/`redoStack`/fingerprints on first mount using whatever
> `initialNodes`/`initialEdges` the caller passed — in production `App.tsx`
> always calls it with `[]`/`[]` and the real graph arrives later via
> `setNodesRaw`/`setEdgesRaw` from `usePipelineAPI`, so this reset is
> effectively a one-time clear, not a load path.

## Approved change contract — 0.7.0 canonical data-I/O canvas nodes

Remaining graph-canvas improvement work is tracked in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md).

- Canvas node metadata, the React Flow registry, palette ordering, derived source/sink sets,
  node-search results, comparison inspector dispatch, and factories contain **Data Input** and
  **Data Output**, never Data Source/Data Sink. The exact frontend node set becomes 19 and must
  match the backend enum.
- `dataInput` is source-only and `dataOutput` is sink-only with one upstream input; neither is a
  singleton, so a graph may contain multiple instances. Data Output remains previewable as a
  side-effect-free pass-through; preview never invokes its explicit Write action.
- New-node defaults contain only one active discriminated branch. Required values not yet chosen
  are visibly incomplete and block save/execution; metadata never copies inactive fields or
  invents a provider/format after capability loading fails.
- Loading a graph containing a removed node fails at the guarded API/parser boundary. The canvas,
  comparison view, WebSocket sync, undo history, and graph factories provide no hidden legacy
  renderer or migration. Repository-owned affected graphs are reset to the standard blank graph
  before they reach this layer.

Acceptance pins 19-type registry parity, palette/search/derived-set membership, source/sink
handles, multiple input/output creation and save/reload, side-effect-free Data Output preview,
strict default branch shape, comparison dispatch, and legacy graph rejection.

## Approved change contract — canvas live-update reconciliation

This contract implements the live-update part of the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md) (AUD-C17).

- **Current limitation.** WebSocket graph updates currently admit an update when either side of
  the source-file comparison is blank, a parse error does not supersede layout work already in
  flight, incoming edges are normalised without proving that their endpoints and handles still
  exist, and automatic layout is all-or-nothing. Those behaviours can apply an update to the
  wrong pipeline, clear a newer error, retain a dangling edge, or move an established node at
  the canvas origin.
- **Target behaviour.** Once either the open pipeline or a WebSocket message carries a source
  identity, both identities are required and must resolve to the same file. An accepted message
  advances one monotonic generation before any asynchronous work: a later graph update or parse
  error permanently supersedes earlier layout work. Imported edges are checked against the
  incoming live node and port set. Unresolved edges remain in graph state and the next save
  snapshot so advisory renderer-contract drift can never delete user topology; only the valid
  subset participates in automatic layout, and one bounded visible warning names representative
  problems. Layout assigns positions only when coordinates are absent or non-finite. Every finite
  persisted position, including `{x: 0, y: 0}`, is authoritative.
- **Non-goals.** This change does not introduce collaborative merge semantics, change the
  backend WebSocket protocol, reinterpret an intentionally dirty local graph, or relayout a
  complete imported graph.
- **Failure and compatibility.** Source-less operation remains valid only when both sides are
  source-less (for isolated consumers and tests); a one-sided identity is fail-closed. A dirty
  local canvas continues to reject external graph replacement. Layout/apply exceptions retain
  the prior graph and surface through the existing error toast. Edge diagnostics never delete or
  fabricate an endpoint or handle. If an unresolved edge reaches save, existing backend
  validation/code generation fails visibly instead of regenerating a truncated pipeline.
- **Acceptance.** Focused hook tests prove one-sided and foreign identities are ignored, a parse
  error wins over an older pending layout, stale updates cannot clear a newer banner, endpoint
  and handle-invalid edges are retained with a bounded warning while only valid edges guide
  layout, and finite origin nodes remain fixed while only non-finite nodes receive
  non-overlapping layout.
