# Frontend graph canvas roadmap

## Scope

The graph store behind the canvas, as [the frontend graph canvas
specification](../frontend-graph-canvas/high-level.md) describes it: what one
edit costs, what the undo history holds, which connections the canvas
takes, and what a tab with unsaved work does when the file changes under
it. `FEG-09` comes from the review plan's phase 5, a reader from a second model
family (Codex) over the code changed since the 31 October audit. A worker
drove its journey in a browser and the main session ran it again on 15
November 2026.

## Priorities

**Start with:** `FEG-09`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEG-09 | Planned | P2 | Comparing an earlier milestone of a submodel that gained an output shows the comparison; an output with no identity yet never replaces the screen with an error. |

## Planned improvements

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
