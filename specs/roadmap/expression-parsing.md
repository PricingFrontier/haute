# Expression parsing roadmap

## Scope

Turning a node's Polars expressions into readable formulas and evaluating them
for one traced row. Current behaviour is specified in
[the expression-parsing specification](../expression-parsing/high-level.md).
This package comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXPR-R01 | Planned | P2 | A traced formula's value is computed by Polars itself, so it cannot disagree with the pipeline. |

## Planned improvements

### EXPR-R01 — Evaluate traced formulas with Polars
**Why:** To show the value of a traced cell's formula, `_ExprEvaluator` (656
lines; its `_call` has cyclomatic complexity 95) and
`_BranchTrackingEvaluator` re-implement Polars semantics by hand: null
propagation, Kleene logic, integer and float division, 64-bit overflow and
half-to-even rounding. About 8,000 lines of tests, including parity property
suites, keep it close to the pinned Polars. The specification admits that many
methods return `None` (unsupported). The stated reason for not using Polars
is to avoid executing user code for a display question, but project code has
been declared trusted since 6 September 2026, and the same expression has
already run in the same execution. The commit standards' "thin
orchestration" principle asks Haute to lean on Polars rather than
reimplement it.

**Plan:** Evaluate each extracted sub-expression, and each `when`/`then`
condition for branch highlighting, by running it with Polars on a one-row frame
built from the traced input row. Keep the AST only to render the formula
text and to locate sub-expressions. Delete the hand-written interpreter and
its parity suites.

**Acceptance:** Every formula the trace panel shows is evaluated by Polars; a
method the old interpreter returned `None` for now shows its value; the branch
taken is reported for nested conditionals; the interpreter module and its
parity tests are gone.

**Dependencies:** The trusted-code boundary specified in sandbox security
(`SBX-R02` does not block this).

**Evidence:** `src/haute/_expression_parser.py::_ExprEvaluator`;
`src/haute/_expression_parser.py::_BranchTrackingEvaluator`;
`src/haute/_expression_parser.py::evaluate_expression`;
`tests/test_expression_parser_coverage.py`;
`tests/test_expression_parity_properties.py`; `docs/COMMIT_STANDARDS.md`.
