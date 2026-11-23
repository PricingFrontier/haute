# Frontend graph canvas roadmap

## Scope

The graph store behind the canvas, as [the frontend graph canvas
specification](../frontend-graph-canvas/high-level.md) describes it: what one
edit costs, what the undo history holds, which connections the canvas
takes, and what a tab with unsaved work does when the file changes under
it. `FEG-02` comes from the
review plan's sweep of the frontend's logic on 10 November 2026 and was
confirmed in a real browser. `FEG-03` comes from the plan's browser sweep
of the editors' fields on 11 November and was re-run by the main session
in a browser and through the save route. `FEG-05`
comes from the plan's browser sweep of what happens around an open editor,
also on 11 November; the main session ran it again with a slip in a code
editor and with a branch checked out and back. `FEG-06` and `FEG-07` come
from the plan's phase 5, a reader from a second model family (Codex) over
the code changed since the 31 October audit. A worker ran each in a
browser, and the main session ran each journey again on 13 November.

`FEG-09` comes from the review plan's phase 5, a reader from a second model
family (Codex) over the code changed since the 31 October audit. A worker
drove its journey in a browser and the main session ran it again on 15
November 2026.

## Priorities

**Start with:** `FEG-02`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEG-02 | Planned | P2 | Deleting a node with Backspace is one undo step, as with Delete: one Undo brings back the node and its edges. |
| FEG-03 | Planned | P2 | A connection that closes a loop is refused at the canvas, and a save that meets a loop names its nodes instead of failing every later save with "Check the server logs". |
| FEG-05 | Planned | P2 | A tab with unsaved work that a change on disk locked is editable again once the file is back as the tab loaded it, and the banner offers the reload it names. |
| FEG-06 | Planned | P3 | Nodes pasted together stay connected in their code: a pasted Transform reads the pasted input, not the original under a name it no longer has. |
| FEG-07 | Planned | P3 | An Edge Join dropped on the edge into an optimiser moves the optimiser's chosen input to the join, and a save's refusal names the node as the canvas does. |
| FEG-09 | Planned | P2 | Comparing an earlier milestone of a submodel that gained an output shows the comparison; an output with no identity yet never replaces the screen with an error. |

## Planned improvements

### FEG-02 — Backspace deletes a node and its edges as one undo step
**Why:** Reproduced on 10 November 2026 in the editor, after the sweep of
the frontend's logic found it under Node. With a connected node selected
(three edges), Backspace removes the node and its edges. One Undo brings the
node back without the edges; a second Undo restores them. The same deletion
made with the Delete key is undone whole by one Undo. An analyst who undoes
an accidental Backspace sees the node return and can carry on, or save, with
its inputs and outputs disconnected.

Both keys are bound to the app's own delete, which removes the selection as
one change. React Flow's built-in delete is also left on for the pipeline
view, on its default key, Backspace. It reports the edges' removal and the
node's removal as two changes, and the canvas's change handlers record an
undo step for each. The graph store's comments name partial undo as a class
of fault to keep out ("the undo-atomicity bug class"), and the keyboard
handler's tests hold one gesture to one step; the path through React Flow
passes neither. In the sweep, 230 of 230 such deletions of a node with an
edge took two undo steps.

**Plan:** Turn React Flow's own delete key off for the pipeline view, as it
already is for a hidden canvas, so that both keys reach the app's handler;
or record the two removals of one deletion as one history step.

**Acceptance:** In a browser test, Backspace on a selected node with edges,
then one Undo, restores the node and every edge; the same for Delete; and
one Redo removes them again.

**Dependencies:** None.

**Evidence:** `frontend/src/App.tsx`, where the canvas is given its delete
key; `frontend/src/hooks/useGraphCanvasState.ts`;
`frontend/src/hooks/useKeyboardShortcuts.ts`;
`frontend/src/stores/useGraphStore.ts`.

