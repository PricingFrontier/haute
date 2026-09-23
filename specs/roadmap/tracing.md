# Tracing roadmap

## Scope

Row correlation and trace assembly for the "why is this cell this value"
feature. Current behaviour is specified in
[the tracing specification](../tracing/high-level.md). These packages come
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| TRACE-R01 | Decision | P3 | Trace rows are correlated by an injected row identity where transforms preserve it. |
| TRACE-R02 | Planned | P3 | The speculative preview-reader abstraction is removed. |

## Planned improvements

### TRACE-R01 — Row identity for trace executions
**Why:** Trace matches rows across nodes after the fact by value, because the
design rejects injecting a row id. That choice costs `_trace_correlation.py`
(2,355 lines): a row-scope resolver, carried-value proofs, ambiguity
diagnostics and edge-join suffix provenance. The original reason was
byte-for-byte agreement with the preview's frames, but trace now re-executes
under its own plan (the first trace after a preview is cold by
specification), so it can run with an extra column.

**Plan:** Measure how often value matching is ambiguous or unproven on
representative pipelines. Then decide whether trace runs inject an internal
row-id column at each source, carry it through row-preserving transforms,
filters and joins, and drop it before output, keeping value matching only
across aggregations and row-expanding steps.

**Acceptance:** The tracing specification records the decision and its
measurement; if adopted, correlation through filters, joins and column
transforms uses the row id and the corresponding value-matching code is
removed.

**Dependencies:** `EXEC-R05` (execution engine) makes a trace-only execution
policy straightforward.

**Evidence:** `src/haute/_trace_correlation.py::RowScopeResolver`;
`src/haute/trace.py::execute_trace`; `tests/test_trace_integration.py`.

### TRACE-R02 — Remove the preview-reader abstraction
**Why:** `execute_trace` accepts a `PreviewReader` protocol, a snapshot
dictionary or `None`, duck-typed in `_resolve_preview_snapshot`, to allow for
a "future Redis-backed reader". The specification says HTTP preview entries
never share a key with a trace, so the first trace after a preview executes
cold.

**Plan:** Drop the reader protocol and the three-shape resolution, or, if
preview reuse is wanted, make preview publish the full-ancestor entry trace
needs and reuse it directly.

**Acceptance:** `execute_trace` takes one well-defined source of prior
results, or none; the trace tests pass.

**Dependencies:** None.

**Evidence:** `src/haute/trace.py::PreviewReader`;
`src/haute/trace.py::_resolve_preview_snapshot`.
