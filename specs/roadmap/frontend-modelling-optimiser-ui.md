# Frontend modelling and optimiser UI roadmap

## Scope

The modelling and optimiser panels, as [the frontend modelling and optimiser
UI specification](../frontend-modelling-optimiser-ui/high-level.md) describes
them: what the optimiser's data preview shows for a value that is missing,
and what the Tables tab does when a model is trained again. `FEM-01` comes
from the review plan's sweep of the frontend's logic on 10 November 2026,
which ran the panel's code under Node, and was then confirmed in a real
browser. `FEM-02` comes from the plan's browser sweep of what happens
around an open editor on 11 November; the main session ran it again in a
browser and with real t-boost fits.

`FEM-03` comes from the review plan's phase 5, a reader from a second
model family (Codex) over the code changed since the 31 October audit. A
worker drove its journey in a browser and the main session ran it again on
15 November 2026.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEM-01 | Planned | P3 | A missing objective or constraint value in the optimiser's data preview is reported as missing: not drawn as zero, and not a crashed pane. |
| FEM-02 | Planned | P3 | A t-boost model trained again while one of its three-way tables is on screen shows the new table, not a crashed result pane. |
| FEM-03 | Planned | P2 | After Undo the search space box shows the range the training request carries. |

## Planned improvements

### FEM-01 — A missing value in the optimiser's data is shown as missing
**Why:** Reproduced on 10 November 2026. The optimiser's data preview,
before any solve, was opened in the editor on scenario rows with one null in
the objective column. Choosing Statistics replaces the whole data preview
with "Something went wrong" and "Expected finite numeric value in
"expected_income", received null"; "Try again" fails the same way. The chart
beside it is given 0 for that scenario, as if the pipeline had computed no
income (read from the panel's data under Node, where a null constraint cell
and a missing key behaved the same). The server refuses such rows when a
solve starts, naming the column and the number of null rows and pointing at
upstream joins and filters, so no wrong solve follows. The preview is where
the analyst looks first, and it shows one wrong value and one crash for a
cause the server already words well.

The low-level specification records both halves: the chart "coerces missing
series/scenario values with `Number(... ?? 0)`", and the statistics helper
throws "on present malformed required numeric fields". A null is a missing
value, not a malformed one, and the helper counts it as malformed.

**Plan:** Count the rows whose objective or constraint value is missing
once, where the scenario data is prepared. Leave them out of the curve and
the statistics, and say above both how many rows of which column have no
value, in the server's words. Keep the throw for a value that is present and
not a number, caught by the pane and shown as its own message. Change the
specification's sentence with it.

**Acceptance:** With one null objective cell, the chart shows no point at
zero for it, the Statistics pane renders, and both say that one row of the
objective column has no value. A present value that is not a number shows a
message in the pane, with the rest of the panel usable.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/optimiser/useOptimiserScenarioData.ts`;
`frontend/src/panels/optimiserScenarioStats.ts`;
`src/haute/routes/_optimiser_input.py`.

### FEM-02 — A retrained t-boost model does not crash the Tables tab
**Why:** Reproduced on 11 November 2026 in two halves, after the review
plan's sweep of what happens around an open editor met it. t-boost learns
tables over up to three features unless told otherwise
(`max_interaction_order` is 3), and the Tables tab shows a three-way table
as a grid of two features with the third fixed at a level the analyst
picks. The view keeps that pick for as long as the same table is on
screen, and to the view a table is the same when its name is.

A retrain gives a table of the same name with other cells. Two real fits
through Haute's own t-boost code, on 40,000 made-up policies, gave the
table `proposer_age × vehicle_age × channel` 9, 7 and 3 cells on its axes;
with two of the four channels filtered out upstream it had 5, 4 and 2.

In a browser, a three-way table was on screen with the last level picked on
the fixed axis, and the model was trained again with fewer levels on that
axis. The whole result pane was replaced by "Something went wrong" and
"Cannot read properties of undefined (reading '0')". "Try again" cleared
it. The browser's two results were real training runs of another model
family whose answers were given a t-boost tables report made by hand in
the server's shape, since the browser's project has no t-boost data: what
that run shows is the view's code. The pick is an index kept from the
earlier result and read past the end of the new one.

**Plan:** Start the picks afresh when the result under the view changes:
key the Tables view, and the Terms view beside it, by the result as well as
by the term, or keep each pick only while the new axes hold it.

**Acceptance:** A component test renders a three-way table with the last
level picked on the fixed axis, then the same term with fewer levels on
every axis. Nothing is thrown, and the table shown is the new result's.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/modelling/TermTableView.tsx::TermTableView`;
`frontend/src/panels/modelling/TBoostTablesTab.tsx`;
`frontend/src/panels/ModellingPreview.tsx`;
`src/haute/modelling/_tboost.py::_report_axis`;
`docs/building-models/nodes/model-training.md`.

### FEM-03 — After Undo the search space box shows what training will use
**Why:** Reproduced on 15 November 2026 in the browser. With "Tune
parameters" on, the search space was set to `{"depth":[4,6]}` and then to
`{"depth":[8,10]}`. Toolbar Undo, with the panel open, leaves the box
showing `{"depth":[8,10]}`; the training request then carries
`{"depth":[4,6]}`. The box keeps a draft of the typed text, and the draft
wins over the configuration Undo restored. The analyst tunes over a
range other than the one on screen.

**Plan:** Drop the draft when the node's configuration changes from
outside the box (Undo, Redo, live sync), so the box shows the restored
value.

**Acceptance:** After the Undo above the box and the training request
both hold `{"depth":[4,6]}`.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/ModellingConfig.tsx`;
`specs/frontend-modelling-optimiser-ui/high-level.md`.

