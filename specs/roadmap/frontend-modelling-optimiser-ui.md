# Frontend modelling and optimiser UI roadmap

## Scope

The modelling and optimiser panels, as [the frontend modelling and optimiser
UI specification](../frontend-modelling-optimiser-ui/high-level.md) describes
them: what the Tables tab does when a model is trained again. `FEM-02` comes from the plan's browser sweep of what happens
around an open editor on 11 November; the main session ran it again in a
browser and with real t-boost fits.

## Priorities

**Start with:** `FEM-02`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEM-02 | Planned | P3 | A t-boost model trained again while one of its three-way tables is on screen shows the new table, not a crashed result pane. |

## Planned improvements

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
