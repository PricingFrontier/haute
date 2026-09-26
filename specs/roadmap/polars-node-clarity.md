# Polars node clarity roadmap

## Scope

The low-code step editor on the Polars node, as a first-time pricing analyst
meets it: the `Add step` menu, the step cards and their forms, the column
completion boxes, the formula box and the generated code panel. Current
behaviour is specified in
[the frontend node editors specification](../frontend-node-editors/high-level.md)
and its [low-level specification](../frontend-node-editors/low-level.md);
the server renders the steps to code in `src/haute/_polars_steps.py`. The
canvas, the node palette, the node panel's layout and the data preview are out
of scope.

This roadmap improves the experience of what the editor already does: how
intuitive it is, how it looks, and where it tells the analyst what happened.
The step kinds and what each can compute stay as they are, so proposals that
add capability are out of scope and `PNC-07` is deferred for that reason.
Labels use plain English, with the Polars name beside them where a technical
name helps; SAS and Excel terms are not used.

The editor's completion, keyboard handling, error placement, cards,
summaries, forms, formula box, step menu and generated code now behave as the
specifications above describe. What remains is a visual baseline for the
redesigned cards, recorded once their look has been reviewed.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PNC-13 | Planned | P3 | The redesigned step cards are held by a visual baseline. |
| PNC-07 | Deferred | P3 | A formula can compare, so a flag such as `amount_paid > 5000` is one formula. |

## Planned improvements

### PNC-13 — The redesigned step cards are held by a visual baseline
**Why:** The step cards' header (chevron, muted number, kind icon, label, the
summary in the label's column, three fixed action slots) and their surfaces
are specified and covered by component tests, and the text colours are held at
4.5:1 against the card by a token test. Component tests cannot see layout, so
nothing yet catches a regression in alignment, wrapping at the narrow panel
width, or the hover and focus states.

**Plan:** Once the new look has been reviewed and settled, add a Playwright
screenshot of a three-step Polars node to the canvas-assurance visual
baselines, in the narrow viewport, with one card open, one hovered and one
focused, and record its baselines on every platform the suite runs on.

**Acceptance:** The canvas-assurance suite carries the new screenshot and
passes on CI with its recorded baselines; changing a card's header spacing
fails it.

**Dependencies:** A review of the new look.

**Evidence:** `frontend/src/panels/editors/polarsSteps/StepCard.tsx::StepCard`;
`frontend/e2e/canvas-assurance.spec.ts`.

### PNC-07 — A formula can compare
**Why:** The `Formula` box parses arithmetic, functions, columns, variables
and literals, but not comparisons or boolean logic. A flag such as
`amount_paid > 5000`, or `claim_type == 'windscreen'`, cannot be written as
a formula and has to be rebuilt as an `If-then` with a condition row and
explicit `then` and `otherwise` values, a much longer path for a very common
pricing derivation. The package is deferred because it adds capability to the
formula language, which this roadmap leaves out of scope; the flag can already
be built with `If-then`.

**Plan:** If reopened, decide whether comparisons (`== != > >= < <=`) and
`and`, `or`, `not` join the formula grammar, producing a boolean expression,
and whether `if(cond, then, otherwise)` joins it as a function. Leaving
multi-branch logic to `If-then` would not cover it, since `If-then` has one
branch. The step schema and the server's closed vocabulary would gain the new
operators, held equal by the catalogue parity test.

**Acceptance:** Parser tests for each comparison and boolean operator with
Python precedence, `formulaText` round trips, a server render test producing
the matching Polars expression, and the catalogue parity test updated for the
new operators.

**Dependencies:** A decision to extend the formula language.

**Evidence:** `frontend/src/panels/editors/polarsSteps/formula.ts::parseFormula`;
`frontend/src/panels/editors/polarsSteps/forms.tsx::EXPR_TYPES`.
