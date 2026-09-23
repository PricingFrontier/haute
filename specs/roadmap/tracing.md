# Tracing roadmap

## Scope

Row correlation and trace assembly for the "why is this cell this value"
feature. Current behaviour is specified in
[the tracing specification](../tracing/high-level.md). These packages come
from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| TRACE-R01 | Decision | P3 | Trace rows are correlated by a row identity wherever it can be carried without user code seeing it. |
| TRACE-R02 | Planned | P3 | The speculative preview-reader abstraction is removed. |

## Planned improvements

### TRACE-R01 — Row identity for trace executions
**Why:** Trace matches rows across nodes after the fact by value, because the
design rejects injecting a row id. That choice costs `_trace_correlation.py`
(2,355 lines): a row-scope resolver, carried-value proofs, ambiguity
diagnostics and edge-join suffix provenance. Trace now re-executes under its
own plan (the first trace after a preview is cold by specification), so an
extra column would not have to match a cached preview frame.

An injected column is not invisible, though. User code receives the frames
themselves, so a row-id column changes the result of all-column selectors
(`pl.all()`, `pl.sum_horizontal(pl.all())`, dtype selectors), makes a
`unique()` without a subset keep every row, and alters any code that
inspects the schema or column count. Dropping the column at the output cannot
undo a value it has already changed, and the tracing specification requires
trace to be a pure observation layer that never modifies pipeline execution
or its outputs.

**Plan:** Measure how often value matching is ambiguous or unproven on
representative pipelines. Then decide whether, and where, trace runs carry an
internal row identity. The decision must say how the identity stays invisible
to user code: for example, carry it only through node types Haute builds
itself (joins, filters it generates, banding and rating steps) and keep value
matching across user-code nodes, or attach the identity outside the frame
where the node is provably row- and order-preserving. Value matching remains
across aggregations and row-expanding steps either way.

**Acceptance:** The tracing specification records the decision and its
measurement; if adopted, correlation through the covered node types uses the
row identity and the corresponding value-matching code is removed; a fixture
set including all-column selectors, `unique()` without a subset and
schema-inspecting user code produces outputs identical to an untraced run.

**Dependencies:** `EXEC-R05` (execution engine) makes a trace-only execution
policy straightforward.

**Evidence:** `src/haute/_trace_correlation.py::RowScopeResolver`;
`src/haute/trace.py::execute_trace`; `src/haute/_user_exec.py::_exec_user_code`;
`specs/tracing/high-level.md`; `tests/test_trace_integration.py`.

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
