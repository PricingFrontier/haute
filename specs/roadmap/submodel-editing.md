# Submodel editing roadmap

## Scope

How submodels behave is specified in [the submodels specification](../submodels/high-level.md)
and [its low-level design](../submodels/low-level.md); how the canvas shows and edits an open
submodel in [the graph canvas specification](../frontend-graph-canvas/high-level.md) and
[its low-level design](../frontend-graph-canvas/low-level.md); and the requests that run a
graph in [the server API design](../server-api/low-level.md). The packages below replace how
the editor represents an open submodel and how requests and answers name nodes.

Today three versions of the graph are in play while a submodel is open:

1. **The canvas graph:** a stand-in made of the definition's nodes plus synthetic Input and
   Output cards. Every panel reads it.
2. **The real pipeline:** set aside on entry and rebuilt from the stand-in by a passive effect
   after each commit (`reconcileCanonicalSubmodelBoundaryState`), so it lags the canvas by one
   commit, and an edit the rebuild refuses stays on the canvas until the user leaves, then is
   dropped.
3. **What the server runs:** the flattened pipeline, where a node inside occurrence `pricing`
   runs as `submodel_runtime/pricing/<local id>` and a reused copy's nodes are relabelled.

Every request and answer translates between them in the browser: a bridge
(`nodeRequestTarget`, a module-level scope, a one-tick wait) carries panel requests, and
Preview and Trace have their own translations. Defects of this kind have been found in
thirteen panels, in node status colours, in the cached-data list and in previews of a frame
coming out of a submodel. The packages replace the three versions with one authored document,
a view of it, and server-owned execution addresses.

## Target model

The end state the packages build. Each package specifies its part in the owning component
specification before code changes.

