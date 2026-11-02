# Frontend node editors roadmap

## Scope

The node configuration panel's editors: one per node type, the shared pickers
and form kit they compose, the client-side mirrors of backend rules (paths,
labels, output mapping), and the Explore configuration cards. Current behaviour
is specified in
[the frontend node editors specification](../frontend-node-editors/high-level.md).
These packages come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FNED-R05 | Planned | P3 | The step builder's open card follows its step through undo, the pivot editor carries no unknown keys the backend refuses, the two banding grids, the two rating grids, the params pickers and the zone cards exist once, and the internal-only exports are un-exported. |

## Planned improvements

### FNED-R05 — Positions, passthroughs and copies in the editor subdirectories
**Why:** The step builder holds its open card and pointed step as positions,
adjusted only by its own moves and deletes, so an undo that re-inserts an
earlier step opens a different step's form in place of the one being edited.
`futurePlacementFields` carries unknown pivot placement keys through every move
while the backend validator refuses any key outside its known set, a
forward-compatibility branch the canonical-only policy forbids and a frontend
specification sentence that contradicts the backend's rule. The two banding
grids each carry a copy of the header recogniser, pasted-range applier, TSV
builder and cell-paste handler; the one-way and two-way rating grids
re-implement the same selection state machine and copy handler; Banding's Import
from params and the Workbench's Allowed values each own the params-folder
listing and header cache; the formula card and the field card in a pivot zone
repeat the draggable-card scaffolding; two private dtype classifiers re-implement
the shared `castFamily`; twenty-odd exports are referenced only inside their own
module. The numeric Generate dialog suggests a step of 1 for any range below 10,
so rates and factors get a single band; the aggregation column box and the type
select offer two different type lists.

**Plan:** Hold the open and pointed steps by id; delete the passthrough and the
specification sentence (or make the backend carry unknown keys); one grid
clipboard hook, one cell-selection hook, one params-files hook, one placement
card, `castFamily` everywhere; un-export the internal symbols; a
magnitude-aware suggested step; one type list.

**Acceptance:** Undo while a card is open keeps that card's form; a placement
with an unknown key is refused by the editor as by the backend; jscpd reports
none of the pairs; knip reports none of the exports; a range of 0.2–0.9 suggests
about ten bands.

**Dependencies:** None; the backend already requires every card field it once defaulted.

**Evidence:** `frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx`;
`frontend/src/panels/editors/explorePivots/placements.ts::futurePlacementFields`;
`frontend/src/panels/editors/banding/BreakpointGrid.tsx`;
`frontend/src/panels/editors/banding/BandingRulesGrid.tsx`;
`frontend/src/panels/editors/rating/TwoWayGrid.tsx`;
`frontend/src/panels/editors/banding/ImportParamValues.tsx`;
`frontend/src/workbench/schema/AllowedField.tsx`;
`frontend/src/panels/editors/explorePivots/ZoneSection.tsx`;
`frontend/src/panels/editors/banding/GenerateBandsDialog.tsx`;
`frontend/src/utils/polarsDtypes.ts`.
