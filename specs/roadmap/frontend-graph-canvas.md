# Frontend graph canvas roadmap

## Scope

The graph store behind the canvas, as [the frontend graph canvas
specification](../frontend-graph-canvas/high-level.md) describes it: what one
edit costs, what the undo history holds, which connections the canvas
takes, and what a tab with unsaved work does when the file changes under
it. `FEG-07` comes
from the plan's phase 5, a reader from a second model family (Codex) over
the code changed since the 31 October audit. A worker ran it in a
browser, and the main session ran the journey again on 13 November.

`FEG-09` comes from the review plan's phase 5, a reader from a second model
family (Codex) over the code changed since the 31 October audit. A worker
drove its journey in a browser and the main session ran it again on 15
November 2026.

## Priorities

**Start with:** `FEG-07`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEG-07 | Planned | P3 | An Edge Join dropped on the edge into an optimiser moves the optimiser's chosen input to the join, and a save's refusal names the node as the canvas does. |
| FEG-09 | Planned | P2 | Comparing an earlier milestone of a submodel that gained an output shows the comparison; an output with no identity yet never replaces the screen with an error. |

## Planned improvements

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