- **One authored document.** The graph store owns the document: root nodes and edges, every
  submodel definition (its graph, ports and file, with its children's persisted layout),
  the preamble and the global constants. Opening a submodel sets `focus` to the occurrence
  (instance id and definition id); it does not copy anything.
- **The canvas is a projection.** The nodes and edges React Flow draws are a memoised pure
  function of (document, focus, view state, execution annotations). Unchanged nodes keep their
  object identity. At the root the projection is the root graph; with a focus it is the
  definition's children plus the Input and Output cards.
- **Three kinds of state, kept apart.** The authored document (including persisted layout)
  enters history and is saved. View state (focus, selection, viewport, drag presentation and
  the cards' positions) does not. Execution annotations and results (columns, schema warnings,
  notes, statuses, previews, train and solve results) live outside the document, keyed by the
  occurrence-aware address of the node they describe, and never enter a definition.
- **Edits are document commands.** Each edit says what it changes: update a node's config,
  rename it, add or delete nodes, connect or disconnect an edge, bind or unbind a public port,
  declare or retire a port, move nodes. A command validates and applies one document
  transaction inside one store update, or changes nothing and says why. A command addressed
  to a read-only occurrence (`instance_of`) is refused in the store. Moving nodes takes the
  existing position-only fast path: no projection of unchanged nodes, no fingerprint, no
  request invalidation.
- **History is the document's.** Every edit is one undo step wherever it is made, as on the
  main canvas. History is one chronological list of document transactions, each recording the
  focus it was made under. Undo and Redo restore the document and show that focus: they switch
  the view to the occurrence (or root) the step was made in, and show the root, with a notice,
  when that occurrence no longer exists. Opening and leaving a submodel are navigation, not
  steps.
- **Addresses, not ids.** Nodes are named by a structured address end to end in the editor:
  a **node address** `{occurrence, node}` (occurrence `null` at the root; an occurrence itself
  is the root node `{null, <instance id>}`), and a **frame target** — a node's output port, or
  a declared input or output port of an occurrence. The server owns the **runtime node key**
  (`submodel_runtime/...`): it resolves addresses to keys through the expansion's own mappings
  and labels every node it names in an answer with its address. The browser never encodes or
  parses a runtime key.
- **Two versions, never restored.** The **semantic generation** advances with every accepted
  semantic command, Undo, Redo, navigation and reload, and never with a layout-only change; it is
  monotonic, and history never restores it. It fences prepared document transactions and
  publication bound to a view (an answer shown in the panel that asked). It does not fence
  background jobs: a training or solve start acknowledgement and its outcome are admitted under
  the document, target and job identity they were captured with, whatever the user has navigated
  to since, and whether the result is still current is checked separately.
  **Request freshness** compares semantic content (the structural fingerprint, which excludes
  layout), so two documents with identical content are equally fresh even though their
  generations differ.
- **One request gateway.** The only way to send a request that runs the graph is a gateway
  that captures one immutable snapshot: the document, its identity and semantic version, the
  target, the data source and the operation's inputs. Its scope comes from the store and the
  view context, not a module variable; a read-only comparison editor has no scope and sends
  nothing.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SUBE-02 | Planned | P1 | The store owns one authored document and a focus; the canvas is its projection; edits are document commands; history is one list of document steps, one step per edit, wherever made. |
| SUBE-03 | Planned | P2 | Every request that runs the graph goes through one gateway that snapshots the document, target and source together; no panel builds a request graph. |
| SUBE-04 | Planned | P2 | Results, annotations and caches are keyed by occurrence-aware address, so an owner and its read-only copies never show each other's previews, columns or training results. |
| SUBE-05 | Planned | P2 | Requests name nodes and frames by address, the server resolves them and labels every node an answer names with its address; the browser's runtime-id code is removed. |
| SUBE-06 | Planned | P2 | Saving works inside a submodel, and a reload keeps the open submodel when it still exists. |

## Planned improvements

### SUBE-02 — One authored document, a focus, and document commands
**Why:** The real pipeline is rebuilt from the canvas stand-in by a passive effect after each
commit. A request sent from a child's effect sees it one edit behind, which is why the bridge
needs a one-tick wait. An edit the rebuild refuses raises a toast but stays on the canvas, and
leaving the submodel restores the last rebuilt pipeline, dropping it. Undo inside a submodel
stores only the canvas, so the canvas carries history fields that let the rebuild recover
document facts (`ports[].parentEdges`, `_parentEdgeOrder`, `_parentBindingScope`,
`_parentOutputState`), and the rebuild throws without them. Leaving a submodel collapses the
visit into one undo step. The rebuild copies the screen's nodes, annotations included, into the
shared definition. While an output-cardinality edit waits for server names, any re-render
publishes the uncommitted candidate into the refs requests read.

**Plan:** Specify the model first in the submodels and graph-canvas high- and low-level
designs (the Target model above), replacing the rules for the submodel view's own history, the
collapsed visit and the reconciled parent graph.
0. The projection asks the server nothing, so it can be redrawn from any document (Undo
   included): a boundary card's identity is a fixed function of its direction and its declared
   ports (its label is its name, and each port, already a canonical identifier, names its own
   frame), built by the projection and held to the server's resolver by a shared fixture.
   Opening a submodel therefore commits at once.
1. The store holds the document and `focus`; `nodes`/`edges` are the projection, built from
   the existing pure projection (`buildSubmodelViewGraph`) without its history carriers, and
   memoised per node. This package defines the shared contracts later packages consume: the
   `NodeAddress` and `FrameTarget` types (Target model) in one frontend module, and the
   execution-annotation interface (write and read annotations by address; the projection merges
   them into the nodes it draws). SUBE-03 and SUBE-04 use them as defined here; SUBE-05 replaces
   the frontend types with the generated server contracts of the same shape.
2. Every writer becomes a command, applied in one store update. As a migration step the
   store may accept the existing setters while focused, mapping a child node or internal edge
   change by id into the definition (a total mapping) and routing every card change through
   an explicit boundary command (bind, unbind, declare, retire, rename a port); the generic
   rebuild from a view is not kept as an editing path.
3. A refused command leaves document and canvas unchanged and shows its reason. An unfinished
   pipeline (unconnected ports, unbound inputs) is a valid document, not a refusal.
4. One chronological document history, one step per command or gesture, each step recording
   its focus (see Target model). Delete the submodel view's own undo stacks, the collapsed-visit
   entry and the per-view saved baselines; the dirty check compares the document with the
   saved document.
5. An edit that needs server-assigned names (taking a definition from one public output to
   several or back) is a prepared transaction: build the candidate's semantic change, resolve its
   identities, and commit only if the semantic generation is still the one it was prepared at; a
   superseding semantic edit, Undo, Redo, navigation or a reload advances the generation and
   discards it with a notice, even when the document returns to identical content. Layout-only
   changes during resolution do not invalidate it, and committing applies the semantic change on
   top of the current document, keeping the layout changes made meanwhile. The candidate is never
   visible to the projection, history or requests.
6. Delete `parentGraphRef`, the passive reconcile effect, the history carriers and the
   App-level `graphRef`/`submodelsRef` mirrors; readers use the store. The bridge's scope
   (`setDrilledRequestScope`) reads the store's document synchronously, and its one-tick wait
   (`afterCommitEffects`) is deleted with its callers here; SUBE-03 then replaces the bridge.

**Acceptance:**
- Opening a submodel shows it with no server round trip, and its cards are named as the server's
  resolver names a `submodelPort` node.
- An edit inside a submodel followed by a request sent from an effect in the same commit sends
  the edited document, with `afterCommitEffects` removed.
- A refused edit changes neither the document nor the canvas, and leaving the submodel keeps
  every accepted edit.
- Undo inside and outside a submodel reverts exactly one edit per step, Redo replays it, and
  undoing a step made in another view switches to that view; undoing past the creation of the
  open occurrence shows the root with a notice. An edit made in occurrence A, undone after
  navigating to B, is redone from anywhere with the view focused on A.
- An edit addressed to a read-only occurrence is refused in the store with its reason.
- For an output-cardinality edit waiting on identity resolution: a re-render publishes nothing;
  moving a node meanwhile keeps the move and the edit still commits; navigating away and back
  discards it; Undo then Redo back to identical content discards it; a superseded resolution
  completing late changes nothing.
- Dragging a node in a submodel of 200 nodes re-renders only that node and computes no
  fingerprint (render-count test).
- Annotations written by a preview inside one occurrence do not appear in the definition.
- Property tests: projecting the document and applying a no-op command returns the same
  document; applying a command and projecting equals the expected view.

**Dependencies:** None. Deliver with SUBE-03 and SUBE-04 in one phase, so requests and
results move onto the document once.

**Evidence:** `frontend/src/hooks/useSubmodelBoundaryEditing.ts`;
`frontend/src/utils/canonicalSubmodelBoundaryEditing.ts::reconcileCanonicalSubmodelBoundaryState`;
`frontend/src/utils/submodelViewGraph.ts::buildSubmodelViewGraph`;
`frontend/src/hooks/useSubmodelNavigation.ts`; `frontend/src/stores/useGraphStore.ts`;
`frontend/src/hooks/useGraphCommitController.ts`;
`frontend/src/utils/submodelDocument.ts::liftSubmodelCanvas`;
`frontend/src/utils/buildGraph.ts::documentGraphPayload`.

### SUBE-03 — One request gateway
**Why:** Panels built request graphs from the canvas they hold; inside a submodel every such
request was refused until the bridge (`nodeRequestTarget`) moved thirteen of them, and nothing
stops a new panel building its own. The bridge's scope is a module variable that editors in the
version-comparison view can reach although they mount on an empty graph, and when its scope does
not resolve it sends the stand-in graph instead of failing. Preview and Trace keep their own
translations.

**Plan:** Specify the gateway in the frontend-shared low-level design. One module owns request
construction: `prepareNodeRequest(scope, target, inputs)` and `preparePipelineRequest(scope,
inputs)` return a branded prepared request holding the document payload, document identity and
semantic generation, the target, the data source and the operation inputs, captured together.
A frame read may name several ports of one node, read in one execution, as the Workbench prices
every table of its sample at once ([workbench specification](../workbench/high-level.md)). Raw
transport functions that take a graph accept only prepared requests; `buildGraph` becomes
private to the gateway. The scope comes from the store and the view context; the comparison
view's provider supplies none, and a request without a scope is refused, not sent. Until
SUBE-05 the gateway translates the target to a runtime id with the existing helpers; that
adapter is the only place they are called. Move every panel, Preview, Trace, the Workbench
pricing path and Pipeline settings' cached-data list onto it, and delete `nodeRequestTarget.ts`
with its scope.

**Acceptance:** Type-checking fails for any call that passes a graph it built itself to a
request function. Every request inside a submodel carries the whole document and the open
occurrence's target, from panels, Preview, Trace and the cached-data list alike. An editor in
the comparison view sends no request. A two-table Workbench preview runs the pipeline once. A
test per request family asserts the captured snapshot (semantic generation, target, source)
belongs to one render.

**Dependencies:** SUBE-02.

**Evidence:** `frontend/src/utils/nodeRequestTarget.ts::nodeRequestTarget`;
`frontend/src/utils/nodeRequestTarget.ts::setDrilledRequestScope`;
`frontend/src/components/ReadOnlyNodeConfig.tsx`; `frontend/src/hooks/usePipelineAPI.ts`;
`frontend/src/hooks/useTracing.ts`; `frontend/src/api/client.ts`.

### SUBE-04 — Results and caches keyed by occurrence-aware address
**Why:** A shared definition's owner and its read-only copies show the same local node ids.
The preview cache is keyed by that id and shown before its freshness is checked, so the owner's
rows can appear in the copy. Trained-job handles are stored by local id, and their lineage is a
digest of the whole document, which is the same for owner and copy, so the copy can restore the
owner's training result as current. Column annotations are copied into the shared definition.

**Plan:** Specify result identity in the frontend-shared and graph-canvas low-level designs.
Separate **which result** (document identity plus the node address or frame target) from
**whether it is current** (data source, frame, the relevant semantic content, data generation
and operation inputs). Key the preview cache, node results, training and solve jobs, trained-job
handles, the node-data store, output-write state, the column cache and the execution annotations
by address; check validity before showing any cached result. The same applies to state held
outside stores: hook dependencies and hook-local answer and error state (the whole-data answer's
identity), Refresh and Stop registrations, and the publication of every response, which stays
attached to the document and target it was captured with; whether it is still current is a
separate check. A background job (training, a solve) keeps running when the user navigates away,
and its result stays attached to the occurrence it was started in. Keep legitimate sharing of
immutable data (snapshots the server shares between occurrences) below the presentation layer.

**Acceptance:** Training a node in the owner and opening the same node in a read-only copy
shows no result for the copy; previews of the same child in two occurrences show their own rows
with no flash of the other's; columns from one occurrence's preview never appear in another or
in the saved definition; a stale cached preview is never shown as current. With one editor kept
mounted while the view switches from the owner to a copy with the owner's request still pending,
the copy is queried afresh, the owner's late answer or error is not shown in the copy, and Refresh
and Stop act on the copy's request only. Starting training in occurrence A, navigating to B
before the start is acknowledged, and letting training complete keeps the job tracked, shows none
of A's result in B, and shows A's result, with its freshness, on returning to A. Tests cover each
store and each of these hook paths with two occurrences of one definition.

**Dependencies:** SUBE-02, SUBE-03.

**Evidence:** `frontend/src/hooks/usePipelineAPI.ts`;
`frontend/src/utils/trainedJobHandles.ts::trainingLineage`;
`frontend/src/panels/modelling/useTrainedJobRestore.ts`;
`frontend/src/stores/useNodeResultsStore.ts`; `frontend/src/stores/useNodeDataStore.ts`.

### SUBE-05 — Server-resolved addresses
**Why:** The browser duplicates the server's execution knowledge: it encodes runtime ids
(mirroring Python's quoting), maps the Input card to the parent node feeding it, and maps
runtime ids in answers back to the canvas, in three places. Where it doesn't, answers are
wrong on screen: node statuses inside a submodel are keyed by runtime id and never colour the
canvas; the cached-data list shows `pricing/<encoded id>`; a frame coming out of a submodel
occurrence, previewed from the Output editor, asks for the occurrence id, which the flattened
graph lacks (404); a card fed by another submodel's output is not mapped; execution diagnostics,
timings, the Explore producer card and `input_node_ids` are untranslated; error prose embeds
runtime ids.

**Plan:** Specify the address contracts in the server-api and submodels low-level designs,
replacing "the editor never sends an occurrence as a preview target".
1. Request contracts name targets explicitly (required fields, no defaults): a node address
   `{occurrence, node_id}` for operations on a node (train, solve, estimates, write, node data,
   pivots, banding, rating levels), and a frame target — `{kind: node, occurrence, node_id,
   port_label}`, `{kind: occurrence_input, occurrence, port}` or `{kind: occurrence_output,
   occurrence, port}` — for reads of a frame (preview, the Output editor's frames, columns). A
   node frame read may list several port labels of one node, read in one execution (the
   Workbench's tables). Trace's seed plan stays an opaque execution receipt: its runtime keys
   and generation ids are echoed exactly as the preview returned them, so translating the
   primary target never substitutes a newer snapshot, and an expired generation keeps the
   existing explicit refusal ([tracing specification](../tracing/high-level.md)).
2. One server function prepares an execution from a request: it flattens the document and
   resolves the target through the expansion's own mappings (the id map and the expanded
   endpoint of each occurrence port, factored out of `expand_submodel_instances`), translating
   the handle as well as the node, and returns the flat graph, the runtime key, the port and the
   provenance. Supersession keys and worker arguments use the resolved key and port. An
   unbound port or unknown node is a named refusal.
3. Every answer field that names a node is typed as an opaque runtime key, and each answer
   carries the address of every key it names, generated from the provenance (never by parsing
   strings); jobs keep their provenance so status reads can label keys after navigation or
   reload. A schema test fails on any node-naming field not so typed, and the generated
   TypeScript contracts brand the key so an answer can only be read through its addresses.
4. Answers also carry **frame provenance**: for every occurrence port in the executed graph,
   either `bound` with the runtime endpoint (key and handle) it resolves to, keeping every alias
   when several ports resolve to one endpoint, or `unbound` when no parent edge supplies it, as a
   declared input may legitimately be. Building provenance never fails or invents an endpoint
   for an unbound port; only a request whose target is that port is refused, by name. Schema
   information for an occurrence's Input card comes from it, including ports that were not the
   requested target, and a bound port whose columns are missing is shown as missing data, not as
   unbound.
5. **Persisted artifacts carry their own provenance.** A cached snapshot records, when it is
   written, its document origin (pipeline source file), the address of its owner at that time
   and the owner's label. The cached-data list describes each owner either as a node of the
   executed graph (with its current address) or as a historical or foreign owner (source file,
   recorded address and label); it never resolves an owner against today's graph or parses its
   key, and owners are keyed by origin and key together, so two pipelines with the same node
   ids stay distinct.
6. Error messages name nodes by label and carry structured node references.
7. The browser maps addresses to the canvas with its projection rules (a child of the focused
   occurrence, the card of another occurrence, a root node; statuses of a collapsed occurrence
   aggregated on its card). Delete the browser's runtime-id encoding and parsing, the Input card
   feed mapping, `previewSchemaKey`, Trace's child-to-submodel maps and the cached-data list's
   prefix stripping. Migrate request and answer handling together, one operation family at a
   time.

**Acceptance:** Inside a submodel, node status colours, schema warnings and timings show on the
right nodes; the cached-data list names submodel nodes by label and submodel; previewing a frame
out of an occurrence from the Output editor (root view) shows that frame; a request reading an
Input card fed by another submodel's output reads that output's producing node and port; two
public outputs of one producer node preview their own frames; a reused copy's training and
preview target the copy. Previewing a child of occurrence A while two of A's input ports receive
different frames from one producer inside occurrence B gives both Input-card rows their own
columns. Previewing a runnable child of an occurrence with an unused, unbound public input
succeeds and marks that input unbound; requesting that input directly is refused by name. A cached submodel node whose occurrence is then deleted is still listed, after a reload
and a server restart, under its recorded label and pipeline; two pipelines with identical node
ids list distinct, correctly labelled owners. A two-table Workbench preview runs once. A preview
inside a submodel followed by Trace traces the preview's own generations, or gives the expiry
refusal. Error toasts name nodes by label. Server tests cover resolution for root nodes,
children of an owner and of a copy, both card kinds, several port labels, and refusals; a
contract test covers every node-naming answer field.

**Dependencies:** SUBE-03.

**Evidence:** `frontend/src/utils/submodelRuntimeTarget.ts::encodeRuntimeIdPart`;
`frontend/src/utils/submodelRuntimeTarget.ts::submodelOutputPreview`;
`frontend/src/hooks/usePipelineAPI.ts::previewSchemaKey`;
`frontend/src/hooks/usePipelineAPI.ts::canvasNodeStatuses`;
`frontend/src/components/PipelineSettingsModal.tsx::displayNodeName`;
`frontend/src/panels/editors/OutputEditor.tsx`;
`src/haute/_flatten.py::flatten_executable_graph`;
`src/haute/_submodel_instances.py::expand_submodel_instances`;
`src/haute/_submodel_instances.py::qualified_runtime_node_id`;
`src/haute/routes/pipeline.py`; `src/haute/routes/cache.py`; `src/haute/_node_snapshots.py`;
`frontend/src/workbench/priceSample.ts::priceSample`; `frontend/src/hooks/useTracing.ts`.

### SUBE-06 — Save and reload inside a submodel
**Why:** Saving inside a submodel is refused, and a reload returns to the main pipeline; both
follow only from the canvas not being the document.

**Plan:** Specify in the submodels and graph-canvas designs. Save serialises the document whatever
the focus, and records that
exact document as saved. A clean external reload replaces the document atomically, keeps the
focus when the same occurrence and definition still exist, and otherwise shows the root with a
notice; dirty-document conflict handling and execution fences stay as they are.

**Acceptance:**
- Save inside a submodel saves the whole pipeline and leaves the view clean; an edit accepted
  while a save is in flight stays dirty after the save completes (the save acknowledges the
  exact document it sent).
- A clean reload that changes the open definition's contents keeps the focus, and the canvas and
  the open panel show the new contents; an external change to another file keeps the open
  submodel.
- Deleting the open occurrence externally, or a reload in which its instance id names a
  different definition, shows the root with a notice.

**Dependencies:** SUBE-02.

**Evidence:** `frontend/src/hooks/useSubmodelNavigation.ts`;
`frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/hooks/useWebSocketSync.ts`.
