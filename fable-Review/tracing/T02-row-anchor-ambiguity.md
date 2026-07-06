# T02 — Row relocation silently anchors the trace to the wrong row

**Severity:** CRITICAL · **Effort:** S–M · **Dev/reviewer pair: REQUIRED** (silent-wrongness class)
**Files:** `src/haute/trace.py` (also deletes in `trace.py`; touches `tests/test_trace*.py`)
**Origin:** CORE-01 (backend-core review) = P03's FR-06, now reproduced **end-to-end**; plus CORE-10.
**Repro:** `repros/repro_trace.py` (unit) and `repros/repro_e2e.py` (end-to-end) — re-run before starting.

## The defect

`_find_target_row_index` (`trace.py:262-271`) is the relocation entry point used at `trace.py:489`
when the clicked row's values no longer sit at the clicked `row_index` — the documented, expected
case after preview-cache eviction or join reordering. It returns the **first** row matching the
clicked (visible) columns, with no ambiguity check:

```python
for idx, row in enumerate(df.select(shared).iter_rows(named=True)):
    if all(_trace_values_match(row.get(col), row_values.get(col)) for col in shared):
        return idx
```

One call deeper, `_find_matching_row` (hardened by W4, `_trace_correlation.py:401-443`) refuses
exactly this ambiguity — records `duplicate_exact_match`, returns `(None, -1)`. The entry point is
the permissive outlier. Reproduced end-to-end: two rows identical on the clicked columns
(`region`,`premium`) but different hidden `id`s; relocation picked `id=111` with
`correlation_diagnostics = []` — zero signal. Every upstream step then shows the *other* policy's
values, correct-for-the-wrong-row, in a regulator-facing view.

Note `shared` drops any `row_values` column absent from the target frame, so the anchor weakens
further when the preview was projected (fewer visible columns ⇒ more collision-prone).

## Companion cleanup (CORE-10)

`trace.py:503-524`'s `else` branch (target not in `eager_outputs`) builds partial rows from the raw
`row_index` with no verification. It is **unreachable** through the API today — the cold path runs
`_execute_eager_core(..., swallow_errors=False)` which raises on the first failure, and the
preview-reuse path returns only when the full `order` is present (`trace.py:719-738`) — but it reads
as a live fail-soft and becomes one if a future caller flips `swallow_errors`. Delete it and assert
the invariant.

## Fix design

1. In `_find_target_row_index`, collect matching indices with a short-circuit at the second match.
   On `>1` matches, raise the exact `ValueError` shape already used at `trace.py:493`
   ("Trace data does not match the preview row. …") — the route maps that prefix to HTTP 409 and the
   client shows the actionable "click the node to refresh" message. **Never return the first index
   on ambiguity.**
2. While here, vectorise the scan with the tolerance-aware `_build_value_match_expr` (after T03/P03
   FR-05 gives its float branch tolerance — sequence this fix after FR-05 or accept exact-match
   semantics until then; ambiguity-raising must NOT wait for the vectorisation):
   `df.lazy().with_row_index().filter(reduce(and_, exprs)).head(2).collect()` → 0 rows = None,
   1 row = its index, 2 rows = raise. This also removes the relocation path's full Python
   `iter_rows` scan (PERF-13).
3. Replace `trace.py:503-524` with `assert target_node_id in eager_outputs` (or an explicit
   `ValueError`), per CORE-10. Keep a comment stating the invariant's two guarantors.

## Failing tests first

1. Target frame with two rows identical on the `row_values` columns but differing on a hidden
   column; `execute_trace(..., row_values=<clicked>)` with a `row_index` whose values mismatch
   (forcing relocation) → assert `ValueError` with the "does not match the preview row" prefix
   (currently: returns a trace carrying the first row's hidden value — the repro's exact shape).
2. Route-level: same graph via `POST /pipeline/trace` → assert HTTP 409 (reuse
   `tests/test_trace_api.py` harness).
3. Unique-match relocation still works: one matching row at a different index → trace anchored
   there, no exception (pin the happy path).
4. Structural (with step 2's vectorisation): spy `pl.DataFrame.iter_rows`; relocation path does not
   call it on the target frame.
5. CORE-10: construct `eager_outputs` missing the target (direct call, not via API) → assert the
   loud failure, not a partial trace.

## Acceptance

- Ambiguous relocation ⇒ 409 with the actionable message; never a silently re-anchored trace.
- Unique relocation unchanged; full trace test suite green.
- The unreachable partial-rows branch is gone; its invariant asserted.
- Relocation path does zero full-frame Python scans (after vectorisation lands).
