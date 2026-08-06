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
- Node and handle rendering across zoom, status, trace, comparison, and
  submodel-boundary states.
- Top-level canvas interactions: selection, node/edge creation and mutation,
  edge-join insertion, context/keyboard gestures, rename, layout, and
  save/commit gating.
- The single graph state/history/dirty contract and its read-only bridge to
  inspectors.
- Pipeline load/save, cache-aware previews, external code-to-canvas sync, and
  submodel navigation from the browser side.
- Read-only historical comparison and its inert configuration inspector.
- Graph payload construction, normalisation, diffing, layout, connection
  validation, and canonical node-type metadata used by those behaviors.

Out of scope (owned by neighbouring components, linked where they exist):
- Individual node configuration editors and the node inspector panel body —
  [frontend-node-editors](../frontend-node-editors/high-level.md). (The comparison inspector reuses those same editors
  read-only; it does not reimplement them.)
- Toolbar, node palette, and other general editor chrome. Canvas-gesture
  rename and submodel dialogs remain in scope.
- Version-control UI and the async operations that populate VC history
  entries — [git-integration](../git-integration/high-level.md). The save path
  consumes git state but does not implement git operations.
- Trace overlay computation — the canvas only renders the resulting visual
  state.
- Background job polling.
- Backend submodel occurrence, public-port, and endpoint behavior —
  [submodels](../submodels/high-level.md). This component owns only browser
  navigation and endpoint consumption.

## Behaviour

### Reusable submodel instances (normative)

The canvas treats a submodel definition as shared library state and each
`SUBMODEL` node as an independent occurrence. A definition can be instantiated
from an existing occurrence through **Create instance**. The action creates a
new node with a fresh immutable instance id and stable source alias, copies no
internal graph or file, starts with no bindings, and participates in the normal
undo/redo snapshot. It is available only in the parent view; nesting remains
unsupported.

Each occurrence owns its label, position, selection, and incident edges.
Renaming an occurrence never renames its definition file or changes its id or
alias. Exactly one occurrence is the editable definition owner; created
instances persist `instanceOf` pointing directly at it. Opening the owner
navigates to the shared definition editor and shows that edits affect every
occurrence. Opening an instance presents the same definition with an explicit
read-only indicator. The panel and every canvas mutation path reject edits in
that view while preview, trace, selection, copy, pan, and zoom remain usable.
Removing or dissolving targets the clicked instance id, never a display name or
definition id, and an owner cannot be dissolved while instances reference it.

Submodel cards render only declared public ports. Handles are
`in__<portId>`/`out__<portId>` and labels come from the port contract; internal
child ids are neither rendered nor accepted as parent endpoints. Each canonical
input handle accepts at most one parent binding. A connection
between occurrences is allowed only output-public-port to input-public-port.
Stale, missing, wrong-direction, or internal-child handles are blocked with an
actionable error rather than repaired heuristically.

Before saving a shared definition edit, the client and server validate every
live occurrence against the proposed interface. In v1, removing or changing a
bound public port hard-blocks the transaction and lists all affected
instance/port pairs. Interface-preserving internal edits save once and become
visible from all occurrences. Per-instance internal overrides are not offered.

Reload, WebSocket replacement, undo/redo, breadcrumbs, comparison views, and
dirty-state tracking preserve immutable definition/instance/port identities as
well as occurrence-specific positions and bindings.

- **Node rendering.** All non-submodel pipeline node types render through one
  component, `PipelineNode`, dispatched via a shared `nodeTypes` registry
  (`utils/nodeTypeRegistry.ts`) so the live editor canvas and the read-only
  comparison canvases never disagree on which component renders a given node
  type. Every node renders at full detail at every zoom level — zoom changes
  scale and nothing else — plus a distinct marker/pill render path for
  edge-join nodes. Reduced zoom-dependent renderings were removed: they hid an
  API input's emitted frames and narrowed other nodes into a truncating label
  precisely when the whole graph was in view, which is when that structure is
  most worth seeing.
