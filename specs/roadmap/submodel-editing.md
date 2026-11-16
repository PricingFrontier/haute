# Submodel editing roadmap

## Scope

How submodels behave is specified in [the submodels specification](../submodels/high-level.md)
and [its low-level design](../submodels/low-level.md); how the canvas shows and edits an open
submodel in [the graph canvas specification](../frontend-graph-canvas/high-level.md) and
[its low-level design](../frontend-graph-canvas/low-level.md); and the requests that run a
graph in [the server API design](../server-api/low-level.md). The packages below replace how
the editor represents an open submodel and how requests and answers name nodes.

The graph store owns one authored document: an open submodel is a focus on it, the canvas is
its projection, every request that runs the graph goes through one gateway, and results are
kept under the occurrence they belong to. Three things remain:

1. **Runtime ids in the browser.** The server runs the flattened pipeline, where a node
   inside occurrence `pricing` runs as `submodel_runtime/pricing/<local id>`. The gateway
   encodes those ids and reads answers back through them, and the cached-data list and
   previews of a frame coming out of a submodel are still wrong on screen (SUBE-05).
2. **Save and reload.** Saving inside a submodel is refused, and a reload returns to the main
   pipeline (SUBE-06).
3. **Boundary actions.** Connecting to a card, removing an input and the other boundary
   actions still rebuild a definition from the canvas
   (`reconcileCanonicalSubmodelBoundaryState`), reading parent bindings back from fields on
   the Input and Output cards (SUBE-07).

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
| SUBE-05 | Planned | P2 | Requests name nodes and frames by address, the server resolves them and labels every node an answer names with its address; the browser's runtime-id code is removed. |
| SUBE-06 | Planned | P2 | Saving works inside a submodel, and a reload keeps the open submodel when it still exists. |
| SUBE-07 | Planned | P2 | Boundary actions edit the document directly: the Input and Output cards carry no parent bindings, and no edit rebuilds a definition from the canvas. |

## Planned improvements

### SUBE-05 — Server-resolved addresses
**Why:** The browser duplicates the server's execution knowledge: it encodes runtime ids
(mirroring Python's quoting), maps the Input card to the parent node feeding it, and maps
runtime ids in answers back to the canvas, in three places. Where it doesn't, answers are
wrong on screen: the cached-data list shows `pricing/<encoded id>`; a frame coming out of a submodel
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

**Progress:** The server's resolution, the labelled answers, frame provenance, the preview
family, the trace, the node-data family, modelling, the optimiser, Data Output and the
cached-data list and the execution diagnostics are in place; error messages by label follow,
then the browser's runtime-id code is deleted.

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

**Dependencies:** None.

**Evidence:** `frontend/src/utils/submodelRuntimeTarget.ts::encodeRuntimeIdPart`;
`frontend/src/utils/submodelRuntimeTarget.ts::submodelOutputPreview`;
`frontend/src/hooks/usePipelineAPI.ts::canvasNodeStatuses`;
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

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useSubmodelNavigation.ts`;
`frontend/src/hooks/usePipelineAPI.ts`; `frontend/src/hooks/useWebSocketSync.ts`.

### SUBE-07 — Boundary actions as document commands
**Why:** Canvas writes inside a submodel change the document directly, but the
boundary actions (connect to or from a card, remove an input, delete a child a port uses,
remove a boundary edge) still run the generic rebuild of a definition from the canvas
(`reconcileCanonicalSubmodelBoundaryState`). The rebuild reads the parent bindings back from
fields the projection writes onto the cards (`ports[].parentEdges`, `_parentEdgeOrder`,
`_parentBindingScope`, `_parentOutputState`), so the cards carry document facts and the store
has to strip what the rebuild copies from the canvas.

**Plan:** Specify the boundary commands in the submodels low-level design: bind and unbind a
public input, declare and retire a port, rename a port, and route a port to a child. Each
reads the document (definition, occurrences and their parent edges), applies one transaction
and returns the new document, or refuses with its reason; the projection draws the cards from
the document with no binding fields beyond what the cards display. Move each caller of the
rebuild in `useSubmodelBoundaryEditing` onto a command, then delete the rebuild, the carriers
and the store's stripping of boundary results.

**Acceptance:**
- No card's data holds a parent edge, an edge order, a binding scope or an output state.
- Every boundary action is one undo step, refused whole with its reason when it cannot apply,
  and leaves no canvas object in a definition.
- The existing boundary suites (`frontend/src/hooks/__tests__/useSubmodelBoundaryEditing.test.ts`,
  `frontend/src/utils/__tests__/submodelBoundaryEditing.test.ts`) pass against the commands,
  including parent edge order and output identities across Undo and Redo.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/canonicalSubmodelBoundaryEditing.ts::reconcileCanonicalSubmodelBoundaryState`;
`frontend/src/utils/submodelViewGraph.ts::captureParentOutputState`;
`frontend/src/hooks/useSubmodelBoundaryEditing.ts`.
