# Tracing roadmap

## Scope

Row correlation and trace assembly for the "why is this cell this value"
feature. Current behaviour is specified in
[the tracing specification](../tracing/high-level.md), which also records why
correlation stays value matching rather than a carried row identity.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| TRACE-R03 | Planned | P3 | A step whose candidate rows are identical in every column shows their values as one of N identical rows instead of a trace gap, without choosing a physical row. |

## Planned improvements

### TRACE-R03 — Identical candidate rows are not a trace gap
**Why:** When value matching finds several parent rows for one child row,
`_find_matching_row` records `duplicate_exact_match` and the step becomes a
trace gap ("One upstream row could not be identified unambiguously").
The 24 September 2026 measurement in the tracing specification found this is
the only ambiguity outside aggregates, and in every measured case the
candidate rows were value-identical. The
trace then hides a row whose values it knows exactly. It must not pretend to
know which physical row it is, though: `_match_parent_row` trusts a parent row
at the child's position on order-preserving paths, and a projection that
drops a distinguishing column can make two different source rows identical
downstream. Choosing one candidate's position would attach whichever source
row sits there.

**Plan:** When an exact match is ambiguous, test whether the candidate rows
are identical in every column of the parent frame, not only in the shared
columns the match used. If they are, the step shows those values with an
informational diagnostic carrying the candidate count, and its position stays
unresolved: correlation above it continues only by value matching from those
values, and any step that would need the unresolved position (the positional
fast path, head alignment) records the ambiguity exactly as today. The trace
panel and the trace export show the step with "one of N identical rows". A
relaxed-match ambiguity, or candidates that differ in any column, stay a gap
as today. Specify the rule in the tracing low-level specification first.

**Acceptance:** Tracing a keyless pipeline through `unique()` without a
subset, a sort on a non-unique column and a filter shows the duplicated step
labelled one of N identical rows instead of a gap; two different source rows
made identical by a projection that drops a column and then multiplied by an
order-preserving left join show the identical step, while the source step
stays ambiguous rather than naming one source row; candidates that differ in
a parent column still produce the `duplicate_exact_match` gap; relaxed
ambiguity is unchanged; the export carries the same label.

**Dependencies:** None.

**Evidence:** `src/haute/_trace_correlation.py::_find_matching_row`;
`src/haute/_trace_correlation.py::_match_parent_row`;
`src/haute/_trace_correlation.py::_record_ambiguous_row_match`;
`frontend/src/panels/TracePanel.tsx::omissionSummary`;
`frontend/src/trace/traceExport.ts`; `specs/tracing/low-level.md`.