- **Canonical data-I/O nodes.** The 19-type frontend vocabulary matches the
  backend enum and includes `dataInput` and `dataOutput`, never historical
  Data Source/Data Sink aliases. Data Input is source-only; Data Output is a
  sink with one upstream input. Neither is a singleton. Their new-node
  defaults contain only the active discriminated branch, and an incomplete
  required choice remains visibly incomplete rather than gaining an
  invented provider or format. Data Output is intentionally
  non-previewable: selecting the sink opens its editor without invoking a
  potentially side-effecting output operation.
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
  mode. A node with zero eligible frames renders no source handle, so a
  source that emits nothing cannot be wired and every persisted API-input
  edge names a frame. The labelled handles are
  mounted on the body's frame rows, so each dot sits at the vertical centre of
  the row naming its frame, and they stay there at every zoom level.
- **API-input body.** An API-input node with at least one
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
  name truncates with the full name available as a tooltip. Frame labels
  use the same semibold primary-text typography as node names.
- **Visual state.** Nodes show: a selection border; a dashed border for
  submodel instances; a "LIVE" badge on live-switch nodes when the active
  data source is live; a status dot for ok/error/running; a warning dot for
  schema warnings (suppressed when status is error); trace-active/dimmed/
  hover-dimmed opacity and glow; and, in the read-only comparison view, a
  diff ring (solid glow for added/removed/changed, dashed outline for
  moved-only). Edge-join status and warning dots stay within the visible
  marker ellipse.
- **Submodel nodes** use the same opaque card, full-width coloured header,
  and frame-row presentation as a full-detail API-input node. The header
  retains the package icon and `SUBMODEL` identity and shows the occurrence
  alias in its right-hand badge. The body does not expose the backing file or
  repeat the name. A canonical occurrence resolves `config.definitionId`
  against the typed definition registry, derives its accessible child count
  from the definition graph, and renders every public input/output row with
  `in__<portId>`/`out__<portId>` handles and the definition's label. Submodel
  cards have no default target handle: every binding must name a declared port.
  A missing or invalid referenced definition is a visible `role="alert"` state,
  never an empty-looking card. A submodel with no exported ports renders no
  source handle.
- **Drilled submodel boundaries** are exactly two composite
  `SUBMODEL_PORT` nodes, one Input and one Output, rather than one marker per
  external source or target. Both boundary headers use the same right-pointing
  arrow to communicate left-to-right graph flow; handle direction remains
  source-right for Input and target-left for Output. In the canonical
  projection, Input lists one row per declared public input port and maps its
  immutable `portId` to one or more ordered internal target endpoints. Its
  child-side executable input name is the sanitised port id; the displayed
  label never becomes parameter identity. Output keeps one target handle;
  every child-to-Output mapping carries an immutable public output `portId` and
  one internal source endpoint. A canonical occurrence contributes the
  sanitised `<alias>__<portId>` name downstream. Parent bindings stay on
  `in__<portId>`/`out__<portId>`. Changing internal endpoints while retaining
  a port id and direction is a compatible shared-definition edit; removing or
  changing the direction of a bound port is rejected atomically across all
  occurrences. Both composite nodes remain visible when empty and keep their
  current canvas positions while structured endpoints change. Reconciliation
  is definition-based and atomic: stale or malformed port bindings fail visibly
  instead of becoming draft child-id edges. When history restores a non-drilled
  snapshot while a drilled view is active, reconciliation is a no-op and the
  parent refs keep their last synchronized state. Each composite records the
  external parent node ids it represents, so flat-graph trace
  steps still highlight the corresponding Input or Output card after the
  per-parent markers are collapsed. Input-to-child and child-to-Output edges
  use the same solid default edge rendering as ordinary main-canvas edges;
  submodel boundaries add no private stroke or opacity styling.
