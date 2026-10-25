# Tracing roadmap

## Scope

The row trace: correlating one quote's row through every node, the per-step
enrichments (formulas, input sources, renames, banding and rating verdicts,
waterfalls), the value lineage, and the expression parser that reads a node's
Polars code. Current behaviour is specified in
[the tracing specification](../tracing/high-level.md) and
[expression parsing](../expression-parsing/high-level.md). These packages come
from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| TRACE-R01 | Planned | P2 | A formula's input sources name the node whose value the traced row carries, resolved through the value lineage, never the nearest step in declaration order. |
| TRACE-R02 | Planned | P2 | A step's expression type, description, referenced columns and rename detail come from the located expression's syntax tree, never from regular expressions over the node's whole code. |
| TRACE-R03 | Planned | P2 | A parent row proven by carried values is matched strictly or omitted, a banding coercion that fails is an error marker, an unnameable edge is reported, and a failed comparison names its real cause. |
| TRACE-R04 | Planned | P3 | The unreachable partial-trace path, the test-only correlation branch, the dead helper, the stale comments and the three duplicated mechanisms are gone. |

## Planned improvements

### TRACE-R01 — Input sources from the value lineage
**Why:** `calculation.input_sources` names, for each column a formula reads, the
nearest step in list order whose schema diff added or modified that column, with
no ancestry or value check, so on a join whose two sides share a non-key column
the join side is shown as the source of a base-side value and the attribution
flips with node declaration order (probed: `calc.x` is 10.0 from A while
`input_sources["x"]` names B with 1000.0; declaring B first names A). The
lineage's `derivations[].reads` is right both ways, and `_pass_through_origin`
and the lineage's `computed_by` already do the graph- and value-aware walk; the
low-level specification describes the list-order algorithm, so it and the code
both contradict the high-level rule that relevance follows the value's whole
lineage.

**Plan:** Resolve each read through the lineage's column reads (or
`_pass_through_origin`'s single-parent same-value walk) and drop the list-order
scan, or retire `input_sources` in favour of `derivations[].reads` so one
mechanism explains inputs; correct the specification; add the probe's join shape
as a test.

**Acceptance:** On the probe graph `input_sources["x"]` names A with 10.0
whatever the declaration order; one mechanism explains a formula's inputs.

**Dependencies:** None.

**Evidence:** `src/haute/_trace_enrichment.py::_build_input_sources`;
`src/haute/_trace_enrichment.py::_pass_through_origin`;
`src/haute/_trace_lineage.py::computed_by`;
`frontend/src/trace/InputSourceTree.tsx`.

