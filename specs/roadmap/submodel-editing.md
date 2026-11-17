# Submodel editing roadmap

## Scope

How submodels behave is specified in [the submodels specification](../submodels/high-level.md)
and [its low-level design](../submodels/low-level.md); how the canvas shows and edits an open
submodel in [the graph canvas specification](../frontend-graph-canvas/high-level.md) and
[its low-level design](../frontend-graph-canvas/low-level.md); and the requests that run a
graph in [the server API design](../server-api/low-level.md). The packages below finish how
the editor saves an open submodel and edits its boundary.

The graph store owns one authored document: an open submodel is a focus on it, the canvas is
its projection, every request that runs the graph goes through one gateway and names its
target by address, the server labels every node an answer names with its address, and
results are kept under the occurrence they belong to. Two things remain:

1. **Save and reload.** Saving inside a submodel is refused, and a reload returns to the main
   pipeline (SUBE-06).
2. **Boundary actions.** Connecting to a card, removing an input and the other boundary
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
| SUBE-06 | Planned | P2 | Saving works inside a submodel, and a reload keeps the open submodel when it still exists. |
| SUBE-07 | Planned | P2 | Boundary actions edit the document directly: the Input and Output cards carry no parent bindings, and no edit rebuilds a definition from the canvas. |

## Planned improvements

### SUBE-06 — Save and reload inside a submodel
**Why:** Saving inside a submodel is refused, and a reload returns to the main pipeline; both
follow only from the canvas not being the document. This changes what a user sees, decided
on 15 November 2026: Save works inside a submodel, and a reload keeps the open submodel when
it still exists.

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
