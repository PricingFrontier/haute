# Tracing roadmap

## Scope

The row trace: correlating one quote's row through every node, the per-step
enrichments (formulas, input sources, renames, banding and rating verdicts,
waterfalls), the value lineage, and the expression parser that reads a node's
Polars code, and the trace panel that shows the result. Current behaviour is
specified in [the tracing specification](../tracing/high-level.md),
[expression parsing](../expression-parsing/high-level.md) and
[the trace UI](../frontend-trace-ui/high-level.md). These packages come
from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| TRACE-R02 | Planned | P2 | A step's expression type, description, referenced columns and rename detail come from the located expression's syntax tree, never from regular expressions over the node's whole code. Explanations use statement-time scalar bindings and preserve unary grouping and quoted literals in backend and browser formatting. |
| TRACE-R03 | Planned | P2 | A parent row proven by carried values is matched strictly or omitted, a banding coercion that fails is an error marker, an unnameable edge is reported, and a failed comparison names its real cause. |
| TRACE-R04 | Planned | P3 | The unreachable partial-trace path, the test-only correlation branch, the dead helper, the stale comments and the three duplicated mechanisms are gone. |
| TRACE-R05 | Planned | P2 | The trace panel shows an additive waterfall step with its own operator, the clicked column's conditional or opaque card shows its value, export failures say their cause, a power renders as a power, omission notes are chosen by code, and the panel's test-only helpers are gone. |

## Planned improvements

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

**28 October full-pass addition:** Expression binding and rendering also drift. A real full
trace of `factor=2; df=src.with_columns(y=pl.col("x")*factor); factor=3` returns output 20 but
calculation/derivation `10 * 3 = 30`, because the symbol table uses the final binding. Unary
minus renders `-(x+z)` as `-x + z`, and regex substitution changes the string literal in
`concat_str(x, "x")` into `concat_str("A", ""A"")`. **Plan/acceptance:** resolve scalar
bindings at the defining expression's statement, preserve AST precedence, and substitute only
column-expression tokens, never literal text. Execute the reassignment graph and assert
explanation agrees with its actual output; cover nested unary/binary expressions and literals
equal to column names. If a binding cannot be proved, report that limitation instead of a
confident alternative result. Evidence: `src/haute/_expression_parser.py::_build_safe_symbol_table`, `src/haute/_expression_parser.py::_resolve_reassignment_chains`, `src/haute/_expression_parser.py::_ExprConverter._unaryop`, `src/haute/_expression_parser.py::_substitute_values`;
`_trace_enrichment.py` currently trusts the wrong row-local evaluation.

The browser also replaces every `*` and `/` in displayed expressions, including
quoted literals: its real formatter turns `replace('a/b', 'a*b')` into
`replace('a÷b', 'a×b')`. Include the shared formatter and CalculationHero's
substituted-text rendering in the same syntax-preserving contract; test quoted
operators unchanged while actual arithmetic operators receive display glyphs.
Evidence: `frontend/src/trace/traceFormatting.ts::formatDisplayExpression` and
`frontend/src/trace/CalculationHero.tsx::CalculationHero.renderBody` (other trace renderers repeat this rule).

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

### TRACE-R05 — The trace panel shows what the response says
**Why:** The backend emits waterfall entries with `operation: "add"` whose value
is a delta, but `resolveWaterfallProp` drops `operation` and the chart prefixes
every row after the first with `×`, so a step that added 30 reads `×30` and one
that subtracted 30 reads `×-30`; the running totals are right and the operator
shown is wrong, and no frontend test uses an `add` entry. On the clicked column's
card a conditional or opaque calculation shows no result value, because the
value is drawn only by the frame the card mounts without. Trace export failures
are swallowed into one "check browser permissions" sentence whatever the cause.
The operator-glyph substitution is written seven times and six copies replace
every `*`, so a power renders as `××`. Omission notes outside the typed set are
chosen by substring, and `ambiguous` is tested before `source_frame`, so an
`ambiguous_source_frame` omission gets the wrong sentence. The "default used"
badge walks every nested object for a `status === "default"`, so a user column
named `status` can light it. `groupTraceSteps`, `buildFlowChain` and
`formatResultValue` are test-only or dead.

**Plan:** Carry `operation` into the chart and label an `add` row `+N` or `−N`
with a test on a backend `add` entry; render the value row in the conditional and
opaque branches; keep the export error and word the alert by cause; one
`displayOperators` helper matching operators as tokens; a code-keyed omission
note table; typed default-used fields; delete the dead helpers and their tests.

**Acceptance:** A trace whose waterfall holds an additive step shows `+30`; the
clicked card of a conditional column shows its value; `a ** 2` renders as a
power; the dead symbols are gone.

**Dependencies:** None.

**Evidence:** `frontend/src/trace/WaterfallChart.tsx`;
`frontend/src/trace/traceHelpers.ts::resolveWaterfallProp`;
`src/haute/_trace_waterfall.py`; `frontend/src/trace/CalculationHero.tsx`;
`frontend/src/trace/StepCard.tsx`; `frontend/src/panels/TracePanel.tsx`;
`frontend/src/trace/traceFormatting.ts`;
`frontend/src/panels/trace/traceGrouping.ts`.
