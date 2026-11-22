# Tracing roadmap

## Scope

Explaining one output cell: which rows fed it and how each step computed
it, as [the tracing specification](../tracing/high-level.md) and
[the expression parsing specification](../expression-parsing/high-level.md)
describe them. The packages below come from phase 5 of
[the codebase review plan](codebase-review-plan.md). A reader from a second
model family (Codex) read the code changed since the 31 October 2026 audit
and named each fault with a trigger; the review then reproduced each on
12 November 2026 by tracing one cell of a three-node pipeline through
`execute_trace`. Both are explanations that contradict the value they
explain. The value itself is right in each.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| TRC-01 | Planned | P2 | A column written inside an `if` is traced as not computable, never with the formula of the branch that did not run. |
| TRC-02 | Planned | P3 | A grouped row whose grouping column was dropped afterwards is no longer explained by the rows of every group that shares the columns left. |

## Planned improvements

### TRC-01 — A trace does not compute the branch that did not run
**Why:** Reproduced on 12 November 2026. A Transform chooses one of two
formulas with a flag from the pipeline's preamble: under
`if APPLY_DISCOUNT:` it writes `premium` as `pl.col("base") * 0.9`, under
`else:` as `pl.col("base")`. With the flag true the pipeline gives 90 for a
base of 100, and the trace of that cell reports 90 as its `output_value`.
The same step's calculation reports `substituted_text` "base" and
`result_value` 100, with no `not_computable_reason`: the formula of the
`else` branch. The trace panel draws a step's calculation whenever it has
one, so an analyst asking why the premium is 90 is shown 100.

The expression parsing specification says what such a column is: a
`with_columns` inside `if`, `for`, `while`, `try`, `with` or `match` makes
the value branch-dependent, and `parse_expression` marks the expression
opaque. The evaluation does not follow it. It finds the column's definition
by "last alias match wins", which is the `else` branch's, and evaluates
that against the row. With the flag false the two happen to agree.

**Plan:** When the traced column is written inside control flow, give the
calculation no result and a reason that says the value depends on a branch,
as the evaluation already does for a name it cannot resolve. Keep the
opaque expression text.

**Acceptance:** Tracing the cell above gives a calculation with no result
value and a reason that names the branch, with the flag true and with it
false, and the same for a write inside `for`, `try`, `with` and `match`. A
column written once, outside any block, is traced as it is today.

**Dependencies:** None.

**Evidence:**
`src/haute/_expression_parser.py::_has_control_flow_wrapping_target`;
`src/haute/_expression_parser.py::_find_definition`;
`src/haute/_expression_parser.py::_evaluate_expression_impl`;
`src/haute/_trace_enrichment.py::enrich_steps`;
`frontend/src/trace/CalculationHero.tsx`;
`specs/expression-parsing/low-level.md`.

### TRC-02 — A dropped grouping column does not widen the group a trace reports
**Why:** Reproduced on 12 November 2026. Four rows of one policy: two fire
premiums, 10 and 20, and two theft premiums, 100 and 200. A Transform sums
the premium by `policy_id` and `cover`, then keeps `policy_id` and
`premium`. The trace of the 30 says "The row of 'summed' aggregates rows of
node 'source' grouped by ['policy_id']: 4 of its rows share these key
values." Two rows made the 30. With `cover` kept, the same trace says
"grouped by ['cover', 'policy_id']: 2 of its rows".

The tracing specification allows the `aggregated_rows` explanation for code
whose one grouping "keeps every group key unchanged". The proof of carried
columns keeps its claim that whole groups are summarised when a later
`select` drops a key, and only narrows the columns a group is matched on.
The rows counted are then the rows of every group that shares the keys
left.

**Plan:** When a selection after the grouping drops a grouping column, the
proof stops claiming whole groups: the rows that fed the group cannot be
told from the columns left, and the trace reports that correlation as it
does any other it cannot prove.

**Acceptance:** The trace above no longer reports four aggregated rows; it
reports that the rows behind the group cannot be identified. With every
grouping column kept it reports two, as it does today.

**Dependencies:** None.

**Evidence:**
`src/haute/_column_lineage.py::carried_column_proof`;
`src/haute/_trace_correlation.py::RowScopeResolver`;
`specs/tracing/high-level.md`.

