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
| EXPR-R01 | Planned | P2 | Traced formula values are computed by Polars in the context they need, and a value that one row cannot determine is never shown as if it could. |

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

Evaluating on the traced row alone is exact only for row-local expressions.
Aggregations, window expressions (`over`), `shift`, `diff`, ranks and
cumulative operations depend on other rows: `pl.col("x").sum().over("y")`
over `x = [10, 20]` in one group is `30` for both rows, but `10` and `20` when
each row is evaluated alone
(`test_window_semantics_diverge_from_single_row_approximation`). The current
evaluator already treats `over`, `shift`, `diff` and listed aggregations as
single-row identities, so it shows those approximations today.

**Plan:** Specify the evaluation contexts first, in the expression-parsing
specification:

- A sub-expression that the row-locality classifier (or an expression-level
  form of it) proves row-local is evaluated by Polars on a one-row slice of
  the node's actual input frame, so column dtypes are preserved, rather than
  on a frame rebuilt from the row's JSON values.
- A sub-expression that depends on other rows is evaluated by Polars against
  the same input frame the trace execution used, and the traced row's value
  is selected from the result. Where that frame is unavailable, it is shown as
  not computable from one row, with the reason.
- Each `when`/`then` condition is evaluated the same way for branch
  highlighting.

Keep the AST only to render the formula text and to locate sub-expressions.
Delete the hand-written interpreter once every form it handles has a
specified outcome under the new contexts, and replace the parity suites with
tests of those outcomes.

**Acceptance:** Every value the trace panel shows is computed by Polars or is
explicitly marked not computable with a reason; a row-local method the old
interpreter returned `None` for now shows its value; a window, aggregation,
shift or cumulative sub-expression shows its full-context value or the
not-computable marker, never its one-row value, and the two-row window test
above is kept as a regression; the branch taken is reported for nested
conditionals; the interpreter module is gone.

**Dependencies:** The trusted-code boundary specified in sandbox security
(`SBX-R02` does not block this). The row-locality classifier must survive
`EXEC-R03` (execution engine) or move with this package.

**Evidence:** `src/haute/_expression_parser.py::_ExprEvaluator`;
`src/haute/_expression_parser.py::_BranchTrackingEvaluator`;
`src/haute/_expression_parser.py::evaluate_expression`;
`src/haute/chunking.py::classify_chunk_local_polars_code`;
`tests/test_expression_parser_coverage.py`;
`tests/test_expression_parity_properties.py::test_window_semantics_diverge_from_single_row_approximation`;
`docs/COMMIT_STANDARDS.md`.