### FEG-03 — A connection that closes a loop is refused at the canvas
**Why:** Reproduced on 11 November 2026 in a browser by the plan's sweep of
the editors' fields, and again by the main session. With Transforms in a
chain, `enriched` to `loop_a` to `loop_b`, the analyst drags `loop_b`'s
output onto `loop_a`'s input. The canvas takes the edge without a word. A
node connected to itself is refused ("Self-connections are not allowed"),
and the Edge Join gesture refuses a loop ("Edge join rejected: that
connection would create a cycle"), but an ordinary connection is not
checked. Save then answers 500 and the screen reads "Failed to save
pipeline: Operation failed. Check the server logs for details." The server
log holds the sentence the analyst needs, in the form "Cycle detected in
pipeline graph involving nodes: a, b. Remove one of the edges to break the
cycle."

From then on nothing can be saved. Another node was renamed and Save
pressed: 500 again, the same text, and the pipeline file without the new
name. The editor does not try again by itself. After two presses of Undo,
the first for the rename and the second for the edge, the next save
succeeds. The sweep saw a loop of three behave the same, and the preview's
request for a node's inputs answer the same 500 for the nodes in the loop.
A request whose edge names a node that is not there gets the same generic
answer through the route ("Edges reference unknown node IDs: ghost." in the
log); no way to make one on the canvas was found.

**Plan:** Refuse a connection that would close a loop in the canvas's
connection rule, with the Edge Join's check and its words. Answer the graph
errors that the save and the preview routes can meet, a loop and an edge
without its node, as refusals that carry the error's own sentence, which
already names the nodes and the remedy.

**Acceptance:** Dragging a connection that closes a loop of two or of three
is refused at the canvas with a message, and the number of edges does not
change. A save or a preview request whose graph holds a loop answers 4xx
naming the nodes. Neither answers "Operation failed." A browser test drags
the loop.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useEdgeHandlers.ts`;
`frontend/src/utils/connectionValidation.ts::validatePipelineConnection`;
`frontend/src/utils/edgeJoinGraph.ts::hasDirectedCycle`;
`src/haute/_topo.py::CycleError`;
`src/haute/_topo.py::UnknownEdgeEndpointError`;
`src/haute/routes/pipeline.py::save_pipeline`.

### FEG-05 — A tab locked by a change on disk unlocks when the change is gone
**Why:** Reproduced in a browser on 11 November 2026, by the review plan's
sweep of what happens around an open editor and again by the main session.
A tab with unsaved work is never overwritten by an edit to the pipeline
file: the specification says "a banner asks them to reload or discard
first", and the tab turns read-only. Two everyday cases leave it read-only
when nothing on disk differs from what the tab loaded.

- A slip in a code editor that saves as it goes: a stray character typed
  into `rating/main.py` and taken out again.
- A look at a colleague's work: `git checkout colleague` in a terminal,
  then `git checkout` back.

In both runs the file was byte for byte what the tab had loaded, and the tab
still showed "Pipeline changed on disk (rating/main.py) while you have
unsaved changes. Reload the file or discard local edits before applying
external changes." It read "READ-ONLY", its Save button was off, and Ctrl+S
answered with the same sentence. It was the same thirty seconds later, and
nothing in the source lifts it. The banner names two remedies and has one
control, a close button, which hides the banner and leaves the tab
read-only. Reloading the page is the only way on, and the page has no
unload guard: the rename made before the slip was on the canvas, never
reached the file, and was gone.

The node's panel adds a sentence that points the wrong way: "This node is
valid and available for inspection. Resolve the pipeline load diagnostics
before editing or running it." There are no load diagnostics.

The cause is in what the tab remembers. Each document the server sends
replaces the tab's record of the file's revision and fingerprint before the
check for unsaved work. Once one changed document has arrived, the tab no
longer knows which document its edits were made on, and the document that
arrives when the file comes back is treated as one more change.

The sweep saw the same lock after the file was saved again with Windows
line ends, and with a byte-order mark, neither of which changes a node (not
run again). A lost answer to the tab's own save no longer reaches the
locked tab: the save is held and sent again first by the next Save.

**Plan:** Keep, while a tab has unsaved work, the revision and fingerprint
of the document it was last in step with. When a document arrives with that
fingerprint, put the tab back in step: the revision is that one again, the
banner goes and Save works. Decide with it whether a rewrite that changes
only the file's bytes should lock at all, since the document it parses to
is the one the tab holds. Give the banner the control its sentence names, a
button that reloads the file and drops the local edits. In the node's panel
use the reason `documentReadOnlyReason` already words for this state.

**Acceptance:** In a browser test, a tab with an unsaved rename sees the
pipeline file changed and changed back: the banner goes, the editor is
editable, and Save writes the rename. With the file left changed, the
banner has a control that reloads the file, and the node's panel names the
change on disk, not load diagnostics.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useWebSocketSync.ts`;
`frontend/src/stores/useDocumentStatusStore.ts::documentReadOnlyReason`;
`frontend/src/panels/NodePanel.tsx`; `frontend/src/App.tsx`;
`specs/frontend-graph-canvas/high-level.md`;
`specs/frontend-shared/high-level.md`.

### FEG-06 — Pasted nodes read each other, not the nodes they were copied from
**Why:** Reproduced on 13 November 2026 in a browser. On the starter
pipeline the Data Input `raw_rows` and the Transform `enriched` that reads
it are selected together, copied and pasted; the canvas says "Pasted 2
nodes". The pasted Transform's preview says "name 'raw_rows' is not
defined". Save writes `def enriched_copy(raw_rows_copy: pl.LazyFrame)`
with a body that still reads `raw_rows`, and
`haute run --node enriched_copy` ends with a `NameError`.

The paste gives the pasted input a new name and points the pasted edge at
it, and leaves the pasted Transform's code as it was. A rename of an input
keeps a coded Transform working by recording which name its code uses for
the input; the paste records nothing.

**Plan:** Give a pasted node whose code names a pasted input the record a
rename gives it, from the name in its code to the pasted input.

**Acceptance:** After the paste above the pasted Transform previews rows,
saves and runs. A Transform pasted without its input still reads the
original.

**Dependencies:** None.

**Evidence:** `frontend/src/hooks/useKeyboardShortcuts.ts`;
`frontend/src/utils/nodeUpdatePlan.ts`;
`specs/frontend-node-editors/low-level.md`.

### FEG-07 — An Edge Join dropped on an optimiser's input edge takes the optimiser's input with it
**Why:** Reproduced on 13 November 2026 in a browser. An optimiser's
settings name its input: `data_input` is `browser_optimiser_rows`. A
second table's output is dropped onto the edge from that input into the
optimiser, which puts an Edge Join between them. The optimiser's Data tab
then says "Missing input", its estimate answers 400, Optimise is off, and
every Save of the pipeline answers 400: "Node 'optimiser' data_input
'browser_optimiser_rows' is not an exact connected input name". The node
is called `browser_ratebook` on the canvas, not `optimiser`. The Data tab
offers `edge_join` to be picked by hand. In the plan's run an optimiser
that had never saved a `data_input` was not affected.

The insertion rewrites the step references and the input mapping of the
node after the join, and not the settings by which an optimiser or a
modelling node names its input. A rename of the input does move them.

**Plan:** Move a node's input selectors when a join is inserted on its
edge, as a rename moves them, and name the node by its label in the save's
refusal.

**Acceptance:** After the drop above the optimiser reads `edge_join`, its
Data tab shows the input, and Save succeeds. A refusal that names a node
uses the name on the canvas.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/edgeJoinGraph.ts`;
`frontend/src/utils/nodeUpdatePlan.ts`;
`specs/frontend-modelling-optimiser-ui/high-level.md`.

### FEG-09 — A submodel output with no resolved identity does not replace the screen
**Why:** Reproduced on 15 November 2026 in the browser, in two journeys.
First: two nodes were grouped into a submodel with one output, saved and
committed; a second output was connected, saved and committed; then the
first milestone was opened from the Git panel. Instead of the comparison,
the screen reads "Something went wrong. Submodel fr1_group output
browser_mixed_banding has no authoritative identity. Try again". The
comparison draws the old version's submodel node with the live
definition, which has an output the old version lacks. Second, run on 13
November: the canvas showed the same error screen while a new submodel
output's identities were still being resolved. The node throws while it
draws, so one output without an identity takes down the whole view.

**Plan:** Draw a compared version's submodel from that version's own
definition. Where an output has no identity yet, draw the node without
that output's handle instead of throwing.

**Acceptance:** The first journey shows the comparison with one output on
the old side. Neither journey shows the error screen.

**Dependencies:** None.

**Evidence:** `frontend/src/nodes/SubmodelNode.tsx`;
`specs/frontend-graph-canvas/high-level.md`.
