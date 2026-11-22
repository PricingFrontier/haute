# Tracing roadmap

## Scope

Explaining one output cell: which rows fed it and how each step computed
it, as [the tracing specification](../tracing/high-level.md) and
[the expression parsing specification](../expression-parsing/high-level.md)
describe them. `TRC-02` comes from phase 5 of
[the codebase review plan](codebase-review-plan.md). A reader from a second
model family (Codex) read the code changed since the 31 October 2026 audit
and named the fault with a trigger; the review then reproduced it on
12 November 2026 by tracing one cell of a three-node pipeline through
`execute_trace`. It is an explanation that contradicts the value it
explains. The value itself is right.
`TRC-15` comes from the 22 November 2026 sweep of `src/haute` for
the cause `XPL-03` removed from Explore, a temporal value read into Python;
the [temporal values root cause](root-cause-temporal-values.md) is taken before it.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| TRC-15 | Planned | P2 | A traced row whose timestamp has digits below a microsecond is found by its own values, not reported uncorrelated or matched to another row. |
| TRC-02 | Planned | P3 | A grouped row whose grouping column was dropped afterwards is no longer explained by the rows of every group that shares the columns left. |

## Planned improvements

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

### TRC-15 — A traced row with nanoseconds is found by its own values
**Why:** Read on 22 November 2026, not reproduced. Row correlation
(`_correlate_rows_posthoc`) reads the traced row with `row(..., named=True)`
and makes it JSON-safe (`_jsonify_row`), which holds a datetime to the microsecond, and then
matches each upstream row again with `_typed_value_match_expr` built from
those values, compared exactly with the column's own integer. A row whose
`Datetime("ns")` or `Time` value has digits below a microsecond never
equals its own match. The trace then reports that row correlation failed,
or a relaxed match leaves the column out and can take another row as the
parent. The enrichment reads the same JSON values: it keys a Rating Step's
lookup with `normalise_rating_key` of the traced value, and compares a
date-time breakpoint with `datetime.fromisoformat` of it, so a traced value
can be shown the default for a level the run matched, or the other side of
a breakpoint inside its microsecond. A traced value is shown with six digits
(`_expression_parser.py`).

**Plan:** Carry the traced row's temporal values as their exact text and
build each match from it, through `IOL-08`'s shared literal builder.
Update the tracing specification first.

**Acceptance:** A three-node pipeline over a `Datetime("ns")` column whose
rows differ by 100 ns traces each row to its own parent rows, shows its
value with nine digits, and explains a Rating Step keyed by that column
with the level the run matched.

**Dependencies:** `IOL-08` (the [temporal values root cause](root-cause-temporal-values.md)) first.

**Evidence:** `src/haute/_trace_correlation.py::_correlate_rows_posthoc`;
`src/haute/_trace_correlation.py::_typed_value_match_expr`;
`src/haute/_trace_correlation.py::_jsonify_row`;
`src/haute/_trace_enrichment.py`; `src/haute/_expression_parser.py`;
`specs/tracing/low-level.md`.
