# Frontend modelling and optimiser UI roadmap

## Scope

The modelling and optimiser panels, as [the frontend modelling and optimiser
UI specification](../frontend-modelling-optimiser-ui/high-level.md) describes
them: what the optimiser's data preview shows for a value that is missing.
The package below comes from the review plan's sweep of the frontend's logic
on 10 November 2026, which ran the panel's code under Node, and was then
confirmed in a real browser.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEM-01 | Planned | P3 | A missing objective or constraint value in the optimiser's data preview is reported as missing: not drawn as zero, and not a crashed pane. |

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
