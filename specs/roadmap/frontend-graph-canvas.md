# Frontend graph canvas roadmap

## Scope

The graph store behind the canvas, as [the frontend graph canvas
specification](../frontend-graph-canvas/high-level.md) describes it: what one
edit costs, what the undo history holds, which connections the canvas
takes and what a save does when its answer never comes. `FEG-01` comes
from the
9 November 2026 codebase review and was measured against `HEAD` on that day
by bundling the store and driving it under Node. `FEG-02` comes from the
review plan's sweep of the frontend's logic on 10 November 2026 and was
confirmed in a real browser. `FEG-03` comes from the plan's browser sweep
of the editors' fields on 11 November and was re-run by the main session
in a browser and through the save route. `FEG-04` comes from the plan's
browser sweep of failed calls on the same day; the main session ran it
again with a dropped connection and with a gateway's answer.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEG-01 | Planned | P2 | An edit's cost and the undo history's size follow what changed, not the size of every table in the graph. |
| FEG-02 | Planned | P2 | Deleting a node with Backspace is one undo step, as with Delete: one Undo brings back the node and its edges. |
| FEG-03 | Planned | P2 | A connection that closes a loop is refused at the canvas, and a save that meets a loop names its nodes instead of failing every later save with "Check the server logs". |
| FEG-04 | Planned | P2 | After a save whose answer never came, the next Save repeats it under the same operation: the analyst's own save is not refused as a change on disk, and the work done since is kept. |

## Planned improvements

### FEG-01 — An edit no longer serialises and clones the whole graph
**Why:** Measured. Every history-aware change to the graph serialises the
whole graph for the new fingerprint, serialises the state before it a second
time to decide whether history moves, computes the structural and
panel-context fingerprints, and deep-clones every node into the undo stack,
which keeps one hundred such clones. The node editors commit through this on
each edit. A Rating Step's tables live in its node's settings with no cap on
their rows, so their size is in every one of these passes, for an edit to any
node. One-character edits to one code node, one hundred times:

| Graph | Per edit | Undo stack after 100 edits |
|---|---:|---:|
| 100 code nodes | 2.4 ms | 3 MiB |
| 300 code nodes | 6.1 ms | 9 MiB |
| 100 code nodes and one 20,000-row table | 47.8 ms | 158 MiB |
| 100 code nodes and one 100,000-row table | 276.1 ms | 776 MiB |

With a large rating table in the pipeline, typing into any node's editor
stalls for a quarter of a second per keystroke and the tab's memory grows
towards a gigabyte.

**Plan:** Nodes are replaced, never mutated, on a commit, so history can hold
references: an undo entry shares every node the change did not touch.
Fingerprint per node, cached by the node object's identity, and combine the
cached parts; compare the changed nodes only to decide whether history
moves. Keep the saved-baseline and dirty semantics exactly as they are.

**Acceptance:** On the last graph above, a one-character edit to a code node
takes under 5 ms and one hundred edits grow the heap by under 10 MiB, held by
a store-level test with a generous bound. Undo and redo restore the same
graph as today across the existing history tests, and `dirty` returns to
false when an undo reaches the saved state.

**Dependencies:** A check that no caller mutates a node object in place
after a commit; the store's tests are where that is asserted.

**Evidence:** `frontend/src/stores/useGraphStore.ts::commitGraphChange`;
`frontend/src/stores/useGraphStore.ts::derivedPatch`;
`frontend/src/stores/useGraphStore.ts::MAX_HISTORY`;
`frontend/src/utils/graphSnapshot.ts::cloneGraphSnapshot`;
`frontend/src/utils/graphSnapshot.ts::serializeSnapshot`;
`frontend/src/hooks/useGraphCommitController.ts::onUpdateNode`;
`src/haute/_node_config_models.py:490` holds the uncapped `entries` list.

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

### FEG-04 — A lost answer to a save does not lock the editor against itself
**Why:** Reproduced in a browser on 11 November 2026, by the review plan's
sweep of failed calls and again by the main session. A pipeline save reaches
the server and is written, and its answer never reaches the browser. Two
ordinary causes were run. The connection drops while the save is in flight,
so the one repeat the client sends cannot reach the server either. Or a
gateway in front of a hosted editor answers 504 for a save the server went
on to finish. The screen says "Failed to save pipeline: Failed to fetch", or
"Failed to save pipeline: Gateway Timeout", and the file holds the save.

The analyst works on and presses Save again with the network back. The
answer is "Save rejected: the pipeline changed on disk. Reload the file or
discard local edits first.", under the banner "Pipeline changed on disk
while you have unsaved changes. Reload the file or discard local edits
first." Nothing changed on disk but their own save. The editor now reads
"READ-ONLY", the Save button is off, Ctrl+S repeats the banner, and no
control offers either remedy the message names. Reloading the page is the
way out, and it shows the file as the lost save left it. In the run, a
second rename made after the failed save was on the canvas, never reached
the file, and was gone after the reload: everything done since the save is
lost.

The server is right to refuse: the second save is a new operation on the
revision the first one replaced. The client had what it needed to avoid
asking. Every save "names a fresh `operation`", and the server "answers a
repeat of a save that committed with that save's response". The client uses
that for one immediate repeat after a network failure and then lets the
request go: "a second lost answer, or any other failure, is reported as a
failed save". After an answer of 500 or more, or one it cannot read, it
does not repeat at all. The underwriter app keeps such a save, "so the next
Save repeats it exactly before saving anything typed since", and the same
fault there cost nothing (run the same day: one version, "Saved").
`SRV-03` names a third cause, a save the server itself answers 500 after
writing it.

**Plan:** Keep a save whose fate is unknown, with its operation key: no
answer, an answer of 500 or more, or an answer that does not parse. Before
the next Save sends anything new, repeat the kept one. When it had
committed, its answer is the first request's: take the committed revision,
keep the later edits as unsaved, and save them on that revision. When it
had not, it is made now. Only an answer the server words, a status below
500, lets the kept save go. The conflict banner stays for a revision
another writer made.

**Acceptance:** In a browser test, a save the server wrote whose answer is
dropped twice, and one answered by a gateway's 504 page, are each followed
by a further edit and a Save: that Save answers "Saved", the file holds both
edits, no conflict banner shows and the editor stays editable. A save
refused because another writer saved in between still shows the banner.

**Dependencies:** None. `SRV-03` removes one cause on the server.

**Evidence:** `frontend/src/hooks/usePipelineAPI.ts::isLostAnswer`;
`frontend/src/hooks/usePipelineAPI.ts::usePipelineAPI`;
`frontend/src/app/useOpenQuoteStore.ts`;
`src/haute/routes/pipeline.py::save_pipeline`;
`specs/frontend-graph-canvas/low-level.md`; `specs/workbench/high-level.md`.