- **Graph state.** One store owns nodes, edges, imports preamble, and nested
  submodel metadata so every frame rename is one coherent transaction.
  Submodel occurrences rebind only their parent edge because definition configs
  already use public port ids. User-meaningful changes push one complete snapshot;
  transient drag motion, external sync, and continuous preamble editing use a
  non-history path. A whole-document load is a distinct atomic transition: it
  installs all four persisted fields, establishes that exact snapshot as the
  saved baseline, clears undo and redo, and advances cache/context versions
  monotonically. Undo/redo restores all four persisted graph fields together.
- **Authored boundary ports survive the client.** `PipelineEdge` extends the React Flow edge shape
  with optional `sourcePort`/`targetPort` fields used while a submodel occurrence occupies the
  visible handle. Response parsing, edge normalisation, graph snapshots, and save payloads retain
  those fields unchanged; they are persisted metadata, not React Flow presentation state.
- **Undo/redo** operates over a single stack that can hold either a graph
  snapshot or a version-control history entry, so a branch switch and a
  graph edit interleave and reverse in the order they actually happened.
  Both undo and redo stacks retain at most 100 entries.
- **Dirty state** is derived, not imperatively toggled: it's a fingerprint
  comparison over persisted nodes, edges, preamble, and submodels against the
  last-saved snapshot, recomputed on every persisted mutation. Undoing back
  to exactly the saved state always reports clean.
- **Graph context.** `GraphProvider`/`useGraph()` exposes a render-stable
  `{allNodes, edges, submodels, preamble}` snapshot to the node inspector's
  subtree. Calling `useGraph()` outside a provider throws immediately.
- **`App.tsx`'s `FlowEditor`** is the orchestrator: it wires ReactFlow's
  event props to interaction hooks, decides which preview pane to show for
  the currently active node, owns local UI state (selected node, context
  menu, dialogs), and gates Save/Commit behind the current
  version-control working-branch state before delegating to the save API.
- **Node CRUD.** Deleting an ordinary node removes it and every edge touching
  it as one atomic undo step. Duplicating offsets the copy's position and is a
  no-op for singleton node types (Quote Input, Quote Response, and Source
  Switch). Generic Duplicate is unavailable for reusable-submodel occurrences,
  and the handler directs callers to Create Instance. The palette, duplicate,
  paste, instance, and context-menu paths consume the same singleton metadata,
  matching the backend save invariant; the instance guard lives in the shared
  handler rather than in each caller's enabled-state check, so a new entry
  point cannot reach an unguarded path. Creating an
  instance of an ordinary node stamps `config.instanceOf` at the ORIGINAL id —
  instancing an existing instance yields a sibling pointing at the same
  original, never a chain, because instance resolution does not walk chains.
  A selected instance whose pointer is dangling, type-mismatched, self-pointing,
  or itself points to another instance is rejected explicitly; the client does
  not walk or repair invalid chains.
  Creating an instance of a canonical `SUBMODEL` instead retains only its
  `definitionId`, allocates a fresh immutable node id and collision-free alias,
  copies presentation defaults, and starts with no boundary bindings as one undo
  step. Deleting a submodel occurrence is owner-aware: an instance copy (valid
  `instanceOf`) deletes exactly like an ordinary node — together with its
  incident edges, as one undo step — from the context menu, the delete handler,
  the window keyboard shortcut, and React Flow's native delete. The definition
  owner (or an occurrence with malformed identity) is refused on every one of
  those surfaces with a visible explanation, because it anchors the shared
  definition; "Dissolve Submodel" is its only removal path. A mixed keyboard
  selection spares owners with an explanatory toast while the rest of the
  selection still deletes; canvas-native deletion never includes an owner
  because owners are stamped non-deletable, and React Flow preserves the
  edges of nodes it does not delete. An explicitly selected boundary edge
  remains deletable — removing a binding is an ordinary edit.
  Auto-layout runs ELK asynchronously, guards against
  overlapping runs from repeated clicks, and re-fits the view once positions
  land. Node-cache cleanup for an ordinary deleted node is deferred one task
  tick past the graph mutation so no component reads a torn state in the same
  render.
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
  type is non-previewable, a preview pane is about to render instead, or a
  structured API-input has not yet acquired its required `tables[]` schema,
  triggers a debounced preview fetch (a longer debounce for Optimiser
  nodes). Selecting that incomplete API-input still opens its editor and
  clears any prior node's preview, but does not issue a predictably failing
  execution request; Infer Tables followed by an explicit refresh is the
  normal first-preview flow.