### TRACE-R02 — Expression facts from the located expression
**Why:** `evaluate_expression` decides "window" by the substring `.over(`
anywhere in the node's code, so every column of a `with_columns` that also
contains a window is typed `window`, given the window's regex-built description
as its `substituted_text` and the window's partition column among its referenced
columns; only the value is right (probed: `y = a * 2` shown as "rank of x over
g"). `_detect_rename` finds `.rename({` anywhere and then reads every `'k': 'v'`
pair in the whole code as a rename mapping, so an unrelated `replace({...})`
labels the step a rename with a wrong original name (probed). Two sites decide
"this node's code assigns `column`" with the regex `\b{column}\s*=` over the raw
code, which matches comments, string literals and `==` comparisons, forcing
`column_relevant` for a step the lineage never reached; AST answers to the same
question exist three times.

**Plan:** Decide window-ness from the located defining expression and build the
description from its syntax tree; read the `.rename(...)` call's dict argument
from the AST or retire rename detection; replace both regexes with one shared
helper over `assignment_phases` and `_locate_defining_expression`, and let
`column_relevant` come from the lineage alone; align the expression-parsing
specification, which documents the substring detection.

**Acceptance:** A node mixing a window and a row-local column types each column
by its own expression; a `replace` mapping beside a `rename` does not label the
step a rename; a comment containing `premium ==` does not make a step relevant.

**Dependencies:** None.

**Evidence:** `src/haute/_expression_parser.py::evaluate_expression`;
`src/haute/_expression_parser.py::_build_window_description`;
`src/haute/_expression_parser.py::_locate_defining_expression`;
`src/haute/_trace_enrichment.py::_detect_rename`;
`src/haute/trace.py::_prune_to_column_relevance`;
`src/haute/_trace_waterfall.py::_step_targets_column`.

### TRACE-R03 — Proven matches stay strict; failures carry their cause
**Why:** A head-frame match on proven carried values still allows relaxed
matching (`allow_relaxed` is true for every non-join-role parent whatever the
match row's origin), so when the strict match misses — which with a correct
proof means the parent row is not in the head frame — the widest-subset row is
shown with a `low_confidence_relaxed_match` warning instead of the step being
omitted, against the specification's strict-equality rule for proven columns.
`_coerce_pair_through_dtype` catches every comparison failure and returns the
float64 operands, the widened comparison it exists to avoid, so a banding
verdict can contradict the engine with no error marker.
`_trace_lineage_alignments` swallows the `ValueError` `edge_input_name` raises
for an edge it cannot name, so the child's input names lack it and the
carried-column proof runs on the wrong set with no diagnostic (reachability
unconfirmed, as for `EXEC-R14`). Any `PolarsError` from the strict match is
reported as `incompatible_nested_schema` whatever its cause.

**Plan:** Thread `allow_relaxed=False` through `_match_parent_row` when the
match row came from carried values; return an error marker from the coercion
through the existing `enrich_banding` boundary; let the edge-naming error
propagate or record a correlation diagnostic; carry the exception's type in the
unsupported-match reason.

**Acceptance:** A proven parent row absent from the head frame is omitted, not
relaxed; a failed banding coercion shows `error_type`; an unnameable edge
produces a diagnostic; a non-nested Polars failure is not labelled nested.

**Dependencies:** `EXEC-R14` makes a null-handle request-input edge fail
validation.

**Evidence:** `src/haute/_trace_correlation.py::_match_parent_row`;
`src/haute/_trace_correlation.py::_allows_relaxed_parent_match`;
`src/haute/_trace_enrichment.py::_coerce_pair_through_dtype`;
`src/haute/_trace_enrichment.py::enrich_banding`;
`src/haute/trace.py::_trace_lineage_alignments`;
`src/haute/_trace_correlation.py::_unsupported_match_result`.

### TRACE-R04 — Dead paths and clones in the trace
**Why:** The "target node execution failed" branch builds partial rows by
`row_index` position with no correlation and is unreachable (the walk raises
node failures, the target is always collected, the cache holds only completed
walks), while the specification still describes the partial path. The
non-resolver branch of `_correlate_rows_posthoc` is a second correlation path
only tests take. `_planned_order` has no caller. Two comments claim the
enrichment dispatch resolves parser names through `sys.modules`, which nothing
does; a dead `else` supplies a profile `execute_trace` always passes. The
dot-chain code wrapper exists twice (differing on BOM stripping and indentation,
against the specification's "same wrapper"), the preamble compile-and-merge
three times, and the ancestor BFS in `_prune_to_column_relevance` twice.

**Plan:** Delete the branch and the specification's partial-trace sentences;
make `row_scope` required and move the tests onto a resolver; delete
`_planned_order`, the comments and the dead `else`; one wrapper, one preamble
helper, one BFS.

**Acceptance:** The named code is gone; `_correlate_rows_posthoc` has one path;
jscpd reports none of the pairs; the specification no longer describes a
partial trace.

**Dependencies:** `ENGQ-R01` owns dead code outside this component.

**Evidence:** `src/haute/trace.py::_materialize_eager_outputs`;
`src/haute/_trace_correlation.py::_correlate_rows_posthoc`;
`src/haute/trace.py::_planned_order`;
`src/haute/_trace_enrichment.py::_wrap_node_code`;
`src/haute/_expression_parser.py::_wrap_expression_code`;
`src/haute/trace.py::_prune_to_column_relevance`.
