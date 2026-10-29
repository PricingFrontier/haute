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