- **Pipeline load and save.** The pipeline loads once on mount with a
  cold-start retry policy; a backend-contract violation in the response
  throws before it reaches the graph, surfacing as a load-failure toast
  rather than a downstream crash. A node whose persisted `type` or
  `data.nodeType` is outside the canonical vocabulary is rejected at that parser boundary;
  there is no legacy renderer or client migration. Save validates config references and
  edge-join wiring first — a broken edge-join blocks the save outright with
  an error toast, while broken config references only warn. A concurrent
  second save can never let an older response's `markSaved` clobber a newer
  one's. Initial canvas seeding and every successful whole-pipeline
  load/switch use the same full-snapshot load action; a missing preamble or
  submodel collection becomes its canonical empty value, so no persisted
  field, saved baseline, or history entry can survive from the prior
  document. Backend edge metadata that is not a React Flow UI-only field,
  including authored submodel `sourcePort`/`targetPort`, remains present in
  the captured save snapshot and outgoing graph payload. Non-editable
  module-level preserved blocks and `source_revision` live in request-facing
  refs alongside `sourceFileRef`: load records both, save sends the preserved
  blocks unchanged, and a successful save replaces the revision with the
  committed response value.
- **Preview fetching.** Selecting or refreshing a node debounces, then
  fetches its preview; a cache hit for the same structural version, source,
  and row limit paints instantly and skips the network call, otherwise
  cached data paints immediately while a fresh fetch runs in the
  background. A schema change cascades to every reachable downstream node
  (bounded concurrency, diamond-shaped fan-in deduplicated so a shared
  child previews once), each terminating in a definite ok/error state even
  if the graph structure changes mid-flight.
- **Column stash source identity.** Cached editor columns and schema warnings
  carry the source under which they were captured. Initial mount and a source
  change discard any stash from another or unknown source, returning that node
  to its ordinary not-yet-previewed state; the next preview lazily refills the
  required upstream columns. The preview path independently rechecks source
  identity so it remains correct while the state update is still settling.
- **Live code sync.** External edits to a pipeline's `.py` file arrive over
  WebSocket and replace the in-memory graph — but never while the user has
  unsaved local edits, where a banner asks them to reload or discard first.
  Once either side supplies a source identity, both sides must resolve to the
  same file; one-sided or foreign updates are ignored. Each accepted
  update advances a generation before asynchronous layout, so a later graph
  update or parse error permanently supersedes older work. Incoming edges
  are checked against the live node/handle set: unresolved edges remain in
  graph state and the save snapshot, a bounded warning names representative
  problems, and only the valid partition guides layout. Finite incoming
  positions, including `{x: 0, y: 0}`, remain authoritative; layout fills
  only missing/non-finite positions. Nodes, edges, preamble, and the required
  `submodels` value are applied as one guarded update, with the backend's
  explicit `null` empty-collection representation normalised to `{}` and the
  submodel, preserved-block, and source-revision refs updated before
  `markSaved`; any apply failure restores the graph fields and request-facing
  refs. An omitted submodels field or missing live `source_revision` fails
  loudly.
  A resync on reconnect sends the last-applied graph fingerprint so the
  server can skip re-sending an unchanged graph.
- **Submodel navigation.** Drilling resolves a canonical occurrence from its
  node type and `{definitionId, alias}` config, loads the shared definition by
  definition id, verifies any returned identity, and builds boundary nodes from
  that definition's structured ports and the selected occurrence's parent
  bindings. Load, projection, and ELK layout must all finish before the view
  stack, graph, source-file refs, or selection mutate; any failure leaves the
  parent view byte-for-byte unchanged and reports one error toast. A successful
  frame records both `instanceId` and `definitionId`, so two occurrences of one
  definition navigate and return independently, and announces when edits affect
  more than one occurrence. Breadcrumb navigation restores the exact saved
  node/edge state for any ancestor level rather than re-fetching it, using the
  backend's recorded submodel file instead of reconstructing a path from a
  display label.
