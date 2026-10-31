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
| TRACE-R03 | Planned | P2 | A parent row proven by carried values is matched strictly or omitted, a banding coercion that fails is an error marker, an unnameable edge is reported, and a failed comparison names its real cause. |
| TRACE-R04 | Planned | P1 | `carried_column_proof` restricts `carried_only` and `join_keys` to the columns kept by `select(...)`, `_contains_with_columns_producing` detects unaliased and symbol-table expressions inside control-flow blocks, and `CalculationHero` renders `WaterfallErrorAlert` when `waterfallError` is set. |

## Planned improvements

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

### TRACE-R04 — Restrict `carried_column_proof` to columns retained by `select(...)`, detect unaliased/symbol-bound `with_columns` inside control flow, and render `WaterfallErrorAlert` in `CalculationHero`
**Why:** Three trace and expression-parsing defects affect carried-column lineage,
control-flow opacity detection and waterfall error display:
1. `carried_column_proof` maintains a single optional `carried_only: set[str] | None`
   restriction for the proof (initially `None`, meaning all non-assigned root/joined
   columns are carried), along with `assigned` and `join_keys`. In the
   `method in {"with_columns", "select"}` branch, `.select(...)` is treated identically
   to `.with_columns(...)`: it records columns overwritten or renamed inside the call in
   `assigned`, but never updates `carried_only` or intersects `join_keys` with the set
   of unchanged pass-through columns actually kept by `select(...)`. When a node runs
   `df = parent.select("a", "b")`, `carried_only` stays `None` (or keeps previously
   included columns outside `{"a", "b"}`) and every dropped parent column (`"c"`,
   `"d"`, ...) remains treated as carried in the returned `CarriedColumnProof`, so a
   composed `df = parent.select("a").join(other, on="a")` treats an output column `"c"`
   coming from `other` as carried from `parent`.
2. In `_has_control_flow_wrapping_target` / `_contains_with_columns_producing`
   (`_expression_parser.py`), `_contains_with_columns_producing` checks only
   `_, alias = _strip_alias(arg)` and `kw.arg == target_column`, without falling back
   to `_infer_auto_name(arg)` or resolving variable arguments via `symbol_table`
   (unlike `_extract_expressions_from_with_columns`). When a column `"x"` is modified
   inside an `if`/`for`/`while`/`try`/`with`/`match` block via an unaliased expression
   (`df = df.with_columns(pl.col("x") * 2)`) or a pre-bound expression variable
   (`expr = (pl.col("x") * 2).alias("x"); if cond: df = df.with_columns(expr)`),
   `_has_control_flow_wrapping_target` returns `False` and `_find_definition` extracts
   the conditional branch's formula as if it were unconditional instead of returning
   `expression_type="opaque"`.
3. `CalculationHero` (`frontend/src/trace/CalculationHero.tsx`) destructures
   `const { steps: backendSteps, error: waterfallError } = resolveWaterfallProp(props.waterfall)`
   and has the comment `// Waterfall build failed on the backend — surface the error loudly rather than rendering a silently-empty trace`,
   but never renders `<WaterfallErrorAlert>` when `waterfallError !== null`.

**Plan:** Separate the `"select"` branch from `"with_columns"` in
`carried_column_proof`: compute the set of unchanged pass-through columns kept by the
`select(...)` arguments and update the proof's `carried_only` to that set (intersecting
with the existing `carried_only` when already non-`None`, and distinguishing an empty
`frozenset()` when `select` keeps no unchanged columns from `None`), while intersecting
`join_keys` with the kept columns (or bailing out if a star/dynamic selector prevents
static enumeration of the selected columns); use `_infer_auto_name` and `symbol_table`
lookup in `_contains_with_columns_producing` (matching
`_extract_expressions_from_with_columns`); and render `<WaterfallErrorAlert>` inside
`CalculationHero` when `waterfallError !== null`.

**Acceptance:** For `df = parent.select("a", pl.col("b").alias("b_new"))` with parent
columns `["a", "b", "c"]`, `carried_column_proof` returns
`carried_only == frozenset({"a"})` and excludes both the renamed `"b"` and the dropped
`"c"`, and for the composed chain `df = parent.select("a").join(other, on="a")` (where
`parent` has `c=10` and `other` has `c=20`), `carried_column_proof` returns
`carried_only == frozenset({"a"})` so `RowScopeResolver._shared_carried_values` does
not treat output `c=20` as carried from `parent`; a column modified inside an `if`
block via an unaliased `pl.col("x") * 2` or a variable `expr` is classified as
`expression_type="opaque"`; and `CalculationHero` renders `WaterfallErrorAlert` when
given a `{ status: "error", error: ... }` waterfall prop.

**Dependencies:** None.

**Evidence:** `src/haute/_column_lineage.py::carried_column_proof`;
`src/haute/_trace_correlation.py::RowScopeResolver._shared_carried_values`;
`src/haute/_expression_parser.py::_has_control_flow_wrapping_target`;
`src/haute/_expression_parser.py::_contains_with_columns_producing`;
`frontend/src/trace/CalculationHero.tsx::CalculationHero`.