- **Submodel creation is a toolbar action or a keyboard shortcut, not a
  context-menu item.** Selecting two or more nodes and either pressing Ctrl+G
  or clicking the toolbar's Submodel button opens `SubmodelDialog` for the
  name; there is no "Group as Submodel" right-click entry. The toolbar button
  is a second entry point, not a second policy: the same three rules gate both
  triggers (editable
  context, main canvas, 2+ nodes) and both answer a refusal with the same
  toast. The toolbar pair also exposes Instance, which stamps `instanceOf` for
  a single selected non-singleton node — the context menu offers Create Instance
  only for submodel occurrences. Singleton availability is derived from the
  same canonical node-type metadata used by the shared handler; an attempted
  unavailable action remains explainable without being presented as enabled.
  The context menu's entry for an existing submodel node
  reads "Dissolve Submodel", not "Ungroup Submodel". Clicking a submodel node
  opens the standard node inspector but fetches no preview — submodel is a
  non-previewable node type — rather than the output-port summary table that
  was once proposed and never built. Create and dissolve are main-canvas
  operations: while a drilled submodel view is active both handlers refuse to
  run and toast an instruction to return to the main pipeline — the same
  client-side gate as save — instead of sending a mis-scoped request that the
  backend would reject with a misleading revision `409`. Create and dissolve
  send the retained parent `source_revision` as `base_revision` and include
  the untouched preserved blocks. Transform responses leave that persisted
  revision unchanged until the user saves. A `409` leaves the local graph
  unchanged and tells the user to reload. Dissolve also installs the returned
  graph's merged preamble and preserved blocks so support code contributed by
  a hand-authored child survives the next manual save. Its response and toast
  expose no child-file lifecycle compatibility state.
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
- **Combined graph mutations close an undo-atomicity bug class.** Updating
  nodes and edges as separate history actions for one delete, paste, or cut
  would require two undos; one complete snapshot makes each gesture reverse
  as one action.
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
  beyond what's needed). Whole-document loads are deliberate identity
  boundaries: they recompute all fingerprints and increment the structural
  and panel-context versions rather than resetting or conditionally reusing
  an earlier number, including when only nested submodel metadata changed.
  This prevents preview/result caches from treating a different loaded
  document as an earlier in-memory graph.
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
  time.** Every ordinary incoming edge contributes its API frame label or
  sanitised source label. A canonical drilled Input contributes its sanitised
  public port id, and a canonical occurrence output contributes sanitised
  `<alias>__<portId>`. A connection whose derived name duplicates an existing
  executable input on the target is refused with a named toast, mirroring the
  backend's save-time `ParseError`. The
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
- **One frame-label derivation, one identity.** One ordered list of eligible
  frame labels (with no minimum count) drives the
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
- **Missing graph context throws instead of defaulting to an empty graph**, so
  a misconfigured mount surfaces immediately through
  the enclosing `ErrorBoundary` instead of silently rendering editors against
  no data (which would hide broken references and stale column sets).
- **Cache cleanup on delete is deferred by one task turn.** Synchronous
  cleanup can run before React commits the node-removal render and expose a
  torn node/cache view to another subscriber; deferral lets the graph commit
  first.
- **The preview cascade snapshots row limit, active source, and chunk size
  once at fetch time**, then closes over those values for every node it
  previews in that cascade — reading the live settings refs again partway
  through would let a user flipping the active data source mid-cascade split
  one logical preview across two sources.
- **Downstream propagation is bounded and deduplicated**, not a naive
  "preview every descendant": a diamond-shaped fan-in
  previews its shared child exactly once (waiting for every changed parent
  first), and a fixed request bound prevents a wide fan-out from saturating
  the backend.
- **Save concurrency uses request ordering, not a boolean in-flight gate.**
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
- **Version comparison strips codegen-derived contract metadata before
  comparing node content.** The I/O contract shifts whenever an *unrelated*
  node is added elsewhere in the graph; without stripping it, every node
  would read as "changed" whenever any other node's shape changed,
  defeating the point of the diff.
- **Column-schema fingerprints encode field boundaries unambiguously**, so a
  name or dtype containing a separator cannot collide with another schema.
- **Column stashes are tagged with the source they were captured under,
  not just left to go stale silently.** Without source identity, switching
  from source A to B could leave editors reading A's columns as current.
  Tagging the stash with its capture source and invalidating on mismatch
  closes the same class of "cached result silently outlives the source it
  was computed for" bug that motivated widening `useNodeResultsStore`'s
  solve/train staleness key (see
  [frontend-shared](../frontend-shared/high-level.md)) — stripping the
  stash and re-triggering the existing lazy gap-fill was preferred over
  keeping a per-node/per-source cache, since editors already tolerate
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
  `useWebSocketSync` (`/ws/sync`). `usePipelineAPI` installs a successful
  whole-document response through the atomic snapshot-load action and calls
  `markSaved()` only after successful saves; `useWebSocketSync` installs an
  accepted external document through the same atomic clean-snapshot boundary.
- [frontend-shared](../frontend-shared/high-level.md) owns typed HTTP/WebSocket
  transport; this component owns canvas orchestration such as debounce, cache,
  cascade, retry gating, and reconnect/backoff.
- [tracing](../tracing/high-level.md) supplies active/dimmed/hover/value/motion
  state that nodes render; the canvas does not compute trace lineage.
- [submodels](../submodels/high-level.md) owns backend occurrence construction
  and public-port derivation; this component consumes its endpoints and owns the
  browser's drill-in/out stack and port-marker presentation.
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
- Edge-join input-swap failures surface as a named error toast rather than
  throwing — this is a
  best-effort canvas convenience action, not a data-integrity operation.
- Edge-join *insertion* failures (self-join, cycle, missing source/target
  node, drop point not over an edge) are surfaced as a named error toast; no
  partial node/edge mutation is applied. Candidate calculation uses those
  same compatibility reasons but does not toast while the pointer merely
  moves: invalid edges remain visually and programmatically unmarked. The
  release path revalidates and toasts an actionable rejection where an edge
  was actually targeted. Every connection-end path clears the active
  candidate before it can return or throw, and pointer exit clears the
  feedback without ending the underlying connection gesture.
- A palette drop parses its drag-carried config defensively: a malformed
  payload, or one that is not a plain JSON object, produces a named error
  toast and creates no node; it never falls back to an empty config.
- The initial pipeline load throws on any drift from the expected backend
  contract; its load boundary turns that into a load-failure toast
  rather than letting a malformed response reach the graph as
  `undefined`-shaped nodes.
- Saving never rejects to the UI — every failure path is caught and surfaced
  as an error toast, and the action reports failure so Commit can stop safely
  before opening its milestone dialog.
- A preview request or its downstream cascade member that fails with an
  aborted/superseded error is treated as expected cancellation (no toast);
  any other failure shows a `warning` toast naming the failing node, and a
  client-side preview *timeout* additionally shows an `error` toast.
- Live sync toasts on WebSocket construction errors, unparsable message JSON,
  and any error raised while applying an
  incoming `graph_update`, including an omitted or invalid non-object
  `submodels` value (explicit `null` means an empty map); a failed apply
  attempts to roll nodes, edges, submodels, and preamble
  back to their pre-update snapshot (best-effort — a rollback failure is
  swallowed so it doesn't mask the original error in the toast). A session-expiry
  close code stops reconnect attempts and calls
  `notifyHauteSessionExpired` instead.
- Submodel create, drill-in, and dissolve actions catch their own failures
  and surface a named error toast; graph mutation occurs only after a
  successful response, so a failed call never leaves a partial graph.
- Version comparison catches a failed historical-pipeline fetch and renders a
  dedicated error state (message plus a "Back to editor" button) in place
  of the canvases, rather than crashing the comparison view.
